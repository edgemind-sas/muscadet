"""Suite-wide options, markers, and the one check that must run before them.

Two ways to run the suite, and nothing in between:

- the ordinary run needs PyCATSHOO, and is REFUSED by name when the
  interpreter cannot import it (see below);
- ``pytest --no-engine`` runs the engine-free part, ``tests/engine_free/``,
  in a process where PyCATSHOO is made unimportable. It is asked for, never
  fallen back to: a missing engine that quietly shrank the suite to a dozen
  tests and exited green is the silent run the refusal exists to prevent.


The suite needs an interpreter that carries PyCATSHOO and cod3s, and muscadet
pins Python ``>=3.10,<3.11`` because PyCATSHOO ships native libraries built for
it. A ``pytest`` picked up from any other environment therefore imports no test
module at all: every one of them fails on the same ``import cod3s`` line, and
the run ends on a wall of identical collection errors in which nothing names
the interpreter as the cause. :func:`pytest_configure` refuses that run once,
by name, instead.
"""

import importlib
import importlib.abc
import os
import sys
from pathlib import Path

import pytest

pytest.RUN_TESTS_DIR = os.getcwd()

#: What the suite cannot run without, checked by IMPORT rather than by
#: ``find_spec``: a PyCATSHOO whose native libraries are missing from
#: ``LD_LIBRARY_PATH`` has a findable spec and still cannot be imported, and
#: that environment fails the suite exactly like an absent module.
REQUIREMENTS = ("Pycatshoo", "cod3s")


def unimportable_requirements(names=None):
    """Return ``(name, reason)`` for each of ``names`` this interpreter refuses.

    :param names: module names the suite needs; :data:`REQUIREMENTS` when
        omitted, read at CALL time so that a test can substitute the list
    :return: one pair per module that could not be imported, in the given order
    """
    names = REQUIREMENTS if names is None else names
    broken = []
    for name in names:
        try:
            importlib.import_module(name)
        except (Exception, SystemExit) as error:  # noqa: BLE001 -- see below
            # Deliberately broad. A missing module raises ImportError, but a
            # native library loaded against the wrong Python raises whatever
            # the extension chose to raise, up to SystemExit: any failure here
            # disqualifies the interpreter just the same, and a narrower clause
            # would let it through to the wall of collection errors this guard
            # exists to replace. KeyboardInterrupt is left alone on purpose.
            broken.append((name, f"{type(error).__name__}: {error}"))
    return broken


def _wrong_interpreter_message(broken):
    """The one message a run under the wrong interpreter gets, in place of 143."""
    version = ".".join(str(part) for part in sys.version_info[:3])
    lines = [
        "muscadet's test suite cannot run under this interpreter.",
        "",
        f"  interpreter : {sys.executable} (Python {version})",
    ]
    lines += [f"  unusable    : {name} -- {reason}" for name, reason in broken]
    lines += [
        "",
        "muscadet pins Python >=3.10,<3.11 because PyCATSHOO ships native",
        "libraries built for it. Run the suite through the project environment:",
        "",
        "    uv sync                        # once, to build .venv",
        "    uv run pytest                  # or: .venv/bin/python -m pytest",
    ]
    if any(name == "Pycatshoo" for name, _ in broken):
        # `uv sync` alone never repairs this one, so it is said rather than
        # implied: PyCATSHOO is not on PyPI and is not in the lock file. It is
        # picked up from the environment, and a run that clears PYTHONPATH --
        # setting it rather than prefixing it, most often -- loses it while the
        # rest of the environment still looks right.
        lines += [
            "",
            "PyCATSHOO itself comes from neither: it is found through PYTHONPATH",
            "(its Core/lib and addsOn) and loaded through LD_LIBRARY_PATH (Core/lib",
            "and ThirdParty/lib). Prefix those, do not replace them.",
        ]
        lines += [
            "",
            "The engine-free part of the suite runs without it, on request:",
            "",
            "    uv run pytest --no-engine",
        ]
    return "\n".join(lines)


