"""Transformation rule declaration model.

A component declares its transformation rules with
:meth:`muscadet.ObjFlow.add_rules`. The rules do **not** live on an output
flow: a reaction with correlated outputs cannot be stated one output at a time,
and its limiting-reagent computation must be shared rather than duplicated.

A rule set is an **ordered** list of rules. Each rule carries

- a guard (``cond``), a conjunction of operands,
- a ``cons`` map of consumed input coefficients,
- a ``prod`` map of produced output coefficients.

A coefficient omitted from a map defaults to ``1``. A rule declared with no
guard (or an empty one) is the **default rule** and applies when no other rule
matches; a rule set carrying no default produces zero when no guard is true. At
most one rule may be a default -- a rule set with two of them is rejected at
declaration time.

Example
-------
>>> self.add_rules(                             # doctest: +SKIP
...     name="X",
...     rules=[
...         # F4 false : 3.F3 + 2.F2 -> 2.X
...         dict(cond=[{"name": "F4", "negate": True}],
...              cons={"F3": 3, "F2": 2}, prod={"X": 2}),
...         # F4 true and F1 < 10 : no production
...         dict(cond=[{"name": "F4"}, {"name": "F1", "op": "<", "value": 10}],
...              prod={"X": 0}),
...         # F4 true and F1 >= 10 : 3.F1 -> 0.5.X
...         dict(cond=[{"name": "F4"}, {"name": "F1", "op": ">=", "value": 10}],
...              cons={"F1": 3}, prod={"X": 0.5}),
...     ],
... )

Guard operands
--------------
An operand reuses the discrete production-condition vocabulary (``name``,
``negate``, ``port``) and extends it with ``op`` and ``value`` for a numeric
comparison, so one guard may mix discrete flow states with numeric comparisons
on continuous flow values. ``port`` disambiguates a name carried by BOTH an
input and an output flow of the component: absent, it keeps the historical
input-first resolution, and the resolved side is written back into ``port`` at
declaration time.

Guard strings
-------------
A guard may also be written as an expression string, e.g. ``"F4 and F1 >= 10"``,
which is normalised into structured operands at declaration time. **Only the
structured form is stored**, so a rule declared as a string and the same rule
declared structurally serialise identically. The grammar is deliberately
minimal -- a conjunction of operands, an optional negation, the six comparison
operators -- and is NOT a general expression language: no disjunction, no
parentheses, no arithmetic.

Evaluation
----------
Beyond the declaration model, this module carries the arithmetic of ONE rule:
:func:`rule_scale` computes the limiting-reagent scale, :func:`rule_consumption`
and :func:`rule_production` apply it to the two coefficient maps. The scale is
computed once per rule and shared by every ``prod`` entry, which is what keeps a
reaction's correlated outputs in their declared proportion (R15).

The arithmetic is deliberately kept free of any backend object: it takes an
``available`` mapping and returns rate mappings, so it is testable on its own and
the component only has to decide *what* counts as available (KTD13) and *where*
production is written. That split is what :meth:`muscadet.ObjFlow.compute_production`
implements.

Guard compilation (KD7, R12, R13, R14)
--------------------------------------
Rule *selection* goes through a **mode automaton**, :class:`RuleMode`: one state
per rule -- plus one meaning "no rule applies" when the set declares no default
-- and a transition between every pair of states, conditioned on the guards.

Two properties come out of making selection an automaton rather than an ``if``
inside the equation:

- the coefficient maps are **frozen within a mode**, which breaks the algebraic
  coupling between demand and production the two topological sweeps rest on;
- the transitions are registered as **watched**, so the solver stops the
  integration *at* a threshold crossing instead of noticing it at the following
  step. A guard like ``F1 >= 10`` would otherwise fire late, by an amount that
  depends on the integration step size (R12).

At most one rule is active at a time: two guards holding together is a model
error naming both rules (R13). It is checked at **every** evaluation -- in the
transition conditions and again in :meth:`RuleMode.active_rule` -- because it
depends on the current variable values and nothing about the declaration can
rule it out. A rule declared without a guard is the default and applies when no
other rule matches; a set with no default selects nothing, and a rule set that
selects nothing produces zero (R14).

:meth:`RuleSet.active_rule` is what reads the mode back, and
``ObjFlow.get_active_rule`` is where a component reaches it.
"""

