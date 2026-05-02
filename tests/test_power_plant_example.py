"""Sanity / regression tests for the power_plant pedagogical example.

Pins the three core scenarios so the documented behaviour cannot drift
silently as muscadet / cod3s evolve:

A. ``hw_pumpA`` alone — the cold redundancy must restore Plant electricity
   in steady state (transient flicker is allowed).
B. Full cyber kill chain (phishing → lateral → disable_main → inhibit_backup)
   must defeat the redundancy: Plant electricity reaches False AND stays
   False after all transitions resolve.
C. Partial cyber chain (without inhibit_backup) must NOT defeat the
   redundancy in steady state.

These cover the pedagogical claims of ``examples/isimu/power_plant.py``.
"""

import sys
from pathlib import Path

import muscadet
import cod3s
import pytest

# Make the example module importable.
EXAMPLE_DIR = (Path(__file__).resolve().parent.parent / "examples" / "isimu").as_posix()
if EXAMPLE_DIR not in sys.path:
    sys.path.insert(0, EXAMPLE_DIR)


# Importing the example module registers the component classes via cod3s'
# Pydantic-based class registry, so the ``cls=...`` lookups below resolve.
import power_plant  # noqa: F401,E402


def _base_system(name):
    sys = muscadet.System(name=name)
    sys.add_component(name="Grid", cls="Grid")
    sys.add_component(name="PumpA", cls="MainPump")
    sys.add_component(name="PumpB", cls="BackupPump")
    sys.add_component(name="Plant", cls="Plant")
    sys.add_component(name="HMI", cls="HMI")
    sys.connect_flow("Grid", "PumpA", "power")
    sys.connect_flow("Grid", "PumpB", "power")
    sys.connect_flow("Grid", "Plant", "power")
    sys.connect_flow("PumpA", "Plant", "cooling")
    sys.connect_flow("PumpB", "Plant", "cooling")
    sys.connect_trigger("PumpA", "PumpB", "cooling")
    return sys


def _drain(system, max_steps=20):
    """Fire all available transitions in earliest-first order."""
    log = []
    for _ in range(max_steps):
        trs = system.isimu_fireable_transitions()
        fireable = [(i, t) for i, t in enumerate(trs) if t]
        if not fireable:
            break
        fireable.sort(key=lambda it: it[1].end_time)
        idx, tr = fireable[0]
        system.isimu_set_transition(idx, date=tr.end_time)
        system.isimu_step_forward()
        log.append((tr.comp_name, tr.name, system.currentTime()))
    return log


def _elec(system):
    return system.comp["Plant"].flows_out["electricity"].var_fed.value()


def _pump_a(system):
    return system.comp["PumpA"].flows_out["cooling"].var_fed.value()


def _pump_b(system):
    return system.comp["PumpB"].flows_out["cooling"].var_fed.value()


# ----------------------------------------------------------------------------
# Initial-state sanity
# ----------------------------------------------------------------------------


def test_initial_state_nominal():
    """Under nominal: Grid up, PumpA producing, PumpB idle, Plant producing."""
    system = _base_system("init")
    try:
        system.isimu_start()
        assert system.comp["Grid"].flows_out["power"].var_fed.value() is True
        assert _pump_a(system) is True
        assert _pump_b(system) is False, "Cold redundancy: PumpB starts idle"
        assert (
            system.comp["Plant"].flows_in["cooling"].var_fed.value() is True
        ), "Plant receives cooling from PumpA"
        assert _elec(system) is True
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ----------------------------------------------------------------------------
# Scenario A: hw_pumpA alone — redundancy mitigates in steady state
# ----------------------------------------------------------------------------


def test_scenario_A_hw_pumpA_redundancy_recovers():
    system = _base_system("scen_a")
    system.add_component(
        cls="ObjFMDelay",
        fm_name="hw_pumpA",
        targets=["PumpA"],
        failure_param=8,
        failure_effects={"cooling_fed_available_out": False},
        repair_cond=lambda: False,
        repair_param=1e9,
    )
    try:
        system.isimu_start()
        log = _drain(system)
        # Two events: hw_pumpA at t=8, then trigger_up at t=8 (same time, separate).
        assert len(log) == 2
        comp_a, _, t_a = log[0]
        comp_b, name_b, t_b = log[1]
        assert "hw_pumpA" in comp_a
        assert t_a == pytest.approx(8.0)
        assert name_b == "cooling_trigger_up"
        assert t_b == pytest.approx(8.0)
        # Steady state: PumpB took over
        assert _pump_a(system) is False
        assert _pump_b(system) is True
        assert _elec(system) is True
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ----------------------------------------------------------------------------
# Scenario B: full cyber kill chain — redundancy defeated
# ----------------------------------------------------------------------------


