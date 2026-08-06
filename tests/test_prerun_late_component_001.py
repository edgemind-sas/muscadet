"""A model that grew after the pre-run step is refused, not run inert (R-11).

The pre-run step runs **once** per engine system: it derives the evaluation
order from the whole connection graph and registers the two sweep equations of
every continuous component on the PDMP manager. Both run entry points go
through it, and ``_prerun_done`` makes the second call a no-op.

A continuous component added after that first run cycle was therefore never
registered. It ran **inert** -- no rule evaluated, no demand published, its
outputs frozen at their declared defaults -- and, because the components
feeding it saw no demand from it, they silently produced less too. Nothing was
reported: the run completed and every number it produced was wrong.

Why it is refused rather than fixed
-----------------------------------
Establishing it experimentally was the point:

* PyCATSHOO **refuses** to register an equation its manager already holds --
  ``[E]L'ODE SNK1.compute_demand appartient déjà au PDMP muscadet_pdmp`` -- and
  ``IPDMPManager`` carries no removal counterpart. A manager's equation set is
  append-only;
* the order is derived **globally** from the connection graph, so a late
  component does not merely add equations, it renumbers existing ones -- and
  those are precisely the registrations that can no longer be redone;
* appending the new equations above every order already taken is all that is
  left, and it places a demand equation above production equations, breaking
  the band separation ``ObjFlow.get_output_request`` documents and relies on.

So a second pass cannot register a late component correctly, and registering it
incorrectly is worse than not registering it. What is left is to say so, at the
point of use, which is what this module pins down.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, driven and deleted before the next one starts.
"""

import contextlib

import muscadet
import cod3s
import pytest

from muscadet.system import ModelChangedAfterPrerunError

#: A date the interactive session can always step to, so the solver integrates.
LATE_CLOCK = 5.0

#: A minimal batch-run parameter set.
LATE_SIMU = {"nb_runs": 1, "schedule": [{"start": 0, "end": 1, "nvalues": 2}]}


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class LateSource(muscadet.ObjFlow):
    """A continuous producer holding the rate it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=kwargs.get("rate", 20.0))


class LateMid(muscadet.ObjFlow):
    """One unit of ``q`` in, one unit of ``x`` out."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="x")
        self.add_rules(name="make", rules=[dict(cons={"q": 1}, prod={"x": 1})])


class LateConverter(muscadet.ObjFlow):
    """2 of ``x`` make 1 of ``y``: the component added too late."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="x")
        self.add_flow_continuous_out(name="y")
        self.add_rules(name="conv", rules=[dict(cons={"x": 2}, prod={"y": 1})])


class LateSink(muscadet.ObjFlow):
    """A pure consumer of one named flow."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "x"), var_demand_default=kwargs.get("demand", 4.0)
        )


class LateDiscreteSrc(muscadet.ObjFlow):
    """A purely discrete producer: no continuous flow anywhere."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="b", var_prod_default=True)


class LateDiscreteSnk(muscadet.ObjFlow):
    """A purely discrete consumer."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="b", logic="or")


# ----------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------


def add_clock(comp):
    """Give the interactive session a date it can always step to."""
    comp.add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": LATE_CLOCK},
        cond_occ_21=False,
    )


def build_stage1(name, spare_source=False):
    """SRC -> MID -> SNK, optionally with a second source left unwired."""
    system = muscadet.System(name=name)
    system.add_component(name="SRC", cls="LateSource", rate=20.0)
    system.add_component(name="MID", cls="LateMid")
    system.add_component(name="SNK", cls="LateSink", flow="x", demand=4.0)
    system.connect_flow(source="SRC", target="MID", flow_name="q")
    system.connect_flow(source="MID", target="SNK", flow_name="x")

    if spare_source:
        # Present at pre-run time, wired to nothing: adding the CONNECTION
        # later changes the graph without adding a component.
        system.add_component(name="SPARE", cls="LateSource", rate=7.0)

    add_clock(system.comp["MID"])
    return system


def add_stage2(system):
    """The branch added too late: a converter and its consumer."""
    system.add_component(name="CONV", cls="LateConverter")
    system.add_component(name="SNK2", cls="LateSink", flow="y", demand=3.0)
    system.connect_flow(source="MID", target="CONV", flow_name="x")
    system.connect_flow(source="CONV", target="SNK2", flow_name="y")


