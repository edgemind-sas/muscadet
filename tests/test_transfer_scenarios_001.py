"""The two flows a transfer pair exists for, end to end.

**Environmental exchange** (F1). A wall between a reservoir held at one
potential and a tank at another moves what the gradient says, and the tank
relaxes toward the reservoir. This is the case the whole notion was measured
against: shaped as an identity transfer the exchange conserves but its rate is
a proportion of what arrived; shaped as a source the rate is exact but the
quantity appears from nowhere. A pair is the shape that gets both right, and
the analytic solution of ``dH/dt = K (H_env - H)`` is what says so.

The reversal is where the fixed-direction rule of KD1 becomes visible. A
conduit's direction is its connection's, so a wall pointing INTO the tank
crosses nothing once the tank is the hotter body -- and says so, publishing a
negative request against a zero crossing rather than a plausible number. Losing
heat is a second wall pointing the other way, which is what a formalism with
fixed connection directions gives you.

**Two-stream exchanger** (F2). Two streams of different natures cross one
component and a pair moves a quantity between their balances. One leaves
depleted by exactly what the other leaves enriched by, with no mass crossing
between them.

**The graph claim** (KTD8). A pair's two flows belong to ONE component, so it
adds no edge to a graph whose nodes are components. That is what lets a pair
ride the three existing sweeps instead of needing a band of its own, so it is
asserted rather than assumed.

PyCATSHOO forbids more than one live system per process, so every scenario
lives in the one system below.
"""

import math

import pytest

import cod3s
import muscadet
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    ExchangeContinuous,
    SourceContinuous,
)

# The relaxation, in the lumped form the benchmark literature uses: one
# constituent in a volume of one, so the level IS the potential.
TSC_ENV = 100.0
TSC_INIT = 20.0
TSC_CONDUCTANCE = 0.1
TSC_VOLUME = 500.0
TSC_RESERVOIR = 100.0
TSC_HORIZON = 5.0

#: The two streams of the exchanger, and what the pair moves between them.
TSC_STREAM = 8.0
TSC_MOVED = 2.5


def analytic(time):
    """``H(t)`` of ``dH/dt = K (H_env - H)``, the case F1 is measured against."""
    return TSC_ENV + (TSC_INIT - TSC_ENV) * math.exp(-TSC_CONDUCTANCE * time)


class TscExchanger(muscadet.ObjFlow):
    """Two streams transiting, a pair moving a quantity between their balances."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("water", "air"):
            self.add_flow_continuous_in(name=flow, var_demand_default=TSC_STREAM)
            self.add_flow_continuous_out(name=flow)

        self.add_transfer(
            "exchange",
            flows=["water", "air"],
            equation=muscadet.Transfer(fun=lambda comp: TSC_MOVED, continuous=True),
        )


class TscBareExchanger(muscadet.ObjFlow):
    """The same two streams and no pair: the control F2 is compared against."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("water", "air"):
            self.add_flow_continuous_in(name=flow, var_demand_default=TSC_STREAM)
            self.add_flow_continuous_out(name=flow)


