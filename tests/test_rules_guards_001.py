"""Guard compilation: rule selection through a watched mode automaton.

A rule set's guards compile into an automaton with one state per rule -- plus a
state meaning "no rule applies" when the set declares no default (R14) -- and
the automaton's ACTIVE STATE is what selects the coefficient maps the production
equation reads (KD7, R12).

Two properties motivate the automaton, and neither is available to a guard
evaluated inline in the equation:

* the coefficients are **frozen within a mode**, which breaks the algebraic
  coupling between demand and production the two topological sweeps rest on;
* the transitions are **watched** by the solver, so a threshold crossing is
  detected AT the crossing. ``RAMPED`` below crosses its bound at t = 4 while
  the only other stops in the whole run are t = 3 and the t = 10 horizon: an
  unwatched guard would fire six time units late, and by an amount that depends
  on the integration step size.

At most one rule is active at a time: two guards holding together is a model
error naming both rules (R13). It is checked at every evaluation, so the
conflicting system below is built, driven and deleted on its own -- PyCATSHOO
forbids more than one live system per process, and that scenario aborts its run
on purpose.

The reference component is the one the acceptance examples use: discrete input
``F4``, continuous inputs ``F1`` / ``F2`` / ``F3``, continuous output ``X``, and
three rules -- ``F4`` false: 3.F3 + 2.F2 -> 2.X; ``F4`` true and F1 < 10: no
production; ``F4`` true and F1 >= 10: 3.F1 -> 0.5.X.
"""

import muscadet
import cod3s
import pytest

from muscadet import rules

#: Horizon the interactive session runs to, so every mode has settled.
HORIZON = 10.0

#: Date the discrete control signal of ``FLIP`` drops (R21).
SWITCH_DATE = 3.0

#: Date ``RAMPED``'s input crosses the 10 bound: it starts at 6 and rises at 1.
CROSSING_DATE = 4.0

#: The solver stops the integration on the crossing rather than refining it to
#: machine precision, so the dates are asserted within one default PDMP step.
CROSSING_TOL = 0.05

#: Components carrying a rule set whose mode is traced through the run.
GUARDED = ["NOMINAL", "BELOW", "ABOVE", "SCARCE", "GAP", "FLIP", "RAMPED"]


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class RateSource(muscadet.ObjFlow):
    """A continuous producer holding the rates it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for name, rate in kwargs.get("rates", {}).items():
            self.add_flow_continuous_out(name=name, var_fed_default=rate)


class Switch(muscadet.ObjFlow):
    """A DISCRETE producer of the control signal a guard reads (R21)."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="F4", var_prod_default=kwargs.get("on", True))


class Unit(muscadet.ObjFlow):
    """The reference component of the acceptance examples: three guarded rules."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="F4", logic="and")
        self.add_flow_continuous_in(name="F1")
        self.add_flow_continuous_in(name="F2")
        self.add_flow_continuous_in(name="F3")
        self.add_flow_continuous_out(name="X")
        self.add_rules(
            name="X",
            rules=[
                # F4 false : 3.F3 + 2.F2 -> 2.X
                dict(name="off", cond="not F4", cons={"F3": 3, "F2": 2}, prod={"X": 2}),
                # F4 true and F1 < 10 : no production
                dict(name="idle", cond="F4 and F1 < 10", prod={"X": 0}),
                # F4 true and F1 >= 10 : 3.F1 -> 0.5.X
                dict(
                    name="boost",
                    cond="F4 and F1 >= 10",
                    cons={"F1": 3},
                    prod={"X": 0.5},
                ),
            ],
        )


class Uncovered(muscadet.ObjFlow):
    """A single guarded rule and NO default: some combination is uncovered."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="F4", logic="and")
        self.add_flow_continuous_in(name="F1")
        self.add_flow_continuous_out(name="X")
        self.add_rules(
            name="X",
            rules=[dict(name="on", cond="F4", cons={"F1": 1}, prod={"X": 1})],
        )


