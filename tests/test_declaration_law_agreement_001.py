"""A mode's occurrence law is written twice, and the two writings have to agree.

An ``ObjMode2S`` declaration says its four hours in two fields:

    {"occ_law": {"cls": "delay", "time": [4.0]}, "occ_param": [4.0]}

The law carries the NATURE, the vector carries one number per common-cause
order. Neither is redundant, and until this module the two could disagree in
silence: ``ObjMode2S`` builds from the vector, so a document saying 4 in one
and 5 in the other built a four-hour mode -- while the typed law, the form a
reader of the document takes FIRST because it is the one that says what the
law is, said five. The document read back identical either way, so the only
fidelity check the seam can make without a second engine ("the rebuilt system
redeclares the same document") went green on a document that describes two
different systems.

Found by the COD3S Platform's interactive declarative seam bench, in the act of
proving the bench was sensitive: one of the numbers it altered in the document
changed no trajectory at all.

Two halves, and each is measured on its own here:

- **read back**, the law's parameter is written FROM the vector, so a document
  coming out of ``system_spec`` never disagrees with itself whatever the mode
  was constructed from;
- **read in**, a document whose two writings disagree is REFUSED, naming the
  mode, both fields and both values -- rather than resolved by a precedence
  rule that lives nowhere in the document.

The same holds of ``not_occ_law`` / ``not_occ_param`` on the return direction,
which is measured beside every claim rather than assumed symmetric.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from muscadet.declare import (
    MODE_LAW_PARAM_FIELDS,
    ComponentSpecError,
    check_failure_mode_spec,
    check_system_spec,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The law parameter of the occurrence direction, and the one of the return
#: direction, as the feature's running example spells them: a failure mode at
#: four hours whose document was altered to five in the law alone.
OCC_TIME = 4.0
NOT_OCC_RATE = 0.5

#: What the altered document said instead. Never equal to the above, which is
#: the whole of what a disagreement is.
ALTERED_TIME = 5.0
ALTERED_RATE = 9.0


#: Sentinel for an override that REMOVES a key rather than setting it. A
#: writing absent is a different declaration from a writing set to ``None``,
#: and both are cases this module has to be able to spell.
_ABSENT = object()


def mode_declaration(**overrides):
    """A two-state mode declaration carrying both writings, as data.

    No system: everything this module checks on the way IN is a property of the
    mapping alone, which is what ``check_failure_mode_spec`` promises and what
    makes these claims cost nothing.
    """
    spec = {
        "name": "A__fail",
        "kind": "two_state_mode",
        "cls": "ObjMode2S",
        "mode_name": "fail",
        "targets": ["A"],
        "target_name": "A",
        "occ_law": {"cls": "delay", "time": [OCC_TIME]},
        "occ_param_name": ["occ_time"],
        "occ_param": [OCC_TIME],
        "not_occ_law": {"cls": "exp", "rate": [NOT_OCC_RATE]},
        "not_occ_param_name": ["not_occ_rate"],
        "not_occ_param": [NOT_OCC_RATE],
    }
    spec.update(overrides)
    return {key: value for key, value in spec.items() if value is not _ABSENT}


def system_declaration_of(mode_spec):
    """The smallest system document holding ``mode_spec`` and its target."""
    return {
        "version": "1.0.0",
        "name": "LawAgreement",
        "components": {
            "A": {
                "name": "A",
                "cls": "ObjFlow",
                "flows": [{"cls": "FlowOut", "name": "f"}],
            },
            mode_spec["name"]: mode_spec,
        },
        "connections": [],
        "indicators": [],
    }


def run_probe(script):
    """Run ``script`` in a process of its own and return what it printed.

    PyCATSHOO forbids more than one live system per process, and the suite runs
    in one: a module keeping a system alive takes down every other module's
    with a ``PycException`` naming a system nobody there wrote. So every claim
    that needs a live one is measured here instead, and the reproduction below
    additionally tears its session down to rebuild from the document alone.

    The probe's stderr is the assertion message when it dies, so a probe that
    crashed does not surface as a test failing on a missing key.
    """
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        # PyCATSHOO greets and warns in French through its native layer, in the
        # locale encoding rather than in UTF-8, so a strict decode turns a
        # perfectly good probe into a ``UnicodeDecodeError`` on an accented
        # word. The result line itself is ASCII JSON.
        errors="replace",
    )
    assert completed.returncode == 0, completed.stderr

    for line in completed.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT ") :])

    raise AssertionError(
        f"probe printed no result:\n{completed.stdout}\n{completed.stderr}"
    )


# ---------------------------------------------------------------------------
# Read in: a disagreement is refused, by name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "law_key, param_key, altered",
    [
        ("occ_law", "occ_param", {"cls": "delay", "time": [ALTERED_TIME]}),
        ("not_occ_law", "not_occ_param", {"cls": "exp", "rate": [ALTERED_RATE]}),
    ],
)
def test_a_law_that_contradicts_its_parameter_vector_is_refused(
    law_key, param_key, altered
):
    """The running example's document, refused on the way in rather than half-read.

    Both directions, because the occurrence face and the repair face are two
    field pairs and a guard written on one of them is a guard on half the
    modes: a repair time silently wrong is a system that looks like it comes
    back and does not.
    """
    spec = mode_declaration(**{law_key: altered})

    with pytest.raises(ComponentSpecError) as refusal:
        check_system_spec(system_declaration_of(spec))

    message = str(refusal.value)
    assert "A__fail" in message, "the mode is named"
    assert law_key in message and param_key in message, "both fields are named"
    # Both values, so the reader does not have to open the document to learn
    # which of the two numbers their engine would have taken.
    assert str(altered[MODE_LAW_PARAM_FIELDS[altered["cls"]]][0]) in message
    assert str(spec[param_key][0]) in message


def test_the_refusal_says_which_number_muscadet_would_have_built():
    """A refusal saying only "they differ" would leave the modeller to
    rediscover the precedence this module exists to stop relying on."""
    spec = mode_declaration(occ_law={"cls": "delay", "time": [ALTERED_TIME]})

    with pytest.raises(ComponentSpecError) as refusal:
        check_system_spec(system_declaration_of(spec))

    message = str(refusal.value)
    assert "muscadet would build [4.0]" in message, message
    assert "[5.0]" in message, message


def test_the_refusal_follows_the_precedence_when_it_flips():
    """The vector does not always win, and a message that said so would be
    wrong exactly where it is hardest to check: ``ObjMode2S`` replaces a vector
    SHORTER than its targets by the law's own values, so a two-target mode
    handed one number builds the law's pair."""
    spec = mode_declaration(
        targets=["A", "B"],
        target_name="AB",
        occ_law={"cls": "exp", "rate": [0.1, 0.01]},
        occ_param=[0.1],
    )

    with pytest.raises(ComponentSpecError) as refusal:
        check_failure_mode_spec(spec)

    assert "muscadet would build [0.1, 0.01]" in str(refusal.value), refusal.value