def build_system():
    system = muscadet.System(name="TscSys")

    # -- F1: reservoir, wall, tank. The wall reads the tank's own level and
    #    moves what the gradient says.
    system.add_component(
        name="RESERVOIR", cls="SourceContinuous", flow="heat", rate=TSC_RESERVOIR
    )
    system.add_component(
        name="WALL",
        cls="ExchangeContinuous",
        flow="heat",
        measurements=["tank"],
        conductance=TSC_CONDUCTANCE,
        potential_a={"const": TSC_ENV},
        potential_b={"measurement": "tank"},
    )
    system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        flow="heat",
        ports="in",
        capacity=TSC_VOLUME,
        capacity_name="tank",
        content_init={"heat": TSC_INIT},
        # Declared, and it has to be: a volume claims for ITSELF only what its
        # fill_rate says (R36), and the default of 0 is a pure pass-through
        # buffer. A terminal tank that asks for nothing receives nothing, and
        # the wall's own upstream claim cannot make up for that -- what reaches
        # the tank is still the lesser of production and the tank's demand.
        fill_rate=math.inf,
    )
    system.connect_flow(source="RESERVOIR", target="WALL", flow_name="heat")
    system.connect_flow(source="WALL", target="TANK", flow_name="heat")
    system.connect("TANK", "tank_level_out", "WALL", "tank_level_in")

    # -- F2: two streams, one exchanger, and its pair-free twin.
    for prefix, cls in (("X", "TscExchanger"), ("B", "TscBareExchanger")):
        for flow in ("water", "air"):
            system.add_component(
                name=f"{prefix}SRC_{flow}",
                cls="SourceContinuous",
                flow=flow,
                rate=TSC_STREAM,
            )
            system.add_component(
                name=f"{prefix}SNK_{flow}",
                cls="ConsumerContinuous",
                flow=flow,
                # The enriched stream needs a consumer willing to take the
                # enrichment: a delivery is the lesser of production and
                # demand, so a sink asking for the bare stream would cap the
                # exchanger's own output and hide what the pair moved.
                demand=TSC_STREAM + TSC_MOVED,
            )
        system.add_component(name=f"{prefix}CH", cls=cls)
        for flow in ("water", "air"):
            system.connect_flow(
                source=f"{prefix}SRC_{flow}", target=f"{prefix}CH", flow_name=flow
            )
            system.connect_flow(
                source=f"{prefix}CH", target=f"{prefix}SNK_{flow}", flow_name=flow
            )

    # Nothing here fails or switches, so the session needs a date to step to:
    # without one the solver has no transition to advance the integration on.
    system.comp["RESERVOIR"].add_atm2states(
        name="horizon",
        st1="before",
        st2="after",
        occ_law_12={"cls": "delay", "time": TSC_HORIZON},
        cond_occ_21=False,
    )

    return system


@pytest.fixture(scope="module")
def the_system():
    system = build_system()
    system.isimu_start()
    system.isimu_step_forward()
    yield system


@pytest.fixture(scope="module")
def the_order(the_system):
    from muscadet.ordering import compute_equation_order

    return compute_equation_order(the_system)


def level(system):
    return system.comp["TANK"].capacities["tank"].get_quantity("heat")


def wall_pair(system):
    return system.comp["WALL"].transfers["transfer"]


def out_value(system, comp_name, flow_name):
    return system.comp[comp_name].flows_out[flow_name].var_fed.value()


# ----------------------------------------------------------------------
# F1: environmental exchange
# ----------------------------------------------------------------------


def test_the_tank_relaxes_toward_the_reservoir(the_system):
    """Covers F1. The rate is absolute, not a proportion of what arrived."""
    assert TSC_INIT < level(the_system) < TSC_ENV


def test_the_relaxation_follows_the_analytic_solution(the_system):
    """The measurement that says a pair gets both properties right at once.

    An identity transfer conserves but scales with its supplier's rate; a
    source is exact but debits nobody. Only the pair tracks ``dH/dt = K (H_env
    - H)`` while drawing exactly what it moves.
    """
    expected = analytic(TSC_HORIZON)

    assert level(the_system) == pytest.approx(expected, rel=0.02)


def test_the_wall_draws_exactly_what_it_moves(the_system):
    """Covers AE2 on F1: the reservoir is debited by the quantity delivered."""
    consumption, production = the_system.comp["WALL"].evaluate_production()

    assert consumption["heat"] == pytest.approx(production["heat"])


def test_the_wall_moves_what_the_gradient_says(the_system):
    """The equation reads the tank's live level, so the rate follows the state."""
    pair = wall_pair(the_system)
    expected = TSC_CONDUCTANCE * (TSC_ENV - level(the_system))

    assert pair.last_moved == pytest.approx(expected, rel=0.02)


def test_a_wall_crosses_nothing_once_the_tank_is_the_hotter_body(the_system):
    """Covers F1's reversal half, under the fixed-direction rule of KD1.

    The gradient reverses, the conduit cannot, and the request stays readable:
    a negative ask against a zero crossing, not a plausible number. Losing heat
    is a second wall pointing the other way.
    """
    wall = the_system.comp["WALL"]
    pair = wall.transfers["transfer"]
    original = pair.equation

    pair.equation = muscadet.ConductiveTransfer(
        conductance=TSC_CONDUCTANCE,
        potential_a={"const": 0.0},
        potential_b={"measurement": "tank"},
    )
    try:
        consumption, production = wall.evaluate_production()

        assert pair.last_requested < 0.0
        assert pair.last_moved == pytest.approx(0.0)
        assert production["heat"] == pytest.approx(0.0)
        assert consumption["heat"] == pytest.approx(0.0)
        assert pair.shortfall > 0.0
    finally:
        pair.equation = original


