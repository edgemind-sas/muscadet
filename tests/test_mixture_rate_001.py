"""A mixture group's rate is a published variable a failure mode clamps (R52).

R51 copied ``flow_rate`` into the volume as a float at the pre-run step, so a
machine could be neither stopped nor failed: a fan that breaks down went on
ventilating. The rate is now ``{group}_flow_rate``, a public variable of the
consuming component created at the declared value, exactly as
``{capacity}_serve_rate_{flow}`` is for a discharge ceiling (R48). muscadet
never writes it; a mode does, by name, and the repair restores the value the
mode declares on its way back.

The montage is the room of R51: air and hydrogen pushed into one volume, a fan
drawing the mixture. What is measured is the rate the room delivers to the fan,
which is the only thing the variable is allowed to move.
"""

import cod3s
import pytest

import muscadet
from muscadet.kb.continuous import SourceContinuous  # noqa: F401

Q = 50.0
QH2 = 2.0
V = 90.0

#: The fan breaks down at this date and is repaired DOWN_FOR later.
FAILS_AT = 2.0
DOWN_FOR = 3.0

#: The engine stores a quantity in single precision.
REL = 1e-5


class MrRoom(muscadet.ObjFlow):
    """One volume over both constituents, so the draw has a composition."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
            self.add_flow_continuous_out(name=flow)
        self.add_capacity(
            name="room",
            flows=["AIR", "H2"],
            side="out",
            capacity=1e5,
            content_init={"AIR": V, "H2": 1.0},
            fill_rate="inf",
        )


class MrFan(muscadet.ObjFlow):
    """A terminal machine drawing the mixture at one volumetric rate."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        self.add_mixture_in(name="extraction", flows=["AIR", "H2"], flow_rate=Q)


def build(name):
    """Sources, room and fan, wired. The caller deletes the system."""
    system = muscadet.System(name=name)
    try:
        system.add_component(name="S_AIR", cls="SourceContinuous", flow="AIR", rate=Q)
        system.add_component(name="S_H2", cls="SourceContinuous", flow="H2", rate=QH2)
        system.add_component(name="ROOM", cls="MrRoom")
        system.add_component(name="FAN", cls="MrFan")
        for flow in ("AIR", "H2"):
            system.connect_flow(source=f"S_{flow}", target="ROOM", flow_name=flow)
            system.connect_flow(source="ROOM", target="FAN", flow_name=flow)
    except Exception:
        system.deleteSys()
        raise
    return system


def drawn(system):
    """The volume the room delivers to the fan right now, every weight at 1."""
    room = system.comp["ROOM"]
    return sum(room.flows_out[flow].var_fed.value() for flow in ("AIR", "H2"))


@pytest.fixture(scope="module")
def failed_and_repaired():
    """The fan fails at FAILS_AT and is repaired DOWN_FOR later."""
    system = build("MixtureRateMode")
    obs = {}
    try:
        fan = system.comp["FAN"]
        obs["declared"] = fan.variable("extraction_flow_rate").value()

        fan.add_atm2states(
            name="breakdown",
            occ_law_12={"cls": "delay", "time": FAILS_AT},
            effects_12=[("extraction_flow_rate", 0.0)],
            occ_law_21={"cls": "delay", "time": DOWN_FOR},
            effects_21=[("extraction_flow_rate", Q)],
        )

        system.isimu_start()
        for date, key in (
            (1.0, "running"),
            (FAILS_AT + 1.0, "failed"),
            (FAILS_AT + DOWN_FOR + 1.0, "repaired"),
        ):
            system.isimu_step_to(date, max_events=100000)
            obs[key] = drawn(system)
            obs[f"{key}_var"] = fan.variable("extraction_flow_rate").value()
        system.isimu_stop()
    finally:
        system.deleteSys()
    return obs


def test_the_rate_is_published_at_the_declared_value(failed_and_repaired):
    assert failed_and_repaired["declared"] == pytest.approx(Q)


def test_the_room_delivers_the_declared_rate_while_the_fan_runs(failed_and_repaired):
    assert failed_and_repaired["running"] == pytest.approx(Q, rel=REL)


def test_a_mode_clamping_the_rate_stops_the_draw(failed_and_repaired):
    assert failed_and_repaired["failed_var"] == 0.0
    assert failed_and_repaired["failed"] == pytest.approx(0.0, abs=1e-9)


def test_the_repair_restores_the_draw_it_declares(failed_and_repaired):
    assert failed_and_repaired["repaired_var"] == pytest.approx(Q)
    assert failed_and_repaired["repaired"] == pytest.approx(Q, rel=REL)


def test_the_kb_machine_publishes_its_rate_too():
    """The library form declares the same variable: what a platform mode clamps."""
    system = muscadet.System(name="MixtureRateKb")
    try:
        system.add_component(
            name="PUMP",
            cls="MixturePumpContinuous",
            flows=["AIR", "H2"],
            flow_rate=12.0,
            name_group="extraction",
        )
        pump = system.comp["PUMP"]
        assert pump.variable("extraction_flow_rate").value() == pytest.approx(12.0)
    finally:
        system.deleteSys()


def test_delete():
    cod3s.terminate_session()
