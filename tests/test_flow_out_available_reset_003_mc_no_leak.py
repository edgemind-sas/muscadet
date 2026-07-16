"""Availability-gate reset — no inter-sequence leak in Monte-Carlo.

THE critical de-risk: a non-reinitialised (persistent, reset=False) gate must
NOT carry its held value from one MC sequence into the next. PyCATSHOO restores
declared init values between sequences regardless of the reinitialised flag, so
the memory is intra-sequence only.

Single system, two twin components sharing the same deterministic pulse schedule
and seed:
  * Base    (reset=True) : the one-shot pulse sets the gate True momentarily,
    then the reinitialised gate reverts -> mean ~0 at every sampled instant.
  * Persist (reset=False): the pulse-set True is HELD -> mean ~1 after the pulse.

Two checks:
  * POWER : Base@late ~ 0 vs Persist@late ~ 1  (the persistence mechanism is
    genuinely active; otherwise the leak check would be a false negative).
  * NO-LEAK: Persist @ t=0 (start of each sequence, BEFORE the pulse) ~ 0.
"""

import muscadet
import cod3s
import pytest

GATE = "S_fed_available_out"
PULSE_DELAY = 0.5
SCHEDULE = [0.0, 0.1, 1.0, 5.0, 10.0]
NB_RUNS = 200
SEED = 4242

_KEEPALIVE = []  # keep sensitive-method closures alive for the run's lifetime


class PulseFlowComp(muscadet.ObjFlow):
    def add_flows(self, reset=True, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(
            dict(
                cls="FlowOut",
                name="S",
                var_prod_default=True,
                var_fed_available_out_init=False,
                var_fed_available_out_reset=reset,
            )
        )


def _add_one_shot_pulse(comp):
    """idle --delay 0.5--> firing (transient, sets gate True) --delay 0--> done.

    `done` is terminal: no re-arm, no standing writer. The memory persists ONLY
    when the gate is not reinitialised, which is exactly what discriminates the
    two twins.
    """
    gate = comp.flows_out["S"].var_fed_available
    aut = cod3s.PycAutomaton(
        name=f"{comp.name()}_pulse",
        states=["pl_idle", "pl_firing", "pl_done"],
        init_state="pl_idle",
        transitions=[
            {
                "name": "pl_idle_firing",
                "source": "pl_idle",
                "target": "pl_firing",
                "is_interruptible": True,
                "occ_law": {"cls": "delay", "time": PULSE_DELAY},
            },
            {
                "name": "pl_firing_done",
                "source": "pl_firing",
                "target": "pl_done",
                "is_interruptible": True,
                "occ_law": {"cls": "delay", "time": 0.0},
            },
        ],
    )
    aut.update_bkd(comp)
    aut.get_transition_by_name("pl_idle_firing")._bkd.setCondition(True)
    aut.get_transition_by_name("pl_firing_done")._bkd.setCondition(True)
    firing_bkd = aut.get_state_by_name("pl_firing")._bkd

    def eff_pulse():
        if firing_bkd.isActive():
            gate.setValue(True)

    aut._bkd.addSensitiveMethod("pulse_eff", eff_pulse)
    gate.addSensitiveMethod("pulse_eff", eff_pulse)
    _KEEPALIVE.append(eff_pulse)


@pytest.fixture(scope="module")
def the_run():
    system = muscadet.System(name="McNoLeakSys")
    base = system.add_component(name="Base", cls="PulseFlowComp", reset=True)
    persist = system.add_component(name="Persist", cls="PulseFlowComp", reset=False)
    _add_one_shot_pulse(base)
    _add_one_shot_pulse(persist)
    system.add_indicator_var(component="Base", var=f"^{GATE}$", stats=["mean"])
    system.add_indicator_var(component="Persist", var=f"^{GATE}$", stats=["mean"])

    system.simulate({"nb_runs": NB_RUNS, "schedule": SCHEDULE, "seed": SEED})

    means = {}
    for name, indic in system.indicators.items():
        df = indic.values
        means[name] = {
            round(float(r["instant"]), 4): float(r["values"])
            for _, r in df[df["stat"] == "mean"].iterrows()
        }
    return {"system": system, "means": means}


def test_power_persistent_mechanism_is_active(the_run):
    """Baseline reverts (~0) while persistent holds (~1): the memory is genuinely on."""
    base = the_run["means"]["Base_" + GATE]
    persist = the_run["means"]["Persist_" + GATE]
    assert base[10.0] < 0.05, f"baseline should revert, got {base[10.0]}"
    assert persist[10.0] > 0.95, f"persistent gate should hold, got {persist[10.0]}"


def test_no_inter_sequence_leak(the_run):
    """Persistent gate at the start of each sequence is back to its init (~0)."""
    persist = the_run["means"]["Persist_" + GATE]
    # If the memory leaked across sequences this would be ~(N-1)/N ~ 0.995.
    assert (
        persist[0.0] < 0.05
    ), f"inter-sequence leak detected: gate@t=0 = {persist[0.0]}"


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
