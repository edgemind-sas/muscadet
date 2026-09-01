"""ObjCtrl -- the controller, a PEER of :class:`muscadet.ObjFlow` (R39).

A muscadet model carries two natures of thing, and until now it had a word for
only one of them. ``ObjFlow`` transports a **conserved quantity**: what leaves
one component arrives at another, the sum over a connection is permanent, and
that is why a flow enters allocation, acyclicity and the balance sweeps. A
controller transports a **reading or a signal**. Nothing is conserved, nothing
is allocated, and republishing a value creates no matter.

That is why ``ObjCtrl`` is a peer of ``ObjFlow`` and not a subclass of it. The
library already had the pattern: :class:`muscadet.ObjLogicGate` and ``ObjFlow``
both descend from the cod3s component and share nothing else. A controller is
built the same way -- variables and message boxes mounted by hand, no flow
collections at all -- and it is connected the same way, through the RAW
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
  controller reads a capacity LEVEL (``kind="level"``) or the RATE a continuous
  output delivers (``kind="rate"``, R38) through one concept and one wire
  shape. It reads through PyCATSHOO references, which carry no setter: an
  observation cannot be written, takes no share of what it watches, and adds no
  edge to the graph the acyclicity check walks. An input declaring an
  ``aggregate`` takes SEVERAL sources and reduces them to one value (R40).

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

**Scope of this module today.** A controller declares, builds, connects,
reduces several sources on one input (R40) and declares that reduction's kinks
(R41). The closed output grammar (compare, band, combine, republish) and the
ordering of controllers among themselves are separate units; the value a
``"bool"`` output carries is written through :meth:`CtrlSignalOut.publish`
until the grammar writes it.
"""

import typing

import pydantic

import cod3s

from .capacity import (
    COMBINE_MAX,
    COMBINE_MEAN,
    COMBINE_MEDIAN,
    COMBINE_MIN,
    COMBINE_POLICIES,
    COMBINE_SUM,
    MeasurementIn,
    MeasurementOut,
)
from .common import fresh_instant_occ_law, get_pyc_type

#: The CLOSED list an observation input's aggregation is chosen from (R40):
#: minimum, maximum, mean, median and sum, under the names
#: :data:`muscadet.COMBINE_POLICIES` already gives them.
#:
#: An alias, deliberately not a copy. What each name means is settled once, in
#: :mod:`muscadet.capacity` -- notably the median of an EVEN count, which is the
#: mean of the two central readings and not a tie-break -- and a second tuple
#: here would be free to drift from it silently.
#:
#: Closed, and closed to a Python callable too: an output grammar reasons about
#: what an input computes, and it can do that over five names and not over
#: arbitrary Python. ``SensorContinuous`` accepts ``combine_fun`` on its own
#: surface and keeps it; that is an inherited exception of that surface, not a
#: door onto this one. See :meth:`ObjCtrl.check_aggregation`.
CONTROL_AGGREGATIONS = COMBINE_POLICIES

#: The aggregations that are SENSITIVE TO RANK, and therefore the ones a
#: crossing of two readings makes non-differentiable (R41). A minimum and a
#: maximum change ARGUMENT when two readings cross; a median changes
#: REPRESENTATIVE. The reduced value stays continuous through such a point --
#: the two readings are equal there -- so nothing is discontinuous and nothing
#: is refused; what breaks is the derivative, which is what an integration step
#: extrapolates from.
AGGREGATION_KINK_POLICIES = (COMBINE_MIN, COMBINE_MAX, COMBINE_MEDIAN)

#: The aggregations that are LINEAR in their readings, and therefore carry no
#: kink at all: a sum and a mean have the same derivative whatever the order of
#: their arguments, so a crossing is not an event for them.
AGGREGATION_SMOOTH_POLICIES = (COMBINE_SUM, COMBINE_MEAN)

