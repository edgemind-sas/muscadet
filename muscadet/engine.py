"""The seam an engine registers at, and the one place a run picks one.

muscadet is a modelling façade, and a façade that knows its engines is not one.
So this module holds no engine name but the reference one, ``pycatshoo``, which
is not registered here because it is not a plugin: it is muscadet's own body,
reached by the direct path :class:`muscadet.System` has always taken.

**Engines register; muscadet never imports them.** The registry below is filled
one of two ways, and both mean the same thing -- a third engine is added
without a line of muscadet changing:

- an engine package calls :func:`register_engine`, typically from its own
  ``__init__``;
- an engine advertises itself under the :data:`ENGINE_ENTRY_POINT_GROUP` entry
  point group of its distribution, and muscadet discovers it on first need.

The second route is what removes the last import from the CALLER too: a study
that names ``"raichu"`` does not have to import ``pyraichu`` first, so the
choice of engine really is a setting rather than a line of code. An engine
declares itself with::

    [project.entry-points."muscadet.engines"]
    raichu = "pyraichu.muscadet_engine:register"

The temptation to ``import`` an engine here returns with every new one, and
each time it would make muscadet uninstallable without that engine.
``tests/test_engine_registry_001.py`` parses this package and fails on any
import beyond muscadet's declared dependencies, which is what stops it.

What an engine receives is the SYSTEM DECLARATION
-------------------------------------------------
Never the live system. An engine handed the live object would read PyCATSHOO
components back out of it, and the seam would quietly become "share the
reference engine's objects" instead of "share a document". The declaration is
:func:`muscadet.declare.system_spec`, checked on the way out so a malformed
document is muscadet's fault at muscadet's door rather than an obscure failure
inside a third-party engine.

Run parameters travel BESIDE the declaration, not inside it, because they are
the configuration of a run and not the description of a system: two systems
carrying the same declaration are the same system, whatever one intends to
compute on them.
"""

import importlib.metadata
from typing import Any, Callable, Dict, Optional, Tuple

import pydantic

from .declare import check_system_spec, system_spec

#: The engine muscadet carries in its own body: PyCATSHOO, through ``cod3s``.
#: Selecting it, or selecting nothing, takes the direct path. It is deliberately
#: NOT a registered engine -- it needs no declaration to run a system it already
#: holds, and the day it reads the document like the others (which is the stated
#: target, not today's state) muscadet itself changes.
REFERENCE_ENGINE = "pycatshoo"

#: Entry point group an engine distribution advertises itself under.
ENGINE_ENTRY_POINT_GROUP = "muscadet.engines"

#: The two run entry points of :class:`muscadet.System`, and the two attributes
#: an :class:`Engine` may carry. Named once so the dispatch below cannot invent
#: a third by typo.
RUN_BATCH = "simulate"
RUN_INTERACTIVE = "isimu_start"
RUN_KINDS = (RUN_BATCH, RUN_INTERACTIVE)


class EngineError(ValueError):
    """Base for everything that can go wrong selecting or registering an engine."""


class UnknownEngineError(EngineError):
    """A run named an engine no one registered."""


class EngineAlreadyRegisteredError(EngineError):
    """The name is taken, or reserved for the reference engine."""


class EngineRunnerMissingError(EngineError):
    """The engine is registered but carries no runner for this kind of run.

    A distinct error from :class:`UnknownEngineError` on purpose: an engine that
    runs batches but has no interactive session is a normal, declarable state,
    and reporting it as "unknown engine" would send the caller looking for an
    installation problem that does not exist.
    """


