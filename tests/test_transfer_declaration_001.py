"""The transfer equation is a declared object, and what it refuses.

A transfer pair carries an equation returning the signed quantity to move
between two of a component's balances. That equation is a declared OBJECT and
not a bare callable, on the pattern of ``muscadet.Profile`` and for the
identical reason: it is read from inside the sweeps at the integration points
the solver chooses, so a law with a jump is crossed inside a step and
overshoots by that step with no error the solver can detect. Continuity cannot
be read off a Python function, so it is attested.

Where this parts company with a profile, and the departure is the point: a
profile SCALES production, so a negative factor would mean a negative quantity
and is refused. A transfer's sign IS its direction, so a negative quantity is
returned unchanged and a tank warming past its environment starts losing heat
with no declaration changing.

Pure declaration and evaluation: no system is built here, so there is nothing
to delete.
"""

import math

import pytest

import cod3s
import muscadet


class FakeChannel:
    """The slice of a measurement channel an operand reads."""

    def __init__(self, levels):
        self.levels = levels

    def get_level(self, flow=None):
        return self.levels[flow]


class FakeComponent:
    """Enough of a component for an operand to resolve against."""

    def __init__(self, channels=None):
        self.measurements_in = channels or {}

    def name(self):
        return "FAKE"


# ----------------------------------------------------------------------
# The attestation
# ----------------------------------------------------------------------


def test_a_declared_continuous_equation_evaluates():
    transfer = muscadet.Transfer(fun=lambda comp: 4.0, continuous=True)

    assert transfer.quantity(FakeComponent()) == pytest.approx(4.0)
    assert transfer.continuous is True


def test_a_bare_callable_is_refused_and_names_the_mechanism():
    """The message names the watched transition, not only the broken rule.

    A modeller told "pass a Transfer" learns the spelling. A modeller told why
    learns whether their law is one muscadet can integrate at all.
    """
    with pytest.raises(ValueError, match="watched transition"):
        muscadet.build_transfer(lambda comp: 1.0)


def test_the_attestation_has_no_default():
    with pytest.raises(ValueError, match="CONTINUOUS"):
        muscadet.Transfer(fun=lambda comp: 1.0)


@pytest.mark.parametrize("flag", [False, 1, "yes", None])
def test_only_an_explicit_true_attests(flag):
    """Anything truthy-but-not-True is refused: an attestation is not a hint."""
    with pytest.raises(ValueError, match="CONTINUOUS"):
        muscadet.Transfer(fun=lambda comp: 1.0, continuous=flag)


def test_a_non_callable_equation_is_refused():
    with pytest.raises(ValueError, match="not callable"):
        muscadet.Transfer(fun=42, continuous=True)


# ----------------------------------------------------------------------
# The signed quantity, which is the departure from Profile
# ----------------------------------------------------------------------


def test_a_negative_quantity_survives_unchanged():
    """The sign is the direction. A profile would have refused this value."""
    transfer = muscadet.Transfer(fun=lambda comp: -2.5, continuous=True)

    assert transfer.quantity(FakeComponent()) == pytest.approx(-2.5)


def test_an_infinite_quantity_is_allowed():
    """ "Move whatever the supply allows". The caller caps it before it lands."""
    transfer = muscadet.Transfer(fun=lambda comp: math.inf, continuous=True)

    assert math.isinf(transfer.quantity(FakeComponent()))


def test_nan_is_refused_where_it_happens():
    """A NaN reaching a balance propagates silently through every level below."""
    transfer = muscadet.Transfer(fun=lambda comp: float("nan"), continuous=True)

    with pytest.raises(ValueError, match="NaN"):
        transfer.quantity(FakeComponent(), pair_name="wall")


def test_a_non_numeric_return_is_refused_naming_the_pair():
    transfer = muscadet.Transfer(fun=lambda comp: "warm", continuous=True)

    with pytest.raises(ValueError, match="transfer pair wall"):
        transfer.quantity(FakeComponent(), pair_name="wall")


# ----------------------------------------------------------------------
# The mapping form
# ----------------------------------------------------------------------


def test_the_mapping_form_builds_the_named_shape():
    transfer = muscadet.build_transfer(
        {
            "cls": "ConductiveTransfer",
            "conductance": 0.5,
            "potential_a": {"const": 20.0},
            "potential_b": {"const": 10.0},
        }
    )

    assert isinstance(transfer, muscadet.ConductiveTransfer)
    assert transfer.quantity(FakeComponent()) == pytest.approx(5.0)


