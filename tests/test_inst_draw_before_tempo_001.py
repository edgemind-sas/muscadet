"""An on-demand refusal is drawn before a delay(0) production, never after it.

A refusal on demand ("the breaker is asked to open and does not") is an inst
mode whose condition is the demand and whose effect clears the availability of
the output. That availability is True by default, so at the date the demand
appears two things become due at once: the inst draw, and the production the
demand enables. If the production goes first, the output is fed for a
zero-duration instant before the refusal clears it, and anything that reacts to
the output within that instant (a delay(0), an inst, a counter) sees an opening
that never happened.

The modelling answer is to temporise the production with a ``delay(0)``
(``FlowOutTempo``): the availability is not part of the temporisation's
condition but of the product ``var_fed = var_prod AND var_fed_available``, so
if the inst draw lands first the output is never fed. That rests on one claim
about the engine, written in cod3s as a comment and measured nowhere until
this module: at equal date, PyCATSHOO fires inst transitions before timed
ones, ``delay(0)`` included.

Measured here on the engine's own scheduling (``simulate()``, not the
interactive simulator, where the caller picks the order), with a spy on every
value the output takes:

* the temporised output is never fed at the date of a refused demand, for a
  certain refusal (single-branch inst) and for an uncertain one (two-branch
  inst with its parked micro-state);
* the plain ``FlowOut`` control IS fed for an instant before the refusal
  clears it -- without this the bench could not tell the claim from a spy
  that sees nothing.

The mode is written the way the COD3S Platform emits it: a native
``ObjMode2S`` with a structured condition on the input variable and a level
effect on the output's availability variable.
"""

import cod3s
import pytest

import muscadet

#: Date the demand appears.
DEMAND_AT = 5.0
HORIZON = 10.0
#: Runs: enough for both outcomes of the uncertain refusal to occur many times.
UNCERTAIN_RUNS = 200
UNCERTAIN_PROB = 0.5


class Demand(muscadet.ObjFlow):
    """Asks for the opening at ``DEMAND_AT`` and keeps asking."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(
            dict(
                cls="FlowOutTempo",
                name="cmd",
                var_prod_default=True,
                occ_enable_flow={"cls": "delay", "time": DEMAND_AT},
                occ_disable_flow={"cls": "delay", "time": 0},
            )
        )


class ValveTempo(muscadet.ObjFlow):
    """Opens on demand, through a delay(0) temporisation."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowIn", name="cmd", logic="and"))
        self.add_flow(
            dict(
                cls="FlowOutTempo",
                name="open",
                var_prod_cond=["cmd"],
                occ_enable_flow={"cls": "delay", "time": 0},
                occ_disable_flow={"cls": "delay", "time": 0},
            )
        )


class ValvePlain(muscadet.ObjFlow):
    """Opens on demand, immediately: the control."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowIn", name="cmd", logic="and"))
        self.add_flow(dict(cls="FlowOut", name="open", var_prod_cond=["cmd"]))


def refusal_mode(target, prob):
    """The refusal on demand, as the platform's translator emits it."""
    return dict(
        cls="ObjMode2S",
        fm_name="refuses_to_open",
        targets=[target],
        behaviour="internal",
        occ_state="occ",
        not_occ_state="rep",
        occ_law={"cls": "inst", "prob": [prob]},
        # Repair far beyond the horizon: a refusal stays a refusal.
        not_occ_law={"cls": "exp", "rate": [1e-9]},
        occ_param_name=["gamma"],
        not_occ_param_name=["mu"],
        occ_effects={"open_fed_available_out": False},
        occ_parked_state="not_occ",
        failure_cond=[{"attr": "cmd_fed_in", "ope": "==", "value": True}],
    )


#: Valve pairs (temporised, plain) per refusal probability.
PAIRS = {1.0: ("VT1", "VP1"), UNCERTAIN_PROB: ("VTU", "VPU")}


def build():
    """One system for both cases: PyCATSHOO holds one system at a time."""
    system = muscadet.System(name="InstBeforeTempo")
    system.add_component(name="D", cls="Demand")
    for prob, (tempo, plain) in PAIRS.items():
        system.add_component(name=tempo, cls="ValveTempo")
        system.add_component(name=plain, cls="ValvePlain")
        for valve in (tempo, plain):
            system.connect_flow("D", valve, "cmd")
            system.add_component(**refusal_mode(valve, prob))
    return system


