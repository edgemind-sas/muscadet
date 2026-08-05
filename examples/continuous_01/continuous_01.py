"""A continuous flow model exercising rules, a capacity, a sensor and a derating.

The plant is a bottling line:

    PUMP --water--> TANK --water--> MIXER --syrup--> BOTTLER
                     |                 ^
                 tank level        run (discrete)
                     |                 |
                     v                CMD
                   LEVEL --fill--> PUMP

* ``PUMP`` is a continuous source gated by a discrete control port.
* ``TANK`` is a capacity buffering the water, claiming an unbounded fill rate
  so it stocks up with whatever the pump delivers beyond what the mixer draws.
* ``LEVEL`` is a sensor reading the tank level over a measurement link and
  driving the pump's control port. Its deadband -- refill below 20, stop above
  50 -- is what keeps the loop from chattering around a single threshold.
* ``MIXER`` turns 2 units of water into 1 of syrup, under a rule guarded on a
  discrete command.
* ``BOTTLER`` asks for 3 units of syrup, so the line draws 6 of water.

Two events disturb it:

* at t = 13 a delay failure mode **derates** the pump to 40 % of its rate, so
  the refill can no longer outrun the draw and the tank empties;
* at t = 28 the discrete command drops, the mixer's guard selects the idle rule,
  the water demand collapses and the tank refills.

The script drives the model with the interactive API, which stops at events
rather than on an integration grid, and prints the resulting trace.
"""

import math

import muscadet

# The five shipped continuous components resolve by name, so importing them is
# what makes cls="SourceContinuous" work.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SensorContinuous,
    SourceContinuous,
)

# Plant parameters
# ================

PUMP_RATE = 10.0  # what the pump can deliver, at nominal
PUMP_DERATING = 0.4  # what the wear mode leaves of it
WEAR_DATE = 13.0

TANK_VOLUME = 60.0
TANK_INIT = 40.0

REFILL_BELOW = 20.0  # the sensor calls for water at this level
STOP_ABOVE = 50.0  # ... and releases the call at this one

SYRUP_DEMAND = 3.0  # what the bottler asks for
WATER_PER_SYRUP = 2.0  # the recipe, so 6 of water per unit of time

TRIP_DATE = 28.0
HORIZON = 34.0

NEVER = 1e6  # a repair that does not happen within the horizon


# Component classes
# =================


class Mixer(muscadet.ObjFlow):
    """2 units of water make 1 of syrup, while a discrete command holds.

    The rule set is declared on the COMPONENT, guarded on the discrete input
    ``run``: a boolean flow is an ordinary guard operand, so the discrete and
    the continuous families meet here without any glue.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(name="run", logic="and")
        self.add_flow_continuous_in(name="water")
        self.add_flow_continuous_out(name="syrup")

        self.add_rules(
            name="recipe",
            rules=[
                dict(
                    name="mixing",
                    cond="run",
                    cons={"water": WATER_PER_SYRUP},
                    prod={"syrup": 1.0},
                ),
                dict(name="idle", cond="not run", prod={"syrup": 0.0}),
            ],
        )


class Command(muscadet.ObjFlow):
    """An ordinary discrete output: the boolean side of MUSCADET, unchanged."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_out(name="run", var_prod_default=True)


