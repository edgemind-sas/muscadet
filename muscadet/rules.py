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

Scope
-----
This module is **declaration and validation only**. Rule evaluation and the
compilation of guards into mode automata live in later units.
"""

import re
import typing

import pydantic

import cod3s

#: The six comparison operators a numeric guard operand may carry.
COMPARISON_OPERATORS = ("<", "<=", ">", ">=", "==", "!=")

#: The two sides a guard operand may be resolved against.
PORTS = ("in", "out")

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
    def check_op(cls, value):
        if value is not None and value not in COMPARISON_OPERATORS:
            raise ValueError(
                f"Guard operand 'op' must be one of "
                f"{', '.join(COMPARISON_OPERATORS)}, got {value!r}"
            )
        return value

    @pydantic.field_validator("port")
    @classmethod
    def check_port(cls, value):
        if value is not None and value not in PORTS:
            raise ValueError(
                f"Guard operand 'port' must be 'in' or 'out', got {value!r}"
            )
        return value

    @pydantic.model_validator(mode="after")
    def check_operand_shape(self):
        if (self.op is None) != (self.value is None):
            raise ValueError(
                f"Guard operand {self.name!r}: 'op' and 'value' must be given "
                "together (a numeric operand) or both omitted (a boolean operand)"
            )
        if self.op is not None and self.negate:
            raise ValueError(
                f"Guard operand {self.name!r}: 'negate' cannot be combined with a "
                "comparison; use the opposite comparison operator instead"
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


class RuleSet(cod3s.ObjCOD3S):
    """An ordered set of transformation rules declared on a component."""

    name: str = pydantic.Field(..., description="Rule set name")

    rules: typing.List[Rule] = pydantic.Field(
        default_factory=list,
        description="Ordered rules. At most one of them may be the default rule.",
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

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name}, {len(self.rules)} rules)"

    def __str__(self) -> str:
        lines = [f"{self.__class__.__name__} {self.name}"]
        lines += [
            f"  [{self.rule_label(index)}] {rule.to_expression()}"
            for index, rule in enumerate(self.rules)
        ]
        return "\n".join(lines)
