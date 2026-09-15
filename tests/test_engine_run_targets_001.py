"""A run declares the events it stops at, and the document stays a document.

A sequence target is a feared event a trajectory stops at: the run ends there,
the indicators latch at its first occurrence, and the transitions that led to it
are what a minimal-sequence campaign reads. Both engines have the notion and
neither could be told about it -- the declaration leaves targets out on purpose,
a run carried no vocabulary for them, and ``target: true`` on an event's
declaration was refused by name -- so a study's feared event reached nobody and
the campaign came back empty on a run that exited cleanly.

What is pinned here is the door that closes that, and the shape of the door is
the decision this module exists to hold:

1. **a target travels BESIDE the document, never inside it.** The proof is
   ``test_the_document_the_engine_receives_carries_no_target_at_all``, and the
   reason is one system run twice: a study reads its availability figures off a
   free-cycling campaign and its sequences off a first-occurrence campaign, on
   ONE model. A ``targets`` section in the declaration would make those two
   runs two different systems;
2. **it names the event, and nothing but the event.** The translation into an
   automaton and the state that ends a trajectory is the engine's, and the two
   engines spell it differently -- which is exactly why muscadet does not;
3. **it is honoured on both paths**, the seam and the direct PyCATSHOO one, so
   it is a vocabulary rather than a hole shaped like one engine.

The last class runs a real campaign, and it is the running example itself: an
event on the loss of the flow, a repairable block upstream of it, and the
question "by which paths do we get there". The discriminator is not a tuned
threshold. The block fails at 0.1 and is repaired at 0.5, so a FREE-CYCLING
campaign plateaus at its stationary unavailability, 0.1 / 0.6 = 0.167, and can
never come near 1 at any instant; a first-occurrence campaign latches and
climbs. Measured on this very model, 2000 replicas, seed 4242::

    instant        0.0     5.0    10.0    25.0    50.0
    free-cycling   0.000   0.154   0.162   0.174   0.169
    with a target  0.000   0.386   0.624   0.910   0.991

The two runs cannot live in one module -- a system simulates once, PyCATSHOO
refusing to define an indicator twice -- so the campaign is the one that runs
and the plateau is what the ceiling above proves unreachable.
"""

import cod3s
import pytest

import muscadet
import muscadet.engine
import muscadet.kb.rbd  # noqa: F401 -- registers Source / Block / Target

FLOW = "is_ok"

#: The feared event of the running example: the flow no longer reaches the far
#: end. Written with the attribute and the object NAMED rather than with a live
#: PyCATSHOO variable, which is the form that survives being written out -- a
#: condition holding a variable is refused by ``component_spec``, and an event
#: nobody can declare is not one an engine could be told to stop at.
FEARED_EVENT = "EVT_LOSS"

#: A second event, whose occurrence state is not called ``occ``. It is here for
#: one assertion: the reference path reads the state off the event instead of
#: assuming cod3s' own hard-coded ``occ``.
RENAMED_EVENT = "EVT_RENAMED"
RENAMED_STATE = "reached"

FAILURE_RATE = 0.1
REPAIR_RATE = 0.5

#: What a free-cycling campaign of this model plateaus at, and therefore the
#: ceiling a run without a target cannot pass at any instant.
FREE_CYCLING_CEILING = FAILURE_RATE / (FAILURE_RATE + REPAIR_RATE)

NB_RUNS = 2000
SCHEDULE = [0.0, 5.0, 10.0, 25.0, 50.0]
SEED = 4242


class RecordingEngine:
    """An engine that records what it is handed and answers something.

    Deliberately not a simulator: what is under test is the seam, and a seam is
    proved by what crosses it.
    """

    def __init__(self, name="recorder"):
        self.name = name
        self.batches = []
        self.sessions = []

    def simulate(self, spec, *args, **kwargs):
        self.batches.append({"spec": spec, "args": args, "kwargs": kwargs})
        return f"{self.name}-batch-{len(self.batches)}"

    def isimu_start(self, spec, *args, **kwargs):
        self.sessions.append({"spec": spec, "args": args, "kwargs": kwargs})
        return f"{self.name}-session-{len(self.sessions)}"


