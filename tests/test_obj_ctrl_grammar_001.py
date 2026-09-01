"""A controller output is a COMPOSITION of closed operators, never a function.

What this unit pins down
------------------------
A controller says what an output carries through the ``emit`` key of that
output's declaration, and what it may say there is a closed grammar of four
operators:

======================= =============================================
``compare``             a reading against a threshold -> boolean
``band``                two thresholds and a direction -> boolean
``combine``             booleans by and / or / not / k-of-n -> boolean
``republish``           a reading, times a gain -> number
======================= =============================================

Why closed, and not a callable
------------------------------
The solver can only date a crossing exactly on a form it recognises. muscadet
already drew that conclusion once, on production profiles: a Python callable
has to **attest** its own continuity because nothing can inspect it, and a
discontinuous profile is refused outright, because the solver would otherwise
walk straight through the break inside an integration step. A closed grammar is
what lets every form compile to a mechanism the integration manager WATCHES --
so ``emit`` refuses a Python function even when that function carries the
continuity attestation :class:`muscadet.Profile` exists to give.

The second half of the same rule, and the one this module proves by inventory
rather than by argument: **no continuous quantity is read by a sensitive
method, anywhere on a controller**. A sensitive method fires when a variable
ANNOUNCES a change, and a level moving inside an integration step announces
nothing at all, so such a reading would simply never be re-evaluated. That is a
silent failure, not a slow one. A controller therefore re-evaluates its outputs
on the notification of a watched AUTOMATON -- a discrete object, which does
announce its state -- and reads the quantity itself live at that moment.

The whole montage is built, wired and driven once in the fixture: PyCATSHOO
forbids more than one live system per process, and the interactive session must
advance monotonically for the trace to mean anything.
"""

import cod3s
import muscadet
import pytest
import Pycatshoo as pyc

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

#: Every tank of the montage rises or falls by one unit per unit time, so a
#: threshold and a date are the same number and the trace reads directly.
UNIT_RATE = 1.0

#: Where the compare montage's alarm turns on: its tank starts empty and fills
#: at :data:`UNIT_RATE`.
ALARM_THRESHOLD = 5.0

#: The band montage: a tank draining from 10 at 1, refilled at 2 -- so net +1 --
#: while the controller holds its source open.
BAND_INIT = 10.0
BAND_ACTIVATE = 3.0
BAND_RELEASE = 7.0
BAND_FILL_RATE = 2.0

#: The dates the band crosses its two edges: 10 - t == 3, then 3 + (t - 7) == 7.
BAND_ACTIVATE_DATE = BAND_INIT - BAND_ACTIVATE
BAND_RELEASE_DATE = BAND_ACTIVATE_DATE + (BAND_RELEASE - BAND_ACTIVATE)

#: The k-of-n montage: three tanks filling at 1 from empty, against three
#: thresholds, so the k-th one is reached at the k-th threshold's own date.
KN_THRESHOLDS = (2.0, 4.0, 6.0)
KN_K = 2

#: The gain the republishing controller applies to what it reads.
ECHO_GAIN = 2.0

#: Where the negated comparison of montage A turns off. Its tank starts BELOW
#: it, so the output starts ON -- which is what the start seed has to produce,
#: no automaton having fired yet.
IDLE_THRESHOLD = 1.5

#: Dates the interactive session is given something to stop at, so the trace
#: has readings BETWEEN the crossings. None of them is a crossing date: a stop
#: at a crossing must be the crossing's own doing, which is the whole point.
CLOCKS = (1.0, 3.0, 8.0, 9.0, 10.0, 12.0, 14.0)

#: Where the session stops driving.
HORIZON = 14.0

#: The solver root-finds a crossing rather than landing on it exactly.
CROSSING_TOL = 0.01

#: The controllers of the montage, by name.
CONTROLLERS = ("CTRL_CMP", "CTRL_BAND", "CTRL_KN", "CTRL_OBS")