import math
import operator
import re
import typing

import pydantic

import cod3s

from .common import entity_label, fresh_instant_occ_law

#: The six comparison operators a numeric guard operand may carry.
COMPARISON_OPERATORS = ("<", "<=", ">", ">=", "==", "!=")

#: The two sides a guard operand may be resolved against.
PORTS = ("in", "out")

#: Scale of a rule nothing constrains: an empty ``cons`` map, or one whose
#: inputs are all served by an unbounded counterparty (a capacity holding stock
#: serves without limit, R7). Such a rule produces exactly its declared ``prod``
#: quantities -- what finally bounds it is the demand of the consumers, which
#: the demand sweep computes.
UNCONSTRAINED_SCALE = 1.0

#: Key of the mode state a rule set sits in while no guard holds and it declares
#: no default rule. Production is zero there (R14).
NO_RULE = "none"

#: The comparison operators of a numeric guard operand, as callables.
_COMPARATORS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}


# One operand of a guard string: an optional negation ("not x" / "!x"), a flow
# name, and an optional comparison against a numeric literal. Anything else --
# parentheses, arithmetic, a disjunction -- is rejected on purpose.
_OPERAND_RE = re.compile(
    r"""^
        (?:(?P<neg>not\s+|!\s*))?
        (?P<name>[A-Za-z_]\w*)
        (?:
            \s*(?P<op><=|>=|==|!=|<|>)\s*
            (?P<value>[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
        )?
        $""",
    re.VERBOSE,
)


def format_number(value) -> str:
    """Render a coefficient / threshold without a trailing ``.0``."""
    return f"{float(value):g}"


def parse_guard_expression(expression: str) -> typing.List[dict]:
    """Normalise a guard expression string into structured operands.

    The grammar is a conjunction of operands separated by ``and``. An operand is
    ``NAME``, ``not NAME`` (or ``!NAME``) or ``NAME <op> NUMBER`` with ``<op>``
    one of ``< <= > >= == !=``. Returns the list of operand mappings, ready to be
    validated into :class:`RuleOperand` objects.

    Raises
    ------
    ValueError
        If the expression uses anything outside that grammar.
    """
    if not isinstance(expression, str):
        raise ValueError(f"Guard expression must be a string : {expression!r}")

    text = expression.strip()
    if not text:
        return []

    if re.search(r"\bor\b", text, flags=re.IGNORECASE):
        raise ValueError(
            f"Bad guard expression {expression!r}: only conjunctions are "
            "supported (operands joined by 'and'); express a disjunction as "
            "several rules"
        )
    if "(" in text or ")" in text:
        raise ValueError(
            f"Bad guard expression {expression!r}: parentheses are not supported, "
            "the guard grammar is a flat conjunction of operands"
        )

    operands = []
    for chunk in re.split(r"\s+and\s+", text, flags=re.IGNORECASE):
        chunk = chunk.strip()
        match = _OPERAND_RE.match(chunk)
        if match is None:
            raise ValueError(
                f"Bad guard expression {expression!r}: cannot parse operand "
                f"{chunk!r} (expected 'NAME', 'not NAME' or 'NAME <op> NUMBER' "
                f"with <op> in {', '.join(COMPARISON_OPERATORS)})"
            )

        negate = match.group("neg") is not None
        op = match.group("op")
        if negate and op is not None:
            raise ValueError(
                f"Bad guard expression {expression!r}: operand {chunk!r} negates "
                "a comparison; use the opposite comparison operator instead"
            )

        operand = {"name": match.group("name")}
        if negate:
            operand["negate"] = True
        if op is not None:
            operand["op"] = op
            operand["value"] = float(match.group("value"))

        operands.append(operand)

    return operands


