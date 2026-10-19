"""What a volume drawn as a mixture asks upstream (R51, R53).

A room asks each supply for what leaves it of that constituent, plus what it
claims for itself (``fill_rate``). A machine drawing it at ``R`` releases each
constituent at its composed share, ``R . m_f / sum_g m_g w_g``. Up to 5.17.0
the room asked each supply for the machine's whole rate instead: a room drawn
at 10 asked 10 of air AND 10 of hydrogen on top of its claim, while the other
engine reading the same declaration asked the composed share.

A FED room claiming nothing for itself (``fill_rate=0``) is refused (5.18.0):
asking only what it releases at its own composition, it would never admit a
constituent it does not hold and never start when empty. A room nothing feeds
keeps the default and simply empties.
"""

import cod3s
import pytest

import muscadet
from muscadet.kb.continuous import SourceContinuous  # noqa: F401
from muscadet.mixture import MixtureGroupError

AIR0 = 90.0
H20 = 10.0
RATE = 10.0
SUPPLY = 50.0
CLAIM = 1.0
VOLUME = 1e5


class RdRoom(muscadet.ObjFlow):
    """A closed room with room to spare, whose claim the test chooses."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
            self.add_flow_continuous_out(name=flow)
        self.add_capacity(
            name="room",
            flows=["AIR", "H2"],
            side="out",
            capacity=VOLUME,
            content_init={"AIR": AIR0, "H2": H20},
            fill_rate=kwargs.get("fill_rate", CLAIM),
        )


class RdFan(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        self.add_mixture_in(name="extraction", flows=["AIR", "H2"], flow_rate=RATE)


def _build(name, fill_rate, fed=True):
    system = muscadet.System(name=name)
    if fed:
        for flow in ("AIR", "H2"):
            system.add_component(
                name=f"S_{flow}", cls="SourceContinuous", flow=flow, rate=SUPPLY
            )
    system.add_component(name="ROOM", cls="RdRoom", fill_rate=fill_rate)
    system.add_component(name="FAN", cls="RdFan")
    for flow in ("AIR", "H2"):
        if fed:
            system.connect_flow(source=f"S_{flow}", target="ROOM", flow_name=flow)
        system.connect_flow(source="ROOM", target="FAN", flow_name=flow)
    return system


@pytest.fixture(scope="module")
def delivered():
    system = _build("RoomDemand", CLAIM)
    try:
        system.isimu_start()
        system.isimu_step_to(1.0, max_events=100000)
        out = {
            flow: system.comp[f"S_{flow}"].flows_out[flow].var_fed.value()
            for flow in ("AIR", "H2")
        }
        room = system.comp["ROOM"].capacities["room"]
        out["held"] = room.get_quantity()
        out["share"] = room.get_quantity("H2") / out["held"]
        system.isimu_stop()
    finally:
        system.deleteSys()
    return out


def test_the_room_asks_each_supply_its_composed_share_plus_its_claim(delivered):
    # The composition at the instant of the reading: the room grows by its
    # claim on each constituent, so the hydrogen share drifts from 10 %.
    share = delivered["share"]
    assert delivered["AIR"] == pytest.approx(RATE * (1.0 - share) + CLAIM, rel=1e-4)
    assert delivered["H2"] == pytest.approx(RATE * share + CLAIM, rel=1e-4)


def test_the_room_grows_by_its_claim_alone(delivered):
    assert delivered["held"] == pytest.approx(AIR0 + H20 + 2 * CLAIM, rel=1e-3)


def test_a_fed_room_claiming_nothing_is_refused():
    system = _build("RoomRefused", 0.0)
    try:
        with pytest.raises(MixtureGroupError, match="fill_rate=0"):
            system.isimu_start()
    finally:
        system.deleteSys()


def test_a_room_nothing_feeds_keeps_the_default_and_empties():
    system = _build("RoomUnfed", 0.0, fed=False)
    try:
        system.isimu_start()
        system.isimu_step_to(1.0, max_events=100000)
        held = system.comp["ROOM"].capacities["room"].get_quantity()
        system.isimu_stop()
    finally:
        system.deleteSys()
    assert held == pytest.approx(AIR0 + H20 - RATE, rel=1e-5)


def test_delete():
    cod3s.terminate_session()
