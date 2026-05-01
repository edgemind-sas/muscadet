"""k/n redundancy demonstrator.

System: 3 sources S1, S2, S3 feeding a target T configured with at-least-2
logic on its single input ``f1``. Each source carries a deterministic
delay failure mode firing at distinct dates so the user can step through
and observe the threshold being crossed.

Flow propagation timeline (when stepped in order)::

  t=0:   S1=ok, S2=ok, S3=ok          T.f1.var_fed = True   (3/3 >= 2)
  t=5:   S1 fails                      T.f1.var_fed = True   (2/3 >= 2)
  t=10:  S2 fails                      T.f1.var_fed = False  (1/3 <  2)
  t=15:  S2 repairs                    T.f1.var_fed = True   (2/3 >= 2)

Run interactively with the TUI (after ``pip install ".../cod3s[isimu]"``)::

    cod3s-isimu --factory examples.isimu.rbd_kn:build
"""

from __future__ import annotations

import muscadet


class Source(muscadet.ObjFlow):
    """Always-producing source on flow ``f1``."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="f1", var_prod_default=True)


class TargetK2(muscadet.ObjFlow):
    """Target accepting flow ``f1`` with at-least-2 logic on its input."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="f1", logic=2)


def build() -> muscadet.System:
    """Return a populated muscadet System ready for interactive simulation."""
    system = muscadet.System(name="rbd_kn_demo")
    system.add_component(name="S1", cls="Source")
    system.add_component(name="S2", cls="Source")
    system.add_component(name="S3", cls="Source")
    system.add_component(name="T", cls="TargetK2")

    system.auto_connect("S.*", "T")

    # S1 fails at t=5, S2 fails at t=10 (S2 also has a finite repair time
    # so we can demonstrate crossing the threshold both ways).
    system.comp["S1"].add_delay_failure_mode(
        name="fail_S1",
        failure_cond="f1_fed_out",
        failure_time=5,
        failure_effects=[("f1_fed_available_out", False)],
        repair_time=100,
    )
    system.comp["S2"].add_delay_failure_mode(
        name="fail_S2",
        failure_cond="f1_fed_out",
        failure_time=10,
        failure_effects=[("f1_fed_available_out", False)],
        repair_time=5,
    )
    return system


def _snapshot(system: muscadet.System) -> str:
    parts = [f"t={system.currentTime():g}"]
    for name in ("S1", "S2", "S3"):
        v = system.comp[name].flows_out["f1"].var_fed.value()
        parts.append(f"{name}.f1={'1' if v else '0'}")
    t = system.comp["T"].flows_in["f1"]
    parts.append(f"T.f1.sum={int(t.var_in.sumValue(0))}")
    parts.append(f"T.f1={'1' if t.var_fed.value() else '0'}")
    return " | ".join(parts)


def run() -> None:
    """Step through the scripted timeline from Python (no TUI)."""
    import cod3s

    system = build()
    try:
        system.isimu_start()
        print(f"INITIAL          {_snapshot(system)}")

        # Fire S1 failure at t=5
        system.isimu_set_transition(0, date=5)
        system.isimu_step_forward()
        print(f"AFTER S1 fail    {_snapshot(system)}")

        # Fire S2 failure at t=10 (transition 0 is now S1's repair, 1 is S2 fail)
        system.isimu_set_transition(1, date=10)
        system.isimu_step_forward()
        print(f"AFTER S2 fail    {_snapshot(system)}")

        # Fire S2 repair at t=15
        system.isimu_set_transition(0, date=15)
        system.isimu_step_forward()
        print(f"AFTER S2 repair  {_snapshot(system)}")

        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


if __name__ == "__main__":
    run()
