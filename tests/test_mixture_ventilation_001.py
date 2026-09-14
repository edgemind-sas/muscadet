"""A ventilated mixture: one volumetric rate, composed at the volume (R51).

Issue #4. A multi-flow capacity could not be ventilated: what arrived in a step
left in the same step without entering the composition, and there was no common
rate on the way out. The rate now belongs to a MACHINE and the composition stays
in the volume.

The physics this file measures against, for a room of ``V`` receiving air at
``Q`` and hydrogen at ``q`` while ONE extractor moves ``R = Q`` of the mixture::

    out_AIR = R (1 - x)      out_H2 = R x        x = m_H2 / (m_AIR + m_H2)

    dm_AIR/dt = Q - R(1-x)   dm_H2/dt = q - R x  dM/dt = Q + q - R = q

    supply open   x(t) = q/(Q+q) . [1 - (V/(V+qt))^((Q+q)/q)]
    supply cut    x(t) = x0 . exp(-R t / M0)        M0 constant

The total is NOT conserved here: it grows by ``q``. The share plateau
``q/(Q+q)`` is the ratio of the INLET rates and does not depend on ``R``.
"""

import math

import cod3s
import pytest

import muscadet
from muscadet.kb.continuous import (  # noqa: F401
    MixturePumpContinuous,
    SourceContinuous,
)

V = 90.0
Q = 50.0
QH2 = 2.0

#: Where phase 2 starts: the content the plateau composition would put there.
H2_INIT = QH2 / (Q + QH2) * V

#: The engine stores a quantity in single precision (README), so nothing here is
#: asserted tighter than seven significant digits.
REL = 1e-5


class MxRoom(muscadet.ObjFlow):
    """One capacity over both constituents, so the volume has a composition.

    ``fill_rate=inf`` is what makes the sources deliver their declared rate: the
    supply is pushed into the room, not pulled out of it, and R36 spells that
    "whatever the producer delivers".
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
            self.add_flow_continuous_out(name=flow)
        self.add_capacity(
            name="room",
            flows=[
                {"name": "AIR", "weight": kwargs.get("w_air", 1.0)},
                {"name": "H2", "weight": kwargs.get("w_h2", 1.0)},
            ],
            side="out",
            capacity=kwargs.get("volume", 1e5),
            content_init={"AIR": V, "H2": kwargs.get("h2_init", 0.0)},
            fill_rate="inf",
            **{key: kwargs[key] for key in ("serve_rate",) if key in kwargs},
        )


class MxFan(muscadet.ObjFlow):
    """The machine: one volumetric rate over both constituents."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        self.add_mixture_in(
            name="extraction",
            flows=["AIR", "H2"],
            flow_rate=kwargs.get("rate", Q),
        )