def test_a_mapping_without_cls_is_refused():
    with pytest.raises(ValueError, match="'cls'"):
        muscadet.build_transfer({"conductance": 1.0})


def test_an_unknown_shape_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown transfer class"):
        muscadet.build_transfer({"cls": "MagicTransfer"})


def test_the_base_class_is_not_reachable_from_the_mapping_form():
    """Its whole content is a Python function, which no mapping can carry.

    Offering it would advertise a serialised form that cannot be serialised.
    """
    assert "Transfer" not in muscadet.transfer.TRANSFER_CLASSES


def test_an_object_passes_through_build_unchanged():
    transfer = muscadet.Transfer(fun=lambda comp: 1.0, continuous=True)

    assert muscadet.build_transfer(transfer) is transfer


# ----------------------------------------------------------------------
# The conduction shape
# ----------------------------------------------------------------------


def test_conduction_follows_the_difference():
    transfer = muscadet.ConductiveTransfer(
        conductance=2.0, potential_a={"const": 30.0}, potential_b={"const": 12.0}
    )

    assert transfer.quantity(FakeComponent()) == pytest.approx(36.0)


def test_conduction_reverses_when_the_second_potential_overtakes():
    """The sign follows the gradient, so no model writes a direction clamp."""
    transfer = muscadet.ConductiveTransfer(
        conductance=2.0, potential_a={"const": 10.0}, potential_b={"const": 25.0}
    )

    assert transfer.quantity(FakeComponent()) == pytest.approx(-30.0)


def test_conduction_is_continuous_by_construction():
    """An affine function of two potentials attests nothing of its own."""
    transfer = muscadet.ConductiveTransfer(
        conductance=1.0, potential_a=1.0, potential_b=0.0
    )

    assert transfer.continuous is True


def test_a_negative_conductance_is_refused():
    """It would drive the quantity UP its own gradient, which is not transport."""
    with pytest.raises(ValueError, match="up its own gradient|UP its own gradient"):
        muscadet.ConductiveTransfer(conductance=-1.0, potential_a=1.0, potential_b=0.0)


def test_conduction_requires_both_potentials():
    with pytest.raises(ValueError, match="potential_a and potential_b"):
        muscadet.ConductiveTransfer(conductance=1.0, potential_a={"const": 1.0})


def test_conduction_requires_a_conductance():
    with pytest.raises(ValueError, match="conductance is required"):
        muscadet.ConductiveTransfer(potential_a=1.0, potential_b=0.0)


# ----------------------------------------------------------------------
# Operands
# ----------------------------------------------------------------------


def test_a_measurement_operand_reads_the_channel():
    comp = FakeComponent({"tank": FakeChannel({None: 224.0, "heat": 217.0})})
    transfer = muscadet.ConductiveTransfer(
        conductance=1.0,
        potential_a={"measurement": "tank", "flow": "heat"},
        potential_b={"const": 17.0},
    )

    assert transfer.quantity(comp) == pytest.approx(200.0)


def test_a_measurement_operand_without_a_flow_reads_the_total():
    comp = FakeComponent({"tank": FakeChannel({None: 224.0})})

    assert muscadet.resolve_operand(comp, {"measurement": "tank"}) == pytest.approx(
        224.0
    )


def test_a_bare_number_is_a_constant_potential():
    assert muscadet.resolve_operand(FakeComponent(), 7.5) == pytest.approx(7.5)


def test_an_undeclared_channel_is_refused_naming_the_component():
    with pytest.raises(ValueError, match="add_measurement_in"):
        muscadet.resolve_operand(FakeComponent(), {"measurement": "ghost"})


def test_an_unknown_operand_form_is_refused_naming_the_accepted_ones():
    with pytest.raises(ValueError, match="const"):
        muscadet.resolve_operand(FakeComponent(), {"quantity": "heat", "per": "water"})


# ----------------------------------------------------------------------
# Declaring a pair ON a component (U2)
# ----------------------------------------------------------------------
#
# One subject component carries every surface a pair may name, and the tests
# call add_transfer on it directly rather than through add_component: a refusal
# raised inside add_flows would leave a half-built component in the system,
# which is a worse thing to leave behind than the assertion is worth.


def a_quantity(value=1.0):
    """A declared equation returning a constant, for declaration tests."""
    return muscadet.Transfer(fun=lambda comp: value, continuous=True)


