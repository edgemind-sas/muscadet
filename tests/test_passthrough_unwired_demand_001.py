"""A rule-less pass-through with an unwired output asks for nothing.

R-10 stopped an unconnected output from making a RULE claim without bound, but
the rule-less identity transfer of R31 kept carrying its output's demand across
unchanged -- and an output nobody is connected to reports that demand as
``inf``. So a plain pipe whose far end was not wired still competed for the
whole upstream supply, and starved the consumers that were wired: the same
footgun, on the one path R-10's filter did not reach.

The choice recorded here is that an unwired output constrains nothing on BOTH
paths. It is a consistency argument rather than a physical one. Read as
physics, an open pipe end is a discharge to atmosphere and demanding without
bound is faithful -- but a transfer declares no coefficients, so it has no
nominal scale to fall back to the way a rule does, and leaving it unbounded
would mean the same physical arrangement answers differently depending on
whether the modeller happened to write a rule. A deliberate vent stays
modellable, and more legibly, by declaring the discharge as a consumer with its
own demand.

Worth knowing why the unbounded reading could not simply be kept as it stood:
it was not implemented either. ``regularize_demands`` rewrites ``inf`` as the
whole quantity available before any split, so a dangling pipe was treated as
having asked for exactly the supply -- 6.67 against a rival asking 5 out of 10,
where a true unbounded demand would have taken all 10. Two dangling pipes drove
that rival down to 2.0. The share a real consumer received depended on how many
unconnected outputs existed elsewhere in the model.

PyCATSHOO forbids more than one live system per process, so every scenario
lives in the one system below.
"""

import math

import muscadet
import cod3s
import pytest

#: A date the interactive session can always step to, so the solver integrates.
PTU_CLOCK = 5.0

#: What the shared source supplies, and what the wired rival asks of it.
PTU_SUPPLY = 10.0
PTU_RIVAL_DEMAND = 5.0

#: Declared on every pipe's input, and never what a pipe should publish. A
#: transferable input that fell out of the demand map would pick this up as its
#: default, turning "asks for nothing" into "asks for its declared default".
PTU_INPUT_DEFAULT = 7.0


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class PtuSource(muscadet.ObjFlow):
    """A continuous producer holding the rate it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name=kwargs.get("flow", "feed"),
            var_fed_default=kwargs.get("rate", PTU_SUPPLY),
        )


class PtuConsumer(muscadet.ObjFlow):
    """A pure consumer publishing the demand it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "feed"),
            var_demand_default=kwargs.get("demand", 0.0),
        )


class PtuPipe(muscadet.ObjFlow):
    """One continuous flow in, the same one out, and no rule at all.

    The R31 identity transfer. Its input carries a non-zero declared demand on
    purpose: a pipe must publish what its OUTPUT is asked for, never that
    default, whichever way the output answers.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="feed", var_demand_default=PTU_INPUT_DEFAULT)
        self.add_flow_continuous_out(name="feed")


# ----------------------------------------------------------------------
# The one system every scenario lives in
# ----------------------------------------------------------------------


def add_clock(comp):
    """Give the interactive session a date it can always step to."""
    comp.add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": PTU_CLOCK},
        cond_occ_21=False,
    )


def build_system():
    system = muscadet.System(name="PtuSys")

    # -- The footgun: a dangling pipe and a wired rival on one source. The
    #    rival asks 5 of the 10 available and must get all 5 of them.
    system.add_component(name="SHARED", cls="PtuSource", rate=PTU_SUPPLY)
    system.add_component(name="DANGLING", cls="PtuPipe")
    system.add_component(name="RIVAL", cls="PtuConsumer", demand=PTU_RIVAL_DEMAND)
    system.connect_flow(source="SHARED", target="DANGLING", flow_name="feed")
    system.connect_flow(source="SHARED", target="RIVAL", flow_name="feed")

    # -- The working case, unchanged: a pipe whose output IS wired carries its
    #    consumer's demand across and passes the quantity through.
    system.add_component(name="WIRED_SRC", cls="PtuSource", rate=PTU_SUPPLY)
    system.add_component(name="WIRED", cls="PtuPipe")
    system.add_component(name="WIRED_C", cls="PtuConsumer", demand=4.0)
    system.connect_flow(source="WIRED_SRC", target="WIRED", flow_name="feed")
    system.connect_flow(source="WIRED", target="WIRED_C", flow_name="feed")

    # -- And a pipe onto a consumer asking without bound: the filter is
    #    structural, so an inf a real consumer published still travels.
    system.add_component(name="OPEN_SRC", cls="PtuSource", rate=PTU_SUPPLY)
    system.add_component(name="OPEN", cls="PtuPipe")
    system.add_component(name="OPEN_C", cls="PtuConsumer", demand=math.inf)
    system.connect_flow(source="OPEN_SRC", target="OPEN", flow_name="feed")
    system.connect_flow(source="OPEN", target="OPEN_C", flow_name="feed")

    add_clock(system.comp["SHARED"])

    return system


def published_demand(system, comp_name, flow_name):
    """What a component currently publishes upstream on one of its inputs."""
    return system.comp[comp_name].flows_in[flow_name].var_demand.value()


def delivered(system, comp_name, flow_name):
    """What one of a component's inputs currently receives."""
    return system.comp[comp_name].flows_in[flow_name].get_delivered()


