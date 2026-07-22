"""FlowOutTempo behaviour: any-law temporisation + no-law == FlowOut.

A ``FlowOutTempo`` enables/disables its production through a two-state automaton
whose enable/disable transitions carry occurrence laws (delay / exp / inst).
Three properties are locked here with the deterministic interactive simulation:

- **no law** (both ``occ_*_flow`` None) -> NO automaton -> behaves EXACTLY like a
  plain ``FlowOut`` (produces as soon as ``prod_cond`` holds) ;
- **delay(T)** enable -> the output stays unfed until t = T after ``prod_cond``
  holds, then produces ;
- **inst** enable -> the output produces at the SAME time step (instantaneous,
  p = 1) as soon as ``prod_cond`` holds.

Cf. the tempo finalisation chantier (2026-07).
"""

import muscadet

import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():
    class Source(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowOut", name="is_ok", var_prod_default=True))

    class BlockNoLaw(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="is_ok", logic="and"))
            # No occ law -> must behave as a plain FlowOut.
            self.add_flow(
                dict(cls="FlowOutTempo", name="flow", var_prod_cond=["is_ok"])
            )

    class BlockDelay(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="is_ok", logic="and"))
            self.add_flow(
                dict(
                    cls="FlowOutTempo",
                    name="flow",
                    var_prod_cond=["is_ok"],
                    occ_enable_flow={"cls": "delay", "time": 3},
                    occ_disable_flow={"cls": "delay", "time": 0},
                )
            )

    class BlockInst(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="is_ok", logic="and"))
            self.add_flow(
                dict(
                    cls="FlowOutTempo",
                    name="flow",
                    var_prod_cond=["is_ok"],
                    occ_enable_flow={"cls": "inst"},
                    occ_disable_flow={"cls": "inst"},
                )
            )

    class Reference(muscadet.ObjFlow):
        # Plain FlowOut, the parity reference for BlockNoLaw.
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="is_ok", logic="and"))
            self.add_flow(dict(cls="FlowOut", name="flow", var_prod_cond=["is_ok"]))

    system = muscadet.System(name="SysTempo")
    system.add_component(name="S", cls="Source")
    system.add_component(name="NOLAW", cls="BlockNoLaw")
    system.add_component(name="DELAY", cls="BlockDelay")
    system.add_component(name="INST", cls="BlockInst")
    system.add_component(name="REF", cls="Reference")
    for c in ("NOLAW", "DELAY", "INST", "REF"):
        system.connect_flow("S", c, "is_ok")
    return system


def _out(system, comp):
    return system.comp[comp].flows_out["flow"].var_fed.value()


def _fire(system, comp_name):
    """Fire the (single) fireable transition of ``comp_name`` and step."""
    trans = system.isimu_fireable_transitions()
    idx = next(
        i for i, t in enumerate(trans) if t is not None and t.comp_name == comp_name
    )
    system.isimu_set_transition(idx)
    system.isimu_step_forward()


def test_no_automaton_when_no_law(the_system):
    # A no-law FlowOutTempo builds NO enable/disable automaton.
    nolaw = the_system.comp["NOLAW"].flows_out["flow"]
    assert nolaw.state_enable_bkd is None
    # A configured tempo DOES build the automaton.
    assert the_system.comp["DELAY"].flows_out["flow"].state_enable_bkd is not None
    assert the_system.comp["INST"].flows_out["flow"].state_enable_bkd is not None


def test_initial_step(the_system):
    the_system.isimu_start()
    assert the_system.comp["S"].flows_out["is_ok"].var_fed.value() is True

    # no-law tempo == plain FlowOut: both produce immediately (prod_cond holds).
    assert _out(the_system, "NOLAW") is True
    assert _out(the_system, "REF") is True
    assert _out(the_system, "NOLAW") == _out(the_system, "REF")

    # The two configured tempos start in 'disabled' (init_enable False): their
    # enable transitions are fireable but not yet fired, so both are unfed at t=0.
    assert the_system.currentTime() == 0
    assert _out(the_system, "INST") is False
    assert _out(the_system, "DELAY") is False


def test_inst_enables_at_same_time_step(the_system):
    # Firing the inst enable does NOT advance the clock (p=1 instantaneous jump).
    _fire(the_system, "INST")
    assert the_system.currentTime() == 0
    assert _out(the_system, "INST") is True
    # DELAY is still waiting for its delay(3).
    assert _out(the_system, "DELAY") is False


def test_delay_enables_after_its_delay(the_system):
    # Firing DELAY's enable advances the clock to t=3 (deterministic delay).
    _fire(the_system, "DELAY")
    assert the_system.currentTime() == 3
    assert _out(the_system, "DELAY") is True
    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
