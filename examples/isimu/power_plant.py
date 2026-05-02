"""Mini electricity production plant with cold-redundant cooling.

System architecture::

    [Grid] ─┬─ power ──> [PumpA] ── cooling ─┐
            ├─ power ──> [PumpB] ── cooling ─┤── [Plant] ── electricity ──>
            └─ power ──────────────────────────────^
                          ▲
                          │ trigger (PumpA.cooling_out → PumpB.cooling_trigger_in)
                          │
            PumpB stays idle while PumpA feeds; activates instantly when
            PumpA's cooling drops (FlowOutOnTrigger with trigger_logic="and").

Components:

- ``Grid``  — power source (always on under nominal).
- ``PumpA`` — main cooling pump. Needs power, produces cooling.
- ``PumpB`` — backup cooling pump (cold redundancy). Needs power. Its
  cooling output is a :class:`muscadet.FlowOutOnTrigger` whose trigger
  is fed by ``PumpA.cooling_out`` — so it activates only when the main
  pump stops feeding.
- ``Plant`` — production unit. Needs power AND cooling (any pump).
  Produces the ``electricity`` flow (the redoubt-event variable).
- ``HMI``   — operator/SCADA workstation. Holds the cyber attack chain
  (``mdc_phishing``, ``mdc_lateral_movement``); has no physical flow.

Failure modes:

Hardware (MdD, exponential laws):
- ``hw_grid``   — grid power loss.
- ``hw_pumpA``  — main pump mechanical failure.
- ``hw_pumpB``  — backup pump mechanical failure (lower rate; less used).

Cyber compromise modes (MdC, exponential laws unless noted), forming
a classical IT → OT attack chain that defeats the redundancy:

- ``mdc_phishing``        — initial entry on HMI (slow).
- ``mdc_lateral_movement``— pivot to OT network (medium, gated on
  ``mdc_phishing.occ``).
- ``mdc_disable_main_pump`` — firmware exploit on PumpA's PLC: forces
  ``cooling_fed_available_out=False`` on PumpA (gated on
  ``mdc_lateral_movement.occ``).
- ``mdc_inhibit_backup_pump`` — disables the safety interlock that
  would activate PumpB: forces ``cooling_fed_available_out=False`` on
  PumpB itself (gated on ``mdc_lateral_movement.occ``).

The interesting consequence: under nominal operation, a single failure
of ``PumpA`` (hardware) triggers the backup pump and the plant keeps
producing. But the cyber attack is **coordinated** — both pumps are
disabled in parallel — defeating the redundancy.

Run::

    cod3s-isimu --factory examples.isimu.power_plant:build
    python -m examples.isimu.power_plant

For a probabilistic study with mixed MdD + MdC sequences::

    cd examples/isimu
    run-cod3s-study --model power_plant_model.yaml \\
        --study-specs power_plant_study.yaml \\
        --log-level INFO
"""

from __future__ import annotations

import muscadet

# Re-export for the YAML loader (model.yaml uses ``python_class: System``).
System = muscadet.System


# ----------------------------------------------------------------------------
# Component classes
# ----------------------------------------------------------------------------


class Grid(muscadet.ObjFlow):
    """External power source."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="power", var_prod_default=True)


class MainPump(muscadet.ObjFlow):
    """Main cooling pump — runs continuously while powered."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="power", logic="and")
        self.add_flow_out(name="cooling", var_prod_cond=[["power"]])


class BackupPump(muscadet.ObjFlow):
    """Backup cooling pump — cold redundancy via FlowOutOnTrigger.

    The trigger input ``cooling_trigger_in`` is wired to the main
    pump's ``cooling_out`` (see ``connect_trigger`` in :func:`build`).
    With ``trigger_logic="and"``, the backup activates when the
    trigger input is False (main pump no longer feeding).

    ``var_prod_cond=[["power"]]`` ensures the backup also requires
    power to actually deliver cooling (otherwise an unplugged backup
    would still appear active when the trigger fires).
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="power", logic="and")
        self.add_flow_out_on_trigger(
            name="cooling",
            trigger_time_up=0,
            trigger_time_down=0,
            trigger_logic="and",
            var_prod_default=True,
            var_prod_cond=[["power"]],
        )


class Plant(muscadet.ObjFlow):
    """Production unit — needs power AND cooling, produces electricity."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="power", logic="and")
        self.add_flow_in(name="cooling", logic="or")  # either pump feeds
        self.add_flow_out(
            name="electricity",
            var_prod_cond=[["power"], ["cooling"]],
        )


