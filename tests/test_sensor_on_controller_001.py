"""The shipped sensor rests on the controller's grammar (R18, R42).

``SensorContinuous`` was reimplemented rather than replaced, so its whole
observable surface is pinned down by the tests that predate the controller --
``test_kb_continuous_001``, ``test_heated_tank_001``, ``test_declare_001`` and
``test_measurement_combine_001`` -- and none of them was touched. Those are the
oracle; this file is the complement they cannot be, because a sensor that
re-spelled the band inline would satisfy every one of them.

What is pinned here is therefore the SHARING itself: that the direction
vocabulary, the operator of each band edge, the meaning of a missing release
edge and the default gain of a republication all come from
:class:`muscadet.CtrlBand` and :class:`muscadet.CtrlRepublish`, and are not
restated by the sensor. Re-inlining any of the four would leave the suite green
without this file, and would then drift the day the controller's own band
changes.

The one place the two genuinely differ is the SURFACE, and that is deliberate:
a sensor lands its outputs on discrete flows a model connects with
``connect_flow``, where a controller lands them on its own message boxes. What
they share is the semantics.
"""

import cod3s
import pytest

import muscadet
from muscadet.kb.continuous import SensorContinuous
from muscadet.obj_ctrl import CONTROL_IN_KEYS, CTRL_BAND_EDGE_OPERATORS

#: A level the band edges are declared at, distinct in each direction so that
#: an edge landing on the wrong one is visible rather than symmetric.
ACTIVATE = 4.0
RELEASE = 8.0


@pytest.fixture(scope="module")
def obs():
    """One system holding a sensor of each shape the reuse is read on."""
    system = muscadet.System(name="SensorOnController")

    system.add_component(
        name="BELOW",
        cls="SensorContinuous",
        measurement="tank",
        control="fill",
        direction="below",
        activate=ACTIVATE,
        release=RELEASE,
    )
    system.add_component(
        name="ABOVE",
        cls="SensorContinuous",
        measurement="tank",
        control="vent",
        direction="above",
        activate=RELEASE,
        release=ACTIVATE,
    )
    system.add_component(
        name="SINGLE",
        cls="SensorContinuous",
        measurement="tank",
        control="alarm",
        activate=ACTIVATE,
    )
    system.add_component(
        name="INSTRUMENT",
        cls="SensorContinuous",
        measurement="tank",
        control="alarm",
        activate=ACTIVATE,
        combine="median",
        publish="reported",
    )
    system.add_component(
        name="LEGACY",
        cls="SensorContinuous",
        measurement="tank",
        control="alarm",
        activate=ACTIVATE,
        combine_fun=min,
    )

    return {"system": system}


def declaration_of(obs, name, **kwargs):
    """The controller declaration a sensor renders its parameters as."""
    return obs["system"].comp[name].controller_declaration(kwargs)


# What a sensor IS, in the controller's own vocabulary
# ====================================================


def test_a_sensor_renders_as_a_controller_declaration(obs):
    """The two sections a controller is declared with, and nothing else."""
    declaration = declaration_of(
        obs,
        "BELOW",
        measurement="tank",
        control="fill",
        direction="below",
        activate=ACTIVATE,
        release=RELEASE,
    )

    assert set(declaration) == {"controls_in", "controls_out"}

    assert declaration["controls_in"] == [{"name": "tank"}]

    (output,) = declaration["controls_out"]
    assert output["name"] == "fill"
    assert output["kind"] == muscadet.CTRL_OUT_BOOL
    assert isinstance(output["emit"], muscadet.CtrlBand)


def test_a_republishing_sensor_declares_a_value_output_first(obs):
    """An instrument that also thresholds carries two outputs, in build order.

    The republication comes first, which is the order the channels have always
    been created in: reordering them would rename nothing but would move the
    publication after the control port in every read-back spec.
    """
    declaration = declaration_of(
        obs,
        "INSTRUMENT",
        measurement="tank",
        control="alarm",
        activate=ACTIVATE,
        combine="median",
        publish="reported",
    )

    assert [entry["name"] for entry in declaration["controls_out"]] == [
        "reported",
        "alarm",
    ]
    assert [entry["kind"] for entry in declaration["controls_out"]] == [
        muscadet.CTRL_OUT_VALUE,
        muscadet.CTRL_OUT_BOOL,
    ]
    assert isinstance(declaration["controls_out"][0]["emit"], muscadet.CtrlRepublish)

    # The observed channel says ``aggregate``, which is the controller's word
    # for it -- the measurement channel's own ``combine`` is what the compiler
    # renames it to, exactly as ObjCtrl.add_control_in does.
    assert declaration["controls_in"] == [{"name": "tank", "aggregate": "median"}]


# The band semantics come from CtrlBand, and are not restated
# ===========================================================


