"""The heated tank benchmark, ported to MUSCADET 2.0 -- and where it stops.

The formulation
---------------
The heated tank is the classical dynamic-reliability benchmark: a tank whose
level is regulated between two thresholds by two pumps and a valve, heated by a
constant thermal power, with dry-out, overflow and overheating as the feared
events. The formulation used here is taken from the PyCATSHOO distribution's own
treatment of it (``Samples/HeatedTank/S8d``, EDF, H. Chraibi), whose parameter
file gives every constant below:

=========================  ========================================
``area = 1``               tank section
``level`` init ``7``       (this port starts at 5, see ``LEVEL_INIT``)
``dryOutLevel = 4``        feared event
``overFlowLevel = 10``     feared event
``levelMin = 6``           pumps open below it, valve closes below it
``levelMax = 8``           pumps close above it, valve opens above it
``nominalFlow = 1.5``      per pump and per valve
``lambda0``                0.0022831 / 0.0028571 / 0.0015625 (P1/P2/V3)
``hotTemperature = 100``   the third feared event -- NOT modelled, see below
``power = 23.88915``       heater -- NOT modelled, see below
=========================  ========================================

The published origins of the benchmark, both obtained through OpenAlex during
this session rather than from memory:

* T. Aldemir, "Computer-Assisted Markov Failure Modeling of Process Control
  Systems", *IEEE Transactions on Reliability* (1987),
  DOI ``10.1109/tr.1987.5222318``;
* M. Marseguerra and E. Zio, "Monte Carlo approach to PSA for dynamic process
  systems", *Reliability Engineering & System Safety* (1996),
  DOI ``10.1016/0951-8320(95)00131-x``.

The level ODE ``dL/dt = (inflow - outflow) / area`` and the control law are
ported exactly. The temperature ODE
``dT/dt = (sum_i q_i (T_i - T) + power) / (L * area)`` is **not**, and neither
is the temperature-dependent failure rate
``lambda(T) = lambda0 (b1 e^(bc (T-20)) + b2 e^(-bd (20-T))) / (b1 + b2)``.
The last four tests in this module are the measurement of why: they are boundary
tests, and each one asserts what MUSCADET 2.0 does instead of what the benchmark
needs.

One of those boundaries has since moved, and the test that marks it says so. A
capacity used to publish only the total raw quantity over its constituents, so
on a water-plus-enthalpy volume every threshold was watching 7 + 217 = 224. A
measurement channel now names the constituents it reads, so ``H/V`` -- the
mixture temperature -- is observable. What is still missing is the other half:
a watched threshold is built from the ``{name, op, value}`` operand vocabulary,
which names a channel and not a constituent of one, so the reading is available
to Python and unavailable to a guard.

The benchmark's Monte-Carlo estimate is out of reach here and is deliberately
not attempted as a test: measured on this model, one 200 h sequence costs about
37 s, so the reference's 1000 sequences over 1000 h would run for days. The
regulation cycles every 2 h and each cycle costs several watched crossings and
the instantaneous transitions they trigger. What this module asserts instead is
the deterministic sequence behind each feared event.

What is ported
--------------
``P1``, ``P2`` -water-> ``TANK`` -water-> ``V3``, with the level published over
a measurement channel to four sensors: two regulating (``LOW`` drives the
pumps, ``HIGH`` drives the valve) and two detecting the feared events (``DRY``,
``OVF``). The regulation is the benchmark's, deadband included: the pumps run
below 6 and stop above 8, the valve opens above 8 and shuts below 6, so the
level cycles between the two thresholds -- 2/3.0 h up, 2/1.5 h down, a period of
exactly 2 h.

Each of the three flow components carries the benchmark's two failure modes.
**Stuck closed** is a derating of the continuous output to 0, which is the
library's own idiom. **Stuck on** has no idiom: a derating can only subtract, so
production is forced by a discrete output of the component's own, produced
unconditionally with its availability initially false, which the mode clamps
true and which a third rule guard reads through ``port="out"``.
"""

