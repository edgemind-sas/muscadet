"""Demand propagation and allocation: what is asked for, and how a shortage is split.

Production travels downstream, demand travels upstream, and what a connection
carries is the lesser of the two (KD4). This module pins down the upstream half:

* a continuous input publishes a demand, aggregated over the connections feeding
  it (R5), and a component maps the demand aggregated on an output back onto its
  inputs through the active rule's DECLARED coefficients (R34) -- including the
  recorded limitation that a component capped by a scarce input still claims its
  nominal demand on the others;
* an insufficient supply is split by the output's declared policy -- proportional
  to demand, at fixed shares, or by ordered priorities -- or by a Python rule the
  component supplies itself (R16, R17, KD9);
* a surplus freed by a consumer capped at its demand is redistributed by the same
  policy, repeatedly, until no consumer exceeds its demand (F2);
* a capacity bounds both directions: a full one reduces the demand it exports
  upstream (AE11), an empty one serves onward only what transits through it (R7).

Every allocation here is a closed-form function of demands already known (KTD12):
nothing renegotiates with a consumer that was capped.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, driven and deleted before the next one starts; the fixture snapshots what
each produced. The policies themselves are also exercised away from the engine,
where a redistribution pass can be observed one at a time.
"""

import math

import cod3s
import muscadet
import pytest

from muscadet import flow_continuous
from muscadet.flow_continuous import (
    UNBOUNDED,
    allocate,
    split_priority,
    split_proportional,
    split_shares,
)

#: A date the interactive session can always step to, so the solver integrates.
CLOCK_DELAY = 5.0

#: The solver stops the integration on a crossing rather than refining it to
#: machine precision, so a date is asserted within one default PDMP step.
CROSSING_TOL = 0.05

#: Every producer / consumers cluster built below, ``{prefix: consumer names}``.
#: What makes the conservation invariant assertable over all of them at once:
#: whatever the policy, ``{f}_fed_out`` is the total delivered downstream, so
#: it must equal the sum of the shares the consumers actually read back.
CLUSTERS = {
    "AE16": ("AE16_C1", "AE16_C2"),
    "SH": ("SH_C1", "SH_C2"),
    "PR": ("PR_C1", "PR_C2", "PR_C3"),
    "CAP": ("CAP_C1", "CAP_C2", "CAP_C3"),
    "FUN": ("FUN_C1", "FUN_C2"),
    "Z": ("Z_C1", "Z_C2"),
    "UND": ("UND_C1", "UND_C2", "UND_C3"),
}


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class AllocSource(muscadet.ObjFlow):
    """A producer able to supply the rate it was declared with.

    ``policy`` carries the allocation declaration straight onto the output flow,
    which is where an insufficient supply is split (R16, R17).
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name=kwargs.get("flow", "q"),
            var_fed_default=kwargs.get("rate", 0.0),
            **kwargs.get("policy", {}),
        )


class AllocConsumer(muscadet.ObjFlow):
    """A pure consumer: it declares what it asks for and takes what it gets."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "q"),
            var_demand_default=kwargs.get("demand", 0.0),
        )


class AllocTank(muscadet.ObjFlow):
    """A pass-through buffering downstream what it serves onward (KTD13)."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="q")
        self.add_capacity(
            name="cuve",
            flow="q",
            capacity=kwargs.get("volume", 1000.0),
            side="out",
            content_init={"q": kwargs.get("init", 0.0)},
        )


class AllocCatalysed(muscadet.ObjFlow):
    """The catalyst idiom: an input the rule NAMES but consumes none of.

    A coefficient of 0 is what declares a flow the rule needs present without
    drawing it down. ``muscadet.rules.rule_scale`` skips such a coefficient --
    a rule consuming nothing of a flow is not limited by it -- and the demand
    half has to skip it identically.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="cat")
        self.add_flow_continuous_in(name="fuel")
        self.add_flow_continuous_out(name="burn")
        self.add_rules(
            name="react",
            rules=[dict(cons={"cat": 0.0, "fuel": 1.0}, prod={"burn": 1.0})],
        )


class AllocBlender(muscadet.ObjFlow):
    """3 of ``a`` and 2 of ``b`` make 2 of ``x``: a demand mapped in a ratio."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_in(name="b")
        self.add_flow_continuous_out(name="x")
        self.add_rules(
            name="blend",
            rules=[dict(cons={"a": 3, "b": 2}, prod={"x": 2})],
        )


class AllocMixTank(muscadet.ObjFlow):
    """2 of ``feed`` make 1 of ``h`` and 1 of ``o``, both held in ONE volume.

    The two constituents share the tank, so a draw on it is composed at their
    raw-quantity share (R35): asking for more of one than its share of the draw
    allows serves only that share -- a mixture cannot serve a pure constituent.
    The weights differ from 1 on purpose, so a split following the VOLUME each
    unit occupies would come out at a different number.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="feed")
        self.add_flow_continuous_out(name="h")
        self.add_flow_continuous_out(name="o")
        self.add_rules(
            name="split",
            rules=[dict(cons={"feed": 2}, prod={"h": 1, "o": 1})],
        )
        self.add_capacity(
            name="tank",
            flows=[{"name": "h", "weight": 1}, {"name": "o", "weight": 3}],
            capacity=1000.0,
            side="out",
            content_init={"h": 30.0, "o": 10.0},
        )


