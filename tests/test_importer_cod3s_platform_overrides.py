"""Unit tests — P1.6 instance overrides on the parse layer.

Covers ``_build_overrides_index``, ``_apply_instance_overrides``,
``_parse_input_logic_value``, and the end-to-end ``parse_platform_export``
behaviour when the model carries instance attributes with role=init or
role=logic.

These tests exercise the pure parse layer only — no muscadet runtime,
no PyCATSHOO. They verify that the FlowSpec list emitted by the parser
already reflects the effective post-override configuration.
"""

import pytest

from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    FlowSpec,
    _apply_instance_overrides,
    _build_overrides_index,
    _parse_input_logic_value,
    parse_platform_export,
)


# ---------------------------------------------------------------------------
# _parse_input_logic_value
# ---------------------------------------------------------------------------


class TestParseInputLogicValue:
    def test_string_and_or_passthrough(self):
        assert _parse_input_logic_value("and", flow_name="x", comp_name="c") == "and"
        assert _parse_input_logic_value("or", flow_name="x", comp_name="c") == "or"

    def test_decimal_string_to_int(self):
        assert _parse_input_logic_value("2", flow_name="x", comp_name="c") == 2
        assert _parse_input_logic_value("5", flow_name="x", comp_name="c") == 5

    def test_native_int(self):
        assert _parse_input_logic_value(3, flow_name="x", comp_name="c") == 3

    def test_invalid_string_rejected(self):
        with pytest.raises(Cod3sPlatformImportError, match="invalid logic"):
            _parse_input_logic_value("xor", flow_name="x", comp_name="c")

    def test_zero_rejected(self):
        with pytest.raises(Cod3sPlatformImportError, match="must be >= 1"):
            _parse_input_logic_value("0", flow_name="x", comp_name="c")

    def test_negative_rejected(self):
        with pytest.raises(Cod3sPlatformImportError, match="must be >= 1"):
            _parse_input_logic_value(-1, flow_name="x", comp_name="c")

    def test_bool_rejected(self):
        # Python : isinstance(True, int) is True. We must reject explicitly
        # since booleans aren't valid k-of-n values.
        with pytest.raises(Cod3sPlatformImportError, match="of type bool"):
            _parse_input_logic_value(True, flow_name="x", comp_name="c")


# ---------------------------------------------------------------------------
# _build_overrides_index
# ---------------------------------------------------------------------------


class TestBuildOverridesIndex:
    def test_indexes_by_name_role(self):
        attrs = [
            {"name": "F-AEC", "role": "logic", "value": "2"},
            {"name": "F-AEBT", "role": "init", "value": True},
        ]
        idx = _build_overrides_index(attrs)
        assert idx == {("F-AEC", "logic"): "2", ("F-AEBT", "init"): True}

    def test_skips_observable_roles(self):
        # availability + state are runtime observables — never overrides
        attrs = [
            {"name": "F-AEBT", "role": "availability", "value": True},
            {"name": "F-AEC", "role": "state", "value": False},
        ]
        assert _build_overrides_index(attrs) == {}

    def test_skips_null_role(self):
        attrs = [{"name": "manual", "role": None, "value": "x"}]
        assert _build_overrides_index(attrs) == {}

    def test_skips_null_value(self):
        # Null value means "use KB default" — drop the entry.
        attrs = [{"name": "F-AEC", "role": "logic", "value": None}]
        assert _build_overrides_index(attrs) == {}

    def test_skips_legacy_no_role(self):
        attrs = [{"name": "X", "value": True}]
        assert _build_overrides_index(attrs) == {}

    def test_handles_empty(self):
        assert _build_overrides_index([]) == {}
        assert _build_overrides_index(None) == {}


# ---------------------------------------------------------------------------
# _apply_instance_overrides
# ---------------------------------------------------------------------------


