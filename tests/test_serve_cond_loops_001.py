"""A discharge condition enters the loop detection (R50).

R49 gave a capacity a condition, in the operand vocabulary a production
condition uses. That opened a second place a comparison can live, and
``muscadet.ordering`` knew only the first: both seeds walk the OUTPUT FLOWS of
a component, and a capacity is not a flow.

So the discharge command reopened, on a capacity, exactly the defect the
zero-hop recognition had just closed on an output (R47). Two escapes, and they
are independent:

* **the arrival end.** ``gates_production_on`` lists three ways a discrete
  input can reach a component's production, and a discharge condition is a
  fourth. A signal thresholded on a rate and wired back to command the
  discharge of the volume delivering it closed a loop the walk arrived at and
  was told did not exist;
* **the departure end.** ``compared_continuous_inputs`` and
  ``measurement_thresholds`` seed from rule guards and flow production
  conditions only, so a discharge thresholded on a RATE, transported or
  observed, produced no seed, no walk and no report.

Both are closed by reading the capacity conditions in the same two places, and
by seeding on what a discharge actually drives: the held flows when the volume
sits on the OUT side, and what the rules make of them when it sits on the IN
side, which is the rule a guard's seeds already follow.

**Forbidding the shape outright was considered and refused.** A discharge
commanded on a measure of PRODUCTION is what the H2 showcase does, and it is
legitimate whenever the measure is not the discharge's own output. What is
refused is the loop, not the vocabulary.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, inspected and deleted before the next one starts; the fixture snapshots
what each produced.
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

#: What a commanded volume holds, in a vessel large enough that no scenario
#: reaches either bound.
SCL_VOLUME = 1000.0
SCL_INIT = 500.0

#: The threshold every comparison of this module is declared at, and the
#: demand it stands behind.
SCL_THRESHOLD = 5.0
SCL_DEMAND = 100.0

#: The reserve floor of the sanctioned montage: a LEVEL, integrated, which is
#: what breaks the loop the rate versions close.
SCL_FLOOR = 100.0

#: How far the one montage that is DRIVEN rather than merely built is taken.
SCL_HORIZON = 4.0


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class SclCommandedStore(muscadet.ObjFlow):
    """A volume whose discharge a boolean input commands (R49)."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="supply", logic="and")
        self.add_flow_continuous_out(name="q", var_fed_default=SCL_DEMAND)
        self.add_capacity(
            name="store",
            flow="q",
            side="out",
            capacity=SCL_VOLUME,
            content_init={"q": SCL_INIT},
            serve_cond=["supply"],
        )


class SclGatedStore(muscadet.ObjFlow):
    """The same volume, with the threshold written on the OUTPUT instead.

    The twin the detectors already refuse (R44), built beside the one they did
    not so that the two verdicts are compared rather than asserted apart.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="supply", logic="and")
        self.add_flow_continuous_out(
            name="q", var_fed_default=SCL_DEMAND, var_prod_cond=["supply"]
        )
        self.add_capacity(
            name="store",
            flow="q",
            side="out",
            capacity=SCL_VOLUME,
            content_init={"q": SCL_INIT},
        )


class SclRateGate(muscadet.ObjFlow):
    """Thresholds the rate it receives and publishes a verdict."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=SCL_DEMAND)
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="supply",
                var_prod_cond=[{"name": "q", "op": "<", "value": SCL_THRESHOLD}],
            )
        )


class SclMeteredStore(muscadet.ObjFlow):
    """A volume whose discharge a threshold on its ARRIVING RATE commands.

    The departure-end escape on a transported quantity. What it closes is NOT
    a transport loop: the command and the comparison sit on one component, so
    no edge of the graph runs between them. It closes through the DEMAND
    sweep, ``demand_claim`` capping what the volume asks upstream by what it
    may release (R48), which the command drives.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=SCL_DEMAND)
        self.add_flow_continuous_out(name="q")
        self.add_capacity(
            name="buf",
            flow="q",
            side="in",
            capacity=SCL_VOLUME,
            content_init={"q": SCL_INIT},
            serve_cond=[
                {"name": "q", "port": "in", "op": ">=", "value": SCL_THRESHOLD}
            ],
        )


class SclObservingStore(muscadet.ObjFlow):
    """A volume whose discharge a threshold on an OBSERVED rate commands.

    The reading is the rate this very volume delivers, observed back onto
    itself: the R47 montage, on a capacity instead of an output.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="echo")
        self.add_flow_continuous_out(name="q", var_fed_default=SCL_DEMAND)
        self.add_capacity(
            name="store",
            flow="q",
            side="out",
            capacity=SCL_VOLUME,
            content_init={"q": SCL_INIT},
            serve_cond=[{"name": "echo", "op": "<", "value": SCL_THRESHOLD}],
        )


