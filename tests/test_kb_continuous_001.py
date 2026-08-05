"""The five continuous components MUSCADET ships (R32, F4, AE18).

A source, a transformer, a capacity, a consumer and a sensor, all domain
neutral, all composition over the declaration API. What this module pins down
is that they are enough to state a threshold-controlled loop without writing a
component class at all.

The sensor is the one that carries new behaviour. A rule guard cannot read a
capacity level (R29), so a sensor reading it over a measurement link and
driving a discrete control port is the only sanctioned way to gate production
on a level (F4). Its **deadband** -- an activation and a release threshold --
is what stops a loop closed through it oscillating: inside the band the control
output holds whatever it already was, which is asserted here against a single
threshold sensor watching the very same level.

PyCATSHOO forbids more than one live system per process and
``PycSystem.simulate`` cannot be called twice on one system, so each scenario
below is built, driven and deleted before the next one starts; the fixture
snapshots what each produced, and the last is kept alive for the teardown.
"""

import cod3s
import muscadet
import pytest

# Imported for their side effect too: a component class resolves by name, so
# declaring cls="SourceContinuous" needs the class to have been imported.
from muscadet.kb.continuous import (  # noqa: F401
    ALLOCATION_KEYS,
    CapacityContinuous,
    ConsumerContinuous,
    SensorContinuous,
    SourceContinuous,
    TransformerContinuous,
)

#: The solver stops the integration ON a crossing rather than refining it to
#: machine precision, so a date is asserted within one default PDMP step.
CROSSING_TOL = 0.05

# -- The sensor pattern (AE18): a tank drained at 1, refilled on demand
PATTERN_RATE = 2.0
PATTERN_DEMAND = 1.0
PATTERN_INIT = 10.0
PATTERN_ACTIVATE = 4.0
PATTERN_RELEASE = 8.0
PATTERN_VOLUME = 100.0
#: Draining from 10 at 1, the level reaches the activation threshold at t = 6.
PATTERN_SWITCH_DATE = 6.0
PATTERN_HORIZON = 15.0

# -- The accumulator: 6 of volume, filled at 2, full at t = 3
FILL_RATE = 2.0
FILL_VOLUME = 6.0
FILL_DATE = 3.0
FILL_SENSOR_THRESHOLD = 4.0
FILL_SENSOR_DATE = 2.0
FILL_HORIZON = 8.0

# -- The deadband: a level walking up to 7 and back down to 5, inside [4, 8)
BAND_INIT = 5.0
BAND_ACTIVATE = 8.0
BAND_RELEASE = 4.0
BAND_SINGLE = 6.0
BAND_TURN_DATE = 2.0
BAND_HORIZON = 4.0

# -- The transformer: 10 of a and 50 of b make 5 of x and 2 of y
TRANS_A_RATE = 100.0
TRANS_B_RATE = 25.0
TRANS_DEMAND = 100.0

# -- The mixture: three constituents sharing one volume
MIX_CONTENT = {"m1": 30.0, "m2": 10.0, "m3": 10.0}
MIX_WEIGHTS = {"m1": 1.0, "m2": 3.0, "m3": 5.0}
MIX_DEMAND = 3.0

# -- The demand chain: a source able to supply 10, a consumer asking for 3
CHAIN_RATE = 10.0
CHAIN_DEMAND = 3.0

# -- R-3: a declaration key no component reads, one per shipped class. The
# spellings are the plausible slips, not nonsense: a missing numeric parameter
# is indistinguishable from a legitimate zero, which is what makes a swallowed
# typo report wrong numbers rather than fail.
UNKNOWN_KEY_DECLARATIONS = {
    "SourceContinuous": dict(raet=5.0),
    "TransformerContinuous": dict(flows_in=["a"], flow_out=["x"]),
    "CapacityContinuous": dict(flow="q", capacity=10.0, content_intit={"q": 1.0}),
    "ConsumerContinuous": dict(flow="q", demmand=3.0),
    "SensorContinuous": dict(measurement="tank", activate=1.0, realease=2.0),
}

