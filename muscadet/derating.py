"""What the failure modes bearing on a continuous output leave of its rate.

A continuous output carries no boolean availability gate: a mode reaches it
through a **derating** instead, a factor in [0, 1] that multiplies what the
rules produced (R18, R19, KD10). The engine is a handful of functions over a
component, which ``ObjFlow`` binds as methods of the same name.

The design this unit implements, in one sentence: **one variable per (mode,
output) pair, composed by minimum at read time**. Two modes derating one output
own two variables rather than sharing one, so neither overwrites the other and
repairing one while the other still stands leaves the surviving degradation in
force (R20, KTD8). The composition itself lives on the flow, in
``FlowContinuousOut.get_effective_rate``; what lives here is the allocation
(:func:`add_derating`), the resolution of a mode's declared effects onto it
(:func:`resolve_mode_effects`) and the return to nominal a mode owes its own
variables (:func:`release_deratings`), a derating having no per-step reset.
"""

import re

from .flow_continuous import (
    NOMINAL_RATE,
    FlowContinuous,
    FlowContinuousOut,
)


def match_flow_name(pattern, flow_name):
    """
    Whether an effect pattern names ``flow_name``, ANCHORED.

    The one spelling of the rule, shared by the two paths a mode reaches a
    flow through: the modes muscadet declares on a component (here) and the
    standalone ``ObjFailureMode*`` family, which anchors the very same way
    (``ObjFailureMode.resolve_effects_on``). Unanchored, ``"H2"`` would name
    ``H2O`` as well and a declaration meant for one output would silently
    derate its neighbour -- two spellings of one declaration producing
    different physics.

    ``^...$`` rather than :func:`re.fullmatch`, deliberately: it is the exact
    expression the standalone path uses, so an alternation is anchored the
    same way on both sides.
    """
    return re.search(f"^{pattern}$", flow_name) is not None


def match_continuous_outputs(comp, pattern):
    """
    Returns the continuous outputs an effect pattern bears on (R18).

    Matched on the flow NAME and on the name of the variable it exports, so
    ``"X"`` and ``"X_fed_out"`` designate the same output -- the two
    spellings a 1.x effect string uses for a discrete flow. Both matches are
    ANCHORED: see :func:`match_flow_name`.

    Parameters
    ----------
    pattern : str
        The effect pattern, a regular expression as everywhere else.

    Returns
    -------
    list of str
        The matching continuous output flow names, in declaration order.
        Empty for a purely discrete component, which is what leaves boolean
        effects resolved exactly as they were.
    """
    return [
        flow_name
        for flow_name in comp.flows_continuous_out
        if match_flow_name(pattern, flow_name)
        or match_flow_name(pattern, f"{flow_name}_fed_out")
    ]


def add_derating(comp, mode_name, flow_name):
    """
    Allocates the derating variable a mode owns on a continuous output.

    One variable per (mode, output flow) pair (R18), named
    ``{mode}_derating_{flow}`` and created at 1 -- the rate of an output
    nothing derates. Two modes derating the same output therefore own two
    variables, and the effective rate is the minimum over them (R20, KTD8)
    rather than whatever the mode that fired last wrote.

    Called by :meth:`resolve_mode_effects` at declaration time, and public
    so that a mode declared OUTSIDE the component -- a standalone
    ``cod3s.ObjFM*`` naming variables by their exact basename -- can
    allocate the variable it needs and target it.

    Parameters
    ----------
    mode_name : str
        The declaring mode, unique per component.
    flow_name : str
        A continuous output of this component.

    Returns
    -------
    The PyCATSHOO variable to clamp, at the rate the mode leaves.

    Raises
    ------
    ValueError
        When ``flow_name`` is not a continuous output of this component:
        only a continuous output carries a rate (R19).
    """
    flow = comp.flows_out.get(flow_name)

    if not isinstance(flow, FlowContinuousOut):
        raise ValueError(
            f"Object {comp.name()}: cannot derate {flow_name!r}: it is not "
            "a continuous output flow of this component -- only a "
            "continuous output carries a rate"
        )

    return flow.register_derating(comp, mode_name)


