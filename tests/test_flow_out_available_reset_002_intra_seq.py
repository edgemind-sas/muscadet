"""Availability-gate reset — intra-sequence persistence (memory within a run).

With ``var_fed_available_out_reset=False`` the gate is built with
``setReinitialized(False)``: it MEMORISES its last value within a sequence. A
momentary write (activate effect) is HELD after the source is retracted, a slow
transition conditioned on the flow stays fireable, and an explicit clear effect
(inhibit) takes the hand back.

This reproduces the go/no-go spike (patched / persistent branch) as a real test.
"""

import muscadet
import cod3s
import pytest

GATE = "S_fed_available_out"
SFED = "S_fed_out"
MU_SLOW = 2e-3  # slow transition (mean ~500 t.u.)


class PersistentHoldComp(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        # prod_default=True so var_fed can actually be True once the gate opens
        # (var_fed = var_prod AND var_is_active AND var_fed_available).
        self.add_flow(
            dict(
                cls="FlowOut",
                name="S",
                var_prod_default=True,
                var_fed_available_out_init=False,
                var_fed_available_out_reset=False,
            )
        )


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="IntraSeqSys")
    comp = system.add_component(name="Oc", cls="PersistentHoldComp")

    # det: absent <-> present. While present force the gate True (momentary set);
    # on retract no effect -> a PERSISTENT gate holds True.
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
    # slow: armed -> fired, conditioned on S_fed True, never returns.
    comp.add_atm2states(
        name="slow",
        st1="armed",
        st2="fired",
        init_st2=False,
        cond_occ_12=SFED,
        occ_law_12={"cls": "exp", "rate": MU_SLOW},
        effects_12=[],
        cond_occ_21=False,
        occ_law_21={"cls": "exp", "rate": 0.0},
        effects_21=[],
    )
    # inhibit: off -> on. While on force the gate False (clear / repair effect).
    comp.add_atm2states(
        name="inhibit",
        st1="off",
        st2="on",
        init_st2=False,
        cond_occ_12=True,
        occ_law_12={"cls": "exp", "rate": 0.1},
        effects_12=[(f"^{GATE}$", False)],
        cond_occ_21=False,
        occ_law_21={"cls": "exp", "rate": 0.0},
        effects_21=[],
    )
    return system


def _find(system, substr):
    for i, t in enumerate(system.isimu_active_transitions()):
        if t is not None and substr in t._bkd.name():
            return i
    return None


def _fire(system, substr, date=None):
    i = _find(system, substr)
    assert i is not None, f"transition {substr!r} not fireable"
    system.isimu_set_transition(
        i, date=date if date is not None else system.currentTime()
    )
    system.isimu_step_forward()


def test_persistent_gate_holds_and_inhibit_clears(the_system):
    fo = the_system.comp["Oc"].flows_out["S"]
    gate = fo.var_fed_available
    fed = fo.var_fed

    the_system.isimu_start()
    assert gate.value() is False  # init: gate closed
    assert fed.value() is False

    # activate: momentary detection forces the gate open
    _fire(the_system, "det_absent_present")
    assert gate.value() is True
    assert fed.value() is True

    # retract the source: the gate must HOLD (this is the whole point)
    _fire(the_system, "det_present_absent")
    assert (
        gate.value() is True
    ), "persistent gate (reset=False) must hold its value after retract"
    assert fed.value() is True

    # a slow transition conditioned on the flow is still fireable after the
    # source is gone, and actually fires (planned far in the future)
    assert _find(the_system, "slow_armed_fired") is not None
    _fire(the_system, "slow_armed_fired", date=the_system.currentTime() + 500.0)
    # once fired, armed->fired is no longer among the active transitions
    assert _find(the_system, "slow_armed_fired") is None

    # explicit clear takes the hand back: the persistent gate drops to False
    _fire(the_system, "inhibit_off_on")
    assert gate.value() is False, "inhibit effect must clear the persistent gate"

    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
