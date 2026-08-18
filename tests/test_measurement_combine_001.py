"""Redundant instruments, and a measurement channel that VOTES on them (R37).

A model with redundant sensors must be able to reject a single stuck or wild
reading. The estimator that does that is the **median**; a mean does not, which
is the whole reason the redundancy is there.

What this exercises
-------------------
* the combination policies themselves -- ``sum`` (the identity on one reading,
  and therefore the only default that leaves an existing model alone), ``mean``,
  ``median``, ``min``, ``max`` -- and the Python extension point beside them,
  the mirror of ``FlowContinuousOut.allocation_fun``;
* the channel: a measurement observes exactly one publisher until a combination
  policy says how several of them reduce to one, so many-to-one is unreachable
  without stating the policy;
* the topology the IMDR port needs, end to end -- one tank, three instruments
  republishing its level, one voter combining their readings and driving a
  discrete control port. One instrument goes wild mid-run and the median is
  unmoved while the mean is dragged across the threshold and turns its control
  port on when nothing is there;
* **conservation**: the channel carries no quantity (an unobserved twin tank
  integrates identically), no combination policy can be declared on a flow, and
  no measurement channel can appear in a rule's ``cons`` or ``prod`` map.
"""

import cod3s
import pytest

import muscadet
from muscadet.kb.continuous import SensorContinuous

TANK_VOLUME = 1000.0
TANK_INIT = 20.0
TANK_INFLOW = 2.0

#: Level the voters activate at. The TRUE level reaches it at t = 5.
ACTIVATE = 30.0

#: When the wild instrument starts lying, and by how much.
FAULT_DATE = 2.0
FAULT_GAIN = 5.0

#: A date before the fault, so the healthy agreement is observed too.
HEALTHY_DATE = 1.0

#: Where the two voters are compared: after the fault, before the true crossing.
PROBE_DATE = 3.0

#: The TRUE level reaches ACTIVATE here, which is where the median must fire.
TRUE_CROSSING = (ACTIVATE - TANK_INIT) / TANK_INFLOW

HORIZON = TRUE_CROSSING + 1.0

#: The threshold crossings the deadband automata resolve are found by the
#: solver's root finder, so a level is compared at the tolerance the rest of the
#: suite uses on one.
TOL = 0.05


# ----------------------------------------------------------------------
# The policies themselves -- pure Python, no engine
# ----------------------------------------------------------------------


def test_the_named_policies_reduce_the_readings():
    """Each policy is the reduction its name says, over the whole reading list."""
    readings = [20.0, 24.0, 100.0]

    assert muscadet.combine(readings, policy="sum") == pytest.approx(144.0)
    assert muscadet.combine(readings, policy="mean") == pytest.approx(48.0)
    assert muscadet.combine(readings, policy="median") == pytest.approx(24.0)
    assert muscadet.combine(readings, policy="min") == pytest.approx(20.0)
    assert muscadet.combine(readings, policy="max") == pytest.approx(100.0)


def test_the_default_policy_is_the_identity_on_one_reading():
    """``sum`` is the generalisation of the single-source channel, not a change."""
    assert muscadet.combine([7.5]) == pytest.approx(7.5)
    assert muscadet.combine([7.5], policy="sum") == pytest.approx(7.5)

    # ... and it is what an undeclared policy resolves to.
    assert muscadet.combine([1.0, 2.0, 4.0]) == pytest.approx(7.0)


def test_no_reading_at_all_combines_to_the_declared_default():
    """A channel connected to nobody reads its default rather than raising."""
    assert muscadet.combine([], policy="median", default=-1.0) == pytest.approx(-1.0)
    assert muscadet.combine([], combine_fun=max, default=3.5) == pytest.approx(3.5)


