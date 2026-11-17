"""Interactive simulation factory for the DIL V2 export with UI overrides.

Sibling of :mod:`examples.isimu.dil_v2`, but loads
``tests/fixtures/dil_v2_attr.json``, an export where instance-level attribute
overrides have been applied through the UI: ``PLC_1`` and ``PLC_2`` carry a
``k=2`` logic on the ``RS485_UT_PLCY`` input port.

Use this factory to drive ``cod3s-isimu`` against a model that exercises the
``_apply_instance_overrides`` code path of the cod3s_platform importer
end-to-end (UI override -> JSON export -> muscadet System -> PyCATSHOO).

Usage::

    cod3s-isimu --factory examples.isimu.dil_v2_attr:build
"""

from __future__ import annotations

import muscadet

from .platform_fixture import build_from_fixture

#: The values an override carries when it restates the KB default.
DEFAULT_VALUES = ("or", False)


def build() -> muscadet.System:
    return build_from_fixture("dil_v2_attr.json")


def non_default_overrides(system: muscadet.System, comp_name: str) -> list:
    """The instance overrides of a component that depart from the KB default.

    ``metadata["instance_overrides"]`` is the importer's audit trail: a list of
    ``{"name", "role", "value"}`` entries.
    """
    trail = system.comp[comp_name].metadata.get("instance_overrides") or []
    return [entry for entry in trail if entry["value"] not in DEFAULT_VALUES]


if __name__ == "__main__":
    import cod3s

    system = build()
    try:
        print(
            f"DIL V2 (attr) system built: {len(system.comp)} components, "
            f"name={system.name()!r}"
        )
        for cname in ("PLC_1", "PLC_2"):
            print(
                f"  {cname} non-default overrides: "
                f"{non_default_overrides(system, cname)}"
            )
    finally:
        system.deleteSys()
        cod3s.terminate_session()
