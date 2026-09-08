"""A system whose components are not all made of flows, read back as a document.

``system_declaration`` used to iterate ``flows_in`` over everything
``system.comp`` held, and a standalone failure mode -- a ``cod3s.ObjFMDelay``,
added by ``system.add_component(cls="ObjFMDelay", ...)`` -- holds none. Two of
the six interactive examples died on an ``AttributeError`` naming a class the
modeller never wrote down, before any engine saw anything, which is precisely
what a document checked on the way out exists to avoid.

**The arbitration this module pins: a standalone mode gets its own entry, it is
not skipped.** A mode declared ON a component is kept by
``ObjFlow.declared_failure_modes`` and written into that component's
``failure_modes`` section, so skipping the component-scale one would lose
nothing. A STANDALONE mode is recorded nowhere else -- its targets carry no
trace of it -- so skipping it would not lose decoration, it would lose the
model: ``cyber_3comp`` would declare three components and none of the three-step
compromise cascade that is the whole example, and an engine reading that
document would compute a system that never fails.

Two claims need more than one live system and PyCATSHOO allows one per process,
so they run in subprocesses of their own: the six examples (one process each,
as the ticket asks) and the round trip, which has to tear a system down before
rebuilding one from its own document.
"""

import json
import subprocess
import sys
from pathlib import Path

import cod3s
import pytest

