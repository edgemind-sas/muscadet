"""Automatic equation ordering (R8, R30).

PyCATSHOO needs an explicit integer order per equation method, and a model
author must never write one down: adding a component would then force every
other component's declaration to be renumbered. This module derives the whole
sequence from the connection graph instead.

Why a plain topological sort is enough (KTD1)
---------------------------------------------
Two independent sweeps -- demand in reverse-topological order, production in
topological order -- are sufficient **provided** every remaining loop is broken
by an integrated state. Capacities and mode automata are the state breaks, and
:data:`R30` refuses what is left. So no matching, no block-triangular
decomposition and no iterative solve -- a topological sort of the *algebraic*
dependencies is the whole mechanism.

Which loops a capacity breaks, and which it does not (R-14)
-----------------------------------------------------------
The module used to refuse **every** cycle, which contradicted the paragraph
above: a tank wired to a recirculation pump and back is a loop whose closing
dependency crosses an ODE level, and that is precisely the shape of the
heated-tank dynamic-reliability benchmark. Such a model was refused at its first
``simulate()``.

An edge ``A --q--> B`` exists because B reads what A exports. Dropping it lets B
run first, which is sound exactly when **B's own exports do not algebraically
depend on what arrived on q** -- and a capacity of B's is what can make that
true, because the volume, not the connection, is then the counterparty of the
rules (KTD13):

* a capacity of B holding ``q`` on its **input** side. What arrives is written
  into the volume by ``fill_input_capacities`` and integrated there; the rules
  face ``Capacity.serve_limit`` instead of the flow. Every path out of q crosses
  the level, whatever else B produces;
* a capacity of B holding **every one of its continuous outputs** on the
  ``out`` side. What the rules produce enters the volume and what leaves is
  served from it, so no output carries the arriving quantity onward
  algebraically. This is the two-sided tank of
  ``CapacityContinuous(ports="both")``, whose capacity ``side`` is ``"out"``,
  and it is the case the recirculation loop needs. Requiring *every* output is
  what keeps the break honest: a transformer buffering one output and exporting
  another straight through still passes its input on algebraically.

The break is a property of the **receiving** component, never of the sending
one: a capacity on A's output side does not license B to run first, since B
would still use the stale value algebraically.

The break is **structural** -- declared, not conditioned on the level -- like
every other capacity test in this release. Its residual is honest and worth
recording: a volume standing at zero degrades to a pass-through (``serve_limit``
falls back to what transits, ``draw_from_capacity`` serves the transit), so the
torn dependency is then read one evaluation late rather than not at all. The
solver evaluates the equation set several times per integration step at an
advancing time, so that is a within-step lag absorbed by the level, which is the
same residual any state-variable tearing carries.

**Nothing acyclic changes, and the tear is minimal.** The full edge set is
sorted first, so a model that builds today derives exactly the order it derived
before; a cycle is then torn one reported loop at a time, dropping only the
state-broken connections **on that loop**, so a buffered edge elsewhere in the
model keeps constraining the order it always did. A loop carrying no such
connection is a genuinely algebraic one and is still refused, with the same
:class:`ContinuousFlowCycleError`.

The **demand** sweep is torn at the same edge, and a capacity does NOT break it:
``Capacity.demand_claim`` passes a demand straight through a volume by design
(R7, R36). A recirculation loop's demand is therefore a unit-gain fixpoint read
one evaluation late -- neutrally stable, and drifting by the claim per
evaluation if a ``fill_rate`` or a rival consumer injects one inside the loop.
Declare the fill claim outside the loop.

Where the graph comes from (KTD15)
----------------------------------
Neither ``muscadet.System`` nor its ``cod3s`` base keeps a Python-side registry
of connections, and instrumenting ``connect`` / ``auto_connect`` /
``connect_flow`` separately would drift from what the engine actually wired. The
topology is therefore **read back from the engine**, walking each component's
message boxes and their connected counterparts -- the walk
``cod3s.PycComponent.get_cnct_info`` already performs.

What counts as an edge
----------------------
A message box contributes an edge only when it is the **data channel of a
continuous output**. Everything else a component exports is skipped:

=================================  ==============================================
Channel                            Why it is not an edge
=================================  ==============================================
``{f}_available_out`` / ``_in``    availability channel of a *discrete* flow
``{f}_trigger_in``                 third channel of a discrete trigger flow
``{f}_out`` on a logic gate        exported by no flow object at all
``{c}_level_out`` / ``_in``        measurement link: carries no quantity (R33, R37)
``{f}_out`` of a discrete flow     discrete flows are not continuous flows
=================================  ==============================================

Missing one of those would feed a spurious edge into the acyclicity check and
turn a valid model into a first-run error -- which is exactly what would break
the sensor pattern of AE18, where a component reads a capacity level and gates
its own supplier back through a discrete control flow.

Note that U2 gives a continuous flow **one bidirectional message box per port**,
carrying both the data alias and the demand alias. The box is therefore shared
between the two directions, so the filter is on *the flow behind the box*, never
on the box name alone -- see :func:`continuous_data_channel`.

Two detection paths, and why the second is not a clause of the first (R43)
--------------------------------------------------------------------------
:func:`find_rate_comparison_loops` walks the channels the graph drops, and it
walks them **from the flow collections of a component**: which continuous
inputs it compares, which discrete outputs those comparisons drive. That
indexing carried two assumptions, and R38 and R39 each broke one.

* it exempted measurement links wholesale, on the written ground that a
  measurement carries a capacity LEVEL -- an integrated state, which breaks a
  loop. Since R38 a continuous output publishes the rate it DELIVERS, and a
  measurement channel declared ``kind="rate"`` reads it. That number is
  recomputed by the allocation sweep at every evaluation: a threshold on it is
  as algebraic as a threshold on the transported flow, so the exemption now
  waves through the very shape :class:`RateComparisonLoopError` exists for;
* it can only see a component that HAS flow collections. A controller
  (:class:`muscadet.ObjCtrl`, a peer of ``ObjFlow``) has none at all, so it is
  neither a node of the graph nor a stop of that walk. Its edges are not
  exempted, they are invisible.

Hence :func:`find_rate_observation_loops`, a **second** path rather than a
third clause of the first. It is indexed on readings instead of on flows: it
reads its edges off the raw wiring of each component's measurement boxes, marks
the readings that are algebraic, follows them through republishers, and closes
the loop when the signal a marked reading drives reaches an ancestor -- in THIS
module's graph -- of the component delivering the rate. The two paths meet only
in :func:`_walk_signal` and in :meth:`ContinuousFlowGraph.ancestors`, which is
what lets a node absent from the graph still be tested against it.

A third shape, and why it needs neither a third path nor a wider walk (R47)
--------------------------------------------------------------------------
Both paths above follow a verdict from where it is decided to where it lands,
and both follow it on DISCRETE channels. A verdict can also leave as the rate
itself -- a continuous output carrying a production condition (R44) -- and then
neither can see it: the graph holds no edge for the observation half, and the
walk recognises no channel for the command half.

Widening the walk to return continuous outputs was tried and measured: it does
return them, and the montage still builds, because the verdict has no discrete
channel to travel on and the walk ends where it began. What closes it is that
there is **nothing to travel**: a commanded rate is already carried by the
graph, so what is left to ask is whether the commanded output reaches the
producer of the observed rate, and whether that producer's own declaration
turns what arrives into the rate being read. That is
:func:`commanded_rate_wiring` and :func:`continuous_input_feeds`, read by
:func:`find_rate_observation_loops` over what :func:`rate_driven_outputs`
returns, and it costs no new path, no new marking and no change to what a rule
guard is reported as driving.

The transported twin needs nothing at all: a command leg and an observed leg
that are both edges close a plain cycle, which the graph already refuses.

**No observation edge ever enters the flow graph**, and that separation is
deliberate on both sides. It is why R38 refuses an output flow named
``{f}_rate`` beside ``{f}`` (KD19): the collection of output flows must never
be able to hold that name, or :func:`continuous_data_channel` would resolve an
observation box as a transport edge and the acyclicity check would refuse a
loop the model does not close. This module upholds the same line from the other
end -- :func:`find_rate_observation_loops` only ever READS the graph.

The integer bands
-----------------
Every equation gets a **distinct** integer (KTD3): PyCATSHOO falls back to
alphabetical equation-name order when two equations share an order value, so
ties would make the evaluation sequence a function of equation *names* rather
than of the graph. The integer space is banded, and the bands reproduce the
evaluation sequence of one integration step:

1. capability sweep, topological      -- allocated here, from 0 upwards (R-20)
2. demand sweep, reverse-topological  -- allocated here, straight after
3. production sweep, topological      -- allocated here, straight after that
4. capacity levels integrate          -- :data:`CAPACITY_ORDER_BASE` upwards
5. signal nodes republish             -- :data:`CONTROL_ORDER_BASE` upwards (R45),
   published measurements and controllers alike, sorted together

The capability band is **first**, and it has to be: a demand is bounded by what
the rule's other inputs could supply, so every capability in the system must be
settled before the first demand equation runs. It carries no base constant of
its own because it is graph-derived like the two sweeps below it -- the three
share one allocator, which is also what keeps their integers distinct.

The controller band, and why it is the top one (R45)
----------------------------------------------------

A controller republishes what its observation inputs currently carry, and one
controller's value output IS another's observation input (R4). A chain of them
therefore has an evaluation order, and only one: upstream first. Evaluated
backwards, each controller republishes what the one before it published in the
PREVIOUS evaluation, so a chain of three carries a number from end to end in
three passes instead of one -- silently, and by a lag no reading shows, because
the solver evaluates the equation set many times per integration step and the
chain has caught up by the time anything is read back.

The band is therefore its own, and it sits **above the measurement one**: a
controller reads a measurement, so every published reading must be current
before the first controller equation runs. Within the band the integer comes
from a topological sort of the SIGNAL graph -- who publishes into whom -- and
not from the order the components were declared in, which is what a chain
written downstream first used to get.

The signal graph is built by :func:`controller_signal_links` and holds **every
node that publishes a reading an equation refreshes**: a controller's VALUE
output and an ``ObjFlow``'s sourced ``MeasurementOut`` alike, which is what the
shipped ``SensorContinuous`` compiles its ``publish`` channel to. It was a graph
of controllers only, and the instrument drew from a band of its own below them,
allocated in declaration order at declaration time -- so an instrument reading a
controller was refreshed BEFORE it however the controllers were sorted, and two
instruments in a row ran in the order they were written in. Both are gone: one
band, one sort, and the question the sort answers -- has this reading settled --
does not depend on the class answering it.

Two limits follow, and both are deliberate:

* **the sort is over COMPONENTS, so the refusal is too.** Two nodes
  republishing into each other close a loop of two whatever channels carry the
  two publications, and a set of channels that forms no loop is refused with
  them: a controller watching, through an instrument, a reading it publishes
  itself is the shape this costs. It is the granularity controllers have always
  been refused at; widening the population widened what it catches. A
  per-channel graph is what would close it, and it would change the existing
  controller refusal too, which is why it is not done here;
* a loop closing through a rate OBSERVATION is not this graph's business:
  :func:`find_rate_observation_loops` reports it, with the message written for
  it. What is refused here is the shape that walk terminates on rather than
  reports -- a chain of controller republications that closes on itself, which
  has no evaluation order at all.

A capacity equation only reads its own transit variables and writes its own
levels, so it carries no cross-component constraint -- but it must still take a
distinct integer, and it must run last. It is registered at *declaration* time,
long before the graph is known, so it draws from the top band:
``muscadet.System._capacity_equation_order_next`` starts at
:data:`CAPACITY_ORDER_BASE`, which is how the provisional counter of the
capacity unit is superseded by this module's allocation.
"""

import graphlib
import itertools
import typing

# Reused rather than reimplemented: reading a production condition's aligned
# comparison matrix has exactly one correct handling of a missing entry, and two
# copies of it would be two chances to disagree about what an empty matrix means.
from .capability import register_capability_variables
from .evaluation import rule_named_flows
from .flow import _prod_cond_matrix_entry
from .flow_continuous import FlowContinuous, rate_observation_box

#: Equation method looked up on each component for the capability sweep (R-20).
#: Registered FIRST, on the same topological order the production sweep uses: a
#: producer must publish what it could deliver before the component it feeds
#: sizes a demand against it.
CAPABILITY_EQUATION_METHOD = "compute_capability"

#: Equation method looked up on each component for the demand sweep. The sweep
#: itself lands in a later unit; a component that does not define this method is
#: simply skipped, so the machinery is complete before its first client exists.
DEMAND_EQUATION_METHOD = "compute_demand"

#: Equation method looked up on each component for the production sweep.
PRODUCTION_EQUATION_METHOD = "compute_production"

#: Equation method a controller republishes under (R45). Looked up here only to
#: name the registration; the controller owns the registration itself, which is
#: what keeps this module free of an import of the controller unit.
CONTROL_EQUATION_METHOD = "compute_controls"

#: The equation an ``ObjFlow`` refreshes its sourced publications with (R37).
#: It takes its integer from the same band, and the same sort, as the one
#: above: the two are one concept, a publication refreshed from something read.
MEASUREMENT_EQUATION_METHOD = "compute_measurements"

#: First integer of the capacity band. Capacity equations are registered when a
#: capacity is *declared*, before any graph exists, so they cannot be part of the
#: graph-derived allocation -- they take the top band instead, which also makes
#: them run after both sweeps, as the evaluation sequence requires.
CAPACITY_ORDER_BASE = 1_000_000

#: First integer of the SIGNAL band (R37, R45). The top band, above the capacity
#: one because every publication is ultimately taken from a level a capacity
#: holds: the level must be current before the instrument reporting it is
#: refreshed. Within the band the integers follow a topological sort of the
#: signal graph, which is what makes a chain settle in ONE evaluation of the
#: equation set.
#:
#: **One band for two equations**, and it used to be two. A published
#: measurement had a band of its own below this one, allocated in declaration
#: order at declaration time, so an instrument reading a controller was
#: refreshed before it whatever the wiring said, and two instruments in a row
#: ran in the order they were written in. What the sort answers is "this
#: reading has settled", and that question does not depend on the class
#: answering it. The old constant is gone with the band. The base is unchanged,
#: but the integers above it are NOT: an instrument sorting ahead of a
#: controller takes one from the same allocator, so a model that reads an
#: equation order back sees its controllers shifted up by however many
#: publications precede them.
CONTROL_ORDER_BASE = 3_000_000


