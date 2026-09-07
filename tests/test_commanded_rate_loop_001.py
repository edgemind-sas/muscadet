"""A RATE commanded by a threshold on its own observation (R47).

The third shape of the same offence, and the one no detector saw. Two things
vary: how the threshold reaches the rate, and how its verdict comes back.
``find_rate_comparison_loops`` refuses a comparison on a TRANSPORTED rate;
``find_rate_observation_loops`` (R43) refuses a threshold on an OBSERVED rate
whose verdict leaves as a DISCRETE signal; and a verdict leaving as a rate over
a transported comparison is a plain cycle the continuous graph already refuses,
both its legs being edges. The fourth cell is what was left:

    a continuous output is gated by a comparison on an observed quantity, and
    that quantity is the rate that very output delivers.

Neither path could see it, and for two independent reasons. The continuous
graph holds no edge for the observation half, because a measurement link
carries no quantity and never becomes an edge (KD19). And the signal walk that
picks up what the graph drops is indexed on DISCRETE channels, so a verdict
leaving as a rate travels on nothing it recognises.

**Widening that walk to both families does not close it**, measured on the
montage below: ``measurement_driven_outputs`` does start returning the
continuous output, and the model still builds, because
``discrete_data_channel`` answers None on a continuous flow and the verdict
never leaves the component. What closes it is that there is NOTHING TO WALK: a
commanded rate that reaches the observed producer is a loop at zero hops, and
the observation half has already been proved by the marking of algebraic
readings.

The criterion is tight at BOTH ends, and the near misses here are worth as much
as the refusals: the failure mode of this unit is a wrongful refusal, not a
missed loop.

* at the leaving end, the commanded output must reach the observed producer,
  so a component commanding an output that goes elsewhere keeps building;
* at the arriving end, the producer's own declaration must turn what arrives
  into the observed output, so an input no rule consumes, an input feeding a
  different rule set, and an input a capacity integrates all keep building.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, inspected and deleted before the next one starts; the fixture snapshots
what each produced.
"""

import warnings

import cod3s
import pytest

import muscadet
from muscadet import ordering

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)
from muscadet.ordering import CommandedRateLoopError

#: The rate a commanded output delivers while it is told to run.
CRL_RATE = 10.0

#: The threshold every comparison of this module is declared at. Strictly
#: between 0 and :data:`CRL_RATE`, so the gate flips every time the output
#: starts or stops, which is what makes the loop a loop.
CRL_THRESHOLD = 5.0

#: What a consumer asks for: enough not to throttle what is watched, so the
#: delivered rate is the declared one and the comparison is unambiguous.
CRL_DEMAND = 1e6

#: The tank of the licit montage, and what it starts at.
CRL_VOLUME = 100.0
CRL_INIT = 10.0


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class CrlSelfThrottle(muscadet.ObjFlow):
    """Produces while the rate it is shown is below the threshold.

    The montage of the defect: the reading it is shown is the rate of its own
    output, so the value of this instant decides the value of this instant.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="echo")
        self.add_flow_continuous_out(
            name="q",
            var_fed_default=CRL_RATE,
            var_prod_cond=[{"name": "echo", "op": "<", "value": CRL_THRESHOLD}],
        )


class CrlDeratedThrottle(muscadet.ObjFlow):
    """The same loop, with the verdict reaching the rate through a MODE.

    A production condition is not the only way a threshold commands a rate: an
    alarm a mode watches derates the output just as surely, and the derating
    variable is the endpoint the taint has to travel through.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="echo")
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="alarm",
                var_prod_cond=[{"name": "echo", "op": "<", "value": CRL_THRESHOLD}],
            )
        )
        self.add_flow_continuous_out(name="q", var_fed_default=CRL_RATE)