#: R-3, the other way round: every key a component DOES declare, with a value
#: that builds. Checked key by key against ``DECLARATION_KEYS``, so a key added
#: to a component without a value here fails loudly rather than going untested.
FULL_DECLARATIONS = {
    "SourceContinuous": dict(
        flow="q",
        rate=1.0,
        control="c",
        control_logic="and",
        allocation="proportional",
        allocation_shares={},
        allocation_priorities={},
        allocation_fun=None,
    ),
    "TransformerContinuous": dict(
        flows_in=["a"],
        flows_out=["x"],
        controls=["c"],
        rules=[dict(cons={"a": 1.0}, prod={"x": 1.0})],
        rules_name="convert",
        allocation="proportional",
        allocation_shares={},
        allocation_priorities={},
        allocation_fun=None,
    ),
    "CapacityContinuous": dict(
        flow="q",
        flows=["q"],
        capacity=10.0,
        ports="both",
        side="out",
        demand=0.0,
        fill_rate=0.0,
        content_init={"q": 1.0},
        capacity_name="vessel",
        allocation="proportional",
        allocation_shares={},
        allocation_priorities={},
        allocation_fun=None,
    ),
    "ConsumerContinuous": dict(flow="q", flows=["q"], demand=3.0),
    "SensorContinuous": dict(
        measurement="vessel",
        control="c",
        activate=4.0,
        release=8.0,
        direction="below",
        level_default=0.0,
    ),
}


# ----------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------


class KbContinuousLeakySource(SourceContinuous):
    """A shipped component extended by a project, the way one would be.

    Declares the one key of its own and inherits the rest: the accepted set is
    unioned over the MRO rather than read off the class, so subclassing does
    not mean restating every parameter the base already takes.
    """

    DECLARATION_KEYS = ("leak",)

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.leak = float(kwargs.get("leak", 0.0))


def add_clock(comp, date):
    """Give the interactive session a date it can always step to."""
    comp.add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def walk(system, snap, horizon, limit=60):
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


def switches(trace, key):
    """The dates at which ``key`` changed value, in order."""
    dates = []
    for previous, entry in zip(trace, trace[1:]):
        if entry[key] != previous[key]:
            dates.append(entry["time"])
    return dates


def build_sensor_pattern(name):
    """A source, a capacity, a consumer and the sensor closing the loop (F4).

    The sensor reads the very capacity whose supplier it gates, so the
    connection graph carries a loop -- which must NOT be refused: neither the
    measurement link nor the control port is a continuous flow, so neither
    enters the acyclicity check (R30, AE18).
    """
    system = muscadet.System(name=name)

    system.add_component(
        name="SRC",
        cls="SourceContinuous",
        flow="q",
        rate=PATTERN_RATE,
        control="fill",
    )
    system.add_component(
        name="CAP",
        cls="CapacityContinuous",
        flow="q",
        capacity=PATTERN_VOLUME,
        capacity_name="tank",
        content_init={"q": PATTERN_INIT},
    )
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="q", demand=PATTERN_DEMAND
    )
    system.add_component(
        name="SENS",
        cls="SensorContinuous",
        measurement="tank",
        control="fill",
        direction="below",
        activate=PATTERN_ACTIVATE,
        release=PATTERN_RELEASE,
    )

    system.connect_flow(source="SRC", target="CAP", flow_name="q")
    system.connect_flow(source="CAP", target="SINK", flow_name="q")
    # The measurement link, and the control port closing the loop back
    system.connect("CAP", "tank_level_out", "SENS", "tank_level_in")
    system.connect_flow(source="SENS", target="SRC", flow_name="fill")

    return system


def pattern_snapshot(system):
    """What the sensor pattern reads right now."""
    return {
        "time": system.currentTime(),
        "level": system.comp["CAP"].capacities["tank"].get_quantity("q"),
        "inflow": system.comp["CAP"].capacities["tank"].get_inflow("q"),
        "outflow": system.comp["CAP"].capacities["tank"].get_outflow("q"),
        "supplied": system.comp["SRC"].flows_out["q"].var_fed.value(),
        "control": system.comp["SENS"].flows_out["fill"].var_fed.value(),
    }


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_declaration_scenario(obs):
    """What the shipped declarations refuse, on a system of their own."""
    system = muscadet.System(name="KbContinuousDeclaration")

    def declare(name, **specs):
        try:
            system.add_component(name=name, **specs)
        except Exception as err:
            return err
        return None

    obs["err_no_threshold"] = declare(
        "NOTHR", cls="SensorContinuous", measurement="tank", control="c"
    )
    obs["err_direction"] = declare(
        "DIR", cls="SensorContinuous", measurement="tank", activate=1.0, direction="up"
    )
    obs["err_band_above"] = declare(
        "BANDUP",
        cls="SensorContinuous",
        measurement="tank",
        activate=4.0,
        release=8.0,
        direction="above",
    )
    obs["err_band_below"] = declare(
        "BANDDOWN",
        cls="SensorContinuous",
        measurement="tank",
        activate=8.0,
        release=4.0,
        direction="below",
    )
    obs["err_ports"] = declare(
        "PORTS", cls="CapacityContinuous", flow="q", capacity=10.0, ports="sideways"
    )

    # R-3 -- a key the component does not read is refused, rather than
    # swallowed by kwargs.get() and left to take its default.
    obs["err_typo"] = declare("SRCTYPO", cls="SourceContinuous", raet=5.0)
    obs["err_typos"] = declare(
        "SRCTYPOS", cls="SourceContinuous", raet=5.0, flwo="q", control_logik="or"
    )
    obs["err_unknown_by_class"] = {
        cls_name: declare(f"UNK_{cls_name}", cls=cls_name, **specs)
        for cls_name, specs in UNKNOWN_KEY_DECLARATIONS.items()
    }
    # A key one component reads is still unknown to another: the accepted set
    # is per class, not a union over the module.
    obs["err_foreign_key"] = declare(
        "CONSALLOC", cls="ConsumerContinuous", demand=1.0, allocation_shares={}
    )

    # ... and every key a component DOES declare still builds. The risk this
    # guards against is the opposite of the bug: an over-strict check would
    # refuse legitimate declarations.
    obs["full_declarations"] = {
        cls_name: declare(f"FULL_{cls_name}", cls=cls_name, **specs)
        for cls_name, specs in FULL_DECLARATIONS.items()
    }

    # A project subclassing one of the five adds its own key without losing
    # the inherited ones.
    obs["err_subclass"] = declare("SUBTYPO", cls="KbContinuousLeakySource", raet=1.0)
    obs["subclass_full"] = declare(
        "SUBFULL", cls="KbContinuousLeakySource", rate=2.0, leak=0.5
    )

    system.deleteSys()


