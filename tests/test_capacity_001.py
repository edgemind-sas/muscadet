"""Capacity declaration model: levels, fills, bounds, composition.

A capacity is a volume a component holds over one or more of its continuous
flows, declared **independently of its transformation rules** (KD14). This
module pins down what it reports (R28), how it integrates, how its bounds are
crossed (R7), how a withdrawal on it is composed (R35), and what it refuses at
declaration time (R4, R29).

The whole system is built, wired and driven once in the fixture, and every test
reads back a snapshot: PyCATSHOO forbids more than one live system per process,
and the interactive session must advance monotonically for the crossing dates to
mean anything.
"""

import muscadet
import cod3s
import pytest

# Analytic crossing dates of the two bounded tanks below. Both are reached long
# before the horizon clock, which is the whole point of watching the bounds.
DRAIN_EMPTY_DATE = 5.0
FILL_FULL_DATE = 8.0
HORIZON = 20.0

# The solver stops the integration on the crossing rather than refining it to
# machine precision, so the dates are asserted within one default PDMP step.
CROSSING_TOL = 0.05


class Mixer(muscadet.ObjFlow):
    """AE10: one volume shared by two flows occupying it at different weights."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="H2O")
        self.add_flow_continuous_in(name="additif")
        self.add_capacity(
            name="cuve",
            flows=[
                {"name": "H2O", "weight": 1},
                {"name": "additif", "weight": 2},
            ],
            capacity=100,
            side="in",
            content_init={"H2O": 40, "additif": 20},
        )


class Electrolyser(muscadet.ObjFlow):
    """AE22: two constituents at equal weight, drawn at their raw share."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="H2")
        self.add_flow_continuous_out(name="O2")
        self.add_capacity(
            name="tank",
            flows=["H2", "O2"],
            capacity=100,
            side="out",
            content_init={"H2": 30, "O2": 10},
        )


class WeightedElectrolyser(muscadet.ObjFlow):
    """Same contents, unequal weights: the split must not follow the volume."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="H2")
        self.add_flow_continuous_out(name="O2")
        self.add_capacity(
            name="tank",
            flows=[{"name": "H2", "weight": 1}, {"name": "O2", "weight": 3}],
            capacity=100,
            side="out",
            content_init={"H2": 30, "O2": 10},
        )


class Vessel(muscadet.ObjFlow):
    """A single-flow capacity, parameterised by volume and initial content."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="w")
        self.add_capacity(
            name="cuve",
            flow="w",
            capacity=kwargs.get("volume", 10),
            content_init={"w": kwargs.get("init", 0)},
        )


class WeightedVessel(Vessel):
    """Same volume and content, but each unit occupies twice the volume."""

    def add_flows(self, **kwargs):
        muscadet.ObjFlow.add_flows(self, **kwargs)
        self.add_flow_continuous_in(name="w")
        self.add_capacity(
            name="cuve",
            flows=[{"name": "w", "weight": 2}],
            capacity=kwargs.get("volume", 10),
            content_init={"w": kwargs.get("init", 0)},
        )


