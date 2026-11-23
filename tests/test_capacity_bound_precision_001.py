"""A full capacity stays full whatever its volume is worth in storage.

The engine stores a quantity in single precision. ``clamp_to_bounds`` puts the
level back ON the volume after a full crossing, and a volume single precision
rounds DOWN is then read back just under itself: 0.7 comes back 0.699999988.
The transition leaving the full state fired on ``fill < 1``, so it fired at the
very instant the capacity was declared full; the capacity claimed its fill
again, crossed again, and so on.

Measured before the tolerance landed, on a tank of 0.7 fed at 10 and drawn at
4: 2649 bound transitions in one time unit where two are expected, a run about
sixty times slower, and a level read above the declared volume between two
reset maps. Every volume asserted on elsewhere (60, 64, 100, 1000) is exactly
representable, which is why nothing saw it.
"""

import math

import cod3s
import pytest

import muscadet
from muscadet.capacity import BOUND_TOLERANCE
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

SUPPLY = 10.0
DEMAND = 4.0
HORIZON = 20.0

# Both round DOWN in single precision, which is the case that chattered.
INEXACT_VOLUMES = (0.7, 33.3)

# empty -> partial at the first inflow, partial -> full at the bound.
EXPECTED_TRANSITIONS = 2

MIX_VOLUME = 0.7
MIX_SYRUP_WEIGHT = 0.3
MIX_SYRUP = 1.0
MIX_WATER = 0.1


class BpMixTank(muscadet.ObjFlow):
    """One volume, two weighted constituents, only water moving.

    The fill is then a weighted SUM of two stored quantities, so no rounding of
    the volume alone could put it exactly on the bound.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="water")
        self.add_flow_continuous_in(name="syrup")
        self.add_flow_continuous_out(name="water")
        self.add_flow_continuous_out(name="syrup")
        self.add_capacity(
            name="mix",
            flows=[
                {"name": "water", "weight": 1},
                {"name": "syrup", "weight": MIX_SYRUP_WEIGHT},
            ],
            capacity=MIX_VOLUME,
            side="in",
            fill_rate=math.inf,
            content_init={"water": MIX_WATER, "syrup": MIX_SYRUP},
        )


def build_tank(name, volume, content, supply=SUPPLY, demand=DEMAND):
    system = muscadet.System(name=name)
    system.add_component(name="SRC", cls="SourceContinuous", flow="w", rate=supply)
    system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        flow="w",
        capacity=volume,
        capacity_name="tk",
        fill_rate=math.inf,
        content_init={"w": content},
    )
    system.add_component(name="USER", cls="ConsumerContinuous", flow="w", demand=demand)
    system.connect_flow(source="SRC", target="TANK", flow_name="w")
    system.connect_flow(source="TANK", target="USER", flow_name="w")
    return system, system.comp["TANK"].capacities["tk"], "w"


def build_mix(name):
    system = muscadet.System(name=name)
    system.add_component(name="SRC", cls="SourceContinuous", flow="water", rate=SUPPLY)
    system.add_component(name="TANK", cls="BpMixTank")
    system.add_component(
        name="USER", cls="ConsumerContinuous", flow="water", demand=DEMAND
    )
    system.connect_flow(source="SRC", target="TANK", flow_name="water")
    system.connect_flow(source="TANK", target="USER", flow_name="water")
    return system, system.comp["TANK"].capacities["mix"], "water"


def observe(system, capacity, flow, horizon=HORIZON):
    """Run to ``horizon`` and report the bound transitions and the end state."""
    fired = []
    capacity.automaton._bkd.addSensitiveMethod(
        "bp_count_bound_transitions", lambda: fired.append(system.currentTime())
    )

    try:
        system.isimu_start()
        t = 0.0
        while t < horizon:
            t += 1.0
            system.isimu_step_to(t)
        obs = {
            "transitions": len(fired),
            "full": capacity.is_full,
            "empty": capacity.is_empty,
            "fill": capacity.total_fill(),
            "inflow": capacity.get_inflow(flow),
            "outflow": capacity.get_outflow(flow),
        }
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()

    return obs


@pytest.fixture(scope="module")
def the_run():
    """Every observation this module asserts on, each system deleted at once."""
    obs = {}

    for index, volume in enumerate(INEXACT_VOLUMES):
        obs[("fills", volume)] = observe(*build_tank(f"BpFill{index}", volume, 0.0))
        obs[("starts_full", volume)] = observe(
            *build_tank(f"BpStartsFull{index}", volume, volume)
        )

    obs["mix"] = observe(*build_mix("BpMix"))

    # Drawn harder than it is supplied: the tolerance must not hold it full.
    obs["drains"] = observe(
        *build_tank("BpDrains", 0.7, 0.7, supply=DEMAND, demand=SUPPLY)
    )

    return obs


def assert_settled_full(obs, transitions):
    assert obs["transitions"] <= transitions, obs
    assert obs["full"], obs
    assert obs["fill"] == pytest.approx(1.0, abs=BOUND_TOLERANCE), obs
    assert obs["inflow"] == pytest.approx(DEMAND), obs
    assert obs["outflow"] == pytest.approx(DEMAND), obs


@pytest.mark.parametrize("volume", INEXACT_VOLUMES)
def test_a_tank_reaching_an_inexact_volume_stays_full(the_run, volume):
    """Two transitions for the whole run, and a producer throttled to the draw."""
    assert_settled_full(the_run[("fills", volume)], EXPECTED_TRANSITIONS)


@pytest.mark.parametrize("volume", INEXACT_VOLUMES)
def test_a_tank_declared_full_starts_full(the_run, volume):
    """The declared content is stored rounded too, so the initial state is read
    with the same tolerance: a tank declared at its volume has no bound to
    cross and fires nothing."""
    assert_settled_full(the_run[("starts_full", volume)], 0)


def test_a_weighted_mixture_stays_full(the_run):
    """Starts partial, so one transition: partial -> full."""
    assert_settled_full(the_run["mix"], 1)


def test_the_tolerance_does_not_hold_a_draining_tank_full(the_run):
    """Supplied at 4 and drawn at 10, a full tank of 0.7 empties in 0.117."""
    obs = the_run["drains"]
    assert not obs["full"], obs
    assert obs["empty"], obs
    assert obs["outflow"] == pytest.approx(DEMAND), obs


def test_delete():
    """Each scenario deletes its own system; this closes the session."""
    cod3s.terminate_session()
