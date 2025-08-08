import muscadet

import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():

    class CompA(muscadet.ObjFlow):

        def add_flows(self, **kwargs):

            super().add_flows(**kwargs)

            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="f1",
                    var_prod_default=True,
                )
            )
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="f2",
                    var_prod_default=True,
                )
            )

    class CompB(muscadet.ObjFlow):

        def add_flows(self, **kwargs):

            super().add_flows(**kwargs)

            self.add_flow(
                dict(
                    cls="FlowIn",
                    name="f1",
                    logic="and",
                )
            )
            self.add_flow(
                dict(
                    cls="FlowIn",
                    name="f2",
                    logic="and",
                )
            )

            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="f3",
                    var_prod_cond=[
                        "f1",
                        "f2",
                    ],
                )
            )
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="f4",
                    var_prod_cond=[
                        "f1",
                        "f2",
                    ],
                )
            )
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="f5",
                    var_prod_cond=[
                        "f1",
                        "f2",
                    ],
                )
            )

    system = muscadet.System(name="Sys")

    # Create coin toss component
    system.add_component(name="C1", cls="CompA")
    system.add_component(name="C1b", cls="CompA")

    system.add_component(name="C2", cls="CompB")
    system.add_component(name="C3", cls="CompB")
    system.add_component(name="C4", cls="CompB")
    system.add_component(name="C5", cls="CompB")

    system.auto_connect("C1", ".*")

    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="frun",
        targets=["C1"],
        failure_effects={"f1": False, "f2": False},
        failure_param=1 / 10,
        # repair_param=0,
    )

    return system


def test_system(the_system):
    # Run simulation
    the_system.isimu_start()

    assert the_system.comp["C1"].flows_out["f1"].var_fed.value() is True
    assert the_system.comp["C1"].flows_out["f2"].var_fed.value() is True
    for cname in ["C2", "C3", "C4", "C5"]:
        for fname in ["f3", "f4", "f5"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    # Ensure transitions are valid before proceeding
    transitions = the_system.isimu_fireable_transitions()
    assert len(transitions) == 1
    assert transitions[0].end_time == float("inf")

    the_system.isimu_set_transition(0, date=10)
    trans_fired = the_system.isimu_step_forward()

    assert len(trans_fired) == 1

    transitions = the_system.isimu_fireable_transitions()

    assert the_system.currentTime() == 10

    the_system.isimu_set_transition(0)
    the_system.isimu_step_forward()

    assert the_system.currentTime() == 10

    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
