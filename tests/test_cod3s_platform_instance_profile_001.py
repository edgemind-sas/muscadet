"""Time profiles carried by a COD3S Platform export, down to the engine.

The platform writes a continuous port's quantity as a profile ``{"cls": <shape>,
"value": <nominal rate>, ...parameters}``, in two places:

- on the class interface (``production_profile`` on an output,
  ``demand_profile`` on an input), the default every component of the class
  takes;
- in a model component's ``attributes``, under the same two roles, when that
  component's profile differs from its class's. The per-component profile
  REPLACES the whole profile of that flow on that component -- shape, nominal
  rate and modifiers -- and the other components keep the class one.

The shapes the importer reads: ``constant`` (no profile object, the value is
the rate), ``sinusoidal``, ``square`` (a ``TrapezoidProfile``) and ``drawn`` (a
``PiecewiseLinearProfile``), every modulated one taking the clamp and the noise
modifiers. A modulated profile on an input is accepted by the importer; whether
that input is a pure consumer is the engine's question, settled at pre-run.
"""

import cod3s
import pytest

from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    _parse_interface,
    parse_platform_export,
    system_from_export,
)
from muscadet.profile import (
    PiecewiseLinearProfile,
    SinusoidalProfile,
    TrapezoidProfile,
    flow_noise_key,
)

# ---------------------------------------------------------------------------
# Shapes, as the platform writes them
# ---------------------------------------------------------------------------

#: A daily sinusoid around 1, the class default of the generators.
CLASS_SINE = {
    "cls": "sinusoidal",
    "value": 4.0,
    "amplitude": 0.5,
    "period": 24.0,
    "offset": 1.0,
}

#: A square at 0.2 / 1.5, high from 6 h to 18 h with half-hour ramps.
SQUARE = {
    "cls": "square",
    "value": 3.0,
    "low": 0.2,
    "high": 1.5,
    "period": 24.0,
    "high_duration": 11.0,
    "ramp_duration": 0.5,
    "phase_shift": 6.0,
}

#: A drawn day of three points, with smoothed noise and a clamp.
DRAWN_NOISY = {
    "cls": "drawn",
    "value": 5.0,
    "points": [[0.0, 0.5], [8.0, 1.2], [16.0, 0.8]],
    "period": 24.0,
    "value_min": 0.0,
    "value_max": 1.5,
    "noise_amplitude": 0.1,
    "noise_time_scale": 2.0,
}

#: The per-component curve of a consumer whose class demand is constant.
WINTER_SINE = {
    "cls": "sinusoidal",
    "value": 2.0,
    "amplitude": 0.4,
    "period": 24.0,
    "offset": 1.0,
    "phase_shift": -3.0,
}

#: Instants sampled by the Monte-Carlo runs below.
SCHEDULE = [0.0, 3.0, 7.0, 12.0, 19.0, 23.0]


def _out(name, profile=None):
    interface = {
        "name": name,
        "port_type": {"general": "output"},
        "flow_family": "continuous",
    }
    if profile is not None:
        interface["production_profile"] = profile
    return interface


def _in(name, profile=None):
    interface = {
        "name": name,
        "port_type": {"general": "input"},
        "flow_family": "continuous",
    }
    if profile is not None:
        interface["demand_profile"] = profile
    return interface


def _component(name, class_name, attributes=()):
    return {"name": name, "class_name": class_name, "attributes": list(attributes)}


def _connection(source, target, flow):
    return {
        "component_source": source,
        "component_target": target,
        "interface_source": flow,
        "interface_target": flow,
    }


def _payload(sys_name, templates, components, connections=None):
    return {
        "model": {
            "name": sys_name,
            "kb": {"name": "KB", "version": "1.0.0"},
            "elements": {
                "components": components,
                "connections": connections or {},
            },
        },
        "kb": {"component_templates": templates},
    }


def _profile_override(flow, role, value):
    return {"name": flow, "role": role, "value": value}