import muscadet
from muscadet.declare import (
    COMPONENT_KIND_FAILURE_MODE,
    COMPONENT_KIND_KEY,
    ComponentSpecError,
    SystemSpecError,
    check_system_spec,
    component_spec,
    system_spec,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The six factories ``examples/isimu`` ships and the ticket names. Listed here
#: rather than discovered by globbing the package: a module dropped in beside
#: them is not automatically one of the six, and a module REMOVED from them
#: should fail this list rather than silently shrink the claim.
INTERACTIVE_EXAMPLES = (
    "cyber_3comp",
    "power_plant",
    "rbd_kn",
    "trigger_source",
    "datacenter_lite",
    "inverter_chain",
)

#: Run in the subprocess, one example per process: PyCATSHOO forbids a second
#: live system, so six examples are six processes and there is no arrangement
#: of fixtures that gets around it. Prints the shape of the document it
#: produced, which is what the parent asserts on.
_DECLARE_ONE_EXAMPLE = """
import importlib, json, sys

from muscadet.engine import system_declaration

module = importlib.import_module("examples.isimu." + sys.argv[1])
spec = system_declaration(module.build())

# Serialisable, or it is not a document two engines can share. Checked HERE
# rather than in the parent so a live object that survived the read is caught
# in the process that produced it.
json.dumps(spec)

kinds = {}
for entry in spec["components"].values():
    kind = entry.get("kind", "flow")
    kinds[kind] = kinds.get(kind, 0) + 1

print("RESULT " + json.dumps({"kinds": kinds, "version": spec["version"]}))
"""

#: The round trip, at the system scale, on a model carrying standalone modes:
#: declared, torn down, rebuilt from the document alone, declared again. The
#: comparison is on the DOCUMENT, as it is for the ObjFlow round trip -- two
#: engines receive the same declaration or they do not.
_ROUND_TRIP = """
import json

import cod3s
import muscadet
from muscadet.declare import build_system, system_spec

from examples.isimu.cyber_3comp import build

original = build()
declared = json.loads(json.dumps(system_spec(original)))
original.deleteSys()
cod3s.terminate_session()

rebuilt = system_spec(build_system(declared, system=muscadet.System(name="Rebuilt")))
rebuilt = json.loads(json.dumps(rebuilt))


def comparable(document):
    document = dict(document)
    # The system's own name is the caller's, not the document's subject, and
    # ``source_cls`` is origin rather than identity -- both are what the
    # ObjFlow round trip already strips, for the same reasons.
    document.pop("name", None)
    document["components"] = {
        name: {k: v for k, v in entry.items() if k != "source_cls"}
        for name, entry in document["components"].items()
    }
    return document


print("RESULT " + json.dumps({
    "identical": comparable(declared) == comparable(rebuilt),
    "modes": sorted(
        name
        for name, entry in declared["components"].items()
        if entry.get("kind") == "failure_mode"
    ),
    "rebuilt_modes": sorted(
        name
        for name, entry in rebuilt["components"].items()
        if entry.get("kind") == "failure_mode"
    ),
}))
"""


#: A common cause mode declared with ONE rate for every order, which is the
#: ordinary way to write one -- and the shape that catches the tuple trap: the
#: engine pads the vector to one entry per order with ``(0,)`` TUPLES, a
#: document has no tuples, and a list handed back where the engine reads a
#: tuple is taken for a single parameter whose value is a list. PyCATSHOO then
#: refuses it with a Boost signature dump naming no field at all.
_CC_ROUND_TRIP = """
import json

import cod3s
import muscadet
from muscadet.declare import build_system, system_spec


class ModeTargetForDeclarationTest(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowOut", name="f", var_prod_default=True))


system = muscadet.System(name="CCRef")
for name in ("A", "B", "C"):
    system.add_component(name=name, cls="ModeTargetForDeclarationTest")
system.add_component(
    cls="ObjFMExp",
    fm_name="cc",
    targets=["A", "B", "C"],
    failure_effects={"f_fed_available_out": False},
    failure_param=1.0,
    repair_param=2.0,
)

declared = json.loads(json.dumps(system_spec(system)))
automata = len(system.comp["X__cc"].automata_d)
system.deleteSys()
cod3s.terminate_session()

rebuilt_system = build_system(declared, system=muscadet.System(name="CCRebuilt"))
rebuilt = json.loads(json.dumps(system_spec(rebuilt_system)))

def comparable(components):
    # ``source_cls`` is origin, not identity: the rebuild's origin IS the
    # document, so it reports ObjFlow, exactly as the ObjFlow round trip
    # already documents.
    return {
        name: {k: v for k, v in entry.items() if k != "source_cls"}
        for name, entry in components.items()
    }


print("RESULT " + json.dumps({
    "identical": comparable(declared["components"]) == comparable(rebuilt["components"]),
    "failure_param": declared["components"]["X__cc"]["failure_param"],
    "automata": automata,
    "rebuilt_automata": len(rebuilt_system.comp["X__cc"].automata_d),
}))
"""


#: The generic engine, round-tripped: it is the vocabulary that carries
#: declared occurrence LAWS, which are pydantic models rather than mappings and
#: are the one thing on this path that has to be dumped rather than walked.
_MODE2S_ROUND_TRIP = """
import json

import cod3s
import muscadet
from muscadet.declare import build_system, system_spec


class ModeTargetForDeclarationTest(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowOut", name="f", var_prod_default=True))


system = muscadet.System(name="M2SRef")
for name in ("A", "B"):
    system.add_component(name=name, cls="ModeTargetForDeclarationTest")
system.add_component(
    cls="ObjMode2S",
    mode_name="engine",
    targets=["A"],
    occ_law={"cls": "exp", "rate": 0.1},
    not_occ_law={"cls": "delay", "time": 3},
    occ_effects={"f_fed_available_out": False},
)
# The shape the COD3S Platform actually emits: a law whose parameter is a
# VECTOR, one entry per common-cause order, rather than the scalar a hand-
# written model uses. Measured on a real export bundle
# (`occ_law: {"cls": "delay", "time": [1000.0]}`), so it is pinned here: a
# scalar-only test would have let a vector law be lost in silence.
system.add_component(
    cls="ObjMode2S",
    mode_name="vector",
    targets=["A", "B"],
    occ_law={"cls": "exp", "rate": [0.1, 0.01]},
    not_occ_law={"cls": "exp", "rate": [1.0, 1.0]},
    occ_effects={"f_fed_available_out": False},
)
# ``targets=None`` is the SELF-HOSTED shape, and it is not ``targets=[]``: one
# automaton on the component itself, under a name of its own. The engine
# normalises the None to an empty list right after reading it, so the document
# reads the distinction from the mode's own flag, and ``aut_name`` -- which the
# engine does not keep at all -- from the automaton that got built.
# The platform's own shape for a multi-clause indicator, and the one whose
# comparison is only recoverable through the operator singletons.
system.add_component(
    name="_ind_two_clauses",
    cls="ObjEvent",
    cond=[
        [{"attr": "f_fed_out", "obj": "A", "value": True}],
        [{"attr": "f_fed_out", "obj": "A", "value": False}],
    ],
    outer_logic="all",
    cond_operator="!=",
    tempo_occ=2,
)
system.add_component(
    cls="ObjMode2S",
    mode_name="selfhosted",
    targets=None,
    aut_name="ev",
    occ_law={"cls": "delay", "time": 5},
    not_occ_law={"cls": "delay", "time": 7},
)

declared = json.loads(json.dumps(system_spec(system)))
system.deleteSys()
cod3s.terminate_session()

rebuilt_system = build_system(declared, system=muscadet.System(name="M2SRebuilt"))
rebuilt = json.loads(json.dumps(system_spec(rebuilt_system)))


def comparable(components):
    return {
        name: {k: v for k, v in entry.items() if k != "source_cls"}
        for name, entry in components.items()
    }


print("RESULT " + json.dumps({
    "identical": comparable(declared["components"]) == comparable(rebuilt["components"]),
    "declared": declared["components"]["A__engine"],
    "rebuilt": rebuilt["components"]["A__engine"],
    "self_hosted": declared["components"]["selfhosted"],
    "event": declared["components"]["_ind_two_clauses"],
    "vector_law": declared["components"]["X__vector"],
    "vector_automata": len(rebuilt_system.comp["X__vector"].automata_d),
    "self_hosted_automata": sorted(rebuilt_system.comp["selfhosted"].automata_d),
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


# ---------------------------------------------------------------------------
# The six examples, one process each
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("example", INTERACTIVE_EXAMPLES)
def test_every_interactive_example_declares_itself(example):
    """The claim the ticket opens on: all six, not four."""
    result = run_probe(_DECLARE_ONE_EXAMPLE, example)
    assert result["kinds"].get("flow"), result


def test_the_two_richest_examples_declare_their_failure_modes():
    """The two that used to fail are the two carrying standalone modes, and
    what they declare is the cascade -- not three components and no attack."""
    assert run_probe(_DECLARE_ONE_EXAMPLE, "cyber_3comp")["kinds"] == {
        "flow": 3,
        "failure_mode": 3,
    }
    assert run_probe(_DECLARE_ONE_EXAMPLE, "power_plant")["kinds"] == {
        "flow": 5,
        "failure_mode": 5,
    }


def test_a_model_of_pure_flows_declares_no_mode():
    """The other four are unchanged, key for key: a document that grew a
    section on a model that has none would be a document that moved."""
    for example in ("rbd_kn", "trigger_source", "datacenter_lite", "inverter_chain"):
        assert "failure_mode" not in run_probe(_DECLARE_ONE_EXAMPLE, example)["kinds"]


def test_the_document_round_trips_through_its_own_modes():
    """Declared, torn down, rebuilt, declared again: identical."""
    result = run_probe(_ROUND_TRIP)
    assert result["modes"] == ["Proc__mdc_proc", "Srv__mdc_a", "Srv__mdc_b"]
    assert result["rebuilt_modes"] == result["modes"]
    assert result["identical"], result


def test_a_common_cause_order_vector_survives_having_no_tuples():
    """The trap a document with no tuples sets, pinned where it bit.

    A mode over three targets declared with one rate is padded by the engine
    to ``[1.0, (0,), (0,)]``, and JSON turns those tuples into lists. Handed
    back as lists they are read as one parameter holding a list, and PyCATSHOO
    refuses the value without naming a field. The document says a list at an
    order IS that order's parameters, and the build converts.
    """
    result = run_probe(_CC_ROUND_TRIP)
    assert result["failure_param"] == [1.0, [0], [0]], "the padding, as a document"
    # Three, not seven: the padded orders carry a rate of 0, so their law is
    # inactive and the default drop leaves only the three order-1 automata.
    # Which is also why no ``drop_inactive_automata`` is written here -- see
    # the mode that DOES get one, below.
    assert result["automata"] == result["rebuilt_automata"] == 3
    assert result["identical"], result


def test_the_generic_engine_round_trips_with_its_declared_laws():
    """An occurrence law is a pydantic model, so it is dumped and taken back by
    ``parse_mode_law``: without that, the mode would be refused as a live
    object and no ObjMode2S could cross at all."""
    result = run_probe(_MODE2S_ROUND_TRIP)
    assert result["declared"]["occ_law"] == {"cls": "exp", "rate": 0.1}
    # The self-hosted shape crosses whole: ``targets`` stays null rather than
    # collapsing to the empty list the engine normalises it into, and the
    # automaton comes back under the name it was given and not the mode's.
    assert result["self_hosted"]["targets"] is None
    assert result["self_hosted"]["aut_name"] == "ev"
    assert result["self_hosted_automata"] == ["ev"]
    # A comparison that is NOT the default comes back by its spelling, which
    # is the whole reason an event has a declaration form rather than a refusal.
    assert result["event"]["cond_operator"] == "!="
    # A law whose parameter is a vector, one entry per common-cause order,
    # which is what a platform export carries and what a scalar-only test
    # would have let through unnoticed.
    assert result["vector_law"]["occ_law"] == {"cls": "exp", "rate": [0.1, 0.01]}
    assert result["vector_automata"] == 2**2 - 1
    assert result["identical"], result


# ---------------------------------------------------------------------------
# One system, read back component by component
# ---------------------------------------------------------------------------


class ModeTargetForDeclarationTest(muscadet.ObjFlow):
    """Something for a mode to act on: one discrete output it can lock.

    The mouthful of a name is deliberate. ``add_component(cls=...)`` resolves
    through ``PycComponent.get_subclasses()``, which is the live subclass tree
    of the whole process and keeps the LAST class defined under a given name.
    A class defined here as ``Target`` therefore shadows
    ``muscadet.kb.rbd.Target`` for every OTHER module of the suite from the
    moment pytest imports this one at collection -- and the modules that broke
    were fifty files earlier, failing inside ``connect_flow`` on a component
    silently built from the wrong class. Test classes get names nothing else
    can be called.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowOut", name="f", var_prod_default=True))


def _naming(target_set_idx, order_max, **kwargs):
    """A naming function: a live object, and the reason the read refuses it."""
    return "__" + "".join(str(i) for i in target_set_idx)


@pytest.fixture(scope="module")
def the_run():
    """One system, and its document read BEFORE the undeclarable parts join it.

    PyCATSHOO forbids a second live system, so the components whose whole point
    is that they cannot be written out share this one. Reading the document
    first is what keeps them from poisoning it: what they are here for is
    :func:`component_spec` called on them by name, never a system-scale read.
    """
    system = muscadet.System(name="FMDecl")

    for name in ("A", "B", "C"):
        system.add_component(name=name, cls="ModeTargetForDeclarationTest")

    system.add_component(
        cls="ObjFMDelay",
        fm_name="single",
        targets=["A"],
        failure_param=4,
        failure_effects={"f_fed_available_out": False},
        repair_cond=False,
        repair_param=1e9,
    )
    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="common",
        targets=["A", "B", "C"],
        target_name="ABC",
        failure_effects={"f": False},
        failure_param=[1, 0, 0],
        repair_param=[1, 0, 0],
        drop_inactive_automata=False,
        cond_outer_logic=all,
    )
    # The generic two-state engine, which the COD3S Platform emits natively and
    # which is NOT an ObjFM: it takes the occ_*/not_occ_* spelling and carries
    # its occurrence laws as declarations rather than in its class.
    system.add_component(
        cls="ObjMode2S",
        mode_name="engine",
        targets=["C"],
        occ_law={"cls": "exp", "rate": 0.1},
        not_occ_law={"cls": "delay", "time": 3},
        occ_effects={"f_fed_available_out": False},
    )

    # An event: a second façade over the same engine, self-hosted, watching the
    # system rather than naming targets. The COD3S Platform synthesises one per
    # study event AND one per indicator whose formula has more than one clause,
    # which is why it is here rather than in the refusals: it is an ordinary
    # part of the corpus, not an exotic corner.
    system.add_component(
        name="_ind_two_clauses",
        cls="ObjEvent",
        cond=[
            [{"attr": "f_fed_out", "obj": "A", "value": True}],
            [{"attr": "f_fed_out", "obj": "B", "value": True}],
        ],
        outer_logic="all",
        tempo_occ=2,
    )

    spec = system_spec(system)

    undeclarable = {
        "controller": system.add_component(
            name="CTRL",
            cls="ObjCtrl",
            controls_in=[{"name": "level"}],
            controls_out=[{"name": "go", "kind": "bool"}],
        ),
        "naming_function": system.add_component(
            cls="ObjFMDelay",
            fm_name="named",
            targets=["B"],
            failure_param=1,
            failure_effects={"f_fed_available_out": False},
            trans_name_prefix_fun=_naming,
        ),
        "degradation": system.add_component(
            cls="ObjDegMode",
            fm_name="deg",
            targets=["C"],
            states=[
                {
                    "name": "degraded",
                    "occ_law": {"cls": "exp", "rate": 0.1},
                    "effects": {"f_fed_available_out": False},
                }
            ],
        ),
        "tuple_keyed_metadata": system.add_component(
            name="TUPLES",
            cls="ModeTargetForDeclarationTest",
            # What a platform export attaches: its instance overrides are
            # indexed by ``(attribute, role)``, and every VALUE under those
            # keys serialises perfectly well.
            metadata={"instance_overrides": {("CS_E_KVPP", "logic_in"): "2"}},
        ),
    }

    yield {"system": system, "spec": spec, "undeclarable": undeclarable}


