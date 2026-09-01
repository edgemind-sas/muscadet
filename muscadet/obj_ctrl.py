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

**Scope of this module today.** A controller declares, builds, connects, and
reduces several sources on one input (R40). The closed output grammar (compare,
band, combine, republish) and the ordering of controllers among themselves are
separate units; the value a ``"bool"`` output carries is written through
:meth:`CtrlSignalOut.publish` until the grammar writes it.
"""

import typing

import pydantic

import cod3s

from .capacity import COMBINE_POLICIES, MeasurementIn, MeasurementOut
from .common import get_pyc_type

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
