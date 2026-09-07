"""Continuous flow family in the COD3S Platform importer (2026-08).

The platform exports a model whose KB interfaces used to be discrete only. A
``flow_family`` discriminator now selects the muscadet flow family, and it is
deliberately DISTINCT from the pre-existing ``flow_type`` discriminator, which
stays a discrete-only concern (``classic`` / ``tempo`` / ``on_trigger``).

What this file locks:

- an input interface of the continuous family builds a ``FlowContinuousIn``
  carrying its unconnected default and its declared demand;
- an output interface builds a ``FlowContinuousOut`` carrying its nominal rate
  and its allocation policy;
- the **profile decomposition**: muscadet's profile is a multiplicative factor
  on ``var_fed_default`` and its shape library holds no constant shape, so a
  constant profile projects onto the nominal rate ALONE and never reaches
  ``build_profile``; a modulated shape projects onto the rate **and** the
  factor;
- every refusal is a ``Cod3sPlatformImportError`` raised by the parse layer,
  naming what it refused — never a fallback to the discrete family and never a
  ValueError surfacing from muscadet's own flow declaration validator;
- a purely discrete KB builds exactly what it built before the change.
"""

import logging

import pytest

from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    _parse_interface,
    parse_platform_export,
    system_from_export,
)


@pytest.fixture
def build_profile_spy(monkeypatch):
    """Record every specification handed to muscadet's profile constructor.

    ``build_profile`` is reached at TWO points, and both must be watched or the
    assertion proves nothing: ``ObjFlow.postprocess_flow_specs`` resolves a
    ``{"cls": ...}`` mapping into a ``Profile`` object before the flow is built,
    and ``FlowContinuousOut.check_profile`` then normalises whatever the field
    holds. The second call therefore sees an already-built object, or ``None``
    when nothing was declared.
    """
    import muscadet.flow_continuous as flow_continuous
    import muscadet.obj as obj

    seen: list = []

    for module in (obj, flow_continuous):
        original = module.build_profile

        def spy(spec, flow_name=None, _original=original):
            seen.append(spec)
            return _original(spec, flow_name)

        monkeypatch.setattr(module, "build_profile", spy)

    return seen


@pytest.fixture
def cleanup_system():
    """Tear down PyCATSHOO state after a runtime build (one System per name)."""
    import cod3s

    systems: list = []
    yield systems
    for system in systems:
        try:
            system.deleteSys()
        except Exception:
            pass
    cod3s.terminate_session()


def _payload(interfaces, sys_name, connections=None):
    """Canonical payload: one component ``C1`` of class ``Cls``.

    ``interfaces`` is the raw KB ``interfaces`` mapping, so a test can declare
    any mix of directions and families.
    """
    return {
        "model": {
            "name": sys_name,
            "kb": {"name": "KB", "version": "1.0.0"},
            "elements": {
                "components": {
                    "c1": {"name": "C1", "class_name": "Cls", "attributes": []},
                },
                "connections": connections or {},
            },
        },
        "kb": {
            "component_templates": {
                "Cls": {"interfaces": interfaces},
            },
        },
    }


def _one(interface, sys_name):
    """Payload carrying a single interface."""
    return _payload({"iface": interface}, sys_name)


# ---------------------------------------------------------------------------
# Capability marker — contract the COD3S Platform translator probes
# ---------------------------------------------------------------------------


class TestCapabilityMarker:
    def test_marker_is_declared_true(self):
        # The platform guard reads it with getattr(module, marker, False), so an
        # older muscadet answers False and the platform refuses to simulate a
        # continuous KB rather than importing it as something else.
        from muscadet.importers import cod3s_platform

        assert cod3s_platform._SUPPORTS_CONTINUOUS_FLOW_FAMILY is True


# ---------------------------------------------------------------------------
# Parse layer — family discriminator
# ---------------------------------------------------------------------------


class TestParseFlowFamily:
    def test_absent_defaults_discrete(self):
        fs = _parse_interface({"name": "o", "port_type": {"general": "output"}})
        assert fs.flow_family == "discrete"
        assert fs.nominal_rate is None
        assert fs.profile_spec is None
        assert fs.allocation is None

    def test_explicit_discrete(self):
        fs = _parse_interface(
            {
                "name": "o",
                "port_type": {"general": "output"},
                "flow_family": "discrete",
            }
        )
        assert fs.flow_family == "discrete"

    def test_unknown_family_rejected_and_names_the_value(self):
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _parse_interface(
                {
                    "name": "o",
                    "port_type": {"general": "output"},
                    "flow_family": "quantum",
                }
            )
        message = str(excinfo.value)
        assert "flow_family" in message
        assert "quantum" in message

    def test_unknown_family_rejected_end_to_end(self):
        with pytest.raises(Cod3sPlatformImportError, match="quantum"):
            parse_platform_export(
                _one(
                    {
                        "name": "out",
                        "port_type": {"general": "output"},
                        "flow_family": "quantum",
                    },
                    sys_name="Mparse_badfamily",
                )
            )

    def test_family_is_not_the_flow_type_discriminator(self):
        # flow_type stays a DISCRETE-only concern: a continuous interface may
        # not carry it, and a discrete one keeps parsing it as before.
        fs = _parse_interface(
            {
                "name": "o",
                "port_type": {"general": "output"},
                "flow_type": "tempo",
                "occ_enable": {"cls": "delay", "time": 3},
            }
        )
        assert fs.flow_family == "discrete"
        assert fs.flow_type == "tempo"


# ---------------------------------------------------------------------------
# Parse layer — continuous interface fields
# ---------------------------------------------------------------------------


class TestParseContinuousInterface:
    def test_continuous_input_fields(self):
        fs = _parse_interface(
            {
                "name": "water",
                "port_type": {"general": "input"},
                "flow_family": "continuous",
                "var_in_default": 1.5,
                "demand_profile": {"cls": "constant", "value": 4.0},
            }
        )
        assert fs.direction == "input"
        assert fs.flow_family == "continuous"
        assert fs.var_in_default == 1.5
        assert fs.nominal_rate == 4.0
        assert fs.profile_spec is None

    def test_continuous_output_fields(self):
        fs = _parse_interface(
            {
                "name": "power",
                "port_type": {"general": "output"},
                "flow_family": "continuous",
                "production_profile": {"cls": "constant", "value": 7.0},
                "allocation": "proportional",
            }
        )
        assert fs.direction == "output"
        assert fs.flow_family == "continuous"
        assert fs.nominal_rate == 7.0
        assert fs.profile_spec is None
        assert fs.allocation == "proportional"

    def test_continuous_output_modulated_shape_decomposes(self):
        fs = _parse_interface(
            {
                "name": "solar",
                "port_type": {"general": "output"},
                "flow_family": "continuous",
                "production_profile": {
                    "cls": "sinusoidal",
                    "value": 10.0,
                    "amplitude": 0.5,
                    "period": 24.0,
                    "offset": 0.5,
                },
            }
        )
        # The value is ALWAYS the nominal rate; the shape parameters become the
        # multiplicative factor, in muscadet's own {"cls": ...} mapping form.
        assert fs.nominal_rate == 10.0
        assert fs.profile_spec == {
            "cls": "SinusoidalProfile",
            "amplitude": 0.5,
            "period": 24.0,
            "offset": 0.5,
        }

    def test_profile_without_shape_rejected(self):
        with pytest.raises(Cod3sPlatformImportError, match="cls"):
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {"value": 2.0},
                }
            )

    def test_profile_unknown_shape_rejected(self):
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {"cls": "sawtooth", "value": 2.0},
                }
            )
        assert "sawtooth" in str(excinfo.value)

    def test_profile_without_value_rejected(self):
        # The value IS the nominal rate. A profile with no rate would scale
        # nothing and produce nothing without signalling it.
        with pytest.raises(Cod3sPlatformImportError, match="value"):
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {"cls": "constant"},
                }
            )

    def test_constant_profile_with_foreign_parameter_rejected(self):
        with pytest.raises(Cod3sPlatformImportError, match="amplitude"):
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {
                        "cls": "constant",
                        "value": 2.0,
                        "amplitude": 3.0,
                    },
                }
            )

    def test_modulated_profile_with_foreign_parameter_rejected(self):
        with pytest.raises(Cod3sPlatformImportError, match="frequency"):
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {
                        "cls": "sinusoidal",
                        "value": 2.0,
                        "frequency": 3.0,
                    },
                }
            )

    def test_modulated_demand_profile_rejected_on_an_input(self):
        # The engine carries no profile channel on a continuous input: a
        # modulated demand would be silently flattened to its nominal.
        with pytest.raises(Cod3sPlatformImportError, match="sinusoidal"):
            _parse_interface(
                {
                    "name": "water",
                    "port_type": {"general": "input"},
                    "flow_family": "continuous",
                    "demand_profile": {
                        "cls": "sinusoidal",
                        "value": 2.0,
                        "period": 12.0,
                    },
                }
            )

    def test_allocation_policy_other_than_proportional_rejected(self):
        # v1 exposes proportional sharing only.
        with pytest.raises(Cod3sPlatformImportError, match="priority"):
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "allocation": "priority",
                }
            )


# ---------------------------------------------------------------------------
# Parse layer — the discrete keys a continuous interface may not carry
# ---------------------------------------------------------------------------


