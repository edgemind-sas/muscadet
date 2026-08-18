"""Automatic equation ordering: derived from the graph, cycle-free, reproducible.

A model author never writes an equation order down (R8), so the whole sequence
has to come from the connection graph -- read back from the engine, sorted
twice, and handed out as distinct increasing integers. Three properties carry
the rest of the continuous layer and are the easiest to lose silently:

* a continuous-flow cycle is refused at the **first run** (R30, AE17), before
  any equation is evaluated, with an error naming the connections that close it;
* measurement links and the discrete control flows built on them are *not*
  continuous flows, so the sensor pattern of AE18 starts;
* the derived sequence is a function of the graph and of declaration order, not
  of hash order, of equation names or of the run entry point.

The production and demand sweeps themselves land in later units, so the
components below carry **stub** equation methods: ``compute_demand`` and
``compute_production`` do nothing but record that they ran. That is enough to
observe the sequence the engine actually applies.

PyCATSHOO forbids more than one live system per process, so each scenario below
is built, driven and deleted before the next one starts; the fixture snapshots
what each produced.
"""

import Pycatshoo as pyc
import cod3s
import muscadet
import pytest

from muscadet import ordering

#: Recording of every stub equation call, in the order the engine made them.
TRACE = []

CLOCK_DELAY = 5.0

#: The rate a gated source exports while its control port is unfed.
RATE_LOOP_RATE = 10.0

#: The threshold a rate comparison closing a loop is declared at.
RATE_LOOP_THRESHOLD = 5.0

#: The two edges of the deadband variant, declared the way a sensor declares
#: its band. A source at 10 cut to 0 crosses BOTH in one jump, which is why a
#: band damps nothing here.
RATE_LOOP_ACTIVATE = 8.0
RATE_LOOP_RELEASE = 3.0

#: What a gate asks for: enough not to throttle what it watches.
RATE_LOOP_DEMAND = 1e6


def simu_params():
    """A fresh, minimal batch-run parameter set."""
    return {"nb_runs": 1, "schedule": [{"start": 0, "end": 1, "nvalues": 2}]}


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class Node(muscadet.ObjFlow):
    """Continuous pass-through: one input and one output named ``q``.

    Declares no equation order of any kind -- that is the point of R8.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="q", var_fed_default=1.0)


class TracedNode(Node):
    """Same, plus the two stub sweep equations and a level to integrate.

    The level exists only so the solver has an ODE system to advance: without
    one there is nothing for the equation methods to be called *for*.
    """

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)
        self.v_level = self.addVariable("level", pyc.TVarType.t_double, 0.0)

    def compute_demand(self):
        TRACE.append(("demand", self.basename()))

    def compute_production(self):
        TRACE.append(("production", self.basename()))
        self.v_level.setDvdtODE(1.0)


class BufferedNode(TracedNode):
    """A traced node buffering its input, so a capacity equation joins the mix."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_capacity(name="buf", flow="q", capacity=100.0, side="in")


class ContinuousSource(muscadet.ObjFlow):
    """Continuous producer whose production is gated by a discrete control flow."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=1.0)
        self.add_flow_in(name="gate", logic="or")


class BufferedSink(muscadet.ObjFlow):
    """Continuous consumer holding a capacity and publishing its level (R33)."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_capacity(name="buf", flow="q", capacity=100.0, side="in")


class LevelSensor(muscadet.ObjFlow):
    """Reads a capacity level and drives a discrete control flow (AE18).

    Carries no continuous flow at all: the measurement link exchanges no
    quantity, and the control flow it drives is discrete.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="buf")
        self.add_flow_out(name="gate", var_prod_default=True)


class DiscreteNode(muscadet.ObjFlow):
    """Discrete-only pass-through, produced unconditionally."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="sig", logic="or")
        self.add_flow_out(name="sig", var_prod_default=True)


class MixedNode(DiscreteNode):
    """Carries continuous flows AND a discrete flow with its availability channel."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="q", var_fed_default=1.0)


# -- The loop the continuous graph does not carry ----------------------


class OrdGatedSource(muscadet.ObjFlow):
    """Produces its rate while ``run`` is UNFED, nothing once it is fed.

    The producing half of the loop: what it exports THIS instant is a function
    of the control port it reads this instant, with no state in between.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=RATE_LOOP_RATE)
        self.add_flow_in(name="run", logic="and")
        self.add_rules(
            name="q_control",
            rules=[
                dict(name="idle", cond="run", prod={"q": 0.0}),
                dict(name="supply", cond="not run", prod={"q": RATE_LOOP_RATE}),
            ],
        )


