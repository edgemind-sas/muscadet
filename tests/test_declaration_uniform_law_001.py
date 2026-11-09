"""The uniform occurrence law in a declaration document.

cod3s 1.24.0 adds ``{"cls": "uniform", "low": ..., "high": ...}``, the first
law with TWO parameters per common-cause order. Its parameter vector carries
one ``(low, high)`` pair per order, so the two writings of the law (the typed
law and the vector, cf. ``test_declaration_law_agreement_001``) are compared
field by field, and the read-back writes both fields from the vector.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from muscadet.declare import (
    MODE_LAW_PARAM_FIELDS,
    MODE_LAW_PARAMS,
    ComponentSpecError,
    check_failure_mode_spec,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def uniform_mode(**overrides):
    spec = {
        "name": "A__late",
        "kind": "two_state_mode",
        "cls": "ObjMode2S",
        "mode_name": "late",
        "targets": ["A"],
        "target_name": "A",
        "occ_law": {"cls": "uniform", "low": [2.0], "high": [12.0]},
        "occ_param_name": ["occ_low", "occ_high"],
        "occ_param": [[2.0, 12.0]],
        "not_occ_law": {"cls": "exp", "rate": [0.0]},
        "not_occ_param_name": ["not_occ_rate"],
        "not_occ_param": [0.0],
    }
    spec.update(overrides)
    return spec


def test_the_uniform_law_declares_both_its_fields():
    assert MODE_LAW_PARAMS["uniform"] == ("low", "high")
    assert MODE_LAW_PARAM_FIELDS["uniform"] == "low"
    assert MODE_LAW_PARAMS["exp"] == ("rate",)


def test_an_agreeing_uniform_declaration_is_accepted():
    assert check_failure_mode_spec(uniform_mode()) == "A__late"


def test_a_scalar_uniform_law_agrees_with_its_one_order_vector():
    spec = uniform_mode(occ_law={"cls": "uniform", "low": 2.0, "high": 12.0})
    assert check_failure_mode_spec(spec) == "A__late"


@pytest.mark.parametrize(
    "law, field, value",
    [
        ({"cls": "uniform", "low": [3.0], "high": [12.0]}, "low", "3.0"),
        ({"cls": "uniform", "low": [2.0], "high": [11.0]}, "high", "11.0"),
    ],
)
def test_a_disagreement_on_either_bound_is_refused(law, field, value):
    with pytest.raises(ComponentSpecError) as refusal:
        check_failure_mode_spec(uniform_mode(occ_law=law))
    message = str(refusal.value)
    assert "A__late" in message
    assert f"occ_law[{field!r}]=[{value}]" in message, message


_READ_BACK = r"""
import json

import muscadet
from muscadet.declare import check_failure_mode_spec, component_spec


class UniformProbeTarget(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowOut", name="f", var_prod_default=True))


system = muscadet.System(name="UniformReadBack")
system.add_component(name="A", cls="UniformProbeTarget")
mode = system.add_component(
    cls="ObjMode2S",
    mode_name="late",
    targets=["A"],
    occ_law={"cls": "uniform", "low": 2.0, "high": 12.0},
    not_occ_law={"cls": "exp", "rate": 0.0},
    occ_effects={"f_fed_available_out": False},
)
spec = component_spec(mode)
check_failure_mode_spec(spec)
print("RESULT " + json.dumps({
    "spec": spec,
    "wired": [mode.variable("occ_low").value(), mode.variable("occ_high").value()],
}))
"""


@pytest.fixture(scope="module")
def read_back():
    completed = subprocess.run(
        [sys.executable, "-c", _READ_BACK],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert completed.returncode == 0, completed.stderr
    for line in completed.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT ") :])
    raise AssertionError(completed.stdout + completed.stderr)


def test_a_built_uniform_mode_reads_back_both_bounds(read_back):
    spec = read_back["spec"]
    assert read_back["wired"] == [2.0, 12.0]
    assert spec["occ_law"] == {"cls": "uniform", "low": [2.0], "high": [12.0]}
    assert spec["occ_param_name"] == ["occ_low", "occ_high"]
