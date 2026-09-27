"""muscadet knows no engine, and an engine reaches it through a document.

Three statements are pinned here, and only the first one can be proved from
inside muscadet at all -- which is why this module exists rather than a test on
the platform or in an engine package:

1. **muscadet runs without any engine but its own.** The registry is empty on a
   plain install and a model still simulates, on the reference PyCATSHOO path.
2. **An engine registers without a line of muscadet changing.** Both routes are
   exercised: the explicit :func:`muscadet.register_engine` call an engine
   package makes, and the ``muscadet.engines`` entry point an installed
   distribution advertises.
3. **What the engine receives is the system declaration**, the same document
   :func:`muscadet.system_spec` produces, with the run parameters beside it and
   never inside it.

The guard against the fourth statement -- that muscadet never IMPORTS an engine
-- is the AST test below, modelled on the one the COD3S Platform's Domain
lifecycle carries for the same reason: the temptation to import the consumer
returns with every new consumer, and only a test stops it.

The model is the smallest thing that still has something to declare: a boolean
discrete RBD, source to block to target, with a failure mode on the block and
one indicator. Anything smaller would have an empty ``connections`` list, and
the wiring is exactly what a component-scale declaration could not carry.
"""

import ast
import importlib.metadata
import json
import sys
from pathlib import Path

import cod3s
import pytest

import muscadet
import muscadet.engine
import muscadet.kb.rbd  # noqa: F401 -- registers Source / Block / Target

FLOW = "is_ok"
NB_RUNS = 100
SCHEDULE = [0.0, 1.0, 5.0, 10.0]
SEED = 4242


# ---------------------------------------------------------------------------
# The fake engine. It is deliberately not a simulator: what is under test is
# the seam, and a seam is proved by what crosses it.
# ---------------------------------------------------------------------------


class FakeEngine:
    """An engine that records what it is handed and answers something."""

    def __init__(self, name="fake"):
        self.name = name
        self.batches = []
        self.sessions = []

    def simulate(self, spec, *args, **kwargs):
        self.batches.append({"spec": spec, "args": args, "kwargs": kwargs})
        return f"{self.name}-batch-{len(self.batches)}"

    def isimu_start(self, spec, *args, **kwargs):
        self.sessions.append({"spec": spec, "args": args, "kwargs": kwargs})
        return f"{self.name}-session-{len(self.sessions)}"


#: Registered by the entry point of :class:`TestAnEngineArrivesByEntryPoint`.
#: Module level, because an entry point resolves ``module:attribute`` and a
#: closure has no such address -- which is itself part of the contract being
#: tested.
ADVERTISED = FakeEngine(name="advertised")


def register_the_advertised_engine():
    """The hook shape an engine distribution points its entry point at."""
    muscadet.register_engine(
        name="advertised",
        simulate=ADVERTISED.simulate,
        description="an engine that arrived by entry point",
    )


@pytest.fixture(autouse=True)
def a_clean_registry():
    """The registry is process-wide state; no test inherits another's engines."""
    muscadet.reset_engines()
    yield
    muscadet.reset_engines()


@pytest.fixture(scope="module")
def the_model():
    """One system for the module, PyCATSHOO allowing exactly one per process."""
    system = muscadet.System(name="EngineSeamSys")
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
    system.add_indicator_var(component="^Tgt$", var=f"^{FLOW}_fed_in$", stats=["mean"])
    yield system


# ---------------------------------------------------------------------------


