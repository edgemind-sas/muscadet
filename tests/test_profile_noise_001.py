"""Smoothed noise on the modulated profiles, drawn again for every replica.

Every shipped shape (sinusoid, trapezoid, piecewise-linear) takes an optional
noise modifier::

    factor(t) = clamp(base(t) + noise(t), value_min, value_max)

The noise is a sequence of nodes every ``noise_time_scale``, each carrying
``noise_amplitude * z_k`` with ``z_k`` a standard normal, joined by a
smoothstep. ``z_k`` is a pure function of (replica seed, flow key, k), so the
noise is a pure function of time inside a sequence, is drawn anew for every
Monte Carlo sequence, and is reproduced by the study seed.

What is pinned here:

* replicas differ, a rerun at the same seed is identical, another seed differs,
  and the trajectories the engine published ARE the ones the seeds predict;
* instant 0 carries the noise-free value on every replica;
* two components of one class get independent noises;
* the noise is continuous, follows its law, and composes with the clamp;
* a model without noise consumes exactly the draws it did before;
* two multi-rank runs at the same seed give the same results;
* an evaluation costs the same whatever ``t``;
* the declaration carries the three noise parameters.
"""

import json
import math
import os
import pathlib
import random
import shutil
import subprocess
import sys
import time

import cod3s
import pytest

import muscadet
from muscadet import declare
from muscadet.profile import (
    NOISE_SEED_BEFORE_ANY_SEQUENCE,
    flow_noise_key,
    fnv1a_64,
    noise_node,
)

TESTS_DIR = pathlib.Path(__file__).parent

#: Sample instants of the Monte Carlo studies.
SCHEDULE = [float(t) for t in range(13)]

#: The noisy sinusoid every source below carries.
BASE = dict(amplitude=0.5, period=12.0, offset=1.0)
NOISE = dict(noise_amplitude=0.25, noise_time_scale=1.5)

#: ONE profile object handed to every component of the class: attaching it to
#: a flow must not write the key of that flow into the shared object.
SHARED_PROFILE = muscadet.SinusoidalProfile(**BASE, **NOISE)


class NzPlant(muscadet.ObjFlow):
    """A noisy sinusoidal source; every instance shares one profile object."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name="X", var_fed_default=1.0, profile=SHARED_PROFILE
        )


class NzSink(muscadet.ObjFlow):
    """A consumer asking for more than is ever produced."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="X", var_demand_default=100.0)


class NzRefPlant(muscadet.ObjFlow):
    """The noise-free reference: a sinusoid and nothing else."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name="X",
            var_fed_default=10.0,
            profile=muscadet.SinusoidalProfile(amplitude=0.5, period=24.0, offset=1.0),
        )


class NzRefSink(muscadet.ObjFlow):
    """The reference consumer."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="X", var_demand_default=100.0)


def noise_free(time_):
    """The base sinusoid of every noisy source, unclamped (it stays above 0)."""
    return BASE["amplitude"] * math.sin(2 * math.pi * time_ / BASE["period"]) + 1.0


def indicator_rows(system):
    """``{indicator name: {(stat, instant): value}}``."""
    return {
        name: {
            (row["stat"], float(row["instant"])): float(row["values"])
            for _, row in indic.values.iterrows()
        }
        for name, indic in system.indicators.items()
    }