# ----------------------------------------------------------------------
# The filtering predicate
# ----------------------------------------------------------------------


def continuous_data_channel(comp, mb_name, port):
    """Name of the continuous flow whose data channel ``mb_name`` is, or None.

    This is *the* filter that decides whether a message box takes part in the
    graph. It resolves the box back to the flow object behind it, because a
    continuous flow's box is bidirectional (it carries the data alias *and* the
    demand alias), and because several unrelated channels share the same
    ``_in`` / ``_out`` suffix.

    Parameters
    ----------
    comp : muscadet.ObjFlow
        Component owning the message box.
    mb_name : str
        Message box base name, e.g. ``"q_out"``.
    port : str
        ``"out"`` to test it against the component's output flows, ``"in"``
        against its input flows.

    Returns
    -------
    str or None
        The flow name when the box is the data channel of a *continuous* flow
        of that direction, None for every other box -- availability channels,
        trigger channels, measurement links, logic-gate exports and discrete
        data channels alike.
    """
    suffix = f"_{port}"
    if not mb_name or not mb_name.endswith(suffix):
        return None

    flow_name = mb_name[: -len(suffix)]
    flows = getattr(comp, "flows_out" if port == "out" else "flows_in", None) or {}
    flow = flows.get(flow_name)

    return flow_name if isinstance(flow, FlowContinuous) else None


def discrete_data_channel(comp, mb_name, port):
    """Name of the DISCRETE flow whose data channel ``mb_name`` is, or None.

    The mirror of :func:`continuous_data_channel`, and the filter deciding what
    the signal walk of :func:`find_rate_comparison_loops` may travel along. A
    discrete flow carries an availability channel and, on a trigger flow, a
    third one; only the data channel carries the flow's own state, so only it
    propagates the value a comparison produced.
    """
    suffix = f"_{port}"
    if not mb_name or not mb_name.endswith(suffix):
        return None

    flow_name = mb_name[: -len(suffix)]
    flows = getattr(comp, "flows_out" if port == "out" else "flows_in", None) or {}
    flow = flows.get(flow_name)

    if flow is None or isinstance(flow, FlowContinuous):
        return None

    return flow_name


def capacity_breaks_inbound(comp, flow_name):
    """True when a volume of ``comp`` stands between what arrives and what leaves.

    The predicate of R-14: whether an edge delivering ``flow_name`` to ``comp``
    still constrains the evaluation order, or whether an integrated level
    already breaks it. See the module docstring for the argument; in short, the
    two ways every algebraic path out of that input crosses a level are

    * a capacity holding ``flow_name`` on the **input** side -- what arrives is
      integrated before any rule reads it (KTD13, hop 1). **Unless its
      discharge condition READS that flow** (R49, R50): ``serve_holds`` takes
      its quantity live, so what leaves the volume then depends algebraically
      on what arrives and the level is no longer between the two. Without this
      clause the edge was torn on the sole ground that a volume held the flow,
      and a genuinely algebraic loop got an evaluation order instead of a
      refusal;
    * a capacity holding **every** continuous output of ``comp`` on the
      ``out`` side -- what the rules produce enters a volume and what leaves is
      served from it, so nothing carries the arriving quantity onward. This is
      ``CapacityContinuous(ports="both")``, whose ``side`` is ``"out"``.

    False for a component exporting no continuous flow at all -- a pure
    consumer. Nothing algebraic leaves it either, so tearing its inbound edge
    would be sound; it is left alone because it can take part in no loop, and a
    tear that breaks nothing would only make the reported one harder to read.

    Purely structural. It asks whether a capacity is DECLARED, never what it
    currently holds -- an equation order is derived once, at the pre-run step,
    and a level moves. The residual is recorded in the module docstring.

    Parameters
    ----------
    comp : muscadet.ObjFlow
        The component RECEIVING the flow. The break is never a property of the
        sender: a capacity behind a producer's output does not let its consumer
        run first, since the consumer would use the stale value algebraically.
    flow_name : str
        Name of the continuous flow arriving on ``comp``.

    Returns
    -------
    bool
    """
    get_capacity = getattr(comp, "get_capacity_of_flow", None)

    if not callable(get_capacity):
        return False

    inbound = get_capacity(flow_name, "in")

    if inbound is not None:
        return not discharge_reads(comp, inbound, flow_name)

    outputs = getattr(comp, "flows_continuous_out", None) or {}

    return bool(outputs) and all(
        get_capacity(name, "out") is not None for name in outputs
    )


def discharge_reads(comp, capacity, flow_name):
    """True when ``capacity``'s discharge condition reads ``flow_name`` (R50).

    What breaks a :func:`capacity_breaks_inbound` tear. A condition naming the
    arriving flow makes what LEAVES the volume depend on what ARRIVES within
    the instant -- ``serve_holds`` reads the quantity live, exactly as R12
    requires of any comparison -- so the level no longer stands between the
    two and the edge is a plain algebraic dependency again.
    """
    flow = (getattr(comp, "flows_in", None) or {}).get(flow_name)

    if flow is None:
        return False

    return any(source is flow for source, _ in serve_cond_operands(capacity))


def component_is_continuous(comp):
    """True when ``comp`` carries at least one continuous flow.

    Uses the filtered properties the continuous-flow unit put on ``ObjFlow``,
    so a component's flow dicts are read once through the same lens everywhere.
    """
    return bool(getattr(comp, "flows_continuous_in", None)) or bool(
        getattr(comp, "flows_continuous_out", None)
    )


def engine_name_index(components):
    """Map each component's ENGINE name to its ``system.comp`` key.

    Message boxes report their counterpart by the component's engine name,
    which is the ``system.comp`` key for a flat model -- resolved rather than
    assumed, so a renamed or nested component still lands on the right node.
    First declaration wins, as a duplicate engine name cannot be told apart.

    ``system.comp`` is a public dict a model may put anything into, and the
    walks reading this index already resolve their own entries defensively
    (``components.get(...)``, ``getattr(comp, ...)``): an entry with no
    ``name()`` is skipped here for the same reason, rather than aborting the
    pre-run check of the whole system on it.
    """
    index = {}

    for key, comp in components.items():
        name = getattr(comp, "name", None)

        if not callable(name):
            continue

        index.setdefault(name(), key)

    return index


# ----------------------------------------------------------------------
# The graph
# ----------------------------------------------------------------------


class ContinuousConnection(typing.NamedTuple):
    """One continuous data connection, named the way the modeller wired it."""

    source: str
    target: str
    flow: str

    #: True when a capacity of the TARGET already breaks this dependency, so it
    #: need not constrain the evaluation order (R-14,
    #: :func:`capacity_breaks_inbound`). Defaulted so a connection built by hand
    #: -- in a test, in an inspection -- stays the plain algebraic edge it was.
    state_broken: bool = False

    def __str__(self) -> str:
        return f"{self.source}.{self.flow}_out -> {self.target}.{self.flow}_in"


class SignalConnection(typing.NamedTuple):
    """One DISCRETE data connection, named the way the modeller wired it.

    Same shape and same rendering as :class:`ContinuousConnection`, because the
    two families usually share the ``{flow}_out`` / ``{flow}_in`` naming
    convention: a loop closed through a discrete signal is reported with its
    continuous and its discrete connections side by side, in the order they
    close it.

    ``inbound`` is the name the SIGNAL ARRIVES UNDER, which is not always the
    one it left under: ``System.connect`` takes an interface name on each side,
    so ``connect("GATE", "alarm_out", "BAT", "supply_in")`` is legal. Kept
    beside ``flow`` for the same reason :class:`ObservationConnection` keeps
    both of its box names, and defaulted so a connection built by hand -- in a
    test, in an inspection -- renders as it always did.
    """

    source: str
    target: str
    flow: str
    inbound: typing.Optional[str] = None

    def __str__(self) -> str:
        arrives = self.inbound or self.flow

        return f"{self.source}.{self.flow}_out -> {self.target}.{arrives}_in"


class ObservationConnection(typing.NamedTuple):
    """One OBSERVATION link, named the way the modeller wired it (R33, R37, R38).

    Reported beside the continuous and discrete connections of a loop, and kept
    apart from both by its own type: it carries no quantity, so it is not a
    :class:`ContinuousConnection`, and it carries a number rather than a state,
    so it is not a :class:`SignalConnection`.

    The two box names are stored rather than derived from ``channel``, because
    the two natures of a measurement link spell them differently -- ``q_rate_out``
    for a delivered rate (R38), ``q_level_out`` for a level -- and a rendering
    that guessed would misname exactly the link a modeller has to go and find.
    """

    source: str
    target: str
    channel: str
    source_box: str
    target_box: str

    def __str__(self) -> str:
        return f"{self.source}.{self.source_box} -> {self.target}.{self.target_box}"


class ContinuousFlowCycleError(ValueError):
    """A continuous-flow cycle, refused at the system's first run (R30).

    Subclasses ``ValueError`` so a model error stays a model error for any
    caller already catching one.
    """

    def __init__(self, cycle, connections, message=None):
        self.cycle = list(cycle)
        self.connections = list(connections)

        super().__init__(self.default_message() if message is None else message)

    @property
    def path(self) -> str:
        """The loop, as the components it runs through."""
        return " -> ".join(self.cycle)

    @property
    def wiring(self) -> str:
        """The connections closing the loop, in the order they close it."""
        return ", ".join(str(cnct) for cnct in self.connections) or "none found"

    def default_message(self) -> str:
        return (
            "Continuous flow graph must be acyclic (R30): "
            f"{self.path} closes a loop. "
            f"Connections closing the loop: {self.wiring}"
        )


def capacity_clause(capacities: typing.Sequence[str], what: str) -> str:
    """The sentence a refusal names the capacities carrying a command with.

    Empty when none does, so a loop closed the way it always was reads exactly
    as it always did: the clause is ADDED to a message, never woven into it.
    """
    if not capacities:
        return ""

    names = ", ".join(repr(name) for name in capacities)
    plural = "capacity" if len(capacities) == 1 else "capacities"

    return f" The {what} is declared on the {plural} {names} (serve_cond, R49)."


class RateComparisonLoopError(ContinuousFlowCycleError):
    """A discrete signal thresholded on a RATE, wired back upstream (R30).

    The graph of continuous connections is acyclic, but a loop closes through
    channels that graph does not carry: a comparison against a continuous flow
    VALUE drives a discrete output, and that output reaches a component
    upstream of the very flow the comparison reads. Nothing integrates along
    that path, so the regimes on either side of the threshold select each other
    within one instant.

    Kept apart from a plain cycle by its type, and by a message naming the
    comparison and the supported alternative -- but it IS a
    :class:`ContinuousFlowCycleError`, so a caller already catching a first-run
    cycle catches this one too.
    """

    def __init__(self, reader, flow, operand, connections, capacities=()):
        #: Component carrying the comparison.
        self.reader = reader
        #: Continuous input flow it compares.
        self.flow = flow
        #: The comparison, rendered as it was declared.
        self.operand = operand
        #: Capacities of the ARRIVING component whose discharge condition the
        #: signal gates, empty when it gates a rule, an output or a mode (R50).
        self.capacities = list(capacities)

        cycle = [cnct.source for cnct in connections] + [connections[-1].target]

        super().__init__(
            cycle,
            connections,
            message=(
                "Continuous flow graph must be acyclic (R30): "
                f"{' -> '.join(cycle)} closes a loop through a rate comparison. "
                f"Connections closing the loop: "
                f"{', '.join(str(cnct) for cnct in connections)}. "
                f"{reader} compares the continuous flow {flow} against a "
                f"threshold ({operand}) and drives a discrete signal from it, "
                f"and that signal reaches a component producing the very "
                f"{flow} it reads. A comparison on a RATE is algebraic: no "
                "integrated state stands between the two, so the regimes on "
                "either side of the threshold select each other within one "
                "instant and the model chatters instead of settling. A "
                "deadband does not damp it either -- a rate JUMPS across the "
                "band instead of moving through it, crossing both edges at "
                "once. Gate production on a quantity through a sensor reading "
                "a CAPACITY LEVEL over a measurement link: a level is "
                "integrated, so it does break the loop."
                + capacity_clause(self.capacities, "command it arrives at")
            ),
        )


class RateObservationLoopError(ContinuousFlowCycleError):
    """A signal thresholded on an OBSERVED rate, wired back upstream (R30, R43).

    The sibling of :class:`RateComparisonLoopError`, on the path that reaches
    the rate through a measurement link instead of through transport. The
    offence is the same and so is the physics; what differs is that neither the
    flow graph nor the walk indexed on flows can see any part of it, which is
    why it is a class of its own and not a second message of that one.

    A modeller reaching this has almost always done the right thing in the
    wrong place: the montage is the sensor pattern of F4/AE18, with the
    threshold moved from the level of a buffer onto the rate that fills it. The
    message therefore names the reading, what published it, and the way out.

    Kept a :class:`ContinuousFlowCycleError`, so a caller already catching a
    first-run cycle catches this one too.
    """

    #: The physics the two observation refusals share, and the way out of both.
    #: Held once rather than written twice: a modeller reaching either has the
    #: same thing to fix, and a wording that drifted between them would read as
    #: two different diagnoses of one offence.
    RATE_IS_NOT_STATE = (
        "A DELIVERED RATE is not an integrated state, whatever the measurement "
        "link it arrived on: the allocation sweep recomputes it at every "
        "evaluation, so the regimes on either side of the threshold select "
        "each other within one instant and the model chatters instead of "
        "settling. A deadband does not damp it either -- a rate JUMPS across "
        "the band instead of moving through it, crossing both edges at once. "
        "Observe a CAPACITY LEVEL instead: put a volume between the producer "
        "and this reading and threshold its level, which is integrated and "
        "does break the loop."
    )

    def __init__(
        self, reader, channel, flow, producer, operand, connections, capacities=()
    ):
        #: Capacities whose discharge condition takes part, empty when none
        #: does (R50). Assigned FIRST, and a subclass forwards its own rather
        #: than assigning before ``super()``: this line would clobber it.
        self.capacities = list(capacities)
        #: Component carrying the threshold.
        self.reader = reader
        #: Its measurement channel the threshold reads.
        self.channel = channel
        #: The continuous output flow whose delivered rate that reading is.
        self.flow = flow
        #: The component delivering it.
        self.producer = producer
        #: The threshold, rendered as it was declared.
        self.operand = operand

        cycle = [cnct.source for cnct in connections] + [connections[-1].target]

        super().__init__(cycle, connections)

    def default_message(self) -> str:
        """The refusal, written for a verdict that leaves as a DISCRETE signal.

        Overrides the hook ``ContinuousFlowCycleError.__init__`` already
        dispatches through, rather than a mechanism of its own, so
        :attr:`path` and :attr:`wiring` are populated by the time it runs
        and :class:`CommandedRateLoopError` can say what it alone has to
        say without restating the physics the two share.
        """
        return (
            "Continuous flow graph must be acyclic (R30, R43): "
            f"{self.path} closes a loop through a rate "
            f"observation. Connections closing the loop: {self.wiring}. "
            f"{self.reader} thresholds the reading {self.channel} "
            f"({self.operand}) and drives a discrete signal from it, that "
            f"reading is the rate {self.flow} delivered by {self.producer}, "
            f"and that signal reaches a component producing the very "
            f"{self.flow} it reads. "
            + self.RATE_IS_NOT_STATE
            + capacity_clause(self.capacities, "command it arrives at")
        )