class TestMuscadetNeverImportsAnEngine:
    """The guard, because the temptation returns with every new engine.

    Written as an allowlist of what muscadet MAY import rather than a denylist
    of engine names, so it refuses the third engine nobody has written yet --
    which is the one the ADR is actually about. A denylist would pass on it.
    """

    #: Everything outside the standard library that muscadet's body is allowed
    #: to import. ``Pycatshoo`` is on it because it IS the reference engine,
    #: the one muscadet carries in its own body; that single line is the whole
    #: exception, and any other engine appearing here is the regression.
    ALLOWED_THIRD_PARTY = {
        "Pycatshoo",
        "cod3s",
        "colored",
        "muscadet",
        "pydantic",
    }

    def _sources(self):
        return sorted(Path(muscadet.__file__).parent.rglob("*.py"))

    def test_its_imports_stay_within_its_declared_dependencies(self):
        modules = {}
        for path in self._sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level or not node.module:
                        continue  # relative: muscadet importing itself
                    modules.setdefault(node.module.split(".")[0], path.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        modules.setdefault(alias.name.split(".")[0], path.name)

        forbidden = {
            name: where
            for name, where in modules.items()
            if name not in sys.stdlib_module_names
            and name not in self.ALLOWED_THIRD_PARTY
        }
        assert not forbidden, (
            "muscadet imports something it does not declare, and an engine is "
            f"what this catches: {forbidden}"
        )

    def test_it_does_not_reach_an_engine_by_dynamic_import_either(self):
        """An allowlist of ``import`` statements is blind to ``import_module``."""
        offenders = []
        for path in self._sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                target = ast.unparse(node.func)
                if not target.endswith(("import_module", "__import__")):
                    continue
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        root = arg.value.split(".")[0]
                        if root not in self.ALLOWED_THIRD_PARTY:
                            offenders.append((path.name, arg.value))
        assert not offenders, f"muscadet imports a module by name: {offenders}"

    def test_the_seam_itself_reaches_no_engine_at_all(self):
        """Stricter than the package, and on purpose: ``cod3s`` is out too.

        The extension point is the one module that must stay neutral between
        engines. Reaching the reference engine from HERE would make the
        reference a special case written into the seam, which is precisely the
        shape the ADR asks the seam not to have.
        """
        assert muscadet.REFERENCE_ENGINE == "pycatshoo"
        tree = ast.parse(Path(muscadet.engine.__file__).read_text(encoding="utf-8"))
        allowed = {"importlib", "typing", "pydantic"}
        reached = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    continue  # relative: muscadet importing itself
                reached.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Import):
                reached.update(alias.name.split(".")[0] for alias in node.names)
        assert reached <= allowed, f"the seam reaches {sorted(reached - allowed)}"


class TestASystemRunsWithoutAnyEngine:
    """The state a plain ``pip install muscadet`` is in, and it is a working one."""

    def test_the_registry_is_empty(self):
        assert muscadet.registered_engines() == ()

    def test_saying_nothing_selects_the_reference_path(self):
        assert muscadet.is_reference_engine(None)
        assert muscadet.is_reference_engine("pycatshoo")
        assert not muscadet.is_reference_engine("raichu")

    def test_the_reference_engine_is_not_a_plugin(self):
        """It needs no registration, and cannot be shadowed by one.

        Left registrable, an engine package could take the name over and every
        model that never asked for a plugin would silently change engine.
        """
        with pytest.raises(muscadet.EngineAlreadyRegisteredError, match="pycatshoo"):
            muscadet.register_engine(name="pycatshoo", simulate=lambda spec: spec)
        with pytest.raises(muscadet.UnknownEngineError, match="reference path"):
            muscadet.get_engine("pycatshoo")

    def test_a_model_simulates_with_the_registry_empty(self, the_model):
        """The acceptance criterion itself: no engine installed, results anyway."""
        assert muscadet.registered_engines() == ()
        the_model.simulate({"nb_runs": NB_RUNS, "schedule": SCHEDULE, "seed": SEED})

        indicator = the_model.indicators[f"Tgt_{FLOW}_fed_in"]
        means = {
            round(float(row["instant"]), 4): float(row["values"])
            for _, row in indicator.values[
                indicator.values["stat"] == "mean"
            ].iterrows()
        }
        assert means[0.0] == pytest.approx(1.0), means
        assert 0.0 < means[10.0] < 1.0, means


