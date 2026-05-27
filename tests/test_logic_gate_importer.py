"""Integration test — COD3S Platform importer synthesises ObjLogicGate.

This is the Phase C end-to-end proof: a canonical ``{model, kb}`` payload
carrying logic-gate components (``metadata.logic_gate``) with a polymorphic
joker port is run through :func:`system_from_export`, and the resulting
:class:`muscadet.System` must materialise working ``ObjLogicGate``
components.

Covered:

* **heterogeneous input flow names** — a single gate aggregates sources
  whose output flows are named ``fa`` / ``fb`` / ``fc``;
* **k-of-n** across those heterogeneous sources (k=2 True, k=3 False on
  the fed channel);
* **OR / AND** combinational logic;
* **heterogeneous output flow names** — a gate's result feeds downstream
  inputs named differently from the gate's ``out`` port and from each
  other (``redundancy`` / ``backup`` / ``ca`` / ``cb``);
* **broadcast** — one gate feeding two distinct targets;
* **check_fed channel switch** — a gate reading the availability channel
  (``check_fed=false``) sees the sources available even when their
  production is off.

Requires PyCATSHOO native libraries at import time (skipped otherwise).
"""

import cod3s
import pytest

muscadet = pytest.importorskip("muscadet")

from muscadet.importers.cod3s_platform import system_from_export  # noqa: E402


# ---------------------------------------------------------------------------
# Payload builders (canonical {model, kb} shape)
# ---------------------------------------------------------------------------


def _source_template(flow: str) -> dict:
    return {
        "name": f"Src_{flow}",
        "description": "",
        "interfaces": {
            f"{flow}__output": {
                "name": flow,
                "port_type": {"general": "output"},
                "prod_cond": [],
            }
        },
    }


def _sink_template(class_name: str, input_name: str) -> dict:
    return {
        "name": class_name,
        "description": "",
        "interfaces": {
            f"{input_name}__input": {
                "name": input_name,
                "port_type": {"general": "input"},
            }
        },
    }


def _gate_template(class_name: str, kind: str) -> dict:
    return {
        "name": class_name,
        "description": "",
        "interfaces": {
            "in__input": {
                "name": "in",
                "port_type": {"general": "input"},
                "metadata": {"wildcard": "true"},
            },
            "out__output": {
                "name": "out",
                "port_type": {"general": "output"},
                "metadata": {"wildcard": "true"},
            },
        },
        "metadata": {"logic_gate": kind},
    }


def _source_component(name: str, flow: str, on: bool) -> dict:
    # role=prod_init drives the FlowOut var_prod_default so the source's
    # fed_out reflects the desired ON/OFF state at isimu_start.
    return {
        "name": name,
        "class_name": f"Src_{flow}",
        "attributes": [{"name": flow, "role": "prod_init", "value": on}],
    }


def _gate_component(name: str, class_name: str, *, check_fed: bool = True, k=None) -> dict:
    attrs = [{"name": "check_fed", "value": check_fed}]
    if k is not None:
        attrs.append({"name": "k", "value": k})
    return {"name": name, "class_name": class_name, "attributes": attrs}


