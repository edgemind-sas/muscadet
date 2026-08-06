"""Time profiles on a continuous output: what a declared curve of time scales.

A continuous output's production used to be either a constant -- the rate the
flow was declared with -- or whatever a transformation rule computed from what
its inputs delivered. A **profile** is the third term: a declared function of
simulation time scaling what the output produces, so a solar curve or a daily
cycle is stated on the flow instead of being wired as a component recomputing
an equation of its own.

The composition rule, which is the whole point
----------------------------------------------
::

    produced = what the rule (or the declared rate) produces
               x  profile(t)
               x  min(out_rate, per-mode deratings)

A profile is a **channel of its own** and is never folded into
``{flow}_out_rate``, because the two must compose differently:

* deratings fold by MINIMUM among themselves -- two modes leaving 0.5 and 0.8
  leave 0.5, order-independently, and repairing the 0.5 one while the 0.8 still
  holds gives 0.8 rather than 1 (R20, KD11);
* a profile MULTIPLIES whatever the deratings left, because it is the size of
  the thing being degraded and not a competing degradation. A panel at 30 % of
  its curve that is also 50 % derated produces 15 %.

Folded into the shared rate the three would fold by minimum, production would
read 50 % instead of 15 %, and nothing would signal it. The trace below is
built so that the product, a minimum over all three, and a product taken over
the deratings too all give DIFFERENT numbers at the same instant.

Why only continuous profiles
----------------------------
A profile is read inside the production equation, at the integration points the
solver chooses. A smooth curve is therefore integrated to the solver's own
accuracy. A discontinuous one is not: nothing makes the solver place a step
boundary at the jump, so it crosses the breakpoint inside a step and overshoots
by up to that step, undetectably. Making that correct needs a watched
transition at every breakpoint, which muscadet does not derive from a Python
callable -- so continuity is **declared**, and a profile that does not attest it
is refused at declaration rather than integrated wrong.

Reading the trace
-----------------
Everything runs in ONE interactive session -- PyCATSHOO forbids more than one
live system per process -- and every stop is snapshotted. The clock stops sit
strictly BETWEEN the mode dates, so both the rate and the quantity are settled
at each of them.
"""

import math

import cod3s
import muscadet
import pytest

from muscadet.flow_continuous import NOMINAL_RATE
from muscadet.kb.continuous import SourceSinusoidalContinuous  # noqa: F401
from muscadet.ordering import compute_equation_order
from muscadet.profile import NOMINAL_FACTOR

#: Horizon the interactive session runs to.
HORIZON = 3.5

#: Stops added so that rate and quantity are read strictly between mode dates.
CLOCK_DATES = (0.5, 1.5, 2.5, 3.5)

#: A mode that never comes back within the horizon.
NEVER = 1e6

#: What the profiled plants produce nominally, and what their consumers ask.
PLANT_RATE = 10.0
SINK_DEMAND = 1e3

#: What the source feeds the rule-driven converter with, throughout.
SUPPLY_RATE = 7.0

#: The two deratings applied to PLANT, and the shared rate a native mode leaves.
DEG_A_RATE = 0.5
DEG_B_RATE = 0.8
SHARED_RATE = 0.5

#: The sinusoid the KB component is declared with: 10 +/- 10 over 8 time units,
#: so it is non-negative without needing the clamp to bite.
SIN_AMPLITUDE = 10.0
SIN_OFFSET = 10.0
SIN_PERIOD = 8.0


def ramp(time):
    """A deliberately trivial CONTINUOUS profile: 0.2 at t=0, +0.2 per unit.

    Linear on purpose. The composition rule is what is under test, and a curve
    whose value at each stop is exact to the last bit keeps the arithmetic of
    the assertions readable: 0.3, 0.5, 0.7, 0.9 at the four stops.
    """
    return 0.2 + 0.2 * time


#: The factor ``ramp`` takes at each clock stop.
RAMP_AT = {date: ramp(date) for date in CLOCK_DATES}


