"""A rank-sensitive aggregation declares its kinks to the solver (R41).

``min``, ``max`` and ``median`` are continuous in their readings and NOT
differentiable in them: they change argument -- or representative -- the moment
two readings cross. The PDMP solver knows nothing of that on its own, so it
integrates straight THROUGH the kink and only sees the new representative at
the next watched event, exactly as it would step over a discontinuity. Nothing
raises: the model simply overshoots by one step, by an amount that depends on
the step size. Measured on the montage below, before the unit existed, the run
walked ``[0.0, 0.000644, 0.000644, 8.0]`` and the minimum was first seen to
have changed representative **three time units late**.

What is declared, and what is not
---------------------------------
A crossing is declared the way every other crossing in this library is: a
two-state automaton with two INSTANTANEOUS transitions, both registered as
watched, one per PAIR of sources. ``sum`` and ``mean`` are linear in their
readings and produce none.

The library already carries kinks it does not declare -- the minimums its own
allocation and reactant limitations take. This unit declares the ones the
controller introduces; it does not go back over those.

Integration cost, and where the cap comes from
----------------------------------------------
Measured 2026-09-01 on ``hypatie`` (AMD Ryzen AI 9 HX 370, 24 threads, Python
3.10.16, muscadet 3.1.0). The montage, written out here rather than left in a
throwaway script, so the figures can be reproduced: N pairs of
``SourceContinuous`` / ``CapacityContinuous``, source ``k`` at rate ``k``, tank
``k`` starting at ``400 - S k**3`` with ``fill_rate=inf`` and a capacity of
1e5, every tank publishing on the channel name ``reading`` and wired straight
onto ONE controller input that reduces them all. With ``S = 20 / (3 N**2 + 1)``
the pair ``(i, j)`` crosses at ``S (i*i + i*j + j*j)``, so **every pair crosses
at a date of its own** inside the horizon -- the worst case a cap has to be
dimensioned against, since each crossing is then a stop of its own. Five Monte
Carlo sequences to t = 20, timed around ``simulate()`` with ``prerun()`` called
and timed separately before it. The same montage aggregated by ``mean`` is the
control, so the difference between the two columns is the crossings and
nothing else. The cap was lifted on the class for the two largest points.

======= ========= ============ =========== ========== ============
sources crossings mean (no     min (with   overhead   overhead, in
                  crossings)   crossings)             % of control
======= ========= ============ =========== ========== ============
      3         3      2.50 s      2.58 s     0.08 s        +   3 %
      7        21      5.79 s      5.83 s     0.04 s        +   1 %
     16       120     12.46 s     16.13 s     3.67 s        +  29 %
     20       190     14.79 s     22.75 s     7.96 s        +  54 %
     30       435     22.07 s     49.09 s    27.02 s        + 122 %
======= ========= ============ =========== ========== ============

The cost is **superlinear in the crossing count**: from 120 to 190 crossings
the overhead grows by 2.2 for a count that grows by 1.6, and from 190 to 435 by
3.4 for a count that grows by 2.3 -- an exponent between 1.5 and 1.7. Since the
count itself is quadratic in the sources, the run time of the kinks grows as
roughly the third power of the source count. A single point could not have
shown that, and would have made a cap a guess.

:data:`muscadet.AGGREGATION_CROSSING_CAP` is set at **120 crossings, i.e. 16
sources**, the last measured point where the crossings stay under a quarter of
the run time -- 3.67 s of 16.13 s, against 7.96 s of 22.75 s one point later. The reference case of seven redundant instruments is 21
crossings and costs 1 %, so the ceiling leaves it a factor of six of headroom
on the count; what it refuses is a model far past any voting architecture. It
is an early refusal and not the operational guard: the count depends on what a
model WIRES, so the ceiling that matters is applied by whoever emits the model.
"""

import math

import cod3s
import muscadet
import pytest

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

#: The draining tank starts here and gives up one unit per unit time.
FALLING_INIT = 10.0

#: The filling tank starts empty and takes one unit per unit time.
RISING_RATE = 1.0

#: Where the two readings meet: ``10 - t == t``.
CROSSING_DATE = 0.5 * FALLING_INIT

