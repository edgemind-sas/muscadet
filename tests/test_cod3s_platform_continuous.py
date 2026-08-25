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
    def test_production_condition_refused_by_the_importer(self):
        # NOT by FlowContinuous.check_declaration_keys: the refusal must name
        # the platform key, at parse time, before any runtime is touched.
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _parse_interface(
                {
                    "name": "power",
                    "port_type": {"general": "output"},
                    "flow_family": "continuous",
                    "prod_cond": [["ctrl"]],
                }
            )
        message = str(excinfo.value)
        assert "prod_cond" in message
        assert "continuous" in message

    @pytest.mark.parametrize(
        "key, value",
        [
            ("negate", True),
            ("logic_inner_mode", "or"),
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
# Runtime layer — a continuous flow never receives a production condition
# ---------------------------------------------------------------------------


class TestContinuousKwargsAreSeparate:
    def test_continuous_flow_carries_no_discrete_declaration_key(self, cleanup_system):
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
        for key in ("var_prod_cond", "var_prod_cond_inner_mode", "negate"):
            assert not hasattr(
                flow, key
            ), f"{key} reached a continuous flow; the kwargs dicts are shared"


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
