"""A chain of controllers evaluates upstream first, and a cycle is refused (R45).

What this unit pins down
------------------------
A controller reads a value another controller publishes (R4), so a model may
hold a whole chain of them. Two things then have to be true, and neither was:

* the chain must settle in **one** evaluation of the equation set. A
  controller's ``compute_controls`` republishes what its observation inputs
  currently carry, so a downstream controller evaluated BEFORE its upstream one
  republishes the value of the previous evaluation. Three controllers ordered
  backwards therefore take three evaluations to carry one number from end to
  end, silently, and the model reports numbers that lag its own state;
* a chain that closes on itself has no evaluation order at all, and must be
  refused rather than run in whichever order the declarations happened to fall
  in.

Until this unit, a controller's equation drew from the published-measurement
band in **declaration order**, which is to say from the order the components
were written down in. Declaring the chain downstream first was enough to get
the backwards evaluation, and nothing anywhere said so.

How the observation is taken
----------------------------
The lag is invisible at a simulation stop: the solver evaluates the equation
set many times per integration step, so a chain three evaluations deep has
caught up by the time anything is read back. It is only visible **inside** one
evaluation, which is why the chain scenario wraps each controller's equation
and records what it published at every call. Within one pass of the equation
set, the three publications are equal when the order is right, and differ by
one and two evaluations of lag when it is not.

The chain is declared DOWNSTREAM FIRST on purpose: declaration order is then
the wrong order, so the test can only pass on an order derived from the wiring.

The three scenarios are built one after the other, each system deleted before
the next: PyCATSHOO forbids more than one live system per process. The last is
kept alive for the teardown test, per the module convention.
"""

import cod3s
import muscadet
import pytest
from muscadet import ordering

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

#: Every tank of these montages rises by one unit per unit time, so a level and
#: a date are the same number.
UNIT_RATE = 1.0

#: Where the chain session stops driving. Short on purpose: the observation is
#: taken per equation evaluation, of which there are thousands per unit time.
CHAIN_HORIZON = 3.0

#: Dates the chain session is given something to stop at.
CHAIN_CLOCKS = (1.0, 2.0, 3.0)

#: The gate montage's two thresholds, on the level and on what a first
#: controller republishes of it.
GATE_ALARM_THRESHOLD = 2.0
GATE_SHUT_THRESHOLD = 4.0

#: Where the gate session stops driving: past both thresholds.
GATE_HORIZON = 5.0

#: Dates the gate session is given something to stop at.
GATE_CLOCKS = (1.0, 3.0, 5.0)