class TestApplyInstanceOverrides:
    def _flows(self):
        return [
            FlowSpec(name="in_a", direction="input", logic="or"),
            FlowSpec(name="in_b", direction="input", logic="or"),
            FlowSpec(name="out_x", direction="output", logic=[]),
        ]

    def test_overrides_input_logic_to_int(self):
        flows = self._flows()
        result = _apply_instance_overrides(
            flows, {("in_a", "logic"): "2"}, comp_name="c"
        )
        # in_a logic now 2, others untouched
        in_a = next(f for f in result if f.name == "in_a")
        in_b = next(f for f in result if f.name == "in_b")
        assert in_a.logic == 2
        assert in_b.logic == "or"

    def test_overrides_input_logic_to_and(self):
        flows = self._flows()
        result = _apply_instance_overrides(
            flows, {("in_a", "logic"): "and"}, comp_name="c"
        )
        assert next(f for f in result if f.name == "in_a").logic == "and"

    def test_overrides_output_init_value(self):
        flows = self._flows()
        result = _apply_instance_overrides(
            flows, {("out_x", "init"): True}, comp_name="c"
        )
        out_x = next(f for f in result if f.name == "out_x")
        assert out_x.init_value is True

    def test_rejects_logic_on_output(self):
        flows = self._flows()
        with pytest.raises(Cod3sPlatformImportError, match="role=logic.*non-input"):
            _apply_instance_overrides(
                flows, {("out_x", "logic"): "and"}, comp_name="c"
            )

    def test_rejects_init_on_input(self):
        flows = self._flows()
        with pytest.raises(Cod3sPlatformImportError, match="role=init.*non-output"):
            _apply_instance_overrides(
                flows, {("in_a", "init"): True}, comp_name="c"
            )

    def test_stale_override_silently_ignored(self):
        # Override pointing to an unknown flow (e.g. KB removed it) :
        # log + ignore, don't crash.
        flows = self._flows()
        result = _apply_instance_overrides(
            flows, {("DELETED", "logic"): "and"}, comp_name="c"
        )
        # Flows unchanged
        assert [f.name for f in result] == ["in_a", "in_b", "out_x"]
        assert all(f.logic == "or" for f in result if f.direction == "input")

    def test_preserves_flow_order(self):
        flows = self._flows()
        result = _apply_instance_overrides(
            flows,
            {("in_a", "logic"): "2", ("out_x", "init"): True},
            comp_name="c",
        )
        assert [f.name for f in result] == ["in_a", "in_b", "out_x"]


# ---------------------------------------------------------------------------
# End-to-end parse_platform_export with instance overrides
# ---------------------------------------------------------------------------


def _payload(component_attributes):
    """Build a minimal canonical payload with one component carrying given attrs."""
    return {
        "model": {
            "name": "M",
            "kb": {"name": "KB", "version": "1.0.0"},
            "elements": {
                "components": {
                    "c1": {
                        "name": "C1",
                        "class_name": "Cls",
                        "attributes": component_attributes,
                    },
                },
                "connections": {},
            },
        },
        "kb": {
            "component_templates": {
                "Cls": {
                    "interfaces": {
                        "in_a__input": {
                            "name": "in_a",
                            "port_type": {"general": "input"},
                        },
                        "out_x__output": {
                            "name": "out_x",
                            "port_type": {"general": "output"},
                        },
                    },
                },
            },
        },
    }


class TestEndToEndOverrides:
    def test_logic_override_propagated_through_parse(self):
        ctx = parse_platform_export(_payload([
            {"name": "in_a", "role": "logic", "value": "3"},
        ]))
        comp = ctx.components[0]
        in_a = next(f for f in comp.flows if f.name == "in_a")
        assert in_a.logic == 3
        # Out unchanged
        out_x = next(f for f in comp.flows if f.name == "out_x")
        assert out_x.init_value is None

    def test_init_override_propagated_through_parse(self):
        ctx = parse_platform_export(_payload([
            {"name": "out_x", "role": "init", "value": True},
        ]))
        comp = ctx.components[0]
        out_x = next(f for f in comp.flows if f.name == "out_x")
        assert out_x.init_value is True

    def test_combined_logic_and_init_overrides(self):
        ctx = parse_platform_export(_payload([
            {"name": "in_a", "role": "logic", "value": "and"},
            {"name": "out_x", "role": "init", "value": True},
        ]))
        comp = ctx.components[0]
        in_a = next(f for f in comp.flows if f.name == "in_a")
        out_x = next(f for f in comp.flows if f.name == "out_x")
        assert in_a.logic == "and"
        assert out_x.init_value is True

    def test_state_attribute_does_not_override(self):
        # role=state is a runtime observable — must be ignored.
        ctx = parse_platform_export(_payload([
            {"name": "in_a", "role": "state", "value": True},
        ]))
        comp = ctx.components[0]
        in_a = next(f for f in comp.flows if f.name == "in_a")
        assert in_a.logic == "or"  # KB default unchanged

    def test_overrides_persisted_in_component_metadata(self):
        ctx = parse_platform_export(_payload([
            {"name": "in_a", "role": "logic", "value": "2"},
        ]))
        comp = ctx.components[0]
        # Traceability: instance_overrides bag carries the raw map
        assert comp.metadata["instance_overrides"] == {("in_a", "logic"): "2"}
        # And the raw attributes_initial list is preserved verbatim
        assert comp.metadata["attributes_initial"] == [
            {"name": "in_a", "role": "logic", "value": "2"},
        ]