class OrdSpareSource(muscadet.ObjFlow):
    """Gated on a control port too, but on one no comparison decides."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=RATE_LOOP_RATE)
        self.add_flow_in(name="spare", logic="and")
        self.add_rules(
            name="q_control",
            rules=[
                dict(name="idle", cond="spare", prod={"q": 0.0}),
                dict(name="supply", cond="not spare", prod={"q": RATE_LOOP_RATE}),
            ],
        )


class OrdOpenSource(muscadet.ObjFlow):
    """The same ports, but nothing reads the control one.

    A discrete input no rule guard and no mode watches cannot change what this
    component produces, so a signal arriving here closes nothing.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=RATE_LOOP_RATE)
        self.add_flow_in(name="run", logic="and")


class OrdRateGate(muscadet.ObjFlow):
    """A discrete output thresholded on the CONTINUOUS RATE it receives."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=RATE_LOOP_DEMAND)
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="run",
                var_prod_cond=[{"name": "q", "op": ">=", "value": RATE_LOOP_THRESHOLD}],
            )
        )


class OrdRateGateBand(muscadet.ObjFlow):
    """The same gate, carrying a DEADBAND: activates at 8, releases below 3.

    Declared exactly as the shipped sensor declares its band -- two edge
    outputs and a mode clamping the availability of the port actually wired
    out -- but over a rate rather than over a capacity level.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=RATE_LOOP_DEMAND)

        for suffix, op, value in (
            ("activate", ">=", RATE_LOOP_ACTIVATE),
            ("release", "<", RATE_LOOP_RELEASE),
        ):
            self.add_flow(
                dict(
                    cls="FlowDiscreteOut",
                    name=f"run_{suffix}",
                    var_prod_cond=[{"name": "q", "op": op, "value": value}],
                )
            )

        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="run",
                var_prod_default=True,
                var_fed_available_out_init=False,
            )
        )

    def set_flows(self, **kwargs):
        super().set_flows(**kwargs)
        self.add_atm2states(
            name="run_band",
            st1="released",
            st2="activated",
            init_st2=False,
            cond_occ_12="run_activate_fed_out",
            cond_occ_21="run_release_fed_out",
            effects_12=[(r"^run_fed_available_out$", True)],
            effects_21=[(r"^run_fed_available_out$", False)],
        )


class OrdBypassGate(OrdRateGate):
    """Thresholds the rate, and ALSO carries a signal derived from nothing.

    Discrete traffic between two components that exchange a continuous flow is
    not by itself a loop: ``spare`` is produced unconditionally, so wiring it
    back to the producer -- which does gate its production on it -- feeds that
    producer nothing the comparison decided.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="spare", var_prod_default=True)


class OrdSignalSink(muscadet.ObjFlow):
    """Somewhere downstream for a control signal to end up."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="run", logic="or")


class OrdLevelGate(muscadet.ObjFlow):
    """Receives the rate, but thresholds the LEVEL of a tank it observes.

    The discriminator itself, wired into the refused shape: this component IS
    the target of a continuous edge from the producer it gates, and it does
    drive that producer's control port from a comparison. Only one thing
    differs from the refused model -- what the comparison reads. A level is
    integrated, so the loop closes around a state and settles; a rate is not.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=RATE_LOOP_DEMAND)
        self.add_measurement_in(name="buf")
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="run",
                var_prod_cond=[
                    {"name": "buf", "op": ">=", "value": RATE_LOOP_THRESHOLD}
                ],
            )
        )


class OrdLevelSensor(muscadet.ObjFlow):
    """The sanctioned pattern: the comparison reads a capacity LEVEL (F4, AE18).

    Same topology as the refused ones -- it gates the very component filling
    the capacity it observes -- and it must keep building, because a level is
    integrated state and integrated state is what breaks a loop.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="buf")
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="run",
                var_prod_cond=[
                    {"name": "buf", "op": ">=", "value": RATE_LOOP_THRESHOLD}
                ],
            )
        )


# ----------------------------------------------------------------------
# System
# ----------------------------------------------------------------------


class OrderingSystem(muscadet.System):
    """Registers the stub levels, then defers to the derived ordering.

    Only the ODE variables are registered here: the equation *orders* are the
    thing under test and must come from the graph, never from this class.
    """

    def prerun_step(self):
        for comp in self.comp.values():
            level = getattr(comp, "v_level", None)
            if level is not None:
                self.pdmp_add_ode_variable(level)
        return super().prerun_step()


def build_chain(system_name, names, cls="TracedNode", buffered=None):
    """A linear chain ``names[0] -> names[1] -> ...`` of continuous components.

    ``buffered`` names the one component that also holds a capacity, so that a
    capacity equation joins the allocation.
    """
    system = OrderingSystem(name=system_name)
    for name in names:
        comp_cls = "BufferedNode" if name == buffered else cls
        system.add_component(name=name, cls=comp_cls)
    for source, target in zip(names, names[1:]):
        system.auto_connect(source, target)
    return system


