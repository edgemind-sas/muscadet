"""The controller, as a declaration.

``ObjCtrl`` describes a controller: which quantities it observes, which
signals and readings it publishes, and the closed grammar each output emits
(:mod:`muscadet.ctrl_grammar`). It holds data and builds nothing. What runs a
controller belongs to an engine, which receives the declaration through the
component builder it registered (:func:`muscadet.engine.component_builder`).
The classes and methods named below as carrying a behaviour (``CtrlSignalOut``,
``CtrlServiceIn``, the ``add_*`` methods of the live controller) are the
engine's.

This module loads on a machine where no engine is installed.

A controller is a PEER of :class:`muscadet.ObjFlow` (R39).

A muscadet model carries two natures of thing, and until now it had a word for
only one of them. ``ObjFlow`` transports a **conserved quantity**: what leaves
one component arrives at another, the sum over a connection is permanent, and
that is why a flow enters allocation, acyclicity and the balance sweeps. A
controller transports a **reading or a signal**. Nothing is conserved, nothing
is allocated, and republishing a value creates no matter.

That is why ``ObjCtrl`` is a peer of ``ObjFlow`` and not a subclass of it. The
library already had the pattern: :class:`muscadet.ObjLogicGate` and ``ObjFlow``
both descend from the cod3s component and share nothing else. A controller is
built the same way by the engine -- variables and message boxes mounted by
hand, no flow collections at all -- and it is connected the same way, through the RAW
:meth:`muscadet.System.connect`. :meth:`muscadet.System.flow_behind_message_box`
answers "no flow" on both ends of a controller box, so
:meth:`muscadet.System.check_flow_families` judges nothing: an information edge
crosses the discrete/continuous boundary that a flow edge may not.

What a controller declares
--------------------------
Two sections, and the order between them is the one
:data:`muscadet.declare.DECLARATION_SECTIONS` records:

* ``controls_in`` -- **observation inputs**. An input is a measurement channel,
  the very :class:`muscadet.MeasurementIn` a sensor already used, so a
  controller reads a capacity LEVEL (``kind="level"``), the RATE a continuous
  output delivers (``kind="rate"``, R38) or the SHARE one constituent is of what
  a volume holds (``kind="ratio"``) through one concept and one wire shape. It
  reads through PyCATSHOO references, which carry no setter: an observation
  cannot be written, takes no share of what it watches, and adds no edge to the
  graph the acyclicity check walks. An input declaring an ``aggregate`` takes
  SEVERAL sources and reduces them to one value (R40).

  An input of ``kind="service"`` is the one exception, and it is not a
  measurement: a :class:`CtrlServiceIn` observes a BOOLEAN -- a service output
  of a component, or another controller's boolean output -- and keeps it in
  ``{name}_fed_in``, the variable a mode condition reads. It is held in
  ``controls_service_in``, apart from the measurement channels.

  **A ratio input is what a closed grammar costs, and where that cost is paid.**
  There is no division among the four operators below and there will not be
  one: a quotient carries no threshold anything could root-find, so admitting it
  would break the property the whole grammar exists for. A fraction is therefore
  materialised by the volume that holds the constituents -- it publishes
  ``{c}_ratio_{f}`` beside its levels -- and reaches the controller as an
  ordinary reading, which a ``band`` then thresholds like any other. Nothing in
  this module knows about it, and that is the point.

* ``controls_out`` -- **outputs**, of two natures (R3). A ``"bool"`` output is
  one boolean variable exported on ``{name}_out``, exactly what a shipped
  component's discrete control port imports, so a controller drives a source
  with no new mechanism on the driven side. A ``"value"`` output is a
  :class:`muscadet.MeasurementOut` publication on ``{name}_level_out``, which is
  what lets the output of one controller be the input of another (R4) and what
  an instrument publishes to a voter.

**Why an input is not read by a sensitive method.** A sensitive method fires
when a variable ANNOUNCES a change, and a continuous quantity moving inside an
integration step announces nothing. Reading a level that way is not refused by
anything -- it simply never re-evaluates, so the failure is silent. A
controller therefore reads its channels on demand, and the dating of a
threshold crossing is the business of the watched automata the output grammar
compiles into, not of a notification.

**Where an aggregation stops being differentiable** (R41). ``min``, ``max`` and
``median`` are continuous in their readings and change ARGUMENT -- or
representative -- when two of them cross. Nothing announces that: the solver
extrapolates through the kink inside an integration step and picks up the new
representative at the next watched event, silently and by an amount that
depends on the step size. So a rank-sensitive input declares one watched
two-state automaton per PAIR of sources, which is what makes the solver stop AT
the crossing. ``sum`` and ``mean`` are linear and declare none. The count grows
as ``N (N - 1) / 2`` and is capped -- see :data:`AGGREGATION_CROSSING_CAP`. The
library carries kinks of its own that predate this and are NOT declared, its
allocation and reactant minimums among them; this is the doctrine for what a
controller introduces, not a sweep of what came before.

**What an output CARRIES: a closed grammar, never a function** (R42). An
output declares its value under the key ``emit``, and what may be written there
is a composition of exactly four operators: :class:`CtrlCompare` (a reading
against a threshold), :class:`CtrlBand` (two thresholds and a direction),
:class:`CtrlCombine` (booleans by and / or / not / k-of-n) and
:class:`CtrlRepublish` (a reading, times a gain). A Python callable is refused
there **even when it attests its own continuity** the way
:class:`muscadet.Profile` lets a production profile attest it.

The reason is the one the whole module turns on. The solver dates a crossing
exactly only on a form it RECOGNISES: a threshold it can root-find, an edge it
can watch. Nothing can read a threshold out of arbitrary Python, so nothing
could compile one to a watched transition -- and the fallback, a sensitive
method, is precisely the silent failure described above. A closed grammar is
what makes every form compilable: a comparison and each edge of a band become a
watched two-state automaton, exactly like the threshold of a discrete
production condition (R22) and the kinks above, and the output is re-evaluated
on the NOTIFICATION of those automata, which are discrete objects and do
announce their state. Only :class:`CtrlCombine` reads booleans, and it is the
one place where a change announces itself.

An output declaring no ``emit`` keeps the hand-written value
:meth:`CtrlSignalOut.publish` and :meth:`muscadet.MeasurementOut.publish` give
it, which is what a test drives and what the skeleton has always done.

**Every number the grammar carries is a VARIABLE of the model** (R44). A
threshold, a band's two edges and a republication's gain are engine variables
on the controller, not constants captured in the closures that read them. That
is one change with three consequences, and the third is the one this module was
missing: two instances of one class can be tuned to different thresholds, an
indicator can name a threshold as its target, and a **failure mode can move
one**. While a threshold was a captured float there was no value anywhere for
any of the three to reach, and none of them failed loudly -- a model simply
could not be written.

Three effects therefore reach a controller output, each an ordinary component
variable a ``cod3s.ObjFM*`` names by its exact basename:

============================================ ====================================
``{output}_level_gain``                      a value output's publication, scaled
``{output}_forced`` / ``{output}_forced_value``  that publication, replaced
``{output}_signal_available``                a boolean output, blinded
============================================ ====================================

The exact-name spelling is not a fallback, it is the only one available: a
controller is a peer of ``ObjFlow`` and declares no flow at all, so there is
no flow name a pattern could match on it.

**None of these endpoints is reinitialised, and a mode owes both polarities.**
The PyCATSHOO flag governs the reset at every STEP, and a parameter reset at
every step would flap against the standing clamp of the mode holding it -- an
automaton condition reading such a threshold would flap with it. So they hold
what was written, which means a mode that moves one hands it back explicitly,
exactly as :func:`muscadet.derating.release_deratings` makes a mode hand back a
rate. The engine still restores every declared init between Monte Carlo
sequences, so nothing leaks from one run to the next. ``{name}_level_gain`` is
the one endpoint that IS reinitialised: it belongs to
:class:`muscadet.MeasurementOut`, which an ``ObjFlow`` shares, and it is left
exactly as it was.

**Why blinding a boolean output needs an automaton of its own.** A signal
variable is not reinitialised either -- it is a state, not a pulse -- so it
does not fall back when the clamp on its availability is released. Handing the
availability back therefore has to RE-EVALUATE the output, on the returning
edge as much as on the leaving one, and a controller re-evaluates on the
notification of an automaton and on nothing else. Hence one two-state automaton
per boolean output carrying a grammar, whose state IS "this output is blinded"
(:meth:`ObjCtrl.add_blinding_automaton`). It is deliberately NOT watched: its
condition is a boolean written by a discrete event, so there is no crossing for
the integration manager to root-find.

**What a controller does, once built.** It connects, reduces several sources
on one input (R40), declares that reduction's kinks (R41), compiles its outputs
from the closed grammar (R42) and exposes that grammar's numbers to whoever
tunes, observes or breaks them (R44). This module declares all of it and runs
none of it. The order the controllers run in among themselves is derived by
:mod:`muscadet.ordering`, in a band of its own above the measurements (R45):
the equation a controller registers takes its integer from a topological sort
of the signal graph, at the pre-run step, so a chain of controllers settles in
ONE evaluation whatever order the components were declared in.
"""