class CommandedRateLoopError(RateObservationLoopError):
    """A RATE commanded by a threshold on its own observation (R30, R47).

    The third shape of one offence, and the one that closes at ZERO HOPS. Its
    two siblings both follow a verdict from where it is decided to where it
    lands: :class:`RateComparisonLoopError` over transport,
    :class:`RateObservationLoopError` over an observation link. Here the
    verdict never leaves as a signal at all -- what the threshold decides IS a
    continuous output, and that output reaches the producer of the very rate
    the threshold reads.

    So there is nothing to walk, and that is exactly why nothing caught it. A
    measurement link is never an edge of the continuous graph (KD19), so the
    graph sees no part of the observation half; and the walk that picks up what
    the graph drops travels on DISCRETE channels, so it sees no part of the
    command half. Widening that walk to both families does not help, measured:
    the continuous output does start coming back, and the verdict still travels
    on nothing.

    The criterion is deliberately tight -- the commanded output must REACH the
    observed producer, not merely exist -- because a component thresholding a
    rate and commanding an output that goes elsewhere closes nothing, and in
    this module refusing wrongly costs more than missing.

    Kept a :class:`RateObservationLoopError`: the reading, the producer and the
    physics are that error's, and a caller catching it must catch this too.
    """

    def __init__(
        self,
        reader,
        channel,
        flow,
        producer,
        commanded,
        operand,
        connections,
        capacities=(),
    ):
        # Assigned BEFORE the base constructor, which is what formats the
        # message: ``default_message`` is dispatched from there and reads it.
        #: The continuous output the threshold commands.
        self.commanded = commanded

        # Forwarded rather than assigned here: the base constructor assigns
        # ``capacities`` too, and would overwrite an assignment made before it.
        super().__init__(
            reader, channel, flow, producer, operand, connections, capacities
        )

    def default_message(self) -> str:
        return (
            "Continuous flow graph must be acyclic (R30, R47): "
            f"{self.path} closes a loop through a commanded rate. "
            f"Connections closing the loop: {self.wiring}. "
            f"{self.reader} thresholds the reading {self.channel} "
            f"({self.operand}) and commands its continuous output "
            f"{self.commanded} from it; that reading is the rate {self.flow} "
            f"delivered by {self.producer}, and {self.commanded} reaches "
            f"{self.producer}. No discrete signal carries the verdict, so "
            "there was nothing to follow and the loop closes at zero hops: "
            "neither the continuous graph, which holds no edge for an "
            "observation link (KD19), nor the walk indexed on discrete "
            "channels could see any part of it. "
            + self.RATE_IS_NOT_STATE
            + capacity_clause(self.capacities, "command")
        )


class CommandedRateSelfLoopError(ContinuousFlowCycleError):
    """A discharge commanded by a reading of the very rate it serves (R50).

    The tightest loop this vocabulary can express, and the one no other
    detector covers. Its two siblings both need a route: R47 reaches the rate
    through an observation link, R30 through transport. Here the condition
    names the OUTPUT FLOW ITSELF, so the loop closes inside one component and
    crosses no connection at all: ``serve_holds`` reads what the production
    sweep is about to write from ``serve_limit``, which ``serve_holds`` decides.

    That is why neither seed saw it. ``compared_continuous_inputs`` filters on
    the component's INPUTS and ``measurement_thresholds`` needs a channel, so a
    condition naming an output was recorded by nobody and the model built, its
    discharge chattering between serving and not serving at the period of the
    integration step.

    Reported with no connection, because there is none: a wiring is exactly
    what this shape does not have.
    """

    def __init__(self, reader, capacity, flow, operand):
        #: Component carrying both the volume and the output it reads.
        self.reader = reader
        #: The volume whose discharge condition closes it.
        self.capacity = capacity
        #: The continuous output the condition reads AND the discharge feeds.
        self.flow = flow
        #: The operand, rendered as it was declared.
        self.operand = operand

        super().__init__(
            [reader],
            [],
            message=(
                "Continuous flow graph must be acyclic (R30, R50): "
                f"{reader} closes a loop inside itself. The capacity "
                f"{capacity!r} commands its discharge on {operand}, and "
                f"{flow} is the output that discharge feeds: what the "
                "condition reads is what it decides, within one instant and "
                "over no connection at all. No wiring closes this loop, which "
                "is why no walk reports it. Command the discharge on something "
                "the volume does not itself produce -- a boolean signal, or a "
                "CAPACITY LEVEL read over a measurement link, a level being "
                "integrated and therefore carried between instants."
            ),
        )


class ControllerSignalCycleError(ContinuousFlowCycleError):
    """A chain of controller republications that closes on itself (R45).

    One controller's value output is another's observation input (R4), so a
    model may hold a chain of them. A chain has exactly one evaluation order,
    upstream first; a chain that comes back to its own start has none, and no
    integer this module could hand out would make it settle.

    Refused rather than ordered arbitrarily, and refused where every other
    first-run model error is: while the order is derived, before a single
    equation is registered. :func:`mark_algebraic_readings` terminates on this
    shape rather than reporting it, deliberately, because refusing it belongs
    here.

    Kept a :class:`ContinuousFlowCycleError`, so a caller already catching a
    first-run refusal catches this one too.
    """

    def __init__(self, cycle, connections):
        super().__init__(
            cycle,
            connections,
            message=(
                "Controller signal graph must be acyclic (R45): "
                f"{' -> '.join(cycle)} closes a loop. "
                "Links closing the loop: "
                f"{', '.join(str(cnct) for cnct in connections) or 'none found'}. "
                "A controller republishes what its observation inputs carry at "
                "the moment its equation runs, so a chain of them settles in "
                "one evaluation only when every controller runs after the one "
                "it reads. A chain that returns to its own start has no such "
                "order: whichever controller ran first would republish the "
                "previous evaluation's value, and the loop would crawl one hop "
                "per evaluation instead of settling. Break the chain, or put "
                "an integrated state on it -- a capacity level read over a "
                "measurement link, which is carried between instants and does "
                "break the loop."
            ),
        )


class ContinuousFlowGraph:
    """The continuous-flow connection graph of a system.

    Nodes are component names and edges are continuous data connections, both
    held in **declaration order** and never in a set: ``TopologicalSorter``
    breaks its own ties by insertion order, so declaration order is what makes
    the derived sequence reproducible from run to run and independent of hash
    randomisation (KTD3).
    """

    def __init__(self):
        #: Component names carrying continuous flows, in declaration order.
        self.nodes = []
        #: Every continuous data connection, in declaration order.
        self.connections = []

    # -- construction --------------------------------------------------

    def add_node(self, name):
        """Insert ``name`` once, keeping first-insertion (declaration) order."""
        if name not in self.nodes:
            self.nodes.append(name)
        return name

    def add_connection(self, source, target, flow, state_broken=False):
        """Record one continuous data connection."""
        cnct = ContinuousConnection(
            source=source, target=target, flow=flow, state_broken=state_broken
        )
        self.connections.append(cnct)
        return cnct

    # -- reading -------------------------------------------------------

    @staticmethod
    def edges_of(connections):
        """``(source, target)`` pairs of ``connections``, deduplicated, in order.

        Two components joined by several continuous flows are one edge of the
        dependency graph but several connections of the model.
        """
        seen = set()
        edges = []
        for cnct in connections:
            pair = (cnct.source, cnct.target)
            if pair not in seen:
                seen.add(pair)
                edges.append(pair)
        return edges

    @property
    def edges(self):
        """Every ``(source, target)`` pair, deduplicated, in declaration order."""
        return self.edges_of(self.connections)

    @property
    def state_broken_connections(self):
        """The connections an integrated level already breaks (R-14).

        Empty for a model holding no capacity, which is what makes the fallback
        of :func:`compute_equation_order` a no-op there.
        """
        return [cnct for cnct in self.connections if cnct.state_broken]

    @property
    def algebraic_connections(self):
        """The connections NO integrated level breaks.

        The complement of :attr:`state_broken_connections`, and what a cyclic
        model is left constrained by once every loop has been torn: a cycle
        surviving among these is an algebraic loop and is refused.
        """
        return [cnct for cnct in self.connections if not cnct.state_broken]

    def connections_between(self, source, target, connections=None):
        """Every connection of ``connections`` wiring ``source`` to ``target``."""
        pool = self.connections if connections is None else connections

        return [
            cnct for cnct in pool if cnct.source == source and cnct.target == target
        ]

    def ancestors(self, node):
        """Every node reaching ``node`` through continuous edges, and ``node``.

        What "upstream of this quantity" means: influencing any of these
        components can change what ``node`` produces. A discrete signal
        derived from a rate and arriving here is what closes an instantaneous
        loop -- see :func:`find_rate_comparison_loops`.
        """
        incoming = {}
        for source, target in self.edges:
            incoming.setdefault(target, []).append(source)

        reached = {node}
        stack = [node]

        while stack:
            for source in incoming.get(stack.pop(), ()):
                if source not in reached:
                    reached.add(source)
                    stack.append(source)

        return reached

    # -- sorting -------------------------------------------------------

    def _build_sorter(self, reverse, connections):
        sorter = graphlib.TopologicalSorter()

        # Nodes first, in declaration order: an isolated continuous component
        # is a node too, and insertion order is the tie-break.
        for node in self.nodes:
            sorter.add(node)

        for source, target in self.edges_of(connections):
            if reverse:
                sorter.add(source, target)
            else:
                sorter.add(target, source)

        return sorter

    def static_order(self, reverse=False, connections=None):
        """A topological order of the graph, ties broken by declaration order.

        Parameters
        ----------
        reverse : bool
            False for the production sweep (a producer before its consumers),
            True for the demand sweep (a consumer before its producers).
        connections : list, optional
            The connections to constrain the order by. Defaults to all of them;
            :func:`compute_equation_order` passes the set minus the ones it has
            torn, so a loop an integrated level breaks stops constraining
            anything (R-14).

        Raises
        ------
        ContinuousFlowCycleError
            When the graph is cyclic, naming the connections that close it.
        """
        connections = self.connections if connections is None else connections

        sorter = self._build_sorter(reverse=reverse, connections=connections)

        try:
            sorter.prepare()
        except graphlib.CycleError as err:
            raise self._cycle_error(err, reverse, connections) from err

        order = []
        while sorter.is_active():
            group = sorter.get_ready()
            order.extend(group)
            sorter.done(*group)

        return order

    def _cycle_error(self, err, reverse, connections):
        """Turn ``graphlib``'s cycle path into an error naming the connections.

        ``CycleError.args[1]`` is the offending path, read along the sorter's
        successor direction -- which is the flow direction for the production
        sweep and its opposite for the demand sweep.

        Only the connections the sort was actually constrained by are named: a
        loop reported over :attr:`algebraic_connections` must point at the
        dependencies nothing integrates, not at the buffered ones that were
        deliberately dropped.
        """
        cycle = list(err.args[1]) if len(err.args) > 1 else []
        pairs = list(zip(cycle, cycle[1:]))
        if reverse:
            cycle = list(reversed(cycle))
            pairs = [(target, source) for source, target in pairs]

        closing = []
        for source, target in pairs:
            closing.extend(self.connections_between(source, target, connections))

        return ContinuousFlowCycleError(cycle, closing)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"nodes={len(self.nodes)}, connections={len(self.connections)})"
        )

    def __str__(self) -> str:
        lines = [f"{self.__class__.__name__}: {', '.join(self.nodes) or 'empty'}"]
        lines.extend(f"  {cnct}" for cnct in self.connections)
        return "\n".join(lines)


def build_continuous_flow_graph(system):
    """Read the continuous-flow graph back from the engine (KTD15).

    Walks each component's message boxes and their connected counterparts,
    keeping only the data channels of continuous outputs.
    """
    graph = ContinuousFlowGraph()

    components = getattr(system, "comp", None) or {}

    by_engine_name = engine_name_index(components)

    for key, comp in components.items():
        if component_is_continuous(comp):
            graph.add_node(key)

    for key, comp in components.items():
        if key not in graph.nodes:
            continue

        cnct_info = comp.get_cnct_info()

        # Driven from the declaration side rather than from ``messageBoxes()``:
        # this is the same predicate as ``continuous_data_channel(..., "out")``,
        # applied in the order the flows were declared.
        for flow_name in comp.flows_continuous_out:
            info = cnct_info.get(f"{flow_name}_out")
            if info is None:
                continue

            for target in info.get("targets", []):
                target_key = by_engine_name.get(target.get("obj"), target.get("obj"))
                target_comp = components.get(target_key)
                if target_comp is None:
                    continue

                if (
                    continuous_data_channel(target_comp, target.get("cnct"), "in")
                    != flow_name
                ):
                    continue

                graph.add_node(target_key)
                graph.add_connection(
                    key,
                    target_key,
                    flow_name,
                    state_broken=capacity_breaks_inbound(target_comp, flow_name),
                )

    return graph


# ----------------------------------------------------------------------
# The loop the graph does not carry (R30)
# ----------------------------------------------------------------------
#
# The graph above is built from continuous data channels only, so a loop that
# leaves a component through a DISCRETE channel is invisible to it. Most such
# loops are legitimate and must stay so: the sensor pattern of AE18 reads a
# capacity LEVEL over a measurement link and drives a control port back to the
# component filling it, and the level is integrated state, which is exactly
# what breaks the loop.
#
# One shape is not. A comparison against a continuous flow VALUE is algebraic:
# the rate a producer exports this instant is a function of the guard it reads
# this instant, with nothing in between. Wire the result of such a comparison
# back to a component producing that rate and the two regimes select each other
# within one instant -- the model does not diverge, it chatters, indefinitely
# and at a period set by the integration step, so a study silently never
# finishes rather than being refused.
#
# A deadband does NOT rescue it, and the reason is worth recording: a deadband
# damps a value that moves CONTINUOUSLY through the band, and a rate does not
# move, it jumps. A source at 10 gated off by a guard falls to 0 in one
# instant, crossing an activation edge at 8 and a release edge at 3 together,
# so the band is never inhabited and the chatter is unchanged -- measured, on
# the model of R-2, at the same flip dates with and without a band.


