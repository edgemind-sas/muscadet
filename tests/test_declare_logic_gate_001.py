"""Logic gate declarations survive live read-back and reordered rebuilding."""

import json

import cod3s
import pytest

import muscadet
from muscadet import declare


def test_logic_gate_live_round_trip_and_reordered_dependency():
    system = muscadet.System("gate_round_trip")
    try:
        system.add_component(name="Source", cls="ObjFlow", partial_init=True)
        source = system.comp["Source"]
        source.add_flow_out(name="feed", var_prod_default=True)
        source.set_flows()
        cond = [[{"obj": "Source", "attr": "feed_fed_out", "value": True}]]
        gate = system.add_component(
            name="Gate",
            cls="ObjLogicGate",
            kind="k",
            k=1,
            cond=cond,
            out_elements=["g"],
        )
        declared = declare.system_spec(system)
        assert declared["components"]["Gate"]["kind"] == "logic_gate"
        assert declared["components"]["Gate"]["logic_kind"] == "k"
        assert declared["components"]["Gate"]["cond"] == cond
        json.dumps(declared, allow_nan=False)
        cond[0][0]["obj"] = "mutated"
        assert declare.component_spec(gate)["cond"][0][0]["obj"] == "Source"
    finally:
        system.deleteSys()
        cod3s.terminate_session()
    declared["components"] = {
        "Gate": declared["components"]["Gate"],
        "Source": declared["components"]["Source"],
    }
    rebuilt = declare.build_system(declared)
    try:
        rebuilt.isimu_start()
        assert rebuilt.comp["Gate"].result.value() is True
        rebuilt.isimu_stop()
        assert (
            declare.system_spec(rebuilt)["components"]["Gate"]
            == declared["components"]["Gate"]
        )
    finally:
        rebuilt.deleteSys()
        cod3s.terminate_session()


@pytest.mark.parametrize(
    "logic_kind,k,expected", [("or", None, False), ("and", None, True), ("k", 1, False)]
)
def test_empty_gate_declared_semantics(logic_kind, k, expected):
    spec = {
        "name": "Gate",
        "kind": "logic_gate",
        "cls": "ObjLogicGate",
        "logic_kind": logic_kind,
        "k": k,
        "cond": [],
        "out_elements": [],
    }
    system = muscadet.System("empty_gate")
    try:
        gate = declare.build_component(system, spec)
        assert gate.result.value() is expected
        assert declare.component_spec(gate)["cond"] == []
    finally:
        system.deleteSys()
        cod3s.terminate_session()


@pytest.mark.parametrize(
    "changes",
    [
        {"logic_kind": "xor"},
        {"logic_kind": "k", "k": 0},
        {"logic_kind": "k", "k": True},
        {"logic_kind": "k", "k": 1.5},
        {"cond": [[{"obj": "S", "attr": "feed", "ope": "!=", "value": True}]]},
        {"cond": [[{"attr": "feed", "value": True}]]},
        {"cond": "invalid"},
        {"out_elements": ["g", "g"]},
    ],
)
def test_invalid_portable_gate_is_named_before_build(changes):
    spec = {
        "name": "Gate",
        "kind": "logic_gate",
        "logic_kind": "or",
        "cond": [],
        "out_elements": [],
    }
    with pytest.raises(declare.ComponentSpecError, match="Gate"):
        declare.check_spec({**spec, **changes})


@pytest.mark.parametrize(
    "obj,attr", [("Missing", "feed_fed_out"), ("Source", "missing")]
)
def test_rejected_gate_leaves_system_reusable(obj, attr):
    system = muscadet.System("rejected_gate")
    try:
        source = system.add_component(name="Source", cls="ObjFlow", partial_init=True)
        source.add_flow_out(name="feed", var_prod_default=True)
        source.set_flows()
        spec = {
            "name": "Gate",
            "kind": "logic_gate",
            "cond": {"obj": obj, "attr": attr, "value": True},
        }
        with pytest.raises(declare.ComponentSpecError, match=f"Gate.*{obj}.*{attr}"):
            declare.build_component(system, spec)
        assert "Gate" not in system.comp
        spec["cond"] = {"obj": "Source", "attr": "feed_fed_out", "value": True}
        gate = declare.build_component(system, spec)
        system.isimu_start()
        assert gate.result.value() is True
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


