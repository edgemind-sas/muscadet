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


# Major versions of the COD3S Platform export schemas this importer
# supports. The platform versions the model and the KB independently:
#
# - Top-level ``export_version`` carries the **model export schema major**
#   (= ``MODEL_EXPORT_VERSION`` in ``services/model_io_service.py``).
#   Currently ``1.x``. Tracks structural changes to the model envelope,
#   ``elements``, ``rendering``, etc.
# - ``kb_embedded.export_version`` carries the **KB export schema major**
#   (= ``KB_EXPORT_VERSION`` in ``services/kb_io_service.py``). Currently
#   ``3.x``. Tracks changes to ``component_templates``, ``interfaces``
#   (e.g. ``prod_cond`` / ``input_logic`` rename in 3.0.0).
#
# Both are checked at parse time : a payload outside either major is
# rejected because we cannot guarantee semantic compatibility. Bump
# these in lockstep with the platform breaking releases.
# Top-level model export major. Accepts both 1.x (current platform
# ``MODEL_EXPORT_VERSION``) and 3.x (legacy fixtures generated when the
# model and KB export versions were synchronised pre-decoupling). Drop
# 3 once all checked-in fixtures have been regenerated under the
# current platform release.
_SUPPORTED_MODEL_EXPORT_MAJORS = frozenset({1, 3})

# KB export major. The current platform tag is 3.x. Major 2 is also
# accepted because checked-in fixtures (e.g. ``dil_v2_export.json``)
# carry ``kb_embedded.export_version = "2.0.0"`` even though their
# structure was migrated to the 3.0.0 schema (``logic`` rename to
# ``prod_cond`` / ``input_logic``) in commit 147edca. The structural
# check in ``_parse_interface`` rejects the legacy ``logic`` field
# regardless, so this widening is safe. Drop 2 once those fixtures
# have been regenerated under a 3.0.0-tagged platform.
_SUPPORTED_KB_EXPORT_MAJORS = frozenset({2, 3})


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
    # Default 'and' aligns the importer on the COD3S Platform UI semantics
    # (outer-OR / inner-AND : ``[[A], [B]]`` ⇒ ``A OR B``). With muscadet's
    # ``var_prod_cond_inner_mode='and'``, the runtime matches what the KB
    # Editor displays. KBs that explicitly want outer-AND semantics ship
    # ``logic_inner_mode='or'`` on the interface.
    logic_inner_mode: str = "and"
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
    # F-SYS-10 — logic gate discriminator. ``None`` for a regular
    # ObjFlow component; ``"or"`` / ``"and"`` / ``"k"`` when the
    # component's KB class carries ``metadata.logic_gate``. A gate is
    # materialised as a muscadet ``ObjLogicGate`` (not ObjFlow): it
    # reads its source observable variables directly via ``cond`` and
    # exports a boolean ``result`` to its downstream flows.
    gate_kind: Optional[str] = None
    # Threshold for ``gate_kind == "k"`` (number of fed inputs required).
    gate_k: Optional[int] = None
    # Channel the gate logic reads on its sources: ``True`` → the
    # ``is_fed`` channel (``<flow>_fed_out``), ``False`` → the
    # availability channel (``<flow>_fed_available_out``).
    gate_check_fed: bool = True


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
    # F-SYS-10 — per-endpoint interface names. For a regular connection
    # both equal ``flow_name`` (muscadet collapses to the source name).
    # For a connection touching a logic gate's joker port they differ:
    # ``source_interface`` is the upstream output flow name (used to
    # build the gate ``cond`` leaf ``<flow>_fed_out``) and
    # ``target_interface`` is the downstream input flow name (used as
    # the gate's exported ``out_element``). Default to ``flow_name`` so
    # existing 3-arg construction keeps working unchanged.
    source_interface: str = ""
    target_interface: str = ""


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
        # Default 'and' = outer-OR / inner-AND, matching the KB Editor UI.
        # See FlowSpec.logic_inner_mode docstring for rationale.
        logic_inner_mode=interface.get("logic_inner_mode", "and"),
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