import math

import pytest

import cod3s
import muscadet
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SensorContinuous,
)

# Benchmark data (PyCATSHOO S8dHTBase.xml)
# ========================================

NOMINAL = 1.5  # nominalFlow, per pump and per valve
LEVEL_MIN = 6.0  # levelMin: pumps open below, valve shuts below
LEVEL_MAX = 8.0  # levelMax: pumps close above, valve opens above
DRYOUT = 4.0  # dryOutLevel
OVERFLOW = 10.0  # overFlowLevel

#: The tank's own volume, ABOVE the overflow threshold on purpose. A capacity
#: bound is a hard throttle -- a full capacity cuts the demand it publishes
#: upstream -- while the benchmark's overflow is an EVENT observed on the level,
#: after which the run is over. Putting the volume at 10 would make the feared
#: event and the model's own wall the same thing and stop the excursion the test
#: is there to observe.
TANK_VOLUME = 12.0

#: Starts below ``LEVEL_MIN`` rather than at the benchmark's 7. A sensor's
#: deadband automaton is built released (``SensorContinuous`` hard-codes
#: ``init_st2=False``), so at 7 -- inside the band -- every control port would
#: start off and the level would never move. 5 puts the plant into the
#: benchmark's cycle on the first crossing instead.
LEVEL_INIT = 5.0

#: lambda0 per component, per hour.
LAMBDA_0 = {"P1": 0.0022831, "P2": 0.0028571, "V3": 0.0015625}

FAILURE_DATE = 2.0  # when the deterministic scenarios break something
HORIZON = 20.0
NEVER = 1e6

#: The solver stops ON a threshold rather than refining it to machine
#: precision, so a crossing reads a few 1e-4 past its nominal value.
TOL = 0.05


# Components
# ==========


class HTPump(muscadet.ObjFlow):
    """A pump: a rate gated by a control port, with both benchmark failures.

    Three rules rather than two. ``stuck_on`` is a discrete output of the pump's
    own, produced unconditionally but initially unavailable, so nothing feeds it
    until a failure mode clamps its availability true; the ``forced`` rule reads
    it back through ``port="out"``. That indirection is what a "stuck on" needs
    and a derating cannot give: an effect on a continuous output multiplies
    production by a factor in [0, 1] and can only ever subtract.

    The three guards are mutually exclusive by construction, which a rule set
    requires -- two guards holding at once is refused at evaluation.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(name="run", logic="and")
        self.add_flow_out(
            name="stuck_on",
            var_prod_default=True,
            var_fed_available_out_init=False,
        )
        self.add_flow_continuous_out(name="water")

        self.add_rules(
            name="duty",
            rules=[
                dict(
                    name="forced",
                    cond=[{"name": "stuck_on", "port": "out"}],
                    prod={"water": NOMINAL},
                ),
                dict(
                    name="running",
                    cond=[
                        {"name": "run", "port": "in"},
                        {"name": "stuck_on", "port": "out", "negate": True},
                    ],
                    prod={"water": NOMINAL},
                ),
                dict(
                    name="idle",
                    cond=[
                        {"name": "run", "port": "in", "negate": True},
                        {"name": "stuck_on", "port": "out", "negate": True},
                    ],
                    prod={"water": 0.0},
                ),
            ],
        )


class HTValve(muscadet.ObjFlow):
    """The drain valve: the pump's mirror, consuming instead of producing.

    ``drain`` is deliberately wired to nothing. An unconnected continuous output
    constrains no demand, so the rule runs at its NOMINAL scale and the valve
    claims exactly the ``cons`` coefficient it declares -- 1.5, the benchmark's
    ``nominalFlow`` -- rather than whatever the tank could supply.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(name="open", logic="and")
        self.add_flow_out(
            name="stuck_on",
            var_prod_default=True,
            var_fed_available_out_init=False,
        )
        self.add_flow_continuous_in(name="water")
        self.add_flow_continuous_out(name="drain")

        self.add_rules(
            name="duty",
            rules=[
                dict(
                    name="forced",
                    cond=[{"name": "stuck_on", "port": "out"}],
                    cons={"water": NOMINAL},
                    prod={"drain": NOMINAL},
                ),
                dict(
                    name="draining",
                    cond=[
                        {"name": "open", "port": "in"},
                        {"name": "stuck_on", "port": "out", "negate": True},
                    ],
                    cons={"water": NOMINAL},
                    prod={"drain": NOMINAL},
                ),
                dict(
                    name="shut",
                    cond=[
                        {"name": "open", "port": "in", "negate": True},
                        {"name": "stuck_on", "port": "out", "negate": True},
                    ],
                    cons={"water": 0.0},
                    prod={"drain": 0.0},
                ),
            ],
        )


