"""Cold-standby integration scenario — negation + tempo + port + failure.

Capstone test exercising the three prod_cond primitives together on a realistic
cold-standby architecture (the edgemind RBD_CTRL validation, mirrored
self-contained):

- a main source feeds a target through a sensor ;
- the sensor exposes a ``ctrl`` output that MIRRORS its flow OUTPUT (port='out') ;
- a backup produces when ``ctrl`` is ABSENT (negate) after a startup delay
  (tempo delay) ;
- the main source fails at t=5 (delay failure mode).

Expected: nominal fed by main; a coverage GAP at the failure until the backup's
startup delay elapses; then the backup takes over.
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
            # Produce when ctrl ABSENT (negate), after a 3-unit startup (tempo).
            self.add_flow(
                dict(
                    cls="FlowOutTempo",
                    name="flow",
                    var_prod_cond=[[{"name": "ctrl", "negate": True}]],
                    occ_enable_flow={"cls": "delay", "time": 3},
                    occ_disable_flow={"cls": "delay", "time": 0},
                )
            )

    class Target(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="flow", logic="or"))

    system = muscadet.System(name="ColdStandby")
    system.add_component(name="MainSrc", cls="Source")
    system.add_component(name="Sensor", cls="Sensor")
    system.add_component(name="Bkp", cls="Backup")
    system.add_component(name="Tgt", cls="Target")

    system.connect_flow("MainSrc", "Sensor", "flow")
    system.connect_flow("Sensor", "Bkp", "ctrl")
    system.connect_flow("Sensor", "Tgt", "flow")
    system.connect_flow("Bkp", "Tgt", "flow")

    system.comp["MainSrc"].add_delay_failure_mode(
        name="fail_main",
        failure_cond="flow_fed_out",
        failure_time=5,
        failure_effects=[("flow_fed_available_out", False)],
        repair_time=1000,
    )
    return system


def _fire(system, comp_name):
    tr = system.isimu_fireable_transitions()
    idx = next(
        i for i, t in enumerate(tr) if t is not None and t.comp_name == comp_name
    )
    system.isimu_set_transition(idx)
    system.isimu_step_forward()


def test_cold_standby_takes_over_after_startup_delay(the_system):
    s = the_system

    def fed(comp, direction, flow):
        d = s.comp[comp].flows_out if direction == "out" else s.comp[comp].flows_in
        return d[flow].var_fed.value()

    s.isimu_start()
    # Nominal: main feeds the target; the standby is off (ctrl present).
    assert s.currentTime() == 0
    assert fed("MainSrc", "out", "flow") is True
    assert fed("Sensor", "out", "ctrl") is True
    assert fed("Bkp", "out", "flow") is False
    assert fed("Tgt", "in", "flow") is True

    # Main fails at t=5.
    _fire(the_system, "MainSrc")
    assert s.currentTime() == 5
    assert fed("MainSrc", "out", "flow") is False
    assert fed("Sensor", "out", "ctrl") is False
    # Coverage gap: main down, backup still spinning up.
    assert fed("Bkp", "out", "flow") is False
    assert fed("Tgt", "in", "flow") is False

    # Backup's tempo startup delay elapses at t=8 -> it takes over.
    _fire(the_system, "Bkp")
    assert s.currentTime() == 8
    assert fed("Bkp", "out", "flow") is True
    assert fed("Tgt", "in", "flow") is True

    s.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
