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

    system.add_component(name="T1", cls="CompT")
    system.add_component(name="T2", cls="CompT")
    system.add_component(name="T3", cls="CompT")
    system.add_component(name="T4", cls="CompT")

    system.auto_connect("CA", ".*")

    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="frun",
        targets=["CA", "CB"],
        failure_effects={"c1": False, "c2": False},
        failure_param=[0.1, 0.1],
        repair_param=[0.1, 0.1],
    )

    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="frun",
        targets=["T1", "T2", "T3", "T4"],
        target_name="TXX",
        failure_effects={"f1": False, "f2": False},
        failure_param=[0.1, 0, 0, 0.0001],
        failure_cond={"c1": True, "c2": True},
        repair_effects={"f3": False},
        repair_param=[0.0001, 0.001, 0.01, 0.1],
    )

    return system


def test_system(the_system):
    # the_system.traceVariable(".", 3)
    # the_system.traceAutomaton(".", 1)

    # CX__frun_obj = the_system.comp["CX__frun"]
    # TXX__frun_obj = the_system.comp["TXX__frun"]

    assert "TXX__frun" in the_system.comp
    assert len(the_system.comp["TXX__frun"].automata_d) == 15

    # Run simulation
    the_system.isimu_start()

    for cname in ["CA", "CB"]:
        for fname in ["c1", "c2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    for cname in ["T1", "T2", "T3", "T4"]:
        for fname in ["f1", "f2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True
        for fname in ["f3"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is False

    # Ensure transitions are valid before proceeding
    transitions = the_system.isimu_fireable_transitions()
    assert len(transitions) == 8

    the_system.isimu_set_transition("CX__frun.occ__cc_12")
    trans_fired = the_system.isimu_step_forward()

    assert len(trans_fired) == 1
    tf = trans_fired[0]
    assert tf.end_time == 0
    assert tf.bkd.distLaw().parameter(0) == 0.1
    assert tf.bkd.target(0).basename() == "occ__cc_12"
    assert tf.bkd.parent().name() == "CX__frun"
    assert the_system.comp["CA"].flows_out["c1"].var_fed.value() is False
    assert the_system.comp["CA"].flows_out["c2"].var_fed.value() is False
    assert the_system.comp["CB"].flows_out["c1"].var_fed.value() is False
    assert the_system.comp["CB"].flows_out["c2"].var_fed.value() is False

    transitions = the_system.isimu_fireable_transitions()

    assert the_system.currentTime() == 0
    assert len(transitions) == 3
    assert all([tr.comp_name == "CX__frun" for tr in transitions])
    the_system.isimu_set_transition("CX__frun.occ__cc_2")
    trans_fired = the_system.isimu_step_forward()

    transitions = the_system.isimu_fireable_transitions()

    assert len(transitions) == 3
    assert all([tr.comp_name == "CX__frun" for tr in transitions])

    the_system.isimu_set_transition("CX__frun.rep__cc_12")
    trans_fired = the_system.isimu_step_forward()
    transitions = the_system.isimu_fireable_transitions()

    assert the_system.currentTime() == 0
    for cname in ["CB"]:
        for fname in ["c1", "c2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is False


def test_delete(the_system):

    the_system.deleteSys()
    cod3s.terminate_session()
