"""A time profile on the declared demand of a pure consumer's input.

A continuous input whose demand nothing derives -- no rule consumes it, no
output of the same name relays it, no transfer meters it, no mixture draws it
-- claims the demand it was declared with. A profile on such an input scales
that declared demand::

    published demand = var_demand_default x profile(t)

and the factor applied is published on ``{flow}_in_profile``, a name distinct
from the ``{flow}_out_profile`` of an output, so a component carrying both an
input and an output of one name publishes the two factors side by side.

Any other input refuses a profile at the pre-run step (rules are declared after
flows, so declaration time cannot tell), with a message naming the reason.
"""

import math

import cod3s
import pytest

import muscadet
from muscadet import declare
from muscadet.profile import flow_noise_key

#: What the pure consumer asks for, before the profile scales it.
DEMAND = 2.0

#: A daily sinusoid between 0 and 2, shifted so that instant 0 is NOT at the
#: mean: a first sample carrying the unprofiled default would read 2, not 2 x
#: factor(0).
DAILY = dict(amplitude=1.0, period=24.0, offset=1.0, phase_shift=-4.0, value_min=0.0)

#: Stops of the interactive session, over one day.
CLOCK_DATES = tuple(float(t) for t in range(2, 25, 2))

#: The two profiles of the component carrying an input and an output ``X``.
IN_X = dict(amplitude=0.5, period=12.0, offset=1.0)
OUT_X = dict(amplitude=0.25, period=6.0, offset=1.0)


# ----------------------------------------------------------------------
# Components -- prefixed, since component classes resolve by name globally
# ----------------------------------------------------------------------


class PinSource(muscadet.ObjFlow):
    """An abundant, unprofiled supply of ``X`` and ``q``."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="X", var_fed_default=100.0)
        self.add_flow_continuous_out(name="q", var_fed_default=100.0)


class PinConsumer(muscadet.ObjFlow):
    """A pure consumer whose declared demand follows a daily curve."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name="X",
            var_demand_default=DEMAND,
            profile=muscadet.SinusoidalProfile(**DAILY),
        )


class PinFlatConsumer(muscadet.ObjFlow):
    """The same consumer with no profile: the parity reference."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="X", var_demand_default=DEMAND)


class PinBoth(muscadet.ObjFlow):
    """A rule produces ``X`` from ``q``; the input ``X`` is consumed by nothing.

    Both ``X`` ports carry a profile: the input's scales its declared demand,
    the output's scales what the rule produces.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_in(
            name="X",
            var_demand_default=1.0,
            profile=muscadet.SinusoidalProfile(**IN_X),
        )
        self.add_flow_continuous_out(
            name="X",
            var_fed_default=1.0,
            profile=muscadet.SinusoidalProfile(**OUT_X),
        )
        self.add_rules(name="make_X", rules=[dict(cons={"q": 1}, prod={"X": 1})])


class PinSink(muscadet.ObjFlow):
    """Takes what ``PinBoth`` produces."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="X", var_demand_default=10.0)


class PinClock(muscadet.ObjFlow):
    """Nothing but dates the interactive session can stop at."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


def add_clock(comp, date):
    """Give the session a stop at ``date`` it can always step to."""
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="before",
        st2="after",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def daily(time):
    """The factor the pure consumer's profile applies at ``time``."""
    return muscadet.SinusoidalProfile(**DAILY).factor(time)


# ----------------------------------------------------------------------
# One day of a pure consumer, stepped interactively
# ----------------------------------------------------------------------


def snapshot(system):
    consumer = system.comp["CONS"].flows_in["X"]
    both = system.comp["BOTH"]
    return {
        "time": system.currentTime(),
        "demand": consumer.var_demand.value(),
        "in_profile": consumer.var_profile.value(),
        "flat_demand": system.comp["FLAT"].flows_in["X"].var_demand.value(),
        "both_in_profile": both.flows_in["X"].var_profile.value(),
        "both_out_profile": both.flows_out["X"].var_profile.value(),
        "both_demand": both.flows_in["X"].var_demand.value(),
    }


