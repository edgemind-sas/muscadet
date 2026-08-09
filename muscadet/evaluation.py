"""Two-sweep evaluation of a component's continuous flows.

The algorithm a continuous component runs at every integration step, as
functions over a component rather than as methods on it -- the shape the
sibling units of this release already use (``muscadet.ordering``,
``muscadet.capacity``). ``ObjFlow`` binds each of them as a method of the same
name, so ``comp.compute_demand()`` and every override point it carries are
exactly what they were.

Two sweeps, in this order (R8, KD4):

- **demand**, upstream: :func:`compute_demand` publishes on each continuous
  input what the component needs from it, mapped back from the demand its
  outputs carry through the active rule's declared coefficients (R34), then
  claimed by an interposed input capacity (R7, R36);
- **production**, downstream: :func:`compute_production` runs the active rule
  of each rule set at the scale its scarcest input allows (R15) -- or transfers
  each input to the output of the same name when the component declares no rule
  at all (R31) -- caps the draw on the suppliers at that same scale and hands
  back what it did not take (:func:`release_unused_supply`, R-12), then delivers
  the lesser of what was produced and what was asked for, split among the
  consumers by the output's allocation policy (R16).

Three quantities are meant to agree, and two of them now do::

    demand        what a component publishes upstream
    delivery      what its suppliers actually give it
    consumption   what its rule actually uses

``delivery == consumption`` is enforced by :func:`release_unused_supply`: what
a supplier gives up is what a reaction, a stock or an output receives, so
nothing is destroyed. ``demand == consumption`` does **not** hold: the demand is
sized on the declared coefficients before the scale is known, so a rule limited
by one reagent still ASKS for its nominal share of the others. It no longer
takes it, but it still competes for it, which distorts the split of a supply two
components share. Closing that is a design decision, not a missing pass -- see
Scope Boundaries.

``muscadet.ordering`` looks the two equation methods up BY NAME and registers
them with an order derived from the connection graph, so nothing here and
nothing in a model ever writes an equation order down.

Both are evaluated by the solver, repeatedly, inside one integration step: they
must stay pure functions of the current variable values and must not create or
register anything.
"""

import math

from .flow_continuous import (
    UNBOUNDED,
    UPTAKE_TOL,
    FlowContinuous,
    FlowContinuousIn,
    FlowContinuousOut,
)
from .profile import NOMINAL_FACTOR
from .rules import (
    UNCONSTRAINED_SCALE,
    rule_consumption,
    rule_production,
    rule_scale,
)

#: The demand an output NOTHING draws on faces, before its own capacity claims
#: on it. The counterpart of :data:`~muscadet.flow_continuous.UNBOUNDED`, and
#: deliberately a different statement: "nothing asks" is not "asks for
#: everything". Only reached when a capacity sits behind the output, which is
#: what makes the volume -- rather than a modelled sink downstream -- the thing
#: production meets (R36).
NO_DEMAND = 0.0


def compute_demand(comp):
    """
    PDMP equation: what this component asks its producers for.

    Named after ``muscadet.ordering.DEMAND_EQUATION_METHOD``: the ordering
    module looks this method up BY NAME on every node of the continuous-flow
    graph and registers it with a graph-derived order on the **reverse**
    sweep, so a consumer publishes its demand before the component feeding
    it computes its own (R8). Nothing here, and nothing in a model, ever
    writes an order down.

    Demand travels the graph in the opposite direction to production (KD4):
    each continuous input publishes upstream what the component needs from
    it, and the producer then delivers the lesser of what it can produce and
    what was asked for (R6).

    Notes
    -----
    Evaluated by the solver, repeatedly, inside one integration step: it
    must stay a pure function of the current variable values and must not
    create or register anything.
    """
    # This evaluation's demand bounds, discarded from the previous one. It
    # is filled by get_output_demand below and consulted by the production
    # sweep -- a per-evaluation hand-off, never a memo across evaluations.
    comp._demand_bound.clear()

    comp.apply_demand(comp.evaluate_demand())


