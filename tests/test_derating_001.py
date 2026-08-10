"""Derating: what a failure mode leaves of a continuous output (R18, R19, R20).

A continuous output carries an **effective rate**, defaulting to 1, by which
whatever it produces is multiplied. A failure mode declares its effect against
the output flow, and the library allocates ONE derating variable per (mode,
output flow) pair -- named ``{mode}_derating_{flow}`` -- and rewrites the effect
onto it at declaration time. The effective rate is the **minimum** over those
variables (R20, KD11, KTD8).

Why the per-mode variable, and why the minimum
----------------------------------------------
Were two modes to clamp one shared rate variable, the minimum would be
unreachable: whichever fired last would win, and the first of them to repair
would write the rate back to 1 while the other degradation still stood. The
reset-and-reclamp convention that makes boolean effects compose for free -- a
gate reinitialised at every step and re-clamped by every active mode, so
``False`` wins whatever the order -- does not transpose to numbers, because no
reset value is neutral for a minimum. Hence: one variable per mode, released by
its own mode alone, and a minimum the library computes.

Reading the trace
-----------------
Everything runs in ONE interactive session -- PyCATSHOO forbids more than one
live system per process -- and every stop is snapshotted. A stop reports the
LEFT limit of the continuous variables: at the very instant a mode fires, the
effective rate already reflects it but the quantities still hold what the
integration up to that instant produced. Rates are therefore asserted AT the
stop and quantities at the next one, which is what the clock stops below are
for.
"""

import muscadet
import cod3s
import pytest

# Imported for its side effect: a component class resolves by name.
from muscadet.kb.continuous import CapacityContinuous  # noqa: F401

#: Horizon the interactive session runs to.
HORIZON = 4.5

#: Stops added so that a quantity can be read strictly after each mode change.
CLOCK_DATES = (1.5, 2.5, 3.5, 4.5)

#: A mode that never comes back within the horizon.
NEVER = 1e6

#: Components whose output rate and quantity are traced through the run.
DERATED = ["PLANT", "PLANT_REV", "ZEROED", "AUTO", "EXPLICIT", "DRAINED"]

# -- The stock behind a derated converter (R-13). A rate of 0 stops the
# -- production, and it must stop the DRAW that fed it: with an unstocked
# -- source upstream nothing is depleted and an over-draw leaves no trace at
# -- all, which is why the cut is also measured against a reservoir here.
DRAIN_VOLUME = 200.0
DRAIN_STOCK = 100.0
#: What the sink asks for, and therefore what the converter draws while alive.
DRAIN_RATE = 2.0
#: The date the converter's output is cut, shared with ``ZEROED``.
DRAIN_CUT = 1.0


# ----------------------------------------------------------------------
# Components -- prefixed, since component classes resolve by name globally
# ----------------------------------------------------------------------


class DeratingSource(muscadet.ObjFlow):
    """A continuous producer holding the rate it was declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name=kwargs.get("flow", "q"), var_fed_default=kwargs.get("rate", 1.0)
        )


class DeratingSink(muscadet.ObjFlow):
    """A continuous consumer asking for more than it can ever be served."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "q"), var_demand_default=kwargs.get("demand", 1e3)
        )


class DeratingPlant(muscadet.ObjFlow):
    """A continuous output produced at a declared rate, with nothing upstream."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="X", var_fed_default=kwargs.get("rate", 10.0))


class DeratingConverter(muscadet.ObjFlow):
    """One unit of ``q`` in, one unit of ``X`` out: production follows the input."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="X")
        self.add_rules(name="X", rules=[dict(cons={"q": 1}, prod={"X": 1})])


class DeratingSignal(muscadet.ObjFlow):
    """A DISCRETE producer: the 1.x boolean shape, to check it is untouched."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="b", var_prod_default=True)


class DeratingLamp(muscadet.ObjFlow):
    """A DISCRETE consumer lit by the signal it receives."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="b", logic="and")
        self.add_flow_out(name="lit", var_prod_cond=["b"])


