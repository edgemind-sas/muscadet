"""The platform importer reads mixture groups and the make-up of a volume.

A COD3S Platform library now carries a ``mixtures`` section on a class template
(R51, R52) and a ``makeup_flow`` on a capacity (R53), in the shape of muscadet's
portable declaration. This file locks three things:

* the parse layer accepts that shape and refuses, naming the class, what it
  cannot build;
* a payload of the hydrogen showcase room (a seep, an open room, a fan and a
  natural draft) builds the plant ``test_capacity_open_001`` builds in Python,
  and settles at the same share;
* the declaration muscadet writes for RAICHU carries both, so the other engine
  reads what this one ran.
"""

import math

import cod3s
import pytest

import muscadet
import muscadet.kb.continuous  # noqa: F401  -- registers the shipped classes
from muscadet import declare
from muscadet.importers import cod3s_platform
from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    _build_kb_capacities,
    _build_kb_lookup,
    _build_kb_mixtures,
    system_from_export,
)

VOLUME = 100.0
FAN = 100.0
DRAFT = 1.0
SEEP = 0.05
REL = 1e-3


def _continuous(name, direction, rate=None):
    interface = {
        "name": name,
        "port_type": {"general": direction},
        "flow_family": "continuous",
    }
    if rate is not None:
        interface["production_profile"] = {"cls": "constant", "value": rate}
    return interface


def _discrete(name, direction):
    return {"name": name, "port_type": {"general": direction}}


def _room_template(**capacity):
    entry = {
        "name": "room",
        "flows": [{"name": "AIR"}, {"name": "H2"}],
        "volume": VOLUME,
        "side": "out",
        "fill_rate": "inf",
        "makeup_flow": "AIR",
    }
    entry.update(capacity)
    return {
        "interfaces": {
            "AIR__input": _continuous("AIR", "input"),
            "H2__input": _continuous("H2", "input"),
            "AIR__output": _continuous("AIR", "output"),
            "H2__output": _continuous("H2", "output"),
        },
        "capacities": [entry],
    }


def _machine_template(rate, *, commanded=False, **group):
    interfaces = {
        "AIR__input": _continuous("AIR", "input"),
        "H2__input": _continuous("H2", "input"),
    }
    entry = {"name": "extraction", "flows": ["AIR", "H2"], "flow_rate": rate}
    if commanded:
        interfaces["vent__input"] = _discrete("vent", "input")
        entry["run_cond"] = [["vent"]]
    entry.update(group)
    return {"interfaces": interfaces, "mixtures": [entry]}


def _connection(source, target, flow):
    return {
        "component_source": source,
        "component_target": target,
        "interface_source": flow,
        "interface_target": flow,
    }


def room_payload(name, *, commanded=False):
    """The showcase room as a platform export: seep, open room, fan, draft."""
    components = {
        "id-leak": {"name": "LEAK", "class_name": "Leak", "attributes": []},
        "id-room": {"name": "ROOM", "class_name": "Room", "attributes": []},
        "id-fan": {"name": "FAN", "class_name": "Fan", "attributes": []},
        "id-draft": {"name": "DRAFT", "class_name": "Draft", "attributes": []},
    }
    connections = {"c-leak": _connection("id-leak", "id-room", "H2")}
    for flow in ("AIR", "H2"):
        connections[f"c-fan-{flow}"] = _connection("id-room", "id-fan", flow)
        connections[f"c-draft-{flow}"] = _connection("id-room", "id-draft", flow)
    return {
        "model": {
            "name": name,
            "kb": {"name": "VentKB", "version": "1.0.0"},
            "elements": {"components": components, "connections": connections},
        },
        "kb": {
            "component_templates": {
                "Leak": {
                    "interfaces": {"H2__output": _continuous("H2", "output", SEEP)}
                },
                "Room": _room_template(),
                "Fan": _machine_template(FAN, commanded=commanded),
                "Draft": _machine_template(DRAFT),
            }
        },
    }


def _parse(templates):
    kb = {"component_templates": templates}
    lookup = _build_kb_lookup(kb)
    capacities = _build_kb_capacities(kb, lookup)
    return capacities, _build_kb_mixtures(kb, lookup, capacities)


@pytest.fixture
def cleanup():
    systems: list = []
    yield systems
    for system in systems:
        try:
            system.deleteSys()
        except Exception:
            pass
    cod3s.terminate_session()


# ---------------------------------------------------------------------------
# Markers: the contract the platform probes before emitting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("marker", ["_SUPPORTS_MIXTURES", "_SUPPORTS_MAKEUP_FLOW"])
def test_the_markers_are_declared_true(marker):
    assert getattr(cod3s_platform, marker) is True


# ---------------------------------------------------------------------------
# Parse layer
# ---------------------------------------------------------------------------


class TestParseMakeup:
    def test_the_makeup_reaches_the_capacity_spec(self):
        capacities, _ = _parse({"Room": _room_template()})
        assert capacities["Room"][0].makeup_flow == "AIR"

    def test_a_closed_volume_carries_none(self):
        template = _room_template()
        del template["capacities"][0]["makeup_flow"]
        capacities, _ = _parse({"Room": template})
        assert capacities["Room"][0].makeup_flow is None

    def test_a_makeup_the_volume_does_not_hold_is_refused_naming_the_class(self):
        template = _room_template(flows=[{"name": "H2"}])
        with pytest.raises(Cod3sPlatformImportError, match="'Room'.*does not hold"):
            _parse({"Room": template})


