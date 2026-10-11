"""The initial state is the fixpoint of the deterministic interactions (#12).

At t = 0 an engine first stabilises the deterministic interactions -- every
discrete flow, every effect of the state an automaton starts in, every value a
controller publishes from what it reads -- and that fixpoint IS the initial
state. Only then does whatever can fire at t = 0 fire, and only that is an
event: a delay-0 transition is an occurrence, a predicate true at the fixpoint
is not.

**Why muscadet has to close the fixpoint itself.** PyCATSHOO runs the start
methods component by component, in creation order. A component started before
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
the recorded variables moves. Two properties make the position safe:

- the settle reads the components LIVE, so a discrete component added after the
  pre-run step -- a 1.x model may keep growing between runs -- is settled too;
- the recorded evaluations are pure functions of their inputs, so a start
  method that happens to run after the settle recomputes the value the settle
  already reached and moves nothing.

**What it does not do.** A discrete loop with no fixpoint (a negation feeding
itself) does not converge; the settle stops after a bounded number of sweeps
and leaves the engine its own behaviour, rather than inventing a state. The
continuous flows are not concerned: their initial state is the PDMP's, computed
by the sweep equations.
"""

#: The name of the start method :func:`register_initial_settle` adds.
SETTLE_METHOD_NAME = "muscadet_settle_initial_state"

#: Where a component keeps its recorded start-time evaluations.
EVALUATIONS_ATTRIBUTE = "_initial_evaluations"


def register_initial_evaluation(comp, fun, writes, start_name=None):
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

    evaluations.append((fun, tuple(var for var in writes if var is not None)))


def recorded_evaluations(system):
    """Every recorded evaluation of ``system``, components in creation order."""
    return [
        evaluation
        for comp in system.comp.values()
        for evaluation in getattr(comp, EVALUATIONS_ATTRIBUTE, ())
    ]


def settle_initial_state(system):
    """Replay the recorded evaluations until the recorded variables stop moving.

    Returns
    -------
    int
        The number of sweeps run. A sweep that moves nothing ends the settle,
        so a model already at its fixpoint costs one.
    """
    evaluations = recorded_evaluations(system)
    written = [var for _, writes in evaluations for var in writes]

    # One sweep per evaluation is the longest a chain of them can need to
    # propagate, plus the sweep that observes nothing moving.
    bound = len(evaluations) + 1

    for sweep in range(1, bound + 1):
        before = [var.value() for var in written]
        for fun, _ in evaluations:
            fun()
        if [var.value() for var in written] == before:
            return sweep

    return bound


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