def normalize_coefficients(value, what: str) -> typing.Dict[str, float]:
    """Normalise a ``cons`` / ``prod`` short form into a coefficient map.

    Accepted forms, all meaning the same thing when a coefficient is omitted
    (it then defaults to ``1``):

    - ``None`` / ``{}`` -> ``{}``
    - ``"F1"`` -> ``{"F1": 1.0}``
    - ``["F1", "F2"]`` -> ``{"F1": 1.0, "F2": 1.0}``
    - ``{"F1": 3, "F2": None}`` -> ``{"F1": 3.0, "F2": 1.0}``
    """
    if value is None:
        return {}
    if isinstance(value, str):
        return {value: 1.0}
    if isinstance(value, (list, set, tuple)):
        coefficients = {}
        for item in value:
            if not isinstance(item, str):
                raise ValueError(
                    f"Bad format for rule '{what}' map: a list entry must be a "
                    f"flow name, got {item!r}"
                )
            coefficients[item] = 1.0
        return coefficients
    if isinstance(value, dict):
        coefficients = {}
        for name, coefficient in value.items():
            if not isinstance(name, str):
                raise ValueError(
                    f"Bad format for rule '{what}' map: a key must be a flow "
                    f"name, got {name!r}"
                )
            # An omitted coefficient (None) defaults to 1.
            if coefficient is None:
                coefficients[name] = 1.0
            else:
                try:
                    coefficients[name] = float(coefficient)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Bad format for rule '{what}' map: coefficient of "
                        f"{name!r} must be a number, got {coefficient!r}"
                    )
        return coefficients

    raise ValueError(f"Bad format for rule '{what}' map : {value!r}")


# ----------------------------------------------------------------------
# Operand shape: one implementation, both directions (R-5)
# ----------------------------------------------------------------------
#
# A rule guard operand and a discrete production-condition operand share one
# comparison vocabulary, so they share one implementation of the rules that
# vocabulary obeys. The two sites word their messages differently -- a guard
# operand names itself, a production condition names its component too -- so
# each check takes the LABEL to prefix its message with, and the pairing check
# takes the phrase naming what a comparison is on that side. Nothing else about
# the two messages differs, and a rule tightened here tightens both directions
# at once, which is the point: they used to be two implementations that could
# silently diverge.

#: How the pairing message names a comparison operand on the rule-guard side.
GUARD_COMPARISON_KIND = "a numeric operand"


def check_operand_operator(label: str, op) -> None:
    """``op``, when given, is one of the six known comparison operators."""
    if op is not None and op not in COMPARISON_OPERATORS:
        raise ValueError(
            f"{label}: 'op' must be one of "
            f"{', '.join(COMPARISON_OPERATORS)}, got {op!r}"
        )


def check_operand_pairing(
    label: str, op, value, comparison_kind: str = GUARD_COMPARISON_KIND
) -> None:
    """``op`` and ``value`` are given together, or neither is.

    Half a comparison leaves the threshold -- or the operator -- undefined, and
    silently reading the operand as a boolean would hide the typo.
    """
    if (op is None) != (value is None):
        raise ValueError(
            f"{label}: 'op' and 'value' must be given together "
            f"({comparison_kind}) or both omitted (a boolean operand)"
        )


def check_operand_negation(label: str, op, negate) -> None:
    """``negate`` is not combined with a comparison.

    ``not (x >= 10)`` is ``x < 10``: the opposite operator says it, and allowing
    both spellings would give one condition two serialisations.
    """
    if op is not None and negate:
        raise ValueError(
            f"{label}: 'negate' cannot be combined with a comparison; use the "
            "opposite comparison operator instead"
        )


def validate_operand_shape(
    label: str, op, value, negate, comparison_kind: str = GUARD_COMPARISON_KIND
) -> None:
    """The three shape rules of a comparison operand, in declaration order."""
    check_operand_pairing(label, op, value, comparison_kind)
    check_operand_operator(label, op)
    check_operand_negation(label, op, negate)


