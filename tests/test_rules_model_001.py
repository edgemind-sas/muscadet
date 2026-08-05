"""Transformation rule DECLARATION model.

Declaration-time only: rules are declared, normalised, validated and stored.
Nothing here evaluates a rule or runs a simulation.
"""

import muscadet
import cod3s
import pytest

# The user's reference example: three ordered rules on a component carrying a
# discrete input F4, three continuous inputs F1/F2/F3 and a continuous output X.
REFERENCE_RULES = [
    # F4 false : 3.F3 + 2.F2 -> 2.X
    dict(
        cond=[{"name": "F4", "negate": True}],
        cons={"F3": 3, "F2": 2},
        prod={"X": 2},
    ),
    # F4 true and F1 < 10 : no production
    dict(
        cond=[{"name": "F4"}, {"name": "F1", "op": "<", "value": 10}],
        prod={"X": 0},
    ),
    # F4 true and F1 >= 10 : 3.F1 -> 0.5.X
    dict(
        cond=[{"name": "F4"}, {"name": "F1", "op": ">=", "value": 10}],
        cons={"F1": 3},
        prod={"X": 0.5},
    ),
]


@pytest.fixture(scope="module")
def the_system():
    """A system whose components declare transformation rule sets."""

    class Reactor(muscadet.ObjFlow):
        """The reference example, declared structurally."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="F4", logic="and")
            self.add_flow_continuous_in(name="F1")
            self.add_flow_continuous_in(name="F2")
            self.add_flow_continuous_in(name="F3")
            self.add_flow_continuous_out(name="X")
            self.add_rules(name="X", rules=REFERENCE_RULES)

    class ReactorStructGuard(muscadet.ObjFlow):
        """One rule, guard declared as a structured operand list."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="F4", logic="and")
            self.add_flow_continuous_in(name="F1")
            self.add_flow_continuous_out(name="X")
            self.add_rules(
                name="X",
                rules=[
                    dict(
                        cond=[
                            {"name": "F4"},
                            {"name": "F1", "op": ">=", "value": 10},
                        ],
                        cons={"F1": 3},
                        prod={"X": 0.5},
                    )
                ],
            )

    class ReactorStringGuard(muscadet.ObjFlow):
        """The very same rule, guard declared as an expression string."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="F4", logic="and")
            self.add_flow_continuous_in(name="F1")
            self.add_flow_continuous_out(name="X")
            self.add_rules(
                name="X",
                rules=[
                    dict(
                        cond="F4 and F1 >= 10",
                        cons={"F1": 3},
                        prod={"X": 0.5},
                    )
                ],
            )

    class Defaulted(muscadet.ObjFlow):
        """A rule set carrying a guarded rule and an unguarded default one."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="F4", logic="and")
            self.add_flow_continuous_in(name="F2")
            self.add_flow_continuous_in(name="F3")
            self.add_flow_continuous_out(name="X")
            self.add_rules(
                name="X",
                rules=[
                    dict(name="nominal", cond="F4", cons=["F2", "F3"], prod="X"),
                    dict(name="fallback", cons={"F2": None}, prod={"X": 1}),
                ],
            )

    class BothPorts(muscadet.ObjFlow):
        """One name carried by BOTH an input and an output flow."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_in(name="q")
            self.add_flow_continuous_out(name="q")
            self.add_rules(
                name="implicit",
                rules=[
                    dict(cond=[{"name": "q", "op": ">", "value": 1}], prod={"q": 1})
                ],
            )
            self.add_rules(
                name="explicit",
                rules=[
                    dict(
                        cond=[{"name": "q", "port": "out", "op": ">", "value": 1}],
                        prod={"q": 1},
                    )
                ],
            )

    system = muscadet.System(name="Sys")

    system.add_component(name="R", cls="Reactor")
    system.add_component(name="GSTRUCT", cls="ReactorStructGuard")
    system.add_component(name="GSTRING", cls="ReactorStringGuard")
    system.add_component(name="DEF", cls="Defaulted")
    system.add_component(name="BOTH", cls="BothPorts")

    return system


def test_reference_example_declares(the_system):
    """The three-rule reference example declares without error."""
    rule_set = the_system.comp["R"].rule_sets["X"]

    assert list(the_system.comp["R"].rule_sets) == ["X"]
    assert rule_set.name == "X"
    assert len(rule_set.rules) == 3

    # Rule 1: F4 false : 3.F3 + 2.F2 -> 2.X
    rule = rule_set.rules[0]
    assert rule.guard_expression() == "not F4"
    assert rule.cons == {"F3": 3.0, "F2": 2.0}
    assert rule.prod == {"X": 2.0}

    # Rule 2: F4 true and F1 < 10 : no production
    rule = rule_set.rules[1]
    assert rule.guard_expression() == "F4 and F1 < 10"
    assert rule.cons == {}
    assert rule.prod == {"X": 0.0}

    # Rule 3: F4 true and F1 >= 10 : 3.F1 -> 0.5.X
    rule = rule_set.rules[2]
    assert rule.guard_expression() == "F4 and F1 >= 10"
    assert rule.cons == {"F1": 3.0}
    assert rule.prod == {"X": 0.5}

    # Every rule is guarded: the set carries no default rule
    assert rule_set.default_rule is None
    assert len(rule_set.guarded_rules) == 3

    # Guard operands are bound to the flows they name
    assert rule_set.rules[0].cond[0].flow is the_system.comp["R"].flows_in["F4"]
    assert rule_set.rules[2].cond[1].flow is the_system.comp["R"].flows_in["F1"]


def test_string_guard_normalises_to_structured_form(the_system):
    """Covers AE7: a string guard is stored as, and serialises like, operands."""
    struct_set = the_system.comp["GSTRUCT"].rule_sets["X"]
    string_set = the_system.comp["GSTRING"].rule_sets["X"]

    # The string form is normalised into structured operands...
    operands = string_set.rules[0].cond
    assert [type(operand) for operand in operands] == [
        muscadet.RuleOperand,
        muscadet.RuleOperand,
    ]
    assert (operands[0].name, operands[0].negate, operands[0].op) == ("F4", False, None)
    assert (operands[1].name, operands[1].op, operands[1].value) == ("F1", ">=", 10.0)

    # ... and only the structured form is stored: both serialise identically
    assert string_set.model_dump() == struct_set.model_dump()

    # The stored guard renders back to the declared expression
    assert string_set.rules[0].guard_expression() == "F4 and F1 >= 10"


def test_string_guard_negation_and_operators(the_system):
    """The minimal grammar: conjunction, negation, six comparison operators."""
    comp = the_system.comp["R"]

    rule_set = comp.add_rules(
        name="grammar",
        rules=[dict(cond="not F4 and F1 <= 2.5 and F2 != 0", prod={"X": 1})],
    )

    assert [operand.to_expression() for operand in rule_set.rules[0].cond] == [
        "not F4",
        "F1 <= 2.5",
        "F2 != 0",
    ]
    assert rule_set.rules[0].cond[0].negate is True

    # A disjunction is not part of the grammar
    with pytest.raises(ValueError, match="only conjunctions are supported"):
        comp.add_rules(name="bad_or", rules=[dict(cond="F4 or F1 >= 10")])

    # Neither is a negated comparison
    with pytest.raises(ValueError, match="negates a comparison"):
        comp.add_rules(name="bad_neg", rules=[dict(cond="not F1 >= 10")])

    assert "bad_or" not in comp.rule_sets
    assert "bad_neg" not in comp.rule_sets


def test_omitted_coefficients_default_to_one(the_system):
    """A rule omitting cons / prod coefficients defaults them to 1."""
    rule_set = the_system.comp["DEF"].rule_sets["X"]

    # cons declared as a bare name list, prod as a bare name
    nominal = rule_set.rules[0]
    assert nominal.cons == {"F2": 1.0, "F3": 1.0}
    assert nominal.prod == {"X": 1.0}

    # cons declared as a map with an omitted (None) coefficient
    fallback = rule_set.rules[1]
    assert fallback.cons == {"F2": 1.0}
    assert fallback.prod == {"X": 1.0}


def test_unguarded_rule_is_the_default_rule(the_system):
    """A rule declared without a guard is the default of its set."""
    rule_set = the_system.comp["DEF"].rule_sets["X"]

    assert rule_set.rules[0].is_default is False
    assert rule_set.rules[1].is_default is True
    assert rule_set.default_rule is rule_set.rules[1]
    assert [rule.name for rule in rule_set.guarded_rules] == ["nominal"]


def test_two_unguarded_rules_raise(the_system):
    """KD8: a rule set may carry at most one default rule."""
    comp = the_system.comp["R"]

    with pytest.raises(ValueError, match="at most one rule may be declared without"):
        comp.add_rules(
            name="two_defaults",
            rules=[
                dict(name="d1", cons={"F2": 1}, prod={"X": 1}),
                dict(name="d2", cons={"F3": 1}, prod={"X": 2}),
            ],
        )

    assert "two_defaults" not in comp.rule_sets


def test_undeclared_flow_in_guard_raises(the_system):
    """A guard operand naming an undeclared flow raises, naming the offender."""
    comp = the_system.comp["R"]

    with pytest.raises(ValueError, match="flow F9 does not exist"):
        comp.add_rules(name="bad_guard", rules=[dict(cond="F9", prod={"X": 1})])

    # The same holds for the coefficient maps
    with pytest.raises(ValueError, match="flow NOPE does not exist"):
        comp.add_rules(name="bad_cons", rules=[dict(cond="F4", cons={"NOPE": 2})])

    with pytest.raises(ValueError, match="flow NOPE does not exist"):
        comp.add_rules(name="bad_prod", rules=[dict(cond="F4", prod={"NOPE": 2})])

    # A rejected declaration leaves the component untouched
    assert not {"bad_guard", "bad_cons", "bad_prod"} & set(comp.rule_sets)


def test_operand_resolves_to_input_unless_port_stated(the_system):
    """A name carried by both ports resolves to the input by default."""
    comp = the_system.comp["BOTH"]

    implicit = comp.rule_sets["implicit"].rules[0].cond[0]
    assert implicit.port == "in"
    assert implicit.flow is comp.flows_in["q"]

    explicit = comp.rule_sets["explicit"].rules[0].cond[0]
    assert explicit.port == "out"
    assert explicit.flow is comp.flows_out["q"]


def test_rule_set_round_trips_through_dict_form(the_system):
    """A dumped rule set redeclares into an identical rule set."""
    comp = the_system.comp["R"]

    dumped = comp.rule_sets["X"].model_dump()

    # The dump carries the structured form only (no guard string, no bound flow)
    assert dumped["cls"] == "RuleSet"
    assert dumped["rules"][2]["cond"][1] == {
        "cls": "RuleOperand",
        "name": "F1",
        "negate": False,
        "port": "in",
        "op": ">=",
        "value": 10.0,
    }

    redeclared = comp.add_rules(name="round_trip", rules=dumped["rules"])

    assert redeclared.model_dump()["rules"] == dumped["rules"]

    # ... and the redeclared set is fully resolved too
    assert redeclared.rules[2].cond[1].flow is comp.flows_in["F1"]


def test_duplicate_rule_set_name_raises(the_system):
    """A rule set name is unique on its component."""
    comp = the_system.comp["R"]

    with pytest.raises(ValueError, match="Rule set X already exists"):
        comp.add_rules(name="X", rules=REFERENCE_RULES)

    assert len(comp.rule_sets["X"].rules) == 3


def test_components_do_not_share_rule_sets(the_system):
    """Two components declared from the same specs own independent rule sets."""
    struct = the_system.comp["GSTRUCT"].rule_sets["X"]
    string = the_system.comp["GSTRING"].rule_sets["X"]

    assert struct is not string
    assert struct.rules[0].cond[0] is not string.rules[0].cond[0]
    assert struct.rules[0].cond[0].flow is the_system.comp["GSTRUCT"].flows_in["F4"]
    assert string.rules[0].cond[0].flow is the_system.comp["GSTRING"].flows_in["F4"]

    # The module-level reference specs were not mutated by any declaration
    assert REFERENCE_RULES[2]["cond"][1] == {"name": "F1", "op": ">=", "value": 10}


def test_component_without_rules_has_none(the_system):
    """Declaring no rule leaves the component with an empty rule set mapping."""
    assert the_system.comp["R"].rule_sets  # the reference component has some
    assert the_system.comp["BOTH"].rule_sets

    class NoRules(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_out(name="q")

    the_system.add_component(name="NORULE", cls="NoRules")
    assert the_system.comp["NORULE"].rule_sets == {}


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
