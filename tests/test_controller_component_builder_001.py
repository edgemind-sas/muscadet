"""A controller is built by the engine that registered a builder for its kind.

Until every engine distribution carries one, muscadet keeps its own controller
class: no builder registered, and a model runs as it always did.
"""

import importlib.metadata

import cod3s
import pytest

import muscadet
from muscadet import declare
from muscadet.obj_ctrl import ObjCtrl as OwnCtrl

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


class EngineCtrl(OwnCtrl):
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
        super().__init__(name, controls_in=[{"name": "tank"}], **kwargs)


@pytest.fixture(autouse=True)
def only_the_engines_this_test_registers(monkeypatch):
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda group=None, **kwargs: []
    )
    muscadet.reset_engines()
    yield
    muscadet.reset_engines()


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


def test_the_controller_a_system_adds_is_declared_first():
    document = muscadet.controller.ObjCtrl(
        "Ctrl", controls_in=CONTROLS_IN, controls_out=CONTROLS_OUT
    ).declaration()
    assert declare.check_controller_spec(document) == "Ctrl"
    assert muscadet.system.DECLARED_COMPONENT_CLASSES["ObjCtrl"] is (
        muscadet.controller.ObjCtrl
    )


def test_without_a_builder_the_controller_is_muscadets_own():
    assert muscadet.find_component_builder("pycatshoo", "controller") is None
    system = muscadet.System("own_controller")
    try:
        controller = _add_controller(system)
        assert type(controller) is OwnCtrl
        assert system.comp["Ctrl"] is controller
    finally:
        _drop(system)


def test_an_engine_that_builds_no_controller_leaves_it_to_muscadet():
    muscadet.register_engine(name="pycatshoo", simulate=lambda spec: None)
    with pytest.raises(muscadet.ComponentBuilderMissingError, match="controller"):
        muscadet.component_builder("pycatshoo", "controller")
    system = muscadet.System("still_own_controller")
    try:
        assert type(_add_controller(system)) is OwnCtrl
    finally:
        _drop(system)


def test_a_registered_builder_receives_the_checked_declaration():
    builder = ControllerBuilder()
    muscadet.register_engine(
        name="pycatshoo",
        simulate=lambda spec: None,
        component_builders={"controller": builder},
    )
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
        # Read back on the kind it declares, not on its class.
        read = declare.component_spec(controller)
        assert read["kind"] == "controller" and read["cls"] == "ObjCtrl"
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


def test_the_two_builds_declare_the_same_document():
    system = muscadet.System("own_document")
    try:
        own = declare.system_spec(_and_a_label(system))
    finally:
        _drop(system)

    muscadet.register_engine(
        name="pycatshoo",
        simulate=lambda spec: None,
        component_builders={"controller": ControllerBuilder()},
    )
    system = muscadet.System("own_document")
    try:
        engine = declare.system_spec(_and_a_label(system))
    finally:
        _drop(system)

    # ``source_cls`` names the class that was read, which is the one thing
    # the two builds do not share in this test.
    assert own["components"]["Ctrl"].pop("source_cls") == "ObjCtrl"
    assert engine["components"]["Ctrl"].pop("source_cls") == "EngineCtrl"
    assert own == engine


def _and_a_label(system):
    _add_controller(system, label="Level switch")
    return system


def test_a_class_of_the_model_is_still_searched_by_name():
    builder = ControllerBuilder()
    muscadet.register_engine(
        name="pycatshoo",
        simulate=lambda spec: None,
        component_builders={"controller": builder},
    )
    system = muscadet.System("own_class")
    try:
        sensor = system.add_component(name="S", cls="ControllerBuilder001Sensor")
        assert type(sensor) is ControllerBuilder001Sensor
        assert list(sensor.controls_in) == ["tank"]
        assert builder.specs == []
    finally:
        _drop(system)


def a_hook_that_fails():
    raise RuntimeError("the native library could not be loaded")


def test_an_engine_whose_discovery_failed_is_not_hidden_behind_muscadets_class(
    monkeypatch,
):
    entry = importlib.metadata.EntryPoint(
        name="pycatshoo",
        value=f"{__name__}:a_hook_that_fails",
        group=muscadet.ENGINE_ENTRY_POINT_GROUP,
    )
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda group=None, **kwargs: (
            [entry] if group == muscadet.ENGINE_ENTRY_POINT_GROUP else []
        ),
    )
    muscadet.reset_engines()
    system = muscadet.System("broken_engine")
    try:
        with pytest.raises(muscadet.UnknownEngineError) as refusal:
            _add_controller(system)
        message = str(refusal.value)
        assert "Controller Ctrl" in message and "native library" in message
        assert "is installed" in message
        assert "Ctrl" not in system.comp
    finally:
        _drop(system)


def test_a_second_controller_of_the_same_name_is_refused_as_any_component():
    system = muscadet.System("twice")
    try:
        _add_controller(system)
        with pytest.warns(UserWarning, match="already exists"):
            assert _add_controller(system) is None
    finally:
        _drop(system)


def test_a_malformed_controller_is_refused_before_any_builder_sees_it():
    builder = ControllerBuilder()
    muscadet.register_engine(
        name="pycatshoo",
        simulate=lambda spec: None,
        component_builders={"controller": builder},
    )
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
