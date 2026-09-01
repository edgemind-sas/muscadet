"""A failure mode reaches the OUTPUT of a controller (R44).

What this unit pins down
------------------------
Until now a controller's thresholds were constants captured in the closures
that carry its automaton conditions. Nothing of the model held them, so they
were unreachable three times over: an instance could not be tuned apart from
its class, an indicator had nothing to name, and a failure mode had nothing to
clamp. This module asserts that they are **variables of the engine**, and that
three effects reach a controller output through variables of the same kind:

========================================= ===========================================
``{output}_level_gain``                   what a value output publishes, times a gain
``{output}_forced`` / ``_forced_value``   what a value output publishes, replaced
``{output}_signal_available``             a boolean output blinded, back to its default
========================================= ===========================================

Each is an ordinary component variable a ``cod3s.ObjFM*`` names by its exact
basename, which is the whole point: a controller is a peer of ``ObjFlow`` and
carries no flow, so the muscadet regex-on-flows spelling has nothing to match
on it, while the engine's own exact-name spelling has everything.

Why the blinded output is the scenario that matters
---------------------------------------------------
The cyber case of the reference model: an instrument is not destroyed, it is
made to stop speaking. The reading is still right, the band underneath is still
activated, the montage still believes it is filling its tank -- and the pump
never receives the order. Nothing raises. What the run produces is a trajectory
that diverges from the healthy twin's, and that is the only observable.

The montage is built, wired and driven once in the fixture: PyCATSHOO forbids
more than one live system per process, and the interactive session must advance
monotonically for the trace to mean anything.
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

#: Every tank of the montage moves one unit per unit time under its consumer,
#: so a level and a date are the same number and the trace reads directly.
UNIT_RATE = 1.0

# -- Montage A: a band controller blinded in mid-command, and its healthy twin.
BAND_INIT = 10.0
BAND_ACTIVATE = 3.0
BAND_RELEASE = 7.0
BAND_FILL_RATE = 2.0

#: 10 - t == 3: where both bands activate and both sources start.
BAND_ACTIVATE_DATE = BAND_INIT - BAND_ACTIVATE

#: 3 + (t - 7) == 7: where the HEALTHY band releases. The blinded one never
#: gets there -- its tank is draining, not filling.
BAND_RELEASE_DATE = BAND_ACTIVATE_DATE + (BAND_RELEASE - BAND_ACTIVATE)

#: Where the instrument is blinded, and where it starts speaking again. Both
#: fall strictly inside the window the band holds, which is what makes the
#: blinding observable at all: the band says "fill" throughout.
BLIND_DATE = 9.0
UNBLIND_DELAY = 3.0
UNBLIND_DATE = BLIND_DATE + UNBLIND_DELAY

# -- Montage B: a reading forced to a lie, and a gauge whose gain is killed.
FORCE_DATE = 4.0
FORCED_VALUE = 42.0
GAIN_DATE = 6.0
DEAD_GAIN = 0.0

#: A level no reading of this montage ever reaches: what the combination of
#: the downstream controller compares against, so its two nodes declare two
#: variables without adding an event to the trace.
UNREACHABLE_THRESHOLD = 1000.0

# -- Montage C: two instances of ONE class, tuned to different thresholds.
SENSOR_DEFAULT_THRESHOLD = 5.0
SENSOR_LOW_THRESHOLD = 2.0
SENSOR_HIGH_THRESHOLD = 8.0

#: Where the session stops driving. Chosen so the blinded tank and its twin
#: are still telling different stories there.
HORIZON = 13.0

#: Dates the session is given something to stop at, so the trace has readings
#: BETWEEN the events. None of them is an event date: every stop at an event
#: must be that event's own doing.
CLOCKS = (1.0, 3.0, 5.0, 10.0, 10.5, 13.0)

#: The solver root-finds a crossing rather than landing on it exactly.
CROSSING_TOL = 0.01

#: How many Monte Carlo sequences the indicator run takes. The montage is
#: deterministic -- delay laws and constant rates -- so one would do; four say
#: so out loud, a mean over four identical runs being the value itself.
NB_RUNS = 4


class ObjCtrlFailure001Sensor(muscadet.ObjCtrl):
    """One instrument class, declared once and instantiated twice.

    It carries no threshold argument on purpose. What separates two instances
    is not their declaration but the VALUE of ``alarm_threshold``, the variable
    the grammar creates -- which is exactly what could not be done while the
    threshold was a constant captured in a closure.
    """

    def __init__(self, name, **kwargs):
        super().__init__(
            name,
            controls_in=[{"name": "tank_m"}],
            controls_out=[
                {
                    "name": "alarm",
                    "kind": "bool",
                    "emit": {
                        "op": "compare",
                        "input": "tank_m",
                        "operator": ">=",
                        "threshold": SENSOR_DEFAULT_THRESHOLD,
                    },
                }
            ],
            **kwargs,
        )


def record_watched_automata(system):
    """Record the automata this system is asked to WATCH, by name.

    The PDMP manager offers no way to ask it back what it watches, so the
    registration is the only observation available -- the same shape the
    grammar module uses to inventory the sensitive methods, and a real
    observation for the same reason: it sees every registration, not only the
    ones this unit writes.

    Returns
    -------
    tuple
        ``(names, restore)`` -- the list the wrapper appends to, and the
        callable that puts the system back as it was.
    """
    names = []
    original = system.pdmp_add_watched_automaton

    def wrapper(automaton):
        names.append(automaton.name)
        return original(automaton)

    system.pdmp_add_watched_automaton = wrapper

    def restore():
        system.pdmp_add_watched_automaton = original

    return names, restore


def add_clock(comp, date):
    """Give the interactive session a date it can always stop at."""
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def snapshot(system):
    """Everything the three montages read at the current stop."""
    live = system.comp["CTRL_LIVE"]
    ref = system.comp["CTRL_REF"]
    meter = system.comp["CTRL_M"]
    down = system.comp["CTRL_DOWN"]

    return {
        "time": system.currentTime(),
        # Montage A
        "level_live": system.comp["CAP_LIVE"].capacities["tank_live"].get_quantity("q"),
        "level_ref": system.comp["CAP_REF"].capacities["tank_ref"].get_quantity("q"),
        "signal_live": live.controls_out["fill"].get_signal(),
        "signal_ref": ref.controls_out["fill"].get_signal(),
        "band_live": live.emit_automata["fill"][0]
        .get_state_by_name("fill_band_activated")
        ._bkd.isActive(),
        "supplied_live": system.comp["SRC_LIVE"].flows_out["q"].var_fed.value(),
        "available_live": live.controls_out["fill"].get_available(),
        # Montage B
        "level_m": system.comp["CAP_M"].capacities["tank_m"].get_quantity("q"),
        "echo": meter.controls_out["echo"].get_level(),
        "gauge": meter.controls_out["gauge"].get_level(),
        "echo_read": down.controls_in["echo"].get_reading(),
        "gauge_read": down.controls_in["gauge"].get_reading(),
        "echo_forced": bool(meter.emit_forced["echo"][0].value()),
        "gauge_gain": meter.controls_out["gauge"].get_gain(),
        # Montage C
        "alarm_low": system.comp["SENSOR_LOW"].controls_out["alarm"].get_signal(),
        "alarm_high": system.comp["SENSOR_HIGH"].controls_out["alarm"].get_signal(),
    }


def drive(system, horizon, limit=120):
    """Step the session to ``horizon``, recording what every stop saw."""
    trace = []

    for _ in range(limit):
        trace.append(snapshot(system))

        if system.currentTime() >= horizon:
            return trace

        system.isimu_step_forward()

    raise AssertionError(f"the session did not reach {horizon} in {limit} steps")


def build_montage_a(system):
    """A controlled loop and its twin: one of them will be blinded."""
    for tag in ("LIVE", "REF"):
        tank = f"tank_{tag.lower()}"

        system.add_component(
            name=f"SRC_{tag}",
            cls="SourceContinuous",
            flow="q",
            rate=BAND_FILL_RATE,
            control="fill",
        )
        system.add_component(
            name=f"CAP_{tag}",
            cls="CapacityContinuous",
            flow="q",
            capacity=100.0,
            capacity_name=tank,
            content_init={"q": BAND_INIT},
            fill_rate=float("inf"),
        )
        system.add_component(
            name=f"SINK_{tag}", cls="ConsumerContinuous", flow="q", demand=UNIT_RATE
        )
        system.add_component(
            name=f"CTRL_{tag}",
            cls="ObjCtrl",
            controls_in=[{"name": tank}],
            controls_out=[
                {
                    "name": "fill",
                    "kind": "bool",
                    "emit": {
                        "op": "band",
                        "input": tank,
                        "direction": "below",
                        "activate": BAND_ACTIVATE,
                        "release": BAND_RELEASE,
                    },
                }
            ],
        )

        system.connect_flow(source=f"SRC_{tag}", target=f"CAP_{tag}", flow_name="q")
        system.connect_flow(source=f"CAP_{tag}", target=f"SINK_{tag}", flow_name="q")
        system.connect(
            f"CAP_{tag}", f"{tank}_level_out", f"CTRL_{tag}", f"{tank}_level_in"
        )
        system.connect(f"CTRL_{tag}", "fill_out", f"SRC_{tag}", "fill_in")


def build_montage_b_and_c(system):
    """One rising tank, two instruments that lie about it, two that alarm on it."""
    system.add_component(name="SRC_M", cls="SourceContinuous", flow="q", rate=UNIT_RATE)
    system.add_component(
        name="CAP_M",
        cls="CapacityContinuous",
        flow="q",
        capacity=1000.0,
        capacity_name="tank_m",
        content_init={"q": 0.0},
        fill_rate=float("inf"),
    )
    system.connect_flow(source="SRC_M", target="CAP_M", flow_name="q")

    system.add_component(
        name="CTRL_M",
        cls="ObjCtrl",
        controls_in=[{"name": "tank_m"}],
        controls_out=[
            {
                "name": name,
                "kind": "value",
                "emit": {"op": "republish", "input": "tank_m", "gain": 1.0},
            }
            for name in ("echo", "gauge")
        ],
    )
    system.connect("CAP_M", "tank_m_level_out", "CTRL_M", "tank_m_level_in")

    system.add_component(
        name="CTRL_DOWN",
        cls="ObjCtrl",
        controls_in=[{"name": "echo"}, {"name": "gauge"}],
        controls_out=[
            {
                "name": "both",
                "kind": "bool",
                # Two comparisons under one combination, and the only thing
                # asked of them is that they be told APART: one variable per
                # node POSITION. Both thresholds are out of reach of this
                # montage on purpose -- what a combination computes is the
                # grammar module's business, and an automaton that never fires
                # adds no stop to a trace read date by date here.
                "emit": {
                    "op": "combine",
                    "logic": "and",
                    "operands": [
                        {
                            "op": "compare",
                            "input": name,
                            "operator": ">=",
                            "threshold": UNREACHABLE_THRESHOLD,
                        }
                        for name in ("echo", "gauge")
                    ],
                },
            }
        ],
    )
    system.connect("CTRL_M", "echo_level_out", "CTRL_DOWN", "echo_level_in")
    system.connect("CTRL_M", "gauge_level_out", "CTRL_DOWN", "gauge_level_in")

    for name, threshold in (
        ("SENSOR_LOW", SENSOR_LOW_THRESHOLD),
        ("SENSOR_HIGH", SENSOR_HIGH_THRESHOLD),
    ):
        system.add_component(name=name, cls="ObjCtrlFailure001Sensor")
        system.connect("CAP_M", "tank_m_level_out", name, "tank_m_level_in")
        # The tuning, and the whole of montage C: one class, two instances,
        # two thresholds. Written before the run starts, which is when
        # PyCATSHOO takes a write as the variable's INITIAL value -- so the
        # tuning survives every Monte Carlo sequence.
        system.comp[name].variable("alarm_threshold").setValue(threshold)


def build_failure_modes(system):
    """The three effects, each spelled as an exact variable of its target."""
    # 1. The blinded instrument: the boolean output stops carrying its band.
    #    Both polarities are declared, because neither the endpoint nor the
    #    signal it gates is reinitialised: what does not fall back on its own
    #    has to be handed back.
    system.add_component(
        cls="ObjFMDelay",
        fm_name="blind",
        targets=["CTRL_LIVE"],
        failure_param=BLIND_DATE,
        failure_effects={"fill_signal_available": False},
        repair_param=UNBLIND_DELAY,
        repair_effects={"fill_signal_available": True},
    )

    # 2. The forced reading: the instrument publishes a number of its own.
    system.add_component(
        cls="ObjFMDelay",
        fm_name="stuck",
        targets=["CTRL_M"],
        failure_param=FORCE_DATE,
        failure_effects={"echo_forced": True, "echo_forced_value": FORCED_VALUE},
        repair_cond=False,
        repair_effects={"echo_forced": False},
    )

    # 3. The dead gauge: the gain everything it publishes is multiplied by.
    system.add_component(
        cls="ObjFMDelay",
        fm_name="dead",
        targets=["CTRL_M"],
        failure_param=GAIN_DATE,
        failure_effects={"gauge_level_gain": DEAD_GAIN},
        repair_cond=False,
        repair_effects={"gauge_level_gain": 1.0},
    )


@pytest.fixture(scope="module")
def obs():
    """Build the three montages, drive one session, then run the indicators."""
    observations = {}

    system = muscadet.System(name="ObjCtrlFailure001")

    watched, restore_watched = record_watched_automata(system)

    build_montage_a(system)
    build_montage_b_and_c(system)
    build_failure_modes(system)

    restore_watched()
    observations["watched"] = watched

    for date in CLOCKS:
        add_clock(system.comp["SINK_REF"], date)

    system.isimu_start()
    observations["trace"] = drive(system, HORIZON)
    system.isimu_stop()

    # The threshold is a variable, so it is a target an indicator can name --
    # which is the second thing a constant in a closure made impossible.
    for name in ("SENSOR_LOW", "SENSOR_HIGH"):
        system.add_indicator_var(
            component=f"^{name}$", var="^alarm_threshold$", stats=["mean"]
        )

    system.simulate(
        {"nb_runs": NB_RUNS, "schedule": [{"start": 0, "end": 4, "nvalues": 2}]}
    )

    observations["indicators"] = {
        indic_name: {
            round(float(row["instant"]), 4): float(row["values"])
            for _, row in indic.values[indic.values["stat"] == "mean"].iterrows()
        }
        for indic_name, indic in system.indicators.items()
    }
    observations["system"] = system

    return observations


def stop_at(trace, date, tol=CROSSING_TOL):
    """The SETTLED stop at ``date``, or ``(None, None)``.

    The session stops twice at an event date: once because the integration was
    told to stop there, and once more when the instantaneous transition it
    enabled actually fires. What an output carries is read after the second.
    """
    found = (None, None)

    for index, entry in enumerate(trace):
        if entry["time"] == pytest.approx(date, abs=tol):
            found = (index, entry)

    return found


def before(trace, date, tol=CROSSING_TOL):
    """The last stop strictly before ``date``."""
    earlier = [entry for entry in trace if entry["time"] < date - tol]

    assert earlier, f"nothing was recorded before {date}; walked {dates(trace)}"

    return earlier[-1]


def between(trace, start, end, tol=CROSSING_TOL):
    """Every stop strictly inside ``(start, end)``."""
    return [entry for entry in trace if start + tol < entry["time"] < end - tol]


def dates(trace):
    """The dates the session stopped at, rounded for a readable message."""
    return [round(entry["time"], 4) for entry in trace]


# 1. A mode forces the value of a numeric output
# ==============================================


def test_the_downstream_reads_the_forced_value_from_the_date_it_is_forced(obs):
    """The instrument publishes a number of its own, and the reading is wrong.

    Read on the OBSERVER and not on the publisher: what a failure of an
    instrument means is what the next component believes, and a controller's
    value output is another controller's observation input.

    **What lands AT the date and what lands just after it.** The mode's clamp
    is applied at its own event, so the forcing flag is already up at the stop
    the mode produced. What that flag governs is a PDMP explicit variable, and
    an explicit variable is refreshed by the INTEGRATOR: its value at the
    instant of a discrete event is still the one solved before that event, and
    the forced publication appears at the first integration point past it. That
    is not a property of forcing -- ``{name}_level_gain``, muscadet's shipped
    endpoint, behaves identically -- so it is asserted rather than worked
    around.
    """
    trace = obs["trace"]

    _, at_date = stop_at(trace, FORCE_DATE)
    assert (
        at_date is not None
    ), f"no stop at {FORCE_DATE}; the session walked {dates(trace)}"
    assert at_date["echo_forced"] is True, "the clamp lands at the mode's own event"

    healthy = before(trace, FORCE_DATE)
    assert healthy["echo_forced"] is False
    assert healthy["echo_read"] == pytest.approx(healthy["level_m"], rel=1e-6)
    assert healthy["echo_read"] != pytest.approx(FORCED_VALUE)

    after = [entry for entry in trace if entry["time"] > FORCE_DATE + CROSSING_TOL]
    assert after, f"nothing recorded after {FORCE_DATE}: {dates(trace)}"
    assert after[0]["echo_read"] == pytest.approx(FORCED_VALUE)
    assert after[0]["echo"] == pytest.approx(FORCED_VALUE)


def test_a_forced_output_stays_forced_while_the_reading_moves(obs):
    """A level clamp, not a pulse: the lie holds while the mode holds."""
    after = [
        entry for entry in obs["trace"] if entry["time"] > FORCE_DATE + CROSSING_TOL
    ]

    assert len(after) >= 3, f"too few stops after {FORCE_DATE}: {dates(obs['trace'])}"
    for entry in after:
        assert entry["echo_read"] == pytest.approx(FORCED_VALUE)
        # And the observed quantity went on moving underneath it.
        assert entry["level_m"] > FORCE_DATE


# 2. A mode blinds a boolean output
# =================================


def test_the_band_still_commands_while_the_blinded_output_says_nothing(obs):
    """The reading is right, the band is activated, and the pump is idle.

    This is the whole of the cyber scenario: nothing is broken, nothing is
    raised, and the order simply does not arrive.
    """
    blinded = between(obs["trace"], BLIND_DATE, UNBLIND_DATE)

    assert len(blinded) >= 1, (
        "the montage must be read at least once while blinded; it walked "
        f"{dates(obs['trace'])}"
    )

    for entry in blinded:
        assert entry["band_live"] is True, "the band underneath is still commanding"
        assert entry["available_live"] is False
        assert entry["signal_live"] is False, "and the output carries nothing"
        assert entry["supplied_live"] == pytest.approx(0.0)

        if entry["time"] < BAND_RELEASE_DATE:
            # The twin, wired identically and not blinded, is still being
            # filled. Past its own release edge it stops of its own accord,
            # which is the band doing its job and not a blinding.
            assert entry["signal_ref"] is True


def test_the_blinded_trajectory_diverges_from_its_healthy_twin(obs):
    """The only observable a blinding leaves: two tanks that stop agreeing."""
    trace = obs["trace"]

    for entry in (
        before(trace, BAND_ACTIVATE_DATE),
        stop_at(trace, BAND_ACTIVATE_DATE)[1],
    ):
        assert entry is not None
        assert entry["level_live"] == pytest.approx(
            entry["level_ref"], abs=CROSSING_TOL
        )

    for entry in between(trace, BLIND_DATE, HORIZON + 1.0):
        assert entry["level_live"] < entry["level_ref"] - CROSSING_TOL


def test_the_blinding_raises_nothing_at_all(obs):
    """The run reached its horizon. A silent failure is what this models."""
    assert obs["trace"][-1]["time"] >= HORIZON


def test_a_repaired_instrument_starts_commanding_again(obs):
    """The second polarity, and why a mode owes it.

    A boolean output is a state, not a pulse: it is not reinitialised, so it
    does not fall back on its own. Handing the availability back therefore has
    to RE-EVALUATE the output, not merely stop clamping it -- otherwise the
    montage would stay idle with nothing wrong anywhere.
    """
    trace = obs["trace"]

    _, entry = stop_at(trace, UNBLIND_DATE)
    assert entry is not None, f"no stop at {UNBLIND_DATE}; walked {dates(trace)}"
    assert entry["available_live"] is True
    assert entry["signal_live"] is True
    assert entry["band_live"] is True

    later = between(trace, UNBLIND_DATE, HORIZON + 1.0)
    assert later, f"nothing recorded after {UNBLIND_DATE}: {dates(trace)}"
    for entry in later:
        assert entry["signal_live"] is True
        assert entry["supplied_live"] == pytest.approx(BAND_FILL_RATE)


# 3. A gain of zero kills the reading and nothing else
# ====================================================


def test_a_dead_gain_annuls_the_reading_and_not_the_quantity(obs):
    """The instrument lies; the tank goes on filling.

    The distinction is the point: a gauge reading zero is not an empty tank,
    and a model that could not tell the two apart would report an outage that
    never happened.
    """
    trace = obs["trace"]

    _, at_date = stop_at(trace, GAIN_DATE)
    assert at_date is not None, f"no stop at {GAIN_DATE}; walked {dates(trace)}"
    assert at_date["gauge_gain"] == pytest.approx(DEAD_GAIN), (
        "the clamp lands at the mode's own event; what it governs is refreshed "
        "at the next integration point, as it is for every published reading"
    )

    healthy = before(trace, GAIN_DATE)
    assert healthy["gauge_gain"] == pytest.approx(1.0)
    assert healthy["gauge_read"] == pytest.approx(healthy["level_m"], rel=1e-6)

    after = [entry for entry in trace if entry["time"] > GAIN_DATE + CROSSING_TOL]
    assert after, f"nothing recorded after {GAIN_DATE}: {dates(trace)}"

    for entry in after:
        assert entry["gauge_read"] == pytest.approx(0.0)
        assert entry["level_m"] > GAIN_DATE - CROSSING_TOL, "the tank kept filling"
        # And the instrument standing beside it is untouched: one gain, one
        # instrument.
        assert entry["echo_read"] == pytest.approx(FORCED_VALUE)


# 4. Two instances of one class, two thresholds, two dates
# ========================================================


def test_two_instances_of_one_class_switch_at_their_own_thresholds(obs):
    """The defect this unit exists to lift.

    Both instruments are the same class, wired to the same tank, and separated
    by nothing but the value of ``alarm_threshold``. With the threshold
    captured in a closure there is no such value to set, and three identical
    instruments tuned to three levels cannot be modelled at all.
    """
    trace = obs["trace"]

    for key, threshold in (
        ("alarm_low", SENSOR_LOW_THRESHOLD),
        ("alarm_high", SENSOR_HIGH_THRESHOLD),
    ):
        _, entry = stop_at(trace, threshold)
        assert (
            entry is not None
        ), f"no stop at {threshold} for {key}; the session walked {dates(trace)}"
        assert entry["level_m"] == pytest.approx(threshold, abs=CROSSING_TOL)
        assert entry[key] is True, f"{key} must be on AT its own threshold"
        assert before(trace, threshold)[key] is False

    # The two dates are distinct, and neither is the class's declared default:
    # what the run followed is the instance, not the declaration.
    assert SENSOR_LOW_THRESHOLD != SENSOR_HIGH_THRESHOLD
    assert SENSOR_DEFAULT_THRESHOLD not in (SENSOR_LOW_THRESHOLD, SENSOR_HIGH_THRESHOLD)

    low_on = stop_at(trace, SENSOR_LOW_THRESHOLD)[1]
    assert low_on["alarm_high"] is False, "the high instrument is still silent there"


def test_each_instance_holds_its_own_threshold_variable(obs):
    """One class, two variables, two values -- and the class default in neither."""
    system = obs["system"]

    for name, threshold in (
        ("SENSOR_LOW", SENSOR_LOW_THRESHOLD),
        ("SENSOR_HIGH", SENSOR_HIGH_THRESHOLD),
    ):
        comp = system.comp[name]
        basenames = {var.basename() for var in comp.variables()}

        assert "alarm_threshold" in basenames
        assert comp.variable("alarm_threshold").value() == pytest.approx(threshold)


# 5. A threshold is a target an indicator can name
# ================================================


def test_a_threshold_is_visible_in_the_results(obs):
    """The second thing a constant in a closure made impossible."""
    indicators = obs["indicators"]

    for name, threshold in (
        ("SENSOR_LOW", SENSOR_LOW_THRESHOLD),
        ("SENSOR_HIGH", SENSOR_HIGH_THRESHOLD),
    ):
        key = f"{name}_alarm_threshold"
        assert key in indicators, f"no indicator named {key}; got {sorted(indicators)}"

        values = indicators[key]
        assert values, f"indicator {key} produced no value"
        for instant, value in values.items():
            assert value == pytest.approx(
                threshold
            ), f"{key} reads {value} at t={instant}, expected {threshold}"


# 6. What the three effects are, declared
# =======================================


def test_the_three_effect_endpoints_are_variables_of_the_controller(obs):
    """The surface a mode names, by exact basename, on a controller."""
    system = obs["system"]

    meter = {var.basename() for var in system.comp["CTRL_M"].variables()}
    assert {"echo_level_gain", "echo_forced", "echo_forced_value"} <= meter
    assert {"gauge_level_gain", "gauge_forced", "gauge_forced_value"} <= meter

    live = {var.basename() for var in system.comp["CTRL_LIVE"].variables()}
    assert "fill_signal_available" in live

    # And the two natures do not borrow each other's endpoints: a boolean
    # output HAS a rest value, so blinding it is publishing that one; a number
    # has none, so forcing it must name one.
    assert "fill_forced" not in live
    assert "echo_signal_available" not in meter


def test_the_grammar_declares_every_number_it_carries_as_a_variable(obs):
    """One variable per number, named from the node's position in the tree."""
    system = obs["system"]

    band = system.comp["CTRL_LIVE"]
    assert set(band.emit_params) == {"fill_activate", "fill_release"}
    assert band.emit_params["fill_activate"].value() == pytest.approx(BAND_ACTIVATE)
    assert band.emit_params["fill_release"].value() == pytest.approx(BAND_RELEASE)

    sensor = system.comp["SENSOR_LOW"]
    assert set(sensor.emit_params) == {"alarm_threshold"}

    # Under a combination, the node's POSITION is what tells two comparisons
    # apart -- the same naming the automata of that subtree carry, so an effect
    # and a sequence entry name the same node.
    nested = system.comp["CTRL_DOWN"]
    assert set(nested.emit_params) == {
        "both_operand_0_threshold",
        "both_operand_1_threshold",
    }
    assert [aut.name for aut in nested.emit_automata["both"]] == [
        "CTRL_DOWN_both_operand_0_compare",
        "CTRL_DOWN_both_operand_1_compare",
    ]

    # A republication carries no threshold of its own: its number is the gain,
    # and the gain is MeasurementOut's variable, not a second spelling here.
    assert system.comp["CTRL_M"].emit_params == {}