def add_clock(comp):
    """Give an interactive session a date to step to."""
    comp.add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": CLOCK_DELAY},
        cond_occ_21=False,
    )


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_cycle_scenario(obs):
    """AE17: three components whose continuous flows form a loop."""
    system = OrderingSystem(name="OrderingCycle")
    for name in ["CYC_A", "CYC_B", "CYC_C"]:
        system.add_component(name=name, cls="Node")
    system.auto_connect("CYC_A", "CYC_B")
    system.auto_connect("CYC_B", "CYC_C")
    system.auto_connect("CYC_C", "CYC_A")

    obs["cycle_error"] = None
    try:
        system.isimu_start()
    except ordering.ContinuousFlowCycleError as err:
        obs["cycle_error"] = err

    obs["cycle_started"] = system.prerun_done and obs["cycle_error"] is None
    obs["cycle_order"] = system.equation_order
    obs["cycle_registrations"] = list(system.equation_registrations)

    system.deleteSys()


def run_sensor_scenario(obs):
    """AE18: a sensor reads a capacity and gates its own supplier."""
    system = OrderingSystem(name="OrderingSensor")
    system.add_component(name="SRC", cls="ContinuousSource")
    system.add_component(name="TANK", cls="BufferedSink")
    system.add_component(name="SENSOR", cls="LevelSensor")

    # Continuous data: the only thing the graph is allowed to see.
    system.auto_connect("SRC", "TANK")
    # Measurement link: read-only, carries no quantity (R33).
    system.connect("TANK", "buf_level_out", "SENSOR", "buf_level_in")
    # Discrete control flow back to the supplier: closes the loop for a human
    # reader, but not for the continuous-flow graph.
    system.auto_connect("SENSOR", "SRC")

    obs["sensor_error"] = None
    try:
        system.isimu_start()
        system.isimu_stop()
    except Exception as err:  # pragma: no cover - a failure is the assertion
        obs["sensor_error"] = err

    order = system.equation_order
    obs["sensor_nodes"] = list(order.graph.nodes)
    obs["sensor_edges"] = list(order.graph.edges)
    obs["sensor_production"] = list(order.production_order)

    system.deleteSys()


def run_interactive_chain(obs):
    """A four-component chain, driven on the interactive path."""
    TRACE.clear()

    system = build_chain(
        "OrderingChainInteractive",
        ["N1", "N2", "N3", "N4"],
        buffered="N2",
    )
    add_clock(system.comp["N1"])

    system.isimu_start()
    order = system.equation_order
    obs["chain_demand"] = list(order.demand_order)
    obs["chain_production"] = list(order.production_order)
    obs["chain_sweep_registrations"] = list(order.registrations)
    obs["chain_registrations"] = list(system.equation_registrations)

    TRACE.clear()
    system.isimu_step_forward()
    obs["chain_trace"] = list(TRACE[:8])
    system.isimu_stop()

    system.deleteSys()


def run_batch_chain(obs):
    """The same four-component chain, driven on the batch path."""
    system = build_chain(
        "OrderingChainBatch",
        ["N1", "N2", "N3", "N4"],
        buffered="N2",
    )
    add_clock(system.comp["N1"])

    system.simulate(simu_params())

    order = system.equation_order
    obs["batch_demand"] = list(order.demand_order)
    obs["batch_production"] = list(order.production_order)
    obs["batch_sweep_registrations"] = list(order.registrations)

    system.deleteSys()


def run_branch_scenario(obs):
    """Two independent branches, declared against alphabetical order."""
    system = OrderingSystem(name="OrderingBranches")
    # Declaration order is deliberately NOT alphabetical: an order derived from
    # names or from hash order would come out as ALPHA, ZED.
    system.add_component(name="ZED", cls="TracedNode")
    system.add_component(name="ALPHA", cls="TracedNode")
    system.add_component(name="SINK", cls="TracedNode")
    system.auto_connect("ZED", "SINK")
    system.auto_connect("ALPHA", "SINK")

    system.prerun()
    order = system.equation_order
    obs["branch_nodes"] = list(order.graph.nodes)
    obs["branch_demand"] = list(order.demand_order)
    obs["branch_production"] = list(order.production_order)

    system.deleteSys()


def run_growth_scenario(obs):
    """The same component classes, in a two- then a three-component model."""
    small = build_chain("OrderingSmall", ["P1", "P2"])
    small.prerun()
    obs["small_production"] = list(small.equation_order.production_order)
    obs["small_orders"] = dict(small.equation_order.orders)
    small.deleteSys()

    grown = build_chain("OrderingGrown", ["P0", "P1", "P2"])
    grown.prerun()
    obs["grown_production"] = list(grown.equation_order.production_order)
    obs["grown_orders"] = dict(grown.equation_order.orders)
    grown.deleteSys()


