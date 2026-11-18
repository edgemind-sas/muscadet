"""A controller reads a SERVICE: a boolean input wired from a service output.

What this unit pins down
------------------------
Until now a controller observed numbers only: a level, a rate, a share. A
service a component renders -- a dormant output a mode wakes up, the boolean
output of another controller -- had no way in, a controller carrying no
interface at all. The service input is a fourth nature of controller input,
boolean, and three properties make it usable:

* it imports on ``{name}_in`` under the alias ``{name}``, the very box a discrete
  input flow imports on, so it reads a discrete output's ``{flow}_out`` and a
  controller's boolean ``{output}_out`` with no adapter;
* it holds a real engine VARIABLE, ``{name}_fed_in``, kept by a sensitive method
  and by the initial fixpoint. A mode condition reads a variable, never a
  reference, so this variable is what an AMDEC condition names;
* it is an observation: it adds no edge to the signal graph, takes at most one
  source, is false while unwired, and no output grammar may read it as a
  number.

The montage
-----------
``OT_SWITCH`` carries a dormant service output ``ot_access``. A mode,
``elevation``, wakes it at ``ELEVATION_DATE`` and hands it back
``ELEVATION_HOLD`` later. ``SENSOR`` reads it through its service input, and a
mode on the sensor, ``falsification``, is conditioned on that input: it can
only occur while the switch serves it. ``RELAY`` is a controller whose boolean
output ``link`` feeds ``SENSOR``'s second service input, and ``ALONE`` declares
a service input wired to nothing.
"""

import json
import pathlib
import subprocess
import sys
import textwrap

import cod3s
import pytest

import muscadet

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

PREFIX = "CtrlServiceIn"

SWITCH_CLASS = f"{PREFIX}Switch"
SENSOR_CLASS = f"{PREFIX}Sensor"
RELAY_CLASS = f"{PREFIX}Relay"
ALONE_CLASS = f"{PREFIX}Alone"

#: When the elevation wakes the dormant service output, and how long it holds.
ELEVATION_DATE = 2.0
ELEVATION_HOLD = 3.0
RELEASE_DATE = ELEVATION_DATE + ELEVATION_HOLD

#: How long the falsification waits once its condition holds.
FALSIFICATION_DELAY = 0.5
FALSIFICATION_DATE = ELEVATION_DATE + FALSIFICATION_DELAY

#: How far either side of an event date the session is read.
BRACKET = 0.1


def _connection(source_id, source_iface, target_id, target_iface):
    return {
        "component_source": source_id,
        "interface_source": source_iface,
        "component_target": target_id,
        "interface_target": target_iface,
    }


def build_payload():
    """The montage, as the COD3S Platform would export it."""
    templates = {
        SWITCH_CLASS: {
            "name": SWITCH_CLASS,
            "interfaces": {
                "ot_access__output": {
                    "name": "ot_access",
                    "port_type": {"general": "output"},
                }
            },
        },
        SENSOR_CLASS: {
            "name": SENSOR_CLASS,
            "metadata": {"controller": True},
            "controls_in": [
                {"name": "ot_access", "kind": "service"},
                {"name": "link", "kind": "service"},
            ],
        },
        RELAY_CLASS: {
            "name": RELAY_CLASS,
            "metadata": {"controller": True},
            "controls_out": [{"name": "link", "kind": "bool", "default": True}],
        },
        ALONE_CLASS: {
            "name": ALONE_CLASS,
            "metadata": {"controller": True},
            "controls_in": [{"name": "spare", "kind": "service"}],
        },
    }
    components = {
        "id-sw": {
            "name": "OT_SWITCH",
            "class_name": SWITCH_CLASS,
            # Produced, but dormant: its availability gate starts closed, and
            # the output is not served until an effect opens it.
            "attributes": [
                {"name": "ot_access", "role": "prod_init", "value": True},
                {"name": "ot_access", "role": "fed_available_init", "value": False},
            ],
        },
        "id-sen": {"name": "SENSOR", "class_name": SENSOR_CLASS, "attributes": []},
        "id-rel": {"name": "RELAY", "class_name": RELAY_CLASS, "attributes": []},
        "id-alo": {"name": "ALONE", "class_name": ALONE_CLASS, "attributes": []},
    }
    connections = {
        "c-ot": _connection("id-sw", "ot_access", "id-sen", "ot_access"),
        "c-link": _connection("id-rel", "link", "id-sen", "link"),
    }
    return {
        "model": {
            "name": "CtrlServiceInModel",
            "kb": {"name": "CtrlServiceInKB", "version": "0.0.1"},
            "elements": {"components": components, "connections": connections},
        },
        "kb": {
            "name": "CtrlServiceInKB",
            "version": "0.0.1",
            "component_templates": templates,
            "interface_templates": {},
        },
    }


