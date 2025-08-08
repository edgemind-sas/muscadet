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
                    name="a1",
                    var_prod_default=True,
                )
            )
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="a2",
                    var_prod_default=True,
                )
            )

    class CompB(muscadet.ObjFlow):

        def add_flows(self, **kwargs):

            super().add_flows(**kwargs)

            self.add_flow(
                dict(
                    cls="FlowIn",
                    name="a1",
                    logic="and",
                )
            )

            self.add_flow(
                dict(
                    cls="FlowIn",
                    name="a2",
                    logic="and",
                )
            )

            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="b1",
                    var_prod_cond=[
                        "a1",
                    ],
                )
            )
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="b2",
                    var_prod_default=True,
                )
            )
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="b3",
                    var_prod_cond=[
                        "a2",
                        "b2",
                    ],
                )
            )

    system = muscadet.System(name="003")

    system.add_component(name="CA", cls="CompA")
    system.add_component(name="CB", cls="CompB")

    system.auto_connect("CA", "CB")

    # if system.name() == "003":
    #     __import__("ipdb").set_trace()

    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="frun",
        targets="CB",
        failure_effects={"b2": False},
        failure_param=1 / 10,
        #        repair_param=[],
    )

    # system.add_component(
    #     cls="ObjFailureModeExp",
    #     fm_name="frun",
    #     targets=["T1", "T2", "T3", "T4"],
    #     target_name="TXX",
    #     failure_effects={"f1": False, "f2": False},
    #     failure_param=[0.1, 0.01, 0.001, 0.0001],
    #     failure_cond={"c1": True, "c2": True},
    #     repair_param=[0.0001, 0.001, 0.01, 0.1],
    # )

    return system


def test_system(the_system):

    # Run simulation
    the_system.isimu_start()

    for cname in ["CA"]:
        for fname in ["a1", "a2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    for cname in ["CB"]:
        for fname in ["b1", "b2", "b3"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    transitions = the_system.isimu_fireable_transitions()
    assert len(transitions) == 1
    the_system.isimu_set_transition("CB__frun.frun__occ")
    trans_fired = the_system.isimu_step_forward()

    for cname in ["CA"]:
        for fname in ["a1", "a2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    for cname in ["CB"]:
        for fname in ["b2", "b3"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is False
        for fname in ["b1"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    the_system.isimu_set_transition("CB__frun.frun__rep")
    trans_fired = the_system.isimu_step_forward()

    for cname in ["CA"]:
        for fname in ["a1", "a2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    for cname in ["CB"]:
        for fname in ["b1", "b2", "b3"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True


def test_delete(the_system):

    the_system.deleteSys()
    cod3s.terminate_session()
