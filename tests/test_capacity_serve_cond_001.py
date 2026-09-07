"""A boolean signal commands a capacity's discharge (R49).

A control signal could gate what a component PRODUCES (R44) and nothing else.
What leaves a capacity is not production, it is stock, so the gate reached the
volume's charge and never its discharge: a battery whose command stood at false
went on delivering. The production condition's own docstring named the gap and
the remedy in the same breath, "to stop the delivery too, gate what DRAWS from
the capacity, not only what fills it".

``serve_cond`` is that gate, written in the operand vocabulary R44 already uses:
the same shapes, the same conjunctive-normal shape, the same comparison
grammar, resolved by the same implementation. What differs is only what the
verdict does.

**It composes with the ceiling by BRANCHING, never by product.** ``serve_rate``
defaults to ``math.inf`` and ``inf * 0`` is NaN, which would poison every level
downstream of the volume rather than stopping it. False gives zero, true gives
the ceiling.

**A commanded halt is a halt of the DISCHARGE, not of the volume.** Charging
stays available while the discharge is off, which is what a battery under a
load-shedding order does, and the capability announces zero so that nothing
downstream sizes itself as though the volume were pouring.

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

#: What the battery holds at t=0, in a volume large enough that no scenario
#: reaches either bound over its horizon.
CSC_VOLUME = 1000.0
CSC_INIT = 500.0

#: The ceiling the commanded scenarios declare, and the demand they stand
#: behind. The demand is above the ceiling so a delivery equal to the ceiling
#: can only come from the ceiling.
CSC_SERVE = 40.0
CSC_DEMAND = 100.0

#: What the charging scenario's source offers and what the volume claims for
#: itself while its discharge is commanded off.
CSC_FILL = 12.0

#: How far each scenario is driven, and by what dated transition.
CSC_HORIZON = 4.0

#: The level a discharge command reads to stop itself: a reserve floor.
CSC_FLOOR = 100.0

#: Far enough past the crossing that the floor is REACHED inside the observed
#: window. At :data:`CSC_HORIZON` the level arrives at the floor exactly at the
#: horizon, so the gate never flips and a test would pass with the watched
#: automaton deleted.
CSC_LONG_HORIZON = 10.0

#: What the settled level may sit below the floor by. Measured: 0.042917 with
#: the crossing watched, 0.5 without it, the integration step being what a
#: threshold nothing watches is noticed one of.
CSC_OVERSHOOT = 0.1


class CscHorizon(muscadet.ObjFlow):
    """A dated transition, so the interactive session has somewhere to go."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_atm2states(
            name="horizon",
            occ_law_12={"cls": "delay", "time": CSC_HORIZON},
            cond_occ_21=False,
        )


class CscCommander(muscadet.ObjFlow):
    """Publishes one discrete signal, held at whatever it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(
            name="supply", var_prod_default=bool(kwargs.get("emit", True))
        )


class CscMeteredBattery(muscadet.ObjFlow):
    """A volume whose discharge is commanded by a threshold on its own level.

    The comparison half of the vocabulary, on the one quantity a capacity can
    threshold without closing any loop: an INTEGRATED level, read over a
    measurement link. A reserve floor is what a real battery management system
    enforces, and it is the sanctioned shape of F4/AE18.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="reserve")
        self.add_flow_continuous_in(name="elec", var_demand_default=0.0)
        self.add_flow_continuous_out(name="elec")
        self.add_capacity(
            name="store",
            flow="elec",
            side="out",
            capacity=CSC_VOLUME,
            content_init={"elec": float(kwargs.get("init", CSC_INIT))},
            serve_cond=[{"name": "reserve", "op": ">=", "value": CSC_FLOOR}],
        )


class CscLongHorizon(muscadet.ObjFlow):
    """The same dated transition, far enough out to cross the reserve floor."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_atm2states(
            name="horizon",
            occ_law_12={"cls": "delay", "time": CSC_LONG_HORIZON},
            cond_occ_21=False,
        )


class CscProbe(muscadet.ObjFlow):
    """Reads a level and republishes it (R37).

    PyCATSHOO refuses a component wired to itself, so a volume thresholding its
    own level needs an instrument between the two ends. That is not a test
    artefact: a battery management system IS a separate instrument, and giving
    it a component of its own is what lets a failure mode make it lie.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="store")
        self.add_measurement_out(name="reserve", source="store")