def run_discrete_cycle_scenario(obs):
    """A discrete-only loop: no continuous flow, therefore no graph at all."""
    system = muscadet.System(name="OrderingDiscreteCycle")
    for name in ["D1", "D2", "D3"]:
        system.add_component(name=name, cls="DiscreteNode")
    system.auto_connect("D1", "D2")
    system.auto_connect("D2", "D3")
    system.auto_connect("D3", "D1")

    obs["discrete_error"] = None
    try:
        system.isimu_start()
        system.isimu_stop()
    except Exception as err:  # pragma: no cover - a failure is the assertion
        obs["discrete_error"] = err

    order = system.equation_order
    obs["discrete_nodes"] = list(order.graph.nodes)
    obs["discrete_registrations"] = list(system.equation_registrations)
    obs["discrete_manager"] = system.pdmp_manager

    system.deleteSys()


def run_rate_loop_scenario(obs, key, gate_cls, system_name):
    """A rate comparison wired back to the component producing that rate.

    The continuous graph is a single edge and perfectly acyclic; the loop
    closes through the discrete control port, which the graph never carries.
    Run to the very same point as the cycle scenario -- the FIRST run, before
    any equation is evaluated.
    """
    system = OrderingSystem(name=system_name)

    system.add_component(name="RL_SRC", cls="OrdGatedSource")
    system.add_component(name="RL_GATE", cls=gate_cls)

    system.connect_flow(source="RL_SRC", target="RL_GATE", flow_name="q")
    system.connect_flow(source="RL_GATE", target="RL_SRC", flow_name="run")

    obs[f"{key}_error"] = None
    try:
        system.isimu_start()
        system.isimu_stop()
    except ordering.ContinuousFlowCycleError as err:
        obs[f"{key}_error"] = err

    obs[f"{key}_started"] = system.prerun_done and obs[f"{key}_error"] is None

    system.deleteSys()


def build_allowed_loop_system():
    """Every shape the rate-comparison check must NOT refuse, in one system.

    Refusing a legitimate model is worse than missing a loop, so each of these
    is a near miss of the refused shape: same components, same continuous edge,
    and one thing different.
    """
    system = OrderingSystem(name="OrderingRateLoopAllowed")

    # -- The sanctioned pattern (F4, AE18): the comparison reads a LEVEL.
    system.add_component(name="OK_SRC", cls="OrdGatedSource")
    system.add_component(name="OK_TANK", cls="BufferedSink")
    system.add_component(name="OK_SENS", cls="OrdLevelSensor")
    system.connect_flow(source="OK_SRC", target="OK_TANK", flow_name="q")
    system.connect("OK_TANK", "buf_level_out", "OK_SENS", "buf_level_in")
    system.connect_flow(source="OK_SENS", target="OK_SRC", flow_name="run")

    # -- The same shape as the refused one, thresholding a LEVEL instead: the
    #    gate sits on the continuous edge AND drives its producer's control.
    system.add_component(name="LG_SRC", cls="OrdGatedSource")
    system.add_component(name="LG_TANK", cls="BufferedSink")
    system.add_component(name="LG_GATE", cls="OrdLevelGate")
    system.connect_flow(source="LG_SRC", target="LG_TANK", flow_name="q")
    system.connect_flow(source="LG_SRC", target="LG_GATE", flow_name="q")
    system.connect("LG_TANK", "buf_level_out", "LG_GATE", "buf_level_in")
    system.connect_flow(source="LG_GATE", target="LG_SRC", flow_name="run")

    # -- The comparison drives a signal that travels DOWNSTREAM only.
    system.add_component(name="DOWN_SRC", cls="OrdGatedSource")
    system.add_component(name="DOWN_GATE", cls="OrdRateGate")
    system.add_component(name="DOWN_SINK", cls="OrdSignalSink")
    system.connect_flow(source="DOWN_SRC", target="DOWN_GATE", flow_name="q")
    system.connect_flow(source="DOWN_GATE", target="DOWN_SINK", flow_name="run")

    # -- A signal DOES travel back upstream to a port the producer gates on,
    #    but it derives from no comparison.
    system.add_component(name="BY_SRC", cls="OrdSpareSource")
    system.add_component(name="BY_GATE", cls="OrdBypassGate")
    system.add_component(name="BY_SINK", cls="OrdSignalSink")
    system.connect_flow(source="BY_SRC", target="BY_GATE", flow_name="q")
    system.connect_flow(source="BY_GATE", target="BY_SINK", flow_name="run")
    system.connect_flow(source="BY_GATE", target="BY_SRC", flow_name="spare")

    # -- The comparison DOES reach the producer, which does not read it.
    system.add_component(name="OPEN_SRC", cls="OrdOpenSource")
    system.add_component(name="OPEN_GATE", cls="OrdRateGate")
    system.connect_flow(source="OPEN_SRC", target="OPEN_GATE", flow_name="q")
    system.connect_flow(source="OPEN_GATE", target="OPEN_SRC", flow_name="run")

    return system


