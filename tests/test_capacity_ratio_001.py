"""A capacity publishes the SHARE each constituent is of what it holds.

Why a quotient has to be published rather than computed
-------------------------------------------------------
A controller's output grammar is closed at four operators and none of them is
arithmetic (R42). It is closed so that every form it carries compiles to a
threshold the solver can root-find, and a quotient carries none. So a montage
that has to act on a **composition** -- ventilate a room when the fraction of
hydrogen in it passes 2 %, shed the electrolyser at 3 % -- cannot be written by
teaching the controller to divide. The volume publishes the fraction instead,
as one more variable of the model, and the controller does what it already
knows how to do: compare, and date the crossing.

That is the shape of the reference installation this was ported from, where the
quotient is materialised on the producing side and the automaton only holds a
hysteresis band.

What this module pins down
--------------------------
* the share is ``qty_f / total raw quantity``, and therefore NOT the fill: the
  declared volume never enters it, so a constituent may be a third of what a
  tank holds while occupying a tenth of it;
* a null total reads zero, by convention;
* an observer reads the shares beside the levels, an instrument republishes
  them under its own gain, and a controller bands on one through an ordinary
  observation input;
* **the crossing of a threshold on a share is DATED**, not noticed at the
  following step. It is the point the whole change turns on: a share is a
  derived, algebraic quantity and a non-linear one, so nothing guaranteed in
  advance that the root search would work on it. Measured here by shrinking
  ``dtCond`` and watching the date converge on the analytic crossing.

PyCATSHOO forbids more than one live system per process, so each scenario builds
its system, drives it, and gives it back before the next one starts.
"""

import cod3s
import muscadet
import pytest
from muscadet.derating import solver_owned_endpoints
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

# --------------------------------------------------------------------------
# The room, and the fraction of hydrogen in it
# --------------------------------------------------------------------------

#: Wide enough that the full bound is never approached: what is under test is a
#: composition, and a volume bound would be a second story in the trace.
RA_VOLUME = 1000.0

#: The air the room starts with, and the hydrogen leaking into it. Air occupies
#: twice the volume per unit that hydrogen does, so the fill and the share
#: cannot be the same number and a test cannot pass by confusing them.
RA_AIR = 100.0
RA_AIR_WEIGHT = 2.0
RA_H2_WEIGHT = 1.0
RA_H2_RATE = 1.0

#: The band the ventilation carries, in the reference installation's own
#: numbers: on at 2 % of hydrogen, off at 1 %.
RA_VENT_ON = 0.02
RA_VENT_OFF = 0.01

#: What the relay instrument reads: half, so a gain is visibly applied to a
#: share and not only to a level.
RA_RELAY_GAIN = 0.5

#: When the share crosses the activation edge, analytically. ``q / (q + air)``
#: reaches ``on`` at ``q = air * on / (1 - on)``: NOT a linear function of the
#: integrated state, which is what makes the dating measurement below worth
#: making.
RA_H2_AT_VENT_ON = RA_AIR * RA_VENT_ON / (1.0 - RA_VENT_ON)
RA_VENT_ON_AT = RA_H2_AT_VENT_ON / RA_H2_RATE

RA_HORIZON = 8.0

#: The crossing search of the default configuration, and one two decades finer.
RA_DT_COND_FINE = 1e-7

# --------------------------------------------------------------------------
# The two mixtures that are drawn down
# --------------------------------------------------------------------------

RA_MIX_VOLUME = 200.0
RA_MIX_WATER = 40.0
RA_MIX_SYRUP = 20.0
RA_SYRUP_WEIGHT = 2.0

#: Only the water is drawn out of the first tank, so its composition MOVES: its
#: share falls away from two thirds while the tank goes on holding all its
#: syrup, and the empty bound is never reached.
RA_DRAW_WATER = 4.0

#: Both are drawn out of the second, in the proportion it holds them, so its
#: composition is CONSTANT until the volume empties -- and then jumps.
RA_DRAW_SYRUP = 2.0
RA_MIX_EMPTIES_AT = (RA_MIX_WATER + RA_MIX_SYRUP) / (RA_DRAW_WATER + RA_DRAW_SYRUP)

RA_GRID = 1.0
RA_DRAIN_HORIZON = 12.0