def run_pattern_scenario(obs):
    """AE18: the whole sensor pattern, built and simulated on the BATCH path."""
    system = build_sensor_pattern("KbContinuousPatternBatch")

    system.simulate(
        {"nb_runs": 1, "schedule": [{"start": 0, "end": PATTERN_HORIZON, "nvalues": 2}]}
    )

    obs["batch"] = pattern_snapshot(system)
    obs["batch_measured"] = system.comp["SENS"].measurements_in["tank"].get_level()

    system.deleteSys()


def run_loop_scenario(obs):
    """The same pattern, driven stop by stop: does the loop settle?"""
    system = build_sensor_pattern("KbContinuousPatternLoop")

    add_clock(system.comp["SENS"], PATTERN_HORIZON)

    system.isimu_start()
    obs["loop_trace"] = walk(system, pattern_snapshot, PATTERN_HORIZON)
    system.isimu_stop()

    obs["sensor"] = system.comp["SENS"]
    obs["source"] = system.comp["SRC"]

    # Kept alive for the teardown test, per the module convention.
    obs["system"] = system


def run_chain_scenario(obs):
    """The transformer, the mixture and the demand chain, in one step."""
    system = muscadet.System(name="KbContinuousChain")

    # -- 10 of a and 50 of b make 5 of x and 2 of y, with b at half nominal
    system.add_component(name="A", cls="SourceContinuous", flow="a", rate=TRANS_A_RATE)
    system.add_component(name="B", cls="SourceContinuous", flow="b", rate=TRANS_B_RATE)
    system.add_component(
        name="T",
        cls="TransformerContinuous",
        flows_in=["a", "b"],
        flows_out=["x", "y"],
        rules=[dict(cons={"a": 10, "b": 50}, prod={"x": 5, "y": 2})],
    )
    system.add_component(
        name="CX", cls="ConsumerContinuous", flow="x", demand=TRANS_DEMAND
    )
    system.add_component(
        name="CY", cls="ConsumerContinuous", flow="y", demand=TRANS_DEMAND
    )
    system.connect_flow(source="A", target="T", flow_name="a")
    system.connect_flow(source="B", target="T", flow_name="b")
    system.connect_flow(source="T", target="CX", flow_name="x")
    system.connect_flow(source="T", target="CY", flow_name="y")

    # -- Three constituents sharing ONE volume, drawn on together
    system.add_component(
        name="MIX",
        cls="CapacityContinuous",
        ports="out",
        flows=[
            {"name": name, "weight": weight} for name, weight in MIX_WEIGHTS.items()
        ],
        capacity=1000.0,
        capacity_name="vessel",
        content_init=dict(MIX_CONTENT),
    )
    for name in MIX_CONTENT:
        system.add_component(
            name=f"D_{name}", cls="ConsumerContinuous", flow=name, demand=MIX_DEMAND
        )
        system.connect_flow(source="MIX", target=f"D_{name}", flow_name=name)

    # -- A demand travelling back up a source / capacity / consumer chain
    system.add_component(name="CS", cls="SourceContinuous", flow="q", rate=CHAIN_RATE)
    system.add_component(name="CB", cls="CapacityContinuous", flow="q", capacity=50.0)
    system.add_component(
        name="CC", cls="ConsumerContinuous", flow="q", demand=CHAIN_DEMAND
    )
    system.connect_flow(source="CS", target="CB", flow_name="q")
    system.connect_flow(source="CB", target="CC", flow_name="q")

    add_clock(system.comp["T"], 1.0)

    system.isimu_start()
    system.isimu_step_forward()

    obs["transform"] = {
        "x": system.comp["T"].flows_out["x"].var_fed.value(),
        "y": system.comp["T"].flows_out["y"].var_fed.value(),
        "received_x": system.comp["CX"].flows_in["x"].get_delivered(),
        "received_y": system.comp["CY"].flows_in["y"].get_delivered(),
    }

    vessel = system.comp["MIX"].capacities["vessel"]
    obs["mixture"] = {
        "quantity": {name: vessel.get_quantity(name) for name in MIX_CONTENT},
        "weight": {name: vessel.weight_of(name) for name in MIX_CONTENT},
        "transit": {name: vessel.get_inflow(name) for name in MIX_CONTENT},
        "served": {
            name: system.comp["MIX"].flows_out[name].var_fed.value()
            for name in MIX_CONTENT
        },
        "received": {
            name: system.comp[f"D_{name}"].flows_in[name].get_delivered()
            for name in MIX_CONTENT
        },
    }

    obs["chain"] = {
        "supplied": system.comp["CS"].flows_out["q"].var_fed.value(),
        "source_demand": system.comp["CS"].flows_out["q"].get_var_demand_value(),
        "published": system.comp["CB"].flows_in["q"].var_demand.value(),
        "received": system.comp["CC"].flows_in["q"].get_delivered(),
    }

    obs["mix_component"] = system.comp["MIX"]

    system.isimu_stop()
    system.deleteSys()


