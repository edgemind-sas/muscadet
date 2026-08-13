"""A demand a component can honour: the capability channel (R-20).

The **over-demand** defect, and the third sweep that closes it.

A component's demand is mapped back from its outputs through the active rule's
DECLARED coefficients, because production has not run yet and the scale is not
known. So a reaction limited by a scarce reagent went on claiming its nominal
share of an abundant one -- it no longer TOOK it (R-12 caps the draw and
releases the surplus) but it still competed for it, and the split of a shared
supply was made in proportion to a demand one of the rivals could not honour.

No lagged scheme closes that. A delivery is ``min(capability, demand)``, so a
demand recomputed from what ARRIVED is self-referential: the quantity being
looked for has already been destroyed. Measured, on the rivals model below:
bounding by the previous production scale converges on a wrong fixed point,
bounding by the previous deliveries decays monotonically to zero (0.1 to 5e-4
over 4000 evaluations), and adding a saturation test oscillates with period 2.

What was missing is the suppliers' **capability**, so it is published: a third
sweep, downstream like production, on a channel of its own, ahead of the demand
sweep. A demand is then bounded by

    demand_i = coefficient_i x min( downstream scale,
                                    min over j != i of ( capability_j / coefficient_j ) )

and the two rivals split their contested supply 0.0909 / 0.909 -- the fair
split, at the first evaluation, under a modest downstream demand and under an
unbounded one alike.

What is asserted here
---------------------
* the channel and its variables, and that a mode cannot usefully clamp them;
* the band: capability before demand before production, on the production
  order;
* what capability MEANS per component kind -- a source, a transformer, a
  stocked volume, an empty one, an unwired input, a rule with no inputs, a
  discrete gate -- each established rather than assumed;
* the rivals split, and that it does not move over repeated evaluations;
* that ``release_unused_supply`` became a **no-op on the common path** and
  still fires where the estimate is knowingly optimistic;
* that a capacity-broken cycle still builds and still circulates.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, driven and deleted before the next; the fixture snapshots what each
produced and the last is kept alive for the teardown.
"""

import math

import cod3s
import muscadet
import pytest

from muscadet import capability, ordering

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
    TransformerContinuous,
)

#: A date the interactive session can always step to, so the solver integrates.
CD_CLOCK = 1.0

# -- Two rivals on one shared supply, the model of the R-20 analysis.
#    U1: 10 A + 1 E -> 1 P1, limited to 0.1 by A. U2: 1 E -> 1 P2, limited by
#    nothing. SE can only give 1.0 in all.
CD_SHARED = 1.0
CD_U1_CONS = {"A": 10.0, "E": 1.0}
#: What U1 can achieve: 1.0 of A against a coefficient of 10.
CD_U1_SCALE = CD_SHARED / CD_U1_CONS["A"]
#: The fair split of the contested 1.0: claims of 0.1 and 1.0, shared in
#: proportion because 1.1 is genuinely more than there is.
CD_FAIR_U1 = CD_U1_SCALE / (CD_U1_SCALE + 1.0)
CD_FAIR_U2 = 1.0 / (CD_U1_SCALE + 1.0)
#: An unbounded downstream demand is the normal case in the models being ported.
CD_UNBOUNDED_DOWNSTREAM = 1000.0
#: How many times the three sweeps are driven by hand to show the bound does not
#: drift. One is enough for the answer; the rest are the evidence.
CD_ROUNDS = 12

# -- A transformer whose reagents nothing else contends for: the common path.
CD_CONS = {"a": 2.0, "b": 1.0}
CD_PLENTY = 10.0
#: Scarce enough on ``a`` to make the rule limited by it, which is exactly the
#: case release_unused_supply was written for.
CD_SCARCE = 3.0
CD_BIG_DEMAND = 1000.0

# -- What a mode leaves of the derated output.
CD_DERATING = 0.25

# -- The recirculation loop of R-14, which must go on building.
CD_LOOP_VOLUME = 100.0
CD_LOOP_CONTENT = 40.0
CD_LOOP_RATE = 2.0


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class CapSource(muscadet.ObjFlow):
    """A producer holding its declared rates, recording what it is handed back.

    ``released`` is what :func:`muscadet.evaluation.release_unused_supply` gave
    back to it, accumulated over every evaluation. It is the measurement the
    "no-op on the common path" assertions rest on: a release is a write on THIS
    component, so counting it here counts it where it happens.
    """

    released = 0.0

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for name, rate in kwargs.get("rates", {}).items():
            self.add_flow_continuous_out(name=name, var_fed_default=rate)

    def release_output(self, flow, comp_name, taken):
        released = super().release_output(flow, comp_name, taken)
        self.released += released
        return released


