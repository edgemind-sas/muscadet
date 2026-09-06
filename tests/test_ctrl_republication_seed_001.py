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

The same fault, written twice
----------------------------
A republication is written twice in this library. A controller's ``"value"``
output publishes through a PDMP equation of its own; an ``ObjFlow`` publishing a
sourced ``MeasurementOut`` -- which is what the shipped ``SensorContinuous``
compiles its ``publish`` channel to -- publishes through
``compute_measurements``. Neither equation runs at instant 0, so both carried
the fault and both are seeded. The **shipped sensor** scenario is the one that
matters to a model: it is the instrument a model actually declares.

And the ORDER the seeds run in
------------------------------
PyCATSHOO calls start methods in the order they were REGISTERED, one global list
with component boundaries ignored. Registered where each output is declared, the
seeds would therefore run in declaration order, while controllers *evaluate* in
the order derived from the signal graph. The two agree on a montage declared in
signal order and diverge otherwise, so the **chain order** scenario declares one
downstream first: a controller thresholding an instrument, then the instrument
relaying, then the instrument observing. Every seed is registered at the pre-run
step instead, in the derived order, which is what makes that montage settle
whole at instant 0 rather than one hop at a time.

Five scenarios, each built, driven and deleted before the next one starts:
PyCATSHOO forbids more than one live system per process.

* **interactive** -- the reading and the threshold, read at t = 0 BEFORE any
  step, then re-read after the session has moved so the seed is shown to be a
  starting point and not a stuck value. The refusal of a republication declared
  after the pre-run step is recorded on the same live system.
* **monte carlo** -- the mean of the published variable at instant 0 over
  several sequences. Seeded once, that mean would be the value divided by the
  sequence count; seeded at every sequence start, it is the value.
* **forcing** -- an output a mode holds publishes the forced number at t = 0
  too, gain included: the seed goes through the ordinary publication path or it
  starts a sequence on a number the next step contradicts.
* **chain order** -- two republications and a threshold, declared against the
  direction the signal travels.
* **shipped sensor** -- the ``ObjFlow`` republication, declared the way a model
  declares one.
