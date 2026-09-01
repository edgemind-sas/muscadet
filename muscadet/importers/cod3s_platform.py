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

Flow families (2026-08) : a KB interface carries a ``flow_family``
discriminator selecting the muscadet flow family — ``'discrete'`` (the
default, and the whole of the 1.x schema) or ``'continuous'``. The two
families share no declaration key and are built from two separate kwargs
dictionaries; an unknown family is refused rather than read as discrete.
``_SUPPORTS_CONTINUOUS_FLOW_FAMILY`` is the marker the platform probes to
know this muscadet understands the field at all.

Capacities and recipes (2026-08) : a continuous plant is not made of ports
alone. A KB class template carries two further sections, ``capacities`` and
``rule_sets``, PARALLEL to ``interfaces`` and never nested inside it -- a volume
is held over several flows at once, and a rule correlates several outputs, so
neither is a property of a single port. A MODEL COMPONENT carries the numbers
that differ between two instances of one class, as ``attributes`` under the
``capacity_volume`` / ``capacity_content_init`` roles, plus a ``deratings``
list of ``(mode, continuous output)`` pairs whose variable is allocated after
``set_flows()``. The declaration ORDER is read from
``muscadet.declare.DECLARATION_SECTIONS`` rather than restated here: it belongs
to the library, and three of its refusals are only reachable when the thing
doing the refusing exists first.

