"""A mixture machine is commanded on and off (R52).

A ventilation fan runs on an order: a gas detector, a controller, an operator.
R51 had no way to say so, and the H2 showcase wrote its fan as rule sets to get
one. ``run_cond`` is that order, in the operand vocabulary a discharge command
already uses (R49): same shapes, same conjunctive-normal form, same comparison
grammar, resolved by the same implementation. An empty command holds, so a
group declaring none runs whenever its rate allows, which is every model
written before the field existed.

**It composes with the rate by BRANCHING**, for the reason the ceiling does: a
command that does not hold answers zero outright, never ``rate * 0``.

**It enters the loop detection.** A command reading the rate its own group
draws closes a loop over no connection at all and is refused by name; a
command reading that rate over an observation link is refused by the walk.
"""

import json
import warnings

import cod3s
import pytest

import muscadet
from muscadet.kb.continuous import SourceContinuous  # noqa: F401
from muscadet.ordering import ContinuousFlowCycleError

Q = 50.0
QH2 = 2.0
V = 90.0

#: The date the command signal drops in the discrete scenario.
DROP_AT = 2.0

#: The clock level the comparison scenario starts the fan at. The clock fills
#: at 1 per unit of time from 0, so this is also the date the fan starts.
START_AT = 3.0

#: How far either side of a date a reading is taken.
EPS = 0.05

REL = 1e-5


class McRoom(muscadet.ObjFlow):
    """One volume over both constituents."""

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


class McSwitch(muscadet.ObjFlow):
    """Publishes the fan's order, on until a failure mode drops it."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="vent", var_prod_default=True)


class McOrderedFan(muscadet.ObjFlow):
    """A fan running while its discrete order holds."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        self.add_flow_in(name="vent")
        self.add_mixture_in(
            name="extraction", flows=["AIR", "H2"], flow_rate=Q, run_cond=["vent"]
        )


class McClock(muscadet.ObjFlow):
    """An integrated level reading the date: filled at 1 per unit of time."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="tick")
        self.add_capacity(
            name="clock",
            flow="tick",
            side="in",
            capacity=1e6,
            fill_rate="inf",
        )


class McClockedFan(muscadet.ObjFlow):
    """A fan starting once a level crosses a threshold, watched by the solver."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        self.add_measurement_in(name="clock")
        self.add_mixture_in(
            name="extraction",
            flows=["AIR", "H2"],
            flow_rate=Q,
            run_cond=[{"name": "clock", "op": ">=", "value": START_AT}],
        )


class McSelfCommandedFan(muscadet.ObjFlow):
    """A fan commanded on the hydrogen rate it itself draws."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        self.add_mixture_in(
            name="extraction",
            flows=["AIR", "H2"],
            flow_rate=Q,
            run_cond=[{"name": "H2", "op": ">", "value": 0.1}],
        )


class McRateWatcher(muscadet.ObjFlow):
    """Observes the hydrogen rate the room delivers and orders the fan off.

    The verdict leaves as a discrete signal, so the loop it closes is found by
    the walk, not recognised at zero hops.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="H2", kind="rate")
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name="vent",
                var_prod_cond=[{"name": "H2", "op": "<", "value": 1.0}],
            )
        )


def build(name, fan_cls, sources=True):
    """Sources, room and fan, wired. The caller deletes the system."""
    system = muscadet.System(name=name)
    try:
        if sources:
            system.add_component(
                name="S_AIR", cls="SourceContinuous", flow="AIR", rate=Q
            )
            system.add_component(
                name="S_H2", cls="SourceContinuous", flow="H2", rate=QH2
            )
        system.add_component(name="ROOM", cls="McRoom")
        system.add_component(name="FAN", cls=fan_cls)
        for flow in ("AIR", "H2"):
            if sources:
                system.connect_flow(source=f"S_{flow}", target="ROOM", flow_name=flow)
            system.connect_flow(source="ROOM", target="FAN", flow_name=flow)
    except Exception:
        system.deleteSys()
        raise
    return system


def drawn(system):
    """The volume the room delivers to the fan right now."""
    room = system.comp["ROOM"]
    return sum(room.flows_out[flow].var_fed.value() for flow in ("AIR", "H2"))


def record(system, dates):
    """``{date: drawn}`` at each date, then delete the system."""
    out = {}
    try:
        system.isimu_start()
        for date in dates:
            system.isimu_step_to(date, max_events=100000)
            out[date] = drawn(system)
        system.isimu_stop()
    finally:
        system.deleteSys()
    return out


def start_error(system):
    """What starting ``system`` raised, or None. Deletes the system."""
    error = None
    try:
        try:
            system.isimu_start()
        except Exception as err:  # noqa: BLE001 -- the refusal IS the observation
            error = err
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                system.isimu_stop()
            except Exception:  # pragma: no cover - nothing was started
                pass
    finally:
        system.deleteSys()
    return error


@pytest.fixture(scope="module")
def ordered():
    """The discrete order drops at DROP_AT."""
    system = build("MixtureRunOrdered", "McOrderedFan")
    system.add_component(name="SW", cls="McSwitch")
    system.comp["SW"].add_delay_failure_mode(
        name="drop",
        failure_time=DROP_AT,
        failure_effects=[("vent_fed_available_out", False)],
        repair_cond=False,
    )
    system.connect_flow(source="SW", target="FAN", flow_name="vent")
    return record(system, [DROP_AT - 1.0, DROP_AT + 1.0])


