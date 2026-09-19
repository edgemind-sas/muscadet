"""One model, two engines, and the documents they received compare on a diff.

The seam was asymmetric on purpose while the format was being invented (ADR
``SIMULATION_ENGINE/ADR-2026-09-08-muscadet-facade-portable-deux-moteurs``,
decision 7): RAICHU crossed the declaration, PyCATSHOO kept a construction path
of its own. What that cost is the thing this module closes. While one engine
alone read the document, **the document was not the semantics**: it was an
output towards RAICHU, free to drift from what the reference engine actually
ran, and nothing anywhere could notice -- not a campaign, which measures one
engine against itself, and not a golden, which measures one engine against its
own past.

The claim pinned here is what replaces that, and it is deliberately cheap to
check: two engines are given one model, and what each RECEIVED is compared.
Not what each computed. A diff of two documents needs no replica, no seed and
no horizon, it names the component and the field where the two parted company,
and it stays true of every model rather than of the one somebody sampled.

Three statements, and the third is the one that keeps the first two honest:

1. **the two documents are the same document**, byte for byte once serialised
   with sorted keys -- key order being the one thing a JSON object does not
   carry;
2. **the document does not move when the RUN does.** The same model run
   free-cycling and run stopping at two feared events yields the same
   declaration, because a target is a parameter of a run and not a section of a
   system. Without this the diff would be comparing run configurations and
   would pass for reasons that have nothing to do with the model;
3. **a model no document describes is refused on the seam and still runs on
   PyCATSHOO**, with the refusal kept where it can be read. That is not a hole
   in the symmetry, it is where the format's declared boundary falls: a live
   Python callable in a model is refused by name (ADR decision 3), those models
   have always run here, and taking them away would be a regression bought to
   pay for a slogan. The sentence that stays true is "both engines receive the
   same document, or neither receives one".

The running example is the RBD of the chantier: a source, a repairable block, a
far end, the feared event "the flow no longer arrives", and a second event whose
occurrence state is NOT called ``occ`` -- that one is here so the target
PyCATSHOO declares is shown to come from the document rather than from cod3s'
hard-coded default.
"""

import json

import cod3s
import pytest

import muscadet
import muscadet.kb.continuous  # noqa: F401 -- registers SourceContinuous
import muscadet.kb.rbd  # noqa: F401 -- registers Source / Block / Target

FLOW = "is_ok"

#: The feared event: the flow no longer reaches the far end. Named objects and
#: attributes rather than live PyCATSHOO variables, which is the form a
#: document can carry.
FEARED_EVENT = "EVT_LOSS"

#: A second event whose occurrence state is renamed. A document writes only what
#: someone decided, so this one appears in it and the default does not -- which
#: is exactly why both are made targets here.
RENAMED_EVENT = "EVT_RENAMED"
RENAMED_STATE = "reached"

FAILURE_RATE = 0.1
REPAIR_RATE = 0.5

SCHEDULE = [0.0, 5.0, 10.0]
NB_RUNS = 5
SEED = 4242


class RecordingEngine:
    """A registered engine that records the document it is handed.

    Not a simulator: what is under test is what crosses the seam, and that is
    proved by holding on to it rather than by computing with it.
    """

    def __init__(self, name="recorder"):
        self.name = name
        self.batches = []

    def simulate(self, spec, *args, **kwargs):
        self.batches.append({"spec": spec, "args": args, "kwargs": kwargs})
        return f"{self.name}-batch-{len(self.batches)}"


def _sorted_json(document):
    """The one comparison a document supports: serialised, keys sorted.

    Sorted because the key order of a JSON object carries no meaning and any
    consumer may rewrite it -- a Rust reader over a ``BTreeMap``, which is the
    RAICHU path, sorts. Serialised because that is the form a diff is read in,
    and because a document that compares equal as dicts but cannot be dumped is
    not a document.
    """
    return json.dumps(document, sort_keys=True, indent=1)


def _build_the_rbd(system):
    system.add_component(name="Src", cls="Source")
    system.add_component(name="Blk", cls="Block")
    system.add_component(name="Tgt", cls="Target")
    system.connect_flow(source="Src", target="Blk", flow_name=FLOW)
    system.connect_flow(source="Blk", target="Tgt", flow_name=FLOW)
    system.comp["Blk"].add_exp_failure_mode(
        name="failure",
        failure_rate=FAILURE_RATE,
        repair_rate=REPAIR_RATE,
        failure_effects=[(FLOW, False)],
    )
    system.add_component(
        cls="ObjEvent",
        name=FEARED_EVENT,
        cond=[[{"attr": f"{FLOW}_fed_in", "obj": "Tgt", "value": False}]],
    )
    system.add_component(
        cls="ObjEvent",
        name=RENAMED_EVENT,
        occ_state_name=RENAMED_STATE,
        cond=[[{"attr": f"{FLOW}_fed_in", "obj": "Blk", "value": False}]],
    )
    system.add_indicator_var(component="^Tgt$", var=f"^{FLOW}_fed_in$", stats=["mean"])
    return system


