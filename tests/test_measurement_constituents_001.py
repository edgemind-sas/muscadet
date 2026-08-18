"""A capacity publishes each constituent it holds, not only the total.

A measurement channel carried one level: the volume as a whole. That is the
one reading an **intensive** property cannot be recovered from. A tank holding
water and heat is at a temperature of ``heat / water``, and the total is their
weighted sum, which is neither term.

The total is worse than insufficient when the constituents differ in nature:
``get_quantity(None)`` sums the RAW quantities, so 100 kg of water beside
8000 J of heat reports 8100 of nothing at all. It is a legitimate reading for a
volume of like constituents (two reagents sharing a tank) and meaningless here,
which is exactly why the constituents have to be reachable separately rather
than being reconstructed from it.

So a capacity now exports one alias pair per held flow beside the totals, an
observer declares which constituents it reads, and a republished measurement
carries them too, so an instrument standing between a volume and a voter is
still indistinguishable from the volume.

Two facts this module pins down because they were measured on the engine
rather than assumed:

* an observer importing only the total still connects to the wider box and
  still reads the total, so the extra aliases cost a 1.x model nothing;
* an observer importing an alias the box does not export fails the WHOLE
  connect, total included, which is why muscadet intercepts that case ahead of
  the engine and names what the volume actually holds.

PyCATSHOO forbids more than one live system per process, so every scenario
lives in the one system below.
"""

import muscadet
import cod3s
import pytest

#: A date the interactive session can always step to, so the equations run.
CST_CLOCK = 5.0

#: The volume the tank holds, and what is in it. Heat occupies a thousandth of
#: the volume a unit of water does: a weight must be strictly positive, so
#: "occupies no volume" is expressed as "occupies very little".
CST_CAPACITY = 200.0
CST_WATER = 100.0
CST_HEAT = 8000.0
CST_WEIGHT_WATER = 1.0
CST_WEIGHT_HEAT = 0.001

#: What the two constituents are worth once weighted into the shared volume.
CST_FILL_WATER = CST_WATER * CST_WEIGHT_WATER / CST_CAPACITY
CST_FILL_HEAT = CST_HEAT * CST_WEIGHT_HEAT / CST_CAPACITY

#: The intensive property the whole change exists for.
CST_TEMPERATURE = CST_HEAT / CST_WATER

#: The relay instrument reads low by half, so a gain is visibly applied to the
#: constituents and not only to the total.
CST_GAIN = 0.5

#: What `connect` refused, filled by :func:`build_system`.
REFUSED = {}


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


class CstTank(muscadet.ObjFlow):
    """One volume holding two constituents of different natures."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="water")
        self.add_flow_continuous_in(name="heat")
        self.add_capacity(
            name="tank",
            flows=[
                {"name": "water", "weight": CST_WEIGHT_WATER},
                {"name": "heat", "weight": CST_WEIGHT_HEAT},
            ],
            capacity=CST_CAPACITY,
            side="in",
            content_init={"water": CST_WATER, "heat": CST_HEAT},
        )


class CstProbe(muscadet.ObjFlow):
    """Reads both constituents, which is what forms a temperature."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="tank", flows=["water", "heat"])

    def temperature(self):
        channel = self.measurements_in["tank"]
        return channel.get_level("heat") / channel.get_level("water")


class CstLegacyProbe(muscadet.ObjFlow):
    """The 1.x observer: the total alone, declared exactly as it always was."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="tank")


class CstRelay(muscadet.ObjFlow):
    """An instrument between the volume and a voter, republishing constituents."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="tank", flows=["water", "heat"])
        self.add_measurement_out(
            name="relay",
            source="tank",
            flows=["water", "heat"],
            gain_default=CST_GAIN,
        )


class CstVoter(muscadet.ObjFlow):
    """Observes the instrument, and cannot tell it from the volume."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="relay", flows=["water", "heat"])


class CstGhostProbe(muscadet.ObjFlow):
    """Asks for a constituent the tank does not hold."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="tank", flows=["water", "plutonium"])


# ----------------------------------------------------------------------
# The one system every scenario lives in
# ----------------------------------------------------------------------


