"""A volume drawn as a mixture asks upstream for what it releases (R51, R53).

A room claiming nothing for itself (``fill_rate=0``, "a pure pass-through
buffer, it never stocks up") asks its supplies for exactly what leaves it. A
machine drawing it at ``R`` releases each constituent at its composed share,
``R . m_f / sum_g m_g w_g``. Up to 5.17.0 the room asked each supply for the
machine's whole rate instead, which over-asks: a room drawn at 10 asked 10 of
air AND 10 of hydrogen, twice the volume it releases, and filled. The other
engine reading the same declaration asked the composed share, so the two
diverged on every such room.
"""

import cod3s
import pytest

import muscadet
from muscadet.kb.continuous import SourceContinuous  # noqa: F401

AIR0 = 90.0
H20 = 10.0
RATE = 10.0
SUPPLY = 50.0


class RdRoom(muscadet.ObjFlow):
    """A closed room claiming nothing for itself."""

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
            content_init={"AIR": AIR0, "H2": H20},
        )


class RdFan(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        self.add_mixture_in(name="extraction", flows=["AIR", "H2"], flow_rate=RATE)


@pytest.fixture(scope="module")
def delivered():
    system = muscadet.System(name="RoomDemand")
    try:
        for flow in ("AIR", "H2"):
            system.add_component(
                name=f"S_{flow}", cls="SourceContinuous", flow=flow, rate=SUPPLY
            )
        system.add_component(name="ROOM", cls="RdRoom")
        system.add_component(name="FAN", cls="RdFan")
        for flow in ("AIR", "H2"):
            system.connect_flow(source=f"S_{flow}", target="ROOM", flow_name=flow)
            system.connect_flow(source="ROOM", target="FAN", flow_name=flow)
        system.isimu_start()
        system.isimu_step_to(1.0, max_events=100000)
        out = {
            flow: system.comp[f"S_{flow}"].flows_out[flow].var_fed.value()
            for flow in ("AIR", "H2")
        }
        out["held"] = system.comp["ROOM"].capacities["room"].get_quantity()
        system.isimu_stop()
    finally:
        system.deleteSys()
    return out


def test_the_room_asks_each_supply_its_composed_share(delivered):
    share = H20 / (AIR0 + H20)
    assert delivered["AIR"] == pytest.approx(RATE * (1.0 - share), rel=1e-5)
    assert delivered["H2"] == pytest.approx(RATE * share, rel=1e-5)


def test_a_pass_through_room_does_not_fill(delivered):
    assert delivered["held"] == pytest.approx(AIR0 + H20, rel=1e-5)


def test_delete():
    cod3s.terminate_session()
