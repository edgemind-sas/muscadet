"""Declaring a component from data: ``muscadet.declare``.

The library's normal way of declaring a component is a subclass of ``ObjFlow``
overriding ``add_flows``. That subclass is almost never behaviour: it declares
flows, rule sets, capacities, measurement channels and transfer pairs, and every
one of those is a declaration a mapping can carry. What it really supplies is a
place to write the declaration, and the ORDER to write it in.

This file measures that the mapping form is not merely accepted but
**equivalent**: one installation is built twice, once from subclasses and once
from a spec, and the two are compared step by step over the same interactive
session. It then reads the spec-built one back and builds a third from what came
out.

The installation is deliberately the shape the two shipped use cases share: a
rated supply, a machine transforming it under a discrete control, a buffered
volume, a thermostat with a deadband reading that volume over a measurement
link, a standing loss expressed as a transfer pair, and a clock so that the
session has a date to step to.

Component classes here are prefixed ``Dcl`` because a class name declared in a
test is resolved globally, across every module pytest has imported.
"""

import math

import pytest

import cod3s
import muscadet
import muscadet.kb.continuous  # noqa: F401  -- registers the shipped classes
from muscadet.declare import ComponentSpecError

# The installation
# ================

SUPPLY_RATE = 2.0  # what the supply can deliver
MACHINE_DRAW = 2.0  # what the machine consumes at nominal
MACHINE_OUTPUT = 7.0  # what it produces from that draw

TANK_VOLUME = 200.0
TANK_INIT = 10.0
BAND_LOW = 60.0  # the thermostat calls below this
BAND_HIGH = 80.0  # and releases above this

LOSS_CONDUCTANCE = 0.02
AMBIENT = 5.0

#: (80 - 10) at 7 per hour is 10 h, and the loss adds a little. The horizon
#: sits past it so the band is certainly reached within the session.
HORIZON = 14.0

#: Two stops: one on the ramp, one past the cut-out.
STOP_MID = 5.0
STOP_WARM = 13.0

FAILURE_DATE = 3.0
NEVER = 1e9


# The reference: the same installation, written as subclasses
# ===========================================================


class DclMachine(muscadet.ObjFlow):
    """Supply in, product out, under a discrete control.

    Carries a discrete output of its own (``healthy``) beside its continuous
    one. That combination is what no shipped continuous class declares -- the
    KB classes carry continuous ports and rules, never a discrete output -- and
    it is what both worked use cases need, for a "stuck on" port in one and a
    backup interlock in the other.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="supply")
        self.add_flow_continuous_out(name="product")
        self.add_flow_in(name="call", logic="or")
        self.add_flow_out(name="healthy", var_prod_default=True)

        self.add_rules(
            name="duty",
            rules=[
                dict(
                    cond=["call"],
                    cons={"supply": MACHINE_DRAW},
                    prod={"product": MACHINE_OUTPUT},
                )
            ],
        )


class DclTank(muscadet.ObjFlow):
    """The volume: what arrives is stored, what leaves is served from it."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="product")
        self.add_flow_continuous_out(name="product")
        self.add_capacity(
            name="store",
            flow="product",
            capacity=TANK_VOLUME,
            side="out",
            content_init={"product": TANK_INIT},
            fill_rate=math.inf,
        )


