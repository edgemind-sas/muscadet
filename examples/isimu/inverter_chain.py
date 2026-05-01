"""FlowOut(negate=True) demonstrator.

Two-stage inverter chain: Source -> INV1 -> INV2 -> Target. Each inverter
re-emits the negation of its input flow. As a result the target sees the
*same* value as the source (double inversion), but watching INV1 between
events shows the flip.

This exercises the ``negate=True`` branch of
:meth:`muscadet.FlowOut.create_sensitive_set_flow_fed_out`.

Run::

    cod3s-isimu --factory examples.isimu.inverter_chain:build
    # or:
    python -m examples.isimu.inverter_chain
"""

from __future__ import annotations

import muscadet


class Source(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="signal", var_prod_default=True)


class Inverter(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="signal", logic="and")
        self.add_flow_out(
            name="signal",
            var_prod_cond=["signal"],
            negate=True,
        )


class Target(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="signal", logic="and")


def build() -> muscadet.System:
    system = muscadet.System(name="inverter_chain")
    system.add_component(name="S", cls="Source")
    system.add_component(name="INV1", cls="Inverter")
    system.add_component(name="INV2", cls="Inverter")
    system.add_component(name="T", cls="Target")

    system.auto_connect("S", "INV1")
    system.auto_connect("INV1", "INV2")
    system.auto_connect("INV2", "T")

    # Drop the source at t=4, restore at t=8 — easy back-and-forth.
    system.comp["S"].add_delay_failure_mode(
        name="fail_S",
        failure_cond="signal_fed_out",
        failure_time=4,
        failure_effects=[("signal_fed_available_out", False)],
        repair_time=4,
    )
    return system


def _snapshot(system: muscadet.System) -> str:
    s = system.comp["S"].flows_out["signal"].var_fed.value()
    i1 = system.comp["INV1"].flows_out["signal"].var_fed.value()
    i2 = system.comp["INV2"].flows_out["signal"].var_fed.value()
    t = system.comp["T"].flows_in["signal"].var_fed.value()
    return (
        f"t={system.currentTime():g} | "
        f"S={'1' if s else '0'} -> "
        f"INV1={'1' if i1 else '0'} -> "
        f"INV2={'1' if i2 else '0'} -> "
        f"T={'1' if t else '0'}"
    )


def run() -> None:
    import cod3s

    system = build()
    try:
        system.isimu_start()
        print(f"INITIAL          {_snapshot(system)}")

        # S fail at t=4
        system.isimu_set_transition(0, date=4)
        system.isimu_step_forward()
        print(f"AFTER S fail     {_snapshot(system)}")

        # S repair at t=8
        system.isimu_set_transition(0, date=8)
        system.isimu_step_forward()
        print(f"AFTER S repair   {_snapshot(system)}")

        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


if __name__ == "__main__":
    run()
