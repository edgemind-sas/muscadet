"""Import plugin for COD3S Platform model exports.

Translates a JSON payload produced by the COD3S Platform
(``GET /modelisation/{name}/export?include_kb=true``) into a
``muscadet.System`` instance ready to be consumed by ``cod3s-isimu``
or ``cod3s.simulate()`` (Monte Carlo).

The module is split into two layers :

* **Pure parse layer** — :func:`parse_platform_export` and the
  ``*Spec`` dataclasses. Pure ``dict``-in / dataclass-out, no
  ``muscadet`` runtime imports, no PyCATSHOO dependency. Designed
  for extensive unit testing without the native libraries.
* **Apply layer** — :func:`apply_to_system` and the public entry
  point :func:`system_from_export`. Consumes the parse output and
  drives ``muscadet.System.add_component`` /
  ``ObjFlow.add_flow_in`` / ``add_flow_out`` /
  ``System.connect_flow``. Requires PyCATSHOO at import time of
  ``muscadet.System`` (lazy-imported here).

Usage::

    import json
    from muscadet.importers.cod3s_platform import system_from_export

    with open("dil_v2_export.json") as f:
        payload = json.load(f)
    system = system_from_export(payload)
    system.isimu_start()  # ready to drive interactive simulation

Phase 1 scope : topology only (components + flows in/out + connections).
Failure modes, business attributes wiring, and indicators are
explicitly out of scope and deferred to later phases.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class Cod3sPlatformImportError(ValueError):
    """Domain error raised by the COD3S Platform importer.

    Subclass of :class:`ValueError` so callers using the broader
    ``except ValueError`` form continue to catch import failures —
    but the dedicated class lets stricter callers distinguish
    converter errors from generic value errors.
    """


# ---------------------------------------------------------------------------
# Pure data structures (parse layer output)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlowSpec:
    """One flow declaration parsed from a KB component template.

    Mirrors the ``muscadet`` flow primitives but stays a pure data
    object so the parse layer doesn't pull in the runtime.

    P1.6 — instance overrides : when the source model carries an
    ``instance.attribute`` for this flow with role ``logic`` or ``init``,
    the corresponding fields below are overridden by the parse layer
    so the apply layer sees the effective configuration directly.
    """

    name: str
    direction: Literal["input", "output"]
    # For inputs : ``'or'`` (default), ``'and'``, or an integer ``k``
    # (at-least-k). For outputs : the ``var_prod_cond`` nested-list
    # expression carried in the KB JSON, e.g. ``[["A"], ["B", "C"]]``.
    logic: Union[str, int, list]
    # Outputs only — meaningful when direction == 'output'.
    logic_inner_mode: str = "or"
    negate: bool = False
    # P1.6 — initial value of ``var_prod`` for an output flow, set by
    # the parse layer when an ``instance.attribute(role=init)`` override
    # exists on the model component. ``None`` means "leave the muscadet
    # default" (False — ``var_prod`` starts off, propagation kicks in
    # from ``var_prod_cond`` inputs at t=0+).
    init_value: Optional[bool] = None


@dataclass(frozen=True)
class ComponentSpec:
    """One component instance parsed from the model + KB pair."""

    # Platform UUID — kept separately from the human display name so
    # connections (which reference UUIDs) can be resolved without
    # ambiguity even when display names collide pre-validation.
    id: str
    # Display name — becomes the muscadet component name.
    name: str
    # KB class identifier — preserved in metadata even though all
    # components are instantiated as the generic ``muscadet.ObjFlow``,
    # so downstream filters can still group / filter by class.
    class_name: str
    # Resolved flow specs (input + output) for this instance, derived
    # from the referenced KB template.
    flows: List[FlowSpec]
    # Free-form bag of preserved fields (e.g., the raw attributes
    # list from the model document, the source KB ref, ...). Not
    # consumed by the apply layer in P1 ; available for downstream.
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectionSpec:
    """One inter-component connection parsed from the model.

    Stored using component **display names** (not UUIDs) because
    ``muscadet.System.connect_flow`` expects display names. The
    parse layer resolves UUIDs against the components map so the
    apply layer doesn't need to know about UUIDs at all.
    """

    source_component: str
    target_component: str
    flow_name: str  # short interface name shared by both ends


@dataclass(frozen=True)
class ImporterContext:
    """Full result of the parse layer — input to the apply layer."""

    system_name: str
    components: List[ComponentSpec]
    connections: List[ConnectionSpec]
    # ``{name, version}`` dict of the source KB (for traceability).
    source_kb: Dict[str, Any]
    # Free-form bag for additional preservation (description, owner,
    # export_version, ...). Not used in P1 by the apply layer.
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Format detection + KB resolution
# ---------------------------------------------------------------------------


def _detect_payload_shape(payload: Dict[str, Any]) -> str:
    """Identify the input shape : full export vs canonical test dict.

    - ``'platform_export'`` : the shape produced by
      ``GET /modelisation/{name}/export?include_kb=true``. Has
      ``model`` + ``kb_embedded`` at top level.
    - ``'canonical'`` : a flat ``{model, kb}`` dict. Convenient for
      tests that don't want to wrap their KB in ``kb_embedded``.

    Raises :class:`Cod3sPlatformImportError` for unrecognized shapes.
    """
    if not isinstance(payload, dict):
        raise Cod3sPlatformImportError(
            f"Payload must be a dict, got {type(payload).__name__}"
        )
    if "model" not in payload:
        raise Cod3sPlatformImportError(
            "Payload missing 'model' key; expected COD3S Platform export "
            "shape with at least {model: {...}}"
        )
    if "kb_embedded" in payload and isinstance(payload["kb_embedded"], dict):
        return "platform_export"
    if "kb" in payload and isinstance(payload["kb"], dict) and (
        "component_templates" in payload["kb"]
    ):
        return "canonical"
    raise Cod3sPlatformImportError(
        "Payload contains no resolvable KB. Expected either 'kb_embedded' "
        "(full Platform export) or a 'kb' dict carrying 'component_templates' "
        "(canonical test shape)."
    )


def _resolve_kb(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return the KB dict regardless of payload shape (export vs canonical)."""
    shape = _detect_payload_shape(payload)
    return payload["kb_embedded"] if shape == "platform_export" else payload["kb"]


