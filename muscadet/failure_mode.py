"""Standalone failure modes, as thin façades over the cod3s mode engine.

``muscadet.ObjFailureMode`` and its two occurrence-law flavours used to be a
FORK of the ``cod3s.ObjFM`` family: the same template hooks, the same
common-cause combinatorics, the same ``trans_name_prefix`` mechanics, all
reimplemented on top of ``cod3s.PycComponent``. They are now subclasses of the
corresponding ``cod3s.ObjFM*``, which since ``ObjMode2S`` owns the
combinatorics, the parameter variables, the drop-inactive gate and the
automaton build. What is left here is only what is genuinely muscadet's:

**Effects named by FLOW, matched as a regular expression.** A cod3s mode names
the target's variables by their exact basename
(``{"is_ok_fed_available_out": False}``); a muscadet mode names its target's
FLOWS with a regex (``{"is_ok": False}``, ``{".*": 0.0}``) and the library
resolves each match to what the flow actually offers a mode -- the availability
variable of a discrete output, the derating variable of a continuous one. Both
spellings are load-bearing in existing models, so the two contracts coexist:
the muscadet classes keep the regex-on-flows one, ``cod3s.ObjFM*`` keeps the
exact-variable one.

**Deratings.** A continuous output carries no boolean availability gate, so a
mode reaches it through a derating variable it OWNS -- one per (automaton,
output) pair, composed by minimum at read time (R18, R20). A mode declared on
a component gets this from :mod:`muscadet.derating`; a standalone mode gets it
from :meth:`ObjFailureMode.resolve_effects_on` and
:meth:`ObjFailureMode.release_deratings_on` here, which is why the effects have
to be resolved once the declaring AUTOMATON is known and not once per mode: the
two automata a second-order mode builds over one target would otherwise clamp a
single variable and overwrite one another.

**The dict shorthand for a condition.** ``failure_cond={"c1": True}`` reads the
target's INPUT flows, where a cod3s structured condition names variables. The
two are recognised side by side.

Where the engine hooks in
-------------------------
The regex resolution needs the automaton's name (to key the deratings) and the
combination's targets. ``_build_fm_automaton`` -- the documented extension hook
of the ObjFM family -- is called once per built combination and carries the
name; the combination itself is recovered from that name through the same
``cc_comb_suffix`` helper the engine names it with. The declared effect dicts
are therefore kept AWAY from the engine (it is handed empty ones, and would
otherwise reject a flow name as an unknown variable) and restored onto the
public attributes once construction is over.
"""

import copy
import itertools
import re
import warnings

import cod3s
from cod3s.pycatshoo.fm_wiring import cc_comb_suffix

from .flow_continuous import NOMINAL_RATE, FlowContinuousOut

#: Constructor keywords ``cod3s.ObjFM`` accepts but whose effect resolution
#: this façade cannot honour: they route effects through the engine's
#: exact-variable-name path, which a muscadet flow pattern never matches. A
#: model needing them wants ``cod3s.ObjFM*`` directly rather than a silently
#: effect-less mode. Each entry maps the keyword to the value that leaves the
#: façade's own path untouched, and to why anything else is refused.
_UNSUPPORTED_KWARGS = {
    "behaviour": (
        "internal",
        "the external behaviours resolve their effects through the engine's "
        "exact-variable-name path, which a muscadet flow pattern does not "
        "match",
    ),
    "failure_effects_trans": (
        None,
        "trans-based (one-shot) effects are resolved by exact variable name",
    ),
    "repair_effects_trans": (
        None,
        "trans-based (one-shot) effects are resolved by exact variable name",
    ),
}