def sinusoid(time):
    """What the KB sinusoidal source is declared to follow, computed here."""
    return SIN_AMPLITUDE * math.sin(2 * math.pi * time / SIN_PERIOD) + SIN_OFFSET


def make_ramp():
    """A fresh ramp profile. One per flow: a profile is declaration state."""
    return muscadet.Profile(ramp, continuous=True)


# ----------------------------------------------------------------------
# Components -- prefixed, since component classes resolve by name globally
# ----------------------------------------------------------------------


class ProfilePlant(muscadet.ObjFlow):
    """A profiled producer holding the rate it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name="X", var_fed_default=PLANT_RATE, profile=make_ramp()
        )


class ProfileFlatPlant(muscadet.ObjFlow):
    """The same producer with NO profile: the parity reference."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="X", var_fed_default=PLANT_RATE)


class ProfileSink(muscadet.ObjFlow):
    """A continuous consumer asking for more than it can ever be served."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="X", var_demand_default=SINK_DEMAND)


class ProfileSource(muscadet.ObjFlow):
    """Feeds the rule-driven converter, and is never itself profiled."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=SUPPLY_RATE)


class ProfileConverter(muscadet.ObjFlow):
    """One unit of ``q`` in, one of ``X`` out -- and the OUTPUT is profiled.

    Pins that a profile scales what a RULE produces, not only a declared rate:
    the two production paths must not diverge.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="X", profile=make_ramp())
        self.add_rules(name="X", rules=[dict(cons={"q": 1}, prod={"X": 1})])


class ProfileClock(muscadet.ObjFlow):
    """Nothing but dates the interactive session can stop at."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


# ----------------------------------------------------------------------
# Building the one system every scenario lives in
# ----------------------------------------------------------------------


def add_clock(comp, date):
    """Give the session a stop at ``date`` it can always step to."""
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="before",
        st2="after",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


#: Every profiled producer whose quantity is traced through the run.
PROFILED = ["PLANT", "BARE", "SHARED", "CONV", "SIN"]


def build_system():
    system = muscadet.System(name="ProfileSys")

    # -- The load-bearing scenario: a profile AND two muscadet deratings on one
    #    output. deg_a degrades at 1 and repairs at 3; deg_b degrades at 2 and
    #    stays. The deratings fold by minimum; the profile multiplies the fold.
    system.add_component(name="PLANT", cls="ProfilePlant")
    system.add_component(name="PLANT_SINK", cls="ProfileSink")
    system.connect_flow(source="PLANT", target="PLANT_SINK", flow_name="X")
    system.comp["PLANT"].add_delay_failure_mode(
        name="deg_a",
        failure_time=1.0,
        failure_effects=[("X", DEG_A_RATE)],
        repair_time=2.0,
    )
    system.comp["PLANT"].add_delay_failure_mode(
        name="deg_b",
        failure_time=2.0,
        failure_effects=[("X", DEG_B_RATE)],
        repair_time=NEVER,
    )

    # -- The profile on its own, nothing derating it.
    system.add_component(name="BARE", cls="ProfilePlant")
    system.add_component(name="BARE_SINK", cls="ProfileSink")
    system.connect_flow(source="BARE", target="BARE_SINK", flow_name="X")

    # -- The profile against the SHARED rate variable, clamped by a mode
    #    declared outside muscadet. The two multiply as well.
    system.add_component(name="SHARED", cls="ProfilePlant")
    system.add_component(name="SHARED_SINK", cls="ProfileSink")
    system.connect_flow(source="SHARED", target="SHARED_SINK", flow_name="X")
    system.add_component(
        cls="ObjMode2S",
        mode_name="shrink",
        targets=["SHARED"],
        occ_law={"cls": "delay", "time": 1.0},
        not_occ_law={"cls": "delay", "time": NEVER},
        occ_effects={"X_out_rate": SHARED_RATE},
    )

    # -- The profile on a RULE-driven output rather than on a declared rate.
    system.add_component(name="SUPPLY", cls="ProfileSource")
    system.add_component(name="CONV", cls="ProfileConverter")
    system.connect_flow(source="SUPPLY", target="CONV", flow_name="q")
    system.add_component(name="CONV_SINK", cls="ProfileSink")
    system.connect_flow(source="CONV", target="CONV_SINK", flow_name="X")

    # -- The shipped sinusoidal source, declared entirely by parameters.
    system.add_component(
        name="SIN",
        cls="SourceSinusoidalContinuous",
        flow="X",
        amplitude=SIN_AMPLITUDE,
        offset=SIN_OFFSET,
        period=SIN_PERIOD,
    )
    system.add_component(name="SIN_SINK", cls="ProfileSink")
    system.connect_flow(source="SIN", target="SIN_SINK", flow_name="X")

    # -- An unprofiled producer beside them all, untouched by any of this.
    system.add_component(name="FLAT", cls="ProfileFlatPlant")
    system.add_component(name="FLAT_SINK", cls="ProfileSink")
    system.connect_flow(source="FLAT", target="FLAT_SINK", flow_name="X")

    system.add_component(name="CLOCK", cls="ProfileClock")
    for date in CLOCK_DATES:
        add_clock(system.comp["CLOCK"], date)

    return system