class DeratingClock(muscadet.ObjFlow):
    """Nothing but dates the interactive session can stop at."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


# ----------------------------------------------------------------------
# Building the one system every scenario lives in
# ----------------------------------------------------------------------


def add_clock(comp, date):
    """Give the session a stop at ``date`` it can always step to."""
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="before",
        st2="after",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def build_system():
    system = muscadet.System(name="DeratingSys")

    # -- AE13 / AE14: two modes derating one output, one of them repairing.
    #    deg_a degrades at 1 and is repaired at 4; deg_b degrades at 2 and stays.
    system.add_component(name="PLANT", cls="DeratingPlant", rate=10.0)
    system.add_component(name="PLANT_SINK", cls="DeratingSink", flow="X", demand=1e3)
    system.connect_flow(source="PLANT", target="PLANT_SINK", flow_name="X")
    system.comp["PLANT"].add_delay_failure_mode(
        name="deg_a",
        failure_time=1.0,
        failure_effects=[("X", 0.5)],
        repair_time=3.0,
    )
    system.comp["PLANT"].add_delay_failure_mode(
        name="deg_b",
        failure_time=2.0,
        failure_effects=[("X", 0.8)],
        repair_time=NEVER,
    )

    # -- The SAME two deratings applied in the opposite order.
    system.add_component(name="PLANT_REV", cls="DeratingPlant", rate=10.0)
    system.comp["PLANT_REV"].add_delay_failure_mode(
        name="deg_a",
        failure_time=2.0,
        failure_effects=[("X", 0.5)],
        repair_time=NEVER,
    )
    system.comp["PLANT_REV"].add_delay_failure_mode(
        name="deg_b",
        failure_time=1.0,
        failure_effects=[("X", 0.8)],
        repair_time=NEVER,
    )

    # -- AE15: a mode setting the rate to 0, on a component whose production
    #    follows its input. The input keeps arriving; the output stops.
    system.add_component(name="SUPPLY", cls="DeratingSource", flow="q", rate=7.0)
    system.add_component(name="ZEROED", cls="DeratingConverter")
    system.connect_flow(source="SUPPLY", target="ZEROED", flow_name="q")
    # The downstream that takes everything ZEROED can make, declared the way
    # PLANT's already is. It used to be left implicit: an unwired output
    # demanded without bound and the converter drew its whole supply. Since
    # R-10 an unwired output constrains nothing and the rule falls back to its
    # nominal scale, so "the input keeps arriving" is stated rather than
    # inferred from the absence of a consumer.
    system.add_component(name="ZEROED_SINK", cls="DeratingSink", flow="X", demand=1e3)
    system.connect_flow(source="ZEROED", target="ZEROED_SINK", flow_name="X")
    system.comp["ZEROED"].add_delay_failure_mode(
        name="cut",
        failure_time=1.0,
        failure_effects=[("X", 0)],
        repair_time=NEVER,
    )

    # -- The same cut as ZEROED, with a STOCK behind the input instead of a
    #    source (R-13). The reservoir is what makes an over-draw visible: an
    #    unstocked source is never depleted, so a component consuming at the
    #    nominal rate while producing nothing leaves no trace on it.
    system.add_component(
        name="DRAIN_STOCK",
        cls="CapacityContinuous",
        ports="out",
        flow="q",
        capacity=DRAIN_VOLUME,
        capacity_name="reservoir",
        content_init={"q": DRAIN_STOCK},
    )
    system.add_component(name="DRAINED", cls="DeratingConverter")
    system.add_component(
        name="DRAINED_SINK", cls="DeratingSink", flow="X", demand=DRAIN_RATE
    )
    system.connect_flow(source="DRAIN_STOCK", target="DRAINED", flow_name="q")
    system.connect_flow(source="DRAINED", target="DRAINED_SINK", flow_name="X")
    system.comp["DRAINED"].add_delay_failure_mode(
        name="cut",
        failure_time=DRAIN_CUT,
        failure_effects=[("X", 0)],
        repair_time=NEVER,
    )

    # -- The library's return to nominal, against the same thing declared by
    #    hand: both cycle at 1 / 2 / 3 / 4 and must trace identically.
    system.add_component(name="AUTO", cls="DeratingPlant", rate=10.0)
    system.comp["AUTO"].add_delay_failure_mode(
        name="dip",
        failure_time=1.0,
        failure_effects=[("X", 0.25)],
        repair_time=1.0,
    )
    system.add_component(name="EXPLICIT", cls="DeratingPlant", rate=10.0)
    system.comp["EXPLICIT"].add_delay_failure_mode(
        name="dip",
        failure_time=1.0,
        failure_effects=[("X", 0.25)],
        repair_time=1.0,
        repair_effects=[("X", 1.0)],
    )

    # -- 1.x parity: a boolean effect on a discrete flow.
    system.add_component(name="SIGNAL", cls="DeratingSignal")
    system.add_component(name="LAMP", cls="DeratingLamp")
    system.connect_flow(source="SIGNAL", target="LAMP", flow_name="b")
    system.comp["SIGNAL"].add_delay_failure_mode(
        name="stop",
        failure_time=1.0,
        failure_effects=[("b_fed_available_out", False)],
        repair_time=NEVER,
    )

    # -- The exponential helper funnels through the same retargeting.
    system.add_component(name="WEARING", cls="DeratingPlant", rate=4.0)
    system.comp["WEARING"].add_exp_failure_mode(
        name="wear",
        failure_rate=1e-3,
        failure_effects=system.comp["WEARING"].compute_effects_tuples("X=0.25"),
        repair_rate=0.0,
    )

    system.add_component(name="CLOCK", cls="DeratingClock")
    for date in CLOCK_DATES:
        add_clock(system.comp["CLOCK"], date)

    return system


def snapshot(system):
    """The rate and the quantity of every traced output, at one stop."""
    return {
        "time": system.currentTime(),
        "rate": {
            name: system.comp[name].flows_out["X"].get_effective_rate()
            for name in DERATED
        },
        "X": {
            name: system.comp[name].flows_out["X"].var_fed.value() for name in DERATED
        },
        "derating": {
            mode: var.value()
            for mode, var in system.comp["PLANT"].flows_out["X"].derating.items()
        },
        "plant_sink": system.comp["PLANT_SINK"].flows_in["X"].var_fed.value(),
        "zeroed_q": system.comp["ZEROED"].flows_in["q"].var_fed.value(),
        "drained_q": system.comp["DRAINED"].flows_in["q"].get_delivered(),
        "reservoir": system.comp["DRAIN_STOCK"]
        .capacities["reservoir"]
        .get_quantity("q"),
        "reservoir_outflow": system.comp["DRAIN_STOCK"]
        .capacities["reservoir"]
        .get_outflow("q"),
        "signal_b": system.comp["SIGNAL"].flows_out["b"].var_fed.value(),
        "lamp_lit": system.comp["LAMP"].flows_out["lit"].var_fed.value(),
    }


@pytest.fixture(scope="module")
def the_run():
    """Drive the system to the horizon, recording every stop."""
    system = build_system()

    system.isimu_start()

    trace = [snapshot(system)]
    for _ in range(60):
        system.isimu_step_forward()
        trace.append(snapshot(system))
        if system.currentTime() >= HORIZON:
            break

    system.isimu_stop()

    return {"system": system, "trace": trace}


def at(trace, date):
    """The stop AT ``date``: rates are read there, the mode has just fired."""
    for entry in trace:
        if entry["time"] == pytest.approx(date):
            return entry
    raise AssertionError(
        f"no stop at t={date}; stops were {[e['time'] for e in trace]}"
    )


def after(trace, date):
    """The first stop strictly after ``date``: where the quantities are read."""
    for entry in trace:
        if entry["time"] > date:
            return entry
    raise AssertionError(f"no stop after t={date}")


# ----------------------------------------------------------------------
# The acceptance examples
# ----------------------------------------------------------------------


def test_two_simultaneous_deratings_compose_by_minimum(the_run):
    """AE13: X derated at 0.5 and at 0.8 at once, and the effective rate is 0.5.

    Not 0.4 (the product), not 0.8 (the last applied): the minimum, per KD11.
    """
    trace = the_run["trace"]

    # Only deg_a has fired yet.
    assert at(trace, 1.0)["rate"]["PLANT"] == pytest.approx(0.5)

    # ... and deg_b joining it at 0.8 leaves the harsher one standing.
    both = at(trace, 2.0)
    assert both["derating"] == {
        "deg_a": pytest.approx(0.5),
        "deg_b": pytest.approx(0.8),
    }
    assert both["rate"]["PLANT"] == pytest.approx(0.5)

    # The output produces 10 nominally, so 5 once the run resumes.
    assert after(trace, 2.0)["X"]["PLANT"] == pytest.approx(5.0)


def test_repairing_one_derating_restores_the_other_one_not_the_nominal(the_run):
    """AE14: the 0.5 mode repairs, the 0.8 one holds, and the rate becomes 0.8.

    The failure this unit exists to prevent: on a shared rate variable the
    repair would have written 1 back and hidden a degradation that never went
    away.
    """
    trace = the_run["trace"]

    repaired = at(trace, 4.0)
    assert repaired["derating"] == {
        "deg_a": pytest.approx(1.0),
        "deg_b": pytest.approx(0.8),
    }
    assert repaired["rate"]["PLANT"] == pytest.approx(0.8)

    assert after(trace, 4.0)["X"]["PLANT"] == pytest.approx(8.0)


def test_a_rate_of_zero_stops_production_whatever_the_inputs(the_run):
    """AE15: a mode setting the rate of X to 0, and X produces 0.

    ZEROED converts one unit of q into one of X, and its SUPPLY goes on
    offering 7 of q throughout: the output stops because the rate is 0, not
    because the supply dried up. A continuous flow carries no separate
    availability gate (R19).

    The DRAW stops with it (R-13). This test used to assert the opposite --
    ``zeroed_q`` holding at 7 under the heading "the input never stopped" --
    which recorded the defect rather than the property: the draw is sized by
    ``rule_scale`` from what the inputs allow, before ``get_effective_rate`` is
    read, so a dead converter went on consuming at the nominal rate for the
    whole mission. SUPPLY is an unstocked source, so nothing was depleted and
    the over-draw left no trace here at all -- which is exactly why
    :func:`test_a_dead_output_stops_draining_the_stock_behind_it` measures the
    same cut against a reservoir.
    """
    trace = the_run["trace"]

    before = at(trace, 1.0)
    assert before["rate"]["ZEROED"] == pytest.approx(0.0)
    assert before["X"]["ZEROED"] == pytest.approx(7.0), "it produced 7 up to the cut"

    stopped = after(trace, 1.0)
    assert stopped["X"]["ZEROED"] == pytest.approx(0.0)
    assert stopped["zeroed_q"] == pytest.approx(
        0.0
    ), "a converter producing nothing draws nothing"

    # ... and both stay at zero for the rest of the run.
    for entry in trace:
        if entry["time"] > 1.0:
            assert entry["X"]["ZEROED"] == pytest.approx(0.0)
            assert entry["zeroed_q"] == pytest.approx(0.0)

    # The supply is untouched: it can still produce 7, and it is the converter
    # that stopped asking.
    supply = the_run["system"].comp["SUPPLY"].flows_out["q"]
    assert supply.var_fed_default == pytest.approx(7.0)
    assert supply.get_effective_rate() == pytest.approx(1.0)


def test_a_dead_output_stops_draining_the_stock_behind_it(the_run):
    """R-13: the depletion an unstocked source cannot show.

    DRAINED converts one unit of ``q`` into one of ``X`` for a sink asking for
    2, so it draws 2 per unit time from a 100-unit reservoir. Its output is cut
    to 0 at t=1.

    Against ``9c5e647`` the reservoir went on being drawn at 2 per unit time
    for the whole horizon -- 100 down to 91 by t=4.5 -- while the converter
    delivered nothing: the draw was capped at ``scale x coefficient`` by the
    over-draw fix, but that scale is computed before the derating is read, so
    the cap had nothing to hand back. The stock now stops falling AT the cut
    and holds what the reaction justifies.
    """
    trace = the_run["trace"]

    alive = at(trace, DRAIN_CUT)
    assert alive["X"]["DRAINED"] == pytest.approx(
        DRAIN_RATE
    ), "it produced 2 up to the cut"
    assert alive["drained_q"] == pytest.approx(DRAIN_RATE)
    assert alive["reservoir"] == pytest.approx(
        DRAIN_STOCK - DRAIN_RATE * DRAIN_CUT, rel=0.05
    )

    for entry in trace:
        if entry["time"] <= DRAIN_CUT:
            continue

        assert entry["X"]["DRAINED"] == pytest.approx(0.0)
        assert entry["drained_q"] == pytest.approx(0.0, abs=1e-9)
        assert entry["reservoir_outflow"] == pytest.approx(0.0, abs=1e-9)

    # The stock is frozen at what the reaction consumed, whatever the horizon.
    final = trace[-1]
    assert final["time"] > DRAIN_CUT
    assert final["reservoir"] == pytest.approx(
        DRAIN_STOCK - DRAIN_RATE * DRAIN_CUT, rel=0.05
    )
    assert (
        final["reservoir"] > DRAIN_STOCK - DRAIN_RATE * final["time"]
    ), "the stock kept draining after the output died"


# ----------------------------------------------------------------------
# Order independence, and why one variable per mode
# ----------------------------------------------------------------------


def test_the_same_two_deratings_in_the_opposite_order_give_the_same_rate(the_run):
    """0.5 then 0.8, or 0.8 then 0.5: the effective rate is 0.5 either way.

    PLANT degrades at 0.5 first and PLANT_REV at 0.8 first. A minimum is
    order-independent by construction; a shared variable would not be.
    """
    trace = the_run["trace"]

    for date in (2.0, 2.5, 3.5):
        entry = at(trace, date)
        assert entry["rate"]["PLANT"] == pytest.approx(0.5)
        assert entry["rate"]["PLANT_REV"] == pytest.approx(0.5)


def test_two_modes_on_one_output_own_two_variables(the_run):
    """The same effect string declared twice writes two variables, not one.

    ``deg_a`` and ``deg_b`` both declare their effect against ``X``. Each owns a
    variable named from itself and the output, and both hold their own value at
    the same time -- which is what there is a minimum to take.
    """
    flow = the_run["system"].comp["PLANT"].flows_out["X"]

    assert set(flow.derating) == {"deg_a", "deg_b"}

    names = sorted(var.basename() for var in flow.derating.values())
    assert names == ["deg_a_derating_X", "deg_b_derating_X"]
    assert flow.derating_var_name("deg_a") == "deg_a_derating_X"

    # Two distinct variables, holding two distinct values at once.
    both = at(the_run["trace"], 2.0)
    assert both["derating"]["deg_a"] != both["derating"]["deg_b"]


def test_an_output_no_mode_derates_keeps_the_nominal_rate(the_run):
    """An output nothing derates has no variable at all and produces in full."""
    system = the_run["system"]

    assert system.comp["SUPPLY"].flows_out["q"].derating == {}
    assert system.comp["SUPPLY"].flows_out["q"].get_effective_rate() == pytest.approx(
        1.0
    )
    assert the_run["trace"][0]["rate"]["PLANT"] == pytest.approx(1.0)
    assert the_run["trace"][0]["X"]["PLANT"] == pytest.approx(10.0)


# ----------------------------------------------------------------------
# Propagation, release, and the declaration surface
# ----------------------------------------------------------------------


def test_a_derated_output_propagates_a_reduced_quantity_downstream(the_run):
    """The consumer receives what the derated producer delivers, not the nominal.

    PLANT_SINK asks for far more than PLANT can ever produce, so what it gets
    tracks the derating exactly.
    """
    trace = the_run["trace"]

    assert trace[0]["plant_sink"] == pytest.approx(10.0)
    assert after(trace, 1.0)["plant_sink"] == pytest.approx(5.0)
    assert after(trace, 4.0)["plant_sink"] == pytest.approx(8.0)

    # What the consumer reads is what the producer's flow carries.
    for entry in trace:
        assert entry["plant_sink"] == pytest.approx(entry["X"]["PLANT"])


def test_the_library_releases_a_derating_exactly_as_a_declaration_would(the_run):
    """A mode that derates on one state returns to nominal on the other.

    AUTO declares nothing on repair and EXPLICIT declares ``("X", 1.0)``; both
    cycle at 1 / 2 / 3 / 4 and their traces coincide. Without the release, a
    repaired mode would pin its own degradation for the rest of the sequence --
    a derating variable has no per-step reset, precisely because no reset value
    is neutral for a minimum.
    """
    trace = the_run["trace"]

    for entry in trace:
        assert entry["rate"]["AUTO"] == pytest.approx(entry["rate"]["EXPLICIT"])
        assert entry["X"]["AUTO"] == pytest.approx(entry["X"]["EXPLICIT"])

    assert at(trace, 1.0)["rate"]["AUTO"] == pytest.approx(0.25)
    assert at(trace, 2.0)["rate"]["AUTO"] == pytest.approx(1.0)
    assert after(trace, 2.0)["X"]["AUTO"] == pytest.approx(10.0)


def test_the_exponential_helper_allocates_the_same_per_mode_variable(the_run):
    """``add_exp_failure_mode`` retargets exactly like the delay one.

    Both funnel through ``add_atm2states``, which is where the rewrite happens,
    so a derating is declared the same way whatever the occurrence law.
    """
    flow = the_run["system"].comp["WEARING"].flows_out["X"]

    assert set(flow.derating) == {"wear"}
    assert flow.derating["wear"].basename() == "wear_derating_X"

    # Nothing has fired: the variable sits at the nominal rate.
    assert flow.get_effective_rate() == pytest.approx(1.0)


def test_deratings_are_discoverable_from_the_mode_and_from_the_flow(the_run):
    """R18's naming is reachable from either end: the mode, or the output."""
    plant = the_run["system"].comp["PLANT"]

    assert set(plant.derating_vars_of("deg_a")) == {"deg_a_derating_X"}
    assert set(plant.derating_vars_of("deg_b")) == {"deg_b_derating_X"}
    assert plant.derating_vars_of("nothing_declared_this") == {}

    assert plant.match_continuous_outputs("X") == ["X"]
    assert plant.match_continuous_outputs("X_fed_out") == ["X"]
    assert plant.match_continuous_outputs("nope") == []


