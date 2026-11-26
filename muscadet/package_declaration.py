"""The declaration of a run package, built from DATA alone.

A run package is a COD3S Platform model export and, beside it, the study that
says which modes, events and indicators the run carries. Until now the system
declaration of such a package was READ BACK from a live system: the export was
materialised on the engine (:func:`muscadet.importers.cod3s_platform.apply_to_system`),
the study was applied on the live objects, and :func:`muscadet.declare.system_spec`
dumped the result. That road needs an engine to produce a document whose whole
point is to be handed to ANY engine.

This module is the other road. It walks the parse layer of the importer
(:func:`~muscadet.importers.cod3s_platform.parse_platform_export`, pure) and
writes the document directly, creating no engine object.

What a live object used to supply, two tables supply here, and they are the
ONLY place either is written:

* :data:`DECLARATION_DEFAULTS` -- the value every declaration field takes when
  nothing set it, per declaration class. A live flow fills them in its own
  constructor; the document has to carry them all the same, since a reader that
  met a missing key would fall back on a default it cannot see.
* :data:`COMPONENT_CATALOGUE` -- the variables and the states each kind of
  component exposes, by name. An indicator of a study designates what it
  observes by a pattern, and the pattern is expanded on what the system holds.

**The proof is a comparison of documents, never of systems.** PyCATSHOO admits
one live system per process, so two systems are never put side by side: the
document built here and the one read back from a live system are compared key
by key (``tests/test_declaration_double_reading.py``), on every export the
suite builds.
"""

from __future__ import annotations

import copy
import itertools
import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from cod3s.specs.conditions import sanitize_cond_format
from cod3s.specs.mode_footprint import mode_footprint
from cod3s.specs.mode_law import ModeLawInst, parse_mode_law
from cod3s.specs.naming import cc_comb_suffix, factorize_target_names, order_param_name
from cod3s.specs.study_yaml import StudyYaml

from . import declare
from .common import copy_declaration
from .ctrl_grammar import CTRL_IN_SERVICE, CtrlRepublish, build_ctrl_node
from .importers import cod3s_platform as importer
from .profile import PROFILE_CLASSES, build_profile
from .rules import RuleSet, normalise_boolean_operand, validate_operand_shape

# ---------------------------------------------------------------------------
# Table 1 -- the defaults of every declaration class
# ---------------------------------------------------------------------------

#: What ``component_authorized`` holds on a flow nobody restricted.
_ANY_COMPONENT = [{"class_name_bkd": ".*"}]

#: The value each DECLARATION field takes when nothing set it, per declaration
#: class, exactly as the document writes it. A field absent from a class's
#: entry is one the document never writes for that class at its default.
DECLARATION_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "FlowIn": {
        "component_authorized": _ANY_COMPONENT,
        "logic": "or",
        "var_available_in_default": True,
        "var_fed_default": False,
        "var_in_default": False,
        "var_type": "bool",
    },
    "FlowOut": {
        "component_authorized": _ANY_COMPONENT,
        "negate": False,
        "var_fed_available_out_init": True,
        "var_fed_available_out_reset": True,
        "var_fed_default": False,
        "var_is_active_default": True,
        "var_prod_cond_inner_mode": "or",
        "var_prod_default": False,
        "var_type": "bool",
    },
    "FlowContinuousIn": {
        "component_authorized": _ANY_COMPONENT,
        "var_demand_default": 0.0,
        "var_fed_default": 0.0,
        "var_in_default": 0.0,
        "var_type": "float",
    },
    "FlowContinuousOut": {
        "allocation": "proportional",
        "allocation_priorities": {},
        "allocation_shares": {},
        "component_authorized": _ANY_COMPONENT,
        "var_demand_in_default": 0.0,
        "var_fed_default": 0.0,
        "var_prod_cond_inner_mode": "or",
        "var_type": "float",
    },
}
DECLARATION_DEFAULTS["FlowOutTempo"] = {
    **DECLARATION_DEFAULTS["FlowOut"],
    "init_enable": False,
    "occ_disable_flow": None,
    "occ_enable_flow": None,
    "state_disabling_name": "disabling",
    "state_enabling_name": "enabling",
}
DECLARATION_DEFAULTS["FlowOutOnTrigger"] = {
    **DECLARATION_DEFAULTS["FlowOut"],
    "trigger_logic": "or",
    "trigger_time_down": 0,
    "trigger_time_up": 0,
    "trigger_up": None,
}


def _defaults(clsname: str) -> Dict[str, Any]:
    return copy.deepcopy(DECLARATION_DEFAULTS[clsname])


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


class _Side:
    """What one component offers a condition operand to resolve onto."""

    def __init__(self, spec: importer.ComponentSpec):
        self.name = spec.name
        self.inputs = {f.name: f for f in spec.flows if f.direction == "input"}
        self.outputs = {f.name: f for f in spec.flows if f.direction == "output"}


class _FlowRef:
    """Stand-in of the flow object an operand resolves onto: its family."""

    def __init__(self, flow: importer.FlowSpec):
        self.is_continuous = flow.flow_family == importer.CONTINUOUS_FAMILY


#: How a refused operand names what a comparison is, as the engine words it.
_COMPARISON_KIND = "a comparison against a continuous quantity"


def _checked_operand(side: _Side, op: dict, name, port, cmp_op, cmp_value):
    """Refuse an operand mapping the engine refuses; the threshold as a number."""
    where = f"Object {side.name}: production condition operand"
    if not isinstance(name, str):
        raise importer.Cod3sPlatformImportError(
            f"{where} mapping must carry a string 'name' : {op}"
        )
    if port not in (None, "in", "out"):
        raise importer.Cod3sPlatformImportError(
            f"{where} 'port' must be 'in' or 'out', got {port!r}"
        )
    try:
        validate_operand_shape(
            f"{where} {name!r}",
            cmp_op,
            cmp_value,
            bool(op.get("negate", False)),
            _COMPARISON_KIND,
        )
        return None if cmp_op is None else float(cmp_value)
    except (TypeError, ValueError) as err:
        raise importer.Cod3sPlatformImportError(
            f"{where} {name!r}: {err}" if isinstance(err, TypeError) else str(err)
        ) from err


def _condition_spec(side: _Side, cond: Any, created_out: set) -> List[List[dict]]:
    """A declared condition, in the form the document carries.

    Mirrors :meth:`muscadet.ObjFlow.apply_prod_cond` followed by
    :func:`muscadet.declare._prod_cond_spec`: an operand is resolved onto the
    side it names (inputs first when it names none), a boolean read of a
    continuous flow becomes the comparison it is, and the operand is written
    back with its port.
    """
    if not cond:
        return []
    if isinstance(cond, (str, dict)):
        cond = [[cond]]
    out = []
    for group in cond:
        operands = [group] if isinstance(group, (str, dict)) else list(group)
        row = []
        for op in operands:
            if not isinstance(op, (str, dict)):
                raise importer.Cod3sPlatformImportError(
                    f"Bad format for production condition operand : {op}"
                )
            if isinstance(op, str):
                name, negate, port, cmp_op, cmp_value = op, False, None, None, None
            else:
                name = op.get("name")
                negate = bool(op.get("negate", False))
                port = op.get("port")
                cmp_op = op.get("op")
                cmp_value = op.get("value")
                cmp_value = _checked_operand(side, op, name, port, cmp_op, cmp_value)
            if port == "in":
                flow, resolved = side.inputs.get(name), "in"
            elif port == "out":
                flow, resolved = side.outputs.get(name), "out"
            elif name in side.inputs:
                flow, resolved = side.inputs[name], "in"
            else:
                flow, resolved = side.outputs.get(name), "out"
            if flow is None or (resolved == "out" and name not in created_out):
                kind = {"in": "input", "out": "output"}.get(port, "input nor output")
                raise importer.Cod3sPlatformImportError(
                    f"Object {side.name}: Flow {name} does not exist as {kind} "
                    f"flow (you must create it before using it in a FlowOut "
                    f"condition)"
                )
            cmp_op, cmp_value, negate = normalise_boolean_operand(
                _FlowRef(flow), cmp_op, cmp_value, negate
            )
            entry = {"name": name, "port": resolved}
            if negate:
                entry["negate"] = True
            if cmp_op is not None:
                entry["op"] = cmp_op
                entry["value"] = cmp_value
            row.append(entry)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Flows
# ---------------------------------------------------------------------------


def _profile_spec(raw: dict, component_name: str, flow_name: str) -> dict:
    """A declared profile, as the document writes it back.

    Attached to its flow first: a noisy profile that declares no key takes the
    one its component and its flow give it, and the document carries that key.
    """
    profile = build_profile(dict(raw), flow_name)
    profile = profile.attached_to(component_name, flow_name)
    return declare._declared_object_spec(
        profile, PROFILE_CLASSES, f"flow {flow_name}.profile"
    )


def _occ_law(raw: Optional[dict]) -> Optional[dict]:
    """A temporisation law, under the long class name the document carries."""
    if not isinstance(raw, dict):
        return raw
    law = dict(raw)
    clsname = law.get("cls")
    if clsname and "OccDistribution" not in clsname:
        law["cls"] = clsname.capitalize() + "OccDistribution"
    return law


def _input_flow(flow: importer.FlowSpec, component_name: str) -> dict:
    if flow.flow_family == importer.CONTINUOUS_FAMILY:
        kwargs = importer._continuous_in_kwargs(flow)
        fields = {**_defaults("FlowContinuousIn"), **kwargs}
        if "profile" in kwargs:
            fields["profile"] = _profile_spec(
                kwargs["profile"], component_name, flow.name
            )
        return fields
    fields = {**_defaults("FlowIn"), "cls": "FlowIn", "name": flow.name}
    fields["logic"] = flow.logic
    if flow.var_in_default is not None:
        fields["var_in_default"] = flow.var_in_default
    return fields


