"""Two-sweep evaluation of a component's continuous flows.

The algorithm a continuous component runs at every integration step, as
functions over a component rather than as methods on it -- the shape the
sibling units of this release already use (``muscadet.ordering``,
``muscadet.capacity``). ``ObjFlow`` binds each of them as a method of the same
name, so ``comp.compute_demand()`` and every override point it carries are
exactly what they were.

Two sweeps live here; a third, :mod:`muscadet.capability`, runs ahead of both
and is what lets the first of them size a claim it can honour. In evaluation
order (R8, KD4, R-20):

- **capability**, downstream, in :mod:`muscadet.capability`: each continuous
  output publishes what it could deliver if asked without bound;
- **demand**, upstream: :func:`compute_demand` publishes on each continuous
  input what the component needs from it, mapped back from the demand its
  outputs carry through the active rule's declared coefficients (R34), bounded
  by the scale the rule's OTHER inputs can sustain (R-20), then claimed by an
  interposed input capacity (R7, R36);
- **production**, downstream: :func:`compute_production` runs the active rule
  of each rule set at the scale its scarcest input allows (R15) -- or transfers
  each input to the output of the same name when the component declares no rule
  at all (R31) -- caps the draw on the suppliers at the scale the outputs were
  actually PRODUCED at, deratings and time profiles included
  (:func:`get_uptake_factor`), hands back what it did not take
  (:func:`release_unused_supply`, R-12), then delivers the lesser of what was
  produced and what was asked for, split among the consumers by the output's
  allocation policy (R16).

Three quantities are meant to agree, and two of them now do::

    demand        what a component publishes upstream
    delivery      what its suppliers actually give it
    consumption   what its rule actually uses

``delivery == consumption`` is enforced by :func:`release_unused_supply`: what
a supplier gives up is what a reaction, a stock or an output receives, so
nothing is destroyed. That holds for what a failure mode or a time profile
leaves of the outputs as well as for the limiting reagent
(:func:`get_uptake_factor`): a component whose output is derated to zero
produces nothing and therefore draws nothing, instead of draining its suppliers
at the nominal rate for the rest of the mission.

``demand == consumption`` now holds on the common path too, and R-20 is how: the
demand is still sized on the declared coefficients -- production has not run, so
there is no scale to size it on -- but it is bounded by the scale the rule's
other inputs can sustain, read from the capability those suppliers publish
(:mod:`muscadet.capability`). A rule limited by one reagent therefore no longer
asks for its nominal share of the others.

Two residues remain, both knowingly optimistic rather than overlooked. A
capability read by two rivals is counted twice, since it is published before any
demand exists to apportion it against; and a derating or a time profile scales
what an output DELIVERS without being applied to what it claims.
:func:`release_unused_supply` is what catches both, at the point where the scale
is finally known -- see Scope Boundaries.

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
    comp._demand_scale.clear()

    comp.apply_demand(comp.evaluate_demand())


def evaluate_demand(comp):
    """
    Computes what the component needs from each of its continuous inputs.

    The mapping of R34: the demand aggregated on an output is carried back
    onto the inputs through the active rule's ``prod`` and ``cons``
    coefficients, and then **bounded by what the rule's other inputs can
    actually supply** (R-20).

    That bound is the whole of R-20. The R34 mapping uses the DECLARED
    coefficients, and it has to: production has not run, so the scale is not
    known yet. Left at that, a component capped by a scarce reagent went on
    claiming its nominal share of the abundant ones and over-claimed a shared
    upstream supply -- taking 0.999 of a supply of 1.0 it could use 0.1 of, and
    leaving a rival 0.001 of the 0.909 available to it. What was missing is the
    suppliers' CAPABILITY, which ``min(capability, demand)`` destroys and which
    no lag can recover; :mod:`muscadet.capability` publishes it on a sweep of
    its own, ahead of this one, and :func:`~muscadet.capability.get_supply_scale`
    is what is read here::

        demand_i = coefficient_i x min( downstream scale,
                                        min over j != i of ( capability_j / coefficient_j ) )

    What this does NOT close is a capability two rivals read at once: each sums
    what its producers publish and apportions nothing, so each sizes itself as
    if the other were absent. The production sweep still caps the draw at the
    scale it computes and releases the rest (R-12), which is what catches that
    residue -- and the two derated cases the bound deliberately ignores.

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

    for set_key, rule_set in comp.rule_sets.items():
        # Every flow the SET consumes starts at zero, whichever of its
        # rules is active: a rule set selecting nothing demands nothing,
        # exactly as it produces nothing (R14).
        for flow_name in rule_set.consumed_flows:
            accumulate(flow_name, 0.0)

        rule = comp.get_active_rule(rule_set)
        if rule is None:
            continue

        scale = comp.get_demand_scale(rule)
        # Handed to the production sweep of this same evaluation, with the
        # rule it was computed for: a set whose guard selected another rule
        # in between must not be capped by a scale that belongs to the one
        # before it.
        comp._demand_scale[set_key] = (rule, scale)
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

            # The supply bound of R-20: what this input is asked for is
            # capped by the scale the rule's OTHER inputs can sustain, read
            # from the capability they publish. Without it the demand was
            # the downstream scale alone, so a reaction limited by a scarce
            # reagent claimed its nominal share of an abundant one and
            # out-competed a rival that could have used it.
            supply = comp.get_supply_scale(rule, exclude=flow_name)

            accumulate(flow_name, coefficient * min(scale, supply))

    # The identity transfer, over what the rules leave untouched (R31, R-16).
    # Unconditional rather than an ``else`` of the branch above: the two are
    # not alternatives but two halves of one component. A rule set names the
    # flows it transforms, and a continuous flow carried on both sides that NO
    # rule set names is still a pass-through -- declaring a rule on a splitter
    # must not silently stop the flows beside it. The list is empty when the
    # rules name everything, so a pure transformer is unaffected.
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

    # A CONDUIT asks for what it is about to move (R6, KD4). It replaced its
    # flow's identity transfer, so nothing else claims that input, and without
    # this the three-component conduit returns zero -- the measured failure the
    # whole notion exists for.
    #
    # A TWO-FLOW pair asks for nothing of its own, and that asymmetry is not an
    # omission. Both its streams keep their identity transfer, so each already
    # carries its consumer's demand upstream; adding the moved quantity on top
    # would ask a supplier for a quantity no balance needs.
    for pair in comp.transfers.values():
        if not pair.is_conduit:
            continue

        # Clamped like the crossing it is about to ask for: a conduit that
        # computed a reversal asks for nothing rather than for a magnitude it
        # will not move.
        accumulate(pair.source, max(pair.quantity(comp), 0.0))

    # A continuous input no rule and no transfer covers claims what it was
    # declared with: a pure consumer has no output to map a demand back from.
    for flow_name, flow in comp.flows_continuous_in.items():
        demands.setdefault(flow_name, float(flow.var_demand_default))

    return demands


