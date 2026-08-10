"""What a component draws equals what it consumes plus what it stores (R-12).

The **over-draw** defect, and the conservation property that closes it.

A rule runs at the scale its scarcest input allows, but the demand that fetched
its inputs was sized on the DECLARED coefficients -- the scale is not known when
the demand sweep runs. So a reaction limited by one reagent was served more of
the others than it could use, and the difference entered no reaction, no stock
and no output. It was destroyed. On a source that is invisible: nothing there is
depleted. Behind a STOCK it turns an accounting discrepancy into lost matter,
and that is what this module measures -- a battery falling to 0.67 out of 100
over five time units where the reaction justifies 2.5.

The fix caps the draw at ``scale x uptake x coefficient`` in the production
sweep, where both are already known, and gives the surplus back to the
suppliers.

``uptake`` is the second half of it (R-13). ``rule_scale`` computes what the
INPUTS allow, which is not what the outputs delivered: a derating and a time
profile scale production and were read only afterwards, so a component whose
output a failure mode cut to zero went on drawing at the nominal rate for the
whole mission -- and the cap above had nothing to give back, because it compared
the draw against the very scale the derating had not reached. Conservation is
therefore asserted here with each of those terms active, per component and per
stop, exactly as it is for the limiting reagent.

Three draws are deliberately NOT capped, and each has a scenario here:

- an input a **capacity** buffers -- the surplus is stored, not destroyed, and
  starving the buffer would defeat what a buffer is for;
- an input **no rule accounts for** -- a pure consumer IS the sink;
- a connection whose producer allocated nothing, which has no split to correct.

What this does NOT close is the **over-demand**: the demand a component
publishes is still sized on the declared coefficients, so two rivals on one
supply are still split in proportion to demands one of them cannot honour. That
boundary is asserted here as it stands rather than left unmeasured -- see
``test_the_over_demand_boundary_is_unchanged_and_measured``.

PyCATSHOO forbids more than one live system per process and ``simulate`` cannot
be called twice on one system, so each scenario is built, driven and deleted
before the next one starts; the fixture snapshots what each produced, and the
last is kept alive for the teardown.
"""

import math

import cod3s
import muscadet
import pytest

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
    TransformerContinuous,
)

#: The solver stops the integration ON a crossing rather than refining it, so a
#: level integrated over a horizon is asserted within this relative slack.
ODC_TOL = 0.05

# -- The ported hydrogen slice: 4 H2O + 1 Elec -> 1 H2 + 1 O2
ODC_H2O_RATE = 2.0
ODC_CONS = {"H2O": 4.0, "Elec": 1.0}
ODC_PROD = {"H2": 1.0, "O2": 1.0}
#: The limiting reagent: 2 of H2O arrive where the rule wants 4.
ODC_SCALE = ODC_H2O_RATE / ODC_CONS["H2O"]
ODC_STOCK = 100.0
ODC_BATTERY_VOLUME = 200.0
ODC_TANK_VOLUME = 100.0
ODC_PLANT_HORIZON = 5.0
#: What the reaction justifies over the horizon: 0.5 of Elec per unit time.
ODC_JUSTIFIED = ODC_SCALE * ODC_CONS["Elec"] * ODC_PLANT_HORIZON

# -- Two rivals on one shared supply
ODC_SHARED = 1.0
ODC_RIVAL_CONS = {"A": 10.0, "E": 1.0}
#: A is supplied at 1.0 against a coefficient of 10, so U1 can run at 0.1 --
#: and therefore use 0.1 of the contested E, whatever it asks for.
ODC_RIVAL_SCALE = ODC_SHARED / ODC_RIVAL_CONS["A"]
ODC_RIVAL_HORIZON = 1.0
#: An unbounded downstream demand is the normal case in the model being ported.
ODC_UNBOUNDED_DOWNSTREAM = 1000.0

# -- A buffered input, which must go on absorbing the surplus
ODC_BUFFER_SUPPLY = 4.0
ODC_BUFFER_VOLUME = 50.0
ODC_BUFFER_CONS = {"b": 1.0}
ODC_BUFFER_PROD = {"y": 1.0}
ODC_BUFFER_DEMAND = 1.0
ODC_BUFFER_HORIZON = 2.0

