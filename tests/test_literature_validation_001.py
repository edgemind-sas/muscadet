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

What is validated, and what is assumed
--------------------------------------
The effectiveness correlation is an INPUT here, not an output: a single-node
component cannot derive a counter-flow arrangement's effectiveness, and
pretending otherwise would be a circular test. What is checked is everything
downstream of it -- that a MUSCADET exchanger built on that closure reproduces
the outlet temperatures the correlation predicts, that its energy balance
closes, and that the quantity one stream loses is the quantity the other
gains. Those are the properties a modeller relies on and the ones a defect in
the sweeps would break.

Temperatures are carried as **enthalpy rates against a 0 degree reference**:
``heat = C x T`` with ``C`` the stream's capacity rate. That is the honest
lumped form for a formalism with no carried-flow notion yet -- the stream's
own mass flow does not cross the component here, only its enthalpy does.

The precision floor, measured here
----------------------------------
A quantity crossing a PyCATSHOO variable carries about **seven significant
digits**, not the fifteen a Python float suggests. Measured on this model: an
enthalpy balance that closes exactly in Python reads ``240000.0078125``
against ``240000.0`` on the way out, and ``0.0078125`` is exactly HALF AN ULP
of single precision at that magnitude (``numpy.spacing(float32(240000))`` is
``0.015625``). Nothing is lost -- it is representation, not leakage.

This matters beyond this file: an assertion tighter than ``rel=1e-7`` on a
large quantity is asserting against the engine's storage rather than against
the model, and it will fail for reasons that have nothing to do with the
physics. Absolute tolerances must be scaled to the magnitude involved, which
is why the balances below use a relative one.
"""

import math

import pytest

import cod3s
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

#: What the correlation says crosses the wall, and the outlet temperatures it
#: implies. Computed here, outside MUSCADET, and compared against it below.
DUTY = EFFECTIVENESS * C_MIN * (T_HOT_IN - T_COLD_IN)
T_HOT_OUT = T_HOT_IN - DUTY / C_HOT
T_COLD_OUT = T_COLD_IN + DUTY / C_COLD

#: Enthalpy rates the two streams arrive with.
H_HOT_IN = C_HOT * T_HOT_IN
H_COLD_IN = C_COLD * T_COLD_IN

#: The engine stores a quantity in single precision, so a balance closes to
#: about seven significant digits and no further. See the module docstring:
#: the residual was measured at half an ulp of float32.
ENGINE_PRECISION = 1e-7

#: A balance formed as the DIFFERENCE of two large stored quantities loses
#: more than that: the enthalpies are ~1.6e5 and the duty is ~6.8e4, so the
#: cancellation lifts a half-ulp storage residual into the seventh digit of
#: the difference. The honest tolerance is absolute and scaled to the ULP of
#: the operands (0.015625 at 160000 in single precision), not relative to
#: their difference -- a relative bound there measures the cancellation.
BALANCE_TOL = 4 * 0.015625

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


@pytest.fixture(scope="module")
def the_system():
    system = build_system()
    system.isimu_start()
    system.isimu_step_forward()
    yield system


def outlet(system, flow_name, capacity_rate):
    """The outlet temperature the exchanger delivers on one stream."""
    return system.comp["HX"].flows_out[flow_name].var_fed.value() / capacity_rate


# ----------------------------------------------------------------------
# The correlation itself, checked against the published worked values
# ----------------------------------------------------------------------


def test_the_effectiveness_matches_the_published_relation():
    """A guard on the arithmetic this module measures MUSCADET against.

    NTU = 1, Cr = 0.5 is a standard worked arrangement; if this drifts, every
    comparison below is measuring the wrong target.
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


def test_the_hot_outlet_matches_the_correlation(the_system):
    assert outlet(the_system, "h_hot", C_HOT) == pytest.approx(T_HOT_OUT, rel=1e-6)


def test_the_cold_outlet_matches_the_correlation(the_system):
    assert outlet(the_system, "h_cold", C_COLD) == pytest.approx(T_COLD_OUT, rel=1e-6)


def test_the_duty_crossing_the_wall_matches_the_correlation(the_system):
    pair = the_system.comp["HX"].transfers["duty"]

    assert pair.last_moved == pytest.approx(DUTY, rel=1e-6)


def test_the_energy_balance_closes(the_system):
    """What the hot stream lost is what the cold stream gained, exactly."""
    hot_lost = H_HOT_IN - the_system.comp["HX"].flows_out["h_hot"].var_fed.value()
    cold_gained = the_system.comp["HX"].flows_out["h_cold"].var_fed.value() - H_COLD_IN

    assert hot_lost == pytest.approx(cold_gained, abs=BALANCE_TOL)


def test_no_enthalpy_is_created_or_destroyed(the_system):
    """The component's total is what arrived, to the last significant digit."""
    delivered = sum(
        the_system.comp["HX"].flows_out[flow].var_fed.value()
        for flow in ("h_hot", "h_cold")
    )

    assert delivered == pytest.approx(H_HOT_IN + H_COLD_IN, rel=ENGINE_PRECISION)


def test_the_hot_stream_never_falls_below_the_cold_inlet(the_system):
    """The second law, as the correlation enforces it: no temperature crossing."""
    assert outlet(the_system, "h_hot", C_HOT) > T_COLD_IN
    assert outlet(the_system, "h_cold", C_COLD) < T_HOT_IN


def test_the_smaller_capacity_rate_swings_the_most(the_system):
    """Cr = 0.5, so the hot stream's drop is twice the cold stream's rise."""
    hot_drop = T_HOT_IN - outlet(the_system, "h_hot", C_HOT)
    cold_rise = outlet(the_system, "h_cold", C_COLD) - T_COLD_IN

    assert hot_drop == pytest.approx(
        cold_rise * (C_COLD / C_HOT), abs=BALANCE_TOL / C_HOT
    )


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
