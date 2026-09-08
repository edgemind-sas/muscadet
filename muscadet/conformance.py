"""Where an engine does what muscadet defines, but does it OTHERWISE.

muscadet is a modelling interface over more than one engine, and it is the
semantic authority of that interface: when two engines disagree legitimately,
what a muscadet model MEANS is decided here and not by whichever engine happens
to run it. An engine that cannot honour a decision does not make it wrong -- it
makes the engine non-conformant on that point, and the gap has to be written
down somewhere a modeller can read it.

This module is that somewhere.

Not the capability matrix, and never confused with it
-----------------------------------------------------
Two registries, two questions:

===========================  ==============================================
The capability matrix        what an engine **knows how** to do
This registry                where it does it **otherwise**
===========================  ==============================================

Keeping them apart is not tidiness, it is what keeps a launch decision
answerable. A refusal is a set subtraction -- the capabilities a study uses,
minus the capabilities the engine covers -- and it is a refusal precisely
because a missing capability means the study cannot run. A divergence is not
that: the study runs, it returns numbers, and those numbers are right under a
convention that is not muscadet's. Folding a divergence into the matrix would
make it look like a missing capability, so the selector would refuse an engine
perfectly able to carry the study, and the subtraction would have to grow a
third state to tell the two apart.

**So nothing here can refuse anything, and that is enforced structurally rather
than promised**: no module of muscadet imports this one. It is a leaf that only
a caller outside the library reads, so there is no code path in which a
conformance entry could reach a decision to run or not to run
(``tests/test_conformance_registry_001.py`` walks the package and fails if that
ever stops being true).

The two registries even answer an unknown engine in OPPOSITE directions, which
is the shortest proof they are different objects:

- the capability matrix answers "covers nothing", so an engine nobody declared
  is refused everything. Restrictive, because it guards a launch;
- :func:`deviations` answers ``()``, so an engine nobody assessed is warned
  about for nothing. Permissive, because it guards nothing at all. Silence is
  not a claim of conformance either, which is what :func:`is_assessed` is for.

Why it lives in the library and not in the platform
---------------------------------------------------
The divergence is a property of the ENGINE, not of whoever drives it. A
modeller writing a model in Python, with no platform anywhere, runs into the
same convention and deserves the same warning -- and gets it here, from
:func:`describe`, without building a system or running a single replica.

That is not a theoretical audience. The three interactive entries below are
compensated today by the COD3S Platform's interactive worker, so they never
reach a platform user. A library user driving the same engine directly gets no
such compensation, and is therefore the ONLY one who meets them. Held platform
side, the registry would warn exactly the people it does not concern.

Kept import-light on purpose (pydantic and nothing else): a registry that has
to load an engine to be read is a registry nobody reads before choosing one.

What is written here, and what would be
---------------------------------------
A **semantic point** is one place muscadet had to decide. It carries the rule,
why muscadet settled on it, and where that was settled.

A **deviation** attaches an engine to a point and says what the engine does
instead, what it costs, and what -- if anything -- restores muscadet's meaning
before a result is read.

An engine is **assessed** once it has been reviewed against every declared
point. Conformance is then readable both ways: :func:`deviations` for the gaps
and :func:`conformant_points` for the rest, so a reader can tell a point an
engine honours from a point nobody has looked at. The capability matrix states
what IS covered for the same reason.

A third engine that muscadet cannot know declares itself through
:func:`register_semantic_point`, :func:`register_deviation` and
:func:`assess_engine`, exactly as it registers itself as an engine: muscadet
imports no engine, here no more than anywhere else. What it does NOT do is let
an engine be the sole author of its own conformance record -- the points and
the built-in entries below are muscadet's, because an engine free to declare
itself conformant would turn the registry into self-certification.

Examples
--------
>>> from muscadet import conformance
>>> [d.point for d in conformance.deviations(conformance.ENGINE_PYCATSHOO)]
['transition_instant_observation']
>>> print(conformance.describe("raichu"))       # doctest: +SKIP

From a shell, with no model and no run::

    python -m muscadet.conformance raichu

Both records are pydantic models, so a consumer carrying divergences into a
result's provenance serialises them with ``model_dump()`` rather than
re-transcribing the wording -- which is how the two would come to say different
things about the same gap.
"""

from __future__ import annotations

import typing

import pydantic

# ---------------------------------------------------------------------------
# Engine identity
# ---------------------------------------------------------------------------