def run_capacity_scenario(obs):
    """An accumulator fills, saturates, and throttles the source feeding it.

    Nothing writes the transit hook by hand here: the production sweep fills an
    input capacity from what its flow DELIVERS, whatever the rules drawing on it
    do -- an accumulator declares none at all and still fills at its producer's
    rate. What is asserted through the same connection is the other half -- the
    throttling: once at its volume the capacity accepts only what leaves it,
    the demand it publishes upstream collapses, and its producer delivers
    nothing (R7, AE11).
    """
    system = muscadet.System(name="KbContinuousCapacity")

    system.add_component(name="SRC", cls="SourceContinuous", flow="q", rate=FILL_RATE)
    system.add_component(
        name="CAP",
        cls="CapacityContinuous",
        ports="in",
        flow="q",
        capacity=FILL_VOLUME,
        capacity_name="tank",
        demand=1e6,
        content_init={"q": 0.0},
    )
    system.add_component(
        name="SENS",
        cls="SensorContinuous",
        measurement="tank",
        control="high",
        activate=FILL_SENSOR_THRESHOLD,
    )

    system.connect_flow(source="SRC", target="CAP", flow_name="q")
    system.connect("CAP", "tank_level_out", "SENS", "tank_level_in")

    add_clock(system.comp["SENS"], FILL_HORIZON)

    system.isimu_start()

    def snap(system):
        tank = system.comp["CAP"].capacities["tank"]
        return {
            "time": system.currentTime(),
            "level": tank.get_quantity("q"),
            "full": tank.is_full,
            "accept": tank.accept_limit("q"),
            "published": system.comp["CAP"].flows_in["q"].var_demand.value(),
            "supplied": system.comp["SRC"].flows_out["q"].var_fed.value(),
            "control": system.comp["SENS"].flows_out["high"].var_fed.value(),
        }

    obs["fill_trace"] = walk(system, snap, FILL_HORIZON)

    system.isimu_stop()
    system.deleteSys()