#: PyCATSHOO stores a variable in single precision, so ``0.18`` written by an
#: equation reads back ``0.18000000715``. The README records it under "a
#: quantity carries about seven significant digits", along with the rule this
#: module follows: a bound tighter than about 1e-7 measures the engine's
#: storage rather than the model. Nothing new here -- the fills have carried it
#: since they were declared -- but it is why nothing below is asserted at 1e-12
#: unless the two sides are literally the same variable.
RA_REL = 1e-6

TOL = 1e-9


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------


class RaProbe(muscadet.ObjFlow):
    """Reads both constituents: their levels, their fills and their shares."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="room", flows=["h2", "air"])


class RaLegacyProbe(muscadet.ObjFlow):
    """The 1.x observer: the totals alone, declared exactly as it always was.

    Its presence is the measurement behind "the extra aliases cost an existing
    model nothing": PyCATSHOO matches an import to an export by alias and
    ignores an export nobody imports, so this still connects to a box that has
    grown a third family since.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="room")
        self.add_atm2states(
            name="horizon",
            st1="s0",
            st2="s1",
            occ_law_12={"cls": "delay", "time": RA_HORIZON},
            cond_occ_21=False,
        )


class RaRelay(muscadet.ObjFlow):
    """An instrument between the room and a controller, reading half."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="room", flows=["h2", "air"])
        self.add_measurement_out(
            name="relay",
            source="room",
            flows=["h2", "air"],
            gain_default=RA_RELAY_GAIN,
        )


class RaWrongConstituent(muscadet.ObjFlow):
    """A ratio channel naming a constituent the room does not hold."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="room", kind="ratio", flows=["argon"])


def build_room(name, dt_cond=None):
    """The room, its leak, its observers and the two controllers on it."""
    system = muscadet.System(name=name)

    system.add_component(
        name="LEAK", cls="SourceContinuous", flow="h2", rate=RA_H2_RATE
    )
    system.add_component(
        name="ROOM",
        cls="CapacityContinuous",
        ports="in",
        flows=[
            {"name": "h2", "weight": RA_H2_WEIGHT, "demand": RA_H2_RATE},
            {"name": "air", "weight": RA_AIR_WEIGHT, "demand": 0.0},
        ],
        capacity=RA_VOLUME,
        capacity_name="room",
        content_init={"air": RA_AIR, "h2": 0.0},
    )
    system.add_component(name="PROBE", cls="RaProbe")
    system.add_component(name="LEGACY", cls="RaLegacyProbe")
    system.add_component(name="RELAY", cls="RaRelay")

    # The controller of the reference installation: an ordinary observation
    # input, an ordinary band, and no arithmetic anywhere.
    system.add_component(
        name="VENT",
        cls="ObjCtrl",
        controls_in=[{"name": "room", "kind": "ratio", "flows": ["h2"]}],
        controls_out=[
            {
                "name": "vent",
                "kind": "bool",
                "emit": {
                    "op": "band",
                    "input": "room",
                    "direction": "above",
                    "activate": RA_VENT_ON,
                    "release": RA_VENT_OFF,
                },
            }
        ],
    )

    # The same montage one instrument further out. An observer cannot tell a
    # capacity from a republisher, so this one is declared exactly like the
    # first -- and its band sits at the gain-adjusted edge, so the two must
    # switch at the very same instant.
    system.add_component(
        name="VENT_RELAY",
        cls="ObjCtrl",
        controls_in=[{"name": "relay", "kind": "ratio", "flows": ["h2"]}],
        controls_out=[
            {
                "name": "vent",
                "kind": "bool",
                "emit": {
                    "op": "band",
                    "input": "relay",
                    "direction": "above",
                    "activate": RA_VENT_ON * RA_RELAY_GAIN,
                    "release": RA_VENT_OFF * RA_RELAY_GAIN,
                },
            }
        ],
    )

    system.connect_flow(source="LEAK", target="ROOM", flow_name="h2")
    system.connect("ROOM", "room_level_out", "PROBE", "room_level_in")
    system.connect("ROOM", "room_level_out", "LEGACY", "room_level_in")
    system.connect("ROOM", "room_level_out", "RELAY", "room_level_in")
    system.connect("ROOM", "room_level_out", "VENT", "room_level_in")
    system.connect("RELAY", "relay_level_out", "VENT_RELAY", "relay_level_in")

    if dt_cond is not None:
        system.get_or_create_pdmp_manager().setDtCond(dt_cond)

    return system


