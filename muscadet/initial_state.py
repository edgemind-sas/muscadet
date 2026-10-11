"""The initial state is the fixpoint of the deterministic interactions (#12).

At t = 0 an engine first stabilises the deterministic interactions -- every
discrete flow, every combinational gate, every effect of the state a mode
starts in -- and that fixpoint IS the initial state. Only then does whatever can fire at t = 0 fire, and only that is an
event: a delay-0 transition is an occurrence, a predicate true at the fixpoint
is not.

**Why muscadet has to close the fixpoint itself.** PyCATSHOO runs the start
methods in registration order, which for the flows is component creation order. A component started before
its supplier reads the supplier's default, and the truth reaches it afterwards
through a sensitive method, which the engine counts as a change. Measured on a
Source feeding a Target with no failure at all, "target fed" counted 0
occurrences when the source was declared first and 1 when the target was: the
same model, two answers, and an engine that evaluates the fixpoint without a
declaration order could follow neither.

**How it is closed.** Every component records its start-time evaluations, each
with the variables it writes (:func:`register_initial_evaluation`). The pre-run
step registers ONE settle method on the component created last
(:func:`register_initial_settle`), so it runs after every other start method,
and :func:`settle_initial_state` replays the recorded evaluations until none of
the recorded variables moves. A sweep runs the components along their
connections (:func:`component_order`) and, within one, inputs before effects
before conditions before outputs (``RANK_*``), so a chain settles in two sweeps
whatever order it was declared in. Two properties make the position safe:

- the settle reads the components LIVE, so a discrete component added after the
  pre-run step -- a 1.x model may keep growing between runs -- is settled too;
- the recorded evaluations are pure functions of their inputs, so a start
  method that happens to run after the settle recomputes the value the settle
  already reached and moves nothing.

**What it does not do.** A discrete loop with no fixpoint (a negation feeding
itself) has no initial state: the settle stops at the first repeated state and
refuses the start with :class:`InitialFixpointError`.
A loop with SEVERAL fixpoints (a latch) settles on the one the sweep order
reaches, deterministically; another engine may legitimately reach another.
The continuous flows are not concerned: their initial state is the PDMP's,
computed by the sweep equations. A controller's seeds are not recorded either:
they are registered at the pre-run step, before the settle, and the components
reading a controller's signal are replayed after them.
"""

import contextlib
import heapq

#: The name of the start method :func:`register_initial_settle` adds.
SETTLE_METHOD_NAME = "muscadet_settle_initial_state"

#: Where a component keeps its recorded start-time evaluations.
EVALUATIONS_ATTRIBUTE = "_initial_evaluations"

#: Where a component's evaluations stand in one sweep, in dependency order:
#: what arrives, then the effects of the state a mode starts in, then the
#: production conditions they feed, then what leaves. Recorded in any other
#: order, a sweep read the previous sweep's condition, so the fixpoint moved by
#: one component per sweep (measured: 402 sweeps on a 400-block chain) and a
#: latch of two negations cycled instead of settling.
RANK_INPUT = 0
RANK_EFFECT = 1
RANK_CONDITION = 2
RANK_OUTPUT = 3

#: Where the pre-run step leaves the refusal :func:`settle_initial_state`
#: raised, for the run entry points to raise it again typed: PyCATSHOO hands an
#: exception raised inside a start method back as a ``PycException`` carrying
#: only its text.
ERROR_ATTRIBUTE = "_initial_fixpoint_error"


class InitialFixpointError(ValueError):
    """The deterministic interactions have no fixpoint at t = 0 (#12).

    A discrete loop through a negation: no state of it agrees with its own
    evaluations, so there is no initial state to start from. Raised rather than
    started, because a start used to HANG there: the settle's writes woke the
    loop's sensitive methods, which then chased each other for ever. Up to
    5.13.0 such a loop stayed dormant by accident -- nothing at start had moved
    a variable it reads -- and woke at its first change in the run instead.

    Attributes
    ----------
    variables : list of str
        ``component.variable`` for every recorded variable still moving.
    """

    def __init__(self, system_name, variables):
        self.variables = list(variables)
        super().__init__(
            f"System {system_name}: the deterministic interactions have no "
            f"fixpoint at t = 0, so there is no initial state to start from. "
            f"Still moving after the settle: {', '.join(self.variables)}. "
            f"This is a discrete loop through a negation, most likely: break "
            f"it with a delay (a tempo output or a mode) or remove the "
            f"negation"
        )


def register_initial_evaluation(comp, fun, writes, rank, start_name=None):
    """Record a start-time evaluation the initial fixpoint replays.

    Parameters
    ----------
    comp : cod3s.PycComponent
        The component the evaluation belongs to.
    fun : callable
        The evaluation: a pure function of the current variable values,
        writing ``writes``.
    writes : iterable of PyCATSHOO variables
        What ``fun`` writes. The fixpoint is reached when none of them moves
        over a whole sweep, so a variable left out here is not waited for.
    rank : int
        Where it stands within its component's sweep, one of the ``RANK_*``
        constants.
    start_name : str, optional
        When given, ``fun`` is also registered as a start method under that
        name. Left out for an evaluation the component already reaches some
        other way at start, so that the legacy wiring stays byte-identical.
    """
    if start_name is not None:
        comp.addStartMethod(start_name, fun)

    evaluations = getattr(comp, EVALUATIONS_ATTRIBUTE, None)
    if evaluations is None:
        evaluations = []
        setattr(comp, EVALUATIONS_ATTRIBUTE, evaluations)

    evaluations.append((rank, fun, tuple(var for var in writes if var is not None)))