class HTClock(muscadet.ObjFlow):
    """Nothing but a date the interactive session can always step to."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


class HTConstituentProbe(muscadet.ObjFlow):
    """Observes named constituents of a volume rather than its total.

    Hand-written on purpose: the primitive
    ``add_measurement_in(flows=[...])`` reads a constituent, but the shipped
    ``SensorContinuous`` does not carry the key, so this is what the KB cannot
    yet express. See
    ``test_a_constituent_is_observable_but_no_shipped_sensor_thresholds_it``.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(
            name=kwargs.get("measurement", "tank"),
            flows=list(kwargs.get("flows", [])),
        )

    def reading(self, flow=None):
        return self.measurements_in["tank"].get_level(flow)


# The plant
# =========


def build_plant(name):
    """The benchmark's five components, its regulation and its two detectors."""
    system = muscadet.System(name=name)

    system.add_component(name="P1", cls="HTPump")
    system.add_component(name="P2", cls="HTPump")
    system.add_component(name="V3", cls="HTValve")
    system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        flow="water",
        capacity=TANK_VOLUME,
        capacity_name="tank",
        fill_rate=math.inf,
        content_init={"water": LEVEL_INIT},
    )

    # The regulation, deadband and all. The two sensors read the same channel.
    system.add_component(
        name="LOW",
        cls="SensorContinuous",
        measurement="tank",
        control="run",
        direction="below",
        activate=LEVEL_MIN,
        release=LEVEL_MAX,
    )
    system.add_component(
        name="HIGH",
        cls="SensorContinuous",
        measurement="tank",
        control="open",
        direction="above",
        activate=LEVEL_MAX,
        release=LEVEL_MIN,
    )

    # The two feared events, as single-threshold detectors.
    system.add_component(
        name="DRY",
        cls="SensorContinuous",
        measurement="tank",
        control="dryout",
        direction="below",
        activate=DRYOUT,
    )
    system.add_component(
        name="OVF",
        cls="SensorContinuous",
        measurement="tank",
        control="overflow",
        direction="above",
        activate=OVERFLOW,
    )
    system.add_component(name="CLOCK", cls="HTClock")

    system.connect_flow(source="P1", target="TANK", flow_name="water")
    system.connect_flow(source="P2", target="TANK", flow_name="water")
    system.connect_flow(source="TANK", target="V3", flow_name="water")

    for sensor in ("LOW", "HIGH", "DRY", "OVF"):
        system.connect("TANK", "tank_level_out", sensor, "tank_level_in")

    system.connect_flow(source="LOW", target="P1", flow_name="run")
    system.connect_flow(source="LOW", target="P2", flow_name="run")
    system.connect_flow(source="HIGH", target="V3", flow_name="open")

    system.comp["CLOCK"].add_atm2states(
        name="horizon",
        st1="before",
        st2="after",
        occ_law_12={"cls": "delay", "time": HORIZON},
        cond_occ_21=False,
    )

    return system


def stick_closed(comp, flow, date=FAILURE_DATE):
    """Stuck closed: the library's own idiom, a derating of the output to 0."""
    comp.add_delay_failure_mode(
        name="stuck_closed",
        failure_time=date,
        failure_effects=[(flow, 0.0)],
        repair_time=NEVER,
    )


