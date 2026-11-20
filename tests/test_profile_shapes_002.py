"""Two continuous base shapes for time profiles, and a clamp they all share.

A profile returns a dimensionless FACTOR multiplying an output's nominal rate.
Besides the sinusoid, two shapes cover what a schedule or a measured curve
needs without ever introducing a jump:

* ``TrapezoidProfile``: a square wave whose edges are linear ramps of declared,
  strictly positive duration. A zero ramp would be a step, which the solver
  would integrate across without stopping at it, so it is refused.
* ``PiecewiseLinearProfile``: points joined by segments and repeated every
  period; the last point joins the first one period later, so repetition never
  creates a jump.

Every modulated shape is clamped into ``[value_min, value_max]`` by the same
helper, and the sinusoid keeps the exact values it always had: stored sines
must simulate identically.

The last section runs one simulation with a trapezoid on a continuous output
and reads the delivered quantity at sampled instants.
"""

import math

import cod3s
import pytest

import muscadet
from muscadet.profile import (
    PROFILE_CLASSES,
    PiecewiseLinearProfile,
    SinusoidalProfile,
    TrapezoidProfile,
    build_profile,
)

HOUR = 1.0
MINUTE = HOUR / 60.0

#: The daily square wave: 2 at night, 8 from 8:00 for 10 hours, 5-minute edges.
SQUARE = dict(
    low=2.0,
    high=8.0,
    period=24.0,
    high_duration=10.0,
    ramp_duration=5 * MINUTE,
    phase_shift=8.0,
)

#: A drawn daily curve: three points, factor 0.5 at midnight, 1.5 at 6:00,
#: 1.0 at 12:00, and back to 0.5 at the next midnight.
DRAWN_POINTS = [[0.0, 0.5], [6.0, 1.5], [12.0, 1.0]]
DRAWN_PERIOD = 24.0


def square():
    return TrapezoidProfile(**SQUARE)


def drawn():
    return PiecewiseLinearProfile(points=DRAWN_POINTS, period=DRAWN_PERIOD)


# ----------------------------------------------------------------------
# Square wave with ramps
# ----------------------------------------------------------------------


def test_the_square_wave_is_halfway_up_its_ramp():
    """Half-way through a 5-minute rise from 2 to 8, the factor is 5."""
    assert square().factor(8.0 + 2.5 * MINUTE) == pytest.approx(5.0)


def test_the_square_wave_holds_its_plateau_and_its_floor():
    profile = square()
    assert profile.factor(13.0) == pytest.approx(8.0)
    assert profile.factor(3.0) == pytest.approx(2.0)
    assert profile.factor(23.0) == pytest.approx(2.0)


def test_the_square_wave_falls_on_its_second_ramp():
    profile = square()
    fall_start = 8.0 + 5 * MINUTE + 10.0
    assert profile.factor(fall_start) == pytest.approx(8.0)
    assert profile.factor(fall_start + 2.5 * MINUTE) == pytest.approx(5.0)
    assert profile.factor(fall_start + 5 * MINUTE) == pytest.approx(2.0)


@pytest.mark.parametrize("periods", [1, 3])
def test_the_square_wave_repeats_every_period(periods):
    profile = square()
    for time in (8.0 + 2.5 * MINUTE, 13.0, 3.0):
        assert profile.factor(time + periods * 24.0) == pytest.approx(
            profile.factor(time)
        )


def test_the_square_wave_is_continuous_at_its_corners():
    profile = square()
    eps = 1e-9
    for corner in (8.0, 8.0 + 5 * MINUTE, 18.0 + 5 * MINUTE, 18.0 + 10 * MINUTE):
        assert profile.factor(corner - eps) == pytest.approx(
            profile.factor(corner + eps), abs=1e-5
        )


def test_a_zero_ramp_is_refused_as_a_discontinuity():
    with pytest.raises(ValueError, match="discontinuit"):
        TrapezoidProfile(**dict(SQUARE, ramp_duration=0.0))


def test_a_plateau_and_two_ramps_longer_than_the_period_are_refused():
    with pytest.raises(ValueError, match="period"):
        TrapezoidProfile(**dict(SQUARE, high_duration=23.95))


@pytest.mark.parametrize(
    "override",
    [
        dict(period=0.0),
        dict(period=-1.0),
        dict(high_duration=-1.0),
        dict(low=-0.1),
        dict(high=-0.1),
        dict(ramp_duration=-1.0),
    ],
)
def test_a_square_wave_refuses_an_unusable_declaration(override):
    with pytest.raises(ValueError):
        TrapezoidProfile(**dict(SQUARE, **override))


# ----------------------------------------------------------------------
# Drawn shape
# ----------------------------------------------------------------------


