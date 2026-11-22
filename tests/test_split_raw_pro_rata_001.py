"""The default split weighs every consumer by the demand it declares.

A proportional split reads each claim as a weight, and the weight is the claim
as declared: consumers in parallel on a saturating source are all served the
same fraction of what they need. A supply of 12 shared by claims of 6 and 18
serves 3 and 9, half of each.

From 5.11.0 to 5.23.0 a claim was truncated at the quantity available before
the split read it, which served 4 and 8 and favoured the small consumer. What
is asserted here:

* the split itself, without an engine: the raw pro rata, its limit at an
  unbounded claim, and the policies that never read a claim as a weight;
* the same on a running model, whether the consumer is wired to the source
  directly or through a pass-through pipe;
* the published demand is the one declared.

The limit is continuous. A supply of 5 shared by a consumer asking 2 and a
consumer asking ``D`` serves the first 0.83, 0.01 and 1e-5 for ``D`` = 10, 1000
and 1e6, and nothing for an unbounded ``D``: an open outlet takes everything.

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
RPR_SUPPLY = 5.0
#: What the modest consumer asks for.
RPR_NEED = 2.0
#: What the greedy consumer asks for, scenario by scenario.
RPR_ASKS = {"TEN": 10.0, "THOUSAND": 1000.0, "MILLION": 1e6, "UNBOUNDED": math.inf}
RPR_SHAPES = ("direct", "pipe")

RPR_ROUNDS = 3

#: The engine stores a quantity in single precision.
RPR_REL = 1e-5


def modest_share(asked):
    """What the modest consumer is served beside a rival asking ``asked``."""
    if math.isinf(asked):
        return 0.0

    return RPR_SUPPLY * RPR_NEED / (RPR_NEED + asked)


# ----------------------------------------------------------------------
# The split, without an engine
# ----------------------------------------------------------------------


def test_a_shortage_is_shared_at_one_service_ratio():
    """12 for claims of 6 and 18: half of each, where truncation gave 4 and 8."""
    split = allocate(12.0, {"small": 6.0, "large": 18.0})

    assert split == {"small": pytest.approx(3.0), "large": pytest.approx(9.0)}


@pytest.mark.parametrize("asked", list(RPR_ASKS.values()))
def test_a_claim_weighs_what_it_declares(asked):
    split = allocate(RPR_SUPPLY, {"greedy": asked, "modest": RPR_NEED})

    assert split["modest"] == pytest.approx(modest_share(asked))
    assert split["greedy"] == pytest.approx(RPR_SUPPLY - modest_share(asked))


def test_an_unbounded_claim_is_the_limit_of_a_growing_one():
    """No jump at infinity: the modest share falls to the zero it ends at."""
    shares = [
        allocate(RPR_SUPPLY, {"greedy": asked, "modest": RPR_NEED})["modest"]
        for asked in (1e3, 1e6, 1e9, 1e12, math.inf)
    ]

    assert shares == sorted(shares, reverse=True)
    assert shares[-2] == pytest.approx(0.0, abs=1e-10)
    assert shares[-1] == 0.0


def test_unbounded_claims_share_everything_equally():
    split = allocate(9.0, {"a": math.inf, "b": 4.0, "c": math.inf, "d": 1.0})

    assert split == {"a": 4.5, "b": 0.0, "c": 4.5, "d": 0.0}
    assert all(math.isfinite(quantity) for quantity in split.values())


def test_an_ample_supply_serves_every_claim_in_full():
    split = allocate(100.0, {"a": 2.0, "b": 3.0})

    assert split == {"a": pytest.approx(2.0), "b": pytest.approx(3.0)}


def test_fixed_shares_do_not_read_the_claims():
    def shares(available, active):
        return split_shares(available, active, {"a": 0.5, "b": 0.5})

    for asked in (1e6, math.inf):
        split = allocate(RPR_SUPPLY, {"a": asked, "b": RPR_NEED}, shares)

        assert split == {"a": pytest.approx(3.0), "b": pytest.approx(2.0)}


def test_an_ordered_priority_does_not_read_the_claims():
    def ordered(available, active):
        return split_priority(available, active, {"a": 1, "b": 2})

    for asked in (1e6, math.inf):
        split = allocate(RPR_SUPPLY, {"a": asked, "b": RPR_NEED}, ordered)

        assert split == {"a": pytest.approx(RPR_SUPPLY), "b": pytest.approx(0.0)}


def test_a_priority_tie_is_split_at_the_raw_pro_rata():
    def tied(available, active):
        return split_priority(available, active, {"a": 1, "b": 1})

    split = allocate(12.0, {"a": 6.0, "b": 18.0}, tied)

    assert split == {"a": pytest.approx(3.0), "b": pytest.approx(9.0)}


def test_a_custom_split_returning_a_non_finite_quantity_is_refused():
    """A custom rule receives the claims as declared, an unbounded one included."""

    def by_claim(available, active):
        total = sum(active.values())
        return {key: available * claim / total for key, claim in active.items()}

    with pytest.raises(ValueError, match="non-finite"):
        allocate(RPR_SUPPLY, {"a": math.inf, "b": RPR_NEED}, by_claim)


# ----------------------------------------------------------------------
# The same on a running model
# ----------------------------------------------------------------------


class RprSource(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="w", var_fed_default=RPR_SUPPLY)


class RprPipe(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="w")
        self.add_flow_continuous_out(name="w")


class RprSink(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="w", var_demand_default=kwargs["demand"])


def add_scenario(system, prefix, asked, shape):
    """One source, a modest consumer wired to it, a greedy one directly or not."""
    system.add_component(name=f"{prefix}_SRC", cls="RprSource")
    system.add_component(name=f"{prefix}_MODEST", cls="RprSink", demand=RPR_NEED)
    system.connect_flow(
        source=f"{prefix}_SRC", target=f"{prefix}_MODEST", flow_name="w"
    )
    system.add_component(name=f"{prefix}_GREEDY", cls="RprSink", demand=asked)
    if shape == "pipe":
        system.add_component(name=f"{prefix}_PIPE", cls="RprPipe")
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


SCENARIOS = [(key, shape) for key in RPR_ASKS for shape in RPR_SHAPES]


@pytest.fixture(scope="module")
def the_run():
    system = muscadet.System(name="SplitRawProRata")
    for key, shape in SCENARIOS:
        add_scenario(system, f"{key}_{shape}", RPR_ASKS[key], shape)

    first = f"{SCENARIOS[0][0]}_{SCENARIOS[0][1]}_SRC"
    system.comp[first].add_atm2states(
        name="rpr_clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": 1.0},
        cond_occ_21=False,
    )

    obs = {}
    try:
        system.isimu_start()
        order = system.equation_order
        for _ in range(RPR_ROUNDS):
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
            for key in RPR_ASKS
        }
        system.isimu_stop()
    finally:
        obs["system"] = system

    return obs


@pytest.mark.parametrize("key,shape", SCENARIOS)
def test_the_modest_consumer_is_served_its_raw_share(the_run, key, shape):
    served = the_run["served"][(key, shape)]
    expected = modest_share(RPR_ASKS[key])

    assert served["MODEST"] == pytest.approx(expected, rel=RPR_REL, abs=1e-12)
    assert served["GREEDY"] == pytest.approx(RPR_SUPPLY - expected, rel=RPR_REL)


@pytest.mark.parametrize("key", [key for key in RPR_ASKS if key != "UNBOUNDED"])
def test_the_published_demand_is_the_one_declared(the_run, key):
    assert the_run["published"][key] == pytest.approx(RPR_ASKS[key])


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