def test_derating_a_flow_that_carries_no_rate_is_refused(the_run):
    """Only a continuous output carries a rate (R19)."""
    signal = the_run["system"].comp["SIGNAL"]

    with pytest.raises(ValueError, match="not a continuous output flow"):
        signal.add_derating("stop", "b")

    with pytest.raises(ValueError, match="not a continuous output flow"):
        signal.add_derating("stop", "does_not_exist")


# ----------------------------------------------------------------------
# compute_effects_tuples: numeric values alongside the boolean form
# ----------------------------------------------------------------------


def test_effect_strings_carry_numeric_values(the_run):
    """``"X=0.5"`` carries 0.5, resolved against the continuous output."""
    plant = the_run["system"].comp["PLANT"]

    assert plant.compute_effects_tuples("X=0.5") == [("X", 0.5)]
    assert plant.compute_effects_tuples("X=0") == [("X", 0.0)]

    # A pattern matching no continuous output falls back to the variables, so a
    # numeric parameter stays reachable.
    assert plant.compute_effects_tuples("deg_a_ttf=7") == [("deg_a_ttf", 7.0)]

    with pytest.raises(ValueError, match="non-numeric value"):
        plant.compute_effects_tuples("X=broken")


def test_the_boolean_effect_form_is_unchanged(the_run):
    """``"f"`` and ``"!f"`` resolve to variable basenames exactly as in 1.x."""
    signal = the_run["system"].comp["SIGNAL"]

    assert set(signal.compute_effects_tuples("b_fed_available_out")) == {
        ("b_fed_available_out", True)
    }
    assert set(signal.compute_effects_tuples("!b_fed_available_out")) == {
        ("b_fed_available_out", False)
    }
    assert signal.compute_effects_tuples("") == []
    assert signal.compute_effects_tuples(None) == []

    # Several patterns at once, and every matching variable resolved.
    assert set(signal.compute_effects_tuples("!b_fed_out,!b_prod")) == {
        ("b_fed_out", False),
        ("b_prod", False),
        ("b_prod_available", False),
    }


