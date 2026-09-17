"""A model declares whether it wants the generated indicator set, and says so.

An engine reading a muscadet declaration can emit two kinds of indicator: the
ones the document DECLARES, and a set it GENERATES, one per observable
variable, named ``{component}_{variable}``. Which of the two a model got used
to depend on the route that assembled it -- on whether a continuous construct
happened to sit somewhere in the model -- so removing a tank from a model
removed the observations of everything else in it, and nothing said so.

The engine closed that with a model-level key
(:data:`muscadet.declare.GENERATED_INDICATORS`), absent meaning false. This
module pins muscadet's half: a system can ASK, and the document it exports
says what it asked for.

Two arbitrations are pinned here rather than left to reading the code:

* **The key is written on every document**, whatever its value, as
  ``transmits`` is on a capacity. Writing it only when it departs from the
  default would leave the platform's reference fingerprints untouched, which
  is a genuine cost paid once; it would also put a reader back in front of a
  document that says nothing and a default it cannot see, which is the exact
  silence this key was introduced to break.
* **A system says true when its author says nothing; a document says false.**
  The asymmetry is deliberate and it is the one the engine's own
  muscadet-compatible writer settled on: a system built object by object is
  the muscadet authoring surface, whose models have always been observed
  variable by variable, so defaulting it to false would take those
  observations away from every existing model, silently -- the regression this
  whole key exists to prevent. A document is read as it is written, and one
  that asks for nothing gets nothing.

The spelling is not muscadet's to choose: ``pyraichu.indicators`` reads this
exact string, and the model level is an OPEN vocabulary on both sides, so a
key spelled otherwise is accepted, dropped, and never mentioned again.

One live system per process, so the claim that needs a SECOND one -- the
constructor keyword, which ``cod3s.PycSystem.__init__`` would otherwise
swallow without a word -- runs in a process of its own.
"""

import json
import subprocess
import sys
from pathlib import Path

import cod3s
import pytest

