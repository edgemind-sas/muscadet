"""The two halves of the rate-comparison loop check that never fired (R-18/R-19).

``RateComparisonLoopError`` exists because a comparison against a continuous
flow VALUE is algebraic: the rate a producer exports this instant is a function
of a guard read this instant, with nothing integrated in between, so wiring the
result back upstream makes the two regimes select each other within one instant.
The model does not diverge -- it chatters at a period set by the integration
step, so a study silently never finishes rather than being refused.

Both halves of the check were unreachable, and both are exercised here.

**The seed.** ``compared_continuous_inputs`` deliberately reads *both*
vocabularies -- a rule guard (R21) and a discrete production condition (R22)
share one operand shape and one meaning -- but the walk was seeded from
production conditions alone. A comparison written as a **rule guard** therefore
produced no seed, no walk and no report, however plainly the loop closed. A
guard decides which rule of its set runs, so what carries it onward is what that
SET produces; those states are the seeds now.

**The gate.** ``gates_production_on`` saw a mode only through
``comp.mode_signals``, written by ``ObjFlow.add_atm2states`` and by nothing
else. A loop closed through a **standalone** failure mode -- an
``ObjFailureModeDelay`` clamping a derating on the very output it is gated by --
escaped entirely: the model built and the gated production settled on whatever
the topological order happened to evaluate first. Standalone modes now publish
what they read and what they write on each target.

Both models below build, run to completion and report a plausible number at
``399730d``. Refusing one that should build would be worse than missing one, so
the near misses of ``tests/test_ordering_001.py`` are re-asserted here against
the widened seeding.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, inspected and deleted before the next one starts; the fixture snapshots
what each produced.
"""

import warnings

import cod3s
import muscadet
import pytest

from muscadet import ordering

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import ConsumerContinuous  # noqa: F401

#: The rate a gated source exports while its control port is unfed.
RLS_RATE = 10.0
#: The threshold the comparison closing the loop is declared at.
RLS_THRESHOLD = 5.0
#: What a gate asks for: enough not to throttle what it watches.
RLS_DEMAND = 1e6


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class RlsGatedSource(muscadet.ObjFlow):
    """Produces its rate while ``run`` is UNFED, nothing once it is fed.

    The producing half of the loop, gated by a rule GUARD -- which
    ``gates_production_on`` already recognises.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=RLS_RATE)
        self.add_flow_in(name="run", logic="and")
        self.add_rules(
            name="q_control",
            rules=[
                dict(name="idle", cond="run", prod={"q": 0.0}),
                dict(name="supply", cond="not run", prod={"q": RLS_RATE}),
            ],
        )


class RlsGuardGate(muscadet.ObjFlow):
    """Thresholds the incoming RATE in a rule GUARD, not in a production condition.

    The guard selects between two rules producing different amounts of ``p``,
    and a discrete output thresholds ``p``. Nothing here is integrated: ``q``
    decides ``p`` decides ``run``, all within one instant.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=RLS_DEMAND)
        self.add_flow_continuous_out(name="p")
        self.add_rules(
            name="relay",
            rules=[
                dict(
                    name="high",
                    cond=f"q >= {RLS_THRESHOLD}",
                    cons={"q": 1.0},
                    prod={"p": RLS_RATE},
                ),
                dict(name="low", cons={"q": 1.0}, prod={"p": 0.0}),
            ],
        )
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="run",
                var_prod_cond=[
                    {"name": "p", "op": ">=", "value": RLS_THRESHOLD, "port": "out"}
                ],
            )
        )


class RlsPlainSource(muscadet.ObjFlow):
    """A source gated by NOTHING it declares itself.

    Its production is cut by a standalone failure mode instead, which is
    exactly what the second half of the check could not see.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=RLS_RATE)
        self.add_flow_in(name="run", logic="and")


class RlsRateGate(muscadet.ObjFlow):
    """A discrete output thresholded on the continuous rate it receives."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=RLS_DEMAND)
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="run",
                var_prod_cond=[{"name": "q", "op": ">=", "value": RLS_THRESHOLD}],
            )
        )