def add_clock(comp, date):
    """Give the interactive session a date it can always stop at."""
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def record_sensitive_methods():
    """Install a recorder over every ``addSensitiveMethod`` of the engine.

    The inventory of test 5, and it has to be taken this way: PyCATSHOO exposes
    ``addSensitiveMethod`` and ``removeSensitiveMethod`` and no way at all to
    ASK a variable which methods are registered on it. Recording the calls is
    therefore the only observation available, and it is a real one -- it sees
    every registration the whole library makes, not only the ones this unit
    writes.

    Returns
    -------
    tuple
        ``(records, restore)`` -- the list the wrappers append to, and the
        callable that puts the engine back as it was.
    """
    records = []
    originals = {}

    for owner in (pyc.IVarBase, pyc.IAutomaton):
        originals[owner] = owner.addSensitiveMethod

    def wrap(original):
        def wrapper(target, method_name, method):
            value_type = getattr(target, "valueType", None)
            records.append(
                {
                    "target": target.name(),
                    "owner": target.name().split(".")[0],
                    "class": type(target).__name__,
                    "method": method_name,
                    "value_type": None if value_type is None else str(value_type()),
                }
            )
            return original(target, method_name, method)

        return wrapper

    for owner, original in originals.items():
        owner.addSensitiveMethod = wrap(original)

    def restore():
        for owner, original in originals.items():
            owner.addSensitiveMethod = original

    return records, restore


def snapshot(system):
    """Everything the four montages read at the current stop."""
    cmp_ctrl = system.comp["CTRL_CMP"]
    band_ctrl = system.comp["CTRL_BAND"]
    kn_ctrl = system.comp["CTRL_KN"]

    return {
        "time": system.currentTime(),
        "level_a": cmp_ctrl.controls_in["tank_a"].get_reading(),
        "alarm": cmp_ctrl.controls_out["alarm"].get_signal(),
        "idle": cmp_ctrl.controls_out["idle"].get_signal(),
        "echo": cmp_ctrl.controls_out["echo"].get_level(),
        "observed_echo": system.comp["CTRL_OBS"].controls_in["echo"].get_reading(),
        "level_b": band_ctrl.controls_in["tank_b"].get_reading(),
        "fill": band_ctrl.controls_out["fill"].get_signal(),
        "levels_c": [
            kn_ctrl.controls_in[f"tank_c{index}"].get_reading() for index in (1, 2, 3)
        ],
        "trip": kn_ctrl.controls_out["trip"].get_signal(),
    }


def drive(system, horizon, limit=80):
    """Step the session to ``horizon``, recording what every stop saw."""
    trace = []

    for _ in range(limit):
        trace.append(snapshot(system))

        if system.currentTime() >= horizon:
            return trace

        system.isimu_step_forward()

    raise AssertionError(f"the session did not reach {horizon} in {limit} steps")


def refuse(observations, key, build):
    """Record what ``build`` raised, or that it raised nothing."""
    try:
        build()
        observations[key] = None
    except Exception as err:  # noqa: BLE001 -- the refusal IS the observation
        observations[key] = err