def snapshot(system):
    """Quantities, rates and published factors at one stop."""
    plant = system.comp["PLANT"].flows_out["X"]

    return {
        "time": system.currentTime(),
        "X": {
            name: system.comp[name].flows_out["X"].var_fed.value() for name in PROFILED
        },
        "profile": {
            name: system.comp[name].flows_out["X"].var_profile.value()
            for name in PROFILED
        },
        "rate": {
            name: system.comp[name].flows_out["X"].get_effective_rate()
            for name in PROFILED
        },
        "plant_derating": {mode: var.value() for mode, var in plant.derating.items()},
        "shared": system.comp["SHARED"].flows_out["X"].var_out_rate.value(),
        "flat_X": system.comp["FLAT"].flows_out["X"].var_fed.value(),
        "conv_q": system.comp["CONV"].flows_in["q"].var_fed.value(),
        "sink_X": system.comp["PLANT_SINK"].flows_in["X"].var_fed.value(),
    }


@pytest.fixture(scope="module")
def the_run():
    """Drive the system to the horizon, recording every stop."""
    system = build_system()

    order = compute_equation_order(system)

    system.isimu_start()

    trace = [snapshot(system)]
    for _ in range(80):
        system.isimu_step_forward()
        trace.append(snapshot(system))
        if system.currentTime() >= HORIZON:
            break

    system.isimu_stop()

    return {"system": system, "trace": trace, "order": order}


def at(trace, date):
    """The stop AT ``date``."""
    for entry in trace:
        if entry["time"] == pytest.approx(date):
            return entry
    raise AssertionError(
        f"no stop at t={date}; stops were {[e['time'] for e in trace]}"
    )


# ----------------------------------------------------------------------
# The composition rule
# ----------------------------------------------------------------------


def test_a_profile_alone_scales_the_declared_rate(the_run):
    """With nothing derating it, production is the rate times the curve."""
    for date, factor in RAMP_AT.items():
        entry = at(the_run["trace"], date)

        assert entry["rate"]["BARE"] == pytest.approx(NOMINAL_RATE)
        assert entry["profile"]["BARE"] == pytest.approx(factor)
        assert entry["X"]["BARE"] == pytest.approx(PLANT_RATE * factor)