@pytest.mark.parametrize("direction", muscadet.CTRL_BAND_DIRECTIONS)
def test_each_band_edge_carries_the_operator_the_grammar_gives_it(obs, direction):
    """Which comparison goes on which edge is the band's answer, not the sensor's.

    Parameterised over the grammar's OWN list of directions, so a third one
    added to :class:`muscadet.CtrlBand` makes this test speak instead of
    silently covering two cases out of three.
    """
    name, control = {"below": ("BELOW", "fill"), "above": ("ABOVE", "vent")}[direction]
    sensor = obs["system"].comp[name]

    activate_op, release_op = CTRL_BAND_EDGE_OPERATORS[direction]

    assert sensor.flows_out[f"{control}_activate"].var_prod_cond_compare == [
        [{"op": activate_op, "value": ACTIVATE if direction == "below" else RELEASE}]
    ]
    assert sensor.flows_out[f"{control}_release"].var_prod_cond_compare == [
        [{"op": release_op, "value": RELEASE if direction == "below" else ACTIVATE}]
    ]


def test_a_sensor_declaring_no_release_takes_the_bands_degenerate_default(obs):
    """The two edges coincide, and it is CtrlBand that says so."""
    declaration = declaration_of(
        obs, "SINGLE", measurement="tank", control="alarm", activate=ACTIVATE
    )
    band = declaration["controls_out"][0]["emit"]

    assert band.release == pytest.approx(band.activate)
    assert band.direction == muscadet.CTRL_BAND_ABOVE

    # ... and the edge comparisons are mutually exclusive at that single level,
    # the release one being strict.
    sensor = obs["system"].comp["SINGLE"]
    edges = [
        sensor.flows_out[f"alarm_{suffix}"].var_prod_cond_compare[0][0]
        for suffix in ("activate", "release")
    ]
    assert [edge["value"] for edge in edges] == [ACTIVATE, ACTIVATE]
    assert {edge["op"] for edge in edges} == {">=", "<"}


def test_a_republication_takes_the_grammars_default_gain(obs):
    """One number, one spelling: the gain IS ``{publish}_level_gain``."""
    declaration = declaration_of(
        obs,
        "INSTRUMENT",
        measurement="tank",
        control="alarm",
        activate=ACTIVATE,
        publish="reported",
    )
    republish = declaration["controls_out"][0]["emit"]

    assert republish.gain == pytest.approx(1.0)
    assert republish.input == "tank"

    published = obs["system"].comp["INSTRUMENT"].measurements_out["reported"]
    assert published.get_gain() == pytest.approx(republish.gain)
    assert published.var_gain.basename() == "reported_level_gain"


# The refusals: the rule is the band's, the sentence is the sensor's
# ==================================================================


def test_the_direction_vocabulary_is_the_grammars_own(obs):
    """Every direction the band carries is a direction the sensor accepts.

    Asserted over the grammar's list rather than over two literals: the sensor
    is not free to carry a subset of it, and a direction added to one and not
    the other is exactly the drift this file exists to catch.
    """
    for direction in muscadet.CTRL_BAND_DIRECTIONS:
        declaration = declaration_of(
            obs,
            "SINGLE",
            measurement="tank",
            control="alarm",
            activate=ACTIVATE,
            direction=direction,
        )
        assert declaration["controls_out"][0]["emit"].direction == direction


def test_an_inverted_band_is_refused_in_the_sensors_own_words(obs):
    """The band decides, the sensor words it -- and the wording is the old one.

    A model has been reading this sentence since before the controller existed,
    so it is part of this component's surface and is asserted as such.
    """
    with pytest.raises(ValueError) as error:
        declaration_of(
            obs,
            "BELOW",
            measurement="tank",
            control="fill",
            direction="below",
            activate=RELEASE,
            release=ACTIVATE,
        )

    message = str(error.value)
    assert "the wrong way round" in message
    assert "releases at or above it" in message
    # ... and it names the two levels, so the slip is fixable from the message.
    assert str(RELEASE) in message and str(ACTIVATE) in message


# The inherited exception, and the door it does not open (R18)
# ===========================================================


def test_the_sensor_keeps_its_aggregation_function(obs):
    """``combine_fun`` predates the controller and goes on working HERE."""
    channel = obs["system"].comp["LEGACY"].measurements_in["tank"]

    assert channel.combine_fun is min

    declaration = declaration_of(
        obs,
        "LEGACY",
        measurement="tank",
        control="alarm",
        activate=ACTIVATE,
        combine_fun=min,
    )
    assert declaration["controls_in"] == [{"name": "tank", "combine_fun": min}]


def test_the_aggregation_function_has_no_controller_spelling(obs):
    """... and that is what makes it an exception rather than a feature.

    Every other key of the sensor's observed channel is renamed onto a key a
    controller input reads. This one is not, because a controller input has no
    such key: its list is closed precisely so an output grammar can reason
    about what an input computes.
    """
    renamed = dict(SensorContinuous.CONTROL_IN_FROM_SENSOR)

    assert renamed["combine"] == "aggregate"
    assert renamed["combine_fun"] == "combine_fun"

    accepted = CONTROL_IN_KEYS
    assert "aggregate" in accepted
    assert "level_default" in accepted
    assert "combine_fun" not in accepted

    # The policy name a sensor takes is the controller's closed list, though:
    # the exception is the function, not the vocabulary.
    assert "median" in muscadet.CONTROL_AGGREGATIONS


def test_delete(obs):
    obs["system"].deleteSys()
    cod3s.terminate_session()
