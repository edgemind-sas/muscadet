"""Unit tests — P1.6 instance overrides on the parse layer.

Covers ``_build_overrides_index``, ``_apply_instance_overrides``,
``_parse_input_logic_value``, and the end-to-end ``parse_platform_export``
behaviour when the model carries instance attributes with role=prod_init or
role=logic_in.

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


@pytest.fixture
def cleanup_system():
    """Tear down PyCATSHOO state after a runtime build (one System per name).

    Same pattern as test_importer_cod3s_platform_apply_001 — the test fills the
    yielded list with built systems; on teardown each is deleted and the
    session terminated so a later test can build a fresh one.
    """
    import cod3s

    systems: list = []
    yield systems
    for system in systems:
        try:
            system.deleteSys()
        except Exception:
            pass
    cod3s.terminate_session()


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
            {"name": "F-AEC", "role": "logic_in", "value": "2"},
            {"name": "F-AEBT", "role": "prod_init", "value": True},
        ]
        idx = _build_overrides_index(attrs)
        assert idx == {("F-AEC", "logic_in"): "2", ("F-AEBT", "prod_init"): True}

    def test_skips_observable_roles(self):
        # is_available + fed_in are runtime observables — never overrides
        attrs = [
            {"name": "F-AEBT", "role": "is_available", "value": True},
            {"name": "F-AEC", "role": "fed_in", "value": False},
        ]
        assert _build_overrides_index(attrs) == {}

    def test_skips_null_role(self):
        attrs = [{"name": "manual", "role": None, "value": "x"}]
        assert _build_overrides_index(attrs) == {}

    def test_skips_null_value(self):
        # Null value means "use KB default" — drop the entry.
        attrs = [{"name": "F-AEC", "role": "logic_in", "value": None}]
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
            flows, {("in_a", "logic_in"): "2"}, comp_name="c"
        )
        # in_a logic now 2, others untouched
        in_a = next(f for f in result if f.name == "in_a")
        in_b = next(f for f in result if f.name == "in_b")
        assert in_a.logic == 2
        assert in_b.logic == "or"

    def test_overrides_input_logic_to_and(self):
        flows = self._flows()
        result = _apply_instance_overrides(
            flows, {("in_a", "logic_in"): "and"}, comp_name="c"
        )
        assert next(f for f in result if f.name == "in_a").logic == "and"

    def test_overrides_output_init_value(self):
        flows = self._flows()
        result = _apply_instance_overrides(
            flows, {("out_x", "prod_init"): True}, comp_name="c"
        )
        out_x = next(f for f in result if f.name == "out_x")
        assert out_x.init_value is True

    def test_rejects_logic_in_on_output(self):
        flows = self._flows()
        with pytest.raises(Cod3sPlatformImportError, match="role=logic_in.*expects a input"):
            _apply_instance_overrides(
                flows, {("out_x", "logic_in"): "and"}, comp_name="c"
            )

    def test_rejects_prod_init_on_input(self):
        flows = self._flows()
        with pytest.raises(Cod3sPlatformImportError, match="role=prod_init.*expects a output"):
            _apply_instance_overrides(
                flows, {("in_a", "prod_init"): True}, comp_name="c"
            )

    def test_overrides_input_var_in_default(self):
        flows = self._flows()
        result = _apply_instance_overrides(
            flows, {("in_a", "var_in_default"): True}, comp_name="c"
        )
        assert next(f for f in result if f.name == "in_a").var_in_default is True

    def test_rejects_var_in_default_on_output(self):
        flows = self._flows()
        with pytest.raises(Cod3sPlatformImportError, match="role=var_in_default.*expects a input"):
            _apply_instance_overrides(
                flows, {("out_x", "var_in_default"): True}, comp_name="c"
            )

    def test_stale_override_silently_ignored(self):
        # Override pointing to an unknown flow (e.g. KB removed it) :
        # log + ignore, don't crash.
        flows = self._flows()
        result = _apply_instance_overrides(
            flows, {("DELETED", "logic_in"): "and"}, comp_name="c"
        )
        # Flows unchanged
        assert [f.name for f in result] == ["in_a", "in_b", "out_x"]
        assert all(f.logic == "or" for f in result if f.direction == "input")

    def test_preserves_flow_order(self):
        flows = self._flows()
        result = _apply_instance_overrides(
            flows,
            {("in_a", "logic_in"): "2", ("out_x", "prod_init"): True},
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
            {"name": "in_a", "role": "logic_in", "value": "3"},
        ]))
        comp = ctx.components[0]
        in_a = next(f for f in comp.flows if f.name == "in_a")
        assert in_a.logic == 3
        # Out unchanged
        out_x = next(f for f in comp.flows if f.name == "out_x")
        assert out_x.init_value is None

    def test_init_override_propagated_through_parse(self):
        ctx = parse_platform_export(_payload([
            {"name": "out_x", "role": "prod_init", "value": True},
        ]))
        comp = ctx.components[0]
        out_x = next(f for f in comp.flows if f.name == "out_x")
        assert out_x.init_value is True

    def test_combined_logic_and_init_overrides(self):
        ctx = parse_platform_export(_payload([
            {"name": "in_a", "role": "logic_in", "value": "and"},
            {"name": "out_x", "role": "prod_init", "value": True},
        ]))
        comp = ctx.components[0]
        in_a = next(f for f in comp.flows if f.name == "in_a")
        out_x = next(f for f in comp.flows if f.name == "out_x")
        assert in_a.logic == "and"
        assert out_x.init_value is True

    def test_fed_in_attribute_does_not_override(self):
        # role=fed_in is a runtime observable — must be ignored.
        ctx = parse_platform_export(_payload([
            {"name": "in_a", "role": "fed_in", "value": True},
        ]))
        comp = ctx.components[0]
        in_a = next(f for f in comp.flows if f.name == "in_a")
        assert in_a.logic == "or"  # KB default unchanged

    def test_overrides_persisted_in_component_metadata(self):
        ctx = parse_platform_export(_payload([
            {"name": "in_a", "role": "logic_in", "value": "2"},
        ]))
        comp = ctx.components[0]
        # Traceability: instance_overrides bag carries the raw map
        assert comp.metadata["instance_overrides"] == {("in_a", "logic_in"): "2"}
        # And the raw attributes_initial list is preserved verbatim
        assert comp.metadata["attributes_initial"] == [
            {"name": "in_a", "role": "logic_in", "value": "2"},
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
                {"name": "x", "role": "is_available", "value": True},
                {"name": "y", "role": "fed_in", "value": False},
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


# ---------------------------------------------------------------------------
# Service functions — role=active_init -> FlowOut.var_is_active_default
# (2026-06-15). A service function is dormant by default (fed_out=False even
# when prod_cond holds) and only fed once an effect sets var_is_active True,
# ORTHOGONALLY to prod_cond.
# ---------------------------------------------------------------------------


class TestServiceFunctionActiveInit:
    def test_build_overrides_index_keeps_active_init(self):
        idx = _build_overrides_index([
            {"name": "SRVFEU", "role": "active_init", "value": False},
        ])
        assert idx == {("SRVFEU", "active_init"): False}

    def test_build_overrides_index_skips_is_active_observable(self):
        # is_active is a runtime observable (the effect target), never a
        # config override on the parse layer.
        assert _build_overrides_index([
            {"name": "SRVFEU", "role": "is_active", "value": True},
        ]) == {}

    def test_apply_active_init_sets_is_active_default(self):
        flows = [
            FlowSpec(name="in_a", direction="input", logic="or"),
            FlowSpec(name="SRVFEU", direction="output", logic=[["in_a"]]),
        ]
        result = _apply_instance_overrides(
            flows, {("SRVFEU", "active_init"): False}, comp_name="c"
        )
        srv = next(f for f in result if f.name == "SRVFEU")
        # Dormancy is orthogonal to prod_cond: the flow keeps its prod_cond.
        assert srv.is_active_default is False
        assert srv.logic == [["in_a"]]

    def test_rejects_active_init_on_input(self):
        flows = [FlowSpec(name="in_a", direction="input", logic="or")]
        with pytest.raises(Cod3sPlatformImportError, match="role=active_init.*expects a output"):
            _apply_instance_overrides(
                flows, {("in_a", "active_init"): False}, comp_name="c"
            )

    def test_end_to_end_active_init_propagated(self):
        # A service-function output WITH a prod_cond + active_init=False.
        ctx = parse_platform_export({
            "model": {
                "name": "M",
                "kb": {"name": "KB", "version": "1.0.0"},
                "elements": {
                    "components": {
                        "c1": {
                            "name": "C1",
                            "class_name": "Cls",
                            "attributes": [
                                {"name": "SRVFEU", "role": "active_init", "value": False},
                            ],
                        },
                    },
                    "connections": {},
                },
            },
            "kb": {
                "component_templates": {
                    "Cls": {
                        "interfaces": {
                            "in_a__input": {"name": "in_a", "port_type": {"general": "input"}},
                            "SRVFEU__output": {
                                "name": "SRVFEU",
                                "port_type": {"general": "output"},
                                "prod_cond": [["in_a"]],
                            },
                        },
                    },
                },
            },
        })
        comp = ctx.components[0]
        srv = next(f for f in comp.flows if f.name == "SRVFEU")
        assert srv.is_active_default is False
        # prod_cond preserved alongside dormancy
        assert srv.logic == [["in_a"]]

    def test_runtime_flowout_gets_var_is_active_default(self, cleanup_system):
        # Full build to the muscadet runtime: the FlowOut object carries
        # var_is_active_default=False so var_fed stays False until an effect
        # sets var_is_active (cf. FlowOut.create_sensitive_set_flow_fed_out:
        # var_fed = var_prod AND var_is_active AND var_fed_available_out).
        from muscadet.importers.cod3s_platform import system_from_export

        system = system_from_export({
            "model": {
                "name": "Msvc",
                "kb": {"name": "KB", "version": "1.0.0"},
                "elements": {
                    "components": {
                        "c1": {
                            "name": "C1",
                            "class_name": "Cls",
                            "attributes": [
                                {"name": "SRVFEU", "role": "active_init", "value": False},
                            ],
                        },
                    },
                    "connections": {},
                },
            },
            "kb": {
                "component_templates": {
                    "Cls": {
                        "interfaces": {
                            "in_a__input": {"name": "in_a", "port_type": {"general": "input"}},
                            "SRVFEU__output": {
                                "name": "SRVFEU",
                                "port_type": {"general": "output"},
                                "prod_cond": [["in_a"]],
                            },
                        },
                    },
                },
            },
        })
        cleanup_system.append(system)
        comp = system.comp["C1"]
        srv = comp.flows_out["SRVFEU"]
        assert srv.var_is_active_default is False

    def test_runtime_normal_flow_keeps_active_default_true(self, cleanup_system):
        # No active_init override → normal flow stays always-active (default).
        from muscadet.importers.cod3s_platform import system_from_export

        system = system_from_export({
            "model": {
                "name": "Mnorm",
                "kb": {"name": "KB", "version": "1.0.0"},
                "elements": {
                    "components": {
                        "c1": {"name": "C1", "class_name": "Cls", "attributes": []},
                    },
                    "connections": {},
                },
            },
            "kb": {
                "component_templates": {
                    "Cls": {
                        "interfaces": {
                            "out_x__output": {"name": "out_x", "port_type": {"general": "output"}},
                        },
                    },
                },
            },
        })
        cleanup_system.append(system)
        comp = system.comp["C1"]
        assert comp.flows_out["out_x"].var_is_active_default is True


# ---------------------------------------------------------------------------
# Service functions — role=fed_available_init -> FlowOut.var_fed_available_out_init
# (2026-06-15, USER-FACING dormancy). A dormant flow starts (and reinitialises)
# with var_fed_available_out=False : unfed AND "unavailable" downstream until an
# effect re-opens the gate. Orthogonal to prod_cond.
# ---------------------------------------------------------------------------


class TestServiceFunctionFedAvailableInit:
    def test_build_overrides_index_keeps_fed_available_init(self):
        idx = _build_overrides_index([
            {"name": "SRVFEU", "role": "fed_available_init", "value": False},
        ])
        assert idx == {("SRVFEU", "fed_available_init"): False}

    def test_apply_fed_available_init_sets_flowspec_field(self):
        flows = [
            FlowSpec(name="in_a", direction="input", logic="or"),
            FlowSpec(name="SRVFEU", direction="output", logic=[["in_a"]]),
        ]
        result = _apply_instance_overrides(
            flows, {("SRVFEU", "fed_available_init"): False}, comp_name="c"
        )
        srv = next(f for f in result if f.name == "SRVFEU")
        assert srv.fed_available_init is False
        # dormancy orthogonal to prod_cond: the flow keeps its prod_cond
        assert srv.logic == [["in_a"]]
        # and does NOT touch the var_is_active path
        assert srv.is_active_default is None

    def test_rejects_fed_available_init_on_input(self):
        flows = [FlowSpec(name="in_a", direction="input", logic="or")]
        with pytest.raises(Cod3sPlatformImportError, match="role=fed_available_init.*expects a output"):
            _apply_instance_overrides(
                flows, {("in_a", "fed_available_init"): False}, comp_name="c"
            )

    def test_runtime_flowout_gets_var_fed_available_out_init(self, cleanup_system):
        from muscadet.importers.cod3s_platform import system_from_export

        system = system_from_export({
            "model": {
                "name": "Msvc_avail",
                "kb": {"name": "KB", "version": "1.0.0"},
                "elements": {
                    "components": {
                        "c1": {
                            "name": "C1",
                            "class_name": "Cls",
                            "attributes": [
                                {"name": "SRVFEU", "role": "fed_available_init", "value": False},
                            ],
                        },
                    },
                    "connections": {},
                },
            },
            "kb": {
                "component_templates": {
                    "Cls": {
                        "interfaces": {
                            "in_a__input": {"name": "in_a", "port_type": {"general": "input"}},
                            "SRVFEU__output": {
                                "name": "SRVFEU",
                                "port_type": {"general": "output"},
                                "prod_cond": [["in_a"]],
                            },
                        },
                    },
                },
            },
        })
        cleanup_system.append(system)
        srv = system.comp["C1"].flows_out["SRVFEU"]
        assert srv.var_fed_available_out_init is False

    def test_runtime_normal_flow_keeps_fed_available_out_init_true(self, cleanup_system):
        from muscadet.importers.cod3s_platform import system_from_export

        system = system_from_export({
            "model": {
                "name": "Mnorm_avail",
                "kb": {"name": "KB", "version": "1.0.0"},
                "elements": {
                    "components": {"c1": {"name": "C1", "class_name": "Cls", "attributes": []}},
                    "connections": {},
                },
            },
            "kb": {
                "component_templates": {
                    "Cls": {
                        "interfaces": {
                            "out_x__output": {"name": "out_x", "port_type": {"general": "output"}},
                        },
                    },
                },
            },
        })
        cleanup_system.append(system)
        assert system.comp["C1"].flows_out["out_x"].var_fed_available_out_init is True
