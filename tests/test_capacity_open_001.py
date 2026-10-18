"""A volume open to the atmosphere stays full and breathes (R53).

A real room is never full: it is open to the outside through its grilles. When
its fans draw more than arrives, outside air comes in; when a breach pushes
more in than leaves, the excess leaks out, mixed. A closed capacity can do
neither. A separate source of fresh air dilutes the room when the fan fails,
which hides the failure; a make-up carried by the fan lets a breach fill the
room until arrivals are refused.

``makeup_flow`` names the constituent the outside supplies. With
``D = sum_f w_f (in_f - out_f)``, the make-up enters at ``max(-D, 0) / w`` and
every constituent vents at ``max(D, 0) . m_f / sum_g m_g w_g``, so the volume
occupied never moves. What remains of the declared volume at t=0 is the
make-up constituent. The room has no empty/full automaton: full by
construction, it would toggle at every step around its own bound.

The montage is the room of the platform's hydrogen showcase: 100 m^3 of air, a
fan of 100 m^3/h and a natural draft of 1 m^3/h. The equilibrium share is what
arrives over what leaves.
"""

import math
import warnings

import cod3s
import pytest

import muscadet
from muscadet.kb.continuous import SourceContinuous  # noqa: F401

VOLUME = 100.0
FAN = 100.0
DRAFT = 1.0
SEEP = 0.05
BREACH = 2.0

#: The fan fails once AE1 has settled, and is repaired after the 2 % crossing.
FAILS_AT = 20.0
REPAIRED_AT = 80.0

REL = 1e-3


class OpRoom(muscadet.ObjFlow):
    """The showcase room, open to the atmosphere, air as the make-up."""

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
            content_init={"H2": 0.0},
            makeup_flow="AIR",
        )


class OpMachine(muscadet.ObjFlow):
    """A terminal extractor, optionally ordered."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        run_cond = []
        if kwargs.get("ordered"):
            self.add_flow_in(name="vent")
            run_cond = ["vent"]
        self.add_mixture_in(
            name="extraction",
            flows=["AIR", "H2"],
            flow_rate=kwargs["rate"],
            run_cond=run_cond,
        )


class OpSwitch(muscadet.ObjFlow):
    """The fan's order, at the value it is declared with."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="vent", var_prod_default=kwargs.get("on", True))


def build(name, leak, fan=FAN, ordered=False):
    """Leak, open room, fan and draft. The caller deletes the system."""
    system = muscadet.System(name=name)
    try:
        system.add_component(name="LEAK", cls="SourceContinuous", flow="H2", rate=leak)
        system.add_component(name="ROOM", cls="OpRoom")
        system.add_component(name="FAN", cls="OpMachine", rate=fan, ordered=ordered)
        system.add_component(name="DRAFT", cls="OpMachine", rate=DRAFT)
        system.connect_flow(source="LEAK", target="ROOM", flow_name="H2")
        for flow in ("AIR", "H2"):
            system.connect_flow(source="ROOM", target="FAN", flow_name=flow)
            system.connect_flow(source="ROOM", target="DRAFT", flow_name=flow)
    except Exception:
        system.deleteSys()
        raise
    return system


def snapshot(system):
    room = system.comp["ROOM"]
    capacity = room.capacities["room"]
    return {
        "share": capacity.get_quantity("H2") / capacity.get_quantity(),
        "occupied": capacity.occupied_volume(),
        "makeup": room.variable("room_makeup").value(),
        "vent": sum(room.variable(f"room_vent_{f}").value() for f in ("AIR", "H2")),
        "leak": system.comp["LEAK"].flows_out["H2"].var_fed.value(),
    }


def run(system, dates):
    """``{date: snapshot}``, then delete the system."""
    out = {}
    try:
        system.isimu_start()
        for date in dates:
            system.isimu_step_to(date, max_events=1000000)
            out[date] = snapshot(system)
        system.isimu_stop()
    finally:
        system.deleteSys()
    return out


def failing_share(t):
    """AE2 after the failure: the draft alone, from AE1's equilibrium."""
    start = SEEP / (FAN + DRAFT)
    plateau = SEEP / DRAFT
    return plateau + (start - plateau) * math.exp(-(t - FAILS_AT) * DRAFT / VOLUME)


#: When AE2 crosses 2 %, from AE1's equilibrium.
CROSSES_AT = FAILS_AT - VOLUME / DRAFT * math.log(
    (SEEP / DRAFT - 0.02) / (SEEP / DRAFT - SEEP / (FAN + DRAFT))
)


@pytest.fixture(scope="module")
def seepage():
    """AE1 then AE2: a seep, the fan failing at FAILS_AT, repaired REPAIRED_AT."""
    system = build("OpenSeepage", SEEP)
    system.comp["FAN"].add_atm2states(
        name="breakdown",
        occ_law_12={"cls": "delay", "time": FAILS_AT},
        effects_12=[("extraction_flow_rate", 0.0)],
        occ_law_21={"cls": "delay", "time": REPAIRED_AT - FAILS_AT},
        effects_21=[("extraction_flow_rate", FAN)],
    )
    return run(system, [FAILS_AT, FAILS_AT + 30.0, CROSSES_AT, REPAIRED_AT + 10.0])


def test_ae1_a_seep_settles_at_what_arrives_over_what_leaves(seepage):
    assert seepage[FAILS_AT]["share"] == pytest.approx(SEEP / (FAN + DRAFT), rel=REL)


def test_ae1_the_outside_makes_up_what_the_machines_draw_beyond_the_seep(seepage):
    assert seepage[FAILS_AT]["makeup"] == pytest.approx(FAN + DRAFT - SEEP, rel=1e-6)
    assert seepage[FAILS_AT]["vent"] == 0.0


