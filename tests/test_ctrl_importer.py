"""The COD3S Platform bridge reads a CONTROLLER declaration and materialises it.

What this unit pins down
------------------------
A platform export carries components, and until now every one of them
transported a conserved quantity: an ``ObjFlow``, or the one exception the
bridge already made, a combinational ``ObjLogicGate``. A controller is the
second exception and a peer of neither -- it observes readings and publishes
signals (R39, R46) -- so the bridge reads it through a discriminant of its own,
``metadata.controller``, and materialises it OUTSIDE the regular component path,
exactly as it does for a gate.

The four things that had to be proved
-------------------------------------
* the declaration is read by the **pure parse layer**, which imports neither
  muscadet nor PyCATSHOO. Proved rather than asserted: the parse runs in a
  subprocess where those three roots are BLOCKED at the meta path and the module
  is loaded straight off its file, so nothing can smuggle the runtime in through
  the package ``__init__``;
* the built system carries the controller, its aggregated observation input and
  its two natures of output, and the whole chain responds end to end at the date
  the montage puts the crossing at;
* an **observation edge** is wired even though NEITHER of its two endpoint names
  is a declared flow -- on the publishing end a capacity or a rate export box,
  on the observing end an observation input -- while an ordinary flow edge
  naming something that is not a flow of one of its ends stays refused;
* a KB carrying no controller builds exactly the system it built before.

The montage
-----------
Two tanks publish their level onto ONE observation input, which reduces them by
their median. ``TANK_A`` starts at 2 and is filled at 1 per unit time, ``TANK_B``
holds 8 and nothing moves it, so the median reads ``5 + t / 2`` and crosses the
threshold of 6 at ``t = 2``. That crossing is a watched transition, which is
what makes the session advance to it and what re-evaluates the boolean output;
the signal then reaches a discrete input flow of a valve that knows nothing
about controllers. Beside it, a source delivers a constant rate to a sink, and
the controller reads that rate through a second input and republishes it, times
a gain, onto a second controller.

Requires PyCATSHOO native libraries for everything but the pure-layer class
(skipped otherwise).
"""

import json
import pathlib
import subprocess
import sys
import textwrap

import pytest

# The module under test is loaded BY PATH in the subprocess of the purity
# proof, so the path is computed without importing anything from muscadet.
IMPORTER_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "muscadet"
    / "importers"
    / "cod3s_platform.py"
)

#: Every KB class of this montage is prefixed with the module's own name, so a
#: class of one test file can never be read as a class of another.
PREFIX = "CtrlImporter"

TANK_A_CLASS = f"{PREFIX}TankA"
TANK_B_CLASS = f"{PREFIX}TankB"
FILLER_CLASS = f"{PREFIX}Filler"
SOURCE_CLASS = f"{PREFIX}Source"
SINK_CLASS = f"{PREFIX}Sink"
VALVE_CLASS = f"{PREFIX}Valve"
CONTROLLER_CLASS = f"{PREFIX}Controller"
OBSERVER_CLASS = f"{PREFIX}Observer"

#: What the two tanks start with. Their median is their mean, there being two.
TANK_A_CONTENT = 2.0
TANK_B_CONTENT = 8.0

#: What ``TANK_A`` is filled at, so the median rises at half that.
FILL_RATE = 1.0

#: Where the boolean output turns on, and the date the median reaches it.
FILL_THRESHOLD = 6.0
CROSSING_DATE = 2.0

#: The rate the source delivers, observed through the second input (R38).
NOMINAL_RATE = 3.0

#: The gain the value output republishes that rate with.
ECHO_GAIN = 2.0

#: How far either side of the crossing the trajectory is read. The response is
#: pinned to that bracket, which needs nothing of how the solver lands on a
#: crossing it root-finds.
BRACKET = 0.1

#: Tolerance on a reading compared to the number the montage puts there.
CROSSING_TOL = 0.01


# ---------------------------------------------------------------------------
# Payload builders (canonical {model, kb} shape)
# ---------------------------------------------------------------------------


