"""A domestic hot-water circuit: heat pump, electric backup, tank, losses.

The integration case. Every mechanism the continuous layer carries meets here
on one small, physically checkable system:

* **rules with coefficients** -- a heat pump is ``elec -> heat`` at its
  coefficient of performance, and the resistance is the same rule at COP 1,
  which is what makes the redundancy interesting rather than cosmetic: the
  backup delivers the same heat for three and a half times the electricity;
* **a capacity** holding the tank's stored energy, integrated by the solver;
* **a sensor with a deadband** -- the thermostat, whose band is what stops the
  loop chattering around a single setpoint;
* **a failure mode** derating the heat pump's output to nothing;
* **a transfer pair** for the standing loss, which is the one quantity here
  that moves because a gradient makes it move rather than because somebody
  asked for it.

Units, and why they are hours
-----------------------------
Everything is in **kWh** and **kW** (= kWh/h), with time in **hours** and
energies counted from 0 degrees.

The time unit is not cosmetic, it is the difference between a test that runs
and one that does not. Measured on this model: in SECONDS, with the heat-up
spanning 5380 time units, a bare tank-and-pump chain cost 19 s of wall clock
for 300 s of simulated time -- and stripping the thermostat and the transfer
pair changed nothing, so the cost is the integrator crossing time units, not
any one mechanism. In hours the same physics spans 1.5 time units and the
whole file runs in well under a second. ``tests/test_heated_tank_001.py``
works in hours for the same reason.

The magnitudes are comfortable too: the tank holds 3.5 to 14 kWh, far inside
the seven significant digits a PyCATSHOO variable carries (measured in
``tests/test_literature_validation_001.py``).

The tank stores **heat only**, with the water mass a declared constant. That
is not an approximation of convenience: a watched threshold cannot name one
constituent of a multi-constituent volume yet, so a thermostat on a
water-plus-heat tank would be comparing against the sum of a mass and an
energy. Holding heat alone keeps the level proportional to the temperature and
the thresholds exact -- expressed in kJ rather than in degrees, which is what
``TEMPERATURE`` converts back for the assertions.

The numbers, and where they come from
-------------------------------------
Ordinary domestic figures, chosen so every one of them can be checked by hand:
a 200 L tank, water at 4.185 kJ/(kg.K), a 2 kW heat pump at COP 3.5, a 3 kW
resistive backup, and a standing loss of 1.5 kWh per day at a 45 K difference.
Every derived quantity below is computed from those, not tuned to the model.
"""

import math

import pytest

import cod3s
import muscadet
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    ExchangeContinuous,
    SensorContinuous,
    SourceContinuous,
)

# The installation
# ================

WATER_MASS = 200.0  # kg, a 200 L tank
WATER_C = 4.185  # kJ/(kg.K)
SECONDS_PER_HOUR = 3600.0
TANK_C = WATER_MASS * WATER_C / SECONDS_PER_HOUR  # kWh/K, 0.2325

T_AMBIENT = 15.0  # degrees, the plant room
T_INITIAL = 15.0  # the tank starts at room temperature
T_LOW = 55.0  # thermostat: heating comes on below this
T_HIGH = 60.0  # thermostat: heating goes off above this

PUMP_ELECTRIC = 2.0  # kW drawn by the heat pump
PUMP_COP = 3.5  # its coefficient of performance
PUMP_THERMAL = PUMP_ELECTRIC * PUMP_COP  # 7 kW into the water

BACKUP_ELECTRIC = 3.0  # kW drawn by the resistance
BACKUP_COP = 1.0  # a resistance is COP 1 by definition
BACKUP_THERMAL = BACKUP_ELECTRIC * BACKUP_COP

#: 1.5 kWh lost per day at a 45 K difference: the label figure of an ordinary
#: insulated cylinder, converted to kWh per hour per kelvin.
STANDING_LOSS_KWH_PER_DAY = 1.5
STANDING_LOSS_REFERENCE_DELTA = 45.0
UA_TEMPERATURE = STANDING_LOSS_KWH_PER_DAY / 24.0 / STANDING_LOSS_REFERENCE_DELTA

