"""Unit tests — connection spec extraction (pure, no muscadet runtime)."""

import logging

import pytest

from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    ComponentSpec,
    FlowSpec,
    _parse_connections,
)


def _comp(id_: str, name: str, *, inputs=(), outputs=()) -> ComponentSpec:
    flows = [FlowSpec(name=n, direction="input", logic="or") for n in inputs] + [
        FlowSpec(name=n, direction="output", logic=[]) for n in outputs
    ]
    return ComponentSpec(
        id=id_, name=name, class_name="Generic", flows=flows, metadata={}
    )


def test_basic_connection_uuid_to_display_name_resolution():
    components = [
        _comp("uuid-A", "A", outputs=["f1"]),
        _comp("uuid-B", "B", inputs=["f1"]),
    ]
    conns = _parse_connections(
        {
            "c1": {
                "component_source": "uuid-A",
                "component_target": "uuid-B",
                "interface_source": "f1",
                "interface_target": "f1",
            }
        },
        components,
    )
    assert len(conns) == 1
    assert conns[0].source_component == "A"
    assert conns[0].target_component == "B"
    assert conns[0].flow_name == "f1"


def test_unknown_source_component_raises():
    components = [_comp("uuid-A", "A", outputs=["f1"])]
    with pytest.raises(Cod3sPlatformImportError, match="unknown source component"):
        _parse_connections(
            {
                "c1": {
                    "component_source": "uuid-ghost",
                    "component_target": "uuid-A",
                    "interface_source": "f1",
                    "interface_target": "f1",
                }
            },
            components,
        )


def test_unknown_target_component_raises():
    components = [_comp("uuid-A", "A", outputs=["f1"])]
    with pytest.raises(Cod3sPlatformImportError, match="unknown target component"):
        _parse_connections(
            {
                "c1": {
                    "component_source": "uuid-A",
                    "component_target": "uuid-ghost",
                    "interface_source": "f1",
                    "interface_target": "f1",
                }
            },
            components,
        )


def test_source_interface_not_an_output_raises():
    components = [
        _comp("uuid-A", "A", inputs=["f1"]),  # f1 declared as input on source !
        _comp("uuid-B", "B", inputs=["f1"]),
    ]
    with pytest.raises(
        Cod3sPlatformImportError, match="not an output flow of component"
    ):
        _parse_connections(
            {
                "c1": {
                    "component_source": "uuid-A",
                    "component_target": "uuid-B",
                    "interface_source": "f1",
                    "interface_target": "f1",
                }
            },
            components,
        )


def test_target_interface_not_an_input_raises():
    components = [
        _comp("uuid-A", "A", outputs=["f1"]),
        _comp("uuid-B", "B", outputs=["f1"]),  # f1 declared as output on target !
    ]
    with pytest.raises(
        Cod3sPlatformImportError, match="not an input flow of component"
    ):
        _parse_connections(
            {
                "c1": {
                    "component_source": "uuid-A",
                    "component_target": "uuid-B",
                    "interface_source": "f1",
                    "interface_target": "f1",
                }
            },
            components,
        )


def test_missing_required_fields_raises():
    components = [_comp("uuid-A", "A", outputs=["f1"])]
    with pytest.raises(Cod3sPlatformImportError, match="missing required fields"):
        _parse_connections({"c1": {"component_source": "uuid-A"}}, components)


def test_asymmetric_interface_names_warns_but_succeeds(caplog):
    """The schema technically allows source != target interface names,
    but muscadet uses a single flow_name on both ends. We use the
    source name and emit a warning."""
    components = [
        _comp("uuid-A", "A", outputs=["f1"]),
        _comp("uuid-B", "B", inputs=["f1"]),
    ]
    with caplog.at_level(logging.WARNING, logger="muscadet.importers.cod3s_platform"):
        conns = _parse_connections(
            {
                "c1": {
                    "component_source": "uuid-A",
                    "component_target": "uuid-B",
                    "interface_source": "f1",
                    "interface_target": "f2_different",
                }
            },
            components,
        )
    # Source name wins
    assert conns[0].flow_name == "f1"
    assert any(
        "interface names differ" in record.message for record in caplog.records
    )


def test_empty_connections_dict():
    assert _parse_connections({}, []) == []
    assert _parse_connections(None, []) == []  # type: ignore[arg-type]