class RuleOperand(cod3s.ObjCOD3S):
    """One operand of a rule guard.

    Either a boolean operand -- the ``var_fed`` state of a flow, optionally
    negated -- or a numeric comparison of a flow value against a threshold.
    """

    name: str = pydantic.Field(..., description="Name of the flow the operand reads")

    negate: bool = pydantic.Field(
        False,
        description=(
            "Boolean operand only: the operand is true when the flow is NOT fed"
        ),
    )

    port: typing.Optional[str] = pydantic.Field(
        None,
        description=(
            "Side the name resolves against, 'in' or 'out'. Absent at "
            "declaration means input-first resolution; the resolved side is "
            "written back here by ObjFlow.add_rules."
        ),
    )

    op: typing.Optional[str] = pydantic.Field(
        None,
        description=(
            "Comparison operator of a numeric operand, one of "
            "'<', '<=', '>', '>=', '==', '!='. None for a boolean operand."
        ),
    )

    value: typing.Optional[float] = pydantic.Field(
        None, description="Threshold a numeric operand compares the flow value to"
    )

    flow: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description=(
            "Flow object the operand resolves to, bound at declaration time by "
            "ObjFlow.add_rules. Never serialised: 'name' and 'port' are enough "
            "to resolve it again."
        ),
    )

    @pydantic.field_validator("op")
    @classmethod
    def check_op(cls, value, info):
        check_operand_operator(entity_label("Guard operand", info, quote=True), value)
        return value

    @pydantic.field_validator("port")
    @classmethod
    def check_port(cls, value, info):
        # NOT shared with the production-condition side, unlike the three shape
        # rules below: the two messages differ structurally there (this one
        # separates the label with a colon, the other does not), and unifying
        # them would change a wording rather than remove a duplication.
        if value is not None and value not in PORTS:
            raise ValueError(
                f"{entity_label('Guard operand', info, quote=True)}: 'port' "
                f"must be 'in' or 'out', got {value!r}"
            )
        return value

    @pydantic.model_validator(mode="after")
    def check_operand_shape(self):
        # Same three rules a discrete production-condition operand obeys, from
        # the same implementation (R-5). ``op`` has already passed
        # ``check_op`` by the time a model validator runs, so re-checking it
        # here costs nothing and keeps ONE entry point for both directions.
        validate_operand_shape(
            f"Guard operand {self.name!r}", self.op, self.value, self.negate
        )
        return self

    @property
    def is_comparison(self) -> bool:
        """True when the operand compares a value, False when it reads a state."""
        return self.op is not None

    def to_expression(self) -> str:
        """Render the operand back into its guard-string form."""
        if self.is_comparison:
            return f"{self.name} {self.op} {format_number(self.value)}"
        return f"not {self.name}" if self.negate else self.name

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.to_expression()})"

    def __str__(self) -> str:
        return self.to_expression()


class Rule(cod3s.ObjCOD3S):
    """One transformation rule: a guard, a ``cons`` map and a ``prod`` map."""

    name: typing.Optional[str] = pydantic.Field(
        None,
        description=(
            "Optional rule name, used to designate the rule in model error "
            "messages. Falls back to its position in the rule set."
        ),
    )

    cond: typing.List[RuleOperand] = pydantic.Field(
        default_factory=list,
        description=(
            "Guard: a conjunction (AND) of operands. Accepts an expression "
            "string, which is normalised into operands at declaration time. An "
            "empty guard makes the rule the default rule of its set."
        ),
    )

    cons: typing.Dict[str, float] = pydantic.Field(
        default_factory=dict,
        description=(
            "Consumed input coefficients, {flow_name: coefficient}. An omitted "
            "coefficient defaults to 1."
        ),
    )

    prod: typing.Dict[str, float] = pydantic.Field(
        default_factory=dict,
        description=(
            "Produced output coefficients, {flow_name: coefficient}. An omitted "
            "coefficient defaults to 1."
        ),
    )

    @pydantic.field_validator("cond", mode="before")
    @classmethod
    def normalize_cond(cls, value):
        # Normalise every accepted short form to the canonical list of operand
        # mappings, exactly as ObjFlow.postprocess_flow_specs does for the
        # discrete production condition.
        if value is None:
            return []
        if isinstance(value, str):
            return parse_guard_expression(value)
        if isinstance(value, (dict, RuleOperand)):
            return [value]
        if isinstance(value, (list, set, tuple)):
            operands = []
            for item in value:
                if isinstance(item, str):
                    # A string entry may itself be a conjunction, so it extends
                    # the operand list rather than adding a single operand.
                    operands.extend(parse_guard_expression(item))
                elif isinstance(item, (dict, RuleOperand)):
                    operands.append(item)
                else:
                    raise ValueError(f"Bad format for rule guard operand : {item!r}")
            return operands

        raise ValueError(f"Bad format for rule guard : {value!r}")

    @pydantic.field_validator("cons", mode="before")
    @classmethod
    def normalize_cons(cls, value):
        return normalize_coefficients(value, "cons")

    @pydantic.field_validator("prod", mode="before")
    @classmethod
    def normalize_prod(cls, value):
        return normalize_coefficients(value, "prod")

    @property
    def is_default(self) -> bool:
        """True when the rule carries no guard: it is the default of its set."""
        return len(self.cond) == 0

    def guard_expression(self) -> str:
        """Render the guard back into its expression-string form."""
        return " and ".join(operand.to_expression() for operand in self.cond)

    def to_expression(self) -> str:
        """Render the whole rule, guard included, for messages and debugging."""

        def _side(coefficients):
            if not coefficients:
                return "0"
            return " + ".join(
                f"{format_number(coefficient)}.{name}"
                for name, coefficient in coefficients.items()
            )

        body = f"{_side(self.cons)} -> {_side(self.prod)}"
        guard = self.guard_expression()
        return f"{guard} : {body}" if guard else f"default : {body}"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.to_expression()})"

    def __str__(self) -> str:
        return self.to_expression()


