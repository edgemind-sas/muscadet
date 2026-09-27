"""A system holding a CONTROLLER, read back as a document and rebuilt from it.

``muscadet.ObjCtrl`` is a PEER of ``ObjFlow`` (R39) -- it transports a reading
or a signal, not a conserved quantity -- so it is a subclass of neither
``ObjFlow`` nor ``cod3s.ObjMode2S`` and escaped both arms of the read. A model
carrying one died on ``AttributeError: 'ObjCtrl' object has no attribute
'flows_in'``, and once the standalone-mode ticket had turned that into a named
refusal it died on "Component PUMP_LOW is of class ObjCtrl, which no component
declaration describes" -- a better diagnosis of the same dead end. Either way
``system.simulate(params, engine=...)`` was out of reach for every model that
commands anything by threshold, and the failure arrived BEFORE any engine saw
anything.

That is not an exotic corner: ``ObjCtrl`` is what the COD3S Platform importer
instantiates as soon as a template carries ``metadata.controller``.

What this module pins
---------------------
* a controller declares itself, with both its sections and the emission grammar
  each output carries (R42);
* that declaration READS BACK: a system rebuilt from it renders the same
  document, thresholds and grammar included;
* the two translations the read owes the rebuild, each of which would otherwise
  make a controller that builds today refuse to rebuild from its own
  declaration -- an input's aggregation, and a republication's gain;
* the refusals, which are pure and need no system at all.

**Skipping the controller was never on the table, and the connections are why.**
A controller is WIRED. A document that dropped it would keep the connections
naming it -- they are read off the engine, not off the components -- and
``check_system_spec`` would refuse the whole document on a connection whose
target is not a declared component. Skip the connections too and the rebuilt
model commands nothing, with no order arriving anywhere and nothing raised.

Two claims need more than one live system, and PyCATSHOO allows one per
process, so they run in subprocesses of their own.
"""

import json
import subprocess
import sys
from pathlib import Path

import cod3s
import pytest

