"""Probe: ObjCtrl's output machinery grafted onto a live ObjFlow (2026-09-15).

Evidence for the framing note beside which this file stands
(``2026-09-15-001-design-ctrl-grammar-objflow.md``). It patches classes at
runtime and is NOT a design: the real work is extracting the machinery into a
shared base, opening the operand door and settling the two open questions the
note records. Run it with the package importable::

    python docs/plans/2026-09-15-001-ctrl-grammar-objflow-probe.py

Three stages, each printed:

1. BUILD: an ordinary ``ObjFlow`` declares a control input, a boolean verdict
   and a continuous output, all in one component. What the probe borrows, and
   what it costs, is printed first;
2. DOOR: the same component tries to gate its own production on its own
   verdict (``var_prod_cond=["enough"]``). This is refused today, and the
   message is the one gap the note's section 4.2 closes;
3. TRACE: the verdict still computes, and the crossing is dated: a pump-sized
   component watching a tank drains through a band 20/50 and the verdict
   switches between the observation at 24 and the one at 18.
"""

import math

import cod3s
import muscadet
from muscadet.kb.continuous import ConsumerContinuous  # noqa: F401

CTRL = muscadet.ObjCtrl

#: Every method the two control constructors depend on, transitively. Borrowed
#: UNCHANGED: not one line of the machinery was rewritten for this probe.
BORROWED = (
    "add_control_in",
    "add_control_out",
    "claim_name",
    "claim_box",
    "check_aggregation",
    "check_emit_nature",
    "check_emit_inputs",
    "emit_gain_params",
    "compile_emit",
    "build_emit_reader",
    "emit_automaton_base",
    "add_emit_param",
    "add_forcing_params",
    "add_blinding_automaton",
    "add_emit_automaton",
    "add_compare_automaton",
    "add_band_automaton",
    "register_republication",
    "seed_emitted_outputs",
    "needs_control_equation",
    "register_control_equation",
    "compute_controls",
    "publish_control",
    "aggregation_has_kinks",
    "check_crossing_cap",
    "check_incoming_crossing_cap",
    "add_crossing_automata",
    "add_input_crossing_automata",
    "add_one_crossing_automaton",
    "crossing_source_counts",
    "check_crossings_unchanged",
)

#: The containers ``ObjCtrl.__init__`` sets up, by name and kind.
CONTAINERS = (
    "controls_out",
    "interface_boxes",
    "crossing_automata",
    "crossing_sources",
    "controls_emit",
    "emit_automata",
    "emit_republications",
    "emit_publishers",
    "emit_params",
    "emit_forced",
    "blinding_automata",
)

missing = [name for name in BORROWED if not hasattr(CTRL, name)]
if missing:
    raise SystemExit(f"methods not found on ObjCtrl: {missing}")

for name in BORROWED:
    setattr(muscadet.ObjFlow, name, getattr(CTRL, name))

_prev_init = muscadet.ObjFlow.__init__


def _init(self, *args, **kwargs):
    for name in CONTAINERS:
        setattr(self, name, {})
    self.emit_seeded = set()
    self.emit_equation_registered = False
    _prev_init(self, *args, **kwargs)


muscadet.ObjFlow.__init__ = _init

# THE one collision, resolved toward unification: ObjFlow writes
# ``measurements_in`` as a dict, ObjCtrl exposes it as a property aliasing
# ``controls_in``. Same concept, two names; one of them has to go.
muscadet.ObjFlow.controls_in = property(lambda self: self.measurements_in)