class DualSide(muscadet.ObjFlow):
    """One flow carried by both sides, buffered upstream AND downstream."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="w")
        self.add_flow_continuous_out(name="w")
        self.add_flow_continuous_in(name="feed")
        self.add_flow_continuous_out(name="dry")
        self.add_flow_in(name="switch", logic="and")
        self.add_capacity(name="up", flow="w", capacity=50, side="in")
        self.add_capacity(name="down", flow="w", capacity=50, side="out")


@pytest.fixture(scope="module")
def the_system():
    """Build every capacity scenario, then drive one interactive session."""

    system = muscadet.System(name="CapacitySys")

    # -- What a capacity reports, and how a draw on it is composed
    system.add_component(name="MIX", cls="Mixer")
    system.add_component(name="ELY", cls="Electrolyser")
    system.add_component(name="ELYW", cls="WeightedElectrolyser")
    system.add_component(name="VOID", cls="Vessel", volume=10, init=0)

    # -- Bounds: one tank empties at t = 5, one fills at t = 8
    system.add_component(name="DRAIN", cls="Vessel", volume=10, init=5)
    system.add_component(name="FILL", cls="Vessel", volume=10, init=2)

    # -- Bounds: what an empty / a full capacity can still serve and accept
    system.add_component(name="TRANSIT", cls="Vessel", volume=10, init=0)
    system.add_component(name="BRIM", cls="Vessel", volume=10, init=10)

    # -- Integration of the imbalance between what enters and what leaves
    system.add_component(name="BAL", cls="Vessel", volume=1000, init=10)

    # -- Weight governs how fast the volume fills
    system.add_component(name="LIGHT", cls="Vessel", volume=100, init=1)
    system.add_component(name="HEAVY", cls="WeightedVessel", volume=100, init=1)

    # -- The same flow buffered on both sides of the rules
    system.add_component(name="DUAL", cls="DualSide")

    # A horizon the session can always step to, so a bound that was NOT watched
    # would only be noticed here.
    system.comp["DUAL"].add_atm2states(
        name="horizon",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": HORIZON},
        cond_occ_21=False,
    )

    caps = {name: system.comp[name].capacities for name in system.comp}
    obs = {"system": system}

    system.isimu_start()

    # The sweeps of the later units write these; here they are set once and the
    # capacities integrate their imbalance.
    caps["DRAIN"]["cuve"].set_outflow("w", 1.0)  # 5 -> 0 at t = 5
    caps["FILL"]["cuve"].set_inflow("w", 1.0)  # 2 -> 10 at t = 8
    caps["TRANSIT"]["cuve"].set_inflow("w", 2.0)  # empty, but 2 transits
    caps["TRANSIT"]["cuve"].set_outflow("w", 2.0)
    caps["BRIM"]["cuve"].set_inflow("w", 1.0)  # full, but 1 transits
    caps["BRIM"]["cuve"].set_outflow("w", 1.0)
    caps["BAL"]["cuve"].set_inflow("w", 3.0)  # net +2
    caps["BAL"]["cuve"].set_outflow("w", 1.0)
    caps["LIGHT"]["cuve"].set_inflow("w", 1.0)
    caps["HEAVY"]["cuve"].set_inflow("w", 1.0)

    obs["t0"] = {
        "time": system.currentTime(),
        "mix_qty": {
            name: caps["MIX"]["cuve"].get_quantity(name) for name in ("H2O", "additif")
        },
        "mix_fill": {
            name: caps["MIX"]["cuve"].get_fill(name) for name in ("H2O", "additif")
        },
        "mix_fill_total": caps["MIX"]["cuve"].get_fill(),
        "mix_qty_total": caps["MIX"]["cuve"].get_quantity(),
        "ely_draw": caps["ELY"]["tank"].split_draw(8),
        "elyw_draw": caps["ELYW"]["tank"].split_draw(8),
        "void_draw": caps["VOID"]["cuve"].split_draw(8),
        "void_qty": caps["VOID"]["cuve"].get_quantity(),
        "drain_empty": caps["DRAIN"]["cuve"].is_empty,
        "fill_full": caps["FILL"]["cuve"].is_full,
        "transit_empty": caps["TRANSIT"]["cuve"].is_empty,
        "transit_serve": caps["TRANSIT"]["cuve"].serve_limit(),
        "transit_serve_w": caps["TRANSIT"]["cuve"].serve_limit("w"),
        "brim_full": caps["BRIM"]["cuve"].is_full,
        "brim_accept": caps["BRIM"]["cuve"].accept_limit(),
        "bal_qty": caps["BAL"]["cuve"].get_quantity("w"),
        "bal_serve": caps["BAL"]["cuve"].serve_limit(),
        "bal_accept": caps["BAL"]["cuve"].accept_limit(),
    }

    # Step until both bounds have been crossed, recording every stop.
    obs["steps"] = []
    for _ in range(8):
        system.isimu_step_forward()
        obs["steps"].append(
            {
                "time": system.currentTime(),
                "drain_empty": caps["DRAIN"]["cuve"].is_empty,
                "fill_full": caps["FILL"]["cuve"].is_full,
            }
        )
        if caps["DRAIN"]["cuve"].is_empty and caps["FILL"]["cuve"].is_full:
            break

    obs["tend"] = {
        "time": system.currentTime(),
        "bal_qty": caps["BAL"]["cuve"].get_quantity("w"),
        "bal_qty_total": caps["BAL"]["cuve"].var_qty_total.value(),
        "drain_qty": caps["DRAIN"]["cuve"].get_quantity("w"),
        "drain_serve": caps["DRAIN"]["cuve"].serve_limit(),
        "light_qty": caps["LIGHT"]["cuve"].get_quantity("w"),
        "light_fill": caps["LIGHT"]["cuve"].get_fill("w"),
        "heavy_qty": caps["HEAVY"]["cuve"].get_quantity("w"),
        "heavy_fill": caps["HEAVY"]["cuve"].get_fill("w"),
        "transit_empty": caps["TRANSIT"]["cuve"].is_empty,
        "transit_serve": caps["TRANSIT"]["cuve"].serve_limit(),
        "brim_full": caps["BRIM"]["cuve"].is_full,
        "brim_accept": caps["BRIM"]["cuve"].accept_limit(),
    }

    system.isimu_stop()

    return obs


# ----------------------------------------------------------------------
# What a capacity reports (R28)
# ----------------------------------------------------------------------


def test_reported_quantities_and_weighted_fills(the_system):
    """AE10: volume 100, H2O at weight 1 and additif at weight 2.

    Holding 40 and 20, the reported quantities are 40 and 20, the weighted
    fills are 0.4 and 0.4, and the total fill is their sum, 0.8.
    """
    t0 = the_system["t0"]

    assert t0["mix_qty"]["H2O"] == pytest.approx(40.0)
    assert t0["mix_qty"]["additif"] == pytest.approx(20.0)

    assert t0["mix_fill"]["H2O"] == pytest.approx(0.4)
    assert t0["mix_fill"]["additif"] == pytest.approx(0.4)

    assert t0["mix_fill_total"] == pytest.approx(0.8)
    assert t0["mix_fill_total"] == pytest.approx(
        t0["mix_fill"]["H2O"] + t0["mix_fill"]["additif"]
    )
    assert t0["mix_qty_total"] == pytest.approx(60.0)


def test_quantity_and_fill_are_real_variables(the_system):
    """R28: reported as PyCATSHOO variables, so they reach the indicators."""
    capacity = the_system["system"].comp["MIX"].capacities["cuve"]
    names = {var.basename() for var in the_system["system"].comp["MIX"].variables()}

    assert {
        "cuve_qty_H2O",
        "cuve_qty_additif",
        "cuve_qty",
        "cuve_fill_H2O",
        "cuve_fill_additif",
        "cuve_fill",
    } <= names

    # ... and they are the very objects the capacity reads back
    assert capacity.var_qty["H2O"].basename() == "cuve_qty_H2O"
    assert capacity.var_fill["additif"].basename() == "cuve_fill_additif"
    assert capacity.var_qty_total.basename() == "cuve_qty"
    assert capacity.var_fill_total.basename() == "cuve_fill"


# ----------------------------------------------------------------------
# Extraction (R35)
# ----------------------------------------------------------------------


def test_draw_splits_at_the_raw_quantity_share(the_system):
    """AE22: 30 of H2 and 10 of O2 serving a draw of 8 release 6 and 2."""
    draw = the_system["t0"]["ely_draw"]

    assert draw["H2"] == pytest.approx(6.0)
    assert draw["O2"] == pytest.approx(2.0)
    assert sum(draw.values()) == pytest.approx(8.0)


def test_weights_do_not_compose_a_withdrawal(the_system):
    """The same contents at unequal weights split the same way.

    O2 at weight 3 occupies 30 of the volume against H2's 30, so a split on the
    volume each occupies would give 4 and 4. R35 splits on the RAW share.
    """
    draw = the_system["t0"]["elyw_draw"]

    assert draw["H2"] == pytest.approx(6.0)
    assert draw["O2"] == pytest.approx(2.0)
    assert draw == the_system["t0"]["ely_draw"]


def test_empty_capacity_serves_a_draw_of_zero(the_system):
    """A null total content serves zero, it does not divide by it."""
    assert the_system["t0"]["void_qty"] == pytest.approx(0.0)
    assert the_system["t0"]["void_draw"] == {"w": 0.0}


# ----------------------------------------------------------------------
# Integration
# ----------------------------------------------------------------------


def test_level_integrates_the_imbalance(the_system):
    """3 in, 1 out: the level rises at the net rate, and so does the total."""
    tend = the_system["tend"]
    expected = 10.0 + 2.0 * tend["time"]

    assert tend["time"] > 0
    assert tend["bal_qty"] == pytest.approx(expected, rel=1e-6)
    assert tend["bal_qty_total"] == pytest.approx(expected, rel=1e-6)


def test_weight_fills_the_volume_faster(the_system):
    """The same quantity at weight 2 fills the shared volume twice as fast."""
    tend = the_system["tend"]

    # Same volume, same initial content, same inflow -> same raw quantity
    assert tend["heavy_qty"] == pytest.approx(tend["light_qty"], rel=1e-6)

    # ... but twice the fill
    assert tend["light_fill"] == pytest.approx(tend["light_qty"] / 100.0, rel=1e-5)
    assert tend["heavy_fill"] == pytest.approx(2 * tend["light_fill"], rel=1e-5)


# ----------------------------------------------------------------------
# Bounds (R7)
# ----------------------------------------------------------------------


def test_bounds_fire_at_the_crossing(the_system):
    """The empty and full transitions fire when the bound is reached.

    Nothing else happens between t = 0 and the horizon at t = 20, so an
    unwatched bound would only be noticed there.
    """
    steps = the_system["steps"]

    empty_dates = [step["time"] for step in steps if step["drain_empty"]]
    full_dates = [step["time"] for step in steps if step["fill_full"]]

    assert empty_dates, "the drained tank never reached its empty bound"
    assert full_dates, "the filled tank never reached its full bound"

    assert empty_dates[0] == pytest.approx(DRAIN_EMPTY_DATE, abs=CROSSING_TOL)
    assert full_dates[0] == pytest.approx(FILL_FULL_DATE, abs=CROSSING_TOL)

    # Crossed on the way, not at the next scheduled event
    assert empty_dates[0] < full_dates[0] < HORIZON
    assert the_system["tend"]["time"] < HORIZON


def test_initial_bound_states_follow_the_initial_content(the_system):
    """A capacity declared at 0 starts empty, one declared at its volume full."""
    t0 = the_system["t0"]

    assert t0["transit_empty"] is True
    assert t0["brim_full"] is True
    assert t0["drain_empty"] is False
    assert t0["fill_full"] is False


def test_an_empty_capacity_serves_what_transits(the_system):
    """R7: emptied, it can only serve onward what currently goes through it."""
    t0 = the_system["t0"]
    tend = the_system["tend"]

    assert t0["transit_empty"] is True
    assert t0["transit_serve"] == pytest.approx(2.0)
    assert t0["transit_serve_w"] == pytest.approx(2.0)

    assert tend["transit_empty"] is True
    assert tend["transit_serve"] == pytest.approx(2.0)

    # The tank drained to empty can serve nothing: nothing transits through it
    assert tend["drain_serve"] == pytest.approx(0.0)


def test_a_full_capacity_accepts_what_transits(the_system):
    """R7: full, it reduces what it propagates upstream to what leaves it."""
    t0 = the_system["t0"]
    tend = the_system["tend"]

    assert t0["brim_full"] is True
    assert t0["brim_accept"] == pytest.approx(1.0)
    assert tend["brim_full"] is True
    assert tend["brim_accept"] == pytest.approx(1.0)


def test_an_unbounded_capacity_limits_nothing(the_system):
    """Neither empty nor full, it constrains neither side."""
    t0 = the_system["t0"]

    assert t0["bal_serve"] == float("inf")
    assert t0["bal_accept"] == float("inf")


# ----------------------------------------------------------------------
# Declaration model (R4)
# ----------------------------------------------------------------------


def test_short_and_general_forms_declare_the_same_thing(the_system):
    """``flow="w"`` is the single-flow short form of the general ``flows``."""
    short = the_system["system"].comp["DRAIN"].capacities["cuve"]
    general = the_system["system"].comp["MIX"].capacities["cuve"]

    assert short.flow_names == ["w"]
    assert short.weight_of("w") == 1.0
    assert short.side == "in"

    assert general.flow_names == ["H2O", "additif"]
    assert general.weight_of("H2O") == 1.0
    assert general.weight_of("additif") == 2.0


def test_side_is_resolved_from_the_flows_when_not_declared(the_system):
    """An output-only flow resolves the capacity to the output side."""
    ely = the_system["system"].comp["ELY"]

    assert ely.capacities["tank"].side == "out"
    assert set(ely.capacities_out) == {"tank"}
    assert ely.capacities_in == {}


def test_the_same_flow_may_be_buffered_on_both_sides(the_system):
    """Upstream and downstream of the rules are two distinct hops (KTD13)."""
    dual = the_system["system"].comp["DUAL"]

    assert set(dual.capacities) == {"up", "down"}
    assert set(dual.capacities_in) == {"up"}
    assert set(dual.capacities_out) == {"down"}

    assert dual.get_capacity_of_flow("w", "in") is dual.capacities["up"]
    assert dual.get_capacity_of_flow("w", "out") is dual.capacities["down"]
    assert dual.get_capacity_of_flow("dry", "out") is None


def test_two_capacities_on_the_same_flow_and_side_raise(the_system):
    """A flow is buffered by at most one capacity per side."""
    dual = the_system["system"].comp["DUAL"]

    with pytest.raises(ValueError, match="already held by capacity up"):
        dual.add_capacity(name="clash", flow="w", capacity=5, side="in")

    with pytest.raises(ValueError, match="already held by capacity down"):
        dual.add_capacity(name="clash", flow="w", capacity=5, side="out")

    assert set(dual.capacities) == {"up", "down"}


def test_flows_resolving_to_different_sides_raise(the_system):
    """A capacity sits entirely upstream or entirely downstream of the rules."""
    dual = the_system["system"].comp["DUAL"]

    # "feed" exists only as an input, "dry" only as an output
    with pytest.raises(ValueError, match="different sides"):
        dual.add_capacity(name="straddle", flows=["feed", "dry"], capacity=5)

    assert "straddle" not in dual.capacities


def test_declaration_errors(the_system):
    """The malformed declarations a capacity refuses outright."""
    dual = the_system["system"].comp["DUAL"]

    with pytest.raises(ValueError, match="does not exist as input nor output"):
        dual.add_capacity(name="ghost", flow="nope", capacity=5)

    with pytest.raises(ValueError, match="discrete flow"):
        dual.add_capacity(name="bool", flow="switch", capacity=5)

    with pytest.raises(ValueError, match="does not exist as input flow"):
        dual.add_capacity(name="wrong_side", flow="dry", capacity=5, side="in")

    with pytest.raises(ValueError, match="not both"):
        dual.add_capacity(name="twice", flow="w", flows=["w"], capacity=5)

    with pytest.raises(ValueError, match="is required"):
        dual.add_capacity(name="volumeless", flow="w")

    with pytest.raises(ValueError, match="strictly positive"):
        dual.add_capacity(name="flat", flow="w", capacity=0, side="in")

    with pytest.raises(ValueError, match="already exists"):
        dual.add_capacity(name="up", flow="dry", capacity=5)


# ----------------------------------------------------------------------
# Rule guards cannot read a level (R29 / KD15)
# ----------------------------------------------------------------------


def test_a_guard_naming_a_capacity_raises(the_system):
    """AE12: the message names the guard and points at the sensor pattern."""
    dual = the_system["system"].comp["DUAL"]

    with pytest.raises(ValueError) as excinfo:
        dual.add_rules(
            name="production",
            rules=[dict(name="topup", cond="up", cons={"w": 1}, prod={"dry": 1})],
        )

    message = str(excinfo.value)
    assert "rule guard" in message
    assert "topup" in message
    assert "capacity up" in message
    assert "sensor" in message
    assert "control port" in message

    assert "production" not in dual.rule_sets


def test_a_rule_map_naming_a_capacity_raises(the_system):
    """A capacity is interposed automatically: rules name flows, not capacities."""
    dual = the_system["system"].comp["DUAL"]

    with pytest.raises(ValueError, match="which is not a flow"):
        dual.add_rules(name="bad_cons", rules=[dict(cons={"up": 1}, prod={"dry": 1})])

    with pytest.raises(ValueError, match="which is not a flow"):
        dual.add_rules(name="bad_prod", rules=[dict(cons={"w": 1}, prod={"down": 1})])


def test_a_guard_naming_a_flow_still_resolves(the_system):
    """The capacity check does not shadow ordinary flow resolution."""
    dual = the_system["system"].comp["DUAL"]

    rule_set = dual.add_rules(
        name="ok", rules=[dict(cond="switch", cons={"w": 1}, prod={"dry": 1})]
    )

    assert rule_set.rules[0].cond[0].port == "in"
    assert rule_set.rules[0].cond[0].flow is dual.flows_in["switch"]


def test_delete(the_system):
    the_system["system"].deleteSys()
    cod3s.terminate_session()