@pytest.fixture
def cleanup_system():
    systems: list = []
    yield systems
    for system in systems:
        system.deleteSys()
    cod3s.terminate_session()


def indicator_rows(system):
    return {
        name: {
            (row["stat"], float(row["instant"])): float(row["values"])
            for _, row in indic.values.iterrows()
        }
        for name, indic in system.indicators.items()
    }


# ---------------------------------------------------------------------------
# Capability marker
# ---------------------------------------------------------------------------


def test_the_instance_profile_marker_is_declared():
    # The platform refuses per-component profiles until it reads this marker:
    # an importer without the channel logs the role as unknown and simulates
    # the class profile, in silence.
    from muscadet.importers import cod3s_platform

    assert cod3s_platform._SUPPORTS_INSTANCE_FLOW_PROFILE_OVERRIDE is True


# ---------------------------------------------------------------------------
# Parse layer: the new shapes and their modifiers
# ---------------------------------------------------------------------------


class TestParseShapes:
    def test_square_maps_to_a_trapezoid(self):
        fs = _parse_interface(_out("P", SQUARE))
        assert fs.nominal_rate == 3.0
        assert fs.profile_spec == {
            "cls": "TrapezoidProfile",
            "low": 0.2,
            "high": 1.5,
            "period": 24.0,
            "high_duration": 11.0,
            "ramp_duration": 0.5,
            "phase_shift": 6.0,
        }

    def test_drawn_maps_to_a_piecewise_linear_shape_with_its_modifiers(self):
        fs = _parse_interface(_out("P", DRAWN_NOISY))
        assert fs.nominal_rate == 5.0
        assert fs.profile_spec == {
            "cls": "PiecewiseLinearProfile",
            "points": [[0.0, 0.5], [8.0, 1.2], [16.0, 0.8]],
            "period": 24.0,
            "value_min": 0.0,
            "value_max": 1.5,
            "noise_amplitude": 0.1,
            "noise_time_scale": 2.0,
        }

    def test_noise_on_a_sinusoid_is_carried(self):
        fs = _parse_interface(
            _out("P", {**CLASS_SINE, "noise_amplitude": 0.2, "noise_time_scale": 3.0})
        )
        assert fs.profile_spec["noise_amplitude"] == 0.2
        assert fs.profile_spec["noise_time_scale"] == 3.0

    @pytest.mark.parametrize(
        "shape, foreign",
        [
            (DRAWN_NOISY, "low"),
            (SQUARE, "points"),
            (SQUARE, "amplitude"),
            (CLASS_SINE, "high_duration"),
        ],
    )
    def test_a_parameter_of_another_shape_is_refused_by_name(self, shape, foreign):
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _parse_interface(_out("P", {**shape, foreign: 1.0}))
        message = str(excinfo.value)
        assert repr(foreign) in message
        assert repr(shape["cls"]) in message

    def test_the_noise_key_is_not_a_platform_parameter(self):
        # muscadet derives it from the component and the flow; a key sent by
        # the platform would pin every instance of the class to one noise.
        with pytest.raises(Cod3sPlatformImportError, match="noise_key"):
            _parse_interface(_out("P", {**CLASS_SINE, "noise_key": 7}))

    def test_a_template_name_is_refused_as_an_unknown_key(self):
        with pytest.raises(Cod3sPlatformImportError, match="template_name"):
            _parse_interface(_out("P", {**CLASS_SINE, "template_name": "Day"}))

    def test_a_modulated_demand_is_carried_on_an_input(self):
        fs = _parse_interface(_in("P", WINTER_SINE))
        assert fs.nominal_rate == 2.0
        assert fs.profile_spec == {
            "cls": "SinusoidalProfile",
            "amplitude": 0.4,
            "period": 24.0,
            "offset": 1.0,
            "phase_shift": -3.0,
        }


# ---------------------------------------------------------------------------
# Parse layer: a per-component profile
# ---------------------------------------------------------------------------