def build_system():
    system = muscadet.System(name="CstSys")

    system.add_component(name="TANK", cls="CstTank")
    system.add_component(name="PROBE", cls="CstProbe")
    system.add_component(name="LEGACY", cls="CstLegacyProbe")
    system.add_component(name="RELAY", cls="CstRelay")
    system.add_component(name="VOTER", cls="CstVoter")
    system.add_component(name="GHOST", cls="CstGhostProbe")

    for observer in ("PROBE", "LEGACY", "RELAY"):
        system.connect("TANK", "tank_level_out", observer, "tank_level_in")

    system.connect("RELAY", "relay_level_out", "VOTER", "relay_level_in")

    # The refusal, taken here so the model is never left half-wired: the check
    # runs BEFORE the engine connect, so nothing was added when it raised.
    try:
        system.connect("TANK", "tank_level_out", "GHOST", "tank_level_in")
    except ValueError as error:
        REFUSED["ghost"] = str(error)

    system.comp["TANK"].add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": CST_CLOCK},
        cond_occ_21=False,
    )

    return system


@pytest.fixture(scope="module")
def the_system():
    system = build_system()
    system.isimu_start()
    system.isimu_step_forward()
    yield system


def channel(system, comp_name, name):
    return system.comp[comp_name].measurements_in[name]


# ----------------------------------------------------------------------
# What a capacity publishes
# ----------------------------------------------------------------------


def test_each_constituent_is_readable(the_system):
    """The whole point: both terms of the ratio arrive separately."""
    tank = channel(the_system, "PROBE", "tank")

    assert tank.get_level("water") == pytest.approx(CST_WATER)
    assert tank.get_level("heat") == pytest.approx(CST_HEAT)


def test_the_intensive_property_is_formed_from_them(the_system):
    """A temperature is a ratio of two constituents, and now computable."""
    assert the_system.comp["PROBE"].temperature() == pytest.approx(CST_TEMPERATURE)


def test_the_total_could_not_have_given_it(the_system):
    """The reading that motivates the change, asserted rather than argued.

    The total is the raw sum, so it is 8100 for 100 kg of water and 8000 J of
    heat: not the temperature, and not a quantity of anything.
    """
    tank = channel(the_system, "PROBE", "tank")

    assert tank.get_level() == pytest.approx(CST_WATER + CST_HEAT)
    assert tank.get_level() != pytest.approx(CST_TEMPERATURE)


def test_constituent_fills_are_weighted_and_sum_to_the_total(the_system):
    """A fill is the share of the VOLUME, so the weights show up here."""
    tank = channel(the_system, "PROBE", "tank")

    assert tank.get_fill("water") == pytest.approx(CST_FILL_WATER)
    assert tank.get_fill("heat") == pytest.approx(CST_FILL_HEAT)
    assert tank.get_fill() == pytest.approx(CST_FILL_WATER + CST_FILL_HEAT)


# ----------------------------------------------------------------------
# What the extra aliases do to an existing model
# ----------------------------------------------------------------------


def test_an_observer_of_the_total_alone_still_connects_and_reads(the_system):
    """The compatibility fact, measured on the engine: extra exports are inert.

    PyCATSHOO matches an import to an export by alias and ignores an export
    nobody imports. Had it required the two alias sets to match, publishing per
    constituent would have broken every 1.x measurement link in existence.
    """
    legacy = channel(the_system, "LEGACY", "tank")

    assert legacy.is_connected
    assert legacy.flows == []
    assert legacy.get_level() == pytest.approx(CST_WATER + CST_HEAT)


def test_a_channel_reads_nothing_it_did_not_declare(the_system):
    """Asking a total-only channel for a constituent names the omission."""
    legacy = channel(the_system, "LEGACY", "tank")

    with pytest.raises(ValueError, match="constituent 'water'"):
        legacy.get_level("water")


# ----------------------------------------------------------------------
# A republished measurement carries constituents too
# ----------------------------------------------------------------------


def test_a_republisher_carries_the_constituents(the_system):
    """An observer cannot tell an instrument from the volume behind it."""
    relay = channel(the_system, "VOTER", "relay")

    assert relay.get_level("water") == pytest.approx(CST_WATER * CST_GAIN)
    assert relay.get_level("heat") == pytest.approx(CST_HEAT * CST_GAIN)


def test_one_gain_covers_every_reading(the_system):
    """A mode that kills an instrument kills all of what it publishes.

    A per-constituent gain would model a probe lying about the heat while
    staying honest about the water, which is two instruments, and two
    instruments are two components.
    """
    relay = channel(the_system, "VOTER", "relay")

    assert relay.get_level() == pytest.approx((CST_WATER + CST_HEAT) * CST_GAIN)
    assert relay.get_fill("water") == pytest.approx(CST_FILL_WATER * CST_GAIN)
    assert relay.get_fill("heat") == pytest.approx(CST_FILL_HEAT * CST_GAIN)