def run_noisy_study(seed, nb_runs=2):
    """Run the two-source study; return seeds, predicted trajectories, means.

    The replica seed each source receives is recorded per sequence. Given it,
    the factor is a pure function of time, so the trajectory a sequence
    followed is re-evaluated on the schedule afterwards -- and checked against
    what the engine itself published (the mean of the two).
    """
    system = muscadet.System(name="NzSys")
    try:
        for name in ("A", "B"):
            system.add_component(name=name, cls="NzPlant")
            system.add_component(name=f"{name}_SINK", cls="NzSink")
            system.connect_flow(source=name, target=f"{name}_SINK", flow_name="X")
            system.add_indicator_var(
                component=name, var="^X_out_profile$", stats=["mean"]
            )

        profiles = {name: system.comp[name].flows_out["X"].profile for name in "AB"}
        seeds = {name: [] for name in "AB"}

        for name, profile in profiles.items():
            seed_noise = profile.seed_noise

            def recording(draw, profile=profile, name=name, seed_noise=seed_noise):
                seed_noise(draw)
                seeds[name].append(profile.noise_seed)

            profile.seed_noise = recording

        initial = {name: profile.noise_seed for name, profile in profiles.items()}

        system.simulate({"nb_runs": nb_runs, "schedule": SCHEDULE, "seed": seed})

        trajectories = {}
        for name, profile in profiles.items():
            trajectories[name] = []
            for replica_seed in seeds[name]:
                profile.noise_seed = replica_seed
                trajectories[name].append([profile.factor(t) for t in SCHEDULE])

        return dict(
            seeds=seeds,
            initial_seeds=initial,
            keys={name: profile.noise_key for name, profile in profiles.items()},
            trajectories=trajectories,
            means=indicator_rows(system),
            store_deterministic=system.storeDeterministicSeq(),
        )
    finally:
        system.deleteSys()
        cod3s.terminate_session()


@pytest.fixture(scope="module")
def studies():
    """The noisy study at seed 7 twice and at seed 8 once."""
    return {
        "first": run_noisy_study(7),
        "again": run_noisy_study(7),
        "other": run_noisy_study(8),
    }


# ----------------------------------------------------------------------
# Monte Carlo: one noise per replica, reproduced by the seed
# ----------------------------------------------------------------------


def test_each_replica_draws_its_own_noise(studies):
    run = studies["first"]
    assert len(run["seeds"]["A"]) == 2
    first, second = run["trajectories"]["A"]
    assert first != second


def test_the_engine_published_the_predicted_trajectories(studies):
    """The mean the engine published is the mean of the two predicted ones.

    This is what proves both sequences were SIMULATED with their own noise:
    PyCATSHOO replays a sequence in which no transition drew a date unless
    told otherwise, and a replayed sequence would publish the first one twice.
    """
    run = studies["first"]
    assert run["store_deterministic"] is False
    for name in "AB":
        means = run["means"][f"{name}_X_out_profile"]
        for index, instant in enumerate(SCHEDULE):
            predicted = sum(traj[index] for traj in run["trajectories"][name]) / 2
            assert means[("mean", instant)] == pytest.approx(predicted, rel=1e-6)


def test_the_same_seed_reproduces_the_run(studies):
    first, again = studies["first"], studies["again"]
    assert first["seeds"] == again["seeds"]
    assert first["trajectories"] == again["trajectories"]
    assert first["means"] == again["means"]


def test_another_seed_draws_another_noise(studies):
    first, other = studies["first"], studies["other"]
    assert first["seeds"] != other["seeds"]
    assert first["trajectories"]["A"] != other["trajectories"]["A"]


def test_instant_zero_is_the_noise_free_value_on_every_replica(studies):
    run = studies["first"]
    for name in "AB":
        assert run["means"][f"{name}_X_out_profile"][("mean", 0.0)] == noise_free(0.0)
        for trajectory in run["trajectories"][name]:
            assert trajectory[0] == noise_free(0.0)


def test_instant_zero_is_the_right_limit_on_every_replica(studies):
    profile = muscadet.SinusoidalProfile(**BASE, **NOISE, noise_key=123)
    for replica_seed in studies["first"]["seeds"]["A"]:
        profile.noise_seed = replica_seed
        assert profile.factor(1e-9) == pytest.approx(profile.factor(0.0), abs=1e-8)


