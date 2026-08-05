"""Continuous (real-valued) flow primitives.

This module hosts the *continuous* flow family: :class:`FlowContinuousIn` and
:class:`FlowContinuousOut`. They sit on the shared :class:`~muscadet.flow.FlowModel`
base **beside** the discrete family (``FlowDiscreteIn`` / ``FlowDiscreteOut`` and
their legacy aliases), never on top of it: the two families are parallel, and the
discrete boolean semantics stay strictly untouched.

Naming convention
-----------------
Continuous flows mirror the discrete naming convention so that
``System.connect_flow`` — which wires message boxes by string concatenation,
``{flow_name}_out`` on the source to ``{flow_name}_in`` on the target — keeps
working unchanged, and therefore ``auto_connect`` too.

Variables (per flow named ``f``):

===============================  ===========  =======================================
Variable                         Owner        Meaning
===============================  ===========  =======================================
``f_fed_out``  (``t_double``)    out flow     value produced / delivered downstream
``f_demand_in`` (reference)      out flow     demands published by the consumers
``f_fed_in``   (``t_double``)    in flow      value received, sum of the connections
``f_in``       (reference)       in flow      upstream produced values
``f_demand_out`` (``t_double``)  in flow      demand published upstream
===============================  ===========  =======================================

The ``_in`` / ``_out`` suffix denotes the **direction of travel** of the quantity,
exactly like the discrete ``{name}_trigger_in`` channel carried by an *output*
flow: an input flow receives ``fed`` and emits ``demand``; an output flow emits
``fed`` and receives ``demand``.

Message boxes: a continuous flow uses a **single bidirectional** message box per
port — ``f_out`` on the producer, ``f_in`` on the consumer — carrying two aliases:

- alias ``f``          — data channel, exported by the out flow, imported by the in flow
- alias ``f_demand``   — demand channel, exported by the in flow, imported by the out flow

One ``connect(src, "f_out", tgt, "f_in")`` therefore wires *both* directions, and
the demand alias can collide neither with the data channel nor with the discrete
``{name}_available_in`` / ``{name}_available_out`` / ``{name}_trigger_in`` boxes.

Demand and allocation (R5, R6, R16, R17)
----------------------------------------
The demand channel above is what travels **upstream**: a consumer publishes what
it asks for on ``f_demand_out``, and the producer reads the demands of all its
consumers back through ``f_demand_in``. The quantity actually delivered on a
connection is the lesser of what the producer can produce and what the consumer
asks for (KD4), so when the demands exceed the supply the producer must split
what it has. That split is the **allocation policy** of the output flow:

===============  ============================================================
Policy           What it does
===============  ============================================================
``proportional`` splits in proportion to the demands (the default)
``shares``       splits at declared fixed shares, keyed by consumer name
``priority``     serves consumers in priority order until the supply is gone
===============  ============================================================

and :attr:`FlowContinuousOut.allocation_fun` is the Python extension point of
R17, used in preference to the declared policy.

Whatever the policy, :func:`allocate` wraps it in the **surplus redistribution**
loop of F2: a consumer capped at its demand releases what it did not take, and
the policy is applied again to the rest, until no consumer exceeds its demand.
One pass is not enough -- with shares ``0.5 / 0.3 / 0.2``, a supply of 10 and
demands of 1, 4 and 100, capping the first at 1 and redistributing gives the
second 5.4 against a demand of 4, so a further pass is required. The loop is
bounded by the consumer count: each pass caps at least one consumer.

Per KTD12 an allocation is a **closed-form function of demands already known**.
A consumer that revised its demand after seeing what it was actually served
would reopen a fixed point inside the forward sweep and invalidate the whole
two-sweep ordering, so nothing here ever asks a capped consumer again.

Who computes what
-----------------
A message box exports **one** variable to every connection, so a producer
cannot hand a different number to each consumer through ``f_fed_out``: that
variable carries the TOTAL it delivers. The split itself is held on the
producing flow, keyed by consumer component name (:attr:`FlowContinuousOut.
allocated`), and a consumer reads its own share back by resolving each
connection to the flow behind it -- see :meth:`FlowContinuousIn.get_delivered`.

Scope
-----
This module owns the channels, the allocation policies and the two accessors
the sweeps read (:meth:`FlowContinuousIn.get_delivered`,
:meth:`FlowContinuousOut.get_demand_bound`). What a component *demands* --
mapping an output's demand back onto its inputs through the active rule's
coefficients (R34), and throttling it against a capacity bound (R7) -- belongs
to the component, in :meth:`muscadet.ObjFlow.compute_demand`.
"""

