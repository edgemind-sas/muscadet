import itertools

import cod3s
import pytest

import muscadet


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

    def trans_name_prefix_fun(target_set_idx, order_max, **kwargs):
        order = len(target_set_idx)
        if order < order_max:
            return "__" + "_".join([f"{i + 1:02}" for i in target_set_idx])
        else:
            return "__all"

    obj_frun = the_system.add_component(
        cls="ObjFMExp",
        fm_name="frun",
        targets=comp_name_list,
        failure_effects={"f1_fed_available_out": False, "f2_fed_available_out": False},
        failure_param=[1, 0, 1, 0, 1],
        trans_name_prefix_fun=trans_name_prefix_fun,
        drop_inactive_automata=True,
    )

    assert len(obj_frun.automata_d) == 16

    # The naming rule is a Python function, which no declaration can carry:
    # the automata are built, and the run is refused.
    with pytest.raises(
        muscadet.declare.ComponentSpecError,
        match="Failure mode C00X__frun, trans_name_prefix_fun: holds function",
    ):
        the_system.isimu_start()


def test_delete(the_system):

    the_system.deleteSys()
    cod3s.terminate_session()