def _tank_template(class_name: str, content: float) -> dict:
    """A volume, observable as a LEVEL under the capacity name ``level``."""
    return {
        "name": class_name,
        "interfaces": {
            "q__input": {
                "name": "q",
                "port_type": {"general": "input"},
                "flow_family": "continuous",
            }
        },
        "capacities": [
            {
                "name": "level",
                "flow": "q",
                "volume": 1000.0,
                "content_init": {"q": content},
                "fill_rate": "inf",
            }
        ],
    }


def _continuous_source_template(class_name: str, rate: float) -> dict:
    """A continuous output, observable as a RATE under the flow name ``q``."""
    return {
        "name": class_name,
        "interfaces": {
            "q__output": {
                "name": "q",
                "port_type": {"general": "output"},
                "flow_family": "continuous",
                "production_profile": {"cls": "constant", "value": rate},
            }
        },
    }


def _sink_template() -> dict:
    return {
        "name": SINK_CLASS,
        "interfaces": {
            "q__input": {
                "name": "q",
                "port_type": {"general": "input"},
                "flow_family": "continuous",
                "demand_profile": {"cls": "constant", "value": NOMINAL_RATE},
            }
        },
    }


def _valve_template() -> dict:
    """A discrete input flow, which is what a boolean control signal drives."""
    return {
        "name": VALVE_CLASS,
        "interfaces": {
            "fill__input": {"name": "fill", "port_type": {"general": "input"}}
        },
    }


def _controller_template() -> dict:
    """The controller class: two sections beside ``interfaces``, never inside it.

    ``metadata.controller`` is the discriminant, on the model of the logic
    gate's ``metadata.logic_gate``. The template declares no interface at all: a
    controller is a peer of ``ObjFlow`` and carries no flow.
    """
    return {
        "name": CONTROLLER_CLASS,
        "metadata": {"controller": True},
        "controls_in": [
            # Aggregated: two tanks publish onto this one input, and the
            # declared policy is what says how the two readings reduce (R40).
            {"name": "level", "kind": "level", "aggregate": "median"},
            {"name": "q", "kind": "rate"},
        ],
        "controls_out": [
            {
                "name": "fill",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "level",
                    "operator": ">=",
                    "threshold": FILL_THRESHOLD,
                },
            },
            {
                "name": "echo",
                "kind": "value",
                "emit": {"op": "republish", "input": "q", "gain": ECHO_GAIN},
            },
        ],
    }


def _observer_template() -> dict:
    """A second controller, whose input is the first one's value output (R4)."""
    return {
        "name": OBSERVER_CLASS,
        "metadata": {"controller": True},
        "controls_in": [{"name": "echo", "kind": "level"}],
    }


def _connection(source_id, source_iface, target_id, target_iface) -> dict:
    return {
        "component_source": source_id,
        "interface_source": source_iface,
        "component_target": target_id,
        "interface_target": target_iface,
    }


