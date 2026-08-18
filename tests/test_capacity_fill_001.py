"""A capacity that is not full claims headroom and accumulates (R36).

The complement of R7. A full capacity collapses the demand it exports upstream,
which is what stops its producer; without a claim of its own, though, a capacity
that is *not* full asks for exactly what passes through it -- so its inflow can
never exceed its outflow and a tank between a source and a consumer can never
fill.

The claim is declared, as :attr:`muscadet.capacity.Capacity.fill_rate`, and it is
**added** to the demand crossing the capacity rather than replacing it: a
transforming component still maps its output demand onto its inputs through the
active rule's coefficients (R34), and the volume asks for its fill rate on top.
``math.inf`` is the "whatever you can deliver" claim, which needs no bound of its
own -- a delivery is already the lesser of production and demand (R6), so a tank
connected to a pump fills at the pump's rate until it is full.

The default is 0, a pure pass-through buffer: every model that declares no fill
rate keeps exactly the behaviour it had, which one scenario here pins down
alongside the filling one.

PyCATSHOO forbids more than one live system per process and
``PycSystem.simulate`` cannot be called twice on one system, so each scenario
below is built, driven and deleted before the next one starts; the fixture
snapshots what each produced, and the last is kept alive for the teardown.
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
)

#: The solver stops the integration ON a crossing rather than refining it to
#: machine precision, so a date is asserted within one default PDMP step.
CROSSING_TOL = 0.05

# -- The filling buffer: 10 supplied, 3 drawn, 20 of volume
FILL_SUPPLY = 10.0
FILL_DRAW = 3.0
FILL_VOLUME = 20.0
#: What is left over for the volume once the consumer is served.
FILL_SURPLUS = FILL_SUPPLY - FILL_DRAW
#: Filling from empty at 7, the volume of 20 is reached at t = 20 / 7.
FILL_DATE = FILL_VOLUME / FILL_SURPLUS
FILL_HORIZON = 5.0

# -- The short supply: 4 arriving, 10 asked for, an empty volume in between
SHORT_SUPPLY = 4.0
SHORT_DEMAND = 10.0
#: The pass-through chain driven beside it: same supply as the filling one.
PLAIN_VOLUME = 20.0
SHORT_HORIZON = 1.0

# -- Nothing drawing on the buffer: it takes the source's whole rate
IDLE_VOLUME = 5.0
#: Filling from empty at 10, the volume of 5 is reached at t = 0.5.
IDLE_DATE = IDLE_VOLUME / FILL_SUPPLY
IDLE_HORIZON = 1.0

# -- The mapping: 2 of ``a`` make 1 of ``x``, with ``a`` held in a tank
MAP_SUPPLY = 12.0
MAP_DRAW = 3.0
MAP_COEFFICIENT = 2.0
#: What the rules need for the 3 of ``x`` asked of them, through the R34 mapping.
MAP_REQUIRED = MAP_DRAW * MAP_COEFFICIENT
#: What the tank claims for itself on top of it.
MAP_FILL_RATE = 4.0
MAP_VOLUME = 40.0
MAP_HORIZON = 2.0

# -- One producer, a buffer and a direct consumer sharing what it supplies
SPLIT_DIRECT_DEMAND = 3.0
SPLIT_SINK_DEMAND = 1.0
SPLIT_VOLUME = 100.0
SPLIT_HORIZON = 1.0

# -- A two-mode plant buffering an input the second mode stops consuming
SWITCH_RATE = 1.0
SWITCH_VOLUME = 1000.0
SWITCH_DATE = 10.0
SWITCH_HORIZON = 40.0
#: A repair that does not happen within the horizon.
SWITCH_NEVER = 1e6


# ----------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------


class CapFillUnit(muscadet.ObjFlow):
    """2 of ``a`` make 1 of ``x``, with ``a`` held in a tank claiming a fill rate.

    The tank sits upstream of the rules, so what the rules draw from it and what
    the component asks its own producer for are two different quantities: the
    first is the R34 mapping and the capacity never touches it, the second
    carries the fill claim on top (R36).
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_out(name="x")
        self.add_rules(
            name="unit",
            rules=[dict(cons={"a": MAP_COEFFICIENT}, prod={"x": 1.0})],
        )
        self.add_capacity(
            name="cuve",
            flow="a",
            capacity=MAP_VOLUME,
            side="in",
            fill_rate=MAP_FILL_RATE,
            content_init={"a": 0.0},
        )