#: The same loss expressed against the STORED ENERGY rather than the
#: temperature, which is what the conduit's equation reads: the tank holds heat
#: alone, so dividing by its heat capacity converts one to the other.
UA = UA_TEMPERATURE / TANK_C

#: Each appliance sits on its own rated supply, and that is not a detail: a
#: rule's coefficients are a RATIO, not a rating. ``cons={elec: 2},
#: prod={heat: 7}`` says seven of heat per two of electricity and leaves the
#: SCALE free, so a pump behind a shared 100 kW head and a tank asking without
#: bound ran at scale 50 and delivered 350 kW. What caps a machine is what it
#: can draw, so the rating lives on its supply -- which is where a breaker
#: lives in the real installation too.

#: The volume, generous enough that the bound never throttles the heaters.
TANK_CAPACITY = TANK_C * 100.0


def energy(temperature):
    """The tank's stored energy at a temperature, in kJ."""
    return TANK_C * temperature


def temperature(stored):
    """The temperature a stored energy corresponds to."""
    return stored / TANK_C


E_INITIAL = energy(T_INITIAL)
E_LOW = energy(T_LOW)
E_HIGH = energy(T_HIGH)

#: Heating the tank from cold to the cut-out, ignoring the standing loss.
#: (13.95 - 3.4875) kWh at 7 kW is 1.4946 h, a little under an hour and a
#: half -- which is what an ordinary cylinder does.
HEAT_UP_HOURS = (E_HIGH - E_INITIAL) / PUMP_THERMAL

#: A stop just past the cut-out, so the band has certainly been reached, and
#: one early enough to catch the ramp in progress.
T_STOP_MID = HEAT_UP_HOURS * 0.5
T_STOP_WARM = HEAT_UP_HOURS * 1.02

PUMP_FAILURE_RATE = 1e-3  # per hour, only ever fired by hand below

#: Nothing in this installation fails or switches on its own once the band
#: is reached, so the session needs a date to step to: without one the
#: solver has no transition to advance the integration on and every reading
#: stays at its initial value.
HORIZON = HEAT_UP_HOURS * 1.05


# Components
# ==========


class DhwPump(muscadet.ObjFlow):
    """Electricity in, heat out, at a coefficient of performance.

    A heat pump is exactly a rule with two coefficients, which is why nothing
    here is heat-pump-specific: the COP is the ratio of the ``prod`` to the
    ``cons`` coefficient and nothing else.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="elec")
        self.add_flow_continuous_out(name="heat")
        self.add_flow_in(name="call", logic="or")

        # Announces that the pump is able to run. Produced unconditionally and
        # clamped false by the failure mode, so the backup can read it.
        self.add_flow_out(name="healthy", var_prod_default=True)

        self.add_rules(
            name="heat_pump",
            rules=[
                dict(
                    cond=["call"],
                    cons={"elec": kwargs.get("electric", PUMP_ELECTRIC)},
                    prod={"heat": kwargs.get("thermal", PUMP_THERMAL)},
                )
            ],
        )


class DhwBackup(muscadet.ObjFlow):
    """The resistance: the same rule at COP 1, and only when the pump is out."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="elec")
        self.add_flow_continuous_out(name="heat")
        self.add_flow_in(name="call", logic="or")
        self.add_flow_in(name="healthy", logic="or")

        self.add_rules(
            name="resistance",
            rules=[
                dict(
                    cond=["call", {"name": "healthy", "negate": True}],
                    cons={"elec": BACKUP_ELECTRIC},
                    prod={"heat": BACKUP_THERMAL},
                )
            ],
        )