class TestContinuousRefusesDiscreteKeys:
    # ``prod_cond`` was refused here until R44 carried it across, and what
    # replaced that refusal is not a weaker one but a differently indexed one:
    # the key is refused on an INPUT, by direction rather than by family, in
    # TestContinuousProductionCondition above. The refusal that mattered was
    # never "this family does not read it" but "this port has nothing to
    # condition", and only one of the two was ever true of an output.

    @pytest.mark.parametrize(
        "key, value",
        [
            # ``negate`` stays: it negates the boolean STATE a port publishes,
            # and a rate has none. ``logic_inner_mode`` left this list with the
            # condition it qualifies -- alone it would say nothing, and beside a
            # condition it is what keeps a CNF meaning the same thing on both
            # sides.
            ("negate", True),
            ("flow_type", "tempo"),
            ("occ_enable", {"cls": "delay", "time": 3}),
            ("init_enable", True),
            ("trigger_time_up", 2.0),
            ("trigger_logic", "and"),
        ],
    )
    def test_discrete_only_keys_refused(self, key, value):
        with pytest.raises(Cod3sPlatformImportError, match=key):
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    key: value,
                }
            )

    def test_input_logic_refused_on_a_continuous_input(self):
        with pytest.raises(Cod3sPlatformImportError, match="input_logic"):
            _parse_interface(
                {
                    "name": "water",
                    "port_type": {"general": "input"},
                    "flow_family": "continuous",
                    "input_logic": "and",
                }
            )

    def test_platform_serialised_defaults_are_information_free(self):
        # The platform's ComponentInterface declares ``negate=False`` and
        # ``logic_inner_mode="and"`` with non-null defaults, so EVERY exported
        # interface carries them, a continuous one included, without anyone
        # having declared anything. Refusing them would refuse a payload that
        # means exactly what the absence of the key means.
        fs = _parse_interface(
            {
                "name": "power",
                "port_type": {"general": "output"},
                "flow_family": "continuous",
                "prod_cond": None,
                "negate": False,
                "logic_inner_mode": "and",
                "input_logic": None,
                "production_profile": {"cls": "constant", "value": 2.0},
            }
        )
        assert fs.flow_family == "continuous"
        assert fs.nominal_rate == 2.0

    def test_a_chosen_value_under_a_neutral_key_is_still_refused(self):
        # ``negate=True`` is a modeller believing in a negation that a
        # continuous flow will never apply.
        with pytest.raises(Cod3sPlatformImportError, match="negate"):
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "negate": True,
                }
            )

    def test_neutral_allocation_tolerated_on_a_discrete_interface(self):
        fs = _parse_interface(
            {
                "name": "power",
                "port_type": {"general": "output"},
                "allocation": "proportional",
            }
        )
        assert fs.flow_family == "discrete"
        assert fs.allocation is None

    def test_empty_discrete_key_is_information_free_and_tolerated(self):
        # A defensively serialised ``prod_cond: []`` carries no production
        # condition, so it is not a declaration the modeller can be wrong about.
        fs = _parse_interface(
            {
                "name": "power",
                "port_type": {"general": "output"},
                "flow_family": "continuous",
                "prod_cond": [],
            }
        )
        assert fs.flow_family == "continuous"

    def test_continuous_keys_refused_on_a_discrete_interface(self):
        with pytest.raises(Cod3sPlatformImportError, match="production_profile"):
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "production_profile": {"cls": "constant", "value": 2.0},
                }
            )


# ---------------------------------------------------------------------------
# Runtime layer — the flow classes actually built
# ---------------------------------------------------------------------------


class TestRuntimeContinuousDispatch:
    def test_continuous_input_builds_flowcontinuousin(self, cleanup_system):
        from muscadet.flow import FlowDiscreteIn
        from muscadet.flow_continuous import FlowContinuousIn

        system = system_from_export(
            _one(
                {
                    "name": "water",
                    "port_type": {"general": "input"},
                    "flow_family": "continuous",
                    "var_in_default": 1.5,
                    "demand_profile": {"cls": "constant", "value": 4.0},
                },
                sys_name="Mcont_in",
            )
        )
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_in["water"]
        assert isinstance(flow, FlowContinuousIn)
        assert not isinstance(flow, FlowDiscreteIn)
        assert flow.var_in_default == 1.5
        assert flow.var_demand_default == 4.0

    def test_continuous_output_builds_flowcontinuousout(self, cleanup_system):
        from muscadet.flow import FlowDiscreteOut
        from muscadet.flow_continuous import FlowContinuousOut

        system = system_from_export(
            _one(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {"cls": "constant", "value": 7.0},
                    "allocation": "proportional",
                },
                sys_name="Mcont_out",
            )
        )
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["power"]
        assert isinstance(flow, FlowContinuousOut)
        assert not isinstance(flow, FlowDiscreteOut)
        assert flow.var_fed_default == 7.0
        assert flow.allocation == "proportional"

    def test_continuous_output_defaults_when_no_profile_declared(self, cleanup_system):
        # A port a recipe or a capacity will serve declares no profile: the
        # muscadet defaults must stand, untouched.
        from muscadet.flow_continuous import FlowContinuousOut

        system = system_from_export(
            _one(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                },
                sys_name="Mcont_out_bare",
            )
        )
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["power"]
        assert isinstance(flow, FlowContinuousOut)
        assert flow.var_fed_default == 0.0
        assert flow.profile is None
        assert flow.allocation == "proportional"

    def test_mixed_families_on_one_component(self, cleanup_system):
        from muscadet.flow import FlowIn, FlowOut
        from muscadet.flow_continuous import FlowContinuousIn, FlowContinuousOut

        system = system_from_export(
            _payload(
                {
                    "d_in": {"name": "ctrl", "port_type": {"general": "input"}},
                    "d_out": {
                        "name": "signal",
                        "port_type": {"general": "output"},
                        "prod_cond": [["ctrl"]],
                    },
                    "c_in": {
                        "name": "water",
                        "port_type": {"general": "input"},
                        "flow_family": "continuous",
                    },
                    "c_out": {
                        "name": "power",
                        "port_type": {"general": "output"},
                        "flow_family": "continuous",
                        "production_profile": {"cls": "constant", "value": 3.0},
                    },
                },
                sys_name="Mcont_mixed",
            )
        )
        cleanup_system.append(system)
        comp = system.comp["C1"]
        assert isinstance(comp.flows_in["ctrl"], FlowIn)
        assert isinstance(comp.flows_out["signal"], FlowOut)
        assert isinstance(comp.flows_in["water"], FlowContinuousIn)
        assert isinstance(comp.flows_out["power"], FlowContinuousOut)


# ---------------------------------------------------------------------------
# Runtime layer — profile decomposition
# ---------------------------------------------------------------------------


class TestProfileDecomposition:
    def test_constant_profile_projects_onto_the_rate_and_builds_no_object(
        self, cleanup_system
    ):
        system = system_from_export(
            _one(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {"cls": "constant", "value": 2.0},
                },
                sys_name="Mcont_const",
            )
        )
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["power"]
        assert flow.var_fed_default == 2.0
        assert flow.profile is None

    def test_constant_profile_never_reaches_build_profile(
        self, cleanup_system, build_profile_spy
    ):
        # Instrumentation: muscadet's own profile constructor must never be
        # handed a declaration for a constant shape. ``check_profile`` calls it
        # unconditionally, so what is asserted is that every call it receives
        # carries None -- i.e. nothing was ever declared for it to build.
        system = system_from_export(
            _one(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {"cls": "constant", "value": 2.0},
                },
                sys_name="Mcont_spy",
            )
        )
        cleanup_system.append(system)
        assert (
            build_profile_spy
        ), "build_profile was never reached at all; the spy proves nothing"
        assert all(
            spec is None for spec in build_profile_spy
        ), f"a constant shape reached build_profile: {build_profile_spy!r}"

    def test_modulated_shape_builds_the_rate_and_the_profile_object(
        self, cleanup_system
    ):
        from muscadet.profile import SinusoidalProfile

        system = system_from_export(
            _one(
                {
                    "name": "solar",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {
                        "cls": "sinusoidal",
                        "value": 10.0,
                        "amplitude": 0.5,
                        "period": 24.0,
                        "offset": 0.5,
                    },
                },
                sys_name="Mcont_sin",
            )
        )
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["solar"]
        assert flow.var_fed_default == 10.0
        assert isinstance(flow.profile, SinusoidalProfile)
        assert flow.profile.amplitude == 0.5
        assert flow.profile.period == 24.0
        assert flow.profile.offset == 0.5
        # The profile is a FACTOR: at a quarter period past the origin the
        # sinusoid peaks, so the factor is offset + amplitude.
        assert flow.profile.factor(6.0, "solar") == pytest.approx(1.0)

    def test_modulated_shape_reaches_build_profile(
        self, cleanup_system, build_profile_spy
    ):
        # The mirror of the constant case: a modulated shape is exactly what
        # muscadet's profile constructor is FOR, and it must arrive there as the
        # {"cls": "SinusoidalProfile", ...} mapping the parse layer emitted.
        system = system_from_export(
            _one(
                {
                    "name": "solar",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {
                        "cls": "sinusoidal",
                        "value": 10.0,
                        "period": 24.0,
                    },
                },
                sys_name="Mcont_sinspy",
            )
        )
        cleanup_system.append(system)
        assert any(
            isinstance(spec, dict) and spec.get("cls") == "SinusoidalProfile"
            for spec in build_profile_spy
        ), (
            "the modulated shape never reached build_profile: " f"{build_profile_spy!r}"
        )


# ---------------------------------------------------------------------------
# Runtime layer — the discrete kwargs never leak into a continuous flow
# ---------------------------------------------------------------------------


class TestContinuousKwargsAreSeparate:
    def test_the_discrete_kwargs_do_not_leak_into_a_continuous_flow(
        self, cleanup_system
    ):
        system = system_from_export(
            _one(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {"cls": "constant", "value": 2.0},
                },
                sys_name="Mcont_nokeys",
            )
        )
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["power"]

        # ``var_prod_cond`` and its inner mode became fields of a continuous
        # output with R44 -- a production condition means the same thing on
        # both families -- so their ABSENCE no longer expresses what this test
        # guards. What it guards is unchanged: the two kwargs builders must not
        # be shared. So the flow must carry muscadet's own DEFAULTS and not a
        # value the discrete builder put there, which is the stronger check of
        # the two: the platform's own default inner mode is 'and' (matching the
        # KB Editor UI) where muscadet's is 'or', so a leak shows up as a value
        # and not merely as a present attribute.
        assert flow.var_prod_cond == []
        assert flow.var_prod_cond_negate == []
        assert flow.var_prod_cond_compare == []
        assert flow.var_prod_cond_inner_mode == "or"

        # These two never became continuous fields, and must still not exist:
        # ``var_prod_default`` names the boolean production variable only a
        # discrete output has, and ``negate`` is a FlowSpec-level key of the
        # importer that is no flow field on either family.
        for key in ("var_prod_default", "negate"):
            assert not hasattr(
                flow, key
            ), f"{key} reached a continuous flow; the kwargs dicts are shared"


