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
5. published measurements refresh     -- :data:`MEASUREMENT_ORDER_BASE` upwards
6. controllers republish              -- :data:`CONTROL_ORDER_BASE` upwards (R45)

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

The signal graph is built by :func:`controller_signal_links` and is a graph of
**controllers only**. Two limits follow, and both are deliberate:

* a republication routed through an ``ObjFlow`` instrument -- controller,
  instrument, controller -- is not an edge here. The instrument draws from the
  measurement band, so it is refreshed BEFORE the controller feeding it and
  reads that controller one evaluation late whatever this module does; no
  ordering of the controllers repairs it. Such a montage is neither ordered nor
  refused today. Put the two controllers side by side, or accept the lag
  knowingly;
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

#: First integer of the capacity band. Capacity equations are registered when a
#: capacity is *declared*, before any graph exists, so they cannot be part of the
#: graph-derived allocation -- they take the top band instead, which also makes
#: them run after both sweeps, as the evaluation sequence requires.
CAPACITY_ORDER_BASE = 1_000_000

#: First integer of the published-measurement band (R37). Above the capacity one
#: because a republished reading is taken from a level a capacity holds: the
#: level must be current before the instrument reporting it is refreshed.
MEASUREMENT_ORDER_BASE = 2_000_000

#: First integer of the controller band (R45). The top band, above the
#: measurement one: a controller READS a measurement, so every published reading
#: must be current before the first controller equation runs. Within the band the
#: integers follow a topological sort of the signal graph, which is what makes a
#: chain of controllers settle in ONE evaluation of the equation set.
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
      integrated before any rule reads it (KTD13, hop 1);
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

    if get_capacity(flow_name, "in") is not None:
        return True

    outputs = getattr(comp, "flows_continuous_out", None) or {}

    return bool(outputs) and all(
        get_capacity(name, "out") is not None for name in outputs
    )


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
    two families share the ``{flow}_out`` / ``{flow}_in`` naming convention: a
    loop closed through a discrete signal is reported with its continuous and
    its discrete connections side by side, in the order they close it.
    """

    source: str
    target: str
    flow: str

    def __str__(self) -> str:
        return f"{self.source}.{self.flow}_out -> {self.target}.{self.flow}_in"


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

    def __init__(self, reader, flow, operand, connections):
        #: Component carrying the comparison.
        self.reader = reader
        #: Continuous input flow it compares.
        self.flow = flow
        #: The comparison, rendered as it was declared.
        self.operand = operand

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

    def __init__(self, reader, channel, flow, producer, operand, connections):
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

        super().__init__(
            cycle,
            connections,
            message=(
                "Continuous flow graph must be acyclic (R30, R43): "
                f"{' -> '.join(cycle)} closes a loop through a rate "
                f"observation. Connections closing the loop: "
                f"{', '.join(str(cnct) for cnct in connections)}. "
                f"{reader} thresholds the reading {channel} ({operand}) and "
                f"drives a discrete signal from it, that reading is the rate "
                f"{flow} delivered by {producer}, and that signal reaches a "
                f"component producing the very {flow} it reads. A DELIVERED "
                "RATE is not an integrated state, whatever the measurement "
                "link it arrived on: the allocation sweep recomputes it at "
                "every evaluation, so the regimes on either side of the "
                "threshold select each other within one instant and the model "
                "chatters instead of settling. A deadband does not damp it "
                "either -- a rate JUMPS across the band instead of moving "
                "through it, crossing both edges at once. Observe a CAPACITY "
                "LEVEL instead: put a volume between the producer and this "
                "reading and threshold its level, which is integrated and does "
                "break the loop."
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


def compared_continuous_inputs(comp):
    """The continuous INPUTS this component compares against a threshold.

    ``{flow name: the comparison, rendered as it was declared}``. Both
    directions of the interoperation vocabulary are read, because both are
    algebraic in the same way: a rule guard (R21) and a discrete production
    condition (R22) share one operand shape and one meaning.

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
        if isinstance(flow, FlowContinuous):
            continue

        for source, compare in prod_cond_operands(flow):
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

    * a discrete output whose production condition reads a tainted flow;
    * a mode automaton whose transition watches a tainted variable, which
      taints everything that mode clamps.

    The second is what makes a deadband visible. A band is a mode reading the
    two edge outputs and clamping the AVAILABILITY of the port actually wired
    out, so following production conditions alone would stop at the edges and
    miss the very signal that leaves the component.
    """
    flows_out = {
        name: flow
        for name, flow in (getattr(comp, "flows_out", None) or {}).items()
        if not isinstance(flow, FlowContinuous)
    }

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

        for name, flow in flows_out.items():
            state = state_var_name(flow) or f"{name}_fed_out"

            if state in tainted:
                continue

            reads = {
                state_var_name(source)
                for source, _ in prod_cond_operands(flow)
                if state_var_name(source) is not None
            }

            if f"{name}_fed_available_out" in tainted or not tainted.isdisjoint(reads):
                tainted.add(state)
                changed = True

    return [
        name
        for name, flow in flows_out.items()
        if (state_var_name(flow) or f"{name}_fed_out") in tainted
    ]


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
    """Discrete outputs of ``comp`` carrying the comparison on ``flow_name``.

    Both ways a component can compare a continuous input against a threshold
    seed the walk: a discrete output's production condition (R22) and a rule
    set's guard (R21, :func:`rule_guard_comparison_seeds`).
    """
    flow_in = (getattr(comp, "flows_in", None) or {}).get(flow_name)
    seeds = set()

    for name, flow in (getattr(comp, "flows_out", None) or {}).items():
        if isinstance(flow, FlowContinuous):
            continue

        if any(
            compare is not None and source is flow_in
            for source, compare in prod_cond_operands(flow)
        ):
            seeds.add(state_var_name(flow) or f"{name}_fed_out")

    seeds |= rule_guard_comparison_seeds(comp, flow_in)

    return signal_driven_outputs(comp, seeds) if seeds else []


def inbound_driven_outputs(comp, flow_name):
    """Discrete outputs of ``comp`` deriving from the input ``flow_name``."""
    flow_in = (getattr(comp, "flows_in", None) or {}).get(flow_name)
    state = None if flow_in is None else state_var_name(flow_in)

    return [] if state is None else signal_driven_outputs(comp, {state})


def gates_production_on(comp, flow_name):
    """True when ``comp``'s own production can depend on that discrete input.

    A rule guard naming it -- the declared way a boolean signal selects a
    continuous regime (R21) -- or a mode automaton watching it, since a mode is
    what a derating hangs on and a derating scales what an output produces.

    Requiring it is what keeps a legitimate model building: a discrete signal
    that merely happens to travel between two components which also exchange a
    continuous flow closes no loop, and refusing one would be worse than
    missing one.
    """
    flow_in = (getattr(comp, "flows_in", None) or {}).get(flow_name)

    if flow_in is None:
        return False

    for rule_set in (getattr(comp, "rule_sets", None) or {}).values():
        for rule in rule_set.rules:
            if any(operand.flow is flow_in for operand in rule.cond):
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
                    loops.append(
                        RateComparisonLoopError(key, flow_name, operand, [cnct] + path)
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

            walked = path + [SignalConnection(source_key, target_key, flow_name)]

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
    """How a DISCRETE production condition of ``comp`` thresholds a reading.

    The measurement half of :func:`compared_continuous_inputs`, which reads the
    same operand shape over continuous inputs. A rule guard is deliberately
    absent and cannot be added: ``ObjFlow._resolve_rule_flow`` refuses a
    measurement name in a guard outright (R29), so the discrete production
    condition (R22) is the only vocabulary an ``ObjFlow`` can threshold a
    reading in.

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
        if isinstance(flow, FlowContinuous):
            continue

        for source, compare in prod_cond_operands(flow):
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
        ``(output names, thresholds rendered)``. Both empty when this component
        derives no signal from that reading.
    """
    thresholds = measurement_thresholds(comp, channel_name)
    outputs = measurement_driven_outputs(comp, channel_name) if thresholds else []

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

    return outputs, thresholds


def measurement_driven_outputs(comp, channel_name):
    """Discrete outputs of ``comp`` carrying a threshold on that reading.

    The measurement counterpart of :func:`comparison_driven_outputs`, seeded
    the same way and followed by the same fixpoint.
    """
    channel = measurement_channels(comp).get(channel_name)
    seeds = set()

    for name, flow in (getattr(comp, "flows_out", None) or {}).items():
        if isinstance(flow, FlowContinuous):
            continue

        if any(
            compare is not None and source is channel
            for source, compare in prod_cond_operands(flow)
        ):
            seeds.add(state_var_name(flow) or f"{name}_fed_out")

    return signal_driven_outputs(comp, seeds) if seeds else []


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


def find_rate_observation_loops(system, graph):
    """Every instantaneous loop closed by a threshold on an OBSERVED rate (R43).

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

    Returns
    -------
    list of RateObservationLoopError
        One per (reader, channel, producer) triple, in declaration order. Empty
        for a model that closes no such loop.
    """
    components = getattr(system, "comp", None) or {}

    by_engine_name = engine_name_index(components)

    cnct_info = {}

    def connections_of(key):
        if key not in cnct_info:
            cnct_info[key] = components[key].get_cnct_info()
        return cnct_info[key]

    marked = mark_algebraic_readings(components, by_engine_name, connections_of)

    loops = []

    for key, comp in components.items():
        for channel_name in measurement_channels(comp):
            reached = marked.get((key, channel_name))

            if not reached:
                continue

            outputs, thresholds = reading_driven_signals(comp, channel_name)

            if not outputs:
                continue

            operand = ", ".join(thresholds) or channel_name

            for (producer, flow_name), path in reached.items():
                walked = _walk_signal(
                    key,
                    outputs,
                    graph.ancestors(producer),
                    components,
                    by_engine_name,
                    connections_of,
                )

                if walked is not None:
                    loops.append(
                        RateObservationLoopError(
                            key,
                            channel_name,
                            flow_name,
                            producer,
                            operand,
                            path + walked,
                        )
                    )

    return loops


# ----------------------------------------------------------------------
# The order of the controllers among themselves (R45)
# ----------------------------------------------------------------------
#
# A graph of its own, and it has to be: a controller carries no flow, so the
# continuous-flow graph holds no node for it, and it carries no transported
# quantity, so none of its links may ever become an edge of that graph (KD19).
# What is walked here is the SIGNAL wiring -- who publishes a computed value
# into whose observation input -- and the walk is over controllers only. The
# two montages it deliberately leaves alone are named in the module docstring.


def is_controller(comp):
    """True when ``comp`` is an :class:`muscadet.ObjCtrl`, without importing it.

    Asked of the collection a controller and nothing else carries. Duck-typed
    on purpose, exactly as :func:`republished_channels` reads ``controls_emit``:
    an import of the controller unit here would tie the ordering of a model to
    the presence of a class it need not declare.
    """
    return getattr(comp, "controls_out", None) is not None


def computed_publications(comp):
    """The value outputs of ``comp`` its own equation refreshes (R42, R45).

    A value output declaring no ``emit`` is absent: nothing computes it, so a
    reader of it depends on whatever a model or a failure mode wrote and not on
    when this component's equation ran. Same rule, and the same reading of
    ``controls_emit``, as :func:`republished_channels`.
    """
    emit = getattr(comp, "controls_emit", None)

    if emit is None:
        return []

    return [name for name in published_measurements(comp) if emit.get(name) is not None]


def controller_signal_links(system):
    """The controllers of ``system``, and the signal links between them (R45).

    Returns
    -------
    tuple
        ``(controllers, links)`` -- the controllers keyed as ``system.comp``
        keys them, in declaration order, and one
        :class:`ObservationConnection` per wiring from a computed value output
        to another controller's observation input. Both empty on a model
        holding no controller, in which case the engine is not walked at all.
    """
    components = getattr(system, "comp", None) or {}

    controllers = {key: comp for key, comp in components.items() if is_controller(comp)}

    # The engine is asked nothing on a model that declares no controller, which
    # is every model that predates this unit.
    if not controllers:
        return controllers, []

    by_engine_name = engine_name_index(components)

    links: typing.List[ObservationConnection] = []

    for pub_key, pub_comp in controllers.items():
        cnct_info = pub_comp.get_cnct_info()

        for name in computed_publications(pub_comp):
            box = publication_box(name)
            info = cnct_info.get(box) or {}

            for target in info.get("targets", []):
                obs_key = by_engine_name.get(target.get("obj"), target.get("obj"))
                obs_comp = controllers.get(obs_key)

                # Not a controller: an ObjFlow instrument republishing this
                # reading draws from the measurement band and is refreshed
                # BEFORE this controller whatever is decided here, so it is not
                # an edge of this graph. See the module docstring.
                if obs_comp is None:
                    continue

                obs_box = target.get("cnct")
                channel = channel_behind_box(obs_comp, obs_box)

                if channel is None:
                    continue

                links.append(
                    ObservationConnection(pub_key, obs_key, channel, box, obs_box)
                )

    return controllers, links


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
    """Controller names in the order their equations must run (R45).

    A topological sort of :func:`controller_signal_links`, ties broken by
    declaration order exactly as the flow graph breaks its own (KTD3): the
    nodes are inserted first, and ``TopologicalSorter`` breaks ties by
    insertion order, so the derived sequence is reproducible from run to run
    and independent of hash randomisation.

    Every controller is in the result, including one nothing reads and one that
    reads nothing: an isolated controller is a node too.

    Raises
    ------
    ControllerSignalCycleError
        When a chain of republications closes on itself.
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
        (:class:`RateObservationLoopError`, R43).
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

    return order


