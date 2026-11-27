"""A logic gate is declared on a machine without an engine."""

import json

import pytest

from muscadet import declare
from muscadet.logic_gate import ObjLogicGate

COND = [[{"obj": "A", "attr": "f_fed_out", "value": True}]]


def test_a_gate_declares_what_its_reader_checks():
    spec = ObjLogicGate("Voter", cond=COND, out_elements=["g"], kind="k", k=1)
    document = spec.declaration()

    assert declare.check_logic_gate_spec(document) == "Voter"
    assert document["kind"] == declare.COMPONENT_KIND_LOGIC_GATE
    assert document["cls"] == document["source_cls"] == "ObjLogicGate"
    assert document["logic_kind"] == "k" and document["k"] == 1
    json.dumps(document, allow_nan=False)


def test_the_declaration_does_not_alias_what_it_was_given():
    cond = [[{"obj": "A", "attr": "f_fed_out", "value": True}]]
    gate = ObjLogicGate("Voter", cond=cond)
    cond[0][0]["obj"] = "mutated"
    document = gate.declaration()
    document["cond"][0][0]["obj"] = "mutated too"

    assert gate.declaration()["cond"] == COND


def test_what_is_left_unsaid_stays_out_of_the_document():
    document = ObjLogicGate("Voter").declaration()

    assert {"label", "description", "metadata"}.isdisjoint(document)
    assert document["cond"] == [] and document["out_elements"] == []


def test_an_unknown_aggregation_is_refused_at_declaration():
    with pytest.raises(ValueError, match="unknown kind"):
        ObjLogicGate("Voter", kind="xor")