def stick_open(comp, date=FAILURE_DATE):
    """Stuck on: clamp the availability of the component's own forcing port."""
    comp.add_delay_failure_mode(
        name="stuck_open",
        failure_time=date,
        failure_effects=[("stuck_on", True)],
        repair_time=NEVER,
    )


def walk(system, horizon=HORIZON, limit=250):
    """Step to ``horizon``, keeping one row per observable change."""

    def snapshot():
        tank = system.comp["TANK"].capacities["tank"]

        return dict(
            time=system.currentTime(),
            level=tank.get_quantity("water"),
            inflow=tank.get_inflow("water"),
            outflow=tank.get_outflow("water"),
            run=system.comp["P1"].flows_in["run"].var_fed.value(),
            open=system.comp["V3"].flows_in["open"].var_fed.value(),
            dryout=system.comp["DRY"].flows_out["dryout"].var_fed.value(),
            overflow=system.comp["OVF"].flows_out["overflow"].var_fed.value(),
        )

    trace = [snapshot()]
    for _ in range(limit):
        if system.currentTime() >= horizon:
            break
        system.isimu_step_forward()
        entry = snapshot()
        if entry != trace[-1]:
            trace.append(entry)

    return trace


def first_at(trace, key):
    """The first row at which ``key`` is true, or None."""
    return next((row for row in trace if row[key]), None)


# The scenarios
# =============


def run_nominal(obs):
    """Every component healthy: the level must stay inside the deadband."""
    system = build_plant("HeatedTankNominal")

    system.isimu_start()
    obs["nominal"] = walk(system)
    system.isimu_stop()
    system.deleteSys()


def run_overflow(obs):
    """Both pumps stuck on: 3.0 in against 1.5 out, so the level runs away."""
    system = build_plant("HeatedTankOverflow")

    stick_open(system.comp["P1"])
    stick_open(system.comp["P2"])

    system.isimu_start()
    obs["overflow"] = walk(system)
    system.isimu_stop()
    system.deleteSys()


def run_dryout(obs):
    """Both pumps stuck closed and the valve stuck open: the tank drains."""
    system = build_plant("HeatedTankDryout")

    stick_closed(system.comp["P1"], "water")
    stick_closed(system.comp["P2"], "water")
    stick_open(system.comp["V3"])

    system.isimu_start()
    obs["dryout"] = walk(system)
    system.isimu_stop()
    system.deleteSys()