import math
import typing

import Pycatshoo as pyc
import pydantic
from colored import fg, attr

from .flow import FlowModel

#: "Nothing bounds this quantity". Read on a demand: an output no consumer is
#: connected to is not throttled by anybody, and a component that derives no
#: demand for an input claims no bound on it. Never a *delivered* quantity: the
#: sweeps resolve an unbounded demand back to something finite before writing it.
UNBOUNDED = math.inf

#: Split in proportion to the demands. The default: it needs no declaration and
#: it is the only policy that stays meaningful when consumers come and go.
ALLOCATION_PROPORTIONAL = "proportional"

#: Split at declared fixed shares, keyed by consumer component name.
ALLOCATION_SHARES = "shares"

#: Serve consumers in declared priority order until the supply is exhausted.
ALLOCATION_PRIORITY = "priority"

#: Every policy name a continuous output may declare.
ALLOCATION_POLICIES = (
    ALLOCATION_PROPORTIONAL,
    ALLOCATION_SHARES,
    ALLOCATION_PRIORITY,
)

#: Tolerance on "this consumer got more than it demanded". Purely numerical: it
#: keeps a rounding residue from being mistaken for a surplus and spending a
#: redistribution pass on it.
ALLOCATION_TOL = 1e-12


# ----------------------------------------------------------------------
# Allocation policies (R16) -- closed-form splits of a known quantity
# ----------------------------------------------------------------------


def split_proportional(available, demands):
    """Split ``available`` in proportion to ``demands``.

    Parameters
    ----------
    available : float
        The quantity to split.
    demands : mapping
        ``{consumer: demand}``, every demand finite and non-negative.

    Returns
    -------
    dict
        ``{consumer: quantity}``, in the order of ``demands``. Every consumer
        demanding zero yields zeros rather than a division by a null total.
    """
    total = sum(demands.values())

    if total <= 0.0:
        return {key: 0.0 for key in demands}

    return {key: available * demand / total for key, demand in demands.items()}


def split_shares(available, demands, shares):
    """Split ``available`` at declared fixed shares, ignoring the demands.

    The shares of the consumers being served are **renormalised**, which is what
    makes the redistribution loop meaningful: once a consumer is capped and
    dropped, the others share what is left in their own declared ratio.

    A consumer no share was declared for takes none: the split of a fixed-share
    output is fully declared, and an undeclared consumer would otherwise silently
    take from the ones that were.
    """
    weights = {key: max(float(shares.get(key, 0.0)), 0.0) for key in demands}
    total = sum(weights.values())

    if total <= 0.0:
        return {key: 0.0 for key in demands}

    return {key: available * weight / total for key, weight in weights.items()}


def split_priority(available, demands, priorities):
    """Serve consumers in priority order until ``available`` is exhausted.

    The lowest priority number is served first. Consumers sharing one priority
    are a **group**, and the group's allotment is split proportionally to their
    demands -- a priority policy that cannot order two consumers falls back to
    the default policy between them rather than to declaration order. A consumer
    no priority was declared for is served last.
    """
    ranks = {}
    for key in demands:
        rank = priorities.get(key)
        ranks[key] = UNBOUNDED if rank is None else float(rank)

    allocation = {key: 0.0 for key in demands}
    remaining = available

    for rank in sorted(set(ranks.values())):
        group = {key: demands[key] for key in demands if ranks[key] == rank}
        group_demand = sum(group.values())

        if remaining <= 0.0 or group_demand <= 0.0:
            continue

        allotment = min(remaining, group_demand)
        allocation.update(split_proportional(allotment, group))
        remaining -= allotment

    return allocation


def regularize_demands(available, demands):
    """Make ``demands`` usable by a split: finite, non-negative, ordered.

    An **unbounded** demand is regularised to the whole quantity available,
    which is the most any consumer could ever receive. Without it a split would
    divide by an infinite total and hand out NaN, and an unbounded demand is
    exactly what an output nobody is connected to publishes upstream.
    """
    regular = {}
    for key, demand in demands.items():
        value = float(demand)
        if math.isinf(value) or value != value:  # inf or NaN
            value = float(available)
        regular[key] = max(value, 0.0)
    return regular