def test_before_any_sequence_the_seed_is_the_documented_constant(studies):
    assert studies["first"]["initial_seeds"] == {
        "A": NOISE_SEED_BEFORE_ANY_SEQUENCE,
        "B": NOISE_SEED_BEFORE_ANY_SEQUENCE,
    }


def test_two_components_of_one_class_get_independent_noises(studies):
    run = studies["first"]
    assert run["keys"]["A"] == flow_noise_key("A", "X")
    assert run["keys"]["B"] == flow_noise_key("B", "X")
    for trajectory_a, trajectory_b in zip(
        run["trajectories"]["A"], run["trajectories"]["B"]
    ):
        assert trajectory_a != trajectory_b


def test_the_shared_profile_object_is_left_untouched(studies):
    assert SHARED_PROFILE.noise_key is None


# ----------------------------------------------------------------------
# The noise as a function
# ----------------------------------------------------------------------


def noisy(**overrides):
    params = dict(BASE, **NOISE, noise_key=flow_noise_key("C", "X"))
    params.update(overrides)
    profile = muscadet.SinusoidalProfile(**params)
    profile.seed_noise(0.123456789)
    return profile


def test_the_noise_is_continuous_including_at_the_nodes():
    profile = noisy()
    rng = random.Random(2026)
    scale = NOISE["noise_time_scale"]
    instants = [rng.uniform(0.0, 500.0) for _ in range(9_000)]
    instants += [scale * k for k in range(1_000)]
    instants += [scale * k - 1e-9 for k in range(1, 1_001)]

    worst = max(abs(profile.factor(t) - profile.factor(t + 1e-9)) for t in instants)

    assert worst < 1e-6


@pytest.mark.parametrize(
    "cls,params",
    [
        (
            muscadet.TrapezoidProfile,
            dict(low=1.0, high=2.0, period=24.0, high_duration=8.0, ramp_duration=1.0),
        ),
        (
            muscadet.PiecewiseLinearProfile,
            dict(points=[[0.0, 1.0], [6.0, 2.0], [12.0, 1.5]], period=24.0),
        ),
    ],
    ids=["trapezoid", "piecewise_linear"],
)
def test_every_modulated_shape_takes_the_noise(cls, params):
    plain = cls(**params)
    profile = cls(**params, **NOISE, noise_key=42)
    profile.seed_noise(0.5)

    assert profile.factor(0.0) == plain.factor(0.0)
    offsets = [profile.factor(t) - plain.factor(t) for t in (3.7, 9.1, 15.3)]
    assert all(abs(offset) > 0.0 for offset in offsets)


def test_the_nodes_follow_their_law():
    """Mean 0, standard deviation ``noise_amplitude``, within 4 sigma."""
    profile = noisy()
    count = 1_000
    scale = NOISE["noise_time_scale"]
    amplitude = NOISE["noise_amplitude"]
    values = [profile.noise(scale * k) for k in range(1, count + 1)]

    mean = sum(values) / count
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / (count - 1))

    assert abs(mean) < 4 * amplitude / math.sqrt(count)
    assert abs(std - amplitude) < 4 * amplitude / math.sqrt(2 * (count - 1))


def test_the_clamp_applies_after_the_noise():
    profile = noisy(amplitude=1.0, offset=0.3, noise_amplitude=0.6, value_min=0.0)
    factors = [profile.factor(0.01 * k) for k in range(100_000)]

    assert min(factors) == 0.0
    assert max(factors) > 1.3


def test_the_first_node_is_zero_and_the_generator_is_pinned():
    """The node values another engine must reproduce bit for bit."""
    assert noise_node(123, 456, 0) == 0.0
    assert noise_node(1, 2, 1) == 0.7500139990340491
    assert noise_node(1, 2, 2) == -0.20004681714558584
    assert noise_node(1, 2, 3) == 0.5771604572669078


