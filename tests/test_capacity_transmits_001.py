"""A capacity declares whether it transits, and the exported document says so.

What makes a cuve a BUFFER is that it passes on what its volume does not hold
back: the empty branch of ``Capacity.serve_limit``, where a volume holding
nothing still lets through what currently crosses it (R7). That behaviour lived
in muscadet's code and in no key of the exported document, so an engine reading
the document back could not know it.

**Why ``side`` could not carry it.** ``CAPACITY_PORTS`` maps both ``"both"``
and ``"out"`` onto ``side="out"``, so a buffer and a reservoir came out
byte-identical apart from their flow list. The only discriminant left in the
whole document was the presence of a ``FlowContinuousIn`` of the same name --
usable, but a convention the document never states, and a reader that did not
know it fed nothing to whatever stood downstream. Measured on the companion
engine before this: a load behind a full cuve received 0.

**Why the name is ``transmits`` and not a third rate.** ``fill_rate`` is a
CLAIM, what the volume asks for itself; ``serve_rate`` is a CEILING, what it
forbids itself to release. Both answer HOW MUCH. This one answers WHETHER: the
branch exists or it does not. Naming it in the ``*_rate`` family -- or
symmetrically with either of them, which is the trap ``serve_rate``'s own
docstring records -- would invite reading a quantity where there is a
predicate. It is also the word the reading engine's own ticket uses for the
thing it has to build, and the two halves of one contract are better off
sharing a word.

**Its default is True**, which is what every capacity did before the field
existed, so no model moves and a document written at 1.0.0 rebuilds unchanged.

``CapacityContinuous`` DERIVES it from ``ports`` and does not accept it:
transit needs two ports, one to receive at and one to pass on to, so a buffer
transits and a reservoir and an accumulator do not. It is not an overridable
default the way ``side`` is, and taking the two for a pair is the mistake this
module was written with: this class declares no rule, so the ports settle the
question and an explicit value could only restate the derivation or contradict
it. The contradiction is the one that matters -- the solver transits whatever
the key says, so ``ports="both", transmits=False`` served its consumer 1.0
while writing ``transmits: false`` into the document, which is this ticket's
own divergence produced by the shipped class. ``ObjFlow.add_capacity`` keeps
the key, where a component declaring RULES has a route the ports do not show.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, driven, inspected and deleted before the next one starts; the fixture
snapshots what each produced.
"""

import copy
import json

import cod3s
import pytest

import muscadet
from muscadet import declare

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    CAPACITY_PORTS,
    CAPACITY_TRANSMITS,
    CapacityContinuous,
    ConsumerContinuous,
    SourceContinuous,
)

#: The ticket's montage, and the shape a continuous corpus is mostly made of:
#: a source, a volume with something in it, a load asking for less than the
#: source delivers.
CT_VOLUME = 100.0
CT_INIT = 10.0
CT_SOURCE = 2.0
CT_DEMAND = 1.0

#: How far each scenario is driven, and by what dated transition.
CT_HORIZON = 4.0