def readings(system):
    """What the whole chain settles at."""
    return {
        "mid_demand_q": system.comp["MID"].flows_in["q"].var_demand.value(),
        "mid_x": system.comp["MID"].flows_out["x"].var_fed.value(),
        "conv_y": system.comp["CONV"].flows_out["y"].var_fed.value(),
        "snk2_y": system.comp["SNK2"].flows_in["y"].get_delivered(),
    }


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_reference(obs):
    """The same model assembled in ONE go: what the late build should equal."""
    system = build_stage1("LateRef")
    add_stage2(system)

    system.isimu_start()
    system.isimu_step_forward()
    obs["reference"] = readings(system)
    system.isimu_stop()

    system.deleteSys()


def run_late_interactive(obs):
    """A continuous branch added after the first interactive run cycle."""
    system = build_stage1("LateInteractive")

    system.isimu_start()
    system.isimu_step_forward()
    system.isimu_stop()
    obs["count_after_first_run"] = system.prerun_count

    # A restart that changes nothing must stay the silent no-op it always was.
    obs["clean_restart_error"] = None
    try:
        system.isimu_start()
        system.isimu_stop()
    except Exception as err:  # pragma: no cover - a regression would land here
        obs["clean_restart_error"] = err
    obs["count_after_clean_restart"] = system.prerun_count

    add_stage2(system)

    # The one-shot guard is still a no-op ...
    obs["prerun_error"] = None
    try:
        system.prerun()
    except Exception as err:
        obs["prerun_error"] = err

    # ... and the entry point refuses rather than running the branch inert.
    obs["isimu_error"] = None
    try:
        system.isimu_start()
        system.isimu_step_forward()
        obs["inert_readings"] = readings(system)
        system.isimu_stop()
    except Exception as err:
        obs["isimu_error"] = err

    obs["count_after_refusal"] = system.prerun_count
    obs["registered_late"] = [
        (reg.comp, reg.method)
        for reg in system.equation_registrations
        if reg.comp in ("CONV", "SNK2")
    ]

    system.deleteSys()
    obs["done_after_delete"] = system.prerun_done


def run_late_batch(obs):
    """The same, on the batch entry point: it goes through the step too."""
    system = build_stage1("LateBatch")
    system.simulate(dict(LATE_SIMU))

    add_stage2(system)

    obs["batch_error"] = None
    try:
        system.simulate(dict(LATE_SIMU))
    except Exception as err:
        obs["batch_error"] = err

    system.deleteSys()


def run_late_connection(obs):
    """A CONNECTION added between two components that both already existed."""
    system = build_stage1("LateConnection", spare_source=True)

    system.isimu_start()
    system.isimu_step_forward()
    system.isimu_stop()

    system.connect_flow(source="SPARE", target="MID", flow_name="q")

    obs["connection_error"] = None
    try:
        system.isimu_start()
        system.isimu_step_forward()
        system.isimu_stop()
    except Exception as err:
        obs["connection_error"] = err

    system.deleteSys()


def run_discrete_growth(obs):
    """1.x compatibility: a purely discrete system may still grow between runs."""
    system = muscadet.System(name="LateDiscrete")
    system.add_component(name="D_SRC", cls="LateDiscreteSrc")
    system.add_component(name="D_SNK", cls="LateDiscreteSnk")
    system.auto_connect("D_SRC", "D_SNK")

    system.isimu_start()
    system.isimu_step_forward()
    system.isimu_stop()

    system.add_component(name="D_SNK2", cls="LateDiscreteSnk")
    system.auto_connect("D_SRC", "D_SNK2")

    obs["discrete_error"] = None
    try:
        system.isimu_start()
        system.isimu_step_forward()
        obs["discrete_fed"] = system.comp["D_SNK2"].flows_in["b"].var_fed.value()
        system.isimu_stop()
    except Exception as err:
        obs["discrete_error"] = err

    obs["discrete_signature"] = system.model_signature()

    # Kept alive for the teardown test, per the module convention.
    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_reference(obs)
    run_late_interactive(obs)
    run_late_batch(obs)
    run_late_connection(obs)
    run_discrete_growth(obs)

    return obs


# ----------------------------------------------------------------------
# What the refusal is protecting
# ----------------------------------------------------------------------


