"""The round trip, at the SYSTEM scale: declared, rebuilt, and identical.

``test_system_declaration_001`` pins what the declaration carries. This module
pins the only claim that matters: a system built from a declaration is the
system the declaration was read from.

**The comparison is on the document, and that is the stronger statement.**
Two engines receive the same declaration or they do not; comparing trajectories
would only say that one engine agrees with itself. A document that reads back
identical after a full rebuild says the description is complete, and a
description that is complete is exactly what the seam needs.

The subject is the H2 electrolysis plant, the hardest continuous case shipped:
capacities, a rule with a limiting reagent, a derating failure mode and a
capability sweep.

The session is torn down between the two builds because **PyCATSHOO forbids
more than one live system per process**. That constraint is the reason the
declaration is worth having: two documents compare without building anything.
"""

import json

import cod3s
import pytest

import muscadet
import muscadet.kb.continuous  # noqa: F401  -- registers the shipped classes
from muscadet.declare import build_system, system_spec

CONS = {"H2O": 4, "Elec": 1}
PROD = {"H2": 1, "O2": 1}


def build_by_hand(system):
    system.add_component(name="S_H2O", cls="SourceContinuous", flow="H2O", rate=2)
    system.add_component(
        name="B1",
        cls="CapacityContinuous",
        flow="Elec",
        ports="out",
        capacity=100,
        content_init={"Elec": 100},
        capacity_name="battery",
    )
    system.add_component(
        name="Electro",
        cls="TransformerContinuous",
        flows_in=list(CONS),
        flows_out=list(PROD),
        rules=[dict(name="electrolysis", cons=CONS, prod=PROD)],
    )
    system.add_component(
        name="Local",
        cls="CapacityContinuous",
        flow="H2",
        ports="both",
        capacity=6,
        content_init={"H2": 3},
        capacity_name="tank",
        fill_rate=1,
    )
    system.connect_flow(source="S_H2O", target="Electro", flow_name="H2O")
    system.connect_flow(source="B1", target="Electro", flow_name="Elec")
    system.connect_flow(source="Electro", target="Local", flow_name="H2")
    system.comp["Electro"].add_delay_failure_mode(
        name="df_H2", failure_time=2, repair_time=2, failure_effects=[(".*", 0.0)]
    )
    system.add_indicator_var(component="^Local$", var="^tank_qty_H2$", stats=["mean"])
    return system


@pytest.fixture(scope="module")
def the_round_trip():
    """Declare, tear the session down, rebuild from the document alone."""
    original = build_by_hand(muscadet.System(name="RoundTripRef"))
    declared = json.loads(json.dumps(system_spec(original)))
    original.deleteSys()
    cod3s.terminate_session()

    rebuilt = build_system(declared, system=muscadet.System(name="RoundTripRebuilt"))
    yield {"system": rebuilt, "declared": declared, "rebuilt": system_spec(rebuilt)}


def _without_provenance(components):
    """Strip ``source_cls``, which is origin and not identity.

    ``component_spec`` documents it as the class a declaration was READ BACK
    from, kept for a caller that wants to show it, and a declaration always
    expands onto ``ObjFlow``. The rebuilt component's origin IS the document,
    so reporting ``ObjFlow`` is truthful rather than a loss. Two components
    are the same when they declare the same thing, not when they remember the
    same ancestor.
    """
    return {
        name: {k: v for k, v in spec.items() if k != "source_cls"}
        for name, spec in components.items()
    }


def test_the_rebuilt_system_declares_the_same_components(the_round_trip):
    assert _without_provenance(
        the_round_trip["rebuilt"]["components"]
    ) == _without_provenance(the_round_trip["declared"]["components"])


def test_provenance_says_the_rebuild_came_from_the_document(the_round_trip):
    """Pinned so the choice above stays a choice and not an oversight."""
    for spec in the_round_trip["rebuilt"]["components"].values():
        assert spec.get("source_cls") == "ObjFlow"
    assert (
        the_round_trip["declared"]["components"]["S_H2O"]["source_cls"]
        == "SourceContinuous"
    )


def test_the_rebuilt_system_declares_the_same_wiring(the_round_trip):
    assert (
        the_round_trip["rebuilt"]["connections"]
        == the_round_trip["declared"]["connections"]
    )


def test_the_rebuilt_system_observes_the_same_things(the_round_trip):
    assert (
        the_round_trip["rebuilt"]["indicators"]
        == the_round_trip["declared"]["indicators"]
    )


def test_the_failure_mode_crossed_without_being_redeclared(the_round_trip):
    """The delay mode is part of the COMPONENT declaration, so the rebuild
    carries it without the caller adding it back. Pinned because a mode lost in
    the crossing would make a plant look reliable rather than fail loudly."""
    electro = the_round_trip["rebuilt"]["components"]["Electro"]
    assert electro.get("failure_modes"), electro.keys()


def test_the_whole_document_round_trips(the_round_trip):
    """The claim, whole: nothing was lost and nothing was invented."""
    declared = dict(the_round_trip["declared"])
    rebuilt = dict(the_round_trip["rebuilt"])
    # The system's own name is the caller's, not the document's subject.
    declared.pop("name", None)
    rebuilt.pop("name", None)
    declared["components"] = _without_provenance(declared["components"])
    rebuilt["components"] = _without_provenance(rebuilt["components"])
    assert rebuilt == declared


def test_delete(the_round_trip):
    the_round_trip["system"].deleteSys()
    cod3s.terminate_session()
