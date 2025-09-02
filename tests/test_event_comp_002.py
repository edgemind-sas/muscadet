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

    system = muscadet.System(name="Sys")

    system.add_component(name="CA", cls="CompC")
    system.add_component(name="CB", cls="CompC")

    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="frun",
        targets=["CA", "CB"],
        failure_effects={"c1": False, "c2": False},
        failure_param=[0.1, 0.1],
    )

    return system


def test_system(the_system):
    # the_system.traceVariable(".", 3)
    # the_system.traceAutomaton(".", 1)

    # CX__frun_obj = the_system.comp["CX__frun"]
    # TXX__frun_obj = the_system.comp["TXX__frun"]

    CA_comp = the_system.comp["CA"]
    CB_comp = the_system.comp["CB"]

    cond = [
        [
            {"var": CA_comp.flows_out["c1"].var_fed, "value": False},
            {"var": CA_comp.flows_out["c1"].var_fed, "value": False},
        ]
    ]
    C_NOK = the_system.add_component(
        cls="ObjEvent",
        name="CX_NOK",
        cond=cond,
        tempo_occ=10,
    )

    # Run simulation
    the_system.isimu_start()

    for cname in ["CA", "CB"]:
        for fname in ["c1", "c2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    assert C_NOK.state("occ").isActive() is False

    # Ensure transitions are valid before proceeding
    transitions = the_system.isimu_fireable_transitions()

    assert len(transitions) == 3

    the_system.isimu_set_transition("CX__frun.occ__cc_12")
    trans_fired = the_system.isimu_step_forward()
    assert the_system.comp["CA"].flows_out["c1"].var_fed.value() is False
    assert the_system.comp["CA"].flows_out["c2"].var_fed.value() is False
    assert the_system.comp["CB"].flows_out["c1"].var_fed.value() is False
    assert the_system.comp["CB"].flows_out["c2"].var_fed.value() is False

    transitions = the_system.isimu_fireable_transitions()
    assert C_NOK.state("occ").isActive() is False
    trans_fired = the_system.isimu_step_forward()
    assert len(trans_fired) == 1
    assert trans_fired[0].name == "occ"
    assert C_NOK.state("occ").isActive() is True
    assert the_system.currentTime() == 10


def test_delete(the_system):

    the_system.deleteSys()
    cod3s.terminate_session()
