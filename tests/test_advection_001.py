"""Advection: a quantity travelling with the stream that carries it.

A tank fed at one temperature and drained at the same rate mixes toward the
inlet temperature::

    dT/dt = q (T_in - T) / V        T(t) = T_in + (T_0 - T_in) exp(-q t / V)

The inflow half is a rule, ``cons={"water": 1}, prod={"water": 1, "heat":
T_in}``: heat is produced in proportion to the water passed. The outflow half
carries the tank's OWN temperature, so its enthalpy rate is ``q x H/V`` where
both H and V move. A conduit meters it, reading the two constituents over one
measurement channel.

Why this model no longer runs
-----------------------------
The outflow law is a Python function, ``muscadet.Transfer(fun=...)``. A run
always emits the declaration of its model first, and a model holding a Python
function has no declaration, so the run is refused. No declarative shape
replaces the law: ``ConductiveTransfer`` computes ``G x (a - b)`` over
operands that are a constant or one reading of a measurement channel, and the
operand missing here is a RATIO of two constituents of one measurement
channel. Until that operand exists, this file builds the same model and
asserts the refusal.
"""

import math

import cod3s
import pytest

import muscadet
from muscadet.kb.continuous import SourceContinuous  # noqa: F401

# A tank fed and drained at the same rate, so its volume is constant and the
# temperature relaxes toward the inlet with time constant V/q = 50 h.
ADV_RATE = 2.0  # water per hour
ADV_T_IN = 80.0  # inlet temperature
ADV_T_INIT = 20.0  # the tank starts here
ADV_VOLUME = 100.0  # water held
ADV_HORIZON = 20.0

#: Nothing here crosses a threshold, so without a repeating tick the solver
#: jumps straight to the horizon in one step and the trace has a single
#: point -- enough for the endpoint, useless for the trajectory.
ADV_TICK = ADV_HORIZON / 10.0

#: Heat occupies no volume, and a weight must be strictly positive.
ADV_HEAT_WEIGHT = 1e-9


class AdvInlet(muscadet.ObjFlow):
    """Transits water and produces the enthalpy that water carries."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="water")
        self.add_flow_continuous_out(name="water")
        self.add_flow_continuous_out(name="heat")
        self.add_rules(
            name="carry_in",
            rules=[dict(cons={"water": 1.0}, prod={"water": 1.0, "heat": ADV_T_IN})],
        )


class AdvTank(muscadet.ObjFlow):
    """One volume holding water and the heat dissolved in it."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("water", "heat"):
            self.add_flow_continuous_in(name=flow)
            self.add_flow_continuous_out(name=flow)

        self.add_capacity(
            name="tank",
            flows=[
                {"name": "water", "weight": 1.0},
                {"name": "heat", "weight": ADV_HEAT_WEIGHT},
            ],
            capacity=ADV_VOLUME * 10.0,
            side="out",
            content_init={
                "water": ADV_VOLUME,
                "heat": ADV_VOLUME * ADV_T_INIT,
            },
            # Declared, and it has to be: a volume claims for itself only what
            # its fill_rate says (R36). Without it the tank asks for no heat
            # and the inlet's enthalpy never arrives, however correctly the
            # rule computed it.
            fill_rate=math.inf,
        )


class AdvOutletMeter(muscadet.ObjFlow):
    """The outflow's enthalpy, metered at the MIXTURE temperature.

    The half a rule cannot state. Both terms of ``q x H/V`` move, so the rate
    is computed rather than declared, which is exactly what a conduit is for.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="heat")
        self.add_flow_continuous_out(name="heat")
        self.add_measurement_in(name="tank", flows=["water", "heat"])

        self.add_transfer(
            "carry_out",
            flows=["heat", "heat"],
            equation=muscadet.Transfer(fun=self.enthalpy_out, continuous=True),
        )

    def enthalpy_out(self, comp):
        channel = comp.measurements_in["tank"]
        water = channel.get_level("water")

        if water <= 0.0:
            return 0.0

        return ADV_RATE * channel.get_level("heat") / water


class AdvDrain(muscadet.ObjFlow):
    """Draws water at the inlet rate, and whatever the meter passes on."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="water", var_demand_default=ADV_RATE)
        self.add_flow_continuous_in(name="heat", var_demand_default=math.inf)


class AdvClock(muscadet.ObjFlow):
    """A horizon the interactive session can integrate toward."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


def build_system():
    system = muscadet.System(name="AdvSys")

    system.add_component(
        name="SRC", cls="SourceContinuous", flow="water", rate=ADV_RATE
    )
    system.add_component(name="IN", cls="AdvInlet")
    system.add_component(name="TANK", cls="AdvTank")
    system.add_component(name="METER", cls="AdvOutletMeter")
    system.add_component(name="OUT", cls="AdvDrain")

    system.connect_flow(source="SRC", target="IN", flow_name="water")
    system.connect_flow(source="IN", target="TANK", flow_name="water")
    system.connect_flow(source="IN", target="TANK", flow_name="heat")
    system.connect_flow(source="TANK", target="OUT", flow_name="water")
    system.connect_flow(source="TANK", target="METER", flow_name="heat")
    system.connect_flow(source="METER", target="OUT", flow_name="heat")
    system.connect("TANK", "tank_level_out", "METER", "tank_level_in")

    system.add_component(name="CLOCK", cls="AdvClock")
    system.comp["CLOCK"].add_atm2states(
        name="tick",
        st1="a",
        st2="b",
        occ_law_12={"cls": "delay", "time": ADV_TICK},
        occ_law_21={"cls": "delay", "time": ADV_TICK},
    )

    return system


def test_a_python_outflow_law_is_refused_when_the_model_runs():
    """The meter's law is a Python function, so the model has no declaration."""
    system = build_system()

    try:
        with pytest.raises(
            muscadet.declare.ComponentSpecError,
            match=(
                "Component METER, transfer carry_out: "
                "Transfer carries a Python function"
            ),
        ):
            system.isimu_start()
    finally:
        system.deleteSys()
        cod3s.terminate_session()
