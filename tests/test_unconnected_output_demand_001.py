"""An unconnected continuous output constrains nothing (R-10).

``get_demand_scale`` sizes the scale a rule must run at from its outputs, over
what each of them is asked for divided by its coefficient. An output nobody is
connected to is asked for nothing, and ``FlowContinuousOut.get_demand_bound``
reports that as ``inf`` -- "nobody is throttling me". The scale was a **maximum**
when this module was written, and fed into one that single unwired output
out-voted every connected one: the component claimed its whole upstream supply,
a vent or a model still being assembled silently over-drawing a shared source
with no diagnostic at all.

The scale is a **minimum** since 3.0.0
(``tests/test_demand_scale_minimum_001.py``), and everything below holds
unchanged, but not for a symmetric reason and the difference is easy to get
wrong. An unwired output publishes ``inf``, and an ``inf`` never wins a
minimum: dropping it is no longer what saves a rule that has one connected
output left. What the filter still carries is the **third** case below, where
every output is unwired: a minimum of infinities would make the rule draw its
whole supply, and only the filter routes it to the nominal fallback instead.
Measured with the filter disabled, on the LONE_T model of this module: 100.0
drawn instead of 1.0.

Three cases have to stay apart, and this module pins all three:

* an output **nothing is connected to** constrains nothing and is dropped from
  the scale -- it is a legitimate model (a vent), so it is neither refused nor
  made to demand;
* an output connected to a consumer **currently demanding zero** still
  constrains, at scale zero. "Nobody is asking" and "somebody is asking for
  nothing" are different models and must not collapse into one;
* a rule whose outputs are **all** unconnected falls back to the nominal
  scale, which is the ``not scales`` branch it now reaches.

And the test is **structural**, never a test on the demand's value: a demand of
``inf`` published by a real consumer -- a capacity claiming its fill rate, a
downstream itself unconstrained -- still travels as the "deliver whatever you
can" it means. The ``0 * inf`` NaN trap a catalyst coefficient of 0 has to
survive therefore still exists, and is exercised here against such a consumer.

PyCATSHOO forbids more than one live system per process, so every scenario
lives in the one system below, driven through a single interactive session.
"""

import math

import muscadet
import cod3s
import pytest

#: A date the interactive session can always step to, so the solver integrates.
UCD_CLOCK = 5.0

#: What the shared source can supply, and what each of its two consumers wants.
UCD_SHARED_SUPPLY = 10.0
UCD_FAIR_SHARE = 5.0


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class UcdSource(muscadet.ObjFlow):
    """A continuous producer holding the rate it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name=kwargs.get("flow", "feed"), var_fed_default=kwargs.get("rate", 10.0)
        )


class UcdConsumer(muscadet.ObjFlow):
    """A pure consumer publishing the demand it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "good"),
            var_demand_default=kwargs.get("demand", 0.0),
        )


class UcdVented(muscadet.ObjFlow):
    """One input, two outputs -- and one of them is deliberately unwired.

    The footgun in one component: ``good`` is consumed downstream, ``vent`` is
    a modelled discharge nobody is connected to. The scale that serves ``good``
    is the only one this rule is constrained by.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="feed")
        self.add_flow_continuous_out(name="good")
        self.add_flow_continuous_out(name="vent")
        self.add_rules(
            name="split",
            rules=[dict(cons={"feed": 1}, prod={"good": 1, "vent": 1})],
        )


class UcdTransformer(muscadet.ObjFlow):
    """2 of ``q`` make 1 of ``x``: a rule with one output and nothing else."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="x")
        self.add_rules(name="make", rules=[dict(cons={"q": 2}, prod={"x": 1})])


class UcdCatalysed(muscadet.ObjFlow):
    """The catalyst idiom: an input the rule NAMES but consumes none of.

    ``muscadet.rules.rule_scale`` skips a non-positive coefficient and the
    demand half has to skip it identically, or ``0 * inf`` publishes NaN
    upstream. The trap needs an unbounded scale to spring, which since R-10
    only a CONNECTED consumer asking without bound can produce.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="cat")
        self.add_flow_continuous_in(name="fuel")
        self.add_flow_continuous_out(name="burn")
        self.add_rules(
            name="react",
            rules=[dict(cons={"cat": 0.0, "fuel": 1.0}, prod={"burn": 1.0})],
        )


class UcdSignaller(muscadet.ObjFlow):
    """A rule PRODUCING a discrete output, which carries no demand channel.

    ``alarm`` is named in the ``prod`` map beside the continuous ``x``. A
    boolean production is not a quantity: nothing downstream can ask for a
    quantity of it, so it sizes nothing and only ``x`` constrains the rule.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_out(name="alarm", var_prod_default=True)
        self.add_flow_continuous_out(name="x")
        self.add_rules(
            name="watch",
            rules=[dict(cons={"q": 1}, prod={"x": 1, "alarm": 1})],
        )