#: How many pair crossings ONE aggregating input may declare.
#:
#: The count grows as ``N (N - 1) / 2`` in the number of sources, and every
#: crossing costs the solver two watched transitions it re-evaluates at each
#: integration step plus, when it happens, two stops.
#:
#: 120 crossings is 16 sources. Dimensioned on the four measurements recorded
#: at the head of ``tests/test_obj_ctrl_kinks_001.py``, not guessed: the cost
#: of the crossings is SUPERLINEAR in their number -- it grows roughly as the
#: 1.5th power of the pair count, so as the third power of the source count --
#: and 120 is the last measured point where the crossings stay under a quarter
#: of the run time. The reference case
#: of seven redundant instruments is 21 crossings and costs under 1 %, so the
#: cap leaves it a factor of six of headroom; what it refuses is a model far
#: past any voting architecture.
#:
#: This is an EARLY refusal, not the guard that matters: the count depends on
#: what a model wires, so the operational ceiling is a role limit applied by
#: whoever emits the model. Lowering it on a subclass is a supported seam --
#: it is read as ``self.CROSSING_CAP`` at every check.
AGGREGATION_CROSSING_CAP = 120

#: A controller output carrying a BOOLEAN signal, exported on ``{name}_out``
#: under the alias ``{name}``: the very box shape a discrete input imports, so
#: a control port consumes a controller's output with no adapter.
CTRL_OUT_BOOL = "bool"

#: A controller output carrying a NUMBER, published on ``{name}_level_out`` as
#: a :class:`muscadet.MeasurementOut`. Indistinguishable from a capacity's own
#: publication by whoever observes it, which is what makes a chain of
#: controllers possible (R4).
CTRL_OUT_VALUE = "value"

#: Every nature a controller output may be declared with.
CTRL_OUT_KINDS = (CTRL_OUT_BOOL, CTRL_OUT_VALUE)

#: Declaration keys an OBSERVATION INPUT reads. All but ``aggregate`` are
#: forwarded verbatim to :class:`muscadet.MeasurementIn`.
#:
#: ``aggregate`` is the ONE door onto how several sources reduce to one value
#: (R40), and it is why the channel's own ``combine`` and ``combine_fun`` stay
#: out: the reduction is a property of the interface, not of the wire, and two
#: ways of stating it would be two answers to one question -- one of them, worse
#: still, able to smuggle in a Python callable the closed list refuses.
CONTROL_IN_KEYS = (
    "kind",
    "flows",
    "level_default",
    "fill_default",
    "rate_default",
    "aggregate",
)

#: Declaration keys a BOOLEAN output reads.
CONTROL_OUT_BOOL_KEYS = ("kind", "default")

#: Declaration keys a VALUE output reads, forwarded verbatim to
#: :class:`muscadet.MeasurementOut`. ``source`` is deliberately absent: what a
#: value output publishes comes from the output grammar, and a second way of
#: saying it would be a second answer to the same question.
CONTROL_OUT_VALUE_KEYS = (
    "kind",
    "flows",
    "level_default",
    "fill_default",
    "gain_default",
)


def check_declaration_keys(where, params, accepted):
    """Refuse a declaration key nothing reads, naming it (R-3, R-15, R39).

    The same discipline as ``ContinuousComponent.DECLARATION_KEYS`` and
    ``FlowContinuous.check_declaration_keys``, and for the same reason: a
    misspelt key is otherwise swallowed whole, and a controller silently
    missing a threshold is indistinguishable from one that never declared any.

    Parameters
    ----------
    where : str
        What is being declared, as the message should name it.
    params : dict
        The declaration keys the caller passed.
    accepted : tuple
        The keys that are read.

    Raises
    ------
    ValueError
        Naming the offending keys and listing the accepted ones.
    """
    unknown = sorted(set(params) - set(accepted))

    if not unknown:
        return

    plural = "s" if len(unknown) > 1 else ""
    unknown_str = ", ".join(repr(key) for key in unknown)
    accepted_str = ", ".join(sorted(accepted))

    raise ValueError(
        f"{where} does not accept declaration key{plural} {unknown_str}; "
        f"it accepts {accepted_str}"
    )


def crossing_count(sources: int) -> int:
    """How many pair crossings ``sources`` readings can produce (R41).

    ``N (N - 1) / 2``: every unordered pair of readings is one place where the
    rank of the two can swap. Fewer than two readings produce none, which is
    why a single-source input -- the default one -- costs nothing at all.

    **Every pair is watched, and not only the pairs that currently matter.** A
    minimum only bends when the crossing involves its current argument, a
    median only when it involves the middle rank -- but WHICH pairs those are
    is a function of the state, and the state is precisely what these automata
    track. Selecting on it would mean knowing the answer before integrating.
    So the count is the pair count, conservatively, and the cap is set on it.
    """
    if sources < 2:
        return 0

    return sources * (sources - 1) // 2


