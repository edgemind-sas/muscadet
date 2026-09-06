"""A republishing instrument starts from what it OBSERVES, at t = 0 (R42, R45).

A value output is refreshed by a PDMP equation, and the engine samples instant 0
before it evaluates a single equation. So an instrument standing in front of a
tank published its declared default -- zero -- for the whole of the first
integration step, and did it again at the start of every Monte Carlo sequence.
The consequences are the two this module measures: a reading nobody could trust
at t = 0, and a regulation whose level already stands past its threshold sitting
idle until the reading comes back and crosses it.

The asymmetry was inside one method. ``ObjCtrl.compile_emit`` seeds a BOOLEAN
output on a start method, with a comment saying why it is load-bearing; the
value branch, three lines above, had nothing. The precedent for the fix is
:meth:`muscadet.FlowContinuousOut.initial_fed_value`, which applies a production
profile at instant 0 for the very same reason -- a solar source announcing its
peak rate at midnight.

Three scenarios, each built, driven and deleted before the next one starts:
PyCATSHOO forbids more than one live system per process.

* **interactive** -- the reading and the threshold, read at t = 0 BEFORE any
  step, then re-read after the session has moved so the seed is shown to be a
  starting point and not a stuck value. The refusal of a republication declared
  after the pre-run step is recorded on the same live system: the seed is
  registered in the very method that raises it.
* **monte carlo** -- the mean of the published variable at instant 0 over
  several sequences. Seeded once, that mean would be the value divided by the
  sequence count; seeded at every sequence start, it is the value.
* **forcing** -- an output a mode holds publishes the forced number at t = 0
  too, gain included: the seed goes through the ordinary publication path or it
  starts a sequence on a number the next step contradicts.
"""

import cod3s
import muscadet
import pytest

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

#: What the tank holds when the clock starts, and therefore what an honest
#: instrument reports at t = 0.
SEED_LEVEL = 40.0

#: The tank's size. Wide enough that the level moves slowly against it.
SEED_VOLUME = 100.0

#: What drains the tank, so a seeded reading and a stepped one differ.
SEED_DEMAND = 1.0

#: The instrument's gain, deliberately NOT 1: it is what shows the seed goes
#: through the publication path rather than around it.
SEED_GAIN = 2.0

#: What the instrument therefore publishes at t = 0.
SEED_PUBLISHED = SEED_LEVEL * SEED_GAIN

#: The threshold the regulation watches, standing BELOW the initial reading:
#: the montage is already past it when the clock starts.
SEED_THRESHOLD = 60.0

#: The number a mode forces the second instrument's publication to.
SEED_FORCED = 7.0

#: Sequences of the Monte Carlo scenario. A seed applied to the first one alone
#: would divide the mean at instant 0 by this.
SEED_RUNS = 5

#: The instants the Monte Carlo run samples.
SEED_SCHEDULE = [0.0, 1.0, 2.0]

#: Seed of the Monte Carlo run. Nothing here is stochastic; it is pinned so the
#: run is reproducible all the same.
SEED_MC_SEED = 4242


def add_clock(comp, date):
    """Give the interactive session a date it can always step to."""
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def add_tank(system, name):
    """A volume holding :data:`SEED_LEVEL` of ``q`` when the clock starts."""
    return system.add_component(
        name=name,
        cls="CapacityContinuous",
        flow="q",
        capacity=SEED_VOLUME,
        capacity_name="tank",
        content_init={"q": SEED_LEVEL},
        fill_rate=float("inf"),
    )