class ObjCtrlOrdering001Sink(muscadet.ObjFlow):
    """A discrete consumer, so a logic gate has somewhere to export to."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="g", logic="or")


def add_clock(comp, date):
    """Give the interactive session a date it can always stop at."""
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def add_republishing_controller(system, name, reads, publishes):
    """A controller republishing one observation input under another name."""
    system.add_component(
        name=name,
        cls="ObjCtrl",
        controls_in=[{"name": reads}],
        controls_out=[
            {
                "name": publishes,
                "kind": "value",
                "emit": {"op": "republish", "input": reads},
            }
        ],
    )


def record_control_equations(system, stages):
    """Wrap each controller's equation, recording what every call published.

    The only observation that sees INSIDE one evaluation of the equation set.
    PyCATSHOO resolves the equation method by name at every call, so an
    instance attribute shadowing the bound method is enough, and it sees every
    call the solver makes -- not a sample of them.

    Returns
    -------
    list
        Filled during the run with ``(component, published value)``, one entry
        per call, in call order.
    """
    calls = []

    def wrap(comp, published):
        original = comp.compute_controls

        def wrapper():
            original()
            calls.append((comp.basename(), comp.controls_out[published].get_level()))

        comp.compute_controls = wrapper

    for name, _reads, publishes in stages:
        wrap(system.comp[name], publishes)

    return calls


def passes_of(calls, width):
    """``calls`` cut into consecutive passes of the equation set."""
    return [calls[start : start + width] for start in range(0, len(calls), width)]


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_cycle_scenario(obs):
    """Three controllers whose republications close on themselves."""
    system = muscadet.System(name="ObjCtrlOrderingCycle")

    add_republishing_controller(system, "CYC_1", "s3", "s1")
    add_republishing_controller(system, "CYC_2", "s1", "s2")
    add_republishing_controller(system, "CYC_3", "s2", "s3")

    system.connect("CYC_1", "s1_level_out", "CYC_2", "s1_level_in")
    system.connect("CYC_2", "s2_level_out", "CYC_3", "s2_level_in")
    system.connect("CYC_3", "s3_level_out", "CYC_1", "s3_level_in")

    obs["cycle_error"] = None
    try:
        system.prerun()
    except Exception as err:  # noqa: BLE001 -- the refusal IS the observation
        obs["cycle_error"] = err

    obs["cycle_order"] = system.equation_order
    obs["cycle_registrations"] = list(system.equation_registrations)

    system.deleteSys()


def run_chain_scenario(obs):
    """A tank read by three controllers in a row, declared downstream first."""
    system = muscadet.System(name="ObjCtrlOrderingChain")

    system.add_component(name="SRC_X", cls="SourceContinuous", flow="q", rate=UNIT_RATE)
    system.add_component(
        name="CAP_X",
        cls="CapacityContinuous",
        flow="q",
        capacity=1000.0,
        capacity_name="tank",
        content_init={"q": 0.0},
        fill_rate=float("inf"),
    )
    system.connect_flow(source="SRC_X", target="CAP_X", flow_name="q")

    stages = (
        ("CTRL_A", "tank", "stage_a"),
        ("CTRL_B", "stage_a", "stage_b"),
        ("CTRL_C", "stage_b", "stage_c"),
    )

    # DOWNSTREAM FIRST: declaration order is the reverse of the signal order,
    # so nothing but an order derived from the wiring can get this right.
    for name, reads, publishes in reversed(stages):
        add_republishing_controller(system, name, reads, publishes)

    system.connect("CAP_X", "tank_level_out", "CTRL_A", "tank_level_in")
    system.connect("CTRL_A", "stage_a_level_out", "CTRL_B", "stage_a_level_in")
    system.connect("CTRL_B", "stage_b_level_out", "CTRL_C", "stage_b_level_in")

    obs["chain_calls"] = record_control_equations(system, stages)
    obs["chain_stages"] = stages

    for date in CHAIN_CLOCKS:
        add_clock(system.comp["CAP_X"], date)

    system.isimu_start()
    for _ in range(40):
        if system.currentTime() >= CHAIN_HORIZON:
            break
        system.isimu_step_forward()
    system.isimu_stop()

    obs["chain_order"] = system.equation_order
    obs["chain_registrations"] = list(system.equation_registrations)

    system.deleteSys()


def run_gate_scenario(obs):
    """A controller chain and a logic gate, side by side."""
    system = muscadet.System(name="ObjCtrlOrderingGate")

    system.add_component(name="SRC_G", cls="SourceContinuous", flow="q", rate=UNIT_RATE)
    system.add_component(
        name="CAP_G",
        cls="CapacityContinuous",
        flow="q",
        capacity=1000.0,
        capacity_name="tank_g",
        content_init={"q": 0.0},
        fill_rate=float("inf"),
    )
    system.connect_flow(source="SRC_G", target="CAP_G", flow_name="q")

    # Again downstream first, so the gate scenario proves the chain too.
    system.add_component(
        name="CTRL_G2",
        cls="ObjCtrl",
        controls_in=[{"name": "echo"}],
        controls_out=[
            {
                "name": "shut",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "echo",
                    "operator": ">=",
                    "threshold": GATE_SHUT_THRESHOLD,
                },
            }
        ],
    )
    system.add_component(
        name="CTRL_G1",
        cls="ObjCtrl",
        controls_in=[{"name": "tank_g"}],
        controls_out=[
            {
                "name": "alarm",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "tank_g",
                    "operator": ">=",
                    "threshold": GATE_ALARM_THRESHOLD,
                },
            },
            {
                "name": "echo",
                "kind": "value",
                "emit": {"op": "republish", "input": "tank_g"},
            },
        ],
    )
    system.connect("CAP_G", "tank_g_level_out", "CTRL_G1", "tank_g_level_in")
    system.connect("CTRL_G1", "echo_level_out", "CTRL_G2", "echo_level_in")

    system.add_component(name="SINK_G", cls="ObjCtrlOrdering001Sink")
    system.add_component(
        name="GATE_G",
        cls="ObjLogicGate",
        kind="and",
        cond=[
            [{"obj": "CTRL_G1", "attr": "alarm_signal_out", "value": True}],
            [{"obj": "CTRL_G2", "attr": "shut_signal_out", "value": True}],
        ],
        out_elements=["g"],
    )
    system.connect("GATE_G", "g_out", "SINK_G", "g_in")

    for date in GATE_CLOCKS:
        add_clock(system.comp["CAP_G"], date)

    system.isimu_start()
    for _ in range(40):
        if system.currentTime() >= GATE_HORIZON:
            break
        system.isimu_step_forward()

    obs["gate_alarm"] = system.comp["CTRL_G1"].controls_out["alarm"].get_signal()
    obs["gate_shut"] = system.comp["CTRL_G2"].controls_out["shut"].get_signal()
    obs["gate_result"] = system.comp["GATE_G"].result.value()
    obs["gate_fed"] = system.comp["SINK_G"].flows_in["g"].var_fed.value()
    system.isimu_stop()

    obs["gate_order"] = system.equation_order
    obs["gate_registrations"] = list(system.equation_registrations)

    # The pre-run step is one-shot, so a republication declared now would never
    # get an equation. Recorded here rather than asserted on the live system, so
    # every test of this module stays a reader of a snapshot.
    obs["late_error"] = None
    try:
        system.comp["CTRL_G2"].add_control_out(
            name="late",
            kind="value",
            emit={"op": "republish", "input": "echo"},
        )
    except Exception as err:  # noqa: BLE001 -- the refusal IS the observation
        obs["late_error"] = err

    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_cycle_scenario(obs)
    run_chain_scenario(obs)
    run_gate_scenario(obs)

    return obs


def control_registrations(registrations):
    """The controller equations of one system, in registration order."""
    return [reg for reg in registrations if reg.method == "compute_controls"]


# ----------------------------------------------------------------------
# 1. A cycle between controllers is refused, and names its components
# ----------------------------------------------------------------------


def test_a_controller_cycle_is_refused_before_the_first_run(the_run):
    """No evaluation order exists, so the model must not build."""
    error = the_run["cycle_error"]

    assert error is not None, "a cyclic controller chain must not build"
    assert isinstance(error, ValueError)


def test_the_controller_cycle_error_names_the_three_components(the_run):
    """A modeller has to be told WHICH controllers close the loop."""
    message = str(the_run["cycle_error"])

    for name in ("CYC_1", "CYC_2", "CYC_3"):
        assert name in message, f"{name} is not named in: {message}"


def test_the_controller_cycle_error_names_the_closing_links(the_run):
    """Naming the components alone leaves the wiring to be hunted for."""
    message = str(the_run["cycle_error"])

    for link in (
        "CYC_1.s1_level_out -> CYC_2.s1_level_in",
        "CYC_2.s2_level_out -> CYC_3.s2_level_in",
        "CYC_3.s3_level_out -> CYC_1.s3_level_in",
    ):
        assert link in message, f"{link} is not named in: {message}"


def test_no_equation_is_registered_when_the_controllers_cycle(the_run):
    """The refusal happens while the order is derived, before any registration."""
    assert the_run["cycle_order"] is None
    assert the_run["cycle_registrations"] == []


# ----------------------------------------------------------------------
# 2. A chain of three evaluates in ONE pass
# ----------------------------------------------------------------------


def test_the_chain_is_ordered_from_the_wiring_and_not_from_the_declarations(the_run):
    """The controllers were declared downstream first; the order is the wiring's."""
    order = the_run["chain_order"]

    assert order.controller_order == ["CTRL_A", "CTRL_B", "CTRL_C"]

    registered = control_registrations(the_run["chain_registrations"])
    assert [reg.comp for reg in registered] == ["CTRL_A", "CTRL_B", "CTRL_C"]
    assert [reg.order for reg in registered] == sorted(reg.order for reg in registered)


