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

    system.add_component(name="C1a", cls="CompA")
    system.add_component(name="C1b", cls="CompA")

    system.add_component(name="C2", cls="CompB")
    system.add_component(name="C3", cls="CompB")
    system.add_component(name="C4", cls="CompB")
    system.add_component(name="C5", cls="CompB")

    system.auto_connect("C1a", ".*")

    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="frun",
        targets=["C1a", "C1b"],
        failure_effects={"f1": False, "f2": False},
        failure_param=[0.1, 0.2],
        repair_param=[0.1, 0.2],
    )

    return system


def test_system(the_system):
    # Run simulation
    the_system.isimu_start()

    for cname in ["C1a", "C1b"]:
        for fname in ["f1", "f2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    for cname in ["C2", "C3", "C4", "C5"]:
        for fname in ["f3", "f4", "f5"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    # Ensure transitions are valid before proceeding
    transitions = the_system.isimu_fireable_transitions()
    assert len(transitions) == 3
    assert transitions[0].end_time == float("inf")
    the_system.isimu_set_transition(0, date=10)
    trans_fired = the_system.isimu_step_forward()

    assert len(trans_fired) == 1

    tf = trans_fired[0]
    assert tf.bkd.distLaw().parameter(0) == 0.1
    assert tf.bkd.target(0).basename() == "occ__cc_1"
    assert tf.bkd.parent().name() == "C1X__frun"
    assert the_system.comp["C1a"].flows_out["f1"].var_fed.value() is False
    assert the_system.comp["C1a"].flows_out["f2"].var_fed.value() is False
    assert the_system.comp["C1b"].flows_out["f1"].var_fed.value() is True
    assert the_system.comp["C1b"].flows_out["f2"].var_fed.value() is True

    transitions = the_system.isimu_fireable_transitions()

    assert the_system.currentTime() == 10

    the_system.isimu_set_transition(0)
    the_system.isimu_step_forward()

    assert the_system.currentTime() == 10
    for cname in ["C1a", "C1b"]:
        for fname in ["f1", "f2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True


def test_delete(the_system):

    the_system.deleteSys()
    cod3s.terminate_session()