def out_value(system, comp_name, flow_name):
    """What a component currently delivers on one of its outputs."""
    return system.comp[comp_name].flows_out[flow_name].var_fed.value()


@pytest.fixture(scope="module")
def the_system():
    system = build_system()
    system.isimu_start()
    system.isimu_step_forward()
    yield system


@pytest.fixture(scope="module")
def the_run(the_system):
    """Snapshot every reading once the system has been driven one step."""
    return {
        "dangling": {
            "demand": published_demand(the_system, "DANGLING", "feed"),
            "received": delivered(the_system, "DANGLING", "feed"),
            "out": out_value(the_system, "DANGLING", "feed"),
        },
        "rival": {
            "received": delivered(the_system, "RIVAL", "feed"),
        },
        "wired": {
            "demand": published_demand(the_system, "WIRED", "feed"),
            "received": delivered(the_system, "WIRED", "feed"),
            "out": out_value(the_system, "WIRED", "feed"),
            "consumer": delivered(the_system, "WIRED_C", "feed"),
        },
        "open": {
            "demand": published_demand(the_system, "OPEN", "feed"),
            "received": delivered(the_system, "OPEN", "feed"),
            "consumer": delivered(the_system, "OPEN_C", "feed"),
        },
    }


# ----------------------------------------------------------------------
# The footgun
# ----------------------------------------------------------------------


def test_a_dangling_pipe_publishes_no_demand_at_all(the_run):
    """The heart of it: nothing consumes the pipe, so the pipe asks nothing.

    Not merely "bounded" -- exactly zero. A pipe reduced to some finite figure
    would still take a share of the supply away from the rival.
    """
    assert not math.isinf(the_run["dangling"]["demand"])
    assert the_run["dangling"]["demand"] == pytest.approx(0.0)


def test_a_dangling_pipe_does_not_publish_its_inputs_declared_default(the_run):
    """Zero is published deliberately, not by the input dropping out of the map.

    A transferable flow left out of the demand map falls through to the
    ``var_demand_default`` of a pure consumer's input. Here that default is 7,
    so the difference between "accumulated as zero" and "skipped" is visible.
    """
    assert the_run["dangling"]["demand"] != pytest.approx(PTU_INPUT_DEFAULT)
    assert the_run["dangling"]["demand"] == pytest.approx(0.0)


def test_the_wired_rival_receives_everything_it_asked_for(the_run):
    """The consequence that matters to a modeller: no silent starving.

    Before, the dangling pipe was regularised to the whole supply and out-voted
    the rival two to one, leaving it 3.33 of the 5 it asked for.
    """
    assert the_run["rival"]["received"] == pytest.approx(PTU_RIVAL_DEMAND)


def test_a_dangling_pipe_draws_and_carries_nothing(the_run):
    """Asking for nothing, it receives nothing and transfers nothing."""
    assert the_run["dangling"]["received"] == pytest.approx(0.0)
    assert the_run["dangling"]["out"] == pytest.approx(0.0)


# ----------------------------------------------------------------------
# What must NOT have changed
# ----------------------------------------------------------------------


def test_a_wired_pipe_still_carries_its_consumers_demand_across(the_run):
    """The identity transfer itself is untouched on the path that has a consumer."""
    assert the_run["wired"]["demand"] == pytest.approx(4.0)
    assert the_run["wired"]["received"] == pytest.approx(4.0)
    assert the_run["wired"]["out"] == pytest.approx(4.0)
    assert the_run["wired"]["consumer"] == pytest.approx(4.0)


def test_an_unbounded_demand_from_a_real_consumer_still_travels(the_run):
    """The filter is structural: it asks whether anything is connected, never
    what the demand's value is. An ``inf`` a consumer genuinely published keeps
    meaning "deliver whatever you can"."""
    assert math.isinf(the_run["open"]["demand"])
    assert the_run["open"]["received"] == pytest.approx(PTU_SUPPLY)
    assert the_run["open"]["consumer"] == pytest.approx(PTU_SUPPLY)


def test_the_two_paths_now_agree_on_an_unwired_output(the_system):
    """The consistency this change exists for, read on the live model.

    ``output_constrains_demand`` is the one predicate both the rule scale and
    the transfer consult, so a rule-bearing component and a rule-less one
    cannot drift apart on what an unwired output means.
    """
    dangling = the_system.comp["DANGLING"]
    wired = the_system.comp["WIRED"]

    assert dangling.output_constrains_demand("feed") is False
    assert wired.output_constrains_demand("feed") is True


# ----------------------------------------------------------------------


def test_delete(the_system):
    the_system.isimu_stop()
    the_system.deleteSys()
    cod3s.terminate_session()
