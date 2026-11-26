"""The double reading itself: it is installed, it compares, and it refuses.

``tests/double_reading.py`` reads every export the suite materialises by two
roads and fails the test that built it when they differ. These tests are about
the instrument, so that a suite in which it had quietly stopped comparing could
not stay green: it is wrapped around the importer, it counts what it compares,
and a difference it should catch is caught.
"""

import copy

import pytest
from test_cod3s_platform_h2_rebuild import h2_payload

from muscadet import declare, package_declaration
from muscadet.importers import cod3s_platform as importer
from tests import double_reading
from tests.package_models import chain_export


@pytest.fixture
def system():
    """A live chain of three units, dropped after the test."""
    built = importer.system_from_export(chain_export(3, name="DoubleReading"))
    yield built
    double_reading.tear_down(built)


def test_every_materialised_export_is_compared():
    before = double_reading.COMPARED["exports"]
    built = importer.system_from_export(chain_export(2, name="DoubleReadingBis"))
    double_reading.tear_down(built)

    assert double_reading.COMPARED["exports"] == before + 1


def test_the_importer_is_read_by_both_roads():
    assert getattr(importer.apply_to_system, "double_reading", False)
    assert double_reading.ORIGINAL["apply_to_system"] is not importer.apply_to_system


def test_a_continuous_plant_with_a_capacity_a_recipe_and_a_derating_reads_the_same():
    """The H2 plant: a continuous family, volumes, a rule set, a derating."""
    payload = h2_payload()
    pure = package_declaration.export_declaration(payload)
    kinds = {
        section
        for comp in pure["components"].values()
        for section in ("capacities", "rules", "deratings")
        if comp.get(section)
    }
    assert kinds == {"capacities", "rules", "deratings"}

    before = double_reading.COMPARED["exports"]
    built = importer.system_from_export(payload)
    double_reading.tear_down(built)
    assert double_reading.COMPARED["exports"] == before + 1


def test_a_field_the_pure_road_gets_wrong_is_refused(system):
    """An unexplained difference fails the comparison: nothing switches on it."""
    pure = package_declaration.export_declaration(chain_export(3, name="DoubleReading"))
    tampered = copy.deepcopy(pure)
    tampered["components"]["U0002"]["flows"][0]["logic"] = "and"

    double_reading.compare(pure, system)
    with pytest.raises(double_reading.DoubleReadingMismatch) as refusal:
        double_reading.compare(tampered, system)
    assert "U0002" in str(refusal.value)
    assert "logic" in str(refusal.value)


def test_a_missing_component_is_refused(system):
    pure = package_declaration.export_declaration(chain_export(3, name="DoubleReading"))
    del pure["components"]["U0003"]

    with pytest.raises(double_reading.DoubleReadingMismatch):
        double_reading.compare(pure, system)


def test_a_name_the_catalogue_misses_is_refused(system, monkeypatch):
    """The catalogue is compared to what the engine exposes, name for name."""
    pure = package_declaration.export_declaration(chain_export(3, name="DoubleReading"))
    complete = package_declaration.component_catalogue

    def short_catalogue(declaration):
        catalogue = complete(declaration)
        catalogue["U0001"]["variables"].remove("sig_fed_out")
        return catalogue

    monkeypatch.setattr(package_declaration, "component_catalogue", short_catalogue)

    with pytest.raises(double_reading.DoubleReadingMismatch) as refusal:
        double_reading.compare(pure, system)
    assert "catalogue" in str(refusal.value)


def test_the_catalogue_lists_what_each_kind_of_component_exposes(system):
    """Per component, the catalogue of the live document is the engine's list."""
    document = declare.system_spec(system)

    assert double_reading.canonical(
        package_declaration.component_catalogue(document)
    ) == double_reading.canonical(double_reading.live_catalogue(system))


def test_a_system_that_already_held_components_is_left_alone():
    """The pure road describes an export, not what was built before it."""
    import muscadet

    host = muscadet.System("DoubleReadingHost")
    try:
        host.add_component(cls="ObjFlow", name="ALREADY_THERE")
        before = double_reading.COMPARED["exports"]
        importer.apply_to_system(importer.parse_platform_export(chain_export(1)), host)
        assert double_reading.COMPARED["exports"] == before
    finally:
        double_reading.tear_down(host)