def run_allowed_loop_scenario(obs):
    """None of the near misses above may be refused."""
    system = build_allowed_loop_system()

    obs["allowed_error"] = None
    try:
        system.isimu_start()
        system.isimu_stop()
    except Exception as err:  # pragma: no cover - a failure is the assertion
        obs["allowed_error"] = err

    order = system.equation_order

    obs["allowed_started"] = system.prerun_done
    obs["allowed_edges"] = [] if order is None else list(order.graph.edges)

    system.deleteSys()


def build_non_data_channel_system():
    """Continuous components wired by everything EXCEPT continuous data.

    ``M1 -> M2`` is the one continuous data connection. Everything else is a
    channel the graph must ignore: an availability connection back from ``M2``,
    and a discrete loop between ``M3`` and ``M4`` -- both of which carry
    continuous flows, so neither is skipped for being "not a continuous
    component".
    """
    system = OrderingSystem(name="OrderingChannels")
    for name in ["M1", "M2", "M3", "M4"]:
        system.add_component(name=name, cls="MixedNode")

    # The only continuous data connection.
    system.connect("M1", "q_out", "M2", "q_in")

    # Availability channel of a discrete flow, wired the other way round: an
    # edge here would close a loop and refuse a valid model.
    system.connect_flow(
        source="M2",
        target="M1",
        flow_name="sig_available",
        flow_key="sig",
    )

    # Discrete data channels, wired as a loop.
    system.connect("M3", "sig_out", "M4", "sig_in")
    system.connect("M4", "sig_out", "M3", "sig_in")

    return system


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_cycle_scenario(obs)
    run_sensor_scenario(obs)
    run_interactive_chain(obs)
    run_batch_chain(obs)
    run_branch_scenario(obs)
    run_growth_scenario(obs)
    run_discrete_cycle_scenario(obs)
    run_rate_loop_scenario(obs, "rate_loop", "OrdRateGate", "OrderingRateLoop")
    run_rate_loop_scenario(obs, "rate_band", "OrdRateGateBand", "OrderingRateLoopBand")
    run_allowed_loop_scenario(obs)

    # Kept alive for the teardown test, per the module convention.
    system = build_non_data_channel_system()
    obs["channels_error"] = None
    try:
        system.prerun()
    except Exception as err:  # pragma: no cover - a failure is the assertion
        obs["channels_error"] = err
    order = system.equation_order
    obs["channels_nodes"] = list(order.graph.nodes)
    obs["channels_edges"] = list(order.graph.edges)
    obs["channels_connections"] = [str(cnct) for cnct in order.graph.connections]

    obs["system"] = system
    return obs


# ----------------------------------------------------------------------
# R30 / AE17 -- cycles are refused at the first run
# ----------------------------------------------------------------------


def test_a_continuous_cycle_fails_at_the_first_run(the_run):
    """AE17: the run never starts, and the failure is a model error."""
    error = the_run["cycle_error"]

    assert error is not None, "a cyclic model must not start"
    assert isinstance(error, ordering.ContinuousFlowCycleError)
    assert isinstance(error, ValueError)
    assert the_run["cycle_started"] is False


def test_the_cycle_error_names_the_closing_connections(the_run):
    """AE17: the message names the connections, not just the components.

    A message naming only the components would leave a modeller hunting for
    which of several flows between them closed the loop.
    """
    message = str(the_run["cycle_error"])

    assert "CYC_A.q_out -> CYC_B.q_in" in message
    assert "CYC_B.q_out -> CYC_C.q_in" in message
    assert "CYC_C.q_out -> CYC_A.q_in" in message

    connections = {str(cnct) for cnct in the_run["cycle_error"].connections}
    assert connections == {
        "CYC_A.q_out -> CYC_B.q_in",
        "CYC_B.q_out -> CYC_C.q_in",
        "CYC_C.q_out -> CYC_A.q_in",
    }
    assert set(the_run["cycle_error"].cycle) == {"CYC_A", "CYC_B", "CYC_C"}


def test_no_equation_is_registered_when_the_graph_is_cyclic(the_run):
    """The refusal happens before any equation is evaluated -- or registered."""
    assert the_run["cycle_order"] is None
    assert the_run["cycle_registrations"] == []


