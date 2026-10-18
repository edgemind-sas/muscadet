"""Several mixture machines draw one volume, each at the composition (R52).

A ventilated room has a fan AND a natural draft: two extractors, two commands,
two ways to fail. R51 refused a second group on a volume, because nothing
arbitrated two volumetric rates against one composition. Nothing needs to:
each machine takes its own rate at the volume's composition, the draws add up,
and when the volume cannot serve the sum the proportional allocation already
serves each machine in proportion to what it asked for.

Three refusals keep the right bounded:

* a group must draw EVERY constituent of its volume, or what it leaves out
  would be drawn by a machine that is not wired to it;
* a volume drawn by several groups must allocate proportionally, or the
  pro-rata above is not what happens;
* two groups of one component on one volume share flows, which the
  declaration already refuses.
"""

import cod3s
import pytest

import muscadet
from muscadet.kb.continuous import SourceContinuous  # noqa: F401
from muscadet.mixture import MixtureGroupError

Q = 50.0
QH2 = 2.0
V = 90.0

FAN = 30.0
DRAFT = 1.0

HORIZON = 10.0

REL = 1e-5


class MgRoom(muscadet.ObjFlow):
    """One volume over two constituents."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        allocation = kwargs.get("allocation", {})
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
            self.add_flow_continuous_out(name=flow, **allocation)
        self.add_capacity(
            name="room",
            flows=["AIR", "H2"],
            side="out",
            capacity=1e5,
            content_init={"AIR": V, "H2": kwargs.get("h2_init", 1.0)},
            fill_rate="inf",
            **{key: kwargs[key] for key in ("serve_rate",) if key in kwargs},
        )


class MgMachine(muscadet.ObjFlow):
    """A terminal extractor at the rate it is given."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        self.add_mixture_in(
            name="extraction", flows=["AIR", "H2"], flow_rate=kwargs["rate"]
        )


def build(name, supply=True, room=None):
    """Sources, room, fan and draft, wired. The caller deletes the system."""
    system = muscadet.System(name=name)
    try:
        if supply:
            system.add_component(
                name="S_AIR", cls="SourceContinuous", flow="AIR", rate=Q
            )
            system.add_component(
                name="S_H2", cls="SourceContinuous", flow="H2", rate=QH2
            )
        system.add_component(name="ROOM", cls="MgRoom", **(room or {}))
        system.add_component(name="FAN", cls="MgMachine", rate=FAN)
        system.add_component(name="DRAFT", cls="MgMachine", rate=DRAFT)
        for flow in ("AIR", "H2"):
            if supply:
                system.connect_flow(source=f"S_{flow}", target="ROOM", flow_name=flow)
            system.connect_flow(source="ROOM", target="FAN", flow_name=flow)
            system.connect_flow(source="ROOM", target="DRAFT", flow_name=flow)
    except Exception:
        system.deleteSys()
        raise
    return system


def taken(system, consumer):
    """What ``consumer`` was allocated of each constituent, summed."""
    room = system.comp["ROOM"]
    return sum(
        room.flows_out[flow].allocated_for(consumer) or 0.0 for flow in ("AIR", "H2")
    )


def reference_share(rate, horizon=HORIZON, dt=1e-4):
    """The hydrogen share of a well-mixed volume drained at ONE total rate."""
    held = {"AIR": V, "H2": 1.0}
    inflow = {"AIR": Q, "H2": QH2}
    date = 0.0
    while date < horizon - 1e-12:
        total = sum(held.values())
        for flow in held:
            held[flow] += (inflow[flow] - rate * held[flow] / total) * dt
        date += dt
    return held["H2"] / sum(held.values())


@pytest.fixture(scope="module")
def two_machines():
    """Fan and draft on one room, observed at HORIZON."""
    system = build("MixtureGroupsTwo")
    obs = {}
    try:
        system.isimu_start()
        system.isimu_step_to(HORIZON, max_events=100000)
        capacity = system.comp["ROOM"].capacities["room"]
        obs["fan"] = taken(system, "FAN")
        obs["draft"] = taken(system, "DRAFT")
        obs["fan_h2"] = system.comp["ROOM"].flows_out["H2"].allocated_for("FAN")
        obs["draft_h2"] = system.comp["ROOM"].flows_out["H2"].allocated_for("DRAFT")
        obs["share"] = capacity.get_quantity("H2") / capacity.get_quantity()
        system.isimu_stop()
    finally:
        system.deleteSys()
    return obs


def test_each_machine_draws_its_own_rate(two_machines):
    assert two_machines["fan"] == pytest.approx(FAN, rel=REL)
    assert two_machines["draft"] == pytest.approx(DRAFT, rel=REL)