class DclClock(muscadet.ObjFlow):
    """Nothing but a date the interactive session can step to."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


# The same installation, as data
# ==============================

SPEC = [
    {
        "name": "SUPPLY",
        "cls": "SourceContinuous",
        "params": {"flow": "supply", "rate": SUPPLY_RATE},
    },
    {
        "name": "MACHINE",
        "flows": [
            {"cls": "FlowContinuousIn", "name": "supply"},
            {"cls": "FlowContinuousOut", "name": "product"},
            {"cls": "FlowIn", "name": "call", "logic": "or"},
            {"cls": "FlowOut", "name": "healthy", "var_prod_default": True},
        ],
        "rules": [
            {
                "name": "duty",
                "rules": [
                    {
                        "cond": ["call"],
                        "cons": {"supply": MACHINE_DRAW},
                        "prod": {"product": MACHINE_OUTPUT},
                    }
                ],
            }
        ],
    },
    {
        "name": "TANK",
        "flows": [
            {"cls": "FlowContinuousIn", "name": "product"},
            {"cls": "FlowContinuousOut", "name": "product"},
        ],
        "capacities": [
            {
                "name": "store",
                "flow": "product",
                "capacity": TANK_VOLUME,
                "side": "out",
                "content_init": {"product": TANK_INIT},
                "fill_rate": math.inf,
            }
        ],
    },
    {
        "name": "STAT",
        "cls": "SensorContinuous",
        "params": {
            "measurement": "store",
            "control": "call",
            "direction": "below",
            "activate": BAND_LOW,
            "release": BAND_HIGH,
        },
    },
    {
        "name": "LOSS",
        "cls": "ExchangeContinuous",
        "params": {
            "flow": "product",
            "measurements": ["store"],
            "conductance": LOSS_CONDUCTANCE,
            "potential_a": {"measurement": "store"},
            "potential_b": {"const": AMBIENT},
            "transfer_name": "standing",
        },
    },
    {
        "name": "OUTSIDE",
        "cls": "ConsumerContinuous",
        "params": {"flow": "product", "demand": math.inf},
    },
    {
        "name": "CLOCK",
        "automata": [
            {
                "name": "horizon",
                "st1": "before",
                "st2": "after",
                "occ_law_12": {"cls": "delay", "time": HORIZON},
                "cond_occ_21": False,
            }
        ],
    },
]

FLOW_LINKS = (
    ("SUPPLY", "MACHINE", "supply"),
    ("MACHINE", "TANK", "product"),
    ("STAT", "MACHINE", "call"),
    ("TANK", "LOSS", "product"),
    ("LOSS", "OUTSIDE", "product"),
)

MEASUREMENT_LINKS = (("TANK", "store", "STAT"), ("TANK", "store", "LOSS"))


def wire(system):
    """The connections, identical whichever way the components were built."""
    for source, target, flow in FLOW_LINKS:
        system.connect_flow(source=source, target=target, flow_name=flow)
    for holder, channel, observer in MEASUREMENT_LINKS:
        system.connect(holder, f"{channel}_level_out", observer, f"{channel}_level_in")
    return system


def build_from_subclasses(name):
    """The reference installation."""
    system = muscadet.System(name=name)

    system.add_component(
        name="SUPPLY", cls="SourceContinuous", flow="supply", rate=SUPPLY_RATE
    )
    system.add_component(name="MACHINE", cls="DclMachine")
    system.add_component(name="TANK", cls="DclTank")
    system.add_component(
        name="STAT",
        cls="SensorContinuous",
        measurement="store",
        control="call",
        direction="below",
        activate=BAND_LOW,
        release=BAND_HIGH,
    )
    system.add_component(
        name="LOSS",
        cls="ExchangeContinuous",
        flow="product",
        measurements=["store"],
        conductance=LOSS_CONDUCTANCE,
        potential_a={"measurement": "store"},
        potential_b={"const": AMBIENT},
        transfer_name="standing",
    )
    system.add_component(
        name="OUTSIDE", cls="ConsumerContinuous", flow="product", demand=math.inf
    )
    system.add_component(name="CLOCK", cls="DclClock")
    system.comp["CLOCK"].add_atm2states(
        name="horizon",
        st1="before",
        st2="after",
        occ_law_12={"cls": "delay", "time": HORIZON},
        cond_occ_21=False,
    )

    return wire(system)


def build_from_spec(name, specs):
    """The same installation, every component built by ``build_component``."""
    system = muscadet.System(name=name)
    for spec in specs:
        muscadet.build_component(system, spec)
    return wire(system)


# Driving one installation
# ========================


def run_to(system, date):
    """Advance to ``date`` through whatever fires before it."""
    for _ in range(200):
        if system.currentTime() >= date:
            return
        if not system.isimu_active_transitions():
            return
        system.isimu_step_forward()


def trace(system):
    """What the installation does, as a tuple per stop.

    Rounded to six digits: a quantity is stored in single precision, so an
    exact comparison of two runs is only meaningful once the seventh digit is
    dropped (see the ``tests/test_literature_validation_001.py`` note).
    """
    system.isimu_start()
    rows = []
    for date in (0.0, STOP_MID, STOP_WARM):
        run_to(system, date)
        tank = system.comp["TANK"].capacities["store"]
        rows.append(
            (
                round(system.currentTime(), 6),
                round(tank.get_quantity("product"), 6),
                system.comp["MACHINE"].flows_in["call"].var_fed.value(),
                round(system.comp["MACHINE"].flows_out["product"].var_fed.value(), 6),
                system.comp["MACHINE"].flows_out["healthy"].var_fed.value(),
            )
        )
    system.isimu_stop()
    return rows


def observe(builder, name, failed=False):
    """Build, drive, snapshot, and read the components back before deleting."""
    system = builder(name)

    if failed:
        system.comp["MACHINE"].add_delay_failure_mode(
            name="machine_down",
            failure_time=FAILURE_DATE,
            failure_effects=[("product", 0.0), ("healthy", False)],
            repair_time=NEVER,
        )

    rows = trace(system)
    specs = [muscadet.component_spec(system.comp[s["name"]]) for s in SPEC]
    system.deleteSys()
    cod3s.terminate_session()

    return rows, specs


# The one fixture every comparison reads
# ======================================


@pytest.fixture(scope="module")
def the_run():
    """Four builds of one installation, and what each produced.

    Kept in a single module-scoped fixture rather than one system per test: a
    PyCATSHOO system is expensive and the suite runs near an engine ceiling
    (see ``tests/conftest.py``).
    """
    obs = {}

    obs["reference"], _ = observe(build_from_subclasses, "DclReference")
    obs["spec"], obs["read_back"] = observe(
        lambda name: build_from_spec(name, SPEC), "DclFromSpec"
    )

    # The SAME spec objects a second time. A declaration held in data is built
    # more than once by anything that reuses it -- a template, an importer, a
    # study sweeping a parameter -- and that is what the occurrence-law copy in
    # ``add_atm2states`` exists for.
    obs["spec_again"], _ = observe(
        lambda name: build_from_spec(name, SPEC), "DclFromSpecAgain"
    )

    obs["round_trip"], _ = observe(
        lambda name: build_from_spec(name, obs["read_back"]), "DclRoundTrip"
    )

    obs["reference_failed"], _ = observe(
        build_from_subclasses, "DclReferenceFailed", failed=True
    )
    obs["spec_failed"], obs["read_back_failed"] = observe(
        lambda name: build_from_spec(name, SPEC), "DclFromSpecFailed", failed=True
    )

    # The read-back spec CARRIES the failure mode, so this build declares none
    # of its own: re-applying it would declare the same mode twice and the
    # engine refuses its parameter variable ("La variable machine_down_ttf
    # existe déjà").
    obs["round_trip_failed"], _ = observe(
        lambda name: build_from_spec(name, obs["read_back_failed"]),
        "DclRoundTripFailed",
    )

    return obs


# What the mapping form is worth
# ==============================


def test_the_installation_does_something_worth_comparing(the_run):
    """A comparison of two inert runs would prove nothing.

    The tank must start cold, be heating at the mid stop with the thermostat
    calling, and have reached the band by the late one.
    """
    start, mid, late = the_run["reference"]

    assert start[1] == pytest.approx(TANK_INIT)
    assert mid[2] is True  # calling
    assert mid[3] == pytest.approx(MACHINE_OUTPUT)
    assert TANK_INIT < mid[1] < BAND_HIGH
    assert late[1] >= BAND_LOW


def test_a_spec_builds_what_the_subclasses_build(the_run):
    """Step for step, the two installations are the same installation."""
    assert the_run["spec"] == the_run["reference"]


def test_a_spec_is_not_consumed_by_the_build(the_run):
    """The same spec objects, built a second time, give the same run.

    ``TransitionModel.sanitize_occ_law`` rewrites an occurrence law's ``cls``
    entry IN PLACE and ``ObjCOD3S.from_dict`` then pops it, so a declaration
    held in data was emptied by its first use: ``{"cls": "delay", "time": 14}``
    came back ``{"time": 14}`` and the second build raised ``Missing attribute
    'cls' in OccurrenceDistributionModel``. That is the regime of anything that
    reuses a declaration -- a template, an importer replaying an export.
    """
    assert the_run["spec_again"] == the_run["reference"]


def test_the_occurrence_law_a_caller_supplies_is_left_alone():
    """The same fix, measured on the mapping itself rather than on a run."""
    system = muscadet.System(name="DclOccLaw")
    try:
        comp = muscadet.build_component(system, {"name": "C"})

        law = {"cls": "delay", "time": 1.0}
        comp.add_atm2states(name="first", st1="a", st2="b", occ_law_12=law)

        assert law == {"cls": "delay", "time": 1.0}

        # And it is still usable, which is the property that matters.
        comp.add_atm2states(name="second", st1="a", st2="b", occ_law_12=law)
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_a_component_read_back_rebuilds_the_same_installation(the_run):
    """Build, read every component back, build again from what came out."""
    assert the_run["round_trip"] == the_run["reference"]


def test_a_failure_mode_survives_the_build_and_the_read_back(the_run):
    """A derating and a discrete clamp, declared on the component.

    The failed run must differ from the healthy one -- otherwise the three
    comparisons below would hold on an inert model -- and the spec and
    round-trip builds must reproduce the failed run exactly.
    """
    assert the_run["reference_failed"] != the_run["reference"]

    late = the_run["reference_failed"][-1]
    assert late[3] == pytest.approx(0.0)  # the product is derated to nothing
    assert late[4] is False  # and the interlock says so

    assert the_run["spec_failed"] == the_run["reference_failed"]
    assert the_run["round_trip_failed"] == the_run["reference_failed"]


# What a spec holds, and what it deliberately does not
# ====================================================


def spec_of(specs, name):
    return next(spec for spec in specs if spec["name"] == name)


def test_a_read_back_spec_is_expanded_onto_objflow(the_run):
    """A shipped class comes back as its ports, not as its parameters.

    ``component_spec`` cannot recover ``activate`` and ``release`` from a built
    sensor -- nothing stores them -- so it reports what the sensor IS: three
    discrete outputs, a measurement channel and the band automaton. The class
    it came from is kept under ``source_cls`` for a caller that wants to show
    it.
    """
    stat = spec_of(the_run["read_back"], "STAT")

    assert stat["cls"] == "ObjFlow"
    assert stat["source_cls"] == "SensorContinuous"
    assert {flow["name"] for flow in stat["flows"]} == {
        "call",
        "call_activate",
        "call_release",
    }
    assert [channel["name"] for channel in stat["measurements_in"]] == ["store"]


def test_the_sensor_band_is_read_back_and_is_what_holds_the_output(the_run):
    """The deadband is a declaration of the sensor, not a derived automaton.

    ``build_component`` expands a component onto ``ObjFlow``, so nothing in the
    spec would rebuild a band the sensor's own ``set_flows`` derives from
    ``activate`` / ``release``. Left out, the round-trip sensor thresholds but
    holds nothing between the edges, and the loop chatters around a single
    value instead of settling at the band edges. That is why the band is
    recorded as DECLARED where the two genuinely derived automata -- a discrete
    output's default ok/nok pair, the pair a failure mode builds -- are not.
    """
    stat = spec_of(the_run["read_back"], "STAT")

    assert [automaton["name"] for automaton in stat["automata"]] == ["call_band"]

    band = stat["automata"][0]
    assert band["cond_occ_12"] == "call_activate_fed_out"
    assert band["cond_occ_21"] == "call_release_fed_out"


def test_a_production_condition_is_read_back_in_its_DECLARATION_form(the_run):
    """``var_prod_cond`` is stored RESOLVED, and the resolved form is not one.

    Declaring a condition replaces the operand names with the flow (or
    measurement channel) objects themselves and lifts the negation and the
    comparison into two parallel matrices beside them. Dumped as it stands, an
    operand comes back as a mapping carrying a ``name`` and no ``op``, and a
    boolean operand never resolves onto a measurement channel -- a level
    carries no state to read -- so rebuilding the sensor failed with ``Flow
    store does not exist as input nor output flow``.
    """
    stat = spec_of(the_run["read_back"], "STAT")
    activate = next(f for f in stat["flows"] if f["name"] == "call_activate")

    assert activate["var_prod_cond"] == [
        [{"name": "store", "op": "<=", "value": BAND_LOW}]
    ]

    # The two matrices derived from it are dropped rather than dumped stale.
    assert "var_prod_cond_negate" not in activate
    assert "var_prod_cond_compare" not in activate


def test_a_transfer_equation_is_read_back_through_its_registry(the_run):
    """A declared equation serialises; a bare ``Transfer`` could not.

    ``TransferPair.equation`` is excluded from the model dump, so it is read
    explicitly, through the same ``{"cls": ...}`` registry ``build_transfer``
    accepts. ``Transfer`` is deliberately absent from that registry -- its whole
    content is a Python function -- which is exactly why an equation outside it
    is refused rather than dropped.
    """
    loss = spec_of(the_run["read_back"], "LOSS")
    (pair,) = loss["transfers"]

    assert pair["name"] == "standing"
    assert pair["equation"] == {
        "cls": "ConductiveTransfer",
        "conductance": LOSS_CONDUCTANCE,
        "potential_a": {"measurement": "store"},
        "potential_b": {"const": AMBIENT},
        # ExchangeContinuous labels the equation after the pair it serves.
        "name": "standing",
    }


def test_the_runtime_handles_are_dropped_and_the_declarations_are_not(the_run):
    """A flow's dump mixes both, and only one of them belongs in a spec.

    ``var_fed``, ``var_demand`` and the sensitive-method functions are engine
    objects rebuilt at every construction; ``var_fed_default`` and
    ``var_prod_default`` are declarations that decide what the component does.
    """
    machine = spec_of(the_run["read_back"], "MACHINE")
    healthy = next(f for f in machine["flows"] if f["name"] == "healthy")

    assert healthy["cls"] == "FlowOut"
    assert healthy["var_prod_default"] is True
    assert "var_fed" not in healthy
    assert "sm_flow_fed_fun" not in healthy


def test_a_read_back_spec_is_serialisable(the_run):
    """The point of the exercise: what comes out is data, not objects."""
    import json

    payload = json.dumps(the_run["read_back"])
    assert json.loads(payload) == the_run["read_back"]


def test_a_declaration_holding_a_callable_is_refused_not_dropped():
    """An ``allocation_fun`` no mapping can carry stops the read-back.

    Dropping it silently would return a spec that rebuilds a component
    splitting an insufficient supply by a different policy, which is the class
    of silently-different model the library refuses everywhere else.
    """
    system = muscadet.System(name="DclCallable")
    try:
        comp = muscadet.build_component(
            system,
            {
                "name": "C",
                "flows": [
                    {
                        "cls": "FlowContinuousOut",
                        "name": "q",
                        "allocation_fun": lambda available, demands: demands,
                    }
                ],
            },
        )

        with pytest.raises(ComponentSpecError) as error:
            muscadet.component_spec(comp)

        assert "allocation_fun" in str(error.value)
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# The order, and the refusals it keeps reachable
# ==============================================


def test_the_declaration_order_keeps_a_refusal_reachable():
    """A rule naming a capacity must be refused, so the capacity comes first.

    The order in ``DECLARATION_SECTIONS`` is not arbitrary. A rule refuses a
    capacity name and a measurement channel name in its ``cons`` map, and a
    conduit refuses a flow a rule already consumes: declared the other way
    round, none of those three refusals can fire, and a malformed model builds
    quietly.
    """
    system = muscadet.System(name="DclOrder")
    try:
        with pytest.raises(ValueError) as error:
            muscadet.build_component(
                system,
                {
                    "name": "C",
                    "flows": [
                        {"cls": "FlowContinuousIn", "name": "q"},
                        {"cls": "FlowContinuousOut", "name": "q"},
                    ],
                    "capacities": [{"name": "store", "flow": "q", "capacity": 10.0}],
                    "rules": [
                        {
                            "name": "duty",
                            "rules": [{"cons": {"store": 1.0}, "prod": {"q": 1.0}}],
                        }
                    ],
                },
            )

        assert "store" in str(error.value)
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# What a malformed spec is told
# =============================
#
# These reach no system: the keys are checked before anything is created, so a
# malformed declaration costs no engine object. ``None`` stands in for the
# system to pin that down.


def test_an_unknown_section_is_refused_by_name():
    """A misspelled section is otherwise swallowed whole.

    The same discipline as ``ContinuousComponent.DECLARATION_KEYS`` and
    ``FlowContinuous.check_declaration_keys``: a component silently missing its
    rule set is indistinguishable from one that never declared any.
    """
    with pytest.raises(ComponentSpecError) as error:
        muscadet.build_component(None, {"name": "C", "rule": []})

    message = str(error.value)
    assert "'rule'" in message
    assert "rules" in message


def test_a_spec_without_a_name_is_refused():
    with pytest.raises(ComponentSpecError):
        muscadet.build_component(None, {"flows": []})


def test_params_on_objflow_are_refused_rather_than_dropped():
    """``ObjFlow.add_flows`` does nothing, so its ``params`` would vanish."""
    with pytest.raises(ComponentSpecError) as error:
        muscadet.build_component(None, {"name": "C", "params": {"rate": 1.0}})

    assert "rate" in str(error.value)


def test_a_section_that_is_not_a_list_is_refused():
    with pytest.raises(ComponentSpecError) as error:
        muscadet.build_component(None, {"name": "C", "flows": "q"})

    assert "flows" in str(error.value)


def test_an_unknown_failure_mode_shape_is_refused():
    """Two shapes exist on a component; a standalone mode is a component."""
    system = muscadet.System(name="DclFmShape")
    try:
        with pytest.raises(ComponentSpecError) as error:
            muscadet.build_component(
                system,
                {"name": "C", "failure_modes": [{"cls": "weibull", "name": "m"}]},
            )

        message = str(error.value)
        assert "weibull" in message
        assert "delay" in message and "exp" in message
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_delete():
    """Nothing is left to delete, and that is the point.

    Every system here is deleted at the moment its observation is taken --
    ``component_spec`` needs the components alive, and nothing after it does --
    so no system outlives its scenario. The session close stays, for the same
    reason every other file in the suite carries one.
    """
    cod3s.terminate_session()