def comparator(op):
    """The callable implementing comparison operator ``op``.

    Exposed so a discrete production condition compares a continuous quantity
    (R22) through the very operators a rule guard uses (R21): one comparison
    vocabulary for both directions of the interoperation, one implementation.
    """
    try:
        return _COMPARATORS[op]
    except KeyError:
        raise ValueError(
            f"Comparison operator must be one of "
            f"{', '.join(COMPARISON_OPERATORS)}, got {op!r}"
        )


def operand_value(operand: RuleOperand):
    """The value ``operand`` currently reads on the flow it was bound to.

    Delegates to :meth:`muscadet.flow.FlowModel.live_value`, so a guard and a
    discrete production condition read a given flow exactly the same way. A
    continuous input is read there from its **reference** rather than from the
    ``var_fed`` mirror, which lags one integration step behind (R12); every
    other flow carries its value in ``var_fed``, a discrete one as a boolean
    (R21).
    """
    flow = operand.flow

    if flow is None:
        raise ValueError(
            f"Guard operand {operand.to_expression()!r} is not bound to a flow: "
            "ObjFlow.add_rules resolves every operand at declaration time"
        )

    return flow.live_value()


def operand_holds(operand: RuleOperand) -> bool:
    """True when ``operand`` is currently satisfied."""
    value = operand_value(operand)

    if operand.is_comparison:
        return bool(_COMPARATORS[operand.op](float(value), float(operand.value)))

    truth = bool(value)
    return (not truth) if operand.negate else truth


def guard_holds(rule: Rule) -> bool:
    """True when every operand of ``rule``'s guard holds.

    A guard is a conjunction, so an EMPTY one holds vacuously -- which is why
    only guarded rules are ever passed here: the unguarded rule of a set is its
    default, and a default applies by not being matched, not by being true.
    """
    return all(operand_holds(operand) for operand in rule.cond)


def rule_scale(rule: Rule, available: typing.Mapping[str, float]) -> float:
    """The scale at which ``rule`` runs, set by its scarcest input (R15).

    ``available`` maps each consumed flow name to the quantity the component
    may actually draw from it this step -- which is *not* always what the input
    flow delivers: with a capacity interposed, it is what that capacity can
    serve (KTD13).

    The scale is the smallest ``available / coefficient`` ratio over the
    ``cons`` map: the limiting reagent. It is computed ONCE per rule, so every
    ``prod`` entry is produced at that same scale and correlated outputs keep
    their declared proportion.

    An input delivered in excess of its coefficient therefore raises nothing:
    the scale is a minimum, so a surplus on one input cannot compensate a
    shortage on another. An input delivered at 0 -- or absent from
    ``available`` altogether -- yields a scale of 0, hence no production.

    Returns :data:`UNCONSTRAINED_SCALE` when nothing constrains the rule: an
    empty ``cons`` map, or one whose inputs are all served without limit.

    Parameters
    ----------
    rule : Rule
        The rule being evaluated.
    available : mapping
        ``{flow name: quantity available}``. A missing name counts as 0.

    Returns
    -------
    float
        The scale, never negative.
    """
    scale = math.inf

    for name, coefficient in rule.cons.items():
        if coefficient <= 0:
            # A rule consuming nothing of a flow is not limited by it.
            continue
        scale = min(scale, float(available.get(name, 0.0)) / coefficient)

    if math.isinf(scale):
        return UNCONSTRAINED_SCALE

    # A negative availability is not a production in reverse.
    return max(scale, 0.0)


