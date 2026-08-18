"""How a failure-mode effect pattern reaches the thing it names (R-14).

Three defects of one family, all of them producing a model that builds, runs to
completion and reports plausible-but-wrong numbers with no diagnostic.

1. **A continuous match used to swallow the discrete ones.** The resolution
   diverted a whole pattern to the derating path as soon as it matched ONE
   continuous output, so ``failure_effects=[(".*", False)]`` on a plant
   declaring an ``H2`` rate beside an ``H2_status`` signal cut the rate and left
   the signal telling every downstream consumer that the plant was alive. The
   STANDALONE path (``ObjFailureMode.resolve_effects_on``) always branched per
   flow and got this right; the two now share one resolution.

2. **The match used to be unanchored.** ``[("H2", 0.5)]`` on a component that
   also declares ``H2O`` derated the water output too, and a downstream mass
   balance reported half the water it should. The standalone path anchors with
   ``^...$``; both now do.

3. **An effect on a solver-owned variable used to be a silent no-op.** The
   filter that (correctly) protects ``{flow}_fed_out`` listed only the flow
   endpoints, so an effect naming a capacity's ``{c}_inflow_{f}`` /
   ``{c}_outflow_{f}``, its levels, or an output's ``{flow}_out_profile`` was
   accepted, wired, and then overwritten at every integration step. The
   modeller had every reason to think the filter covered them. They are now
   refused at declaration, naming what to clamp instead.

Reading the trace
-----------------
Everything runs in ONE interactive session -- PyCATSHOO forbids more than one
live system per process -- and every stop is snapshotted. A stop reports the
left limit, so a rate is read AT the stop it changed on and a delivery strictly
after it.
"""

import gc

import pytest

import cod3s
import muscadet

#: Horizon the interactive session runs to.
ER_HORIZON = 2.5

#: Stops added so a delivery can be read strictly after the mode fires.
ER_CLOCK_DATES = (0.5, 1.5, 2.5)

#: When the two modes under test fire.
ER_FAIL_DATE = 1.0

#: A repair that never comes back within the horizon.
ER_NEVER = 1e6

#: What the plant produces on each of its two continuous outputs.
ER_H2_RATE = 5.0
ER_H2O_RATE = 3.0

#: What the sinks ask for: more than the plant can ever deliver, so every
#: delivery below is the production and not the demand.
ER_SINK_DEMAND = 1e3

#: What the sibling's mode leaves of ``H2``.
ER_HALF = 0.5

#: Volume and stock of the tank the refused effects are probed against.
ER_TANK_VOLUME = 100.0
ER_TANK_STOCK = 10.0


# ----------------------------------------------------------------------
# Components -- prefixed, since component classes resolve by name globally
# ----------------------------------------------------------------------


class EffResPlant(muscadet.ObjFlow):
    """Two continuous outputs and a discrete one, on the same component.

    The shape the first two defects need: ``H2`` and ``H2O`` so an unanchored
    pattern can reach the wrong one, and ``H2_status`` so a wildcard has a
    discrete output to skip.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="H2", var_fed_default=ER_H2_RATE)
        self.add_flow_continuous_out(name="H2O", var_fed_default=ER_H2O_RATE)
        self.add_flow_out(name="H2_status", var_prod_default=True)


class EffResSink(muscadet.ObjFlow):
    """A continuous consumer asking for more than it can ever be served."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("flow", "H2"), var_demand_default=ER_SINK_DEMAND
        )


class EffResLamp(muscadet.ObjFlow):
    """A discrete consumer lit by the status signal it receives."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="H2_status", logic="and")
        self.add_flow_out(name="lit", var_prod_cond=["H2_status"])


class EffResTank(muscadet.ObjFlow):
    """A buffered pass-through publishing its own level, on a profiled output.

    Carries every kind of variable the sweeps own, on one component: a
    capacity's quantities, fills and transit rates, a republished reading, and
    the ``{flow}_out_profile`` an output declaring a time profile publishes. It
    also publishes one reading with NO source, which stays a plain writable
    variable and must therefore stay clampable.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="H2")
        self.add_flow_continuous_out(
            name="H2",
            profile=muscadet.SinusoidalProfile(period=24.0, offset=1.0),
        )
        self.add_capacity(
            name="cuve",
            flow="H2",
            capacity=ER_TANK_VOLUME,
            side="out",
            content_init={"H2": ER_TANK_STOCK},
        )
        self.add_measurement_out(name="reported", source="cuve")
        self.add_measurement_out(name="freehand")


