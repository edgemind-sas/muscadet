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
  controller reads a capacity LEVEL (``kind="level"``), the RATE a continuous
  output delivers (``kind="rate"``, R38) or the SHARE one constituent is of what
  a volume holds (``kind="ratio"``) through one concept and one wire shape. It
  reads through PyCATSHOO references, which carry no setter: an observation
  cannot be written, takes no share of what it watches, and adds no edge to the
  graph the acyclicity check walks. An input declaring an ``aggregate`` takes
  SEVERAL sources and reduces them to one value (R40).

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
controller is a peer of ``ObjFlow`` and declares no flow at all, so the
muscadet regex-on-flows spelling of :class:`muscadet.ObjFailureMode` has
nothing to match on it.

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

**Scope of this module today.** A controller declares, builds, connects,
reduces several sources on one input (R40), declares that reduction's kinks
(R41), compiles its outputs from the closed grammar (R42) and exposes that
grammar's numbers to whoever tunes, observes or breaks them (R44). The order the
controllers run in among themselves is derived by :mod:`muscadet.ordering`, in a
band of its own above the measurements (R45): the equation this module declares
takes its integer from a topological sort of the signal graph, at the pre-run
step, so a chain of controllers settles in ONE evaluation whatever order the
components were declared in.
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
from .common import copy_declaration, fresh_instant_occ_law, get_pyc_type
from .rules import COMPARISON_OPERATORS, comparator

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

# ----------------------------------------------------------------------
# The output grammar (R42)
# ----------------------------------------------------------------------

#: A reading compared to a threshold. Compiles to the WATCHED two-state
#: automaton :meth:`ObjCtrl.add_compare_automaton` builds, which is the very
#: shape a discrete production condition uses for the same job (R22).
CTRL_OP_COMPARE = "compare"

#: Two thresholds and a direction: a hysteresis band. Compiles to one watched
#: two-state automaton whose two edges are the two thresholds, which is what
#: the shipped sensor already does with an ``add_atm2states`` pair.
CTRL_OP_BAND = "band"

#: Booleans reduced by ``and``, ``or``, ``not`` or k-of-n. The ONE operator
#: whose operands are already discrete, and therefore the one place where a
#: change announces itself.
CTRL_OP_COMBINE = "combine"

#: A reading published as a number, multiplied by a gain. Compiles to a PDMP
#: equation rather than to an automaton: it carries a quantity, and a quantity
#: has no crossing to date.
CTRL_OP_REPUBLISH = "republish"

#: The operators that answer a BOOLEAN, and therefore the ones a
#: :data:`CTRL_OUT_BOOL` output may emit.
CTRL_BOOL_OPERATORS = (CTRL_OP_COMPARE, CTRL_OP_BAND, CTRL_OP_COMBINE)

#: The operators that answer a NUMBER, and therefore the ones a
#: :data:`CTRL_OUT_VALUE` output may emit.
CTRL_VALUE_OPERATORS = (CTRL_OP_REPUBLISH,)

#: The CLOSED list an output value is composed from (R42).
#:
#: Closed, and closed to a Python callable above all. muscadet already drew
#: this conclusion on production profiles: a callable must ATTEST its own
#: continuity because nothing can inspect it, and a discontinuous one is
#: refused outright because the solver would walk through the break inside an
#: integration step. An output value needs more than continuity -- it needs a
#: form a threshold can be read out of -- so the attestation buys nothing here
#: and a callable is refused whatever it carries. See
#: :func:`build_ctrl_node`.
CTRL_OPERATORS = CTRL_BOOL_OPERATORS + CTRL_VALUE_OPERATORS

#: A band detecting a reading that has risen ABOVE its activation level. Its
#: release edge then sits at or below that level.
CTRL_BAND_ABOVE = "above"

#: A band detecting a reading that has fallen BELOW its activation level. Its
#: release edge then sits at or above that level.
CTRL_BAND_BELOW = "below"

#: Every direction a band may be declared in.
CTRL_BAND_DIRECTIONS = (CTRL_BAND_ABOVE, CTRL_BAND_BELOW)

#: The comparison operators of each band edge, per direction: the activation
#: one first, the release one second.
#:
#: The release edge is deliberately STRICT, exactly as the shipped sensor's is
#: and for the same reason: the degenerate band -- the two levels coinciding --
#: then leaves the two edges mutually exclusive, so the output switches at that
#: single level instead of having both edges hold at once.
CTRL_BAND_EDGE_OPERATORS = {
    CTRL_BAND_ABOVE: (">=", "<"),
    CTRL_BAND_BELOW: ("<=", ">"),
}

#: What a comparison's threshold variable is named from: ``{output}{path}_
#: threshold``, ``path`` being the node's POSITION in the output's tree, the
#: very one :meth:`ObjCtrl.emit_automaton_base` names the automata from. Two
#: comparisons on one input against two levels are therefore two variables, and
#: a model rebuilt names them the same way.
CTRL_PARAM_THRESHOLD = "threshold"

#: What a band's activation edge is named from.
CTRL_PARAM_ACTIVATE = "activate"

#: What a band's release edge is named from.
CTRL_PARAM_RELEASE = "release"

#: Every number of the grammar that becomes a variable of the model (R44), in
#: the order a node declares them.
CTRL_PARAMS = (CTRL_PARAM_THRESHOLD, CTRL_PARAM_ACTIVATE, CTRL_PARAM_RELEASE)

#: The variable a failure mode clamps to blind a BOOLEAN output:
#: Two modes clamping one endpoint are last-writer-wins. The min-composition
#: that ``derating.py`` gives a continuous output has no equivalent here, so a
#: redundancy model pinching one endpoint from two modes reads whichever wrote
#: last. Named rather than guarded.
#:
#: ``{output}_signal_available``, created at True. False makes the output carry
#: its declared default -- for a control port, no order at all -- while
#: everything upstream of it goes on being right.
CTRL_AVAILABLE_SUFFIX = "signal_available"

#: The variable a failure mode raises to force a VALUE output:
#: ``{output}_forced``, created at False.
CTRL_FORCED_SUFFIX = "forced"

#: What a forced VALUE output publishes instead of its reading:
#: ``{output}_forced_value``, created at 0. Two variables and not one because a
#: number has no rest value a single flag could stand for -- which is exactly
#: what a boolean output's default IS, and why blinding one takes no second
#: variable.
CTRL_FORCED_VALUE_SUFFIX = "forced_value"

#: What the automaton carrying a boolean output's blinding is named from.
CTRL_BLINDING_SUFFIX = "blinding"

#: Conjunction: every operand holds.
CTRL_LOGIC_AND = "and"

#: Disjunction: at least one operand holds.
CTRL_LOGIC_OR = "or"

#: Negation: the single operand does not hold.
CTRL_LOGIC_NOT = "not"

#: k-of-n: at least ``k`` of the operands hold. The voting shape a redundant
#: instrument set exists for.
CTRL_LOGIC_K = "k"

#: The CLOSED list a combination is chosen from.
CTRL_LOGICS = (CTRL_LOGIC_AND, CTRL_LOGIC_OR, CTRL_LOGIC_NOT, CTRL_LOGIC_K)