def _output_flow(flow: importer.FlowSpec, side: _Side, created_out: set) -> dict:
    if flow.flow_family == importer.CONTINUOUS_FAMILY:
        kwargs = importer._continuous_out_kwargs(flow)
        cond = kwargs.pop("var_prod_cond", None)
        fields = {**_defaults("FlowContinuousOut"), **kwargs}
        if "profile" in kwargs:
            fields["profile"] = _profile_spec(kwargs["profile"], side.name, flow.name)
        prod_cond = _condition_spec(side, cond, created_out)
        if prod_cond:
            fields["var_prod_cond"] = prod_cond
        return fields

    flow_type = flow.flow_type or "classic"
    clsname = {"tempo": "FlowOutTempo", "on_trigger": "FlowOutOnTrigger"}.get(
        flow_type, "FlowOut"
    )
    fields = {**_defaults(clsname), "cls": clsname, "name": flow.name}
    fields["var_prod_cond_inner_mode"] = flow.logic_inner_mode
    fields["negate"] = flow.negate
    if flow.init_value is not None:
        fields["var_prod_default"] = flow.init_value
    if flow.is_active_default is not None:
        fields["var_is_active_default"] = flow.is_active_default
    if flow.fed_available_init is not None:
        fields["var_fed_available_out_init"] = flow.fed_available_init
    if flow.fed_available_reset is not None:
        fields["var_fed_available_out_reset"] = flow.fed_available_reset
    if flow_type == "tempo":
        if flow.occ_enable is not None:
            fields["occ_enable_flow"] = _occ_law(flow.occ_enable)
        if flow.occ_disable is not None:
            fields["occ_disable_flow"] = _occ_law(flow.occ_disable)
        if flow.init_enable is not None:
            fields["init_enable"] = flow.init_enable
        elif flow.init_value is not None:
            fields["init_enable"] = flow.init_value
    elif flow_type == "on_trigger":
        if flow.trigger_time_up is not None:
            fields["trigger_time_up"] = flow.trigger_time_up
        if flow.trigger_time_down is not None:
            fields["trigger_time_down"] = flow.trigger_time_down
        if flow.trigger_logic is not None:
            fields["trigger_logic"] = flow.trigger_logic
        # The automaton of a triggered output is always built, and the output
        # keeps the state it is "up" in.
        fields["trigger_up"] = {"cls": "StateModel", "name": "up"}
    if flow_type == "tempo" and (
        fields["occ_enable_flow"] is not None or fields["occ_disable_flow"] is not None
    ):
        # The state a temporised output is in once enabled: built with the
        # automaton, so present exactly when a law asks for one.
        fields["state_enable_bkd"] = {
            "cls": "StateModel",
            "name": f"{flow.name}_enabled",
        }
    elif flow_type == "tempo":
        fields["state_enable_bkd"] = None
    prod_cond = _condition_spec(side, flow.logic, created_out)
    if prod_cond:
        fields["var_prod_cond"] = prod_cond
    return fields


def _flows(spec: importer.ComponentSpec) -> List[dict]:
    side = _Side(spec)
    flows = [_input_flow(f, spec.name) for f in spec.flows if f.direction == "input"]
    outputs = [f for f in spec.flows if f.direction == "output"]
    created_out: set = set()
    for flow in importer._order_outputs_by_deps(outputs, set(side.inputs), spec.name):
        flows.append(_output_flow(flow, side, created_out))
        created_out.add(flow.name)
    return flows


# ---------------------------------------------------------------------------
# Capacities, rule sets, mixture groups
# ---------------------------------------------------------------------------

DECLARATION_DEFAULTS["Capacity"] = {
    "fill_rate": 0.0,
    "transmits": True,
}
DECLARATION_DEFAULTS["CapacityFlow"] = {"weight": 1.0}
DECLARATION_DEFAULTS["MixtureIn"] = {}


def _capacity(
    capacity: importer.CapacitySpec, spec: importer.ComponentSpec, side: _Side
) -> dict:
    where = f"capacity {capacity.name}"
    kwargs = importer._capacity_kwargs(
        capacity, index=importer._FlowIndex(spec.flows), rule_sets=spec.rule_sets
    )
    declared_side = kwargs.get("side")
    flows = []
    for held in kwargs["flows"]:
        sides = [
            name
            for name, flows_on in (("in", side.inputs), ("out", side.outputs))
            if held["name"] in flows_on
        ]
        if not sides:
            raise importer.Cod3sPlatformImportError(
                f"Object {side.name}: {where}: flow {held['name']} does not "
                "exist as input nor output flow (you must create it before "
                "holding it in a capacity)"
            )
        if declared_side is not None:
            if declared_side not in sides:
                kind = "input" if declared_side == "in" else "output"
                raise importer.Cod3sPlatformImportError(
                    f"Object {side.name}: {where} is declared on side "
                    f"{declared_side!r} but flow {held['name']} does not "
                    f"exist as {kind} flow"
                )
            flow_side = declared_side
        else:
            flow_side = "in" if "in" in sides else "out"
        flows.append(
            {
                "cls": "CapacityFlow",
                "name": held["name"],
                "side": flow_side,
                "weight": held.get("weight", 1.0),
            }
        )
    resolved = {entry["side"] for entry in flows}
    if len(resolved) > 1:
        detail = ", ".join(f"{entry['name']}->{entry['side']}" for entry in flows)
        raise importer.Cod3sPlatformImportError(
            f"Object {side.name}: {where} holds flows resolving to different "
            f"sides ({detail}): a capacity sits entirely upstream (side='in') "
            "or entirely downstream (side='out') of the component's rules"
        )

    capacity_side = next(iter(resolved))
    flows_on_side = side.inputs if capacity_side == "in" else side.outputs
    for entry in flows:
        if flows_on_side[entry["name"]].flow_family != importer.CONTINUOUS_FAMILY:
            raise importer.Cod3sPlatformImportError(
                f"Object {side.name}: {where}: flow {entry['name']} is a discrete "
                "flow; a capacity holds continuous flows only"
            )
    held_names = {entry["name"] for entry in flows}
    for other in spec.capacities:
        if other is capacity:
            break
        other_side = other.side
        if other_side is None:
            other_side = (
                "in" if any(held.name in side.inputs for held in other.flows) else "out"
            )
        clash = sorted(held_names & {held.name for held in other.flows})
        if clash and other_side == capacity_side:
            raise importer.Cod3sPlatformImportError(
                f"Object {side.name}: {where} claims flow {', '.join(clash)} on "
                f"side {capacity_side!r}, already held by capacity {other.name}: "
                "a flow is buffered by at most one capacity per side"
            )

    content = {
        name: float(value) for name, value in (kwargs.get("content_init") or {}).items()
    }
    weights = {entry["name"]: entry["weight"] for entry in flows}
    for entry in flows:
        content.setdefault(entry["name"], 0.0)
    unknown = sorted(set(content) - set(weights))
    if unknown:
        raise importer.Cod3sPlatformImportError(
            f"Capacity {capacity.name}: 'content_init' names flows the capacity "
            f"does not hold : {', '.join(unknown)}"
        )
    for name, quantity in content.items():
        if quantity < 0 or quantity != quantity:
            raise importer.Cod3sPlatformImportError(
                f"Capacity {capacity.name}: initial content of {name!r} must be "
                f"positive or zero, got {quantity}"
            )
    occupied = sum(quantity * weights[name] for name, quantity in content.items())
    if occupied > kwargs["capacity"]:
        raise importer.Cod3sPlatformImportError(
            f"Capacity {capacity.name}: the initial content occupies "
            f"{occupied:g} of a volume of {kwargs['capacity']:g}"
        )
    makeup = kwargs.get("makeup_flow")
    if makeup is not None:
        room = kwargs["capacity"] - sum(
            quantity * weights[name] for name, quantity in content.items()
        )
        if room > 0.0:
            content[makeup] += room / weights[makeup]

    out = {
        **_defaults("Capacity"),
        "name": capacity.name,
        "capacity": kwargs["capacity"],
        "content_init": content,
        "flows": flows,
        "side": resolved.pop(),
        "transmits": kwargs["transmits"],
    }
    if "fill_rate" in kwargs:
        out["fill_rate"] = kwargs["fill_rate"]
    serve_rate = kwargs.get("serve_rate", math.inf)
    if serve_rate != math.inf:
        out["serve_rate"] = serve_rate
    if makeup is not None:
        out["makeup_flow"] = makeup
    serve_cond = _condition_spec(side, kwargs.get("serve_cond"), set(side.outputs))
    if serve_cond:
        out["serve_cond"] = serve_cond
        out["serve_cond_inner_mode"] = kwargs["serve_cond_inner_mode"]
    return out


def _rule_port(side: _Side, name: str, port: Optional[str], where: str, role: str):
    """The side a rule names a flow on, resolved as the engine resolves it."""
    if port == "in":
        flow, kind = side.inputs.get(name), "input"
    elif port == "out":
        flow, kind = side.outputs.get(name), "output"
    elif name in side.inputs:
        flow, port, kind = side.inputs[name], "in", "input"
    elif name in side.outputs:
        flow, port, kind = side.outputs[name], "out", "output"
    else:
        flow, kind = None, "input nor output"
    if flow is None:
        raise importer.Cod3sPlatformImportError(
            f"Object {side.name}: {where}: flow {name} does not exist as {kind} "
            f"flow (you must create it before using it in a {role})"
        )
    return flow, port


def _rule_set(rule_set: importer.RuleSetSpec, side: _Side) -> dict:
    kwargs = importer._rule_set_kwargs(rule_set)
    built = RuleSet(**copy.deepcopy(kwargs))
    for index, rule in enumerate(built.rules):
        where = f"rule set {built.name}, {built.rule_label(index)}"
        for operand in rule.cond:
            flow, operand.port = _rule_port(
                side, operand.name, operand.port, where, "rule guard"
            )
            operand.op, operand.value, operand.negate = normalise_boolean_operand(
                _FlowRef(flow), operand.op, operand.value, operand.negate
            )
        for flow_name in rule.cons:
            _rule_port(side, flow_name, "in", where, "rule 'cons' map")
        for flow_name in rule.prod:
            _rule_port(side, flow_name, "out", where, "rule 'prod' map")
    fields = built.model_dump()
    fields.pop("cls", None)
    return fields