class TestParseInitValue:
    """Strict init override coercion (P2 — todo 053).

    Symmetric of TestParseInputLogicValue. The Python idiom
    ``bool(non_empty_string)`` is True for ``"false"`` ; the parser
    must refuse string forms that aren't canonical true/false.
    """

    def test_native_true(self):
        from muscadet.importers.cod3s_platform import _parse_init_value
        assert _parse_init_value(True, flow_name="x", comp_name="c") is True

    def test_native_false(self):
        from muscadet.importers.cod3s_platform import _parse_init_value
        assert _parse_init_value(False, flow_name="x", comp_name="c") is False

    def test_string_true_canonical(self):
        from muscadet.importers.cod3s_platform import _parse_init_value
        assert _parse_init_value("true", flow_name="x", comp_name="c") is True
        assert _parse_init_value(" TRUE ", flow_name="x", comp_name="c") is True
        assert _parse_init_value("1", flow_name="x", comp_name="c") is True

    def test_string_false_not_silently_truthy(self):
        from muscadet.importers.cod3s_platform import _parse_init_value, Cod3sPlatformImportError
        # The bug we guard against: bool("false") == True in pure Python.
        assert _parse_init_value("false", flow_name="x", comp_name="c") is False
        assert _parse_init_value("0", flow_name="x", comp_name="c") is False

    def test_arbitrary_string_rejected(self):
        from muscadet.importers.cod3s_platform import _parse_init_value, Cod3sPlatformImportError
        with pytest.raises(Cod3sPlatformImportError, match="invalid init"):
            _parse_init_value("yes", flow_name="x", comp_name="c")
        with pytest.raises(Cod3sPlatformImportError, match="invalid init"):
            _parse_init_value("abc", flow_name="x", comp_name="c")

    def test_non_string_non_bool_rejected(self):
        from muscadet.importers.cod3s_platform import _parse_init_value, Cod3sPlatformImportError
        with pytest.raises(Cod3sPlatformImportError, match="invalid init"):
            _parse_init_value(1, flow_name="x", comp_name="c")
        with pytest.raises(Cod3sPlatformImportError, match="invalid init"):
            _parse_init_value(None, flow_name="x", comp_name="c")
        with pytest.raises(Cod3sPlatformImportError, match="invalid init"):
            _parse_init_value([], flow_name="x", comp_name="c")


class TestUnknownRoleHandling:
    """Unknown attribute roles are logged + ignored, not silently dropped (todo 055-D)."""

    def test_unknown_role_logs_warning(self, caplog):
        import logging
        from muscadet.importers.cod3s_platform import _build_overrides_index
        with caplog.at_level(logging.WARNING, logger="muscadet.importers.cod3s_platform"):
            idx = _build_overrides_index([
                {"name": "x", "role": "spurious", "value": "y"},
            ])
        assert idx == {}
        assert any("Unknown attribute role" in rec.getMessage() for rec in caplog.records)

    def test_observable_role_silent(self, caplog):
        import logging
        from muscadet.importers.cod3s_platform import _build_overrides_index
        with caplog.at_level(logging.WARNING, logger="muscadet.importers.cod3s_platform"):
            idx = _build_overrides_index([
                {"name": "x", "role": "availability", "value": True},
                {"name": "y", "role": "state", "value": False},
            ])
        assert idx == {}
        # No warning for observable roles — they're a known taxonomy.
        assert not any("Unknown" in rec.getMessage() for rec in caplog.records)


class TestInputLogicWhitespace:
    """Whitespace handling on logic value strings (todo 055-C)."""

    def test_whitespace_string_int(self):
        from muscadet.importers.cod3s_platform import _parse_input_logic_value
        assert _parse_input_logic_value(" 2 ", flow_name="x", comp_name="c") == 2

    def test_whitespace_string_keyword(self):
        from muscadet.importers.cod3s_platform import _parse_input_logic_value
        assert _parse_input_logic_value(" or ", flow_name="x", comp_name="c") == "or"
        assert _parse_input_logic_value(" and ", flow_name="x", comp_name="c") == "and"

    def test_float_string_rejected(self):
        from muscadet.importers.cod3s_platform import _parse_input_logic_value, Cod3sPlatformImportError
        with pytest.raises(Cod3sPlatformImportError, match="invalid logic"):
            _parse_input_logic_value("2.5", flow_name="x", comp_name="c")

    def test_empty_string_rejected(self):
        from muscadet.importers.cod3s_platform import _parse_input_logic_value, Cod3sPlatformImportError
        with pytest.raises(Cod3sPlatformImportError, match="invalid logic"):
            _parse_input_logic_value("", flow_name="x", comp_name="c")
