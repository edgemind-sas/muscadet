"""A production condition on a CONTINUOUS output (R44).

An actuator is commanded the same way whatever the nature of the flow it
carries. Until now ``var_prod_cond`` belonged to the discrete family alone, so
a continuous source gated by a control port had no declaration: the modeller
wrote one, ``FlowContinuous.check_declaration_keys`` refused it by name, and
the only way out was a recipe guard.

**The condition is a factor, not an availability channel.** A continuous
output states its total loss as a rate of ZERO (R19, KD10), and that invariant
survives here: the condition multiplies the production factor alongside the
time profile and the effective rate, so a false condition gives a rate of zero
rather than a parallel boolean gate. What composes by MINIMUM stays the
deratings among themselves; everything else multiplies.

Three operand forms are exercised, the very ones a discrete output accepts:

* a plain boolean read of a discrete input (the control port);
* the same read NEGATED, so a signal can inhibit rather than enable;
* a COMPARISON against a continuous quantity (R22), which is what lets a
  regulation gate a rate on a level.

The comparison compiles to the same WATCHED two-state automaton R22 builds for
a discrete output, and for the same reason: a continuous quantity crossing a
threshold inside an integration step announces no change of its own, so nothing
would re-run the condition there. The boolean forms need none of it -- a
discrete flow announces its own change.

``THROTTLE`` crosses its bound at t = 4 (its input ramps from 6 at 1 per unit)
while the only other stop in the run is the control signal dropping at t = 3,
so a threshold the solver did not watch would be seen a whole step late.
"""

import muscadet
import cod3s
import pytest

from muscadet import declare, ordering

#: Horizon the interactive session runs to.
CPC_HORIZON = 8.0

#: An early stop, before anything moves. The nominal regime is read THERE and
#: not at t = 0: the production sweep has not run before the first step, so at
#: t = 0 every output still holds the rate it was declared with.
CPC_SETTLE_DATE = 1.0

#: Date the control signal drops.
CPC_SWITCH_DATE = 3.0

#: A stop just after the drop. The state recorded AT a transition's own date is
#: the one the transition fires from; what it produced is read at the next stop.
CPC_AFTER_SWITCH = 3.5

#: Level at which the throttled output starts producing. Its input ramps from
#: 6.0 at 1 per time unit, so it is crossed at t = 4.
CPC_THRESHOLD = 10.0

#: Date the ramp crosses CPC_THRESHOLD.
CPC_CROSSING_DATE = 4.0

#: The solver stops ON the crossing rather than refining it to machine
#: precision, so dates are asserted within one default PDMP step.
CPC_TOL = 0.05

#: Nominal rate every gated output carries.
CPC_RATE = 5.0

#: Constant factor of the profile the commanded source also declares, so the
#: product of the three terms is observable rather than inferred.
CPC_PROFILE = 0.5

#: What a sink asks for: large enough never to be the binding constraint.
CPC_DEMAND = 1000.0

#: Rate feeding the ramp's tank, hence its slope.
CPC_FILL_RATE = 1.0

#: The ramp's initial level.
CPC_RAMP_INIT = 6.0

#: A profile object SHARED by declaration rather than built per flow. Its
#: identity is what a deep copy of the declaration would quietly break.
SHARED_PROFILE = muscadet.SinusoidalProfile(amplitude=0.0, offset=1.0)


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class CpcSink(muscadet.ObjFlow):
    """A pure consumer publishing an ample demand."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "q"), var_demand_default=CPC_DEMAND
        )


class CpcSwitch(muscadet.ObjFlow):
    """A DISCRETE producer of the control signal the gates read."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="cmd", var_prod_default=True)