def test_profile_and_derating_compose_by_product(the_run):
    """The crux: the curve MULTIPLIES what the failure modes left.

    Every stop is written out with the two alternatives it excludes. Neither is
    a strawman: folding the profile into ``{flow}_out_rate`` -- the variable
    that already exists and is already "a public multiplier on production" --
    yields the *minimum* column, and treating the deratings as a product yields
    the last one.
    """
    trace = the_run["trace"]

    # (date, active deratings, expected effective rate)
    expected = [
        (0.5, [], NOMINAL_RATE),
        (1.5, [DEG_A_RATE], DEG_A_RATE),
        (2.5, [DEG_A_RATE, DEG_B_RATE], min(DEG_A_RATE, DEG_B_RATE)),
        (3.5, [DEG_B_RATE], DEG_B_RATE),
    ]

    for date, active, rate in expected:
        entry = at(trace, date)
        factor = RAMP_AT[date]

        assert entry["rate"]["PLANT"] == pytest.approx(rate), f"rate at t={date}"
        assert entry["profile"]["PLANT"] == pytest.approx(factor)

        product = PLANT_RATE * factor * rate
        assert entry["X"]["PLANT"] == pytest.approx(product), f"product at t={date}"

        # ... and what it is NOT.
        folded_into_the_rate = PLANT_RATE * min([factor, rate])
        deratings_multiplied = PLANT_RATE * factor * math.prod(active or [1.0])

        if folded_into_the_rate != pytest.approx(product):
            assert entry["X"]["PLANT"] != pytest.approx(folded_into_the_rate)
        if deratings_multiplied != pytest.approx(product):
            assert entry["X"]["PLANT"] != pytest.approx(deratings_multiplied)


def test_the_discriminating_instant_is_unambiguous(the_run):
    """t = 2.5: the three candidate rules give three different numbers.

    Both deratings hold and the curve is at 0.7, so:

    ==============================================  =======
    product (the rule)      10 x 0.7 x min(.5, .8)      3.5
    profile folded into the rate  10 x min(.7,.5,.8)    5.0
    deratings multiplied    10 x 0.7 x (0.5 x 0.8)      2.8
    ==============================================  =======
    """
    entry = at(the_run["trace"], 2.5)

    assert entry["X"]["PLANT"] == pytest.approx(3.5)
    assert entry["X"]["PLANT"] != pytest.approx(5.0)
    assert entry["X"]["PLANT"] != pytest.approx(2.8)


def test_deratings_still_compose_by_minimum_among_themselves(the_run):
    """Adding a profile changed nothing about how deratings fold.

    Each mode still owns its own variable, the fold is still a minimum, and
    repairing the deeper one while the shallower still holds leaves the
    shallower standing rather than returning to nominal.
    """
    trace = the_run["trace"]

    assert at(trace, 0.5)["plant_derating"] == {
        "deg_a": pytest.approx(NOMINAL_RATE),
        "deg_b": pytest.approx(NOMINAL_RATE),
    }
    assert at(trace, 2.5)["plant_derating"] == {
        "deg_a": pytest.approx(DEG_A_RATE),
        "deg_b": pytest.approx(DEG_B_RATE),
    }

    # deg_a repaired at t=3; deg_b still holds, so the rate is deg_b's and not 1
    assert at(trace, 3.5)["plant_derating"] == {
        "deg_a": pytest.approx(NOMINAL_RATE),
        "deg_b": pytest.approx(DEG_B_RATE),
    }
    assert at(trace, 3.5)["rate"]["PLANT"] == pytest.approx(DEG_B_RATE)


def test_the_profile_multiplies_the_shared_rate_too(the_run):
    """A mode declared OUTSIDE muscadet clamps ``X_out_rate``; it multiplies.

    The shared variable is the one a profile could most plausibly have been
    folded into, so this is the case that has to be pinned: the two are
    separate numbers and their product is what is produced.
    """
    trace = the_run["trace"]

    before = at(trace, 0.5)
    assert before["shared"] == pytest.approx(NOMINAL_RATE)
    assert before["X"]["SHARED"] == pytest.approx(PLANT_RATE * RAMP_AT[0.5])

    for date in (1.5, 2.5, 3.5):
        entry = at(trace, date)
        assert entry["shared"] == pytest.approx(SHARED_RATE)
        assert entry["X"]["SHARED"] == pytest.approx(
            PLANT_RATE * RAMP_AT[date] * SHARED_RATE
        )
        # Not the minimum of the two
        assert entry["X"]["SHARED"] != pytest.approx(
            PLANT_RATE * min(RAMP_AT[date], SHARED_RATE)
        )


