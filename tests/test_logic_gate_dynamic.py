"""ObjLogicGate dynamic recompute: when a source variable changes mid-simulation
(here via an exponential failure mode that flips the source flow OFF), the gate's
sensitive method must recompute and propagate the new result downstream.

This proves the automaton-free gate reacts to runtime changes, not only at init.
"""

import muscadet
import cod3s
import pytest


class SD(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="fd", var_prod_default=True)  # ON initially


class Sink(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="g", logic="or")


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="Sys")
    system.add_component(name="SD", cls="SD")
    system.add_component(name="SinkD", cls="Sink")

    system.add_component(
        name="GDYN",
        cls="ObjLogicGate",
        kind="or",
        cond=[[{"obj": "SD", "attr": "fd_fed_out", "value": True}]],
        out_elements=["g"],
    )
    system.connect("GDYN", "g_out", "SinkD", "g_in")

    # Exponential failure that turns the source flow OFF when it fires.
    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="fail_sd",
        targets=["SD"],
        failure_effects={"fd": False},
        failure_param=1 / 10,
        repair_param=0.1,
    )
    return system


def test_gate_follows_source_failure(the_system):
    the_system.isimu_start()
    try:
        # Initially the source is fed -> OR gate True -> sink fed.
        assert the_system.comp["SinkD"].flows_in["g"].var_fed.value() is True

        transitions = the_system.isimu_fireable_transitions()
        assert len(transitions) >= 1

        # Fire the failure at t=10: source flow goes OFF.
        the_system.isimu_set_transition(0, date=10)
        the_system.isimu_step_forward()
        assert the_system.currentTime() == 10

        # The gate must have recomputed and propagated False downstream.
        assert the_system.comp["SinkD"].flows_in["g"].var_fed.value() is False
    finally:
        the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