class TestParseMixtures:
    def test_every_declared_field_reaches_the_spec(self):
        _, mixtures = _parse({"Fan": _machine_template(FAN, commanded=True)})
        group = mixtures["Fan"][0]
        assert group.name == "extraction"
        assert group.flows == ("AIR", "H2")
        assert group.flow_rate == FAN
        assert [[op.name for op in clause] for clause in group.run_cond] == [["vent"]]
        # Resolved here, as a discharge command's mode is, never left to muscadet.
        assert group.run_cond_inner_mode == "and"

    def test_an_uncommanded_group_carries_no_command(self):
        _, mixtures = _parse({"Draft": _machine_template(DRAFT)})
        assert mixtures["Draft"][0].run_cond == ()
        assert mixtures["Draft"][0].run_cond_inner_mode is None

    def test_a_class_without_the_section_has_no_group(self):
        _, mixtures = _parse({"Room": _room_template()})
        assert mixtures["Room"] == ()

    @pytest.mark.parametrize(
        ("group", "message"),
        [
            ({"flows": ["CO2"]}, "not a continuous input"),
            ({"flows": []}, "lists the continuous inputs"),
            ({"flows": ["AIR", "AIR"]}, "drawn twice"),
            ({"flow_rate": -1.0}, "positive or zero"),
            ({"flow_rate": "inf"}, "finite"),
            ({"flow_rate": None}, "is required"),
            ({"run_cond_inner_mode": "or"}, "no 'run_cond'"),
            ({"typo": 1}, "unknown declaration key"),
        ],
    )
    def test_what_cannot_be_built_is_refused_naming_the_class(self, group, message):
        with pytest.raises(Cod3sPlatformImportError, match=message) as exc:
            _parse({"Fan": _machine_template(FAN, **group)})
        assert "'Fan'" in str(exc.value)

    def test_a_discrete_input_cannot_be_drawn(self):
        template = _machine_template(FAN, commanded=True, flows=["vent"])
        del template["mixtures"][0]["run_cond"]
        with pytest.raises(Cod3sPlatformImportError, match="not a continuous input"):
            _parse({"Fan": template})

    def test_a_group_declared_twice_is_refused(self):
        template = _machine_template(FAN)
        template["mixtures"].append(dict(template["mixtures"][0]))
        with pytest.raises(Cod3sPlatformImportError, match="declared twice"):
            _parse({"Fan": template})

    def test_a_section_that_is_not_a_list_is_refused(self):
        template = _machine_template(FAN)
        template["mixtures"] = {"extraction": template["mixtures"][0]}
        with pytest.raises(Cod3sPlatformImportError, match="is a LIST"):
            _parse({"Fan": template})


# ---------------------------------------------------------------------------
# Runtime: the showcase room built from a platform payload
# ---------------------------------------------------------------------------


def _share(system):
    capacity = system.comp["ROOM"].capacities["room"]
    return capacity.get_quantity("H2") / capacity.get_quantity()


def test_the_imported_room_settles_where_the_python_room_does(cleanup):
    system = system_from_export(room_payload("PlatformOpenRoom"))
    cleanup.append(system)
    room = system.comp["ROOM"]
    assert room.capacities["room"].makeup_flow == "AIR"
    assert set(system.comp["FAN"].mixtures) == {"extraction"}

    system.isimu_start()
    system.isimu_step_to(20.0, max_events=1000000)
    share = _share(system)
    makeup = room.variable("room_makeup").value()
    occupied = room.capacities["room"].occupied_volume()
    system.isimu_stop()

    assert share == pytest.approx(SEEP / (FAN + DRAFT), rel=REL)
    assert makeup == pytest.approx(FAN + DRAFT - SEEP, rel=1e-6)
    assert occupied == pytest.approx(VOLUME, rel=1e-6)


def test_an_imported_command_holds_the_fan_while_its_order_is_absent(cleanup):
    # The order is a discrete input nobody feeds, so it reads false: the fan
    # draws nothing and the draft alone takes the room, which then rises.
    system = system_from_export(room_payload("PlatformOrderedRoom", commanded=True))
    cleanup.append(system)
    system.isimu_start()
    system.isimu_step_to(10.0, max_events=1000000)
    fan_rate = system.comp["FAN"].mixtures["extraction"].rate()
    share = _share(system)
    system.isimu_stop()

    assert fan_rate == 0.0
    expected = SEEP / DRAFT * (1.0 - math.exp(-10.0 * DRAFT / VOLUME))
    assert share == pytest.approx(expected, rel=1e-2)


def test_the_declaration_written_for_raichu_carries_both(cleanup):
    system = system_from_export(room_payload("PlatformRoomDeclared", commanded=True))
    cleanup.append(system)
    document = declare.system_spec(system)
    components = document["components"]

    room = components["ROOM"]["capacities"][0]
    assert room["makeup_flow"] == "AIR"

    fan = components["FAN"]["mixtures"][0]
    assert fan["name"] == "extraction"
    assert list(fan["flows"]) == ["AIR", "H2"]
    assert fan["flow_rate"] == FAN
    assert fan["run_cond"]
