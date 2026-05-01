"""Cyber compromise mode (MdC) cascade — 3-component POC.

Reproduces the example from the IMdR P23-4 atelier slides (49-56) using
only existing muscadet + cod3s.ObjFM* primitives — no new abstraction.

Architecture::

    Alim ── power ──> Serveur ── f_orange, f_blue ──> Process
                          └──── f_service (dormant)─> Process

Three cascading compromise modes on the system:

- ``MdC_A`` on Serveur — privilege escalation (delay=10). No flow effect,
  state-only. Gates ``MdC_B``.
- ``MdC_B`` on Serveur — service-function exploit (delay=5 after MdC_A
  active). Effect: latches the dormant ``f_service`` flow active so it
  starts feeding the process. ``failure_cond`` references ``MdC_A``'s
  automaton state via cod3s' structured-condition syntax (``obj`` / ``attr``).
- ``MdC_proc`` on Process — function inhibition (delay=8 after f_service
  reaches the process). Effect: kills both functional outputs F1 and F2.
  ``failure_cond`` watches the ``f_service_fed_in`` variable.

Two cascade mechanisms intentionally illustrated together:

1. **Intra-component, by automaton state**: MdC_B → MdC_A.occ.isActive()
   (cod3s structured-cond pattern with state attr).
2. **Inter-component, by flow propagation**: MdC_proc → f_service flux
   on ProcessIndustriel (the attack travels through the regular muscadet
   message-box wiring once MdC_B turns on the service flow).

Repair is disabled (``repair_cond=lambda: False`` plus large
``repair_param``): once compromised, the MdC stays active for the
simulation horizon — matches the slides' one-way scenario.

Expected stepping timeline::

    t=0    | MdC[A=0 B=0 P=0] | Srv.svc_out=0 Proc.svc_in=0 | Proc[F1=1 F2=1]
    t=10   | MdC[A=1 B=0 P=0] | Srv.svc_out=0 Proc.svc_in=0 | Proc[F1=1 F2=1]
    t=15   | MdC[A=1 B=1 P=0] | Srv.svc_out=1 Proc.svc_in=1 | Proc[F1=1 F2=1]
    t=23   | MdC[A=1 B=1 P=1] | Srv.svc_out=1 Proc.svc_in=1 | Proc[F1=0 F2=0]

Run::

    cod3s-isimu --factory examples.isimu.cyber_3comp:build
    # or, no TUI dep:
    python -m examples.isimu.cyber_3comp
"""

from __future__ import annotations

import muscadet

# ----------------------------------------------------------------------------
# Component classes
# ----------------------------------------------------------------------------


class AlimElec(muscadet.ObjFlow):
    """Power source — always on."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="power", var_prod_default=True)


class Serveur(muscadet.ObjFlow):
    """Server with two normal mission outputs and one dormant service output.

    The ``f_service`` flow models the "exploitable but unused under nominal
    operation" function from slide 51. Two key parameters keep it dormant:

    - ``var_prod_default=False`` — initialises ``var_prod_available`` to
      ``False`` (the variable is created with this initial value in
      :meth:`muscadet.FlowOut.add_variables`).
    - ``var_prod_cond=[]`` (default) — no sensitive method binds
      ``var_prod_available`` to upstream flows, so the value stays at its
      initial ``False`` until something else writes it.

    ``MdC_B`` is that "something else": its ``failure_effects`` set
    ``f_service_prod_available=True``, which then propagates through the
    fed-out sensitive method (registered on var_prod_available) to flip
    ``var_fed`` to True.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="power", logic="and")
        self.add_flow_out(name="f_orange", var_prod_cond=[["power"]])
        self.add_flow_out(name="f_blue", var_prod_cond=[["power"]])
        self.add_flow_out(name="f_service", var_prod_default=False)


