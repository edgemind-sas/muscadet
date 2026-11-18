"""A two-state mode's effect priority crosses the system declaration.

``cod3s.ObjMode2S`` takes ``effects_priority``: the rank by which the engine
decides which of several active modes writing the same variable of the same
component applies its value. A second engine reading the declaration has to
apply the same rank, so the declaration carries it -- and only when it is set,
a mode declaring none being written exactly as before.

One live system per process, so the round trip runs in a subprocess.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_ROUND_TRIP = """
import json

import cod3s
import muscadet
from muscadet.declare import build_system, system_spec


class PriorityTargetForDeclarationTest(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowOut", name="f", var_prod_default=True))


system = muscadet.System(name="PriorityRef")
for name in ("A", "B"):
    system.add_component(name=name, cls="PriorityTargetForDeclarationTest")
system.add_component(
    cls="ObjMode2S",
    mode_name="ranked",
    targets=["A"],
    occ_law={"cls": "delay", "time": 2},
    not_occ_law={"cls": "delay", "time": 3},
    occ_effects={"f_fed_available_out": False},
    effects_priority=1,
)
system.add_component(
    cls="ObjMode2S",
    mode_name="plain",
    targets=["B"],
    occ_law={"cls": "delay", "time": 2},
    not_occ_law={"cls": "delay", "time": 3},
    occ_effects={"f_fed_available_out": False},
)

declared = json.loads(json.dumps(system_spec(system)))
system.deleteSys()
cod3s.terminate_session()

rebuilt_system = build_system(declared, system=muscadet.System(name="PriorityRe"))
rebuilt = json.loads(json.dumps(system_spec(rebuilt_system)))


def comparable(components):
    return {
        name: {k: v for k, v in entry.items() if k != "source_cls"}
        for name, entry in components.items()
    }


print("RESULT " + json.dumps({
    "identical": comparable(declared["components"])
    == comparable(rebuilt["components"]),
    "ranked": declared["components"]["A__ranked"],
    "plain": declared["components"]["B__plain"],
    "rebuilt_priority": rebuilt_system.comp["A__ranked"].effects_priority,
}))
"""


def run_probe(script):
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    for line in completed.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT ") :])

    raise AssertionError(
        f"probe printed no result:\n{completed.stdout}\n{completed.stderr}"
    )


def test_the_effect_priority_round_trips_and_is_written_only_when_set():
    result = run_probe(_ROUND_TRIP)

    assert result["ranked"]["effects_priority"] == 1
    assert "effects_priority" not in result["plain"]
    assert result["rebuilt_priority"] == 1
    assert result["identical"], result
