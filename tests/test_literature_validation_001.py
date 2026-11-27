"""MUSCADET models measured against published closed-form results.

A test that only compares MUSCADET to MUSCADET can be uniformly wrong. This
module compares it to results computed outside it, from the engineering
literature, so a defect that moves every number the same way is still caught.

Sources, obtained through OpenAlex and Crossref during this work rather than
from memory:

* R. K. Shah and D. P. Sekulic, *Fundamentals of Heat Exchanger Design*, Wiley
  (2003), DOI ``10.1002/9780470172605`` -- the effectiveness-NTU relations, of
  which the counter-flow case is used below;
* T. Aldemir, "Computer-Assisted Markov Failure Modeling of Process Control
  Systems", *IEEE Transactions on Reliability* (1987), DOI
  ``10.1109/tr.1987.5222318``, and M. Marseguerra and E. Zio, "Monte Carlo
  approach to PSA for dynamic process systems", *Reliability Engineering &
  System Safety* (1996), DOI ``10.1016/0951-8320(95)00131-x`` -- the heated
  tank, already ported in ``tests/test_heated_tank_001.py``.

The model
---------
A counter-flow exchanger as one component: two enthalpy streams (``heat = C x
T`` against a 0 degree reference) and a transfer pair moving the duty
``eps x Cmin x (T_hot_in - T_cold_in)`` between them. The effectiveness
correlation is an INPUT, computed here outside MUSCADET, and its arithmetic is
still checked below.

Why this model no longer runs
-----------------------------
The duty law is a Python function, ``muscadet.Transfer(fun=...)``, forming
both temperatures from the enthalpy rates the component receives. A run always
emits the declaration of its model first, and a model holding a Python
function has no declaration, so the run is refused. No declarative shape
replaces the law: ``ConductiveTransfer`` computes ``G x (a - b)`` over
operands that are a constant or a measurement reading, and the operand missing
here is one reading the component's OWN INPUT DELIVERIES (scaled by a declared
capacity rate). Until that operand exists, this file builds the same model and
asserts the refusal.
"""

import math

import cod3s
import pytest

import muscadet

# Counter-flow exchanger (Shah & Sekulic, effectiveness-NTU)
# =========================================================
#
# A standard worked arrangement: the hot stream is the one with the smaller
# capacity rate, so it is the one that changes temperature the most.

C_HOT = 2000.0  # W/K, capacity rate of the hot stream
C_COLD = 4000.0  # W/K, capacity rate of the cold stream
T_HOT_IN = 80.0  # degrees
T_COLD_IN = 20.0  # degrees
UA = 2000.0  # W/K, overall conductance x area

C_MIN = min(C_HOT, C_COLD)
C_MAX = max(C_HOT, C_COLD)
C_RATIO = C_MIN / C_MAX
NTU = UA / C_MIN


def counterflow_effectiveness(ntu, ratio):
    """The counter-flow relation of Shah & Sekulic, computed independently.

    ``eps = (1 - exp(-NTU (1 - Cr))) / (1 - Cr exp(-NTU (1 - Cr)))``, with the
    balanced case ``Cr = 1`` taken at its limit ``NTU / (1 + NTU)``.
    """
    if math.isclose(ratio, 1.0):
        return ntu / (1.0 + ntu)

    decay = math.exp(-ntu * (1.0 - ratio))

    return (1.0 - decay) / (1.0 - ratio * decay)


EFFECTIVENESS = counterflow_effectiveness(NTU, C_RATIO)

#: Enthalpy rates the two streams arrive with.
H_HOT_IN = C_HOT * T_HOT_IN
H_COLD_IN = C_COLD * T_COLD_IN

LIT_CLOCK = 5.0


class LitStream(muscadet.ObjFlow):
    """A stream arriving at a fixed enthalpy rate."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name=kwargs["flow"], var_fed_default=kwargs["enthalpy"]
        )


class LitDrain(muscadet.ObjFlow):
    """Takes whatever leaves the exchanger, so nothing is capped downstream."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name=kwargs["flow"], var_demand_default=math.inf)


class LitExchanger(muscadet.ObjFlow):
    """Two enthalpy streams, and a pair moving the duty between them.

    The equation forms both temperatures itself, from the raw enthalpy rates
    the component receives divided by the declared capacity rates -- which is
    the whole of KD2: MUSCADET hands it no intensive property.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("h_hot", "h_cold"):
            self.add_flow_continuous_in(name=flow, var_demand_default=math.inf)
            self.add_flow_continuous_out(name=flow)

        self.add_transfer(
            "duty",
            flows=["h_hot", "h_cold"],
            equation=muscadet.Transfer(fun=self.duty, continuous=True),
        )

    def duty(self, comp):
        t_hot = comp.get_input_delivered("h_hot") / C_HOT
        t_cold = comp.get_input_delivered("h_cold") / C_COLD

        return EFFECTIVENESS * C_MIN * (t_hot - t_cold)


def build_system():
    system = muscadet.System(name="LitSys")

    system.add_component(name="HOT", cls="LitStream", flow="h_hot", enthalpy=H_HOT_IN)
    system.add_component(
        name="COLD", cls="LitStream", flow="h_cold", enthalpy=H_COLD_IN
    )
    system.add_component(name="HX", cls="LitExchanger")
    system.add_component(name="HOT_OUT", cls="LitDrain", flow="h_hot")
    system.add_component(name="COLD_OUT", cls="LitDrain", flow="h_cold")

    system.connect_flow(source="HOT", target="HX", flow_name="h_hot")
    system.connect_flow(source="COLD", target="HX", flow_name="h_cold")
    system.connect_flow(source="HX", target="HOT_OUT", flow_name="h_hot")
    system.connect_flow(source="HX", target="COLD_OUT", flow_name="h_cold")

    system.comp["HOT"].add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": LIT_CLOCK},
        cond_occ_21=False,
    )

    return system


# ----------------------------------------------------------------------
# The correlation itself, checked against the published worked values
# ----------------------------------------------------------------------


def test_the_effectiveness_matches_the_published_relation():
    """A guard on the arithmetic this module measures MUSCADET against.

    NTU = 1, Cr = 0.5 is a standard worked arrangement; if this drifts, every
    use of it is measuring the wrong target.
    """
    assert NTU == pytest.approx(1.0)
    assert C_RATIO == pytest.approx(0.5)
    assert EFFECTIVENESS == pytest.approx(0.5647, abs=1e-4)


def test_the_balanced_case_takes_its_limit():
    """``Cr = 1`` is the removable singularity of the relation."""
    assert counterflow_effectiveness(1.0, 1.0) == pytest.approx(0.5)
    assert counterflow_effectiveness(2.0, 1.0) == pytest.approx(2.0 / 3.0)


# ----------------------------------------------------------------------
# What MUSCADET does with it
# ----------------------------------------------------------------------


def test_a_python_duty_law_is_refused_when_the_model_runs():
    """The duty law is a Python function, so the model has no declaration."""
    system = build_system()

    try:
        with pytest.raises(
            muscadet.declare.ComponentSpecError,
            match="Component HX, transfer duty: Transfer carries a Python function",
        ):
            system.isimu_start()
    finally:
        system.deleteSys()
        cod3s.terminate_session()
