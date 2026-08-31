"""ObjCtrl -- the controller skeleton: it declares, it builds, it connects (R39).

A controller is a PEER of ``ObjFlow``, not a subclass of it: it carries a
reading or a signal, never a conserved quantity. This module pins down the
skeleton and nothing else -- the aggregation of several sources, the output
grammar and the ordering of controllers come later. What is asserted here is
the WIRING:

* a controller declares observation inputs and signal outputs, and the system
  builds;
* a boolean output drives the control port of a shipped source, which starts
  and stops accordingly;
* the value output of one controller feeds the observation input of another;
* a declaration key no controller reads is refused BY NAME, at the component
  and at the interface.

The whole system is built, wired and driven once in the fixture, and every
test reads back a snapshot: PyCATSHOO forbids more than one live system per
process, and the interactive session must advance monotonically for the trace
to mean anything.
"""

import cod3s
import muscadet
import pytest

# Imported for their side effect: a component class resolves by name, so
# declaring cls="SourceContinuous" needs the class to have been imported.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

# -- The controlled loop: a tank drained at 1, refilled at 2 when told to
LOOP_RATE = 2.0
LOOP_DEMAND = 1.0
LOOP_INIT = 10.0
LOOP_VOLUME = 100.0

#: The dates the interactive session is given something to stop at. The
#: controller carries no threshold in this unit, so nothing would otherwise
#: make the solver stop, and a session holding one transition would jump the
#: horizon in a single step and observe nothing.
LOOP_STOPS = (2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0)

#: The four dates the trace is read at: idle, just after the signal comes on,
#: while it is on, and after it goes off again. Read through :func:`step_to`
#: rather than by counting steps: the session stops twice at some dates, an
#: instantaneous step following a dated one, and a count would drift.
LOOP_READS = (4.0, 8.0, 12.0, 18.0)

#: What the first controller publishes on its value output, and what the
#: second one must read back over the observation link.
CHAIN_READING = 7.5


def add_clock(comp, date):
    """Give the interactive session a date it can always step to."""
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def step_to(system, date, limit=40):
    """Step the interactive session until it reaches ``date``."""
    for _ in range(limit):
        if system.currentTime() >= date:
            return
        system.isimu_step_forward()

    raise AssertionError(f"the session did not reach {date} in {limit} steps")


def snapshot(system):
    """What the controlled loop reads right now."""
    return {
        "time": system.currentTime(),
        "level": system.comp["CAP"].capacities["tank"].get_quantity("q"),
        "observed": system.comp["CTRL"].controls_in["tank"].get_reading(),
        "supplied": system.comp["SRC"].flows_out["q"].var_fed.value(),
        "signal": system.comp["CTRL"].controls_out["fill"].get_signal(),
        "control": system.comp["SRC"].flows_in["fill"].var_fed.value(),
    }


