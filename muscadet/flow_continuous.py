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
``f_out_rate`` (``t_double``)    out flow     shared derating rate, 1 by default
``f_out_profile`` (``t_double``) out flow     applied time-profile factor, if any
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

A demand published upstream is not always the demand of a consumer: a capacity
crossed on the way adds what it claims for its own filling and subtracts what it
can no longer accept (:meth:`muscadet.capacity.Capacity.demand_claim`), which is
how a tank stocks up (R36) and how a full one throttles its producer (R7). Such a
claim may be :data:`UNBOUNDED` -- "whatever you can deliver" -- which needs no
bound of its own: a delivery is the lesser of production and demand, so the
producer's capability is what bounds it, and :func:`regularize_demands` turns it
into a finite share before any split is proposed.

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

A consumer may nevertheless take LESS than it was allocated -- a rule limited by
its scarcest input cannot use what the others were fetched at -- and it hands
the difference back through :meth:`FlowContinuousOut.restrict_allocation` (R-12).
That is not a revised demand and it reopens nothing: the split is lowered to a
quantity already computed, the surplus is offered again at the next evaluation
rather than reallocated within this one, and no consumer is consulted twice.

Who computes what
-----------------
A message box exports **one** variable to every connection, so a producer
cannot hand a different number to each consumer through ``f_fed_out``: that
variable carries the TOTAL it delivers. The split itself is held on the
producing flow, keyed by consumer component name (:attr:`FlowContinuousOut.
allocated`), and a consumer reads its own share back by resolving each
connection to the flow behind it -- see :meth:`FlowContinuousIn.get_delivered`.

Derating (R18, R19, R20)
------------------------
A continuous output carries an **effective rate**, defaulting to 1, by which
whatever it produces is multiplied. A rate of 0 is a total loss of production:
there is no separate boolean availability gate on a continuous flow (R19, KD10),
so the one number expresses both the cut and the degradation.

That rate is folded from TWO kinds of variable, and the fold is a **minimum**
(R20, KD11, KTD8) -- see :meth:`FlowContinuousOut.get_effective_rate`.

* ``{flow}_out_rate`` (:data:`RATE_VAR_FMT`, :attr:`FlowContinuousOut.
  var_out_rate`) -- the SHARED rate of KD10, created with the flow itself and
  holding :data:`NOMINAL_RATE`. Public, writable, and in existence from the
  moment the flow is declared, so anything that targets a component variable by
  name can reach it: a native ``cod3s.ObjMode2S`` / ``ObjFM*`` resolves its
  effects against the target's declared variables, and this is the one a
  continuous output offers it. muscadet never writes it.
* ``{mode}_derating_{flow}`` (:data:`DERATING_VAR_FMT`, held in
  :attr:`FlowContinuousOut.derating`) -- **one per (mode, output flow) pair**
  (R18), allocated for the modes muscadet declares itself and therefore knows
  the identity of.

The per-mode variable is what makes the minimum reachable *between modes
muscadet owns*. Were several of them to clamp one shared variable, whichever
fired last would win, and the first of them to repair would write the rate back
to 1 while the other degradation was still standing. The reset-and-reclamp
convention that lets boolean effects compose for free -- a gate reinitialised at
every step and re-clamped by every active mode, so ``False`` wins whatever the
order -- does not transpose to numbers, because no reset value is neutral for a
minimum. That is exactly why the shared ``{flow}_out_rate`` is left to what
muscadet does NOT own: a mode declared outside the library clamps it, muscadet
folds it in, and the two mechanisms compose instead of competing.

Time profiles
-------------
A continuous output may also carry a :class:`muscadet.Profile`: a declared
CONTINUOUS function of simulation time scaling what it produces. The three terms
compose as a **product**::

    produced = what the rule (or the declared rate) produces
               x  profile(t)
               x  min(out_rate, per-mode deratings)