def run_enthalpy_reading(obs):
    """What a sensor observing a two-constituent volume actually reads."""
    system = muscadet.System(name="HeatedTankEnthalpyReading")

    system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        flows=[
            {"name": "water", "weight": 1.0},
            # Enthalpy has to be given a volume weight, since a weight must be
            # strictly positive -- so the constituent that occupies no volume is
            # declared as occupying a negligible one.
            {"name": "heat", "weight": 1e-9},
        ],
        ports="out",
        capacity=TANK_VOLUME,
        capacity_name="tank",
        content_init={"water": 7.0, "heat": 217.0},
    )
    system.add_component(
        name="OBS",
        cls="SensorContinuous",
        measurement="tank",
        control="high",
        direction="above",
        activate=OVERFLOW,
    )
    system.connect("TANK", "tank_level_out", "OBS", "tank_level_in")

    # The same volume, observed per constituent. One box, more aliases: the
    # total-only observer above is untouched by this one existing.
    system.add_component(
        name="CPROBE",
        cls="HTConstituentProbe",
        measurement="tank",
        flows=["water", "heat"],
    )
    system.connect("TANK", "tank_level_out", "CPROBE", "tank_level_in")

    # What the KB still cannot say: a shipped sensor thresholds a CHANNEL, and
    # the operand vocabulary it builds -- {name, op, value} -- names no
    # constituent. Taken here rather than asserted from memory.
    try:
        system.add_component(
            name="KBPROBE",
            cls="SensorContinuous",
            measurement="tank",
            flows=["water"],
            control="low",
            direction="below",
            activate=LEVEL_MIN,
        )
        obs["kb_sensor_constituent"] = None
    except ValueError as error:
        obs["kb_sensor_constituent"] = str(error)

    system.add_component(name="CLOCK", cls="HTClock")
    system.comp["CLOCK"].add_atm2states(
        name="horizon",
        st1="before",
        st2="after",
        occ_law_12={"cls": "delay", "time": 1.0},
        cond_occ_21=False,
    )

    system.isimu_start()
    # The threshold and its band automaton are instantaneous transitions at
    # t = 0: they need a step to be applied.
    for _ in range(3):
        system.isimu_step_forward()

    capacity = system.comp["TANK"].capacities["tank"]
    # A channel that names no constituent still reads the whole volume ...
    obs["enthalpy_reading"] = system.comp["OBS"].measurements_in["tank"].get_level()
    obs["enthalpy_water_level"] = capacity.get_quantity("water")
    obs["enthalpy_heat_level"] = capacity.get_quantity("heat")
    # ... and the detector at the overflow threshold is already tripped by it.
    obs["enthalpy_detector"] = system.comp["OBS"].flows_out["high"].var_fed.value()

    # ... while a channel that names them reads each one, and their ratio is
    # the mixture temperature the energy balance needs.
    probe = system.comp["CPROBE"]
    obs["enthalpy_water_reading"] = probe.reading("water")
    obs["enthalpy_heat_reading"] = probe.reading("heat")
    obs["enthalpy_total_reading"] = probe.reading()
    obs["enthalpy_temperature"] = probe.reading("heat") / probe.reading("water")

    system.isimu_stop()
    system.deleteSys()


def run_enthalpy_request(obs, water, heat, key):
    """One (water, heat) request put to a two-constituent reservoir.

    The physically honest route to a temperature: enthalpy is extensive, so it
    adds where a temperature would have to mix, and ``T = H / V`` is read back
    as a ratio of two integrated levels. A capacity does hold several
    constituents, and ``split_draw`` composes a withdrawal at their raw-quantity
    share -- which is exactly ``H/V``, the right ratio.

    What is measured is the magnitude. A tank at ``V = 7``, ``H = 217`` stands
    at ``T = 31``, and the valve must remove ``(1.5, 1.5 x 31)``. Three requests
    are put to it: the physically exact pair, an arbitrary pair, and an
    unbounded pair.
    """
    system = muscadet.System(name=f"HeatedTankEnthalpy_{key}")

    system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        flows=[
            {"name": "water", "weight": 1.0},
            {"name": "heat", "weight": 1e-9},
        ],
        ports="out",
        capacity=TANK_VOLUME,
        capacity_name="tank",
        content_init={"water": 7.0, "heat": 217.0},
    )
    system.add_component(
        name="SINK",
        cls="ConsumerContinuous",
        flows=[
            {"name": "water", "demand": water},
            {"name": "heat", "demand": heat},
        ],
    )
    system.connect_flow(source="TANK", target="SINK", flow_name="water")
    system.connect_flow(source="TANK", target="SINK", flow_name="heat")

    system.add_component(name="CLOCK", cls="HTClock")
    system.comp["CLOCK"].add_atm2states(
        name="horizon",
        st1="before",
        st2="after",
        occ_law_12={"cls": "delay", "time": 1.0},
        cond_occ_21=False,
    )

    system.isimu_start()
    system.isimu_step_forward()
    obs[key] = (
        system.comp["SINK"].flows_in["water"].get_delivered(),
        system.comp["SINK"].flows_in["heat"].get_delivered(),
    )
    system.isimu_stop()

    return system


