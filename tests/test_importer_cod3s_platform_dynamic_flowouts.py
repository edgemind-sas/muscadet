"""Dynamic output flow types (2026-07-10) — COD3S Platform importer.

A KB output interface may carry a ``flow_type`` discriminator selecting the
muscadet flow-out class:

- ``classic`` (or absent on a legacy KB) -> ``FlowOut``
- ``tempo``   -> ``FlowOutTempo`` (+ occ_enable / occ_disable / init_enable)
- ``on_trigger`` -> ``FlowOutOnTrigger`` (+ trigger_time_up/down / trigger_logic)

Parse-layer tests exercise ``_parse_interface`` / ``parse_platform_export`` with
no runtime. Runtime tests build via ``system_from_export`` and assert the flow
class + params. Two edge cases are locked:

- short-form occurrence laws for BOTH tempo delays build without the historical
  double-capitalise ValueError (regression for the obj.py normalisation removal);
- an un-wired ``on_trigger`` flow builds and starts an interactive session
  without crashing (declarable-but-inert; trigger wiring is a separate concern
  and the platform gates ``on_trigger`` behind a superadmin preview feature).
"""

import pytest

from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    _parse_interface,
    parse_platform_export,
    system_from_export,
)


@pytest.fixture
def cleanup_system():
    """Tear down PyCATSHOO state after a runtime build (one System per name)."""
    import cod3s

    systems: list = []
    yield systems
    for system in systems:
        try:
            system.deleteSys()
        except Exception:
            pass
    cod3s.terminate_session()


def _payload(output_iface, sys_name):
    """Canonical payload: one component ``C1`` of class ``Cls`` + one output."""
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
        "kb": {
            "component_templates": {
                "Cls": {"interfaces": {"out__output": output_iface}},
            },
        },
    }


# ---------------------------------------------------------------------------
# Parse layer — _parse_interface
# ---------------------------------------------------------------------------


class TestParseFlowType:
    def test_absent_defaults_classic(self):
        fs = _parse_interface({"name": "o", "port_type": {"general": "output"}})
        assert fs.flow_type == "classic"
        assert fs.occ_enable is None
        assert fs.trigger_time_up is None

    def test_explicit_classic(self):
        fs = _parse_interface(
            {"name": "o", "port_type": {"general": "output"}, "flow_type": "classic"}
        )
        assert fs.flow_type == "classic"

    def test_tempo_params_parsed(self):
        fs = _parse_interface(
            {
                "name": "o",
                "port_type": {"general": "output"},
                "flow_type": "tempo",
                "occ_enable": {"cls": "delay", "time": 3},
                "occ_disable": {"cls": "exp", "rate": 0.1},
                "init_enable": True,
            }
        )
        assert fs.flow_type == "tempo"
        assert fs.occ_enable == {"cls": "delay", "time": 3}
        assert fs.occ_disable == {"cls": "exp", "rate": 0.1}
        assert fs.init_enable is True

    def test_on_trigger_params_parsed(self):
        fs = _parse_interface(
            {
                "name": "o",
                "port_type": {"general": "output"},
                "flow_type": "on_trigger",
                "trigger_time_up": 2.0,
                "trigger_time_down": 4.0,
                "trigger_logic": "and",
            }
        )
        assert fs.flow_type == "on_trigger"
        assert fs.trigger_time_up == 2.0
        assert fs.trigger_time_down == 4.0
        assert fs.trigger_logic == "and"

    def test_unknown_flow_type_rejected(self):
        with pytest.raises(Cod3sPlatformImportError, match="unsupported flow_type"):
            _parse_interface(
                {
                    "name": "o",
                    "port_type": {"general": "output"},
                    "flow_type": "warp_drive",
                }
            )

    def test_input_never_carries_flow_type(self):
        # flow_type is an output-only concept; an input keeps the dataclass
        # default and never triggers dispatch.
        fs = _parse_interface({"name": "i", "port_type": {"general": "input"}})
        assert fs.direction == "input"
        assert fs.flow_type == "classic"

    def test_end_to_end_tempo_through_parse(self):
        ctx = parse_platform_export(
            _payload(
                {
                    "name": "out",
                    "port_type": {"general": "output"},
                    "flow_type": "tempo",
                    "occ_enable": {"cls": "delay", "time": 5},
                },
                sys_name="Mparse_tempo",
            )
        )
        out = next(f for f in ctx.components[0].flows if f.name == "out")
        assert out.flow_type == "tempo"
        assert out.occ_enable == {"cls": "delay", "time": 5}


