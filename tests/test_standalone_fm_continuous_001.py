"""A STANDALONE failure mode against a continuous output (R-4, R18, R19).

``ObjFailureModeExp`` / ``ObjFailureModeDelay`` declare their effects from
outside the components they hit, resolving each pattern against the target's
output flows. Until R-4 was closed they resolved every one of them to
``var_fed_available`` -- the boolean gate a DISCRETE output carries. A
continuous output declares no such gate: R19 gives it a rate instead, so the
field stays at its ``FlowModel`` default of ``None`` and construction aborted
with ``AttributeError: 'NoneType' object has no attribute
'addSensitiveMethod'``, naming neither the component, nor the flow, nor the
derating API that exists for exactly this case.

What this module pins down is the routing that replaced it: a pattern matching
a continuous output is a DERATING declaration, allocated through
``ObjFlow.add_derating`` -- public, its docstring says, "so that a mode
declared OUTSIDE the component can allocate the variable it needs and target
it". Which makes a standalone mode usable against a continuous output rather
than merely refused.

Three properties follow, and are asserted below:

* the effect composes by MINIMUM with any other mode derating the same output
  (R18, R20), instead of the last writer winning;
* the direction that does not name the output releases it back to nominal, a
  derating having no per-step reset;
* a second-order mode's two automata over one target own two variables, not
  one -- which is why the derating key is per automaton and not per mode.

The pattern-matched-nothing guard is asserted too: it used to be suppressed on
this path, ``fo_found`` being set even where no usable effect was produced.

PyCATSHOO forbids more than one live system per process, so each scenario
builds, drives and deletes its system before the next one starts; the last is
kept alive for the teardown.
"""

import warnings

import cod3s
import muscadet
import pytest

from muscadet.flow_continuous import NOMINAL_RATE

#: The wrapper classes warn on instantiation; the deprecation is asserted in
#: tests/test_objfailuremode_deprecation.py and is not what is under test here.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

#: Horizon the interactive sessions run to.
HORIZON = 6.0

#: A mode that never comes back within the horizon.
NEVER = 1e6

#: What the source can produce, and what the sink asks of it.
SRC_RATE = 10.0
SINK_DEMAND = 4.0

#: The derating a mode leaves, and when it fires.
DERATE_TO = 0.25
DERATE_DATE = 2.0

#: A second mode on the same output, firing later and derating LESS deeply.
SECOND_DERATE_TO = 0.5
SECOND_DERATE_DATE = 3.0

#: When the first mode repairs, restoring the rate it took.
REPAIR_DATE = 4.0


# ----------------------------------------------------------------------
# Components -- prefixed, since component classes resolve by name globally
# ----------------------------------------------------------------------


class StandaloneFmMixed(muscadet.ObjFlow):
    """A component carrying BOTH families of output.

    The shape a ``".*"`` effect pattern hits without the modeller intending to
    derate anything: one continuous output holding a rate, one discrete output
    holding an availability gate.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name="q", var_fed_default=kwargs.get("rate", SRC_RATE)
        )
        self.add_flow_out(name="alive", var_prod_default=True)


class StandaloneFmSink(muscadet.ObjFlow):
    """A continuous consumer publishing a declared demand."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name="q", var_demand_default=kwargs.get("demand", SINK_DEMAND)
        )


class StandaloneFmDiscrete(muscadet.ObjFlow):
    """A purely discrete component: the 1.x resolution must be untouched."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="sig", var_prod_default=True)


class StandaloneFmClock(muscadet.ObjFlow):
    """Dates the interactive session can always step to."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="tick", var_prod_default=True)


def add_clock(comp, date):
    """Give the interactive session a date it can always step to."""
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def walk(system, snap, horizon, limit=80):
    """Step to ``horizon``, recording ``snap(system)`` at every stop."""
    trace = [snap(system)]

    for _ in range(limit):
        if system.currentTime() >= horizon:
            break
        system.isimu_step_forward()
        trace.append(snap(system))

    return trace


def at_or_after(trace, date):
    """The first stop at or after ``date``: where a RATE is read.

    A stop reports the left limit of the continuous variables, so at the very
    instant a mode fires the effective rate already reflects it while the
    quantities still hold what the integration up to that instant produced.
    """
    for entry in trace:
        if entry["time"] >= date - 1e-9:
            return entry
    raise AssertionError(f"no stop at or after t={date}")


