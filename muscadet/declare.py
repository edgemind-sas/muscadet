"""Building a component from a declaration held in DATA, and reading one back.

A muscadet component is normally a subclass of :class:`muscadet.ObjFlow`
overriding ``add_flows``. That subclass is almost never *behaviour*: it declares
flows, rule sets, capacities, measurement channels and transfer pairs, and every
one of those is a declaration a mapping can carry. What the subclass really
provides is a **place to write the declaration** and, less visibly, the right
ORDER to write it in.

This module is that place, for a caller whose declaration arrives as data -- a
COD3S Platform export, a YAML knowledge base, a generated study. It owns the two
directions:

- :func:`build_component` turns a spec into a live component;
- :func:`component_spec` reads a live component back into a spec.

**The order is the whole difficulty, and it is not guessable.** ``set_flows()``
is what creates the PyCATSHOO variables and message boxes, it runs once, and it
cannot be re-run. The three ways of getting it wrong all fail far from their
cause:

=========================================  ==========================================
Mistake                                    What the caller sees
=========================================  ==========================================
``set_flows()`` never called               ``La boîte de messages X_in est
                                           introuvable``, at ``connect`` time, on
                                           another component
``set_flows()`` called twice               ``La variable X_fed_in existe déjà``
Rules declared before their flows          ``KeyError`` on a flow the spec declares
=========================================  ==========================================

:data:`DECLARATION_SECTIONS` is the order, written down once. It is not
alphabetical and not arbitrary: a rule refuses a capacity or a measurement
channel in its ``cons`` map, and a conduit refuses a flow a rule already
consumes, so the thing doing the refusing has to exist first. Declaring
capacities and measurements before the rules, and the rules before the transfer
pairs, is what makes those three refusals reachable instead of dead.

**A shipped class stays usable as a template.** ``cls`` names any component
class; ``params`` is its own declaration (``rate``, ``capacity``, ``activate``
...). The class declares its ports first and the spec's sections are added on
top, so ``SourceContinuous`` plus one discrete output is a spec and not a
subclass.

Examples
--------
>>> comp = build_component(system, {                        # doctest: +SKIP
...     "name": "PUMP",
...     "flows": [
...         {"cls": "FlowContinuousIn", "name": "elec"},
...         {"cls": "FlowContinuousOut", "name": "heat"},
...         {"cls": "FlowIn", "name": "call", "logic": "or"},
...         {"cls": "FlowOut", "name": "healthy", "var_prod_default": True},
...     ],
...     "rules": [{"name": "heat_pump", "rules": [
...         {"cond": ["call"], "cons": {"elec": 2.0}, "prod": {"heat": 7.0}},
...     ]}],
... })
"""

import inspect
import math
import re

import pydantic

from .common import copy_declaration
from .profile import PROFILE_CLASSES, Profile
from .transfer import TRANSFER_CLASSES, Transfer

#: Keys the component CONSTRUCTOR consumes, in the order it takes them.
#: ``partial_init`` is deliberately absent: this module always builds partially
#: and calls ``set_flows()`` itself, which is the point of it existing.
CONSTRUCTOR_KEYS = (
    "name",
    "cls",
    "label",
    "description",
    "metadata",
    "create_default_out_automata",
)

#: The declaration sections, IN THE ORDER THEY MUST BE DECLARED, each with the
#: method that consumes one entry. Every dependency below is one a declaration
#: actually has, and the order is what makes three refusals reachable instead of
#: dead:
#:
#: - ``measurements_in`` first, and NOT after the flows: a discrete output may
#:   compare a level, which is how a sensor thresholds one
#:   (``var_prod_cond=[{"name": channel, "op": ">=", "value": x}]``), and the
#:   channel has to exist for that operand to resolve. The channel itself
#:   depends on nothing -- its own ``flows`` are constituent names of the remote
#:   volume, not flows of this component;
#: - ``controls_in`` after the flows: a controller input observes a quantity,
#:   and one of the two quantities it may observe is the rate a continuous
#:   OUTPUT publishes (R38), so the flow has to exist for the box to;
#: - ``controls_out`` after ``controls_in``, because an output says what it is
#:   made of and names an input to say it;
#: - ``capacities`` name flows, so they follow them;
#: - ``measurements_out`` may take their ``source`` from a capacity or from an
#:   imported channel, so they follow both;
#: - ``rules`` refuse a capacity name and a measurement channel name in a
#:   ``cons`` map, which is only refusable once those exist;
#: - ``transfers`` last: a conduit refuses a flow a rule already consumes.
#:
#: The two controller sections are declared by :class:`muscadet.ObjCtrl`, which
#: is a PEER of ``ObjFlow`` and not a subclass of it (R39), so no single
#: component ever carries both them and the flow sections. What this constant
#: records for them is the ORDER and the two method names, in the one place the
#: order is written down, so that a bridge reading it places a controller's
#: sections without forking the sequence. :func:`build_component` owns the
#: ``ObjFlow`` construction lifecycle and does not build controllers; a spec
#: carrying a controller section on a component that has no builder for it is
#: refused BY NAME below rather than crashing on a missing attribute.
#:
#: Every one of these runs BEFORE ``set_flows()``. The two sections that run
#: after it are handled apart, in :data:`POST_SET_FLOWS_SECTIONS`, because they
#: need the variables their effects clamp to exist.
DECLARATION_SECTIONS = (
    ("measurements_in", "add_measurement_in"),
    ("flows", "add_flow"),
    ("controls_in", "add_control_in"),
    ("controls_out", "add_control_out"),
    ("capacities", "add_capacity"),
    ("measurements_out", "add_measurement_out"),
    ("rules", "add_rules"),
    ("transfers", "add_transfer"),
)