@pytest.fixture(scope="module")
def obs():
    """Build the two montages, drive one session, record what it produced."""
    observations = {}

    system = muscadet.System(name="ObjCtrl001")

    # -- Montage 1: the controlled loop. A source gated on a discrete control
    # port, the tank it fills, the consumer draining it, and the controller
    # observing the level and driving the port.
    system.add_component(
        name="SRC", cls="SourceContinuous", flow="q", rate=LOOP_RATE, control="fill"
    )
    system.add_component(
        name="CAP",
        cls="CapacityContinuous",
        flow="q",
        capacity=LOOP_VOLUME,
        capacity_name="tank",
        content_init={"q": LOOP_INIT},
        # The volume claims whatever its producer delivers while it has room,
        # so the level RISES once the source is told to run instead of merely
        # holding at what the consumer draws (R36).
        fill_rate=float("inf"),
    )
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="q", demand=LOOP_DEMAND
    )
    system.add_component(
        name="CTRL",
        cls="ObjCtrl",
        controls_in=[{"name": "tank"}],
        controls_out=[{"name": "fill", "kind": "bool"}],
    )

    system.connect_flow(source="SRC", target="CAP", flow_name="q")
    system.connect_flow(source="CAP", target="SINK", flow_name="q")
    # Both controller edges go through the RAW connection: neither end of an
    # observation link nor of a control port is a flow of the controller.
    system.connect("CAP", "tank_level_out", "CTRL", "tank_level_in")
    system.connect("CTRL", "fill_out", "SRC", "fill_in")

    # -- Montage 2: one controller's value output feeding another's input.
    system.add_component(
        name="CTRLA",
        cls="ObjCtrl",
        controls_out=[{"name": "reading", "kind": "value"}],
    )
    system.add_component(name="CTRLB", cls="ObjCtrl", controls_in=[{"name": "reading"}])
    system.connect("CTRLA", "reading_level_out", "CTRLB", "reading_level_in")

    # -- What a controller input asking for a constituent nobody publishes is
    # told. The refusal is the observing side of the measurement diagnostic,
    # reached from a controller for the first time.
    system.add_component(
        name="CTRLC",
        cls="ObjCtrl",
        controls_in=[{"name": "reading", "flows": ["h2"]}],
    )
    try:
        system.connect("CTRLA", "reading_level_out", "CTRLC", "reading_level_in")
        observations["err_constituent"] = None
    except Exception as err:  # noqa: BLE001 -- the refusal is the observation
        observations["err_constituent"] = err

    # What the flow resolution answers on a controller box, which is what
    # keeps the family check from judging an information edge.
    observations["behind"] = {
        "signal_out": system.flow_behind_message_box("CTRL", "fill_out", "out"),
        "level_in": system.flow_behind_message_box("CTRL", "tank_level_in", "in"),
        "value_out": system.flow_behind_message_box(
            "CTRLA", "reading_level_out", "out"
        ),
        "control_in": system.flow_behind_message_box("SRC", "fill_in", "in"),
    }

    for date in LOOP_STOPS:
        add_clock(system.comp["SINK"], date)

    signal = system.comp["CTRL"].controls_out["fill"]
    trace = []

    system.isimu_start()

    # Signal off: the source is idle and the tank drains.
    step_to(system, LOOP_READS[0])
    trace.append(snapshot(system))

    # Signal on: the source starts. The control port picks the signal up at
    # the next stop -- a sensitive method fires on the engine's boundary, not
    # on the write -- so the reading is taken one stop further on.
    signal.publish(True)
    step_to(system, LOOP_READS[1])
    trace.append(snapshot(system))
    step_to(system, LOOP_READS[2])
    trace.append(snapshot(system))

    # Signal off again: the source stops.
    signal.publish(False)
    step_to(system, LOOP_READS[3])
    trace.append(snapshot(system))

    # The chain: what one controller publishes, the next one reads.
    system.comp["CTRLA"].controls_out["reading"].publish(CHAIN_READING)
    observations["chained"] = system.comp["CTRLB"].controls_in["reading"].get_reading()
    observations["unwired"] = system.comp["CTRLC"].controls_in["reading"].get_reading()

    system.isimu_stop()

    observations["trace"] = trace
    observations["system"] = system

    return observations


# A controller declares, and the system builds
# ============================================


def test_a_controller_declares_its_inputs_and_its_outputs(obs):
    controller = obs["system"].comp["CTRL"]

    assert isinstance(controller, muscadet.ObjCtrl)
    assert not isinstance(controller, muscadet.ObjFlow)
    assert list(controller.controls_in) == ["tank"]
    assert list(controller.controls_out) == ["fill"]


def test_a_controller_carries_no_flow_at_all(obs):
    """The peer contract: it transports information, never a quantity."""
    controller = obs["system"].comp["CTRL"]

    assert not hasattr(controller, "flows_in")
    assert not hasattr(controller, "flows_out")


def test_its_observation_input_reads_the_level_it_is_wired_to(obs):
    """The input side is the measurement link, not a sensitive method."""
    for entry in obs["trace"]:
        assert entry["observed"] == pytest.approx(entry["level"])


def test_a_controller_box_stands_for_no_flow(obs):
    """What routes a controller's connections to the raw connection.

    ``check_flow_families`` judges nothing when either side resolves to no
    flow, which is what lets an information edge cross the discrete/continuous
    boundary the family check would otherwise refuse.
    """
    behind = obs["behind"]

    assert behind["signal_out"] is None
    assert behind["level_in"] is None
    assert behind["value_out"] is None
    # The other end of the very same edge IS a flow: the resolution is
    # conservative, not blind.
    assert behind["control_in"] is not None


# A boolean output drives a control port
# ======================================


