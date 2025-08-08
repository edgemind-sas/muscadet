import muscadet
import cod3s
import pytest
import kb_project


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="Sys")

    # Create coin toss component
    system.add_component(name="Start", cls="Start")
    system.add_component(name="T1", cls="Task", duration_mean=3)
    system.add_component(name="T2", cls="Task", duration_mean=5)
    system.add_component(name="End", cls="Task")

    system.auto_connect("Start", "T1|T2")
    system.auto_connect("T1|T2", "End")

    return system


def test_system(the_system):
    # Run simulation
    the_system.isimu_start()

    assert the_system.comp["Start"].flows_out["flow"].var_fed.value() == True
    assert the_system.comp["Start"].flows_out["flow"].var_fed_available.value() == True
    # Ensure transitions are valid before proceeding
    transitions = the_system.isimu_fireable_transitions()

    the_system.isimu_set_transition(1, date=None, state_index=0)
    trans_fired = the_system.isimu_step_forward()

    assert len(trans_fired) == 1

    assert the_system.comp["Start"].flows_out["flow"].var_fed.value() == False
    assert the_system.comp["Start"].flows_out["flow"].var_fed_available.value() == False


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
