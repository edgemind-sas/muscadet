"""Unit tests — payload shape detection (pure, no muscadet runtime)."""

import pytest

from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    _detect_payload_shape,
)


def _platform_export_min():
    return {
        "model": {"name": "X", "elements": {"components": {}, "connections": {}}},
        "kb_embedded": {"component_templates": {}},
    }


def _canonical_min():
    return {
        "model": {"name": "X", "elements": {"components": {}, "connections": {}}},
        "kb": {"component_templates": {}},
    }


def test_detect_platform_export_shape():
    assert _detect_payload_shape(_platform_export_min()) == "platform_export"


def test_detect_canonical_shape():
    assert _detect_payload_shape(_canonical_min()) == "canonical"


def test_platform_export_takes_precedence_over_canonical_kb():
    """Both keys present → platform_export wins (the kb_embedded path is
    used by the real platform export, the canonical 'kb' is just a test
    convenience that should never coexist with kb_embedded in practice)."""
    payload = _platform_export_min()
    payload["kb"] = {"component_templates": {}}
    assert _detect_payload_shape(payload) == "platform_export"


def test_reject_non_dict():
    with pytest.raises(Cod3sPlatformImportError, match="must be a dict"):
        _detect_payload_shape("not a dict")  # type: ignore[arg-type]


def test_reject_missing_model_key():
    with pytest.raises(Cod3sPlatformImportError, match="missing 'model'"):
        _detect_payload_shape({"kb_embedded": {}})


def test_reject_payload_with_no_resolvable_kb():
    with pytest.raises(Cod3sPlatformImportError, match="no resolvable KB"):
        _detect_payload_shape({"model": {"elements": {}}})


def test_reject_canonical_kb_without_component_templates():
    with pytest.raises(Cod3sPlatformImportError, match="no resolvable KB"):
        _detect_payload_shape({"model": {}, "kb": {}})
