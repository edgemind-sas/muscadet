"""End-to-end test for the ``logic_inner_mode`` default.

Locks the contract that ``prod_cond=[[A], [B]]`` (no explicit
``logic_inner_mode`` on the interface) is evaluated as ``A OR B`` by
muscadet at runtime — matching the COD3S Platform KB Editor UI
semantics (groups separated by "OU").

Pre-fix the importer used muscadet's native default
``logic_inner_mode='or'`` which inverted the meaning to ``A AND B``
and silently broke every redundant-source modelling pattern.

Test strategy: build a minimal 3-component synthetic system (2 sources
+ 1 sink) directly via the platform-export shape, run it through
``system_from_export``, drive ``isimu_start`` and check the sink's
output ``var_fed`` against the truth table of OR semantics.
"""

from __future__ import annotations

import pytest

# muscadet pulls PyCATSHOO; gracefully skip when the lib isn't loadable
muscadet = pytest.importorskip("muscadet")

import cod3s  # noqa: E402

from muscadet.importers.cod3s_platform import system_from_export  # noqa: E402


def _make_payload(*, source_a_init: bool, source_b_init: bool, system_name: str):
    """Synthesise a minimal platform-export payload with 2 sources fanning
    into 1 sink whose output is conditional on either source.

    The KB declares ``prod_cond=[["A"], ["B"]]`` on the sink output, with
    NO explicit ``logic_inner_mode`` — exercising the importer default.
    The sources' init values drive whether their ``var_fed`` starts true.
    """
    return {
        "export_version": "1.0.0",
        "model": {
            "name": system_name,
            "kb": {"name": "MinimalKB", "version": "0.0.1"},
            "elements": {
                "components": {
                    "src-a": {
                        "name": "SourceA",
                        "class_name": "Source",
                        "attributes": [
                            {"name": "A", "role": "prod_init", "value": source_a_init}
                        ],
                    },
                    "src-b": {
                        "name": "SourceB",
                        "class_name": "Source",
                        "attributes": [
                            {"name": "A", "role": "prod_init", "value": source_b_init}
                        ],
                    },
                    "sink": {
                        "name": "Sink",
                        "class_name": "Sink",
                    },
                },
                "connections": {
                    "c-a": {
                        "component_source": "src-a",
                        "interface_source": "A",
                        "component_target": "sink",
                        "interface_target": "A",
                    },
                    "c-b": {
                        "component_source": "src-b",
                        "interface_source": "A",
                        "component_target": "sink",
                        "interface_target": "B",
                    },
                },
            },
        },
        "kb_embedded": {
            "export_version": "3.0.0",
            "name": "MinimalKB",
            "version": "0.0.1",
            "type": "MUSCADET",
            "component_templates": {
                "Source": {
                    "interfaces": {
                        "A": {
                            "name": "A",
                            "port_type": {"general": "output"},
                            # No prod_cond → unconditional source-like flow
                        }
                    }
                },
                "Sink": {
                    "interfaces": {
                        "A": {"name": "A", "port_type": {"general": "input"}},
                        "B": {"name": "B", "port_type": {"general": "input"}},
                        "Y": {
                            "name": "Y",
                            "port_type": {"general": "output"},
                            # prod_cond is [[A], [B]] — two sub-lists.
                            # NO logic_inner_mode → exercises the importer
                            # default. Should evaluate as A OR B.
                            "prod_cond": [["A"], ["B"]],
                        },
                    }
                },
            },
        },
    }


@pytest.fixture
def cleanup_system():
    systems = []
    yield systems
    for s in systems:
        try:
            s.deleteSys()
        except Exception:
            pass
    cod3s.terminate_session()


@pytest.mark.parametrize(
    "source_a, source_b, expected_y",
    [
        (False, False, False),  # neither source: Y off
        (True, False, True),    # source A only: Y on (would be False under AND)
        (False, True, True),    # source B only: Y on (would be False under AND)
        (True, True, True),     # both sources: Y on (always)
    ],
    ids=["neither", "only-A", "only-B", "both"],
)
def test_default_logic_inner_mode_yields_or_semantics(
    cleanup_system, source_a, source_b, expected_y
):
    """Truth table: with ``prod_cond=[[A], [B]]`` and no explicit
    ``logic_inner_mode``, Sink.Y must follow ``A OR B`` semantics.

    The "only-A" and "only-B" rows are the discriminating cases: under
    the legacy muscadet default (inner_mode='or' = outer-AND), these
    would be False and the test would fail.
    """
    payload = _make_payload(
        source_a_init=source_a,
        source_b_init=source_b,
        system_name=f"DefaultOR_{source_a}_{source_b}",
    )
    system = system_from_export(payload)
    cleanup_system.append(system)
    system.isimu_start()
    sink_y = system.comp["Sink"].flows_out["Y"]
    assert sink_y.var_fed.value() is expected_y, (
        f"Expected Y={expected_y} for (A={source_a}, B={source_b}) under OR "
        f"semantics ; got {sink_y.var_fed.value()} — possible regression to "
        f"AND outer (legacy muscadet default)."
    )


def test_explicit_or_inner_mode_yields_and_semantics(cleanup_system):
    """Explicit ``logic_inner_mode='or'`` opts out of the platform
    default and yields muscadet's outer-AND / inner-OR. Sink.Y must
    require BOTH A and B fed. The "only-A" case becomes False.
    """
    payload = _make_payload(
        source_a_init=True,
        source_b_init=False,
        system_name="ExplicitAND_only_A",
    )
    payload["kb_embedded"]["component_templates"]["Sink"]["interfaces"]["Y"][
        "logic_inner_mode"
    ] = "or"
    system = system_from_export(payload)
    cleanup_system.append(system)
    system.isimu_start()
    assert system.comp["Sink"].flows_out["Y"].var_fed.value() is False, (
        "Under explicit logic_inner_mode='or' (outer-AND), Y should require "
        "both inputs to be fed; A alone must not feed Y."
    )
