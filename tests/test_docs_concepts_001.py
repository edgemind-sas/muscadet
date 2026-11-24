"""The figures of the Concepts pages of the documentation site, simulated.

Every number drawn on an animated figure under ``docs/manual/concepts/`` is a
claim about what muscadet computes. This module builds each figure's model and
pins the rates it settles on, so a change of semantics that would make a figure
lie fails here rather than on the site. When a test below moves, the figure
named in its docstring moves with it.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, stepped once, read and deleted before the next one starts.
"""

import math

import cod3s
import pytest

import muscadet

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
    TransformerContinuous,
)

#: Date the session steps to. Any positive date works: every figure is a
#: steady state, reached at the first evaluation.
DC_CLOCK = 1.0


def add_clock(system, comp):
    """Give the interactive session a date to step to."""
    system.comp[comp].add_atm2states(
        name="dc_clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": DC_CLOCK},
        cond_occ_21=False,
    )


def received(system, comp, flow="q"):
    """What ``comp`` receives on its input ``flow``."""
    return system.comp[comp].flows_in[flow].var_fed.value()


def delivered(system, comp, flow="q"):
    """What ``comp`` delivers on its output ``flow``."""
    return system.comp[comp].flows_out[flow].var_fed.value()


def run(build, read):
    """Build a system, step it once, read it, delete it."""
    system = muscadet.System(name="DocsConcepts")
    try:
        build(system)
        system.isimu_start()
        system.isimu_step_forward()
        return read(system)
    finally:
        system.isimu_stop()
        system.deleteSys()


def source(system, name, rate, flow="q"):
    system.add_component(name=name, cls="SourceContinuous", flow=flow, rate=rate)


def consumer(system, name, demand, flow="q"):
    system.add_component(
        name=name, cls="ConsumerContinuous", flow=flow, demand=demand
    )


# --- concepts/continuous-flows.md ---------------------------------------------


@pytest.mark.parametrize(
    "offer, demand, expected",
    [(10.0, 10.0, 10.0), (10.0, 15.0, 10.0), (10.0, 4.0, 4.0)],
    ids=["balanced", "short-supply", "demand-led"],
)
def test_one_source_one_consumer(offer, demand, expected):
    """Figure "one source, one consumer": the lesser of offer and demand."""

    def build(system):
        source(system, "S", offer)
        consumer(system, "C", demand)
        system.connect_flow(source="S", target="C", flow_name="q")
        add_clock(system, "C")

    got = run(build, lambda s: (delivered(s, "S"), received(s, "C")))
    assert got == pytest.approx((expected, expected))


def test_shortage_split_in_proportion_to_demand():
    """Figure "shortage": 12 shared by demands of 6 and 12 gives 4 and 8."""

    def build(system):
        source(system, "S", 12.0)
        consumer(system, "C1", 6.0)
        consumer(system, "C2", 12.0)
        system.connect_flow(source="S", target="C1", flow_name="q")
        system.connect_flow(source="S", target="C2", flow_name="q")
        add_clock(system, "C1")

    got = run(build, lambda s: (received(s, "C1"), received(s, "C2")))
    assert got == pytest.approx((4.0, 8.0))


def test_a_limited_consumer_asks_only_for_what_it_can_use():
    """Figure "the offer goes first": a machine short of its other input asks
    for 2, so its neighbour gets its whole 10 out of a supply of 12."""

    def build(system):
        source(system, "S", 12.0)
        source(system, "W", 2.0, flow="w")
        system.add_component(
            name="M",
            cls="TransformerContinuous",
            flows_in=["q", "w"],
            flows_out=["p"],
            rules=[dict(name="r", cons={"q": 1.0, "w": 1.0}, prod={"p": 1.0})],
        )
        consumer(system, "OUT", 10.0, flow="p")
        consumer(system, "C2", 10.0)
        system.connect_flow(source="S", target="M", flow_name="q")
        system.connect_flow(source="W", target="M", flow_name="w")
        system.connect_flow(source="M", target="OUT", flow_name="p")
        system.connect_flow(source="S", target="C2", flow_name="q")
        add_clock(system, "C2")

    def read(system):
        demand_m = system.comp["M"].flows_in["q"].var_demand.value()
        return demand_m, received(system, "M"), received(system, "C2")

    assert run(build, read) == pytest.approx((2.0, 2.0, 10.0))


