"""The controller declaration grammar, without the controller.

Everything a controller declaration is CHECKED against: the closed lists of
aggregations, operators and logics, the emission nodes and the pure walk
that builds them (:func:`build_ctrl_node`), and the keys an input or an
output may carry. None of it drives the engine, which is why it lives
apart from :class:`muscadet.ObjCtrl`: a batch of declarations is validated
on a machine where no engine is installed.

:mod:`muscadet.obj_ctrl` re-exports every name defined here, so the
historical import path keeps working.
"""

import typing

import pydantic

from .combine import (
    COMBINE_MAX,
    COMBINE_MEAN,
    COMBINE_MEDIAN,
    COMBINE_MIN,
    COMBINE_POLICIES,
    COMBINE_SUM,
)
from .common import copy_declaration
from .rules import COMPARISON_OPERATORS, comparator

#: The sections a controller declares, in the order its constructor reads
#: them. :attr:`muscadet.ObjCtrl.DECLARATION_KEYS` is this tuple.
CONTROLLER_DECLARATION_KEYS = ("controls_in", "controls_out")

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

#: The nature of a SERVICE input: a boolean observed on ``{name}_in``, the box a
#: discrete input flow imports on, and folded into the variable
#: ``{name}_fed_in`` a mode condition reads. Not a measurement kind: it reads
#: no number, so it is not one of :data:`muscadet.MEASUREMENT_KINDS` and no
#: output grammar reads it. See :class:`CtrlServiceIn`.
CTRL_IN_SERVICE = "service"

#: Declaration keys a SERVICE input reads, beside its name. Nothing else: it
#: has one source at most, no constituent and no default -- wired to nothing,
#: it reads false.
CONTROL_IN_SERVICE_KEYS = ("kind",)

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