#: The engine slugs, spelled exactly as the COD3S Platform spells them
#: (``backend/src/definitions/simulation_engine.py``). Deliberately the same
#: strings and not a second vocabulary: the platform reads this registry to put
#: the divergences in a result's provenance, and two spellings of one engine
#: would join on nothing. muscadet still imports no engine -- a slug is a
#: string, not a dependency.
ENGINE_PYCATSHOO: typing.Final[str] = "pycatshoo"
ENGINE_RAICHU: typing.Final[str] = "raichu"

#: The engine muscadet is itself written against. Named because it is what
#: makes the first entry below worth reading: the reference engine is the one
#: found non-conformant, which is possible exactly because muscadet decides the
#: semantics rather than transcribing whatever its own engine happens to do.
REFERENCE_ENGINE: typing.Final[str] = ENGINE_PYCATSHOO

# ---------------------------------------------------------------------------
# The vocabulary: one slug per point muscadet had to decide
# ---------------------------------------------------------------------------

#: What an indicator reads at the exact instant a transition fires.
POINT_TRANSITION_INSTANT_OBSERVATION: typing.Final[str] = (
    "transition_instant_observation"
)

#: The end date an armed non-deterministic transition carries in an interactive
#: session, before the operator has planned one.
POINT_ARMED_TRANSITION_DATE: typing.Final[str] = "armed_transition_date"

#: How much of an instant one interactive step resolves.
POINT_INTERACTIVE_STEP_GRANULARITY: typing.Final[str] = "interactive_step_granularity"

#: Advancing an interactive session to a date the caller names.
POINT_ADVANCE_TO_DATE: typing.Final[str] = "advance_to_date"


class SemanticPoint(pydantic.BaseModel):
    """One place muscadet had to decide what a model means.

    A point exists because two engines could each be defensible and still
    disagree. Writing the rule down is what turns "the engines differ" into
    "this engine is non-conformant on this point", which is a statement someone
    can act on.
    """

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    name: str = pydantic.Field(..., description="Slug, stable across releases")
    rule: str = pydantic.Field(
        ..., description="What a muscadet model means on this point"
    )
    rationale: str = pydantic.Field(
        ..., description="Why muscadet settled on that reading"
    )
    source: str = pydantic.Field(..., description="Where the decision is written down")


class Deviation(pydantic.BaseModel):
    """An engine departing from muscadet on one semantic point.

    ``compensation`` is the field a reader should look at second: a divergence
    an adapter absorbs before anything is reported is a very different thing
    from one that reaches the numbers, and the distinction is invisible from
    the engine's behaviour alone. ``None`` means nothing restores muscadet's
    meaning, so the divergence is in what the analyst reads.
    """

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    engine: str = pydantic.Field(..., description="Engine slug")
    point: str = pydantic.Field(..., description="Slug of the semantic point")
    behaviour: str = pydantic.Field(..., description="What the engine does instead")
    consequence: str = pydantic.Field(
        ..., description="What it changes for whoever reads a result"
    )
    compensation: typing.Optional[str] = pydantic.Field(
        default=None,
        description="What restores muscadet's meaning, and where; None if nothing does",
    )
    source: str = pydantic.Field(
        ..., description="Where the divergence was established"
    )


class ConformanceRegistryError(ValueError):
    """A registry entry that could not be recorded, or a point that is unknown."""


# ---------------------------------------------------------------------------
# The built-in content
# ---------------------------------------------------------------------------

