"""Discrete flow rename and legacy alias guarantees (MUSCADET 2.0).

The discrete flow classes are canonically named ``FlowDiscrete*``. The former
names remain available FOREVER as real ``pass``-bodied subclasses placed INSIDE
the canonical inheritance chain::

    FlowModel -> FlowDiscrete -> FlowDiscreteIn  -> FlowIn
    FlowModel -> FlowDiscrete -> FlowDiscreteOut -> FlowOut
                                                     -> FlowDiscreteOutTempo
                                                          -> FlowOutTempo
                                                     -> FlowDiscreteOutOnTrigger
                                                          -> FlowOutOnTrigger

``FlowDiscrete`` is the family marker mirroring
:class:`muscadet.FlowContinuous` (R-7): a pure ``pass``-bodied base declaring
no field, inserted so that a discrete flow is recognised by a POSITIVE
``isinstance`` test rather than by negating the continuous one.

Three properties matter and are asserted here:

1. Subclasses (not assignment aliases), so a flow built through a legacy name
   still reports that legacy name as its runtime class name.
2. The legacy ``FlowOut`` sits BETWEEN the canonical output class and the
   canonical tempo / trigger classes, so every ``isinstance`` relation that
   held before the rename still holds.
3. The marker sits ABOVE both canonical roots and below nothing else, so every
   discrete class -- canonical and legacy alike -- answers to it, and no
   continuous class does.
"""

import cod3s
import pydantic
import pytest

import muscadet
import muscadet.flow as muscadet_flow

CANONICAL_NAMES = [
    "FlowDiscreteIn",
    "FlowDiscreteOut",
    "FlowDiscreteOutTempo",
    "FlowDiscreteOutOnTrigger",
]

LEGACY_NAMES = [
    "FlowIn",
    "FlowOut",
    "FlowOutTempo",
    "FlowOutOnTrigger",
]