class CpcCommanded(muscadet.ObjFlow):
    """A continuous output gated by a discrete control port, with a profile.

    The declaration under test, in its shortest form: one discrete input, one
    continuous output, and the plain operand naming the port. Declared through
    the KWARGS entry point (``add_flow_continuous_out``), which is one of the
    two doors and the one that used not to normalise anything.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="cmd")
        self.add_flow_continuous_out(
            name="p",
            var_fed_default=CPC_RATE,
            var_prod_cond=["cmd"],
            profile=muscadet.SinusoidalProfile(amplitude=0.0, offset=CPC_PROFILE),
        )


class CpcInhibited(muscadet.ObjFlow):
    """The mirror: a continuous output the same signal INHIBITS.

    Declared through the DICT entry point (``add_flow``), the other door, so
    both are exercised on the same run.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="cmd")
        self.add_flow(
            dict(
                cls="FlowContinuousOut",
                name="p",
                var_fed_default=CPC_RATE,
                var_prod_cond=[{"name": "cmd", "negate": True}],
            )
        )


class CpcRamp(muscadet.ObjFlow):
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
            content_init={"fill": CPC_RAMP_INIT},
        )

    def compute_production(self):
        self.flows_out["level"].var_fed.setValue(
            self.capacities["tank"].get_quantity("fill")
        )


class CpcThrottle(muscadet.ObjFlow):
    """A continuous output gated by a COMPARISON on an observed level (R22).

    The declaration a regulation takes: produce only while the observed level
    is at or above the bound. No component code reads the level and no equation
    is written by hand.

    The level arrives over a MEASUREMENT link, which is the shape a sensor
    takes: it carries a continuous quantity without entering the allocation, so
    the component states no transformation rule between an input it consumes
    and an output it produces -- it has no such input.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="tank")
        self.add_flow_continuous_out(
            name="q",
            var_fed_default=CPC_RATE,
            var_prod_cond=[{"name": "tank", "op": ">=", "value": CPC_THRESHOLD}],
        )


class CpcConjunction(muscadet.ObjFlow):
    """An output gated by an AND of ORs, the canonical CNF shape.

    ``(a or b) and c``: what the outer/inner structure means must not change
    with the family that declares it.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for name in ("a", "b", "c"):
            self.add_flow_in(name=name)
        self.add_flow_continuous_out(
            name="p",
            var_fed_default=CPC_RATE,
            var_prod_cond=[["a", "b"], ["c"]],
        )


class CpcRelay(muscadet.ObjFlow):
    """A comparison reaching a discrete signal THROUGH a continuous output.

    One hop longer than the shape the detector was written for: ``f`` is
    compared in the production condition of the continuous ``g``, and the
    discrete ``s`` thresholds ``g``. The chain f -> g -> s leaves this
    component as a discrete signal exactly as before, but the middle hop is a
    continuous output the propagation used not to see at all.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="f", var_demand_default=CPC_DEMAND)
        self.add_flow_continuous_out(
            name="g",
            var_fed_default=CPC_RATE,
            var_prod_cond=[{"name": "f", "op": ">=", "value": CPC_THRESHOLD}],
        )
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="s",
                var_prod_cond=[{"name": "g", "port": "out", "op": ">", "value": 0.0}],
            )
        )


class CpcMethodBound(muscadet.ObjFlow):
    """Declares its output with objects bound to the component itself.

    Both forms are documented by ``add_flow_continuous_out`` and both are what
    a deep copy of the declaration destroys.
    """

    def split_evenly(self, available, demands):
        share = available / len(demands) if demands else 0.0
        return {name: share for name in demands}

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name="p",
            var_fed_default=CPC_RATE,
            profile=SHARED_PROFILE,
            allocation_fun=self.split_evenly,
        )


class CpcLoopSensor(muscadet.ObjFlow):
    """Compares a continuous input and emits the discrete verdict (R22)."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=CPC_DEMAND)
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="sig",
                var_prod_cond=[{"name": "q", "op": ">=", "value": CPC_THRESHOLD}],
            )
        )


class CpcLoopSource(muscadet.ObjFlow):
    """A continuous output gated by the very signal derived from its own rate.

    The instantaneous loop the rate-comparison check exists for: ``q`` is a
    function of ``sig`` read this instant, and ``sig`` is a function of ``q``
    read this instant, with nothing integrated in between.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="sig")
        self.add_flow_continuous_out(
            name="q", var_fed_default=CPC_RATE, var_prod_cond=["sig"]
        )


class CpcBoolTriple(muscadet.ObjFlow):
    """Three independent discrete signals, each declared on or off."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for name in ("a", "b", "c"):
            self.add_flow_out(name=name, var_prod_default=kwargs.get(name, True))


