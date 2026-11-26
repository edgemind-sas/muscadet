"""A run package declared from data, on a machine without an engine.

``muscadet.package_declaration`` creates no engine object, so everything here
runs under ``pytest --no-engine``. What is asserted is what the document says;
that it is the document a live system reads back is the business of
``tests/test_pure_declaration.py`` and of the double reading, which need the
engine.
"""

import json
from pathlib import Path

import pytest

from muscadet import declare, package_declaration
from muscadet.importers.cod3s_platform import Cod3sPlatformImportError
from tests.package_models import chain_export, indicator, mode, study

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_an_export_is_declared_and_the_document_is_valid():
    payload = json.loads((FIXTURES / "minimal_export.json").read_text())

    document = package_declaration.export_declaration(payload)

    declare.check_system_spec(document)
    assert document["version"] == declare.SYSTEM_SPEC_VERSION
    assert sorted(document["components"]) == ["Sink1", "Source1"]
    assert document["connections"] == [
        {
            "source": "Source1",
            "source_box": "out_a_out",
            "target": "Sink1",
            "target_box": "out_a_in",
            "flow": "out_a",
        }
    ]


def test_the_detection_chain_is_declared_whole():
    payload = json.loads((FIXTURES / "detection_chain_export.json").read_text())

    document = package_declaration.export_declaration(payload)

    declare.check_system_spec(document)
    assert len(document["components"]) == len(
        payload["model"]["elements"]["components"]
    )


def test_a_study_adds_its_modes_its_events_and_its_indicators():
    document = package_declaration.package_declaration(
        chain_export(3),
        study(
            failure_modes=[
                mode("group_loss", ["U0001", "U0002", "U0003"], [1e-3] * 3, [0.1] * 3)
            ],
            events=[
                {
                    "name": "cut",
                    "cond": [[{"attr": "sig_fed_out", "obj": "U0003", "value": False}]],
                }
            ],
            indicators=[indicator("^U.*$", "^sig_fed_out$")],
        ),
    )

    declare.check_system_spec(document)
    assert declare.declared_events(document) == ("cut",)
    assert "U000X__group_loss" in document["components"]
    assert [entry["component"] for entry in document["indicators"]] == [
        "U0001",
        "U0002",
        "U0003",
    ]


def test_the_export_is_left_as_it_was_handed():
    payload = chain_export(2)
    before = json.dumps(payload, sort_keys=True)

    package_declaration.package_declaration(
        payload, study(failure_modes=[mode("loss", ["U0001"], [1e-3], [0.1])])
    )

    assert json.dumps(payload, sort_keys=True) == before


def test_the_catalogue_names_what_a_study_may_designate():
    document = package_declaration.package_declaration(
        chain_export(1),
        study(
            failure_modes=[mode("loss", ["U0001"], [1e-3], [0.1], behaviour="external")]
        ),
    )

    catalogue = package_declaration.component_catalogue(document)

    assert catalogue["U0001"] == {
        "variables": [
            "sig_fed_available_out",
            "sig_fed_in",
            "sig_fed_out",
            "sig_is_active",
            "sig_prod",
            "sig_prod_available",
        ],
        "states": ["loss__occ", "loss__rep", "sig_nok", "sig_ok"],
        "automata": ["U0001_sig", "loss"],
    }
    assert catalogue["U0001__loss"] == {
        "variables": ["ctrl_loss_U0001", "lambda", "mu"],
        "states": ["occ", "rep"],
        "automata": ["loss"],
    }


def test_a_mode_that_cannot_be_declared_refuses_the_package_by_name():
    with pytest.raises(package_declaration.PackageDeclarationError) as refusal:
        package_declaration.package_declaration(
            chain_export(1),
            study(failure_modes=[mode("lost", ["NOWHERE"], [1e-3], [0.1])]),
        )

    assert refusal.value.refusals[0][0] == "lost"
    assert "NOWHERE" in str(refusal.value)


def test_an_invalid_export_is_refused_as_the_importer_refuses_it():
    payload = chain_export(1)
    payload["model"]["elements"]["components"]["c1"]["class_name"] = "Missing"

    with pytest.raises(Cod3sPlatformImportError):
        package_declaration.export_declaration(payload)
