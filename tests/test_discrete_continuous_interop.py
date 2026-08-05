"""Discrete and continuous flows conditioning one another (R21, R22).

Two directions, one vocabulary. An operand mapping ``{name, negate, port, op,
value}`` reads the same on both sides of the interoperation:

* **R21** -- a discrete flow appears as a guard operand on a transformation
  rule, so a boolean signal selects which continuous regime runs. This is what
  the guard compilation already brought in: nothing new is declared for it here,
  and the scenario below confirms it end to end on a component that ALSO
  declares the other direction.
* **R22** -- a comparison against a continuous quantity appears in a discrete
  output's production condition, so a component emits a boolean signal derived
  from a measured value. That is what makes a protection or a threshold alarm
  expressible.

The crossing of R22 is detected AT the crossing, not at the following
integration step: the comparison compiles to a two-state automaton whose
transitions are registered as watched, exactly like a rule guard's threshold
(R12). ``ALARM`` below crosses its bound at t = 4 while the only other stops in
the whole run are the discrete switch at t = 3 and the t = 10 horizon, so a
threshold the solver did not watch would fire six time units late.

PyCATSHOO forbids more than one live system per process, so the declaration
scenario, the batch scenario and the interactive one are built, driven and
deleted one after the other.
"""

import muscadet
import cod3s
import pytest

#: Horizon the interactive session runs to.
HORIZON = 10.0

#: Date the discrete control signal drops, driving the R21 direction.
SWITCH_DATE = 3.0

#: An early stop, before anything moves: the mode automaton is compiled at
#: DECLARATION time and settles on the state its guards select at the first
#: stop, so the nominal regime is read there rather than at t = 0.
SETTLE_DATE = 1.0

#: Date ``ALARM``'s input crosses its bound: the ramp starts at 6 and rises at 1.
CROSSING_DATE = 4.0

#: The solver stops the integration on the crossing rather than refining it to
#: machine precision, so dates are asserted within one default PDMP step.
CROSSING_TOL = 0.05

#: Level at which the alarm output becomes fed.
ALARM_THRESHOLD = 10.0

#: Level at which the alarm reading a MEASUREMENT link becomes fed. The ramp's
#: tank starts at 6 and rises at 1, so it is crossed at t = 2.
MEASURED_THRESHOLD = 8.0

#: Date the measured level crosses MEASURED_THRESHOLD.
MEASURED_DATE = 2.0

#: Level below which the mixed component's ``hot`` output stays unfed.
HOT_THRESHOLD = 5.0

#: What the mixed component's threshold input is fed with: above HOT_THRESHOLD
#: from t = 0, which is what the seeding start method has to catch.
HOT_SUPPLY = 8.0

#: What the mixed component's consumed input is fed with.
NOMINAL_SUPPLY = 12.0

#: What the never-crossing alarm is fed with: comfortably below its bound.
QUIET_SUPPLY = 3.0

#: A pure observer has no output to map a demand back from, so it claims the
#: demand it is DECLARED with -- large enough not to throttle what it watches.
OBSERVER_DEMAND = 1e6

# -- One supply shared by two consumers, watched at a threshold in between
#: What the shared producer publishes to all of its consumers.
ALLOC_SUPPLY = 10.0
#: What the gated consumer asks for, through the R34 mapping of its own output.
ALLOC_GATE_DEMAND = 20.0
#: What the other consumer asks for. 20 against 10 splits the supply 2:1 ...
ALLOC_RIVAL_DEMAND = 10.0
#: ... so the gated consumer is allocated this much of it.
ALLOC_GATE_SHARE = (
    ALLOC_SUPPLY * ALLOC_GATE_DEMAND / (ALLOC_GATE_DEMAND + ALLOC_RIVAL_DEMAND)
)
#: The bound both the guard and the discrete threshold compare against. Between
#: the share actually allocated and the total the producer publishes, which is
#: what makes the two readings disagree.
ALLOC_THRESHOLD = 8.0


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class IopRateSource(muscadet.ObjFlow):
    """A continuous producer holding the rates it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for name, rate in kwargs.get("rates", {}).items():
            self.add_flow_continuous_out(name=name, var_fed_default=rate)


class IopDemandSink(muscadet.ObjFlow):
    """A pure consumer of one flow, publishing the demand it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "q"),
            var_demand_default=kwargs.get("demand", 0.0),
        )