def test_a_flowless_object_no_longer_breaks_the_read(the_run):
    """The defect itself: ``component_spec`` on a mode returns a declaration.

    Stated on the object rather than on the system, because the failure was on
    the object: ``comp.flows_in`` on something that has none.
    """
    mode = the_run["system"].comp["A__single"]
    assert not hasattr(mode, "flows_in")
    spec = component_spec(mode)
    assert spec[COMPONENT_KIND_KEY] == COMPONENT_KIND_FAILURE_MODE
    assert spec["cls"] == "ObjFMDelay"


def test_a_standalone_mode_gets_its_own_entry(the_run):
    """The arbitration, at the system scale: an entry, not a skip."""
    components = the_run["spec"]["components"]
    assert set(components) == {
        "A",
        "B",
        "C",
        "A__single",
        "ABC__common",
        "C__engine",
        "_ind_two_clauses",
    }
    assert components["A__single"][COMPONENT_KIND_KEY] == COMPONENT_KIND_FAILURE_MODE
    assert components["A"].get(COMPONENT_KIND_KEY, "flow") == "flow"


def test_the_mode_declares_what_it_would_be_written_with(the_run):
    """Spelled the way ``ObjFMDelay(...)`` takes it, not the way the engine
    stores it: a declaration is what one would write."""
    spec = the_run["spec"]["components"]["A__single"]
    assert spec["fm_name"] == "single"
    assert spec["targets"] == ["A"]
    assert spec["failure_param"] == [4]
    assert spec["failure_effects"] == {"f_fed_available_out": False}
    assert spec["repair_cond"] is False
    # The engine's own vocabulary stays out of the document, or two spellings
    # of one field would be readable and only one of them buildable.
    assert not [key for key in spec if key.startswith(("occ_", "not_occ_"))]


