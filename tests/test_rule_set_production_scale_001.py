"""Two rule sets on one input: the production sweep obeys the demand sweep.

The missing half of the minimum scale of 3.0.0. ``get_demand_scale`` sizes each
rule set from what its outputs are asked for, and for a component carrying ONE
set that is enough: the demand sweep narrows the inputs to exactly that, so the
budget the production sweep then divides is already the right size.

A component carrying TWO sets shares one budget between them, and the
production sweep sized each set from that budget alone
(``rule_scale(rule, available)``), never reading the demand scale back. A set
the demand sweep had sized at **zero** therefore ran anyway, at whatever the
input allowed, ate the budget its sibling needed, and dropped everything it
made. Measured before the fix, on a supply of 5 shared by two sets, the first
declared having one outlet asked for nothing::

    echelle de demande par jeu : {'s1': 0.0, 's2': 4.0}
    production evaluee         : {'x': 4.0, 'y': 4.0, 'z': 0.0}
    livre sur x / y / z        : 0.0000 / 4.0000 / 0.0000

Four units an hour of ``x`` made and dropped, and ``z``, the only product with
a consumer actually asking for it, not made at all. Swapping the declaration
order swapped the outcome, which says plainly that the order was deciding what
the demands should have decided.

**Why a hand-off and not a recomputation.** ``get_output_demand`` WRITES
``comp._demand_bound``, which ``get_output_request`` consumes further down the
production sweep. Recomputing the scale there would overwrite this
evaluation's reading with one taken after the capacity levels moved. The scale
is therefore recorded by the demand sweep in ``comp._demand_scale``, beside the
bound and cleared at the same place, and read back by identity of the rule it
was computed for.

**In a real run the reading is never missing.** The bands are ordered demand
before production, one of each per component per instant, and a measurement
over 1 428 reads on the model below found none absent, including at
``isimu_start`` and across a guard flipping mid-run.

**Outside a solver step the reading is STALE, not missing.** A test calling
``evaluate_production()`` directly gets the one the last evaluation left, so
such a call reports what the run last computed rather than an uncapped number.
This module relies on that deliberately: ``blocked_evaluated`` below is such a
call, and it is the only place a dropped product is visible at all. The
consequence to know is the other way round: hand-editing a demand and calling
the sweep without replaying the demand band answers from before the edit.

``UNBOUNDED`` for a missing reading is therefore a **cold-start guard**, not
the out-of-step story: it covers a call made before any evaluation has run,
and a record whose rule is not the one being sized.

PyCATSHOO forbids more than one live system per process, so every scenario
lives in the one system below, driven through a single interactive session.
"""

import math

import cod3s
import muscadet
import pytest

#: Tick of the interactive session, and how far it is driven.
RSP_TICK = 0.5
RSP_HORIZON = 4.0

#: The supply the two rule sets of a component share.
RSP_SUPPLY = 5.0

#: What the sibling set's consumer asks for, and what the blocked set's
#: second outlet asks for while its first is asked nothing.
RSP_SIBLING = 4.0
RSP_SPARE = 6.0

#: The partial case: the blocked set's outlet asks for a little, not nothing.
RSP_TRICKLE = 1.0

#: These scenarios settle on exact rationals. Largest residue observed: 1e-14.
RSP_EPS = 1e-6

#: The generator's rating, what its load draws, and the fill rate its buffer
#: claims in the variant that declares one.
RSP_RATING = 2.0
RSP_LOAD = 0.5
RSP_FILL = 0.5

#: What each consumer of the derated pair asks for, and when the fault cuts
#: the first outlet. It never repairs.
RSP_PAIR = 2.0
RSP_CUT = 1.0


# ----------------------------------------------------------------------
# Components -- prefixed, component classes resolving by name globally
# ----------------------------------------------------------------------


class RspSource(muscadet.ObjFlow):
    """A continuous producer holding the rate it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name=kwargs.get("flow", "feed"),
            var_fed_default=kwargs.get("rate", RSP_SUPPLY),
        )


class RspConsumer(muscadet.ObjFlow):
    """A pure consumer publishing the demand it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "x"),
            var_demand_default=kwargs.get("demand", 0.0),
        )