@pytest.fixture(scope="module")
def the_system():
    class Source(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            # Legacy class name through the dict form.
            self.add_flow(dict(cls="FlowOut", name="legacy", var_prod_default=True))
            # Canonical class name through the dict form.
            self.add_flow(
                dict(cls="FlowDiscreteOut", name="canonical", var_prod_default=True)
            )
            # Trigger source for the downstream on-trigger flow.
            self.add_flow(dict(cls="FlowOut", name="trig", var_prod_default=True))

    class Relay(muscadet.ObjFlow):
        def __init__(self, name, create_default_out_automata=True, **kwargs):
            super().__init__(
                name,
                create_default_out_automata=create_default_out_automata,
                **kwargs,
            )

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            # Legacy method form and canonical dict form, side by side.
            self.add_flow_in(name="legacy", logic="or")
            self.add_flow(dict(cls="FlowDiscreteIn", name="canonical", logic="or"))
            # Two mirror outputs, one per name set, on the same wiring.
            self.add_flow(
                dict(cls="FlowOut", name="mirror_legacy", var_prod_cond=["legacy"])
            )
            self.add_flow(
                dict(
                    cls="FlowDiscreteOut",
                    name="mirror_canonical",
                    var_prod_cond=["canonical"],
                )
            )
            self.add_flow_out_tempo(
                name="tempo",
                var_prod_cond=["legacy"],
                occ_enable_flow={"cls": "delay", "time": 0},
                occ_disable_flow={"cls": "delay", "time": 0},
            )
            self.add_flow_out_on_trigger(
                name="trig",
                trigger_time_up=0,
                trigger_time_down=0,
                trigger_logic="and",
                var_prod_default=True,
            )

    system = muscadet.System(name="Sys")
    system.add_component(name="Src", cls="Source")
    system.add_component(name="Rly", cls="Relay")

    system.connect_flow("Src", "Rly", "legacy")
    system.connect_flow("Src", "Rly", "canonical")
    system.connect_trigger("Src", "Rly", "trig")

    return system


# ---------------------------------------------------------------------------
# Class-level guarantees (no simulation involved)
# ---------------------------------------------------------------------------


def test_both_name_sets_import_from_package_root_and_flow_module():
    for clsname in CANONICAL_NAMES + LEGACY_NAMES:
        assert hasattr(muscadet, clsname), f"{clsname} missing from package root"
        assert hasattr(muscadet_flow, clsname), f"{clsname} missing from muscadet.flow"
        assert getattr(muscadet, clsname) is getattr(muscadet_flow, clsname)
        # Real classes carrying their own name, never assignment aliases.
        assert getattr(muscadet, clsname).__name__ == clsname


def test_inheritance_chain_shape():
    # The family marker roots both canonical chains, directly under FlowModel.
    assert muscadet.FlowDiscrete.__bases__ == (muscadet_flow.FlowModel,)

    # Input side.
    assert muscadet.FlowDiscreteIn.__bases__ == (muscadet.FlowDiscrete,)
    assert muscadet.FlowIn.__bases__ == (muscadet.FlowDiscreteIn,)

    # Output side: the legacy name sits BETWEEN the canonical parent and the
    # canonical tempo / trigger classes.
    assert muscadet.FlowDiscreteOut.__bases__ == (muscadet.FlowDiscrete,)
    assert muscadet.FlowOut.__bases__ == (muscadet.FlowDiscreteOut,)
    assert muscadet.FlowDiscreteOutTempo.__bases__ == (muscadet.FlowOut,)
    assert muscadet.FlowOutTempo.__bases__ == (muscadet.FlowDiscreteOutTempo,)
    assert muscadet.FlowDiscreteOutOnTrigger.__bases__ == (muscadet.FlowOut,)
    assert muscadet.FlowOutOnTrigger.__bases__ == (muscadet.FlowDiscreteOutOnTrigger,)

    # The 1.x isinstance relations still hold for the canonical classes.
    assert issubclass(muscadet.FlowDiscreteOutTempo, muscadet.FlowOut)
    assert issubclass(muscadet.FlowDiscreteOutOnTrigger, muscadet.FlowOut)

    # And every one of them still descends from FlowModel, which the marker was
    # inserted UNDER rather than in place of.
    for clsname in CANONICAL_NAMES + LEGACY_NAMES:
        assert issubclass(getattr(muscadet, clsname), muscadet_flow.FlowModel)


def test_discrete_marker_is_the_mirror_of_the_continuous_one():
    """R-7: enumerating discrete flows is a positive test, not a negation."""
    continuous = [
        muscadet.FlowContinuous,
        muscadet.FlowContinuousIn,
        muscadet.FlowContinuousOut,
    ]

    # Every discrete class answers to the marker...
    for clsname in CANONICAL_NAMES + LEGACY_NAMES:
        assert issubclass(getattr(muscadet, clsname), muscadet.FlowDiscrete)

    # ...and no continuous one does, in either direction.
    for cls in continuous:
        assert not issubclass(cls, muscadet.FlowDiscrete)
    assert not issubclass(muscadet.FlowDiscrete, muscadet.FlowContinuous)

    # The two markers are siblings under one base, so a third family would be
    # neither rather than being mis-labelled discrete.
    assert muscadet.FlowContinuous.__bases__ == (muscadet_flow.FlowModel,)
    assert muscadet.FlowDiscrete.__bases__ == (muscadet_flow.FlowModel,)


def test_discrete_marker_declares_no_field_of_its_own():
    """A pure marker: it must not change what a discrete flow serialises."""
    assert set(muscadet.FlowDiscrete.model_fields) == set(
        muscadet_flow.FlowModel.model_fields
    )


def test_alias_chain_builds_under_pydantic():
    """A canonical class inheriting THROUGH a no-field alias constructs and
    validates -- the pydantic v2 assumption the alias chain rests on."""

    class _CanonicalBase(muscadet_flow.FlowModel):
        canonical_field: int = pydantic.Field(0, description="canonical base field")

    class _LegacyAlias(_CanonicalBase):
        pass

    class _CanonicalLeaf(_LegacyAlias):
        leaf_field: int = pydantic.Field(1, description="canonical leaf field")

    class _LegacyLeafAlias(_CanonicalLeaf):
        pass

    probe = _LegacyLeafAlias(name="probe")
    assert type(probe).__name__ == "_LegacyLeafAlias"
    assert probe.canonical_field == 0
    assert probe.leaf_field == 1
    assert isinstance(probe, _CanonicalBase)
    assert isinstance(probe, _LegacyAlias)
    assert isinstance(probe, _CanonicalLeaf)

    # The alias adds no field of its own.
    assert set(_LegacyAlias.model_fields) == set(_CanonicalBase.model_fields)

    # Validation still runs through the chain: ``name`` is required.
    with pytest.raises(pydantic.ValidationError):
        _LegacyLeafAlias()

    # And the same holds on the real flow classes.
    assert set(muscadet.FlowOut.model_fields) == set(
        muscadet.FlowDiscreteOut.model_fields
    )
    assert set(muscadet.FlowOutTempo.model_fields) == set(
        muscadet.FlowDiscreteOutTempo.model_fields
    )
    with pytest.raises(pydantic.ValidationError):
        muscadet.FlowDiscreteOutTempo()


# ---------------------------------------------------------------------------
# Component-level guarantees
# ---------------------------------------------------------------------------


def test_dict_form_legacy_cls_keeps_legacy_runtime_name(the_system):
    flow = the_system.comp["Src"].flows_out["legacy"]
    assert type(flow).__name__ == "FlowOut"


def test_dict_form_canonical_cls_resolves(the_system):
    flow = the_system.comp["Src"].flows_out["canonical"]
    assert type(flow).__name__ == "FlowDiscreteOut"
    assert isinstance(flow, muscadet.FlowDiscreteOut)
    # Canonical instance is NOT an instance of the legacy leaf, which lives
    # BELOW it -- this is why the factory methods keep building legacy leaves.
    assert not isinstance(flow, muscadet.FlowOut)


def test_factory_methods_build_legacy_leaves(the_system):
    comp = the_system.comp["Rly"]
    assert type(comp.flows_in["legacy"]).__name__ == "FlowIn"
    assert type(comp.flows_out["tempo"]).__name__ == "FlowOutTempo"
    assert type(comp.flows_out["trig"]).__name__ == "FlowOutOnTrigger"


def test_tempo_flow_is_instance_of_legacy_and_canonical(the_system):
    flow = the_system.comp["Rly"].flows_out["tempo"]
    # Legacy names, exactly as 1.x client code tests them.
    assert isinstance(flow, muscadet.FlowOutTempo)
    assert isinstance(flow, muscadet.FlowOut)
    # Canonical names.
    assert isinstance(flow, muscadet.FlowDiscreteOutTempo)
    assert isinstance(flow, muscadet.FlowDiscreteOut)
    assert isinstance(flow, muscadet_flow.FlowModel)


def test_trigger_flow_is_instance_of_legacy_and_canonical(the_system):
    flow = the_system.comp["Rly"].flows_out["trig"]
    assert isinstance(flow, muscadet.FlowOutOnTrigger)
    assert isinstance(flow, muscadet.FlowOut)
    assert isinstance(flow, muscadet.FlowDiscreteOutOnTrigger)
    assert isinstance(flow, muscadet.FlowDiscreteOut)


def test_input_flows_are_instances_of_both_names(the_system):
    comp = the_system.comp["Rly"]
    assert isinstance(comp.flows_in["legacy"], muscadet.FlowIn)
    assert isinstance(comp.flows_in["legacy"], muscadet.FlowDiscreteIn)
    # Canonically declared input: canonical only.
    assert isinstance(comp.flows_in["canonical"], muscadet.FlowDiscreteIn)
    assert not isinstance(comp.flows_in["canonical"], muscadet.FlowIn)


def test_default_out_automata_attach_to_tempo_and_trigger(the_system):
    comp = the_system.comp["Rly"]
    prefix = comp.name()
    # The default ok/nok automaton is keyed "{component}_{flow}".
    assert f"{prefix}_tempo" in comp.automata_d
    assert f"{prefix}_trig" in comp.automata_d


def test_add_flow_unknown_cls_raises_naming_the_string(the_system):
    comp = the_system.comp["Src"]
    with pytest.raises(ValueError) as excinfo:
        comp.add_flow(dict(cls="FlowNotAFlowClass", name="nope"))
    assert "FlowNotAFlowClass" in str(excinfo.value)
    assert "nope" not in comp.flows_out
    assert "nope" not in comp.flows_in


# ---------------------------------------------------------------------------
# Runtime behaviour: canonically-declared flows behave like legacy ones
# ---------------------------------------------------------------------------


def test_canonical_and_legacy_flows_behave_identically(the_system):
    the_system.isimu_start()

    src = the_system.comp["Src"]
    rly = the_system.comp["Rly"]

    assert src.flows_out["legacy"].var_fed.value() is True
    assert src.flows_out["canonical"].var_fed.value() is True

    assert rly.flows_in["legacy"].var_fed.value() is True
    assert rly.flows_in["canonical"].var_fed.value() is True

    # Same wiring, one output declared with the legacy class name and one with
    # the canonical one: identical outcome.
    legacy_out = rly.flows_out["mirror_legacy"]
    canonical_out = rly.flows_out["mirror_canonical"]
    assert type(legacy_out).__name__ == "FlowOut"
    assert type(canonical_out).__name__ == "FlowDiscreteOut"
    assert legacy_out.var_fed.value() is True
    assert canonical_out.var_fed.value() is True
    assert legacy_out.var_prod.value() == canonical_out.var_prod.value()
    assert (
        legacy_out.var_fed_available.value() == canonical_out.var_fed_available.value()
    )


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