class SwitchCommand(muscadet.ObjFlow):
    """A plain discrete output: the boolean operand the plant's guards read."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="run", var_prod_default=True)


class TwoModePlant(muscadet.ObjFlow):
    """Two guarded rules over a buffered input only the first one consumes."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="run", logic="and")
        self.add_flow_continuous_in(name="b")
        self.add_flow_continuous_out(name="p")
        self.add_capacity(
            name="bufB",
            flow="b",
            capacity=SWITCH_VOLUME,
            side="in",
            content_init={"b": 0.0},
        )
        self.add_rules(
            name="plant",
            rules=[
                dict(name="running", cond="run", cons={"b": 1.0}, prod={"p": 1.0}),
                dict(name="idle", cond="not run", prod={"p": 0.0}),
            ],
        )


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


def first_stop_where(trace, key, value=True):
    """The first recorded stop at which ``key`` reads ``value``."""
    for index, entry in enumerate(trace):
        if entry[key] == value:
            return index, entry
    return None, None


def add_chain(system, prefix, rate, volume, demand, fill_rate=None):
    """A source, a buffering capacity and a consumer, wired in that order."""
    params = {} if fill_rate is None else {"fill_rate": fill_rate}

    system.add_component(name=f"{prefix}_SRC", cls="SourceContinuous", rate=rate)
    system.add_component(
        name=f"{prefix}_BUF",
        cls="CapacityContinuous",
        flow="q",
        capacity=volume,
        capacity_name="tank",
        **params,
    )
    system.add_component(name=f"{prefix}_SINK", cls="ConsumerContinuous", demand=demand)

    system.connect_flow(source=f"{prefix}_SRC", target=f"{prefix}_BUF", flow_name="q")
    system.connect_flow(source=f"{prefix}_BUF", target=f"{prefix}_SINK", flow_name="q")


def chain_snapshot(system, prefix):
    """What one source / buffer / consumer chain reads right now."""
    tank = system.comp[f"{prefix}_BUF"].capacities["tank"]

    return {
        "time": system.currentTime(),
        "level": tank.get_quantity("q"),
        "inflow": tank.get_inflow("q"),
        "outflow": tank.get_outflow("q"),
        "full": tank.is_full,
        "empty": tank.is_empty,
        "published": system.comp[f"{prefix}_BUF"].flows_in["q"].var_demand.value(),
        "supplied": system.comp[f"{prefix}_SRC"].flows_out["q"].var_fed.value(),
        "served": system.comp[f"{prefix}_BUF"].flows_out["q"].var_fed.value(),
        "received": system.comp[f"{prefix}_SINK"].flows_in["q"].get_delivered(),
    }


# ----------------------------------------------------------------------
# The scenarios
# ----------------------------------------------------------------------


def run_fill_scenario(obs):
    """A tank between a source of 10 and a consumer of 3 fills, then saturates."""
    system = muscadet.System(name="CapacityFillRise")

    add_chain(
        system,
        "F",
        rate=FILL_SUPPLY,
        volume=FILL_VOLUME,
        demand=FILL_DRAW,
        fill_rate=math.inf,
    )
    add_clock(system.comp["F_SINK"], FILL_HORIZON)

    system.isimu_start()
    obs["fill_trace"] = walk(system, lambda s: chain_snapshot(s, "F"), FILL_HORIZON)
    system.isimu_stop()

    system.deleteSys()


def run_passthrough_scenario(obs):
    """Two chains: an empty claiming volume, and one claiming nothing at all.

    ``SHORT`` is asked for more than arrives and holds no stock, so the R7 rule
    stands whatever it claims upstream: it serves only what transits through it.
    ``PLAIN`` declares no fill rate and is the guard on the default -- the same
    supply and the same draw as the filling scenario, and a level that never
    moves.
    """
    system = muscadet.System(name="CapacityFillPassthrough")

    add_chain(
        system,
        "SHORT",
        rate=SHORT_SUPPLY,
        volume=PLAIN_VOLUME,
        demand=SHORT_DEMAND,
        fill_rate=math.inf,
    )
    add_chain(
        system,
        "PLAIN",
        rate=FILL_SUPPLY,
        volume=PLAIN_VOLUME,
        demand=FILL_DRAW,
    )
    add_clock(system.comp["SHORT_SINK"], SHORT_HORIZON)

    system.isimu_start()
    system.isimu_step_forward()

    obs["short"] = chain_snapshot(system, "SHORT")
    obs["plain"] = chain_snapshot(system, "PLAIN")

    system.isimu_stop()

    system.deleteSys()


