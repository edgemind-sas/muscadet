"""Unit tests — full parse_platform_export end-to-end on the minimal fixture
+ assorted error paths (pure, no muscadet runtime)."""

import json
import os

import pytest

from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    parse_platform_export,
)


_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "minimal_export.json"
)


def _load_minimal():
    with open(_FIXTURE) as f:
        return json.load(f)


def test_parse_minimal_fixture_round_trip():
    payload = _load_minimal()
    ctx = parse_platform_export(payload)
    assert ctx.system_name == "MinimalModel"
    # 2 components
    names = sorted(c.name for c in ctx.components)
    assert names == ["Sink1", "Source1"]
    # 1 connection
    assert len(ctx.connections) == 1
    c = ctx.connections[0]
    assert c.source_component == "Source1"
    assert c.target_component == "Sink1"
    assert c.flow_name == "out_a"
    # Source KB metadata is preserved
    assert ctx.source_kb == {"name": "MinimalKB", "version": "0.0.1"}
    assert ctx.metadata["description"] == (
        "Synthetic 2-component fixture for unit tests"
    )


def test_parse_canonical_shape():
    """The canonical {model, kb} shape should produce the same context
    as the platform_export shape (modulo ``export_version`` which is
    only present on the export shape)."""
    full = _load_minimal()
    canonical = {
        "model": full["model"],
        "kb": full["kb_embedded"],
    }
    ctx = parse_platform_export(canonical)
    assert ctx.system_name == "MinimalModel"
    assert len(ctx.components) == 2
    assert len(ctx.connections) == 1


def test_class_name_preserved_on_each_component():
    payload = _load_minimal()
    ctx = parse_platform_export(payload)
    by_name = {c.name: c for c in ctx.components}
    assert by_name["Source1"].class_name == "Source"
    assert by_name["Sink1"].class_name == "Sink"


def test_platform_id_preserved_in_metadata():
    payload = _load_minimal()
    ctx = parse_platform_export(payload)
    by_name = {c.name: c for c in ctx.components}
    assert by_name["Source1"].metadata["platform_id"] == "id-source"
    assert by_name["Sink1"].metadata["platform_id"] == "id-sink"


def test_attributes_initial_preserved():
    payload = _load_minimal()
    ctx = parse_platform_export(payload)
    by_name = {c.name: c for c in ctx.components}
    assert by_name["Source1"].metadata["attributes_initial"] == [
        {"name": "out_a", "value": False}
    ]
    assert by_name["Sink1"].metadata["attributes_initial"] == []


def test_unknown_class_in_full_pipeline_raises():
    payload = _load_minimal()
    payload["model"]["elements"]["components"]["id-source"]["class_name"] = "Ghost"
    with pytest.raises(Cod3sPlatformImportError, match="unknown class"):
        parse_platform_export(payload)


def test_dangling_connection_in_full_pipeline_raises():
    payload = _load_minimal()
    payload["model"]["elements"]["connections"]["conn-1"][
        "component_source"
    ] = "id-ghost"
    with pytest.raises(Cod3sPlatformImportError, match="unknown source component"):
        parse_platform_export(payload)


def test_no_kb_at_all_raises():
    with pytest.raises(Cod3sPlatformImportError, match="no resolvable KB"):
        parse_platform_export({"model": {"elements": {}}})