class AllocBufferedUnit(muscadet.ObjFlow):
    """1 of ``w`` and 1 of ``elec`` make 1 of ``x``, with ``w`` held in a tank.

    The tank sits upstream of the rules, so what the rules draw from it and what
    the component asks its own producer for are two different quantities -- which
    is exactly what a capacity at its volume separates (R7, AE11).
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="w")
        self.add_flow_continuous_in(name="elec")
        self.add_flow_continuous_out(name="x")
        self.add_rules(
            name="unit",
            rules=[dict(cons={"w": 1, "elec": 1}, prod={"x": 1})],
        )
        self.add_capacity(
            name="cuve",
            flow="w",
            capacity=kwargs.get("volume", 10.0),
            side="in",
            content_init={"w": kwargs.get("init", 0.0)},
        )


def split_evenly(available, demands):
    """A custom allocation rule: share and share alike, whatever is asked for.

    The R17 extension point in its simplest form -- none of the declared
    policies expresses "one each", since even shares would have to be restated
    every time a consumer is added.
    """
    if not demands:
        return {}

    quantity = available / len(demands)

    return {key: quantity for key in demands}


# ----------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------


def add_clock(comp):
    """Give the interactive session a date to step to."""
    comp.add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": CLOCK_DELAY},
        cond_occ_21=False,
    )


def add_cluster(system, prefix, rate, demands, policy=None, flow="q"):
    """One producer and its consumers, wired and named after ``prefix``."""
    source = f"{prefix}_SRC"
    system.add_component(
        name=source,
        cls="AllocSource",
        rate=rate,
        flow=flow,
        policy=policy or {},
    )

    for name, demand in demands.items():
        system.add_component(name=name, cls="AllocConsumer", flow=flow, demand=demand)
        system.connect_flow(source=source, target=name, flow_name=flow)

    return source


def delivered(system, comp, flow="q"):
    """What a consumer's input actually receives, allocation included."""
    return system.comp[comp].flows_in[flow].get_delivered()


def out_value(system, comp, flow="q"):
    """The total a producer delivers on its output flow."""
    return system.comp[comp].flows_out[flow].var_fed.value()