def run_failure_rate_wiring(obs):
    """Is the exponential rate a constant, or something a model can drive?"""
    system = build_plant("HeatedTankRate")

    pump = system.comp["P1"]
    pump.add_exp_failure_mode(
        name="stuck_closed",
        failure_rate=LAMBDA_0["P1"],
        failure_effects=[("water", 0.0)],
        repair_rate=0.0,
    )

    automaton = pump.automata_d[f"{pump.name()}_stuck_closed"]
    transition = automaton.get_transition_by_name("stuck_closed_rep_occ")

    obs["rate_variable"] = pump.params["stuck_closed_lambda"]
    obs["rate_initial"] = pump.params["stuck_closed_lambda"].value()
    # The law's parameter: the VARIABLE object itself, not a copy of its value.
    obs["rate_is_variable"] = (
        transition.occ_law.rate is pump.params["stuck_closed_lambda"]
    )
    obs["transition_fields"] = set(type(transition).model_fields)

    # Kept alive for the teardown test, per the module convention.
    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_nominal(obs)
    run_overflow(obs)
    run_dryout(obs)
    run_enthalpy_reading(obs)
    run_enthalpy_request(obs, NOMINAL, NOMINAL * 31.0, "enthalpy_exact").deleteSys()
    run_enthalpy_request(obs, NOMINAL, NOMINAL, "request_equal").deleteSys()
    run_enthalpy_request(obs, math.inf, math.inf, "request_unbounded").deleteSys()
    run_failure_rate_wiring(obs)

    return obs


# The regulation
# ==============


def test_the_regulation_holds_the_level_between_its_thresholds(the_run):
    """Every component healthy: the level never leaves ``[6, 8]`` once in band.

    The first rise from ``LEVEL_INIT`` is the only excursion below 6, and it is
    the port's own initial condition rather than the benchmark's.
    """
    trace = the_run["nominal"]

    in_band = [row for row in trace if row["time"] > 1.0]
    assert in_band, "the plant never reached its operating band"

    assert min(row["level"] for row in in_band) >= LEVEL_MIN - TOL
    assert max(row["level"] for row in in_band) <= LEVEL_MAX + TOL


def test_neither_feared_event_fires_while_the_plant_is_healthy(the_run):
    trace = the_run["nominal"]

    assert first_at(trace, "dryout") is None
    assert first_at(trace, "overflow") is None


def test_the_regulation_cycle_is_the_benchmark_s_two_hours(the_run):
    """2 units up at 3.0, 2 units down at 1.5: a period of exactly 2 h.

    Below 6 both pumps run and the valve is shut, so the level rises at 3.0;
    above 8 both pumps stop and the valve drains, so it falls at 1.5. That is
    the reference implementation's cycle, and it is a property of the deadband
    rather than of the integrator.
    """
    trace = the_run["nominal"]

    # Dates at which the pumps came back on: one per cycle.
    starts = [
        row["time"]
        for previous, row in zip(trace, trace[1:])
        if row["run"] and not previous["run"]
    ]
    assert len(starts) >= 3, f"expected several cycles, got {starts}"

    periods = [b - a for a, b in zip(starts[1:], starts[2:])]
    assert periods, "not enough cycles to measure a period"
    for period in periods:
        assert period == pytest.approx(2.0, abs=0.01)


def test_the_pumps_and_the_valve_never_run_together_in_band(the_run):
    """The deadband is what keeps the two sensors from fighting.

    Between the thresholds each control port holds whatever the last crossing
    left it, so the plant is either filling at 3.0 or draining at 1.5 and never
    doing both -- the chattering the deadband exists to remove.
    """
    trace = the_run["nominal"]

    for row in trace:
        if row["time"] <= 1.0:
            continue
        assert not (row["inflow"] > 0.0 and row["outflow"] > 0.0)


# The failure-driven excursions -- the benchmark's actual subject
# ==============================================================