class Plain(muscadet.ObjFlow):
    """A rule set carrying no guard at all: there is nothing to compile."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="F1")
        self.add_flow_continuous_out(name="X")
        self.add_rules(name="X", rules=[dict(cons={"F1": 2}, prod={"X": 1})])


class Conflicting(muscadet.ObjFlow):
    """Two guards that overlap: both hold as soon as ``F1`` reaches 10."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="F1")
        self.add_flow_continuous_out(name="X")
        self.add_rules(
            name="X",
            rules=[
                dict(name="over_five", cond="F1 >= 5", prod={"X": 1}),
                dict(name="over_ten", cond="F1 >= 10", prod={"X": 2}),
            ],
        )


class Ramp(muscadet.ObjFlow):
    """A continuous output that ramps: ``F1`` = ``init`` + t.

    The ramp comes from an INTEGRATED level, which is what gives the solver
    something to root-find the crossing on. Its own equation replaces the rule
    evaluation entirely, so the internal tank needs no rule to drain it.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="fill")
        self.add_flow_continuous_out(name="F1")
        self.add_capacity(
            name="tank",
            flow="fill",
            capacity=1e6,
            side="in",
            content_init={"fill": kwargs.get("init", 6.0)},
        )

    def compute_production(self):
        self.flows_out["F1"].var_fed.setValue(
            self.capacities["tank"].get_quantity("fill")
        )


# ----------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------


def feed(system, target, flow, rate):
    """Wire a dedicated source delivering ``rate`` of ``flow`` into ``target``."""
    name = f"{target}_{flow}_SRC"
    system.add_component(name=name, cls="RateSource", rates={flow: rate})
    system.connect_flow(source=name, target=target, flow_name=flow)
    return name


def add_clock(comp, date):
    """Give the interactive session a date it can always step to."""
    comp.add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def mode_of(system, comp_name):
    """The compiled mode of a component's ``X`` rule set."""
    return system.comp[comp_name].rule_sets["X"].mode


def snapshot(system):
    """What every guarded component's mode and output read right now."""
    return {
        "time": system.currentTime(),
        "mode": {name: mode_of(system, name).active_key() for name in GUARDED},
        "X": {
            name: system.comp[name].flows_out["X"].var_fed.value() for name in GUARDED
        },
    }


def first_stop_with_mode(trace, comp_name, key):
    """The first recorded stop at which ``comp_name`` sits in mode ``key``."""
    for entry in trace:
        if entry["mode"][comp_name] == key:
            return entry
    return None


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_conflict_scenario(obs):
    """AE8: two guards holding at once, on its own system -- the run aborts."""
    system = muscadet.System(name="RulesGuardsConflict")

    system.add_component(name="CONFLICT", cls="Conflicting")
    feed(system, "CONFLICT", "F1", 12.0)
    add_clock(system.comp["CONFLICT"], HORIZON)

    # The check is an evaluation-time one, so asking the component directly is
    # enough to trigger it: the guards read the values the flows carry now.
    rule_set = system.comp["CONFLICT"].rule_sets["X"]
    obs["conflict_direct_error"] = None
    try:
        system.comp["CONFLICT"].get_active_rule(rule_set)
    except Exception as err:
        obs["conflict_direct_error"] = err

    # ... and the same error is what the run raises, rather than one of the two
    # regimes being elected silently.
    obs["conflict_run_error"] = None
    try:
        system.isimu_start()
        for _ in range(4):
            system.isimu_step_forward()
    except Exception as err:
        obs["conflict_run_error"] = err
    system.isimu_stop()

    system.deleteSys()