def evaluate_demand(comp):
    """
    Computes what the component needs from each of its continuous inputs.

    The mapping of R34: the demand aggregated on an output is carried back
    onto the inputs through the active rule's ``prod`` and ``cons``
    coefficients. It uses the **declared** coefficients, never the
    quantities actually available -- a component capped by a scarce input
    therefore still claims its nominal demand on the others, and over-claims
    a shared upstream supply.

    What it no longer does is TAKE the over-claim: the production sweep caps
    the draw at the scale it computes and releases the rest (R-12). So the
    over-claim costs an allocation distortion between rivals and no longer
    costs conservation.

    The distortion itself cannot be closed by looking at what arrived. A
    delivery is ``min(capability, demand)``, so a demand computed from
    deliveries is self-referential: bounding it by what the inputs allowed at
    the previous evaluation has ZERO as its only fixed point -- measured, a
    reactor starved from 0.1 to 5e-4 over 4000 evaluations -- and the variant
    that only counts a saturated input oscillates with period 2 between the
    nominal claim and the achievable one. What is missing is the suppliers'
    CAPABILITY, which ``min(capability, demand)`` destroys and which no lag can
    recover. Scope Boundaries records the decision.

    A component declaring no rule transfers each input to the output of the
    same name (R31), so its demand crosses it unchanged -- but only from an
    output that can carry one. An unconnected output asks for nothing, so the
    transfer asks for nothing either: the rule :meth:`get_demand_scale`
    applies to a rule's outputs (R-10), applied to the one path that has no
    declared coefficients to fall back on. An unwired output therefore never
    makes its component compete for the supply upstream, whether that
    component declares rules or not -- the two paths agree, which they did
    not while a pass-through claimed without bound.

    A deliberate open discharge stays modellable, and more legibly: declare
    the vent as a consumer with its own demand, so the intent is visible
    instead of resting on a connection that is absent.

    An output **capacity** is the one thing that still asks with nobody
    connected: it claims a fill rate for the volume itself
    (:meth:`output_capacity_claims_demand`, R36), which is what makes a tank
    at the end of a chain fill instead of asking for nothing.

    An input no rule and no transfer covers is a pure consumer's input: it
    claims the demand it was DECLARED with, ``var_demand_default``.

    Returns
    -------
    dict
        ``{input flow name: demand}``, possibly ``math.inf``: a connected
        consumer may publish an unbounded demand -- a capacity claiming its
        fill rate -- and a rule-less transfer carries that across unchanged.

    Raises
    ------
    ValueError
        When two guards of one rule set hold at once (R13). The R31 mismatch
        of a rule-less component is deliberately NOT raised here: it belongs
        to the production sweep, where it was already reported, and a
        component replacing that sweep with an equation of its own must not
        be refused by the demand sweep instead.
    """
    demands = {}

    def accumulate(flow_name, quantity):
        demands[flow_name] = demands.get(flow_name, 0.0) + quantity

    if comp.rule_sets:
        for rule_set in comp.rule_sets.values():
            # Every flow the SET consumes starts at zero, whichever of its
            # rules is active: a rule set selecting nothing demands nothing,
            # exactly as it produces nothing (R14).
            for flow_name in rule_set.consumed_flows:
                accumulate(flow_name, 0.0)

            rule = comp.get_active_rule(rule_set)
            if rule is None:
                continue

            scale = comp.get_demand_scale(rule)
            for flow_name, coefficient in rule.cons.items():
                if coefficient <= 0:
                    # A rule consuming nothing of a flow demands nothing of
                    # it -- the catalyst idiom, named by the rule so it can
                    # be guarded on, drawn on at coefficient 0. The guard
                    # mirrors muscadet.rules.rule_scale, which skips the
                    # same coefficients: without it the scale of a rule
                    # whose output nobody is connected to is unbounded, and
                    # 0 * inf would publish NaN upstream.
                    accumulate(flow_name, 0.0)
                    continue
                accumulate(flow_name, coefficient * scale)
    else:
        for flow_name in comp.get_transferable_flows():
            if not comp.output_carries_demand(flow_name):
                # Accumulated as an explicit zero rather than skipped: a name
                # left out of ``demands`` falls through to the
                # ``var_demand_default`` of a pure consumer's input below,
                # which would turn "asks for nothing" into "asks for its
                # declared default" -- the opposite of the intent.
                accumulate(flow_name, 0.0)
                continue
            accumulate(flow_name, comp.get_output_demand(flow_name))

    # A continuous input no rule and no transfer covers claims what it was
    # declared with: a pure consumer has no output to map a demand back from.
    for flow_name, flow in comp.flows_continuous_in.items():
        demands.setdefault(flow_name, float(flow.var_demand_default))

    return demands


def get_demand_scale(comp, rule):
    """
    Returns the scale ``rule`` would have to run at to satisfy its outputs.

    The scale is taken over the ``prod`` coefficients, as a **maximum**: the
    rule's outputs are correlated by construction, so the scale that serves
    them all is the one the most demanding of them needs.

    Only the outputs that actually CONSTRAIN the rule take part in it --
    :meth:`output_constrains_demand`. An output nothing asks anything of
    constrains nothing, so it must not enter the maximum at all: entering it
    as an unbounded demand would let a single unwired output dominate every
    connected one and make the component claim its whole upstream supply, a
    vent or a half-assembled model silently over-drawing a shared source.

    "Nobody is connected" and "somebody is asking for nothing" stay strictly
    apart. A consumer publishing a demand of zero is a real bound and gives a
    scale of zero, which is what stops the rule; only the absence of a
    consumer is dropped. And a genuinely unbounded demand published BY a
    connected consumer -- a capacity claiming ``inf`` for its filling (R36),
    a downstream itself unconstrained -- still travels, because the test is
    structural (is there a consumer?) and never reads the demand's value.

    A rule left with no constraining output at all -- producing nothing, or
    producing only into unwired outputs -- falls back to the nominal scale,
    exactly as a rule producing nothing always did.

    An output **capacity** takes part whether or not the output is wired: it
    is not the output that constrains there but the volume behind it, which
    claims a fill rate for itself and is throttled by its own accept bound
    (:meth:`output_capacity_claims_demand`). The two are added by
    :meth:`get_output_demand` and enter the maximum as one figure.

    Parameters
    ----------
    rule : muscadet.rules.Rule
        The active rule of one of the component's rule sets.

    Returns
    -------
    float
        The scale, possibly ``math.inf``.
    """
    scales = []

    for flow_name, coefficient in rule.prod.items():
        if coefficient <= 0:
            continue

        # Read unconditionally, and filtered afterwards: get_output_demand
        # records the bound the production sweep of this same evaluation
        # reads back (see get_output_request), so every produced output must
        # go on being read exactly as it was before this filter existed.
        demand = comp.get_output_demand(flow_name)

        if not comp.output_carries_demand(flow_name):
            continue

        scales.append(demand / coefficient)

    if not scales:
        return UNCONSTRAINED_SCALE

    return max(scales)


def output_constrains_demand(comp, flow_name):
    """
    Tells whether an output can bound the scale its rule runs at.

    An output constrains a rule when something downstream can ask it for a
    quantity -- which takes both a demand channel and somebody connected to
    it. The two ways of failing that are the two ways
    :meth:`get_output_demand` answers :data:`~muscadet.flow_continuous.
    UNBOUNDED` without any consumer having said so:

    - a **discrete** output named in a ``prod`` map. It carries no demand
      channel at all: a boolean production is not a quantity, and it throttles
      nothing;
    - a **continuous** output with no connection. Nothing consumes it, so it
      asks for nothing.

    Purely structural: the demand's value is never read, so an ``inf``
    published by a real consumer -- a capacity claiming its fill rate (R36) --
    keeps propagating as the "deliver whatever you can" it means.

    Parameters
    ----------
    flow_name : str
        Name of an output flow of the component.

    Returns
    -------
    bool
    """
    flow = comp.flows_out.get(flow_name)

    if not isinstance(flow, FlowContinuousOut):
        return False

    return comp.continuous_flow_is_connected(flow, "out")


