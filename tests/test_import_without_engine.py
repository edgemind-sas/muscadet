"""muscadet loads, checks a declaration and parses an export without PyCATSHOO.

Each scenario runs in a subprocess whose import system refuses ``Pycatshoo``:
this suite has imported the engine long before this module runs, and an import
cannot be undone. Nothing is planted in ``sys.modules``; the engine is made
unfindable, which is what an install without the binary looks like.

The last test runs the engine-free part of the suite the way a machine without
the engine does, ``pytest --no-engine``.
"""

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

_BLOCK_ENGINE = textwrap.dedent("""
    import importlib.abc
    import json
    import sys


    class _NoEngine(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name == "Pycatshoo" or name.startswith("Pycatshoo."):
                raise ModuleNotFoundError(
                    "No module named 'Pycatshoo'", name="Pycatshoo"
                )
            return None


    sys.meta_path.insert(0, _NoEngine())
    """)


def _run_without_engine(body):
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_ENGINE + textwrap.dedent(body)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _assert_ok(result):
    assert result.returncode == 0, result.stderr


def test_muscadet_imports_without_the_engine():
    _assert_ok(_run_without_engine("""
            import muscadet
            import muscadet.declare
            import muscadet.engine
            import muscadet.importers
            import muscadet.conformance

            import inspect

            assert muscadet.ENGINE_AVAILABLE is False
            assert "ObjFlow" not in dir(muscadet)
            inspect.getmembers(muscadet)
            assert "Pycatshoo" not in sys.modules
            assert muscadet.__version__
            assert muscadet.registered_engines() is not None
            """))


def test_a_declaration_is_checked_without_the_engine():
    _assert_ok(_run_without_engine("""
            from muscadet.declare import ComponentSpecError, check_system_spec

            spec = json.load(open("tests/fixtures/engine_free_reference.json"))
            check_system_spec(spec)

            spec["components"]["A"]["no_such_key"] = 1
            try:
                check_system_spec(spec)
            except ComponentSpecError as error:
                assert "no_such_key" in str(error), str(error)
            else:
                raise SystemExit("a malformed declaration was accepted")
            assert "Pycatshoo" not in sys.modules
            """))


def test_a_platform_export_is_parsed_without_the_engine():
    _assert_ok(_run_without_engine("""
            from muscadet.importers.cod3s_platform import parse_platform_export

            payload = json.load(open("tests/fixtures/detection_chain_export.json"))
            context = parse_platform_export(payload)

            assert len(context.components) == 26
            assert len(context.connections) == 177
            assert "Pycatshoo" not in sys.modules
            """))


@pytest.mark.parametrize(
    "access",
    ["muscadet.ObjFlow", "muscadet.System", "muscadet.flow", "muscadet.Capacity"],
)
def test_an_execution_name_names_the_missing_engine(access):
    _assert_ok(_run_without_engine(f"""
            import muscadet

            try:
                {access}
            except ImportError as error:
                assert "PyCATSHOO" in str(error), str(error)
            else:
                raise SystemExit("expected an ImportError naming PyCATSHOO")
            """))


def test_an_unknown_name_stays_an_attribute_error():
    _assert_ok(_run_without_engine("""
            import muscadet

            assert not hasattr(muscadet, "no_such_name")
            """))


def test_a_foreign_mode_class_is_refused_where_no_engine_can_resolve_it():
    """A class cod3s does not ship is looked up in the live tree, which needs the engine."""
    _assert_ok(_run_without_engine("""
            from muscadet.declare import (
                ModeClassUnresolvedError,
                check_system_spec,
                declared_events,
            )

            spec = json.load(open("tests/fixtures/engine_free_reference.json"))
            spec["components"]["feared"]["cls"] = "ModellerOwnEvent"
            for read in (check_system_spec, declared_events):
                try:
                    read(spec)
                except ModeClassUnresolvedError as error:
                    assert "ModellerOwnEvent" in str(error), str(error)
                    assert "engine" in str(error), str(error)
                else:
                    raise SystemExit(f"{read.__name__} read past an unresolvable class")
            """))


def test_the_engine_free_part_of_the_suite_runs_without_the_engine():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--no-engine", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    report = result.stdout + result.stderr

    assert result.returncode == 0, report[-4000:]
    passed = re.search(r"(\d+) passed", report)
    assert passed and int(passed.group(1)) >= 10, report[-2000:]
    assert not re.search(r"\d+ (skipped|deselected|xfailed)", report), report[-2000:]
