"""Declaring a rule must not stop the flows the rule says nothing about (R-16).

Both sweeps branched on ``if comp.rule_sets:``, so the R31 identity transfer was
switched OFF for every continuous flow the rules did not name -- and the R31
mismatch check lived in the same dead branch, so nothing was reported either.

Measured at ``399730d`` on the splitter below (``flows_in=[a, b]``,
``flows_out=[x, b]``, one rule ``cons={a: 1} -> prod={x: 1}``): ``b`` was fed at
3.0 and the component asked for **0.0** of it, emitted **0.0** of it, and
everything downstream of ``b`` read zero. The very same component *without* the
rule would either transfer ``b`` or raise. Adding a rule to an existing buffer or
splitter therefore killed its untouched flows, silently.

A rule set says what its component TRANSFORMS, not what its component carries.
So the transfer is now subtracted from rather than switched off: every
continuous flow present on both sides and named by no rule set is a
pass-through, exactly as it is on a component that declares no rule at all --
and the mismatch check applies to that same residue, so a hole in the model is
reported instead of being silently emptied.

Conservation is asserted per component and per stop: what an input draws equals
what the rules consume plus what the transfer carries plus what a capacity
stores.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, driven and deleted before the next one starts; the fixture snapshots what
each produced and the last is kept alive for the teardown.
"""

import cod3s
import muscadet
import pytest

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    ConsumerContinuous,
    SourceContinuous,
)

#: What each source produces.
RRT_A_RATE = 1.0
RRT_B_RATE = 3.0
#: What the consumer of the untouched flow asks for: less than it is offered,
#: so the transfer is metered rather than merely non-zero.
RRT_B_DEMAND = 2.0
#: Plenty: the reacting leg must be limited by its supply, not by its consumer.
RRT_X_DEMAND = 100.0
RRT_HORIZON = 1.0


class RrtSplitter(muscadet.ObjFlow):
    """Reacts on ``a``, and carries ``b`` straight through beside it.

    The shape of every real splitter, buffer and manifold: one leg transformed,
    the others untouched.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_in(name="b")
        self.add_flow_continuous_out(name="x")
        self.add_flow_continuous_out(name="b")
        self.add_rules(name="react", rules=[dict(cons={"a": 1.0}, prod={"x": 1.0})])


class RrtPlainSplitter(muscadet.ObjFlow):
    """The same ``b`` leg, with NO rule: the reference the rule must not change.

    ``a`` is carried on both sides here rather than transformed into ``x``,
    because a rule-less component transfers name for name (R31). What matters
    is that the ``b`` leg is declared and wired exactly as it is on
    :class:`RrtSplitter`.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_in(name="b")
        self.add_flow_continuous_out(name="a")
        self.add_flow_continuous_out(name="b")


class RrtHole(muscadet.ObjFlow):
    """A rule on ``a``, a transfer on ``b``, and a wired ``c`` going nowhere.

    ``c`` is named by no rule and has no output of the same name: a quantity
    arrives and vanishes. The residue straddles both sides (``b`` is on both),
    so this is the case the mismatch check must report.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_in(name="b")
        self.add_flow_continuous_in(name="c")
        self.add_flow_continuous_out(name="x")
        self.add_flow_continuous_out(name="b")
        self.add_rules(name="react", rules=[dict(cons={"a": 1.0}, prod={"x": 1.0})])


class RrtSink(muscadet.ObjFlow):
    """A rule on ``a``, and an extra wired input no rule and no output covers.

    Its residue lies on ONE side only, so it transfers nothing and demands no
    counterpart -- exactly like a pure consumer. This must go on building: a
    component may legitimately carry an input a future mode will consume.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_in(name="spare", var_demand_default=1.0)
        self.add_flow_continuous_out(name="x")
        self.add_rules(name="react", rules=[dict(cons={"a": 1.0}, prod={"x": 1.0})])