from .common import copy_declaration
from .ctrl_grammar import (
    CONTROLLER_DECLARATION_KEYS,
    CtrlNode,
    build_ctrl_node,
    check_declaration_keys,
)

#: The declaration kind of a controller, as :mod:`muscadet.declare` reads it.
KIND = "controller"

#: The class name a controller declaration carries.
CLS = "ObjCtrl"

#: What a class of a model may define when it derives from the declaration.
_DECLARING_CLASS_MEMBERS = frozenset({"declaration"})


class ObjCtrl:
    """A controller: it observes quantities and publishes signals.

    Parameters
    ----------
    name : str
        Component name.
    controls_in : list of dict, optional
        Observation inputs, each ``{"name": ..., **keys}``.
    controls_out : list of dict, optional
        Outputs, each ``{"name": ..., "kind": "bool"|"value", ...}``, with the
        grammar it emits under ``emit``.
    label, description, metadata
        As on every component.

    Raises
    ------
    ValueError
        On a key no controller reads, and on an emission the closed grammar
        does not carry. The refusals are the ones a live controller gives, in
        the same words, and they cost no engine object.

    A class of the model
    --------------------
    A model that writes one instrument several times derives this class and
    fixes the interfaces in its constructor. ``system.add_component`` finds it
    by its name, writes its document and has the engine build it::

        class LevelAlarm(muscadet.ObjCtrl):
            def __init__(self, name, **kwargs):
                super().__init__(
                    name,
                    controls_in=[{"name": "tank"}],
                    controls_out=[{"name": "alarm", "kind": "bool", "emit": ...}],
                    **kwargs,
                )

        system.add_component(name="LA1", cls="LevelAlarm")

    Such a class declares; it cannot change how a controller behaves. A class
    that does (a method of its own, a lower ``CROSSING_CAP``) derives from the
    controller class of the engine distribution, and one written on this class
    is refused where it is defined: the engine would build a plain controller
    and drop what the class added, in silence.
    """

    #: The sections a controller is declared with.
    DECLARATION_KEYS = CONTROLLER_DECLARATION_KEYS

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        added = sorted(
            name
            for name in vars(cls)
            if name not in _DECLARING_CLASS_MEMBERS
            and not (name.startswith("__") and name.endswith("__"))
        )
        if added:
            raise TypeError(
                f"{cls.__name__} derives from muscadet.ObjCtrl, which is a "
                f"declaration, and defines {', '.join(added)}. A class of a "
                "model fixes the interfaces of a controller in its __init__ "
                "and nothing else: the engine builds the controller and would "
                "ignore the rest. A class that changes how a controller "
                "behaves derives from the controller class of the engine "
                "distribution (cod3s_pycatshoo.controller.ObjCtrl)."
            )

    def __init__(self, name, label=None, description=None, metadata=None, **kwargs):
        check_declaration_keys(
            f"Object {name}: {type(self).__name__}", kwargs, self.DECLARATION_KEYS
        )

        for entry in kwargs.get("controls_out") or []:
            build_ctrl_node(
                f"Object {name}: controller output "
                f"{dict(entry).get('name')!r} emit",
                dict(entry).get("emit"),
            )

        self.name = name
        self.label = label
        self.description = description
        self.metadata = copy_declaration(metadata or {})
        self.controls_in = [
            copy_declaration(dict(entry)) for entry in kwargs.get("controls_in") or []
        ]
        self.controls_out = [
            _output_entry(entry) for entry in kwargs.get("controls_out") or []
        ]

    def declaration(self):
        """The controller as a component declaration, checked by its reader."""
        spec = {
            "name": self.name,
            "kind": KIND,
            "cls": CLS,
            "source_cls": type(self).__name__,
            "controls_in": copy_declaration(self.controls_in),
            "controls_out": copy_declaration(self.controls_out),
        }
        if self.label is not None:
            spec["label"] = self.label
        if self.description is not None:
            spec["description"] = self.description
        if self.metadata:
            spec["metadata"] = copy_declaration(self.metadata)
        return spec


def _output_entry(entry):
    """One output as data: a grammar handed as nodes is written as a mapping."""
    entry = copy_declaration(dict(entry))
    if _holds_a_node(entry.get("emit")):
        from .declare import _ctrl_emit_spec

        entry["emit"] = _ctrl_emit_spec(build_ctrl_node("emit", entry["emit"]))
    return entry


def _holds_a_node(value):
    """True when a grammar node sits anywhere in ``value``."""
    if isinstance(value, CtrlNode):
        return True
    if isinstance(value, dict):
        return any(_holds_a_node(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_holds_a_node(item) for item in value)
    return False