#: What both readings are worth there.
CROSSING_LEVEL = 0.5 * FALLING_INIT

#: The solver root-finds the crossing rather than landing on it exactly.
#: Measured at 2.5e-4 on this montage; the margin below is forty times that and
#: still three time units short of the next watched stop, which is what the
#: assertion has to tell apart.
CROSSING_TOL = 0.01

#: A watched stop well past the crossing. Nothing else stops the solver in
#: between, so an undeclared kink is seen here and only here.
HORIZON = 8.0

#: The publishers of the counting montages, and how many each controller reads.
PUBLISHERS = ("PUB_A", "PUB_B", "PUB_C", "PUB_D")

#: The cap the tight controller below declares, in crossings. Three is what
#: three sources cost, so the third source is accepted and the fourth -- six
#: crossings -- is not.
TIGHT_CAP = 3


class ObjCtrlKinksTightCap(muscadet.ObjCtrl):
    """A controller refusing above :data:`TIGHT_CAP` crossings.

    The cap is read as ``self.CROSSING_CAP`` at every check precisely so that
    it can be lowered like this: asserting the refusal on the shipped value
    would mean wiring seventeen publishers to watch a message appear.
    """

    CROSSING_CAP = TIGHT_CAP


def drive(system, horizon, limit=60):
    """Step the session to ``horizon``, recording what every stop saw."""
    channel = system.comp["CROSS"].controls_in["reading"]
    trace = []

    for _ in range(limit):
        readings = channel.readings(channel.var_level, 0.0)
        trace.append(
            {
                "time": system.currentTime(),
                "readings": list(readings),
                "aggregated": channel.get_reading(),
                # Which source the minimum currently designates. THE
                # observable of a kink: the value is continuous across it, the
                # representative is not.
                "representative": min(range(len(readings)), key=readings.__getitem__),
            }
        )

        if system.currentTime() >= horizon:
            return trace

        system.isimu_step_forward()

    raise AssertionError(f"the session did not reach {horizon} in {limit} steps")


@pytest.fixture(scope="module")
def obs():
    """One system, one session, one recorded trace."""
    observations = {}

    system = muscadet.System(name="ObjCtrlKinks001")

    # -- A falling reading: a stocked tank drained at a constant rate.
    system.add_component(
        name="FALLING",
        cls="CapacityContinuous",
        flow="q",
        capacity=1000.0,
        capacity_name="reading",
        content_init={"q": FALLING_INIT},
    )
    system.add_component(name="SINK", cls="ConsumerContinuous", flow="q", demand=1.0)
    system.connect_flow(source="FALLING", target="SINK", flow_name="q")

    # -- A rising reading: an empty tank filled at the same rate.
    system.add_component(name="SRC", cls="SourceContinuous", flow="q", rate=RISING_RATE)
    system.add_component(
        name="RISING",
        cls="CapacityContinuous",
        flow="q",
        capacity=1000.0,
        capacity_name="reading",
        content_init={"q": 0.0},
        fill_rate=math.inf,
    )
    system.connect_flow(source="SRC", target="RISING", flow_name="q")

    # -- One controller input reading both, reduced by a MINIMUM: the reduced
    # -- value is continuous through the crossing, its argument is not.
    system.add_component(
        name="CROSS",
        cls="ObjCtrl",
        controls_in=[{"name": "reading", "aggregate": "min"}],
    )
    system.connect("FALLING", "reading_level_out", "CROSS", "reading_level_in")
    system.connect("RISING", "reading_level_out", "CROSS", "reading_level_in")

    # -- The counting montages. Their publishers are controllers publishing a
    # -- value, which is the cheapest publisher there is: what is counted here
    # -- is automata, not quantities, and none of these readings ever moves.
    for publisher in PUBLISHERS + ("PUB_E",):
        system.add_component(
            name=publisher,
            cls="ObjCtrl",
            controls_out=[{"name": "reading", "kind": "value"}],
        )

    for name, cls, aggregate, wired in (
        ("SMOOTH", "ObjCtrl", "mean", PUBLISHERS),
        ("PAIRS", "ObjCtrl", "max", PUBLISHERS),
        ("TIGHT", "ObjCtrlKinksTightCap", "median", PUBLISHERS[:TIGHT_CAP]),
    ):
        system.add_component(
            name=name,
            cls=cls,
            controls_in=[{"name": "reading", "aggregate": aggregate}],
        )
        for publisher in wired:
            system.connect(publisher, "reading_level_out", name, "reading_level_in")

    # The connection that takes the tight controller past its cap: three
    # sources are three crossings, a fourth would be six.
    try:
        system.connect("PUB_D", "reading_level_out", "TIGHT", "reading_level_in")
        observations["err_cap"] = None
    except Exception as err:  # noqa: BLE001 -- the refusal is the observation
        observations["err_cap"] = err

    observations["tight_sources"] = (
        system.comp["TIGHT"].controls_in["reading"].var_level.cnctCount()
    )

    # The one other watched stop of the run, three time units past the
    # crossing: what an undeclared kink would be noticed at.
    system.comp["SINK"].add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": HORIZON},
        cond_occ_21=False,
    )

    system.isimu_start()
    observations["trace"] = drive(system, HORIZON)
    system.isimu_stop()

    observations["automata"] = {
        name: list(system.comp[name].crossing_automata.get("reading", []))
        for name in ("CROSS", "SMOOTH", "PAIRS", "TIGHT")
    }

    # A publisher wired AFTER the pre-run step built the automata. Its
    # crossings would be the only ones the solver never stopped at, so the
    # next entry point refuses the model instead of running it short.
    system.connect("PUB_E", "reading_level_out", "PAIRS", "reading_level_in")
    try:
        system.prerun()
        observations["err_late"] = None
    except Exception as err:  # noqa: BLE001 -- the refusal is the observation
        observations["err_late"] = err

    observations["system"] = system

    return observations