def build_payload(with_controllers: bool = True) -> dict:
    """The montage, as the platform would export it.

    ``with_controllers=False`` drops the two controller classes, their two
    components and the four information edges, and NOTHING else. What is left is
    the plant alone -- the payload shape this bridge has always read -- which is
    what makes the difference between the two builds attributable.
    """
    templates = {
        TANK_A_CLASS: _tank_template(TANK_A_CLASS, TANK_A_CONTENT),
        TANK_B_CLASS: _tank_template(TANK_B_CLASS, TANK_B_CONTENT),
        FILLER_CLASS: _continuous_source_template(FILLER_CLASS, FILL_RATE),
        SOURCE_CLASS: _continuous_source_template(SOURCE_CLASS, NOMINAL_RATE),
        SINK_CLASS: _sink_template(),
        VALVE_CLASS: _valve_template(),
    }
    components = {
        "id-ta": {"name": "TANK_A", "class_name": TANK_A_CLASS, "attributes": []},
        "id-tb": {"name": "TANK_B", "class_name": TANK_B_CLASS, "attributes": []},
        "id-fil": {"name": "FILLER", "class_name": FILLER_CLASS, "attributes": []},
        "id-src": {"name": "SRC", "class_name": SOURCE_CLASS, "attributes": []},
        "id-snk": {"name": "SINK", "class_name": SINK_CLASS, "attributes": []},
        "id-val": {"name": "VALVE", "class_name": VALVE_CLASS, "attributes": []},
    }
    connections = {
        # Ordinary flow edges, untouched by any of this.
        "c-fill": _connection("id-fil", "q", "id-ta", "q"),
        "c-flow": _connection("id-src", "q", "id-snk", "q"),
    }

    if with_controllers:
        templates[CONTROLLER_CLASS] = _controller_template()
        templates[OBSERVER_CLASS] = _observer_template()
        components["id-ctl"] = {
            "name": "CTRL",
            "class_name": CONTROLLER_CLASS,
            "attributes": [],
        }
        components["id-obs"] = {
            "name": "OBS",
            "class_name": OBSERVER_CLASS,
            "attributes": [],
        }
        connections.update(
            {
                # Observation edges: NEITHER end is a declared flow.
                "c-obs-a": _connection("id-ta", "level", "id-ctl", "level"),
                "c-obs-b": _connection("id-tb", "level", "id-ctl", "level"),
                "c-obs-rate": _connection("id-src", "q", "id-ctl", "q"),
                # A boolean signal, imported by a discrete input flow.
                "c-signal": _connection("id-ctl", "fill", "id-val", "fill"),
                # A published value, imported by a second controller.
                "c-value": _connection("id-ctl", "echo", "id-obs", "echo"),
            }
        )

    return {
        "model": {
            # Distinct per variant: PyCATSHOO refuses a second system of one
            # name, and this module builds both.
            "name": "CtrlImporterModel" if with_controllers else "CtrlImporterPlant",
            "kb": {"name": "CtrlKB", "version": "0.0.1"},
            "elements": {"components": components, "connections": connections},
        },
        "kb": {
            "name": "CtrlKB",
            "version": "0.0.1",
            "component_templates": templates,
            "interface_templates": {},
        },
    }


# ===========================================================================
# 1. The pure layer reads a controller with no library underneath it
# ===========================================================================
#
# Proved in a SUBPROCESS, and it has to be. Every other test of this file
# imports muscadet, so ``muscadet`` is in ``sys.modules`` by the time any
# in-process assertion could be made, and the claim would be unfalsifiable. The
# child blocks the three roots at the meta path and loads the importer straight
# off its file, so the package ``__init__`` -- which does pull the runtime in --
# is never executed.

_PURE_PROBE = textwrap.dedent("""
    import importlib.abc
    import importlib.util
    import json
    import sys

    BANNED = ("muscadet", "cod3s", "Pycatshoo")


    class Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in BANNED:
                raise ImportError("blocked import of " + fullname)
            return None


    sys.meta_path.insert(0, Blocker())

    module_path, payload_path = sys.argv[1], sys.argv[2]

    spec = importlib.util.spec_from_file_location("importer_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because ``dataclasses`` resolves a field
    # annotation through ``sys.modules[cls.__module__]``: a module absent from
    # it makes every dataclass in the file fail to build.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    with open(payload_path) as handle:
        payload = json.load(handle)

    ctx = module.parse_platform_export(payload)

    leaked = sorted(name for name in sys.modules if name.split(".")[0] in BANNED)
    if leaked:
        raise AssertionError("the runtime was imported after all: %r" % leaked)

    controllers = {
        comp.name: {
            "inputs": [
                {"name": e.name, "kind": e.kind, "aggregate": e.aggregate}
                for e in comp.controller.controls_in
            ],
            "outputs": [
                {"name": e.name, "kind": e.kind, "emit": e.emit}
                for e in comp.controller.controls_out
            ],
        }
        for comp in ctx.components
        if comp.controller is not None
    }

    edges = [
        {
            "source": conn.source_component,
            "source_box": conn.source_box,
            "target": conn.target_component,
            "target_box": conn.target_box,
        }
        for conn in ctx.connections
    ]

    print(json.dumps({
        "marker": module._SUPPORTS_CONTROLLERS,
        "controllers": controllers,
        "edges": edges,
    }))
    """)


