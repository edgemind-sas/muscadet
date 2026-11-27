"""A gate is built by the engine that registered a builder for its kind.

Until every engine distribution carries one, muscadet keeps its own gate
class: no builder registered, and a model runs as it always did.
"""

import importlib.metadata

import cod3s
import pytest

import muscadet
from muscadet import declare
from muscadet.obj_logic import ObjLogicGate as OwnGate

COND = [[{"obj": "Source", "attr": "feed_fed_out", "value": True}]]


class EngineGate(OwnGate):
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


@pytest.fixture(autouse=True)
def only_the_engines_this_test_registers(monkeypatch):
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda group=None, **kwargs: []
    )
    muscadet.reset_engines()
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


def test_without_a_builder_the_gate_is_muscadets_own():
    assert muscadet.find_component_builder("pycatshoo", "logic_gate") is None
    system = _system_with_a_source("own_gate")
    try:
        gate = _add_gate(system)
        assert type(gate) is OwnGate
        assert system.comp["Gate"] is gate
    finally:
        _drop(system)


def test_an_engine_that_builds_no_gate_leaves_it_to_muscadet():
    muscadet.register_engine(name="pycatshoo", simulate=lambda spec: None)
    with pytest.raises(muscadet.ComponentBuilderMissingError, match="logic_gate"):
        muscadet.component_builder("pycatshoo", "logic_gate")
    system = _system_with_a_source("still_own_gate")
    try:
        assert type(_add_gate(system)) is OwnGate
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
