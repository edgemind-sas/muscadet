"""cod3s.ObjFMDelay used directly on a muscadet system.

Validates that the deterministic delay variant of ObjFM (no statistical
draws) integrates with muscadet ObjFlow components, mirroring the
``add_delay_failure_mode`` shortcut used in the k/n tests.
"""

import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():

    class Source(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out(name="f1", var_prod_default=True)

    class Target(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="f1", logic="and")

    system = muscadet.System(name="Sys")
    system.add_component(name="S", cls="Source")
    system.add_component(name="T", cls="Target")
    system.auto_connect("S", "T")

    # Direct cod3s.ObjFMDelay on muscadet ObjFlow:
    # - failure fires after 5 time units, drives f1_fed_available_out=False
    # - repair fires after 10 time units, restores it
    system.add_component(
        cls="ObjFMDelay",
        fm_name="fail",
        targets=["S"],
        failure_effects={"f1_fed_available_out": False},
        failure_param=5,
        repair_effects={"f1_fed_available_out": True},
        repair_param=10,
    )

    return system


def test_initial_state(the_system):
    the_system.isimu_start()
    assert the_system.comp["S"].flows_out["f1"].var_fed.value() is True
    assert the_system.comp["T"].flows_in["f1"].var_fed.value() is True


def test_failure_fires(the_system):
    """First fireable transition is the failure delay."""
    transitions = the_system.isimu_fireable_transitions()
    fireable = [t for t in transitions if t]
    assert len(fireable) == 1
    assert fireable[0].end_time == 5.0

    the_system.isimu_set_transition(0, date=5)
    the_system.isimu_step_forward()

    assert the_system.currentTime() == 5
    assert the_system.comp["S"].flows_out["f1"].var_fed.value() is False
    assert the_system.comp["T"].flows_in["f1"].var_fed.value() is False


def test_repair_fires(the_system):
    """Repair takes 10 units after failure."""
    transitions = the_system.isimu_fireable_transitions()
    fireable = [t for t in transitions if t]
    assert len(fireable) == 1
    assert fireable[0].end_time == 15.0  # 5 (failure time) + 10 (repair time)

    the_system.isimu_set_transition(0, date=15)
    the_system.isimu_step_forward()

    assert the_system.currentTime() == 15
    assert the_system.comp["S"].flows_out["f1"].var_fed.value() is True
    assert the_system.comp["T"].flows_in["f1"].var_fed.value() is True

    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