def _generators_payload(sys_name, g2_attributes, l1_attributes=()):
    return _payload(
        sys_name,
        templates={
            "Gen": {"interfaces": {"P__output": _out("P", CLASS_SINE)}},
            "Load": {
                "interfaces": {"P__input": _in("P", {"cls": "constant", "value": 2.0})}
            },
        },
        components={
            "id-g1": _component("G1", "Gen"),
            "id-g2": _component("G2", "Gen", g2_attributes),
            "id-l1": _component("L1", "Load", l1_attributes),
        },
        connections={"c1": _connection("id-g1", "id-l1", "P")},
    )


def _flow(parsed, component, name, direction):
    comp = next(c for c in parsed.components if c.name == component)
    return next(f for f in comp.flows if f.name == name and f.direction == direction)


class TestParseInstanceProfiles:
    def test_the_overridden_component_carries_its_own_profile(self):
        parsed = parse_platform_export(
            _generators_payload(
                "MIp_parse",
                [_profile_override("P", "production_profile", SQUARE)],
            )
        )
        g1 = _flow(parsed, "G1", "P", "output")
        g2 = _flow(parsed, "G2", "P", "output")
        assert g1.nominal_rate == 4.0
        assert g1.profile_spec["cls"] == "SinusoidalProfile"
        assert g2.nominal_rate == 3.0
        assert g2.profile_spec["cls"] == "TrapezoidProfile"

    def test_a_constant_override_drops_the_class_modulation(self):
        parsed = parse_platform_export(
            _generators_payload(
                "MIp_const",
                [
                    _profile_override(
                        "P", "production_profile", {"cls": "constant", "value": 9.0}
                    )
                ],
            )
        )
        g2 = _flow(parsed, "G2", "P", "output")
        assert g2.nominal_rate == 9.0
        assert g2.profile_spec is None

    def test_a_demand_override_is_read_on_the_input(self):
        parsed = parse_platform_export(
            _generators_payload(
                "MIp_demand",
                [],
                [_profile_override("P", "demand_profile", WINTER_SINE)],
            )
        )
        l1 = _flow(parsed, "L1", "P", "input")
        assert l1.nominal_rate == 2.0
        assert l1.profile_spec["cls"] == "SinusoidalProfile"

    def test_a_malformed_override_is_refused_naming_the_component(self):
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            parse_platform_export(
                _generators_payload(
                    "MIp_bad",
                    [
                        _profile_override(
                            "P", "production_profile", {**SQUARE, "points": []}
                        )
                    ],
                )
            )
        message = str(excinfo.value)
        assert "G2" in message
        assert "'points'" in message

    def test_a_production_profile_on_an_input_is_refused(self):
        # The role says which side it targets; the only flow named P on the
        # load is an input.
        with pytest.raises(Cod3sPlatformImportError, match="production_profile"):
            parse_platform_export(
                _generators_payload(
                    "MIp_side",
                    [],
                    [_profile_override("P", "production_profile", SQUARE)],
                )
            )

    def test_a_profile_override_on_a_discrete_flow_is_refused(self):
        payload = _payload(
            "MIp_discrete",
            templates={
                "D": {
                    "interfaces": {
                        "s__output": {"name": "s", "port_type": {"general": "output"}}
                    }
                }
            },
            components={
                "id-d": _component(
                    "D1", "D", [_profile_override("s", "production_profile", SQUARE)]
                )
            },
        )
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            parse_platform_export(payload)
        message = str(excinfo.value)
        assert "production_profile" in message
        assert "discrete" in message

    def test_any_other_override_on_a_continuous_flow_stays_refused(self):
        with pytest.raises(Cod3sPlatformImportError, match="continuous"):
            parse_platform_export(
                _generators_payload(
                    "MIp_other", [_profile_override("P", "prod_init", True)]
                )
            )