# ---------------------------------------------------------------------------
# Parse layer
# ---------------------------------------------------------------------------


def _parse_interface(interface: Dict[str, Any]) -> FlowSpec:
    """Translate one KB interface dict into a :class:`FlowSpec`.

    Post-COD3S Platform 3.0.0 schema (cf. plan P1.5 G4 task 16) :

    - **input** ports use ``input_logic`` ('and' / 'or' / int k for
      k-of-n aggregation of incoming flows). Default 'or' if missing.
    - **output** ports use ``prod_cond`` (DNF list-of-lists, var_prod_cond
      muscadet propagation). Defaults to empty list (unconditional).
      Plus ``logic_inner_mode`` ('or' default) and ``negate`` (False
      default).

    The legacy ambiguous ``logic`` field is rejected outright (no
    fallback) per Resolved Arbitrations #4 — re-export from a
    post-3.0.0 platform instance to upgrade the file.
    """
    name = interface.get("name")
    if not name:
        raise Cod3sPlatformImportError(
            f"Interface missing 'name' field: {interface!r}"
        )
    if "logic" in interface:
        raise Cod3sPlatformImportError(
            f"Interface {name!r}: legacy 'logic' field is no longer supported. "
            f"Re-export from a post-3.0.0 COD3S Platform instance "
            f"(use prod_cond for output, input_logic for input)."
        )
    port_type = (interface.get("port_type") or {}).get("general")
    if port_type not in ("input", "output"):
        raise Cod3sPlatformImportError(
            f"Interface {name!r}: unsupported port_type.general={port_type!r} "
            "(expected 'input' or 'output')"
        )
    if port_type == "input":
        return FlowSpec(
            name=name,
            direction="input",
            logic=interface.get("input_logic", "or"),
        )
    # output
    return FlowSpec(
        name=name,
        direction="output",
        logic=interface.get("prod_cond", []),
        logic_inner_mode=interface.get("logic_inner_mode", "or"),
        negate=bool(interface.get("negate", False)),
    )