#: The points muscadet ships with, in REPORT order: every listing follows this
#: order, so two consultations of the same engine read identically.
SEMANTIC_POINTS: typing.Final[typing.Tuple[SemanticPoint, ...]] = (
    SemanticPoint(
        name=POINT_TRANSITION_INSTANT_OBSERVATION,
        rule=(
            "An indicator observed at instant t sees the state AFTER every "
            "transition that fires at t."
        ),
        rationale=(
            "The usual convention for jump processes, and the one that reads "
            "trajectories as continuous from the right. It is also the plain "
            "reading of a maintenance planned at 8760 h: at 8760 h, it has "
            "happened. Nothing else makes a planned date mean the same thing "
            "to the analyst who wrote it and to the model that runs it."
        ),
        source=(
            "COD3S Platform ADR-2026-09-01-multi-moteur-simulation-raichu, "
            "decision 9 (settled 2026-09-04)"
        ),
    ),
    SemanticPoint(
        name=POINT_ARMED_TRANSITION_DATE,
        rule=(
            "In an interactive session an armed non-deterministic transition "
            "carries an end date of inf until the operator plans one "
            "(isimu_set_transition). muscadet samples no law on the "
            "interactive path."
        ),
        rationale=(
            "An interactive session is a trajectory the operator drives, so "
            "the session must not pick the future on their behalf: the point "
            "of stepping through a model is to choose what happens next. "
            "muscadet holds that line to its unpleasant end -- a step with "
            "nothing but unsampled laws active is REFUSED with a warning "
            "rather than carrying the clock, and every integrated level with "
            "it, to infinity, which no later call could undo."
        ),
        source="README, 'Interactive simulation'; muscadet.System.isimu_start",
    ),
    SemanticPoint(
        name=POINT_INTERACTIVE_STEP_GRANULARITY,
        rule=(
            "One interactive step resolves the whole instant: every "
            "transition due at the date it lands on fires before the step "
            "returns."
        ),
        rationale=(
            "The state a step hands back has to be a state the model can "
            "actually be in. Stopping between two transitions due at the same "
            "date exposes a half-resolved instant that no batch run ever "
            "produces, so the same model would answer differently step by "
            "step and in Monte Carlo -- which is the one divergence an "
            "interactive session exists to rule out."
        ),
        source="README, 'Interactive simulation'; cod3s PycSystem.isimu_step_forward",
    ),
    SemanticPoint(
        name=POINT_ADVANCE_TO_DATE,
        rule=(
            "An interactive session advances to a date the caller names, "
            "stopping on every event due before it, through the "
            "isimu_step_to(date) primitive."
        ),
        rationale=(
            "Stepping from dated transition to dated transition is not enough "
            "to walk a continuous model: a source feeding a tank, with no "
            "failure mode and no automaton, has no event to advance towards "
            "and sits at t=0 reporting its initial values for ever. The date "
            "has to be able to come from the caller, which is why muscadet "
            "pins cod3s at 1.16.0 or later."
        ),
        source="README, 'Interactive simulation'; muscadet pyproject.toml (cod3s pin)",
    ),
)

#: What each assessed engine does otherwise, in point order then engine order.
#:
#: Both engines appear on both sides of the line, and that is worth noticing
#: rather than a coincidence: the reference engine is the one that fails the
#: first point, and the engine that fails the last three is the one that gets
#: the first one right. A registry where one engine only ever deviates would be
#: a comparison dressed as a rule.
DEVIATIONS: typing.Final[typing.Tuple[Deviation, ...]] = (
    Deviation(
        engine=ENGINE_PYCATSHOO,
        point=POINT_TRANSITION_INSTANT_OBSERVATION,
        behaviour=(
            "Observes the state BEFORE the transitions due at that instant "
            "are resolved."
        ),
        consequence=(
            "Visible only when a DETERMINISTIC transition falls exactly on an "
            "observation instant, which commensurable durations make ordinary "
            "-- a mode with a 1000 h delay observed at 1000 h is reported "
            "still running by this engine and failed by an engine that reads "
            "the instant as muscadet defines it. Measured on the coin_toss "
            "corpus at 8.41 and 9.05 Monte-Carlo sigma, the affected "
            "trajectory mass predicting the gap at 0.031 against 0.034 "
            "observed. No number already delivered changes: what changes is "
            "which reading is declared right."
        ),
        compensation=None,
        source=(
            "COD3S Platform ADR-2026-09-01-multi-moteur-simulation-raichu, "
            "decision 9; ADR-2026-09-08-muscadet-facade-portable-deux-moteurs, "
            "decision 8"
        ),
    ),
    Deviation(
        engine=ENGINE_RAICHU,
        point=POINT_ARMED_TRANSITION_DATE,
        behaviour="Samples the law as soon as the transition is armed.",
        consequence=(
            "The session carries a date the operator did not plan, so what "
            "the model does next is settled before they are asked. A step "
            "that muscadet refuses rather than run to infinity advances here "
            "instead, to a drawn date."
        ),
        compensation=(
            "The COD3S Platform's interactive worker, by hand. Nothing "
            "compensates it for a library user driving this engine directly."
        ),
        source=(
            "COD3S Platform SIMULATION_ENGINE/COMPARAISON-MOTEURS, section 8 "
            "(2026-09-08)"
        ),
    ),
    Deviation(
        engine=ENGINE_RAICHU,
        point=POINT_INTERACTIVE_STEP_GRANULARITY,
        behaviour=(
            "One step draws a SINGLE transition, leaving the rest of the "
            "instant pending."
        ),
        consequence=(
            "A state read between two transitions sharing a date is a state "
            "the batch path never exposes, so a model walked step by step can "
            "be seen in a configuration its Monte-Carlo runs never visit."
        ),
        compensation=(
            "The COD3S Platform's interactive worker, by hand. Nothing "
            "compensates it for a library user driving this engine directly."
        ),
        source=(
            "COD3S Platform SIMULATION_ENGINE/COMPARAISON-MOTEURS, section 8 "
            "(2026-09-08)"
        ),
    ),
    Deviation(
        engine=ENGINE_RAICHU,
        point=POINT_ADVANCE_TO_DATE,
        behaviour=(
            "Has no advance-to-a-date primitive at all; the date is reached "
            "by repeating single steps."
        ),
        consequence=(
            "Nothing an analyst reads differs once the emulation is in place, "
            "which is exactly why this is a deviation and not a hole in the "
            "capability matrix: at the muscadet interface the capability IS "
            "served, so a matrix line would be red for something that works. "
            "What the emulation inherits is the granularity above -- it is "
            "built out of the very step that resolves one transition at a "
            "time -- and a caller who has to know that is a caller the "
            "registry is for."
        ),
        compensation=(
            "The COD3S Platform's interactive worker, by hand. Nothing "
            "compensates it for a library user driving this engine directly."
        ),
        source=(
            "COD3S Platform SIMULATION_ENGINE/COMPARAISON-MOTEURS, sections 8 "
            "and 9 (2026-09-08)"
        ),
    ),
)

