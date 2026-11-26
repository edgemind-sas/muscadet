"""What muscadet does with a declaration, on a machine without an engine.

Every test here reads documents and builds nothing. The directory is what
``pytest --no-engine`` collects, in a process where PyCATSHOO cannot be
imported; it also runs in the ordinary suite, where the same statements hold
with the engine present.
"""

import copy
import json
from pathlib import Path

import pytest

from muscadet import declare
from muscadet.declare import (
    ComponentSpecError,
    SystemSpecError,
    check_system_spec,
    component_build_order,
    declared_events,
)
from muscadet.importers.cod3s_platform import parse_platform_export

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def reference():
    return json.loads((FIXTURES / "engine_free_reference.json").read_text())


def test_the_reference_declaration_is_valid(reference):
    check_system_spec(reference)


def test_a_declaration_of_time_profiles_is_valid():
    check_system_spec(
        json.loads((FIXTURES / "profile_declarations_reference.json").read_text())
    )


def test_an_unknown_key_is_refused_by_name(reference):
    reference["components"]["A"]["no_such_key"] = 1

    with pytest.raises(ComponentSpecError, match="no_such_key"):
        check_system_spec(reference)


def test_a_document_from_another_major_is_refused_with_its_version(reference):
    reference["version"] = "99.0.0"

    with pytest.raises(SystemSpecError, match="99.0.0"):
        check_system_spec(reference)


def test_a_mode_naming_a_missing_target_is_refused(reference):
    reference["components"]["X__common"]["targets"] = ["A", "Nowhere"]

    with pytest.raises(SystemSpecError, match="Nowhere"):
        check_system_spec(reference)


def test_a_mode_class_cod3s_ships_is_read_off_the_table(reference):
    """The class is not resolved: its family is enough to check the document."""
    for name in ("X__common", "A__engine", "feared"):
        cls = reference["components"][name]["cls"]
        assert cls in declare.SHIPPED_MODE_CLASSES


def test_a_removed_mode_class_is_refused_with_its_replacement(reference):
    reference["components"]["X__common"]["cls"] = "ObjFailureModeExp"

    with pytest.raises(ComponentSpecError, match="ObjFMExp"):
        check_system_spec(reference)


def test_a_malformed_controller_emission_is_refused(reference):
    broken = copy.deepcopy(reference)
    outputs = broken["components"]["CTRL"]["controls_out"]
    outputs[0]["emit"] = {"no_such_key": 1}

    with pytest.raises(ComponentSpecError, match="unknown operator"):
        check_system_spec(broken)


def test_a_malformed_logic_gate_is_refused(reference):
    reference["components"]["Gate"]["logic_kind"] = "xor"

    with pytest.raises(ComponentSpecError, match="xor"):
        check_system_spec(reference)


def test_the_events_of_a_declaration_are_read_without_building_it(reference):
    assert declared_events(reference) == ("feared",)


def test_the_build_order_is_a_property_of_the_document(reference):
    order = component_build_order(reference["components"])

    assert set(order) == set(reference["components"])
    assert order.index("A") < order.index("X__common")
    assert order.index("A") < order.index("Gate")


def test_a_platform_export_is_parsed_into_data():
    payload = json.loads((FIXTURES / "detection_chain_export.json").read_text())

    context = parse_platform_export(payload)

    assert len(context.components) == 26
    assert len(context.connections) == 177