def test_the_chain_settles_in_one_evaluation_of_the_equation_set(the_run):
    """The whole point: three controllers, one pass, no lag.

    Evaluated backwards, the last controller of the chain republishes what the
    middle one published in the PREVIOUS pass, which republished what the first
    one published in the pass before that. The three publications of one pass
    then differ by one and two evaluations of lag, and the chain needs three
    passes to carry one number from the tank to its end.
    """
    calls = the_run["chain_calls"]
    width = len(the_run["chain_stages"])

    assert calls, "the chain session must have evaluated something"
    assert len(calls) % width == 0, (
        f"{len(calls)} calls do not divide into passes of {width}: "
        "the solver did not evaluate the whole set each time"
    )

    lagging = [
        (index, entry)
        for index, entry in enumerate(passes_of(calls, width))
        if len({value for _comp, value in entry}) != 1
    ]

    assert not lagging, (
        f"{len(lagging)} of {len(calls) // width} passes carry a lagged "
        f"publication; first one: {lagging[0]}"
    )


def test_every_pass_evaluates_the_chain_from_upstream_to_downstream(the_run):
    """The mechanism behind the assertion above: increasing integers."""
    calls = the_run["chain_calls"]
    width = len(the_run["chain_stages"])
    expected = ["CTRL_A", "CTRL_B", "CTRL_C"]

    walked = {
        tuple(comp for comp, _value in entry) for entry in passes_of(calls, width)
    }

    assert walked == {tuple(expected)}


