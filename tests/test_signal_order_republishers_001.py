"""An ObjFlow instrument is a node of the signal graph, like a controller (R45).

What this unit pins down
------------------------
A republication is written twice in this library, and only one of the two was
ordered. A controller's VALUE output took its integer from a topological sort
of the signal graph; an ``ObjFlow`` publishing a sourced ``MeasurementOut`` --
which is what the shipped ``SensorContinuous`` compiles its ``publish`` channel
to -- took one from a band of its own, BELOW the controllers, allocated in
declaration order at declaration time.

Two consequences, and neither was visible in a passing test:

* **an instrument reading a controller was refreshed before it**, whatever the
  wiring said, so it reported the publication of the previous evaluation;
* **two instruments in a row ran in the order they were written in**, so a
  relay declared before the instrument it reads was permanently one evaluation
  behind.

In a running sequence the solver's repeated evaluations wash that lag out,
which is why no test ever caught it. At instant 0 there is no previous evaluation
to be behind, so what came out was the declared DEFAULT: an instrument
observing 80 and reporting 0 at the same instant, and doing it again at the
start of every Monte Carlo sequence.

Both equations now take their integer from ONE band and ONE sort. What that
sort answers is "this reading has settled", and that question does not depend
on the class answering it.

What this unit therefore owes, and takes from the montages below
----------------------------------------------------------------
* the two shapes, read at instant 0 before any step: instrument into
  instrument, and controller into instrument, each declared DOWNSTREAM FIRST so
  that declaration order is the wrong order;
* the derived order itself, and the band the integers come from, which is what
  carries the claim about a RUNNING sequence. That claim is deliberately not
  asserted on the stream of equation calls: a stream evaluated in the wrong
  order is the right stream shifted by one entry, and the lag is by
  construction exactly the previous entry, so no local reading of it tells the
  two apart. That is precisely why the lag went uncaught, and it is why the
  order is asserted where it is decided rather than where it is felt;
* a cycle closing through an instrument, which used to build and now cannot;
* the refusal of a sourced publication declared after the one-shot pre-run
  step, which an instrument did not have and a controller did.
"""

import cod3s
import pytest

import muscadet
from muscadet import ordering

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SensorContinuous,
    SourceContinuous,
)

#: What the tank holds when the clock starts, and therefore what an honest
#: chain reports at instant 0.
TANK_INIT = 40.0

#: What drains it, so a reading of the previous evaluation differs from a
#: reading of this one and the per-pass observation has something to see.
DRAIN_RATE = 1.0

#: The instrument's gain, and the relay's. Deliberately different, and neither
#: 1: a hop taken in the wrong order is then a number nothing else produces.
INSTRUMENT_GAIN = 2.0
RELAY_GAIN = 0.5

#: What each hop publishes at instant 0, if the hop before it published.
INSTRUMENT_READING = TANK_INIT * INSTRUMENT_GAIN
RELAY_READING = INSTRUMENT_READING * RELAY_GAIN

#: A date the interactive sessions can always stop at.
CLOCK_DATE = 2.0


def add_clock(comp, date):
    """Give the interactive session a date it can always stop at."""
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def add_draining_tank(system):
    """A volume holding :data:`TANK_INIT`, draining once the session runs."""
    system.add_component(
        name="CAP",
        cls="CapacityContinuous",
        flow="q",
        capacity=100.0,
        capacity_name="tank",
        content_init={"q": TANK_INIT},
        fill_rate=float("inf"),
    )
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="q", demand=DRAIN_RATE
    )
    system.connect_flow(source="CAP", target="SINK", flow_name="q")


def add_instrument(system, name, reads, publishes, gain):
    """A shipped sensor republishing what it reads, times ``gain``."""
    return system.add_component(
        name=name,
        cls="SensorContinuous",
        measurement=reads,
        publish=publishes,
        gain=gain,
    )