def output_capacity_claims_demand(comp, flow_name):
    """
    Tells whether the VOLUME behind an unasked output still asks for something.

    The second, additive source of demand an output can carry, and the one
    R-10 says nothing about: R-10 governs whether an **output** constrains a
    rule -- it does not, with nobody connected -- while a fill claim belongs
    to the capacity, which is entitled to fill "for itself, over and above
    the demand it already carries" (R36) whether or not anything is wired
    downstream of it.

    Without this, a two-sided capacity at the end of a chain was a silent
    dead model: its own output being connected to nothing,
    :meth:`evaluate_demand` never asked it for a demand, the fill claim was
    never consulted at all, the tank asked for nothing, its producer produced
    nothing, and the whole plant read zero with the ``fill_rate`` accepted and
    inert.

    Structural, exactly like :meth:`output_constrains_demand`: it asks
    whether a capacity is DECLARED on this output's side and never reads what
    the claim is worth, so an unbounded fill rate keeps travelling as the
    "deliver whatever you can" it means. A capacity claiming zero -- the
    default, a pure pass-through buffer -- therefore still carries a demand
    of zero rather than dropping out of the sizing, which is the same
    distinction R-10 draws between "nobody is asking" and "somebody is asking
    for nothing".

    Restricted to an output NOTHING is connected to: with a consumer wired,
    :meth:`output_constrains_demand` already covers the output and
    :meth:`get_output_demand` already adds the capacity's claim on top.

    Parameters
    ----------
    flow_name : str
        Name of an output flow of the component.

    Returns
    -------
    bool
    """
    flow = comp.flows_out.get(flow_name)

    if not isinstance(flow, FlowContinuousOut):
        return False

    if comp.continuous_flow_is_connected(flow, "out"):
        return False

    return comp.get_capacity_of_flow(flow_name, "out") is not None


def output_carries_demand(comp, flow_name):
    """
    Tells whether an output takes part in sizing what the component asks for.

    The union of the two ways an output can carry a demand back into the
    component, and the single predicate both sizing paths consult -- the rule
    scale of :meth:`get_demand_scale` and the rule-less transfer of
    :meth:`evaluate_demand` -- so the two cannot drift apart on what an
    unwired output means:

    - something downstream can ask it for a quantity
      (:meth:`output_constrains_demand`, R-10);
    - a capacity of its own sits behind it and claims for the volume
      (:meth:`output_capacity_claims_demand`, R36).

    Both are structural and neither reads a demand's value.

    Parameters
    ----------
    flow_name : str
        Name of an output flow of the component.

    Returns
    -------
    bool
    """
    if comp.output_constrains_demand(flow_name):
        return True

    return comp.output_capacity_claims_demand(flow_name)


def get_output_consumer_demand(comp, flow):
    """
    Returns what the CONSUMERS of a continuous output ask it for.

    The demand before any capacity claim, and the one place the three
    structurally different answers are written down:

    - **connected** -- the sum the consumers publish, possibly
      :data:`~muscadet.flow_continuous.UNBOUNDED` when one of them asks
      without bound;
    - **unconnected, with a capacity behind it** -- :data:`NO_DEMAND`.
      Nothing draws the volume onward, so what is produced stays in it. This
      is what stops a buffered output venting a tank into a connection that
      does not exist, and it is what makes the fill claim of R36 the whole of
      the demand such an output carries;
    - **unconnected, with no capacity** -- ``UNBOUNDED``, unchanged: an
      output nobody is connected to is a modelled sink that takes whatever is
      produced (R-10), and a vent still discharges what the reaction makes.

    Parameters
    ----------
    flow : muscadet.flow_continuous.FlowContinuousOut
        The output being sized.

    Returns
    -------
    float
    """
    if comp.continuous_flow_is_connected(flow, "out"):
        return flow.get_demand_bound()

    if comp.get_capacity_of_flow(flow.name, "out") is not None:
        return NO_DEMAND

    return UNBOUNDED


def get_output_demand(comp, flow_name):
    """
    Returns the demand an output carries back into the component.

    Two bounds compose here:

    - what the consumers ask for
      (:meth:`get_output_consumer_demand`): :data:`NO_DEMAND` when none is
      connected and a capacity sits behind the output,
      :data:`~muscadet.flow_continuous.UNBOUNDED` when none is connected and
      none does;
    - what an interposed output capacity makes of it
      (:meth:`~muscadet.capacity.Capacity.demand_claim`): while it has room
      it adds its own fill claim, so the rules run beyond what the consumers
      draw and the buffer accumulates the difference (R36); once full it
      accepts only what currently leaves it, which is how a full capacity
      reduces the demand propagated upstream (R7).

    The claim is ADDITIONAL to the R34 mapping and never replaces it: this
    is the demand the rules face, which :meth:`get_demand_scale` then carries
    back onto the inputs through the active rule's declared coefficients.

    Parameters
    ----------
    flow_name : str
        Name of an output flow of the component.

    Returns
    -------
    float
        The demand, possibly ``math.inf``. A discrete output carries no
        demand channel and never throttles anything.
    """
    flow = comp.flows_out.get(flow_name)

    if not isinstance(flow, FlowContinuousOut):
        return UNBOUNDED

    demand = comp.get_output_consumer_demand(flow)

    # Kept for the production sweep of this same evaluation, which needs
    # the bound BEFORE the capacity claim below (see get_output_request).
    comp._demand_bound[flow_name] = demand

    capacity = comp.get_capacity_of_flow(flow_name, "out")
    if capacity is not None:
        demand = capacity.demand_claim(demand, flow_name)

    return demand