def _mixture(mixture: importer.MixtureSpec, side: _Side) -> dict:
    kwargs = importer._mixture_kwargs(mixture)
    out = {
        "name": kwargs["name"],
        "flows": list(kwargs["flows"]),
        "flow_rate": kwargs["flow_rate"],
    }
    run_cond = _condition_spec(side, kwargs.get("run_cond"), set(side.outputs))
    if run_cond:
        out["run_cond"] = run_cond
        out["run_cond_inner_mode"] = kwargs["run_cond_inner_mode"]
    return out


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def _flow_component(
    spec: importer.ComponentSpec, *, create_default_out_automata: bool
) -> dict:
    side = _Side(spec)
    out = {
        "name": spec.name,
        "cls": "ObjFlow",
        declare.SOURCE_CLS_KEY: "ObjFlow",
        "flows": _flows(spec),
        "capacities": [_capacity(c, spec, side) for c in spec.capacities],
        "measurements_in": [],
        "measurements_out": [],
        "rules": [_rule_set(r, side) for r in spec.rule_sets],
        "transfers": [],
        "mixtures": [_mixture(m, side) for m in spec.mixtures],
    }
    if spec.deratings:
        out["deratings"] = [
            {"mode": derating.mode, "flow": derating.flow}
            for derating in spec.deratings
        ]
    out["automata"] = []
    out["failure_modes"] = []
    out["metadata"] = {
        "class_name": spec.class_name,
        "platform_id": spec.metadata.get("platform_id"),
        "attributes_initial": copy.deepcopy(
            spec.metadata.get("attributes_initial", [])
        ),
        "instance_overrides": importer._copied_audit_trail(
            spec.metadata.get("instance_overrides")
        ),
        "capacity_overrides": importer._copied_audit_trail(
            spec.metadata.get("capacity_overrides")
        ),
    }
    if create_default_out_automata:
        out["create_default_out_automata"] = True
    return out


def _gate_component(
    gate: importer.ComponentSpec, ctx: importer.ImporterContext, gate_names: set
) -> dict:
    cond, out_elements = importer._build_gate_cond_and_outputs(
        gate, ctx.connections, gate_names
    )
    k = None
    if gate.gate_kind == "k":
        k = gate.gate_k if gate.gate_k is not None else 2
    out = {
        "name": gate.name,
        declare.COMPONENT_KIND_KEY: declare.COMPONENT_KIND_LOGIC_GATE,
        "cls": "ObjLogicGate",
        declare.SOURCE_CLS_KEY: "ObjLogicGate",
        "logic_kind": gate.gate_kind,
        "k": k,
        "cond": copy_declaration(sanitize_cond_format(cond or [])),
        "out_elements": list(out_elements),
        "metadata": {
            "class_name": gate.class_name,
            "platform_id": gate.metadata.get("platform_id"),
            "logic_gate": gate.gate_kind,
        },
    }
    declare.check_logic_gate_spec(out)
    return out


#: What a controller's measurement input declares when nothing set it.
DECLARATION_DEFAULTS["MeasurementIn"] = {
    "fill_default": 0.0,
    "flows": [],
    "kind": "level",
    "level_default": 0.0,
    "rate_default": 0.0,
    "ratio_default": 0.0,
}
#: What a controller's value output declares when nothing set it.
DECLARATION_DEFAULTS["MeasurementOut"] = {
    "fill_default": 0.0,
    "flows": [],
    "gain_default": 1.0,
    "level_default": 0.0,
    "ratio_default": 0.0,
}
#: What a controller's boolean output declares when nothing set it.
DECLARATION_DEFAULTS["CtrlSignalOut"] = {"default": False}


def _controller_component(spec: importer.ComponentSpec) -> dict:
    controller = spec.controller
    controls_in, service_in = [], []
    for entry in controller.controls_in:
        kwargs = importer._control_in_kwargs(entry)
        if kwargs.get("kind") == CTRL_IN_SERVICE:
            service_in.append({"name": kwargs["name"], "kind": CTRL_IN_SERVICE})
            continue
        aggregate = kwargs.pop("aggregate", None)
        controls_in.append(
            {**_defaults("MeasurementIn"), **kwargs, "aggregate": aggregate}
        )

    controls_out = []
    for entry in controller.controls_out:
        kwargs = importer._control_out_kwargs(entry)
        name, kind, emit = (
            kwargs.pop("name"),
            kwargs.pop("kind"),
            kwargs.pop("emit", None),
        )
        node = build_ctrl_node(
            f"Object {spec.name}: controller output {name!r} emit", emit
        )
        if kind == importer.CONTROL_OUT_BOOL:
            fields = {**_defaults("CtrlSignalOut"), **kwargs}
        else:
            fields = {**_defaults("MeasurementOut"), **kwargs}
            if isinstance(node, CtrlRepublish):
                fields.pop("gain_default", None)
        out_entry = {"name": name, "kind": kind, **fields}
        emitted = declare._ctrl_emit_spec(node)
        if emitted is not None:
            out_entry["emit"] = emitted
        controls_out.append(out_entry)

    return {
        "name": spec.name,
        declare.COMPONENT_KIND_KEY: declare.COMPONENT_KIND_CONTROLLER,
        "cls": "ObjCtrl",
        declare.SOURCE_CLS_KEY: "ObjCtrl",
        "controls_in": controls_in + service_in,
        "controls_out": controls_out,
        "metadata": {
            "class_name": spec.class_name,
            "platform_id": spec.metadata.get("platform_id"),
            "attributes_initial": spec.metadata.get("attributes_initial", []),
            "controller_threshold_overrides": importer._copied_audit_trail(
                spec.metadata.get("controller_threshold_overrides")
            ),
            importer.CONTROLLER_MARKER: True,
        },
    }


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


def _connections(ctx: importer.ImporterContext, gate_names: set) -> List[dict]:
    """Every wire the export declares, as the document lists them."""
    flows_in, flows_out = {}, {}
    for spec in ctx.components:
        if spec.gate_kind is None and spec.controller is None:
            flows_in[spec.name] = {f.name for f in spec.flows if f.direction == "input"}
            flows_out[spec.name] = {
                f.name for f in spec.flows if f.direction == "output"
            }

    wires = {}
    for conn in ctx.connections:
        if conn.source_box is not None and conn.target_box is not None:
            source_box, target_box = conn.source_box, conn.target_box
        elif conn.target_component in gate_names:
            continue
        elif conn.source_component in gate_names:
            source_box = f"{conn.target_interface}_out"
            target_box = f"{conn.target_interface}_in"
        else:
            source_box, target_box = f"{conn.flow_name}_out", f"{conn.flow_name}_in"
        entry = {
            "source": conn.source_component,
            "source_box": source_box,
            "target": conn.target_component,
            "target_box": target_box,
        }
        flow = source_box[: -len("_out")] if source_box.endswith("_out") else None
        if (
            flow is not None
            and target_box == f"{flow}_in"
            and flow in flows_out.get(conn.source_component, ())
            and flow in flows_in.get(conn.target_component, ())
        ):
            entry["flow"] = flow
        key = (entry["source"], source_box, entry["target"], target_box)
        wires.setdefault(key, entry)
    return [wires[key] for key in sorted(wires)]


def context_declaration(
    ctx: importer.ImporterContext,
    *,
    name: Optional[str] = None,
    create_default_out_automata: bool = True,
) -> Dict[str, Any]:
    """The system declaration of a parsed export, with no engine object built.

    The pure counterpart of :func:`~muscadet.importers.cod3s_platform.apply_to_system`
    followed by :func:`muscadet.declare.system_spec`: same parse context in,
    same document out.
    """
    gate_specs = [c for c in ctx.components if c.gate_kind is not None]
    gate_names = {gate.name for gate in gate_specs}

    components: Dict[str, dict] = {}
    for spec in ctx.components:
        if spec.gate_kind is None and spec.controller is None:
            components[spec.name] = _flow_component(
                spec, create_default_out_automata=create_default_out_automata
            )
    for gate in importer._order_gates(gate_specs, ctx.connections, gate_names):
        components[gate.name] = _gate_component(gate, ctx, gate_names)
    for spec in ctx.components:
        if spec.controller is not None:
            components[spec.name] = _controller_component(spec)

    return declare._checked_document(
        {
            "version": declare.SYSTEM_SPEC_VERSION,
            "name": name or ctx.system_name,
            "components": components,
            "connections": _connections(ctx, gate_names),
            "indicators": [],
            declare.GENERATED_INDICATORS: declare.GENERATED_INDICATORS_DEFAULT,
        },
        "$",
        declare.SystemSpecError,
    )


def export_declaration(
    payload: Dict[str, Any],
    *,
    name: Optional[str] = None,
    create_default_out_automata: bool = True,
) -> Dict[str, Any]:
    """The system declaration of a platform export, with no engine object built."""
    return context_declaration(
        importer.parse_platform_export(payload),
        name=name,
        create_default_out_automata=create_default_out_automata,
    )


# ---------------------------------------------------------------------------
# Table 2 -- what each kind of component exposes
# ---------------------------------------------------------------------------


class Exposed:
    """The names one component exposes to a study: variables, states, automata."""

    __slots__ = ("variables", "states", "automata")

    def __init__(self):
        self.variables: set = set()
        self.states: set = set()
        self.automata: set = set()

    def as_data(self) -> Dict[str, List[str]]:
        return {
            "variables": sorted(self.variables),
            "states": sorted(self.states),
            "automata": sorted(self.automata),
        }