class IopAllocGate(muscadet.ObjFlow):
    """Both R21 and R22 over ONE continuous input, under an active allocation.

    The rules consume ``q`` and are guarded on it; the discrete ``alarm`` is
    conditioned on the same bound. Both readings must be the share this
    component is allocated -- the quantity its own rules draw on.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="p")
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="alarm",
                var_prod_cond=[{"name": "q", "op": ">=", "value": ALLOC_THRESHOLD}],
            )
        )
        self.add_rules(
            name="gate",
            rules=[
                dict(
                    name="rich",
                    cond=[{"name": "q", "op": ">=", "value": ALLOC_THRESHOLD}],
                    cons={"q": 1.0},
                    prod={"p": 1.0},
                ),
                dict(
                    name="lean",
                    cond=[{"name": "q", "op": "<", "value": ALLOC_THRESHOLD}],
                    cons={"q": 0.5},
                    prod={"p": 0.5},
                ),
            ],
        )


class IopSwitch(muscadet.ObjFlow):
    """A DISCRETE producer of the control signal a rule guard reads (R21)."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="cmd", var_prod_default=kwargs.get("on", True))


class IopRamp(muscadet.ObjFlow):
    """A continuous output that ramps: ``level`` = ``init`` + t.

    The ramp comes from an INTEGRATED level, which is what gives the solver
    something to root-find the crossing on. Its own equation replaces the rule
    evaluation entirely, so the internal tank needs no rule to drain it.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="fill")
        self.add_flow_continuous_out(name="level")
        self.add_capacity(
            name="tank",
            flow="fill",
            capacity=1e6,
            side="in",
            content_init={"fill": kwargs.get("init", 6.0)},
        )

    def compute_production(self):
        self.flows_out["level"].var_fed.setValue(
            self.capacities["tank"].get_quantity("fill")
        )


class IopThreshold(muscadet.ObjFlow):
    """A DISCRETE output conditioned on a CONTINUOUS input (R22).

    The whole declaration of a threshold alarm: one continuous input, one
    discrete output, and an operand carrying the comparison. No component code
    reads the level, and no equation is written by hand.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="level", var_demand_default=OBSERVER_DEMAND)
        self.add_flow(
            dict(
                cls="FlowOut",
                name="alarm",
                var_prod_cond=[
                    {
                        "name": "level",
                        "op": ">=",
                        "value": kwargs.get("threshold", ALARM_THRESHOLD),
                    }
                ],
            )
        )


class IopLevelAlarm(muscadet.ObjFlow):
    """The same declaration, on a level read over a MEASUREMENT link (R33).

    A measurement link is not a flow -- it carries no quantity and enters no
    allocation -- but what it publishes is a continuous quantity all the same,
    so the very same comparison operand thresholds it. This is the shape a
    sensor component takes.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="tank")
        self.add_flow(
            dict(
                cls="FlowOut",
                name="high",
                var_prod_cond=[
                    {"name": "tank", "op": ">=", "value": MEASURED_THRESHOLD}
                ],
            )
        )


class IopMixed(muscadet.ObjFlow):
    """Both families in ONE declaration, in both directions.

    R21: the discrete input ``cmd`` guards the rules producing ``X``.
    R22: the continuous input ``temp`` conditions the discrete output ``hot``.

    The two are deliberately independent -- ``hot`` watches an input no rule
    consumes -- so each direction can be asserted without the other's cascade.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="cmd", logic="and")
        self.add_flow_continuous_in(name="F1")
        self.add_flow_continuous_in(name="temp", var_demand_default=OBSERVER_DEMAND)
        self.add_flow_continuous_out(name="X")
        self.add_flow(
            dict(
                cls="FlowOut",
                name="hot",
                var_prod_cond=[{"name": "temp", "op": ">=", "value": HOT_THRESHOLD}],
            )
        )
        self.add_rules(
            name="X",
            rules=[
                dict(name="run", cond="cmd", cons={"F1": 2}, prod={"X": 1}),
                dict(name="idle", cond="not cmd", prod={"X": 0}),
            ],
        )