def stop_at(trace, date, tol=CROSSING_TOL):
    """The recorded stop at ``date``, or ``(None, None)``."""
    for index, entry in enumerate(trace):
        if entry["time"] == pytest.approx(date, abs=tol):
            return index, entry
    return None, None


# The crossing is dated
# =====================


def test_the_session_stops_at_the_crossing_and_not_at_the_next_event(obs):
    """The kink is a watched event, so the integration stops ON it.

    Undeclared, the run walks from t = 0 straight to the horizon and the
    minimum is first seen to have changed representative three time units
    late -- silently, and by an amount that depends on the step size.
    """
    trace = obs["trace"]

    index, entry = stop_at(trace, CROSSING_DATE)
    assert entry is not None, (
        "no stop at the crossing date; the run walked "
        f"{[round(row['time'], 6) for row in trace]}"
    )
    assert entry["readings"] == pytest.approx(
        [CROSSING_LEVEL, CROSSING_LEVEL], abs=CROSSING_TOL
    )
    assert index < len(trace) - 1, "the crossing must not be the horizon itself"


def test_the_minimum_changes_representative_at_that_date(obs):
    """Before the crossing the rising tank is the minimum, after it the falling one."""
    trace = obs["trace"]

    index, entry = stop_at(trace, CROSSING_DATE)
    assert entry is not None

    before = trace[index - 1]
    assert before["time"] < CROSSING_DATE - CROSSING_TOL
    assert before["representative"] == 1, "the rising tank is the smaller one first"
    assert trace[-1]["representative"] == 0, "the falling tank is the smaller one after"

    # The value itself is continuous across the kink: the two readings meet.
    assert entry["aggregated"] == pytest.approx(CROSSING_LEVEL, abs=CROSSING_TOL)


def test_the_crossing_compiles_to_one_instantaneous_two_state_automaton(obs):
    """What a pair of sources adds to the controller, and nothing more."""
    automata = obs["automata"]["CROSS"]

    assert len(automata) == 1

    aut = automata[0]
    assert aut.name == "CROSS_reading_cross_0_1"
    assert [state.name for state in aut.states] == [
        "reading_cross_0_1_le",
        "reading_cross_0_1_gt",
    ]
    # Instantaneous, so the rank settles AT the crossing and not after it.
    assert [transition.occ_law.time for transition in aut.transitions] == [0, 0]

    # Held on the component like every other automaton it carries.
    assert obs["system"].comp["CROSS"].automata_d[aut.name] is aut