# ---------------------------------------------------------------------------
# Production condition on a continuous output — the bridge (R44)
# ---------------------------------------------------------------------------


CTRL_IN = {"name": "ctrl", "port_type": {"general": "input"}}

LEVEL_IN = {
    "name": "level",
    "port_type": {"general": "input"},
    "flow_family": "continuous",
}


def _gated_output(prod_cond, **extra):
    """A continuous output port carrying a production condition."""
    return {
        "name": "power",
        "port_type": {"general": "output"},
        "flow_family": "continuous",
        "production_profile": {"cls": "constant", "value": 2.0},
        "prod_cond": prod_cond,
        **extra,
    }


class TestContinuousProductionCondition:
    def test_a_continuous_output_carries_its_production_condition(self, cleanup_system):
        """The whole point: a valve declared on the platform is a valve.

        The operand must arrive RESOLVED -- the flow object itself, not the
        name it was written as -- because that is what the gate reads.
        """
        system = system_from_export(
            _payload(
                {"ctrl": CTRL_IN, "power": _gated_output([["ctrl"]])},
                sys_name="Mcont_gate",
            )
        )
        cleanup_system.append(system)
        comp = system.comp["C1"]
        flow = comp.flows_out["power"]

        assert flow.var_prod_cond == [[comp.flows_in["ctrl"]]]

    def test_a_negated_operand_crosses_the_bridge(self, cleanup_system):
        """The per-operand negation, which is part of the operand vocabulary."""
        system = system_from_export(
            _payload(
                {
                    "ctrl": CTRL_IN,
                    "power": _gated_output([[{"name": "ctrl", "negate": True}]]),
                },
                sys_name="Mcont_gate_neg",
            )
        )
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["power"]

        assert flow.var_prod_cond_negate == [[True]]

    def test_a_comparison_operand_crosses_the_bridge(self, cleanup_system):
        """A rate gated on an observed quantity: the regulation shape."""
        system = system_from_export(
            _payload(
                {
                    "level": LEVEL_IN,
                    "power": _gated_output(
                        [[{"name": "level", "op": ">=", "value": 3.0}]]
                    ),
                },
                sys_name="Mcont_gate_cmp",
            )
        )
        cleanup_system.append(system)
        flow = system.comp["C1"].flows_out["power"]

        assert flow.var_prod_cond_compare == [[{"op": ">=", "value": 3.0}]]

    # The two inner-mode tests below look alike and catch different faults,
    # because the two sides disagree on the default: the platform reads a bare
    # CNF 'and'-first, muscadet 'or'-first. The first test therefore dies if
    # the mode never crosses at all; the second dies if it crosses as a
    # hardcoded default instead of what was declared. Either one alone leaves
    # half the bridge unwatched.

    def test_the_inner_mode_crosses_only_beside_a_condition(self, cleanup_system):
        """The platform reads a condition 'and'-first, muscadet 'or'-first.

        So the mode must cross whenever a condition does, or the same CNF would
        mean two different things on the two sides. And it must NOT cross on a
        port carrying no condition, where passing the platform default would be
        a value nobody declared -- which is exactly what the kwargs-leak guard
        further down watches for.
        """
        system = system_from_export(
            _payload(
                {"ctrl": CTRL_IN, "power": _gated_output([["ctrl"]])},
                sys_name="Mcont_gate_mode",
            )
        )
        cleanup_system.append(system)

        assert system.comp["C1"].flows_out["power"].var_prod_cond_inner_mode == "and"

    def test_the_declared_inner_mode_is_honoured(self, cleanup_system):
        """Declared 'or' must arrive as 'or', which the platform default is not."""
        system = system_from_export(
            _payload(
                {
                    "ctrl": CTRL_IN,
                    "power": _gated_output([["ctrl"]], logic_inner_mode="or"),
                },
                sys_name="Mcont_gate_mode_or",
            )
        )
        cleanup_system.append(system)

        assert system.comp["C1"].flows_out["power"].var_prod_cond_inner_mode == "or"

    def test_a_condition_on_a_continuous_input_is_refused_by_direction(self):
        """The guard had to stop being about the FAMILY and start being about
        the DIRECTION, or lifting it would let a condition through on a port
        that has no production to condition."""
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _parse_interface(
                {
                    "name": "water",
                    "port_type": {"general": "input"},
                    "flow_family": "continuous",
                    "prod_cond": [["ctrl"]],
                }
            )
        message = str(excinfo.value)

        assert "prod_cond" in message
        assert "input" in message
        assert "output" in message

    def test_a_whole_output_negation_stays_refused_without_misdirecting(self):
        """``negate`` negates a boolean STATE, and a rate has none.

        Distinct from the per-operand ``negate`` inside a condition, which does
        cross: that one negates what an operand READS, this one would negate
        what the port produces.

        The refusal needs a message of its own, and that is the whole reason
        this key is not simply in the discrete-family tuple: the generic family
        message ends on "declare the port as flow_family='discrete'", which is
        sound for a tempo class and a trap here, since someone writing
        ``negate`` on a rate wants an inverted gate and would get a boolean.
        """
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "negate": True,
                }
            )
        message = str(excinfo.value)

        assert "negate" in message
        assert "Do NOT declare the port as flow_family='discrete'" in message
        assert "prod_cond" in message

    def test_an_inner_mode_with_no_condition_to_qualify_is_refused(self):
        """A mode says how the GROUPS of a condition combine.

        Alone it decides nothing, and the bridge carries it only beside a
        condition, so accepting it would be accepting a declaration that is
        dropped on the way.
        """
        with pytest.raises(Cod3sPlatformImportError, match="logic_inner_mode"):
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "production_profile": {"cls": "constant", "value": 2.0},
                    "logic_inner_mode": "or",
                }
            )

    def test_a_bare_operand_is_ordered_like_a_nested_one(self, cleanup_system):
        """Declaration order must not decide whether a model builds, part two.

        ``prod_cond: "main"`` is a legal bare operand that muscadet normalises
        when it resolves it. The dependency sort walked the nested form only,
        so on the bare one it saw no dependency at all and the build came down
        to the order of the interface mapping. The dependent output is declared
        FIRST here, so nothing but the sort can save it.
        """
        system = system_from_export(
            _payload(
                {
                    "backup": {
                        "name": "backup",
                        "port_type": {"general": "output"},
                        "flow_family": "continuous",
                        "production_profile": {"cls": "constant", "value": 1.0},
                        "prod_cond": "main",
                    },
                    "main": {
                        "name": "main",
                        "port_type": {"general": "output"},
                        "flow_family": "continuous",
                        "production_profile": {"cls": "constant", "value": 5.0},
                    },
                },
                sys_name="Mcont_gate_bare",
            )
        )
        cleanup_system.append(system)
        comp = system.comp["C1"]

        assert comp.flows_out["backup"].var_prod_cond == [[comp.flows_out["main"]]]

    def test_an_empty_group_is_refused(self, cleanup_system):
        """A valve with no diagnostic, either always open or always shut.

        Under inner mode 'or' an empty group is always false, so the output
        never produces; under 'and' it is always true, so the condition does
        not bind. A malformed export gives a mute output either way.
        """
        with pytest.raises(Cod3sPlatformImportError, match="EMPTY group"):
            system_from_export(
                _payload(
                    {"power": _gated_output([[]])},
                    sys_name="Mcont_gate_empty",
                )
            )

    def test_the_gate_actually_closes_the_rate(self, cleanup_system):
        """The bridge declares, and the engine must obey what it declared.

        Every other test here reads a field. This one runs the model, because a
        bridge that carried the condition to a flow the sweep then ignored
        would satisfy all of them: the defect is in the call, not the function.

        ``ctrl`` is left unconnected, so it is unfed: the plain condition is
        false and the negated one true, on the same payload and the same step.
        """
        rates = {}
        for label, cond in (
            ("plain", [["ctrl"]]),
            ("negated", [[{"name": "ctrl", "negate": True}]]),
        ):
            system = system_from_export(
                _payload(
                    {"ctrl": CTRL_IN, "power": _gated_output(cond)},
                    sys_name=f"Mcont_gate_run_{label}",
                )
            )
            comp = system.comp["C1"]
            comp.add_atm2states(
                name="clock",
                st1="s0",
                st2="s1",
                occ_law_12={"cls": "delay", "time": 1.0},
                cond_occ_21=False,
            )
            system.isimu_start()
            system.isimu_step_forward()
            rates[label] = comp.flows_out["power"].var_fed.value()
            system.isimu_stop()
            system.deleteSys()

        import cod3s

        cod3s.terminate_session()

        assert rates["plain"] == pytest.approx(0.0)
        assert rates["negated"] == pytest.approx(2.0)

    def test_a_condition_naming_another_output_is_created_after_it(
        self, cleanup_system
    ):
        """Declaration order must not decide whether a model builds.

        ``_order_outputs_by_deps`` sorts the outputs by the names their
        conditions reference, and it reads ``FlowSpec.logic`` -- which the
        continuous parser left empty precisely so the sort would be a no-op.
        Filling it is what puts a continuous output under that sort, and this
        payload declares the dependent output FIRST so nothing but the sort can
        save it.
        """
        system = system_from_export(
            _payload(
                {
                    "backup": {
                        "name": "backup",
                        "port_type": {"general": "output"},
                        "flow_family": "continuous",
                        "production_profile": {"cls": "constant", "value": 1.0},
                        "prod_cond": [
                            [{"name": "main", "port": "out", "op": ">", "value": 0.0}]
                        ],
                    },
                    "main": {
                        "name": "main",
                        "port_type": {"general": "output"},
                        "flow_family": "continuous",
                        "production_profile": {"cls": "constant", "value": 5.0},
                    },
                },
                sys_name="Mcont_gate_order",
            )
        )
        cleanup_system.append(system)
        comp = system.comp["C1"]

        assert comp.flows_out["backup"].var_prod_cond == [[comp.flows_out["main"]]]

    def test_the_condition_survives_a_declaration_round_trip(self, cleanup_system):
        """What the importer built must be re-declarable.

        The condition is stored RESOLVED, so ``muscadet.declare`` rebuilds the
        declaration form rather than dumping it; a continuous output reaching
        that code for the first time is what needs saying so.
        """
        from muscadet import declare

        system = system_from_export(
            _payload(
                {"ctrl": CTRL_IN, "power": _gated_output([["ctrl"]])},
                sys_name="Mcont_gate_rt",
            )
        )
        cleanup_system.append(system)

        spec = declare.component_spec(system.comp["C1"])
        flow = next(f for f in spec["flows"] if f["name"] == "power")

        assert flow["cls"] == "FlowContinuousOut"
        assert flow["var_prod_cond"] == [[{"name": "ctrl", "port": "in"}]]
        # The inner mode is the one thing that could diverge without a trace,
        # the two sides defaulting differently: it must be IN the spec, or a
        # re-declared component would silently read the same CNF the other way.
        assert flow["var_prod_cond_inner_mode"] == "and"

        # And the spec must rebuild. Stopping at the dump would leave the name
        # of this test a claim rather than a check: what is asserted below is
        # that the operands resolve again, onto the flows of the NEW component.
        rebuilt = declare.build_component(system, dict(spec, name="C1_RT"))
        rebuilt_flow = rebuilt.flows_out["power"]

        assert rebuilt_flow.var_prod_cond == [[rebuilt.flows_in["ctrl"]]]
        assert rebuilt_flow.var_prod_cond_inner_mode == "and"


