"""A capacity declares the rate it releases at most (R48).

Nothing bounded what a volume let out. A reservoir asked for 100 served 100
while it held anything, and a battery's ``flow_nominal`` had no declarable
equivalent: ``fill_rate`` exists for the way in and had no counterpart for the
way out.

**It is a CEILING, not a claim, and the two must not be read as a pair.**
``fill_rate`` is what a volume asks FOR ITSELF, over and above the demand
passing through it, so it makes a tank fill. ``serve_rate`` asks for nothing:
it caps what leaves. Naming them symmetrically (``drain_rate``) would invite
exactly the wrong reading, so the name is taken from the quantity the code
already bounds, ``serve_limit``.

**Two clamp points, one ceiling.** The natural one is the demand seen at the
output, where a rate is bounded by a rate. The other is the service bound
itself, on BOTH its branches: what a stocked volume offers, and what transits
an empty one. Restricting it to the stocked branch was written first and
measured wrong, and this module pins the measurement: an empty volume then
passed on more than its rating whenever the demand was unbounded, while the
capability sweep announced the rating. A rating a state of charge can suspend
is not a rating.

**What it is a ceiling ON is what LEAVES, never what arrives.** Two inversions
follow, and both are pinned here because both shipped green in a first cut. A
declared maximum must not become a floor: an unbounded demand still moves only
what arrives, and the identity transfer chooses that fallback on the DEMAND
rather than on the resulting number, a ceiling having made the number finite.
And a buffer must not become an accumulator: the demand carried upstream is
capped too, exactly as R-20 already ruled for the input side, or a volume at
``fill_rate=0`` -- documented as never stocking up -- would pile up the
difference between what it asks for and what it may release.

The stock draw is NOT a clamp point, and that is measured elsewhere
(``tests/test_capacity_bound_clamp_001.py``): capping a finite request there
compares a rate with a quantity, and the implicit "per unit of time" that makes
such a comparison typecheck once fixed the physics of every discharge.

The ceiling also bounds the **capability** (R-20): a volume commanded to a
trickle that still announced an unlimited capability would have every consumer
downstream sized as though it were pouring.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, driven, inspected and deleted before the next one starts; the fixture
snapshots what each produced.
"""

import json
import math

import cod3s
import pytest

import muscadet
from muscadet import declare

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

#: What the reservoir of every scenario holds at t=0. Large enough that no
#: scenario empties it over its horizon, so the service bound stays on its
#: unbounded branch and the ceiling is the only thing capping anything.
CSR_VOLUME = 1000.0
CSR_INIT = 500.0

#: The ceiling every declaring scenario is built with.
CSR_SERVE = 40.0

#: What a consumer asks for: strictly above the ceiling, so a delivery equal to
#: the ceiling can only come from the ceiling.
CSR_DEMAND = 100.0

#: A supply well below the ceiling, so that a ceiling turning into a floor
#: shows up as a rate going UP when a maximum is declared.
CSR_TRICKLE = 5.0

#: How far each scenario is driven, and by what dated transition.
CSR_HORIZON = 4.0


class CsrHorizon(muscadet.ObjFlow):
    """A dated transition, so the interactive session has somewhere to go.

    A purely continuous model never moves: ``stepForward`` advances to the next
    DATED transition and integrates on the way, so without one the clock sits
    at zero and every level reports its declared initial value.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_atm2states(
            name="horizon",
            occ_law_12={"cls": "delay", "time": CSR_HORIZON},
            cond_occ_21=False,
        )


class CsrBufferedPipe(muscadet.ObjFlow):
    """A stocked pass-through: one flow in and out, buffered on the way in.

    No rule, so what crosses is the identity transfer of R31, which is the path
    the "nothing bounds this" sentinel lives on.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=CSR_DEMAND)
        self.add_flow_continuous_out(name="q")
        self.add_capacity(
            name="buf",
            flow="q",
            side="in",
            capacity=CSR_VOLUME,
            content_init={"q": CSR_INIT},
            serve_rate=kwargs.get("serve_rate", math.inf),
        )


