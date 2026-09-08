"""The conformance registry: what it declares, and what it must never touch.

Three properties are checked here, and only the first is about content.

1. **The divergences are declared and legible.** The observation gap at a
   transition instant, and the three interactive ones, with what muscadet
   defines opposite what the engine does instead.

2. **Reading it costs nothing.** No system, no engine, no run -- which is why
   this module builds nothing and therefore carries no ``test_delete``, exactly
   as ``test_declaration_keys_001.py`` and the importer tests do. The one
   subprocess here runs ``python -m muscadet.conformance``, in a process of its
   own, and is the literal form of the promise: a modeller with no platform
   reads the record from a shell.

3. **It cannot reach a launch decision.** That is the property worth the most
   care, because it cannot be tested by exercising the API: a function nobody
   calls proves nothing about the day someone calls it. It is tested
   structurally instead -- the package is walked and the registry must be a
   LEAF, imported by ``muscadet/__init__.py`` for the namespace and by nothing
   else. Nothing reads it, so nothing can refuse on it, and the day a module
   starts reading it this file says so before a divergence has had the chance
   to look like a missing capability.
"""

import ast
import io
import pathlib
import subprocess
import sys
import token
import tokenize

import pydantic
import pytest

import muscadet
from muscadet import conformance
from muscadet.conformance import (
    ENGINE_PYCATSHOO,
    ENGINE_RAICHU,
    POINT_ADVANCE_TO_DATE,
    POINT_ARMED_TRANSITION_DATE,
    POINT_INTERACTIVE_STEP_GRANULARITY,
    POINT_TRANSITION_INSTANT_OBSERVATION,
    ConformanceRegistryError,
    Deviation,
    SemanticPoint,
)

#: The package under test, on disk: the structural checks read the source
#: rather than the imported modules, so an import hidden inside a function is
#: caught too.
CR_PACKAGE_ROOT = pathlib.Path(muscadet.__file__).parent

#: The only files allowed to name the registry, by path within the package:
#: the top-level ``__init__`` names it inside its PEP 562 ``__getattr__``, so
#: ``muscadet.conformance`` resolves on first use and importing muscadet does
#: not reach it at all (checked at runtime below, which is the stronger of the
#: two forms). The module names itself, unavoidably. The sub-package
#: ``__init__`` files are deliberately NOT exempt, which is why these are paths
#: and not bare file names.
CR_ALLOWED_READERS = frozenset({"__init__.py", "conformance.py"})

#: The three interactive divergences the chantier asked to see written down,
#: as opposed to compensated by hand in a worker nobody reads.
CR_INTERACTIVE_POINTS = (
    POINT_ARMED_TRANSITION_DATE,
    POINT_INTERACTIVE_STEP_GRANULARITY,
    POINT_ADVANCE_TO_DATE,
)


@pytest.fixture
def cr_isolated_registry():
    """Give the module-level registry back exactly as it was.

    The registration functions mutate module state by design -- that is how an
    engine muscadet cannot know declares itself. A test that registered
    something and walked away would leave it visible to every later test in the
    process, and the failure would land somewhere else entirely.
    """
    points = list(conformance._POINTS)
    deviations = list(conformance._DEVIATIONS)
    assessed = set(conformance._ASSESSED)

    yield

    conformance._POINTS[:] = points
    conformance._DEVIATIONS[:] = deviations
    conformance._ASSESSED.clear()
    conformance._ASSESSED.update(assessed)


def cr_source_files():
    """Every Python file of the package, importers and knowledge bases included."""
    return sorted(CR_PACKAGE_ROOT.rglob("*.py"))


# ----------------------------------------------------------------------
# 1. The observation gap at a transition instant
# ----------------------------------------------------------------------


def test_the_observation_gap_is_declared_against_the_reference_engine():
    """PyCATSHOO reads the instant BEFORE, and muscadet defines it as AFTER.

    The entry the whole registry was opened for, and the one that shows the
    registry is not a comparison in muscadet's own favour: the engine it finds
    non-conformant is the engine it is written against.
    """
    declared = conformance.deviations(
        ENGINE_PYCATSHOO, POINT_TRANSITION_INSTANT_OBSERVATION
    )

    assert len(declared) == 1

    deviation = declared[0]
    point = conformance.semantic_point(POINT_TRANSITION_INSTANT_OBSERVATION)

    assert "AFTER" in point.rule
    assert "BEFORE" in deviation.behaviour

    # Nothing puts the reference engine back on muscadet's reading: it is a
    # third-party library, so the gap is permanent and reaches the result.
    assert deviation.compensation is None

    assert ENGINE_PYCATSHOO == conformance.REFERENCE_ENGINE