# ---------------------------------------------------------------------------
# Runtime: what each component simulates
# ---------------------------------------------------------------------------


def test_two_components_of_one_class_simulate_their_own_profiles(cleanup_system):
    system = system_from_export(
        _generators_payload(
            "MIp_run",
            [_profile_override("P", "production_profile", SQUARE)],
            [_profile_override("P", "demand_profile", WINTER_SINE)],
        )
    )
    cleanup_system.append(system)

    g1 = system.comp["G1"].flows_out["P"]
    g2 = system.comp["G2"].flows_out["P"]
    l1 = system.comp["L1"].flows_in["P"]
    assert isinstance(g1.profile, SinusoidalProfile)
    assert isinstance(g2.profile, TrapezoidProfile)
    assert isinstance(l1.profile, SinusoidalProfile)

    for comp in ("G1", "G2"):
        system.add_indicator_var(component=comp, var="^P_out_profile$", stats=["mean"])
    system.add_indicator_var(
        component="L1", var="^P_(demand_out|in_profile)$", stats=["mean"]
    )
    system.simulate({"nb_runs": 1, "schedule": SCHEDULE})
    rows = indicator_rows(system)

    class_sine = SinusoidalProfile(amplitude=0.5, period=24.0, offset=1.0)
    square = TrapezoidProfile(
        low=0.2,
        high=1.5,
        period=24.0,
        high_duration=11.0,
        ramp_duration=0.5,
        phase_shift=6.0,
    )
    winter = SinusoidalProfile(amplitude=0.4, period=24.0, offset=1.0, phase_shift=-3.0)
    for instant in SCHEDULE:
        assert rows["G1_P_out_profile"][("mean", instant)] == pytest.approx(
            class_sine.factor(instant), abs=1e-5
        )
        assert rows["G2_P_out_profile"][("mean", instant)] == pytest.approx(
            square.factor(instant), abs=1e-5
        )
        assert rows["L1_P_in_profile"][("mean", instant)] == pytest.approx(
            winter.factor(instant), abs=1e-5
        )
        assert rows["L1_P_demand_out"][("mean", instant)] == pytest.approx(
            2.0 * winter.factor(instant), abs=1e-5
        )

    # The square actually differs from the class sine at these instants.
    assert any(abs(square.factor(t) - class_sine.factor(t)) > 0.1 for t in SCHEDULE)


def test_a_drawn_noisy_production_profile_is_simulated(cleanup_system):
    payload = _payload(
        "MIp_drawn",
        templates={"Gen": {"interfaces": {"P__output": _out("P", DRAWN_NOISY)}}},
        components={"id-g": _component("G", "Gen")},
    )
    system = system_from_export(payload)
    cleanup_system.append(system)

    flow = system.comp["G"].flows_out["P"]
    assert flow.var_fed_default == 5.0
    assert isinstance(flow.profile, PiecewiseLinearProfile)
    assert flow.profile.noise_amplitude == 0.1
    assert flow.profile.noise_time_scale == 2.0
    # The flow key is muscadet's, never the platform's.
    assert flow.profile.noise_key == flow_noise_key("G", "P")

    system.add_indicator_var(component="G", var="^P_out_profile$", stats=["mean"])
    system.simulate({"nb_runs": 1, "schedule": SCHEDULE})
    rows = indicator_rows(system)["G_P_out_profile"]

    noise_free = PiecewiseLinearProfile(
        points=[[0.0, 0.5], [8.0, 1.2], [16.0, 0.8]], period=24.0
    )
    # The first noise node is zero: instant 0 is the drawn shape itself.
    assert rows[("mean", 0.0)] == pytest.approx(noise_free.factor(0.0), abs=1e-6)
    values = [rows[("mean", t)] for t in SCHEDULE]
    assert all(0.0 <= v <= 1.5 for v in values)
    assert any(
        abs(rows[("mean", t)] - noise_free.factor(t)) > 1e-6 for t in SCHEDULE[1:]
    )
