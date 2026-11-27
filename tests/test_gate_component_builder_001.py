"""A gate is built by the engine that registered a builder for its kind.

muscadet declares a gate and carries no class that evaluates one. Without a
builder, adding a gate is refused, and the refusal says which of the three
things is missing: an engine, an engine whose discovery failed, or an engine
that builds no gate.
"""

import importlib.metadata

import cod3s
import pytest
from cod3s_pycatshoo.logic_gate import ObjLogicGate as AdapterGate

import muscadet
from muscadet import declare

COND = [[{"obj": "Source", "attr": "feed_fed_out", "value": True}]]


class EngineGate(AdapterGate):
    """What an engine distribution carries: a gate class of its own."""


class GateBuilder:
    """Records what muscadet hands over, and builds on the system it is given."""

    def __init__(self):
        self.specs = []

    def __call__(self, system, spec):
        self.specs.append(spec)
        return EngineGate(
            spec["name"],
            cond=spec["cond"],
            out_elements=spec["out_elements"],
            kind=spec["logic_kind"],
            k=spec["k"],
        )


def a_hook_that_fails():
    raise RuntimeError("the native library could not be loaded")


def _advertise(monkeypatch, *entries):
    def entry_points(group=None, **kwargs):
        return list(entries) if group == muscadet.ENGINE_ENTRY_POINT_GROUP else []

    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)
    muscadet.reset_engines()


@pytest.fixture(autouse=True)
def only_the_engines_this_test_registers(monkeypatch):
    _advertise(monkeypatch)
    yield
    muscadet.reset_engines()


def _system_with_a_source(name):
    system = muscadet.System(name)
    system.add_component(name="Source", cls="ObjFlow", partial_init=True)
    source = system.comp["Source"]
    source.add_flow_out(name="feed", var_prod_default=True)
    source.set_flows()
    return system


def _add_gate(system):
    return system.add_component(
        name="Gate", cls="ObjLogicGate", kind="or", cond=COND, out_elements=["g"]
    )


def _drop(system):
    system.deleteSys()
    cod3s.terminate_session()


def test_the_gate_muscadet_exports_is_a_declaration():
    assert muscadet.ObjLogicGate is muscadet.logic_gate.ObjLogicGate
    assert not hasattr(muscadet.obj_logic, "ObjLogicGate")
    document = muscadet.ObjLogicGate("Gate", cond=COND, kind="or").declaration()
    assert declare.check_logic_gate_spec(document) == "Gate"


def test_without_an_engine_a_gate_is_refused_by_name():
    assert muscadet.find_component_builder("pycatshoo", "logic_gate") is None
    system = _system_with_a_source("no_engine")
    try:
        with pytest.raises(muscadet.UnknownEngineError) as refusal:
            _add_gate(system)
        message = str(refusal.value)
        assert "Logic gate Gate" in message and "cod3s-pycatshoo" in message
        assert "Gate" not in system.comp
    finally:
        _drop(system)


def test_an_engine_that_builds_no_gate_is_named_as_such():
    muscadet.register_engine(name="pycatshoo", simulate=lambda spec: None)
    assert muscadet.find_component_builder("pycatshoo", "logic_gate") is None
    system = _system_with_a_source("no_gate_builder")
    try:
        with pytest.raises(muscadet.ComponentBuilderMissingError, match="logic_gate"):
            _add_gate(system)
    finally:
        _drop(system)


def test_an_engine_whose_discovery_failed_is_not_an_absent_engine(monkeypatch):
    _advertise(
        monkeypatch,
        importlib.metadata.EntryPoint(
            name="pycatshoo",
            value=f"{__name__}:a_hook_that_fails",
            group=muscadet.ENGINE_ENTRY_POINT_GROUP,
        ),
    )
    with pytest.raises(muscadet.UnknownEngineError, match="native library"):
        muscadet.find_component_builder("pycatshoo", "logic_gate")
    system = _system_with_a_source("broken_engine")
    try:
        with pytest.raises(muscadet.UnknownEngineError, match="native library"):
            _add_gate(system)
    finally:
        _drop(system)


def test_a_registered_builder_receives_the_checked_declaration():
    builder = GateBuilder()
    muscadet.register_engine(
        name="pycatshoo",
        simulate=lambda spec: None,
        component_builders={"logic_gate": builder},
    )
    system = _system_with_a_source("engine_gate")
    try:
        gate = _add_gate(system)
        assert type(gate) is EngineGate
        assert system.comp["Gate"] is gate
        (spec,) = builder.specs
        assert spec["kind"] == "logic_gate" and spec["cond"] == COND
        assert spec["logic_kind"] == "or" and spec["out_elements"] == ["g"]
        # Read back on the kind it declares, not on its class.
        read = declare.component_spec(gate)
        assert read["kind"] == "logic_gate" and read["cond"] == COND
        declared = declare.system_spec(system)
    finally:
        _drop(system)

    rebuilt = declare.build_system(declared)
    try:
        assert type(rebuilt.comp["Gate"]) is EngineGate
        assert len(builder.specs) == 2
        rebuilt.isimu_start()
        assert rebuilt.comp["Gate"].result.value() is True
        rebuilt.isimu_stop()
    finally:
        _drop(rebuilt)


def test_a_second_gate_of_the_same_name_is_refused_as_any_component():
    muscadet.register_engine(
        name="pycatshoo",
        simulate=lambda spec: None,
        component_builders={"logic_gate": GateBuilder()},
    )
    system = _system_with_a_source("twice")
    try:
        _add_gate(system)
        with pytest.warns(UserWarning, match="already exists"):
            assert _add_gate(system) is None
    finally:
        _drop(system)


def test_a_malformed_gate_is_refused_before_any_builder_sees_it():
    builder = GateBuilder()
    muscadet.register_engine(
        name="pycatshoo",
        simulate=lambda spec: None,
        component_builders={"logic_gate": builder},
    )
    system = _system_with_a_source("malformed")
    try:
        with pytest.raises(ValueError):
            system.add_component(name="Gate", cls="ObjLogicGate", kind="xor")
        assert builder.specs == []
    finally:
        _drop(system)


def test_delete():
    cod3s.terminate_session()