def room_snapshot(system):
    """Everything the room scenario asserts on, at one instant."""
    room = system.comp["ROOM"].capacities["room"]
    probe = system.comp["PROBE"].measurements_in["room"]
    legacy = system.comp["LEGACY"].measurements_in["room"]
    relay = system.comp["RELAY"].measurements_out["relay"]

    return {
        "time": system.currentTime(),
        "h2": room.get_quantity("h2"),
        "total": room.total_quantity(),
        "ratio_h2": room.get_ratio("h2"),
        "ratio_air": room.get_ratio("air"),
        "fill_h2": room.get_fill("h2"),
        "fill_air": room.get_fill("air"),
        "read_ratio_h2": probe.get_ratio("h2"),
        "read_ratio_air": probe.get_ratio("air"),
        "read_level_h2": probe.get_level("h2"),
        "legacy_level": legacy.get_level(),
        "relay_ratio_h2": relay.get_ratio("h2"),
        "ctrl_reading": system.comp["VENT"].controls_in["room"].get_reading(),
        "vent": system.comp["VENT"].controls_out["vent"].get_signal(),
        "vent_relay": system.comp["VENT_RELAY"].controls_out["vent"].get_signal(),
    }


def drive_events(system, snap, horizon, steps=32):
    """Walk from event to event, sampling AT each stop.

    ``isimu_step_forward`` and not ``isimu_step_to``: the latter covers a quiet
    stretch with one interactive step and runs THROUGH the watched crossings in
    it, so their dates are lost -- which is precisely what this module measures.
    """
    trace = [snap(system)]
    for _ in range(steps):
        system.isimu_step_forward()
        trace.append(snap(system))
        if system.currentTime() >= horizon:
            break
    return trace


def build_mixtures(name):
    """Two mixtures drawn down: one whose composition moves, one that empties."""
    system = muscadet.System(name=name)

    for tank in ("MOVE", "EMPTY"):
        system.add_component(
            name=f"MIX_{tank}",
            cls="CapacityContinuous",
            ports="out",
            flows=[
                {"name": "water", "weight": 1.0},
                {"name": "syrup", "weight": RA_SYRUP_WEIGHT},
            ],
            capacity=RA_MIX_VOLUME,
            capacity_name="mix",
            content_init={"water": RA_MIX_WATER, "syrup": RA_MIX_SYRUP},
        )
        system.add_component(
            name=f"WATER_{tank}",
            cls="ConsumerContinuous",
            flow="water",
            demand=RA_DRAW_WATER,
        )
        system.connect_flow(
            source=f"MIX_{tank}", target=f"WATER_{tank}", flow_name="water"
        )

    # Only the second tank has its syrup drawn too, in the proportion it holds
    # it, so only the second one reaches the empty bound.
    system.add_component(
        name="SYRUP_EMPTY",
        cls="ConsumerContinuous",
        flow="syrup",
        demand=RA_DRAW_SYRUP,
    )
    system.connect_flow(source="MIX_EMPTY", target="SYRUP_EMPTY", flow_name="syrup")

    # Something dated to walk to, so the run can be driven from event to event
    # and the empty crossing gets a date of its own.
    system.comp["SYRUP_EMPTY"].add_atm2states(
        name="horizon",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": RA_DRAIN_HORIZON},
        cond_occ_21=False,
    )

    return system


def mixture_snapshot(system, at):
    """The composition of both mixtures at one stop."""
    rows = {"time": at}
    for tank in ("MOVE", "EMPTY"):
        mix = system.comp[f"MIX_{tank}"].capacities["mix"]
        rows[tank] = {
            "water": mix.get_quantity("water"),
            "syrup": mix.get_quantity("syrup"),
            "ratio_water": mix.get_ratio("water"),
            "ratio_syrup": mix.get_ratio("syrup"),
            "fill_water": mix.get_fill("water"),
            "empty": mix.is_empty,
        }
    return rows