def _build_kb_lookup(kb: Dict[str, Any]) -> Dict[str, List[FlowSpec]]:
    """Compute ``{class_name: [FlowSpec, ...]}`` from the KB dict.

    Iterates ``kb['component_templates']`` and parses each template's
    interfaces into FlowSpec instances. The resulting map is a small
    dictionary (~tens of classes for realistic KBs) suitable for
    O(1) lookup in the component pass.
    """
    templates = kb.get("component_templates") or {}
    out: Dict[str, List[FlowSpec]] = {}
    for class_name, template in templates.items():
        ifaces = template.get("interfaces") or {}
        # ``interfaces`` is a dict keyed by ``{name}__{direction}``
        # but the keys are not authoritative — we read ``port_type.general``
        # for direction. Iterating ``.values()`` is sufficient.
        out[class_name] = [_parse_interface(iface) for iface in ifaces.values()]
    return out


def _parse_input_logic_value(raw: Any, *, flow_name: str, comp_name: str) -> Union[str, int]:
    """Coerce an instance override of an input ``logic`` attribute.

    Backend AttributeTemplate for role=logic declares type='string'
    (cf. plan G2 sync_v2). The platform persists ``'and'`` / ``'or'``
    as plain strings and ``int k`` (k-of-n) as a decimal string ``'2'``,
    ``'5'``, ... — the muscadet ``add_flow_in(logic=...)`` API expects
    a real Python int for k-of-n, hence the str→int coercion here.
    """
    if isinstance(raw, str):
        if raw in ("and", "or"):
            return raw
        # Decimal-string k-of-n
        try:
            k = int(raw)
        except ValueError as e:
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}, flow {flow_name!r}: invalid logic "
                f"override {raw!r} (expected 'and', 'or', or an integer)"
            ) from e
        if k < 1:
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}, flow {flow_name!r}: k-of-n logic "
                f"must be ≥ 1 (got {k})"
            )
        return k
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise Cod3sPlatformImportError(
            f"Component {comp_name!r}, flow {flow_name!r}: invalid logic "
            f"override of type {type(raw).__name__} (expected str or int)"
        )
    if raw < 1:
        raise Cod3sPlatformImportError(
            f"Component {comp_name!r}, flow {flow_name!r}: k-of-n logic "
            f"must be ≥ 1 (got {raw})"
        )
    return raw


def _build_overrides_index(
    attributes: List[Dict[str, Any]],
) -> Dict[Tuple[str, str], Any]:
    """Index a model component's instance attributes by ``(name, role)``.

    Skips entries without a role (legacy / manual attributes) since
    the apply layer only consumes the ``logic`` (input) and ``init``
    (output) facets — the observable roles ``availability`` / ``state``
    are runtime variables, not configuration overrides.

    Drops entries whose ``value`` is ``None`` : an absent value means
    "use the KB default", same as no override at all.
    """
    out: Dict[Tuple[str, str], Any] = {}
    for attr in attributes or []:
        if not isinstance(attr, dict):
            continue
        name = attr.get("name")
        role = attr.get("role")
        value = attr.get("value")
        if not name or not role:
            continue
        if role not in ("logic", "init"):
            # Observable roles (availability, state) — runtime, ignored.
            continue
        if value is None:
            continue
        out[(name, role)] = value
    return out