def test_a_field_left_at_its_default_says_nothing(the_run):
    """Only what someone decided is written. Otherwise every mode declaration
    would carry the whole constructor, and a diff would say nothing."""
    spec = the_run["spec"]["components"]["A__single"]
    for silent in ("behaviour", "failure_state", "repair_state", "repair_effects"):
        assert silent not in spec, spec


def test_the_muscadet_spelling_of_an_effect_survives_the_read(the_run):
    """``ObjFailureMode*`` names its target's FLOWS with a regex where a
    ``cod3s.ObjFM`` names variables by their exact basename, and it keeps the
    declared dicts away from the engine during construction (the engine is
    handed empty ones, and would reject a flow name as an unknown variable),
    restoring them onto the public attributes afterwards.

    So this is read from the public attribute and gets ``{"f": False}``. Read a
    moment earlier, or from the engine's own storage, it would get ``{}`` and
    declare a mode with no effect at all -- a plant that looks reliable.
    """
    spec = the_run["spec"]["components"]["ABC__common"]
    assert spec["cls"] == "ObjFailureModeExp"
    assert spec["failure_effects"] == {"f": False}


def test_the_generic_two_state_engine_declares_itself_in_its_own_spelling(the_run):
    """``cod3s.ObjMode2S`` is not an ``ObjFM`` and takes none of its keys.

    The platform emits this class natively, and a guard written on the ObjFM
    family alone would have let it through to the same ``AttributeError``. Its
    declaration is spelled ``occ_*`` / ``not_occ_*``, because that is what its
    constructor takes -- and it carries its occurrence LAWS, which a façade
    derives from its class instead.
    """
    spec = the_run["spec"]["components"]["C__engine"]
    assert spec["cls"] == "ObjMode2S"
    assert spec["mode_name"] == "engine"
    assert spec["occ_law"] == {"cls": "exp", "rate": 0.1}
    assert spec["not_occ_law"] == {"cls": "delay", "time": 3.0}
    assert spec["occ_effects"] == {"f_fed_available_out": False}
    # The façade's spelling has no business on a class that does not take it.
    assert not [key for key in spec if key.startswith(("failure_", "repair_"))]