@pytest.fixture(scope="module")
def the_run():
    """Every observation this module asserts on, each system given back at once."""
    obs = {}

    # -- the room, at the default crossing precision ------------------------
    system = build_room("RaRoom")
    try:
        # Before anything runs: the shares a capacity is BORN with. A model
        # that reads a composition at t=0 gets the one its content_init
        # describes, not a zero it would read once and never again.
        room = system.comp["ROOM"].capacities["room"]
        obs["born"] = {
            "ratio_h2": room.get_ratio("h2"),
            "ratio_air": room.get_ratio("air"),
            "fill_air": room.get_fill("air"),
        }

        # The constituent diagnostic, ahead of the engine: nothing is wired by
        # a refused connect, so the session below is unaffected.
        system.add_component(name="WRONG", cls="RaWrongConstituent")
        with pytest.raises(ValueError) as refused:
            system.connect("ROOM", "room_level_out", "WRONG", "room_level_in")
        obs["refused"] = str(refused.value)

        # What a failure mode may not clamp, taken while the components exist.
        obs["owned_room"] = dict(solver_owned_endpoints(system.comp["ROOM"]))
        obs["owned_relay"] = dict(solver_owned_endpoints(system.comp["RELAY"]))

        system.isimu_start()
        obs["room"] = drive_events(system, room_snapshot, RA_HORIZON)
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()

    # -- the same room, with the crossing search four decades finer ---------
    system = build_room("RaRoomFine", dt_cond=RA_DT_COND_FINE)
    try:
        system.isimu_start()
        obs["room_fine"] = drive_events(system, room_snapshot, RA_HORIZON)
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()

    # -- the two mixtures, on an observation grid ---------------------------
    system = build_mixtures("RaMixGrid")
    try:
        system.isimu_start()
        trace = []
        t = 0.0
        while t < RA_DRAIN_HORIZON:
            t += RA_GRID
            system.isimu_step_to(
                t,
                on_stop=lambda kind, at, fired: trace.append(
                    mixture_snapshot(system, at)
                ),
            )
        obs["mixtures"] = trace
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()

    # -- the same mixtures, walked from event to event ----------------------
    # The grid above cannot date the empty crossing: ``isimu_step_to`` covers a
    # quiet stretch with one interactive step and runs through the watched
    # crossings inside it. This run stops on them.
    system = build_mixtures("RaMixEvents")
    try:
        system.isimu_start()
        obs["mixture_events"] = drive_events(
            system,
            lambda sys_: mixture_snapshot(sys_, sys_.currentTime()),
            RA_DRAIN_HORIZON,
        )
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()

    return obs


def first_true(trace, key):
    """The first row where ``key`` holds, or None."""
    for row in trace:
        if row[key]:
            return row
    return None


# ---------------------------------------------------------------------------
# What the volume publishes
# ---------------------------------------------------------------------------
def test_the_shares_are_set_before_the_clock_moves(the_run):
    """A volume is born with the composition its content_init describes."""
    born = the_run["born"]

    assert born["ratio_air"] == pytest.approx(1.0)
    assert born["ratio_h2"] == pytest.approx(0.0)


def test_a_share_is_not_a_fill(the_run):
    """The declared volume never enters a share, only the content does.

    Air fills a fifth of the room -- 100 units of it at weight 2 in a volume of
    1000 -- and is the whole of what the room holds. Two different numbers out
    of one state, and confusing them is the mistake this asserts against.
    """
    born = the_run["born"]

    assert born["fill_air"] == pytest.approx(RA_AIR * RA_AIR_WEIGHT / RA_VOLUME)
    assert born["ratio_air"] == pytest.approx(1.0)
    assert born["fill_air"] != pytest.approx(born["ratio_air"])


def test_the_shares_track_the_content(the_run):
    """``qty_f / total``, at every stop, and the shares add up to one."""
    for row in the_run["room"]:
        expected = row["h2"] / (row["h2"] + RA_AIR)

        assert row["ratio_h2"] == pytest.approx(expected, rel=RA_REL, abs=1e-9), row
        assert row["ratio_h2"] + row["ratio_air"] == pytest.approx(1.0, rel=RA_REL)