def prod_cond_operands(flow):
    """``(source, comparison)`` for every operand of a production condition.

    ``source`` is the flow -- or measurement link -- the operand reads, and
    ``comparison`` the ``{"op", "value"}`` mapping when the operand compares a
    quantity, None when it reads a boolean state.
    """
    compare_matrix = getattr(flow, "var_prod_cond_compare", None) or []

    for i, group in enumerate(getattr(flow, "var_prod_cond", None) or []):
        for j, source in enumerate(group):
            yield source, _prod_cond_matrix_entry(compare_matrix, i, j)


def state_var_name(flow):
    """Basename of the variable carrying a flow's own state, or None."""
    var = getattr(flow, "var_fed", None)

    return None if var is None else var.basename()


def serve_cond_operands(capacity):
    """``(source, comparison)`` for every operand of a discharge condition (R50).

    The capacity counterpart of :func:`prod_cond_operands`, over the three
    fields R49 stores a resolved condition in. Held apart from it because a
    capacity is not a flow and spells them differently, and identical in shape
    because the vocabulary is one.
    """
    compare_matrix = getattr(capacity, "serve_cond_compare", None) or []

    for i, group in enumerate(getattr(capacity, "serve_cond", None) or []):
        for j, source in enumerate(group):
            yield source, _prod_cond_matrix_entry(compare_matrix, i, j)


def commanded_discharge_outputs(comp, capacity):
    """The continuous outputs a capacity's discharge command decides (R50).

    What a discharge drives depends on how what it releases leaves the
    component, and all three answers are the ones the production sweep honours:

    * a held flow that is also a continuous **output** is driven directly. On
      the ``out`` side that is the whole of it: the held flows ARE the outputs.
      On the ``in`` side it is the IDENTITY TRANSFER (R31), and leaving it out
      was measured wrong -- a rule-less pass-through buffered on the way in
      produced no seed at all, so the loop it closed went unreported;
    * on the ``in`` side, what the RULES make of the released quantity. The
      same rule :func:`rule_guard_comparison_seeds` follows, for the same
      reason: what carries a verdict onward is everything the consuming set
      produces.

    Returns
    -------
    set of str
        Variable basenames, empty when nothing the capacity commands leaves the
        component.
    """
    flows_out = getattr(comp, "flows_out", None) or {}
    held = capacity.flow_names
    named = rule_named_flows(comp)
    seeds = set()

    def seed(name):
        flow = flows_out.get(name)

        if flow is not None:
            seeds.add(state_var_name(flow) or f"{name}_fed_out")

    def transits(name):
        """True when the IDENTITY TRANSFER carries this held flow out (R31).

        Two preconditions, and both were missing. The output must be
        CONTINUOUS: a component may carry one name on both sides in different
        families, and seeding a discrete status output because a continuous
        input of the same name is buffered makes the walk follow a signal that
        has nothing to do with the discharge -- a wrongful refusal, the error
        this module ranks worst. And the flow must be in the R-16 RESIDUE: a
        held flow a rule set consumes is drawn by that rule, so the same-named
        output receives nothing from a transfer and its taint carries nothing.
        """
        return isinstance(flows_out.get(name), FlowContinuous) and name not in named

    # Walked in DECLARATION order, as everything in this module is (KTD3), even
    # though the answer is a set: what is iterated here decides nothing today
    # and iterating a set would make that an accident rather than a choice.
    for name in held:
        if transits(name):
            seed(name)

    if capacity.side == "out":
        return seeds

    held_set = set(held)

    for rule_set in (getattr(comp, "rule_sets", None) or {}).values():
        if held_set.isdisjoint(rule_set.consumed_flows):
            continue

        # No family test here, deliberately: a ``prod`` map legitimately names
        # a DISCRETE output, and a rule really does decide it.
        for name in rule_set.produced_flows:
            seed(name)

    return seeds


def capacities_reading(comp, source):
    """Names of the capacities whose discharge condition READS ``source`` (R50).

    What a refusal has to name beside the wiring and the operand: a component
    may carry several volumes, and the condition lives on ONE of them. Without
    it a modeller is told which component closes the loop and left to find the
    declaration.
    """
    return [
        capacity.name
        for capacity in (getattr(comp, "capacities", None) or {}).values()
        if any(operand is source for operand, _ in serve_cond_operands(capacity))
        # Reading the signal is not commanding: an INERT volume, releasing into
        # neither an output nor a rule, reads it and decides nothing, and
        # ``gates_production_on`` has already answered False on that clause.
        # Naming it would point the modeller at a declaration with no part in
        # the loop, and contradict this class's own docstring.
        and commanded_discharge_outputs(comp, capacity)
    ]


def capacities_commanding(comp, source, flow_name):
    """Capacities whose condition READS ``source`` and DECIDES ``flow_name``.

    The other end of the same question: :func:`capacities_reading` names the
    volume a loop arrives at, this one the volume a commanded rate leaves.

    **Both halves are needed**, and the first was missing. Deciding the output
    is not enough: a component may threshold the reading in a production
    condition ON THE OUTPUT while an unrelated volume, commanded by something
    else entirely, sits behind that same output. Naming it then sends the
    modeller to a declaration with no part in the loop, while the real command
    goes unnamed.
    """
    flow = (getattr(comp, "flows_out", None) or {}).get(flow_name)

    if flow is None or source is None:
        return []

    state = state_var_name(flow) or f"{flow_name}_fed_out"

    return [
        capacity.name
        for capacity in (getattr(comp, "capacities", None) or {}).values()
        if any(operand is source for operand, _ in serve_cond_operands(capacity))
        and state in commanded_discharge_outputs(comp, capacity)
    ]


def compared_continuous_inputs(comp):
    """The continuous INPUTS this component compares against a threshold.

    ``{flow name: the comparison, rendered as it was declared}``. Both
    directions of the interoperation vocabulary are read, because both are
    algebraic in the same way: a rule guard (R21) and a discrete production
    condition (R22) share one operand shape and one meaning.

    A CONTINUOUS output is read like a discrete one since R44: it carries the
    same production condition, so a comparison written on it thresholds the
    same quantity and closes the same loop. Skipping it was correct exactly as
    long as it could declare no condition at all.

    A comparison reading a MEASUREMENT link is deliberately absent, and what
    that absence means has narrowed (R43). It was written when a measurement
    could only carry a capacity level -- integrated state, which breaks a loop,
    and the sanctioned way to gate production on a quantity (F4, AE18), so it
    must keep building. A measurement may now carry a delivered rate as well
    (R38), which breaks nothing: that half is judged by
    :func:`find_rate_observation_loops`, on a path indexed on readings rather
    than on flows. This one stays about flows, and stays silent about readings.
    """
    compared = {}
    flows_in = getattr(comp, "flows_in", None) or {}

    for rule_set in (getattr(comp, "rule_sets", None) or {}).values():
        for rule in rule_set.rules:
            for operand in rule.cond:
                flow = operand.flow

                if not operand.is_comparison or not isinstance(flow, FlowContinuous):
                    continue

                if flows_in.get(operand.name) is flow:
                    compared.setdefault(operand.name, operand.to_expression())

    for flow in (getattr(comp, "flows_out", None) or {}).values():
        for source, compare in prod_cond_operands(flow):
            name = getattr(source, "name", None)

            if compare is None or not isinstance(source, FlowContinuous):
                continue

            if flows_in.get(name) is source:
                compared.setdefault(
                    name, f"{name} {compare['op']} {compare['value']:g}"
                )

    # A capacity's DISCHARGE condition is a third place the same comparison can
    # live (R49, R50), and a capacity is not a flow, so the two loops above
    # walk past it. Read here rather than in a path of its own: the offence is
    # the comparison, whatever declaration carries it.
    for capacity in (getattr(comp, "capacities", None) or {}).values():
        for source, compare in serve_cond_operands(capacity):
            name = getattr(source, "name", None)

            if compare is None or not isinstance(source, FlowContinuous):
                continue

            if flows_in.get(name) is source:
                compared.setdefault(
                    name, f"{name} {compare['op']} {compare['value']:g}"
                )

    return compared


def signal_driven_outputs(comp, seeds):
    """Discrete outputs of ``comp`` whose state derives from ``seeds``.

    ``seeds`` is a set of variable basenames the derivation starts from, and
    the propagation is a fixpoint over the two ways one variable reaches
    another INSIDE a component:

    * an output of EITHER family whose production condition reads a tainted
      flow;
    * a mode automaton whose transition watches a tainted variable, which
      taints everything that mode clamps.

    The second is what makes a deadband visible. A band is a mode reading the
    two edge outputs and clamping the AVAILABILITY of the port actually wired
    out, so following production conditions alone would stop at the edges and
    miss the very signal that leaves the component.

    The taint PROPAGATES through both families since R44, and only the
    DISCRETE outputs are returned. A continuous output carries a production
    condition too, so a comparison can reach it and travel on to a discrete
    output thresholding it -- a hop that used to break the chain, since the
    continuous output was invisible to the fixpoint. What LEAVES a component as
    a followable signal stays discrete: this walk is indexed on discrete
    channels, the ones the continuous graph drops.

    Why the return stays discrete rather than being widened (R47)
    ------------------------------------------------------------
    A verdict leaving as a RATE is not this walk's business, and the division
    of roles is one of competence rather than of convenience. Widening the
    return was tried and measured: the continuous output does come back, and
    the montage the widening was meant to refuse still builds, because
    :func:`discrete_data_channel` answers None on a continuous flow. The
    verdict travels on nothing, so the walk ends where it began.

    A commanded rate is judged instead by the two things that can judge it.
    Transported, it is the continuous graph's business already: the command leg
    and the observed leg are both edges, and the loop is refused as a plain
    cycle. Observed, there is no path to walk at all -- the commanded output
    either reaches the observed producer or it does not -- which is
    :func:`commanded_rate_wiring`, read by
    :func:`find_rate_observation_loops` over what
    :func:`rate_driven_outputs` returns.

    Keeping the two questions apart is also what holds a contract still: what a
    rule guard or a threshold DRIVES is read by other units and pinned by their
    own tests, and it is not the same question as what a threshold COMMANDS.
    """
    return driven_outputs(comp, seeds, continuous=False)


def rate_driven_outputs(comp, seeds):
    """CONTINUOUS outputs of ``comp`` whose production derives from ``seeds``.

    The other half of :func:`signal_driven_outputs`, over the same fixpoint and
    the same taint: what a threshold COMMANDS rather than what it DRIVES. A
    seeded output is in its own answer, which is the whole of the degenerate
    case -- a rate commanded by a threshold on that very rate is commanded at
    zero hops.
    """
    return driven_outputs(comp, seeds, continuous=True)


def driven_outputs(comp, seeds, *, continuous):
    """Outputs of one family whose state is tainted by ``seeds``.

    ``continuous`` is keyword-only: it selects a family rather than tuning a
    behaviour, and the two named wrappers above are what a caller is meant to
    reach for.
    """
    tainted = tainted_output_states(comp, seeds)

    return outputs_of_family(comp, tainted, continuous=continuous)


def outputs_of_family(comp, tainted, *, continuous):
    """The tainted outputs of one family, from a taint already computed."""
    return [
        name
        for name, flow in (getattr(comp, "flows_out", None) or {}).items()
        if isinstance(flow, FlowContinuous) == continuous
        and (state_var_name(flow) or f"{name}_fed_out") in tainted
    ]


def clamped_production_endpoints(name, flow):
    """The variables a MODE clamps to change what that output produces.

    The mode hop of the fixpoint, and the two families spell it differently: a
    discrete output is switched off through its availability gate, a continuous
    one is SCALED, through the shared ``{flow}_out_rate`` (KD10) or through the
    per-mode ``{mode}_derating_{flow}`` variables (R18).

    The continuous half was missing, and it was not a detail. A rate commanded
    by a DERATING rather than by a production condition never entered the taint,
    so the loop R47 exists for still built when the verdict reached the output
    through a mode: measured on a threshold driving an alarm that a mode watches
    in order to cut the very rate the threshold reads.
    """
    if not isinstance(flow, FlowContinuous):
        return {f"{name}_fed_available_out"}

    endpoints = {flow.rate_var_name()}
    endpoints.update(
        flow.derating_var_name(mode) for mode in getattr(flow, "derating", None) or {}
    )

    return endpoints


def tainted_output_states(comp, seeds):
    """The fixpoint itself: every state basename ``seeds`` reaches inside ``comp``.

    Both families are propagated through and both are returned; the caller
    picks the family it is asking about. See :func:`signal_driven_outputs` for
    what the two hops are and why a deadband needs the second.
    """
    all_flows_out = getattr(comp, "flows_out", None) or {}

    tainted = set(seeds)
    modes = getattr(comp, "mode_signals", None) or {}

    changed = True
    while changed:
        changed = False

        for signals in modes.values():
            if tainted.isdisjoint(signals["conditions"]):
                continue

            for basename in signals["effects"]:
                if basename not in tainted:
                    tainted.add(basename)
                    changed = True

        for name, flow in all_flows_out.items():
            state = state_var_name(flow) or f"{name}_fed_out"

            if state in tainted:
                continue

            reads = {
                state_var_name(source)
                for source, _ in prod_cond_operands(flow)
                if state_var_name(source) is not None
            }

            clamped = clamped_production_endpoints(name, flow)

            if not tainted.isdisjoint(clamped) or not tainted.isdisjoint(reads):
                tainted.add(state)
                changed = True

        # A capacity's discharge condition is the THIRD hop, and it was the
        # one place the vocabulary lived that this fixpoint did not read.
        # Seeding and gating on a capacity is not enough: a signal RELAYED
        # through a discharge -- commanding a volume whose output another
        # condition then thresholds -- was not propagated inside the component,
        # so the walk stopped there and a multi-hop loop went unreported.
        for capacity in (getattr(comp, "capacities", None) or {}).values():
            reads = {
                state_var_name(source)
                for source, _ in serve_cond_operands(capacity)
                if state_var_name(source) is not None
            }

            if tainted.isdisjoint(reads):
                continue

            for state in commanded_discharge_outputs(comp, capacity):
                if state not in tainted:
                    tainted.add(state)
                    changed = True

    return tainted