class CrlLevelThrottle(muscadet.ObjFlow):
    """The licit twin: the same gate, on an integrated LEVEL.

    F4/AE18 with the gate written straight on the continuous output (R44)
    rather than relayed by a discrete one. A level is carried between instants,
    so it breaks the loop and this must keep building.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="tank")
        self.add_flow_continuous_out(
            name="q",
            var_fed_default=CRL_RATE,
            var_prod_cond=[{"name": "tank", "op": "<", "value": CRL_THRESHOLD}],
        )


class CrlGovernor(muscadet.ObjFlow):
    """Commands a rate of its own from a threshold on an observed rate.

    Where its commanded output goes, and what the component it lands on does
    with it, are the whole content of the criterion: wired into the supply the
    observed rate is made of it closes the loop, and anywhere else it does not.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="q", kind="rate")
        self.add_flow_continuous_out(
            name="fuel",
            var_fed_default=CRL_RATE,
            var_prod_cond=[{"name": "q", "op": "<", "value": CRL_THRESHOLD}],
        )


class CrlFuelledSource(muscadet.ObjFlow):
    """Turns what it is fed into what it delivers, one for one.

    The consumption is what makes the governor's command reach this output: the
    rate observed off ``q`` is a function of the ``fuel`` arriving now.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="fuel", var_demand_default=CRL_RATE)
        self.add_flow_continuous_out(name="q")
        self.add_rules(
            name="burn", rules=[dict(name="run", cons={"fuel": 1.0}, prod={"q": 1.0})]
        )


class CrlBufferedSource(muscadet.ObjFlow):
    """The same burner, with a volume standing between the command and the rule.

    A capacity on the input side integrates what arrives before any rule reads
    it (R-14), so the command of this instant cannot move the rate of this
    instant. The refusal's own message advises putting a volume in the way.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="fuel", var_demand_default=CRL_RATE)
        self.add_flow_continuous_out(name="q")
        self.add_rules(
            name="burn", rules=[dict(name="run", cons={"fuel": 1.0}, prod={"q": 1.0})]
        )
        self.add_capacity(
            name="buf",
            flow="fuel",
            side="in",
            capacity=CRL_VOLUME,
            content_init={"fuel": CRL_INIT},
        )


class CrlUnconsumedSource(muscadet.ObjFlow):
    """Delivers a constant, and is fed something no rule of its own consumes."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="fuel", var_demand_default=CRL_RATE)
        self.add_flow_continuous_out(name="q", var_fed_default=CRL_RATE)


class CrlTwoSetSource(muscadet.ObjFlow):
    """Two independent rule sets: ``fuel`` makes ``z``, ``a`` makes ``q``.

    The realistic form of the same near miss. The command lands on this
    component, and on an input it really consumes, and the observed output is
    still not made of it.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a", var_demand_default=CRL_RATE)
        self.add_flow_continuous_in(name="fuel", var_demand_default=CRL_RATE)
        self.add_flow_continuous_out(name="q")
        self.add_flow_continuous_out(name="z")
        self.add_rules(
            name="mq", rules=[dict(name="run", cons={"a": 1.0}, prod={"q": 1.0})]
        )
        self.add_rules(
            name="mz", rules=[dict(name="run", cons={"fuel": 1.0}, prod={"z": 1.0})]
        )


