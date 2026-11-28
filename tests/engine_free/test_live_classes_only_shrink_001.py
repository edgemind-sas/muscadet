"""The modules of muscadet that still carry a live class, and no other.

muscadet hands its live classes to the engine distribution kind by kind: the
logic gate, then the controller. This guard names the modules where a class
still descends from a cod3s base, and refuses any other. The list only
shrinks: a module that no longer carries one makes the guard fail until its
line is removed.

Read statically, and through the package: a class is live when one of its
bases is written ``cod3s.X``, or carries the name of a class already found
live anywhere in muscadet. A base imported under another name escapes it,
which muscadet does nowhere.
"""

import ast
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "muscadet"

#: Where a class descends from a cod3s or PyCATSHOO base, and the family it
#: leaves with.
STILL_LIVE = {
    "capacity.py": "continuous flows, capacities and measurement channels",
    "flow.py": "discrete flows",
    "flow_continuous.py": "continuous flows",
    "kb/continuous.py": "the flow component, through its library classes",
    "kb/datacenter.py": "the flow component, through its library classes",
    "kb/electric.py": "the flow component, through its library classes",
    "kb/hydraulic.py": "the flow component, through its library classes",
    "kb/rbd.py": "the flow component, through its library classes",
    "mixture.py": "mixtures",
    "obj.py": "the flow component",
    "obj_logic.py": "the flow component, through LogicOr and LogicAnd",
    "rules.py": "rules",
    "system.py": "the system",
    "transfer.py": "transfers",
}

ENGINE_ROOTS = {"cod3s", "Pyc", "pyc", "Pycatshoo"}


def _classes():
    for path in sorted(PACKAGE.rglob("*.py")):
        module = str(path.relative_to(PACKAGE))
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef):
                yield module, node.name, [ast.unparse(base) for base in node.bases]


def _modules_with_a_live_class():
    classes = list(_classes())
    live = set()
    grown = True
    while grown:
        grown = False
        for _, name, bases in classes:
            if name in live:
                continue
            if any(
                base.split(".")[0] in ENGINE_ROOTS or base.split(".")[-1] in live
                for base in bases
            ):
                live.add(name)
                grown = True
    found = {}
    for module, name, _ in classes:
        if name in live:
            found.setdefault(module, []).append(name)
    return found


def test_no_module_outside_the_list_carries_a_live_class():
    unexpected = {
        module: classes
        for module, classes in _modules_with_a_live_class().items()
        if module not in STILL_LIVE
    }
    assert not unexpected, (
        f"these modules carry a class that descends from an engine base: "
        f"{unexpected}. muscadet declares a component and the engine "
        "distribution builds it"
    )


def test_a_module_that_carries_none_left_the_list():
    gone = sorted(set(STILL_LIVE) - set(_modules_with_a_live_class()))
    assert not gone, f"remove these lines from STILL_LIVE: {gone}"


def test_the_gate_and_the_controller_are_declarations():
    live = _modules_with_a_live_class()
    for module in ("logic_gate.py", "controller.py", "obj_ctrl.py", "ctrl_grammar.py"):
        assert module not in live
    assert "ObjLogicGate" not in live.get("obj_logic.py", [])
