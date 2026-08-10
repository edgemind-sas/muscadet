"""The pre-run step reaches every real entry point, and a failed one is retried.

Two defects of the same family: the model builds, the run completes, and the
numbers are the declared defaults.

**The engine primitive is the entry point, not the wrapper.** The step was
hooked onto ``muscadet.System.simulate`` and ``muscadet.System.isimu_start``.
But ``cod3s.pycatshoo.isimu.engine.ISimuEngine.start`` -- the object behind
``isimu_start_cli``, behind the TUI, and behind every driver that steps a model
by hand -- calls ``system.startInteractive()`` directly and never touches
``isimu_start``. Measured at ``399730d`` on the Src -> Tank chain below:
``prerun_done`` False, ``equation_order`` None, no equation registered, and the
tank standing at **0.0** after four steps while its source advertised 10.0. No
exception, no diagnostic. The step is therefore hooked onto the primitive.

**A pre-run that raised did not run.** ``prerun`` set ``_prerun_done`` BEFORE
calling ``prerun_step``, so a model refused on its first run -- a cycle (R30), a
rate-comparison loop -- was treated as pre-run on the second: that run completed
with zero sweep equations registered and no diagnostic at all. Any script that
catches a model error and re-runs (a notebook, a study driver, an interactive
session) got declared defaults presented as results. The flag is now set once
the step has returned.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, driven and deleted before the next one starts; the fixture snapshots what
each produced and the last is kept alive for the teardown.
"""

import cod3s
import muscadet
import pytest

from cod3s.pycatshoo.isimu.engine import ISimuEngine

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    SourceContinuous,
)

#: What the source advertises, and therefore what the tank must accumulate.
PEP_RATE = 10.0
PEP_VOLUME = 100.0
#: The clock stop the walk is driven to. One unit at 10 per unit fills 10.
PEP_HORIZON = 1.0
#: The integration stops ON a crossing rather than refining it.
PEP_TOL = 0.05


def simu_params():
    """A fresh, minimal batch-run parameter set."""
    return {"nb_runs": 1, "schedule": [{"start": 0, "end": 1, "nvalues": 2}]}


class PepClock(muscadet.ObjFlow):
    """Carries the dated stops an interactive walk can step to."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="tick", var_prod_default=False)

    def set_flows(self, **kwargs):
        super().set_flows(**kwargs)
        for index, date in enumerate((PEP_HORIZON, 2 * PEP_HORIZON)):
            self.add_atm2states(
                name=f"clock_{index}",
                st1="s0",
                st2="s1",
                occ_law_12={"cls": "delay", "time": date},
                cond_occ_21=False,
            )


class PepPipe(muscadet.ObjFlow):
    """A continuous pass-through, so two of them close an algebraic loop."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="q", var_fed_default=1.0)


def build_chain(name):
    """Src -> Tank, the shape a TUI session is opened on."""
    system = muscadet.System(name=name)
    system.add_component(name="SRC", cls="SourceContinuous", flow="q", rate=PEP_RATE)
    system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        flow="q",
        capacity=PEP_VOLUME,
        capacity_name="tank",
        ports="in",
        demand=PEP_RATE,
    )
    system.add_component(name="CLK", cls="PepClock")
    system.auto_connect("SRC", "TANK")
    return system


def walk(step, system, horizon, limit=20):
    """Step until ``horizon`` is reached."""
    for _ in range(limit):
        if system.currentTime() >= horizon:
            return
        step()