Controllers (2026-09) : a component need not transport anything. A KB class
template carrying ``metadata.controller: true`` declares a CONTROLLER, the peer
of ``ObjFlow`` that observes readings and publishes signals (R39, R46). It
carries no ``interfaces`` at all -- it holds no flow -- and states instead two
sections beside them, ``controls_in`` and ``controls_out``, the placement
``capacities`` and ``rule_sets`` already established. A controller is
materialised as a ``muscadet.ObjCtrl`` OUTSIDE the regular component path, like
a logic gate and for the same reason: neither has the ``ObjFlow`` construction
lifecycle. Every edge touching one is an INFORMATION edge, wired with the raw
``System.connect`` on boxes the parse layer resolves, because neither of its two
endpoint names is a declared flow. ``_SUPPORTS_CONTROLLERS`` is the marker the
platform probes before translating one.
"""

from __future__ import annotations

import copy
import logging
import math
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
    # Value of an INPUT flow when it is NOT connected, set by the parse
    # layer when an ``instance.attribute(role=var_in_default)`` override
    # exists (discrete family) or read off the KB interface (continuous
    # family). ``None`` means "leave the muscadet default" (``False`` on a
    # discrete input, ``0.0`` on a continuous one). On a DISCRETE flow it is a
    # bool and True marks an always-fed boundary input (external source); on a
    # CONTINUOUS flow it is the real value the input reads while nothing is
    # connected. One field because it is one property -- the platform names the
    # attribute role ``var_in_default`` on both families for the same reason.
    # Passed to ``FlowIn.var_in_default`` / ``FlowContinuousIn.var_in_default``.
    var_in_default: Optional[Union[bool, float]] = None
    # Service-function dormancy default for an OUTPUT flow, set by the parse
    # layer when an ``instance.attribute(role=active_init)`` override exists.
    # ``None`` means "leave the muscadet default" (``var_is_active_default`` =
    # True, i.e. always active). ``False`` makes the flow a *service function*:
    # ``var_fed = var_prod AND var_is_active AND var_fed_available_out`` stays
    # False even when ``var_prod_cond`` holds, until an effect sets
    # ``var_is_active`` True. Passed to ``FlowOut.var_is_active_default``.
    # NOTE: kept for the var_is_active path (cf. flow.py "POINT À TRANCHER") but
    # NOT surfaced as a platform UI role — the user-facing dormancy control is
    # ``fed_available_init`` below.
    is_active_default: Optional[bool] = None
    # Service-function dormancy via the availability gate (the user-facing
    # mechanism). Set by the parse layer when an
    # ``instance.attribute(role=fed_available_init)`` override exists. ``None``
    # = muscadet default (``var_fed_available_out_init`` = True, available).
    # ``False`` = dormant: ``var_fed_available_out`` starts (and reinitialises)
    # False, so the flow is unfed AND propagates "unavailable" downstream until
    # an effect sets it True. Passed to ``FlowOut.var_fed_available_out_init``.
    fed_available_init: Optional[bool] = None
    # Availability-gate reset control for an OUTPUT flow. Set by the parse layer
    # when an ``instance.attribute(role=fed_available_reset)`` override exists.
    # ``None`` = muscadet default (``var_fed_available_out_reset`` = True, gate
    # reinitialised at each step). ``False`` = the availability gate is PERSISTENT
    # and memorises its last value within a sequence (setReinitialized(False));
    # the init is still honoured at t=0 and between MC sequences. Passed to
    # ``FlowOut.var_fed_available_out_reset``.
    fed_available_reset: Optional[bool] = None

    # --- Dynamic output flow types (2026-07-10, cross-repo contract) ---------
    # Discriminator selecting the muscadet flow-out class for an OUTPUT flow.
    # ``'classic'`` (or absent on a legacy KB) -> ``FlowOut`` (combinational);
    # ``'tempo'`` -> ``FlowOutTempo`` (timed enable/disable automaton);
    # ``'on_trigger'`` -> ``FlowOutOnTrigger`` (external-signal-driven automaton).
    # Kept a plain string so a KB predating this field parses as ``classic``.
    flow_type: str = "classic"
    # tempo params (flow_type == 'tempo'). Occurrence-law dicts in SHORT wire
    # form ({"cls": "delay"|"exp"|"inst", ...}); cod3s' sanitize_occ_law
    # normalises them when the automaton is built. ``None`` leaves the muscadet
    # FlowOutTempo default ({"cls": "delay", "time": 0}).
    occ_enable: Optional[dict] = None
    occ_disable: Optional[dict] = None
    # Initial automaton state for a tempo flow. ``None`` leaves the muscadet
    # default (init_enable=False, i.e. starts disabled/dormant).
    init_enable: Optional[bool] = None
    # on_trigger params (flow_type == 'on_trigger'). Plain floats — muscadet
    # wraps them in a ``delay`` law internally (cf. flow.py FlowOutOnTrigger).
    # ``None`` leaves the muscadet default (0).
    trigger_time_up: Optional[float] = None
    trigger_time_down: Optional[float] = None
    # 'and' / 'or' / int k (at-least-k). ``None`` leaves the muscadet default
    # ('or'). Trigger wiring itself (connect_trigger) is a separate concern and
    # is not emitted by this importer yet — an unwired on_trigger flow builds
    # and simulates but follows the automaton default until wired.
    trigger_logic: Optional[Union[str, int]] = None

    # --- Flow FAMILY (2026-08, continuous flows chantier) -------------------
    # Which muscadet flow family this interface belongs to: ``'discrete'``
    # (``FlowIn`` / ``FlowOut`` and friends) or ``'continuous'``
    # (``FlowContinuousIn`` / ``FlowContinuousOut``). Deliberately DISTINCT
    # from ``flow_type`` above, which stays a discrete-only discriminator
    # (classic / tempo / on_trigger): the two answer different questions and
    # collapsing them would make ``tempo`` look like a family. Kept a plain
    # string defaulting to ``'discrete'`` so a KB predating the field parses
    # exactly as it did before.
    flow_family: str = "discrete"
    # CONTINUOUS family only — the nominal rate of the port, decomposed by the
    # parse layer from its profile attribute (``production_profile`` on an
    # output, ``demand_profile`` on an input). ``None`` leaves the muscadet
    # default (0.0), which is what a port a recipe or a capacity serves wants.
    # Target: ``FlowContinuousOut.var_fed_default`` on an output,
    # ``FlowContinuousIn.var_demand_default`` on an input.
    nominal_rate: Optional[float] = None
    # CONTINUOUS output only — the MODULATION half of the profile
    # decomposition, in muscadet's own ``{"cls": "SinusoidalProfile", ...}``
    # mapping form, or ``None`` for a constant shape. muscadet's profile is a
    # multiplicative FACTOR on ``var_fed_default`` and its shape library holds
    # no constant shape, so a constant profile lives entirely in
    # ``nominal_rate`` and never reaches ``muscadet.build_profile``.
    profile_spec: Optional[dict] = None
    # CONTINUOUS output only — how an insufficient supply is split among the
    # consumers. v1 exposes proportional sharing only (muscadet's own default);
    # ``None`` on a discrete flow, where the notion does not exist.
    allocation: Optional[str] = None


# ---------------------------------------------------------------------------
# Capacities and rule sets (2026-08) — CLASS-TEMPLATE sections
# ---------------------------------------------------------------------------
#
# Both live on ``component_templates[<class>]``, PARALLEL to ``interfaces`` and
# never nested inside it. That placement is the contract, and it follows from
# what each declaration is:
#
# * a capacity is a volume held over one or more flows AT ONCE, with a weight
#   per constituent -- a mixture in one tank is not a property of any single
#   port;
# * a rule set states a transformation whose outputs are CORRELATED -- the
#   electrolysis rule produces H2 and O2 together, so a derating takes both
#   down -- and a recipe written one output at a time would state something
#   else entirely.
#
# Both name flows, so both are validated against the template's own interfaces
# at parse time: a name that is not a declared flow, or a discrete flow where a
# rate is required, is refused here rather than at ``add_capacity`` time, where
# the message would name a muscadet field on a muscadet class and tell a
# platform user nothing about the class they wrote.


@dataclass(frozen=True)
class CapacityFlowSpec:
    """One flow held by a capacity, and the volume a unit of it occupies."""

    name: str
    # Volume one unit of this flow occupies. Governs OCCUPANCY only, never how
    # a withdrawal is composed. muscadet's own default, restated here so a
    # capacity read back from a spec carries the number rather than an absence.
    weight: float = 1.0


@dataclass(frozen=True)
class CapacitySpec:
    """One capacity declared on a KB class template."""

    name: str
    flows: Tuple[CapacityFlowSpec, ...]
    # The volume the held flows SHARE, strictly positive. Named ``volume`` on
    # the platform side and mapped onto muscadet's ``capacity`` keyword: inside
    # an entry of a ``capacities`` list, a key called ``capacity`` reads as the
    # capacity itself rather than as its size.
    volume: float
    # ``'in'`` places the whole capacity upstream of the component's rules,
    # ``'out'`` downstream. ``None`` leaves muscadet to resolve it from the
    # sides the held flows are carried on.
    side: Optional[str] = None
    # Initial raw quantity per held flow; an omitted flow starts empty.
    content_init: Dict[str, float] = field(default_factory=dict)
    # Rate the volume claims for ITSELF while it has room, over and above the
    # demand crossing it. ``None`` leaves the muscadet default (0.0, a pure
    # buffer). ``math.inf`` means "whatever the producer can deliver" and is
    # spelled ``"inf"`` as well as ``Infinity``, JSON having no literal for it.
    fill_rate: Optional[float] = None


@dataclass(frozen=True)
class RuleOperandSpec:
    """One operand of a rule guard: a boolean flow state, or a comparison."""

    name: str
    negate: bool = False
    # ``'in'`` / ``'out'`` force the side the name resolves against. ``None``
    # keeps muscadet's input-first resolution.
    port: Optional[str] = None
    # Comparison operator of a numeric operand; ``None`` for a boolean one.
    op: Optional[str] = None
    value: Optional[float] = None


@dataclass(frozen=True)
class RuleSpec:
    """One transformation rule: a guard, a ``cons`` map and a ``prod`` map."""

    # Optional, and used to designate the rule in muscadet's error messages.
    name: Optional[str] = None
    # Conjunction of operands. EMPTY makes this the default rule of its set,
    # and a set carries at most one of those.
    cond: Tuple[RuleOperandSpec, ...] = ()
    cons: Dict[str, float] = field(default_factory=dict)
    prod: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleSetSpec:
    """One ordered set of transformation rules declared on a class template."""

    name: str
    rules: Tuple[RuleSpec, ...] = ()


@dataclass(frozen=True)
class DeratingSpec:
    """One ``(mode, continuous output)`` pair to pre-allocate a variable for.

    Carried by the MODEL COMPONENT and not by the class template: a failure
    mode is declared per instance, and the variable a mode clamps belongs to
    the component that carries it.
    """

    mode: str
    flow: str


# ---------------------------------------------------------------------------
# Controllers (R46)
# ---------------------------------------------------------------------------
#
# A controller observes quantities and publishes signals. It is a PEER of
# ``ObjFlow`` and not a kind of it (R39): nothing is conserved along a signal,
# so it declares no flow, enters no allocation and is connected with the raw
# ``System.connect``. The platform declares one as a class template carrying
# ``metadata.controller: true`` -- the discriminant, on the model of the logic
# gate's ``metadata.logic_gate`` -- plus two sections BESIDE ``interfaces``.
#
# Beside, and never inside, for a reason stronger than the one that put
# ``capacities`` there: a controller carries no interface at all, so there is no
# port for an observation input to be a property of.

#: An observation input reads a capacity LEVEL, or a republished reading.
CONTROL_IN_LEVEL = "level"

#: An observation input reads the RATE a continuous output delivers (R38).
CONTROL_IN_RATE = "rate"

#: Every nature an observation input may be declared with.
_VALID_CONTROL_IN_KINDS = frozenset({CONTROL_IN_LEVEL, CONTROL_IN_RATE})

#: A boolean output: one signal exported on ``{name}_out``, which a discrete
#: input flow of the same name imports with no adapter.
CONTROL_OUT_BOOL = "bool"

#: A value output: a publication on ``{name}_level_out``, which any observer
#: reads -- a second controller included (R4).
CONTROL_OUT_VALUE = "value"

#: Every nature an output may be declared with.
_VALID_CONTROL_OUT_KINDS = frozenset({CONTROL_OUT_BOOL, CONTROL_OUT_VALUE})

#: How SEVERAL publishers reduce to one reading on one observation input (R40).
#: A closed list, and restated here rather than imported from
#: :data:`muscadet.CONTROL_AGGREGATIONS`: this layer imports no muscadet, and
#: restating is what makes a misspelt policy a PARSE-time refusal naming the
#: platform's own key instead of a runtime one naming a muscadet field.
_VALID_CONTROL_AGGREGATIONS = frozenset({"sum", "mean", "median", "min", "max"})

#: Keys one ``controls_in`` entry may carry -- the mirror of
#: ``muscadet.obj_ctrl.CONTROL_IN_KEYS``, plus the ``name`` that identifies it.
_CONTROL_IN_KEYS = frozenset(
    {
        "name",
        "kind",
        "aggregate",
        "flows",
        "level_default",
        "fill_default",
        "rate_default",
    }
)

#: Keys one BOOLEAN ``controls_out`` entry may carry.
_CONTROL_OUT_BOOL_KEYS = frozenset({"name", "kind", "default", "emit"})

#: Keys one VALUE ``controls_out`` entry may carry. ``source`` is absent on both
#: sides of the bridge: what a value output publishes comes from its ``emit``
#: grammar, and a second way of saying it would be a second answer.
_CONTROL_OUT_VALUE_KEYS = frozenset(
    {
        "name",
        "kind",
        "flows",
        "level_default",
        "fill_default",
        "gain_default",
        "emit",
    }
)


@dataclass(frozen=True)
class ControlInSpec:
    """One observation input declared on a controller class template."""

    name: str
    # ``level`` or ``rate``. It decides the box the input imports on --
    # ``{name}_level_in`` or ``{name}_rate_in`` -- and therefore what a
    # publisher must export on the other end.
    kind: str = CONTROL_IN_LEVEL
    # ``None`` is the single-publisher input, capped at one connection by
    # muscadet. Any policy lifts the cap and says how the readings reduce.
    aggregate: Optional[str] = None
    # Constituents of the observed volume to read individually, beside the
    # total. Passed through verbatim: muscadet deliberately judges a
    # constituent at CONNECT time, against what the publisher actually holds.
    flows: Tuple[str, ...] = ()
    level_default: Optional[float] = None
    fill_default: Optional[float] = None
    rate_default: Optional[float] = None


@dataclass(frozen=True)
class ControlOutSpec:
    """One output declared on a controller class template."""

    name: str
    kind: str = CONTROL_OUT_BOOL
    # Boolean outputs only: the value the signal holds before anything writes
    # it, and the value a blinded output carries.
    default: Optional[bool] = None
    # The closed output grammar (R42), verbatim. NOT validated here: what may
    # be written is a composition of four operators whose one implementation is
    # ``muscadet.build_ctrl_node``, and a second reading of it in this layer
    # would be a second grammar, free to drift. It is refused by name, before a
    # single engine object exists, at ``ObjCtrl`` construction.
    emit: Optional[Dict[str, Any]] = None
    # Value outputs only, forwarded to ``muscadet.MeasurementOut``.
    flows: Tuple[str, ...] = ()
    level_default: Optional[float] = None
    fill_default: Optional[float] = None
    gain_default: Optional[float] = None


@dataclass(frozen=True)
class ControllerSpec:
    """What a controller class template declares, in declaration order."""

    controls_in: Tuple[ControlInSpec, ...] = ()
    controls_out: Tuple[ControlOutSpec, ...] = ()

    def input_named(self, name: str) -> Optional[ControlInSpec]:
        """The observation input of that name, or ``None``."""
        for entry in self.controls_in:
            if entry.name == name:
                return entry
        return None

    def output_named(self, name: str) -> Optional[ControlOutSpec]:
        """The output of that name, or ``None``."""
        for entry in self.controls_out:
            if entry.name == name:
                return entry
        return None

    @property
    def input_names(self) -> List[str]:
        return [entry.name for entry in self.controls_in]

    @property
    def output_names(self) -> List[str]:
        return [entry.name for entry in self.controls_out]


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
    # Capacities of the referenced class template, WITH this instance's
    # overrides already folded in (cf. _apply_capacity_overrides). Resolved per
    # component rather than once per class precisely because of those: two
    # components of one class routinely hold different volumes.
    capacities: Tuple[CapacitySpec, ...] = ()
    # Rule sets of the referenced class template, verbatim. No per-instance
    # override channel exists for a recipe: a coefficient that differs between
    # two instances is a different reaction, hence a different class.
    rule_sets: Tuple[RuleSetSpec, ...] = ()
    # ``(mode, continuous output)`` pairs whose derating variable this
    # component allocates after ``set_flows()``.
    deratings: Tuple[DeratingSpec, ...] = ()
    # R46 -- controller discriminator. ``None`` for everything that transports
    # a quantity; a :class:`ControllerSpec` when the component's KB class
    # carries ``metadata.controller``. Such a component is materialised as a
    # ``muscadet.ObjCtrl`` (neither ObjFlow nor ObjLogicGate) and holds no flow
    # at all, which is why ``flows`` stays empty on it.
    controller: Optional[ControllerSpec] = None


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
    # R46 -- an INFORMATION edge (one touching a controller) is wired with the
    # raw ``System.connect`` on these two message boxes, and a regular flow edge
    # leaves both at ``None``. Resolved by the parse layer rather than by the
    # apply layer: deciding which box an endpoint exports on takes the whole
    # matrix of natures (level / rate / bool / value), and a second site
    # deciding it would be a second answer to one question.
    source_box: Optional[str] = None
    target_box: Optional[str] = None


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
    if (
        "kb" in payload
        and isinstance(payload["kb"], dict)
        and ("component_templates" in payload["kb"])
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

# Output flow-out discriminator values understood by the apply layer.
# ``classic`` -> FlowOut, ``tempo`` -> FlowOutTempo, ``on_trigger`` ->
# FlowOutOnTrigger (cf. FlowSpec.flow_type).
_VALID_FLOW_TYPES = frozenset({"classic", "tempo", "on_trigger"})

# Capability marker probed by the COD3S Platform (jumeau of _VALID_FLOW_TYPES):
# this muscadet resolves a ``prod_cond`` operand of the ``{"name", "negate"}``
# mapping form to a per-operand negated flow (ObjFlow.postprocess_flow_specs +
# FlowOut.var_prod_cond_negate). An older muscadet lacks this attribute, so the
# platform can refuse to simulate a KB carrying negation rather than silently
# dropping the negation. Cf. per-operand-negation ADR (2026-07-22).
_SUPPORTS_PROD_COND_NEGATION = True

# Capability marker (jumeau of _SUPPORTS_PROD_COND_NEGATION): this muscadet
# resolves a ``prod_cond`` operand carrying a ``"port": "in"|"out"`` hint, which
# disambiguates a flow name held by BOTH an input and an output of the same
# component (e.g. a passthrough ``flow`` where a ``ctrl`` output must mirror the
# OUTPUT, not the raw input). An older muscadet lacks this attribute, so the
# platform can refuse to simulate a KB that uses ``port`` rather than silently
# mis-resolving it. Cf. the prod_cond port-disambiguation chantier (2026-07).
_SUPPORTS_PROD_COND_PORT = True

# Capability marker (jumeau of _SUPPORTS_PROD_COND_PORT): this muscadet applies
# PER-INSTANCE tempo overrides — the ``tempo_activation`` / ``tempo_deactivation``
# attribute roles set the enable/disable occurrence laws of an output flow at the
# component level, so a model can turn a classic FlowOut into a FlowOutTempo (or
# back) without editing the KB. The override value is an occurrence-law dict in
# SHORT wire form (``{"cls": "delay"|"exp"|"inst", ...}``) or the sentinel
# ``{"cls": "none"}`` meaning "force classic (no law)". An absent value (``None``)
# is dropped upstream and means "inherit the KB default". An older muscadet lacks
# this attribute, so the platform can refuse to simulate a model carrying tempo
# overrides rather than silently ignoring them. Cf. the tempo-in-attributes
# chantier (2026-07).
_SUPPORTS_INSTANCE_TEMPO_OVERRIDE = True

# Capability marker (jumeau of _SUPPORTS_INSTANCE_TEMPO_OVERRIDE): this muscadet
# reads the ``flow_family`` interface discriminator and builds a CONTINUOUS flow
# (``FlowContinuousIn`` / ``FlowContinuousOut``) for the ``"continuous"`` value,
# with its nominal rate, its allocation policy and its production profile. An
# older muscadet lacks this attribute, so the platform can refuse to simulate a
# KB carrying continuous ports rather than importing them as discrete ones --
# which is what an unknown interface key silently degrades to. Probed as
# ``getattr(module, "_SUPPORTS_CONTINUOUS_FLOW_FAMILY", False)``. Cf. the
# continuous flows chantier (2026-08).
_SUPPORTS_CONTINUOUS_FLOW_FAMILY = True

# Capability marker (jumeau of _SUPPORTS_CONTINUOUS_FLOW_FAMILY): this muscadet
# reads a ``capacities`` section on a KB CLASS TEMPLATE, beside ``interfaces``
# and never inside it, and declares each entry through ``ObjFlow.add_capacity``.
# A volume is held over SEVERAL flows and is therefore not a property of one
# port, which is why it cannot live on an interface. An older muscadet lacks
# this attribute, so the platform can refuse to simulate a KB carrying buffers
# rather than importing a plant whose tanks are missing. Cf. the continuous
# flows chantier (2026-08).
_SUPPORTS_CONTINUOUS_CAPACITIES = True

# Capability marker (jumeau of _SUPPORTS_CONTINUOUS_CAPACITIES): this muscadet
# reads a ``rule_sets`` section on a KB class template and declares each entry
# through ``ObjFlow.add_rules``. Same reason it is not an interface key: a rule
# correlates several outputs -- H2 and O2 come out of one reaction and scale
# together -- so a recipe stated one output at a time would state something
# else. Cf. the continuous flows chantier (2026-08).
_SUPPORTS_CONTINUOUS_RULE_SETS = True

# Capability marker (jumeau of _SUPPORTS_CONTINUOUS_RULE_SETS): this muscadet
# applies PER-INSTANCE capacity overrides -- the ``capacity_volume`` and
# ``capacity_content_init`` attribute roles, keyed by CAPACITY name rather than
# by flow name -- so two components of one class can hold different volumes
# without splitting the class in two. An older muscadet lacks this attribute,
# so the platform can refuse rather than build every tank at the template's
# volume. Cf. the continuous flows chantier (2026-08).
_SUPPORTS_INSTANCE_CAPACITY_OVERRIDE = True

# Capability marker (jumeau of _SUPPORTS_INSTANCE_CAPACITY_OVERRIDE): this
# muscadet PRE-ALLOCATES the derating variable of a (mode, continuous output)
# pair carried by a model component, through the public ``add_derating``, after
# ``set_flows()``. A mode declared outside the component -- a standalone
# ``cod3s.ObjFM*`` naming variables by their exact basename -- needs the
# variable to exist before it can target it. An older muscadet lacks this
# attribute, so the platform can refuse rather than emit a mode clamping a
# variable nothing created. Cf. the continuous flows chantier (2026-08).
_SUPPORTS_DERATING_PREALLOCATION = True

# Capability marker (jumeau of _SUPPORTS_DERATING_PREALLOCATION): this muscadet
# reads a CONTROLLER declaration -- a KB class template carrying
# ``metadata.controller`` and the two sections ``controls_in`` /
# ``controls_out`` -- and materialises it as a ``muscadet.ObjCtrl`` (R39, R46),
# wiring every edge that touches it as an information edge. An older muscadet
# lacks this attribute, and the degradation it would produce is the worst of the
# lot: the class template carries no ``interfaces``, so the controller would
# import as a component with NO port, its observation and signal edges would be
# refused or dropped, and a study would run to completion on a plant whose
# regulation is simply absent -- a false reliability figure, not an error. The
# platform therefore probes this before translating a model that carries one.
# Cf. the controller chantier (2026-09).
_SUPPORTS_CONTROLLERS = True

# ---------------------------------------------------------------------------
# Flow families (2026-08)
# ---------------------------------------------------------------------------
#
# ``flow_family`` selects the muscadet flow FAMILY and is orthogonal to
# ``flow_type``, which selects the discrete flow CLASS. A discrete interface is
# the historical shape and stays the default, so a KB predating the field is
# read exactly as before. There is no fallback the other way: an unknown family
# is refused, because reading it as discrete would build a boolean port where a
# rate was declared and report nothing.

DISCRETE_FAMILY = "discrete"
CONTINUOUS_FAMILY = "continuous"
_VALID_FLOW_FAMILIES = frozenset({DISCRETE_FAMILY, CONTINUOUS_FAMILY})

# Allocation policies exposed by v1. muscadet also implements ``shares`` and
# ``priority``, both of which need a per-consumer map the platform has no
# surface for yet; declaring one here would build a flow whose split is decided
# by a map nothing fills. Proportional is muscadet's own default.
_ALLOCATION_PROPORTIONAL = "proportional"
_VALID_ALLOCATIONS = frozenset({_ALLOCATION_PROPORTIONAL})

# Interface keys that belong to the DISCRETE family alone. Refused on a
# continuous interface, by name, at parse time. The three that matter most --
# ``prod_cond``, ``negate``, ``logic_inner_mode`` -- are also refused by
# ``FlowContinuous.check_declaration_keys`` downstream, but that refusal names a
# muscadet field on a muscadet class: it tells a platform user nothing about the
# port they declared. Refusing here names the PLATFORM key, and refusing at
# parse time keeps the diagnostic reachable without a runtime.
_DISCRETE_ONLY_INTERFACE_KEYS: Tuple[str, ...] = (
    "input_logic",
    "prod_cond",
    "logic_inner_mode",
    "negate",
    "flow_type",
    "occ_enable",
    "occ_disable",
    "init_enable",
    "trigger_time_up",
    "trigger_time_down",
    "trigger_logic",
)

# The mirror: keys that belong to the CONTINUOUS family alone, refused on a
# discrete interface. A profile written on a boolean port is a declaration that
# could never take effect, and the silent drop is what the family discriminator
# exists to make impossible in both directions.
_CONTINUOUS_ONLY_INTERFACE_KEYS: Tuple[str, ...] = (
    "production_profile",
    "demand_profile",
    "allocation",
)

# Values a key may carry while saying NOTHING, beside ``None`` and the empty
# containers handled generically below. The platform serialises its interface
# model whole, and two of its discrete fields are declared with a non-null
# default (``negate=False``, ``logic_inner_mode="and"``): every exported
# interface carries them, continuous ones included, without anyone having
# declared anything. Refusing those would refuse a payload that means what the
# absence of the key means. Anything ELSE written under these names was chosen,
# and is refused -- ``negate=True`` on a continuous port is a modeller
# believing in a negation that will never be applied.
_NEUTRAL_INTERFACE_VALUES: Dict[str, Tuple[Any, ...]] = {
    "input_logic": ("or",),
    "logic_inner_mode": ("and",),
    "negate": (False,),
    "flow_type": ("classic",),
    "allocation": (_ALLOCATION_PROPORTIONAL,),
}

# ---------------------------------------------------------------------------
# Profile decomposition (2026-08)
# ---------------------------------------------------------------------------
#
# The platform declares the quantity of a continuous port as a PROFILE: a named
# shape and its parameters, on the model of the occurrence laws it already
# carries, whose ``value`` is ALWAYS the nominal rate of the port. muscadet
# reads that declaration in two separate places, and the split is not optional:
#
# * ``var_fed_default`` is the nominal rate;
# * ``profile`` is a multiplicative FACTOR on it (``muscadet/profile.py``), and
#   ``PROFILE_CLASSES`` holds ``Profile`` and ``SinusoidalProfile`` only -- there
#   is NO constant shape, because a constant factor is the absence of a profile.
#
# So the importer decomposes: the ``value`` projects onto the nominal rate for
# every shape, and a profile OBJECT is emitted only for a modulated one. A
# constant shape must never reach ``build_profile`` -- there is nothing there
# for it to build, and routing it through ``Profile(lambda t: v)`` would fold
# the rate into the factor, where a derating composes with it by MINIMUM instead
# of by product (cf. the composition rule in ``muscadet/profile.py``).

_PROFILE_CONSTANT_SHAPE = "constant"

# {platform shape name: muscadet PROFILE_CLASSES entry}. ``None`` marks the
# shape that yields no profile object at all.
_PROFILE_SHAPE_CLASSES: Dict[str, Optional[str]] = {
    _PROFILE_CONSTANT_SHAPE: None,
    "sinusoidal": "SinusoidalProfile",
}

# Modulation parameters each shape accepts, beside ``cls`` and ``value``. An
# allowlist rather than a passthrough: an unknown parameter reaching muscadet
# would raise a TypeError from a constructor signature, which names neither the
# port nor the platform shape.
_PROFILE_SHAPE_PARAMS: Dict[str, frozenset] = {
    _PROFILE_CONSTANT_SHAPE: frozenset(),
    "sinusoidal": frozenset(
        {"amplitude", "period", "phase_shift", "offset", "value_min", "value_max"}
    ),
}


def _declared_interface_keys(
    interface: Dict[str, Any], keys: Tuple[str, ...]
) -> List[str]:
    """Return the ``keys`` the interface carries with an actual declaration.

    ``None``, an empty container and the value listed in
    :data:`_NEUTRAL_INTERFACE_VALUES` are information-free: a platform that
    serialises its interface model whole emits ``prod_cond: []`` on a port
    carrying no production condition and ``negate: false`` on every port at
    all, and refusing those would refuse a payload saying nothing. Anything
    else was written on purpose and is a declaration the modeller can be wrong
    about.
    """
    declared = []
    for key in keys:
        if key not in interface:
            continue
        value = interface[key]
        if value is None or value == [] or value == {}:
            continue
        if any(
            value == neutral and isinstance(value, type(neutral))
            for neutral in _NEUTRAL_INTERFACE_VALUES.get(key, ())
        ):
            continue
        declared.append(key)
    return declared


def _check_family_keys(
    interface: Dict[str, Any],
    *,
    name: str,
    family: str,
    forbidden: Tuple[str, ...],
    other_family: str,
) -> None:
    """Refuse, by name, a key belonging to the other flow family."""
    declared = _declared_interface_keys(interface, forbidden)
    if not declared:
        return

    plural = "s" if len(declared) > 1 else ""
    keys = ", ".join(repr(key) for key in declared)
    raise Cod3sPlatformImportError(
        f"Interface {name!r}: flow_family={family!r} does not accept "
        f"{other_family} declaration key{plural} {keys}. Either drop "
        f"{'them' if len(declared) > 1 else 'it'} or declare the port as "
        f"flow_family={other_family!r}."
    )


def _parse_profile(
    raw: Any,
    *,
    flow_name: str,
    key: str,
    allow_modulated: bool,
) -> Tuple[Optional[float], Optional[dict]]:
    """Decompose a platform profile into ``(nominal rate, profile mapping)``.

    Returns ``(None, None)`` when nothing is declared -- the port is then served
    by a recipe or by a capacity, and the muscadet defaults must stand.

    ``allow_modulated`` is False on an input: muscadet carries a profile channel
    on a continuous OUTPUT only, so a modulated demand would be flattened to its
    nominal rate with nothing to signal the loss.
    """
    if raw is None:
        return None, None

    where = f"Interface {flow_name!r}: {key}"

    if not isinstance(raw, dict):
        raise Cod3sPlatformImportError(
            f"{where} must be a mapping {{'cls': <shape>, 'value': <rate>, "
            f"...}}, got {type(raw).__name__}"
        )

    params = dict(raw)
    shape = params.pop("cls", None)

    if shape is None:
        raise Cod3sPlatformImportError(
            f"{where} carries no 'cls' key naming its shape; expected one of "
            f"{sorted(_PROFILE_SHAPE_CLASSES)}"
        )

    if shape not in _PROFILE_SHAPE_CLASSES:
        raise Cod3sPlatformImportError(
            f"{where}: unsupported profile shape cls={shape!r} (expected one "
            f"of {sorted(_PROFILE_SHAPE_CLASSES)})"
        )

    if params.get("value") is None:
        raise Cod3sPlatformImportError(
            f"{where}: a profile carries a 'value', which is the NOMINAL RATE "
            "of the port -- a profile is a multiplicative factor, so one "
            "declared over no rate would produce nothing without signalling it"
        )

    raw_value = params.pop("value")
    try:
        rate = float(raw_value)
    except (TypeError, ValueError):
        raise Cod3sPlatformImportError(
            f"{where}: value must be a real number, got {raw_value!r}"
        ) from None

    if not math.isfinite(rate):
        raise Cod3sPlatformImportError(
            f"{where}: value must be a finite real number, got {raw_value!r}"
        )

    unknown = sorted(set(params) - _PROFILE_SHAPE_PARAMS[shape])
    if unknown:
        plural = "s" if len(unknown) > 1 else ""
        keys = ", ".join(repr(k) for k in unknown)
        accepted = sorted(_PROFILE_SHAPE_PARAMS[shape])
        raise Cod3sPlatformImportError(
            f"{where}: shape {shape!r} does not accept parameter{plural} "
            f"{keys}; it accepts {accepted}"
        )

    cls_name = _PROFILE_SHAPE_CLASSES[shape]
    if cls_name is None:
        # Constant shape: the whole declaration IS the nominal rate. No profile
        # object, so nothing reaches build_profile.
        return rate, None

    if not allow_modulated:
        raise Cod3sPlatformImportError(
            f"{where}: shape {shape!r} is a modulated profile, and muscadet "
            "carries a time profile on a continuous OUTPUT only -- there is no "
            "profile channel on a continuous input. Declare a "
            f"{_PROFILE_CONSTANT_SHAPE!r} shape here."
        )

    return rate, {"cls": cls_name, **params}


def _parse_continuous_rate(raw: Any, *, flow_name: str, key: str) -> Optional[float]:
    """Coerce a bare numeric field of a continuous interface."""
    if raw is None:
        return None

    if isinstance(raw, bool):
        # A bool here is a discrete default that survived a family switch: it
        # would coerce to 0.0/1.0 and read as a plausible rate.
        raise Cod3sPlatformImportError(
            f"Interface {flow_name!r}: {key} must be a real number on a "
            f"continuous port, got the boolean {raw!r}"
        )

    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise Cod3sPlatformImportError(
            f"Interface {flow_name!r}: {key} must be a real number, got {raw!r}"
        ) from None

    if not math.isfinite(value):
        raise Cod3sPlatformImportError(
            f"Interface {flow_name!r}: {key} must be a finite real number, "
            f"got {raw!r}"
        )

    return value


def _parse_allocation(raw: Any, *, flow_name: str) -> str:
    """Validate the allocation policy of a continuous output."""
    if raw is None:
        return _ALLOCATION_PROPORTIONAL

    if raw not in _VALID_ALLOCATIONS:
        raise Cod3sPlatformImportError(
            f"Interface {flow_name!r}: unsupported allocation={raw!r} "
            f"(expected one of {sorted(_VALID_ALLOCATIONS)})"
        )

    return raw


def _parse_continuous_interface(
    interface: Dict[str, Any], *, name: str, port_type: str
) -> FlowSpec:
    """Translate one CONTINUOUS KB interface into a :class:`FlowSpec`.

    ``logic`` is set to an empty list on both directions: the continuous family
    reads no boolean production condition and no input aggregation logic, and
    the empty list is what keeps the intra-component output ordering (which
    walks ``logic``) a no-op rather than a special case.
    """
    _check_family_keys(
        interface,
        name=name,
        family=CONTINUOUS_FAMILY,
        forbidden=_DISCRETE_ONLY_INTERFACE_KEYS,
        other_family=DISCRETE_FAMILY,
    )

    if port_type == "input":
        rate, _ = _parse_profile(
            interface.get("demand_profile"),
            flow_name=name,
            key="demand_profile",
            allow_modulated=False,
        )
        return FlowSpec(
            name=name,
            direction="input",
            logic=[],
            flow_family=CONTINUOUS_FAMILY,
            var_in_default=_parse_continuous_rate(
                interface.get("var_in_default"),
                flow_name=name,
                key="var_in_default",
            ),
            nominal_rate=rate,
        )

    rate, profile_spec = _parse_profile(
        interface.get("production_profile"),
        flow_name=name,
        key="production_profile",
        allow_modulated=True,
    )
    return FlowSpec(
        name=name,
        direction="output",
        logic=[],
        flow_family=CONTINUOUS_FAMILY,
        nominal_rate=rate,
        profile_spec=profile_spec,
        allocation=_parse_allocation(interface.get("allocation"), flow_name=name),
    )


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

    A ``flow_family`` discriminator selects the muscadet flow FAMILY before any
    of the above is read: ``'discrete'`` (the default, and the whole of the
    schema described here) or ``'continuous'``, which is parsed by
    :func:`_parse_continuous_interface` and shares none of the discrete keys.
    An unknown family is refused rather than read as discrete.
    """
    name = interface.get("name")
    if not name:
        raise Cod3sPlatformImportError(f"Interface missing 'name' field: {interface!r}")
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

    # Flow family first: it decides which schema the rest of the interface is
    # read under. No fallback on an unknown value — reading a continuous port as
    # discrete builds a boolean where a rate was declared.
    flow_family = interface.get("flow_family") or DISCRETE_FAMILY
    if flow_family not in _VALID_FLOW_FAMILIES:
        raise Cod3sPlatformImportError(
            f"Interface {name!r}: unsupported flow_family={flow_family!r} "
            f"(expected one of {sorted(_VALID_FLOW_FAMILIES)})"
        )
    if flow_family == CONTINUOUS_FAMILY:
        return _parse_continuous_interface(interface, name=name, port_type=port_type)

    _check_family_keys(
        interface,
        name=name,
        family=DISCRETE_FAMILY,
        forbidden=_CONTINUOUS_ONLY_INTERFACE_KEYS,
        other_family=CONTINUOUS_FAMILY,
    )

    if port_type == "input":
        return FlowSpec(
            name=name,
            direction="input",
            logic=interface.get("input_logic", "or"),
        )
    # output
    flow_type = interface.get("flow_type") or "classic"
    if flow_type not in _VALID_FLOW_TYPES:
        raise Cod3sPlatformImportError(
            f"Interface {name!r}: unsupported flow_type={flow_type!r} "
            f"(expected one of {sorted(_VALID_FLOW_TYPES)})"
        )
    return FlowSpec(
        name=name,
        direction="output",
        logic=interface.get("prod_cond", []),
        # Default 'and' = outer-OR / inner-AND, matching the KB Editor UI.
        # See FlowSpec.logic_inner_mode docstring for rationale.
        logic_inner_mode=interface.get("logic_inner_mode", "and"),
        negate=bool(interface.get("negate", False)),
        flow_type=flow_type,
        occ_enable=interface.get("occ_enable"),
        occ_disable=interface.get("occ_disable"),
        init_enable=interface.get("init_enable"),
        trigger_time_up=interface.get("trigger_time_up"),
        trigger_time_down=interface.get("trigger_time_down"),
        trigger_logic=interface.get("trigger_logic"),
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
# Capacity and rule-set parsing (2026-08)
# ---------------------------------------------------------------------------

#: Keys a ``capacities`` entry may carry. An allowlist, like everything else in
#: this module: a misspelled ``fillrate`` would otherwise take its default and
#: build a pure buffer where a tank was meant, which no reading of the model
#: distinguishes from a tank that was never asked to fill.
_CAPACITY_KEYS = frozenset(
    {"name", "flows", "flow", "volume", "side", "content_init", "fill_rate"}
)

#: Keys a held-flow entry may carry.
_CAPACITY_FLOW_KEYS = frozenset({"name", "weight"})

#: Keys a ``rule_sets`` entry may carry.
_RULE_SET_KEYS = frozenset({"name", "rules"})

#: Keys one rule may carry.
_RULE_KEYS = frozenset({"name", "cond", "cons", "prod"})

#: Keys one guard operand may carry.
_RULE_OPERAND_KEYS = frozenset({"name", "negate", "port", "op", "value"})

#: Sides a capacity, or a guard operand, may name.
_VALID_SIDES = frozenset({"in", "out"})

#: Comparison operators a numeric guard operand may carry. muscadet's own set;
#: restated so an unsupported spelling is refused naming the class rather than
#: at guard-compilation time.
_VALID_GUARD_OPS = frozenset({"<", "<=", ">", ">=", "==", "!="})

#: Spellings of an unbounded fill rate. JSON has no literal for infinity, and
#: ``Infinity`` (what Python's ``json`` writes) is not portable, so the string
#: form is part of the contract rather than a convenience.
_INFINITE_SPELLINGS = frozenset({"inf", "+inf", "infinity", "+infinity"})


def _coerce_number(raw: Any, *, where: str, allow_infinite: bool = False) -> float:
    """Coerce a declared number, refusing a bool and a non-finite by default.

    A bool is refused rather than coerced for the reason it is refused on a
    continuous rate: ``True`` becomes a perfectly plausible 1.0, and a discrete
    default that survived an edit reads as a deliberate quantity.
    """
    if isinstance(raw, bool):
        raise Cod3sPlatformImportError(
            f"{where} must be a real number, got the boolean {raw!r}"
        )

    if allow_infinite and isinstance(raw, str):
        if raw.strip().lower() in _INFINITE_SPELLINGS:
            return math.inf

    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise Cod3sPlatformImportError(
            f"{where} must be a real number, got {raw!r}"
        ) from None

    if math.isnan(value) or (not allow_infinite and not math.isfinite(value)):
        raise Cod3sPlatformImportError(
            f"{where} must be a finite real number, got {raw!r}"
        )

    return value


def _section_entries(
    template: Dict[str, Any], section: str, *, class_name: str
) -> List[Dict[str, Any]]:
    """The entries of one class-template section, refusing anything but a list.

    A mapping is NOT silently wrapped: ``interfaces`` is a mapping and these two
    sections are lists, and tolerating both here would leave a platform free to
    emit either, which is how a contract stops being one.
    """
    raw = template.get(section)
    if raw is None:
        return []

    if not isinstance(raw, list):
        raise Cod3sPlatformImportError(
            f"Class {class_name!r}: section {section!r} is a LIST of "
            f"declarations, got {type(raw).__name__}"
        )

    for entry in raw:
        if not isinstance(entry, dict):
            raise Cod3sPlatformImportError(
                f"Class {class_name!r}: every entry of section {section!r} is a "
                f"mapping, got {type(entry).__name__}"
            )

    return raw


def _check_entry_keys(entry: Dict[str, Any], allowed: frozenset, *, where: str) -> None:
    """Refuse, by name, a key the entry has no reader for."""
    unknown = sorted(set(entry) - allowed)
    if not unknown:
        return
    plural = "s" if len(unknown) > 1 else ""
    keys = ", ".join(repr(key) for key in unknown)
    raise Cod3sPlatformImportError(
        f"{where}: unknown declaration key{plural} {keys}; it accepts "
        f"{', '.join(sorted(allowed))}"
    )


class _FlowIndex:
    """The flows of one class template, indexed for the two sections to check.

    Both a capacity and a rule name flows, and both must be able to tell three
    situations apart -- the name is unknown, the name exists on the other side,
    the name exists but is discrete -- because each calls for a different fix.
    """

    def __init__(self, flows: List[FlowSpec]):
        self.inputs = {flow.name: flow for flow in flows if flow.direction == "input"}
        self.outputs = {flow.name: flow for flow in flows if flow.direction == "output"}
        self.continuous = {
            flow.name for flow in flows if flow.flow_family == CONTINUOUS_FAMILY
        }
        self.names = set(self.inputs) | set(self.outputs)

    @property
    def has_continuous(self) -> bool:
        return bool(self.continuous)

    def on_side(self, side: str) -> Dict[str, FlowSpec]:
        return self.inputs if side == "in" else self.outputs


def _parse_capacity_flows(
    entry: Dict[str, Any], *, where: str, index: _FlowIndex
) -> Tuple[CapacityFlowSpec, ...]:
    """The held flows of one capacity, validated against the class's ports."""
    if "flow" in entry and "flows" in entry:
        raise Cod3sPlatformImportError(
            f"{where}: give either 'flow' (the single-flow short form) or "
            "'flows', not both"
        )

    raw = entry.get("flows", entry.get("flow"))
    if raw is None:
        raise Cod3sPlatformImportError(
            f"{where}: declare the flows it holds with 'flow' or 'flows'"
        )
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise Cod3sPlatformImportError(
            f"{where}: 'flows' is a non-empty list of flow names or of "
            f"{{'name', 'weight'}} mappings, got {raw!r}"
        )

    held: List[CapacityFlowSpec] = []
    seen: set = set()
    for item in raw:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            raise Cod3sPlatformImportError(
                f"{where}: a held flow is a name or a {{'name', 'weight'}} "
                f"mapping, got {type(item).__name__}"
            )
        _check_entry_keys(item, _CAPACITY_FLOW_KEYS, where=f"{where}, held flow")

        name = item.get("name")
        if not name:
            raise Cod3sPlatformImportError(f"{where}: a held flow carries no 'name'")
        if name in seen:
            raise Cod3sPlatformImportError(
                f"{where}: flow {name!r} is held twice; one entry holds it once, "
                "with one weight"
            )
        seen.add(name)

        if name not in index.names:
            raise Cod3sPlatformImportError(
                f"{where}: flow {name!r} is not an interface of this class "
                f"(declared: {sorted(index.names)})"
            )
        if name not in index.continuous:
            raise Cod3sPlatformImportError(
                f"{where}: flow {name!r} is a discrete flow; a capacity holds "
                "continuous flows only -- declare the interface with "
                f"flow_family={CONTINUOUS_FAMILY!r}"
            )

        weight = 1.0
        if item.get("weight") is not None:
            weight = _coerce_number(
                item["weight"], where=f"{where}, held flow {name!r}: weight"
            )
            if weight <= 0:
                raise Cod3sPlatformImportError(
                    f"{where}, held flow {name!r}: weight must be strictly "
                    f"positive, got {weight}"
                )

        held.append(CapacityFlowSpec(name=name, weight=weight))

    return tuple(held)


def _parse_capacity(
    entry: Dict[str, Any], *, class_name: str, index: _FlowIndex
) -> CapacitySpec:
    """Translate one ``capacities`` entry into a :class:`CapacitySpec`."""
    name = entry.get("name")
    if not name:
        raise Cod3sPlatformImportError(
            f"Class {class_name!r}: a capacity carries no 'name': {entry!r}"
        )

    where = f"Class {class_name!r}, capacity {name!r}"
    _check_entry_keys(entry, _CAPACITY_KEYS, where=where)

    flows = _parse_capacity_flows(entry, where=where, index=index)

    if entry.get("volume") is None:
        raise Cod3sPlatformImportError(
            f"{where}: 'volume', the quantity the held flows SHARE, is required"
        )
    volume = _coerce_number(entry["volume"], where=f"{where}: volume")
    if volume <= 0:
        raise Cod3sPlatformImportError(
            f"{where}: volume must be strictly positive, got {volume}"
        )

    side = entry.get("side")
    if side is not None and side not in _VALID_SIDES:
        raise Cod3sPlatformImportError(
            f"{where}: side must be one of {sorted(_VALID_SIDES)}, got {side!r}"
        )
    if side is not None:
        missing = [held.name for held in flows if held.name not in index.on_side(side)]
        if missing:
            kind = "input" if side == "in" else "output"
            raise Cod3sPlatformImportError(
                f"{where} is declared on side {side!r} but "
                f"{', '.join(repr(m) for m in missing)} is not an {kind} flow "
                "of this class"
            )

    held_names = {held.name for held in flows}
    content_init: Dict[str, float] = {}
    raw_content = entry.get("content_init")
    if raw_content is not None:
        if not isinstance(raw_content, dict):
            raise Cod3sPlatformImportError(
                f"{where}: 'content_init' is a {{flow: quantity}} mapping, got "
                f"{type(raw_content).__name__}"
            )
        for flow_name, quantity in raw_content.items():
            if flow_name not in held_names:
                raise Cod3sPlatformImportError(
                    f"{where}: 'content_init' names flow {flow_name!r}, which "
                    f"this capacity does not hold (it holds "
                    f"{sorted(held_names)})"
                )
            content_init[flow_name] = _coerce_number(
                quantity, where=f"{where}: content_init[{flow_name!r}]"
            )

    fill_rate = None
    if entry.get("fill_rate") is not None:
        fill_rate = _coerce_number(
            entry["fill_rate"], where=f"{where}: fill_rate", allow_infinite=True
        )
        if fill_rate < 0:
            raise Cod3sPlatformImportError(
                f"{where}: fill_rate must be positive or zero, got {fill_rate}"
            )

    return CapacitySpec(
        name=name,
        flows=flows,
        volume=volume,
        side=side,
        content_init=content_init,
        fill_rate=fill_rate,
    )


def _build_kb_capacities(
    kb: Dict[str, Any], kb_lookup: Dict[str, List[FlowSpec]]
) -> Dict[str, Tuple[CapacitySpec, ...]]:
    """Compute ``{class_name: (CapacitySpec, ...)}`` from the KB dict.

    Kept apart from :func:`_build_kb_lookup` rather than folded into it: that
    function's ``{class: [FlowSpec]}`` shape is what every existing caller and
    test reads, and a capacity needs the parsed flows to validate itself
    against, so it comes second by dependency as well as by compatibility.
    """
    templates = kb.get("component_templates") or {}
    out: Dict[str, Tuple[CapacitySpec, ...]] = {}

    for class_name, template in templates.items():
        entries = _section_entries(template, "capacities", class_name=class_name)
        if not entries:
            out[class_name] = ()
            continue

        if _gate_kind_of_template(template) is not None:
            raise Cod3sPlatformImportError(
                f"Class {class_name!r} is a logic gate and declares capacities. "
                "A gate is a combinational component reading its sources "
                "directly; it holds no flow and therefore no volume."
            )

        index = _FlowIndex(kb_lookup.get(class_name) or [])
        if not index.has_continuous:
            raise Cod3sPlatformImportError(
                f"Class {class_name!r} declares a capacity but carries no "
                f"continuous interface. A volume is held over continuous flows "
                f"only -- declare the port with flow_family="
                f"{CONTINUOUS_FAMILY!r} first."
            )

        capacities: List[CapacitySpec] = []
        seen: set = set()
        for entry in entries:
            capacity = _parse_capacity(entry, class_name=class_name, index=index)
            if capacity.name in seen:
                raise Cod3sPlatformImportError(
                    f"Class {class_name!r}: capacity {capacity.name!r} is "
                    "declared twice; a capacity name is unique on a component"
                )
            seen.add(capacity.name)
            capacities.append(capacity)

        out[class_name] = tuple(capacities)

    return out


def _parse_rule_operand(
    raw: Any, *, where: str, index: _FlowIndex, capacity_names: set
) -> RuleOperandSpec:
    """Translate one guard operand, validated against the class's ports."""
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict):
        raise Cod3sPlatformImportError(
            f"{where}: a guard operand is a flow name or a "
            f"{{'name', 'negate', 'port', 'op', 'value'}} mapping, got "
            f"{type(raw).__name__}"
        )
    _check_entry_keys(raw, _RULE_OPERAND_KEYS, where=f"{where}, guard operand")

    name = raw.get("name")
    if not name:
        raise Cod3sPlatformImportError(f"{where}: a guard operand carries no 'name'")

    port = raw.get("port")
    if port is not None and port not in _VALID_SIDES:
        raise Cod3sPlatformImportError(
            f"{where}, guard operand {name!r}: port must be one of "
            f"{sorted(_VALID_SIDES)}, got {port!r}"
        )

    _check_rule_flow_name(
        name,
        where=f"{where}, guard operand",
        side=port,
        index=index,
        capacity_names=capacity_names,
    )

    op = raw.get("op")
    value = raw.get("value")
    if op is not None:
        if op not in _VALID_GUARD_OPS:
            raise Cod3sPlatformImportError(
                f"{where}, guard operand {name!r}: unsupported comparison "
                f"op={op!r} (expected one of {sorted(_VALID_GUARD_OPS)})"
            )
        if value is None:
            raise Cod3sPlatformImportError(
                f"{where}, guard operand {name!r}: a comparison carries the "
                "'value' it compares to"
            )
        value = _coerce_number(value, where=f"{where}, guard operand {name!r}: value")
    elif value is not None:
        raise Cod3sPlatformImportError(
            f"{where}, guard operand {name!r}: a 'value' without an 'op' "
            "compares to nothing; declare the operator or drop the value"
        )

    return RuleOperandSpec(
        name=name,
        negate=bool(raw.get("negate", False)),
        port=port,
        op=op,
        value=value,
    )


