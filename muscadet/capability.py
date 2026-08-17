"""What each continuous output could deliver if asked without bound (R-20).

The third sweep, and the one that makes a component's DEMAND agree with what it
can actually consume. Like :mod:`muscadet.evaluation`, it is written as
functions over a component rather than as methods on it, and ``ObjFlow`` binds
each of them under the same name, so ``comp.compute_capability()`` and every
override point it carries behave exactly like the two older sweeps.

Why a third sweep, and why nothing shorter works
------------------------------------------------
Three quantities are meant to agree::

    demand        what a component publishes upstream
    delivery      what its suppliers actually give it
    consumption   what its rule actually uses

``delivery == consumption`` is enforced by
:func:`muscadet.evaluation.release_unused_supply`, at the point where both are
finally known. ``demand == consumption`` was not, and could not be: the demand
is mapped back through the rule's DECLARED coefficients
(:func:`muscadet.evaluation.get_demand_scale`) before the scale is known, so a
reaction limited by a scarce reagent still asked for its nominal share of an
abundant one -- and out-competed a rival that could have used it. Measured on
two consumers sharing a supply of 1.0, one of them capped at 0.1 by a different
reagent: the capped one took 0.999 and left its rival 0.001 of the 0.909 that
was rightfully available to it.

**No lagged scheme closes that**, and this was established by measurement rather
than assumed. A delivery is ``min(capability, demand)``, so a demand recomputed
from what ARRIVED is self-referential -- the ``min`` has already destroyed the
quantity being looked for:

* bounding the demand by the previous evaluation's production SCALE converges on
  a wrong fixed point, each consumer frozen at whatever it first received;
* bounding it by the previous evaluation's DELIVERIES decays monotonically to
  zero: 0.1, then 0.0169 at 100 evaluations, then 5e-4 at 4000;
* adding a saturation test, so that only an input delivered less than it asked
  for counts as binding, gives a clean period-2 oscillation between the nominal
  claim and the achievable one.

Nor is there an inner fixpoint to lean on: the solver evaluates the equation set
once per integration step at an ADVANCING time, so a lag is a one-step delay
integrated into the levels and never a relaxation.

What is missing is the suppliers' **capability** -- what each of them could
deliver if asked without bound -- which is exactly the quantity ``min(capability,
demand)`` throws away. So it is published, on a channel of its own
(``{flow}_capability_out`` / ``{flow}_capability_in``), by a sweep that runs
DOWNSTREAM like production and therefore ahead of the demand sweep. A demand is
then bounded by::

    demand_i = coefficient_i x min( downstream scale,
                                    min over j != i of ( capability_j / coefficient_j ) )

The ``j != i`` is not a refinement, it is what makes the scheme work at all:
including the input being sized would freeze it at its own current capability
and it could never grow again.

Measured on the same two-rival model, this converges on the FIRST evaluation and
gives the fair split 0.0909 / 0.909, under a modest downstream demand and under
an unbounded one alike.

What it does not close, stated plainly
--------------------------------------
A capability shared by two rivals is **over-counted by both**:
:meth:`muscadet.flow_continuous.FlowContinuousIn.incoming_capability` sums what
the producers publish and apportions nothing, because the question is asked
before any demand exists to apportion against. Two consumers of one source
therefore each size their demand as if the other were absent. This is a strict
improvement rather than a complete answer, and
:func:`muscadet.evaluation.release_unused_supply` stays exactly where it is to
catch what is left -- see its own docstring for the three cases where this
estimate is knowingly optimistic.

Why it agrees with production
-----------------------------
Every bound here is computed by the SAME function the production sweep uses:
:func:`muscadet.rules.rule_scale` for a rule, ``serve_limit`` for a capacity,
``var_fed_default`` for a source, ``var_in_default`` for an unwired input. That
is deliberate and load-bearing. A capability derived by a second, parallel
reckoning would be an independent estimate of the same physics, free to drift
from it -- and a demand bounded by a capability the production sweep does not
honour would starve a component that could have run.

Deratings and time profiles are **not** applied, and the reason is the same one
that keeps them out of the demand sweep (R-13): both sweeps that size a claim
work on the rule's declared coefficients, an existing scope boundary. Applying
them would sharpen the estimate for a derated producer -- and it is recorded as
a separate decision, not taken here, precisely because it moves that boundary.

Torn cycles
-----------
A capacity-broken loop (R-14) has an edge dropped from the ordering, so the
receiving component runs BEFORE its supplier in this sweep too. That edge
carries no information here, which is what makes the tear exact rather than
merely tolerable: both ways :func:`muscadet.ordering.capacity_breaks_inbound`
can hold put a volume between what arrives and what leaves, and this module asks
the volume rather than the connection -- an input capacity answers
``serve_limit`` and a fully buffered output answers ``serve_limit`` too. The
residual is the documented one: a volume standing at zero degrades to a
pass-through and the torn capability is then read one evaluation late.

Notes
-----
Like both older sweeps, the equation here is evaluated by the solver repeatedly
inside one integration step: it must stay a pure function of the current
variable values and must create or register nothing.
"""