def test_a_truth_function_is_carried_by_its_name(the_run):
    """A callable a document cannot hold, and one of exactly two, so it is
    named rather than refused."""
    spec = the_run["spec"]["components"]["ABC__common"]
    assert spec["cond_outer_logic"] == "all"
    assert "cond_inner_logic" not in spec, "the default says nothing"


def test_the_dropping_of_inactive_orders_is_written_when_it_decided_something(the_run):
    """``drop_inactive_automata`` is consulted at construction and not kept, so
    it is written as what reproduces the automata that ARE there: the mode
    holds one per combination, which only ``False`` rebuilds."""
    spec = the_run["spec"]["components"]["ABC__common"]
    assert spec["drop_inactive_automata"] is False
    assert len(the_run["system"].comp["ABC__common"].automata_d) == 2**3 - 1
    assert "drop_inactive_automata" not in the_run["spec"]["components"]["A__single"]


def test_the_document_is_data(the_run):
    """Serialisable, or it is not a document two engines can share."""
    assert json.loads(json.dumps(the_run["spec"])) == the_run["spec"]


def test_it_validates_without_building(the_run):
    check_system_spec(the_run["spec"])


# ---------------------------------------------------------------------------
# What is refused, and by what message
# ---------------------------------------------------------------------------


def test_a_component_of_an_undeclared_kind_is_refused_by_name(the_run):
    """The class the ticket asks about in general: not an ObjFlow, not an
    ObjFM. Refused by a message naming the class, never by an AttributeError
    from inside a dict comprehension."""
    with pytest.raises(ComponentSpecError, match="ObjCtrl"):
        component_spec(the_run["undeclarable"]["controller"])


