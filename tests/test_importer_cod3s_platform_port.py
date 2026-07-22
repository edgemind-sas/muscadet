"""prod_cond ``port`` hint through the COD3S Platform importer.

Locks that ``_order_outputs_by_deps`` orders a ``port:"out"`` output reference
after the referenced output (dependency), and that the importer resolves the
hint via the installed muscadet (>= 0.6.6, capability marker
``_SUPPORTS_PROD_COND_PORT``).
"""

import pytest

from muscadet.importers.cod3s_platform import (
    _SUPPORTS_PROD_COND_PORT,
    system_from_export,
)


@pytest.fixture
def cleanup_system():
    import cod3s

    systems: list = []
    yield systems
    for system in systems:
        try:
            system.deleteSys()
        except Exception:
            pass
    cod3s.terminate_session()


def _payload(interfaces, sys_name):
    return {
        "model": {
            "name": sys_name,
            "kb": {"name": "KB", "version": "1.0.0"},
            "elements": {
                "components": {
                    "c1": {"name": "C1", "class_name": "Cls", "attributes": []}
                },
                "connections": {},
            },
        },
        "kb": {"component_templates": {"Cls": {"interfaces": interfaces}}},
    }


def test_capability_marker_present():
    assert _SUPPORTS_PROD_COND_PORT is True


def test_port_out_output_reference_orders_and_resolves(cleanup_system):
    # ``ctrl`` (output) references ``flow`` (output) via port:"out"; the ctrl
    # interface is declared FIRST on purpose, so only port-aware ordering
    # (ctrl after flow) makes the build succeed and resolve to the output.
    system = system_from_export(
        _payload(
            {
                "flow__input": {"name": "flow", "port_type": {"general": "input"}},
                "ctrl__output": {
                    "name": "ctrl",
                    "port_type": {"general": "output"},
                    "prod_cond": [[{"name": "flow", "port": "out"}]],
                },
                "flow__output": {
                    "name": "flow",
                    "port_type": {"general": "output"},
                    "prod_cond": [["flow"]],
                },
            },
            sys_name="SysPortImp",
        )
    )
    cleanup_system.append(system)
    tap = system.comp["C1"]
    # ctrl resolved the OUTPUT flow (not the same-named input).
    assert tap.flows_out["ctrl"].var_prod_cond[0][0] is tap.flows_out["flow"]
