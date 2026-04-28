import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():

    class Source(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out(name="f1", var_prod_default=True)

    class TargetK2(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="f1", logic=2)

    system = muscadet.System(name="Sys")
    system.add_component(name="S1", cls="Source")
    system.add_component(name="S2", cls="Source")
    system.add_component(name="S3", cls="Source")
    system.add_component(name="T", cls="TargetK2")

    system.auto_connect("S.*", "T")

    # Add deterministic failure modes on S1 and S2
    system.comp["S1"].add_delay_failure_mode(
        name="fail_S1",
        failure_cond="f1_fed_out",
        failure_time=5,
        failure_effects=[("f1_fed_available_out", False)],
        repair_time=100,
    )
    system.comp["S2"].add_delay_failure_mode(
        name="fail_S2",
        failure_cond="f1_fed_out",
        failure_time=10,
        failure_effects=[("f1_fed_available_out", False)],
        repair_time=100,
    )

    return system


def test_all_sources_ok(the_system):
    """3 sources connected, k=2: all True -> fed=True"""
    the_system.isimu_start()
    assert the_system.comp["T"].flows_in["f1"].var_fed.value() is True


def test_one_source_fails(the_system):
    """S1 fails at t=5: 2 remaining >= k=2 -> still True"""
    # Transition 0 is S1 failure (end_time=5)
    the_system.isimu_set_transition(0, date=5)
    the_system.isimu_step_forward()

    assert the_system.currentTime() == 5
    # S1 failed, S2 and S3 still ok -> 2 >= 2 -> True
    assert the_system.comp["T"].flows_in["f1"].var_fed.value() is True


def test_two_sources_fail(the_system):
    """S2 fails at t=10: only S3 left, 1 < k=2 -> fed=False"""
    # After S1 failure: transition 0 is S1 repair, transition 1 is S2 failure
    the_system.isimu_set_transition(1, date=10)
    the_system.isimu_step_forward()

    assert the_system.currentTime() == 10
    # S1 and S2 failed, only S3 ok -> 1 < 2 -> False
    assert the_system.comp["T"].flows_in["f1"].var_fed.value() is False

    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