def _run_pure_probe(tmp_path, payload):
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload))
    probe_path = tmp_path / "probe.py"
    probe_path.write_text(_PURE_PROBE)

    completed = subprocess.run(
        [sys.executable, str(probe_path), str(IMPORTER_PATH), str(payload_path)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


class TestPureLayerReadsAControllerWithoutTheLibrary:
    def test_the_module_carries_no_runtime_import_at_top_level(self):
        """The claim the subprocess then proves, stated where it is checkable."""
        source = IMPORTER_PATH.read_text()
        for banned in ("\nimport muscadet", "\nfrom muscadet", "\nimport cod3s"):
            assert banned not in source

    def test_a_controller_payload_parses_with_the_runtime_blocked(self, tmp_path):
        result = _run_pure_probe(tmp_path, build_payload())

        assert set(result["controllers"]) == {"CTRL", "OBS"}

        ctrl = result["controllers"]["CTRL"]
        assert ctrl["inputs"] == [
            {"name": "level", "kind": "level", "aggregate": "median"},
            {"name": "q", "kind": "rate", "aggregate": None},
        ]
        assert [entry["name"] for entry in ctrl["outputs"]] == ["fill", "echo"]
        assert [entry["kind"] for entry in ctrl["outputs"]] == ["bool", "value"]
        assert ctrl["outputs"][0]["emit"]["op"] == "compare"
        assert ctrl["outputs"][1]["emit"]["gain"] == ECHO_GAIN

        assert result["controllers"]["OBS"]["outputs"] == []

    def test_the_pure_layer_resolves_the_boxes_each_edge_will_be_wired_on(
        self, tmp_path
    ):
        """The resolution is the parse layer's, so the apply layer forks none."""
        result = _run_pure_probe(tmp_path, build_payload())
        boxes = {
            (edge["source"], edge["target"]): (edge["source_box"], edge["target_box"])
            for edge in result["edges"]
        }

        # An ordinary flow edge resolves no box: it goes through connect_flow.
        assert boxes[("SRC", "SINK")] == (None, None)
        assert boxes[("FILLER", "TANK_A")] == (None, None)
        # A level observation, both ends named after the publisher.
        assert boxes[("TANK_A", "CTRL")] == ("level_level_out", "level_level_in")
        assert boxes[("TANK_B", "CTRL")] == ("level_level_out", "level_level_in")
        # A rate observation (R38).
        assert boxes[("SRC", "CTRL")] == ("q_rate_out", "q_rate_in")
        # A boolean signal into a discrete input flow.
        assert boxes[("CTRL", "VALVE")] == ("fill_out", "fill_in")
        # A published value into a second controller.
        assert boxes[("CTRL", "OBS")] == ("echo_level_out", "echo_level_in")


# ===========================================================================
# 2. The capability marker the platform probes before translating
# ===========================================================================


class TestCapabilityMarker:
    def test_marker_is_declared_true_and_readable_from_outside(self):
        # The platform guard reads it with getattr(module, marker, False), so an
        # older muscadet answers False and the platform refuses to translate a
        # model carrying a controller. The degradation it prevents is the worst
        # of the family: the controller's template declares no interface, so it
        # would import as a component with no port at all, its edges would be
        # dropped, and the study would run to completion on a plant whose
        # regulation is simply absent -- a false figure, not an error.
        from muscadet.importers import cod3s_platform

        assert getattr(cod3s_platform, "_SUPPORTS_CONTROLLERS", False) is True

    def test_the_marker_is_visible_to_a_caller_that_never_imported_the_module(
        self, tmp_path
    ):
        """Read off the file, with the runtime blocked: the platform's own way."""
        assert _run_pure_probe(tmp_path, build_payload())["marker"] is True


# ===========================================================================
# 3. What the parse layer refuses (pure -- no system is built here)
# ===========================================================================


def _parse(payload):
    from muscadet.importers.cod3s_platform import parse_platform_export

    return parse_platform_export(payload)


def _refused(payload, match):
    from muscadet.importers.cod3s_platform import Cod3sPlatformImportError

    with pytest.raises(Cod3sPlatformImportError, match=match):
        _parse(payload)


class TestTheContractIsRefusedByName:
    def test_an_ordinary_flow_edge_naming_a_non_flow_target_is_still_refused(self):
        """The bypass is for information edges, and for those alone.

        Rewired onto the valve, which declares ``fill`` and not ``q``: the flow
        branch checks the SOURCE name against the target's inputs, that name
        being the one ``connect_flow`` collapses both ends onto.
        """
        payload = build_payload()
        payload["model"]["elements"]["connections"]["c-flow"].update(
            {"component_target": "id-val", "interface_target": "q"}
        )
        _refused(payload, "not an input flow of component 'VALVE'")

    def test_an_ordinary_flow_edge_naming_a_non_flow_source_is_still_refused(self):
        payload = build_payload()
        payload["model"]["elements"]["connections"]["c-flow"][
            "interface_source"
        ] = "nope"
        _refused(payload, "not an output flow of component 'SRC'")

    def test_a_controller_section_without_the_marker_is_refused(self):
        payload = build_payload()
        payload["kb"]["component_templates"][CONTROLLER_CLASS]["metadata"] = {}
        _refused(payload, "carries no 'controller' marker")

    def test_a_controller_declaring_interfaces_is_refused(self):
        payload = build_payload()
        payload["kb"]["component_templates"][CONTROLLER_CLASS]["interfaces"] = {
            "q__input": {"name": "q", "port_type": {"general": "input"}}
        }
        _refused(payload, "is a controller and declares 'interfaces'")

    def test_a_controller_that_is_also_a_gate_is_refused(self):
        payload = build_payload()
        payload["kb"]["component_templates"][CONTROLLER_CLASS]["metadata"][
            "logic_gate"
        ] = "or"
        _refused(payload, "BOTH a controller and a logic gate")

    def test_an_unknown_aggregation_policy_is_refused_and_named(self):
        payload = build_payload()
        payload["kb"]["component_templates"][CONTROLLER_CLASS]["controls_in"][0][
            "aggregate"
        ] = "average"
        _refused(payload, "unknown aggregate 'average'")

    def test_an_unknown_declaration_key_is_refused_and_named(self):
        payload = build_payload()
        payload["kb"]["component_templates"][CONTROLLER_CLASS]["controls_in"][0][
            "combine_fun"
        ] = "anything"
        _refused(payload, "unknown declaration key 'combine_fun'")

    def test_two_publishers_on_an_input_stating_no_reduction_are_refused(self):
        payload = build_payload()
        del payload["kb"]["component_templates"][CONTROLLER_CLASS]["controls_in"][0][
            "aggregate"
        ]
        _refused(payload, "declares no 'aggregate'")

    def test_an_edge_whose_two_ends_bear_different_names_is_refused(self):
        """The alias is derived from the name on both ends, so they must match."""
        payload = build_payload()
        payload["kb"]["component_templates"][OBSERVER_CLASS]["controls_in"][0][
            "name"
        ] = "reading"
        payload["model"]["elements"]["connections"]["c-value"][
            "interface_target"
        ] = "reading"
        _refused(payload, "must bear the same one")

    def test_a_boolean_output_wired_onto_a_controller_is_refused(self):
        payload = build_payload()
        payload["kb"]["component_templates"][OBSERVER_CLASS]["controls_in"][0][
            "name"
        ] = "fill"
        payload["model"]["elements"]["connections"]["c-value"].update(
            {"interface_source": "fill", "interface_target": "fill"}
        )
        _refused(payload, "is a boolean output and 'OBS' is a controller")

    def test_a_value_output_wired_onto_a_rate_input_is_refused(self):
        payload = build_payload()
        payload["kb"]["component_templates"][OBSERVER_CLASS]["controls_in"][0][
            "kind"
        ] = "rate"
        _refused(payload, "reads a rate")

    def test_a_level_observation_of_something_that_holds_no_volume_is_refused(self):
        payload = build_payload()
        payload["model"]["elements"]["connections"]["c-obs-a"][
            "component_source"
        ] = "id-snk"
        _refused(payload, "is not a capacity of 'SINK'")

    def test_an_edge_onto_an_undeclared_observation_input_is_refused(self):
        payload = build_payload()
        payload["model"]["elements"]["connections"]["c-obs-rate"].update(
            {"interface_source": "q", "interface_target": "nope"}
        )
        _refused(payload, "not an observation input of controller 'CTRL'")


# ===========================================================================
# 4. The system built from the montage
# ===========================================================================


#: What the plant alone produced, recorded before the controlled montage is
#: built. PyCATSHOO holds ONE system at a time, so the two builds are
#: sequential: the plant is measured, deleted, and only then does the controlled
#: montage exist.


def _record_plant(system):
    """Everything the controller-free build is judged on, as plain data."""
    topology = {
        name: (
            type(comp).__name__,
            sorted(getattr(comp, "flows_in", {})),
            sorted(getattr(comp, "flows_out", {})),
            sorted(getattr(comp, "capacities", {})),
        )
        for name, comp in system.comp.items()
    }

    system.isimu_start()
    try:
        readings = {
            "tank_a": system.comp["TANK_A"].capacities["level"].var_qty_total.value(),
            "tank_b": system.comp["TANK_B"].capacities["level"].var_qty_total.value(),
            "delivered": system.comp["SRC"].flows_out["q"].var_fed.value(),
            "valve_fed": system.comp["VALVE"].flows_in["fill"].var_fed.value(),
        }
    finally:
        system.isimu_stop()

    return {"topology": topology, "readings": readings}


@pytest.fixture(scope="module")
def montage():
    """The plant's record, then the controlled system, one at a time."""
    import cod3s
    from muscadet.importers.cod3s_platform import system_from_export

    plant = system_from_export(build_payload(with_controllers=False))
    try:
        record = _record_plant(plant)
    finally:
        plant.deleteSys()

    system = system_from_export(build_payload(with_controllers=True))
    try:
        yield {"plant": record, "system": system}
    finally:
        try:
            system.deleteSys()
        except Exception:
            pass
        cod3s.terminate_session()


@pytest.fixture(scope="module")
def controlled(montage):
    return montage["system"]


@pytest.fixture(scope="module")
def plant(montage):
    return montage["plant"]


class TestTheBuiltSystemCarriesTheController:
    def test_the_controllers_are_objctrl_and_nothing_else(self, controlled):
        for name in ("CTRL", "OBS"):
            assert name in controlled.comp
            assert type(controlled.comp[name]).__name__ == "ObjCtrl"

    def test_the_controller_holds_no_flow_at_all(self, controlled):
        """A peer of ObjFlow, not a kind of it: there is no flow collection."""
        ctrl = controlled.comp["CTRL"]
        assert getattr(ctrl, "flows_in", None) is None
        assert getattr(ctrl, "flows_out", None) is None

    def test_its_observation_inputs_are_declared_in_order_with_their_natures(
        self, controlled
    ):
        ctrl = controlled.comp["CTRL"]
        assert list(ctrl.controls_in) == ["level", "q"]
        assert ctrl.controls_in["level"].kind == "level"
        assert ctrl.controls_in["level"].combine == "median"
        assert ctrl.controls_in["q"].kind == "rate"
        # Declared nothing, so muscadet's single-publisher cap stands.
        assert ctrl.controls_in["q"].combine is None

    def test_its_outputs_carry_the_two_natures_and_their_grammar(self, controlled):
        ctrl = controlled.comp["CTRL"]
        assert list(ctrl.controls_out) == ["fill", "echo"]
        assert type(ctrl.controls_out["fill"]).__name__ == "CtrlSignalOut"
        assert type(ctrl.controls_out["echo"]).__name__ == "MeasurementOut"
        assert ctrl.controls_emit["fill"].op == "compare"
        assert ctrl.controls_emit["echo"].op == "republish"

    def test_the_threshold_became_a_variable_of_the_model(self, controlled):
        """R44: what a mode moves and an indicator names has to be a variable."""
        assert controlled.comp["CTRL"].emit_params
        assert any(
            float(var.value()) == FILL_THRESHOLD
            for var in controlled.comp["CTRL"].emit_params.values()
        )

    def test_the_class_name_survives_on_the_component(self, controlled):
        metadata = controlled.comp["CTRL"].metadata
        assert metadata["class_name"] == CONTROLLER_CLASS
        assert metadata["platform_id"] == "id-ctl"
        assert metadata["controller"] is True


class TestTheWholeChainRespondsEndToEnd:
    def test_the_aggregated_input_reduces_its_two_publishers(self, controlled):
        controlled.isimu_start()
        try:
            reading = controlled.comp["CTRL"].controls_in["level"].get_reading()
            assert reading == pytest.approx(
                (TANK_A_CONTENT + TANK_B_CONTENT) / 2, abs=CROSSING_TOL
            )
        finally:
            controlled.isimu_stop()

    def test_the_rate_input_reads_what_the_source_delivers(self, controlled):
        controlled.isimu_start()
        try:
            assert controlled.comp["CTRL"].controls_in["q"].get_reading() == (
                pytest.approx(NOMINAL_RATE)
            )
        finally:
            controlled.isimu_stop()

    def test_the_signal_reaches_the_valve_when_the_median_crosses(self, controlled):
        """The whole chain: two levels, a median, a threshold, a discrete flow.

        Read on either side of the date the median reaches the threshold, which
        pins the response to a bracket of one tenth without depending on how
        exactly the solver lands on the crossing. The valve knows nothing about
        controllers -- it declares an ordinary discrete input flow -- which is
        the whole point of the boolean output's box shape.
        """
        controlled.isimu_start()
        try:
            valve = controlled.comp["VALVE"].flows_in["fill"]
            level = controlled.comp["CTRL"].controls_in["level"]
            signal = controlled.comp["CTRL"].controls_out["fill"]

            assert signal.get_signal() is False
            assert valve.var_fed.value() is False

            controlled.isimu_step_to(CROSSING_DATE - BRACKET)
            assert level.get_reading() < FILL_THRESHOLD
            assert signal.get_signal() is False
            assert valve.var_fed.value() is False

            controlled.isimu_step_to(CROSSING_DATE + BRACKET)
            assert level.get_reading() > FILL_THRESHOLD
            assert signal.get_signal() is True
            assert valve.var_fed.value() is True
        finally:
            controlled.isimu_stop()

    def test_the_value_output_republishes_onto_the_second_controller(self, controlled):
        """R4: the output of one controller is the input of another."""
        controlled.isimu_start()
        try:
            controlled.isimu_step_to(CROSSING_DATE)

            published = controlled.comp["CTRL"].controls_out["echo"].get_level()
            observed = controlled.comp["OBS"].controls_in["echo"].get_reading()
            assert published == pytest.approx(NOMINAL_RATE * ECHO_GAIN)
            assert observed == pytest.approx(published)
        finally:
            controlled.isimu_stop()


# ===========================================================================
# 5. A KB with no controller builds exactly what it built before
# ===========================================================================
#
# The reference numbers below were MEASURED on the bridge as it stood before
# this unit, on this very payload: TANK_A holds 2, TANK_B holds 8, the source
# delivers 3 and the valve is unfed. They are the regression detector, and the
# structural assertions beside them are what would catch a regular edge
# swallowed by the information branch -- the one way this change could damage a
# payload that carries no controller at all.


class TestAPayloadWithoutAControllerIsUntouched:
    def test_the_parse_layer_engages_none_of_the_controller_machinery(self):
        ctx = _parse(build_payload(with_controllers=False))

        assert [comp.name for comp in ctx.components] == [
            "TANK_A",
            "TANK_B",
            "FILLER",
            "SRC",
            "SINK",
            "VALVE",
        ]
        assert all(comp.controller is None for comp in ctx.components)
        assert all(conn.source_box is None for conn in ctx.connections)
        assert all(conn.target_box is None for conn in ctx.connections)

    def test_every_component_is_an_objflow_with_the_flows_it_declared(self, plant):
        assert plant["topology"] == {
            "TANK_A": ("ObjFlow", ["q"], [], ["level"]),
            "TANK_B": ("ObjFlow", ["q"], [], ["level"]),
            "FILLER": ("ObjFlow", [], ["q"], []),
            "SRC": ("ObjFlow", [], ["q"], []),
            "SINK": ("ObjFlow", ["q"], [], []),
            "VALVE": ("ObjFlow", ["fill"], [], []),
        }

    def test_it_reproduces_the_numbers_measured_before_the_change(self, plant):
        assert plant["readings"]["tank_a"] == pytest.approx(TANK_A_CONTENT)
        assert plant["readings"]["tank_b"] == pytest.approx(TANK_B_CONTENT)
        assert plant["readings"]["delivered"] == pytest.approx(NOMINAL_RATE)
        assert plant["readings"]["valve_fed"] is False


def test_delete(montage):
    # Teardown is handled by the fixture; this keeps a stable last test.
    assert montage["system"] is not None