# ----------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------


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
    """What every gated output delivers right now."""
    return {
        "time": system.currentTime(),
        "commanded": system.comp["CMD"].flows_out["p"].var_fed.value(),
        "inhibited": system.comp["INH"].flows_out["p"].var_fed.value(),
        "throttled": system.comp["THROTTLE"].flows_out["q"].var_fed.value(),
        "observed": system.comp["THROTTLE"].measurements_in["tank"].get_level(),
        "conjunction": system.comp["CNF"].flows_out["p"].var_fed.value(),
    }


def stop_at(trace, date):
    """The recorded stop at ``date``."""
    for entry in trace:
        if entry["time"] == pytest.approx(date, abs=CPC_TOL):
            return entry
    raise AssertionError(f"no stop at t = {date}: {[e['time'] for e in trace]}")


def build_cpc_system():
    """Every gated output, in one system driven through one session."""
    system = muscadet.System(name="CpcSys")

    # -- a control signal that drops at CPC_SWITCH_DATE
    system.add_component(name="SW", cls="CpcSwitch")
    system.comp["SW"].add_delay_failure_mode(
        name="drop",
        failure_time=CPC_SWITCH_DATE,
        failure_effects=[("cmd_fed_available_out", False)],
        repair_cond=False,
    )

    # -- the plain operand, and its negation, on the same signal
    for name, cls in (("CMD", "CpcCommanded"), ("INH", "CpcInhibited")):
        system.add_component(name=name, cls=cls)
        system.connect_flow(source="SW", target=name, flow_name="cmd")
        system.add_component(name=f"{name}_SINK", cls="CpcSink", flow="p")
        system.connect_flow(source=name, target=f"{name}_SINK", flow_name="p")

    # -- the comparison operand, on a level ramping across its bound
    system.add_component(name="RAMP", cls="CpcRamp")
    system.add_component(name="THROTTLE", cls="CpcThrottle")
    system.connect("RAMP", "tank_level_out", "THROTTLE", "tank_level_in")
    system.add_component(name="Q_SINK", cls="CpcSink", flow="q")
    system.connect_flow(source="THROTTLE", target="Q_SINK", flow_name="q")

    # -- the CNF shape: (a or b) and c, with b and c on and a off
    system.add_component(name="TRIPLE", cls="CpcBoolTriple", a=False, b=True, c=True)
    system.add_component(name="CNF", cls="CpcConjunction")
    for name in ("a", "b", "c"):
        system.connect_flow(source="TRIPLE", target="CNF", flow_name=name)
    system.add_component(name="CNF_SINK", cls="CpcSink", flow="p")
    system.connect_flow(source="CNF", target="CNF_SINK", flow_name="p")

    system.comp["RAMP"].capacities["tank"].set_inflow("fill", CPC_FILL_RATE)

    add_clock(system.comp["CMD_SINK"], CPC_SETTLE_DATE)
    add_clock(system.comp["INH_SINK"], CPC_AFTER_SWITCH)
    add_clock(system.comp["Q_SINK"], CPC_HORIZON)

    return system


def run_cpc_session(obs):
    """Drive the system and record what every gated output reads at each stop."""
    system = build_cpc_system()

    obs["throttle_automata"] = threshold_automata(system.comp["THROTTLE"])
    obs["commanded_automata"] = threshold_automata(system.comp["CMD"])

    system.isimu_start()
    trace = []

    for _ in range(40):
        system.isimu_step_forward()
        trace.append(snapshot(system))
        if system.currentTime() >= CPC_HORIZON:
            break

    obs["trace"] = trace

    system.isimu_stop()
    system.deleteSys()


# ----------------------------------------------------------------------
# Fixture
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def the_run():
    obs = {}
    try:
        run_cpc_session(obs)
    except Exception:
        try:
            cod3s.terminate_session()
        except Exception:
            pass
        raise
    return obs


# ----------------------------------------------------------------------
# The condition gates the rate
# ----------------------------------------------------------------------