def test_the_document_the_platform_emits_is_accepted():
    """The two writings in agreement, which is every document produced today.

    The guard has to be silent on the corpus it was added under, or it is not a
    guard on a defect but a change of format.
    """
    assert check_system_spec(system_declaration_of(mode_declaration())) is None


@pytest.mark.parametrize(
    "targets, law, params",
    [
        # A scalar is the one-order spelling of a one-entry vector, on either
        # side. Refusing that pair would refuse every hand-written model in the
        # README, which declares its laws with scalars.
        (["A"], {"cls": "delay", "time": OCC_TIME}, [OCC_TIME]),
        (["A"], {"cls": "delay", "time": [OCC_TIME]}, OCC_TIME),
        (["A"], {"cls": "delay", "time": OCC_TIME}, OCC_TIME),
        # An entry of the vector may be the TUPLE of that order's parameters,
        # spelled as a list in a document. The engine parametrises the law with
        # the FIRST of them, so that is what the law's own field holds and the
        # rest is a variable the law does not read.
        (["A"], {"cls": "delay", "time": [OCC_TIME]}, [[OCC_TIME, 1.0]]),
        # ``None`` is the explicit inactive-order marker, and it is a value like
        # any other: equal to itself, unequal to a number.
        (["A", "B"], {"cls": "exp", "rate": [0.1, None]}, [0.1, None]),
        # An int and a float are the same number, and JSON writes either.
        (["A"], {"cls": "delay", "time": [4]}, [4.0]),
    ],
)
def test_two_writings_that_say_the_same_thing_are_not_a_disagreement(
    targets, law, params
):
    spec = mode_declaration(targets=targets, occ_law=law, occ_param=params)
    assert check_failure_mode_spec(spec) == "A__fail"