def _check_rule_flow_name(
    name: str,
    *,
    where: str,
    side: Optional[str],
    index: _FlowIndex,
    capacity_names: set,
) -> None:
    """Refuse a rule name that is not a flow reachable on ``side``.

    A name designating a declared CAPACITY is refused as such rather than as an
    unknown flow: muscadet refuses it too (an interposed capacity replaces the
    flow it buffers automatically, so rules name flows and never capacities),
    and "flow 'cuve' does not exist" would send the reader looking for a port
    they never meant to declare.
    """
    if name in capacity_names:
        raise Cod3sPlatformImportError(
            f"{where} references capacity {name!r}, which is not a flow. An "
            "interposed capacity replaces the flow it buffers automatically, "
            "so a rule names the FLOW the capacity holds."
        )

    if side is None:
        if name not in index.names:
            raise Cod3sPlatformImportError(
                f"{where} references flow {name!r}, which this class does not "
                f"declare (declared: {sorted(index.names)})"
            )
        return

    if name not in index.on_side(side):
        kind = "input" if side == "in" else "output"
        other = "output" if side == "in" else "input"
        detail = f" -- it is declared as an {other} flow" if name in index.names else ""
        raise Cod3sPlatformImportError(
            f"{where} references flow {name!r}, which is not an {kind} flow of "
            f"this class{detail} (its {kind}s: {sorted(index.on_side(side))})"
        )


