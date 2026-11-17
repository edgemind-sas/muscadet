"""Shared loader for the factories built on a COD3S Platform export fixture."""

from __future__ import annotations

import json
from pathlib import Path

import muscadet
from muscadet.importers.cod3s_platform import system_from_export

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def build_from_fixture(name: str) -> muscadet.System:
    """Build the system described by ``tests/fixtures/<name>``."""
    with (FIXTURES / name).open() as f:
        payload = json.load(f)
    return system_from_export(payload)
