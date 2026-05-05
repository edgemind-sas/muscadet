"""Unit tests — flow spec extraction (pure, no muscadet runtime)."""

import pytest

from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    _parse_interface,
)


def test_input_default_logic_is_or():
    flow = _parse_interface(
        {"name": "in_a", "port_type": {"general": "input"}}
    )
    assert flow.direction == "input"
    assert flow.logic == "or"


def test_input_with_explicit_logic():
    flow = _parse_interface(
        {"name": "in_a", "port_type": {"general": "input"}, "logic": "and"}
    )
    assert flow.logic == "and"


def test_input_with_at_least_k_logic():
    flow = _parse_interface(
        {"name": "in_a", "port_type": {"general": "input"}, "logic": 2}
    )
    assert flow.logic == 2


def test_output_default_logic_is_empty_list():
    flow = _parse_interface(
        {"name": "out_a", "port_type": {"general": "output"}}
    )
    assert flow.direction == "output"
    assert flow.logic == []
    assert flow.logic_inner_mode == "or"
    assert flow.negate is False


def test_output_with_var_prod_cond():
    flow = _parse_interface(
        {
            "name": "out_a",
            "port_type": {"general": "output"},
            "logic": [["in_a"], ["in_b"]],
        }
    )
    assert flow.logic == [["in_a"], ["in_b"]]


def test_output_with_inner_mode_and():
    flow = _parse_interface(
        {
            "name": "out_a",
            "port_type": {"general": "output"},
            "logic": [["in_a"]],
            "logic_inner_mode": "and",
        }
    )
    assert flow.logic_inner_mode == "and"


def test_output_with_negate():
    flow = _parse_interface(
        {
            "name": "out_a",
            "port_type": {"general": "output"},
            "logic": [["in_a"]],
            "negate": True,
        }
    )
    assert flow.negate is True


def test_unknown_port_type_raises():
    with pytest.raises(Cod3sPlatformImportError, match="unsupported port_type"):
        _parse_interface(
            {"name": "x", "port_type": {"general": "trigger"}}
        )


def test_missing_name_raises():
    with pytest.raises(Cod3sPlatformImportError, match="missing 'name'"):
        _parse_interface({"port_type": {"general": "input"}})


def test_missing_port_type_raises():
    with pytest.raises(Cod3sPlatformImportError, match="unsupported port_type"):
        _parse_interface({"name": "x"})