class EffResClock(muscadet.ObjFlow):
    """Nothing but dates the interactive session can stop at."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


# ----------------------------------------------------------------------
# Building the one system every scenario lives in
# ----------------------------------------------------------------------


def er_add_clock(comp, date):
    """Give the session a stop at ``date`` it can always step to."""
    comp.add_atm2states(
        name=f"clock_{str(date).replace('.', '_')}",
        st1="before",
        st2="after",
        occ_law_12={"cls": "delay", "time": date},
        cond_occ_21=False,
    )


def er_build_system():
    system = muscadet.System(name="EffectResolutionSys")

    # -- Defect 1: a wildcard over a component mixing both families. The
    #    continuous outputs go to their derating variables, the discrete one to
    #    its availability gate, and the lamp downstream goes out.
    system.add_component(name="MIXED", cls="EffResPlant")
    system.add_component(name="MIXED_H2", cls="EffResSink", flow="H2")
    system.add_component(name="LAMP", cls="EffResLamp")
    system.connect_flow(source="MIXED", target="MIXED_H2", flow_name="H2")
    system.connect_flow(source="MIXED", target="LAMP", flow_name="H2_status")
    system.comp["MIXED"].add_delay_failure_mode(
        name="dead",
        failure_time=ER_FAIL_DATE,
        failure_effects=[(".*", False)],
        repair_time=ER_NEVER,
    )

    # -- Defect 2: the sibling output must not follow. ``H2`` names ``H2`` and
    #    nothing else, however much of it ``H2O`` starts with.
    system.add_component(name="SIBLING", cls="EffResPlant")
    system.add_component(name="SIBLING_H2", cls="EffResSink", flow="H2")
    system.add_component(name="SIBLING_H2O", cls="EffResSink", flow="H2O")
    system.connect_flow(source="SIBLING", target="SIBLING_H2", flow_name="H2")
    system.connect_flow(source="SIBLING", target="SIBLING_H2O", flow_name="H2O")
    system.comp["SIBLING"].add_delay_failure_mode(
        name="half",
        failure_time=ER_FAIL_DATE,
        failure_effects=[("H2", ER_HALF)],
        repair_time=ER_NEVER,
    )

    # -- Defect 3: the variables the sweeps own, all of them on one component.
    system.add_component(name="TANK", cls="EffResTank")

    system.add_component(name="CLOCK", cls="EffResClock")
    for date in ER_CLOCK_DATES:
        er_add_clock(system.comp["CLOCK"], date)

    return system


def er_snapshot(system):
    """Rates, deliveries and the discrete status, at one stop."""

    def rate(comp, flow):
        return system.comp[comp].flows_out[flow].get_effective_rate()

    def delivered(comp, flow):
        return system.comp[comp].flows_in[flow].var_fed.value()

    return {
        "time": system.currentTime(),
        "mixed_h2_rate": rate("MIXED", "H2"),
        "mixed_h2o_rate": rate("MIXED", "H2O"),
        "mixed_h2": delivered("MIXED_H2", "H2"),
        "status_available": system.comp["MIXED"]
        .flows_out["H2_status"]
        .var_fed_available.value(),
        "status": system.comp["MIXED"].flows_out["H2_status"].var_fed.value(),
        "lit": system.comp["LAMP"].flows_out["lit"].var_fed.value(),
        "sibling_h2_rate": rate("SIBLING", "H2"),
        "sibling_h2o_rate": rate("SIBLING", "H2O"),
        "sibling_h2": delivered("SIBLING_H2", "H2"),
        "sibling_h2o": delivered("SIBLING_H2O", "H2O"),
    }


def er_probe(comp, pattern):
    """What ``resolve_mode_effects`` answers, or the error it raises.

    ``resolve_mode_effects`` is the single point every mode declared on a
    component funnels through -- ``add_exp_failure_mode`` and
    ``add_delay_failure_mode`` both reach it via ``add_atm2states`` -- so
    probing it directly exercises the declaration without leaving a
    half-built automaton behind on a component the run then walks over.
    """
    try:
        resolved = comp.resolve_mode_effects("probe", [(pattern, 0.0)])
    except ValueError as error:
        return error

    return sorted(var.basename() for var, _ in resolved)


@pytest.fixture(scope="module")
def the_run():
    """Drive the system to the horizon, recording every stop."""
    system = er_build_system()

    tank = system.comp["TANK"]

    # Probed BEFORE the run: a malformed declaration is refused at declaration
    # time, which is the whole point of refusing it at all.
    probes = {
        "outflow": er_probe(tank, "cuve_outflow_H2"),
        "inflow": er_probe(tank, "cuve_inflow_H2"),
        "qty": er_probe(tank, "cuve_qty_H2"),
        "qty_total": er_probe(tank, "cuve_qty"),
        "fill": er_probe(tank, "cuve_fill_H2"),
        "demand": er_probe(tank, "H2_demand_out"),
        "reported_level": er_probe(tank, "^reported_level$"),
        "profile": er_probe(tank, "H2_out_profile"),
        # ... and the endpoints that DO work, which must keep working.
        "gain": er_probe(tank, "^reported_level_gain$"),
        "freehand": er_probe(tank, "^freehand_level$"),
        "out_rate": er_probe(tank, "^H2_out_rate$"),
        "flow": er_probe(tank, "H2"),
        # ... and one wildcard over a component mixing the two families. Read
        # on the sibling: what a mode of the model's own already put on it is
        # a derating variable at nominal, which changes no answer here.
        "wildcard": er_probe(system.comp["SIBLING"], ".*"),
    }

    # The public API refuses it too, and says so before anything is wired.
    try:
        tank.add_delay_failure_mode(
            name="valve_stuck",
            failure_time=1.0,
            failure_effects=[("cuve_outflow_H2", 0.0)],
            repair_time=ER_NEVER,
        )
        probes["declared"] = None
    except ValueError as error:
        probes["declared"] = error

    system.isimu_start()

    trace = [er_snapshot(system)]
    for _ in range(80):
        system.isimu_step_forward()
        trace.append(er_snapshot(system))
        if system.currentTime() >= ER_HORIZON:
            break

    system.isimu_stop()

    return {"system": system, "trace": trace, "probes": probes}


def er_at(trace, date):
    """The first stop at or after ``date``."""
    return next(stop for stop in trace if stop["time"] >= date - 1e-9)


def er_after(trace, date):
    """The first stop strictly after ``date``."""
    return next(stop for stop in trace if stop["time"] > date + 1e-9)


# ----------------------------------------------------------------------
# Defect 1 -- a continuous match no longer swallows the discrete outputs
# ----------------------------------------------------------------------


def test_everything_runs_at_nominal_before_the_modes_fire(the_run):
    """Nothing is degraded until something degrades it."""
    start = the_run["trace"][0]

    assert start["mixed_h2_rate"] == pytest.approx(1.0)
    assert start["mixed_h2o_rate"] == pytest.approx(1.0)
    assert start["status"] is True
    assert start["lit"] is True

    settled = er_at(the_run["trace"], 0.5)
    assert settled["mixed_h2"] == pytest.approx(ER_H2_RATE)


def test_a_wildcard_reaches_the_discrete_output_beside_the_continuous_ones(the_run):
    """The regression: a plant declared totally lost must say so downstream.

    ``failure_effects=[(".*", False)]`` used to resolve to the two derating
    variables ALONE -- the pattern was diverted to the continuous path by its
    first continuous match -- so the rate went to zero while ``H2_status``
    stayed available and fed, and the lamp downstream stayed lit.
    """
    fired = er_at(the_run["trace"], ER_FAIL_DATE)

    # The continuous half, which already worked
    assert fired["mixed_h2_rate"] == pytest.approx(0.0)
    assert fired["mixed_h2o_rate"] == pytest.approx(0.0)

    # ... and the discrete half, which did not
    assert fired["status_available"] is False
    assert fired["status"] is False
    assert fired["lit"] is False

    # Nothing is delivered afterwards, on either family.
    settled = er_after(the_run["trace"], ER_FAIL_DATE)
    assert settled["mixed_h2"] == pytest.approx(0.0)
    assert settled["status"] is False
    assert settled["lit"] is False


def test_the_two_paths_resolve_a_wildcard_alike(the_run):
    """The standalone path was right; the component path now agrees with it.

    Both branch per flow: a continuous output to a derating variable owned by
    the declaring mode, a discrete one to its availability gate. It used to
    return the two derating variables and nothing else.
    """
    assert the_run["probes"]["wildcard"] == [
        "H2_status_fed_available_out",
        "probe_derating_H2",
        "probe_derating_H2O",
    ]


# ----------------------------------------------------------------------
# Defect 2 -- the match is anchored, so a sibling output is left alone
# ----------------------------------------------------------------------


def test_deratings_do_not_spill_onto_a_sibling_output(the_run):
    """The regression: ``("H2", 0.5)`` must not touch ``H2O``.

    Unanchored, ``re.search("H2", "H2O")`` matches, so the water output ran at
    half rate and a downstream mass balance reported half the water it should.
    """
    fired = er_at(the_run["trace"], ER_FAIL_DATE)

    assert fired["sibling_h2_rate"] == pytest.approx(ER_HALF)
    assert fired["sibling_h2o_rate"] == pytest.approx(1.0)

    settled = er_after(the_run["trace"], ER_FAIL_DATE)
    assert settled["sibling_h2"] == pytest.approx(ER_H2_RATE * ER_HALF)
    assert settled["sibling_h2o"] == pytest.approx(ER_H2O_RATE)


def test_the_anchoring_is_the_one_the_standalone_path_uses(the_run):
    """``match_continuous_outputs`` names one output, not everything sharing a
    prefix -- and a wildcard still names them all."""
    sibling = the_run["system"].comp["SIBLING"]

    assert sibling.match_continuous_outputs("H2") == ["H2"]
    assert sibling.match_continuous_outputs("H2O") == ["H2O"]
    assert sibling.match_continuous_outputs("H2.*") == ["H2", "H2O"]
    assert sibling.match_continuous_outputs(".*") == ["H2", "H2O"]

    # The 1.x spelling of an effect on an output still designates it.
    assert sibling.match_continuous_outputs("H2_fed_out") == ["H2"]


# ----------------------------------------------------------------------
# Defect 3 -- an effect the solver would overwrite is refused, not ignored
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "probe, named",
    [
        ("outflow", "cuve_outflow_H2"),
        ("inflow", "cuve_inflow_H2"),
        ("qty", "cuve_qty_H2"),
        ("qty_total", "cuve_qty"),
        ("fill", "cuve_fill_H2"),
        ("demand", "H2_demand_out"),
        ("reported_level", "reported_level"),
        ("profile", "H2_out_profile"),
    ],
)
def test_an_effect_on_a_solver_owned_variable_is_refused(the_run, probe, named):
    """Silently accepting an effect that cannot work is the one outcome that is
    not acceptable.

    Each of these used to be accepted, wired, and then overwritten by the
    sweeps at every integration step: a valve declared stuck never blocked, and
    the availability figures reported were those of a plant whose modelled
    failure never happened.
    """
    error = the_run["probes"][probe]

    assert isinstance(error, ValueError), error
    assert named in str(error)
    assert "silent no-op" in str(error)


def test_the_refusal_names_what_to_clamp_instead(the_run):
    """A refusal that does not say what to write instead only moves the problem."""
    assert "rule guard" in str(the_run["probes"]["outflow"])
    assert "derate the output it buffers" in str(the_run["probes"]["outflow"])
    assert "level_gain" in str(the_run["probes"]["reported_level"])
    assert "derate the output itself" in str(the_run["probes"]["profile"])


def test_the_public_declaration_is_refused_too(the_run):
    """``add_delay_failure_mode`` funnels through the same resolution."""
    error = the_run["probes"]["declared"]

    assert isinstance(error, ValueError), error
    assert "cuve_outflow_H2" in str(error)


def test_the_endpoints_that_work_are_left_reachable(the_run):
    """The refusal is about variables the solver rewrites, and only those.

    ``{flow}_out_rate`` and ``{m}_level_gain`` are the public endpoints a mode
    declared outside muscadet clamps (KD10, R37), and a publication declaring no
    source is a plain writable variable a model may drive. All three stay
    resolvable, or the refusal would have closed the door it exists to point at.
    """
    probes = the_run["probes"]

    assert probes["gain"] == ["reported_level_gain"]
    assert probes["freehand"] == ["freehand_level"]
    assert probes["out_rate"] == ["H2_out_rate"]

    # ... and naming the flow itself still routes to the per-mode derating.
    assert probes["flow"] == ["probe_derating_H2"]


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
    gc.collect()