@pytest.fixture(scope="module")
def the_run():
    system = muscadet.System(name="PinSys")

    system.add_component(name="SRC", cls="PinSource")
    system.add_component(name="CONS", cls="PinConsumer")
    system.connect_flow(source="SRC", target="CONS", flow_name="X")

    system.add_component(name="FLAT_SRC", cls="PinSource")
    system.add_component(name="FLAT", cls="PinFlatConsumer")
    system.connect_flow(source="FLAT_SRC", target="FLAT", flow_name="X")

    system.add_component(name="BOTH_SRC", cls="PinSource")
    system.add_component(name="BOTH", cls="PinBoth")
    system.add_component(name="BOTH_SINK", cls="PinSink")
    system.connect_flow(source="BOTH_SRC", target="BOTH", flow_name="q")
    system.connect_flow(source="BOTH_SRC", target="BOTH", flow_name="X")
    system.connect_flow(source="BOTH", target="BOTH_SINK", flow_name="X")

    system.add_component(name="CLOCK", cls="PinClock")
    for date in CLOCK_DATES:
        add_clock(system.comp["CLOCK"], date)

    system.isimu_start()
    trace = [snapshot(system)]
    for _ in range(4 * len(CLOCK_DATES)):
        system.isimu_step_forward()
        trace.append(snapshot(system))
        if system.currentTime() >= CLOCK_DATES[-1]:
            break
    system.isimu_stop()

    return {"system": system, "trace": trace}


def at(trace, date):
    for entry in trace:
        if entry["time"] == pytest.approx(date):
            return entry
    raise AssertionError(f"no stop at t={date}: {[e['time'] for e in trace]}")


def test_the_declared_demand_follows_the_profile_over_a_day(the_run):
    for date in CLOCK_DATES:
        entry = at(the_run["trace"], date)
        assert entry["demand"] == pytest.approx(DEMAND * daily(date), abs=1e-6)


def test_the_applied_factor_is_published_on_in_profile(the_run):
    consumer = the_run["system"].comp["CONS"].flows_in["X"]
    assert consumer.var_profile.basename() == "X_in_profile"
    for date in CLOCK_DATES:
        entry = at(the_run["trace"], date)
        assert entry["in_profile"] == pytest.approx(daily(date), abs=1e-6)


def test_the_profile_actually_moves_over_the_day(the_run):
    demands = [at(the_run["trace"], date)["demand"] for date in CLOCK_DATES]
    assert max(demands) - min(demands) > 1.0


def test_the_first_sample_is_already_profiled(the_run):
    assert daily(0.0) != pytest.approx(1.0)
    entry = the_run["trace"][0]
    assert entry["time"] == pytest.approx(0.0)
    assert entry["demand"] == pytest.approx(DEMAND * daily(0.0), abs=1e-6)
    assert entry["in_profile"] == pytest.approx(daily(0.0), abs=1e-6)


def test_an_unprofiled_consumer_is_unchanged(the_run):
    flat = the_run["system"].comp["FLAT"].flows_in["X"]
    assert flat.var_profile is None
    assert flat.profile is None
    assert "X_in_profile" not in [
        var.basename() for var in the_run["system"].comp["FLAT"].variables()
    ]
    for entry in the_run["trace"]:
        assert entry["flat_demand"] == pytest.approx(DEMAND)


def test_input_and_output_of_one_name_publish_two_factors(the_run):
    both = the_run["system"].comp["BOTH"]
    assert both.flows_in["X"].var_profile.basename() == "X_in_profile"
    assert both.flows_out["X"].var_profile.basename() == "X_out_profile"

    in_profile = muscadet.SinusoidalProfile(**IN_X)
    out_profile = muscadet.SinusoidalProfile(**OUT_X)
    for date in CLOCK_DATES:
        entry = at(the_run["trace"], date)
        assert entry["both_in_profile"] == pytest.approx(
            in_profile.factor(date), abs=1e-6
        )
        assert entry["both_out_profile"] == pytest.approx(
            out_profile.factor(date), abs=1e-6
        )
        assert entry["both_demand"] == pytest.approx(in_profile.factor(date), abs=1e-6)


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()


