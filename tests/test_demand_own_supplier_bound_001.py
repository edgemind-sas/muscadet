"""A claim never exceeds what its own supplier can deliver.

The demand on an input is the downstream scale mapped back through the rule's
coefficients, bounded by the scale the rule's suppliers can sustain (R-20). Up
to 5.9.0 that bound ran over the OTHER inputs only, so a claim could exceed what
its own supplier would ever deliver. Two consequences, both asserted here:

* **the published figure**: an electrolyser venting its oxygen to a sink asking
  1000 claimed 1666.67 of water from a pump delivering 5;
* **the split**: a source shared by two consumers is split in proportion to the
  claims, so a consumer whose downstream asked more took the supply from its
  rival -- and the more its downstream asked, the less the rival got, down to
  nothing. An UNBOUNDED downstream, which ``regularize_demands`` brings back to
  the quantity available, gave the rival its fair share again: the split was
  discontinuous at infinity.

Since 5.10.0 every claim is bounded by its own supplier as well, so the split no
longer depends on how much more than the supply a downstream asks for.

PyCATSHOO forbids more than one live system per process: one system carries
every scenario, and the sweeps are driven by hand once the session has started,
as ``test_capability_demand_001`` drives them.
"""

import math

import cod3s
import pytest

import muscadet

#: What the shared source delivers.
OSB_SUPPLY = 5.0
#: What the modest consumer needs.
OSB_NEED = 2.0
#: How much the greedy consumer's downstream asks for, scenario by scenario.
OSB_ASKS = {"TEN": 10.0, "THOUSAND": 1000.0, "MILLION": 1e6, "UNBOUNDED": math.inf}
#: Both claims are bounded by the supply, so the split is 5 against 2.
OSB_GREEDY_SHARE = OSB_SUPPLY * OSB_SUPPLY / (OSB_SUPPLY + OSB_NEED)
OSB_MODEST_SHARE = OSB_SUPPLY * OSB_NEED / (OSB_SUPPLY + OSB_NEED)

#: The electrolyser of the H2 showcase: water and power in, hydrogen and oxygen out.
OSB_STACK_CONS = {"H2O": 5.0, "Elec": 20.0}
OSB_STACK_PROD = {"H2": 2.0, "O2": 3.0}
OSB_PUMP_RATE = 5.0
OSB_GRID_RATE = 1e4
OSB_SINK_DEMAND = 1000.0

OSB_ROUNDS = 3


class OsbSource(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name=kwargs["flow"], var_fed_default=kwargs["rate"]
        )


