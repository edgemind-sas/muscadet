"""A constituent running out is a bound, crossed exactly and put back on zero.

The empty/full automaton of a capacity watches its TOTAL fill. On the input
side the rules draw each held flow on its own, so one constituent can run out
while the volume as a whole still holds plenty, and nothing watched that
crossing: the per-flow guard of ``serve_limit`` only saw the level at or under
zero at the following integration step, and nothing took the overshoot back.

Measured before the constituent watch landed, on a hopper holding 5 of ``a``
and 500 of ``b`` feeding a rule that draws both at 10: ``a`` settled at -0.05
and stayed there for the rest of the run, and ``b`` had lost the same 0.05 to a
production no reagent covered.
"""

import cod3s
import pytest

import muscadet
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

DEMAND = 10.0
A_INIT = 5.0
B_INIT = 500.0
A_RUNS_OUT_AT = A_INIT / DEMAND
TRICKLE = 3.0
HORIZON = 1.5
GRID = 0.1

# What the crossing search leaves before the reset map: dtCond (1e-3 by
# default) times the rate the level was falling at.
CROSSING_RESIDUE = 1e-3 * DEMAND

# The crossing search stops within a few dtCond of the date, off the grid:
# measured 0.71564 for a reagent running out at 0.71429.
CROSSING_DATE_TOLERANCE = 3e-3


class CdHopper(muscadet.ObjFlow):
    """Two reagents in one input volume, drawn one for one by a single rule."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_in(name="b")
        self.add_flow_continuous_out(name="x")
        self.add_capacity(
            name="hop",
            flows=["a", "b"],
            capacity=1000.0,
            side="in",
            content_init={"a": A_INIT, "b": B_INIT},
        )
        self.add_rules("mk", rules=[{"cons": {"a": 1, "b": 1}, "prod": {"x": 1}}])


def build(name, trickle=None):
    system = muscadet.System(name=name)
    system.add_component(name="H", cls="CdHopper")
    system.add_component(name="U", cls="ConsumerContinuous", flow="x", demand=DEMAND)
    system.connect_flow(source="H", target="U", flow_name="x")
    if trickle is not None:
        system.add_component(name="SRC", cls="SourceContinuous", flow="a", rate=trickle)
        system.connect_flow(source="SRC", target="H", flow_name="a")
    return system


def observe(system):
    """Walk the grid; report every stop and the constituent transitions."""
    capacity = system.comp["H"].capacities["hop"]
    fired = []
    capacity.constituent_automata["a"]._bkd.addSensitiveMethod(
        "cd_count_constituent_transitions",
        lambda: fired.append(system.currentTime()),
    )

    rows = []

    def snap(kind, at, transitions):
        rows.append(
            {
                "time": at,
                "a": capacity.get_quantity("a"),
                "b": capacity.get_quantity("b"),
                "level": capacity.var_qty_total.value(),
                "x": system.comp["U"].flows_in["x"].get_delivered(),
            }
        )

    try:
        system.isimu_start()
        t = 0.0
        while t < HORIZON - 1e-9:
            t += GRID
            system.isimu_step_to(t, on_stop=snap)
        system.isimu_stop()
    finally:
        system.deleteSys()
        cod3s.terminate_session()

    return {"rows": rows, "fired": fired}


@pytest.fixture(scope="module")
def the_run():
    """Every observation this module asserts on, each system deleted at once."""
    return {
        "dry": observe(build("CdDry")),
        "trickle": observe(build("CdTrickle", trickle=TRICKLE)),
    }


def test_a_constituent_never_reads_negative(the_run):
    for label in ("dry", "trickle"):
        for row in the_run[label]["rows"]:
            assert row["a"] >= 0.0, (label, row)


def test_the_crossing_is_located_not_stepped_over(the_run):
    """One transition, at the date the reagent runs out."""
    fired = the_run["dry"]["fired"]
    assert len(fired) == 1, fired
    assert fired[0] == pytest.approx(A_RUNS_OUT_AT, abs=CROSSING_DATE_TOLERANCE)


def test_production_stops_with_the_reagent(the_run):
    """``b`` is drawn for exactly as long as ``a`` lasted, within the residue
    of one crossing search."""
    last = the_run["dry"]["rows"][-1]
    assert last["a"] == 0.0
    assert last["x"] == 0.0
    assert last["b"] == pytest.approx(B_INIT - A_INIT, abs=CROSSING_RESIDUE)


def test_the_published_level_follows_the_reset(the_run):
    """The total is an ODE variable of its own, and the one a measurement link
    exports: lifting a constituent must not leave it behind."""
    for label in ("dry", "trickle"):
        for row in the_run[label]["rows"]:
            assert row["level"] == pytest.approx(row["a"] + row["b"], abs=1e-4), (
                label,
                row,
            )


def test_a_depleted_constituent_passes_on_what_arrives(the_run):
    """Fed at 3 and drawn at 10, ``a`` runs out later and the rule then runs on
    the transit: no chatter around zero."""
    run = the_run["trickle"]
    runs_out_at = A_INIT / (DEMAND - TRICKLE)

    assert len(run["fired"]) == 1, run["fired"]
    assert run["fired"][0] == pytest.approx(runs_out_at, abs=CROSSING_DATE_TOLERANCE)

    last = run["rows"][-1]
    assert last["a"] == 0.0
    assert last["x"] == pytest.approx(TRICKLE)


def test_only_a_volume_the_rules_draw_per_flow_is_watched():
    """A single flow is the total the empty/full automaton already watches, and
    an output volume serves at its composition, which decays towards zero and
    never crosses it."""
    system = muscadet.System(name="CdStructure")
    try:
        system.add_component(
            name="T",
            cls="CapacityContinuous",
            flow="w",
            capacity=10.0,
            capacity_name="tk",
        )
        assert system.comp["T"].capacities["tk"].constituent_automata == {}
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_delete():
    """Each scenario deletes its own system; this closes the session."""
    cod3s.terminate_session()