def test_the_cycle_error_is_catchable_from_the_package_root(the_run):
    """R-7: a documented model error must not require importing a module the
    public API never mentions.

    ``muscadet.ContinuousFlowCycleError`` is the type a consumer catches; the
    rate-comparison refusal is a subclass of it, so one ``except`` covers both
    shapes of first-run refusal.
    """
    assert muscadet.ContinuousFlowCycleError is ordering.ContinuousFlowCycleError
    assert muscadet.RateComparisonLoopError is ordering.RateComparisonLoopError
    assert issubclass(
        muscadet.RateComparisonLoopError, muscadet.ContinuousFlowCycleError
    )

    # The error the run actually raised is caught by the root-level name, with
    # its structured attributes reachable through it.
    error = the_run["cycle_error"]

    try:
        raise error
    except muscadet.ContinuousFlowCycleError as caught:
        assert caught.cycle
        assert caught.connections
        assert caught.path
        assert caught.wiring


def test_the_engine_name_index_skips_a_foreign_entry():
    """R-7: ``system.comp`` is a public dict, and the walk resolves defensively.

    The graph walk used to call ``comp.name()`` unconditionally on every entry
    while the very next lines resolved the same objects with ``.get()`` and
    ``getattr``. An entry carrying no ``name()`` is skipped now, so a foreign
    object parked in the dict cannot abort the whole system's pre-run check.
    """
    index = ordering.engine_name_index(
        {
            "REAL": _NamedStub("engine_real"),
            "FOREIGN": object(),
            "ALSO_FOREIGN": {"name": "not callable"},
        }
    )

    assert index == {"engine_real": "REAL"}

    # First declaration wins for a duplicated engine name, which cannot be
    # told apart from the message boxes' side.
    duplicated = ordering.engine_name_index(
        {"FIRST": _NamedStub("same"), "SECOND": _NamedStub("same")}
    )

    assert duplicated == {"same": "FIRST"}


class _NamedStub:
    """The only thing ``engine_name_index`` asks of an entry."""

    def __init__(self, engine_name):
        self._engine_name = engine_name

    def name(self):
        return self._engine_name


# ----------------------------------------------------------------------
# R30 / AE18 -- measurement links and control flows are not continuous flows
# ----------------------------------------------------------------------


def test_the_sensor_pattern_starts(the_run):
    """AE18: reading a capacity and gating its own supplier is not a cycle."""
    assert the_run["sensor_error"] is None


def test_the_sensor_contributes_no_node_and_no_edge(the_run):
    """The measurement link and the discrete gate stay out of the graph.

    The sensor carries no continuous flow, so it is not even a node; the only
    edge is the continuous data connection feeding the tank.
    """
    assert the_run["sensor_nodes"] == ["SRC", "TANK"]
    assert the_run["sensor_edges"] == [("SRC", "TANK")]
    assert the_run["sensor_production"] == ["SRC", "TANK"]


# ----------------------------------------------------------------------
# R8 -- the derived sequences
# ----------------------------------------------------------------------


def test_a_chain_orders_demand_and_production_as_exact_reverses(the_run):
    """Demand climbs the chain, production descends it."""
    assert the_run["chain_production"] == ["N1", "N2", "N3", "N4"]
    assert the_run["chain_demand"] == ["N4", "N3", "N2", "N1"]
    assert the_run["chain_demand"] == list(reversed(the_run["chain_production"]))


def test_the_engine_evaluates_the_sweeps_in_the_derived_order(the_run):
    """Both sweeps are sequenced ACROSS components, not only within each one.

    Every component registers on the same PDMP manager, so the whole demand
    sweep must run before the whole production sweep -- an order that only
    holds because each equation got its own integer.
    """
    assert the_run["chain_trace"] == [
        ("demand", "N4"),
        ("demand", "N3"),
        ("demand", "N2"),
        ("demand", "N1"),
        ("production", "N1"),
        ("production", "N2"),
        ("production", "N3"),
        ("production", "N4"),
    ]


def test_every_equation_receives_a_distinct_order_integer(the_run):
    """KTD3: ties would make the sequence a function of names, not of the graph.

    Asserted over EVERY equation on the system, capacity equations included --
    they are registered at declaration time, from a different band, and must
    still be distinct from the sweeps.
    """
    registrations = the_run["chain_registrations"]
    orders = [reg.order for reg in registrations]

    assert len(orders) == len(set(orders))

    methods = sorted({reg.method for reg in registrations})
    assert methods == [
        "compute_capability",
        "compute_capacities",
        "compute_demand",
        "compute_production",
    ]


def test_the_sweeps_are_ordered_before_the_capacity_equations(the_run):
    """Capacity levels integrate last, after both sweeps."""
    registrations = the_run["chain_registrations"]

    sweeps = [r.order for r in registrations if r.method != "compute_capacities"]
    capacities = [r.order for r in registrations if r.method == "compute_capacities"]

    assert capacities == [ordering.CAPACITY_ORDER_BASE]
    assert max(sweeps) < min(capacities)