def run_deadband_scenario(obs):
    """A level walking up and back down INSIDE a band, watched two ways.

    Two sensors on one capacity: one carrying a deadband the level never leaves,
    one carrying the single threshold the level crosses twice. The level is
    driven through the capacity's OUTFLOW hook, so the walk is exact and the
    only thing under test is what the two sensors make of it. The outflow is
    the drivable half here: the inflow of a buffered input is rewritten at every
    evaluation from what its flow delivers (KTD13, hop 1), while nothing draws
    on this accumulator, so its outflow is written by nobody. A negative outflow
    is what makes the level rise.
    """
    system = muscadet.System(name="KbContinuousDeadband")

    system.add_component(
        name="CAP",
        cls="CapacityContinuous",
        ports="in",
        flow="q",
        capacity=100.0,
        capacity_name="tank",
        content_init={"q": BAND_INIT},
    )
    system.add_component(
        name="BAND",
        cls="SensorContinuous",
        measurement="tank",
        control="band",
        activate=BAND_ACTIVATE,
        release=BAND_RELEASE,
    )
    system.add_component(
        name="SINGLE",
        cls="SensorContinuous",
        measurement="tank",
        control="single",
        activate=BAND_SINGLE,
    )
    system.connect("CAP", "tank_level_out", "BAND", "tank_level_in")
    system.connect("CAP", "tank_level_out", "SINGLE", "tank_level_in")

    for name, date in (("turn", BAND_TURN_DATE), ("horizon", BAND_HORIZON)):
        system.comp["CAP"].add_atm2states(
            name=name,
            st1="s0",
            st2="s1",
            occ_law_12={"cls": "delay", "time": date},
            cond_occ_21=False,
        )

    def tank_of(system):
        return system.comp["CAP"].capacities["tank"]

    def snap(system):
        return {
            "time": system.currentTime(),
            "level": tank_of(system).get_quantity("q"),
            "band": system.comp["BAND"].flows_out["band"].var_fed.value(),
            "single": system.comp["SINGLE"].flows_out["single"].var_fed.value(),
        }

    system.isimu_start()
    tank_of(system).set_outflow("q", -1.0)

    trace = [snap(system)]
    turned = False
    for _ in range(40):
        if system.currentTime() >= BAND_HORIZON:
            break
        system.isimu_step_forward()
        trace.append(snap(system))
        if not turned and system.currentTime() >= BAND_TURN_DATE:
            # The level turns around, still inside the band
            tank_of(system).set_outflow("q", 1.0)
            turned = True

    obs["band_trace"] = trace
    obs["band_sensor"] = system.comp["BAND"]

    system.isimu_stop()
    system.deleteSys()


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_declaration_scenario(obs)
    run_pattern_scenario(obs)
    run_chain_scenario(obs)
    run_capacity_scenario(obs)
    run_deadband_scenario(obs)
    run_loop_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# AE18 -- the sensor pattern builds and runs
# ----------------------------------------------------------------------


def test_the_sensor_pattern_builds_and_simulates(the_run):
    """AE18: a sensor reading the capacity whose supplier it gates.

    The connection graph carries a loop -- source to capacity, capacity to
    sensor, sensor back to source -- and the system starts all the same,
    because a measurement link and a control port are not continuous flows and
    take no part in the acyclicity check (R30).
    """
    batch = the_run["batch"]

    # It ran to the horizon, and the sensor read the level it gates on
    assert batch["level"] == pytest.approx(PATTERN_ACTIVATE, abs=CROSSING_TOL)
    assert the_run["batch_measured"] == pytest.approx(batch["level"])

    # ... and the loop closed: the level fell to the activation threshold, the
    # control came on, and the source it gates started supplying again
    assert batch["control"] is True
    assert batch["supplied"] == pytest.approx(PATTERN_DEMAND)


def test_the_control_output_switches_at_the_capacity_threshold(the_run):
    """The level reaches 4 at t = 6, and the control port comes on there.

    Draining from 10 at 1 per unit of time, and detected AT the crossing: the
    comparison against the measured level compiles to a watched transition, so
    the solver stops the integration there rather than at the next step.
    """
    trace = the_run["loop_trace"]

    assert trace[0]["control"] is False, "the tank starts above the band"

    index, entry = first_stop_where(trace, "control")
    assert entry is not None, "the falling level must activate the sensor"
    assert entry["time"] == pytest.approx(PATTERN_SWITCH_DATE, abs=CROSSING_TOL)
    assert entry["level"] == pytest.approx(PATTERN_ACTIVATE, abs=CROSSING_TOL)


def test_the_loop_closed_through_the_sensor_settles(the_run):
    """The sensor gates its own supplier, and the level stops moving.

    One switch over the whole run, not one per integration step: what the
    source then delivers is exactly what the consumer draws, so the level sits
    on the activation threshold instead of walking back and forth across it.
    """
    trace = the_run["loop_trace"]

    assert switches(trace, "control") == [
        pytest.approx(PATTERN_SWITCH_DATE, abs=CROSSING_TOL)
    ]

    settled = trace[-1]
    assert settled["time"] == pytest.approx(PATTERN_HORIZON)
    assert settled["level"] == pytest.approx(PATTERN_ACTIVATE, abs=CROSSING_TOL)
    assert settled["inflow"] == pytest.approx(settled["outflow"])

    # The run walked events, not an integration grid
    assert len(trace) < 12


# ----------------------------------------------------------------------
# The deadband
# ----------------------------------------------------------------------


