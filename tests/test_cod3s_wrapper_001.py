import muscadet
import cod3s
import pytest
import kb_project
from muscadet.cod3s_wrapper import (
    KBMuscadet,
    InterfaceMuscadetTemplate,
    InterfaceFlowIn,
    InterfaceFlowOut,
    ObjFlowClass,
    ObjFlowInstance,
    ComponentMuscadetClass,
)


def test_interface_flow_in():
    """Test the InterfaceFlowIn class."""
    interface = InterfaceFlowIn(name="test_flow_in")
    assert interface.name == "test_flow_in"
    assert interface.port_type == "input"
    assert interface.logic == "or"


def test_interface_flow_out():
    """Test the InterfaceFlowOut class."""
    interface = InterfaceFlowOut(name="test_flow_out")
    assert interface.name == "test_flow_out"
    assert interface.port_type == "output"
    assert interface.logic == []
    assert interface.logic_inner_mode == "or"
    assert interface.negate is False


def test_obj_flow_class():
    """Test the ObjFlowClass class."""
    obj_flow_class = ObjFlowClass(class_name="TestFlow")

    flow_in = InterfaceFlowIn(name="in_flow")
    flow_out = InterfaceFlowOut(name="out_flow")
    obj_flow_class.add_interface(flow_in)
    obj_flow_class.add_interface(flow_out)

    assert obj_flow_class.class_name == "TestFlow"
    assert obj_flow_class.class_name_bkd == {"pycatshoo": "ObjFlow"}
    assert len(obj_flow_class.interfaces) == 2
    assert obj_flow_class.interfaces[0].name == "in_flow"
    assert obj_flow_class.interfaces[1].name == "out_flow"


def test_obj_flow_instance():
    """Test the ObjFlowInstance class."""

    obj_flow_class = ObjFlowClass(class_name="TestFlow")

    obj_flow = obj_flow_class.create_instance("TestFlowInstance")

    assert obj_flow.name == "TestFlowInstance"
    assert obj_flow.class_name == "TestFlow"
    assert obj_flow.interfaces == []
    assert obj_flow.class_name_bkd == {"pycatshoo": "ObjFlow"}
    assert obj_flow.__class__.__name__ == "ObjFlowInstance"


def test_kb_muscadet():
    """Test the KBMuscadet class."""
    kb = KBMuscadet(name="TestKB")
    assert kb.name == "TestKB"
    assert kb.component_classes == {}

    # Test adding a component class to the KB
    flow_class = ObjFlowClass(class_name="TestFlow")
    kb.component_classes["TestFlow"] = flow_class
    assert "TestFlow" in kb.component_classes
    assert kb.component_classes["TestFlow"].class_name == "TestFlow"


# @pytest.fixture(scope="module")
# def the_system():
#     system = muscadet.System(name="Sys")

#     # Create coin toss component
#     system.add_component(name="Start", cls="Start")
#     system.add_component(name="T1", cls="Task", duration_mean=3)
#     system.add_component(name="T2", cls="Task", duration_mean=5)
#     system.add_component(name="End", cls="Task")

#     system.auto_connect("Start", "T1|T2")
#     system.auto_connect("T1|T2", "End")

#     return system


# def test_system(the_system):
#     # Run simulation
#     the_system.isimu_start()

#     assert the_system.comp["Start"].flows_out["flow"].var_fed.value() == True
#     assert the_system.comp["Start"].flows_out["flow"].var_fed_available.value() == True
#     # Ensure transitions are valid before proceeding
#     transitions = the_system.isimu_fireable_transitions()

#     the_system.isimu_set_transition(1, date=None, state_index=0)
#     trans_fired = the_system.isimu_step_forward()

#     assert len(trans_fired) == 1

#     assert the_system.comp["Start"].flows_out["flow"].var_fed.value() == False
#     assert the_system.comp["Start"].flows_out["flow"].var_fed_available.value() == False


# def test_delete(the_system):
#     the_system.deleteSys()
