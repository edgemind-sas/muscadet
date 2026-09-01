"""An observation input takes SEVERAL sources and reduces them to one (R40).

The previous unit left a controller input capped at one publisher: a
measurement channel that declares no combination policy sets ``setCnctMax(1)``,
and the controller had no way of declaring one. This module pins down what
lifts that cap and what it lifts it into.

* an input declares an ``aggregate``, chosen from a **closed list** -- minimum,
  maximum, mean, median, sum -- and several sources may then publish onto it;
* the reduction is the one measurement channels already carry, semantics
  included: a median over an EVEN count is the mean of the two central
  readings, which is what ``statistics.median`` defines and what this library
  decided not to tie-break;
* an aggregation outside the list is refused at the declaration, **a Python
  callable included**: the shipped sensor accepts one on its own surface, and
  that stays an exception of that surface;
* an input mixing a tank LEVEL and a delivered RATE reduces both without
  distinguishing where either came from, which is what "one observer concept"
  means when it is observed rather than asserted;
* declaring no aggregation keeps the cap, so many-to-one is never reached
  without saying how the readings combine.

The whole system is built, wired and driven once in the fixture, and every test
reads back a snapshot: PyCATSHOO forbids more than one live system per process.
"""

import cod3s
import muscadet
import pytest

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

#: What the three publishers of the vote publish. Deliberately asymmetric: 100
#: is the wild one, so a median and a mean cannot agree by accident.
VOTE_A, VOTE_B, VOTE_C = 20.0, 24.0, 100.0

#: What each aggregation must make of those three readings. Every one of them
#: is a different number, so no assertion here can pass for the wrong reason.
VOTE_EXPECTED = {
    "median": VOTE_B,  # 24: the middle reading, untouched by the wild one
    "mean": 48.0,  # dragged a third of the way to it
    "min": VOTE_A,
    "max": VOTE_C,
    "sum": 144.0,
}

#: A median over TWO readings, on the pair that straddles the wild one. The
#: convention is the measurement channel's -- the mean of the two central
#: readings -- so this is 60, not 20 and not 100.
PAIR_EXPECTED = 0.5 * (VOTE_A + VOTE_C)

# -- The mixed montage: a tank observed by one instrument, the rate a source
# -- delivers observed by another, both publishing onto ONE controller input.
MIX_SOURCE_RATE = 1.0
MIX_DEMAND = 2.0
MIX_INIT = 30.0
MIX_VOLUME = 1000.0
MIX_DATE = 4.0

#: The tank is drained at ``MIX_DEMAND`` and refilled at ``MIX_SOURCE_RATE``,
#: which is less, so it gives up the difference out of its own stock.
MIX_LEVEL = MIX_INIT - (MIX_DEMAND - MIX_SOURCE_RATE) * MIX_DATE

#: The source is the binding constraint, so it delivers its whole capability.
#: Neither this nor the level above is the channel's unconnected default, and
#: the two differ by more than an order of magnitude: their mean can only come
#: from having read both.
MIX_RATE = MIX_SOURCE_RATE

#: What an aggregating input wired to nobody must read. A value nothing in this
#: file could produce, so a zero standing in for it would be visible.
LONELY_DEFAULT = -3.5


class ObjCtrlAggLevelProbe(muscadet.ObjFlow):
    """An instrument reporting the level of the tank it watches."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="tank")
        self.add_measurement_out(name="mix", source="tank")


class ObjCtrlAggRateProbe(muscadet.ObjFlow):
    """An instrument reporting the rate the output it watches delivers (R38).

    It publishes on the SAME channel name as the level probe above, which is
    what puts a level and a rate on one controller input.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="q", kind="rate")
        self.add_measurement_out(name="mix", source="q")


def step_to(system, date, limit=40):
    """Step the interactive session until it reaches ``date``."""
    for _ in range(limit):
        if system.currentTime() >= date:
            return
        system.isimu_step_forward()

    raise AssertionError(f"the session did not reach {date} in {limit} steps")