# ----------------------------------------------------------------------
# The one system every scenario lives in
# ----------------------------------------------------------------------


def add_clock(comp):
    """Give the interactive session a date it can always step to."""
    comp.add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": UCD_CLOCK},
        cond_occ_21=False,
    )


def build_system():
    system = muscadet.System(name="UcdSys")

    # -- The footgun: one shared source, a vented transformer and a rival.
    #    Both want 5 of the 10 available, so a correct model splits it evenly.
    system.add_component(
        name="SHARED", cls="UcdSource", flow="feed", rate=UCD_SHARED_SUPPLY
    )
    system.add_component(name="VENTED", cls="UcdVented")
    system.add_component(
        name="VENT_C", cls="UcdConsumer", flow="good", demand=UCD_FAIR_SHARE
    )
    system.add_component(
        name="RIVAL", cls="UcdConsumer", flow="feed", demand=UCD_FAIR_SHARE
    )
    system.connect_flow(source="SHARED", target="VENTED", flow_name="feed")
    system.connect_flow(source="SHARED", target="RIVAL", flow_name="feed")
    system.connect_flow(source="VENTED", target="VENT_C", flow_name="good")

    # -- A consumer connected and asking for NOTHING: it constrains at 0.
    system.add_component(name="ZERO_SRC", cls="UcdSource", flow="q", rate=10.0)
    system.add_component(name="ZERO_T", cls="UcdTransformer")
    system.add_component(name="ZERO_C", cls="UcdConsumer", flow="x", demand=0.0)
    system.connect_flow(source="ZERO_SRC", target="ZERO_T", flow_name="q")
    system.connect_flow(source="ZERO_T", target="ZERO_C", flow_name="x")

    # -- The same transformer with its only output unwired: nominal scale.
    system.add_component(name="LONE_SRC", cls="UcdSource", flow="q", rate=10.0)
    system.add_component(name="LONE_T", cls="UcdTransformer")
    system.connect_flow(source="LONE_SRC", target="LONE_T", flow_name="q")

    # -- A connected consumer asking without bound: the demand still travels.
    system.add_component(name="OPEN_SRC", cls="UcdSource", flow="q", rate=10.0)
    system.add_component(name="OPEN_T", cls="UcdTransformer")
    system.add_component(name="OPEN_C", cls="UcdConsumer", flow="x", demand=math.inf)
    system.connect_flow(source="OPEN_SRC", target="OPEN_T", flow_name="q")
    system.connect_flow(source="OPEN_T", target="OPEN_C", flow_name="x")

    # -- The catalyst, under that same unbounded-but-connected demand.
    system.add_component(name="CAT_SRC", cls="UcdSource", flow="cat", rate=10.0)
    system.add_component(name="FUEL_SRC", cls="UcdSource", flow="fuel", rate=10.0)
    system.add_component(name="CATU", cls="UcdCatalysed")
    system.add_component(name="CAT_C", cls="UcdConsumer", flow="burn", demand=math.inf)
    system.connect_flow(source="CAT_SRC", target="CATU", flow_name="cat")
    system.connect_flow(source="FUEL_SRC", target="CATU", flow_name="fuel")
    system.connect_flow(source="CATU", target="CAT_C", flow_name="burn")

    # -- A rule producing a discrete output beside a connected continuous one.
    system.add_component(name="SIG_SRC", cls="UcdSource", flow="q", rate=10.0)
    system.add_component(name="SIGNAL", cls="UcdSignaller")
    system.add_component(name="SIG_C", cls="UcdConsumer", flow="x", demand=3.0)
    system.connect_flow(source="SIG_SRC", target="SIGNAL", flow_name="q")
    system.connect_flow(source="SIGNAL", target="SIG_C", flow_name="x")

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
def the_run():
    """Drive the system one step and snapshot every reading."""
    system = build_system()

    system.isimu_start()
    system.isimu_step_forward()

    obs = {"time": system.currentTime()}

    obs["shared"] = {
        "vented_demand": published_demand(system, "VENTED", "feed"),
        "vented_received": delivered(system, "VENTED", "feed"),
        "rival_received": delivered(system, "RIVAL", "feed"),
        "allocated": dict(system.comp["SHARED"].flows_out["feed"].allocated),
        "good": out_value(system, "VENTED", "good"),
        "vent": out_value(system, "VENTED", "vent"),
    }

    obs["zero"] = {
        "demand": published_demand(system, "ZERO_T", "q"),
        "received": delivered(system, "ZERO_T", "q"),
        "x": out_value(system, "ZERO_T", "x"),
    }

    obs["lone"] = {
        "demand": published_demand(system, "LONE_T", "q"),
        "received": delivered(system, "LONE_T", "q"),
        "x": out_value(system, "LONE_T", "x"),
    }

    obs["open"] = {
        "demand": published_demand(system, "OPEN_T", "q"),
        "received": delivered(system, "OPEN_T", "q"),
        "x": out_value(system, "OPEN_T", "x"),
    }

    obs["catalyst"] = {
        "demand_cat": published_demand(system, "CATU", "cat"),
        "demand_fuel": published_demand(system, "CATU", "fuel"),
        "delivered_cat": delivered(system, "CATU", "cat"),
        "delivered_fuel": delivered(system, "CATU", "fuel"),
        "burn": out_value(system, "CATU", "burn"),
    }

    obs["signal"] = {
        "demand": published_demand(system, "SIGNAL", "q"),
        "x": out_value(system, "SIGNAL", "x"),
        "alarm": system.comp["SIGNAL"].flows_out["alarm"].var_fed.value(),
    }

    # The predicate itself, read on the live model
    vented = system.comp["VENTED"]
    signaller = system.comp["SIGNAL"]
    obs["predicate"] = {
        "connected_continuous": vented.output_constrains_demand("good"),
        "unconnected_continuous": vented.output_constrains_demand("vent"),
        "discrete": signaller.output_constrains_demand("alarm"),
        "unknown": vented.output_constrains_demand("nope"),
    }

    system.isimu_stop()

    obs["system"] = system
    return obs