def record_publications(system, stages):
    """Wrap each node's equation, recording what every call published.

    The only observation that sees INSIDE one evaluation of the equation set.
    PyCATSHOO resolves an equation method by name at every call, so an instance
    attribute shadowing the bound method is enough, and it sees every call the
    solver makes rather than a sample of them.

    ``stages`` is ``(component, equation method, reader)``, the reader being
    what answers the number that component currently publishes: the two natures
    of node hold their publications in different collections, which is the
    whole reason this unit exists.

    Returns
    -------
    list
        Filled during the run with ``(component, published value)``, one entry
        per call, in call order.
    """
    calls = []

    def wrap(comp, method, read):
        original = getattr(comp, method)

        def wrapper():
            original()
            calls.append((comp.basename(), read()))

        setattr(comp, method, wrapper)

    for name, method, read in stages:
        wrap(system.comp[name], method, read)

    return calls


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def run_instrument_chain_scenario(obs):
    """Two shipped sensors in a row, declared DOWNSTREAM FIRST."""
    system = muscadet.System(name="SignalOrderInstrumentChain")

    add_draining_tank(system)

    # The relay BEFORE the instrument it reads, so declaration order is the
    # reverse of the order the signal travels in.
    add_instrument(system, "RELAY", "reported", "relayed", RELAY_GAIN)
    add_instrument(system, "INSTR", "tank", "reported", INSTRUMENT_GAIN)

    system.connect("CAP", "tank_level_out", "INSTR", "tank_level_in")
    system.connect("INSTR", "reported_level_out", "RELAY", "reported_level_in")

    instr = system.comp["INSTR"]
    relay = system.comp["RELAY"]

    calls = record_publications(
        system,
        (
            (
                "INSTR",
                "compute_measurements",
                lambda: instr.measurements_out["reported"].get_level(),
            ),
            (
                "RELAY",
                "compute_measurements",
                lambda: relay.measurements_out["relayed"].get_level(),
            ),
        ),
    )

    add_clock(system.comp["SINK"], CLOCK_DATE)

    system.isimu_start()

    obs["chain_at_zero"] = {
        "time": system.currentTime(),
        "observed": instr.measurements_in["tank"].get_reading(),
        "reported": instr.measurements_out["reported"].get_level(),
        "relay_observes": relay.measurements_in["reported"].get_reading(),
        "relayed": relay.measurements_out["relayed"].get_level(),
    }
    obs["chain_calls_at_zero"] = [name for name, _value in calls]

    for _ in range(40):
        if system.currentTime() >= CLOCK_DATE:
            break
        system.isimu_step_forward()

    system.isimu_stop()

    obs["chain_order"] = system.equation_order.controller_order
    obs["chain_registrations"] = list(system.equation_registrations)
    obs["chain_calls"] = calls

    # The pre-run step is one-shot, so a sourced publication declared now would
    # never be refreshed. An instrument had no refusal for that; a controller
    # did.
    obs["late_error"] = None
    try:
        instr.add_measurement_out(name="late", source="tank")
    except Exception as err:  # noqa: BLE001 -- the refusal IS the observation
        obs["late_error"] = err

    # And one declaring NO source is untouched: nothing refreshes it, so the
    # one-shot step owes it nothing.
    obs["late_unsourced_error"] = None
    try:
        instr.add_measurement_out(name="late_free")
    except Exception as err:  # noqa: BLE001
        obs["late_unsourced_error"] = err

    system.deleteSys()


