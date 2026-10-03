"""A claim weighs no more than the quantity available.

A split proportional to the claims reads each claim as a weight. Up to 5.10.0
only an UNBOUNDED claim was truncated at the quantity available, so a finite
claim far above the supply outweighed everyone else, and the split was
discontinuous at infinity: a supply of 5 shared by a consumer asking 2 and a
consumer asking ``D`` served the first 0.83, 0.01 and 1e-5 for ``D`` = 10, 1000
and 1e6, then 1.43 for an unbounded ``D``. Since 5.11.0 every claim is truncated
at the quantity available before the split reads it. What is asserted here:

* the split itself, without an engine: the proportional policy moves, shares
  and priorities do not;
* the same on a running model, whether the consumer is wired to the source
  directly or through a pass-through pipe;
* the published demand is left as declared: the truncation belongs to the
  split, and a shortfall is read against what the consumer needs.

PyCATSHOO forbids more than one live system per process: one system carries
every scenario, and the sweeps are driven by hand once the session has started,
as ``test_capability_demand_001`` drives them.
"""

import math

import cod3s
import pytest

import muscadet
from muscadet.flow_continuous import allocate, split_priority, split_shares

#: What the shared source delivers.
CTS_SUPPLY = 5.0
#: What the modest consumer asks for.
CTS_NEED = 2.0
#: What the greedy consumer asks for, scenario by scenario.
CTS_ASKS = {"TEN": 10.0, "THOUSAND": 1000.0, "MILLION": 1e6, "UNBOUNDED": math.inf}
#: Both claims weigh at most the supply, so the split is 5 against 2.
CTS_GREEDY_SHARE = CTS_SUPPLY * CTS_SUPPLY / (CTS_SUPPLY + CTS_NEED)
CTS_MODEST_SHARE = CTS_SUPPLY * CTS_NEED / (CTS_SUPPLY + CTS_NEED)
CTS_SHAPES = ("direct", "pipe")

CTS_ROUNDS = 3


# ----------------------------------------------------------------------
# The split, without an engine
# ----------------------------------------------------------------------


@pytest.mark.parametrize("asked", list(CTS_ASKS.values()))
def test_a_claim_above_the_supply_weighs_as_the_supply(asked):
    split = allocate(CTS_SUPPLY, {"greedy": asked, "modest": CTS_NEED})

    assert split["greedy"] == pytest.approx(CTS_GREEDY_SHARE)
    assert split["modest"] == pytest.approx(CTS_MODEST_SHARE)


def test_claims_below_the_supply_split_as_declared():
    """Nothing to truncate: 2 and 3 of a supply of 4 are weighed 2 : 3."""
    split = allocate(4.0, {"a": 2.0, "b": 3.0})

    assert split == {"a": pytest.approx(1.6), "b": pytest.approx(2.4)}


def test_fixed_shares_do_not_move():
    def shares(available, active):
        return split_shares(available, active, {"a": 0.5, "b": 0.5})

    split = allocate(CTS_SUPPLY, {"a": 1e6, "b": CTS_NEED}, shares)

    assert split == {"a": pytest.approx(3.0), "b": pytest.approx(2.0)}


def test_an_ordered_priority_does_not_move():
    def ordered(available, active):
        return split_priority(available, active, {"a": 1, "b": 2})

    split = allocate(CTS_SUPPLY, {"a": 1e6, "b": CTS_NEED}, ordered)

    assert split == {"a": pytest.approx(CTS_SUPPLY), "b": pytest.approx(0.0)}


# ----------------------------------------------------------------------
# The same on a running model
# ----------------------------------------------------------------------


class CtsSource(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="w", var_fed_default=CTS_SUPPLY)


class CtsPipe(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="w")
        self.add_flow_continuous_out(name="w")


class CtsSink(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="w", var_demand_default=kwargs["demand"])


def add_scenario(system, prefix, asked, shape):
    """One source, a modest consumer wired to it, a greedy one directly or not."""
    system.add_component(name=f"{prefix}_SRC", cls="CtsSource")
    system.add_component(name=f"{prefix}_MODEST", cls="CtsSink", demand=CTS_NEED)
    system.connect_flow(
        source=f"{prefix}_SRC", target=f"{prefix}_MODEST", flow_name="w"
    )
    system.add_component(name=f"{prefix}_GREEDY", cls="CtsSink", demand=asked)
    if shape == "pipe":
        system.add_component(name=f"{prefix}_PIPE", cls="CtsPipe")
        system.connect_flow(
            source=f"{prefix}_SRC", target=f"{prefix}_PIPE", flow_name="w"
        )
        system.connect_flow(
            source=f"{prefix}_PIPE", target=f"{prefix}_GREEDY", flow_name="w"
        )
    else:
        system.connect_flow(
            source=f"{prefix}_SRC", target=f"{prefix}_GREEDY", flow_name="w"
        )


SCENARIOS = [(key, shape) for key in CTS_ASKS for shape in CTS_SHAPES]


@pytest.fixture(scope="module")
def the_run():
    system = muscadet.System(name="ClaimTruncatedAtSupply")
    for key, shape in SCENARIOS:
        add_scenario(system, f"{key}_{shape}", CTS_ASKS[key], shape)

    first = f"{SCENARIOS[0][0]}_{SCENARIOS[0][1]}_SRC"
    system.comp[first].add_atm2states(
        name="cts_clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": 1.0},
        cond_occ_21=False,
    )

    obs = {}
    try:
        system.isimu_start()
        order = system.equation_order
        for _ in range(CTS_ROUNDS):
            for name in order.capability_order:
                system.comp[name].compute_capability()
            for name in order.demand_order:
                system.comp[name].compute_demand()
            for name in order.production_order:
                system.comp[name].compute_production()

        obs["served"] = {
            (key, shape): {
                role: system.comp[f"{key}_{shape}_{role}"].flows_in["w"].get_delivered()
                for role in ("GREEDY", "MODEST")
            }
            for key, shape in SCENARIOS
        }
        obs["published"] = {
            key: system.comp[f"{key}_direct_GREEDY"].flows_in["w"].var_demand.value()
            for key in CTS_ASKS
        }
        system.isimu_stop()
    finally:
        obs["system"] = system

    return obs


@pytest.mark.parametrize("key,shape", SCENARIOS)
def test_the_split_does_not_depend_on_how_far_past_the_supply_a_claim_goes(
    the_run, key, shape
):
    """Up to 5.10.0 the modest consumer got 0.83, 0.01, 1e-5, then 1.43."""
    served = the_run["served"][(key, shape)]

    assert served["GREEDY"] == pytest.approx(CTS_GREEDY_SHARE)
    assert served["MODEST"] == pytest.approx(CTS_MODEST_SHARE)


@pytest.mark.parametrize("key", [key for key in CTS_ASKS if key != "UNBOUNDED"])
def test_the_published_demand_stays_as_declared(the_run, key):
    """The truncation belongs to the split: the need is still what is read."""
    assert the_run["published"][key] == pytest.approx(CTS_ASKS[key])


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