import muscadet
from muscadet.declare import (
    GENERATED_INDICATORS,
    GENERATED_INDICATORS_DEFAULT,
    SYSTEM_SPEC_VERSION,
    SystemSpecError,
    build_system,
    check_system_spec,
    generated_indicators,
    system_spec,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: A system built with the keyword, exported, and nothing else: the one claim
#: that needs a second live system. ``cod3s.PycSystem.__init__(self, name,
#: **kwrds)`` accepts and drops every keyword it does not know, so a surface
#: that merely LOOKED like an API would pass a test written on the attribute.
_CONSTRUCTOR_KEYWORD = """
import json

import muscadet
from muscadet.declare import GENERATED_INDICATORS, system_spec

system = muscadet.System(name="Asked", generated_indicators=False)
system.add_component(
    cls="ObjFlow", name="A", flows=[{"name": "f", "var_prod_default": True}]
)

print("RESULT " + json.dumps({
    "attribute": system.generated_indicators,
    "document": system_spec(system)[GENERATED_INDICATORS],
}))
"""


def run_probe(script, *args):
    """Run ``script`` in a process of its own and return what it printed."""
    completed = subprocess.run(
        [sys.executable, "-c", script, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    for line in completed.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT ") :])

    raise AssertionError(
        f"probe printed no result:\n{completed.stdout}\n{completed.stderr}"
    )


@pytest.fixture(scope="module")
def the_system():
    """One system, built saying nothing about what it wants observed."""
    system = muscadet.System(name="Observed")
    system.add_component(
        cls="ObjFlow", name="A", flows=[{"name": "f", "var_prod_default": True}]
    )
    return system


# ---------------------------------------------------------------------------
# What a system wants, and what its document says
# ---------------------------------------------------------------------------


def test_a_system_that_says_nothing_wants_the_generated_set(the_system):
    """The authoring default, which is what keeps existing models observed."""
    assert GENERATED_INDICATORS_DEFAULT is True
    assert the_system.generated_indicators is True


def test_the_key_is_spelled_as_the_engine_reads_it():
    """``pyraichu.indicators.GENERATED_INDICATORS`` is this same string.

    Pinned as a literal because the model level is an open vocabulary on both
    sides: a key spelled otherwise is accepted by the reader, dropped, and
    reported by nobody -- the model simply observes less than it asked for.
    """
    assert GENERATED_INDICATORS == "generated_indicators"


def test_the_document_says_what_the_system_wants(the_system):
    spec = system_spec(the_system)

    assert spec[GENERATED_INDICATORS] is True


def test_the_key_is_written_whatever_its_value(the_system):
    """The arbitration: always written, never left to the reader's default."""
    the_system.generated_indicators = False
    spec = system_spec(the_system)

    assert GENERATED_INDICATORS in spec
    assert spec[GENERATED_INDICATORS] is False

    the_system.generated_indicators = True
    assert system_spec(the_system)[GENERATED_INDICATORS] is True


def test_the_document_is_json(the_system):
    """A document carrying a live object is not a document two engines share."""
    spec = json.loads(json.dumps(system_spec(the_system)))

    assert spec[GENERATED_INDICATORS] is True


def test_the_constructor_keyword_is_honoured():
    """A second live system, hence a second process.

    The claim is narrow and it is the one that would rot: the keyword reaches
    the system rather than being swallowed by the cod3s base, and the document
    it exports carries what was asked for.
    """
    result = run_probe(_CONSTRUCTOR_KEYWORD)

    assert result == {"attribute": False, "document": False}


# ---------------------------------------------------------------------------
# What a value that is not a boolean gets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_a_value_that_is_not_a_boolean_is_refused_at_the_assignment(the_system, value):
    """``"false"`` is true to Python and false to whoever wrote it.

    Refused where it is written, so the message names the line that wrote it
    rather than a document nobody typed.
    """
    with pytest.raises(SystemSpecError) as refusal:
        the_system.generated_indicators = value

    assert GENERATED_INDICATORS in str(refusal.value)
    assert repr(value) in str(refusal.value)

    # And the system kept what it had, rather than half-taking the assignment.
    assert the_system.generated_indicators is True


@pytest.mark.parametrize("value", ["false", 1, [], {"yes": True}])
def test_a_document_carrying_something_else_is_refused_by_the_check(the_system, value):
    """Same refusal from the document side, where a hand-written one arrives.

    The engine refuses it too, in its own vocabulary; refusing here is what
    gives the author a message about the document in front of them.
    """
    spec = dict(system_spec(the_system), **{GENERATED_INDICATORS: value})

    with pytest.raises(SystemSpecError) as refusal:
        check_system_spec(spec)

    assert GENERATED_INDICATORS in str(refusal.value)


# ---------------------------------------------------------------------------
# What a document means, including when it says nothing
# ---------------------------------------------------------------------------


def test_a_document_that_says_nothing_means_false():
    """The format's own reading, shared with the engine that reads it."""
    assert generated_indicators({}) is False
    assert generated_indicators({GENERATED_INDICATORS: True}) is True
    assert generated_indicators({GENERATED_INDICATORS: False}) is False


def test_a_document_written_before_this_key_builds_and_re_exports_false(
    the_system,
):
    """A 1.0.1 document rebuilds unchanged, and does not gain what it never asked.

    The system's authoring default is true; the DOCUMENT decides here, or a
    round trip would quietly upgrade what an old document observes.
    """
    older = {
        "version": "1.0.1",
        "name": "Older",
        "components": {
            "B": {
                "name": "B",
                "cls": "ObjFlow",
                "flows": [{"cls": "FlowOut", "name": "f", "var_prod_default": True}],
            }
        },
        "connections": [],
        "indicators": [],
    }

    check_system_spec(older)
    build_system(older, system=the_system)

    assert the_system.generated_indicators is False
    assert system_spec(the_system)[GENERATED_INDICATORS] is False


def test_a_document_that_asks_is_read_back_onto_the_system(the_system):
    """The other half of the round trip: what was asked survives the rebuild."""
    asking = {
        "version": SYSTEM_SPEC_VERSION,
        "name": "Asking",
        GENERATED_INDICATORS: True,
        "components": {
            "C": {
                "name": "C",
                "cls": "ObjFlow",
                "flows": [{"cls": "FlowOut", "name": "f", "var_prod_default": True}],
            }
        },
        "connections": [],
        "indicators": [],
    }

    build_system(asking, system=the_system)

    assert the_system.generated_indicators is True
    assert system_spec(the_system)[GENERATED_INDICATORS] is True


def test_the_version_moved_by_a_patch(the_system):
    """An optional field carrying a default is a patch, and the major holds.

    The major is what a reader refuses on, and refusing a document because it
    now states what it observes would be a reader refusing the very thing this
    key was added to tell it.
    """
    version = tuple(int(part) for part in SYSTEM_SPEC_VERSION.split("."))

    assert version[0] == 1
    assert version > (1, 0, 1)
    assert system_spec(the_system)["version"] == SYSTEM_SPEC_VERSION


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