def test_the_sweep_orders_are_increasing_along_each_sequence(the_run):
    """The integers are what the engine sorts on, so they must follow the sort."""
    registrations = the_run["chain_sweep_registrations"]

    demand = [r for r in registrations if r.method == "compute_demand"]
    production = [r for r in registrations if r.method == "compute_production"]

    assert [r.comp for r in demand] == ["N4", "N3", "N2", "N1"]
    assert [r.comp for r in production] == ["N1", "N2", "N3", "N4"]
    assert [r.order for r in demand] == sorted(r.order for r in demand)
    assert [r.order for r in production] == sorted(r.order for r in production)
    assert max(r.order for r in demand) < min(r.order for r in production)


def test_the_same_model_run_twice_yields_the_same_order(the_run):
    """Reproducibility: two independent builds of one model agree exactly.

    Two separate builds, in two separate engine systems, with fresh Python
    objects throughout -- so this catches an order that leaked in from set
    iteration or hash order rather than from the graph.
    """
    assert the_run["batch_production"] == the_run["chain_production"]
    assert the_run["batch_demand"] == the_run["chain_demand"]
    assert the_run["batch_sweep_registrations"] == the_run["chain_sweep_registrations"]


def test_the_order_does_not_depend_on_the_run_entry_point(the_run):
    """The batch and the interactive path go through the same pre-run step.

    The two run entry points do not converge in ``cod3s``, so an ordering wired
    into only one of them would leave the other running with no order at all.
    """
    assert the_run["batch_production"] == the_run["chain_production"]
    assert the_run["batch_demand"] == the_run["chain_demand"]
    assert [
        (r.comp, r.method, r.order) for r in the_run["batch_sweep_registrations"]
    ] == [(r.comp, r.method, r.order) for r in the_run["chain_sweep_registrations"]]


def test_independent_branches_follow_declaration_order(the_run):
    """Ties are broken by declaration order, never by name or hash order.

    ``ZED`` is declared before ``ALPHA`` precisely so that an alphabetical or
    hash-driven tie-break would show up as the opposite sequence.
    """
    assert the_run["branch_nodes"] == ["ZED", "ALPHA", "SINK"]
    assert the_run["branch_production"] == ["ZED", "ALPHA", "SINK"]
    assert the_run["branch_demand"] == ["SINK", "ZED", "ALPHA"]


def test_adding_a_component_changes_no_other_declaration(the_run):
    """R8: growing a model never forces a renumbering upstream.

    ``P1`` and ``P2`` are the very same component class in both models and
    declare nothing about ordering; inserting ``P0`` upstream re-derives the
    whole sequence and leaves their relative order untouched.
    """
    assert the_run["small_production"] == ["P1", "P2"]
    assert the_run["grown_production"] == ["P0", "P1", "P2"]

    # Neither component class carries an order of any kind.
    for attr in ("equation_order", "order", "compute_order"):
        assert not hasattr(Node, attr)

    # The integers moved -- because they are derived, not declared -- while the
    # relative order of the pre-existing pair did not.
    small = the_run["small_orders"]
    grown = the_run["grown_orders"]

    assert small[("P1", "compute_production")] < small[("P2", "compute_production")]
    assert grown[("P1", "compute_production")] < grown[("P2", "compute_production")]
    assert grown[("P0", "compute_production")] < grown[("P1", "compute_production")]


# ----------------------------------------------------------------------
# What the graph must NOT see
# ----------------------------------------------------------------------


def test_a_discrete_only_cycle_runs(the_run):
    """No continuous flow, no graph, no check -- and no PDMP manager either."""
    assert the_run["discrete_error"] is None
    assert the_run["discrete_nodes"] == []
    assert the_run["discrete_registrations"] == []
    assert the_run["discrete_manager"] is None


def test_an_availability_connection_adds_no_edge(the_run):
    """The availability channel of a discrete flow is not a data channel.

    ``M2`` publishes availability back to ``M1``, which already feeds it: an
    edge here would close a loop and refuse a perfectly valid model.
    """
    assert the_run["channels_error"] is None
    assert the_run["channels_edges"] == [("M1", "M2")]
    assert the_run["channels_connections"] == ["M1.q_out -> M2.q_in"]


def test_a_discrete_connection_between_continuous_components_adds_no_edge(the_run):
    """``M3`` and ``M4`` carry continuous flows and are wired discretely in a loop.

    They are graph *nodes* -- they declare continuous flows -- but their only
    connection is discrete, so the graph holds no edge between them.
    """
    assert the_run["channels_nodes"] == ["M1", "M2", "M3", "M4"]
    assert ("M3", "M4") not in the_run["channels_edges"]
    assert ("M4", "M3") not in the_run["channels_edges"]


# ----------------------------------------------------------------------
# The filtering predicate itself
# ----------------------------------------------------------------------