class RspTwoSets(muscadet.ObjFlow):
    """One input, two rule sets: ``s1`` makes x and y, ``s2`` makes z.

    ``s1`` is the set whose outlets decide whether it may run at all, ``s2``
    the sibling whose budget it used to eat.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="feed")
        for name in ("x", "y", "z"):
            self.add_flow_continuous_out(name=name)
        self.add_rules(
            name="s1", rules=[dict(cons={"feed": 1.0}, prod={"x": 1.0, "y": 1.0})]
        )
        self.add_rules(name="s2", rules=[dict(cons={"feed": 1.0}, prod={"z": 1.0})])


class RspTwoSetsSwapped(muscadet.ObjFlow):
    """The same two sets, declared the other way round.

    Declaration order is the priority order over the shared budget, and that
    is deliberate. What must NOT depend on it is whether a product is made
    and dropped.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="feed")
        for name in ("x", "y", "z"):
            self.add_flow_continuous_out(name=name)
        self.add_rules(name="s2", rules=[dict(cons={"feed": 1.0}, prod={"z": 1.0})])
        self.add_rules(
            name="s1", rules=[dict(cons={"feed": 1.0}, prod={"x": 1.0, "y": 1.0})]
        )


class RspGenset(muscadet.ObjFlow):
    """A rule that CONSUMES NOTHING: the single-set shape that moves.

    ``rule_scale`` has no reagent to size against and answers the nominal
    scale, so before 3.1.0 the demand never reached the production and this
    rule ran at its rating whatever anyone asked. The buffer takes whatever
    the rating exceeds the load by, and claims nothing for itself unless a
    ``fill_rate`` says so.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="power")
        self.add_rules(name="gen", rules=[dict(prod={"power": RSP_RATING})])
        self.add_capacity(
            name="battery",
            flow="power",
            side="out",
            capacity=100.0,
            content_init={"power": 0.0},
            **({"fill_rate": kwargs["fill_rate"]} if "fill_rate" in kwargs else {}),
        )


class RspOneSet(muscadet.ObjFlow):
    """A single rule set, where the cap is a no-op and must stay one."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="feed")
        self.add_flow_continuous_out(name="x")
        self.add_rules(name="only", rules=[dict(cons={"feed": 1.0}, prod={"x": 1.0})])


# ----------------------------------------------------------------------
# The one system every scenario lives in
# ----------------------------------------------------------------------