def strictly_after(trace, date):
    """The first stop strictly after ``date``: where a QUANTITY is read."""
    for entry in trace:
        if entry["time"] > date + 1e-9:
            return entry
    raise AssertionError(f"no stop after t={date}")


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_guard_scenario(obs):
    """What a standalone mode still refuses, and what it now accepts."""
    system = muscadet.System(name="StandaloneFmGuard")

    # Two targets of the same shape: a refused declaration still allocates
    # whatever it resolved before raising, so what the guards leave behind is
    # kept away from the component the wildcard is measured on.
    system.add_component(name="MIX", cls="StandaloneFmMixed")
    system.add_component(name="GUARDED", cls="StandaloneFmMixed")
    system.add_component(name="SINK", cls="StandaloneFmSink")
    system.connect_flow(source="MIX", target="SINK", flow_name="q")

    def declare(**specs):
        try:
            system.add_component(cls="ObjFailureModeDelay", **specs)
        except Exception as err:
            return err
        return None

    # The guard that must still fire: a pattern matching NO flow out. It used
    # to be suppressed on the continuous path, fo_found being set even where
    # var_fed_available was None.
    obs["err_no_match"] = declare(
        fm_name="ghost",
        targets=["GUARDED"],
        failure_effects={"nowhere": 0.0},
        failure_param=[(1.0,)],
        repair_param=[(NEVER,)],
    )
    obs["err_no_match_repair"] = declare(
        fm_name="ghost_rep",
        targets=["GUARDED"],
        failure_effects={"q": 0.0},
        repair_effects={"nowhere": 1.0},
        failure_param=[(1.0,)],
        repair_param=[(NEVER,)],
    )

    # ... and the filed reproduction, which must now go through: a ".*"
    # pattern over a component mixing both families.
    obs["err_wildcard"] = declare(
        fm_name="wildcard",
        targets=["MIX"],
        failure_effects={".*": 0.0},
        repair_effects={".*": 1.0},
        failure_param=[(1.0,)],
        repair_param=[(NEVER,)],
    )

    mix = system.comp["MIX"]
    obs["wildcard_deratings"] = sorted(
        var.basename() for var in mix.flows_out["q"].derating.values()
    )
    # The discrete output beside it carries no derating at all: a boolean gate
    # is clamped directly, as it has been since 1.x.
    obs["wildcard_discrete_untouched"] = not any(
        "derating_alive" in var.basename() for var in mix.variables()
    )

    system.deleteSys()


def build_derating_system(name):
    """A source derated by two standalone modes, one of which repairs."""
    system = muscadet.System(name=name)

    system.add_component(name="MIX", cls="StandaloneFmMixed")
    system.add_component(name="SINK", cls="StandaloneFmSink")
    system.add_component(name="SIG", cls="StandaloneFmDiscrete")
    system.connect_flow(source="MIX", target="SINK", flow_name="q")

    # A deep derating that repairs: the repair direction names NOTHING, so the
    # release is what must hand the rate back.
    system.add_component(
        cls="ObjFailureModeDelay",
        fm_name="deep",
        targets=["MIX"],
        failure_effects={"q": DERATE_TO},
        failure_param=[(DERATE_DATE,)],
        repair_param=[(REPAIR_DATE - DERATE_DATE,)],
    )

    # A shallower one that never repairs, declared as a wildcard so it also
    # gates the discrete output beside it.
    system.add_component(
        cls="ObjFailureModeDelay",
        fm_name="shallow",
        targets=["MIX"],
        failure_effects={".*": SECOND_DERATE_TO},
        failure_param=[(SECOND_DERATE_DATE,)],
        repair_param=[(NEVER,)],
    )

    # A purely discrete target, on the 1.x resolution: unchanged.
    system.add_component(
        cls="ObjFailureModeDelay",
        fm_name="mute",
        targets=["SIG"],
        failure_effects={"sig": False},
        failure_param=[(DERATE_DATE,)],
        repair_param=[(NEVER,)],
    )

    system.add_component(name="CLOCK", cls="StandaloneFmClock")
    for date in (1.0, DERATE_DATE + 0.5, SECOND_DERATE_DATE + 0.5, REPAIR_DATE + 0.5):
        add_clock(system.comp["CLOCK"], date)
    add_clock(system.comp["CLOCK"], HORIZON)

    return system


