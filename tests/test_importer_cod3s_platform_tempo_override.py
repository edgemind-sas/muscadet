"""Per-instance tempo overrides — importer parse + runtime.

The COD3S Platform «tempo en attributs» chantier (2026-07) promotes a flow's
tempo configuration to two attribute roles editable at the MODEL level:

- ``tempo_activation``   -> FlowOutTempo.occ_enable_flow
- ``tempo_deactivation`` -> FlowOutTempo.occ_disable_flow

The override value is an occurrence-law dict in SHORT wire form
(``{"cls": "delay"|"exp"|"inst", ...}``) or the sentinel ``{"cls": "none"}``
meaning "force classic (no law)". An absent value (``None``) is dropped upstream
(``_build_overrides_index``) and means "inherit the KB default".

Setting a law on a classic flow PROMOTES it to tempo; clearing the last law with
the sentinel DEMOTES it back to classic (``_derive_output_flow_type``). ``no-law``
FlowOutTempo is byte-identical to a plain FlowOut (muscadet 0.6.5), so a demoted
flow behaves exactly like a classic one.
"""

import pytest

from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    FlowSpec,
    _SUPPORTS_INSTANCE_TEMPO_OVERRIDE,
    _apply_instance_overrides,
    _build_overrides_index,
    _derive_output_flow_type,
    parse_platform_export,
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


def test_capability_marker_present():
    # The platform probes this to refuse simulating a model with tempo overrides
    # on an older muscadet.
    assert _SUPPORTS_INSTANCE_TEMPO_OVERRIDE is True


# ---------------------------------------------------------------------------
# _derive_output_flow_type
# ---------------------------------------------------------------------------


class TestDeriveOutputFlowType:
    def _out(self, **kw):
        return FlowSpec(name="o", direction="output", logic=[], **kw)

    def test_no_law_is_classic(self):
        assert _derive_output_flow_type(self._out()) == "classic"

    def test_enable_law_is_tempo(self):
        assert _derive_output_flow_type(self._out(occ_enable={"cls": "delay", "time": 3})) == "tempo"

    def test_disable_law_is_tempo(self):
        assert _derive_output_flow_type(self._out(occ_disable={"cls": "exp", "rate": 1e-3})) == "tempo"

    def test_on_trigger_is_preserved(self):
        # on_trigger is a distinct flow class, never derived from occ laws.
        assert _derive_output_flow_type(self._out(flow_type="on_trigger")) == "on_trigger"


# ---------------------------------------------------------------------------
# _build_overrides_index — tempo roles
# ---------------------------------------------------------------------------


class TestBuildOverridesIndexTempo:
    def test_keeps_tempo_law_dict(self):
        idx = _build_overrides_index([
            {"name": "flow", "role": "tempo_activation", "value": {"cls": "delay", "time": 3}},
        ])
        assert idx == {("flow", "tempo_activation"): {"cls": "delay", "time": 3}}

    def test_keeps_none_sentinel(self):
        # {"cls": "none"} is a real (non-None) value: force classic. Kept.
        idx = _build_overrides_index([
            {"name": "flow", "role": "tempo_deactivation", "value": {"cls": "none"}},
        ])
        assert idx == {("flow", "tempo_deactivation"): {"cls": "none"}}

    def test_drops_null_value_inherit(self):
        # value=None means inherit the KB default — dropped.
        assert _build_overrides_index([
            {"name": "flow", "role": "tempo_activation", "value": None},
        ]) == {}


# ---------------------------------------------------------------------------
# _apply_instance_overrides — tempo roles
# ---------------------------------------------------------------------------


