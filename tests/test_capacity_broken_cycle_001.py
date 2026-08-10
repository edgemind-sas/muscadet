"""A cycle a capacity breaks builds; an algebraic one is still refused (R-14).

``muscadet.ordering`` used to refuse **every** cycle its graph found, which
contradicted its own rationale -- "capacities and mode automata are the state
breaks". A tank wired to a recirculation pump and back is a loop whose closing
dependency crosses an ODE level, and it was refused at the first
``simulate()``. That is the shape of the heated-tank dynamic-reliability
benchmark (Aldemir; Marseguerra & Zio), so the refusal blocked the whole class
of problem.

What now decides
----------------
An edge ``A --q--> B`` exists because B reads what A exports. Dropping it lets B
run first, which is sound exactly when B's own exports do not algebraically
depend on what arrived on q -- and a capacity of B's is what makes that true,
because the volume is then the counterparty of the rules (KTD13):

* a capacity of B holding ``q`` on its **input** side: what arrives is
  integrated before any rule reads it;
* a capacity of B holding **every** continuous output of B on the ``out``
  side: what leaves is served from the volume rather than from what arrived.
  This is ``CapacityContinuous(ports="both")``, whose capacity ``side`` is
  ``"out"``, and it is what the recirculation loop rests on.

The break belongs to the **receiving** component. A capacity behind a
*producer's* output does not license its consumer to run first: the consumer
would use the stale value algebraically.

What is still refused
---------------------
A loop with nothing integrated on it -- two transformers whose rates depend on
each other. That distinction is the whole value of the change, so both
directions are measured here.

PyCATSHOO forbids more than one live system per process and ``simulate`` cannot
be called twice on one system, so each scenario is built, driven and deleted
before the next one starts; the fixture snapshots what each produced, and the
last is kept alive for the teardown.
"""

import cod3s
import muscadet
import pytest

from muscadet import ordering

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
    TransformerContinuous,
)

#: The tank the loop turns around.
CBC_VOLUME = 100.0
CBC_CONTENT = 50.0

#: What circulates. The demand of a conserving loop is a unit-gain fixpoint --
#: the pump asks for what the tank asks for and the tank asks for what the pump
#: asks for -- so it holds whatever it is seeded with, and the tank's declared
#: input demand is that seed. See ``test_the_demand_of_a_conserving_loop_holds``.
CBC_RATE = 5.0

#: Horizon the interactive sessions run to.
CBC_HORIZON = 5.0

#: Relative slack on a level integrated over the horizon.
CBC_TOL = 0.01


def add_clock(comp, dates):
    """Give an interactive session dates it can always step to."""
    for date in dates:
        comp.add_atm2states(
            name=f"clock_{str(date).replace('.', '_')}",
            st1="before",
            st2="after",
            occ_law_12={"cls": "delay", "time": date},
            cond_occ_21=False,
        )


def build_recirculation(name):
    """The blocked topology, verbatim: a two-sided tank and a pump around it."""
    system = muscadet.System(name=name)

    system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        ports="both",
        flow="q",
        capacity=CBC_VOLUME,
        capacity_name="tank",
        content_init={"q": CBC_CONTENT},
        demand=CBC_RATE,
    )
    system.add_component(
        name="PUMP",
        cls="TransformerContinuous",
        flows_in=["q"],
        flows_out=["q"],
        rules=[dict(name="recirculate", cons={"q": 1.0}, prod={"q": 1.0})],
    )

    system.connect_flow(source="TANK", target="PUMP", flow_name="q")
    system.connect_flow(source="PUMP", target="TANK", flow_name="q")

    return system


# ----------------------------------------------------------------------
# The scenarios
# ----------------------------------------------------------------------


def run_recirculation_scenario(obs):
    """TANK.q_out -> PUMP.q_in, PUMP.q_out -> TANK.q_in, and it runs."""
    system = build_recirculation("CapacityBrokenCycle")

    order = ordering.compute_equation_order(system)

    obs["order"] = order
    obs["production_order"] = list(order.production_order)
    obs["demand_order"] = list(order.demand_order)
    obs["torn"] = [str(cnct) for cnct in order.torn]
    obs["breaks_into_tank"] = ordering.capacity_breaks_inbound(system.comp["TANK"], "q")
    obs["breaks_into_pump"] = ordering.capacity_breaks_inbound(system.comp["PUMP"], "q")

    add_clock(system.comp["PUMP"], (1.0, 2.0, 3.0, 4.0, 5.0))

    system.isimu_start()

    def snap():
        tank = system.comp["TANK"].capacities["tank"]

        return {
            "time": system.currentTime(),
            "level": tank.get_quantity("q"),
            "inflow": tank.get_inflow("q"),
            "outflow": tank.get_outflow("q"),
            "tank_out": system.comp["TANK"].flows_out["q"].var_fed.value(),
            "pump_out": system.comp["PUMP"].flows_out["q"].var_fed.value(),
            "pump_demand": system.comp["PUMP"].flows_in["q"].var_demand.value(),
            "tank_demand": system.comp["TANK"].flows_in["q"].var_demand.value(),
        }

    trace = [snap()]
    for _ in range(30):
        if system.currentTime() >= CBC_HORIZON:
            break
        system.isimu_step_forward()
        trace.append(snap())

    obs["trace"] = trace

    system.isimu_stop()
    system.deleteSys()


