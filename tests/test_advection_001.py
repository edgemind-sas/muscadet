"""Advection: a quantity travelling with the stream that carries it.

A tank fed at one temperature and drained at the same rate mixes toward the
inlet temperature::

    dT/dt = q (T_in - T) / V        T(t) = T_in + (T_0 - T_in) exp(-q t / V)

This is the term the heated-tank benchmark needs and the one MUSCADET was
documented as unable to express. That was true when it was written and is no
longer: the two halves are expressible, each by a different mechanism, and
**both mechanisms shipped in the same release**. Nothing here needs a
"carried flow" notion.

The inflow: a rule
------------------
A rule's coefficients are per unit consumed, so::

    cons={"water": 1}, prod={"water": 1, "heat": T_in}

produces heat exactly in proportion to the water it passes, with nothing tying
them by declaration. The water transits unchanged and the enthalpy it carries
appears beside it. Measured below: 160 per hour for ``q = 2`` at ``T_in = 80``,
exactly, and it follows the water rate on its own.

The outflow: a conduit
----------------------
This is the half a rule cannot state. The outflow carries the tank's **own**
temperature, so its enthalpy rate is ``q x H/V`` where both H and V move. A
rule coefficient is a constant; a demand default is a constant. What says
"move exactly this much, which I computed" is a transfer pair, and a conduit
naming one flow twice meters exactly that transit.

It reads the two constituents over one measurement channel, which is the other
half of why this works now: a channel that published only the total could not
give H and V separately, and the total of a water-plus-heat volume is neither.

What is still missing, precisely
--------------------------------
Not the physics: the **declaration**. Nothing ties the carried quantity to its
carrier, so the modeller states the association twice, once in the rule's
coefficients and once in the conduit's equation, and MUSCADET checks neither
against the other. A declared association would make both automatic and is
worth having; its absence is an ergonomic gap, not a modelling one.
"""

import math

import pytest

import cod3s
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

#: The enthalpy the inlet carries per hour: q x T_in, and nothing declares it.
ADV_INLET_ENTHALPY = ADV_RATE * ADV_T_IN

#: Heat occupies no volume, and a weight must be strictly positive.
ADV_HEAT_WEIGHT = 1e-9


def analytic(time):
    """``T(t)`` of ``dT/dt = q (T_in - T) / V``."""
    decay = math.exp(-ADV_RATE * time / ADV_VOLUME)

    return ADV_T_IN + (ADV_T_INIT - ADV_T_IN) * decay


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


@pytest.fixture(scope="module")
def the_system():
    system = build_system()
    system.isimu_start()
    yield system


@pytest.fixture(scope="module")
def the_run(the_system):
    """Drive to the horizon and snapshot the readings on the way."""
    trace = []

    for _ in range(60):
        if not the_system.isimu_active_transitions():
            break
        the_system.isimu_step_forward()
        capacity = the_system.comp["TANK"].capacities["tank"]
        trace.append(
            {
                "t": the_system.currentTime(),
                "water": capacity.get_quantity("water"),
                "heat": capacity.get_quantity("heat"),
            }
        )
        if the_system.currentTime() >= ADV_HORIZON:
            break

    return trace


def held(entry):
    return entry["heat"] / entry["water"]


# ----------------------------------------------------------------------
# The inflow half: a rule carries the enthalpy
# ----------------------------------------------------------------------


def test_the_inlet_produces_enthalpy_in_proportion_to_the_water(the_system):
    """160 per hour for 2 of water at 80 degrees, and nothing declared it."""
    _, production = the_system.comp["IN"].evaluate_production()

    assert production["water"] == pytest.approx(ADV_RATE)
    assert production["heat"] == pytest.approx(ADV_INLET_ENTHALPY)


def test_the_enthalpy_is_tied_to_whatever_water_flows(the_system):
    """The coupling itself, asserted against the water actually arriving.

    This is what makes it advection rather than a coincidence: the heat rate
    is the water rate times the inlet temperature at ANY rate, and no second
    declaration ties them.
    """
    inlet = the_system.comp["IN"]
    _, production = inlet.evaluate_production()
    water = inlet.get_input_delivered("water")

    assert production["heat"] == pytest.approx(water * ADV_T_IN)
    assert production["water"] == pytest.approx(water)


# ----------------------------------------------------------------------
# The outflow half: a conduit meters the mixture
# ----------------------------------------------------------------------


def test_the_meter_carries_the_tanks_own_temperature(the_system, the_run):
    """``q x H/V``, both terms moving. No rule coefficient can say this."""
    meter = the_system.comp["METER"]
    capacity = the_system.comp["TANK"].capacities["tank"]
    expected = ADV_RATE * capacity.get_quantity("heat") / capacity.get_quantity("water")

    assert meter.transfers["carry_out"].last_moved == pytest.approx(expected, rel=1e-4)


# ----------------------------------------------------------------------
# The two halves together: the analytic solution
# ----------------------------------------------------------------------


def test_the_volume_is_conserved(the_run):
    """In-rate equals out-rate, so the water never moves."""
    for entry in the_run:
        assert entry["water"] == pytest.approx(ADV_VOLUME, rel=1e-6)


def test_the_tank_was_declared_at_its_starting_temperature(the_system):
    """Read as a declaration, not off the trace: the run's first stop is at
    the first tick, by which time the mixing has already begun."""
    capacity = the_system.comp["TANK"].capacities["tank"]

    assert ADV_VOLUME * ADV_T_INIT == pytest.approx(capacity.content_init["heat"])


def test_the_temperature_relaxes_toward_the_inlet(the_run):
    """Monotone, and never past the inlet: the second law on a mixer."""
    temperatures = [held(entry) for entry in the_run]

    assert temperatures == sorted(temperatures)
    assert max(temperatures) < ADV_T_IN


def test_the_relaxation_matches_the_analytic_solution(the_run):
    """The whole point: ``T(t) = T_in + (T_0 - T_in) exp(-q t / V)``.

    39.78 degrees at 20 h for this arrangement, and the model lands on it.
    """
    final = the_run[-1]

    assert final["t"] == pytest.approx(ADV_HORIZON, rel=1e-6)
    assert held(final) == pytest.approx(analytic(ADV_HORIZON), rel=1e-3)


def test_every_stop_matches_the_analytic_solution(the_run):
    """Not just the endpoint: the whole trajectory."""
    for entry in the_run:
        assert held(entry) == pytest.approx(analytic(entry["t"]), rel=2e-3)


def test_the_energy_balance_closes_over_the_run(the_run):
    """What accumulated is what came in minus what the meter carried out."""
    first, last = the_run[0], the_run[-1]
    gained = last["heat"] - first["heat"]
    elapsed = last["t"] - first["t"]

    # Mean outflow enthalpy over the run, from the trapezoid of q x H/V.
    carried = sum(
        ADV_RATE * (held(a) + held(b)) / 2 * (b["t"] - a["t"])
        for a, b in zip(the_run, the_run[1:])
    )

    assert gained == pytest.approx(ADV_INLET_ENTHALPY * elapsed - carried, rel=5e-3)


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