def test_both_pumps_stuck_on_drive_the_level_to_overflow(the_run):
    """3.0 in against the valve's 1.5 out: +1.5, and the level runs away."""
    trace = the_run["overflow"]

    event = first_at(trace, "overflow")
    assert event is not None, "the level never reached the overflow threshold"
    assert event["level"] == pytest.approx(OVERFLOW, abs=TOL)

    # It is the failure that did it: nothing before FAILURE_DATE went near 10.
    assert event["time"] > FAILURE_DATE
    assert (
        max(row["level"] for row in trace if row["time"] <= FAILURE_DATE)
        < LEVEL_MAX + TOL
    )

    # The forcing port really is what carries the failure: the pumps deliver
    # their nominal with the control port off.
    assert event["inflow"] == pytest.approx(2 * NOMINAL, abs=TOL)
    assert event["run"] is False


def test_overflow_arrives_when_the_ramp_says_it_should(the_run):
    """The level rises at exactly 1.5 from the failure on: a checkable date."""
    trace = the_run["overflow"]

    at_failure = [row for row in trace if row["time"] <= FAILURE_DATE][-1]
    event = first_at(trace, "overflow")

    expected = FAILURE_DATE + (OVERFLOW - at_failure["level"]) / NOMINAL
    assert event["time"] == pytest.approx(expected, abs=0.05)


def test_dead_pumps_and_a_stuck_valve_drive_the_level_to_dryout(the_run):
    """Nothing in, 1.5 out, and the regulation cannot answer."""
    trace = the_run["dryout"]

    event = first_at(trace, "dryout")
    assert event is not None, "the level never reached the dry-out threshold"
    assert event["level"] == pytest.approx(DRYOUT, abs=TOL)
    assert event["time"] > FAILURE_DATE

    # The control loop DID call for water -- the pumps simply cannot deliver.
    assert event["run"] is True
    assert event["inflow"] == pytest.approx(0.0, abs=TOL)
    assert event["outflow"] == pytest.approx(NOMINAL, abs=TOL)


def test_the_tank_empties_and_then_serves_only_what_transits(the_run):
    """Past dry-out the level goes to 0 and the valve is served nothing."""
    trace = the_run["dryout"]

    assert trace[-1]["level"] == pytest.approx(0.0, abs=TOL)
    assert trace[-1]["outflow"] == pytest.approx(0.0, abs=TOL)


# The boundary: what MUSCADET 2.0 cannot express
# ==============================================


def test_one_volume_serves_two_constituents_only_at_a_ratio_it_is_told(the_run):
    """Enthalpy co-transport: the RATIO is right, the magnitude cannot be.

    ``split_draw`` composes a withdrawal at the constituents' raw-quantity
    share, which is ``H/V`` -- the mixture temperature, exactly what an energy
    balance needs. But a consumer states its demand per flow, as a CONSTANT: a
    ``var_demand_default`` on a continuous input, or a ``cons`` coefficient of a
    rule. So the request ``(1.5, 1.5 x 31)`` below is served exactly -- and is
    right only for as long as the tank stands at T = 31.
    """
    assert the_run["enthalpy_exact"][0] == pytest.approx(NOMINAL, abs=1e-3)
    assert the_run["enthalpy_exact"][1] == pytest.approx(NOMINAL * 31.0, abs=1e-3)


def test_an_arbitrary_request_is_not_composed_at_the_mixture_ratio(the_run):
    """Asking for equal parts of a 7 : 217 mixture serves neither.

    The stock draw is the SUM of what each flow asks beyond its transit, split
    at the raw share and then capped per flow. Water, whose share of the volume
    is 3 %, is served 3 % of the joint draw; heat is served the whole of what it
    asked. The pair is wrong in both directions, which is what makes "state the
    exact enthalpy rate" the only usable spelling.
    """
    water, heat = the_run["request_equal"]

    assert water == pytest.approx(0.0932, abs=1e-3)
    assert heat == pytest.approx(NOMINAL, abs=1e-3)
    # The ratio is nowhere near the tank's temperature.
    assert heat / water == pytest.approx(16.1, abs=0.2)