def register_controller_equations(system, order):
    """Register every controller's equation in the control band (R45).

    Called once the sweeps have taken their integers, so the allocator sees
    them: "distinct" holds across the whole system and not only within a band.

    A controller carrying no republication registers nothing and takes no
    integer -- it has no equation to order -- but it is still a node of the
    signal graph, so it still constrains the controllers around it.

    **Gated on the PDMP manager**, exactly as
    :meth:`muscadet.System.register_controller_crossings` is, and for the same
    reason: a purely discrete system must stay one, and creating a manager here
    would drag it onto the continuous solver for no gain. A controller that
    republishes has already created one at declaration, its published variables
    being explicit variables of the solver.

    Returns
    -------
    list
        The controllers whose equation was registered, in registration order.
    """
    if not order.controller_order or getattr(system, "pdmp_manager", None) is None:
        return []

    allocate = _order_allocator(
        system, start=CONTROL_ORDER_BASE, ceiling=None, band="controller band"
    )

    registered = []

    for comp_name in order.controller_order:
        comp = system.comp[comp_name]
        needs = getattr(comp, "needs_control_equation", None)

        if needs is None or not needs():
            continue

        value = allocate()
        comp.register_control_equation(system, value)
        order.registrations.append(
            EquationRegistration(
                comp=comp_name, method=CONTROL_EQUATION_METHOD, order=value
            )
        )
        registered.append(comp_name)

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