# -- A derating and a time profile, with a stock behind the metered reagent.
#    Conservation must hold with either term active: what the component draws
#    is what it PRODUCED, not what its inputs would have allowed (R-13).
ODC_SCALED_CONS = {"m": 2.0, "e": 1.0}
ODC_SCALED_PROD = {"p": 1.0}
#: Plentiful, so that neither reagent is the limiting one: the only thing that
#: can reduce the draw here is the derating or the profile.
ODC_SCALED_SUPPLY = 20.0
ODC_SCALED_STOCK = 100.0
ODC_SCALED_VOLUME = 200.0
#: What the sink asks for, and therefore the scale the rules run at.
ODC_SCALED_DEMAND = 4.0
#: What the mode leaves of the output, and when it fires.
ODC_DERATING = 0.25
ODC_DERATING_DATE = 1.0
ODC_SCALED_HORIZON = 2.0


def odc_ramp(time):
    """A deliberately trivial CONTINUOUS profile: 0.2 at t=0, +0.2 per unit.

    Linear, and kept below 1 over the horizon, so the arithmetic of the
    assertions is exact and the clamp of :func:`get_uptake_factor` -- which
    refuses to draw MORE than the supply allowed -- never has to bite.
    """
    return 0.2 + 0.2 * time


# ----------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------


def add_clock(comp, date):
    """Give the interactive session a date it can always step to.

    Named from the date, so one component may carry several stops.
    """
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def walk(system, snap, horizon, limit=40):
    """Step to ``horizon``, recording ``snap(system)`` at every stop."""
    trace = [snap(system)]

    for _ in range(limit):
        if system.currentTime() >= horizon:
            break
        system.isimu_step_forward()
        trace.append(snap(system))

    return trace


def settled(trace):
    """The stops at which the sweeps have run.

    Both sweeps are PDMP equations and a PDMP equation is not evaluated at
    ``t=0``, so the first stop of every trace reports declared defaults rather
    than a settled flow network.
    """
    return [entry for entry in trace if entry["time"] > 0.0]


def drawn(system, comp_name, flow_name):
    """What one continuous input actually draws from its suppliers."""
    return system.comp[comp_name].flows_in[flow_name].get_delivered()


def published(system, comp_name, flow_name):
    """What one continuous input asks its suppliers for."""
    return system.comp[comp_name].flows_in[flow_name].var_demand.value()


def stored(system, comp_name, flow_name):
    """What an input capacity buffering one flow accumulates, per unit time.

    Zero when no capacity buffers it: an unbuffered input stores nothing, which
    is exactly why its draw has to be capped.
    """
    comp = system.comp[comp_name]
    capacity = comp.get_capacity_of_flow(flow_name, "in")

    if capacity is None:
        return 0.0

    return capacity.get_inflow(flow_name) - capacity.get_outflow(flow_name)


def reaction_balance(system, comp_name, cons, prod, produced_flow):
    """The conservation books of one reacting component, recomputed here.

    Deliberately recomputed from the coefficients THIS MODULE declares, and from
    what the component publishes on its output, rather than read back from the
    scale the implementation computed: a check that asks the implementation what
    it did would agree with it whatever it did.

    Returns
    -------
    dict
        ``{flow name: (drawn, consumed + stored)}`` over the consumed flows.
    """
    scale = (
        system.comp[comp_name].flows_out[produced_flow].var_fed.value()
        / prod[produced_flow]
    )

    return {
        flow_name: (
            drawn(system, comp_name, flow_name),
            coefficient * scale + stored(system, comp_name, flow_name),
        )
        for flow_name, coefficient in cons.items()
    }


def assert_conserved(balance, label):
    """Every draw is accounted for by a consumption or a storage."""
    for flow_name, (draw, uptake) in balance.items():
        assert draw == pytest.approx(uptake, abs=1e-6), (
            f"{label}: {flow_name} draws {draw:g} but consumes and stores "
            f"only {uptake:g} -- {draw - uptake:g} destroyed"
        )


# ----------------------------------------------------------------------
# The scenarios
# ----------------------------------------------------------------------