import math

from .flow_continuous import UNBOUNDED, FlowContinuousIn, FlowContinuousOut
from .rules import rule_consumption, rule_scale


def register_capability_variables(system, comp):
    """Declare this component's capability variables EXPLICIT on the PDMP.

    ``{flow}_capability_out`` is written from inside an equation method, and
    PyCATSHOO refuses ``setValue`` on a variable its solver does not know about
    while the differential system is being resolved -- the same rule
    :meth:`muscadet.ObjFlow.register_capacity` declares a capacity's transit
    hooks under. Established the way that one was: a source publishing an
    unchanged capability is silently tolerated, so the refusal only appears on
    the first component whose capability actually MOVES, which in a real model
    is the first transformer downstream of anything.

    Called from the pre-run step rather than when the flow is declared, and
    deliberately: registering at declaration time would create a PDMP manager
    for any system carrying a continuous flow, whether or not it ever runs,
    where :func:`muscadet.ordering.register_equation_order` creates one only for
    a system that actually has a graph to order.

    Parameters
    ----------
    system : muscadet.System
        The system owning the PDMP manager.
    comp : muscadet.ObjFlow
        A component of the continuous-flow graph.
    """
    for flow in comp.flows_continuous_out.values():
        if flow.var_capability is not None:
            system.pdmp_add_explicit_variable(flow.var_capability)

    # A transfer pair's two publications are written from the production sweep
    # and fall under the same rule, so they are declared in the same place
    # rather than in a second pre-run hook that could drift out of step with
    # this one.
    for pair in comp.transfers.values():
        for var in pair.every_variable():
            system.pdmp_add_explicit_variable(var)


def compute_capability(comp):
    """
    PDMP equation: what this component could deliver if asked without bound.

    Named after ``muscadet.ordering.CAPABILITY_EQUATION_METHOD``: the ordering
    module looks this method up BY NAME on every node of the continuous-flow
    graph and registers it in the **first** band, on the same topological
    order the production sweep uses -- so a producer publishes its capability
    before the component it feeds reads it, and every capability in the system
    is settled before the first demand equation runs.

    Notes
    -----
    Evaluated by the solver, repeatedly, inside one integration step: it must
    stay a pure function of the current variable values and must not create or
    register anything.
    """
    comp.apply_capability(comp.evaluate_capability())