def test_each_machine_draws_at_the_composition(two_machines):
    """Both see one composition: the hydrogen they take is in their rates' ratio."""
    ratio = two_machines["fan_h2"] / two_machines["draft_h2"]
    assert ratio == pytest.approx(FAN / DRAFT, rel=REL)


def test_the_draws_add_up_on_the_composition(two_machines):
    """The room evolves as one well-mixed volume drained at the summed rate."""
    assert two_machines["share"] == pytest.approx(
        reference_share(FAN + DRAFT), rel=1e-3
    )


def test_a_volume_that_cannot_serve_the_sum_serves_each_pro_rata():
    """AE6: a ceiling of half the sum, two machines of 100 and 1."""
    system = muscadet.System(name="MixtureGroupsHalf")
    try:
        system.add_component(name="ROOM", cls="MgRoom", h2_init=0.0, serve_rate=50.5)
        system.add_component(name="FAN", cls="MgMachine", rate=100.0)
        system.add_component(name="DRAFT", cls="MgMachine", rate=1.0)
        for flow in ("AIR", "H2"):
            system.connect_flow(source="ROOM", target="FAN", flow_name=flow)
            system.connect_flow(source="ROOM", target="DRAFT", flow_name=flow)

        system.isimu_start()
        system.isimu_step_to(0.1, max_events=100000)
        fan, draft = taken(system, "FAN"), taken(system, "DRAFT")
        system.isimu_stop()
    finally:
        system.deleteSys()

    assert fan == pytest.approx(50.0, rel=REL)
    assert draft == pytest.approx(0.5, rel=REL)


class MgThreeRoom(muscadet.ObjFlow):
    """One volume over three constituents."""

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


def prerun_error(system):
    """What the pre-run step raised, or None. Deletes the system."""
    try:
        try:
            system.prerun()
        except Exception as err:  # noqa: BLE001 -- the refusal IS the observation
            return err
        return None
    finally:
        system.deleteSys()


def test_a_group_leaving_out_a_constituent_is_refused():
    system = muscadet.System(name="MixtureGroupsPartial")
    system.add_component(name="ROOM", cls="MgThreeRoom")
    system.add_component(name="FAN", cls="MgMachine", rate=FAN)
    for flow in ("AIR", "H2"):
        system.connect_flow(source="ROOM", target="FAN", flow_name=flow)

    error = prerun_error(system)

    assert isinstance(error, MixtureGroupError)
    assert "CO2" in str(error)
    assert "every constituent" in str(error)


def test_two_groups_need_a_proportional_allocation():
    priority = {"allocation": "priority", "allocation_priorities": {"FAN": 1}}
    error = prerun_error(build("MixtureGroupsPriority", room={"allocation": priority}))

    assert isinstance(error, MixtureGroupError)
    assert "proportional" in str(error)


def test_one_group_keeps_whatever_allocation_it_declared():
    """A single consumer has nothing to arbitrate: no model of 5.16 moves."""
    priority = {"allocation": "priority", "allocation_priorities": {"FAN": 1}}
    system = muscadet.System(name="MixtureGroupsOnePriority")
    system.add_component(name="ROOM", cls="MgRoom", allocation=priority)
    system.add_component(name="FAN", cls="MgMachine", rate=FAN)
    for flow in ("AIR", "H2"):
        system.connect_flow(source="ROOM", target="FAN", flow_name=flow)

    assert prerun_error(system) is None


class MgTwinMachine(muscadet.ObjFlow):
    """One component declaring two groups over the same constituents."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        self.add_mixture_in(name="fan", flows=["AIR", "H2"], flow_rate=FAN)
        self.add_mixture_in(name="draft", flows=["AIR", "H2"], flow_rate=DRAFT)


def test_two_groups_of_one_component_on_one_volume_are_refused():
    system = muscadet.System(name="MixtureGroupsTwin")
    try:
        with pytest.raises(ValueError, match="already drawn by group"):
            system.add_component(name="TWIN", cls="MgTwinMachine")
    finally:
        system.deleteSys()


class MgOutlet(muscadet.ObjFlow):
    """A per-flow consumer."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow, var_demand_default=1.0)


def test_a_per_flow_consumer_beside_two_groups_is_refused():
    system = build("MixtureGroupsTap")
    system.add_component(name="TAP", cls="MgOutlet")
    for flow in ("AIR", "H2"):
        system.connect_flow(source="ROOM", target="TAP", flow_name=flow)

    error = prerun_error(system)

    assert isinstance(error, MixtureGroupError)
    assert "also serves" in str(error)
    assert "TAP" in str(error)


def test_delete():
    cod3s.terminate_session()