class IopDiscreteOnly(muscadet.ObjFlow):
    """A purely discrete component, declared the way it always was.

    Its production condition is the historical list-of-names form: the extended
    operand vocabulary must leave it exactly as it was -- no comparison matrix,
    no threshold automaton, no PDMP registration of its own.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="a", logic="and")
        self.add_flow_in(name="b", logic="and")
        self.add_flow(dict(cls="FlowOut", name="y", var_prod_cond=[["a"], ["b"]]))


class IopBoolSource(muscadet.ObjFlow):
    """A discrete producer of ``a`` and ``b``, on or off as declared."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for name in ("a", "b"):
            self.add_flow_out(name=name, var_prod_default=kwargs.get(name, True))


# ----------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------


def feed(system, target, flow, rate):
    """Wire a dedicated source delivering ``rate`` of ``flow`` into ``target``."""
    name = f"{target}_{flow}_SRC"
    system.add_component(name=name, cls="IopRateSource", rates={flow: rate})
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


def threshold_automata(comp):
    """The threshold automata a component's production conditions compiled to."""
    return [name for name in comp.automata_d if name.endswith("_threshold")]


def snapshot(system):
    """What every watched output and quantity reads right now."""
    return {
        "time": system.currentTime(),
        "alarm": system.comp["ALARM"].flows_out["alarm"].var_fed.value(),
        "alarm_level": system.comp["ALARM"].flows_in["level"].live_value(),
        "high": system.comp["LEVEL"].flows_out["high"].var_fed.value(),
        "measured": system.comp["LEVEL"].measurements_in["tank"].get_level(),
        "quiet": system.comp["QUIET"].flows_out["alarm"].var_fed.value(),
        "quiet_level": system.comp["QUIET"].flows_in["level"].live_value(),
        "mixed_mode": system.comp["MIXED"].rule_sets["X"].mode.active_key(),
        "mixed_hot": system.comp["MIXED"].flows_out["hot"].var_fed.value(),
        "mixed_X": system.comp["MIXED"].flows_out["X"].var_fed.value(),
        "plain_on": system.comp["PLAIN_ON"].flows_out["y"].var_fed.value(),
        "plain_off": system.comp["PLAIN_OFF"].flows_out["y"].var_fed.value(),
    }


def first_stop_where(trace, key, value=True):
    """The first recorded stop at which ``key`` reads ``value``."""
    for index, entry in enumerate(trace):
        if entry[key] == value:
            return index, entry
    return None, None


def stop_at(trace, date):
    """The recorded stop at ``date``."""
    for entry in trace:
        if entry["time"] == pytest.approx(date, abs=CROSSING_TOL):
            return entry
    raise AssertionError(
        f"no stop recorded at t = {date}: {[e['time'] for e in trace]}"
    )


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_declaration_scenario(obs):
    """A malformed comparison operand is refused at declaration, on its own system."""
    system = muscadet.System(name="InteropDeclaration")

    def declare(name, operand):
        try:
            system.add_component(
                name=name,
                cls="IopDeclared",
                operand=operand,
            )
        except Exception as err:
            return err
        return None

    class IopDeclared(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_in(name="level")
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="alarm",
                    var_prod_cond=[kwargs["operand"]],
                )
            )

    obs["err_half_comparison"] = declare("HALF", {"name": "level", "op": ">="})
    obs["err_unknown_operator"] = declare(
        "UNKNOWN", {"name": "level", "op": "~=", "value": 1}
    )
    obs["err_negated_comparison"] = declare(
        "NEGATED", {"name": "level", "negate": True, "op": ">=", "value": 1}
    )

    system.deleteSys()