class CapSink(muscadet.ObjFlow):
    """A pure consumer asking for a declared quantity on each flow it takes."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for name in kwargs.get("takes", []):
            self.add_flow_continuous_in(
                name=name, var_demand_default=kwargs.get("demand", 0.0)
            )


class CapUnit(muscadet.ObjFlow):
    """``2 a + 1 b -> 1 x``: two reagents, one of them the limiting one."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_in(name="b")
        self.add_flow_continuous_out(name="x")
        self.add_rules(name="unit", rules=[dict(cons=CD_CONS, prod={"x": 1.0})])


class CapUnwired(muscadet.ObjFlow):
    """The same rule with ``b`` wired to nobody, holding a declared default.

    An unwired input supplies its declared constant and nothing else, so its
    capability is that constant -- the same answer ``get_input_delivered``
    gives it, which is what keeps the bound and the production agreeing.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_in(name="b", var_in_default=kwargs.get("held", 4.0))
        self.add_flow_continuous_out(name="x")
        self.add_rules(name="unit", rules=[dict(cons=CD_CONS, prod={"x": 1.0})])


class CapBoiler(muscadet.ObjFlow):
    """A rule with an EMPTY ``cons`` map: nothing constrains it."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="steam")
        self.add_rules(name="boil", rules=[dict(prod={"steam": 4.0})])


class CapGated(muscadet.ObjFlow):
    """A rule naming a DISCRETE input in its ``cons`` map.

    A gate carries no quantity, so it must bound no quantity: the continuous
    ``a`` is claimed at what the downstream asks for, never clamped to 1 by a
    boolean read as a rate.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="g", logic="and")
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_out(name="x")
        self.add_rules(
            name="gated", rules=[dict(cons={"g": 1.0, "a": 2.0}, prod={"x": 1.0})]
        )


class CapPipe(muscadet.ObjFlow):
    """One continuous flow, carried on both sides: the identity transfer."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="q")