def test_a_median_rejects_a_stuck_reading_where_a_mean_does_not():
    """The whole point of the redundancy, stated on the estimator alone.

    Two instruments agree on 20, a third is stuck at 100. The median is exactly
    what the two agreeing ones say; the mean is dragged a third of the way to
    the fault and lands above a threshold neither honest reading is near.
    """
    honest, stuck = 20.0, 100.0
    readings = [honest, honest, stuck]

    assert muscadet.combine(readings, policy="median") == pytest.approx(honest)
    assert muscadet.combine(readings, policy="mean") == pytest.approx(46.6667, abs=1e-3)

    # ... and the same, with the fault at either end of the list: a median is
    # order-independent, which a "drop the outlier" heuristic would not be.
    for permutation in ([stuck, honest, honest], [honest, stuck, honest]):
        assert muscadet.combine(permutation, policy="median") == pytest.approx(honest)

    # A stuck-LOW instrument is rejected exactly as a stuck-high one is.
    assert muscadet.combine([honest, honest, 0.0], policy="median") == pytest.approx(
        honest
    )
    assert muscadet.combine([honest, honest, 0.0], policy="mean") == pytest.approx(
        13.3333, abs=1e-3
    )


def test_the_python_extension_point_wins_over_the_named_policy():
    """``combine_fun`` mirrors ``allocation_fun``: preferred over the policy."""
    readings = [1.0, 2.0, 30.0]

    assert muscadet.combine(
        readings, policy="median", combine_fun=lambda values: min(values)
    ) == pytest.approx(1.0)


def test_an_unknown_policy_is_refused_at_declaration():
    """A misspelt policy fails where it is written, not on the first reading."""
    with pytest.raises(ValueError, match="unknown combination policy"):
        muscadet.MeasurementIn(name="m", combine="mediane")

    with pytest.raises(ValueError, match="combine_fun must be a callable"):
        muscadet.MeasurementIn(name="m", combine_fun="median")


# ----------------------------------------------------------------------
# Conservation: the restriction that has to be UNREACHABLE
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "flow_cls",
    [
        muscadet.FlowContinuousIn,
        muscadet.FlowContinuousOut,
        muscadet.FlowDiscreteIn,
        muscadet.FlowDiscreteOut,
    ],
    ids=lambda cls: cls.__name__,
)
def test_a_flow_refuses_a_combination_policy(flow_cls):
    """A conserved quantity is the SUM of its connections, permanently.

    Refused by name rather than left to a docstring: pydantic ignores unknown
    keys, so without the declared-and-refused field a ``combine="median"``
    written on a pipe would be accepted, dropped, and the flow would go on
    summing -- a model that reads as a vote and behaves as a sum.
    """
    with pytest.raises(ValueError, match="cannot be declared on a flow"):
        flow_cls(name="q", combine="median")

    with pytest.raises(ValueError, match="cannot be declared on a flow"):
        flow_cls(name="q", combine_fun=lambda values: values[0])

    # ... and the refusal says what to do instead, both ways round.
    with pytest.raises(ValueError, match="add_measurement_in"):
        flow_cls(name="q", combine="median")
    with pytest.raises(ValueError, match="logic=2"):
        flow_cls(name="q", combine="median")


def test_a_flow_declaring_nothing_is_untouched():
    """The refusal costs a flow that declares no policy exactly nothing."""
    flow = muscadet.FlowContinuousIn(name="q")

    assert flow.combine is None
    assert flow.combine_fun is None

    # Excluded from the dump, like every other non-declaration field: the
    # serialised shape of a flow is what it was.
    assert "combine" not in flow.model_dump()
    assert "combine_fun" not in flow.model_dump()


# ----------------------------------------------------------------------
# The model: one tank, three instruments, two voters
# ----------------------------------------------------------------------


class McTank(muscadet.ObjFlow):
    """A tank filled through a connection, publishing its level."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="w", var_demand_default=TANK_INFLOW)
        self.add_capacity(
            name="tank",
            flow="w",
            capacity=TANK_VOLUME,
            content_init={"w": TANK_INIT},
        )


class McFeed(muscadet.ObjFlow):
    """The rate each tank is filled at."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="w", var_fed_default=TANK_INFLOW)