def _parse_rule_coefficients(
    raw: Any,
    *,
    where: str,
    key: str,
    side: str,
    index: _FlowIndex,
    capacity_names: set,
) -> Dict[str, float]:
    """One ``cons`` / ``prod`` map, validated against the class's ports."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise Cod3sPlatformImportError(
            f"{where}: {key!r} is a {{flow: coefficient}} mapping, got "
            f"{type(raw).__name__}"
        )

    out: Dict[str, float] = {}
    for name, coefficient in raw.items():
        _check_rule_flow_name(
            name,
            where=f"{where}, {key!r} map",
            side=side,
            index=index,
            capacity_names=capacity_names,
        )
        out[name] = _coerce_number(coefficient, where=f"{where}: {key}[{name!r}]")
    return out


def _parse_rule(
    entry: Dict[str, Any], *, where: str, index: _FlowIndex, capacity_names: set
) -> RuleSpec:
    """Translate one rule of a rule set."""
    _check_entry_keys(entry, _RULE_KEYS, where=where)

    raw_cond = entry.get("cond")
    if isinstance(raw_cond, str):
        raise Cod3sPlatformImportError(
            f"{where}: 'cond' is a LIST of operands. muscadet also reads an "
            "expression string, but a mini-language embedded in an exported "
            "payload cannot be validated against the class's own ports, so the "
            "bridge carries the structured form only: "
            "[{'name': ..., 'negate': ..., 'port': ..., 'op': ..., 'value': ...}]"
        )
    if raw_cond is None:
        raw_cond = []
    if isinstance(raw_cond, dict):
        raw_cond = [raw_cond]
    if not isinstance(raw_cond, list):
        raise Cod3sPlatformImportError(
            f"{where}: 'cond' is a list of operands, got " f"{type(raw_cond).__name__}"
        )

    cond = tuple(
        _parse_rule_operand(
            operand, where=where, index=index, capacity_names=capacity_names
        )
        for operand in raw_cond
    )

    return RuleSpec(
        name=entry.get("name"),
        cond=cond,
        cons=_parse_rule_coefficients(
            entry.get("cons"),
            where=where,
            key="cons",
            side="in",
            index=index,
            capacity_names=capacity_names,
        ),
        prod=_parse_rule_coefficients(
            entry.get("prod"),
            where=where,
            key="prod",
            side="out",
            index=index,
            capacity_names=capacity_names,
        ),
    )


def _parse_rule_set(
    entry: Dict[str, Any], *, class_name: str, index: _FlowIndex, capacity_names: set
) -> RuleSetSpec:
    """Translate one ``rule_sets`` entry into a :class:`RuleSetSpec`."""
    name = entry.get("name")
    if not name:
        raise Cod3sPlatformImportError(
            f"Class {class_name!r}: a rule set carries no 'name': {entry!r}"
        )

    where = f"Class {class_name!r}, rule set {name!r}"
    _check_entry_keys(entry, _RULE_SET_KEYS, where=where)

    raw_rules = entry.get("rules")
    if raw_rules is None:
        raw_rules = []
    if isinstance(raw_rules, dict):
        raw_rules = [raw_rules]
    if not isinstance(raw_rules, list):
        raise Cod3sPlatformImportError(
            f"{where}: 'rules' is a list of rules, got {type(raw_rules).__name__}"
        )

    rules: List[RuleSpec] = []
    for position, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            raise Cod3sPlatformImportError(
                f"{where}: every rule is a mapping, got " f"{type(raw_rule).__name__}"
            )
        label = raw_rule.get("name") or f"rule #{position}"
        rules.append(
            _parse_rule(
                raw_rule,
                where=f"{where}, {label}",
                index=index,
                capacity_names=capacity_names,
            )
        )

    defaults = [
        rule.name or f"rule #{position}"
        for position, rule in enumerate(rules)
        if not rule.cond
    ]
    if len(defaults) > 1:
        raise Cod3sPlatformImportError(
            f"{where}: {len(defaults)} rules carry no guard "
            f"({', '.join(repr(label) for label in defaults)}). A rule without "
            "a guard is the DEFAULT rule of its set, applying when no other "
            "matches, and a set holds at most one."
        )

    return RuleSetSpec(name=name, rules=tuple(rules))


def _build_kb_rule_sets(
    kb: Dict[str, Any],
    kb_lookup: Dict[str, List[FlowSpec]],
    capacities_lookup: Dict[str, Tuple[CapacitySpec, ...]],
) -> Dict[str, Tuple[RuleSetSpec, ...]]:
    """Compute ``{class_name: (RuleSetSpec, ...)}`` from the KB dict.

    Takes the capacities as well as the flows because a rule REFUSES a capacity
    name in a guard or in a coefficient map, and that refusal is only reachable
    once the capacities of the class are known -- the same dependency muscadet's
    own ``DECLARATION_SECTIONS`` encodes by ordering capacities before rules.
    """
    templates = kb.get("component_templates") or {}
    out: Dict[str, Tuple[RuleSetSpec, ...]] = {}

    for class_name, template in templates.items():
        entries = _section_entries(template, "rule_sets", class_name=class_name)
        if not entries:
            out[class_name] = ()
            continue

        if _gate_kind_of_template(template) is not None:
            raise Cod3sPlatformImportError(
                f"Class {class_name!r} is a logic gate and declares rule sets. "
                "A gate aggregates booleans through its own kind; it "
                "transforms no quantity."
            )

        index = _FlowIndex(kb_lookup.get(class_name) or [])
        capacity_names = {
            capacity.name for capacity in capacities_lookup.get(class_name) or ()
        }

        rule_sets: List[RuleSetSpec] = []
        seen: set = set()
        for entry in entries:
            rule_set = _parse_rule_set(
                entry,
                class_name=class_name,
                index=index,
                capacity_names=capacity_names,
            )
            if rule_set.name in seen:
                raise Cod3sPlatformImportError(
                    f"Class {class_name!r}: rule set {rule_set.name!r} is "
                    "declared twice; a rule set name is unique on a component"
                )
            seen.add(rule_set.name)
            rule_sets.append(rule_set)

        out[class_name] = tuple(rule_sets)

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


# ---------------------------------------------------------------------------
# Controller templates (R46) -- parse layer
# ---------------------------------------------------------------------------
#
# The discriminant is ``metadata.controller``, truthy. It is required even
# though the two sections would be enough to guess: a template that declares
# ``controls_in`` without the marker is far more likely to be a mistake than a
# shorthand, and guessing would import it as a component with no port and no
# regulation, which no reading of the built system distinguishes from a plant
# that was never regulated.

#: Metadata key marking a class template as a controller.
CONTROLLER_MARKER = "controller"

#: The two sections a controller template declares, and the ONLY two. Their
#: order is muscadet's, recorded in ``muscadet.declare.DECLARATION_SECTIONS``
#: and applied by ``ObjCtrl.__init__``; this tuple only says which sections
#: belong to a controller, so that a section found on the wrong kind of
#: template can be refused by name.
_CONTROLLER_SECTIONS = ("controls_in", "controls_out")

#: Sections that belong to a component that TRANSPORTS something, and are
#: therefore refused on a controller. ``interfaces`` is a mapping and the other
#: two are lists, so they are named rather than walked.
_TRANSPORT_SECTIONS = ("interfaces", "capacities", "rule_sets")


def _is_controller_template(template: Any) -> bool:
    """True when the KB class template carries the controller marker.

    ``Any`` and not ``Dict``, like :func:`_gate_kind_of_template`: what a
    payload puts under a class name is whatever it puts there, and answering
    "not a controller" is a better outcome than an attribute error.
    """
    if not isinstance(template, dict):
        return False
    return bool((template.get("metadata") or {}).get(CONTROLLER_MARKER))


def _parse_control_in(entry: Dict[str, Any], *, class_name: str) -> ControlInSpec:
    """One observation input, validated against the closed vocabularies."""
    where = f"Class {class_name!r}, controller input"
    _check_entry_keys(entry, _CONTROL_IN_KEYS, where=where)

    name = entry.get("name")
    if not name or not isinstance(name, str):
        raise Cod3sPlatformImportError(
            f"{where}: every entry carries a non-empty 'name'; it is also the "
            "name of the publisher it observes, the two message-box aliases "
            "being matched by string equality"
        )

    kind = entry.get("kind") or CONTROL_IN_LEVEL
    if kind not in _VALID_CONTROL_IN_KINDS:
        raise Cod3sPlatformImportError(
            f"{where} {name!r}: unknown kind {kind!r}, expected one of "
            f"{sorted(_VALID_CONTROL_IN_KINDS)}"
        )

    aggregate = entry.get("aggregate")
    if aggregate is not None and aggregate not in _VALID_CONTROL_AGGREGATIONS:
        raise Cod3sPlatformImportError(
            f"{where} {name!r}: unknown aggregate {aggregate!r}, expected one "
            f"of {sorted(_VALID_CONTROL_AGGREGATIONS)}. Omit it for the "
            "single-publisher input; there is no way to read several sources "
            "without saying how their readings reduce"
        )

    return ControlInSpec(
        name=name,
        kind=kind,
        aggregate=aggregate,
        flows=_parse_control_flows(entry.get("flows"), where=f"{where} {name!r}"),
        level_default=_parse_control_number(entry, "level_default", where, name),
        fill_default=_parse_control_number(entry, "fill_default", where, name),
        rate_default=_parse_control_number(entry, "rate_default", where, name),
    )


def _parse_control_flows(raw: Any, *, where: str) -> Tuple[str, ...]:
    """The constituent list of a channel, refusing anything but names."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(
        isinstance(item, str) and item for item in raw
    ):
        raise Cod3sPlatformImportError(
            f"{where}: 'flows' is a list of constituent names, got {raw!r}"
        )
    return tuple(raw)


