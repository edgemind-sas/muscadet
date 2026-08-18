"""A tank at the end of a chain fills, and a stocked one serves what it holds.

Two defects that a real port surfaced, both of the family this release exists to
close: a model that runs to completion and reports zero everywhere, with no
diagnostic at all.

**A two-sided capacity at the end of a chain never filled.**
``CapacityContinuous(ports="both")`` -- every flow declared on both sides, which
is how a tank is written when nothing suggests otherwise -- implies
``side="out"``. Placed at the end of a chain its own output is wired to nothing,
an unwired output constrains no demand (R-10), so ``evaluate_demand`` never
asked it for one and the capacity's ``fill_rate`` was **never consulted at
all**: the tank asked for nothing, its producer therefore produced nothing, and
the declared claim was accepted and inert.

The claim is what a volume asks "for ITSELF, over and above the demand it
already carries" (R36), and that "for itself" is the point -- it does not depend
on having a downstream consumer. R-10 governs whether an **output** constrains a
rule; a fill claim belongs to the **capacity**, and
``output_capacity_claims_demand`` is where the two are told apart. Both tests
stay structural: neither ever reads a demand's value, so an ``inf`` a real
consumer published keeps travelling as the "deliver whatever you can" it means.

**A stocked capacity meeting an unbounded demand served nothing.** "Deliver
whatever you can" was answered from what the component *produces*, which for a
reservoir is its declared default of zero -- so a battery holding 100 served 0
for the whole run and every model downstream of it stalled. What a capacity can
deliver is what it holds: ``Capacity.serve_limit``, with the draw capped at the
volume actually held, which is the conservation bound this module asserts
directly.

PyCATSHOO forbids more than one live system per process and ``simulate`` cannot
be called twice on one system, so each scenario below is built, driven and
deleted before the next one starts; the fixture snapshots what each produced,
and the last is kept alive for the teardown.
"""

import math

import cod3s
import muscadet
import pytest

# Imported for their side effect too: a component class resolves by name, so
# declaring cls="SourceContinuous" needs the class to have been imported.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
    TransformerContinuous,
)

#: The solver stops the integration ON a crossing rather than refining it to
#: machine precision, so a date is asserted within one default PDMP step.
TTF_TOL = 0.05

# -- The literal terminal tank: every flow on both sides, no explicit side,
# -- and an unbounded claim. It must fill at its producer's rate.
TTF_SUPPLY = 2.0
TTF_VOLUME = 10.0
#: Filling from empty at the producer's whole rate, the volume is reached here.
TTF_FULL_DATE = TTF_VOLUME / TTF_SUPPLY
TTF_HORIZON = 8.0

# -- A finite claim beside the default one, which must not have moved
TTF_FINITE_CLAIM = 1.0
TTF_PLAIN_CONTENT = 4.0
TTF_SIDE_HORIZON = 3.0

# -- The reservoir: outputs only, starting stocked
TTF_STOCK = 100.0
TTF_TANK_VOLUME = 200.0
TTF_METERED_DEMAND = 3.0
TTF_RES_HORIZON = 1.0

# -- The plant: the ported hydrogen slice, written the obvious way
TTF_H2O_RATE = 2.0
TTF_CONS = {"H2O": 4.0, "Elec": 1.0}
TTF_PROD = {"H2": 1.0, "O2": 1.0}
#: The limiting reagent: 2 of H2O arrive where the rule wants 4.
TTF_PLANT_SCALE = TTF_H2O_RATE / TTF_CONS["H2O"]
TTF_LOCAL_VOLUME = 6.0
TTF_LOCAL_CONTENT = 3.0
TTF_PLANT_HORIZON = 4.0


# ----------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------