class MxOutlet(muscadet.ObjFlow):
    """The 5.1.0 shape: two independent per-flow demands."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(
                name=flow, var_demand_default=kwargs.get("demand", Q / 2.0)
            )


def build(consumer_cls, supply=QH2, h2_init=0.0, room=None, consumer=None):
    """Sources, room and consumer, wired. The caller deletes the system."""
    room = dict(room or {})
    consumer = dict(consumer or {})

    system = muscadet.System(name="MixtureVentilation")

    # Deleted on the way out of a FAILED build: the engine holds one system at a
    # time, so a helper raising half way would make every later test in this
    # file fail on construction, and none of them would name the cause.
    try:
        system.add_component(name="S_AIR", cls="SourceContinuous", flow="AIR", rate=Q)
        system.add_component(
            name="S_H2", cls="SourceContinuous", flow="H2", rate=supply
        )
        system.add_component(name="ROOM", cls="MxRoom", h2_init=h2_init, **room)
        system.add_component(name="LOAD", cls=consumer_cls.__name__, **consumer)

        for flow in ("AIR", "H2"):
            system.connect_flow(source=f"S_{flow}", target="ROOM", flow_name=flow)
            system.connect_flow(source="ROOM", target="LOAD", flow_name=flow)
    except Exception:
        system.deleteSys()
        raise

    return system


def trace(system, horizon=10.0, step=1.0):
    """Read the room at each observation point, then delete the system."""
    rows = []
    try:
        capacity = system.comp["ROOM"].capacities["room"]
        system.isimu_start()
        k = 1
        while k * step <= horizon + 1e-9:
            system.isimu_step_to(k * step, max_events=100000)
            air = capacity.get_quantity("AIR")
            h2 = capacity.get_quantity("H2")
            rows.append(
                {
                    "t": system.currentTime(),
                    "AIR": air,
                    "H2": h2,
                    "total": air + h2,
                    "share": h2 / (air + h2),
                    "out_AIR": capacity.get_outflow("AIR"),
                    "out_H2": capacity.get_outflow("H2"),
                    "capability_H2": system.comp["ROOM"]
                    .flows_out["H2"]
                    .get_capability(),
                }
            )
            k += 1
        system.isimu_stop()
    finally:
        system.deleteSys()
    return rows


def integrate(weights, supply, h2_init, rate, horizon=10.0, dt=2e-4, step=1.0):
    """The reference: a well-mixed volume drained at ONE volumetric rate."""
    held = {"AIR": V, "H2": h2_init}
    inflow = {"AIR": Q, "H2": supply}
    date, due, rows = 0.0, step, []

    while date <= horizon + 1e-9:
        occupied = sum(held[f] * weights[f] for f in held)
        out = {f: (rate * held[f] / occupied if occupied > 0 else 0.0) for f in held}

        if date >= due - 1e-9:
            total = sum(held.values())
            rows.append(
                {"t": date, "H2": held["H2"], "share": held["H2"] / total, "out": out}
            )
            due += step

        for f in held:
            held[f] += (inflow[f] - out[f]) * dt
        date += dt

    return rows


# ----------------------------------------------------------------------
# What it does, against the closed forms
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def phase_one():
    """Supply open. Data, not a live system: the system is deleted in trace()."""
    return trace(build(MxFan))


@pytest.fixture(scope="module")
def phase_two():
    """Supply cut, starting from the content phase 1 tends to."""
    return trace(build(MxFan, supply=0.0, h2_init=H2_INIT))


def test_the_share_follows_its_closed_form_while_the_supply_is_open(phase_one):
    """x(t) = q/(Q+q) . [1 - (V/(V+qt))^((Q+q)/q)], and it was pinned at zero."""
    exponent = (Q + QH2) / QH2

    for row in phase_one:
        total = V + QH2 * row["t"]
        expected = QH2 / (Q + QH2) * (1.0 - (V / total) ** exponent)

        assert row["share"] == pytest.approx(expected, rel=REL)
        assert row["H2"] == pytest.approx(expected * total, rel=REL)

    # The defect this closes: 5.1.0 reported exactly zero at every step.
    assert phase_one[-1]["share"] > 0.038


def test_the_volume_grows_by_the_hydrogen_it_is_fed(phase_one):
    """dM/dt = Q + q - R = q, because the extractor moves R = Q of the mixture."""
    for row in phase_one:
        assert row["total"] == pytest.approx(V + QH2 * row["t"], rel=REL)


def test_the_two_outlet_rates_are_one_degree_of_freedom(phase_one):
    """out_AIR = R(1-x) and out_H2 = R x: their sum is the declared rate."""
    for row in phase_one:
        assert row["out_AIR"] + row["out_H2"] == pytest.approx(Q, rel=REL)
        assert row["out_H2"] == pytest.approx(Q * row["share"], rel=REL)


def test_the_share_decays_exponentially_once_the_supply_is_cut(phase_two):
    """x(t) = x0 exp(-R t / M0), with the time constant M0/R and not V/R."""
    m0 = V + H2_INIT
    x0 = H2_INIT / m0

    for row in phase_two:
        assert row["share"] == pytest.approx(x0 * math.exp(-Q * row["t"] / m0), rel=REL)

    # The total is constant here: what leaves equals what arrives.
    for row in phase_two:
        assert row["total"] == pytest.approx(m0, rel=REL)


def test_the_capability_announces_the_composed_share(phase_one):
    """R-20's rule: every bound is computed by the function production honours.

    A room announcing ``serve_limit`` would tell everything downstream that it
    can pour, when what leaves it is a fraction of one volumetric rate.
    """
    for row in phase_one:
        assert row["capability_H2"] == pytest.approx(row["out_H2"], rel=1e-3)


# ----------------------------------------------------------------------
# The weighted split
# ----------------------------------------------------------------------


def test_a_flow_rate_is_split_at_the_weighted_share():
    """out_f = R . m_f / sum_g (m_g . w_g), so sum_f out_f . w_f is exactly R."""
    weights = {"AIR": 1.0, "H2": 0.5}
    rows = trace(build(MxFan, room={"w_h2": weights["H2"]}))
    reference = integrate(weights, QH2, 0.0, Q)

    for row, expected in zip(rows, reference):
        assert row["share"] == pytest.approx(expected["share"], rel=1e-3)
        assert row["H2"] == pytest.approx(expected["H2"], rel=1e-3)

        # The invariant, which needs no reference at all: a volumetric machine
        # moves a VOLUME, and that volume is the declared rate.
        moved = row["out_AIR"] * weights["AIR"] + row["out_H2"] * weights["H2"]
        assert moved == pytest.approx(Q, rel=REL)


def test_the_rate_is_one_for_the_whole_group_not_one_per_flow():
    """What the key name does not say, pinned so a refactor cannot lose it.

    ``flow_rate`` reads like a rate per flow beside ``flows=[...]``, and it is
    not: it is ONE rate for the group. Two flows at ``flow_rate=50`` move 50 of
    the mixture, not 100. The invariant below is the difference between the two
    readings, and it is what the whole notion is for.
    """
    rows = trace(build(MxFan))

    for row in rows:
        assert row["out_AIR"] + row["out_H2"] == pytest.approx(Q, rel=REL)
        assert row["out_AIR"] + row["out_H2"] != pytest.approx(2.0 * Q, rel=1e-3)


def test_a_composed_share_above_the_declared_rate_is_still_delivered():
    """A weight below 1 makes sum_g m_g w_g smaller than the raw total.

    The share is then ABOVE the volumetric rate the machine published on each
    flow, and capping it at that figure would quietly serve less than the volume
    released. Measured on a room holding hydrogen alone at ``weight 0.5``: the
    share is twice the rate.
    """
    weights = {"AIR": 1.0, "H2": 0.5}
    system = build(MxFan, supply=QH2, h2_init=0.0, room={"w_h2": weights["H2"]})
    try:
        capacity = system.comp["ROOM"].capacities["room"]
        system.isimu_start()

        # Put the room on hydrogen alone, which no trajectory of this model
        # reaches: the point is the arithmetic of the share, not a scenario.
        capacity.var_qty["AIR"].setValue(0.0)
        capacity.var_qty["H2"].setValue(100.0)

        # occupied = 100 * 0.5 = 50, so the share is 50 * 100 / 50 = 100 = 2 R.
        assert capacity.occupied_volume() == pytest.approx(50.0)
        assert capacity.mixture_share("H2") == pytest.approx(2.0 * Q)
        system.isimu_stop()
    finally:
        system.deleteSys()


def test_an_empty_volume_serves_nothing_and_fills_first():
    """No composition, nothing to compose: what arrives accumulates for a step.

    The alternative is the short-circuit this whole notion removes, so an empty
    volume filling before it ventilates is the behaviour, not a rounding.
    """
    system = build(MxFan)
    try:
        capacity = system.comp["ROOM"].capacities["room"]
        system.isimu_start()
        for flow in ("AIR", "H2"):
            capacity.var_qty[flow].setValue(0.0)

        assert capacity.occupied_volume() == pytest.approx(0.0)
        assert capacity.mixture_share("AIR") == 0.0
        assert capacity.mixture_share("H2") == 0.0
        system.isimu_stop()
    finally:
        system.deleteSys()


def test_a_discharge_ceiling_still_caps_a_mixture():
    """R48 is not suspended by R51: the ceiling caps what LEAVES, per flow."""
    system = build(MxFan, room={"serve_rate": 10.0})
    try:
        rows = None
        capacity = system.comp["ROOM"].capacities["room"]
        system.isimu_start()
        system.isimu_step_to(1.0, max_events=100000)
        rows = {
            "AIR": capacity.get_outflow("AIR"),
            "H2": capacity.get_outflow("H2"),
        }
        system.isimu_stop()
    finally:
        system.deleteSys()

    assert rows["AIR"] == pytest.approx(10.0, rel=1e-6)
    assert rows["H2"] <= 10.0 + 1e-9


# ----------------------------------------------------------------------
# What a model written before this notion still does
# ----------------------------------------------------------------------


def test_a_volume_drawn_per_flow_is_left_exactly_as_it_was():
    """The 5.1.0 behaviour, unchanged: the transit short-circuit stands.

    It is correct for a mono-flow buffer, it is what every existing model rests
    on, and it is what makes this notion cost nothing to anybody who does not
    declare a machine. Numbers measured on 5.1.0 before any of this landed.
    """
    rows = trace(build(MxOutlet))

    assert rows[-1]["H2"] == pytest.approx(0.0, abs=1e-9)
    assert rows[-1]["AIR"] == pytest.approx(340.0, rel=1e-6)
    assert rows[-1]["out_AIR"] == pytest.approx(25.0, rel=1e-6)
    assert rows[-1]["out_H2"] == pytest.approx(QH2, rel=1e-6)


# ----------------------------------------------------------------------
# The declaration refuses what it cannot draw
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"flows": [], "flow_rate": 1.0}, "at least one flow"),
        ({"flows": ["AIR", "AIR"], "flow_rate": 1.0}, "must be distinct"),
        ({"flows": ["AIR"], "flow_rate": -1.0}, "zero or positive"),
        ({"flows": ["AIR"], "flow_rate": math.inf}, "must be finite"),
    ],
)
def test_a_malformed_group_is_refused_by_name(kwargs, message):
    with pytest.raises(ValueError, match=message):
        muscadet.MixtureIn(name="g", **kwargs)


def test_a_group_naming_something_it_cannot_draw_is_refused():
    """Four ways a group names a flow it has no business drawing."""

    class MxBadFlow(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_in(name="AIR")
            self.add_flow_out(name="alarm")
            self.add_mixture_in(name="g", flows=["alarm"], flow_rate=1.0)

    class MxRuleClash(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_in(name="AIR")
            self.add_flow_continuous_out(name="z")
            self.add_rules(
                name="burn", rules=[{"cons": {"AIR": 1.0}, "prod": {"z": 1.0}}]
            )
            self.add_mixture_in(name="g", flows=["AIR"], flow_rate=1.0)

    class MxTwoGroups(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_in(name="AIR")
            self.add_mixture_in(name="g1", flows=["AIR"], flow_rate=1.0)
            self.add_mixture_in(name="g2", flows=["AIR"], flow_rate=2.0)

    class MxNearCapacity(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_in(name="AIR")
            self.add_capacity(name="hopper", flow="AIR", side="in", capacity=10.0)
            self.add_mixture_in(name="g", flows=["AIR"], flow_rate=1.0)

    class MxPassThrough(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_in(name="AIR")
            self.add_flow_continuous_out(name="AIR")
            self.add_mixture_in(name="g", flows=["AIR"], flow_rate=1.0)

    for cls, message in (
        (MxBadFlow, "not a continuous input flow"),
        (MxRuleClash, "consumed by rule set"),
        (MxTwoGroups, "already drawn by group"),
        (MxNearCapacity, "buffered by a capacity"),
        (MxPassThrough, "also a continuous OUTPUT"),
    ):
        system = muscadet.System(name="MixtureRefusal")
        try:
            with pytest.raises(ValueError, match=message):
                system.add_component(name="C", cls=cls.__name__)
        finally:
            system.deleteSys()


# ----------------------------------------------------------------------
# The pre-run resolution refuses what no volume can compose for
# ----------------------------------------------------------------------


class MxPipe(muscadet.ObjFlow):
    """A pass-through with no capacity at all."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
            self.add_flow_continuous_out(name=flow)


