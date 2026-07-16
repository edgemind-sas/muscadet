"""Availability-gate reset — decision for the dynamic output flows.

DECISION (allow): ``FlowOutTempo`` and ``FlowOutOnTrigger`` inherit
``add_variables`` from ``FlowOut``; their ``var_fed`` still ANDs
``var_fed_available`` (see their overridden ``create_sensitive_set_flow_fed_out``).
The reset field is therefore a property of the same availability gate in all
three classes and composes naturally — no special-casing, no rejection. This
test locks that a persistent (reset=False) dynamic flow constructs and runs
without breaking.

The byte-identical default (reset=True) for both dynamic flows is already
covered by the existing ``test_flow_out_tempo.py`` and
``test_flow_out_trigger_001.py`` (which stay green unchanged).
"""

import muscadet
import cod3s
import pytest


class TempoPersistentComp(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(
            dict(
                cls="FlowOutTempo",
                name="T",
                var_prod_default=True,
                occ_enable_flow={"cls": "delay", "time": 0},
                occ_disable_flow={"cls": "delay", "time": 0},
                var_fed_available_out_reset=False,
            )
        )


class TriggerSrcComp(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="sig", var_prod_default=True)


class TriggerPersistentComp(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(
            dict(
                cls="FlowOutOnTrigger",
                name="sig",
                var_prod_default=True,
                trigger_time_up=0,
                trigger_time_down=0,
                trigger_logic="and",
                var_fed_available_out_reset=False,
            )
        )


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="DynamicResetSys")
    system.add_component(name="TC", cls="TempoPersistentComp")
    system.add_component(name="S", cls="TriggerSrcComp")
    system.add_component(name="TRC", cls="TriggerPersistentComp")
    system.connect_trigger("S", "TRC", "sig")
    return system


def test_dynamic_flows_carry_reset_and_run(the_system):
    tempo = the_system.comp["TC"].flows_out["T"]
    trig = the_system.comp["TRC"].flows_out["sig"]

    assert tempo.var_fed_available_out_reset is False
    assert trig.var_fed_available_out_reset is False

    # constructs and starts without raising, gates are readable booleans
    the_system.isimu_start()
    assert isinstance(tempo.var_fed_available.value(), bool)
    assert isinstance(trig.var_fed_available.value(), bool)
    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
