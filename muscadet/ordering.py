"""Automatic equation ordering (R8, R30).

PyCATSHOO needs an explicit integer order per equation method, and a model
author must never write one down: adding a component would then force every
other component's declaration to be renumbered. This module derives the whole
sequence from the connection graph instead.

Why a plain topological sort is enough (KTD1)
---------------------------------------------
Two independent sweeps -- demand in reverse-topological order, production in
topological order -- are sufficient **provided** the graph is acyclic and every
remaining loop is broken by an integrated state. Both hold here: :data:`R30`
refuses cycles at the first run, and capacities and mode automata are the state
breaks. So no matching, no block-triangular decomposition, no tearing and no
iterative solve -- a topological sort is the whole mechanism.

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
``{c}_level_out`` / ``_in``        measurement link: carries no quantity (R33)
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

The integer bands
-----------------
Every equation gets a **distinct** integer (KTD3): PyCATSHOO falls back to
alphabetical equation-name order when two equations share an order value, so
ties would make the evaluation sequence a function of equation *names* rather
than of the graph. The integer space is banded, and the bands reproduce the
evaluation sequence of one integration step:

1. demand sweep, reverse-topological  -- allocated here, from 0 upwards
2. production sweep, topological      -- allocated here, straight after
3. capacity levels integrate          -- :data:`CAPACITY_ORDER_BASE` upwards

A capacity equation only reads its own transit variables and writes its own
levels, so it carries no cross-component constraint -- but it must still take a
distinct integer, and it must run last. It is registered at *declaration* time,
long before the graph is known, so it draws from the top band:
``muscadet.System._capacity_equation_order_next`` starts at
:data:`CAPACITY_ORDER_BASE`, which is how the provisional counter of the
capacity unit is superseded by this module's allocation.
"""

import graphlib
import typing

# Reused rather than reimplemented: reading a production condition's aligned
# comparison matrix has exactly one correct handling of a missing entry, and two
# copies of it would be two chances to disagree about what an empty matrix means.
from .flow import _prod_cond_matrix_entry
from .flow_continuous import FlowContinuous

#: Equation method looked up on each component for the demand sweep. The sweep
#: itself lands in a later unit; a component that does not define this method is
#: simply skipped, so the machinery is complete before its first client exists.
DEMAND_EQUATION_METHOD = "compute_demand"

#: Equation method looked up on each component for the production sweep.
PRODUCTION_EQUATION_METHOD = "compute_production"