class Engine(pydantic.BaseModel):
    """What muscadet holds about one engine: a name, and how to run on it.

    Frozen, because a registry entry that could be mutated in place would let a
    caller repoint an engine under everyone else's feet, with no registration
    to point at afterwards.

    ``isimu_start`` is optional and ``simulate`` is not: an engine that cannot
    run a batch has nothing to offer a reliability study, while an engine
    without an interactive session is merely one a demonstration cannot use.
    """

    model_config = pydantic.ConfigDict(frozen=True)

    #: How the engine is named at a run. Free-form: muscadet does not keep a
    #: list of the engines that may exist, which is the whole point.
    name: str = pydantic.Field(..., min_length=1)

    #: ``(spec: dict, *args, **kwargs) -> Any`` -- run a batch from a system
    #: declaration. The return value is the engine's, handed back untouched.
    simulate: Callable[..., Any]

    #: ``(spec: dict, *args, **kwargs) -> Any`` -- open an interactive session
    #: from the same declaration, or ``None`` when the engine has none.
    isimu_start: Optional[Callable[..., Any]] = None

    #: Free text for a human reading :func:`registered_engines`.
    description: str = ""

    def run(self, kind: str, spec: dict, *args, **kwargs) -> Any:
        """Hand ``spec`` to the runner for ``kind``, or say why there is none."""
        if kind not in RUN_KINDS:
            raise EngineError(f"unknown kind of run {kind!r}, known: {list(RUN_KINDS)}")
        runner = getattr(self, kind)
        if runner is None:
            raise EngineRunnerMissingError(
                f"engine {self.name!r} declares no {kind!r} runner; it was "
                f"registered with {sorted(k for k in RUN_KINDS if getattr(self, k))}"
            )
        return runner(spec, *args, **kwargs)


# ---------------------------------------------------------------------------
# The registry. Engines register into it; muscadet never imports one.
# ---------------------------------------------------------------------------

_ENGINES: Dict[str, Engine] = {}

#: Discovery is done once and remembered, because reading the installed
#: distributions on every ``simulate`` would pay a filesystem walk per run.
_DISCOVERED = False

#: What went wrong loading an advertised engine, kept rather than raised. One
#: broken third-party plugin is not allowed to make an unrelated engine
#: unusable -- but it is not swallowed either: the failure is reported in the
#: message of the next :class:`UnknownEngineError`, which is when it starts to
#: explain something.
_DISCOVERY_FAILURES: Dict[str, str] = {}


def register_engine(
    name: str,
    simulate: Callable[..., Any],
    isimu_start: Optional[Callable[..., Any]] = None,
    description: str = "",
    replace: bool = False,
) -> Engine:
    """Register an engine under ``name``, and return what was registered.

    Parameters
    ----------
    name : str
        How a run selects this engine. :data:`REFERENCE_ENGINE` is reserved.
    simulate : callable
        ``(spec, *args, **kwargs)`` -- runs a batch from a system declaration.
    isimu_start : callable, optional
        Same shape, for an interactive session.
    description : str, optional
        Free text, for a human listing what is installed.
    replace : bool, optional
        Registering twice under the same name is refused by default. Two
        packages claiming one name is a broken installation, and letting the
        last import win would make which engine ran depend on import order.
        Tests, and an engine deliberately re-registering itself, pass True.

    Raises
    ------
    EngineAlreadyRegisteredError
        The name is taken by another registration, or reserved.
    """
    name = str(name)
    if name == REFERENCE_ENGINE:
        raise EngineAlreadyRegisteredError(
            f"{name!r} is muscadet's own reference path, not a plugin: it needs "
            "no registration and cannot be shadowed by one"
        )
    if name in _ENGINES and not replace:
        raise EngineAlreadyRegisteredError(
            f"an engine is already registered as {name!r}; pass replace=True to "
            "take the name over deliberately"
        )
    engine = Engine(
        name=name,
        simulate=simulate,
        isimu_start=isimu_start,
        description=description,
    )
    _ENGINES[name] = engine
    return engine


def unregister_engine(name: str) -> None:
    """Drop a registration. Unknown names are ignored, so cleanup is idempotent."""
    _ENGINES.pop(str(name), None)


def reset_engines() -> None:
    """Empty the registry and forget discovery. For tests, and for tests only."""
    global _DISCOVERED
    _ENGINES.clear()
    _DISCOVERY_FAILURES.clear()
    _DISCOVERED = False


def is_reference_engine(name: Optional[str]) -> bool:
    """True when ``name`` selects muscadet's own path -- including by saying nothing."""
    return name is None or str(name) == REFERENCE_ENGINE


def registered_engines() -> Tuple[str, ...]:
    """Every engine name a run may select, the reference one excluded.

    Empty on a plain muscadet install, which is the intended state: muscadet
    runs its own models on its own engine without any of these.
    """
    discover_engines()
    return tuple(sorted(_ENGINES))