def run_idle_scenario(obs):
    """Nothing drawing on the buffer: it takes the source's whole rate.

    The consumer is declared with a demand of 0 rather than left out, because an
    output NOBODY is connected to is a modelled sink: it delivers what it
    produces, capacity or no capacity, which ``tests/test_rules_eval_001.py``
    pins down. A connected consumer asking for nothing is the same physical
    statement -- no draw -- expressed where the model can see it.
    """
    system = muscadet.System(name="CapacityFillIdle")

    add_chain(
        system,
        "I",
        rate=FILL_SUPPLY,
        volume=IDLE_VOLUME,
        demand=0.0,
        fill_rate=math.inf,
    )
    add_clock(system.comp["I_SINK"], IDLE_HORIZON)

    system.isimu_start()
    obs["idle_trace"] = walk(system, lambda s: chain_snapshot(s, "I"), IDLE_HORIZON)
    system.isimu_stop()

    system.deleteSys()


def run_mapping_scenario(obs):
    """A transforming component whose input capacity claims a fill rate (R34, R36)."""
    system = muscadet.System(name="CapacityFillMapping")

    system.add_component(name="SRC", cls="SourceContinuous", flow="a", rate=MAP_SUPPLY)
    system.add_component(name="UNIT", cls="CapFillUnit")
    system.add_component(
        name="CONS", cls="ConsumerContinuous", flow="x", demand=MAP_DRAW
    )

    system.connect_flow(source="SRC", target="UNIT", flow_name="a")
    system.connect_flow(source="UNIT", target="CONS", flow_name="x")

    add_clock(system.comp["CONS"], MAP_HORIZON)

    system.isimu_start()
    system.isimu_step_forward()

    cuve = system.comp["UNIT"].capacities["cuve"]
    obs["mapping"] = {
        "time": system.currentTime(),
        "required": system.comp["UNIT"].flows_in["a"].demand_required,
        "published": system.comp["UNIT"].flows_in["a"].var_demand.value(),
        "delivered": system.comp["UNIT"].flows_in["a"].get_delivered(),
        "x": system.comp["UNIT"].flows_out["x"].var_fed.value(),
        "received": system.comp["CONS"].flows_in["x"].get_delivered(),
        "inflow": cuve.get_inflow("a"),
        "outflow": cuve.get_outflow("a"),
        "level": cuve.get_quantity("a"),
    }

    system.isimu_stop()

    system.deleteSys()


def run_split_scenario(obs):
    """One producer, a claiming buffer and a direct consumer sharing its supply."""
    system = muscadet.System(name="CapacityFillSplit")

    system.add_component(name="SRC", cls="SourceContinuous", rate=FILL_SUPPLY)
    system.add_component(
        name="BUF",
        cls="CapacityContinuous",
        flow="q",
        capacity=SPLIT_VOLUME,
        capacity_name="tank",
        fill_rate=math.inf,
    )
    system.add_component(
        name="DIRECT", cls="ConsumerContinuous", demand=SPLIT_DIRECT_DEMAND
    )
    system.add_component(
        name="SINK", cls="ConsumerContinuous", demand=SPLIT_SINK_DEMAND
    )

    system.connect_flow(source="SRC", target="BUF", flow_name="q")
    system.connect_flow(source="SRC", target="DIRECT", flow_name="q")
    system.connect_flow(source="BUF", target="SINK", flow_name="q")

    add_clock(system.comp["DIRECT"], SPLIT_HORIZON)

    system.isimu_start()
    system.isimu_step_forward()

    obs["split"] = {
        "supplied": system.comp["SRC"].flows_out["q"].var_fed.value(),
        "buffer": system.comp["BUF"].flows_in["q"].get_delivered(),
        "direct": system.comp["DIRECT"].flows_in["q"].get_delivered(),
        "level": system.comp["BUF"].capacities["tank"].get_quantity("q"),
    }

    system.isimu_stop()

    system.isimu_stop()
    system.deleteSys()


