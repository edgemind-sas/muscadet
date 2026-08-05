"""The shared rate variable of a continuous output (KD10, R18, R19, R20).

Every continuous output carries ``{flow}_out_rate``, a double created with the
flow itself and holding 1. It is public, writable, and in existence from the
moment the flow is declared -- which is the whole point of it.

Why it has to exist
-------------------
``cod3s`` is the reference mechanism for failure modes, and a native
``cod3s.ObjMode2S`` / ``ObjFM*`` resolves its effects against the target
component's declared variables **by name**: it can clamp what is there, and it
has no way of asking for anything to be created. Before this variable existed,
the only names a continuous output offered were ``{flow}_fed_out`` and
``{flow}_demand_in`` -- both owned by the PDMP solver and rewritten at every
integration step, so a clamp on either is erased inside the step. The per-mode
derating variables that DO work were allocated on demand by muscadet's own
``add_derating``, so a native mode could not name one either: it did not exist
until muscadet made it. Declaring such a mode simply raised
``ValueError: Component ... has no attribute nor variable named H2``.

Hence the two mechanisms, and why both are kept:

* ``{flow}_out_rate`` -- ONE shared variable, for everything muscadet does not
  own. A mode outside the library clamps it by name, with no muscadet-specific
  call anywhere in the declaration.
* ``{mode}_derating_{flow}`` -- one per (mode, output) pair, for the modes
  muscadet declares itself and therefore knows the identity of, so that two of
  them derating one output neither overwrite each other nor un-derate on the
  first repair (R18, R20).

``get_effective_rate`` folds the lot by **minimum**, so the two compose instead
of competing: neither mechanism can hide a degradation the other is holding.

Reading the trace
-----------------
Everything runs in ONE interactive session -- PyCATSHOO forbids more than one
live system per process -- and every stop is snapshotted. A stop reports the
LEFT limit of the continuous variables: at the very instant a mode fires the
effective rate already reflects it while the quantities still hold what the
integration up to that instant produced. Rates are therefore asserted AT the
stop and quantities at the next one, which is what the clock stops are for.
"""

import cod3s
import muscadet
import pytest

from muscadet.flow_continuous import NOMINAL_RATE

#: Horizon the interactive session runs to.
HORIZON = 3.5

#: Stops added so that a quantity can be read strictly after each mode change.
CLOCK_DATES = (0.5, 1.5, 2.5, 3.5)

#: A mode that never comes back within the horizon.
NEVER = 1e6

#: What the plants produce nominally, and what their consumer asks of them.
PLANT_RATE = 10.0
SINK_DEMAND = 1e3

#: What the source feeds the converter with, throughout.
SUPPLY_RATE = 7.0

#: The rate a NATIVE cod3s mode leaves on the shared variable, and the deeper
#: one a muscadet mode leaves on the variable it owns.
SHARED_RATE = 0.5
DERATED_RATE = 0.3


# ----------------------------------------------------------------------
# Components -- prefixed, since component classes resolve by name globally
# ----------------------------------------------------------------------


class OutRatePlant(muscadet.ObjFlow):
    """A continuous producer holding the rate it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="H2", var_fed_default=PLANT_RATE)


class OutRateSink(muscadet.ObjFlow):
    """A continuous consumer asking for more than it can ever be served."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="H2", var_demand_default=SINK_DEMAND)


class OutRateSource(muscadet.ObjFlow):
    """Feeds the converter, and is never itself derated."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=SUPPLY_RATE)


class OutRateConverter(muscadet.ObjFlow):
    """One unit of ``q`` in, one unit of ``X`` out: production follows the input."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="X")
        self.add_rules(name="X", rules=[dict(cons={"q": 1}, prod={"X": 1})])