def test_an_unbounded_request_gets_the_ratio_and_loses_the_magnitude(the_run):
    """``inf`` on both flows composes at exactly T, and drains without bound.

    This is the one request that carries the mixture ratio without being told
    it -- and it offers the tank's whole content per unit time, so it cannot be
    a metered valve. Between this and the exact-constant spelling there is
    nothing: a rate proportional to a component's own integrated state has no
    declaration in MUSCADET 2.0.
    """
    water, heat = the_run["request_unbounded"]

    assert water > 0.0
    assert heat / water == pytest.approx(31.0, abs=1e-3)
    # ... and it is not 1.5.
    assert water > NOMINAL


def test_a_constituent_is_observable_but_no_shipped_sensor_thresholds_it(
    the_run,
):
    """The second blocker, and where it now stands: half lifted.

    It used to be whole. A capacity exported ONE level, the total raw quantity
    over every constituent, and that was the only thing a sensor could compare.
    On a water-plus-enthalpy tank that reads 7 + 217 = 224, so the regulation at
    6 and 8 and both feared-event detectors were watching the enthalpy: the
    overflow detector below is tripped from t = 0 on a tank holding 7 of water.

    A channel naming its constituents now reads each one, so ``H/V`` -- the
    mixture temperature -- is observable, and that half of the blocker is gone.
    What remains is that no SHIPPED component can act on it: a sensor's watched
    threshold is built from the ``{name, op, value}`` operand vocabulary, which
    names a measurement CHANNEL and has no way to name a constituent of one.
    So the reading is available to Python and unavailable to a guard, which is
    the boundary this test now marks.
    """
    # Unchanged, and deliberately so: a channel that names nothing still reads
    # the whole volume, which is what keeps every 1.x model working.
    assert the_run["enthalpy_reading"] == pytest.approx(224.0, abs=1e-1)
    assert the_run["enthalpy_detector"] is True

    # The levels the capacity holds, and the same two now arriving over the
    # channel rather than being read out of the capacity object in Python.
    assert the_run["enthalpy_water_level"] == pytest.approx(7.0, abs=1e-2)
    assert the_run["enthalpy_heat_level"] == pytest.approx(217.0, abs=1e-1)
    assert the_run["enthalpy_water_reading"] == pytest.approx(7.0, abs=1e-2)
    assert the_run["enthalpy_heat_reading"] == pytest.approx(217.0, abs=1e-1)
    assert the_run["enthalpy_total_reading"] == pytest.approx(224.0, abs=1e-1)

    # And the ratio an energy balance needs, which no reading gave before.
    assert the_run["enthalpy_temperature"] == pytest.approx(31.0, abs=1e-3)

    # The residual: the shipped sensor refuses the key, so a threshold on a
    # constituent has no declaration. Refused rather than silently ignored,
    # which is the one behaviour that would have been worse.
    refusal = the_run["kb_sensor_constituent"]
    assert refusal is not None, "SensorContinuous now accepts flows= -- update this"
    assert "'flows'" in refusal


def test_an_exponential_rate_is_a_variable_that_nothing_may_drive(the_run):
    """The third blocker: ``lambda(T)`` has the wiring but not the seam.

    ``add_exp_failure_mode`` creates a real ``t_double`` variable for the rate
    and hands the VARIABLE OBJECT to PyCATSHOO's ``newLaw``, so the parameter is
    already a model variable rather than a literal. What is missing is
    ``ITransition.setModifiable``: without it PyCATSHOO draws every firing time
    from the parameter's INITIAL value, so writing the variable during a run
    changes nothing. ``PycTransition`` carries ``is_interruptible`` and calls
    ``setInterruptible``; it has no counterpart for the modification mode.
    """
    assert the_run["rate_initial"] == pytest.approx(LAMBDA_0["P1"])
    assert the_run["rate_is_variable"], "the law should hold the variable itself"

    fields = the_run["transition_fields"]
    assert "is_interruptible" in fields
    assert not [name for name in fields if "modifiable" in name.lower()]


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
