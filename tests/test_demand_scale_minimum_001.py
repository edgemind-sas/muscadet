"""A rule runs at the scale its MOST CONSTRAINED output allows (R-37).

``get_demand_scale`` sizes the scale a rule must run at from what its outputs
are asked for. It took that as a **maximum**, on the reading that the outputs
are correlated by construction so the scale serving them all is the one the
most demanding of them needs. The surplus on every less-demanding output was
then simply not delivered -- which is to say **destroyed**, silently, with no
balance recording it.

That is not a corner case of an exotic model. It fires whenever a rule has two
connected outputs asked for unequal quantities, and its worst consequence is
not the missing matter but this: **it walks straight through the capacity
bound**. An electrolyser whose hydrogen outlet is blocked, holding a 10-unit
buffer behind that outlet, filled that buffer to 39 units in twenty hours and
went on climbing, while ``Capacity.clamp_to_bounds`` and the per-constituent
residue accounting worked to hold a bound the production sweep was refilling
past. Block the SECOND outlet too and everything is exact -- the buffer settles
on 10.000 and the draw falls to 0.000. One outlet still asking was the whole of
the defect.

The scale is therefore a **minimum**: a reaction cannot run faster than its
most constrained product allows.

**The argument that settles it is expressiveness, not physics.** Under the
minimum both intents stay modellable:

* "this outlet constrains me" -- wire it to its real consumer, whose demand
  bounds the rule;
* "this outlet discharges freely" -- leave it unconnected, which
  ``output_constrains_demand`` drops from the scale entirely, or wire it to a
  discharge asking for far more than the rule can make, which a minimum never
  retains.

Under the maximum the second works and **the first cannot be said at all**.
Worse, the discharge pattern R-10 itself recommends -- declare the vent as a
consumer with its own demand, so the intent is visible -- was already wrong
under a maximum: the rule took off at the vent's rate and destroyed the
surplus of the useful product. A semantics that erases an intent loses to one
that keeps both, and that is the whole of the case.

**Why this is not the ``get_uptake_factor`` maximum in reverse.** The two look
contradictory and answer different questions. A **derating** is a declared
loss: the fault makes the product and destroys it on the way out, so it must
not spare the reagents the surviving legs still consume -- hence a maximum
there, or a two-output reaction with one output cut to zero would make its
other product out of nothing. A **demand of zero** is not a loss: it is the
absence of an outlet, and no fault is destroying anything. Nothing may be
created that has nowhere to go. Both are pinned below, side by side, because
"harmonising" them is the change this module exists to refuse.

PyCATSHOO forbids more than one live system per process, so every scenario
lives in the one system below, driven through a single interactive session.
"""

import math

import cod3s
import muscadet
import pytest

from muscadet.kb.continuous import CapacityContinuous  # noqa: F401

#: Tick of the interactive session, and how far it is driven.
DSM_TICK = 0.5
DSM_HORIZON = 20.0

#: Volume of the buffer sitting behind a blocked outlet.
DSM_BUFFER = 10.0

#: Supply large enough that no scenario is ever bounded by its source: what
#: bounds a rule here must be its outputs, never its reagents.
DSM_AMPLE = 100.0

#: When the fault cuts outlet ``a``. It never repairs, said the way the
#: "Modelling pitfalls" section of the README asks for it: a repair pushed
#: beyond the horizon is an integration horizon, a repair that cannot fire
#: costs nothing.
DSM_CUT = 1.0

#: Volume of the secondary buffer that throttles a main product, and what its
#: slow consumer draws through it.
DSM_SECONDARY = 5.0
DSM_SLOW = 1.0

#: Numerical slack on an ALGEBRAIC reading. Every scenario below settles on an
#: exact rational and measures to nine decimals, so nothing here needs room:
#: a loose tolerance on these would let a half-percent error pass unseen.
DSM_EPS = 1e-6

#: Numerical slack on a CAPACITY LEVEL, and only there. The solver root-finds
#: a bound crossing to ``dtCond`` and lands just past it, so a ten-unit volume
#: is read at 10.0013 before ``clamp_to_bounds`` takes it back.
DSM_BOUND_EPS = 1e-2


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class DsmSource(muscadet.ObjFlow):
    """A continuous producer holding the rate it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name=kwargs.get("flow", "feed"),
            var_fed_default=kwargs.get("rate", DSM_AMPLE),
        )


class DsmConsumer(muscadet.ObjFlow):
    """A pure consumer publishing the demand it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "good"),
            var_demand_default=kwargs.get("demand", 0.0),
        )