#: The sections declared AFTER ``set_flows()``. An automaton's effects are
#: resolved against the component's variables, which do not exist until then.
POST_SET_FLOWS_SECTIONS = ("automata", "failure_modes")

#: ``cls`` values a ``failure_modes`` entry may carry, and the method each maps
#: to. A failure mode declared on a component is one of exactly two shapes; the
#: standalone ``ObjFailureMode*`` objects are components in their own right and
#: are declared as components, not inside one.
FAILURE_MODE_METHODS = {
    "exp": "add_exp_failure_mode",
    "delay": "add_delay_failure_mode",
}

#: Informational key :func:`component_spec` writes and :func:`build_component`
#: ignores: the class the spec was READ BACK from. A spec is always expanded
#: onto ``ObjFlow``, so the original class name would otherwise be lost, and it
#: is worth keeping -- a template picker wants to show it.
SOURCE_CLS_KEY = "source_cls"

#: Every key a spec may carry.
COMPONENT_KEYS = frozenset(
    CONSTRUCTOR_KEYS
    + ("params", SOURCE_CLS_KEY)
    + tuple(section for section, _ in DECLARATION_SECTIONS)
    + POST_SET_FLOWS_SECTIONS
)

#: Field-name prefixes a RUNTIME HANDLE may carry: the PyCATSHOO variables and
#: references a flow is wired to at ``set_flows()``, and the sensitive methods
#: bound with them. They hold engine objects, they are rebuilt on every
#: construction, and they are dropped from a spec rather than refused. Anything
#: else that will not serialise is refused instead, because it is then a
#: declaration being silently lost.
#:
#: **The prefix alone does not tell the two apart**, and reading it as if it did
#: is the mistake to avoid: ``var_prod_default``, ``var_fed_default``,
#: ``var_type`` and ``var_in_default`` all carry this prefix and are all
#: declarations a spec must keep. What separates them is the VALUE. A handle is
#: an engine object, or ``None`` before ``set_flows()`` has wired it; a
#: declaration is data. :data:`RUNTIME_HANDLE_PREFIXES` is the narrower set that
#: never holds a declaration at all.
RUNTIME_FIELD_PREFIXES = ("var_", "sm_")

#: Prefixes whose fields are ALWAYS plumbing, never a declaration: a sensitive
#: method's name and the bound function itself. The name is recomputed from the
#: flow's own name at every construction (``flow.py``, ``add_mb``), so a spec
#: carrying one says nothing and goes stale the moment a generated model renames
#: a flow -- verified: renaming ``f`` to ``g`` in the data rebuilds
#: ``set_g_fed_out`` and ignores what the spec held.
RUNTIME_HANDLE_PREFIXES = ("sm_",)

#: Fields carrying ``exclude=True`` that are DERIVED, and are therefore dropped
#: from a spec like a runtime handle. Everything else excluded is treated as a
#: declaration and must survive or be refused -- which is the safe default,
#: because ``model_dump`` does not show an excluded field at all and a
#: declaration hidden that way would otherwise vanish without a trace. That is
#: not hypothetical: ``allocation_fun`` and ``combine_fun`` are excluded, and a
#: spec that dropped one would rebuild a component splitting an insufficient
#: supply by a different policy.
#:
#: ``comp_name`` is written at wiring, ``allocated`` / ``derating`` /
#: ``demand_required`` are per-evaluation state, ``automaton`` / ``state_empty``
#: / ``state_full`` are a capacity's built bound automaton, ``mode`` is the
#: automaton a rule set's guards compile into, and ``flow`` is the object a
#: guard operand resolved onto. Every one is rebuilt by the declaration it comes
#: from.
DERIVED_EXCLUDED_FIELDS = frozenset(
    {
        "comp_name",
        "demand_required",
        "allocated",
        "derating",
        "automaton",
        "state_empty",
        "state_full",
        "mode",
        "flow",
    }
)


class ComponentSpecError(ValueError):
    """A component declaration muscadet refuses to build or to read back."""