def apply_demand(comp, demands):
    """
    Publishes the demands upstream, as the input capacities claim them (R7, R36).

    Two quantities, deliberately kept apart:

    - what the rules may draw, kept on the flow as ``demand_required`` and
      read back by :meth:`get_input_available`. It is the R34 mapping, and
      an input capacity never changes it;
    - what is PUBLISHED upstream, which the input capacity claims through
      :meth:`~muscadet.capacity.Capacity.demand_claim`: with room left it
      asks for its fill rate on top, and what arrives beyond what the rules
      draw stays in the volume (R36); once full it asks only for what
      currently leaves it, so the producer feeding a capacity at its volume
      delivers less (AE11) while the rules keep drawing from the stock.

    Parameters
    ----------
    demands : dict
        ``{input flow name: demand}``, as returned by
        :meth:`evaluate_demand`.
    """
    for flow_name, demand in demands.items():
        flow = comp.flows_in.get(flow_name)

        # Only a continuous input carries a demand channel. A discrete input
        # named by a rule is a gate, not a quantity.
        if not isinstance(flow, FlowContinuousIn):
            continue

        required = max(float(demand), 0.0)
        flow.demand_required = required

        capacity = comp.get_capacity_of_flow(flow_name, "in")
        if capacity is not None:
            required = capacity.demand_claim(required, flow_name)

        flow.set_demand(required)


def get_input_required_demand(comp, flow_name):
    """
    Returns what the rules may draw from an input, as the demand sweep sees it.

    Unbounded for a discrete input, and for a continuous one whose demand
    equation has not run: the production sweep then behaves exactly as it
    did before demand existed, producing whatever the inputs allow.
    """
    flow = comp.flows_in.get(flow_name)

    if not isinstance(flow, FlowContinuousIn):
        return UNBOUNDED

    return flow.demand_required


def current_time(comp):
    """
    Returns the simulation instant the sweeps are being evaluated at.

    Read from the engine, inside an equation method, which is where the
    solver's integration clock is meaningful: it is the instant a time
    profile is a function of. The override point for a component that
    reckons time differently -- an offset epoch, a shifted season.

    Falls back to 0 only when there is no engine system to ask, which is
    what a flow primitive exercised outside a run sees.
    """
    system = comp.system()

    return 0.0 if system is None else float(system.currentTime())


def compute_production(comp):
    """
    PDMP equation: what this component produces at this integration step.

    Named after ``muscadet.ordering.PRODUCTION_EQUATION_METHOD``: the
    ordering module looks this method up BY NAME on every node of the
    continuous-flow graph and registers it with a graph-derived order, so
    nothing here -- and nothing in a model -- ever writes an order down
    (R8).

    One equation covers every rule set of the component: the active rule of
    each is evaluated at the scale its scarcest input allows (R15), and a
    component declaring no rule at all transfers each input to the output
    of the same name (R31). The four hops of KTD13 are crossed in order:
    the input flows fill their capacity, the rules draw from it, production
    enters the output capacity, and the output flows draw from that.

    Notes
    -----
    Evaluated by the solver, repeatedly, inside one integration step: it
    must stay a pure function of the current variable values and must not
    create or register anything.
    """
    comp.refresh_continuous_inputs()
    comp.fill_input_capacities()

    consumption, production = comp.evaluate_production()

    comp.apply_consumption(consumption)
    comp.release_unused_supply(consumption)
    comp.apply_production(production)


def fill_input_capacities(comp):
    """
    Fills every input capacity with what its flows deliver (KTD13, hop 1).

    Unconditional, over the whole set of buffered input flows, and NOT over
    the flows the active rule happens to consume: hop 1 is a property of the
    wiring -- what arrives on an input enters the capacity buffering it --
    and has nothing to do with which rule is currently selected. Driving it
    from the rules instead would leave the inflow of a flow the newly active
    rule stopped consuming at the rate the PREVIOUS mode wrote, while
    :meth:`apply_consumption` correctly zeroes the outflow over the whole
    rule-set footprint: the capacity would then integrate an imbalance that
    no producer delivers, and create quantity from nothing.

    The symmetric reset on the consumption side is what
    :meth:`evaluate_production` does with ``rule_set.consumed_flows``.
    """
    for capacity in comp.capacities_in.values():
        for flow_name in capacity.flow_names:
            capacity.set_inflow(flow_name, comp.get_input_delivered(flow_name))


def refresh_continuous_inputs(comp):
    """
    Mirrors what each continuous input receives onto its ``var_fed`` (R6).

    The mirror is otherwise refreshed by a sensitive method, which the
    solver runs when the value a producer EXPORTS changes -- and an
    allocation can move without that value moving at all, when a producer
    keeps delivering the same total and only the split among its consumers
    changes. Rewriting it here, at the head of the forward sweep and
    therefore after every producer has been evaluated, is what keeps the
    input a model reads equal to the share it was allocated.
    """
    for flow in comp.flows_continuous_in.values():
        flow.var_fed.setValue(flow.get_delivered())


def evaluate_production(comp):
    """
    Computes what the component draws from its inputs and what it produces.

    Returns
    -------
    tuple of dict
        ``(consumption, production)``, both ``{flow name: rate}``. Rule
        sets accumulate: a flow named by two of them carries their sum.
        Every flow a rule set names appears, at zero when the active mode
        does not name it -- a rate another mode left behind must be cleared,
        not inherited.

    Raises
    ------
    ValueError
        When the component declares no rule and its continuous flows do not
        match name for name (R31), or when two guards of one rule set hold
        at once (R13).
    """
    consumption = {}
    production = {}

    def accumulate(target, quantities):
        for flow_name, quantity in quantities.items():
            target[flow_name] = target.get(flow_name, 0.0) + quantity

    if comp.rule_sets:
        for rule_set in comp.rule_sets.values():
            # Every flow the SET names starts at zero, whichever of its
            # rules is active. Written rather than left out: a flow the
            # previously active mode produced -- or drew from a capacity --
            # would otherwise keep the rate that mode left on it, and a rule
            # set selecting nothing must produce zero (R14).
            accumulate(consumption, dict.fromkeys(rule_set.consumed_flows, 0.0))
            accumulate(production, dict.fromkeys(rule_set.produced_flows, 0.0))

            rule = comp.get_active_rule(rule_set)
            if rule is None:
                continue

            available = {
                flow_name: comp.get_input_available(flow_name)
                for flow_name in rule.cons
            }
            scale = rule_scale(rule, available)

            accumulate(consumption, rule_consumption(rule, scale))
            accumulate(production, rule_production(rule, scale))
    else:
        for flow_name in comp.get_identity_transfer_flows():
            transferred = comp.get_input_transferred(flow_name)
            accumulate(consumption, {flow_name: transferred})
            accumulate(production, {flow_name: transferred})

    # A continuous output no rule and no transfer names is a SOURCE: the
    # value it was declared with is what it can produce. It appears here so
    # that what it delivers is reconciled with the demand like any other
    # production -- an output holding a rate nobody asks for delivers less.
    for flow_name, flow in comp.flows_continuous_out.items():
        production.setdefault(flow_name, float(flow.var_fed_default))

    return consumption, production


