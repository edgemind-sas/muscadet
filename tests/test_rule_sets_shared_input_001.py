"""Two rule sets on one input share it; they do not each get the whole of it.

A conservation breach, and the most serious of its family.

``evaluate_production`` called ``get_input_available(flow_name)`` **once per
rule set**, with no shared budget: each set was told the whole of what the
input could serve. Measured at ``399730d`` on the component below -- two rule
sets both consuming ``a``, fed at 1.0 -- ``a_fed_in`` reported **1.0** with
``x_fed_out = 1.0`` and ``y_fed_out = 1.0``: 2.0 produced out of 1.0 received.

``release_unused_supply`` cannot catch it. That pass hands back what was
delivered and NOT drawn; here the recorded consumption is above the delivery, so
there is nothing to release. Behind an input capacity the same path writes an
outflow of 2.0 against an inflow of 1.0 and drains the volume of a quantity
nobody delivered -- which is where the breach turns into lost matter rather than
a discrepancy on a source.

The budget is now shared and spent in **declaration order**: the first set
declared is the first served. Deterministic and inspectable, which no
proportional split of a contested reagent would be without a policy of its own
-- and the same order the sets are already evaluated in.

Conservation is asserted per component and per stop: what an input draws equals
what the rules consume plus what a capacity stores.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, driven and deleted before the next one starts; the fixture snapshots what
each produced and the last is kept alive for the teardown.
"""

import cod3s
import muscadet
import pytest

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    ConsumerContinuous,
    SourceContinuous,
)

#: What the shared reagent is supplied at.
RSS_SUPPLY = 1.0
#: What each product's consumer asks for: far more than the supply allows, so
#: nothing but the shared input can limit the sets.
RSS_DEMAND = 100.0
#: Coefficients of the second set, chosen so that "half the supply" and "the
#: coefficients" cannot be confused for one another.
RSS_SECOND_COEFF = 2.0
RSS_HORIZON = 1.0


class RssTwoSets(muscadet.ObjFlow):
    """Two rule sets, both drawing on ``a``, each with its own product."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_out(name="x")
        self.add_flow_continuous_out(name="y")
        self.add_rules(name="rx", rules=[dict(cons={"a": 1.0}, prod={"x": 1.0})])
        self.add_rules(name="ry", rules=[dict(cons={"a": 1.0}, prod={"y": 1.0})])


class RssPartialSets(muscadet.ObjFlow):
    """The first set cannot use the whole supply; the second gets the rest.

    ``rx`` is limited by its OWN second reagent ``e``, so it leaves part of
    ``a`` behind -- and what it leaves must reach ``ry`` rather than being
    reserved or lost.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_in(name="e")
        self.add_flow_continuous_out(name="x")
        self.add_flow_continuous_out(name="y")
        self.add_rules(
            name="rx", rules=[dict(cons={"a": 1.0, "e": 1.0}, prod={"x": 1.0})]
        )
        self.add_rules(
            name="ry",
            rules=[dict(cons={"a": RSS_SECOND_COEFF}, prod={"y": 1.0})],
        )


class RssBufferedSets(muscadet.ObjFlow):
    """The same two sets, behind an input capacity buffering the shared reagent.

    This is where the breach became lost matter: the outflow the rules write on
    the volume was twice the inflow its supplier delivered.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="a")
        self.add_flow_continuous_out(name="x")
        self.add_flow_continuous_out(name="y")
        self.add_capacity(name="buf", flow="a", capacity=50.0, side="in")
        self.add_rules(name="rx", rules=[dict(cons={"a": 1.0}, prod={"x": 1.0})])
        self.add_rules(name="ry", rules=[dict(cons={"a": 1.0}, prod={"y": 1.0})])


class RssClock(muscadet.ObjFlow):
    """Carries the dated stops an interactive walk can step to."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="tick", var_prod_default=False)

    def set_flows(self, **kwargs):
        super().set_flows(**kwargs)
        for index, date in enumerate((RSS_HORIZON, 2 * RSS_HORIZON)):
            self.add_atm2states(
                name=f"clock_{index}",
                st1="s0",
                st2="s1",
                occ_law_12={"cls": "delay", "time": date},
                cond_occ_21=False,
            )


def build(name, comp_cls, extra_supply=None):
    """One shared source, the two-set component, and a consumer per product."""
    system = muscadet.System(name=name)

    system.add_component(name="SA", cls="SourceContinuous", flow="a", rate=RSS_SUPPLY)
    system.add_component(name="TWO", cls=comp_cls)
    system.add_component(
        name="KX", cls="ConsumerContinuous", flow="x", demand=RSS_DEMAND
    )
    system.add_component(
        name="KY", cls="ConsumerContinuous", flow="y", demand=RSS_DEMAND
    )
    system.add_component(name="CLK", cls="RssClock")

    system.connect_flow(source="SA", target="TWO", flow_name="a")
    system.connect_flow(source="TWO", target="KX", flow_name="x")
    system.connect_flow(source="TWO", target="KY", flow_name="y")

    if extra_supply is not None:
        flow_name, rate = extra_supply
        system.add_component(
            name="SE", cls="SourceContinuous", flow=flow_name, rate=rate
        )
        system.connect_flow(source="SE", target="TWO", flow_name=flow_name)

    return system


def stored(system, flow_name):
    """What an input capacity accumulates per unit time, 0 when there is none."""
    comp = system.comp["TWO"]
    capacity = comp.get_capacity_of_flow(flow_name, "in")

    if capacity is None:
        return 0.0

    return capacity.get_inflow(flow_name) - capacity.get_outflow(flow_name)