def test_a_live_object_inside_a_mode_is_refused_by_its_field(the_run):
    """A naming function rebuilds different automaton names, so a mode quietly
    missing one is a model whose indicators silently name nothing."""
    with pytest.raises(ComponentSpecError, match="trans_name_prefix_fun"):
        component_spec(the_run["undeclarable"]["naming_function"])


def test_an_event_declares_its_condition_and_its_comparison(the_run):
    """The one field that looked unreadable, and is not.

    ``ObjEvent`` keeps the COMPILED comparison and drops its spelling, which
    reads as a mode that cannot be written out. But the six functions it
    compiles to are ``operator`` module singletons, so identity gives the
    spelling back exactly -- and the same goes for the two tempos, which the
    façade turns into delay laws and which are read from there.
    """
    spec = the_run["spec"]["components"]["_ind_two_clauses"]
    assert spec["cls"] == "ObjEvent"
    assert spec["name"] == "_ind_two_clauses"
    assert spec["cond"] == [
        [{"attr": "f_fed_out", "obj": "A", "value": True}],
        [{"attr": "f_fed_out", "obj": "B", "value": True}],
    ]
    assert spec["outer_logic"] == "all"
    assert spec["tempo_occ"] == 2.0
    # Everything left at its default says nothing, the comparison included:
    # the platform never departs from `==` / True, so an ordinary event
    # declares its condition and little else.
    for silent in ("cond_operator", "cond_value", "inner_logic", "tempo_not_occ"):
        assert silent not in spec, spec