#: Engines reviewed against every point above. Membership is what separates
#: "honours this point" from "nobody looked": :func:`conformant_points` answers
#: only for an engine in here, so an engine that simply has no entry cannot be
#: read as a clean bill of health.
ASSESSED_ENGINES: typing.Final[typing.FrozenSet[str]] = frozenset(
    {ENGINE_PYCATSHOO, ENGINE_RAICHU}
)


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------

#: Live content: the built-in declarations above, plus whatever an engine
#: outside muscadet registered. Private because it is mutable and the public
#: functions are the only reader -- a caller holding the list would see it
#: change under them when a third engine is imported.
_POINTS: typing.List[SemanticPoint] = list(SEMANTIC_POINTS)
_DEVIATIONS: typing.List[Deviation] = list(DEVIATIONS)
_ASSESSED: typing.Set[str] = set(ASSESSED_ENGINES)


def semantic_points() -> typing.Tuple[SemanticPoint, ...]:
    """Every point muscadet has decided, in report order.

    Returns
    -------
    tuple of SemanticPoint
        Built-in points first, in declaration order, then any a third engine
        registered, in registration order.
    """
    return tuple(_POINTS)


def semantic_point(name: str) -> SemanticPoint:
    """The point ``name`` designates.

    Raises
    ------
    ConformanceRegistryError
        When no point carries that name. Refused rather than answered with
        ``None``: a misspelt slug that returned nothing would read as "muscadet
        has decided nothing here", which is the opposite of the truth.
    """
    for point in _POINTS:
        if point.name == name:
            return point

    known = ", ".join(sorted(p.name for p in _POINTS))
    raise ConformanceRegistryError(
        f"unknown semantic point '{name}'; declared points are: {known}"
    )


def deviations(
    engine: typing.Optional[str] = None,
    point: typing.Optional[str] = None,
) -> typing.Tuple[Deviation, ...]:
    """Where things are done otherwise, narrowed by engine and/or point.

    Ordered by :func:`semantic_points` and then by engine slug, never by the
    order entries happened to be registered in: a listing whose order depends
    on which engines are installed is a listing nobody can diff between two
    runs.

    Parameters
    ----------
    engine : str, optional
        An engine slug. Omitted, every engine.
    point : str, optional
        A semantic point slug. Omitted, every point.

    Returns
    -------
    tuple of Deviation
        Empty both when there is nothing to report and when the engine was
        never assessed -- the two are told apart by :func:`is_assessed`, not by
        the length of this tuple. Empty is never a refusal and never a verdict.
    """
    order = {p.name: index for index, p in enumerate(_POINTS)}

    selected = [
        deviation
        for deviation in _DEVIATIONS
        if (engine is None or deviation.engine == engine)
        and (point is None or deviation.point == point)
    ]

    # A deviation on a point that is no longer declared sorts last rather than
    # raising: reading the registry must never be the thing that fails.
    return tuple(
        sorted(
            selected,
            key=lambda d: (order.get(d.point, len(order)), d.engine),
        )
    )


def conformant_points(engine: str) -> typing.Tuple[str, ...]:
    """The points ``engine`` was reviewed against and honours, in report order.

    Empty for an engine outside :func:`is_assessed`, and empty there means
    "nobody looked" rather than "nothing to honour" -- which is why this
    answers slugs for an assessed engine and nothing at all for the others,
    instead of quietly returning the whole vocabulary.
    """
    if not is_assessed(engine):
        return ()

    departed = {deviation.point for deviation in deviations(engine)}
    return tuple(p.name for p in _POINTS if p.name not in departed)