class DsmElectrolyser(muscadet.ObjFlow):
    """1 power makes 2 h2 and 1 o2: the stoichiometry cannot be broken.

    The shape the defect was found on. Both products leave by their own
    outlet, and neither can be made without the other.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="power", var_demand_default=1.0)
        self.add_flow_continuous_out(name="h2")
        self.add_flow_continuous_out(name="o2")
        self.add_rules(
            name="electrolysis",
            rules=[dict(cons={"power": 1.0}, prod={"h2": 2.0, "o2": 1.0})],
        )


class DsmBufferedElectrolyser(DsmElectrolyser):
    """The same, with a pressure buffer behind the hydrogen outlet.

    The volume is the invariant this module is hardest on: whatever the outlets
    downstream do, a capacity never holds more than it was declared with.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_capacity(
            name="buffer_h2",
            flow="h2",
            side="out",
            capacity=DSM_BUFFER,
            content_init={"h2": 0.0},
            fill_rate=math.inf,
        )


class DsmSplitter(muscadet.ObjFlow):
    """One reagent, two products in equal parts, on two named outlets."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="feed")
        self.add_flow_continuous_out(name="a")
        self.add_flow_continuous_out(name="b")
        self.add_rules(
            name="split",
            rules=[dict(cons={"feed": 1.0}, prod={"a": 1.0, "b": 1.0})],
        )


class DsmRatio(muscadet.ObjFlow):
    """1 feed makes **2** of ``a`` and **1** of ``b``.

    The only shape in which the division by the coefficient decides. Every
    other scenario here has 1:1 coefficients, or a demand of zero, or an
    unbounded one, and a scale computed on the bare demand would pass them
    all: a mutant dropping ``/ coefficient`` was caught by no test of this
    module until this component existed.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="feed")
        self.add_flow_continuous_out(name="a")
        self.add_flow_continuous_out(name="b")
        self.add_rules(
            name="ratio",
            rules=[dict(cons={"feed": 1.0}, prod={"a": 2.0, "b": 1.0})],
        )


