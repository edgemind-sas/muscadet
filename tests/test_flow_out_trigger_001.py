import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():
    """Test FlowOutOnTrigger: S2 activates when S1 fails."""

    flow_name = "is_ok"

    class Source(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out(name=flow_name, var_prod_default=True)

    class SourceTrigger(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out_on_trigger(
                name=flow_name,
                trigger_time_up=0,
                trigger_time_down=0,
                trigger_logic="and",
                var_prod_default=True,
            )

    class Target(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name=flow_name, logic="or")

    system = muscadet.System(name="Sys")
    system.add_component(name="S1", cls="Source")
    system.add_component(name="S2", cls="SourceTrigger")
    system.add_component(name="T", cls="Target")

    # Connect trigger: S2 activates when S1 stops producing
    system.connect_trigger("S1", "S2", flow_name)
    system.auto_connect("S1", "T")
    system.connect_flow("S2", "T", flow_name)

    # S1 fails at t=5, repairs at t=10
    system.comp["S1"].add_delay_failure_mode(
        name="fail_S1",
        failure_cond="is_ok_fed_out",
        failure_time=5,
        failure_effects=[("is_ok_fed_available_out", False)],
        repair_time=5,
    )

    return system


def test_initial_state(the_system):
    """S1 ok, S2 should be inactive (trigger not activated)."""
    the_system.isimu_start()

    # S1 is producing
    assert the_system.comp["S1"].flows_out["is_ok"].var_fed.value() is True
    # S2 trigger is NOT activated (S1 is still feeding)
    assert the_system.comp["S2"].flows_out["is_ok"].var_fed.value() is False
    # Target is fed (via S1)
    assert the_system.comp["T"].flows_in["is_ok"].var_fed.value() is True


def test_s1_fails_s2_activates(the_system):
    """When S1 fails, S2 trigger should activate."""
    # Fire S1 failure at t=5
    the_system.isimu_set_transition(0, date=5)
    the_system.isimu_step_forward()

    assert the_system.currentTime() == 5

    # S1 has failed
    assert the_system.comp["S1"].flows_out["is_ok"].var_fed.value() is False

    # Fire trigger_up transition (instantaneous, also at t=5)
    the_system.isimu_set_transition(1)
    the_system.isimu_step_forward()

    # S2 should now be active (trigger activated because S1 stopped)
    assert the_system.comp["S2"].flows_out["is_ok"].var_fed.value() is True
    # Target should still be fed (via S2)
    assert the_system.comp["T"].flows_in["is_ok"].var_fed.value() is True

    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