def get_active_rule(comp, rule_set):
    """
    Returns the rule of ``rule_set`` whose production is evaluated, or None.

    The override point for rule selection. Guards are compiled into a mode
    automaton by :meth:`compile_rule_mode`, and the automaton's ACTIVE STATE
    is what designates the rule: reading a state rather than re-evaluating
    the guards here is what freezes the coefficients within a mode. A set
    carrying no guard has no automaton and yields its default rule.

    Parameters
    ----------
    rule_set : muscadet.rules.RuleSet
        The rule set to select a rule from.

    Returns
    -------
    muscadet.rules.Rule or None
        None means "produce nothing for this rule set" (R14).

    Raises
    ------
    ValueError
        When two guards of the set hold at once (R13). Checked at every
        evaluation, which is where a conflict can first be seen: it depends
        on the current variable values.
    """
    return rule_set.active_rule()


def get_input_delivered(comp, flow_name):
    """
    Returns what an input flow currently delivers to the component.

    Read from the flow's REFERENCE rather than from its ``var_fed`` mirror:
    the mirror is refreshed by a sensitive method, which the solver runs
    between integration steps and not between two equations of the same
    step. Reading the reference is what makes a chain of components settle
    within a single step instead of lagging one step per hop.

    Parameters
    ----------
    flow_name : str
        Name of an input flow of the component.

    Returns
    -------
    float
        The delivered quantity; the declared default when nothing is
        connected. A discrete input consumed by a rule reads as 1 when fed
        and 0 otherwise.
    """
    flow = comp.flows_in[flow_name]

    if isinstance(flow, FlowContinuousIn):
        # Not the raw sum of the connections: a producer exports ONE value
        # to all its consumers, so what this one receives is the share its
        # producers allocated to it (R16).
        return float(flow.get_delivered())

    return float(flow.var_fed.value())


def get_input_available(comp, flow_name):
    """
    Returns what the rules may draw from an input flow at this step.

    This is KTD13's counterparty substitution on the input side: **an
    interposed capacity replaces the flow it buffers**. With one, the input
    flow fills the capacity -- hop 1, done once for every buffered input by
    :meth:`fill_input_capacities` before any rule is looked at -- and the
    rules draw from what the capacity can serve, unbounded while it holds
    something, limited to what transits through it once empty (R7). Without
    one, the rules face the flow directly and draw what it delivers.

    A pure reader: it computes a bound and writes nothing.

    **Demand is the other bound.** A capacity holding stock serves without
    limit and an entirely unbounded rule would run at its nominal scale, so
    what the component actually needs from this input -- computed by the
    demand sweep, before any capacity bound (R7) -- is what caps the draw. A
    component asked for nothing draws nothing, however much stock it sits on.

    Parameters
    ----------
    flow_name : str
        Name of an input flow of the component.

    Returns
    -------
    float
        The drawable quantity, still ``math.inf`` when NOTHING bounds it: a
        capacity holding stock, and no demand computed either.
        :func:`muscadet.rules.rule_scale` then turns the unbounded rule into
        its nominal production.
    """
    capacity = comp.get_capacity_of_flow(flow_name, "in")
    if capacity is None:
        available = comp.get_input_delivered(flow_name)
    else:
        available = capacity.serve_limit(flow_name)

    return min(available, comp.get_input_required_demand(flow_name))


def get_input_transferred(comp, flow_name):
    """
    Returns what an identity transfer moves from an input to its output.

    The transfer moves what is asked for, and never more than its
    counterparty can serve: a capacity holding stock now releases what the
    demand sweep claims, which is more than what enters it (R7).

    When nothing bounds the draw at all -- a stocked capacity, and no demand
    either -- the transfer falls back to what arrives. An unbounded demand
    is a downstream that asked for nothing in particular, not a downstream
    asking for everything, so it must not drain a tank.

    Parameters
    ----------
    flow_name : str
        Name of an input flow carried on both sides of the component.

    Returns
    -------
    float
        The transferred quantity, always finite.
    """
    available = comp.get_input_available(flow_name)

    if math.isinf(available):
        return comp.get_input_delivered(flow_name)

    return max(available, 0.0)