def build_guard_system():
    """Every guard scenario, in one system driven through a single session."""
    system = muscadet.System(name="RulesGuardsSys")

    # -- The discrete control signal the guards read (R21)
    system.add_component(name="SW_ON", cls="Switch", on=True)
    system.add_component(name="SW_OFF", cls="Switch", on=False)
    system.add_component(name="SW_FLIP", cls="Switch", on=True)

    # -- AE1: F4 false, F3 at 3 and F2 at 2 -> X produces 2
    system.add_component(name="NOMINAL", cls="Unit")
    system.connect_flow(source="SW_OFF", target="NOMINAL", flow_name="F4")
    feed(system, "NOMINAL", "F3", 3.0)
    feed(system, "NOMINAL", "F2", 2.0)

    # -- AE2: F4 true, F1 at 9 -> X produces 0
    system.add_component(name="BELOW", cls="Unit")
    system.connect_flow(source="SW_ON", target="BELOW", flow_name="F4")
    feed(system, "BELOW", "F1", 9.0)

    # -- AE3: F4 true, F1 at 12 -> X produces 2
    system.add_component(name="ABOVE", cls="Unit")
    system.connect_flow(source="SW_ON", target="ABOVE", flow_name="F4")
    feed(system, "ABOVE", "F1", 12.0)

    # -- AE4: F4 false, F3 at 3 but F2 only at 1 -> X produces 1
    system.add_component(name="SCARCE", cls="Unit")
    system.connect_flow(source="SW_OFF", target="SCARCE", flow_name="F4")
    feed(system, "SCARCE", "F3", 3.0)
    feed(system, "SCARCE", "F2", 1.0)

    # -- AE6: the only guard is false and the set declares no default
    system.add_component(name="GAP", cls="Uncovered")
    system.connect_flow(source="SW_OFF", target="GAP", flow_name="F4")
    feed(system, "GAP", "F1", 5.0)

    # -- A guard on a discrete flow, which drops at SWITCH_DATE
    system.add_component(name="FLIP", cls="Unit")
    system.connect_flow(source="SW_FLIP", target="FLIP", flow_name="F4")
    feed(system, "FLIP", "F1", 12.0)
    feed(system, "FLIP", "F3", 3.0)
    feed(system, "FLIP", "F2", 1.0)
    system.comp["SW_FLIP"].add_delay_failure_mode(
        name="flip",
        failure_time=SWITCH_DATE,
        failure_effects=[("F4_fed_available_out", False)],
        repair_time=1e6,
    )

    # -- A threshold crossed by a ramping input, watched by the solver
    system.add_component(name="RAMP", cls="Ramp", init=6.0)
    system.add_component(name="RAMPED", cls="Unit")
    system.connect_flow(source="SW_ON", target="RAMPED", flow_name="F4")
    system.connect_flow(source="RAMP", target="RAMPED", flow_name="F1")

    # -- A rule set carrying no guard: nothing is compiled for it
    system.add_component(name="PLAIN", cls="Plain")
    feed(system, "PLAIN", "F1", 4.0)

    add_clock(system.comp["SW_ON"], HORIZON)

    return system