class DsmVented(muscadet.ObjFlow):
    """One reagent, a useful product and a discharge.

    Same shape as ``DsmSplitter`` under different names, because the two
    scenarios it serves are about INTENT and read better spelled out: ``good``
    is sold, ``vent`` is thrown away.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="feed")
        self.add_flow_continuous_out(name="good")
        self.add_flow_continuous_out(name="vent")
        self.add_rules(
            name="split",
            rules=[dict(cons={"feed": 1.0}, prod={"good": 1.0, "vent": 1.0})],
        )


# ----------------------------------------------------------------------
# The one system every scenario lives in
# ----------------------------------------------------------------------


def build_system():
    system = muscadet.System(name="DsmSys")

    # -- BLOCKED. The hydrogen outlet is wired to a consumer asking for
    #    nothing while the oxygen outlet is wired to a discharge asking for
    #    far more than the reaction can make. No hydrogen outlet, no reaction.
    system.add_component(name="B_GRID", cls="DsmSource", flow="power", rate=1.0)
    system.add_component(name="B_ELY", cls="DsmElectrolyser")
    system.add_component(name="B_TANK", cls="DsmConsumer", flow="h2", demand=0.0)
    system.add_component(
        name="B_VENT", cls="DsmConsumer", flow="o2", demand=DSM_AMPLE * 10
    )
    system.connect_flow(source="B_GRID", target="B_ELY", flow_name="power")
    system.connect_flow(source="B_ELY", target="B_TANK", flow_name="h2")
    system.connect_flow(source="B_ELY", target="B_VENT", flow_name="o2")

    # -- BUFFERED. Same, with a pressure buffer behind the blocked outlet: the
    #    reaction runs while the buffer takes the hydrogen, and stops when it
    #    is full. The buffer is the bound this module measures.
    system.add_component(name="P_GRID", cls="DsmSource", flow="power", rate=1.0)
    system.add_component(name="P_ELY", cls="DsmBufferedElectrolyser")
    system.add_component(name="P_TANK", cls="DsmConsumer", flow="h2", demand=0.0)
    system.add_component(
        name="P_VENT", cls="DsmConsumer", flow="o2", demand=DSM_AMPLE * 10
    )
    system.connect_flow(source="P_GRID", target="P_ELY", flow_name="power")
    system.connect_flow(source="P_ELY", target="P_TANK", flow_name="h2")
    system.connect_flow(source="P_ELY", target="P_VENT", flow_name="o2")

    # -- UNEQUAL. Two real consumers, one wanting five times the other. The
    #    rule runs at what the smaller one takes: the surplus of the other
    #    product has nowhere to go and may not be made.
    system.add_component(name="U_SRC", cls="DsmSource", flow="feed")
    system.add_component(name="U_SPLIT", cls="DsmSplitter")
    system.add_component(name="U_BIG", cls="DsmConsumer", flow="a", demand=10.0)
    system.add_component(name="U_SMALL", cls="DsmConsumer", flow="b", demand=2.0)
    system.connect_flow(source="U_SRC", target="U_SPLIT", flow_name="feed")
    system.connect_flow(source="U_SPLIT", target="U_BIG", flow_name="a")
    system.connect_flow(source="U_SPLIT", target="U_SMALL", flow_name="b")

    # -- COEFFICIENTS. Both consumers ask for exactly four, and the outlets
    #    still do not constrain equally: the criterion is demand DIVIDED BY
    #    coefficient. Making four of ``a`` takes a scale of two, which makes
    #    two of ``b``, so the smaller consumer is served half of what it
    #    asked. This is also the counter-example to reading the migration
    #    note as "outputs asked the same are unaffected".
    system.add_component(name="C_SRC", cls="DsmSource", flow="feed")
    system.add_component(name="C_RATIO", cls="DsmRatio")
    system.add_component(name="C_A", cls="DsmConsumer", flow="a", demand=4.0)
    system.add_component(name="C_B", cls="DsmConsumer", flow="b", demand=4.0)
    system.connect_flow(source="C_SRC", target="C_RATIO", flow_name="feed")
    system.connect_flow(source="C_RATIO", target="C_A", flow_name="a")
    system.connect_flow(source="C_RATIO", target="C_B", flow_name="b")

    # -- THROTTLE. The shape a modeller actually meets, where the blocked
    #    outlet of the scenarios above is the degenerate case: a by-product
    #    buffered on its way to a consumer that draws it more slowly than the
    #    reaction makes it. The main product runs at full rate until the
    #    buffer saturates, then follows the slow consumer.
    system.add_component(name="H_SRC", cls="DsmSource", flow="feed")
    system.add_component(name="H_REACT", cls="DsmSplitter")
    system.add_component(name="H_MAIN", cls="DsmConsumer", flow="a", demand=10.0)
    system.add_component(
        name="H_BUF",
        cls="CapacityContinuous",
        flow="b",
        capacity=DSM_SECONDARY,
        ports="both",
        fill_rate=math.inf,
    )
    system.add_component(name="H_SLOW", cls="DsmConsumer", flow="b", demand=DSM_SLOW)
    system.connect_flow(source="H_SRC", target="H_REACT", flow_name="feed")
    system.connect_flow(source="H_REACT", target="H_MAIN", flow_name="a")
    system.connect_flow(source="H_REACT", target="H_BUF", flow_name="b")
    system.connect_flow(source="H_BUF", target="H_SLOW", flow_name="b")

    # -- UNWIRED VENT (R-10, non-regression). The discharge is connected to
    #    nothing, so it is dropped from the scale and the useful consumer
    #    alone sizes the rule. A minimum must not turn a dropped outlet into
    #    a bound of zero.
    system.add_component(name="W_SRC", cls="DsmSource", flow="feed")
    system.add_component(name="W_T", cls="DsmVented")
    system.add_component(name="W_C", cls="DsmConsumer", flow="good", demand=5.0)
    system.connect_flow(source="W_SRC", target="W_T", flow_name="feed")
    system.connect_flow(source="W_T", target="W_C", flow_name="good")

    # -- DECLARED VENT. The discharge R-10 recommends declaring, as a consumer
    #    with a demand of its own. Under a maximum the rule took off at the
    #    vent's rate and destroyed the useful surplus; the intent it exists to
    #    make visible was betrayed by the semantics.
    system.add_component(name="D_SRC", cls="DsmSource", flow="feed")
    system.add_component(name="D_T", cls="DsmVented")
    system.add_component(name="D_C", cls="DsmConsumer", flow="good", demand=5.0)
    system.add_component(
        name="D_V", cls="DsmConsumer", flow="vent", demand=DSM_AMPLE * 10
    )
    system.connect_flow(source="D_SRC", target="D_T", flow_name="feed")
    system.connect_flow(source="D_T", target="D_C", flow_name="good")
    system.connect_flow(source="D_T", target="D_V", flow_name="vent")

    # -- FILLING TANK (R36). One outlet feeds a tank claiming its fill rate
    #    without bound, the other a consumer wanting three. The unbounded
    #    claim never wins a minimum, so the metered consumer sizes the rule
    #    and the tank fills at that rate rather than at whatever the source
    #    could give.
    system.add_component(name="T_SRC", cls="DsmSource", flow="feed")
    system.add_component(name="T_SPLIT", cls="DsmSplitter")
    system.add_component(
        name="T_TANK",
        cls="CapacityContinuous",
        flow="a",
        capacity=DSM_AMPLE,
        ports="both",
        fill_rate=math.inf,
    )
    system.add_component(name="T_C", cls="DsmConsumer", flow="b", demand=3.0)
    system.connect_flow(source="T_SRC", target="T_SPLIT", flow_name="feed")
    system.connect_flow(source="T_SPLIT", target="T_TANK", flow_name="a")
    system.connect_flow(source="T_SPLIT", target="T_C", flow_name="b")

    # -- DERATED. Outlet ``a`` is cut to zero by a fault while both consumers
    #    ask normally. A loss is not an absent outlet: the draw must hold.
    system.add_component(name="R_SRC", cls="DsmSource", flow="feed")
    system.add_component(name="R_SPLIT", cls="DsmSplitter")
    system.add_component(name="R_A", cls="DsmConsumer", flow="a", demand=4.0)
    system.add_component(name="R_B", cls="DsmConsumer", flow="b", demand=4.0)
    system.connect_flow(source="R_SRC", target="R_SPLIT", flow_name="feed")
    system.connect_flow(source="R_SPLIT", target="R_A", flow_name="a")
    system.connect_flow(source="R_SPLIT", target="R_B", flow_name="b")
    system.comp["R_SPLIT"].add_delay_failure_mode(
        name="cut_a",
        failure_time=DSM_CUT,
        failure_effects=[("a", 0.0)],
        repair_cond=False,
    )

    # -- The clock. On a component of its own, so no scenario depends on
    #    another one's events to be integrated.
    system.add_component(name="CLOCK", cls="DsmSource", flow="tick", rate=0.0)
    system.comp["CLOCK"].add_atm2states(
        name="tick",
        st1="a",
        st2="b",
        occ_law_12={"cls": "delay", "time": DSM_TICK},
        occ_law_21={"cls": "delay", "time": DSM_TICK},
    )

    return system


def delivered(system, comp_name, flow_name):
    """What one of a component's inputs currently receives."""
    return system.comp[comp_name].flows_in[flow_name].get_delivered()