def published_demand(system, comp, flow="q"):
    """What a component publishes upstream on one of its inputs."""
    return system.comp[comp].flows_in[flow].var_demand.value()


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def build_allocation_system():
    """Every allocation and demand-mapping scenario, in one system."""
    system = muscadet.System(name="DemandAllocation")

    # -- AE16: 10 to share between demands of 8 and 12, default policy
    add_cluster(system, "AE16", 10.0, {"AE16_C1": 8.0, "AE16_C2": 12.0})

    # -- Fixed shares, against the same demands: the shares decide, not the ratio
    add_cluster(
        system,
        "SH",
        10.0,
        {"SH_C1": 8.0, "SH_C2": 12.0},
        policy=dict(
            allocation="shares",
            allocation_shares={"SH_C1": 0.7, "SH_C2": 0.3},
        ),
    )

    # -- Ordered priorities: served in order until the supply is gone
    add_cluster(
        system,
        "PR",
        10.0,
        {"PR_C1": 6.0, "PR_C2": 6.0, "PR_C3": 6.0},
        policy=dict(
            allocation="priority",
            allocation_priorities={"PR_C1": 1, "PR_C2": 2, "PR_C3": 3},
        ),
    )

    # -- Shares 0.5 / 0.3 / 0.2 against demands of 1, 4 and 100: two passes
    add_cluster(
        system,
        "CAP",
        10.0,
        {"CAP_C1": 1.0, "CAP_C2": 4.0, "CAP_C3": 100.0},
        policy=dict(
            allocation="shares",
            allocation_shares={"CAP_C1": 0.5, "CAP_C2": 0.3, "CAP_C3": 0.2},
        ),
    )

    # -- A Python rule, declared alongside a policy it must win over
    add_cluster(
        system,
        "FUN",
        10.0,
        {"FUN_C1": 6.0, "FUN_C2": 6.0},
        policy=dict(
            allocation="shares",
            allocation_shares={"FUN_C1": 0.9, "FUN_C2": 0.1},
            allocation_fun=split_evenly,
        ),
    )

    # -- Every consumer demanding zero
    add_cluster(system, "Z", 10.0, {"Z_C1": 0.0, "Z_C2": 0.0})

    # -- A connected consumer NO share was declared for: the policy hands out
    # -- less than the whole supply, and what is published must follow it down
    add_cluster(
        system,
        "UND",
        10.0,
        {"UND_C1": 1.0, "UND_C2": 4.0, "UND_C3": 100.0},
        policy=dict(
            allocation="shares",
            allocation_shares={"UND_C1": 0.5, "UND_C2": 0.5},
        ),
    )

    # -- An empty capacity, and a stocked one, both asked for 10 while 4 arrives
    for prefix, init in (("EMPTY", 0.0), ("STOCK", 500.0)):
        system.add_component(name=f"{prefix}_SRC", cls="AllocSource", rate=4.0)
        system.add_component(
            name=f"{prefix}_TANK", cls="AllocTank", volume=1000.0, init=init
        )
        system.add_component(name=f"{prefix}_C", cls="AllocConsumer", demand=10.0)
        system.connect_flow(
            source=f"{prefix}_SRC", target=f"{prefix}_TANK", flow_name="q"
        )
        system.connect_flow(
            source=f"{prefix}_TANK", target=f"{prefix}_C", flow_name="q"
        )

    # -- One volume holding two constituents, one of them asked for beyond
    # -- what transits through it (R35)
    system.add_component(name="MIXT_SRC", cls="AllocSource", flow="feed", rate=2.0)
    system.add_component(name="MIXT", cls="AllocMixTank")
    system.add_component(name="MIXT_H", cls="AllocConsumer", flow="h", demand=5.0)
    system.add_component(name="MIXT_O", cls="AllocConsumer", flow="o", demand=1.0)
    system.connect_flow(source="MIXT_SRC", target="MIXT", flow_name="feed")
    system.connect_flow(source="MIXT", target="MIXT_H", flow_name="h")
    system.connect_flow(source="MIXT", target="MIXT_O", flow_name="o")

    # -- The demand mapping of R34, with a plentiful and with a scarce input
    for prefix, rate_a in (("MAP", 100.0), ("SC", 3.0)):
        system.add_component(
            name=f"{prefix}_A", cls="AllocSource", flow="a", rate=rate_a
        )
        system.add_component(
            name=f"{prefix}_B", cls="AllocSource", flow="b", rate=100.0
        )
        system.add_component(name=prefix, cls="AllocBlender")
        system.add_component(
            name=f"{prefix}_C", cls="AllocConsumer", flow="x", demand=6.0
        )
        system.connect_flow(source=f"{prefix}_A", target=prefix, flow_name="a")
        system.connect_flow(source=f"{prefix}_B", target=prefix, flow_name="b")
        system.connect_flow(source=prefix, target=f"{prefix}_C", flow_name="x")

    # -- A catalyst coefficient of 0, on a rule whose output NOBODY is
    # -- connected to: the scale is unbounded, and 0 x inf is NaN
    system.add_component(name="CAT_SRC", cls="AllocSource", flow="cat", rate=10.0)
    system.add_component(name="CAT_FUEL", cls="AllocSource", flow="fuel", rate=10.0)
    system.add_component(name="CATU", cls="AllocCatalysed")
    system.connect_flow(source="CAT_SRC", target="CATU", flow_name="cat")
    system.connect_flow(source="CAT_FUEL", target="CATU", flow_name="fuel")

    add_clock(system.comp["AE16_SRC"])

    return system