def crossing_pairs(sources: int) -> typing.List[typing.Tuple[int, int]]:
    """The index pairs of :func:`crossing_count`, in a stable order.

    Ordered ``(0, 1), (0, 2), ..., (1, 2), ...`` so that an automaton's name
    is a function of the pair alone: the same model rebuilt names the same
    automata, whatever else changed around it.
    """
    return [
        (first, second)
        for first in range(sources)
        for second in range(first + 1, sources)
    ]


def crossing_condition(var, first, second, above):
    """Condition of one crossing transition of a pair (R41).

    ``above`` selects the direction, exactly as it does for the threshold
    automaton of a discrete production condition: the transition INTO the
    "greater" state fires while ``first`` reads above ``second``, the one back
    out of it when it stops doing so.

    The readings are taken LIVE off the reference, by connection index, so the
    condition and the reduction can never disagree about a value: they read the
    same connections of the same reference. ``value(index)`` is what
    :meth:`muscadet.MeasurementIn.readings` reads too.
    """

    def condition():
        holds = float(var.value(first)) > float(var.value(second))
        return holds if above else (not holds)

    return condition


class CtrlSignalOut(cod3s.ObjCOD3S):
    """A controller's BOOLEAN output: one variable, one export box (R3, R39).

    The box shape is that of a discrete output's data channel -- ``{name}_out``
    exporting under the alias ``{name}`` -- so a shipped component's control
    port imports it without knowing a controller exists. There is no
    availability channel beside it: availability is a property of a transported
    quantity, and this carries a signal.

    The value is WRITTEN, never derived here: :meth:`publish` is the single
    seam, used by a test today and by the output grammar tomorrow. Reading it
    back is :meth:`get_signal`.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    name: str = pydantic.Field(
        ...,
        description=(
            "Interface name. It is the exported alias, so a control port of "
            "the same name imports this output with no adapter."
        ),
    )

    default: bool = pydantic.Field(
        False,
        description=(
            "Value the signal holds before anything writes it, and the value "
            "it returns to at the start of every Monte Carlo sequence."
        ),
    )

    var: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description="The exported boolean variable, created at declaration",
    )

    def box_name(self) -> str:
        """Name of the box this output exports on."""
        return f"{self.name}_out"

    def var_name(self) -> str:
        """Name of the exported variable.

        Distinct from the alias so that a failure mode's effect -- resolved by
        an unanchored regular expression over the component's variable
        basenames -- has a name of its own to anchor on.
        """
        return f"{self.name}_signal_out"

    def add_variables(self, comp: typing.Any) -> None:
        """Create the exported variable on ``comp``."""
        py_type, pyc_type = get_pyc_type("bool")
        self.var = comp.addVariable(self.var_name(), pyc_type, py_type(self.default))
        # NOT reinitialised, and this is the load-bearing choice of the whole
        # interface. In PyCATSHOO the flag governs the reset at every STEP, not
        # the reset between Monte Carlo sequences -- the engine restores a
        # declared init between sequences whatever the flag says, so there is
        # no leak to guard against here. Left reinitialised, the signal would
        # revert to its default at the next step whoever wrote it, which makes
        # it a PULSE; a controller output is a state, and the loop it closes
        # only settles because the state holds. Measured on the shipped source
        # pattern: with the flag on, the control port never sees the signal at
        # all and the source stays idle, silently.
        #
        # The counterpart is the write-safety invariant a non-reinitialised
        # gate carries (see ``FlowDiscreteOut.var_fed_available_out_reset``):
        # it can no longer fall back to rest on its own, so whatever drives it
        # must write BOTH polarities.
        self.var.setReinitialized(False)

    def add_mb(self, comp: typing.Any) -> None:
        """Export the signal, one box and one alias."""
        comp.addMessageBox(self.box_name())
        comp.addMessageBoxExport(self.box_name(), self.var, self.name)

    def publish(self, value: typing.Any) -> None:
        """Write the signal."""
        self.var.setValue(bool(value))

    def get_signal(self) -> bool:
        """The signal currently carried."""
        return bool(self.var.value()) if self.var is not None else bool(self.default)

    def __repr__(self) -> str:
        state = self.get_signal() if self.var is not None else "N/A"
        return f"{self.__class__.__name__} {self.name} = {state}"

    def __str__(self) -> str:
        return self.__repr__()


class ObjCtrl(cod3s.PycComponent):
    """A controller: it observes quantities and publishes signals (R1, R39).

    Built like :class:`muscadet.ObjLogicGate` -- straight off the cod3s
    component, message boxes mounted by hand -- and connected like it, through
    the raw :meth:`muscadet.System.connect`.

    Examples
    --------
    >>> system.add_component(                                   # doctest: +SKIP
    ...     name="CTRL",
    ...     cls="ObjCtrl",
    ...     controls_in=[{"name": "tank"}],
    ...     controls_out=[{"name": "fill", "kind": "bool"}],
    ... )
    >>> system.connect("CAP", "tank_level_out", "CTRL", "tank_level_in")  # doctest: +SKIP
    >>> system.connect("CTRL", "fill_out", "SRC", "fill_in")             # doctest: +SKIP

    Parameters
    ----------
    name : str
        Component name.
    controls_in : list of dict, optional
        Observation inputs, each ``{"name": ..., **CONTROL_IN_KEYS}``. An input
        declaring ``aggregate`` reads several sources at once (R40).
    controls_out : list of dict, optional
        Outputs, each ``{"name": ..., "kind": "bool"|"value", ...}``.

    Notes
    -----
    A controller is NOT built by :func:`muscadet.build_component`, which owns
    the ``ObjFlow`` construction lifecycle (``add_flows`` then ``set_flows``)
    that a peer class does not have. ``ObjLogicGate`` stands outside it for the
    same reason. What :data:`muscadet.declare.DECLARATION_SECTIONS` carries is
    the ORDER the two controller sections are declared in, and the two method
    names that declare them, so that a bridge reading that constant places them
    where they belong instead of forking the order.
    """

    #: The declaration keys the constructor reads. Anything else is refused by
    #: name, BEFORE the engine object exists -- see :meth:`__init__`.
    DECLARATION_KEYS = ("controls_in", "controls_out")

    #: The ceiling on the pair crossings ONE aggregating input may declare
    #: (R41). Read as ``self.CROSSING_CAP`` at every check, so a subclass
    #: lowers it by declaring it -- which is what a test of the refusal does,
    #: and what a deployment wanting a tighter library-side ceiling would do.
    CROSSING_CAP = AGGREGATION_CROSSING_CAP

    def __init__(
        self,
        name,
        label=None,
        description=None,
        metadata=None,
        **kwargs,
    ):
        # Checked before ``super().__init__``, which is what registers the
        # component in ``system.comp``: a refused declaration then costs no
        # engine object and leaves no half-built component behind for the next
        # walk of the system to trip over.
        check_declaration_keys(
            f"Object {name}: {type(self).__name__}",
            kwargs,
            self.DECLARATION_KEYS,
        )

        super().__init__(
            name,
            label=label,
            description=description,
            metadata={} if metadata is None else metadata,
        )

        #: Observation inputs, keyed by interface name, in declaration order.
        self.controls_in: typing.Dict[str, MeasurementIn] = {}

        #: Outputs, keyed by interface name, in declaration order. Holds a
        #: :class:`CtrlSignalOut` or a :class:`muscadet.MeasurementOut`
        #: according to the nature each was declared with.
        self.controls_out: typing.Dict[str, typing.Any] = {}

        #: Every message box the interfaces have claimed, mapped to the
        #: interface that claimed it. See :meth:`claim_box` for what it buys.
        self.interface_boxes: typing.Dict[str, str] = {}

        #: The crossing automata of each aggregating input, keyed by interface
        #: name, once :meth:`add_crossing_automata` has run (R41). An input
        #: that reached it and produced none holds an empty list, which is what
        #: tells "no kink" apart from "not declared yet".
        self.crossing_automata: typing.Dict[str, typing.List[typing.Any]] = {}

        #: How many sources each of those inputs was wired to when its automata
        #: were built. Read back by :meth:`check_crossings_unchanged`.
        self.crossing_sources: typing.Dict[str, int] = {}

        for entry in kwargs.get("controls_in") or []:
            self.add_control_in(**dict(entry))

        for entry in kwargs.get("controls_out") or []:
            self.add_control_out(**dict(entry))

    # ------------------------------------------------------------------
    # What the rest of the library looks for on a component
    # ------------------------------------------------------------------

    @property
    def measurements_in(self) -> typing.Dict[str, MeasurementIn]:
        """The observation inputs, under the name the system resolves them by.

        :meth:`muscadet.System.measurement_observer` answers a measurement link
        from this collection, and it is what makes the constituent diagnostic
        of :meth:`muscadet.System.check_measurement_constituents` reach a
        controller: an input asking for a constituent its publisher does not
        hold is then told what that publisher does hold, instead of failing at
        the engine on a missing alias.
        """
        return self.controls_in

    @property
    def measurements_out(self) -> typing.Dict[str, MeasurementOut]:
        """The VALUE outputs, which are publications like any other.

        Filtered rather than stored apart: ``controls_out`` is one collection
        keyed by interface name, because an output grammar names an interface
        and must not have to know which of the two dictionaries it landed in.
        """
        return {
            iface_name: iface
            for iface_name, iface in self.controls_out.items()
            if isinstance(iface, MeasurementOut)
        }

    # ------------------------------------------------------------------
    # Declaration
    # ------------------------------------------------------------------

    def claim_name(self, name: str) -> None:
        """Refuse an interface name already taken on this controller.

        Inputs and outputs share ONE name space, which is more than the
        collections require: an output grammar names an interface, and a name
        standing for two of them has no unambiguous answer.
        """
        if name in self.controls_in or name in self.controls_out:
            side = "input" if name in self.controls_in else "output"
            raise ValueError(
                f"Object {self.name()}: interface {name!r} is already declared "
                f"as an {side} of this controller. An interface name is unique "
                "across a controller, inputs and outputs together"
            )

    def claim_box(self, name: str, box: str) -> None:
        """Refuse a message box two interfaces would both claim (KD20).

        The box names are DERIVED from the interface names, and two derivations
        meet: a boolean output named ``x_level`` exports on ``x_level_out``,
        which is the very box a value output named ``x`` publishes on. Left to
        the engine, the second declaration fails on a duplicate box, naming the
        box and neither of the two interfaces that disagree about it.
        """
        owner = self.interface_boxes.get(box)

        if owner is not None:
            raise ValueError(
                f"Object {self.name()}: interface {name!r} claims message box "
                f"{box!r}, which interface {owner!r} already publishes on. The "
                "box name is derived from the interface name: rename one of "
                "the two"
            )

        self.interface_boxes[box] = name

    def check_aggregation(self, name: str, aggregate: typing.Any) -> None:
        """Refuse an aggregation outside the closed list, naming it (R40).

        The list is :data:`CONTROL_AGGREGATIONS`, and a **Python callable is
        refused like any other value that is not in it**. That refusal is the
        point of the check, not a side effect of it: the measurement channel
        underneath does accept a callable, so passing the declaration straight
        through would let one in, and it would be read by
        :meth:`muscadet.MeasurementIn.reduce` in preference to every name here.
        An output grammar reasons about what an input computes; five names it
        can reason about, arbitrary Python it cannot.

        Refused BEFORE anything is built, so a rejected declaration leaves no
        variable, no message box and no entry behind.
        """
        if aggregate is None or aggregate in CONTROL_AGGREGATIONS:
            return

        where = f"Object {self.name()}: controller input {name!r}"
        accepted = ", ".join(CONTROL_AGGREGATIONS)

        if callable(aggregate):
            raise ValueError(
                f"{where}: an aggregation is chosen from the closed list "
                f"{accepted}, and a Python callable is not one of them. A "
                "controller interface takes no aggregation function of its "
                "own; the shipped sensor accepts one on its own surface, and "
                "that stays an exception of that surface"
            )

        raise ValueError(
            f"{where}: unknown aggregation {aggregate!r}, expected one of "
            f"{accepted}"
        )

    def add_control_in(self, name, aggregate=None, **params):
        """Declare one observation input (R4, R40).

        The input is a measurement channel: ``kind="level"`` reads a capacity
        level or a republished reading, ``kind="rate"`` reads what a continuous
        output delivers (R38). Wire it with the raw connection, against the box
        the channel names::

            system.connect(holder, "tank_level_out", ctrl, "tank_level_in")

        **Several sources, and how they reduce.** Declaring ``aggregate`` is
        what lets more than one publisher wire onto the input, and what says
        what the input then reads: the readings are reduced by the named policy
        at every read. Declaring nothing keeps the channel's one-publisher cap,
        so many-to-one is never reached without stating how the readings
        combine -- a silent sum over redundant sources is a wrong model, not a
        default::

            controls_in=[{"name": "reading", "aggregate": "median"}]

        The reduction, its five names and their semantics are the measurement
        channel's own (:func:`muscadet.combine`), reused rather than restated:
        a median over an EVEN count is the mean of the two central readings
        there, and it has to be the same here.

        Parameters
        ----------
        name : str
            Interface name. It is also the observed publisher's name, which is
            what makes the exported and imported aliases line up.
        aggregate : str, optional
            How SEVERAL sources reduce to one value: a name from
            :data:`CONTROL_AGGREGATIONS`. ``None`` -- the default -- is the
            single-source input, capped at one publisher.
        **params : dict
            The rest of :data:`CONTROL_IN_KEYS`, forwarded to
            :class:`muscadet.MeasurementIn`.

        Returns
        -------
        muscadet.MeasurementIn

        Raises
        ------
        ValueError
            On an unknown declaration key, an aggregation outside the closed
            list -- a callable included -- a name already taken, or a message
            box another interface already claims. An unknown ``kind`` is
            refused by :class:`muscadet.MeasurementIn`.
        """
        check_declaration_keys(
            f"Object {self.name()}: controller input {name!r}",
            params,
            CONTROL_IN_KEYS,
        )

        self.check_aggregation(name, aggregate)

        self.claim_name(name)

        # An unknown ``kind`` is refused by ``MeasurementIn`` itself, at
        # declaration and naming the key. Refusing it again here would give one
        # mistake two vocabularies, which is worse than one message that says
        # "measurement channel" where the model says "controller input".
        #
        # The aggregation is the one key that does NOT pass through verbatim:
        # the interface says ``aggregate``, the wire underneath says
        # ``combine``, and translating here is what keeps the channel's own two
        # keys out of a controller declaration -- ``combine_fun`` among them.
        channel = MeasurementIn(name=name, combine=aggregate, **params)
        self.claim_box(name, channel.box_name())

        channel.add_variables(self)
        channel.add_mb(self)

        self.controls_in[name] = channel

        return channel

    def add_control_out(self, name, kind=CTRL_OUT_BOOL, **params):
        """Declare one output (R3).

        Parameters
        ----------
        name : str
            Interface name, and the alias the output is exported under.
        kind : str
            :data:`CTRL_OUT_BOOL` -- a boolean signal on ``{name}_out``,
            consumed by a discrete control port -- or :data:`CTRL_OUT_VALUE`
            -- a number published on ``{name}_level_out`` and read by any
            observer, a second controller included (R4).
        **params : dict
            :data:`CONTROL_OUT_BOOL_KEYS` or :data:`CONTROL_OUT_VALUE_KEYS`,
            according to ``kind``.

        Returns
        -------
        CtrlSignalOut or muscadet.MeasurementOut

        Raises
        ------
        ValueError
            On an unknown nature, an unknown declaration key, a name already
            taken, or a message box another interface already claims.
        """
        if kind not in CTRL_OUT_KINDS:
            raise ValueError(
                f"Object {self.name()}: controller output {name!r} has unknown "
                f"kind {kind!r}, expected one of {', '.join(CTRL_OUT_KINDS)}"
            )

        accepted = (
            CONTROL_OUT_BOOL_KEYS if kind == CTRL_OUT_BOOL else CONTROL_OUT_VALUE_KEYS
        )
        check_declaration_keys(
            f"Object {self.name()}: controller output {name!r}",
            params,
            accepted,
        )

        self.claim_name(name)

        if kind == CTRL_OUT_BOOL:
            interface: typing.Any = CtrlSignalOut(name=name, **params)
            box = interface.box_name()
        else:
            interface = MeasurementOut(name=name, **params)
            box = f"{name}_level_out"

        self.claim_box(name, box)

        interface.add_variables(self)
        interface.add_mb(self)

        self.controls_out[name] = interface

        return interface

    # ------------------------------------------------------------------
    # Aggregation kinks (R41)
    # ------------------------------------------------------------------

    def aggregation_has_kinks(self, name: str) -> bool:
        """True when input ``name`` reduces by a rank-sensitive aggregation.

        The whole of what decides whether an input costs crossing automata:
        :data:`AGGREGATION_KINK_POLICIES` on one side, and on the other the
        sum and the mean, which are linear in their readings and therefore have
        the same derivative whatever order the readings arrive in.
        """
        channel = self.controls_in.get(name)

        return channel is not None and channel.combine in AGGREGATION_KINK_POLICIES

    def check_crossing_cap(self, name: str, sources: int) -> None:
        """Refuse an aggregation over too many sources, naming the ceiling.

        Raises
        ------
        ValueError
            Naming the cap, the count reached and the source count that
            produced it.
        """
        count = crossing_count(sources)

        if count <= self.CROSSING_CAP:
            return

        raise ValueError(
            f"Object {self.name()}: controller input {name!r} aggregates by "
            f"{self.controls_in[name].combine!r} over {sources} sources, which "
            f"is {count} pair crossings, above the cap of {self.CROSSING_CAP}. "
            "A rank-sensitive aggregation declares one watched automaton per "
            "pair of sources, and the pair count grows as N (N - 1) / 2: "
            "reduce the sources, or aggregate them in stages through a second "
            "controller"
        )

    def check_incoming_crossing_cap(self, box: str) -> None:
        """Refuse, at the connection that breaks it, a cap the model exceeds.

        The EARLY refusal, and deliberately not the guard: the guard is
        :meth:`add_crossing_automata`, which runs once every connection exists
        and therefore sees the count whatever route the connections took. This
        one is worth having because it names the connection that broke the cap,
        which a refusal raised at the first run cannot do.

        Called by :meth:`muscadet.System.connect` BEFORE the connection is
        made, so a refused model keeps the wiring it had.
        """
        name = self.interface_boxes.get(box)

        if name is None or not self.aggregation_has_kinks(name):
            return

        channel = self.controls_in[name]

        self.check_crossing_cap(name, channel.var_level.cnctCount() + 1)

    def add_crossing_automata(self, system: typing.Any) -> typing.List[typing.Any]:
        """Declare every aggregation kink this controller carries (R41).

        Called once per engine system, from
        :meth:`muscadet.System.prerun_step`, and it has to be called there
        rather than at declaration: the number of sources is a property of the
        WIRING, and no connection exists yet when an input is declared.

        Returns
        -------
        list
            The automata built, empty when no input aggregates by rank.
        """
        built: typing.List[typing.Any] = []

        for name in self.controls_in:
            built.extend(self.add_input_crossing_automata(system, name))

        return built

    def add_input_crossing_automata(
        self, system: typing.Any, name: str
    ) -> typing.List[typing.Any]:
        """Declare the kinks of ONE input. See :meth:`add_crossing_automata`."""
        if name in self.crossing_automata:
            return []

        channel = self.controls_in[name]
        sources = channel.var_level.cnctCount()
        automata: typing.List[typing.Any] = []

        if self.aggregation_has_kinks(name):
            self.check_crossing_cap(name, sources)

            automata = [
                self.add_one_crossing_automaton(system, name, first, second)
                for first, second in crossing_pairs(sources)
            ]

        # EVERY input is recorded, a mean and a single-source one included, and
        # an empty list is a real answer: it says this input was looked at and
        # carries no kink. An input MISSING from the record is a different
        # thing entirely -- a controller the pre-run step never reached -- and
        # the two would be indistinguishable if only the kinked ones were kept.
        self.crossing_automata[name] = automata
        self.crossing_sources[name] = sources

        return automata

    def add_one_crossing_automaton(
        self, system: typing.Any, name: str, first: int, second: int
    ) -> typing.Any:
        """The watched two-state automaton of one pair of sources (R41).

        The very shape the library already uses three times to catch a
        crossing -- a capacity's empty/full bounds, a rule set's mode automaton
        and the threshold automaton of a discrete production condition -- and
        for the same reason: two INSTANTANEOUS transitions, both registered as
        watched, so the solver root-finds the date the two readings meet and
        stops the integration there instead of extrapolating past it.

        The automaton is NOT what the aggregation reads.
        :meth:`muscadet.MeasurementIn.reduce` reads the same references live,
        so the two cannot disagree about a value. What the automaton
        contributes is the STOP at the right date: a continuous reading moving
        inside an integration step announces no change of its own, so nothing
        else would.

        Only the reading the controller consumes is watched -- the channel
        total, what :meth:`muscadet.MeasurementIn.get_reading` answers and what
        the output grammar compares, bands and republishes. A channel's
        constituent and fill readings are reduced by the same policy and carry
        kinks of their own; they belong to the sensor's surface, no controller
        reads them, and watching them would multiply the automata by the
        constituent count for nobody.
        """
        channel = self.controls_in[name]
        var = channel.var_level

        base = f"{name}_cross_{first}_{second}"
        st_le = f"{base}_le"
        st_gt = f"{base}_gt"
        trans_up = f"{base}_up"
        trans_down = f"{base}_down"

        aut = cod3s.PycAutomaton(
            name=f"{self.name()}_{base}",
            states=[st_le, st_gt],
            # Which of the two readings leads cannot be known here: the
            # connections carry their publishers' defaults until the first
            # equation has run. The instantaneous transitions settle the
            # automaton at t = 0, which is one more reason they are watched
            # rather than merely conditioned.
            init_state=st_le,
            transitions=[
                {
                    "name": trans_up,
                    "source": st_le,
                    "target": st_gt,
                    "is_interruptible": True,
                    # A fresh mapping per transition: cod3s rewrites the 'cls'
                    # entry in place while sanitizing it.
                    "occ_law": fresh_instant_occ_law(),
                },
                {
                    "name": trans_down,
                    "source": st_gt,
                    "target": st_le,
                    "is_interruptible": True,
                    "occ_law": fresh_instant_occ_law(),
                },
            ],
        )
        aut.update_bkd(self)

        aut.get_transition_by_name(trans_up)._bkd.setCondition(
            crossing_condition(var, first, second, True)
        )
        aut.get_transition_by_name(trans_down)._bkd.setCondition(
            crossing_condition(var, first, second, False)
        )

        system.pdmp_add_watched_automaton(aut)

        self.automata_d[aut.name] = aut

        return aut

    def crossing_source_counts(self) -> typing.Dict[str, int]:
        """How many sources each rank-sensitive input reads RIGHT NOW."""
        return {
            name: self.controls_in[name].var_level.cnctCount()
            for name in self.controls_in
            if self.aggregation_has_kinks(name)
        }

    def check_crossings_unchanged(self) -> None:
        """Refuse a run whose controller gained a source since its kinks were declared.

        The automata are built once, at the pre-run step, from the connections
        that existed then. A source wired after it would be reduced by the
        aggregation like any other -- ``reduce`` reads ``cnctCount()`` live --
        and its crossings would be the only ones the solver never stopped at.
        Nothing raises on that, and nothing is visibly wrong: the model simply
        overshoots some of its kinks, which is the failure this whole unit
        exists to remove.

        Raises
        ------
        ValueError
            Naming the input and both counts.
        """
        for name, sources in self.crossing_source_counts().items():
            declared = self.crossing_sources.get(name)

            if declared is None or declared == sources:
                continue

            raise ValueError(
                f"Object {self.name()}: controller input {name!r} read "
                f"{declared} sources when its crossing automata were declared "
                f"and reads {sources} now. Those automata are built once, at "
                "the start of the first run, so the crossings of a source "
                "wired since would never stop the integration and the "
                "aggregation would overshoot them silently. Wire every "
                "publisher before the first simulate() / isimu_start()"
            )
