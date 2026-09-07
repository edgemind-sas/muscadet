"""A boolean operand naming a CONTINUOUS flow normalises into ``!= 0`` (R46).

An operand written without a comparison builds its reader from
``source.var_fed.value()``. On a continuous flow that value is a **rate**, so
the condition means "the rate differs from zero", bit for bit, while the
modeller writing ``{"name": "level"}`` almost certainly wanted a threshold.
Nothing said so, and nothing could tell the two apart afterwards: the matrices
recorded only the ABSENCE of a comparison.

**This was a detection hole, not merely a drift.** Measured before the fix, on
one topology written two ways -- a discrete verdict derived from an arriving
rate, wired back to gate that rate's producer:

===========================================  =============================  =========
Operand on the arriving rate                 ``compared_continuous_inputs``  Start
===========================================  =============================  =========
``{"name": "q", "op": ">=", "value": 5}``    ``{'q': 'q >= 5'}``             REFUSED
``{"name": "q"}``                            ``{}``                          BUILDS
===========================================  =============================  =========

Both spellings read the same rate algebraically and close the same
instantaneous loop; only one of them was collected by the seeds of the loop
walk. Normalising is what closes that, as a direct consequence rather than as a
second mechanism.

**The two vocabularies change together.** R-5 pins that a rule guard and a
production condition accept exactly the same operand shapes, from one
implementation. The normalisation therefore lives beside
``validate_operand_shape`` and both entry points call it.

**It cannot live in a pydantic validator.** ``RuleOperand.flow`` is bound later,
by ``ObjFlow.add_rules``, so a validator cannot know whether the source is
continuous. Both sides normalise at RESOLUTION.
"""

import warnings

import cod3s
import pytest

import muscadet
from muscadet import ordering

BNC_THRESHOLD = 5.0
BNC_HORIZON = 4.0
BNC_RATE = 10.0
BNC_DEMAND = 100.0


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class BncGate(muscadet.ObjFlow):
    """Every operand shape at once, on a continuous input and a discrete one."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=BNC_DEMAND)
        self.add_flow_in(name="d", logic="and")

        self.add_flow(
            dict(cls="FlowDiscreteOut", name="plain", var_prod_cond=[{"name": "q"}])
        )
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="negated",
                var_prod_cond=[{"name": "q", "negate": True}],
            )
        )
        self.add_flow(
            dict(cls="FlowDiscreteOut", name="as_string", var_prod_cond=["q"])
        )
        self.add_flow(
            dict(cls="FlowDiscreteOut", name="discrete", var_prod_cond=[{"name": "d"}])
        )
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="discrete_negated",
                var_prod_cond=[{"name": "d", "negate": True}],
            )
        )
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="compared",
                var_prod_cond=[{"name": "q", "op": ">=", "value": BNC_THRESHOLD}],
            )
        )

        self.add_flow_continuous_out(name="p")
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="on_output",
                var_prod_cond=[{"name": "p", "port": "out"}],
            )
        )
        self.add_rules(
            name="guards",
            rules=[
                dict(name="plain", cond=[{"name": "q"}], prod={"p": 1.0}),
                dict(
                    name="negated",
                    cond=[{"name": "q", "negate": True}],
                    prod={"p": 0.0},
                ),
            ],
        )
        self.add_rules(
            name="discrete_guards",
            rules=[
                dict(name="on", cond=[{"name": "d"}], prod={"p": 0.0}),
                dict(name="off", cond=[{"name": "d", "negate": True}], prod={"p": 0.0}),
            ],
        )


class BncStringGuard(muscadet.ObjFlow):
    """A guard written as an expression STRING, on a continuous input."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=BNC_DEMAND)
        self.add_flow_continuous_out(name="p")
        self.add_rules(
            name="s",
            rules=[
                dict(name="on", cond="q", prod={"p": 1.0}),
                dict(name="off", cond="not q", prod={"p": 0.0}),
            ],
        )