class SclReserveStore(muscadet.ObjFlow):
    """The sanctioned montage: a reserve floor on an INTEGRATED level."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="reserve")
        self.add_flow_continuous_out(name="q", var_fed_default=SCL_DEMAND)
        self.add_capacity(
            name="store",
            flow="q",
            side="out",
            capacity=SCL_VOLUME,
            content_init={"q": SCL_INIT},
            serve_cond=[{"name": "reserve", "op": ">=", "value": SCL_FLOOR}],
        )


class SclHorizon(muscadet.ObjFlow):
    """A dated transition, so a purely continuous montage can be driven at all."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_atm2states(
            name="horizon",
            occ_law_12={"cls": "delay", "time": SCL_HORIZON},
            cond_occ_21=False,
        )


class SclProductionGate(muscadet.ObjFlow):
    """Observes a delivered rate and publishes a supply order.

    The shape of the H2 showcase's ``AP_BATTERY`` controller: it watches what
    the plant produces and tells the battery to help when it falls short.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="q", kind="rate")
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="supply",
                var_prod_cond=[{"name": "q", "op": "<", "value": SCL_DEMAND / 2}],
            )
        )


class SclObservationGate(muscadet.ObjFlow):
    """Thresholds an OBSERVED rate and drives a DISCRETE signal from it.

    The third refusal class of the family, and the one whose arrival end also
    lands on a discharge condition: the verdict travels as a signal, so the
    walk reaches the volume rather than recognising it at zero hops.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="q", kind="rate")
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="supply",
                var_prod_cond=[{"name": "q", "op": "<", "value": SCL_THRESHOLD}],
            )
        )


class SclSelfGatedStore(muscadet.ObjFlow):
    """A discharge commanded by a reading of the rate it itself serves.

    The tightest loop the vocabulary can express: the condition names the
    OUTPUT the discharge feeds, so nothing is transported and nothing is
    observed. It crosses no connection at all, which is why no walk saw it.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=SCL_DEMAND)
        self.add_capacity(
            name="store",
            flow="q",
            side="out",
            capacity=SCL_VOLUME,
            content_init={"q": SCL_INIT},
            serve_cond=[
                {"name": "q", "port": "out", "op": "<", "value": SCL_THRESHOLD}
            ],
        )


class SclRelayStore(muscadet.ObjFlow):
    """A commanded volume whose output another condition then thresholds.

    The capacity as a HOP: the signal arrives, commands the discharge, and the
    discrete output thresholding what that discharge delivers carries it on.
    A fixpoint blind to the middle hop stops here.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="supply", logic="and")
        self.add_flow_continuous_out(name="p", var_fed_default=SCL_DEMAND)
        self.add_capacity(
            name="buf",
            flow="p",
            side="out",
            capacity=SCL_VOLUME,
            content_init={"p": SCL_INIT},
            serve_cond=["supply"],
        )
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="supply",
                var_prod_cond=[{"name": "p", "port": "out", "op": "<", "value": 1.0}],
            )
        )


