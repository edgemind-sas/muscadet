"""What a pair asks upstream, and what it lets its output promise downstream.

Two sweeps, one asymmetry, and the asymmetry is the point.

**Demand.** A CONDUIT asks for what it is about to move. It replaced its flow's
identity transfer, so nothing else claims that input, and without this the
three-component conduit returns zero -- the measured failure the whole notion
exists for. A TWO-FLOW pair asks for nothing of its own: both its streams keep
their identity transfer and already carry their consumer's demand upstream, so
adding the moved quantity on top would ask a supplier for a quantity no balance
needs.

**Capability.** Both shapes contribute, mirroring the production sweep exactly,
with one difference that defines the sweep: the bound is the source's
CAPABILITY and not its delivery, so what an output publishes is what the pair
could move if nothing downstream held it back. A consumer sizing itself on that
figure asks for a quantity the pair can honour, which is what keeps demand,
delivery and consumption in agreement on the common path.

PyCATSHOO forbids more than one live system per process, so every chain lives
in the one system below.
"""

import pytest

import cod3s
import muscadet

TDC_CLOCK = 5.0

#: What the sources hold. Large enough that nothing below saturates by
#: accident: a conduit that met its supply would hide the demand it published.
TDC_SUPPLY = 10.0

#: What the conduit meters, and what the exchanger moves.
TDC_METERED = 4.0
TDC_SWAPPED = 3.0

#: Declared on a conduit's input and never what it should publish: a conduit
#: that fell through to this default would ask for the wrong thing silently.
TDC_INPUT_DEFAULT = 7.0


def fixed(value):
    return muscadet.Transfer(fun=lambda comp: value, continuous=True)


class TdcSource(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name=kwargs.get("flow", "x"), var_fed_default=TDC_SUPPLY
        )


class TdcSink(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "x"),
            var_demand_default=kwargs.get("demand", TDC_SUPPLY),
        )


class TdcConduit(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="x", var_demand_default=TDC_INPUT_DEFAULT)
        self.add_flow_continuous_out(name="x")
        self.add_transfer("meter", flows=["x", "x"], equation=fixed(kwargs["rate"]))


class TdcPlainPipe(muscadet.ObjFlow):
    """The control: the same shape carrying no pair at all."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="x", var_demand_default=TDC_INPUT_DEFAULT)
        self.add_flow_continuous_out(name="x")


class TdcExchanger(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("a", "b"):
            self.add_flow_continuous_in(name=flow, var_demand_default=TDC_SUPPLY)
            self.add_flow_continuous_out(name=flow)

        if kwargs.get("paired", True):
            self.add_transfer(
                "swap",
                flows=["a", "b"],
                equation=fixed(kwargs.get("rate", TDC_SWAPPED)),
            )


def build_system():
    system = muscadet.System(name="TdcSys")

    # The three-component conduit: the case that used to return zero.
    system.add_component(name="SRC", cls="TdcSource")
    system.add_component(name="CND", cls="TdcConduit", rate=TDC_METERED)
    system.add_component(name="SNK", cls="TdcSink")
    system.connect_flow(source="SRC", target="CND", flow_name="x")
    system.connect_flow(source="CND", target="SNK", flow_name="x")

    # The same chain with no pair, to read the untouched behaviour off.
    system.add_component(name="SRC_P", cls="TdcSource")
    system.add_component(name="PIPE", cls="TdcPlainPipe")
    system.add_component(name="SNK_P", cls="TdcSink")
    system.connect_flow(source="SRC_P", target="PIPE", flow_name="x")
    system.connect_flow(source="PIPE", target="SNK_P", flow_name="x")

    # A paired exchanger and its pair-free twin, to compare demand against.
    for prefix, paired in (("PAIRED", True), ("BARE", False)):
        system.add_component(name=f"XA_{prefix}", cls="TdcSource", flow="a")
        system.add_component(name=f"XB_{prefix}", cls="TdcSource", flow="b")
        system.add_component(name=f"XX_{prefix}", cls="TdcExchanger", paired=paired)
        system.add_component(name=f"XKA_{prefix}", cls="TdcSink", flow="a")
        system.add_component(name=f"XKB_{prefix}", cls="TdcSink", flow="b")
        system.connect_flow(source=f"XA_{prefix}", target=f"XX_{prefix}", flow_name="a")
        system.connect_flow(source=f"XB_{prefix}", target=f"XX_{prefix}", flow_name="b")
        system.connect_flow(
            source=f"XX_{prefix}", target=f"XKA_{prefix}", flow_name="a"
        )
        system.connect_flow(
            source=f"XX_{prefix}", target=f"XKB_{prefix}", flow_name="b"
        )

    system.comp["SRC"].add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": TDC_CLOCK},
        cond_occ_21=False,
    )

    return system


@pytest.fixture(scope="module")
def the_system():
    system = build_system()
    system.isimu_start()
    system.isimu_step_forward()
    yield system


def demand_of(system, comp_name):
    return system.comp[comp_name].evaluate_demand()


def capability_of(system, comp_name):
    return system.comp[comp_name].evaluate_capability()


def received(system, comp_name, flow_name):
    return system.comp[comp_name].flows_in[flow_name].get_delivered()


# ----------------------------------------------------------------------
# Demand
# ----------------------------------------------------------------------


def test_a_conduit_asks_for_what_it_will_move(the_system):
    """Covers AE3's demand half, and R6."""
    assert demand_of(the_system, "CND")["x"] == pytest.approx(TDC_METERED)


