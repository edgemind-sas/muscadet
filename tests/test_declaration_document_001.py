"""What muscadet reads back is a document, and that is a property, not a habit.

``muscadet.declare`` refuses, section by section, what a mapping cannot carry:
a Python callable on a flow, an engine handle in an occurrence law, a mapping
keyed by anything but a string in the metadata. Each of those refusals fires
where ONE value is read and names the declaration that carried it, which is
exactly what a modeller needs to fix it.

None of them says anything about the DOCUMENT. They close the holes they were
written for, one at a time, and the sections do not even all go through the
same gate: the indicators are dumped by cod3s, the connections are read off the
engine, and a section added later goes through whichever gate its author picks.
So the guarantee the seam actually needs -- *a declaration muscadet returned
survives* ``json.dumps`` -- was the sum of what the parts happened to refuse,
and nobody was watching that sum.

The measured cost of that gap: a mapping keyed by ``("Etat_O", "prod_init")``
walked through untouched, because every VALUE under a tuple key serialises
perfectly well. The declaration looked read, looked checked, and died at the
moment it was written, on a ``TypeError`` naming a type and no field, far from
the declaration that caused it. A COD3S Platform import reaches exactly that,
its instance overrides being indexed by ``(attribute, role)``.

This module pins the guarantee itself rather than that one case:

1. :func:`~muscadet.declare._document_fault` and ``json`` AGREE, both ways
   round, over a table of shapes. That is what makes the gate a statement
   about JSON and not a second opinion on it.
2. The two read-back entry points, ``component_spec`` and ``system_spec``,
   carry the gate, and name the path rather than the type.
3. A model imported from a platform export goes through it end to end, which
   is where the defect was measured.

Point 3 also measures what is NOT this module's to fix: the importer writes its
audit trail under ``(name, role)`` keys, and giving those a portable spelling
is a separate arbitration with a test of its own. What is pinned here is that
the guarantee holds the moment they have one, whichever one is chosen.
"""

import copy
import json
import math
import os

import cod3s
import pytest

import muscadet
from muscadet.declare import (
    ComponentSpecError,
    SystemSpecError,
    _document_fault,
    _is_serialisable,
    component_spec,
    system_spec,
)
from muscadet.engine import system_declaration
from muscadet.importers.cod3s_platform import system_from_export

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "minimal_export.json")

#: The audit trail the COD3S Platform importer attaches to an imported
#: instance, in the very shape it was measured in: one entry per flow-role
#: couple, keyed by the couple itself.
MEASURED_OVERRIDES = {("Etat_O", "prod_init"): True}


def _survives_json(value):
    """True when ``json`` both writes ``value`` and gives it back unchanged.

    Unchanged is half the question and the half a bare ``json.dumps`` misses.
    ``json`` writes ``{1: "a"}`` happily and reads back ``{"1": "a"}``: nothing
    failed, and what came out is not what went in. A declaration is a versioned
    exchange format, so a key that only survives as something else has not
    survived.
    """
    try:
        blob = json.dumps(value)
    except (TypeError, ValueError):
        return False
    return json.loads(blob) == value


# ---------------------------------------------------------------------------
# 1. The gate and ``json`` agree, both ways round
# ---------------------------------------------------------------------------

#: Shapes a declaration legitimately holds. ``inf`` is here on purpose: a
#: capacity's ``fill_rate`` is routinely infinite and ``json`` writes it, so a
#: gate refusing it would refuse the default spelling of "whatever the producer
#: delivers".
DOCUMENTS = {
    "a scalar": 3,
    "the rate of a capacity nothing throttles": math.inf,
    "an empty mapping": {},
    "an empty list": [],
    "a flow declaration": {"flows": [{"cls": "FlowOut", "name": "f"}]},
    "a mapping under a mapping": {"a": {"b": {"c": [1, 2.5, True, None]}}},
    "a name that is not ascii": {"vapeur à 60°C": 1.0},
    "a component name that looks like a number": {"3": 1},
}