def build_system():
    system = muscadet.System(name="RspSys")

    # -- BLOCKED. ``s1``'s first outlet is asked for nothing, so the set may
    #    not run at all, whatever its second outlet would take.
    system.add_component(name="B_SRC", cls="RspSource", rate=RSP_SUPPLY)
    system.add_component(name="B_T", cls="RspTwoSets")
    system.add_component(name="B_X", cls="RspConsumer", flow="x", demand=0.0)
    system.add_component(name="B_Y", cls="RspConsumer", flow="y", demand=RSP_SPARE)
    system.add_component(name="B_Z", cls="RspConsumer", flow="z", demand=RSP_SIBLING)
    system.connect_flow(source="B_SRC", target="B_T", flow_name="feed")
    for name, flow in (("B_X", "x"), ("B_Y", "y"), ("B_Z", "z")):
        system.connect_flow(source="B_T", target=name, flow_name=flow)

    # -- SWAPPED. The identical model with the two sets declared the other
    #    way round. The order decides who is served first out of a contested
    #    budget; it must not decide whether anything is dropped.
    system.add_component(name="W_SRC", cls="RspSource", rate=RSP_SUPPLY)
    system.add_component(name="W_T", cls="RspTwoSetsSwapped")
    system.add_component(name="W_X", cls="RspConsumer", flow="x", demand=0.0)
    system.add_component(name="W_Y", cls="RspConsumer", flow="y", demand=RSP_SPARE)
    system.add_component(name="W_Z", cls="RspConsumer", flow="z", demand=RSP_SIBLING)
    system.connect_flow(source="W_SRC", target="W_T", flow_name="feed")
    for name, flow in (("W_X", "x"), ("W_Y", "y"), ("W_Z", "z")):
        system.connect_flow(source="W_T", target=name, flow_name=flow)

    # -- TRICKLE. The partial case: ``s1`` is throttled rather than stopped,
    #    so the cap has to be a quantity and not a boolean.
    system.add_component(name="P_SRC", cls="RspSource", rate=RSP_SUPPLY)
    system.add_component(name="P_T", cls="RspTwoSets")
    system.add_component(name="P_X", cls="RspConsumer", flow="x", demand=RSP_TRICKLE)
    system.add_component(name="P_Y", cls="RspConsumer", flow="y", demand=RSP_SPARE)
    system.add_component(name="P_Z", cls="RspConsumer", flow="z", demand=RSP_SIBLING)
    system.connect_flow(source="P_SRC", target="P_T", flow_name="feed")
    for name, flow in (("P_X", "x"), ("P_Y", "y"), ("P_Z", "z")):
        system.connect_flow(source="P_T", target=name, flow_name=flow)

    # -- SINGLE. One set, where the demand sweep already narrowed the input
    #    and the cap changes nothing.
    system.add_component(name="O_SRC", cls="RspSource", rate=RSP_SUPPLY)
    system.add_component(name="O_T", cls="RspOneSet")
    system.add_component(name="O_X", cls="RspConsumer", flow="x", demand=3.0)
    system.connect_flow(source="O_SRC", target="O_T", flow_name="feed")
    system.connect_flow(source="O_T", target="O_X", flow_name="x")

    # -- GENSET. One rule set, and a rule with no reagent to size against.
    #    Its buffer claims nothing (fill_rate defaults to 0), so the load is
    #    the whole of what the output asks for.
    system.add_component(name="G_GEN", cls="RspGenset")
    system.add_component(
        name="G_LOAD", cls="RspConsumer", flow="power", demand=RSP_LOAD
    )
    system.connect_flow(source="G_GEN", target="G_LOAD", flow_name="power")

    # -- GENSET, CHARGING. The same, with the fill rate the buffer really has
    #    stated rather than inferred from a rule running flat out.
    system.add_component(name="F_GEN", cls="RspGenset", fill_rate=RSP_FILL)
    system.add_component(
        name="F_LOAD", cls="RspConsumer", flow="power", demand=RSP_LOAD
    )
    system.connect_flow(source="F_GEN", target="F_LOAD", flow_name="power")

    # -- DERATED. Two sets, and a fault cutting one outlet of the first. The
    #    cap must not read that loss as an absent outlet: the product is made
    #    and destroyed on the way out, so the draw holds.
    system.add_component(name="D_SRC", cls="RspSource", rate=RSP_SUPPLY)
    system.add_component(name="D_T", cls="RspTwoSets")
    system.add_component(name="D_X", cls="RspConsumer", flow="x", demand=RSP_PAIR)
    system.add_component(name="D_Y", cls="RspConsumer", flow="y", demand=RSP_PAIR)
    system.add_component(name="D_Z", cls="RspConsumer", flow="z", demand=RSP_PAIR)
    system.connect_flow(source="D_SRC", target="D_T", flow_name="feed")
    for name, flow in (("D_X", "x"), ("D_Y", "y"), ("D_Z", "z")):
        system.connect_flow(source="D_T", target=name, flow_name=flow)
    system.comp["D_T"].add_delay_failure_mode(
        name="cut_x",
        failure_time=RSP_CUT,
        failure_effects=[("x", 0.0)],
        repair_cond=False,
    )

    system.add_component(name="CLOCK", cls="RspSource", flow="tick", rate=0.0)
    system.comp["CLOCK"].add_atm2states(
        name="tick",
        st1="a",
        st2="b",
        occ_law_12={"cls": "delay", "time": RSP_TICK},
        occ_law_21={"cls": "delay", "time": RSP_TICK},
    )

    return system


def drawn(system, comp_name):
    """What a component's shared input currently receives."""
    return system.comp[comp_name].flows_in["feed"].get_delivered()


def out_value(system, comp_name, flow_name):
    """What a component currently delivers on one of its outputs."""
    return system.comp[comp_name].flows_out[flow_name].var_fed.value()


@pytest.fixture(scope="module")
def the_run():
    system = build_system()
    system.isimu_start()
    while system.currentTime() < RSP_HORIZON:
        system.isimu_step_forward()

    def readings(comp_name):
        return {
            "feed": drawn(system, comp_name),
            "x": out_value(system, comp_name, "x"),
            "y": out_value(system, comp_name, "y"),
            "z": out_value(system, comp_name, "z"),
        }

    obs = {
        "time": system.currentTime(),
        "blocked": readings("B_T"),
        "swapped": readings("W_T"),
        "trickle": readings("P_T"),
        "single": {
            "feed": drawn(system, "O_T"),
            "x": out_value(system, "O_T", "x"),
        },
        "genset": {
            "power": out_value(system, "G_GEN", "power"),
            "battery": system.comp["G_GEN"].capacities["battery"].total_quantity(),
        },
        "charging": {
            "power": out_value(system, "F_GEN", "power"),
            "battery": system.comp["F_GEN"].capacities["battery"].total_quantity(),
        },
        "derated": readings("D_T"),
        "system": system,
    }

    # The evaluated production of the blocked component, which is where a
    # dropped product is visible at all: ``var_fed`` on an output carries
    # what was DELIVERED, so a rule making four and delivering none reads
    # zero there either way.
    consumed, produced = system.comp["B_T"].evaluate_production()
    obs["blocked_evaluated"] = {"consumed": dict(consumed), "produced": dict(produced)}

    system.isimu_stop()
    return obs