def stage_build():
    """One component: a flow port, an observation channel, a verdict."""

    class Plant(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_control_in(name="tank")
            self.add_control_out(
                name="enough",
                kind="bool",
                emit={
                    "op": "band",
                    "input": "tank",
                    "direction": "below",
                    "activate": 20.0,
                    "release": 50.0,
                },
            )
            self.add_flow_continuous_out(name="q", var_fed_default=10.0)

    print(f"borrowed : {len(BORROWED)} methods unchanged from ObjCtrl")
    print(f"state    : {len(CONTAINERS)} containers, one set, one flag")
    print("collision: measurements_in (ObjFlow dict) vs controls_in alias")

    system = muscadet.System(name="ProbeBuild")
    try:
        comp = system.add_component(name="P", cls="Plant")
        print("built    :", type(comp).__name__)
        print("  flows_out    :", list(comp.flows_out))
        print("  controls_in  :", list(comp.controls_in))
        print("  controls_out :", list(comp.controls_out))
        print(
            "  boxes        :",
            sorted(mb.basename() for mb in comp.messageBoxes()),
        )
        print(
            "  thresholds   :",
            [
                v.basename()
                for v in comp.variables()
                if any(k in v.basename() for k in ("activate", "release"))
            ],
        )
        print(
            "  automata     :",
            sorted(a.basename() for a in comp.automata()),
        )
    finally:
        system.deleteSys()


def stage_door():
    """Gate one's own production on one's own verdict: refused, verbatim."""

    # A class name declared here is GLOBALLY visible to cls= resolution (the
    # test-name-collision pitfall), so every stage names its classes apart.
    class DoorPump(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_control_in(name="tank")
            self.add_control_out(
                name="enough",
                kind="bool",
                emit={
                    "op": "band",
                    "input": "tank",
                    "direction": "below",
                    "activate": 20.0,
                    "release": 50.0,
                },
            )
            try:
                self.add_flow_continuous_out(
                    name="q", var_fed_default=4.0, var_prod_cond=["enough"]
                )
                print("var_prod_cond=['enough'] : ACCEPTED")
            except ValueError as err:
                print("var_prod_cond=['enough'] : REFUSED")
                print("  ", err)
                self.add_flow_continuous_out(name="q", var_fed_default=4.0)

    system = muscadet.System(name="ProbeDoor")
    try:
        system.add_component(name="PUMP", cls="DoorPump")
    finally:
        system.deleteSys()


class ProbePump(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_control_in(name="tank")
        self.add_control_out(
            name="enough",
            kind="bool",
            emit={
                "op": "band",
                "input": "tank",
                "direction": "below",
                "activate": 20.0,
                "release": 50.0,
            },
        )
        self.add_flow_continuous_out(name="q", var_fed_default=4.0)


class ProbeTank(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="q")
        self.add_flow_continuous_out(name="q")
        self.add_capacity(
            name="tank",
            flow="q",
            side="out",
            capacity=200.0,
            content_init={"q": 60.0},
            fill_rate=math.inf,
        )


def stage_trace():
    """The verdict computes inside the component and switches at the band."""
    system = muscadet.System(name="ProbeTrace")
    try:
        system.add_component(name="PUMP", cls="ProbePump")
        system.add_component(name="TANK", cls="ProbeTank")
        system.add_component(
            name="LEAK", cls="ConsumerContinuous", flow="q", demand=10.0
        )
        system.connect_flow(source="PUMP", target="TANK", flow_name="q")
        system.connect_flow(source="TANK", target="LEAK", flow_name="q")
        system.connect("TANK", "tank_level_out", "PUMP", "tank_level_in")

        capacity = system.comp["TANK"].capacities["tank"]
        pump = system.comp["PUMP"]
        verdict = pump.controls_out["enough"].var

        system.isimu_start()
        print("     t    level    verdict")
        for k in range(1, 13):
            system.isimu_step_to(k * 1.0, max_events=100000)
            print(
                f" {system.currentTime():5.1f} {capacity.get_quantity('q'):8.3f}"
                f"    {bool(verdict.value())}"
            )
        system.isimu_stop()
    finally:
        system.deleteSys()


print("=" * 60, "\nSTAGE 1, BUILD\n", "=" * 60, sep="")
stage_build()
print("\n", "=" * 60, "\nSTAGE 2, DOOR\n", "=" * 60, sep="")
stage_door()
print("\n", "=" * 60, "\nSTAGE 3, TRACE\n", "=" * 60, sep="")
stage_trace()
cod3s.terminate_session()