class OutRateSignal(muscadet.ObjFlow):
    """A purely DISCRETE producer: it carries no rate at all (R19)."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="b", var_prod_default=True)


class OutRateClock(muscadet.ObjFlow):
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


def build_system():
    system = muscadet.System(name="OutRateNativeSys")

    # -- The acceptance: a NATIVE cod3s mode, declared exactly as it would be
    #    against any other cod3s component. It names the output's rate
    #    variable, and nothing in this declaration is muscadet-specific.
    system.add_component(name="NATIVE", cls="OutRatePlant")
    system.add_component(name="NATIVE_SINK", cls="OutRateSink")
    system.connect_flow(source="NATIVE", target="NATIVE_SINK", flow_name="H2")
    native_mode = system.add_component(
        cls="ObjMode2S",
        mode_name="leak",
        targets=["NATIVE"],
        occ_law={"cls": "delay", "time": 1.0},
        not_occ_law={"cls": "delay", "time": 2.0},
        occ_effects={"H2_out_rate": SHARED_RATE},
        not_occ_effects={"H2_out_rate": NOMINAL_RATE},
    )

    # -- The two mechanisms at once, on ONE output: a native mode clamping the
    #    shared variable at 1, a muscadet mode clamping the variable it owns at
    #    2, and neither ever repairing. The minimum has to change hands.
    system.add_component(name="MIXED", cls="OutRatePlant")
    system.add_component(
        cls="ObjMode2S",
        mode_name="shrink",
        targets=["MIXED"],
        occ_law={"cls": "delay", "time": 1.0},
        not_occ_law={"cls": "delay", "time": NEVER},
        occ_effects={"H2_out_rate": SHARED_RATE},
    )
    system.comp["MIXED"].add_delay_failure_mode(
        name="deep",
        failure_time=2.0,
        failure_effects=[("H2", DERATED_RATE)],
        repair_time=NEVER,
    )

    # -- R19: the shared rate driven to 0, on a component whose production
    #    follows its input. The input keeps arriving; the output stops.
    system.add_component(name="SUPPLY", cls="OutRateSource")
    system.add_component(name="ZEROED", cls="OutRateConverter")
    system.connect_flow(source="SUPPLY", target="ZEROED", flow_name="q")
    system.add_component(
        cls="ObjMode2S",
        mode_name="cut",
        targets=["ZEROED"],
        occ_law={"cls": "delay", "time": 1.0},
        not_occ_law={"cls": "delay", "time": NEVER},
        occ_effects={"X_out_rate": 0.0},
    )

    # -- A purely discrete producer beside them, untouched by any of this.
    system.add_component(name="SIGNAL", cls="OutRateSignal")

    system.add_component(name="CLOCK", cls="OutRateClock")
    for date in CLOCK_DATES:
        add_clock(system.comp["CLOCK"], date)

    return system, native_mode


def snapshot(system):
    """Rates, shared variables and quantities at one stop."""
    native = system.comp["NATIVE"].flows_out["H2"]
    mixed = system.comp["MIXED"].flows_out["H2"]
    zeroed = system.comp["ZEROED"].flows_out["X"]

    return {
        "time": system.currentTime(),
        "native_rate": native.get_effective_rate(),
        "native_shared": native.var_out_rate.value(),
        "native_out": native.var_fed.value(),
        "native_sink": system.comp["NATIVE_SINK"].flows_in["H2"].var_fed.value(),
        "mixed_rate": mixed.get_effective_rate(),
        "mixed_shared": mixed.var_out_rate.value(),
        "mixed_derating": {mode: var.value() for mode, var in mixed.derating.items()},
        "zeroed_rate": zeroed.get_effective_rate(),
        "zeroed_out": zeroed.var_fed.value(),
        "zeroed_in": system.comp["ZEROED"].flows_in["q"].var_fed.value(),
    }


@pytest.fixture(scope="module")
def the_run():
    """Drive the system to the horizon, recording every stop."""
    system, native_mode = build_system()

    system.isimu_start()

    trace = [snapshot(system)]
    for _ in range(60):
        system.isimu_step_forward()
        trace.append(snapshot(system))
        if system.currentTime() >= HORIZON:
            break

    system.isimu_stop()

    return {"system": system, "trace": trace, "native_mode": native_mode}


def at(trace, date):
    """The stop AT ``date``: rates are read there, the mode has just fired."""
    for entry in trace:
        if entry["time"] == pytest.approx(date):
            return entry
    raise AssertionError(
        f"no stop at t={date}; stops were {[e['time'] for e in trace]}"
    )


def after(trace, date):
    """The first stop strictly after ``date``: where the quantities are read."""
    for entry in trace:
        if entry["time"] > date:
            return entry
    raise AssertionError(f"no stop after t={date}")


# ----------------------------------------------------------------------
# The variable itself
# ----------------------------------------------------------------------


def test_a_continuous_output_carries_its_rate_variable_from_construction(the_run):
    """``H2`` gives ``H2_out_rate``, a double at 1, declared with the flow.

    Not allocated on demand by a muscadet call: a mode that knows nothing of
    muscadet can only name a variable that is already there.
    """
    flow = the_run["system"].comp["NATIVE"].flows_out["H2"]

    assert flow.rate_var_name() == "H2_out_rate"
    assert flow.var_out_rate is not None
    assert flow.var_out_rate.basename() == "H2_out_rate"

    # Reachable by name on the component, which is how a mode reaches it.
    basenames = {var.basename() for var in the_run["system"].comp["NATIVE"].variables()}
    assert "H2_out_rate" in basenames

    # And nothing derates it at the start of the run.
    assert the_run["trace"][0]["native_shared"] == pytest.approx(NOMINAL_RATE)
    assert the_run["trace"][0]["native_rate"] == pytest.approx(NOMINAL_RATE)
    assert the_run["trace"][0]["native_out"] == pytest.approx(PLANT_RATE)


def test_only_a_continuous_output_carries_one(the_run):
    """An input has no rate to give, and a discrete output has a gate instead."""
    system = the_run["system"]

    sink_names = {var.basename() for var in system.comp["NATIVE_SINK"].variables()}
    assert not any(name.endswith("_out_rate") for name in sink_names)

    signal_names = {var.basename() for var in system.comp["SIGNAL"].variables()}
    assert not any(name.endswith("_out_rate") for name in signal_names)
    assert "b_fed_available_out" in signal_names


# ----------------------------------------------------------------------
# The acceptance: a NATIVE cod3s mode derating a muscadet continuous output
# ----------------------------------------------------------------------


def test_the_mode_under_test_is_a_native_cod3s_one(the_run):
    """No muscadet class, no muscadet call: the declaration is pure cod3s.

    If this test needed ``muscadet.ObjFailureModeExp``, or a call to
    ``add_derating`` to bring a variable into existence first, the change would
    have missed its purpose -- cod3s is the reference mechanism for failure
    modes, and it must work against a muscadet component out of the box.
    """
    mode = the_run["native_mode"]

    assert isinstance(mode, cod3s.ObjMode2S)
    assert type(mode).__module__.startswith("cod3s.")

    # It clamps the shared variable, and the per-mode registry stays empty:
    # muscadet allocated nothing on this output's behalf.
    assert the_run["system"].comp["NATIVE"].flows_out["H2"].derating == {}


def test_a_native_cod3s_mode_derates_the_output_while_it_holds(the_run):
    """The mode fires at 1 and the output produces half of its nominal rate.

    Before the shared variable existed this declaration did not run at all: it
    raised ``ValueError: ... has no attribute nor variable named H2_out_rate``,
    the only names on offer being the two the PDMP solver rewrites every step.
    """
    trace = the_run["trace"]

    fired = at(trace, 1.0)
    assert fired["native_shared"] == pytest.approx(SHARED_RATE)
    assert fired["native_rate"] == pytest.approx(SHARED_RATE)

    # The quantity follows at the next stop, the run having resumed.
    resumed = after(trace, 1.0)
    assert resumed["native_out"] == pytest.approx(PLANT_RATE * SHARED_RATE)
    assert resumed["native_sink"] == pytest.approx(PLANT_RATE * SHARED_RATE)


def test_the_native_mode_returns_the_output_to_nominal_on_repair(the_run):
    """The mode repairs at 3 and the output produces in full again."""
    trace = the_run["trace"]

    repaired = at(trace, 3.0)
    assert repaired["native_shared"] == pytest.approx(NOMINAL_RATE)
    assert repaired["native_rate"] == pytest.approx(NOMINAL_RATE)

    resumed = after(trace, 3.0)
    assert resumed["native_out"] == pytest.approx(PLANT_RATE)
    assert resumed["native_sink"] == pytest.approx(PLANT_RATE)


# ----------------------------------------------------------------------
# R19: one number expresses the degradation AND the cut
# ----------------------------------------------------------------------


def test_a_shared_rate_of_zero_stops_production_whatever_the_inputs(the_run):
    """R19: the rate is driven to 0 and ``X`` produces nothing.

    ZEROED converts one unit of ``q`` into one of ``X`` and keeps being fed 7
    of ``q`` throughout: the output stops because the rate is 0, not because
    the input dried up. A continuous flow carries no separate boolean
    availability gate -- the one number expresses both the cut and the
    degradation.
    """
    trace = the_run["trace"]

    before = at(trace, 0.5)
    assert before["zeroed_rate"] == pytest.approx(NOMINAL_RATE)
    assert before["zeroed_out"] == pytest.approx(SUPPLY_RATE)

    cut = at(trace, 1.0)
    assert cut["zeroed_rate"] == pytest.approx(0.0)
    assert cut["zeroed_out"] == pytest.approx(
        SUPPLY_RATE
    ), "it produced 7 up to the cut"

    stopped = after(trace, 1.0)
    assert stopped["zeroed_out"] == pytest.approx(0.0)
    assert stopped["zeroed_in"] == pytest.approx(SUPPLY_RATE), "the input never stopped"

    # ... and it stays at zero for the rest of the run.
    for entry in trace:
        if entry["time"] > 1.0:
            assert entry["zeroed_out"] == pytest.approx(0.0)
            assert entry["zeroed_in"] == pytest.approx(SUPPLY_RATE)


# ----------------------------------------------------------------------
# R20: the two mechanisms compose by minimum, neither one wins
# ----------------------------------------------------------------------


def test_the_shared_rate_and_a_per_mode_derating_compose_by_minimum(the_run):
    """0.5 on the shared variable, 0.3 on a per-mode one: the rate is 0.3.

    Both hold their own value at the same time and neither is overwritten. The
    minimum changes hands during the run, which is what proves it is a fold and
    not one mechanism winning:

    * from 1, the shared variable is the deeper of the two (0.5 against 1);
    * from 2, the per-mode derating is (0.3 against 0.5).
    """
    trace = the_run["trace"]

    # The shared variable alone.
    shared_only = at(trace, 1.0)
    assert shared_only["mixed_shared"] == pytest.approx(SHARED_RATE)
    assert shared_only["mixed_derating"] == {"deep": pytest.approx(NOMINAL_RATE)}
    assert shared_only["mixed_rate"] == pytest.approx(SHARED_RATE)
    assert after(trace, 1.0)["mixed_rate"] == pytest.approx(SHARED_RATE)

    # ... then the per-mode one goes deeper, WITHOUT touching the shared one.
    both = at(trace, 2.0)
    assert both["mixed_shared"] == pytest.approx(SHARED_RATE)
    assert both["mixed_derating"] == {"deep": pytest.approx(DERATED_RATE)}
    assert both["mixed_rate"] == pytest.approx(DERATED_RATE)

    # Not the product (0.15), and not the last one written on its own.
    assert both["mixed_rate"] != pytest.approx(SHARED_RATE * DERATED_RATE)

    # Two distinct variables holding two distinct values at once, for the rest
    # of the run: the fold is a reading, not an assignment.
    for entry in trace:
        if entry["time"] >= 2.0:
            assert entry["mixed_shared"] == pytest.approx(SHARED_RATE)
            assert entry["mixed_derating"]["deep"] == pytest.approx(DERATED_RATE)
            assert entry["mixed_rate"] == pytest.approx(DERATED_RATE)


def test_setting_the_rate_by_hand_is_read_back_by_the_effective_rate(the_run):
    """The variable is public and writable, and the fold reads it live.

    Asserted OUTSIDE the run (the session is over), so that nothing re-clamps
    it: this is the plain contract of the variable, independent of any mode.
    """
    # The output of a component no mode touches at all.
    supply = the_run["system"].comp["SUPPLY"].flows_out["q"]

    assert supply.derating == {}
    assert supply.get_effective_rate() == pytest.approx(NOMINAL_RATE)

    supply.var_out_rate.setValue(0.25)
    assert supply.get_effective_rate() == pytest.approx(0.25)

    supply.var_out_rate.setValue(0.0)
    assert supply.get_effective_rate() == pytest.approx(0.0)

    supply.var_out_rate.setValue(NOMINAL_RATE)
    assert supply.get_effective_rate() == pytest.approx(NOMINAL_RATE)


# ----------------------------------------------------------------------
# The pattern form, for the callers that hold a pattern rather than a name
# ----------------------------------------------------------------------


def test_a_pattern_naming_the_output_resolves_onto_its_rate_variable(the_run):
    """``pat_to_var_value_list("H2")`` gives ``H2_out_rate``, and nothing else.

    The cod3s utility resolves a pattern against the component's variable
    basenames, which on a continuous output would otherwise return
    ``H2_fed_out`` -- rewritten by the production equation at every integration
    step, so a clamp on it is erased inside the step (R19).
    """
    plant = the_run["system"].comp["NATIVE"]

    resolved = plant.pat_to_var_value_list(("H2", 0.5))
    assert [(var.basename(), value) for var, value in resolved] == [
        ("H2_out_rate", 0.5)
    ]

    # The solver-owned endpoint is dropped whatever the spelling.
    solver_owned = plant.continuous_endpoint_names()
    assert "H2_fed_out" in solver_owned
    for pattern in ("H2", "H2_fed_out", ".*"):
        names = {var.basename() for var, _ in plant.pat_to_var_value_list((pattern, 0))}
        assert not (names & solver_owned), pattern

    # A discrete component keeps the 1.x resolution untouched.
    signal = the_run["system"].comp["SIGNAL"]
    assert {
        var.basename()
        for var, _ in signal.pat_to_var_value_list(("b_fed_available_out", False))
    } == {"b_fed_available_out"}


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
