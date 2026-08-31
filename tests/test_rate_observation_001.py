"""A third party reads the rate a continuous output delivers (R38).

A continuous output publishes its transport box ``{f}_out`` -- data, demand and
capability on one bidirectional channel -- and its only foreseen importer is a
continuous input: wiring onto it means publishing a demand and entering the
allocation. There was no way to LOOK at a rate.

``{f}_rate_out`` is that way: a second box, export only, carrying the delivered
rate under the alias ``{f}_rate`` and read by the ``{f}_rate_in`` import of a
measurement channel declared ``kind="rate"``. What the module pins down is that
the reading changes nothing (the two consumers of an observed source receive
exactly what the two consumers of its unobserved twin receive), that it is the
rate left by the LAST allocation restriction rather than the producer's nominal
one, that the observer cannot write it, and that the new box stays out of the
continuous-flow graph the acyclicity check rests on.

The unobserved twin is what makes "changes nothing" observable: it is built and
wired exactly like the observed triad, minus the observer.
"""

import muscadet
import cod3s
import pytest

from muscadet import ordering

#: The observed source's declared rate, and the two demands contending for it.
#: Their sum exceeds the supply on purpose: an allocation that an observer
#: perturbed would show up as a different split, not as a different total.
RO_SUPPLY = 10.0
RO_DEMAND_A = 6.0
RO_DEMAND_B = 9.0

#: What the proportional policy gives each of them out of ``RO_SUPPLY``.
RO_SHARE_A = RO_SUPPLY * RO_DEMAND_A / (RO_DEMAND_A + RO_DEMAND_B)
RO_SHARE_B = RO_SUPPLY * RO_DEMAND_B / (RO_DEMAND_A + RO_DEMAND_B)

#: What a mode leaves of the derated consumer's output. It asks for its nominal
#: share -- the capability sweep works on declared coefficients -- and hands the
#: rest straight back, which is the one shipped shape where a rate is lowered
#: AFTER it was allocated.
RO_DERATING = 0.25
RO_BIG_DEMAND = 1000.0

#: The level scenario, verbatim from the measurement-link module: a rate channel
#: must not have moved what a level channel does.
RO_TANK_VOLUME = 100.0
RO_TANK_INIT = 20.0
RO_TANK_INFLOW = 2.0

#: What an unwired rate channel reads.
RO_RATE_DEFAULT = -1.0

RO_HORIZON = 10.0


class RateObsSource(muscadet.ObjFlow):
    """A continuous source holding its declared rate, counting what it is handed back.

    ``released`` is what :func:`muscadet.evaluation.release_unused_supply` gave
    back to it. It is what makes the restriction of the second scenario a
    measured fact rather than an inference from a number that happens to be
    small.
    """

    released = 0.0

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name="q", var_fed_default=kwargs.get("rate", RO_SUPPLY)
        )

    def release_output(self, flow, comp_name, taken):
        released = super().release_output(flow, comp_name, taken)
        self.released += released
        return released


class RateObsSink(muscadet.ObjFlow):
    """A pure consumer asking for a declared quantity."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(
            name=kwargs.get("takes", "q"), var_demand_default=kwargs.get("demand", 0.0)
        )


class RateObsEye(muscadet.ObjFlow):
    """Observes a rate. No flow at all: it moves nothing and asks for nothing."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="q", kind="rate")


class RateObsLoneEye(muscadet.ObjFlow):
    """The same observer, wired to nobody, with an explicit read default."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="q", kind="rate", rate_default=RO_RATE_DEFAULT)


class RateObsGreedyEye(muscadet.ObjFlow):
    """Asks a rate for constituents. A rate is one number and holds none."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="q", kind="rate", flows=["water"])


class RateObsDerated(muscadet.ObjFlow):
    """``1 q -> 1 x``, with a mode derating ``x`` from the start.

    The capability and demand sweeps work on the declared coefficients, so it
    asks its supplier for the whole of its rate and draws only the quarter the
    derating leaves -- the surplus goes back through ``restrict_allocation``,
    which lowers the very variable the rate box exports.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="x")
        self.add_rules(name="r", rules=[dict(cons={"q": 1.0}, prod={"x": 1.0})])


class RateObsRelay(muscadet.ObjFlow):
    """An instrument republishing the rate of its own output as a reading."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="q")
        self.add_measurement_out(name="probe", source="q")


