"""The continuous/discrete type check holds on the RAW connection too (AE19).

``System.connect`` is the base-class method: the one the README prescribes for
wiring a measurement link, the one four shipped examples use for everything,
and the one ``connect_flow`` itself ends on. The check of AE19 lived only
*inside* ``connect_flow``, so the raw route accepted a discrete output feeding a
continuous input **silently** -- and a boolean signal then reads as a mass flow
of one unit per unit time, feeding every downstream balance, capacity level and
indicator with a quantity nothing produced.

Measured on the model below at ``399730d``: ``connect("C", "x_out", "D",
"x_in")`` between a ``FlowOut`` and a ``FlowContinuousIn`` returned normally and
``D.flows_in["x"].get_delivered()`` reported **1.0**.

What must NOT be refused is at least as important, and is why the check
resolves the message box back to a flow object rather than trusting its name:

* a **measurement link** (``{c}_level_out`` -> ``{c}_level_in``), which is the
  documented use of the raw ``connect`` and carries a reading, not a quantity;
* a **logic gate**'s export, standing behind no flow object at all;
* a **trigger** (``{f}_trigger_in``), whose flow lives in the *output* dict of
  the component receiving it;
* every discrete-to-discrete and continuous-to-continuous pair.
"""

import cod3s
import muscadet
import pytest

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    SourceContinuous,
)


class RawDiscreteOut(muscadet.ObjFlow):
    """A discrete output named ``x``."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="x", var_prod_default=True)


class RawContinuousIn(muscadet.ObjFlow):
    """A continuous input named ``x``."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="x")


class RawContinuousOut(muscadet.ObjFlow):
    """A continuous output named ``x``."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="x", var_fed_default=3.0)


class RawDiscreteIn(muscadet.ObjFlow):
    """A discrete input named ``x``, plus its availability channel."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="x", logic="or")


class RawTriggerSource(muscadet.ObjFlow):
    """A discrete output feeding a trigger."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="trig", var_prod_default=True)


class RawTriggerTarget(muscadet.ObjFlow):
    """A discrete output activated by an incoming trigger."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out_on_trigger(
            name="trig",
            trigger_time_up=0,
            trigger_time_down=0,
            trigger_logic="and",
            var_prod_default=True,
        )