# ---------------------------------------------------------------------------
# Instance overrides — every existing role is a discrete-family one
# ---------------------------------------------------------------------------


class TestDiscreteOverrideOnAContinuousFlow:
    def test_discrete_override_role_refused(self):
        # role=var_in_default is a BOOLEAN facet on a discrete input. Applied to
        # a continuous one it would coerce to a plausible rate of 1.0; the other
        # roles would be dropped by the continuous kwargs builders in silence.
        payload = _one(
            {
                "name": "water",
                "port_type": {"general": "input"},
                "flow_family": "continuous",
            },
            sys_name="Mcont_override",
        )
        payload["model"]["elements"]["components"]["c1"]["attributes"] = [
            {"name": "water", "role": "var_in_default", "value": True}
        ]
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            parse_platform_export(payload)
        message = str(excinfo.value)
        assert "var_in_default" in message
        assert "continuous" in message


# ---------------------------------------------------------------------------
# Non-regression — a purely discrete KB is untouched
# ---------------------------------------------------------------------------


class TestDiscreteNonRegression:
    def test_discrete_kb_builds_the_same_system(self, cleanup_system):
        from muscadet.flow import FlowIn, FlowOut, FlowOutTempo

        payload = _payload(
            {
                "in__a": {
                    "name": "a",
                    "port_type": {"general": "input"},
                    "input_logic": "and",
                },
                "out__b": {
                    "name": "b",
                    "port_type": {"general": "output"},
                    "prod_cond": [["a"]],
                },
                "out__c": {
                    "name": "c",
                    "port_type": {"general": "output"},
                    "flow_type": "tempo",
                    "occ_enable": {"cls": "delay", "time": 3},
                },
            },
            sys_name="Mdisc_noregress",
        )

        ctx = parse_platform_export(payload)
        flows = {f.name: f for f in ctx.components[0].flows}
        assert flows["a"].direction == "input"
        assert flows["a"].logic == "and"
        assert flows["b"].logic == [["a"]]
        assert flows["b"].logic_inner_mode == "and"
        assert flows["b"].negate is False
        assert flows["c"].flow_type == "tempo"
        assert flows["c"].occ_enable == {"cls": "delay", "time": 3}
        # Every continuous field stays at its neutral default.
        for flow in flows.values():
            assert flow.flow_family == "discrete"
            assert flow.nominal_rate is None
            assert flow.profile_spec is None
            assert flow.allocation is None

        system = system_from_export(payload)
        cleanup_system.append(system)
        comp = system.comp["C1"]
        assert isinstance(comp.flows_in["a"], FlowIn)
        assert comp.flows_in["a"].logic == "and"
        assert isinstance(comp.flows_out["b"], FlowOut)
        assert comp.flows_out["b"].var_prod_cond_inner_mode == "and"
        assert isinstance(comp.flows_out["c"], FlowOutTempo)

    def test_discrete_declaration_round_trips_unchanged(self, cleanup_system):
        # The structural dump of a discrete component is the golden: it is what
        # muscadet.component_spec would have returned before the change.
        import muscadet

        system = system_from_export(
            _payload(
                {
                    "in__a": {"name": "a", "port_type": {"general": "input"}},
                    "out__b": {
                        "name": "b",
                        "port_type": {"general": "output"},
                        "prod_cond": [["a"]],
                    },
                },
                sys_name="Mdisc_spec",
            ),
            create_default_out_automata=False,
        )
        cleanup_system.append(system)
        spec = muscadet.component_spec(system.comp["C1"])
        declared = {flow["name"]: flow for flow in spec["flows"]}
        assert declared["a"]["cls"] == "FlowIn"
        assert declared["b"]["cls"] == "FlowOut"
        assert declared["b"]["var_prod_cond"] == [[{"name": "a", "port": "in"}]]
        for flow in spec["flows"]:
            assert "profile" not in flow
            assert "allocation" not in flow


# ===========================================================================
# U2 — capacities, rule sets, per-instance capacity overrides, deratings
# ===========================================================================
#
# The three declarations below live at the CLASS TEMPLATE level, beside
# ``interfaces`` and never inside it: a capacity is held over several flows and
# a rule set correlates several outputs, so neither is a property of one port.
# The fourth, the derating pairs, lives on the MODEL COMPONENT: a failure mode
# is declared per instance, and the variable it will clamp has to be allocated
# on the component that carries it.

from muscadet.importers.cod3s_platform import (  # noqa: E402
    CAPACITY_CONTENT_INIT_ROLE,
    CAPACITY_FILL_RATE_ROLE,
    CAPACITY_VOLUME_ROLE,
    _build_kb_capacities,
    _build_kb_lookup,
    _build_kb_rule_sets,
)


def _template_payload(
    interfaces,
    sys_name,
    *,
    capacities=None,
    rule_sets=None,
    components=None,
):
    """Canonical payload whose class template may carry the U2 sections.

    ``components`` overrides the single default instance, so a test can declare
    two components of the same class and give each its own overrides.
    """
    template = {"interfaces": interfaces}
    if capacities is not None:
        template["capacities"] = capacities
    if rule_sets is not None:
        template["rule_sets"] = rule_sets

    if components is None:
        components = {
            "c1": {"name": "C1", "class_name": "Cls", "attributes": []},
        }

    return {
        "model": {
            "name": sys_name,
            "kb": {"name": "KB", "version": "1.0.0"},
            "elements": {"components": components, "connections": {}},
        },
        "kb": {"component_templates": {"Cls": template}},
    }


def _cont_in(name):
    return {
        "name": name,
        "port_type": {"general": "input"},
        "flow_family": "continuous",
    }


def _cont_out(name, value=None):
    iface = {
        "name": name,
        "port_type": {"general": "output"},
        "flow_family": "continuous",
    }
    if value is not None:
        iface["production_profile"] = {"cls": "constant", "value": value}
    return iface


def _capacities_of(payload, class_name="Cls"):
    """Parse-layer capacities of one class, without going through a component."""
    kb = payload["kb"]
    return _build_kb_capacities(kb, _build_kb_lookup(kb))[class_name]


def _rule_sets_of(payload, class_name="Cls"):
    kb = payload["kb"]
    return _build_kb_rule_sets(
        kb, _build_kb_lookup(kb), _build_kb_capacities(kb, _build_kb_lookup(kb))
    )[class_name]


# ---------------------------------------------------------------------------
# Capability markers — the four contracts the platform translator probes
# ---------------------------------------------------------------------------


class TestU2CapabilityMarkers:
    @pytest.mark.parametrize(
        "marker",
        [
            "_SUPPORTS_CONTINUOUS_CAPACITIES",
            "_SUPPORTS_CONTINUOUS_RULE_SETS",
            "_SUPPORTS_INSTANCE_CAPACITY_OVERRIDE",
            "_SUPPORTS_DERATING_PREALLOCATION",
            "_SUPPORTS_CAPACITY_SERVE_COMMAND",
        ],
    )
    def test_marker_is_declared_true(self, marker):
        from muscadet.importers import cod3s_platform

        assert getattr(cod3s_platform, marker) is True


# ---------------------------------------------------------------------------
# Parse layer — capacities declared on the class template
# ---------------------------------------------------------------------------