@pytest.fixture(scope="module")
def the_run():
    """Drive every entry point and snapshot what the pre-run step did."""
    obs = {}

    # -- The TUI's path: ISimuEngine.start -> system.startInteractive() -----
    esys = build_chain("PrerunEngineEntry")
    engine = ISimuEngine(esys)
    engine.start()

    obs["engine_done"] = esys.prerun_done
    obs["engine_order"] = esys.equation_order
    obs["engine_registrations"] = [
        (reg.comp, reg.method) for reg in esys.equation_registrations
    ]

    walk(engine.step_forward, esys, PEP_HORIZON)
    obs["engine_time"] = esys.currentTime()
    obs["engine_level"] = esys.comp["TANK"].capacities["tank"].get_quantity("q")
    obs["engine_rate"] = esys.comp["SRC"].flows_out["q"].var_fed.value()
    obs["engine_count"] = esys.prerun_count

    engine.stop()
    esys.deleteSys()

    # -- The documented wrapper, on the very same model, as the reference ---
    isys = build_chain("PrerunWrapperEntry")
    isys.isimu_start()
    walk(isys.isimu_step_forward, isys, PEP_HORIZON)

    obs["wrapper_done"] = isys.prerun_done
    obs["wrapper_time"] = isys.currentTime()
    obs["wrapper_level"] = isys.comp["TANK"].capacities["tank"].get_quantity("q")
    obs["wrapper_count"] = isys.prerun_count

    isys.isimu_stop()
    isys.deleteSys()

    # -- A raw startInteractive, with no wrapper anywhere near it -----------
    rsys = build_chain("PrerunRawPrimitive")
    rsys.startInteractive()
    obs["raw_done"] = rsys.prerun_done
    obs["raw_registrations"] = len(rsys.equation_registrations)
    obs["raw_count"] = rsys.prerun_count

    # A second entry is still a no-op: the step is one-shot per engine system.
    rsys.stopInteractive()
    rsys.startInteractive()
    obs["raw_count_after_restart"] = rsys.prerun_count
    rsys.stopInteractive()
    rsys.deleteSys()

    # -- A refused model, run twice ----------------------------------------
    #    An ALGEBRAIC loop: two pass-throughs, nothing integrated between
    #    them, so the refusal of R30 is the one still standing after the
    #    state-broken tear of R-14 (ef7c6b4).
    fsys = muscadet.System(name="PrerunRefusedTwice")
    fsys.add_component(name="P1", cls="PepPipe")
    fsys.add_component(name="P2", cls="PepPipe")
    fsys.connect_flow(source="P1", target="P2", flow_name="q")
    fsys.connect_flow(source="P2", target="P1", flow_name="q")

    obs["fail_first"] = None
    try:
        fsys.simulate(simu_params())
    except Exception as err:
        obs["fail_first"] = err

    obs["fail_second"] = None
    try:
        fsys.simulate(simu_params())
    except Exception as err:
        obs["fail_second"] = err

    obs["fail_done"] = fsys.prerun_done
    obs["fail_registrations"] = len(fsys.equation_registrations)
    obs["fail_count"] = fsys.prerun_count

    # ... and the interactive entry point refuses it too, rather than opening
    # an inert session on it.
    obs["fail_interactive"] = None
    try:
        fsys.isimu_start()
    except Exception as err:
        obs["fail_interactive"] = err

    obs["system"] = fsys
    return obs


# ----------------------------------------------------------------------
# The entry point behind the TUI
# ----------------------------------------------------------------------


def test_the_engine_entry_point_runs_the_prerun_step(the_run):
    """``ISimuEngine.start`` calls the primitive, never the wrapper."""
    assert the_run["engine_done"] is True
    assert the_run["engine_order"] is not None
    assert the_run["engine_count"] == 1


def test_the_engine_entry_point_registers_the_sweep_equations(the_run):
    """Both sweeps of both continuous components, or nothing evaluates."""
    assert ("SRC", "compute_demand") in the_run["engine_registrations"]
    assert ("SRC", "compute_production") in the_run["engine_registrations"]
    assert ("TANK", "compute_demand") in the_run["engine_registrations"]
    assert ("TANK", "compute_production") in the_run["engine_registrations"]


def test_the_tank_actually_fills_on_the_engine_entry_point(the_run):
    """The number the defect produced: a tank at 0.0 under a source at 10.0.

    Nothing was refused and nothing was reported -- the sweeps were simply
    never registered, so no equation ran and the level stayed at its declared
    content.
    """
    assert the_run["engine_rate"] == pytest.approx(PEP_RATE)
    assert the_run["engine_level"] > 0.0, "the tank must not stay at its default"
    assert the_run["engine_level"] == pytest.approx(
        PEP_RATE * the_run["engine_time"], rel=PEP_TOL
    )


def test_the_two_interactive_entry_points_agree(the_run):
    """The wrapper and the primitive must not report different physics."""
    assert the_run["wrapper_done"] is True
    assert the_run["engine_time"] == pytest.approx(the_run["wrapper_time"])
    assert the_run["engine_level"] == pytest.approx(the_run["wrapper_level"])
    assert the_run["wrapper_count"] == 1


def test_a_raw_start_interactive_runs_the_step_exactly_once(the_run):
    """The primitive on its own, and a restart that does not re-register."""
    assert the_run["raw_done"] is True
    assert the_run["raw_registrations"] > 0
    assert the_run["raw_count"] == 1
    assert the_run["raw_count_after_restart"] == 1


# ----------------------------------------------------------------------
# A pre-run that raised did not run
# ----------------------------------------------------------------------


def test_a_refused_model_is_refused_again_on_the_next_run(the_run):
    """The second run must not present declared defaults as results."""
    first = the_run["fail_first"]
    second = the_run["fail_second"]

    assert isinstance(first, muscadet.ContinuousFlowCycleError)
    assert second is not None, "a model refused once must not run silently next"
    assert isinstance(second, muscadet.ContinuousFlowCycleError)

    # The same diagnostic, naming the same connections: the second report is
    # not a degraded echo of the first.
    assert str(second) == str(first)


def test_a_refused_model_leaves_no_pre_run_behind(the_run):
    """Nothing was registered, so nothing is done -- and the counter says so."""
    assert the_run["fail_done"] is False
    assert the_run["fail_registrations"] == 0
    assert the_run["fail_count"] == 2


def test_the_interactive_entry_point_refuses_it_too(the_run):
    """A refused model must not open an inert interactive session either."""
    assert isinstance(the_run["fail_interactive"], muscadet.ContinuousFlowCycleError)


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