# ----------------------------------------------------------------------
# F2: the two-stream exchanger
# ----------------------------------------------------------------------


def test_one_stream_leaves_depleted_by_what_the_other_gains(the_system):
    """Covers F2 and AE2 on the two-flow shape."""
    water = out_value(the_system, "XCH", "water")
    air = out_value(the_system, "XCH", "air")

    assert water == pytest.approx(TSC_STREAM - TSC_MOVED)
    assert air == pytest.approx(TSC_STREAM + TSC_MOVED)
    assert (TSC_STREAM - water) == pytest.approx(air - TSC_STREAM)


def test_no_mass_crosses_between_the_streams(the_system):
    """The totals are conserved: what left one balance entered the other."""
    paired = out_value(the_system, "XCH", "water") + out_value(the_system, "XCH", "air")
    bare = out_value(the_system, "BCH", "water") + out_value(the_system, "BCH", "air")

    assert paired == pytest.approx(bare)


def test_the_moved_quantity_follows_both_streams(the_system):
    """Covers AE7: the equation divides one quantity by another and tracks both."""
    exchange = the_system.comp["XCH"]
    pair = exchange.transfers["exchange"]
    original = pair.equation

    pair.equation = muscadet.Transfer(
        fun=lambda comp: comp.get_input_delivered("water")
        / max(comp.get_input_delivered("air"), 1e-9)
        * 2.0,
        continuous=True,
    )
    try:
        exchange.evaluate_production()
        first = pair.last_requested

        pair.equation = muscadet.Transfer(
            fun=lambda comp: comp.get_input_delivered("water")
            / max(comp.get_input_delivered("air") * 2.0, 1e-9)
            * 2.0,
            continuous=True,
        )
        exchange.evaluate_production()

        assert pair.last_requested == pytest.approx(first / 2.0)
    finally:
        pair.equation = original


# ----------------------------------------------------------------------
# The graph claim, and the knowledge-base component
# ----------------------------------------------------------------------


def test_a_pair_tears_no_connection(the_order):
    """Covers KTD8. Empty for every acyclic model, and a pair adds no cycle."""
    assert the_order.torn == []


def test_a_pair_adds_no_edge_to_the_graph(the_order):
    """The dependency a pair creates is INSIDE a node, so the graph cannot see it.

    Compared against the pair-free twin exchanger, which sits on the same
    two-source two-sink shape: the same edges around both means the pair
    contributed none of its own.
    """
    graph = the_order.graph

    def around(node):
        incoming = sum(1 for cnct in graph.connections if cnct.target == node)
        outgoing = sum(1 for cnct in graph.connections if cnct.source == node)
        return incoming, outgoing

    assert around("XCH") == around("BCH")

    # And no connection of the graph ever names a pair: a pair's two ends are
    # flows of one component, so there is nothing for an edge to join.
    assert all(cnct.source != cnct.target for cnct in graph.connections)


def test_the_order_respects_the_walls_place_in_the_chain(the_order):
    """A pair-carrying component is ordered like any other node."""
    order = the_order.production_order

    assert order.index("RESERVOIR") < order.index("WALL") < order.index("TANK")


def test_the_kb_component_refuses_a_key_it_does_not_read(the_system):
    with pytest.raises(ValueError, match="does not accept declaration key"):
        the_system.add_component(
            name="BADWALL",
            cls="ExchangeContinuous",
            flow="heat",
            conductance=1.0,
            potential_a=1.0,
            potential_b=0.0,
            viscosity=3.0,
        )


def test_the_kb_component_refuses_two_spellings_at_once(the_system):
    """A component carrying both would silently honour one of them."""
    with pytest.raises(ValueError, match="both a whole 'transfer'"):
        the_system.add_component(
            name="BADWALL2",
            cls="ExchangeContinuous",
            flow="heat",
            transfer=muscadet.Transfer(fun=lambda comp: 1.0, continuous=True),
            conductance=1.0,
            potential_a=1.0,
            potential_b=0.0,
        )


def test_the_kb_component_refuses_no_law_at_all(the_system):
    with pytest.raises(ValueError, match="declares no transfer law"):
        the_system.add_component(name="BADWALL3", cls="ExchangeContinuous", flow="heat")


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