def test_the_same_model_assembled_in_one_go_runs_the_whole_chain(the_run):
    """The reference: MID feeds both consumers and CONV converts.

    MID demands 4 for SNK and 6 for CONV -- CONV turns 2 of x into 1 of y and
    its consumer wants 3 -- so 10 travel and CONV delivers 3.
    """
    reference = the_run["reference"]

    assert reference["mid_demand_q"] == pytest.approx(10.0)
    assert reference["mid_x"] == pytest.approx(10.0)
    assert reference["conv_y"] == pytest.approx(3.0)
    assert reference["snk2_y"] == pytest.approx(3.0)


# ----------------------------------------------------------------------
# The refusal itself
# ----------------------------------------------------------------------


def test_a_continuous_component_added_after_the_first_run_is_refused(the_run):
    """R-11: ``isimu_start`` refuses instead of running the branch inert.

    Against ``dabc2b1`` the restart succeeded and CONV produced 0 while MID
    demanded 4 instead of 10 -- the whole reference above, silently wrong, with
    no diagnostic anywhere.
    """
    error = the_run["isimu_error"]

    assert isinstance(error, ModelChangedAfterPrerunError)
    assert "inert_readings" not in the_run, "the inert run must not have happened"

    # Nothing was registered for the late branch, which is why it is refused
    assert the_run["registered_late"] == []


def test_the_refusal_names_the_components_added_since_the_prerun(the_run):
    """A diagnostic pointing at the model, not at the machinery."""
    message = str(the_run["isimu_error"])

    assert "CONV" in message
    assert "SNK2" in message
    assert "components added since" in message

    # ... and it says what to do about it
    assert "before the first simulate() / isimu_start()" in message
    assert "LateInteractive" in message, "the system must be named"


def test_the_batch_entry_point_refuses_it_too(the_run):
    """``simulate`` goes through the pre-run step, so it refuses identically.

    A guard wired only into the interactive path would leave the batch one --
    the path that writes the golden CSVs -- silently producing the wrong file.
    """
    assert isinstance(the_run["batch_error"], ModelChangedAfterPrerunError)
    assert "CONV" in str(the_run["batch_error"])


def test_a_connection_added_between_existing_components_is_refused(the_run):
    """A new edge renumbers the order exactly as a new node does.

    SPARE existed at pre-run time and was wired to nothing; connecting it to
    MID afterwards adds no component and still changes the graph the order was
    derived from, and the sweep of a producer feeding MID cannot be reordered
    against MID's.
    """
    error = the_run["connection_error"]

    assert isinstance(error, ModelChangedAfterPrerunError)
    assert "connections added since" in str(error)
    assert "SPARE.q_out -> MID.q_in" in str(error)


def test_the_error_is_a_value_error_and_reachable_from_the_package_root(the_run):
    """It is caught by anything already catching muscadet model errors."""
    assert issubclass(ModelChangedAfterPrerunError, ValueError)
    assert muscadet.ModelChangedAfterPrerunError is ModelChangedAfterPrerunError


# ----------------------------------------------------------------------
# What the refusal must NOT break
# ----------------------------------------------------------------------


def test_an_unchanged_restart_is_still_a_silent_no_op(the_run):
    """A stop followed by a start re-enters the engine, not the pre-run step."""
    assert the_run["clean_restart_error"] is None
    assert the_run["count_after_first_run"] == 1
    assert the_run["count_after_clean_restart"] == 1

    # The refused run did not run the step again either
    assert the_run["count_after_refusal"] == 1
    assert the_run["done_after_delete"] is False


def test_prerun_itself_refuses_rather_than_returning_false(the_run):
    """The guard lives in ``prerun``, which is what both entry points share."""
    assert isinstance(the_run["prerun_error"], ModelChangedAfterPrerunError)


def test_a_purely_discrete_system_may_still_grow_between_runs(the_run):
    """1.x compatibility: no continuous model, nothing to renumber, no refusal.

    The signature of a discrete-only system is empty on both sides however many
    components it gains, so a 1.x model keeps behaving exactly as it did.
    """
    assert the_run["discrete_error"] is None
    assert the_run["discrete_fed"] is True
    assert the_run["discrete_signature"] == ((), ())


def test_delete(the_run):
    with contextlib.suppress(Exception):
        the_run["system"].isimu_stop()
    the_run["system"].deleteSys()
    cod3s.terminate_session()