import muscadet
from muscadet.declare import (
    CONTROLLER_SECTIONS,
    COMPONENT_KIND_CONTROLLER,
    COMPONENT_KIND_KEY,
    ComponentSpecError,
    check_system_spec,
    component_spec,
    system_spec,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The level the montage's boolean output switches at.
START_LEVEL = 5.0

#: The two edges of its band, and the gain its value output republishes with.
BAND_ACTIVATE = 8.0
BAND_RELEASE = 6.0
ECHO_GAIN = 2.5


# ---------------------------------------------------------------------------
# The subprocess probes: everything that needs a second live system
# ---------------------------------------------------------------------------

#: The platform montage of ``tests/test_ctrl_threshold_override.py``, declared
#: and written out. That montage is the one the ticket reproduces the failure
#: on, and it is a REAL importer product rather than a hand-built controller:
#: three instances of one controller class, two of them tuned away from it, the
#: metadata bags the importer attaches included.
_DECLARE_THE_PLATFORM_MONTAGE = """
import json, sys

sys.path.insert(0, "tests")

from muscadet.engine import system_declaration
from muscadet.importers.cod3s_platform import system_from_export
from test_ctrl_threshold_override import build_payload

document = system_declaration(system_from_export(build_payload()))

# The document survives being written out, checked in the process that produced
# it: a live object that got through the read is caught here and not in a
# parent that only ever sees JSON.
reread = json.loads(json.dumps(document))

kinds = {}
for entry in document["components"].values():
    kind = entry.get("kind", "flow")
    kinds[kind] = kinds.get(kind, 0) + 1

print("RESULT " + json.dumps({
    "kinds": kinds,
    "survives_json": reread == document,
    "pump_low": document["components"]["PUMP_LOW"],
    "connections": document["connections"],
}))
"""

#: Declared, torn down, rebuilt from the document alone, declared again. The
#: comparison is on the DOCUMENT, as it is for the two other component kinds:
#: two engines receive the same declaration or they do not.
_ROUND_TRIP = """
import json, sys

sys.path.insert(0, "tests")

import cod3s
import muscadet
from muscadet.declare import build_system, system_spec
from muscadet.importers.cod3s_platform import system_from_export
from test_ctrl_threshold_override import build_payload

original = system_from_export(build_payload())
declared = json.loads(json.dumps(system_spec(original)))
original.deleteSys()
cod3s.terminate_session()

rebuilt_system = build_system(declared, system=muscadet.System(name="CtrlRebuilt"))
rebuilt = json.loads(json.dumps(system_spec(rebuilt_system)))


def comparable(document):
    document = dict(document)
    # The system's own name is the caller's, not the document's subject, and
    # ``source_cls`` is origin rather than identity -- both are what the two
    # other round trips already strip, for the same reasons.
    document.pop("name", None)
    document["components"] = {
        name: {k: v for k, v in entry.items() if k != "source_cls"}
        for name, entry in document["components"].items()
    }
    return document


controller = rebuilt_system.comp["PUMP_LOW"]

print("RESULT " + json.dumps({
    "identical": comparable(declared) == comparable(rebuilt),
    "declared": declared["components"]["PUMP_LOW"],
    "rebuilt": rebuilt["components"]["PUMP_LOW"],
    # The rebuild reached the ENGINE and not merely the document: the tuned
    # threshold is the initial value of the variable a run reads, and the
    # grammar compiled to the automata that watch it.
    "threshold": controller.variable("run_threshold").value(),
    "emit_params": sorted(controller.emit_params),
    "automata": sorted(controller.automata_d),
}))
"""


def run_probe(script, *args):
    """Run ``script`` in a process of its own and return what it printed.

    One live system per process is the constraint every one of these probes
    exists for, so the failure that matters is the probe dying: its stderr is
    the message, and swallowing it would leave a test failing on a missing key.
    """
    completed = subprocess.run(
        [sys.executable, "-c", script, *args],
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


def test_a_platform_model_with_controllers_declares_itself():
    """The claim the ticket opens on, on the very montage it reproduces with."""
    result = run_probe(_DECLARE_THE_PLATFORM_MONTAGE)

    # Two fillers and a tank, and the three pumps of one controller class.
    assert result["kinds"] == {"flow": 2, "controller": 3}, result


def test_that_document_survives_a_json_round_trip():
    """A declaration is an exchange format or it is nothing, and the metadata
    is where that used to break: the importer indexes its override bags by a
    ``(name, role)`` pair, and a tuple key walks a value-only gate untouched to
    die at ``json.dumps`` on a message naming a type and no field."""
    result = run_probe(_DECLARE_THE_PLATFORM_MONTAGE)

    assert result["survives_json"], result
    trail = result["pump_low"]["metadata"]["controller_threshold_overrides"]
    assert {entry["name"] for entry in trail} == {
        "run__threshold",
        "alarm__op1__activate",
        "alarm__op1__release",
    }


def test_the_tuned_threshold_is_what_the_document_carries():
    """Not the CLASS value: the platform importer folds an instance's threshold
    overrides into the emission grammar at the parse layer, so what the built
    node holds is already the tuned number. ``PUMP_LOW`` starts at 2, its class
    at 5, and a document carrying 5 would be a reliability figure that is wrong
    and looks right."""
    result = run_probe(_DECLARE_THE_PLATFORM_MONTAGE)

    outputs = {entry["name"]: entry for entry in result["pump_low"]["controls_out"]}
    assert outputs["run"]["emit"]["threshold"] == 2.0

    # And the nested one, reached through its operand chain, which is what
    # makes the walk recursive rather than a dump of the root.
    band = outputs["alarm"]["emit"]["operands"][1]
    assert (band["activate"], band["release"]) == (3.0, 0.5)


def test_the_observation_wire_is_not_written_as_a_flow():
    """A measurement link follows the very ``{x}_out`` / ``{x}_in`` convention a
    flow does, so it used to be written with a ``flow`` key naming a flow
    nothing declares -- and the rebuild routed it through ``connect_flow``,
    which reads ``flows_in`` on a class that has none (R39). Read back as the
    raw connection the README prescribes for a measurement link."""
    result = run_probe(_DECLARE_THE_PLATFORM_MONTAGE)

    by_pair = {
        (entry["source"], entry["target"]): entry for entry in result["connections"]
    }

    observation = by_pair[("TANK", "PUMP_LOW")]
    assert observation["source_box"] == "level_level_out"
    assert observation["target_box"] == "level_level_in"
    assert "flow" not in observation

    # The real flow beside it still carries its name, which is what re-runs the
    # discrete/continuous family check at the rebuild.
    assert by_pair[("FILL", "TANK")]["flow"] == "q"


def test_the_document_round_trips_through_its_own_controllers():
    """Declared, torn down, rebuilt, declared again: identical."""
    result = run_probe(_ROUND_TRIP)

    assert result["identical"], (result["declared"], result["rebuilt"])


def test_the_rebuild_reaches_the_engine_and_not_only_the_document():
    """Two documents matching is not the claim -- a rebuild that lost every
    threshold would match a read that lost them too. What the rebuilt component
    HOLDS is the second half: the tuned initial value, one variable per number
    the grammar declares (R44), and the automata that watch them."""
    result = run_probe(_ROUND_TRIP)

    assert result["threshold"] == 2.0
    assert result["emit_params"] == [
        "alarm_operand_0_threshold",
        "alarm_operand_1_activate",
        "alarm_operand_1_release",
        "run_threshold",
    ]
    assert result["automata"], result


# ---------------------------------------------------------------------------
# One live system, read back and checked in place
# ---------------------------------------------------------------------------


class CtrlDeclarationTestVolume(muscadet.ObjFlow):
    """Something to observe: a volume publishing its level under ``level``.

    The mouthful of a name is deliberate. ``add_component(cls=...)`` resolves
    through ``PycComponent.get_subclasses()``, the live subclass tree of the
    whole process, which keeps the LAST class defined under a given name -- so
    a test class called ``Tank`` shadows any shipped one for every other module
    of the suite from the moment pytest imports this one at collection.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowContinuousIn", name="q"))
        self.add_capacity(name="level", flow="q", capacity=100.0)


class CtrlDeclarationTestGauge(muscadet.ObjFlow):
    """A second volume, publishing under ``redundant``.

    A class of its own and not a second capacity on the first: an observation
    input reads the publisher of its OWN name -- that is what makes the
    exported and imported aliases line up -- so two publishers feeding one
    aggregating input must both publish under that input's name.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowContinuousIn", name="q"))
        self.add_capacity(name="redundant", flow="q", capacity=100.0)


class CtrlDeclarationTestSensor(muscadet.ObjFlow):
    """An ORDINARY component observing the same level, and not a controller.

    Here for the general half of one defect: a measurement link follows the
    ``{x}_out`` / ``{x}_in`` convention a flow does, so it was written out with
    a ``flow`` key whatever the classes on either end. On two ``ObjFlow``
    components that was a latent ``KeyError`` at rebuild -- ``connect_flow``
    indexes ``flows_out["level_level"]`` for its authorization check -- and on
    a controller it was an ``AttributeError`` one line earlier. One fix, and
    this is the end of it a controller does not cover.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="level")


@pytest.fixture(scope="module")
def the_run():
    """One system, one controller, and the document read off it.

    The controller carries one of each shape the read has to handle: a plain
    input and an AGGREGATING one, a boolean output on a comparison, a second
    boolean output on a band, and a VALUE output republishing with a gain.
    """
    system = muscadet.System(name="CtrlDecl")

    system.add_component(name="TANK", cls="CtrlDeclarationTestVolume")
    system.add_component(name="SENSOR", cls="CtrlDeclarationTestSensor")
    for name in ("GAUGE_A", "GAUGE_B"):
        system.add_component(name=name, cls="CtrlDeclarationTestGauge")

    system.add_component(
        name="CTRL",
        cls="ObjCtrl",
        label="The controller",
        metadata={"controller": True},
        controls_in=[
            {"name": "level", "kind": "level"},
            {"name": "redundant", "kind": "level", "aggregate": "median"},
        ],
        controls_out=[
            {
                "name": "run",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "level",
                    "operator": ">=",
                    "threshold": START_LEVEL,
                },
            },
            {
                "name": "alarm",
                "kind": "bool",
                "emit": {
                    "op": "band",
                    "input": "redundant",
                    "direction": "above",
                    "activate": BAND_ACTIVATE,
                    "release": BAND_RELEASE,
                },
            },
            {
                "name": "echo",
                "kind": "value",
                "emit": {"op": "republish", "input": "level", "gain": ECHO_GAIN},
            },
            # An output nothing computes: its value is written by hand, which
            # is the ``emit=None`` case and the one a dump would have written
            # out as a null grammar rather than as no grammar at all.
            {"name": "manual", "kind": "bool"},
        ],
    )

    system.connect("TANK", "level_level_out", "CTRL", "level_level_in")
    system.connect("TANK", "level_level_out", "SENSOR", "level_level_in")
    system.connect("GAUGE_A", "redundant_level_out", "CTRL", "redundant_level_in")
    system.connect("GAUGE_B", "redundant_level_out", "CTRL", "redundant_level_in")

    yield {"system": system, "spec": system_spec(system)}


def _outputs(spec):
    return {entry["name"]: entry for entry in spec["controls_out"]}


def test_the_controller_is_a_component_of_its_own_kind(the_run):
    """Stated on the object rather than on the system, because the failure was
    on the object: ``comp.flows_in`` on something that has none."""
    controller = the_run["system"].comp["CTRL"]
    assert not hasattr(controller, "flows_in")

    spec = component_spec(controller)
    assert spec[COMPONENT_KIND_KEY] == COMPONENT_KIND_CONTROLLER
    assert spec["cls"] == "ObjCtrl"


def test_it_gets_its_own_entry_in_the_system_document(the_run):
    """An entry, not a skip -- and the connections naming it are why: they are
    read off the engine, so a document that dropped the component would be
    refused on its own wiring."""
    components = the_run["spec"]["components"]
    assert set(components) == {"TANK", "SENSOR", "GAUGE_A", "GAUGE_B", "CTRL"}
    assert components["CTRL"][COMPONENT_KIND_KEY] == COMPONENT_KIND_CONTROLLER
    assert components["TANK"].get(COMPONENT_KIND_KEY, "flow") == "flow"

    wired = {(e["target"], e["target_box"]) for e in the_run["spec"]["connections"]}
    assert wired == {
        ("CTRL", "level_level_in"),
        ("CTRL", "redundant_level_in"),
        ("SENSOR", "level_level_in"),
    }


def test_an_aggregating_input_comes_back_under_the_interface_spelling(the_run):
    """The ONE key that does not pass through verbatim. The interface says
    ``aggregate`` and the measurement channel underneath says ``combine``; left
    as ``combine`` the rebuild is refused by name, the channel's own keys being
    deliberately kept out of a controller declaration (R40)."""
    inputs = {
        entry["name"]: entry
        for entry in the_run["spec"]["components"]["CTRL"]["controls_in"]
    }

    assert inputs["redundant"]["aggregate"] == "median"
    assert "combine" not in inputs["redundant"]
    # The single-source input says so rather than saying nothing: ``None`` is
    # the one-publisher cap, which is a decision and not an omission.
    assert inputs["level"]["aggregate"] is None


def test_each_output_says_its_nature_and_what_it_emits(the_run):
    outputs = _outputs(the_run["spec"]["components"]["CTRL"])

    assert outputs["run"]["kind"] == "bool"
    assert outputs["run"]["emit"] == {
        "op": "compare",
        "input": "level",
        "operator": ">=",
        "threshold": START_LEVEL,
    }
    assert outputs["echo"]["kind"] == "value"


def test_a_band_writes_both_its_edges(the_run):
    """A band declared without a release edge has one filled in from its
    activation level by the validator, so the node never holds a None there and
    the document always carries the two numbers. That is what makes the round
    trip stable rather than what makes it verbose."""
    assert _outputs(the_run["spec"]["components"]["CTRL"])["alarm"]["emit"] == {
        "op": "band",
        "input": "redundant",
        "direction": "above",
        "activate": BAND_ACTIVATE,
        "release": BAND_RELEASE,
    }


def test_a_republished_gain_is_written_once(the_run):
    """ONE number, ONE spelling. ``emit_gain_params`` folds the grammar's gain
    into ``gain_default`` -- the initial value of ``echo_level_gain``, which is
    the endpoint a failure mode clamps -- and ``add_control_out`` REFUSES a
    declaration carrying both. A read writing the two back would make a
    controller that builds today refuse to rebuild from its own declaration."""
    echo = _outputs(the_run["spec"]["components"]["CTRL"])["echo"]

    assert echo["emit"] == {"op": "republish", "input": "level", "gain": ECHO_GAIN}
    assert "gain_default" not in echo

    # ``source`` names the capacity a publication reads, and a controller has
    # none: what a value output publishes comes from its grammar, which is why
    # the key is not one a controller output accepts.
    assert "source" not in echo


def test_an_output_nothing_computes_carries_no_grammar(the_run):
    """``emit`` absent, not ``emit: null``: the value is written by hand, and a
    key saying "no grammar" is not the same document as no key at all."""
    manual = _outputs(the_run["spec"]["components"]["CTRL"])["manual"]

    assert "emit" not in manual
    assert manual["kind"] == "bool"


def test_the_live_signal_variable_is_not_written_out(the_run):
    """``CtrlSignalOut.var`` is the PyCATSHOO variable the output WRITES, and
    the one runtime handle ``RUNTIME_FIELD_PREFIXES`` does not catch:
    ``"var".startswith("var_")`` is False where its neighbour ``var_available``
    falls through. Excluded through the ``skip`` parameter, borne by the class
    that justifies it -- not by widening the prefix, and above all not by
    ``DERIVED_EXCLUDED_FIELDS``, which is consulted by field name alone and
    where ``var`` is the SUBJECT a variable indicator observes."""
    for entry in the_run["spec"]["components"]["CTRL"]["controls_out"]:
        assert "var" not in entry
        assert "var_available" not in entry


def test_the_decoration_is_written_only_when_it_says_something(the_run):
    spec = the_run["spec"]["components"]["CTRL"]

    assert spec["label"] == "The controller"
    assert spec["metadata"] == {"controller": True}
    # ``description`` defaults to the label, so writing it would fill every
    # declaration with its own label twice.
    assert "description" not in spec


def test_a_measurement_wire_between_two_flow_components_is_not_a_flow_either(
    the_run,
):
    """The general half of the fix, on two ordinary ``ObjFlow`` components.

    A capacity publishes its level on ``level_level_out`` and a channel imports
    it on ``level_level_in``, which is the flow convention exactly -- so the
    pair was written out with ``flow: "level_level"``, a flow neither component
    declares. The rebuild routed it through ``connect_flow``, which INDEXES
    ``flows_out["level_level"]`` for its authorization check, and died on a
    bare ``KeyError``. Nothing about a controller is needed to reach that; the
    controller only made it louder.
    """
    to_sensor = [
        entry for entry in the_run["spec"]["connections"] if entry["target"] == "SENSOR"
    ]

    assert len(to_sensor) == 1
    assert "flow" not in to_sensor[0]


def test_the_document_is_data(the_run):
    """Serialisable, or it is not a document two engines can share."""
    assert json.loads(json.dumps(the_run["spec"])) == the_run["spec"]


def test_it_validates_without_building(the_run):
    check_system_spec(the_run["spec"])


# ---------------------------------------------------------------------------
# The sections a controller carries, derived rather than restated
# ---------------------------------------------------------------------------


def test_the_sections_are_the_ones_the_class_declares():
    """The anti-shrink half of the derivation.

    ``CONTROLLER_SECTIONS`` is the intersection of ``DECLARATION_SECTIONS``
    with ``ObjCtrl.DECLARATION_KEYS``, which covers the addition: a third
    section registered in both is read and built without this module changing.
    It covers nothing at all about a section added to the CLASS alone -- it
    would simply fall out of the intersection, and a section a component
    declares that the document does not carry is a declaration lost without a
    trace. This is the guard for that half.
    """
    assert set(CONTROLLER_SECTIONS) == set(muscadet.ObjCtrl.DECLARATION_KEYS)
    # And in the order the one place the order is written down gives them: an
    # output's grammar names an input, so the inputs are declared first.
    assert CONTROLLER_SECTIONS == ("controls_in", "controls_out")


# ---------------------------------------------------------------------------
# What is refused, and by what message. All pure: no system is raised.
# ---------------------------------------------------------------------------


def _declaration(**overrides):
    spec = {
        "name": "CTRL",
        COMPONENT_KIND_KEY: COMPONENT_KIND_CONTROLLER,
        "cls": "ObjCtrl",
        "controls_in": [{"name": "level", "aggregate": None}],
        "controls_out": [{"name": "run", "kind": "bool"}],
    }
    spec.update(overrides)
    return spec


def test_a_declaration_without_a_name_is_refused():
    spec = _declaration()
    del spec["name"]
    with pytest.raises(ComponentSpecError, match="name"):
        muscadet.check_spec(spec)


def test_an_unknown_key_is_refused_by_name():
    """The same discipline as every other section of this module: a misspelt
    key is otherwise swallowed whole, and a controller silently missing its
    outputs is indistinguishable from one that never declared any."""
    with pytest.raises(ComponentSpecError, match="controls_outs"):
        muscadet.check_spec(_declaration(controls_outs=[{"name": "run"}]))


def test_a_flow_section_is_not_a_controller_key():
    """A controller carries no flow at all (R39), so a spec that puts one on it
    is refused rather than silently dropped."""
    with pytest.raises(ComponentSpecError, match="flows"):
        muscadet.check_spec(_declaration(flows=[{"cls": "FlowOut", "name": "f"}]))


def test_an_unnamed_interface_is_refused():
    """An interface name is what an output grammar names its input by and what
    a connection reaches its message box through."""
    with pytest.raises(ComponentSpecError, match="controls_in"):
        muscadet.check_spec(_declaration(controls_in=[{"kind": "level"}]))


def test_an_emission_grammar_is_checked_before_anything_is_built():
    """``build_ctrl_node`` is pure, so a batch of declarations is sorted without
    raising a system -- and the refusal arrives as a ``ComponentSpecError``,
    like every other refusal of this module, rather than as the bare
    ``ValueError`` the grammar raises."""
    spec = _declaration(
        controls_out=[{"name": "run", "kind": "bool", "emit": {"op": "integrate"}}]
    )
    with pytest.raises(ComponentSpecError, match="integrate"):
        muscadet.check_spec(spec)


def test_an_inverted_band_is_refused_by_what_is_wrong_with_it():
    """A band detecting BELOW 3 and releasing at 1 can never release: the
    reading has to fall to 1 while the band is what stops it falling. The
    montage latches on its first activation and never speaks again."""
    spec = _declaration(
        controls_out=[
            {
                "name": "run",
                "kind": "bool",
                "emit": {
                    "op": "band",
                    "input": "level",
                    "direction": "below",
                    "activate": 3.0,
                    "release": 1.0,
                },
            }
        ]
    )
    with pytest.raises(ComponentSpecError, match="releases"):
        muscadet.check_spec(spec)


def test_a_python_callable_in_the_grammar_is_refused():
    """The closed operator list is closed to a callable above all (R42): an
    output value needs a form a threshold can be read out of, which no
    continuity attestation buys."""
    spec = _declaration(
        controls_out=[{"name": "run", "kind": "bool", "emit": lambda: True}]
    )
    with pytest.raises(ComponentSpecError, match="COMPOSITION"):
        muscadet.check_spec(spec)


def test_a_controller_naming_an_undeclared_wire_end_is_refused_at_the_system_scale(
    the_run,
):
    """The system-scale check reads a controller like any other component, so a
    connection pointing at one the document does not declare is refused before
    the first component is built."""
    spec = json.loads(json.dumps(the_run["spec"]))
    spec["components"].pop("CTRL")
    with pytest.raises(Exception, match="CTRL"):
        check_system_spec(spec)


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
