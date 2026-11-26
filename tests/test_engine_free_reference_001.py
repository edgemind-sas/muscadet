"""The reference declaration the engine-free checks read.

``tests/engine_free/`` validates a system declaration on a machine where no
engine is installed, so it cannot BUILD the system it checks: it reads
``tests/fixtures/engine_free_reference.json``. This module is what keeps that
file honest. It builds the system by hand, on the engine, and compares its
declaration with the stored one.

The system holds one component of each kind a declaration describes: two flow
components, a common-cause failure mode of the ``cod3s.ObjFM`` family, a mode
of the generic two-state engine, an event, a logic gate and a controller.

A deliberate change regenerates the file with ``MUSCADET_UPDATE_FIXTURES=1``.
"""

import json
import os
from pathlib import Path

import cod3s
import pytest

import muscadet
from muscadet.declare import system_spec

FIXTURE = Path(__file__).parent / "fixtures" / "engine_free_reference.json"

#: Set to regenerate the fixture rather than compare with it.
UPDATE_ENV = "MUSCADET_UPDATE_FIXTURES"


def build_reference(system):
    for name in ("A", "B"):
        comp = system.add_component(name=name, cls="ObjFlow", partial_init=True)
        comp.add_flow_out(name="f", var_prod_default=True)
        comp.set_flows()

    system.add_component(
        cls="ObjFMExp",
        fm_name="common",
        targets=["A", "B"],
        failure_effects={"f_fed_available_out": False},
        failure_param=[1e-3, 1e-4],
        repair_param=[0.1, 0.1],
    )
    system.add_component(
        cls="ObjMode2S",
        mode_name="engine",
        targets=["A"],
        occ_law={"cls": "exp", "rate": 0.1},
        not_occ_law={"cls": "delay", "time": 3},
        occ_effects={"f_fed_available_out": False},
    )
    system.add_component(
        name="feared",
        cls="ObjEvent",
        cond=[[{"attr": "f_fed_out", "obj": "A", "value": False}]],
        tempo_occ=2,
    )
    system.add_component(
        name="Gate",
        cls="ObjLogicGate",
        kind="and",
        cond=[
            [{"obj": "A", "attr": "f_fed_out", "value": True}],
            [{"obj": "B", "attr": "f_fed_out", "value": True}],
        ],
        out_elements=["g"],
    )
    system.add_component(
        name="CTRL",
        cls="ObjCtrl",
        controls_in=[{"name": "level"}],
        controls_out=[{"name": "go", "kind": "bool"}],
    )
    return system


@pytest.fixture(scope="module")
def declared():
    system = build_reference(muscadet.System(name="EngineFreeReference"))
    spec = json.loads(json.dumps(system_spec(system)))
    yield spec
    system.deleteSys()
    cod3s.terminate_session()


def test_the_stored_reference_is_the_declaration_of_the_system_built_by_hand(declared):
    if os.environ.get(UPDATE_ENV):
        FIXTURE.write_text(json.dumps(declared, indent=2, sort_keys=True) + "\n")

    assert json.loads(FIXTURE.read_text()) == declared


def test_the_reference_holds_one_component_of_each_kind(declared):
    kinds = {spec.get("kind", "flow") for spec in declared["components"].values()}

    assert kinds == {"flow", "two_state_mode", "logic_gate", "controller"}
