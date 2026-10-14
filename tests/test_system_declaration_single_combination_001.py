"""A targeted mode left with ONE combination, read back as a document and rebuilt.

``system_spec`` reads a mode's ``aut_name`` back from the automaton that got
built, because ``ObjMode2S`` does not keep the argument: a single automaton
named otherwise than the mode means a self-hosted mode built under a name of its
own. A TARGETED mode can show the same thing. Over two targets with an inactive
order 1 (a pure common cause), the default drop keeps the order-2 automaton
alone, named ``<mode>__cc_1_2``. The read took that suffix for an ``aut_name``, and cod3s
refuses an ``aut_name`` on a targeted mode, so the document could not be built
back. The COD3S Platform rebuilds every PyCATSHOO run from its declaration, so
a study holding such a mode failed at launch.

The rebuild needs a system torn down first and PyCATSHOO allows one per process,
so the claim runs in a subprocess of its own.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_SINGLE_COMBINATION_ROUND_TRIP = """
import json

import cod3s
import muscadet
from muscadet.declare import build_system, system_spec


class SingleCombinationTarget(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow(dict(cls="FlowOut", name="f", var_prod_default=True))


system = muscadet.System(name="SingleCombination")
for name in ("A", "B"):
    system.add_component(name=name, cls="SingleCombinationTarget")
# The wire the COD3S Platform emits for a pure common cause: a delay law whose
# order 1 is the explicit inactive marker, so only the order-2 combination gets
# an automaton.
mode = system.add_component(
    cls="ObjMode2S",
    fm_name="ccf",
    targets=["A", "B"],
    occ_state="occ",
    not_occ_state="rep",
    occ_law={"cls": "delay", "time": [None, 10.0]},
    not_occ_law={"cls": "delay", "time": [None, 1.0]},
    occ_param_name=["ttf"],
    not_occ_param_name=["ttr"],
    occ_effects={"f_fed_available_out": False},
)
automata = sorted(mode.automata_d)
mode_key = mode.basename()

declared = json.loads(json.dumps(system_spec(system)))
system.deleteSys()
cod3s.terminate_session()

rebuilt_system = build_system(declared, system=muscadet.System(name="SingleRebuilt"))
rebuilt = json.loads(json.dumps(system_spec(rebuilt_system)))

print("RESULT " + json.dumps({
    "automata": automata,
    "declared": declared["components"][mode_key],
    "rebuilt_automata": sorted(rebuilt_system.comp[mode_key].automata_d),
    "identical": declared["components"][mode_key] == rebuilt["components"][mode_key],
}))
"""


def run_probe(script):
    """Run ``script`` in a process of its own and return what it printed."""
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


def test_a_targeted_mode_with_one_combination_round_trips():
    """The lone automaton's suffix is not taken for an ``aut_name``."""
    result = run_probe(_SINGLE_COMBINATION_ROUND_TRIP)
    # The shape is the one that bit: a single automaton, not named after the
    # mode alone.
    assert result["automata"] == ["ccf__cc_1_2"]
    assert "aut_name" not in result["declared"], result["declared"]
    assert result["declared"]["targets"] == ["A", "B"]
    assert result["rebuilt_automata"] == result["automata"]
    assert result["identical"], result