def test_ae2_a_lost_fan_lets_the_share_rise_with_a_time_constant_of_100_h(seepage):
    date = FAILS_AT + 30.0
    assert seepage[date]["share"] == pytest.approx(failing_share(date), rel=REL)


def test_ae2_the_trip_threshold_is_crossed_near_51_h_after_the_failure(seepage):
    assert CROSSES_AT - FAILS_AT == pytest.approx(50.1, abs=0.05)
    assert seepage[CROSSES_AT]["share"] == pytest.approx(0.02, rel=REL)


def test_ae2_the_repair_brings_the_share_back_within_hours(seepage):
    assert seepage[REPAIRED_AT + 10.0]["share"] == pytest.approx(
        SEEP / (FAN + DRAFT), rel=1e-2
    )


def test_the_volume_occupied_never_moves(seepage):
    for row in seepage.values():
        assert row["occupied"] == pytest.approx(VOLUME, rel=1e-6)


def test_ae3_a_breach_settles_at_the_trip_threshold():
    rows = run(build("OpenBreach", BREACH), [30.0])
    assert rows[30.0]["share"] == pytest.approx(BREACH / (FAN + DRAFT), rel=REL)


def test_ae4_a_breach_beyond_the_extraction_vents_and_is_never_refused():
    rows = run(build("OpenOverflow", BREACH, fan=0.0), [50.0])
    row = rows[50.0]

    assert row["leak"] == pytest.approx(BREACH, rel=1e-6)
    assert row["vent"] == pytest.approx(BREACH - DRAFT, rel=1e-6)
    assert row["makeup"] == 0.0
    # Everything leaving is at the composition, so the share rises as
    # 1 - exp(-t q / V), the room renewed by the breach alone.
    assert row["share"] == pytest.approx(
        1.0 - math.exp(-50.0 * BREACH / VOLUME), rel=REL
    )


def test_ae5_a_fan_ordered_off_draws_nothing_while_the_draft_goes_on():
    system = build("OpenOrderedOff", SEEP, ordered=True)
    system.add_component(name="SW", cls="OpSwitch", on=False)
    system.connect_flow(source="SW", target="FAN", flow_name="vent")
    try:
        system.isimu_start()
        system.isimu_step_to(1.0, max_events=100000)
        room = system.comp["ROOM"]
        fan = sum(room.flows_out[f].allocated_for("FAN") or 0.0 for f in ("AIR", "H2"))
        draft = sum(
            room.flows_out[f].allocated_for("DRAFT") or 0.0 for f in ("AIR", "H2")
        )
        system.isimu_stop()
    finally:
        system.deleteSys()

    assert fan == pytest.approx(0.0, abs=1e-12)
    assert draft == pytest.approx(DRAFT, rel=1e-6)


def test_the_initial_content_is_completed_by_the_makeup():
    system = build("OpenInit", SEEP)
    try:
        capacity = system.comp["ROOM"].capacities["room"]
        assert capacity.content_init == {"AIR": VOLUME, "H2": 0.0}
        assert capacity.get_quantity("AIR") == pytest.approx(VOLUME)
    finally:
        system.deleteSys()


def test_an_open_volume_builds_no_bound_automaton():
    system = build("OpenNoBounds", SEEP)
    try:
        room = system.comp["ROOM"]
        assert room.capacities["room"].automaton is None
        assert not any(name.endswith("_bounds") for name in room.automata_d)
    finally:
        system.deleteSys()


class OpBadMakeup(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="H2")
        self.add_flow_continuous_out(name="H2")
        self.add_capacity(
            name="room", flow="H2", side="out", capacity=VOLUME, makeup_flow="AIR"
        )


class OpClaimingRoom(muscadet.ObjFlow):
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
            makeup_flow="AIR",
            fill_rate=5.0,
        )


@pytest.mark.parametrize(
    "cls, message",
    [
        ("OpBadMakeup", "does not hold"),
        ("OpClaimingRoom", "fill_rate"),
    ],
)
def test_an_open_volume_refuses_what_it_cannot_mean(cls, message):
    system = muscadet.System(name=f"Open{cls}")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(ValueError, match=message):
                system.add_component(name="ROOM", cls=cls)
    finally:
        system.deleteSys()


def test_an_open_volume_survives_the_spec_round_trip():
    system = build("OpenSpec", SEEP)
    try:
        spec = muscadet.component_spec(system.comp["ROOM"])
    finally:
        system.deleteSys()

    (entry,) = spec["capacities"]
    assert entry["makeup_flow"] == "AIR"

    system = muscadet.System(name="OpenSpecRebuilt")
    try:
        comp = muscadet.build_component(system, spec)
        assert muscadet.component_spec(comp)["capacities"] == spec["capacities"]
    finally:
        system.deleteSys()


def test_a_closed_volume_writes_no_makeup_into_its_spec():
    system = muscadet.System(name="ClosedSpec")
    try:
        comp = muscadet.build_component(
            system,
            {
                "name": "TANK",
                "flows": [
                    {"cls": "FlowContinuousIn", "name": "q"},
                    {"cls": "FlowContinuousOut", "name": "q"},
                ],
                "capacities": [
                    {"name": "tank", "flows": ["q"], "capacity": 10.0, "side": "out"}
                ],
            },
        )
        (entry,) = muscadet.component_spec(comp)["capacities"]
        assert "makeup_flow" not in entry
    finally:
        system.deleteSys()


def test_delete():
    cod3s.terminate_session()