def out_value(system, comp_name, flow_name):
    """What a component currently delivers on one of its outputs."""
    return system.comp[comp_name].flows_out[flow_name].var_fed.value()


@pytest.fixture(scope="module")
def the_run():
    """Drive the one system to the horizon, snapshotting as it goes.

    Two kinds of reading come out of it: the algebraic scenarios settle on the
    first step and are read at the end, while the buffer is a trajectory whose
    **peak** is the assertion -- a level that overshoots and comes back would
    pass a reading taken only at the horizon.
    """
    system = build_system()
    system.isimu_start()

    buffer_peak = 0.0
    main_peak = 0.0
    running = None
    while system.currentTime() < DSM_HORIZON:
        system.isimu_step_forward()
        cell = system.comp["P_ELY"].capacities["buffer_h2"]
        buffer_peak = max(buffer_peak, cell.total_quantity())
        main_peak = max(main_peak, out_value(system, "H_REACT", "a"))
        # The stoichiometry, caught WHILE the cell runs. Read once, on the
        # first step that has an inflow: at the horizon everything is zero
        # and a ratio between two zeros asserts nothing.
        if running is None and cell.get_inflow("h2") > 0.0:
            running = {
                "into_buffer": cell.get_inflow("h2"),
                "o2": out_value(system, "P_ELY", "o2"),
            }

    obs = {
        "time": system.currentTime(),
        "blocked": {
            "power": delivered(system, "B_ELY", "power"),
            "h2": out_value(system, "B_ELY", "h2"),
            "o2": out_value(system, "B_ELY", "o2"),
        },
        "buffered": {
            "power": delivered(system, "P_ELY", "power"),
            "h2": out_value(system, "P_ELY", "h2"),
            "o2": out_value(system, "P_ELY", "o2"),
            "level": system.comp["P_ELY"].capacities["buffer_h2"].total_quantity(),
            "peak": buffer_peak,
            "running": running,
        },
        "unequal": {
            "feed": delivered(system, "U_SPLIT", "feed"),
            "a": out_value(system, "U_SPLIT", "a"),
            "b": out_value(system, "U_SPLIT", "b"),
            "a_received": delivered(system, "U_BIG", "a"),
            "b_received": delivered(system, "U_SMALL", "b"),
        },
        "unwired": {
            "feed": delivered(system, "W_T", "feed"),
            "good": out_value(system, "W_T", "good"),
            "good_received": delivered(system, "W_C", "good"),
        },
        "declared_vent": {
            "feed": delivered(system, "D_T", "feed"),
            "good": out_value(system, "D_T", "good"),
            "good_received": delivered(system, "D_C", "good"),
            "vent": out_value(system, "D_T", "vent"),
        },
        "tank": {
            "feed": delivered(system, "T_SPLIT", "feed"),
            "a": out_value(system, "T_SPLIT", "a"),
            "b": out_value(system, "T_SPLIT", "b"),
            "level": system.comp["T_TANK"].capacities["capacity"].total_quantity(),
        },
        "coef": {
            "feed": delivered(system, "C_RATIO", "feed"),
            "a": delivered(system, "C_A", "a"),
            "b": delivered(system, "C_B", "b"),
        },
        "throttle": {
            "main_peak": main_peak,
            "main": out_value(system, "H_REACT", "a"),
            "feed": delivered(system, "H_REACT", "feed"),
            "level": system.comp["H_BUF"].capacities["capacity"].total_quantity(),
        },
        "derated": {
            "feed": delivered(system, "R_SPLIT", "feed"),
            "a": out_value(system, "R_SPLIT", "a"),
            "b": out_value(system, "R_SPLIT", "b"),
        },
    }

    system.isimu_stop()
    obs["system"] = system
    return obs


