"""Instantaneous loops closed through an OBSERVATION of a rate (R43).

``find_rate_comparison_loops`` refuses a loop closed by a comparison on a
continuous flow VALUE, and exempts measurement links wholesale. The exemption
had one ground and it was written down: a measurement carried a capacity LEVEL,
an integrated state, and integrated state breaks a loop.

Two changes made that ground false, and this module pins down both.

**A published rate is not an integrated state.** Since R38 a continuous output
publishes what it delivers on ``{f}_rate_out``, and a measurement channel
declared ``kind="rate"`` reads it. That number is recomputed by the allocation
sweep at every evaluation, so a threshold on it is algebraic exactly as a
threshold on the transported flow is. Wire the signal back onto the component
producing the rate and the two regimes select each other within one instant.

**A controller is invisible to the graph, not exempted from it.**
:class:`muscadet.ObjCtrl` is a PEER of ``ObjFlow`` and carries no flow at all,
so the continuous-flow graph does not hold its nodes and the signal walk of
``find_rate_comparison_loops`` -- indexed on flow collections -- never reaches
its edges. A controller thresholding a rate and driving its producer therefore
escaped the check entirely: not exempted, unseen.

What must keep building is asserted here just as hard as what must be refused,
because the failure mode of this unit is an ACCEPTANCE and not an error: the
sanctioned pattern of F4/AE18 -- observe a capacity LEVEL, drive the component
filling it -- has exactly the topology of the refused montages and differs only
in what stands between the two.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, driven, inspected and deleted before the next one starts; the fixture
snapshots what each produced.
"""

import warnings

import cod3s
import muscadet
import pytest

from muscadet import ordering

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

#: The rate a gated source delivers while it is told to run.
ROL_RATE = 10.0

#: The threshold every comparison of this module is declared at. Sits strictly
#: between 0 and :data:`ROL_RATE`, so a gated source flips the comparison every
#: time it starts or stops -- which is what makes the loop a loop.
ROL_THRESHOLD = 5.0

#: What a consumer asks for: enough not to throttle what it watches, so the
#: delivered rate is the declared one and the comparison is unambiguous.
ROL_DEMAND = 1e6

#: The tank of the sanctioned montage, and what it starts at.
ROL_VOLUME = 100.0
ROL_INIT = 10.0

#: The band the sanctioned montage refills between.
ROL_BAND_ON = 3.0
ROL_BAND_OFF = 7.0

#: Resolved rather than imported: the characterisation half of this module runs
#: against the detector as it stands, where this class does not exist yet.
RateObservationLoopError = getattr(ordering, "RateObservationLoopError", None)


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class RolGatedSource(muscadet.ObjFlow):
    """Delivers its rate while ``run`` is UNFED, nothing once it is fed.

    The producing half of the DIRECT comparison loop, the one the shipped
    detector already refuses. Gated by a rule guard, which
    ``gates_production_on`` recognises.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=ROL_RATE)
        self.add_flow_in(name="run", logic="and")
        self.add_rules(
            name="q_control",
            rules=[
                dict(name="idle", cond="run", prod={"q": 0.0}),
                dict(name="supply", cond="not run", prod={"q": ROL_RATE}),
            ],
        )


class RolRateGate(muscadet.ObjFlow):
    """Thresholds the rate it RECEIVES, over a continuous input.

    The shape ``RateComparisonLoopError`` was written for: the comparison reads
    a transported flow, so the loop it closes runs through the continuous graph
    and the walk indexed on flows finds it.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=ROL_DEMAND)
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="run",
                var_prod_cond=[{"name": "q", "op": ">=", "value": ROL_THRESHOLD}],
            )
        )