# ----------------------------------------------------------------------
# 1.x parity: nothing above touched the boolean path
# ----------------------------------------------------------------------


def test_a_boolean_effect_on_a_discrete_flow_behaves_as_in_1_x(the_run):
    """The availability gate is clamped False, and the flow and its consumer follow.

    Same declaration shape as every ``test_comp_failure_*`` module: a
    ``(variable, False)`` effect on a discrete output's availability gate.
    """
    trace = the_run["trace"]

    assert trace[0]["signal_b"] is True
    assert trace[0]["lamp_lit"] is True

    stopped = at(trace, 1.0)
    assert stopped["signal_b"] is False, "a boolean effect applies at the jump"
    assert stopped["lamp_lit"] is False

    for entry in trace:
        if entry["time"] >= 1.0:
            assert entry["signal_b"] is False
            assert entry["lamp_lit"] is False


def test_a_discrete_component_declares_no_derating_at_all(the_run):
    """A purely discrete component is untouched: no variable, no registry."""
    signal = the_run["system"].comp["SIGNAL"]

    assert signal.flows_continuous_out == {}
    assert signal.derating_vars_of("stop") == {}
    assert signal.continuous_endpoint_names() == set()

    # The 1.x variables, and nothing else added beside them.
    basenames = {var.basename() for var in signal.variables()}
    assert not any("derating" in name for name in basenames)


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
