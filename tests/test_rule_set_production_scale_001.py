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

**An absent reading is no cap at all**, and that is a decision this module
pins rather than an omission. It is neither zero nor the nominal scale: a
production sweep run outside a solver step, by a test calling
``evaluate_production()`` directly, has no reading, and a diagnostic call must
not change the number it reports. In a real run the bands are ordered demand
before production, so the reading is always there.

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

#: These scenarios settle on exact rationals and measure to nine decimals.
RSP_EPS = 1e-6


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


def test_a_single_rule_set_is_untouched(the_run):
    """One set, where the demand sweep had already narrowed the input to what
    the outputs asked for: the cap meets a scale it cannot lower."""
    single = the_run["single"]
    assert single["feed"] == pytest.approx(3.0, abs=RSP_EPS)
    assert single["x"] == pytest.approx(3.0, abs=RSP_EPS)


# ----------------------------------------------------------------------
# An absent reading is no cap, and that is a decision
# ----------------------------------------------------------------------


def test_a_production_sweep_with_no_reading_is_not_throttled(the_run):
    """A diagnostic call must report the number, not a different one.

    ``evaluate_production()`` called outside a solver step has no reading from
    the demand band of the same evaluation. Answering ``0`` would stop the
    model, answering the nominal scale would invent a bound; the hand-off
    answers "no cap", so the sweep reports exactly what it reported before
    this mechanism existed. Two tests of ``test_advection_001.py`` read the
    sweep this way, and they are why the default is what it is.

    The private dictionary is emptied here on purpose: that is precisely the
    state a call outside a step finds it in.
    """
    component = the_run["system"].comp["O_T"]
    component._demand_scale.clear()

    _, produced = component.evaluate_production()

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

    component._demand_scale["s1"] = (rule, 2.0)
    assert component.demand_scale("s1", rule) == pytest.approx(2.0)

    # Same set, another rule object: the reading does not apply.
    other = component.get_active_rule(component.rule_sets["s2"])
    assert component.demand_scale("s1", other) == math.inf

    # No reading at all.
    component._demand_scale.clear()
    assert component.demand_scale("s1", rule) == math.inf


# ----------------------------------------------------------------------
# Teardown -- PyCATSHOO holds one live system per process
# ----------------------------------------------------------------------


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
