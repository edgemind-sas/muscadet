"""What a continuous declaration accepts, and what it refuses (R-15).

Two defects of the same family as the effect-resolution ones: the model builds,
runs to completion and reports plausible-but-wrong numbers with no diagnostic.

1. **A continuous flow used to swallow an unknown declaration key.** The
   classes inherit pydantic's ``extra="ignore"``, so
   ``FlowContinuousOut(var_prod_cond=["ctrl"])`` -- the DISCRETE production
   gate, written on a continuous output -- constructed with no
   ``var_prod_cond`` attribute at all and the source the modeller believed was
   gated produced unconditionally for the whole run. Worse,
   ``FlowContinuousIn(demand=5.0)`` uses the KB's own spelling
   (``ConsumerContinuous(demand=...)``) against a field named
   ``var_demand_default``, so a hand-written consumer published a demand of
   zero and its whole chain reported zero. This is what ``FlowModel.combine``
   / ``combine_fun`` were declared-and-refused to close for ONE key (R37),
   generalised to all of them.

2. **A capacity's ``content_init`` was unvalidated.** ``capacity`` is checked
   strictly positive and ``fill_rate`` non-negative; the content they bound was
   not. ``capacity=100, content_init={"q": 500}`` built a tank at five times its
   own volume -- the empty/full automaton initialises ``full``, the producer is
   throttled from t=0, and the bound violation is the one thing that automaton
   cannot report, being already past it. A negative content propagated into
   ``Capacity.split_draw``, whose share clamp exists explicitly "so a negative
   content cannot invert the split": the code worked around a state a validator
   makes unreachable.

The accepted set is checked BOTH ways, the shape ``ContinuousComponent``
established for the KB layer (R-3): every key a flow reads must build, and every
key it does not read must be refused. A field added without a value in
:data:`DK_FULL_FLOW_DECLARATIONS` fails loudly rather than going untested.

Both defects are refused at DECLARATION, so nothing here needs an engine: this
module builds no system and therefore carries no ``test_delete``, exactly as
``test_builders.py`` and the importer tests do. What the accepted declarations
then do at run time is covered by the models the rest of the suite already
drives.
"""

import math

import pytest

import muscadet
from muscadet.kb.continuous import ConsumerContinuous

#: R-15, one way round: a declaration key no continuous flow reads. The
#: spellings are the plausible slips, not nonsense -- the discrete production
#: gate on a continuous output, and the KB's own name for a declared demand.
DK_UNKNOWN_FLOW_DECLARATIONS = {
    "FlowContinuousIn": dict(demand=5.0),
    "FlowContinuousOut": dict(var_prod_cond=["ctrl"], var_prod_default=True),
}

#: R-15, the other way round: every field a continuous flow declares, with a
#: value that builds. Checked key by key against the model's fields.
DK_FULL_FLOW_DECLARATIONS = {
    "FlowContinuousIn": dict(
        name="q",
        var_type="float",
        var_fed_default=0.0,
        var_fed=None,
        var_fed_available=None,
        sm_flow_fed_fun=None,
        sm_flow_fed_name=None,
        component_authorized=[{"class_name_bkd": ".*"}],
        # Declared ONLY so they can be refused by name (R37), hence legal at
        # None and nowhere else. Exercised exactly as ``allocation_fun`` is.
        combine=None,
        combine_fun=None,
        var_demand=None,
        var_capability=None,
        comp_name=None,
        var_in=None,
        var_in_default=0.0,
        var_demand_default=3.0,
        demand_required=math.inf,
    ),
    "FlowContinuousOut": dict(
        name="q",
        var_type="float",
        var_fed_default=1.0,
        var_fed=None,
        var_fed_available=None,
        sm_flow_fed_fun=None,
        sm_flow_fed_name=None,
        component_authorized=[{"class_name_bkd": ".*"}],
        combine=None,
        combine_fun=None,
        var_demand=None,
        var_capability=None,
        comp_name=None,
        var_demand_in_default=0.0,
        allocation="proportional",
        allocation_shares={},
        allocation_priorities={},
        allocation_fun=None,
        allocated={},
        var_out_rate=None,
        derating={},
        profile=muscadet.SinusoidalProfile(period=24.0, offset=1.0),
        var_profile=None,
    ),
}

DK_FLOW_CLASSES = {
    "FlowContinuousIn": muscadet.FlowContinuousIn,
    "FlowContinuousOut": muscadet.FlowContinuousOut,
}

#: The volume the tank declarations below are bounded by.
DK_VOLUME = 100.0

#: What a consumer declared the RIGHT way asks for.
DK_DEMAND = 4.0


# ----------------------------------------------------------------------
# Defect 4 -- a key a continuous flow does not read is refused by name
# ----------------------------------------------------------------------


@pytest.mark.parametrize("clsname", sorted(DK_UNKNOWN_FLOW_DECLARATIONS))
def test_a_continuous_flow_refuses_a_key_it_does_not_read(clsname):
    """The regression: an unknown key used to be accepted and dropped.

    Left ignored, the parameter takes its default -- indistinguishable, for a
    numeric one, from a legitimate zero -- and the model reports the numbers of
    a system nobody declared.
    """
    flow_cls = DK_FLOW_CLASSES[clsname]
    params = DK_UNKNOWN_FLOW_DECLARATIONS[clsname]

    with pytest.raises(ValueError) as raised:
        flow_cls(name="q", **params)

    message = str(raised.value)
    assert "does not accept declaration key" in message
    for key in params:
        assert repr(key) in message

    # ... and it says what the class DOES accept, or the refusal only moves the
    # problem one step along.
    assert "it accepts" in message
    assert "name" in message