@pytest.fixture(scope="module")
def the_two_readings():
    """One model handed to both engines, and everything each of them received.

    All the live work happens here, in order, because PyCATSHOO allows exactly
    one system per process and a system simulates exactly once: the assertions
    below read what was recorded rather than re-running anything.
    """
    muscadet.reset_engines()
    recorder = RecordingEngine()
    muscadet.register_engine("recorder", simulate=recorder.simulate)

    system = _build_the_rbd(muscadet.System(name="DocParity"))

    targets = [FEARED_EVENT, RENAMED_EVENT]
    run_params = {"nb_runs": NB_RUNS, "schedule": SCHEDULE, "seed": SEED}

    # The seam, twice: with the run this study wants, and free-cycling. Neither
    # touches the system, a recording engine computing nothing.
    system.simulate(run_params, engine="recorder", targets=targets)
    system.simulate(run_params, engine="recorder")

    # The reference engine, once, for real. ``addTarget`` is wrapped rather than
    # replaced: what PyCATSHOO is told has to be observed without changing what
    # the campaign then does.
    declared = []
    engine_add_target = system.addTarget

    def recording_add_target(*args):
        declared.append(args)
        return engine_add_target(*args)

    system.addTarget = recording_add_target
    system.simulate(run_params, targets=targets)

    readings = {
        "seam_with_targets": recorder.batches[0],
        "seam_free_cycling": recorder.batches[1],
        "reference": system.run_declaration,
        "reference_refusal": system.run_declaration_refusal,
        "declared_targets": declared,
    }

    system.deleteSys()
    cod3s.terminate_session()

    # A model the declaration cannot write: a continuous output whose split is a
    # Python function. Refused by name, and that refusal is the format's
    # boundary rather than a gap waiting to be closed.
    undeclarable = muscadet.System(name="DocParityCallable")
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

    with pytest.raises(muscadet.ComponentSpecError) as seam_refusal:
        undeclarable.simulate(run_params, engine="recorder")

    undeclarable.simulate(run_params)

    readings["undeclarable"] = {
        "seam_refusal": str(seam_refusal.value),
        "reference": undeclarable.run_declaration,
        "reference_refusal": undeclarable.run_declaration_refusal,
        "batches_after": len(recorder.batches),
    }

    yield readings

    undeclarable.deleteSys()
    cod3s.terminate_session()
    muscadet.reset_engines()


class TestTheTwoEnginesReceivedOneDocument:
    """The headline, and the reason the whole switch was worth making."""

    def test_the_documents_are_identical(self, the_two_readings):
        assert _sorted_json(the_two_readings["reference"]) == _sorted_json(
            the_two_readings["seam_with_targets"]["spec"]
        )

    def test_the_reference_run_kept_the_document_it_was_read_from(
        self, the_two_readings
    ):
        """Without this there is nothing on the reference side to diff at all."""
        assert the_two_readings["reference"] is not None
        assert the_two_readings["reference_refusal"] is None
        assert set(the_two_readings["reference"]["components"]) == {
            "Src",
            "Blk",
            "Tgt",
            FEARED_EVENT,
            RENAMED_EVENT,
        }

    def test_the_document_carries_the_wiring_and_what_is_observed(
        self, the_two_readings
    ):
        """A document equal to another says little if both are nearly empty."""
        document = the_two_readings["reference"]
        assert len(document["connections"]) == 2
        assert [entry["component"] for entry in document["indicators"]] == ["Tgt"]


class TestTheDocumentIsThemodelAndNotTheRun:
    """A target is a run parameter, so it moves without the document moving."""

    def test_a_run_with_targets_and_one_without_yield_the_same_document(
        self, the_two_readings
    ):
        assert _sorted_json(
            the_two_readings["seam_with_targets"]["spec"]
        ) == _sorted_json(the_two_readings["seam_free_cycling"]["spec"])

    def test_the_document_carries_no_target_section(self, the_two_readings):
        assert muscadet.RUN_TARGETS not in the_two_readings["reference"]


class TestBothEnginesResolveATargetOnTheDocument:
    """The names, and the state each one latches on, come from the declaration."""

    def test_the_seam_was_handed_the_names_beside_the_document(self, the_two_readings):
        assert the_two_readings["seam_with_targets"]["kwargs"][
            muscadet.RUN_TARGETS
        ] == (
            FEARED_EVENT,
            RENAMED_EVENT,
        )

    def test_pycatshoo_was_told_the_same_events(self, the_two_readings):
        assert [name for name, _, _ in the_two_readings["declared_targets"]] == [
            FEARED_EVENT,
            RENAMED_EVENT,
        ]

    def test_the_occurrence_state_came_from_the_document(self, the_two_readings):
        """cod3s' own helper hard codes ``occ``; the document says otherwise here.

        A target declared on a state the event does not have does not fail: it
        produces a campaign stopping at nothing, on a run that exits cleanly.
        """
        assert the_two_readings["declared_targets"] == [
            (FEARED_EVENT, f"{FEARED_EVENT}.occ", "ST"),
            (RENAMED_EVENT, f"{RENAMED_EVENT}.{RENAMED_STATE}", "ST"),
        ]
        assert (
            the_two_readings["reference"]["components"][RENAMED_EVENT]["occ_state_name"]
            == RENAMED_STATE
        )
        assert (
            "occ_state_name"
            not in the_two_readings["reference"]["components"][FEARED_EVENT]
        ), "a document writes only what someone decided"


class TestAModelNoDocumentDescribes:
    """Neither engine receives one, and the reference engine still runs it."""

    def test_the_seam_refuses_it_by_name(self, the_two_readings):
        assert "allocation_fun" in the_two_readings["undeclarable"]["seam_refusal"]

    def test_the_engine_was_never_reached(self, the_two_readings):
        """Refused at muscadet's door: the two recorded batches are the model above."""
        assert the_two_readings["undeclarable"]["batches_after"] == 2

    def test_the_reference_run_happened_and_carries_no_document(self, the_two_readings):
        assert the_two_readings["undeclarable"]["reference"] is None

    def test_the_refusal_is_kept_rather_than_swallowed(self, the_two_readings):
        """'This model is not portable' has to be a question with an answer."""
        refusal = the_two_readings["undeclarable"]["reference_refusal"]
        assert isinstance(refusal, muscadet.ComponentSpecError)
        assert "allocation_fun" in str(refusal)
