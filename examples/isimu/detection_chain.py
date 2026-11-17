"""Interactive simulation factory for the COD3S Platform Detection chain V2 export.

Loads the real ``detection chain + kb.json`` payload (26 components, 177
connections, 15 KB classes) committed under
``tests/fixtures/detection_chain_export.json`` and runs it through
``muscadet.importers.cod3s_platform.system_from_export`` to produce a
populated ``muscadet.System`` ready for ``cod3s-isimu``.

This is the importer's interactive smoke test: if the TUI can step
through the model without raising, the Phase 1 importer is end-to-end
consumable by the simulation stack — the same property that the slow
``test_isimu_start_does_not_raise`` test pins, but driveable by hand.

Usage::

    cod3s-isimu --factory examples.isimu.detection_chain:build

Unlike the small pedagogical examples in this directory, no scripted
``run()`` is provided — the system is too large for a meaningful
single-line snapshot. Inspect via the TUI instead.
"""

from __future__ import annotations

import muscadet

from .platform_fixture import build_from_fixture


def build() -> muscadet.System:
    return build_from_fixture("detection_chain_export.json")


if __name__ == "__main__":
    import cod3s

    system = build()
    try:
        print(
            f"Detection chain V2 system built: {len(system.comp)} components, "
            f"name={system.name()!r}"
        )
    finally:
        system.deleteSys()
        cod3s.terminate_session()