@pytest.fixture(scope="module")
def obs():
    """Build the four montages, drive one session, record what it produced."""
    observations = {}
    records, restore = record_sensitive_methods()

    try:
        system = muscadet.System(name="ObjCtrlGrammar001")

        # -- Montage A: a reading rising through a threshold, and the same
        # -- reading republished with a gain onto a second controller.
        system.add_component(
            name="SRC_A", cls="SourceContinuous", flow="q", rate=UNIT_RATE
        )
        system.add_component(
            name="CAP_A",
            cls="CapacityContinuous",
            flow="q",
            capacity=1000.0,
            capacity_name="tank_a",
            content_init={"q": 0.0},
            fill_rate=float("inf"),
        )
        system.connect_flow(source="SRC_A", target="CAP_A", flow_name="q")

        system.add_component(
            name="CTRL_CMP",
            cls="ObjCtrl",
            controls_in=[{"name": "tank_a"}],
            controls_out=[
                {
                    "name": "alarm",
                    "kind": "bool",
                    "emit": {
                        "op": "compare",
                        "input": "tank_a",
                        "operator": ">=",
                        "threshold": ALARM_THRESHOLD,
                    },
                },
                {
                    "name": "idle",
                    "kind": "bool",
                    # The one combination whose operand count is fixed, and the
                    # cheapest way to start an output ON: the tank is empty at
                    # t = 0, so the negation holds before anything has moved.
                    "emit": {
                        "op": "combine",
                        "logic": "not",
                        "operands": [
                            {
                                "op": "compare",
                                "input": "tank_a",
                                "operator": ">=",
                                "threshold": IDLE_THRESHOLD,
                            }
                        ],
                    },
                },
                {
                    "name": "echo",
                    "kind": "value",
                    "emit": {
                        "op": "republish",
                        "input": "tank_a",
                        "gain": ECHO_GAIN,
                    },
                },
            ],
        )
        system.connect("CAP_A", "tank_a_level_out", "CTRL_CMP", "tank_a_level_in")

        system.add_component(
            name="CTRL_OBS", cls="ObjCtrl", controls_in=[{"name": "echo"}]
        )
        system.connect("CTRL_CMP", "echo_level_out", "CTRL_OBS", "echo_level_in")

        # -- Montage B: the hysteresis band, closing a real loop. The tank
        # -- drains at 1 and is refilled at 2 while the band holds its source
        # -- open, so the level turns round at the activation edge.
        system.add_component(
            name="SRC_B",
            cls="SourceContinuous",
            flow="q",
            rate=BAND_FILL_RATE,
            control="fill",
        )
        system.add_component(
            name="CAP_B",
            cls="CapacityContinuous",
            flow="q",
            capacity=100.0,
            capacity_name="tank_b",
            content_init={"q": BAND_INIT},
            fill_rate=float("inf"),
        )
        system.add_component(
            name="SINK_B", cls="ConsumerContinuous", flow="q", demand=UNIT_RATE
        )
        system.connect_flow(source="SRC_B", target="CAP_B", flow_name="q")
        system.connect_flow(source="CAP_B", target="SINK_B", flow_name="q")

        system.add_component(
            name="CTRL_BAND",
            cls="ObjCtrl",
            controls_in=[{"name": "tank_b"}],
            controls_out=[
                {
                    "name": "fill",
                    "kind": "bool",
                    "emit": {
                        "op": "band",
                        "input": "tank_b",
                        "direction": "below",
                        "activate": BAND_ACTIVATE,
                        "release": BAND_RELEASE,
                    },
                }
            ],
        )
        system.connect("CAP_B", "tank_b_level_out", "CTRL_BAND", "tank_b_level_in")
        system.connect("CTRL_BAND", "fill_out", "SRC_B", "fill_in")

        # -- Montage C: three readings crossing three thresholds at three
        # -- dates, two of them enough to trip.
        for index, threshold in enumerate(KN_THRESHOLDS, start=1):
            system.add_component(
                name=f"SRC_C{index}", cls="SourceContinuous", flow="q", rate=UNIT_RATE
            )
            system.add_component(
                name=f"CAP_C{index}",
                cls="CapacityContinuous",
                flow="q",
                capacity=1000.0,
                capacity_name=f"tank_c{index}",
                content_init={"q": 0.0},
                fill_rate=float("inf"),
            )
            system.connect_flow(
                source=f"SRC_C{index}", target=f"CAP_C{index}", flow_name="q"
            )

        system.add_component(
            name="CTRL_KN",
            cls="ObjCtrl",
            controls_in=[{"name": f"tank_c{index}"} for index in (1, 2, 3)],
            controls_out=[
                {
                    "name": "trip",
                    "kind": "bool",
                    "emit": {
                        "op": "combine",
                        "logic": "k",
                        "k": KN_K,
                        "operands": [
                            {
                                "op": "compare",
                                "input": f"tank_c{index}",
                                "operator": ">=",
                                "threshold": threshold,
                            }
                            for index, threshold in enumerate(KN_THRESHOLDS, start=1)
                        ],
                    },
                }
            ],
        )
        for index in (1, 2, 3):
            system.connect(
                f"CAP_C{index}",
                f"tank_c{index}_level_out",
                "CTRL_KN",
                f"tank_c{index}_level_in",
            )

        # -- What the grammar refuses, all at declaration.
        refuse(
            observations,
            "err_band_order",
            lambda: system.add_component(
                name="CTRL_BAD_BAND",
                cls="ObjCtrl",
                controls_in=[{"name": "tank"}],
                controls_out=[
                    {
                        "name": "fill",
                        "kind": "bool",
                        # Detecting BELOW 3 and releasing at 1 is a band that
                        # can never release: the release edge has to sit on the
                        # far side of the activation edge.
                        "emit": {
                            "op": "band",
                            "input": "tank",
                            "direction": "below",
                            "activate": 3.0,
                            "release": 1.0,
                        },
                    }
                ],
            ),
        )

        refuse(
            observations,
            "err_callable",
            lambda: system.add_component(
                name="CTRL_BAD_FUN",
                cls="ObjCtrl",
                controls_in=[{"name": "tank"}],
                controls_out=[
                    {
                        "name": "fill",
                        "kind": "bool",
                        "emit": lambda reading: reading > 3,
                    }
                ],
            ),
        )

        refuse(
            observations,
            "err_attested_callable",
            lambda: system.add_component(
                name="CTRL_BAD_PROFILE",
                cls="ObjCtrl",
                controls_in=[{"name": "tank"}],
                controls_out=[
                    {
                        "name": "fill",
                        "kind": "bool",
                        # The continuity attestation muscadet accepts on a
                        # production profile. It buys nothing here: a
                        # continuous function is still a function, and the
                        # solver dates a crossing on a FORM it recognises.
                        "emit": muscadet.Profile(lambda time: time, continuous=True),
                    }
                ],
            ),
        )

        refuse(
            observations,
            "err_unknown_input",
            lambda: system.add_component(
                name="CTRL_BAD_INPUT",
                cls="ObjCtrl",
                controls_in=[{"name": "tank"}],
                controls_out=[
                    {
                        "name": "fill",
                        "kind": "bool",
                        "emit": {
                            "op": "compare",
                            "input": "tenk",
                            "operator": ">=",
                            "threshold": 1.0,
                        },
                    }
                ],
            ),
        )

        refuse(
            observations,
            "err_nature",
            lambda: system.add_component(
                name="CTRL_BAD_NATURE",
                cls="ObjCtrl",
                controls_in=[{"name": "tank"}],
                controls_out=[
                    {
                        "name": "fill",
                        "kind": "bool",
                        # A republication is a NUMBER; a boolean output cannot
                        # carry one.
                        "emit": {"op": "republish", "input": "tank"},
                    }
                ],
            ),
        )

        refuse(
            observations,
            "err_unknown_op",
            lambda: system.add_component(
                name="CTRL_BAD_OP",
                cls="ObjCtrl",
                controls_in=[{"name": "tank"}],
                controls_out=[
                    {
                        "name": "fill",
                        "kind": "bool",
                        "emit": {"op": "integrate", "input": "tank"},
                    }
                ],
            ),
        )

        for date in CLOCKS:
            add_clock(system.comp["SINK_B"], date)

        system.isimu_start()
        observations["trace"] = drive(system, HORIZON)
        system.isimu_stop()

        observations["system"] = system
        observations["sensitive"] = records

        yield observations
    finally:
        restore()