def rule_consumption(rule: Rule, scale: float) -> typing.Dict[str, float]:
    """What ``rule`` draws from each of its inputs at ``scale``."""
    return {name: coefficient * scale for name, coefficient in rule.cons.items()}


def rule_production(rule: Rule, scale: float) -> typing.Dict[str, float]:
    """What ``rule`` produces on each of its outputs at ``scale``."""
    return {name: coefficient * scale for name, coefficient in rule.prod.items()}


class RuleSet(cod3s.ObjCOD3S):
    """An ordered set of transformation rules declared on a component."""

    name: str = pydantic.Field(..., description="Rule set name")

    rules: typing.List[Rule] = pydantic.Field(
        default_factory=list,
        description="Ordered rules. At most one of them may be the default rule.",
    )

    mode: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description=(
            "The RuleMode compiled from this set's guards, bound by "
            "ObjFlow.compile_rule_mode. Never serialised: the rules above are "
            "enough to compile it again. None while the set carries no guard, "
            "and None on a set built outside a component."
        ),
    )

    #: Memoised :attr:`consumed_flows` / :attr:`produced_flows`. Private
    #: attributes rather than fields: they are derived from ``rules`` and must
    #: never reach a dump.
    _consumed_flows: typing.Optional[typing.List[str]] = pydantic.PrivateAttr(
        default=None
    )
    _produced_flows: typing.Optional[typing.List[str]] = pydantic.PrivateAttr(
        default=None
    )

    @pydantic.field_validator("rules", mode="before")
    @classmethod
    def normalize_rules(cls, value):
        if value is None:
            return []
        if isinstance(value, (dict, Rule)):
            return [value]
        return value

    @pydantic.model_validator(mode="after")
    def check_single_default(self):
        # KD8: at most one rule is active at a time, and an unguarded rule
        # serves as the default -- two defaults leave the active rule undefined.
        defaults = [index for index, rule in enumerate(self.rules) if rule.is_default]
        if len(defaults) > 1:
            labels = ", ".join(self.rule_label(index) for index in defaults)
            raise ValueError(
                f"Rule set {self.name}: at most one rule may be declared without "
                f"a guard (the default rule), got {len(defaults)} : {labels}"
            )
        return self

    def rule_label(self, index: int) -> str:
        """Designate a rule by its name, falling back to its position."""
        rule = self.rules[index]
        return rule.name if rule.name else f"rule #{index}"

    @property
    def default_rule(self) -> typing.Optional[Rule]:
        """The unguarded rule of the set, or None when it carries none."""
        for rule in self.rules:
            if rule.is_default:
                return rule
        return None

    @property
    def guarded_rules(self) -> typing.List[Rule]:
        """The guarded rules, in declaration order."""
        return [rule for rule in self.rules if not rule.is_default]

    def _footprint(self, side: str) -> typing.List[str]:
        """Deduplicated flow names of one coefficient map, in declaration order."""
        names = []
        for rule in self.rules:
            for name in getattr(rule, side):
                if name not in names:
                    names.append(name)
        return names

    @property
    def consumed_flows(self) -> typing.List[str]:
        """Every input flow name ANY rule of the set consumes, in order.

        The whole set's footprint, not the active rule's: a flow the previously
        active mode drew on must be brought back to zero when another mode takes
        over, or it would keep the rate that mode left on it.

        Memoised: a rule's ``cons`` map is settled at declaration -- resolution
        only binds guard operands to flow objects, and nothing writes a
        coefficient map afterwards -- while both sweeps read this once per rule
        set per equation evaluation.
        """
        if self._consumed_flows is None:
            self._consumed_flows = self._footprint("cons")
        return self._consumed_flows

    @property
    def produced_flows(self) -> typing.List[str]:
        """Every output flow name ANY rule of the set produces, in order.

        Memoised for the same reason as :attr:`consumed_flows`.
        """
        if self._produced_flows is None:
            self._produced_flows = self._footprint("prod")
        return self._produced_flows

    def active_rule(self) -> typing.Optional[Rule]:
        """The rule whose production is evaluated, or None to produce zero.

        With guards, the compiled mode automaton is what selects it: its ACTIVE
        STATE designates the rule, and not a live evaluation of the guards
        (KD7). Freezing the coefficients within a mode is the state break the
        two-sweep ordering rests on, and it is also what a watched transition
        buys -- the mode changes at the crossing, not at the next step.

        Without a compiled mode -- a set carrying no guard at all, or one built
        outside a component -- the default rule is what applies. None means
        "produce nothing for this rule set" (R14).

        ``ObjFlow.get_active_rule`` is where a component reaches this.

        Raises
        ------
        ValueError
            When two guards of the set hold at once (R13).
        """
        if self.mode is not None:
            return self.mode.active_rule()

        return self.default_rule

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name}, {len(self.rules)} rules)"

    def __str__(self) -> str:
        lines = [f"{self.__class__.__name__} {self.name}"]
        lines += [
            f"  [{self.rule_label(index)}] {rule.to_expression()}"
            for index, rule in enumerate(self.rules)
        ]
        return "\n".join(lines)