@pytest.mark.parametrize(
    "targets, law, params",
    [
        # A length mismatch is a disagreement like any other: the two writings
        # do not even declare the same number of common-cause orders, and the
        # engine silently prefers the law when the vector is the shorter one.
        (["A", "B"], {"cls": "exp", "rate": [0.1, 0.01]}, [0.1]),
        (["A"], {"cls": "exp", "rate": [0.1]}, [0.1, 0.01]),
        # ``None`` against a number, in both directions. Zero is not an absent
        # order under a delay law -- a delay of 0 is IMMEDIATE -- so reading the
        # two as equal would erase the distinction the marker exists for.
        (["A"], {"cls": "exp", "rate": [None]}, [0.0]),
        (["A"], {"cls": "exp", "rate": [0.0]}, [None]),
    ],
)
def test_a_disagreement_is_a_disagreement_whatever_its_shape(targets, law, params):
    with pytest.raises(ComponentSpecError, match="disagree"):
        check_failure_mode_spec(
            mode_declaration(targets=targets, occ_law=law, occ_param=params)
        )


def test_the_self_hosted_shape_has_one_writing_and_is_not_a_disagreement():
    """``targets=None`` builds no parameter variable at all, so the law is the
    only writing such a mode has and an empty vector does not contradict it."""
    spec = mode_declaration(
        targets=None,
        aut_name="ev",
        target_name=_ABSENT,
        occ_param=[],
        not_occ_param=[],
        occ_param_name=[],
        not_occ_param_name=[],
    )
    assert check_failure_mode_spec(spec) == "A__fail"


def test_a_writing_simply_absent_is_not_a_disagreement():
    """The engine derives the vector from the law when the vector is short or
    missing, which is the one case where there is nothing to disagree with."""
    spec = mode_declaration(occ_param=_ABSENT, not_occ_param=_ABSENT)
    assert check_failure_mode_spec(spec) == "A__fail"


def test_an_objfm_facade_has_no_law_to_disagree_with():
    """The façade family carries its law in its CLASS -- ``ObjFMDelay`` draws a
    delay -- so its declaration has one writing and this check has nothing to do
    on it. Pinned so a guard written against the ``occ_*`` spelling never
    silently grows a second opinion on ``failure_param``."""
    assert (
        check_failure_mode_spec(
            {
                "name": "A__fm",
                "kind": "two_state_mode",
                "cls": "ObjFMDelay",
                "fm_name": "fm",
                "targets": ["A"],
                "failure_param": [OCC_TIME],
            }
        )
        == "A__fm"
    )


# ---------------------------------------------------------------------------
# Read back: the law says what the mode RUNS ON
#
# Everything below raises a live system, so it runs in a process of its own:
# PyCATSHOO forbids more than one per process, and a module that keeps one
# alive takes down every other module's with a ``PycException`` naming a system
# nobody here wrote. Both probes print one JSON line under ``RESULT``.
# ---------------------------------------------------------------------------

_CONTRADICTORY_BUILD = r"""
import json

import muscadet
from muscadet.declare import component_spec


class LawAgreementProbeTarget(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowOut", name="f", var_prod_default=True))


# A mode CONSTRUCTED with two disagreeing writings. The constructor is the one
# place the disagreement can still be expressed -- the document is refused --
# so it is where the read is measured.
system = muscadet.System(name="LawAgreementRef")
system.add_component(name="A", cls="LawAgreementProbeTarget")
mode = system.add_component(
    cls="ObjMode2S",
    mode_name="fail",
    targets=["A"],
    occ_law={"cls": "delay", "time": 5.0},
    occ_param=[4.0],
    not_occ_law={"cls": "exp", "rate": 9.0},
    not_occ_param=[0.5],
    occ_effects={"f_fed_available_out": False},
)

print("RESULT " + json.dumps({
    "spec": component_spec(mode),
    # What the automata are really wired to, read off the parameter variables
    # the engine created, and what the mode still holds as its law spec.
    "wired": {
        "occ": mode.variable("occ_time").value(),
        "not_occ": mode.variable("not_occ_rate").value(),
    },
    "held_law": {"occ": mode.occ_law.time, "not_occ": mode.not_occ_law.rate},
}))
"""


@pytest.fixture(scope="module")
def contradictory_build():
    return run_probe(_CONTRADICTORY_BUILD)


def test_the_engine_wires_the_parameter_vector_and_not_the_law(contradictory_build):
    """The precedence, measured rather than believed: it is what makes the
    refusal's "muscadet would build [4.0]" a fact about this engine.

    And the law spec the mode was handed is kept whole beside it, which is
    exactly what the read must not transport.
    """
    assert contradictory_build["wired"] == {"occ": OCC_TIME, "not_occ": NOT_OCC_RATE}
    assert contradictory_build["held_law"] == {
        "occ": ALTERED_TIME,
        "not_occ": ALTERED_RATE,
    }


