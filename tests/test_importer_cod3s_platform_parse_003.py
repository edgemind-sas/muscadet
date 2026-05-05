"""Unit tests — component spec extraction (pure, no muscadet runtime)."""

import pytest

from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    FlowSpec,
    _parse_components,
)


def _kb_lookup_with(*classes):
    return {cls: [] for cls in classes}


def test_component_basic():
    components = _parse_components(
        {
            "uuid-1": {"name": "Pump1", "class_name": "Pump", "attributes": []},
        },
        _kb_lookup_with("Pump"),
    )
    assert len(components) == 1
    assert components[0].id == "uuid-1"
    assert components[0].name == "Pump1"
    assert components[0].class_name == "Pump"
    assert components[0].metadata["platform_id"] == "uuid-1"
    assert components[0].metadata["attributes_initial"] == []


def test_component_preserves_attributes_in_metadata():
    components = _parse_components(
        {
            "uuid-1": {
                "name": "Pump1",
                "class_name": "Pump",
                "attributes": [{"name": "in_a", "value": False}],
            }
        },
        _kb_lookup_with("Pump"),
    )
    assert components[0].metadata["attributes_initial"] == [
        {"name": "in_a", "value": False}
    ]


def test_component_inherits_kb_flows():
    flows = [FlowSpec(name="in_a", direction="input", logic="or")]
    components = _parse_components(
        {"uuid-1": {"name": "Pump1", "class_name": "Pump"}},
        {"Pump": flows},
    )
    assert components[0].flows == flows


def test_unknown_class_name_raises():
    with pytest.raises(Cod3sPlatformImportError, match="unknown class"):
        _parse_components(
            {"uuid-1": {"name": "Pump1", "class_name": "Ghost"}},
            _kb_lookup_with("Pump"),
        )


def test_missing_name_raises():
    with pytest.raises(Cod3sPlatformImportError, match="missing 'name'"):
        _parse_components(
            {"uuid-1": {"class_name": "Pump"}},
            _kb_lookup_with("Pump"),
        )


def test_missing_class_name_raises():
    with pytest.raises(Cod3sPlatformImportError, match="missing 'class_name'"):
        _parse_components(
            {"uuid-1": {"name": "Pump1"}},
            _kb_lookup_with("Pump"),
        )


def test_duplicate_display_name_raises():
    with pytest.raises(Cod3sPlatformImportError, match="Duplicate"):
        _parse_components(
            {
                "uuid-1": {"name": "Pump1", "class_name": "Pump"},
                "uuid-2": {"name": "Pump1", "class_name": "Pump"},
            },
            _kb_lookup_with("Pump"),
        )


def test_empty_components_dict():
    assert _parse_components({}, _kb_lookup_with("Pump")) == []
    assert _parse_components(None, _kb_lookup_with("Pump")) == []  # type: ignore[arg-type]
