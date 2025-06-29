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
    SystemMuscadet,
)


@pytest.fixture(scope="module")
def kb():
    """Create a KBMuscadet populated with ObjFlowClass representing components in kb_project."""
    kb = KBMuscadet(name="ProjectGuard")

    # Create Start component class
    start_class = ObjFlowClass(
        class_name="Start",
    )
    flow_out = InterfaceFlowOut(name="flow")
    start_class.add_interface(flow_out)
    kb.component_classes["Start"] = start_class

    # Create Task component class
    task_class = ObjFlowClass(class_name="Task")
    flow_in = InterfaceFlowIn(name="flow")
    flow_out = InterfaceFlowOut(name="flow")
    task_class.add_interface(flow_in)
    task_class.add_interface(flow_out)
    kb.component_classes["Task"] = task_class

    # Create End component class
    end_class = ObjFlowClass(class_name="End")
    flow_in = InterfaceFlowIn(name="flow")
    end_class.add_interface(flow_in)
    kb.component_classes["End"] = end_class

    return kb


def test_kb_muscadet(kb):
    """Test the KBMuscadet fixture with project components."""
    assert kb.name == "ProjectGuard"
    assert "Start" in kb.component_classes
    assert "Task" in kb.component_classes
    assert "End" in kb.component_classes

    # Test Start component
    start_class = kb.component_classes["Start"]
    assert start_class.class_name == "Start"
    assert len(start_class.interfaces) == 1
    assert start_class.interfaces[0].name == "flow"
    assert start_class.interfaces[0].port_type == "output"

    # Test Task component
    task_class = kb.component_classes["Task"]
    assert task_class.class_name == "Task"
    assert len(task_class.interfaces) == 2

    # Test End component
    end_class = kb.component_classes["End"]
    assert end_class.class_name == "End"
    assert len(end_class.interfaces) == 1
    assert end_class.interfaces[0].name == "flow"
    assert end_class.interfaces[0].port_type == "input"


def test_system_muscadet(kb):
    """Test the SystemMuscadet class."""

    # Create a basic system
    system = SystemMuscadet(
        name="AProject",
        kb_name="ProjectGuard",
    )

    system.add_component(kb, "Start", "S")
    system.add_component(kb, "Task", "T1")
    system.add_component(kb, "Task", "T2")
    system.add_component(kb, "End", "E")

    # Test basic properties
    assert system.name == "AProject"
    assert system.kb_name == "ProjectGuard"
    assert system.class_name_bkd == {"pycatshoo": "muscadet.System"}

    # Test that the system can be initialized without errors
    assert isinstance(system, cod3s.System)
    assert len(system.components) == 4

    system_pyc = system.to_bkd_pycatshoo()


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
