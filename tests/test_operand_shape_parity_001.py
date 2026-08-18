"""Parity of the two operand-shape validators (R-5).

A comparison operand is declared in two directions of the discrete/continuous
interoperation:

- on a **rule guard**, validated by :class:`muscadet.RuleOperand`;
- on a **discrete production condition**, validated by
  ``ObjFlow.postprocess_flow_specs``.

They share one comparison vocabulary, and since R-5 they share one
implementation of the three shape rules that vocabulary obeys
(``muscadet.rules.validate_operand_shape``):

1. ``op`` and ``value`` are given together, or neither is;
2. ``op``, when given, is one of the six known comparison operators;
3. ``negate`` is not combined with a comparison.

What this file pins is the property the consolidation exists for: **the two
directions accept and refuse exactly the same operand shapes**. It also pins
the two error WORDINGS, which deliberately differ -- a guard operand names
itself, a production condition names its component too, and each describes a
comparison in its own terms. A future change tightening one direction now
tightens both; a change that split them again would fail here.

No system is simulated: every assertion is about what a DECLARATION does.
"""

import cod3s
import pytest

import muscadet
from muscadet import rules as muscadet_rules

# ---------------------------------------------------------------------------
# The shapes, and what each shape rule says about them
# ---------------------------------------------------------------------------

#: ``(shape, marker)`` pairs both directions must REFUSE, with a fragment that
#: identifies which of the three rules did the refusing.
BAD_SHAPES = [
    # Rule 1: half a comparison, either half missing.
    ({"op": ">="}, "'op' and 'value' must be given together"),
    ({"op": "<", "value": None}, "'op' and 'value' must be given together"),
    ({"value": 10}, "'op' and 'value' must be given together"),
    ({"value": 0}, "'op' and 'value' must be given together"),
    # Rule 2: an operator outside the six.
    ({"op": "~=", "value": 1}, "'op' must be one of"),
    ({"op": "=<", "value": 1}, "'op' must be one of"),
    ({"op": "=", "value": 1}, "'op' must be one of"),
    ({"op": "in", "value": 1}, "'op' must be one of"),
    # Rule 3: a negated comparison.
    (
        {"negate": True, "op": ">=", "value": 5},
        "use the opposite comparison operator instead",
    ),
    (
        {"negate": True, "op": "!=", "value": 0},
        "use the opposite comparison operator instead",
    ),
]

#: Shapes both directions must ACCEPT: the six operators, the boolean forms,
#: and the negated boolean the third rule leaves alone.
GOOD_SHAPES = [
    {"op": "<", "value": 1},
    {"op": "<=", "value": 1},
    {"op": ">", "value": 1},
    {"op": ">=", "value": 1},
    {"op": "==", "value": 1},
    {"op": "!=", "value": 1},
    # A threshold of zero, which must not be read as "no threshold".
    {"op": ">=", "value": 0},
    # The boolean forms the comparison vocabulary extends.
    {},
    {"negate": True},
]