def run_guard_scenario(obs):
    """Drive the guard system to the horizon, recording every stop."""
    system = build_guard_system()

    system.isimu_start()

    # The ramp's tank fills at 1 per unit of time: the demand sweep that would
    # write this transit hook lands in a later unit.
    system.comp["RAMP"].capacities["tank"].set_inflow("fill", 1.0)

    trace = [snapshot(system)]
    for _ in range(60):
        system.isimu_step_forward()
        trace.append(snapshot(system))
        if system.currentTime() >= HORIZON:
            break

    obs["trace"] = trace
    obs["settled"] = trace[-1]
    obs["plain_X"] = system.comp["PLAIN"].flows_out["X"].var_fed.value()

    system.isimu_stop()

    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive both systems in turn, snapshotting what each produced."""
    obs = {}

    run_conflict_scenario(obs)

    # Kept alive for the teardown test, per the module convention.
    run_guard_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# The acceptance examples (R12, R14, R21)
# ----------------------------------------------------------------------


def test_a_false_discrete_guard_selects_the_first_rule(the_run):
    """AE1: F4 false, F3 delivered at 3 and F2 at 2, and X produces 2.

    The guard is a NEGATED operand on a discrete flow (R21), and it selects the
    rule whose coefficients are 3 of F3 and 2 of F2 for 2 of X.
    """
    settled = the_run["settled"]

    assert settled["mode"]["NOMINAL"] == 0
    assert settled["X"]["NOMINAL"] == pytest.approx(2.0)


def test_a_threshold_below_the_bound_yields_no_production(the_run):
    """AE2: F4 true and F1 delivered at 9, so X produces 0.

    The selected rule declares ``prod={"X": 0}``: the mode is not "no rule", it
    is a rule whose production regime happens to be null.
    """
    settled = the_run["settled"]

    assert settled["mode"]["BELOW"] == 1
    assert settled["X"]["BELOW"] == pytest.approx(0.0)


def test_the_same_guard_above_the_bound_selects_the_other_rule(the_run):
    """AE3: F4 true and F1 delivered at 12, so X produces 2.

    Same discrete operand, opposite side of the numeric one: 3 of F1 make 0.5
    of X, and 12 delivered runs the rule at scale 4.
    """
    settled = the_run["settled"]

    assert settled["mode"]["ABOVE"] == 2
    assert settled["X"]["ABOVE"] == pytest.approx(2.0)


def test_the_scarcest_input_still_sets_the_scale_of_the_selected_rule(the_run):
    """AE4: F4 false, F3 at 3 but F2 only at 1, so X produces 1.

    The mode picks the coefficients; the limiting reagent then scales them --
    half the nominal here, set by F2. The two mechanisms compose.
    """
    settled = the_run["settled"]

    assert settled["mode"]["SCARCE"] == settled["mode"]["NOMINAL"]
    assert settled["X"]["SCARCE"] == pytest.approx(1.0)
    assert settled["X"]["SCARCE"] == pytest.approx(settled["X"]["NOMINAL"] / 2)


def test_an_uncovered_combination_with_no_default_produces_zero(the_run):
    """AE6: the only guard is false and the set declares no default rule.

    The automaton sits in its "no rule applies" state, and a rule set that
    selects nothing produces zero (R14) -- it does not fall back on the single
    rule it happens to carry.
    """
    settled = the_run["settled"]

    assert settled["mode"]["GAP"] == rules.NO_RULE
    assert settled["X"]["GAP"] == pytest.approx(0.0)


def test_two_guards_holding_at_once_raise_naming_both_rules(the_run):
    """AE8: F1 at 12 satisfies both ``F1 >= 5`` and ``F1 >= 10``.

    Two production regimes holding together is a model error, and reporting it
    beats electing one silently. The message names BOTH rules, so the modeller
    knows which pair to separate.
    """
    for key in ("conflict_direct_error", "conflict_run_error"):
        error = the_run[key]

        assert error is not None, f"{key}: a guard conflict must not pass silently"
        assert isinstance(error, ValueError)

        message = str(error)
        assert "CONFLICT" in message
        assert "over_five" in message
        assert "over_ten" in message


# ----------------------------------------------------------------------
# R12 -- the transitions are watched by the solver
# ----------------------------------------------------------------------


def test_a_threshold_crossing_fires_the_transition_at_the_crossing(the_run):
    """The input ramps from 6 at rate 1 and crosses the 10 bound at t = 4.

    The only other stops in the whole run are the discrete switch at t = 3 and
    the t = 10 horizon, so a guard the solver did not watch would fire six time
    units late. Watched, the mode changes at the crossing.
    """
    entry = first_stop_with_mode(the_run["trace"], "RAMPED", 2)

    assert entry is not None, "the ramping input must cross its bound"
    assert entry["time"] == pytest.approx(CROSSING_DATE, abs=CROSSING_TOL)
    assert entry["time"] < HORIZON

    # Below the bound it sat in the null-production rule, and above it produces
    # 0.5 of X per 3 of F1 -- at the horizon, F1 reads 16.
    assert the_run["trace"][1]["mode"]["RAMPED"] == 1
    assert the_run["settled"]["X"]["RAMPED"] == pytest.approx(16.0 / 3 * 0.5)


def test_a_guard_on_a_discrete_flow_reacts_to_the_flow_changing_state(the_run):
    """R21: the control signal drops at t = 3 and the mode follows it there.

    Before the drop, F4 is true and F1 is 12: the boost rule applies. After it,
    the negated operand holds and the F3 / F2 rule takes over -- at half its
    nominal, since F2 is delivered at 1.
    """
    trace = the_run["trace"]

    before = [
        entry
        for entry in trace
        if entry["time"] < SWITCH_DATE or entry["mode"]["FLIP"] == 2
    ]
    assert before[-1]["mode"]["FLIP"] == 2, "the boost rule holds while F4 is true"

    entry = first_stop_with_mode(trace, "FLIP", 0)
    assert entry is not None, "the guard must follow the discrete flow"
    assert entry["time"] == pytest.approx(SWITCH_DATE, abs=CROSSING_TOL)

    assert the_run["settled"]["X"]["FLIP"] == pytest.approx(1.0)


def test_a_negated_operand_selects_the_complementary_rule(the_run):
    """The same three rules, and only the state of F4 differs.

    NOMINAL reads F4 false and lands on the negated rule; ABOVE reads it true
    and lands on a rule whose guard carries the same operand un-negated.
    """
    settled = the_run["settled"]

    assert settled["mode"]["NOMINAL"] != settled["mode"]["ABOVE"]

    rule_set = the_run["system"].comp["NOMINAL"].rule_sets["X"]
    selected = rule_set.rules[settled["mode"]["NOMINAL"]]

    assert selected.name == "off"
    assert [operand.negate for operand in selected.cond] == [True]
    assert selected.guard_expression() == "not F4"


# ----------------------------------------------------------------------
# What is compiled, and what is not
# ----------------------------------------------------------------------


def test_the_mode_automaton_carries_one_state_per_rule(the_run):
    """Three rules and no default: three rule states plus the "none" state.

    Every pair of states is joined by an instantaneous transition, which is what
    lets the mode move to whichever rule the guards designate without passing
    through an intermediate regime.
    """
    comp = the_run["system"].comp["NOMINAL"]
    mode = comp.rule_sets["X"].mode

    assert mode is not None
    assert mode.keys == [0, 1, 2, rules.NO_RULE]
    assert mode.init_key == rules.NO_RULE

    aut = mode.automaton
    assert aut.name in comp.automata_d
    assert [state.name for state in aut.states] == [
        "X_rule_0",
        "X_rule_1",
        "X_rule_2",
        "X_none",
    ]

    # One transition per ordered pair of distinct states.
    assert len(aut.transitions) == 4 * 3

    transition = aut.get_transition_by_name("X_0_to_none")
    assert transition.source == "X_rule_0"
    assert transition.target == "X_none"
    assert transition.occ_law.time == 0


def test_a_rule_set_with_no_guard_compiles_no_automaton(the_run):
    """Nothing to select: the default rule needs no mode, and gets none.

    That is what keeps a model declaring no guard exactly what it was -- no
    automaton, no watched transition, no state to read back.
    """
    comp = the_run["system"].comp["PLAIN"]
    rule_set = comp.rule_sets["X"]

    assert rule_set.mode is None
    assert rule_set.active_rule() is rule_set.rules[0]

    assert not [name for name in comp.automata_d if name.endswith("_mode")]

    # 2 of F1 make 1 of X, and F1 is delivered at 4.
    assert the_run["plain_X"] == pytest.approx(2.0)


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