def test_scenario_B_full_cyber_chain_defeats_redundancy():
    system = _base_system("scen_b")
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_phishing",
        targets=["HMI"],
        failure_param=10,
        failure_effects={},
        repair_cond=lambda: False,
        repair_param=1e9,
    )
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_lateral",
        targets=["HMI"],
        failure_param=5,
        failure_cond=[[{"attr": "occ", "obj": "HMI__mdc_phishing", "value": True}]],
        failure_effects={},
        repair_cond=lambda: False,
        repair_param=1e9,
    )
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_disable_main",
        targets=["PumpA"],
        failure_param=3,
        failure_cond=[[{"attr": "occ", "obj": "HMI__mdc_lateral", "value": True}]],
        failure_effects={"cooling_fed_available_out": False},
        repair_cond=lambda: False,
        repair_param=1e9,
    )
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_inhibit_backup",
        targets=["PumpB"],
        failure_param=4,
        failure_cond=[[{"attr": "occ", "obj": "HMI__mdc_lateral", "value": True}]],
        failure_effects={"cooling_fed_available_out": False},
        repair_cond=lambda: False,
        repair_param=1e9,
    )
    try:
        system.isimu_start()
        log = _drain(system)
        # Expected order:
        # 1. mdc_phishing @ t=10
        # 2. mdc_lateral @ t=15
        # 3. mdc_disable_main @ t=18 (PumpA cooling drops)
        # 4. PumpB cooling_trigger_up @ t=18 (PumpB takes over)
        # 5. mdc_inhibit_backup @ t=19 (PumpB cooling drops, no fallback)
        comp_names = [comp for comp, _, _ in log]
        assert any("mdc_phishing" in c for c in comp_names)
        assert any("mdc_lateral" in c for c in comp_names)
        assert any("mdc_disable_main" in c for c in comp_names)
        assert any("mdc_inhibit_backup" in c for c in comp_names)
        # Trigger fired (the redundancy DID activate at some point)
        trigger_names = [name for _, name, _ in log if "trigger" in name]
        assert "cooling_trigger_up" in trigger_names
        # Steady state: redundancy DEFEATED
        assert _pump_a(system) is False
        assert _pump_b(system) is False
        assert _elec(system) is False
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ----------------------------------------------------------------------------
# Scenario C: partial cyber chain (no inhibit_backup) — redundancy holds
# ----------------------------------------------------------------------------


def test_scenario_C_partial_cyber_redundancy_holds():
    system = _base_system("scen_c")
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_phishing",
        targets=["HMI"],
        failure_param=10,
        failure_effects={},
        repair_cond=lambda: False,
        repair_param=1e9,
    )
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_lateral",
        targets=["HMI"],
        failure_param=5,
        failure_cond=[[{"attr": "occ", "obj": "HMI__mdc_phishing", "value": True}]],
        failure_effects={},
        repair_cond=lambda: False,
        repair_param=1e9,
    )
    system.add_component(
        cls="ObjFMDelay",
        fm_name="mdc_disable_main",
        targets=["PumpA"],
        failure_param=3,
        failure_cond=[[{"attr": "occ", "obj": "HMI__mdc_lateral", "value": True}]],
        failure_effects={"cooling_fed_available_out": False},
        repair_cond=lambda: False,
        repair_param=1e9,
    )
    try:
        system.isimu_start()
        log = _drain(system)
        # Trigger fired
        trigger_names = [name for _, name, _ in log if "trigger" in name]
        assert "cooling_trigger_up" in trigger_names
        # Steady state: PumpA killed by cyber, PumpB took over, plant OK
        assert _pump_a(system) is False
        assert _pump_b(system) is True
        assert _elec(system) is True
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ----------------------------------------------------------------------------
# var_prod_cond on FlowOutOnTrigger: PumpB requires power to activate
# ----------------------------------------------------------------------------


def test_pumpB_requires_power_to_activate():
    """Backup pump declared with var_prod_cond=[["power"]] must NOT activate
    when its trigger fires but no power is available (Grid down case)."""
    system = _base_system("grid_first")
    system.add_component(
        cls="ObjFMDelay",
        fm_name="hw_grid",
        targets=["Grid"],
        failure_param=5,
        failure_effects={"power_fed_available_out": False},
        repair_cond=lambda: False,
        repair_param=1e9,
    )
    try:
        system.isimu_start()
        _drain(system)
        # Grid down → PumpA loses power → cooling stops → trigger fires →
        # but PumpB also has no power → must not activate.
        assert _pump_a(system) is False
        assert _pump_b(system) is False, (
            "PumpB must not activate without power "
            "(var_prod_cond=[['power']] gating)"
        )
        assert _elec(system) is False
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()