def allocate(available, demands, split=None, trace=None):
    """Distribute ``available`` over ``demands``, redistributing every surplus.

    The policy proposes a split; a consumer proposed more than it demanded is
    **capped** at its demand and dropped, and the policy is applied again to
    what is left over the consumers that remain. It repeats until no consumer
    exceeds its demand -- bounded by the consumer count, since every pass caps
    at least one of them (F2).

    Parameters
    ----------
    available : float
        The quantity the producer has to give.
    demands : mapping
        ``{consumer: demand}``. An unbounded demand is regularised first.
    split : callable, optional
        ``split(available, demands) -> {consumer: quantity}``. Defaults to
        :func:`split_proportional`. This is the shape of the R17 extension
        point: a custom rule proposes a split and inherits the convergence.
    trace : list, optional
        When given, each pass appends its proposal to it -- the inspection
        surface for "one redistribution pass was not enough".

    Returns
    -------
    dict
        ``{consumer: quantity}``, in the order of ``demands``. The distributed
        total is the available quantity, or the total demanded when that is
        less.
    """
    split = split_proportional if split is None else split
    available = max(float(available), 0.0)
    demands = regularize_demands(available, demands)

    capped = {}
    allocation = {}

    # Bounded by the consumer count: a pass either caps somebody, or ends it.
    for _ in range(len(demands) + 1):
        active = {key: demand for key, demand in demands.items() if key not in capped}
        remaining = max(available - sum(capped.values()), 0.0)

        proposal = split(remaining, active) if active else {}
        if trace is not None:
            trace.append(dict(proposal))

        over = [
            key
            for key, quantity in proposal.items()
            if quantity > demands[key] + ALLOCATION_TOL
        ]

        if not over:
            allocation = dict(capped)
            allocation.update(proposal)
            break

        for key in over:
            capped[key] = demands[key]
    else:  # pragma: no cover - unreachable: each pass caps at least one consumer
        allocation = dict(capped)

    return {key: float(allocation.get(key, 0.0)) for key in demands}


class FlowContinuous(FlowModel):
    """Shared base of the continuous flow family.

    Exists so a continuous flow can be told apart from a discrete one with a
    single ``isinstance(flow, FlowContinuous)`` check — used by the connection
    type check and by every consumer that must enumerate only the continuous
    flows of a component.
    """

    var_type: str = pydantic.Field(
        "float", description="Flow type (real-valued for a continuous flow)"
    )

    var_demand: typing.Any = pydantic.Field(
        None,
        description=(
            "Demand channel endpoint: a variable on an input flow (the demand "
            "this consumer publishes upstream), a reference on an output flow "
            "(the demands published by the downstream consumers)."
        ),
    )

    comp_name: typing.Optional[str] = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description=(
            "Engine name of the component owning this flow, recorded when its "
            "variables are declared. It is the key an allocation is held under, "
            "and the key a fixed share or a priority is declared against."
        ),
    )

    def add_variables(self, comp, **kwargs):
        # Recorded here rather than passed around: an allocation is keyed by
        # consumer component name, and a flow is reached from both ends -- from
        # its own component, and from the component at the other end of a
        # connection, which resolves it through a reference.
        self.comp_name = comp.name()

        super().add_variables(comp, **kwargs)

    def get_flow_type_color(self) -> str:
        """Return the color formatting for continuous flow types."""
        return f"{attr('bold')}{fg('cyan')}"

    @classmethod
    def get_format_class_name(cls) -> str:
        """Return the color formatting for continuous flow class names."""
        return f"{fg('cyan')}"

    def format_flow_value(self, value) -> str:
        """Format a real flow value."""
        try:
            return f"{fg('green')}{float(value):g}{attr('reset')}"
        except (TypeError, ValueError):
            return str(value)

    def get_var_demand_value(self):
        """Return the current value of this flow's demand endpoint."""
        raise NotImplementedError

    def __repr__(self) -> str:
        # NOTE: deliberately does NOT call FlowModel.__repr__ -- the discrete
        # representation reads ``var_fed_available``, the boolean availability
        # gate, which a continuous flow does not carry (a continuous output
        # expresses a total loss of production by a zero rate, not by a gate).
        flow_type = self.__class__.__name__
        var_fed = self.var_fed.value() if self.var_fed is not None else "N/A"

        return (
            f"{self.get_flow_type_color()}{flow_type}{attr('reset')} "
            f"{fg('blue')}{self.name}{attr('reset')} "
            f"[{self.var_type}] = {self.format_flow_value(var_fed)} "
            f"[{self.format_flow_value(self.var_fed_default)}]"
        )

    def __str__(self) -> str:
        flow_type = self.__class__.__name__
        var_fed = self.var_fed.value() if self.var_fed is not None else "N/A"

        lines = [
            f"{self.get_flow_type_color()}{flow_type}{attr('reset')} "
            f"{fg('blue')}{self.name}{attr('reset')}",
            f"  {fg('white')}Type{attr('reset')}: {self.var_type}",
            f"  {fg('white')}Value{attr('reset')}: {self.format_flow_value(var_fed)}",
            f"  {fg('white')}Default{attr('reset')}: "
            f"{self.format_flow_value(self.var_fed_default)}",
        ]
        return "\n".join(lines)