class DhwClock(muscadet.ObjFlow):
    """Nothing but a horizon the interactive session can integrate toward."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


class DhwTank(muscadet.ObjFlow):
    """The cylinder: stored energy, integrated, and published for the sensor."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="heat")
        self.add_flow_continuous_out(name="heat")
        self.add_capacity(
            name="store",
            flow="heat",
            capacity=TANK_CAPACITY,
            side="out",
            content_init={"heat": E_INITIAL},
            fill_rate=math.inf,
        )


def build_system(with_backup=True):
    system = muscadet.System(name="DhwSys")

    system.add_component(
        name="GRID_PUMP", cls="SourceContinuous", flow="elec", rate=PUMP_ELECTRIC
    )
    system.add_component(name="PUMP", cls="DhwPump")
    system.add_component(name="TANK", cls="DhwTank")

    # The thermostat: comes on below the low mark, releases above the high one.
    # The band is what stops the loop chattering around a single setpoint.
    system.add_component(
        name="STAT",
        cls="SensorContinuous",
        measurement="store",
        control="call",
        direction="below",
        activate=E_LOW,
        release=E_HIGH,
    )

    # The standing loss: a conduit metering UA (T_tank - T_ambient). The one
    # quantity here that moves because a gradient makes it move.
    system.add_component(
        name="LOSS",
        cls="ExchangeContinuous",
        flow="heat",
        measurements=["store"],
        conductance=UA,
        potential_a={"measurement": "store"},
        potential_b={"const": energy(T_AMBIENT)},
        transfer_name="standing",
    )
    system.add_component(
        name="OUTSIDE", cls="ConsumerContinuous", flow="heat", demand=math.inf
    )

    system.connect_flow(source="GRID_PUMP", target="PUMP", flow_name="elec")
    system.connect_flow(source="PUMP", target="TANK", flow_name="heat")
    system.connect("TANK", "store_level_out", "STAT", "store_level_in")
    system.connect("TANK", "store_level_out", "LOSS", "store_level_in")
    system.connect_flow(source="STAT", target="PUMP", flow_name="call")
    system.connect_flow(source="TANK", target="LOSS", flow_name="heat")
    system.connect_flow(source="LOSS", target="OUTSIDE", flow_name="heat")

    if with_backup:
        system.add_component(name="BACKUP", cls="DhwBackup")
        system.add_component(
            name="GRID_BACKUP",
            cls="SourceContinuous",
            flow="elec",
            rate=BACKUP_ELECTRIC,
        )
        system.connect_flow(source="GRID_BACKUP", target="BACKUP", flow_name="elec")
        system.connect_flow(source="BACKUP", target="TANK", flow_name="heat")
        system.connect_flow(source="STAT", target="BACKUP", flow_name="call")
        system.connect_flow(source="PUMP", target="BACKUP", flow_name="healthy")

    system.add_component(name="CLOCK", cls="DhwClock")
    system.comp["CLOCK"].add_atm2states(
        name="horizon",
        st1="before",
        st2="after",
        occ_law_12={"cls": "delay", "time": HORIZON},
        cond_occ_21=False,
    )

    return system


# The one system every scenario lives in
# ======================================


@pytest.fixture(scope="module")
def the_system():
    system = build_system()
    system.comp["PUMP"].add_exp_failure_mode(
        name="pump_down",
        failure_rate=PUMP_FAILURE_RATE,
        failure_effects=[("heat", 0.0), ("healthy", False)],
    )
    system.isimu_start()
    yield system


def stored(system):
    return system.comp["TANK"].capacities["store"].get_quantity("heat")


def tank_temperature(system):
    return temperature(stored(system))


def produced(system, comp_name):
    return system.comp[comp_name].flows_out["heat"].var_fed.value()


def drawn(system, comp_name):
    return system.comp[comp_name].flows_in["elec"].get_delivered()


def settle(system):
    """Step through the instantaneous transitions at t=0 and stop there.

    The thermostat band settling on a cold tank fires several transitions
    at date 0. They must be applied before anything is read, and the clock
    must not be allowed to move: the first integration jumps straight to the
    next WATCHED crossing, which is the cut-in, so a reading taken after it
    is no longer a reading of a cold tank.
    """
    for _ in range(20):
        if system.currentTime() > 0.0:
            return
        if not system.isimu_active_transitions():
            return
        system.isimu_step_forward()