class ShapeParityProbe(muscadet.ObjFlow):
    """One continuous input to compare, one discrete input to read as a state."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="level")
        self.add_flow_in(name="ctl", logic="and")


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="OperandShapeParity")
    system.add_component(name="PROBE", cls="ShapeParityProbe")

    yield system


@pytest.fixture(scope="module")
def probe(the_system):
    return the_system.comp["PROBE"]


def guard_error(operand):
    """What a rule guard raises for ``operand``, or None when it accepts it."""
    try:
        muscadet_rules.RuleOperand(**operand)
    except Exception as error:
        return error
    return None


def prod_cond_error(probe, operand):
    """What a production condition raises for ``operand``, or None."""
    try:
        probe.postprocess_flow_specs({"var_prod_cond": [operand]})
    except Exception as error:
        return error
    return None


def named(shape, name="level"):
    """``shape`` as a complete operand mapping."""
    return dict(shape, name=name)


# ---------------------------------------------------------------------------
# The parity itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape, marker", BAD_SHAPES)
def test_both_directions_refuse_the_same_shapes(probe, shape, marker):
    """Neither direction accepts what the other refuses."""
    operand = named(shape)

    from_guard = guard_error(operand)
    from_prod_cond = prod_cond_error(probe, operand)

    assert from_guard is not None, f"rule guard accepted {operand}"
    assert from_prod_cond is not None, f"production condition accepted {operand}"

    # Both are model errors a caller catching ValueError still catches: a
    # pydantic ValidationError is one.
    assert isinstance(from_guard, ValueError)
    assert isinstance(from_prod_cond, ValueError)

    # And both refused it for the SAME reason.
    assert marker in str(from_guard)
    assert marker in str(from_prod_cond)


@pytest.mark.parametrize("shape", GOOD_SHAPES)
def test_both_directions_accept_the_same_shapes(probe, shape):
    """Neither direction refuses what the other accepts."""
    operand = named(shape)

    assert guard_error(operand) is None, f"rule guard refused {operand}"
    assert (
        prod_cond_error(probe, operand) is None
    ), f"production condition refused {operand}"


def test_the_offending_value_is_reported_by_both(probe):
    """An unknown operator is echoed back, on both sides, as it was written."""
    operand = named({"op": "~=", "value": 1})

    assert "'~='" in str(guard_error(operand))
    assert "'~='" in str(prod_cond_error(probe, operand))


def test_the_six_operators_are_the_shared_vocabulary(probe):
    """The accepted set is one constant, not two lists that can drift."""
    assert muscadet_rules.COMPARISON_OPERATORS == ("<", "<=", ">", ">=", "==", "!=")

    for op in muscadet_rules.COMPARISON_OPERATORS:
        operand = named({"op": op, "value": 1})
        assert guard_error(operand) is None
        assert prod_cond_error(probe, operand) is None


# ---------------------------------------------------------------------------
# The wordings, which differ on purpose
# ---------------------------------------------------------------------------


def test_the_two_wordings_differ_exactly_where_they_are_meant_to(probe):
    """One implementation, two labels -- and one clause naming a comparison.

    The guard operand names itself; the production condition names its
    component too. The pairing message additionally describes a comparison in
    each side's own terms. Nothing else about the two messages differs, and
    this is what would break if the shared function stopped taking them as
    parameters.
    """
    operand = named({"op": ">="})

    from_guard = str(guard_error(operand))
    from_prod_cond = str(prod_cond_error(probe, operand))

    assert (
        "Guard operand 'level': 'op' and 'value' must be given together "
        "(a numeric operand) or both omitted (a boolean operand)"
    ) in from_guard
    assert (
        "Object PROBE: production condition operand 'level': 'op' and 'value' "
        "must be given together (a comparison against a continuous quantity) "
        "or both omitted (a boolean operand)"
    ) in from_prod_cond

    # The label is what carries the difference on the other two rules.
    unknown = named({"op": "~=", "value": 1})
    assert "Guard operand 'level': 'op' must be one of" in str(guard_error(unknown))
    assert (
        "Object PROBE: production condition operand 'level': 'op' must be one of"
        in (str(prod_cond_error(probe, unknown)))
    )

    negated = named({"negate": True, "op": ">=", "value": 5})
    tail = (
        "'negate' cannot be combined with a comparison; use the opposite "
        "comparison operator instead"
    )
    assert f"Guard operand 'level': {tail}" in str(guard_error(negated))
    assert f"Object PROBE: production condition operand 'level': {tail}" in str(
        prod_cond_error(probe, negated)
    )


def test_the_shared_checks_are_reachable_and_parameterised():
    """The consolidation is one function, taking the wording as a parameter."""
    label = "Somewhere"

    with pytest.raises(ValueError, match="Somewhere: 'op' must be one of"):
        muscadet_rules.check_operand_operator(label, "~=")

    with pytest.raises(ValueError, match=r"Somewhere: 'op' and 'value'"):
        muscadet_rules.check_operand_pairing(label, ">=", None)

    with pytest.raises(ValueError, match="Somewhere: 'negate' cannot be combined"):
        muscadet_rules.check_operand_negation(label, ">=", True)

    # The default wording is the guard's; the other side passes its own.
    with pytest.raises(ValueError, match=r"\(a numeric operand\)"):
        muscadet_rules.validate_operand_shape(label, ">=", None, False)

    with pytest.raises(ValueError, match=r"\(something else entirely\)"):
        muscadet_rules.validate_operand_shape(
            label, ">=", None, False, "something else entirely"
        )

    # A well-shaped operand passes every one of them silently.
    muscadet_rules.validate_operand_shape(label, ">=", 10.0, False)
    muscadet_rules.validate_operand_shape(label, None, None, True)


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