The profile is a channel of its own and is never folded into
``{flow}_out_rate``, because the two must compose differently: deratings fold by
MINIMUM among themselves, while a profile MULTIPLIES whatever is left of the
output. A panel at 30 % irradiance that is also 50 % derated produces 15 %; the
minimum of 0.3 and 0.5 is 0.3, which overstates it by a factor of two with
nothing to signal it. See :mod:`muscadet.profile` for the continuity rule -- why
a discontinuous profile is refused rather than integrated wrong -- and for why a
profile factor may not be negative.

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
from .profile import NOMINAL_FACTOR, PROFILE_VAR_FMT, build_profile

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

#: Relative tolerance on "this consumer took less than it was allocated" (R-12).
#: Relative rather than absolute: a draw is a physical quantity of any
#: magnitude, and an absolute epsilon that is meaningless at 1e-3 is a real
#: release at 1e6. A residue below it is left where it is rather than spending a
#: write on giving it back.
UPTAKE_TOL = 1e-12

#: The effective rate of a continuous output nothing derates (R18). Also the
#: value a derating variable is created at, and the value a mode returns its own
#: derating variable to when it leaves the state that derated.
NOMINAL_RATE = 1.0

#: How a derating variable is named: from the mode that DECLARES it and the
#: output it bears on (R18). Two modes derating one output therefore own two
#: distinct variables, which is what makes the minimum of R20 reachable.
DERATING_VAR_FMT = "{mode}_derating_{flow}"