def test_the_drawn_shape_interpolates_inside_a_segment():
    profile = drawn()
    assert profile.factor(3.0) == pytest.approx(1.0)
    assert profile.factor(9.0) == pytest.approx(1.25)
    assert profile.factor(6.0) == pytest.approx(1.5)


def test_the_last_point_joins_the_first_one_period_later():
    profile = drawn()
    # Half-way between (12, 1.0) and (24, 0.5).
    assert profile.factor(18.0) == pytest.approx(0.75)


def test_the_drawn_shape_is_continuous_at_the_period_boundary():
    profile = drawn()
    eps = 1e-9
    left = profile.factor(DRAWN_PERIOD - eps)
    right = profile.factor(DRAWN_PERIOD + eps)
    assert left == pytest.approx(right, abs=1e-6)
    assert right == pytest.approx(0.5, abs=1e-6)


@pytest.mark.parametrize("time", [0.0, 2.0, 7.5, 13.0, 23.9])
def test_the_drawn_shape_repeats_every_period(time):
    profile = drawn()
    assert profile.factor(3 * DRAWN_PERIOD + time) == pytest.approx(
        profile.factor(time)
    )


def test_the_drawn_shape_honours_its_phase_shift():
    shifted = PiecewiseLinearProfile(
        points=DRAWN_POINTS, period=DRAWN_PERIOD, phase_shift=2.0
    )
    assert shifted.factor(11.0) == pytest.approx(drawn().factor(9.0))


@pytest.mark.parametrize(
    "points",
    [
        [[0.0, 1.0], [6.0, 1.0], [6.0, 2.0]],  # two equal instants
        [[0.0, 1.0], [24.0, 2.0]],  # an instant at the period
        [[0.0, 1.0], [30.0, 2.0]],  # an instant beyond the period
        [[1.0, 1.0], [6.0, 2.0]],  # first instant not 0
        [[0.0, 1.0]],  # one point
        [[0.0, 1.0], [6.0, -0.5]],  # a negative factor
        [[0.0, 1.0], [12.0, 2.0], [6.0, 1.0]],  # instants not increasing
        [[0.0, 1.0, 3.0], [6.0, 2.0]],  # not a pair
    ],
)
def test_the_drawn_shape_refuses_an_unusable_point_list(points):
    with pytest.raises(ValueError):
        PiecewiseLinearProfile(points=points, period=DRAWN_PERIOD)


def test_the_drawn_shape_stores_plain_lists_of_floats():
    """The declaration path writes ``points`` as it stands, so it must be JSON."""
    profile = PiecewiseLinearProfile(points=[(0, 1), (6, 2)], period=24)
    assert profile.points == [[0.0, 1.0], [6.0, 2.0]]
    assert all(type(value) is float for pair in profile.points for value in pair)


# ----------------------------------------------------------------------
# Clamp, shared by every modulated shape
# ----------------------------------------------------------------------


def test_the_clamp_bites_on_a_drawn_point():
    profile = PiecewiseLinearProfile(
        points=[[0.0, 0.4], [6.0, 1.2]], period=12.0, value_max=1.0
    )
    assert profile.factor(6.0) == pytest.approx(1.0)
    assert profile.factor(0.0) == pytest.approx(0.4)


def test_the_clamp_bites_on_a_square_wave():
    profile = TrapezoidProfile(**dict(SQUARE, value_min=3.0, value_max=6.0))
    assert profile.factor(13.0) == pytest.approx(6.0)
    assert profile.factor(3.0) == pytest.approx(3.0)
    assert profile.factor(8.0 + 2.5 * MINUTE) == pytest.approx(5.0)


@pytest.mark.parametrize("cls", ["TrapezoidProfile", "PiecewiseLinearProfile"])
def test_the_clamp_refuses_a_negative_floor_and_inverted_bounds(cls):
    params = (
        dict(SQUARE)
        if cls == "TrapezoidProfile"
        else dict(points=DRAWN_POINTS, period=DRAWN_PERIOD)
    )
    with pytest.raises(ValueError, match="value_min"):
        PROFILE_CLASSES[cls](**params, value_min=-1.0)
    with pytest.raises(ValueError, match="wrong way round"):
        PROFILE_CLASSES[cls](**params, value_min=2.0, value_max=1.0)


# ----------------------------------------------------------------------
# The sinusoid is unchanged
# ----------------------------------------------------------------------

SINE_CASES = [
    dict(),
    dict(amplitude=3.0, period=24.0, offset=5.0),
    dict(
        amplitude=10.0,
        period=8.0,
        offset=10.0,
        phase_shift=1.3,
        value_min=2.0,
        value_max=17.5,
    ),
    dict(amplitude=1.0, period=24.0, phase_shift=6.0, value_max=0.7),
]


