"""The reference declaration of time profiles, shared with a second engine.

A second engine reads the system declaration muscadet writes
(:func:`muscadet.declare.system_spec`), whose profile keys are the names of the
profile constructors' parameters. This module builds, from a COD3S Platform
export, a small system holding every case that contract covers, and compares
its declaration with ``tests/fixtures/profile_declarations_reference.json``:

- a square (``TrapezoidProfile``) on an output;
- a drawn shape (``PiecewiseLinearProfile``) on an output;
- a sinusoid carrying smoothed noise on an output, its ``noise_key`` derived
  from the component and the flow;
- a profile on a pure consumer's continuous input;
- two components of one class, one carrying its own profile.

The fixture is the contract: the test fails when the declaration drifts from
it. A deliberate change regenerates it with ``MUSCADET_UPDATE_FIXTURES=1``, and
the regenerated file is then copied as is into the second engine's tests.
"""

import json
import os
from pathlib import Path

import cod3s

from muscadet.declare import system_spec
from muscadet.importers.cod3s_platform import system_from_export

FIXTURE = Path(__file__).parent / "fixtures" / "profile_declarations_reference.json"

#: Set to regenerate the fixture rather than compare with it.
UPDATE_ENV = "MUSCADET_UPDATE_FIXTURES"


def _out(name, profile):
    return {
        "name": name,
        "port_type": {"general": "output"},
        "flow_family": "continuous",
        "production_profile": profile,
    }


def _in(name, profile):
    return {
        "name": name,
        "port_type": {"general": "input"},
        "flow_family": "continuous",
        "demand_profile": profile,
    }


def _component(name, class_name, attributes=()):
    return {"name": name, "class_name": class_name, "attributes": list(attributes)}


def reference_payload():
    """The platform export of the reference system."""
    sine = {
        "cls": "sinusoidal",
        "value": 4.0,
        "amplitude": 0.5,
        "period": 24.0,
        "offset": 1.0,
    }
    square = {
        "cls": "square",
        "value": 3.0,
        "low": 0.2,
        "high": 1.5,
        "period": 24.0,
        "high_duration": 11.0,
        "ramp_duration": 0.5,
        "phase_shift": 6.0,
    }
    drawn = {
        "cls": "drawn",
        "value": 5.0,
        "points": [[0.0, 0.5], [8.0, 1.2], [16.0, 0.8]],
        "period": 24.0,
        "value_max": 1.5,
    }
    noisy_sine = {
        "cls": "sinusoidal",
        "value": 2.0,
        "amplitude": 0.3,
        "period": 12.0,
        "offset": 1.0,
        "value_min": 0.0,
        "noise_amplitude": 0.1,
        "noise_time_scale": 2.0,
    }
    load = {
        "cls": "sinusoidal",
        "value": 1.5,
        "amplitude": 0.4,
        "period": 24.0,
        "offset": 1.0,
        "phase_shift": -3.0,
    }
    return {
        "model": {
            "name": "ProfileReference",
            "kb": {"name": "ProfileKB", "version": "1.0.0"},
            "elements": {
                "components": {
                    "id-g1": _component("G1", "Gen"),
                    "id-g2": _component(
                        "G2",
                        "Gen",
                        [{"name": "P", "role": "production_profile", "value": square}],
                    ),
                    "id-d1": _component("D1", "Drawn"),
                    "id-n1": _component("N1", "Noisy"),
                    "id-l1": _component("L1", "Load"),
                },
                "connections": {
                    "c1": {
                        "component_source": "id-g1",
                        "component_target": "id-l1",
                        "interface_source": "P",
                        "interface_target": "P",
                    },
                },
            },
        },
        "kb": {
            "component_templates": {
                "Gen": {"interfaces": {"P__output": _out("P", sine)}},
                "Drawn": {"interfaces": {"P__output": _out("P", drawn)}},
                "Noisy": {"interfaces": {"P__output": _out("P", noisy_sine)}},
                "Load": {"interfaces": {"P__input": _in("P", load)}},
            },
        },
    }


def produced_declaration():
    """The declaration muscadet writes for the reference system, as JSON."""
    system = system_from_export(reference_payload())
    try:
        return json.loads(json.dumps(system_spec(system)))
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_the_declaration_matches_the_reference():
    produced = produced_declaration()

    if os.environ.get(UPDATE_ENV) == "1":
        FIXTURE.write_text(json.dumps(produced, indent=2, sort_keys=True) + "\n")

    assert FIXTURE.exists(), f"{FIXTURE} is missing; regenerate it with {UPDATE_ENV}=1"
    reference = json.loads(FIXTURE.read_text())
    assert produced == reference, (
        "the profile declaration drifted from the reference shared with the "
        f"second engine; if the change is deliberate, regenerate it with "
        f"{UPDATE_ENV}=1 and copy it into that engine's tests"
    )


def test_the_reference_covers_every_case():
    reference = json.loads(FIXTURE.read_text())
    components = reference["components"]

    def profile(component, cls):
        (flow,) = [f for f in components[component]["flows"] if f["cls"] == cls]
        return flow["profile"]

    assert profile("G1", "FlowContinuousOut")["cls"] == "SinusoidalProfile"
    assert profile("G2", "FlowContinuousOut")["cls"] == "TrapezoidProfile"
    assert profile("D1", "FlowContinuousOut")["cls"] == "PiecewiseLinearProfile"
    noisy = profile("N1", "FlowContinuousOut")
    assert noisy["noise_amplitude"] == 0.1
    assert isinstance(noisy["noise_key"], int)
    assert profile("L1", "FlowContinuousIn")["cls"] == "SinusoidalProfile"
    # A noise-free profile declares no noise parameter at all.
    assert "noise_key" not in profile("G1", "FlowContinuousOut")