def evaluate_capability(comp):
    """
    Computes what each continuous output could deliver if asked without bound.

    The mirror image of :func:`muscadet.evaluation.evaluate_production`, with
    the demand removed from every bound -- that is the whole difference between
    the two, and it is why this one can run first. Each of the three ways a
    component puts a quantity on an output is answered by the same primitive
    the production sweep answers it with:

    - a **rule**, at the scale its scarcest input's CAPABILITY allows
      (:func:`muscadet.rules.rule_scale` over
      :func:`get_input_capability`), producing each output at its declared
      coefficient. A rule nothing constrains falls back to
      ``UNCONSTRAINED_SCALE`` exactly as production does, so its capability is
      its nominal coefficient rather than an unbounded claim;
    - an **identity transfer** (R31, R-16), over the flows no rule set names:
      what could arrive is what could leave;
    - a **source**: the declared ``var_fed_default``, which is all a rule-less
      output has.

    The rule sets share one budget per input, in declaration order, exactly as
    :func:`muscadet.evaluation.evaluate_production` shares it (R-17): two sets
    consuming one input cannot each be told they may have the whole of it, or
    the capability published would be the sum of two mutually exclusive futures.

    :func:`muscadet.evaluation.get_transferable_flows` is used rather than
    ``get_identity_transfer_flows``, for the reason the demand sweep uses it
    too: the R31 mismatch belongs to the production sweep, where a lost quantity
    actually happens and where it is already reported. A component replacing
    production with an equation of its own must not be refused here.

    Returns
    -------
    dict
        ``{output flow name: capability}``, possibly ``math.inf``. Every flow a
        rule set produces appears, at zero when the active mode does not name it
        -- a capability another mode left behind must be cleared, not inherited.
    """
    capabilities = {}

    def accumulate(quantities):
        for flow_name, quantity in quantities.items():
            capabilities[flow_name] = capabilities.get(flow_name, 0.0) + quantity

    # What each input has LEFT to offer, shared across the rule sets (R-17).
    budget = {}

    def take(flow_name):
        if flow_name not in budget:
            budget[flow_name] = comp.get_input_capability(flow_name)
        return budget[flow_name]

    def spend(drawn):
        for flow_name, quantity in drawn.items():
            remaining = budget.get(flow_name)
            # An unbounded supply is not consumed away, and inf - inf is not a
            # quantity -- the same guard evaluate_production carries.
            if remaining is None or math.isinf(remaining):
                continue
            budget[flow_name] = max(remaining - quantity, 0.0)

    for rule_set in comp.rule_sets.values():
        accumulate(dict.fromkeys(rule_set.produced_flows, 0.0))

        rule = comp.get_active_rule(rule_set)
        if rule is None:
            continue

        available = {flow_name: take(flow_name) for flow_name in rule.cons}
        scale = rule_scale(rule, available)

        spend(rule_consumption(rule, scale))

        accumulate(
            {
                flow_name: coefficient * scale
                for flow_name, coefficient in rule.prod.items()
            }
        )

    for flow_name in comp.get_transferable_flows():
        accumulate({flow_name: comp.get_input_capability(flow_name)})

    for flow_name, flow in comp.flows_continuous_out.items():
        capabilities.setdefault(flow_name, float(flow.var_fed_default))

    return capabilities


def apply_capability(comp, capabilities):
    """
    Publishes each output's capability downstream, as its capacity makes it.

    Parameters
    ----------
    capabilities : dict
        ``{output flow name: capability}``, as :func:`evaluate_capability`
        returned it. A name that is not a continuous output carries no channel
        and is skipped -- a discrete output named in a ``prod`` map is a boolean
        production, not a quantity.
    """
    for flow_name, capability in capabilities.items():
        flow = comp.flows_out.get(flow_name)

        if not isinstance(flow, FlowContinuousOut):
            continue

        flow.publish_capability(comp.output_capability(flow_name, capability))