class FlowContinuousIn(FlowContinuous):
    """Real-valued input flow.

    Aggregates every incoming connection by **sum**: several producers feeding
    one continuous input deliver their sum -- each of them the share it
    allocated to this consumer, not the total it publishes (R16). An input with
    no connection reads ``var_in_default``.

    Publishes a demand upstream over the same connections (R5): what the
    component derives for it, or the demand it was declared with.
    """

    var_in: typing.Any = pydantic.Field(
        None, description="Reference collecting the upstream produced values"
    )

    var_in_default: float = pydantic.Field(
        0.0, description="Flow input value when not connected"
    )

    var_demand_default: float = pydantic.Field(
        0.0,
        description=(
            "The demand this consumer claims when its component derives none: "
            "the DECLARED demand of a pure consumer, which has no output to map "
            "a demand back from. Also the value published before the first "
            "demand sweep has run."
        ),
    )

    demand_required: float = pydantic.Field(
        UNBOUNDED,
        exclude=True,
        repr=False,
        description=(
            "What the component needs from this input, written by the demand "
            "sweep BEFORE any capacity bound is applied. This is what the rules "
            "may draw (a full capacity reduces what is published upstream, not "
            "what the rules take from it). Unbounded until a sweep computes it, "
            "so a component whose demand equation never runs produces exactly "
            "what its inputs allow."
        ),
    )

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, port="in", **kwargs)

        # Data channel: upstream produced values, aggregated by sum.
        self.var_in = comp.addReference(f"{self.name}_in")

        # Demand channel: what this consumer asks its producers for.
        self.var_demand = comp.addVariable(
            f"{self.name}_demand_out",
            pyc.TVarType.t_double,
            float(self.var_demand_default),
        )

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_in")
        comp.addMessageBoxImport(f"{self.name}_in", self.var_in, self.name)
        comp.addMessageBoxExport(
            f"{self.name}_in", self.var_demand, f"{self.name}_demand"
        )

    def get_var_demand_value(self):
        """Return the demand this consumer currently publishes upstream."""
        return self.var_demand.value()

    def set_demand(self, value):
        """Publish ``value`` upstream as this consumer's demand (R5)."""
        self.var_demand.setValue(max(float(value), 0.0))

    # -- what this input actually receives -----------------------------

    def supplier_flow(self, var):
        """The producing flow behind one connection, or None.

        ``var`` is the variable a connection carries -- the producer's
        ``{f}_fed_out``. Its ``parent()`` is the producing component itself, so
        the flow is resolved from the variable's own name rather than assumed to
        be called like this one.
        """
        parent = var.parent()
        flows_out = getattr(parent, "flows_out", None) or {}

        suffix = "_fed_out"
        basename = var.basename() or ""
        flow_name = basename[: -len(suffix)] if basename.endswith(suffix) else self.name

        flow = flows_out.get(flow_name)

        return flow if isinstance(flow, FlowContinuousOut) else None

    def get_delivered(self):
        """What this input receives, connection by connection (R6, R16).

        A producer exports ONE variable to all its consumers, so that variable
        carries the total it delivers, not what this consumer gets: the share is
        read back from the producing flow's allocation, keyed by the consumer's
        component name.

        Falls back to the raw exported value for a connection whose producer has
        allocated nothing yet -- before the first production sweep, an output
        simply delivers what it holds.
        """
        count = self.var_in.cnctCount()

        if count == 0:
            return float(self.var_in_default)

        total = 0.0
        for index in range(count):
            var = self.var_in.variable(index)
            flow = self.supplier_flow(var)
            share = None if flow is None else flow.allocated_for(self.comp_name)
            total += float(var.value()) if share is None else float(share)

        return total

    def create_sensitive_set_flow_fed_in(self):
        """Build the closure mirroring what the incoming connections deliver.

        Reads :meth:`get_delivered`, so the mirror carries this consumer's
        ALLOCATED share rather than the totals its producers publish. With no
        allocation and no connection it degrades to
        ``IReference.sumValue(default)``, which is exactly the "several
        producers deliver their sum, an unconnected input reads its declared
        default" semantics of the primitives.
        """

        def sensitive_set_flow_template():
            self.var_fed.setValue(self.get_delivered())

        return sensitive_set_flow_template

    def update_sensitive_methods(self, comp):
        self.sm_flow_fed_fun = self.create_sensitive_set_flow_fed_in()
        self.sm_flow_fed_name = f"set_{self.name}_fed_in"

        self.var_in.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        # Seeds the value at t=0 (and for an unconnected input, whose reference
        # never notifies a change).
        comp.addStartMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

    def __repr__(self) -> str:
        base_str = super().__repr__()
        demand = self.var_demand.value() if self.var_demand is not None else "N/A"
        return f"{base_str} | demand: {self.format_flow_value(demand)}"

    def __str__(self) -> str:
        base_repr = super().__str__()
        demand = self.var_demand.value() if self.var_demand is not None else "N/A"
        return (
            f"{base_repr}\n"
            f"  {fg('white')}Demand{attr('reset')}: {self.format_flow_value(demand)}"
        )