# ----------------------------------------------------------------------
# A blocked outlet stops the reaction
# ----------------------------------------------------------------------


def test_a_blocked_outlet_stops_the_reaction(the_run):
    """No hydrogen outlet, no electrolysis -- and therefore no oxygen either.

    Under a maximum the oxygen discharge alone carried the rule: the cell drew
    its full power, vented its oxygen and destroyed two units of hydrogen an
    hour.
    """
    blocked = the_run["blocked"]
    assert blocked["power"] == pytest.approx(0.0, abs=DSM_EPS)
    assert blocked["h2"] == pytest.approx(0.0, abs=DSM_EPS)
    assert blocked["o2"] == pytest.approx(0.0, abs=DSM_EPS)


def test_a_running_cell_places_both_products_in_the_declared_ratio(the_run):
    """Stoichiometry as a balance, read on a cell that is actually running.

    On the blocked cell the same statement would be ``0 == 2 x 0``, true of
    any semantics and therefore worth nothing. Read here on the BUFFERED cell
    while it fills, where both terms are non-zero: what enters the buffer is
    exactly twice the oxygen leaving, which is the rule's coefficients holding
    on the wire. A product made and dropped breaks this, and nothing else in
    the model records it.

    The hydrogen term is the buffer's INFLOW, not the outlet's ``var_fed``:
    that variable carries what was DELIVERED, which is zero here whatever the
    cell does, and reading it would assert nothing again.
    """
    running = the_run["buffered"]["running"]
    assert running is not None, "the buffered cell never ran"
    assert running["o2"] > 0.0
    assert running["into_buffer"] == pytest.approx(2.0 * running["o2"], abs=DSM_EPS)


# ----------------------------------------------------------------------
# The bound of a capacity is a bound
# ----------------------------------------------------------------------


