"""Rule evaluation: the limiting reagent, and the transfer when there is no rule.

The active rule produces every ``prod`` entry at the scale its scarcest ``cons``
input allows (R15). The scale is computed ONCE per rule, which is what keeps a
reaction's correlated outputs in their declared proportion -- state it one output
at a time and the two outputs of an electrolysis would each get their own,
possibly different, limiting-reagent computation.

Three further properties are pinned here:

* a component declaring continuous flows and no rule transfers each input to the
  output of the same name (R31, AE9), and a wired flow with no counterpart is a
  model error naming the component and the flow;
* an interposed capacity replaces the flow it buffers as the counterparty of the
  rules (KTD13): the rules draw from what an input capacity can serve, and
  production enters an output capacity that the output flow then draws from;
* the equation is registered by name from the connection graph -- no model, and
  no component below, ever writes an evaluation order down (R8).

Production is only evaluated while the solver integrates, so every value below
is read after one interactive step. PyCATSHOO forbids more than one live system
per process, so each scenario is built, driven and deleted before the next one
starts; the fixture snapshots what each produced.
"""

import muscadet
import cod3s
import pytest

from muscadet import ordering, rules

#: A date the interactive session can always step to, so the solver integrates.
CLOCK_DELAY = 5.0


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class RateSource(muscadet.ObjFlow):
    """A continuous producer holding the rates it was declared with.

    Carries continuous flows on a single side, so it transfers nothing and its
    outputs simply hold their declared rate.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for name, rate in kwargs.get("rates", {}).items():
            self.add_flow_continuous_out(name=name, var_fed_default=rate)


class RateSink(muscadet.ObjFlow):
    """A continuous consumer, declared so an output can be wired to something.

    ``demand`` is what it asks for on every flow it takes. A sink declared
    with a large one is a downstream that takes whatever is produced: before
    R-10 an unwired output said that implicitly by demanding without bound,
    and since R-10 an unwired output constrains nothing and the producing rule
    falls back to its nominal scale, so a scenario about the scale an ABUNDANT
    input sets has to state its downstream.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for name in kwargs.get("takes", []):
            self.add_flow_continuous_in(
                name=name, var_demand_default=kwargs.get("demand", 0.0)
            )


class Electrolysis(muscadet.ObjFlow):
    """AE5: 10 of ``H2O`` and 50 of ``elec`` produce 5 of ``H2`` and 2 of ``O2``."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="H2O")
        self.add_flow_continuous_in(name="elec")
        self.add_flow_continuous_out(name="H2")
        self.add_flow_continuous_out(name="O2")
        self.add_rules(
            name="electrolysis",
            rules=[dict(cons={"H2O": 10, "elec": 50}, prod={"H2": 5, "O2": 2})],
        )


class BufferedElectrolysis(Electrolysis):
    """The same reaction, drawing its ``H2O`` from a tank instead (KTD13)."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_capacity(
            name="tank",
            flow="H2O",
            capacity=200,
            side="in",
            content_init={"H2O": 100},
        )


class Bottler(muscadet.ObjFlow):
    """2 of ``H2O`` make 1 of ``H2``, produced into a bottle downstream (KTD13)."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="H2O")
        self.add_flow_continuous_out(name="H2")
        self.add_rules(name="split", rules=[dict(cons={"H2O": 2}, prod={"H2": 1})])
        self.add_capacity(name="bottle", flow="H2", capacity=100, side="out")


class Boiler(muscadet.ObjFlow):
    """A rule with an empty ``cons`` map: it produces its declared quantity."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="steam")
        self.add_rules(name="boil", rules=[dict(prod={"steam": 4})])


class Pipe(muscadet.ObjFlow):
    """AE9: one continuous input, one continuous output of the same name."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="q")


class LostInput(Pipe):
    """A rule-less transfer whose ``extra`` input has no output to go to."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="extra")