def get_identity_transfer_flows(comp):
    """
    Returns the flow names a rule-less component transfers, input to output.

    A component that declares continuous flows and no transformation rule
    performs an identity transfer, matching each input to the output of the
    same name (R31, KD18): without it every plain tank would need a
    ceremonial same-in-same-out rule.

    A flow declared on one side only is not part of any transfer, and what
    it means depends on whether the component transforms at all:

    - a component carrying continuous flows on a SINGLE side transfers
      nothing. Its outputs are sources holding their declared rate and its
      inputs are sinks -- there is no counterpart for them to match, and
      demanding one would outlaw every source and every consumer;
    - on a two-sided component, an unmatched flow is a hole in the model:
      a quantity arrives and vanishes, or an output is expected to carry
      something nothing produces. It raises, naming the component and the
      flow.

    The check is scoped to the flows the model actually WIRES. An
    unconnected continuous flow exchanges nothing -- an input reads its
    declared default, an output holds its own -- so it can neither lose nor
    invent a quantity, and refusing it would reject models still being
    assembled.

    Returns
    -------
    list of str
        The transferred flow names, in input declaration order. Empty when
        the component transfers nothing.

    Raises
    ------
    ValueError
        When a wired continuous flow of a two-sided rule-less component has
        no counterpart of the same name. The message names the component
        and every unmatched flow.
    """
    flows_in = comp.flows_continuous_in
    flows_out = comp.flows_continuous_out

    if not flows_in or not flows_out:
        return []

    unmatched = [
        f"input flow {name}"
        for name, flow in flows_in.items()
        if name not in flows_out and comp.continuous_flow_is_connected(flow, "in")
    ]
    unmatched += [
        f"output flow {name}"
        for name, flow in flows_out.items()
        if name not in flows_in and comp.continuous_flow_is_connected(flow, "out")
    ]

    if unmatched:
        raise ValueError(
            f"Object {comp.name()}: declares continuous flows and no "
            f"transformation rule, so it transfers each input to the output "
            f"of the same name, but {', '.join(unmatched)} has no flow of "
            f"the same name on the other side. Declare the missing flow, or "
            f"declare what this component transforms with add_rules"
        )

    return [name for name in flows_in if name in flows_out]


def get_transferable_flows(comp):
    """
    Returns the continuous flow names carried on BOTH sides, judging nothing.

    The same list :meth:`get_identity_transfer_flows` returns, without the
    R31 model check: the check belongs to the production sweep, which is
    where a lost quantity actually happens and where it is already reported.
    The demand sweep uses this one, so that a component replacing production
    with an equation of its own -- and therefore never transferring anything
    -- is not refused for a mismatch that has no consequence.

    Returns
    -------
    list of str
        The matched flow names, in input declaration order.
    """
    flows_out = comp.flows_continuous_out

    return [name for name in comp.flows_continuous_in if name in flows_out]


def continuous_flow_is_connected(flow, port):
    """
    Tells whether anything is wired to a continuous flow's port.

    Read through the flow's reference, the only endpoint carrying a
    connection count: the values arriving on an input, the demands
    published by the consumers on an output. Both travel over the single
    bidirectional message box of the port, so either one counts the
    connections of the port itself.

    Parameters
    ----------
    flow : muscadet.flow_continuous.FlowContinuous
        The flow to test.
    port : str
        ``"in"`` or ``"out"``.

    Returns
    -------
    bool
    """
    reference = flow.var_in if port == "in" else flow.var_demand

    return reference is not None and reference.nbCnx() > 0


def apply_consumption(comp, consumption):
    """
    Writes what the rules drew from the input capacities (KTD13, hop 2).

    A flow the rules consume without a capacity buffering it has nothing to
    record: it was drawn straight from the connections feeding it.

    Parameters
    ----------
    consumption : dict
        ``{flow name: rate drawn}``.
    """
    for flow_name, rate in consumption.items():
        capacity = comp.get_capacity_of_flow(flow_name, "in")
        if capacity is not None:
            capacity.set_outflow(flow_name, float(rate))


def release_unused_supply(comp, consumption):
    """Give back what the suppliers delivered and the rules did not use (R-12).

    **The over-draw fix.** A rule runs at the scale its scarcest input allows,
    but the demand that fetched its inputs was sized on the DECLARED
    coefficients, before that scale was known. So a reaction limited by one
    reagent is served more of the others than it can use, and the difference --
    ``delivery - consumption`` -- entered no reaction, no stock and no output.
    It was destroyed, which behind a stocked supplier turned an accounting
    discrepancy into lost matter: a battery falling 100 to 95 where the reaction
    justifies 2.5.

    The scale is already known here: the production sweep computed it a few
    lines above. So the draw is capped at ``scale x coefficient``, which is
    exactly what :meth:`evaluate_production` returned, and every supplier is
    told what was actually taken of what it allocated
    (:meth:`~muscadet.flow_continuous.FlowContinuousOut.restrict_allocation`).

    Three inputs are deliberately left uncapped, and none of the three is an
    oversight:

    - an input a **capacity** buffers. What arrives there is not drawn by the
      rules but stored, and hop 1 of KTD13 has already put all of it in the
      volume: ``draw = consumption + storage`` is conservation, not a breach of
      it. Capping the draw would starve the buffer of exactly what a buffer is
      for;
    - an input **no rule and no transfer accounts for** -- a pure consumer's
      input, absent from ``consumption``. There is no scale to cap it against:
      such a component IS the sink, and what it is given is what it takes;
    - a connection whose producer is **not a continuous output**, or one that
      allocated nothing to this consumer. There is no split behind it to
      correct.

    Nothing is redistributed to a rival consumer of the same supplier: see
    :meth:`~muscadet.flow_continuous.FlowContinuousOut.restrict_allocation`.
    This closes the conservation half; the demand a component PUBLISHES is
    still sized on the declared coefficients, so two rivals on one supply are
    still split in proportion to demands one of them cannot honour. Scope
    Boundaries records what closing that would take.

    Runs BEFORE :meth:`apply_production`, so that a rule whose production is
    written into an output capacity has already had its draw settled: the
    volume's two sides are then written from one consistent evaluation.

    Notes
    -----
    Idempotent under re-evaluation. A release lowers a split to an absolute
    value and never subtracts from it repeatedly, and every producer's own
    production equation recomputes its split from scratch earlier in the same
    sweep, so evaluating the sweep twice at one instant releases the same
    quantity twice rather than twice the quantity.

    Parameters
    ----------
    consumption : dict
        ``{flow name: rate drawn}``, as :meth:`evaluate_production` returned it.
    """
    for flow_name, drawn in consumption.items():
        flow = comp.flows_continuous_in.get(flow_name)

        if flow is None:
            continue

        if comp.get_capacity_of_flow(flow_name, "in") is not None:
            continue

        comp.release_input_surplus(flow, drawn)