class TestApplyTempoOverride:
    def _flows(self, **out_kw):
        return [
            FlowSpec(name="in_a", direction="input", logic="or"),
            FlowSpec(name="flow", direction="output", logic=[], **out_kw),
        ]

    def _out(self, result):
        return next(f for f in result if f.name == "flow")

    def test_activation_on_classic_promotes_to_tempo(self):
        flows = self._flows()  # classic (no law)
        result = _apply_instance_overrides(
            flows, {("flow", "tempo_activation"): {"cls": "delay", "time": 3}}, comp_name="c"
        )
        out = self._out(result)
        assert out.occ_enable == {"cls": "delay", "time": 3}
        assert out.flow_type == "tempo"

    def test_deactivation_sets_disable_law(self):
        flows = self._flows()
        result = _apply_instance_overrides(
            flows, {("flow", "tempo_deactivation"): {"cls": "exp", "rate": 1e-3}}, comp_name="c"
        )
        out = self._out(result)
        assert out.occ_disable == {"cls": "exp", "rate": 1e-3}
        assert out.flow_type == "tempo"

    def test_none_sentinel_demotes_to_classic(self):
        # A KB-tempo flow (enable law set) overridden with the sentinel -> classic.
        flows = self._flows(flow_type="tempo", occ_enable={"cls": "delay", "time": 5})
        result = _apply_instance_overrides(
            flows, {("flow", "tempo_activation"): {"cls": "none"}}, comp_name="c"
        )
        out = self._out(result)
        assert out.occ_enable is None
        assert out.flow_type == "classic"

    def test_both_sides_overridden(self):
        flows = self._flows()
        result = _apply_instance_overrides(
            flows,
            {
                ("flow", "tempo_activation"): {"cls": "delay", "time": 3},
                ("flow", "tempo_deactivation"): {"cls": "delay", "time": 0},
            },
            comp_name="c",
        )
        out = self._out(result)
        assert out.occ_enable == {"cls": "delay", "time": 3}
        assert out.occ_disable == {"cls": "delay", "time": 0}
        assert out.flow_type == "tempo"

    def test_partial_demote_keeps_tempo_if_other_side_set(self):
        # enable + disable set; clear enable only -> still tempo (disable remains).
        flows = self._flows(
            flow_type="tempo",
            occ_enable={"cls": "delay", "time": 3},
            occ_disable={"cls": "delay", "time": 0},
        )
        result = _apply_instance_overrides(
            flows, {("flow", "tempo_activation"): {"cls": "none"}}, comp_name="c"
        )
        out = self._out(result)
        assert out.occ_enable is None
        assert out.occ_disable == {"cls": "delay", "time": 0}
        assert out.flow_type == "tempo"

    def test_rejects_tempo_on_input(self):
        flows = self._flows()
        with pytest.raises(Cod3sPlatformImportError, match="role=tempo_activation.*expects a output"):
            _apply_instance_overrides(
                flows, {("in_a", "tempo_activation"): {"cls": "delay", "time": 1}}, comp_name="c"
            )

    def test_rejects_malformed_value(self):
        flows = self._flows()
        with pytest.raises(Cod3sPlatformImportError, match="occurrence-law dict"):
            _apply_instance_overrides(
                flows, {("flow", "tempo_activation"): "delay"}, comp_name="c"
            )


# ---------------------------------------------------------------------------
# End-to-end parse_platform_export
# ---------------------------------------------------------------------------


def _payload(component_attributes, out_iface=None):
    out = {"name": "flow", "port_type": {"general": "output"}}
    out.update(out_iface or {})
    return {
        "model": {
            "name": "M",
            "kb": {"name": "KB", "version": "1.0.0"},
            "elements": {
                "components": {
                    "c1": {"name": "C1", "class_name": "Cls", "attributes": component_attributes},
                },
                "connections": {},
            },
        },
        "kb": {"component_templates": {"Cls": {"interfaces": {"flow__output": out}}}},
    }


class TestEndToEndTempoOverride:
    def test_classic_flow_promoted_by_override(self):
        ctx = parse_platform_export(_payload([
            {"name": "flow", "role": "tempo_activation", "value": {"cls": "delay", "time": 3}},
        ]))
        flow = next(f for f in ctx.components[0].flows if f.name == "flow")
        assert flow.flow_type == "tempo"
        assert flow.occ_enable == {"cls": "delay", "time": 3}

    def test_kb_tempo_demoted_by_sentinel(self):
        ctx = parse_platform_export(_payload(
            [{"name": "flow", "role": "tempo_activation", "value": {"cls": "none"}}],
            out_iface={"flow_type": "tempo", "occ_enable": {"cls": "delay", "time": 5}},
        ))
        flow = next(f for f in ctx.components[0].flows if f.name == "flow")
        assert flow.flow_type == "classic"
        assert flow.occ_enable is None

    def test_no_override_keeps_kb_default(self):
        ctx = parse_platform_export(_payload(
            [],
            out_iface={"flow_type": "tempo", "occ_enable": {"cls": "delay", "time": 5}},
        ))
        flow = next(f for f in ctx.components[0].flows if f.name == "flow")
        assert flow.flow_type == "tempo"
        assert flow.occ_enable == {"cls": "delay", "time": 5}


# ---------------------------------------------------------------------------
# Runtime build — the right muscadet flow class is instantiated
# ---------------------------------------------------------------------------


class TestRuntimeTempoOverride:
    def test_override_builds_flowouttempo(self, cleanup_system):
        from muscadet.importers.cod3s_platform import system_from_export

        system = system_from_export(_payload([
            {"name": "flow", "role": "tempo_activation", "value": {"cls": "delay", "time": 3}},
        ]))
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["flow"]
        assert type(flow).__name__ == "FlowOutTempo"
        assert flow.occ_enable_flow is not None  # the delay law took effect

    def test_no_override_classic_builds_flowout(self, cleanup_system):
        from muscadet.importers.cod3s_platform import system_from_export

        system = system_from_export(_payload([]))
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["flow"]
        assert type(flow).__name__ == "FlowOut"

    def test_sentinel_demote_builds_flowout(self, cleanup_system):
        from muscadet.importers.cod3s_platform import system_from_export

        system = system_from_export(_payload(
            [{"name": "flow", "role": "tempo_activation", "value": {"cls": "none"}}],
            out_iface={"flow_type": "tempo", "occ_enable": {"cls": "delay", "time": 5}},
        ))
        cleanup_system.append(system)
        # Demoted to classic -> plain FlowOut (no-law parity path).
        assert type(system.comp["C1"].flows_out["flow"]).__name__ == "FlowOut"