def rule_guard_comparison_seeds(comp, flow_in):
    """Variables a RULE GUARD's comparison on ``flow_in`` decides (R-18).

    The missing half of the seeding. :func:`compared_continuous_inputs`
    deliberately reads both vocabularies -- a rule guard (R21) and a discrete
    production condition (R22) share one operand shape and one meaning -- but
    the walk was seeded from production conditions alone, so a comparison
    written as a guard produced no seed, no walk and therefore no loop report,
    however plainly the loop closed.

    What a guard decides is which rule of its set runs, so what carries the
    comparison onward is everything that SET produces: its continuous outputs
    as much as any discrete output named in a ``prod`` map. Those states are
    the seeds; :func:`signal_driven_outputs` then follows them through the
    production conditions and the mode automata that read them, which is how a
    guard on a rate reaches the discrete port actually wired out.

    Returns
    -------
    set of str
        Variable basenames, empty when no guard of the component compares
        ``flow_in``.
    """
    seeds = set()

    if flow_in is None:
        return seeds

    flows_out = getattr(comp, "flows_out", None) or {}

    for rule_set in (getattr(comp, "rule_sets", None) or {}).values():
        compares = any(
            operand.is_comparison and operand.flow is flow_in
            for rule in rule_set.rules
            for operand in rule.cond
        )

        if not compares:
            continue

        for name in rule_set.produced_flows:
            flow = flows_out.get(name)

            if flow is None:
                continue

            seeds.add(state_var_name(flow) or f"{name}_fed_out")

    return seeds


def comparison_driven_outputs(comp, flow_name):
    """Outputs of ``comp`` carrying the comparison on ``flow_name``.

    Three ways a component can compare a continuous input against a threshold
    seed the walk: a production condition on a discrete output (R22) or on a
    continuous one (R44), and a rule set's guard (R21,
    :func:`rule_guard_comparison_seeds`).

    A continuous output seeds on the very variable a discrete one does, its own
    ``{flow}_fed_out``: the two families spell that variable alike, and what the
    walk follows is the connection carrying it, not its type.
    """
    flow_in = (getattr(comp, "flows_in", None) or {}).get(flow_name)
    seeds = set()

    for name, flow in (getattr(comp, "flows_out", None) or {}).items():
        if any(
            compare is not None and source is flow_in
            for source, compare in prod_cond_operands(flow)
        ):
            seeds.add(state_var_name(flow) or f"{name}_fed_out")

    seeds |= rule_guard_comparison_seeds(comp, flow_in)

    for capacity in (getattr(comp, "capacities", None) or {}).values():
        if any(
            compare is not None and source is flow_in
            for source, compare in serve_cond_operands(capacity)
        ):
            seeds |= commanded_discharge_outputs(comp, capacity)

    return signal_driven_outputs(comp, seeds) if seeds else []


def inbound_driven_outputs(comp, flow_name):
    """Discrete outputs of ``comp`` deriving from the input ``flow_name``."""
    flow_in = (getattr(comp, "flows_in", None) or {}).get(flow_name)
    state = None if flow_in is None else state_var_name(flow_in)

    return [] if state is None else signal_driven_outputs(comp, {state})


def gates_production_on(comp, flow_name):
    """True when ``comp``'s own production can depend on that discrete input.

    Four ways, and a loop closed through any of them is the same loop:

    * a **rule guard** naming it -- the declared way a boolean signal selects a
      continuous regime (R21);
    * a **production condition on a continuous output** naming it (R44) -- the
      declared way a boolean signal commands a continuous actuator. The gate is
      a factor of the production, so a signal derived from that very production
      closes the loop as surely as a guard does;
    * a **capacity's discharge condition** naming it (R49, R50) -- the declared
      way a boolean signal commands a STOCK. What leaves a volume is not
      production, which is the whole reason that condition exists, and it is
      also why this clause had to be written separately: the loop is identical
      and the declaration carrying it is not;
    * a **mode automaton** watching it, since a mode is what a derating hangs
      on and a derating scales what an output produces.

    The last two were each added with the mechanism they read, and each absence
    was silent in the same worst way: the walk reaches the component, asks
    whether its production depends on the signal, is told no, and the model
    builds and chatters at a period set by the integration step instead of
    being refused. Measured for the discharge clause on one montage written two
    ways, a rate thresholded into a signal wired back to the volume delivering
    it: gated on the OUTPUT it was refused, gated on the CAPACITY it built, so
    moving the gate from one to the other lost the diagnostic without changing
    the model.

    Requiring one of the four is what keeps a legitimate model building: a
    discrete signal that merely happens to travel between two components which
    also exchange a continuous flow closes no loop, and refusing one would be
    worse than missing one.
    """
    flow_in = (getattr(comp, "flows_in", None) or {}).get(flow_name)

    if flow_in is None:
        return False

    for rule_set in (getattr(comp, "rule_sets", None) or {}).values():
        for rule in rule_set.rules:
            if any(operand.flow is flow_in for operand in rule.cond):
                return True

    for flow in (getattr(comp, "flows_out", None) or {}).values():
        if not isinstance(flow, FlowContinuous):
            continue

        if any(source is flow_in for source, _ in prod_cond_operands(flow)):
            return True

    for capacity in (getattr(comp, "capacities", None) or {}).values():
        if not any(source is flow_in for source, _ in serve_cond_operands(capacity)):
            continue

        # Naming the signal is not enough: what the volume releases has to
        # LEAVE, or the command reaches no production and refusing would be the
        # expensive error. The two ends of this route therefore ask one
        # question -- a hand-declared volume releasing into neither an output
        # nor a rule commands nothing, whatever its condition says.
        if commanded_discharge_outputs(comp, capacity):
            return True

    state = state_var_name(flow_in)

    return any(
        state is not None and state in signals["conditions"]
        for signals in (getattr(comp, "mode_signals", None) or {}).values()
    )


def find_rate_comparison_loops(system, graph):
    """Every instantaneous loop closed by a comparison on a continuous rate.

    Reads the same system as :func:`build_continuous_flow_graph`, and the same
    already-acyclic graph, but walks the channels that graph drops: for each
    component comparing a continuous input against a threshold, the discrete
    signal that comparison drives is followed from component to component, and
    the loop is closed when it arrives at a component upstream of the very
    quantity the comparison reads AND that component's production depends on
    it.

    Returns
    -------
    list of RateComparisonLoopError
        One per (reader, compared flow, producer) triple, in declaration order.
        Empty for a model that closes no such loop.
    """
    components = getattr(system, "comp", None) or {}

    by_engine_name = engine_name_index(components)

    cnct_info = {}

    def connections_of(key):
        if key not in cnct_info:
            cnct_info[key] = components[key].get_cnct_info()
        return cnct_info[key]

    loops = []

    for key, comp in components.items():
        for flow_name, operand in compared_continuous_inputs(comp).items():
            outputs = comparison_driven_outputs(comp, flow_name)

            if not outputs:
                continue

            for cnct in graph.connections:
                if cnct.target != key or cnct.flow != flow_name:
                    continue

                path = _walk_signal(
                    key,
                    outputs,
                    graph.ancestors(cnct.source),
                    components,
                    by_engine_name,
                    connections_of,
                )

                if path is not None:
                    # The signal ARRIVES at the last connection's target, and
                    # that is the component whose declaration gates on it.
                    arrival = components.get(path[-1].target)
                    gated = (
                        (getattr(arrival, "flows_in", None) or {}).get(
                            path[-1].inbound or path[-1].flow
                        )
                        if arrival is not None
                        else None
                    )

                    loops.append(
                        RateComparisonLoopError(
                            key,
                            flow_name,
                            operand,
                            [cnct] + path,
                            capacities=(
                                []
                                if gated is None
                                else capacities_reading(arrival, gated)
                            ),
                        )
                    )

    return loops


def _walk_signal(reader, outputs, upstream, components, by_engine_name, connections_of):
    """Follow a discrete signal out of ``reader``, breadth first.

    Returns the connections leading from ``reader`` to the first component in
    ``upstream`` whose production depends on what arrives there, or None when
    the signal never gets back upstream.
    """
    queue = [(reader, name, []) for name in outputs]
    seen = {(reader, name) for name in outputs}

    while queue:
        source_key, flow_name, path = queue.pop(0)
        info = connections_of(source_key).get(f"{flow_name}_out") or {}

        for target in info.get("targets", []):
            target_key = by_engine_name.get(target.get("obj"), target.get("obj"))
            target_comp = components.get(target_key)

            if target_comp is None:
                continue

            inbound = discrete_data_channel(target_comp, target.get("cnct"), "in")

            if inbound is None:
                continue

            walked = path + [
                SignalConnection(source_key, target_key, flow_name, inbound)
            ]

            if target_key in upstream and gates_production_on(target_comp, inbound):
                return walked

            for onward in inbound_driven_outputs(target_comp, inbound):
                if (target_key, onward) in seen:
                    continue

                seen.add((target_key, onward))
                queue.append((target_key, onward, walked))

    return None


# ----------------------------------------------------------------------
# The loop the graph cannot even see (R30, R43)
# ----------------------------------------------------------------------
#
# The walk above is indexed on FLOWS: which continuous inputs a component
# compares, which discrete outputs those comparisons drive. Everything below is
# indexed on READINGS instead, and it exists because two changes made the flow
# indexing blind rather than merely permissive -- see the module docstring.
#
# The mechanism is a marking, and the thing marked is a reading. A reading is
# ALGEBRAIC when what it reports is recomputed within the instant, and
# INTEGRATED when it is a level a differential equation carries between
# instants. Only the first can close an instantaneous loop, and the distinction
# is invisible at the point of use on purpose: an observer must not be able to
# tell a capacity from a republisher (R37), so nothing at the reading end says
# which of the two arrived. What decides is where the reading CAME FROM, which
# is why the mark has to travel from the publisher rather than be read off the
# consumer.
#
# It travels over three hops and no more:
#
#   1. ``{f}_rate_out`` -- a continuous output publishing what it delivers
#      (R38) -- and ``{name}_level_out`` of an instrument republishing one of
#      its own continuous outputs. These are the only two ways a rate becomes a
#      reading, and both are marked at the source;
#   2. a marked publication marks the measurement channels wired to it;
#   3. a marked channel marks whatever republishes it: a published measurement
#      whose ``source`` names it, or a controller VALUE output whose grammar
#      reads it (R42). A hand-written publication -- no ``source``, no ``emit``
#      -- is NOT marked: nothing computes it from the reading, so it carries no
#      algebraic dependency, only whatever a model or a failure mode wrote.
#
# A capacity level marks nothing, and that is the whole of why the sanctioned
# montage of F4/AE18 keeps building: it has the topology of every refused
# montage here and differs only in the state standing in the middle.


#: Where a rate comes from: the component DELIVERING it, and its output flow.
#: What the mark carries, and what :meth:`ContinuousFlowGraph.ancestors` is then
#: asked about.
RateOrigin = typing.Tuple[str, str]

#: A publication box: the component holding it, and the box name.
Publication = typing.Tuple[str, str]

#: A reading: the component doing the reading, and its measurement channel.
Reading = typing.Tuple[str, str]

#: The observation links a rate travelled from its producer to one reading.
ObservationPath = typing.List[ObservationConnection]


def measurement_channels(comp):
    """The measurement channels ``comp`` READS, whatever class it is.

    One name for a collection two unrelated classes carry: ``ObjFlow`` holds
    the channels declared by ``add_measurement_in``, ``ObjCtrl`` exposes its
    observation inputs under the same property precisely so that the rest of
    the library never has to ask which of the two it is holding.
    """
    return getattr(comp, "measurements_in", None) or {}


def published_measurements(comp):
    """The measurement channels ``comp`` PUBLISHES, whatever class it is."""
    return getattr(comp, "measurements_out", None) or {}


def publication_box(name):
    """Name of the box a published measurement exports on.

    Derived here rather than asked of the channel, exactly as
    :meth:`muscadet.ObjCtrl.add_control_out` derives it: a published
    measurement has no ``box_name``, its box being the one a capacity already
    published under so that an observer cannot tell the two apart.
    """
    return f"{name}_level_out"


def algebraic_publications(components):
    """Every publication box carrying a RATE, with what produces it.

    ``{(component key, box name): {(producer key, flow name)}}`` -- the seeds
    of the marking, and the only two shapes a rate is published under:

    * ``{f}_rate_out``, the observation box every continuous output carries
      beside its transport box (R38). The producer is the component itself;
    * ``{name}_level_out`` of a published measurement whose ``source`` names a
      continuous output OF THE SAME COMPONENT -- an instrument reporting the
      rate it delivers. The producer is again the component itself, and the
      flow is what the source names.

    A publication whose source is a capacity is deliberately absent: a level is
    integrated state, so it breaks a loop rather than carrying one. So is a
    publication whose source is another measurement channel, which is not a
    seed but a hop -- :func:`find_rate_observation_loops` reaches it once it
    knows what that channel reads.

    **The precedence of the three is the one that resolves the source**, not the
    one that reads best here. ``MeasurementOut.resolve_source`` tries capacities,
    then measurement channels, then continuous outputs, and one component may
    legitimately name a volume and the flow filling it alike -- a tank named
    ``q`` holding ``q``. Testing membership of the outputs alone would then mark
    a republished LEVEL as a rate and refuse a model the library sanctions.
    """
    seeds: typing.Dict[Publication, typing.Set[RateOrigin]] = {}

    for key, comp in components.items():
        outputs = getattr(comp, "flows_continuous_out", None) or {}

        for flow_name in outputs:
            seeds[(key, rate_observation_box(flow_name, "out"))] = {(key, flow_name)}

        for name, published in published_measurements(comp).items():
            source = published_measurement_rate(comp, published, outputs)

            if source is not None:
                seeds[(key, publication_box(name))] = {(key, source)}

    return seeds


def published_measurement_rate(comp, published, outputs):
    """The continuous output ``published`` republishes the rate of, or None.

    The resolution of :meth:`muscadet.MeasurementOut.resolve_source`, reduced to
    the one question this module asks and answered in the SAME order, so the two
    cannot disagree about a name that designates two things.
    """
    source = getattr(published, "source", None)

    if source is None:
        return None

    if source in (getattr(comp, "capacities", None) or {}):
        return None

    if source in measurement_channels(comp):
        return None

    return source if source in outputs else None