def run_mode_switch_scenario(obs):
    """A buffered input the newly active rule stops consuming (KTD13, hop 1).

    Hop 1 -- what a flow delivers enters the capacity buffering it -- is a
    property of the WIRING, not of the rule currently selected. Driven from the
    active rule's ``cons`` map instead, the inflow of a flow the new mode stops
    consuming would keep the rate the PREVIOUS mode wrote, while the outflow is
    correctly zeroed over the whole rule-set footprint: the volume would then
    integrate an imbalance no producer delivers and CREATE quantity.
    """
    system = muscadet.System(name="CapacityFillModeSwitch")

    system.add_component(name="SRC", cls="SourceContinuous", flow="b", rate=SWITCH_RATE)
    system.add_component(name="CMD", cls="SwitchCommand")
    system.add_component(name="PLANT", cls="TwoModePlant")
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="p", demand=SWITCH_RATE
    )

    system.connect_flow(source="SRC", target="PLANT", flow_name="b")
    system.connect_flow(source="CMD", target="PLANT", flow_name="run")
    system.connect_flow(source="PLANT", target="SINK", flow_name="p")

    # The command drops at SWITCH_DATE: the guard selects the idle rule, which
    # consumes nothing of ``b``.
    system.comp["CMD"].add_delay_failure_mode(
        name="trip",
        failure_time=SWITCH_DATE,
        failure_effects=[("run_fed_available_out", False)],
        repair_time=SWITCH_NEVER,
    )
    add_clock(system.comp["SINK"], SWITCH_HORIZON)

    def snap(system):
        tank = system.comp["PLANT"].capacities["bufB"]
        return {
            "time": system.currentTime(),
            "level": tank.get_quantity("b"),
            "inflow": tank.get_inflow("b"),
            "outflow": tank.get_outflow("b"),
            "delivered": system.comp["PLANT"].flows_in["b"].get_delivered(),
            "mode": getattr(
                system.comp["PLANT"].rule_sets["plant"].active_rule(), "name", None
            ),
        }

    system.isimu_start()
    obs["switch_trace"] = walk(system, snap, SWITCH_HORIZON)
    system.isimu_stop()

    # Kept alive for the teardown test, per the module convention.
    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_fill_scenario(obs)
    run_passthrough_scenario(obs)
    run_idle_scenario(obs)
    run_mapping_scenario(obs)
    run_split_scenario(obs)
    run_mode_switch_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# R36 -- a capacity with room claims headroom and accumulates
# ----------------------------------------------------------------------


def test_a_buffer_claims_headroom_and_accumulates(the_run):
    """10 supplied, 3 drawn, and the 7 left over stay in the volume.

    The claim is what makes the producer deliver its whole rate: without it the
    buffer would publish the 3 its consumer asks for, the source would deliver
    3, and inflow would equal outflow forever.
    """
    trace = the_run["fill_trace"]

    rising = [entry for entry in trace if entry["time"] > 0.0 and not entry["full"]]
    assert rising, "the buffer never started filling"

    for entry in rising:
        # The whole of what the source can supply arrives ...
        assert entry["supplied"] == pytest.approx(FILL_SUPPLY)
        assert entry["inflow"] == pytest.approx(FILL_SUPPLY)
        # ... the consumer is served in full out of what transits ...
        assert entry["outflow"] == pytest.approx(FILL_DRAW)
        assert entry["received"] == pytest.approx(FILL_DRAW)
        # ... and the difference accumulates
        assert entry["level"] == pytest.approx(FILL_SURPLUS * entry["time"], rel=1e-2)

    # The claim itself: "whatever you can deliver", bounded by the producer
    assert math.isinf(rising[0]["published"])

    # The level RISES: this is the half that did not work before R36
    assert rising[-1]["level"] > rising[0]["level"]


def test_the_buffer_reaches_its_volume_at_the_expected_date(the_run):
    """Empty, filled at 7 while serving 3: full at t = 20 / 7."""
    trace = the_run["fill_trace"]

    index, entry = first_stop_where(trace, "full")
    assert entry is not None, "the buffer never reached its volume"

    assert entry["time"] == pytest.approx(FILL_DATE, abs=CROSSING_TOL)
    assert entry["level"] == pytest.approx(FILL_VOLUME, abs=CROSSING_TOL)