def _apply_instance_overrides(
    flows: List[FlowSpec],
    overrides: Dict[Tuple[str, str], Any],
    *,
    comp_name: str,
) -> List[FlowSpec]:
    """Return a new flow list with instance overrides folded in.

    For each flow, look up overrides on its ``(name, role)`` pair :

    * role=logic → replace the input flow's ``logic``
    * role=init → set the output flow's ``init_value``

    Rejects role/direction mismatches (logic on output, init on input)
    with a clear error — these would indicate a corrupted snapshot
    that the platform validators should have caught.

    The role-to-direction mapping disambiguates the case where an
    interface name appears on both an input AND an output port of the
    same component (e.g. DIL ``Logique_Sorties.S_NDILH_PPz_Qx``).
    Indexing by ``(name, direction)`` rather than ``name`` alone
    avoids accidentally collapsing the two ports.
    """
    # Preserve original order; mutate by index when an override matches.
    out: List[FlowSpec] = list(flows)
    for (name, role), value in overrides.items():
        target_direction = "input" if role == "logic" else "output"
        idx = next(
            (
                i
                for i, f in enumerate(out)
                if f.name == name and f.direction == target_direction
            ),
            -1,
        )
        if idx < 0:
            # Either the flow is gone (stale override) or it exists with
            # the OPPOSITE direction (snapshot corruption — surface it).
            opposite_idx = next(
                (i for i, f in enumerate(out) if f.name == name),
                -1,
            )
            if opposite_idx < 0:
                logger.debug(
                    "Ignoring stale instance override on %s/%s: flow not in current KB",
                    comp_name, name,
                )
                continue
            other = out[opposite_idx]
            if role == "logic":
                raise Cod3sPlatformImportError(
                    f"Component {comp_name!r}: instance override role=logic "
                    f"on non-input flow {name!r} (direction={other.direction})"
                )
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}: instance override role=init "
                f"on non-output flow {name!r} (direction={other.direction})"
            )
        flow = out[idx]
        if role == "logic":
            new_logic = _parse_input_logic_value(value, flow_name=name, comp_name=comp_name)
            out[idx] = replace(flow, logic=new_logic)
        else:  # role == "init"
            out[idx] = replace(flow, init_value=bool(value))
    return out


def _parse_components(
    components_raw: Dict[str, Dict[str, Any]],
    kb_lookup: Dict[str, List[FlowSpec]],
) -> List[ComponentSpec]:
    """Translate the model components dict into a list of ComponentSpec.

    Validates that each component's ``class_name`` is known in the
    KB lookup. Folds instance overrides (attributes with role=logic
    or role=init) into the FlowSpec list so the apply layer sees the
    effective configuration directly. Preserves the raw ``attributes``
    list in metadata for downstream traceability.
    """
    seen_names: set[str] = set()
    out: List[ComponentSpec] = []
    for cid, comp in (components_raw or {}).items():
        name = comp.get("name")
        class_name = comp.get("class_name")
        if not name:
            raise Cod3sPlatformImportError(
                f"Component {cid!r} missing 'name' field"
            )
        if not class_name:
            raise Cod3sPlatformImportError(
                f"Component {name!r} ({cid}) missing 'class_name' field"
            )
        if class_name not in kb_lookup:
            raise Cod3sPlatformImportError(
                f"Component {name!r} references unknown class {class_name!r} "
                f"(known classes: {sorted(kb_lookup)})"
            )
        if name in seen_names:
            raise Cod3sPlatformImportError(
                f"Duplicate component name {name!r}; PyCATSHOO uses display "
                "names as ids and cannot disambiguate collisions."
            )
        seen_names.add(name)
        # Instance overrides : attributes with role=logic (input) or
        # role=init (output) replace the KB defaults for THIS instance.
        instance_attrs = list(comp.get("attributes") or [])
        overrides = _build_overrides_index(instance_attrs)
        flows = _apply_instance_overrides(
            list(kb_lookup[class_name]), overrides, comp_name=name
        )
        out.append(
            ComponentSpec(
                id=cid,
                name=name,
                class_name=class_name,
                flows=flows,
                metadata={
                    "platform_id": cid,
                    "attributes_initial": instance_attrs,
                    "instance_overrides": dict(overrides),
                },
            )
        )
    return out


