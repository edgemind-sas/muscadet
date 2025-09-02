import muscadet

import cod3s
import pytest
import itertools


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

    system = muscadet.System(name="Sys")

    return system


def test_system(the_system):

    nb_comp = 5
    comp_name_list = []
    for i in range(nb_comp):
        comp_name = f"C{i:03}"
        comp_name_list.append(comp_name)
        the_system.add_component(name=comp_name, cls="CompA")

    obj_frun = the_system.add_component(
        cls="ObjFailureModeExp",
        fm_name="frun",
        targets=comp_name_list,
        failure_effects={"f1": False, "f2": False},
        failure_param=[1, 0, 0, 0, 1],
        repair_param=[1, 0, 0, 0, 1],
        trans_name_prefix="__{target_binary}",
        drop_inactive_automata=True,
    )

    assert len(obj_frun.automata_d) == 6

    # Run simulation
    the_system.isimu_start()

    for cname in comp_name_list:
        for fname in ["f1", "f2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    # Ensure transitions are valid before proceeding
    transitions = the_system.isimu_fireable_transitions()
    assert len(transitions) == 6
    assert transitions[0].name.endswith("00001")
    assert transitions[1].name.endswith("00010")
    assert transitions[-1].name.endswith("11111")


def test_delete(the_system):

    the_system.deleteSys()
    cod3s.terminate_session()