@pytest.fixture(scope="module")
def obs():
    """Build the two montages, drive one session, record what it produced."""
    observations = {}

    system = muscadet.System(name="ObjCtrlAggregation001")

    # -- Montage 1: three publishers, one voter per aggregation.
    for publisher in ("PUB_A", "PUB_B", "PUB_C"):
        system.add_component(
            name=publisher,
            cls="ObjCtrl",
            controls_out=[{"name": "reading", "kind": "value"}],
        )

    for policy in VOTE_EXPECTED:
        voter = f"VOTE_{policy.upper()}"
        system.add_component(
            name=voter,
            cls="ObjCtrl",
            controls_in=[{"name": "reading", "aggregate": policy}],
        )
        for publisher in ("PUB_A", "PUB_B", "PUB_C"):
            system.connect(publisher, "reading_level_out", voter, "reading_level_in")

    # Two sources only, on the pair that straddles the wild reading.
    system.add_component(
        name="PAIR",
        cls="ObjCtrl",
        controls_in=[{"name": "reading", "aggregate": "median"}],
    )
    for publisher in ("PUB_A", "PUB_C"):
        system.connect(publisher, "reading_level_out", "PAIR", "reading_level_in")

    # An aggregating input wired to nobody: the EMPTY reduction, which is not
    # the code path a capped input takes to reach the same number.
    system.add_component(
        name="LONELY",
        cls="ObjCtrl",
        controls_in=[
            {
                "name": "reading",
                "aggregate": "median",
                "level_default": LONELY_DEFAULT,
            }
        ],
    )

    # -- Montage 2: a level and a rate reduced on one input.
    system.add_component(
        name="SRC", cls="SourceContinuous", flow="q", rate=MIX_SOURCE_RATE
    )
    system.add_component(
        name="CAP",
        cls="CapacityContinuous",
        flow="q",
        capacity=MIX_VOLUME,
        capacity_name="tank",
        content_init={"q": MIX_INIT},
    )
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="q", demand=MIX_DEMAND
    )
    system.connect_flow(source="SRC", target="CAP", flow_name="q")
    system.connect_flow(source="CAP", target="SINK", flow_name="q")

    system.add_component(name="LPROBE", cls="ObjCtrlAggLevelProbe")
    system.add_component(name="RPROBE", cls="ObjCtrlAggRateProbe")
    system.connect("CAP", "tank_level_out", "LPROBE", "tank_level_in")
    system.connect("SRC", "q_rate_out", "RPROBE", "q_rate_in")

    system.add_component(
        name="MIX",
        cls="ObjCtrl",
        controls_in=[{"name": "mix", "aggregate": "mean"}],
    )
    system.connect("LPROBE", "mix_level_out", "MIX", "mix_level_in")
    system.connect("RPROBE", "mix_level_out", "MIX", "mix_level_in")

    # -- What an input that declared NO aggregation does with a second
    # -- publisher. The refusal comes from the engine's connection cap, which
    # -- is exactly the cap an aggregation lifts.
    system.add_component(name="CAPPED", cls="ObjCtrl", controls_in=[{"name": "mix"}])
    system.connect("LPROBE", "mix_level_out", "CAPPED", "mix_level_in")
    try:
        system.connect("RPROBE", "mix_level_out", "CAPPED", "mix_level_in")
        observations["err_capped"] = None
    except Exception as err:  # noqa: BLE001 -- the refusal is the observation
        observations["err_capped"] = err

    # A date the interactive session can always step to: nothing else in the
    # mixed montage would make the solver stop before the horizon.
    system.comp["SINK"].add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": MIX_DATE},
        cond_occ_21=False,
    )

    system.isimu_start()

    step_to(system, MIX_DATE)

    capped_input = system.comp["CAPPED"].controls_in["mix"]
    observations["capped_cnx"] = {
        "level": capped_input.var_level.cnctCount(),
        "fill": capped_input.var_fill.cnctCount(),
    }

    mixed_input = system.comp["MIX"].controls_in["mix"]
    observations["mixed"] = {
        "time": system.currentTime(),
        "level": system.comp["CAP"].capacities["tank"].get_quantity("q"),
        "rate": system.comp["SRC"].flows_out["q"].var_fed.value(),
        "published": [
            system.comp[probe].measurements_out["mix"].get_level()
            for probe in ("LPROBE", "RPROBE")
        ],
        "readings": mixed_input.readings(mixed_input.var_level, 0.0),
        "aggregated": mixed_input.get_reading(),
    }

    # The vote is read with no step in between: a published reading is a plain
    # component variable, and the engine restores those at every step.
    for publisher, reading in (
        ("PUB_A", VOTE_A),
        ("PUB_B", VOTE_B),
        ("PUB_C", VOTE_C),
    ):
        system.comp[publisher].controls_out["reading"].publish(reading)

    observations["vote"] = {
        policy: system.comp[f"VOTE_{policy.upper()}"]
        .controls_in["reading"]
        .get_reading()
        for policy in VOTE_EXPECTED
    }
    observations["pair"] = system.comp["PAIR"].controls_in["reading"].get_reading()
    observations["lonely"] = system.comp["LONELY"].controls_in["reading"].get_reading()
    observations["pair_readings"] = len(
        system.comp["PAIR"]
        .controls_in["reading"]
        .readings(system.comp["PAIR"].controls_in["reading"].var_level, 0.0)
    )

    system.isimu_stop()

    observations["system"] = system

    return observations


# Several sources, one value
# ==========================


@pytest.mark.parametrize("policy", sorted(VOTE_EXPECTED))
def test_each_aggregation_reduces_the_three_readings_its_own_way(obs, policy):
    assert obs["vote"][policy] == pytest.approx(VOTE_EXPECTED[policy])