def test_the_data_channel_predicate_accepts_only_continuous_data_boxes(the_run):
    """Every other channel a component exports resolves to None.

    The predicate is what the acyclicity check rests on: a miss here turns a
    valid model into a first-run error.
    """
    system = the_run["system"]
    comp = system.comp["M1"]

    assert ordering.continuous_data_channel(comp, "q_out", "out") == "q"
    assert ordering.continuous_data_channel(comp, "q_in", "in") == "q"

    # Discrete data channel, availability channel, wrong direction, unknown box.
    assert ordering.continuous_data_channel(comp, "sig_out", "out") is None
    assert ordering.continuous_data_channel(comp, "sig_available_out", "out") is None
    assert ordering.continuous_data_channel(comp, "sig_available_in", "in") is None
    assert ordering.continuous_data_channel(comp, "q_out", "in") is None
    assert ordering.continuous_data_channel(comp, "buf_level_out", "out") is None
    assert ordering.continuous_data_channel(comp, "nope", "out") is None


# ----------------------------------------------------------------------
# R30 -- the loop the continuous graph does not carry
# ----------------------------------------------------------------------


def test_a_rate_comparison_wired_back_upstream_fails_at_the_first_run(the_run):
    """The continuous graph is acyclic and the model is still refused.

    ``RL_GATE`` compares the rate it receives and drives ``RL_SRC``'s control
    port with the result, so the two production regimes select each other
    inside one instant. Left to run, that model does not diverge: it flips
    regime every 6.25e-4 of simulated time and never finishes, which is worse
    than being refused.
    """
    error = the_run["rate_loop_error"]

    assert error is not None, "a rate comparison closing a loop must not start"
    assert isinstance(error, ordering.RateComparisonLoopError)
    assert isinstance(error, ordering.ContinuousFlowCycleError)
    assert isinstance(error, ValueError)
    assert the_run["rate_loop_started"] is False


def test_the_rate_loop_error_names_the_connections_and_the_way_out(the_run):
    """It names both closing connections, the comparison, and the alternative."""
    error = the_run["rate_loop_error"]
    message = str(error)

    assert "RL_SRC.q_out -> RL_GATE.q_in" in message
    assert "RL_GATE.run_out -> RL_SRC.run_in" in message

    # The comparison itself, so the modeller knows which operand to move
    assert "q >= 5" in message

    # ... and where to move it to: the sanctioned pattern, named
    assert "CAPACITY LEVEL" in message
    assert "measurement link" in message

    assert error.reader == "RL_GATE"
    assert error.flow == "q"
    assert error.cycle == ["RL_SRC", "RL_GATE", "RL_SRC"]


def test_a_deadband_over_a_rate_does_not_lift_the_refusal(the_run):
    """A band damps a value that moves through it; a rate JUMPS across it.

    Measured on this very model: a source at 10 cut to 0 by its own guard
    crosses the activation edge at 8 and the release edge at 3 in one step, so
    the band is never inhabited and the flip dates are the same with it as
    without it. The refusal therefore does not depend on the band.

    The band is also what makes the check non-trivial: the port actually wired
    out carries no comparison at all -- it is a mode reading the two edge
    outputs that clamps it -- so the signal has to be followed through that
    mode.
    """
    error = the_run["rate_band_error"]

    assert error is not None, "a deadband over a rate must not lift the refusal"
    assert isinstance(error, ordering.RateComparisonLoopError)
    assert the_run["rate_band_started"] is False

    message = str(error)
    assert "RL_GATE.run_out -> RL_SRC.run_in" in message
    assert "deadband does not damp it" in message


def test_the_near_misses_of_that_shape_all_build(the_run):
    """Four loops that are not loops, all refused would be worse than the bug.

    * ``OK_*``  -- the sanctioned pattern (F4, AE18): a sensor carrying no
      continuous flow at all reads a capacity LEVEL and gates its supplier;
    * ``LG_*``  -- the same shape as the refused one, one thing different: the
      gate sits ON the continuous edge and drives the producer's control port,
      but what it compares is a LEVEL, which is integrated state;
    * ``DOWN_*`` -- the signal the comparison drives travels downstream only;
    * ``BY_*``  -- a signal does return to the producer, on a port that
      producer gates on, but it is produced unconditionally and carries
      nothing the comparison decided;
    * ``OPEN_*`` -- the comparison does reach the producer, whose production
      reads nothing of it.
    """
    assert the_run["allowed_error"] is None, str(the_run["allowed_error"])
    assert the_run["allowed_started"] is True

    # The continuous graph is exactly the supply edges, unchanged by any of the
    # discrete traffic wired over them.
    assert the_run["allowed_edges"] == [
        ("OK_SRC", "OK_TANK"),
        ("LG_SRC", "LG_TANK"),
        ("LG_SRC", "LG_GATE"),
        ("DOWN_SRC", "DOWN_GATE"),
        ("BY_SRC", "BY_GATE"),
        ("OPEN_SRC", "OPEN_GATE"),
    ]


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
