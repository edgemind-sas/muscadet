"""Per-operand negation in prod_cond — COD3S Platform importer path.

A KB output interface may carry a ``prod_cond`` whose operands are either plain
flow-name strings (non-negated) or ``{"name": str, "negate": True}`` mappings
(negated). The importer threads the mapping through untouched to
``ObjFlow.add_flow`` (which resolves it into the ``var_prod_cond_negate``
matrix), and ``_order_outputs_by_deps`` reads the operand name whether it is a
string or a mapping.

Cf. per-operand-negation ADR (2026-07-22).
"""

import pytest

from muscadet.importers.cod3s_platform import (
    _SUPPORTS_PROD_COND_NEGATION,
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
                    "c1": {"name": "C1", "class_name": "Cls", "attributes": []},
                },
                "connections": {},
            },
        },
        "kb": {"component_templates": {"Cls": {"interfaces": interfaces}}},
    }


def test_capability_marker_present():
    # The platform probes this attribute to refuse simulating a negated KB on a
    # muscadet too old to support it.
    assert _SUPPORTS_PROD_COND_NEGATION is True


def test_negated_operand_builds_negate_matrix(cleanup_system):
    # An unconnected input defaults to False; NOT False = True, so the negated
    # output produces at t=0 (exercises the negation-gated start method too).
    system = system_from_export(
        _payload(
            {
                "b__input": {"name": "b", "port_type": {"general": "input"}},
                "out__output": {
                    "name": "out",
                    "port_type": {"general": "output"},
                    "prod_cond": [[{"name": "b", "negate": True}]],
                },
            },
            sys_name="SysNegImp1",
        )
    )
    cleanup_system.append(system)
    flow = system.comp["C1"].flows_out["out"]
    assert flow.var_prod_cond_negate == [[True]]

    system.isimu_start()
    assert system.comp["C1"].flows_in["b"].var_fed.value() is False
    assert flow.var_fed.value() is True  # NOT False
    system.isimu_stop()


def test_non_negated_operand_leaves_empty_matrix(cleanup_system):
    system = system_from_export(
        _payload(
            {
                "b__input": {"name": "b", "port_type": {"general": "input"}},
                "out__output": {
                    "name": "out",
                    "port_type": {"general": "output"},
                    "prod_cond": [["b"]],
                },
            },
            sys_name="SysNegImp2",
        )
    )
    cleanup_system.append(system)
    # Byte-identical parity: a plain-string operand keeps the empty matrix.
    assert system.comp["C1"].flows_out["out"].var_prod_cond_negate == []


def test_order_outputs_by_deps_reads_name_from_negated_ref(cleanup_system):
    # Output ``y`` references output ``x`` NEGATED. Dependency ordering must
    # read the operand name out of the mapping so ``x`` is created before ``y``
    # (a bare dict would otherwise never satisfy ``ref in available``).
    system = system_from_export(
        _payload(
            {
                "x__output": {"name": "x", "port_type": {"general": "output"}},
                "y__output": {
                    "name": "y",
                    "port_type": {"general": "output"},
                    "prod_cond": [[{"name": "x", "negate": True}]],
                },
            },
            sys_name="SysNegImp3",
        )
    )
    cleanup_system.append(system)
    flows = system.comp["C1"].flows_out
    assert "x" in flows and "y" in flows
    assert flows["y"].var_prod_cond_negate == [[True]]