def test_a_share_is_the_share_of_the_levels_published_beside_it(the_run):
    """``ratio_f * total == qty_f``, which is what makes the two combinable.

    Nothing clamps the share into [0, 1], and this is why: an observer holding
    a share and a level must get the constituent back by multiplying them, and
    a clamp would break that agreement exactly where a bound crossing left a
    residue -- silently, and only there.
    """
    for row in the_run["room"]:
        assert row["ratio_h2"] * row["total"] == pytest.approx(
            row["h2"], rel=RA_REL, abs=1e-9
        )


# ---------------------------------------------------------------------------
# What an observer, an instrument and a controller make of it
# ---------------------------------------------------------------------------
def test_an_observer_reads_the_shares_beside_the_levels(the_run):
    """One channel, three families, and the totals it always carried."""
    for row in the_run["room"]:
        # Exactly, and not approximately: the observer reads THE variable the
        # volume publishes, through a reference. Any daylight between the two
        # would be a copy nobody asked for.
        assert row["read_ratio_h2"] == row["ratio_h2"], row
        assert row["read_ratio_air"] == row["ratio_air"], row
        assert row["read_level_h2"] == row["h2"], row


def test_a_1x_observer_still_connects_and_still_reads_the_total(the_run):
    """The extra aliases cost an existing model nothing, measured not assumed.

    PyCATSHOO matches an import to an export by alias and ignores an export
    nobody imports, so an observer declaring the totals alone connects to a box
    that has grown a third family since and reads what it always read.
    """
    for row in the_run["room"]:
        assert row["legacy_level"] == pytest.approx(row["total"], rel=RA_REL)


def test_an_instrument_republishes_the_share_under_its_gain(the_run):
    """A share lies with the rest of the readings when the instrument lies.

    Left honest, a probe reading half its levels would report a composition
    nothing else agrees with, and -- worse -- a failure mode clamping its gain
    to zero would leave the montage behind it acting on the true one.
    """
    for row in the_run["room"]:
        assert row["relay_ratio_h2"] == pytest.approx(
            row["ratio_h2"] * RA_RELAY_GAIN, rel=RA_REL, abs=1e-12
        )


def test_a_controller_reads_a_share_through_an_ordinary_input(the_run):
    """The acceptance criterion: a ratio input answers a number, and that is all.

    Nothing in the controller knows a share from a level. Its input is the very
    measurement channel a sensor uses, and its band the very band a level would
    get.
    """
    for row in the_run["room"]:
        assert row["ctrl_reading"] == row["ratio_h2"], row


def test_the_band_switches_on_the_share(the_run):
    """Off below the activation edge, on above it, and it never comes back.

    The share only rises in this scenario -- the leak has no counterpart -- so
    the band has one edge to cross and the trace splits cleanly around it.

    The crossing instant itself carries TWO stops at the same date, and that is
    the ordinary settling of an instantaneous transition rather than anything
    to do with a share: the first stop is where the solver put the state on the
    edge, the second where the automaton has fired and the sensitive method has
    rewritten the output. Asserting on the reading at the first would be
    asserting that a notification precedes the event that raises it.
    """
    trace = the_run["room"]
    switched = first_true(trace, "vent")

    assert switched is not None, "the ventilation never started"
    assert switched["ratio_h2"] >= RA_VENT_ON

    for row in trace:
        if row["time"] < switched["time"]:
            assert not row["vent"], row
            assert row["ratio_h2"] < RA_VENT_ON, row
        if row["time"] > switched["time"]:
            assert row["vent"], row
            assert row["ratio_h2"] > RA_VENT_ON, row


def test_an_instrument_between_the_two_changes_nothing(the_run):
    """A controller cannot tell a capacity from a republisher, shares included.

    The second montage reads the relay, whose gain halves everything it
    publishes, and carries the halved edges. The two must therefore switch at
    the same instant -- if they do not, the republished share is not the share
    of the republished levels.
    """
    trace = the_run["room"]
    direct = first_true(trace, "vent")
    relayed = first_true(trace, "vent_relay")

    assert direct is not None and relayed is not None
    assert direct["time"] == pytest.approx(relayed["time"], abs=1e-12)