def snapshot(system):
    """Rates, quantities and gates at one stop."""
    mix = system.comp["MIX"]
    return {
        "time": system.currentTime(),
        "rate": mix.flows_out["q"].get_effective_rate(),
        "produced": mix.flows_out["q"].var_fed.value(),
        "delivered": system.comp["SINK"].flows_in["q"].var_fed.value(),
        "alive": mix.flows_out["alive"].var_fed.value(),
        "sig": system.comp["SIG"].flows_out["sig"].var_fed.value(),
        "deratings": {
            mode: var.value() for mode, var in mix.flows_out["q"].derating.items()
        },
    }


def run_derating_scenario(obs):
    """Drive the derated source stop by stop."""
    system = build_derating_system("StandaloneFmDerating")

    system.isimu_start()
    obs["trace"] = walk(system, snapshot, HORIZON)
    system.isimu_stop()

    obs["mix"] = system.comp["MIX"]

    # Kept alive for the teardown test, per the module convention.
    obs["system"] = system


def run_order_scenario(obs):
    """A SECOND-ORDER mode: two automata over one target, two variables.

    A common-cause mode of order 2 builds one automaton per combination of its
    targets -- ``{A}``, ``{B}`` and ``{A, B}`` -- and each of them derates. Were
    they to share one derating variable per (mode, output) pair, they would
    overwrite one another and the one repairing first would restore the rate
    while the other degradation still stood: exactly what R18 exists to
    prevent.
    """
    system = muscadet.System(name="StandaloneFmOrder")

    system.add_component(name="A", cls="StandaloneFmMixed")
    system.add_component(name="B", cls="StandaloneFmMixed")

    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="cc",
        targets=["A", "B"],
        failure_effects={"q": 0.5},
        failure_param=[1e-3, 1e-3],
        repair_param=[1e-3, 1e-3],
    )

    obs["order_deratings_a"] = sorted(system.comp["A"].flows_out["q"].derating)
    obs["order_var_names_a"] = sorted(
        var.basename() for var in system.comp["A"].flows_out["q"].derating.values()
    )

    system.deleteSys()


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        run_guard_scenario(obs)
        run_order_scenario(obs)
        run_derating_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# The filed crash, and the guard that used to be suppressed
# ----------------------------------------------------------------------


def test_a_wildcard_over_both_families_builds(the_run):
    """R-4's reproduction: ``failure_effects={".*": ...}`` on a mixed component.

    It used to abort model construction with ``AttributeError: 'NoneType'
    object has no attribute 'addSensitiveMethod'``. It now resolves: the
    continuous output through a derating variable, the discrete one through
    its availability gate.
    """
    assert the_run["err_wildcard"] is None, the_run["err_wildcard"]

    # One variable, allocated on the continuous output by the mode itself
    assert len(the_run["wildcard_deratings"]) == 1
    assert the_run["wildcard_deratings"][0].endswith("_derating_q")

    # ... and nothing allocated on the discrete output beside it
    assert the_run["wildcard_discrete_untouched"]


def test_a_pattern_matching_no_flow_is_still_refused(the_run):
    """The guard R-4 suppressed: ``fo_found`` used to be set regardless.

    A pattern naming a flow that does not exist is a modelling mistake and must
    stay one, in both directions of the mode.
    """
    failure_error = the_run["err_no_match"]
    assert isinstance(failure_error, ValueError)
    assert "does not match any flow out" in str(failure_error)
    assert "Failure effects of mode ghost" in str(failure_error)
    assert "nowhere" in str(failure_error)

    repair_error = the_run["err_no_match_repair"]
    assert isinstance(repair_error, ValueError)
    assert "does not match any flow out" in str(repair_error)
    assert "Repair effects of mode ghost_rep" in str(repair_error)


# ----------------------------------------------------------------------
# What the routing actually does to the model
# ----------------------------------------------------------------------