def test_a_buffer_behind_a_blocked_outlet_never_exceeds_its_volume(the_run):
    """The invariant the maximum walked through: 39 units in a 10-unit buffer.

    Asserted on the PEAK over the whole run, not on the final reading: an
    overshoot that later drains would slip past an end-of-run assertion, and
    an overshoot is exactly the failure mode.
    """
    assert the_run["buffered"]["peak"] <= DSM_BUFFER + DSM_BOUND_EPS


def test_a_full_buffer_stops_the_reaction_that_fills_it(the_run):
    """Once the buffer is full the outlet is blocked for good, so the cell
    stops -- power drawn and oxygen both back to zero, the buffer resting on
    its bound."""
    buffered = the_run["buffered"]
    assert buffered["level"] == pytest.approx(DSM_BUFFER, abs=DSM_BOUND_EPS)
    assert buffered["power"] == pytest.approx(0.0, abs=DSM_EPS)
    assert buffered["h2"] == pytest.approx(0.0, abs=DSM_EPS)
    assert buffered["o2"] == pytest.approx(0.0, abs=DSM_EPS)


# ----------------------------------------------------------------------
# Unequal demands: the smaller one sizes the rule
# ----------------------------------------------------------------------


def test_the_least_demanded_output_sizes_the_rule(the_run):
    """One consumer wants ten, the other two: the rule runs at two.

    The bigger consumer goes short, which is the honest answer -- serving it
    would take making eight units of the other product for a consumer that
    cannot take them.
    """
    unequal = the_run["unequal"]
    assert unequal["feed"] == pytest.approx(2.0, abs=DSM_EPS)
    assert unequal["a"] == pytest.approx(2.0, abs=DSM_EPS)
    assert unequal["b"] == pytest.approx(2.0, abs=DSM_EPS)


def test_neither_product_of_an_unequal_split_is_destroyed(the_run):
    """The balance, read where the loss is actually visible.

    Not "delivered equals received": ``var_fed`` on an output IS the delivered
    quantity, so the two agree even while matter goes missing -- which is the
    whole reason the defect was silent. The reagent draw is the only witness.
    One unit of feed makes one of each product, so a conserving rule delivers
    on BOTH outlets exactly what it drew. Under the maximum the splitter drew
    ten, delivered ten and two, and eight units left no trace anywhere in the
    model.
    """
    unequal = the_run["unequal"]
    assert unequal["a"] == pytest.approx(unequal["feed"], abs=DSM_EPS)
    assert unequal["b"] == pytest.approx(unequal["feed"], abs=DSM_EPS)


# ----------------------------------------------------------------------
# It is demand DIVIDED BY coefficient that is compared
# ----------------------------------------------------------------------


def test_the_coefficients_decide_which_output_constrains(the_run):
    """Two consumers asking for the same four, and they do not weigh the same.

    The rule makes two of ``a`` per one of ``b``, so four of ``a`` costs a
    scale of two while four of ``b`` would cost four. Two is the scale, and
    the smaller consumer gets half of what it asked.

    The only scenario of this module in which the division decides: a scale
    computed on the bare demand would give four here and pass every other
    test in the file. It is also the counter-example to reading the migration
    note as "outputs asked the same quantity do not move" -- they are asked
    the same and they move.
    """
    coef = the_run["coef"]
    assert coef["feed"] == pytest.approx(2.0, abs=DSM_EPS)
    assert coef["a"] == pytest.approx(4.0, abs=DSM_EPS)
    assert coef["b"] == pytest.approx(2.0, abs=DSM_EPS)


# ----------------------------------------------------------------------
# The shape a modeller actually meets
# ----------------------------------------------------------------------


def test_a_saturated_secondary_buffer_throttles_the_main_product(the_run):
    """A by-product buffered on its way to a slow consumer, which is the
    everyday form of the blocked outlet above.

    The reaction runs at ten while the buffer takes the difference, then the
    buffer saturates and the whole cell follows the slow consumer at one. The
    main product drops by a factor of ten because of a constraint on the OTHER
    outlet, which is the consequence of this semantics a modeller is most
    likely to meet and to find surprising. Under the old maximum the main
    product stayed at ten and nine units an hour of by-product were dropped.

    Measured beside it and worth recording: the regime settles in one step and
    does not chatter. The buffer's accept bound is a lagged reading, which is
    the shape a latch comes in, and it does not latch here.
    """
    throttle = the_run["throttle"]
    assert throttle["main_peak"] == pytest.approx(10.0, abs=DSM_EPS)
    assert throttle["main"] == pytest.approx(DSM_SLOW, abs=DSM_EPS)
    assert throttle["feed"] == pytest.approx(DSM_SLOW, abs=DSM_EPS)
    assert throttle["level"] == pytest.approx(DSM_SECONDARY, abs=DSM_BOUND_EPS)