class SclNamesakeStore(muscadet.ObjFlow):
    """One name on both sides, in DIFFERENT families.

    A continuous input ``q`` buffered on the way in, and an unrelated DISCRETE
    status output also called ``q``. The identity transfer needs both sides
    continuous, so the discharge drives nothing here and refusing would be the
    expensive error.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="sig", logic="and")
        self.add_flow_continuous_in(name="q", var_demand_default=SCL_DEMAND)
        self.add_flow(dict(cls="FlowDiscreteOut", name="q", var_prod_default=True))
        self.add_capacity(
            name="buf",
            flow="q",
            side="in",
            capacity=SCL_VOLUME,
            content_init={"q": SCL_INIT},
            serve_cond=["sig"],
        )


class SclInertStore(muscadet.ObjFlow):
    """A commanded volume whose released quantity reaches nothing.

    It holds a flow on the way in that no rule consumes and that is no output
    of its own, so what the command lets out leaves by no route at all. Naming
    the signal is therefore not enough to say production depends on it.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="supply", logic="and")
        self.add_flow_continuous_in(name="q", var_demand_default=SCL_DEMAND)
        self.add_flow_continuous_out(name="p", var_fed_default=SCL_DEMAND)
        self.add_capacity(
            name="sink",
            flow="q",
            side="in",
            capacity=SCL_VOLUME,
            content_init={"q": SCL_INIT},
            serve_cond=["supply"],
        )