@pytest.fixture(scope="module")
def clocked():
    """The fan starts when the clock crosses START_AT."""
    system = build("MixtureRunClocked", "McClockedFan")
    system.add_component(name="S_TICK", cls="SourceContinuous", flow="tick", rate=1.0)
    system.add_component(name="CLOCK", cls="McClock")
    system.connect_flow(source="S_TICK", target="CLOCK", flow_name="tick")
    system.connect("CLOCK", "clock_level_out", "FAN", "clock_level_in")
    return record(system, [START_AT - EPS, START_AT + EPS])


def test_the_fan_draws_while_its_order_holds(ordered):
    assert ordered[DROP_AT - 1.0] == pytest.approx(Q, rel=REL)


def test_the_fan_draws_nothing_once_its_order_drops(ordered):
    assert ordered[DROP_AT + 1.0] == pytest.approx(0.0, abs=1e-9)


def test_a_watched_threshold_starts_the_fan_at_its_date(clocked):
    assert clocked[START_AT - EPS] == pytest.approx(0.0, abs=1e-9)
    assert clocked[START_AT + EPS] == pytest.approx(Q, rel=REL)


def test_the_threshold_is_watched_under_the_group_name():
    system = build("MixtureRunWatched", "McClockedFan", sources=False)
    try:
        names = list(system.comp["FAN"].automata_d)
        assert any("_extraction_run_cond_" in name for name in names), names
    finally:
        system.deleteSys()


def test_a_command_on_the_rate_its_group_draws_is_refused():
    error = start_error(build("MixtureRunSelf", "McSelfCommandedFan"))

    assert isinstance(error, ContinuousFlowCycleError)
    assert "extraction" in str(error)
    assert "H2" in str(error)


def test_a_command_on_the_observed_rate_its_group_draws_is_refused():
    system = build("MixtureRunObserved", "McOrderedFan")
    system.add_component(name="WATCH", cls="McRateWatcher")
    system.connect("ROOM", "H2_rate_out", "WATCH", "H2_rate_in")
    system.connect_flow(source="WATCH", target="FAN", flow_name="vent")

    error = start_error(system)

    assert isinstance(error, ContinuousFlowCycleError)
    assert "extraction" in str(error)


class McRepublisher(muscadet.ObjFlow):
    """Reads the hydrogen rate the room delivers and republishes it (R37)."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="H2", kind="rate")
        self.add_measurement_out(name="seen", source="H2")


class McDirectFan(muscadet.ObjFlow):
    """A fan whose command thresholds a reading directly, with no signal."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        self.add_measurement_in(name="seen")
        self.add_mixture_in(
            name="extraction",
            flows=["AIR", "H2"],
            flow_rate=Q,
            run_cond=[{"name": "seen", "op": "<", "value": 1.0}],
        )


class McDetectedFan(muscadet.ObjFlow):
    """A fan commanded on the room's LEVEL: the sanctioned gas-detector shape."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("AIR", "H2"):
            self.add_flow_continuous_in(name=flow)
        self.add_measurement_in(name="room", flows=["H2"])
        self.add_mixture_in(
            name="extraction",
            flows=["AIR", "H2"],
            flow_rate=Q,
            run_cond=[{"name": "room", "op": ">=", "value": 0.0}],
        )


def test_a_command_thresholding_the_observed_rate_directly_is_refused():
    """No discrete signal carries the verdict, so nothing is walked."""
    system = build("MixtureRunDirect", "McDirectFan")
    system.add_component(name="PROBE", cls="McRepublisher")
    system.connect("ROOM", "H2_rate_out", "PROBE", "H2_rate_in")
    system.connect("PROBE", "seen_level_out", "FAN", "seen_level_in")

    error = start_error(system)

    assert isinstance(error, ContinuousFlowCycleError)
    assert "extraction" in str(error)


def test_a_command_on_the_room_level_builds():
    """A level is integrated, so a detector reading it closes no loop."""
    system = build("MixtureRunDetected", "McDetectedFan")
    system.connect("ROOM", "room_level_out", "FAN", "room_level_in")

    assert start_error(system) is None


def test_a_command_survives_the_spec_round_trip():
    system = build("MixtureRunSpec", "McClockedFan", sources=False)
    try:
        spec = muscadet.component_spec(system.comp["FAN"])
        (group,) = spec["mixtures"]
        assert group["run_cond"] == [[{"name": "clock", "op": ">=", "value": START_AT}]]
        json.dumps(spec)
    finally:
        system.deleteSys()

    system = muscadet.System(name="MixtureRunRebuilt")
    try:
        comp = muscadet.build_component(system, spec)
        rebuilt = muscadet.component_spec(comp)
        assert rebuilt["mixtures"] == spec["mixtures"]
    finally:
        system.deleteSys()


def test_an_uncommanded_group_writes_no_command_into_its_spec():
    """The bytes of every group spec stored before the field existed stay put."""
    system = muscadet.System(name="MixtureRunBare")
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
                    {"name": "extraction", "flows": ["AIR", "H2"], "flow_rate": 5.0}
                ],
            },
        )
        assert muscadet.component_spec(comp)["mixtures"] == [
            {"name": "extraction", "flows": ["AIR", "H2"], "flow_rate": 5.0}
        ]
    finally:
        system.deleteSys()


def test_delete():
    cod3s.terminate_session()