def test_the_engine_that_honours_the_instant_is_named_as_honouring_it():
    """RAICHU carries no entry on that point, and that reads as conformance.

    Checked through :func:`conformant_points` rather than through the absence
    of a deviation: an absence is also what an engine nobody assessed looks
    like, and the two must not be told apart by squinting.
    """
    assert (
        conformance.deviations(ENGINE_RAICHU, POINT_TRANSITION_INSTANT_OBSERVATION)
        == ()
    )
    assert POINT_TRANSITION_INSTANT_OBSERVATION in conformance.conformant_points(
        ENGINE_RAICHU
    )


def test_the_gap_is_readable_without_building_anything():
    """``describe`` renders the rule, the behaviour and where it was settled.

    This test module has built no system to get here, which is the point: the
    record is consulted BEFORE choosing an engine, so needing an engine to read
    it would defeat it.
    """
    text = conformance.describe(ENGINE_PYCATSHOO)

    assert POINT_TRANSITION_INSTANT_OBSERVATION in text
    assert "muscadet defines" in text
    assert "this engine" in text
    assert "ADR-2026-09-01" in text


def test_reading_the_registry_loads_no_engine():
    """The module imports pydantic and the standard library, nothing else.

    A registry that pulls in ``Pycatshoo`` to be read is a registry a modeller
    cannot consult while deciding whether to install it.
    """
    tree = ast.parse((CR_PACKAGE_ROOT / "conformance.py").read_text())

    imported = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # A relative import has no module name of its own to look at.
            imported.add("." if node.level else (node.module or "").split(".")[0])

    assert "Pycatshoo" not in imported
    assert "cod3s" not in imported
    assert "." not in imported, "the registry imports a sibling of muscadet"


# ----------------------------------------------------------------------
# 2. Distinct from the capability matrix, and unable to refuse anything
# ----------------------------------------------------------------------


def test_no_module_of_muscadet_reads_the_registry():
    """The structural guarantee: a leaf cannot be consulted by a decision.

    Source-level rather than import-level, so an import buried in a function
    body is caught as readily as one at the top. ``__init__.py`` is exempt
    because it binds the namespace and reads nothing from it.

    Read from the NAME tokens, so the docstrings that discuss conformance --
    this repo has a few -- do not raise a false alarm. The blind spot is the
    dotted path handed to ``importlib`` as a string, which nothing here does
    and which no reviewer would let through unnoticed anyway.
    """
    readers = []

    for path in cr_source_files():
        if str(path.relative_to(CR_PACKAGE_ROOT)) in CR_ALLOWED_READERS:
            continue

        names = {
            tok.string
            for tok in tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
            if tok.type == token.NAME
        }

        if "conformance" in names:
            readers.append(str(path.relative_to(CR_PACKAGE_ROOT)))

    assert readers == [], (
        "the conformance registry is read inside muscadet: "
        "a divergence that reaches a code path can end up refusing a run, "
        "which is exactly what keeping it out of the capability matrix "
        f"prevents (readers: {readers})"
    )