def derating_vars_of(comp, mode_name):
    """
    Returns the derating variables ``mode_name`` owns, keyed by basename.

    The discovery side of R18: an output knows which modes derate it, so a
    mode can be asked back what it derates without holding a registry of
    its own.
    """
    return {
        flow.derating[mode_name].basename(): flow.derating[mode_name]
        for flow in comp.flows_continuous_out.values()
        if mode_name in flow.derating
    }


def derate_the_output(flow_name):
    """The advice a refusal ends on: name the output, not one of its variables."""
    return (
        f"derate the output itself -- ({flow_name!r}, rate) -- which muscadet "
        "routes onto the derating variable the declaring mode owns"
    )


def solver_owned_endpoints(comp):
    """
    Returns ``{variable basename: what to clamp instead}`` for every variable
    the solver or the sweeps rewrite at every integration step.

    A clamp on one of them is erased inside the step, so an effect naming one
    is a **silent no-op** -- the model builds, runs to completion and reports
    the availability figures of a plant whose modelled failure never happened.
    That is why the set is exhaustive rather than confined to the flow
    endpoints it started as, and why each entry carries the endpoint that
    *would* work: the whole point of listing a variable here is to be able to
    refuse it by name and say what to write instead.

    What is in it, and why each one cannot be clamped:

    * ``{flow}_fed_{in,out}`` / ``{flow}_demand_{in,out}`` -- what a
      continuous flow carries and what it asks for, rewritten by the two
      sweeps (R19);
    * ``{flow}_out_profile`` -- a read-only PUBLICATION of the factor the
      production sweep applied, not an input;
    * a capacity's ``{c}_qty*`` / ``{c}_fill*`` (integrated or derived from
      the levels) and ``{c}_inflow_{f}`` / ``{c}_outflow_{f}`` (written by
      the sweeps at every hop);
    * a published measurement's ``{m}_level`` / ``{m}_fill`` **when it
      declares a source**, since ``compute_measurements`` republishes them.
      Without a source they are plain writable variables a mode may drive,
      which is exactly what they are for, so they stay out.

    ``{flow}_out_rate`` and ``{m}_level_gain`` are deliberately NOT here:
    they are the public endpoints a mode declared outside muscadet clamps
    (KD10, R37), and muscadet never writes either.
    """
    endpoints = {}

    for flow in list(comp.flows_in.values()) + list(comp.flows_out.values()):
        if not isinstance(flow, FlowContinuous):
            continue

        if isinstance(flow, FlowContinuousOut):
            advice = derate_the_output(flow.name)
        else:
            advice = (
                "a continuous INPUT carries no clampable endpoint: derate the "
                f"output feeding {flow.name!r} on the producing component"
            )

        for var in (flow.var_fed, flow.var_demand):
            if var is not None:
                endpoints[var.basename()] = advice

        var_profile = getattr(flow, "var_profile", None)
        if var_profile is not None:
            endpoints[var_profile.basename()] = (
                "a time profile is PUBLISHED, never driven: declare a different "
                f"profile on {flow.name!r}, or {derate_the_output(flow.name)}"
            )

    for capacity in comp.capacities.values():
        held = ", ".join(repr(name) for name in capacity.flow_names)
        advice = (
            f"a capacity's levels and transit rates are integrated by the "
            f"solver: derate the output it buffers ({held}), or gate what "
            "crosses it with a rule guard"
        )
        variables = (
            list(capacity.var_qty.values())
            + list(capacity.var_fill.values())
            + list(capacity.var_inflow.values())
            + list(capacity.var_outflow.values())
            + [capacity.var_qty_total, capacity.var_fill_total]
        )
        for var in variables:
            if var is not None:
                endpoints[var.basename()] = advice

    for measurement in comp.measurements_out.values():
        # A publication with no source is a plain writable variable: driving it
        # from a mode is its documented use, so it is left clampable.
        if measurement.source is None:
            continue
        advice = (
            f"a published reading is rewritten from {measurement.source!r} at "
            f"every integration step: clamp {measurement.name}_level_gain, the "
            "public gain everything this channel publishes is multiplied by"
        )
        for var in (measurement.var_level, measurement.var_fill):
            if var is not None:
                endpoints[var.basename()] = advice

    return endpoints


def continuous_endpoint_names(comp):
    """
    Returns the basenames of the variables the solver and the sweeps own.

    The names of :func:`solver_owned_endpoints`, which is where what belongs
    in the set -- and what to clamp instead of each -- is written down.
    """
    return set(solver_owned_endpoints(comp))


