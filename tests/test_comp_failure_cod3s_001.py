"""Mirror of test_comp_failure_001.py but using cod3s.ObjFMExp directly,
i.e. without the muscadet.ObjFailureModeExp wrapper.

The wrapper's only added value over cod3s.ObjFMExp is rewriting
``failure_effects={"flow_name": value}`` into
``{flow.var_fed_available: value}`` by inspecting ``flows_out``. With cod3s
directly we have to address the underlying PyCATSHOO variable name
(``flow_name_fed_available_out``).

Goal: validate that cod3s.ObjFMExp wired against muscadet ObjFlow components
behaves the same as the muscadet wrapper, and document any practical
divergence (kept as comments in the test body).
"""

import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():

    class CompA(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowOut", name="f1", var_prod_default=True))
            self.add_flow(dict(cls="FlowOut", name="f2", var_prod_default=True))

    class CompB(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="f1", logic="and"))
            self.add_flow(dict(cls="FlowIn", name="f2", logic="and"))
            self.add_flow(dict(cls="FlowOut", name="f3", var_prod_cond=["f1", "f2"]))
            self.add_flow(dict(cls="FlowOut", name="f4", var_prod_cond=["f1", "f2"]))
            self.add_flow(dict(cls="FlowOut", name="f5", var_prod_cond=["f1", "f2"]))

    system = muscadet.System(name="Sys")
    system.add_component(name="C1", cls="CompA")
    system.add_component(name="C1b", cls="CompA")
    system.add_component(name="C2", cls="CompB")
    system.add_component(name="C3", cls="CompB")
    system.add_component(name="C4", cls="CompB")
    system.add_component(name="C5", cls="CompB")

    system.auto_connect("C1", ".*")

    # Same intent as test_comp_failure_001:
    #   failure_effects={"f1": False, "f2": False} on muscadet wrapper means
    #   "set var_fed_available_out=False on flows f1 and f2".
    # Here we have to address the variables explicitly.
    system.add_component(
        cls="ObjFMExp",
        fm_name="frun",
        targets=["C1"],
        failure_effects={
            "f1_fed_available_out": False,
            "f2_fed_available_out": False,
        },
        failure_param=1 / 10,
        repair_param=0.1,
    )

    return system


def test_initial_state(the_system):
    the_system.isimu_start()

    assert the_system.comp["C1"].flows_out["f1"].var_fed.value() is True
    assert the_system.comp["C1"].flows_out["f2"].var_fed.value() is True
    for cname in ["C2", "C3", "C4", "C5"]:
        for fname in ["f3", "f4", "f5"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True


def test_one_transition_fireable(the_system):
    """Only the failure transition of frun on C1 is fireable initially."""
    transitions = the_system.isimu_fireable_transitions()
    assert len(transitions) == 1
    assert transitions[0].end_time == float("inf")


def test_failure_propagates(the_system):
    """Firing the failure stops C1.f1/f2 and downstream f3/f4/f5."""
    the_system.isimu_set_transition(0, date=10)
    trans_fired = the_system.isimu_step_forward()
    assert len(trans_fired) == 1
    assert the_system.currentTime() == 10

    assert the_system.comp["C1"].flows_out["f1"].var_fed.value() is False
    assert the_system.comp["C1"].flows_out["f2"].var_fed.value() is False
    for cname in ["C2", "C3", "C4", "C5"]:
        for fname in ["f3", "f4", "f5"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is False


def test_no_advance_if_no_date(the_system):
    """Re-firing without a date does not advance the clock when nothing else is due."""
    the_system.isimu_set_transition(0)
    the_system.isimu_step_forward()
    assert the_system.currentTime() == 10

    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
