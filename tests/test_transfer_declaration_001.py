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