def reference_sine(
    time,
    amplitude=1.0,
    period=2 * math.pi,
    phase_shift=0.0,
    offset=0.0,
    value_min=0.0,
    value_max=math.inf,
):
    """The sinusoid as muscadet has always computed it, operation for operation."""
    angle = (time - phase_shift) * (2 * math.pi / period)
    value = amplitude * math.sin(angle) + offset
    return min(max(value_min, value), value_max)


@pytest.mark.parametrize("case", SINE_CASES)
def test_the_sinusoid_returns_exactly_its_former_factors(case):
    profile = SinusoidalProfile(**case)
    for i in range(1000):
        time = i * 0.0731 - 3.0
        assert profile.factor(time) == reference_sine(time, **case)


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        dict(cls="TrapezoidProfile", **SQUARE),
        dict(cls="PiecewiseLinearProfile", points=DRAWN_POINTS, period=24.0),
    ],
)
def test_the_mapping_form_builds_each_new_shape(spec):
    profile = build_profile(spec)
    assert type(profile).__name__ == spec["cls"]
    assert muscadet.TrapezoidProfile is TrapezoidProfile
    assert muscadet.PiecewiseLinearProfile is PiecewiseLinearProfile


# ----------------------------------------------------------------------
# One simulation: a trapezoid on a continuous output
# ----------------------------------------------------------------------

#: A short trapezoid so the run stays brief: 0.2 low, 1.0 high, period 4,
#: rising from t=0.5 over 0.5, high for 1, falling over 0.5.
SIM_SHAPE = dict(
    low=0.2,
    high=1.0,
    period=4.0,
    high_duration=1.0,
    ramp_duration=0.5,
    phase_shift=0.5,
)
SIM_RATE = 10.0
SIM_DEMAND = 1e3

#: Stops, and the factor the trapezoid takes at each one.
SIM_EXPECTED = {
    0.75: 0.6,  # mid rising ramp
    1.75: 1.0,  # plateau
    2.25: 0.6,  # mid falling ramp
    3.25: 0.2,  # floor
    4.75: 0.6,  # mid rising ramp, one period later
}
SIM_HORIZON = max(SIM_EXPECTED)


class ShapeTrapezoidPlant(muscadet.ObjFlow):
    """A producer whose nominal rate is scaled by a trapezoid."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name="X",
            var_fed_default=SIM_RATE,
            profile=TrapezoidProfile(**SIM_SHAPE),
        )


class ShapeSink(muscadet.ObjFlow):
    """A consumer asking for more than it can be served."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="X", var_demand_default=SIM_DEMAND)


class ShapeClock(muscadet.ObjFlow):
    """Nothing but dates the interactive session can stop at."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


@pytest.fixture(scope="module")
def the_run():
    system = muscadet.System(name="ProfileShapesSys")
    system.add_component(name="PLANT", cls="ShapeTrapezoidPlant")
    system.add_component(name="SINK", cls="ShapeSink")
    system.connect_flow(source="PLANT", target="SINK", flow_name="X")
    system.add_component(name="CLOCK", cls="ShapeClock")
    for date in SIM_EXPECTED:
        system.comp["CLOCK"].add_atm2states(
            name=f"clock_{str(date).replace('.', '_')}",
            st1="before",
            st2="after",
            occ_law_12={"cls": "delay", "time": date},
            cond_occ_21=False,
        )

    system.isimu_start()
    trace = {}
    for _ in range(40):
        system.isimu_step_forward()
        trace[system.currentTime()] = {
            "plant": system.comp["PLANT"].flows_out["X"].var_fed.value(),
            "profile": system.comp["PLANT"].flows_out["X"].var_profile.value(),
            "sink": system.comp["SINK"].flows_in["X"].var_fed.value(),
        }
        if system.currentTime() >= SIM_HORIZON:
            break
    system.isimu_stop()

    return {"system": system, "trace": trace}


def at(trace, date):
    for time, entry in trace.items():
        if time == pytest.approx(date):
            return entry
    raise AssertionError(f"no stop at t={date}; stops were {sorted(trace)}")


@pytest.mark.parametrize("date", sorted(SIM_EXPECTED))
def test_a_trapezoid_scales_the_delivered_quantity(the_run, date):
    entry = at(the_run["trace"], date)
    factor = SIM_EXPECTED[date]
    assert entry["profile"] == pytest.approx(factor)
    assert entry["plant"] == pytest.approx(SIM_RATE * factor)
    assert entry["sink"] == pytest.approx(SIM_RATE * factor)


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
