"""A controller is built by the engine that registered a builder for its kind.

muscadet declares a controller and carries no class that runs one. Without a
builder, adding a controller is refused, and the refusal says which of the
three things is missing: an engine, an engine whose discovery failed, or an
engine that builds no controller.

A class a model derives from ``muscadet.ObjCtrl`` is a declaration too: it is
found by its name, writes its document, and the engine builds it.
"""

import importlib.metadata

import cod3s
import pytest
from cod3s_pycatshoo.controller import ObjCtrl as AdapterCtrl

import muscadet
from muscadet import declare

CONTROLS_IN = [{"name": "tank"}]
CONTROLS_OUT = [
    {
        "name": "fill",
        "kind": "bool",
        "emit": {
            "op": "compare",
            "input": "tank",
            "operator": "<=",
            "threshold": 2.0,
        },
    }
]


class EngineCtrl(AdapterCtrl):
    """What an engine distribution carries: a controller class of its own."""


class ControllerBuilder:
    """Records what muscadet hands over, and builds on the system it is given."""

    def __init__(self):
        self.specs = []

    def __call__(self, system, spec):
        self.specs.append(spec)
        return EngineCtrl(
            spec["name"],
            label=spec.get("label"),
            controls_in=spec["controls_in"],
            controls_out=spec["controls_out"],
        )


class ControllerBuilder001Sensor(muscadet.ObjCtrl):
    """A class of the model's own, declaring its interfaces itself."""

    def __init__(self, name, **kwargs):
        super().__init__(
            name, controls_in=CONTROLS_IN, controls_out=CONTROLS_OUT, **kwargs
        )


class ControllerBuilder001Gauge(ControllerBuilder001Sensor):
    """A class derived from a class of the model: found by its name as well."""


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


def _register(builder):
    muscadet.register_engine(
        name="pycatshoo",
        simulate=lambda spec: None,
        component_builders={"controller": builder},
    )


def _add_controller(system, **more):
    return system.add_component(
        name="Ctrl",
        cls="ObjCtrl",
        controls_in=CONTROLS_IN,
        controls_out=CONTROLS_OUT,
        **more,
    )


def _drop(system):
    system.deleteSys()
    cod3s.terminate_session()


def test_the_controller_muscadet_exports_is_a_declaration():
    assert muscadet.ObjCtrl is muscadet.controller.ObjCtrl
    assert not hasattr(muscadet.obj_ctrl, "ObjCtrl")
    assert not hasattr(muscadet.obj_ctrl, "CtrlSignalOut")
    document = muscadet.ObjCtrl(
        "Ctrl", controls_in=CONTROLS_IN, controls_out=CONTROLS_OUT
    ).declaration()
    assert declare.check_controller_spec(document) == "Ctrl"


def test_the_grammar_is_still_read_from_the_module_it_was_first_read_from():
    from muscadet import ctrl_grammar, obj_ctrl

    assert obj_ctrl.CtrlBand is ctrl_grammar.CtrlBand
    assert obj_ctrl.build_ctrl_node is ctrl_grammar.build_ctrl_node
    assert obj_ctrl.AGGREGATION_CROSSING_CAP == ctrl_grammar.AGGREGATION_CROSSING_CAP


def test_without_an_engine_a_controller_is_refused_by_name():
    assert muscadet.find_component_builder("pycatshoo", "controller") is None
    system = muscadet.System("no_engine")
    try:
        with pytest.raises(muscadet.UnknownEngineError) as refusal:
            _add_controller(system)
        message = str(refusal.value)
        assert "Controller Ctrl" in message
        assert "Install the engine distribution beside muscadet" in message
        assert "Ctrl" not in system.comp
    finally:
        _drop(system)


def test_an_engine_that_builds_no_controller_is_named_as_such():
    muscadet.register_engine(name="pycatshoo", simulate=lambda spec: None)
    system = muscadet.System("no_controller_builder")
    try:
        with pytest.raises(muscadet.ComponentBuilderMissingError) as refusal:
            _add_controller(system)
        message = str(refusal.value)
        assert "controller" in message
        assert "Upgrade the engine distribution" in message
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
    system = muscadet.System("broken_engine")
    try:
        with pytest.raises(muscadet.UnknownEngineError) as refusal:
            _add_controller(system)
        message = str(refusal.value)
        assert "Controller Ctrl" in message and "native library" in message
        assert "is installed" in message and "Install the" not in message
    finally:
        _drop(system)