def add_clock(comp, date):
    """Give the interactive session a date it can always step to."""
    comp.add_atm2states(
        name="clock",
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


def tank_snapshot(system, source, tank, capacity_name, flow):
    """What one source / terminal tank pair reads right now."""
    volume = system.comp[tank].capacities[capacity_name]

    return {
        "time": system.currentTime(),
        "level": volume.get_quantity(flow),
        "inflow": volume.get_inflow(flow),
        "outflow": volume.get_outflow(flow),
        "full": volume.is_full,
        "published": system.comp[tank].flows_in[flow].var_demand.value(),
        "supplied": system.comp[source].flows_out[flow].var_fed.value(),
        "received": system.comp[tank].flows_in[flow].get_delivered(),
        "served": system.comp[tank].flows_out[flow].var_fed.value(),
    }


def reservoir_snapshot(system, reservoir, consumer, capacity_name, flow):
    """What one reservoir / consumer pair reads right now."""
    volume = system.comp[reservoir].capacities[capacity_name]

    return {
        "time": system.currentTime(),
        "level": volume.get_quantity(flow),
        "inflow": volume.get_inflow(flow),
        "outflow": volume.get_outflow(flow),
        "served": system.comp[reservoir].flows_out[flow].var_fed.value(),
        "received": system.comp[consumer].flows_in[flow].get_delivered(),
    }


def predicate_reading(comp, flow_name, name):
    """One structural predicate, read on a live component.

    Read through ``getattr`` rather than called outright so that a build
    carrying no such predicate reports its absence HERE, as a ``None`` the
    predicate test rejects, instead of erroring the whole fixture and taking
    every physical assertion of this module down with it.
    """
    method = getattr(comp, name, None)

    return None if method is None else method(flow_name)


def add_terminal_tank(system, prefix, rate, volume, content, **params):
    """A source feeding a two-sided capacity whose own output is wired to nothing.

    The literal translation of a tank at the end of a chain: ``ports`` is left
    to its default of ``"both"`` and no ``side`` is declared, so the capacity
    lands on the ``"out"`` side with nothing connected to it.
    """
    system.add_component(name=f"{prefix}_SRC", cls="SourceContinuous", rate=rate)
    system.add_component(
        name=f"{prefix}_TANK",
        cls="CapacityContinuous",
        flow="q",
        capacity=volume,
        capacity_name="tank",
        content_init={"q": content},
        **params,
    )
    system.connect_flow(source=f"{prefix}_SRC", target=f"{prefix}_TANK", flow_name="q")


def add_reservoir(system, prefix, demand):
    """A stocked outputs-only capacity and the consumer drawing on it."""
    system.add_component(
        name=f"{prefix}_RES",
        cls="CapacityContinuous",
        ports="out",
        flow="e",
        capacity=TTF_TANK_VOLUME,
        capacity_name="stock",
        content_init={"e": TTF_STOCK},
    )
    system.add_component(
        name=f"{prefix}_C", cls="ConsumerContinuous", flow="e", demand=demand
    )
    system.connect_flow(source=f"{prefix}_RES", target=f"{prefix}_C", flow_name="e")


# ----------------------------------------------------------------------
# The scenarios
# ----------------------------------------------------------------------


def run_literal_tank_scenario(obs):
    """The acceptance bar: the obvious declaration, filling at its producer's rate."""
    system = muscadet.System(name="TerminalTankLiteral")

    add_terminal_tank(
        system,
        "L",
        rate=TTF_SUPPLY,
        volume=TTF_VOLUME,
        content=0.0,
        fill_rate=math.inf,
    )
    add_clock(system.comp["L_TANK"], TTF_HORIZON)

    system.isimu_start()
    obs["literal"] = walk(
        system,
        lambda s: tank_snapshot(s, "L_SRC", "L_TANK", "tank", "q"),
        TTF_HORIZON,
    )
    system.isimu_stop()

    system.deleteSys()


def run_claim_variants_scenario(obs):
    """A finite claim, and the default of 0 that must behave exactly as before."""
    system = muscadet.System(name="TerminalTankVariants")

    # A claim smaller than what the producer could deliver: the claim wins.
    add_terminal_tank(
        system,
        "F",
        rate=TTF_SUPPLY,
        volume=TTF_VOLUME,
        content=0.0,
        fill_rate=TTF_FINITE_CLAIM,
    )
    # No claim at all: a pure pass-through buffer, which asks for exactly what
    # crosses it -- and nothing crosses a tank nobody draws on.
    add_terminal_tank(
        system,
        "P",
        rate=TTF_SUPPLY,
        volume=TTF_VOLUME,
        content=TTF_PLAIN_CONTENT,
    )
    # The vent of R-10: an output nothing is connected to and no capacity sits
    # behind. It must go on constraining and claiming nothing at all.
    system.add_component(name="VENT", cls="SourceContinuous", rate=TTF_SUPPLY)
    add_clock(system.comp["F_TANK"], TTF_SIDE_HORIZON)

    system.isimu_start()
    system.isimu_step_forward()

    obs["finite"] = tank_snapshot(system, "F_SRC", "F_TANK", "tank", "q")
    obs["plain"] = tank_snapshot(system, "P_SRC", "P_TANK", "tank", "q")

    # The predicates themselves, read on the live model: the fill claim must
    # travel WITHOUT the unwired output having become a constraining one.
    obs["predicate"] = {
        f"{label}_{name}": predicate_reading(system.comp[comp_name], "q", method)
        for label, comp_name in (
            ("tank", "F_TANK"),
            ("bare", "VENT"),
            ("wired", "F_SRC"),
        )
        for name, method in (
            ("constrains", "output_constrains_demand"),
            ("capacity_claims", "output_capacity_claims_demand"),
            ("carries", "output_carries_demand"),
        )
    }

    system.isimu_stop()

    system.deleteSys()


def run_reservoir_scenario(obs):
    """A stocked capacity facing an unbounded demand, and a metered one beside it."""
    system = muscadet.System(name="TerminalTankReservoir")

    add_reservoir(system, "G", demand=math.inf)
    add_reservoir(system, "M", demand=TTF_METERED_DEMAND)
    add_clock(system.comp["G_C"], TTF_RES_HORIZON)

    system.isimu_start()

    obs["greedy"] = walk(
        system,
        lambda s: reservoir_snapshot(s, "G_RES", "G_C", "stock", "e"),
        TTF_RES_HORIZON,
    )
    obs["metered"] = reservoir_snapshot(system, "M_RES", "M_C", "stock", "e")

    system.isimu_stop()

    system.deleteSys()


def run_plant_scenario(obs):
    """The ported slice, declared the obvious way: it must run instead of stalling.

    A water source, a stocked battery, an electrolyser and a terminal hydrogen
    tank claiming without bound. Every one of the two defects is on the path:
    the tank is two-sided with no explicit side, and the battery is asked
    without bound because the tank's claim travels back through the rule.
    """
    system = muscadet.System(name="TerminalTankPlant")

    system.add_component(
        name="S_H2O", cls="SourceContinuous", flow="H2O", rate=TTF_H2O_RATE
    )
    system.add_component(
        name="B1",
        cls="CapacityContinuous",
        ports="out",
        flow="Elec",
        capacity=TTF_TANK_VOLUME,
        capacity_name="battery",
        content_init={"Elec": TTF_STOCK},
    )
    system.add_component(
        name="ELECTRO",
        cls="TransformerContinuous",
        flows_in=list(TTF_CONS),
        flows_out=list(TTF_PROD),
        rules=[dict(name="electrolysis", cons=TTF_CONS, prod=TTF_PROD)],
    )
    # The terminal tank, written the way a modeller writes one: every flow on
    # both sides, no explicit side, and "whatever you can deliver".
    system.add_component(
        name="LOCAL",
        cls="CapacityContinuous",
        flow="H2",
        capacity=TTF_LOCAL_VOLUME,
        capacity_name="tank",
        content_init={"H2": TTF_LOCAL_CONTENT},
        fill_rate=math.inf,
    )

    system.connect_flow(source="S_H2O", target="ELECTRO", flow_name="H2O")
    system.connect_flow(source="B1", target="ELECTRO", flow_name="Elec")
    system.connect_flow(source="ELECTRO", target="LOCAL", flow_name="H2")
    # O2 is produced by the rule and deliberately connected to nothing.

    add_clock(system.comp["ELECTRO"], TTF_PLANT_HORIZON)

    system.isimu_start()

    def snap(system):
        tank = system.comp["LOCAL"].capacities["tank"]
        battery = system.comp["B1"].capacities["battery"]

        return {
            "time": system.currentTime(),
            "H2": system.comp["ELECTRO"].flows_out["H2"].var_fed.value(),
            "O2": system.comp["ELECTRO"].flows_out["O2"].var_fed.value(),
            "H2O_demand": system.comp["ELECTRO"].flows_in["H2O"].var_demand.value(),
            "H2O_received": system.comp["ELECTRO"].flows_in["H2O"].get_delivered(),
            "H2_demand": system.comp["LOCAL"].flows_in["H2"].var_demand.value(),
            "level": tank.get_quantity("H2"),
            "inflow": tank.get_inflow("H2"),
            "outflow": tank.get_outflow("H2"),
            "battery_level": battery.get_quantity("Elec"),
            "battery_inflow": battery.get_inflow("Elec"),
            "battery_outflow": battery.get_outflow("Elec"),
        }

    obs["plant"] = walk(system, snap, TTF_PLANT_HORIZON)

    system.isimu_stop()

    # Kept alive for the teardown test, per the module convention.
    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_literal_tank_scenario(obs)
    run_claim_variants_scenario(obs)
    run_reservoir_scenario(obs)
    run_plant_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# Defect 1 -- a two-sided capacity at the end of a chain fills
# ----------------------------------------------------------------------


def test_a_terminal_tank_fills_at_its_producers_rate(the_run):
    """The acceptance bar, and the whole of what was silently inert before.

    ``ports`` left to its default, no ``side`` declared, ``fill_rate=inf``: the
    tank claims for itself, its producer delivers its whole rate, and the level
    rises. Against ``bad37a7`` the claim was never consulted, the tank published
    a demand of 0, the source delivered nothing and the level never moved.
    """
    rising = [entry for entry in settled(the_run["literal"]) if not entry["full"]]
    assert rising, "the tank never started filling"

    for entry in rising:
        assert math.isinf(entry["published"]), "the fill claim never reached upstream"
        assert entry["supplied"] == pytest.approx(TTF_SUPPLY)
        assert entry["received"] == pytest.approx(TTF_SUPPLY)
        assert entry["inflow"] == pytest.approx(TTF_SUPPLY)
        assert entry["level"] == pytest.approx(TTF_SUPPLY * entry["time"], rel=1e-2)

    assert rising[-1]["level"] > rising[0]["level"]


def test_the_unwired_output_of_a_terminal_tank_is_not_a_hole(the_run):
    """Nothing draws on it, so nothing leaves the volume through it.

    The other half of the defect: an unwired output backed by a capacity used to
    be treated as a modelled sink, and everything produced into the volume
    travelled straight out of it. A tank whose outlet is connected to nothing
    does not drain.
    """
    for entry in settled(the_run["literal"]):
        assert entry["outflow"] == pytest.approx(0.0)
        assert entry["served"] == pytest.approx(0.0)


def test_the_terminal_tank_reaches_its_volume_and_stops_its_producer(the_run):
    """R7 is untouched: at the volume the claim collapses and the source stops."""
    trace = settled(the_run["literal"])

    full = [entry for entry in trace if entry["full"]]
    assert full, "the tank never reached its volume"

    assert full[0]["time"] == pytest.approx(TTF_FULL_DATE, abs=TTF_TOL)
    assert full[0]["level"] == pytest.approx(TTF_VOLUME, abs=TTF_TOL)

    last = trace[-1]
    assert last["published"] == pytest.approx(0.0)
    assert last["supplied"] == pytest.approx(0.0)
    assert last["inflow"] == pytest.approx(0.0)
    assert last["level"] == pytest.approx(TTF_VOLUME, abs=TTF_TOL)


def test_a_finite_claim_is_what_the_terminal_tank_asks_for(the_run):
    """The claim sizes the draw: 1 asked for out of a producer that could give 2."""
    finite = the_run["finite"]

    assert finite["published"] == pytest.approx(TTF_FINITE_CLAIM)
    assert finite["supplied"] == pytest.approx(TTF_FINITE_CLAIM)
    assert finite["inflow"] == pytest.approx(TTF_FINITE_CLAIM)
    assert finite["outflow"] == pytest.approx(0.0)
    assert finite["level"] == pytest.approx(TTF_FINITE_CLAIM * finite["time"], rel=1e-2)


def test_a_terminal_tank_claiming_nothing_is_unchanged(the_run):
    """The default of 0 is a pure pass-through, and nothing passes through.

    The guard on the fix: every model that declares no ``fill_rate`` keeps
    exactly the behaviour it had. A tank nobody draws on and that claims nothing
    for itself asks for nothing, receives nothing, and holds its content.
    """
    plain = the_run["plain"]

    assert plain["published"] == pytest.approx(0.0)
    assert plain["supplied"] == pytest.approx(0.0)
    assert plain["inflow"] == pytest.approx(0.0)
    assert plain["outflow"] == pytest.approx(0.0)
    assert plain["level"] == pytest.approx(TTF_PLAIN_CONTENT)


def test_the_claim_travels_without_the_unwired_output_constraining(the_run):
    """R-10 is not weakened: it is the VOLUME that asks, not the output.

    ``output_constrains_demand`` still answers False for an output nothing is
    connected to, capacity or no capacity. What changed is that a capacity
    behind it carries a claim of its own, which
    ``output_capacity_claims_demand`` reports and ``output_carries_demand``
    unions -- and a bare unwired output, the vent of R-10, still carries
    nothing.
    """
    predicate = the_run["predicate"]

    assert predicate["tank_constrains"] is False
    assert predicate["tank_capacity_claims"] is True
    assert predicate["tank_carries"] is True

    assert predicate["bare_constrains"] is False
    assert predicate["bare_capacity_claims"] is False
    assert predicate["bare_carries"] is False

    # And a wired output is covered by the R-10 predicate alone: the claim
    # branch is for the case nothing is connected, never a second helping.
    assert predicate["wired_constrains"] is True
    assert predicate["wired_capacity_claims"] is False
    assert predicate["wired_carries"] is True


# ----------------------------------------------------------------------
# Defect 2 -- a stocked capacity serves an unbounded demand from its stock
# ----------------------------------------------------------------------


def test_a_reservoir_serves_an_unbounded_demand_from_its_stock(the_run):
    """ "Deliver whatever you can" is answered from what it holds, not what it makes.

    A reservoir produces nothing -- its declared default is 0 -- so answering an
    unbounded demand from production served exactly 0 for the whole run, and
    against ``bad37a7`` that is what this asserts against.
    """
    trace = settled(the_run["greedy"])
    assert trace, "the reservoir was never evaluated"

    for entry in trace:
        assert entry["served"] > 0.0, "the reservoir served nothing from its stock"
        assert entry["received"] == pytest.approx(entry["served"])

    assert trace[-1]["level"] < TTF_STOCK, "the stock never moved"


def test_a_metered_demand_on_a_reservoir_is_unchanged(the_run):
    """A consumer naming a number is served that number, out of the stock.

    The guard on the fix: nothing about a finite demand goes through the
    unbounded path, so the reservoir every existing model declares behaves
    exactly as it did.
    """
    metered = the_run["metered"]

    assert metered["served"] == pytest.approx(TTF_METERED_DEMAND)
    assert metered["received"] == pytest.approx(TTF_METERED_DEMAND)
    assert metered["outflow"] == pytest.approx(TTF_METERED_DEMAND)
    assert metered["level"] == pytest.approx(
        TTF_STOCK - TTF_METERED_DEMAND * metered["time"], rel=1e-2
    )


# ----------------------------------------------------------------------
# Conservation -- what a capacity serves, against what it has to serve with
# ----------------------------------------------------------------------


def test_a_capacity_never_serves_more_than_it_holds_plus_what_transits(the_run):
    """The invariant, asserted on every capacity of every scenario.

    An unbounded request is a statement about the absence of a bound, not a
    quantity, and ``serve_limit`` reports a stocked capacity as unbounded. What
    keeps that answerable is the cap ``draw_from_capacity`` applies: the stock
    can give what the stock holds and no more, so the level cannot be conjured
    negative within one integration step.
    """
    checked = 0

    for trace in (the_run["literal"], the_run["greedy"]):
        for entry in settled(trace):
            assert entry["outflow"] <= entry["inflow"] + entry["level"] + TTF_TOL
            assert entry["level"] >= -TTF_TOL
            checked += 1

    for entry in settled(the_run["plant"]):
        assert (
            entry["outflow"] <= entry["inflow"] + entry["level"] + TTF_TOL
        ), "the hydrogen tank served more than it holds"
        assert (
            entry["battery_outflow"]
            <= entry["battery_inflow"] + entry["battery_level"] + TTF_TOL
        ), "the battery served more than it holds"
        assert entry["battery_level"] >= -TTF_TOL
        checked += 1

    for entry in (the_run["finite"], the_run["plain"], the_run["metered"]):
        assert entry["outflow"] <= entry["inflow"] + entry["level"] + TTF_TOL
        checked += 1

    assert checked > 0, "nothing was actually checked"


# ----------------------------------------------------------------------
# The two together: the ported plant, declared the obvious way
# ----------------------------------------------------------------------


def test_the_literal_plant_runs_instead_of_reading_zero_everywhere(the_run):
    """The real bar: both defects on one path, and the plant produces.

    Against ``bad37a7`` every quantity below reads 0 for the whole run -- the
    terminal tank asks for nothing, so the electrolyser produces nothing; and
    even asked without bound the battery serves nothing, so the rule has no
    electricity. The run completes normally and reports a dead plant.
    """
    trace = settled(the_run["plant"])
    assert trace, "the plant was never evaluated"

    for entry in trace:
        # The limiting reagent, exactly as a bounded model gives it
        assert entry["H2"] == pytest.approx(TTF_PLANT_SCALE)
        assert entry["O2"] == pytest.approx(TTF_PLANT_SCALE)
        assert entry["H2O_received"] == pytest.approx(TTF_H2O_RATE)

        # The tank's claim is what makes the whole chain draw
        assert math.isinf(entry["H2_demand"])
        assert math.isinf(entry["H2O_demand"])

        # ... and the battery really is serving out of its stock
        assert entry["battery_outflow"] > 0.0


def test_the_hydrogen_tank_accumulates_what_the_plant_makes(the_run):
    """3 to start with, and 0.5 per unit time on top of it."""
    trace = settled(the_run["plant"])

    for entry in trace:
        assert entry["inflow"] == pytest.approx(TTF_PLANT_SCALE)
        assert entry["outflow"] == pytest.approx(0.0)
        assert entry["level"] == pytest.approx(
            TTF_LOCAL_CONTENT + TTF_PLANT_SCALE * entry["time"], rel=1e-2
        )

    assert trace[-1]["level"] > TTF_LOCAL_CONTENT


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