def release_input_surplus(comp, flow, drawn):
    """Hand back what one continuous input was given and did not draw (R-12).

    The surplus is given back to the suppliers **in the proportion each of them
    supplied**: a consumer fed by two producers that uses half of what arrives
    took half of each one's share, and there is nothing in the model that would
    single one of them out to bear the whole reduction.

    ``{f}_fed_in`` is re-mirrored afterwards, because
    :meth:`refresh_continuous_inputs` wrote it at the head of the sweep from the
    pre-release delivery: what a model reads as received must be what was
    actually drawn.

    Parameters
    ----------
    flow : muscadet.flow_continuous.FlowContinuousIn
        The input being capped.
    drawn : float
        What the rules took from it.

    Returns
    -------
    float
        What the input now draws.
    """
    shares = flow.incoming_shares()

    if not shares:
        return float(flow.var_in_default)

    delivered = sum(quantity for _, _, quantity in shares)
    drawn = max(float(drawn), 0.0)

    if delivered <= drawn + abs(delivered) * UPTAKE_TOL:
        return delivered

    ratio = drawn / delivered if delivered > 0.0 else 0.0

    for producer, producing_flow, quantity in shares:
        if producing_flow is None:
            continue
        producer.release_output(producing_flow, flow.comp_name, quantity * ratio)

    flow.var_fed.setValue(flow.get_delivered())

    return drawn


def release_output(comp, flow, comp_name, taken):
    """Record on this producer that one consumer took less than it was given.

    The producing half of :meth:`release_unused_supply`, on the component rather
    than on the flow because a flow does not know the capacities of the
    component carrying it: what a reservoir hands out is drained from its
    volume, and a share that is given back must be put back in the volume too --
    otherwise the correction reaches the books and not the matter, and the
    battery goes on emptying.

    Parameters
    ----------
    flow : muscadet.flow_continuous.FlowContinuousOut
        The output the consumer draws on.
    comp_name : str
        Engine name of the consuming component.
    taken : float
        What it actually drew.

    Returns
    -------
    float
        The quantity released, 0 when there was nothing to release.
    """
    released = flow.restrict_allocation(comp_name, taken)

    if released <= 0.0:
        return 0.0

    capacity = comp.get_capacity_of_flow(flow.name, "out")

    if capacity is not None:
        capacity.set_outflow(
            flow.name, max(capacity.get_outflow(flow.name) - released, 0.0)
        )

    return released


def apply_production(comp, production):
    """
    Writes production onto the output flows (KTD13, hops 3 and 4).

    With a capacity interposed on the output side, **the capacity replaces
    the flow as the counterparty of the rules**: production enters the
    capacity, and the output flow carries what the capacity serves onward.
    Without one, the flow carries the production directly.

    What a flow carries is the lesser of what was produced and what the
    consumers asked for (R6, KD4), and that quantity is then split among
    them by the output's allocation policy (R16).

    Each rate is then scaled by two INDEPENDENT terms, whose composition is
    a **product**::

        produced = what the rule (or the declared rate) produces
                   x  profile(t)
                   x  min(out_rate, per-mode deratings)

    - the **time profile** of the output, if it declares one: a continuous
      function of simulation time saying how large the output is at this
      instant -- a solar curve, a daily cycle;
    - the **effective rate** of the output (R18): what the failure modes
      bearing on it leave of it, the minimum over their derating variables
      and the shared ``{flow}_out_rate`` (R20). A rate of 0 is a total loss
      of production -- a continuous output carries no separate boolean
      availability gate (R19, KD10).

    The two must not be collapsed into one another. Deratings compose by
    MINIMUM among themselves, because that is what makes them
    order-independent and safe on repair; a profile MULTIPLIES whatever the
    deratings left, because it is the size of the thing being degraded and
    not a competing degradation. A panel at 0.3 of its curve that is also
    derated to 0.5 produces 0.15, where a minimum would give 0.3.

    Parameters
    ----------
    production : dict
        ``{flow name: rate produced}``.
    """
    # Grouped by capacity: several flows held in ONE volume are drawn from
    # it together, so their requests must all be known before any of them is
    # served (R35).
    buffered = {}

    # Read at most once per evaluation, and only when something reads it: the
    # profiles of one component are functions of the SAME instant, and a model
    # declaring none must not start consulting the clock at every step of
    # every run for a factor that is always 1.
    time = None

    for flow_name, rate in production.items():
        flow = comp.flows_out.get(flow_name)

        # Only a continuous output carries a rate. A discrete output named
        # by a rule keeps its boolean production condition.
        if not isinstance(flow, FlowContinuous):
            continue

        # Profile then derating, by product. Applied HERE, before the demand
        # is reconciled and before a capacity is filled, so a scaled-down
        # output fills its buffer more slowly rather than draining it faster.
        factor = NOMINAL_FACTOR

        if flow.profile is not None:
            if time is None:
                time = comp.current_time()
            factor = flow.get_profile_factor(time)
            flow.publish_profile_factor(factor)

        rate = float(rate) * factor * flow.get_effective_rate()

        request = comp.get_output_request(flow, rate)
        capacity = comp.get_capacity_of_flow(flow_name, "out")

        if capacity is None:
            comp.deliver_output(flow, min(rate, request))
            continue

        # Hop 3: production enters the capacity.
        capacity.set_inflow(flow_name, rate)
        buffered.setdefault(capacity.name, (capacity, {}))[1][flow_name] = request

    # Hop 4: the output flows draw on their capacity. What LEAVES the volume
    # is what the consumers actually received, not what was offered to them:
    # a policy handing out less than the draw would otherwise drain the tank
    # of a quantity nobody took (R16).
    for capacity, requests in buffered.values():
        for flow_name, served in comp.draw_from_capacity(capacity, requests).items():
            delivered = comp.deliver_output(comp.flows_out[flow_name], served)
            capacity.set_outflow(flow_name, delivered)