def test_a_gain_does_not_distort_the_ratio(the_system):
    """A uniformly wrong instrument still reports the right temperature.

    Worth pinning: it is what makes a scaled instrument detectable on the level
    and invisible on the intensive property, so a model relying on the ratio
    survives a derated probe while a model relying on the level does not.
    """
    relay = channel(the_system, "VOTER", "relay")

    assert relay.get_level("heat") / relay.get_level("water") == pytest.approx(
        CST_TEMPERATURE
    )


# ----------------------------------------------------------------------
# The refusals
# ----------------------------------------------------------------------


def test_an_unpublished_constituent_is_refused_at_connect(the_system):
    """And the message names what the volume holds, which the engine does not."""
    message = REFUSED["ghost"]

    assert "'plutonium'" in message
    assert "water, heat" in message
    assert "TANK" in message


def test_the_refusal_leaves_the_observer_unwired(the_system):
    """The engine refusal is atomic, so the guard has to run before it.

    Had muscadet let the engine refuse, GHOST would have lost the 'water' it
    asked for correctly along with the 'plutonium' it did not.
    """
    ghost = channel(the_system, "GHOST", "tank")

    assert not ghost.is_connected


def test_a_republisher_may_not_carry_what_its_source_does_not_hold(the_system):
    """The publishing side of the mistake the connect guard catches downstream.

    Left to the first integration step it surfaces as a bare ``KeyError`` out
    of a PDMP equation, naming neither the component, nor the channel, nor the
    volume's contents.
    """
    relay = the_system.comp["RELAY"]

    with pytest.raises(ValueError, match="which holds water, heat"):
        relay.add_measurement_out(
            name="bogus", source="tank", flows=["water", "plutonium"]
        )


def test_a_republisher_refuses_a_constituent_it_does_not_publish(the_system):
    """Symmetric with MeasurementIn, and the invariant depends on it.

    An observer is not supposed to be able to tell a capacity from a
    republisher. A plausible zero on one side against a naming error on the
    other is exactly the difference that forbids -- and zero is the one wrong
    answer that reads as a real measurement of an empty volume.
    """
    relay = the_system.comp["RELAY"].measurements_out["relay"]

    with pytest.raises(ValueError, match="constituent 'plutonium'"):
        relay.get_level("plutonium")

    with pytest.raises(ValueError, match="constituent 'plutonium'"):
        relay.get_fill("plutonium")


def test_a_duplicate_constituent_is_refused_at_declaration():
    """It would declare one alias twice, far from the declaration at fault."""
    with pytest.raises(ValueError, match="declared more than once"):
        muscadet.MeasurementIn(name="tank", flows=["water", "water"])


def test_a_duplicate_republished_constituent_is_refused_too():
    with pytest.raises(ValueError, match="declared more than once"):
        muscadet.MeasurementOut(name="relay", flows=["heat", "heat"])


# ----------------------------------------------------------------------
# The lag the republisher must not introduce
# ----------------------------------------------------------------------


def test_a_republished_fill_is_recomputed_not_read_back(the_system):
    """``current_fill`` exists so a republished fill matches its own level.

    ``var_fill`` is an explicit variable the capacity equation writes, so
    reading it back from the measurement equation of the same step lags one
    step behind the level. Here nothing moves, so both agree -- what this test
    guards is that the republisher goes through the recomputing path at all.
    """
    capacity = the_system.comp["TANK"].capacities["tank"]

    assert capacity.current_fill("water") == pytest.approx(CST_FILL_WATER)
    assert capacity.current_fill("heat") == pytest.approx(CST_FILL_HEAT)
    assert capacity.current_fill() == pytest.approx(CST_FILL_WATER + CST_FILL_HEAT)


def test_published_flows_reports_what_each_side_carries(the_system):
    """The list the connect guard compares against, on both kinds of publisher."""
    capacity = the_system.comp["TANK"].capacities["tank"]
    relay = the_system.comp["RELAY"].measurements_out["relay"]

    assert capacity.published_flows() == ["water", "heat"]
    assert relay.published_flows() == ["water", "heat"]


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