@pytest.fixture(autouse=True)
def a_clean_registry():
    """The registry is process-wide state; no test inherits another's engines."""
    muscadet.reset_engines()
    yield
    muscadet.reset_engines()


@pytest.fixture(scope="module")
def the_system():
    """One system for the module, PyCATSHOO allowing exactly one per process."""
    system = muscadet.System(name="RunTargetSys")
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
        tempo_occ=0,
    )
    system.add_component(
        cls="ObjEvent",
        name=RENAMED_EVENT,
        cond=[[{"attr": f"{FLOW}_fed_in", "obj": "Tgt", "value": False}]],
        tempo_occ=0,
        occ_state_name=RENAMED_STATE,
    )
    system.add_indicator_state(
        component=f"^{FEARED_EVENT}$", state="^occ$", stats=["mean"]
    )
    yield system


@pytest.fixture
def a_registered_engine():
    """A recording engine under the name ``recorder``, batch and interactive."""
    engine = RecordingEngine()
    muscadet.register_engine(
        name="recorder", simulate=engine.simulate, isimu_start=engine.isimu_start
    )
    return engine


# ---------------------------------------------------------------------------


class TestTheVocabularyIsReadableOnItsOwn:
    """What a run says when it declares targets, before any system is involved."""

    def test_a_run_that_declares_nothing_declares_no_target(self):
        """Saying nothing and saying "no target" are the same free-cycling run."""
        assert muscadet.run_target_names(None) == ()
        assert muscadet.run_target_names([]) == ()
        assert muscadet.run_target_names(()) == ()

    def test_a_bare_name_is_refused_rather_than_read_letter_by_letter(self):
        """A string is iterable, and that is the whole danger.

        Taken as a sequence, ``"PANNE_OND"`` is nine targets named ``P``, ``A``,
        ``N``..., none of which exists -- so the run would be refused for nine
        reasons that never mention the one mistake made.
        """
        with pytest.raises(muscadet.RunTargetError, match="one string"):
            muscadet.run_target_names(FEARED_EVENT)

    def test_what_is_not_a_list_of_names_is_refused_by_its_type(self):
        with pytest.raises(muscadet.RunTargetError, match="dict"):
            muscadet.run_target_names({FEARED_EVENT: True})
        with pytest.raises(muscadet.RunTargetError, match="name of its event"):
            muscadet.run_target_names([{"name": FEARED_EVENT}])
        with pytest.raises(muscadet.RunTargetError, match="name of its event"):
            muscadet.run_target_names([""])

    def test_a_name_declared_twice_is_one_target_and_the_order_is_kept(self):
        assert muscadet.run_target_names(["B", "A", "B"]) == ("B", "A")


class TestTheNamesAreReadAgainstTheDeclaration:
    """A target names an event of the very document about to be handed over."""

    def test_the_events_are_read_off_the_document(self, the_system):
        """``declared_events`` is what says which names a document offers."""
        spec = muscadet.system_spec(the_system)
        assert set(muscadet.declared_events(spec)) == {FEARED_EVENT, RENAMED_EVENT}

    def test_a_declared_event_is_accepted_by_its_name(self, the_system):
        spec = muscadet.system_spec(the_system)
        assert muscadet.run_targets(spec, [FEARED_EVENT]) == (FEARED_EVENT,)

    def test_a_target_naming_no_component_at_all_is_refused(self, the_system):
        spec = muscadet.system_spec(the_system)
        with pytest.raises(muscadet.RunTargetError, match="stop at nothing"):
            muscadet.run_targets(spec, ["PANNE_OND"])

    def test_a_target_naming_a_component_that_is_no_event_is_refused(self, the_system):
        """And told which it is, because the two typos need two answers.

        A name nobody declared and a name declared as something else are one
        keystroke apart and a world apart to fix.
        """
        spec = muscadet.system_spec(the_system)
        with pytest.raises(muscadet.RunTargetError, match="not an event"):
            muscadet.run_targets(spec, ["Blk"])

    def test_the_refusal_lists_the_events_there_are(self, the_system):
        spec = muscadet.system_spec(the_system)
        with pytest.raises(muscadet.RunTargetError) as refused:
            muscadet.run_targets(spec, ["Blk"])
        assert FEARED_EVENT in str(refused.value)

    def test_declaring_the_target_on_the_event_is_refused_with_its_address(
        self, the_system
    ):
        """The third door, still shut, and now signposted.

        Writing ``target: true`` on the event is the first thing anyone tries:
        it is what the engines' own model files carry. It stays refused -- a
        target is not a description of a system -- but the refusal now says
        where the thing goes instead, which is the whole difference between a
        closed door and a wall.
        """
        spec = muscadet.system_spec(the_system)
        misplaced = dict(spec["components"][FEARED_EVENT], target=True)

        with pytest.raises(muscadet.ComponentSpecError) as refused:
            muscadet.check_spec(misplaced)

        assert "muscadet.engine.RUN_TARGETS" in str(refused.value)
        assert "parameter of the run" in str(refused.value)