def _parse_control_number(
    entry: Dict[str, Any], key: str, where: str, name: str
) -> Optional[float]:
    """One optional numeric declaration key of a controller interface."""
    if entry.get(key) is None:
        return None
    return _coerce_number(entry[key], where=f"{where} {name!r}: {key}")


def _parse_control_out(entry: Dict[str, Any], *, class_name: str) -> ControlOutSpec:
    """One output, validated against the closed vocabularies.

    ``emit`` is carried through untouched. What may be written there is the
    closed grammar of :func:`muscadet.build_ctrl_node`, which refuses a
    malformed one by name and BEFORE the engine object exists; re-deciding it
    here would be a second grammar, and the two would drift on the first
    operator either side gained.
    """
    where = f"Class {class_name!r}, controller output"
    kind = entry.get("kind") or CONTROL_OUT_BOOL
    if kind not in _VALID_CONTROL_OUT_KINDS:
        raise Cod3sPlatformImportError(
            f"{where} {entry.get('name')!r}: unknown kind {kind!r}, expected "
            f"one of {sorted(_VALID_CONTROL_OUT_KINDS)}"
        )

    accepted = (
        _CONTROL_OUT_BOOL_KEYS if kind == CONTROL_OUT_BOOL else _CONTROL_OUT_VALUE_KEYS
    )
    _check_entry_keys(entry, accepted, where=f"{where} ({kind})")

    name = entry.get("name")
    if not name or not isinstance(name, str):
        raise Cod3sPlatformImportError(
            f"{where}: every entry carries a non-empty 'name'; it is also the "
            "exported alias, so the importing end must bear the same name"
        )

    emit = entry.get("emit")
    if emit is not None and not isinstance(emit, dict):
        raise Cod3sPlatformImportError(
            f"{where} {name!r}: 'emit' is a mapping naming an operator, got "
            f"{type(emit).__name__}"
        )

    default = entry.get("default")
    if default is not None and not isinstance(default, bool):
        raise Cod3sPlatformImportError(
            f"{where} {name!r}: 'default' is the boolean the signal rests at, "
            f"got {default!r}"
        )

    return ControlOutSpec(
        name=name,
        kind=kind,
        default=default,
        emit=copy.deepcopy(emit) if emit is not None else None,
        flows=_parse_control_flows(entry.get("flows"), where=f"{where} {name!r}"),
        level_default=_parse_control_number(entry, "level_default", where, name),
        fill_default=_parse_control_number(entry, "fill_default", where, name),
        gain_default=_parse_control_number(entry, "gain_default", where, name),
    )


def _parse_controller_template(
    template: Dict[str, Any], *, class_name: str
) -> ControllerSpec:
    """The two sections of one controller class template, in declaration order."""
    if _gate_kind_of_template(template) is not None:
        raise Cod3sPlatformImportError(
            f"Class {class_name!r} is marked BOTH a controller and a logic "
            "gate. The two are different components: a gate aggregates "
            "booleans through its kind, a controller observes readings and "
            "publishes signals. Declare one or the other."
        )

    for section in _TRANSPORT_SECTIONS:
        if template.get(section):
            raise Cod3sPlatformImportError(
                f"Class {class_name!r} is a controller and declares "
                f"{section!r}. A controller transports nothing: it holds no "
                "flow, therefore no volume and no recipe. Its own two sections "
                f"are {', '.join(_CONTROLLER_SECTIONS)}."
            )

    controls_in = tuple(
        _parse_control_in(entry, class_name=class_name)
        for entry in _section_entries(template, "controls_in", class_name=class_name)
    )
    controls_out = tuple(
        _parse_control_out(entry, class_name=class_name)
        for entry in _section_entries(template, "controls_out", class_name=class_name)
    )

    if not controls_in and not controls_out:
        raise Cod3sPlatformImportError(
            f"Class {class_name!r} is a controller and declares neither an "
            "observation input nor an output. It would observe nothing and "
            "drive nothing."
        )

    # One name space across both sections, which is muscadet's own rule
    # (``ObjCtrl.claim_name``): an output grammar names an INTERFACE, and a name
    # standing for two of them has no unambiguous answer. Refused here so the
    # message names the class rather than the engine object.
    seen: set = set()
    for entry_name in list(controls_in) + list(controls_out):
        if entry_name.name in seen:
            raise Cod3sPlatformImportError(
                f"Class {class_name!r}: controller interface "
                f"{entry_name.name!r} is declared twice. An interface name is "
                "unique across a controller, inputs and outputs together."
            )
        seen.add(entry_name.name)

    return ControllerSpec(controls_in=controls_in, controls_out=controls_out)


def _build_controller_specs(kb: Dict[str, Any]) -> Dict[str, ControllerSpec]:
    """Compute ``{class_name: ControllerSpec}`` for every controller template.

    Empty for a KB with no controller -- which is every KB written before this
    existed, and the reason the whole feature costs such a payload nothing.

    Also the one place a controller SECTION on a non-controller template is
    refused. Left unchecked, it would be dropped in silence, and a class meant
    to regulate would build as a plain transporter.
    """
    templates = kb.get("component_templates") or {}
    out: Dict[str, ControllerSpec] = {}

    for class_name, template in templates.items():
        if not _is_controller_template(template):
            declared = [
                section
                for section in _CONTROLLER_SECTIONS
                if isinstance(template, dict) and template.get(section)
            ]
            if declared:
                raise Cod3sPlatformImportError(
                    f"Class {class_name!r} declares {', '.join(declared)} but "
                    f"carries no {CONTROLLER_MARKER!r} marker in its metadata. "
                    "Only a controller reads those sections; without the "
                    "marker the class would build as a plain transporter and "
                    "the declaration would be lost."
                )
            continue

        out[class_name] = _parse_controller_template(template, class_name=class_name)

    return out


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
    "var_in_default": "input",
    "prod_init": "output",
    # Service-function dormancy default (output) → FlowOut.var_is_active_default.
    "active_init": "output",
    # Service-function dormancy via availability gate (output) →
    # FlowOut.var_fed_available_out_init. User-facing UI role.
    "fed_available_init": "output",
    # Availability-gate reset control (output) →
    # FlowOut.var_fed_available_out_reset. User-facing UI role.
    "fed_available_reset": "output",
    # Tempo enable/disable occurrence laws (output) → FlowOutTempo
    # occ_enable_flow / occ_disable_flow. Value is a SHORT-wire occurrence-law
    # dict or the ``{"cls": "none"}`` sentinel (force classic). Setting a law on
    # a classic flow promotes it to tempo (flow_type is derived from the laws,
    # see _derive_output_flow_type). Cf. tempo-in-attributes chantier (2026-07).
    "tempo_activation": "output",
    "tempo_deactivation": "output",
}
_OVERRIDE_ROLES: frozenset = frozenset(_ROLE_TO_DIRECTION)

# --- Per-instance CAPACITY overrides (2026-08) -----------------------------
#
# The pre-existing override channel indexes ``(name, role)`` against a FLOW
# spec, so nothing in it can carry a capacity: a capacity is not a flow, it has
# no direction, and its name lives in a namespace of its own. These roles reuse
# the same ``attributes`` list on the MODEL COMPONENT and read ``name`` as the
# CAPACITY name, which is what lets two components of one class hold different
# volumes without splitting the class in two.
#
# They must be REGISTERED, not merely handled: an unknown role is logged and
# dropped by ``_build_overrides_index``, so an unregistered capacity role would
# leave every tank at the template's volume with nothing in the model saying so.

#: The volume the held flows share. Always overridable: it is one scalar and it
#: is the number that differs between two instances of one class.
CAPACITY_VOLUME_ROLE = "capacity_volume"

#: The initial raw quantity, on a SINGLE-FLOW capacity only. The engine indexes
#: a content by held flow, so a scalar override addresses one flow and one
#: only; on a mixture it would have to say which constituent it fills, and a
#: mapping-valued attribute is a channel the platform does not have.
CAPACITY_CONTENT_INIT_ROLE = "capacity_content_init"

#: Declared so it can be REFUSED by name. The fill rate is frozen at the
#: template on purpose -- it is a property of the buffering behaviour, not of
#: the instance -- and leaving the role out of the registry would have it
#: logged and dropped, which is the one outcome worth avoiding.
CAPACITY_FILL_RATE_ROLE = "capacity_fill_rate"

_CAPACITY_OVERRIDE_ROLES: frozenset = frozenset(
    {CAPACITY_VOLUME_ROLE, CAPACITY_CONTENT_INIT_ROLE, CAPACITY_FILL_RATE_ROLE}
)
# Roles that exist on the platform but are NOT instance configuration
# overrides — they are runtime observables (is_available, fed_out,
# fed_in, is_active) and the importer ignores them silently.
_OBSERVABLE_ROLES: frozenset = frozenset(
    {"is_available", "fed_out", "fed_in", "is_active"}
)