def test_the_flow_key_is_fnv1a_64():
    assert fnv1a_64("") == 0xCBF29CE484222325
    assert fnv1a_64("a") == 0xAF63DC4C8601EC8C
    assert fnv1a_64("foobar") == 0x85944171F73967E8
    assert flow_noise_key("comp", "flow") == fnv1a_64("comp/flow")


def test_an_evaluation_costs_the_same_whatever_t():
    """O(1): no walk over the nodes, every call landing between new nodes."""
    profile = noisy()
    scale = NOISE["noise_time_scale"]
    calls = 500_000

    def timed(origin):
        instants = [origin + scale * (k + 0.5) for k in range(calls)]
        start = time.perf_counter()
        for t in instants:
            profile.factor(t)
        return time.perf_counter() - start

    early = timed(0.0)
    late = timed(1e9)

    assert late < 3.0 * early


@pytest.mark.parametrize(
    "params,message",
    [
        (dict(noise_amplitude=-0.1, noise_time_scale=1.0), "non-negative"),
        (dict(noise_amplitude=0.1), "needs a noise_time_scale"),
        (dict(noise_amplitude=0.1, noise_time_scale=0.0), "strictly positive"),
        (dict(noise_time_scale=1.0), "without noise"),
        (dict(noise_amplitude=0.1, noise_time_scale=1.0, noise_key=-1), "64"),
        (dict(noise_amplitude=0.1, noise_time_scale=1.0, noise_key=1.5), "integer"),
    ],
)
def test_an_unusable_noise_is_refused(params, message):
    with pytest.raises(ValueError, match=message):
        muscadet.SinusoidalProfile(**BASE, **params)


# ----------------------------------------------------------------------
# A model without noise: unchanged, draws included
# ----------------------------------------------------------------------


def test_a_model_without_noise_draws_what_it_did_before():
    """Indicators of a study with failures, against values recorded before.

    The reference was computed by the code preceding the noise. Its means and
    standard deviations at 101 instants depend on every failure and repair
    date of 40 sequences, so a single draw consumed by the noise machinery
    shifts them.
    """
    reference = json.loads(
        (TESTS_DIR / "fixtures" / "profile_noise_free_reference.json").read_text()
    )

    system = muscadet.System(name="NzRef")
    try:
        plant = system.add_component(name="P", cls="NzRefPlant")
        system.add_component(name="S", cls="NzRefSink")
        system.connect_flow(source="P", target="S", flow_name="X")
        plant.add_exp_failure_mode(
            name="fm",
            failure_rate=0.05,
            repair_rate=0.3,
            failure_effects=[("X", 0.0)],
        )
        system.add_indicator_var(
            component="P", var="^X_fed_out$", stats=["mean", "stddev"]
        )
        system.simulate(
            {"nb_runs": 40, "schedule": [float(t) for t in range(101)], "seed": 7}
        )

        assert not plant.flows_out["X"].profile.has_noise
        assert not hasattr(system, "_noise_seed_method")
        assert system.storeDeterministicSeq() is True

        rows = {
            name: [
                [row["stat"], float(row["instant"]), float(row["values"])]
                for _, row in indic.values.iterrows()
            ]
            for name, indic in system.indicators.items()
        }
        assert rows == reference
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ----------------------------------------------------------------------
# Interactive simulation
# ----------------------------------------------------------------------


def test_an_interactive_session_seeds_the_noise():
    system = muscadet.System(name="NzIsimu")
    try:
        system.add_component(name="A", cls="NzPlant")
        system.add_component(name="A_SINK", cls="NzSink")
        system.connect_flow(source="A", target="A_SINK", flow_name="X")
        clock = system.add_component(name="CLOCK", cls="NzSink")
        for date in (2.3, 4.7):
            clock.add_atm2states(
                name=f"clock_{str(date).replace('.', '_')}",
                st1="before",
                st2="after",
                occ_law_12={"cls": "delay", "time": date},
                cond_occ_21=False,
            )

        flow = system.comp["A"].flows_out["X"]
        system.isimu_start()

        assert flow.profile.noise_seed != NOISE_SEED_BEFORE_ANY_SEQUENCE
        assert flow.var_profile.value() == noise_free(0.0)

        stops = 0
        while system.currentTime() < 4.7 and stops < 20:
            system.isimu_step_forward()
            stops += 1
            now = system.currentTime()
            assert flow.var_profile.value() == pytest.approx(
                flow.profile.factor(now), rel=1e-9
            )
            assert flow.profile.factor(now) != pytest.approx(noise_free(now))

        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ----------------------------------------------------------------------
