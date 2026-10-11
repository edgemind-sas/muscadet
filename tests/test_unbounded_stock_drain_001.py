"""A stock nothing bounds, facing a demand nothing bounds, is refused (#8).

A capacity beside the rules (``side="in"``) holding a stock and declaring no
``serve_rate`` serves its rules without limit. When what the rules feed takes
without limit too -- a room with an infinite ``fill_rate`` -- nothing bounds
the rule at all, and up to 5.11.0 it ran at ``UNCONSTRAINED_SCALE``, one rule
activity per time unit: measured, the membrane below drained at -0.99 and the
room rose at +1, whatever the stock and whatever the room's volume. That rate
is stated nowhere in the model, so the same model written in seconds rather
than hours drained 3600 times slower in physical time.

The physics is an instantaneous transfer, which a rate cannot express. Since
5.12.0 the configuration is refused at the first evaluation that meets it,
with ``UnboundedRateError`` naming the component, the rule set, the capacity
and the key to declare, exactly as RAICHU refuses it since pyraichu 0.64.0.
What is asserted here:

* the refusal, on both entry points, and what it names;
* the near misses that must keep building: a finite ``serve_rate``, a finite
  demand downstream, an empty volume serving what transits it, and a rule
  consuming nothing, whose declared ``prod`` IS its rate.
"""

import math

import cod3s
import pytest

import muscadet

#: What the source feeds the membrane with.
USD_INFLOW = 0.01
#: What the membrane holds at t=0.
USD_STOCK = 5.0
#: What the room holds at t=0.
USD_ROOM_INIT = 2.0
#: A declared discharge ceiling, or a declared downstream claim.
USD_RATE = 0.5
#: Where the trajectory is read.
USD_DATE = 1.0


class UsdSource(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="H2m", var_fed_default=USD_INFLOW)


class UsdGate(muscadet.ObjFlow):
    def add_flows(self, open_=True, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="go", var_prod_default=open_)


class UsdMembrane(muscadet.ObjFlow):
    def add_flows(
        self, stock=USD_STOCK, serve_rate=math.inf, cons=1.0, gated=False, **kwargs
    ):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="H2m")
        if gated:
            self.add_flow_in(name="go")
        self.add_flow_continuous_out(name="H2")
        self.add_capacity(
            name="membrane",
            flows=[{"name": "H2m", "weight": 1.0}],
            capacity=100.0,
            content_init={"H2m": stock},
            fill_rate=math.inf,
            serve_rate=serve_rate,
            side="in",
        )
        consumed = {} if cons is None else {"H2m": cons}
        if gated:
            consumed["go"] = 1.0
        self.add_rules(name="release", rules=[{"cons": consumed, "prod": {"H2": 1.0}}])


class UsdRoom(muscadet.ObjFlow):
    def add_flows(self, fill_rate=math.inf, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="H2")
        self.add_capacity(
            name="room",
            flow="H2",
            capacity=100.0,
            content_init={"H2": USD_ROOM_INIT},
            fill_rate=fill_rate,
        )


def build_system(name, membrane=None, room=None, gate=None):
    system = muscadet.System(name=name)
    system.add_component(name="SRC", cls="UsdSource")
    if gate is not None:
        membrane = {**(membrane or {}), "gated": True}
        system.add_component(name="GATE", cls="UsdGate", open_=gate)
    system.add_component(name="MEM", cls="UsdMembrane", **(membrane or {}))
    system.add_component(name="ROOM", cls="UsdRoom", **(room or {}))
    system.connect_flow(source="SRC", target="MEM", flow_name="H2m")
    system.connect_flow(source="MEM", target="ROOM", flow_name="H2")
    if gate is not None:
        system.connect("GATE", "go_out", "MEM", "go_in")
    return system


def levels_at(name, date=USD_DATE, membrane=None, room=None, gate=None):
    """The membrane and room contents at ``date``, the system deleted."""
    system = build_system(name, membrane, room, gate)
    try:
        system.isimu_start()
        system.isimu_step_to(date)
        levels = (
            system.comp["MEM"].variable("membrane_qty_H2m").value(),
            system.comp["ROOM"].variable("room_qty_H2").value(),
        )
        system.isimu_stop()
        return levels
    finally:
        system.deleteSys()