def _parse_connections(
    connections_raw: Dict[str, Dict[str, Any]],
    components: List[ComponentSpec],
) -> List[ConnectionSpec]:
    """Resolve UUID-based connections to display-name-based ConnectionSpecs.

    Validates :

    - both endpoint UUIDs exist in the components list
    - ``interface_source`` is an output flow of the source component
    - ``interface_target`` is an input flow of the target component
    - if ``interface_source != interface_target``, log a warning
      (muscadet.System.connect_flow uses a single flow_name on both
      ends ; the schema technically allows asymmetry but dil V2 always
      has equality)
    """
    by_id: Dict[str, ComponentSpec] = {c.id: c for c in components}
    out: List[ConnectionSpec] = []
    for conn_id, conn in (connections_raw or {}).items():
        src_id = conn.get("component_source")
        tgt_id = conn.get("component_target")
        src_iface = conn.get("interface_source")
        tgt_iface = conn.get("interface_target")
        if not all((src_id, tgt_id, src_iface, tgt_iface)):
            raise Cod3sPlatformImportError(
                f"Connection {conn_id!r} missing required fields "
                f"(component_source/target, interface_source/target)"
            )
        if src_id not in by_id:
            raise Cod3sPlatformImportError(
                f"Connection {conn_id!r}: unknown source component {src_id!r}"
            )
        if tgt_id not in by_id:
            raise Cod3sPlatformImportError(
                f"Connection {conn_id!r}: unknown target component {tgt_id!r}"
            )
        src = by_id[src_id]
        tgt = by_id[tgt_id]
        src_outputs = {f.name for f in src.flows if f.direction == "output"}
        tgt_inputs = {f.name for f in tgt.flows if f.direction == "input"}
        if src_iface not in src_outputs:
            raise Cod3sPlatformImportError(
                f"Connection {conn_id!r}: source interface {src_iface!r} is "
                f"not an output flow of component {src.name!r} "
                f"(outputs: {sorted(src_outputs)})"
            )
        # ``muscadet.System.connect_flow`` uses a single flow_name on both
        # ends — the source name wins. Validate that name (not the target
        # name) against the target's inputs, so the chosen flow exists
        # where it'll actually be wired.
        if src_iface != tgt_iface:
            logger.warning(
                "Connection %s: source/target interface names differ "
                "(%r != %r); muscadet.System.connect_flow uses a single "
                "flow_name on both ends — using source name.",
                conn_id, src_iface, tgt_iface,
            )
        if src_iface not in tgt_inputs:
            raise Cod3sPlatformImportError(
                f"Connection {conn_id!r}: interface {src_iface!r} is "
                f"not an input flow of component {tgt.name!r} "
                f"(inputs: {sorted(tgt_inputs)})"
            )
        out.append(
            ConnectionSpec(
                source_component=src.name,
                target_component=tgt.name,
                flow_name=src_iface,
            )
        )
    return out


def parse_platform_export(payload: Dict[str, Any]) -> ImporterContext:
    """Translate a Platform JSON payload into a parse-layer context.

    Pure function : no muscadet runtime, no PyCATSHOO dependency,
    no side effects. Validates structure and references.

    Args:
        payload: COD3S Platform export shape (with ``kb_embedded``)
            or canonical test shape (with ``kb`` dict).

    Returns:
        :class:`ImporterContext` containing the system name, ordered
        component specs, ordered connection specs, and metadata.

    Raises:
        Cod3sPlatformImportError: payload malformed, KB missing,
            unknown class, dangling component reference, missing
            interface, duplicate component name.
    """
    kb = _resolve_kb(payload)
    model = payload.get("model") or {}
    elements = model.get("elements") or {}

    kb_lookup = _build_kb_lookup(kb)
    components = _parse_components(elements.get("components") or {}, kb_lookup)
    connections = _parse_connections(elements.get("connections") or {}, components)

    return ImporterContext(
        system_name=model.get("name") or "model",
        components=components,
        connections=connections,
        source_kb={
            "name": (model.get("kb") or {}).get("name", ""),
            "version": (model.get("kb") or {}).get("version", ""),
        },
        metadata={
            "description": model.get("description", ""),
            "owner": model.get("owner", ""),
            "export_version": payload.get("export_version", ""),
        },
    )


# ---------------------------------------------------------------------------
# Apply layer (requires muscadet runtime + PyCATSHOO)
# ---------------------------------------------------------------------------