class CtHorizon(muscadet.ObjFlow):
    """A dated transition, so the interactive session has somewhere to go.

    A purely continuous model never moves: ``stepForward`` advances to the next
    DATED transition and integrates on the way, so without one the clock sits
    at zero and every level reports its declared initial value.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_atm2states(
            name="horizon",
            occ_law_12={"cls": "delay", "time": CT_HORIZON},
            cond_occ_21=False,
        )


def build_tank(system, ports, **extra):
    """The same volume every time, told apart by ``ports`` alone."""
    return system.add_component(
        name="TANK",
        cls="CapacityContinuous",
        flow="q",
        capacity=CT_VOLUME,
        capacity_name="tank",
        content_init={"q": CT_INIT},
        ports=ports,
        **extra,
    )


def build_buffered_system(name):
    """Source, cuve, load: the montage the reading engine fed nothing to."""
    system = muscadet.System(name=name)
    system.add_component(name="SRC", cls="SourceContinuous", flow="q", rate=CT_SOURCE)
    build_tank(system, "both")
    system.add_component(
        name="SINK", cls="ConsumerContinuous", flow="q", demand=CT_DEMAND
    )
    system.add_component(name="H", cls="CtHorizon")
    system.connect_flow(source="SRC", target="TANK", flow_name="q")
    system.connect_flow(source="TANK", target="SINK", flow_name="q")
    return system


def run_derivation(obs):
    """What each of the three ports declares, side and transit side by side."""
    for ports in ("both", "in", "out"):
        system = muscadet.System(name=f"CtDerive{ports}")
        try:
            tank = build_tank(system, ports)
            capacity = tank.capacities["tank"]
            obs[f"{ports}_side"] = capacity.side
            obs[f"{ports}_transmits"] = capacity.transmits
            obs[f"{ports}_spec"] = declare.component_spec(tank)
        finally:
            system.deleteSys()


#: Every (``ports``, value) pair a modeller could write on the class. All four
#: are refused: see :func:`run_the_key_is_not_declarable`.
CT_DECLARED = (("both", False), ("both", True), ("out", True), ("in", False))


def run_the_key_is_not_declarable(obs):
    """The class derives the key and refuses to be told it.

    ``ports="both"`` with ``transmits=False`` is the pair that matters, and it
    was accepted for a day: measured then, it built, served its consumer 1.0
    and wrote ``transmits: false`` into the document -- the very divergence
    between the two engines this field exists to close, written by the shipped
    class itself.
    """
    system = muscadet.System(name="CtNotDeclarable")
    try:
        for index, (ports, value) in enumerate(CT_DECLARED):
            try:
                system.add_component(
                    name=f"T{index}",
                    cls="CapacityContinuous",
                    flow="q",
                    capacity=CT_VOLUME,
                    capacity_name="tank",
                    ports=ports,
                    transmits=value,
                )
                obs[f"declared_{ports}_{value}"] = None
            except ValueError as err:
                obs[f"declared_{ports}_{value}"] = str(err)
    finally:
        system.deleteSys()


#: Every way the accumulator refusal can be built, and what its subject must
#: read like. The message names the OFFENDING keys, so its subject varies with
#: what was declared where it used to be a fixed pair.
#:
#: ``serve_cond`` names no port here on purpose: the refusal fires before the
#: condition is resolved, which is what lets a second key join the list
#: without a control port having to exist.
CT_REFUSALS = (
    ("ceiling", dict(serve_rate=40.0), "serve_rate governs what"),
    ("command", dict(serve_cond=["cmd"]), "serve_cond governs what"),
    (
        "two",
        dict(serve_rate=40.0, serve_cond=["cmd"]),
        "serve_rate and serve_cond govern what",
    ),
)


def run_refusal_wordings(obs):
    """The sentences the refusal builds, collected verbatim."""
    system = muscadet.System(name="CtRefusalWordings")
    try:
        for index, (label, extra, _) in enumerate(CT_REFUSALS):
            try:
                system.add_component(
                    name=f"ACC{index}",
                    cls="CapacityContinuous",
                    flow="q",
                    capacity=CT_VOLUME,
                    capacity_name="acc",
                    ports="in",
                    **extra,
                )
                obs[f"refusal_{label}"] = None
            except ValueError as err:
                obs[f"refusal_{label}"] = str(err)
    finally:
        system.deleteSys()


def run_buffered_scenario(obs):
    """The montage driven, its document taken, both recorded."""
    system = build_buffered_system("CtBuffered")
    try:
        tank = system.comp["TANK"]

        system.isimu_start()
        system.isimu_step_forward()

        obs["delivered"] = system.comp["SINK"].flows_in["q"].var_fed.value()
        obs["level"] = tank.capacities["tank"].get_quantity("q")
        obs["time"] = system.currentTime()

        system.isimu_stop()

        obs["document"] = declare.system_spec(system)
    finally:
        system.deleteSys()


def run_round_trip(obs):
    """The document rebuilt, with the key and with the key taken out of it."""
    document = obs["document"]

    for prefix, spec in (
        ("kept", copy.deepcopy(document)),
        ("dropped", _without_transmits(copy.deepcopy(document))),
    ):
        if prefix == "dropped":
            # A reader at 1.0.0 wrote no such key, so the rebuild is fed a
            # document that carries the version it was written under too.
            spec["version"] = "1.0.0"

        spec["name"] = f"CtRebuild{prefix}"
        try:
            rebuilt = declare.build_system(spec)
            capacity = rebuilt.comp["TANK"].capacities["tank"]
            obs[f"{prefix}_transmits"] = capacity.transmits

            rebuilt.isimu_start()
            rebuilt.isimu_step_forward()
            obs[f"{prefix}_delivered"] = (
                rebuilt.comp["SINK"].flows_in["q"].var_fed.value()
            )
            obs[f"{prefix}_level"] = capacity.get_quantity("q")
            rebuilt.isimu_stop()
        finally:
            rebuilt.deleteSys()


def _without_transmits(spec):
    """The same document as an engine at 1.0.0 wrote it: no such key anywhere."""
    for comp in spec["components"].values():
        for capacity in comp.get("capacities", ()):
            capacity.pop("transmits", None)
    return spec


@pytest.fixture(scope="module")
def the_run():
    """Every scenario, built, driven and deleted in turn."""
    obs = {}

    run_derivation(obs)
    run_the_key_is_not_declarable(obs)
    run_refusal_wordings(obs)
    run_buffered_scenario(obs)
    run_round_trip(obs)

    return obs


# ----------------------------------------------------------------------
# The declaration
# ----------------------------------------------------------------------


def test_a_capacity_transmits_unless_it_says_otherwise():
    """The default is what every capacity did before the field existed."""
    capacity = muscadet.Capacity(name="tank", flows=["q"], capacity=CT_VOLUME)

    assert capacity.transmits is True
    assert (
        muscadet.Capacity(
            name="tank", flows=["q"], capacity=CT_VOLUME, transmits=False
        ).transmits
        is False
    )


def test_the_kb_derives_the_key_from_ports(the_run):
    """As ``CAPACITY_PORTS`` derives ``side``, and where ``side`` cannot follow.

    The point of the whole field is in the first two assertions: ``both`` and
    ``out`` are the SAME side, and a document carrying only the side told a
    buffer from a reservoir by nothing at all.
    """
    assert the_run["both_side"] == the_run["out_side"] == "out"
    assert the_run["in_side"] == "in"

    assert the_run["both_transmits"] is True
    assert the_run["out_transmits"] is False
    assert the_run["in_transmits"] is False

    assert (
        CAPACITY_TRANSMITS.keys() == CAPACITY_PORTS.keys()
    ), "every shape ports names must say whether it transits"


def test_what_the_document_declares_is_what_the_component_is_wired_for(the_run):
    """The derivation read off the BUILD, not off a second copy of the table.

    This class declares no rule, so the ports are the whole of the route
    through the volume: it transits exactly when its held flow is carried on
    both sides. Checking the table against the component that was built is
    what makes the document true rather than merely consistent with itself.
    """
    for ports in ("both", "in", "out"):
        spec = the_run[f"{ports}_spec"]
        sides = {flow["cls"] for flow in spec["flows"]}
        crosses = sides == {"FlowContinuousIn", "FlowContinuousOut"}

        assert spec["capacities"][0]["transmits"] is crosses, (
            f"ports={ports}: the document says "
            f"{spec['capacities'][0]['transmits']} of a component carrying "
            f"{sorted(sides)}"
        )


def test_the_key_is_derived_and_the_class_refuses_to_be_told_it(the_run):
    """An explicit value could only restate the derivation, or lie.

    Where ``side`` takes an override because ``ports="both"`` leaves a real
    choice -- the volume upstream of the rules or downstream -- this leaves
    none: no rule, so the ports settle it. And the contradiction is not a
    modelling choice a model may mean, because the solver transits whatever
    the key says. ``ports="both", transmits=False`` was accepted for a day and
    measured: consumer served 1.0, document ``transmits: false``. A document
    that lies about the engine that wrote it is the one thing this field
    exists to prevent.
    """
    for ports, value in CT_DECLARED:
        message = the_run[f"declared_{ports}_{value}"]

        assert (
            message is not None
        ), f"ports={ports}, transmits={value}: the class took a key it derives"
        assert "'transmits'" in message
        assert "does not accept declaration key" in message
        # And it says what it does accept, ``ports`` included, which is what
        # the modeller has to reach for instead.
        assert "ports" in message


def test_the_refusal_reads_as_a_sentence_whatever_it_names(the_run):
    """Naming the offender made the subject vary, so the rest of it follows.

    The message was FIXED while it listed the two keys it knew, and therefore
    always plural and always right. Built from what was actually declared, it
    said "serve_rate govern" on one key. A refusal a modeller reads is prose.

    The neighbouring refusal in the platform importer carried the same
    disagreement and is pinned in ``test_cod3s_platform_continuous.py``.
    """
    for label, _, subject in CT_REFUSALS:
        message = the_run[f"refusal_{label}"]

        assert message is not None, f"{label}: nothing was refused"
        assert subject in message, f"{label}: {message}"

    # The pronoun agrees too, and the offending keys are repeated at the end,
    # which is where the reader is told what to drop.
    assert "would ever read it." in the_run["refusal_ceiling"]
    assert the_run["refusal_ceiling"].endswith("drop serve_rate")
    assert "would ever read them." in the_run["refusal_two"]
    assert the_run["refusal_two"].endswith("drop serve_rate and serve_cond")


# ----------------------------------------------------------------------
# The document
# ----------------------------------------------------------------------


def test_the_document_carries_the_key_on_every_capacity(the_run):
    """Read off a document really produced, not off the field list.

    ``_declaration_fields`` pours every serialisable field into the spec, so a
    boolean crosses with no code of its own -- which is a reason to check the
    document rather than to suppose it.
    """
    for ports in ("both", "in", "out"):
        capacity = the_run[f"{ports}_spec"]["capacities"][0]
        assert "transmits" in capacity, f"ports={ports}"
        assert capacity["transmits"] is the_run[f"{ports}_transmits"]

    document = the_run["document"]
    capacity = document["components"]["TANK"]["capacities"][0]

    assert capacity["transmits"] is True
    assert json.dumps(document, allow_nan=False)


def test_a_buffer_and_a_reservoir_differ_by_more_than_their_flows(the_run):
    """The discriminant is now stated, where it used to be inferred.

    Both component specs have their flow list taken away, which is exactly what
    is left of the old convention: before the key, what remained was identical.
    """
    buffer_spec = copy.deepcopy(the_run["both_spec"])
    reservoir_spec = copy.deepcopy(the_run["out_spec"])

    for spec in (buffer_spec, reservoir_spec):
        spec.pop("flows")

    assert (
        buffer_spec != reservoir_spec
    ), "with the flows gone, nothing else tells the two apart"

    difference = {
        key
        for key in set(buffer_spec["capacities"][0])
        | set(reservoir_spec["capacities"][0])
        if buffer_spec["capacities"][0].get(key)
        != reservoir_spec["capacities"][0].get(key)
    }
    assert difference == {"transmits"}


def test_the_importer_derives_the_key_off_the_rule_route_too():
    """The second route a volume is fed or drained by, which no shipped class has.

    The platform payload declares no ``ports``: the importer builds plain
    ``ObjFlow`` components, so it reads the answer off the two routes the
    production sweep honours -- a flow carried on the other side, and a rule
    that produces into the volume or consumes out of it. The H2 plant exercises
    the first on both its volumes; the second is reachable only through a rule
    set, which ``CapacityContinuous`` never declares.
    """
    from muscadet.importers.cod3s_platform import (
        CapacityFlowSpec,
        CapacitySpec,
        FlowSpec,
        RuleSetSpec,
        RuleSpec,
        _capacity_transmits,
        _FlowIndex,
    )

    def flow(name, direction):
        return FlowSpec(
            name=name, direction=direction, logic="or", flow_family="continuous"
        )

    hopper = CapacitySpec(
        name="hopper", flows=(CapacityFlowSpec(name="a"),), volume=10.0, side="in"
    )
    milling = (
        RuleSetSpec(
            name="mill",
            rules=(RuleSpec(name="run", cons={"a": 1.0}, prod={"x": 1.0}),),
        ),
    )

    # An input the rules draw on: it arrives at the port and leaves into the
    # recipe, so the volume has a through-path and transits.
    assert (
        _capacity_transmits(
            hopper,
            index=_FlowIndex([flow("a", "input"), flow("x", "output")]),
            rule_sets=milling,
        )
        is True
    )

    # The same volume on a class declaring no recipe: nothing draws from it, so
    # nothing crosses it whatever arrives.
    assert (
        _capacity_transmits(
            hopper, index=_FlowIndex([flow("a", "input")]), rule_sets=()
        )
        is False
    )

    # An OUTPUT volume the rules fill: it arrives from the recipe and leaves by
    # the identity transfer, which is a buffer sitting after a transformation.
    buffer_after = CapacitySpec(
        name="out_buffer", flows=(CapacityFlowSpec(name="x"),), volume=10.0, side="out"
    )
    assert (
        _capacity_transmits(
            buffer_after,
            index=_FlowIndex([flow("a", "input"), flow("x", "output")]),
            rule_sets=milling,
        )
        is True
    )


def test_the_spec_version_is_a_patch_increment():
    """An optional field carrying a default is a patch, and the major holds.

    A reader controls the major alone, so a document written at 1.0.0 is still
    read here, and a reader that ignores the key behaves exactly as it did.
    """
    assert declare.SYSTEM_SPEC_VERSION == "1.0.1"
    assert declare.SYSTEM_SPEC_VERSION.split(".")[0] == "1"


# ----------------------------------------------------------------------
# What it leaves where it was
# ----------------------------------------------------------------------


def test_the_buffered_montage_delivers_what_it_always_did(the_run):
    """A load behind a cuve is served, and the cuve does not budge.

    The number the reading engine got wrong: 1.0 through a volume holding 10
    that neither fills nor empties, its ``fill_rate`` being the documented
    default of 0.
    """
    assert the_run["time"] == pytest.approx(CT_HORIZON)
    assert the_run["delivered"] == pytest.approx(CT_DEMAND)
    assert the_run["level"] == pytest.approx(CT_INIT)


def test_a_document_without_the_key_rebuilds_as_one_carrying_it(the_run):
    """The 1.0.0 document, rebuilt: the default puts it back where it was."""
    assert the_run["dropped_transmits"] is True
    assert the_run["kept_transmits"] is True

    assert the_run["dropped_delivered"] == pytest.approx(the_run["delivered"])
    assert the_run["kept_delivered"] == pytest.approx(the_run["delivered"])
    assert the_run["dropped_level"] == pytest.approx(the_run["level"])
    assert the_run["kept_level"] == pytest.approx(the_run["level"])


def test_delete():
    cod3s.terminate_session()