class RlsLevelSensor(muscadet.ObjFlow):
    """The sanctioned pattern: the comparison reads a capacity LEVEL (F4, AE18).

    Same topology as the refused ones -- it gates the very component filling
    the capacity it observes -- and it must keep building, because a level is
    integrated state and integrated state is what breaks a loop.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="buf")
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="run",
                var_prod_cond=[{"name": "buf", "op": ">=", "value": RLS_THRESHOLD}],
            )
        )


class RlsBufferedSink(muscadet.ObjFlow):
    """A continuous consumer holding a capacity and publishing its level."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=RLS_DEMAND)
        self.add_capacity(name="buf", flow="q", capacity=100.0, side="in")


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_guard_seed_scenario(obs):
    """A rule GUARD thresholding a rate, wired back to the rate's producer."""
    system = muscadet.System(name="RateLoopGuardSeed")

    system.add_component(name="RG_SRC", cls="RlsGatedSource")
    system.add_component(name="RG_GATE", cls="RlsGuardGate")
    system.add_component(
        name="RG_SINK", cls="ConsumerContinuous", flow="p", demand=RLS_DEMAND
    )

    system.connect_flow(source="RG_SRC", target="RG_GATE", flow_name="q")
    system.connect_flow(source="RG_GATE", target="RG_SINK", flow_name="p")
    system.connect_flow(source="RG_GATE", target="RG_SRC", flow_name="run")

    gate = system.comp["RG_GATE"]
    obs["guard_compared"] = ordering.compared_continuous_inputs(gate)
    obs["guard_driven"] = ordering.comparison_driven_outputs(gate, "q")

    # Resolved rather than called: the seeding helper is what this defect
    # added, and an implementation without it must fail on the numbers below
    # rather than break the scenarios that follow in this module.
    seeds_of = getattr(ordering, "rule_guard_comparison_seeds", None)
    obs["guard_seeds"] = (
        None if seeds_of is None else seeds_of(gate, gate.flows_in["q"])
    )

    obs["guard_error"] = None
    obs["guard_started"] = False
    try:
        system.isimu_start()
        obs["guard_started"] = True
    except Exception as err:
        obs["guard_error"] = err

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            system.isimu_stop()
        except Exception:  # pragma: no cover - nothing was started
            pass

    system.deleteSys()


def run_standalone_mode_scenario(obs):
    """The same loop, closed through a STANDALONE failure mode."""
    warnings.simplefilter("ignore", DeprecationWarning)

    system = muscadet.System(name="RateLoopStandaloneMode")

    system.add_component(name="SF_SRC", cls="RlsPlainSource")
    system.add_component(name="SF_GATE", cls="RlsRateGate")

    system.connect_flow(source="SF_SRC", target="SF_GATE", flow_name="q")
    system.connect_flow(source="SF_GATE", target="SF_SRC", flow_name="run")

    # Delay 0 both ways: an instantaneous mode, so the loop it closes carries
    # no integrated state at all -- the shape RateComparisonLoopError exists
    # for, written with the mode declared outside the component.
    system.add_component(
        cls="ObjFailureModeDelay",
        fm_name="trip",
        targets=["SF_SRC"],
        failure_cond={"run": True},
        failure_effects={"q": 0.0},
        failure_param=[(0.0,)],
        repair_cond={"run": False},
        repair_param=[(0.0,)],
    )

    source = system.comp["SF_SRC"]
    obs["standalone_signals"] = {
        key: dict(value) for key, value in source.mode_signals.items()
    }
    obs["standalone_gates"] = ordering.gates_production_on(source, "run")

    obs["standalone_error"] = None
    obs["standalone_started"] = False
    try:
        system.isimu_start()
        obs["standalone_started"] = True
    except Exception as err:
        obs["standalone_error"] = err

    try:
        system.isimu_stop()
    except Exception:  # pragma: no cover - nothing was started
        pass

    system.deleteSys()