class RolRateSensor(muscadet.ObjFlow):
    """Thresholds the rate it OBSERVES, over a measurement link (R38).

    The same comparison as :class:`RolRateGate` on the same number, reached
    through the observation box instead of through transport. It takes no share
    of what it watches, which is exactly why the graph carries no edge for it.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="q", kind="rate")
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="run",
                var_prod_cond=[{"name": "q", "op": ">=", "value": ROL_THRESHOLD}],
            )
        )


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_controller_on_a_rate_scenario(obs):
    """A controller thresholds an observed RATE and drives its producer."""
    system = muscadet.System(name="RateObsLoopControllerRate")

    system.add_component(
        name="CR_SRC", cls="SourceContinuous", flow="q", rate=ROL_RATE, control="run"
    )
    system.add_component(
        name="CR_SINK", cls="ConsumerContinuous", flow="q", demand=ROL_DEMAND
    )
    system.add_component(
        name="CR_CTRL",
        cls="ObjCtrl",
        controls_in=[{"name": "q", "kind": "rate"}],
        controls_out=[
            {
                "name": "run",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "q",
                    "operator": "<",
                    "threshold": ROL_THRESHOLD,
                },
            }
        ],
    )

    system.connect_flow(source="CR_SRC", target="CR_SINK", flow_name="q")
    system.connect("CR_SRC", "q_rate_out", "CR_CTRL", "q_rate_in")
    system.connect("CR_CTRL", "run_out", "CR_SRC", "run_in")

    # The graph carries the transport edge and nothing else: the observation
    # link and the control signal are not continuous connections, which is the
    # whole reason a second detection path is needed.
    obs["ctrl_rate_edges"] = ordering.build_continuous_flow_graph(system).edges

    start_and_record(system, obs, "ctrl_rate")

    system.deleteSys()


def run_controller_on_a_level_scenario(obs):
    """The sanctioned montage: observe a LEVEL, drive the source filling it."""
    system = muscadet.System(name="RateObsLoopControllerLevel")

    system.add_component(
        name="CL_SRC", cls="SourceContinuous", flow="q", rate=ROL_RATE, control="fill"
    )
    system.add_component(
        name="CL_CAP",
        cls="CapacityContinuous",
        flow="q",
        capacity=ROL_VOLUME,
        capacity_name="tank",
        content_init={"q": ROL_INIT},
        fill_rate=float("inf"),
    )
    system.add_component(name="CL_SINK", cls="ConsumerContinuous", flow="q", demand=1.0)
    system.add_component(
        name="CL_CTRL",
        cls="ObjCtrl",
        controls_in=[{"name": "tank"}],
        controls_out=[
            {
                "name": "fill",
                "kind": "bool",
                "emit": {
                    "op": "band",
                    "input": "tank",
                    "direction": "below",
                    "activate": ROL_BAND_ON,
                    "release": ROL_BAND_OFF,
                },
            }
        ],
    )

    system.connect_flow(source="CL_SRC", target="CL_CAP", flow_name="q")
    system.connect_flow(source="CL_CAP", target="CL_SINK", flow_name="q")
    system.connect("CL_CAP", "tank_level_out", "CL_CTRL", "tank_level_in")
    system.connect("CL_CTRL", "fill_out", "CL_SRC", "fill_in")

    start_and_record(system, obs, "ctrl_level")

    system.deleteSys()


def run_direct_comparison_scenario(obs):
    """The refusal already in place, over a continuous INPUT."""
    system = muscadet.System(name="RateObsLoopDirectComparison")

    system.add_component(name="DC_SRC", cls="RolGatedSource")
    system.add_component(name="DC_GATE", cls="RolRateGate")

    system.connect_flow(source="DC_SRC", target="DC_GATE", flow_name="q")
    system.connect_flow(source="DC_GATE", target="DC_SRC", flow_name="run")

    start_and_record(system, obs, "direct")

    system.deleteSys()


def run_sensor_on_a_rate_scenario(obs):
    """The same threshold as the direct one, reached over an observation link."""
    system = muscadet.System(name="RateObsLoopSensorRate")

    system.add_component(name="SR_SRC", cls="RolGatedSource")
    system.add_component(
        name="SR_SINK", cls="ConsumerContinuous", flow="q", demand=ROL_DEMAND
    )
    system.add_component(name="SR_SENS", cls="RolRateSensor")

    system.connect_flow(source="SR_SRC", target="SR_SINK", flow_name="q")
    system.connect("SR_SRC", "q_rate_out", "SR_SENS", "q_rate_in")
    system.connect_flow(source="SR_SENS", target="SR_SRC", flow_name="run")

    start_and_record(system, obs, "sensor")

    system.deleteSys()


def run_republished_rate_scenario(obs):
    """A rate republished by one controller, thresholded by the next (R4).

    The hole ``MeasurementOut`` names in its own docstring: the observing
    controller declares a LEVEL channel, because a republication is
    indistinguishable from a capacity's, and what it reads is a rate all the
    same.
    """
    system = muscadet.System(name="RateObsLoopRepublished")

    system.add_component(
        name="RP_SRC", cls="SourceContinuous", flow="q", rate=ROL_RATE, control="run"
    )
    system.add_component(
        name="RP_SINK", cls="ConsumerContinuous", flow="q", demand=ROL_DEMAND
    )
    system.add_component(
        name="RP_PROBE",
        cls="ObjCtrl",
        controls_in=[{"name": "q", "kind": "rate"}],
        controls_out=[
            {
                "name": "echo",
                "kind": "value",
                "emit": {"op": "republish", "input": "q", "gain": 1.0},
            }
        ],
    )
    system.add_component(
        name="RP_VOTE",
        cls="ObjCtrl",
        controls_in=[{"name": "echo"}],
        controls_out=[
            {
                "name": "run",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "echo",
                    "operator": "<",
                    "threshold": ROL_THRESHOLD,
                },
            }
        ],
    )

    system.connect_flow(source="RP_SRC", target="RP_SINK", flow_name="q")
    system.connect("RP_SRC", "q_rate_out", "RP_PROBE", "q_rate_in")
    system.connect("RP_PROBE", "echo_level_out", "RP_VOTE", "echo_level_in")
    system.connect("RP_VOTE", "run_out", "RP_SRC", "run_in")

    start_and_record(system, obs, "republished")

    system.deleteSys()


def run_open_chain_scenario(obs):
    """Rate observations that close nothing, and must therefore build.

    Three near misses at once, and refusing any of them would be worse than
    missing a loop:

    * a chain of rate observations ending on a boolean output wired to nobody;
    * the same chain driving a control port on a component that produces no
      part of the observed rate;
    * a controller observing a rate and publishing a reading, full stop.
    """
    system = muscadet.System(name="RateObsLoopOpenChain")

    system.add_component(name="OC_SRC", cls="SourceContinuous", flow="q", rate=ROL_RATE)
    system.add_component(
        name="OC_SINK", cls="ConsumerContinuous", flow="q", demand=ROL_DEMAND
    )
    system.add_component(
        name="OC_PROBE",
        cls="ObjCtrl",
        controls_in=[{"name": "q", "kind": "rate"}],
        controls_out=[
            {
                "name": "echo",
                "kind": "value",
                "emit": {"op": "republish", "input": "q", "gain": 1.0},
            }
        ],
    )
    system.add_component(
        name="OC_VOTE",
        cls="ObjCtrl",
        controls_in=[{"name": "echo"}],
        controls_out=[
            {
                "name": "alarm",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "echo",
                    "operator": ">=",
                    "threshold": ROL_THRESHOLD,
                },
            },
            {
                "name": "spare",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "echo",
                    "operator": "<",
                    "threshold": ROL_THRESHOLD,
                },
            },
        ],
    )

    # An unrelated montage the alarm drives: a control port, on a source that
    # produces no part of the rate the chain observes.
    system.add_component(
        name="OC_OTHER",
        cls="SourceContinuous",
        flow="p",
        rate=ROL_RATE,
        control="alarm",
    )
    system.add_component(
        name="OC_OTHER_SINK", cls="ConsumerContinuous", flow="p", demand=ROL_DEMAND
    )

    system.connect_flow(source="OC_SRC", target="OC_SINK", flow_name="q")
    system.connect_flow(source="OC_OTHER", target="OC_OTHER_SINK", flow_name="p")
    system.connect("OC_SRC", "q_rate_out", "OC_PROBE", "q_rate_in")
    system.connect("OC_PROBE", "echo_level_out", "OC_VOTE", "echo_level_in")
    system.connect("OC_VOTE", "alarm_out", "OC_OTHER", "alarm_in")
    # ``spare`` is wired to nobody, on purpose.

    start_and_record(system, obs, "open")

    obs["system"] = system


def start_and_record(system, obs, prefix):
    """Try to start ``system``, record whether it did and what it raised."""
    obs[f"{prefix}_error"] = None
    obs[f"{prefix}_started"] = False

    try:
        system.isimu_start()
        obs[f"{prefix}_started"] = True
    except Exception as err:  # noqa: BLE001 -- the refusal IS the observation
        obs[f"{prefix}_error"] = err

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            system.isimu_stop()
        except Exception:  # pragma: no cover - nothing was started
            pass


@pytest.fixture(scope="module")
def the_run():
    """Every scenario, built, driven and deleted in turn."""
    obs = {}

    run_controller_on_a_rate_scenario(obs)
    run_controller_on_a_level_scenario(obs)
    run_direct_comparison_scenario(obs)
    run_sensor_on_a_rate_scenario(obs)
    run_republished_rate_scenario(obs)
    run_open_chain_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# Characterisation: what is refused today, and with what message
# ----------------------------------------------------------------------


def test_the_direct_rate_comparison_is_refused_exactly_as_it_was(the_run):
    """The shipped refusal, message included. Nothing here may move."""
    error = the_run["direct_error"]

    assert error is not None, "a comparison on a transported rate must not start"
    assert isinstance(error, ordering.RateComparisonLoopError)
    assert isinstance(error, muscadet.ContinuousFlowCycleError)
    assert the_run["direct_started"] is False

    message = str(error)
    assert "Continuous flow graph must be acyclic (R30)" in message
    assert "closes a loop through a rate comparison" in message
    assert "DC_SRC.q_out -> DC_GATE.q_in" in message
    assert "DC_GATE.run_out -> DC_SRC.run_in" in message
    assert f"q >= {ROL_THRESHOLD:g}" in message
    assert "CAPACITY LEVEL" in message

    assert error.reader == "DC_GATE"
    assert error.flow == "q"


def test_the_direct_refusal_is_not_the_observation_one(the_run):
    """Two shapes, two errors: the message of each names its own way out."""
    if RateObservationLoopError is None:
        pytest.skip("the observation detector does not exist yet")

    assert not isinstance(the_run["direct_error"], RateObservationLoopError)


def test_the_sanctioned_level_montage_builds(the_run):
    """F4/AE18: observe a capacity LEVEL, drive the component filling it.

    Same topology as every refused montage of this module. The level is an
    integrated state and that is the whole difference, so a detector that
    refused this one would have understood nothing.
    """
    assert the_run["ctrl_level_error"] is None, str(the_run["ctrl_level_error"])
    assert the_run["ctrl_level_started"] is True


def test_an_open_chain_of_rate_observations_builds(the_run):
    """Observing a rate is not the offence; driving its producer is."""
    assert the_run["open_error"] is None, str(the_run["open_error"])
    assert the_run["open_started"] is True


def test_a_controller_adds_no_edge_to_the_continuous_graph(the_run):
    """Why the shipped walk cannot see any of this.

    A controller carries no flow, so it is not a node; an observation link
    carries no quantity, so it is not an edge. The transport edge is the whole
    of what the graph holds, and the loop closes entirely outside it.
    """
    assert the_run["ctrl_rate_edges"] == [("CR_SRC", "CR_SINK")]


# ----------------------------------------------------------------------
# The loop an observed rate closes (R43)
# ----------------------------------------------------------------------


def test_a_controller_thresholding_an_observed_rate_is_refused(the_run):
    """The montage the whole unit exists for."""
    error = the_run["ctrl_rate_error"]

    assert error is not None, (
        "a controller thresholding a rate and driving its producer closes an "
        "instantaneous loop and must not start"
    )
    assert isinstance(error, muscadet.ContinuousFlowCycleError)
    assert the_run["ctrl_rate_started"] is False


def test_the_observation_loop_error_names_the_wiring_and_the_way_out(the_run):
    """A refusal a modeller can act on: what closes it, and what to do."""
    message = str(the_run["ctrl_rate_error"])

    assert "CR_SRC.q_rate_out -> CR_CTRL.q_rate_in" in message
    assert "CR_CTRL.run_out -> CR_SRC.run_in" in message
    assert f"q < {ROL_THRESHOLD:g}" in message
    # The supported alternative, named the way the shipped refusal names it.
    assert "CAPACITY LEVEL" in message


def test_the_observation_loop_error_carries_what_it_found(the_run):
    """Inspected directly rather than read out of the message."""
    if RateObservationLoopError is None:
        pytest.skip("the observation detector does not exist yet")

    error = the_run["ctrl_rate_error"]

    assert isinstance(error, RateObservationLoopError)
    assert error.reader == "CR_CTRL"
    assert error.channel == "q"
    assert error.flow == "q"
    assert error.producer == "CR_SRC"


def test_a_sensor_thresholding_an_observed_rate_is_refused(the_run):
    """The same threshold as the direct shape, over an observation link.

    Nothing about the offence is specific to a controller: an ``ObjFlow``
    reading ``kind="rate"`` in a discrete production condition closes the very
    same loop, and the measurement exemption used to wave it through.
    """
    error = the_run["sensor_error"]

    assert error is not None, "a sensor thresholding an observed rate must not start"
    assert isinstance(error, muscadet.ContinuousFlowCycleError)
    assert the_run["sensor_started"] is False

    message = str(error)
    assert "SR_SRC.q_rate_out -> SR_SENS.q_rate_in" in message
    assert "SR_SENS.run_out -> SR_SRC.run_in" in message


def test_a_republished_rate_driven_back_onto_its_producer_is_refused(the_run):
    """The hole ``MeasurementOut`` names: one hop further out, same loop.

    The thresholding controller declares a LEVEL channel and is right to: an
    observer cannot tell a republisher from a capacity. What decides is what
    the reading came FROM, which is why the taint has to travel.
    """
    error = the_run["republished_error"]

    assert error is not None, (
        "a rate republished and thresholded back onto its producer closes an "
        "instantaneous loop and must not start"
    )
    assert isinstance(error, muscadet.ContinuousFlowCycleError)
    assert the_run["republished_started"] is False

    message = str(error)
    assert "RP_SRC.q_rate_out -> RP_PROBE.q_rate_in" in message
    assert "RP_PROBE.echo_level_out -> RP_VOTE.echo_level_in" in message
    assert "RP_VOTE.run_out -> RP_SRC.run_in" in message


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
