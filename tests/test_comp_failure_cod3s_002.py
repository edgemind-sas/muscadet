"""Mirror of test_comp_failure_007.py using cod3s.ObjFMExp directly.

Exercises:
- Multi-target failure mode (CA, CB) with full-coverage combinations.
- Conditional failure mode (T1..T4) where firing requires the inputs c1
  and c2 to be currently fed.
- Repair effects on a different flow (f3) than the failure effects.

Notable translations from the muscadet wrapper:

1. ``failure_effects`` keys
   muscadet:  ``{"c1": False, "c2": False}``  -> targets ``flows_out["c1"].var_fed_available``
   cod3s:     ``{"c1_fed_available_out": False, "c2_fed_available_out": False}``

2. ``failure_cond={"c1": True, "c2": True}`` (muscadet shorthand)
   -> structured form with explicit input variable names:
       ``[[{"attr": "c1_fed_in", "value": True},
           {"attr": "c2_fed_in", "value": True}]]``
   The outer list is an OR (default), the inner list is an AND (default).
"""

import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():

    class CompC(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowOut", name="c1", var_prod_default=True))
            self.add_flow(dict(cls="FlowOut", name="c2", var_prod_default=True))

    class CompT(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="c1", logic="and"))
            self.add_flow(dict(cls="FlowIn", name="c2", logic="and"))
            self.add_flow(dict(cls="FlowOut", name="f1", var_prod_cond=["c1", "c2"]))
            self.add_flow(dict(cls="FlowOut", name="f2", var_prod_cond=["c1", "c2"]))
            self.add_flow(dict(cls="FlowOut", name="f3", var_prod_cond=["c1", "c2"]))

    system = muscadet.System(name="Sys")
    system.add_component(name="CA", cls="CompC")
    system.add_component(name="CB", cls="CompC")
    system.add_component(name="T1", cls="CompT")
    system.add_component(name="T2", cls="CompT")
    system.add_component(name="T3", cls="CompT")
    system.add_component(name="T4", cls="CompT")

    system.auto_connect("CA", ".*")

    # First FM: CA, CB common-cause failure on c1 and c2
    system.add_component(
        cls="ObjFMExp",
        fm_name="frun",
        targets=["CA", "CB"],
        failure_effects={
            "c1_fed_available_out": False,
            "c2_fed_available_out": False,
        },
        failure_param=[0.1, 0.1],
        repair_param=[0.1, 0.1],
    )

    # Second FM: T1..T4 with conditional firing on inputs c1 AND c2 fed,
    # failure stops f1/f2, repair sets f3 to False.
    system.add_component(
        cls="ObjFMExp",
        fm_name="frun",
        targets=["T1", "T2", "T3", "T4"],
        target_name="TXX",
        failure_effects={
            "f1_fed_available_out": False,
            "f2_fed_available_out": False,
        },
        failure_param=[0.1, 0, 0, 0.0001],
        # cod3s structured form: per-target condition built from variable names
        failure_cond=[
            [
                {"attr": "c1_fed_in", "value": True},
                {"attr": "c2_fed_in", "value": True},
            ]
        ],
        repair_effects={"f3_fed_available_out": False},
        repair_param=[0.0001, 0.001, 0.01, 0.1],
    )

    return system


def test_failure_modes_present(the_system):
    """The two failure-mode components are registered."""
    assert "CX__frun" in the_system.comp
    assert "TXX__frun" in the_system.comp
    # 4 targets => 2^4 - 1 = 15 non-empty combinations
    assert len(the_system.comp["TXX__frun"].automata_d) == 15


def test_initial_state(the_system):
    """All flows up; T*.f3 is False initially because the second FM's
    repair effect drives f3_fed_available_out=False at startup."""
    the_system.isimu_start()

    for cname in ["CA", "CB"]:
        for fname in ["c1", "c2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True

    for cname in ["T1", "T2", "T3", "T4"]:
        for fname in ["f1", "f2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is True
        # Same f3=False initial as in the muscadet wrapper test
        assert the_system.comp[cname].flows_out["f3"].var_fed.value() is False


def test_first_fm_propagates(the_system):
    """Firing CX__frun.occ__cc_12 (both CA and CB fail) cascades to all T*."""
    transitions = the_system.isimu_fireable_transitions()
    assert len(transitions) >= 1

    the_system.isimu_set_transition("CX__frun.occ__cc_12")
    trans_fired = the_system.isimu_step_forward()
    assert len(trans_fired) == 1

    for cname in ["CA", "CB"]:
        for fname in ["c1", "c2"]:
            assert the_system.comp[cname].flows_out[fname].var_fed.value() is False


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