class TestParseCapacities:
    def test_single_flow_capacity_carries_volume_side_and_content(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_one",
            capacities=[
                {
                    "name": "tank",
                    "flows": [{"name": "H2"}],
                    "volume": 6.0,
                    "side": "out",
                    "content_init": {"H2": 3.0},
                }
            ],
        )
        (cap,) = _capacities_of(payload)
        assert cap.name == "tank"
        assert cap.volume == 6.0
        assert cap.side == "out"
        assert cap.content_init == {"H2": 3.0}
        assert [(f.name, f.weight) for f in cap.flows] == [("H2", 1.0)]
        # Not declared: the muscadet default must stand, so nothing is carried.
        assert cap.fill_rate is None

    def test_multi_flow_capacity_defaults_the_weight_to_one(self):
        payload = _template_payload(
            {"a": _cont_in("H2O"), "b": _cont_in("additif")},
            sys_name="Mcap_multi",
            capacities=[
                {
                    "name": "cuve",
                    "flows": [{"name": "H2O"}, {"name": "additif", "weight": 2.0}],
                    "volume": 1000.0,
                }
            ],
        )
        (cap,) = _capacities_of(payload)
        assert [(f.name, f.weight) for f in cap.flows] == [
            ("H2O", 1.0),
            ("additif", 2.0),
        ]
        assert cap.side is None  # left to muscadet's own resolution

    def test_flow_shorthand_string_is_accepted(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_short",
            capacities=[{"name": "tank", "flows": "H2", "volume": 6.0}],
        )
        (cap,) = _capacities_of(payload)
        assert [(f.name, f.weight) for f in cap.flows] == [("H2", 1.0)]

    def test_fill_rate_accepts_the_unbounded_spelling(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_inf",
            capacities=[
                {"name": "tank", "flows": "H2", "volume": 6.0, "fill_rate": "inf"}
            ],
        )
        (cap,) = _capacities_of(payload)
        assert cap.fill_rate == float("inf")

    def test_unknown_flow_is_refused_and_named(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_unknown",
            capacities=[{"name": "tank", "flows": "H2O", "volume": 6.0}],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        message = str(excinfo.value)
        assert "'H2O'" in message
        assert "tank" in message

    def test_discrete_flow_is_refused(self):
        payload = _template_payload(
            {
                "o": _cont_out("H2"),
                "d": {"name": "signal", "port_type": {"general": "output"}},
            },
            sys_name="Mcap_discrete",
            capacities=[{"name": "tank", "flows": "signal", "volume": 6.0}],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        message = str(excinfo.value)
        assert "signal" in message
        assert "discrete" in message

    def test_capacity_without_volume_is_refused(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_novol",
            capacities=[{"name": "tank", "flows": "H2"}],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        assert "volume" in str(excinfo.value)

    def test_capacity_on_a_class_with_no_continuous_flow_is_refused(self):
        payload = _template_payload(
            {"d": {"name": "signal", "port_type": {"general": "output"}}},
            sys_name="Mcap_nocont",
            capacities=[{"name": "tank", "flows": "signal", "volume": 6.0}],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        message = str(excinfo.value)
        assert "Cls" in message
        assert "continuous" in message

    def test_duplicate_capacity_name_is_refused(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_dup",
            capacities=[
                {"name": "tank", "flows": "H2", "volume": 6.0},
                {"name": "tank", "flows": "H2", "volume": 7.0},
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        assert "tank" in str(excinfo.value)

    def test_content_init_naming_a_flow_the_capacity_does_not_hold_is_refused(self):
        payload = _template_payload(
            {"o": _cont_out("H2"), "p": _cont_out("O2")},
            sys_name="Mcap_content_alien",
            capacities=[
                {
                    "name": "tank",
                    "flows": "H2",
                    "volume": 6.0,
                    "content_init": {"O2": 1.0},
                }
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        assert "O2" in str(excinfo.value)

    def test_capacities_reach_the_component_spec(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_ctx",
            capacities=[{"name": "tank", "flows": "H2", "volume": 6.0}],
        )
        ctx = parse_platform_export(payload)
        (cap,) = ctx.components[0].capacities
        assert cap.name == "tank"

    def test_the_discharge_keys_are_absent_until_they_are_declared(self):
        # muscadet's own defaults are an unbounded ceiling and an empty
        # command, so an undeclared key must reach ``add_capacity`` as an
        # ABSENCE and never as a value this layer made up.
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_serve_absent",
            capacities=[{"name": "tank", "flows": "H2", "volume": 6.0}],
        )
        (cap,) = _capacities_of(payload)
        assert cap.serve_rate is None
        assert cap.serve_cond == ()
        assert cap.serve_cond_inner_mode is None

    def test_serve_rate_is_parsed_and_accepts_the_unbounded_spelling(self):
        payload = _template_payload(
            {"o": _cont_out("H2"), "p": _cont_out("O2")},
            sys_name="Mcap_serve_rate",
            capacities=[
                {"name": "tank", "flows": "H2", "volume": 6.0, "serve_rate": 40.0},
                {"name": "free", "flows": "O2", "volume": 6.0, "serve_rate": "inf"},
            ],
        )
        tank, free = _capacities_of(payload)
        assert tank.serve_rate == 40.0
        assert free.serve_rate == float("inf")

    def test_a_negative_serve_rate_is_refused(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_serve_neg",
            capacities=[
                {"name": "tank", "flows": "H2", "volume": 6.0, "serve_rate": -1.0}
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        message = str(excinfo.value)
        assert "serve_rate" in message
        assert "tank" in message
        assert "positive or zero" in message

    def test_serve_cond_reads_the_nested_operand_form_prod_cond_uses(self):
        payload = _template_payload(
            {
                "c": {"name": "cmd", "port_type": {"general": "input"}},
                "o": _cont_out("H2"),
            },
            sys_name="Mcap_serve_cond",
            capacities=[
                {
                    "name": "battery",
                    "flows": "H2",
                    "volume": 6.0,
                    "serve_cond": [
                        [{"name": "cmd"}],
                        [{"name": "H2", "port": "out", "op": ">=", "value": 2.0}],
                    ],
                }
            ],
        )
        (cap,) = _capacities_of(payload)
        first, second = cap.serve_cond
        (cmd,) = first
        assert (cmd.name, cmd.negate, cmd.port, cmd.op) == ("cmd", False, None, None)
        (level,) = second
        assert (level.name, level.port, level.op, level.value) == (
            "H2",
            "out",
            ">=",
            2.0,
        )

    def test_serve_cond_normalises_the_shorthands_muscadet_normalises(self):
        # ``prod_cond`` accepts a bare operand and a flat list as much as the
        # nested form, and muscadet normalises both to groups. Parsing them
        # into a different shape here would be the second grammar this exists
        # to avoid.
        payload = _template_payload(
            {
                "c": {"name": "cmd", "port_type": {"general": "input"}},
                "d": {"name": "other", "port_type": {"general": "input"}},
                "o": _cont_out("H2"),
            },
            sys_name="Mcap_serve_short",
            capacities=[
                {"name": "bare", "flows": "H2", "volume": 6.0, "serve_cond": "cmd"},
                {
                    "name": "flat",
                    "flows": "H2",
                    "volume": 6.0,
                    "serve_cond": ["cmd", "other"],
                },
            ],
        )
        bare, flat = _capacities_of(payload)
        assert [[op.name for op in group] for group in bare.serve_cond] == [["cmd"]]
        assert [[op.name for op in group] for group in flat.serve_cond] == [
            ["cmd"],
            ["other"],
        ]

    def test_a_serve_cond_operand_naming_no_flow_is_refused(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_serve_unknown",
            capacities=[
                {
                    "name": "battery",
                    "flows": "H2",
                    "volume": 6.0,
                    "serve_cond": [["cmd"]],
                }
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        message = str(excinfo.value)
        assert "'cmd'" in message
        assert "battery" in message
        # The operand is named for what it is. One function serves the rule
        # guard and the discharge command, so calling this a "guard operand"
        # would send the author looking for a rule set they never wrote.
        assert "command operand" in message
        assert "guard" not in message

    def test_a_serve_cond_operand_naming_a_capacity_is_refused_as_such(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_serve_capname",
            capacities=[
                {
                    "name": "battery",
                    "flows": "H2",
                    "volume": 6.0,
                    "serve_cond": [["battery"]],
                }
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        message = str(excinfo.value)
        assert "capacity 'battery'" in message
        assert "not a flow" in message

    def test_a_serve_cond_naming_a_capacity_declared_LATER_is_refused_too(self):
        # The names are collected off the raw entries before any is parsed: a
        # refusal that depended on declaration order is one a reader cannot
        # reproduce from the file they are looking at.
        payload = _template_payload(
            {"o": _cont_out("H2"), "p": _cont_out("O2")},
            sys_name="Mcap_serve_forward",
            capacities=[
                {
                    "name": "first",
                    "flows": "H2",
                    "volume": 6.0,
                    "serve_cond": [["second"]],
                },
                {"name": "second", "flows": "O2", "volume": 6.0},
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        assert "capacity 'second'" in str(excinfo.value)

    def test_an_unsupported_serve_cond_comparison_is_refused(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_serve_op",
            capacities=[
                {
                    "name": "battery",
                    "flows": "H2",
                    "volume": 6.0,
                    "serve_cond": [[{"name": "H2", "op": "=>", "value": 2.0}]],
                }
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        assert "'=>'" in str(excinfo.value)

    def test_serve_cond_inner_mode_is_carried_and_needs_a_condition(self):
        payload = _template_payload(
            {
                "c": {"name": "cmd", "port_type": {"general": "input"}},
                "o": _cont_out("H2"),
            },
            sys_name="Mcap_serve_mode",
            capacities=[
                {
                    "name": "battery",
                    "flows": "H2",
                    "volume": 6.0,
                    "serve_cond": [["cmd"]],
                    "serve_cond_inner_mode": "or",
                }
            ],
        )
        (cap,) = _capacities_of(payload)
        assert cap.serve_cond_inner_mode == "or"

        alone = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_serve_mode_alone",
            capacities=[
                {
                    "name": "battery",
                    "flows": "H2",
                    "volume": 6.0,
                    "serve_cond_inner_mode": "or",
                }
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(alone)
        assert "serve_cond_inner_mode" in str(excinfo.value)

    def test_a_command_operand_naming_a_flow_a_capacity_shares_its_name_with(self):
        # The two vocabularies resolve through two engine functions, and only
        # one of them looks at the capacities: a rule guard goes through
        # _resolve_rule_flow, which refuses a capacity name outright, while a
        # discharge command goes through apply_prod_cond, which resolves the
        # FLOWS first and never sees a capacity. ``add_capacity(name=X,
        # flow=X)`` is the spelling R49 calls the most natural there is, so
        # refusing the operand here would refuse a model the engine builds.
        payload = _template_payload(
            {"i": _cont_in("elec"), "o": _cont_out("elec"), "p": _cont_out("H2")},
            sys_name="Mcap_serve_shadow",
            capacities=[
                {"name": "elec", "flows": "elec", "volume": 10.0, "side": "in"},
                {
                    "name": "battery",
                    "flows": "H2",
                    "volume": 6.0,
                    "side": "out",
                    "serve_cond": [
                        [{"name": "elec", "port": "in", "op": ">", "value": 0.0}]
                    ],
                },
            ],
        )
        _, battery = _capacities_of(payload)
        ((operand,),) = battery.serve_cond
        assert (operand.name, operand.port, operand.op) == ("elec", "in", ">")

    def test_a_rule_guard_still_refuses_a_flow_a_capacity_shares_its_name_with(self):
        # The other half of the same contract: the engine DOES refuse it there,
        # so relaxing the command path must not relax this one.
        payload = _template_payload(
            {"i": _cont_in("elec"), "o": _cont_out("H2")},
            sys_name="Mcap_guard_shadow",
            capacities=[
                {"name": "elec", "flows": "elec", "volume": 10.0, "side": "in"}
            ],
            rule_sets=[
                {
                    "name": "make",
                    "rules": [
                        {
                            "name": "on",
                            "cond": [{"name": "elec", "op": ">", "value": 0.0}],
                            "prod": {"H2": 1.0},
                        }
                    ],
                }
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _rule_sets_of(payload)
        assert "capacity 'elec'" in str(excinfo.value)

    def test_an_empty_serve_cond_inner_mode_is_refused_rather_than_defaulted(self):
        # A platform form serialising an unset select as "" must not silently
        # select outer-OR / inner-AND: that inverts the condition of anyone who
        # meant "or", with nothing anywhere saying so.
        payload = _template_payload(
            {
                "c": {"name": "cmd", "port_type": {"general": "input"}},
                "o": _cont_out("H2"),
            },
            sys_name="Mcap_serve_mode_empty",
            capacities=[
                {
                    "name": "battery",
                    "flows": "H2",
                    "volume": 6.0,
                    "serve_cond": [["cmd"]],
                    "serve_cond_inner_mode": "",
                }
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        assert "serve_cond_inner_mode" in str(excinfo.value)

    def test_a_negation_beside_a_comparison_is_refused_at_the_parse_layer(self):
        # muscadet's third operand shape rule. Left to the engine it surfaces
        # as a ValueError about a "production condition operand", for something
        # the author declared as a discharge command.
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mcap_serve_negate",
            capacities=[
                {
                    "name": "battery",
                    "flows": "H2",
                    "volume": 6.0,
                    "serve_cond": [
                        [{"name": "H2", "negate": True, "op": ">=", "value": 2.0}]
                    ],
                }
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        message = str(excinfo.value)
        assert "negate" in message
        assert "battery" in message

    def test_an_unknown_serve_cond_inner_mode_is_refused(self):
        payload = _template_payload(
            {
                "c": {"name": "cmd", "port_type": {"general": "input"}},
                "o": _cont_out("H2"),
            },
            sys_name="Mcap_serve_mode_bad",
            capacities=[
                {
                    "name": "battery",
                    "flows": "H2",
                    "volume": 6.0,
                    "serve_cond": [["cmd"]],
                    "serve_cond_inner_mode": "xor",
                }
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        assert "'xor'" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Parse layer — a discharge nothing can read is refused, not accepted inert
# ---------------------------------------------------------------------------


def _accumulator_payload(sys_name, *, capacity_extra, rule_sets=None, outputs=None):
    """A one-sided input volume, with whatever route out the test gives it."""
    interfaces = {"i": _cont_in("H2")}
    if outputs:
        for index, name in enumerate(outputs):
            interfaces[f"o{index}"] = _cont_out(name)
    capacity = {"name": "hopper", "flows": "H2", "volume": 6.0, "side": "in"}
    capacity.update(capacity_extra)
    return _template_payload(
        interfaces,
        sys_name=sys_name,
        capacities=[capacity],
        rule_sets=rule_sets,
    )


class TestDischargeOnAVolumeWithNoWayOut:
    @pytest.mark.parametrize(
        "extra",
        [
            {"serve_rate": 40.0},
            {"serve_cond": [["H2"]]},
        ],
        ids=["ceiling", "command"],
    )
    def test_a_discharge_nothing_can_read_is_refused(self, extra):
        # muscadet's own CapacityContinuous refuses this by name on
        # ports="in". The importer builds plain ObjFlow components, so nothing
        # downstream enforces it: measured before this refusal, the model
        # imported clean, wired the port, and the declaration changed nothing
        # at any point of the run.
        payload = _accumulator_payload("Macc_inert", capacity_extra=extra)
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _build_kb_rule_sets(
                payload["kb"],
                _build_kb_lookup(payload["kb"]),
                _build_kb_capacities(payload["kb"], _build_kb_lookup(payload["kb"])),
            )
        message = str(excinfo.value)
        assert "hopper" in message
        assert next(iter(extra)) in message

    def test_a_rule_consuming_the_held_flow_is_a_way_out(self):
        # R48: both sides honour the ceiling, a hopper releasing into its rules
        # included. This must keep building.
        payload = _accumulator_payload(
            "Macc_rule",
            capacity_extra={"serve_rate": 40.0},
            outputs=["O2"],
            rule_sets=[
                {
                    "name": "burn",
                    "rules": [{"cons": {"H2": 1.0}, "prod": {"O2": 1.0}}],
                }
            ],
        )
        (cap,) = _capacities_of(payload)
        assert cap.serve_rate == 40.0

    def test_a_same_named_output_is_a_way_out(self):
        # The identity transfer (R31) carries the flow across, so a volume
        # declared on the input side of a pass-through does release.
        payload = _accumulator_payload(
            "Macc_passthrough",
            capacity_extra={"serve_rate": 40.0},
            outputs=["H2"],
        )
        (cap,) = _capacities_of(payload)
        assert cap.serve_rate == 40.0

    def test_an_undeclared_discharge_on_an_accumulator_is_left_alone(self):
        # A volume with no way out is a legitimate accumulator. It is the
        # DECLARATION that is refused, never the shape.
        payload = _accumulator_payload("Macc_plain", capacity_extra={})
        (cap,) = _capacities_of(payload)
        assert cap.serve_rate is None


# ---------------------------------------------------------------------------
# Parse layer — rule sets declared on the class template
# ---------------------------------------------------------------------------


class TestParseRuleSets:
    def test_single_unguarded_rule_carries_its_coefficients(self):
        payload = _template_payload(
            {"i": _cont_in("H2O"), "o": _cont_out("H2")},
            sys_name="Mrules_one",
            rule_sets=[
                {
                    "name": "transform",
                    "rules": [
                        {
                            "name": "electrolysis",
                            "cons": {"H2O": 4.0},
                            "prod": {"H2": 1.0},
                        }
                    ],
                }
            ],
        )
        (rule_set,) = _rule_sets_of(payload)
        assert rule_set.name == "transform"
        (rule,) = rule_set.rules
        assert rule.name == "electrolysis"
        assert rule.cons == {"H2O": 4.0}
        assert rule.prod == {"H2": 1.0}
        assert rule.cond == ()

    def test_guard_operands_are_parsed(self):
        payload = _template_payload(
            {
                "c": {"name": "ctrl", "port_type": {"general": "input"}},
                "i": _cont_in("H2O"),
                "o": _cont_out("H2"),
            },
            sys_name="Mrules_guard",
            rule_sets=[
                {
                    "name": "transform",
                    "rules": [
                        {
                            "name": "run",
                            "cond": [{"name": "ctrl", "port": "in"}],
                            "cons": {"H2O": 4.0},
                            "prod": {"H2": 1.0},
                        },
                        {"name": "idle", "prod": {"H2": 0.0}},
                    ],
                }
            ],
        )
        (rule_set,) = _rule_sets_of(payload)
        run, idle = rule_set.rules
        (operand,) = run.cond
        assert operand.name == "ctrl"
        assert operand.port == "in"
        assert operand.negate is False
        assert idle.cond == ()

    def test_numeric_guard_operand_is_parsed(self):
        payload = _template_payload(
            {"i": _cont_in("H2O"), "o": _cont_out("H2")},
            sys_name="Mrules_numeric",
            rule_sets=[
                {
                    "name": "transform",
                    "rules": [
                        {
                            "cond": [{"name": "H2O", "op": ">=", "value": 10.0}],
                            "prod": {"H2": 1.0},
                        },
                        {"prod": {"H2": 0.0}},
                    ],
                }
            ],
        )
        (rule_set,) = _rule_sets_of(payload)
        (operand,) = rule_set.rules[0].cond
        assert (operand.op, operand.value) == (">=", 10.0)

    def test_two_unguarded_rules_are_refused(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mrules_two_defaults",
            rule_sets=[
                {
                    "name": "transform",
                    "rules": [
                        {"name": "a", "prod": {"H2": 1.0}},
                        {"name": "b", "prod": {"H2": 2.0}},
                    ],
                }
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _rule_sets_of(payload)
        message = str(excinfo.value)
        assert "transform" in message
        assert "a" in message and "b" in message

    def test_rule_referencing_an_unknown_flow_is_refused(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mrules_unknown",
            rule_sets=[
                {"name": "transform", "rules": [{"cons": {"H2O": 4.0}}]},
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _rule_sets_of(payload)
        assert "H2O" in str(excinfo.value)

    def test_consumed_name_must_be_an_input(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mrules_cons_out",
            rule_sets=[{"name": "transform", "rules": [{"cons": {"H2": 1.0}}]}],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _rule_sets_of(payload)
        assert "H2" in str(excinfo.value)

    def test_produced_name_must_be_an_output(self):
        payload = _template_payload(
            {"i": _cont_in("H2O")},
            sys_name="Mrules_prod_in",
            rule_sets=[{"name": "transform", "rules": [{"prod": {"H2O": 1.0}}]}],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _rule_sets_of(payload)
        assert "H2O" in str(excinfo.value)

    def test_expression_string_guard_is_refused(self):
        payload = _template_payload(
            {
                "c": {"name": "ctrl", "port_type": {"general": "input"}},
                "o": _cont_out("H2"),
            },
            sys_name="Mrules_expr",
            rule_sets=[
                {
                    "name": "transform",
                    "rules": [{"cond": "ctrl", "prod": {"H2": 1.0}}],
                }
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _rule_sets_of(payload)
        assert "cond" in str(excinfo.value)

    def test_rule_naming_a_capacity_is_refused_as_such(self):
        payload = _template_payload(
            {"i": _cont_in("H2O"), "o": _cont_out("H2")},
            sys_name="Mrules_capname",
            capacities=[{"name": "cuve", "flows": "H2O", "volume": 10.0}],
            rule_sets=[{"name": "transform", "rules": [{"cons": {"cuve": 1.0}}]}],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _rule_sets_of(payload)
        message = str(excinfo.value)
        assert "cuve" in message
        assert "capacity" in message

    def test_duplicate_rule_set_name_is_refused(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mrules_dup",
            rule_sets=[
                {"name": "transform", "rules": [{"prod": {"H2": 1.0}}]},
                {"name": "transform", "rules": [{"prod": {"H2": 2.0}}]},
            ],
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _rule_sets_of(payload)
        assert "transform" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Parse layer — per-instance capacity overrides
# ---------------------------------------------------------------------------


def _two_instance_payload(attributes_1, attributes_2, *, capacities, sys_name):
    return _template_payload(
        {"o": _cont_out("H2")},
        sys_name=sys_name,
        capacities=capacities,
        components={
            "c1": {"name": "C1", "class_name": "Cls", "attributes": attributes_1},
            "c2": {"name": "C2", "class_name": "Cls", "attributes": attributes_2},
        },
    )


class TestInstanceCapacityOverrides:
    def test_two_components_of_one_class_carry_their_own_volume(self):
        payload = _two_instance_payload(
            [{"name": "tank", "role": CAPACITY_VOLUME_ROLE, "value": 12.0}],
            [{"name": "tank", "role": CAPACITY_VOLUME_ROLE, "value": 30.0}],
            capacities=[{"name": "tank", "flows": "H2", "volume": 6.0}],
            sys_name="Mov_two",
        )
        ctx = parse_platform_export(payload)
        volumes = {c.name: c.capacities[0].volume for c in ctx.components}
        assert volumes == {"C1": 12.0, "C2": 30.0}

    def test_a_component_without_override_keeps_the_template_volume(self):
        payload = _two_instance_payload(
            [{"name": "tank", "role": CAPACITY_VOLUME_ROLE, "value": 12.0}],
            [],
            capacities=[{"name": "tank", "flows": "H2", "volume": 6.0}],
            sys_name="Mov_bare",
        )
        ctx = parse_platform_export(payload)
        volumes = {c.name: c.capacities[0].volume for c in ctx.components}
        assert volumes == {"C1": 12.0, "C2": 6.0}

    def test_content_init_override_on_a_single_flow_capacity(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mov_content",
            capacities=[
                {
                    "name": "tank",
                    "flows": "H2",
                    "volume": 6.0,
                    "content_init": {"H2": 1.0},
                }
            ],
            components={
                "c1": {
                    "name": "C1",
                    "class_name": "Cls",
                    "attributes": [
                        {
                            "name": "tank",
                            "role": CAPACITY_CONTENT_INIT_ROLE,
                            "value": 4.0,
                        }
                    ],
                }
            },
        )
        ctx = parse_platform_export(payload)
        assert ctx.components[0].capacities[0].content_init == {"H2": 4.0}

    def test_content_init_override_on_a_multi_flow_capacity_is_refused(self):
        payload = _template_payload(
            {"a": _cont_out("H2"), "b": _cont_out("O2")},
            sys_name="Mov_content_multi",
            capacities=[
                {"name": "tank", "flows": ["H2", "O2"], "volume": 6.0},
            ],
            components={
                "c1": {
                    "name": "C1",
                    "class_name": "Cls",
                    "attributes": [
                        {
                            "name": "tank",
                            "role": CAPACITY_CONTENT_INIT_ROLE,
                            "value": 4.0,
                        }
                    ],
                }
            },
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            parse_platform_export(payload)
        message = str(excinfo.value)
        assert "tank" in message
        assert "single-flow" in message.lower()

    def test_override_naming_an_unknown_capacity_is_refused(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mov_unknown",
            capacities=[{"name": "tank", "flows": "H2", "volume": 6.0}],
            components={
                "c1": {
                    "name": "C1",
                    "class_name": "Cls",
                    "attributes": [
                        {"name": "cuve", "role": CAPACITY_VOLUME_ROLE, "value": 4.0}
                    ],
                }
            },
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            parse_platform_export(payload)
        message = str(excinfo.value)
        assert "cuve" in message
        assert "tank" in message

    def test_fill_rate_is_explicitly_not_overridable(self):
        # The plan freezes the fill rate at the template. An unregistered role
        # would be logged and dropped, so the refusal has to be explicit.
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mov_fill",
            capacities=[{"name": "tank", "flows": "H2", "volume": 6.0}],
            components={
                "c1": {
                    "name": "C1",
                    "class_name": "Cls",
                    "attributes": [
                        {"name": "tank", "role": CAPACITY_FILL_RATE_ROLE, "value": 1.0}
                    ],
                }
            },
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            parse_platform_export(payload)
        assert "fill" in str(excinfo.value).lower()

    def test_capacity_roles_are_registered_and_never_silently_dropped(self, caplog):
        # A role absent from the registry is warned about and ignored, so the
        # override would vanish without an error. This pins the registration.
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mov_registered",
            capacities=[{"name": "tank", "flows": "H2", "volume": 6.0}],
            components={
                "c1": {
                    "name": "C1",
                    "class_name": "Cls",
                    "attributes": [
                        {"name": "tank", "role": CAPACITY_VOLUME_ROLE, "value": 9.0}
                    ],
                }
            },
        )
        with caplog.at_level(logging.WARNING):
            ctx = parse_platform_export(payload)
        assert ctx.components[0].capacities[0].volume == 9.0
        assert "Unknown attribute role" not in caplog.text

    def test_non_numeric_volume_override_is_refused(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mov_badvalue",
            capacities=[{"name": "tank", "flows": "H2", "volume": 6.0}],
            components={
                "c1": {
                    "name": "C1",
                    "class_name": "Cls",
                    "attributes": [
                        {"name": "tank", "role": CAPACITY_VOLUME_ROLE, "value": True}
                    ],
                }
            },
        )
        with pytest.raises(Cod3sPlatformImportError):
            parse_platform_export(payload)


# ---------------------------------------------------------------------------
# Parse layer — derating pairs on the model component
# ---------------------------------------------------------------------------


def _derating_payload(deratings, sys_name, interfaces=None):
    payload = _template_payload(
        interfaces or {"o": _cont_out("H2"), "p": _cont_out("O2")},
        sys_name=sys_name,
    )
    payload["model"]["elements"]["components"]["c1"]["deratings"] = deratings
    return payload


class TestParseDeratings:
    def test_pairs_reach_the_component_spec(self):
        ctx = parse_platform_export(
            _derating_payload(
                [{"mode": "df_H2", "flow": "H2"}, {"mode": "df_H2", "flow": "O2"}],
                sys_name="Mder_ok",
            )
        )
        assert [(d.mode, d.flow) for d in ctx.components[0].deratings] == [
            ("df_H2", "H2"),
            ("df_H2", "O2"),
        ]

    def test_targeting_a_continuous_input_is_refused_and_named(self):
        payload = _derating_payload(
            [{"mode": "df", "flow": "water"}],
            sys_name="Mder_in",
            interfaces={"i": _cont_in("water"), "o": _cont_out("H2")},
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            parse_platform_export(payload)
        message = str(excinfo.value)
        assert "water" in message
        assert "output" in message

    def test_targeting_a_discrete_output_is_refused_and_named(self):
        payload = _derating_payload(
            [{"mode": "df", "flow": "signal"}],
            sys_name="Mder_discrete",
            interfaces={
                "o": _cont_out("H2"),
                "d": {"name": "signal", "port_type": {"general": "output"}},
            },
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            parse_platform_export(payload)
        message = str(excinfo.value)
        assert "signal" in message
        assert "continuous" in message

    def test_targeting_an_unknown_flow_is_refused(self):
        payload = _derating_payload(
            [{"mode": "df", "flow": "nope"}], sys_name="Mder_unknown"
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            parse_platform_export(payload)
        assert "nope" in str(excinfo.value)

    def test_entry_without_a_mode_is_refused(self):
        payload = _derating_payload([{"flow": "H2"}], sys_name="Mder_nomode")
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            parse_platform_export(payload)
        assert "mode" in str(excinfo.value)

    def test_duplicate_pair_is_collapsed(self):
        # register_derating is idempotent; the spec follows so that a payload
        # naming the same pair twice does not read as two variables.
        ctx = parse_platform_export(
            _derating_payload(
                [{"mode": "df", "flow": "H2"}, {"mode": "df", "flow": "H2"}],
                sys_name="Mder_dup",
            )
        )
        assert [(d.mode, d.flow) for d in ctx.components[0].deratings] == [("df", "H2")]


# ---------------------------------------------------------------------------
# Runtime layer — the sections are built in the library's own order
# ---------------------------------------------------------------------------


class TestRuntimeCapacitiesRulesAndDeratings:
    def test_capacity_is_built_on_the_component(self, cleanup_system):
        system = system_from_export(
            _template_payload(
                {"i": _cont_in("H2"), "o": _cont_out("H2")},
                sys_name="Rcap_build",
                capacities=[
                    {
                        "name": "tank",
                        "flows": [{"name": "H2", "weight": 2.0}],
                        "volume": 6.0,
                        "side": "out",
                        "content_init": {"H2": 3.0},
                        "fill_rate": 1.0,
                    }
                ],
            )
        )
        cleanup_system.append(system)
        capacity = system.comp["C1"].capacities["tank"]
        assert capacity.capacity == 6.0
        assert capacity.side == "out"
        assert capacity.content_init == {"H2": 3.0}
        assert capacity.fill_rate == 1.0
        assert [(f.name, f.weight) for f in capacity.flows] == [("H2", 2.0)]

    def test_a_commanded_discharge_reaches_the_capacity(self, cleanup_system):
        # #167: the platform declares the ceiling and the command, and the
        # engine honours both. The verdict is read LIVE through
        # ``serve_ceiling``, so an unfed command answers zero rather than the
        # ceiling -- which is the whole point: the wire is not decorative.
        system = system_from_export(
            _template_payload(
                {
                    "c": {"name": "cmd", "port_type": {"general": "input"}},
                    "i": _cont_in("H2"),
                    "o": _cont_out("H2"),
                },
                sys_name="Rcap_serve",
                capacities=[
                    {
                        "name": "battery",
                        "flows": "H2",
                        "volume": 60.0,
                        "side": "out",
                        "content_init": {"H2": 30.0},
                        "serve_rate": 40.0,
                        "serve_cond": [["cmd"]],
                    }
                ],
            )
        )
        cleanup_system.append(system)
        comp = system.comp["C1"]
        capacity = comp.capacities["battery"]

        assert capacity.serve_rate == 40.0
        assert capacity.var_serve_rate["H2"].value() == 40.0
        # Stored RESOLVED: the operand name is replaced by the flow object.
        ((operand,),) = capacity.serve_cond
        assert operand is comp.flows_in["cmd"]

        assert capacity.serve_ceiling("H2") == 0.0
        comp.flows_in["cmd"].var_fed.setValue(True)
        assert capacity.serve_ceiling("H2") == 40.0

    def test_an_undeclared_command_leaves_the_capacity_uncommanded(
        self, cleanup_system
    ):
        system = system_from_export(
            _template_payload(
                {"i": _cont_in("H2"), "o": _cont_out("H2")},
                sys_name="Rcap_serve_none",
                capacities=[
                    {"name": "tank", "flows": "H2", "volume": 6.0, "side": "out"}
                ],
            )
        )
        cleanup_system.append(system)
        capacity = system.comp["C1"].capacities["tank"]
        assert capacity.serve_cond == []
        assert capacity.serve_ceiling("H2") == float("inf")

    def test_rule_set_is_built_with_its_coefficients(self, cleanup_system):
        system = system_from_export(
            _template_payload(
                {
                    "a": _cont_in("H2O"),
                    "b": _cont_in("Elec"),
                    "c": _cont_out("H2"),
                    "d": _cont_out("O2"),
                },
                sys_name="Rrules_build",
                rule_sets=[
                    {
                        "name": "transform",
                        "rules": [
                            {
                                "name": "electrolysis",
                                "cons": {"H2O": 4.0, "Elec": 1.0},
                                "prod": {"H2": 1.0, "O2": 1.0},
                            }
                        ],
                    }
                ],
            )
        )
        cleanup_system.append(system)
        rule_set = system.comp["C1"].rule_sets["transform"]
        (rule,) = rule_set.rules
        assert rule.name == "electrolysis"
        assert rule.cons == {"H2O": 4.0, "Elec": 1.0}
        assert rule.prod == {"H2": 1.0, "O2": 1.0}

    def test_a_guarded_rule_set_compiles_its_mode(self, cleanup_system):
        system = system_from_export(
            _template_payload(
                {
                    "c": {"name": "ctrl", "port_type": {"general": "input"}},
                    "o": _cont_out("H2"),
                },
                sys_name="Rrules_guard",
                rule_sets=[
                    {
                        "name": "gate",
                        "rules": [
                            {
                                "name": "on",
                                "cond": [{"name": "ctrl"}],
                                "prod": {"H2": 2.0},
                            },
                            {"name": "off", "prod": {"H2": 0.0}},
                        ],
                    }
                ],
            )
        )
        cleanup_system.append(system)
        rule_set = system.comp["C1"].rule_sets["gate"]
        assert rule_set.mode is not None

    def test_declaration_order_is_read_from_the_library(self):
        from muscadet.declare import DECLARATION_SECTIONS

        order = [section for section, _ in DECLARATION_SECTIONS]
        assert order.index("flows") < order.index("capacities")
        assert order.index("capacities") < order.index("rules")

    def test_a_rule_consuming_a_buffered_flow_builds(self, cleanup_system):
        # Only reachable when the capacity is declared BEFORE the rule set: the
        # rule's counterparty substitution rests on the capacity index.
        system = system_from_export(
            _template_payload(
                {"i": _cont_in("H2O"), "o": _cont_out("H2")},
                sys_name="Rorder_build",
                capacities=[{"name": "cuve", "flows": "H2O", "volume": 10.0}],
                rule_sets=[
                    {
                        "name": "transform",
                        "rules": [{"cons": {"H2O": 2.0}, "prod": {"H2": 1.0}}],
                    }
                ],
            )
        )
        cleanup_system.append(system)
        comp = system.comp["C1"]
        assert comp.get_capacity_of_flow("H2O", "in") is comp.capacities["cuve"]
        assert comp.rule_sets["transform"].rules[0].cons == {"H2O": 2.0}

    def test_derating_variable_is_allocated_at_the_nominal_rate(self, cleanup_system):
        from muscadet.flow_continuous import NOMINAL_RATE

        system = system_from_export(
            _derating_payload(
                [{"mode": "df_H2", "flow": "H2"}, {"mode": "df_H2", "flow": "O2"}],
                sys_name="Rder_alloc",
            )
        )
        cleanup_system.append(system)
        comp = system.comp["C1"]
        for flow_name in ("H2", "O2"):
            flow = comp.flows_out[flow_name]
            assert "df_H2" in flow.derating
            assert flow.derating["df_H2"].basename() == f"df_H2_derating_{flow_name}"
            assert flow.derating["df_H2"].value() == NOMINAL_RATE
            assert flow.get_effective_rate() == NOMINAL_RATE

    def test_preallocation_is_idempotent_with_a_declared_failure_mode(
        self, cleanup_system
    ):
        system = system_from_export(
            _derating_payload([{"mode": "df_H2", "flow": "H2"}], sys_name="Rder_idem")
        )
        cleanup_system.append(system)
        comp = system.comp["C1"]
        before = comp.flows_out["H2"].derating["df_H2"]
        comp.add_delay_failure_mode(
            name="df_H2",
            failure_time=1.0,
            repair_time=1.0,
            failure_effects=[("H2", 0.0)],
        )
        assert comp.flows_out["H2"].derating["df_H2"] is before

    def test_capacity_override_reaches_the_built_component(self, cleanup_system):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Rov_build",
            capacities=[{"name": "tank", "flows": "H2", "volume": 6.0}],
            components={
                "c1": {
                    "name": "C1",
                    "class_name": "Cls",
                    "attributes": [
                        {"name": "tank", "role": CAPACITY_VOLUME_ROLE, "value": 42.0}
                    ],
                },
                "c2": {"name": "C2", "class_name": "Cls", "attributes": []},
            },
        )
        system = system_from_export(payload)
        cleanup_system.append(system)
        assert system.comp["C1"].capacities["tank"].capacity == 42.0
        assert system.comp["C2"].capacities["tank"].capacity == 6.0


# ---------------------------------------------------------------------------
# Refusals reaching the class template as a whole
# ---------------------------------------------------------------------------


class TestTemplateSectionShapes:
    def test_capacities_must_be_a_list(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mshape_cap",
            capacities={"name": "tank", "flows": "H2", "volume": 6.0},
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _capacities_of(payload)
        assert "capacities" in str(excinfo.value)

    def test_rule_sets_must_be_a_list(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mshape_rules",
            rule_sets={"name": "transform", "rules": []},
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _rule_sets_of(payload)
        assert "rule_sets" in str(excinfo.value)

    def test_a_logic_gate_template_carries_neither_section(self):
        payload = _template_payload(
            {"o": _cont_out("H2")},
            sys_name="Mshape_gate",
            capacities=[{"name": "tank", "flows": "H2", "volume": 6.0}],
        )
        payload["kb"]["component_templates"]["Cls"]["metadata"] = {"logic_gate": "or"}
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            parse_platform_export(payload)
        assert "logic gate" in str(excinfo.value).lower()

    def test_a_discrete_only_template_is_untouched(self):
        payload = _template_payload(
            {
                "i": {"name": "a", "port_type": {"general": "input"}},
                "o": {
                    "name": "b",
                    "port_type": {"general": "output"},
                    "prod_cond": [["a"]],
                },
            },
            sys_name="Mshape_discrete",
        )
        ctx = parse_platform_export(payload)
        component = ctx.components[0]
        assert component.capacities == ()
        assert component.rule_sets == ()
        assert component.deratings == ()