# Declaration
# ----------------------------------------------------------------------


def test_the_declaration_carries_the_noise():
    system = muscadet.System(name="NzDecl")
    try:
        comp = muscadet.build_component(
            system,
            {
                "name": "C",
                "flows": [
                    {
                        "cls": "FlowContinuousOut",
                        "name": "q",
                        "var_fed_default": 5.0,
                        "profile": {
                            "cls": "TrapezoidProfile",
                            "low": 1.0,
                            "high": 2.0,
                            "period": 24.0,
                            "high_duration": 8.0,
                            "ramp_duration": 1.0,
                            "noise_amplitude": 0.2,
                            "noise_time_scale": 3.0,
                        },
                    }
                ],
            },
        )

        spec = declare.component_spec(comp)
        (written,) = [f["profile"] for f in spec["flows"] if f["name"] == "q"]
        assert written["noise_amplitude"] == 0.2
        assert written["noise_time_scale"] == 3.0
        assert written["noise_key"] == flow_noise_key("C", "q")

        rebuilt = muscadet.build_component(system, dict(spec, name="C2"))
        profile = rebuilt.flows_out["q"].profile
        assert profile.noise_amplitude == 0.2
        assert profile.noise_time_scale == 3.0
        assert profile.noise_key == flow_noise_key("C", "q")
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_a_noise_free_declaration_writes_no_noise_key():
    system = muscadet.System(name="NzDeclFree")
    try:
        comp = muscadet.build_component(
            system,
            {
                "name": "C",
                "flows": [
                    {
                        "cls": "FlowContinuousOut",
                        "name": "q",
                        "var_fed_default": 5.0,
                        "profile": {"cls": "SinusoidalProfile", "period": 24.0},
                    }
                ],
            },
        )

        spec = declare.component_spec(comp)
        (written,) = [f["profile"] for f in spec["flows"] if f["name"] == "q"]
        assert not {"noise_amplitude", "noise_time_scale", "noise_key"} & set(written)
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ----------------------------------------------------------------------
# Several ranks
# ----------------------------------------------------------------------


def mpi_run(seed, output, ranks):
    """Run the multi-rank study script; return its indicator values."""
    command = [sys.executable, str(TESTS_DIR / "profile_noise_mpi_study.py")]
    if ranks > 1:
        command = ["mpirun", "-n", str(ranks)] + command
    subprocess.run(
        command + [str(seed), str(output)],
        check=True,
        cwd=TESTS_DIR,
        env=dict(os.environ),
        timeout=300,
        capture_output=True,
    )
    return json.loads(pathlib.Path(output).read_text())


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("mpirun") is None, reason="mpirun is not installed")
def test_two_runs_on_two_ranks_at_one_seed_agree(tmp_path):
    """Two 2-rank runs at the same seed give the same indicators.

    Recorded beside it, without being required: the 2-rank run is compared
    with a single-process run of the same study.
    """
    first = mpi_run(7, tmp_path / "first.json", ranks=2)
    second = mpi_run(7, tmp_path / "second.json", ranks=2)
    other = mpi_run(8, tmp_path / "other.json", ranks=2)

    assert first == second
    assert first != other

    single = mpi_run(7, tmp_path / "single.json", ranks=1)
    print(f"2-rank run equal to the single-process run: {first == single}")
