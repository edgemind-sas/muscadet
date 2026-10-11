"""A derating allocated from outside its component survives a declaration (#13).

``ObjFlow.add_derating(mode, flow)`` is the public door for a mode declared
OUTSIDE the component -- a standalone ``cod3s.ObjMode2S`` naming variables by
their exact basename -- and the road the COD3S Platform importer takes for
every ``deratings`` entry of an export. Up to 5.12.0 the declaration did not
carry that allocation: ``FlowContinuousOut.derating`` sat among the derived
fields, on the ground that the rebuild recreates it, which it does only for a
mode declared ON the component. ``build_system`` rebuilt the component without
the variable, then refused the mode that writes it.

Since 5.13.0 a component's declaration carries the ``(mode, flow)`` pairs
allocated through ``add_derating`` in a ``deratings`` section, replayed after
``set_flows()`` and before the modes. What is asserted here:

* the issue's model round trips, and the rebuilt system behaves identically;
* a derating a mode declared ON the component allocates for itself is NOT
  written, since the mode recreates it -- so a spec with no external
  allocation is byte-identical to what it was;
* the section is validated before anything is built, and an entry naming no
  continuous output is refused naming the component.
"""

import cod3s
import pytest

import muscadet
from muscadet.declare import (
    ComponentSpecError,
    build_component,
    build_system,
    check_spec,
    component_spec,
)
from muscadet.engine import system_declaration

#: What the source produces.
DD_RATE = 10.0
#: What the consumer asks for.
DD_DEMAND = 8.0
#: When the outside mode cuts the output, and how long it keeps it cut.
DD_DELAY = 2.0
#: Where the trajectory is read.
DD_DATES = (1.0, 3.0, 5.0)


class DdSource(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=DD_RATE)


class DdSink(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q", var_demand_default=DD_DEMAND)


def outside_mode_system(name):
    """The issue's model: a source derated by a mode declared outside it."""
    system = muscadet.System(name=name)
    src = system.add_component(cls="DdSource", name="Src")
    system.add_component(cls="DdSink", name="Snk")
    system.connect_flow(source="Src", target="Snk", flow_name="q")
    src.add_derating("M", "q")
    cod3s.ObjMode2S(
        fm_name="M",
        targets=["Src"],
        behaviour="external",
        occ_law={"cls": "delay", "time": [DD_DELAY]},
        not_occ_law={"cls": "delay", "time": [DD_DELAY]},
        occ_param_name=["ttf"],
        not_occ_param_name=["ttr"],
        occ_effects={"M_derating_q": 0.0},
        not_occ_effects={"M_derating_q": 1.0},
    )
    return system


def delivered(system):
    """What reaches the consumer at each of :data:`DD_DATES`."""
    rows = []
    system.isimu_start()
    for date in DD_DATES:
        system.isimu_step_to(date)
        rows.append(system.comp["Snk"].flows_in["q"].var_fed.value())
    system.isimu_stop()
    return rows


def test_an_outside_derating_round_trips_through_the_declaration():
    live = outside_mode_system("DdLive")
    try:
        document = system_declaration(live)
        direct = delivered(live)
    finally:
        live.deleteSys()

    assert document["components"]["Src"]["deratings"] == [{"mode": "M", "flow": "q"}]
    # The mode does cut the output: otherwise the comparison below says nothing.
    assert direct == pytest.approx([DD_DEMAND, 0.0, DD_DEMAND])

    rebuilt = muscadet.System(name="DdRebuilt")
    try:
        build_system(document, system=rebuilt)
        assert "M_derating_q" in [
            var.basename() for var in rebuilt.comp["Src"].variables()
        ]
        # Read back from the rebuilt system, the declaration is the same, but
        # for the class it was read from: ``ObjFlow`` once rebuilt from a spec.
        again = dict(system_declaration(rebuilt)["components"]["Src"])
        first = dict(document["components"]["Src"])
        again.pop("source_cls")
        first.pop("source_cls")
        assert again == first
        assert delivered(rebuilt) == pytest.approx(direct)
    finally:
        rebuilt.deleteSys()


def test_a_derating_a_declared_mode_allocates_itself_is_not_written():
    """The mode recreates it; writing it too would change every stored spec."""
    system = muscadet.System(name="DdOwnMode")
    try:
        src = system.add_component(cls="DdSource", name="Src")
        src.add_delay_failure_mode(
            name="wear",
            failure_time=DD_DELAY,
            repair_time=DD_DELAY,
            failure_effects=[("q", 0.5)],
        )
        assert "wear" in src.flows_out["q"].derating

        spec = component_spec(src)
    finally:
        system.deleteSys()

    assert "deratings" not in spec


@pytest.mark.parametrize("preallocated_first", [True, False], ids=["before", "after"])
def test_a_preallocated_derating_a_declared_mode_reuses_is_not_written(
    preallocated_first,
):
    """The importer's shape: the pair is recorded, then a mode ON it owns it.

    Either order: the platform pre-allocates before the mode exists, a
    hand-written model may well allocate after.
    """
    system = muscadet.System(name=f"DdPrealloc{preallocated_first}")
    try:
        src = system.add_component(cls="DdSource", name="Src")
        if preallocated_first:
            src.add_derating("wear", "q")
        src.add_delay_failure_mode(
            name="wear",
            failure_time=DD_DELAY,
            repair_time=DD_DELAY,
            failure_effects=[("q", 0.5)],
        )
        if not preallocated_first:
            src.add_derating("wear", "q")
        # An outside mode beside it is still written.
        src.add_derating("M", "q")

        spec = component_spec(src)
    finally:
        system.deleteSys()

    assert spec["deratings"] == [{"mode": "M", "flow": "q"}]


def test_a_component_with_no_derating_writes_no_section():
    system = muscadet.System(name="DdBare")
    try:
        spec = component_spec(system.add_component(cls="DdSource", name="Src"))
    finally:
        system.deleteSys()

    assert "deratings" not in spec


@pytest.mark.parametrize(
    "entry, words",
    [
        ({"mode": "M"}, ["flow"]),
        ({"flow": "q"}, ["mode"]),
        ({"mode": "M", "flow": "q", "value": 0.5}, ["value"]),
        ({"mode": "", "flow": "q"}, ["mode"]),
        ("M_derating_q", ["mapping"]),
    ],
    ids=["no_flow", "no_mode", "unknown_key", "empty_mode", "not_a_mapping"],
)
def test_a_malformed_entry_is_refused_before_anything_is_built(entry, words):
    with pytest.raises(ComponentSpecError) as excinfo:
        check_spec({"name": "Src", "deratings": [entry]})

    for word in ["Src", "deratings"] + words:
        assert word in str(excinfo.value)


def test_an_entry_naming_no_continuous_output_is_refused_naming_the_component():
    spec = {
        "name": "Src",
        "cls": "DdSource",
        "deratings": [{"mode": "M", "flow": "nope"}],
    }
    system = muscadet.System(name="DdRefused")
    try:
        with pytest.raises(ComponentSpecError) as excinfo:
            build_component(system, spec)
    finally:
        system.deleteSys()

    for word in ("Src", "'M'", "'nope'", "continuous output"):
        assert word in str(excinfo.value)


def test_delete():
    cod3s.terminate_session()