def run_plant_scenario(obs):
    """The ported hydrogen slice, with a STOCK behind the abundant reagent.

    A water source rate-limited to 2 against a coefficient of 4, so the
    electrolysis runs at 0.5 and needs 0.5 of electricity. The battery holds
    100. What it loses over the horizon must be what the reaction consumed.
    """
    system = muscadet.System(name="OverDrawPlant")

    system.add_component(
        name="S_H2O", cls="SourceContinuous", flow="H2O", rate=ODC_H2O_RATE
    )
    system.add_component(
        name="B1",
        cls="CapacityContinuous",
        ports="out",
        flow="Elec",
        capacity=ODC_BATTERY_VOLUME,
        capacity_name="battery",
        content_init={"Elec": ODC_STOCK},
    )
    system.add_component(
        name="ELECTRO",
        cls="TransformerContinuous",
        flows_in=list(ODC_CONS),
        flows_out=list(ODC_PROD),
        rules=[dict(name="electrolysis", cons=ODC_CONS, prod=ODC_PROD)],
    )
    system.add_component(
        name="LOCAL",
        cls="CapacityContinuous",
        flow="H2",
        capacity=ODC_TANK_VOLUME,
        capacity_name="tank",
        content_init={"H2": 0.0},
        fill_rate=math.inf,
    )

    system.connect_flow(source="S_H2O", target="ELECTRO", flow_name="H2O")
    system.connect_flow(source="B1", target="ELECTRO", flow_name="Elec")
    system.connect_flow(source="ELECTRO", target="LOCAL", flow_name="H2")
    # O2 is produced by the rule and deliberately connected to nothing (R-10).

    add_clock(system.comp["ELECTRO"], ODC_PLANT_HORIZON)

    system.isimu_start()

    def snap(system):
        battery = system.comp["B1"].capacities["battery"]
        tank = system.comp["LOCAL"].capacities["tank"]

        return {
            "time": system.currentTime(),
            "battery": battery.get_quantity("Elec"),
            "battery_outflow": battery.get_outflow("Elec"),
            "tank": tank.get_quantity("H2"),
            "H2": system.comp["ELECTRO"].flows_out["H2"].var_fed.value(),
            "Elec_demand": published(system, "ELECTRO", "Elec"),
            "balance": reaction_balance(system, "ELECTRO", ODC_CONS, ODC_PROD, "H2"),
        }

    obs["plant"] = walk(system, snap, ODC_PLANT_HORIZON)

    system.isimu_stop()
    system.deleteSys()


def add_rivals(system, prefix, downstream_demand):
    """Two reactors on one shared supply, one of them limited by another input."""
    system.add_component(
        name=f"{prefix}_SA", cls="SourceContinuous", flow="A", rate=ODC_SHARED
    )
    system.add_component(
        name=f"{prefix}_SE", cls="SourceContinuous", flow="E", rate=ODC_SHARED
    )
    system.add_component(
        name=f"{prefix}_U1",
        cls="TransformerContinuous",
        flows_in=list(ODC_RIVAL_CONS),
        flows_out=["P1"],
        rules=[dict(name="r", cons=ODC_RIVAL_CONS, prod={"P1": 1.0})],
    )
    system.add_component(
        name=f"{prefix}_U2",
        cls="TransformerContinuous",
        flows_in=["E"],
        flows_out=["P2"],
        rules=[dict(name="r", cons={"E": 1.0}, prod={"P2": 1.0})],
    )
    system.add_component(
        name=f"{prefix}_C1",
        cls="ConsumerContinuous",
        flow="P1",
        demand=downstream_demand,
    )
    system.add_component(
        name=f"{prefix}_C2", cls="ConsumerContinuous", flow="P2", demand=1.0
    )

    for source, target, flow in (
        (f"{prefix}_SA", f"{prefix}_U1", "A"),
        (f"{prefix}_SE", f"{prefix}_U1", "E"),
        (f"{prefix}_SE", f"{prefix}_U2", "E"),
        (f"{prefix}_U1", f"{prefix}_C1", "P1"),
        (f"{prefix}_U2", f"{prefix}_C2", "P2"),
    ):
        system.connect_flow(source=source, target=target, flow_name=flow)