# ---------------------------------------------------------------------------
# The crossing is DATED, on a derived and non-linear quantity
# ---------------------------------------------------------------------------
def test_the_crossing_of_a_share_is_root_found(the_run):
    """The measurement the whole change rests on.

    A share is an EXPLICIT variable -- algebraic, derived, and here non-linear
    in the integrated state -- where every threshold muscadet dated before it
    read either an ODE variable or a boolean. Nothing guaranteed that the
    integration manager would search for the root of a condition reading one.

    It does: the date the band switches at converges on the analytic crossing
    as ``dtCond`` shrinks, which is only possible if the solver is bracketing
    it. A crossing merely noticed at the following step would not move.
    """
    coarse = first_true(the_run["room"], "vent")
    fine = first_true(the_run["room_fine"], "vent")

    assert coarse is not None and fine is not None

    coarse_error = abs(coarse["time"] - RA_VENT_ON_AT)
    fine_error = abs(fine["time"] - RA_VENT_ON_AT)

    # Both land past the crossing rather than before it: the search stops on
    # the far side of the edge, as it does for a level bound.
    assert coarse["time"] >= RA_VENT_ON_AT
    assert fine["time"] >= RA_VENT_ON_AT

    # Measured at 4.5e-4 by default and 1.0e-7 four decades finer.
    assert coarse_error < 1e-3
    assert fine_error < 1e-5
    assert fine_error < coarse_error / 10


def test_the_reading_at_the_crossing_is_the_edge(the_run):
    """And the state at that date is the state the edge names, not a step past it."""
    fine = first_true(the_run["room_fine"], "vent")

    assert fine["ratio_h2"] == pytest.approx(RA_VENT_ON, abs=1e-6)
    assert fine["h2"] == pytest.approx(RA_H2_AT_VENT_ON, abs=1e-5)


# ---------------------------------------------------------------------------
# A composition that moves, and one that stops existing
# ---------------------------------------------------------------------------
def test_a_composition_moves_when_one_constituent_is_drawn(the_run):
    """The share follows the content down, monotonically and without crossing zero.

    Drawing on a mixture takes it at the composition it holds (R35), so the
    water leaves at a rate proportional to the share it still has: it decays
    towards nothing without ever getting there, which is exactly what
    ``split_draw`` says it does. The share is the visible face of that
    behaviour, and this is what it is for -- a montage watching a dilution
    watches this number, not a level that would look like a straight line.
    """
    rows = [row["MOVE"] for row in the_run["mixtures"]]

    assert len(rows) >= 5

    for tank in rows:
        total = tank["water"] + tank["syrup"]

        assert tank["ratio_water"] == pytest.approx(
            total and tank["water"] / total, rel=RA_REL
        ), tank
        # Nothing draws the syrup here, so the tank keeps all of it and the
        # empty bound is never in play: what moves is the composition alone.
        assert tank["syrup"] == pytest.approx(RA_MIX_SYRUP, rel=RA_REL), tank
        assert not tank["empty"], tank
        assert tank["water"] > 0.0, tank

    shares = [tank["ratio_water"] for tank in rows]
    assert shares[0] < 2.0 / 3.0
    assert shares == sorted(shares, reverse=True)
    assert shares[-1] < shares[0] / 1.5


def test_a_draw_at_the_held_composition_leaves_it_untouched(the_run):
    """``split_draw`` composes a withdrawal at the very share this publishes.

    Both constituents leave in the proportion the tank holds them, so the
    composition is a constant of the run right up to the volume emptying --
    which is the one thing that then moves it.
    """
    for row in the_run["mixtures"]:
        tank = row["EMPTY"]
        if tank["empty"] or row["time"] > RA_MIX_EMPTIES_AT:
            continue

        assert tank["ratio_water"] == pytest.approx(2.0 / 3.0, abs=1e-6), row
        assert tank["ratio_syrup"] == pytest.approx(1.0 / 3.0, abs=1e-6), row


