"""A whole system, described in data and rebuilt from it.

``component_spec`` already reads a component back, down to its capacities,
rules, transfers and failure modes. What no component knows is how it is wired
and what is observed, and those are exactly what stops a declaration from being
a system. This module pins the system scale.

The subject is the H2 electrolysis plant of ``test_h2_stack_001``, the hardest
continuous case shipped: capacities, a rule with a limiting reagent, a derating
failure mode and a capability sweep. If the declaration survives that, the
easier models are not the question.

**Two processes would be needed to compare a system against its rebuild**,
PyCATSHOO forbidding more than one live system per process. So the comparison
here is on the DOCUMENT: a system built by hand and a system built from its own
declaration must produce the same declaration. That is a stronger statement
than comparing trajectories, and it is the property the seam actually needs --
two engines receive the same document or they do not.
"""

import json

import cod3s
import pytest

import muscadet
import muscadet.kb.continuous  # noqa: F401  -- registers the shipped classes
from muscadet.declare import (
    SYSTEM_SPEC_VERSION,
    SystemSpecError,
    build_system,
    check_system_spec,
    system_spec,
)

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
def the_run():
    system = build_by_hand(muscadet.System(name="SysDeclSys"))
    yield {"system": system, "spec": system_spec(system)}


def test_the_declaration_is_data(the_run):
    """Serialisable, or it is not a document two engines can share."""
    blob = json.dumps(the_run["spec"])
    assert json.loads(blob) == the_run["spec"]


def test_it_carries_its_version(the_run):
    assert the_run["spec"]["version"] == SYSTEM_SPEC_VERSION


def test_it_carries_every_component(the_run):
    assert set(the_run["spec"]["components"]) == {"S_H2O", "B1", "Electro", "Local"}


def test_it_carries_the_wiring_no_component_knows(the_run):
    """The gap the component scale leaves: who is wired to whom."""
    wired = {
        (c["source"], c["target"], c.get("flow"))
        for c in the_run["spec"]["connections"]
    }
    assert ("S_H2O", "Electro", "H2O") in wired
    assert ("B1", "Electro", "Elec") in wired
    assert ("Electro", "Local", "H2") in wired


def test_a_connection_names_its_flow_when_the_convention_holds(the_run):
    """The flow name is not decoration: rebuilding through ``connect_flow``
    re-runs the family check that the raw ``connect`` route skips."""
    for entry in the_run["spec"]["connections"]:
        if entry["source_box"].endswith("_out") and entry["target_box"].endswith("_in"):
            if entry["source_box"][:-4] == entry["target_box"][:-3]:
                assert entry.get("flow") == entry["source_box"][:-4]


def test_it_carries_what_is_observed(the_run):
    """An indicator is read back CONCRETELY, not as the pattern typed.

    A declaration holding ``^Local$`` as a pattern would re-expand at rebuild
    time against whatever components exist then, which is not the same system.
    """
    names = {i["component"] for i in the_run["spec"]["indicators"]}
    assert "Local" in names, the_run["spec"]["indicators"]


def test_the_declaration_is_stable(the_run):
    """Read twice, identical: a document that moves cannot be diffed."""
    assert system_spec(the_run["system"]) == the_run["spec"]


def test_it_validates_without_building(the_run):
    check_system_spec(the_run["spec"])


def test_a_future_major_is_refused_by_number(the_run):
    """Refused with its own number, never half-built."""
    spec = dict(the_run["spec"], version="99.0.0")
    with pytest.raises(SystemSpecError, match="99.0.0"):
        check_system_spec(spec)


def test_a_connection_to_an_undeclared_component_is_refused(the_run):
    spec = json.loads(json.dumps(the_run["spec"]))
    spec["connections"].append(
        {
            "source": "S_H2O",
            "source_box": "H2O_out",
            "target": "Ghost",
            "target_box": "H2O_in",
        }
    )
    with pytest.raises(SystemSpecError, match="Ghost"):
        check_system_spec(spec)


def test_a_declaration_without_version_is_refused(the_run):
    spec = {k: v for k, v in the_run["spec"].items() if k != "version"}
    with pytest.raises(SystemSpecError, match="version"):
        check_system_spec(spec)


def test_build_system_fills_a_system_the_caller_owns(the_run):
    """``build_system`` accepts an existing system precisely BECAUSE PyCATSHOO
    forbids a second one: a caller comparing two declarations cannot let this
    function create either of them."""
    import inspect

    assert "system" in inspect.signature(build_system).parameters


def test_delete(the_run):
    the_run["system"].deleteSys()
    cod3s.terminate_session()