def _is_serialisable(value):
    """True when ``value`` is made only of JSON-native parts.

    ``float('inf')`` counts: a capacity's ``fill_rate`` is routinely infinite
    and ``json`` writes it, so refusing it here would refuse the default
    spelling of "whatever the producer delivers".
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_serialisable(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_serialisable(item)
            for key, item in value.items()
        )
    return False


def _as_data(value):
    """Normalise a read-back declaration to what a JSON round trip gives back.

    The container walk itself is :func:`~muscadet.common.copy_declaration`,
    written once and shared: the two differed only in what they did with a
    tuple, and a container type added to one would have been silently absent
    from the other.
    """
    return copy_declaration(value, tuples_as_lists=True)


def _checked_declaration(value, where):
    """A declaration read back verbatim, refused if a mapping cannot carry it.

    The other sections are dumped field by field through
    :func:`_declaration_fields`, which classifies each value and refuses what
    will not serialise, naming the field. ``automata`` and ``failure_modes``
    are not dumped: they are what the caller DECLARED, kept verbatim by
    ``ObjFlow.declared_automata`` / ``declared_failure_modes``, so they reached
    the spec without passing that gate.

    Nothing refused them, and the two things they legitimately hold that a
    mapping cannot are the ones this module exists to catch: a Python callable
    as a transition condition, and the PyCATSHOO variable an occurrence law may
    carry as its rate -- ``add_exp_failure_mode`` writes one itself, so an
    indicator can reference the rate by name. A spec carrying either came back
    without complaint and failed later, at ``json.dumps``, far from the
    declaration that caused it and naming only a type.

    Note this refuses on the READ side only. Building FROM such a declaration
    stays supported: ``copy_declaration`` shares leaves precisely so a law
    holding an engine handle can be built from, and a caller holding one in
    memory is not doing anything wrong. What cannot be done is writing it out.
    """
    if _is_serialisable(value):
        return _as_data(value)

    if isinstance(value, dict):
        return {
            key: _checked_declaration(item, f"{where}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _checked_declaration(item, f"{where}[{index}]")
            for index, item in enumerate(value)
        ]

    raise ComponentSpecError(
        f"{where}: holds {type(value).__name__}, which no mapping can carry. "
        f"A PyCATSHOO variable and a Python callable are both live objects: "
        f"they build a component but do not survive being written out. "
        f"Declare the equivalent value instead -- a number for an occurrence "
        f"law's rate, a named condition for a callable -- or keep this "
        f"component a subclass"
    )


def _declared_object_spec(obj, registry, where):
    """Serialise a declared-continuous object (a profile, a transfer equation).

    Both families follow the same pattern deliberately: a plain class, a
    registry of the shapes a ``{"cls": ...}`` mapping may name, and a builder
    refusing anything else.

    **The two registries do not agree on their own base class, and the
    difference is deliberate on the transfer side only.** ``Transfer`` is left
    out of :data:`~muscadet.transfer.TRANSFER_CLASSES`, so a bare one is
    refused here by the registry test below. ``Profile`` IS in
    :data:`~muscadet.profile.PROFILE_CLASSES`, because ``{"cls": "Profile",
    "fun": f, "continuous": True}`` is a mapping a caller can hand to
    ``build_profile`` in memory -- a callable is a legal value in a Python
    dict, just not one that survives being written out. A bare ``Profile``
    therefore reaches the per-parameter loop instead, and is refused there, on
    its ``fun``. Both are refusals and neither is silent; only the message
    differs, and it names the offending parameter rather than the shape.

    The keys written are the constructor's own parameter names, read off the
    signature, so a shape added to a registry serialises without touching this
    module.
    """
    clsname = type(obj).__name__

    if clsname not in registry:
        raise ComponentSpecError(
            f"{where}: {clsname} carries a Python function and has no mapping "
            f"form, so this declaration cannot be read back as data. The "
            f"shapes that can are {', '.join(sorted(registry))}"
        )

    spec = {"cls": clsname}
    for param in inspect.signature(type(obj).__init__).parameters:
        if param == "self" or not hasattr(obj, param):
            continue
        value = getattr(obj, param)
        if not _is_serialisable(value):
            raise ComponentSpecError(
                f"{where}: parameter {param!r} of {clsname} holds "
                f"{type(value).__name__}, which cannot be written to a spec"
            )
        spec[param] = value

    return spec


#: The three fields a discrete output's production condition is STORED in, all
#: of them post-resolution and none of them a declaration. Handled together by
#: :func:`_prod_cond_spec` and never dumped as they stand.
PROD_COND_FIELDS = (
    "var_prod_cond",
    "var_prod_cond_negate",
    "var_prod_cond_compare",
)

#: The same three fields on a CAPACITY, where the vocabulary commands what the
#: volume RELEASES rather than what a flow produces (R49). Stored resolved for
#: the same reason, and walked back by the same function.
SERVE_COND_FIELDS = (
    "serve_cond",
    "serve_cond_negate",
    "serve_cond_compare",
)


def _matrix_at(matrix, row, column):
    """One cell of a parallel matrix that may be empty or short.

    ``var_prod_cond_negate`` is attached only when at least one operand is
    negated, so the common case leaves it empty rather than filled with False.
    """
    try:
        return matrix[row][column]
    except (IndexError, TypeError):
        return None


def _prod_cond_spec(comp, groups, negates, compares):
    """The DECLARATION form of a resolved condition.

    ``postprocess_flow_specs`` RESOLVES a condition as it is declared: the
    operand names are replaced by the flow (or measurement channel) objects
    themselves, and the negation and the comparison are lifted out into two
    parallel matrices beside them. What the flow then holds is not a
    declaration and cannot be re-declared: fed back as it stands, an operand
    reads as a mapping with a ``name`` and no ``op``, and the resolution refuses
    it -- a boolean operand never resolves onto a measurement channel, since a
    level carries no state to read, so a sensor's threshold comes back as
    ``Flow store does not exist as input nor output flow``.

    This walks the three fields back to the canonical
    ``{name, port, negate, op, value}`` operands. ``port`` is written
    explicitly for a flow, because the resolution that produced this one
    searched the inputs first and a component carrying the same name on both
    sides would otherwise come back resolved to the other side. It is
    deliberately NOT written for a measurement channel: that branch of the
    resolution is only reachable with no ``port`` at all.

    Takes the three matrices rather than the object holding them: a flow spells
    them ``var_prod_cond*`` and a capacity ``serve_cond*`` (R49), and one
    walk-back serves both.
    """
    groups = groups or []
    negates = negates or []
    compares = compares or []

    spec = []
    for row, group in enumerate(groups):
        operands = []
        for column, operand in enumerate(group):
            name = operand.name
            entry = {"name": name}

            if name in comp.measurements_in and comp.measurements_in[name] is operand:
                pass  # no port: the measurement branch needs it absent
            elif comp.flows_in.get(name) is operand:
                entry["port"] = "in"
            elif comp.flows_out.get(name) is operand:
                entry["port"] = "out"

            if _matrix_at(negates, row, column):
                entry["negate"] = True

            compare = _matrix_at(compares, row, column)
            if compare:
                entry["op"] = compare["op"]
                entry["value"] = compare["value"]

            operands.append(entry)
        spec.append(operands)

    return spec


def _declaration_fields(obj, where, skip=()):
    """The declaration fields of one pydantic declaration object.

    ``skip`` names fields the CALLER rebuilds itself and must therefore not be
    refused here. A capacity's ``serve_cond`` is the case it exists for: stored
    resolved, it holds flow objects no mapping can carry, and unlike the flow
    side's ``var_prod_cond`` it carries no ``var_`` prefix to fall through the
    runtime-handle branch on. Naming it explicitly is what that prefix does by
    accident, and says so.

    Drops the runtime handles (see :data:`RUNTIME_FIELD_PREFIXES`), serialises a
    declared profile or transfer equation through its registry, and REFUSES
    anything else that will not serialise, naming the field. The refusal is the
    point: an ``allocation_fun`` or a ``combine_fun`` is a real declaration that
    a mapping cannot carry, and a spec that quietly dropped it would rebuild a
    component splitting its supply by a different policy.
    """
    fields = {}

    def classify(key, value):
        # An unset handle serialises -- it is ``None`` until ``set_flows()``
        # wires it -- and a sensitive method's name serialises as the string it
        # is, so neither is caught by the refusal below. Both are plumbing, and
        # writing them into a spec fills every flow with four keys that say
        # nothing and that the next build recomputes anyway.
        if key.startswith(RUNTIME_HANDLE_PREFIXES) or (
            value is None and key.startswith(RUNTIME_FIELD_PREFIXES)
        ):
            return

        if _is_serialisable(value):
            fields[key] = value
            return

        if isinstance(value, (Profile, Transfer)):
            registry = (
                PROFILE_CLASSES if isinstance(value, Profile) else TRANSFER_CLASSES
            )
            fields[key] = _declared_object_spec(value, registry, f"{where}.{key}")
            return

        if key.startswith(RUNTIME_FIELD_PREFIXES):
            return

        raise ComponentSpecError(
            f"{where}: field {key!r} holds {type(value).__name__}, which "
            f"cannot be written to a spec. A Python callable is a declaration "
            f"no mapping can carry -- declare the equivalent shape instead, or "
            f"keep this component a subclass"
        )

    dumped = obj.model_dump()

    # A field DECLARED as a union with a model base, but HOLDING a subclass of
    # it, is serialised by the parent through the declared member: pydantic
    # dumps ``FlowOutTempo.occ_enable_flow``, typed ``Union[dict,
    # OccurrenceDistributionModel]``, through that base -- which carries no
    # fields -- so ``DelayOccDistribution(time=7)`` came out ``{"cls":
    # "DelayOccDistribution"}`` and rebuilt at ``time=0``. A seven-unit
    # temporisation became instantaneous, with nothing in the spec pointing at
    # the loss. Dumping such a value from the object it actually is restores
    # what it declares. The dict spelling of the same law was never affected,
    # which is what made this narrow enough to go unnoticed.
    for key, value in list(dumped.items()):
        live = getattr(obj, key, None)
        if isinstance(live, pydantic.BaseModel) and isinstance(value, dict):
            dumped[key] = live.model_dump()

    for key, value in dumped.items():
        if key in skip:
            continue
        classify(key, value)

    # The dump shows no excluded field at all, so a declaration carrying
    # ``exclude=True`` -- ``allocation_fun``, ``combine_fun``, ``profile`` --
    # has to be read off the object itself. Everything excluded that is not
    # listed as derived is treated as a declaration, so a field added later
    # trips the refusal above instead of disappearing.
    for key, field in type(obj).model_fields.items():
        if not field.exclude or key in fields or key in skip:
            continue
        if key.startswith(RUNTIME_FIELD_PREFIXES) or key in DERIVED_EXCLUDED_FIELDS:
            continue
        value = getattr(obj, key, None)
        if value is not None:
            classify(key, value)

    return fields


def _check_keys(spec):
    """Refuse a spec key muscadet does not read, by name.

    The same discipline as ``ContinuousComponent.DECLARATION_KEYS`` (R-3) and
    ``FlowContinuous.check_declaration_keys`` (R-15), and for the same reason: a
    misspelled section is otherwise swallowed whole, and a component silently
    missing its rule set is indistinguishable from one that never had any.
    """
    if not isinstance(spec, dict):
        raise ComponentSpecError(
            f"A component declaration is a mapping, got {type(spec).__name__}"
        )

    name = spec.get("name")
    if not name:
        raise ComponentSpecError(f"Component declaration without a 'name': {spec!r}")

    unknown = sorted(set(spec) - COMPONENT_KEYS)
    if unknown:
        plural = "s" if len(unknown) > 1 else ""
        raise ComponentSpecError(
            f"Component {name}: unknown declaration key{plural} "
            f"{', '.join(repr(key) for key in unknown)}; it accepts "
            f"{', '.join(sorted(COMPONENT_KEYS))}"
        )

    return name


def _entries(spec, section, name):
    """One declaration section, as a list, refusing a value that is not one."""
    entries = spec.get(section) or []

    if isinstance(entries, dict):
        entries = [entries]

    if not isinstance(entries, (list, tuple)):
        raise ComponentSpecError(
            f"Component {name}: section {section!r} is a list of declarations, "
            f"got {type(entries).__name__}"
        )

    return entries


def _failure_mode_method(entry, name):
    """The method one ``failure_modes`` entry maps to, or a refusal."""
    kind = entry.get("cls")
    method_name = FAILURE_MODE_METHODS.get(kind)

    if method_name is None:
        raise ComponentSpecError(
            f"Component {name}: failure mode {entry.get('name')!r} has "
            f"cls={kind!r}; a failure mode declared on a component is one of "
            f"{', '.join(sorted(FAILURE_MODE_METHODS))}. A standalone "
            f"ObjFailureMode is a component of its own and is declared as one"
        )

    return method_name


def check_spec(spec):
    """Validate a declaration WITHOUT building anything, and return its name.

    Everything checkable from the mapping alone is checked here, before the
    component exists: an engine object is expensive, a half-built one is worse
    than none, and a caller validating a batch of declarations should not have
    to raise a system to find out that one of them is misspelled.

    What is left to the build is what only the engine can answer -- that a rule
    names a declared flow, that a conduit does not meter what a rule already
    carries.
    """
    name = _check_keys(spec)

    for section in [key for key, _ in DECLARATION_SECTIONS] + list(
        POST_SET_FLOWS_SECTIONS
    ):
        for entry in _entries(spec, section, name):
            if not isinstance(entry, dict):
                raise ComponentSpecError(
                    f"Component {name}: every entry of section {section!r} is "
                    f"a mapping, got {type(entry).__name__}"
                )
            if section == "failure_modes":
                _failure_mode_method(entry, name)

    return name


def build_component(system, spec):
    """Build one component from a declaration held in data.

    Parameters
    ----------
    system : muscadet.System
        The system the component is added to.
    spec : dict
        The declaration. ``name`` is required; ``cls`` defaults to
        ``"ObjFlow"``. ``params`` is the declaration of the named class itself
        -- ``rate``, ``capacity``, ``activate`` and so on -- and the sections
        listed in :data:`DECLARATION_SECTIONS` and
        :data:`POST_SET_FLOWS_SECTIONS` are added on top of what that class
        declared.

    Returns
    -------
    muscadet.ObjFlow
        The built component, ``set_flows()`` already called, ready to connect.

    Raises
    ------
    ComponentSpecError
        For a missing ``name``, an unknown key, a section that is not a list, or
        ``params`` on a class that reads none.

    Notes
    -----
    The component is built with ``partial_init=True`` and ``set_flows()`` is
    called here, once, after every section that needs to precede it. That is why
    a spec never carries ``partial_init``: a caller who set it would either get
    a component built twice or one never wired to the engine.
    """
    name = check_spec(spec)
    clsname = spec.get("cls", "ObjFlow")
    params = spec.get("params") or {}

    if params and clsname == "ObjFlow":
        raise ComponentSpecError(
            f"Component {name}: 'params' names the declaration of a component "
            f"CLASS, and ObjFlow reads none -- its add_flows does nothing. The "
            f"keys {', '.join(sorted(params))} would be silently dropped. "
            f"Declare them in the sections instead, or name a class that reads "
            f"them"
        )

    comp = system.add_component(
        cls=clsname,
        name=name,
        label=spec.get("label"),
        description=spec.get("description"),
        metadata=spec.get("metadata", {}),
        create_default_out_automata=spec.get("create_default_out_automata", False),
        partial_init=True,
    )

    # ``cod3s.PycSystem.add_component`` WARNS on a name the system already
    # holds and returns None, so every line below dereferenced None and the
    # caller got ``'NoneType' object has no attribute 'metadata'`` -- a
    # traceback naming neither the spec nor the name that collided. A duplicate
    # instance name is the single most likely defect in a platform export or a
    # generated study, which is the input this module exists for, so it is the
    # one shape that has to name itself.
    if comp is None:
        raise ComponentSpecError(
            f"Component {name}: the system already holds a component of that "
            f"name. A spec builds a NEW component; give this one a distinct "
            f"'name', or read the existing one back with component_spec"
        )

    # The named class's own declaration. ``partial_init`` skipped the
    # constructor's call, so it is made here instead, which is what lets a
    # shipped class serve as a template the spec then adds to. ``metadata`` is
    # passed back the way ``ObjFlow.__init__`` passes it, since the continuous
    # KB accepts it as a declaration key.
    class_params = dict(params, metadata=comp.metadata)
    comp.add_flows(**class_params)

    for section, method_name in DECLARATION_SECTIONS:
        entries = _entries(spec, section, name)

        # Resolved only for a section the spec actually carries. Looked up for
        # every section, an entry of :data:`DECLARATION_SECTIONS` that a given
        # component class does not build -- the controller sections, which
        # belong to a PEER of ``ObjFlow`` (R39) -- made EVERY build of EVERY
        # component fail on a missing attribute, and the failure named a method
        # rather than a section.
        if not entries:
            continue

        method = getattr(comp, method_name, None)

        if method is None:
            raise ComponentSpecError(
                f"Component {name}: section {section!r} is declared, but "
                f"{clsname} has no {method_name}() to build it. A section a "
                f"spec may carry and the component cannot build is a "
                f"declaration silently lost"
            )

        for entry in entries:
            # ``add_flow`` takes the whole mapping positionally, the others take
            # it as keywords. A spec is data the caller keeps and may build
            # twice, so nothing here may write through it -- and ``add_flow``
            # needs no copy from us, ``postprocess_flow_specs`` opening with one
            # of its own. Copying twice cost a walk per flow on every build.
            if section == "flows":
                method(entry)
            else:
                method(**copy_declaration(entry))

    # The SAME arguments ``add_flows`` was given, exactly as
    # ``ObjFlow.__init__`` hands them to both. A class may override
    # ``set_flows`` and read its own declaration keys there:
    # ``SensorContinuous`` builds its deadband automaton in that override,
    # because the automaton reads state variables only ``set_flows`` creates.
    # Called bare, the override finds no ``activate`` in its kwargs, returns
    # early, and the sensor is built without the memory that holds its output
    # inside the band -- a component that thresholds but chatters.
    comp.set_flows(**class_params)

    # Driven from :data:`POST_SET_FLOWS_SECTIONS` rather than written out twice,
    # so the constant is what decides. Hardcoded, a third name added to it would
    # be admitted by ``COMPONENT_KEYS``, accepted by ``check_spec`` and then
    # silently ignored here -- the exact class of dead declaration this module
    # refuses everywhere else. The ``else`` below is what makes that impossible.
    #
    # ``copy_declaration``, not ``copy.deepcopy``, and this is the section where
    # the difference bites: an occurrence law legitimately holds the PyCATSHOO
    # variable its rate lives in -- ``{"cls": "exp", "rate": <IVariable>}``,
    # which is what ``add_exp_failure_mode`` writes so an indicator can name the
    # rate -- and deep-copying that raises ``Pickling of "Pycatshoo.IVariable"
    # instances is not enabled``. Sharing the leaves is also the correct
    # semantics, not merely the one that runs: an engine handle is one variable.
    for section in POST_SET_FLOWS_SECTIONS:
        for entry in _entries(spec, section, name):
            entry = copy_declaration(entry)
            if section == "automata":
                comp.add_atm2states(**entry)
            elif section == "failure_modes":
                method_name = _failure_mode_method(entry, name)
                entry.pop("cls", None)
                getattr(comp, method_name)(**entry)
            else:
                raise ComponentSpecError(
                    f"Component {name}: section {section!r} is declared in "
                    f"POST_SET_FLOWS_SECTIONS but build_component does not "
                    f"build it. A section a spec may carry and nothing builds "
                    f"is a declaration silently lost"
                )

    return comp


def component_spec(comp):
    """Read a live component back into a declaration.

    What a spec can hold, it holds: the flows with their declaration fields, the
    capacities, the measurement channels, the rule sets, the transfer pairs, and
    the automata and failure modes the component was DECLARED with.

    Two things it deliberately does not hold, and both are absences worth
    knowing about:

    - the automata muscadet DERIVES -- a discrete output's default ok/nok pair,
      a sensor's deadband, the pair a failure mode builds. They are recreated by
      the declaration that generates them, so emitting them here would build
      each one twice. This is why ``ObjFlow.declared_automata`` exists rather
      than reading ``automata_d``, whose entries carry no record of what asked
      for them;
    - anything holding a Python callable, which is refused rather than dropped
      (see :func:`_declaration_fields`).

    The spec is always expanded onto ``ObjFlow``: reading back a
    ``SensorContinuous`` gives its three discrete outputs and its measurement
    channel, not the five parameters it was declared with. The class name is
    kept under :data:`SOURCE_CLS_KEY` for a caller that wants to show it.

    Parameters
    ----------
    comp : muscadet.ObjFlow

    Returns
    -------
    dict
        A spec :func:`build_component` accepts.

    Raises
    ------
    ComponentSpecError
        When a declaration holds something no mapping can carry.
    """
    where = f"Component {comp.basename()}"

    flows = []
    for side, flow_dict in (("in", comp.flows_in), ("out", comp.flows_out)):
        for flow_name, flow in flow_dict.items():
            fields = _declaration_fields(flow, f"{where}, flow {flow_name} {side}")

            # The production condition is stored resolved, so it is rebuilt
            # rather than dumped, and the two matrices derived from it are
            # dropped: ``postprocess_flow_specs`` recomputes them, and a stale
            # copy beside a rebuilt condition is worse than none.
            for field in PROD_COND_FIELDS:
                fields.pop(field, None)
            prod_cond = _prod_cond_spec(
                comp,
                getattr(flow, "var_prod_cond", None),
                getattr(flow, "var_prod_cond_negate", None),
                getattr(flow, "var_prod_cond_compare", None),
            )
            if prod_cond:
                fields["var_prod_cond"] = prod_cond

            # ``model_dump`` writes ``cls`` already, through the ObjCOD3S dump
            # hook; setting it explicitly keeps the spec correct if that ever
            # changes, and costs nothing.
            fields["cls"] = type(flow).__name__
            flows.append(fields)

    def dump_all(objects, kind, skip=()):
        out = []
        for obj_name, obj in objects.items():
            fields = _declaration_fields(obj, f"{where}, {kind} {obj_name}", skip=skip)
            fields.pop("cls", None)
            out.append(fields)
        return out

    # A discharge command is stored RESOLVED, exactly as a production condition
    # is, so it is rebuilt rather than dumped and the two matrices derived from
    # it are skipped: the rebuild recomputes them, and a stale copy beside a
    # rebuilt condition is worse than none.
    capacities = dump_all(comp.capacities, "capacity", skip=SERVE_COND_FIELDS)

    for entry, capacity in zip(capacities, comp.capacities.values()):
        serve_cond = _prod_cond_spec(
            comp,
            getattr(capacity, "serve_cond", None),
            getattr(capacity, "serve_cond_negate", None),
            getattr(capacity, "serve_cond_compare", None),
        )

        if serve_cond:
            entry["serve_cond"] = serve_cond
        else:
            # Without a condition the mode says nothing, and writing it would
            # change the bytes of every capacity spec ever stored -- the very
            # argument the infinite ceiling is popped on, just below.
            entry.pop("serve_cond_inner_mode", None)

        # A discharge ceiling of ``inf`` says nothing either, and writing it
        # would put the JSON literal ``Infinity`` into EVERY capacity spec,
        # including those of models declared before the field existed (R48).
        # ``fill_rate`` is the counter-example and stays: its default is 0.0,
        # so an infinite one is something a modeller wrote.
        if entry.get("serve_rate") == math.inf:
            entry.pop("serve_rate")

    transfers = []
    for pair_name, pair in comp.transfers.items():
        transfers.append(
            {
                "name": pair.name,
                "flows": list(pair.flows),
                "equation": _declared_object_spec(
                    pair.equation,
                    TRANSFER_CLASSES,
                    f"{where}, transfer {pair_name}",
                ),
            }
        )

    spec = _as_data(
        {
            "name": comp.basename(),
            "cls": "ObjFlow",
            SOURCE_CLS_KEY: type(comp).__name__,
            "flows": flows,
            "capacities": capacities,
            "measurements_in": dump_all(comp.measurements_in, "measurement in"),
            "measurements_out": dump_all(comp.measurements_out, "measurement out"),
            "rules": dump_all(comp.rule_sets, "rule set"),
            "transfers": transfers,
        }
    )

    # Declared verbatim rather than dumped, so they get their own gate -- see
    # :func:`_checked_declaration`.
    for section, declared, kind in (
        ("automata", comp.declared_automata, "automaton"),
        ("failure_modes", comp.declared_failure_modes, "failure mode"),
    ):
        spec[section] = [
            _checked_declaration(entry, f"{where}, {kind} {entry_name}")
            for entry_name, entry in declared.items()
        ]

    # The constructor's own declaration. Written only when it says something:
    # ``label`` defaults to the name and ``description`` to the label, so
    # emitting them unconditionally would fill every spec with its own name
    # twice. ``create_default_out_automata`` is the one that is BEHAVIOUR
    # rather than decoration -- dropped, the rebuilt component had no ok/nok
    # pair on its discrete outputs and any indicator naming one was silently
    # gone -- and ``metadata`` is where a platform export attaches what it
    # knows about an instance.
    if comp.label != comp.basename():
        spec["label"] = comp.label
    if comp.description != comp.label:
        spec["description"] = comp.description
    if comp.metadata:
        spec["metadata"] = _checked_declaration(comp.metadata, f"{where}, metadata")
    if getattr(comp, "has_default_out_automata", False):
        spec["create_default_out_automata"] = True

    return spec


# ---------------------------------------------------------------------------
# The SYSTEM scale: what a component declaration cannot carry
# ---------------------------------------------------------------------------

#: Version of the system declaration format, semver. An optional field added
#: is a patch, a required field or a new kind is a minor, a removal or a
#: changed meaning is a major. A reader refuses a major it does not know
#: rather than guessing, because the whole point of this document is that two
#: engines read the SAME thing.
SYSTEM_SPEC_VERSION = "1.0.0"

#: What an indicator declaration carries. Read back concretely rather than as
#: the pattern the modeller typed: ``add_indicator`` takes regexes and expands
#: them, so a declaration holding the pattern would re-expand against whatever
#: components happen to exist at rebuild time, which is not the same system.
_INDICATOR_COMMON_KEYS = (
    "name",
    "label",
    "description",
    "unit",
    "measure",
    "stats",
    "component",
    "operator",
    "value_test",
)

#: The three things cod3s knows how to observe, each naming its subject with a
#: DIFFERENT key and built by a different method. So a declaration states its
#: ``kind``: without it, a variable and a state are indistinguishable mappings
#: and the rebuild would have to guess which method to call.
_INDICATOR_KINDS = {
    "PycVarIndicator": ("var", "add_indicator_var", ()),
    "PycSTIndicator": ("state", "add_indicator_state", ()),
    "PycAttrIndicator": ("attr_name", "add_indicator", ("attr_type",)),
}


class SystemSpecError(ValueError):
    """A system declaration that cannot be read or cannot be built."""


def _anchor(name):
    """``add_indicator`` matches on a regex: anchor so one name means one match.

    Unanchored, ``occ`` also matches ``not_occ`` and the rebuilt system gains an
    indicator the original never had. Same anchoring the COD3S Platform
    translator applies, and for the same reason.
    """
    return f"^{re.escape(str(name))}$"


def system_connections(system):
    """Every wired connection of ``system``, as data.

    Read from ``get_cnct_info()``, which is the engine's own view of what is
    wired, rather than from a log of what the modeller called: a system built
    by any route reads back the same way.

    Each entry carries the two message boxes, and ``flow`` when the pair
    follows the ``{flow}_out`` / ``{flow}_in`` convention. That name is not
    decoration: rebuilding through :meth:`System.connect_flow` re-runs the
    family check that refuses a discrete output feeding a continuous input,
    which the raw ``connect`` route does not.
    """
    components = getattr(system, "comp", None) or {}
    by_engine_name = {}
    for key, comp in components.items():
        by_engine_name[getattr(comp, "name", key)] = key
        by_engine_name.setdefault(key, key)

    out = []
    for key, comp in components.items():
        info = comp.get_cnct_info() or {}
        for source_box, box_info in info.items():
            if not str(source_box).endswith("_out"):
                continue
            for target in (box_info or {}).get("targets", []) or []:
                target_key = by_engine_name.get(target.get("obj"), target.get("obj"))
                target_box = target.get("cnct")
                entry = {
                    "source": key,
                    "source_box": source_box,
                    "target": target_key,
                    "target_box": target_box,
                }
                flow = str(source_box)[: -len("_out")]
                if str(target_box) == f"{flow}_in":
                    entry["flow"] = flow
                out.append(entry)
    return sorted(
        out, key=lambda e: (e["source"], e["source_box"], e["target"], e["target_box"])
    )


def system_indicators(system):
    """Every indicator of ``system``, as data, concretely named."""
    out = []
    for indic in (getattr(system, "indicators", None) or {}).values():
        kind = type(indic).__name__
        if kind not in _INDICATOR_KINDS:
            raise SystemSpecError(
                f"indicator {getattr(indic, 'name', indic)!r} is a {kind}, which this "
                f"declaration cannot carry (known: {sorted(_INDICATOR_KINDS)})"
            )
        subject_key, _builder, extra = _INDICATOR_KINDS[kind]
        try:
            dumped = indic.model_dump()
        except AttributeError as err:  # pragma: no cover - cod3s always pydantic
            raise SystemSpecError(
                f"indicator {indic!r} cannot be read as data"
            ) from err
        keys = _INDICATOR_COMMON_KEYS + (subject_key,) + extra
        spec = {k: dumped[k] for k in keys if dumped.get(k) is not None}
        spec["kind"] = kind
        out.append(spec)
    return sorted(out, key=lambda s: str(s.get("name") or ""))


def system_spec(system):
    """Read a live system back as a declaration held in DATA.

    The counterpart of :func:`build_system`, and the system-scale sibling of
    :func:`component_spec`. What it adds over reading each component is the
    part no component knows: how they are wired, and what is observed.

    Deliberately NOT included: targets and simulation parameters. Those are the
    configuration of a RUN, handed to ``simulate()``, not the description of a
    system. Two systems carrying the same declaration are the same system,
    whatever one intends to compute on them. The COD3S Platform already draws
    this line, between its model export and its study.

    Raises
    ------
    ComponentSpecError
        Through :func:`component_spec`, when a component holds something no
        mapping can carry -- a Python callable, typically.
    """
    return {
        "version": SYSTEM_SPEC_VERSION,
        "name": (
            getattr(system, "name", lambda: None)()
            if callable(getattr(system, "name", None))
            else getattr(system, "name", None)
        ),
        "components": {
            name: component_spec(comp) for name, comp in (system.comp or {}).items()
        },
        "connections": system_connections(system),
        "indicators": system_indicators(system),
    }


def check_system_spec(spec):
    """Validate a system declaration without building anything.

    Sorting a batch before paying the engine cost, and the reason the version
    is checked HERE: a document from a future major is refused with its own
    number in the message, rather than half-built into a system whose shape
    nobody can explain.
    """
    if not isinstance(spec, dict):
        raise SystemSpecError(
            f"a system declaration is a mapping, got {type(spec).__name__}"
        )

    version = spec.get("version")
    if version is None:
        raise SystemSpecError("a system declaration carries a 'version'")
    major = str(version).split(".")[0]
    if major != SYSTEM_SPEC_VERSION.split(".")[0]:
        raise SystemSpecError(
            f"system declaration version {version!r} is not readable by this "
            f"muscadet, which reads {SYSTEM_SPEC_VERSION.split('.')[0]}.x"
        )

    components = spec.get("components")
    if not isinstance(components, dict):
        raise SystemSpecError(
            "'components' is a mapping of name to component declaration"
        )
    for name, comp_spec in components.items():
        check_spec(comp_spec)

    for entry in spec.get("connections") or []:
        missing = [
            k
            for k in ("source", "source_box", "target", "target_box")
            if not entry.get(k)
        ]
        if missing:
            raise SystemSpecError(f"connection {entry!r}: missing {missing}")
        for side in ("source", "target"):
            if entry[side] not in components:
                raise SystemSpecError(
                    f"connection {entry!r}: {side} {entry[side]!r} is not a declared component"
                )

    for entry in spec.get("indicators") or []:
        kind = entry.get("kind")
        if kind not in _INDICATOR_KINDS:
            raise SystemSpecError(
                f"indicator {entry!r}: 'kind' must be one of {sorted(_INDICATOR_KINDS)}, got {kind!r}"
            )
        subject_key = _INDICATOR_KINDS[kind][0]
        missing = [k for k in ("component", subject_key) if not entry.get(k)]
        if missing:
            raise SystemSpecError(f"indicator {entry!r}: missing {missing}")


def build_system(spec, system=None):
    """Build a whole system from a declaration held in data.

    Components first, then the wiring, then what is observed: the order is not
    a preference. A connection needs both ends to exist, and an indicator
    resolves against components that are already there.

    Parameters
    ----------
    spec : dict
        A declaration produced by :func:`system_spec`, or written by hand.
    system : muscadet.System, optional
        An existing system to fill. Given one, the caller owns its creation --
        which matters because PyCATSHOO forbids more than one system per
        process, so a caller comparing two declarations cannot let this
        function create either of them.

    Returns
    -------
    muscadet.System
    """
    check_system_spec(spec)

    if system is None:
        from muscadet.system import System

        system = System(name=spec.get("name") or "system")

    for name, comp_spec in spec["components"].items():
        build_component(system, {**comp_spec, "name": comp_spec.get("name", name)})

    for entry in spec.get("connections") or []:
        flow = entry.get("flow")
        if flow:
            # Through ``connect_flow`` when the convention allows: it re-runs
            # the family check that refuses a discrete output feeding a
            # continuous input, which the raw route accepts in silence.
            system.connect_flow(
                source=entry["source"], target=entry["target"], flow_name=flow
            )
        else:
            system.connect(
                entry["source"],
                entry["source_box"],
                entry["target"],
                entry["target_box"],
            )

    for entry in spec.get("indicators") or []:
        indic = dict(entry)
        subject_key, builder, _extra = _INDICATOR_KINDS[indic.pop("kind")]
        # The three builders DERIVE the name, the label and the description
        # from what they are HANDED, not from what the indicator turns out to
        # be: an already-derived name handed back gets derived a second time
        # (``Local_tank_qty_H2`` becomes ``Local_tank_qty_H2_tank_qty_H2_value``).
        # They are read back for a reader, and imposed here, because the
        # declaration is what says how an indicator is called.
        imposed = {
            k: indic.pop(k) for k in ("name", "label", "description") if k in indic
        }
        indic["component"] = _anchor(indic["component"])
        indic[subject_key] = _anchor(indic[subject_key])
        created = getattr(system, builder)(**indic) or []
        if imposed.get("name"):
            if len(created) != 1:
                raise SystemSpecError(
                    f"indicator {imposed['name']!r} resolved to {len(created)} indicators; "
                    "a declaration names exactly one"
                )
            built = created[0]
            system.indicators.pop(built.name, None)
            for key, value in imposed.items():
                setattr(built, key, value)
            system.indicators[built.name] = built

    return system