class CapDerated(muscadet.ObjFlow):
    """``1 q -> 1 x``, with a mode that derates ``x`` from the start."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="x")
        self.add_rules(name="r", rules=[dict(cons={"q": 1.0}, prod={"x": 1.0})])


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def add_clock(comp, delay=CD_CLOCK):
    """Give the interactive session a date to step to."""
    comp.add_atm2states(
        name="cd_clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": delay},
        cond_occ_21=False,
    )


def published(system, comp, flow):
    """What ``comp`` currently asks its producers for on ``flow``."""
    return system.comp[comp].flows_in[flow].var_demand.value()


def drawn(system, comp, flow):
    """What ``comp`` currently receives on ``flow``, after any release."""
    return system.comp[comp].flows_in[flow].get_delivered()


def capability_of(system, comp, flow):
    """What ``comp`` publishes as the capability of its output ``flow``."""
    return system.comp[comp].flows_out[flow].get_capability()


def drive_sweeps(system, order):
    """Drive the three sweeps by hand, in band order, once.

    Outside the solver, so it measures the algorithm rather than the
    integration: the same methods the PDMP manager calls, in the same sequence
    the derived order registers them in.
    """
    for name in order.capability_order:
        system.comp[name].compute_capability()
    for name in order.demand_order:
        system.comp[name].compute_demand()
    for name in order.production_order:
        system.comp[name].compute_production()


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def add_rivals(system, prefix, downstream_demand):
    """Two reactors on one shared supply, one of them limited by another input."""
    system.add_component(
        name=f"{prefix}_SA", cls="SourceContinuous", flow="A", rate=CD_SHARED
    )
    system.add_component(
        name=f"{prefix}_SE", cls="SourceContinuous", flow="E", rate=CD_SHARED
    )
    system.add_component(
        name=f"{prefix}_U1",
        cls="TransformerContinuous",
        flows_in=list(CD_U1_CONS),
        flows_out=["P1"],
        rules=[dict(name="r", cons=CD_U1_CONS, prod={"P1": 1.0})],
    )
    system.add_component(
        name=f"{prefix}_U2",
        cls="TransformerContinuous",
        flows_in=["E"],
        flows_out=["P2"],
        rules=[dict(name="r", cons={"E": 1.0}, prod={"P2": 1.0})],
    )
    system.add_component(
        name=f"{prefix}_C1",
        cls="ConsumerContinuous",
        flow="P1",
        demand=downstream_demand,
    )
    system.add_component(
        name=f"{prefix}_C2", cls="ConsumerContinuous", flow="P2", demand=1.0
    )

    for source, target, flow in (
        (f"{prefix}_SA", f"{prefix}_U1", "A"),
        (f"{prefix}_SE", f"{prefix}_U1", "E"),
        (f"{prefix}_SE", f"{prefix}_U2", "E"),
        (f"{prefix}_U1", f"{prefix}_C1", "P1"),
        (f"{prefix}_U2", f"{prefix}_C2", "P2"),
    ):
        system.connect_flow(source=source, target=target, flow_name=flow)


def rival_snapshot(system, prefix):
    """What one rival pair reads right now."""
    return {
        "U1_demand_E": published(system, f"{prefix}_U1", "E"),
        "U1_demand_A": published(system, f"{prefix}_U1", "A"),
        "U2_demand_E": published(system, f"{prefix}_U2", "E"),
        "U1_draws_E": drawn(system, f"{prefix}_U1", "E"),
        "U2_draws_E": drawn(system, f"{prefix}_U2", "E"),
        "cap_A": capability_of(system, f"{prefix}_SA", "A"),
        "cap_E": capability_of(system, f"{prefix}_SE", "E"),
    }


def run_rivals_scenario(obs):
    """The rivals split, and that repeated evaluation does not move it."""
    system = muscadet.System(name="CapabilityRivals")

    add_rivals(system, "MOD", downstream_demand=1.0)
    add_rivals(system, "UNB", downstream_demand=CD_UNBOUNDED_DOWNSTREAM)
    add_clock(system.comp["MOD_SA"])

    system.isimu_start()

    order = system.equation_order

    # One round per sample, driven by hand: the bound settles on the FIRST and
    # a drifting scheme would show it here rather than in an integrated level.
    obs["rivals_rounds"] = []
    for _ in range(CD_ROUNDS):
        drive_sweeps(system, order)
        obs["rivals_rounds"].append(
            {
                "MOD": rival_snapshot(system, "MOD"),
                "UNB": rival_snapshot(system, "UNB"),
            }
        )

    system.isimu_step_forward()
    obs["rivals_integrated"] = {
        "MOD": rival_snapshot(system, "MOD"),
        "UNB": rival_snapshot(system, "UNB"),
    }
    obs["rivals_time"] = system.currentTime()

    system.isimu_stop()
    system.deleteSys()


def run_kinds_scenario(obs):
    """What capability means for each way a component puts a quantity out."""
    system = muscadet.System(name="CapabilityKinds")

    # -- A source: its declared rate.
    system.add_component(name="SRC_A", cls="CapSource", rates={"a": CD_PLENTY})
    system.add_component(name="SRC_B", cls="CapSource", rates={"b": CD_PLENTY})

    # -- A transformer: what its rule could produce from its inputs.
    system.add_component(name="UNIT", cls="CapUnit")
    system.add_component(name="UNIT_C", cls="CapSink", takes=["x"], demand=1.0)
    system.connect_flow(source="SRC_A", target="UNIT", flow_name="a")
    system.connect_flow(source="SRC_B", target="UNIT", flow_name="b")
    system.connect_flow(source="UNIT", target="UNIT_C", flow_name="x")

    # -- A rule with no inputs: nothing constrains it, so its nominal.
    system.add_component(name="BOILER", cls="CapBoiler")
    system.add_component(name="BOILER_C", cls="CapSink", takes=["steam"], demand=1.0)
    system.connect_flow(source="BOILER", target="BOILER_C", flow_name="steam")

    # -- An unwired input: the constant it was declared with.
    system.add_component(name="SRC_A2", cls="CapSource", rates={"a": CD_PLENTY})
    system.add_component(name="UNWIRED", cls="CapUnwired", held=4.0)
    system.add_component(name="UNWIRED_C", cls="CapSink", takes=["x"], demand=1.0)
    system.connect_flow(source="SRC_A2", target="UNWIRED", flow_name="a")
    system.connect_flow(source="UNWIRED", target="UNWIRED_C", flow_name="x")

    # -- A stocked volume: unbounded. An empty one: what transits it.
    system.add_component(
        name="STOCKED",
        cls="CapacityContinuous",
        flow="q",
        ports="both",
        capacity=CD_LOOP_VOLUME,
        capacity_name="vol",
        content_init={"q": CD_LOOP_CONTENT},
    )
    system.add_component(
        name="EMPTY",
        cls="CapacityContinuous",
        flow="q",
        ports="both",
        capacity=CD_LOOP_VOLUME,
        capacity_name="vol",
    )
    system.add_component(name="SRC_Q", cls="CapSource", rates={"q": CD_LOOP_RATE})
    system.add_component(name="STOCKED_C", cls="CapSink", takes=["q"], demand=1.0)
    system.add_component(name="EMPTY_C", cls="CapSink", takes=["q"], demand=1.0)
    system.connect_flow(source="SRC_Q", target="EMPTY", flow_name="q")
    system.connect_flow(source="STOCKED", target="STOCKED_C", flow_name="q")
    system.connect_flow(source="EMPTY", target="EMPTY_C", flow_name="q")

    # -- An identity transfer: what could arrive is what could leave.
    system.add_component(name="SRC_P", cls="CapSource", rates={"q": CD_LOOP_RATE})
    system.add_component(name="PIPE", cls="CapPipe")
    system.add_component(name="PIPE_C", cls="CapSink", takes=["q"], demand=1.0)
    system.connect_flow(source="SRC_P", target="PIPE", flow_name="q")
    system.connect_flow(source="PIPE", target="PIPE_C", flow_name="q")

    # -- A DISCRETE input named in a cons map: it bounds nothing.
    system.add_component(name="SRC_G", cls="CapSource", rates={"a": CD_PLENTY})
    system.add_component(name="GATED", cls="CapGated")
    system.add_component(name="GATED_C", cls="CapSink", takes=["x"], demand=2.0)
    system.connect_flow(source="SRC_G", target="GATED", flow_name="a")
    system.connect_flow(source="GATED", target="GATED_C", flow_name="x")

    add_clock(system.comp["BOILER"])

    system.isimu_start()
    order = system.equation_order

    obs["kinds_order"] = order
    obs["kinds_registrations"] = list(order.registrations)

    drive_sweeps(system, order)

    obs["kinds"] = {
        "source": capability_of(system, "SRC_A", "a"),
        # 10 of a / 2 and 10 of b / 1 -> the scarcest is 5, and 1 x per unit
        "transformer": capability_of(system, "UNIT", "x"),
        "no_inputs": capability_of(system, "BOILER", "steam"),
        # min(10 / 2, 4 / 1) = 4
        "unwired_input": capability_of(system, "UNWIRED", "x"),
        "stocked_capacity": capability_of(system, "STOCKED", "q"),
        "empty_capacity": capability_of(system, "EMPTY", "q"),
        "transfer": capability_of(system, "PIPE", "q"),
        # the gate bounds nothing, so 10 of a / 2 -> 5
        "gated": capability_of(system, "GATED", "x"),
    }
    obs["kinds_input"] = {
        "unwired": system.comp["UNWIRED"].get_input_capability("b"),
        "discrete": system.comp["GATED"].get_input_capability("g"),
        "wired": system.comp["UNIT"].get_input_capability("a"),
    }
    obs["kinds_supply_scale"] = {
        "exclude_a": system.comp["UNIT"].get_supply_scale(
            system.comp["UNIT"].rule_sets["unit"].rules[0], exclude="a"
        ),
        "exclude_b": system.comp["UNIT"].get_supply_scale(
            system.comp["UNIT"].rule_sets["unit"].rules[0], exclude="b"
        ),
        "exclude_none": system.comp["UNIT"].get_supply_scale(
            system.comp["UNIT"].rule_sets["unit"].rules[0]
        ),
    }
    obs["kinds_endpoints"] = muscadet.derating.solver_owned_endpoints(
        system.comp["SRC_A"]
    )
    obs["kinds_demand"] = {
        # bounded by b's capability: 10 / 1 = 10, above the downstream 1 -> 2
        "unit_a": published(system, "UNIT", "a"),
        "unit_b": published(system, "UNIT", "b"),
        # the gate does not clamp the continuous claim to 1
        "gated_a": published(system, "GATED", "a"),
    }

    system.isimu_stop()
    system.deleteSys()


def run_release_scenario(obs):
    """Where the estimate is exact, and where it is knowingly optimistic."""
    system = muscadet.System(name="CapabilityRelease")

    # -- The common path: dedicated suppliers, one of them limiting.
    for prefix, rate_a in (("PLENTY", CD_PLENTY), ("LIMITED", CD_SCARCE)):
        system.add_component(name=f"{prefix}_A", cls="CapSource", rates={"a": rate_a})
        system.add_component(
            name=f"{prefix}_B", cls="CapSource", rates={"b": CD_PLENTY}
        )
        system.add_component(name=prefix, cls="CapUnit")
        system.add_component(
            name=f"{prefix}_C", cls="CapSink", takes=["x"], demand=CD_BIG_DEMAND
        )
        system.connect_flow(source=f"{prefix}_A", target=prefix, flow_name="a")
        system.connect_flow(source=f"{prefix}_B", target=prefix, flow_name="b")
        system.connect_flow(source=prefix, target=f"{prefix}_C", flow_name="x")

    # -- The optimistic case: a derating, applied only in the production sweep.
    system.add_component(name="DER_Q", cls="CapSource", rates={"q": CD_PLENTY})
    system.add_component(name="DERATED", cls="CapDerated")
    system.add_component(
        name="DERATED_C", cls="CapSink", takes=["x"], demand=CD_BIG_DEMAND
    )
    system.connect_flow(source="DER_Q", target="DERATED", flow_name="q")
    system.connect_flow(source="DERATED", target="DERATED_C", flow_name="x")
    system.comp["DERATED"].flows_out["x"].var_out_rate.setValue(CD_DERATING)

    add_clock(system.comp["PLENTY_A"])

    system.isimu_start()
    order = system.equation_order

    # Reset after the start-up evaluations, so what is counted is the steady
    # state and not the transient of a model settling from its declared values.
    for name in ("PLENTY_A", "PLENTY_B", "LIMITED_A", "LIMITED_B", "DER_Q"):
        system.comp[name].released = 0.0

    for _ in range(CD_ROUNDS):
        drive_sweeps(system, order)

    obs["release"] = {
        "plenty_a": system.comp["PLENTY_A"].released,
        "plenty_b": system.comp["PLENTY_B"].released,
        "limited_a": system.comp["LIMITED_A"].released,
        "limited_b": system.comp["LIMITED_B"].released,
        "derated_q": system.comp["DER_Q"].released,
    }
    obs["release_quantities"] = {
        "limited_demand_b": published(system, "LIMITED", "b"),
        "limited_draws_b": drawn(system, "LIMITED", "b"),
        "limited_draws_a": drawn(system, "LIMITED", "a"),
        "derated_demand_q": published(system, "DERATED", "q"),
        "derated_draws_q": drawn(system, "DERATED", "q"),
    }

    system.isimu_stop()
    system.deleteSys()


def run_torn_cycle_scenario(obs):
    """R-14 unchanged: the recirculation loop builds, and capability is defined."""
    system = muscadet.System(name="CapabilityTornCycle")

    system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        flow="q",
        ports="both",
        capacity=CD_LOOP_VOLUME,
        capacity_name="tank",
        content_init={"q": CD_LOOP_CONTENT},
    )
    system.add_component(
        name="PUMP",
        cls="TransformerContinuous",
        flows_in=["q"],
        flows_out=["q"],
    )
    system.connect_flow(source="TANK", target="PUMP", flow_name="q")
    system.connect_flow(source="PUMP", target="TANK", flow_name="q")

    add_clock(system.comp["PUMP"])

    order = ordering.compute_equation_order(system)
    obs["torn"] = [str(cnct) for cnct in order.torn]

    system.isimu_start()
    system.isimu_step_forward()

    obs["loop"] = {
        "tank_cap": capability_of(system, "TANK", "q"),
        "pump_cap": capability_of(system, "PUMP", "q"),
        "level": system.comp["TANK"].capacities["tank"].get_quantity("q"),
        "time": system.currentTime(),
    }

    system.isimu_stop()

    # Kept alive for the teardown test, per the module convention.
    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_rivals_scenario(obs)
    run_kinds_scenario(obs)
    run_release_scenario(obs)
    run_torn_cycle_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# The channel
# ----------------------------------------------------------------------


def test_a_continuous_output_publishes_a_capability_a_consumer_reads():
    """The channel is a third alias on the SAME bidirectional message box.

    One ``connect`` therefore wires data, demand and capability together, and a
    model that already builds needs no new wiring.
    """
    out = muscadet.FlowContinuousOut(name="q", var_fed_default=3.0)
    inp = muscadet.FlowContinuousIn(name="q")

    # Declared fields on both sides, before any engine exists
    assert "var_capability" in type(out).model_fields
    assert "var_capability" in type(inp).model_fields
    assert out.var_capability is None
    assert inp.var_capability is None


def test_the_capability_variables_are_named_for_their_direction(the_run):
    """``{flow}_capability_out`` travels with the quantity, like ``{flow}_fed_out``."""
    endpoints = the_run["kinds_endpoints"]

    assert "a_capability_out" in endpoints
    assert "a_fed_out" in endpoints

    # Written by the sweep at every step, so a mode clamping it is a silent
    # no-op -- and the endpoint that WOULD work is named, as for every other
    # solver-owned variable (R-14). It is the same advice ``a_fed_out`` carries,
    # because the mistake and its remedy are the same.
    assert endpoints["a_capability_out"] == endpoints["a_fed_out"]
    assert "derate the output" in endpoints["a_capability_out"]

    # ... and the public endpoint stays clampable, as it always was
    assert "a_out_rate" not in endpoints


# ----------------------------------------------------------------------
# The band
# ----------------------------------------------------------------------


def test_the_capability_band_runs_before_demand_and_production(the_run):
    """Every capability is settled before the first demand equation runs.

    It has to be: a demand is bounded by what the rule's OTHER inputs could
    supply, and those are published by this sweep.
    """
    registrations = the_run["kinds_registrations"]

    bands = {
        method: [r.order for r in registrations if r.method == method]
        for method in (
            ordering.CAPABILITY_EQUATION_METHOD,
            ordering.DEMAND_EQUATION_METHOD,
            ordering.PRODUCTION_EQUATION_METHOD,
        )
    }

    assert ordering.CAPABILITY_EQUATION_METHOD == "compute_capability"
    assert bands[ordering.CAPABILITY_EQUATION_METHOD]

    assert max(bands["compute_capability"]) < min(bands["compute_demand"])
    assert max(bands["compute_demand"]) < min(bands["compute_production"])
    assert max(bands["compute_production"]) < ordering.CAPACITY_ORDER_BASE


def test_the_capability_sweep_follows_the_production_order(the_run):
    """A capability travels WITH the quantity, so a producer publishes first."""
    order = the_run["kinds_order"]

    assert order.capability_order == order.production_order

    registrations = [
        r.comp
        for r in the_run["kinds_registrations"]
        if r.method == ordering.CAPABILITY_EQUATION_METHOD
    ]
    assert registrations == order.production_order


def test_every_equation_still_receives_a_distinct_order_integer(the_run):
    """KTD3 holds across the new band as well as within it."""
    orders = [r.order for r in the_run["kinds_registrations"]]

    assert len(orders) == len(set(orders))


# ----------------------------------------------------------------------
# What capability means, per component kind
# ----------------------------------------------------------------------


def test_a_source_publishes_its_declared_rate(the_run):
    """The one case with nothing to derive it from."""
    assert the_run["kinds"]["source"] == pytest.approx(CD_PLENTY)


def test_a_transformer_publishes_what_its_rule_could_make(the_run):
    """The limiting reagent again, over CAPABILITIES instead of deliveries.

    Computed by the very same :func:`muscadet.rules.rule_scale` the production
    sweep uses, which is what stops the estimate drifting from the physics it
    is meant to predict.
    """
    # min(10 / 2, 10 / 1) = 5, and 1 of x per unit of scale
    assert the_run["kinds"]["transformer"] == pytest.approx(5.0)


def test_a_rule_with_no_inputs_publishes_its_nominal(the_run):
    """``rule_scale`` returns UNCONSTRAINED_SCALE, exactly as production does."""
    assert the_run["kinds"]["no_inputs"] == pytest.approx(4.0)


def test_an_unwired_input_supplies_the_constant_it_was_declared_with(the_run):
    """``var_in_default``, the same answer ``get_input_delivered`` gives it."""
    assert the_run["kinds_input"]["unwired"] == pytest.approx(4.0)

    # min(10 / 2, 4 / 1) = 4 -- the unwired input is the limiting one here
    assert the_run["kinds"]["unwired_input"] == pytest.approx(4.0)


def test_a_stocked_volume_is_unbounded_and_an_empty_one_passes_through(the_run):
    """KTD13 on the capability channel: the volume replaces the flow.

    A reservoir can serve its whole content, so what it could deliver is not
    what its rules could make -- answering from production alone would report a
    stocked tank as capable of nothing, and every consumer downstream would size
    its demand at zero.

    An empty volume degrades to a pass-through and can only pass on what
    transits it, which is what its producer is about to deliver.
    """
    assert math.isinf(the_run["kinds"]["stocked_capacity"])
    assert the_run["kinds"]["empty_capacity"] == pytest.approx(CD_LOOP_RATE)


def test_an_identity_transfer_carries_the_capability_across(the_run):
    """What could arrive is what could leave (R31)."""
    assert the_run["kinds"]["transfer"] == pytest.approx(CD_LOOP_RATE)


def test_a_discrete_gate_bounds_no_quantity(the_run):
    """A boolean is not a rate, so it must not clamp a continuous claim.

    Reading the gate as 1 would bound the rule at a scale of 1 and halve what
    this component asks for, on the strength of a variable that carries no
    quantity at all. Its effect on PRODUCTION is untouched: ``rule_scale`` goes
    on reading it as 0 or 1 and a closed gate still stops the rule.
    """
    assert math.isinf(the_run["kinds_input"]["discrete"])

    # 10 of a against a coefficient of 2: the gate did not enter it
    assert the_run["kinds"]["gated"] == pytest.approx(5.0)

    # The downstream asks for 2 of x, so 4 of a -- not the 2 a gate read as a
    # rate would have allowed.
    assert the_run["kinds_demand"]["gated_a"] == pytest.approx(4.0)


def test_an_input_is_never_bounded_by_its_own_capability(the_run):
    """``exclude`` is what makes the scheme work at all, not a refinement.

    Including the input being sized would bound it by what it is already
    getting, so a consumer that started small could never grow -- the ratchet
    the measured "bound by the previous deliveries" variant decays through.
    """
    scales = the_run["kinds_supply_scale"]

    # Excluding ``a`` leaves ``b``: 10 / 1
    assert scales["exclude_a"] == pytest.approx(10.0)
    # Excluding ``b`` leaves ``a``: 10 / 2
    assert scales["exclude_b"] == pytest.approx(5.0)
    # Excluding nothing is the minimum of both, and is never what sizes a demand
    assert scales["exclude_none"] == pytest.approx(5.0)


def test_the_demand_is_the_lesser_of_the_downstream_and_the_supply(the_run):
    """Both bounds are live: here the downstream is the smaller of the two."""
    demand = the_run["kinds_demand"]

    # 1 of x asked for, 2 of a and 1 of b per x, and the supply allows more
    assert demand["unit_a"] == pytest.approx(2.0)
    assert demand["unit_b"] == pytest.approx(1.0)


# ----------------------------------------------------------------------
# The rivals
# ----------------------------------------------------------------------


def test_the_rivals_reach_the_fair_split_under_both_downstream_demands(the_run):
    """0.0909 / 0.909, where it was 0.5 / 0.5 and 0.999 / 0.001.

    U1 asks for the 0.1 its scarce ``A`` lets it turn into P1, instead of the
    nominal 1.0 its downstream would justify. The contested 1.0 is then split in
    proportion to two claims that can both be honoured.
    """
    for label in ("MOD", "UNB"):
        entry = the_run["rivals_rounds"][-1][label]

        assert entry["U1_demand_E"] == pytest.approx(CD_U1_SCALE, rel=1e-6), label
        assert entry["U2_demand_E"] == pytest.approx(1.0, rel=1e-6), label

        assert entry["U1_draws_E"] == pytest.approx(CD_FAIR_U1, rel=1e-4), label
        assert entry["U2_draws_E"] == pytest.approx(CD_FAIR_U2, rel=1e-4), label


def test_the_bound_settles_on_the_first_evaluation_and_does_not_drift(the_run):
    """A capability is PUBLISHED, so there is no fixed point to converge to.

    This is what separates R-20 from every lagged scheme measured before it: a
    demand recomputed from what arrived decays to zero over thousands of
    evaluations, or oscillates with period 2. Twelve rounds of the three sweeps
    move nothing here, and the answer of round 1 is the answer of round 12.
    """
    rounds = the_run["rivals_rounds"]

    assert len(rounds) == CD_ROUNDS

    for label in ("MOD", "UNB"):
        first = rounds[0][label]

        for index, entry in enumerate(rounds):
            assert entry[label]["U1_demand_E"] == pytest.approx(
                first["U1_demand_E"], rel=1e-9
            ), f"{label} moved at round {index}"
            assert entry[label]["U1_draws_E"] == pytest.approx(
                first["U1_draws_E"], rel=1e-9
            ), f"{label} moved at round {index}"

        # ... and it is the right answer from the first round, not merely a
        # stable wrong one.
        assert first["U1_demand_E"] == pytest.approx(CD_U1_SCALE, rel=1e-6)


def test_the_split_survives_an_integration_step(the_run):
    """The hand-driven rounds and the solver's own evaluation agree."""
    for label in ("MOD", "UNB"):
        entry = the_run["rivals_integrated"][label]

        assert entry["U1_draws_E"] == pytest.approx(CD_FAIR_U1, rel=1e-4), label
        assert entry["U2_draws_E"] == pytest.approx(CD_FAIR_U2, rel=1e-4), label

    assert the_run["rivals_time"] == pytest.approx(CD_CLOCK)