def add_modes(system):
    """The two modes of the montage, spelled the way the platform emits them."""
    system.add_component(
        cls="ObjMode2S",
        mode_name="elevation",
        targets=["OT_SWITCH"],
        occ_law={"cls": "delay", "time": ELEVATION_DATE},
        not_occ_law={"cls": "delay", "time": ELEVATION_HOLD},
        occ_effects={"ot_access_fed_available_out": True},
    )
    system.add_component(
        cls="ObjMode2S",
        mode_name="falsification",
        targets=["SENSOR"],
        occ_law={"cls": "delay", "time": FALSIFICATION_DELAY},
        occ_cond=[
            [{"attr": "ot_access_fed_in", "obj": "SENSOR", "ope": "==", "value": True}]
        ],
        # Repaired long after the session ends: the test reads an occurrence.
        not_occ_law={"cls": "delay", "time": 1000.0},
    )


def build_system():
    from muscadet.importers.cod3s_platform import system_from_export

    system = system_from_export(build_payload())
    add_modes(system)
    return system


def falsified(system):
    """True once the falsification mode has occurred."""
    mode = system.comp["SENSOR__falsification"]
    (automaton,) = mode.automata_d.values()
    return bool(automaton.get_state_by_name("occ")._bkd.isActive())


@pytest.fixture(scope="module")
def obs():
    system = build_system()
    try:
        yield {"system": system}
    finally:
        try:
            system.deleteSys()
        except Exception:
            pass
        cod3s.terminate_session()


# ---------------------------------------------------------------------------
# Declaration
# ---------------------------------------------------------------------------


def test_the_service_input_is_a_boolean_variable_kept_apart_from_measurements(obs):
    sensor = obs["system"].comp["SENSOR"]

    assert list(sensor.controls_service_in) == ["ot_access", "link"]
    # Not a measurement channel: what the measurement machinery walks never
    # sees it.
    assert "ot_access" not in sensor.controls_in
    assert "ot_access" not in sensor.measurements_in

    assert sensor.variable("ot_access_fed_in").value() is False


def test_it_imports_on_the_box_a_discrete_input_flow_imports_on(obs):
    sensor = obs["system"].comp["SENSOR"]
    assert sensor.interface_boxes["ot_access_in"] == "ot_access"
    assert sensor.interface_boxes["link_in"] == "link"


def test_a_service_input_takes_no_aggregation_and_no_flow(obs):
    controller = obs["system"].comp["ALONE"]

    with pytest.raises(ValueError) as error:
        controller.add_control_in(name="other", kind="service", aggregate="sum")
    assert "'aggregate'" in str(error.value)

    with pytest.raises(ValueError) as error:
        controller.add_control_in(name="other", kind="service", flows=["q"])
    assert "'flows'" in str(error.value)

    assert "other" not in controller.controls_service_in


def test_its_name_is_unique_across_the_controller(obs):
    controller = obs["system"].comp["ALONE"]

    with pytest.raises(ValueError) as error:
        controller.add_control_out(name="spare", kind="bool")
    assert "spare" in str(error.value)


def test_a_service_input_cannot_claim_a_box_another_input_imports_on(obs):
    """``x_level`` imports on ``x_level_in``, the box of a level input ``x``."""
    controller = obs["system"].comp["ALONE"]
    controller.add_control_in(name="reading")

    with pytest.raises(ValueError) as error:
        controller.add_control_in(name="reading_level", kind="service")

    assert "reading_level_in" in str(error.value)
    assert "reading_level" not in controller.controls_service_in


@pytest.mark.parametrize(
    "emit",
    [
        {"op": "compare", "input": "spare", "operator": ">=", "threshold": 1.0},
        {
            "op": "band",
            "input": "spare",
            "direction": "above",
            "activate": 1.0,
            "release": 0.5,
        },
    ],
)
def test_no_output_grammar_reads_a_service_input_as_a_number(obs, emit):
    controller = obs["system"].comp["ALONE"]

    with pytest.raises(ValueError) as error:
        controller.add_control_out(name="alarm", kind="bool", emit=emit)

    assert "service input" in str(error.value)
    assert "'spare'" in str(error.value)
    assert "alarm" not in controller.controls_out


def test_a_republication_cannot_read_a_service_input(obs):
    controller = obs["system"].comp["ALONE"]

    with pytest.raises(ValueError) as error:
        controller.add_control_out(
            name="echo",
            kind="value",
            emit={"op": "republish", "input": "spare", "gain": 2.0},
        )

    assert "service input" in str(error.value)


def test_the_service_edge_is_no_link_of_the_signal_graph(obs):
    """An observation adds no edge to the order of the controllers."""
    from muscadet.ordering import controller_signal_links

    _, links = controller_signal_links(obs["system"])

    assert all(link.observer != "SENSOR" for link in links)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def test_the_input_follows_the_dormant_service_output(obs):
    """False while dormant, true once a mode wakes the output, false again."""
    system = obs["system"]
    fed = system.comp["SENSOR"].variable("ot_access_fed_in")

    system.isimu_start()
    try:
        assert fed.value() is False

        system.isimu_step_to(ELEVATION_DATE - BRACKET)
        assert fed.value() is False

        system.isimu_step_to(ELEVATION_DATE + BRACKET)
        assert fed.value() is True

        system.isimu_step_to(RELEASE_DATE + BRACKET)
        assert fed.value() is False
    finally:
        system.isimu_stop()