def test_a_conduit_does_not_fall_through_to_its_declared_default(the_system):
    """The default is deliberately a different number, so a fall-through shows."""
    assert demand_of(the_system, "CND")["x"] != pytest.approx(TDC_INPUT_DEFAULT)


def test_the_conduits_computed_quantity_reaches_the_far_end(the_system):
    """Covers AE3. The chain that used to return zero end to end."""
    assert received(the_system, "SNK", "x") == pytest.approx(TDC_METERED)


def test_a_plain_pipe_still_carries_its_consumers_demand(the_system):
    """The control: nothing about the untouched transfer path moved."""
    assert demand_of(the_system, "PIPE")["x"] == pytest.approx(TDC_SUPPLY)


def test_a_two_flow_pair_publishes_no_demand_of_its_own(the_system):
    """Its streams already carry their consumers' demand through the transfer.

    Compared against the pair-free twin rather than against a constant, so the
    claim is "the pair changed nothing here" and not "the number happens to be
    this".
    """
    paired = demand_of(the_system, "XX_PAIRED")
    bare = demand_of(the_system, "XX_BARE")

    assert paired["a"] == pytest.approx(bare["a"])
    assert paired["b"] == pytest.approx(bare["b"])


# ----------------------------------------------------------------------
# Capability
# ----------------------------------------------------------------------


def test_a_conduit_publishes_what_it_could_move(the_system):
    assert capability_of(the_system, "CND")["x"] == pytest.approx(TDC_METERED)


def test_a_two_flow_pair_shifts_the_published_capability(the_system):
    """One output could deliver more, the other less, by the same quantity."""
    paired = capability_of(the_system, "XX_PAIRED")
    bare = capability_of(the_system, "XX_BARE")

    assert bare["a"] - paired["a"] == pytest.approx(TDC_SWAPPED)
    assert paired["b"] - bare["b"] == pytest.approx(TDC_SWAPPED)


def test_the_capability_is_bounded_by_the_source_not_its_delivery(the_system):
    """A conduit asking beyond its supply publishes the supply, not the ask."""
    conduit = the_system.comp["CND"]
    conduit.transfers["meter"].equation = fixed(TDC_SUPPLY * 3)

    try:
        assert capability_of(the_system, "CND")["x"] == pytest.approx(TDC_SUPPLY)
    finally:
        conduit.transfers["meter"].equation = fixed(TDC_METERED)


def test_capability_and_delivery_agree_on_the_common_path(the_system):
    """What the conduit promised is what its consumer got."""
    promised = capability_of(the_system, "CND")["x"]

    assert received(the_system, "SNK", "x") == pytest.approx(promised)


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
