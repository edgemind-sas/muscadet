"""A bound is crossed by the solver, and the state is put back on it.

Two mechanisms meet here, and both were wrong in ways no existing test could
see, because nothing asserted on the crossing itself.

**A finite request was capped by what the volume holds**, comparing a rate (the
shortfall per unit time) with a quantity. The implicit "per one unit of time"
that makes such a comparison typecheck turned the emptying of a tank into an
exponential relaxation of time constant ONE TIME UNIT, whatever the physics.
Now only an UNBOUNDED request is capped -- the one case where a quantity has to
stand in for a rate -- and the empty/full automaton stops the crossing exactly.

**The solver stops just PAST a bound**, within ``dtCond``, and nothing pulled
the state back, so the residue was permanent. ``Capacity.clamp_to_bounds`` is
the PDMP reset map that takes it back, and at the full bound it charges the
excess to the constituents that were flowing in rather than to every one.
"""

import math

import cod3s
import muscadet
import pytest
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

# One tank, drained faster than it is filled: 20 at -2 per unit empties at 10.
DRAIN_RATE = 4.0
DRAIN_DEMAND = 6.0
DRAIN_INIT = 20.0
DRAIN_EMPTIES_AT = 10.0

# One tank, filled faster than it is drawn: 40 to 60 at +6 fills at 3 + 1/3.
FILL_RATE = 10.0
FILL_DEMAND = 4.0
FILL_VOLUME = 60.0
FILL_INIT = 40.0

GRID = 0.25
TOL = 1e-9