def run_allocation_scenario(obs):
    """Drive the allocation system one step and snapshot every reading."""
    system = build_allocation_system()

    system.isimu_start()
    system.isimu_step_forward()

    obs["time"] = system.currentTime()

    obs["allocated"] = {
        prefix: dict(system.comp[f"{prefix}_SRC"].flows_out["q"].allocated)
        for prefix in CLUSTERS
    }
    obs["out"] = {prefix: out_value(system, f"{prefix}_SRC") for prefix in CLUSTERS}
    obs["received"] = {
        name: delivered(system, name)
        for name in [name for names in CLUSTERS.values() for name in names]
        + ["EMPTY_C", "STOCK_C"]
    }
    obs["mirrored"] = {
        name: system.comp[name].flows_in["q"].var_fed.value()
        for name in ("AE16_C1", "AE16_C2")
    }

    # One volume, two constituents, and a draw beyond what transits
    mix = system.comp["MIXT"].capacities["tank"]
    obs["mixture"] = {
        "qty_h": mix.get_quantity("h"),
        "qty_o": mix.get_quantity("o"),
        "weight_h": mix.weight_of("h"),
        "weight_o": mix.weight_of("o"),
        "transit_h": mix.get_inflow("h"),
        "transit_o": mix.get_inflow("o"),
        "served_h": out_value(system, "MIXT", "h"),
        "served_o": out_value(system, "MIXT", "o"),
        "received_h": delivered(system, "MIXT_H", "h"),
        "received_o": delivered(system, "MIXT_O", "o"),
    }

    # What the producer of AE16 sees its consumers ask for
    ae16_out = system.comp["AE16_SRC"].flows_out["q"]
    obs["ae16_demands"] = dict(ae16_out.consumer_demands())
    obs["ae16_demand_bound"] = ae16_out.get_demand_bound()

    # The two capacities, asked for more than what transits through them
    obs["capacity"] = {}
    for prefix in ("EMPTY", "STOCK"):
        cuve = system.comp[f"{prefix}_TANK"].capacities["cuve"]
        obs["capacity"][prefix] = {
            "empty": cuve.is_empty,
            "inflow": cuve.get_inflow("q"),
            "outflow": cuve.get_outflow("q"),
            "served": out_value(system, f"{prefix}_TANK"),
        }

    # The demand mapping, and the over-claim a scarce input leaves behind
    obs["mapping"] = {}
    for prefix in ("MAP", "SC"):
        obs["mapping"][prefix] = {
            "demand_a": published_demand(system, prefix, "a"),
            "demand_b": published_demand(system, prefix, "b"),
            "required_a": system.comp[prefix].flows_in["a"].demand_required,
            "delivered_a": delivered(system, prefix, "a"),
            "delivered_b": delivered(system, prefix, "b"),
            "x": out_value(system, prefix, "x"),
            "received": delivered(system, f"{prefix}_C", "x"),
        }

    # The catalyst: what a coefficient of 0 claims, and what it does not limit
    obs["catalyst"] = {
        "demand_cat": published_demand(system, "CATU", "cat"),
        "demand_fuel": published_demand(system, "CATU", "fuel"),
        "delivered_cat": delivered(system, "CATU", "cat"),
        "delivered_fuel": delivered(system, "CATU", "fuel"),
        "burn": out_value(system, "CATU", "burn"),
    }

    system.isimu_stop()
    system.deleteSys()


def build_capacity_demand_system():
    """AE11: the same unit, once against a full tank and once with room left."""
    system = muscadet.System(name="DemandCapacityBound")

    for prefix, volume, init in (("FULL", 10.0, 8.0), ("ROOM", 1000.0, 8.0)):
        system.add_component(
            name=f"{prefix}_SRC", cls="AllocSource", flow="w", rate=5.0
        )
        system.add_component(
            name=f"{prefix}_ELEC", cls="AllocSource", flow="elec", rate=2.0
        )
        system.add_component(
            name=prefix, cls="AllocBufferedUnit", volume=volume, init=init
        )
        system.add_component(
            name=f"{prefix}_C", cls="AllocConsumer", flow="x", demand=8.0
        )

        system.connect_flow(source=f"{prefix}_SRC", target=prefix, flow_name="w")
        system.connect_flow(source=f"{prefix}_ELEC", target=prefix, flow_name="elec")
        system.connect_flow(source=prefix, target=f"{prefix}_C", flow_name="x")

    add_clock(system.comp["FULL_SRC"])

    return system


