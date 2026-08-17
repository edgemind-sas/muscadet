"""A transfer pair moves its computed quantity, and the two shapes differ.

The production sweep is where a pair actually moves something, and the two
shapes it can take do NOT share a path:

* a **conduit** names one flow twice and meters that flow's transit. It
  replaced the flow's identity transfer, so it draws from the input side like
  a rule does, spends against the shared per-input budget, and what crosses the
  component is the computed quantity;
* a **two-flow pair** moves a quantity between two streams that keep
  transiting. Both keep their identity transfer and the pair applies a signed
  delta on top -- one production falls by exactly what the other rises.

Conflating them breaks whichever shape loses. Remove a two-flow pair's streams
from the identity transfer and the exchanger stops carrying anything; leave a
conduit's flow in it and the stream crosses twice, once by transfer and once by
the pair. Both directions are asserted below.

Pairs run LAST in the sweep, after the rule sets, the identity transfer and the
source defaults. Last for two reasons: a two-flow pair adjusts a production
every earlier contributor may have written, so it needs the map complete; and
on a contested input the pair is then the one that saturates, which is the
state it has a shortfall channel for and a rule set has not.

PyCATSHOO forbids more than one live system per process, so every chain lives
in the one system below.
"""

import pytest

import cod3s
import muscadet

#: A date the interactive session can always step to.
TPR_CLOCK = 5.0

#: What every source declares, and what every consumer asks for. Deliberately
#: larger than any transfer below, so a saturating case has to be built on
#: purpose rather than arrived at by accident.
TPR_SUPPLY = 10.0
TPR_DEMAND = 10.0


def fixed(value):
    """A declared equation returning a constant."""
    return muscadet.Transfer(fun=lambda comp: value, continuous=True)


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class TprSource(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name=kwargs.get("flow", "x"),
            var_fed_default=kwargs.get("rate", TPR_SUPPLY),
        )


class TprSink(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "x"),
            var_demand_default=kwargs.get("demand", TPR_DEMAND),
        )


class TprConduit(muscadet.ObjFlow):
    """One flow in, the same out, and a pair metering what crosses."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="x", var_demand_default=TPR_DEMAND)
        self.add_flow_continuous_out(name="x")
        self.add_transfer("meter", flows=["x", "x"], equation=fixed(kwargs["rate"]))


class TprPlainPipe(muscadet.ObjFlow):
    """The same shape with no pair: the control for what a conduit replaces."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="x", var_demand_default=TPR_DEMAND)
        self.add_flow_continuous_out(name="x")