def test_a_mode_conditioned_on_the_input_occurs_only_while_it_is_fed(obs):
    system = obs["system"]

    system.isimu_start()
    try:
        system.isimu_step_to(FALSIFICATION_DATE - BRACKET)
        assert falsified(system) is False

        system.isimu_step_to(FALSIFICATION_DATE + BRACKET)
        assert falsified(system) is True
    finally:
        system.isimu_stop()


def test_the_input_follows_a_controller_boolean_output(obs):
    system = obs["system"]
    fed = system.comp["SENSOR"].variable("link_fed_in")
    signal = system.comp["RELAY"].controls_out["link"]

    system.isimu_start()
    try:
        # The relay's output carries its declared default from instant 0.
        assert signal.get_signal() is True
        assert fed.value() is True

        # A hand-written signal reaches the input at the next step the session
        # takes, as any variable written between two steps does.
        signal.publish(False)
        system.isimu_step_forward()
        assert fed.value() is False

        signal.publish(True)
        system.isimu_step_forward()
        assert fed.value() is True
    finally:
        system.isimu_stop()


def test_an_unwired_service_input_is_false(obs):
    system = obs["system"]

    system.isimu_start()
    try:
        assert system.comp["ALONE"].variable("spare_fed_in").value() is False
    finally:
        system.isimu_stop()


def test_delete(obs):
    assert obs["system"] is not None


# ---------------------------------------------------------------------------
# The declaration, and the import refusals that need a system of their own
# ---------------------------------------------------------------------------

_ROUND_TRIP = textwrap.dedent("""
    import json, sys

    sys.path.insert(0, "tests")

    import cod3s
    import muscadet
    from muscadet.declare import build_system, system_spec
    from test_ctrl_service_in_001 import build_system as build_original

    original = build_original()
    declared = json.loads(json.dumps(system_spec(original)))
    original.deleteSys()
    cod3s.terminate_session()

    rebuilt_system = build_system(
        declared, system=muscadet.System(name="CtrlServiceInRebuilt")
    )
    rebuilt = json.loads(json.dumps(system_spec(rebuilt_system)))


    def comparable(document):
        document = dict(document)
        document.pop("name", None)
        document["components"] = {
            name: {k: v for k, v in entry.items() if k != "source_cls"}
            for name, entry in document["components"].items()
        }
        return document


    print("RESULT " + json.dumps({
        "identical": comparable(declared) == comparable(rebuilt),
        "sensor": declared["components"]["SENSOR"],
        "connections": declared["connections"],
        "rebuilt_inputs": list(rebuilt_system.comp["SENSOR"].controls_service_in),
    }))
    """)

_COMPARE_ON_A_SERVICE_INPUT = textwrap.dedent("""
    import json, sys

    sys.path.insert(0, "tests")

    from muscadet.importers.cod3s_platform import (
        Cod3sPlatformImportError,
        system_from_export,
    )
    from test_ctrl_service_in_001 import SENSOR_CLASS, build_payload

    payload = build_payload()
    payload["kb"]["component_templates"][SENSOR_CLASS]["controls_out"] = [
        {
            "name": "alarm",
            "kind": "bool",
            "emit": {
                "op": "compare",
                "input": "ot_access",
                "operator": ">=",
                "threshold": 1.0,
            },
        }
    ]
    try:
        system_from_export(payload)
    except Cod3sPlatformImportError as error:
        print("RESULT " + json.dumps({"refused": str(error)}))
    else:
        print("RESULT " + json.dumps({"refused": None}))
    """)


def run_probe(script):
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    for line in completed.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT ") :])

    raise AssertionError(
        f"probe printed no result:\n{completed.stdout}\n{completed.stderr}"
    )


def test_the_declaration_round_trips_with_its_service_inputs():
    result = run_probe(_ROUND_TRIP)

    assert result["identical"], result
    assert result["sensor"]["controls_in"] == [
        {"name": "ot_access", "kind": "service"},
        {"name": "link", "kind": "service"},
    ]
    assert result["rebuilt_inputs"] == ["ot_access", "link"]

    by_pair = {
        (entry["source"], entry["target"]): entry for entry in result["connections"]
    }
    service = by_pair[("OT_SWITCH", "SENSOR")]
    assert service["source_box"] == "ot_access_out"
    assert service["target_box"] == "ot_access_in"
    assert "flow" not in service


def test_a_compare_reading_a_service_input_is_refused_at_import():
    result = run_probe(_COMPARE_ON_A_SERVICE_INPUT)

    assert result["refused"] is not None
    assert "SENSOR" in result["refused"]
    assert "service input" in result["refused"]
