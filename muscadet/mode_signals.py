"""What a cod3s mode declared OUTSIDE a component reads and writes on it.

``ObjFlow.mode_signals`` is what :mod:`muscadet.ordering` consults to answer
"can this component's production depend on that discrete input?"
(``gates_production_on``) and "does a signal arriving here leave again?"
(``tainted_output_states``). A mode declared ON the component writes its entry
itself (``ObjFlow.add_atm2states``). A mode declared as a component of its own
-- the ``cod3s.ObjMode2S`` family, ``ObjFM``, ``ObjFMExp``, ``ObjFMDelay``,
``ObjFMInst`` included -- knows nothing about muscadet, so its entries are
published here, once per (mode, target), at the pre-run step: every component
and every mode exists by then, and nothing has been registered yet.

Unpublished, such a mode is invisible to both questions, and an instantaneous
loop closed through it escapes the first-run check: the model builds and the
gated production settles on whatever the topological order happens to evaluate
first. That was R-19, closed for the muscadet ``ObjFailureMode*`` façades in
2.x and left open for the native classes the COD3S Platform emits. Removing the
façades in 6.0.0 closes it for both, because there is now one family.

What is published
-----------------
**Effects.** A cod3s mode names its target's variables by their EXACT basename
(``{"f_fed_available_out": False}``, ``{"q_out_rate": 0.5}``), in four dicts:
``occ_effects`` and ``not_occ_effects`` (held), and their ``*_trans``
counterparts (one-shot). Every key resolving on the target is published,
whatever the ``behaviour``: the external behaviours apply the very same dicts
through a mirror automaton on the target, so what ends up written is the same.

**Conditions.** Only the forms whose reads are statically knowable: the cod3s
structured tree, whose leaves name an ``attr`` on an ``obj`` (the target when
omitted). A **callable** condition reads whatever its body reads and nothing can
be derived from it; a constant reads nothing. That residual is a mode
conditioned by a Python function, and it stays invisible to the loop analysis.
"""

import cod3s


def condition_reads(cond, comp_target):
    """Variable basenames one condition specification reads on a target.

    Parameters
    ----------
    cond : list, callable, bool or None
        A ``cod3s.ObjMode2S`` condition: a structured tree (nested lists whose
        leaves are ``{"attr", "ope", "value"[, "obj"]}`` mappings), a callable
        or a constant.
    comp_target : ObjFlow
        The component the condition is evaluated against.

    Returns
    -------
    set of str
        What the condition reads on ``comp_target``. A leaf naming another
        component says nothing about this target and is left out.
    """
    if not isinstance(cond, (list, tuple)):
        # A constant reads nothing; a callable reads what nothing can derive.
        return set()

    names = set()
    stack = [cond]

    while stack:
        node = stack.pop()

        if isinstance(node, (list, tuple)):
            stack.extend(node)
            continue

        if not isinstance(node, dict):
            continue

        attr = node.get("attr")
        obj = node.get("obj")

        if not isinstance(attr, str):
            continue

        if obj is None or obj is comp_target or obj == comp_target.name():
            names.add(attr)

    return names


def effect_basenames(mode, comp_target):
    """Basenames of the target variables ``mode`` writes, in declaration order.

    A key the target does not resolve is skipped rather than refused: the
    engine already refused it at construction, so a mode that got this far
    names only what exists -- or names a variable of ANOTHER target of a
    heterogeneous group, which says nothing about this one.
    """
    names = [v.basename() for v in comp_target.variables()]
    known = set(names)
    effects = []

    for attr_name in (
        "occ_effects",
        "not_occ_effects",
        "occ_effects_trans",
        "not_occ_effects_trans",
    ):
        for key in getattr(mode, attr_name, None) or {}:
            if key in known:
                basename = key
            else:
                var = getattr(comp_target, key, None)
                basename_fun = getattr(var, "basename", None)
                if not callable(basename_fun):
                    continue
                basename = basename_fun()

            if basename not in effects:
                effects.append(basename)

    return effects


def mode_signal_key(mode, target_name):
    """The ``mode_signals`` key of one (mode, target) pair.

    Prefixed so it can never collide with the name of a mode declared on the
    component, which ``add_atm2states`` uses as its key.
    """
    return f"{mode.name()}@{target_name}"


def publish_mode_signals(system):
    """Publish, on every muscadet target, what each standalone mode reads and writes.

    Idempotent: an entry is assigned, never accumulated, so a pre-run step
    retried after a refusal publishes the same thing again.

    Returns
    -------
    dict
        ``{key: entry}`` for every entry written, for the tests.
    """
    published = {}

    for mode in list(system.comp.values()):
        if not isinstance(mode, cod3s.ObjMode2S):
            continue

        for target_name in getattr(mode, "targets", None) or []:
            comp_target = system.comp.get(target_name)
            signals = getattr(comp_target, "mode_signals", None)

            if signals is None:
                continue

            reads = set()
            for attr_name in ("occ_cond", "not_occ_cond"):
                reads |= condition_reads(getattr(mode, attr_name, None), comp_target)

            entry = {
                "conditions": sorted(reads),
                "effects": effect_basenames(mode, comp_target),
            }
            key = mode_signal_key(mode, target_name)
            signals[key] = entry
            published[key] = entry

    return published