class HMI(muscadet.ObjFlow):
    """Operator / SCADA workstation. No physical flow.

    Hosts the IT-side cyber compromise modes (``mdc_phishing``,
    ``mdc_lateral_movement``) that gate the OT-side exploits via
    automaton-state references in :file:`power_plant_study.yaml`.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        # Intentionally no flows — this is a "logical" component
        # representing the IT side of the system.


# ----------------------------------------------------------------------------
# System builder
# ----------------------------------------------------------------------------


def build() -> muscadet.System:
    """Build the populated system. Compatible with ``cod3s-isimu --factory``."""
    system = muscadet.System(name="power_plant")
    system.add_component(name="Grid", cls="Grid")
    system.add_component(name="PumpA", cls="MainPump")
    system.add_component(name="PumpB", cls="BackupPump")
    system.add_component(name="Plant", cls="Plant")
    system.add_component(name="HMI", cls="HMI")

    # Power network
    system.connect_flow("Grid", "PumpA", "power")
    system.connect_flow("Grid", "PumpB", "power")
    system.connect_flow("Grid", "Plant", "power")

    # Cooling network
    system.connect_flow("PumpA", "Plant", "cooling")
    system.connect_flow("PumpB", "Plant", "cooling")

    # Trigger wiring: PumpA's cooling output drives PumpB's trigger.
    # When PumpA stops feeding, PumpB fires up.
    system.connect_trigger("PumpA", "PumpB", "cooling")

    # ------------------------------------------------------------------
    # Hardware failure modes (MdD) — deterministic delays for the run()
    # script. The probabilistic study YAML overrides with exponential
    # laws.
    # ------------------------------------------------------------------
    # Single PumpA hardware failure at t=8 to demonstrate the cold
    # redundancy taking over before any cyber attack.
    system.add_component(
        cls="ObjFMDelay",
        fm_name="hw_pumpA",
        targets=["PumpA"],
        failure_param=8,
        failure_effects={"cooling_fed_available_out": False},
        repair_cond=lambda: False,
        repair_param=1e9,
    )

    # ------------------------------------------------------------------
    # Cyber compromise chain (MdC).
    # ------------------------------------------------------------------
    # IT side, on the HMI: phishing then lateral movement (state-only).
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_phishing",
        targets=["HMI"],
        failure_param=20,
        failure_effects={},
        repair_cond=lambda: False,
        repair_param=1e9,
    )
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_lateral_movement",
        targets=["HMI"],
        failure_param=5,
        failure_cond=[[{"attr": "occ", "obj": "HMI__mdc_phishing", "value": True}]],
        failure_effects={},
        repair_cond=lambda: False,
        repair_param=1e9,
    )

    # OT side, the two coordinated exploits gated on lateral movement.
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_disable_main_pump",
        targets=["PumpA"],
        failure_param=3,
        failure_cond=[
            [{"attr": "occ", "obj": "HMI__mdc_lateral_movement", "value": True}]
        ],
        failure_effects={"cooling_fed_available_out": False},
        repair_cond=lambda: False,
        repair_param=1e9,
    )
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_inhibit_backup_pump",
        targets=["PumpB"],
        failure_param=4,
        failure_cond=[
            [{"attr": "occ", "obj": "HMI__mdc_lateral_movement", "value": True}]
        ],
        failure_effects={"cooling_fed_available_out": False},
        repair_cond=lambda: False,
        repair_param=1e9,
    )

    return system


# ----------------------------------------------------------------------------
# Snapshot + scripted run (no TUI deps)
# ----------------------------------------------------------------------------


def _mdc_active(system, fm_comp_name: str, aut_name: str) -> bool:
    aut = system.comp[fm_comp_name].automata_d[aut_name]
    return aut.get_state_by_name("occ")._bkd.isActive()


def _snapshot(system) -> str:
    grid = system.comp["Grid"].flows_out["power"].var_fed.value()
    pa_cool = system.comp["PumpA"].flows_out["cooling"].var_fed.value()
    pb_cool = system.comp["PumpB"].flows_out["cooling"].var_fed.value()
    plant_cool = system.comp["Plant"].flows_in["cooling"].var_fed.value()
    plant_out = system.comp["Plant"].flows_out["electricity"].var_fed.value()
    ph = _mdc_active(system, "HMI__mdc_phishing", "mdc_phishing")
    lt = _mdc_active(system, "HMI__mdc_lateral_movement", "mdc_lateral_movement")
    da = _mdc_active(system, "PumpA__mdc_disable_main_pump", "mdc_disable_main_pump")
    ib = _mdc_active(
        system, "PumpB__mdc_inhibit_backup_pump", "mdc_inhibit_backup_pump"
    )
    return (
        f"t={system.currentTime():g} | "
        f"Grid={int(grid)} PumpA={int(pa_cool)} PumpB={int(pb_cool)} | "
        f"Plant.cool_in={int(plant_cool)} Plant.elec={int(plant_out)} | "
        f"MdC[ph={int(ph)} lt={int(lt)} dpa={int(da)} ibp={int(ib)}]"
    )


def _fire_next(system, label: str) -> None:
    """Fire the earliest pending fireable transition."""
    transitions = system.isimu_fireable_transitions()
    fireable = [(i, t) for i, t in enumerate(transitions) if t]
    if not fireable:
        print(f"{label:30s} (no fireable transition)")
        return
    fireable.sort(key=lambda it: it[1].end_time)
    idx, tr = fireable[0]
    system.isimu_set_transition(idx, date=tr.end_time)
    system.isimu_step_forward()
    print(f"{label:30s} {_snapshot(system)}")


def run() -> None:
    """Step through a scripted scenario.

    With the delays in :func:`build`:

    - t=8  : ``hw_pumpA`` fires — main pump down. The trigger fires
             instantly and PumpB takes over. Plant keeps producing.
    - t=20 : ``mdc_phishing`` (after ``hw_pumpA`` so the redundancy
             situation is visible during the cyber chain).
    - t=25 : ``mdc_lateral_movement``.
    - t=28 : ``mdc_disable_main_pump`` (PumpA already down — no-op on
             the flow but the MdC fires nonetheless).
    - t=29 : ``mdc_inhibit_backup_pump`` — backup pump cooling drops,
             Plant loses cooling, output collapses.
    """
    import cod3s

    system = build()
    try:
        system.isimu_start()
        print(f"{'INITIAL':30s} {_snapshot(system)}")

        _fire_next(system, "hw_pumpA fires")
        # PumpB trigger_up fires instantly after PumpA drop.
        _fire_next(system, "PumpB trigger_up")
        _fire_next(system, "mdc_phishing")
        _fire_next(system, "mdc_lateral_movement")
        _fire_next(system, "mdc_disable_main_pump")
        _fire_next(system, "mdc_inhibit_backup_pump")

        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


if __name__ == "__main__":
    run()