# ----------------------------------------------------------------------
# The refusal
# ----------------------------------------------------------------------


def test_simulate_refuses_an_unbounded_stock_facing_an_unbounded_demand():
    system = build_system("UsdRefusedSimulate")
    try:
        with pytest.raises(muscadet.UnboundedRateError) as excinfo:
            system.simulate({"nb_runs": 1, "schedule": [0.5, 1.0], "seed": 42})
    finally:
        system.deleteSys()

    error = excinfo.value
    assert error.component == "MEM"
    assert error.rule_set == "release"
    assert error.flows == ["H2m"]
    assert error.capacities == ["membrane"]
    for word in ("MEM", "release", "membrane", "serve_rate", "time unit"):
        assert word in str(error)


def test_an_interactive_session_refuses_it_too():
    system = build_system("UsdRefusedIsimu")
    try:
        with pytest.raises(muscadet.UnboundedRateError, match="membrane"):
            system.isimu_start()
            system.isimu_step_to(USD_DATE)
    finally:
        system.deleteSys()


def test_an_open_discrete_gate_does_not_hide_the_unbounded_stock():
    """A boolean in ``cons`` reads 1, a gate and not a bound on the rate."""
    system = build_system("UsdGateOpen", gate=True)
    try:
        with pytest.raises(muscadet.UnboundedRateError) as excinfo:
            system.isimu_start()
            system.isimu_step_to(USD_DATE)
    finally:
        system.deleteSys()

    assert excinfo.value.flows == ["H2m"]


def test_the_refusal_is_a_value_error():
    """Every existing ``except ValueError`` around a run still catches it."""
    assert issubclass(muscadet.UnboundedRateError, ValueError)


# ----------------------------------------------------------------------
# The near misses, which must keep building
# ----------------------------------------------------------------------


def test_a_finite_serve_rate_drains_the_stock_at_that_rate():
    membrane, room = levels_at("UsdServeRate", membrane={"serve_rate": USD_RATE})

    assert membrane == pytest.approx(USD_STOCK - (USD_RATE - USD_INFLOW), abs=1e-3)
    assert room == pytest.approx(USD_ROOM_INIT + USD_RATE, abs=1e-3)


def test_a_finite_demand_downstream_drains_the_stock_at_that_demand():
    membrane, room = levels_at("UsdFiniteDemand", room={"fill_rate": USD_RATE})

    assert membrane == pytest.approx(USD_STOCK - (USD_RATE - USD_INFLOW), abs=1e-3)
    assert room == pytest.approx(USD_ROOM_INIT + USD_RATE, abs=1e-3)


def test_a_closed_discrete_gate_stops_the_rule():
    """Nothing is drawn, so nothing runs at a rate the model does not state."""
    membrane, room = levels_at("UsdGateClosed", gate=False)

    assert membrane == pytest.approx(USD_STOCK + USD_INFLOW, abs=1e-3)
    assert room == pytest.approx(USD_ROOM_INIT, abs=1e-3)


def test_an_empty_volume_serves_what_transits_it():
    membrane, room = levels_at("UsdEmpty", membrane={"stock": 0.0})

    assert membrane == pytest.approx(0.0, abs=1e-3)
    assert room == pytest.approx(USD_ROOM_INIT + USD_INFLOW, abs=1e-3)


@pytest.mark.parametrize("cons", [None, 0.0], ids=["no_cons", "catalyst"])
def test_a_rule_consuming_nothing_runs_at_its_declared_production(cons):
    """No reagent is served without limit: ``prod`` is the declared rate."""
    _, room = levels_at(f"UsdSourceRule{cons}", membrane={"cons": cons})

    assert room == pytest.approx(USD_ROOM_INIT + 1.0, abs=1e-3)


def test_delete():
    cod3s.terminate_session()