def test_two_sources_share_a_demand_in_proportion_to_their_offer():
    """Figure "two sources": offers of 9 and 3 against a machine needing 6
    give 4 and 2. Each source is asked the whole 6 and answers 6 and 3; the
    machine takes what its rule uses and hands the surplus back in proportion
    to what each delivered."""

    def build(system):
        source(system, "S1", 9.0)
        source(system, "S2", 3.0)
        system.add_component(
            name="M",
            cls="TransformerContinuous",
            flows_in=["q"],
            flows_out=["p"],
            rules=[dict(name="r", cons={"q": 1.0}, prod={"p": 1.0})],
        )
        consumer(system, "C", 6.0, flow="p")
        system.connect_flow(source="S1", target="M", flow_name="q")
        system.connect_flow(source="S2", target="M", flow_name="q")
        system.connect_flow(source="M", target="C", flow_name="p")
        add_clock(system, "C")

    def read(system):
        return delivered(system, "S1"), delivered(system, "S2"), received(system, "M")

    assert run(build, read) == pytest.approx((4.0, 2.0, 6.0))


def test_a_pure_sink_takes_only_its_demand():
    """The note under figure "two sources": a consumer carrying no rule caps
    what it takes at its declared demand and hands the rest back, exactly as
    the machine does."""

    def build(system):
        source(system, "S1", 9.0)
        source(system, "S2", 3.0)
        consumer(system, "C", 6.0)
        system.connect_flow(source="S1", target="C", flow_name="q")
        system.connect_flow(source="S2", target="C", flow_name="q")
        add_clock(system, "C")

    def read(system):
        return delivered(system, "S1"), delivered(system, "S2"), received(system, "C")

    assert run(build, read) == pytest.approx((4.0, 2.0, 6.0))


# --- concepts/transformation.md -----------------------------------------------

#: The electrolyser's balance, per kg/h of hydrogen.
DC_RULE = dict(
    name="electrolysis",
    cons={"water": 9.0, "power": 50.0},
    prod={"h2": 1.0, "o2": 8.0},
)


def electrolyser(system, power, h2_demand):
    source(system, "WATER", 1000.0, flow="water")
    source(system, "GRID", power, flow="power")
    system.add_component(
        name="ELY",
        cls="TransformerContinuous",
        flows_in=["water", "power"],
        flows_out=["h2", "o2"],
        rules=[DC_RULE],
    )
    consumer(system, "H2_USE", h2_demand, flow="h2")
    consumer(system, "O2_VENT", 1000.0, flow="o2")
    system.connect_flow(source="WATER", target="ELY", flow_name="water")
    system.connect_flow(source="GRID", target="ELY", flow_name="power")
    system.connect_flow(source="ELY", target="H2_USE", flow_name="h2")
    system.connect_flow(source="ELY", target="O2_VENT", flow_name="o2")
    add_clock(system, "H2_USE")


def read_electrolyser(system):
    return (
        received(system, "ELY", "water"),
        received(system, "ELY", "power"),
        delivered(system, "ELY", "h2"),
        delivered(system, "ELY", "o2"),
    )


def test_the_demand_sets_the_scale_of_a_rule():
    """Figure "a rule": 20 kg/h of hydrogen asked, 1000 kW available."""
    got = run(lambda s: electrolyser(s, 1000.0, 20.0), read_electrolyser)
    assert got == pytest.approx((180.0, 1000.0, 20.0, 160.0))


def test_the_scarcest_input_caps_a_rule():
    """Figure "a rule": 20 kg/h asked, 500 kW available, everything halves."""
    got = run(lambda s: electrolyser(s, 500.0, 20.0), read_electrolyser)
    assert got == pytest.approx((90.0, 500.0, 10.0, 80.0))


# --- concepts/capacities.md ---------------------------------------------------

DC_VOLUME = 100.0
DC_FILL = 10.0


@pytest.mark.parametrize(
    "state, offer, demand, level, inflow, outflow",
    [
        ("empty", 4.0, 10.0, 0.0, 4.0, 4.0),
        ("partial", 10.0, 3.0, 50.0, 10.0, 3.0),
        ("full", 10.0, 3.0, DC_VOLUME, 3.0, 3.0),
    ],
)
def test_a_capacity_in_each_of_its_states(
    state, offer, demand, level, inflow, outflow
):
    """Figure "three states": what enters and what leaves a volume."""

    def build(system):
        source(system, "S", offer)
        system.add_component(
            name="TANK",
            cls="CapacityContinuous",
            flow="q",
            capacity=DC_VOLUME,
            capacity_name="tank",
            content_init={"q": level},
            fill_rate=DC_FILL,
        )
        consumer(system, "C", demand)
        system.connect_flow(source="S", target="TANK", flow_name="q")
        system.connect_flow(source="TANK", target="C", flow_name="q")
        add_clock(system, "C")

    def read(system):
        return received(system, "TANK"), delivered(system, "TANK")

    assert run(build, read) == pytest.approx((inflow, outflow))


def test_delete():
    cod3s.terminate_session()
    assert math.isfinite(DC_CLOCK)