def run_controller_into_instrument_scenario(obs):
    """A controller's value output read by a shipped sensor, sensor first."""
    system = muscadet.System(name="SignalOrderControllerIntoInstrument")

    add_draining_tank(system)

    # Again downstream first.
    add_instrument(system, "SENSOR", "reading", "reported", RELAY_GAIN)
    system.add_component(
        name="GAUGE",
        cls="ObjCtrl",
        controls_in=[{"name": "tank"}],
        controls_out=[
            {
                "name": "reading",
                "kind": "value",
                "emit": {
                    "op": "republish",
                    "input": "tank",
                    "gain": INSTRUMENT_GAIN,
                },
            }
        ],
    )

    system.connect("CAP", "tank_level_out", "GAUGE", "tank_level_in")
    system.connect("GAUGE", "reading_level_out", "SENSOR", "reading_level_in")

    gauge = system.comp["GAUGE"]
    sensor = system.comp["SENSOR"]

    calls = record_publications(
        system,
        (
            (
                "GAUGE",
                "compute_controls",
                lambda: gauge.controls_out["reading"].get_level(),
            ),
            (
                "SENSOR",
                "compute_measurements",
                lambda: sensor.measurements_out["reported"].get_level(),
            ),
        ),
    )

    add_clock(system.comp["SINK"], CLOCK_DATE)

    system.isimu_start()

    obs["mixed_at_zero"] = {
        "time": system.currentTime(),
        "published": gauge.controls_out["reading"].get_level(),
        "sensor_observes": sensor.measurements_in["reading"].get_reading(),
        "reported": sensor.measurements_out["reported"].get_level(),
    }

    for _ in range(40):
        if system.currentTime() >= CLOCK_DATE:
            break
        system.isimu_step_forward()

    system.isimu_stop()

    obs["mixed_order"] = system.equation_order.controller_order
    obs["mixed_calls"] = calls

    system.deleteSys()


def run_cycle_scenario(obs):
    """A controller and an instrument republishing each other.

    Two nodes, one loop, and it used to build: the instrument was not an edge
    of the signal graph, so nothing closed. The sort sees it now.
    """
    system = muscadet.System(name="SignalOrderCycle")

    system.add_component(
        name="CYC_CTRL",
        cls="ObjCtrl",
        controls_in=[{"name": "back"}],
        controls_out=[
            {
                "name": "forth",
                "kind": "value",
                "emit": {"op": "republish", "input": "back"},
            }
        ],
    )
    add_instrument(system, "CYC_INSTR", "forth", "back", 1.0)

    system.connect("CYC_CTRL", "forth_level_out", "CYC_INSTR", "forth_level_in")
    system.connect("CYC_INSTR", "back_level_out", "CYC_CTRL", "back_level_in")

    obs["cycle_error"] = None
    try:
        system.prerun()
    except Exception as err:  # noqa: BLE001 -- the refusal IS the observation
        obs["cycle_error"] = err

    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_instrument_chain_scenario(obs)
    run_controller_into_instrument_scenario(obs)
    run_cycle_scenario(obs)

    return obs


def publication_registrations(registrations):
    """The signal-band equations of one system, in registration order."""
    return [
        reg
        for reg in registrations
        if reg.method in ("compute_measurements", "compute_controls")
    ]


# ----------------------------------------------------------------------
# 1. An instrument chain settles at instant 0, however it was declared
# ----------------------------------------------------------------------


def test_an_instrument_chain_reports_the_observed_quantity_at_instant_zero(the_run):
    """Both hops, on two shipped sensors declared downstream first.

    The relay used to observe the right number and report zero at the very same
    instant, which is the shape a reader believes least and a test catches
    least: nothing is missing, one number is simply the declared default.
    """
    at_zero = the_run["chain_at_zero"]

    assert at_zero["time"] == 0.0
    assert at_zero["observed"] == pytest.approx(TANK_INIT)
    assert at_zero["reported"] == pytest.approx(INSTRUMENT_READING)
    assert at_zero["relay_observes"] == pytest.approx(INSTRUMENT_READING)
    assert at_zero["relayed"] == pytest.approx(RELAY_READING)


def test_the_two_hops_are_written_in_the_derived_order_at_instant_zero(the_run):
    """What produced the numbers above, in the order it produced them.

    The two publications standing before the first step are the SEEDS, which
    reach the variables through the very method the equation uses -- one path,
    one gain -- so this stream carries both. What it says here is that the
    instrument was written before the relay, which is the whole claim: seeded
    the other way round, the relay would carry the instrument's default.
    """
    assert the_run["chain_calls_at_zero"] == ["INSTR", "RELAY"]