class FlowContinuousOut(FlowContinuous):
    """Real-valued output flow.

    Carries a single rate variable (``{name}_fed_out``) exported downstream, and
    imports the demands published by its consumers. With no transformation rule
    and no capacity the rate it can produce is ``var_fed_default``, and what it
    DELIVERS is that rate capped by what its consumers ask for (R6).

    One variable is exported to every connection, so the variable carries the
    total delivered and the split among the consumers is held on the flow, in
    :attr:`allocated`. How that split is computed is declared here -- a policy
    (R16), or a Python rule of the component's own (R17).
    """

    var_demand_in_default: float = pydantic.Field(
        0.0,
        description="Aggregated demand value read when no consumer is connected",
    )

    allocation: str = pydantic.Field(
        ALLOCATION_PROPORTIONAL,
        description=(
            "How an insufficient supply is split among the consumers (R16): "
            "'proportional' to their demands (the default), 'shares' at the "
            "fixed shares declared in allocation_shares, or 'priority' in the "
            "order declared in allocation_priorities."
        ),
    )

    allocation_shares: typing.Dict[str, float] = pydantic.Field(
        default_factory=dict,
        description=(
            "Fixed shares of the 'shares' policy, {consumer component name: "
            "share}. Must sum to 1. A consumer no share is declared for takes "
            "none."
        ),
    )

    allocation_priorities: typing.Dict[str, float] = pydantic.Field(
        default_factory=dict,
        description=(
            "Priorities of the 'priority' policy, {consumer component name: "
            "priority}. The lowest number is served first; consumers sharing a "
            "priority split their allotment proportionally to their demands; a "
            "consumer no priority is declared for is served last."
        ),
    )

    allocation_fun: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description=(
            "The Python extension point of R17: a callable "
            "``split(available, demands) -> {consumer: quantity}`` used in "
            "PREFERENCE to the declared policy. It proposes a split and "
            "inherits the surplus redistribution, so a custom rule never has to "
            "reimplement the convergence."
        ),
    )

    allocated: typing.Dict[str, float] = pydantic.Field(
        default_factory=dict,
        exclude=True,
        repr=False,
        description=(
            "What the last production sweep allocated, {consumer component "
            "name: quantity}. Empty before the first one: an output that has "
            "not produced yet has nothing to share out."
        ),
    )

    @pydantic.model_validator(mode="after")
    def check_allocation(self):
        """Refuse a policy that cannot be applied, at DECLARATION time.

        A share map that does not sum to 1 is the case worth catching early: it
        distributes a quantity other than the one available, and it would
        otherwise only show up as a slightly wrong split deep inside a run.
        """
        if self.allocation not in ALLOCATION_POLICIES:
            raise ValueError(
                f"Output flow {self.name}: unknown allocation policy "
                f"{self.allocation!r}, expected one of "
                f"{', '.join(ALLOCATION_POLICIES)}"
            )

        if self.allocation_shares:
            total = sum(self.allocation_shares.values())
            if abs(total - 1.0) > 1e-9:
                shares = ", ".join(
                    f"{name}={share:g}"
                    for name, share in self.allocation_shares.items()
                )
                raise ValueError(
                    f"Output flow {self.name}: fixed allocation shares must sum "
                    f"to 1, got {total:g} ({shares})"
                )

        if self.allocation == ALLOCATION_SHARES and not self.allocation_shares:
            raise ValueError(
                f"Output flow {self.name}: allocation policy "
                f"{ALLOCATION_SHARES!r} needs allocation_shares, "
                "{consumer component name: share}"
            )

        if self.allocation == ALLOCATION_PRIORITY and not self.allocation_priorities:
            raise ValueError(
                f"Output flow {self.name}: allocation policy "
                f"{ALLOCATION_PRIORITY!r} needs allocation_priorities, "
                "{consumer component name: priority}"
            )

        return self

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, port="out", **kwargs)

        # Demand channel: the demands published by the downstream consumers.
        self.var_demand = comp.addReference(f"{self.name}_demand_in")

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_out")
        comp.addMessageBoxExport(f"{self.name}_out", self.var_fed, self.name)
        comp.addMessageBoxImport(
            f"{self.name}_out", self.var_demand, f"{self.name}_demand"
        )

    def get_var_demand_value(self):
        """Return the total demand published by the downstream consumers."""
        return self.var_demand.sumValue(self.var_demand_in_default)

    # -- what the consumers ask for ------------------------------------

    def consumer_demands(self):
        """The demand published by each consumer, keyed by component name.

        Read connection by connection rather than as a sum: an allocation has to
        know WHO asks for what, and the component behind a connection is reached
        through the demand variable it exports.

        Returns
        -------
        dict
            ``{consumer component name: demand}``, in connection order. Empty
            when no consumer is connected.
        """
        demands = {}

        for index in range(self.var_demand.cnctCount()):
            var = self.var_demand.variable(index)
            demands[var.parent().name()] = float(var.value())

        return demands

    def get_demand_bound(self):
        """The demand bounding what this output delivers.

        The sum of what the consumers ask for, and :data:`UNBOUNDED` when there
        is no consumer at all -- an output nobody is connected to is throttled
        by nobody, and keeps producing exactly what its inputs allow.
        """
        if self.var_demand.cnctCount() == 0:
            return UNBOUNDED

        return float(self.var_demand.sumValue(self.var_demand_in_default))

    # -- how what it delivers is split (R16, R17) ----------------------

    def get_allocation_split(self):
        """The split function this output allocates with.

        The Python rule of R17 wins over the declared policy: it exists exactly
        for the cases the declared ones do not cover.
        """
        if callable(self.allocation_fun):
            return self.allocation_fun

        if self.allocation == ALLOCATION_SHARES:
            shares = self.allocation_shares

            def split(available, demands):
                return split_shares(available, demands, shares)

            return split

        if self.allocation == ALLOCATION_PRIORITY:
            priorities = self.allocation_priorities

            def split(available, demands):
                return split_priority(available, demands, priorities)

            return split

        return split_proportional

    def allocate(self, available, demands=None, trace=None):
        """Split ``available`` among the consumers and record the result.

        Parameters
        ----------
        available : float
            What this output actually delivers this step.
        demands : mapping, optional
            Defaults to :meth:`consumer_demands`. Passing it is what lets the
            policies be exercised without an engine.
        trace : list, optional
            Passed through to :func:`allocate`, one entry per pass.

        Returns
        -------
        dict
            ``{consumer component name: quantity}``, also kept on
            :attr:`allocated` for the consumers to read back.
        """
        demands = self.consumer_demands() if demands is None else demands

        self.allocated = allocate(
            available, demands, self.get_allocation_split(), trace=trace
        )

        return self.allocated

    def allocated_for(self, comp_name):
        """What was allocated to one consumer, or None when nothing was.

        None and 0.0 are deliberately different: None means "this output has not
        allocated anything to that consumer", which is what makes a consumer
        fall back to the raw exported value before the first production sweep.
        """
        return self.allocated.get(comp_name)

    def update_sensitive_methods(self, comp):
        # No transformation rule and no capacity at this stage: the produced
        # rate holds the value it was declared with. Rule-driven recomputation
        # is installed by the rule/equation layer.
        pass

    def __repr__(self) -> str:
        base_str = super().__repr__()
        try:
            demand = self.get_var_demand_value()
        except Exception:
            demand = "N/A"
        return f"{base_str} | demand: {self.format_flow_value(demand)}"

    def __str__(self) -> str:
        base_repr = super().__str__()
        try:
            demand = self.get_var_demand_value()
        except Exception:
            demand = "N/A"
        return (
            f"{base_repr}\n"
            f"  {fg('white')}Demand{attr('reset')}: {self.format_flow_value(demand)}"
        )