def test_muscadet_never_writes_the_shared_rate_of_a_profiled_output(the_run):
    """The profile is published on its own variable, never onto the rate."""
    trace = the_run["trace"]

    for date in CLOCK_DATES:
        entry = at(trace, date)
        # BARE carries a profile and no mode at all: its shared rate stays 1
        assert entry["rate"]["BARE"] == pytest.approx(NOMINAL_RATE)
        assert entry["profile"]["BARE"] == pytest.approx(RAMP_AT[date])


def test_a_profile_scales_rule_driven_production(the_run):
    """The rule produces what its input allows; the curve scales that."""
    trace = the_run["trace"]

    for date, factor in RAMP_AT.items():
        entry = at(trace, date)

        assert entry["conv_q"] == pytest.approx(SUPPLY_RATE)
        assert entry["X"]["CONV"] == pytest.approx(SUPPLY_RATE * factor)


def test_the_shipped_sinusoidal_source_follows_its_curve(the_run):
    """``SourceSinusoidalContinuous``, declared by parameters alone."""
    trace = the_run["trace"]

    for date in CLOCK_DATES:
        entry = at(trace, date)

        assert entry["profile"]["SIN"] == pytest.approx(sinusoid(date))
        # rate defaults to 1 there, so the curve IS the production
        assert entry["X"]["SIN"] == pytest.approx(sinusoid(date))


def test_the_profiled_value_travels_downstream(the_run):
    """A consumer receives the profiled quantity, not the raw rate."""
    trace = the_run["trace"]

    for date in CLOCK_DATES:
        entry = at(trace, date)
        assert entry["sink_X"] == pytest.approx(entry["X"]["PLANT"])


def test_an_unprofiled_output_is_unchanged(the_run):
    """Parity: a component declaring no profile behaves exactly as before."""
    trace = the_run["trace"]
    flat = the_run["system"].comp["FLAT"].flows_out["X"]

    assert flat.profile is None
    assert flat.var_profile is None, "no variable is created for an unprofiled output"

    for date in CLOCK_DATES:
        assert at(trace, date)["flat_X"] == pytest.approx(PLANT_RATE)


def test_the_first_sample_of_a_sequence_is_already_profiled(the_run):
    """t = 0 reports the curve at 0, not the unprofiled declared rate.

    The engine samples instant 0 before it evaluates any equation, so an output
    whose init value were the raw default would announce its peak rate at
    midnight for the first sample of every Monte Carlo sequence.
    """
    entry = the_run["trace"][0]

    assert entry["time"] == pytest.approx(0.0)
    assert entry["X"]["BARE"] == pytest.approx(PLANT_RATE * ramp(0.0))
    assert entry["X"]["SIN"] == pytest.approx(sinusoid(0.0))
    assert entry["flat_X"] == pytest.approx(PLANT_RATE)


# ----------------------------------------------------------------------
# Ordering: a profiled source is a source
# ----------------------------------------------------------------------


def test_a_profiled_source_lands_at_the_head_of_the_derived_order(the_run):
    """It has no continuous input, so nothing may be evaluated before it.

    Verified rather than assumed: a profile is read from the clock and not from
    an upstream, so it must not make its component look like a consumer.
    """
    order = the_run["order"]

    for name in ("BARE", "SIN", "PLANT", "SHARED"):
        assert order.production_order.index(name) < order.production_order.index(
            f"{name}_SINK"
        )

    # A profiled source carries no incoming edge at all
    fed = {target for _, target in order.graph.edges}
    sources = set(order.graph.nodes) - fed

    assert {"BARE", "SIN", "PLANT", "SHARED", "SUPPLY"} <= sources

    # ... and the rule-driven, profiled converter is still ordered after its
    # supplier, because THAT edge is a real one.
    assert order.production_order.index("SUPPLY") < order.production_order.index("CONV")


# ----------------------------------------------------------------------
# The continuity line, drawn at declaration time
# ----------------------------------------------------------------------


def test_a_bare_callable_is_refused_and_says_what_is_missing():
    """The attestation is the mechanism; the message has to name it."""
    with pytest.raises(ValueError) as excinfo:
        muscadet.FlowContinuousOut(name="q", profile=lambda t: 1.0)

    message = str(excinfo.value)

    assert "muscadet.Profile" in message
    assert "continuous=True" in message
    assert "watched transition" in message
    assert "breakpoint" in message
    assert "output flow q" in message