def test_the_output_runs_at_nominal_before_the_mode_fires(the_run):
    """Nothing is derated until something derates it."""
    start = the_run["trace"][0]

    assert start["rate"] == pytest.approx(NOMINAL_RATE)
    assert start["delivered"] == pytest.approx(SINK_DEMAND)


def test_a_standalone_mode_derates_the_continuous_output(the_run):
    """The effect reaches the rate, and the delivery follows it.

    Which is the capability the release lacked: before R-4 this model could not
    be built at all.
    """
    assert at_or_after(the_run["trace"], DERATE_DATE)["rate"] == pytest.approx(
        DERATE_TO
    )

    # 10 produced at a quarter of nominal is 2.5, below the 4 asked for
    settled = strictly_after(the_run["trace"], DERATE_DATE)
    assert settled["produced"] == pytest.approx(SRC_RATE * DERATE_TO)
    assert settled["delivered"] == pytest.approx(SRC_RATE * DERATE_TO)


def test_two_standalone_modes_compose_by_minimum(the_run):
    """Not last-writer-wins: each mode owns its own variable (R18, R20).

    The shallower mode fires while the deeper one still holds, and the rate
    stays at the deeper of the two rather than being written back up.
    """
    entry = at_or_after(the_run["trace"], SECOND_DERATE_DATE)

    assert len(entry["deratings"]) == 2, "one variable per mode, not one shared"
    assert sorted(entry["deratings"].values()) == [
        pytest.approx(DERATE_TO),
        pytest.approx(SECOND_DERATE_TO),
    ]
    assert entry["rate"] == pytest.approx(min(DERATE_TO, SECOND_DERATE_TO))


def test_a_repaired_mode_releases_only_its_own_derating(the_run):
    """The deep mode repairs; the shallow one's degradation still stands.

    The repair direction of the deep mode names NOTHING, so the release is what
    hands its rate back -- a derating has no per-step reset, unlike a boolean
    gate. And it hands back its own only: the rate settles at the shallow
    mode's 0.5, not at nominal.
    """
    entry = at_or_after(the_run["trace"], REPAIR_DATE)

    assert entry["rate"] == pytest.approx(SECOND_DERATE_TO)
    assert max(entry["deratings"].values()) == pytest.approx(
        NOMINAL_RATE
    ), "the repaired mode handed its own rate back"
    assert min(entry["deratings"].values()) == pytest.approx(
        SECOND_DERATE_TO
    ), "... and only its own"

    settled = strictly_after(the_run["trace"], REPAIR_DATE)
    assert settled["delivered"] == pytest.approx(
        SINK_DEMAND
    ), "10 at half nominal is 5, which covers the 4 asked for"


def test_the_discrete_family_is_resolved_as_it_always_was(the_run):
    """A boolean effect still clamps an availability gate.

    Both on the discrete output sitting beside a continuous one -- reached by
    the same wildcard -- and on a purely discrete target, whose resolution this
    release must not have moved.
    """
    before = the_run["trace"][0]
    assert before["alive"] is True
    assert before["sig"] is True

    after = at_or_after(the_run["trace"], SECOND_DERATE_DATE)
    # What matters is that the gate, and not a derating variable, is what the
    # wildcard reached on the discrete output.
    basenames = {var.basename() for var in the_run["mix"].variables()}
    assert not any("derating_alive" in name for name in basenames)
    assert any("derating_q" in name for name in basenames)

    muted = at_or_after(the_run["trace"], DERATE_DATE)
    assert muted["sig"] is False, "the 1.x resolution is untouched"
    assert after["time"] >= SECOND_DERATE_DATE


def test_a_second_order_mode_owns_one_variable_per_automaton(the_run):
    """Three automata over two targets, and no two of them share a variable.

    Target A appears in the order-1 combination ``{A}`` and in the order-2
    combination ``{A, B}``. Each is a separate automaton with its own
    occurrence law, so each owns its own derating variable on A's output.
    """
    keys = the_run["order_deratings_a"]

    assert len(keys) == 2, f"expected one key per automaton over A, got {keys}"
    assert len(set(keys)) == len(keys)

    names = the_run["order_var_names_a"]
    assert len(set(names)) == 2
    assert all(name.endswith("_derating_q") for name in names)


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