def rival_snapshot(system, prefix):
    """What one rival pair reads right now."""
    return {
        "time": system.currentTime(),
        "U1_demand_E": published(system, f"{prefix}_U1", "E"),
        "U1_draws_E": drawn(system, f"{prefix}_U1", "E"),
        "U1_draws_A": drawn(system, f"{prefix}_U1", "A"),
        "U2_draws_E": drawn(system, f"{prefix}_U2", "E"),
        "supply": system.comp[f"{prefix}_SE"].flows_out["E"].var_fed.value(),
        "balance": reaction_balance(
            system, f"{prefix}_U1", ODC_RIVAL_CONS, {"P1": 1.0}, "P1"
        ),
    }


def run_rivals_scenario(obs):
    """The same rivalry under a modest and under an unbounded downstream demand."""
    system = muscadet.System(name="OverDrawRivals")

    add_rivals(system, "MOD", downstream_demand=1.0)
    add_rivals(system, "UNB", downstream_demand=ODC_UNBOUNDED_DOWNSTREAM)
    add_clock(system.comp["MOD_SA"], ODC_RIVAL_HORIZON)

    system.isimu_start()

    # Both variants are sampled at EVERY stop of the one walk: conservation is
    # asserted per component per stop, not on a closing snapshot.
    trace = walk(
        system,
        lambda s: {
            "time": s.currentTime(),
            "MOD": rival_snapshot(s, "MOD"),
            "UNB": rival_snapshot(s, "UNB"),
        },
        ODC_RIVAL_HORIZON,
    )

    obs["rivals_modest"] = [entry["MOD"] for entry in trace]
    obs["rivals_unbounded"] = [entry["UNB"] for entry in trace]

    system.isimu_stop()
    system.deleteSys()


def add_scaled_reactor(system, prefix, flows_out):
    """A reactor on a plentiful supply and a stock, with a metered downstream.

    Both reagents are supplied well beyond what the rule can use, so the scale
    is set by the sink's demand alone: whatever reduces the draw below the
    nominal has to be the derating or the profile, and nothing else.
    """
    system.add_component(
        name=f"{prefix}_M", cls="SourceContinuous", flow="m", rate=ODC_SCALED_SUPPLY
    )
    system.add_component(
        name=f"{prefix}_E",
        cls="CapacityContinuous",
        ports="out",
        flow="e",
        capacity=ODC_SCALED_VOLUME,
        capacity_name="stock",
        content_init={"e": ODC_SCALED_STOCK},
    )
    system.add_component(
        name=prefix,
        cls="TransformerContinuous",
        flows_in=list(ODC_SCALED_CONS),
        flows_out=flows_out,
        rules=[dict(name="react", cons=ODC_SCALED_CONS, prod=ODC_SCALED_PROD)],
    )
    system.add_component(
        name=f"{prefix}_C",
        cls="ConsumerContinuous",
        flow="p",
        demand=ODC_SCALED_DEMAND,
    )

    for source, target, flow in (
        (f"{prefix}_M", prefix, "m"),
        (f"{prefix}_E", prefix, "e"),
        (prefix, f"{prefix}_C", "p"),
    ):
        system.connect_flow(source=source, target=target, flow_name=flow)


def scaled_snapshot(system, prefix):
    """What one scaled reactor reads right now."""
    stock = system.comp[f"{prefix}_E"].capacities["stock"]

    return {
        "time": system.currentTime(),
        "produced": system.comp[prefix].flows_out["p"].var_fed.value(),
        "draws_m": drawn(system, prefix, "m"),
        "draws_e": drawn(system, prefix, "e"),
        "stock": stock.get_quantity("e"),
        "stock_outflow": stock.get_outflow("e"),
        "balance": reaction_balance(
            system, prefix, ODC_SCALED_CONS, ODC_SCALED_PROD, "p"
        ),
    }