def test_a_profile_declared_discontinuous_is_refused():
    """``continuous=False`` is the honest declaration, and it is refused."""
    with pytest.raises(ValueError, match="watched transition"):
        muscadet.Profile(lambda t: 1.0 if t < 8 else 0.0, continuous=False)

    # ... and so is leaving it out: there is no default attestation
    with pytest.raises(ValueError, match="watched transition"):
        muscadet.Profile(lambda t: 1.0)


def test_a_non_callable_profile_is_refused():
    with pytest.raises(ValueError, match="not callable"):
        muscadet.Profile(3.0, continuous=True)

    with pytest.raises(ValueError, match="not a profile"):
        muscadet.build_profile(3.0, "q")


def test_the_dict_form_builds_a_profile():
    """The ``{"cls": ...}`` form the rest of the declaration API uses."""
    profile = muscadet.build_profile(
        {"cls": "SinusoidalProfile", "period": 24.0, "offset": 1.0}, "q"
    )

    assert isinstance(profile, muscadet.SinusoidalProfile)
    assert profile.factor(6.0) == pytest.approx(2.0)

    with pytest.raises(ValueError, match="unknown profile class"):
        muscadet.build_profile({"cls": "Nope"}, "q")

    with pytest.raises(ValueError, match="'cls'"):
        muscadet.build_profile({"period": 24.0}, "q")


def test_no_profile_is_the_nominal_factor():
    """An output declaring none is scaled by 1, and carries no variable."""
    flow = muscadet.FlowContinuousOut(name="q")

    assert flow.profile is None
    assert flow.get_profile_factor(3.0) == pytest.approx(NOMINAL_FACTOR)


# ----------------------------------------------------------------------
# Non-negativity
# ----------------------------------------------------------------------


def test_a_negative_factor_is_refused_at_evaluation():
    """A negative quantity is not a reverse flow here, so it is named."""
    profile = muscadet.Profile(lambda t: 1.0 - t, continuous=True)

    assert profile.factor(0.5, "q") == pytest.approx(0.5)

    with pytest.raises(ValueError) as excinfo:
        profile.factor(2.0, "q")

    message = str(excinfo.value)
    assert "may not be negative" in message
    assert "output flow q" in message
    assert "t=2.0" in message


def test_a_sinusoid_clamped_below_zero_is_refused_at_declaration():
    """The reference implementation defaulted ``value_min`` to -inf.

    No model there relied on it -- every declaration site clamped at 0 -- so the
    default is 0 here and a negative clamp is refused, naming what a negative
    quantity would actually do to a buffered output.
    """
    with pytest.raises(ValueError, match="value_min may not be negative"):
        muscadet.SinusoidalProfile(amplitude=1.0, value_min=-1.0)

    # The default cuts the negative half-cycle rather than admitting it
    profile = muscadet.SinusoidalProfile(amplitude=1.0, period=4.0)

    assert profile.value_min == pytest.approx(0.0)
    assert profile.factor(1.0) == pytest.approx(1.0)
    assert profile.factor(3.0) == pytest.approx(0.0)


def test_a_sinusoid_refuses_an_unusable_declaration():
    with pytest.raises(ValueError, match="period must be strictly positive"):
        muscadet.SinusoidalProfile(period=0.0)

    with pytest.raises(ValueError, match="wrong way round"):
        muscadet.SinusoidalProfile(value_min=5.0, value_max=1.0)


def test_the_sinusoidal_component_refuses_a_profile_of_its_own(the_run):
    """R-3's rule: a key that would be silently ignored is refused instead."""
    system = the_run["system"]

    with pytest.raises(ValueError, match="builds its own"):
        system.add_component(
            name="SIN_CLASH",
            cls="SourceSinusoidalContinuous",
            flow="X",
            profile=muscadet.SinusoidalProfile(period=4.0),
        )


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