def run_algebraic_loop_scenario(obs):
    """Two transformers whose rates depend on each other, and nothing between."""
    system = muscadet.System(name="CapacityBrokenCycleAlgebraic")

    for name in ("ALG_A", "ALG_B"):
        system.add_component(
            name=name,
            cls="TransformerContinuous",
            flows_in=["q"],
            flows_out=["q"],
            rules=[dict(name="pass", cons={"q": 1.0}, prod={"q": 1.0})],
        )

    system.connect_flow(source="ALG_A", target="ALG_B", flow_name="q")
    system.connect_flow(source="ALG_B", target="ALG_A", flow_name="q")

    obs["algebraic_error"] = None
    try:
        system.simulate(
            {"nb_runs": 1, "schedule": [{"start": 0, "end": 1, "nvalues": 2}]}
        )
    except ordering.ContinuousFlowCycleError as err:
        obs["algebraic_error"] = err

    obs["algebraic_torn"] = list(
        ordering.build_continuous_flow_graph(system).state_broken_connections
    )

    system.deleteSys()


def run_half_broken_scenario(obs):
    """A loop through a transformer that buffers ONE of its two outputs.

    The other output carries the arriving quantity straight on, so the volume
    does not stand between what the component receives and what it exports:
    requiring EVERY continuous output to be buffered is what keeps this refused.
    """
    system = muscadet.System(name="CapacityBrokenCycleHalf")

    system.add_component(
        name="HALF",
        cls="TransformerContinuous",
        flows_in=["q"],
        flows_out=["p", "r"],
        rules=[dict(name="split", cons={"q": 1.0}, prod={"p": 1.0, "r": 1.0})],
    )
    system.comp["HALF"].add_capacity(
        name="buffered_p", flow="p", capacity=CBC_VOLUME, side="out"
    )
    system.add_component(
        name="BACK",
        cls="TransformerContinuous",
        flows_in=["r"],
        flows_out=["q"],
        rules=[dict(name="back", cons={"r": 1.0}, prod={"q": 1.0})],
    )

    system.connect_flow(source="HALF", target="BACK", flow_name="r")
    system.connect_flow(source="BACK", target="HALF", flow_name="q")

    graph = ordering.build_continuous_flow_graph(system)

    obs["half_breaks"] = ordering.capacity_breaks_inbound(system.comp["HALF"], "q")
    obs["half_torn"] = list(graph.state_broken_connections)
    obs["half_error"] = None

    try:
        ordering.compute_equation_order(system)
    except ordering.ContinuousFlowCycleError as err:
        obs["half_error"] = err

    system.deleteSys()


def run_acyclic_parity_scenario(obs):
    """An acyclic chain through a buffer derives exactly the order it always did.

    The tear only happens once the full edge set turns out to be cyclic, so a
    model that builds today must be untouched -- including one declared in an
    order that the edges, and nothing else, put right.
    """
    system = muscadet.System(name="CapacityBrokenCycleAcyclic")

    # Declared consumer-first on purpose: only the EDGES can order this.
    system.add_component(name="PAR_C", cls="ConsumerContinuous", flow="q", demand=1.0)
    system.add_component(
        name="PAR_TANK",
        cls="CapacityContinuous",
        ports="both",
        flow="q",
        capacity=CBC_VOLUME,
        capacity_name="tank",
        content_init={"q": CBC_CONTENT},
    )
    system.add_component(name="PAR_S", cls="SourceContinuous", flow="q", rate=2.0)

    system.connect_flow(source="PAR_S", target="PAR_TANK", flow_name="q")
    system.connect_flow(source="PAR_TANK", target="PAR_C", flow_name="q")

    order = ordering.compute_equation_order(system)

    obs["acyclic_production"] = list(order.production_order)
    obs["acyclic_demand"] = list(order.demand_order)
    obs["acyclic_torn"] = list(order.torn)
    obs["acyclic_state_broken"] = [str(c) for c in order.graph.state_broken_connections]

    # Kept alive for the teardown test, per the module convention.
    obs["system"] = system


@pytest.fixture(scope="module")
def the_run():
    """Drive every scenario in turn, snapshotting what each produced."""
    obs = {}

    run_recirculation_scenario(obs)
    run_algebraic_loop_scenario(obs)
    run_half_broken_scenario(obs)
    run_acyclic_parity_scenario(obs)

    return obs


# ----------------------------------------------------------------------
# The loop that now builds
# ----------------------------------------------------------------------


def test_a_tank_and_its_recirculation_pump_build(the_run):
    """The blocked topology, verbatim: it derives an order instead of raising.

    Against ``9c5e647`` this raised ``ContinuousFlowCycleError: TANK -> PUMP ->
    TANK closes a loop`` at the first ``simulate()``.
    """
    assert the_run["production_order"] == ["TANK", "PUMP"]
    assert the_run["demand_order"] == ["PUMP", "TANK"]