def run_scaled_scenario(obs):
    """Conservation with a DERATING and with a TIME PROFILE active (R-13).

    Two reactors on the same shape: ``DERATED`` has its output cut to a quarter
    by a failure mode at t=1, ``PROFILED`` carries a ramp of simulation time.
    Neither is limited by a reagent, so the whole of the reduction is the term
    under test -- and the draw must follow it.
    """
    system = muscadet.System(name="OverDrawScaled")

    add_scaled_reactor(system, "DERATED", flows_out=["p"])
    system.comp["DERATED"].add_delay_failure_mode(
        name="cut",
        failure_time=ODC_DERATING_DATE,
        failure_effects=[("p", ODC_DERATING)],
        repair_time=1e6,
    )

    add_scaled_reactor(
        system,
        "PROFILED",
        flows_out=[
            {"name": "p", "profile": muscadet.Profile(odc_ramp, continuous=True)}
        ],
    )

    for date in (0.5, 1.5, ODC_SCALED_HORIZON):
        add_clock(system.comp["DERATED_C"], date)

    system.isimu_start()

    trace = walk(
        system,
        lambda s: {
            "time": s.currentTime(),
            "DERATED": scaled_snapshot(s, "DERATED"),
            "PROFILED": scaled_snapshot(s, "PROFILED"),
        },
        ODC_SCALED_HORIZON,
    )

    obs["derated"] = [entry["DERATED"] for entry in trace]
    obs["profiled"] = [entry["PROFILED"] for entry in trace]

    system.isimu_stop()
    system.deleteSys()


def run_buffer_scenario(obs):
    """A buffered input, a rule-less sink, and a rule set that selects nothing.

    The three draws the cap must LEAVE ALONE, in one system:

    - ``BUF`` buffers its input in a capacity and consumes 1 of the 4 that
      arrive. The other 3 must go on entering the volume;
    - ``SINK`` is a pure consumer: no rule accounts for its input, and what it
      is given is what it takes;
    - ``IDLE`` carries a rule set whose only rule is guarded on a control that
      is never true, so it selects nothing -- and a component producing nothing
      must draw nothing.
    """
    system = muscadet.System(name="OverDrawBuffer")

    system.add_component(
        name="BUF_SRC", cls="SourceContinuous", flow="b", rate=ODC_BUFFER_SUPPLY
    )
    system.add_component(
        name="BUF",
        cls="TransformerContinuous",
        flows_in=["b"],
        flows_out=["y"],
        rules=[dict(name="r", cons=ODC_BUFFER_CONS, prod=ODC_BUFFER_PROD)],
    )
    system.comp["BUF"].add_capacity(
        name="hopper",
        flow="b",
        capacity=ODC_BUFFER_VOLUME,
        side="in",
        fill_rate=math.inf,
    )
    system.add_component(
        name="BUF_C", cls="ConsumerContinuous", flow="y", demand=ODC_BUFFER_DEMAND
    )
    system.connect_flow(source="BUF_SRC", target="BUF", flow_name="b")
    system.connect_flow(source="BUF", target="BUF_C", flow_name="y")

    system.add_component(
        name="SINK_SRC", cls="SourceContinuous", flow="s", rate=ODC_BUFFER_SUPPLY
    )
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="s", demand=ODC_BUFFER_DEMAND
    )
    system.connect_flow(source="SINK_SRC", target="SINK", flow_name="s")

    system.add_component(
        name="IDLE_SRC", cls="SourceContinuous", flow="i", rate=ODC_BUFFER_SUPPLY
    )
    system.add_component(
        name="IDLE",
        cls="TransformerContinuous",
        flows_in=["i"],
        flows_out=["z"],
        controls=["go"],
        rules=[dict(name="run", cond="go", cons={"i": 1.0}, prod={"z": 1.0})],
    )
    system.add_component(
        name="IDLE_C", cls="ConsumerContinuous", flow="z", demand=ODC_BUFFER_DEMAND
    )
    system.connect_flow(source="IDLE_SRC", target="IDLE", flow_name="i")
    system.connect_flow(source="IDLE", target="IDLE_C", flow_name="z")

    add_clock(system.comp["BUF_SRC"], ODC_BUFFER_HORIZON)

    system.isimu_start()

    def snap(system):
        hopper = system.comp["BUF"].capacities["hopper"]

        return {
            "time": system.currentTime(),
            "buf_draws": drawn(system, "BUF", "b"),
            "buf_consumes": system.comp["BUF"].flows_out["y"].var_fed.value(),
            "buf_stores": stored(system, "BUF", "b"),
            "buf_level": hopper.get_quantity("b"),
            "sink_draws": drawn(system, "SINK", "s"),
            "sink_demand": published(system, "SINK", "s"),
            "idle_draws": drawn(system, "IDLE", "i"),
            "idle_produces": system.comp["IDLE"].flows_out["z"].var_fed.value(),
        }

    obs["buffer"] = walk(system, snap, ODC_BUFFER_HORIZON)

    system.isimu_stop()

    # Kept alive for the teardown test, per the module convention.
    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_plant_scenario(obs)
    run_rivals_scenario(obs)
    run_scaled_scenario(obs)
    run_buffer_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# Conservation, as a first-class assertion
