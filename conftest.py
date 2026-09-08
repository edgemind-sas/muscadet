"""Suite-wide options, markers, and the one check that must run before them.

The suite needs an interpreter that carries PyCATSHOO and cod3s, and muscadet
pins Python ``>=3.10,<3.11`` because PyCATSHOO ships native libraries built for
it. A ``pytest`` picked up from any other environment therefore imports no test
module at all: every one of them fails on the same ``import cod3s`` line, and
the run ends on a wall of identical collection errors in which nothing names
the interpreter as the cause. :func:`pytest_configure` refuses that run once,
by name, instead.
"""

import importlib
import os
import sys

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
    return "\n".join(lines)


def pytest_addoption(parser):
    parser.addoption(
        "--runslow", action="store_true", default=False, help="run slow tests"
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: mark test as slow to run")

    broken = unimportable_requirements()
    if broken:
        raise pytest.UsageError(_wrong_interpreter_message(broken))


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        # --runslow given in cli: do not skip slow tests
        return
    skip_slow = pytest.mark.skip(reason="need --runslow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