# Type aliases — composite key for instance attribute overrides.
OverrideKey = Tuple[str, str]  # (flow_name, role)
OverridesIndex = Dict[OverrideKey, Any]


def _parse_input_logic_value(
    raw: Any, *, flow_name: str, comp_name: str
) -> Union[str, int]:
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
        if role in _CAPACITY_OVERRIDE_ROLES:
            # Read by _build_capacity_overrides_index instead: ``name`` is a
            # capacity there, so this index -- keyed against flow specs --
            # would look it up among the flows and find nothing.
            continue
        if role not in _OVERRIDE_ROLES:
            logger.warning(
                "Unknown attribute role %r on flow %r — ignored. "
                "Importer may need updating to support this role.",
                role,
                name,
            )
            continue
        if value is None:
            continue
        out[(name, role)] = value
    return out


def _parse_tempo_law_value(
    value: Any, *, flow_name: str, comp_name: str, role: str
) -> Optional[dict]:
    """Parse a ``tempo_activation`` / ``tempo_deactivation`` override value.

    Returns the occurrence-law dict to hand to FlowOutTempo (SHORT wire form,
    normalised downstream by cod3s' ``sanitize_occ_law``), or ``None`` for the
    ``{"cls": "none"}`` sentinel meaning "force classic (no law on this side)".
    Rejects a malformed value (not a law-shaped dict) so a corrupted snapshot
    surfaces rather than silently degrading the tempo behaviour.
    """
    if isinstance(value, dict):
        if value.get("cls") == "none":
            return None
        if value.get("cls"):
            return value
    raise Cod3sPlatformImportError(
        f"Component {comp_name!r}: instance override role={role} on flow "
        f"{flow_name!r} expects an occurrence-law dict "
        f"({{'cls': 'delay'|'exp'|'inst', ...}}) or {{'cls': 'none'}}, got {value!r}"
    )


def _derive_output_flow_type(flow: FlowSpec) -> str:
    """Derive the effective ``flow_type`` of an output flow from its tempo laws.

    A flow bearing an enable OR disable law is ``tempo`` (FlowOutTempo); with
    neither it is ``classic`` (FlowOut). ``on_trigger`` is preserved verbatim —
    it is a distinct flow class, not derived from occ laws. This is what makes a
    per-instance tempo override flip a classic flow to tempo (and back) without
    touching the KB.
    """
    if flow.flow_type == "on_trigger":
        return "on_trigger"
    if flow.occ_enable is not None or flow.occ_disable is not None:
        return "tempo"
    return "classic"


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
                    comp_name,
                    name,
                )
                continue
            other = out[opposite_idx]
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}: instance override role={role} "
                f"expects a {target_direction} flow but {name!r} is "
                f"{other.direction} (snapshot corruption)"
            )
        flow = out[idx]
        if flow.flow_family == CONTINUOUS_FAMILY:
            # Every role in _ROLE_TO_DIRECTION is a DISCRETE one: they carry
            # booleans, an aggregation logic or a tempo law, and none of them
            # has a continuous counterpart to be applied to. Applied anyway,
            # role=var_in_default would coerce its bool to a plausible rate of
            # 1.0 and the others would be dropped by the continuous kwargs
            # builders without a word. Refused instead, by name -- the platform
            # gains its continuous override roles in the unit that emits them.
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}: instance override role={role} is a "
                f"discrete-family override and flow {name!r} is continuous. "
                "No continuous instance override is carried yet; declare the "
                "value on the KB interface."
            )
        if role == "logic_in":
            new_logic = _parse_input_logic_value(
                value, flow_name=name, comp_name=comp_name
            )
            out[idx] = replace(flow, logic=new_logic)
        elif role == "var_in_default":
            out[idx] = replace(
                flow,
                var_in_default=_parse_init_value(
                    value, flow_name=name, comp_name=comp_name
                ),
            )
        elif role == "active_init":
            out[idx] = replace(
                flow,
                is_active_default=_parse_init_value(
                    value, flow_name=name, comp_name=comp_name
                ),
            )
        elif role == "fed_available_init":
            out[idx] = replace(
                flow,
                fed_available_init=_parse_init_value(
                    value, flow_name=name, comp_name=comp_name
                ),
            )
        elif role == "fed_available_reset":
            out[idx] = replace(
                flow,
                fed_available_reset=_parse_init_value(
                    value, flow_name=name, comp_name=comp_name
                ),
            )
        elif role in ("tempo_activation", "tempo_deactivation"):
            # Set the enable/disable occurrence law, then re-derive flow_type so
            # adding a law promotes a classic flow to tempo and the sentinel
            # {"cls": "none"} demotes it back. Both sides may be overridden
            # independently (two entries) — each reads the current out[idx].
            law = _parse_tempo_law_value(
                value, flow_name=name, comp_name=comp_name, role=role
            )
            if role == "tempo_activation":
                updated = replace(flow, occ_enable=law)
            else:
                updated = replace(flow, occ_disable=law)
            out[idx] = replace(updated, flow_type=_derive_output_flow_type(updated))
        else:  # role == "prod_init"
            out[idx] = replace(
                flow,
                init_value=_parse_init_value(
                    value, flow_name=name, comp_name=comp_name
                ),
            )
    return out


def _build_capacity_overrides_index(
    attributes: List[Dict[str, Any]],
) -> "OverridesIndex":
    """Index a model component's CAPACITY overrides by ``(capacity name, role)``.

    Same ``attributes`` list as the flow overrides, read under the roles of
    :data:`_CAPACITY_OVERRIDE_ROLES`. A ``value`` of ``None`` means "use the
    template's", exactly as it does on the flow side.
    """
    out: OverridesIndex = {}
    for attr in attributes or []:
        if not isinstance(attr, dict):
            continue
        name = attr.get("name")
        role = attr.get("role")
        if not name or role not in _CAPACITY_OVERRIDE_ROLES:
            continue
        if attr.get("value") is None:
            continue
        out[(name, role)] = attr["value"]
    return out


def _apply_capacity_overrides(
    capacities: Tuple[CapacitySpec, ...],
    overrides: "OverridesIndex",
    *,
    comp_name: str,
) -> Tuple[CapacitySpec, ...]:
    """Return the capacities with this instance's overrides folded in.

    A stale override -- one naming a capacity the class no longer declares --
    is REFUSED rather than dropped, unlike a stale flow override. The asymmetry
    is deliberate: a capacity name is one of a handful on a component and does
    not churn, and a volume silently reverting to the template's builds a
    different plant with nothing anywhere saying which one ran.
    """
    if not overrides:
        return capacities

    by_name = {capacity.name: index for index, capacity in enumerate(capacities)}
    out = list(capacities)

    for (name, role), value in overrides.items():
        if role == CAPACITY_FILL_RATE_ROLE:
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}: capacity {name!r} carries a "
                f"role={role} override. The fill rate is frozen at the class "
                "template: it states HOW the volume buffers, which is a "
                "property of the class and not of one instance. Declare it in "
                "the template's 'capacities' section."
            )

        index = by_name.get(name)
        if index is None:
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}: instance override role={role} names "
                f"capacity {name!r}, which its class does not declare "
                f"(declared: {sorted(by_name)})"
            )

        capacity = out[index]
        where = f"Component {comp_name!r}, capacity {name!r}: {role}"

        if role == CAPACITY_VOLUME_ROLE:
            volume = _coerce_number(value, where=where)
            if volume <= 0:
                raise Cod3sPlatformImportError(
                    f"{where}: volume must be strictly positive, got {volume}"
                )
            out[index] = replace(capacity, volume=volume)
            continue

        # CAPACITY_CONTENT_INIT_ROLE
        if len(capacity.flows) != 1:
            held = sorted(held.name for held in capacity.flows)
            raise Cod3sPlatformImportError(
                f"{where}: the initial content is overridable on a SINGLE-flow "
                f"capacity only, and {name!r} holds {len(capacity.flows)} "
                f"({', '.join(held)}). A content is indexed by held flow, so a "
                "scalar override would not say which constituent it fills; "
                "declare the content in the class template instead."
            )
        content = _coerce_number(value, where=where)
        if content < 0:
            raise Cod3sPlatformImportError(
                f"{where}: an initial content is positive or zero, got {content}"
            )
        out[index] = replace(capacity, content_init={capacity.flows[0].name: content})

    return tuple(out)


