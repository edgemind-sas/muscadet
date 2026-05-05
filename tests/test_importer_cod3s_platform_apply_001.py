"""Integration test — apply layer on the minimal synthetic fixture.

Requires PyCATSHOO native libraries available at import time.
Validates that the parse → apply → ``muscadet.System`` pipeline works
end-to-end on a 2-component / 1-connection model.
"""

import json
import os

import cod3s
import pytest

# Importing ``muscadet`` here triggers Pycatshoo init — if the env
# isn't set up properly, the whole module is skipped rather than
# erroring out the collection phase.
muscadet = pytest.importorskip("muscadet")

from muscadet.importers.cod3s_platform import (  # noqa: E402
    apply_to_system,
    parse_platform_export,
    system_from_export,
)


_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "minimal_export.json"
)


def _load():
    with open(_FIXTURE) as f:
        return json.load(f)


@pytest.fixture
def cleanup_system():
    """Function-scoped guard that tears down PyCATSHOO state after each test.

    PyCATSHOO refuses to instantiate two systems with the same name
    concurrently. Each test in this module builds a fresh system from
    the fixture (which always names it ``MinimalModel``), so we must
    delete the system and terminate the session between tests. The
    fixture yields a list the test fills with system instances ; on
    teardown we delete each one before terminating the session.
    """
    systems: list = []
    yield systems
    for system in systems:
        try:
            system.deleteSys()
        except Exception:
            pass
    cod3s.terminate_session()


def test_system_from_export_minimal_smoke(cleanup_system):
    """End-to-end : payload → muscadet.System with 2 components, 1 connection."""
    payload = _load()
    system = system_from_export(payload)
    cleanup_system.append(system)
    # System name picked up from the payload
    assert system.name() == "MinimalModel"
    # Both components instantiated
    assert "Source1" in system.comp
    assert "Sink1" in system.comp
    assert len(system.comp) == 2


def test_class_name_preserved_in_component_metadata(cleanup_system):
    """The KB ``class_name`` must survive the conversion via metadata —
    even though all components are instantiated as the generic
    ``muscadet.ObjFlow``, downstream filters can still group by class."""
    payload = _load()
    system = system_from_export(payload)
    cleanup_system.append(system)
    src = system.comp["Source1"]
    sink = system.comp["Sink1"]
    assert src.metadata.get("class_name") == "Source"
    assert sink.metadata.get("class_name") == "Sink"
    # Platform UUID also preserved for cross-system traceability
    assert src.metadata.get("platform_id") == "id-source"
    assert sink.metadata.get("platform_id") == "id-sink"


def test_flows_in_and_out_present(cleanup_system):
    payload = _load()
    system = system_from_export(payload)
    cleanup_system.append(system)
    src = system.comp["Source1"]
    sink = system.comp["Sink1"]
    # Source has the ``out_a`` output flow declared in the KB
    assert "out_a" in src.flows_out
    # Sink has the ``out_a`` input flow (per the KB ``out_a__input`` template)
    assert "out_a" in sink.flows_in


def test_connection_wired(cleanup_system):
    """The single connection in the fixture must be reflected in muscadet's
    runtime connection state."""
    payload = _load()
    system = system_from_export(payload)
    cleanup_system.append(system)
    # ``ObjFlow.is_connected_to`` is the canonical muscadet inspector
    src = system.comp["Source1"]
    assert src.is_connected_to("Sink1", "out_a")


def test_apply_to_system_on_existing_system(cleanup_system):
    """The apply layer accepts a pre-existing System (composition use case)."""
    payload = _load()
    ctx = parse_platform_export(payload)
    sys_inst = muscadet.System(name="custom_name")
    cleanup_system.append(sys_inst)
    apply_to_system(ctx, sys_inst)
    assert "Source1" in sys_inst.comp
    assert "Sink1" in sys_inst.comp


def test_system_from_export_name_override(cleanup_system):
    payload = _load()
    system = system_from_export(payload, name="OverriddenName")
    cleanup_system.append(system)
    assert system.name() == "OverriddenName"


def test_canonical_shape_works_too(cleanup_system):
    """Test convenience : the canonical {model, kb} shape skips the
    Platform export wrapper."""
    full = _load()
    canonical = {"model": full["model"], "kb": full["kb_embedded"]}
    system = system_from_export(canonical)
    cleanup_system.append(system)
    assert "Source1" in system.comp
    assert "Sink1" in system.comp


def test_default_out_automata_on_by_default(cleanup_system):
    """The importer defaults to ``create_default_out_automata=True`` so
    every output flow gets a default ok/nok automaton (rate ``1e-100``)
    suitable for downstream failure-mode injection."""
    payload = _load()
    system = system_from_export(payload)
    cleanup_system.append(system)
    src = system.comp["Source1"]
    # ``Source`` template has one output flow ``out_a`` → exactly one
    # default automaton attached.
    assert len(src.automata()) == 1


def test_default_out_automata_off_when_requested(cleanup_system):
    """``create_default_out_automata=False`` produces a lean topology
    with no automata at all — the connectivity-audit use case."""
    payload = _load()
    system = system_from_export(
        payload, name="LeanTopo", create_default_out_automata=False
    )
    cleanup_system.append(system)
    for comp in system.comp.values():
        assert len(comp.automata()) == 0, (
            f"Component {comp.name()} unexpectedly has automata when "
            f"create_default_out_automata=False"
        )
