"""Every door into a reference run emits the document, the primitive included.

There are THREE ways into a run on PyCATSHOO, not two, and the third is the one
a demonstration goes through: ``cod3s.pycatshoo.isimu.engine.ISimuEngine.start``
-- what ``isimu_start_cli`` and the COD3S TUI drive -- calls
``system.startInteractive()`` directly and never touches
:meth:`muscadet.System.isimu_start`.

**This module exists because the emission was first hooked onto the two
wrappers alone**, and a session opened by the TUI was then a reference run with
no document at all: nothing to compare with what the other engine received, on
the very path a model is shown to a client, and ``run_declaration`` /
``run_declaration_refusal`` both at ``None`` -- which is the one thing the pair
must never mean after a run, since the broad refusal capture of
``reference_declaration`` rests on "no document, a reason posted".

It is the same trap :meth:`muscadet.System.prerun` fell into and is documented
under, one layer up: the pre-run step was hooked onto the wrappers too, a
session opened through the primitive registered no equation, every sweep ran
inert, and a full source reported a level of 0 with no exception. A hole on
this door does not fail; it answers.

What is pinned, in order:

1. **the primitive alone emits** -- no wrapper, exactly as the TUI calls it;
2. **the invariant of the pair** -- a document, or a reason, never neither;
3. **the wrapper emits the same document as the primitive.** The wrapper path
   reaches the primitive, so it emits twice, and the second emission happens
   after a target has been declared on the system. Asserting the two agree is
   what says that declaring a target does not change the model -- otherwise the
   attribute would end up holding a document nobody handed anyone;
4. **a model no document describes posts its reason on this door too**, which
   is the half of the invariant the happy path cannot show.
"""

import json

import cod3s
import pytest

import muscadet
import muscadet.kb.rbd  # noqa: F401 -- registers Source / Block / Target

FLOW = "is_ok"
FEARED_EVENT = "EVT_LOSS"


def _sorted_json(document):
    return json.dumps(document, sort_keys=True, indent=1)


@pytest.fixture(scope="module")
def the_three_doors():
    """One system, opened by the primitive and then by the wrapper.

    ``startInteractive`` is called the way ``ISimuEngine.start`` calls it, then
    the session is closed and reopened through ``isimu_start``. One system,
    PyCATSHOO allowing exactly one live one per process.
    """
    system = muscadet.System(name="EveryDoor")
    system.add_component(name="Src", cls="Source")
    system.add_component(name="Blk", cls="Block")
    system.add_component(name="Tgt", cls="Target")
    system.connect_flow(source="Src", target="Blk", flow_name=FLOW)
    system.connect_flow(source="Blk", target="Tgt", flow_name=FLOW)
    system.comp["Blk"].add_exp_failure_mode(
        name="failure",
        failure_rate=0.1,
        repair_rate=0.5,
        failure_effects=[(FLOW, False)],
    )
    system.add_component(
        cls="ObjEvent",
        name=FEARED_EVENT,
        cond=[[{"attr": f"{FLOW}_fed_in", "obj": "Tgt", "value": False}]],
    )

    # The door the TUI uses: the engine primitive, no muscadet wrapper anywhere.
    system.startInteractive()
    by_primitive = {
        "document": system.run_declaration,
        "refusal": system.run_declaration_refusal,
    }

    system.stopInteractive()

    # The wrapper, which reaches the primitive on its way through and therefore
    # emits a second time -- after declaring the target on the live system.
    system.isimu_start(targets=[FEARED_EVENT])
    by_wrapper = {
        "document": system.run_declaration,
        "refusal": system.run_declaration_refusal,
    }

    system.deleteSys()
    cod3s.terminate_session()

    # The other half of the invariant: a model the declaration cannot write,
    # opened through the primitive.
    undeclarable = muscadet.System(name="EveryDoorCallable")
    muscadet.build_component(
        undeclarable,
        {
            "name": "SPLIT",
            "flows": [
                {
                    "cls": "FlowContinuousOut",
                    "name": "q",
                    "allocation_fun": lambda available, demands: demands,
                }
            ],
        },
    )
    undeclarable.startInteractive()
    by_primitive_undeclarable = {
        "document": undeclarable.run_declaration,
        "refusal": undeclarable.run_declaration_refusal,
    }

    yield {
        "primitive": by_primitive,
        "wrapper": by_wrapper,
        "primitive_undeclarable": by_primitive_undeclarable,
    }

    undeclarable.stopInteractive()
    undeclarable.deleteSys()
    cod3s.terminate_session()


def test_the_engine_primitive_emits_the_document(the_three_doors):
    """``startInteractive`` alone, which is all the TUI ever calls."""
    document = the_three_doors["primitive"]["document"]
    assert document is not None, "a session opened by the TUI is a reference run"
    assert set(document["components"]) == {"Src", "Blk", "Tgt", FEARED_EVENT}


@pytest.mark.parametrize("door", ["primitive", "wrapper", "primitive_undeclarable"])
def test_a_reference_run_leaves_a_document_or_a_reason(the_three_doors, door):
    """Never neither: the whole refusal capture rests on this pair being readable."""
    reading = the_three_doors[door]
    assert (reading["document"] is None) != (reading["refusal"] is None), (
        f"{door}: document={reading['document'] is not None}, "
        f"refusal={reading['refusal'] is not None}"
    )


def test_both_doors_of_the_interactive_path_emit_the_same_document(the_three_doors):
    """The wrapper emits, reaches the primitive, and emits again after addTarget.

    Equal, so the second emission is redundant and never contradictory -- which
    is what says a sequence target is a parameter of the run and leaves the
    model alone.
    """
    assert _sorted_json(the_three_doors["wrapper"]["document"]) == _sorted_json(
        the_three_doors["primitive"]["document"]
    )


def test_the_primitive_posts_the_refusal_of_a_model_it_cannot_write(the_three_doors):
    """The door a demonstration uses says why a model is not portable, too."""
    reading = the_three_doors["primitive_undeclarable"]
    assert reading["document"] is None
    assert isinstance(reading["refusal"], muscadet.ComponentSpecError)
    assert "allocation_fun" in str(reading["refusal"])
