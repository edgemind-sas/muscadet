"""Availability-gate reset — parity (byte-identical legacy behaviour).

The ``var_fed_available_out_reset`` field defaults to ``True``. On that default
the gate is built with ``setReinitialized(True)`` — exactly the historical
hard-wired call — so a reset (default) flow must behave identically to legacy:
the availability gate reverts to its init value at every step (it does NOT
remember a momentary write).

This locks the parity gate of the primitive:
  * the model field default is True (static / spec-level assertion), and
  * the runtime gate reverts after a momentary set + retract (behavioural).
"""

import muscadet
import cod3s
import pytest

GATE = "S_fed_available_out"


def test_default_field_is_true():
    """The new spec field defaults to True on FlowOut and its dynamic subclasses.

    ``setReinitialized`` has no getter in PyCATSHOO, so the flag itself cannot be
    read back from the variable; the model-level default is the spec assertion,
    and the behavioural revert below is its runtime manifestation.
    """
    assert muscadet.FlowOut(name="f").var_fed_available_out_reset is True
    assert muscadet.FlowOutTempo(name="f").var_fed_available_out_reset is True
    assert muscadet.FlowOutOnTrigger(name="f").var_fed_available_out_reset is True


class LegacyFlowComp(muscadet.ObjFlow):
    """Flow built via the pure legacy path: no reset kwarg passed at all."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(
            dict(
                cls="FlowOut",
                name="S",
                var_prod_default=True,
                var_fed_available_out_init=False,
            )
        )


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="ParitySys")
    comp = system.add_component(name="Oc", cls="LegacyFlowComp")

    # A detection automaton: while `present`, force the gate True (level effect);
    # on retract (present -> absent) no effect, so a reinitialised gate reverts.
    comp.add_atm2states(
        name="det",
        st1="absent",
        st2="present",
        init_st2=False,
        cond_occ_12=True,
        occ_law_12={"cls": "exp", "rate": 0.1},
        effects_12=[(f"^{GATE}$", True)],
        cond_occ_21=True,
        occ_law_21={"cls": "exp", "rate": 0.1},
        effects_21=[],
    )
    return system


def _find(system, substr):
    for i, t in enumerate(system.isimu_active_transitions()):
        if t is not None and substr in t._bkd.name():
            return i
    return None


def _fire(system, substr):
    i = _find(system, substr)
    assert i is not None, f"transition {substr!r} not fireable"
    system.isimu_set_transition(i, date=system.currentTime())
    system.isimu_step_forward()


def test_legacy_gate_reverts_after_retract(the_system):
    """Default (reset=True) gate: momentary set, then reverts on retract."""
    gate = the_system.comp["Oc"].flows_out["S"].var_fed_available

    the_system.isimu_start()
    assert gate.value() is False  # init

    _fire(the_system, "det_absent_present")
    assert gate.value() is True  # momentarily forced by the effect

    _fire(the_system, "det_present_absent")
    # Byte-identical legacy behaviour: the reinitialised gate forgets and reverts.
    assert gate.value() is False


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