# ---------------------------------------------------------------------------
# Logic gates (F-SYS-10)
# ---------------------------------------------------------------------------
#
# The COD3S Platform injects three synthetic MUSCADET templates
# (``logic_or`` / ``logic_and`` / ``logic_kn``) carrying
# ``metadata.logic_gate ∈ {"or", "and", "k"}``. A gate is materialised as
# a muscadet ``ObjLogicGate`` (an automaton-free combinational component)
# rather than the generic ``ObjFlow``: it reads the observable variables
# of its connected sources directly through the ``cond`` mechanism and
# exports a single boolean ``result`` to each downstream flow element.
#
# Heterogeneous source flow names need NO input plumbing — the gate reads
# ``<source_flow>_fed_out`` (or ``_fed_available_out`` on the availability
# channel) on each source component by name. The k-of-n threshold is
# evaluated natively by ``ObjLogicGate`` (``sum(fed flags) >= k``), so a
# gate aggregating differently-named flows just works.

_VALID_GATE_KINDS = frozenset({"or", "and", "k"})


def _gate_kind_of_template(template: Dict[str, Any]) -> Optional[str]:
    """Return ``"or"``/``"and"``/``"k"`` if the KB template is a logic
    gate, else ``None``. The marker lives at ``metadata.logic_gate``.
    """
    if not isinstance(template, dict):
        return None
    kind = (template.get("metadata") or {}).get("logic_gate")
    return kind if kind in _VALID_GATE_KINDS else None


def _build_gate_kinds(kb: Dict[str, Any]) -> Dict[str, str]:
    """Compute ``{class_name: gate_kind}`` for every logic-gate template
    in the KB. Empty for a KB with no gates.
    """
    templates = kb.get("component_templates") or {}
    out: Dict[str, str] = {}
    for class_name, template in templates.items():
        kind = _gate_kind_of_template(template)
        if kind is not None:
            out[class_name] = kind
    return out


def _gate_attr_value(attributes: List[Dict[str, Any]], name: str) -> Any:
    """Read a gate instance attribute by ``name`` from the model
    component's attributes list. Prefers the instance ``value`` and
    falls back to the template ``value_default``. Returns ``None`` when
    the attribute is absent or carries neither.
    """
    for attr in attributes or []:
        if isinstance(attr, dict) and attr.get("name") == name:
            value = attr.get("value")
            if value is None:
                value = attr.get("value_default")
            return value
    return None


def _read_gate_check_fed(attributes: List[Dict[str, Any]], *, comp_name: str) -> bool:
    """Resolve the gate's ``check_fed`` switch (default ``True``)."""
    raw = _gate_attr_value(attributes, "check_fed")
    if raw is None:
        return True
    return _parse_init_value(raw, flow_name="check_fed", comp_name=comp_name)


def _read_gate_k(attributes: List[Dict[str, Any]], *, comp_name: str) -> int:
    """Resolve the k-of-n threshold for a ``logic_kn`` gate (default 2).

    Accepts native int or decimal string (the platform persists ``k``
    as an editable int attribute, but a JSON round-trip may stringify
    it). Rejects ``k < 1`` loudly.
    """
    raw = _gate_attr_value(attributes, "k")
    if raw is None:
        return 2
    try:
        k = int(raw)
    except (TypeError, ValueError) as exc:
        raise Cod3sPlatformImportError(
            f"Logic gate {comp_name!r}: invalid k attribute {raw!r} (expected an integer)"
        ) from exc
    if k < 1:
        raise Cod3sPlatformImportError(
            f"Logic gate {comp_name!r}: k-of-n threshold must be >= 1 (got {k})"
        )
    return k


# Mapping of P1.6 instance-override roles to the flow direction they
# apply to. The composite key for indexing instance attributes is
# (name, role) — direction is derived from the role at apply time.
#
# Vocabulary refactor 2026-05-22 (cod3s-api 1.x → bumped here in
# lockstep) : legacy roles availability/init/state/logic renamed to
# is_available/prod_init/fed_in/logic_in + new observable fed_out
# (FlowOut var_fed). The platform side migrates DB data via
# migrations kb/007, mbsa/006, modelisation/037.
_ROLE_TO_DIRECTION: Dict[str, str] = {
    "logic_in": "input",
    "prod_init": "output",
}
_OVERRIDE_ROLES: frozenset = frozenset(_ROLE_TO_DIRECTION)
# Roles that exist on the platform but are NOT instance configuration
# overrides — they are runtime observables (is_available, fed_out,
# fed_in) and the importer ignores them silently.
_OBSERVABLE_ROLES: frozenset = frozenset({"is_available", "fed_out", "fed_in"})