def _order_outputs_by_deps(
    output_flows: List[FlowSpec],
    input_names: set,
    component_name: str,
) -> List[FlowSpec]:
    """Topological sort of output flows so each is created after its deps.

    The COD3S Platform KB allows an output's ``logic`` (var_prod_cond) to
    reference another **output** of the same component (e.g. diagnostic
    flows that mirror a primary production output). muscadet's
    ``add_flow`` resolves names against ``flows_in ∪ flows_out``, so as
    long as the referenced output is created first, the reference works.

    Returns the outputs in a creation order that satisfies all
    intra-component dependencies, raising on cycles.
    """
    by_name = {f.name: f for f in output_flows}
    remaining = dict(by_name)
    ordered: List[FlowSpec] = []
    available = set(input_names)
    while remaining:
        # Pick every flow whose deps are all already available.
        ready = [
            f for f in remaining.values()
            if all(
                ref in available
                for disj in (f.logic if isinstance(f.logic, list) else [])
                for ref in (disj if isinstance(disj, list) else [disj])
            )
        ]
        if not ready:
            raise Cod3sPlatformImportError(
                f"Component {component_name!r}: cannot order output flows — "
                f"either a cycle in var_prod_cond or a reference to an "
                f"unknown flow. Remaining: {sorted(remaining)}"
            )
        for f in ready:
            ordered.append(f)
            available.add(f.name)
            del remaining[f.name]
    return ordered


def apply_to_system(
    ctx: ImporterContext,
    system: Any,
    *,
    create_default_out_automata: bool = True,
) -> None:
    """Mutate ``system`` in place to materialise the parse-layer context.

    Ordering rules :

    1. For each component, instantiate via ``system.add_component(cls='ObjFlow',
       name=...)``.
    2. Add **all input flows first** (output ``var_prod_cond`` may reference
       them).
    3. Add output flows in **dependency order** — outputs whose
       ``var_prod_cond`` references another output of the same component
       are created after their dependencies (the COD3S Platform KB uses
       this for diagnostic flows that mirror primary outputs).
    4. After all components and flows are declared, wire connections via
       ``system.connect_flow``.

    Output flows are created via the dict-based ``add_flow`` API, which
    resolves ``var_prod_cond`` against ``flows_in ∪ flows_out`` (unlike
    the deprecated ``add_flow_out`` which only consulted ``flows_in``).

    The ``class_name`` from the source KB is preserved in
    ``component.metadata['class_name']`` for downstream filters (regex
    on class, indicator grouping, audit). Other preservation fields
    (``platform_id``, ``attributes_initial``) come along the same way.

    Args:
        ctx: result of :func:`parse_platform_export`.
        system: a ``muscadet.System`` instance (typed as ``Any`` to
            avoid pulling muscadet at module-import time — the
            parameter type is enforced by the calling sites).
        create_default_out_automata: when ``True`` (default), each
            component is instantiated with a default ok/nok automaton
            attached to every output flow (rate ``1e-100``). This is
            the convenient default for downstream failure-mode
            injection — additional ``ObjFM*`` failure modes can hook
            into existing automata rather than create them. Set to
            ``False`` for a lean topology with no automata at all,
            e.g. for connectivity audits.

    Raises:
        Cod3sPlatformImportError: if a runtime-level constraint is
            violated (delegated to the underlying muscadet error,
            re-raised as our domain exception for consistency).
    """
    for spec in ctx.components:
        # ``partial_init=True`` skips the ObjFlow constructor's automatic
        # call to ``set_flows`` — flows added after ``__init__`` would
        # otherwise miss ``add_variables`` / ``add_mb`` /
        # ``update_sensitive_methods`` and connections would fail with
        # "MessageBox introuvable". We add flows explicitly below, then
        # call ``set_flows()`` once at the end to wire everything to
        # PyCATSHOO in a single pass.
        comp = system.add_component(
            cls="ObjFlow",
            name=spec.name,
            partial_init=True,
            create_default_out_automata=create_default_out_automata,
        )
        # Attach metadata after creation. ObjFlow exposes a
        # ``metadata`` dict attribute ; we update rather than overwrite
        # so any default keys set by the constructor are preserved.
        if hasattr(comp, "metadata") and isinstance(comp.metadata, dict):
            comp.metadata.update(
                {
                    "class_name": spec.class_name,
                    "platform_id": spec.metadata.get("platform_id"),
                    "attributes_initial": spec.metadata.get(
                        "attributes_initial", []
                    ),
                }
            )

        # Inputs first
        input_names = set()
        for flow in spec.flows:
            if flow.direction == "input":
                try:
                    comp.add_flow(
                        {"cls": "FlowIn", "name": flow.name, "logic": flow.logic}
                    )
                except Exception as e:
                    raise Cod3sPlatformImportError(
                        f"Failed to add input flow {flow.name!r} to component "
                        f"{spec.name!r}: {e}"
                    ) from e
                input_names.add(flow.name)

        # Outputs in dependency order (an output's logic may reference
        # another output of the same component — Platform KB pattern
        # for diagnostic flows mirroring primary outputs).
        outputs = [f for f in spec.flows if f.direction == "output"]
        for flow in _order_outputs_by_deps(outputs, input_names, spec.name):
            flow_kwargs: Dict[str, Any] = {
                "cls": "FlowOut",
                "name": flow.name,
                "var_prod_cond": flow.logic,
                "var_prod_cond_inner_mode": flow.logic_inner_mode,
                "negate": flow.negate,
            }
            # P1.6 — instance override role=init: set the initial value
            # of var_prod so the flow starts in the user-chosen state.
            # When prod_cond is non-empty, the propagation will resolve
            # var_prod from inputs at t=0+, but the seed matters for
            # the very first tick and for unconditional outputs.
            if flow.init_value is not None:
                flow_kwargs["var_prod_default"] = flow.init_value
            try:
                comp.add_flow(flow_kwargs)
            except Exception as e:
                raise Cod3sPlatformImportError(
                    f"Failed to add output flow {flow.name!r} to component "
                    f"{spec.name!r}: {e}"
                ) from e

        # Wire all declared flows to PyCATSHOO (variables, message boxes,
        # sensitive methods, automata) in one shot. Required because
        # ``partial_init=True`` skipped this in ``__init__``.
        comp.set_flows()

    # Connections — once all flows exist
    for conn in ctx.connections:
        try:
            system.connect_flow(
                source=conn.source_component,
                target=conn.target_component,
                flow_name=conn.flow_name,
            )
        except Exception as e:
            raise Cod3sPlatformImportError(
                f"Failed to connect {conn.source_component!r} --{conn.flow_name}-->"
                f" {conn.target_component!r}: {e}"
            ) from e