def test_an_empty_volume_holds_no_share_of_anything(the_run):
    """The convention, at the one place a model reaches it.

    Zero rather than a NaN or a refusal: a NaN compares false to everything and
    would release every band watching the composition without a trace, and
    there is no number a refusal could hand the observer instead.
    """
    settled = [row for row in the_run["mixtures"] if row["EMPTY"]["empty"]]

    # More than one, and the count is load-bearing: the first row past the
    # bound is the one the reset map has just clamped, where the shares are
    # still the ones the last equation wrote. They are refreshed at the next
    # evaluation, like every explicit variable of this module -- so the rows
    # this asserts on are the ones after it, and there have to BE some.
    assert len(settled) >= 2, "the mixture never emptied, or emptied too late"
    for row in settled[1:]:
        tank = row["EMPTY"]
        assert tank["water"] == pytest.approx(0.0, abs=1e-6), row
        assert tank["ratio_water"] == pytest.approx(0.0, abs=1e-12), row
        assert tank["ratio_syrup"] == pytest.approx(0.0, abs=1e-12), row


def test_the_share_at_the_bound_is_one_evaluation_behind(the_run):
    """Named rather than hidden: what the reset map does NOT put back.

    A bound crossing fires the empty/full automaton, whose reset map clamps the
    levels and resyncs the total. It refreshes no derived variable, and never
    has: the fills and now the shares are EXPLICIT, recomputed by the capacity
    equation at every evaluation, so between the clamp and the next evaluation
    they still hold what the last one wrote.

    On a fill the residue is numerical -- 0.0026 out of 1 -- and invisible. On a
    share it is not: the composition of a volume that has just reached empty
    reads the composition it had, a third and two thirds here, until the next
    evaluation puts it at zero. The window is one evaluation and the state
    self-corrects, and everything that must not lag reads
    :meth:`Capacity.current_ratio` instead, which recomputes from the levels --
    that is the path a republished reading takes.

    Widening the reset map to write the derived variables would close it, for
    the fills as much as for the shares, and is a change of a different nature
    from publishing a quantity.
    """
    settled = [row for row in the_run["mixture_events"] if row["EMPTY"]["empty"]]

    assert settled, "the mixture never emptied"
    at_bound = settled[0]["EMPTY"]

    # AT the crossing stop: the levels are on the bound, the composition is the
    # one the last evaluation wrote.
    assert at_bound["water"] == pytest.approx(0.0, abs=1e-6)
    assert at_bound["syrup"] == pytest.approx(0.0, abs=1e-6)
    assert at_bound["ratio_water"] == pytest.approx(2.0 / 3.0, rel=RA_REL)

    # ... and it is gone by the next stop, which is what "one evaluation" means.
    assert settled[-1]["EMPTY"]["ratio_water"] == pytest.approx(0.0, abs=1e-12)

    # The window is invisible on an observation grid, where a stop is never the
    # crossing instant itself: the same run sampled on a grid reads zero at
    # every settled row.
    grid = [row for row in the_run["mixtures"] if row["EMPTY"]["empty"]]
    assert grid and grid[0]["EMPTY"]["ratio_water"] == pytest.approx(0.0, abs=1e-12)


def test_the_empty_crossing_is_dated(the_run):
    """The discontinuity of a share sits ON a stop the solver root-found.

    This is what makes the null total harmless to the integrator rather than a
    jump walked through inside a step. Every weight being strictly positive,
    "the weighted fill reaches zero" -- the condition the empty/full automaton
    watches, and it is a WATCHED one (R7) -- and "the raw total reaches zero" --
    the denominator of every share -- are the same instant. So the solver is
    already stopping exactly where the shares jump, and it is not stopping for
    them: it would stop there for a model that published none.
    """
    settled = [row for row in the_run["mixture_events"] if row["EMPTY"]["empty"]]

    assert settled
    # Measured at 10.0025 against an analytic 10.0: the crossing search stops
    # just past the bound, by ``dtCond`` times the rate, exactly as it does for
    # a level bound.
    assert settled[0]["time"] == pytest.approx(RA_MIX_EMPTIES_AT, abs=1e-2)
    assert settled[0]["time"] >= RA_MIX_EMPTIES_AT


def test_the_shares_of_a_mixture_are_not_its_fills(the_run):
    """Syrup occupies twice the volume per unit, so the two families diverge."""
    first = the_run["mixtures"][0]["EMPTY"]

    assert first["ratio_water"] == pytest.approx(2.0 / 3.0, rel=RA_REL)
    assert first["fill_water"] == pytest.approx(
        first["water"] / RA_MIX_VOLUME, rel=RA_REL
    )
    assert first["fill_water"] != pytest.approx(first["ratio_water"])