def test_the_refusal_names_the_flow_and_its_class():
    """A message that does not locate the mistake costs a bisection."""
    with pytest.raises(ValueError, match="FlowContinuousIn"):
        muscadet.FlowContinuousIn(name="cooling", demand=5.0)

    with pytest.raises(ValueError, match="'cooling'"):
        muscadet.FlowContinuousIn(name="cooling", demand=5.0)


@pytest.mark.parametrize("clsname", sorted(DK_FULL_FLOW_DECLARATIONS))
def test_every_key_a_continuous_flow_reads_is_accepted(clsname):
    """The accepted set is exactly what is exercised, both ways round (R-3).

    A field added to a continuous flow without a value here fails on the
    coverage assertion rather than going untested, and a value here naming a
    field that no longer exists fails on the construction.
    """
    flow_cls = DK_FLOW_CLASSES[clsname]
    declaration = DK_FULL_FLOW_DECLARATIONS[clsname]

    assert set(declaration) == set(flow_cls.model_fields)

    flow = flow_cls(**declaration)
    assert flow.name == "q"

    # Key by key too, so a key that only builds alongside another is caught.
    for key, value in declaration.items():
        if key == "name":
            continue
        assert flow_cls(name="q", **{key: value}).name == "q"


def test_a_combination_policy_keeps_its_own_refusal():
    """``combine`` stays a declared field, so it reaches its own message.

    Folding it into the generic unknown-key refusal would lose the sentence
    that says why a conserved quantity cannot be voted on (R37).
    """
    with pytest.raises(ValueError, match="cannot be declared on a flow"):
        muscadet.FlowContinuousIn(name="q", combine="median")

    with pytest.raises(ValueError, match="add_measurement_in"):
        muscadet.FlowContinuousOut(name="q", combine_fun=lambda values: values[0])


def test_the_discrete_family_is_untouched():
    """1.x surface, left as it was: the refusal is scoped to the continuous
    family, which is 2.0's own."""
    flow = muscadet.FlowDiscreteOut(name="q", not_a_key_anything_reads=3)

    assert flow.name == "q"
    assert not hasattr(flow, "not_a_key_anything_reads")


# ----------------------------------------------------------------------
# Defect 5 -- a capacity refuses a content it could never hold
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "content, expected",
    [
        ({"q": 500.0}, "occupies 500"),
        ({"q": DK_VOLUME + 1e-6}, "occupies 100"),
        ({"q": -5.0}, "must be positive or zero"),
        ({"q": float("nan")}, "must be positive or zero"),
    ],
    ids=["over-volume", "just-over", "negative", "nan"],
)
def test_a_capacity_refuses_a_content_it_cannot_hold(content, expected):
    """The regression: ``content_init`` used to be the one unvalidated bound.

    Over the volume, the automaton starts ``full`` and throttles the producer
    from t=0; below zero, the negative level propagates into ``split_draw``.
    """
    with pytest.raises(ValueError, match=expected):
        muscadet.Capacity(
            name="cuve", flows=["q"], capacity=DK_VOLUME, content_init=content
        )


def test_the_bound_is_on_the_weighted_total():
    """Several constituents share ONE volume, so the bound is not per flow.

    40 of each is 80 raw and neither exceeds 100 alone; weighted 1 and 2 they
    occupy 120 of a volume of 100.
    """
    flows = [{"name": "a", "weight": 1}, {"name": "b", "weight": 2}]

    with pytest.raises(ValueError, match="occupies 120"):
        muscadet.Capacity(
            name="cuve",
            flows=flows,
            capacity=DK_VOLUME,
            content_init={"a": 40.0, "b": 40.0},
        )

    # ... and the same content inside the bound builds, weights included.
    held = muscadet.Capacity(
        name="cuve",
        flows=flows,
        capacity=DK_VOLUME,
        content_init={"a": 40.0, "b": 30.0},
    )
    assert held.content_init == {"a": 40.0, "b": 30.0}


def test_a_capacity_filled_exactly_to_its_volume_is_legal():
    """The bound is the volume, not one epsilon below it: a tank may start full."""
    held = muscadet.Capacity(
        name="cuve", flows=["q"], capacity=DK_VOLUME, content_init={"q": DK_VOLUME}
    )

    assert held.content_init == {"q": DK_VOLUME}
    assert held.capacity == pytest.approx(DK_VOLUME)


def test_an_undeclared_content_still_starts_empty():
    """The validation costs a capacity that declares nothing exactly nothing."""
    held = muscadet.Capacity(name="cuve", flows=["q"], capacity=DK_VOLUME)

    assert held.content_init == {"q": 0.0}


# ----------------------------------------------------------------------
# The counterpart: the key that DOES work, on the declaration the KB builds
# ----------------------------------------------------------------------


def test_the_field_the_refused_spelling_was_aiming_at_still_works():
    """``var_demand_default`` is what a continuous input actually reads.

    ``demand=4`` -- the KB's spelling, and the refused one -- used to leave this
    at 0 and the whole chain downstream reported zero. Exercised here so the
    refusal cannot be "fixed" by dropping the field it points at; what the value
    then does over a run is what every continuous model in the suite drives.
    """
    flow = muscadet.FlowContinuousIn(name="q", var_demand_default=DK_DEMAND)

    assert flow.var_demand_default == pytest.approx(DK_DEMAND)

    # ... and the KB component that owns the ``demand`` spelling maps it here,
    # which is why the two names have to be told apart rather than merged.
    assert "demand" in ConsumerContinuous.DECLARATION_KEYS