class MxTwoVolumes(muscadet.ObjFlow):
    """One capacity per constituent: two compositions, so none."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
            self.add_flow_continuous_out(name=flow)
            self.add_capacity(
                name=f"tank_{flow}",
                flow=flow,
                side="out",
                capacity=1e4,
                content_init={flow: 10.0},
            )


def refused(build_system, message):
    """The system is built, its start refused, and the refusal repeats.

    The repetition is the point: ``System.prerun`` sets its done flag only after
    the step returns, and the resolution registers no equation, so a model
    refused once is refused identically at the next entry point rather than
    running with no sweep at all.
    """
    system = build_system()
    try:
        with pytest.raises(muscadet.MixtureGroupError, match=message):
            system.isimu_start()
        with pytest.raises(muscadet.MixtureGroupError, match=message):
            system.isimu_start()
    finally:
        system.deleteSys()


def test_a_group_whose_flows_come_from_two_producers_is_refused():
    def make():
        system = muscadet.System(name="MixtureTwoProducers")
        system.add_component(name="S_AIR", cls="SourceContinuous", flow="AIR", rate=Q)
        system.add_component(name="S_H2", cls="SourceContinuous", flow="H2", rate=QH2)
        system.add_component(name="FAN", cls="MxFan")
        for flow in ("AIR", "H2"):
            system.connect_flow(source=f"S_{flow}", target="FAN", flow_name=flow)
        return system

    refused(make, "several producers")


def test_a_group_drawing_a_producer_with_no_volume_is_refused():
    def make():
        system = muscadet.System(name="MixtureNoVolume")
        system.add_component(name="S_AIR", cls="SourceContinuous", flow="AIR", rate=Q)
        system.add_component(name="S_H2", cls="SourceContinuous", flow="H2", rate=QH2)
        system.add_component(name="PIPE", cls="MxPipe")
        system.add_component(name="FAN", cls="MxFan")
        for flow in ("AIR", "H2"):
            system.connect_flow(source=f"S_{flow}", target="PIPE", flow_name=flow)
            system.connect_flow(source="PIPE", target="FAN", flow_name=flow)
        return system

    refused(make, "without a capacity behind that output")


def test_a_group_spanning_two_volumes_of_one_producer_is_refused():
    def make():
        system = muscadet.System(name="MixtureTwoVolumes")
        system.add_component(name="S_AIR", cls="SourceContinuous", flow="AIR", rate=Q)
        system.add_component(name="S_H2", cls="SourceContinuous", flow="H2", rate=QH2)
        system.add_component(name="TANKS", cls="MxTwoVolumes")
        system.add_component(name="FAN", cls="MxFan")
        for flow in ("AIR", "H2"):
            system.connect_flow(source=f"S_{flow}", target="TANKS", flow_name=flow)
            system.connect_flow(source="TANKS", target="FAN", flow_name=flow)
        return system

    refused(make, "several volumes")


def test_a_volume_serving_a_group_and_somebody_else_is_refused():
    def make():
        system = build(MxFan)
        system.add_component(name="TAP", cls="MxOutlet", demand=1.0)
        for flow in ("AIR", "H2"):
            system.connect_flow(source="ROOM", target="TAP", flow_name=flow)
        return system

    refused(make, "also serves")


def test_a_second_machine_on_the_same_flows_is_refused():
    """Caught as a second CONSUMER, which is what it is on each of those flows."""

    def make():
        system = build(MxFan)
        system.add_component(name="FAN2", cls="MxFan")
        for flow in ("AIR", "H2"):
            system.connect_flow(source="ROOM", target="FAN2", flow_name=flow)
        return system

    refused(make, "also serves")


class MxThreeRoom(muscadet.ObjFlow):
    """One volume over three constituents, so two groups can share it."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2", "CO2"):
            self.add_flow_continuous_in(name=flow)
            self.add_flow_continuous_out(name=flow)
        self.add_capacity(
            name="room",
            flows=["AIR", "H2", "CO2"],
            side="out",
            capacity=1e5,
            content_init={"AIR": V, "H2": 1.0, "CO2": 1.0},
            fill_rate="inf",
        )