class CscBattery(muscadet.ObjFlow):
    """A volume whose discharge a boolean input commands.

    The operands of a discharge command name what the component already
    carries, so the command PORT is declared by whoever declares the
    component: here a subclass, in a generated model a spec
    (``muscadet.declare``). ``CapacityContinuous`` carries the key and not the
    port, which is why this montage is written out rather than parametrised on
    the shipped class.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="supply", logic="and")
        self.add_flow_continuous_in(name="elec", var_demand_default=CSC_DEMAND)
        self.add_flow_continuous_out(name="elec")
        self.add_capacity(
            name="store",
            flow="elec",
            side="out",
            capacity=CSC_VOLUME,
            content_init={"elec": CSC_INIT},
            serve_cond=["supply"],
            **{
                key: value
                for key, value in kwargs.items()
                if key in ("serve_rate", "fill_rate")
            },
        )


def build_battery_system(name, emit, **capacity_params):
    """A commanded battery feeding one load, behind a commander."""
    system = muscadet.System(name=name)

    system.add_component(name="BAT", cls="CscBattery", **capacity_params)
    system.add_component(name="CTRL", cls="CscCommander", emit=emit)
    system.add_component(
        name="LOAD", cls="ConsumerContinuous", flow="elec", demand=CSC_DEMAND
    )
    system.add_component(name="H", cls="CscHorizon")

    system.connect_flow(source="BAT", target="LOAD", flow_name="elec")
    system.connect_flow(source="CTRL", target="BAT", flow_name="supply")

    return system


def observe(system, obs, prefix, comp="BAT", flow="elec", cap="store"):
    """Drive to the horizon and record what the volume delivered and announced.

    Stepped in a bounded loop rather than once, because a comparison operand on
    a continuous quantity gains a WATCHED automaton that settles at t=0: the
    first ``isimu_step_forward`` spends itself on that instantaneous transition
    and the dated horizon is only reached on the next. A montage carrying no
    comparison reaches it on the first, and the loop stops there.
    """
    target = system.comp[comp]

    system.isimu_start()

    for _ in range(4):
        system.isimu_step_forward()
        if system.currentTime() >= CSC_HORIZON:
            break

    # Read BEFORE the session is stopped, and after the steps: a capability is
    # published by an equation and the engine samples instant 0 before it
    # evaluates any of them, while a verdict read past ``isimu_stop`` is read
    # off variables the engine has put back to their declared initial values.
    obs[f"{prefix}_holds"] = target.capacities[cap].serve_holds()
    obs[f"{prefix}_delivered"] = target.flows_out[flow].var_fed.value()
    obs[f"{prefix}_capability"] = target.flows_out[flow].var_capability.value()
    obs[f"{prefix}_level"] = target.capacities[cap].get_quantity(flow)
    obs[f"{prefix}_time"] = system.currentTime()

    system.isimu_stop()


def run_commanded_scenarios(obs):
    """The same battery, commanded on and commanded off."""
    for emit, prefix in ((True, "on"), (False, "off")):
        system = build_battery_system(
            f"CscCommanded{prefix}", emit, serve_rate=CSC_SERVE
        )
        try:
            observe(system, obs, prefix)
        finally:
            system.deleteSys()


def run_unbounded_ceiling_scenario(obs):
    """No ceiling declared, command false: the branch that would be NaN.

    ``serve_rate`` is ``math.inf`` here, so a composition by product would give
    ``inf * 0`` and every level downstream would carry NaN from the first
    integration step onward. The composition branches instead.
    """
    system = build_battery_system("CscUnboundedCeiling", False)
    try:
        observe(system, obs, "nonan")
    finally:
        system.deleteSys()


def run_charging_scenario(obs):
    """The discharge is commanded off; the charge is not.

    A volume claims its ``fill_rate`` for itself whatever its discharge is
    told to do, so a battery under a load-shedding order keeps accepting what
    its producer offers.
    """
    system = build_battery_system(
        "CscCharging", False, fill_rate=CSC_FILL, serve_rate=CSC_SERVE
    )
    try:
        system.add_component(
            name="GRID", cls="SourceContinuous", flow="elec", rate=CSC_DEMAND
        )
        system.connect_flow(source="GRID", target="BAT", flow_name="elec")

        observe(system, obs, "charging")
    finally:
        system.deleteSys()


def run_undeclared_scenario(obs):
    """No command at all: what every existing model does today.

    Built from the shipped ``CapacityContinuous``, which is also where the
    declaration key is checked: it accepts ``serve_cond`` and needs none.
    """
    system = muscadet.System(name="CscUndeclared")
    try:
        system.add_component(
            name="BAT",
            cls="CapacityContinuous",
            flow="elec",
            capacity=CSC_VOLUME,
            capacity_name="store",
            content_init={"elec": CSC_INIT},
            serve_rate=CSC_SERVE,
            demand=CSC_DEMAND,
        )
        system.add_component(
            name="LOAD", cls="ConsumerContinuous", flow="elec", demand=CSC_DEMAND
        )
        system.add_component(name="H", cls="CscHorizon")
        system.connect_flow(source="BAT", target="LOAD", flow_name="elec")

        store = system.comp["BAT"].capacities["store"]
        obs["undeclared_cond"] = list(store.serve_cond)
        obs["undeclared_holds"] = store.serve_holds()

        observe(system, obs, "undeclared")
    finally:
        system.deleteSys()


def run_metered_scenario(obs):
    """A comparison operand, on the level the volume itself publishes.

    Driven twice: from a stock above the reserve floor, and from one below it.
    """
    for init, prefix in ((CSC_INIT, "above"), (CSC_FLOOR / 2, "below")):
        system = muscadet.System(name=f"CscMetered{prefix}")
        try:
            system.add_component(name="BAT", cls="CscMeteredBattery", init=init)
            system.add_component(name="BMS", cls="CscProbe")
            system.add_component(
                name="LOAD", cls="ConsumerContinuous", flow="elec", demand=CSC_DEMAND
            )
            system.add_component(name="H", cls="CscHorizon")
            system.connect_flow(source="BAT", target="LOAD", flow_name="elec")
            system.connect("BAT", "store_level_out", "BMS", "store_level_in")
            system.connect("BMS", "reserve_level_out", "BAT", "reserve_level_in")

            obs[f"{prefix}_automata"] = [
                name
                for name in system.comp["BAT"].automata_d
                if "store_cond" in name or "threshold" in name
            ]

            observe(system, obs, prefix)
        finally:
            system.deleteSys()


def run_crossing_scenario(obs):
    """Driven PAST the reserve floor, which is where the automaton earns itself.

    The short-horizon scenario reaches the floor exactly at its horizon, so the
    gate never flips inside the window and the watched automaton could be
    deleted without a test noticing. Here the volume runs into the floor and
    settles on it, and how far past it settles is the whole measurement.
    """
    system = muscadet.System(name="CscCrossing")
    try:
        system.add_component(name="BAT", cls="CscMeteredBattery", init=CSC_INIT)
        system.add_component(name="BMS", cls="CscProbe")
        system.add_component(
            name="LOAD", cls="ConsumerContinuous", flow="elec", demand=CSC_DEMAND
        )
        system.add_component(name="H", cls="CscLongHorizon")
        system.connect_flow(source="BAT", target="LOAD", flow_name="elec")
        system.connect("BAT", "store_level_out", "BMS", "store_level_in")
        system.connect("BMS", "reserve_level_out", "BAT", "reserve_level_in")

        store = system.comp["BAT"].capacities["store"]

        system.isimu_start()
        for _ in range(6):
            system.isimu_step_forward()
            if system.currentTime() >= CSC_LONG_HORIZON:
                break

        obs["crossing_time"] = system.currentTime()
        obs["crossing_level"] = store.get_quantity("elec")
        obs["crossing_holds"] = store.serve_holds()
        obs["crossing_delivered"] = system.comp["BAT"].flows_out["elec"].var_fed.value()

        system.isimu_stop()
    finally:
        system.deleteSys()


def run_default_fill_scenario(obs):
    """A commanded halt at the DEFAULT ``fill_rate``, with a live producer.

    The charging scenario declares a fill rate, so it says nothing about the
    default. It has to be said: the demand a volume carries upstream is capped
    by what it may release (R48), so at ``fill_rate=0`` a commanded halt stops
    the inflow as well. That is the documented meaning of a pure pass-through
    buffer and not a defect, but it is the opposite of "charging stays
    available" read without the qualifier.
    """
    system = build_battery_system("CscDefaultFill", False)
    try:
        system.add_component(
            name="GRID", cls="SourceContinuous", flow="elec", rate=CSC_DEMAND
        )
        system.connect_flow(source="GRID", target="BAT", flow_name="elec")

        observe(system, obs, "defaultfill")
    finally:
        system.deleteSys()


def run_kb_key_scenario(obs):
    """The shipped class declares the port and names it in the condition.

    ``control`` declares a discrete input and gates nothing by itself;
    ``serve_cond`` names it. Written the other way -- a comparison on one of
    the class's own continuous flows -- the operand would threshold a RATE,
    which is the shape the loop detectors do not yet see (#172) and therefore
    not what a shipped example should show.
    """
    system = muscadet.System(name="CscKbKey")
    try:
        system.add_component(
            name="TANK",
            cls="CapacityContinuous",
            flow="elec",
            capacity=CSC_VOLUME,
            capacity_name="store",
            content_init={"elec": CSC_INIT},
            demand=CSC_DEMAND,
            control="cmd",
            serve_cond=["cmd"],
        )
        store = system.comp["TANK"].capacities["store"]

        obs["kb_port"] = "cmd" in system.comp["TANK"].flows_in
        # The port is UNFED, so the command does not hold: a gate that pinned
        # nothing would answer the same either way.
        obs["kb_holds"] = store.serve_holds()
        obs["kb_ceiling"] = store.serve_ceiling("elec")

        # A command on a volume with no way out is refused by name, exactly as
        # a ceiling on one is: an accumulator releases nothing, so nothing
        # would ever read the condition.
        try:
            system.add_component(
                name="ACC",
                cls="CapacityContinuous",
                flow="elec",
                capacity=CSC_VOLUME,
                capacity_name="acc",
                ports="in",
                demand=CSC_DEMAND,
                serve_cond=[{"name": "elec", "port": "in", "op": ">=", "value": 0.0}],
            )
            obs["accumulator_error"] = None
        except ValueError as err:
            obs["accumulator_error"] = err
    finally:
        system.deleteSys()


def run_spec_round_trip(obs):
    """The KB key survives a dump and a rebuild, resolved form and all."""
    system = build_battery_system("CscSpec", True, serve_rate=CSC_SERVE)
    try:
        spec = declare.component_spec(system.comp["BAT"])
        obs["spec"] = spec
        obs["spec_capacity"] = spec["capacities"][0]
        try:
            json.dumps(spec, allow_nan=False)
            obs["spec_strict_json"] = None
        except ValueError as err:
            obs["spec_strict_json"] = err
    finally:
        system.deleteSys()

    system = muscadet.System(name="CscSpecRebuild")
    try:
        rebuilt = declare.build_component(system, obs["spec"])
        store = rebuilt.capacities["store"]
        obs["rebuilt_cond"] = [
            [operand.name for operand in group] for group in store.serve_cond
        ]
        obs["rebuilt_spec_capacity"] = declare.component_spec(rebuilt)["capacities"][0]
    finally:
        system.deleteSys()


@pytest.fixture(scope="module")
def the_run():
    """Every scenario, built, driven and deleted in turn."""
    obs = {}

    run_commanded_scenarios(obs)
    run_unbounded_ceiling_scenario(obs)
    run_charging_scenario(obs)
    run_undeclared_scenario(obs)
    run_metered_scenario(obs)
    run_crossing_scenario(obs)
    run_default_fill_scenario(obs)
    run_kb_key_scenario(obs)
    run_spec_round_trip(obs)

    return obs


# ----------------------------------------------------------------------
# The command
# ----------------------------------------------------------------------


def test_a_battery_commanded_off_delivers_nothing(the_run):
    """The montage the whole unit exists for.

    A production condition reaches what a component PRODUCES; what leaves a
    volume is stock, so the same signal left the discharge untouched and the
    battery went on delivering while its command stood at false.
    """
    assert the_run["off_holds"] is False
    assert the_run["off_delivered"] == pytest.approx(0.0)


def test_a_battery_commanded_on_delivers_its_ceiling(the_run):
    """And the command is a command, not a permanent stop."""
    assert the_run["on_holds"] is True
    assert the_run["on_delivered"] == pytest.approx(CSC_SERVE)


def test_the_level_holds_while_the_discharge_is_commanded_off(the_run):
    """Not only the reported rate: nothing actually leaves the volume."""
    assert the_run["off_time"] == pytest.approx(CSC_HORIZON)
    assert the_run["off_level"] == pytest.approx(CSC_INIT, rel=1e-4)
    assert the_run["on_level"] == pytest.approx(
        CSC_INIT - CSC_SERVE * CSC_HORIZON, rel=1e-4
    )


def test_a_battery_commanded_off_announces_nothing(the_run):
    """R-20: a volume told to stop must not have consumers sized on its stock."""
    assert the_run["off_capability"] == pytest.approx(0.0)
    assert the_run["on_capability"] == pytest.approx(CSC_SERVE)


# ----------------------------------------------------------------------
# How it composes with the ceiling
# ----------------------------------------------------------------------


def test_an_unbounded_ceiling_and_a_false_command_give_zero_not_nan(the_run):
    """The composition is a branch, and the alternative is not a near miss.

    ``serve_rate`` is ``math.inf`` by default, so a product would give
    ``inf * 0`` -- NaN -- and NaN propagates: every level downstream of the
    volume would carry it from the first integration step, with nothing raised
    anywhere.
    """
    delivered = the_run["nonan_delivered"]
    capability = the_run["nonan_capability"]

    assert delivered == delivered, "a NaN delivery is what a product would give"
    assert capability == capability, "a NaN capability likewise"
    assert delivered == pytest.approx(0.0)
    assert capability == pytest.approx(0.0)
    assert the_run["nonan_level"] == pytest.approx(CSC_INIT, rel=1e-4)


def test_charging_survives_a_commanded_halt_of_the_discharge(the_run):
    """A commanded halt stops the way out, not the way in.

    The volume claims its ``fill_rate`` for itself whatever its discharge is
    told to do, so a battery under a load-shedding order keeps taking what its
    producer offers while delivering nothing.
    """
    assert the_run["charging_delivered"] == pytest.approx(0.0)
    assert the_run["charging_level"] == pytest.approx(
        CSC_INIT + CSC_FILL * CSC_HORIZON, rel=1e-3
    )


# ----------------------------------------------------------------------
# The comparison vocabulary of R44
# ----------------------------------------------------------------------


def test_a_threshold_on_an_integrated_level_commands_the_discharge(the_run):
    """The comparison half, on the quantity a capacity may safely threshold.

    A reserve floor: the volume serves while its level is above it and stops
    below. The level is INTEGRATED, so nothing here closes an instantaneous
    loop -- which is exactly why F4/AE18 sanctions this shape and refuses the
    same threshold on a rate.
    """
    assert the_run["above_delivered"] > 0.0
    assert the_run["below_delivered"] == pytest.approx(0.0)


def test_a_continuous_comparison_gets_a_watched_automaton(the_run):
    """The crossing is stopped on, not noticed at the following step (R22)."""
    assert the_run["above_automata"], "a comparison on a level must be watched"
    assert the_run["below_automata"] == the_run["above_automata"]


def test_the_floor_is_stopped_on_rather_than_crossed_late(the_run):
    """What the watched automaton actually buys, in the one unit that says it.

    A level moving inside an integration step announces no change of its own,
    so a floor nothing watched is noticed at the following step and the volume
    is already past it. Measured on this montage: the level settles
    **0.042917** below the floor with the crossing watched and **0.5** below it
    with ``add_serve_cond_automata`` stubbed out, an order of magnitude that a
    presence-of-automaton assertion cannot see.
    """
    assert the_run["crossing_time"] == pytest.approx(CSC_LONG_HORIZON)
    assert (
        the_run["crossing_holds"] is False
    ), "the floor must be reached INSIDE the window, or this measures nothing"
    assert the_run["crossing_delivered"] == pytest.approx(0.0)

    overshoot = CSC_FLOOR - the_run["crossing_level"]

    assert 0.0 <= overshoot < CSC_OVERSHOOT, (
        f"settled {overshoot} below the floor; an unwatched crossing settles "
        f"at one integration step's worth, measured at 0.5"
    )


def test_a_commanded_halt_at_the_default_fill_rate_stops_the_inflow_too(the_run):
    """The qualifier "charging stays available" needs, stated rather than implied.

    A volume carries upstream only what it may release plus what it claims for
    itself (R48, R-20), so at the documented default ``fill_rate=0`` -- a pure
    pass-through buffer that never stocks up -- a commanded halt stops the way
    in as well. Charging through a halt is available, and it is DECLARED: it
    takes a fill rate, which the scenario above shows working.
    """
    assert the_run["defaultfill_delivered"] == pytest.approx(0.0)
    assert the_run["defaultfill_level"] == pytest.approx(CSC_INIT, rel=1e-4)


# ----------------------------------------------------------------------
# What an undeclared command leaves alone
# ----------------------------------------------------------------------


def test_an_undeclared_command_holds_and_changes_nothing(the_run):
    """An empty condition holds, as an empty production condition does."""
    assert the_run["undeclared_cond"] == []
    assert the_run["undeclared_holds"] is True
    assert the_run["undeclared_delivered"] == pytest.approx(CSC_SERVE)
    assert the_run["undeclared_capability"] == pytest.approx(CSC_SERVE)


# ----------------------------------------------------------------------
# Declaration
# ----------------------------------------------------------------------


def test_the_kb_carries_the_command_and_a_spec_restores_it(the_run):
    """``CapacityContinuous`` accepts it, and a round trip keeps it.

    The condition is stored RESOLVED, the operand names replaced by the flow
    objects, so it cannot be dumped as it stands: a spec walks it back to the
    canonical operand form exactly as it does for a production condition.
    """
    assert the_run["spec_capacity"]["serve_cond"] == [
        [{"name": "supply", "port": "in"}]
    ]
    assert the_run["kb_port"] is True
    assert the_run["kb_holds"] is False
    assert the_run["kb_ceiling"] == pytest.approx(0.0)
    # The two derived matrices are recomputed by the rebuild; a stale copy
    # beside a rebuilt condition is worse than none.
    assert "serve_cond_negate" not in the_run["spec_capacity"]
    assert "serve_cond_compare" not in the_run["spec_capacity"]
    assert the_run["spec_strict_json"] is None, str(the_run["spec_strict_json"])

    assert the_run["rebuilt_cond"] == [["supply"]]
    assert the_run["rebuilt_spec_capacity"] == the_run["spec_capacity"]


def test_a_command_on_a_volume_with_no_way_out_is_refused(the_run):
    """An accumulator releases nothing, so a command on it would be inert.

    The same refusal a ceiling on one gets, and for the same reason: a
    declaration nothing reads is the class ``DECLARATION_KEYS`` (R-3) exists to
    refuse by name.
    """
    error = the_run["accumulator_error"]

    assert error is not None, "a command nothing can read must be refused"
    assert "releases nothing" in str(error)
    assert "serve_cond" in str(error)


def test_delete():
    cod3s.terminate_session()
