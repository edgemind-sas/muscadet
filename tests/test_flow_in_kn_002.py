"""k/n logic with availability channel explicitly connected.

Validates that `var_fed_available` reference is also evaluated with
`sumValue() >= k` (symmetric with `var_in`), not `andValue()`.
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

    # Wire the availability channel manually (auto_connect with
    # available_connect=True is currently broken — out of scope here).
    for src in ["S1", "S2", "S3"]:
        system.connect_flow(
            source=src,
            target="T",
            flow_name="f1_available",
            check_authorization=False,
        )

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


def test_initial_availability_connected(the_system):
    """Availability channel is wired: 3 connections, all True."""
    the_system.isimu_start()

    flow_in = the_system.comp["T"].flows_in["f1"]
    assert flow_in.var_in.nbCnx() == 3
    assert flow_in.var_fed_available.nbCnx() == 3
    assert flow_in.var_fed_available.sumValue(0) == 3
    assert flow_in.var_fed.value() is True


def test_one_unavailable(the_system):
    """1 unavailable: avail count 2 >= k=2 -> True."""
    the_system.isimu_set_transition(0, date=5)
    the_system.isimu_step_forward()

    flow_in = the_system.comp["T"].flows_in["f1"]
    assert flow_in.var_fed_available.sumValue(0) == 2
    assert flow_in.var_in.sumValue(0) == 2
    assert flow_in.var_fed.value() is True


def test_two_unavailable(the_system):
    """2 unavailable: avail count 1 < k=2 -> False."""
    the_system.isimu_set_transition(1, date=10)
    the_system.isimu_step_forward()

    flow_in = the_system.comp["T"].flows_in["f1"]
    assert flow_in.var_fed_available.sumValue(0) == 1
    assert flow_in.var_in.sumValue(0) == 1
    assert flow_in.var_fed.value() is False

    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