def stop_at(trace, date, tol=CROSSING_TOL):
    """The SETTLED stop at ``date``, or ``(None, None)``.

    The session stops twice at an event date: once because the integration was
    told to stop there -- that is the watched transition doing its work -- and
    once more, at the same date, when the instantaneous transition it enabled
    actually fires. What an output carries is read after the second, so this
    answers the LAST entry of the date and not the first.
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


def dates(trace):
    """The dates the session stopped at, rounded for a readable message."""
    return [round(entry["time"], 4) for entry in trace]


# 1. A comparison is dated exactly
# ================================


def test_a_comparison_switches_at_the_crossing_and_not_at_the_next_stop(obs):
    """The threshold compiles to a WATCHED automaton, so the run stops on it.

    Nothing else stops the solver between the clocks at t = 3 and t = 8, so a
    threshold compiled to anything the integration manager does not watch is
    first noticed three time units late -- silently, and by an amount that
    depends on the step size. That is what this assertion fails on.
    """
    trace = obs["trace"]

    index, entry = stop_at(trace, ALARM_THRESHOLD)
    assert entry is not None, (
        "no stop at the threshold crossing; the session walked " f"{dates(trace)}"
    )
    assert entry["level_a"] == pytest.approx(ALARM_THRESHOLD, abs=CROSSING_TOL)
    assert entry["alarm"] is True, "the alarm must be on AT the crossing"

    previous = before(trace, ALARM_THRESHOLD)
    assert previous["alarm"] is False, "and off at every stop before it"

    # The stop belongs to this threshold and to nothing else. No clock is set
    # there, and no other montage of this module crosses anything there, so a
    # threshold compiled to something unwatched produces no stop at all at that
    # date -- and the assertion above is the one that says so.
    assert ALARM_THRESHOLD not in CLOCKS
    assert ALARM_THRESHOLD not in KN_THRESHOLDS
    assert ALARM_THRESHOLD not in (BAND_ACTIVATE_DATE, BAND_RELEASE_DATE)

    assert trace[index + 1]["time"] > ALARM_THRESHOLD + CROSSING_TOL


def test_the_alarm_stays_on_once_the_reading_has_passed_the_threshold(obs):
    """A comparison is not a pulse: it holds while its condition holds."""
    after = [
        entry
        for entry in obs["trace"]
        if entry["level_a"] > ALARM_THRESHOLD + CROSSING_TOL
    ]

    assert after, "the montage must run past the threshold"
    assert all(entry["alarm"] is True for entry in after)


# 2. A band holds inside its two edges
# ====================================


def test_a_band_activates_at_its_activation_edge(obs):
    """Detecting BELOW 3, on a tank draining from 10 at one unit per unit time."""
    trace = obs["trace"]

    _, entry = stop_at(trace, BAND_ACTIVATE_DATE)
    assert entry is not None, f"no stop at the activation edge; walked {dates(trace)}"
    assert entry["level_b"] == pytest.approx(BAND_ACTIVATE, abs=CROSSING_TOL)
    assert entry["fill"] is True

    assert (
        before(trace, BAND_ACTIVATE_DATE)["fill"] is False
    ), "the band starts released"


def test_a_band_does_not_release_between_its_two_edges(obs):
    """The whole of what a band buys over a comparison.

    Between the two edges the reading is back ABOVE the activation level, so a
    plain comparison against 3 would have released already -- and the montage
    would chatter around that single level instead of filling the tank.
    """
    inside = [
        entry
        for entry in obs["trace"]
        if BAND_ACTIVATE + CROSSING_TOL < entry["level_b"] < BAND_RELEASE - CROSSING_TOL
        and BAND_ACTIVATE_DATE < entry["time"] < BAND_RELEASE_DATE
    ]

    assert len(inside) >= 2, (
        "the montage must be read at least twice strictly inside the band; "
        f"it walked {dates(obs['trace'])}"
    )
    assert all(entry["fill"] is True for entry in inside)


def test_a_band_releases_at_its_release_edge(obs):
    """And it is dated exactly too: the release edge is watched like the other."""
    trace = obs["trace"]

    _, entry = stop_at(trace, BAND_RELEASE_DATE)
    assert entry is not None, f"no stop at the release edge; walked {dates(trace)}"
    assert entry["level_b"] == pytest.approx(BAND_RELEASE, abs=CROSSING_TOL)
    assert before(trace, BAND_RELEASE_DATE)["fill"] is True
    assert entry["fill"] is False


def test_a_band_declared_the_wrong_way_round_is_refused(obs):
    """A release edge on the wrong side of the activation edge never releases."""
    error = obs["err_band_order"]

    assert error is not None, "the inverted band must be refused"

    message = str(error)
    assert "band" in message
    assert "below" in message
    assert "3.0" in message and "1.0" in message


# 3. k-of-n counts, and counts right
# ==================================


def test_k_of_n_trips_when_the_kth_reading_crosses_and_not_before(obs):
    """Two of three, on three readings crossing at 2, 4 and 6."""
    trace = obs["trace"]

    first, second, third = KN_THRESHOLDS

    at_first = stop_at(trace, first)[1]
    assert at_first is not None, f"no stop at {first}; walked {dates(trace)}"
    assert at_first["trip"] is False, "one of three is not two of three"

    _, at_second = stop_at(trace, second)
    assert at_second is not None, f"no stop at {second}; walked {dates(trace)}"
    assert at_second["trip"] is True, "the second crossing is what trips it"
    assert before(trace, second)["trip"] is False

    at_third = stop_at(trace, third)[1]
    assert at_third is not None
    assert at_third["trip"] is True, "and a third one does not un-trip it"


# 4. A republication carries a reading and its gain
# =================================================


def test_a_republished_value_is_the_reading_times_the_gain(obs):
    """Refreshed at every integration step, like every published measurement."""
    read = [entry for entry in obs["trace"] if entry["level_a"] > 0.0]

    assert read, "the republishing montage must have something to read"
    for entry in read:
        assert entry["echo"] == pytest.approx(ECHO_GAIN * entry["level_a"], rel=1e-6)


def test_a_republished_value_is_read_back_by_the_next_controller(obs):
    """One controller's value output IS another's observation input (R4)."""
    for entry in obs["trace"]:
        assert entry["observed_echo"] == pytest.approx(entry["echo"], rel=1e-9)


def test_the_gain_is_the_variable_a_failure_mode_will_clamp(obs):
    """The gain the grammar declares lands in ``{name}_level_gain`` and nowhere else.

    The next unit makes a mode brood over it; what this one owes that unit is
    that there be exactly ONE number to reach, already a variable of the model.
    """
    echo = obs["system"].comp["CTRL_CMP"].controls_out["echo"]

    assert echo.var_gain.basename() == "echo_level_gain"
    assert echo.get_gain() == pytest.approx(ECHO_GAIN)


# 5. No continuous quantity is read by a sensitive method
# =======================================================


def test_no_sensitive_method_is_registered_on_a_controller_variable(obs):
    """The inventory, taken from the engine and not argued from the code.

    A sensitive method fires on an ANNOUNCED change. A level moving inside an
    integration step announces none, so a controller reading one that way would
    never re-evaluate -- and nothing would say so. Every re-evaluation a
    controller declares is therefore hung on a watched automaton, which is a
    discrete object and does announce its state.
    """
    on_controllers = [
        record for record in obs["sensitive"] if record["owner"] in CONTROLLERS
    ]

    assert on_controllers, "the controllers must register something at all"

    offenders = [record for record in on_controllers if record["class"] != "IAutomaton"]
    assert offenders == [], (
        "a controller registered a sensitive method on something other than an "
        f"automaton: {offenders}"
    )


def test_no_sensitive_method_anywhere_reads_a_double(obs):
    """The same claim, over the WHOLE montage: not one continuous variable."""
    offenders = [
        record
        for record in obs["sensitive"]
        if record["value_type"] is not None and "double" in record["value_type"]
    ]

    assert offenders == [], f"sensitive methods on continuous variables: {offenders}"


def test_every_operator_of_the_grammar_compiles_to_watched_automata(obs):
    """What each output declared to the solver, by name."""
    system = obs["system"]

    assert [aut.name for aut in system.comp["CTRL_CMP"].emit_automata["alarm"]] == [
        "CTRL_CMP_alarm_compare"
    ]
    assert [aut.name for aut in system.comp["CTRL_BAND"].emit_automata["fill"]] == [
        "CTRL_BAND_fill_band"
    ]
    assert [aut.name for aut in system.comp["CTRL_KN"].emit_automata["trip"]] == [
        "CTRL_KN_trip_operand_0_compare",
        "CTRL_KN_trip_operand_1_compare",
        "CTRL_KN_trip_operand_2_compare",
    ]
    # A republication is an EQUATION, not an automaton: it carries a number,
    # and a number has no crossing to date.
    assert system.comp["CTRL_CMP"].emit_automata["echo"] == []


# 6. A Python function is not a declaration
# =========================================


def test_a_python_function_is_refused_as_an_output_value(obs):
    """The interdict this unit exists to close."""
    error = obs["err_callable"]

    assert error is not None, "a callable must be refused"

    message = str(error)
    assert "callable" in message
    for operator in muscadet.CTRL_OPERATORS:
        assert operator in message


def test_a_callable_attested_continuous_is_refused_too(obs):
    """The attestation buys a production profile its place; it buys nothing here.

    A continuous function is still a function: nothing can read a threshold out
    of it, so nothing can compile it to something the integration manager
    watches.
    """
    error = obs["err_attested_callable"]

    assert error is not None, "an attested-continuous callable must be refused too"
    assert "callable" in str(error)


def test_an_unknown_operator_is_refused_naming_the_closed_list(obs):
    error = obs["err_unknown_op"]

    assert error is not None
    assert "integrate" in str(error)
    for operator in muscadet.CTRL_OPERATORS:
        assert operator in str(error)


def test_an_output_reading_an_undeclared_input_is_refused(obs):
    """Named, and told what the controller does declare."""
    error = obs["err_unknown_input"]

    assert error is not None
    assert "'tenk'" in str(error)
    assert "tank" in str(error)


def test_an_output_carrying_the_wrong_nature_is_refused(obs):
    """A boolean output cannot carry a number, nor a value output a condition."""
    error = obs["err_nature"]

    assert error is not None
    assert "republish" in str(error)
    assert "bool" in str(error)


def test_the_operator_vocabulary_partitions_by_the_nature_it_carries():
    """Every operator answers a boolean or a number, and never both."""
    boolean = set(muscadet.CTRL_BOOL_OPERATORS)
    numeric = set(muscadet.CTRL_VALUE_OPERATORS)

    assert boolean | numeric == set(muscadet.CTRL_OPERATORS)
    assert boolean & numeric == set()


def test_an_output_already_satisfied_at_t0_starts_on(obs):
    """The start seed, and why a signal needs one.

    A signal variable is NOT reinitialised between steps -- it is a state, not
    a pulse -- so nothing but a start method gives it the value its condition
    already has at t = 0. Without one, this montage would sit idle until the
    reading came back down and crossed the threshold from the other side, which
    on a monotonically rising tank is never.
    """
    trace = obs["trace"]

    assert trace[0]["time"] == 0.0
    assert trace[0]["level_a"] < IDLE_THRESHOLD
    assert trace[0]["idle"] is True, "the negation holds before anything moves"


def test_a_negation_switches_at_its_operand_crossing(obs):
    """A 'not' is dated by the automaton of the comparison underneath it."""
    trace = obs["trace"]

    _, entry = stop_at(trace, IDLE_THRESHOLD)
    assert entry is not None, f"no stop at {IDLE_THRESHOLD}; walked {dates(trace)}"
    assert entry["level_a"] == pytest.approx(IDLE_THRESHOLD, abs=CROSSING_TOL)
    assert entry["idle"] is False
    assert before(trace, IDLE_THRESHOLD)["idle"] is True


@pytest.mark.parametrize(
    "logic, k, flags, expected",
    [
        ("and", None, (True, True, True), True),
        ("and", None, (True, False, True), False),
        ("or", None, (False, False, False), False),
        ("or", None, (False, True, False), True),
        ("not", None, (False,), True),
        ("not", None, (True,), False),
        ("k", 2, (True, False, False), False),
        ("k", 2, (True, True, False), True),
        ("k", 3, (True, True, True), True),
    ],
)
def test_every_combination_reduces_its_operands(logic, k, flags, expected):
    """The four logics, over operands whose values are given rather than read.

    Read at the node level because the montage can only exercise one shape at a
    time, and the shapes are what the grammar promises.
    """
    node = muscadet.build_ctrl_node(
        "combination",
        {
            "op": "combine",
            "logic": logic,
            **({} if k is None else {"k": k}),
            "operands": [
                {"op": "compare", "input": "x", "operator": ">=", "threshold": 0.0}
                for _ in flags
            ],
        },
    )
    reader = muscadet.obj_ctrl.combine_reader(
        node, [(lambda value=flag: value) for flag in flags]
    )

    assert bool(reader()) is expected


@pytest.mark.parametrize(
    "spec, operand_count, expected",
    [
        ({"logic": "or"}, 0, "no operand"),
        ({"logic": "not"}, 2, "ONE operand"),
        ({"logic": "or", "k": 2}, 2, "no meaning"),
        ({"logic": "k"}, 2, "at least one operand"),
        ({"logic": "k", "k": 3}, 2, "never hold"),
        ({"logic": "nand"}, 1, "and, or, not, k"),
    ],
)
def test_a_combination_that_could_only_be_a_constant_is_refused(
    spec, operand_count, expected
):
    """A vacuous or unsatisfiable combination is an output that says nothing.

    ``any([])`` is False and ``all([])`` is True, so an empty combination
    compiles perfectly and answers a constant -- which is a controller that
    does not control, with nothing raised anywhere to say so.
    """
    operand = {"op": "compare", "input": "x", "operator": ">=", "threshold": 0.0}
    spec = dict(spec, op="combine")
    spec["operands"] = [dict(operand) for _ in range(operand_count)]

    with pytest.raises(ValueError) as error:
        muscadet.build_ctrl_node("combination", spec)

    assert expected in str(error.value)


def test_a_republication_may_not_spell_its_gain_twice(obs):
    """One number, one spelling: the two would silently multiply."""
    controller = obs["system"].comp["CTRL_OBS"]

    with pytest.raises(ValueError) as error:
        controller.add_control_out(
            name="twice",
            kind="value",
            gain_default=3.0,
            emit={"op": "republish", "input": "echo", "gain": 2.0},
        )

    assert "gain_default" in str(error.value)
    assert "twice_level_gain" in str(error.value)

    # Refused BEFORE anything was built: the controller is the one it was.
    assert "twice" not in controller.controls_out


def test_delete(obs):
    obs["system"].deleteSys()
    cod3s.terminate_session()
