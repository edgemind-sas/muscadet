"""FlowOutTempo under AUTOMATIC (Monte Carlo) scheduling.

``test_cold_standby_scenario.py`` covers the same architecture but drives the
simulation manually (``isimu_set_transition`` / ``isimu_step_forward``), so it never
exercises the *condition-driven* scheduling of the two tempo transitions — which is
exactly what a real Monte Carlo study relies on.

This test closes that gap: the same cold-standby system is run through
``system.simulate(...)`` and the whole cycle is asserted on a single deterministic
trajectory (all laws are delays, so ``nb_runs=1`` is fully reproducible):

- t=2  nominal, the main source feeds the target, the backup is off ;
- t=6  the main failed at t=5, the backup is still starting up -> COVERAGE GAP ;
- t=9  the 3-unit startup delay elapsed at t=8 -> the backup took over ;
- t=13 the main repaired at t=10, the backup shut down after its 1-unit delay ;
- t=20 the main failed again -> the backup is back on (the automaton re-arms).

The shutdown assertions are what guard the ``_disable`` transition, whose condition
used to be registered under the ``_enable`` condition name (cf. issue #1).
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

    class Sensor(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="flow", logic="and"))
            self.add_flow(dict(cls="FlowOut", name="flow", var_prod_cond=["flow"]))
            # ctrl MIRRORS the flow OUTPUT (port='out'), not the raw input.
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="ctrl",
                    var_prod_cond=[[{"name": "flow", "port": "out"}]],
                )
            )

    class Backup(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="ctrl", logic="and"))
            # Produce when ctrl is ABSENT, after a 3-unit startup; stop 1 unit after
            # ctrl comes back (a non-zero disable delay makes the shutdown observable).
            self.add_flow(
                dict(
                    cls="FlowOutTempo",
                    name="flow",
                    var_prod_cond=[[{"name": "ctrl", "negate": True}]],
                    occ_enable_flow={"cls": "delay", "time": 3},
                    occ_disable_flow={"cls": "delay", "time": 1},
                )
            )

    class Target(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="flow", logic="or"))

    system = muscadet.System(name="ColdStandbyAuto")
    system.add_component(name="MainSrc", cls="Source")
    system.add_component(name="Sensor", cls="Sensor")
    system.add_component(name="Bkp", cls="Backup")
    system.add_component(name="Tgt", cls="Target")

    system.connect_flow("MainSrc", "Sensor", "flow")
    system.connect_flow("Sensor", "Bkp", "ctrl")
    system.connect_flow("Sensor", "Tgt", "flow")
    system.connect_flow("Bkp", "Tgt", "flow")

    # Fails 5 units after being fed, repairs 5 units later (so back at t=10).
    system.comp["MainSrc"].add_delay_failure_mode(
        name="fail_main",
        failure_cond="flow_fed_out",
        failure_time=5,
        failure_effects=[("flow_fed_available_out", False)],
        repair_time=5,
    )

    for comp, var in (
        ("MainSrc", "flow_fed_out"),
        ("Bkp", "flow_fed_out"),
        ("Tgt", "flow_fed_in"),
    ):
        system.add_indicator_var(component=comp, var=var, stats=["mean"])

    system.simulate(
        cod3s.PycMCSimulationParam(
            nb_runs=1, schedule=[2.0, 6.0, 9.0, 13.0, 20.0], seed=42
        )
    )
    return system


def _value(system, indicator_name, instant):
    """Read one indicator value at one instant from the simulation results."""
    frame = system.indic_to_frame()
    rows = frame[
        (frame["name"] == indicator_name) & (frame["instant"] == instant)
    ]
    assert not rows.empty, f"no value for {indicator_name} at t={instant}"
    return rows["values"].iloc[0]


def test_nominal_the_backup_stays_off(the_system):
    assert _value(the_system, "MainSrc_flow_fed_out", 2.0) == 1.0
    assert _value(the_system, "Bkp_flow_fed_out", 2.0) == 0.0
    assert _value(the_system, "Tgt_flow_fed_in", 2.0) == 1.0


def test_coverage_gap_while_the_backup_starts_up(the_system):
    # Main failed at t=5, the backup needs 3 units: at t=6 nobody feeds the target.
    assert _value(the_system, "MainSrc_flow_fed_out", 6.0) == 0.0
    assert _value(the_system, "Bkp_flow_fed_out", 6.0) == 0.0
    assert _value(the_system, "Tgt_flow_fed_in", 6.0) == 0.0


def test_backup_takes_over_after_the_startup_delay(the_system):
    # Startup delay elapsed at t=8.
    assert _value(the_system, "Bkp_flow_fed_out", 9.0) == 1.0
    assert _value(the_system, "Tgt_flow_fed_in", 9.0) == 1.0


def test_backup_shuts_down_once_the_main_is_back(the_system):
    # Main repaired at t=10, disable delay is 1 unit -> off well before t=13.
    # This is the assertion that guards the `_disable` transition wiring (issue #1).
    assert _value(the_system, "MainSrc_flow_fed_out", 13.0) == 1.0
    assert _value(the_system, "Bkp_flow_fed_out", 13.0) == 0.0
    assert _value(the_system, "Tgt_flow_fed_in", 13.0) == 1.0


def test_the_automaton_re_arms_on_the_next_failure(the_system):
    # The main fails again after being fed for 5 units; the backup must start over.
    assert _value(the_system, "MainSrc_flow_fed_out", 20.0) == 0.0
    assert _value(the_system, "Bkp_flow_fed_out", 20.0) == 1.0
    assert _value(the_system, "Tgt_flow_fed_in", 20.0) == 1.0


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