class McInstrument(muscadet.ObjFlow):
    """Reads the tank and republishes what it read, gain applied.

    The component redundancy is made OF: three of these between one tank and
    whoever votes, each able to fail on its own. Several observations of one
    tank would be identical and would reject nothing.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="tank")
        self.add_measurement_out(name="reading", source="tank")


class McVoter(muscadet.ObjFlow):
    """Combines the instruments' readings and drives a discrete control port.

    A rule guard may not read a level (R29) and may not read a measurement
    either, so the sanctioned route is unchanged: threshold the reading here and
    let a producer's guard read the control port.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="reading", combine=kwargs.get("combine", "median"))
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="alarm",
                var_prod_cond=[{"name": "reading", "op": ">=", "value": ACTIVATE}],
            )
        )


def build_system():
    """One observed tank with its instruments and voters, one unobserved twin."""
    system = muscadet.System(name="McCombineSys")

    for tank, feed in (("TOBS", "FOBS"), ("TREF", "FREF")):
        system.add_component(name=tank, cls="McTank")
        system.add_component(name=feed, cls="McFeed")
        system.connect_flow(source=feed, target=tank, flow_name="w")

    for name in ("I1", "I2", "I3"):
        system.add_component(name=name, cls="McInstrument")
        system.connect("TOBS", "tank_level_out", name, "tank_level_in")

    for name, policy in (("MEDIAN", "median"), ("MEAN", "mean")):
        system.add_component(name=name, cls="McVoter", combine=policy)
        for instrument in ("I1", "I2", "I3"):
            system.connect(instrument, "reading_level_out", name, "reading_level_in")

    # The fault: one instrument's published reading is multiplied by five from
    # FAULT_DATE on. ``reading_level_gain`` is a public component variable, so
    # this is the ordinary effect path -- no measurement-specific call.
    system.comp["I3"].add_delay_failure_mode(
        name="wild",
        failure_time=FAULT_DATE,
        failure_effects=[("^reading_level_gain$", FAULT_GAIN)],
        repair_cond=False,
    )

    # Dates the interactive session can always step to. Without them the only
    # stops would be the fault and the two threshold crossings, and the healthy
    # agreement before the fault would never be sampled.
    for comp_name, aut_name, date in (
        ("FOBS", "clock_healthy", HEALTHY_DATE),
        ("FREF", "clock_probe", PROBE_DATE),
        ("TREF", "clock_end", HORIZON),
    ):
        system.comp[comp_name].add_atm2states(
            name=aut_name,
            st1="s0",
            st2="s1",
            occ_law_12={"cls": "delay", "time": date},
            cond_occ_21=False,
        )

    return system


def snapshot(system):
    """Everything the assertions read, at the session's current date."""
    return {
        "time": system.currentTime(),
        "observed": system.comp["TOBS"].capacities["tank"].total_quantity(),
        "reference": system.comp["TREF"].capacities["tank"].total_quantity(),
        "readings": [
            system.comp[name].measurements_out["reading"].get_level()
            for name in ("I1", "I2", "I3")
        ],
        "gain": system.comp["I3"].measurements_out["reading"].get_gain(),
        "median": system.comp["MEDIAN"].measurements_in["reading"].get_level(),
        "mean": system.comp["MEAN"].measurements_in["reading"].get_level(),
        "median_alarm": system.comp["MEDIAN"].flows_out["alarm"].var_fed.value(),
        "mean_alarm": system.comp["MEAN"].flows_out["alarm"].var_fed.value(),
        # Read through the channel itself rather than off the publishers: this
        # is what a median cannot be recovered from a sum means, observed.
        "channel": system.comp["MEDIAN"]
        .measurements_in["reading"]
        .readings(
            system.comp["MEDIAN"].measurements_in["reading"].var_level,
            0.0,
        ),
        "channel_sum": system.comp["MEDIAN"]
        .measurements_in["reading"]
        .var_level.sumValue(0.0),
    }


@pytest.fixture(scope="module")
def the_system():
    """Drive one interactive session and record it at every stop."""
    system = build_system()

    system.isimu_start()

    trace = [snapshot(system)]
    for _ in range(60):
        if system.currentTime() >= HORIZON:
            break
        system.isimu_step_forward()
        trace.append(snapshot(system))

    obs = {"system": system, "trace": trace}

    def at(date):
        """The last snapshot taken at or before ``date``."""
        return [snap for snap in trace if snap["time"] <= date + 1e-9][-1]

    obs["before_fault"] = at(HEALTHY_DATE)
    obs["probe"] = at(PROBE_DATE)
    obs["end"] = trace[-1]

    system.isimu_stop()

    return obs