def test_the_source_is_idle_while_the_signal_is_off(obs):
    idle, _, _, stopped = obs["trace"]

    for entry in (idle, stopped):
        assert entry["signal"] is False
        assert entry["control"] is False
        assert entry["supplied"] == pytest.approx(0.0)


def test_the_source_starts_when_the_signal_comes_on(obs):
    _, started, running, _ = obs["trace"]

    for entry in (started, running):
        assert entry["signal"] is True
        assert entry["control"] is True
        assert entry["supplied"] == pytest.approx(LOOP_RATE)


def test_the_level_falls_while_idle_and_rises_once_supplied(obs):
    """The physical consequence, which is the point of the control port."""
    idle, started, running, stopped = obs["trace"]

    assert idle["level"] < LOOP_INIT  # drained while nothing supplied it
    assert running["level"] > started["level"]  # filled while the source ran
    assert stopped["level"] < running["level"]  # drained again once it stopped


# One controller feeds another
# ============================


def test_a_value_output_feeds_another_controller_input(obs):
    assert obs["chained"] == pytest.approx(CHAIN_READING)


def test_an_unwired_controller_input_reads_its_default(obs):
    assert obs["unwired"] == pytest.approx(0.0)


def test_an_input_asking_for_a_constituent_nobody_publishes_is_refused(obs):
    error = obs["err_constituent"]

    assert error is not None
    assert "h2" in str(error)


# What a controller refuses, by name
# ==================================


def test_an_unknown_declaration_key_is_refused_by_name(obs):
    with pytest.raises(ValueError) as error:
        obs["system"].add_component(
            name="BADKEY", cls="ObjCtrl", controls_ni=[{"name": "tank"}]
        )

    message = str(error.value)
    assert "'controls_ni'" in message
    assert "controls_in" in message
    # Refused before anything was created: a malformed declaration costs no
    # engine object and leaves no half-built component behind.
    assert "BADKEY" not in obs["system"].comp


def test_an_unknown_input_key_is_refused_by_name(obs):
    controller = obs["system"].comp["CTRL"]

    with pytest.raises(ValueError) as error:
        controller.add_control_in(name="spare", levl_default=1.0)

    assert "'levl_default'" in str(error.value)
    assert "spare" not in controller.controls_in


def test_an_unknown_output_key_is_refused_by_name(obs):
    controller = obs["system"].comp["CTRL"]

    with pytest.raises(ValueError) as error:
        controller.add_control_out(name="spare", defualt=True)

    assert "'defualt'" in str(error.value)
    assert "spare" not in controller.controls_out


def test_an_unknown_output_nature_is_refused_by_name(obs):
    controller = obs["system"].comp["CTRL"]

    with pytest.raises(ValueError) as error:
        controller.add_control_out(name="spare", kind="boolean")

    assert "boolean" in str(error.value)


def test_two_interfaces_cannot_claim_the_same_name(obs):
    controller = obs["system"].comp["CTRL"]

    with pytest.raises(ValueError) as error:
        controller.add_control_out(name="tank", kind="bool")

    assert "tank" in str(error.value)


def test_two_interfaces_cannot_claim_the_same_message_box(obs):
    """KD20: a boolean output named ``x_level`` claims a value output's box."""
    controller = obs["system"].comp["CTRLA"]

    with pytest.raises(ValueError) as error:
        controller.add_control_out(name="reading_level", kind="bool")

    message = str(error.value)
    assert "reading_level_out" in message
    assert "reading" in message


def test_a_controller_section_on_a_flow_component_is_refused_by_name(obs):
    """``build_component`` owns the ``ObjFlow`` lifecycle, not a controller's.

    The two controller sections sit in ``DECLARATION_SECTIONS`` for the order
    they record, so a spec carrying one on a component that cannot build it has
    to be told which section and which builder are missing -- not fail on a
    missing attribute naming neither.
    """
    with pytest.raises(muscadet.ComponentSpecError) as error:
        muscadet.build_component(
            obs["system"],
            {"name": "SPECFLOW", "controls_in": [{"name": "tank"}]},
        )

    message = str(error.value)
    assert "'controls_in'" in message
    assert "add_control_in" in message


def test_delete(obs):
    obs["system"].deleteSys()
    cod3s.terminate_session()