class MxCO2Fan(muscadet.ObjFlow):
    """A machine drawing the third constituent alone."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="CO2")
        self.add_mixture_in(name="scrub", flows=["CO2"], flow_rate=1.0)


def test_two_groups_over_disjoint_flows_of_one_volume_are_refused():
    """Nothing arbitrates two volumetric rates against one composition.

    The flows are disjoint, so the exclusivity rule above passes: this is the
    refusal that catches what it does not.
    """

    def make():
        system = muscadet.System(name="MixtureTwoGroups")
        try:
            system.add_component(name="ROOM", cls="MxThreeRoom")
            system.add_component(name="FAN", cls="MxFan")
            system.add_component(name="SCRUB", cls="MxCO2Fan")
            for flow in ("AIR", "H2"):
                system.connect_flow(source="ROOM", target="FAN", flow_name=flow)
            system.connect_flow(source="ROOM", target="SCRUB", flow_name="CO2")
        except Exception:
            system.deleteSys()
            raise
        return system

    refused(make, "already drawn by")


def test_a_group_whose_flow_is_not_connected_is_refused():
    def make():
        system = muscadet.System(name="MixtureUnwired")
        system.add_component(name="S_AIR", cls="SourceContinuous", flow="AIR", rate=Q)
        system.add_component(name="ROOM", cls="MxRoom")
        system.add_component(name="FAN", cls="MxFan")
        system.connect_flow(source="S_AIR", target="ROOM", flow_name="AIR")
        system.connect_flow(source="ROOM", target="FAN", flow_name="AIR")
        return system

    refused(make, "nothing is connected to H2")


# ----------------------------------------------------------------------
# The KB form and the declaration held as data
# ----------------------------------------------------------------------


def test_the_kb_machine_ventilates_the_room():
    """MixturePumpContinuous(ports='in') is the extractor, declared in one line."""
    system = muscadet.System(name="MixtureKB")
    system.add_component(name="S_AIR", cls="SourceContinuous", flow="AIR", rate=Q)
    system.add_component(name="S_H2", cls="SourceContinuous", flow="H2", rate=QH2)
    system.add_component(name="ROOM", cls="MxRoom")
    system.add_component(
        name="FAN",
        cls="MixturePumpContinuous",
        flows=["AIR", "H2"],
        flow_rate=Q,
    )
    for flow in ("AIR", "H2"):
        system.connect_flow(source=f"S_{flow}", target="ROOM", flow_name=flow)
        system.connect_flow(source="ROOM", target="FAN", flow_name=flow)

    rows = trace(system)

    exponent = (Q + QH2) / QH2
    total = V + QH2 * rows[-1]["t"]
    expected = QH2 / (Q + QH2) * (1.0 - (V / total) ** exponent)
    assert rows[-1]["share"] == pytest.approx(expected, rel=REL)


def test_the_kb_machine_refuses_to_carry_its_draw_onward():
    """Measured before the refusal landed: a pump at rate 50 behind a load asking
    5 drew 49.16 of air, delivered 5, and 44.16 per unit of time entered no
    balance. A machine that cannot place what it draws destroys it."""
    system = muscadet.System(name="MixtureKBBoth")
    try:
        with pytest.raises(ValueError, match="not available"):
            system.add_component(
                name="PUMP",
                cls="MixturePumpContinuous",
                flows=["AIR", "H2"],
                flow_rate=Q,
                ports="both",
            )
    finally:
        system.deleteSys()


def test_the_kb_machine_refuses_an_unknown_port_shape():
    system = muscadet.System(name="MixtureKBPorts")
    try:
        with pytest.raises(ValueError, match="ports must be one of"):
            system.add_component(
                name="FAN",
                cls="MixturePumpContinuous",
                flows=["AIR"],
                flow_rate=1.0,
                ports="sideways",
            )
    finally:
        system.deleteSys()


def test_a_group_survives_the_spec_round_trip():
    """A declaration this module cannot dump is a declaration silently lost."""
    system = muscadet.System(name="MixtureSpec")
    try:
        comp = muscadet.build_component(
            system,
            {
                "name": "FAN",
                "flows": [
                    {"cls": "FlowContinuousIn", "name": "AIR"},
                    {"cls": "FlowContinuousIn", "name": "H2"},
                ],
                "mixtures": [
                    {
                        "name": "extraction",
                        "flows": ["AIR", "H2"],
                        "flow_rate": 50.0,
                    }
                ],
            },
        )
        spec = muscadet.component_spec(comp)

        assert spec["mixtures"] == [
            {"name": "extraction", "flows": ["AIR", "H2"], "flow_rate": 50.0}
        ]

        # A spec a strict reader can carry: no non-finite literal anywhere.
        import json

        json.dumps(spec)
    finally:
        system.deleteSys()


def test_delete():
    cod3s.terminate_session()