def spy(system, valve):
    """Every (date, fed, available) the valve's output takes, in order.

    A ``None`` opens each run. The clock cannot separate them: a sensitive
    method fires on a CHANGE, so two runs in a row may both start recording at
    the date of the demand.
    """
    out = system.comp[valve].flows_out["open"]
    records = []

    def record():
        records.append(
            (
                system.currentTime(),
                out.var_fed.value(),
                out.var_fed_available.value(),
            )
        )

    system.comp[valve].addStartMethod(f"spy_run_{valve}", lambda: records.append(None))
    out.var_fed.addSensitiveMethod(f"spy_fed_{valve}", record)
    out.var_fed_available.addSensitiveMethod(f"spy_available_{valve}", record)
    return records


def runs_of(records):
    """Split the records into runs on the marker each run opens with."""
    runs = []
    for rec in records:
        if rec is None:
            runs.append([])
        else:
            runs[-1].append(rec)
    return runs


def at_demand(run):
    return [rec for rec in run if rec[0] == DEMAND_AT]


def refused(run):
    """Whether the refusal landed at the demand in this run."""
    return any(available is False for _, _, available in at_demand(run))


def fed_at_demand(run):
    return any(fed is True for _, fed, _ in at_demand(run))


def simulate(system, nb_runs):
    system.simulate(
        {
            "nb_runs": nb_runs,
            "schedule": [{"start": 0, "end": HORIZON, "nvalues": 3}],
        }
    )


@pytest.fixture(scope="module")
def runs():
    """Runs of every valve, keyed by valve name, from one simulation."""
    system = build()
    records = {valve: spy(system, valve) for pair in PAIRS.values() for valve in pair}
    simulate(system, UNCERTAIN_RUNS)
    # Released before ``test_delete`` closes the session, not at teardown.
    system.deleteSys()
    return {valve: runs_of(recs) for valve, recs in records.items()}


@pytest.fixture
def certain(runs):
    tempo, plain = PAIRS[1.0]
    return runs[tempo], runs[plain]


@pytest.fixture
def uncertain(runs):
    tempo, plain = PAIRS[UNCERTAIN_PROB]
    return runs[tempo], runs[plain]


def test_a_certain_refusal_lands_on_both_valves(certain):
    tempo, plain = certain
    assert len(tempo) == len(plain) == UNCERTAIN_RUNS
    assert all(refused(run) for run in tempo + plain)


def test_a_certain_refusal_never_lets_the_temporised_valve_open(certain):
    tempo, _ = certain
    assert not any(fed_at_demand(run) for run in tempo), tempo[:3]


def test_the_plain_valve_opens_for_an_instant_before_a_certain_refusal(certain):
    """The control: without it, a spy blind to zero-duration values would pass
    the claim above for any ordering."""
    _, plain = certain
    assert all(fed_at_demand(run) for run in plain), plain[:3]


def test_an_uncertain_refusal_lands_in_some_runs_and_not_in_others(uncertain):
    tempo, _ = uncertain
    outcomes = [refused(run) for run in tempo]
    assert any(outcomes) and not all(outcomes), outcomes


def test_an_uncertain_refusal_never_lets_the_temporised_valve_open(uncertain):
    tempo, _ = uncertain
    leaks = [run for run in tempo if refused(run) and fed_at_demand(run)]
    assert not leaks, leaks[:3]


def test_an_uncertain_success_opens_the_temporised_valve(uncertain):
    """The parked branch must still let the demand through."""
    tempo, _ = uncertain
    successes = [run for run in tempo if not refused(run)]
    assert successes and all(fed_at_demand(run) for run in successes)


def test_the_plain_valve_opens_for_an_instant_before_an_uncertain_refusal(
    uncertain,
):
    _, plain = uncertain
    refusals = [run for run in plain if refused(run)]
    assert refusals and all(fed_at_demand(run) for run in refusals)


def test_delete():
    cod3s.terminate_session()