def test_a_shared_capability_is_over_counted_by_both_rivals(the_run):
    """The limit of the scheme, measured rather than left as a caveat.

    ``incoming_capability`` sums what the producers publish and apportions
    nothing, because the question is asked before any demand exists to
    apportion against. So U1 sizes its claim on the whole of ``A`` and U2 on the
    whole of ``E``, each as if the other were absent, and the two claims still
    sum to 1.1 against the 1.0 that exists.
    """
    entry = the_run["rivals_rounds"][-1]["MOD"]

    assert entry["cap_A"] == pytest.approx(CD_SHARED)
    assert entry["cap_E"] == pytest.approx(CD_SHARED)

    # Both rivals read the whole of E's capability
    assert entry["U1_demand_E"] + entry["U2_demand_E"] > CD_SHARED

    # ... which is why U1 receives slightly less than the 0.1 it could use
    assert entry["U1_draws_E"] < CD_U1_SCALE


# ----------------------------------------------------------------------
# release_unused_supply: a no-op on the common path, and still needed
# ----------------------------------------------------------------------


def test_the_release_is_a_no_op_where_the_estimate_is_exact(the_run):
    """Nothing is handed back when nothing was over-fetched.

    Two units, one of them limited by its scarce ``a`` -- which is precisely the
    case ``release_unused_supply`` was written for. Since R-20 the demand on
    ``b`` is bounded by what ``a`` can sustain, so ``b`` is asked for exactly
    what the reaction will use and there is nothing left to give back.

    Twelve rounds of the three sweeps, and not one release on any of the four
    dedicated suppliers.
    """
    release = the_run["release"]

    assert release["plenty_a"] == pytest.approx(0.0)
    assert release["plenty_b"] == pytest.approx(0.0)
    assert release["limited_a"] == pytest.approx(0.0)
    assert release["limited_b"] == pytest.approx(0.0)

    # The limited unit runs at 3 / 2 = 1.5, so it asks for and takes 1.5 of b
    quantities = the_run["release_quantities"]
    assert quantities["limited_draws_a"] == pytest.approx(CD_SCARCE, rel=1e-6)
    assert quantities["limited_demand_b"] == pytest.approx(1.5, rel=1e-6)
    assert quantities["limited_draws_b"] == pytest.approx(1.5, rel=1e-6)