def test_a_commanded_output_produces_while_its_control_port_is_fed(the_run):
    """The whole point: a continuous source obeys a boolean command."""
    settled = stop_at(the_run["trace"], CPC_SETTLE_DATE)

    assert settled["commanded"] == pytest.approx(CPC_RATE * CPC_PROFILE)


def test_the_condition_is_re_read_and_the_rate_falls_when_the_command_drops(the_run):
    """Not frozen at declaration: the gate follows the signal during the run."""
    after = stop_at(the_run["trace"], CPC_AFTER_SWITCH)

    assert after["commanded"] == pytest.approx(0.0)


def test_a_false_condition_gives_a_zero_rate_and_not_an_error(the_run):
    """R19 survives: total loss of production is a rate of ZERO.

    Asserted as the pair of regimes each gated output actually visits over the
    run, and not as a bound every implementation would satisfy: a gate that did
    nothing would leave one single regime on each of them.
    """
    for key, gated in (("commanded", CPC_RATE * CPC_PROFILE), ("inhibited", CPC_RATE)):
        seen = {round(entry[key], 9) for entry in the_run["trace"]}

        assert seen == {0.0, round(gated, 9)}, f"{key} never visited both regimes"


def test_the_gate_multiplies_the_profile_rather_than_replacing_it(the_run):
    """Three independent terms composed by PRODUCT, never folded together.

    A gate that composed by minimum would give the profile factor (0.5) rather
    than the product (2.5), and one that replaced the profile would give the
    nominal rate (5.0).
    """
    settled = stop_at(the_run["trace"], CPC_SETTLE_DATE)

    assert settled["commanded"] == pytest.approx(CPC_RATE * CPC_PROFILE)
    assert settled["commanded"] != pytest.approx(CPC_RATE)
    assert settled["commanded"] != pytest.approx(CPC_PROFILE)


def test_a_negated_operand_inhibits_instead_of_enabling(the_run):
    """The mirror of the plain operand, on the very same signal."""
    settled = stop_at(the_run["trace"], CPC_SETTLE_DATE)
    after = stop_at(the_run["trace"], CPC_AFTER_SWITCH)

    assert settled["inhibited"] == pytest.approx(0.0)
    assert after["inhibited"] == pytest.approx(CPC_RATE)


def test_the_conjunctive_shape_keeps_its_meaning(the_run):
    """``(a or b) and c`` with a off, b on, c on: the output produces."""
    settled = stop_at(the_run["trace"], CPC_SETTLE_DATE)

    assert settled["conjunction"] == pytest.approx(CPC_RATE)


# ----------------------------------------------------------------------
# A comparison operand, and the crossing it must be watched at
# ----------------------------------------------------------------------


def test_a_comparison_operand_gates_a_continuous_output(the_run):
    """The regulation shape: produce only above the observed bound."""
    settled = stop_at(the_run["trace"], CPC_SETTLE_DATE)
    end = the_run["trace"][-1]

    assert settled["observed"] < CPC_THRESHOLD
    assert settled["throttled"] == pytest.approx(0.0)
    assert end["observed"] > CPC_THRESHOLD
    assert end["throttled"] == pytest.approx(CPC_RATE)


def test_the_crossing_is_detected_at_the_crossing(the_run):
    """A threshold the solver did not watch would be seen a whole step late."""
    crossing = stop_at(the_run["trace"], CPC_CROSSING_DATE)

    assert crossing["observed"] == pytest.approx(CPC_THRESHOLD, abs=CPC_TOL)
    assert crossing["throttled"] == pytest.approx(CPC_RATE)


def test_the_comparison_compiles_to_one_watched_two_state_automaton(the_run):
    """One automaton per comparison operand, and none for a boolean one."""
    assert len(the_run["throttle_automata"]) == 1
    assert the_run["commanded_automata"] == []


# ----------------------------------------------------------------------
# Declaration: what the two doors normalise, and what stays refused
# ----------------------------------------------------------------------