class SclProbe(muscadet.ObjFlow):
    """Reads a level and republishes it: the instrument of the sanctioned shape."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="store")
        self.add_measurement_out(name="reserve", source="store")


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


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


def run_arrival_scenarios(obs):
    """A signal thresholded on a rate, commanding the volume that delivers it.

    Built twice, gated on the CAPACITY and on the OUTPUT, so the two verdicts
    are compared. The output version is what the shipped detectors refuse.
    """
    for cls, prefix in (("SclCommandedStore", "capgate"), ("SclGatedStore", "outgate")):
        system = muscadet.System(name=f"SclArrival{prefix}")
        try:
            system.add_component(name="BAT", cls=cls)
            system.add_component(name="GATE", cls="SclRateGate")

            system.connect_flow(source="BAT", target="GATE", flow_name="q")
            system.connect_flow(source="GATE", target="BAT", flow_name="supply")

            obs[f"{prefix}_gates"] = ordering.gates_production_on(
                system.comp["BAT"], "supply"
            )

            start_and_record(system, obs, prefix)
        finally:
            system.deleteSys()


def run_transported_threshold_scenario(obs):
    """A discharge thresholded on a rate it RECEIVES, commanding its producer."""
    system = muscadet.System(name="SclTransported")
    try:
        system.add_component(
            name="SRC", cls="SourceContinuous", flow="q", rate=SCL_DEMAND
        )
        system.add_component(name="BUF", cls="SclMeteredStore")
        system.add_component(
            name="LOAD", cls="ConsumerContinuous", flow="q", demand=SCL_DEMAND
        )
        system.add_component(name="H", cls="SclHorizon")

        system.connect_flow(source="SRC", target="BUF", flow_name="q")
        system.connect_flow(source="BUF", target="LOAD", flow_name="q")

        obs["transported_compared"] = ordering.compared_continuous_inputs(
            system.comp["BUF"]
        )
        obs["transported_driven"] = ordering.comparison_driven_outputs(
            system.comp["BUF"], "q"
        )
        obs["transported_seeds"] = ordering.commanded_discharge_outputs(
            system.comp["BUF"], system.comp["BUF"].capacities["buf"]
        )

        start_and_record(system, obs, "transported")

        if obs["transported_started"]:
            system.isimu_start()
            for _ in range(3):
                system.isimu_step_forward()
                if system.currentTime() >= SCL_HORIZON:
                    break
            buf = system.comp["BUF"]
            obs["transported_in"] = buf.flows_in["q"].var_fed.value()
            obs["transported_out"] = buf.flows_out["q"].var_fed.value()
            obs["transported_holds"] = buf.capacities["buf"].serve_holds()
            system.isimu_stop()
    finally:
        system.deleteSys()


def run_observed_threshold_scenario(obs):
    """A discharge thresholded on the rate it delivers, observed back."""
    system = muscadet.System(name="SclObserved")
    try:
        system.add_component(name="BAT", cls="SclObservingStore")
        system.add_component(
            name="LOAD", cls="ConsumerContinuous", flow="q", demand=SCL_DEMAND
        )
        system.add_component(
            name="PROBE",
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

        system.connect_flow(source="BAT", target="LOAD", flow_name="q")
        system.connect("BAT", "q_rate_out", "PROBE", "q_rate_in")
        system.connect("PROBE", "echo_level_out", "BAT", "echo_level_in")

        comp = system.comp["BAT"]

        obs["observed_thresholds"] = ordering.measurement_thresholds(comp, "echo")
        obs["observed_rates"] = ordering.measurement_driven_rates(comp, "echo")

        start_and_record(system, obs, "observed")
    finally:
        system.deleteSys()


def run_reserve_floor_scenario(obs):
    """The sanctioned montage: the same topology, over an INTEGRATED level."""
    system = muscadet.System(name="SclReserve")
    try:
        system.add_component(name="BAT", cls="SclReserveStore")
        system.add_component(name="BMS", cls="SclProbe")
        system.add_component(
            name="LOAD", cls="ConsumerContinuous", flow="q", demand=SCL_DEMAND
        )

        system.connect_flow(source="BAT", target="LOAD", flow_name="q")
        system.connect("BAT", "store_level_out", "BMS", "store_level_in")
        system.connect("BMS", "reserve_level_out", "BAT", "reserve_level_in")

        start_and_record(system, obs, "reserve")
    finally:
        system.deleteSys()


def run_observation_gate_scenario(obs):
    """An observation loop whose ARRIVAL end is a discharge condition.

    The verdict leaves as a discrete signal, so this is the R43 class rather
    than the zero-hop one, and its message has to name the volume too. All
    three refusals of the family reach a discharge condition, and naming the
    capacity on two of the three would be the kind of hole that survives.
    """
    system = muscadet.System(name="SclObservationGate")
    try:
        system.add_component(name="BAT", cls="SclCommandedStore")
        system.add_component(
            name="LOAD", cls="ConsumerContinuous", flow="q", demand=SCL_DEMAND
        )
        system.add_component(name="SENS", cls="SclObservationGate")

        system.connect_flow(source="BAT", target="LOAD", flow_name="q")
        system.connect("BAT", "q_rate_out", "SENS", "q_rate_in")
        system.connect_flow(source="SENS", target="BAT", flow_name="supply")

        start_and_record(system, obs, "obsgate")
    finally:
        system.deleteSys()


def run_self_gated_scenario(obs):
    """The loop that crosses no connection at all."""
    system = muscadet.System(name="SclSelfGated")
    try:
        system.add_component(name="BAT", cls="SclSelfGatedStore")
        system.add_component(
            name="LOAD", cls="ConsumerContinuous", flow="q", demand=SCL_DEMAND
        )
        system.connect_flow(source="BAT", target="LOAD", flow_name="q")

        start_and_record(system, obs, "selfgated")
    finally:
        system.deleteSys()


def run_relay_scenario(obs):
    """A signal RELAYED through a discharge, on to a threshold on its output."""
    system = muscadet.System(name="SclRelay")
    try:
        system.add_component(name="SRC", cls="SclCommandedStore")
        system.add_component(name="SENS", cls="SclRateGate")
        system.add_component(name="MID", cls="SclRelayStore")
        system.add_component(
            name="PSINK", cls="ConsumerContinuous", flow="p", demand=SCL_DEMAND
        )

        system.connect_flow(source="SRC", target="SENS", flow_name="q")
        system.connect_flow(source="SENS", target="MID", flow_name="supply")
        system.connect_flow(source="MID", target="PSINK", flow_name="p")
        system.connect_flow(source="MID", target="SRC", flow_name="supply")

        obs["relay_hop"] = ordering.inbound_driven_outputs(system.comp["MID"], "supply")

        start_and_record(system, obs, "relay")
    finally:
        system.deleteSys()


def run_namesake_scenario(obs):
    """One name on both sides in different families: nothing is driven."""
    system = muscadet.System(name="SclNamesake")
    try:
        system.add_component(name="MIX", cls="SclNamesakeStore")

        obs["namesake_drives"] = ordering.commanded_discharge_outputs(
            system.comp["MIX"], system.comp["MIX"].capacities["buf"]
        )
        obs["namesake_gates"] = ordering.gates_production_on(system.comp["MIX"], "sig")
    finally:
        system.deleteSys()


def run_tear_scenario(obs):
    """A volume whose discharge condition reads the flow it buffers.

    ``capacity_breaks_inbound`` used to answer True on the sole ground that a
    capacity held the flow, so the edge was TORN and a genuinely algebraic loop
    got an evaluation order instead of a refusal.
    """
    system = muscadet.System(name="SclTear")
    try:
        system.add_component(
            name="SRC", cls="SourceContinuous", flow="q", rate=SCL_DEMAND
        )
        system.add_component(name="BUF", cls="SclMeteredStore")
        system.add_component(
            name="LOAD", cls="ConsumerContinuous", flow="q", demand=SCL_DEMAND
        )
        system.connect_flow(source="SRC", target="BUF", flow_name="q")
        system.connect_flow(source="BUF", target="LOAD", flow_name="q")

        obs["tear_breaks"] = ordering.capacity_breaks_inbound(system.comp["BUF"], "q")
        obs["tear_broken_edges"] = [
            (cnct.source, cnct.target, cnct.flow)
            for cnct in ordering.build_continuous_flow_graph(
                system
            ).state_broken_connections
        ]
    finally:
        system.deleteSys()


def run_showcase_shape_scenario(obs):
    """The H2 showcase's own shape: a battery helping a plant on one bus.

    Two SIBLING producers feeding one consumer, with the order derived from
    what the first delivers and commanding the second. It must build, and this
    is the montage the ticket's acceptance rests on: commanding a discharge
    from a measure of production is legitimate.
    """
    system = muscadet.System(name="SclShowcase")
    try:
        system.add_component(
            name="PLANT", cls="SourceContinuous", flow="q", rate=SCL_DEMAND
        )
        system.add_component(name="BAT", cls="SclCommandedStore")
        system.add_component(
            name="BUS", cls="ConsumerContinuous", flow="q", demand=2 * SCL_DEMAND
        )
        system.add_component(name="CTRL", cls="SclProductionGate")

        system.connect_flow(source="PLANT", target="BUS", flow_name="q")
        system.connect_flow(source="BAT", target="BUS", flow_name="q")
        system.connect("PLANT", "q_rate_out", "CTRL", "q_rate_in")
        system.connect_flow(source="CTRL", target="BAT", flow_name="supply")

        graph = ordering.build_continuous_flow_graph(system)

        obs["showcase_edges"] = graph.edges
        obs["showcase_ancestors"] = sorted(graph.ancestors("PLANT"))
        obs["showcase_gates"] = ordering.gates_production_on(
            system.comp["BAT"], "supply"
        )

        start_and_record(system, obs, "showcase")
    finally:
        system.deleteSys()


def run_inert_volume_scenario(obs):
    """A commanded volume releasing into neither an output nor a rule."""
    system = muscadet.System(name="SclInert")
    try:
        system.add_component(name="ST", cls="SclInertStore")
        capacity = system.comp["ST"].capacities["sink"]

        obs["inert_drives"] = ordering.commanded_discharge_outputs(
            system.comp["ST"], capacity
        )
        obs["inert_gates"] = ordering.gates_production_on(system.comp["ST"], "supply")
    finally:
        system.deleteSys()


def run_command_elsewhere_scenario(obs):
    """The near miss: the commanded volume produces no part of what is read."""
    system = muscadet.System(name="SclElsewhere")
    try:
        system.add_component(
            name="SRC", cls="SourceContinuous", flow="q", rate=SCL_DEMAND
        )
        system.add_component(name="GATE", cls="SclRateGate")
        system.add_component(name="BAT", cls="SclCommandedStore")
        system.add_component(
            name="LOAD", cls="ConsumerContinuous", flow="q", demand=SCL_DEMAND
        )

        # Two chains and one signal: the gate watches what SRC delivers and
        # commands BAT, which delivers into a load of its own. BAT produces no
        # part of what the threshold reads, so nothing closes.
        system.connect_flow(source="SRC", target="GATE", flow_name="q")
        system.connect_flow(source="GATE", target="BAT", flow_name="supply")
        system.connect_flow(source="BAT", target="LOAD", flow_name="q")

        obs["elsewhere_gates"] = ordering.gates_production_on(
            system.comp["BAT"], "supply"
        )

        start_and_record(system, obs, "elsewhere")

        obs["system"] = system
    except Exception:  # pragma: no cover - the fixture would hide the cause
        system.deleteSys()
        raise


@pytest.fixture(scope="module")
def the_run():
    """Every scenario, built, inspected and deleted in turn."""
    obs = {}

    run_arrival_scenarios(obs)
    run_transported_threshold_scenario(obs)
    run_observed_threshold_scenario(obs)
    run_reserve_floor_scenario(obs)
    run_observation_gate_scenario(obs)
    run_self_gated_scenario(obs)
    run_relay_scenario(obs)
    run_namesake_scenario(obs)
    run_tear_scenario(obs)
    run_showcase_shape_scenario(obs)
    run_inert_volume_scenario(obs)
    run_command_elsewhere_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# The arrival end
# ----------------------------------------------------------------------


def test_a_discharge_command_is_a_way_production_depends_on_a_signal(the_run):
    """``gates_production_on`` knew three ways, and a discharge is a fourth."""
    assert the_run["capgate_gates"] is True
    assert the_run["outgate_gates"] is True


def test_the_two_spellings_of_one_montage_get_the_same_verdict(the_run):
    """The whole point, stated as a comparison rather than as two assertions.

    The same physics written on a capacity and on an output must be refused
    alike. It was not: the output version raised and the capacity version
    built, so a modeller moving the gate from one to the other lost the
    diagnostic without changing the model.
    """
    assert the_run["outgate_started"] is False
    assert the_run["capgate_started"] is False

    assert isinstance(the_run["outgate_error"], muscadet.ContinuousFlowCycleError)
    assert isinstance(the_run["capgate_error"], muscadet.ContinuousFlowCycleError)


def test_the_refusal_names_the_wiring_the_capacity_and_the_operand(the_run):
    """A refusal a modeller can act on, as the neighbouring loop errors are.

    Naming the component is not enough: one component may carry several
    volumes and the condition lives on ONE of them, so without the name the
    modeller is told which component closes the loop and left to find the
    declaration.
    """
    message = str(the_run["capgate_error"])

    assert "BAT.q_out -> GATE.q_in" in message
    assert "GATE.supply_out -> BAT.supply_in" in message
    assert f"q < {SCL_THRESHOLD:g}" in message
    assert "capacity 'store'" in message
    assert "serve_cond" in message
    assert the_run["capgate_error"].capacities == ["store"]

    # The same, on the other end of the route: the observed montage names the
    # volume its command is declared on.
    observed = str(the_run["observed_error"])

    assert "capacity 'store'" in observed
    assert the_run["observed_error"].capacities == ["store"]


def test_every_refusal_of_the_family_names_the_capacity(the_run):
    """Three refusal classes reach a discharge condition, and all three say so.

    The zero-hop one recognises the command without walking; this one walks a
    discrete signal to it; the transported one walks it over the flow graph.
    Naming the volume on two of the three is the kind of hole that survives a
    reading, so the third is asserted here rather than assumed.
    """
    error = the_run["obsgate_error"]

    assert (
        error is not None
    ), "an observed rate commanding its own producer must not start"
    assert isinstance(error, ordering.RateObservationLoopError)
    assert not isinstance(
        error, muscadet.CommandedRateLoopError
    ), "the verdict leaves as a SIGNAL here, so this is the walked class"
    assert error.capacities == ["store"]
    assert "capacity 'store'" in str(error)


def test_a_loop_closed_without_a_capacity_reads_as_it_always_did(the_run):
    """The clause is ADDED to a message, never woven into it.

    The output-gated twin closes through a production condition, so no volume
    carries the command and the refusal is byte-for-byte the one 4.x shipped.
    """
    assert the_run["outgate_error"].capacities == []
    assert "serve_cond" not in str(the_run["outgate_error"])


# ----------------------------------------------------------------------
# The departure end
# ----------------------------------------------------------------------


def test_a_discharge_thresholded_on_a_transported_rate_is_seen_by_the_seed(the_run):
    """The seed reads a capacity's condition, which is this unit's contribution.

    ``compared_continuous_inputs`` walked the output flows only, so a
    comparison living on a capacity produced no entry at all: whatever the walk
    could have concluded, it was never started. It is now seeded, and
    ``commanded_discharge_outputs`` answers what that command drives -- here
    the identity transfer the volume sits on, a rule-less pass-through having
    no rule set to seed from.
    """
    assert the_run["transported_compared"] == {"q": f"q >= {SCL_THRESHOLD:g}"}
    assert the_run["transported_seeds"] == {"q_fed_out"}


def test_a_command_reading_the_rate_it_meters_is_not_a_transport_loop(the_run):
    """And what the walk concludes on it is: nothing, correctly.

    The comparison and the command sit on ONE component, so no edge of the
    graph runs between them and ``signal_driven_outputs`` has no discrete
    channel to leave by. What closes here closes through the DEMAND sweep --
    ``demand_claim`` caps what the volume asks upstream by what it may release
    (R48), and the command drives that -- which is the fixpoint R-14 records as
    a demand-sweep design decision and deliberately leaves open.

    Measured: it settles self-consistently rather than chattering, the arriving
    rate standing well clear of the threshold. The hazard the shape carries is
    that a threshold BETWEEN the two regimes admits two fixpoints, and the
    solver keeps whichever the first evaluation reached. That is worth knowing
    and is not a loop any detector here can express.
    """
    assert (
        the_run["transported_driven"] == []
    ), "nothing leaves this component as a followable signal"
    assert the_run["transported_started"] is True
    assert the_run["transported_in"] == pytest.approx(SCL_DEMAND)
    assert the_run["transported_out"] == pytest.approx(SCL_DEMAND)
    assert the_run["transported_holds"] is True


def test_a_discharge_thresholded_on_an_observed_rate_is_refused(the_run):
    """And the seed that reads the measurement channels, likewise.

    Zero hops: what the threshold commands is the very output whose rate it
    reads, so there is nothing to walk and the observation path has already
    proved the loop (R47).
    """
    assert the_run["observed_thresholds"] == [f"echo < {SCL_THRESHOLD:g}"]
    assert the_run["observed_rates"] == ["q"]

    assert the_run["observed_started"] is False
    assert isinstance(the_run["observed_error"], muscadet.CommandedRateLoopError)


# ----------------------------------------------------------------------
# What must keep building
# ----------------------------------------------------------------------


def test_a_discharge_reading_the_rate_it_serves_is_refused(the_run):
    """The tightest loop the vocabulary can express, and it crosses no wire.

    Its two siblings both need a route: one reaches the rate through an
    observation link, the other through transport. Here the condition names the
    OUTPUT the discharge feeds, so the loop closes inside one component. Both
    seeds were structurally blind to it -- one filters on the INPUTS, the other
    needs a channel -- and the model built, its discharge chattering at the
    period of the integration step.
    """
    error = the_run["selfgated_error"]

    assert error is not None, "a discharge gated on the rate it serves must not start"
    assert isinstance(error, muscadet.CommandedRateSelfLoopError)
    assert the_run["selfgated_started"] is False

    message = str(error)
    assert "capacity 'store'" in message
    assert f"q < {SCL_THRESHOLD:g}" in message
    assert "no connection at all" in message
    assert error.connections == [], "there is no wiring: that is the point"


def test_a_signal_relayed_through_a_discharge_travels_on(the_run):
    """A capacity is a HOP of the taint fixpoint, not only a seed and a gate.

    The commit that taught the two SEEDS and the GATE about a discharge left
    the fixpoint alone, so a signal commanding a volume whose output another
    condition then thresholds stopped there: the walk reached the component,
    found nothing driven, and dropped a loop that closes one hop further on.
    """
    assert the_run["relay_hop"] == [
        "supply"
    ], "the signal must reach the discrete output through the discharge"
    assert the_run["relay_started"] is False
    assert isinstance(the_run["relay_error"], muscadet.ContinuousFlowCycleError)


def test_a_discharge_condition_on_the_buffered_flow_unbreaks_the_tear(the_run):
    """R-14's tear rests on the level standing between arrival and departure.

    A condition reading the arriving flow makes what LEAVES depend on what
    ARRIVES within the instant, so the level no longer stands between them.
    Tearing the edge anyway hands a genuinely algebraic loop an evaluation
    order instead of a refusal.
    """
    assert the_run["tear_breaks"] is False
    assert the_run["tear_broken_edges"] == []


def test_a_namesake_in_the_other_family_drives_nothing(the_run):
    """The identity transfer needs BOTH sides continuous.

    A component may carry one name on both sides in different families. Seeding
    a discrete status output because a continuous input of the same name is
    buffered makes the walk follow a signal with nothing to do with the
    discharge, and refuse a model that closes nothing.
    """
    assert the_run["namesake_drives"] == set()
    assert the_run["namesake_gates"] is False


def test_a_reserve_floor_on_an_integrated_level_builds(the_run):
    """The sanctioned montage, and the reason the shape is not forbidden.

    Same topology as the refused ones, with a LEVEL where the rate was. A
    level is carried between instants, so it breaks the loop; a detector that
    refused this would have understood nothing.
    """
    assert the_run["reserve_error"] is None, str(the_run["reserve_error"])
    assert the_run["reserve_started"] is True


def test_a_command_reaching_an_unrelated_volume_builds(the_run):
    """Commanding a discharge on a measure of production is legitimate.

    It is what the H2 showcase does. What closes a loop is the volume
    producing part of what the threshold reads, not the vocabulary, so
    forbidding a discharge condition to read a rate was refused as a remedy.
    """
    assert the_run["elsewhere_gates"] is True, (
        "the near miss must differ by the wiring, not by whether the signal "
        "reaches the production at all"
    )
    assert the_run["elsewhere_error"] is None, str(the_run["elsewhere_error"])
    assert the_run["elsewhere_started"] is True


def test_the_showcase_shape_of_two_siblings_on_one_bus_builds(the_run):
    """What the acceptance rests on, measured on the shape it names.

    A battery helping a plant on one bus, ordered by a threshold on what the
    plant delivers. The two producers are SIBLINGS: the battery is not an
    ancestor of the plant, so the order does not reach what the threshold
    reads and nothing closes.

    This is also the boundary of the route, and it is the one R43 already
    records: sibling coupling runs sideways through a shared demand and
    traverses no edge, so neither detector sees it. Widening ``ancestors`` to
    catch it would newly refuse the ordinary main-plus-backup shape, which is
    exactly this one.
    """
    assert the_run["showcase_edges"] == [("PLANT", "BUS"), ("BAT", "BUS")]
    assert the_run["showcase_ancestors"] == ["PLANT"], (
        "the battery must not be an ancestor of the plant, or this measures "
        "a different montage"
    )
    assert the_run["showcase_gates"] is True, (
        "the order does reach the battery's production; what saves it is where "
        "that production goes"
    )
    assert the_run["showcase_error"] is None, str(the_run["showcase_error"])
    assert the_run["showcase_started"] is True


def test_a_volume_releasing_into_nothing_gates_no_production(the_run):
    """Naming the signal is not enough: what is released has to LEAVE.

    The two ends of this route ask one question. A volume holding a flow that
    no rule consumes and that is no output releases into nothing, so its
    command reaches no production and answering otherwise would refuse a model
    that closes nothing -- the expensive error in this module.
    """
    assert the_run["inert_drives"] == set()
    assert the_run["inert_gates"] is False


def test_delete(the_run):
    """Closes the session WHATEVER happened above.

    The last scenario leaves its system alive for the tests to read, so this
    is what deletes it; guarded because a scenario that raised earlier never
    put it there, and a ``KeyError`` here would leave the session open. The
    suite runs near a PyCATSHOO ceiling (``tests/conftest.py``), where a leaked
    engine reference makes UNRELATED later modules fail with an argument-less
    exception naming nothing.
    """
    system = the_run.get("system")

    try:
        if system is not None:
            system.deleteSys()
    finally:
        cod3s.terminate_session()