class CrlSplitter(muscadet.ObjFlow):
    """Makes two outputs from one supply, and gates the SIBLING of what it watches.

    The subtlest near miss of the criterion: the commanded output belongs to
    the very component delivering the observed rate, and still reaches nothing.
    A rule's outputs do not throttle each other.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="echo")
        self.add_flow_continuous_in(name="a", var_demand_default=CRL_RATE)
        self.add_flow_continuous_out(name="q")
        self.add_flow_continuous_out(
            name="r",
            var_prod_cond=[{"name": "echo", "op": "<", "value": CRL_THRESHOLD}],
        )
        self.add_rules(
            name="split",
            rules=[dict(name="go", cons={"a": 1.0}, prod={"q": 1.0, "r": 1.0})],
        )


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def add_rate_probe(system, source, name):
    """A controller republishing ``source``'s delivered rate as a reading.

    The one component every montage here needs and none of them is about:
    PyCATSHOO refuses a component wired to itself, so the rate has to leave and
    come back through somebody.
    """
    system.add_component(
        name=name,
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
    system.connect(source, "q_rate_out", name, "q_rate_in")
    system.connect(name, "echo_level_out", source, "echo_level_in")


def run_self_commanded_scenario(obs):
    """The montage of the defect: a rate gated on its own observed value."""
    system = muscadet.System(name="CrlSelfCommanded")

    try:
        system.add_component(name="SC_SRC", cls="CrlSelfThrottle")
        system.add_component(
            name="SC_SINK", cls="ConsumerContinuous", flow="q", demand=CRL_DEMAND
        )
        system.connect_flow(source="SC_SRC", target="SC_SINK", flow_name="q")
        add_rate_probe(system, "SC_SRC", "SC_PROBE")

        comp = system.comp["SC_SRC"]
        graph = ordering.build_continuous_flow_graph(system)

        # What the two halves of the seeding answer on their own, recorded
        # before the refusal: the threshold IS seen, and what it drives is
        # invisible to a walk that only returns discrete outputs.
        obs["self_thresholds"] = ordering.measurement_thresholds(comp, "echo")
        obs["self_signal_outputs"] = ordering.measurement_driven_outputs(comp, "echo")
        obs["self_edges"] = graph.edges

        start_and_record(system, obs, "self")
    finally:
        system.deleteSys()


def run_derated_scenario(obs):
    """The verdict reaches the rate through a mode derating, not a gate."""
    system = muscadet.System(name="CrlDerated")

    try:
        system.add_component(name="DR_SRC", cls="CrlDeratedThrottle")
        system.comp["DR_SRC"].add_atm2states(
            name="cut", cond_occ_12="alarm_fed_out", effects_12=[("q", 0.0)]
        )
        system.add_component(
            name="DR_SINK", cls="ConsumerContinuous", flow="q", demand=CRL_DEMAND
        )
        system.connect_flow(source="DR_SRC", target="DR_SINK", flow_name="q")
        add_rate_probe(system, "DR_SRC", "DR_PROBE")

        comp = system.comp["DR_SRC"]

        # The taint has to cross the mode and land on the derating variable,
        # which is the endpoint the continuous family is clamped through.
        obs["derated_signals"] = comp.mode_signals.get("cut")
        obs["derated_rates"] = ordering.measurement_driven_rates(comp, "echo")

        start_and_record(system, obs, "derated")
    finally:
        system.deleteSys()


def run_level_scenario(obs):
    """The licit neighbour: the observed quantity is an integrated level."""
    system = muscadet.System(name="CrlLevel")

    try:
        system.add_component(name="LV_SRC", cls="CrlLevelThrottle")
        system.add_component(
            name="LV_CAP",
            cls="CapacityContinuous",
            flow="q",
            capacity=CRL_VOLUME,
            capacity_name="tank",
            content_init={"q": CRL_INIT},
            fill_rate=float("inf"),
        )
        system.add_component(
            name="LV_SINK", cls="ConsumerContinuous", flow="q", demand=1.0
        )

        system.connect_flow(source="LV_SRC", target="LV_CAP", flow_name="q")
        system.connect_flow(source="LV_CAP", target="LV_SINK", flow_name="q")
        system.connect("LV_CAP", "tank_level_out", "LV_SRC", "tank_level_in")

        start_and_record(system, obs, "level")
    finally:
        system.deleteSys()


def run_command_elsewhere_scenario(obs):
    """Leaving end, negative: the commanded rate is delivered elsewhere."""
    system = muscadet.System(name="CrlElsewhere")

    try:
        system.add_component(
            name="EL_SRC", cls="SourceContinuous", flow="q", rate=CRL_RATE
        )
        system.add_component(
            name="EL_SINK", cls="ConsumerContinuous", flow="q", demand=CRL_DEMAND
        )
        system.add_component(name="EL_GOV", cls="CrlGovernor")
        system.add_component(
            name="EL_OTHER", cls="ConsumerContinuous", flow="fuel", demand=CRL_DEMAND
        )

        system.connect_flow(source="EL_SRC", target="EL_SINK", flow_name="q")
        system.connect("EL_SRC", "q_rate_out", "EL_GOV", "q_rate_in")
        system.connect_flow(source="EL_GOV", target="EL_OTHER", flow_name="fuel")

        # The command IS driven by the threshold here too: what differs from
        # the refused montage is only where it goes.
        obs["elsewhere_rates"] = ordering.measurement_driven_rates(
            system.comp["EL_GOV"], "q"
        )

        start_and_record(system, obs, "elsewhere")
    finally:
        system.deleteSys()


def run_sibling_output_scenario(obs):
    """Leaving end, negative: the command is a sibling of what is observed.

    ``get_uptake_factor`` is the MAXIMUM over a rule's outputs (R-13), so
    cutting one leaves the others and the draw where they were. Measured
    directly, on this very rule at a supply of 8.0: ``r`` cut to zero leaves
    ``q`` at 8.0 and the draw at 8.0, and only cutting both takes the three to
    zero. So this must build, and it is why the zero-hop branch asks for the
    commanded output to BE the observed one rather than merely to share its
    component.
    """
    system = muscadet.System(name="CrlSibling")

    try:
        system.add_component(name="SB_SRC", cls="SourceContinuous", flow="a", rate=8.0)
        system.add_component(name="SB_SPL", cls="CrlSplitter")
        system.add_component(
            name="SB_QS", cls="ConsumerContinuous", flow="q", demand=CRL_DEMAND
        )
        system.add_component(
            name="SB_RS", cls="ConsumerContinuous", flow="r", demand=CRL_DEMAND
        )

        system.connect_flow(source="SB_SRC", target="SB_SPL", flow_name="a")
        system.connect_flow(source="SB_SPL", target="SB_QS", flow_name="q")
        system.connect_flow(source="SB_SPL", target="SB_RS", flow_name="r")
        add_rate_probe(system, "SB_SPL", "SB_PROBE")

        obs["sibling_rates"] = ordering.measurement_driven_rates(
            system.comp["SB_SPL"], "echo"
        )

        start_and_record(system, obs, "sibling")
    finally:
        system.deleteSys()


def run_command_feeds_producer_scenario(obs):
    """Both ends positive: the command is the supply the observed rate is made of."""
    system = muscadet.System(name="CrlFeedsProducer")

    try:
        system.add_component(name="FD_SRC", cls="CrlFuelledSource")
        system.add_component(
            name="FD_SINK", cls="ConsumerContinuous", flow="q", demand=CRL_DEMAND
        )
        system.add_component(name="FD_GOV", cls="CrlGovernor")

        system.connect_flow(source="FD_SRC", target="FD_SINK", flow_name="q")
        system.connect("FD_SRC", "q_rate_out", "FD_GOV", "q_rate_in")
        system.connect_flow(source="FD_GOV", target="FD_SRC", flow_name="fuel")

        obs["feeds_signal_outputs"] = ordering.measurement_driven_outputs(
            system.comp["FD_GOV"], "q"
        )
        obs["feeds_rates"] = ordering.measurement_driven_rates(
            system.comp["FD_GOV"], "q"
        )

        start_and_record(system, obs, "feeds")
    finally:
        system.deleteSys()


def run_unconsumed_input_scenario(obs):
    """Arriving end, negative: no rule of the producer consumes the command."""
    system = muscadet.System(name="CrlUnconsumed")

    try:
        system.add_component(name="UC_SRC", cls="CrlUnconsumedSource")
        system.add_component(
            name="UC_SINK", cls="ConsumerContinuous", flow="q", demand=CRL_DEMAND
        )
        system.add_component(name="UC_GOV", cls="CrlGovernor")

        system.connect_flow(source="UC_SRC", target="UC_SINK", flow_name="q")
        system.connect("UC_SRC", "q_rate_out", "UC_GOV", "q_rate_in")
        system.connect_flow(source="UC_GOV", target="UC_SRC", flow_name="fuel")

        start_and_record(system, obs, "unconsumed")
    finally:
        system.deleteSys()


def run_other_rule_set_scenario(obs):
    """Arriving end, negative: the command feeds a different rule set."""
    system = muscadet.System(name="CrlTwoSets")

    try:
        system.add_component(
            name="TS_A", cls="SourceContinuous", flow="a", rate=CRL_RATE
        )
        system.add_component(name="TS_SRC", cls="CrlTwoSetSource")
        system.add_component(
            name="TS_QS", cls="ConsumerContinuous", flow="q", demand=CRL_DEMAND
        )
        system.add_component(
            name="TS_ZS", cls="ConsumerContinuous", flow="z", demand=CRL_DEMAND
        )
        system.add_component(name="TS_GOV", cls="CrlGovernor")

        system.connect_flow(source="TS_A", target="TS_SRC", flow_name="a")
        system.connect_flow(source="TS_SRC", target="TS_QS", flow_name="q")
        system.connect_flow(source="TS_SRC", target="TS_ZS", flow_name="z")
        system.connect("TS_SRC", "q_rate_out", "TS_GOV", "q_rate_in")
        system.connect_flow(source="TS_GOV", target="TS_SRC", flow_name="fuel")

        start_and_record(system, obs, "othersets")
    finally:
        system.deleteSys()


def run_buffered_command_scenario(obs):
    """Arriving end, negative: a capacity integrates the command first."""
    system = muscadet.System(name="CrlBuffered")

    try:
        system.add_component(name="BF_SRC", cls="CrlBufferedSource")
        system.add_component(
            name="BF_SINK", cls="ConsumerContinuous", flow="q", demand=CRL_DEMAND
        )
        system.add_component(name="BF_GOV", cls="CrlGovernor")

        system.connect_flow(source="BF_SRC", target="BF_SINK", flow_name="q")
        system.connect("BF_SRC", "q_rate_out", "BF_GOV", "q_rate_in")
        system.connect_flow(source="BF_GOV", target="BF_SRC", flow_name="fuel")

        graph = ordering.build_continuous_flow_graph(system)

        # The state break is what the refusal has to honour, and it is on the
        # command leg itself.
        obs["buffered_broken"] = [
            (cnct.source, cnct.target, cnct.flow)
            for cnct in graph.state_broken_connections
        ]

        start_and_record(system, obs, "buffered")

        obs["system"] = system
    except Exception:  # pragma: no cover - the fixture would hide the cause
        system.deleteSys()
        raise


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
    """Every scenario, built, inspected and deleted in turn."""
    obs = {}

    run_self_commanded_scenario(obs)
    run_derated_scenario(obs)
    run_level_scenario(obs)
    run_command_elsewhere_scenario(obs)
    run_sibling_output_scenario(obs)
    run_command_feeds_producer_scenario(obs)
    run_unconsumed_input_scenario(obs)
    run_other_rule_set_scenario(obs)
    run_buffered_command_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# Why neither shipped path could see it
# ----------------------------------------------------------------------


def test_the_threshold_is_seen_and_what_it_drives_is_not(the_run):
    """The exact shape of the gap, asserted rather than described.

    The seeding half landed with R44 and works: the threshold on the reading is
    found. The return half is what is missing, and it is missing STRUCTURALLY,
    not by omission: the walk returns discrete outputs because it travels on
    discrete channels, and the verdict here is a rate.
    """
    assert the_run["self_thresholds"] == [f"echo < {CRL_THRESHOLD:g}"]
    assert the_run["self_signal_outputs"] == []


def test_no_edge_of_the_continuous_graph_carries_any_of_it(the_run):
    """The other half of the same explanation.

    The observation link is not an edge (KD19) and the command is a production
    condition rather than a connection, so the transport edge is the whole of
    what the graph holds and the loop closes entirely outside it.
    """
    assert the_run["self_edges"] == [("SC_SRC", "SC_SINK")]


# ----------------------------------------------------------------------
# The refusal
# ----------------------------------------------------------------------


def test_a_rate_commanded_by_its_own_observation_is_refused(the_run):
    """The montage the whole unit exists for, where it used to build."""
    error = the_run["self_error"]

    assert error is not None, (
        "a continuous output gated on the observed value of its own rate "
        "closes an instantaneous loop and must not start"
    )
    assert isinstance(error, muscadet.ContinuousFlowCycleError)
    assert the_run["self_started"] is False


def test_the_refusal_names_the_wiring_the_command_and_the_operand(the_run):
    """A refusal a modeller can act on, as the neighbouring loop errors are."""
    message = str(the_run["self_error"])

    assert message.startswith("Continuous flow graph must be acyclic (R30, R47): ")
    assert "SC_SRC.q_rate_out -> SC_PROBE.q_rate_in" in message
    assert "SC_PROBE.echo_level_out -> SC_SRC.echo_level_in" in message
    assert f"echo < {CRL_THRESHOLD:g}" in message
    # The commanded output, which is what this refusal adds to its siblings.
    # The whole clause, so that dropping the name from the message fails here.
    assert "commands its continuous output q from it" in message
    # The supported alternative, named the way the shipped refusals name it.
    assert "CAPACITY LEVEL" in message


def test_the_refusal_carries_what_it_found(the_run):
    """Inspected directly rather than read out of the message."""
    error = the_run["self_error"]

    assert isinstance(error, CommandedRateLoopError)
    assert isinstance(error, ordering.RateObservationLoopError)
    assert error.reader == "SC_SRC"
    assert error.channel == "echo"
    assert error.flow == "q"
    assert error.producer == "SC_SRC"
    assert error.commanded == "q"


def test_the_zero_hop_loop_is_reported_as_the_cycle_it_is(the_run):
    """Nothing is walked, and the cycle is complete all the same.

    The observation half already proves the whole loop: the rate leaves the
    producer, comes back as a reading, and the output it gates is the very one
    it left. Reporting it needs no discrete connection because there is none.
    """
    error = the_run["self_error"]

    assert error.cycle == ["SC_SRC", "SC_PROBE", "SC_SRC"]
    assert all(
        isinstance(cnct, ordering.ObservationConnection) for cnct in error.connections
    )


def test_a_rate_commanded_through_a_mode_derating_is_refused(the_run):
    """A production condition is not the only way a threshold commands a rate.

    The taint has to cross the mode and land on ``{mode}_derating_{flow}``,
    which is where the continuous family is clamped (R18, KD10). Matching a
    flow's own state basename alone left this whole class of loop building, and
    the discrete family has had the analogous guard on its availability gate
    from the start.
    """
    assert "cut_derating_q" in the_run["derated_signals"]["effects"]
    assert the_run["derated_rates"] == ["q"]

    error = the_run["derated_error"]

    assert error is not None, "a rate a mode cuts on its own observation must not start"
    assert isinstance(error, CommandedRateLoopError)
    assert the_run["derated_started"] is False


# ----------------------------------------------------------------------
# The leaving end of the criterion
# ----------------------------------------------------------------------


def test_a_command_that_feeds_the_observed_producer_is_refused(the_run):
    """One hop rather than zero, and the same loop.

    The governor's own output is not the observed one: it is the supply the
    observed one is made of. Requiring the commanded output to BE the observed
    rate would miss this, which is why the criterion is stated as reaching.
    """
    error = the_run["feeds_error"]

    assert error is not None, (
        "a rate commanded from the observation of what it produces closes an "
        "instantaneous loop and must not start"
    )
    assert isinstance(error, CommandedRateLoopError)
    assert the_run["feeds_started"] is False

    message = str(error)
    assert "FD_SRC.q_rate_out -> FD_GOV.q_rate_in" in message
    assert "FD_GOV.fuel_out -> FD_SRC.fuel_in" in message
    # The cycle closes: the wiring named is the wiring that closes it.
    assert error.cycle == ["FD_SRC", "FD_GOV", "FD_SRC"]


def test_a_command_that_goes_elsewhere_builds(the_run):
    """The near miss the leaving end of the criterion exists for.

    Identical up to one connection: the governor thresholds an observed rate
    and commands a continuous output from it, and that output is delivered to
    somebody who produces no part of what is observed. Refusing this would cost
    more than missing a loop, which is the standing preference of the module.
    """
    assert the_run["elsewhere_rates"] == ["fuel"], (
        "the near miss must differ by where the command goes, not by whether "
        "it is commanded at all"
    )
    assert the_run["elsewhere_error"] is None, str(the_run["elsewhere_error"])
    assert the_run["elsewhere_started"] is True


def test_a_command_on_a_sibling_output_builds(the_run):
    """The commanded output shares the producer, and still reaches nothing.

    Everything the refused montage has, up to the identity of one flow: the
    threshold reads a rate this component delivers, and commands a continuous
    output of this component. What is missing is any way for that command to
    change the rate, a rule's outputs not throttling one another (R-13).
    """
    assert the_run["sibling_rates"] == ["r"], (
        "the near miss must differ by WHICH output is commanded, not by "
        "whether one is"
    )
    assert the_run["sibling_error"] is None, str(the_run["sibling_error"])
    assert the_run["sibling_started"] is True


# ----------------------------------------------------------------------
# The arriving end of the criterion
# ----------------------------------------------------------------------


def test_a_command_no_rule_consumes_builds(the_run):
    """Reaching a component is not closing a loop.

    The producer delivers a declared constant and consumes nothing: whatever
    the governor sends it, the observed rate cannot move. An unqualified "the
    command lands on the producer" test refuses this, which is the outcome this
    module ranks worst.
    """
    assert the_run["unconsumed_error"] is None, str(the_run["unconsumed_error"])
    assert the_run["unconsumed_started"] is True


def test_a_command_feeding_another_rule_set_builds(the_run):
    """The realistic form of the same near miss.

    The command lands on the producer, on an input it genuinely consumes, and
    the observed output is made by a different rule set out of a different
    supply. Two independent sets do not couple.
    """
    assert the_run["othersets_error"] is None, str(the_run["othersets_error"])
    assert the_run["othersets_started"] is True


def test_a_command_a_capacity_integrates_builds(the_run):
    """The state break the whole module treats as the loop breaker (R-14).

    A capacity on the producer's input side integrates what arrives before any
    rule reads it, so the command of this instant cannot move the rate of this
    instant. Refusing this would answer the refusal's own advice -- put a
    volume in the way -- with the volume already in place.
    """
    assert the_run["buffered_broken"] == [("BF_GOV", "BF_SRC", "fuel")], (
        "the near miss rests on the command leg being state-broken; if it is "
        "not, this scenario no longer tests what it says it does"
    )
    assert the_run["buffered_error"] is None, str(the_run["buffered_error"])
    assert the_run["buffered_started"] is True


# ----------------------------------------------------------------------
# What this route does NOT change
# ----------------------------------------------------------------------


def test_the_signal_walk_still_returns_discrete_outputs_only(the_run):
    """The contract of the shipped walk, unmoved.

    What a rule guard or a threshold DRIVES is read by other units and pinned
    by their own tests. This route asks a different question -- what a
    threshold COMMANDS -- and answers it beside them rather than by widening
    them, which is why those tests stay green without a line of change.
    """
    assert the_run["feeds_signal_outputs"] == []
    assert the_run["feeds_rates"] == ["fuel"]


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