_CONTINUOUS_FLOWS = ("FlowContinuousIn", "FlowContinuousOut")
_INPUT_FLOWS = ("FlowIn", "FlowDiscreteIn", "FlowContinuousIn")


def _expose_thresholds(name: str, spec: dict, cond: Any, base: str, out: Exposed):
    """The automaton watching each comparison of ``cond`` on a continuous quantity.

    A comparison reading a discrete flow watches nothing: its state already
    changes at an exact date. An operand naming no port reads a measurement
    channel, which is continuous.
    """
    families = {}
    for flow in spec.get("flows") or []:
        port = "in" if flow.get("cls") in _INPUT_FLOWS else "out"
        families[(flow["name"], port)] = flow.get("cls") in _CONTINUOUS_FLOWS
    for row, group in enumerate(cond or []):
        for column, operand in enumerate(group):
            if not isinstance(operand, dict) or operand.get("op") is None:
                continue
            if not families.get((operand.get("name"), operand.get("port")), True):
                continue
            stem = f"{base}_cond_{row}_{column}"
            out.states.update({f"{stem}_below", f"{stem}_above"})
            out.automata.add(f"{name}_{stem}_threshold")


def _expose_flow_component(name: str, spec: dict, out: Exposed) -> None:
    default_automata = bool(spec.get("create_default_out_automata"))
    for flow in spec.get("flows") or []:
        clsname, flow_name = flow.get("cls"), flow["name"]
        _expose_thresholds(name, spec, flow.get("var_prod_cond"), flow_name, out)
        if clsname in ("FlowIn", "FlowDiscreteIn"):
            out.variables.add(f"{flow_name}_fed_in")
        elif clsname == "FlowContinuousIn":
            out.variables.update({f"{flow_name}_fed_in", f"{flow_name}_demand_out"})
            if flow.get("profile") is not None:
                out.variables.add(f"{flow_name}_in_profile")
        elif clsname == "FlowContinuousOut":
            out.variables.update(
                {
                    f"{flow_name}_fed_out",
                    f"{flow_name}_capability_out",
                    f"{flow_name}_out_rate",
                }
            )
            if flow.get("profile") is not None:
                out.variables.add(f"{flow_name}_out_profile")
        else:
            out.variables.update(
                {
                    f"{flow_name}_fed_out",
                    f"{flow_name}_fed_available_out",
                    f"{flow_name}_is_active",
                    f"{flow_name}_prod",
                    f"{flow_name}_prod_available",
                }
            )
            if default_automata:
                out.states.update({f"{flow_name}_ok", f"{flow_name}_nok"})
                out.automata.add(f"{name}_{flow_name}")
            if clsname in ("FlowOutTempo", "FlowDiscreteOutTempo") and (
                flow.get("occ_enable_flow") is not None
                or flow.get("occ_disable_flow") is not None
            ):
                out.states.add(flow.get("state_enable_name", f"{flow_name}_enabled"))
                out.states.add(flow.get("state_disable_name", f"{flow_name}_disabled"))
                out.automata.add(f"{flow_name}_out_tempo")
            if clsname in ("FlowOutOnTrigger", "FlowDiscreteOutOnTrigger"):
                out.states.update({"down", "up"})
                out.automata.add(f"{flow_name}_trigger")

    for capacity in spec.get("capacities") or []:
        cap = capacity["name"]
        held = [entry["name"] for entry in capacity.get("flows") or []]
        is_open = capacity.get("makeup_flow") is not None
        _expose_thresholds(name, spec, capacity.get("serve_cond"), f"{cap}_serve", out)
        out.variables.update({f"{cap}_qty", f"{cap}_fill"})
        for flow_name in held:
            out.variables.update(
                {
                    f"{cap}_qty_{flow_name}",
                    f"{cap}_fill_{flow_name}",
                    f"{cap}_ratio_{flow_name}",
                    f"{cap}_inflow_{flow_name}",
                    f"{cap}_outflow_{flow_name}",
                    f"{cap}_serve_rate_{flow_name}",
                }
            )
            if is_open:
                out.variables.add(f"{cap}_vent_{flow_name}")
        if is_open:
            out.variables.add(f"{cap}_makeup")
        else:
            out.states.update({f"{cap}_empty", f"{cap}_partial", f"{cap}_full"})
            out.automata.add(f"{name}_{cap}_bounds")
        if capacity.get("side") == "in" and len(held) > 1:
            for flow_name in held:
                base = f"{cap}_stock_{flow_name}"
                out.states.update({f"{base}_held", f"{base}_depleted"})
                out.automata.add(f"{name}_{base}")

    for rule_set in spec.get("rules") or []:
        rules = rule_set.get("rules") or []
        if not any(rule.get("cond") for rule in rules):
            continue
        out.automata.add(f"{name}_{rule_set['name']}_mode")
        for index in range(len(rules)):
            out.states.add(f"{rule_set['name']}_rule_{index}")

    for mixture in spec.get("mixtures") or []:
        out.variables.add(f"{mixture['name']}_flow_rate")
        _expose_thresholds(
            name, spec, mixture.get("run_cond"), f"{mixture['name']}_run", out
        )

    for derating in spec.get("deratings") or []:
        out.variables.add(f"{derating['mode']}_derating_{derating['flow']}")


def _expose_controller(name: str, spec: dict, out: Exposed) -> None:
    for entry in spec.get("controls_in") or []:
        if entry.get("kind") == "service":
            out.variables.add(f"{entry['name']}_fed_in")
    for entry in spec.get("controls_out") or []:
        iface = entry["name"]
        if entry.get("kind") == "value":
            out.variables.update(
                {
                    f"{iface}_level",
                    f"{iface}_fill",
                    f"{iface}_level_gain",
                    f"{iface}_forced",
                    f"{iface}_forced_value",
                }
            )
            for flow_name in entry.get("flows") or []:
                out.variables.update(
                    {
                        f"{iface}_level_{flow_name}",
                        f"{iface}_fill_{flow_name}",
                        f"{iface}_ratio_{flow_name}",
                    }
                )
            continue
        out.variables.update({f"{iface}_signal_out", f"{iface}_signal_available"})
        if entry.get("emit") is None:
            continue
        out.states.update({f"{iface}_blinding_blinded", f"{iface}_blinding_serving"})
        out.automata.add(f"{name}_{iface}_blinding")
        _expose_emit(name, iface, entry["emit"], "", out)


#: What each boolean node of an output grammar compiles to: the parameters it
#: reads, and the two states of the automaton that watches it.
_EMIT_NODES = {
    "compare": (("threshold",), ("below", "above")),
    "band": (("activate", "release"), ("released", "activated")),
}


def _expose_emit(name: str, iface: str, node: dict, path: str, out: Exposed) -> None:
    """The parameters and the automata one node of an output grammar builds."""
    op = node.get("op")
    if op in _EMIT_NODES:
        params, states = _EMIT_NODES[op]
        base = f"{iface}{path}_{op}"
        out.variables.update(f"{iface}{path}_{param}" for param in params)
        out.states.update(f"{base}_{state}" for state in states)
        out.automata.add(f"{name}_{base}")
    for index, operand in enumerate(node.get("operands") or []):
        _expose_emit(name, iface, operand, f"{path}_operand_{index}", out)


#: The two-state FACADE classes a study may still name, each with the law its
#: class stands for: ``(law, parameter of the law, default parameter names)``.
#: Such a mode writes no law field, its class carries it.
_FACADE_LAWS = {
    "ObjFMExp": ("exp", "rate", ("lambda", "mu")),
    "ObjFMDelay": ("delay", "time", ("ttf", "ttr")),
}

#: ``(facade key, engine key)`` for every field the two spellings share.
_FACADE_KEYS = (
    ("fm_name", "mode_name"),
    ("failure_state", "occ_state"),
    ("failure_cond", "occ_cond"),
    ("failure_effects", "occ_effects"),
    ("failure_effects_trans", "occ_effects_trans"),
    ("failure_param_name", "occ_param_name"),
    ("failure_param", "occ_param"),
    ("repair_state", "not_occ_state"),
    ("repair_cond", "not_occ_cond"),
    ("repair_effects", "not_occ_effects"),
    ("repair_effects_trans", "not_occ_effects_trans"),
    ("repair_param_name", "not_occ_param_name"),
    ("repair_param", "not_occ_param"),
)


def _engine_view(spec: dict) -> dict:
    """A two-state mode declaration in the ENGINE spelling, whatever its class.

    A facade mode (``ObjFMExp``, ``ObjFMDelay``) spells ``failure_*`` /
    ``repair_*`` and carries its law in its class. Read through this, it
    answers the same questions an ``ObjMode2S`` declaration answers: which
    orders it keeps, what it exposes.
    """
    facade = _FACADE_LAWS.get(spec.get("cls"))
    if facade is None:
        return spec
    law, field, _names = facade
    defaults = declare.FAILURE_MODE_VOCABULARY.defaults
    view = {key: value for key, value in spec.items()}
    for facade_key, engine_key in _FACADE_KEYS:
        if facade_key in spec:
            view[engine_key] = spec[facade_key]
        elif facade_key in defaults:
            view[engine_key] = defaults[facade_key]
    for direction in ("occ", "not_occ"):
        scalars = [
            declare._law_order_value(entry)
            for entry in view.get(f"{direction}_param") or []
        ]
        view[f"{direction}_param"] = scalars
        view[f"{direction}_law"] = {"cls": law, field: scalars}
    return view


def _mode_orders(spec: dict):
    """``(order_max, kept orders)`` of a targeted two-state mode declaration."""
    spec = _engine_view(spec)
    targets = spec.get("targets") or []
    footprint = mode_footprint(
        len(targets),
        occ_law=spec.get("occ_law"),
        not_occ_law=spec.get("not_occ_law"),
        behaviour=spec.get("behaviour", "internal"),
        occ_param=spec.get("occ_param"),
        not_occ_param=spec.get("not_occ_param"),
        drop_inactive_automata=spec.get("drop_inactive_automata", True),
    )
    return len(targets), [o.order for o in footprint.by_order if o.combinations]


