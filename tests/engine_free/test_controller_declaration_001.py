"""A controller is declared on a machine without an engine."""

import json

import pytest

from muscadet import declare
from muscadet.controller import ObjCtrl
from muscadet.ctrl_grammar import build_ctrl_node

EMIT = {"op": "compare", "input": "tank", "operator": ">=", "threshold": 8.0}


def _controller(**more):
    return ObjCtrl(
        "Ctrl",
        controls_in=[{"name": "tank"}],
        controls_out=[{"name": "alarm", "kind": "bool", "emit": dict(EMIT)}],
        **more,
    )


def test_a_controller_declares_what_its_reader_checks():
    document = _controller(label="High level").declaration()

    assert declare.check_controller_spec(document) == "Ctrl"
    assert declare.component_kind(document) == declare.COMPONENT_KIND_CONTROLLER
    assert document["cls"] == document["source_cls"] == "ObjCtrl"
    assert document["controls_in"] == [{"name": "tank"}]
    assert document["controls_out"][0]["emit"] == EMIT
    assert document["label"] == "High level"
    json.dumps(document, allow_nan=False)


def test_the_declaration_does_not_alias_what_it_was_given():
    entry = {"name": "alarm", "kind": "bool", "emit": dict(EMIT)}
    controller = ObjCtrl("Ctrl", controls_in=[{"name": "tank"}], controls_out=[entry])
    entry["emit"]["threshold"] = 0.0
    document = controller.declaration()
    document["controls_out"][0]["emit"]["threshold"] = 1.0

    assert controller.declaration()["controls_out"][0]["emit"] == EMIT


def test_what_is_left_unsaid_stays_out_of_the_document():
    document = ObjCtrl("Ctrl").declaration()

    assert {"label", "description", "metadata"}.isdisjoint(document)
    assert document["controls_in"] == [] and document["controls_out"] == []


def test_a_grammar_handed_as_nodes_is_written_as_data():
    node = build_ctrl_node("test", dict(EMIT))
    document = ObjCtrl(
        "Ctrl",
        controls_in=[{"name": "tank"}],
        controls_out=[{"name": "alarm", "kind": "bool", "emit": node}],
    ).declaration()

    json.dumps(document, allow_nan=False)
    assert build_ctrl_node("test", document["controls_out"][0]["emit"]) == node


def test_a_node_nested_in_a_mapping_is_written_as_data_too():
    node = build_ctrl_node("test", dict(EMIT))
    emit = {"op": "combine", "logic": "not", "operands": [node]}
    document = ObjCtrl(
        "Ctrl",
        controls_in=[{"name": "tank"}],
        controls_out=[{"name": "alarm", "kind": "bool", "emit": emit}],
    ).declaration()

    json.dumps(document, allow_nan=False)
    written = document["controls_out"][0]["emit"]
    assert build_ctrl_node("test", written) == build_ctrl_node("test", emit)


def test_a_key_no_controller_reads_is_refused_at_declaration():
    with pytest.raises(ValueError, match="Object Ctrl: ObjCtrl"):
        ObjCtrl("Ctrl", thresold=2.0)


def test_a_callable_emission_is_refused_at_declaration():
    with pytest.raises(ValueError, match="'alarm' emit"):
        ObjCtrl("Ctrl", controls_out=[{"name": "alarm", "emit": lambda: True}])