def get_demand_scale(comp, rule):
    """
    Returns the scale ``rule`` may run at without making what it cannot place.

    The scale is taken over the ``prod`` coefficients, as a **minimum**
    (``tests/test_demand_scale_minimum_001.py``): the rule's outputs are
    correlated by construction, so a scale above what
    any one of them can take produces a surplus of the others that has nowhere
    to go. There is no such place: an output delivers what its consumers draw,
    and the rest is simply gone, recorded by no balance.

    It was a **maximum** until 3.0.0, on the reading that the scale serving
    every output is the one the most demanding of them needs. That reading is
    only sound when the surplus can leave, and nothing in a model said whether
    it could. Its worst consequence was not the missing matter but the bound it
    broke: an electrolyser whose hydrogen outlet was blocked, holding a
    ten-unit buffer behind that outlet, filled it to 39.999 in twenty hours
    while :meth:`Capacity.clamp_to_bounds` worked to hold a bound the
    production sweep kept refilling past. Blocking the second outlet made
    everything exact. One outlet still asking was the whole of the defect.

    **The argument that settles it is expressiveness.** Under a minimum an
    outlet that constrains and an outlet that discharges freely are both
    sayable: the first is a wire to its real consumer, the second is either no
    wire at all (dropped by :meth:`output_constrains_demand`) or a discharge
    asking for more than the rule can make, which a minimum never retains.
    Under a maximum the second worked and the first could not be said. The
    discharge pattern R-10 recommends -- declare the vent as a consumer with
    its own demand, so the intent is visible -- was itself wrong under a
    maximum: the rule took off at the vent's rate and destroyed the useful
    surplus.

    **Where "discharges freely" is NOT sayable, and it is worth knowing.** A
    rule-less pass-through (R31) carries the demand of whatever is beyond it
    and has no demand default to set, so a branch ending in a pipe whose far
    end is wired to nothing publishes zero and stops the rule upstream, main
    product included. Measured: a reactor delivering 10 of its useful product
    drops to 0 when a by-product branch is terminated by such a pipe, with no
    diagnostic. The fix in a model is to wire a discharge at the end of that
    pipe, or to remove the pipe: an unwired output is a modelled vent, an
    unwired PIPE is a dead end. The README repeats this where it blesses "a
    branch not built yet".

    **Not to be harmonised with the maximum of** :meth:`get_uptake_factor`.
    The two answer different questions. A derating is a declared LOSS: the
    product is made and the fault destroys it on the way out, so it must not
    spare the reagents the surviving legs still consume. A demand of zero is
    not a loss but the ABSENCE of an outlet, and nothing may be created that
    has nowhere to go.

    Only the outputs that actually CONSTRAIN the rule take part in it --
    :meth:`output_constrains_demand`. An output nothing asks anything of
    constrains nothing, so it must not enter the scale at all. What that
    filter carries is NOT the same under the two rules, and the difference is
    worth stating rather than assuming. An unwired output publishes ``inf``,
    so under the old maximum it dominated every connected one and made the
    component draw without bound; under a minimum an ``inf`` never wins, and
    removing the filter changes nothing for a rule that has one connected
    output left. What the filter still carries is the case where **every**
    output is unwired: without it the minimum is a minimum of infinities and
    the rule takes its whole supply, where the ``not scales`` branch below
    gives it the nominal scale. Measured with the filter disabled: 100.0
    drawn instead of 1.0.

    "Nobody is connected" and "somebody is asking for nothing" stay strictly
    apart. A consumer publishing a demand of zero is a real bound and gives a
    scale of zero, which is what stops the rule; only the absence of a
    consumer is dropped. And a genuinely unbounded demand published BY a
    connected consumer -- a capacity claiming ``inf`` for its filling (R36),
    a downstream itself unconstrained -- still travels: it means "deliver
    whatever you can", and an unbounded claim never wins a minimum, so a
    metered sibling output goes on sizing the rule.

    A rule left with no constraining output at all -- producing nothing, or
    producing only into unwired outputs -- falls back to the nominal scale,
    exactly as a rule producing nothing always did.

    An output **capacity** takes part whether or not the output is wired: it
    is not the output that constrains there but the volume behind it, which
    claims a fill rate for itself and is throttled by its own accept bound
    (:meth:`output_capacity_claims_demand`). The two are added by
    :meth:`get_output_demand` and enter the scale as one figure -- which is
    what makes a full buffer stop the rule filling it. It is also the one
    shape in which an UNWIRED output publishes a hard zero rather than
    ``inf``: a capacity whose ``fill_rate`` is the default 0 is a pure buffer,
    it claims nothing, and a rule producing into it alone therefore stands
    still from t=0. Under the old maximum that model ran and dropped
    everything it made.

    **This scale binds the production sweep too**, since 3.1.0. It is recorded
    per rule set in ``comp._demand_scale`` and read back by
    :meth:`demand_scale`, because :meth:`evaluate_production` sizes each set
    from the SHARED input budget and that is only equivalent for a component
    carrying one set. With two, a set this scale had sized at zero ran anyway,
    ate the budget its sibling needed, and dropped what it made: measured on a
    supply of 5, the first-declared set at 4 dropping 4 an hour while the only
    set with a real consumer produced nothing, and swapping the declaration
    order swapped which consumer was served.

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

    return min(scales)


def demand_scale(comp, set_key, rule):
    """
    Returns what the DEMAND sweep sized ``rule`` at, this evaluation.

    The read side of the per-evaluation hand-off :meth:`get_demand_scale`
    fills. Deliberately not a recomputation: :meth:`get_output_demand` writes
    ``comp._demand_bound``, which :meth:`get_output_request` consumes further
    down the production sweep, so calling it again here would replace this
    evaluation's reading with one taken after the capacity levels moved.

    **An absent reading is not a scale of zero, and not the nominal scale
    either: it is no cap at all.** Two situations produce one, and neither
    justifies throttling anything. A rule set whose guard selected a different
    rule between the two sweeps has a reading that belongs to another rule, so
    it is refused by identity rather than applied to this one. And a
    production sweep run OUTSIDE a solver step -- a test calling
    :meth:`evaluate_production` directly, before the demand band of the same
    evaluation has run -- has no reading at all. In a real run the bands are
    ordered demand before production (see :mod:`muscadet.ordering`), so the
    reading is always there and always current; inventing one where it is
    missing would make a diagnostic call change the number it reports.

    Parameters
    ----------
    set_key : str
        Key of the rule set in ``comp.rule_sets``.
    rule : muscadet.rules.Rule
        The rule the production sweep is about to size.

    Returns
    -------
    float
        The recorded scale, or :data:`~muscadet.flow_continuous.UNBOUNDED`
        when there is none to apply.
    """
    recorded = comp._demand_scale.get(set_key)

    if recorded is None or recorded[0] is not rule:
        return UNBOUNDED

    return recorded[1]


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
    # This evaluation's production factors, discarded from the previous one:
    # what each continuous output's production is scaled by, and the instant
    # the profiles among them are functions of. Both are read at most once per
    # evaluation and only when something reads them -- see
    # get_production_factor. A per-evaluation hand-off between the two halves
    # of the sweep (the draw sizes itself on them, the delivery applies them),
    # never a memo across evaluations.
    comp._production_factor.clear()
    comp._evaluation_time = None

    comp.refresh_continuous_inputs()
    comp.fill_input_capacities()

    consumption, production = comp.evaluate_production()

    comp.apply_consumption(consumption)
    comp.release_unused_supply(consumption)
    comp.apply_production(production)
    comp.publish_transfers()


def evaluation_time(comp):
    """The instant this evaluation reads its time profiles at.

    :func:`current_time`, memoised for the duration of one production sweep:
    the profiles of one component are functions of the SAME instant, and a
    model declaring none never reaches here at all.
    """
    if comp._evaluation_time is None:
        comp._evaluation_time = comp.current_time()

    return comp._evaluation_time


def output_production_factor(comp, flow_name):
    """What one continuous output's production is multiplied by, right now.

    The two independent terms of R18/R20, composed by **product**::

        profile(t)  x  min(out_rate, per-mode deratings)

    - the **time profile** of the output, if it declares one: a continuous
      function of simulation time saying how large the output is at this
      instant -- a solar curve, a daily cycle. Reading it here is also what
      publishes it on ``{flow}_out_profile``;
    - the **effective rate** (R18): what the failure modes bearing on the
      output leave of it, the minimum over their derating variables and the
      shared ``{flow}_out_rate`` (R20). A rate of 0 is a total loss of
      production -- a continuous output carries no separate boolean
      availability gate (R19, KD10).

    The two must not be collapsed into one another. Deratings compose by
    MINIMUM among themselves, because that is what makes them
    order-independent and safe on repair; a profile MULTIPLIES whatever the
    deratings left, because it is the size of the thing being degraded and not
    a competing degradation. A panel at 0.3 of its curve that is also derated
    to 0.5 produces 0.15, where a minimum would give 0.3.

    :data:`~muscadet.profile.NOMINAL_FACTOR` for anything that is not a
    continuous output: a discrete output named in a ``prod`` map keeps its
    boolean production condition and carries no rate.
    """
    flow = comp.flows_out.get(flow_name)

    if not isinstance(flow, FlowContinuousOut):
        return NOMINAL_FACTOR

    factor = NOMINAL_FACTOR

    if flow.profile is not None:
        factor = flow.get_profile_factor(comp.evaluation_time())
        flow.publish_profile_factor(factor)

    return factor * flow.get_effective_rate()


def get_production_factor(comp, flow_name):
    """The factor one output's production carries for THIS evaluation.

    :func:`output_production_factor`, read at most once per output per
    evaluation. Memoising it is what makes the two halves of the sweep agree:
    the draw is sized on it in :func:`evaluate_production` and the delivery
    applies it in :func:`apply_production`, and a profile re-read in between --
    at an instant the solver may already have advanced -- would let a component
    consume against one factor and produce against another.
    """
    factor = comp._production_factor.get(flow_name)

    if factor is None:
        factor = comp.output_production_factor(flow_name)
        comp._production_factor[flow_name] = factor

    return factor


def get_uptake_factor(comp, flow_names):
    """How much of its nominal draw a rule actually needs (R-13).

    **The derated-draw fix.** A derating and a time profile scale what an
    output PRODUCES, and the draw on the suppliers was sized -- by
    :func:`evaluate_production`, through :func:`~muscadet.rules.rule_scale` --
    before either was read. So a component whose output a failure mode cut to
    zero went on consuming its inputs at the nominal rate while delivering
    nothing: a dead electrolyser drained its battery and its water tank for the
    whole mission, and starved every rival sharing them as if it were still
    running. :func:`release_unused_supply` did not catch it either, since it
    caps the draw at the PRE-derating scale and therefore had nothing to give
    back.

    The factor is the **maximum** over the outputs the rule actually produces
    into, and the choice is forced rather than aesthetic. A rule's outputs are
    correlated but their deratings are not: one output of a two-output reaction
    may be cut while the other still delivers in full. Taking the minimum would
    then cut the draw to zero while the output nothing derated went on
    producing -- matter created out of nothing. The maximum is the smallest draw that covers
    everything the rule demonstrably put out, which is exactly what a derating
    is: a LOSS on the leg it bears on, never a saving on the reagents the other
    legs still consume. On a single-output rule -- the shape a derating is
    almost always declared on -- the two coincide.

    Clamped at :data:`~muscadet.profile.NOMINAL_FACTOR` from above. A factor
    above 1 is an amplifying profile, and the scale it would amplify was
    already the most the inputs allowed (:func:`~muscadet.rules.rule_scale`):
    there is nothing more to draw, so an amplification stays what it was, a
    statement about production alone.

    Deliberately not applied in the demand sweep -- exactly like the derating
    and the profile themselves, and for the same reason: :func:`evaluate_demand`
    maps demand back through the rule's DECLARED coefficients, an existing scope
    boundary. A derated component therefore still ASKS for its nominal share and
    hands the surplus straight back (R-12).

    Parameters
    ----------
    flow_names : iterable of str
        The output flows the step produces into. Names that are not continuous
        outputs are ignored: they carry no rate.

    Returns
    -------
    float
        In ``[0, 1]``. :data:`~muscadet.profile.NOMINAL_FACTOR` when the step
        produces into no continuous output at all -- a rule feeding only
        discrete outputs throttles nothing, and neither does one producing
        nothing.
    """
    outputs = comp.flows_continuous_out

    factors = [
        comp.get_production_factor(flow_name)
        for flow_name in flow_names
        if flow_name in outputs
    ]

    if not factors:
        return NOMINAL_FACTOR

    return min(max(factors), NOMINAL_FACTOR)


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

    The two are **not** returned at the same scale, and that is the whole of
    R-13. ``production`` carries the rule's own coefficients, for
    :meth:`apply_production` to scale by each output's derating and time
    profile; ``consumption`` is already scaled by what those left
    (:meth:`get_uptake_factor`), because it is the draw the suppliers are about
    to be told about and nothing downstream of here would apply it.

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
    # Same reasoning as the clear at the end of :meth:`apply_production`: this
    # half FILLS the per-evaluation factors, so it must not inherit the
    # previous evaluation's when it is driven directly rather than by
    # :meth:`compute_production`.
    comp._production_factor.clear()
    comp._evaluation_time = None

    consumption = {}
    production = {}

    def accumulate(target, quantities):
        for flow_name, quantity in quantities.items():
            target[flow_name] = target.get(flow_name, 0.0) + quantity

    # What each input has LEFT to give, shared across the rule sets (R-17).
    # ``get_input_available`` reports what one input can serve, and asking it
    # once per rule set handed each of them the whole of it: two sets both
    # consuming ``a`` at an inflow of 1.0 each ran at scale 1 and produced 2.0
    # out of 1.0 received -- matter created, and invisible to
    # ``release_unused_supply``, which sees a consumption ABOVE the delivery
    # and has nothing to release. Behind an input capacity the same path wrote
    # an outflow of 2.0 against an inflow of 1.0 and drained the volume of a
    # quantity nobody delivered.
    budget = {}

    def take(flow_name):
        """What the next rule set may draw from ``flow_name``."""
        if flow_name not in budget:
            budget[flow_name] = comp.get_input_available(flow_name)
        return budget[flow_name]

    def spend(drawn):
        """Record a draw against the shared budget."""
        for flow_name, quantity in drawn.items():
            remaining = budget.get(flow_name)
            # An unbounded supply -- a stocked capacity nothing bounds -- is
            # not consumed away, and inf - inf is not a quantity.
            if remaining is None or math.isinf(remaining):
                continue
            budget[flow_name] = max(remaining - quantity, 0.0)

    # Declaration order is the priority order: the sets are evaluated in the
    # order they were declared, and the first one served is the first one
    # declared. Deterministic and inspectable, which no proportional split of a
    # contested reagent would be without a declared policy of its own.
    for set_key, rule_set in comp.rule_sets.items():
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

        available = {flow_name: take(flow_name) for flow_name in rule.cons}
        # What the inputs allow, capped by what the outputs can take.
        #
        # The cap is the missing half of the minimum scale of 3.0.0. Sizing a
        # set on the shared input budget ALONE is right for one rule set,
        # whose inputs the demand sweep already narrowed to what its outputs
        # asked for. It is wrong the moment a component carries two: the
        # budget is the aggregate, so a set the demand sweep sized at zero ran
        # anyway, ate the budget its sibling needed, and dropped what it made
        # -- measured on a supply of 5, the first-declared set running at 4
        # and dropping 4 an hour while the only set with a real consumer
        # produced nothing.
        #
        # Read rather than recomputed, and that is not an optimisation:
        # get_output_demand WRITES _demand_bound, which get_output_request is
        # about to read below, so recomputing here would overwrite this
        # evaluation's hand-off with a bound taken after the capacity levels
        # moved.
        scale = min(rule_scale(rule, available), comp.demand_scale(set_key, rule))

        # The scale the outputs are actually PRODUCED at: a rule whose
        # outputs a failure mode or a time profile scaled down draws less
        # of its reagents, and one whose only output is dead draws nothing
        # (R-13). A coefficient of 0 produces nothing of that output and
        # must not vote -- the catalyst idiom, mirrored from rule_scale.
        uptake = comp.get_uptake_factor(
            flow_name for flow_name, coefficient in rule.prod.items() if coefficient > 0
        )

        drawn = rule_consumption(rule, scale * uptake)

        # Spent BEFORE the next set is sized, and on what was actually drawn:
        # a derated set hands the difference to the next one rather than
        # reserving it.
        spend(drawn)

        accumulate(consumption, drawn)
        accumulate(production, rule_production(rule, scale))

    # The identity transfer, over the flows no rule set names (R31, R-16).
    # Unconditional for the reason given in :meth:`evaluate_demand`: a rule set
    # says what its component transforms, not what its component carries, so a
    # continuous flow present on both sides and named by no rule is a
    # pass-through whether or not the component also transforms something else.
    for flow_name in comp.get_identity_transfer_flows():
        transferred = comp.get_input_transferred(flow_name)
        uptake = comp.get_uptake_factor((flow_name,))
        accumulate(consumption, {flow_name: transferred * uptake})
        accumulate(production, {flow_name: transferred})

    # Every flow a CONDUIT meters starts at zero, for the reason a rule set's
    # flows do: the conduit replaced that flow's identity transfer, so the
    # source default below must not fill it, and a quantity the previous
    # evaluation left must be cleared rather than inherited.
    for flow_name in comp.transfer_named_flows():
        accumulate(consumption, {flow_name: 0.0})
        accumulate(production, {flow_name: 0.0})

    # A continuous output no rule and no transfer names is a SOURCE: the
    # value it was declared with is what it can produce. It appears here so
    # that what it delivers is reconciled with the demand like any other
    # production -- an output holding a rate nobody asks for delivers less.
    for flow_name, flow in comp.flows_continuous_out.items():
        production.setdefault(flow_name, float(flow.var_fed_default))

    # Transfer pairs, LAST and in declaration order (KTD2). Last for two
    # independent reasons: a two-flow pair adjusts a production every earlier
    # contributor may have written, so it needs the map complete; and on a
    # contested input the pair is then the thing that saturates, which is the
    # state it has a shortfall channel for and a rule set has not.
    for pair in comp.transfers.values():
        requested = pair.quantity(comp)
        origin, target, magnitude = pair.directed(comp, requested)

        if pair.is_conduit:
            # What crosses IS the computed quantity (R5). The pair replaced
            # the transfer, so it draws from the input side like a rule does
            # and spends against the shared budget.
            #
            # A NEGATIVE quantity crosses nothing. A conduit's direction is the
            # connection's, and a connection whose direction reverses mid-run
            # is out of scope by KD1 -- ordering, acyclicity and allocation all
            # assume it fixed. Moving the magnitude forward instead would be
            # actively wrong, warming the tank a symmetric conduction law is
            # telling to cool, so it is clamped here and the raw request stays
            # readable on {pair}_requested: a reversal shows up as a negative
            # ask against a zero crossing, not as a plausible number.
            moved = min(max(requested, 0.0), max(take(pair.source), 0.0))

            # And never more than the output can actually hand on. A conduit
            # replaced its flow's identity transfer, so the demand it published
            # upstream is its OWN computed quantity and not the downstream
            # demand -- which is exactly what keeps `get_input_available` from
            # bounding it the way it bounds a transfer. Drawn but undeliverable,
            # the difference is destroyed: nothing downstream of here releases
            # it, and `release_unused_supply` caps at this very consumption.
            if comp.output_carries_demand(pair.source):
                moved = min(moved, max(comp.get_output_demand(pair.source), 0.0))

            # The draw is scaled by what the output's deratings and time profile
            # LEFT, exactly as the identity transfer scales its own (R-13). A
            # dead output produces nothing and must therefore consume nothing,
            # or the difference is drawn from the supplier and lost.
            uptake = comp.get_uptake_factor((pair.source,))

            spend({pair.source: moved * uptake})
            accumulate(consumption, {pair.source: moved * uptake})
            accumulate(production, {pair.source: moved})
        else:
            # A signed delta on top of two streams that keep transiting. The
            # cap is what the source is about to produce: a stream cannot be
            # relieved of more than it carries.
            magnitude = min(magnitude, max(production.get(origin, 0.0), 0.0))
            accumulate(production, {origin: -magnitude})
            accumulate(production, {target: magnitude})
            moved = magnitude if requested >= 0.0 else -magnitude

        pair.last_requested = requested
        pair.last_moved = moved

    return consumption, production


def publish_transfers(comp):
    """Write each pair's two magnitudes onto the model (R8, KD5).

    Called from :func:`compute_production` and not from
    :func:`evaluate_production`: the latter is a pure reader that tests and the
    capability sweep drive on their own, and writing a solver variable from it
    would put a ``setValue`` on a path that is not always inside an equation.
    """
    for pair in comp.transfers.values():
        pair.publish()


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


def rule_named_flows(comp):
    """
    Returns every flow name the component's rule sets consume or produce.

    What the rules ACCOUNT FOR, and therefore what the identity transfer must
    stand aside from: a flow a rule consumes is drawn by that rule, and a flow
    a rule produces is written by it. Everything else a component carries is
    the rules' business no more than if they had not been declared.

    Returns
    -------
    set of str
        Empty when the component declares no rule set.
    """
    named = set()

    for rule_set in (comp.rule_sets or {}).values():
        named.update(rule_set.consumed_flows)
        named.update(rule_set.produced_flows)

    return named


def transfer_named_flows(comp):
    """
    Returns the flow names the component's CONDUIT pairs meter.

    The transfer-pair counterpart of :func:`rule_named_flows`, and deliberately
    narrower than it: only a conduit is subtracted from the identity-transfer
    residue, because only a conduit REPLACES a transit (R5, KTD4).

    A two-flow pair contributes nothing here, and that asymmetry is the whole
    of KTD4. Its two streams keep crossing the component and the pair moves a
    quantity BETWEEN their balances as a signed delta; subtract them and the
    exchanger's streams stop crossing at all, leaving the pair to carry a whole
    balance it was never asked to carry.

    Returns
    -------
    set of str
        Empty when the component declares no pair, or only two-flow ones.
    """
    return {pair.source for pair in (comp.transfers or {}).values() if pair.is_conduit}


def get_identity_transfer_flows(comp):
    """
    Returns the flow names a component transfers unchanged, input to output.

    A component performs an identity transfer on every continuous flow it
    carries on both sides and **no rule set names**, matching each input to the
    output of the same name (R31, KD18): without it every plain tank would need
    a ceremonial same-in-same-out rule.

    The rule sets are subtracted rather than switched on (R-16). Both sweeps
    used to test ``if comp.rule_sets`` and skip the transfer entirely, so
    declaring one rule disabled the pass-through for every flow that rule did
    not mention: a component with ``flows_in=[a, b]``, ``flows_out=[x, b]`` and
    ``cons={a} -> prod={x}`` asked for no ``b`` and emitted none, and everything
    downstream of ``b`` read zero. The mismatch check below lived in the same
    dead branch, so nothing was reported either -- and the very same component
    WITHOUT the rule would either transfer ``b`` or raise. Adding a rule to an
    existing buffer or splitter therefore killed its untouched flows.

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

    It is also scoped to the RESIDUE the rules leave, both for the two-sided
    precondition and for the mismatch itself: a component whose residue lies on
    one side only transfers nothing and raises nothing -- an input a rule set
    would consume under a mode it has not reached is a sink, an output nothing
    produces is a source -- exactly as a component carrying flows on one side
    only always did.

    Returns
    -------
    list of str
        The transferred flow names, in input declaration order. Empty when
        the component transfers nothing.

    Raises
    ------
    ValueError
        When a wired continuous flow no rule set names, on a component whose
        unnamed flows straddle both sides, has no counterpart of the same
        name. The message names the component and every unmatched flow.
    """
    named = rule_named_flows(comp) | transfer_named_flows(comp)

    flows_in = {
        name: flow
        for name, flow in comp.flows_continuous_in.items()
        if name not in named
    }
    flows_out = {
        name: flow
        for name, flow in comp.flows_continuous_out.items()
        if name not in named
    }

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
            f"Object {comp.name()}: no transformation rule names "
            f"{', '.join(unmatched)}, so it is transferred to the flow of the "
            f"same name on the other side -- and the other side declares none. "
            f"Declare the missing flow, or say what this component does with "
            f"that one in a rule's 'cons' / 'prod' map (add_rules)"
        )

    return [name for name in flows_in if name in flows_out]


def get_transferable_flows(comp):
    """
    Returns the transferable continuous flow names, judging nothing.

    The same list :meth:`get_identity_transfer_flows` returns -- carried on
    both sides, named by no rule set -- without the R31 model check: the check
    belongs to the production sweep, which is where a lost quantity actually
    happens and where it is already reported. The demand sweep uses this one,
    so that a component replacing production with an equation of its own -- and
    therefore never transferring anything -- is not refused for a mismatch that
    has no consequence.

    Returns
    -------
    list of str
        The matched flow names, in input declaration order.
    """
    named = rule_named_flows(comp) | transfer_named_flows(comp)
    flows_out = comp.flows_continuous_out

    return [
        name
        for name in comp.flows_continuous_in
        if name in flows_out and name not in named
    ]


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
    lines above. So the draw is capped at ``scale x uptake x coefficient``,
    which is exactly what :meth:`evaluate_production` returned, and every
    supplier is told what was actually taken of what it allocated
    (:meth:`~muscadet.flow_continuous.FlowContinuousOut.restrict_allocation`).

    ``uptake`` is the second half of the cap (R-13,
    :meth:`get_uptake_factor`): the scale ``rule_scale`` computes is what the
    inputs allow, not what the outputs delivered, so a rule whose outputs a
    failure mode or a time profile scaled down is capped at what it actually
    produced. Without it a component derated to zero went on drawing at the
    nominal rate for ever, and this pass had nothing to give back because it
    was comparing the draw against the very scale the derating had not reached.

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

    Both terms are read through :meth:`get_production_factor`, which is the
    same reading :meth:`evaluate_production` already sized the draw on: what
    the component takes and what it delivers are then two views of one factor
    rather than two readings of a clock the solver may have advanced between.

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

        # Profile then derating, by product. Applied HERE, before the demand
        # is reconciled and before a capacity is filled, so a scaled-down
        # output fills its buffer more slowly rather than draining it faster.
        rate = float(rate) * comp.get_production_factor(flow_name)

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

    # The last reader of the per-evaluation factors, so it is also what ends
    # their life. :meth:`compute_production` empties them at its head too, but
    # a component calling this one directly -- outside the sweep, or from an
    # equation of its own -- must not be served a memo from a previous
    # evaluation: a frozen factor would pin a derating at the value it had when
    # the mode first fired and never follow a repair.
    comp._production_factor.clear()
    comp._evaluation_time = None


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

    **An UNBOUNDED request is answered out of the stock**, capped at what the
    stock holds: :meth:`~muscadet.capacity.Capacity.serve_limit` reports a
    stocked capacity as unbounded, which is a statement about the absence of a
    bound and not a quantity, and this is where that statement becomes one.
    :meth:`~muscadet.capacity.Capacity.split_draw` apportions the capped draw
    at each constituent's raw share, so the cap holds per constituent too.

    **A FINITE request is not capped, and the difference is the whole point.**
    Capping it compared a rate (the shortfall per unit time) with a quantity
    (what the volume holds), which is only meaningful if the quantity is
    implicitly divided by one unit of time. That implicit unit turned the
    emptying of a tank into an exponential relaxation whose time constant was
    ONE TIME UNIT, whatever the physics: measured on one model at three speeds,
    a tank emptying in 10, 5 and 1 time units took the same 4.5 units to reach
    its degraded regime every time. The bound is a property of the integrated
    state and the empty/full automaton watches it, so the crossing belongs to
    the solver, which stops on it exactly (R7) and leaves at most one
    ``dtCond``-sized overshoot for
    :meth:`~muscadet.capacity.Capacity.clamp_to_bounds` to take back.

    Reduces exactly to "an empty capacity serves what transits through it,
    a stocked one serves what is asked for" when it holds a single flow -- which
    it did NOT while a finite request was capped: a tank holding 1 and short of
    2 served transit + 1, being stocked and yet serving less than it was asked.

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

    # What the stock is asked for, over and above what transits. Capped by what
    # the stock holds ONLY when the ask is unbounded, that being the one case
    # where a quantity has to stand in for a rate -- see the docstring.
    beyond = sum(max(requests[name] - transit[name], 0.0) for name in requests)
    draw = capacity.split_draw(
        beyond if math.isfinite(beyond) else capacity.total_quantity()
    )

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