def republished_channels(comp, channel_name):
    """Boxes on which ``comp`` republishes what it reads on ``channel_name``.

    The two vocabularies a republication is written in, and the reason they
    cannot be read the same way: an ``ObjFlow`` instrument names its source in
    ``MeasurementOut.source``, while a controller's VALUE output names its
    inputs inside the ``emit`` grammar (R42) and leaves ``source`` unset --
    what refreshes it is ``compute_controls`` walking that tree.

    An output computing nothing -- no ``source``, no ``emit`` -- is absent from
    the result: its value is written by hand, so it carries no dependency on
    any reading whatever the two are wired to.
    """
    emit = getattr(comp, "controls_emit", None)
    capacities = getattr(comp, "capacities", None) or {}
    boxes = []

    for name, published in published_measurements(comp).items():
        if emit is not None:
            node = emit.get(name)

            if node is not None and channel_name in node.inputs_read():
                boxes.append(publication_box(name))

            continue

        source = getattr(published, "source", None)

        # A capacity of the same name WINS the resolution (see
        # ``published_measurement_rate``): this publication then carries a
        # level, and following it would propagate a rate that never arrived.
        if source == channel_name and source not in capacities:
            boxes.append(publication_box(name))

    return boxes


def measurement_thresholds(comp, channel_name):
    """How a production condition of ``comp`` thresholds a reading.

    The measurement half of :func:`compared_continuous_inputs`, which reads the
    same operand shape over continuous inputs. A rule guard is deliberately
    absent and cannot be added: ``ObjFlow._resolve_rule_flow`` refuses a
    measurement name in a guard outright (R29), so a production condition is
    the only vocabulary an ``ObjFlow`` can threshold a reading in -- on either
    family of output since R44, and this is the reading a continuous graph can
    never carry an edge for, so nothing else would catch the loop.

    Returns
    -------
    list of str
        One rendering per comparison, in declaration order, empty when this
        component thresholds nothing on that channel.
    """
    channel = measurement_channels(comp).get(channel_name)

    if channel is None:
        return []

    found = []

    for flow in (getattr(comp, "flows_out", None) or {}).values():
        for source, compare in prod_cond_operands(flow):
            if compare is not None and source is channel:
                found.append(f"{channel_name} {compare['op']} {compare['value']:g}")

    # A capacity's DISCHARGE condition thresholds a reading in the same
    # vocabulary (R49, R50), and a capacity is not a flow, so the loop above
    # walks past it. Read here for the same reason the seeds read it: the
    # offence is the comparison, whatever declaration carries it.
    for capacity in (getattr(comp, "capacities", None) or {}).values():
        for source, compare in serve_cond_operands(capacity):
            if compare is not None and source is channel:
                found.append(f"{channel_name} {compare['op']} {compare['value']:g}")

    return found


def ctrl_node_thresholds(node, input_name):
    """How a controller's output grammar thresholds one observation input (R42).

    Every LEAF of the tree reading ``input_name``, rendered the way it was
    declared, so a refusal names the number a modeller wrote rather than the
    operator that carries it. Read through the grammar's own accessors
    (``operand_nodes``, ``inputs_read``) and never by class, which is what
    keeps this module free of an import of the controller unit.

    The DECLARED number, deliberately, and since R44 that is the initial value
    of a variable an instance may have been tuned away from and a failure mode
    may move. What this renders is what somebody wrote, which is what a message
    about a loop in a declaration has to name; the loop itself does not depend
    on the number.
    """
    operands = node.operand_nodes()

    if operands:
        found = []
        for operand in operands:
            found.extend(ctrl_node_thresholds(operand, input_name))
        return found

    if input_name not in node.inputs_read():
        return []

    operator = getattr(node, "operator", None)

    if operator is not None:
        return [f"{input_name} {operator} {float(node.threshold):g}"]

    direction = getattr(node, "direction", None)

    if direction is not None:
        return [
            f"{input_name} {direction} {float(node.activate):g}, "
            f"releasing at {float(node.release):g}"
        ]

    return [f"{node.op} of {input_name}"]


def reading_driven_signals(comp, channel_name):
    """The discrete signals ``comp`` derives from the reading ``channel_name``.

    The one place the two component families meet, and they meet by union
    rather than by dispatch: a component answers whichever of the two questions
    it can, and a class that carries neither answers nothing.

    * an ``ObjFlow`` thresholds the reading in a discrete production condition,
      and :func:`signal_driven_outputs` then follows that output through the
      production conditions and the mode automata reading it -- which is what
      makes a deadband built out of two edge outputs visible;
    * a controller's BOOLEAN output names the input in its ``emit`` grammar.
      Nothing is followed onward there: a controller output is a leaf of the
      component, and its grammar reads observation inputs only.

    Returns
    -------
    tuple
        ``(discrete output names, continuous output names, thresholds
        rendered)``. All three empty when this component derives nothing from
        that reading. The two families come out of ONE run of the fixpoint,
        which is what :func:`tainted_output_states` was split out for: they are
        two readings of one propagation, not two propagations.
    """
    thresholds = measurement_thresholds(comp, channel_name)
    outputs = []
    rates = []

    if thresholds:
        tainted = tainted_output_states(
            comp, measurement_threshold_seeds(comp, channel_name)
        )
        outputs = outputs_of_family(comp, tainted, continuous=False)
        rates = outputs_of_family(comp, tainted, continuous=True)

    emit = getattr(comp, "controls_emit", None)
    published = published_measurements(comp)

    for name in getattr(comp, "controls_out", None) or {}:
        # A VALUE output is a republication, handled as a hop by
        # ``republished_channels``: what closes a loop is a SIGNAL.
        if name in published:
            continue

        node = None if emit is None else emit.get(name)

        if node is None or channel_name not in node.inputs_read():
            continue

        outputs.append(name)
        thresholds.extend(ctrl_node_thresholds(node, channel_name))

    return outputs, rates, thresholds


def measurement_threshold_seeds(comp, channel_name):
    """States a threshold of ``comp`` on that reading decides, either family.

    The seeding half, held apart from the two walks reading it so that asking
    what a threshold DRIVES and what it COMMANDS starts from one place and one
    reading of the declaration.
    """
    channel = measurement_channels(comp).get(channel_name)
    seeds = set()

    for name, flow in (getattr(comp, "flows_out", None) or {}).items():
        if any(
            compare is not None and source is channel
            for source, compare in prod_cond_operands(flow)
        ):
            seeds.add(state_var_name(flow) or f"{name}_fed_out")

    for capacity in (getattr(comp, "capacities", None) or {}).values():
        if any(
            compare is not None and source is channel
            for source, compare in serve_cond_operands(capacity)
        ):
            seeds |= commanded_discharge_outputs(comp, capacity)

    return seeds


def measurement_driven_outputs(comp, channel_name):
    """Discrete outputs of ``comp`` carrying a threshold on that reading.

    The measurement counterpart of :func:`comparison_driven_outputs`, seeded
    the same way, over both families since R44, and followed by the same
    fixpoint.
    """
    seeds = measurement_threshold_seeds(comp, channel_name)

    return signal_driven_outputs(comp, seeds) if seeds else []


def measurement_driven_rates(comp, channel_name):
    """CONTINUOUS outputs of ``comp`` a threshold on that reading commands (R47).

    The command counterpart of :func:`measurement_driven_outputs`, on the same
    seeds. What it returns is not followed anywhere: a rate leaving a component
    is carried by the continuous graph, so the only question left is whether it
    reaches the producer of the observed rate (:func:`commanded_rate_wiring`).
    """
    seeds = measurement_threshold_seeds(comp, channel_name)

    return rate_driven_outputs(comp, seeds) if seeds else []


def channel_behind_box(comp, box_name):
    """The measurement channel of ``comp`` importing on ``box_name``, or None.

    The observation counterpart of :func:`continuous_data_channel`: it resolves
    a box back to the interface behind it rather than parsing the name, because
    the two natures of a measurement link spell their box differently (R38) and
    a controller and an ``ObjFlow`` hold their channels in different attributes.
    """
    for name, channel in measurement_channels(comp).items():
        if channel.box_name() == box_name:
            return name

    return None


def mark_algebraic_readings(components, by_engine_name, connections_of):
    """Which readings carry a rate, where it came from, and how it got there.

    The marking described at the head of this section, run to a fixpoint,
    breadth first so that the wiring a reading is REPORTED with is the shortest
    chain that brought the rate to it.

    **Walked from the OBSERVERS, not from the producers**, and that is a cost
    decision rather than a stylistic one. Every continuous output publishes a
    rate box (R38), so seeding from the publishing side would ask the engine for
    the connections of every producer in the model -- thousands of them on a
    model that holds a handful of instruments. Asking each instrument who
    publishes into it walks the small collection instead, and
    :func:`algebraic_publications` answers the "is this box a rate" half without
    touching the engine at all.

    Returns
    -------
    dict
        ``{(component key, channel name): {(producer, flow): [links]}}`` where
        ``links`` is the observation path from the producer to that reading. A
        reading absent from the mapping carries no rate: it reads an integrated
        level, or a value nothing computes, or nothing at all.
    """
    marked: typing.Dict[Reading, typing.Dict[RateOrigin, ObservationPath]] = {}

    # ``(component, its marked channel, what is new on it, how it got there)``.
    # Deduplication happens on the READINGS, in ``mark`` below, and that is what
    # bounds the walk: the set of (component, channel, origin) triples is finite
    # and each is entered once, so a chain of republishers that circles back
    # terminates instead of looping. Refusing such a circle is the ordering of
    # controllers' business, not this one's.
    queue: typing.List[
        typing.Tuple[str, str, typing.Set[RateOrigin], ObservationPath]
    ] = []

    def mark(obs_key, channel_name, origins, path):
        """Record what is NEW on one reading, and queue it for propagation."""
        reached = marked.setdefault((obs_key, channel_name), {})
        fresh = {origin for origin in origins if origin not in reached}

        if not fresh:
            return

        reached.update({origin: path for origin in fresh})
        queue.append((obs_key, channel_name, fresh, path))

    def counterparts(key, box):
        """``(component key, its box)`` for everything wired onto ``key.box``."""
        info = connections_of(key).get(box) or {}

        for target in info.get("targets", []):
            other = by_engine_name.get(target.get("obj"), target.get("obj"))

            if other in components:
                yield other, target.get("cnct")

    readers = [
        (key, comp, name, channel)
        for key, comp in components.items()
        for name, channel in measurement_channels(comp).items()
    ]

    # Nothing observes anything: no reading exists to carry a rate, and the
    # engine is not walked at all.
    if not readers:
        return marked

    seeds = algebraic_publications(components)

    # First hop: what publishes into each reading, and whether that publication
    # is a rate.
    for obs_key, obs_comp, channel_name, channel in readers:
        box = channel.box_name()

        for pub_key, pub_box in counterparts(obs_key, box):
            origins = seeds.get((pub_key, pub_box))

            if origins:
                mark(
                    obs_key,
                    channel_name,
                    origins,
                    [
                        ObservationConnection(
                            pub_key, obs_key, channel_name, pub_box, box
                        )
                    ],
                )

    # Republication hops: one component's marked reading, published onward and
    # read by the next. The connections of a republisher are already in the
    # cache -- it had to read a measurement to be here.
    while queue:
        pub_key, channel_name, origins, path = queue.pop(0)

        for box in republished_channels(components[pub_key], channel_name):
            for obs_key, obs_box in counterparts(pub_key, box):
                onward = channel_behind_box(components[obs_key], obs_box)

                if onward is None:
                    continue

                mark(
                    obs_key,
                    onward,
                    origins,
                    path
                    + [ObservationConnection(pub_key, obs_key, onward, box, obs_box)],
                )

    return marked


def continuous_input_feeds(comp, flow_in, flow_out):
    """True when what arrives on ``flow_in`` can change what leaves on ``flow_out``.

    The arrival-end predicate, and the exact counterpart of
    :func:`gates_production_on` on the other walk: reaching a component is not
    closing a loop, what closes it is that the component's production depends
    on what arrived. Structural like every test in this module, so it asks
    whether a dependency is DECLARED and never what it is currently worth.

    Three ways a declaration creates one, and a loop through any of them is the
    same loop:

    * a **rule set** consuming ``flow_in`` and producing ``flow_out``. Two
      independent sets on one component do not couple, which is the case this
      predicate exists for: a plant burning ``fuel`` into ``z`` while making
      ``q`` out of something else does not make ``q`` depend on ``fuel``;
    * an **identity transfer** (R31), where the two are one flow transiting the
      component;
    * a **transfer pair** naming both, which moves a quantity between them.

    Without this, an unqualified "the command lands on an ancestor" test
    refuses a model whose commanded output provably cannot move the observed
    rate -- measured on an input no rule consumes at all, and on a producer
    running two independent rule sets. In a module where refusing wrongly costs
    more than missing, that is the outcome to avoid first.
    """
    if flow_in == flow_out:
        return True

    for rule_set in (getattr(comp, "rule_sets", None) or {}).values():
        if flow_in in rule_set.consumed_flows and flow_out in rule_set.produced_flows:
            return True

    for pair in (getattr(comp, "transfers", None) or {}).values():
        flows = getattr(pair, "flows", None) or []

        if flow_in in flows and flow_out in flows:
            return True

    return False


