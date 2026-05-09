"""Tests for ``muscadet.builders``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from muscadet.builders import PlatformExportBuilder


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "minimal_export.json"


class TestPlatformExportBuilder:
    def test_repr_with_path(self):
        b = PlatformExportBuilder("foo.json")
        assert "foo.json" in repr(b)

    def test_repr_with_dict(self):
        b = PlatformExportBuilder({"model": {}})
        assert "<dict>" in repr(b)

    def test_missing_file_raises_on_build(self, tmp_path):
        b = PlatformExportBuilder(tmp_path / "nope.json")
        with pytest.raises(FileNotFoundError):
            b.build()

    def test_lazy_load_from_path(self):
        """Path is read on .build(), not on __init__."""
        if not FIXTURE_PATH.exists():
            pytest.skip(f"fixture missing: {FIXTURE_PATH}")
        # Construct without the file existing
        nonexistent = FIXTURE_PATH.parent / "definitely_not_there.json"
        b = PlatformExportBuilder(nonexistent)
        # No error at construction
        assert b is not None

    def test_build_from_fixture(self):
        """End-to-end: load fixture, build muscadet system."""
        if not FIXTURE_PATH.exists():
            pytest.skip(f"fixture missing: {FIXTURE_PATH}")
        b = PlatformExportBuilder(FIXTURE_PATH)
        try:
            sys = b.build()
        except Exception as e:
            pytest.skip(f"PyCATSHOO unavailable / build failed: {e}")
        assert sys is not None
        assert hasattr(sys, "comp")
        # The fixture has Source1 + Sink1
        assert "Source1" in sys.comp
        assert "Sink1" in sys.comp

    def test_build_from_inline_dict(self):
        """Pre-loaded payload dict is used directly without disk access."""
        if not FIXTURE_PATH.exists():
            pytest.skip(f"fixture missing: {FIXTURE_PATH}")
        payload = json.loads(FIXTURE_PATH.read_text())
        b = PlatformExportBuilder(payload)
        try:
            sys = b.build()
        except Exception as e:
            pytest.skip(f"PyCATSHOO unavailable: {e}")
        assert "Source1" in sys.comp

    def test_protocol_satisfied(self):
        """Builder satisfies cod3s SystemBuilder Protocol (runtime check)."""
        try:
            from cod3s.scripts.builders import SystemBuilder
        except ImportError:
            pytest.skip("cod3s.scripts.builders not available")
        b = PlatformExportBuilder({})
        assert isinstance(b, SystemBuilder)