@pytest.mark.parametrize("source_value", [False, True])
@pytest.mark.parametrize("compared", [True, False, 0, 1, 1.0, 2])
def test_gate_python_equality_round_trip(source_value, compared):
    system = muscadet.System("mixed_equality")
    try:
        source = system.add_component(name="Source", cls="ObjFlow", partial_init=True)
        source.add_flow_out(name="feed", var_prod_default=source_value)
        source.set_flows()
        spec = {
            "name": "Gate",
            "kind": "logic_gate",
            "cond": {"obj": "Source", "attr": "feed_fed_out", "value": compared},
        }
        gate = declare.build_component(system, spec)
        system.isimu_start()
        assert gate.result.value() is (source_value == compared)
        assert declare.component_spec(gate)["cond"] == [[spec["cond"]]]
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


@pytest.mark.parametrize("form", ["mapping", "flat", "compound"])
def test_gate_normalized_clause_round_trip(form):
    system = muscadet.System("clauses")
    try:
        for name, flag in [("S0", True), ("S1", False)]:
            source = system.add_component(name=name, cls="ObjFlow", partial_init=True)
            source.add_flow_out(name="feed", var_prod_default=flag)
            source.set_flows()
        yes = {"obj": "S0", "attr": "feed_fed_out", "value": True}
        no = {"obj": "S1", "attr": "feed_fed_out", "value": False}
        cond = (
            yes
            if form == "mapping"
            else (
                [yes, no]
                if form == "flat"
                else [[yes, no], [{"obj": "S1", "attr": "feed_fed_out", "value": True}]]
            )
        )
        spec = {
            "name": "Gate",
            "kind": "logic_gate",
            "logic_kind": "k",
            "k": 2 if form == "compound" else 1,
            "cond": cond,
        }
        gate = declare.build_component(system, spec)
        system.isimu_start()
        assert gate.result.value() is (form != "compound")
        normalized = (
            [[yes]] if form == "mapping" else [[yes, no]] if form == "flat" else cond
        )
        assert declare.component_spec(gate)["cond"] == normalized
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


@pytest.mark.parametrize("source_value", [0.0, 1.0, 2.0])
@pytest.mark.parametrize("compared", [False, True])
def test_gate_numeric_source_boolean_equality(source_value, compared):
    spec = {
        "version": 1,
        "name": "numeric_gate",
        "components": {
            "ControllerSlot": {
                "name": "Control",
                "kind": "controller",
                "cls": "ObjCtrl",
                "controls_in": [],
                "controls_out": [
                    {
                        "name": "reading",
                        "kind": "value",
                        "flows": [],
                        "level_default": source_value,
                        "gain_default": 1.0,
                    }
                ],
            },
            "Gate": {
                "name": "Gate",
                "kind": "logic_gate",
                "cond": {"obj": "Control", "attr": "reading_level", "value": compared},
            },
        },
        "connections": [],
        "indicators": [],
    }
    system = declare.build_system(spec)
    try:
        system.isimu_start()
        assert system.comp["Gate"].result.value() is (source_value == compared)
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_gate_reads_standalone_failure_and_repair_state():
    spec = {
        "version": 1,
        "name": "mode_gate",
        "components": {
            "S0": {
                "name": "S0",
                "flows": [{"cls": "FlowOut", "name": "feed", "var_prod_default": True}],
            },
            "ModeSlot": {
                "name": "S0__fault",
                "kind": "two_state_mode",
                "cls": "ObjFMDelay",
                "fm_name": "fault",
                "targets": ["S0"],
                "failure_param": [4],
                "repair_param": [2],
            },
            "Gate": {
                "name": "Gate",
                "kind": "logic_gate",
                "cond": {"obj": "S0__fault", "attr": "occ", "value": True},
            },
        },
        "connections": [],
        "indicators": [],
    }
    system = declare.build_system(spec)
    try:
        system.isimu_start()
        assert system.comp["Gate"].result.value() is False
        system.isimu_set_transition(0, date=4)
        system.isimu_step_forward()
        assert system.currentTime() == 4
        assert system.comp["Gate"].result.value() is True
        system.isimu_set_transition(0, date=6)
        system.isimu_step_forward()
        assert system.currentTime() == 6
        assert system.comp["Gate"].result.value() is False
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_delete():
    cod3s.terminate_session()