# ---------------------------------------------------------------------------
# Runtime layer — system_from_export dispatch
# ---------------------------------------------------------------------------


class TestRuntimeDispatch:
    def test_classic_builds_flowout(self, cleanup_system):
        from muscadet.flow import FlowOut, FlowOutOnTrigger, FlowOutTempo

        system = system_from_export(
            _payload(
                {"name": "out", "port_type": {"general": "output"}},
                sys_name="Mrt_classic",
            )
        )
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["out"]
        assert isinstance(flow, FlowOut)
        assert not isinstance(flow, (FlowOutTempo, FlowOutOnTrigger))

    def test_tempo_builds_flowouttempo(self, cleanup_system):
        from muscadet.flow import FlowOutTempo

        system = system_from_export(
            _payload(
                {
                    "name": "out",
                    "port_type": {"general": "output"},
                    "flow_type": "tempo",
                    "occ_enable": {"cls": "delay", "time": 3},
                    "occ_disable": {"cls": "delay", "time": 2},
                    "init_enable": True,
                },
                sys_name="Mrt_tempo",
            )
        )
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["out"]
        assert isinstance(flow, FlowOutTempo)
        assert flow.init_enable is True

    def test_on_trigger_builds_flowoutontrigger(self, cleanup_system):
        from muscadet.flow import FlowOutOnTrigger

        system = system_from_export(
            _payload(
                {
                    "name": "out",
                    "port_type": {"general": "output"},
                    "flow_type": "on_trigger",
                    "trigger_time_up": 2,
                    "trigger_time_down": 3,
                    "trigger_logic": "and",
                },
                sys_name="Mrt_trigger",
            )
        )
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["out"]
        assert isinstance(flow, FlowOutOnTrigger)
        assert flow.trigger_time_up == 2
        assert flow.trigger_logic == "and"


# ---------------------------------------------------------------------------
# Short-form occurrence-law regression (obj.py normalisation removal)
# ---------------------------------------------------------------------------


class TestShortFormOccLawRegression:
    def test_tempo_short_form_enable_and_disable_build(self, cleanup_system):
        # BOTH occ_enable and occ_disable in short form must build. obj.py
        # normalises short-form laws ("delay" -> "DelayOccDistribution") so
        # ObjCOD3S.from_dict can resolve them; a previous version normalised
        # only occ_enable_flow, so a short-form occ_disable_flow crashed
        # from_dict with "delay is not a subclass of ObjCOD3S". Reaching the
        # assert means both laws resolved and both automaton transitions built.
        system = system_from_export(
            _payload(
                {
                    "name": "out",
                    "port_type": {"general": "output"},
                    "flow_type": "tempo",
                    "occ_enable": {"cls": "delay", "time": 1},
                    "occ_disable": {"cls": "delay", "time": 1},
                },
                sys_name="Mrt_shortform",
            )
        )
        cleanup_system.append(system)
        assert system.comp["C1"].flows_out["out"] is not None


# ---------------------------------------------------------------------------
# Degenerate: on_trigger with no trigger wired (trigger wiring deferred)
# ---------------------------------------------------------------------------


class TestOnTriggerDegenerate:
    def test_unwired_on_trigger_builds_and_isimu(self, cleanup_system):
        # The importer does not emit connect_trigger yet, so every on_trigger
        # flow it builds is un-wired. It MUST still build and start/stop an
        # interactive session without crashing. Behaviour is left to the
        # muscadet automaton default and not asserted (only meaningful once the
        # trigger is wired — which is why the platform gates on_trigger behind a
        # superadmin preview feature).
        system = system_from_export(
            _payload(
                {
                    "name": "out",
                    "port_type": {"general": "output"},
                    "flow_type": "on_trigger",
                    "trigger_time_up": 0,
                    "trigger_time_down": 0,
                },
                sys_name="Mrt_unwired",
            )
        )
        cleanup_system.append(system)
        system.isimu_start()
        val = system.comp["C1"].flows_out["out"].var_fed.value()
        assert isinstance(val, bool)
        system.isimu_stop()