class RrtClock(muscadet.ObjFlow):
    """Carries the dated stops an interactive walk can step to."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="tick", var_prod_default=False)

    def set_flows(self, **kwargs):
        super().set_flows(**kwargs)
        for index, date in enumerate((RRT_HORIZON, 2 * RRT_HORIZON)):
            self.add_atm2states(
                name=f"clock_{index}",
                st1="s0",
                st2="s1",
                occ_law_12={"cls": "delay", "time": date},
                cond_occ_21=False,
            )


def feed(system, splitter_cls, name, produced="x"):
    """Two sources, the splitter under test, and two metered consumers.

    ``produced`` is the output the reacting leg ends on: ``x`` for the ruled
    splitter, ``a`` for the rule-less reference, which transfers name for name.
    """
    system.add_component(name="SA", cls="SourceContinuous", flow="a", rate=RRT_A_RATE)
    system.add_component(name="SB", cls="SourceContinuous", flow="b", rate=RRT_B_RATE)
    system.add_component(name=name, cls=splitter_cls)
    system.add_component(
        name="KX", cls="ConsumerContinuous", flow=produced, demand=RRT_X_DEMAND
    )
    system.add_component(
        name="KB", cls="ConsumerContinuous", flow="b", demand=RRT_B_DEMAND
    )
    system.add_component(name="CLK", cls="RrtClock")

    system.connect_flow(source="SA", target=name, flow_name="a")
    system.connect_flow(source="SB", target=name, flow_name="b")
    system.connect_flow(source=name, target="KX", flow_name=produced)
    system.connect_flow(source=name, target="KB", flow_name="b")


def snapshot(system, name, produced="x"):
    """What the splitter draws, asks for and emits, at the current instant."""
    comp = system.comp[name]

    return {
        "time": system.currentTime(),
        "a_in": comp.flows_in["a"].get_delivered(),
        "b_in": comp.flows_in["b"].get_delivered(),
        "b_demand": comp.flows_in["b"].var_demand.value(),
        "b_out": comp.flows_out["b"].var_fed.value(),
        "x_out": comp.flows_out[produced].var_fed.value(),
        "kb_in": system.comp["KB"].flows_in["b"].get_delivered(),
    }


def walk(system, name, horizon, produced="x", limit=20):
    """Step to ``horizon``, recording a snapshot at every stop."""
    trace = [snapshot(system, name, produced)]

    for _ in range(limit):
        if system.currentTime() >= horizon:
            break
        system.isimu_step_forward()
        trace.append(snapshot(system, name, produced))

    return trace


def settled(trace):
    """The stops at which the sweeps have run.

    A PDMP equation is not evaluated at ``t = 0``, so the first entry of every
    trace reports declared defaults rather than a settled flow network.
    """
    return [entry for entry in trace if entry["time"] > 0.0]


@pytest.fixture(scope="module")
def the_run():
    """Every scenario, built, driven and deleted in turn."""
    obs = {}

    # -- The splitter carrying a rule ------------------------------------
    rsys = muscadet.System(name="RulesResidueRuled")
    feed(rsys, "RrtSplitter", "SPL")
    rsys.isimu_start()
    obs["ruled"] = walk(rsys, "SPL", RRT_HORIZON)
    rsys.isimu_stop()
    rsys.deleteSys()

    # -- The same flows with no rule at all, as the reference -------------
    psys = muscadet.System(name="RulesResiduePlain")
    feed(psys, "RrtPlainSplitter", "SPL", produced="a")
    psys.isimu_start()
    obs["plain"] = walk(psys, "SPL", RRT_HORIZON, produced="a")
    psys.isimu_stop()
    psys.deleteSys()

    # -- A hole in the residue: it must be reported, not emptied ----------
    hsys = muscadet.System(name="RulesResidueHole")
    feed(hsys, "RrtHole", "HOLE")
    hsys.add_component(name="SC", cls="SourceContinuous", flow="c", rate=1.0)
    hsys.connect_flow(source="SC", target="HOLE", flow_name="c")

    obs["hole_error"] = None
    try:
        hsys.comp["HOLE"].get_identity_transfer_flows()
    except ValueError as err:
        obs["hole_error"] = err

    obs["hole_run_error"] = None
    try:
        hsys.isimu_start()
        hsys.isimu_step_forward()
    except Exception as err:
        obs["hole_run_error"] = err
    hsys.isimu_stop()
    hsys.deleteSys()

    # -- A one-sided residue: a sink, and it must keep building ------------
    ssys = muscadet.System(name="RulesResidueSink")
    ssys.add_component(name="SA", cls="SourceContinuous", flow="a", rate=RRT_A_RATE)
    ssys.add_component(name="SS", cls="SourceContinuous", flow="spare", rate=RRT_B_RATE)
    ssys.add_component(name="SNK", cls="RrtSink")
    ssys.add_component(
        name="KX", cls="ConsumerContinuous", flow="x", demand=RRT_X_DEMAND
    )
    ssys.add_component(name="CLK", cls="RrtClock")
    ssys.connect_flow(source="SA", target="SNK", flow_name="a")
    ssys.connect_flow(source="SS", target="SNK", flow_name="spare")
    ssys.connect_flow(source="SNK", target="KX", flow_name="x")

    obs["sink_transfer"] = ssys.comp["SNK"].get_identity_transfer_flows()

    obs["sink_error"] = None
    try:
        ssys.isimu_start()
        ssys.isimu_step_forward()
    except Exception as err:  # pragma: no cover - a failure is the assertion
        obs["sink_error"] = err

    obs["sink_x"] = ssys.comp["SNK"].flows_out["x"].var_fed.value()
    obs["sink_spare"] = ssys.comp["SNK"].flows_in["spare"].get_delivered()

    obs["system"] = ssys
    return obs


# ----------------------------------------------------------------------
# The untouched flow crosses the component
# ----------------------------------------------------------------------


def test_a_flow_no_rule_names_still_crosses_the_component(the_run):
    """The defect: ``b`` was asked for at 0.0 and emitted at 0.0."""
    stops = settled(the_run["ruled"])

    assert stops, "the walk must reach a stop where the sweeps have run"

    for stop in stops:
        assert stop["b_demand"] == pytest.approx(RRT_B_DEMAND), (
            "the untouched flow must publish the demand its consumer carries, "
            f"not {stop['b_demand']:g}"
        )
        assert stop["b_in"] == pytest.approx(RRT_B_DEMAND)
        assert stop["b_out"] == pytest.approx(RRT_B_DEMAND)
        assert stop["kb_in"] == pytest.approx(RRT_B_DEMAND)


def test_the_rule_itself_is_unaffected(the_run):
    """The transformed leg runs exactly as it did: supply-limited at 1.0."""
    for stop in settled(the_run["ruled"]):
        assert stop["a_in"] == pytest.approx(RRT_A_RATE)
        assert stop["x_out"] == pytest.approx(RRT_A_RATE)


def test_the_rule_changes_nothing_about_the_untouched_flow(the_run):
    """The same component without the rule reports the same ``b``.

    This is the property the defect broke: a rule is a statement about the
    flows it names.
    """
    ruled = settled(the_run["ruled"])
    plain = settled(the_run["plain"])

    assert ruled and plain

    assert ruled[-1]["b_in"] == pytest.approx(plain[-1]["b_in"])
    assert ruled[-1]["b_out"] == pytest.approx(plain[-1]["b_out"])
    assert ruled[-1]["b_demand"] == pytest.approx(plain[-1]["b_demand"])


def test_conservation_holds_on_the_splitter_at_every_stop(the_run):
    """Per component, per stop: what is drawn is what is consumed or carried.

    ``a`` is consumed by the rule at the scale its output reports; ``b`` is
    carried unchanged. No capacity buffers either, so there is no storage term
    and the books must close exactly.
    """
    for stop in settled(the_run["ruled"]):
        assert stop["a_in"] == pytest.approx(stop["x_out"], abs=1e-6), (
            f"t={stop['time']:g}: a draws {stop['a_in']:g} against a "
            f"production of {stop['x_out']:g}"
        )
        assert stop["b_in"] == pytest.approx(stop["b_out"], abs=1e-6), (
            f"t={stop['time']:g}: b draws {stop['b_in']:g} and carries "
            f"{stop['b_out']:g}"
        )


# ----------------------------------------------------------------------
# The mismatch check applies to the residue too
# ----------------------------------------------------------------------


def test_a_wired_flow_no_rule_and_no_counterpart_covers_raises(the_run):
    """``c`` arrives and vanishes: reported, rather than silently emptied.

    The check lived in the branch a rule set disabled, so this model built and
    ran with ``c`` drawn and destroyed.
    """
    error = the_run["hole_error"]

    assert error is not None, "a quantity arriving nowhere must not pass silently"
    assert isinstance(error, ValueError)

    message = str(error)
    assert "HOLE" in message
    assert "input flow c" in message
    assert "add_rules" in message

    # The flows the model does account for are not what is complained about.
    assert "input flow a" not in message
    assert "input flow b" not in message


def test_that_mismatch_is_a_model_error_at_run_time(the_run):
    """The transfer is evaluated by the solver, so the run is what refuses it."""
    error = the_run["hole_run_error"]

    assert error is not None, "the run must not proceed on a broken transfer"
    assert isinstance(error, ValueError)
    assert "input flow c" in str(error)


def test_a_one_sided_residue_transfers_nothing_and_raises_nothing(the_run):
    """An unmatched input beside a rule is a sink, exactly as it always was.

    Refusing this would outlaw every component carrying an input its rules
    consume only under a mode it has not reached.
    """
    assert the_run["sink_error"] is None, str(the_run["sink_error"])
    assert the_run["sink_transfer"] == []
    assert the_run["sink_x"] == pytest.approx(RRT_A_RATE)


def test_delete(the_run):
    the_run["system"].isimu_stop()
    the_run["system"].deleteSys()
    cod3s.terminate_session()