def commanded_rate_wiring(graph, components, reader, commanded, producer, flow):
    """How ``reader``'s commanded output reaches the observed rate, or None (R47).

    The whole of the zero-hop recognition, and the tight half of its criterion.
    A rate leaving a component is carried by the continuous graph, so there is
    no signal to follow here: either the commanded output can change what
    ``producer`` delivers on ``flow``, or it cannot.

    Two ways it can, and the first is the degenerate one the defect was
    measured on:

    * the commanded output IS the observed one, so the value of this instant
      decides the value of this instant. **Zero hops**, and no connection
      closes it that the observation path has not already named -- hence the
      empty list, which is a found loop and not a missing one;
    * it is **delivered straight into the producer**, onto an input that
      producer's declaration turns into the observed output
      (:func:`continuous_input_feeds`), over a connection no capacity breaks.
      One hop: the supply the observed rate is made of.

    Three things it refuses to conclude, and each is deliberate.

    **Reaching an ANCESTOR of the producer is not enough**, though ``ancestors``
    is transitively closed and the arithmetic would work. What is missing is
    the arrival-end test at every intermediate node: the quantity has to keep
    being passed on, hop after hop, and this module holds no walk that checks
    it. Asserting the chain on the strength of its first edge alone refuses
    models that close nothing, so the second way stops at the producer and a
    longer chain is a MISS. That is the cheap error here, and the honest one.

    **A state-broken connection carries nothing within the instant.** A
    capacity on the producer's input side integrates what arrives before any
    rule reads it (R-14, :func:`capacity_breaks_inbound`), so the commanded
    rate of this instant cannot move the observed rate of this instant. The
    error's own message advises putting a volume in the way; refusing a model
    that already has one would be answering a question with itself.

    **A SIBLING output of the same rule set is not a third way**, which is what
    makes the first way ask for the commanded output to BE the observed one.
    Gating one output of a two-output rule leaves the other untouched, because
    ``get_uptake_factor`` is the MAXIMUM over the rule's outputs and not their
    minimum (R-13). Measured on a rule making ``q`` and ``r`` out of ``a`` at
    8.0: cutting ``r`` to zero leaves ``q`` at 8.0 and the draw at 8.0, and
    only cutting BOTH takes the three to zero, where the observed output is
    gated as well and the first way already holds.

    Returns
    -------
    list or None
        The connections closing the loop, empty at zero hops; None when the
        commanded output does not reach the observed rate.
    """
    if reader == producer and commanded == flow:
        return []

    target = components.get(producer)

    if target is None or not continuous_input_feeds(target, commanded, flow):
        return None

    for cnct in graph.connections:
        if (
            cnct.source == reader
            and cnct.flow == commanded
            and cnct.target == producer
            and not cnct.state_broken
        ):
            return [cnct]

    return None


def find_self_commanded_discharges(system):
    """Every discharge commanded by a reading of a rate it itself serves (R50).

    Needs no graph and no walk: the loop closes inside one component, so what
    is asked is whether a discharge condition names an OUTPUT that same
    discharge feeds. Both operand shapes count -- a comparison and a bare
    boolean read alike -- because the offence is reading the rate you serve,
    not the way the reading is written.

    Returns
    -------
    list of CommandedRateSelfLoopError
        In declaration order, empty for a model closing no such loop.
    """
    loops = []

    for key, comp in (getattr(system, "comp", None) or {}).items():
        flows_out = getattr(comp, "flows_out", None) or {}

        for capacity in (getattr(comp, "capacities", None) or {}).values():
            driven = commanded_discharge_outputs(comp, capacity)

            for source, compare in serve_cond_operands(capacity):
                name = getattr(source, "name", None)

                if flows_out.get(name) is not source:
                    continue

                state = state_var_name(source) or f"{name}_fed_out"

                if state not in driven:
                    continue

                operand = (
                    f"{name} {compare['op']} {compare['value']:g}"
                    if compare is not None
                    else name
                )

                loops.append(
                    CommandedRateSelfLoopError(key, capacity.name, name, operand)
                )

    return loops


def find_rate_observation_loops(system, graph):
    """Every instantaneous loop closed by a threshold on an OBSERVED rate (R43).

    Known gap, measured rather than suspected: two sibling sources feeding one
    consumer, with a controller observing one and driving the other, is accepted.
    The loop exists, since the rate published by the first depends on what the
    second produces once the allocation pass lowers it. Neither detector can see
    it: both close on "upstream BY TRANSPORT", and sibling coupling runs sideways
    through a shared demand without ever traversing an edge. Closing it means
    widening ``ContinuousFlowGraph.ancestors``, which also widens the comparison
    path and would newly refuse the common main-plus-backup shape. In a module
    where refusing wrongly costs more than missing, that is its own change.

    Reads the same system as :func:`build_continuous_flow_graph` and the same
    already-acyclic graph, but takes its edges from the raw wiring of the
    measurement boxes instead of from the flow collections -- which is what
    lets a controller, a component the graph holds no node for, take part in a
    loop the graph is nonetheless the judge of.

    The graph is only ever READ, through :meth:`ContinuousFlowGraph.ancestors`:
    an observation link must never become an edge of it (KD19).

    What this does NOT catch, and it is worth knowing which is which
    ---------------------------------------------------------------
    * a reading a model WRITES by hand -- ``MeasurementOut.publish`` called from
      a sensitive method, a test, a failure mode -- carries no declared
      dependency on anything, so nothing here can know it came from a rate.
      Only the two declared republications, ``source`` and ``emit``, travel;
    * the signal walk stops where :func:`_walk_signal` stops, which is at a
      channel :func:`discrete_data_channel` does not recognise. A signal routed
      through an :class:`muscadet.ObjLogicGate` is the case that matters: its
      ``{f}_out`` is exported by no flow object at all, so the walk ends there.
      That limit is shared with :func:`find_rate_comparison_loops` and predates
      this path;
    * a loop between CONTROLLERS closes nothing here on purpose: the marking
      terminates on it rather than reporting it, because the order of
      controllers among themselves is a unit of its own.

    Where it over-approximates
    --------------------------
    ``ancestors`` is read over the WHOLE edge set, torn edges included, exactly
    as :func:`find_rate_comparison_loops` reads it. A producer standing behind a
    capacity is therefore still an ancestor of what that capacity serves, even
    though the level makes the downstream rate independent of it within the
    instant -- so a threshold on a rate delivered past a buffer, driven back
    onto the source filling it, is refused although the volume breaks it. Both
    paths inherit that from R-14, and they inherit it TOGETHER on purpose: two
    detectors disagreeing about what "upstream" means would refuse a model or
    not according to which of them looked first, which is a worse defect than
    the over-approximation itself. Tightening it is one change, in
    :meth:`ContinuousFlowGraph.ancestors`, for both.

    The verdict does not have to LEAVE (R47)
    ----------------------------------------
    A threshold on a marked reading is followed two ways, because a verdict can
    be carried two ways. As a **discrete signal**, walked by
    :func:`_walk_signal` until it lands on an ancestor of the producer, which
    is the R43 path above. Or as the **rate itself**, when what the threshold
    gates is a continuous output (R44), or a mode derating one: there is then
    nothing to walk, and what is left to ask is whether that output reaches the
    observed producer
    (:func:`commanded_rate_wiring`), and whether the producer's own declaration
    turns what arrives into the observed rate
    (:func:`continuous_input_feeds`). EVERY commanded-rate loop of the model is
    reported after every signal one, and not merely after the ones closing on
    the same producer, so a montage closing both keeps the diagnostic naming
    its signal.

    Returns
    -------
    list of RateObservationLoopError
        One per (reader, channel, producer, verdict) tuple, in declaration
        order, the commanded-rate ones being :class:`CommandedRateLoopError`.
        Empty for a model that closes no such loop.
    """
    components = getattr(system, "comp", None) or {}

    by_engine_name = engine_name_index(components)

    cnct_info = {}

    def connections_of(key):
        if key not in cnct_info:
            cnct_info[key] = components[key].get_cnct_info()
        return cnct_info[key]

    marked = mark_algebraic_readings(components, by_engine_name, connections_of)

    # The two verdicts are collected apart and concatenated at the end, so the
    # precedence below holds over the WHOLE model and not merely within one
    # producer: ``compute_equation_order`` raises the first, and a loop a signal
    # can be followed through keeps the message written for it.
    signal_loops = []
    commanded_loops = []

    # ``ancestors`` rebuilds its incoming map on every call, so it is asked once
    # per producer rather than once per (producer, flow) pair.
    upstream_of = {}

    for key, comp in components.items():
        for channel_name in measurement_channels(comp):
            reached = marked.get((key, channel_name))

            if not reached:
                continue

            outputs, commanded, thresholds = reading_driven_signals(comp, channel_name)

            if not outputs and not commanded:
                continue

            operand = ", ".join(thresholds) or channel_name

            for (producer, flow_name), path in reached.items():
                if outputs:
                    if producer not in upstream_of:
                        upstream_of[producer] = graph.ancestors(producer)

                    walked = _walk_signal(
                        key,
                        outputs,
                        upstream_of[producer],
                        components,
                        by_engine_name,
                        connections_of,
                    )

                    if walked is not None:
                        arrival = components.get(walked[-1].target)
                        gated = (
                            (getattr(arrival, "flows_in", None) or {}).get(
                                walked[-1].inbound or walked[-1].flow
                            )
                            if arrival is not None
                            else None
                        )

                        signal_loops.append(
                            RateObservationLoopError(
                                key,
                                channel_name,
                                flow_name,
                                producer,
                                operand,
                                path + walked,
                                capacities=(
                                    []
                                    if gated is None
                                    else capacities_reading(arrival, gated)
                                ),
                            )
                        )

                for name in commanded:
                    wiring = commanded_rate_wiring(
                        graph, components, key, name, producer, flow_name
                    )

                    if wiring is not None:
                        commanded_loops.append(
                            CommandedRateLoopError(
                                key,
                                channel_name,
                                flow_name,
                                producer,
                                name,
                                operand,
                                path + wiring,
                                capacities=capacities_commanding(
                                    comp,
                                    measurement_channels(comp).get(channel_name),
                                    name,
                                ),
                            )
                        )

    return signal_loops + commanded_loops


# ----------------------------------------------------------------------
# The order of the signal nodes among themselves (R45)
# ----------------------------------------------------------------------
#
# A graph of its own, and it has to be: a controller carries no flow, so the
# continuous-flow graph holds no node for it, and neither a controller nor a
# measurement link carries a transported quantity, so none of their links may
# ever become an edge of that graph (KD19). What is walked here is the SIGNAL
# wiring -- who publishes a reading into whose observation channel -- over
# every node whose own equation refreshes a publication, whichever of the two
# classes it belongs to.


def is_controller(comp):
    """True when ``comp`` is an :class:`muscadet.ObjCtrl`, without importing it.

    Asked of the collection a controller and nothing else carries. Duck-typed
    on purpose, exactly as :func:`republished_channels` reads ``controls_emit``:
    an import of the controller unit here would tie the ordering of a model to
    the presence of a class it need not declare.
    """
    return getattr(comp, "controls_out", None) is not None


def computed_publications(comp):
    """The publications of ``comp`` its own equation refreshes (R42, R45).

    **Both vocabularies**, read the way :func:`republished_channels` reads
    them: a controller's VALUE output names its inputs inside the ``emit``
    grammar and leaves ``source`` unset, while an ``ObjFlow`` instrument names
    what it republishes in ``MeasurementOut.source``. The two are one concept
    here -- a reading somebody else's equation has to have settled first -- and
    telling them apart is what kept the ``ObjFlow`` half out of the signal
    graph, and therefore out of its order.

    A publication computing nothing is absent from the result, whichever
    vocabulary it is written in: its value is written by hand, so a reader of
    it depends on the hand that wrote it and not on when any equation ran.

    A source naming a **capacity of the same component** is kept, unlike in
    :func:`republished_channels` where it is dropped. The two ask different
    questions: that one asks what a RATE travelled through, and a level is not
    a rate; this one asks whether an equation refreshes this publication, and
    one does.
    """
    emit = getattr(comp, "controls_emit", None)
    published = published_measurements(comp)

    if emit is not None:
        return [name for name in published if emit.get(name) is not None]

    return [
        name
        for name, channel in published.items()
        if getattr(channel, "source", None) is not None
    ]


def publishes_computed_readings(comp):
    """True when an equation of ``comp`` refreshes at least one publication.

    What makes a component a node of the signal graph other than by being a
    controller: an ``ObjFlow`` republishing a sourced measurement carries an
    equation whose order matters exactly as a controller's does.
    """
    return bool(computed_publications(comp))


def is_signal_node(comp):
    """True when ``comp`` takes a place in the signal order (R45).

    Every controller, republishing or not -- an isolated one is still a node,
    and one carrying only boolean outputs still constrains the components
    around it -- plus every other component whose equation refreshes a
    publication, which is the ``ObjFlow`` instrument.
    """
    return is_controller(comp) or publishes_computed_readings(comp)


def controller_signal_links(system):
    """The signal nodes of ``system``, and the links between them (R45).

    A node is a controller or any other component whose equation refreshes a
    publication, which in practice is an ``ObjFlow`` instrument republishing a
    sourced measurement (:func:`is_signal_node`). **Both belong here for one
    reason**: what the order exists to guarantee is that a reading has settled
    before whoever reads it is evaluated, and that claim is about the
    publication, not about the class publishing it. Keeping the ``ObjFlow``
    half out made "controller, instrument, controller" unorderable, and left a
    chain of two shipped sensors ordered by declaration alone -- so a relay
    declared before the instrument it reads was one evaluation behind it
    forever, and reported its declared default at instant 0.

    The name is kept for the surface it already had.

    Returns
    -------
    tuple
        ``(nodes, links)`` -- the nodes keyed as ``system.comp`` keys them, in
        declaration order, and one :class:`ObservationConnection` per wiring
        from a computed publication to another node's observation channel.
        Both empty on a model holding neither, in which case the engine is not
        walked at all.
    """
    components = getattr(system, "comp", None) or {}

    nodes = {key: comp for key, comp in components.items() if is_signal_node(comp)}

    # The engine is asked nothing on a model that publishes no computed
    # reading, which is every model that predates the measurement link.
    if not nodes:
        return nodes, []

    by_engine_name = engine_name_index(components)

    links: typing.List[ObservationConnection] = []

    for pub_key, pub_comp in nodes.items():
        cnct_info = pub_comp.get_cnct_info()

        for name in computed_publications(pub_comp):
            box = publication_box(name)
            info = cnct_info.get(box) or {}

            for target in info.get("targets", []):
                obs_key = by_engine_name.get(target.get("obj"), target.get("obj"))
                obs_comp = nodes.get(obs_key)

                # An observer that publishes nothing computed has no equation
                # in this band, so there is nothing to order it against: its
                # own behaviour is settled by the sweeps, which run below.
                if obs_comp is None:
                    continue

                obs_box = target.get("cnct")
                channel = channel_behind_box(obs_comp, obs_box)

                if channel is None:
                    continue

                links.append(
                    ObservationConnection(pub_key, obs_key, channel, box, obs_box)
                )

    return nodes, links


def controller_cycle_error(err, links):
    """Turn ``graphlib``'s cycle path into an error naming the closing links.

    ``CycleError.args[1]`` is the offending path read along the sorter's
    successor direction, which here is the direction the signal travels: the
    sorter is built with the publisher as the predecessor of the reader.
    """
    cycle = list(err.args[1]) if len(err.args) > 1 else []

    closing: typing.List[ObservationConnection] = []
    for source, target in zip(cycle, cycle[1:]):
        closing.extend(
            link for link in links if link.source == source and link.target == target
        )

    return ControllerSignalCycleError(cycle, closing)