#: How the SHARED rate variable of a continuous output is named (KD10). Every
#: continuous output carries one, created with the flow itself and holding
#: :data:`NOMINAL_RATE`: ``H2`` gives ``H2_out_rate``. It exists so that a mode
#: muscadet knows nothing about -- a native ``cod3s.ObjMode2S`` / ``ObjFM*``,
#: which resolves its effects against the target's variables BY NAME -- has
#: something to clamp that is neither owned by the solver nor conjured by a
#: muscadet-specific call.
RATE_VAR_FMT = "{flow}_out_rate"


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

    @pydantic.model_validator(mode="before")
    @classmethod
    def check_declaration_keys(cls, data):
        """Refuse a declaration key this flow does not read, BY NAME.

        Pydantic's default is ``extra="ignore"``, so every key a continuous
        flow does not declare was accepted, dropped, and the parameter the
        modeller believed they had set took its default -- indistinguishable,
        for a numeric one, from a legitimate zero. The two spellings that make
        it worth closing generally:

        * ``FlowContinuousOut(var_prod_cond=["ctrl"], var_prod_default=True)``
          -- the DISCRETE production gate, written on a continuous output. The
          source is believed to be gated by a control port and produces
          unconditionally for the whole run;
        * ``FlowContinuousIn(demand=5.0)`` -- ``demand`` is the KB's own
          spelling (``ConsumerContinuous(demand=...)``), and the flow-level
          field is ``var_demand_default``. The consumer publishes a demand of
          zero and its whole chain reports zero.

        This is what ``FlowModel.combine`` / ``combine_fun`` were
        declared-and-refused to close for one key (R37), generalised: those two
        stay declared fields, so they reach their own validator and keep their
        own message about conserved quantities.

        The accepted set is the model's own fields, which is exactly what the
        flow reads -- the empirical basis of ``ContinuousComponent`` (R-3),
        where the set has to be declared because the component reads its
        parameters out of ``kwargs``. Scoped to the CONTINUOUS family: the
        discrete classes are 1.x surface and are left as they were.
        """
        if not isinstance(data, dict):
            return data

        accepted = set(cls.model_fields)
        unknown = sorted(set(data) - accepted)

        if unknown:
            plural = "s" if len(unknown) > 1 else ""
            unknown_str = ", ".join(repr(key) for key in unknown)
            accepted_str = ", ".join(sorted(accepted))
            raise ValueError(
                f"Flow {data.get('name')!r}: {cls.__name__} does not accept "
                f"declaration key{plural} {unknown_str}; it accepts "
                f"{accepted_str}"
            )

        return data

    @property
    def is_continuous(self) -> bool:
        """True: this flow's value moves between integration steps.

        What makes a comparison operand reading it a WATCHED threshold, whether
        it is declared on a rule guard (R21) or on a discrete production
        condition (R22).
        """
        return True

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
            "sweep BEFORE any input capacity claims on it. This is what the "
            "rules may draw: an input capacity changes what is published "
            "upstream -- less once full (R7), more while it fills (R36) -- "
            "never what the rules take from it. Unbounded until a sweep "
            "computes it, so a component whose demand equation never runs "
            "produces exactly what its inputs allow."
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

    def live_value(self):
        """What this consumer receives RIGHT NOW.

        Read from the connections rather than from the ``var_fed`` mirror: the
        mirror is refreshed by a sensitive method, which the solver runs between
        integration steps and not while it root-finds a crossing. A threshold
        read from it would therefore lag one step behind the value it watches --
        exactly what R12 forbids, on a rule guard (R21) and on a discrete
        production condition (R22) alike.

        This consumer's ALLOCATED share, not the totals its producers publish
        (R16): :meth:`get_delivered` reads the very same connection variables --
        so nothing is lagged by applying the allocation -- and it is what the
        rules actually consume. A guard testing a different number than the rule
        it guards draws on would select a mode the component cannot sustain, and
        the divergence appears exactly under the shortage the allocation layer
        exists for.
        """
        return float(self.get_delivered())

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

    def incoming_shares(self):
        """What each connection currently brings in, and who brings it.

        The connection-by-connection reading :meth:`get_delivered` sums, kept
        apart so the SAME walk answers both "how much arrives" and "who would
        have to be told if less than that is taken" (R-12). Two readings of the
        connections could otherwise disagree about a share, and a release
        computed against a different total than the draw it caps would give back
        the wrong quantity.

        Returns
        -------
        list of tuple
            ``(producing component, producing flow, quantity)`` in connection
            order. The flow is ``None`` for a connection whose producer is not a
            continuous output -- there is no split behind it to read, and
            nothing to give anything back to.
        """
        shares = []

        for index in range(self.var_in.cnctCount()):
            var = self.var_in.variable(index)
            value = float(var.value())
            flow = self.supplier_flow(var)
            share = None if flow is None else flow.share_for(self.comp_name, value)
            shares.append(
                (var.parent(), flow, value if share is None else float(share))
            )

        return shares

    def get_delivered(self):
        """What this input receives, connection by connection (R6, R16).

        A producer exports ONE variable to all its consumers, so that variable
        carries the total it delivers, not what this consumer gets: the share is
        read back from the producing flow's allocation, keyed by the consumer's
        component name.

        A producer that has not allocated anything -- before the first
        production sweep, or because its ``compute_production`` writes the
        exported variable itself and never splits it -- hands out a PROVISIONAL
        share instead (:meth:`FlowContinuousOut.provisional_share`). Reading the
        raw exported value there is what let every consumer of one producer
        record its whole output at instant 0 of a batch schedule, delivering
        several times what was produced.

        The raw value is still the answer for a connection whose producer is
        not a continuous output at all: there is no split behind it to read.
        """
        if self.var_in.cnctCount() == 0:
            return float(self.var_in_default)

        return sum(quantity for _, _, quantity in self.incoming_shares())

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

    var_out_rate: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description=(
            "The SHARED rate variable of this output, ``{flow}_out_rate`` "
            "(KD10), created with the flow at NOMINAL_RATE. Public and "
            "writable: it is what a mode declared OUTSIDE muscadet clamps, "
            "having no way to ask for a per-mode variable. Setting it to 0 is "
            "a total loss of production -- a continuous output carries no "
            "separate boolean availability gate (R19)."
        ),
    )

    derating: typing.Dict[str, typing.Any] = pydantic.Field(
        default_factory=dict,
        exclude=True,
        repr=False,
        description=(
            "The derating variables bearing on this output, {declaring mode "
            "name: variable}. One per (mode, output flow) pair (R18): a mode "
            "muscadet declares itself clamps the variable IT owns and never "
            "the shared one, so the effective rate can be the minimum over "
            "them (R20) instead of whatever the mode that fired last happened "
            "to write."
        ),
    )

    profile: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description=(
            "A :class:`muscadet.Profile`: a declared CONTINUOUS function of "
            "simulation time this output's production is multiplied by -- a "
            "solar curve, a daily cycle. Kept strictly apart from the derating "
            "rate above, and composed with it by PRODUCT: a profile is the "
            "size of the thing being degraded, not a competing degradation, so "
            "an output at 0.3 of profile that is also derated to 0.5 produces "
            "0.15. Folded into ``{flow}_out_rate`` the two would compose by "
            "MINIMUM instead, and production would read 0.5. A bare callable is "
            "refused: it carries no continuity attestation."
        ),
    )

    var_profile: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description=(
            "``{flow}_out_profile``, created only on an output that declares a "
            "profile. A read-only PUBLICATION of the factor currently applied, "
            "rewritten by the production sweep at every evaluation: it exists "
            "so the profile can be observed and indicated next to the rate, "
            "never so it can be driven. Writing it has no effect."
        ),
    )

    @pydantic.model_validator(mode="after")
    def check_profile(self):
        """Normalise the declared profile, and refuse one that is not usable.

        At DECLARATION time, like every other malformed declaration in this
        release: a profile that is not a declared-continuous
        :class:`muscadet.Profile` -- a bare callable, an unknown shape -- would
        otherwise only be found on the first integration step of the first run.
        """
        self.profile = build_profile(self.profile, self.name)

        return self

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

    def initial_fed_value(self):
        """What this output carries before the first production sweep has run.

        The declared rate, scaled by the profile AT INSTANT 0 when one is
        declared. Without it the first sample of every Monte Carlo sequence
        would report the unprofiled default -- a solar source announcing its
        peak rate at midnight -- because the engine samples instant 0 before it
        evaluates any equation, and the init value is what a sequence restarts
        from.
        """
        if self.profile is None:
            return self.var_fed_default

        factor = self.profile.factor(0.0, self.name)

        return float(self.var_fed_default or 0.0) * factor

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, port="out", **kwargs)

        # Demand channel: the demands published by the downstream consumers.
        self.var_demand = comp.addReference(f"{self.name}_demand_in")

        # The shared rate of KD10, declared HERE rather than allocated on
        # demand: a mode that knows nothing of muscadet cannot ask for a
        # variable to be created, it can only name one that already exists.
        # ``var_fed`` and ``var_demand`` are the solver's -- rewritten at every
        # integration step -- so this is the only endpoint such a mode can
        # usefully clamp.
        self.var_out_rate = comp.addVariable(
            self.rate_var_name(), pyc.TVarType.t_double, NOMINAL_RATE
        )

        # Only when a profile is declared: an output nothing profiles must stay
        # byte-identical to what it was, variable list included.
        if self.profile is not None:
            self.var_profile = comp.addVariable(
                self.profile_var_name(),
                pyc.TVarType.t_double,
                float(self.profile.factor(0.0, self.name)),
            )

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

    def restrict_allocation(self, comp_name, taken):
        """Lower what one consumer was allocated to what it actually took (R-12).

        The producing half of the over-draw fix. A rule runs at the scale its
        scarcest input allows, so a component asked -- and served -- on its
        DECLARED coefficients draws more of its abundant reagents than the
        reaction can use. What it did not use never entered a reaction, a stock
        or an output: it was destroyed, and behind a stocked supplier the
        accounting discrepancy became lost matter.

        The consumer therefore hands the surplus back, and this is where the
        producer's books are corrected: the split records what was taken, and
        ``{f}_fed_out`` -- documented as the total delivered downstream -- is
        reduced by the same amount, so it stays equal to the sum of the shares.
        The producer's own capacity, if one sits behind the output, is drained
        by the reduced figure too; that is :meth:`muscadet.ObjFlow.
        release_output`, which is the only caller.

        Nothing is redistributed to the OTHER consumers of this output. The
        production sweep is topological and they may already have run, so a
        surplus released here is available-but-untaken for this evaluation and
        offered again at the next one -- a deprivation becomes a delay instead
        of a permanent loss. Redistributing it within the sweep would reopen a
        fixed point the two-sweep ordering rests on not having (KTD12).

        A consumer this output never allocated anything to is left alone: there
        is no split to correct, only a provisional share standing in for one
        (:meth:`provisional_share`), and lowering that would freeze a figure
        that is meant to follow the exported value.

        Parameters
        ----------
        comp_name : str
            Engine name of the consuming component.
        taken : float
            What it actually drew, never more than it was allocated.

        Returns
        -------
        float
            The quantity released, 0 when there was nothing to release.
        """
        allocated = self.allocated.get(comp_name)

        if allocated is None:
            return 0.0

        taken = max(float(taken), 0.0)
        released = allocated - taken

        if released <= abs(allocated) * UPTAKE_TOL:
            return 0.0

        self.allocated[comp_name] = taken
        self.var_fed.setValue(max(float(self.var_fed.value()) - released, 0.0))

        return released

    def allocated_for(self, comp_name):
        """What was allocated to one consumer, or None when nothing was.

        None and 0.0 are deliberately different: None means "this output has not
        allocated anything to that consumer", which is what sends a consumer to
        the provisional split of :meth:`provisional_share`.
        """
        return self.allocated.get(comp_name)

    def reset_allocation(self):
        """Forget the split, registered as a start method.

        :attr:`allocated` is Python state that no engine reinitialisation
        touches, so without this the first sample of every Monte Carlo sequence
        but the first would read what the PREVIOUS sequence ended on. Cleared
        rather than recomputed: an empty allocation is the honest statement
        that this sequence's production sweep has not run yet, and it is what
        routes a consumer to the provisional split.
        """
        self.allocated = {}

    # -- what a consumer reads before the first sweep ------------------

    def provisional_demands(self):
        """The demands a PRE-SWEEP split is proposed over.

        The demand channel carries two different things before the first demand
        sweep has run, and :attr:`FlowContinuousIn.var_demand_default` documents
        both: the DECLARED demand of a pure consumer, which is final, and the
        placeholder of a consumer that derives its demand from its own outputs,
        which is not. Nothing on the wire tells them apart, so a demand of zero
        is read here as "this consumer has not said what it wants yet" and
        widened to :data:`UNBOUNDED` -- the same convention
        :meth:`get_demand_bound` applies to an output nobody is connected to.

        Reading it the other way round would be destructive rather than merely
        approximate: a component whose production rule is GUARDED on what it
        receives would start at zero, select the idle rule, derive a zero demand
        from it and stay there -- a self-consistent standstill the demand sweep
        never leaves. Over-stating an underived demand only costs an optimistic
        first sample, which is exactly what such a consumer read before.
        """
        return {
            name: (demand if demand > 0.0 else UNBOUNDED)
            for name, demand in self.consumer_demands().items()
        }

    def provisional_share(self, comp_name, available):
        """One consumer's share of ``available``, when nothing was allocated.

        Proposed over :meth:`provisional_demands`, through this flow's own
        policy. What it guarantees is the conservation the raw exported value
        broke: the provisional shares of one output's consumers never total
        more than what that output exports, whereas handing each of them the
        whole exported value multiplied it by the consumer count.

        Deliberately NOT written to :attr:`allocated`. A component whose
        ``compute_production`` writes its exported variable itself never
        allocates at all, so a stored split would freeze at its first value
        while the variable it splits went on moving -- and a threshold watched
        downstream would never see the crossing.

        Deliberately not routed through
        :meth:`muscadet.ObjFlow.allocate_output` either: that override point
        shapes what a component DELIVERS once it has produced, and this splits
        what has not been produced yet.
        """
        split = allocate(
            available, self.provisional_demands(), self.get_allocation_split()
        )

        return split.get(comp_name)

    def share_for(self, comp_name, available):
        """What one consumer receives over one connection carrying ``available``.

        The allocated share when a production sweep wrote one, the provisional
        split otherwise. None when this output has nothing to say about that
        consumer at all -- it is not among the ones publishing a demand here.
        """
        share = self.allocated_for(comp_name)

        if share is not None:
            return share

        return self.provisional_share(comp_name, available)

    # -- how much of it a failure mode leaves (R18, R19, R20) ----------

    def rate_var_name(self):
        """Name of the shared rate variable of this output (KD10).

        ``H2`` gives ``H2_out_rate``. Derived from the flow name alone, since
        the point of this one is to be guessable from outside the library: a
        mode declares its effect against it by name, with no muscadet-specific
        call to allocate anything.
        """
        return RATE_VAR_FMT.format(flow=self.name)

    def derating_var_name(self, mode_name):
        """Name of the derating variable ``mode_name`` owns on this output.

        Derived from the declaring mode AND the output, per R18, so the name is
        discoverable from either end: a mode knows what it derates, and an
        output knows which modes derate it through :attr:`derating`.
        """
        return DERATING_VAR_FMT.format(mode=mode_name, flow=self.name)

    def register_derating(self, comp, mode_name):
        """Allocate, once, the derating variable of ``mode_name`` on this output.

        Idempotent on purpose: a mode naming this output in BOTH directions of
        its automaton -- degrade on occurrence, restore on repair -- keeps one
        variable, and re-declaring the same effect never doubles it.

        Parameters
        ----------
        comp : muscadet.ObjFlow
            The component owning this flow; the variable is declared on it, so
            that the effect machinery resolves it like any other variable.
        mode_name : str
            The declaring mode. Unique per component, which is what makes the
            variable name unique.

        Returns
        -------
        The PyCATSHOO variable, created at :data:`NOMINAL_RATE`.
        """
        var = self.derating.get(mode_name)

        if var is None:
            var = comp.addVariable(
                self.derating_var_name(mode_name),
                pyc.TVarType.t_double,
                NOMINAL_RATE,
            )
            self.derating[mode_name] = var

        return var

    def get_effective_rate(self):
        """The rate this output's production is multiplied by (R18, R20, KD11).

        The **minimum** over the shared ``{flow}_out_rate`` (KD10) and the
        per-mode derating variables registered against it, and
        :data:`NOMINAL_RATE` when nothing derates it at all.

        The two mechanisms compose rather than compete: a mode declared outside
        muscadet clamps the shared variable, a mode muscadet declares itself
        clamps the one it owns, and the deeper of the two is what the output
        produces at. The minimum is order-independent: two modes degrading at
        0.5 and 0.8 give 0.5 whichever fired first, and the one repairing leaves
        the other's 0.8 standing.
        """
        rates = [float(var.value()) for var in self.derating.values()]

        if self.var_out_rate is not None:
            rates.append(float(self.var_out_rate.value()))

        return min(rates) if rates else NOMINAL_RATE

    # -- how much of it the time profile calls for ---------------------

    def profile_var_name(self):
        """Name of the published profile variable of this output.

        ``H2`` gives ``H2_out_profile``. Deliberately distinct from
        ``H2_out_rate``: the two multiply, they do not fold together, and
        naming them alike would invite exactly the substitution that turns a
        product into a minimum.
        """
        return PROFILE_VAR_FMT.format(flow=self.name)

    def get_profile_factor(self, time):
        """The factor this output's production is multiplied by at ``time``.

        :data:`~muscadet.profile.NOMINAL_FACTOR` when no profile is declared,
        so an unprofiled output is unaffected.

        **Multiplied with the effective rate, never folded into it.** The rate
        is what failure modes leave of the output and composes by minimum
        (R20); the profile is how large the output is at this instant. A panel
        at 0.3 of its curve that is also derated to 0.5 produces 0.15, and the
        minimum of the two would give 0.5.
        """
        if self.profile is None:
            return NOMINAL_FACTOR

        return self.profile.factor(time, self.name)

    def publish_profile_factor(self, factor):
        """Mirror the applied factor onto ``{flow}_out_profile``, if it exists.

        A publication and not a channel: nothing reads it back, so a model that
        never declared a profile pays nothing and one that did can indicate the
        curve alongside the quantity it scales.
        """
        if self.var_profile is not None:
            self.var_profile.setDValue(float(factor))

    def update_sensitive_methods(self, comp):
        # No transformation rule and no capacity at this stage: the produced
        # rate holds the value it was declared with. Rule-driven recomputation
        # is installed by the rule/equation layer.
        #
        # The split, on the other hand, is Python state surviving the end of a
        # Monte Carlo sequence, so it is dropped at the beginning of each one.
        comp.addStartMethod(f"reset_{self.name}_allocation", self.reset_allocation)

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