class CtrlNode(pydantic.BaseModel):
    """One node of an output value (R42): an operator and its operands.

    Never built directly by a model: :func:`build_ctrl_node` is the door, and
    it is what turns a declaration into a tree, refusing by name what the
    closed grammar does not carry.

    Every semantic refusal lives in a validator of the node itself rather than
    in the factory, so a node handed round the library is a node that was
    checked -- and the factory only adds WHERE the mistake was made.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    #: True when this operator answers a boolean, False when it answers a
    #: number. A class fact, not a field: it is a property of the OPERATOR.
    IS_BOOLEAN: typing.ClassVar[bool] = True

    op: str = pydantic.Field(..., description="The operator this node applies")

    def operand_nodes(self) -> typing.List["CtrlNode"]:
        """The sub-nodes this one reduces. Empty on a leaf."""
        return []

    def inputs_read(self) -> typing.List[str]:
        """Every observation input this subtree reads, in declaration order."""
        found: typing.List[str] = []

        for operand in self.operand_nodes():
            for name in operand.inputs_read():
                if name not in found:
                    found.append(name)

        return found


class CtrlCompare(CtrlNode):
    """A reading against a threshold (R42).

    Examples
    --------
    >>> {"op": "compare", "input": "tank", "operator": ">=", "threshold": 5.0}
    {'op': 'compare', 'input': 'tank', 'operator': '>=', 'threshold': 5.0}
    """

    op: str = pydantic.Field(CTRL_OP_COMPARE, description="Always 'compare'")

    input: str = pydantic.Field(
        ..., description="Name of the observation input whose reading is compared"
    )

    operator: str = pydantic.Field(
        ...,
        description=(
            "A name from muscadet.rules.COMPARISON_OPERATORS. The very "
            "vocabulary a rule guard (R21) and a discrete production condition "
            "(R22) compare with: one comparison vocabulary for the library, "
            "one implementation."
        ),
    )

    threshold: float = pydantic.Field(
        ...,
        description=(
            "The level the reading is compared to. The INITIAL value of "
            "'{output}{path}_threshold' (R44): what the comparison reads is "
            "that variable, so an instance may be tuned away from its class, "
            "an indicator may name it and a failure mode may move it."
        ),
    )

    @pydantic.model_validator(mode="after")
    def check_operator(self) -> "CtrlCompare":
        """Refuse a comparison operator the library does not carry."""
        if self.operator not in COMPARISON_OPERATORS:
            raise ValueError(
                f"a comparison operator is one of "
                f"{', '.join(COMPARISON_OPERATORS)}, got {self.operator!r}"
            )

        return self

    def inputs_read(self) -> typing.List[str]:
        return [self.input]


class CtrlBand(CtrlNode):
    """A hysteresis band: two thresholds and the direction they are read in.

    What a band buys over a comparison is the whole of why it is an operator of
    its own: a comparison switches back the instant its condition stops
    holding, so a montage gated on one chatters around a single level. A band
    holds between its two edges, which is what lets the quantity it controls
    actually move.

    Examples
    --------
    >>> {"op": "band", "input": "tank", "direction": "below",
    ...  "activate": 3.0, "release": 7.0}      # doctest: +NORMALIZE_WHITESPACE
    {'op': 'band', 'input': 'tank', 'direction': 'below', 'activate': 3.0,
     'release': 7.0}
    """

    op: str = pydantic.Field(CTRL_OP_BAND, description="Always 'band'")

    input: str = pydantic.Field(
        ..., description="Name of the observation input whose reading is banded"
    )

    direction: str = pydantic.Field(
        CTRL_BAND_ABOVE,
        description=(
            "'above' to activate when the reading rises past the activation "
            "level, 'below' to activate when it falls past it. It is what "
            "fixes on which side of that level the release edge must sit."
        ),
    )

    activate: float = pydantic.Field(
        ...,
        description=(
            "The level the band switches ON at, and the initial value of "
            "'{output}{path}_activate' (R44)."
        ),
    )

    release: typing.Optional[float] = pydantic.Field(
        None,
        description=(
            "The level it switches OFF at, and the initial value of "
            "'{output}{path}_release' (R44). None -- the default -- coincides "
            "with the activation level, which is the degenerate band: no "
            "hysteresis, and the two edges still mutually exclusive because "
            "the release comparison is strict."
        ),
    )

    @pydantic.model_validator(mode="after")
    def check_edges(self) -> "CtrlBand":
        """Refuse a direction the grammar does not carry, and an inverted band.

        An inverted band is not a subtle mistake with a subtle consequence: a
        band detecting BELOW 3 and releasing at 1 can never release, because
        the reading has to fall to 1 while the band is what stops it falling.
        The montage then latches on its first activation and never speaks
        again, with nothing raised anywhere.
        """
        if self.direction not in CTRL_BAND_DIRECTIONS:
            raise ValueError(
                f"a band detects in one of {', '.join(CTRL_BAND_DIRECTIONS)}, "
                f"got {self.direction!r}"
            )

        if self.release is None:
            self.release = self.activate

            return self

        inverted = (
            self.release > self.activate
            if self.direction == CTRL_BAND_ABOVE
            else self.release < self.activate
        )

        if inverted:
            side = "below" if self.direction == CTRL_BAND_ABOVE else "above"
            raise ValueError(
                f"a band detecting {self.direction!r} {self.activate} releases "
                f"at or {side} it, not at {self.release}"
            )

        return self

    def edge_operators(self) -> typing.Tuple[str, str]:
        """The activation and release comparison operators of this band."""
        return CTRL_BAND_EDGE_OPERATORS[self.direction]

    def inputs_read(self) -> typing.List[str]:
        return [self.input]


class CtrlCombine(CtrlNode):
    """Booleans reduced by and / or / not / k-of-n (R42).

    Examples
    --------
    >>> {"op": "combine", "logic": "k", "k": 2, "operands": [
    ...     {"op": "compare", "input": "a", "operator": ">=", "threshold": 1.0},
    ...     {"op": "compare", "input": "b", "operator": ">=", "threshold": 1.0},
    ...     {"op": "compare", "input": "c", "operator": ">=", "threshold": 1.0},
    ... ]}                                              # doctest: +ELLIPSIS
    {'op': 'combine', 'logic': 'k', 'k': 2, 'operands': [...]}
    """

    op: str = pydantic.Field(CTRL_OP_COMBINE, description="Always 'combine'")

    logic: str = pydantic.Field(..., description=f"One of {', '.join(CTRL_LOGICS)}")

    k: typing.Optional[int] = pydantic.Field(
        None,
        description=(
            "How many operands must hold, for logic='k' and for it alone. "
            "Refused elsewhere rather than ignored: a k declared beside an "
            "'or' is a modeller who meant k-of-n."
        ),
    )

    operands: typing.List[typing.Any] = pydantic.Field(
        ...,
        description=(
            "The sub-nodes combined, already built. Typed loosely on purpose: "
            "build_ctrl_node walks the declaration depth first and hands this "
            "field nodes, so the recursion has exactly one entry point."
        ),
    )

    @pydantic.model_validator(mode="after")
    def check_logic(self) -> "CtrlCombine":
        """Refuse an unusable combination, naming what is wrong with it.

        The vacuous cases are refused rather than answered. ``any([])`` is
        False and ``all([])`` is True, so an empty combination is a silent
        constant -- and a constant output is a controller that does nothing,
        which is not what anyone declares a controller for.
        """
        if self.logic not in CTRL_LOGICS:
            raise ValueError(
                f"a combination is one of {', '.join(CTRL_LOGICS)}, got "
                f"{self.logic!r}"
            )

        for operand in self.operands:
            if not isinstance(operand, CtrlNode):
                raise ValueError(f"operand {operand!r} is not a grammar node")

            if not operand.IS_BOOLEAN:
                raise ValueError(
                    f"a combination reduces conditions, and operand "
                    f"{operand.op!r} carries a number"
                )

        if not self.operands:
            raise ValueError("a combination with no operand is a constant")

        if self.logic == CTRL_LOGIC_NOT and len(self.operands) != 1:
            raise ValueError(f"a 'not' negates ONE operand, got {len(self.operands)}")

        if self.logic != CTRL_LOGIC_K:
            if self.k is not None:
                raise ValueError(
                    f"'k' counts the operands of a {CTRL_LOGIC_K!r} "
                    f"combination and has no meaning beside a "
                    f"{self.logic!r} one"
                )

            return self

        if not isinstance(self.k, int) or isinstance(self.k, bool) or self.k < 1:
            raise ValueError(
                f"a {CTRL_LOGIC_K!r} combination counts at least one operand, "
                f"got k={self.k!r}"
            )

        if self.k > len(self.operands):
            raise ValueError(
                f"a {CTRL_LOGIC_K!r} combination asks for {self.k} of "
                f"{len(self.operands)} operands, which can never hold"
            )

        return self

    def operand_nodes(self) -> typing.List[CtrlNode]:
        return list(self.operands)


class CtrlRepublish(CtrlNode):
    """A reading published as a number, multiplied by a gain (R42).

    The ONE operator that answers a quantity, and therefore the one that
    compiles to a PDMP equation instead of to an automaton: what it carries is
    refreshed at every integration step, exactly as every other published
    measurement is, and it has no crossing for anything to date.

    **Where the gain lives, and why it matters here.** It is written into
    ``{name}_level_gain``, the public variable :class:`muscadet.MeasurementOut`
    creates on every publication and multiplies everything it publishes by. So
    the number a model declares here IS the endpoint a failure mode clamps -- a
    gain of 0 is a dead instrument, a gain of 5 a wild one -- and R44 added no
    plumbing at all on this side. That is also why ``gain_default`` may not be
    declared beside it: one number, one spelling.

    A gain of 0 annuls the READING and nothing else: what the channel observes
    goes on moving underneath it, which is the difference between a dead
    instrument and an empty tank. Forcing the publication to a number of its
    own is the other effect, and it is a different one:
    :data:`CTRL_FORCED_SUFFIX`.

    Examples
    --------
    >>> {"op": "republish", "input": "reading", "gain": 1.0}
    {'op': 'republish', 'input': 'reading', 'gain': 1.0}
    """

    IS_BOOLEAN: typing.ClassVar[bool] = False

    op: str = pydantic.Field(CTRL_OP_REPUBLISH, description="Always 'republish'")

    input: str = pydantic.Field(
        ..., description="Name of the observation input whose reading is published"
    )

    gain: float = pydantic.Field(
        1.0,
        description=(
            "The factor everything published is multiplied by. Lands in "
            "'{name}_level_gain', the variable a failure mode clamps."
        ),
    )

    def inputs_read(self) -> typing.List[str]:
        return [self.input]


#: Which node class each operator builds.
CTRL_NODE_CLASSES: typing.Dict[str, typing.Type[CtrlNode]] = {
    CTRL_OP_COMPARE: CtrlCompare,
    CTRL_OP_BAND: CtrlBand,
    CTRL_OP_COMBINE: CtrlCombine,
    CTRL_OP_REPUBLISH: CtrlRepublish,
}


def describe_node_error(item: typing.Any) -> str:
    """One pydantic error, rendered as the sentence a modeller needs.

    Pydantic prefixes a validator's own message with ``Value error,`` and
    reports an unknown key without naming it in the message -- the key is in
    ``loc``. Both are undone here so that a grammar refusal reads like every
    other refusal of this module.
    """
    message = str(item.get("msg", "")).replace("Value error, ", "")
    location = ".".join(str(part) for part in item.get("loc") or ())

    if item.get("type") in ("extra_forbidden", "missing") and location:
        return f"{message} ({location})"

    return message


def build_ctrl_node(where: str, spec: typing.Any) -> typing.Optional[CtrlNode]:
    """Turn one ``emit`` declaration into a grammar node, or refuse it (R42).

    The single door onto the grammar, and therefore the single place a Python
    callable is turned away. Recursive: a combination's operands are built
    first, so a node always holds nodes and never raw mappings.

    Parameters
    ----------
    where : str
        What is being declared, as the message should name it.
    spec : dict or None
        The declaration. ``None`` is an output nothing computes, which keeps
        the hand-written value :meth:`CtrlSignalOut.publish` gives it.

    Returns
    -------
    CtrlNode or None

    Raises
    ------
    ValueError
        On anything that is not a mapping -- a Python callable included,
        whatever continuity it attests -- on an unknown operator, on an unknown
        key, and on every semantic refusal the node classes carry.
    """
    if spec is None:
        return None

    if isinstance(spec, CtrlNode):
        return spec

    operators = ", ".join(CTRL_OPERATORS)

    if not isinstance(spec, dict):
        raise ValueError(
            f"{where}: an output value is a COMPOSITION of the closed "
            f"operators {operators}, written as a mapping carrying an 'op' "
            f"key, and {spec!r} is not one. A Python callable is refused here "
            "whatever it attests: muscadet.Profile's continuity attestation "
            "lets a function scale a production rate, and it still leaves "
            "nothing a threshold can be read out of -- so nothing the "
            "integration manager could watch, and no way to date a crossing "
            "other than stepping over it"
        )

    spec = copy_declaration(spec)
    op = spec.pop("op", None)
    node_class = CTRL_NODE_CLASSES.get(op)

    if node_class is None:
        raise ValueError(
            f"{where}: unknown operator {op!r}, expected one of {operators}"
        )

    if op == CTRL_OP_COMBINE:
        spec["operands"] = [
            build_ctrl_node(f"{where} operand {index}", operand)
            for index, operand in enumerate(spec.get("operands") or [])
        ]

    try:
        return node_class(op=op, **spec)
    except pydantic.ValidationError as error:
        reasons = "; ".join(describe_node_error(item) for item in error.errors())

        raise ValueError(f"{where}: {reasons}") from None


def band_edge_condition(
    read: typing.Callable,
    compare_fun: typing.Callable,
    level: typing.Any,
    holds: bool,
) -> typing.Callable:
    """Condition of one edge of a band, or of one side of a comparison (R42).

    ``holds`` selects the direction, exactly as it does for the threshold
    automaton of a discrete production condition (R22) and for the crossing
    automaton of an aggregation kink (R41): the transition INTO the far state
    fires while the comparison holds, the one back out of it when it stops.

    Both operands are taken LIVE, and for the same reason on each side. The
    reading is live so that the automaton and whatever reads the output can
    never disagree about a value: what the automaton contributes is the STOP at
    the right date and the notification that re-runs the output there. The
    LEVEL is live because it is a variable of the model since R44 -- an
    instance is tuned by writing it and a failure mode moves it -- and a level
    captured here would leave the automaton dating a crossing of the level the
    declaration named while the output compared against the current one.

    Parameters
    ----------
    read : callable
        ``f() -> float``, the reading this edge compares.
    compare_fun : callable
        The comparison, from :func:`muscadet.rules.comparator`.
    level : PyCATSHOO variable
        The threshold or band edge, read at every evaluation.
    holds : bool
        True for the transition INTO the far state, False for the way back.
    """

    def condition() -> bool:
        satisfied = bool(compare_fun(float(read()), float(level.value())))

        return satisfied if holds else (not satisfied)

    return condition


def blinding_condition(var: typing.Any, blinded: bool) -> typing.Callable:
    """Condition of one edge of a boolean output's blinding automaton (R44).

    ``blinded`` selects the direction, as ``holds`` does above: the transition
    INTO the blinded state fires while the availability is False, the one back
    out of it when the availability returns.

    Both edges exist, and the returning one is the load-bearing half. A signal
    variable is not reinitialised -- it is a state, not a pulse -- so releasing
    the clamp does not put the output back: only a notification re-runs the
    grammar, and only this automaton's second edge produces one.
    """

    def condition() -> bool:
        available = bool(var.value())

        return (not available) if blinded else available

    return condition


def combine_reader(
    node: "CtrlCombine", readers: typing.List[typing.Callable]
) -> typing.Callable:
    """The closure one combination reduces its operands with (R42).

    The only operator whose operands are already BOOLEANS, and therefore the
    only one that adds no automaton of its own: what it reduces are the states
    of the automata its subtree declared, and a state announces itself. The
    library has the shape already -- :class:`muscadet.ObjLogicGate` is exactly
    this reduction over the variables of connected components -- and this is
    the same four kinds, over the compiled operands of one controller instead.

    Parameters
    ----------
    node : CtrlCombine
        The validated combination. Its vacuous cases were refused at
        declaration, so nothing here has to answer what ``any([])`` means.
    readers : list of callable
        One closure per operand, in declaration order.
    """
    logic = node.logic

    if logic == CTRL_LOGIC_AND:
        return lambda: all(read() for read in readers)

    if logic == CTRL_LOGIC_OR:
        return lambda: any(read() for read in readers)

    if logic == CTRL_LOGIC_NOT:
        first = readers[0]

        return lambda: not first()

    # An int, and settled long before here: ``CtrlCombine.check_logic`` refuses
    # a k-of-n whose count is missing, not an integer, below one, or above the
    # operand count. The cast says that rather than re-deciding it.
    count = typing.cast(int, node.k)

    return lambda: sum(1 for read in readers if read()) >= count


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
    "ratio_default",
    "aggregate",
)

#: Declaration keys a BOOLEAN output reads. ``emit`` is the output grammar
#: (R42): the condition the signal carries, as a composition of
#: :data:`CTRL_BOOL_OPERATORS`.
CONTROL_OUT_BOOL_KEYS = ("kind", "default", "emit")

#: Declaration keys a VALUE output reads, forwarded verbatim to
#: :class:`muscadet.MeasurementOut`. ``source`` is deliberately absent: what a
#: value output publishes comes from the output grammar, and a second way of
#: saying it would be a second answer to the same question.
CONTROL_OUT_VALUE_KEYS = (
    "kind",
    "flows",
    "level_default",
    "fill_default",
    "ratio_default",
    "gain_default",
    "emit",
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
    availability CHANNEL beside it: availability is a property of a transported
    quantity, and this carries a signal.

    The value is WRITTEN, never derived here: :meth:`publish` is the single
    seam, used by a test and by the output grammar. Reading it back is
    :meth:`get_signal`.

    **What a failure mode reaches here** (R44): ``{name}_signal_available``, a
    variable of the component created at True, read at that single seam. False
    makes the output carry its declared ``default`` -- for a control port, no
    order at all -- while the reading, the band and the whole of the montage
    upstream go on being right. That is the blinded instrument, and it is why
    the effect is not spelled as a value: a boolean output HAS a rest value,
    and blinding it is publishing that one.

    Blinding a hand-written output takes effect at the next call to
    :meth:`publish`, there being no grammar to re-run; an output the grammar
    drives is re-evaluated at the instant the availability turns, in either
    direction (:meth:`ObjCtrl.add_blinding_automaton`).
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

    var_available: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description=(
            "The public endpoint a failure mode clamps to blind this output "
            "(R44). Created at True; muscadet never writes it."
        ),
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

    def available_var_name(self) -> str:
        """Name of the availability endpoint a failure mode clamps (R44)."""
        return f"{self.name}_{CTRL_AVAILABLE_SUFFIX}"

    def add_variables(self, comp: typing.Any) -> None:
        """Create the exported variable and its availability endpoint."""
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
        # must write BOTH polarities. That invariant is exactly what
        # ``ObjCtrl.add_blinding_automaton`` answers for the availability
        # endpoint below.
        self.var.setReinitialized(False)

        self.var_available = comp.addVariable(
            self.available_var_name(), pyc_type, py_type(True)
        )
        # Not reinitialised either, and for the reason its own docstring gives
        # (R44): reset at every step, it would flap against the standing clamp
        # of the mode holding it. So it keeps what was written, and a mode that
        # blinds this output hands the availability back itself.
        self.var_available.setReinitialized(False)

    def add_mb(self, comp: typing.Any) -> None:
        """Export the signal, one box and one alias.

        The availability endpoint is deliberately NOT exported: it is what a
        failure mode writes, not what a consumer reads. A control port sees one
        boolean, and a blinded output is indistinguishable from an output whose
        condition simply does not hold -- which is the whole of the scenario.
        """
        comp.addMessageBox(self.box_name())
        comp.addMessageBoxExport(self.box_name(), self.var, self.name)

    def publish(self, value: typing.Any) -> None:
        """Write the signal, or the default while this output is blinded (R44).

        The single write seam, so the blinding is honoured whether the value
        comes from the output grammar or from a hand-written call.
        """
        self.var.setValue(bool(value) if self.get_available() else bool(self.default))

    def get_available(self) -> bool:
        """False while a failure mode holds this output blinded (R44)."""
        if self.var_available is None:
            return True

        return bool(self.var_available.value())

    def get_signal(self) -> bool:
        """The signal currently carried."""
        return bool(self.var.value()) if self.var is not None else bool(self.default)

    def __repr__(self) -> str:
        state = self.get_signal() if self.var is not None else "N/A"
        if self.var is not None and not self.get_available():
            state = f"{state} (blinded)"
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

    Tuning one instance away from its class (R44)
    ---------------------------------------------
    Every number the output grammar carries is a variable of the component, so
    two instances of one class are separated by writing them. Written BEFORE
    the run starts, which is when PyCATSHOO takes a write as the variable's
    INITIAL value, so the tuning survives every Monte Carlo sequence::

        system.comp["SENSOR_LOW"].variable("alarm_threshold").setValue(2.0)
        system.comp["SENSOR_HIGH"].variable("alarm_threshold").setValue(8.0)

    The same names are what an indicator targets::

        system.add_indicator_var(component="^SENSOR_LOW$", var="^alarm_threshold$")

    What a failure mode reaches, and how it is spelled (R44)
    --------------------------------------------------------
    A controller declares no flow, so the muscadet regex-on-flows spelling of
    :class:`muscadet.ObjFailureMode` has nothing to match on it. The spelling
    is the engine's own, an exact variable basename on a ``cod3s.ObjFM*``, and
    the three effects are::

        # a value output's publication, scaled -- 0 is a dead instrument
        system.add_component(
            cls="ObjFMDelay", fm_name="dead", targets=["CTRL"],
            failure_param=6.0,
            failure_effects={"gauge_level_gain": 0.0},
            repair_param=2.0,
            repair_effects={"gauge_level_gain": 1.0},
        )

        # a value output's publication, replaced by a number of its own
        system.add_component(
            cls="ObjFMDelay", fm_name="stuck", targets=["CTRL"],
            failure_param=4.0,
            failure_effects={"echo_forced": True, "echo_forced_value": 42.0},
            repair_param=2.0,
            repair_effects={"echo_forced": False},
        )

        # a boolean output blinded: the driven equipment stops being told
        system.add_component(
            cls="ObjFMDelay", fm_name="blind", targets=["CTRL"],
            failure_param=9.0,
            failure_effects={"fill_signal_available": False},
            repair_param=3.0,
            repair_effects={"fill_signal_available": True},
        )

    **Both directions, always.** None of these endpoints is reinitialised, so
    releasing a clamp puts nothing back: a mode that moves one hands it back,
    exactly as :func:`muscadet.derating.release_deratings` makes a mode hand a
    rate back. A mode that never repairs (``repair_cond=False``) needs no
    return effect, and models an instrument nobody comes to fix.

    A misspelt endpoint is refused by ``cod3s.ObjFM`` at construction, naming
    the variable and the target: the surface is closed, so a typo is loud.

    Notes
    -----
    A controller is NOT built by :func:`muscadet.build_component`, which owns
    the ``ObjFlow`` construction lifecycle (``add_flows`` then ``set_flows``)
    that a peer class does not have. ``ObjLogicGate`` stands outside it for the
    same reason. What :data:`muscadet.declare.DECLARATION_SECTIONS` carries is
    the ORDER the two controller sections are declared in, and the two method
    names that declare them, so that a bridge reading that constant places them
    where they belong instead of forking the order.

    What a mode clamps on a VALUE output is read back by the PDMP equation at
    every integration step, so it is in force from its own event onward and
    read at the first integration point past it -- the behaviour
    ``{name}_level_gain`` has always had. What it clamps on a BOOLEAN output is
    read at the single write seam, and the output is re-evaluated at the
    instant the availability turns, in either direction.
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

        # The grammar of every output is checked here too, and for the same
        # reason: a malformed one then costs no engine object. It is checked
        # again in ``add_control_out``, which is a public door of its own -- the
        # walk is pure, so doing it twice buys the early refusal for nothing.
        for entry in kwargs.get("controls_out") or []:
            build_ctrl_node(
                f"Object {name}: controller output "
                f"{dict(entry).get('name')!r} emit",
                dict(entry).get("emit"),
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

        #: What each output emits (R42), keyed by interface name: a
        #: :class:`CtrlNode` tree, or ``None`` for an output nothing computes
        #: and whose value is written by hand.
        self.controls_emit: typing.Dict[str, typing.Optional[CtrlNode]] = {}

        #: The automata each output's grammar compiled to, keyed by interface
        #: name. An output that reached the compiler and produced none holds an
        #: empty list, which is what tells "nothing to watch" -- a republication
        #: -- apart from "not compiled".
        self.emit_automata: typing.Dict[str, typing.List[typing.Any]] = {}

        #: The closure each VALUE output's republication reads, keyed by
        #: interface name. Walked by :meth:`compute_controls`, the one PDMP
        #: equation a controller registers.
        self.emit_republications: typing.Dict[str, typing.Callable] = {}

        #: Every number the grammar turned into a variable of the model (R44),
        #: keyed by the variable's basename: the thresholds of the comparisons
        #: and the two edges of the bands, in declaration order. What a model
        #: tunes, what an indicator names, what a failure mode moves.
        self.emit_params: typing.Dict[str, typing.Any] = {}

        #: The forcing endpoints of each VALUE output that republishes (R44),
        #: keyed by interface name: ``(flag, value)``. Read by
        #: :meth:`compute_controls` at every integration step, which is what
        #: makes a forced publication hold and what makes it retreat.
        self.emit_forced: typing.Dict[str, typing.Tuple[typing.Any, typing.Any]] = {}

        #: The blinding automaton of each BOOLEAN output carrying a grammar
        #: (R44), keyed by interface name. Kept apart from
        #: :attr:`emit_automata`, which is what the output's VALUE compiled to:
        #: this one carries no threshold and dates no crossing, it only makes
        #: the output re-evaluate when its availability turns.
        self.blinding_automata: typing.Dict[str, typing.Any] = {}

        #: True once :meth:`compute_controls` is registered as an equation. One
        #: registration covers every republication of the component, as
        #: ``compute_measurements`` does on an ``ObjFlow``. Written at the
        #: PRE-RUN step (R45), by :meth:`register_control_equation`: the
        #: integer comes from the signal graph, which does not exist while a
        #: component is still being declared.
        self.emit_equation_registered = False

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
        output delivers (R38), ``kind="ratio"`` reads the share of the ONE
        constituent its ``flows`` names. Wire it with the raw connection,
        against the box the channel names::

            system.connect(holder, "tank_level_out", ctrl, "tank_level_in")

        A ratio input imports on that same ``_level_in`` box -- a share is
        published by whoever publishes the level it comes from::

            controls_in=[{"name": "room", "kind": "ratio", "flows": ["H2"]}]
            controls_out=[{"name": "vent", "kind": "bool", "emit": {
                "op": "band", "input": "room", "direction": "above",
                "activate": 0.02, "release": 0.01}}]

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

    def add_control_out(self, name, kind=CTRL_OUT_BOOL, emit=None, **params):
        """Declare one output, and what it carries (R3, R42).

        Parameters
        ----------
        name : str
            Interface name, and the alias the output is exported under.
        kind : str
            :data:`CTRL_OUT_BOOL` -- a boolean signal on ``{name}_out``,
            consumed by a discrete control port -- or :data:`CTRL_OUT_VALUE`
            -- a number published on ``{name}_level_out`` and read by any
            observer, a second controller included (R4).
        emit : dict, optional
            What the output carries, as a composition of the closed operators
            :data:`CTRL_OPERATORS` (R42). A boolean output emits one of
            :data:`CTRL_BOOL_OPERATORS`, a value output one of
            :data:`CTRL_VALUE_OPERATORS`. ``None`` -- the default -- leaves the
            output's value written by hand, which is what a test drives::

                controls_out=[{
                    "name": "fill",
                    "kind": "bool",
                    "emit": {"op": "band", "input": "tank",
                             "direction": "below",
                             "activate": 3.0, "release": 7.0},
                }]

            **No Python callable is accepted here**, whatever continuity it
            attests: see :func:`build_ctrl_node`.
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
            taken, a message box another interface already claims, a grammar
            the closed list does not carry, an operator whose nature is not the
            output's, or an input the controller does not declare.
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

        # Everything the grammar can refuse is refused HERE, before a single
        # variable or message box exists: an output whose value is nonsense
        # leaves no half-built interface for the next walk of the system to
        # trip over, exactly as a refused component leaves no engine object.
        node = build_ctrl_node(
            f"Object {self.name()}: controller output {name!r} emit", emit
        )
        self.check_emit_nature(name, kind, node)
        self.check_emit_inputs(name, node)
        params = self.emit_gain_params(name, node, params)

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
        self.controls_emit[name] = node

        self.compile_emit(name)

        return interface

    # ------------------------------------------------------------------
    # The output grammar (R42)
    # ------------------------------------------------------------------

    def check_emit_nature(
        self, name: str, kind: str, node: typing.Optional[CtrlNode]
    ) -> None:
        """Refuse an operator whose nature is not the output's (R42, R3).

        The two natures are not interchangeable and nothing downstream would
        say so: a boolean output is a signal a control port imports, a value
        output a number an observer reads. An output carrying the wrong one
        would build, connect, and publish something its consumer cannot use.
        """
        if node is None:
            return

        wants_boolean = kind == CTRL_OUT_BOOL

        if node.IS_BOOLEAN == wants_boolean:
            return

        carries = "a condition" if node.IS_BOOLEAN else "a number"

        raise ValueError(
            f"Object {self.name()}: controller output {name!r} is declared "
            f"kind={kind!r} and emits {node.op!r}, which carries {carries}. A "
            f"{CTRL_OUT_BOOL!r} output carries a condition "
            f"({', '.join(CTRL_BOOL_OPERATORS)}) and a {CTRL_OUT_VALUE!r} "
            f"output a number ({', '.join(CTRL_VALUE_OPERATORS)})"
        )

    def check_emit_inputs(self, name: str, node: typing.Optional[CtrlNode]) -> None:
        """Refuse an output reading an input this controller does not declare.

        Refused at declaration, where the typo was made, and telling the
        modeller what the controller DOES declare. Left to the compiler it
        would be a bare ``KeyError`` out of a closure, naming neither the
        output nor the input.

        This is also what fixes the order of the two sections: an output says
        what it is made of by naming an input, so the inputs have to exist
        first -- which is the order :data:`muscadet.declare.DECLARATION_SECTIONS`
        already records.
        """
        if node is None:
            return

        missing = [read for read in node.inputs_read() if read not in self.controls_in]

        if not missing:
            return

        declared = ", ".join(self.controls_in) if self.controls_in else "none"
        plural = "s" if len(missing) > 1 else ""

        raise ValueError(
            f"Object {self.name()}: controller output {name!r} reads input"
            f"{plural} {', '.join(repr(read) for read in missing)}, which this "
            f"controller does not declare; it declares {declared}. Declare "
            "controls_in before the outputs that read them"
        )

    def emit_gain_params(self, name, node, params):
        """Fold a republication's gain into the publication's own gain.

        ONE number, ONE spelling. ``{name}_level_gain`` is the variable
        :class:`muscadet.MeasurementOut` multiplies everything it publishes by,
        and it is the endpoint a failure mode clamps; the gain the grammar
        declares is that variable's initial value and not a second factor
        beside it. Declaring both is refused rather than arbitrated, because
        the two would silently multiply.
        """
        if not isinstance(node, CtrlRepublish):
            return params

        if "gain_default" in params:
            raise ValueError(
                f"Object {self.name()}: controller output {name!r} declares a "
                "gain on its republication and a gain_default beside it. They "
                f"are the same number -- the initial value of "
                f"{name}_level_gain, which multiplies everything the output "
                "publishes -- so declare one of the two"
            )

        params = dict(params)
        params["gain_default"] = node.gain

        return params

    def compile_emit(self, name: str) -> typing.List[typing.Any]:
        """Compile one output's grammar to engine mechanisms (R42).

        A boolean output becomes a closure over its subtree, re-run on the
        notification of every automaton that subtree declared -- and on a start
        method, so the signal is seeded at t = 0 of every Monte Carlo sequence,
        which a non-reinitialised variable needs. A value output becomes a PDMP
        equation.

        **Not one sensitive method on a reading.** Every registration below is
        on an automaton, which is discrete and announces its state; the reading
        itself is taken live inside the closure, at the moment that
        notification fires. A sensitive method on the reading would never fire
        at all, because a quantity moving inside an integration step announces
        nothing -- the silent failure this whole grammar exists to make
        unreachable.

        A boolean output's blinding (R44) is registered on an automaton for the
        very same reason, and not on the availability variable it reads: the
        rule is one rule, whatever the nature of what is read.

        Returns
        -------
        list
            The automata the output's VALUE compiled to, empty for a
            republication and for an output emitting nothing. The blinding
            automaton is NOT among them -- it is in
            :attr:`blinding_automata` -- because it carries no threshold and
            dates no crossing.
        """
        node = self.controls_emit.get(name)
        automata: typing.List[typing.Any] = []

        if node is None:
            self.emit_automata[name] = automata

            return automata

        read = self.build_emit_reader(name, node, "", automata)
        self.emit_automata[name] = automata

        if not node.IS_BOOLEAN:
            self.register_republication(name, read)

            return automata

        interface = self.controls_out[name]
        method_name = f"emit_{self.name()}_{name}"

        def write_signal() -> None:
            interface.publish(read())

        # The blinding automaton is registered on the SAME method and in the
        # same breath as the grammar's own: what it announces is that this
        # output has to be written again, which is the one thing every
        # automaton here announces.
        blinding = self.add_blinding_automaton(name)

        for aut in automata if blinding is None else automata + [blinding]:
            aut._bkd.addSensitiveMethod(method_name, write_signal)

        # The seed, and it is load-bearing twice over. A signal variable is not
        # reinitialised between steps -- it is a state, not a pulse -- so
        # nothing else would give it the value its condition already has at
        # t = 0, and a montage starting past its own threshold would sit idle
        # until the reading came back and crossed it again.
        self.addStartMethod(method_name, write_signal)
        write_signal()

        return automata

    def build_emit_reader(self, out_name, node, path, automata):
        """Compile one node to a closure, appending the automata it needs.

        Depth first, so an operand's automata exist before the combination that
        reads it. ``path`` is what makes an automaton's name a function of its
        POSITION in the tree: two comparisons on the same input against
        different thresholds are two automata -- and two THRESHOLD VARIABLES
        (R44) -- and they have to be tellable apart in ``automata_d`` and among
        the component's variables.
        """
        if isinstance(node, CtrlCompare):
            channel = self.controls_in[node.input]
            compare_fun = comparator(node.operator)
            # Created BEFORE the automaton and handed to it: the condition the
            # automaton dates and the closure the output reads must consult the
            # same variable, or the two would answer differently the moment
            # anything moved the threshold.
            threshold = self.add_emit_param(
                out_name, path, CTRL_PARAM_THRESHOLD, node.threshold
            )

            automata.append(self.add_compare_automaton(out_name, node, path, threshold))

            def read_compare() -> bool:
                return bool(compare_fun(channel.get_reading(), threshold.value()))

            return read_compare

        if isinstance(node, CtrlBand):
            edges = (
                self.add_emit_param(out_name, path, CTRL_PARAM_ACTIVATE, node.activate),
                self.add_emit_param(out_name, path, CTRL_PARAM_RELEASE, node.release),
            )
            aut, activated = self.add_band_automaton(out_name, node, path, edges)
            automata.append(aut)

            def read_band() -> bool:
                return bool(activated.isActive())

            return read_band

        if isinstance(node, CtrlCombine):
            readers = [
                self.build_emit_reader(
                    out_name, operand, f"{path}_operand_{index}", automata
                )
                for index, operand in enumerate(node.operands)
            ]

            return combine_reader(node, readers)

        channel = self.controls_in[node.input]

        def read_value() -> float:
            return float(channel.get_reading())

        return read_value

    def emit_automaton_base(self, out_name: str, path: str, suffix: str) -> str:
        """The name every part of one node's automaton is derived from."""
        return f"{out_name}{path}_{suffix}"

    def add_emit_param(self, out_name, path, suffix, value):
        """Create the variable of ONE number the grammar declared (R44).

        Named ``{output}{path}_{suffix}`` -- the very naming
        :meth:`emit_automaton_base` gives the automata, so a threshold and the
        automaton dating its crossing are read off the same position in the
        tree and a model rebuilt names them the same way.

        NOT reinitialised, and this is the choice the whole endpoint rests on.
        The PyCATSHOO flag governs the reset at every STEP: a threshold reset
        at every step would be restored under the standing clamp of the mode
        holding it, and the watched automaton reading it would flap between the
        two values inside a single integration step. So it holds what was
        written, and the counterpart is the one
        :func:`muscadet.derating.release_deratings` already carries -- what
        does not fall back on its own has to be handed back. The engine still
        restores the declared init between Monte Carlo sequences, so a moved
        threshold leaks into no other run.

        Writing it BEFORE the run starts is how an instance is tuned away from
        its class: PyCATSHOO takes such a write as the variable's initial
        value, so the tuning survives every sequence.

        Parameters
        ----------
        out_name : str
            The output whose grammar declares this number.
        path : str
            The node's position in that output's tree.
        suffix : str
            One of :data:`CTRL_PARAMS`.
        value : float
            The declared value, which becomes the variable's initial one.

        Returns
        -------
        The PyCATSHOO variable, read live by the condition and by the closure.
        """
        basename = self.emit_automaton_base(out_name, path, suffix)
        py_type, pyc_type = get_pyc_type("float")

        var = self.addVariable(basename, pyc_type, py_type(value))
        var.setReinitialized(False)

        self.emit_params[basename] = var

        return var

    def add_forcing_params(self, out_name):
        """Create the forcing endpoints of one VALUE output (R44).

        ``{output}_forced`` and ``{output}_forced_value``: a flag a mode raises
        and the number the output then publishes instead of its reading. Two
        variables because a number has no rest value one flag could stand for,
        which is what a boolean output's declared default IS -- and why
        blinding one takes no second variable.

        Declared here rather than at :meth:`add_control_out` because only an
        output the equation refreshes can be forced: what forces a hand-written
        publication is the hand that writes it.

        Returns
        -------
        tuple
            The flag and the value, in that order.
        """
        bool_py, bool_pyc = get_pyc_type("bool")
        float_py, float_pyc = get_pyc_type("float")

        forced = self.addVariable(
            f"{out_name}_{CTRL_FORCED_SUFFIX}", bool_pyc, bool_py(False)
        )
        forced_value = self.addVariable(
            f"{out_name}_{CTRL_FORCED_VALUE_SUFFIX}", float_pyc, float_py(0.0)
        )

        # Not reinitialised, exactly as the thresholds are not: see
        # ``add_emit_param``. A mode that forces an output hands it back.
        forced.setReinitialized(False)
        forced_value.setReinitialized(False)

        self.emit_forced[out_name] = (forced, forced_value)

        return forced, forced_value

    def add_blinding_automaton(self, out_name):
        """The two-state automaton one BOOLEAN output's blinding compiles to (R44).

        Its state IS "this output is blinded", and what it exists for is the
        NOTIFICATION on both of its edges. The leaving one could be dispensed
        with -- a mode blinding an output usually moves something else too --
        but the returning one cannot: a signal variable is not reinitialised,
        so releasing the clamp on the availability puts nothing back, and the
        montage would stay idle with nothing wrong anywhere.

        Registered on an AUTOMATON rather than on the availability variable,
        which would work just as well on its own terms -- a boolean announces
        its change -- because a controller re-evaluates on automata and on
        nothing else. One rule, whatever the nature of what is read.

        Deliberately NOT watched, unlike every other automaton of this module.
        A watched transition is re-evaluated by the integration manager at
        every step so it can root-find the date a CONTINUOUS condition turns;
        this condition is a boolean written by a discrete event, and a discrete
        event is already an exact date.

        Returns
        -------
        The automaton, or ``None`` when the interface carries no availability
        endpoint -- which is every VALUE output, forced rather than blinded.
        """
        interface = self.controls_out[out_name]
        var = getattr(interface, "var_available", None)

        if var is None:
            return None

        base = f"{out_name}_{CTRL_BLINDING_SUFFIX}"
        aut, _ = self.add_emit_automaton(
            base,
            ("serving", "blinded"),
            (blinding_condition(var, True), blinding_condition(var, False)),
            watched=False,
        )

        self.blinding_automata[out_name] = aut

        return aut

    def add_emit_automaton(self, base, states, conditions, watched=True):
        """The watched two-state automaton one grammar node compiles to (R42).

        The very shape the library already uses to catch a crossing -- a
        capacity's empty/full bounds (R7), a rule set's mode automaton (R12),
        the threshold of a discrete production condition (R22), an aggregation
        kink (R41) -- and for the same reason: two INSTANTANEOUS transitions,
        both registered as watched, so the solver root-finds the date the
        condition turns and stops the integration there instead of picking the
        change up at the following step.

        Parameters
        ----------
        base : str
            What the automaton, its states and its transitions are named from.
        states : tuple
            The two state suffixes, resting one first.
        conditions : tuple
            The two transition conditions, out of the resting state first.
        watched : bool
            Whether the integration manager watches the two transitions. True
            for everything the output's VALUE compiles to, where the condition
            turns on a CONTINUOUS reading and a date has to be root-found.
            False for the blinding automaton of R44, whose condition is a
            boolean written by a discrete event: there is no crossing to find,
            and watching it would cost the integrator two evaluations per step
            for a date it already has exactly.

        Returns
        -------
        tuple
            The automaton, and the backend object of its far state -- what a
            band reads to know whether it is activated.
        """
        rest, far = (f"{base}_{suffix}" for suffix in states)
        trans_up = f"{base}_up"
        trans_down = f"{base}_down"

        aut = cod3s.PycAutomaton(
            name=f"{self.name()}_{base}",
            states=[rest, far],
            # Which side the condition designates cannot be known here: the
            # connections carry their publishers' defaults until the first
            # equation has run. The instantaneous transitions settle the
            # automaton at t = 0, which is one more reason they are watched
            # rather than merely conditioned.
            init_state=rest,
            transitions=[
                {
                    "name": trans_up,
                    "source": rest,
                    "target": far,
                    "is_interruptible": True,
                    # A fresh mapping per transition: cod3s rewrites the 'cls'
                    # entry in place while sanitizing it.
                    "occ_law": fresh_instant_occ_law(),
                },
                {
                    "name": trans_down,
                    "source": far,
                    "target": rest,
                    "is_interruptible": True,
                    "occ_law": fresh_instant_occ_law(),
                },
            ],
        )
        aut.update_bkd(self)

        for trans_name, condition in zip((trans_up, trans_down), conditions):
            aut.get_transition_by_name(trans_name)._bkd.setCondition(condition)

        if watched:
            self.system().pdmp_add_watched_automaton(aut)

        self.automata_d[aut.name] = aut

        return aut, aut.get_state_by_name(far)._bkd

    def add_compare_automaton(self, out_name, node, path, threshold):
        """The watched automaton one comparison compiles to (R42).

        Symmetric with :meth:`add_band_automaton`: a comparison is the band
        whose two edges coincide, read from one side and then the other.

        ``threshold`` is the VARIABLE :meth:`add_emit_param` created (R44), and
        it is handed in rather than read off the node: the automaton and the
        output's own closure have to compare against the same number at the
        same instant, and a mode may move it between two instants.
        """
        base = self.emit_automaton_base(out_name, path, CTRL_OP_COMPARE)
        channel = self.controls_in[node.input]
        compare_fun = comparator(node.operator)
        read = channel.get_reading

        aut, _ = self.add_emit_automaton(
            base,
            ("below", "above"),
            (
                band_edge_condition(read, compare_fun, threshold, True),
                band_edge_condition(read, compare_fun, threshold, False),
            ),
        )

        return aut

    def add_band_automaton(self, out_name, node, path, edges):
        """The watched automaton one band compiles to (R42).

        Two edges, two conditions, ONE automaton -- and the automaton's state
        IS the band's value. That is what makes the hysteresis a property of
        the compiled form rather than of a rule somebody has to remember: no
        reading of the quantity alone can say whether the band is holding,
        because the band's whole business is to answer differently at the same
        level depending on where it came from.

        ``edges`` is the ``(activate, release)`` pair of VARIABLES
        :meth:`add_emit_param` created (R44). The band's declaration-time check
        that the release edge sits on the far side of the activation one
        therefore covers the DECLARED values and not every value the two
        variables may later hold: a mode is free to invert a band, as it is
        free to declare any other unusable model.
        """
        base = self.emit_automaton_base(out_name, path, CTRL_OP_BAND)
        channel = self.controls_in[node.input]
        activate_op, release_op = node.edge_operators()
        activate, release = edges
        read = channel.get_reading

        return self.add_emit_automaton(
            base,
            ("released", "activated"),
            (
                band_edge_condition(read, comparator(activate_op), activate, True),
                band_edge_condition(read, comparator(release_op), release, True),
            ),
        )

    def register_republication(self, out_name: str, read: typing.Callable) -> None:
        """Declare a value output's republication to the solver (R42).

        An equation and not a sensitive method, for the reason that governs the
        whole module: the reading it republishes moves inside an integration
        step and announces nothing, so the only way to keep the publication
        current is to recompute it at every step.

        That is also why a VALUE output needs no automaton to be forced (R44),
        where a boolean one does: the equation runs at every step, so a
        forcing flag is read the instant it is raised and the instant it is
        released, in both directions and without anything else to declare.

        **The equation itself is registered at the pre-run step** (R45), by
        :meth:`register_control_equation`, and not here: its integer comes from
        a topological sort of the signal graph, which does not exist until every
        connection does. What happens here is everything that does not depend on
        the wiring -- the published variables the solver has to know about, the
        forcing endpoints, and the reader itself.

        Raises
        ------
        ValueError
            When the system's pre-run step has already run. That step is
            one-shot, so this output would otherwise get no equation at all.
        """
        system = self.system()

        # The pre-run step is one-shot, so an output declared after it would
        # never have its equation registered and would publish its declared
        # default for the whole run, in silence. Refused where the mistake was
        # made, which is the counterpart, for this band, of
        # ``System.check_model_unchanged_since_prerun``: a controller is not a
        # node of the continuous-flow graph, so that check cannot see it.
        if getattr(system, "prerun_done", False):
            raise ValueError(
                f"Object {self.name()}: controller output {out_name!r} "
                "republishes a reading, and the equation refreshing it is "
                "registered at the pre-run step (R45), which has already run "
                "on this system. That step derives the order of the "
                "controllers from the whole signal graph and runs once, so "
                "this output would never be refreshed at all: it would publish "
                "its declared default for the whole run, with nothing raised "
                "to say so. Declare every controller before the first "
                "simulate() / isimu_start()"
            )

        interface = self.controls_out[out_name]

        # Every published variable, constituents included: PyCATSHOO refuses
        # setValue on one its solver does not know about, and the refusal lands
        # at the first integration step rather than here.
        for var in interface.every_variable():
            system.pdmp_add_explicit_variable(var)

        self.add_forcing_params(out_name)

        self.emit_republications[out_name] = read

    def needs_control_equation(self) -> bool:
        """True when this controller has an equation left to register (R45).

        A controller carrying no republication has nothing for the solver to
        evaluate: its boolean outputs are written on the notification of the
        automata their grammar compiled to, which is not an equation. It is
        still a node of the signal graph, so it still constrains the order of
        the controllers around it -- it simply takes no integer.
        """
        return bool(self.emit_republications) and not self.emit_equation_registered

    def register_control_equation(self, system: typing.Any, order: int) -> None:
        """Register :meth:`compute_controls` at the derived order (R45).

        Called from :func:`muscadet.ordering.register_controller_equations` at
        the pre-run step, once every connection exists and the signal graph has
        been sorted. One registration covers every republication of the
        component, as ``compute_measurements`` does on an ``ObjFlow``.
        """
        system.pdmp_add_equation_method("compute_controls", self, order)
        self.emit_equation_registered = True

    def compute_controls(self) -> None:
        """PDMP equation: refresh every republication this controller carries.

        One equation for the whole component, as ``compute_measurements`` is on
        an ``ObjFlow``. The gain is applied by
        :meth:`muscadet.MeasurementOut.publish`, so what a mode clamps there
        reaches every reading this output carries and nothing else.

        **A forced output publishes its forced value, gain and all** (R44). One
        publication path and one gain: routing a forced value around the gain
        would make a mode that kills the gain of a forced instrument a silent
        no-op, and a silent no-op is the one outcome this module refuses
        everywhere else. With the gain at its declared 1, forcing publishes
        exactly the number the mode named.
        """
        for out_name, read in self.emit_republications.items():
            forced = self.emit_forced.get(out_name)

            if forced is not None and bool(forced[0].value()):
                self.controls_out[out_name].publish(float(forced[1].value()))
                continue

            self.controls_out[out_name].publish(read())

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
