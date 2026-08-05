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


def match_continuous_outputs(comp, pattern):
    """
    Returns the continuous outputs an effect pattern bears on (R18).

    Matched on the flow NAME and on the name of the variable it exports, so
    ``"X"`` and ``"X_fed_out"`` designate the same output -- the two
    spellings a 1.x effect string uses for a discrete flow.

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
        if re.search(pattern, flow_name) or re.search(pattern, f"{flow_name}_fed_out")
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


def continuous_endpoint_names(comp):
    """
    Returns the basenames of the continuous variables the sweeps own.

    What a continuous flow carries, and what an input demands: written by
    the production and demand equations at every integration step. A mode
    clamping one of them would be overwritten within the step, so a mode
    reaches a continuous output through its derating variable and nowhere
    else (R19).
    """
    names = set()

    for flow in list(comp.flows_in.values()) + list(comp.flows_out.values()):
        if not isinstance(flow, FlowContinuous):
            continue
        for var in (flow.var_fed, flow.var_demand):
            if var is not None:
                names.add(var.basename())

    return names


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

    Everything else keeps the 1.x resolution: a regex over the component's
    variable basenames, through ``pat_to_var_value_list``.

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
    patterns = []
    derated = []

    for pattern, value in effects:
        flow_names = comp.match_continuous_outputs(pattern)

        if not flow_names:
            patterns.append((pattern, value))
            continue

        derated += [
            (comp.add_derating(mode_name, flow_name), float(value))
            for flow_name in flow_names
        ]

    solver_owned = comp.continuous_endpoint_names()

    return [
        (var, value)
        for var, value in comp.pat_to_var_value_list(*patterns)
        if var.basename() not in solver_owned
    ] + derated


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