def test_a_registered_builder_receives_the_checked_declaration():
    builder = ControllerBuilder()
    _register(builder)
    system = muscadet.System("engine_controller")
    try:
        controller = _add_controller(system, label="Level switch")
        assert type(controller) is EngineCtrl
        assert system.comp["Ctrl"] is controller
        (spec,) = builder.specs
        assert spec["kind"] == "controller" and spec["cls"] == "ObjCtrl"
        assert spec["controls_in"] == CONTROLS_IN
        assert spec["controls_out"] == CONTROLS_OUT
        assert spec["label"] == "Level switch"
        # Read back on the kind it declares, not on its class, and under the
        # class the model wrote rather than the one the engine built.
        read = declare.component_spec(controller)
        assert read["kind"] == "controller" and read["cls"] == "ObjCtrl"
        assert read["source_cls"] == "ObjCtrl"
        assert read["controls_out"][0]["emit"] == CONTROLS_OUT[0]["emit"]
        declared = declare.system_spec(system)
    finally:
        _drop(system)

    rebuilt = declare.build_system(declared)
    try:
        assert type(rebuilt.comp["Ctrl"]) is EngineCtrl
        assert len(builder.specs) == 2
        assert declare.system_spec(rebuilt) == declared
    finally:
        _drop(rebuilt)


@pytest.mark.parametrize(
    "cls", ["ControllerBuilder001Sensor", "ControllerBuilder001Gauge"]
)
def test_a_class_of_the_model_declares_and_the_engine_builds(cls):
    builder = ControllerBuilder()
    _register(builder)
    system = muscadet.System("own_class")
    try:
        sensor = system.add_component(name="S", cls=cls, label="Sensor")
        assert type(sensor) is EngineCtrl
        (spec,) = builder.specs
        assert spec["cls"] == "ObjCtrl" and spec["source_cls"] == cls
        assert spec["controls_in"] == CONTROLS_IN
        assert list(sensor.controls_in) == ["tank"]
        assert list(sensor.controls_out) == ["fill"]
        # Read back under the class the model wrote.
        assert declare.component_spec(sensor)["source_cls"] == cls
    finally:
        _drop(system)


def test_a_class_that_changes_a_behaviour_is_refused_where_it_is_defined():
    with pytest.raises(TypeError) as refusal:

        class ControllerBuilder001Tight(muscadet.ObjCtrl):
            CROSSING_CAP = 3

    message = str(refusal.value)
    assert "ControllerBuilder001Tight" in message and "CROSSING_CAP" in message
    assert "cod3s_pycatshoo.controller.ObjCtrl" in message

    with pytest.raises(TypeError, match="add_control_out"):

        class ControllerBuilder001Loud(muscadet.ObjCtrl):
            def add_control_out(self, name, **params):
                return None


def test_a_document_naming_a_live_class_reads_back_as_what_the_engine_built():
    """Only a declaration class is kept as the provenance of a rebuild."""
    _register(ControllerBuilder())
    system = muscadet.System("live_provenance")
    try:
        document = muscadet.ObjCtrl("Ctrl", controls_in=CONTROLS_IN).declaration()
        document["source_cls"] = "SomeLiveSubclass"
        controller = declare.build_component(system, document)
        assert declare.component_spec(controller)["source_cls"] == "EngineCtrl"
    finally:
        _drop(system)


def test_a_class_of_the_model_refuses_what_a_controller_refuses():
    builder = ControllerBuilder()
    _register(builder)
    system = muscadet.System("own_class_refusal")
    try:
        with pytest.raises(ValueError, match="ControllerBuilder001Sensor"):
            system.add_component(
                name="S", cls="ControllerBuilder001Sensor", thresold=2.0
            )
        assert builder.specs == []
    finally:
        _drop(system)


def test_a_name_no_class_carries_is_left_to_the_live_classes():
    from muscadet.system import declared_component_class

    assert declared_component_class("ObjCtrl") is muscadet.ObjCtrl
    assert declared_component_class("ObjLogicGate") is muscadet.ObjLogicGate
    assert declared_component_class("ObjFlow") is None
    assert declared_component_class(None) is None


def test_a_second_controller_of_the_same_name_is_refused_as_any_component():
    _register(ControllerBuilder())
    system = muscadet.System("twice")
    try:
        _add_controller(system)
        with pytest.warns(UserWarning, match="already exists"):
            assert _add_controller(system) is None
    finally:
        _drop(system)


def test_a_malformed_controller_is_refused_before_any_builder_sees_it():
    builder = ControllerBuilder()
    _register(builder)
    system = muscadet.System("malformed")
    try:
        with pytest.raises(ValueError, match="thresold"):
            system.add_component(name="Ctrl", cls="ObjCtrl", thresold=2.0)
        with pytest.raises(ValueError, match="emit"):
            system.add_component(
                name="Ctrl",
                cls="ObjCtrl",
                controls_out=[{"name": "fill", "emit": lambda: True}],
            )
        assert builder.specs == []
        assert "Ctrl" not in system.comp
    finally:
        _drop(system)


def test_delete():
    cod3s.terminate_session()