class CsrBufferedMill(muscadet.ObjFlow):
    """A rule fed through an INPUT-side capacity carrying the ceiling.

    The ceiling is on ``Capacity``, which has a side, so declaring it on the
    way in has to mean something. It does: an input volume releases into the
    rules exactly as an output one releases onto a connection, and
    ``get_input_available`` reaches it through the same ``serve_limit``.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a", var_demand_default=CSR_DEMAND)
        self.add_flow_continuous_out(name="q")
        self.add_rules(
            name="mill", rules=[dict(name="run", cons={"a": 1.0}, prod={"q": 1.0})]
        )
        self.add_capacity(
            name="hopper",
            flow="a",
            side="in",
            capacity=CSR_VOLUME,
            content_init={"a": CSR_INIT},
            serve_rate=kwargs.get("serve_rate", math.inf),
        )


def build_reservoir_system(name, serve_rate=None, demand=CSR_DEMAND, init=CSR_INIT):
    """A reservoir feeding one consumer, with the ceiling declared or not."""
    system = muscadet.System(name=name)

    params = dict(
        flow="q",
        capacity=CSR_VOLUME,
        capacity_name="tank",
        content_init={"q": init},
        ports="out",
    )
    if serve_rate is not None:
        params["serve_rate"] = serve_rate

    system.add_component(name="TANK", cls="CapacityContinuous", **params)
    system.add_component(name="LOAD", cls="ConsumerContinuous", flow="q", demand=demand)
    system.add_component(name="H", cls="CsrHorizon")
    system.connect_flow(source="TANK", target="LOAD", flow_name="q")

    return system


def observe(system, obs, prefix):
    """Drive one step and record what the tank delivered and announced."""
    tank = system.comp["TANK"]
    flow = tank.flows_out["q"]

    system.isimu_start()
    system.isimu_step_forward()

    # Read AFTER the step: the capability is published by an equation, and the
    # engine samples instant 0 before it evaluates any of them, so a read at
    # the seed returns the variable's declared default and nothing else.
    obs[f"{prefix}_capability"] = flow.var_capability.value()
    obs[f"{prefix}_time"] = system.currentTime()
    obs[f"{prefix}_delivered"] = flow.var_fed.value()
    obs[f"{prefix}_level"] = tank.capacities["tank"].get_quantity("q")

    system.isimu_stop()


def run_bounded_demand_scenario(obs):
    """A ceiling of 40 behind a demand of 100."""
    system = build_reservoir_system("CsrBounded", serve_rate=CSR_SERVE)
    try:
        observe(system, obs, "bounded")
    finally:
        system.deleteSys()


def run_unbounded_demand_scenario(obs):
    """The same ceiling behind a demand of ``inf``."""
    system = build_reservoir_system(
        "CsrUnbounded", serve_rate=CSR_SERVE, demand=math.inf
    )
    try:
        observe(system, obs, "unbounded")
    finally:
        system.deleteSys()


def run_undeclared_scenario(obs):
    """No ceiling declared: what every existing model does today."""
    system = build_reservoir_system("CsrUndeclared")
    try:
        tank = system.comp["TANK"].capacities["tank"]

        obs["undeclared_rate"] = tank.serve_rate
        obs["undeclared_var"] = tank.var_serve_rate["q"].value()

        observe(system, obs, "undeclared")
    finally:
        system.deleteSys()


def run_empty_scenario(obs):
    """An EMPTY capacity, with no ceiling: the branch left deliberately alone."""
    system = build_reservoir_system("CsrEmpty", init=0.0)
    try:
        observe(system, obs, "empty")
    finally:
        system.deleteSys()


def run_transit_scenarios(obs):
    """An empty capacity under a ceiling, fed below it and above it.

    Below, the ceiling must not become a floor; above, it must hold, and the
    demand is driven both finite and unbounded because those are two different
    paths to the same volume and a first cut had them disagreeing.
    """
    cases = (
        ("under", CSR_SERVE / 2, CSR_DEMAND),
        ("over", CSR_DEMAND, CSR_DEMAND),
        ("overfree", CSR_DEMAND, math.inf),
    )

    for prefix, rate, demand in cases:
        system = muscadet.System(name=f"CsrTransit{prefix}")
        try:
            system.add_component(
                name="SRC", cls="SourceContinuous", flow="q", rate=rate
            )
            system.add_component(
                name="TANK",
                cls="CapacityContinuous",
                flow="q",
                capacity=CSR_VOLUME,
                capacity_name="tank",
                content_init={"q": 0.0},
                serve_rate=CSR_SERVE,
                demand=CSR_DEMAND,
            )
            system.add_component(
                name="LOAD", cls="ConsumerContinuous", flow="q", demand=demand
            )
            system.add_component(name="H", cls="CsrHorizon")
            system.connect_flow(source="SRC", target="TANK", flow_name="q")
            system.connect_flow(source="TANK", target="LOAD", flow_name="q")

            observe(system, obs, prefix)
        finally:
            system.deleteSys()


def run_unbounded_drain_scenario(obs):
    """A stocked pass-through behind a consumer asking without bound.

    The inversion: an unbounded demand asked for nothing in particular, so what
    arrives is what moves. A first cut let a declared MAXIMUM of 40 take the
    transfer from 5 to 40 and start draining a volume the model conserved,
    because the "nothing bounds this" sentinel read ``math.isinf`` on a bound
    the ceiling had just made finite.
    """
    for rate, prefix in ((math.inf, "drainfree"), (CSR_SERVE, "draincapped")):
        system = muscadet.System(name=f"CsrDrain{prefix}")
        try:
            system.add_component(
                name="SRC", cls="SourceContinuous", flow="q", rate=CSR_TRICKLE
            )
            system.add_component(name="PIPE", cls="CsrBufferedPipe", serve_rate=rate)
            system.add_component(
                name="LOAD", cls="ConsumerContinuous", flow="q", demand=math.inf
            )
            system.add_component(name="H", cls="CsrHorizon")
            system.connect_flow(source="SRC", target="PIPE", flow_name="q")
            system.connect_flow(source="PIPE", target="LOAD", flow_name="q")

            pipe = system.comp["PIPE"]

            system.isimu_start()
            system.isimu_step_forward()

            obs[f"{prefix}_delivered"] = pipe.flows_out["q"].var_fed.value()
            obs[f"{prefix}_level"] = pipe.capacities["buf"].get_quantity("q")

            system.isimu_stop()
        finally:
            system.deleteSys()


def run_no_accumulation_scenario(obs):
    """A ``fill_rate=0`` buffer behind a ceiling must not stock up.

    R-20 already ruled it for the input side: a volume does not fill out of a
    demand it cannot honour. Uncapped, this volume asked its source for the
    whole downstream demand while only the ceiling could leave, and the
    difference piled up at 60 per unit of time.
    """
    system = muscadet.System(name="CsrNoAccumulation")
    try:
        system.add_component(
            name="SRC", cls="SourceContinuous", flow="q", rate=CSR_DEMAND
        )
        system.add_component(
            name="TANK",
            cls="CapacityContinuous",
            flow="q",
            capacity=CSR_VOLUME,
            capacity_name="tank",
            content_init={"q": CSR_INIT},
            serve_rate=CSR_SERVE,
            demand=CSR_DEMAND,
        )
        system.add_component(
            name="LOAD", cls="ConsumerContinuous", flow="q", demand=CSR_DEMAND
        )
        system.add_component(name="H", cls="CsrHorizon")
        system.connect_flow(source="SRC", target="TANK", flow_name="q")
        system.connect_flow(source="TANK", target="LOAD", flow_name="q")

        observe(system, obs, "buffer")
    finally:
        system.deleteSys()


def run_accumulator_refusal(obs):
    """A ceiling on a volume with no way out is refused rather than ignored."""
    system = muscadet.System(name="CsrAccumulator")
    try:
        system.add_component(
            name="ACC",
            cls="CapacityContinuous",
            flow="q",
            capacity=CSR_VOLUME,
            capacity_name="acc",
            ports="in",
            demand=CSR_DEMAND,
        )
        obs["accumulator_plain"] = "built"

        try:
            system.add_component(
                name="ACC2",
                cls="CapacityContinuous",
                flow="q",
                capacity=CSR_VOLUME,
                capacity_name="acc",
                ports="in",
                demand=CSR_DEMAND,
                serve_rate=CSR_SERVE,
            )
            obs["accumulator_error"] = None
        except ValueError as err:
            obs["accumulator_error"] = err
    finally:
        system.deleteSys()


def run_input_side_scenario(obs):
    """The ceiling on the way IN, capping what the rules may draw."""
    for rate, prefix in ((math.inf, "millfree"), (CSR_SERVE, "millcapped")):
        system = muscadet.System(name=f"CsrMill{prefix}")
        try:
            system.add_component(name="MILL", cls="CsrBufferedMill", serve_rate=rate)
            system.add_component(
                name="LOAD", cls="ConsumerContinuous", flow="q", demand=CSR_DEMAND
            )
            system.add_component(name="H", cls="CsrHorizon")
            system.connect_flow(source="MILL", target="LOAD", flow_name="q")

            mill = system.comp["MILL"]

            system.isimu_start()
            system.isimu_step_forward()

            obs[f"{prefix}_delivered"] = mill.flows_out["q"].var_fed.value()
            obs[f"{prefix}_hopper"] = mill.capacities["hopper"].get_quantity("a")

            system.isimu_stop()
        finally:
            system.deleteSys()


def run_clamped_scenario(obs):
    """A failure mode clamping the public variable BY NAME, with no muscadet call."""
    system = build_reservoir_system("CsrClamped", serve_rate=CSR_SERVE)
    try:
        system.comp["TANK"].add_atm2states(
            name="throttle",
            occ_law_12={"cls": "delay", "time": CSR_HORIZON / 2},
            effects_12=[("tank_serve_rate_q", CSR_SERVE / 4)],
            cond_occ_21=False,
        )

        tank = system.comp["TANK"]
        flow = tank.flows_out["q"]

        system.isimu_start()
        system.isimu_step_forward()

        obs["clamped_before"] = flow.var_fed.value()

        # Past the mode's own date: the clamp stands and the delivery follows.
        system.isimu_step_forward()

        obs["clamped_var"] = tank.capacities["tank"].var_serve_rate["q"].value()
        obs["clamped_after"] = flow.var_fed.value()

        system.isimu_stop()
    finally:
        system.deleteSys()


def run_spec_round_trip(obs):
    """The KB key survives a dump and a rebuild, and an undeclared one is absent."""
    system = build_reservoir_system("CsrSpecPlain")
    try:
        plain = declare.component_spec(system.comp["TANK"])
        obs["plain_capacity_spec"] = plain["capacities"][0]
        try:
            json.dumps(plain, allow_nan=False)
            obs["plain_strict_json"] = None
        except ValueError as err:
            obs["plain_strict_json"] = err
    finally:
        system.deleteSys()

    system = build_reservoir_system("CsrSpec", serve_rate=CSR_SERVE)
    try:
        spec = declare.component_spec(system.comp["TANK"])
        obs["spec"] = spec
        obs["spec_serve_rate"] = spec["capacities"][0].get("serve_rate")
    finally:
        system.deleteSys()

    system = muscadet.System(name="CsrSpecRebuild")
    try:
        rebuilt = declare.build_component(system, obs["spec"])
        obs["rebuilt_serve_rate"] = rebuilt.capacities["tank"].serve_rate
        obs["rebuilt_var"] = rebuilt.capacities["tank"].var_serve_rate["q"].value()
    finally:
        system.deleteSys()


@pytest.fixture(scope="module")
def the_run():
    """Every scenario, built, driven and deleted in turn."""
    obs = {}

    run_bounded_demand_scenario(obs)
    run_unbounded_demand_scenario(obs)
    run_undeclared_scenario(obs)
    run_empty_scenario(obs)
    run_transit_scenarios(obs)
    run_unbounded_drain_scenario(obs)
    run_no_accumulation_scenario(obs)
    run_accumulator_refusal(obs)
    run_input_side_scenario(obs)
    run_clamped_scenario(obs)
    run_spec_round_trip(obs)

    return obs


# ----------------------------------------------------------------------
# The ceiling
# ----------------------------------------------------------------------


def test_a_ceiling_of_40_behind_a_demand_of_100_delivers_40(the_run):
    """The natural clamp point: a rate bounded by a rate."""
    assert the_run["bounded_delivered"] == pytest.approx(CSR_SERVE)


def test_a_ceiling_of_40_behind_an_unbounded_demand_delivers_40(the_run):
    """The other clamp point: the branch that reports a stocked volume.

    "Deliver whatever you can" is answered from the stock, so without the
    ceiling on that branch a reservoir empties at whatever the consumer asks
    for, which is the whole of what an unbounded demand means.
    """
    assert the_run["unbounded_delivered"] == pytest.approx(CSR_SERVE)


def test_the_ceiling_is_what_the_level_falls_at(the_run):
    """Not only the reported rate: what leaves the volume really is capped."""
    expected = CSR_INIT - CSR_SERVE * CSR_HORIZON

    assert the_run["bounded_time"] == pytest.approx(CSR_HORIZON)
    assert the_run["bounded_level"] == pytest.approx(expected, rel=1e-4)


def test_the_capability_announces_the_ceiling(the_run):
    """R-20: a bound is computed by the function production honours.

    A volume announcing an unlimited capability while serving 40 would have
    every consumer downstream size its own claim as though it were pouring.
    """
    assert the_run["bounded_capability"] == pytest.approx(CSR_SERVE)
    assert the_run["unbounded_capability"] == pytest.approx(CSR_SERVE)


# ----------------------------------------------------------------------
# What an undeclared ceiling leaves alone
# ----------------------------------------------------------------------


def test_an_undeclared_ceiling_is_infinite(the_run):
    """The declared default and the variable created from it."""
    assert the_run["undeclared_rate"] == math.inf
    assert math.isinf(the_run["undeclared_var"])


def test_an_undeclared_ceiling_leaves_the_model_where_it_was(the_run):
    """The demand is served whole, and the capability stays unbounded."""
    assert the_run["undeclared_delivered"] == pytest.approx(CSR_DEMAND)
    assert math.isinf(the_run["undeclared_capability"])


def test_an_empty_capacity_is_untouched(the_run):
    """The branch the ceiling deliberately does not pass through.

    An empty volume serves what transits through it, and with nothing arriving
    that is zero. Routing this branch through the ceiling would change what an
    empty capacity does, which is not what a discharge ceiling is for.
    """
    assert the_run["empty_delivered"] == pytest.approx(0.0)
    assert the_run["empty_level"] == pytest.approx(0.0)


def test_an_empty_capacity_passes_its_transit_on_and_no_more(the_run):
    """A ceiling is a maximum, never a floor, and it holds when empty too.

    Below the ceiling, what leaves is the transit: there is no stock to serve
    from and a maximum does not top anything up. Above it, the ceiling holds,
    and it holds on both paths to the volume. The over-fed case with a finite
    demand and the one with an unbounded demand are the two that disagreed
    while the ceiling was on the stocked branch alone: 40 against 100, with the
    capability announcing 40 either way.
    """
    assert the_run["under_delivered"] == pytest.approx(CSR_SERVE / 2)
    assert the_run["over_delivered"] == pytest.approx(CSR_SERVE)
    assert the_run["overfree_delivered"] == pytest.approx(CSR_SERVE)

    for prefix in ("under", "over", "overfree"):
        assert the_run[f"{prefix}_capability"] == pytest.approx(
            the_run[f"{prefix}_delivered"]
        ), f"{prefix}: what is announced and what is delivered must agree"


def test_a_ceiling_does_not_drain_a_volume_it_was_meant_to_cap(the_run):
    """The inversion: declaring a MAXIMUM must not raise the discharge.

    An unbounded demand asked for nothing in particular, so a stocked
    pass-through moves what arrives and leaves its volume alone. Measured
    before the fix: 5 became 40 and the level started falling, because the
    sentinel for "nothing bounds this" was read off a number the ceiling had
    just made finite.
    """
    assert the_run["drainfree_delivered"] == pytest.approx(CSR_TRICKLE)
    assert the_run["draincapped_delivered"] == pytest.approx(CSR_TRICKLE)
    assert the_run["draincapped_level"] == pytest.approx(the_run["drainfree_level"])


def test_a_buffer_behind_a_ceiling_does_not_become_an_accumulator(the_run):
    """``fill_rate=0`` means "never stocks up", ceiling or no ceiling.

    A volume does not fill out of a demand it cannot honour, which is the rule
    R-20 wrote for the input side and this extends to the way out. Uncapped,
    this tank asked its source for 100 while only 40 could leave and rose by 60
    per unit of time.
    """
    assert the_run["buffer_delivered"] == pytest.approx(CSR_SERVE)
    assert the_run["buffer_level"] == pytest.approx(CSR_INIT, rel=1e-4)


def test_a_ceiling_on_a_volume_with_no_way_out_is_refused(the_run):
    """An accumulator releases nothing, so a ceiling on it would be inert.

    A declaration nothing reads is the class ``DECLARATION_KEYS`` (R-3) and
    ``check_declaration_keys`` (R-15) exist to refuse by name, and measured, it
    was exactly inert: the level was identical with and without.
    """
    assert the_run["accumulator_plain"] == "built"

    error = the_run["accumulator_error"]

    assert error is not None, "a ceiling nothing can read must be refused"
    assert "releases nothing" in str(error)
    assert "ports='both'" in str(error)


def test_the_ceiling_caps_an_input_side_capacity_too(the_run):
    """A ceiling declared on the way IN is not a silently dead declaration.

    ``Capacity`` carries a side, so ``serve_rate`` has to mean something on
    both. It does, and through the same ``serve_limit``: an input volume
    releases into the rules as an output one releases onto a connection. A
    hopper capped at 40 drains at 40 where it drained at whatever the rule
    could pass on.
    """
    assert the_run["millfree_delivered"] == pytest.approx(CSR_DEMAND)
    assert the_run["millcapped_delivered"] == pytest.approx(CSR_SERVE)

    drained = CSR_INIT - CSR_SERVE * CSR_HORIZON
    assert the_run["millcapped_hopper"] == pytest.approx(drained, rel=1e-4)
    assert the_run["millfree_hopper"] < the_run["millcapped_hopper"]


# ----------------------------------------------------------------------
# The public variable
# ----------------------------------------------------------------------


def test_a_failure_mode_clamps_the_ceiling_by_name(the_run):
    """The endpoint of KD10, on a capacity.

    ``{capacity}_serve_rate_{flow}`` exists before any mode is declared and
    muscadet never writes it, so anything targeting a component variable by
    name throttles the discharge with no muscadet-specific call.
    """
    assert the_run["clamped_before"] == pytest.approx(CSR_SERVE)
    assert the_run["clamped_var"] == pytest.approx(CSR_SERVE / 4)
    assert the_run["clamped_after"] == pytest.approx(CSR_SERVE / 4)


# ----------------------------------------------------------------------
# Declaration
# ----------------------------------------------------------------------


def test_the_kb_carries_the_key_and_a_spec_restores_it(the_run):
    """``CapacityContinuous`` accepts it, and a round trip keeps it."""
    assert the_run["spec_serve_rate"] == pytest.approx(CSR_SERVE)
    assert the_run["rebuilt_serve_rate"] == pytest.approx(CSR_SERVE)
    assert the_run["rebuilt_var"] == pytest.approx(CSR_SERVE)


def test_an_undeclared_ceiling_is_absent_from_the_spec(the_run):
    """A ceiling of ``inf`` says nothing, and writing it would say ``Infinity``.

    ``json`` emits the non-standard literal for a non-finite float, so dumping
    the default would have made EVERY capacity spec fail a strict reader,
    including those of models declared before the field existed. ``fill_rate``
    is the counter-example and stays in: its default is 0.0, so an infinite one
    is something a modeller wrote.
    """
    assert "serve_rate" not in the_run["plain_capacity_spec"]
    assert "fill_rate" in the_run["plain_capacity_spec"]
    assert the_run["plain_strict_json"] is None, str(the_run["plain_strict_json"])


def test_a_negative_or_nan_ceiling_is_refused():
    """Refused at declaration, where ``fill_rate`` is and for the same reason."""
    for value in (-1.0, float("nan")):
        with pytest.raises(ValueError, match="serve rate"):
            muscadet.Capacity(name="tank", flows=["q"], capacity=10.0, serve_rate=value)


def test_delete():
    cod3s.terminate_session()