def run_batch_scenario(obs):
    """A component mixing both families builds and runs on the BATCH path."""
    system = muscadet.System(name="InteropBatch")

    system.add_component(name="SW", cls="IopSwitch", on=True)
    system.add_component(name="MIXED", cls="IopMixed")
    system.connect_flow(source="SW", target="MIXED", flow_name="cmd")
    feed(system, "MIXED", "F1", NOMINAL_SUPPLY)
    feed(system, "MIXED", "temp", HOT_SUPPLY)

    system.simulate({"nb_runs": 1, "schedule": [{"start": 0, "end": 1, "nvalues": 2}]})

    comp = system.comp["MIXED"]
    obs["batch_hot"] = comp.flows_out["hot"].var_fed.value()
    obs["batch_X"] = comp.flows_out["X"].var_fed.value()
    obs["batch_mode"] = comp.rule_sets["X"].mode.active_key()
    obs["batch_threshold_automata"] = threshold_automata(comp)

    system.deleteSys()


def run_allocated_threshold_scenario(obs):
    """A guard and a threshold under an allocation that halves what arrives.

    The producer publishes 10 to all its consumers, and this one is allocated
    6.667 of it. A comparison operand reading the producer's total rather than
    this consumer's share would hold at ``>= 8`` -- selecting a rule the
    component cannot feed and raising an alarm on a quantity it never got --
    while the rule it guards drew on 6.667. The divergence appears exactly
    under the shortage the allocation layer exists for.
    """
    system = muscadet.System(name="InteropAllocatedThreshold")

    system.add_component(name="SRC", cls="IopRateSource", rates={"q": ALLOC_SUPPLY})
    system.add_component(name="GATE", cls="IopAllocGate")
    system.add_component(
        name="RIVAL", cls="IopDemandSink", flow="q", demand=ALLOC_RIVAL_DEMAND
    )
    system.add_component(
        name="PSINK", cls="IopDemandSink", flow="p", demand=ALLOC_GATE_DEMAND
    )

    system.connect_flow(source="SRC", target="GATE", flow_name="q")
    system.connect_flow(source="SRC", target="RIVAL", flow_name="q")
    system.connect_flow(source="GATE", target="PSINK", flow_name="p")

    add_clock(system.comp["RIVAL"], HORIZON)

    system.isimu_start()
    for _ in range(20):
        system.isimu_step_forward()
        if system.currentTime() >= HORIZON:
            break

    gate = system.comp["GATE"]
    flow_in = gate.flows_in["q"]

    obs["alloc_threshold"] = {
        "published": system.comp["SRC"].flows_out["q"].var_fed.value(),
        "delivered": flow_in.get_delivered(),
        "live": flow_in.live_value(),
        "mode": gate.rule_sets["gate"].active_rule().name,
        "alarm": gate.flows_out["alarm"].var_fed.value(),
    }

    system.isimu_stop()
    system.deleteSys()


