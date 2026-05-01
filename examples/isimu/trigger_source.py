"""Trigger-source demonstrator (warm standby).

System layout::

    S1 (primary)  ---is_ok--->  T  <---is_ok---  S2 (standby, FlowOutOnTrigger)
                                ^
                                |
    S1 ---is_ok_trigger--->  S2  (S2 starts producing only when S1 stops)

S2 uses :class:`muscadet.FlowOutOnTrigger` with ``trigger_logic="and"``: it
produces ``is_ok`` only while its trigger input is False (i.e. while S1 is
not feeding). Both ``trigger_time_up`` and ``trigger_time_down`` are 0,
giving an instantaneous switch-over.

Timeline driven by S1's delay failure mode (5 time units up, 5 down)::

  t=0:   S1 ok, S2 idle, T fed (via S1)
  t=5:   S1 fails -> S2 trigger up (instant) -> T fed (via S2)
  t=10:  S1 repairs -> S2 trigger down (instant) -> T fed (via S1 only)

Run::

    cod3s-isimu --factory examples.isimu.trigger_source:build
    # or:
    python -m examples.isimu.trigger_source
"""

from __future__ import annotations

import muscadet

FLOW = "is_ok"


class Source(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name=FLOW, var_prod_default=True)


class SourceTrigger(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out_on_trigger(
            name=FLOW,
            trigger_time_up=0,
            trigger_time_down=0,
            trigger_logic="and",
            var_prod_default=True,
        )


class Target(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name=FLOW, logic="or")


def build() -> muscadet.System:
    system = muscadet.System(name="trigger_source_demo")
    system.add_component(name="S1", cls="Source")
    system.add_component(name="S2", cls="SourceTrigger")
    system.add_component(name="T", cls="Target")

    # S2.trigger_in <- S1.is_ok_out
    system.connect_trigger("S1", "S2", FLOW)
    # T <- S1 and S2 (parallel)
    system.auto_connect("S1", "T")
    system.connect_flow("S2", "T", FLOW)

    # S1 fails at t=5, repairs at t=10
    system.comp["S1"].add_delay_failure_mode(
        name="fail_S1",
        failure_cond=f"{FLOW}_fed_out",
        failure_time=5,
        failure_effects=[(f"{FLOW}_fed_available_out", False)],
        repair_time=5,
    )
    return system


def _snapshot(system: muscadet.System) -> str:
    s1 = system.comp["S1"].flows_out[FLOW].var_fed.value()
    s2 = system.comp["S2"].flows_out[FLOW].var_fed.value()
    t = system.comp["T"].flows_in[FLOW].var_fed.value()
    return (
        f"t={system.currentTime():g} | "
        f"S1={'1' if s1 else '0'} S2={'1' if s2 else '0'} T={'1' if t else '0'}"
    )


def run() -> None:
    import cod3s

    system = build()
    try:
        system.isimu_start()
        print(f"INITIAL              {_snapshot(system)}")

        # S1 fail at t=5: drives S1.is_ok_fed_out to False, then trigger_up
        # fires instantaneously activating S2.
        system.isimu_set_transition(0, date=5)
        system.isimu_step_forward()
        print(f"AFTER S1 fail        {_snapshot(system)}")

        # The trigger_up of S2 is fireable next (instantaneous).
        system.isimu_set_transition(1)
        system.isimu_step_forward()
        print(f"AFTER S2 trigger up  {_snapshot(system)}")

        # S1 repair: emits the trigger_down on S2 next.
        system.isimu_set_transition(0, date=10)
        system.isimu_step_forward()
        print(f"AFTER S1 repair      {_snapshot(system)}")

        # Trigger down fires instantly, S2 goes idle again.
        system.isimu_set_transition(1)
        system.isimu_step_forward()
        print(f"AFTER S2 trigger dn  {_snapshot(system)}")

        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


if __name__ == "__main__":
    run()