def test_the_instruments_all_report_the_level_while_healthy(the_system):
    """Before the fault the three readings agree, and both voters are right."""
    snap = the_system["before_fault"]
    expected = TANK_INIT + TANK_INFLOW * snap["time"]

    assert snap["observed"] == pytest.approx(expected, abs=TOL)
    for reading in snap["readings"]:
        assert reading == pytest.approx(expected, abs=TOL)

    assert snap["median"] == pytest.approx(expected, abs=TOL)
    assert snap["mean"] == pytest.approx(expected, abs=TOL)


def test_the_median_rejects_the_wild_instrument_and_the_mean_does_not(the_system):
    """THE regression: one instrument lies, and only the mean believes it."""
    snap = the_system["probe"]
    true_level = TANK_INIT + TANK_INFLOW * snap["time"]

    # The fault has fired: the third instrument reports five times the level.
    assert snap["time"] >= FAULT_DATE
    assert snap["gain"] == pytest.approx(FAULT_GAIN)
    assert snap["readings"][2] == pytest.approx(FAULT_GAIN * true_level, abs=TOL)

    # ... while the two healthy ones still report it.
    assert snap["readings"][0] == pytest.approx(true_level, abs=TOL)
    assert snap["readings"][1] == pytest.approx(true_level, abs=TOL)

    # The median is exactly what the healthy majority says. The mean is not:
    # it is dragged a third of the fault's excess above the true level.
    assert snap["median"] == pytest.approx(true_level, abs=TOL)
    assert snap["mean"] == pytest.approx(
        (2 * true_level + FAULT_GAIN * true_level) / 3, abs=TOL
    )
    assert snap["mean"] > snap["median"] + 20.0


def test_only_the_mean_voter_raises_a_spurious_alarm(the_system):
    """And the difference is observable on the control port, not just the number.

    At the probe date the true level is below the activation threshold, so no
    alarm is warranted. The median voter agrees; the mean voter has been carried
    across the threshold by a single instrument.
    """
    snap = the_system["probe"]
    true_level = TANK_INIT + TANK_INFLOW * snap["time"]

    assert true_level < ACTIVATE
    assert snap["median"] < ACTIVATE
    assert snap["mean"] > ACTIVATE

    assert snap["median_alarm"] is False
    assert snap["mean_alarm"] is True


def test_the_median_voter_still_fires_when_it_should(the_system):
    """Rejecting a fault is not the same as being deaf: the vote still works.

    Once the TRUE level reaches the threshold, two honest instruments carry the
    median across it and the alarm comes on -- so the test above is not passing
    merely because the median voter never fires at all.
    """
    trace = the_system["trace"]
    fired = [snap for snap in trace if snap["median_alarm"]]

    assert fired, "the median voter never raised its alarm"

    first = fired[0]
    true_level = TANK_INIT + TANK_INFLOW * first["time"]

    assert true_level == pytest.approx(ACTIVATE, abs=TOL)
    assert first["time"] == pytest.approx(TRUE_CROSSING, abs=TOL)

    # ... and it fired LATER than the mean voter, which had been carried across
    # the threshold at the fault date by a single instrument.
    mean_fired = [snap for snap in trace if snap["mean_alarm"]]
    assert mean_fired
    assert mean_fired[0]["time"] < first["time"]


def test_the_channel_carries_no_quantity(the_system):
    """Three instruments and two voters draw nothing out of the observed tank."""
    for key in ("before_fault", "probe", "end"):
        snap = the_system[key]
        assert snap["observed"] == pytest.approx(snap["reference"], rel=1e-9)

    # The instruments and the voters hold no flow at all: they are outside both
    # sweeps, and no allocation can reach them.
    system = the_system["system"]
    for name in ("I1", "I2", "I3", "MEDIAN", "MEAN"):
        comp = system.comp[name]
        assert comp.flows_in == {}
        assert comp.flows_out == {} or set(comp.flows_out) == {"alarm"}
        assert comp.capacities == {}


