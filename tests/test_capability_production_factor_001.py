"""A capability says what an output could deliver now, not what it is rated at.

The capability channel (R-20) publishes what each continuous output could
deliver if asked without bound. Up to 5.8.0 it published the nominal rating:
the production factor -- time profile, effective rate, production gate -- was
applied by the production sweep alone. A solar field therefore announced its
peak at midnight, a source derated to half announced its whole rate, and an
output whose production condition was false announced the rate it was not
producing. Every demand bounded by such a figure was sized on a production the
supplier was not making.

The capability now carries the same factor production carries, applied at the
same point, before an output capacity substitutes for the flow. What is
asserted here:

* each of the three terms reaches the capability, alone;
* a stocked volume still announces what it can serve, whatever the factor on
  its output: the factor scales production, and a stock is not production;
* a zero factor on an unbounded capability publishes zero, not NaN;
* the consequence on a demand: a two-reagent rule whose first reagent comes
  from a source producing nothing no longer claims the second.

PyCATSHOO forbids more than one live system per process: one system carries
every scenario, and the sweeps are driven by hand once the session has started,
as ``test_capability_demand_001`` drives them.
"""

import math

import cod3s
import pytest

import muscadet

# Imported for its side effect: a component class resolves by name.
from muscadet.kb.continuous import CapacityContinuous  # noqa: F401

#: The rating every source below is declared with.
CPF_RATE = 10.0
#: The factor the half profile and the derating leave of it.
CPF_HALF = 0.5
#: A downstream demand nothing below can satisfy.
CPF_BIG_DEMAND = 1000.0
#: ``2 a + 1 b -> 1 x``.
CPF_CONS = {"a": 2.0, "b": 1.0}
CPF_VOLUME = 100.0
CPF_CONTENT = 40.0
#: How many times the sweeps are driven, so a value that drifts would show.
CPF_ROUNDS = 3


def constant(value):
    """A profile holding ``value`` at every instant."""
    return muscadet.Profile(lambda time: value, continuous=True)


class CpfSource(muscadet.ObjFlow):
    """A source of ``q`` at :data:`CPF_RATE`, profiled when asked to be."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        profile = kwargs.get("profile")
        self.add_flow_continuous_out(
            name=kwargs.get("flow", "q"),
            var_fed_default=CPF_RATE,
            profile=None if profile is None else constant(profile),
        )


class CpfGatedSource(muscadet.ObjFlow):
    """A source of ``q`` producing only while its discrete command holds."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="cmd", var_in_default=kwargs.get("command", False))
        self.add_flow_continuous_out(
            name="q", var_fed_default=CPF_RATE, var_prod_cond=["cmd"]
        )


class CpfSink(muscadet.ObjFlow):
    """A consumer asking :data:`CPF_BIG_DEMAND` on each flow it takes."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for name in kwargs.get("takes", ["q"]):
            self.add_flow_continuous_in(name=name, var_demand_default=CPF_BIG_DEMAND)


class CpfPipe(muscadet.ObjFlow):
    """``q`` in, ``q`` out: the identity transfer."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="q")