def test_a_full_buffer_stops_claiming_and_the_level_holds(the_run):
    """R7, unchanged: the claim collapses with the demand, and its producer follows.

    The throttling half of a capacity's effect on demand. Once at its volume it
    exports only what currently leaves it, so the source delivers the 3 the
    consumer draws instead of the 10 it could supply.
    """
    settled = the_run["fill_trace"][-1]

    assert settled["time"] == pytest.approx(FILL_HORIZON)
    assert settled["full"] is True

    # The claim is gone: what is published is what leaves the volume
    assert settled["published"] == pytest.approx(FILL_DRAW)
    assert settled["supplied"] == pytest.approx(FILL_DRAW)
    assert settled["supplied"] < FILL_SUPPLY

    # ... so the volume is in balance and the level stops moving
    assert settled["inflow"] == pytest.approx(settled["outflow"])
    assert settled["level"] == pytest.approx(FILL_VOLUME, abs=CROSSING_TOL)

    # ... and the consumer keeps being served throughout
    assert settled["received"] == pytest.approx(FILL_DRAW)


def test_a_buffer_nothing_draws_on_takes_the_whole_rate(the_run):
    """No draw at all: the tank fills at the source's rate until it is full."""
    trace = the_run["idle_trace"]

    rising = [entry for entry in trace if entry["time"] > 0.0 and not entry["full"]]
    assert rising, "the buffer never started filling"

    for entry in rising:
        assert entry["inflow"] == pytest.approx(FILL_SUPPLY)
        assert entry["outflow"] == pytest.approx(0.0)
        assert entry["level"] == pytest.approx(FILL_SUPPLY * entry["time"], rel=1e-2)

    index, entry = first_stop_where(trace, "full")
    assert entry is not None, "the buffer never reached its volume"
    assert entry["time"] == pytest.approx(IDLE_DATE, abs=CROSSING_TOL)

    # ... and once full it asks for nothing, so its producer supplies nothing
    settled = trace[-1]
    assert settled["published"] == pytest.approx(0.0)
    assert settled["supplied"] == pytest.approx(0.0)
    assert settled["level"] == pytest.approx(IDLE_VOLUME, abs=CROSSING_TOL)


# ----------------------------------------------------------------------
# What the claim does NOT change
# ----------------------------------------------------------------------


def test_an_empty_buffer_still_serves_only_what_transits(the_run):
    """R7, unchanged: 4 arrive, 10 are asked for, and the volume holds nothing.

    A claim asks for more upstream; it cannot invent a stock. What the producer
    has is what transits, and an empty capacity serves no more than that.
    """
    short = the_run["short"]

    assert short["empty"] is True
    assert short["inflow"] == pytest.approx(SHORT_SUPPLY)
    assert short["outflow"] == pytest.approx(SHORT_SUPPLY)
    assert short["served"] == pytest.approx(SHORT_SUPPLY)
    assert short["received"] == pytest.approx(SHORT_SUPPLY)
    assert short["received"] < SHORT_DEMAND

    # Nothing accumulated: there was nothing left over to accumulate
    assert short["level"] == pytest.approx(0.0)


def test_a_buffer_claiming_nothing_is_the_pass_through_it_was(the_run):
    """The default: no fill rate declared, and the level never moves.

    The same 10 supplied and 3 drawn as the filling chain, and the buffer takes
    in exactly what it passes on -- which is what every model written before R36
    relies on.
    """
    plain = the_run["plain"]

    assert plain["published"] == pytest.approx(FILL_DRAW)
    assert plain["supplied"] == pytest.approx(FILL_DRAW)
    assert plain["inflow"] == pytest.approx(FILL_DRAW)
    assert plain["outflow"] == pytest.approx(FILL_DRAW)
    assert plain["received"] == pytest.approx(FILL_DRAW)

    assert plain["level"] == pytest.approx(0.0)
    assert plain["full"] is False