def test_monte_carlo_samples_the_profiled_demand_from_instant_zero():
    system = muscadet.System(name="PinMcSys")
    try:
        system.add_component(name="SRC", cls="PinSource")
        system.add_component(name="CONS", cls="PinConsumer")
        system.connect_flow(source="SRC", target="CONS", flow_name="X")
        system.add_indicator_var(
            component="CONS", var="^X_(demand_out|in_profile)$", stats=["mean"]
        )
        schedule = [0.0, 3.0, 9.0]
        system.simulate({"nb_runs": 1, "schedule": schedule})

        rows = indicator_rows(system)
        for instant in schedule:
            assert rows["CONS_X_demand_out"][("mean", instant)] == pytest.approx(
                DEMAND * daily(instant), abs=1e-5
            )
            assert rows["CONS_X_in_profile"][("mean", instant)] == pytest.approx(
                daily(instant), abs=1e-5
            )
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ----------------------------------------------------------------------
# Inputs whose demand something else decides refuse a profile
# ----------------------------------------------------------------------


class PinRuleConsumer(muscadet.ObjFlow):
    """``q`` is consumed by a rule, and still carries a profile."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name="q", profile=muscadet.SinusoidalProfile(**DAILY)
        )
        self.add_flow_continuous_out(name="X")
        self.add_rules(name="electrolysis", rules=[dict(cons={"q": 1}, prod={"X": 1})])


class PinRelay(muscadet.ObjFlow):
    """``X`` crosses the component unchanged, and its input carries a profile."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name="X", profile=muscadet.SinusoidalProfile(**DAILY)
        )
        self.add_flow_continuous_out(name="X")


class PinMixer(muscadet.ObjFlow):
    """``a`` is drawn as a mixture, and carries a profile."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name="a", profile=muscadet.SinusoidalProfile(**DAILY)
        )
        self.add_mixture_in(name="vent", flows=["a"], flow_rate=1.0)


class PinConduit(muscadet.ObjFlow):
    """``X`` is metered by a conduit, and its input carries a profile."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name="X", profile=muscadet.SinusoidalProfile(**DAILY)
        )
        self.add_flow_continuous_out(name="X")
        self.add_transfer(
            name="meter",
            flows=["X", "X"],
            equation=muscadet.ConductiveTransfer(
                conductance=1.0, potential_a=2.0, potential_b=1.0
            ),
        )


def prerun_refusal(cls):
    system = muscadet.System(name=f"Sys{cls}")
    try:
        system.add_component(name="C", cls=cls)
        with pytest.raises(ValueError) as refusal:
            system.prerun()
        return str(refusal.value)
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_an_input_consumed_by_a_rule_refuses_a_profile_naming_the_rule():
    message = prerun_refusal("PinRuleConsumer")
    assert "electrolysis" in message
    assert "'q'" in message and "'C'" in message


def test_an_input_relayed_by_a_same_name_output_refuses_a_profile():
    message = prerun_refusal("PinRelay")
    assert "output of the same name" in message


def test_an_input_of_a_mixture_refuses_a_profile():
    message = prerun_refusal("PinMixer")
    assert "vent" in message


def test_an_input_metered_by_a_transfer_refuses_a_profile():
    message = prerun_refusal("PinConduit")
    assert "meter" in message


# ----------------------------------------------------------------------
# Noise on an input profile
# ----------------------------------------------------------------------


class PinNoisyConsumer(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name="X",
            var_demand_default=DEMAND,
            profile=muscadet.SinusoidalProfile(
                amplitude=0.5,
                period=12.0,
                offset=1.0,
                noise_amplitude=0.25,
                noise_time_scale=1.5,
            ),
        )


NOISY_SCHEDULE = [float(t) for t in range(13)]