#: First integer of the capacity band. Capacity equations are registered when a
#: capacity is *declared*, before any graph exists, so they cannot be part of the
#: graph-derived allocation -- they take the top band instead, which also makes
#: them run after both sweeps, as the evaluation sequence requires.
CAPACITY_ORDER_BASE = 1_000_000


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

    def add_connection(self, source, target, flow):
        """Record one continuous data connection."""
        cnct = ContinuousConnection(source=source, target=target, flow=flow)
        self.connections.append(cnct)
        return cnct

    # -- reading -------------------------------------------------------

    @property
    def edges(self):
        """``(source, target)`` pairs, deduplicated, in declaration order.

        Two components joined by several continuous flows are one edge of the
        dependency graph but several connections of the model.
        """
        seen = set()
        edges = []
        for cnct in self.connections:
            pair = (cnct.source, cnct.target)
            if pair not in seen:
                seen.add(pair)
                edges.append(pair)
        return edges

    def connections_between(self, source, target):
        """Every connection wiring ``source`` to ``target``."""
        return [
            cnct
            for cnct in self.connections
            if cnct.source == source and cnct.target == target
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

    def _build_sorter(self, reverse):
        sorter = graphlib.TopologicalSorter()

        # Nodes first, in declaration order: an isolated continuous component
        # is a node too, and insertion order is the tie-break.
        for node in self.nodes:
            sorter.add(node)

        for source, target in self.edges:
            if reverse:
                sorter.add(source, target)
            else:
                sorter.add(target, source)

        return sorter

    def static_order(self, reverse=False):
        """A topological order of the graph, ties broken by declaration order.

        Parameters
        ----------
        reverse : bool
            False for the production sweep (a producer before its consumers),
            True for the demand sweep (a consumer before its producers).

        Raises
        ------
        ContinuousFlowCycleError
            When the graph is cyclic, naming the connections that close it.
        """
        sorter = self._build_sorter(reverse=reverse)

        try:
            sorter.prepare()
        except graphlib.CycleError as err:
            raise self._cycle_error(err, reverse=reverse) from err

        order = []
        while sorter.is_active():
            group = sorter.get_ready()
            order.extend(group)
            sorter.done(*group)

        return order

    def _cycle_error(self, err, reverse):
        """Turn ``graphlib``'s cycle path into an error naming the connections.

        ``CycleError.args[1]`` is the offending path, read along the sorter's
        successor direction -- which is the flow direction for the production
        sweep and its opposite for the demand sweep.
        """
        cycle = list(err.args[1]) if len(err.args) > 1 else []
        pairs = list(zip(cycle, cycle[1:]))
        if reverse:
            cycle = list(reversed(cycle))
            pairs = [(target, source) for source, target in pairs]

        connections = []
        for source, target in pairs:
            connections.extend(self.connections_between(source, target))

        return ContinuousFlowCycleError(cycle, connections)

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
                graph.add_connection(key, target_key, flow_name)

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

    A comparison reading a MEASUREMENT link is deliberately absent: what it
    reads is a capacity level, which is integrated state, so a loop closed
    through it is broken by that state. That is the sanctioned way to gate
    production on a quantity (F4, AE18) and it must keep building.
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


def comparison_driven_outputs(comp, flow_name):
    """Discrete outputs of ``comp`` carrying the comparison on ``flow_name``."""
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

    def __init__(self, graph, demand_order, production_order):
        #: The graph the order was derived from.
        self.graph = graph
        #: Component names in reverse-topological order (demand sweep).
        self.demand_order = list(demand_order)
        #: Component names in topological order (production sweep).
        self.production_order = list(production_order)
        #: What this order actually registered, in registration order.
        self.registrations = []

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

    Raises
    ------
    ContinuousFlowCycleError
        When the continuous-flow graph is cyclic (R30), or when a comparison on
        a continuous rate closes an instantaneous loop the graph does not carry
        (:class:`RateComparisonLoopError`).
    """
    graph = build_continuous_flow_graph(system)

    # The forward sweep is what carries the acyclicity check: both sweeps read
    # the same graph, so one check covers both.
    production_order = graph.static_order(reverse=False)
    demand_order = graph.static_order(reverse=True)

    # Only once the graph is known acyclic: the walk below reads it, and a
    # cycle in the continuous connections is the error to report first.
    loops = find_rate_comparison_loops(system, graph)

    if loops:
        raise loops[0]

    return EquationOrder(graph, demand_order, production_order)


def register_equation_order(system):
    """Derive the order and register the sweep equations on the PDMP manager.

    Called once, from the system's pre-run step: every connection exists and no
    equation has run yet.

    A component takes part in a sweep only when it defines that sweep's
    equation method, so this stays correct while the sweeps themselves are
    still being built.
    """
    order = compute_equation_order(system)

    # A purely discrete system must stay byte-identical to what it was before
    # the continuous layer existed -- in particular, no PDMP manager.
    if not order.graph.nodes:
        return order

    allocate = _order_allocator(system)

    for method, sequence in (
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

    return order


def _order_allocator(system):
    """Hand out distinct increasing integers below the capacity band.

    Integers already taken on the system -- a capacity equation, or an equation
    a model registered by hand -- are skipped, so "distinct" holds across the
    whole system and not only within this allocation.
    """
    taken = {reg.order for reg in getattr(system, "equation_registrations", [])}
    counter = iter(range(CAPACITY_ORDER_BASE))

    def allocate():
        for value in counter:
            if value not in taken:
                taken.add(value)
                return value
        raise RuntimeError(
            "Ran out of equation order integers below the capacity band "
            f"({CAPACITY_ORDER_BASE}): the model declares too many continuous "
            "components for the banded allocation"
        )

    return allocate
