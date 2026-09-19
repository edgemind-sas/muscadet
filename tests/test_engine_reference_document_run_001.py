"""A document runs on PyCATSHOO, and what ran reports the document it came from.

:func:`muscadet.reference_simulate` is the reference engine's document entry
point, and it is shaped exactly like the runner a registered engine declares:
a declaration goes in, a run comes out. That shape is the point. Until the
switch, a caller holding a document could run it on RAICHU with one call and had
nothing of the kind for PyCATSHOO -- it had to rebuild a system by hand and then
run the system, so the two engines were reached by two different gestures and
only one of them was a seam.

What this module pins is the loop closing on itself, which is a stronger
statement than either half:

    system -> document -> PyCATSHOO -> the document that run reports

A system is declared, the session is torn down, and the document alone is handed
back to the reference engine. The system the engine built then reports, at its
own run, the very document it was built from. Anything the declaration drops on
the way out, or invents on the way in, separates the two ends.

**The session is torn down between the two, because PyCATSHOO forbids more than
one live system per process** -- the constraint that makes a document worth
having in the first place: two documents compare without building anything.

``source_cls`` is the one key excluded, and it is excluded for what it means
rather than for convenience: it names the class a declaration was READ BACK
from, and a system built from a document has the document for origin. Reporting
``ObjFlow`` there is truthful, not lossy. Two components are the same when they
declare the same thing, not when they remember the same ancestor.
"""

import json

import cod3s
import pytest

import muscadet
import muscadet.kb.rbd  # noqa: F401 -- registers Source / Block / Target

FLOW = "is_ok"
FEARED_EVENT = "EVT_LOSS"
RUN_PARAMS = {"nb_runs": 5, "schedule": [0.0, 5.0, 10.0], "seed": 4242}


def _without_provenance(document):
    """The document, minus the class each component was read back from."""
    stripped = dict(document)
    stripped["components"] = {
        name: {k: v for k, v in spec.items() if k != "source_cls"}
        for name, spec in document["components"].items()
    }
    return stripped


@pytest.fixture(scope="module")
def the_run_from_a_document():
    """Declare a system, tear it down, and run the document on its own."""
    original = muscadet.System(name="RefDocRun")
    original.add_component(name="Src", cls="Source")
    original.add_component(name="Blk", cls="Block")
    original.add_component(name="Tgt", cls="Target")
    original.connect_flow(source="Src", target="Blk", flow_name=FLOW)
    original.connect_flow(source="Blk", target="Tgt", flow_name=FLOW)
    original.comp["Blk"].add_exp_failure_mode(
        name="failure",
        failure_rate=0.1,
        repair_rate=0.5,
        failure_effects=[(FLOW, False)],
    )
    original.add_component(
        cls="ObjEvent",
        name=FEARED_EVENT,
        cond=[[{"attr": f"{FLOW}_fed_in", "obj": "Tgt", "value": False}]],
    )
    original.add_indicator_var(
        component="^Tgt$", var=f"^{FLOW}_fed_in$", stats=["mean"]
    )

    # Through json, because a document that only survives in memory is not one.
    document = json.loads(json.dumps(muscadet.system_spec(original)))
    original.deleteSys()
    cod3s.terminate_session()

    # Named off the document, which is what ``reference_run`` does when it
    # creates the system itself. Handing it one is what a caller does when it
    # owns the creation -- and every caller comparing a system with its own
    # reconstruction has to, one live system per process being the rule.
    rebuilt = muscadet.System(name=document["name"])
    result = muscadet.reference_simulate(
        document, RUN_PARAMS, system=rebuilt, targets=[FEARED_EVENT]
    )

    yield {
        "document": document,
        "system": rebuilt,
        "result": result,
        "reported": rebuilt.run_declaration,
        "refusal": rebuilt.run_declaration_refusal,
    }

    rebuilt.deleteSys()
    cod3s.terminate_session()


def test_the_engine_built_what_the_document_declared(the_run_from_a_document):
    assert set(the_run_from_a_document["system"].comp) == set(
        the_run_from_a_document["document"]["components"]
    )


def test_the_run_reports_the_document_it_was_built_from(the_run_from_a_document):
    """The loop, closed: nothing was lost going out and nothing invented coming in."""
    assert the_run_from_a_document["refusal"] is None
    assert _without_provenance(the_run_from_a_document["reported"]) == (
        _without_provenance(the_run_from_a_document["document"])
    )


def test_the_provenance_says_the_build_came_from_the_document(
    the_run_from_a_document,
):
    """Pinned so the exclusion above stays a choice and not an oversight."""
    reported = the_run_from_a_document["reported"]["components"]
    assert reported["Src"]["source_cls"] == "ObjFlow"
    assert the_run_from_a_document["document"]["components"]["Src"]["source_cls"] == (
        "Source"
    )


def test_a_target_named_beside_the_document_reached_the_run(the_run_from_a_document):
    """The keyword crosses this entry point too, or a campaign stops at nothing."""
    assert FEARED_EVENT in muscadet.declared_events(the_run_from_a_document["reported"])
    assert (
        muscadet.declared_occurrence_state(
            the_run_from_a_document["reported"], FEARED_EVENT
        )
        == "occ"
    )


def test_an_unknown_kind_of_run_is_refused_by_name():
    """The two kinds are named once; a third invented by typo is not a run."""
    with pytest.raises(muscadet.EngineError, match="unknown kind of run"):
        muscadet.reference_run("simulate_batch", {"version": "1.0.0"})