def _parse_deratings(
    raw: Any, *, comp_name: str, flows: List[FlowSpec]
) -> Tuple[DeratingSpec, ...]:
    """Translate a model component's ``deratings`` section.

    Each entry is a ``(mode, continuous output)`` pair whose derating variable
    the apply layer allocates through the public ``ObjFlow.add_derating``,
    AFTER ``set_flows()``. The pair exists because a mode may be declared
    outside the component -- a standalone ``cod3s.ObjFM*`` naming variables by
    their exact basename -- and can only target a variable that already exists.

    A target that is not a continuous OUTPUT is refused naming it: only a
    continuous output carries a rate, so a derating on anything else is a
    declaration that could never take effect.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise Cod3sPlatformImportError(
            f"Component {comp_name!r}: 'deratings' is a list of "
            f"{{'mode', 'flow'}} mappings, got {type(raw).__name__}"
        )

    continuous_outputs = {
        flow.name
        for flow in flows
        if flow.direction == "output" and flow.flow_family == CONTINUOUS_FAMILY
    }
    known = {flow.name for flow in flows}

    out: List[DeratingSpec] = []
    seen: set = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}: every 'deratings' entry is a "
                f"{{'mode', 'flow'}} mapping, got {type(entry).__name__}"
            )
        unknown = sorted(set(entry) - {"mode", "flow"})
        if unknown:
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}: 'deratings' entry carries unknown "
                f"key(s) {', '.join(repr(key) for key in unknown)}; it accepts "
                "'mode' and 'flow'"
            )

        mode = entry.get("mode")
        flow_name = entry.get("flow")
        if not mode:
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}: a 'deratings' entry carries no "
                f"'mode', the name of the failure mode owning the variable: "
                f"{entry!r}"
            )
        if not flow_name:
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}: 'deratings' entry for mode "
                f"{mode!r} carries no 'flow'"
            )

        if flow_name not in continuous_outputs:
            if flow_name not in known:
                detail = "which this component does not declare"
            elif flow_name in {f.name for f in flows if f.direction == "output"}:
                detail = (
                    "a DISCRETE output. Only a continuous output carries a "
                    "rate; a discrete one is gated by its availability instead"
                )
            else:
                detail = (
                    "an input. Only a continuous OUTPUT carries a rate, and a "
                    "derating multiplies what the rules produced"
                )
            raise Cod3sPlatformImportError(
                f"Component {comp_name!r}: mode {mode!r} derates flow "
                f"{flow_name!r}, {detail} (continuous outputs: "
                f"{sorted(continuous_outputs)})"
            )

        pair = (mode, flow_name)
        if pair in seen:
            # register_derating is idempotent; collapsing here keeps the spec a
            # faithful account of the variables that will exist.
            continue
        seen.add(pair)
        out.append(DeratingSpec(mode=mode, flow=flow_name))

    return tuple(out)


def _parse_components(
    components_raw: Dict[str, Dict[str, Any]],
    kb_lookup: Dict[str, List[FlowSpec]],
    gate_kinds: Optional[Dict[str, str]] = None,
    capacities_lookup: Optional[Dict[str, Tuple[CapacitySpec, ...]]] = None,
    rule_sets_lookup: Optional[Dict[str, Tuple[RuleSetSpec, ...]]] = None,
    controller_specs: Optional[Dict[str, ControllerSpec]] = None,
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

    ``controller_specs`` (R46) maps controller class names to what their
    template declares. A component of such a class carries NO flow -- it
    transports nothing -- so it takes none of the flow machinery above: no
    instance override, no capacity, no rule set, no derating.
    """
    gate_kinds = gate_kinds or {}
    capacities_lookup = capacities_lookup or {}
    rule_sets_lookup = rule_sets_lookup or {}
    controller_specs = controller_specs or {}
    seen_names: set[str] = set()
    out: List[ComponentSpec] = []
    for cid, comp in (components_raw or {}).items():
        name = comp.get("name")
        class_name = comp.get("class_name")
        if not name:
            raise Cod3sPlatformImportError(f"Component {cid!r} missing 'name' field")
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

        controller = controller_specs.get(class_name)
        if controller is not None:
            if comp.get("deratings"):
                raise Cod3sPlatformImportError(
                    f"Component {name!r} is a controller and declares "
                    "deratings. A controller carries no continuous output, so "
                    "it has no rate to derate; what a failure mode reaches on "
                    "one is its output endpoints, named exactly."
                )
            out.append(
                ComponentSpec(
                    id=cid,
                    name=name,
                    class_name=class_name,
                    flows=[],
                    metadata={"platform_id": cid, "attributes_initial": instance_attrs},
                    controller=controller,
                )
            )
            continue

        gate_kind = gate_kinds.get(class_name)
        if gate_kind is not None:
            if comp.get("deratings"):
                raise Cod3sPlatformImportError(
                    f"Component {name!r} is a logic gate and declares "
                    "deratings. A gate exports one boolean and carries no "
                    "continuous output, so it has no rate to derate."
                )
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
                    gate_k=(
                        _read_gate_k(instance_attrs, comp_name=name)
                        if gate_kind == "k"
                        else None
                    ),
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
        # Capacities are resolved PER COMPONENT and not once per class,
        # because their numbers are what an instance overrides: two tanks of
        # one class routinely hold different volumes.
        capacity_overrides = _build_capacity_overrides_index(instance_attrs)
        capacities = _apply_capacity_overrides(
            capacities_lookup.get(class_name) or (),
            capacity_overrides,
            comp_name=name,
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
                    "capacity_overrides": dict(capacity_overrides),
                },
                capacities=capacities,
                rule_sets=rule_sets_lookup.get(class_name) or (),
                deratings=_parse_deratings(
                    comp.get("deratings"), comp_name=name, flows=flows
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Information edges (R46) -- the branch neither flow validation applies to
# ---------------------------------------------------------------------------
#
# An edge touching a controller carries a READING or a SIGNAL, never a
# quantity. Its two endpoint names are therefore validated against DIFFERENT
# vocabularies, and neither of them is the flow vocabulary the regular branch
# below checks: on the publishing end a capacity name, a continuous output's
# name or a controller output's name; on the observing end a controller's
# observation input, or -- for a boolean signal alone -- a discrete input flow.
#
# One rule spans them all and is checked last, because a name may be legitimate
# on each end and still not line up: PyCATSHOO matches an import to an export by
# ALIAS, and every alias here is derived from the interface name, so the two
# ends of an information edge must bear the SAME name. Left to the engine, the
# mismatch fails the whole ``connect`` on a missing alias, which names neither
# of the two declarations that disagreed.


def _control_in_box(entry: ControlInSpec) -> str:
    """The box an observation input imports on, per its nature (R38)."""
    if entry.kind == CONTROL_IN_RATE:
        return f"{entry.name}_rate_in"
    return f"{entry.name}_level_in"


def _observation_input(
    conn_id: str, *, tgt: ComponentSpec, tgt_iface: str
) -> ControlInSpec:
    """The observation input an edge lands on, or a refusal naming what exists."""
    controller = tgt.controller
    if controller is None:
        raise Cod3sPlatformImportError(
            f"Connection {conn_id!r}: {tgt.name!r} is not a controller, so it "
            f"declares no observation input {tgt_iface!r}. A published reading "
            "is read by a controller; a flow carries a quantity and is wired "
            "as a flow."
        )

    entry = controller.input_named(tgt_iface)
    if entry is None:
        raise Cod3sPlatformImportError(
            f"Connection {conn_id!r}: {tgt_iface!r} is not an observation "
            f"input of controller {tgt.name!r} (inputs: "
            f"{controller.input_names}). A controller declares no flow at all, "
            "so an edge onto it lands on one of its observation inputs."
        )

    return entry


def _signal_target_box(
    conn_id: str,
    *,
    src: ComponentSpec,
    src_iface: str,
    tgt: ComponentSpec,
    tgt_iface: str,
) -> str:
    """The box a BOOLEAN signal is imported on: a discrete input flow's own."""
    if tgt.controller is not None:
        raise Cod3sPlatformImportError(
            f"Connection {conn_id!r}: {src.name}.{src_iface} is a boolean "
            f"output and {tgt.name!r} is a controller, whose observation "
            "inputs read numbers. A boolean signal is imported by a discrete "
            "input flow; to feed another controller, declare the output with "
            f"kind={CONTROL_OUT_VALUE!r}."
        )

    inputs = {
        flow.name
        for flow in tgt.flows
        if flow.direction == "input" and flow.flow_family == DISCRETE_FAMILY
    }
    if tgt_iface not in inputs:
        raise Cod3sPlatformImportError(
            f"Connection {conn_id!r}: {tgt_iface!r} is not a discrete input "
            f"flow of {tgt.name!r} (discrete inputs: {sorted(inputs)}). A "
            "controller's boolean signal is imported by a discrete input "
            "flow: a continuous port carries a quantity, which a signal is not."
        )

    return f"{tgt_iface}_in"


def _observed_publisher_box(
    conn_id: str,
    *,
    src: ComponentSpec,
    src_iface: str,
    observed: ControlInSpec,
) -> str:
    """The box a transporter publishes an observed quantity on.

    Which of the two it is follows from what the OBSERVER declared it reads, and
    from nothing on the publishing side: the very same continuous output is a
    transport port on ``{f}_out`` and a read-only rate export on
    ``{f}_rate_out``, and only the observer's nature tells the two apart.
    """
    if observed.kind == CONTROL_IN_RATE:
        rates = {
            flow.name
            for flow in src.flows
            if flow.direction == "output" and flow.flow_family == CONTINUOUS_FAMILY
        }
        if src_iface not in rates:
            raise Cod3sPlatformImportError(
                f"Connection {conn_id!r}: observation input {observed.name!r} "
                f"reads a rate, and {src_iface!r} is not a continuous output "
                f"flow of {src.name!r} (continuous outputs: {sorted(rates)}). "
                "A delivered rate is published on '{flow}_rate_out' by a "
                "continuous output, and by nothing else."
            )
        return f"{src_iface}_rate_out"

    holders = {capacity.name for capacity in src.capacities}
    if src_iface not in holders:
        raise Cod3sPlatformImportError(
            f"Connection {conn_id!r}: observation input {observed.name!r} "
            f"reads a level, and {src_iface!r} is not a capacity of "
            f"{src.name!r} (capacities: {sorted(holders)}). A level is "
            "published on '{name}_level_out' by a capacity, or by another "
            "controller's value output."
        )
    return f"{src_iface}_level_out"


def _parse_information_connection(
    conn_id: str,
    *,
    src: ComponentSpec,
    tgt: ComponentSpec,
    src_iface: str,
    tgt_iface: str,
) -> ConnectionSpec:
    """One edge touching a controller, resolved to the two boxes it wires."""
    src_ctrl = src.controller

    if src_ctrl is not None:
        published = src_ctrl.output_named(src_iface)
        if published is None:
            raise Cod3sPlatformImportError(
                f"Connection {conn_id!r}: {src_iface!r} is not an output of "
                f"controller {src.name!r} (outputs: {src_ctrl.output_names}). "
                "A controller declares no flow at all, so an edge leaving it "
                "leaves one of its outputs."
            )

        if published.kind == CONTROL_OUT_BOOL:
            source_box = f"{src_iface}_out"
            target_box = _signal_target_box(
                conn_id, src=src, src_iface=src_iface, tgt=tgt, tgt_iface=tgt_iface
            )
        else:
            observed = _observation_input(conn_id, tgt=tgt, tgt_iface=tgt_iface)
            if observed.kind != CONTROL_IN_LEVEL:
                raise Cod3sPlatformImportError(
                    f"Connection {conn_id!r}: {src.name}.{src_iface} publishes "
                    f"a value on '{src_iface}_level_out', and observation "
                    f"input {tgt.name}.{tgt_iface} reads a {observed.kind}. "
                    f"Declare it kind={CONTROL_IN_LEVEL!r}: a rate is "
                    "published by a continuous output, never by a controller."
                )
            source_box = f"{src_iface}_level_out"
            target_box = _control_in_box(observed)
    else:
        observed = _observation_input(conn_id, tgt=tgt, tgt_iface=tgt_iface)
        target_box = _control_in_box(observed)
        source_box = _observed_publisher_box(
            conn_id, src=src, src_iface=src_iface, observed=observed
        )

    if src_iface != tgt_iface:
        raise Cod3sPlatformImportError(
            f"Connection {conn_id!r}: an information edge is matched by alias, "
            "and both aliases are derived from the interface name, so the two "
            f"ends must bear the same one -- got {src.name}.{src_iface!r} and "
            f"{tgt.name}.{tgt_iface!r}. Rename one of the two declarations."
        )

    return ConnectionSpec(
        source_component=src.name,
        target_component=tgt.name,
        flow_name=src_iface,
        source_interface=src_iface,
        target_interface=tgt_iface,
        source_box=source_box,
        target_box=target_box,
    )


def _check_observation_fan_in(
    connections: List[ConnectionSpec], components: List[ComponentSpec]
) -> None:
    """Refuse a second publisher on an input that states no reduction (R40).

    muscadet caps a single-source channel at one connection, so the engine
    already refuses this -- at the SECOND ``connect``, naming a connection
    limit. Caught here, the message names the input and the closed list of
    policies, which is what the modeller has to choose from.
    """
    by_name = {comp.name: comp for comp in components}
    seen: set = set()

    for conn in connections:
        if conn.target_box is None:
            continue

        target = by_name.get(conn.target_component)
        controller = target.controller if target is not None else None
        if controller is None:
            continue

        entry = controller.input_named(conn.target_interface)
        if entry is None or entry.aggregate is not None:
            continue

        key = (conn.target_component, conn.target_interface)
        if key in seen:
            raise Cod3sPlatformImportError(
                f"Controller {conn.target_component!r}: observation input "
                f"{conn.target_interface!r} is wired to several publishers but "
                "declares no 'aggregate'. State how the readings reduce "
                f"({sorted(_VALID_CONTROL_AGGREGATIONS)}), or wire one "
                "publisher: a silent sum over redundant sources is a wrong "
                "model, not a default."
            )
        seen.add(key)


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
        if src.controller is not None or tgt.controller is not None:
            # R46 -- an INFORMATION edge. Neither endpoint name need be a
            # declared flow, so neither of the two validations below applies;
            # each end is validated against its OWN set of ports instead.
            if not isinstance(src_iface, str) or not isinstance(tgt_iface, str):
                raise Cod3sPlatformImportError(
                    f"Connection {conn_id!r}: an interface name is a string, "
                    f"got {src_iface!r} and {tgt_iface!r}"
                )
            out.append(
                _parse_information_connection(
                    conn_id,
                    src=src,
                    tgt=tgt,
                    src_iface=src_iface,
                    tgt_iface=tgt_iface,
                )
            )
            continue
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
                conn_id,
                src_iface,
                tgt_iface,
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
    _check_observation_fan_in(out, components)
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
            interface, duplicate component name, unsupported
            ``export_version`` major, or a malformed controller declaration
            (R46) -- a section without its marker, a controller carrying a
            transport section, an information edge whose two ends do not line
            up, or several publishers on an input stating no reduction.
    """
    _check_export_version(payload)
    kb = _resolve_kb(payload)
    model = payload.get("model") or {}
    elements = model.get("elements") or {}

    kb_lookup = _build_kb_lookup(kb)
    gate_kinds = _build_gate_kinds(kb)
    controller_specs = _build_controller_specs(kb)
    # Capacities before rule sets, and both after the flows: the same
    # dependency muscadet's own ``DECLARATION_SECTIONS`` encodes, for the same
    # reason -- a capacity names flows, and a rule refuses a capacity name.
    capacities_lookup = _build_kb_capacities(kb, kb_lookup)
    rule_sets_lookup = _build_kb_rule_sets(kb, kb_lookup, capacities_lookup)
    components = _parse_components(
        elements.get("components") or {},
        kb_lookup,
        gate_kinds,
        capacities_lookup=capacities_lookup,
        rule_sets_lookup=rule_sets_lookup,
        controller_specs=controller_specs,
    )
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

    def _ref_name_port(ref):
        # A prod_cond operand is a flow name string or a
        # ``{"name", "negate"?, "port"?}`` mapping. Dependency ordering cares
        # about the referenced name and, when present, the ``port`` hint
        # ("in"/"out") that disambiguates an input vs an output of the same name.
        if isinstance(ref, dict):
            return ref.get("name"), ref.get("port")
        return ref, None

    input_set = set(input_names)
    by_name = {f.name: f for f in output_flows}
    remaining = dict(by_name)
    ordered: List[FlowSpec] = []
    created_out: set = set()

    def _satisfied(ref):
        name, port = _ref_name_port(ref)
        if port == "in":
            return name in input_set
        if port == "out":
            # An explicit output reference depends on that output being created.
            return name in created_out
        # Historical input-first resolution: an input satisfies immediately,
        # otherwise the (same-name) output must already be created.
        return name in input_set or name in created_out

    while remaining:
        # Pick every flow whose deps are all already available.
        ready = [
            f
            for f in remaining.values()
            if all(
                _satisfied(ref)
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
            created_out.add(f.name)
            del remaining[f.name]
    return ordered


# ---------------------------------------------------------------------------
# Logic gate synthesis (F-SYS-10) — apply layer
# ---------------------------------------------------------------------------


def _gate_leaf_attr(
    source_interface: str, *, source_is_gate: bool, check_fed: bool
) -> str:
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
    return (
        f"{source_interface}_fed_out"
        if check_fed
        else f"{source_interface}_fed_available_out"
    )


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
            if conn.target_component == gate.name
            and conn.source_component in gate_names
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


def _create_logic_gate(
    gate: ComponentSpec, system: Any, connections: List[ConnectionSpec], gate_names: set
) -> None:
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
    if (
        comp is not None
        and hasattr(comp, "metadata")
        and isinstance(comp.metadata, dict)
    ):
        comp.metadata.update(
            {
                "class_name": gate.class_name,
                "platform_id": gate.metadata.get("platform_id"),
                "logic_gate": gate.gate_kind,
            }
        )


# ---------------------------------------------------------------------------
# Controller synthesis (R46) -- apply layer
# ---------------------------------------------------------------------------
#
# A controller is built by ``system.add_component(cls="ObjCtrl", ...)`` and NOT
# by ``muscadet.build_component``. That function owns the ``ObjFlow``
# construction lifecycle -- ``partial_init``, ``add_flows``, then one
# ``set_flows()`` -- and ``ObjCtrl`` is a peer of ``ObjFlow``, not a subclass of
# it: it has none of those three methods. ``ObjLogicGate`` stands outside the
# same function for the same reason, and putting a class test inside the one
# function whose job IS that lifecycle would buy nothing here, since a
# controller's own constructor declares both its sections itself.
#
# Which is also why the declaration ORDER is not forked: ``ObjCtrl.__init__``
# walks ``controls_in`` and then ``controls_out``, which is the order
# ``muscadet.declare.DECLARATION_SECTIONS`` records, and it is the library's own
# implementation of it. Handing both sections to the constructor keeps a second
# property worth having: the output grammar is refused BEFORE the engine object
# exists, so a malformed one leaves no half-built component behind.


def _control_in_kwargs(entry: ControlInSpec) -> Dict[str, Any]:
    """Declaration kwargs of one observation input, as ``ObjCtrl`` takes them.

    ``aggregate`` is the interface's own word; the measurement channel
    underneath says ``combine``, and ``ObjCtrl.add_control_in`` is the one place
    that translates. Passing ``combine`` here would reach the channel's second
    key, ``combine_fun``, and with it the Python callable the closed list of
    policies exists to refuse.
    """
    kwargs: Dict[str, Any] = {"name": entry.name, "kind": entry.kind}
    if entry.aggregate is not None:
        kwargs["aggregate"] = entry.aggregate
    if entry.flows:
        kwargs["flows"] = list(entry.flows)
    for key in ("level_default", "fill_default", "rate_default"):
        value = getattr(entry, key)
        if value is not None:
            kwargs[key] = value
    return kwargs


def _control_out_kwargs(entry: ControlOutSpec) -> Dict[str, Any]:
    """Declaration kwargs of one output, as ``ObjCtrl`` takes them."""
    kwargs: Dict[str, Any] = {"name": entry.name, "kind": entry.kind}
    if entry.emit is not None:
        # Copied: a spec is data the caller keeps and may build twice, and
        # ``build_ctrl_node`` reads the mapping it is handed.
        kwargs["emit"] = copy.deepcopy(entry.emit)

    if entry.kind == CONTROL_OUT_BOOL:
        if entry.default is not None:
            kwargs["default"] = entry.default
        return kwargs

    if entry.flows:
        kwargs["flows"] = list(entry.flows)
    for key in ("level_default", "fill_default", "gain_default"):
        value = getattr(entry, key)
        if value is not None:
            kwargs[key] = value
    return kwargs


def _create_controller(
    spec: ComponentSpec, controller: ControllerSpec, system: Any
) -> None:
    """Instantiate one ``ObjCtrl`` on ``system`` from its parse spec."""
    kwargs: Dict[str, Any] = {
        "cls": "ObjCtrl",
        "name": spec.name,
        "controls_in": [_control_in_kwargs(entry) for entry in controller.controls_in],
        "controls_out": [
            _control_out_kwargs(entry) for entry in controller.controls_out
        ],
    }
    try:
        comp = system.add_component(**kwargs)
    except Exception as e:
        raise Cod3sPlatformImportError(
            f"Failed to create controller {spec.name!r}: {e}"
        ) from e

    if (
        comp is not None
        and hasattr(comp, "metadata")
        and isinstance(comp.metadata, dict)
    ):
        comp.metadata.update(
            {
                "class_name": spec.class_name,
                "platform_id": spec.metadata.get("platform_id"),
                "attributes_initial": spec.metadata.get("attributes_initial", []),
                CONTROLLER_MARKER: True,
            }
        )


def _continuous_in_kwargs(flow: FlowSpec) -> Dict[str, Any]:
    """Declaration kwargs of a continuous INPUT flow.

    A dictionary of its own, sharing not one line with the discrete builder.
    That separation is what guarantees no production condition, no inner mode
    and no negation can reach a continuous flow: there is no code path putting
    them here. ``FlowContinuous.check_declaration_keys`` would refuse them, but
    a refusal is a last line of defence, not a design.
    """
    kwargs: Dict[str, Any] = {
        "cls": "FlowContinuousIn",
        "name": flow.name,
    }
    # Only pass what was declared; None leaves the muscadet default (0.0 for
    # both), which is what a port fed by a recipe or a capacity wants.
    if flow.var_in_default is not None:
        kwargs["var_in_default"] = float(flow.var_in_default)
    if flow.nominal_rate is not None:
        kwargs["var_demand_default"] = flow.nominal_rate

    return kwargs


def _continuous_out_kwargs(flow: FlowSpec) -> Dict[str, Any]:
    """Declaration kwargs of a continuous OUTPUT flow.

    The profile arrives already decomposed by the parse layer: ``nominal_rate``
    is the declared rate and ``profile_spec`` the modulation, absent for a
    constant shape. So ``profile`` is written here only when there is a factor
    to build, and a constant shape never reaches ``muscadet.build_profile``.
    """
    kwargs: Dict[str, Any] = {
        "cls": "FlowContinuousOut",
        "name": flow.name,
        "allocation": flow.allocation or _ALLOCATION_PROPORTIONAL,
    }
    if flow.nominal_rate is not None:
        kwargs["var_fed_default"] = flow.nominal_rate
    if flow.profile_spec is not None:
        kwargs["profile"] = dict(flow.profile_spec)

    return kwargs


#: Section of :data:`muscadet.declare.DECLARATION_SECTIONS` the importer builds
#: itself rather than by dispatching entries: the output flows must be created
#: in intra-component DEPENDENCY order (a discrete output's production condition
#: may reference another output of the same component), which is an ordering
#: this bridge computes and the generic dispatch knows nothing about.
_FLOWS_SECTION = "flows"


def _capacity_kwargs(capacity: CapacitySpec) -> Dict[str, Any]:
    """Declaration kwargs of one capacity, as ``ObjFlow.add_capacity`` takes them.

    ``volume`` becomes ``capacity``: the platform names the quantity, muscadet
    names the thing. Only what was declared is passed -- ``side`` left out lets
    muscadet resolve it from the sides the held flows are carried on, and
    ``fill_rate`` left out keeps the pure-buffer default.
    """
    kwargs: Dict[str, Any] = {
        "name": capacity.name,
        "flows": [
            {"name": held.name, "weight": held.weight} for held in capacity.flows
        ],
        "capacity": capacity.volume,
    }
    if capacity.side is not None:
        kwargs["side"] = capacity.side
    if capacity.content_init:
        kwargs["content_init"] = dict(capacity.content_init)
    if capacity.fill_rate is not None:
        kwargs["fill_rate"] = capacity.fill_rate
    return kwargs


def _rule_operand_kwargs(operand: RuleOperandSpec) -> Dict[str, Any]:
    """One guard operand, in muscadet's canonical mapping form."""
    kwargs: Dict[str, Any] = {"name": operand.name}
    if operand.negate:
        kwargs["negate"] = True
    if operand.port is not None:
        kwargs["port"] = operand.port
    if operand.op is not None:
        kwargs["op"] = operand.op
        kwargs["value"] = operand.value
    return kwargs


def _rule_set_kwargs(rule_set: RuleSetSpec) -> Dict[str, Any]:
    """Declaration kwargs of one rule set, as ``ObjFlow.add_rules`` takes them.

    ``cond`` is emitted only when the rule carries one: an EMPTY guard is what
    makes a rule the default rule of its set, and muscadet reads that from the
    absence rather than from an empty list being present or not.
    """
    rules: List[Dict[str, Any]] = []
    for rule in rule_set.rules:
        entry: Dict[str, Any] = {}
        if rule.name is not None:
            entry["name"] = rule.name
        if rule.cond:
            entry["cond"] = [_rule_operand_kwargs(op) for op in rule.cond]
        if rule.cons:
            entry["cons"] = dict(rule.cons)
        if rule.prod:
            entry["prod"] = dict(rule.prod)
        rules.append(entry)

    return {"name": rule_set.name, "rules": rules}


def _declaration_entries(spec: ComponentSpec) -> Dict[str, List[Dict[str, Any]]]:
    """The declaration sections this bridge emits, keyed as muscadet names them.

    The keys are those of :data:`muscadet.declare.DECLARATION_SECTIONS`, which
    is what lets :func:`_declare_component` walk that tuple instead of carrying
    an order of its own. The sections not emitted here (``measurements_in``,
    ``measurements_out``, ``transfers``) simply have no entries, and gain one
    the day the platform exports them.
    """
    return {
        "capacities": [_capacity_kwargs(capacity) for capacity in spec.capacities],
        "rules": [_rule_set_kwargs(rule_set) for rule_set in spec.rule_sets],
    }


def _declare_component_flows(comp: Any, spec: ComponentSpec) -> None:
    """Declare every flow of one component: inputs first, outputs in dep order."""
    input_names = set()
    for flow in spec.flows:
        if flow.direction != "input":
            continue
        if flow.flow_family == CONTINUOUS_FAMILY:
            flow_in_kwargs = _continuous_in_kwargs(flow)
        else:
            flow_in_kwargs = {
                "cls": "FlowIn",
                "name": flow.name,
                "logic": flow.logic,
            }
            # Only pass var_in_default when explicitly set
            # (role=var_in_default instance override) ; None leaves the
            # muscadet FlowIn default (False).
            if flow.var_in_default is not None:
                flow_in_kwargs["var_in_default"] = flow.var_in_default
        try:
            comp.add_flow(flow_in_kwargs)
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
        if flow.flow_family == CONTINUOUS_FAMILY:
            try:
                comp.add_flow(_continuous_out_kwargs(flow))
            except Exception as e:
                raise Cod3sPlatformImportError(
                    f"Failed to add continuous output flow {flow.name!r} "
                    f"to component {spec.name!r}: {e}"
                ) from e
            continue
        # Dynamic flow-out dispatch (2026-07-10). All three classes derive
        # from FlowOut, so var_prod_cond / inner_mode / negate apply to each.
        flow_type = flow.flow_type or "classic"
        cls_name = {
            "tempo": "FlowOutTempo",
            "on_trigger": "FlowOutOnTrigger",
        }.get(flow_type, "FlowOut")
        flow_kwargs: Dict[str, Any] = {
            "cls": cls_name,
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
        # Service-function dormancy: var_is_active_default=False makes the
        # flow stay unfed (orthogonally to prod_cond) until an effect sets
        # var_is_active True. Only passed when explicitly overridden so
        # normal flows keep the muscadet default (True = always active).
        if flow.is_active_default is not None:
            flow_kwargs["var_is_active_default"] = flow.is_active_default
        # Service-function dormancy (user-facing): start the availability
        # gate closed so the flow is dormant until an effect re-opens it.
        if flow.fed_available_init is not None:
            flow_kwargs["var_fed_available_out_init"] = flow.fed_available_init
        # Availability-gate reset control: when False, keep the gate's last
        # value within a sequence instead of reinitialising it each step. Only
        # passed when explicitly overridden so normal flows keep the muscadet
        # default (True = legacy reinitialised gate, byte-identical).
        if flow.fed_available_reset is not None:
            flow_kwargs["var_fed_available_out_reset"] = flow.fed_available_reset
        # Tempo params (FlowOutTempo). Occurrence-law dicts pass through in
        # SHORT wire form; cod3s' sanitize_occ_law normalises them.
        if flow_type == "tempo":
            if flow.occ_enable is not None:
                flow_kwargs["occ_enable_flow"] = flow.occ_enable
            if flow.occ_disable is not None:
                flow_kwargs["occ_disable_flow"] = flow.occ_disable
            if flow.init_enable is not None:
                flow_kwargs["init_enable"] = flow.init_enable
        # On-trigger params (FlowOutOnTrigger). Trigger times are plain
        # floats; muscadet wraps them in a delay law internally.
        elif flow_type == "on_trigger":
            if flow.trigger_time_up is not None:
                flow_kwargs["trigger_time_up"] = flow.trigger_time_up
            if flow.trigger_time_down is not None:
                flow_kwargs["trigger_time_down"] = flow.trigger_time_down
            if flow.trigger_logic is not None:
                flow_kwargs["trigger_logic"] = flow.trigger_logic
        try:
            comp.add_flow(flow_kwargs)
        except Exception as e:
            raise Cod3sPlatformImportError(
                f"Failed to add output flow {flow.name!r} to component "
                f"{spec.name!r}: {e}"
            ) from e


def _declare_component(comp: Any, spec: ComponentSpec) -> None:
    """Declare one component's sections, in the order muscadet owns.

    The order is READ from :data:`muscadet.declare.DECLARATION_SECTIONS` rather
    than restated here, because it is the library's and not the bridge's: a
    capacity names flows, a rule refuses a capacity name in a ``cons`` map, and
    a conduit refuses a flow a rule already consumes -- three refusals that are
    only reachable when the thing doing the refusing exists first. Writing the
    order out here would fork it, and a fork would go unnoticed until a section
    added upstream landed in the wrong place.

    ``set_flows()`` is called ONCE, after every section that must precede it: it
    creates the PyCATSHOO variables and message boxes, it cannot be re-run, and
    skipping it leaves ``connect`` failing on another component entirely. The
    derating pre-allocation follows it for the mirror-image reason muscadet
    keeps :data:`~muscadet.declare.POST_SET_FLOWS_SECTIONS` apart: the bounded
    variable a mode clamps does not exist until then.
    """
    from muscadet.declare import DECLARATION_SECTIONS  # noqa: WPS433

    entries_by_section = _declaration_entries(spec)

    for section, method_name in DECLARATION_SECTIONS:
        if section == _FLOWS_SECTION:
            _declare_component_flows(comp, spec)
            continue

        entries = entries_by_section.get(section)
        if not entries:
            continue

        method = getattr(comp, method_name)
        for entry in entries:
            try:
                # Copied: a spec is data the caller keeps, and muscadet
                # resolves declarations in place (a rule operand is bound to
                # the flow object it names).
                method(**copy.deepcopy(entry))
            except Cod3sPlatformImportError:
                raise
            except Exception as e:
                raise Cod3sPlatformImportError(
                    f"Failed to declare {section} entry {entry.get('name')!r} "
                    f"on component {spec.name!r}: {e}"
                ) from e

    # Wire all declared flows to PyCATSHOO (variables, message boxes,
    # sensitive methods, automata) in one shot. Required because
    # ``partial_init=True`` skipped this in ``__init__``.
    comp.set_flows()

    # After set_flows(), and only after: add_derating allocates a variable on
    # the flow object, which the engine must already know about.
    for derating in spec.deratings:
        try:
            comp.add_derating(derating.mode, derating.flow)
        except Exception as e:
            raise Cod3sPlatformImportError(
                f"Failed to allocate the derating variable of mode "
                f"{derating.mode!r} on flow {derating.flow!r} of component "
                f"{spec.name!r}: {e}"
            ) from e


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
       them). Each flow is dispatched on its ``flow_family``: a continuous one
       is built from :func:`_continuous_in_kwargs` /
       :func:`_continuous_out_kwargs`, which share no key with the discrete
       builders below.
    3. Add output flows in **dependency order** — outputs whose
       ``var_prod_cond`` references another output of the same component
       are created after their dependencies (the COD3S Platform KB uses
       this for diagnostic flows that mirror primary outputs).
    4. Declare the component's **capacities** and then its **rule sets**,
       walking :data:`muscadet.declare.DECLARATION_SECTIONS` rather than
       restating its order, call ``set_flows()`` once, and pre-allocate the
       derating variable of each declared ``(mode, continuous output)`` pair
       (cf. :func:`_declare_component`).
    5. Materialise every **controller** as a ``muscadet.ObjCtrl`` (R46), after
       the regular components and the logic gates. It is built by
       ``add_component`` and not by ``muscadet.build_component``, which owns the
       ``ObjFlow`` lifecycle a peer class does not have.
    6. After all components and flows are declared, wire connections. An
       information edge -- one touching a controller -- goes through the raw
       ``system.connect`` on the two boxes the parse layer resolved; everything
       else through ``system.connect_flow``.

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
    normal_specs = [
        c for c in ctx.components if c.gate_kind is None and c.controller is None
    ]

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
                    "attributes_initial": spec.metadata.get("attributes_initial", []),
                    "instance_overrides": dict(
                        spec.metadata.get("instance_overrides") or {}
                    ),
                    "capacity_overrides": dict(
                        spec.metadata.get("capacity_overrides") or {}
                    ),
                }
            )

        # Flows, capacities and rule sets, in the order muscadet owns, then
        # ``set_flows()`` once, then the derating pre-allocation that needs the
        # variables it creates.
        _declare_component(comp, spec)

    # Logic gates after all regular components exist (their ``cond``
    # references regular source variables) and in dependency order
    # (gate→gate chaining).
    for gate in _order_gates(gate_specs, ctx.connections, gate_names):
        _create_logic_gate(gate, system, ctx.connections, gate_names)

    # R46 -- controllers, in declaration order and after everything else. No
    # ordering among themselves is derived here, unlike the gates: an
    # ``ObjLogicGate`` resolves its ``cond`` leaves against other components at
    # CONSTRUCTION, while a controller reads its sources through references
    # resolved at connection time, so nothing it declares can name a component
    # that does not exist yet. The order the controllers RUN in is a separate
    # question, answered by the signal graph at the pre-run step (R45).
    for spec in ctx.components:
        controller = spec.controller
        if controller is None:
            continue
        _create_controller(spec, controller, system)

    # Connections — once all flows exist.
    for conn in ctx.connections:
        # R46 -- an information edge, wired raw on the two boxes the parse layer
        # resolved. First, because neither of its ends need be a flow: falling
        # through to ``connect_flow`` would look for one and find nothing.
        if conn.source_box is not None and conn.target_box is not None:
            try:
                system.connect(
                    conn.source_component,
                    conn.source_box,
                    conn.target_component,
                    conn.target_box,
                )
            except Exception as e:
                raise Cod3sPlatformImportError(
                    f"Failed to connect {conn.source_component}."
                    f"{conn.source_box} --> {conn.target_component}."
                    f"{conn.target_box}: {e}"
                ) from e
            continue
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