#: The engine module ``--no-engine`` makes unimportable.
ENGINE_MODULE = "Pycatshoo"

#: What ``--no-engine`` still needs: the declarative part of muscadet reads
#: cod3s, and nothing else of :data:`REQUIREMENTS`.
ENGINE_FREE_REQUIREMENTS = ("cod3s",)

#: The directory ``--no-engine`` collects, and the only one.
ENGINE_FREE_DIRECTORY = Path(__file__).parent / "tests" / "engine_free"


class _NoEngine(importlib.abc.MetaPathFinder):
    """Make the engine UNFINDABLE, which is what a machine without it looks like.

    Nothing is planted in ``sys.modules``: a stand-in module would be imported
    happily and fail later, somewhere else, on a missing attribute.
    """

    def find_spec(self, name, path=None, target=None):
        if name == ENGINE_MODULE or name.startswith(ENGINE_MODULE + "."):
            raise ModuleNotFoundError(
                f"No module named {ENGINE_MODULE!r}", name=ENGINE_MODULE
            )
        return None


def block_engine():
    """Install the blocker, refusing a process that already holds the engine."""
    if ENGINE_MODULE in sys.modules:
        raise pytest.UsageError(
            f"--no-engine: {ENGINE_MODULE} is already imported in this process, "
            "so its absence cannot be simulated. Run pytest --no-engine on its "
            "own, not alongside a plugin or a conftest that imports the engine."
        )
    if not any(isinstance(finder, _NoEngine) for finder in sys.meta_path):
        sys.meta_path.insert(0, _NoEngine())


def no_engine_requested(config):
    """Whether this run asked for ``--no-engine``.

    Read defensively: a caller that drives :func:`pytest_configure` with a
    minimal config (the suite's own configuration tests do) has no such
    option, which is the ordinary run.
    """
    getoption = getattr(config, "getoption", None)
    if getoption is None:
        return False
    try:
        return bool(getoption("--no-engine"))
    except ValueError:
        return False


def pytest_addoption(parser):
    parser.addoption(
        "--runslow", action="store_true", default=False, help="run slow tests"
    )
    parser.addoption(
        "--no-engine",
        action="store_true",
        default=False,
        help=(
            "run the engine-free part of the suite (tests/engine_free) in a "
            "process where PyCATSHOO cannot be imported"
        ),
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: mark test as slow to run")

    if no_engine_requested(config):
        block_engine()
        broken = unimportable_requirements(ENGINE_FREE_REQUIREMENTS)
    else:
        broken = unimportable_requirements()

    if broken:
        raise pytest.UsageError(_wrong_interpreter_message(broken))
    if not no_engine_requested(config):
        # Every export the suite materialises is read by both roads, the pure
        # one and the live one, and a difference fails the test that built it.
        from tests import double_reading

        double_reading.install()


def pytest_ignore_collect(collection_path, config):
    """Under ``--no-engine``, collect ``tests/engine_free`` and nothing else.

    Every other test module imports an engine-bound name at module level and
    would fail at collection, which is not a result. Ignoring them is the
    selection, stated once.
    """
    if not no_engine_requested(config):
        return None
    path = Path(collection_path).resolve()
    inside = ENGINE_FREE_DIRECTORY in path.parents or path == ENGINE_FREE_DIRECTORY
    leads_there = path in ENGINE_FREE_DIRECTORY.parents
    return None if inside or leads_there else True


def pytest_report_header(config):
    if no_engine_requested(config):
        return (
            f"muscadet: --no-engine, {ENGINE_MODULE} is unimportable in this "
            f"process and only {ENGINE_FREE_DIRECTORY.name}/ is collected"
        )
    return None


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        # --runslow given in cli: do not skip slow tests
        return
    skip_slow = pytest.mark.skip(reason="need --runslow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