def build_interop_system():
    """Every interoperation scenario, in one system driven through one session."""
    system = muscadet.System(name="InteropSys")

    # -- R22: a continuous input ramping across the alarm's bound
    system.add_component(name="RAMP", cls="IopRamp", init=6.0)
    system.add_component(name="ALARM", cls="IopThreshold", threshold=ALARM_THRESHOLD)
    system.connect_flow(source="RAMP", target="ALARM", flow_name="level")

    # -- R22: the same declaration, on the level read over a measurement link
    system.add_component(name="LEVEL", cls="IopLevelAlarm")
    system.connect("RAMP", "tank_level_out", "LEVEL", "tank_level_in")

    # -- R22: the same declaration on a quantity that never crosses
    system.add_component(name="QUIET", cls="IopThreshold", threshold=ALARM_THRESHOLD)
    feed(system, "QUIET", "level", QUIET_SUPPLY)

    # -- Both directions on one component, with a control signal dropping at 3
    system.add_component(name="SW_FLIP", cls="IopSwitch", on=True)
    system.add_component(name="MIXED", cls="IopMixed")
    system.connect_flow(source="SW_FLIP", target="MIXED", flow_name="cmd")
    feed(system, "MIXED", "F1", NOMINAL_SUPPLY)
    feed(system, "MIXED", "temp", HOT_SUPPLY)
    system.comp["SW_FLIP"].add_delay_failure_mode(
        name="flip",
        failure_time=SWITCH_DATE,
        failure_effects=[("cmd_fed_available_out", False)],
        repair_time=1e6,
    )

    # -- A purely discrete component, twice, on either side of its condition
    system.add_component(name="BOOL_ON", cls="IopBoolSource", a=True, b=True)
    system.add_component(name="BOOL_OFF", cls="IopBoolSource", a=True, b=False)
    system.add_component(name="PLAIN_ON", cls="IopDiscreteOnly")
    system.add_component(name="PLAIN_OFF", cls="IopDiscreteOnly")
    for flow_name in ("a", "b"):
        system.connect_flow(source="BOOL_ON", target="PLAIN_ON", flow_name=flow_name)
        system.connect_flow(source="BOOL_OFF", target="PLAIN_OFF", flow_name=flow_name)

    add_clock(system.comp["ALARM"], HORIZON)
    add_clock(system.comp["MIXED"], SETTLE_DATE)

    return system


