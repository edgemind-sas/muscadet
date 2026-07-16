"""Availability-gate reset — write-safety (both-pulse invariant).

WRITE-SAFETY INVARIANT: a gate that is deliberately not reinitialised
(reset=False, persistent) can no longer fall back to rest on its own. The ONLY
safe way to write it is a PULSE (a transient state drained via inst / delay(0)),
on BOTH polarities (set AND clear). A standing level clamp of either polarity
coexisting with an opposite writer has no fixpoint -> silent non-deterministic
hang on a fraction of MC sequences.

  * test_both_pulse_mc_converges (default): set-pulse detection + clear-pulse
    repair on a persistent gate -> MC returns (no hang) with a sane trajectory.
  * test_level_clamp_on_persistent_gate_hangs (slow): the FORBIDDEN montage (two
    standing clamps of opposite polarity on a persistent gate) is run in a child
    process under a timeout and asserted to HANG. Marked `slow` because it
    deliberately never returns; run with `pytest --runslow`. It documents *why*
    the both-pulse invariant is mandatory and must not be relaxed.
"""

import os
import subprocess
import sys

import muscadet
import cod3s
import pytest

GATE = "X_fed_available_out"
MU_REPAIR = 5e-2
LAMBDA_DET = 0.2
SCHEDULE = [0.0, 1.0, 5.0, 20.0, 50.0, 200.0]
NB_RUNS = 200
SEED = 7

_KEEPALIVE = []


class PersistentPulseComp(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(
            dict(
                cls="FlowOut",
                name="X",
                var_prod_default=True,
                var_fed_available_out_init=False,
                var_fed_available_out_reset=False,
            )
        )


def _add_pulse(comp, tag, rate, set_value):
    """One-shot pulse: idle --exp(rate)--> firing (transient, sets gate to
    set_value) --delay 0--> done. `done` terminal (no re-arm, no standing writer).
    """
    gate = comp.flows_out["X"].var_fed_available
    aut = cod3s.PycAutomaton(
        name=f"{comp.name()}_{tag}",
        states=[f"{tag}_idle", f"{tag}_firing", f"{tag}_done"],
        init_state=f"{tag}_idle",
        transitions=[
            {
                "name": f"{tag}_idle_firing",
                "source": f"{tag}_idle",
                "target": f"{tag}_firing",
                "is_interruptible": True,
                "occ_law": {"cls": "exp", "rate": rate},
            },
            {
                "name": f"{tag}_firing_done",
                "source": f"{tag}_firing",
                "target": f"{tag}_done",
                "is_interruptible": True,
                "occ_law": {"cls": "delay", "time": 0.0},
            },
        ],
    )
    aut.update_bkd(comp)
    aut.get_transition_by_name(f"{tag}_idle_firing")._bkd.setCondition(True)
    aut.get_transition_by_name(f"{tag}_firing_done")._bkd.setCondition(True)
    firing_bkd = aut.get_state_by_name(f"{tag}_firing")._bkd

    def eff():
        if firing_bkd.isActive():
            gate.setValue(set_value)

    aut._bkd.addSensitiveMethod(f"{tag}_eff", eff)
    gate.addSensitiveMethod(f"{tag}_eff", eff)
    _KEEPALIVE.append(eff)


@pytest.fixture(scope="module")
def the_run():
    system = muscadet.System(name="BothPulseSys")
    comp = system.add_component(name="Xc", cls="PersistentPulseComp")
    _add_pulse(comp, "dp", LAMBDA_DET, True)  # detection: set-pulse
    _add_pulse(comp, "rp", MU_REPAIR, False)  # repair: clear-pulse
    system.add_indicator_var(component="Xc", var=f"^{GATE}$", stats=["mean"])

    # If the montage were unsafe this call would hang; reaching the assertions
    # below is itself the convergence proof.
    system.simulate({"nb_runs": NB_RUNS, "schedule": SCHEDULE, "seed": SEED})

    indic = next(iter(system.indicators.values()))
    df = indic.values
    means = {
        round(float(r["instant"]), 4): float(r["values"])
        for _, r in df[df["stat"] == "mean"].iterrows()
    }
    return {"system": system, "means": means}


def test_both_pulse_mc_converges(the_run):
    """Both-polarity pulses on a persistent gate: MC converges, trajectory is sane."""
    means = the_run["means"]
    assert set(SCHEDULE) <= set(means.keys())  # every scheduled instant produced
    assert all(0.0 <= v <= 1.0 for v in means.values())
    assert means[0.0] < 0.05  # gate starts at its init each sequence
    # the set-pulse actually holds on a share of sequences (not a dead model)
    assert max(means.values()) > 0.1


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()


# --- FORBIDDEN montage: documented + guarded (slow, deliberately hangs) -------

_LEVEL_CLAMP_HANG_SCRIPT = r"""
import muscadet, cod3s
GATE = "X_fed_available_out"

class XComp(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowOut", name="X", var_prod_default=True,
                           var_fed_available_out_init=False,
                           var_fed_available_out_reset=False))

system = muscadet.System(name="LevelClamp")
comp = system.add_component(name="Xc", cls="XComp")
# STANDING detection clamp True (level writer, up while `active`)
comp.add_atm2states(name="dem", st1="idle", st2="active", init_st2=False,
    cond_occ_12=True, occ_law_12={"cls": "exp", "rate": 0.2},
    effects_12=[("^%s$" % GATE, True)],
    cond_occ_21=False, occ_law_21={"cls": "exp", "rate": 0.0}, effects_21=[])
# STANDING repair clamp False (opposite polarity, up while `on`)
comp.add_atm2states(name="rep", st1="off", st2="on", init_st2=False,
    cond_occ_12=True, occ_law_12={"cls": "exp", "rate": 0.05},
    effects_12=[("^%s$" % GATE, False)],
    cond_occ_21=False, occ_law_21={"cls": "exp", "rate": 0.0}, effects_21=[])

def find(sub):
    for i, t in enumerate(system.isimu_active_transitions()):
        if t is not None and sub in t._bkd.name():
            return i
    return None
def fire(sub):
    system.isimu_set_transition(find(sub), date=system.currentTime())
    system.isimu_step_forward()

system.isimu_start()
fire("dem_idle_active")   # detection clamp UP
print("PRE_HANG", flush=True)
fire("rep_off_on")        # opposite clamp UP -> no fixpoint -> HANG here
print("CONVERGED", flush=True)   # must never be reached
"""


@pytest.mark.slow
def test_level_clamp_on_persistent_gate_hangs():
    """The forbidden two-standing-clamp montage on a persistent gate hangs (no fixpoint).

    Proven in a child process under a timeout so it cannot block the suite. The
    child inherits LD_LIBRARY_PATH / PYTHONPATH from the pytest process, so it
    imports the same local muscadet + PyCATSHOO.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _LEVEL_CLAMP_HANG_SCRIPT],
            env=os.environ,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        # Expected: the second standing clamp has no fixpoint -> infinite loop.
        out = exc.stdout or b""
        out = out.decode() if isinstance(out, bytes) else out
        assert "PRE_HANG" in out, "child should reach the second clamp before hanging"
        assert "CONVERGED" not in out, "montage unexpectedly converged"
        return
    pytest.fail(
        "level-clamp montage on a persistent gate did NOT hang "
        f"(rc={proc.returncode}); the both-pulse invariant may have changed.\n"
        f"stdout={proc.stdout!r} stderr={proc.stderr[-500:]!r}"
    )
