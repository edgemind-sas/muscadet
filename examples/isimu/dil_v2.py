"""Interactive simulation factory for the COD3S Platform DIL V2 export.

Loads the real ``dil V2 + kb.json`` payload (26 components, 177
connections, 15 KB classes) committed under
``tests/fixtures/dil_v2_export.json`` and runs it through
``muscadet.importers.cod3s_platform.system_from_export`` to produce a
populated ``muscadet.System`` ready for ``cod3s-isimu``.

This is the importer's interactive smoke test: if the TUI can step
through the model without raising, the Phase 1 importer is end-to-end
consumable by the simulation stack — the same property that the slow
``test_isimu_start_does_not_raise`` test pins, but driveable by hand.

Usage::

    cod3s-isimu --factory examples.isimu.dil_v2:build

Unlike the small pedagogical examples in this directory, no scripted
``run()`` is provided — the system is too large for a meaningful
single-line snapshot. Inspect via the TUI instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import muscadet
from muscadet.importers.cod3s_platform import system_from_export

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "dil_v2_export.json"
)


def build() -> muscadet.System:
    with _FIXTURE.open() as f:
        payload = json.load(f)
    return system_from_export(payload)


if __name__ == "__main__":
    system = build()
    print(
        f"DIL V2 system built: {len(system.comp)} components, "
        f"name={system.name()!r}"
    )