def test_the_observer_cannot_write_what_it_reads(the_system):
    """Read-only by construction: the importing endpoints are references."""
    measurement = the_system["system"].comp["MEDIAN"].measurements_in["reading"]

    assert not hasattr(measurement.var_level, "setValue")
    with pytest.raises(AttributeError):
        measurement.var_level.setValue(0.0)


def test_the_combining_channel_holds_the_individual_readings(the_system):
    """A median cannot be recovered from a sum, so the values stay reachable."""
    measurement = the_system["system"].comp["MEDIAN"].measurements_in["reading"]
    snap = the_system["probe"]

    assert measurement.var_level.cnctCount() == 3
    assert measurement.combines_several is True

    assert len(snap["channel"]) == 3
    assert sorted(snap["channel"]) == pytest.approx(sorted(snap["readings"]), abs=TOL)

    # ... and the sum the single-source path would have collapsed them to is a
    # different number entirely, which is why the cap cannot simply be lifted
    # and left summing.
    assert snap["channel_sum"] == pytest.approx(sum(snap["channel"]), abs=TOL)
    assert snap["channel_sum"] > snap["median"] * 2


def test_a_measurement_observes_one_publisher_until_a_policy_says_otherwise(
    the_system,
):
    """The cap is the default, and declaring a policy is the only way past it."""
    system = the_system["system"]

    single = system.comp["I1"].measurements_in["tank"]
    assert single.combines_several is False
    assert single.var_level.cnctMax() == 1
    assert single.var_fill.cnctMax() == 1

    combining = system.comp["MEDIAN"].measurements_in["reading"]
    assert combining.var_level.cnctMax() != 1


def test_a_rule_cannot_consume_or_produce_a_measurement(the_system):
    """Conservation, at the one place a non-conserved estimator could leak in."""
    voter = the_system["system"].comp["MEDIAN"]

    with pytest.raises(ValueError, match="which is not a flow"):
        voter.add_rules(
            name="mc_cons", rules=[dict(cons={"reading": 1.0}, prod={"alarm": 1.0})]
        )

    with pytest.raises(ValueError, match="conserves nothing"):
        voter.add_rules(name="mc_prod", rules=[dict(prod={"reading": 1.0})])

    # A guard is refused too, and pointed at the sanctioned route.
    with pytest.raises(ValueError, match="cannot read a measurement"):
        voter.add_rules(
            name="mc_guard",
            rules=[dict(cond=[{"name": "reading"}], prod={"alarm": 1.0})],
        )


def test_a_published_measurement_cannot_collide_with_a_capacity(the_system):
    """The two export the same variable names, so the clash is refused."""
    instrument = the_system["system"].comp["I1"]

    with pytest.raises(ValueError, match="already exists"):
        instrument.add_measurement_out(name="reading")

    with pytest.raises(ValueError, match="already exports a level"):
        instrument.add_capacity(name="reading", flow="w", capacity=1.0)


def test_the_gain_is_the_public_endpoint_a_mode_clamps(the_system):
    """One variable per published channel, created at 1, never written by us."""
    system = the_system["system"]

    for name in ("I1", "I2"):
        published = system.comp[name].measurements_out["reading"]
        assert published.var_gain.basename() == "reading_level_gain"
        assert published.get_gain() == pytest.approx(1.0)

    # ... and it is a plain component variable, which is what let the failure
    # mode above reach it by name with no measurement-specific call.
    names = {var.basename() for var in system.comp["I3"].variables()}
    assert {"reading_level", "reading_fill", "reading_level_gain"} <= names


def test_the_sensor_kb_component_carries_the_same_two_keys():
    """``SensorContinuous`` reaches both halves through DECLARATION_KEYS (R-3)."""
    accepted = SensorContinuous.accepted_declaration_keys()

    assert {"combine", "combine_fun", "publish", "gain"} <= accepted

    # A misspelling of either is still refused rather than swallowed.
    assert "comibne" not in accepted
    assert "pubish" not in accepted


def test_delete(the_system):
    the_system["system"].deleteSys()
    cod3s.terminate_session()