class TpdSubject(muscadet.ObjFlow):
    """Every kind of name a pair might be pointed at, declared once."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        for flow in ("a", "b", "d"):
            self.add_flow_continuous_in(name=flow)
            self.add_flow_continuous_out(name=flow)

        # Output-only: legal as one end of a two-flow pair, illegal as a
        # conduit, since a conduit meters a transit it does not have.
        self.add_flow_continuous_out(name="c")

        self.add_flow_out(name="sig", var_prod_default=True)
        self.add_measurement_in(name="probe")
        self.add_capacity(name="vol", flow="d", capacity=100.0, side="in")


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="TpdSys")
    system.add_component(name="SUBJ", cls="TpdSubject")
    yield system


@pytest.fixture(scope="module")
def subject(the_system):
    return the_system.comp["SUBJ"]


def test_a_pair_over_two_continuous_flows_registers(subject):
    pair = subject.add_transfer("exchange", flows=["a", "b"], equation=a_quantity())

    assert subject.transfers["exchange"] is pair
    assert pair.source == "a"
    assert pair.destination == "b"
    assert not pair.is_conduit


def test_the_same_flow_twice_is_the_conduit(subject):
    pair = subject.add_transfer("wall", flows=["d", "d"], equation=a_quantity())

    assert pair.is_conduit


def test_the_sign_routes_the_direction(subject):
    """KD1 lives here: the model writes no clamp, the library reads the sign."""
    pair = subject.add_transfer("signed", flows=["a", "b"], equation=a_quantity(-3.0))

    assert pair.directed(subject) == ("b", "a", pytest.approx(3.0))


def test_a_positive_quantity_keeps_the_declared_order(subject):
    pair = subject.add_transfer("plain", flows=["a", "b"], equation=a_quantity(3.0))

    assert pair.directed(subject) == ("a", "b", pytest.approx(3.0))


def test_the_mapping_form_is_accepted_at_declaration(subject):
    pair = subject.add_transfer(
        "mapped",
        flows=["a", "b"],
        equation={
            "cls": "ConductiveTransfer",
            "conductance": 1.0,
            "potential_a": 5.0,
            "potential_b": 1.0,
        },
    )

    assert pair.quantity(subject) == pytest.approx(4.0)


def test_a_duplicate_pair_name_is_refused(subject):
    subject.add_transfer("once", flows=["a", "b"], equation=a_quantity())

    with pytest.raises(ValueError, match="already exists"):
        subject.add_transfer("once", flows=["a", "b"], equation=a_quantity())


def test_a_measurement_channel_may_not_carry_a_quantity(subject):
    """The conserved-quantity firewall, in the one place a pair could breach it."""
    with pytest.raises(ValueError, match="conserves nothing"):
        subject.add_transfer("bad", flows=["probe", "a"], equation=a_quantity())


def test_a_capacity_is_not_a_flow(subject):
    with pytest.raises(ValueError, match="names capacity vol"):
        subject.add_transfer("bad", flows=["vol", "a"], equation=a_quantity())


def test_a_discrete_output_is_refused(subject):
    with pytest.raises(ValueError, match="not a continuous OUTPUT"):
        subject.add_transfer("bad", flows=["sig", "a"], equation=a_quantity())


def test_an_undeclared_flow_is_refused(subject):
    with pytest.raises(ValueError, match="not a continuous OUTPUT"):
        subject.add_transfer("bad", flows=["ghost", "a"], equation=a_quantity())


def test_a_conduit_without_an_input_side_is_refused(subject):
    """It meters a transit, and an output-only flow has no transit to meter."""
    with pytest.raises(ValueError, match="no transit to meter"):
        subject.add_transfer("bad", flows=["c", "c"], equation=a_quantity())


def test_an_output_only_flow_is_a_legal_end_of_a_two_flow_pair(subject):
    """The conduit rule is about metering a transit, not about the flow."""
    pair = subject.add_transfer("into_c", flows=["a", "c"], equation=a_quantity())

    assert pair.destination == "c"


@pytest.mark.parametrize("flows", [["a"], ["a", "b", "d"], []])
def test_a_pair_names_exactly_two_flows(subject, flows):
    with pytest.raises(ValueError, match="exactly two flows"):
        subject.add_transfer("bad", flows=flows, equation=a_quantity())


def test_a_bare_callable_is_refused_at_declaration_too(subject):
    with pytest.raises(ValueError, match="watched transition"):
        subject.add_transfer("bad", flows=["a", "b"], equation=lambda comp: 1.0)


def test_a_refused_pair_leaves_no_trace(subject):
    """A declaration that raised must not half-register."""
    assert "bad" not in subject.transfers


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