def run_near_miss_scenario(obs):
    """The sanctioned pattern, and a standalone mode that closes nothing.

    Refusing either would be worse than missing a loop: the first is the
    documented way to gate production on a quantity (F4, AE18), and the second
    is an ordinary failure mode on a component that happens to carry a control
    port nothing decides.
    """
    warnings.simplefilter("ignore", DeprecationWarning)

    system = muscadet.System(name="RateLoopNearMiss")

    # The sanctioned pattern: the comparison reads a LEVEL, which is integrated.
    system.add_component(name="OK_SRC", cls="RlsGatedSource")
    system.add_component(name="OK_TANK", cls="RlsBufferedSink")
    system.add_component(name="OK_SENS", cls="RlsLevelSensor")
    system.connect_flow(source="OK_SRC", target="OK_TANK", flow_name="q")
    system.connect("OK_TANK", "buf_level_out", "OK_SENS", "buf_level_in")
    system.connect_flow(source="OK_SENS", target="OK_SRC", flow_name="run")

    # A standalone mode on a source whose control port carries no comparison:
    # publishing what the mode reads must not, on its own, refuse anything.
    system.add_component(name="BY_SRC", cls="RlsPlainSource")
    system.add_component(name="BY_TANK", cls="RlsBufferedSink")
    system.connect_flow(source="BY_SRC", target="BY_TANK", flow_name="q")
    system.add_component(
        cls="ObjFailureModeDelay",
        fm_name="wear",
        targets=["BY_SRC"],
        failure_cond={"run": True},
        failure_effects={"q": 0.0},
        failure_param=[(1.0,)],
        repair_param=[(1.0,)],
    )

    obs["near_error"] = None
    obs["near_started"] = False
    try:
        system.isimu_start()
        obs["near_started"] = True
    except Exception as err:
        obs["near_error"] = err

    obs["near_edges"] = ordering.build_continuous_flow_graph(system).edges

    try:
        system.isimu_stop()
    except Exception:  # pragma: no cover - nothing was started
        pass

    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Every scenario, built, inspected and deleted in turn."""
    obs = {}

    run_guard_seed_scenario(obs)
    run_standalone_mode_scenario(obs)
    run_near_miss_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# The seed a rule guard was never given
# ----------------------------------------------------------------------


def test_a_rule_guard_comparison_is_collected_and_now_seeds_the_walk(the_run):
    """It was collected all along; nothing was ever done with it."""
    assert the_run["guard_compared"] == {"q": f"q >= {RLS_THRESHOLD:g}"}
    assert the_run["guard_seeds"] == {"p_fed_out"}
    assert the_run["guard_driven"] == ["run"], (
        "the guard decides what the set produces, and a discrete output "
        "thresholding that is what carries the comparison out of the component"
    )


def test_a_rate_compared_in_a_rule_guard_closes_the_loop(the_run):
    """The model must not start: nothing integrates anywhere on that path."""
    error = the_run["guard_error"]

    assert error is not None, "a guard on a rate wired back upstream must not start"
    assert isinstance(error, ordering.RateComparisonLoopError)
    assert isinstance(error, muscadet.ContinuousFlowCycleError)
    assert the_run["guard_started"] is False


def test_the_guard_loop_error_names_the_comparison_and_the_way_out(the_run):
    """Same diagnostic as the production-condition shape, on the guard shape."""
    message = str(the_run["guard_error"])

    assert "RG_SRC.q_out -> RG_GATE.q_in" in message
    assert "RG_GATE.run_out -> RG_SRC.run_in" in message
    assert f"q >= {RLS_THRESHOLD:g}" in message
    assert "CAPACITY LEVEL" in message

    assert the_run["guard_error"].reader == "RG_GATE"
    assert the_run["guard_error"].flow == "q"


# ----------------------------------------------------------------------
# The gate a standalone failure mode was never behind
# ----------------------------------------------------------------------


def test_a_standalone_mode_publishes_what_it_reads_and_writes(the_run):
    """``mode_signals`` was written by ``add_atm2states`` and by nothing else."""
    signals = the_run["standalone_signals"]

    assert signals, "a standalone mode must publish its signals on its target"

    entry = next(iter(signals.values()))
    assert (
        "run_fed_in" in entry["conditions"]
    ), "the dict shorthand reads the target's INPUT flow"
    assert any(
        "derating_q" in name for name in entry["effects"]
    ), "the mode clamps a derating on the output it bears on"


def test_a_standalone_mode_gates_production_on_its_condition(the_run):
    """The predicate the loop walk stops at, which answered False for every one."""
    assert the_run["standalone_gates"] is True


def test_a_loop_closed_through_a_standalone_mode_is_refused(the_run):
    """The model built and settled on whatever ran first; now it is refused."""
    error = the_run["standalone_error"]

    assert (
        error is not None
    ), "a rate loop closed through a standalone mode must not start"
    assert isinstance(error, ordering.RateComparisonLoopError)
    assert the_run["standalone_started"] is False

    message = str(error)
    assert "SF_SRC.q_out -> SF_GATE.q_in" in message
    assert "SF_GATE.run_out -> SF_SRC.run_in" in message


# ----------------------------------------------------------------------
# What must still build
# ----------------------------------------------------------------------


def test_the_near_misses_still_build(the_run):
    """The sensor pattern, and a standalone mode closing nothing.

    A widened seed that refused either of these would be worse than the defect
    it fixes.
    """
    assert the_run["near_error"] is None, str(the_run["near_error"])
    assert the_run["near_started"] is True

    # The continuous graph is exactly the supply edges: none of the discrete or
    # measurement traffic wired over them adds one.
    assert the_run["near_edges"] == [("OK_SRC", "OK_TANK"), ("BY_SRC", "BY_TANK")]


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