def test_the_median_is_the_middle_reading_and_not_the_wild_one(obs):
    """What a redundant set is for: the outlier moves the mean, not the median."""
    assert obs["vote"]["median"] == pytest.approx(VOTE_B)
    assert obs["vote"]["mean"] != pytest.approx(VOTE_B)


def test_an_even_count_takes_the_convention_of_the_measurement_channel(obs):
    """Two sources: the mean of the two central readings, not a tie-break.

    The convention is ``muscadet.combine_median``'s and is not restated here --
    a controller that decided its own would be a second answer to a settled
    question.
    """
    assert obs["pair_readings"] == 2
    assert obs["pair"] == pytest.approx(PAIR_EXPECTED)
    assert obs["pair"] == pytest.approx(muscadet.combine([VOTE_A, VOTE_C], "median"))


def test_an_aggregating_input_wired_to_nobody_reads_its_declared_default(obs):
    """The EMPTY reduction, a different path from the single-source one.

    An input that declared no aggregation reads its default through
    ``sumValue(default)``; one that declared an aggregation reduces an empty
    list of readings instead, and has to land on the same number rather than on
    a zero that would pass for a real reading.
    """
    assert obs["lonely"] == pytest.approx(LONELY_DEFAULT)


# One observer, whatever it observes
# ==================================


def test_an_input_reduces_a_level_and_a_rate_without_telling_them_apart(obs):
    mixed = obs["mixed"]

    # The two sources are of different natures and of different magnitudes...
    assert mixed["level"] == pytest.approx(MIX_LEVEL)
    assert mixed["rate"] == pytest.approx(MIX_RATE)
    # ... they arrive on ONE input, as two readings...
    assert mixed["published"] == pytest.approx([MIX_LEVEL, MIX_RATE])
    assert mixed["readings"] == pytest.approx([MIX_LEVEL, MIX_RATE])
    # ... and the aggregation makes one number of them, with no term for where
    # either came from.
    assert mixed["aggregated"] == pytest.approx(0.5 * (MIX_LEVEL + MIX_RATE))


def test_the_mixed_reading_is_neither_of_its_sources_nor_a_default(obs):
    """Guards the assertion above against passing for the wrong reason."""
    mixed = obs["mixed"]

    assert mixed["aggregated"] != pytest.approx(mixed["level"])
    assert mixed["aggregated"] != pytest.approx(mixed["rate"])
    assert mixed["aggregated"] != pytest.approx(0.0)


# What a controller input refuses
# ===============================


def test_an_aggregation_outside_the_closed_list_is_refused_by_name(obs):
    controller = obs["system"].comp["MIX"]

    with pytest.raises(ValueError) as error:
        controller.add_control_in(name="spare", aggregate="mediane")

    message = str(error.value)
    assert "mediane" in message
    assert "median" in message
    # Refused before anything was built: no variable, no box, no entry.
    assert "spare" not in controller.controls_in


def test_a_python_callable_is_refused_as_an_aggregation(obs):
    """The closed list is closed to a designer's function too.

    ``SensorContinuous`` accepts a callable on its own surface and keeps it;
    that is an inherited exception of that surface, not a door onto this one.
    """
    controller = obs["system"].comp["MIX"]

    with pytest.raises(ValueError) as error:
        controller.add_control_in(name="spare", aggregate=lambda values: min(values))

    message = str(error.value)
    assert "callable" in message
    assert "median" in message
    assert "spare" not in controller.controls_in


def test_the_measurement_keys_stay_out_of_a_controller_declaration(obs):
    """One door onto the aggregation, and the wire's own keys are not it."""
    controller = obs["system"].comp["MIX"]

    for key in ("combine", "combine_fun"):
        with pytest.raises(ValueError) as error:
            controller.add_control_in(name="spare", **{key: "median"})

        message = str(error.value)
        assert repr(key) in message
        assert "aggregate" in message
        assert "spare" not in controller.controls_in


def test_an_input_declaring_no_aggregation_keeps_its_one_publisher_cap(obs):
    """Many-to-one is never reached without saying how the readings combine.

    Asserted by the refusal's OCCURRENCE and by its effect, never by its text.
    A PyCATSHOO exception carries its message in a process-global buffer that
    the teardown of a previously built system empties, so ``str(err)`` is the
    full diagnostic when this file runs alone and the empty string when it runs
    after another one -- an assertion on the wording passes and fails with the
    collection order.
    """
    assert obs["err_capped"] is not None
    # Refused whole, not by alias: the cap trips on the fill reference, and the
    # level one is left at its single publisher too.
    assert obs["capped_cnx"] == {"level": 1, "fill": 1}


def test_an_input_declaring_one_lifted_it(obs):
    """The same second publisher, on the input that declared an aggregation."""
    assert len(obs["mixed"]["readings"]) == 2


def test_delete(obs):
    obs["system"].deleteSys()
    cod3s.terminate_session()