# ----------------------------------------------------------------------


def test_the_electrolyser_draws_exactly_what_it_consumes(the_run):
    """The stoichiometry, at every stop: 4 of water and 1 of power per H2.

    Against ``09a0b9e`` the water draw was already right -- the source is
    rate-limited, so it could not over-deliver -- and the POWER draw was the
    whole of what the battery could serve, because the tank downstream claimed
    without bound and the battery holds a stock.
    """
    trace = settled(the_run["plant"])
    assert trace, "the plant never ran"

    for entry in trace:
        assert_conserved(entry["balance"], f"ELECTRO at t={entry['time']:g}")


def test_the_battery_loses_what_the_reaction_justifies(the_run):
    """100 down to 97.5 over five time units, and not a unit more.

    The measured symptom. The reaction consumes 0.5 of power per unit time, so
    the stock must fall by 2.5. Against ``09a0b9e`` the battery served its whole
    stock as a rate at every step and decayed exponentially to 0.67.
    """
    trace = settled(the_run["plant"])

    for entry in trace:
        assert entry["battery_outflow"] == pytest.approx(
            ODC_SCALE * ODC_CONS["Elec"], rel=1e-4
        )
        assert entry["battery"] == pytest.approx(
            ODC_STOCK - ODC_SCALE * ODC_CONS["Elec"] * entry["time"], rel=ODC_TOL
        )

    final = trace[-1]
    assert final["time"] == pytest.approx(ODC_PLANT_HORIZON, abs=ODC_TOL)
    assert final["battery"] == pytest.approx(ODC_STOCK - ODC_JUSTIFIED, rel=ODC_TOL)


def test_nothing_the_battery_gives_up_is_destroyed(the_run):
    """What leaves the stock enters the reaction, and the reaction's output.

    The end-to-end statement, over the whole horizon rather than per step: the
    hydrogen tank holds exactly the hydrogen the consumed power accounts for,
    at the rule's 1:1 ratio.
    """
    final = settled(the_run["plant"])[-1]

    lost_by_the_battery = ODC_STOCK - final["battery"]
    held_as_hydrogen = final["tank"] * ODC_CONS["Elec"] / ODC_PROD["H2"]

    assert lost_by_the_battery == pytest.approx(held_as_hydrogen, rel=ODC_TOL)


def test_the_unbounded_demand_still_travels(the_run):
    """R-11 unchanged: an ``inf`` a real consumer published still propagates.

    The cap is on the DRAW, never on the demand. The terminal tank claims
    without bound, that claim crosses the electrolyser's rule, and the
    electrolyser goes on publishing an unbounded demand on its power input --
    what changed is only that an unbounded demand no longer empties the battery.
    """
    for entry in settled(the_run["plant"]):
        assert math.isinf(entry["Elec_demand"])


# ----------------------------------------------------------------------
# Two rivals on one supply
# ----------------------------------------------------------------------


def test_a_limited_reactor_draws_only_what_it_can_use(the_run):
    """U1 is limited to 0.1 by its other input, so it draws 0.1 of the shared E.

    Against ``09a0b9e`` it drew its whole allocated share -- 0.5 on a modest
    downstream demand, 0.999 on an unbounded one -- and used 0.1 of it.
    """
    for label in ("rivals_modest", "rivals_unbounded"):
        trace = settled(the_run[label])
        assert trace, f"{label} never ran"

        for entry in trace:
            assert entry["U1_draws_E"] == pytest.approx(ODC_RIVAL_SCALE, rel=1e-4)
            assert_conserved(entry["balance"], f"{label} at t={entry['time']:g}")


