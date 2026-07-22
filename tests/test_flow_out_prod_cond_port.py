"""Per-operand input/output disambiguation in prod_cond (``port`` hint).

When a component carries a flow name on BOTH an input and an output (the RBD
passthrough ``flow``), a bare ``prod_cond`` operand resolves the INPUT
(historical input-first). A ``{"name": "flow", "port": "out"}`` operand forces
the OUTPUT — so a ``ctrl`` output that must mirror the flow OUTPUT follows its
dynamics (here a tempo delay), not the raw input.

Cf. the prod_cond port-disambiguation chantier (2026-07).
"""

import muscadet

import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():
    class Source(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowOut", name="flow", var_prod_default=True))

    class Tap(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="flow", logic="and"))
            # The main flow output is TEMPO delay(3): it enables 3 steps after
            # its input is fed, so flow_out != flow_in in between.
            self.add_flow(
                dict(
                    cls="FlowOutTempo",
                    name="flow",
                    var_prod_cond=["flow"],
                    occ_enable_flow={"cls": "delay", "time": 3},
                    occ_disable_flow={"cls": "delay", "time": 0},
                )
            )
            # ctrl_out mirrors the OUTPUT flow (delayed) via port='out'.
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="ctrl_out",
                    var_prod_cond=[[{"name": "flow", "port": "out"}]],
                )
            )
            # ctrl_in follows the INPUT flow (immediate) via port='in'.
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="ctrl_in",
                    var_prod_cond=[[{"name": "flow", "port": "in"}]],
                )
            )
            # ctrl_default: bare name -> historical input-first -> the INPUT.
            self.add_flow(
                dict(cls="FlowOut", name="ctrl_default", var_prod_cond=["flow"])
            )

    system = muscadet.System(name="SysPort")
    system.add_component(name="S", cls="Source")
    system.add_component(name="TAP", cls="Tap")
    system.connect_flow("S", "TAP", "flow")
    return system


def _fed(system, comp, flow):
    return system.comp[comp].flows_out[flow].var_fed.value()


def test_resolution_targets(the_system):
    tap = the_system.comp["TAP"]
    flow_in = tap.flows_in["flow"]
    flow_out = tap.flows_out["flow"]
    # port='out' resolved the OUTPUT flow; port='in' and the bare name the INPUT.
    assert tap.flows_out["ctrl_out"].var_prod_cond[0][0] is flow_out
    assert tap.flows_out["ctrl_in"].var_prod_cond[0][0] is flow_in
    assert tap.flows_out["ctrl_default"].var_prod_cond[0][0] is flow_in


def test_port_out_follows_delayed_output(the_system):
    the_system.isimu_start()
    assert _fed(the_system, "TAP", "flow") is False  # tempo disabled at t=0
    # ctrl_out mirrors the (still-disabled) OUTPUT.
    assert _fed(the_system, "TAP", "ctrl_out") is False
    # ctrl_in / ctrl_default follow the INPUT (fed immediately).
    assert _fed(the_system, "TAP", "ctrl_in") is True
    assert _fed(the_system, "TAP", "ctrl_default") is True

    # Fire the tempo enable (delay 3) -> the output produces at t=3.
    trans = the_system.isimu_fireable_transitions()
    idx = next(i for i, t in enumerate(trans) if t is not None and t.comp_name == "TAP")
    the_system.isimu_set_transition(idx)
    the_system.isimu_step_forward()
    assert the_system.currentTime() == 3
    assert _fed(the_system, "TAP", "flow") is True
    # Now ctrl_out follows the enabled OUTPUT.
    assert _fed(the_system, "TAP", "ctrl_out") is True
    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