# ----------------------------------------------------------------------
# Both ways of declaring a discharge keep working
# ----------------------------------------------------------------------


def test_an_unwired_discharge_still_constrains_nothing(the_run):
    """R-10, unchanged: an outlet nobody is connected to is dropped from the
    scale, so the useful consumer alone sizes the rule. Turning a minimum on
    an outlet asked for nothing would have collapsed this model to zero."""
    unwired = the_run["unwired"]
    assert unwired["feed"] == pytest.approx(5.0, abs=DSM_EPS)
    assert unwired["good"] == pytest.approx(5.0, abs=DSM_EPS)
    assert unwired["good"] == pytest.approx(unwired["good_received"], abs=DSM_EPS)


def test_a_declared_discharge_does_not_run_the_rule_at_its_own_rate(the_run):
    """The pattern R-10 recommends, working at last.

    A discharge asking for a thousand does not make the rule produce a
    thousand: it is not a demand to serve, it is an outlet that will take
    whatever comes. The useful consumer sizes the rule and the discharge
    receives what the rule happens to make.
    """
    vent = the_run["declared_vent"]
    assert vent["feed"] == pytest.approx(5.0, abs=DSM_EPS)
    assert vent["good"] == pytest.approx(5.0, abs=DSM_EPS)
    assert vent["good"] == pytest.approx(vent["good_received"], abs=DSM_EPS)
    assert vent["vent"] == pytest.approx(5.0, abs=DSM_EPS)


# ----------------------------------------------------------------------
# An unbounded claim never wins a minimum
# ----------------------------------------------------------------------


def test_a_filling_tank_does_not_out_vote_a_metered_consumer(the_run):
    """R36 meets the minimum, and nothing is lost either way.

    A tank claims its fill rate without bound -- "deliver whatever you can".
    An unbounded claim can never win a minimum, so the metered consumer sizes
    the rule and the tank fills at three an hour instead of drawing the whole
    supply and destroying the other product.
    """
    tank = the_run["tank"]
    assert tank["feed"] == pytest.approx(3.0, abs=DSM_EPS)
    assert tank["a"] == pytest.approx(3.0, abs=DSM_EPS)
    assert tank["b"] == pytest.approx(3.0, abs=DSM_EPS)
    # The level, at the rate the rule was sized to and not merely above zero:
    # a defect filling at a twentieth of that would pass "> 0".
    assert tank["level"] == pytest.approx(3.0 * the_run["time"], rel=1e-3)


# ----------------------------------------------------------------------
# A loss is not an absent outlet
# ----------------------------------------------------------------------


def test_a_derated_outlet_does_not_stop_the_draw(the_run):
    """The distinction this module exists to keep: a fault destroys, an absent
    outlet forbids.

    Outlet ``a`` is cut to zero by a failure mode while its consumer asks
    normally. The reagent draw holds and outlet ``b`` goes on delivering,
    because the product IS made and lost on the way out. Reading the derating
    as a demand of zero would stop the draw and make ``b`` out of nothing --
    which is why ``get_uptake_factor`` takes a maximum where
    ``get_demand_scale`` takes a minimum.
    """
    derated = the_run["derated"]
    assert derated["a"] == pytest.approx(0.0, abs=DSM_EPS)
    assert derated["feed"] == pytest.approx(4.0, abs=DSM_EPS)
    assert derated["b"] == pytest.approx(4.0, abs=DSM_EPS)


# ----------------------------------------------------------------------
# Teardown -- PyCATSHOO holds one live system per process
# ----------------------------------------------------------------------


def test_delete(the_run):
    """Hand the process back, as every module here does: a system left alive
    makes every module collected after this one fail to build its own."""
    the_run["system"].deleteSys()
    cod3s.terminate_session()