class BncLoopSource(muscadet.ObjFlow):
    """A rate whose production a discrete control port gates."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="run", logic="and")
        self.add_flow_continuous_out(
            name="q", var_fed_default=BNC_RATE, var_prod_cond=["run"]
        )


class BncLoopBool(muscadet.ObjFlow):
    """The verdict derived from the arriving rate, written BOOLEANLY."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=BNC_DEMAND)
        self.add_flow(
            dict(cls="FlowDiscreteOut", name="run", var_prod_cond=[{"name": "q"}])
        )


class BncLoopCompare(muscadet.ObjFlow):
    """The same verdict, written as the comparison it always meant."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=BNC_DEMAND)
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="run",
                var_prod_cond=[{"name": "q", "op": ">=", "value": BNC_THRESHOLD}],
            )
        )


class BncRunSource(muscadet.ObjFlow):
    """A plain continuous source, for the evaluation check."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=BNC_RATE)


class BncRunGate(muscadet.ObjFlow):
    """A discrete alarm gated booleanly on an arriving rate."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=BNC_DEMAND)
        self.add_flow(
            dict(cls="FlowDiscreteOut", name="alarm", var_prod_cond=[{"name": "q"}])
        )


class BncDiscreteGate(muscadet.ObjFlow):
    """The control case: a boolean operand on a DISCRETE flow gains nothing."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="d", logic="and")
        self.add_flow(
            dict(cls="FlowDiscreteOut", name="alarm", var_prod_cond=[{"name": "d"}])
        )


# ----------------------------------------------------------------------
# Observations, taken once
# ----------------------------------------------------------------------


def observe_declarations(obs):
    system = muscadet.System(name="BncDeclarations")
    try:
        system.add_component(name="G", cls="BncGate")
        comp = system.comp["G"]

        for name in (
            "plain",
            "negated",
            "as_string",
            "on_output",
            "discrete",
            "discrete_negated",
            "compared",
        ):
            flow = comp.flows_out[name]
            obs[f"cond_{name}"] = getattr(flow, "var_prod_cond_compare", None) or []
            obs[f"negate_{name}"] = getattr(flow, "var_prod_cond_negate", None) or []

        obs["guards"] = {
            rule.name: [
                {"op": o.op, "value": o.value, "negate": o.negate} for o in rule.cond
            ]
            for rule in comp.rule_sets["guards"].rules
        }
        obs["discrete_guards"] = {
            rule.name: [
                {"op": o.op, "value": o.value, "negate": o.negate} for o in rule.cond
            ]
            for rule in comp.rule_sets["discrete_guards"].rules
        }

        obs["spec"] = muscadet.component_spec(comp)
    finally:
        system.deleteSys()


def observe_string_guard(obs):
    system = muscadet.System(name="BncStringGuard")
    try:
        system.add_component(name="S", cls="BncStringGuard")
        comp = system.comp["S"]
        obs["string_guards"] = {
            rule.name: [
                {"op": o.op, "value": o.value, "negate": o.negate} for o in rule.cond
            ]
            for rule in comp.rule_sets["s"].rules
        }
    finally:
        system.deleteSys()


def observe_rebuild(obs):
    """Rebuild the component from its own spec and compare the stored form."""
    system = muscadet.System(name="BncRebuild")
    try:
        comp = muscadet.build_component(system, obs["spec"])
        obs["rebuilt"] = {
            name: getattr(comp.flows_out[name], "var_prod_cond_compare", None) or []
            for name in ("plain", "negated", "as_string", "discrete")
        }
        obs["rebuilt_spec"] = muscadet.component_spec(comp)
    finally:
        system.deleteSys()


def observe_loop(obs, key, gate_cls):
    system = muscadet.System(name=f"BncLoop{key}")
    try:
        system.add_component(name="S", cls="BncLoopSource")
        system.add_component(name="G", cls=gate_cls)
        system.connect_flow(source="S", target="G", flow_name="q")
        system.connect_flow(source="G", target="S", flow_name="run")

        obs[f"seeds_{key}"] = ordering.compared_continuous_inputs(system.comp["G"])

        obs[f"error_{key}"] = None
        try:
            system.isimu_start()
            obs[f"started_{key}"] = True
        except Exception as err:  # noqa: BLE001 -- the refusal is the assertion
            obs[f"started_{key}"] = False
            obs[f"error_{key}"] = err

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                system.isimu_stop()
            except Exception:  # pragma: no cover -- nothing was started
                pass
    finally:
        system.deleteSys()