def test_a_continuous_output_accepts_a_production_condition():
    """The refusal this ticket lifts, at its narrowest.

    In the STORED form, which is a list of groups: this field holds what the
    door resolved, never what the modeller wrote.
    """
    flow = muscadet.FlowContinuousOut(name="q", var_prod_cond=[["ctrl"]])

    assert flow.var_prod_cond == [["ctrl"]]


def test_a_flat_production_condition_is_refused_at_declaration():
    """A string is iterable, and that is what makes the flat form dangerous.

    ``["ctrl"]`` read as a list of groups makes each CHARACTER an operand, so
    the gate would read four readers built over 'c', 't', 'r' and 'l'. Refused
    at declaration rather than found on the first integration step.
    """
    with pytest.raises(ValueError, match="list of GROUPS"):
        muscadet.FlowContinuousOut(name="q", var_prod_cond=["ctrl"])


def test_a_continuous_output_still_refuses_the_discrete_production_default():
    """``var_prod_default`` names a boolean production variable a continuous
    output does not have, so lifting one key must not lift its neighbour."""
    with pytest.raises(ValueError, match="does not accept declaration key"):
        muscadet.FlowContinuousOut(name="q", var_prod_default=True)


def test_both_declaration_doors_resolve_the_operand_to_a_flow():
    """The defect is in the call, not the function.

    ``add_flow`` normalises through ``postprocess_flow_specs``; the kwargs door
    ``add_flow_continuous_out`` did not, so an operand declared there stayed the
    string it was written as and the gate read nothing. Both doors are checked
    on the SAME condition, which is the only way the asymmetry shows.
    """
    system = muscadet.System(name="CpcDoors")
    try:
        system.add_component(name="KW", cls="CpcCommanded")
        system.add_component(name="DICT", cls="CpcInhibited")

        for name in ("KW", "DICT"):
            cond = system.comp[name].flows_out["p"].var_prod_cond
            assert len(cond) == 1 and len(cond[0]) == 1
            operand = cond[0][0]
            assert not isinstance(operand, str), f"{name} left its operand a string"
            assert operand is system.comp[name].flows_in["cmd"]
    finally:
        system.deleteSys()


# ----------------------------------------------------------------------
# The loop the widened gate must not let through
# ----------------------------------------------------------------------


def test_a_loop_closed_through_a_gated_continuous_output_is_refused():
    """``gates_production_on`` had two ways in, and this is the third (R44).

    It answered True for a rule guard and for a mode automaton watching the
    signal. A production condition on a CONTINUOUS output is a third way this
    component's production depends on a discrete input, and it did not exist
    when the predicate was written -- so the walk reached the component, asked
    whether its production depended on the signal, and was told no.

    The model then builds, chatters at a period set by the integration step,
    and a study silently never finishes rather than being refused.
    """
    system = muscadet.System(name="CpcLoop")
    error = None
    started = False
    try:
        system.add_component(name="LSRC", cls="CpcLoopSource")
        system.add_component(name="LSENS", cls="CpcLoopSensor")
        system.connect_flow(source="LSRC", target="LSENS", flow_name="q")
        system.connect_flow(source="LSENS", target="LSRC", flow_name="sig")

        try:
            system.isimu_start()
            started = True
        except Exception as err:
            error = err
    finally:
        if started:
            system.isimu_stop()
        system.deleteSys()

    assert error is not None, "a rate loop closed through a gated rate must not start"
    assert isinstance(error, muscadet.RateComparisonLoopError)
    assert started is False


def test_a_gated_continuous_output_gates_production_on_its_condition():
    """The predicate itself, read directly: the third way must answer True."""
    system = muscadet.System(name="CpcGates")
    try:
        system.add_component(name="LSRC", cls="CpcLoopSource")

        assert ordering.gates_production_on(system.comp["LSRC"], "sig") is True
    finally:
        system.deleteSys()


