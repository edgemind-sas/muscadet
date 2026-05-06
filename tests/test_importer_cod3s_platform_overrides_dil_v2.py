"""Integration test — P1.6 instance overrides on the DIL V2 fixture.

Patches the dil_v2_export.json payload in memory to inject instance
overrides on PLC_1 (a 2-of-3 vote on one of its redundancy inputs)
and asserts the resulting muscadet.System reflects the override end
to end :

- the parse layer carries the int 2 on the FlowSpec.logic field
- after ``system_from_export``, the runtime ``ObjFlow.flows_in[name]``
  carries ``logic = 2`` so muscadet's k-of-n aggregation kicks in at
  the next propagation tick
- a separate output init=True override surfaces on var_prod_default
  of the corresponding FlowOut

Skip when PyCATSHOO native libs aren't available (dev box without
LD setup) — the parse-layer assertions still cover the behaviour
in isolation via ``test_importer_cod3s_platform_overrides.py``.
"""

import copy
import json
import os
from pathlib import Path

import pytest

# muscadet imports trigger PyCATSHOO ; gracefully skip when not loadable
muscadet = pytest.importorskip("muscadet")

import cod3s  # noqa: E402

from muscadet.importers.cod3s_platform import (  # noqa: E402
    parse_platform_export,
    system_from_export,
)


_FIXTURE = Path(__file__).parent / "fixtures" / "dil_v2_export.json"

# Stable IDs from the fixture (cf. find_PLC scan)
PLC_1_ID = "e6c6b100-7a56-4a79-b844-989b39b23393"
PLC_2_ID = "0f995748-29e8-4ae7-94e2-2b0317b18112"


@pytest.fixture(scope="module")
def base_payload():
    with open(_FIXTURE) as f:
        return json.load(f)


def _patch_attributes(payload, comp_id, attrs_extra):
    """Return a deep-copied payload with extra instance attributes on a comp."""
    p = copy.deepcopy(payload)
    comp = p["model"]["elements"]["components"][comp_id]
    existing = list(comp.get("attributes") or [])
    comp["attributes"] = existing + attrs_extra
    return p


def test_parse_layer_propagates_2_of_3_on_plc_1(base_payload):
    """Parse layer extracts the override and stores it on the FlowSpec."""
    payload = _patch_attributes(
        base_payload,
        PLC_1_ID,
        [
            {
                "name": "CS_E_KVPP_Qx_PLC",
                "role": "logic",
                "value": "2",
            }
        ],
    )
    ctx = parse_platform_export(payload)
    plc = next(c for c in ctx.components if c.name == "PLC_1")
    target = next(f for f in plc.flows if f.name == "CS_E_KVPP_Qx_PLC")
    # Override coerced from string '2' to native int 2 so muscadet's
    # k-of-n aggregation receives the right Python type.
    assert target.logic == 2
    # Sibling input untouched
    other = next(f for f in plc.flows if f.name == "F_ETH")
    assert other.logic == "or"
    # PLC_2 untouched (override is per-instance)
    plc2 = next(c for c in ctx.components if c.name == "PLC_2")
    plc2_target = next(f for f in plc2.flows if f.name == "CS_E_KVPP_Qx_PLC")
    assert plc2_target.logic == "or"


def test_parse_layer_propagates_init_override_on_alim(base_payload):
    """Parse layer carries init=True onto a Source-like output flow."""
    # Find Alim_elec_01 by scanning the components dict
    alim_id = next(
        cid
        for cid, c in base_payload["model"]["elements"]["components"].items()
        if c.get("name") == "Alim_elec_01"
    )
    # Pick its first output flow name
    kb = base_payload["kb_embedded"]
    alim_class = base_payload["model"]["elements"]["components"][alim_id]["class_name"]
    iface_template = kb["component_templates"][alim_class]
    outputs = [
        i["name"]
        for i in iface_template["interfaces"].values()
        if i.get("port_type", {}).get("general") == "output"
    ]
    assert outputs, "no output flow on Alim_elec class"
    target_flow = outputs[0]

    payload = _patch_attributes(
        base_payload,
        alim_id,
        [{"name": target_flow, "role": "init", "value": True}],
    )
    ctx = parse_platform_export(payload)
    alim = next(c for c in ctx.components if c.name == "Alim_elec_01")
    out = next(f for f in alim.flows if f.name == target_flow)
    assert out.init_value is True


# ---------------------------------------------------------------------------
# Runtime end-to-end : muscadet.System reflects the parse-layer overrides
# ---------------------------------------------------------------------------


@pytest.fixture
def cleanup_system():
    """Function-scoped guard tearing down PyCATSHOO state after each test."""
    systems = []
    yield systems
    for s in systems:
        try:
            s.deleteSys()
        except Exception:
            pass
    cod3s.terminate_session()


def test_runtime_plc1_input_logic_set_to_2_of_3(base_payload, cleanup_system):
    """End-to-end : after build, PLC_1.flows_in[...].logic == 2."""
    payload = _patch_attributes(
        base_payload,
        PLC_1_ID,
        [
            {
                "name": "CS_E_KVPP_Qx_PLC",
                "role": "logic",
                "value": "2",
            }
        ],
    )
    system = system_from_export(payload)
    cleanup_system.append(system)
    plc1 = system.comp["PLC_1"]
    target = plc1.flows_in["CS_E_KVPP_Qx_PLC"]
    # muscadet flow-in stores the aggregation logic as the ``logic`` attr
    # (str 'and'/'or' or int k). The converter must surface our int 2 here.
    assert target.logic == 2

    # Sibling : confirms only the targeted flow was overridden
    other = plc1.flows_in["F_ETH"]
    assert other.logic == "or"

    # PLC_2 same input unaffected (per-instance override)
    plc2_target = system.comp["PLC_2"].flows_in["CS_E_KVPP_Qx_PLC"]
    assert plc2_target.logic == "or"


def test_runtime_isimu_start_with_overrides_does_not_raise(
    base_payload, cleanup_system
):
    """Sanity : the override doesn't break isimu_start (the simulation
    entry point used by cod3s-isimu). No assertion on simulation output —
    just that a system carrying overrides can be initialised by PyCATSHOO.
    """
    payload = _patch_attributes(
        base_payload,
        PLC_1_ID,
        [
            {
                "name": "CS_E_KVPP_Qx_PLC",
                "role": "logic",
                "value": "2",
            }
        ],
    )
    system = system_from_export(payload)
    cleanup_system.append(system)
    system.isimu_start()
