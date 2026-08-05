"""Measurement link: a capacity publishes its level, read-only, to an observer.

A capacity exposes its level as a read-only export; another component imports it
to observe the level (R33). The link carries no quantity, takes part in no
allocation, and cannot be written by the importing component -- the imported
endpoints are PyCATSHOO references, which have no setter at all.

The unobserved twin ``TREF`` is what makes "carries no quantity" observable: it
is built exactly like the observed tank, wired to nobody, and must integrate to
the very same level.
"""

import muscadet
import cod3s
import pytest

TANK_VOLUME = 100.0
TANK_INIT = 20.0
TANK_INFLOW = 2.0
HORIZON = 10.0

LEVEL_DEFAULT = -1.0
FILL_DEFAULT = -0.5


class Tank(muscadet.ObjFlow):
    """Holds a capacity and publishes its level."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="w")
        self.add_capacity(
            name="cuve",
            flow="w",
            capacity=TANK_VOLUME,
            content_init={"w": TANK_INIT},
        )


class Sensor(muscadet.ObjFlow):
    """Observes a capacity. No flow at all: it moves nothing."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="cuve")


class LoneSensor(Sensor):
    """The same observer, wired to nobody, with explicit read defaults."""

    def add_flows(self, **kwargs):
        muscadet.ObjFlow.add_flows(self, **kwargs)
        self.add_measurement_in(
            name="cuve", level_default=LEVEL_DEFAULT, fill_default=FILL_DEFAULT
        )


@pytest.fixture(scope="module")
def the_system():
    """Wire one observed tank, one unobserved twin, and drive one session."""

    system = muscadet.System(name="MeasurementSys")

    system.add_component(name="TOBS", cls="Tank")
    system.add_component(name="TREF", cls="Tank")
    system.add_component(name="SENSOR", cls="Sensor")
    system.add_component(name="LONE", cls="LoneSensor")

    # The measurement link: a read-only export wired to its matching import.
    system.connect("TOBS", "cuve_level_out", "SENSOR", "cuve_level_in")

    system.comp["SENSOR"].add_atm2states(
        name="horizon",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": HORIZON},
        cond_occ_21=False,
    )

    observed = system.comp["TOBS"].capacities["cuve"]
    reference = system.comp["TREF"].capacities["cuve"]
    measurement = system.comp["SENSOR"].measurements_in["cuve"]
    lone = system.comp["LONE"].measurements_in["cuve"]

    obs = {
        "system": system,
        "observed": observed,
        "reference": reference,
        "measurement": measurement,
        "lone": lone,
    }

    system.isimu_start()

    observed.set_inflow("w", TANK_INFLOW)
    reference.set_inflow("w", TANK_INFLOW)

    def snapshot():
        return {
            "time": system.currentTime(),
            "observed_level": observed.get_quantity(),
            "observed_fill": observed.get_fill(),
            "observed_inflow": observed.get_inflow("w"),
            "observed_outflow": observed.get_outflow("w"),
            "reference_level": reference.get_quantity(),
            "read_level": measurement.get_level(),
            "read_fill": measurement.get_fill(),
            "lone_level": lone.get_level(),
            "lone_fill": lone.get_fill(),
        }

    obs["t0"] = snapshot()

    system.isimu_step_forward()

    obs["tend"] = snapshot()

    system.isimu_stop()

    return obs


def test_the_observer_reads_the_level(the_system):
    """The imported reading tracks the observed capacity's level and fill."""
    t0 = the_system["t0"]
    tend = the_system["tend"]

    assert t0["observed_level"] == pytest.approx(TANK_INIT)
    assert t0["read_level"] == pytest.approx(TANK_INIT)
    assert t0["read_fill"] == pytest.approx(TANK_INIT / TANK_VOLUME)

    # ... and it keeps tracking it once the level has moved
    assert tend["time"] == pytest.approx(HORIZON)
    assert tend["observed_level"] == pytest.approx(
        TANK_INIT + TANK_INFLOW * HORIZON, rel=1e-6
    )
    assert tend["read_level"] == pytest.approx(tend["observed_level"], rel=1e-6)
    assert tend["read_fill"] == pytest.approx(tend["observed_fill"], rel=1e-6)


def test_the_link_moves_no_quantity(the_system):
    """The observed tank and its unobserved twin hold exactly the same level."""
    t0 = the_system["t0"]
    tend = the_system["tend"]

    assert t0["observed_level"] == pytest.approx(t0["reference_level"])
    assert tend["observed_level"] == pytest.approx(tend["reference_level"], rel=1e-12)

    # Observing drew nothing out of the capacity and pushed nothing in
    assert t0["observed_inflow"] == pytest.approx(TANK_INFLOW)
    assert t0["observed_outflow"] == pytest.approx(0.0)
    assert tend["observed_inflow"] == pytest.approx(TANK_INFLOW)
    assert tend["observed_outflow"] == pytest.approx(0.0)


def test_the_observer_takes_part_in_no_allocation(the_system):
    """The observing component carries no flow: it is outside the sweeps."""
    sensor = the_system["system"].comp["SENSOR"]

    assert sensor.flows_in == {}
    assert sensor.flows_out == {}
    assert sensor.capacities == {}
    assert set(sensor.measurements_in) == {"cuve"}


def test_the_observer_cannot_write_the_level(the_system):
    """Read-only by construction: a reference has no setter."""
    measurement = the_system["measurement"]
    sensor = the_system["system"].comp["SENSOR"]

    assert not hasattr(measurement.var_level, "setValue")
    assert not hasattr(measurement.var_fill, "setValue")

    with pytest.raises(AttributeError):
        measurement.var_level.setValue(0.0)

    # The observing component owns no variable behind the link either: there is
    # simply nothing on its side that a write could reach.
    sensor_vars = {var.basename() for var in sensor.variables()}
    assert "cuve_level_in" not in sensor_vars
    assert "cuve_fill_in" not in sensor_vars
    assert "cuve_qty" not in sensor_vars

    # ... while the holder owns the exported variables
    holder_vars = {
        var.basename() for var in the_system["system"].comp["TOBS"].variables()
    }
    assert {"cuve_qty", "cuve_fill"} <= holder_vars


def test_link_naming_and_wiring(the_system):
    """The message boxes are the pair a plain ``connect`` wires together."""
    system = the_system["system"]
    measurement = the_system["measurement"]

    assert system.comp["TOBS"].messageBox("cuve_level_out") is not None
    assert system.comp["SENSOR"].messageBox("cuve_level_in") is not None

    assert measurement.var_level.basename() == "cuve_level_in"
    assert measurement.var_fill.basename() == "cuve_fill_in"
    assert measurement.var_level.nbCnx() == 1
    assert measurement.is_connected is True


def test_an_unconnected_measurement_reads_its_defaults(the_system):
    """No link, no reading: the declared defaults, not an error."""
    t0 = the_system["t0"]
    lone = the_system["lone"]

    assert lone.var_level.nbCnx() == 0
    assert lone.is_connected is False
    assert t0["lone_level"] == pytest.approx(LEVEL_DEFAULT)
    assert t0["lone_fill"] == pytest.approx(FILL_DEFAULT)


def test_duplicate_measurement_link_raises(the_system):
    """One channel name, one import."""
    sensor = the_system["system"].comp["SENSOR"]

    with pytest.raises(ValueError, match="Measurement link cuve already exists"):
        sensor.add_measurement_in(name="cuve")


def test_delete(the_system):
    the_system["system"].deleteSys()
    cod3s.terminate_session()