# ----------------------------------------------------------------------
# The footgun: an unwired output must not out-vote a connected one
# ----------------------------------------------------------------------


def test_an_unwired_output_does_not_make_the_component_demand_without_bound(the_run):
    """R-10: ``vent`` is unwired, so only ``good`` sizes the rule.

    Against ``dabc2b1`` the unwired ``vent`` contributed ``inf`` to the maximum
    and VENTED published an unbounded demand upstream.
    """
    shared = the_run["shared"]

    assert not math.isinf(shared["vented_demand"])
    assert shared["vented_demand"] == pytest.approx(UCD_FAIR_SHARE)


def test_the_unwired_output_no_longer_out_draws_a_rival_on_a_shared_supply(the_run):
    """The whole point: 10 available, two consumers wanting 5, and 5 each.

    Against ``dabc2b1`` VENTED's unbounded claim took 6.67 of the 10 under the
    proportional policy and left the rival -- which asked for exactly what it
    needed -- short at 3.33.
    """
    shared = the_run["shared"]

    assert shared["vented_received"] == pytest.approx(UCD_FAIR_SHARE)
    assert shared["rival_received"] == pytest.approx(UCD_FAIR_SHARE)

    allocated = shared["allocated"]
    assert allocated["VENTED"] == pytest.approx(UCD_FAIR_SHARE)
    assert allocated["RIVAL"] == pytest.approx(UCD_FAIR_SHARE)

    # Nothing is created: the two shares total what the source supplies
    assert sum(allocated.values()) == pytest.approx(UCD_SHARED_SUPPLY)


def test_a_vent_is_a_legitimate_model_and_still_carries_what_is_produced(the_run):
    """An unwired output is not refused, and not silenced either.

    It constrains nothing, which is a statement about DEMAND. The rule still
    produces on it at the scale its connected output sets, and the value is
    still published -- a vent discharges what the reaction makes.
    """
    shared = the_run["shared"]

    assert shared["good"] == pytest.approx(UCD_FAIR_SHARE)
    assert shared["vent"] == pytest.approx(UCD_FAIR_SHARE)


