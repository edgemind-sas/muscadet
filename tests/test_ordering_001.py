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
    assert methods == ["compute_capacities", "compute_demand", "compute_production"]


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


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