class RuleMode:
    """The mode automaton a rule set's guards compile to (KD7, R12).

    One state per rule, plus a :data:`NO_RULE` state when the set declares no
    default, and a transition between every pair of states. Each transition
    carries an instantaneous law and a condition that holds exactly when the
    guards select its target, and every one of them is registered as a WATCHED
    transition of the PDMP manager.

    Why an automaton and not an ``if`` inside the production equation
    ----------------------------------------------------------------
    Two things fall out of it, and neither is available to a guard evaluated
    inline:

    - the active state **freezes** the coefficient maps within a mode, which
      breaks the algebraic coupling between demand and production the two
      topological sweeps depend on;
    - a watched transition makes the solver stop the integration **at** the
      crossing. Inline, a guard like ``F1 >= 10`` fires at the following step,
      late by an amount that depends on the step size -- unacceptable in a
      reliability library.

    Deliberately NOT a pydantic model: it holds nothing but backend handles and
    a back-reference to the rule set that owns it, and that set already carries
    everything needed to compile it again (which is why ``RuleSet.mode``
    excludes it from serialisation).
    """

    def __init__(self, rule_set: RuleSet, comp):
        self.rule_set = rule_set
        self.comp = comp

        #: State keys, in state order: a rule index per rule, then NO_RULE when
        #: the set declares no default rule.
        self.keys: typing.List[typing.Any] = []

        #: The compiled ``cod3s.PycAutomaton``, None until :meth:`build` runs.
        self.automaton = None

        #: Backend state per key, read back by :meth:`active_key`.
        self.state_bkd: typing.Dict[typing.Any, typing.Any] = {}

    # ------------------------------------------------------------------
    # Naming
    # ------------------------------------------------------------------

    @property
    def default_key(self) -> typing.Optional[int]:
        """Index of the set's default rule, or None when it declares none."""
        for index, rule in enumerate(self.rule_set.rules):
            if rule.is_default:
                return index
        return None

    @property
    def init_key(self):
        """The state the automaton starts in.

        The default rule when the set declares one, :data:`NO_RULE` otherwise.
        It cannot be derived from the guards: a mode is compiled at DECLARATION
        time, before the flows carry any variable at all. The instantaneous
        transitions settle the automaton on the state the guards select at
        ``t = 0``, which is why they must be watched rather than merely
        conditioned.
        """
        default = self.default_key
        return NO_RULE if default is None else default

    def state_name(self, key) -> str:
        """Name of the automaton state designated by ``key``."""
        suffix = NO_RULE if key == NO_RULE else f"rule_{key}"
        return f"{self.rule_set.name}_{suffix}"

    def transition_name(self, source, target) -> str:
        """Name of the transition from state ``source`` to state ``target``."""
        return f"{self.rule_set.name}_{source}_to_{target}"

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def selected_key(self):
        """The state the guards designate right now, checking R13 on the way.

        Evaluated live -- this is NOT what the production equation reads, it is
        what drives the transitions towards the state it does read.

        Raises
        ------
        ValueError
            When two guards hold at once. A model in which two production
            regimes hold together is wrong, and naming both rules beats
            electing one silently (KD8).
        """
        matches = [
            index
            for index, rule in enumerate(self.rule_set.rules)
            if not rule.is_default and guard_holds(rule)
        ]

        if len(matches) > 1:
            labels = ", ".join(self.rule_set.rule_label(index) for index in matches)
            guards = " / ".join(
                self.rule_set.rules[index].guard_expression() for index in matches
            )
            raise ValueError(
                f"Object {self.comp.name()}: rule set {self.rule_set.name}: "
                f"{len(matches)} guards hold at once -- {labels} ({guards}): at "
                "most one rule may be active at a time, so make the guards "
                "mutually exclusive"
            )

        if matches:
            return matches[0]

        # No guard holds: the default rule applies, and a set declaring none
        # selects nothing and produces zero (R14).
        return self.init_key

    def active_key(self):
        """The key of the automaton's currently active state."""
        for key, state in self.state_bkd.items():
            if state.isActive():
                return key

        # Before the automaton is built there is no state to read back.
        return self.init_key

    def active_rule(self) -> typing.Optional[Rule]:
        """The rule the automaton's active state designates, or None.

        The exclusivity of the guards (R13) is re-checked here, at EVERY
        evaluation: it is a property of the current variable values, so nothing
        about the declaration can rule it out. The transition conditions check
        it too -- this second check is what makes the error surface from the
        production equation, whichever runs first.
        """
        self.selected_key()

        key = self.active_key()
        if key == NO_RULE:
            return None

        return self.rule_set.rules[key]

    # ------------------------------------------------------------------
    # Backend construction
    # ------------------------------------------------------------------

    def _condition(self, target):
        """The condition of every transition landing on state ``target``."""

        def condition():
            return self.selected_key() == target

        return condition

    def build(self):
        """Build the automaton on the owning component."""
        rule_set = self.rule_set
        comp = self.comp

        self.keys = list(range(len(rule_set.rules)))
        if self.default_key is None:
            self.keys.append(NO_RULE)

        transitions = []
        conditions = {}
        for source in self.keys:
            for target in self.keys:
                if source == target:
                    continue
                name = self.transition_name(source, target)
                transitions.append(
                    {
                        "name": name,
                        "source": self.state_name(source),
                        "target": self.state_name(target),
                        "is_interruptible": True,
                        "occ_law": fresh_instant_occ_law(),
                    }
                )
                conditions[name] = self._condition(target)

        aut = cod3s.PycAutomaton(
            name=f"{comp.name()}_{rule_set.name}_mode",
            states=[self.state_name(key) for key in self.keys],
            init_state=self.state_name(self.init_key),
            transitions=transitions,
        )
        aut.update_bkd(comp)

        for name, condition in conditions.items():
            aut.get_transition_by_name(name)._bkd.setCondition(condition)

        self.automaton = aut
        self.state_bkd = {
            key: aut.get_state_by_name(self.state_name(key))._bkd for key in self.keys
        }

        comp.automata_d[aut.name] = aut

        return aut

    def register(self, system):
        """Register every mode transition as a watched transition (R12)."""
        system.pdmp_add_watched_automaton(self.automaton)

    # ------------------------------------------------------------------
    # Representation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        key = self.active_key()
        state = NO_RULE if key == NO_RULE else self.rule_set.rule_label(key)
        return f"{self.__class__.__name__}({self.rule_set.name} -> {state})"

    def __str__(self) -> str:
        return self.__repr__()
