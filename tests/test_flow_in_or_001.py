import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():

    class Source(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out(name="f1", var_prod_default=True)

    class TargetOr(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="f1", logic="or")

    system = muscadet.System(name="Sys")
    system.add_component(name="S1", cls="Source")
    system.add_component(name="S2", cls="Source")
    system.add_component(name="T", cls="TargetOr")

    system.auto_connect("S.*", "T")

    # S1 fails at t=5, S2 fails at t=10
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
    """Both sources ok -> or logic -> fed=True"""
    the_system.isimu_start()
    assert the_system.comp["T"].flows_in["f1"].var_fed.value() is True


def test_one_source_fails(the_system):
    """S1 fails at t=5: S2 still ok -> or logic -> still True"""
    the_system.isimu_set_transition(0, date=5)
    the_system.isimu_step_forward()

    assert the_system.currentTime() == 5
    assert the_system.comp["T"].flows_in["f1"].var_fed.value() is True


def test_all_sources_fail(the_system):
    """S2 fails at t=10: no source left -> or logic -> False"""
    # Transition 1 is S2 failure (transition 0 is S1 repair)
    the_system.isimu_set_transition(1, date=10)
    the_system.isimu_step_forward()

    assert the_system.currentTime() == 10
    assert the_system.comp["T"].flows_in["f1"].var_fed.value() is False

    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