def test_a_controller_read_by_an_instrument_settles_at_instant_zero(the_run):
    """The other mixed shape, and the one the shipped sensor makes ordinary."""
    at_zero = the_run["mixed_at_zero"]

    assert at_zero["time"] == 0.0
    assert at_zero["published"] == pytest.approx(INSTRUMENT_READING)
    assert at_zero["sensor_observes"] == pytest.approx(INSTRUMENT_READING)
    assert at_zero["reported"] == pytest.approx(RELAY_READING)


# ----------------------------------------------------------------------
# 2. The order itself, and the band it is allocated from
# ----------------------------------------------------------------------


def test_an_instrument_is_a_node_of_the_signal_order(the_run):
    """Derived from the wiring: both were declared the other way round."""
    assert the_run["chain_order"] == ["INSTR", "RELAY"]
    assert the_run["mixed_order"] == ["GAUGE", "SENSOR"]


def test_both_equations_take_their_integer_from_the_signal_band(the_run):
    """One band, so a publication cannot be allocated below a controller."""
    registered = publication_registrations(the_run["chain_registrations"])

    assert [reg.comp for reg in registered] == ["INSTR", "RELAY"]
    assert all(reg.order >= ordering.CONTROL_ORDER_BASE for reg in registered)
    assert [reg.order for reg in registered] == sorted(
        reg.order for reg in registered
    ), "the integers must increase along the derived order"


def test_a_signal_node_is_either_nature(the_run):
    """The predicate the walk is built on, asked of both classes.

    Read off the live montage rather than off the constants: what the walk
    needs is that ``is_signal_node`` answer yes to a controller AND to an
    instrument, and that a component publishing nothing computed stay out of
    the order it would otherwise constrain for nothing.
    """
    system = the_run["system"]

    assert ordering.is_signal_node(system.comp["CYC_CTRL"]), "a controller is one"
    assert ordering.is_signal_node(system.comp["CYC_INSTR"]), "an instrument too"

    assert ordering.computed_publications(system.comp["CYC_CTRL"]) == ["forth"]
    assert ordering.computed_publications(system.comp["CYC_INSTR"]) == ["back"]


# ----------------------------------------------------------------------
# 3. What the wider graph now refuses, and what it still accepts
# ----------------------------------------------------------------------


def test_a_cycle_closing_through_an_instrument_is_refused(the_run):
    """It used to build, the instrument being invisible to the sort.

    A loop of publications has no evaluation order at all, so it must be
    refused rather than run in whichever order the declarations fell in.
    """
    error = the_run["cycle_error"]

    assert error is not None, "a cycle through an instrument must be refused"
    assert isinstance(error, muscadet.ControllerSignalCycleError)

    message = str(error)
    assert "CYC_CTRL" in message
    assert "CYC_INSTR" in message


def test_a_sourced_publication_declared_after_the_pre_run_step_is_refused(the_run):
    """The refusal a controller had and an instrument did not.

    Its equation is now registered at that one-shot step, so a late instrument
    would report its declared default for the whole run with nothing raised.
    """
    error = the_run["late_error"]

    assert error is not None, "a late sourced publication must be refused"
    assert isinstance(error, ValueError)

    message = str(error)
    assert "'late'" in message
    assert "pre-run" in message


def test_an_unsourced_publication_declared_late_is_still_accepted(the_run):
    """Nothing refreshes it, so the one-shot step owes it nothing.

    The refusal above is about an equation that will never be registered, not
    about the moment of declaration: a plain writable variable a model or a
    failure mode drives keeps arriving whenever it likes, exactly as it did
    before the signal band existed.
    """
    assert the_run["late_unsourced_error"] is None


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