def run_interop_scenario(obs):
    """Drive the interoperation system to the horizon, recording every stop."""
    system = build_interop_system()

    system.isimu_start()

    # The ramp's tank fills at 1 per unit of time.
    system.comp["RAMP"].capacities["tank"].set_inflow("fill", 1.0)

    trace = [snapshot(system)]
    for _ in range(60):
        system.isimu_step_forward()
        trace.append(snapshot(system))
        if system.currentTime() >= HORIZON:
            break

    obs["trace"] = trace
    obs["settled"] = trace[-1]

    system.isimu_stop()

    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive the three systems in turn, snapshotting what each produced."""
    obs = {}

    run_declaration_scenario(obs)
    run_batch_scenario(obs)
    run_allocated_threshold_scenario(obs)

    # Kept alive for the teardown test, per the module convention.
    run_interop_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# R21 -- a discrete flow gates a continuous rule
# ----------------------------------------------------------------------


def test_a_discrete_input_switching_state_changes_the_active_rule(the_run):
    """R21: ``cmd`` drops at t = 3 and the rule set follows it there.

    Nothing new was declared for this direction: a guard operand already reads a
    discrete flow's state, and a discrete flow already announces its own change.
    What this asserts is that it still holds on a component that ALSO carries a
    comparison against a continuous quantity.
    """
    trace = the_run["trace"]

    settled = stop_at(trace, SETTLE_DATE)
    assert settled["mixed_mode"] == 0, "the guarded 'run' rule holds while cmd is true"

    index, entry = first_stop_where(trace, "mixed_mode", 1)
    assert entry is not None, "the rule set must follow the discrete flow"
    assert entry["time"] == pytest.approx(SWITCH_DATE, abs=CROSSING_TOL)


def test_the_rule_the_discrete_input_selects_is_the_one_evaluated(the_run):
    """The mode picks the coefficients, and the production follows.

    While ``cmd`` holds, 2 of F1 make 1 of X and F1 is delivered at 12. Once it
    drops, the complementary rule produces nothing at all.
    """
    trace = the_run["trace"]

    assert stop_at(trace, SETTLE_DATE)["mixed_X"] == pytest.approx(NOMINAL_SUPPLY / 2)
    assert the_run["settled"]["mixed_mode"] == 1
    assert the_run["settled"]["mixed_X"] == pytest.approx(0.0)


# ----------------------------------------------------------------------
# R22 -- a continuous quantity conditions a discrete output
# ----------------------------------------------------------------------


def test_a_discrete_output_becomes_fed_when_its_input_crosses_the_threshold(the_run):
    """The alarm's input ramps from 6 at rate 1 and reaches 10 at t = 4.

    Below the bound the output is unfed; above it, it is fed. The whole
    declaration is one operand -- ``{"name": "level", "op": ">=", "value": 10}``
    -- on the discrete output's production condition.
    """
    trace = the_run["trace"]

    assert trace[0]["alarm"] is False, "the bound is not reached at t = 0"

    index, entry = first_stop_where(trace, "alarm")
    assert entry is not None, "the ramping input must cross its bound"
    assert entry["alarm_level"] >= ALARM_THRESHOLD
    assert the_run["settled"]["alarm"] is True


def test_the_crossing_is_detected_at_the_crossing(the_run):
    """The transition is watched, so the solver stops the integration there.

    The stop right before it is the discrete switch at t = 3, a whole time unit
    earlier, and the next one is the t = 10 horizon: the run walks events, not
    an integration grid. Unwatched, the alarm would first be seen fed six time
    units late, by an amount that depends on the step size.
    """
    trace = the_run["trace"]

    index, entry = first_stop_where(trace, "alarm")
    assert entry is not None
    assert entry["time"] == pytest.approx(CROSSING_DATE, abs=CROSSING_TOL)
    assert entry["time"] < HORIZON

    previous = trace[index - 1]
    assert previous["time"] <= SWITCH_DATE + CROSSING_TOL
    assert previous["alarm"] is False


def test_a_measured_level_conditions_a_discrete_output_the_same_way(the_run):
    """R22 on a level read over a measurement link, not over a flow.

    The observed tank starts at 6 and rises at 1, so the bound of 8 is crossed
    at t = 2 -- again at the crossing, on a component that declares no flow of
    its own beyond the discrete output it drives. This is what a sensor
    component is made of.
    """
    trace = the_run["trace"]

    assert trace[0]["high"] is False

    index, entry = first_stop_where(trace, "high")
    assert entry is not None, "the observed level must cross its bound"
    assert entry["time"] == pytest.approx(MEASURED_DATE, abs=CROSSING_TOL)
    assert entry["measured"] >= MEASURED_THRESHOLD
    assert the_run["settled"]["high"] is True


def test_a_threshold_that_is_never_crossed_leaves_the_output_unfed(the_run):
    """The same declaration on a quantity delivered at 3, against a bound of 10.

    It stays unfed at every single stop of the run -- the condition is a
    comparison, not a "there is something on this input" test.
    """
    trace = the_run["trace"]

    assert all(entry["quiet"] is False for entry in trace)
    assert all(
        entry["quiet_level"] == pytest.approx(QUIET_SUPPLY) for entry in trace[1:]
    )


def test_a_threshold_already_met_at_t0_feeds_the_output_from_the_start(the_run):
    """``temp`` is delivered at 8 against a bound of 5, and never moves.

    Nothing ever CHANGES on that input, so no change-subscription would fire:
    the value is seeded by a start method, the way a negated operand's is.
    """
    trace = the_run["trace"]

    assert trace[0]["mixed_hot"] is True
    assert all(entry["mixed_hot"] is True for entry in trace)


# ----------------------------------------------------------------------
# One component, both families
# ----------------------------------------------------------------------


def test_a_component_mixing_both_families_builds_and_simulates(the_run):
    """``IopMixed`` declares a guarded rule set AND a compared discrete output.

    Asserted on the BATCH path, on its own system: the two directions are
    declared side by side in one ``add_flows``, and a run completes.
    """
    assert the_run["batch_hot"] is True
    assert the_run["batch_mode"] == 0
    assert the_run["batch_X"] == pytest.approx(NOMINAL_SUPPLY / 2)

    # One comparison operand -> exactly one threshold automaton.
    assert len(the_run["batch_threshold_automata"]) == 1


def test_the_comparison_compiles_to_one_watched_two_state_automaton(the_run):
    """What a comparison operand adds to the component, and nothing more."""
    comp = the_run["system"].comp["ALARM"]
    flow = comp.flows_out["alarm"]

    assert flow.var_prod_cond_compare == [[{"op": ">=", "value": ALARM_THRESHOLD}]]
    assert flow.var_prod_cond_negate == []

    names = threshold_automata(comp)
    assert names == ["ALARM_alarm_cond_0_0_threshold"]

    aut = comp.automata_d[names[0]]
    assert [state.name for state in aut.states] == [
        "alarm_cond_0_0_below",
        "alarm_cond_0_0_above",
    ]
    # Instantaneous, so the mode settles AT the crossing and not after it.
    assert [transition.occ_law.time for transition in aut.transitions] == [0, 0]


def test_the_condition_renders_the_comparison_it_was_declared_with(the_run):
    """The textual rendering names the operand as the comparison it is."""
    flow = the_run["system"].comp["ALARM"].flows_out["alarm"]

    assert "level >= 10" in repr(flow)


# ----------------------------------------------------------------------
# A discrete-only component is untouched
# ----------------------------------------------------------------------


def test_a_discrete_only_component_is_unaffected_by_the_extended_operand(the_run):
    """The historical declaration keeps the historical wiring.

    An empty comparison matrix is what selects the pre-existing boolean
    closures, so a model that declares no comparison evaluates exactly as it
    did -- and gains no automaton and no solver registration.
    """
    system = the_run["system"]

    for name in ("PLAIN_ON", "PLAIN_OFF"):
        comp = system.comp[name]
        flow = comp.flows_out["y"]

        assert flow.var_prod_cond_compare == []
        assert flow.var_prod_cond_negate == []
        assert threshold_automata(comp) == []

    # ... and the condition still evaluates as the conjunction it always was.
    settled = the_run["settled"]
    assert settled["plain_on"] is True
    assert settled["plain_off"] is False


# ----------------------------------------------------------------------
# What the declaration refuses
# ----------------------------------------------------------------------


def test_a_half_declared_comparison_is_refused(the_run):
    """``op`` without ``value`` leaves the threshold undefined."""
    error = the_run["err_half_comparison"]

    assert isinstance(error, ValueError)
    assert "'op' and 'value' must be given together" in str(error)


def test_an_unknown_comparison_operator_is_refused(the_run):
    """The six operators are the ones a rule guard uses, and no others."""
    error = the_run["err_unknown_operator"]

    assert isinstance(error, ValueError)
    assert "~=" in str(error)


def test_a_negated_comparison_is_refused(the_run):
    """Same rule as on a guard operand: invert the operator instead."""
    error = the_run["err_negated_comparison"]

    assert isinstance(error, ValueError)
    assert "use the opposite comparison operator instead" in str(error)


def test_a_comparison_operand_reads_this_consumers_allocated_share(the_run):
    """R21 and R22 both read what THIS component gets, not the producer's total.

    The producer publishes 10 to its two consumers and this one is allocated
    6.667 of it, below the bound of 8 that its guard and its alarm compare
    against. Reading the published total instead would put both above the
    bound: the guard would select the rule consuming twice as much as the
    component can be served, and the alarm would fire on a quantity that never
    arrived.
    """
    reading = the_run["alloc_threshold"]

    # The producer really does publish more than this consumer receives ...
    assert reading["published"] == pytest.approx(ALLOC_SUPPLY)
    assert reading["delivered"] == pytest.approx(ALLOC_GATE_SHARE)
    assert reading["delivered"] < ALLOC_THRESHOLD < reading["published"]

    # ... and the operand reads the share, so it agrees with what the rules draw
    assert reading["live"] == pytest.approx(reading["delivered"])

    # Both consumers of the one operand therefore fall on the same side of it
    assert reading["mode"] == "lean"
    assert reading["alarm"] is False


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