# ----------------------------------------------------------------------
# A set the demand sweep stopped does not run anyway
# ----------------------------------------------------------------------


def test_a_set_sized_at_zero_makes_nothing(the_run):
    """``s1`` has an outlet asked for nothing, so it may not run.

    Read on the EVALUATED production, the only place a dropped product shows:
    before the fix this was ``{'x': 4.0, 'y': 4.0, 'z': 0.0}`` against
    deliveries of ``0 / 4 / 0``.
    """
    produced = the_run["blocked_evaluated"]["produced"]
    assert produced["x"] == pytest.approx(0.0, abs=RSP_EPS)
    assert produced["y"] == pytest.approx(0.0, abs=RSP_EPS)


def test_the_sibling_set_gets_the_budget_it_was_asked_for(the_run):
    """``s2`` is the only set with a consumer actually asking, and it is
    served: four out of the five available, which is what its consumer wants.
    """
    blocked = the_run["blocked"]
    assert blocked["z"] == pytest.approx(RSP_SIBLING, abs=RSP_EPS)
    assert blocked["feed"] == pytest.approx(RSP_SIBLING, abs=RSP_EPS)


def test_nothing_the_component_drew_is_unaccounted_for(the_run):
    """The balance on the whole component: one of feed makes one of each
    product of the set that consumed it, so what was drawn and what was
    delivered agree. Before the fix the component drew four and delivered
    four of ``y`` alone, four more of ``x`` having been made and dropped.
    """
    evaluated = the_run["blocked_evaluated"]
    blocked = the_run["blocked"]

    # No capacity anywhere on this component, so every unit made leaves it.
    # Stated over EVERY output rather than over the one that happens to be
    # served: before the fix, ``z`` balanced perfectly while ``x`` was made at
    # four and delivered at zero, so a balance read on ``z`` alone was green
    # while four units an hour went missing next to it.
    for flow_name in ("x", "y", "z"):
        assert evaluated["produced"][flow_name] == pytest.approx(
            blocked[flow_name], abs=RSP_EPS
        ), flow_name

    assert evaluated["consumed"]["feed"] == pytest.approx(blocked["feed"], abs=RSP_EPS)


# ----------------------------------------------------------------------
# The declaration order no longer decides what is made
# ----------------------------------------------------------------------


def test_the_declaration_order_does_not_decide_what_is_made(the_run):
    """The same two sets declared the other way round give the same result.

    Order stays the priority order over a contested budget, which is a
    deliberate and inspectable rule. What it must not decide is whether a
    product is made and dropped: before the fix, declaring ``s2`` first gave
    ``z = 4, y = 0`` and declaring ``s1`` first gave ``z = 0, y = 4``, the
    two orders disagreeing about which consumer is served.
    """
    assert the_run["swapped"] == pytest.approx(the_run["blocked"], abs=RSP_EPS)


# ----------------------------------------------------------------------
# The cap is a quantity, not a switch
# ----------------------------------------------------------------------


def test_a_partly_asked_set_runs_partly(the_run):
    """``s1``'s outlet asks for one of the five available, so it runs at one
    and leaves four to its sibling, which wants exactly four.

    The whole supply is used and none of it is wasted, which a boolean
    "may this set run" could not express.
    """
    trickle = the_run["trickle"]
    assert trickle["x"] == pytest.approx(RSP_TRICKLE, abs=RSP_EPS)
    assert trickle["y"] == pytest.approx(RSP_TRICKLE, abs=RSP_EPS)
    assert trickle["z"] == pytest.approx(RSP_SIBLING, abs=RSP_EPS)
    assert trickle["feed"] == pytest.approx(RSP_TRICKLE + RSP_SIBLING, abs=RSP_EPS)


def test_a_single_set_with_a_reagent_is_untouched(the_run):
    """One set consuming something, where the demand sweep had already
    narrowed the input to what the outputs asked for: the cap meets a scale
    it cannot lower.

    The claim is deliberately narrower than "a single rule set is untouched",
    which is false: see the generator below, whose rule consumes nothing and
    whose scale the demand never reached before 3.1.0.
    """
    single = the_run["single"]
    assert single["feed"] == pytest.approx(3.0, abs=RSP_EPS)
    assert single["x"] == pytest.approx(3.0, abs=RSP_EPS)


# ----------------------------------------------------------------------
# A rule with no reagent: the single-set shape that does move
# ----------------------------------------------------------------------