class RawLevelReader(muscadet.ObjFlow):
    """Observes a capacity level over a measurement link, and nothing else."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="tank")


@pytest.fixture(scope="module")
def the_system():
    """One system carrying every raw-connection scenario, each on its own pair."""
    system = muscadet.System(name="RawConnectTypeCheck")

    # -- The defect: a discrete output raw-wired to a continuous input
    system.add_component(name="RC_DOUT", cls="RawDiscreteOut")
    system.add_component(name="RC_CIN", cls="RawContinuousIn")

    # -- The other direction
    system.add_component(name="RC_COUT", cls="RawContinuousOut")
    system.add_component(name="RC_DIN", cls="RawDiscreteIn")

    # -- The availability channel of the same mismatch
    system.add_component(name="RC_COUT2", cls="RawContinuousOut")
    system.add_component(name="RC_DIN2", cls="RawDiscreteIn")

    # -- Same-family pairs, which must go on wiring
    system.add_component(name="RC_DOUT2", cls="RawDiscreteOut")
    system.add_component(name="RC_DIN3", cls="RawDiscreteIn")
    system.add_component(name="RC_COUT3", cls="RawContinuousOut")
    system.add_component(name="RC_CIN2", cls="RawContinuousIn")

    # -- A trigger, whose receiving flow lives in ``flows_out``
    system.add_component(name="RC_TSRC", cls="RawTriggerSource")
    system.add_component(name="RC_TTGT", cls="RawTriggerTarget")

    # -- A measurement link, the documented use of the raw connect
    system.add_component(
        name="RC_TANK",
        cls="CapacityContinuous",
        flow="q",
        capacity=10.0,
        capacity_name="tank",
        ports="in",
    )
    system.add_component(name="RC_EYE", cls="RawLevelReader")

    return system


def test_a_discrete_output_raw_wired_to_a_continuous_input_raises(the_system):
    """The defect, in the exact shape the README's ``connect`` call has.

    Left through, ``RC_CIN`` reported a delivery of 1.0 -- a boolean signal
    read as one unit of matter per unit time.
    """
    with pytest.raises(ValueError, match="continuous and discrete"):
        the_system.connect("RC_DOUT", "x_out", "RC_CIN", "x_in")

    message_parts = ("RC_DOUT", "RC_CIN", "x_out", "x_in")
    with pytest.raises(ValueError) as exc_info:
        the_system.connect("RC_DOUT", "x_out", "RC_CIN", "x_in")
    for part in message_parts:
        assert part in str(exc_info.value)

    assert not the_system.comp["RC_DOUT"].is_connected_to("RC_CIN", "x")
    assert the_system.comp["RC_CIN"].flows_in["x"].get_delivered() == 0.0


def test_the_reverse_direction_raises_the_same_way(the_system):
    """A continuous output raw-wired to a discrete input is the same error."""
    with pytest.raises(ValueError, match="continuous and discrete"):
        the_system.connect("RC_COUT", "x_out", "RC_DIN", "x_in")

    assert not the_system.comp["RC_COUT"].is_connected_to("RC_DIN", "x")


def test_the_availability_channel_of_a_mismatch_raises_too(the_system):
    """``{f}_available_out`` resolves to ``f``, not to ``f_available``.

    ``auto_connect(..., available_connect=True)`` wires that channel, and it
    reaches the engine through the very same raw call.
    """
    with pytest.raises(ValueError, match="continuous and discrete"):
        the_system.connect("RC_COUT2", "x_available_out", "RC_DIN2", "x_available_in")


def test_same_family_raw_connections_are_untouched(the_system):
    """Discrete-to-discrete and continuous-to-continuous wire as they always did."""
    the_system.connect("RC_DOUT2", "x_out", "RC_DIN3", "x_in")
    the_system.connect("RC_COUT3", "x_out", "RC_CIN2", "x_in")

    assert the_system.comp["RC_DOUT2"].is_connected_to("RC_DIN3", "x")
    assert the_system.comp["RC_COUT3"].is_connected_to("RC_CIN2", "x")


def test_a_trigger_wires_over_the_raw_route(the_system):
    """``{f}_trigger_in`` belongs to an OUTPUT flow of the receiving component.

    Resolved in ``flows_in`` alone it would answer None and judge nothing --
    which is safe -- but a continuous source feeding a trigger would then pass;
    resolved as a discrete flow, the legitimate pair still wires.
    """
    the_system.connect("RC_TSRC", "trig_out", "RC_TTGT", "trig_trigger_in")

    assert the_system.comp["RC_TSRC"].is_connected_to("RC_TTGT", "trig")


def test_a_measurement_link_wires_over_the_raw_route(the_system):
    """The documented use of ``System.connect``, and it must not be judged.

    A measurement carries a reading, not a quantity: it belongs to neither
    family, and ``{c}_level_out`` resolves to no flow at all.
    """
    the_system.connect("RC_TANK", "tank_level_out", "RC_EYE", "tank_level_in")

    assert the_system.comp["RC_EYE"].measurements_in["tank"].is_connected


def test_the_resolution_never_invents_a_flow(the_system):
    """A box naming no declared flow resolves to None, so nothing is refused."""
    system = the_system

    assert system.flow_behind_message_box("RC_TANK", "tank_level_out", "out") is None
    assert system.flow_behind_message_box("RC_EYE", "tank_level_in", "in") is None
    assert system.flow_behind_message_box("RC_DOUT", "nope_out", "out") is None
    assert system.flow_behind_message_box("NO_SUCH_COMP", "x_out", "out") is None

    # ... and it does resolve the ones that are flows, on both suffixes.
    assert (
        system.flow_behind_message_box("RC_DOUT", "x_out", "out")
        is system.comp["RC_DOUT"].flows_out["x"]
    )
    assert (
        system.flow_behind_message_box("RC_CIN", "x_in", "in")
        is system.comp["RC_CIN"].flows_in["x"]
    )
    assert (
        system.flow_behind_message_box("RC_DIN", "x_available_in", "in")
        is system.comp["RC_DIN"].flows_in["x"]
    )


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