def snapshot(system):
    """What the shared input delivers, and what the two sets made of it."""
    comp = system.comp["TWO"]

    return {
        "time": system.currentTime(),
        "a_in": comp.flows_in["a"].get_delivered(),
        "a_stored": stored(system, "a"),
        "x_out": comp.flows_out["x"].var_fed.value(),
        "y_out": comp.flows_out["y"].var_fed.value(),
        "kx_in": system.comp["KX"].flows_in["x"].get_delivered(),
        "ky_in": system.comp["KY"].flows_in["y"].get_delivered(),
    }


def walk(system, horizon, limit=20):
    """Step to ``horizon``, recording a snapshot at every stop."""
    trace = [snapshot(system)]

    for _ in range(limit):
        if system.currentTime() >= horizon:
            break
        system.isimu_step_forward()
        trace.append(snapshot(system))

    return trace


def settled(trace):
    """The stops at which the sweeps have run.

    A PDMP equation is not evaluated at ``t = 0``, so the first entry reports
    declared defaults rather than a settled flow network.
    """
    return [entry for entry in trace if entry["time"] > 0.0]


@pytest.fixture(scope="module")
def the_run():
    """Every scenario, built, driven and deleted in turn."""
    obs = {}

    tsys = build("RuleSetsSharedPlain", "RssTwoSets")
    tsys.isimu_start()
    obs["plain"] = walk(tsys, RSS_HORIZON)
    tsys.isimu_stop()
    tsys.deleteSys()

    psys = build("RuleSetsSharedPartial", "RssPartialSets", extra_supply=("e", 0.25))
    psys.isimu_start()
    obs["partial"] = walk(psys, RSS_HORIZON)
    psys.isimu_stop()
    psys.deleteSys()

    bsys = build("RuleSetsSharedBuffered", "RssBufferedSets")
    bsys.isimu_start()
    obs["buffered"] = walk(bsys, RSS_HORIZON)

    obs["system"] = bsys
    return obs


# ----------------------------------------------------------------------
# Conservation
# ----------------------------------------------------------------------


def test_two_rule_sets_never_produce_more_than_the_input_delivered(the_run):
    """The breach itself: 2.0 produced out of 1.0 received."""
    stops = settled(the_run["plain"])

    assert stops, "the walk must reach a stop where the sweeps have run"

    for stop in stops:
        produced = stop["x_out"] + stop["y_out"]
        assert produced <= stop["a_in"] + 1e-6, (
            f"t={stop['time']:g}: the two rule sets produced {produced:g} out "
            f"of {stop['a_in']:g} received"
        )


def test_conservation_holds_on_the_two_set_component_at_every_stop(the_run):
    """Per component, per stop: draw == consumption + storage.

    No capacity buffers ``a`` here, so the storage term is zero and the books
    must close on the two productions alone.
    """
    for stop in settled(the_run["plain"]):
        consumed = stop["x_out"] + stop["y_out"] + stop["a_stored"]
        assert stop["a_in"] == pytest.approx(consumed, abs=1e-6), (
            f"t={stop['time']:g}: a draws {stop['a_in']:g} against a "
            f"consumption of {consumed:g}"
        )


def test_the_first_declared_set_is_served_first(the_run):
    """Declaration order is the priority order, and it is total here.

    ``rx`` is declared first and nothing else limits it, so it takes the whole
    supply and ``ry`` gets nothing -- rather than both running at full scale.
    """
    stop = settled(the_run["plain"])[-1]

    assert stop["x_out"] == pytest.approx(RSS_SUPPLY)
    assert stop["y_out"] == pytest.approx(0.0, abs=1e-9)


def test_what_the_first_set_cannot_use_reaches_the_second(the_run):
    """A set limited by its own reagent leaves the rest, it does not reserve it.

    ``rx`` is capped at 0.25 by ``e``, so it draws 0.25 of ``a`` and the
    remaining 0.75 is what ``ry`` may draw -- at a coefficient of 2, that is
    0.375 of ``y``.
    """
    stop = settled(the_run["partial"])[-1]

    assert stop["x_out"] == pytest.approx(0.25, abs=1e-6)
    assert stop["y_out"] == pytest.approx(
        (RSS_SUPPLY - 0.25) / RSS_SECOND_COEFF, abs=1e-6
    )

    drawn_by_rules = stop["x_out"] + RSS_SECOND_COEFF * stop["y_out"]
    assert stop["a_in"] == pytest.approx(drawn_by_rules, abs=1e-6)


def test_a_buffered_shared_input_is_not_drained_by_the_second_set(the_run):
    """The outflow the rules write must never exceed what the volume can serve.

    With the whole supply handed to each set, the capacity's outflow was twice
    its inflow and the level fell by a quantity no supplier delivered.
    """
    comp = the_run["system"].comp["TWO"]
    capacity = comp.get_capacity_of_flow("a", "in")

    assert capacity is not None

    for stop in settled(the_run["buffered"]):
        produced = stop["x_out"] + stop["y_out"]
        assert produced <= stop["a_in"] + capacity.total_quantity() + 1e-6, (
            f"t={stop['time']:g}: {produced:g} produced from {stop['a_in']:g} "
            f"delivered and {capacity.total_quantity():g} held"
        )

    assert (
        capacity.get_outflow("a")
        <= capacity.get_inflow("a") + capacity.total_quantity() + 1e-6
    )


def test_delete(the_run):
    the_run["system"].isimu_stop()
    the_run["system"].deleteSys()
    cod3s.terminate_session()
