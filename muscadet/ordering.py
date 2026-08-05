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


def component_is_continuous(comp):
    """True when ``comp`` carries at least one continuous flow.

    Uses the filtered properties the continuous-flow unit put on ``ObjFlow``,
    so a component's flow dicts are read once through the same lens everywhere.
    """
    return bool(getattr(comp, "flows_continuous_in", None)) or bool(
        getattr(comp, "flows_continuous_out", None)
    )


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


class ContinuousFlowCycleError(ValueError):
    """A continuous-flow cycle, refused at the system's first run (R30).

    Subclasses ``ValueError`` so a model error stays a model error for any
    caller already catching one.
    """

    def __init__(self, cycle, connections):
        self.cycle = list(cycle)
        self.connections = list(connections)

        path = " -> ".join(self.cycle)
        wiring = ", ".join(str(cnct) for cnct in self.connections) or "none found"

        super().__init__(
            "Continuous flow graph must be acyclic (R30): "
            f"{path} closes a loop. "
            f"Connections closing the loop: {wiring}"
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

    # Message boxes report their counterpart by the component's engine name,
    # which is the ``system.comp`` key for a flat model -- resolved rather than
    # assumed, so a renamed or nested component still lands on the right node.
    by_engine_name = {}
    for key, comp in components.items():
        by_engine_name.setdefault(comp.name(), key)

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
        When the continuous-flow graph is cyclic (R30).
    """
    graph = build_continuous_flow_graph(system)

    # The forward sweep is what carries the acyclicity check: both sweeps read
    # the same graph, so one check covers both.
    production_order = graph.static_order(reverse=False)
    demand_order = graph.static_order(reverse=True)

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