def test_a_level_oscillating_inside_the_band_leaves_the_control_unchanged(the_run):
    """The level walks 5 -> 7 -> 5, and a band of [4, 8) never notices.

    This is what a deadband buys, and the single-threshold sensor watching the
    very same level is the control: it switches twice on the same walk.
    """
    trace = the_run["band_trace"]

    levels = [entry["level"] for entry in trace]
    assert max(levels) == pytest.approx(7.0, abs=CROSSING_TOL)
    assert min(levels) == pytest.approx(5.0, abs=CROSSING_TOL)
    assert BAND_RELEASE <= min(levels) and max(levels) < BAND_ACTIVATE

    assert switches(trace, "band") == []
    assert all(entry["band"] is False for entry in trace)


def test_a_single_threshold_on_the_same_walk_switches_twice(the_run):
    """The degenerate sensor -- the two thresholds coinciding -- follows the level.

    Up through 6 on the way out, back down through 6 on the way home, both
    detected at the crossing.
    """
    trace = the_run["band_trace"]

    dates = switches(trace, "single")
    assert len(dates) == 2
    assert dates[0] == pytest.approx(1.0, abs=CROSSING_TOL)
    assert dates[1] == pytest.approx(3.0, abs=CROSSING_TOL)

    index, entry = first_stop_where(trace, "single")
    assert entry["level"] == pytest.approx(BAND_SINGLE, abs=CROSSING_TOL)
    assert trace[-1]["single"] is False


def test_the_deadband_is_two_declared_edges_and_a_memory(the_run):
    """What a deadband IS on the component: two comparisons and one automaton.

    Both edges are ordinary comparison operands -- the ``{name, op, value}``
    vocabulary a rule guard uses -- read on the measurement link, and the state
    that holds the output between them is a two-state automaton. Nothing here
    reads a level in Python.
    """
    sensor = the_run["band_sensor"]

    assert sensor.flows_out["band_activate"].var_prod_cond_compare == [
        [{"op": ">=", "value": BAND_ACTIVATE}]
    ]
    assert sensor.flows_out["band_release"].var_prod_cond_compare == [
        [{"op": "<", "value": BAND_RELEASE}]
    ]

    band = sensor.automata_d[f"{sensor.name()}_band_band"]
    assert [state.name for state in band.states] == [
        "band_band_released",
        "band_band_activated",
    ]

    # The sensor moves no quantity: one measurement link, and no flow in
    assert set(sensor.measurements_in) == {"tank"}
    assert sensor.flows_in == {}
    assert sensor.capacities == {}


# ----------------------------------------------------------------------
# The capacity
# ----------------------------------------------------------------------


def test_a_capacity_component_fills_and_saturates(the_run):
    """6 of volume filled at 2: full at t = 3, and it stays full."""
    trace = the_run["fill_trace"]

    assert trace[0]["level"] == pytest.approx(0.0)
    assert trace[0]["full"] is False

    index, entry = first_stop_where(trace, "full")
    assert entry is not None, "the capacity must reach its volume"
    assert entry["time"] == pytest.approx(FILL_DATE, abs=CROSSING_TOL)
    assert entry["level"] == pytest.approx(FILL_VOLUME, abs=CROSSING_TOL)

    assert trace[-1]["full"] is True


def test_a_full_capacity_throttles_its_upstream_source(the_run):
    """AE11 on the shipped component: at its volume it accepts only what leaves.

    Nothing leaves this one, so the demand it publishes upstream collapses to
    zero and the source that could supply 2 delivers nothing at all.
    """
    trace = the_run["fill_trace"]

    before = trace[0]
    assert before["supplied"] == pytest.approx(FILL_RATE)
    assert before["published"] > FILL_RATE

    settled = trace[-1]
    assert settled["full"] is True
    assert settled["accept"] == pytest.approx(0.0)
    assert settled["published"] == pytest.approx(0.0)
    assert settled["supplied"] == pytest.approx(0.0)


def test_the_sensor_reads_the_rising_level_at_its_threshold(the_run):
    """A single-threshold sensor on the filling capacity: on at 4, so at t = 2."""
    trace = the_run["fill_trace"]

    assert trace[0]["control"] is False

    index, entry = first_stop_where(trace, "control")
    assert entry is not None
    assert entry["time"] == pytest.approx(FILL_SENSOR_DATE, abs=CROSSING_TOL)
    assert entry["level"] == pytest.approx(FILL_SENSOR_THRESHOLD, abs=CROSSING_TOL)
    assert trace[-1]["control"] is True