def component_order(system):
    """The components of ``system`` along their connections, upstream first.

    A topological order of the graph the exporting ends of the message boxes
    draw (``*_out``), whatever the channel, ties and cycles broken by creation
    order. It decides
    nothing but the COST of the settle: in this order a chain settles in one
    sweep plus the one that sees nothing move, where creation order needed one
    sweep per hop for a chain declared downstream first. Correctness does not
    rest on it, since the settle sweeps until nothing moves.

    Cached on the system and recomputed when a component appears, components
    being the one thing a discrete model may add after the pre-run step. A
    connection added on its own keeps the cached order, which costs sweeps and
    changes no value.
    """
    components = system.comp
    cached = getattr(system, "_initial_component_order", None)
    if cached is not None and cached[0] == len(components):
        return cached[1]

    # Imported here: ``muscadet.ordering`` imports the flow modules, which
    # import this one.
    from .ordering import engine_name_index

    keys = list(components)
    position = {key: index for index, key in enumerate(keys)}
    by_engine_name = engine_name_index(components)

    downstream = {key: set() for key in keys}
    for key, comp in components.items():
        get_cnct_info = getattr(comp, "get_cnct_info", None)
        if get_cnct_info is None:
            continue
        for box_name, info in get_cnct_info().items():
            # The exporting end only. A connection is listed on BOTH of its
            # boxes, so reading the importing end too drew every edge twice,
            # once each way, and turned a chain into a cycle.
            if not box_name.endswith("_out"):
                continue
            for target in info.get("targets", []):
                target_key = by_engine_name.get(target.get("obj"), target.get("obj"))
                if target_key in downstream and target_key != key:
                    downstream[key].add(target_key)

    indegree = {key: 0 for key in keys}
    for targets in downstream.values():
        for target_key in targets:
            indegree[target_key] += 1

    ready = [position[key] for key in keys if indegree[key] == 0]
    heapq.heapify(ready)
    placed = set()
    order = []

    while len(order) < len(keys):
        if not ready:
            # A cycle: enter it at the component created first.
            heapq.heappush(
                ready, min(position[key] for key in keys if key not in placed)
            )
        key = keys[heapq.heappop(ready)]
        if key in placed:
            continue
        placed.add(key)
        order.append(components[key])
        for target_key in downstream[key]:
            indegree[target_key] -= 1
            if indegree[target_key] == 0 and target_key not in placed:
                heapq.heappush(ready, position[target_key])

    system._initial_component_order = (len(components), order)
    return order


def recorded_evaluations(system):
    """Every recorded evaluation of ``system``, in the order a sweep runs them.

    Components along :func:`component_order`, and within one component by
    rank, declaration order breaking ties. Each entry is ``(fun, writes)``.
    """
    return [
        (fun, writes)
        for comp in component_order(system)
        for _, fun, writes in sorted(
            getattr(comp, EVALUATIONS_ATTRIBUTE, ()), key=lambda entry: entry[0]
        )
    ]


def settle_initial_state(system):
    """Replay the recorded evaluations until the recorded variables stop moving.

    **A loop with no fixpoint is refused** with :class:`InitialFixpointError`,
    naming what never settled. A state seen twice is a cycle, so the search
    stops at the first repetition rather than at the bound. Stopping quietly
    was tried first and is not available: whatever the settle wrote, even put
    back, woke the loop's sensitive methods and the start never returned.

    Returns
    -------
    int
        The number of sweeps run. A sweep that moves nothing ends the settle,
        so a model already at its fixpoint costs one.
    """
    evaluations = recorded_evaluations(system)
    written = [var for _, writes in evaluations for var in writes]

    before = tuple(var.value() for var in written)
    seen = {before}

    # One sweep per evaluation is the longest a chain of them can need, plus
    # the sweep that observes nothing moving; a cycle is caught well before.
    bound = len(evaluations) + 1

    for sweep in range(1, bound + 1):
        prior = before
        for fun, _ in evaluations:
            fun()
        after = tuple(var.value() for var in written)
        if after == before:
            return sweep
        if after in seen:
            break
        seen.add(after)
        before = after

    moving = [
        f"{var.parent().basename()}.{var.basename()}"
        for var, old, new in zip(written, prior, after)
        if old != new
    ]
    error = InitialFixpointError(system.name(), moving)
    setattr(system, ERROR_ATTRIBUTE, error)
    raise error


def register_initial_settle(system):
    """Register the settle as a start method of the component created last.

    Called once, from the pre-run step. A system with no component registers
    nothing. One whose components record no evaluation YET still gets the
    settle, since the components are read live and a discrete one may be added
    after the step; with nothing recorded it is a single empty sweep.

    Returns
    -------
    bool
        True when the settle was registered by this call.
    """
    if getattr(system, "_initial_settle_registered", False):
        return False

    components = list(system.comp.values())
    if not components:
        return False

    components[-1].addStartMethod(
        SETTLE_METHOD_NAME, lambda: settle_initial_state(system)
    )
    system._initial_settle_registered = True
    return True


@contextlib.contextmanager
def initial_fixpoint_refusal(system):
    """Raise again, typed, the refusal a start method left on ``system``.

    Wraps a run entry point. PyCATSHOO hands an exception raised inside a start
    method back as a ``PycException`` carrying its text alone, so a caller
    catching :class:`InitialFixpointError` -- or the ``ValueError`` it is --
    would otherwise catch nothing.
    """
    setattr(system, ERROR_ATTRIBUTE, None)
    try:
        yield
    except Exception as exc:
        error = getattr(system, ERROR_ATTRIBUTE, None)
        if error is None or error is exc:
            raise
        setattr(system, ERROR_ATTRIBUTE, None)
        raise error from exc