def compute_controller_order(system):
    """Signal node names in the order their equations must run (R45).

    A topological sort of :func:`controller_signal_links`, ties broken by
    declaration order exactly as the flow graph breaks its own (KTD3): the
    nodes are inserted first, and ``TopologicalSorter`` breaks ties by
    insertion order, so the derived sequence is reproducible from run to run
    and independent of hash randomisation.

    Every node is in the result, including one nothing reads and one that reads
    nothing: an isolated controller is a node too, and so is an instrument
    reading a capacity and nothing else.

    Raises
    ------
    ControllerSignalCycleError
        When a chain of republications closes on itself. Reachable through an
        ``ObjFlow`` instrument since those became nodes: a controller reading
        an instrument that republishes that controller's own output is a cycle
        of two, and it used to build.
    """
    controllers, links = controller_signal_links(system)

    if not controllers:
        return []

    sorter: "graphlib.TopologicalSorter[str]" = graphlib.TopologicalSorter()

    for key in controllers:
        sorter.add(key)

    for link in links:
        sorter.add(link.target, link.source)

    try:
        sorter.prepare()
    except graphlib.CycleError as err:
        raise controller_cycle_error(err, links) from err

    order: typing.List[str] = []
    while sorter.is_active():
        group = sorter.get_ready()
        order.extend(group)
        sorter.done(*group)

    return order


# ----------------------------------------------------------------------
# The derived order
# ----------------------------------------------------------------------


class EquationRegistration(typing.NamedTuple):
    """One equation method registered on the PDMP manager, with its order."""

    comp: str
    method: str
    order: int

    def __str__(self) -> str:
        return f"{self.order}: {self.comp}.{self.method}"


class EquationOrder:
    """The evaluation order derived from one system's connection graph.

    Exposed for inspection so the derived sequence is asserted directly rather
    than inferred from simulation output.
    """

    def __init__(
        self, graph, demand_order, production_order, torn=(), controller_order=()
    ):
        #: The graph the order was derived from.
        self.graph = graph
        #: Component names in reverse-topological order (demand sweep).
        self.demand_order = list(demand_order)
        #: Component names in topological order (production sweep).
        self.production_order = list(production_order)
        #: The connections dropped to break a cycle an integrated level already
        #: breaks (R-14). **Empty for every acyclic model**, which is what makes
        #: the derived order of a model that builds today byte-identical.
        self.torn = list(torn)
        #: Controller names in signal-topological order (R45), publisher before
        #: reader. Every controller of the model is here, including an isolated
        #: one; only those carrying a republication register an equation.
        self.controller_order = list(controller_order)
        #: What this order actually registered, in registration order.
        self.registrations = []

    @property
    def capability_order(self):
        """Component names in topological order (capability sweep, R-20).

        The production order, and the same list object's content by
        construction: a capability travels with the flow, exactly like a
        production, so a producer publishes before its consumers read. Exposed
        under its own name because the three sweeps are three bands and reading
        ``production_order`` for the first of them would hide that.
        """
        return self.production_order

    @property
    def orders(self):
        """``{(component, method): order}`` for what this order registered."""
        return {(reg.comp, reg.method): reg.order for reg in self.registrations}

    def order_of(self, comp_name, method):
        """The integer given to one component's equation, or None."""
        return self.orders.get((comp_name, method))

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"demand={self.demand_order}, production={self.production_order})"
        )

    def __str__(self) -> str:
        lines = [
            f"{self.__class__.__name__}",
            f"  demand     : {' -> '.join(self.demand_order) or 'empty'}",
            f"  production : {' -> '.join(self.production_order) or 'empty'}",
        ]
        lines.extend(f"  {reg}" for reg in self.registrations)
        return "\n".join(lines)


def compute_equation_order(system):
    """Derive the evaluation order of ``system``, registering nothing.

    The **whole** edge set is sorted first, so a model that builds today derives
    exactly the order it derived before: the state-broken edges take part in the
    sort like any other, and no acyclic model's sequence moves.

    A cycle is then torn, one loop at a time (R-14). Each pass drops the
    connections of the *reported* loop that an integrated level already breaks
    (:func:`capacity_breaks_inbound`) and sorts again, so the tear stays minimal
    -- a buffered edge elsewhere in the model keeps constraining the order it
    always did. A tank wired to a recirculation pump and back sorts here.

    A loop with **no** such connection on it is an algebraic loop -- two rates
    depending on each other with nothing integrated between them -- and is
    refused exactly as before, with the connections closing it named.

    Raises
    ------
    ContinuousFlowCycleError
        When the continuous-flow graph carries a loop no integrated state
        breaks (R30), when a comparison on a continuous rate closes an
        instantaneous loop the graph does not carry
        (:class:`RateComparisonLoopError`), or when a threshold on an OBSERVED
        rate closes one the graph cannot even see
        (:class:`RateObservationLoopError`, R43) -- whether its verdict leaves
        as a discrete signal or as the commanded rate itself
        (:class:`CommandedRateLoopError`, R47).
    ControllerSignalCycleError
        When a chain of controller republications closes on itself (R45). A
        subclass of the above, so one ``except`` still covers every first-run
        refusal.
    """
    graph = build_continuous_flow_graph(system)

    connections = list(graph.connections)
    torn = []

    # Bounded by the connection count: every pass that does not return drops at
    # least one connection, and a pass with nothing to drop raises.
    for _ in range(len(graph.connections) + 1):
        try:
            # The forward sweep is what carries the acyclicity check: both
            # sweeps read the same graph, so one check covers both -- but a
            # tear must satisfy them together, hence both inside the try.
            production_order = graph.static_order(
                reverse=False, connections=connections
            )
            demand_order = graph.static_order(reverse=True, connections=connections)
            break
        except ContinuousFlowCycleError as err:
            breaking = [cnct for cnct in err.connections if cnct.state_broken]

            # Nothing integrates anywhere on this loop: the refusal stands, and
            # the error already names the connections closing it.
            if not breaking:
                raise

            dropped = {id(cnct) for cnct in breaking}
            torn.extend(breaking)
            connections = [cnct for cnct in connections if id(cnct) not in dropped]
    else:  # pragma: no cover - unreachable: each pass drops or raises
        raise ContinuousFlowCycleError([], graph.connections)

    # Only once the graph is known sortable: the walk below reads it, and a
    # cycle in the continuous connections is the error to report first.
    loops = find_rate_comparison_loops(system, graph)

    if loops:
        raise loops[0]

    # Second, and in this order: a loop the flow indexing CAN see is reported
    # with the message written for it, so a model refused today keeps its
    # diagnostic word for word even when the second path would also match it.
    observations = find_rate_observation_loops(system, graph)

    if observations:
        raise observations[0]

    # After the two walks, so a model closing a loop one of them CAN see keeps
    # the diagnostic written for it. This one needs neither the graph nor a
    # walk: it refuses a discharge commanded by a reading of the rate it itself
    # serves, which closes inside one component and over no connection (R50).
    self_loops = find_self_commanded_discharges(system)

    if self_loops:
        raise self_loops[0]

    # Last of the three, so a model closing a loop BOTH walks can see keeps the
    # diagnostic written for it: this one refuses the shape they terminate on
    # rather than report -- a chain of controller republications closing on
    # itself (R45).
    controller_order = compute_controller_order(system)

    return EquationOrder(
        graph,
        demand_order,
        production_order,
        torn=torn,
        controller_order=controller_order,
    )


def register_equation_order(system):
    """Derive the order and register every derived equation on the PDMP manager.

    Called once, from the system's pre-run step: every connection exists and no
    equation has run yet.

    Two allocations, and they are gated differently on purpose. The three
    sweeps take their integers only when the continuous-flow graph holds a
    node, so a purely discrete system stays one; the controller band (R45) is
    gated on the PDMP manager instead, so a model of controllers alone -- which
    has a manager but no graph node, a controller carrying no flow -- still has
    its equations ordered.

    A component takes part in a sweep only when it defines that sweep's
    equation method, so this stays correct while the sweeps themselves are
    still being built.
    """
    order = compute_equation_order(system)

    # A purely discrete system must stay byte-identical to what it was before
    # the continuous layer existed -- in particular, no PDMP manager. The
    # controller band below is gated on the manager rather than on the graph,
    # so a model of controllers alone -- which already has one, its
    # republications being explicit variables -- still gets its order.
    if order.graph.nodes:
        # Before any equation: the capability channel is written from inside
        # one, and PyCATSHOO refuses that on a variable its solver does not know
        # about (R-20). Here rather than at declaration time, so a system that
        # never runs still never gains a PDMP manager.
        for comp_name in order.graph.nodes:
            register_capability_variables(system, system.comp[comp_name])

        allocate = _order_allocator(system)

        for method, sequence in (
            (CAPABILITY_EQUATION_METHOD, order.capability_order),
            (DEMAND_EQUATION_METHOD, order.demand_order),
            (PRODUCTION_EQUATION_METHOD, order.production_order),
        ):
            for comp_name in sequence:
                comp = system.comp[comp_name]
                if not callable(getattr(comp, method, None)):
                    continue

                value = allocate()
                system.pdmp_add_equation_method(method, comp, value)
                order.registrations.append(
                    EquationRegistration(comp=comp_name, method=method, order=value)
                )

    register_controller_equations(system, order)
    register_controller_seeds(system, order)

    return order


#: What each signal node registers, in the order the pair is tried: the
#: predicate asking whether this node still owes an equation, the method that
#: registers it, and the name that equation is recorded under.
SIGNAL_EQUATIONS = (
    ("needs_control_equation", "register_control_equation", CONTROL_EQUATION_METHOD),
    (
        "needs_measurement_equation",
        "register_measurement_equation",
        MEASUREMENT_EQUATION_METHOD,
    ),
)


def register_controller_equations(system, order):
    """Register every signal node's equation in the control band (R45).

    Called once the sweeps have taken their integers, so the allocator sees
    them: "distinct" holds across the whole system and not only within a band.

    **One band for the two equations**, and that is the whole of what makes a
    mixed chain settle. A published reading used to take its integer from a
    band of its own, BELOW this one and allocated in declaration order at
    declaration time, so an instrument reading a controller was refreshed
    before it whatever the graph said, and two instruments in a row were
    ordered by the order they were written in. Sharing one topologically
    sorted band removes both: what the sort answers is "this reading has
    settled", and that question does not depend on the class answering it.

    A node carrying no equation registers nothing and takes no integer -- a
    controller with only boolean outputs, an instrument publishing nothing
    sourced -- but it is still a node of the signal graph, so it still
    constrains the nodes around it.

    **Gated on the PDMP manager**, exactly as
    :meth:`muscadet.System.register_controller_crossings` is, and for the same
    reason: a purely discrete system must stay one, and creating a manager here
    would drag it onto the continuous solver for no gain. A node that
    republishes has already created one at declaration, its published variables
    being explicit variables of the solver.

    Returns
    -------
    list
        The nodes whose equation was registered, in registration order.
    """
    if not order.controller_order or getattr(system, "pdmp_manager", None) is None:
        return []

    allocate = _order_allocator(
        system, start=CONTROL_ORDER_BASE, ceiling=None, band="signal band"
    )

    registered = []

    for comp_name in order.controller_order:
        comp = system.comp[comp_name]

        for needs_name, register_name, method in SIGNAL_EQUATIONS:
            needs = getattr(comp, needs_name, None)

            if needs is None or not needs():
                continue

            value = allocate()
            getattr(comp, register_name)(system, value)
            order.registrations.append(
                EquationRegistration(comp=comp_name, method=method, order=value)
            )
            registered.append(comp_name)

            # One equation per node: the two natures are two classes, and the
            # loop above is a dispatch, not an accumulation. Stopping here is
            # what keeps the returned list one entry per node.
            break

    return registered


#: The methods a signal node seeds its publications with, tried in this order
#: on each node. A controller answers the first, an ``ObjFlow`` instrument the
#: second, and each returns what it seeded so the walk can report it.
SIGNAL_SEEDS = ("seed_emitted_outputs", "seed_published_measurements")


def register_controller_seeds(system, order):
    """Seed every signal node at instant 0, in the derived order (R45).

    The instant-0 counterpart of :func:`register_controller_equations`, walking
    the same sequence for the same reason: a publication may be read by
    whoever comes next, so a chain has to be written from the top down.
    PyCATSHOO calls start methods in registration order, so registering them
    here IS ordering them. :meth:`muscadet.ObjCtrl.seed_emitted_outputs` says
    what a seed answers and why each nature of output owes one.

    Both natures of node, and one walk for the two: an instrument seeded where
    it was declared ran before every controller whatever the graph said, which
    is how a shipped sensor reading a controller reported its default at
    instant 0 while observing the right number at the same instant.

    **Deliberately not gated on the PDMP manager**, where the equations are.
    Seeding is a signal-graph concern and not a solver one: a boolean output
    compiles to automata and to no equation at all, so a model of controllers
    over a purely discrete system takes no manager and still owes its outputs
    the value their conditions already hold at t = 0.

    Returns
    -------
    list
        ``(node name, publication name)`` per seed registered, in registration
        order. Empty on a model holding no signal node.
    """
    registered: typing.List[typing.Tuple[str, str]] = []

    for comp_name in order.controller_order:
        comp = system.comp[comp_name]

        for seed_name in SIGNAL_SEEDS:
            seed = getattr(comp, seed_name, None)

            if seed is None:
                continue

            registered.extend((comp_name, name) for name in seed())

    return registered


def _order_allocator(
    system, start=0, ceiling=CAPACITY_ORDER_BASE, band="capacity band"
):
    """Hand out distinct increasing integers from ``start``, below ``ceiling``.

    Integers already taken on the system -- a capacity equation, a published
    measurement, or an equation a model registered by hand -- are skipped, so
    "distinct" holds across the whole system and not only within this
    allocation.

    ``ceiling=None`` is the TOP band, which has no neighbour above it to run
    into and therefore cannot exhaust: the controller band (R45) draws that way.
    ``band`` names the ceiling in the refusal, so a model that fills a band is
    told which one.
    """
    taken = {reg.order for reg in getattr(system, "equation_registrations", [])}
    counter = itertools.count(start) if ceiling is None else iter(range(start, ceiling))

    def allocate():
        for value in counter:
            if value not in taken:
                taken.add(value)
                return value
        raise RuntimeError(
            f"Ran out of equation order integers below the {band} "
            f"({ceiling}): the model declares too many continuous "
            "components for the banded allocation"
        )

    return allocate