class TprExchanger(muscadet.ObjFlow):
    """Two streams transiting, and a pair moving a quantity between them."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("a", "b"):
            self.add_flow_continuous_in(name=flow, var_demand_default=TPR_DEMAND)
            self.add_flow_continuous_out(name=flow)

        for index, rate in enumerate(kwargs["rates"]):
            self.add_transfer(f"swap{index}", flows=["a", "b"], equation=fixed(rate))


# ----------------------------------------------------------------------
# The one system every chain lives in
# ----------------------------------------------------------------------


def chain(system, prefix, conduit_rate):
    """A source, a conduit metering ``conduit_rate``, and a sink."""
    system.add_component(name=f"SRC_{prefix}", cls="TprSource")
    system.add_component(name=f"CND_{prefix}", cls="TprConduit", rate=conduit_rate)
    system.add_component(name=f"SNK_{prefix}", cls="TprSink")
    system.connect_flow(source=f"SRC_{prefix}", target=f"CND_{prefix}", flow_name="x")
    system.connect_flow(source=f"CND_{prefix}", target=f"SNK_{prefix}", flow_name="x")


def exchanger(system, prefix, rates):
    """Two sources, an exchanger carrying ``rates`` worth of pairs, two sinks."""
    system.add_component(name=f"XSA_{prefix}", cls="TprSource", flow="a")
    system.add_component(name=f"XSB_{prefix}", cls="TprSource", flow="b")
    system.add_component(name=f"XCH_{prefix}", cls="TprExchanger", rates=rates)
    system.add_component(name=f"XKA_{prefix}", cls="TprSink", flow="a")
    system.add_component(name=f"XKB_{prefix}", cls="TprSink", flow="b")
    system.connect_flow(source=f"XSA_{prefix}", target=f"XCH_{prefix}", flow_name="a")
    system.connect_flow(source=f"XSB_{prefix}", target=f"XCH_{prefix}", flow_name="b")
    system.connect_flow(source=f"XCH_{prefix}", target=f"XKA_{prefix}", flow_name="a")
    system.connect_flow(source=f"XCH_{prefix}", target=f"XKB_{prefix}", flow_name="b")


def build_system():
    system = muscadet.System(name="TprSys")

    # A conduit that fits inside its supply, and the same shape with no pair.
    chain(system, "OK", 3.0)
    system.add_component(name="SRC_PLAIN", cls="TprSource")
    system.add_component(name="CND_PLAIN", cls="TprPlainPipe")
    system.add_component(name="SNK_PLAIN", cls="TprSink")
    system.connect_flow(source="SRC_PLAIN", target="CND_PLAIN", flow_name="x")
    system.connect_flow(source="CND_PLAIN", target="SNK_PLAIN", flow_name="x")

    # A conduit asking for more than its supply can give.
    system.add_component(name="SRC_SAT", cls="TprSource", rate=1.0)
    system.add_component(name="CND_SAT", cls="TprConduit", rate=5.0)
    system.add_component(name="SNK_SAT", cls="TprSink")
    system.connect_flow(source="SRC_SAT", target="CND_SAT", flow_name="x")
    system.connect_flow(source="CND_SAT", target="SNK_SAT", flow_name="x")

    # Two-flow pairs: one positive, one negative, one pair of them summing.
    exchanger(system, "POS", [2.0])
    exchanger(system, "NEG", [-2.0])
    exchanger(system, "SUM", [1.0, 2.0])
    exchanger(system, "ZERO", [0.0])

    system.add_component(name="CLOCK", cls="TprSource", flow="tick")
    system.comp["CLOCK"].add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": TPR_CLOCK},
        cond_occ_21=False,
    )

    return system


@pytest.fixture(scope="module")
def the_system():
    system = build_system()
    system.isimu_start()
    system.isimu_step_forward()
    yield system


def maps(system, comp_name):
    """The ``(consumption, production)`` this component computes right now."""
    return system.comp[comp_name].evaluate_production()


def received(system, comp_name, flow_name):
    return system.comp[comp_name].flows_in[flow_name].get_delivered()


def delivered(system, comp_name, flow_name):
    return system.comp[comp_name].flows_out[flow_name].var_fed.value()


# ----------------------------------------------------------------------
# The conduit: what crosses IS the computed quantity
# ----------------------------------------------------------------------


def test_a_conduit_moves_its_computed_quantity(the_system):
    consumption, production = maps(the_system, "CND_OK")

    assert consumption["x"] == pytest.approx(3.0)
    assert production["x"] == pytest.approx(3.0)


def test_a_conduit_meters_rather_than_passing_through(the_system):
    """The control: the same chain without a pair carries the whole supply."""
    assert delivered(the_system, "CND_PLAIN", "x") == pytest.approx(TPR_SUPPLY)
    assert delivered(the_system, "CND_OK", "x") == pytest.approx(3.0)


def test_the_conduits_flow_is_not_also_transferred(the_system):
    """Left in the residue it would cross twice, once by transfer once by pair."""
    conduit = the_system.comp["CND_OK"]
    plain = the_system.comp["CND_PLAIN"]

    assert conduit.get_identity_transfer_flows() == []
    assert plain.get_identity_transfer_flows() == ["x"]


def test_the_metered_quantity_reaches_the_consumer(the_system):
    assert received(the_system, "SNK_OK", "x") == pytest.approx(3.0)


# ----------------------------------------------------------------------
# The two-flow pair: a delta on two streams that keep transiting
# ----------------------------------------------------------------------


def test_both_streams_still_transit(the_system):
    """Remove them from the residue and the exchanger stops carrying anything."""
    exchange = the_system.comp["XCH_ZERO"]

    assert sorted(exchange.get_identity_transfer_flows()) == ["a", "b"]

    consumption, production = maps(the_system, "XCH_ZERO")
    assert production["a"] == pytest.approx(TPR_SUPPLY)
    assert production["b"] == pytest.approx(TPR_SUPPLY)


def test_the_delta_moves_between_the_two_balances(the_system):
    consumption, production = maps(the_system, "XCH_POS")

    assert production["a"] == pytest.approx(TPR_SUPPLY - 2.0)
    assert production["b"] == pytest.approx(TPR_SUPPLY + 2.0)


def test_what_one_balance_loses_the_other_gains(the_system):
    """Covers AE2 on the two-flow shape."""
    _, production = maps(the_system, "XCH_POS")
    _, neutral = maps(the_system, "XCH_ZERO")

    lost = neutral["a"] - production["a"]
    gained = production["b"] - neutral["b"]

    assert lost == pytest.approx(gained)
    assert lost == pytest.approx(2.0)


def test_a_negative_quantity_moves_the_other_way(the_system):
    """Covers AE1. The model declares no clamp; the library reads the sign."""
    _, production = maps(the_system, "XCH_NEG")

    assert production["a"] == pytest.approx(TPR_SUPPLY + 2.0)
    assert production["b"] == pytest.approx(TPR_SUPPLY - 2.0)


def test_the_model_declares_no_direction_clamp(the_system):
    """The negative exchanger differs from the positive one only in its sign."""
    pair = the_system.comp["XCH_NEG"].transfers["swap0"]

    assert pair.source == "a"
    assert pair.destination == "b"
    assert pair.quantity(the_system.comp["XCH_NEG"]) == pytest.approx(-2.0)


def test_two_pairs_on_one_flow_sum(the_system):
    """Two gradients across one balance add. Deratings fold by minimum; this
    is a conserved quantity and the rule does not carry over."""
    _, production = maps(the_system, "XCH_SUM")

    assert production["a"] == pytest.approx(TPR_SUPPLY - 3.0)
    assert production["b"] == pytest.approx(TPR_SUPPLY + 3.0)


def test_a_pair_returning_zero_leaves_both_balances_untouched(the_system):
    _, production = maps(the_system, "XCH_ZERO")

    assert production["a"] == pytest.approx(TPR_SUPPLY)
    assert production["b"] == pytest.approx(TPR_SUPPLY)


# ----------------------------------------------------------------------
# Saturation: capped together, never exceeded
# ----------------------------------------------------------------------


def test_a_conduit_cannot_move_more_than_its_supply(the_system):
    """Covers AE4 on the conduit shape: 5 asked, 1 available."""
    consumption, production = maps(the_system, "CND_SAT")

    assert consumption["x"] == pytest.approx(1.0)
    assert production["x"] == pytest.approx(1.0)


def test_the_saturated_pair_records_both_numbers(the_system):
    """Covers AE5, on the pair object. U4 publishes them to the model."""
    maps(the_system, "CND_SAT")
    pair = the_system.comp["CND_SAT"].transfers["meter"]

    assert pair.last_requested == pytest.approx(5.0)
    assert pair.last_moved == pytest.approx(1.0)


def test_an_unsaturated_pair_records_the_two_equal(the_system):
    maps(the_system, "CND_OK")
    pair = the_system.comp["CND_OK"].transfers["meter"]

    assert pair.last_requested == pytest.approx(pair.last_moved)


def test_conservation_holds_on_every_pair(the_system):
    """Covers AE2 across both shapes: nothing is created and nothing lost.

    Compared on MAGNITUDES: both readings are signed, so a pair moving 2.0 the
    other way reports -2.0 rather than a second positive number that would hide
    the direction.
    """
    for comp_name in ("CND_OK", "CND_SAT", "XCH_POS", "XCH_NEG", "XCH_SUM"):
        comp = the_system.comp[comp_name]
        maps(the_system, comp_name)

        for pair in comp.transfers.values():
            assert abs(pair.last_moved) <= abs(pair.last_requested) + 1e-12, comp_name
            assert pair.shortfall >= 0.0, comp_name


def test_the_readback_carries_the_direction(the_system):
    """A reversed two-flow pair reports a negative quantity on both readings."""
    maps(the_system, "XCH_NEG")
    pair = the_system.comp["XCH_NEG"].transfers["swap0"]

    assert pair.last_requested == pytest.approx(-2.0)
    assert pair.last_moved == pytest.approx(-2.0)
    assert pair.shortfall == pytest.approx(0.0)


def test_a_conduit_crosses_nothing_backwards(the_system):
    """A conduit's direction is its connection's, and KD1 fixes that.

    Moving the magnitude forward instead would be actively wrong: a symmetric
    conduction law telling a tank to cool would warm it. The request stays
    readable so the reversal is visible rather than silently dropped.
    """
    conduit = the_system.comp["CND_OK"]
    pair = conduit.transfers["meter"]
    original = pair.equation

    pair.equation = fixed(-4.0)
    try:
        consumption, production = conduit.evaluate_production()

        assert consumption["x"] == pytest.approx(0.0)
        assert production["x"] == pytest.approx(0.0)
        assert pair.last_requested == pytest.approx(-4.0)
        assert pair.last_moved == pytest.approx(0.0)
        assert pair.shortfall == pytest.approx(4.0)
    finally:
        pair.equation = original


def test_a_reversing_conduit_asks_for_nothing(the_system):
    """It would otherwise claim upstream a quantity it will not move."""
    conduit = the_system.comp["CND_OK"]
    pair = conduit.transfers["meter"]
    original = pair.equation

    pair.equation = fixed(-4.0)
    try:
        assert conduit.evaluate_demand()["x"] == pytest.approx(0.0)
    finally:
        pair.equation = original


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