def system_from_export(
    payload: Dict[str, Any],
    *,
    name: Optional[str] = None,
    system_class: Optional[type] = None,
    create_default_out_automata: bool = True,
) -> Any:
    """Public entry point — Platform JSON dict → populated muscadet.System.

    Composes :func:`parse_platform_export` (pure) and
    :func:`apply_to_system` (runtime). Lazy-imports ``muscadet.System``
    so a caller using only the parse layer doesn't pay the PyCATSHOO
    import cost.

    Args:
        payload: COD3S Platform export dict. Either the full export
            shape (``{export_version, model, kb_embedded, ...}``) or
            the canonical test shape (``{model, kb}``).
        name: override for the system name. Defaults to
            ``payload['model']['name']``.
        system_class: muscadet ``System`` subclass to instantiate.
            Defaults to :class:`muscadet.System`. Power users can
            pass a custom subclass to wire extra runtime behaviour.
        create_default_out_automata: when ``True`` (default), each
            imported component is instantiated with a default ok/nok
            automaton on every output flow (rate ``1e-100``). Set to
            ``False`` for a lean topology with no automata —
            forwarded as-is to :func:`apply_to_system`.

    Returns:
        Instance of ``system_class`` populated with components, flows,
        and connections per the payload. Ready for ``isimu`` and Monte
        Carlo simulation.

    Raises:
        Cod3sPlatformImportError: payload malformed, KB missing,
            references dangling, runtime-level wiring failure.
    """
    ctx = parse_platform_export(payload)
    if system_class is None:
        # Lazy import : keeps the parse layer importable without
        # PyCATSHOO native libs.
        from muscadet import System as _MuscadetSystem  # noqa: WPS433

        system_class = _MuscadetSystem
    system = system_class(name or ctx.system_name)
    apply_to_system(
        ctx,
        system,
        create_default_out_automata=create_default_out_automata,
    )
    return system