class SourcelessOutput(Pipe):
    """A rule-less transfer whose ``spare`` output has no input feeding it."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="spare")


# ----------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------


def add_clock(comp):
    """Give the interactive session a date to step to."""
    comp.add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": CLOCK_DELAY},
        cond_occ_21=False,
    )


def feed(system, target, flow, rate):
    """Wire a dedicated source delivering ``rate`` of ``flow`` into ``target``."""
    name = f"{target}_{flow}_SRC"
    system.add_component(name=name, cls="RateSource", rates={flow: rate})
    system.connect_flow(source=name, target=target, flow_name=flow)
    return name


def out_value(system, comp, flow):
    """What ``comp`` currently delivers on its output flow ``flow``."""
    return system.comp[comp].flows_out[flow].var_fed.value()


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_lost_input_scenario(obs):
    """R31: an input with no output of the same name, and it IS wired."""
    system = muscadet.System(name="RulesEvalLostInput")

    system.add_component(name="LOST", cls="LostInput")
    feed(system, "LOST", "q", 1.0)
    feed(system, "LOST", "extra", 1.0)
    system.add_component(name="OUT", cls="RateSink", takes=["q"])
    system.connect_flow(source="LOST", target="OUT", flow_name="q")
    add_clock(system.comp["LOST"])

    obs["lost_error"] = None
    try:
        system.comp["LOST"].get_identity_transfer_flows()
    except ValueError as err:
        obs["lost_error"] = err

    # ... and the same error is what a run raises, rather than a silent loss.
    obs["lost_run_error"] = None
    try:
        system.isimu_start()
        system.isimu_step_forward()
    except Exception as err:
        obs["lost_run_error"] = err
    system.isimu_stop()

    system.deleteSys()


def run_sourceless_output_scenario(obs):
    """R31: an output with no input of the same name, and it IS wired."""
    system = muscadet.System(name="RulesEvalSourcelessOutput")

    system.add_component(name="SOURCELESS", cls="SourcelessOutput")
    feed(system, "SOURCELESS", "q", 1.0)
    system.add_component(name="OUT", cls="RateSink", takes=["q", "spare"])
    system.connect_flow(source="SOURCELESS", target="OUT", flow_name="q")
    system.connect_flow(source="SOURCELESS", target="OUT", flow_name="spare")

    obs["sourceless_error"] = None
    try:
        system.comp["SOURCELESS"].get_identity_transfer_flows()
    except ValueError as err:
        obs["sourceless_error"] = err

    system.deleteSys()


def run_unwired_mismatch_scenario(obs):
    """The same shapes, wired to nothing: they exchange nothing, so they pass."""
    system = muscadet.System(name="RulesEvalUnwired")

    system.add_component(name="LOST", cls="LostInput")
    system.add_component(name="SOURCELESS", cls="SourcelessOutput")

    obs["unwired_error"] = None
    try:
        obs["unwired_lost"] = system.comp["LOST"].get_identity_transfer_flows()
        obs["unwired_sourceless"] = system.comp[
            "SOURCELESS"
        ].get_identity_transfer_flows()
    except Exception as err:  # pragma: no cover - a failure is the assertion
        obs["unwired_error"] = err

    # A component carrying continuous flows on a single side transfers nothing
    # and demands no counterpart: it is a source, or a consumer.
    system.add_component(name="SRC", cls="RateSource", rates={"q": 1.0})
    system.add_component(name="SNK", cls="RateSink", takes=["q"])
    system.connect_flow(source="SRC", target="SNK", flow_name="q")
    obs["source_transfer"] = system.comp["SRC"].get_identity_transfer_flows()
    obs["sink_transfer"] = system.comp["SNK"].get_identity_transfer_flows()

    system.deleteSys()


def build_production_system():
    """Every evaluation scenario, in one system driven for a single step."""
    system = muscadet.System(name="RulesEvalProduction")

    # -- Nominal inputs produce the nominal output
    system.add_component(name="NOMINAL", cls="Electrolysis")
    feed(system, "NOMINAL", "H2O", 10.0)
    feed(system, "NOMINAL", "elec", 50.0)

    # -- AE5: half the nominal elec, both outputs scale together
    system.add_component(name="SCARCE", cls="Electrolysis")
    feed(system, "SCARCE", "H2O", 10.0)
    feed(system, "SCARCE", "elec", 25.0)

    # -- A cons entry whose input is absent: elec is wired to nobody
    system.add_component(name="ABSENT", cls="Electrolysis")
    feed(system, "ABSENT", "H2O", 10.0)

    # -- Ten times the H2O, and the scarcest input still sets the scale
    system.add_component(name="EXCESS", cls="Electrolysis")
    feed(system, "EXCESS", "H2O", 100.0)
    feed(system, "EXCESS", "elec", 50.0)

    # -- The same reaction, starved of H2O, with and without a tank (KTD13)
    system.add_component(name="UNBUFFERED", cls="Electrolysis")
    feed(system, "UNBUFFERED", "H2O", 2.0)
    feed(system, "UNBUFFERED", "elec", 50.0)

    system.add_component(name="BUFFERED", cls="BufferedElectrolysis")
    feed(system, "BUFFERED", "H2O", 2.0)
    feed(system, "BUFFERED", "elec", 50.0)

    # -- Production into a capacity the output flow then draws from (KTD13)
    system.add_component(name="BOTTLER", cls="Bottler")
    feed(system, "BOTTLER", "H2O", 6.0)
    # The downstream that takes whatever the bottler makes: the scenario is
    # about 6 of H2O making 3 of H2, so the rule must be free to run above its
    # nominal scale (R-10).
    system.add_component(name="BOTTLE_SINK", cls="RateSink", takes=["H2"], demand=1e3)
    system.connect_flow(source="BOTTLER", target="BOTTLE_SINK", flow_name="H2")

    # -- A rule with an empty cons map
    system.add_component(name="BOILER", cls="Boiler")

    # -- AE9: continuous flows, no rule, identity transfer
    system.add_component(name="PIPE", cls="Pipe")
    feed(system, "PIPE", "q", 3.5)

    add_clock(system.comp["BOILER"])

    return system


def run_production_scenario(obs):
    """Drive the production system one step and snapshot every reading."""
    system = build_production_system()

    system.isimu_start()

    obs["orders"] = dict(system.equation_order.orders)
    obs["production_order"] = list(system.equation_order.production_order)
    obs["t0"] = {
        "H2_nominal": out_value(system, "NOMINAL", "H2"),
        "steam": out_value(system, "BOILER", "steam"),
        "q_pipe": out_value(system, "PIPE", "q"),
    }

    system.isimu_step_forward()

    tank = system.comp["BUFFERED"].capacities["tank"]
    bottle = system.comp["BOTTLER"].capacities["bottle"]

    obs["time"] = system.currentTime()
    obs["t1"] = {
        "H2_nominal": out_value(system, "NOMINAL", "H2"),
        "O2_nominal": out_value(system, "NOMINAL", "O2"),
        "H2_scarce": out_value(system, "SCARCE", "H2"),
        "O2_scarce": out_value(system, "SCARCE", "O2"),
        "H2_absent": out_value(system, "ABSENT", "H2"),
        "O2_absent": out_value(system, "ABSENT", "O2"),
        "H2_excess": out_value(system, "EXCESS", "H2"),
        "O2_excess": out_value(system, "EXCESS", "O2"),
        "H2_unbuffered": out_value(system, "UNBUFFERED", "H2"),
        "H2_buffered": out_value(system, "BUFFERED", "H2"),
        "O2_buffered": out_value(system, "BUFFERED", "O2"),
        "tank_inflow": tank.get_inflow("H2O"),
        "tank_outflow": tank.get_outflow("H2O"),
        "tank_qty": tank.get_quantity("H2O"),
        "H2_bottler": out_value(system, "BOTTLER", "H2"),
        "bottle_inflow": bottle.get_inflow("H2"),
        "bottle_outflow": bottle.get_outflow("H2"),
        "bottle_qty": bottle.get_quantity("H2"),
        "steam": out_value(system, "BOILER", "steam"),
        "q_pipe_in": system.comp["PIPE"].flows_in["q"].var_fed.value(),
        "q_pipe_out": out_value(system, "PIPE", "q"),
    }

    system.isimu_stop()

    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_lost_input_scenario(obs)
    run_sourceless_output_scenario(obs)
    run_unwired_mismatch_scenario(obs)

    # Kept alive for the teardown test, per the module convention.
    run_production_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# R15 -- the limiting reagent
# ----------------------------------------------------------------------


def test_nominal_inputs_produce_the_nominal_output(the_run):
    """Every cons entry delivered at its coefficient: the rule runs at scale 1."""
    t1 = the_run["t1"]

    assert t1["H2_nominal"] == pytest.approx(5.0)
    assert t1["O2_nominal"] == pytest.approx(2.0)


def test_a_scarce_input_scales_the_rule_down(the_run):
    """AE5: half the nominal elec, and BOTH outputs come out at half.

    10 of H2O and 50 of elec make 5 of H2 and 2 of O2. Delivered 10 of H2O but
    only 25 of elec, the ratios are 1 and 0.5: the scarcest sets the scale.
    """
    t1 = the_run["t1"]

    assert t1["H2_scarce"] == pytest.approx(2.5)
    assert t1["O2_scarce"] == pytest.approx(1.0)


def test_correlated_outputs_keep_their_declared_ratio(the_run):
    """AE5, stated as the property that makes one shared scale necessary."""
    t1 = the_run["t1"]

    nominal_ratio = t1["H2_nominal"] / t1["O2_nominal"]

    assert t1["H2_scarce"] / t1["O2_scarce"] == pytest.approx(nominal_ratio)
    assert t1["H2_scarce"] == pytest.approx(t1["H2_nominal"] / 2)
    assert t1["O2_scarce"] == pytest.approx(t1["O2_nominal"] / 2)


def test_an_absent_input_yields_no_production(the_run):
    """elec is wired to nobody: the reaction has a null ratio, so it stops."""
    t1 = the_run["t1"]

    assert t1["H2_absent"] == pytest.approx(0.0)
    assert t1["O2_absent"] == pytest.approx(0.0)


def test_an_input_in_excess_does_not_raise_production(the_run):
    """Ten times the H2O and the nominal elec produce exactly the nominal.

    The scale is a MINIMUM over the ratios, so a surplus on one input cannot
    compensate -- let alone exceed -- what the scarcest one allows.
    """
    t1 = the_run["t1"]

    assert t1["H2_excess"] == pytest.approx(5.0)
    assert t1["O2_excess"] == pytest.approx(2.0)
    assert t1["H2_excess"] == pytest.approx(t1["H2_nominal"])


def test_an_empty_cons_map_produces_its_declared_quantity(the_run):
    """A rule consuming nothing is limited by nothing: it produces 4 of steam."""
    assert the_run["t1"]["steam"] == pytest.approx(4.0)


def test_the_scale_arithmetic_on_its_own():
    """The limiting reagent, away from the engine: one scale, every prod entry."""
    rule = muscadet.rules.Rule(cons={"H2O": 10, "elec": 50}, prod={"H2": 5, "O2": 2})

    assert rules.rule_scale(rule, {"H2O": 10, "elec": 50}) == pytest.approx(1.0)
    assert rules.rule_scale(rule, {"H2O": 10, "elec": 25}) == pytest.approx(0.5)
    assert rules.rule_scale(rule, {"H2O": 1000, "elec": 50}) == pytest.approx(1.0)
    assert rules.rule_scale(rule, {"H2O": 10}) == pytest.approx(0.0)

    scale = rules.rule_scale(rule, {"H2O": 10, "elec": 25})
    assert rules.rule_production(rule, scale) == {"H2": 2.5, "O2": 1.0}
    assert rules.rule_consumption(rule, scale) == {"H2O": 5.0, "elec": 25.0}

    # Nothing consumed, nothing to limit: the declared quantity, unconditionally
    unconditional = muscadet.rules.Rule(prod={"steam": 4})
    assert rules.rule_scale(unconditional, {}) == rules.UNCONSTRAINED_SCALE
    assert rules.rule_production(unconditional, 1.0) == {"steam": 4.0}

    # An unbounded counterparty does not limit either, and does not produce
    # an infinite quantity
    assert rules.rule_scale(rule, {"H2O": float("inf"), "elec": 50}) == pytest.approx(
        1.0
    )
    assert rules.rule_scale(
        rule, {"H2O": float("inf"), "elec": float("inf")}
    ) == pytest.approx(rules.UNCONSTRAINED_SCALE)


# ----------------------------------------------------------------------
# KTD13 -- an interposed capacity is the counterparty of the rules
# ----------------------------------------------------------------------


def test_the_rules_draw_from_an_input_capacity(the_run):
    """The tank serves what the flow cannot: the reaction runs at nominal.

    Both components are fed 2 of H2O -- a fifth of the coefficient -- and 50 of
    elec. Facing the flow directly, the reaction runs at 0.2. Facing a tank
    holding stock, it is not limited by H2O at all.
    """
    t1 = the_run["t1"]

    assert t1["H2_unbuffered"] == pytest.approx(1.0)

    assert t1["H2_buffered"] == pytest.approx(5.0)
    assert t1["O2_buffered"] == pytest.approx(2.0)


def test_an_input_capacity_is_filled_by_its_flow_and_drained_by_the_rules(the_run):
    """The two hops the rules cross on the input side, seen on the transit."""
    t1 = the_run["t1"]

    # What the flow delivers enters the tank ...
    assert t1["tank_inflow"] == pytest.approx(2.0)
    # ... and what the rule consumes at scale 1 leaves it
    assert t1["tank_outflow"] == pytest.approx(10.0)

    # 100 held, drained by the imbalance for the whole step
    expected = 100.0 - (10.0 - 2.0) * the_run["time"]
    assert the_run["time"] == pytest.approx(CLOCK_DELAY)
    assert t1["tank_qty"] == pytest.approx(expected, rel=1e-6)


def test_production_enters_an_output_capacity_the_flow_draws_from(the_run):
    """6 of H2O make 3 of H2, and the 3 travel THROUGH the bottle."""
    t1 = the_run["t1"]

    # Production enters the capacity ...
    assert t1["bottle_inflow"] == pytest.approx(3.0)
    # ... and the output flow carries what the capacity serves onward
    assert t1["bottle_outflow"] == pytest.approx(3.0)
    assert t1["H2_bottler"] == pytest.approx(t1["bottle_outflow"])

    # Everything transits, so the bottle accumulates nothing
    assert t1["bottle_qty"] == pytest.approx(0.0)


# ----------------------------------------------------------------------
# R31 / AE9 -- the transfer of a component declaring no rule
# ----------------------------------------------------------------------


def test_a_component_with_no_rule_transfers_its_input(the_run):
    """AE9: one continuous input, one continuous output of the same name."""
    t1 = the_run["t1"]

    assert t1["q_pipe_in"] == pytest.approx(3.5)
    assert t1["q_pipe_out"] == pytest.approx(3.5)

    # Nothing was produced before the solver ran: production is an equation,
    # not a declaration, so at t=0 the output still holds its declared default.
    assert the_run["t0"]["q_pipe"] == pytest.approx(0.0)


def test_an_input_with_no_matching_output_raises(the_run):
    """The message names the component and the unmatched flow."""
    error = the_run["lost_error"]

    assert error is not None, "an input going nowhere must not pass silently"
    assert isinstance(error, ValueError)

    message = str(error)
    assert "LOST" in message
    assert "input flow extra" in message
    assert "add_rules" in message

    # The transferable flow is not what is complained about
    assert "input flow q" not in message


def test_an_output_with_no_matching_input_raises(the_run):
    """The same check, on the other side, on its own component."""
    error = the_run["sourceless_error"]

    assert error is not None, "an output nothing feeds must not pass silently"
    assert isinstance(error, ValueError)

    message = str(error)
    assert "SOURCELESS" in message
    assert "output flow spare" in message

    assert "output flow q" not in message


def test_the_unmatched_flow_is_a_model_error_at_run_time(the_run):
    """The transfer is evaluated by the solver, so the run is what refuses it."""
    error = the_run["lost_run_error"]

    assert error is not None, "the run must not proceed on a broken transfer"
    assert isinstance(error, ValueError)
    assert "input flow extra" in str(error)


def test_an_unwired_flow_demands_no_counterpart(the_run):
    """The check is scoped to what the model actually wires.

    An unconnected continuous flow reads (or holds) its declared default and
    exchanges nothing, so it can neither lose nor invent a quantity.
    """
    assert the_run["unwired_error"] is None
    assert the_run["unwired_lost"] == ["q"]
    assert the_run["unwired_sourceless"] == ["q"]


def test_a_one_sided_component_transfers_nothing(the_run):
    """A source and a consumer have no counterpart to match, and need none."""
    assert the_run["source_transfer"] == []
    assert the_run["sink_transfer"] == []


# ----------------------------------------------------------------------
# R8 -- the equation comes from the graph, by name
# ----------------------------------------------------------------------


def test_the_production_equation_is_registered_from_the_graph(the_run):
    """The component defines the method; the graph hands it its order.

    Nothing in the components above declares an order, and none of them calls
    the ordering module: defining ``compute_production`` is the whole contract.
    """
    orders = the_run["orders"]
    method = ordering.PRODUCTION_EQUATION_METHOD

    assert method == "compute_production"

    for comp_name in ["NOMINAL", "SCARCE", "BUFFERED", "BOTTLER", "PIPE", "BOILER"]:
        assert (comp_name, method) in orders

    # A producer is evaluated before the component it feeds, so a chain settles
    # within one step instead of lagging one step per hop.
    production_order = the_run["production_order"]
    assert production_order.index("PIPE_q_SRC") < production_order.index("PIPE")


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