def resolve_effect_patterns(comp, pat_value_list, continuous_endpoint):
    """
    Resolves effect patterns onto variables, BOTH flow families per pattern.

    The single resolution both muscadet-side entry points go through --
    :func:`resolve_mode_effects` for a mode declared on the component,
    :func:`pat_to_var_value_list` for a caller holding only a pattern -- and
    the same shape as ``ObjFailureMode.resolve_effects_on``, which is what
    resolves a standalone mode. All three now answer alike.

    Two levels, in this order:

    1. **the output flows**, matched by name and ANCHORED
       (:func:`match_flow_name`). A continuous output goes to
       ``continuous_endpoint(flow_name)``; a discrete one to its availability
       gate. Both families are scanned for EVERY pattern, which is the whole
       correction: a pattern was previously diverted to the continuous branch
       as soon as it matched one continuous output, so ``(".*", False)`` on a
       plant declaring an ``H2`` rate beside an ``H2_status`` signal derated
       the rate and left the signal announcing that the plant was alive.
    2. **the component's variable basenames**, the unanchored 1.x resolution,
       reached only when the pattern names no output flow at all. This is what
       keeps ``("is_ok_fed_available_out", False)`` -- the dominant 1.x
       spelling -- and every effect on a variable that is not a flow
       (``{m}_level_gain``, ``{flow}_out_rate``) resolving exactly as before.

    A pattern whose ONLY matches are variables the solver rewrites is
    **refused** rather than silently dropped: see
    :func:`solver_owned_endpoints`. A pattern that also matches something
    clampable keeps the silent drop, which is what lets a wildcard sweep a
    component without tripping over its buffers.

    Parameters
    ----------
    pat_value_list : iterable of tuples
        ``(pattern, value)`` pairs, patterns being regular expressions.
    continuous_endpoint : callable
        ``f(flow_name) -> variable``: what a continuous output's effect is
        written to. The per-mode derating variable when the declaring mode is
        known, the shared ``{flow}_out_rate`` when it is not.

    Returns
    -------
    list of tuples
        ``(variable, value)`` pairs.
    """
    pat_value_list = list(pat_value_list)
    resolved = []
    fallback = []

    for pattern, value in pat_value_list:
        matched = []

        for flow_name, flow in comp.flows_out.items():
            continuous = isinstance(flow, FlowContinuousOut)
            spellings = [flow_name]
            if continuous:
                # The 1.x spelling of an effect on an output, kept working.
                spellings.append(f"{flow_name}_fed_out")

            if not any(match_flow_name(pattern, name) for name in spellings):
                continue

            if continuous:
                matched.append((continuous_endpoint(flow_name), float(value)))
            elif flow.var_fed_available is not None:
                matched.append((flow.var_fed_available, value))

        if matched:
            resolved += matched
        else:
            fallback.append((pattern, value))

    if not fallback:
        return resolved

    # No output flow named: the 1.x resolution over variable basenames. Taken
    # over ONE snapshot of the component's variables, and only when a fallback
    # is actually needed -- ``comp.variables()`` builds a fresh proxy per call,
    # so resolving pattern by pattern through the cod3s helper would multiply
    # the engine objects a declaration allocates.
    variables = [(var, var.basename()) for var in comp.variables()]
    solver_owned = solver_owned_endpoints(comp)

    for pattern, value in fallback:
        by_name = [
            (var, basename, value)
            for var, basename in variables
            if re.search(pattern, basename)
        ]
        keep = [
            (var, val) for var, basename, val in by_name if basename not in solver_owned
        ]

        if keep or not by_name:
            resolved += keep
            continue

        raise ValueError(
            unclampable_message(
                comp, pattern, [basename for _, basename, _ in by_name], solver_owned
            )
        )

    return resolved


def unclampable_message(comp, pattern, names, solver_owned):
    """Says which variables a refused effect reached, and what to write instead."""
    advices = []
    for name in names:
        advice = solver_owned[name]
        if advice not in advices:
            advices.append(advice)

    plural = "s" if len(names) > 1 else ""

    return (
        f"Object {comp.name()}: effect pattern {pattern!r} matches only "
        f"variable{plural} the solver rewrites at every integration step "
        f"({', '.join(names)}), so the clamp would be erased inside the step "
        f"and the mode would be a silent no-op. Instead, {'; '.join(advices)}"
    )