class TestAnEngineRegistersAndReceivesTheDeclaration:
    """The seam, from the side an engine package sees it."""

    def test_registering_takes_no_change_to_muscadet(self):
        engine = muscadet.register_engine(
            name="fake",
            simulate=FakeEngine().simulate,
            description="registered from outside",
        )
        assert engine.name == "fake"
        assert muscadet.registered_engines() == ("fake",)
        assert muscadet.get_engine("fake").description == "registered from outside"

    def test_two_packages_cannot_claim_one_name_by_accident(self):
        """Which engine ran would otherwise depend on import order."""
        muscadet.register_engine(name="fake", simulate=FakeEngine().simulate)
        with pytest.raises(muscadet.EngineAlreadyRegisteredError, match="replace"):
            muscadet.register_engine(name="fake", simulate=FakeEngine().simulate)
        second = FakeEngine(name="second")
        muscadet.register_engine(name="fake", simulate=second.simulate, replace=True)
        assert muscadet.get_engine("fake").simulate == second.simulate

    def test_a_run_on_an_unregistered_engine_says_what_is_registered(self):
        muscadet.register_engine(name="fake", simulate=FakeEngine().simulate)
        with pytest.raises(muscadet.UnknownEngineError, match="'fake'"):
            muscadet.get_engine("raichu")

    def test_the_engine_receives_the_declaration_of_the_model_built(self, the_model):
        """The criterion: a fake engine gets the system declaration, as data."""
        fake = FakeEngine()
        muscadet.register_engine(name="fake", simulate=fake.simulate)

        answer = the_model.simulate({"nb_runs": 3, "schedule": SCHEDULE}, engine="fake")

        assert answer == "fake-batch-1", "the engine's answer is handed back untouched"
        assert len(fake.batches) == 1
        received = fake.batches[0]["spec"]
        assert received == muscadet.system_spec(the_model)
        assert json.loads(json.dumps(received)) == received, "a document, not objects"

    def test_the_declaration_carries_the_wiring_and_what_is_observed(self, the_model):
        """What a component-scale declaration could not carry, and an engine needs."""
        fake = FakeEngine()
        muscadet.register_engine(name="fake", simulate=fake.simulate)
        the_model.simulate({"nb_runs": 1}, engine="fake")

        received = fake.batches[0]["spec"]
        assert set(received["components"]) == {"Src", "Blk", "Tgt"}
        wired = {
            (c["source"], c["target"], c.get("flow")) for c in received["connections"]
        }
        assert wired == {("Src", "Blk", FLOW), ("Blk", "Tgt", FLOW)}
        assert [i["component"] for i in received["indicators"]] == ["Tgt"]
        assert received["components"]["Blk"]["failure_modes"], "the block can fail"

    def test_the_run_parameters_travel_beside_the_declaration(self, the_model):
        """A declaration describes a system; parameters configure a run.

        Two systems carrying the same document are the same system, whatever
        one intends to compute on them -- so the parameters reach the engine
        untouched, and never inside the document.
        """
        fake = FakeEngine()
        muscadet.register_engine(name="fake", simulate=fake.simulate)
        params = {"nb_runs": 17, "schedule": SCHEDULE, "seed": SEED}

        the_model.simulate(params, engine="fake", postpone_post_proc=True)

        call = fake.batches[0]
        assert call["args"] == (params,)
        assert call["kwargs"] == {"postpone_post_proc": True}
        assert "nb_runs" not in call["spec"]
        assert "schedule" not in call["spec"]

    def test_an_interactive_session_takes_the_same_declaration(self, the_model):
        """Step by step and Monte Carlo diverge exactly where they build apart."""
        fake = FakeEngine()
        muscadet.register_engine(
            name="fake", simulate=fake.simulate, isimu_start=fake.isimu_start
        )

        assert the_model.isimu_start(engine="fake") == "fake-session-1"
        the_model.simulate({"nb_runs": 1}, engine="fake")
        assert fake.sessions[0]["spec"] == fake.batches[0]["spec"]

    def test_an_engine_without_an_interactive_session_says_so(self, the_model):
        """Not an installation problem, and not reported as one."""
        muscadet.register_engine(name="fake", simulate=FakeEngine().simulate)
        with pytest.raises(muscadet.EngineRunnerMissingError, match="isimu_start"):
            the_model.isimu_start(engine="fake")

    def test_selecting_the_reference_engine_by_name_keeps_the_direct_path(
        self, the_model, monkeypatch
    ):
        """A study naming ``pycatshoo`` runs where it always ran.

        A fake is registered while this happens: naming the reference, or
        naming nothing, must not fall through to whatever else is installed.

        The base ``simulate`` is intercepted rather than run, PyCATSHOO
        refusing to define an indicator twice and the reference path having
        already been run for real by
        :meth:`TestASystemRunsWithoutAnyEngine.test_a_model_simulates_with_the_registry_empty`.
        What is under test here is which of the two paths is taken.
        """
        fake = FakeEngine()
        muscadet.register_engine(name="fake", simulate=fake.simulate)
        taken = []
        monkeypatch.setattr(
            cod3s.PycSystem,
            "simulate",
            lambda self, *args, **kwargs: taken.append((args, kwargs)),
        )

        the_model.simulate({"nb_runs": 2}, engine=muscadet.REFERENCE_ENGINE)
        the_model.simulate({"nb_runs": 2})

        assert taken == [(({"nb_runs": 2},), {})] * 2
        assert fake.batches == [], "the reference path never reaches a plugin"