def test_the_supply_only_gives_up_what_is_taken(the_run):
    """A source delivers the sum of the draws, not the sum of the allocations.

    What is released is available again at the next step rather than lost:
    behind a stock, this is the whole of the conservation fix.
    """
    for label in ("rivals_modest", "rivals_unbounded"):
        for entry in settled(the_run[label]):
            assert entry["supply"] == pytest.approx(
                entry["U1_draws_E"] + entry["U2_draws_E"], rel=1e-6
            ), label

    # Under an unbounded downstream demand the shared source used to give up its
    # whole rate, all but a thousandth of it to a reactor that could use a tenth.
    assert settled(the_run["rivals_unbounded"])[-1]["supply"] < ODC_SHARED


def test_the_over_demand_boundary_is_unchanged_and_measured(the_run):
    """What the over-draw fix does NOT close, asserted rather than left implicit.

    U1 still PUBLISHES its nominal demand on the contested supply, so the split
    is still made in proportion to a demand it cannot honour and its rival is
    still short. The surplus is now released instead of destroyed, so the
    deprivation is a bad split for one step rather than a permanent loss -- but
    the split itself has not moved.

    Fair shares of the contested 1.0, in proportion to what each rival can
    ACHIEVE, would be 0.0909 to U1 and 0.909 to U2. These numbers are the
    distance still to travel; closing it is a design decision recorded in Scope
    Boundaries, and this test is what will fail when it is taken.
    """
    modest = settled(the_run["rivals_modest"])[-1]

    # The demand is the nominal one, not the achievable 0.1
    assert modest["U1_demand_E"] == pytest.approx(1.0)
    # ... so the split is still even, and U2 gets 0.5 where 0.909 is its due
    assert modest["U2_draws_E"] == pytest.approx(0.5, rel=1e-3)

    unbounded = settled(the_run["rivals_unbounded"])[-1]
    assert unbounded["U1_demand_E"] == pytest.approx(ODC_UNBOUNDED_DOWNSTREAM)
    assert unbounded["U2_draws_E"] < 0.01


# ----------------------------------------------------------------------
# Conservation with a derating and with a time profile (R-13)
# ----------------------------------------------------------------------


def test_a_derated_reactor_draws_what_it_produced(the_run):
    """The mode leaves a quarter of the output, so a quarter is drawn.

    Neither reagent limits the rule -- both are supplied at 20 against
    coefficients of 2 and 1 at a scale of 4 -- so the whole of the reduction is
    the derating. Against ``9c5e647`` the draw stayed at the nominal 8 and 4
    while the output delivered 1, and conservation failed by three quarters of
    it.
    """
    trace = settled(the_run["derated"])
    assert trace, "the derated reactor never ran"

    for entry in trace:
        assert_conserved(entry["balance"], f"DERATED at t={entry['time']:g}")

    nominal = [e for e in trace if e["time"] < ODC_DERATING_DATE]
    cut = [e for e in trace if e["time"] > ODC_DERATING_DATE]
    assert nominal and cut, "the mode must fire inside the traced window"

    for entry in nominal:
        assert entry["produced"] == pytest.approx(ODC_SCALED_DEMAND)
        assert entry["draws_m"] == pytest.approx(
            ODC_SCALED_CONS["m"] * ODC_SCALED_DEMAND
        )
        assert entry["draws_e"] == pytest.approx(
            ODC_SCALED_CONS["e"] * ODC_SCALED_DEMAND
        )

    for entry in cut:
        produced = ODC_SCALED_DEMAND * ODC_DERATING
        assert entry["produced"] == pytest.approx(produced)
        assert entry["draws_m"] == pytest.approx(ODC_SCALED_CONS["m"] * produced)
        assert entry["draws_e"] == pytest.approx(ODC_SCALED_CONS["e"] * produced)


