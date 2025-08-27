import muscadet

import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():

    class CompC(muscadet.ObjFlow):

        def add_flows(self, **kwargs):

            super().add_flows(**kwargs)

            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="c1",
                    var_prod_default=True,
                )
            )
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="c2",
                    var_prod_default=True,
                )
            )

    class CompT(muscadet.ObjFlow):

        def add_flows(self, **kwargs):

            super().add_flows(**kwargs)

            self.add_flow(
                dict(
                    cls="FlowIn",
                    name="c1",
                    logic="and",
                )
            )
            self.add_flow(
                dict(
                    cls="FlowIn",
                    name="c2",
                    logic="and",
                )
            )

            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="f1",
                    var_prod_cond=[
                        "c1",
                        "c2",
                    ],
                )
            )
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="f2",
                    var_prod_cond=[
                        "c1",
                        "c2",
                    ],
                )
            )
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="f3",
                    var_prod_cond=[
                        "c1",
                        "c2",
                    ],
                )
            )

    system = muscadet.System(name="Sys")

    system.add_component(name="CA", cls="CompC")
    system.add_component(name="CB", cls="CompC")

    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="frun_1",
        targets=["CA", "CB"],
        failure_effects={".*": False},
        failure_param=[0.1, 0.1],
    )

    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="frun_2",
        targets=["CA", "CB"],
        failure_effects={"c2": False},
        failure_param=[0.1, 0.1],
    )

    return system


def test_system(the_system):

    assert "CX__frun_1" in the_system.comp
    assert len(the_system.comp["CX__frun_1"].automata()) == 3
    assert "CX__frun_2" in the_system.comp
    assert len(the_system.comp["CX__frun_2"].automata()) == 3

    # Run simulation
    the_system.isimu_start()

    for cname in ["CA", "CB"]:
        for fname in ["c1", "c2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    # Ensure transitions are valid before proceeding
    transitions = the_system.isimu_fireable_transitions()
    assert len(transitions) == 6
    # __import__("ipdb").set_trace()
    the_system.isimu_set_transition("CX__frun_1.frun_1__cc_1__occ")
    trans_fired = the_system.isimu_step_forward()

    for cname in ["CA"]:
        for fname in ["c1", "c2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is False
    for cname in ["CB"]:
        for fname in ["c1", "c2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    the_system.isimu_set_transition("CX__frun_2.frun_2__cc_2__occ")
    trans_fired = the_system.isimu_step_forward()

    for cname in ["CA"]:
        for fname in ["c1", "c2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is False
    for cname in ["CB"]:
        for fname in ["c2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is False


def test_delete(the_system):

    the_system.deleteSys()
    cod3s.terminate_session()