def test_a_gated_continuous_output_survives_a_declaration_round_trip():
    """The condition is stored RESOLVED, so it has to be rebuilt to be dumped.

    ``muscadet.declare`` walks every flow of both families and rebuilds the
    declaration form from the three stored fields, so a continuous output gets
    this for free -- and "for free" is exactly what needs a test, since nothing
    in that code names a family. What it must NOT dump is the reader cache: a
    list of closures is what "something no mapping can carry" means, which is
    why the cache is a private attribute rather than a field.
    """
    system = muscadet.System(name="CpcRoundTrip")
    try:
        system.add_component(name="RT", cls="CpcCommanded")
        comp = system.comp["RT"]

        # Warm the cache first: a spec dumped before the gate was ever read
        # would not exercise the case that matters.
        assert comp.flows_out["p"].production_gate() in (0.0, 1.0)

        spec = declare.component_spec(comp)
        flow = next(f for f in spec["flows"] if f["name"] == "p")

        assert flow["cls"] == "FlowContinuousOut"
        assert flow["var_prod_cond"] == [[{"name": "cmd", "port": "in"}]]
        # No private attribute reached the spec, the reader cache above all:
        # a list of closures is what "something no mapping can carry" means.
        assert not any(key.startswith("_") for key in flow)
    finally:
        system.deleteSys()


def test_the_kwargs_door_does_not_copy_what_it_normalises():
    """The regression the normalisation of that door nearly bought.

    ``postprocess_flow_specs`` opens with a ``copy.deepcopy`` of the
    declaration, and a deep copy of a BOUND METHOD copies the component it is
    bound to. Routing the kwargs door through it therefore broke two forms its
    own docstring documents -- a profile built on a method of the component, an
    allocation function of the component -- with a pickling error naming
    neither the parameter nor the flow, and silently gave a shared Profile a
    copy of its own. The door normalises the condition and nothing else.
    """
    system = muscadet.System(name="CpcNoCopy")
    try:
        system.add_component(name="MB", cls="CpcMethodBound")
        flow = system.comp["MB"].flows_out["p"]

        assert flow.profile is SHARED_PROFILE
        assert flow.allocation_fun == system.comp["MB"].split_evenly
    finally:
        system.deleteSys()


# ----------------------------------------------------------------------
# The two seeds that must see a continuous output now that it can compare
# ----------------------------------------------------------------------


def test_a_continuous_output_seeds_the_flow_indexed_walk():
    """Both halves skipped a continuous output, and both had to stop.

    ``gates_production_on`` is where the walk ARRIVES; these two are where it
    STARTS. Widening only the arrival closes nothing: a comparison written on a
    continuous output produced no seed, so the walk never began and the loop
    was reported by nobody.

    The second assertion is the propagation hop: what leaves the component is
    the discrete ``s``, and it is only reachable from ``f`` by passing THROUGH
    the gated continuous ``g``. A fixpoint blind to that middle hop returns
    nothing and the chain breaks one step from its end.
    """
    system = muscadet.System(name="CpcSeedFlow")
    try:
        system.add_component(name="RLY", cls="CpcRelay")
        comp = system.comp["RLY"]

        assert "f" in ordering.compared_continuous_inputs(comp)
        assert ordering.comparison_driven_outputs(comp, "f") == ["s"]
    finally:
        system.deleteSys()


def test_a_continuous_output_seeds_the_observation_walk():
    """The graver half: an observation link is never an edge of the flow graph.

    A comparison on a transported flow still has the continuous graph behind
    it, which refuses a cycle on its own. A comparison on an OBSERVED quantity
    has nothing behind it by construction (KD19), so this seed is what stands
    between a gated rate observed back onto itself and a session that ramps by
    one integration step per stop for ever.

    The seed is what this asserts, and it is the half that was missing here.
    The other half, a verdict that leaves purely as a RATE, was closed by R47
    and closed BESIDE this walk rather than through it: there is no signal to
    follow, so ``find_rate_observation_loops`` asks whether the commanded
    output reaches the observed producer. What a rule guard drives is therefore
    unchanged, which is what the test above still pins.
    """
    system = muscadet.System(name="CpcSeedObs")
    try:
        system.add_component(name="THR", cls="CpcThrottle")
        comp = system.comp["THR"]

        assert ordering.measurement_thresholds(comp, "tank") != []
    finally:
        system.deleteSys()


def test_delete():
    cod3s.terminate_session()