class CpfUnit(muscadet.ObjFlow):
    """``2 a + 1 b -> 1 x``."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_in(name="b")
        self.add_flow_continuous_out(name="x")
        self.add_rules(name="unit", rules=[dict(cons=CPF_CONS, prod={"x": 1.0})])


def add_clock(comp):
    """Give the interactive session a date to step to."""
    comp.add_atm2states(
        name="cpf_clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": 1.0},
        cond_occ_21=False,
    )


def feed_a_sink(system, source, flow="q"):
    """Wire ``source`` to a sink of its own, so its output has a consumer."""
    sink = f"{source}_SINK"
    system.add_component(name=sink, cls="CpfSink", takes=[flow])
    system.connect_flow(source=source, target=sink, flow_name=flow)


def drive_sweeps(system, order):
    """The three sweeps, in band order, once (cf. ``test_capability_demand_001``)."""
    for name in order.capability_order:
        system.comp[name].compute_capability()
    for name in order.demand_order:
        system.comp[name].compute_demand()
    for name in order.production_order:
        system.comp[name].compute_production()


def capability_of(system, comp, flow="q"):
    return system.comp[comp].flows_out[flow].get_capability()


@pytest.fixture(scope="module")
def the_run():
    system = muscadet.System(name="CapabilityProductionFactor")

    # -- One source per term of the factor, and the unfactored reference.
    for name, profile in (("RATED", None), ("NIGHT", 0.0), ("HALF", CPF_HALF)):
        system.add_component(name=name, cls="CpfSource", profile=profile)
        feed_a_sink(system, name)
    system.add_component(name="DERATED", cls="CpfSource")
    feed_a_sink(system, "DERATED")
    for name, command in (("SHUT", False), ("OPEN", True)):
        system.add_component(name=name, cls="CpfGatedSource", command=command)
        feed_a_sink(system, name)

    # -- A stocked volume whose output a mode stopped, and a pipe it feeds whose
    #    own output is stopped: unbounded upstream, zero factor downstream.
    system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        flow="q",
        ports="out",
        capacity=CPF_VOLUME,
        capacity_name="tank",
        content_init={"q": CPF_CONTENT},
    )
    system.add_component(name="PIPE", cls="CpfPipe")
    system.connect_flow(source="TANK", target="PIPE", flow_name="q")
    feed_a_sink(system, "PIPE")

    # -- The consequence on a demand: reagent ``a`` comes from a source
    #    producing nothing, reagent ``b`` from one producing plenty.
    system.add_component(name="DARK_A", cls="CpfSource", flow="a", profile=0.0)
    system.add_component(name="LIT_B", cls="CpfSource", flow="b")
    system.add_component(name="UNIT", cls="CpfUnit")
    system.connect_flow(source="DARK_A", target="UNIT", flow_name="a")
    system.connect_flow(source="LIT_B", target="UNIT", flow_name="b")
    feed_a_sink(system, "UNIT", flow="x")

    add_clock(system.comp["RATED"])

    system.comp["DERATED"].flows_out["q"].var_out_rate.setValue(CPF_HALF)
    system.comp["TANK"].flows_out["q"].var_out_rate.setValue(0.0)
    system.comp["PIPE"].flows_out["q"].var_out_rate.setValue(0.0)

    # What the capability holds before any sweep has run: the value a first
    # sample reads, and the one a sequence restarts from.
    obs = {
        "initial": {
            name: system.comp[name].flows_out["q"].var_capability.value()
            for name in ("RATED", "NIGHT", "HALF")
        }
    }
    try:
        system.isimu_start()
        order = system.equation_order
        for _ in range(CPF_ROUNDS):
            drive_sweeps(system, order)

        obs["capability"] = {
            name: capability_of(system, name)
            for name in (
                "RATED",
                "NIGHT",
                "HALF",
                "DERATED",
                "SHUT",
                "OPEN",
                "TANK",
                "PIPE",
            )
        }
        obs["delivered"] = {
            name: system.comp[name].flows_out["q"].var_fed.value()
            for name in ("NIGHT", "HALF", "DERATED", "SHUT", "OPEN")
        }
        unit = system.comp["UNIT"]
        obs["unit"] = {
            "capability_x": capability_of(system, "UNIT", "x"),
            "demand_a": unit.flows_in["a"].var_demand.value(),
            "demand_b": unit.flows_in["b"].var_demand.value(),
            "drawn_b": unit.flows_in["b"].get_delivered(),
        }
        system.isimu_stop()
    finally:
        obs["system"] = system

    return obs


def test_an_unfactored_source_still_announces_its_rating(the_run):
    assert the_run["capability"]["RATED"] == CPF_RATE


def test_a_profile_at_zero_announces_nothing(the_run):
    """The solar field at midnight: its rating is not on offer."""
    assert the_run["capability"]["NIGHT"] == 0.0
    assert the_run["delivered"]["NIGHT"] == 0.0


def test_the_capability_starts_from_the_profile_at_instant_zero(the_run):
    """Before the first sweep, as the delivered quantity does: the solar field
    does not announce its peak on the first sample of a sequence."""
    assert the_run["initial"] == {
        "RATED": CPF_RATE,
        "NIGHT": 0.0,
        "HALF": pytest.approx(CPF_RATE * CPF_HALF),
    }


def test_a_profile_scales_the_capability_as_it_scales_production(the_run):
    assert the_run["capability"]["HALF"] == pytest.approx(CPF_RATE * CPF_HALF)
    assert the_run["capability"]["HALF"] == pytest.approx(the_run["delivered"]["HALF"])


def test_a_derating_scales_the_capability_as_it_scales_production(the_run):
    assert the_run["capability"]["DERATED"] == pytest.approx(CPF_RATE * CPF_HALF)
    assert the_run["capability"]["DERATED"] == pytest.approx(
        the_run["delivered"]["DERATED"]
    )


def test_a_closed_production_gate_announces_nothing(the_run):
    assert the_run["capability"]["SHUT"] == 0.0
    assert the_run["delivered"]["SHUT"] == 0.0
    assert the_run["capability"]["OPEN"] == CPF_RATE


def test_a_stocked_volume_announces_what_it_can_serve_whatever_its_factor(the_run):
    """The factor scales production; what a stock serves is not production."""
    assert math.isinf(the_run["capability"]["TANK"])


def test_a_zero_factor_on_an_unbounded_capability_publishes_zero(the_run):
    """``inf * 0`` is NaN, which would poison every demand bound downstream."""
    pipe = the_run["capability"]["PIPE"]
    assert not math.isnan(pipe)
    assert pipe == 0.0


def test_a_reagent_from_a_source_producing_nothing_stops_the_claim_on_the_other(
    the_run,
):
    """``b`` is bounded by what ``a`` could supply, which is now nothing.

    Up to 5.8.0 the dark source announced its rating, so the unit claimed
    ``1 x min(downstream, 10 / 2) = 5`` of ``b`` it could never use.
    """
    unit = the_run["unit"]
    assert unit["capability_x"] == 0.0
    assert unit["demand_b"] == 0.0
    assert unit["drawn_b"] == 0.0


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