class RateObsTankFeed(muscadet.ObjFlow):
    """The producer filling the tank of the level scenario."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="w", var_fed_default=RO_TANK_INFLOW)


class RateObsTank(muscadet.ObjFlow):
    """Holds a capacity and publishes its level, unchanged."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="w", var_demand_default=RO_TANK_INFLOW)
        self.add_capacity(
            name="cuve",
            flow="w",
            capacity=RO_TANK_VOLUME,
            content_init={"w": RO_TANK_INIT},
        )


class RateObsLevelEye(muscadet.ObjFlow):
    """A LEVEL observer, declared exactly as it always was."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_measurement_in(name="cuve")


class RateObsBoxRecorder:
    """The little of a component that :meth:`add_mb` actually touches.

    Lets the naming refusal be exercised where it is raised -- at declaration --
    without building a second engine system, which PyCATSHOO forbids while the
    module's own one is alive.
    """

    def __init__(self, flows_out=None):
        self.flows_out = flows_out or {}
        self.boxes = []
        self.exports = []

    def addMessageBox(self, name):  # noqa: N802 -- PyCATSHOO spelling
        self.boxes.append(name)

    def addMessageBoxExport(self, box, var, alias):  # noqa: N802
        self.exports.append((box, alias))

    def addMessageBoxImport(self, box, var, alias):  # noqa: N802
        pass


#: The refusals taken while the model is being wired, so nothing is left half
#: connected: the constituent check runs BEFORE the engine connect.
REFUSED = {}


@pytest.fixture(scope="module")
def the_system():
    """Wire an observed triad, its unobserved twin, a derated chain, a tank."""

    system = muscadet.System(name="RateObservationSys")

    # -- The observed triad and the twin nobody watches
    for prefix, watched in (("OBS", True), ("REF", False)):
        system.add_component(name=f"{prefix}_SRC", cls="RateObsSource")
        system.add_component(name=f"{prefix}_CA", cls="RateObsSink", demand=RO_DEMAND_A)
        system.add_component(name=f"{prefix}_CB", cls="RateObsSink", demand=RO_DEMAND_B)
        system.connect_flow(
            source=f"{prefix}_SRC", target=f"{prefix}_CA", flow_name="q"
        )
        system.connect_flow(
            source=f"{prefix}_SRC", target=f"{prefix}_CB", flow_name="q"
        )

        if watched:
            system.add_component(name="EYE", cls="RateObsEye")
            system.connect(f"{prefix}_SRC", "q_rate_out", "EYE", "q_rate_in")

    # -- A rate lowered after it was allocated
    system.add_component(name="DER_SRC", cls="RateObsSource")
    system.add_component(name="DER", cls="RateObsDerated")
    system.add_component(
        name="DER_SINK", cls="RateObsSink", takes="x", demand=RO_BIG_DEMAND
    )
    system.add_component(name="DER_EYE", cls="RateObsEye")
    system.connect_flow(source="DER_SRC", target="DER", flow_name="q")
    system.connect_flow(source="DER", target="DER_SINK", flow_name="x")
    system.connect("DER_SRC", "q_rate_out", "DER_EYE", "q_rate_in")
    system.comp["DER"].flows_out["x"].var_out_rate.setValue(RO_DERATING)

    # -- An instrument republishing the rate of its own output
    system.add_component(name="RLY_SRC", cls="RateObsSource")
    system.add_component(name="RELAY", cls="RateObsRelay")
    system.add_component(name="RLY_SINK", cls="RateObsSink", demand=RO_DEMAND_A)
    system.connect_flow(source="RLY_SRC", target="RELAY", flow_name="q")
    system.connect_flow(source="RELAY", target="RLY_SINK", flow_name="q")

    # -- An observer wired to nobody
    system.add_component(name="LONE", cls="RateObsLoneEye")

    # -- The level link, which must be exactly what it was
    system.add_component(name="TANK", cls="RateObsTank")
    system.add_component(name="TFEED", cls="RateObsTankFeed")
    system.add_component(name="LEVEL_EYE", cls="RateObsLevelEye")
    system.connect_flow(source="TFEED", target="TANK", flow_name="w")
    system.connect("TANK", "cuve_level_out", "LEVEL_EYE", "cuve_level_in")

    # -- An observer asking a rate for constituents it cannot hold
    system.add_component(name="GREEDY", cls="RateObsGreedyEye")
    try:
        system.connect("OBS_SRC", "q_rate_out", "GREEDY", "q_rate_in")
    except ValueError as error:
        REFUSED["greedy"] = str(error)

    system.comp["LONE"].add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": RO_HORIZON},
        cond_occ_21=False,
    )

    system.isimu_start()

    for name in ("OBS_SRC", "REF_SRC", "DER_SRC", "RLY_SRC"):
        system.comp[name].released = 0.0

    system.isimu_step_forward()

    def delivered(comp, flow="q"):
        return system.comp[comp].flows_in[flow].get_delivered()

    obs = {
        "system": system,
        "time": system.currentTime(),
        "observed_shares": (delivered("OBS_CA"), delivered("OBS_CB")),
        "reference_shares": (delivered("REF_CA"), delivered("REF_CB")),
        "observed_total": system.comp["OBS_SRC"].flows_out["q"].var_fed.value(),
        "reference_total": system.comp["REF_SRC"].flows_out["q"].var_fed.value(),
        "read_rate": system.comp["EYE"].measurements_in["q"].get_rate(),
        "derated_read": system.comp["DER_EYE"].measurements_in["q"].get_rate(),
        "derated_fed": system.comp["DER_SRC"].flows_out["q"].var_fed.value(),
        "derated_released": system.comp["DER_SRC"].released,
        "relay_published": system.comp["RELAY"].measurements_out["probe"].get_level(),
        "relay_fed": system.comp["RELAY"].flows_out["q"].var_fed.value(),
        "lone_rate": system.comp["LONE"].measurements_in["q"].get_rate(),
        "level_read": system.comp["LEVEL_EYE"].measurements_in["cuve"].get_level(),
        "level_fill": system.comp["LEVEL_EYE"].measurements_in["cuve"].get_fill(),
        "tank_level": system.comp["TANK"].capacities["cuve"].get_quantity(),
        "tank_fill": system.comp["TANK"].capacities["cuve"].get_fill(),
    }

    system.isimu_stop()

    return obs


# ----------------------------------------------------------------------
# The observation is not a consumption
# ----------------------------------------------------------------------


def test_observing_a_rate_changes_neither_share(the_system):
    """Two consumers split a scarce supply; the observer moves neither share.

    The whole point of a second box: the transport box carries a demand alias,
    so wiring onto it is asking for a quantity. This one carries none.
    """
    assert the_system["observed_shares"][0] == pytest.approx(RO_SHARE_A, rel=1e-9)
    assert the_system["observed_shares"][1] == pytest.approx(RO_SHARE_B, rel=1e-9)

    # ... and byte for byte what the unobserved twin's consumers received
    assert the_system["observed_shares"] == pytest.approx(
        the_system["reference_shares"], rel=1e-12
    )
    assert the_system["observed_total"] == pytest.approx(
        the_system["reference_total"], rel=1e-12
    )


def test_the_observer_publishes_no_demand(the_system):
    """It is not among the consumers the producer splits its supply between."""
    source = the_system["system"].comp["OBS_SRC"].flows_out["q"]

    assert set(source.consumer_demands()) == {"OBS_CA", "OBS_CB"}
    assert set(source.allocated) == {"OBS_CA", "OBS_CB"}

    eye = the_system["system"].comp["EYE"]
    assert eye.flows_in == {}
    assert eye.flows_out == {}
    assert set(eye.measurements_in) == {"q"}


def test_the_reading_is_the_delivered_rate(the_system):
    """What the observer reads is what the producer exports on the wire."""
    assert the_system["read_rate"] == pytest.approx(
        the_system["observed_total"], rel=1e-12
    )
    assert the_system["read_rate"] == pytest.approx(RO_SUPPLY, rel=1e-9)


# ----------------------------------------------------------------------
# ... and it is the rate AFTER the last restriction
# ----------------------------------------------------------------------


def test_the_reading_follows_the_last_allocation_restriction(the_system):
    """A derated consumer hands back three quarters; the reading follows it down.

    The nominal rate and the delivered one are made to differ on purpose: a
    reading equal to ``var_fed_default`` would prove nothing at all.
    """
    # A restriction really happened -- measured on the producer, where it lands
    assert the_system["derated_released"] > 0.0

    expected = RO_SUPPLY * RO_DERATING

    assert the_system["derated_fed"] == pytest.approx(expected, rel=1e-4)
    assert the_system["derated_read"] == pytest.approx(expected, rel=1e-4)

    # ... and emphatically not the rate the producer was declared with
    assert the_system["derated_read"] < RO_SUPPLY


# ----------------------------------------------------------------------
# Read only, by construction
# ----------------------------------------------------------------------


def test_the_observer_cannot_write_the_rate(the_system):
    """The imported endpoint is a reference, and a reference has no setter."""
    channel = the_system["system"].comp["EYE"].measurements_in["q"]

    assert not hasattr(channel.var_level, "setValue")

    with pytest.raises(AttributeError):
        channel.var_level.setValue(0.0)

    # The observing component owns no variable behind the link either: there is
    # nothing on its side a write could reach.
    eye_vars = {var.basename() for var in the_system["system"].comp["EYE"].variables()}
    assert "q_rate_in" not in eye_vars
    assert "q_fed_out" not in eye_vars

    # ... and the box itself exports and imports nothing else: no demand alias,
    # so nothing downstream can push a quantity back up it.
    flow = the_system["system"].comp["OBS_SRC"].flows_out["q"]
    recorder = RateObsBoxRecorder(flows_out={"q": flow})
    flow.add_rate_observation_mb(recorder)
    assert recorder.boxes == ["q_rate_out"]
    assert recorder.exports == [("q_rate_out", "q_rate")]


def test_a_rate_channel_declares_no_writable_endpoint(the_system):
    """A rate is imported, never published, by an observer.

    ``MeasurementIn`` is the only import side there is, and it builds references
    on both kinds of channel. Declaring one is therefore the declaration of a
    read, and there is no spelling of it that writes.
    """
    channel = the_system["system"].comp["EYE"].measurements_in["q"]

    assert channel.kind == "rate"
    assert all(not hasattr(var, "setValue") for var in channel.every_reference())


# ----------------------------------------------------------------------
# An unconnected channel, and the level channel that must not have moved
# ----------------------------------------------------------------------


def test_an_unconnected_rate_channel_reads_its_default(the_system):
    """No link, no reading: the declared default, not an error."""
    lone = the_system["system"].comp["LONE"].measurements_in["q"]

    assert lone.var_level.nbCnx() == 0
    assert lone.is_connected is False
    assert the_system["lone_rate"] == pytest.approx(RO_RATE_DEFAULT)


def test_a_level_observer_is_untouched(the_system):
    """The capacity link works exactly as it did, level and fill alike."""
    assert the_system["level_read"] == pytest.approx(the_system["tank_level"], rel=1e-9)
    assert the_system["level_fill"] == pytest.approx(the_system["tank_fill"], rel=1e-9)

    expected = RO_TANK_INIT + RO_TANK_INFLOW * RO_HORIZON
    assert the_system["time"] == pytest.approx(RO_HORIZON)
    assert the_system["tank_level"] == pytest.approx(expected, rel=1e-6)

    channel = the_system["system"].comp["LEVEL_EYE"].measurements_in["cuve"]
    assert channel.kind == "level"
    assert channel.var_level.basename() == "cuve_level_in"
    assert channel.var_fill.basename() == "cuve_fill_in"


# ----------------------------------------------------------------------
# Naming, and the trap the naming sets
# ----------------------------------------------------------------------


def test_the_boxes_are_the_pair_a_plain_connect_wires(the_system):
    """``{f}_rate_out`` on the producer, ``{f}_rate_in`` on the observer."""
    system = the_system["system"]
    channel = system.comp["EYE"].measurements_in["q"]

    assert system.comp["OBS_SRC"].messageBox("q_rate_out") is not None
    assert system.comp["EYE"].messageBox("q_rate_in") is not None

    # ... beside the transport box, which is untouched
    assert system.comp["OBS_SRC"].messageBox("q_out") is not None

    assert channel.var_level.basename() == "q_rate_in"
    assert channel.var_level.nbCnx() == 1
    assert channel.is_connected is True


def test_the_rate_box_is_no_edge_of_the_continuous_graph(the_system):
    """The trap: ``q_rate_out`` must not resolve as a flow named ``q_rate``.

    The filter behind the acyclicity check strips a direction suffix and looks
    the remainder up among the component's flows. A rate box that resolved
    would add an edge between a producer and an observer that exchange no
    quantity at all, and a model closing a loop through such an observer would
    be refused for a cycle it does not have.
    """
    system = the_system["system"]
    source = system.comp["OBS_SRC"]
    eye = system.comp["EYE"]

    assert ordering.continuous_data_channel(source, "q_out", "out") == "q"
    assert ordering.continuous_data_channel(source, "q_rate_out", "out") is None
    assert ordering.continuous_data_channel(eye, "q_rate_in", "in") is None

    assert system.flow_behind_message_box("OBS_SRC", "q_rate_out", "out") is None
    assert system.flow_behind_message_box("EYE", "q_rate_in", "in") is None

    graph = ordering.build_continuous_flow_graph(system)
    assert "EYE" not in graph.nodes
    assert all("EYE" not in (cnct.source, cnct.target) for cnct in graph.connections)


def test_a_flow_named_for_the_rate_box_is_refused(the_system):
    """``{f}_rate_out`` is reserved, so ``{f}`` and ``{f}_rate`` cannot coexist.

    Left to the engine the clash surfaces as "message box already exists",
    which names a box the modeller never asked for. Refused here, the message
    names the two flows whose names collide.
    """
    flow = the_system["system"].comp["OBS_SRC"].flows_out["q"]
    other = muscadet.FlowContinuousOut(name="q_rate")

    recorder = RateObsBoxRecorder(flows_out={"q": flow, "q_rate": other})

    with pytest.raises(ValueError, match="q_rate"):
        flow.add_rate_observation_mb(recorder)

    assert recorder.boxes == []


def test_a_rate_publisher_names_what_it_publishes(the_system):
    """An observer asking a rate for constituents is told a rate holds none."""
    message = REFUSED.get("greedy", "")

    assert "water" in message
    assert "OBS_SRC" in message
    assert "none" in message


def test_the_publisher_and_the_observer_resolve_by_name(the_system):
    """The system knows both ends of a rate link, and does not confuse kinds."""
    system = the_system["system"]

    assert (
        system.measurement_publisher("OBS_SRC", "q_rate_out")
        is system.comp["OBS_SRC"].flows_out["q"]
    )
    assert (
        system.measurement_observer("EYE", "q_rate_in")
        is system.comp["EYE"].measurements_in["q"]
    )

    # A rate channel is not reachable under the level suffix, nor the reverse
    assert system.measurement_observer("EYE", "q_level_in") is None
    assert system.measurement_observer("LEVEL_EYE", "cuve_rate_in") is None
    assert system.measurement_publisher("OBS_SRC", "q_level_out") is None
    assert (
        system.measurement_publisher("TANK", "cuve_level_out")
        is system.comp["TANK"].capacities["cuve"]
    )


# ----------------------------------------------------------------------
# A rate republished as a reading
# ----------------------------------------------------------------------


def test_an_instrument_can_republish_a_rate(the_system):
    """``add_measurement_out(source=<continuous output>)`` follows its rate."""
    assert the_system["relay_published"] == pytest.approx(
        the_system["relay_fed"], rel=1e-9
    )
    assert the_system["relay_fed"] == pytest.approx(RO_DEMAND_A, rel=1e-6)


def test_a_republished_rate_carries_no_constituent(the_system):
    """A rate is one number: it holds nothing to republish per constituent."""
    comp = the_system["system"].comp["RELAY"]

    assert comp.flows_out["q"].published_flows() == []

    with pytest.raises(ValueError, match="which holds none"):
        comp.add_measurement_out(name="bad", source="q", flows=["water"])


def test_a_rate_channel_survives_the_declaration_round_trip(the_system):
    """``kind`` and ``rate_default`` are declarations, so a spec must carry them.

    A dropped ``kind`` would rebuild a LEVEL channel under the same name: it
    declares itself happily, imports on a box no publisher exports, and the
    model then fails at ``connect`` -- one step removed from the spec that lost
    it, which is the shape of silent loss this library refuses everywhere else.
    """
    system = the_system["system"]
    spec = muscadet.component_spec(system.comp["LONE"])

    channel = next(entry for entry in spec["measurements_in"] if entry["name"] == "q")
    assert channel["kind"] == "rate"
    assert channel["rate_default"] == pytest.approx(RO_RATE_DEFAULT)

    muscadet.build_component(system, dict(spec, name="LONE_REBUILT"))
    rebuilt = system.comp["LONE_REBUILT"].measurements_in["q"]

    assert rebuilt.kind == "rate"
    assert rebuilt.box_name() == "q_rate_in"
    assert rebuilt.get_rate() == pytest.approx(RO_RATE_DEFAULT)


def test_delete(the_system):
    the_system["system"].deleteSys()
    cod3s.terminate_session()