def test_the_release_still_fires_where_the_estimate_is_optimistic(the_run):
    """A derating is applied in the production sweep alone, by design (R-13).

    The capability sweep works on the rule's declared coefficients, exactly as
    the demand sweep does -- an existing scope boundary this fix deliberately
    does not move. So a derated component still ASKS for its nominal share and
    hands the surplus straight back, which is the invariant
    ``release_unused_supply`` enforces at the point where the scale is finally
    known.
    """
    assert the_run["release"]["derated_q"] > 0.0

    quantities = the_run["release_quantities"]

    # It asks without bound -- nothing else constrains a single-input rule --
    # so its supplier offers the whole of its rate ...
    assert quantities["derated_demand_q"] > CD_PLENTY

    # ... and it draws the quarter of that rate the derating left of it, handing
    # the other three quarters back.
    assert quantities["derated_draws_q"] == pytest.approx(
        CD_PLENTY * CD_DERATING, rel=1e-4
    )
    assert quantities["derated_draws_q"] < CD_PLENTY


# ----------------------------------------------------------------------
# The torn cycle (R-14)
# ----------------------------------------------------------------------


def test_a_capacity_broken_cycle_still_builds_and_circulates(the_run):
    """R-14 unchanged, and the tear is EXACT on the capability channel.

    Both ways :func:`muscadet.ordering.capacity_breaks_inbound` can hold put a
    volume between what arrives and what leaves, and this sweep asks the volume
    rather than the connection -- so the dropped edge carries no capability at
    all, rather than carrying a stale one.
    """
    assert the_run["torn"], "the recirculation loop was not torn"

    loop = the_run["loop"]

    # The tank is stocked, so it could serve without bound; the pump is a
    # pass-through and carries that across.
    assert math.isinf(loop["tank_cap"])
    assert math.isinf(loop["pump_cap"])

    # ... and the loop ran rather than being refused
    assert loop["time"] == pytest.approx(CD_CLOCK)
    assert loop["level"] == pytest.approx(CD_LOOP_CONTENT, rel=1e-6)


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