def test_importing_muscadet_does_not_reach_the_registry():
    """The runtime form of the same guarantee, and the stronger one.

    The source walk above can be argued with; ``sys.modules`` cannot. Importing
    the library must not pull the registry in, because a module that is never
    loaded is a module no decision can consult. ``muscadet.conformance``
    resolves on first ACCESS, through the package's ``__getattr__``.

    In a subprocess, necessarily: this process imported the registry on line
    one of the file.
    """
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import muscadet, sys; print('muscadet.conformance' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False"


def test_an_engine_the_registry_never_heard_of_is_not_refused():
    """The permissive answer, opposite the capability matrix's restrictive one.

    The matrix answers "covers nothing" for an unknown engine, because it
    guards a launch. This one answers "nothing to report", because it guards
    nothing -- and says so through :func:`is_assessed` rather than by letting
    an empty tuple pass for a clean bill of health.
    """
    assert conformance.deviations("an-engine-nobody-wrote") == ()
    assert conformance.conformant_points("an-engine-nobody-wrote") == ()
    assert conformance.is_assessed("an-engine-nobody-wrote") is False

    text = conformance.describe("an-engine-nobody-wrote")

    assert "No conformance assessment on record" in text
    assert "not a clean bill of health" in text


def test_the_registry_exposes_no_launch_verdict():
    """No public name answers "may this engine run this study".

    A naming guard, and a deliberate one: the day someone adds ``supports`` or
    ``may_run`` here, the registry has started answering the matrix's question
    and the two have begun to merge. Failing on the name is the cheapest place
    to notice.
    """
    forbidden = ("support", "refus", "allow", "may_run", "can_run", "capab")

    offenders = [
        name
        for name in dir(conformance)
        if not name.startswith("_") and any(word in name.lower() for word in forbidden)
    ]

    assert offenders == []


def test_the_two_vocabularies_share_no_slug():
    """A semantic point is never spelt like a capability marker.

    muscadet's own capability declaration is the ``_SUPPORTS_*`` family of the
    COD3S Platform importer, which the platform probes to know what this
    muscadet can build. A slug appearing in both would make the two registries
    joinable by accident, which is the first step towards being read as one.
    """
    importer = (CR_PACKAGE_ROOT / "importers" / "cod3s_platform.py").read_text()

    markers = {
        line.split("=")[0].strip()
        for line in importer.splitlines()
        if line.startswith("_SUPPORTS_")
    }

    assert markers, "the capability markers moved; this check no longer checks anything"

    for point in conformance.semantic_points():
        assert point.name.upper() not in {m.lstrip("_") for m in markers}
        assert f"_SUPPORTS_{point.name.upper()}" not in markers


# ----------------------------------------------------------------------
# 3. A library user, no platform anywhere
# ----------------------------------------------------------------------


def test_the_namespace_is_reachable_from_the_package():
    """``import muscadet`` is enough; nothing else has to be found first.

    Run in a process of its own, because this one has already imported the
    registry by hand at the top of the file and would prove nothing about a
    reader who has not.
    """
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import muscadet; print(muscadet.conformance.describe('raichu'))",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert POINT_ADVANCE_TO_DATE in completed.stdout

    assert muscadet.conformance is conformance


def test_a_shell_prints_the_record_of_the_chosen_engine():
    """``python -m muscadet.conformance raichu``, in a process of its own.

    The consultation a modeller actually performs, end to end: no platform, no
    model, no run, and the exit status of a command they can put in a script.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "muscadet.conformance", ENGINE_RAICHU],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr

    for point in CR_INTERACTIVE_POINTS:
        assert point in completed.stdout

    assert "capability matrix" in completed.stdout


def test_every_engine_is_listed_when_none_is_named():
    """The whole record, for a reader who has not chosen yet."""
    text = conformance.describe()

    assert ENGINE_PYCATSHOO in text
    assert ENGINE_RAICHU in text
    assert set(conformance.engines()) == {ENGINE_PYCATSHOO, ENGINE_RAICHU}


# ----------------------------------------------------------------------
# 4. The three interactive divergences
# ----------------------------------------------------------------------


@pytest.mark.parametrize("point_name", CR_INTERACTIVE_POINTS)
def test_the_interactive_divergence_is_written_down(point_name):
    """Each of the three is an entry, not a comment in a worker.

    They were compensated by hand in the platform's interactive worker, where
    nobody choosing an engine ever meets them.
    """
    declared = conformance.deviations(ENGINE_RAICHU, point_name)

    assert len(declared) == 1

    deviation = declared[0]

    assert deviation.behaviour
    assert deviation.consequence
    assert conformance.semantic_point(point_name).rule


@pytest.mark.parametrize("point_name", CR_INTERACTIVE_POINTS)
def test_the_interactive_divergence_says_the_library_user_is_uncovered(point_name):
    """The compensation names where it lives, and where it does not.

    The load-bearing half of the entry, and the reason the registry is held
    library side at all: the platform's worker absorbs these three, so a
    platform user never meets them and a Python user meets nothing else. An
    entry that stopped at "compensated" would warn precisely the audience it
    does not concern.
    """
    deviation = conformance.deviations(ENGINE_RAICHU, point_name)[0]

    assert deviation.compensation is not None
    assert "Nothing compensates it for a library user" in deviation.compensation


def test_the_advance_to_a_date_entry_says_why_it_is_not_a_matrix_line():
    """The one entry that could plausibly have been a capability, and is not.

    A missing primitive looks like a missing capability. It is not one here:
    the muscadet interface serves the capability by emulating it, so a matrix
    line would refuse an engine for something that works. What survives is that
    the emulation is built out of the single-transition step above, and that is
    a registry statement.
    """
    deviation = conformance.deviations(ENGINE_RAICHU, POINT_ADVANCE_TO_DATE)[0]

    assert "capability matrix" in deviation.consequence
    assert POINT_INTERACTIVE_STEP_GRANULARITY in {
        d.point for d in conformance.deviations(ENGINE_RAICHU)
    }


# ----------------------------------------------------------------------
# 5. The registry holds together
# ----------------------------------------------------------------------


def test_every_deviation_points_at_a_declared_point():
    """No entry hangs off a slug nobody declared, where it would list nowhere."""
    declared = {point.name for point in conformance.semantic_points()}

    for deviation in conformance.deviations():
        assert deviation.point in declared


@pytest.mark.parametrize("engine", [ENGINE_PYCATSHOO, ENGINE_RAICHU])
def test_an_assessed_engine_answers_on_every_point(engine):
    """Assessed means reviewed against all of them: honoured or departed from.

    The partition is what makes :func:`conformant_points` worth reading. Add a
    point without assessing the engines against it and this fails, rather than
    silently promoting them to conformant on something nobody checked.
    """
    assert conformance.is_assessed(engine)

    covered = set(conformance.conformant_points(engine)) | {
        deviation.point for deviation in conformance.deviations(engine)
    }

    assert covered == {point.name for point in conformance.semantic_points()}


def test_the_listing_order_is_the_report_order():
    """Two consultations read identically, whatever is installed."""
    order = [point.name for point in conformance.semantic_points()]
    listed = [deviation.point for deviation in conformance.deviations()]

    assert listed == sorted(listed, key=order.index)


def test_an_unknown_point_is_refused_rather_than_answered_with_nothing():
    """A misspelt slug must not read as "muscadet has decided nothing here"."""
    with pytest.raises(ConformanceRegistryError, match="unknown semantic point"):
        conformance.semantic_point("transition_instant_observations")


def test_an_entry_is_frozen():
    """A record nobody can edit in place, having read it out of the registry."""
    deviation = conformance.deviations(ENGINE_PYCATSHOO)[0]

    with pytest.raises(pydantic.ValidationError):
        deviation.behaviour = "something else"


# ----------------------------------------------------------------------
# 6. An engine muscadet cannot know declares itself
# ----------------------------------------------------------------------


def test_a_third_engine_registers_without_touching_muscadet(cr_isolated_registry):
    """The extension the ADR asks for, on the conformance axis too.

    muscadet imports no engine, so a third one has to be able to add its own
    record from outside. What it cannot do is author the POINTS it is judged
    on: those stay muscadet's, or the registry becomes self-certification.
    """
    conformance.register_deviation(
        Deviation(
            engine="a-third-engine",
            point=POINT_ADVANCE_TO_DATE,
            behaviour="Advances to the date, then rounds it to its own grid.",
            consequence="A stop lands near the asked date, not on it.",
            compensation=None,
            source="its own integration notes",
        )
    )
    conformance.assess_engine("a-third-engine")

    declared = conformance.deviations("a-third-engine")

    assert [d.point for d in declared] == [POINT_ADVANCE_TO_DATE]
    assert conformance.is_assessed("a-third-engine")
    assert POINT_TRANSITION_INSTANT_OBSERVATION in conformance.conformant_points(
        "a-third-engine"
    )
    assert "a-third-engine" in conformance.engines()


def test_a_point_muscadet_has_not_met_can_be_declared(cr_isolated_registry):
    """An integration that had to settle a meaning muscadet has not reached."""
    conformance.register_semantic_point(
        SemanticPoint(
            name="snapshot_restore_exactness",
            rule="A restored snapshot replays the same trajectory, bit for bit.",
            rationale="A replay that drifts cannot be used to explain a run.",
            source="its own integration notes",
        )
    )

    assert conformance.semantic_point("snapshot_restore_exactness").rule


def test_a_deviation_on_an_undeclared_point_is_refused(cr_isolated_registry):
    """It would sit in the registry, list under nothing, and look like silence."""
    with pytest.raises(ConformanceRegistryError, match="unknown semantic point"):
        conformance.register_deviation(
            Deviation(
                engine="a-third-engine",
                point="a_point_nobody_declared",
                behaviour="...",
                consequence="...",
                source="...",
            )
        )


def test_one_slug_carries_one_rule(cr_isolated_registry):
    """Re-declaring a point is refused rather than merged."""
    with pytest.raises(ConformanceRegistryError, match="already declared"):
        conformance.register_semantic_point(
            conformance.semantic_point(POINT_ADVANCE_TO_DATE)
        )


def test_an_engine_declares_one_deviation_per_point(cr_isolated_registry):
    """Two entries on one point would each be half the story."""
    with pytest.raises(ConformanceRegistryError, match="already declares a deviation"):
        conformance.register_deviation(
            Deviation(
                engine=ENGINE_RAICHU,
                point=POINT_ADVANCE_TO_DATE,
                behaviour="...",
                consequence="...",
                source="...",
            )
        )