def output_capability(comp, flow_name, produced):
    """
    What a volume behind an output makes of what the rules could produce.

    KTD13's counterparty substitution, on the capability channel: **an
    interposed capacity replaces the flow it buffers**, exactly as it does for a
    demand (:func:`muscadet.evaluation.get_output_request`) and for a draw
    (:func:`muscadet.evaluation.get_input_available`). What could leave a
    stocked volume is not what the rules could make -- a reservoir whose
    declared production is zero can still serve its whole content.

    Two cases, and they are the two ``serve_limit`` already distinguishes:

    - **stocked** -- ``serve_limit`` is unbounded, and so is the capability. It
      is the same statement the demand sweep answers an unbounded request with,
      and :func:`muscadet.evaluation.draw_from_capacity` is where it meets the
      quantity actually held;
    - **empty** -- the volume degrades to a pass-through and only what transits
      can leave. What will transit is what the rules are about to produce, which
      is ``produced``. Deliberately not ``serve_limit``'s own answer there: that
      reads ``{c}_inflow_{f}``, which still holds what the PREVIOUS production
      sweep wrote, and this sweep has a current figure to hand.

    Parameters
    ----------
    flow_name : str
        Name of a continuous output of the component.
    produced : float
        What the rules, a transfer or the declared rate could put on it.

    Returns
    -------
    float
        Possibly ``math.inf``.
    """
    capacity = comp.get_capacity_of_flow(flow_name, "out")

    if capacity is None:
        return produced

    limit = capacity.serve_limit(flow_name)

    return limit if math.isinf(limit) else produced


def get_input_capability(comp, flow_name):
    """
    Returns what an input could supply this component if it asked without bound.

    The capability counterpart of
    :func:`muscadet.evaluation.get_input_available`, and the same substitution
    rule: with a capacity buffering the input, the rules face the volume rather
    than the connection.

    Three answers, and each is the one production would honour:

    - a **discrete** input is :data:`~muscadet.flow_continuous.UNBOUNDED`. A
      boolean gate carries no quantity, so it bounds no quantity. Its effect on
      production is not lost -- :func:`muscadet.rules.rule_scale` reads it as 0
      or 1 and caps the scale accordingly, and what a closed gate did not
      consume is handed back by
      :func:`muscadet.evaluation.release_unused_supply`. Reading a gate as a
      RATE and letting it clamp a continuous demand at 1 would be a category
      error, and one that no model asked for;
    - an input a **capacity** buffers reports what that capacity can serve:
      unbounded while it holds something, and what could transit once empty --
      which is the capability arriving on the connections, since an empty
      volume is a pass-through;
    - otherwise, the sum of what the producers publish
      (:meth:`~muscadet.flow_continuous.FlowContinuousIn.incoming_capability`),
      or ``var_in_default`` with nothing connected.

    Parameters
    ----------
    flow_name : str
        Name of an input flow of the component.

    Returns
    -------
    float
        Possibly ``math.inf``.
    """
    flow = comp.flows_in.get(flow_name)

    if not isinstance(flow, FlowContinuousIn):
        return UNBOUNDED

    capacity = comp.get_capacity_of_flow(flow_name, "in")

    if capacity is not None:
        limit = capacity.serve_limit(flow_name)
        if math.isinf(limit):
            return limit

    return flow.incoming_capability()


def get_supply_scale(comp, rule, exclude=None):
    """
    The scale ``rule``'s OTHER inputs could sustain, ignoring ``exclude``.

    The bound of R-20, and the reason the demand sweep can now size one input
    against the rest: ``min over j != exclude of (capability_j / coefficient_j)``.

    **Excluding the input being sized is not a refinement.** Including it would
    bound each input by its own current capability, which is what the supplier
    is delivering under the present demand -- so an input could never claim more
    than it is already getting, and a consumer that started small would stay
    small for ever. That is the ratchet the measured "bound by the previous
    deliveries" variant decays through, reintroduced by the back door.

    A coefficient of zero is skipped, mirroring
    :func:`muscadet.rules.rule_scale`: a catalyst named so it can be guarded on
    is not a limiting reagent, and ``capability / 0`` is not a scale.

    Returns
    -------
    float
        :data:`~muscadet.flow_continuous.UNBOUNDED` when nothing else
        constrains the rule -- a single-input rule, or one whose other inputs
        all sit behind stocked volumes. The demand is then exactly what it was
        before this bound existed.
    """
    scale = UNBOUNDED

    for flow_name, coefficient in rule.cons.items():
        if coefficient <= 0 or flow_name == exclude:
            continue

        scale = min(scale, comp.get_input_capability(flow_name) / coefficient)

    # A negative capability is not a production in reverse; inf survives it.
    return max(scale, 0.0)