class OsbUnit(muscadet.ObjFlow):
    """One ``w`` makes one ``x``."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="w")
        self.add_flow_continuous_out(name="x")
        self.add_rules(name="run", rules=[dict(cons={"w": 1.0}, prod={"x": 1.0})])


class OsbStack(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for name in OSB_STACK_CONS:
            self.add_flow_continuous_in(name=name)
        for name in OSB_STACK_PROD:
            self.add_flow_continuous_out(name=name)
        self.add_rules(
            name="electrolysis",
            rules=[dict(cons=OSB_STACK_CONS, prod=OSB_STACK_PROD)],
        )


class OsbSink(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs["flow"], var_demand_default=kwargs["demand"]
        )


def add_rivals(system, key, asked):
    """One source of :data:`OSB_SUPPLY` feeding a greedy and a modest unit."""
    system.add_component(name=f"{key}_SRC", cls="OsbSource", flow="w", rate=OSB_SUPPLY)
    for unit, demand in (("GREEDY", asked), ("MODEST", OSB_NEED)):
        name = f"{key}_{unit}"
        system.add_component(name=name, cls="OsbUnit")
        system.add_component(
            name=f"{name}_SINK", cls="OsbSink", flow="x", demand=demand
        )
        system.connect_flow(source=f"{key}_SRC", target=name, flow_name="w")
        system.connect_flow(source=name, target=f"{name}_SINK", flow_name="x")


def add_stack(system):
    """The showcase electrolyser, a pump of 5 short of what its sinks ask."""
    system.add_component(name="PUMP", cls="OsbSource", flow="H2O", rate=OSB_PUMP_RATE)
    system.add_component(name="GRID", cls="OsbSource", flow="Elec", rate=OSB_GRID_RATE)
    system.add_component(name="STACK", cls="OsbStack")
    system.connect_flow(source="PUMP", target="STACK", flow_name="H2O")
    system.connect_flow(source="GRID", target="STACK", flow_name="Elec")
    for flow in OSB_STACK_PROD:
        sink = f"{flow}_SINK"
        system.add_component(
            name=sink, cls="OsbSink", flow=flow, demand=OSB_SINK_DEMAND
        )
        system.connect_flow(source="STACK", target=sink, flow_name=flow)


@pytest.fixture(scope="module")
def the_run():
    system = muscadet.System(name="DemandOwnSupplierBound")
    for key, asked in OSB_ASKS.items():
        add_rivals(system, key, asked)
    add_stack(system)

    system.comp["PUMP"].add_atm2states(
        name="osb_clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": 1.0},
        cond_occ_21=False,
    )

    obs = {}
    try:
        system.isimu_start()
        order = system.equation_order
        for _ in range(OSB_ROUNDS):
            for name in order.capability_order:
                system.comp[name].compute_capability()
            for name in order.demand_order:
                system.comp[name].compute_demand()
            for name in order.production_order:
                system.comp[name].compute_production()

        obs["rivals"] = {
            key: {
                unit: {
                    "claim": system.comp[f"{key}_{unit}"]
                    .flows_in["w"]
                    .var_demand.value(),
                    "served": system.comp[f"{key}_{unit}"]
                    .flows_in["w"]
                    .get_delivered(),
                }
                for unit in ("GREEDY", "MODEST")
            }
            for key in OSB_ASKS
        }
        stack = system.comp["STACK"]
        obs["stack"] = {
            "claim_H2O": stack.flows_in["H2O"].var_demand.value(),
            "claim_Elec": stack.flows_in["Elec"].var_demand.value(),
            "served_H2O": stack.flows_in["H2O"].get_delivered(),
            "H2": stack.flows_out["H2"].var_fed.value(),
        }
        system.isimu_stop()
    finally:
        obs["system"] = system

    return obs


@pytest.mark.parametrize("key", list(OSB_ASKS))
def test_a_claim_is_bounded_by_what_its_own_supplier_delivers(the_run, key):
    """Whatever its downstream asks, the greedy unit claims the supply, no more."""
    greedy = the_run["rivals"][key]["GREEDY"]

    assert greedy["claim"] == pytest.approx(OSB_SUPPLY)


@pytest.mark.parametrize("key", list(OSB_ASKS))
def test_the_split_does_not_depend_on_how_much_more_a_rival_asks(the_run, key):
    """Up to 5.9.0 the modest unit got 0.83, 0.01, 1e-5, then 1.43 at infinity."""
    rivals = the_run["rivals"][key]

    assert rivals["MODEST"]["claim"] == pytest.approx(OSB_NEED)
    assert rivals["GREEDY"]["served"] == pytest.approx(OSB_GREEDY_SHARE)
    assert rivals["MODEST"]["served"] == pytest.approx(OSB_MODEST_SHARE)


def test_the_stack_claims_the_water_the_pump_can_give(the_run):
    """The showcase figure: 5, where 5.9.0 claimed 5 x 1000 / 3 = 1666.67."""
    stack = the_run["stack"]

    assert stack["claim_H2O"] == pytest.approx(OSB_PUMP_RATE)
    assert stack["served_H2O"] == pytest.approx(OSB_PUMP_RATE)


def test_the_other_input_is_still_bounded_by_the_scarce_one(the_run):
    """R-20 as before: the water sustains a scale of 1, so 20 of power."""
    stack = the_run["stack"]
    scale = OSB_PUMP_RATE / OSB_STACK_CONS["H2O"]

    assert stack["claim_Elec"] == pytest.approx(scale * OSB_STACK_CONS["Elec"])
    assert stack["H2"] == pytest.approx(scale * OSB_STACK_PROD["H2"])


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