# Type aliases — composite key for instance attribute overrides.
OverrideKey = Tuple[str, str]  # (flow_name, role)
OverridesIndex = Dict[OverrideKey, Any]


def _parse_input_logic_value(raw: Any, *, flow_name: str, comp_name: str) -> Union[str, int]:
    """Coerce an instance override of an input ``logic`` attribute.

    Backend AttributeTemplate for role=logic declares type='string'
    (cf. plan G2 sync_v2). The platform persists ``'and'`` / ``'or'``
    as plain strings and ``int k`` (k-of-n) as a decimal string ``'2'``,
    ``'5'``, ... — the muscadet ``add_flow_in(logic=...)`` API expects
    a real Python int for k-of-n, hence the str→int coercion here.
    """
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped in ("and", "or"):
            return stripped
        # Decimal-string k-of-n
        try:
            k = int(stripped)
        except ValueError as e:
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}, flow {flow_name!r}: invalid logic "
                f"override {raw!r} (expected 'and', 'or', or an integer)"
            ) from e
        if k < 1:
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}, flow {flow_name!r}: k-of-n logic "
                f"must be >= 1 (got {k})"
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
            f"must be >= 1 (got {raw})"
        )
    return raw


def _parse_init_value(raw: Any, *, flow_name: str, comp_name: str) -> bool:
    """Coerce an instance override of an output ``init`` attribute.

    Symmetric of :func:`_parse_input_logic_value`. Accepts native
    ``bool`` or canonical string forms (``'true'``/``'false'``,
    ``'1'``/``'0'``, case-insensitive). Refuses anything else loudly
    so a ``"false"`` string never silently becomes ``True`` via the
    Python ``bool(non_empty_str)`` truthiness pitfall.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in ("true", "1"):
            return True
        if normalized in ("false", "0"):
            return False
    raise Cod3sPlatformImportError(
        f"Component {comp_name!r}, flow {flow_name!r}: invalid init "
        f"override {raw!r} (expected bool or 'true'/'false')"
    )


def _build_overrides_index(
    attributes: List[Dict[str, Any]],
) -> OverridesIndex:
    """Index a model component's instance attributes by ``(name, role)``.

    Skips entries without a role (legacy / manual attributes) since
    the apply layer only consumes the ``logic`` (input) and ``init``
    (output) facets — the observable roles ``availability`` / ``state``
    are runtime variables, not configuration overrides.

    Drops entries whose ``value`` is ``None`` : an absent value means
    "use the KB default", same as no override at all.
    """
    out: OverridesIndex = {}
    for attr in attributes or []:
        if not isinstance(attr, dict):
            continue
        name = attr.get("name")
        role = attr.get("role")
        value = attr.get("value")
        if not name or not role:
            continue
        if role in _OBSERVABLE_ROLES:
            continue
        if role not in _OVERRIDE_ROLES:
            logger.warning(
                "Unknown attribute role %r on flow %r — ignored. "
                "Importer may need updating to support this role.",
                role, name,
            )
            continue
        if value is None:
            continue
        out[(name, role)] = value
    return out


def _apply_instance_overrides(
    flows: List[FlowSpec],
    overrides: OverridesIndex,
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

    Disambiguates the case where an interface name appears on both an
    input AND an output port of the same component (e.g. DIL
    ``Logique_Sorties.S_NDILH_PPz_Qx``) by deriving the target direction
    from the override's role rather than matching on ``name`` alone.
    """
    # FlowSpec is frozen — rebuild the list with replaced entries at
    # matching indices, preserving the original order.
    out: List[FlowSpec] = list(flows)
    for (name, role), value in overrides.items():
        target_direction = _ROLE_TO_DIRECTION[role]
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
            if role == "logic_in":
                raise Cod3sPlatformImportError(
                    f"Component {comp_name!r}: instance override role=logic_in "
                    f"on non-input flow {name!r} (direction={other.direction})"
                )
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}: instance override role=prod_init "
                f"on non-output flow {name!r} (direction={other.direction})"
            )
        flow = out[idx]
        if role == "logic_in":
            new_logic = _parse_input_logic_value(value, flow_name=name, comp_name=comp_name)
            out[idx] = replace(flow, logic=new_logic)
        else:  # role == "prod_init"
            out[idx] = replace(
                flow,
                init_value=_parse_init_value(value, flow_name=name, comp_name=comp_name),
            )
    return out