"""

import cod3s
import muscadet
import pytest

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SensorContinuous,
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

#: The gain of the second hop of the chain scenario. Chosen so the relayed
#: number is neither the tank's own reading nor the instrument's, and a hop
#: settled from a default is therefore visible rather than plausible.
SEED_RELAY_GAIN = 0.5

#: What the relay publishes at t = 0, if the instrument before it published.
SEED_RELAYED = SEED_PUBLISHED * SEED_RELAY_GAIN

#: The chain's threshold, standing below the relayed reading and above the
#: default a stale hop would carry.
SEED_CHAIN_THRESHOLD = 30.0

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


def run_chain_order_scenario(obs):
    """Two republications and a threshold, declared DOWNSTREAM FIRST.

    Declaration order is then the reverse of the order the signal travels in,
    so nothing but a seed ordered by the wiring settles the montage at instant
    0. Seeded in declaration order, the relay would take the instrument's
    default and the threshold the relay's.
    """
    system = muscadet.System(name="CtrlSeedChainOrder")

    add_tank(system, "CAP")
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="q", demand=SEED_DEMAND
    )
    system.connect_flow(source="CAP", target="SINK", flow_name="q")

    system.add_component(
        name="REG",
        cls="ObjCtrl",
        controls_in=[{"name": "relayed"}],
        controls_out=[
            {
                "name": "run",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "relayed",
                    "operator": ">=",
                    "threshold": SEED_CHAIN_THRESHOLD,
                },
            }
        ],
    )
    system.add_component(
        name="RELAY",
        cls="ObjCtrl",
        controls_in=[{"name": "reading"}],
        controls_out=[
            {
                "name": "relayed",
                "kind": "value",
                "emit": {
                    "op": "republish",
                    "input": "reading",
                    "gain": SEED_RELAY_GAIN,
                },
            }
        ],
    )
    add_instrument(system, "INSTR")

    system.connect("CAP", "tank_level_out", "INSTR", "tank_level_in")
    system.connect("INSTR", "reading_level_out", "RELAY", "reading_level_in")
    system.connect("RELAY", "relayed_level_out", "REG", "relayed_level_in")

    system.isimu_start()

    obs["chain_at_zero"] = {
        "time": system.currentTime(),
        "published": system.comp["INSTR"].controls_out["reading"].var_level.value(),
        "relayed": system.comp["RELAY"].controls_out["relayed"].var_level.value(),
        "regulation_runs": system.comp["REG"].controls_out["run"].get_signal(),
    }

    system.isimu_stop()

    obs["chain_controller_order"] = system.equation_order.controller_order

    system.deleteSys()


def run_shipped_sensor_scenario(obs):
    """The OTHER republication: a sourced ``MeasurementOut`` on an ``ObjFlow``.

    Declared through ``SensorContinuous``, which is what a model writes, and
    whose ``publish`` channel compiles to exactly that. Its band is declared
    beside the publication, so the montage is the shipped component as it is
    really used rather than a republication-only corner of it.
    """
    system = muscadet.System(name="CtrlSeedShippedSensor")

    add_tank(system, "CAP")
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="q", demand=SEED_DEMAND
    )
    system.connect_flow(source="CAP", target="SINK", flow_name="q")

    system.add_component(
        name="SENSOR",
        cls="SensorContinuous",
        measurement="tank",
        control="fill",
        direction="below",
        activate=SEED_CHAIN_THRESHOLD,
        publish="reported",
        gain=SEED_GAIN,
    )
    system.connect("CAP", "tank_level_out", "SENSOR", "tank_level_in")

    system.isimu_start()

    obs["sensor_at_zero"] = {
        "time": system.currentTime(),
        "observed": system.comp["SENSOR"].measurements_in["tank"].get_reading(),
        "reported": system.comp["SENSOR"].measurements_out["reported"].get_level(),
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
    run_chain_order_scenario(obs)
    run_shipped_sensor_scenario(obs)

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
    """A start method, not a one-off write, and this is what tells them apart.

    The engine restores every declared init before EVERY sequence, the first
    one included, so a value written once at declaration is already gone by the
    time the first sequence starts: measured with the seed neutralised, this
    mean is the declared default over the whole campaign, not a fraction of the
    published value. That is why the discriminator is the mean over several
    sequences rather than the reading of one.
    """
    means = the_run["mc_means"]

    assert means[0.0] == pytest.approx(SEED_PUBLISHED), (
        "the mean at instant 0 is the seeded publication only if every "
        "sequence was seeded; unseeded it is the declared default, 0.0"
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


# ----------------------------------------------------------------------
# A chain settles whole, whatever order it was declared in
# ----------------------------------------------------------------------


def test_a_chain_of_republications_settles_whole_at_instant_zero(the_run):
    """Both hops, on a montage declared against the direction its signal takes.

    Start methods run in registration order, so a seed registered where its
    output is declared would settle the relay from the instrument's default.
    """
    at_zero = the_run["chain_at_zero"]

    assert at_zero["time"] == pytest.approx(0.0)
    assert at_zero["published"] == pytest.approx(SEED_PUBLISHED)
    assert at_zero["relayed"] == pytest.approx(SEED_RELAYED)


def test_a_threshold_on_the_far_end_of_the_chain_fires_at_instant_zero(the_run):
    """A boolean output is seeded in the derived order too, not before it.

    Its seed reads a republication, so ordering the value seeds and leaving the
    boolean ones in declaration order would settle this one first, on a reading
    that has not been written yet.
    """
    assert SEED_RELAYED >= SEED_CHAIN_THRESHOLD, "the montage must start past it"
    assert the_run["chain_at_zero"]["regulation_runs"] is True


def test_the_seed_order_is_the_order_the_equations_take(the_run):
    """The mechanism behind the two assertions above, read from the order itself."""
    assert the_run["chain_controller_order"] == ["INSTR", "RELAY", "REG"]


# ----------------------------------------------------------------------
# The other republication: an ObjFlow instrument, which is what a model writes
# ----------------------------------------------------------------------


def test_the_shipped_sensor_reports_what_it_observes_at_instant_zero(the_run):
    """``SensorContinuous`` publishes through ``compute_measurements``.

    A different equation from the controller's, and one the engine does not run
    at instant 0 either. A capacity has never had this fault, its levels being
    given their starting values at declaration; an observer is not supposed to
    be able to tell a capacity from a republisher.
    """
    at_zero = the_run["sensor_at_zero"]

    assert at_zero["time"] == pytest.approx(0.0)
    assert at_zero["observed"] == pytest.approx(SEED_LEVEL)
    assert at_zero["reported"] == pytest.approx(SEED_PUBLISHED)


def test_a_republication_declared_after_the_pre_run_step_is_still_refused(the_run):
    """The seed is registered at that step too, so the refusal matters twice.

    A late output would now miss its seed as well as its equation, and would
    publish its declared default for the whole run with nothing raised.
    """
    error = the_run["late_error"]

    assert error is not None, "a late republication must be refused"
    assert isinstance(error, ValueError)

    message = str(error)
    assert "'late'" in message
    assert "pre-run" in message


def test_delete(the_run):
    cod3s.terminate_session()