# ----------------------------------------------------------------------
# The three cases that must stay apart
# ----------------------------------------------------------------------


def test_a_consumer_asking_for_nothing_still_constrains_at_scale_zero(the_run):
    """ "Nobody is asking" and "somebody is asking for nothing" differ.

    Collapsing the two -- dropping a zero demand from the scale the way an
    absent one is dropped -- would let a component asked for nothing run at its
    nominal scale and draw upstream for a production nobody wants. True of the
    maximum this was written against and of the minimum that replaced it: the
    ``not scales`` fallback is reached either way.
    """
    zero = the_run["zero"]

    assert zero["demand"] == pytest.approx(0.0)
    assert zero["received"] == pytest.approx(0.0)
    assert zero["x"] == pytest.approx(0.0)


def test_a_rule_whose_outputs_are_all_unwired_falls_back_to_the_nominal_scale(the_run):
    """Nothing constrains the rule at all, so it runs at scale 1.

    ``LONE_T`` turns 2 of ``q`` into 1 of ``x`` and its only output is unwired.
    It claims its declared coefficient -- 2 -- and no more, out of a supply of
    10. Against ``dabc2b1`` it claimed without bound and drew the whole 10.
    """
    lone = the_run["lone"]

    assert not math.isinf(lone["demand"])
    assert lone["demand"] == pytest.approx(2.0)
    assert lone["received"] == pytest.approx(2.0)
    assert lone["x"] == pytest.approx(1.0)


def test_an_unbounded_demand_from_a_connected_consumer_still_travels(the_run):
    """The test is structural: it never reads the demand's value.

    ``OPEN_C`` is connected and asks without bound -- what a capacity claiming
    its fill rate publishes, and what an unconstrained downstream publishes.
    Its producer must stay free to run at whatever its inputs allow, so the
    whole supply of 10 is drawn and 5 of ``x`` come out.
    """
    open_case = the_run["open"]

    assert math.isinf(open_case["demand"])
    assert open_case["received"] == pytest.approx(10.0)
    assert open_case["x"] == pytest.approx(5.0)


# ----------------------------------------------------------------------
# What the filter must not break
# ----------------------------------------------------------------------


def test_a_catalyst_coefficient_publishes_no_nan_under_an_unbounded_scale(the_run):
    """``0 * inf`` is NaN, and NaN poisons every split it reaches.

    The trap needs an unbounded scale, which since R-10 only a connected
    consumer asking without bound produces -- ``CAT_C`` here. This is the
    coverage ``test_demand_allocation_001`` used to get from an unwired output.
    """
    catalyst = the_run["catalyst"]

    assert math.isinf(catalyst["demand_fuel"]), "the scale must really be unbounded"

    assert catalyst["demand_cat"] == pytest.approx(0.0)
    assert catalyst["demand_cat"] == catalyst["demand_cat"], "NaN published upstream"
    assert catalyst["delivered_cat"] == pytest.approx(0.0)

    # ... and the catalyst limits the rule no more than it is drawn down
    assert catalyst["delivered_fuel"] == pytest.approx(10.0)
    assert catalyst["burn"] == pytest.approx(10.0)


def test_a_discrete_output_carries_no_demand_and_sizes_nothing(the_run):
    """A boolean production is not a quantity, so it throttles nothing.

    ``SIGNAL`` produces a discrete ``alarm`` beside a continuous ``x`` wired to
    a consumer asking for 3. The alarm has no demand channel to report from, so
    the continuous output is what sizes the rule. Against ``dabc2b1`` the
    discrete output contributed ``inf`` to the maximum and the component drew
    its whole supply of 10.
    """
    signal = the_run["signal"]

    assert not math.isinf(signal["demand"])
    assert signal["demand"] == pytest.approx(3.0)
    assert signal["x"] == pytest.approx(3.0)

    # The discrete output is untouched by any of this
    assert signal["alarm"] is True


def test_the_predicate_reports_what_can_constrain_a_rule(the_run):
    """``output_constrains_demand`` is structural, and total.

    True only for a continuous output somebody is connected to. A discrete
    output, an unconnected continuous one and a name that is no output at all
    each constrain nothing.
    """
    predicate = the_run["predicate"]

    assert predicate["connected_continuous"] is True
    assert predicate["unconnected_continuous"] is False
    assert predicate["discrete"] is False
    assert predicate["unknown"] is False


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
