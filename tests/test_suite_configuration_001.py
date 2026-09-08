"""A bare ``pytest`` at the repository root runs the suite the project declares.

Two ways the command a reviewer, a CI job or an orchestrator types can be red
while the library is green, both met in practice and both pinned here:

1. **A second configuration file shadowing the first.** ``pytest.ini`` used to
   sit beside ``pyproject.toml``. pytest reads the first inifile it finds and
   never merges the two, so ``[tool.pytest.ini_options]`` was dead text: no
   ``testpaths``, so an unscoped run also collected ``examples/``, whose sibling
   tests fail on their own -- 8 of them -- and after which 36 tests that pass
   in a run of their own errored, PyCATSHOO state being process-global; and no
   ``markers``, so ``pytest.mark.integration`` warned as a typo on every run.
   Deleting ``pytest.ini`` is the fix; asserting which file is in force is what
   stops it coming back.

2. **An interpreter without PyCATSHOO or cod3s.** muscadet pins Python
   ``>=3.10,<3.11``, so a ``pytest`` from any other environment fails every test
   module on the same import line and names none of them the cause. The root
   ``conftest.py`` refuses that run once, by name, and this module pins the
   refusal rather than the wall of collection errors it replaces.

The first statement is checked twice on purpose: on the effective configuration
this run received, and end to end in a subprocess, because only the subprocess
proves what the command actually gathers.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

#: A name no environment can satisfy, to drive the guard without touching the
#: real requirements of the interpreter running this test.
ABSENT_MODULE = "muscadet_no_such_requirement"


@pytest.fixture(scope="module")
def root_conftest(pytestconfig):
    """The repository's own ``conftest.py``, loaded by path.

    A plain ``import conftest`` from here lands on ``tests/conftest.py``: both
    files carry that module name and the nearer one wins. The guard under test
    lives in the root one, so it is loaded from its path rather than by name.
    """
    path = Path(pytestconfig.rootpath) / "conftest.py"
    spec = importlib.util.spec_from_file_location("muscadet_root_conftest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ConfigStub:
    """Just enough of a pytest ``Config`` for :func:`conftest.pytest_configure`."""

    def __init__(self):
        self.marker_lines = []

    def addinivalue_line(self, name, line):
        self.marker_lines.append((name, line))


def test_the_configuration_in_force_is_the_one_pyproject_declares(pytestconfig):
    """No inifile shadows ``pyproject.toml``, whatever else the root may hold."""
    assert pytestconfig.inipath is not None, "the suite runs on no configuration"
    assert pytestconfig.inipath.name == "pyproject.toml"
    assert not (Path(pytestconfig.rootpath) / "pytest.ini").exists()


def test_an_unscoped_run_is_scoped_to_the_tests_directory(pytestconfig):
    """``testpaths`` is what keeps ``examples/`` out of a bare ``pytest``."""
    assert pytestconfig.getini("testpaths") == ["tests"]


def test_the_markers_the_suite_uses_are_registered(pytestconfig):
    """Under ``--strict-markers`` an unregistered mark is an error, not a warning."""
    declared = {line.split(":")[0] for line in pytestconfig.getini("markers")}
    assert {"slow", "integration"} <= declared


def test_a_bare_pytest_gathers_the_tests_directory_and_nothing_else(pytestconfig):
    """The end-to-end statement: what the command itself collects, from the root."""
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            # The project's own addopts are dropped for the sole reason that
            # their -v would turn the one-test-id-per-line listing parsed below
            # into a tree. None of them decides WHAT is collected, which is the
            # statement under test; testpaths still does, and still applies.
            "-o",
            "addopts=",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(pytestconfig.rootpath),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr

    gathered = {
        line.split("::")[0] for line in collected.stdout.splitlines() if "::" in line
    }
    assert gathered, "collection reported no test at all"
    assert all(path.startswith("tests/") for path in gathered), sorted(
        path for path in gathered if not path.startswith("tests/")
    )


def test_the_running_interpreter_satisfies_the_requirements_the_guard_checks(
    root_conftest,
):
    """The guard has no false positive: this very run passed it."""
    assert root_conftest.unimportable_requirements() == []


def test_an_interpreter_missing_a_requirement_is_refused_once_and_by_name(
    root_conftest, monkeypatch
):
    """One usage error naming the interpreter, in place of one error per module."""
    monkeypatch.setattr(root_conftest, "REQUIREMENTS", (ABSENT_MODULE,))

    with pytest.raises(pytest.UsageError) as refusal:
        root_conftest.pytest_configure(_ConfigStub())

    message = str(refusal.value)
    assert ABSENT_MODULE in message
    assert sys.executable in message, "the message must name the interpreter at fault"
    assert "uv sync" in message, "the message must carry the way out"


def test_a_broken_requirement_is_reported_with_what_the_import_raised(root_conftest):
    """``ModuleNotFoundError: ...`` beside the name, so the cause needs no rerun."""
    broken = root_conftest.unimportable_requirements((ABSENT_MODULE,))

    assert len(broken) == 1
    name, reason = broken[0]
    assert name == ABSENT_MODULE
    assert reason.startswith("ModuleNotFoundError")