def _expose_mode(name: str, spec: dict, exposed: Dict[str, Exposed]) -> None:
    out = exposed[name]
    if spec.get("cls") == "ObjEvent":
        out.automata.add(spec.get("event_aut_name", "ev"))
        out.states.update(
            {
                spec.get("occ_state_name", "occ"),
                spec.get("not_occ_state_name", "not_occ"),
            }
        )
        return

    spec = _engine_view(spec)
    mode_name = spec["mode_name"]
    targets = spec.get("targets")
    occ_state = spec.get("occ_state", "occ")
    not_occ_state = spec.get("not_occ_state", "not_occ")
    if targets is None:
        out.automata.add(spec.get("aut_name") or mode_name)
        out.states.update({occ_state, not_occ_state})
        return

    behaviour = spec.get("behaviour", "internal")
    order_max, kept = _mode_orders(spec)
    prefix = spec.get("param_name_order_prefix", "__{order}_o_{order_max}")
    trans_prefix = spec.get("trans_name_prefix", "__cc_{target_comb_u}")
    for direction in ("occ", "not_occ"):
        names = spec.get(f"{direction}_param_name") or []
        values = spec.get(f"{direction}_param") or []
        for order in range(1, order_max + 1):
            entry = values[order - 1] if len(values) >= order else None
            width = len(entry) if isinstance(entry, (list, tuple)) else 1
            for base in names[:width]:
                out.variables.add(order_param_name(base, order, order_max, fmt=prefix))
    if behaviour in ("external", "external_rep_indep"):
        for target in targets:
            out.variables.add(f"ctrl_{mode_name}_{target}")
            if target in exposed:
                exposed[target].automata.add(mode_name)
                exposed[target].states.update(
                    {f"{mode_name}__{occ_state}", f"{mode_name}__{not_occ_state}"}
                )
    for order in kept:
        parked = _parked_states(spec, order, occ_state, not_occ_state)
        for combination in itertools.combinations(range(order_max), order):
            suffix = cc_comb_suffix(combination, order_max, trans_prefix)
            out.automata.add(f"{mode_name}{suffix}")
            out.states.update(
                {f"{occ_state}{suffix}", f"{not_occ_state}{suffix}"}
                | {f"{state}{suffix}" for state in parked}
            )


def _parked_states(spec: dict, order: int, occ_state: str, not_occ_state: str) -> set:
    """The parked states the instantaneous draws of one order build.

    A direction drawn instantaneously parks beside the state it leaves, under
    the name the mode declares or ``<state>_star``. A CERTAIN draw
    (probability 1) parks nowhere: the engine builds it as a single branch.
    """
    parked = set()
    for direction, declared, beside in (
        ("occ", spec.get("occ_parked_state"), not_occ_state),
        ("not_occ", spec.get("not_occ_parked_state"), occ_state),
    ):
        law = spec.get(f"{direction}_law")
        if not isinstance(law, dict) or law.get("cls") != "inst":
            continue
        values = spec.get(f"{direction}_param") or []
        value = values[order - 1] if len(values) >= order else None
        value = declare._law_order_value(value)
        try:
            certain = value is not None and float(value) == 1.0
        except (TypeError, ValueError):
            certain = False
        if not certain:
            parked.add(declared or f"{beside}_star")
    return parked