def add_instrument(system, name, gain=SEED_GAIN):
    """A controller republishing the level it observes, times ``gain``."""
    return system.add_component(
        name=name,
        cls="ObjCtrl",
        controls_in=[{"name": "tank"}],
        controls_out=[
            {
                "name": "reading",
                "kind": "value",
                "emit": {"op": "republish", "input": "tank", "gain": gain},
            }
        ],
    )


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_interactive_scenario(obs):
    """Read the publication at t = 0, before the session has stepped at all."""
    system = muscadet.System(name="CtrlSeedInteractive")

    add_tank(system, "CAP")
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="q", demand=SEED_DEMAND
    )
    add_instrument(system, "INSTR")

    # The regulation: it reads the INSTRUMENT, not the tank, so its threshold
    # is crossed only if the instrument publishes something honest at t = 0.
    system.add_component(
        name="REG",
        cls="ObjCtrl",
        controls_in=[{"name": "reading"}],
        controls_out=[
            {
                "name": "run",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "reading",
                    "operator": ">=",
                    "threshold": SEED_THRESHOLD,
                },
            }
        ],
    )

    system.connect_flow(source="CAP", target="SINK", flow_name="q")
    system.connect("CAP", "tank_level_out", "INSTR", "tank_level_in")
    system.connect("INSTR", "reading_level_out", "REG", "reading_level_in")

    for date in (1.0, 2.0):
        add_clock(system.comp["SINK"], date)

    instrument = system.comp["INSTR"]
    published = instrument.controls_out["reading"]

    system.isimu_start()

    # THE reading, taken before a single step: this is the whole defect.
    obs["at_zero"] = {
        "time": system.currentTime(),
        "observed": instrument.controls_in["tank"].get_reading(),
        "published": published.var_level.value(),
        "fill": published.var_fill.value(),
        "gain": published.var_gain.value(),
        "regulation_reads": system.comp["REG"].controls_in["reading"].get_reading(),
        "regulation_runs": system.comp["REG"].controls_out["run"].get_signal(),
    }

    for _ in range(20):
        if system.currentTime() >= 1.0:
            break
        system.isimu_step_forward()

    obs["stepped"] = {
        "time": system.currentTime(),
        "observed": instrument.controls_in["tank"].get_reading(),
        "published": published.var_level.value(),
    }

    system.isimu_stop()

    # The pre-run step is one-shot, so a republication declared now would never
    # get an equation -- and the seed is registered in the very method that
    # refuses it. Recorded rather than asserted here, so every test below stays
    # a reader of a snapshot.
    obs["late_error"] = None
    try:
        instrument.add_control_out(
            name="late",
            kind="value",
            emit={"op": "republish", "input": "tank"},
        )
    except Exception as err:  # noqa: BLE001 -- the refusal IS the observation
        obs["late_error"] = err

    system.deleteSys()


def run_monte_carlo_scenario(obs):
    """Sample the publication at instant 0 of SEVERAL sequences."""
    system = muscadet.System(name="CtrlSeedMonteCarlo")

    add_tank(system, "CAP")
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="q", demand=SEED_DEMAND
    )
    add_instrument(system, "INSTR")

    system.connect_flow(source="CAP", target="SINK", flow_name="q")
    system.connect("CAP", "tank_level_out", "INSTR", "tank_level_in")

    system.add_indicator_var(component="INSTR", var="^reading_level$", stats=["mean"])

    system.simulate(
        {"nb_runs": SEED_RUNS, "schedule": SEED_SCHEDULE, "seed": SEED_MC_SEED}
    )

    means = {}
    for name, indicator in system.indicators.items():
        frame = indicator.values
        means[name] = {
            round(float(row["instant"]), 4): float(row["values"])
            for _, row in frame[frame["stat"] == "mean"].iterrows()
        }

    obs["mc_means"] = means["INSTR_reading_level"]

    system.deleteSys()


def run_forced_scenario(obs):
    """An output a mode holds publishes the forced number at t = 0 too."""
    system = muscadet.System(name="CtrlSeedForced")

    add_tank(system, "CAP")
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="q", demand=SEED_DEMAND
    )
    instrument = add_instrument(system, "INSTR")

    system.connect_flow(source="CAP", target="SINK", flow_name="q")
    system.connect("CAP", "tank_level_out", "INSTR", "tank_level_in")

    add_clock(system.comp["SINK"], 1.0)

    # Written before the run, which is how PyCATSHOO takes a value as the
    # variable's initial one: the forcing therefore stands at t = 0, which is
    # exactly the instant this module is about.
    forced, forced_value = instrument.emit_forced["reading"]
    forced.setValue(True)
    forced_value.setValue(SEED_FORCED)

    system.isimu_start()

    obs["forced_at_zero"] = {
        "time": system.currentTime(),
        "published": instrument.controls_out["reading"].var_level.value(),
    }

    system.isimu_stop()
    system.deleteSys()


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_interactive_scenario(obs)
    run_monte_carlo_scenario(obs)
    run_forced_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# What is published before anything has been integrated