class TestTheSeamCarriesThemBesideTheDocument:
    """The criterion: a target crosses the seam, and the document does not carry it."""

    def test_the_engine_receives_the_targets_as_a_keyword_of_the_run(
        self, the_system, a_registered_engine
    ):
        answer = the_system.simulate(
            {"nb_runs": 3, "schedule": SCHEDULE},
            engine="recorder",
            targets=[FEARED_EVENT],
        )

        assert answer == "recorder-batch-1"
        call = a_registered_engine.batches[0]
        assert call["kwargs"][muscadet.RUN_TARGETS] == (FEARED_EVENT,)
        assert call["args"] == ({"nb_runs": 3, "schedule": SCHEDULE},)

    def test_the_document_the_engine_receives_carries_no_target_at_all(
        self, the_system, a_registered_engine
    ):
        """The decision, asserted rather than described.

        One system, two campaigns: the availability run and the sequence run
        carry the SAME declaration and differ only in what travels beside it. A
        ``targets`` section would make them two systems.
        """
        the_system.simulate({"nb_runs": 1}, engine="recorder")
        the_system.simulate({"nb_runs": 1}, engine="recorder", targets=[FEARED_EVENT])

        free_cycling, campaign = a_registered_engine.batches
        assert muscadet.RUN_TARGETS not in free_cycling["spec"]
        assert muscadet.RUN_TARGETS not in campaign["spec"]
        assert free_cycling["spec"] == campaign["spec"], "one system, one document"
        assert campaign["kwargs"][muscadet.RUN_TARGETS] == (FEARED_EVENT,)

    def test_a_run_without_targets_hands_the_engine_no_such_keyword(
        self, the_system, a_registered_engine
    ):
        """An engine that knows nothing of targets keeps running every other run.

        The keyword is absent rather than empty, so only a caller who really
        asks for a target can meet an engine unable to serve one.
        """
        the_system.simulate({"nb_runs": 1}, engine="recorder")
        the_system.simulate({"nb_runs": 1}, engine="recorder", targets=[])

        for call in a_registered_engine.batches:
            assert muscadet.RUN_TARGETS not in call["kwargs"]

    def test_an_interactive_session_carries_them_the_same_way(
        self, the_system, a_registered_engine
    ):
        """A keyword read by one entry point and not the other is a divergence."""
        the_system.isimu_start(engine="recorder", targets=[FEARED_EVENT])

        session = a_registered_engine.sessions[0]
        assert session["kwargs"][muscadet.RUN_TARGETS] == (FEARED_EVENT,)

    def test_a_target_naming_nothing_is_refused_before_the_engine_is_reached(
        self, the_system, a_registered_engine
    ):
        """muscadet's door, muscadet's refusal: the engine never sees the run."""
        with pytest.raises(muscadet.RunTargetError, match="PANNE_OND"):
            the_system.simulate(
                {"nb_runs": 1}, engine="recorder", targets=["PANNE_OND"]
            )
        assert a_registered_engine.batches == []


