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
  at all (R31) -- and delivers the lesser of what was produced and what was
  asked for, split among the consumers by the output's allocation policy (R16).

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
    FlowContinuous,
    FlowContinuousIn,
    FlowContinuousOut,
)
from .rules import (
    UNCONSTRAINED_SCALE,
    rule_consumption,
    rule_production,
    rule_scale,
)


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
    a shared upstream supply. Correcting it needs a second demand pass or an
    iterative solve, both outside the two-sweep ordering; Scope Boundaries
    records it.

    A component declaring no rule transfers each input to the output of the
    same name (R31), so its demand crosses it unchanged -- including an
    UNBOUNDED one. That is the one path an unconnected output still claims
    without bound on: :meth:`get_demand_scale` drops such an output from the
    rule scale, but a transfer has no declared coefficients and therefore no
    nominal scale to fall back to, so what a rule-less pass-through should
    ask for when nothing consumes it is an open question rather than a
    filter. Recorded as a residual.

    An input no rule and no transfer covers is a pure consumer's input: it
    claims the demand it was DECLARED with, ``var_demand_default``.

    Returns
    -------
    dict
        ``{input flow name: demand}``, possibly ``math.inf``: a connected
        consumer may publish an unbounded demand, and a rule-less transfer
        carries an unconnected output's absent one across unchanged.

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

        if not comp.output_constrains_demand(flow_name):
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


def get_output_demand(comp, flow_name):
    """
    Returns the demand an output carries back into the component.

    Two bounds compose here:

    - what the consumers ask for, :data:`~muscadet.flow_continuous.UNBOUNDED`
      when none is connected;
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

    demand = flow.get_demand_bound()

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

    Each rate is first multiplied by the **effective rate** of the output it
    is produced on (R18): what the failure modes bearing on that output
    leave of it, the minimum over their derating variables (R20). A rate of
    0 is a total loss of production -- a continuous output carries no
    separate boolean availability gate (R19, KD10).

    Parameters
    ----------
    production : dict
        ``{flow name: rate produced}``.
    """
    # Grouped by capacity: several flows held in ONE volume are drawn from
    # it together, so their requests must all be known before any of them is
    # served (R35).
    buffered = {}

    for flow_name, rate in production.items():
        flow = comp.flows_out.get(flow_name)

        # Only a continuous output carries a rate. A discrete output named
        # by a rule keeps its boolean production condition.
        if not isinstance(flow, FlowContinuous):
            continue

        # Derating (R18): what the rules computed, times what the failure
        # modes bearing on this output leave of it. Applied HERE, before the
        # demand is reconciled and before a capacity is filled, so a derated
        # output fills its buffer more slowly rather than draining it faster.
        rate = float(rate) * flow.get_effective_rate()

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

    The demand published by the consumers, and the produced ``rate`` when
    nothing is connected: an output nobody asks anything of delivers what it
    produces, exactly as it did before demand existed. A capacity behind it
    does not change that -- an unconnected output is a modelled sink, so
    what a buffered one produces travels straight through the volume rather
    than accumulating in it. A tank stocks up when what DRAWS on it asks for
    less than what arrives, which is what a fill claim arranges (R36).

    Reuses the bound :meth:`get_output_demand` already read this evaluation
    rather than asking the flow again. The two readings cannot differ:
    ``muscadet.ordering.register_equation_order`` allocates the WHOLE demand
    band below the WHOLE production band, and a demand variable is written
    by nothing but :meth:`apply_demand` -- so every demand in the system is
    settled before the first production equation runs.

    The fallback is not a safety net but a real case: a rule-less pure
    source has no transferable flow, so the demand sweep never looks at its
    output at all and there is nothing to reuse.
    """
    demand = comp._demand_bound.get(flow.name)
    if demand is None:
        demand = flow.get_demand_bound()

    return rate if math.isinf(demand) else max(float(demand), 0.0)


def draw_from_capacity(comp, capacity, requests):
    """
    Returns what an output capacity serves for each flow it holds (R7, R35).

    What currently transits through the capacity passes straight on; anything
    asked for BEYOND that comes out of the stock, and a stock of several
    flows is drawn at its raw-quantity composition (R35). One volume holding
    several constituents therefore cannot serve a pure one: asking for more
    of one than its share of the draw allows serves only that share.

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

    # What the stock is asked for, over and above what transits.
    beyond = sum(max(requests[name] - transit[name], 0.0) for name in requests)
    draw = capacity.split_draw(beyond)

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