def indicator_rows(system):
    return {
        name: {
            (row["stat"], float(row["instant"])): float(row["values"])
            for _, row in indic.values.iterrows()
        }
        for name, indic in system.indicators.items()
    }


def run_noisy(seed):
    system = muscadet.System(name="PinNzSys")
    try:
        system.add_component(name="SRC", cls="PinSource")
        system.add_component(name="CONS", cls="PinNoisyConsumer")
        system.connect_flow(source="SRC", target="CONS", flow_name="X")
        system.add_indicator_var(
            component="CONS", var="^X_(demand_out|in_profile)$", stats=["mean"]
        )
        profile = system.comp["CONS"].flows_in["X"].profile
        seeds = []
        seed_noise = profile.seed_noise

        def recording(draw):
            seed_noise(draw)
            seeds.append(profile.noise_seed)

        profile.seed_noise = recording

        system.simulate({"nb_runs": 2, "schedule": NOISY_SCHEDULE, "seed": seed})

        trajectories = []
        for replica_seed in seeds:
            profile.noise_seed = replica_seed
            trajectories.append([profile.factor(t) for t in NOISY_SCHEDULE])

        return dict(
            key=profile.noise_key,
            trajectories=trajectories,
            means=indicator_rows(system),
        )
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_a_noisy_input_profile_differs_between_sequences_and_follows_the_seed():
    first, again = run_noisy(7), run_noisy(7)

    assert first["key"] == flow_noise_key("CONS", "X")
    assert len(first["trajectories"]) == 2
    assert first["trajectories"][0] != first["trajectories"][1]
    assert first["trajectories"] == again["trajectories"]
    assert first["means"] == again["means"]

    for index, instant in enumerate(NOISY_SCHEDULE):
        predicted = sum(traj[index] for traj in first["trajectories"]) / 2
        assert first["means"]["CONS_X_in_profile"][("mean", instant)] == (
            pytest.approx(predicted, rel=1e-5)
        )
        assert first["means"]["CONS_X_demand_out"][("mean", instant)] == (
            pytest.approx(DEMAND * predicted, rel=1e-5)
        )


# ----------------------------------------------------------------------
# Declaration
# ----------------------------------------------------------------------


def test_an_input_profile_round_trips_through_the_declaration():
    system = muscadet.System(name="PinDecl")
    try:
        comp = muscadet.build_component(
            system,
            {
                "name": "C",
                "flows": [
                    {
                        "cls": "FlowContinuousIn",
                        "name": "X",
                        "var_demand_default": DEMAND,
                        "profile": {"cls": "SinusoidalProfile", **DAILY},
                    }
                ],
            },
        )

        spec = declare.component_spec(comp)
        (written,) = [f["profile"] for f in spec["flows"] if f["name"] == "X"]
        assert written["cls"] == "SinusoidalProfile"
        assert written["period"] == DAILY["period"]
        assert written["phase_shift"] == DAILY["phase_shift"]

        rebuilt = muscadet.build_component(system, dict(spec, name="C2"))
        profile = rebuilt.flows_in["X"].profile
        assert isinstance(profile, muscadet.SinusoidalProfile)
        for time in (0.0, 5.0, 13.0):
            assert profile.factor(time) == pytest.approx(daily(time))
        assert declare.component_spec(rebuilt)["flows"] == spec["flows"]
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_an_unprofiled_input_declares_no_profile():
    system = muscadet.System(name="PinDeclFlat")
    try:
        comp = muscadet.build_component(
            system,
            {
                "name": "C",
                "flows": [
                    {"cls": "FlowContinuousIn", "name": "X", "var_demand_default": 1.0}
                ],
            },
        )
        (flow,) = declare.component_spec(comp)["flows"]
        assert "profile" not in flow
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_the_shift_moves_instant_zero_off_the_mean():
    """Keeps the sinusoid parameters honest: the shift does move instant 0."""
    assert daily(0.0) == pytest.approx(1.0 + math.sin(2 * math.pi * 4.0 / 24.0))