#: Shapes that are not documents, each with the path the refusal has to name.
#: The tuple key is the one that was measured; the others are the same defect
#: in the spellings a producer reaches by accident.
NOT_DOCUMENTS = {
    "the platform's own instance overrides": (
        {"metadata": {"instance_overrides": MEASURED_OVERRIDES}},
        "$.metadata.instance_overrides",
    ),
    "an integer key, which json coerces rather than refuses": (
        {"shares": {1: 0.5}},
        "$.shares",
    ),
    "a boolean key, coerced the same way": (
        {"by_state": {True: "on"}},
        "$.by_state",
    ),
    "a null key, coerced the same way": (
        {"by_state": {None: "unknown"}},
        "$.by_state",
    ),
    "a key buried under a list": (
        {"flows": [{"name": "f"}, {"derating": {("mode", "H2"): 0.5}}]},
        "$.flows[1].derating",
    ),
    "a set, which json refuses outright": (
        {"targets": {"a", "b"}},
        "$.targets",
    ),
    "a callable, the live object the module was written for": (
        {"allocation_fun": len},
        "$.allocation_fun",
    ),
}


@pytest.mark.parametrize("what", sorted(DOCUMENTS))
def test_what_the_gate_accepts_json_writes_and_gives_back(what):
    """One direction: the gate never refuses something ``json`` would carry."""
    value = DOCUMENTS[what]
    assert _document_fault(value, "$") is None
    assert _survives_json(value), what


@pytest.mark.parametrize("what", sorted(NOT_DOCUMENTS))
def test_what_the_gate_refuses_json_does_not_carry(what):
    """The other direction, and the one that makes the gate honest.

    A gate stricter than ``json`` would refuse legitimate declarations for a
    reason nobody could act on. Every shape refused below is required to fail
    ``json`` or to come back changed, so the refusal is a statement about the
    format and not a taste.
    """
    value, _path = NOT_DOCUMENTS[what]
    assert _document_fault(value, "$") is not None
    assert not _survives_json(value), what


@pytest.mark.parametrize("what", sorted(NOT_DOCUMENTS))
def test_the_refusal_names_the_path_and_not_the_type(what):
    """The whole point of refusing early: ``json.dumps`` names a type and
    nothing else, so the reader is left to find which of a hundred components
    carried it. The fault names where it is."""
    value, path = NOT_DOCUMENTS[what]
    fault = _document_fault(value, "$")
    assert path in fault, fault


def test_a_key_is_reported_by_its_own_value():
    """A path says which mapping, the key says which entry of it."""
    fault = _document_fault(
        {"metadata": {"instance_overrides": MEASURED_OVERRIDES}}, "$"
    )
    assert "('Etat_O', 'prod_init')" in fault
    assert "tuple" in fault


@pytest.mark.parametrize(
    "value", [*DOCUMENTS.values(), *(v for v, _ in NOT_DOCUMENTS.values())]
)
def test_the_two_walks_of_the_module_answer_the_same_question(value):
    """``_is_serialisable`` and ``_document_fault`` ask the same thing.

    There are two of them because a path costs something to build and the bool
    answer is asked for field by field, on every declaration read back. That is
    a fair trade only while they agree: the day one accepts what the other
    refuses, a declaration passes the field-by-field gate and dies at the one
    that sees the whole of it, or the reverse -- a defect of exactly the family
    this module exists to close, hiding inside the closing.
    """
    assert _is_serialisable(value) == (_document_fault(value, "$") is None)


# ---------------------------------------------------------------------------
# 2. The two read-back entry points carry the gate
# ---------------------------------------------------------------------------
def _system_with_measured_metadata(name):
    """A component whose metadata is keyed the way the platform keys it."""
    system = muscadet.System(name=name)
    comp = system.add_component(cls="ObjFlow", name="Rail_1")
    comp.metadata["instance_overrides"] = dict(MEASURED_OVERRIDES)
    return system


