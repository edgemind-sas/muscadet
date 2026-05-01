"""Mini datacenter — composite var_prod_cond demonstrator.

A server depends on (power) AND (cooling), where:
- power is at-least-one of two redundant sources (P1, P2);
- cooling is a single mandatory source (C1).

Achieved with ``var_prod_cond=[["power_p1", "power_p2"], ["cooling"]]``
in default inner-mode "or": the outer list is AND, the inner list is OR
(see ``muscadet/flow.py``: ``var_prod_cond_inner_mode``). This translates
to the boolean expression::

    (power_p1 OR power_p2) AND cooling

So the server feeds as long as at least one power source is fed AND
cooling is fed.

Timeline (driven by deterministic delay failures)::

  t=0:   P1 ok, P2 ok, C ok       Server ON
  t=5:   P1 fails                  Server ON   (P2 alive, cooling alive)
  t=8:   C   fails                  Server OFF  (cooling missing)
  t=12:  C   repairs                Server ON   (P2 still alive)
  t=15:  P2 fails                   Server OFF  (no power)

Run::

    cod3s-isimu --factory examples.isimu.datacenter_lite:build
    # or:
    python -m examples.isimu.datacenter_lite
"""

from __future__ import annotations

import muscadet


class PowerSource(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="power", var_prod_default=True)


class CoolingSource(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="cooling", var_prod_default=True)


class Server(muscadet.ObjFlow):
    """Receives power (logic="or") and cooling (logic="and"), produces a
    "running" output flow conditional on both."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="power", logic="or")
        self.add_flow_in(name="cooling", logic="and")
        self.add_flow_out(
            name="running",
            var_prod_cond=[["power"], ["cooling"]],  # AND of two singletons
        )


def build() -> muscadet.System:
    system = muscadet.System(name="datacenter_lite")
    system.add_component(name="P1", cls="PowerSource")
    system.add_component(name="P2", cls="PowerSource")
    system.add_component(name="C", cls="CoolingSource")
    system.add_component(name="Srv", cls="Server")

    # Power lines: both P1 and P2 feed Srv.power (logic="or" -> any feeds)
    system.connect_flow("P1", "Srv", "power")
    system.connect_flow("P2", "Srv", "power")
    # Cooling line: C feeds Srv.cooling
    system.connect_flow("C", "Srv", "cooling")

    # Failure modes:
    # - P1 fails at t=5 (no repair within window)
    # - C  fails at t=8, repairs at t=12
    # - P2 fails at t=15
    system.comp["P1"].add_delay_failure_mode(
        name="fail_P1",
        failure_cond="power_fed_out",
        failure_time=5,
        failure_effects=[("power_fed_available_out", False)],
        repair_time=1000,
    )
    system.comp["C"].add_delay_failure_mode(
        name="fail_C",
        failure_cond="cooling_fed_out",
        failure_time=8,
        failure_effects=[("cooling_fed_available_out", False)],
        repair_time=4,
    )
    system.comp["P2"].add_delay_failure_mode(
        name="fail_P2",
        failure_cond="power_fed_out",
        failure_time=15,
        failure_effects=[("power_fed_available_out", False)],
        repair_time=1000,
    )
    return system


def _snapshot(system: muscadet.System) -> str:
    p1 = system.comp["P1"].flows_out["power"].var_fed.value()
    p2 = system.comp["P2"].flows_out["power"].var_fed.value()
    c = system.comp["C"].flows_out["cooling"].var_fed.value()
    srv_in_power = system.comp["Srv"].flows_in["power"].var_fed.value()
    srv_in_cool = system.comp["Srv"].flows_in["cooling"].var_fed.value()
    srv_run = system.comp["Srv"].flows_out["running"].var_fed.value()
    return (
        f"t={system.currentTime():g} | "
        f"P1={'1' if p1 else '0'} P2={'1' if p2 else '0'} C={'1' if c else '0'} "
        f"|| Srv.power_in={'1' if srv_in_power else '0'} "
        f"Srv.cooling_in={'1' if srv_in_cool else '0'} "
        f"Srv.running={'1' if srv_run else '0'}"
    )


def _fire_next(system: muscadet.System, label: str) -> None:
    """Fire the earliest pending transition."""
    transitions = system.isimu_fireable_transitions()
    fireable = [(i, t) for i, t in enumerate(transitions) if t]
    fireable.sort(key=lambda it: it[1].end_time)
    if not fireable:
        return
    idx, tr = fireable[0]
    system.isimu_set_transition(idx, date=tr.end_time)
    system.isimu_step_forward()
    print(f"{label:30s} {_snapshot(system)}")


def run() -> None:
    import cod3s

    system = build()
    try:
        system.isimu_start()
        print(f"{'INITIAL':30s} {_snapshot(system)}")

        _fire_next(system, "P1 fails (t=5)")
        _fire_next(system, "C fails (t=8)")
        _fire_next(system, "C repairs (t=12)")
        _fire_next(system, "P2 fails (t=15)")

        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


if __name__ == "__main__":
    run()