def test_a_multi_flow_capacity_serves_a_draw_at_its_raw_composition(the_run):
    """R35 on the shipped component: three constituents, one volume, one draw.

    The draw is composed at the constituents' RAW quantity share and not at the
    share of the volume they occupy -- the weights differ from one another on
    purpose, so the two numbers cannot coincide. Asking for a pure constituent
    gets its share of the draw and no more.
    """
    mixture = the_run["mixture"]

    assert mixture["weight"] == pytest.approx(MIX_WEIGHTS)

    total = sum(mixture["quantity"].values())
    beyond = sum(
        max(MIX_DEMAND - mixture["transit"][name], 0.0) for name in MIX_CONTENT
    )
    assert beyond == pytest.approx(3 * MIX_DEMAND)

    for name in MIX_CONTENT:
        raw_share = mixture["quantity"][name] / total
        expected = min(MIX_DEMAND, mixture["transit"][name] + beyond * raw_share)
        assert mixture["served"][name] == pytest.approx(expected)
        assert mixture["received"][name] == pytest.approx(mixture["served"][name])

    weighted_total = sum(
        mixture["quantity"][name] * mixture["weight"][name] for name in MIX_CONTENT
    )
    weighted_share = (
        mixture["quantity"]["m2"] * mixture["weight"]["m2"] / weighted_total
    )
    assert mixture["served"]["m2"] != pytest.approx(beyond * weighted_share)

    # The one asked for beyond its share gets only that share; the plentiful
    # one gets everything it asked for
    assert mixture["served"]["m1"] == pytest.approx(MIX_DEMAND)
    assert mixture["served"]["m2"] < MIX_DEMAND

    # One volume, declared once, holding all three
    assert set(the_run["mix_component"].capacities["vessel"].flow_names) == set(
        MIX_CONTENT
    )


# ----------------------------------------------------------------------
# The transformer and the consumer
# ----------------------------------------------------------------------


def test_a_parameterised_transformer_scales_both_outputs_together(the_run):
    """10 of a and 50 of b make 5 of x and 2 of y, with b at half nominal.

    Both outputs come out at half: a reaction with correlated outputs is stated
    once, as a parameter, and the scarcest input sets the scale of the whole
    rule (R15). No subclass was written for it.
    """
    transform = the_run["transform"]

    assert transform["x"] == pytest.approx(2.5)
    assert transform["y"] == pytest.approx(1.0)

    assert transform["received_x"] == pytest.approx(2.5)
    assert transform["received_y"] == pytest.approx(1.0)


def test_a_consumer_demand_travels_back_up_the_chain(the_run):
    """A consumer asking for 3 behind a capacity: the source delivers 3, not 10."""
    chain = the_run["chain"]

    assert chain["published"] == pytest.approx(CHAIN_DEMAND)
    assert chain["source_demand"] == pytest.approx(CHAIN_DEMAND)
    assert chain["supplied"] == pytest.approx(CHAIN_DEMAND)
    assert chain["received"] == pytest.approx(CHAIN_DEMAND)


def test_a_gated_source_produces_only_while_its_control_holds(the_run):
    """The source's control port is a rule guard, and nothing more.

    Two rules on the shipped source, selected by the discrete input a sensor
    drives: this is the producing half of F4.
    """
    source = the_run["source"]

    assert set(source.rule_sets) == {"q_control"}
    assert [rule.name for rule in source.rule_sets["q_control"].rules] == [
        "supply",
        "idle",
    ]
    assert set(source.flows_in) == {"fill"}

    # Read at the stop before the control comes on -- and not at t = 0, where
    # an output still holds the rate it was DECLARED with, the first
    # integration not having evaluated a rule yet.
    trace = the_run["loop_trace"]
    index, _ = first_stop_where(trace, "control")
    assert trace[index - 1]["supplied"] == pytest.approx(0.0)
    assert trace[-1]["supplied"] == pytest.approx(PATTERN_DEMAND)


# ----------------------------------------------------------------------
# What the declarations refuse
# ----------------------------------------------------------------------


def test_a_sensor_without_a_threshold_is_refused(the_run):
    """A sensor is a threshold: there is no default level to watch."""
    error = the_run["err_no_threshold"]

    assert isinstance(error, ValueError)
    assert "activates at" in str(error)


def test_an_unknown_sensor_direction_is_refused(the_run):
    """A level activates a sensor from above or from below, and nothing else."""
    error = the_run["err_direction"]

    assert isinstance(error, ValueError)
    assert "'above' or 'below'" in str(error)


def test_a_deadband_declared_the_wrong_way_round_is_refused(the_run):
    """A high-level detector cannot release ABOVE where it activates.

    Nor a low-level one below it: the band would be empty in the wrong
    direction, and the sensor would activate and release at once.
    """
    for key in ("err_band_above", "err_band_below"):
        error = the_run[key]
        assert isinstance(error, ValueError)
        assert "the wrong way round" in str(error)


