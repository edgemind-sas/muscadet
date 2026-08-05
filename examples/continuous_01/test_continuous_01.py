"""The continuous example produces the behaviour its narrative claims.

Run from this directory, as the other examples' sibling tests are::

    pytest examples/continuous_01
"""

import cod3s
import pytest

#: The solver stops the integration ON a crossing rather than refining it to
#: machine precision, so a date is asserted within one default PDMP step.
CROSSING_TOL = 0.05


@pytest.fixture(scope="module")
def the_run():
    return __import__("continuous_01")


def at_or_after(trace, date):
    """The first recorded stop at or after ``date``."""
    for entry in trace:
        if entry["time"] >= date - CROSSING_TOL:
            return entry
    raise AssertionError(f"no stop at or after t={date}")


def switches(trace, key):
    return [
        entry["time"]
        for previous, entry in zip(trace, trace[1:])
        if entry[key] != previous[key]
    ]


def test_the_sensor_deadband_switches_the_pump_at_its_two_edges(the_run):
    """Draining from 40 at 6 reaches 20 at t = 10/3; refilling at 4 reaches 50
    seven and a half units later."""
    dates = switches(the_run.trace, "fill")

    assert len(dates) == 3
    assert dates[0] == pytest.approx(10.0 / 3, abs=CROSSING_TOL)
    assert dates[1] == pytest.approx(10.0 / 3 + 7.5, abs=CROSSING_TOL)


def test_the_derating_takes_the_pump_to_forty_percent(the_run):
    """One mode, one variable, and the effective rate is what it left."""
    trace = the_run.trace

    assert trace[0]["rate"] == pytest.approx(1.0)
    assert at_or_after(trace, the_run.WEAR_DATE)["rate"] == pytest.approx(
        the_run.PUMP_DERATING
    )
    assert trace[-1]["rate"] == pytest.approx(the_run.PUMP_DERATING)


def test_the_derated_pump_can_no_longer_keep_the_tank_up(the_run):
    """4 in and 6 out: the tank empties, and the line is short-served."""
    emptied = [entry for entry in the_run.trace if entry["level"] <= 0.0]

    assert emptied, "the derated refill must lose against the draw"
    assert emptied[0]["syrup"] == pytest.approx(2.0)
    assert emptied[0]["syrup"] < the_run.SYRUP_DEMAND


def test_the_discrete_guard_selects_the_idle_rule(the_run):
    """The command drops and the rule set moves to the rule producing nothing."""
    trace = the_run.trace

    assert trace[1]["mode"] == "mixing"

    tripped = at_or_after(trace, the_run.TRIP_DATE)
    assert tripped["run"] is False
    assert trace[-1]["mode"] == "idle"
    assert trace[-1]["syrup"] == pytest.approx(0.0)


def test_the_collapsed_demand_lets_the_tank_refill(the_run):
    """Nothing draws on the tank any more, so all the pump delivers is kept."""
    trace = the_run.trace

    assert trace[-1]["outflow"] == pytest.approx(0.0)
    assert trace[-1]["inflow"] == pytest.approx(
        the_run.PUMP_RATE * the_run.PUMP_DERATING
    )
    assert trace[-1]["level"] == pytest.approx(
        the_run.PUMP_RATE
        * the_run.PUMP_DERATING
        * (the_run.HORIZON - the_run.TRIP_DATE),
        abs=0.1,
    )


def test_delete(the_run):
    the_run.my_line.deleteSys()
    cod3s.terminate_session()