def test_a_mapping_keyed_by_anything_but_a_string_is_refused(the_run):
    """The hole a document falls through much later, closed where it opens.

    Every VALUE under a tuple key serialises, so the gate used to walk such a
    mapping through untouched: the declaration looked read and looked checked,
    and died at ``json.dumps`` on a TypeError naming a type and no field. A
    platform export reaches exactly this, its instance overrides being indexed
    by ``(attribute, role)``.
    """
    with pytest.raises(ComponentSpecError, match="instance_overrides"):
        component_spec(the_run["undeclarable"]["tuple_keyed_metadata"])


def test_a_multi_state_mode_is_refused_by_name(the_run):
    """``cod3s.ObjDegMode`` is not an ``ObjMode2S``: it holds a list of states
    rather than two, so none of the three vocabularies fits it. Pinned so that
    the day it gets a form, this test is what says so."""
    with pytest.raises(ComponentSpecError, match="ObjDegMode"):
        component_spec(the_run["undeclarable"]["degradation"])


def test_a_mode_naming_a_class_that_is_not_one_is_refused(the_run):
    spec = dict(the_run["spec"]["components"]["A__single"], cls="ObjFlow")
    with pytest.raises(ComponentSpecError, match="ObjFM"):
        muscadet.check_spec(spec)


def test_a_mode_naming_no_class_at_all_is_refused(the_run):
    spec = dict(the_run["spec"]["components"]["A__single"], cls="NoSuchMode")
    with pytest.raises(ComponentSpecError, match="NoSuchMode"):
        muscadet.check_spec(spec)


def test_an_unknown_key_is_refused_rather_than_dropped(the_run):
    """A key nothing builds is a declaration silently lost, which is the one
    thing this module refuses everywhere else."""
    spec = dict(the_run["spec"]["components"]["A__single"], failure_rate=3)
    with pytest.raises(ComponentSpecError, match="failure_rate"):
        muscadet.check_spec(spec)


def test_an_unnamed_truth_function_is_refused(the_run):
    spec = dict(the_run["spec"]["components"]["ABC__common"], cond_outer_logic="sum")
    with pytest.raises(ComponentSpecError, match="cond_outer_logic"):
        muscadet.check_spec(spec)


def test_a_mode_targeting_nothing_declared_is_refused_at_the_system_scale(the_run):
    """The target list is the one thing a mode says about the rest of the
    document, and nothing else checks it: unchecked, a typo built the whole
    system and failed inside cod3s naming a variable."""
    spec = json.loads(json.dumps(the_run["spec"]))
    spec["components"]["A__single"]["targets"] = ["Ghost"]
    with pytest.raises(SystemSpecError, match="Ghost"):
        check_system_spec(spec)


def test_an_unknown_kind_is_refused_by_number(the_run):
    spec = dict(the_run["spec"]["components"]["A"], **{COMPONENT_KIND_KEY: "sensor"})
    with pytest.raises(ComponentSpecError, match="sensor"):
        muscadet.check_spec(spec)


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
