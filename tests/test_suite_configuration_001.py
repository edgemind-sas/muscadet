"""A bare ``pytest`` at the repository root runs the suite the project declares.

Three ways the command a reviewer, a CI job or an orchestrator types can be red
-- or worse, green over less than it says -- while the library is fine. All
three were met in practice, the third one by the very commit that fixed the
first two:

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

3. **A collection pattern that stops matching.** Dead text costs nothing, so a
   ``python_functions`` narrowed to ``test_*`` sat in that block unnoticed and
   went live the day the shadowing was removed. The four sibling tests of
   ``examples/rbd_0*`` name their function ``def test()``: they stopped being
   collected at all, which no run reports, no count betrays if nobody knew the
   old one, and no red flags. The pattern is back to pytest's default, and the
   statement asserted here is the general one -- no test file in the repository
   is collected as zero tests.

The scope statement is checked twice on purpose: on the effective configuration
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

#: Every directory of the repository that holds test files, collected or not by
#: a bare run: ``tests`` is the suite, ``examples`` the sibling tests run on
#: demand. Both answer to the same patterns, which is what makes a narrowing
#: there invisible from here.
TEST_DIRECTORIES = ("tests", "examples")

#: pytest's own default file patterns, wider than the ``python_files`` this
#: project declares. Walking the wider ones is deliberate: a file named against
#: the convention is then reported as collected by nothing, which is a sentence
#: someone can act on, rather than left out of both the walk and the run.
TEST_FILE_PATTERNS = ("test_*.py", "*_test.py")


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


def collected_files(rootpath, *targets):
    """The files a real ``pytest`` run collects at least one test from.

    Run in a subprocess from ``rootpath``, because what a command gathers is
    answered by the command and by nothing else. The project's own ``addopts``
    are dropped for one reason only: their ``-v`` would turn the one-id-per-line
    listing parsed here into a tree. None of them decides WHAT is collected,
    which is what every caller below asks about.

    :param rootpath: the repository root, the run's working directory
    :param targets: what to hand the command, nothing for an unscoped run
    :return: the repository-relative paths that yielded at least one test
    """
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-o",
            "addopts=",
            "-q",
            "-p",
            "no:cacheprovider",
            *targets,
        ],
        cwd=str(rootpath),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode == 0, run.stdout + run.stderr

    gathered = {line.split("::")[0] for line in run.stdout.splitlines() if "::" in line}
    assert gathered, "collection reported no test at all"
    return gathered


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
    """The end-to-end scope statement: what the command itself collects."""
    gathered = collected_files(pytestconfig.rootpath)

    assert all(path.startswith("tests/") for path in gathered), sorted(
        path for path in gathered if not path.startswith("tests/")
    )


def test_no_test_file_in_the_repository_is_collected_as_zero_tests(pytestconfig):
    """A pattern that stops matching drops tests in silence, red being the least.

    Asserted over ``examples/`` too, and over what is on disk rather than over a
    list written here: a file added tomorrow under a name the patterns do not
    reach must fail this, not go unnoticed like the four that did.
    """
    root = Path(pytestconfig.rootpath)
    on_disk = {
        path.relative_to(root).as_posix()
        for directory in TEST_DIRECTORIES
        for pattern in TEST_FILE_PATTERNS
        for path in (root / directory).rglob(pattern)
    }
    assert on_disk, "no test file found at all, so the walk is what is wrong"

    gathered = collected_files(root, *TEST_DIRECTORIES)

    assert not on_disk - gathered, sorted(on_disk - gathered)


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


def test_a_missing_pycatshoo_is_told_where_pycatshoo_comes_from(root_conftest):
    """`uv sync` never repairs that one, so the message says what does.

    PyCATSHOO is on no index and in no lock file: it is found through
    ``PYTHONPATH`` and loaded through ``LD_LIBRARY_PATH``. A run that replaces
    either -- which is what happens when a script sets them instead of
    prefixing them -- loses it while everything else still looks right.
    """
    broken = [("Pycatshoo", "ModuleNotFoundError: No module named 'Pycatshoo'")]

    message = root_conftest._wrong_interpreter_message(broken)

    assert "PYTHONPATH" in message
    assert "LD_LIBRARY_PATH" in message
    assert "uv sync" in message, "the generic way out stays, both can be at fault"


def test_a_broken_requirement_is_reported_with_what_the_import_raised(root_conftest):
    """``ModuleNotFoundError: ...`` beside the name, so the cause needs no rerun."""
    broken = root_conftest.unimportable_requirements((ABSENT_MODULE,))

    assert len(broken) == 1
    name, reason = broken[0]
    assert name == ABSENT_MODULE
    assert reason.startswith("ModuleNotFoundError")