def _build_payload() -> dict:
    component_templates = {
        "Src_fa": _source_template("fa"),
        "Src_fb": _source_template("fb"),
        "Src_fc": _source_template("fc"),
        "logic_or": _gate_template("logic_or", "or"),
        "logic_and": _gate_template("logic_and", "and"),
        "logic_kn": _gate_template("logic_kn", "k"),
        # Distinct sink classes, each with a differently-named input flow,
        # to exercise heterogeneous gate-output wiring.
        "SinkCa": _sink_template("SinkCa", "ca"),
        "SinkCb": _sink_template("SinkCb", "cb"),
        "SinkDa": _sink_template("SinkDa", "da"),
        "SinkRed": _sink_template("SinkRed", "redundancy"),
        "SinkBak": _sink_template("SinkBak", "backup"),
        "SinkAvail": _sink_template("SinkAvail", "av"),
    }

    components = {
        # Sources: fa ON, fb OFF, fc ON (fed channel).
        "id-sa": _source_component("SA", "fa", True),
        "id-sb": _source_component("SB", "fb", False),
        "id-sc": _source_component("SC", "fc", True),
        # Gates.
        "id-gor": _gate_component("GOR", "logic_or"),
        "id-gand": _gate_component("GAND", "logic_and"),
        "id-gk2": _gate_component("GK2", "logic_kn", k=2),
        "id-gk3": _gate_component("GK3", "logic_kn", k=3),
        # Availability-channel gate: k=3 but reads is_available (all 3
        # sources are available even though SB's production is off).
        "id-gk3av": _gate_component("GK3AV", "logic_kn", check_fed=False, k=3),
        # Sinks.
        "id-orca": {"name": "SinkOrCa", "class_name": "SinkCa", "attributes": []},
        "id-orcb": {"name": "SinkOrCb", "class_name": "SinkCb", "attributes": []},
        "id-and": {"name": "SinkAnd", "class_name": "SinkDa", "attributes": []},
        "id-k2": {"name": "SinkK2", "class_name": "SinkRed", "attributes": []},
        "id-k3": {"name": "SinkK3", "class_name": "SinkBak", "attributes": []},
        "id-k3av": {"name": "SinkK3Av", "class_name": "SinkAvail", "attributes": []},
    }

    def _conn(cid, src, sif, tgt, tif):
        return {
            cid: {
                "component_source": src,
                "interface_source": sif,
                "component_target": tgt,
                "interface_target": tif,
            }
        }

    connections = {}
    # Each gate's joker input aggregates the 3 heterogeneous sources.
    gate_ids = {
        "GOR": "id-gor",
        "GAND": "id-gand",
        "GK2": "id-gk2",
        "GK3": "id-gk3",
        "GK3AV": "id-gk3av",
    }
    cc = 0
    for gname, gid in gate_ids.items():
        for sid, sflow in (("id-sa", "fa"), ("id-sb", "fb"), ("id-sc", "fc")):
            cc += 1
            connections.update(_conn(f"c{cc}", sid, sflow, gid, "in"))
    # Gate outputs to heterogeneously-named sink inputs. GOR broadcasts to two.
    cc += 1
    connections.update(_conn(f"c{cc}", "id-gor", "out", "id-orca", "ca"))
    cc += 1
    connections.update(_conn(f"c{cc}", "id-gor", "out", "id-orcb", "cb"))
    cc += 1
    connections.update(_conn(f"c{cc}", "id-gand", "out", "id-and", "da"))
    cc += 1
    connections.update(_conn(f"c{cc}", "id-gk2", "out", "id-k2", "redundancy"))
    cc += 1
    connections.update(_conn(f"c{cc}", "id-gk3", "out", "id-k3", "backup"))
    cc += 1
    connections.update(_conn(f"c{cc}", "id-gk3av", "out", "id-k3av", "av"))

    return {
        "model": {
            "name": "LogicGateModel",
            "kb": {"name": "GateKB", "version": "0.0.1"},
            "elements": {"components": components, "connections": connections},
        },
        "kb": {
            "name": "GateKB",
            "version": "0.0.1",
            "component_templates": component_templates,
            "interface_templates": {},
        },
    }


@pytest.fixture(scope="module")
def the_system():
    system = system_from_export(_build_payload())
    yield system
    try:
        system.deleteSys()
    except Exception:
        pass
    cod3s.terminate_session()


def _fed(system, sink: str, flow: str) -> bool:
    return system.comp[sink].flows_in[flow].var_fed.value()


def test_gates_instantiated_as_objlogicgate(the_system):
    for gname in ("GOR", "GAND", "GK2", "GK3", "GK3AV"):
        assert gname in the_system.comp
        assert type(the_system.comp[gname]).__name__ == "ObjLogicGate"


def test_or_heterogeneous_sources_true(the_system):
    the_system.isimu_start()
    try:
        # OR(fa=ON, fb=OFF, fc=ON) = True.
        assert _fed(the_system, "SinkOrCa", "ca") is True
        assert _fed(the_system, "SinkOrCb", "cb") is True  # broadcast
    finally:
        the_system.isimu_stop()


def test_and_false_when_one_source_off(the_system):
    the_system.isimu_start()
    try:
        assert _fed(the_system, "SinkAnd", "da") is False  # fb is OFF
    finally:
        the_system.isimu_stop()


def test_kofn_threshold_on_fed_channel(the_system):
    the_system.isimu_start()
    try:
        assert _fed(the_system, "SinkK2", "redundancy") is True  # 2 fed >= 2
        assert _fed(the_system, "SinkK3", "backup") is False  # 2 fed < 3
    finally:
        the_system.isimu_stop()


def test_check_fed_false_reads_availability_channel(the_system):
    the_system.isimu_start()
    try:
        # check_fed=False reads is_available: all 3 sources are available
        # (no failure) even though SB's production is off, so k=3 holds.
        assert _fed(the_system, "SinkK3Av", "av") is True
    finally:
        the_system.isimu_stop()


def test_delete(the_system):
    # Teardown is handled by the fixture; this keeps a stable last test.
    assert the_system is not None