def component_catalogue(declaration: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    """What every component of ``declaration`` exposes, by name.

    The variables, the states and the automata an engine holds once it has
    built the system the document describes -- the names a study designates:
    an indicator observes one, an effect writes one, a condition reads one.
    Read off the document alone, each list sorted.
    """
    return {name: entry.as_data() for name, entry in _exposed(declaration).items()}


def _exposed(declaration: Dict[str, Any]) -> Dict[str, Exposed]:
    components = declaration.get("components") or {}
    exposed = {name: Exposed() for name in components}
    for name, spec in components.items():
        kind = declare.component_kind(spec)
        if kind == declare.COMPONENT_KIND_FLOW:
            _expose_flow_component(name, spec, exposed[name])
        elif kind == declare.COMPONENT_KIND_CONTROLLER:
            _expose_controller(name, spec, exposed[name])
        elif kind == declare.COMPONENT_KIND_LOGIC_GATE:
            exposed[name].variables.add("result")
    for name, spec in components.items():
        if declare.component_kind(spec) == declare.COMPONENT_KIND_TWO_STATE_MODE:
            _expose_mode(name, spec, exposed)
    return exposed


# ---------------------------------------------------------------------------
# The study, applied on the document
# ---------------------------------------------------------------------------

#: The façade fields a study schema always writes beside the engine fields of
#: an ``ObjMode2S``, with the only values that mean "nothing was said". Any
#: other value belongs to the two-state façade API and is refused, as the
#: engine class refuses it.
_FACADE_PASSTHROUGH = {
    "failure_state": ("occ",),
    "failure_effects": ({}, None),
    "failure_effects_trans": ({}, None),
    "failure_param_name": ([], None),
    "failure_param": ([], None),
    "repair_state": ("rep",),
    "repair_cond": (True, None),
    "repair_effects": ({}, None),
    "repair_effects_trans": ({}, None),
    "repair_param_name": ([], None),
    "repair_param": ([], None),
}

_COMPONENT_KWARGS = ("label", "description", "metadata")
_BEHAVIOURS = ("internal", "external", "external_rep_indep")
_STATE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOGIC_NAMES = {"all": all, "any": any}


class StudyModeError(ValueError):
    """A mode of the study that cannot be declared on this system."""


class _StudyState:
    """What the study application knows of the system while it grows."""

    def __init__(self, declaration: Dict[str, Any]):
        self.components = declaration["components"]
        self.exposed = _exposed(declaration)
        #: ``(target, variable) -> "direct" | "arbiter"``: who holds a value on
        #: a target variable, to refuse a ranked mode beside an unranked one.
        self.holders: Dict[Tuple[str, str], List[Tuple[str, str, Any, Any]]] = {}

    def add(self, name: str, spec: dict) -> None:
        self.components[name] = spec
        self.exposed[name] = Exposed()
        _expose_mode(name, spec, self.exposed)

    def has_attribute(self, component: str, attr: str) -> bool:
        entry = self.exposed.get(component)
        return entry is not None and (attr in entry.variables or attr in entry.states)


def _check_condition(state: _StudyState, cond: Any, default: Optional[str]) -> None:
    """Refuse a condition naming something the system does not hold."""
    if not isinstance(cond, list):
        return

    def walk(element):
        if isinstance(element, list):
            for item in element:
                walk(item)
        elif isinstance(element, dict):
            if "attr" not in element:
                raise StudyModeError(f"Element {element} must have a key 'attr'")
            obj = element.get("obj", default)
            if not obj:
                raise StudyModeError(
                    "'attr' key provided as a string, you must reference the "
                    "related object as the value of key 'obj'"
                )
            if obj not in state.components:
                raise StudyModeError(
                    f"condition names component {obj!r}, "
                    "which the system does not hold"
                )
            if not state.has_attribute(obj, element["attr"]):
                raise StudyModeError(
                    f"{element['attr']} must be a variable or a state or an "
                    f"attribute of {obj}"
                )

    walk(sanitize_cond_format(cond))


def _check_orders(mode_name, targets, behaviour, laws, params, effects, drop_inactive):
    """Refuse what the engine refuses once it knows which orders a mode keeps."""
    for direction in ("occ", "not_occ"):
        if laws[direction] is None:
            which = "occ_law" if direction == "occ" else "not_occ_law"
            raise StudyModeError(
                f"Mode '{mode_name}': no {which} provided. Declare the law with a "
                f"ModeLaw spec, e.g. {{'cls': 'exp', 'rate': ...}}."
            )

    def marked(direction, order):
        entry = params[direction][order - 1]
        values = entry if isinstance(entry, (list, tuple)) else (entry,)
        return any(value is None for value in values)

    def inactive(direction, order):
        if marked(direction, order):
            return True
        value = declare._law_order_value(params[direction][order - 1])
        return not laws[direction].is_active_value(value)

    order_max = len(targets)
    kept = []
    for order in range(1, order_max + 1):
        both_inactive = inactive("occ", order) and inactive("not_occ", order)
        if marked("occ", order) != marked("not_occ", order) and not both_inactive:
            raise StudyModeError(
                f"Mode {mode_name!r}: CC order {order} marks exactly one "
                f"direction inactive (None) while the other is active — mark "
                f"both directions None for an undeclared order, or give both a "
                f"value."
            )
        if not (drop_inactive and both_inactive):
            kept.append(order)

    if behaviour == "internal" and order_max >= 2:
        writers = sum(math.comb(order_max - 1, order - 1) for order in kept)
        both_pulse = sorted(
            set(effects["occ_effects_trans"]) & set(effects["not_occ_effects_trans"])
        )
        if writers >= 2 and both_pulse:
            raise StudyModeError(
                f"Mode {mode_name!r} (behaviour='internal', {order_max} targets, "
                f"CC orders {kept} kept): {writers} combination automata write "
                f"each target. Variables {both_pulse} are pulsed on both faces: "
                f"one combination's repair would clear them while another "
                f"combination covering the same target is still in occ."
            )
    if behaviour == "external_rep_indep" and order_max and inactive("not_occ", 1):
        raise StudyModeError(
            f"behaviour='external_rep_indep' requires the order-1 return "
            f"(repair) law to be active for mode '{mode_name}'. Provide a "
            f"non-zero order-1 parameter (repair_param / not_occ_param)."
        )


def _mode2s_entry(kwargs: Dict[str, Any], state: _StudyState):
    """``(entry, held)`` of one ``ObjMode2S`` of a study, or a refusal.

    ``held`` is what the mode holds on its targets, for the caller to record
    once the mode is in the document.
    """
    kwargs = dict(kwargs)
    mode_name = kwargs.pop("mode_name", None)
    fm_name = kwargs.pop("fm_name", None)
    if fm_name is not None:
        if mode_name is not None and mode_name != fm_name:
            raise StudyModeError(
                f"ObjMode2S: both mode_name={mode_name!r} and its wire alias "
                f"fm_name={fm_name!r} are set — set exactly one."
            )
        mode_name = fm_name
    if not mode_name or not isinstance(mode_name, str):
        raise StudyModeError("ObjMode2S: mode_name must be a non-empty string.")

    occ_cond = kwargs.pop("occ_cond", True)
    failure_cond = kwargs.pop("failure_cond", None)
    if failure_cond is not None and failure_cond is not True:
        if occ_cond is not True:
            raise StudyModeError(
                f"Mode {mode_name!r}: both occ_cond and its wire alias "
                f"failure_cond are set — set exactly one."
            )
        occ_cond = failure_cond

    for key, accepted in _FACADE_PASSTHROUGH.items():
        if key in kwargs:
            value = kwargs.pop(key)
            if not any(value == acc for acc in accepted):
                raise StudyModeError(
                    f"ObjMode2S {mode_name!r}: field {key!r}={value!r} belongs "
                    f"to the two-state ObjFM façade API and has no native "
                    f"meaning. Use the engine fields (occ_* / not_occ_*), or "
                    f"declare the mode with an ObjFM* class."
                )

    targets = kwargs.pop("targets", None)
    target_name = kwargs.pop("target_name", None)
    aut_name = kwargs.pop("aut_name", None)
    behaviour = kwargs.pop("behaviour", "internal")
    occ_state = kwargs.pop("occ_state", "occ")
    not_occ_state = kwargs.pop("not_occ_state", "not_occ")
    occ_law = kwargs.pop("occ_law", None)
    not_occ_law = kwargs.pop("not_occ_law", None)
    not_occ_cond = kwargs.pop("not_occ_cond", True)
    effects = {
        key: copy.deepcopy(kwargs.pop(key, None) or {})
        for key in (
            "occ_effects",
            "occ_effects_trans",
            "not_occ_effects",
            "not_occ_effects_trans",
        )
    }
    param_names = {
        "occ": kwargs.pop("occ_param_name", None),
        "not_occ": kwargs.pop("not_occ_param_name", None),
    }
    params = {
        "occ": kwargs.pop("occ_param", None),
        "not_occ": kwargs.pop("not_occ_param", None),
    }
    parked = {
        "occ_parked_state": kwargs.pop("occ_parked_state", None),
        "not_occ_parked_state": kwargs.pop("not_occ_parked_state", None),
    }
    order_prefix = kwargs.pop("param_name_order_prefix", "__{order}_o_{order_max}")
    trans_prefix = kwargs.pop("trans_name_prefix", "__cc_{target_comb_u}")
    trans_prefix_fun = kwargs.pop("trans_name_prefix_fun", None)
    drop_inactive = kwargs.pop("drop_inactive_automata", True)
    inner_logic = kwargs.pop("cond_inner_logic", all)
    outer_logic = kwargs.pop("cond_outer_logic", any)
    step = kwargs.pop("step", None)
    effects_priority = kwargs.pop("effects_priority", None)

    unknown = [key for key in kwargs if key not in _COMPONENT_KWARGS]
    if unknown:
        raise StudyModeError(
            f"ObjMode2S got unexpected keyword argument(s) {sorted(unknown)}. "
            f"Known extra kwargs: {list(_COMPONENT_KWARGS)}. A typo here would "
            f"otherwise silently build a wrong model."
        )
    if trans_prefix_fun is not None or step is not None:
        raise StudyModeError(
            f"Mode {mode_name!r}: a naming function or a step is an engine "
            f"object no declaration carries"
        )

    for label, value in (
        ("occ_state", occ_state),
        ("not_occ_state", not_occ_state),
        *parked.items(),
    ):
        if value is None and label.endswith("parked_state"):
            continue
        if not isinstance(value, str) or not _STATE_NAME.match(value):
            raise StudyModeError(
                f"Mode {mode_name!r}: {label}={value!r} is not a valid state "
                f"name (identifier-like, regex-safe names only — they are "
                f"interpolated into monitor masks and filter patterns)."
            )
    if occ_state == not_occ_state:
        raise StudyModeError(
            f"Mode {mode_name!r}: occ_state and not_occ_state must be distinct, "
            f"both are {occ_state!r}."
        )

    self_hosted = targets is None
    if effects_priority is not None:
        if isinstance(effects_priority, bool) or not isinstance(effects_priority, int):
            raise StudyModeError(
                f"Mode {mode_name!r}: effects_priority must be an integer, got "
                f"{effects_priority!r}."
            )
        if self_hosted:
            raise StudyModeError(
                f"Mode {mode_name!r}: effects_priority ranks the effects a mode "
                f"holds on its targets; a self-hosted mode (targets=None) has "
                f"none."
            )
    targets = [] if targets is None else targets
    targets = [targets] if isinstance(targets, str) else list(targets)
    if target_name is None and len(targets) == 1:
        target_name = targets[0]
    target_name = target_name or factorize_target_names(targets)
    name = mode_name if self_hosted else f"{target_name}__{mode_name}"

    if behaviour not in _BEHAVIOURS:
        raise StudyModeError(
            f"behaviour must be one of {_BEHAVIOURS}, got '{behaviour}'"
        )
    laws = {
        "occ": parse_mode_law(occ_law, what="occ_law") if occ_law is not None else None,
        "not_occ": (
            parse_mode_law(not_occ_law, what="not_occ_law")
            if not_occ_law is not None
            else None
        ),
    }
    if isinstance(laws["not_occ"], ModeLawInst) and len(targets) > 1:
        raise StudyModeError(
            f"Mode {mode_name!r}: inst on the return direction (not_occ_law) is "
            f"not supported with CC order > 1 (len(targets)={len(targets)}) — "
            f"locked decision, liftable later."
        )
    if isinstance(laws["occ"], ModeLawInst) and isinstance(
        laws["not_occ"], ModeLawInst
    ):
        if (
            any(
                p1 == 1 and p2 == 1
                for p1, p2 in zip(laws["occ"].values(), laws["not_occ"].values())
            )
            and occ_cond is True
            and not_occ_cond is True
        ):
            raise StudyModeError(
                f"Mode {mode_name!r}: inst/inst with prob 1 on both directions "
                f"and trivially-true conditions is a certain same-instant "
                f"livelock."
            )

    overlap = sorted(
        (set(effects["occ_effects"]) & set(effects["occ_effects_trans"]))
        | (set(effects["not_occ_effects"]) & set(effects["not_occ_effects_trans"]))
    )
    if overlap:
        raise StudyModeError(
            f"Mode {mode_name!r}: variables {overlap} are driven by BOTH a "
            f"state-based effect and a trans-based effect on the same face"
        )
    for target in targets:
        if target not in state.components:
            raise StudyModeError(
                f"Mode '{mode_name}': target component {target!r} not found in "
                f"the system. Create the targets before the mode."
            )
        variables = state.exposed[target].variables
        for kind, key in (
            ("failure_effects", "occ_effects"),
            ("repair_effects", "not_occ_effects"),
            ("failure_effects_trans", "occ_effects_trans"),
            ("repair_effects_trans", "not_occ_effects_trans"),
        ):
            for var in effects[key]:
                if var not in variables:
                    raise StudyModeError(
                        f"{kind}: variable {var!r} not found on target "
                        f"{target!r} (FM {mode_name!r}). Check the spelling "
                        f"against the target's declared variables."
                    )

    if self_hosted:
        if any(param_names.values()) or any(params.values()):
            raise StudyModeError(
                f"Mode {mode_name!r}: a self-hosted mode (targets=None) carries "
                f"no per-order parameter"
            )
    else:
        if aut_name is not None:
            raise StudyModeError(
                f"Mode {mode_name!r}: aut_name is only meaningful in self-hosted "
                f"mode (targets=None); targeted modes name their automata after "
                f"mode_name + the CC suffix."
            )
        for direction in ("occ", "not_occ"):
            law = laws[direction]
            names = param_names[direction] or []
            names = [names] if isinstance(names, str) else list(names)
            if not names and law is not None:
                names = [f"{direction}_{field}" for field in law.param_fields]
            param_names[direction] = names
            current = params[direction]
            current = [] if current is None else current
            current = [current] if not isinstance(current, list) else list(current)
            if len(targets) - len(current) > 0:
                which = "occ_law" if direction == "occ" else "not_occ_law"
                if law is not None and not current:
                    values = law.values()
                    if len(values) != len(targets) and not (
                        len(values) == 1 and len(targets) == 1
                    ):
                        raise StudyModeError(
                            f"Mode '{mode_name}': {which} vector has "
                            f"{len(values)} entries but the mode has "
                            f"{len(targets)} targets (strict length, no silent "
                            f"padding)."
                        )
                    current = list(values)
                else:
                    raise StudyModeError(
                        f"Mode '{mode_name}': {len(current)} {direction} "
                        f"parameter(s) provided for {len(targets)} targets "
                        f"(strict length, no silent padding)."
                    )
            elif len(targets) - len(current) < 0:
                raise StudyModeError(
                    f"Mode of order {len(targets)} but you provide "
                    f"{len(current)} {direction} parameters: {current}"
                )
            params[direction] = current

    if not self_hosted:
        _check_orders(
            mode_name, targets, behaviour, laws, params, effects, drop_inactive
        )
    for label, value in (
        ("cond_inner_logic", inner_logic),
        ("cond_outer_logic", outer_logic),
    ):
        if value not in (all, any):
            raise StudyModeError(
                f"Mode {mode_name!r}: {label} is a function of the engine, all "
                f"or any, and {value!r} is neither"
            )

    # Conditions are compiled per target when the mode builds its automata.
    for cond in (occ_cond, not_occ_cond):
        if isinstance(cond, list):
            for target in targets or [None]:
                _check_condition(state, cond, target)

    kind = "direct" if effects_priority is None else "arbiter"
    held = [
        ((target, var), (kind, mode_name, effects_priority, value))
        for target in targets
        for key in ("occ_effects", "not_occ_effects")
        for var, value in effects[key].items()
    ]
    for (target, var), (_kind, _owner, rank, value) in held:
        for other_kind, other, other_rank, other_value in state.holders.get(
            (target, var), ()
        ):
            if other_kind != kind:
                raise StudyModeError(
                    f"Variable {var!r}: mode {mode_name!r} and mode {other!r} "
                    f"both hold a value on it, one with effects_priority and "
                    f"the other without. Their clamps would fight each other: "
                    f"declare effects_priority on both."
                )
            if kind == "arbiter" and other_rank == rank and other_value != value:
                raise StudyModeError(
                    f"Variable {var!r}: modes {other!r} and {mode_name!r} hold "
                    f"different values ({other_value!r} and {value!r}) at the "
                    f"same effects_priority {rank}. The rank cannot decide "
                    f"between them: give them different ranks or the same value."
                )

    where = f"Failure mode {name}"
    vocabulary = declare.MODE_VOCABULARY
    values = {
        "mode_name": mode_name,
        "targets": None if self_hosted else targets,
        "target_name": target_name,
        "aut_name": aut_name if aut_name not in (None, mode_name) else None,
        "behaviour": behaviour,
        "occ_state": occ_state,
        "occ_cond": copy.deepcopy(occ_cond),
        "occ_param_name": [] if self_hosted else param_names["occ"],
        "occ_param": [] if self_hosted else params["occ"],
        "not_occ_state": not_occ_state,
        "not_occ_cond": copy.deepcopy(not_occ_cond),
        "not_occ_param_name": [] if self_hosted else param_names["not_occ"],
        "not_occ_param": [] if self_hosted else params["not_occ"],
        "param_name_order_prefix": order_prefix,
        "trans_name_prefix": trans_prefix,
        "trans_name_prefix_fun": None,
        "step": None,
        "effects_priority": effects_priority,
        **effects,
        **parked,
    }
    for direction in ("occ", "not_occ"):
        law = laws[direction]
        if law is None:
            values[f"{direction}_law"] = None
            continue
        dump = law.model_dump()
        if values[f"{direction}_param"]:
            fields = getattr(law, "param_fields", (law.param_field,))
            for index, field in enumerate(fields):
                dump[field] = [
                    declare._law_order_value(entry, index)
                    for entry in values[f"{direction}_param"]
                ]
        values[f"{direction}_law"] = dump

    entry = {
        "name": name,
        declare.COMPONENT_KIND_KEY: declare.COMPONENT_KIND_TWO_STATE_MODE,
        "cls": "ObjMode2S",
    }
    for key, _attribute in vocabulary.fields:
        value = values[key]
        if key in vocabulary.defaults and value == vocabulary.defaults[key]:
            continue
        entry[key] = declare._checked_declaration(value, f"{where}, {key}")
    for key, value, default in (
        ("cond_inner_logic", inner_logic, all),
        ("cond_outer_logic", outer_logic, any),
    ):
        if value is not default:
            entry[key] = declare._mode_logic_name(value, key, where)

    if len(targets) >= 2:
        probe = dict(entry, drop_inactive_automata=drop_inactive)
        _order_max, kept = _mode_orders(probe)
        automata = sum(math.comb(len(targets), order) for order in kept)
        if automata == 2 ** len(targets) - 1:
            entry["drop_inactive_automata"] = False

    for key in ("label", "description"):
        if kwargs.get(key) is not None:
            entry[key] = kwargs[key]
    if entry.get("label") == name:
        entry.pop("label")
    if entry.get("description") == entry.get("label", name):
        entry.pop("description", None)
    if kwargs.get("metadata"):
        entry["metadata"] = declare._checked_declaration(
            kwargs["metadata"], f"{where}, metadata"
        )
    return entry, held


def _facade_entry(clsname: str, kwargs: Dict[str, Any], state: _StudyState):
    """``(entry, held)`` of one facade mode of a study (``ObjFMExp``, ``ObjFMDelay``).

    The mode is checked as the ``ObjMode2S`` it stands for -- same targets,
    same effects, same conditions, the law its class carries -- and written
    back in the facade spelling its own constructor takes.
    """
    law, field, default_names = _FACADE_LAWS[clsname]
    kwargs = dict(kwargs)
    if kwargs.get("effects_priority") is not None:
        raise StudyModeError(
            f"Mode {kwargs.get('fm_name')!r}: effects_priority is an ObjMode2S "
            f"engine field; {clsname} wires its effects through the historical "
            f"ObjFM hooks. Declare the mode as ObjMode2S."
        )
    targets = kwargs.get("targets") or []
    targets = [targets] if isinstance(targets, str) else list(targets)
    engine = {}
    for facade_key, engine_key in _FACADE_KEYS:
        if facade_key in kwargs:
            engine[engine_key] = kwargs.pop(facade_key)
    engine.setdefault("occ_state", "occ")
    engine.setdefault("not_occ_state", "rep")
    spelled = {}
    for direction, default_name in zip(("occ", "not_occ"), default_names):
        if not engine.get(f"{direction}_param_name"):
            engine[f"{direction}_param_name"] = [default_name]
        vector = engine.get(f"{direction}_param")
        vector = [] if vector is None else vector
        vector = list(vector) if isinstance(vector, list) else [vector]
        # A facade vector shorter than the targets is completed with zeros,
        # where the engine class asks for every order.
        vector += [[0]] * (len(targets) - len(vector))
        spelled[direction] = vector
        scalars = [declare._law_order_value(entry) for entry in vector]
        engine[f"{direction}_param"] = scalars
        engine[f"{direction}_law"] = {"cls": law, field: scalars}
    generic, held = _mode2s_entry({**kwargs, **engine}, state)

    vocabulary = declare.FAILURE_MODE_VOCABULARY
    engine_defaults = declare.MODE_VOCABULARY.defaults
    engine_key_of = dict(_FACADE_KEYS)
    values = {
        "failure_param": spelled["occ"],
        "repair_param": spelled["not_occ"],
    }
    entry = {
        "name": generic["name"],
        declare.COMPONENT_KIND_KEY: declare.COMPONENT_KIND_TWO_STATE_MODE,
        "cls": clsname,
    }
    for key, _attribute in vocabulary.fields:
        engine_key = engine_key_of.get(key, key)
        if key in values:
            value = values[key]
        elif engine_key in generic:
            value = generic[engine_key]
        elif engine_key in engine_defaults:
            value = engine_defaults[engine_key]
        else:
            continue
        if key in vocabulary.defaults and value == vocabulary.defaults[key]:
            continue
        entry[key] = value
    for key in (
        "cond_inner_logic",
        "cond_outer_logic",
        "drop_inactive_automata",
        "label",
        "description",
        "metadata",
    ):
        if key in generic:
            entry[key] = generic[key]
    return entry, held


def _event_entry(payload: Dict[str, Any], state: _StudyState) -> dict:
    """The declaration entry of one event of a study."""
    payload = dict(payload)
    payload.pop("cls", None)
    name = payload.pop("name")
    where = f"Failure mode {name}"
    cond = sanitize_cond_format(payload.pop("cond"))
    if isinstance(cond, list):
        _check_condition(state, cond, None)
    values = {
        "name": name,
        "cond": cond,
        "cond_operator": payload.pop("cond_operator", "=="),
        "cond_value": payload.pop("cond_value", True),
        "tempo_occ": payload.pop("tempo_occ", 0),
        "tempo_not_occ": payload.pop("tempo_not_occ", 0),
        "event_aut_name": payload.pop("event_aut_name", "ev"),
        "occ_state_name": payload.pop("occ_state_name", "occ"),
        "not_occ_state_name": payload.pop("not_occ_state_name", "not_occ"),
    }
    for label in ("event_aut_name", "occ_state_name", "not_occ_state_name"):
        if not isinstance(values[label], str) or not _STATE_NAME.match(values[label]):
            raise StudyModeError(
                f"Event {name!r}: {label}={values[label]!r} is not a valid name "
                f"(identifier-like, regex-safe names only)."
            )
    if values["occ_state_name"] == values["not_occ_state_name"]:
        raise StudyModeError(
            f"Event {name!r}: occ_state_name and not_occ_state_name must be "
            f"distinct, both are {values['occ_state_name']!r}."
        )
    if name in state.components:
        raise StudyModeError(
            f"Event {name!r}: a component of that name already exists in the system"
        )
    vocabulary = declare.EVENT_VOCABULARY
    entry = {
        "name": name,
        declare.COMPONENT_KIND_KEY: declare.COMPONENT_KIND_TWO_STATE_MODE,
        "cls": "ObjEvent",
    }
    for key, _attribute in vocabulary.fields:
        value = values[key]
        if key in vocabulary.defaults and value == vocabulary.defaults[key]:
            continue
        entry[key] = declare._checked_declaration(value, f"{where}, {key}")
    for key, default in (("inner_logic", all), ("outer_logic", any)):
        value = payload.pop(key, default)
        if isinstance(value, str):
            if value not in _LOGIC_NAMES:
                raise StudyModeError(
                    f"ObjEvent {key} must be one of {sorted(_LOGIC_NAMES)}, got "
                    f"{value!r}."
                )
            value = _LOGIC_NAMES[value]
        if value is not default:
            entry[key] = declare._mode_logic_name(value, key, where)
    return entry


_ATTRIBUTE_LISTS = {"VAR": "variables", "ST": "states", "AUT": "automata"}


def _anchored_literal(pattern: str) -> Optional[str]:
    """The one name ``pattern`` designates, when it is an anchored literal.

    A study generated from a model names each thing it observes exactly,
    ``^`` + the escaped name + ``$``. Read as the name it is, such a pattern
    costs a lookup instead of a scan of every component, which is what keeps
    ten thousand indicators on ten thousand components out of a quadratic walk.
    """
    if len(pattern) < 2 or pattern[0] != "^" or pattern[-1] != "$":
        return None
    inner = pattern[1:-1]
    name = re.sub(r"\\(.)", r"\1", inner)
    return name if re.escape(name) == inner else None


def _indicators(study_indicators: List[Any], state: _StudyState) -> List[dict]:
    """The indicators a study asks for, expanded on what the system exposes."""
    by_name: Dict[str, dict] = {}
    for spec in study_indicators:
        payload = spec.model_dump() if hasattr(spec, "model_dump") else dict(spec)
        if not payload.pop("enabled", True):
            continue
        component_pattern = payload.pop("component", ".*")
        attr_pattern = payload.pop("attr_name", ".*")
        attr_type = payload.pop("attr_type")
        if attr_type not in _ATTRIBUTE_LISTS:
            raise ValueError(f"{attr_type} not supported by PyCATSHOO")
        stats = payload.pop("stats", None)
        exact_component = _anchored_literal(component_pattern)
        if exact_component is not None:
            candidates = (
                [exact_component] if exact_component in state.components else []
            )
        else:
            candidates = [
                component
                for component in sorted(state.components)
                if re.fullmatch(component_pattern, component)
            ]
        exact_attr = _anchored_literal(attr_pattern)
        for component in candidates:
            names = getattr(state.exposed[component], _ATTRIBUTE_LISTS[attr_type])
            if exact_attr is not None:
                matched = [exact_attr] if exact_attr in names else []
            else:
                matched = [
                    attr for attr in sorted(names) if re.search(attr_pattern, attr)
                ]
            for attr in matched:
                fields = {
                    "component": component,
                    "attr_type": attr_type,
                    "attr_name": attr,
                    "stats": list(stats) if stats is not None else [],
                    **payload,
                }
                if "measure" in fields and not isinstance(fields["measure"], str):
                    raise ValueError(
                        f"indicator on {component}.{attr}: measure must be a "
                        f"string, got {fields['measure']!r}"
                    )
                pattern = fields.get("name_pattern")
                if pattern is None:
                    pattern = "{component}_{attr_name}"
                    if fields.get("measure"):
                        pattern += "_{measure}"
                if fields.get("name") is None:
                    fields["name"] = pattern.format(**fields)
                if fields.get("label") is None:
                    fields["label"] = fields["name"]
                if fields.get("description") is None:
                    fields["description"] = fields["label"]
                fields.setdefault("unit", "")
                fields.setdefault("measure", "value")
                fields.setdefault("operator", "==")
                entry = {
                    key: fields[key]
                    for key in declare._INDICATOR_COMMON_KEYS
                    + ("attr_name", "attr_type")
                    if fields.get(key) is not None
                }
                entry["kind"] = "PycAttrIndicator"
                by_name[fields["name"]] = entry
    return sorted(by_name.values(), key=lambda entry: str(entry.get("name") or ""))


def _apply_mode_param_override(override: Any, state: _StudyState) -> None:
    """Replace one direction's per-order parameters of a declared mode."""

    entry = state.components.get(override.fm_name)
    if entry is None or declare.component_kind(entry) != (
        declare.COMPONENT_KIND_TWO_STATE_MODE
    ):
        raise StudyModeError(f"failure mode {override.fm_name!r} not found")
    direction = "occ" if override.field == "failure_param" else "not_occ"
    facade = entry.get("cls") in _FACADE_LAWS
    view = _engine_view(entry)
    names = view.get(f"{direction}_param_name")
    targets = entry.get("targets")
    if not isinstance(names, list) or not names:
        raise StudyModeError(
            f"mode exposes no {direction}_param_name list — its laws are not "
            f"bound to overridable parameter variables"
        )
    if not isinstance(targets, list) or not targets:
        raise StudyModeError(
            "mode has no targets — it carries no per-order parameter variables "
            "to override"
        )
    values = list(override.value)
    if len(values) != len(targets):
        raise StudyModeError(
            f"expected {len(targets)} per-order entries, got {len(values)}"
        )
    law = parse_mode_law(view[f"{direction}_law"], what=f"{direction}_law")
    for order, value in enumerate(values, start=1):
        scalars = list(value) if isinstance(value, (tuple, list)) else [value]
        if len(scalars) != len(names):
            raise StudyModeError(
                f"order {order}: expected {len(names)} value(s) for parameters "
                f"{names}, got {len(scalars)}"
            )
        if isinstance(law, ModeLawInst) and order in _mode_orders(entry)[1]:
            before = declare._law_order_value(view[f"{direction}_param"][order - 1])
            try:
                was_certain = float(before) == 1.0
                certain = float(scalars[0]) == 1.0
            except (TypeError, ValueError):
                continue
            if was_certain != certain:
                raise StudyModeError(
                    f"order {order}: a draw built certain (probability 1) "
                    f"cannot become random after construction, nor the reverse"
                )
    if facade:
        # A facade mode carries its law in its class: the vector is the whole
        # of what it declares.
        entry[override.field] = values
        return
    entry[f"{direction}_param"] = values
    dump = dict(entry[f"{direction}_law"])
    fields = getattr(law, "param_fields", (law.param_field,))
    for index, field in enumerate(fields):
        dump[field] = [declare._law_order_value(value, index) for value in values]
    entry[f"{direction}_law"] = dump


class PackageDeclarationError(ValueError):
    """A run package no declaration describes, with every reason at once.

    ``refusals`` holds ``(mode name, reason)`` for each mode of the study that
    cannot be declared on the system the export describes.
    """

    def __init__(self, refusals: List[Tuple[str, str]]):
        self.refusals = list(refusals)
        lines = "; ".join(f"{name!r}: {reason}" for name, reason in self.refusals)
        super().__init__(
            f"{len(self.refusals)} failure mode(s) of the study cannot be "
            f"declared on this system, so the package has no declaration -- "
            f"{lines}"
        )


def apply_study(
    declaration: Dict[str, Any], study: Any, *, logger: Any = None
) -> Dict[str, Any]:
    """Declare what a study adds to a system: modes, events, indicators.

    ``study`` is a ``cod3s.specs.study_yaml.StudyYaml`` or the mapping it
    validates. The sections are applied in the order a run applies them:
    failure modes, events, indicators, then the value changes. Targets
    configure a run and are not declared.

    **A mode that cannot be declared refuses the whole package**, by name and
    with every such mode listed at once (:class:`PackageDeclarationError`).
    The reading that goes through a live system logs the failure and goes on,
    leaving in the system the half-built component the engine had already
    registered; its declaration then carries a mode whose parameters are null,
    which every engine refuses when it builds from the document. The outcome
    is the same -- no run -- and it is reached here before any engine, with the
    cause. ``muscadet.conformance`` records the difference.

    Raises
    ------
    PackageDeclarationError
        When a mode of the study cannot be declared on this system.
    StudyModeError
        When an event of the study cannot be declared.
    ValueError
        When an indicator names an attribute type no engine lists.
    """
    if not isinstance(study, StudyYaml):
        study = StudyYaml.model_validate(study)
    declaration = copy.deepcopy(declaration)
    state = _StudyState(declaration)
    refusals: List[Tuple[str, str]] = []

    for spec in study.failure_modes:
        if not getattr(spec, "enabled", True):
            continue
        try:
            fields = spec.model_dump(exclude={"cls", "enabled"})
            if spec.cls == "ObjMode2S":
                entry, held = _mode2s_entry(fields, state)
            elif spec.cls in _FACADE_LAWS:
                entry, held = _facade_entry(spec.cls, fields, state)
            else:
                raise StudyModeError(
                    f"mode class {spec.cls!r} has no declaration built from "
                    f"data; a study declares its modes as ObjMode2S, or as one "
                    f"of {sorted(_FACADE_LAWS)}"
                )
            if entry["name"] in state.components:
                raise StudyModeError(
                    f"component {entry['name']!r} already exists in the system"
                )
            state.add(entry["name"], entry)
        except ValueError as err:
            # StudyModeError, a law the schema refuses, a field no document
            # carries: every refusal is collected, then raised together.
            refusals.append((spec.fm_name, str(err)))
            if logger is not None:
                logger.error(f"FM {spec.fm_name!r} (cls={spec.cls}) failed: {err}")
            continue
        for key, record in held:
            state.holders.setdefault(key, []).append(record)
    if refusals:
        raise PackageDeclarationError(refusals)

    for spec in study.events:
        payload = spec.model_dump()
        if not payload.pop("enabled", True):
            continue
        entry = _event_entry(payload, state)
        state.add(entry["name"], entry)

    declaration["indicators"] = _indicators(study.indicators, state)

    overrides = study.attribute_overrides
    if overrides is not None:
        for override in overrides.failure_mode_param:
            try:
                _apply_mode_param_override(override, state)
            except (
                Exception
            ) as err:  # noqa: BLE001 - skipped and named, as on a live system
                message = f"override: {override.fm_name}.{override.field} failed: {err}"
                if logger is not None:
                    logger.error(message)
                else:
                    logging.getLogger(__name__).warning(message)

    return declare._checked_document(declaration, "$", declare.SystemSpecError)


def package_declaration(
    export: Dict[str, Any], study: Any = None, *, logger: Any = None, **options: Any
) -> Dict[str, Any]:
    """The declaration of a run package: the export, then the study on top.

    ``options`` are those of :func:`export_declaration`. Without a study, the
    document is the export's own.
    """
    declaration = export_declaration(export, **options)
    if study is None:
        return declaration
    return apply_study(declaration, study, logger=logger)