def test_a_derated_reactor_stops_draining_its_stock_at_the_nominal_rate(the_run):
    """The measured symptom: a stock is what makes an over-draw matter.

    The stock behind ``e`` falls at 4 per unit time while the reactor is whole
    and at 1 once it is cut. Against ``9c5e647`` it went on falling at 4, and
    the difference was destroyed.
    """
    trace = settled(the_run["derated"])

    for entry in trace:
        expected = ODC_SCALED_CONS["e"] * ODC_SCALED_DEMAND
        if entry["time"] > ODC_DERATING_DATE:
            expected *= ODC_DERATING
        assert entry["stock_outflow"] == pytest.approx(expected, rel=1e-4)

    final = trace[-1]
    consumed_whole = ODC_SCALED_CONS["e"] * ODC_SCALED_DEMAND * ODC_DERATING_DATE
    consumed_cut = (
        ODC_SCALED_CONS["e"]
        * ODC_SCALED_DEMAND
        * ODC_DERATING
        * (final["time"] - ODC_DERATING_DATE)
    )

    assert final["stock"] == pytest.approx(
        ODC_SCALED_STOCK - consumed_whole - consumed_cut, rel=ODC_TOL
    )
    # ... and strictly more is left than the nominal draw would have spared.
    assert final["stock"] > ODC_SCALED_STOCK - ODC_SCALED_CONS["e"] * (
        ODC_SCALED_DEMAND * final["time"]
    )


def test_a_profiled_reactor_draws_what_the_curve_let_it_produce(the_run):
    """The same statement for a time profile, which multiplies rather than folds.

    A profile scales how large the output is at this instant, so the reagents
    follow the curve: at 0.3 of it the reactor makes 1.2 of ``p`` and consumes
    2.4 of ``m`` and 1.2 of ``e``, not 8 and 4.
    """
    trace = settled(the_run["profiled"])
    assert trace, "the profiled reactor never ran"

    for entry in trace:
        assert_conserved(entry["balance"], f"PROFILED at t={entry['time']:g}")

        factor = odc_ramp(entry["time"])
        produced = ODC_SCALED_DEMAND * factor

        assert entry["produced"] == pytest.approx(produced, rel=1e-4)
        assert entry["draws_m"] == pytest.approx(
            ODC_SCALED_CONS["m"] * produced, rel=1e-4
        )
        assert entry["draws_e"] == pytest.approx(
            ODC_SCALED_CONS["e"] * produced, rel=1e-4
        )
        # The curve stays below 1 over the horizon, so the draw is strictly
        # below the nominal one at every stop.
        assert entry["draws_e"] < ODC_SCALED_CONS["e"] * ODC_SCALED_DEMAND


# ----------------------------------------------------------------------
# The three draws the cap must leave alone
# ----------------------------------------------------------------------


def test_a_buffered_input_still_absorbs_the_surplus(the_run):
    """A capacity is not starved: what the rule does not use is stored.

    The draw is the whole 4 the source delivers, the rule takes 1, and the
    other 3 enter the volume. ``draw == consumption + storage`` is conservation
    here, not a breach of it, so the cap must not apply.
    """
    trace = settled(the_run["buffer"])
    assert trace, "the buffer scenario never ran"

    for entry in trace:
        assert entry["buf_draws"] == pytest.approx(ODC_BUFFER_SUPPLY)
        assert entry["buf_consumes"] == pytest.approx(ODC_BUFFER_DEMAND)
        assert entry["buf_draws"] == pytest.approx(
            entry["buf_consumes"] + entry["buf_stores"], abs=1e-6
        )

    assert trace[-1]["buf_level"] > 0.0


def test_a_pure_consumer_takes_what_it_is_given(the_run):
    """No rule accounts for a sink's input, so there is no scale to cap it at."""
    for entry in settled(the_run["buffer"]):
        assert entry["sink_demand"] == pytest.approx(ODC_BUFFER_DEMAND)
        assert entry["sink_draws"] == pytest.approx(ODC_BUFFER_DEMAND)


def test_a_rule_set_selecting_nothing_draws_nothing(the_run):
    """R14 reaches the suppliers: producing nothing now also means drawing nothing.

    The guard is never true, so no rule is active. Against ``09a0b9e`` the
    component went on being served whatever its nominal demand fetched, and
    destroyed all of it.
    """
    for entry in settled(the_run["buffer"]):
        assert entry["idle_produces"] == pytest.approx(0.0)
        assert entry["idle_draws"] == pytest.approx(0.0, abs=1e-9)


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