class ProcessIndustriel(muscadet.ObjFlow):
    """Process consuming the server's two functional outputs.

    Also has a ``f_service`` input — normally not fed under nominal
    operation. The MdC on this component watches this variable: once the
    upstream service flow is activated by MdC_B, this MdC becomes
    fireable and inhibits the two functional outputs F1 and F2.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="f_orange", logic="and")
        self.add_flow_in(name="f_blue", logic="and")
        self.add_flow_in(name="f_service", logic="or")
        self.add_flow_out(name="F1", var_prod_cond=[["f_orange"]])
        self.add_flow_out(name="F2", var_prod_cond=[["f_blue"]])


# ----------------------------------------------------------------------------
# System builder
# ----------------------------------------------------------------------------


def build() -> muscadet.System:
    """Build the populated system. Compatible with ``cod3s-isimu --factory``."""
    system = muscadet.System(name="cyber_3comp")
    system.add_component(name="Alim", cls="AlimElec")
    system.add_component(name="Srv", cls="Serveur")
    system.add_component(name="Proc", cls="ProcessIndustriel")

    # Wire the flows. auto_connect matches by flow name on both sides:
    # - Alim.power_out -> Srv.power_in
    # - Srv.f_orange/f_blue/f_service -> Proc.f_orange/f_blue/f_service
    system.auto_connect("Alim", "Srv")
    system.auto_connect("Srv", "Proc")

    # MdC_A — privilege escalation. State-only, no flow effect.
    # MdC_B's failure_cond will reference Srv__mdc_a's "occ" state.
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_a",
        targets=["Srv"],
        failure_param=10,
        failure_effects={},
        repair_cond=lambda: False,
        repair_param=1e9,
    )

    # MdC_B — service-function exploit, gated by MdC_A being active.
    # cod3s' prepare_attr_tree resolves obj="Srv__mdc_a" via system.comp,
    # then matches attr="occ" against the FM component's states (PyCATSHOO
    # IState), which uses .isActive() to compare against value=True.
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_b",
        targets=["Srv"],
        failure_param=5,
        failure_cond=[[{"attr": "occ", "obj": "Srv__mdc_a", "value": True}]],
        failure_effects={"f_service_prod_available": True},
        repair_cond=lambda: False,
        repair_param=1e9,
    )

    # MdC_proc — kills the two process functions once the upstream service
    # flow has reached this component.
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_proc",
        targets=["Proc"],
        failure_param=8,
        failure_cond=[[{"attr": "f_service_fed_in", "value": True}]],
        failure_effects={
            "F1_fed_available_out": False,
            "F2_fed_available_out": False,
        },
        repair_cond=lambda: False,
        repair_param=1e9,
    )

    return system


# ----------------------------------------------------------------------------
# Snapshot + scripted run (no TUI deps)
# ----------------------------------------------------------------------------


def _mdc_active(system: muscadet.System, fm_comp_name: str, aut_name: str) -> bool:
    aut = system.comp[fm_comp_name].automata_d[aut_name]
    return aut.get_state_by_name("occ")._bkd.isActive()


def _snapshot(system: muscadet.System) -> str:
    a = _mdc_active(system, "Srv__mdc_a", "mdc_a")
    b = _mdc_active(system, "Srv__mdc_b", "mdc_b")
    p = _mdc_active(system, "Proc__mdc_proc", "mdc_proc")
    svc_out = system.comp["Srv"].flows_out["f_service"].var_fed.value()
    svc_in = system.comp["Proc"].flows_in["f_service"].var_fed.value()
    f1 = system.comp["Proc"].flows_out["F1"].var_fed.value()
    f2 = system.comp["Proc"].flows_out["F2"].var_fed.value()
    return (
        f"t={system.currentTime():g} | "
        f"MdC[A={int(a)} B={int(b)} P={int(p)}] | "
        f"Srv.svc_out={int(svc_out)} Proc.svc_in={int(svc_in)} | "
        f"Proc[F1={int(f1)} F2={int(f2)}]"
    )


def _fire_next(system: muscadet.System, label: str) -> None:
    """Fire the earliest pending fireable transition."""
    transitions = system.isimu_fireable_transitions()
    fireable = [(i, t) for i, t in enumerate(transitions) if t]
    if not fireable:
        print(f"{label:20s} (no fireable transitions)")
        return
    fireable.sort(key=lambda it: it[1].end_time)
    idx, tr = fireable[0]
    system.isimu_set_transition(idx, date=tr.end_time)
    system.isimu_step_forward()
    print(f"{label:20s} {_snapshot(system)}")


def run() -> None:
    """Step through the cascade from Python (no TUI dep)."""
    import cod3s

    system = build()
    try:
        system.isimu_start()
        print(f"{'INITIAL':20s} {_snapshot(system)}")

        _fire_next(system, "MdC_A fires")
        _fire_next(system, "MdC_B fires")
        _fire_next(system, "MdC_process fires")

        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


if __name__ == "__main__":
    run()