def pat_to_var_value_list(comp, *pat_value_list):
    """
    Resolves effect patterns onto variables, continuous outputs included.

    Overrides ``cod3s.PycComponent.pat_to_var_value_list``, whose plain regex
    over the component's variable basenames has two wrong answers on a
    continuous output: it returns ``{flow}_fed_out``, which the production
    equation rewrites at every integration step so that a clamp on it never
    survives, and -- since KD10 gave every continuous output a
    ``{flow}_out_rate`` -- it would ALSO return that one, so a single pattern
    would resolve to a variable that works and one that silently does not.

    Here a pattern naming a continuous output resolves to that output's rate
    variable and to none of its other variables, a pattern naming a discrete
    output to that output's availability gate -- both families in the same
    pass -- and the solver-owned endpoints are unreachable whatever the
    pattern (R19). See :func:`resolve_effect_patterns` for the two levels and
    for what a pattern reaching nothing BUT a solver-owned variable is
    refused with.

    Note that this is NOT the path muscadet's OWN modes take on a continuous
    output: :func:`resolve_mode_effects` routes those to the per-mode derating
    variable, because muscadet knows the declaring mode's identity and can
    therefore keep concurrent deratings apart (R18, R20). This function is
    what is left for everything else -- a caller holding only a pattern and a
    value, with no mode to attribute them to.

    Parameters
    ----------
    *pat_value_list : list of tuples
        ``(pattern, value)`` pairs, patterns being regular expressions.

    Returns
    -------
    list of tuples
        ``(variable, value)`` pairs.
    """
    return resolve_effect_patterns(
        comp,
        pat_value_list,
        lambda flow_name: comp.flows_out[flow_name].var_out_rate,
    )


def resolve_mode_effects(comp, mode_name, effects):
    """
    Resolves one direction of a mode's effects into (variable, value) pairs.

    A pattern naming a CONTINUOUS OUTPUT is a derating declaration (R18): it
    is rewritten, here at declaration time, onto the variable ``mode_name``
    owns on that output. Two modes declaring the same effect string
    therefore write two variables and compose by minimum (R20, KD11, KTD8)
    instead of overwriting one another -- which is the whole point, since a
    shared variable would be last-writer-wins and the first mode to repair
    would restore the rate while the other degradation still stood.

    A pattern naming a DISCRETE output is resolved beside it, in the same
    pass, onto that output's availability gate: one pattern reaches every
    output it names, whichever family each one belongs to.

    Everything else keeps the 1.x resolution: an unanchored regex over the
    component's variable basenames -- see :func:`resolve_effect_patterns`.

    Parameters
    ----------
    mode_name : str
        The declaring mode: what the derating variable is named from.
    effects : list of tuples
        ``(pattern, value)`` pairs as declared on the mode.

    Returns
    -------
    list of tuples
        ``(variable, value)`` pairs to clamp while the state holds.
    """
    return resolve_effect_patterns(
        comp,
        effects,
        lambda flow_name: comp.add_derating(mode_name, flow_name),
    )


def release_deratings(comp, mode_name, *var_value_lists):
    """
    Gives every derating of ``mode_name`` a return to nominal, in place.

    A mode owns its derating variables, so it owns their release: a mode
    that derates on one of its two states restores :data:`NOMINAL_RATE` on
    the other, unless it declares a value there itself (a mode returning
    degraded rather than as-new is a legitimate model).

    Necessary because a derating variable has NO per-step reset, unlike the
    boolean availability gate: a reset value that composes with a minimum
    does not exist, so what the library reinitialises for a gate it must
    hand back explicitly here. Without it, a repaired mode would leave its
    own degradation standing for the rest of the sequence.

    Parameters
    ----------
    mode_name : str
        The declaring mode.
    *var_value_lists : list
        The ``(variable, value)`` lists of the mode's states, MUTATED in
        place. Order-independent: each list is completed against the
        variables the mode owns, not against the other list.
    """
    derating_vars = comp.derating_vars_of(mode_name)

    for var_value_list in var_value_lists:
        clamped = {var.basename() for var, _ in var_value_list}
        var_value_list += [
            (var, NOMINAL_RATE)
            for basename, var in derating_vars.items()
            if basename not in clamped
        ]