class BcSpectatorTank(muscadet.ObjFlow):
    """One volume, two constituents, and only one of them ever moves.

    ``syrup`` has no producer and no consumer: whatever the water does, its
    quantity is a constant of the model and any rule that changes it is wrong.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="water")
        self.add_flow_continuous_in(name="syrup")
        self.add_flow_continuous_out(name="water")
        self.add_flow_continuous_out(name="syrup")
        self.add_capacity(
            name="mix",
            flows=[{"name": "water", "weight": 1}, {"name": "syrup", "weight": 2}],
            capacity=100.0,
            side="in",
            fill_rate=math.inf,
            content_init={"water": 40.0, "syrup": 20.0},
        )


def drive(system, snap, horizon, step=GRID):
    """Walk an observation grid, sampling AT each stop."""
    trace = []
    t = 0.0
    while t < horizon:
        t += step
        system.isimu_step_to(t, on_stop=lambda kind, at, fired: trace.append(snap(at)))
    return trace


def build_drain(name, dt_cond=None):
    system = muscadet.System(name=name)
    system.add_component(name="SRC", cls="SourceContinuous", flow="w", rate=DRAIN_RATE)
    system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        flow="w",
        capacity=1000.0,
        capacity_name="tk",
        fill_rate=math.inf,
        content_init={"w": DRAIN_INIT},
    )
    system.add_component(
        name="USER", cls="ConsumerContinuous", flow="w", demand=DRAIN_DEMAND
    )
    system.connect_flow(source="SRC", target="TANK", flow_name="w")
    system.connect_flow(source="TANK", target="USER", flow_name="w")
    if dt_cond is not None:
        system.get_or_create_pdmp_manager().setDtCond(dt_cond)
    return system


def drain_snapshot(system, at):
    tank = system.comp["TANK"].capacities["tk"]
    return {
        "time": at,
        "level": tank.get_quantity("w"),
        "served": system.comp["USER"].flows_in["w"].get_delivered(),
        "empty": tank.is_empty,
    }


@pytest.fixture(scope="module")
def the_run():
    """Every observation this module asserts on, each system deleted at once."""
    obs = {}

    # -- draining past the empty bound -----------------------------------
    system = build_drain("BcDrain")
    try:
        system.isimu_start()
        obs["drain"] = drive(
            system, lambda at: drain_snapshot(system, at), horizon=12.0
        )
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()

    # -- the same, with the crossing search two decades finer -------------
    system = build_drain("BcDrainFine", dt_cond=1e-6)
    try:
        system.isimu_start()
        obs["drain_fine"] = drive(
            system, lambda at: drain_snapshot(system, at), horizon=12.0
        )
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()

    # -- filling past the full bound --------------------------------------
    system = muscadet.System(name="BcFill")
    try:
        system.add_component(
            name="SRC", cls="SourceContinuous", flow="w", rate=FILL_RATE
        )
        system.add_component(
            name="TANK",
            cls="CapacityContinuous",
            flow="w",
            capacity=FILL_VOLUME,
            capacity_name="tk",
            fill_rate=math.inf,
            content_init={"w": FILL_INIT},
        )
        system.add_component(
            name="USER", cls="ConsumerContinuous", flow="w", demand=FILL_DEMAND
        )
        system.connect_flow(source="SRC", target="TANK", flow_name="w")
        system.connect_flow(source="TANK", target="USER", flow_name="w")
        system.isimu_start()
        tank = system.comp["TANK"].capacities["tk"]
        obs["fill"] = drive(
            system,
            lambda at: {
                "time": at,
                "level": tank.get_quantity("w"),
                "full": tank.is_full,
            },
            horizon=6.0,
        )
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()

    # -- a spectator constituent, over the same full bound ------------------
    system = muscadet.System(name="BcSpectator")
    try:
        system.add_component(
            name="SRC", cls="SourceContinuous", flow="water", rate=10.0
        )
        system.add_component(name="TANK", cls="BcSpectatorTank")
        system.connect_flow(source="SRC", target="TANK", flow_name="water")
        system.isimu_start()
        tank = system.comp["TANK"].capacities["mix"]
        obs["spectator"] = drive(
            system,
            lambda at: {
                "time": at,
                "water": tank.get_quantity("water"),
                "syrup": tank.get_quantity("syrup"),
                "fill": tank.total_fill(),
                "full": tank.is_full,
            },
            horizon=3.0,
        )
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()

    return obs


# ---------------------------------------------------------------------------
# The crossing is a corner, not a relaxation
# ---------------------------------------------------------------------------
def test_the_tank_drains_at_its_true_rate_until_the_bound(the_run):
    """A finite request is no longer capped by what the volume holds.

    Capped, the drain decayed exponentially from the moment the stock fell
    below the shortfall: the level read 1.213 at t=9.5 where the physics says
    1.0, and the service was already degraded there.
    """
    before = [row for row in the_run["drain"] if row["time"] <= DRAIN_EMPTIES_AT - GRID]

    for row in before:
        expected = DRAIN_INIT - (DRAIN_DEMAND - DRAIN_RATE) * row["time"]
        assert row["level"] == pytest.approx(expected, abs=1e-6)
        # Full service the whole way down: nothing is short-served early.
        assert row["served"] == pytest.approx(DRAIN_DEMAND)


def test_the_service_falls_sharply_at_the_bound(the_run):
    """Before the crossing the consumer is served in full, after it exactly
    what the source delivers. The transition is one grid step wide, not four
    time units."""
    trace = the_run["drain"]
    served_full = [row for row in trace if row["served"] > DRAIN_RATE + 1e-6]
    served_starved = [row for row in trace if row["served"] <= DRAIN_RATE + 1e-6]

    assert served_full and served_starved
    assert served_full[-1]["time"] == pytest.approx(DRAIN_EMPTIES_AT)
    assert served_starved[0]["time"] == pytest.approx(DRAIN_EMPTIES_AT + GRID)
    for row in served_starved:
        assert row["served"] == pytest.approx(DRAIN_RATE)
        assert row["empty"] is True


# ---------------------------------------------------------------------------
# The reset map
# ---------------------------------------------------------------------------
def test_the_level_lands_exactly_on_the_empty_bound(the_run):
    """Without the reset map the level sat at -0.00086 for the rest of the run."""
    levels = [row["level"] for row in the_run["drain"]]

    assert min(levels) >= -TOL
    settled = [row for row in the_run["drain"] if row["empty"]]
    assert settled
    for row in settled:
        assert row["level"] == pytest.approx(0.0, abs=TOL)


def test_the_level_lands_exactly_on_the_full_bound(the_run):
    """Same at the other bound, where the residue was +0.0026."""
    levels = [row["level"] for row in the_run["fill"]]

    assert max(levels) <= FILL_VOLUME + TOL
    settled = [row for row in the_run["fill"] if row["full"]]
    assert settled
    for row in settled:
        assert row["level"] == pytest.approx(FILL_VOLUME, abs=TOL)


def test_the_bound_holds_whatever_the_crossing_precision(the_run):
    """``dtCond`` governs how far past a bound the solver stops, so a coarse
    search left a bigger residue. The reset map removes it either way, and the
    trajectory before the crossing is the same."""
    coarse = the_run["drain"]
    fine = the_run["drain_fine"]

    assert min(row["level"] for row in fine) >= -TOL
    for rough, sharp in zip(coarse, fine):
        assert rough["time"] == pytest.approx(sharp["time"])
        assert rough["level"] == pytest.approx(sharp["level"], abs=1e-6)


# ---------------------------------------------------------------------------
# Who pays for the excess
# ---------------------------------------------------------------------------
def test_a_constituent_at_rest_pays_nothing_for_the_excess(the_run):
    """The volume is shared, so scaling every constituent back is the tempting
    rule. It takes matter from a constituent that never moved: measured at
    0.0013 of syrup removed by a water overflow, which no consumer received and
    no balance records, and which accumulates over every crossing."""
    trace = the_run["spectator"]

    assert any(row["full"] for row in trace), "the tank never reached its bound"
    for row in trace:
        assert row["syrup"] == pytest.approx(20.0, abs=TOL)


def test_the_paying_constituent_lands_the_volume_on_its_bound(the_run):
    """Charging one constituent still has to restore the volume exactly."""
    settled = [row for row in the_run["spectator"] if row["full"]]

    assert settled
    for row in settled:
        assert row["fill"] == pytest.approx(1.0, abs=1e-12)
        # water carries the whole correction: 100 - 2*20 at weight 1.
        assert row["water"] == pytest.approx(60.0, abs=TOL)


# ---------------------------------------------------------------------------
# What the cap is still there for
# ---------------------------------------------------------------------------
def test_an_unbounded_demand_is_still_answered_from_the_stock():
    """The cap has a second job, and removing it wholesale produced NaN.

    ``serve_limit`` reports a stocked capacity as unbounded, a statement about
    the absence of a bound rather than a quantity; the cap is where it becomes
    one. Without it ``inf - transit`` reaches ``split_draw`` and ``inf * share``
    is NaN, which propagates into every level downstream.
    """
    system = muscadet.System(name="BcUnbounded")
    try:
        system.add_component(
            name="TANK",
            cls="CapacityContinuous",
            flow="w",
            capacity=100.0,
            capacity_name="tk",
            fill_rate=0.0,
            content_init={"w": 50.0},
        )
        system.add_component(
            name="USER", cls="ConsumerContinuous", flow="w", demand=math.inf
        )
        system.connect_flow(source="TANK", target="USER", flow_name="w")
        system.isimu_start()

        levels, served = [], []
        t = 0.0
        while t < 2.0:
            t += 0.5
            system.isimu_step_to(
                t,
                on_stop=lambda kind, at, fired: (
                    levels.append(
                        system.comp["TANK"].capacities["tk"].get_quantity("w")
                    ),
                    served.append(system.comp["USER"].flows_in["w"].get_delivered()),
                ),
            )
        system.isimu_stop()

        assert levels and served
        assert not any(math.isnan(value) for value in levels), levels
        assert not any(math.isnan(value) for value in served), served
        # And the volume is never conjured below empty by the unbounded ask.
        assert min(levels) >= -TOL
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_delete():
    """Each scenario deletes its own system; this closes the session."""
    cod3s.terminate_session()