class TestAnEngineArrivesByEntryPoint:
    """Installed is enough: the caller does not import the engine either.

    This is what makes the choice of engine a setting rather than a line of
    code, and it is the route ``pyraichu`` is expected to take.
    """

    def _advertise(self, monkeypatch, *entries):
        def entry_points(group=None, **kwargs):
            if group == muscadet.ENGINE_ENTRY_POINT_GROUP:
                return list(entries)
            return []

        monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)
        muscadet.reset_engines()

    def _entry(self, name, attribute):
        # ``module:attribute``, resolved against THIS module, which is already
        # imported: the hook has to have an address, which is why the real one
        # cannot be a closure.
        return importlib.metadata.EntryPoint(
            name=name,
            value=f"{__name__}:{attribute}",
            group=muscadet.ENGINE_ENTRY_POINT_GROUP,
        )

    def test_an_advertised_engine_is_found_without_being_imported(self, monkeypatch):
        self._advertise(
            monkeypatch, self._entry("advertised", "register_the_advertised_engine")
        )
        assert muscadet.registered_engines() == ("advertised",)
        assert muscadet.get_engine("advertised").simulate == ADVERTISED.simulate

    def test_it_is_discovered_once_and_remembered(self, monkeypatch):
        calls = []

        def entry_points(group=None, **kwargs):
            calls.append(group)
            return (
                [self._entry("advertised", "register_the_advertised_engine")]
                if group == muscadet.ENGINE_ENTRY_POINT_GROUP
                else []
            )

        monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)
        muscadet.reset_engines()

        muscadet.registered_engines()
        muscadet.registered_engines()
        muscadet.get_engine("advertised")
        assert calls.count(muscadet.ENGINE_ENTRY_POINT_GROUP) == 1

    def test_a_hook_that_registers_nothing_is_a_reported_absence(self, monkeypatch):
        """Otherwise the engine is plainly installed and plainly missing."""
        self._advertise(
            monkeypatch, self._entry("silent", "a_hook_that_registers_nothing")
        )
        with pytest.raises(muscadet.UnknownEngineError, match="registered nothing"):
            muscadet.get_engine("silent")

    def test_one_broken_plugin_does_not_hide_another_engine(self, monkeypatch):
        """The failure is kept, and told to whoever asks for the broken one."""
        self._advertise(
            monkeypatch,
            self._entry("broken", "a_hook_that_raises"),
            self._entry("advertised", "register_the_advertised_engine"),
        )
        assert muscadet.registered_engines() == ("advertised",)
        with pytest.raises(muscadet.UnknownEngineError, match="on purpose"):
            muscadet.get_engine("broken")

    def test_unreadable_distribution_metadata_never_breaks_a_run(self, monkeypatch):
        """muscadet needs no engine to work; it must not die looking for one."""

        def explode(group=None, **kwargs):
            raise RuntimeError("metadata directory is a mess")

        monkeypatch.setattr(importlib.metadata, "entry_points", explode)
        muscadet.reset_engines()
        assert muscadet.registered_engines() == ()


def a_hook_that_registers_nothing():
    """An engine hook that forgets its own registration."""


def a_hook_that_raises():
    raise RuntimeError("this engine is broken on purpose")


def test_delete(the_model):
    the_model.deleteSys()
    cod3s.terminate_session()