def test_a_rule_consuming_nothing_delivers_what_is_asked_not_its_rating(the_run):
    """A 2 kW generator feeding a 0.5 kW load produces 0.5.

    ``rule_scale`` sizes a rule against its reagents, and this one has none,
    so it answered the nominal scale and the demand never reached the
    production: the generator ran at its rating whatever anyone asked. The
    surplus went into a buffer that had claimed nothing, ``fill_rate``
    defaulting to 0, which is the one thing R36 says such a buffer must not
    do. Before 3.1.0 the battery held 6.0 after four hours.
    """
    genset = the_run["genset"]
    assert genset["power"] == pytest.approx(RSP_LOAD, abs=RSP_EPS)
    assert genset["battery"] == pytest.approx(0.0, abs=RSP_EPS)


def test_a_declared_fill_rate_brings_the_charging_back(the_run):
    """And says it, which is the whole point of the change.

    The same generator whose buffer declares the rate it really takes: the
    output is asked for the load plus that claim, the rule runs at the sum,
    and the battery charges at exactly the rate it was declared with rather
    than at whatever the rule's rating happened to leave over.
    """
    charging = the_run["charging"]
    assert charging["power"] == pytest.approx(RSP_LOAD, abs=RSP_EPS)
    assert charging["battery"] == pytest.approx(RSP_FILL * the_run["time"], rel=1e-3)


# ----------------------------------------------------------------------
# A loss is still not an absent outlet
# ----------------------------------------------------------------------


def test_the_cap_does_not_read_a_derating_as_an_absent_outlet(the_run):
    """Two sets, and a fault cutting one outlet of the first to zero.

    The cap lowers a set to what its outputs ASK for, and a derated outlet
    asks for exactly what it asked before: the product is made and the fault
    destroys it on the way out. So the draw holds, the surviving outlet keeps
    delivering, and the sibling set keeps its share. Reading the loss as a
    demand of zero would stop the draw and make ``y`` out of nothing, which is
    why ``get_uptake_factor`` is a maximum where this cap is a minimum.
    """
    derated = the_run["derated"]
    assert derated["x"] == pytest.approx(0.0, abs=RSP_EPS)
    assert derated["y"] == pytest.approx(RSP_PAIR, abs=RSP_EPS)
    assert derated["z"] == pytest.approx(RSP_PAIR, abs=RSP_EPS)
    assert derated["feed"] == pytest.approx(2.0 * RSP_PAIR, abs=RSP_EPS)


# ----------------------------------------------------------------------
# An absent reading is no cap, and that is a decision
# ----------------------------------------------------------------------


def test_a_cold_production_sweep_is_not_throttled(the_run):
    """Before any evaluation has run there is no reading, and none is invented.

    Answering ``0`` would stop the model and answering the nominal scale would
    make up a bound; the hand-off answers "no cap", so a sweep with nothing
    recorded reports exactly what it reported before this mechanism existed.

    The dictionary is emptied by hand because that state is otherwise
    unreachable from a test: outside a solver step a component holds the
    reading its last evaluation left, not an empty one. It is restored
    afterwards, the fixture being module-scoped and its components live.
    """
    component = the_run["system"].comp["O_T"]
    saved = dict(component._demand_scale)
    component._demand_scale.clear()
    try:
        _, produced = component.evaluate_production()
    finally:
        # Restored: the fixture is module-scoped and its components are live.
        component._demand_scale.update(saved)

    assert produced["x"] == pytest.approx(3.0, abs=RSP_EPS)


def test_a_reading_belonging_to_another_rule_is_refused(the_run):
    """The hand-off is checked by identity, not by rule-set name.

    A guard selecting a different rule between the two sweeps would otherwise
    cap this one with a scale computed for that one. Cheap to check, and the
    failure it prevents is a wrong number rather than an error.
    """
    component = the_run["system"].comp["B_T"]
    rule_set = component.rule_sets["s1"]
    rule = component.get_active_rule(rule_set)

    saved = dict(component._demand_scale)
    try:
        component._demand_scale["s1"] = (rule, 2.0)
        assert component.recorded_demand_scale("s1", rule) == pytest.approx(2.0)

        # Same set, another rule object: the reading does not apply.
        other = component.get_active_rule(component.rule_sets["s2"])
        assert component.recorded_demand_scale("s1", other) == math.inf

        # No reading at all: the cold-start case.
        component._demand_scale.clear()
        assert component.recorded_demand_scale("s1", rule) == math.inf
    finally:
        # Restored: the fixture is module-scoped and its components are live.
        component._demand_scale.clear()
        component._demand_scale.update(saved)


# ----------------------------------------------------------------------
# Teardown -- PyCATSHOO holds one live system per process
# ----------------------------------------------------------------------


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