class TestTheReferencePathHonoursTheSameKeyword:
    """PyCATSHOO holds a target on the live system, and takes it from here too.

    The base ``simulate`` is intercepted rather than run: a system simulates
    once, and the run that is spent is the campaign of the last class. What is
    under test here is what muscadet declares on the system before handing over.
    """

    @pytest.fixture
    def declared(self, the_system, monkeypatch):
        """``addTarget`` recorded on the instance, so nothing is really declared.

        Patching the instance rather than the class matters: a target declared
        for real would still be there when the real campaign runs, and a
        campaign stopping at one more event than it says is precisely the
        failure this vocabulary exists to prevent.
        """
        calls = []
        monkeypatch.setattr(
            the_system, "addTarget", lambda *args: calls.append(args), raising=False
        )
        return calls

    def test_it_declares_each_target_on_the_live_system(self, the_system, declared):
        assert the_system.declare_run_targets([FEARED_EVENT]) == (FEARED_EVENT,)
        assert declared == [(FEARED_EVENT, f"{FEARED_EVENT}.occ", "ST")]

    def test_a_run_declaring_no_target_declares_nothing_at_all(
        self, the_system, declared
    ):
        assert the_system.declare_run_targets(None) == ()
        assert declared == []

    def test_the_occurrence_state_is_read_from_the_event_and_not_assumed(
        self, the_system, declared
    ):
        """cod3s' own helper hard codes ``occ``; an event names its states.

        A modeller who renamed the occurrence state would otherwise get a
        target on a state that does not exist.
        """
        the_system.declare_run_targets([RENAMED_EVENT])
        assert declared == [(RENAMED_EVENT, f"{RENAMED_EVENT}.{RENAMED_STATE}", "ST")]

    def test_a_target_naming_no_event_is_refused_on_this_path_too(
        self, the_system, declared
    ):
        """The same sentence as the seam, for the same typo."""
        with pytest.raises(muscadet.RunTargetError, match="not an event"):
            the_system.declare_run_targets(["Blk"])
        with pytest.raises(muscadet.RunTargetError, match="stop at nothing"):
            the_system.declare_run_targets(["PANNE_OND"])
        assert declared == [], "nothing is declared once one name is bad"

    def test_the_run_keyword_reaches_it(self, the_system, declared, monkeypatch):
        """``simulate(targets=...)`` on the direct path, not only the seam."""
        taken = []
        monkeypatch.setattr(
            cod3s.PycSystem,
            "simulate",
            lambda self, *args, **kwargs: taken.append((args, kwargs)),
        )

        the_system.simulate({"nb_runs": 2}, targets=[FEARED_EVENT])

        assert taken == [(({"nb_runs": 2},), {})], "the target is not a simu param"
        assert declared == [(FEARED_EVENT, f"{FEARED_EVENT}.occ", "ST")]


class TestAFirstOccurrenceCampaignLatchesAtTheFearedEvent:
    """The running example, run for real on the reference engine.

    The one real simulation of this module, and it is what says the vocabulary
    carries meaning and not merely a keyword: with the target declared, every
    trajectory stops at the first loss and the indicator latches from there to
    the horizon.
    """

    def test_the_indicator_latches_at_the_first_occurrence(self, the_system):
        the_system.simulate(
            {"nb_runs": NB_RUNS, "schedule": SCHEDULE, "seed": SEED},
            targets=[FEARED_EVENT],
        )

        indicator = the_system.indicators[f"{FEARED_EVENT}_occ"]
        rows = indicator.values[indicator.values["stat"] == "mean"]
        means = [
            float(value)
            for _, value in sorted(
                zip(rows["instant"], rows["values"]), key=lambda pair: float(pair[0])
            )
        ]

        assert means[0] == 0.0, "nothing has happened at the first instant"
        assert means == sorted(means), f"a latched indicator never goes back: {means}"
        assert means[-1] > 0.9, (
            f"the campaign did not stop at the feared event: {means}. A "
            f"free-cycling run of this model cannot pass "
            f"{FREE_CYCLING_CEILING:.3f} at any instant, so a curve below that "
            f"is a run whose target reached nobody"
        )


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