def _parse_components(
    components_raw: Dict[str, Dict[str, Any]],
    kb_lookup: Dict[str, List[FlowSpec]],
    gate_kinds: Optional[Dict[str, str]] = None,
) -> List[ComponentSpec]:
    """Translate the model components dict into a list of ComponentSpec.

    Validates that each component's ``class_name`` is known in the
    KB lookup. Folds instance overrides (attributes with role=logic
    or role=init) into the FlowSpec list so the apply layer sees the
    effective configuration directly. Preserves the raw ``attributes``
    list in metadata for downstream traceability.

    ``gate_kinds`` (F-SYS-10) maps logic-gate class names to their kind
    (``"or"``/``"and"``/``"k"``). A component of such a class is tagged
    as a gate: its KB-parsed flows (``in``/``out`` port names) are kept
    for connection validation, but its ``check_fed`` / ``k`` instance
    attributes are read out so the apply layer can build the muscadet
    ``ObjLogicGate``.
    """
    gate_kinds = gate_kinds or {}
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
        instance_attrs = list(comp.get("attributes") or [])

        gate_kind = gate_kinds.get(class_name)
        if gate_kind is not None:
            # Logic gate : keep the KB-parsed flows (the joker ``in``/``out``
            # port names) for connection validation, but synthesise an
            # ObjLogicGate (not an ObjFlow) at apply time. Read the editable
            # ``check_fed`` / ``k`` attributes off the instance.
            out.append(
                ComponentSpec(
                    id=cid,
                    name=name,
                    class_name=class_name,
                    flows=list(kb_lookup[class_name]),
                    metadata={"platform_id": cid, "attributes_initial": instance_attrs},
                    gate_kind=gate_kind,
                    gate_k=(_read_gate_k(instance_attrs, comp_name=name) if gate_kind == "k" else None),
                    gate_check_fed=_read_gate_check_fed(instance_attrs, comp_name=name),
                )
            )
            continue

        # Instance overrides : attributes with role=logic (input) or
        # role=init (output) replace the KB defaults for THIS instance.
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
        involves_gate = src.gate_kind is not None or tgt.gate_kind is not None
        if involves_gate:
            # F-SYS-10 joker port : the gate's ``in`` / ``out`` ports accept
            # heterogeneous flow names, so each endpoint is validated against
            # its OWN port set (no single-name collapse). The apply layer
            # keeps both interface names — inbound source names build the
            # gate ``cond`` leaves, outbound target names become the gate's
            # exported ``out_elements``.
            if tgt_iface not in tgt_inputs:
                raise Cod3sPlatformImportError(
                    f"Connection {conn_id!r}: target interface {tgt_iface!r} is "
                    f"not an input flow of component {tgt.name!r} "
                    f"(inputs: {sorted(tgt_inputs)})"
                )
            out.append(
                ConnectionSpec(
                    source_component=src.name,
                    target_component=tgt.name,
                    flow_name=src_iface,
                    source_interface=src_iface,
                    target_interface=tgt_iface,
                )
            )
            continue
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
                source_interface=src_iface,
                target_interface=src_iface,
            )
        )
    return out