def test_component_spec_refuses_it_rather_than_returning_it():
    """``component_spec`` used to return this spec without complaint."""
    system = _system_with_measured_metadata("DocGateComp")
    try:
        with pytest.raises(ComponentSpecError) as error:
            component_spec(system.comp["Rail_1"])

        message = str(error.value)
        assert "Rail_1" in message
        assert "metadata.instance_overrides" in message
        assert "('Etat_O', 'prod_init')" in message
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_system_spec_refuses_it_too_and_says_which_component():
    """The system scale is where a platform export is read, and where a reader
    needs the component named: a hundred components carry the same section."""
    system = _system_with_measured_metadata("DocGateSys")
    try:
        with pytest.raises((ComponentSpecError, SystemSpecError)) as error:
            system_spec(system)

        message = str(error.value)
        assert "Rail_1" in message
        assert "metadata.instance_overrides" in message
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_a_declaration_that_came_out_is_a_document():
    """The claim, on a model that has something to declare: whatever
    ``system_spec`` returns, ``json`` writes it and gives it back equal."""
    system = muscadet.System(name="DocGateOk")
    try:
        system.add_component(
            cls="ObjFlow",
            name="Rail_1",
            metadata={"instance_overrides": {"Etat_O": {"prod_init": True}}},
        )
        spec = system_spec(system)
        assert _survives_json(spec)
        assert spec["components"]["Rail_1"]["metadata"] == {
            "instance_overrides": {"Etat_O": {"prod_init": True}}
        }
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ---------------------------------------------------------------------------
# 3. The platform path, where the defect was measured
# ---------------------------------------------------------------------------
def _payload_with_an_instance_override():
    """The minimal export, with one attribute the importer folds in.

    ``role="prod_init"`` on an output is the plainest of the override roles,
    and it is one of the two the platform fixture was measured on. The
    importer indexes it by ``(name, role)`` and copies that index into the
    component's metadata, which is the whole of the defect.
    """
    with open(_FIXTURE) as handle:
        payload = copy.deepcopy(json.load(handle))
    components = payload["model"]["elements"]["components"]
    components["id-source"]["attributes"].append(
        {"name": "out_a", "role": "prod_init", "value": True}
    )
    return payload


@pytest.fixture(scope="module")
def imported():
    """One imported platform model, kept for the whole module.

    PyCATSHOO forbids more than one live system per process, so the tests above
    tear their own down and this one is the last standing. ``test_delete``
    closes it.
    """
    return system_from_export(_payload_with_an_instance_override())


def test_an_imported_model_never_declares_a_late_json_failure(imported):
    """The claim, on the path the defect was measured on, stated so that it
    stays a claim once the producer changes.

    ``system_declaration`` is the document every engine receives. It came back
    without complaint and failed at ``json.dumps`` with ``keys must be str,
    int, float, bool or None, not tuple`` -- a message naming a type, no
    component and no field.

    Two outcomes are legitimate and the test admits both, because WHICH one
    this model gets is the producer's business and not this module's: refused
    here with the path, or a document. What is refused is the third one, the
    only one that used to happen. Measured on 2026-09-09: refused, the importer
    keying its audit trail by ``(name, role)``. Giving that couple a portable
    spelling is an arbitration of its own, with its own ticket -- pinning the
    defective shape here would make this module fail the day it is settled, and
    say nothing useful when it did.
    """
    try:
        document = system_declaration(imported)
    except (ComponentSpecError, SystemSpecError) as refusal:
        message = str(refusal)
        assert "Source1" in message, message
        assert "metadata.instance_overrides" in message, message
    else:
        assert _survives_json(document)


def test_a_portable_audit_trail_is_all_that_stands_between_it_and_a_document(
    imported,
):
    """What is left to do, and the proof that it is all that is left.

    The audit trail is rewritten here into A portable spelling -- one entry per
    override, in the platform's own ``name`` / ``role`` / ``value`` vocabulary,
    the shape the sibling ``attributes_initial`` already has. WHICH spelling
    the importer settles on is not decided here, so nothing below reads the
    shape: what is measured is that the declaration comes out, survives the
    round trip and still carries the override, the moment the keys are strings.
    No second defect is hiding behind this one.
    """
    for comp in imported.comp.values():
        for key in ("instance_overrides", "capacity_overrides"):
            bag = comp.metadata.get(key)
            if isinstance(bag, dict) and any(not isinstance(name, str) for name in bag):
                comp.metadata[key] = [
                    {"name": name, "role": role, "value": value}
                    for (name, role), value in bag.items()
                ]

    document = system_declaration(imported)
    assert _survives_json(document)

    trail = json.dumps(document["components"]["Source1"]["metadata"])
    assert "out_a" in trail and "prod_init" in trail, trail


def test_delete(imported):
    imported.deleteSys()
    cod3s.terminate_session()
