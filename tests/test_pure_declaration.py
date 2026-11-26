"""A run package declared from data alone, against the same package read live.

``muscadet.package_declaration`` writes the system declaration of a run
package -- a platform export and its study -- without creating one engine
object. Each scenario here reads one package by that road and by the live one
(the export materialised on the engine, the study applied on the live objects,
the system read back), and asks for the same document.

The export alone is compared on every model the suite builds
(``tests/double_reading.py``). What is specific to this file is the STUDY:
modes with their common-cause vectors, events, indicators designated by a
pattern, value changes.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from test_cod3s_platform_h2_rebuild import _continuous, h2_payload

from muscadet import declare, package_declaration
from muscadet.importers import cod3s_platform as importer
from tests import double_reading
from tests.package_models import chain_export, indicator, mode, study

ROOT = Path(__file__).resolve().parents[1]


def both_roads(export, a_study):
    """``(pure, live, modes the live road added)`` of one run package."""
    pure = package_declaration.package_declaration(export, a_study)
    live, added = double_reading.live_package_declaration(export, a_study)
    return double_reading.canonical(pure), live, added


def assert_same(pure, live):
    assert double_reading.differences(pure, live) == []


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def test_a_common_cause_of_order_three_carries_the_same_vectors():
    a_study = study(
        failure_modes=[
            mode(
                "group_loss",
                ["U0001", "U0002", "U0003"],
                occ_rates=[1e-3, 1e-4, 1e-5],
                repair_rates=[0.1, 0.1, 0.1],
            )
        ]
    )
    pure, live, added = both_roads(chain_export(3, "CommonCause"), a_study)

    assert_same(pure, live)
    assert added == 1
    declared = pure["components"]["U000X__group_loss"]
    assert declared["occ_param"] == [1e-3, 1e-4, 1e-5]
    assert declared["occ_law"] == {"cls": "exp", "rate": [1e-3, 1e-4, 1e-5]}
    assert declared["not_occ_param"] == [0.1, 0.1, 0.1]
    assert declared["drop_inactive_automata"] is False


def test_an_inactive_order_is_dropped_the_same_way():
    a_study = study(
        failure_modes=[
            mode(
                "pair_loss",
                ["U0001", "U0002"],
                occ_rates=[1e-3, 0.0],
                repair_rates=[0.1, 0.0],
            )
        ]
    )
    pure, live, _added = both_roads(chain_export(2, "DroppedOrder"), a_study)

    assert_same(pure, live)
    assert "drop_inactive_automata" not in pure["components"]["U000X__pair_loss"]
    catalogue = package_declaration.component_catalogue(pure)
    assert catalogue["U000X__pair_loss"]["states"] == [
        "occ__cc_1",
        "occ__cc_2",
        "rep__cc_1",
        "rep__cc_2",
    ]


@pytest.mark.parametrize("behaviour", ["internal", "external", "external_rep_indep"])
def test_each_behaviour_reads_the_same(behaviour):
    a_study = study(
        failure_modes=[
            mode("loss", ["U0001"], [1e-3], [0.1], behaviour=behaviour),
            mode(
                "follow",
                ["U0002"],
                [1e-3],
                [0.1],
                behaviour=behaviour,
                failure_cond=[
                    [{"attr": "occ", "obj": "U0001__loss", "ope": "==", "value": True}]
                ],
            ),
        ],
        indicators=[indicator("^U0001$", "loss", attr_type="ST")],
    )
    pure, live, added = both_roads(chain_export(2, f"Behaviour_{behaviour}"), a_study)

    assert_same(pure, live)
    assert added == 2


def test_a_delay_law_and_an_instantaneous_draw_read_the_same():
    a_study = study(
        failure_modes=[
            {
                **mode("timed", ["U0001"], [0.0], [0.0]),
                "occ_law": {"cls": "delay", "time": [40.0]},
                "occ_param_name": ["ttf"],
                "not_occ_law": {"cls": "delay", "time": [5.0]},
                "not_occ_param_name": ["ttr"],
            },
            {
                **mode("drawn", ["U0002"], [0.0], [0.0], behaviour="external"),
                "occ_law": {"cls": "inst", "prob": [0.25]},
                "occ_param_name": ["gamma"],
                "occ_parked_state": "not_occ",
                "failure_cond": [[{"attr": "sig_fed_in", "ope": "==", "value": True}]],
            },
        ]
    )
    pure, live, added = both_roads(chain_export(2, "Laws"), a_study)

    assert_same(pure, live)
    assert added == 2


def test_a_mode_that_cannot_be_declared_refuses_the_package():
    """Every undeclarable mode is named at once, and nothing is declared.

    The one place the two roads part, recorded in ``muscadet.conformance``:
    the live road logs the failure and keeps the half-built component, whose
    declaration no engine can build from.
    """
    export = chain_export(2, "BrokenMode")
    a_study = study(
        failure_modes=[
            mode("sound", ["U0001"], [1e-3], [0.1]),
            mode(
                "broken",
                ["U0002"],
                [1e-3],
                [0.1],
                occ_effects={"no_such_variable": False},
            ),
            mode("orphan", ["NOWHERE"], [1e-3], [0.1]),
        ]
    )

    with pytest.raises(package_declaration.PackageDeclarationError) as refusal:
        package_declaration.package_declaration(export, a_study)
    assert [name for name, _reason in refusal.value.refusals] == ["broken", "orphan"]
    assert "no_such_variable" in str(refusal.value)
    assert "NOWHERE" in str(refusal.value)

    live, added = double_reading.live_package_declaration(
        export, a_study, check_catalogue=False
    )
    assert added == 1
    half_built = live["components"]["U0002__broken"]
    assert half_built["occ_param"] is None and half_built["occ_param_name"] is None


def test_a_ranked_mode_beside_an_unranked_one_refuses_the_package():
    a_study = study(
        failure_modes=[
            mode("first", ["U0001"], [1e-3], [0.1], behaviour="external"),
            mode(
                "second",
                ["U0001"],
                [1e-3],
                [0.1],
                behaviour="external",
                effects_priority=1,
            ),
        ]
    )

    with pytest.raises(package_declaration.PackageDeclarationError) as refusal:
        package_declaration.package_declaration(chain_export(1, "Mixed"), a_study)
    assert [name for name, _reason in refusal.value.refusals] == ["second"]
    assert "effects_priority" in str(refusal.value)


def test_the_gap_on_an_undeclarable_mode_is_in_the_conformance_registry():
    from muscadet import conformance

    recorded = conformance.deviations(
        conformance.ENGINE_PYCATSHOO, conformance.POINT_UNDECLARABLE_MODE
    )
    assert len(recorded) == 1
    assert "package_declaration" in recorded[0].compensation


def facade_mode(cls, name, targets, failure, repair, **fields):
    """One mode of a two-state facade class, as an older study spells it."""
    return {
        "cls": cls,
        "fm_name": name,
        "targets": list(targets),
        "failure_param": list(failure),
        "repair_param": list(repair),
        "failure_effects": {"sig_fed_available_out": False},
        **fields,
    }


@pytest.mark.parametrize("cls", ["ObjFMExp", "ObjFMDelay"])
@pytest.mark.parametrize("behaviour", ["internal", "external", "external_rep_indep"])
def test_a_facade_mode_reads_the_same(cls, behaviour):
    a_study = study(
        failure_modes=[
            facade_mode(cls, "loss", ["U0001"], [0.01], [0.5], behaviour=behaviour),
            facade_mode(
                cls,
                "group",
                ["U0002", "U0003"],
                [0.01, 0.0],
                [0.5, 0.0],
                behaviour=behaviour,
                failure_cond=[
                    [{"attr": "occ", "obj": "U0001__loss", "ope": "==", "value": True}]
                ],
            ),
        ],
        indicators=[
            indicator(".*", ".*", attr_type="ST"),
            indicator("^U000X__group$", ".*"),
        ],
        attribute_overrides={
            "failure_mode_param": [
                {"fm_name": "U0001__loss", "field": "failure_param", "value": [0.02]}
            ]
        },
    )
    pure, live, added = both_roads(chain_export(3, f"Facade{cls}{behaviour}"), a_study)

    assert added == 2
    assert_same(pure, live)
    assert pure["components"]["U0001__loss"]["cls"] == cls
    assert pure["components"]["U0001__loss"]["failure_param"] == [0.02]


def test_a_facade_vector_shorter_than_its_targets_is_completed_the_same_way():
    a_study = study(
        failure_modes=[
            facade_mode("ObjFMExp", "group", ["U0001", "U0002"], [0.01], [0.5])
        ]
    )
    pure, live, added = both_roads(chain_export(2, "ShortVector"), a_study)

    assert added == 1
    assert_same(pure, live)


def test_a_mode_class_no_declaration_covers_is_refused_by_name():
    a_study = study(
        failure_modes=[facade_mode("ObjFMInst", "drawn", ["U0001"], [0.2], [0.0])]
    )

    with pytest.raises(package_declaration.PackageDeclarationError) as refusal:
        package_declaration.package_declaration(chain_export(1, "Inst"), a_study)
    assert "ObjFMInst" in str(refusal.value)


def _refused(export, a_study):
    """The refusals of the pure road, and how many modes the live road added."""
    with pytest.raises(package_declaration.PackageDeclarationError) as refusal:
        package_declaration.package_declaration(export, a_study)
    _live, added = double_reading.live_package_declaration(
        export, a_study, check_catalogue=False
    )
    return [name for name, _reason in refusal.value.refusals], added


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            {"occ_param": [0.1, None], "not_occ_param": [1.0, 1.0]},
            id="one-direction-marked-inactive",
        ),
        pytest.param(
            {
                "occ_effects": {},
                "occ_effects_trans": {"sig_is_active": False},
                "not_occ_effects_trans": {"sig_is_active": True},
            },
            id="pulsed-on-both-faces",
        ),
    ],
)
def test_a_group_mode_the_engine_refuses_is_refused(fields):
    a_study = study(
        failure_modes=[mode("m", ["U0001", "U0002"], [0.1, 0.1], [1.0, 1.0], **fields)]
    )

    assert _refused(chain_export(2, "RefusedGroup"), a_study) == (["m"], 0)


def test_an_independent_repair_needs_an_active_return_law():
    a_study = study(
        failure_modes=[
            mode("m", ["U0001"], [0.1], [0.0], behaviour="external_rep_indep")
        ]
    )

    assert _refused(chain_export(1, "NoReturn"), a_study) == (["m"], 0)


def test_a_targeted_mode_without_a_law_is_refused():
    declared = mode("m", ["U0001"], [0.1], [1.0])
    del declared["occ_law"]
    declared["occ_param"] = [0.1]

    assert _refused(chain_export(1, "NoLaw"), study(failure_modes=[declared])) == (
        ["m"],
        0,
    )


def test_two_ranked_modes_holding_two_values_at_one_rank_are_refused():
    a_study = study(
        failure_modes=[
            mode("first", ["U0001"], [0.1], [1.0], effects_priority=1),
            mode(
                "second",
                ["U0001"],
                [0.1],
                [1.0],
                effects_priority=1,
                occ_effects={"sig_fed_available_out": True},
            ),
        ]
    )

    assert _refused(chain_export(1, "SameRank"), a_study) == (["second"], 1)


@pytest.mark.parametrize(
    "probabilities, parked",
    [
        pytest.param([0.3], None, id="a-random-draw-parks-under-the-default-name"),
        pytest.param([1.0], "pk", id="a-certain-draw-parks-nowhere"),
        pytest.param([0.3, 0.0, 1.0], None, id="each-order-on-its-own"),
    ],
)
def test_the_parked_states_of_a_draw_are_the_ones_the_engine_builds(
    probabilities, parked
):
    """Every state of a drawn mode is designated the same way by a pattern."""
    targets = [f"U{index:04d}" for index in range(1, len(probabilities) + 1)]
    declared = {
        **mode("drawn", targets, [0.0], [0.0], behaviour="external"),
        "occ_law": {"cls": "inst", "prob": probabilities},
        "occ_param_name": ["gamma"],
        "not_occ_law": {"cls": "exp", "rate": [0.1] * len(targets)},
        "failure_cond": [[{"attr": "sig_fed_in", "ope": "==", "value": True}]],
    }
    if parked:
        declared["occ_parked_state"] = parked
    a_study = study(
        failure_modes=[declared], indicators=[indicator(".*", ".*", attr_type="ST")]
    )
    pure, live, added = both_roads(chain_export(len(targets), "Parked"), a_study)

    assert added == 1
    assert_same(pure, live)


# ---------------------------------------------------------------------------
# Events and indicators
# ---------------------------------------------------------------------------


def test_an_event_reads_the_same():
    a_study = study(
        events=[
            {
                "name": "chain_cut",
                "cond": [
                    [
                        {
                            "attr": "sig_fed_out",
                            "obj": "U0003",
                            "ope": "==",
                            "value": False,
                        }
                    ]
                ],
                "tempo_occ": 2.0,
            }
        ],
        indicators=[indicator("^chain_cut$", "^occ$", attr_type="ST")],
    )
    pure, live, _added = both_roads(chain_export(3, "Event"), a_study)

    assert_same(pure, live)
    assert pure["components"]["chain_cut"]["tempo_occ"] == 2.0
    assert declare.declared_events(pure) == ("chain_cut",)


def test_an_event_named_like_a_component_is_refused():
    a_study = study(
        events=[
            {
                "name": "U0001",
                "cond": [[{"attr": "sig_fed_out", "obj": "U0002", "value": True}]],
            }
        ]
    )

    with pytest.raises(package_declaration.StudyModeError, match="already exists"):
        package_declaration.package_declaration(chain_export(2, "Clash"), a_study)


@pytest.mark.parametrize(
    "fields",
    [
        {"occ_state_name": "x", "not_occ_state_name": "x"},
        {"event_aut_name": "my aut"},
    ],
)
def test_an_event_the_engine_refuses_is_refused_by_both_roads(fields):
    export = chain_export(1, "BadEvent")
    a_study = study(
        events=[
            {
                "name": "cut",
                "cond": [[{"attr": "sig_fed_out", "obj": "U0001", "value": False}]],
                **fields,
            }
        ]
    )

    with pytest.raises(package_declaration.StudyModeError):
        package_declaration.package_declaration(export, a_study)
    with pytest.raises(ValueError):
        double_reading.live_package_declaration(export, a_study)


def test_a_naming_pattern_reads_every_field_of_the_indicator():
    a_study = study(
        indicators=[
            indicator(
                "^U0001$",
                "^sig_fed_out$",
                name_pattern="{component}-{attr_type}-{stats}",
            )
        ]
    )
    pure, live, _added = both_roads(chain_export(1, "NamePattern"), a_study)

    assert_same(pure, live)
    assert pure["indicators"][0]["name"] == "U0001-VAR-['mean']"


def test_a_pattern_designating_several_variables_expands_to_the_same_list():
    a_study = study(
        indicators=[
            indicator("^U000[12]$", "sig_fed"),
            indicator(".*", "^sig_prod$", measure="sojourn-time"),
        ]
    )
    pure, live, _added = both_roads(chain_export(3, "Patterns"), a_study)

    assert_same(pure, live)
    names = [entry["name"] for entry in pure["indicators"]]
    assert "U0001_sig_fed_in_value" in names and "U0002_sig_fed_out_value" in names
    assert "SRC_sig_prod_sojourn-time" in names
    assert len(names) == 3 * 2 + 4


def test_a_named_indicator_keeps_its_last_match_on_both_roads():
    """One name for several matches: the engine keeps one, and so do we."""
    a_study = study(indicators=[indicator("^U.*$", "sig_fed_out", name="relay")])
    pure, live, _added = both_roads(chain_export(3, "NamedPattern"), a_study)

    assert_same(pure, live)
    assert [entry["component"] for entry in pure["indicators"]] == ["U0003"]


def test_a_pattern_designating_nothing_gives_the_same_answer():
    a_study = study(
        indicators=[
            indicator("^NOWHERE$", ".*"),
            indicator("^U0001$", "^no_such_variable$"),
        ]
    )
    pure, live, _added = both_roads(chain_export(1, "EmptyPattern"), a_study)

    assert_same(pure, live)
    assert pure["indicators"] == []


def test_an_attribute_type_no_engine_lists_is_refused_by_both_roads():
    a_study = study(indicators=[indicator("^U0001$", ".*", attr_type="REF")])
    export = chain_export(1, "BadAttrType")

    with pytest.raises(ValueError, match="REF not supported"):
        package_declaration.package_declaration(export, a_study)
    with pytest.raises(ValueError, match="REF not supported"):
        double_reading.live_package_declaration(export, a_study)


# ---------------------------------------------------------------------------
# Value changes
# ---------------------------------------------------------------------------


def test_a_value_change_is_found_identical_in_both_declarations():
    a_study = study(
        failure_modes=[
            mode("group_loss", ["U0001", "U0002"], [1e-3, 1e-4], [0.1, 0.1])
        ],
        attribute_overrides={
            "failure_mode_param": [
                {
                    "fm_name": "U000X__group_loss",
                    "field": "failure_param",
                    "value": [5e-3, 5e-4],
                },
                {
                    "fm_name": "U000X__group_loss",
                    "field": "repair_param",
                    "value": [0.2],
                },
            ]
        },
    )
    pure, live, _added = both_roads(chain_export(2, "ValueChange"), a_study)

    assert_same(pure, live)
    declared = pure["components"]["U000X__group_loss"]
    assert declared["occ_param"] == [5e-3, 5e-4]
    assert declared["occ_law"]["rate"] == [5e-3, 5e-4]
    # A vector of the wrong length is skipped, by both roads.
    assert declared["not_occ_param"] == [0.1, 0.1]


# ---------------------------------------------------------------------------
# Refusals of the export, and the two tables
# ---------------------------------------------------------------------------


def _unknown_class(payload):
    payload["model"]["elements"]["components"]["c1"]["class_name"] = "Missing"


def _capacity_on_a_discrete_flow(payload):
    payload["kb"]["component_templates"]["Unit"]["capacities"] = [
        {"name": "buffer", "volume": 10.0, "flows": [{"name": "sig"}]}
    ]


def _condition_on_an_unknown_flow(payload):
    unit = payload["kb"]["component_templates"]["Unit"]
    unit["interfaces"]["sig__output"]["prod_cond"] = [[{"name": "ghost"}]]


def _operand(**fields):
    def spoil(payload):
        unit = payload["kb"]["component_templates"]["Unit"]
        unit["interfaces"]["sig__output"]["prod_cond"] = [
            [{"name": "sig", "port": "in", **fields}]
        ]

    return spoil


@pytest.mark.parametrize(
    "spoil",
    [
        _unknown_class,
        _capacity_on_a_discrete_flow,
        _condition_on_an_unknown_flow,
        pytest.param(_operand(port="bogus"), id="operand-port"),
        pytest.param(_operand(negate=True, op=">", value=1), id="negated-comparison"),
        pytest.param(_operand(op="~", value=1), id="unknown-operator"),
        pytest.param(_operand(op=">"), id="comparison-without-a-value"),
    ],
)
def test_an_invalid_export_is_refused_alike_by_both_roads(spoil):
    payload = chain_export(1, "Invalid")
    spoil(payload)

    with pytest.raises(importer.Cod3sPlatformImportError) as pure:
        package_declaration.export_declaration(payload)
    with pytest.raises(importer.Cod3sPlatformImportError) as live:
        built = importer.system_from_export(payload)
        double_reading.tear_down(built)  # pragma: no cover - never built
    import cod3s

    cod3s.terminate_session()
    assert type(pure.value) is type(live.value)


def _two_volumes(capacities):
    return {
        "model": {
            "name": "TwoVolumes",
            "kb": {"name": "KB", "version": "1.0.0"},
            "elements": {
                "components": {
                    "c1": {"name": "TANK", "class_name": "Tank", "attributes": []}
                },
                "connections": {},
            },
        },
        "kb": {
            "component_templates": {
                "Tank": {
                    "interfaces": {
                        "H2__input": _continuous("H2", "input"),
                        "H2__output": _continuous("H2", "output"),
                    },
                    "capacities": capacities,
                }
            }
        },
    }


def test_one_flow_buffered_on_each_side_is_declared_whatever_the_order():
    """A volume that names no side takes the input one; the other is free."""
    upstream = {"name": "a", "volume": 6.0, "flows": [{"name": "H2"}]}
    downstream = {**upstream, "name": "b", "side": "out"}

    for capacities in ([upstream, downstream], [downstream, upstream]):
        payload = _two_volumes(capacities)
        pure = package_declaration.export_declaration(payload)
        built = importer.system_from_export(payload)  # compared by the double reading
        double_reading.tear_down(built)
        sides = {c["name"]: c["side"] for c in pure["components"]["TANK"]["capacities"]}
        assert sides == {"a": "in", "b": "out"}


@pytest.mark.parametrize("content", [{"H2": 50.0}, {"H2": -1.0}])
def test_an_initial_content_the_volume_cannot_hold_is_refused_by_both_roads(content):
    payload = _two_volumes(
        [
            {
                "name": "a",
                "volume": 6.0,
                "flows": [{"name": "H2"}],
                "content_init": content,
            }
        ]
    )

    with pytest.raises(importer.Cod3sPlatformImportError):
        package_declaration.export_declaration(payload)
    with pytest.raises(importer.Cod3sPlatformImportError):
        importer.system_from_export(payload)
    import cod3s

    cod3s.terminate_session()


def test_the_defaults_table_is_what_a_live_declaration_holds():
    """Each default of the table is the value the engine-side class fills in."""
    import muscadet

    system = muscadet.System("DefaultsTable")
    try:
        comp = declare.build_component(
            system,
            {
                "name": "ALL",
                "flows": [
                    {"cls": "FlowIn", "name": "a"},
                    {"cls": "FlowOut", "name": "b"},
                    {"cls": "FlowOutTempo", "name": "c"},
                    {"cls": "FlowOutOnTrigger", "name": "d"},
                    {"cls": "FlowContinuousIn", "name": "e"},
                    {"cls": "FlowContinuousOut", "name": "f"},
                ],
            },
        )
        flows = {flow["cls"]: flow for flow in declare.component_spec(comp)["flows"]}
    finally:
        double_reading.tear_down(system)

    #: Fields an export always decides, so the table does not carry them.
    decided = {"name", "cls", "var_prod_cond", "trigger_up", "state_enable_bkd"}
    for clsname, flow in flows.items():
        held = {key: value for key, value in flow.items() if key not in decided}
        table = {
            key: value
            for key, value in package_declaration.DECLARATION_DEFAULTS[clsname].items()
            if key not in decided
        }
        assert held == table, clsname


# ---------------------------------------------------------------------------
# No engine at all
# ---------------------------------------------------------------------------


def test_the_h2_plant_is_declared_in_a_process_without_the_engine(tmp_path):
    """The reference plant, declared where ``Pycatshoo`` cannot be imported."""
    payload = h2_payload()
    built = importer.system_from_export(payload)
    try:
        live = double_reading.canonical(declare.system_spec(built))
    finally:
        double_reading.tear_down(built)

    export_file = tmp_path / "h2_export.json"
    export_file.write_text(json.dumps(payload))
    script = textwrap.dedent("""
        import importlib.abc, json, sys

        class NoEngine(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "Pycatshoo":
                    raise ModuleNotFoundError("No module named 'Pycatshoo'", name="Pycatshoo")
                return None

        sys.meta_path.insert(0, NoEngine())
        from muscadet import package_declaration

        document = package_declaration.export_declaration(json.load(open(sys.argv[1])))
        assert "Pycatshoo" not in sys.modules
        print(json.dumps(document, sort_keys=True))
        """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(export_file)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    pure = json.loads(result.stdout.splitlines()[-1])
    assert double_reading.differences(pure, live) == []
