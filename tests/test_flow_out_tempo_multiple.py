"""Independent timed outputs must coexist in the same native component."""

import cod3s
import pytest

import muscadet


@pytest.fixture(scope="module")
def the_system():
    class MultipleTimedOutputs(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            for name, delay in (("first", 10), ("second", 20)):
                self.add_flow(
                    dict(
                        cls="FlowOutTempo",
                        name=name,
                        var_prod_default=True,
                        occ_enable_flow={"cls": "delay", "time": delay},
                    )
                )

    system = muscadet.System(name="MultipleTempo")
    deleted = False

    def cleanup():
        nonlocal deleted
        if not deleted:
            system.deleteSys()
            cod3s.terminate_session()
            deleted = True

    try:
        system.add_component(name="timed", cls="MultipleTimedOutputs")
        yield system, cleanup
    finally:
        cleanup()


def test_outputs_have_independent_delays(the_system):
    system, _ = the_system
    flows = system.comp["timed"].flows_out
    system.isimu_start()
    assert [f.var_fed.value() for f in flows.values()] == [False, False]
    system.isimu_step_to(9.9)
    assert [f.var_fed.value() for f in flows.values()] == [False, False]
    system.isimu_step_to(10)
    assert [f.var_fed.value() for f in flows.values()] == [True, False]
    system.isimu_step_to(20)
    assert [f.var_fed.value() for f in flows.values()] == [True, True]
    system.isimu_stop()


def test_custom_names_are_preserved():
    flow = muscadet.FlowOutTempo(
        name="custom", state_enable_name="ready", state_disable_name="waiting"
    )
    assert flow.state_enable_name == "ready"
    assert flow.state_disable_name == "waiting"


def test_delete(the_system):
    _, cleanup = the_system
    cleanup()