def test_the_blinding_automaton_is_kept_apart_from_the_output_value(obs):
    """What an output's VALUE compiled to, and what merely re-runs it.

    ``emit_automata`` answers the first question and must go on answering only
    that: the blinding automaton carries no threshold and dates no crossing.
    It is also the one automaton of this module the integration manager does
    NOT watch -- its condition is a boolean written by a discrete event, and a
    discrete event is already an exact date.
    """
    system = obs["system"]
    controller = system.comp["CTRL_LIVE"]

    assert [aut.name for aut in controller.emit_automata["fill"]] == [
        "CTRL_LIVE_fill_band"
    ]

    blinding = controller.blinding_automata["fill"]
    assert blinding.name == "CTRL_LIVE_fill_blinding"
    assert blinding not in controller.emit_automata["fill"]
    assert controller.automata_d[blinding.name] is blinding

    watched = obs["watched"]
    assert "CTRL_LIVE_fill_band" in watched
    assert blinding.name not in watched


def test_an_effect_naming_a_variable_the_controller_has_not_got_is_refused(obs):
    """The surface is closed, so a misspelt endpoint is loud rather than inert.

    Declared last on purpose: a refused failure mode is built far enough into
    ``cod3s.ObjFM`` to leave a component behind, and nothing after this reads
    the system again.
    """
    with pytest.raises(Exception) as error:
        obs["system"].add_component(
            cls="ObjFMDelay",
            fm_name="typo",
            targets=["CTRL_LIVE"],
            failure_param=1.0,
            failure_effects={"fill_signal_availabl": False},
            repair_cond=False,
        )

    message = str(error.value)
    assert "fill_signal_availabl" in message
    assert "CTRL_LIVE" in message


def test_delete(obs):
    obs["system"].deleteSys()
    cod3s.terminate_session()