def get_engine(name: str) -> Engine:
    """The engine registered as ``name``.

    Raises
    ------
    UnknownEngineError
        Nothing is registered under that name. The message lists what IS
        registered, and names any engine whose discovery hook failed, because
        "raichu is not registered" and "raichu is installed but broken" send a
        reader to two very different places.
    """
    name = str(name)
    if name == REFERENCE_ENGINE:
        raise UnknownEngineError(
            f"{name!r} is muscadet's reference path, not a registered engine: "
            "select it by name on a run, or select nothing"
        )
    discover_engines()
    engine = _ENGINES.get(name)
    if engine is None:
        known = sorted(_ENGINES) or ["<none>"]
        message = f"no engine registered as {name!r} (registered: {known})"
        if name in _DISCOVERY_FAILURES:
            message += f"; its discovery hook failed with {_DISCOVERY_FAILURES[name]}"
        elif _DISCOVERY_FAILURES:
            message += f"; discovery also failed for {sorted(_DISCOVERY_FAILURES)}"
        raise UnknownEngineError(message)
    return engine


def discover_engines(force: bool = False) -> Tuple[str, ...]:
    """Load every engine advertised under :data:`ENGINE_ENTRY_POINT_GROUP`.

    The contract of one entry point: its NAME is the engine's name, and its
    value resolves to a zero-argument hook that registers it. muscadet then
    checks the name actually appeared rather than trusting the hook, because a
    hook that registers nothing else leaves a plugin silently absent -- the
    caller sees "no engine registered as raichu" on a machine where raichu is
    plainly installed.

    Called on first need by :func:`get_engine` and :func:`registered_engines`,
    and remembered. ``force`` re-runs it, which is what a test that installs a
    fake distribution needs.
    """
    global _DISCOVERED
    if _DISCOVERED and not force:
        return tuple(sorted(_ENGINES))
    _DISCOVERED = True
    _DISCOVERY_FAILURES.clear()
    for entry in _advertised_engines():
        if entry.name in _ENGINES:
            # An engine that already registered itself, by import or by an
            # earlier pass. Its own registration wins over re-running a hook.
            continue
        try:
            hook = entry.load()
            hook()
            if entry.name not in _ENGINES:
                raise EngineError(
                    f"{entry.value!r} registered nothing under {entry.name!r}"
                )
        except Exception as err:  # noqa: BLE001 -- one plugin never hides another
            _DISCOVERY_FAILURES[entry.name] = f"{type(err).__name__}: {err}"
    return tuple(sorted(_ENGINES))


def _advertised_engines():
    """The entry points of the group, or nothing at all.

    Reading installed distributions is not something a modelling run should be
    able to die of: a broken metadata directory somewhere on the path would
    otherwise take down a muscadet that needs no engine to begin with.
    """
    try:
        return tuple(importlib.metadata.entry_points(group=ENGINE_ENTRY_POINT_GROUP))
    except Exception:  # noqa: BLE001 -- see docstring
        return ()


# ---------------------------------------------------------------------------
# Dispatch: what :class:`muscadet.System` calls once a run named an engine
# ---------------------------------------------------------------------------


def system_declaration(system) -> dict:
    """The document handed to an engine, checked before it leaves muscadet.

    The check costs nothing next to a simulation and moves a whole class of
    failure back to its cause: a declaration muscadet cannot read is refused
    here, by muscadet, rather than half-consumed by a third-party engine that
    then reports something about its own internals.
    """
    spec = system_spec(system)
    check_system_spec(spec)
    return spec


def run_on_engine(kind: str, name: str, system, *args, **kwargs) -> Any:
    """Read ``system`` back as a declaration and run it on the engine ``name``."""
    engine = get_engine(name)
    return engine.run(kind, system_declaration(system), *args, **kwargs)


def simulate_on(name: str, system, *args, **kwargs) -> Any:
    """Batch run of ``system`` on the registered engine ``name``."""
    return run_on_engine(RUN_BATCH, name, system, *args, **kwargs)


def isimu_start_on(name: str, system, *args, **kwargs) -> Any:
    """Interactive session for ``system`` on the registered engine ``name``."""
    return run_on_engine(RUN_INTERACTIVE, name, system, *args, **kwargs)