def is_assessed(engine: str) -> bool:
    """Whether ``engine`` has been reviewed against every declared point.

    The question :func:`deviations` cannot answer on its own. It is NOT a
    launch verdict: an unassessed engine is not refused anything by muscadet,
    it is merely an engine about which the registry says nothing.
    """
    return engine in _ASSESSED


def engines() -> typing.Tuple[str, ...]:
    """Every engine the registry knows something about, sorted."""
    return tuple(sorted(_ASSESSED | {d.engine for d in _DEVIATIONS}))


def describe(engine: typing.Optional[str] = None) -> str:
    """The registry as text, for a modeller with no platform and no run.

    This is the surface the library user actually meets: reading it builds no
    system, loads no engine and computes nothing, which is the whole point of
    a conformance record. ``python -m muscadet.conformance <engine>`` prints
    exactly this.

    Parameters
    ----------
    engine : str, optional
        Restrict to one engine. Omitted, every engine the registry knows.

    Returns
    -------
    str
        Plain text, no locale: it goes to a terminal, a log or a notebook.
    """
    if engine is None:
        blocks = [describe(name) for name in engines()]
        return "\n\n".join(blocks) if blocks else "No engine in the registry."

    lines = [f"muscadet conformance -- engine '{engine}'"]

    if engine == REFERENCE_ENGINE:
        lines.append(
            "(the engine muscadet is written against; being the reference "
            "does not make it conformant)"
        )

    if not is_assessed(engine):
        lines.append("")
        lines.append(
            "No conformance assessment on record. That is not a clean bill of "
            "health: nobody has reviewed this engine against the points below."
        )

    departures = deviations(engine)

    if not departures:
        lines.append("")
        lines.append(
            "Departs from muscadet on no declared point."
            if is_assessed(engine)
            else "No declared departure."
        )
    else:
        plural = "" if len(departures) == 1 else "s"
        lines.append("")
        lines.append(f"Departs from muscadet on {len(departures)} point{plural}:")

        for deviation in departures:
            point = semantic_point(deviation.point)
            lines.append("")
            lines.append(f"  [{point.name}]")
            lines.append(f"    muscadet defines : {point.rule}")
            lines.append(f"    this engine      : {deviation.behaviour}")
            lines.append(f"    consequence      : {deviation.consequence}")
            lines.append(
                "    compensated by   : "
                + (deviation.compensation or "nothing; it reaches the result")
            )
            lines.append(f"    source           : {deviation.source}")

    honoured = conformant_points(engine)

    if honoured:
        lines.append("")
        lines.append("Honours: " + ", ".join(honoured))

    lines.append("")
    lines.append(
        "Informational. muscadet refuses no run on the strength of this "
        "record; whether an engine can carry a study is the capability "
        "matrix's question, and it is asked elsewhere."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registration, for an engine muscadet cannot know
# ---------------------------------------------------------------------------


def register_semantic_point(point: SemanticPoint) -> None:
    """Declare a point muscadet does not ship.

    For an integration that had to settle a meaning muscadet has not met yet.
    Re-declaring an existing name is refused rather than merged: two rules
    under one slug would make every deviation attached to it ambiguous.

    Raises
    ------
    ConformanceRegistryError
        When the name is already declared.
    """
    if any(existing.name == point.name for existing in _POINTS):
        raise ConformanceRegistryError(
            f"semantic point '{point.name}' is already declared; "
            "a slug carries one rule"
        )

    _POINTS.append(point)


def register_deviation(deviation: Deviation) -> None:
    """Record a departure, typically from an engine registering itself.

    The point has to exist first. A deviation on an unknown slug is accepted
    nowhere: it would sit in the registry, never be listed under any point and
    look exactly like an engine with nothing to declare.

    Raises
    ------
    ConformanceRegistryError
        When the point is not declared, or when this engine already has an
        entry on it.
    """
    semantic_point(deviation.point)

    for existing in _DEVIATIONS:
        if existing.engine == deviation.engine and existing.point == deviation.point:
            raise ConformanceRegistryError(
                f"engine '{deviation.engine}' already declares a deviation on "
                f"'{deviation.point}'"
            )

    _DEVIATIONS.append(deviation)


def assess_engine(engine: str) -> None:
    """State that ``engine`` has been reviewed against every declared point.

    Called after its deviations are registered, and meaning exactly that: the
    points it does not appear on, it honours. Without it an engine's silence
    stays silence, which is the honest default.
    """
    _ASSESSED.add(engine)


if __name__ == "__main__":  # pragma: no cover
    import sys

    print(describe(sys.argv[1] if len(sys.argv) > 1 else None))