def run_capacity_demand_scenario(obs):
    """Drive until the tank reaches its volume, then read both units."""
    system = build_capacity_demand_system()

    system.isimu_start()

    obs["full_dates"] = []
    for _ in range(8):
        system.isimu_step_forward()
        if system.comp["FULL"].capacities["cuve"].is_full:
            obs["full_dates"].append(system.currentTime())
        if system.currentTime() >= CLOCK_DELAY:
            break

    obs["bound"] = {}
    for prefix in ("FULL", "ROOM"):
        cuve = system.comp[prefix].capacities["cuve"]
        obs["bound"][prefix] = {
            "full": cuve.is_full,
            "published": published_demand(system, prefix, "w"),
            "required": system.comp[prefix].flows_in["w"].demand_required,
            "delivered": out_value(system, f"{prefix}_SRC", "w"),
            "inflow": cuve.get_inflow("w"),
            "outflow": cuve.get_outflow("w"),
            "x": out_value(system, prefix, "x"),
        }

    obs["capacity_time"] = system.currentTime()

    system.isimu_stop()

    # Kept alive for the teardown test, per the module convention.
    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive both systems in turn, snapshotting what each produced."""
    obs = {}

    run_allocation_scenario(obs)
    run_capacity_demand_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# R6, R16 -- an insufficient supply, split
# ----------------------------------------------------------------------


def test_a_short_supply_is_split_in_proportion_to_the_demands(the_run):
    """AE16: 10 to give, demands of 8 and 12, so 4 and 6.

    The default policy needs no declaration at all, and the distributed total is
    the whole available quantity.
    """
    received = the_run["received"]

    assert received["AE16_C1"] == pytest.approx(4.0)
    assert received["AE16_C2"] == pytest.approx(6.0)

    assert the_run["out"]["AE16"] == pytest.approx(10.0)
    assert sum(the_run["allocated"]["AE16"].values()) == pytest.approx(10.0)


def test_the_allocated_share_is_what_the_consumer_reads(the_run):
    """A producer exports ONE value; what a consumer reads is its own share.

    The mirror variable must not report the producer's total either: two
    consumers on one output would then both read 10 and the model would create
    quantity out of nothing.
    """
    mirrored = the_run["mirrored"]

    assert mirrored["AE16_C1"] == pytest.approx(4.0)
    assert mirrored["AE16_C2"] == pytest.approx(6.0)
    assert mirrored["AE16_C1"] + mirrored["AE16_C2"] == pytest.approx(
        the_run["out"]["AE16"]
    )


def test_demand_aggregates_over_the_consumers_of_one_output(the_run):
    """R5: an output reads back what each of its consumers asks for, and the sum."""
    assert the_run["ae16_demands"] == {
        "AE16_C1": pytest.approx(8.0),
        "AE16_C2": pytest.approx(12.0),
    }
    assert the_run["ae16_demand_bound"] == pytest.approx(20.0)


def test_fixed_shares_override_the_demand_proportions(the_run):
    """The same demands as AE16, split 0.7 / 0.3 instead of 8 / 12."""
    received = the_run["received"]

    assert received["SH_C1"] == pytest.approx(7.0)
    assert received["SH_C2"] == pytest.approx(3.0)

    # Proportional would have given 4 and 6: the shares decide, not the demands
    assert received["SH_C1"] != pytest.approx(4.0)
    assert sum(the_run["allocated"]["SH"].values()) == pytest.approx(10.0)


def test_ordered_priorities_serve_in_order_until_the_supply_is_gone(the_run):
    """Three consumers demanding 6 each, 10 to give: 6, then 4, then nothing."""
    received = the_run["received"]

    assert received["PR_C1"] == pytest.approx(6.0)
    assert received["PR_C2"] == pytest.approx(4.0)
    assert received["PR_C3"] == pytest.approx(0.0)

    assert sum(the_run["allocated"]["PR"].values()) == pytest.approx(10.0)


def test_a_custom_python_rule_is_used_over_the_declared_policy(the_run):
    """R17, KD9: the output declares shares of 0.9 / 0.1 AND a Python rule.

    The declared shares would give 6 and 4 once the first consumer is capped at
    its demand; the rule shares alike, so both get 5.
    """
    received = the_run["received"]

    assert received["FUN_C1"] == pytest.approx(5.0)
    assert received["FUN_C2"] == pytest.approx(5.0)

    assert sum(the_run["allocated"]["FUN"].values()) == pytest.approx(10.0)


def test_every_consumer_demanding_zero_delivers_zero(the_run):
    """A producer able to supply 10 that nobody asks anything of delivers nothing.

    The proportional split has a null total here, which must yield zeros rather
    than a division by zero.
    """
    received = the_run["received"]

    assert received["Z_C1"] == pytest.approx(0.0)
    assert received["Z_C2"] == pytest.approx(0.0)

    assert the_run["out"]["Z"] == pytest.approx(0.0)
    assert sum(the_run["allocated"]["Z"].values()) == pytest.approx(0.0)


# ----------------------------------------------------------------------
# Conservation -- what is published is what is distributed
# ----------------------------------------------------------------------


def test_what_an_output_publishes_is_what_its_consumers_receive(the_run):
    """The invariant of every continuous output, whatever its policy.

    ``{f}_fed_out`` is documented as the total value delivered downstream, so
    it must equal the sum of the shares its consumers read back -- under the
    default proportional split, under fixed shares, under priorities and under
    a Python rule alike. A policy handing out less than what was offered must
    drag the published figure down with it, or the difference is quantity that
    left the producer and reached nobody.
    """
    for prefix, consumers in CLUSTERS.items():
        published = the_run["out"][prefix]
        received = sum(the_run["received"][name] for name in consumers)
        allocated = sum(the_run["allocated"][prefix].values())

        assert published == pytest.approx(received, abs=1e-9), (
            f"{prefix}: published {published} but its consumers received " f"{received}"
        )
        assert published == pytest.approx(
            allocated, abs=1e-9
        ), f"{prefix}: published {published} but allocated {allocated}"


def test_a_consumer_with_no_declared_share_does_not_destroy_quantity(the_run):
    """Shares 0.5 / 0.5 declared, and a THIRD consumer connected without one.

    ``shares`` deliberately gives nothing to an undeclared consumer, so the
    split totals 5 of the 10 available: 1 and 4, both capped at their demands,
    and nothing for the third. Publishing the pre-split 10 would put 5 units
    into the model that no consumer ever received.
    """
    allocated = the_run["allocated"]["UND"]
    received = the_run["received"]

    assert received["UND_C1"] == pytest.approx(1.0)
    assert received["UND_C2"] == pytest.approx(4.0)
    assert received["UND_C3"] == pytest.approx(0.0)

    assert sum(allocated.values()) == pytest.approx(5.0)

    # The published total follows the split down, rather than the 10 offered
    assert the_run["out"]["UND"] == pytest.approx(5.0)


# ----------------------------------------------------------------------
# F2 -- the surplus of a capped consumer, redistributed until it converges
# ----------------------------------------------------------------------


def test_a_capped_consumer_releases_its_surplus_to_the_others(the_run):
    """Shares 0.5 / 0.3 / 0.2, a supply of 10, demands of 1, 4 and 100.

    One pass would give 5, 3 and 2, and the first consumer only wants 1. Two
    redistributions later the split is 1, 4 and 5 -- and the distributed total
    is still the whole 10.
    """
    received = the_run["received"]

    assert received["CAP_C1"] == pytest.approx(1.0)
    assert received["CAP_C2"] == pytest.approx(4.0)
    assert received["CAP_C3"] == pytest.approx(5.0)

    assert sum(the_run["allocated"]["CAP"].values()) == pytest.approx(10.0)


def test_one_redistribution_pass_is_not_enough():
    """The same case, pass by pass: the second pass overshoots in its turn.

    Capping the first consumer at 1 and redistributing by renormalised shares
    gives the second 5.4 against a demand of 4, so a further pass is required.
    Asserted away from the engine, where a pass can be looked at on its own.
    """
    shares = {"C1": 0.5, "C2": 0.3, "C3": 0.2}
    demands = {"C1": 1.0, "C2": 4.0, "C3": 100.0}

    def split(available, active):
        return split_shares(available, active, shares)

    trace = []
    allocation = allocate(10.0, demands, split, trace=trace)

    # Pass 1: the declared shares, and the first consumer wants only 1 of its 5
    assert trace[0]["C1"] == pytest.approx(5.0)

    # Pass 2: 9 left, shares renormalised to 0.6 / 0.4 -- and 5.4 for a demand of 4
    assert trace[1]["C2"] == pytest.approx(5.4)

    # Pass 3: 5 left for the only consumer still uncapped
    assert len(trace) == 3
    assert trace[2] == {"C3": pytest.approx(5.0)}

    assert allocation == {
        "C1": pytest.approx(1.0),
        "C2": pytest.approx(4.0),
        "C3": pytest.approx(5.0),
    }
    assert sum(allocation.values()) == pytest.approx(10.0)


def test_the_redistribution_is_bounded_by_the_consumer_count():
    """Every consumer capped, in the worst order: it still terminates and adds up."""
    demands = {f"C{index}": float(index) for index in range(1, 8)}

    allocation = allocate(1000.0, demands, split_proportional)

    # Everyone gets exactly what they asked for: there is more than enough
    assert allocation == {key: pytest.approx(value) for key, value in demands.items()}
    assert sum(allocation.values()) == pytest.approx(sum(demands.values()))


def test_a_priority_tie_falls_back_to_proportional_between_them():
    """Two consumers at one priority split their allotment by demand.

    A priority policy that cannot order two consumers must not fall back to
    declaration order, which would make the split depend on how the model was
    typed rather than on what was asked for.
    """
    demands = {"A": 6.0, "B": 2.0}

    def tied(available, active):
        return split_priority(available, active, {"A": 1, "B": 1})

    assert allocate(4.0, demands, tied) == {
        "A": pytest.approx(3.0),
        "B": pytest.approx(1.0),
    }

    # Ordered, the same demands and the same supply serve A first
    def ordered(available, active):
        return split_priority(available, active, {"A": 1, "B": 2})

    assert allocate(4.0, demands, ordered) == {
        "A": pytest.approx(4.0),
        "B": pytest.approx(0.0),
    }


def test_an_unbounded_demand_is_regularised_to_what_is_available():
    """An output nobody is connected to publishes an unbounded demand upstream.

    A split may never divide by an infinite total, so an unbounded demand is
    regularised to the whole available quantity -- the most any consumer could
    receive.
    """
    allocation = allocate(10.0, {"A": UNBOUNDED, "B": 10.0}, split_proportional)

    assert allocation["A"] == pytest.approx(5.0)
    assert allocation["B"] == pytest.approx(5.0)
    assert sum(allocation.values()) == pytest.approx(10.0)

    assert flow_continuous.UNBOUNDED == math.inf


# ----------------------------------------------------------------------
# R16 -- what a policy refuses at declaration time
# ----------------------------------------------------------------------


def test_shares_that_do_not_sum_to_one_raise_at_declaration():
    """The message names the output flow and the shares it was given."""
    with pytest.raises(ValueError, match="must sum to 1"):
        muscadet.FlowContinuousOut(
            name="q",
            allocation="shares",
            allocation_shares={"C1": 0.5, "C2": 0.4},
        )

    try:
        muscadet.FlowContinuousOut(
            name="steam",
            allocation="shares",
            allocation_shares={"C1": 0.5, "C2": 0.4},
        )
    except ValueError as err:
        message = str(err)
    else:  # pragma: no cover - the raise above is the assertion
        message = ""

    assert "Output flow steam" in message
    assert "0.9" in message
    assert "C1=0.5" in message

    # ... and shares that do sum to one are accepted
    flow = muscadet.FlowContinuousOut(
        name="q",
        allocation="shares",
        allocation_shares={"C1": 0.5, "C2": 0.5},
    )
    assert flow.allocation == flow_continuous.ALLOCATION_SHARES


def test_a_policy_that_cannot_be_applied_raises_at_declaration():
    """A policy name nothing implements, and a policy with nothing declared."""
    with pytest.raises(ValueError, match="unknown allocation policy"):
        muscadet.FlowContinuousOut(name="q", allocation="round_robin")

    with pytest.raises(ValueError, match="needs allocation_shares"):
        muscadet.FlowContinuousOut(name="q", allocation="shares")

    with pytest.raises(ValueError, match="needs allocation_priorities"):
        muscadet.FlowContinuousOut(name="q", allocation="priority")


# ----------------------------------------------------------------------
# R34 -- the demand mapping, and what it deliberately does not do
# ----------------------------------------------------------------------


def test_an_output_demand_maps_onto_the_inputs_in_the_declared_ratio(the_run):
    """3 of ``a`` and 2 of ``b`` make 2 of ``x``, and 6 of ``x`` are asked for.

    The rule has to run at scale 3, so the component asks for 9 of ``a`` and 6
    of ``b`` -- the declared coefficients, in their declared ratio.
    """
    mapping = the_run["mapping"]["MAP"]

    assert mapping["demand_a"] == pytest.approx(9.0)
    assert mapping["demand_b"] == pytest.approx(6.0)
    assert mapping["demand_a"] / mapping["demand_b"] == pytest.approx(3.0 / 2.0)

    # Both producers can supply 100 and deliver exactly what was asked for ...
    assert mapping["delivered_a"] == pytest.approx(9.0)
    assert mapping["delivered_b"] == pytest.approx(6.0)

    # ... so the consumer gets the 6 it asked for
    assert mapping["x"] == pytest.approx(6.0)
    assert mapping["received"] == pytest.approx(6.0)


def test_the_mapping_uses_declared_coefficients_even_when_an_input_is_scarce(the_run):
    """The recorded limitation of R34, asserted as behaviour.

    ``a`` can only be supplied at 3, so the rule runs at scale 1 and uses 2 of
    ``b``. The demand published on ``b`` is nevertheless the nominal 6: the
    mapping reads the declared coefficients, not what is available. Correcting
    it needs a second demand pass or an iterative solve, both outside the
    two-sweep ordering -- Scope Boundaries records it.
    """
    mapping = the_run["mapping"]["SC"]

    # The claim is the nominal one, on both inputs
    assert mapping["demand_a"] == pytest.approx(9.0)
    assert mapping["demand_b"] == pytest.approx(6.0)

    # ``a`` is scarce: 3 arrive against the 9 claimed, so the rule runs at 1
    assert mapping["delivered_a"] == pytest.approx(3.0)
    assert mapping["x"] == pytest.approx(2.0)

    # ... and ``b`` is over-claimed: 6 delivered, 2 used by the rule at scale 1
    assert mapping["delivered_b"] == pytest.approx(6.0)
    consumed_b = mapping["x"] / 2.0 * 2.0
    assert consumed_b == pytest.approx(2.0)
    assert mapping["delivered_b"] > consumed_b


def test_a_catalyst_coefficient_demands_nothing_and_publishes_no_nan(the_run):
    """A ``cons`` coefficient of 0 claims 0, whatever the scale of the rule.

    The rule's output is connected to nobody, so nothing throttles it and the
    scale it would run at is unbounded. Multiplying the coefficient by it
    without a guard gives ``0 * inf`` -- NaN -- and publishes that upstream as
    the catalyst's demand, where it poisons every split it reaches. The two
    halves of one rule must agree: ``rule_scale`` already skips a non-positive
    coefficient, and so must the demand.
    """
    catalyst = the_run["catalyst"]

    # The claim on the catalyst is a real 0, not a NaN and not unbounded
    assert catalyst["demand_cat"] == pytest.approx(0.0)
    assert catalyst["demand_cat"] == catalyst["demand_cat"], "NaN published upstream"
    assert catalyst["delivered_cat"] == pytest.approx(0.0)

    # ... while the flow the rule DOES consume still claims without bound
    assert math.isinf(catalyst["demand_fuel"])

    # ... and the catalyst limits the rule no more than it is drawn down: the
    # unit still burns everything its fuel allows.
    assert catalyst["delivered_fuel"] == pytest.approx(10.0)
    assert catalyst["burn"] == pytest.approx(10.0)


# ----------------------------------------------------------------------
# R7 -- what a capacity does to demand, on both sides
# ----------------------------------------------------------------------


def test_an_empty_capacity_serves_only_what_transits_through_it(the_run):
    """4 arrive, 10 are asked for, and the tank holds nothing to make up the rest."""
    empty = the_run["capacity"]["EMPTY"]

    assert empty["empty"] is True
    assert empty["inflow"] == pytest.approx(4.0)
    assert empty["outflow"] == pytest.approx(4.0)
    assert empty["served"] == pytest.approx(4.0)

    assert the_run["received"]["EMPTY_C"] == pytest.approx(4.0)


def test_a_stocked_capacity_serves_what_is_asked_for(the_run):
    """The same 4 arriving, the same 10 asked for -- and 500 held in the tank.

    This is what demand buys on the output side: without it the tank would serve
    only what enters it, however much stock it sits on.
    """
    stock = the_run["capacity"]["STOCK"]

    assert stock["empty"] is False
    assert stock["inflow"] == pytest.approx(4.0)
    assert stock["outflow"] == pytest.approx(10.0)
    assert stock["served"] == pytest.approx(10.0)

    assert the_run["received"]["STOCK_C"] == pytest.approx(10.0)


def test_a_mixture_serves_a_draw_at_its_raw_quantity_share(the_run):
    """R35: 1 of ``h`` transits and 5 are asked for, out of a shared volume.

    What transits passes straight on; the rest comes out of the stock, and a
    stock of two constituents is drawn at its RAW composition. ``h`` therefore
    gets its share of the draw, not the 5 it asked for -- one volume cannot
    serve a pure constituent. The weights, 1 and 3, would give a different
    number, which is what makes the raw share the assertion here.
    """
    mixture = the_run["mixture"]
    assert the_run["time"] == pytest.approx(CLOCK_DELAY)

    # What is asked for, over and above what transits: 4 of h, none of o
    beyond = (5.0 - mixture["transit_h"]) + (1.0 - mixture["transit_o"])
    assert mixture["transit_h"] == pytest.approx(1.0)
    assert beyond == pytest.approx(4.0)

    total = mixture["qty_h"] + mixture["qty_o"]
    raw_share = mixture["qty_h"] / total
    weighted_share = (mixture["qty_h"] * mixture["weight_h"]) / (
        mixture["qty_h"] * mixture["weight_h"] + mixture["qty_o"] * mixture["weight_o"]
    )

    assert mixture["served_h"] == pytest.approx(
        mixture["transit_h"] + beyond * raw_share
    )
    assert mixture["served_h"] != pytest.approx(
        mixture["transit_h"] + beyond * weighted_share
    )

    # ... and asking for a pure constituent does not get one
    assert mixture["served_h"] < 5.0
    assert mixture["received_h"] == pytest.approx(mixture["served_h"])
    assert mixture["received_o"] == pytest.approx(1.0)


def test_a_full_capacity_reduces_the_demand_it_exports_upstream(the_run):
    """AE11: the tank reaches its volume, and its producer delivers less.

    Both units are identical and consume 2 of ``w`` (their ``elec`` is scarce),
    and both ask their own producer for the nominal 8. The one whose tank is at
    its volume publishes only what currently leaves that tank, so its producer
    delivers 2 instead of the 5 it could supply -- while the unit with room left
    still draws the full 5.
    """
    full = the_run["bound"]["FULL"]
    room = the_run["bound"]["ROOM"]

    assert full["full"] is True
    assert room["full"] is False

    # The internal claim is the same on both: 8 of x asked for, 1 of w per x
    assert full["required"] == pytest.approx(8.0)
    assert room["required"] == pytest.approx(8.0)

    # ... but a full tank exports only what leaves it
    assert full["published"] == pytest.approx(2.0)
    assert room["published"] == pytest.approx(8.0)

    # ... so its producer delivers less, though it could supply 5
    assert full["delivered"] == pytest.approx(2.0)
    assert room["delivered"] == pytest.approx(5.0)
    assert full["delivered"] < room["delivered"]

    # The full tank is in balance: what enters it is what leaves it
    assert full["inflow"] == pytest.approx(full["outflow"])

    # Both produce the same 2 of x: the bound is on what is DELIVERED upstream,
    # not on what the rules draw from the stock the tank holds
    assert full["x"] == pytest.approx(2.0)
    assert room["x"] == pytest.approx(2.0)


def test_the_capacity_reaches_its_volume_at_the_expected_date(the_run):
    """8 held of 10, filled at 5 and drained at 2: full at t = 2 / 3."""
    assert the_run["full_dates"], "the tank never reached its volume"

    assert the_run["full_dates"][0] == pytest.approx(2.0 / 3.0, abs=CROSSING_TOL)
    assert the_run["capacity_time"] == pytest.approx(CLOCK_DELAY)


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