# ---------------------------------------------------------------------------
# What is refused, and where
# ---------------------------------------------------------------------------
def test_a_constituent_the_volume_does_not_hold_is_named_at_connect(the_run):
    """The diagnostic reaches a ratio channel too, ahead of the engine.

    Its box is the level box, so the resolution that finds an observer behind
    ``{c}_level_in`` has to answer for both natures; left out, this link would
    fail inside PyCATSHOO on a missing alias and the modeller would be told
    nothing about what the room holds.
    """
    message = the_run["refused"]

    assert "argon" in message
    assert "h2" in message and "air" in message


def test_a_share_is_a_solver_owned_endpoint(the_run):
    """A failure mode is turned away from a variable an equation rewrites.

    A share is written by the capacity equation at every integration step, so a
    mode clamping it would be overwritten without a word -- the silent failure
    the endpoint registry exists to name. The advice it gets back is the
    capacity's own: derate the output, or gate what crosses it.

    The registry named a republisher's two TOTALS by hand and left its
    per-constituent variables out, so the same silence covered them; it now
    asks the channel what its equation writes.
    """
    room = the_run["owned_room"]
    relay = the_run["owned_relay"]

    assert "room_ratio_h2" in room and "room_ratio_air" in room
    assert "room_fill_h2" in room  # the sibling it is declared beside

    assert "relay_ratio_h2" in relay and "relay_ratio_air" in relay
    assert "relay_level_h2" in relay and "relay_fill_h2" in relay
    # ... and the gain stays clampable: it is the endpoint a mode is FOR.
    assert "relay_level_gain" not in relay


def test_a_ratio_channel_names_exactly_one_constituent():
    """A share is a fraction of something BY something: half of it is the name."""
    with pytest.raises(ValueError, match="exactly one"):
        muscadet.MeasurementIn(name="room", kind="ratio")

    with pytest.raises(ValueError, match="exactly one"):
        muscadet.MeasurementIn(name="room", kind="ratio", flows=["h2", "air"])


def test_each_nature_refuses_the_readings_it_does_not_carry():
    """And names the accessor that does, which keeps the correction one edit."""
    channel = muscadet.MeasurementIn(name="room", kind="ratio", flows=["h2"])

    with pytest.raises(ValueError, match="get_ratio"):
        channel.get_level()

    with pytest.raises(ValueError, match="get_ratio"):
        channel.get_rate()

    level = muscadet.MeasurementIn(name="room", flows=["h2"])

    with pytest.raises(ValueError, match="get_level"):
        level.get_rate()


def test_a_volume_has_no_share_of_itself():
    """There is no ``{c}_ratio`` alias, so there is no total to read either."""
    assert muscadet.capacity.ratio_alias("room", "h2") == "room_ratio_h2"

    level = muscadet.MeasurementIn(name="room", flows=["h2"])
    with pytest.raises(ValueError, match="name the constituent"):
        level.get_ratio()

    published = muscadet.MeasurementOut(name="relay", flows=["h2"])
    with pytest.raises(ValueError, match="name the constituent"):
        published.get_ratio(None)


def test_a_channel_reading_one_number_publishes_no_constituent():
    """What a republisher may decompose, and what it may not.

    A ratio channel's ``flows`` names what its reading is ABOUT, not
    constituents standing beside it: an instrument republishing one carries a
    share, one number, with nothing behind it to take apart.
    """
    ratio = muscadet.MeasurementIn(name="room", kind="ratio", flows=["h2"])
    level = muscadet.MeasurementIn(name="room", flows=["h2", "air"])

    assert ratio.published_flows() == []
    assert level.published_flows() == ["h2", "air"]


def test_the_share_of_a_null_total_is_zero():
    """The convention, at the one function that states it."""
    assert muscadet.capacity.share_of(0.0, 0.0) == 0.0
    assert muscadet.capacity.share_of(3.0, 0.0) == 0.0
    assert muscadet.capacity.share_of(1.0, 4.0) == pytest.approx(0.25)


def test_delete():
    """Each scenario gave its system back; this closes the session."""
    cod3s.terminate_session()