def test_the_break_is_the_tank_s_own_volume_and_belongs_to_the_receiver(the_run):
    """Which edge is torn, and why that one.

    ``CapacityContinuous(ports="both")`` holds its volume on the ``out`` side,
    so what the tank exports is served from the level and not from what the
    pump just returned: the edge INTO the tank is the one an integrated state
    breaks. The edge into the pump is not -- the pump buffers nothing and uses
    what it receives algebraically -- which is what keeps a tear minimal instead
    of dissolving the check.
    """
    assert the_run["breaks_into_tank"] is True
    assert the_run["breaks_into_pump"] is False

    assert the_run["torn"] == ["PUMP.q_out -> TANK.q_in"]


def test_the_loop_circulates_at_a_steady_rate(the_run):
    """A sensible trajectory: 5 goes round and round, and the level holds.

    What leaves the tank is what comes back into it, so a conserving loop that
    nothing draws on keeps its content: the level must not creep in either
    direction over the horizon.
    """
    trace = [entry for entry in the_run["trace"] if entry["time"] > 0.0]
    assert trace, "the loop never ran"

    for entry in trace:
        assert entry["tank_out"] == pytest.approx(CBC_RATE, rel=1e-6)
        assert entry["pump_out"] == pytest.approx(CBC_RATE, rel=1e-6)
        assert entry["inflow"] == pytest.approx(entry["outflow"], rel=1e-6)
        assert entry["level"] == pytest.approx(CBC_CONTENT, rel=CBC_TOL)

    assert trace[-1]["time"] == pytest.approx(CBC_HORIZON, abs=0.05)


def test_the_demand_of_a_conserving_loop_holds(the_run):
    """The demand around a mass-conserving loop is a unit-gain fixpoint.

    The pump asks for what its output is asked for -- the tank's input demand --
    and the tank asks its producers for what its own output is asked for, which
    is the pump's demand. Neither adds anything, so the pair holds whatever it
    starts at, and the tank's declared input demand is what seeds it.

    Recorded because it is the load-bearing limit of the tear: a capacity does
    NOT break the demand sweep (``Capacity.demand_claim`` passes a demand
    straight through a volume, by design), so the demand read across the torn
    edge is one evaluation old. That costs nothing while the loop is settled, as
    here; a claim injected INSIDE the loop -- a ``fill_rate``, or a consumer
    hanging off the tank -- makes it drift by that claim per evaluation instead.
    Declare the claim outside the loop.
    """
    for entry in the_run["trace"]:
        if entry["time"] <= 0.0:
            continue

        assert entry["pump_demand"] == pytest.approx(CBC_RATE)
        assert entry["tank_demand"] == pytest.approx(CBC_RATE)


# ----------------------------------------------------------------------
# What is still refused
# ----------------------------------------------------------------------


def test_a_purely_algebraic_loop_is_still_refused(the_run):
    """Two rates depending on each other with nothing integrated between them."""
    error = the_run["algebraic_error"]

    assert error is not None, "an algebraic loop must not build"
    assert isinstance(error, ordering.ContinuousFlowCycleError)
    assert isinstance(error, ValueError)

    message = str(error)
    assert "must be acyclic" in message
    assert "ALG_A.q_out -> ALG_B.q_in" in message
    assert "ALG_B.q_out -> ALG_A.q_in" in message

    # Nothing was droppable, so the refusal is the FIRST sort's, unchanged.
    assert the_run["algebraic_torn"] == []


def test_a_loop_through_a_half_buffered_component_is_still_refused(the_run):
    """One buffered output is not enough: the other passes the input straight on.

    ``HALF`` splits its input into ``p`` -- held in a volume -- and ``r``, which
    carries the arriving quantity onward with nothing integrated in between. The
    loop closes through ``r``, so the level breaks nothing and the model is
    refused.
    """
    assert the_run["half_breaks"] is False
    assert the_run["half_torn"] == []

    error = the_run["half_error"]
    assert (
        error is not None
    ), "a loop closing through an unbuffered output must not build"
    assert "HALF.r_out -> BACK.r_in" in str(error)
    assert "BACK.q_out -> HALF.q_in" in str(error)


# ----------------------------------------------------------------------
# Parity: nothing acyclic moved
# ----------------------------------------------------------------------


def test_an_acyclic_model_derives_the_order_it_always_did(the_run):
    """The full edge set is sorted first, so a buffered chain is untouched.

    Declared consumer-first, the chain is ordered by its edges alone -- source,
    tank, consumer. Were the state-broken edges dropped unconditionally, the
    tank would lose the constraint putting its source first and would be
    evaluated on a stale supply.
    """
    assert the_run["acyclic_production"] == ["PAR_S", "PAR_TANK", "PAR_C"]
    assert the_run["acyclic_demand"] == ["PAR_C", "PAR_TANK", "PAR_S"]

    # The edge IS recognised as state-broken ...
    assert the_run["acyclic_state_broken"] == ["PAR_S.q_out -> PAR_TANK.q_in"]
    # ... and nothing was torn, because nothing needed to be.
    assert the_run["acyclic_torn"] == []


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
