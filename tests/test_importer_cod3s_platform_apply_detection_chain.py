"""Integration test — full COD3S Platform Detection chain V2 export end-to-end.

Loads the real ``detection chain + kb.json`` payload (26 components, 177
connections, 15 KB classes) exported from a COD3S Platform instance,
runs it through the importer, and asserts the resulting
``muscadet.System`` matches the source counts and is consumable by
``cod3s-isimu`` (no exception on ``isimu_start``).

This is the 'real-world' acceptance test for the Phase 1 importer.
Failures here likely indicate either a regression in the converter
or a schema drift on the Platform side that the fixture has captured.
"""

import json
import os

import cod3s
import pytest

# Importing ``muscadet`` triggers PyCATSHOO init — gracefully skip
# when the native libs are unavailable (dev box without LD setup).
muscadet = pytest.importorskip("muscadet")

from muscadet.importers.cod3s_platform import system_from_export  # noqa: E402

_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "detection_chain_export.json"
)


@pytest.fixture(scope="module")
def detection_chain_payload():
    with open(_FIXTURE) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def detection_chain_system(detection_chain_payload):
    """Build the Detection chain V2 system once per module, tear it down on exit.

    Without the explicit ``deleteSys()`` + ``terminate_session()``,
    PyCATSHOO's single-system constraint leaks into subsequent test
    modules and causes spurious "system already exists" failures.
    """
    system = system_from_export(detection_chain_payload)
    yield system
    system.deleteSys()
    cod3s.terminate_session()


def test_payload_metadata(detection_chain_payload):
    """Sanity : the fixture is the version we expect."""
    assert detection_chain_payload["model"]["name"] == "Detection chain V2"
    assert detection_chain_payload["kb_embedded"]["name"] == "DETECT"
    # Source-of-truth counts that the System must match
    assert len(detection_chain_payload["model"]["elements"]["components"]) == 26
    assert len(detection_chain_payload["model"]["elements"]["connections"]) == 177
    # KB classes ; the fixture has 15 templates
    assert len(detection_chain_payload["kb_embedded"]["component_templates"]) == 15


def test_component_count(detection_chain_system):
    assert len(detection_chain_system.comp) == 26


def test_class_names_preserved(detection_chain_system):
    """Every component carries its source ``class_name`` in metadata.
    Spot-check a few well-known DETECT classes."""
    classes = {
        c.metadata.get("class_name") for c in detection_chain_system.comp.values()
    }
    # Detection chain V2 uses these class names (subset of the 15 templates)
    expected_subset = {"Power_supply", "Serial_FO_converter", "Ethernet"}
    assert expected_subset.issubset(classes), (
        f"Missing classes from converted system: " f"{expected_subset - classes}"
    )


def test_each_component_has_its_kb_flows(
    detection_chain_payload, detection_chain_system
):
    """For each component, the runtime flows_in / flows_out match the
    KB template's interfaces (by direction)."""
    kb_templates = detection_chain_payload["kb_embedded"]["component_templates"]
    for comp in detection_chain_system.comp.values():
        class_name = comp.metadata["class_name"]
        ifaces = kb_templates[class_name]["interfaces"]
        expected_in = {
            i["name"] for i in ifaces.values() if i["port_type"]["general"] == "input"
        }
        expected_out = {
            i["name"] for i in ifaces.values() if i["port_type"]["general"] == "output"
        }
        assert (
            set(comp.flows_in) == expected_in
        ), f"Component {comp.name()}: input flows mismatch"
        assert (
            set(comp.flows_out) == expected_out
        ), f"Component {comp.name()}: output flows mismatch"


def test_all_connections_wired(detection_chain_payload, detection_chain_system):
    """Each connection in the payload should be reflected in muscadet's
    runtime via ``is_connected_to``. Resolved by display name."""
    components_by_id = {
        cid: c["name"]
        for cid, c in detection_chain_payload["model"]["elements"]["components"].items()
    }
    missing = []
    for conn_id, conn in detection_chain_payload["model"]["elements"][
        "connections"
    ].items():
        src_name = components_by_id[conn["component_source"]]
        tgt_name = components_by_id[conn["component_target"]]
        flow = conn["interface_source"]
        src_comp = detection_chain_system.comp[src_name]
        if not src_comp.is_connected_to(tgt_name, flow):
            missing.append((conn_id, src_name, flow, tgt_name))
    assert not missing, (
        f"{len(missing)}/177 connections not wired. First few: " f"{missing[:5]}"
    )


@pytest.mark.slow
def test_isimu_start_does_not_raise(detection_chain_system):
    """Ultimate Phase 1 acceptance : the System can be handed off to
    ``cod3s-isimu`` for interactive simulation without an exception.

    Marked ``slow`` because PyCATSHOO automaton initialization on a
    26-component system is non-trivial. Run with ``--runslow``.
    """
    # ``isimu_start`` is the muscadet/cod3s API entry point. The exact
    # signature may evolve ; the assertion is just that no exception
    # propagates. Phase 1 doesn't validate the simulation OUTPUTS,
    # only that the system instance is well-formed enough to be loaded.
    detection_chain_system.isimu_start()