def test_the_controller_band_sits_above_the_measurement_one(the_run):
    """A controller READS a measurement, so it is refreshed after every one."""
    assert ordering.CONTROL_ORDER_BASE > ordering.MEASUREMENT_ORDER_BASE

    registered = control_registrations(the_run["chain_registrations"])
    others = [
        reg.order
        for reg in the_run["chain_registrations"]
        if reg.method != "compute_controls"
    ]

    assert registered
    assert min(reg.order for reg in registered) >= ordering.CONTROL_ORDER_BASE
    assert max(others) < ordering.CONTROL_ORDER_BASE


def test_every_equation_of_the_chain_keeps_a_distinct_integer(the_run):
    """KTD3: a tie makes the sequence a function of names, not of the graph."""
    orders = [reg.order for reg in the_run["chain_registrations"]]

    assert len(orders) == len(set(orders))


# ----------------------------------------------------------------------
# 3. A controller and a logic gate do not disturb each other
# ----------------------------------------------------------------------


def test_a_logic_gate_takes_no_integer_from_any_band(the_run):
    """A gate is a sensitive method, not an equation: it orders nothing here."""
    registrations = the_run["gate_registrations"]

    assert registrations, "the gate montage must have registered equations"
    assert not [reg for reg in registrations if reg.comp == "GATE_G"]

    orders = [reg.order for reg in registrations]
    assert len(orders) == len(set(orders))


def test_the_controllers_of_the_gate_montage_keep_their_derived_order(the_run):
    """The gate stands beside the chain and does not perturb it."""
    order = the_run["gate_order"]

    assert order.controller_order == ["CTRL_G1", "CTRL_G2"]

    registered = control_registrations(the_run["gate_registrations"])
    # CTRL_G2 carries no republication, so it registers no equation at all.
    assert [reg.comp for reg in registered] == ["CTRL_G1"]
    assert registered[0].order >= ordering.CONTROL_ORDER_BASE


def test_the_gate_reads_the_signals_the_controllers_wrote(the_run):
    """Both orderings coexist: the montage runs and the gate answers."""
    assert the_run["gate_alarm"] is True
    assert the_run["gate_shut"] is True
    assert the_run["gate_result"] is True
    assert the_run["gate_fed"] is True


def test_a_republication_declared_after_the_pre_run_step_is_refused(the_run):
    """The order is derived once, so a late republication would never refresh.

    A controller is not a node of the continuous-flow graph, so
    ``System.check_model_unchanged_since_prerun`` cannot see one arriving late.
    Without this refusal the output would publish its declared default for the
    whole run with nothing raised to say so, which is exactly the silent
    failure the whole controller unit exists to remove.
    """
    error = the_run["late_error"]

    assert error is not None, "a late republication must be refused"
    assert isinstance(error, ValueError)

    message = str(error)
    assert "'late'" in message
    assert "pre-run" in message


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