def test_an_unknown_capacity_port_declaration_is_refused(the_run):
    """The three shapes a capacity component takes, and no fourth."""
    error = the_run["err_ports"]

    assert isinstance(error, ValueError)
    assert "'both', 'in' or 'out'" in str(error)


# ----------------------------------------------------------------------
# R-3 -- a declaration key a component does not read is refused
# ----------------------------------------------------------------------


def test_a_misspelled_parameter_is_refused_rather_than_swallowed(the_run):
    """The filed reproduction: ``SourceContinuous(raet=5.0)``.

    It used to build, connect and simulate without complaint, delivering 0.0 --
    a model that runs and reports wrong numbers, which is the worst failure
    mode a simulator has. The error names the component and the key.
    """
    error = the_run["err_typo"]

    assert isinstance(error, ValueError)
    assert "SRCTYPO" in str(error), "the error must name the component"
    assert "'raet'" in str(error), "... and the key it could not place"
    assert "SourceContinuous" in str(error)
    # The accepted set is spelled out, so the typo is fixable from the message
    assert "rate" in str(error)


def test_every_unplaceable_key_is_named_at_once(the_run):
    """Three slips in one declaration are three names in one error."""
    error = the_run["err_typos"]

    assert isinstance(error, ValueError)
    for key in ("'control_logik'", "'flwo'", "'raet'"):
        assert key in str(error)


def test_each_shipped_component_refuses_an_unknown_key(the_run):
    """All five, not just the source: the check is on the shared base."""
    errors = the_run["err_unknown_by_class"]

    assert set(errors) == set(UNKNOWN_KEY_DECLARATIONS)

    for cls_name, error in errors.items():
        assert isinstance(error, ValueError), f"{cls_name} accepted an unknown key"
        assert cls_name in str(error)
        assert f"UNK_{cls_name}" in str(error)


def test_the_accepted_set_is_per_class(the_run):
    """A key a source reads is still unknown to a consumer.

    ``allocation_shares`` splits an insufficient supply among consumers, so it
    belongs to a component that PRODUCES. A pure consumer declares no output,
    and accepting the key there would silently do nothing.
    """
    error = the_run["err_foreign_key"]

    assert isinstance(error, ValueError)
    assert "'allocation_shares'" in str(error)
    assert "ConsumerContinuous" in str(error)


def test_every_declared_key_still_builds(the_run):
    """The risk to manage is the opposite of the bug.

    An over-strict check would refuse legitimate declarations, which is worse
    than the silent typo it removes. Every key each component declares is
    passed here at once, and the declaration must go through.
    """
    for cls_name, error in the_run["full_declarations"].items():
        assert error is None, f"{cls_name} refused its own documented keys: {error}"


@pytest.mark.parametrize(
    "component_cls",
    [
        CapacityContinuous,
        ConsumerContinuous,
        SensorContinuous,
        SourceContinuous,
        TransformerContinuous,
    ],
    ids=lambda cls: cls.__name__,
)
def test_the_accepted_set_is_exactly_what_is_exercised(component_cls):
    """No key accepted without a declaration exercising it, and none missing.

    ``metadata`` is the one key no caller declares: ``ObjFlow.__init__`` puts
    it back into the kwargs before calling ``add_flows``, so it reaches every
    component here whether or not it was asked for.
    """
    exercised = set(FULL_DECLARATIONS[component_cls.__name__]) | {"metadata"}

    assert component_cls.accepted_declaration_keys() == exercised


def test_a_subclass_adds_a_key_without_losing_the_inherited_ones(the_run):
    """A project extending a shipped component declares only what it adds."""
    assert KbContinuousLeakySource.accepted_declaration_keys() == (
        SourceContinuous.accepted_declaration_keys() | {"leak"}
    )

    # ... and the check is still live on the inherited keys
    error = the_run["err_subclass"]
    assert isinstance(error, ValueError)
    assert "'raet'" in str(error)
    assert "leak" in str(error), "the subclass key belongs to the accepted set"

    assert the_run["subclass_full"] is None, "its own key must be accepted"


def test_the_allocation_keys_are_derived_from_the_flow_model():
    """R-3's companion: the tuple is not a second, drifting copy.

    Hard-coded, a fifth allocation field added to ``FlowContinuousOut`` would
    be silently dropped by every component forwarding them. Derived, it is
    forwarded with no second edit. ``allocated`` is the split the last sweep
    computed -- runtime state, not a declaration -- and is deliberately outside
    the prefix.
    """
    declared = {
        name
        for name, field in muscadet.FlowContinuousOut.model_fields.items()
        if name.startswith("allocation")
    }

    assert set(ALLOCATION_KEYS) == declared
    assert "allocated" not in ALLOCATION_KEYS
    assert "allocation" in ALLOCATION_KEYS


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