def observe_evaluation(obs):
    """The normalised condition must hold exactly where the boolean one did."""
    system = muscadet.System(name="BncEvaluation")
    try:
        system.add_component(name="SRC", cls="BncRunSource")
        system.add_component(name="GATE", cls="BncRunGate")
        system.connect_flow(source="SRC", target="GATE", flow_name="q")
        system.add_component(
            name="HORIZON",
            cls="ObjFlow",
        )
        system.comp["HORIZON"].add_atm2states(
            name="horizon",
            occ_law_12={"cls": "delay", "time": 4.0},
            cond_occ_21=False,
        )

        system.isimu_start()
        gate = system.comp["GATE"]
        alarm = gate.flows_out["alarm"]

        system.isimu_step_to(1.0)
        obs["flowing"] = (
            gate.flows_in["q"].var_fed.value(),
            bool(alarm.var_fed.value()),
        )

        system.comp["SRC"].flows_out["q"].var_out_rate.setValue(0.0)
        system.isimu_step_to(2.0)
        obs["stopped"] = (
            gate.flows_in["q"].var_fed.value(),
            bool(alarm.var_fed.value()),
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            system.isimu_stop()
    finally:
        system.deleteSys()


def observe_watched_automaton(obs):
    """What the normalisation BUILDS, and what that costs an interactive driver."""
    system = muscadet.System(name="BncWatched")
    try:
        system.add_component(name="SRC", cls="BncRunSource")
        system.add_component(name="GATE", cls="BncRunGate")
        system.add_component(name="DGATE", cls="BncDiscreteGate")
        system.connect_flow(source="SRC", target="GATE", flow_name="q")

        obs["automata_continuous"] = sorted(system.comp["GATE"].automata_d)
        obs["automata_discrete"] = sorted(system.comp["DGATE"].automata_d)

        system.add_component(name="H", cls="ObjFlow")
        system.comp["H"].add_atm2states(
            name="horizon",
            occ_law_12={"cls": "delay", "time": BNC_HORIZON},
            cond_occ_21=False,
        )

        system.isimu_start()
        obs["interactive_steps"] = []
        for _ in range(2):
            fired = system.isimu_step_forward() or []
            obs["interactive_steps"].append(
                (
                    system.currentTime(),
                    [getattr(t, "name", str(t)) for t in fired],
                )
            )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            system.isimu_stop()
    finally:
        system.deleteSys()


@pytest.fixture(scope="module")
def the_run():
    obs = {}
    observe_declarations(obs)
    observe_string_guard(obs)
    observe_rebuild(obs)
    observe_loop(obs, "bool", "BncLoopBool")
    observe_loop(obs, "compare", "BncLoopCompare")
    observe_evaluation(obs)
    observe_watched_automaton(obs)
    return obs


# ----------------------------------------------------------------------
# A production condition
# ----------------------------------------------------------------------


def test_a_boolean_production_condition_on_a_continuous_flow_becomes_a_comparison(
    the_run,
):
    """``{"name": "q"}`` on a rate says what it does: the rate is not zero."""
    assert the_run["cond_plain"] == [[{"op": "!=", "value": 0.0}]]


def test_a_negated_boolean_on_a_continuous_flow_becomes_equality_to_zero(the_run):
    """``negate`` is CONSUMED, not carried beside the comparison.

    The third shape rule forbids combining a negation with a comparison -- ``not
    (x != 0)`` is ``x == 0``, and allowing both spellings would give one
    condition two serialisations. So the flag has to go into the operator.
    """
    assert the_run["cond_negated"] == [[{"op": "==", "value": 0.0}]]
    assert not any(any(row) for row in the_run["negate_negated"])


def test_the_bare_string_operand_normalises_like_the_mapping(the_run):
    """The historical spelling reaches the same resolution point."""
    assert the_run["cond_as_string"] == [[{"op": "!=", "value": 0.0}]]


def test_a_boolean_operand_on_a_continuous_OUTPUT_normalises_too(the_run):
    """The family decides, not the side.

    ``port="out"`` is the shape a component carrying one name on both sides
    needs, and R44 made a continuous output carry a production condition of its
    own, so the operand can legitimately read one.
    """
    assert the_run["cond_on_output"] == [[{"op": "!=", "value": 0.0}]]

    flows = {f["name"]: f for f in the_run["spec"]["flows"]}
    assert flows["on_output"]["var_prod_cond"] == [
        [{"name": "p", "port": "out", "op": "!=", "value": 0.0}]
    ]


def test_a_comparison_already_written_is_left_alone(the_run):
    """Normalisation only fills in what was absent."""
    assert the_run["cond_compared"] == [[{"op": ">=", "value": BNC_THRESHOLD}]]


# ----------------------------------------------------------------------
# A rule guard, from the same implementation (R-5)
# ----------------------------------------------------------------------


def test_a_rule_guard_normalises_a_boolean_on_a_continuous_flow(the_run):
    """The other direction of the interoperation, and it must agree exactly."""
    assert the_run["guards"]["plain"] == [{"op": "!=", "value": 0.0, "negate": False}]


def test_a_rule_guard_consumes_the_negation_too(the_run):
    assert the_run["guards"]["negated"] == [{"op": "==", "value": 0.0, "negate": False}]


def test_a_guard_written_as_a_string_normalises_too(the_run):
    """A string guard is parsed into operands, then resolved like any other."""
    assert the_run["string_guards"]["on"] == [
        {"op": "!=", "value": 0.0, "negate": False}
    ]
    assert the_run["string_guards"]["off"] == [
        {"op": "==", "value": 0.0, "negate": False}
    ]


# ----------------------------------------------------------------------
# The discrete family is untouched
# ----------------------------------------------------------------------


def test_a_boolean_production_condition_on_a_discrete_flow_is_untouched(the_run):
    """A boolean state read stays a boolean state read, matrices included.

    The comparison matrix is attached only when at least one operand carries
    one, so a purely discrete condition must leave it EMPTY -- that is what
    keeps the evaluation the boolean ``all(any(...))`` it has always been.
    """
    assert the_run["cond_discrete"] == []
    assert the_run["cond_discrete_negated"] == []


def test_a_negated_boolean_on_a_discrete_flow_keeps_its_flag(the_run):
    """``negate`` survives where there is no comparison to fold it into."""
    assert any(any(row) for row in the_run["negate_discrete_negated"])


def test_a_rule_guard_on_a_discrete_flow_is_untouched(the_run):
    assert the_run["discrete_guards"]["on"] == [
        {"op": None, "value": None, "negate": False}
    ]
    assert the_run["discrete_guards"]["off"] == [
        {"op": None, "value": None, "negate": True}
    ]


# ----------------------------------------------------------------------
# The detection hole this exists to close
# ----------------------------------------------------------------------


def test_the_boolean_spelling_now_seeds_the_loop_walk(the_run):
    """It collected only comparisons, so the boolean spelling was invisible."""
    assert the_run["seeds_bool"] == {"q": "q != 0"}


def test_both_spellings_of_one_topology_are_refused(the_run):
    """The measured defect: refused written one way, accepted written the other.

    Asserted on ``RateComparisonLoopError`` and not on its superclass
    ``ContinuousFlowCycleError``: only the subclass proves the SEED WALK did
    the refusing. The plain continuous-cycle check refuses with the superclass,
    so a change that made this topology refused by the graph instead would
    leave a superclass assertion green while the seeding regression this file
    exists to guard went undetected.
    """
    assert the_run["started_compare"] is False
    assert isinstance(the_run["error_compare"], ordering.RateComparisonLoopError)

    assert the_run["started_bool"] is False, (
        "a boolean operand reads the arriving rate algebraically and closes the "
        "same instantaneous loop a comparison on it closes"
    )
    assert isinstance(the_run["error_bool"], ordering.RateComparisonLoopError)
    assert isinstance(the_run["error_bool"], muscadet.ContinuousFlowCycleError)


# ----------------------------------------------------------------------
# What must NOT change: the meaning
# ----------------------------------------------------------------------


def test_the_normalised_condition_holds_exactly_where_the_boolean_read_did(the_run):
    """``bool(x)`` and ``x != 0`` are the same test; the run must show it."""
    fed_flowing, alarm_flowing = the_run["flowing"]
    fed_stopped, alarm_stopped = the_run["stopped"]

    assert fed_flowing == pytest.approx(BNC_RATE, rel=1e-6)
    assert alarm_flowing is True

    assert fed_stopped == pytest.approx(0.0, abs=1e-9)
    assert alarm_stopped is False


# ----------------------------------------------------------------------
# The spec says what the model does
# ----------------------------------------------------------------------


def test_the_spec_reads_back_the_normalised_form(the_run):
    """A model arriving as data is readable: the operand states its threshold."""
    flows = {f["name"]: f for f in the_run["spec"]["flows"]}

    assert flows["plain"]["var_prod_cond"] == [
        [{"name": "q", "port": "in", "op": "!=", "value": 0.0}]
    ]
    assert flows["negated"]["var_prod_cond"] == [
        [{"name": "q", "port": "in", "op": "==", "value": 0.0}]
    ]
    assert flows["discrete"]["var_prod_cond"] == [[{"name": "d", "port": "in"}]]


def test_a_spec_round_trip_rebuilds_the_same_stored_form(the_run):
    """Normalising twice is normalising once.

    ``source_cls`` is the one key that legitimately differs and is compared
    apart: a spec is expanded onto ``ObjFlow``, so a component declared by a
    subclass comes back naming that subclass only under this key.
    """
    assert the_run["rebuilt"]["plain"] == [[{"op": "!=", "value": 0.0}]]
    assert the_run["rebuilt"]["negated"] == [[{"op": "==", "value": 0.0}]]
    assert the_run["rebuilt"]["as_string"] == [[{"op": "!=", "value": 0.0}]]
    assert the_run["rebuilt"]["discrete"] == []

    first = {k: v for k, v in the_run["spec"].items() if k != "source_cls"}
    again = {k: v for k, v in the_run["rebuilt_spec"].items() if k != "source_cls"}
    assert again == first

    assert the_run["spec"]["source_cls"] == "BncGate"
    assert the_run["rebuilt_spec"]["source_cls"] == "ObjFlow"


# ----------------------------------------------------------------------
# What the normalisation COSTS, which is one automaton and one step
# ----------------------------------------------------------------------


def test_the_normalised_operand_gains_a_watched_threshold_automaton(the_run):
    """A comparison on a continuous quantity is watched, so this one is too.

    That is the point rather than the price: the crossing of zero is now
    root-found instead of noticed at the following integration step, by an
    amount that used to depend on the step size. The DISCRETE control case
    gains nothing, which is what says the automaton follows the family and not
    the normalisation.
    """
    assert the_run["automata_continuous"] == ["GATE_alarm_cond_0_0_threshold"]
    assert the_run["automata_discrete"] == []


def test_the_watched_automaton_costs_one_interactive_step_at_zero(the_run):
    """The user-visible price, pinned so nobody rediscovers it in a driver.

    The automaton settles at t=0 through an instantaneous WATCHED transition,
    so an interactive session spends its first ``isimu_step_forward`` on that
    crossing and only reaches the first dated transition on the second. A
    driver stepping a fixed number of times, or asserting which transition
    fires first, sees a change with no change of its own.
    """
    first, second = the_run["interactive_steps"]

    assert first[0] == pytest.approx(0.0)
    assert first[1] == ["alarm_cond_0_0_cross_up"]

    assert second[0] == pytest.approx(BNC_HORIZON)
    assert second[1] == ["horizon_absent_present"]


def test_delete():
    cod3s.terminate_session()