def test_the_claim_is_added_to_the_mapping_and_does_not_replace_it(the_run):
    """R34 stands: the rules face the mapped demand, the volume claims on top.

    3 of ``x`` are asked for and the rule consumes 2 of ``a`` per ``x``, so the
    rules need 6 whatever the tank does. The tank asks for its own 4 on top, the
    source delivers the 10, the rules draw their 6, and the 4 left over fill the
    volume.
    """
    mapping = the_run["mapping"]

    # The mapping, untouched by the capacity
    assert mapping["required"] == pytest.approx(MAP_REQUIRED)

    # ... and the claim ADDED to it, not replacing it
    assert mapping["published"] == pytest.approx(MAP_REQUIRED + MAP_FILL_RATE)
    assert mapping["delivered"] == pytest.approx(MAP_REQUIRED + MAP_FILL_RATE)

    # The rules ran at the scale the mapping asked for
    assert mapping["x"] == pytest.approx(MAP_DRAW)
    assert mapping["received"] == pytest.approx(MAP_DRAW)
    assert mapping["outflow"] == pytest.approx(MAP_REQUIRED)

    # ... and the tank kept the difference
    assert mapping["inflow"] == pytest.approx(MAP_REQUIRED + MAP_FILL_RATE)
    assert mapping["level"] == pytest.approx(MAP_FILL_RATE * mapping["time"], rel=1e-2)


def test_the_distributed_total_is_still_the_available_quantity(the_run):
    """A claiming buffer takes part in an allocation like any other consumer.

    Its demand is unbounded, so it is regularised to the whole quantity
    available before any split is proposed -- and the split still hands out
    exactly what the producer has, no more and no less.
    """
    split = the_run["split"]

    assert split["supplied"] == pytest.approx(FILL_SUPPLY)
    assert split["buffer"] + split["direct"] == pytest.approx(FILL_SUPPLY)

    assert split["buffer"] > 0.0
    assert split["direct"] > 0.0
    assert split["direct"] <= SPLIT_DIRECT_DEMAND + 1e-9

    # What the buffer was allocated, less what its own consumer draws, stocks up
    assert split["level"] > 0.0


# ----------------------------------------------------------------------
# KTD13 hop 1 -- what enters a buffered input never depends on the mode
# ----------------------------------------------------------------------


def test_a_buffered_input_the_active_rule_stops_consuming_creates_nothing(the_run):
    """The buffer of an input the idle mode does not consume must not fill.

    Once the guard flips, the plant demands nothing of ``b``, so its producer
    delivers nothing and nothing enters the volume. A first hop driven from the
    active rule's ``cons`` map would leave the inflow at the running mode's
    rate while the outflow was zeroed, and the level would climb on quantity no
    producer ever delivered.
    """
    trace = the_run["switch_trace"]

    # Strictly after the switching date: the stop at which the transition fires
    # reports the state the equations were last evaluated in, and they are
    # re-evaluated on the integration that follows it.
    idle = [
        entry
        for entry in trace
        if entry["mode"] == "idle" and entry["time"] > SWITCH_DATE
    ]
    assert idle, "the guard never selected the idle rule"
    assert idle[-1]["time"] == pytest.approx(SWITCH_HORIZON, abs=CROSSING_TOL)

    for entry in idle:
        # Nothing is asked for, so nothing is delivered ...
        assert entry["delivered"] == pytest.approx(0.0, abs=1e-9)
        # ... and what enters the volume is exactly that, never the rate the
        # running mode left behind.
        assert entry["inflow"] == pytest.approx(entry["delivered"], abs=1e-9)
        assert entry["outflow"] == pytest.approx(0.0, abs=1e-9)
        assert entry["level"] == pytest.approx(0.0, abs=1e-9)


def test_the_running_mode_still_transits_what_it_consumes(the_run):
    """The same hop, in the mode that DOES consume: it transits, it hoards nothing.

    Guards against fixing the leak by never filling at all: while the running
    rule holds, its whole supply crosses the buffer.
    """
    trace = the_run["switch_trace"]

    running = [
        entry for entry in trace if entry["mode"] == "running" and entry["time"] > 0.0
    ]
    assert running, "the plant never ran"

    for entry in running:
        assert entry["delivered"] == pytest.approx(SWITCH_RATE)
        assert entry["inflow"] == pytest.approx(SWITCH_RATE)
        assert entry["outflow"] == pytest.approx(SWITCH_RATE)
        assert entry["level"] == pytest.approx(0.0, abs=1e-9)


# ----------------------------------------------------------------------
# What the declaration refuses
# ----------------------------------------------------------------------


def test_a_negative_fill_rate_is_refused():
    """A claim is a rate asked for: there is no negative amount to ask for."""
    with pytest.raises(ValueError, match="fill rate"):
        muscadet.capacity.Capacity(
            name="cuve", flows=["q"], capacity=10.0, fill_rate=-1.0
        )


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