# What produces a kink, and what does not
# =======================================


def test_the_kink_vocabulary_partitions_the_closed_aggregation_list():
    """Every aggregation is on exactly one side, and the two sides are disjoint.

    A policy added to :data:`muscadet.COMBINE_POLICIES` and to neither tuple
    would silently declare no crossing -- the failure this unit exists to
    remove -- so the gate is put on the LIST of policies rather than on either
    tuple.
    """
    kinked = set(muscadet.AGGREGATION_KINK_POLICIES)
    smooth = set(muscadet.AGGREGATION_SMOOTH_POLICIES)

    assert kinked | smooth == set(muscadet.COMBINE_POLICIES)
    assert kinked & smooth == set()


def test_a_mean_registers_no_crossing_automaton_at_all(obs):
    """A sum and a mean are linear in their readings: no argument to change."""
    assert obs["automata"]["SMOOTH"] == []

    # Reached and judged, not merely skipped: the input holds an empty list,
    # which is what tells "no kink" apart from "never looked at".
    assert "reading" in obs["system"].comp["SMOOTH"].crossing_automata
    assert obs["system"].comp["SMOOTH"].crossing_sources["reading"] == len(PUBLISHERS)


def test_the_automata_of_n_sources_are_the_pairs_of_n_sources(obs):
    """Four sources, six pairs, six automata -- and named by the pair."""
    automata = obs["automata"]["PAIRS"]

    assert len(automata) == muscadet.crossing_count(len(PUBLISHERS)) == 6
    assert [aut.name for aut in automata] == [
        f"PAIRS_reading_cross_{first}_{second}"
        for first, second in muscadet.crossing_pairs(len(PUBLISHERS))
    ]


@pytest.mark.parametrize(
    "sources, expected",
    [(0, 0), (1, 0), (2, 1), (3, 3), (7, 21), (16, 120), (30, 435)],
)
def test_the_pair_count_is_n_times_n_minus_one_over_two(sources, expected):
    """Including the two counts that produce nothing: nobody to cross with."""
    assert muscadet.crossing_count(sources) == expected
    assert len(muscadet.crossing_pairs(sources)) == expected


# What a controller refuses
# =========================


def test_a_source_count_above_the_cap_is_refused_naming_the_cap(obs):
    """The connection that breaks the ceiling is the one that is refused."""
    error = obs["err_cap"]

    assert error is not None, "the fourth source must be refused"

    message = str(error)
    assert f"cap of {TIGHT_CAP}" in message
    assert "6 pair crossings" in message
    assert "4 sources" in message
    assert "median" in message

    # Refused BEFORE the connection is made: the wiring is the one it had.
    assert obs["tight_sources"] == TIGHT_CAP


def test_the_cap_refusal_is_one_message_whichever_route_reaches_it(obs):
    """The early refusal and the guard share :meth:`ObjCtrl.check_crossing_cap`.

    The guard runs at the pre-run step, where every connection exists whatever
    route it took; the early one runs at the connection and can name it. Two
    call sites, one refusal, so a model cannot meet two vocabularies for one
    mistake.
    """
    controller = obs["system"].comp["TIGHT"]

    with pytest.raises(ValueError) as error:
        controller.check_crossing_cap("reading", TIGHT_CAP + 1)

    assert f"cap of {TIGHT_CAP}" in str(error.value)

    # At the cap, not above it: the boundary is inclusive.
    controller.check_crossing_cap("reading", TIGHT_CAP)


def test_a_source_wired_after_the_prerun_step_is_refused(obs):
    """The automata are built once; a later publisher would never stop the solver.

    Left to run, the model would reduce five readings and stop at the
    crossings of four of them -- no error, no diagnostic, and an overshoot on
    exactly the pairs the late source takes part in.
    """
    error = obs["err_late"]

    assert error is not None, "the late publisher must be refused"

    message = str(error)
    assert "'reading'" in message
    assert "4 sources" in message
    assert "5 now" in message


def test_delete(obs):
    obs["system"].deleteSys()
    cod3s.terminate_session()
