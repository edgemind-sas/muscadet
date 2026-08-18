"""The shared pre-run step: both run entry points go through it, exactly once.

``muscadet.System`` has no build step -- the modeller adds components, connects
them, then simulates -- so anything that must see the whole connected graph has
to run at the *start of a run*. And the two run entry points do not converge in
``cod3s``: ``simulate`` goes through ``prepare_simu``, while ``isimu_start``
calls ``startInteractive`` / ``stepForward`` directly and never touches
``prepare_simu``.

A step wired only into the batch path would therefore never run in an
interactive session -- which is what this module pins down. Each test asserts
on a *trace* recording when the step ran relative to the engine call of its
entry point, so "it ran" and "it ran first" are distinguishable.
"""

import contextlib

import muscadet
import cod3s
import pytest


def simu_params():
    """A fresh, minimal batch-run parameter set."""
    return {"nb_runs": 1, "schedule": [{"start": 0, "end": 1, "nvalues": 2}]}


class TracingSystem(muscadet.System):
    """Records the pre-run step and the engine call of each entry point.

    ``startInteractive`` and ``prepare_simu`` are the last muscadet-visible
    moment before each path hands over to the engine, so their position in the
    trace is what proves the step ran *before* the run started.
    """

    @property
    def trace(self):
        if not hasattr(self, "_trace"):
            self._trace = []
        return self._trace

    def prerun_step(self):
        self.trace.append("prerun_step")
        return super().prerun_step()

    def startInteractive(self, *args, **kwargs):
        self.trace.append("startInteractive")
        return super().startInteractive(*args, **kwargs)

    def prepare_simu(self, *args, **kwargs):
        self.trace.append("prepare_simu")
        return super().prepare_simu(*args, **kwargs)


class PrerunSrc(muscadet.ObjFlow):
    """Discrete-only producer."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="x", var_prod_default=True)


class PrerunSnk(muscadet.ObjFlow):
    """Discrete-only consumer."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="x", logic="or")


def build_system(name):
    """A discrete-only system: the pre-run step must stay a no-op on it."""
    system = TracingSystem(name=name)
    system.add_component(name="SRC", cls="PrerunSrc")
    system.add_component(name="SNK", cls="PrerunSnk")
    system.auto_connect("SRC", "SNK")
    return system


@pytest.fixture(scope="module")
def the_run():
    """Drive both entry points and snapshot what the pre-run step did.

    PyCATSHOO forbids two live systems in one process, so the interactive
    system is fully driven and deleted before the batch one is built. The
    tests below assert on the snapshots this fixture collects.
    """
    obs = {}

    # -- Interactive path ------------------------------------------------
    isys = build_system("PrerunInteractive")
    obs["int_trace_before"] = list(isys.trace)
    obs["int_done_before"] = isys.prerun_done
    obs["int_count_before"] = isys.prerun_count

    isys.isimu_start()
    obs["int_trace_after_start"] = list(isys.trace)
    obs["int_count_after_start"] = isys.prerun_count
    obs["int_manager_after_start"] = isys.pdmp_manager
    obs["int_done_after_start"] = isys.prerun_done

    isys.isimu_step_forward()
    obs["int_trace_after_step"] = list(isys.trace)

    # -- Stop, then restart: the step must not run again ------------------
    isys.isimu_stop()
    isys.isimu_start()
    obs["int_trace_after_restart"] = list(isys.trace)
    obs["int_count_after_restart"] = isys.prerun_count
    isys.isimu_stop()

    isys.deleteSys()
    obs["int_done_after_delete"] = isys.prerun_done
    obs["int_count_after_delete"] = isys.prerun_count

    # -- Batch path ------------------------------------------------------
    bsys = build_system("PrerunBatch")
    obs["batch_trace_before"] = list(bsys.trace)
    obs["batch_count_before"] = bsys.prerun_count

    bsys.simulate(simu_params())
    obs["batch_trace_after_run1"] = list(bsys.trace)
    obs["batch_count_after_run1"] = bsys.prerun_count

    # A second cod3s batch run re-registers the indicator instants and the
    # engine refuses it -- a pre-existing cod3s limitation, unrelated to the
    # pre-run step. The step is invoked *before* prepare_simu, so the
    # idempotence guard is exercised either way, and the trace shows it.
    with contextlib.suppress(Exception):
        bsys.simulate(simu_params())
    obs["batch_trace_after_run2"] = list(bsys.trace)
    obs["batch_count_after_run2"] = bsys.prerun_count
    obs["batch_manager"] = bsys.pdmp_manager

    obs["system"] = bsys
    return obs


def test_prerun_has_not_run_before_any_entry_point(the_run):
    """Declaring components and connecting them never triggers the step."""
    assert the_run["int_trace_before"] == []
    assert the_run["int_done_before"] is False
    assert the_run["int_count_before"] == 0
    assert the_run["batch_trace_before"] == []
    assert the_run["batch_count_before"] == 0


def test_prerun_runs_before_the_interactive_engine_starts(the_run):
    """The interactive path never touches prepare_simu.

    A step wired only into the batch entry point would leave this trace as
    ``["startInteractive"]`` -- silently doing nothing on the path the suite
    exercises most.
    """
    assert the_run["int_trace_after_start"] == ["prerun_step", "startInteractive"]
    assert the_run["int_count_after_start"] == 1
    assert the_run["int_done_after_start"] is True


def test_prerun_does_not_run_again_on_the_first_interactive_step(the_run):
    """Stepping forward is not a new run: the trace is unchanged."""
    assert the_run["int_trace_after_step"] == ["prerun_step", "startInteractive"]


def test_interactive_restart_does_not_rerun_the_step(the_run):
    """A stop followed by a start re-enters the engine, not the pre-run step."""
    assert the_run["int_trace_after_restart"] == [
        "prerun_step",
        "startInteractive",
        "startInteractive",
    ]
    assert the_run["int_count_after_restart"] == 1


def test_batch_run_goes_through_the_step_before_prepare_simu(the_run):
    """The batch path runs the step first too, ahead of prepare_simu."""
    assert the_run["batch_trace_after_run1"] == ["prerun_step", "prepare_simu"]
    assert the_run["batch_count_after_run1"] == 1


def test_two_successive_batch_runs_run_the_step_once(the_run):
    """The second run re-enters prepare_simu, never the pre-run step."""
    assert the_run["batch_trace_after_run2"] == [
        "prerun_step",
        "prepare_simu",
        "prepare_simu",
    ]
    assert the_run["batch_count_after_run2"] == 1


def test_prerun_is_a_no_op_on_a_discrete_only_system(the_run):
    """The step runs on a discrete-only system but leaves no trace on it.

    No PDMP manager on either path: purely discrete models must stay exactly
    what they were before the continuous layer existed.
    """
    assert the_run["int_count_after_start"] == 1
    assert the_run["int_manager_after_start"] is None
    assert the_run["batch_count_after_run1"] == 1
    assert the_run["batch_manager"] is None


def test_deleting_the_system_clears_the_prerun_flag(the_run):
    """Engine-bound state is released; the diagnostic counter survives."""
    assert the_run["int_done_after_delete"] is False
    assert the_run["int_count_after_delete"] == 1


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