def get_output_request(comp, flow, rate):
    """
    Returns what an output is asked to deliver this step.

    The demand published by the consumers, and -- when nothing is connected
    -- what :meth:`get_output_consumer_demand` makes of that: the produced
    ``rate`` for a plain output, which is the modelled sink of R-10
    delivering what it produces exactly as it did before demand existed, and
    :data:`NO_DEMAND` for one a capacity sits behind, so that what a rule (or
    a transfer) produced into the volume STAYS in it. An unwired output is
    not a hole a tank drains through.

    **An unbounded demand is answered from the stock.** "Deliver whatever you
    can" is answered by a plain output with what it produces, since that is
    all it has; a capacity has a stock as well, and answering from production
    alone made a reservoir -- whose production is its declared default, zero
    -- serve nothing at all to an unbounded consumer, stalling every model
    downstream of it with no diagnostic. What a capacity can serve is
    :meth:`~muscadet.capacity.Capacity.serve_limit`: unbounded while it holds
    something, what currently transits once empty (R7).
    :meth:`draw_from_capacity` is where that meets the volume actually held.

    Reuses the bound :meth:`get_output_demand` already read this evaluation
    rather than asking the flow again. The two readings cannot differ:
    ``muscadet.ordering.register_equation_order`` allocates the WHOLE demand
    band below the WHOLE production band, and a demand variable is written
    by nothing but :meth:`apply_demand` -- so every demand in the system is
    settled before the first production equation runs.

    The fallback is not a safety net but a real case: a rule-less pure
    source has no transferable flow, and neither has a reservoir, so the
    demand sweep never looks at their outputs at all and there is nothing to
    reuse.
    """
    demand = comp._demand_bound.get(flow.name)
    if demand is None:
        demand = comp.get_output_consumer_demand(flow)

    if not math.isinf(demand):
        return max(float(demand), 0.0)

    capacity = comp.get_capacity_of_flow(flow.name, "out")

    return rate if capacity is None else capacity.serve_limit(flow.name)


def draw_from_capacity(comp, capacity, requests):
    """
    Returns what an output capacity serves for each flow it holds (R7, R35).

    What currently transits through the capacity passes straight on; anything
    asked for BEYOND that comes out of the stock, and a stock of several
    flows is drawn at its raw-quantity composition (R35). One volume holding
    several constituents therefore cannot serve a pure one: asking for more
    of one than its share of the draw allows serves only that share.

    **Conservation is enforced here**: what comes out of the stock is capped
    at what the stock HOLDS, so a capacity never serves more than it holds
    plus what transits it. That cap is what makes an unbounded request
    answerable at all -- :meth:`~muscadet.capacity.Capacity.serve_limit`
    reports a stocked capacity as unbounded, which is a statement about the
    absence of a bound and not a quantity -- and it holds per constituent as
    well as per volume, since :meth:`~muscadet.capacity.Capacity.split_draw`
    apportions the capped draw at each one's raw share.

    Reduces exactly to "an empty capacity serves what transits through it,
    a stocked one serves what is asked for" when it holds a single flow.

    Parameters
    ----------
    capacity : muscadet.capacity.Capacity
        The capacity sitting on the output side.
    requests : dict
        ``{flow name: quantity asked for}``, over the flows it holds.

    Returns
    -------
    dict
        ``{flow name: quantity served}``.
    """
    transit = {name: capacity.get_inflow(name) for name in requests}

    # What the stock is asked for, over and above what transits -- and never
    # more than the stock holds.
    beyond = sum(max(requests[name] - transit[name], 0.0) for name in requests)
    draw = capacity.split_draw(min(beyond, capacity.total_quantity()))

    return {
        name: max(min(requests[name], transit[name] + draw.get(name, 0.0)), 0.0)
        for name in requests
    }


def deliver_output(comp, flow, quantity):
    """
    Delivers ``quantity`` on a continuous output and splits it (R16, R17).

    The flow's variable carries the TOTAL delivered -- one variable is
    exported to every connection -- and the split among the consumers is
    held on the flow itself, for each of them to read its own share back.

    **Split first, publish what was actually distributed.** A policy may
    hand out less than what was offered: ``shares`` deliberately gives
    nothing to a connected consumer carrying no declared share (R16), and
    the surplus redistribution stops at the total demanded. Publishing the
    offered quantity and splitting it afterwards would make
    ``{f}_fed_out`` -- documented as the total delivered downstream --
    exceed the sum of the consumers' shares, and the same pre-split figure
    would drain an output capacity of a quantity nobody received.

    An output NO consumer is connected to is the one case where there is no
    split to read: it delivers what it produces, exactly as it did before
    allocation existed.

    What the flow carries here is what was OFFERED to the consumers. A consumer
    that then takes less lowers its own share and this same variable by the
    difference (:meth:`~muscadet.flow_continuous.FlowContinuousOut.
    restrict_allocation`, R-12), so the equality between ``{f}_fed_out`` and the
    sum of the shares holds after the release as it does here.

    Returns
    -------
    float
        What was actually distributed, which is what the flow now carries.
    """
    quantity = max(float(quantity), 0.0)

    allocated = comp.allocate_output(flow, quantity)
    distributed = sum(allocated.values()) if allocated else quantity

    flow.var_fed.setValue(distributed)

    return distributed


def allocate_output(comp, flow, available):
    """
    Splits what an output delivers among its consumers (R16, R17).

    The component-level override point of the allocation: a component whose
    split depends on something no policy can express -- its own state, a
    measurement it reads -- overrides this rather than declaring a policy.
    The declared policies and the flow-level Python rule both live on the
    flow, in ``FlowContinuousOut.get_allocation_split``.

    Parameters
    ----------
    flow : muscadet.flow_continuous.FlowContinuousOut
        The output being delivered.
    available : float
        What it delivers this step.

    Returns
    -------
    dict
        ``{consumer component name: quantity}``.
    """
    return flow.allocate(available)