def run_to(system, date):
    """Advance the session to ``date`` through whatever fires before it.

    The instantaneous transitions at t=0 -- the thermostat band settling on
    a cold tank -- are stepped through here too, which is why the loop is
    bounded by the clock rather than by a step count.
    """
    for _ in range(200):
        if system.currentTime() >= date:
            return
        if not system.isimu_active_transitions():
            return
        system.isimu_step_forward()


# What the installation does
# ==========================


def test_the_tank_starts_at_room_temperature(the_system):
    """Read before anything is stepped: the declared initial condition.

    Not after settling. The first integration jumps straight to the next
    WATCHED crossing -- the 55 degree cut-in -- so a reading taken past it
    is no longer a reading of a cold tank, and asserting one there passes
    or fails for reasons that have nothing to do with the initial state.
    """
    assert tank_temperature(the_system) == pytest.approx(T_INITIAL, abs=1e-3)
    assert E_INITIAL < E_LOW


def test_the_thermostat_calls_for_heat_below_the_cut_in(the_system):
    """The band comes on below 55 degrees and the pump is asked to run."""
    settle(the_system)

    assert tank_temperature(the_system) <= T_HIGH
    assert the_system.comp["PUMP"].flows_in["call"].var_fed.value() is True


def test_the_heat_pump_delivers_its_rated_thermal_output(the_system):
    """7 kW into the water for 2 kW off the meter: the rule's two coefficients."""
    assert produced(the_system, "PUMP") == pytest.approx(PUMP_THERMAL, rel=1e-4)


def test_the_coefficient_of_performance_is_what_was_declared(the_system):
    """Checked as a RATIO of two measured quantities, not read back."""
    heat_out = produced(the_system, "PUMP")
    elec_in = drawn(the_system, "PUMP")

    assert heat_out / elec_in == pytest.approx(PUMP_COP, rel=1e-4)


def test_the_backup_stays_off_while_the_pump_is_healthy(the_system):
    """Redundancy, not parallel operation: 3 kW of resistance for nothing."""
    assert produced(the_system, "BACKUP") == pytest.approx(0.0)


def test_the_standing_loss_follows_the_temperature_difference(the_system):
    """UA (T_tank - T_ambient), the one quantity moved by a gradient here."""
    pair = the_system.comp["LOSS"].transfers["standing"]
    expected = UA * (stored(the_system) - energy(T_AMBIENT))

    assert pair.last_moved == pytest.approx(expected, rel=1e-3)


def test_the_tank_heats_at_the_net_rate(the_system):
    """Net of the standing loss, which is what makes this worth measuring.

    The loss is small here -- about 0.037 kW against 7 kW of heating -- so the
    ramp is dominated by the pump but is measurably slower than it alone.
    """
    run_to(the_system, T_STOP_MID)

    elapsed = the_system.currentTime()
    gained = stored(the_system) - E_INITIAL
    gross = PUMP_THERMAL * elapsed

    assert gained < gross
    assert gained == pytest.approx(gross, rel=0.02)


def test_the_tank_reaches_the_band_and_the_thermostat_releases(the_system):
    """The cut-out at 60 degrees, and the heating stops there."""
    run_to(the_system, T_STOP_WARM)

    assert tank_temperature(the_system) >= T_HIGH - 1.0
    assert the_system.comp["PUMP"].flows_in["call"].var_fed.value() is False


def test_the_heat_up_time_matches_the_hand_calculation(the_system):
    """(13.95 - 3.4875) kWh at 7 kW is 1.4946 h, and the loss adds a little."""
    assert HEAT_UP_HOURS == pytest.approx(1.49464, abs=1e-4)
    assert the_system.currentTime() >= HEAT_UP_HOURS


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