class ObjFailureMode(cod3s.ObjFM):
    """
    A component that models failure modes affecting multiple target components.

    Deprecated in favour of :class:`cod3s.ObjFM`, of which it is now a thin
    subclass: the common-cause combinatorics, the per-order parameter
    variables, the ``drop_inactive_automata`` gate and the automaton build all
    come from the engine, and the automaton, state and transition names are
    the engine's. What this class still owns is the muscadet spelling of
    effects and conditions (see the module docstring).

    This class creates automata-based failure modes that can affect one or more
    target components simultaneously. It supports different orders of failure
    (affecting 1, 2, or more components at once) and allows customization of
    failure and repair conditions, parameters, and effects.

    The failure mode creates all possible combinations of target components up
    to the specified order and generates corresponding automata with failure
    and repair transitions. Each automaton is named using customizable prefixes
    to distinguish between different target combinations.

    Attributes
    ----------
    fm_name : str
        The base name of the failure mode
    targets : list[str]
        List of target component names that can be affected by this failure mode
    target_name : str
        Factorized name representing all targets (auto-generated if not provided)
    failure_state : str
        Name of the failure state in the automaton (default: "occ")
    repair_state : str
        Name of the repair state in the automaton (default: "rep")
    failure_effects : dict
        Dictionary mapping flow name PATTERNS to their values when failure
        occurs. A pattern is a regular expression anchored on the whole flow
        name, matched against the target's OUTPUT flows.
    repair_effects : dict
        Same, applied when repair occurs
    failure_param_name : list[str]
        Names of the failure parameters (e.g., ["lambda"] for exponential)
    repair_param_name : list[str]
        Names of the repair parameters (e.g., ["mu"] for exponential)
    trans_name_prefix : str
        Template string for generating transition/automaton name suffixes
    trans_name_prefix_fun : callable, optional
        Custom function for generating transition/automaton name suffixes

    Parameters
    ----------
    fm_name : str
        The name of the failure mode
    targets : str or list[str]
        Target component(s) that can be affected by this failure mode
    target_name : str, optional
        Custom name for the target combination. If None, auto-generated from
        targets
    failure_state : str, optional
        Name of the failure state (default: "occ")
    failure_cond : bool, dict, list or callable, optional
        Condition that must be met for failure to occur (default: True). The
        muscadet dict shorthand ``{input flow name: expected value}`` requires
        every named INPUT flow of every target of the combination to be fed
        with that value; cod3s structured lists and callables are accepted too.
    failure_effects : dict, optional
        Effects applied when failure occurs (default: {})
    failure_param_name : str or list[str], optional
        Names of failure parameters (default: [])
    failure_param : list, optional
        Values of failure parameters (default: [])
    repair_state : str, optional
        Name of the repair state (default: "rep")
    repair_cond : bool, dict, list or callable, optional
        Condition that must be met for repair to occur (default: True)
    repair_effects : dict, optional
        Effects applied when repair occurs (default: {})
    repair_param_name : str or list[str], optional
        Names of repair parameters (default: [])
    repair_param : list, optional
        Values of repair parameters (default: [])
    param_name_order_prefix : str, optional
        Template for parameter name suffixes (default: "__{order}_o_{order_max}")
    trans_name_prefix : str, optional
        Template for transition/automaton name suffixes
        (default: "__cc_{target_comb_u}")
        Available placeholders: {target_comb}, {target_binary}, {target_comb_u},
        {order}, {order_max}
    trans_name_prefix_fun : callable, optional
        Custom function to generate transition/automaton name suffixes. Takes
        keyword arguments: target_set_idx, target_comb, target_binary,
        target_comb_u, order, order_max
    drop_inactive_automata : bool, optional
        Whether to skip creating automata with inactive occurrence laws
        (default: True)
    step : optional
        Step parameter for automaton transitions

    Methods
    -------
    resolve_effects_on(comp_target, effects, mode_key, direction)
        Resolves one direction's effects on one target into effect records
    release_deratings_on(comp_target, mode_key, *effect_lists)
        Gives every derating the automaton owns on a target a release
    get_failure_cond(target_comps, failure_param)
        Creates a failure condition function for the given target components
    get_repair_cond(target_comps, repair_param)
        Creates a repair condition function for the given target components
    set_default_failure_param_name()
        Sets default failure parameter names (to be overridden in subclasses)
    set_default_repair_param_name()
        Sets default repair parameter names (to be overridden in subclasses)
    _factorize_target_names(targets, ...)
        Static method to create factorized names from target lists (inherited
        from the engine)

    Examples
    --------
    Basic failure mode with default naming:

    >>> fm = ObjFailureMode(
    ...     fm_name="common_cause",
    ...     targets=["pump1", "pump2"],
    ...     failure_effects={"flow": False},
    ...     repair_effects={"flow": True}
    ... )
    # Creates automata: "common_cause__cc_1_2", "common_cause__cc_1",
    # "common_cause__cc_2"

    Using custom trans_name_prefix with binary representation:

    >>> fm = ObjFailureMode(
    ...     fm_name="failure",
    ...     targets=["comp1", "comp2", "comp3"],
    ...     trans_name_prefix="__bin_{target_binary}",
    ...     failure_effects={"output": False}
    ... )
    # Creates automata like: "failure__bin_110", "failure__bin_101", etc.
    """

    def __init__(
        self,
        fm_name,
        targets=[],
        target_name=None,
        failure_state="occ",
        failure_cond=True,
        failure_effects={},
        failure_param_name=[],
        failure_param=[],
        repair_state="rep",
        repair_cond=True,
        repair_effects={},
        repair_param_name=[],
        repair_param=[],
        param_name_order_prefix="__{order}_o_{order_max}",
        trans_name_prefix="__cc_{target_comb_u}",
        trans_name_prefix_fun=None,
        drop_inactive_automata=True,
        step=None,
        **kwargs,
    ):
        # Deprecation: muscadet.ObjFailureMode and its subclasses
        # (ObjFailureModeExp, ObjFailureModeDelay) are historical wrappers
        # around cod3s.ObjFM. The cod3s lib now hosts the canonical
        # implementation; new code should use ``cod3s.ObjFM``,
        # ``cod3s.ObjFMExp``, ``cod3s.ObjFMDelay`` directly. This warning
        # fires once per process at instantiation.
        warnings.warn(
            (
                f"{type(self).__name__} is deprecated; use "
                f"``cod3s.{type(self).__name__.replace('ObjFailureMode', 'ObjFM')}`` "
                "instead. The muscadet wrapper will be removed in a future release."
            ),
            DeprecationWarning,
            stacklevel=2,
        )

        for key, (inert, why) in _UNSUPPORTED_KWARGS.items():
            value = kwargs.get(key, inert)
            if value != inert and value:
                raise ValueError(
                    f"{type(self).__name__} {fm_name!r}: {key}={value!r} is "
                    f"not supported by the muscadet façade -- {why}. Declare "
                    f"the mode with the matching ``cod3s.ObjFM*`` class, whose "
                    f"effects name variables by their exact basename."
                )

        # The declared effects name FLOWS with a regex; the engine resolves
        # effect keys as exact variable basenames and would refuse every one
        # of them. Keep them out of its reach for the whole build, resolve
        # them per combination in ``_build_fm_automaton``, and restore them on
        # the public attributes afterwards so ``fm.failure_effects`` reads
        # back what the model declared.
        self._flow_effects = {
            "occ": copy.deepcopy(failure_effects),
            "not_occ": copy.deepcopy(repair_effects),
        }
        self._cc_target_index = None

        super().__init__(
            fm_name=fm_name,
            targets=targets,
            target_name=target_name,
            failure_state=failure_state,
            failure_cond=failure_cond,
            failure_effects={},
            failure_param_name=failure_param_name,
            failure_param=failure_param,
            repair_state=repair_state,
            repair_cond=repair_cond,
            repair_effects={},
            repair_param_name=repair_param_name,
            repair_param=repair_param,
            param_name_order_prefix=param_name_order_prefix,
            trans_name_prefix=trans_name_prefix,
            trans_name_prefix_fun=trans_name_prefix_fun,
            drop_inactive_automata=drop_inactive_automata,
            step=step,
            **kwargs,
        )

        # ``failure_effects`` / ``repair_effects`` are legacy views on the
        # engine's ``occ_effects`` / ``not_occ_effects`` storage.
        self.occ_effects = self._flow_effects["occ"]
        self.not_occ_effects = self._flow_effects["not_occ"]

    # ------------------------------------------------------------------
    # Effects: named by flow, matched as a regex, derating-aware.
    # ------------------------------------------------------------------

    def _combination_targets(self, aut_name):
        """
        Returns the target components the automaton ``aut_name`` bears on.

        The engine enumerates the combinations and names each automaton
        ``{mode_name}{cc_suffix}``; the same enumeration, through the same
        ``cc_comb_suffix`` helper and the same ``trans_name_prefix`` /
        ``trans_name_prefix_fun``, therefore inverts the name back to its
        combination -- which is what the effect resolution needs and what the
        build hook does not carry.
        """
        if self._cc_target_index is None:
            order_max = len(self.targets)
            index = {}
            for order in range(1, order_max + 1):
                for target_set_idx in itertools.combinations(range(order_max), order):
                    suffix = cc_comb_suffix(
                        target_set_idx,
                        order_max,
                        trans_name_prefix=self.trans_name_prefix,
                        trans_name_prefix_fun=self.trans_name_prefix_fun,
                    )
                    index[f"{self.fm_name}{suffix}"] = target_set_idx
            self._cc_target_index = index

        if aut_name not in self._cc_target_index:
            raise ValueError(
                f"[{self.name()}] Automaton {aut_name!r} does not match any "
                f"target combination of this failure mode. A "
                f"``trans_name_prefix_fun`` naming two combinations alike "
                f"would do this."
            )

        return [
            self.system().component(self.targets[idx])
            for idx in self._cc_target_index[aut_name]
        ]

    def _build_fm_automaton(
        self,
        aut_name,
        repair_state_name,
        failure_state_name,
        failure_cond,
        repair_cond,
        failure_var_params,
        repair_var_params,
        failure_effects,
        repair_effects,
        repair_law,
        failure_effects_trans=None,
        repair_effects_trans=None,
    ):
        """
        Resolves this combination's effects, then lets the engine build.

        The documented extension hook of the ObjFM family, called once per
        built combination. The ``failure_effects`` / ``repair_effects``
        records the engine hands over are empty by construction (it was given
        empty dicts); what this substitutes is the muscadet resolution of the
        DECLARED patterns, which needs both the combination's targets and the
        automaton's name -- the latter being the key a derating variable is
        allocated under, unique per automaton so that the two automata a
        second-order mode builds over one target never share one.
        """
        mode_key = f"{self.basename()}__{aut_name}"
        target_comps = self._combination_targets(aut_name)

        # Kept per target rather than flattened straight away: what a mode
        # clamps ON ONE TARGET is what that target has to publish as this
        # mode's effects (see ``register_mode_signals_on``), and the flat list
        # the engine wants cannot be taken apart again.
        failure_by_target = {}
        repair_by_target = {}

        for comp_target_cur in target_comps:
            failure_by_target[id(comp_target_cur)] = self.resolve_effects_on(
                comp_target_cur, self._flow_effects["occ"], mode_key, "Failure"
            )

        for comp_target_cur in target_comps:
            repair_by_target[id(comp_target_cur)] = self.resolve_effects_on(
                comp_target_cur, self._flow_effects["not_occ"], mode_key, "Repair"
            )

        for comp_target_cur in target_comps:
            key = id(comp_target_cur)
            self.release_deratings_on(
                comp_target_cur,
                mode_key,
                failure_by_target[key],
                repair_by_target[key],
            )
            # After the releases, so the published effects are every variable
            # this automaton writes on that target and not only the ones the
            # declaration named.
            self.register_mode_signals_on(
                comp_target_cur,
                mode_key,
                failure_by_target[key],
                repair_by_target[key],
            )

        failure_effects_cur = [
            record
            for comp_target_cur in target_comps
            for record in failure_by_target[id(comp_target_cur)]
        ]
        repair_effects_cur = [
            record
            for comp_target_cur in target_comps
            for record in repair_by_target[id(comp_target_cur)]
        ]

        return super()._build_fm_automaton(
            aut_name=aut_name,
            repair_state_name=repair_state_name,
            failure_state_name=failure_state_name,
            failure_cond=failure_cond,
            repair_cond=repair_cond,
            failure_var_params=failure_var_params,
            repair_var_params=repair_var_params,
            failure_effects=failure_effects_cur,
            repair_effects=repair_effects_cur,
            repair_law=repair_law,
            failure_effects_trans=failure_effects_trans,
            repair_effects_trans=repair_effects_trans,
        )

    def resolve_effects_on(self, comp_target, effects, mode_key, direction):
        """
        Resolves one direction's effects on one target into effect records.

        Branches on the flow FAMILY, because the two do not carry the same
        thing (R19). A discrete output is gated by its availability variable,
        as it has been since 1.x. A continuous output declares no availability
        at all -- it carries a rate -- so the effect is routed through
        :meth:`ObjFlow.add_derating`, which is public precisely so that a mode
        declared OUTSIDE the component can allocate the variable it needs and
        target it. Two modes derating one output then compose by minimum (R18,
        R20) instead of overwriting one another, exactly as they do for a mode
        declared inside the component.

        The value is the RATE the mode leaves on a continuous output, so the
        boolean spelling of a 1.x effect keeps meaning what it did: ``False``
        is a rate of 0 -- the output stops producing -- and ``True`` is the
        nominal rate.

        Parameters
        ----------
        comp_target : ObjFlow
            The component the effects bear on.
        effects : dict
            ``{flow name pattern: value}``, as declared on the mode.
        mode_key : str
            Name the derating variables are allocated under, unique per
            automaton.
        direction : str
            ``"Failure"`` or ``"Repair"``, which the error messages name.

        Returns
        -------
        list of dict
            ``{"var", "value"}`` records, in declaration order.

        Raises
        ------
        ValueError
            If a pattern matches no output flow of ``comp_target``, or if it
            matches an output carrying neither an availability gate nor a
            rate.
        """
        effects_cur = []

        for flow_name_pat, val in effects.items():
            if len(flow_name_pat) == 0:
                continue
            fo_found = False
            for fo_name, fo in comp_target.flows_out.items():
                if not re.search(f"^{flow_name_pat}$", fo_name):
                    continue
                fo_found = True
                if isinstance(fo, FlowContinuousOut):
                    effects_cur.append(
                        {
                            "var": comp_target.add_derating(mode_key, fo_name),
                            "value": float(val),
                        }
                    )
                elif fo.var_fed_available is None:
                    raise ValueError(
                        f"[Component {str(comp_target)}]\n"
                        f"[{comp_target.name()}: {direction} effects of mode "
                        f"{self.fm_name}] Pattern {flow_name_pat} matches flow "
                        f"out {fo_name}, which declares no availability "
                        f"variable to clamp"
                    )
                else:
                    effects_cur.append({"var": fo.var_fed_available, "value": val})
            if not fo_found:
                raise ValueError(
                    f"[Component {str(comp_target)}]\n[{comp_target.name()}: "
                    f"{direction} effects of mode {self.fm_name}] Pattern "
                    f"{flow_name_pat} does not match any flow out"
                )

        return effects_cur

    def release_deratings_on(self, comp_target, mode_key, *effect_lists):
        """
        Gives every derating ``mode_key`` owns on ``comp_target`` a release.

        A derating variable has NO per-step reset, unlike the boolean
        availability gate: a reset value composing with a minimum does not
        exist. So the direction that does not name an output has to hand its
        rate back explicitly, or a repaired mode would leave its own
        degradation standing for the rest of the sequence. The counterpart,
        for a mode declared OUTSIDE the component, of
        :meth:`ObjFlow.release_deratings`.

        Parameters
        ----------
        comp_target : ObjFlow
            The component holding the derated outputs.
        mode_key : str
            The declaring mode, as ``resolve_effects_on`` allocated it.
        *effect_lists : list
            The effect-record lists of the mode's two states, MUTATED in
            place. Each is completed against the variables the mode owns and
            not against the other, so a mode declaring a degraded return on
            one direction keeps it.
        """
        derating_vars = comp_target.derating_vars_of(mode_key)

        for effects_cur in effect_lists:
            clamped = {record["var"].basename() for record in effects_cur}
            effects_cur += [
                {"var": var, "value": NOMINAL_RATE}
                for basename, var in derating_vars.items()
                if basename not in clamped
            ]

    # ------------------------------------------------------------------
    # What the mode reads and writes, published on the TARGET (R-19).
    # ------------------------------------------------------------------

    def register_mode_signals_on(self, comp_target, mode_key, *effect_lists):
        """
        Publish this automaton's reads and writes on ``comp_target``.

        ``ObjFlow.mode_signals`` is what :mod:`muscadet.ordering` consults to
        answer "can this component's production depend on that discrete input?"
        (``gates_production_on``) and "does a signal arriving here leave again?"
        (``signal_driven_outputs``). It was written by ``ObjFlow.add_atm2states``
        and by nothing else, so a mode declared OUTSIDE the component -- every
        standalone failure mode -- was invisible to both, and an instantaneous
        loop closed through one escaped the first-run check entirely: the model
        built, the gated production settled on whatever the topological order
        happened to evaluate first, and no diagnostic was ever produced.

        Registered per (automaton, target) under the same ``mode_key`` the
        deratings use, so the two automata a second-order mode builds over one
        target stay distinct here as they do there.

        **What a condition is read through.** Only the forms whose reads are
        statically knowable are published: the muscadet dict shorthand
        (``{input flow name: value}``, which reads ``{flow}_fed_in`` on every
        target) and the cod3s structured tree (whose operands name an ``attr``
        on an ``obj``). A **callable** condition reads whatever its body reads
        and nothing can be derived from it; a constant reads nothing. So the
        residual is a mode conditioned by a Python function, which stays
        invisible to the loop analysis -- narrower than the previous residual,
        which was every standalone mode.

        Parameters
        ----------
        comp_target : ObjFlow
            The component the automaton bears on.
        mode_key : str
            The declaring automaton, as ``resolve_effects_on`` allocated it.
        *effect_lists : list
            Effect records ``{"var", "value"}`` clamped ON THIS TARGET, in
            both directions, releases included.
        """
        signals = getattr(comp_target, "mode_signals", None)

        if signals is None:
            return None

        effects = []
        for effects_cur in effect_lists:
            for record in effects_cur:
                basename = record["var"].basename()
                if basename not in effects:
                    effects.append(basename)

        entry = {
            "conditions": sorted(self.condition_signals_on(comp_target)),
            "effects": effects,
        }
        signals[mode_key] = entry

        return entry

    def condition_signals_on(self, comp_target):
        """Variable basenames this mode's two conditions READ on one target."""
        reads = set()

        for attr_name in ("occ_cond", "not_occ_cond"):
            reads |= self._condition_reads(getattr(self, attr_name, None), comp_target)

        return reads

    @staticmethod
    def _condition_reads(cond, comp_target):
        """Variable basenames one condition specification reads on a target."""
        if isinstance(cond, dict):
            # The muscadet shorthand: every named INPUT flow of the target.
            flows_in = getattr(comp_target, "flows_in", None) or {}
            names = set()

            for flow_name in cond:
                flow = flows_in.get(flow_name)
                var = None if flow is None else getattr(flow, "var_fed", None)
                if var is not None:
                    names.add(var.basename())

            return names

        if isinstance(cond, list):
            # The cod3s structured tree: nested lists whose leaves carry an
            # ``attr`` and, optionally, the ``obj`` it belongs to. A leaf naming
            # another component says nothing about this target.
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

        # A constant reads nothing; a callable reads what nothing can derive.
        return set()

    # ------------------------------------------------------------------
    # Conditions: the muscadet dict shorthand over the target's INPUT flows.
    # ------------------------------------------------------------------

    def _compile_direction_cond(self, direction, target_comps):
        """
        Compiles one direction's condition, dict shorthand included.

        ``{input flow name: expected value}`` requires every named input of
        every target of the combination to be fed with that value. Read late,
        on the attribute, so that rebinding ``failure_cond`` after
        construction still takes effect -- the engine does the same for its
        constants. Everything else (structured trees, callables, constants) is
        the engine's.
        """
        attr_name = "occ_cond" if direction == "occ" else "not_occ_cond"

        if isinstance(getattr(self, attr_name), dict):

            def flow_cond_fun():
                return all(
                    [
                        comp.flows_in[flow].var_fed.value() == flow_value
                        for flow, flow_value in getattr(self, attr_name).items()
                        for comp in target_comps
                    ]
                )

            return flow_cond_fun

        return super()._compile_direction_cond(direction, target_comps)


class ObjFailureModeExp(ObjFailureMode, cod3s.ObjFMExp):
    """
    Exponential failure mode: ``lambda`` to fail, ``mu`` to repair.

    Deprecated in favour of :class:`cod3s.ObjFMExp`, whose occurrence-law
    hooks it now inherits verbatim; what it adds is what
    :class:`ObjFailureMode` adds.
    """


class ObjFailureModeDelay(ObjFailureMode, cod3s.ObjFMDelay):
    """
    Delay failure mode: ``ttf`` to fail, ``ttr`` to repair.

    Deprecated in favour of :class:`cod3s.ObjFMDelay`, whose occurrence-law
    hooks it now inherits verbatim; what it adds is what
    :class:`ObjFailureMode` adds.
    """