def test_the_declaration_writes_the_law_from_the_vector(contradictory_build):
    """The claim of the read half: one document, one number, twice."""
    spec = contradictory_build["spec"]
    assert spec["occ_law"] == {"cls": "delay", "time": [OCC_TIME]}
    assert spec["occ_param"] == [OCC_TIME]
    assert spec["not_occ_law"] == {"cls": "exp", "rate": [NOT_OCC_RATE]}
    assert spec["not_occ_param"] == [NOT_OCC_RATE]


def test_the_law_keeps_its_nature_and_only_its_numbers_are_rewritten(
    contradictory_build,
):
    """The vector has no nature of its own, which is why neither writing can be
    dropped: a document holding ``[4.0]`` alone says nothing about whether four
    is a delay, a rate or a probability."""
    spec = contradictory_build["spec"]
    assert spec["occ_law"]["cls"] == "delay"
    assert spec["not_occ_law"]["cls"] == "exp"


def test_what_is_read_back_is_accepted_by_the_check(contradictory_build):
    """The two halves close on each other: what the read writes is what the
    check accepts, so ``system_declaration`` -- which reads then checks --
    cannot emit a document it would refuse."""
    assert check_failure_mode_spec(contradictory_build["spec"]) == "A__fail"


# ---------------------------------------------------------------------------
# The ticket's own reproduction, end to end
# ---------------------------------------------------------------------------

_REPRODUCTION = r"""
import json

import cod3s
import muscadet
from muscadet.declare import build_system, ComponentSpecError
from muscadet.engine import system_declaration


class LawAgreementProbeTarget(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowOut", name="f", var_prod_default=True))


system = muscadet.System(name="LawAgreementProbe")
system.add_component(name="A", cls="LawAgreementProbeTarget")
system.add_component(
    cls="ObjMode2S",
    mode_name="fail",
    targets=["A"],
    occ_law={"cls": "delay", "time": 4.0},
    # A two-state mode always builds both directions, so the return one is
    # declared too: an ObjMode2S with no not_occ law refuses to build at all.
    not_occ_law={"cls": "exp", "rate": 0.5},
    occ_effects={"f_fed_available_out": False},
)

document = json.loads(json.dumps(system_declaration(system)))
system.deleteSys()
cod3s.terminate_session()

# The reproduction the ticket gives: alter the law alone, hand the document
# back, and see whether anything says so.
contradictory = json.loads(json.dumps(document))
contradictory["components"]["A__fail"]["occ_law"]["time"] = [5.0]

# No system handed over, and none created: ``build_system`` opens on the
# check, so the refusal lands before the first component exists. Were it
# accepted, a system WOULD be created here and the rebuild below would die on
# PyCATSHOO's one-system-per-process rule, which is a loud enough failure.
try:
    build_system(contradictory)
except ComponentSpecError as refusal:
    refused = str(refusal)
else:
    refused = None

rebuilt = build_system(document, system=muscadet.System(name="LawAgreementRebuilt"))
redeclared = json.loads(json.dumps(system_declaration(rebuilt)))

print("RESULT " + json.dumps({
    "declared": document["components"]["A__fail"],
    "redeclared": redeclared["components"]["A__fail"],
    "refused": refused,
}))
"""


@pytest.fixture(scope="module")
def reproduction():
    return run_probe(_REPRODUCTION)


def test_the_contradictory_document_no_longer_builds_anything(reproduction):
    """What the ticket measured: ``build_system`` took the document, built 4.0,
    and said nothing about the 5.0 it had just read."""
    assert reproduction["refused"], "the contradictory document was accepted"
    assert "A__fail" in reproduction["refused"]
    assert "occ_law" in reproduction["refused"]
    assert "occ_param" in reproduction["refused"]


def test_an_agreeing_document_still_round_trips_identical(reproduction):
    """And the corpus is untouched: what comes out of ``system_declaration``
    rebuilds and redeclares itself, key for key."""
    assert reproduction["redeclared"] == reproduction["declared"]


def test_the_declared_document_says_the_same_number_twice(reproduction):
    """The shape a platform export carries, now guaranteed rather than
    observed: the law's parameter IS the parameter vector."""
    declared = reproduction["declared"]
    assert declared["occ_law"] == {"cls": "delay", "time": [4.0]}
    assert declared["occ_param"] == [4.0]