class Clock(muscadet.ObjFlow):
    """Nothing but a date the interactive session can always step to."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


# System building
# ===============


def build_system():
    system = muscadet.System(name="Continuous bottling line")

    system.add_component(
        name="PUMP",
        cls="SourceContinuous",
        flow="water",
        rate=PUMP_RATE,
        control="fill",
    )
    system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        flow="water",
        capacity=TANK_VOLUME,
        capacity_name="tank",
        fill_rate=math.inf,
        content_init={"water": TANK_INIT},
    )
    system.add_component(
        name="LEVEL",
        cls="SensorContinuous",
        measurement="tank",
        control="fill",
        direction="below",
        activate=REFILL_BELOW,
        release=STOP_ABOVE,
    )
    system.add_component(name="MIXER", cls="Mixer")
    system.add_component(name="CMD", cls="Command")
    system.add_component(
        name="BOTTLER",
        cls="ConsumerContinuous",
        flow="syrup",
        demand=SYRUP_DEMAND,
    )
    system.add_component(name="CLOCK", cls="Clock")

    # The continuous chain
    system.connect_flow(source="PUMP", target="TANK", flow_name="water")
    system.connect_flow(source="TANK", target="MIXER", flow_name="water")
    system.connect_flow(source="MIXER", target="BOTTLER", flow_name="syrup")

    # The measurement link, and the control port closing the loop back onto the
    # pump. Neither carries a quantity, so neither enters the acyclicity check.
    system.connect("TANK", "tank_level_out", "LEVEL", "tank_level_in")
    system.connect_flow(source="LEVEL", target="PUMP", flow_name="fill")

    # The discrete command the mixer's rule guard reads
    system.connect_flow(source="CMD", target="MIXER", flow_name="run")

    # Failure modes
    # -------------
    # A derating: the effect names the CONTINUOUS OUTPUT, and the library gives
    # this mode a variable of its own on it. Several modes derating one output
    # compose by MINIMUM, so no mode can undo another one's degradation.
    system.comp["PUMP"].add_delay_failure_mode(
        name="wear",
        failure_time=WEAR_DATE,
        failure_effects=[("water", PUMP_DERATING)],
        repair_time=NEVER,
    )

    # A 1.x boolean effect, on the discrete side, unchanged.
    system.comp["CMD"].add_delay_failure_mode(
        name="trip",
        failure_time=TRIP_DATE,
        failure_effects=[("run_fed_available_out", False)],
        repair_time=NEVER,
    )

    # A stop at the horizon, so the session always has somewhere to step to.
    system.comp["CLOCK"].add_atm2states(
        name="horizon",
        st1="before",
        st2="after",
        occ_law_12={"cls": "delay", "time": HORIZON},
        cond_occ_21=False,
    )

    return system


# Driving the model
# =================


def active_rule_name(comp, rule_set_name):
    """The rule the guards currently select, or "-" when none applies.

    A rule set declaring no default rule has a "no rule applies" mode, and a
    set selecting nothing produces zero rather than falling back on a rule.
    """
    rule = comp.rule_sets[rule_set_name].active_rule()

    return "-" if rule is None else rule.name


def clean(value, tol=1e-4):
    """Report a residue within the solver's crossing tolerance as a plain 0.

    A bound is stopped ON rather than refined to machine precision, so a
    capacity that has just emptied reads a few 1e-5 below zero.
    """
    return 0.0 if abs(value) < tol else value


def snapshot(system):
    """What the line reads right now."""
    tank = system.comp["TANK"].capacities["tank"]

    return {
        "time": system.currentTime(),
        "level": clean(tank.get_quantity("water")),
        "inflow": clean(tank.get_inflow("water")),
        "outflow": clean(tank.get_outflow("water")),
        "rate": system.comp["PUMP"].flows_out["water"].get_effective_rate(),
        "pumped": clean(system.comp["PUMP"].flows_out["water"].var_fed.value()),
        "water": clean(system.comp["MIXER"].flows_in["water"].get_delivered()),
        "syrup": clean(system.comp["BOTTLER"].flows_in["syrup"].get_delivered()),
        "fill": system.comp["LEVEL"].flows_out["fill"].var_fed.value(),
        "run": system.comp["MIXER"].flows_in["run"].var_fed.value(),
        "mode": active_rule_name(system.comp["MIXER"], "recipe"),
    }


def walk(system, horizon=HORIZON, limit=80):
    """Step to ``horizon``, recording a snapshot at every stop.

    Several transitions may fire at one date -- a threshold crossing, then the
    mode change it causes -- so a stop that changes nothing readable is left
    out: what is kept is one row per observable change.
    """
    trace = [snapshot(system)]

    for _ in range(limit):
        if system.currentTime() >= horizon:
            break
        system.isimu_step_forward()
        entry = snapshot(system)
        if entry != trace[-1]:
            trace.append(entry)

    return trace


HEADER = (
    f"{'t':>7} {'level':>7} {'in':>6} {'out':>6} {'rate':>5} "
    f"{'water':>6} {'syrup':>6} {'fill':>5} {'run':>5}  mode"
)


def format_row(entry):
    return (
        f"{entry['time']:7.3f} {entry['level']:7.3f} {entry['inflow']:6.2f} "
        f"{entry['outflow']:6.2f} {entry['rate']:5.2f} {entry['water']:6.2f} "
        f"{entry['syrup']:6.2f} {str(entry['fill']):>5} {str(entry['run']):>5}"
        f"  {entry['mode']}"
    )


def switch_dates(trace, key):
    """The dates at which ``key`` changed value, in order."""
    return [
        entry["time"]
        for previous, entry in zip(trace, trace[1:])
        if entry[key] != previous[key]
    ]


def report(trace):
    """The trace, and the four things it is meant to show."""
    emptied = [entry for entry in trace if entry["level"] <= 0.0]

    lines = [
        "",
        "Continuous bottling line -- one row per event the solver stopped at",
        "=" * 78,
        HEADER,
        "-" * 78,
    ]
    lines += [format_row(entry) for entry in trace]
    lines += [
        "-" * 78,
        "",
        "What the trace shows",
        "--------------------",
        "the sensor's deadband switched the pump at t = "
        + ", ".join(f"{date:.3f}" for date in switch_dates(trace, "fill")),
        "the derating took the pump's effective rate from "
        f"{trace[0]['rate']:.2f} to {trace[-1]['rate']:.2f} at t = "
        + ", ".join(f"{date:.3f}" for date in switch_dates(trace, "rate")),
        (
            f"the tank emptied at t = {emptied[0]['time']:.3f}, and the bottler then "
            f"received {emptied[0]['syrup']:.2f} of the {SYRUP_DEMAND:.2f} asked for"
            if emptied
            else "the tank never emptied"
        ),
        "the guard flipped to the idle rule at t = "
        + ", ".join(f"{date:.3f}" for date in switch_dates(trace, "run")),
        f"tank level at the horizon : {trace[-1]['level']:.3f}",
        "",
    ]

    return "\n".join(lines)


my_line = build_system()

my_line.isimu_start()
trace = walk(my_line)
my_line.isimu_stop()

print(report(trace))