# ----------------------------------------------------------------------


def test_the_publication_at_instant_zero_is_the_observed_quantity(the_run):
    """The defect itself: the default was published until the first step."""
    at_zero = the_run["at_zero"]

    assert at_zero["time"] == pytest.approx(0.0), "the session must not have stepped"
    assert at_zero["observed"] == pytest.approx(SEED_LEVEL)
    assert at_zero["published"] == pytest.approx(SEED_PUBLISHED)


def test_the_seed_goes_through_the_gain_like_any_publication(the_run):
    """One publication path, so a mode clamping the gain reaches the seed too."""
    at_zero = the_run["at_zero"]

    assert at_zero["gain"] == pytest.approx(SEED_GAIN)
    assert at_zero["published"] == pytest.approx(at_zero["observed"] * SEED_GAIN)
    # The fill follows the level, as it does at every other instant: an
    # observer reads the same number on both aliases.
    assert at_zero["fill"] == pytest.approx(SEED_PUBLISHED)


def test_the_seed_is_a_starting_point_and_not_a_stuck_value(the_run):
    """The equation still owns the publication once the clock has moved."""
    stepped = the_run["stepped"]

    assert stepped["time"] >= 1.0
    assert stepped["observed"] < SEED_LEVEL, "the tank must have drained"
    assert stepped["published"] == pytest.approx(stepped["observed"] * SEED_GAIN)


# ----------------------------------------------------------------------
# What a regulation reading such an instrument does at t = 0
# ----------------------------------------------------------------------


def test_a_montage_already_past_its_threshold_triggers_at_instant_zero(the_run):
    """The consequence a model notices: a regulation that used to sit idle."""
    at_zero = the_run["at_zero"]

    assert at_zero["regulation_reads"] == pytest.approx(SEED_PUBLISHED)
    assert SEED_PUBLISHED >= SEED_THRESHOLD, "the montage must start past it"
    assert (
        at_zero["regulation_runs"] is True
    ), "a regulation past its threshold must trigger at t = 0"


# ----------------------------------------------------------------------
# Every sequence, not only the first
# ----------------------------------------------------------------------


def test_every_monte_carlo_sequence_starts_from_an_observed_value(the_run):
    """Seeded once, this mean would be the value divided by the run count."""
    means = the_run["mc_means"]

    assert means[0.0] == pytest.approx(SEED_PUBLISHED), (
        "the mean at instant 0 is the seeded publication only if every "
        f"sequence was seeded; seeded once it would be {SEED_PUBLISHED / SEED_RUNS}"
    )


def test_the_monte_carlo_publication_keeps_tracking_after_the_seed(the_run):
    """The power check: the seed is not the only thing the run reports."""
    means = the_run["mc_means"]

    assert means[1.0] < means[0.0], "the tank drains, so the reading falls"
    assert means[1.0] == pytest.approx((SEED_LEVEL - SEED_DEMAND) * SEED_GAIN, rel=1e-3)


# ----------------------------------------------------------------------
# What the seed must NOT go around
# ----------------------------------------------------------------------


def test_a_forced_output_publishes_its_forced_value_at_instant_zero(the_run):
    """The seed is an ordinary publication, forcing included."""
    forced = the_run["forced_at_zero"]

    assert forced["time"] == pytest.approx(0.0)
    assert forced["published"] == pytest.approx(SEED_FORCED * SEED_GAIN)


def test_a_republication_declared_after_the_pre_run_step_is_still_refused(the_run):
    """The seed is registered in the method that raises this: it must stand."""
    error = the_run["late_error"]

    assert error is not None, "a late republication must be refused"
    assert isinstance(error, ValueError)

    message = str(error)
    assert "'late'" in message
    assert "pre-run" in message


def test_delete(the_run):
    cod3s.terminate_session()