def _check_export_version(payload: Dict[str, Any]) -> None:
    """Reject payloads whose model or KB ``export_version`` is outside
    this importer's supported window.

    Two independent checks because the platform versions the model and
    the KB independently (cf. ``_SUPPORTED_MODEL_EXPORT_MAJORS`` vs
    ``_SUPPORTED_KB_EXPORT_MAJORS``).

    A missing ``export_version`` at either level is tolerated so the
    canonical test payload (without the platform metadata wrapper)
    keeps working.
    """

    def _check(version: Any, *, supported_majors: frozenset, label: str) -> None:
        if not version:
            return
        try:
            major = int(str(version).split(".", 1)[0])
        except (ValueError, AttributeError) as e:
            raise Cod3sPlatformImportError(
                f"Invalid {label} export_version {version!r} (expected semver x.y.z)"
            ) from e
        if major not in supported_majors:
            wanted = ", ".join(f"{m}.x" for m in sorted(supported_majors))
            raise Cod3sPlatformImportError(
                f"Unsupported {label} export_version {version!r}: this importer "
                f"requires major version {wanted}. "
                f"Re-export from a compatible COD3S Platform release or upgrade "
                f"the muscadet importer."
            )

    _check(
        payload.get("export_version"),
        supported_majors=_SUPPORTED_MODEL_EXPORT_MAJORS,
        label="model",
    )
    kb_embedded = payload.get("kb_embedded")
    if isinstance(kb_embedded, dict):
        _check(
            kb_embedded.get("export_version"),
            supported_majors=_SUPPORTED_KB_EXPORT_MAJORS,
            label="kb_embedded",
        )


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
            interface, duplicate component name, or unsupported
            ``export_version`` major.
    """
    _check_export_version(payload)
    kb = _resolve_kb(payload)
    model = payload.get("model") or {}
    elements = model.get("elements") or {}

    kb_lookup = _build_kb_lookup(kb)
    gate_kinds = _build_gate_kinds(kb)
    components = _parse_components(elements.get("components") or {}, kb_lookup, gate_kinds)
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


# ---------------------------------------------------------------------------
# Logic gate synthesis (F-SYS-10) — apply layer
# ---------------------------------------------------------------------------


def _gate_leaf_attr(source_interface: str, *, source_is_gate: bool, check_fed: bool) -> str:
    """Resolve the muscadet observable variable a gate ``cond`` leaf reads
    on one of its sources.

    * A regular ObjFlow source exposes its output flow as
      ``<flow>_fed_out`` (is_fed channel) and ``<flow>_fed_available_out``
      (availability channel). ``check_fed`` picks which one the gate
      aggregates.
    * A gate source exposes its combinational outcome as the bare
      boolean variable ``result`` (independent of the channel — a gate's
      output is a single abstract boolean), so gate→gate chaining reads
      ``result`` directly.
    """
    if source_is_gate:
        return "result"
    return f"{source_interface}_fed_out" if check_fed else f"{source_interface}_fed_available_out"


def _build_gate_cond_and_outputs(
    gate: ComponentSpec,
    connections: List[ConnectionSpec],
    gate_names: set,
) -> Tuple[List[List[Dict[str, Any]]], List[str]]:
    """Build the ``(cond, out_elements)`` pair for one logic gate from the
    model topology.

    * ``cond`` — one unit clause ``[{obj, attr, value}]`` per inbound
      connection (source observable on the selected channel). The
      ObjLogicGate's ``kind`` alone then selects the aggregation
      (any / all / sum>=k) across these unit clauses.
    * ``out_elements`` — the distinct downstream input-flow names this
      gate feeds (order-preserving). The gate exports its ``result``
      under ``{elem}_out`` for each, so a plain downstream ``FlowIn``
      named ``elem`` consumes it.
    """
    cond: List[List[Dict[str, Any]]] = []
    for conn in connections:
        if conn.target_component != gate.name:
            continue
        attr = _gate_leaf_attr(
            conn.source_interface,
            source_is_gate=conn.source_component in gate_names,
            check_fed=gate.gate_check_fed,
        )
        cond.append([{"obj": conn.source_component, "attr": attr, "value": True}])

    out_elements: List[str] = []
    seen: set = set()
    for conn in connections:
        if conn.source_component != gate.name:
            continue
        if conn.target_interface not in seen:
            seen.add(conn.target_interface)
            out_elements.append(conn.target_interface)
    return cond, out_elements


def _order_gates(
    gates: List[ComponentSpec],
    connections: List[ConnectionSpec],
    gate_names: set,
) -> List[ComponentSpec]:
    """Topologically order gates so a gate is created after every gate it
    reads (gate→gate chaining). ``ObjLogicGate.__init__`` resolves its
    ``cond`` leaves against ``system.comp[obj]`` at construction time, so
    an upstream gate's ``result`` variable must already exist.

    Raises :class:`Cod3sPlatformImportError` on a cycle among gates.
    """
    deps: Dict[str, set] = {}
    for gate in gates:
        deps[gate.name] = {
            conn.source_component
            for conn in connections
            if conn.target_component == gate.name and conn.source_component in gate_names
        }
    ordered: List[ComponentSpec] = []
    placed: set = set()
    remaining = {gate.name: gate for gate in gates}
    while remaining:
        ready = [name for name in remaining if deps[name] <= placed]
        if not ready:
            raise Cod3sPlatformImportError(
                f"Logic gate cycle detected among {sorted(remaining)} — "
                f"a gate's output feeds (directly or transitively) one of its own inputs."
            )
        for name in ready:
            ordered.append(remaining.pop(name))
            placed.add(name)
    return ordered


def _create_logic_gate(gate: ComponentSpec, system: Any, connections: List[ConnectionSpec], gate_names: set) -> None:
    """Instantiate one ``ObjLogicGate`` on ``system`` from its parse spec."""
    cond, out_elements = _build_gate_cond_and_outputs(gate, connections, gate_names)
    kwargs: Dict[str, Any] = {
        "cls": "ObjLogicGate",
        "name": gate.name,
        "cond": cond,
        "out_elements": out_elements,
        "kind": gate.gate_kind,
    }
    if gate.gate_kind == "k":
        kwargs["k"] = gate.gate_k if gate.gate_k is not None else 2
    try:
        comp = system.add_component(**kwargs)
    except Exception as e:
        raise Cod3sPlatformImportError(
            f"Failed to create logic gate {gate.name!r} (kind={gate.gate_kind!r}): {e}"
        ) from e
    if comp is not None and hasattr(comp, "metadata") and isinstance(comp.metadata, dict):
        comp.metadata.update(
            {
                "class_name": gate.class_name,
                "platform_id": gate.metadata.get("platform_id"),
                "logic_gate": gate.gate_kind,
            }
        )


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
    (``platform_id``, ``attributes_initial``, ``instance_overrides``)
    come along the same way. ``instance_overrides`` is the condensed
    audit trail of overrides actually applied (filtered to roles
    ``logic`` / ``init``, value-non-null), keyed by ``(flow_name, role)``.

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
    # F-SYS-10 — logic gates are materialised as ObjLogicGate, not ObjFlow.
    # Create regular components first so the gates' ``cond`` leaves can
    # resolve their source variables at construction time.
    gate_specs = [c for c in ctx.components if c.gate_kind is not None]
    gate_names = {g.name for g in gate_specs}
    normal_specs = [c for c in ctx.components if c.gate_kind is None]

    for spec in normal_specs:
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
                    "instance_overrides": dict(
                        spec.metadata.get("instance_overrides") or {}
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

    # Logic gates after all regular components exist (their ``cond``
    # references regular source variables) and in dependency order
    # (gate→gate chaining).
    for gate in _order_gates(gate_specs, ctx.connections, gate_names):
        _create_logic_gate(gate, system, ctx.connections, gate_names)

    # Connections — once all flows exist.
    for conn in ctx.connections:
        # Inbound connections to a gate are NOT wired: the gate reads its
        # sources directly through ``cond`` (no input message box exists
        # on an ObjLogicGate). They only contributed to the gate's cond.
        if conn.target_component in gate_names:
            continue
        if conn.source_component in gate_names:
            # Outbound from a gate. ObjLogicGate is a plain PycComponent
            # without ``flows_out`` / ``is_connected_to``, so we cannot use
            # ``connect_flow`` (it runs ObjFlow authorization checks). Wire
            # the raw message boxes directly: the gate exports ``result``
            # under ``{target_iface}_out`` and the downstream FlowIn exposes
            # ``{target_iface}_in``.
            elem = conn.target_interface
            try:
                system.connect(
                    conn.source_component,
                    f"{elem}_out",
                    conn.target_component,
                    f"{elem}_in",
                )
            except Exception as e:
                raise Cod3sPlatformImportError(
                    f"Failed to connect logic gate {conn.source_component!r} "
                    f"--{elem}--> {conn.target_component!r}: {e}"
                ) from e
            continue
        # Regular flow connection (collapsed single ``flow_name``).
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
