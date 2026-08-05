"""
Muscadet Object Flow Module

This module provides the core classes for modeling components in discrete stochastic flow systems.
It defines the fundamental building blocks for creating complex system models with flows, automata,
and failure modes.

Main Classes
------------
ObjFlow : cod3s.PycComponent
    The primary component class for modeling flow-based systems. Supports input/output flows,
    automata, and failure modes with rich visualization capabilities.

ObjFailureMode : cod3s.PycComponent
    Base class for modeling failure modes that can affect multiple target components
    simultaneously. Supports different orders of failure and customizable conditions.

ObjFailureModeExp : ObjFailureMode
    Exponential failure mode implementation with lambda/mu parameters for failure
    and repair rates following exponential distributions.

ObjFailureModeDelay : ObjFailureMode
    Delay-based failure mode implementation with fixed time-to-failure and
    time-to-repair parameters.

FailureModeExp : cod3s.ObjCOD3S
    Pydantic model for exponential failure modes providing structured representation
    with validation, serialization, and colored visualization capabilities.

Key Features
------------
- Flow-based component modeling with input/output flows
- Automata-based state management and transitions
- Multiple failure mode types (exponential, delay-based)
- Rich colored console output for debugging and visualization
- Pydantic integration for data validation and serialization
- Support for complex production conditions and flow logic
- Extensible architecture for custom component types

Usage Example
-------------
>>> import muscadet
>>> system = muscadet.System("MySystem")
>>>
>>> # Create a custom component
>>> class Pump(muscadet.ObjFlow):
...     def add_flows(self, **kwargs):
...         super().add_flows(**kwargs)
...         self.add_flow_in(name="power", logic="and")
...         self.add_flow_out(name="water", var_prod_default=True)
...
>>> # Add component to system
>>> system.add_component(name="pump1", cls=Pump)
>>>
>>> # Add failure mode
>>> system.add_component(
...     name="pump_failure",
...     cls=muscadet.ObjFailureModeExp,
...     fm_name="failure",
...     targets=["pump1"],
...     failure_param=[0.001],  # lambda
...     repair_param=[0.1],     # mu
...     failure_effects={"water": False}
... )

Dependencies
------------
- Pycatshoo: Backend simulation engine
- cod3s: Component-oriented discrete stochastic systems framework
- pydantic: Data validation and serialization
- colored: Terminal color output
- typing: Type hints support

Notes
-----
This module follows the project's coding conventions including:
- Snake_case for variables and functions
- CamelCase for classes
- Comprehensive docstrings with parameter descriptions
- Type hints for all public methods
- Colored output for enhanced user experience
"""

import Pycatshoo as pyc
from .flow import (
    FlowDiscreteIn,
    FlowDiscreteOut,
    FlowDiscreteOutOnTrigger,
    FlowDiscreteOutTempo,
    FlowIn,
    FlowOut,
    FlowOutOnTrigger,
    FlowOutTempo,
)
from .flow_continuous import (
    NOMINAL_RATE,
    UNBOUNDED,
    FlowContinuous,
    FlowContinuousIn,
    FlowContinuousOut,
)
from .rules import (
    COMPARISON_OPERATORS,
    UNCONSTRAINED_SCALE,
    Rule,
    RuleMode,
    RuleSet,
    rule_consumption,
    rule_production,
    rule_scale,
)
from .capacity import (
    Capacity,
    MeasurementIn,
    allocate_capacity_equation_order,
)
import cod3s
import math
import re
import warnings
import copy
import itertools
from colored import fg, attr
import typing
import pydantic


class ObjFlow(cod3s.PycComponent):
    """
    A class to represent a component in a discrete stochastic flow system.

    Attributes
    ----------
    name : str
        The name of the component.
    label : str, optional
        The label of the component.
    description : str, optional
        The description of the component.
    metadata : dict, optional
        Metadata associated with the component.

    Methods
    -------
    is_connected_to(target, flow):
        Checks if the component is connected to a target component via a specified flow.
    add_flows(**kwargs):
        Adds flows to the component. To be overloaded by subclasses.
    add_flow_in(**params):
        Adds an input flow to the component.
    add_flow_io(**params):
        Adds an input/output flow to the component.
    add_flow_out(**params):
        Adds an output flow to the component.
    add_flow_out_tempo(**params):
        Adds a temporized output flow to the component.
    add_flow_out_on_trigger(**params):
        Adds an output flow that is triggered by an input flow.
    add_flow_continuous_in(**params):
        Adds a continuous (real-valued) input flow to the component.
    add_flow_continuous_out(**params):
        Adds a continuous (real-valued) output flow to the component.
    add_rules(name, rules):
        Declares an ordered set of transformation rules on the component.
    add_capacity(name, flow/flows, capacity, side, content_init):
        Declares a volume held over one or more continuous flows.
    add_measurement_in(name):
        Declares the importing side of a capacity's measurement link.
    set_flows(**kwargs):
        Sets up the flows for the component.
    pat_to_var_value(*pat_value_list):
        Converts pattern-value pairs to variable-value pairs.
    add_automaton_flow(aut):
        Adds an automaton to the component.
    compute_effects_tuples(effects_str=None):
        Computes the effects tuples from a string.
    add_derating(mode_name, flow_name):
        Allocates the derating variable a mode owns on a continuous output.
    derating_vars_of(mode_name):
        Returns the derating variables a mode owns, keyed by basename.
    resolve_mode_effects(mode_name, effects):
        Resolves a mode's effects, rewriting deratings onto its own variables.
    add_atm2states(name, st1="absent", st2="present", init_st2=False, cond_occ_12=True, occ_law_12={"cls": "delay", "time": 0}, occ_interruptible_12=True, effects_12=[], cond_occ_21=True, occ_law_21={"cls": "delay", "time": 0}, occ_interruptible_21=True, effects_21=[]):
        Adds a two-state automaton to the component.
    add_exp_failure_mode(name, failure_cond=True, failure_rate=0, failure_effects=[], failure_param_name="lambda", repair_cond=True, repair_rate=0, repair_effects=[], repair_param_name="mu"):
        Adds an exponential failure mode to the component.
    add_delay_failure_mode(name, failure_cond=True, failure_time=0, failure_effects=[], failure_param_name="ttf", repair_cond=True, repair_time=0, repair_effects=[], repair_param_name="ttr"):
        Adds a delay failure mode to the component.
    """

    def __init__(
        self,
        name,
        label=None,
        description=None,
        partial_init=False,
        create_default_out_automata=False,
        metadata={},
        **kwargs,
    ):

        super().__init__(
            name, label=label, description=description, metadata=metadata, **kwargs
        )

        self.flows_in = {}
        self.flows_out = {}

        # Transformation rule sets, keyed by rule set name, in declaration
        # order. Declared with add_rules; consumed by the evaluation and the
        # guard-compilation layers.
        self.rule_sets = {}

        # Capacities, keyed by capacity name, in declaration order. Declared
        # with add_capacity, INDEPENDENTLY of the rules (KD14).
        self.capacities = {}

        # Measurement links this component imports, keyed by channel name.
        self.measurements_in = {}

        # True once ``compute_capacities`` was registered as a PDMP equation
        # method for this component: one registration covers every capacity.
        self._capacity_equation_registered = False

        self.params = {}
        self.has_default_out_automata = create_default_out_automata

        if partial_init:
            # In this cas you need to explicitly call add_flow and set_flows to
            # Create complete the object creation
            pass
        else:
            kwargs.update(metadata=metadata)

            self.add_flows(**kwargs)

            self.set_flows(**kwargs)

    def repr__class_name_fmt(self) -> str:
        """Return the color formatting for class name. Can be overridden in subclasses."""
        return f"{attr('bold')}{fg('medium_orchid_1a')}"

    # def get_component_name_color(self) -> str:
    #     """Return the color formatting for component name. Can be overridden in subclasses."""
    #     return f"{fg('white')}"

    def str__flows_in_header_fmt(self) -> str:
        """Return the color formatting for 'Input Flows' label. Can be overridden in subclasses."""
        return f"{attr('bold')}{fg('orange_1')}"

    def str__flows_out_header_fmt(self) -> str:
        """Return the color formatting for 'Output Flows' label. Can be overridden in subclasses."""
        return f"{attr('bold')}{fg('steel_blue_1a')}"

    def __repr__(self) -> str:
        """Return a concise representation showing flow counts."""
        # Count flows by type for flows_in
        flows_in_counts = {}
        for flow in self.flows_in.values():
            flow_type = flow.__class__.__name__
            flows_in_counts[flow_type] = flows_in_counts.get(flow_type, 0) + 1

        # Count flows by type for flows_out
        flows_out_counts = {}
        for flow in self.flows_out.values():
            flow_type = flow.__class__.__name__
            flows_out_counts[flow_type] = flows_out_counts.get(flow_type, 0) + 1

        # Format flows_in counts with colors
        flows_in_parts = []
        for flow_type, count in flows_in_counts.items():
            # Get the flow class and use its formatting method
            flow_class = globals().get(flow_type)
            if flow_class and hasattr(flow_class, "get_format_class_name"):
                color = flow_class.get_format_class_name()
            else:
                color = f"{fg('white')}"
            flows_in_parts.append(f"{color}{count} {flow_type}{attr('reset')}")

        # Format flows_out counts with colors
        flows_out_parts = []
        for flow_type, count in flows_out_counts.items():
            # Get the flow class and use its formatting method
            flow_class = globals().get(flow_type)
            if flow_class and hasattr(flow_class, "get_format_class_name"):
                color = flow_class.get_format_class_name()
            else:
                color = f"{fg('white')}"
            flows_out_parts.append(f"{color}{count} {flow_type}{attr('reset')}")

        # Build the final representation
        flows_in_str = ", ".join(flows_in_parts) if flows_in_parts else "0"
        flows_out_str = ", ".join(flows_out_parts) if flows_out_parts else "0"

        return (
            f"{self.repr__class_name()} {self.repr__instance_name()}: "
            f"[in: {flows_in_str}, out: {flows_out_str}]"
        )

    def __str__(self) -> str:
        """Return a detailed representation showing all flows."""
        lines = [f"{self.str__class_name()} {self.str__instance_name()}"]

        # Add input flows first
        lines.append(f"{self.str__flows_in_header_fmt()}Input Flows:{attr('reset')}")
        if self.flows_in:
            for flow in self.flows_in.values():
                flow_lines = repr(flow).split("\n")
                lines.extend([f"  {line}" for line in flow_lines])

        # Add output flows
        lines.append(f"{self.str__flows_out_header_fmt()}Output Flows:{attr('reset')}")
        if self.flows_out:
            for flow in self.flows_out.values():
                flow_lines = repr(flow).split("\n")
                lines.extend([f"  {line}" for line in flow_lines])

        lines.append(self.str__cnct())

        # cnct_info = self.get_cnct_info()

        # # Add connection information if there are any connections
        # if cnct_info:
        #     lines.append(f"{attr('bold')}{fg('cyan')}Connections:{attr('reset')}")
        #     for mb_name, info in cnct_info.items():
        #         count = info.get("count", 0)
        #         targets = info.get("targets", [])

        #         if count > 0:
        #             lines.append(f"  {fg('white')}{mb_name}{attr('reset')}")
        #             for target in targets:
        #                 lines.append(
        #                     f"    ⟷  {fg('wheat_1')}{target['obj']}{attr('reset')}.{fg('white')}{target['cnct']}{attr('reset')}"
        #                 )
        #     else:
        #         lines.append(f"  {fg('white')}{mb_name}{attr('reset')}: no connection")

        return "\n".join(lines)

    def is_connected_to(self, target, flow):
        """
        Checks if the component is connected to a target component via a specified flow.

        Parameters
        ----------
        target : str
            The name of the target component.
        flow : str
            The name of the flow.

        Returns
        -------
        bool
            True if the component is connected to the target via the specified flow, False otherwise.
        """

        msg_box_out = self.messageBox(f"{flow}_out")

        for cnx in range(msg_box_out.cnctCount()):

            comp_target = msg_box_out.cnct(cnx).parent()
            if target == comp_target.basename():
                return True

        return False

    # def report_status(self):
    #     sys = self.system()
    #     comp_status = []
    #     comp_status.append(f"{self.name} at t={sys.currentTime()}")

    #     for flow_name, flow in self.flow_fed.items():
    #         comp_status.append(f"Flow {flow_name} fed = {flow.value()}")

    #     comp_status_str = "\n".join(comp_status)
    #     return comp_status_str

    def add_flows(self, **kwargs):
        """
        Adds flows to the component. To be overloaded by subclasses.

        Parameters
        ----------
        **kwargs : dict
            Additional parameters for adding flows.
        """
        # TO BE OVERLOADED
        pass

    # def add_flow_in(self, flow_specs):
    #     """
    #     Adds an input flow to the component.

    #     Parameters
    #     ----------
    #     **params : dict
    #         Parameters for the input flow.
    #     """
    #     if isinstance(flow_specs, FlowIn):
    #         flow_name = flow_specs.name
    #     else:
    #         flow_name = flow_specs.get("name")
    #     if not (flow_name in self.flows_in):
    #         self.flows_in[flow_name] = (
    #             flow_specs if isinstance(flow_specs, FlowIn) else FlowIn(**flow_specs)
    #         )
    #     else:
    #         raise ValueError(f"Input flow {flow_name} already exists")

    def postprocess_flow_specs(self, flow_specs):
        """
        Processes and prepares flow specifications, particularly handling production conditions.

        This method is crucial for converting user-friendly flow condition specifications into
        the internal format required by the simulation engine. It performs several key transformations:

        1. Converts string-based flow references to actual flow objects
        2. Normalizes condition structures into conjunctive normal form (CNF)
        3. Validates that referenced flows exist in the component
        4. Processes occurrence distribution specifications

        The production condition format follows this logic:
        - Single string: Simple condition on one flow
        - List of strings: Disjunctive (OR) condition
        - List of lists: Conjunctive normal form [(A OR B) AND (C OR D)]

        Parameters
        ----------
        flow_specs : dict
            Flow specifications dictionary containing parameters for the flow.
            Key parameters processed:
            - var_prod_cond: Production condition specification
            - occ_enable_flow: Occurrence distribution for flow enabling

        Returns
        -------
        dict
            A deep copy of the input flow_specs with processed parameters.
            The var_prod_cond is converted into a normalized format where:
            - Outer list represents AND conditions (conjunctive)
            - Inner lists represent OR conditions (disjunctive)
            - Each condition references the actual flow object instead of its name

        Raises
        ------
        ValueError
            If referenced flows don't exist or if condition format is invalid

        Examples
        --------
        >>> # Single condition
        >>> specs = {"var_prod_cond": "flow1"}
        >>> # Becomes: [["flow1_object"]]

        >>> # OR condition
        >>> specs = {"var_prod_cond": ["flow1", "flow2"]}
        >>> # Becomes: [["flow1_object", "flow2_object"]]

        >>> # AND of ORs condition
        >>> specs = {"var_prod_cond": [["flow1", "flow2"], ["flow3"]]}
        >>> # Becomes: [["flow1_object", "flow2_object"], ["flow3_object"]]

        >>> # A comparison against a continuous quantity (R22)
        >>> specs = {"var_prod_cond": [{"name": "level", "op": ">=", "value": 10}]}
        >>> # Becomes: [["level_object"]] plus the aligned comparison matrix
        """
        flow_specs = copy.deepcopy(flow_specs)

        # Postprocess : var_prod_cond
        if var_prod_cond := flow_specs.get("var_prod_cond"):

            def _resolve_operand(op):
                # A production-condition operand is either a plain string flow
                # name (non-negated, input-first -- the historical form) or a
                # mapping ``{"name": str, "negate"?: bool, "port"?: "in"|"out",
                # "op"?: str, "value"?: float}`` -- the very vocabulary a rule
                # guard operand uses (muscadet.rules.RuleOperand), so ONE
                # comparison syntax serves both directions of the
                # discrete/continuous interoperation.
                # ``port`` disambiguates a name carried by BOTH an input and an
                # output flow of this component: "in"/"out" force that side,
                # absent keeps the historical input-first resolution.
                # ``op`` / ``value`` make the operand a COMPARISON of the
                # quantity that name carries against a threshold (R22) instead
                # of a read of its boolean state.
                # Returns the ``(source_object, negate_bool, compare_or_None)``
                # triple (``port`` only selects which object, it does not
                # survive into the matrices).
                if isinstance(op, str):
                    name, negate, port = op, False, None
                    compare_op, compare_value = None, None
                elif isinstance(op, dict):
                    name = op.get("name")
                    negate = bool(op.get("negate", False))
                    port = op.get("port")
                    compare_op = op.get("op")
                    compare_value = op.get("value")
                    if not isinstance(name, str):
                        raise ValueError(
                            f"Object {self.name()}: production condition operand mapping must carry a string 'name' : {op}"
                        )
                    if port not in (None, "in", "out"):
                        raise ValueError(
                            f"Object {self.name()}: production condition operand 'port' must be 'in' or 'out', got {port!r}"
                        )
                    # Same shape checks as RuleOperand.check_operand_shape, and
                    # for the same reasons.
                    if (compare_op is None) != (compare_value is None):
                        raise ValueError(
                            f"Object {self.name()}: production condition operand {name!r}: 'op' and 'value' must be given together (a comparison against a continuous quantity) or both omitted (a boolean operand)"
                        )
                    if compare_op is not None:
                        if compare_op not in COMPARISON_OPERATORS:
                            raise ValueError(
                                f"Object {self.name()}: production condition operand {name!r}: 'op' must be one of {', '.join(COMPARISON_OPERATORS)}, got {compare_op!r}"
                            )
                        if negate:
                            raise ValueError(
                                f"Object {self.name()}: production condition operand {name!r}: 'negate' cannot be combined with a comparison; use the opposite comparison operator instead"
                            )
                        try:
                            compare_value = float(compare_value)
                        except (TypeError, ValueError):
                            raise ValueError(
                                f"Object {self.name()}: production condition operand {name!r}: 'value' must be a number, got {compare_value!r}"
                            )
                else:
                    raise ValueError(
                        f"Bad format for production condition operand : {op}"
                    )

                compare = (
                    None
                    if compare_op is None
                    else {"op": compare_op, "value": compare_value}
                )

                if port == "in":
                    fcond = self.flows_in.get(name)
                    kind = "input"
                elif port == "out":
                    fcond = self.flows_out.get(name)
                    kind = "output"
                else:  # historical: input first, then output
                    fcond = self.flows_in.get(name) or self.flows_out.get(name)
                    kind = "input nor output"
                    if fcond is None and compare is not None:
                        # A comparison may also read a MEASUREMENT link: what a
                        # sensor thresholds is the capacity level it observes
                        # (R22, R33). A boolean operand never resolves there --
                        # a level carries no state to read.
                        fcond = self.measurements_in.get(name)
                if fcond is not None:
                    return fcond, negate, compare
                raise ValueError(
                    f"Object {self.name()}: Flow {name} does not exist as {kind} flow (you must create it before using it in a FlowOut condition)"
                )

            # Normalise a bare operand (string or mapping) to the canonical
            # outer-list-of-groups form so a single unified pass resolves it.
            if isinstance(var_prod_cond, (str, dict)):
                var_prod_cond = [[var_prod_cond]]
            if not isinstance(var_prod_cond, (list, set, tuple)):
                raise ValueError(
                    f"Bad format for main conjonctive format of production condition : {var_prod_cond}"
                )

            # Prepare production condition structure in conjonctive way
            # [(C11 or C12 or ... or C1_k1) and (C21 or ... C2_k2) and ...
            # and (Cn1 or ... or Cn_kn)]
            var_prod_cond_tiny = []
            var_prod_cond_negate = []
            var_prod_cond_compare = []
            for flow_disj in var_prod_cond:
                if isinstance(flow_disj, (str, dict)):
                    operands = [flow_disj]
                elif isinstance(flow_disj, (list, set, tuple)):
                    operands = list(flow_disj)
                else:
                    raise ValueError(
                        f"Bad format for production condition structure : {flow_disj}"
                    )
                flow_disj_tiny = []
                negate_disj = []
                compare_disj = []
                for op in operands:
                    fcond, negate, compare = _resolve_operand(op)
                    flow_disj_tiny.append(fcond)
                    negate_disj.append(negate)
                    compare_disj.append(compare)
                var_prod_cond_tiny.append(flow_disj_tiny)
                var_prod_cond_negate.append(negate_disj)
                var_prod_cond_compare.append(compare_disj)

            flow_specs["var_prod_cond"] = var_prod_cond_tiny
            # Attach the negation matrix only when at least one operand is
            # negated, so the common (non-negated) case leaves the FlowOut
            # ``var_prod_cond_negate`` at its empty default -> byte-identical
            # evaluation to pre-negation muscadet.
            if any(any(row) for row in var_prod_cond_negate):
                flow_specs["var_prod_cond_negate"] = var_prod_cond_negate
            # Same gating for the comparison matrix: a condition carrying no
            # comparison leaves it empty, and the evaluation stays the purely
            # boolean one it has always been.
            if any(entry is not None for row in var_prod_cond_compare for entry in row):
                flow_specs["var_prod_cond_compare"] = var_prod_cond_compare

        # Normalise tempo occurrence-law SHORT forms to the long class names so
        # ``ObjCOD3S.from_dict`` (called next in ``add_flow``) can resolve them
        # into concrete OccurrenceDistributionModel subclasses:
        #   "delay" -> "DelayOccDistribution", "exp" -> "ExpOccDistribution",
        #   "inst"  -> "InstOccDistribution".
        # BOTH the enable and disable laws are handled. A previous version
        # normalised only ``occ_enable_flow``, so a short-form ``occ_disable_flow``
        # crashed ``from_dict`` with "delay is not a subclass of ObjCOD3S".
        for _occ_key in ("occ_enable_flow", "occ_disable_flow"):
            occ = flow_specs.get(_occ_key)
            if isinstance(occ, dict):
                occ_clsname = occ.get("cls")
                if occ_clsname and "OccDistribution" not in occ_clsname:
                    occ["cls"] = occ_clsname.capitalize() + "OccDistribution"

        return flow_specs

    def add_flow(self, flow_specs):
        """
        Adds a flow to the component using dictionary specifications.

        This method provides a flexible way to add flows using dictionary-based
        specifications. It automatically determines the flow type from the 'cls'
        attribute and handles the complete flow creation process including
        preprocessing and validation.

        Parameters
        ----------
        flow_specs : dict
            Flow specification dictionary that must contain:
            - cls: Flow class name, canonical ("FlowDiscreteIn",
              "FlowDiscreteOut", "FlowDiscreteOutTempo",
              "FlowDiscreteOutOnTrigger") or legacy ("FlowIn", "FlowOut",
              "FlowOutTempo", "FlowOutOnTrigger"), or continuous
              ("FlowContinuousIn", "FlowContinuousOut")
            - name: Flow name
            - Additional parameters specific to the flow type

        Raises
        ------
        ValueError
            If 'cls' attribute is missing, flow name already exists, or
            flow type is not supported

        Examples
        --------
        >>> comp.add_flow({
        ...     "cls": "FlowIn",
        ...     "name": "power",
        ...     "logic": "and"
        ... })

        >>> comp.add_flow({
        ...     "cls": "FlowOut",
        ...     "name": "output",
        ...     "var_prod_cond": ["input1", "input2"]
        ... })
        """
        if "cls" not in flow_specs:
            raise ValueError(
                "Please add provide a cls attribute to indicate the class of the flow to be added"
            )
        flow_specs = self.postprocess_flow_specs(flow_specs)
        flow = cod3s.ObjCOD3S.from_dict(flow_specs)

        # NOTE: the continuous family is parallel to the discrete one (both
        # derive from FlowModel, neither from the other), so the branch order
        # below carries no precedence -- the four tests are mutually exclusive.
        if isinstance(flow, (FlowDiscreteIn, FlowContinuousIn)):
            if flow.name in self.flows_in:
                raise ValueError(f"Input flow {flow.name} already exists")
            else:
                self.flows_in[flow.name] = flow
        elif isinstance(flow, (FlowDiscreteOut, FlowContinuousOut)):
            if flow.name in self.flows_out:
                raise ValueError(f"Output flow {flow.name} already exists")
            else:
                self.flows_out[flow.name] = flow
        else:
            raise ValueError(f"Flow of type {type(flow)} unsupported")

    def add_flow_in(self, **params):
        """
        Adds an input flow to the component.

        Parameters
        ----------
        **params : dict
            Parameters for the input flow.
        """
        flow_name = params.get("name")
        if not (flow_name in self.flows_in):
            self.flows_in[flow_name] = FlowIn(**params)
        else:
            raise ValueError(f"Input flow {flow_name} already exists")

    # DEPRECATED
    def prepare_flow_out_params(self, **params):
        """
        Prepares the parameters for an output flow.

        Parameters
        ----------
        **params : dict
            Parameters for the output flow.

        Returns
        -------
        dict
            The prepared parameters for the output flow.
        """
        warnings.warn(
            "prepare_flow_out_params() is deprecated and will be removed in a future version, use add_flow method instead",
            DeprecationWarning,
            stacklevel=2,
        )
        var_prod_cond = params.get("var_prod_cond")
        if var_prod_cond:
            if isinstance(var_prod_cond, str):
                var_prod_cond = [[var_prod_cond]]
            elif isinstance(var_prod_cond, (list, set, tuple)):
                # Prepare production condition structure in conjonctive way
                # [(C11 or C12 or ... or C1_k1) and (C21 or ... C2_k2) and ...
                # and (Cn1 or ... or Cn_kn)]
                var_prod_cond_tiny = []
                for flow_disj in var_prod_cond:
                    # Get input flow associated to production conditions
                    if isinstance(flow_disj, str):
                        flow_disj_tiny = [self.flows_in[flow_disj]]
                    elif isinstance(flow_disj, (list, set, tuple)):
                        flow_disj_tiny = [
                            self.flows_in[flow_name] for flow_name in list(flow_disj)
                        ]
                    else:
                        raise ValueError(
                            f"Bad format for production condition structure : {flow_disj}"
                        )
                    var_prod_cond_tiny.append(flow_disj_tiny)
            else:
                raise ValueError(
                    f"Bad format for main conjonctive format of production condition : {var_prod_cond}"
                )

            params["var_prod_cond"] = var_prod_cond_tiny

        return params

    def add_flow_out(self, **params):
        """
        Adds an output flow to the component.

        Parameters
        ----------
        **params : dict
            Parameters for the output flow.
        """
        params = self.prepare_flow_out_params(**params)

        flow_name = params.get("name")

        if not (flow_name in self.flows_out):
            self.flows_out[flow_name] = FlowOut(**params)
        else:
            raise ValueError(f"Output flow {flow_name} already exists")

    def add_flow_out_tempo(self, **params):
        """
        Adds a temporized output flow to the component.

        Parameters
        ----------
        **params : dict
            Parameters for the temporized output flow.
        """

        params = self.prepare_flow_out_params(**params)

        flow_name = params.get("name")

        if not (flow_name in self.flows_out):
            self.flows_out[flow_name] = FlowOutTempo(**params)
        else:
            raise ValueError(f"Output flow {flow_name} already exists")

        # if var_prod_logic:
        # sm_flow_prod_available_fun = \
        #     self.flows[flow_name].create_sensitive_set_flow_prod_available()
        # sm_flow_prod_available_name = f"set_{self.name}_prod_available"

        # addSensitiveMethod(
        #     sm_flow_fed_name, sm_flow_prod_available_fun)

    def add_flow_out_on_trigger(self, **params):
        """
        Adds an output flow that is triggered by an input flow.

        Parameters
        ----------
        **params : dict
            Parameters for the triggered output flow.
        """
        params = self.prepare_flow_out_params(**params)

        flow_name = params.get("name")

        if not (flow_name in self.flows_out):
            self.flows_out[flow_name] = FlowOutOnTrigger(**params)
        else:
            raise ValueError(f"Output (on trigger) flow {flow_name} already exists")

    def add_flow_continuous_in(self, **params):
        """
        Adds a continuous (real-valued) input flow to the component.

        The flow is stored in ``flows_in`` alongside the discrete input flows,
        so that ``auto_connect`` / ``connect_flow`` keep finding it by name; use
        ``flows_continuous_in`` to enumerate only the continuous ones.

        Parameters
        ----------
        **params : dict
            Parameters for the continuous input flow.
        """
        flow_name = params.get("name")
        if not (flow_name in self.flows_in):
            self.flows_in[flow_name] = FlowContinuousIn(**params)
        else:
            raise ValueError(f"Input flow {flow_name} already exists")

    def add_flow_continuous_out(self, **params):
        """
        Adds a continuous (real-valued) output flow to the component.

        Note that ``prepare_flow_out_params`` is deliberately NOT applied: the
        ``var_prod_cond`` boolean production condition it normalises belongs to
        the discrete family only.

        Parameters
        ----------
        **params : dict
            Parameters for the continuous output flow.
        """
        flow_name = params.get("name")
        if not (flow_name in self.flows_out):
            self.flows_out[flow_name] = FlowContinuousOut(**params)
        else:
            raise ValueError(f"Output flow {flow_name} already exists")

    @property
    def flows_continuous_in(self):
        """Continuous input flows only, in declaration order."""
        return {
            name: flow
            for name, flow in self.flows_in.items()
            if isinstance(flow, FlowContinuous)
        }

    @property
    def flows_continuous_out(self):
        """Continuous output flows only, in declaration order."""
        return {
            name: flow
            for name, flow in self.flows_out.items()
            if isinstance(flow, FlowContinuous)
        }

    def add_capacity(
        self,
        name,
        flow=None,
        flows=None,
        capacity=None,
        side=None,
        content_init=None,
        **params,
    ):
        """
        Declares a capacity: a volume held over one or more continuous flows.

        A capacity is declared INDEPENDENTLY of the component's transformation
        rules (KD14), so a buffer can be added to an existing model without
        touching its transformation logic. It integrates one level per held
        flow plus a total, reports the raw quantity and the weighted fill of
        each, and publishes its level over a measurement link.

        The flows it holds must already be declared, so call this method AFTER
        the ``add_flow_continuous_*`` calls of ``add_flows``.

        Parameters
        ----------
        name : str
            Capacity name. Must be unique on the component.
        flow : str or dict, optional
            Short form for the common single-flow case: a flow name, or a
            mapping carrying ``name`` and ``weight``.
        flows : list, optional
            General form: a list of flow names or of mappings carrying ``name``
            and ``weight``. ``weight`` defaults to 1 and expresses how much
            volume one unit of that flow occupies. Mutually exclusive with
            ``flow``.
        capacity : float
            The volume the held flows SHARE. A single scalar, strictly
            positive.
        side : str, optional
            ``"in"`` places the whole capacity upstream of the component's
            rules, ``"out"`` downstream. Left out, the side is resolved from
            the held flows and defaults to ``"in"`` for a flow carried by both
            sides. Every held flow must resolve to the same side.
        content_init : dict, optional
            Initial raw quantity per held flow. An omitted flow starts empty.
        **params : dict
            Additional capacity parameters.

        Returns
        -------
        muscadet.capacity.Capacity
            The declared, validated and resolved capacity.

        Raises
        ------
        ValueError
            If the capacity name is already used, if the declaration is
            malformed, if a held flow does not exist, is not continuous or does
            not resolve to the capacity's side, if the held flows resolve to
            different sides, or if another capacity already holds one of them
            on the same side.

        Examples
        --------
        >>> comp.add_capacity(name="cuve", flow="H2O", capacity=1000)  # doctest: +SKIP

        >>> comp.add_capacity(                                        # doctest: +SKIP
        ...     name="cuve",
        ...     flows=[{"name": "H2O", "weight": 1},
        ...            {"name": "additif", "weight": 2}],
        ...     capacity=1000,
        ...     side="in",
        ...     content_init={"H2O": 0, "additif": 0},
        ... )
        """
        if name in self.capacities:
            raise ValueError(f"Capacity {name} already exists")

        if flow is not None and flows is not None:
            raise ValueError(
                f"Object {self.name()}: capacity {name}: give either 'flow' "
                "(the single-flow short form) or 'flows', not both"
            )
        flows_specs = flows if flows is not None else flow
        if flows_specs is None:
            raise ValueError(
                f"Object {self.name()}: capacity {name}: declare the flows it "
                "holds with 'flow' or 'flows'"
            )
        if capacity is None:
            raise ValueError(
                f"Object {self.name()}: capacity {name}: 'capacity', the "
                "volume the held flows share, is required"
            )

        capacity_obj = Capacity(
            name=name,
            flows=copy.deepcopy(flows_specs),
            capacity=capacity,
            side=side if side is not None else "in",
            content_init=copy.deepcopy(content_init) if content_init else {},
            **params,
        )

        self.resolve_capacity(capacity_obj, side_declared=side)

        capacity_obj.add_variables(self)
        capacity_obj.add_mb(self)
        capacity_obj.add_automaton(self)

        self.capacities[name] = capacity_obj

        self.register_capacity(capacity_obj)

        return capacity_obj

    def resolve_capacity(self, capacity, side_declared=None):
        """
        Resolves a capacity's held flows against the declared continuous flows.

        Each held flow is looked up on both sides. A flow carried by a single
        side resolves to it; a flow carried by both is disambiguated by
        ``side_declared``, falling back to the documented ``"in"`` default. The
        resolved side is written back into each flow entry and into the
        capacity, and every entry must agree: a capacity sits ENTIRELY upstream
        or ENTIRELY downstream of the component's rules (R4).

        Parameters
        ----------
        capacity : muscadet.capacity.Capacity
            The capacity to resolve, updated in place.
        side_declared : str, optional
            The side the caller explicitly asked for, or None.

        Raises
        ------
        ValueError
            If a held flow does not exist, is discrete, does not exist on the
            declared side, if the held flows resolve to different sides, or if
            another capacity already holds one of them on the same side.
        """
        where = f"capacity {capacity.name}"

        for entry in capacity.flows:
            sides = [
                side
                for side, flow in (
                    ("in", self.flows_in.get(entry.name)),
                    ("out", self.flows_out.get(entry.name)),
                )
                if flow is not None
            ]
            if not sides:
                raise ValueError(
                    f"Object {self.name()}: {where}: flow {entry.name} does not "
                    "exist as input nor output flow (you must create it before "
                    "holding it in a capacity)"
                )

            if side_declared is not None:
                if side_declared not in sides:
                    kind = "input" if side_declared == "in" else "output"
                    raise ValueError(
                        f"Object {self.name()}: {where} is declared on side "
                        f"{side_declared!r} but flow {entry.name} does not "
                        f"exist as {kind} flow"
                    )
                entry.side = side_declared
            else:
                # A flow carried by both sides keeps the documented default.
                entry.side = "in" if "in" in sides else "out"

        resolved_sides = {entry.side for entry in capacity.flows}
        if len(resolved_sides) > 1:
            detail = ", ".join(
                f"{entry.name}->{entry.side}" for entry in capacity.flows
            )
            raise ValueError(
                f"Object {self.name()}: {where} holds flows resolving to "
                f"different sides ({detail}): a capacity sits entirely "
                "upstream (side='in') or entirely downstream (side='out') of "
                "the component's rules"
            )

        capacity.side = resolved_sides.pop()

        flows_on_side = self.flows_in if capacity.side == "in" else self.flows_out
        for entry in capacity.flows:
            flow = flows_on_side[entry.name]
            if not isinstance(flow, FlowContinuous):
                raise ValueError(
                    f"Object {self.name()}: {where}: flow {entry.name} is a "
                    f"discrete flow ({type(flow).__name__}); a capacity holds "
                    "continuous flows only"
                )

        for other in self.capacities.values():
            if other.side != capacity.side:
                # The same flow may be buffered upstream AND downstream of the
                # rules: those are two distinct hops (KTD13).
                continue
            clash = sorted(set(capacity.flow_names) & set(other.flow_names))
            if clash:
                raise ValueError(
                    f"Object {self.name()}: {where} claims flow "
                    f"{', '.join(clash)} on side {capacity.side!r}, already "
                    f"held by capacity {other.name}: a flow is buffered by at "
                    "most one capacity per side"
                )

        return capacity

    def register_capacity(self, capacity):
        """
        Registers a capacity's variables, equation and bound transitions.

        The levels become ODE variables, the fills explicit ones, and the
        empty/full transitions are registered as WATCHED so a bound is crossed
        exactly rather than at the next integration step (R7). One equation
        method covers every capacity of the component.
        """
        system = self.system()

        capacity.register(system)

        # The two transit hooks are written by the sweeps, i.e. from inside an
        # equation method, and PyCATSHOO refuses ``setValue`` on a variable its
        # solver does not know about while the differential system is being
        # resolved. Declared EXPLICIT here, alongside the fills, they can be
        # written at each integration step -- which is what makes an interposed
        # capacity the counterparty of the rules (KTD13).
        for flow_name in capacity.flow_names:
            system.pdmp_add_explicit_variable(capacity.var_inflow[flow_name])
            system.pdmp_add_explicit_variable(capacity.var_outflow[flow_name])

        if not self._capacity_equation_registered:
            system.pdmp_add_equation_method(
                "compute_capacities",
                self,
                allocate_capacity_equation_order(system),
            )
            self._capacity_equation_registered = True

        return capacity

    def compute_capacities(self):
        """PDMP equation: integrate every declared capacity of this component."""
        for capacity in self.capacities.values():
            capacity.compute()

    @property
    def capacities_in(self):
        """Capacities sitting upstream of the rules, in declaration order."""
        return {
            name: capacity
            for name, capacity in self.capacities.items()
            if capacity.side == "in"
        }

    @property
    def capacities_out(self):
        """Capacities sitting downstream of the rules, in declaration order."""
        return {
            name: capacity
            for name, capacity in self.capacities.items()
            if capacity.side == "out"
        }

    def get_capacity_of_flow(self, flow_name, side):
        """The capacity buffering ``flow_name`` on ``side``, or None.

        This is the lookup KTD13's counterparty substitution rests on: with a
        capacity interposed, the rules face it instead of the flow.
        """
        for capacity in self.capacities.values():
            if capacity.side == side and flow_name in capacity.flow_names:
                return capacity
        return None

    def add_measurement_in(self, name, **params):
        """
        Declares the importing side of a measurement link (R33).

        The observing component reads a capacity's level through a pair of
        PyCATSHOO references, which carry no setter: the link is read-only by
        construction, exchanges no quantity and enters no allocation. Connect
        it with ``system.connect(holder, f"{name}_level_out", observer,
        f"{name}_level_in")``.

        Parameters
        ----------
        name : str
            Measurement channel name. Matches the observed capacity's name,
            which is what makes the exported and imported aliases line up.
        **params : dict
            Additional measurement parameters, e.g. ``level_default``.

        Returns
        -------
        muscadet.capacity.MeasurementIn
            The declared measurement import.
        """
        if name in self.measurements_in:
            raise ValueError(f"Measurement link {name} already exists")

        measurement = MeasurementIn(name=name, **params)
        measurement.add_variables(self)
        measurement.add_mb(self)

        self.measurements_in[name] = measurement

        return measurement

    def add_rules(self, name, rules=None, **params):
        """
        Declares an ordered set of transformation rules on the component.

        A rule set is declared on the COMPONENT and not on an output flow: a
        reaction with correlated outputs cannot be stated one output at a time.
        Each rule carries a guard (``cond``), a ``cons`` map of consumed input
        coefficients and a ``prod`` map of produced output coefficients; an
        omitted coefficient defaults to 1. A rule declared without a guard is
        the default rule of its set and applies when no other rule matches.

        The flows the rules refer to must already be declared, so call this
        method AFTER the ``add_flow_*`` calls of ``add_flows``.

        Parameters
        ----------
        name : str
            Rule set name. Must be unique on the component.
        rules : list
            Ordered rules, each a mapping (or a :class:`~muscadet.rules.Rule`)
            carrying ``cond``, ``cons`` and ``prod``. A guard may be given as a
            list of operands or as an expression string such as
            ``"F4 and F1 >= 10"``; only the structured form is stored.
        **params : dict
            Additional rule set parameters.

        Returns
        -------
        muscadet.rules.RuleSet
            The declared, validated and resolved rule set.

        Raises
        ------
        ValueError
            If the rule set name is already used, if a rule is malformed, if
            more than one rule is declared without a guard, or if a name used
            in a guard / ``cons`` / ``prod`` does not resolve to a declared
            flow.

        Examples
        --------
        >>> comp.add_rules(                                  # doctest: +SKIP
        ...     name="X",
        ...     rules=[
        ...         dict(cond=[{"name": "F4", "negate": True}],
        ...              cons={"F3": 3, "F2": 2}, prod={"X": 2}),
        ...         dict(cond="F4 and F1 >= 10",
        ...              cons={"F1": 3}, prod={"X": 0.5}),
        ...     ],
        ... )
        """
        if name in self.rule_sets:
            raise ValueError(f"Rule set {name} already exists")

        if rules is None:
            rules = []
        if isinstance(rules, (dict, Rule)):
            rules = [rules]

        # Work on independent specifications so that declaring the same rules on
        # two components -- or re-declaring a dumped rule set -- never shares
        # (nor deep-copies) the flow objects bound during resolution.
        rules_specs = [
            rule.model_dump() if isinstance(rule, Rule) else copy.deepcopy(rule)
            for rule in rules
        ]

        rule_set = RuleSet(name=name, rules=rules_specs, **params)

        self.resolve_rule_set(rule_set)

        self.rule_sets[name] = rule_set

        self.compile_rule_mode(rule_set)

        return rule_set

    def compile_rule_mode(self, rule_set):
        """
        Compiles a rule set's guards into a watched mode automaton (KD7, R12).

        One state per rule -- plus a state meaning "no rule applies" when the
        set declares no default (R14) -- and a transition between every pair of
        states, conditioned on the guards. The transitions carry an
        instantaneous law and are registered as WATCHED, so a threshold is
        crossed exactly rather than at the following integration step: a guard
        like ``F1 >= 10`` would otherwise fire late, and by an amount that
        depends on the step size.

        The automaton's ACTIVE STATE -- and not a live guard evaluation -- is
        what selects the coefficient maps :meth:`evaluate_production` reads.
        That freeze within a mode is what breaks the algebraic coupling between
        demand and production the two topological sweeps rest on.

        Called by :meth:`add_rules`, at declaration time: the conditions are
        closures over the flow objects, so they need no variable to exist yet.

        Parameters
        ----------
        rule_set : muscadet.rules.RuleSet
            The rule set to compile, updated in place through its ``mode``.

        Returns
        -------
        muscadet.rules.RuleMode or None
            None when the set carries no guard at all: its default rule needs
            no automaton, and a model that declares none never gains one.
        """
        if not rule_set.guarded_rules:
            return None

        mode = RuleMode(rule_set, self)
        mode.build()
        mode.register(self.system())

        rule_set.mode = mode

        return mode

    def resolve_rule_set(self, rule_set):
        """
        Resolves every flow name used by a rule set against the declared flows.

        Guard operands follow the discrete production-condition resolution:
        ``port`` forces a side, its absence resolves the input first. The
        resolved side is written back into the operand ``port`` and the flow
        object into its ``flow`` attribute, so the evaluation and the
        guard-compilation layers do not resolve names again.

        ``cons`` names are consumed and therefore resolved against the input
        flows, ``prod`` names are produced and resolved against the output
        flows.

        Parameters
        ----------
        rule_set : muscadet.rules.RuleSet
            The rule set to resolve, updated in place.

        Raises
        ------
        ValueError
            If a name does not resolve to a declared flow. The message names
            the offending name.
        """
        for index, rule in enumerate(rule_set.rules):
            where = f"rule set {rule_set.name}, {rule_set.rule_label(index)}"

            for operand in rule.cond:
                operand.flow, operand.port = self._resolve_rule_flow(
                    operand.name, operand.port, where, "rule guard"
                )

            for flow_name in rule.cons:
                self._resolve_rule_flow(flow_name, "in", where, "rule 'cons' map")

            for flow_name in rule.prod:
                self._resolve_rule_flow(flow_name, "out", where, "rule 'prod' map")

        return rule_set

    def _resolve_rule_flow(self, flow_name, port, where, role):
        """Resolve one rule flow name, returning the ``(flow, port)`` pair.

        A name that designates a declared capacity is refused here (R29): a
        guard reading a level its own rule fills is the mode-chattering case,
        and forbidding it removes that class of instability by construction
        (KD15). Threshold control over production goes through a sensor
        component that reads the capacity and drives a control port.
        """
        if flow_name in self.capacities:
            if role == "rule guard":
                raise ValueError(
                    f"Object {self.name()}: {where}: {role} references "
                    f"capacity {flow_name}: a rule guard cannot read a "
                    "capacity level. Gate production on a level through a "
                    "sensor component that reads the capacity and drives a "
                    "control port, then guard on that control port"
                )
            raise ValueError(
                f"Object {self.name()}: {where}: {role} references capacity "
                f"{flow_name}, which is not a flow: an interposed capacity "
                "replaces the flow it buffers automatically, so rules name "
                "flows, never capacities"
            )

        if port == "in":
            flow, kind = self.flows_in.get(flow_name), "input"
        elif port == "out":
            flow, kind = self.flows_out.get(flow_name), "output"
        else:
            # Historical resolution order: input first, then output.
            flow = self.flows_in.get(flow_name)
            if flow is not None:
                port, kind = "in", "input"
            else:
                flow = self.flows_out.get(flow_name)
                if flow is not None:
                    port, kind = "out", "output"
                else:
                    port, kind = None, "input nor output"

        if flow is None:
            raise ValueError(
                f"Object {self.name()}: {where}: flow {flow_name} does not exist "
                f"as {kind} flow (you must create it before using it in a {role})"
            )

        return flow, port

    # ------------------------------------------------------------------
    # Demand -- the reverse sweep (R5, R7, R34)
    # ------------------------------------------------------------------

    def compute_demand(self):
        """
        PDMP equation: what this component asks its producers for.

        Named after ``muscadet.ordering.DEMAND_EQUATION_METHOD``: the ordering
        module looks this method up BY NAME on every node of the continuous-flow
        graph and registers it with a graph-derived order on the **reverse**
        sweep, so a consumer publishes its demand before the component feeding
        it computes its own (R8). Nothing here, and nothing in a model, ever
        writes an order down.

        Demand travels the graph in the opposite direction to production (KD4):
        each continuous input publishes upstream what the component needs from
        it, and the producer then delivers the lesser of what it can produce and
        what was asked for (R6).

        Notes
        -----
        Evaluated by the solver, repeatedly, inside one integration step: it
        must stay a pure function of the current variable values and must not
        create or register anything.
        """
        self.apply_demand(self.evaluate_demand())

    def evaluate_demand(self):
        """
        Computes what the component needs from each of its continuous inputs.

        The mapping of R34: the demand aggregated on an output is carried back
        onto the inputs through the active rule's ``prod`` and ``cons``
        coefficients. It uses the **declared** coefficients, never the
        quantities actually available -- a component capped by a scarce input
        therefore still claims its nominal demand on the others, and over-claims
        a shared upstream supply. Correcting it needs a second demand pass or an
        iterative solve, both outside the two-sweep ordering; Scope Boundaries
        records it.

        A component declaring no rule transfers each input to the output of the
        same name (R31), so its demand crosses it unchanged. An input no rule
        and no transfer covers is a pure consumer's input: it claims the demand
        it was DECLARED with, ``var_demand_default``.

        Returns
        -------
        dict
            ``{input flow name: demand}``, possibly ``math.inf`` where nothing
            bounds it -- an output no consumer is connected to throttles no
            input of the component producing it.

        Raises
        ------
        ValueError
            When two guards of one rule set hold at once (R13). The R31 mismatch
            of a rule-less component is deliberately NOT raised here: it belongs
            to the production sweep, where it was already reported, and a
            component replacing that sweep with an equation of its own must not
            be refused by the demand sweep instead.
        """
        demands = {}

        def accumulate(flow_name, quantity):
            demands[flow_name] = demands.get(flow_name, 0.0) + quantity

        if self.rule_sets:
            for rule_set in self.rule_sets.values():
                # Every flow the SET consumes starts at zero, whichever of its
                # rules is active: a rule set selecting nothing demands nothing,
                # exactly as it produces nothing (R14).
                for flow_name in rule_set.consumed_flows:
                    accumulate(flow_name, 0.0)

                rule = self.get_active_rule(rule_set)
                if rule is None:
                    continue

                scale = self.get_demand_scale(rule)
                for flow_name, coefficient in rule.cons.items():
                    accumulate(flow_name, coefficient * scale)
        else:
            for flow_name in self.get_transferable_flows():
                accumulate(flow_name, self.get_output_demand(flow_name))

        # A continuous input no rule and no transfer covers claims what it was
        # declared with: a pure consumer has no output to map a demand back from.
        for flow_name, flow in self.flows_continuous_in.items():
            demands.setdefault(flow_name, float(flow.var_demand_default))

        return demands

    def get_demand_scale(self, rule):
        """
        Returns the scale ``rule`` would have to run at to satisfy its outputs.

        The scale is taken over the ``prod`` coefficients, as a **maximum**: the
        rule's outputs are correlated by construction, so the scale that serves
        them all is the one the most demanding of them needs. An output nothing
        is connected to demands without bound, and a rule producing nothing at
        all is limited by no output, so both fall back to the nominal scale.

        Parameters
        ----------
        rule : muscadet.rules.Rule
            The active rule of one of the component's rule sets.

        Returns
        -------
        float
            The scale, possibly ``math.inf``.
        """
        scales = [
            self.get_output_demand(flow_name) / coefficient
            for flow_name, coefficient in rule.prod.items()
            if coefficient > 0
        ]

        if not scales:
            return UNCONSTRAINED_SCALE

        return max(scales)

    def get_output_demand(self, flow_name):
        """
        Returns the demand an output carries back into the component.

        Two bounds compose here:

        - what the consumers ask for, :data:`~muscadet.flow_continuous.UNBOUNDED`
          when none is connected;
        - what an interposed output capacity makes of it
          (:meth:`~muscadet.capacity.Capacity.demand_claim`): while it has room
          it adds its own fill claim, so the rules run beyond what the consumers
          draw and the buffer accumulates the difference (R36); once full it
          accepts only what currently leaves it, which is how a full capacity
          reduces the demand propagated upstream (R7).

        The claim is ADDITIONAL to the R34 mapping and never replaces it: this
        is the demand the rules face, which :meth:`get_demand_scale` then carries
        back onto the inputs through the active rule's declared coefficients.

        Parameters
        ----------
        flow_name : str
            Name of an output flow of the component.

        Returns
        -------
        float
            The demand, possibly ``math.inf``. A discrete output carries no
            demand channel and never throttles anything.
        """
        flow = self.flows_out.get(flow_name)

        if not isinstance(flow, FlowContinuousOut):
            return UNBOUNDED

        demand = flow.get_demand_bound()

        capacity = self.get_capacity_of_flow(flow_name, "out")
        if capacity is not None:
            demand = capacity.demand_claim(demand, flow_name)

        return demand

    def apply_demand(self, demands):
        """
        Publishes the demands upstream, as the input capacities claim them (R7, R36).

        Two quantities, deliberately kept apart:

        - what the rules may draw, kept on the flow as ``demand_required`` and
          read back by :meth:`get_input_available`. It is the R34 mapping, and
          an input capacity never changes it;
        - what is PUBLISHED upstream, which the input capacity claims through
          :meth:`~muscadet.capacity.Capacity.demand_claim`: with room left it
          asks for its fill rate on top, and what arrives beyond what the rules
          draw stays in the volume (R36); once full it asks only for what
          currently leaves it, so the producer feeding a capacity at its volume
          delivers less (AE11) while the rules keep drawing from the stock.

        Parameters
        ----------
        demands : dict
            ``{input flow name: demand}``, as returned by
            :meth:`evaluate_demand`.
        """
        for flow_name, demand in demands.items():
            flow = self.flows_in.get(flow_name)

            # Only a continuous input carries a demand channel. A discrete input
            # named by a rule is a gate, not a quantity.
            if not isinstance(flow, FlowContinuousIn):
                continue

            required = max(float(demand), 0.0)
            flow.demand_required = required

            capacity = self.get_capacity_of_flow(flow_name, "in")
            if capacity is not None:
                required = capacity.demand_claim(required, flow_name)

            flow.set_demand(required)

    def get_input_required_demand(self, flow_name):
        """
        Returns what the rules may draw from an input, as the demand sweep sees it.

        Unbounded for a discrete input, and for a continuous one whose demand
        equation has not run: the production sweep then behaves exactly as it
        did before demand existed, producing whatever the inputs allow.
        """
        flow = self.flows_in.get(flow_name)

        if not isinstance(flow, FlowContinuousIn):
            return UNBOUNDED

        return flow.demand_required

    # ------------------------------------------------------------------
    # Rule evaluation -- the production sweep (R3, R15, R31)
    # ------------------------------------------------------------------

    def compute_production(self):
        """
        PDMP equation: what this component produces at this integration step.

        Named after ``muscadet.ordering.PRODUCTION_EQUATION_METHOD``: the
        ordering module looks this method up BY NAME on every node of the
        continuous-flow graph and registers it with a graph-derived order, so
        nothing here -- and nothing in a model -- ever writes an order down
        (R8).

        One equation covers every rule set of the component: the active rule of
        each is evaluated at the scale its scarcest input allows (R15), and a
        component declaring no rule at all transfers each input to the output
        of the same name (R31). The four hops of KTD13 are crossed in order:
        the input flows fill their capacity, the rules draw from it, production
        enters the output capacity, and the output flows draw from that.

        Notes
        -----
        Evaluated by the solver, repeatedly, inside one integration step: it
        must stay a pure function of the current variable values and must not
        create or register anything.
        """
        self.refresh_continuous_inputs()

        consumption, production = self.evaluate_production()

        self.apply_consumption(consumption)
        self.apply_production(production)

    def refresh_continuous_inputs(self):
        """
        Mirrors what each continuous input receives onto its ``var_fed`` (R6).

        The mirror is otherwise refreshed by a sensitive method, which the
        solver runs when the value a producer EXPORTS changes -- and an
        allocation can move without that value moving at all, when a producer
        keeps delivering the same total and only the split among its consumers
        changes. Rewriting it here, at the head of the forward sweep and
        therefore after every producer has been evaluated, is what keeps the
        input a model reads equal to the share it was allocated.
        """
        for flow in self.flows_continuous_in.values():
            flow.var_fed.setValue(flow.get_delivered())

    def evaluate_production(self):
        """
        Computes what the component draws from its inputs and what it produces.

        Returns
        -------
        tuple of dict
            ``(consumption, production)``, both ``{flow name: rate}``. Rule
            sets accumulate: a flow named by two of them carries their sum.
            Every flow a rule set names appears, at zero when the active mode
            does not name it -- a rate another mode left behind must be cleared,
            not inherited.

        Raises
        ------
        ValueError
            When the component declares no rule and its continuous flows do not
            match name for name (R31), or when two guards of one rule set hold
            at once (R13).
        """
        consumption = {}
        production = {}

        def accumulate(target, quantities):
            for flow_name, quantity in quantities.items():
                target[flow_name] = target.get(flow_name, 0.0) + quantity

        if self.rule_sets:
            for rule_set in self.rule_sets.values():
                # Every flow the SET names starts at zero, whichever of its
                # rules is active. Written rather than left out: a flow the
                # previously active mode produced -- or drew from a capacity --
                # would otherwise keep the rate that mode left on it, and a rule
                # set selecting nothing must produce zero (R14).
                accumulate(consumption, dict.fromkeys(rule_set.consumed_flows, 0.0))
                accumulate(production, dict.fromkeys(rule_set.produced_flows, 0.0))

                rule = self.get_active_rule(rule_set)
                if rule is None:
                    continue

                available = {
                    flow_name: self.get_input_available(flow_name)
                    for flow_name in rule.cons
                }
                scale = rule_scale(rule, available)

                accumulate(consumption, rule_consumption(rule, scale))
                accumulate(production, rule_production(rule, scale))
        else:
            for flow_name in self.get_identity_transfer_flows():
                transferred = self.get_input_transferred(flow_name)
                accumulate(consumption, {flow_name: transferred})
                accumulate(production, {flow_name: transferred})

        # A continuous output no rule and no transfer names is a SOURCE: the
        # value it was declared with is what it can produce. It appears here so
        # that what it delivers is reconciled with the demand like any other
        # production -- an output holding a rate nobody asks for delivers less.
        for flow_name, flow in self.flows_continuous_out.items():
            production.setdefault(flow_name, float(flow.var_fed_default))

        return consumption, production

    def get_active_rule(self, rule_set):
        """
        Returns the rule of ``rule_set`` whose production is evaluated, or None.

        The override point for rule selection. Guards are compiled into a mode
        automaton by :meth:`compile_rule_mode`, and the automaton's ACTIVE STATE
        is what designates the rule: reading a state rather than re-evaluating
        the guards here is what freezes the coefficients within a mode. A set
        carrying no guard has no automaton and yields its default rule.

        Parameters
        ----------
        rule_set : muscadet.rules.RuleSet
            The rule set to select a rule from.

        Returns
        -------
        muscadet.rules.Rule or None
            None means "produce nothing for this rule set" (R14).

        Raises
        ------
        ValueError
            When two guards of the set hold at once (R13). Checked at every
            evaluation, which is where a conflict can first be seen: it depends
            on the current variable values.
        """
        return rule_set.active_rule()

    def get_input_delivered(self, flow_name):
        """
        Returns what an input flow currently delivers to the component.

        Read from the flow's REFERENCE rather than from its ``var_fed`` mirror:
        the mirror is refreshed by a sensitive method, which the solver runs
        between integration steps and not between two equations of the same
        step. Reading the reference is what makes a chain of components settle
        within a single step instead of lagging one step per hop.

        Parameters
        ----------
        flow_name : str
            Name of an input flow of the component.

        Returns
        -------
        float
            The delivered quantity; the declared default when nothing is
            connected. A discrete input consumed by a rule reads as 1 when fed
            and 0 otherwise.
        """
        flow = self.flows_in[flow_name]

        if isinstance(flow, FlowContinuousIn):
            # Not the raw sum of the connections: a producer exports ONE value
            # to all its consumers, so what this one receives is the share its
            # producers allocated to it (R16).
            return float(flow.get_delivered())

        return float(flow.var_fed.value())

    def get_input_available(self, flow_name):
        """
        Returns what the rules may draw from an input flow at this step.

        This is KTD13's counterparty substitution on the input side: **an
        interposed capacity replaces the flow it buffers**. With one, the input
        flow fills the capacity and the rules draw from what the capacity can
        serve -- unbounded while it holds something, limited to what transits
        through it once empty (R7). Without one, the rules face the flow
        directly and draw what it delivers.

        **Demand is the other bound.** A capacity holding stock serves without
        limit and an entirely unbounded rule would run at its nominal scale, so
        what the component actually needs from this input -- computed by the
        demand sweep, before any capacity bound (R7) -- is what caps the draw. A
        component asked for nothing draws nothing, however much stock it sits on.

        Parameters
        ----------
        flow_name : str
            Name of an input flow of the component.

        Returns
        -------
        float
            The drawable quantity, still ``math.inf`` when NOTHING bounds it: a
            capacity holding stock, and no demand computed either.
            :func:`muscadet.rules.rule_scale` then turns the unbounded rule into
            its nominal production.
        """
        delivered = self.get_input_delivered(flow_name)

        capacity = self.get_capacity_of_flow(flow_name, "in")
        if capacity is None:
            available = delivered
        else:
            # Hop 1: whatever the flow delivers enters the capacity.
            capacity.set_inflow(flow_name, delivered)
            available = capacity.serve_limit(flow_name)

        return min(available, self.get_input_required_demand(flow_name))

    def get_input_transferred(self, flow_name):
        """
        Returns what an identity transfer moves from an input to its output.

        The transfer moves what is asked for, and never more than its
        counterparty can serve: a capacity holding stock now releases what the
        demand sweep claims, which is more than what enters it (R7).

        When nothing bounds the draw at all -- a stocked capacity, and no demand
        either -- the transfer falls back to what arrives. An unbounded demand
        is a downstream that asked for nothing in particular, not a downstream
        asking for everything, so it must not drain a tank.

        Parameters
        ----------
        flow_name : str
            Name of an input flow carried on both sides of the component.

        Returns
        -------
        float
            The transferred quantity, always finite.
        """
        # Called for its side effect too: the input capacity is filled here.
        available = self.get_input_available(flow_name)

        if math.isinf(available):
            return self.get_input_delivered(flow_name)

        return max(available, 0.0)

    def get_identity_transfer_flows(self):
        """
        Returns the flow names a rule-less component transfers, input to output.

        A component that declares continuous flows and no transformation rule
        performs an identity transfer, matching each input to the output of the
        same name (R31, KD18): without it every plain tank would need a
        ceremonial same-in-same-out rule.

        A flow declared on one side only is not part of any transfer, and what
        it means depends on whether the component transforms at all:

        - a component carrying continuous flows on a SINGLE side transfers
          nothing. Its outputs are sources holding their declared rate and its
          inputs are sinks -- there is no counterpart for them to match, and
          demanding one would outlaw every source and every consumer;
        - on a two-sided component, an unmatched flow is a hole in the model:
          a quantity arrives and vanishes, or an output is expected to carry
          something nothing produces. It raises, naming the component and the
          flow.

        The check is scoped to the flows the model actually WIRES. An
        unconnected continuous flow exchanges nothing -- an input reads its
        declared default, an output holds its own -- so it can neither lose nor
        invent a quantity, and refusing it would reject models still being
        assembled.

        Returns
        -------
        list of str
            The transferred flow names, in input declaration order. Empty when
            the component transfers nothing.

        Raises
        ------
        ValueError
            When a wired continuous flow of a two-sided rule-less component has
            no counterpart of the same name. The message names the component
            and every unmatched flow.
        """
        flows_in = self.flows_continuous_in
        flows_out = self.flows_continuous_out

        if not flows_in or not flows_out:
            return []

        unmatched = [
            f"input flow {name}"
            for name, flow in flows_in.items()
            if name not in flows_out and self.continuous_flow_is_connected(flow, "in")
        ]
        unmatched += [
            f"output flow {name}"
            for name, flow in flows_out.items()
            if name not in flows_in and self.continuous_flow_is_connected(flow, "out")
        ]

        if unmatched:
            raise ValueError(
                f"Object {self.name()}: declares continuous flows and no "
                f"transformation rule, so it transfers each input to the output "
                f"of the same name, but {', '.join(unmatched)} has no flow of "
                f"the same name on the other side. Declare the missing flow, or "
                f"declare what this component transforms with add_rules"
            )

        return [name for name in flows_in if name in flows_out]

    def get_transferable_flows(self):
        """
        Returns the continuous flow names carried on BOTH sides, judging nothing.

        The same list :meth:`get_identity_transfer_flows` returns, without the
        R31 model check: the check belongs to the production sweep, which is
        where a lost quantity actually happens and where it is already reported.
        The demand sweep uses this one, so that a component replacing production
        with an equation of its own -- and therefore never transferring anything
        -- is not refused for a mismatch that has no consequence.

        Returns
        -------
        list of str
            The matched flow names, in input declaration order.
        """
        flows_out = self.flows_continuous_out

        return [name for name in self.flows_continuous_in if name in flows_out]

    @staticmethod
    def continuous_flow_is_connected(flow, port):
        """
        Tells whether anything is wired to a continuous flow's port.

        Read through the flow's reference, the only endpoint carrying a
        connection count: the values arriving on an input, the demands
        published by the consumers on an output. Both travel over the single
        bidirectional message box of the port, so either one counts the
        connections of the port itself.

        Parameters
        ----------
        flow : muscadet.flow_continuous.FlowContinuous
            The flow to test.
        port : str
            ``"in"`` or ``"out"``.

        Returns
        -------
        bool
        """
        reference = flow.var_in if port == "in" else flow.var_demand

        return reference is not None and reference.nbCnx() > 0

    def apply_consumption(self, consumption):
        """
        Writes what the rules drew from the input capacities (KTD13, hop 2).

        A flow the rules consume without a capacity buffering it has nothing to
        record: it was drawn straight from the connections feeding it.

        Parameters
        ----------
        consumption : dict
            ``{flow name: rate drawn}``.
        """
        for flow_name, rate in consumption.items():
            capacity = self.get_capacity_of_flow(flow_name, "in")
            if capacity is not None:
                capacity.set_outflow(flow_name, float(rate))

    def apply_production(self, production):
        """
        Writes production onto the output flows (KTD13, hops 3 and 4).

        With a capacity interposed on the output side, **the capacity replaces
        the flow as the counterparty of the rules**: production enters the
        capacity, and the output flow carries what the capacity serves onward.
        Without one, the flow carries the production directly.

        What a flow carries is the lesser of what was produced and what the
        consumers asked for (R6, KD4), and that quantity is then split among
        them by the output's allocation policy (R16).

        Each rate is first multiplied by the **effective rate** of the output it
        is produced on (R18): what the failure modes bearing on that output
        leave of it, the minimum over their derating variables (R20). A rate of
        0 is a total loss of production -- a continuous output carries no
        separate boolean availability gate (R19, KD10).

        Parameters
        ----------
        production : dict
            ``{flow name: rate produced}``.
        """
        # Grouped by capacity: several flows held in ONE volume are drawn from
        # it together, so their requests must all be known before any of them is
        # served (R35).
        buffered = {}

        for flow_name, rate in production.items():
            flow = self.flows_out.get(flow_name)

            # Only a continuous output carries a rate. A discrete output named
            # by a rule keeps its boolean production condition.
            if not isinstance(flow, FlowContinuous):
                continue

            # Derating (R18): what the rules computed, times what the failure
            # modes bearing on this output leave of it. Applied HERE, before the
            # demand is reconciled and before a capacity is filled, so a derated
            # output fills its buffer more slowly rather than draining it faster.
            rate = float(rate) * flow.get_effective_rate()

            request = self.get_output_request(flow, rate)
            capacity = self.get_capacity_of_flow(flow_name, "out")

            if capacity is None:
                self.deliver_output(flow, min(rate, request))
                continue

            # Hop 3: production enters the capacity.
            capacity.set_inflow(flow_name, rate)
            buffered.setdefault(capacity.name, (capacity, {}))[1][flow_name] = request

        # Hop 4: the output flows draw on their capacity.
        for capacity, requests in buffered.values():
            for flow_name, served in self.draw_from_capacity(
                capacity, requests
            ).items():
                capacity.set_outflow(flow_name, served)
                self.deliver_output(self.flows_out[flow_name], served)

    def get_output_request(self, flow, rate):
        """
        Returns what an output is asked to deliver this step.

        The demand published by the consumers, and the produced ``rate`` when
        nothing is connected: an output nobody asks anything of delivers what it
        produces, exactly as it did before demand existed. A capacity behind it
        does not change that -- an unconnected output is a modelled sink, so
        what a buffered one produces travels straight through the volume rather
        than accumulating in it. A tank stocks up when what DRAWS on it asks for
        less than what arrives, which is what a fill claim arranges (R36).
        """
        demand = flow.get_demand_bound()

        return rate if math.isinf(demand) else max(float(demand), 0.0)

    def draw_from_capacity(self, capacity, requests):
        """
        Returns what an output capacity serves for each flow it holds (R7, R35).

        What currently transits through the capacity passes straight on; anything
        asked for BEYOND that comes out of the stock, and a stock of several
        flows is drawn at its raw-quantity composition (R35). One volume holding
        several constituents therefore cannot serve a pure one: asking for more
        of one than its share of the draw allows serves only that share.

        Reduces exactly to "an empty capacity serves what transits through it,
        a stocked one serves what is asked for" when it holds a single flow.

        Parameters
        ----------
        capacity : muscadet.capacity.Capacity
            The capacity sitting on the output side.
        requests : dict
            ``{flow name: quantity asked for}``, over the flows it holds.

        Returns
        -------
        dict
            ``{flow name: quantity served}``.
        """
        transit = {name: capacity.get_inflow(name) for name in requests}

        # What the stock is asked for, over and above what transits.
        beyond = sum(max(requests[name] - transit[name], 0.0) for name in requests)
        draw = capacity.split_draw(beyond)

        return {
            name: max(min(requests[name], transit[name] + draw.get(name, 0.0)), 0.0)
            for name in requests
        }

    def deliver_output(self, flow, quantity):
        """
        Delivers ``quantity`` on a continuous output and splits it (R16, R17).

        The flow's variable carries the TOTAL delivered -- one variable is
        exported to every connection -- and the split among the consumers is
        held on the flow itself, for each of them to read its own share back.
        """
        quantity = max(float(quantity), 0.0)

        flow.var_fed.setValue(quantity)
        self.allocate_output(flow, quantity)

        return quantity

    def allocate_output(self, flow, available):
        """
        Splits what an output delivers among its consumers (R16, R17).

        The component-level override point of the allocation: a component whose
        split depends on something no policy can express -- its own state, a
        measurement it reads -- overrides this rather than declaring a policy.
        The declared policies and the flow-level Python rule both live on the
        flow, in ``FlowContinuousOut.get_allocation_split``.

        Parameters
        ----------
        flow : muscadet.flow_continuous.FlowContinuousOut
            The output being delivered.
        available : float
            What it delivers this step.

        Returns
        -------
        dict
            ``{consumer component name: quantity}``.
        """
        return flow.allocate(available)

    def set_flows(self, **kwargs):
        """
        Finalizes flow setup by configuring variables, message boxes, and automata.

        This method completes the flow initialization process by:
        1. Adding backend variables for each flow
        2. Setting up message boxes for inter-component communication
        3. Configuring sensitive methods for automatic updates
        4. Creating flow-specific automata
        5. Optionally adding default failure automata for output flows

        The method processes all flows (input and output) and ensures they are
        properly integrated with the simulation backend. For output flows with
        default automata enabled, it creates a basic failure mode with extremely
        low failure rates.

        Parameters
        ----------
        **kwargs : dict
            Additional parameters for flow setup (currently unused but reserved
            for future extensions)

        Notes
        -----
        This method should be called after all flows have been added to the
        component. It's typically called automatically during component
        initialization unless partial_init=True is specified.

        The default automata feature (has_default_out_automata) creates a
        basic ok/nok state machine for each output flow with negligible
        failure rates (1e-100), primarily for testing and demonstration purposes.
        """
        flow_list = list(self.flows_in.values()) + list(self.flows_out.values())

        for flow in flow_list:
            # Complete flow setup process
            flow.add_variables(self)
            flow.add_mb(self)
            flow.update_sensitive_methods(self)
            flow.add_automata(self)

            if isinstance(flow, FlowContinuous):
                # A continuous flow's value is written from inside an equation
                # method -- by the production sweep on an output, by the
                # aggregating sensitive method on an input. PyCATSHOO refuses
                # ``setValue`` on a variable its solver does not know about
                # while the differential system is being resolved, so both
                # endpoints are declared EXPLICIT: computed alongside the ODE
                # system, exactly like a capacity's fill. Discrete flows never
                # register -- a purely discrete model must stay what it was,
                # PDMP manager included.
                self.system().pdmp_add_explicit_variable(flow.var_fed)

            if isinstance(flow, FlowContinuousIn):
                # Same reason, for the same solver: the demand sweep writes the
                # demand an input publishes upstream. Only an INPUT holds a
                # demand variable -- an output holds a reference on the demands
                # its consumers publish, and a reference is never written.
                self.system().pdmp_add_explicit_variable(flow.var_demand)

            # Add default failure automata for output flows if enabled
            if self.has_default_out_automata and isinstance(flow, FlowDiscreteOut):
                self.add_atm2states(
                    flow.name,
                    st1="ok",
                    st2="nok",
                    init_st2=False,
                    cond_occ_12=True,
                    occ_law_12={"cls": "exp", "rate": 1e-100},
                    occ_interruptible_12=True,
                    effects_12=[(flow.var_fed_available.basename(), False)],
                    cond_occ_21=True,
                    occ_law_21={"cls": "exp", "rate": 1e-100},
                    occ_interruptible_21=True,
                    effects_21=[],
                )

    def add_automaton_flow(self, aut):
        """
        Adds an automaton to the component.

        Parameters
        ----------
        aut : dict
            The automaton to add.
        """

        aut_bis = cod3s.PycAutomaton(**aut)
        aut_bis.update_bkd(self)

        self.automata_d[aut_bis.name] = aut_bis

    def compute_effects_tuples(self, effects_str=None):
        """
        Computes the effects tuples from a string.

        Two forms of entry, comma-separated:

        * **boolean** -- ``"f1"`` sets every matching variable True, ``"!f1"``
          sets it False. Resolved against the component's variable basenames,
          exactly as in 1.x.
        * **numeric** -- ``"X=0.5"`` carries the value 0.5 (R18). Resolved
          against the CONTINUOUS OUTPUT flow names first, since a number
          declared against a flow is a derating declaration and
          :meth:`resolve_mode_effects` rewrites it onto the declaring mode's own
          derating variable; a pattern matching no continuous output falls back
          to the variable basenames, so a numeric parameter stays reachable.

        Parameters
        ----------
        effects_str : str, optional
            The effects string.

        Returns
        -------
        list of tuples
            The effects tuples, ``(pattern or variable basename, value)``.
        """
        if not effects_str:
            return []

        effects_strlist = effects_str.split(",")

        effects_tuplelist = []
        for effects in effects_strlist:
            pattern, sep, value_str = effects.partition("=")

            if sep:
                pattern = pattern.strip()
                try:
                    effects_val = float(value_str)
                except ValueError:
                    raise ValueError(
                        f"Object {self.name()}: effect {effects!r} declares a "
                        f"non-numeric value {value_str.strip()!r}; the "
                        "'pattern=value' form carries a number (a derating "
                        "rate), the boolean form is written 'pattern' or "
                        "'!pattern'"
                    )

                effects_tuplelist_cur = [
                    (flow_name, effects_val)
                    for flow_name in self.flows_continuous_out
                    if re.search(pattern, flow_name)
                ]

                if not effects_tuplelist_cur:
                    effects_tuplelist_cur = [
                        (var.basename(), effects_val)
                        for var in self.variables()
                        if re.search(pattern, var.basename())
                    ]

                effects_tuplelist += effects_tuplelist_cur
                continue

            effects_val = not effects.startswith("!")
            effects_bis = effects.replace("!", "")
            effects_tuplelist_cur = [
                (var.basename(), effects_val)
                for var in self.variables()
                if re.search(effects_bis, var.basename())
            ]

            effects_tuplelist += effects_tuplelist_cur

        return effects_tuplelist

    # ------------------------------------------------------------------
    # Derating: what a failure mode leaves of a continuous output
    # ------------------------------------------------------------------

    def match_continuous_outputs(self, pattern):
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
            for flow_name in self.flows_continuous_out
            if re.search(pattern, flow_name)
            or re.search(pattern, f"{flow_name}_fed_out")
        ]

    def add_derating(self, mode_name, flow_name):
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
        flow = self.flows_out.get(flow_name)

        if not isinstance(flow, FlowContinuousOut):
            raise ValueError(
                f"Object {self.name()}: cannot derate {flow_name!r}: it is not "
                "a continuous output flow of this component -- only a "
                "continuous output carries a rate"
            )

        return flow.register_derating(self, mode_name)

    def derating_vars_of(self, mode_name):
        """
        Returns the derating variables ``mode_name`` owns, keyed by basename.

        The discovery side of R18: an output knows which modes derate it, so a
        mode can be asked back what it derates without holding a registry of
        its own.
        """
        return {
            flow.derating[mode_name].basename(): flow.derating[mode_name]
            for flow in self.flows_continuous_out.values()
            if mode_name in flow.derating
        }

    def continuous_endpoint_names(self):
        """
        Returns the basenames of the continuous variables the sweeps own.

        What a continuous flow carries, and what an input demands: written by
        the production and demand equations at every integration step. A mode
        clamping one of them would be overwritten within the step, so a mode
        reaches a continuous output through its derating variable and nowhere
        else (R19).
        """
        names = set()

        for flow in list(self.flows_in.values()) + list(self.flows_out.values()):
            if not isinstance(flow, FlowContinuous):
                continue
            for var in (flow.var_fed, flow.var_demand):
                if var is not None:
                    names.add(var.basename())

        return names

    def resolve_mode_effects(self, mode_name, effects):
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
            flow_names = self.match_continuous_outputs(pattern)

            if not flow_names:
                patterns.append((pattern, value))
                continue

            derated += [
                (self.add_derating(mode_name, flow_name), float(value))
                for flow_name in flow_names
            ]

        solver_owned = self.continuous_endpoint_names()

        return [
            (var, value)
            for var, value in self.pat_to_var_value_list(*patterns)
            if var.basename() not in solver_owned
        ] + derated

    def release_deratings(self, mode_name, *var_value_lists):
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
        derating_vars = self.derating_vars_of(mode_name)

        for var_value_list in var_value_lists:
            clamped = {var.basename() for var, _ in var_value_list}
            var_value_list += [
                (var, NOMINAL_RATE)
                for basename, var in derating_vars.items()
                if basename not in clamped
            ]

    def add_atm2states(
        self,
        name,
        st1="absent",
        st2="present",
        init_st2=False,
        cond_occ_12=True,
        occ_law_12=None,
        occ_interruptible_12=True,
        effects_12=[],
        cond_occ_21=True,
        occ_law_21=None,
        occ_interruptible_21=True,
        effects_21=[],
    ):
        """
        Adds a two-state automaton to the component.

        Parameters
        ----------
        name : str
            The name of the automaton.
        st1 : str, optional
            The name of the first state (default is "absent").
        st2 : str, optional
            The name of the second state (default is "present").
        init_st2 : bool, optional
            Indicates if the initial state is the second state (default is False).
        cond_occ_12 : bool or str, optional
            The condition for the transition from the first state to the second state (default is True).
        occ_law_12 : dict, optional
            The occurrence law for the transition from the first state to the second state (default is {"cls": "delay", "time": 0}).
        occ_interruptible_12 : bool, optional
            Indicates if the transition from the first state to the second state is interruptible (default is True).
        effects_12 : list of tuples, optional
            The effects of the transition from the first state to the second state (default is []).
        cond_occ_21 : bool or str, optional
            The condition for the transition from the second state to the first state (default is True).
        occ_law_21 : dict, optional
            The occurrence law for the transition from the second state to the first state (default is {"cls": "delay", "time": 0}).
        occ_interruptible_21 : bool, optional
            Indicates if the transition from the second state to the first state is interruptible (default is True).
        effects_21 : list of tuples, optional
            The effects of the transition from the second state to the first state (default is []).

        Notes
        -----
        An effect naming a CONTINUOUS OUTPUT is a **derating** declaration
        (R18): ``effects_12=[("X", 0.5)]`` leaves half of what ``X`` produces
        while the mode holds. It is rewritten onto the variable this mode owns
        on that output, ``{name}_derating_X``, so two modes derating ``X``
        compose by minimum (R20) instead of overwriting one another. The
        opposite state returns the variable to 1 unless it declares a value of
        its own -- see :meth:`release_deratings`.

        Everything else keeps the 1.x resolution: a regex over the component's
        variable basenames, and a boolean clamped while the state holds.
        """

        # Normalise the occurrence-law sentinels, exactly as
        # ``cod3s.PycComponent.add_aut2st`` does. They MUST be rebuilt on every
        # call: ``TransitionModel.sanitize_occ_law`` rewrites their ``cls``
        # entry in place and ``ObjCOD3S.from_dict`` then pops it, so a shared
        # default mapping is emptied by its first use and the second defaulted
        # call raises "Missing attribute 'cls'".
        if occ_law_12 is None:
            occ_law_12 = {"cls": "delay", "time": 0}
        if occ_law_21 is None:
            occ_law_21 = {"cls": "delay", "time": 0}

        st1_name = f"{name}_{st1}"
        st2_name = f"{name}_{st2}"

        aut = cod3s.PycAutomaton(
            name=f"{self.name()}_{name}",
            states=[st1_name, st2_name],
            init_state=st2_name if init_st2 else st1_name,
            transitions=[
                {
                    "name": f"{name}_{st1}_{st2}",
                    "source": f"{st1_name}",
                    "target": f"{st2_name}",
                    "is_interruptible": occ_interruptible_12,
                    "occ_law": occ_law_12,
                },
                {
                    "name": f"{name}_{st2}_{st1}",
                    "source": f"{st2_name}",
                    "target": f"{st1_name}",
                    "is_interruptible": occ_interruptible_21,
                    "occ_law": occ_law_21,
                },
            ],
        )

        aut.update_bkd(self)

        # Jump 1 -> 2
        # -----------
        # Conditions
        trans_name_12 = f"{name}_{st1}_{st2}"
        if isinstance(cond_occ_12, bool):
            aut.get_transition_by_name(trans_name_12)._bkd.setCondition(cond_occ_12)

        elif isinstance(cond_occ_12, str):
            aut.get_transition_by_name(trans_name_12)._bkd.setCondition(
                self.variable(cond_occ_12)
            )
        else:
            raise ValueError(
                f"Condition '{cond_occ_12}' for transition {trans_name_12} not supported"
            )

        # Effects
        #
        # Both directions are resolved BEFORE either is wired: an effect naming
        # a continuous output allocates the derating variable this mode owns on
        # it (R18), and the mode must then return that variable to nominal on
        # its other state -- see ``release_deratings`` below.
        var_value_list_12 = self.resolve_mode_effects(name, effects_12)
        var_value_list_21 = self.resolve_mode_effects(name, effects_21)
        self.release_deratings(name, var_value_list_12, var_value_list_21)

        st2_bkd = aut.get_state_by_name(st2_name)._bkd
        if len(var_value_list_12) > 0:

            def sensitive_method_12():
                if st2_bkd.isActive():
                    [var.setValue(value) for var, value in var_value_list_12]

            # setattr(comp._bkd, method_name, sensitive_method)
            method_name_12 = f"effect_{self.name()}_{trans_name_12}"
            aut._bkd.addSensitiveMethod(method_name_12, sensitive_method_12)
            [
                var.addSensitiveMethod(method_name_12, sensitive_method_12)
                for var, value in var_value_list_12
            ]

        # Jump 2 -> 1
        # -----------
        # Conditions
        trans_name_21 = f"{name}_{st2}_{st1}"
        if isinstance(cond_occ_21, bool):
            aut.get_transition_by_name(trans_name_21)._bkd.setCondition(cond_occ_21)

        elif isinstance(cond_occ_21, str):
            aut.get_transition_by_name(trans_name_21)._bkd.setCondition(
                self.variable(cond_occ_21)
            )
        else:
            raise ValueError(
                f"Condition '{cond_occ_21}' for transition {trans_name_21} not supported"
            )
        # Effects
        st1_bkd = aut.get_state_by_name(st1_name)._bkd
        if len(var_value_list_21) > 0:

            def sensitive_method_21():
                if st1_bkd.isActive():
                    [var.setValue(value) for var, value in var_value_list_21]

            # setattr(comp._bkd, method_name, sensitive_method)
            method_name_21 = f"effect_{self.name()}_{trans_name_21}"
            aut._bkd.addSensitiveMethod(method_name_21, sensitive_method_21)
            [
                var.addSensitiveMethod(method_name_21, sensitive_method_21)
                for var, value in var_value_list_21
            ]

        # Update automata dict
        # --------------------
        self.automata_d[aut.name] = aut

    def add_exp_failure_mode(
        self,
        name,
        failure_state="occ",
        failure_cond=True,
        failure_rate=0,
        failure_effects=[],
        failure_param_name="lambda",
        repair_state="rep",
        repair_cond=True,
        repair_rate=0,
        repair_effects=[],
        repair_param_name="mu",
    ):
        """
        Adds an exponential failure mode to the component.

        Parameters
        ----------
        name : str
            The name of the failure mode.
        failure_cond : bool, optional
            The condition for the failure (default is True).
        failure_rate : float, optional
            The rate of failure (default is 0).
        failure_effects : list of tuples, optional
            The effects of the failure (default is []).
        failure_param_name : str, optional
            The name of the failure parameter (default is "lambda").
        repair_cond : bool, optional
            The condition for the repair (default is True).
        repair_rate : float, optional
            The rate of repair (default is 0).
        repair_effects : list of tuples, optional
            The effects of the repair (default is []).
        repair_param_name : str, optional
            The name of the repair parameter (default is "mu").

        Notes
        -----
        ``failure_effects=[("X", 0.5)]`` on a continuous output ``X`` derates it
        (R18): see :meth:`add_atm2states`, which this funnels through.
        """

        # Create lambda/mu parameter for failure mode name
        failure_rate_name = f"{name}_{failure_param_name}"
        self.params[failure_rate_name] = self.addVariable(
            failure_rate_name, pyc.TVarType.t_double, failure_rate
        )
        repair_rate_name = f"{name}_{repair_param_name}"
        self.params[repair_rate_name] = self.addVariable(
            repair_rate_name, pyc.TVarType.t_double, repair_rate
        )

        self.add_atm2states(
            name=name,
            st1=repair_state,
            st2=failure_state,
            init_st2=False,
            cond_occ_12=failure_cond,
            occ_law_12={"cls": "exp", "rate": self.params[failure_rate_name]},
            occ_interruptible_12=True,
            effects_12=failure_effects,
            cond_occ_21=repair_cond,
            occ_law_21={"cls": "exp", "rate": self.params[repair_rate_name]},
            occ_interruptible_21=True,
            effects_21=repair_effects,
        )

    def add_delay_failure_mode(
        self,
        name,
        failure_state="occ",
        failure_cond=True,
        failure_time=0,
        failure_effects=[],
        failure_param_name="ttf",
        repair_state="rep",
        repair_cond=True,
        repair_time=0,
        repair_effects=[],
        repair_param_name="ttr",
    ):
        """
        Add a delay failure mode to the component.

        Parameters
        ----------
        name : str
            The name of the failure mode.
        failure_cond : bool, optional
            The condition for the failure (default is True).
        failure_time : float, optional
            The time to failure (default is 0).
        failure_effects : list of tuples, optional
            The effects of the failure (default is []).
        failure_param_name : str, optional
            The name of the failure parameter (default is "ttf").
        repair_cond : bool, optional
            The condition for the repair (default is True).
        repair_time : float, optional
            The time to repair (default is 0).
        repair_effects : list of tuples, optional
            The effects of the repair (default is []).
        repair_param_name : str, optional
            The name of the repair parameter (default is "ttr").

        Notes
        -----
        ``failure_effects=[("X", 0.5)]`` on a continuous output ``X`` derates it
        (R18): see :meth:`add_atm2states`, which this funnels through.
        """

        # Create lambda/mu parameter for failure mode name
        failure_time_name = f"{name}_{failure_param_name}"
        self.params[failure_time_name] = self.addVariable(
            failure_time_name, pyc.TVarType.t_double, failure_time
        )
        repair_time_name = f"{name}_{repair_param_name}"
        self.params[repair_time_name] = self.addVariable(
            repair_time_name, pyc.TVarType.t_double, repair_time
        )

        self.add_atm2states(
            name=name,
            st1=repair_state,
            st2=failure_state,
            init_st2=False,
            cond_occ_12=failure_cond,
            occ_law_12={"cls": "delay", "time": self.params[failure_time_name]},
            occ_interruptible_12=True,
            effects_12=failure_effects,
            cond_occ_21=repair_cond,
            occ_law_21={"cls": "delay", "time": self.params[repair_time_name]},
            occ_interruptible_21=True,
            effects_21=repair_effects,
        )


class ObjFailureMode(cod3s.PycComponent):
    """
    A component that models failure modes affecting multiple target components.

    This class creates automata-based failure modes that can affect one or more target
    components simultaneously. It supports different orders of failure (affecting 1, 2,
    or more components at once) and allows customization of failure and repair conditions,
    parameters, and effects.

    The failure mode creates all possible combinations of target components up to the
    specified order and generates corresponding automata with failure and repair transitions.
    Each automaton is named using customizable prefixes to distinguish between different
    target combinations.

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
        Dictionary mapping flow names to their values when failure occurs
    repair_effects : dict
        Dictionary mapping flow names to their values when repair occurs
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
        Custom name for the target combination. If None, auto-generated from targets
    failure_state : str, optional
        Name of the failure state (default: "occ")
    failure_cond : bool or callable, optional
        Condition that must be met for failure to occur (default: True)
    failure_effects : dict, optional
        Effects applied when failure occurs (default: {})
    failure_param_name : str or list[str], optional
        Names of failure parameters (default: [])
    failure_param : list, optional
        Values of failure parameters (default: [])
    repair_state : str, optional
        Name of the repair state (default: "rep")
    repair_cond : bool or callable, optional
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
        Template for transition/automaton name suffixes (default: "__cc_{target_comb_u}")
        Available placeholders: {target_comb}, {target_binary}, {target_comb_u}, {order}, {order_max}
    trans_name_prefix_fun : callable, optional
        Custom function to generate transition/automaton name suffixes. Takes keyword arguments:
        target_set_idx, target_comb, target_binary, target_comb_u, order, order_max
    drop_inactive_automata : bool, optional
        Whether to skip creating automata with inactive occurrence laws (default: True)
    step : optional
        Step parameter for automaton transitions

    Methods
    -------
    get_failure_cond(target_comps, failure_param)
        Creates a failure condition function for the given target components
    get_repair_cond(target_comps, repair_param)
        Creates a repair condition function for the given target components
    set_default_failure_param_name()
        Sets default failure parameter names (to be overridden in subclasses)
    set_default_repair_param_name()
        Sets default repair parameter names (to be overridden in subclasses)
    _factorize_target_names(targets, rep_char="X", ignored_char=["_"], concat_char=["__"])
        Static method to create factorized names from target lists

    Examples
    --------
    Basic failure mode with default naming:

    >>> fm = ObjFailureMode(
    ...     fm_name="common_cause",
    ...     targets=["pump1", "pump2"],
    ...     failure_effects={"flow": False},
    ...     repair_effects={"flow": True}
    ... )
    # Creates automata: "common_cause__cc_1_2", "common_cause__cc_1", "common_cause__cc_2"

    Using custom trans_name_prefix with binary representation:

    >>> fm = ObjFailureMode(
    ...     fm_name="failure",
    ...     targets=["comp1", "comp2", "comp3"],
    ...     trans_name_prefix="__bin_{target_binary}",
    ...     failure_effects={"output": False}
    ... )
    # Creates automata like: "failure__bin_110", "failure__bin_101", etc.

    Using custom trans_name_prefix_fun for complex naming:

    >>> def custom_naming(target_set_idx, target_comb, target_binary, **kwargs):
    ...     return f"__custom_{len(target_set_idx)}of{kwargs['order_max']}_{target_binary}"
    ...
    >>> fm = ObjFailureMode(
    ...     fm_name="advanced",
    ...     targets=["A", "B", "C"],
    ...     trans_name_prefix_fun=custom_naming,
    ...     failure_effects={"signal": False}
    ... )
    # Creates automata like: "advanced__custom_2of3_110", "advanced__custom_1of3_100", etc.

    Using underscore-separated target combinations:

    >>> fm = ObjFailureMode(
    ...     fm_name="mode",
    ...     targets=["unit1", "unit2", "unit3"],
    ...     trans_name_prefix="__targets_{target_comb_u}",
    ...     failure_effects={"active": False}
    ... )
    # Creates automata like: "mode__targets_1_2", "mode__targets_2_3", etc.
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

        self.fm_name = fm_name
        self.targets = [targets] if isinstance(targets, str) else targets
        if target_name is None and len(self.targets) == 1:
            target_name = self.targets[0]
        self.target_name = target_name or self._factorize_target_names(targets)

        comp_name = f"{self.target_name}__{self.fm_name}"

        super().__init__(comp_name, **kwargs)
        # if self.system().name() == "003":
        #     __import__("ipdb").set_trace()

        order_max = len(self.targets)

        self.failure_cond = copy.deepcopy(failure_cond)
        self.repair_cond = copy.deepcopy(repair_cond)

        self.failure_state = failure_state
        self.repair_state = repair_state

        self.step = step
        self.var_params = {}
        self.failure_effects = copy.deepcopy(failure_effects)
        self.repair_effects = copy.deepcopy(repair_effects)
        self.failure_param_name = (
            [failure_param_name]
            if isinstance(failure_param_name, str)
            else copy.deepcopy(failure_param_name)
        )
        self.set_default_failure_param_name()

        self.repair_param_name = (
            [repair_param_name]
            if isinstance(repair_param_name, str)
            else copy.deepcopy(repair_param_name)
        )
        self.set_default_repair_param_name()

        self.param_name_order_prefix = param_name_order_prefix
        self.trans_name_prefix = trans_name_prefix
        self.trans_name_prefix_fun = trans_name_prefix_fun

        effect_flows = list(
            set(list(self.failure_effects.keys()) + list(self.repair_effects.keys()))
        )

        self.failure_param = (
            [failure_param]
            if not isinstance(failure_param, list)
            else copy.deepcopy(failure_param)
        )
        failure_param_diff = len(self.targets) - len(self.failure_param)
        if failure_param_diff > 0:
            self.set_default_failure_param()
        elif failure_param_diff < 0:
            raise ValueError(
                f"Failure mode of order {order_max} but you provide {len(self.failure_param)} failure parameters: {self.failure_param}"
            )

        self.repair_param = (
            [repair_param]
            if not isinstance(repair_param, list)
            else copy.deepcopy(repair_param)
        )
        repair_param_diff = len(self.targets) - len(self.repair_param)
        if repair_param_diff > 0:
            self.set_default_repair_param()
        elif repair_param_diff < 0:
            raise ValueError(
                f"Failure mode of order {order_max} but you provide {len(self.repair_param)} repair parameters: {self.repair_param}"
            )

        for order in range(1, order_max + 1):

            failure_param_cur = self.failure_param[order - 1]
            if not isinstance(failure_param_cur, tuple):
                failure_param_cur = (failure_param_cur,)

            failure_var_params_cur = {}
            for failure_param_name_cur, param_value in zip(
                self.failure_param_name, failure_param_cur
            ):
                failure_param_name_cur_tmp = failure_param_name_cur
                if order_max > 1:
                    failure_param_name_cur_tmp += self.param_name_order_prefix.format(
                        order=order, order_max=order_max
                    )

                failure_var_param = self.addVariable(
                    failure_param_name_cur_tmp, pyc.TVarType.t_double, param_value
                )
                failure_var_params_cur.update(
                    {failure_param_name_cur: failure_var_param}
                )

            repair_param_cur = self.repair_param[order - 1]
            if not isinstance(repair_param_cur, tuple):
                repair_param_cur = (repair_param_cur,)

            repair_var_params_cur = {}
            for repair_param_name_cur, param_value in zip(
                self.repair_param_name, repair_param_cur
            ):
                repair_param_name_cur_tmp = repair_param_name_cur
                if order_max > 1:
                    repair_param_name_cur_tmp += self.param_name_order_prefix.format(
                        order=order, order_max=order_max
                    )

                repair_var_param = self.addVariable(
                    repair_param_name_cur_tmp, pyc.TVarType.t_double, param_value
                )
                repair_var_params_cur.update({repair_param_name_cur: repair_var_param})

            if (
                drop_inactive_automata
                and not self.is_occ_law_failure_active(failure_var_params_cur)
                and not self.is_occ_law_repair_active(repair_var_params_cur)
            ):
                continue

            for target_set_idx in itertools.combinations(range(order_max), order):

                failure_effects_cur = []
                for target_idx in target_set_idx:
                    comp_target_cur = self.system().component(self.targets[target_idx])
                    for flow_name_pat, val in self.failure_effects.items():
                        if len(flow_name_pat) == 0:
                            continue
                        fo_found = False
                        for fo_name, fo in comp_target_cur.flows_out.items():
                            if re.search(f"^{flow_name_pat}$", fo_name):
                                failure_effects_cur.append(
                                    {
                                        "var": fo.var_fed_available,
                                        "value": val,
                                    }
                                )
                                fo_found = True
                        if not fo_found:
                            raise ValueError(
                                f"[Component {str(comp_target_cur)}]\n[{comp_target_cur.name()}: Failure effects of mode {fm_name}] Pattern {flow_name_pat} does not match any flow out"
                            )
                repair_effects_cur = []
                for target_idx in target_set_idx:
                    comp_target_cur = self.system().component(self.targets[target_idx])
                    for flow_name_pat, val in self.repair_effects.items():
                        if len(flow_name_pat) == 0:
                            continue
                        fo_found = False
                        for fo_name, fo in comp_target_cur.flows_out.items():
                            if re.search(f"^{flow_name_pat}$", fo_name):
                                repair_effects_cur.append(
                                    {
                                        "var": fo.var_fed_available,
                                        "value": val,
                                    }
                                )
                                fo_found = True
                        if not fo_found:
                            raise ValueError(
                                f"[Component {str(comp_target_cur)}]\n[{comp_target_cur.name()}: Repair effects of mode {fm_name}] Pattern {flow_name_pat} does not match any flow out"
                            )

                # repair_effects_cur = [
                #     {
                #         "var": self.system()
                #         .component(self.targets[target_idx])
                #         .flows_out[flow_name]
                #         .var_fed_available,
                #         "value": val,
                #     }
                #     for target_idx in target_set_idx
                #     for flow_name, val in self.repair_effects.items()
                # ]

                failure_state_name_cur = self.failure_state
                repair_state_name_cur = self.repair_state
                aut_name_cur = fm_name
                if order_max > 1:
                    target_comb = "".join([str(i + 1) for i in target_set_idx])
                    target_comb_u = "_".join([str(i + 1) for i in target_set_idx])
                    target_binary = "".join(
                        ["1" if i in target_set_idx else "0" for i in range(order_max)]
                    )
                    if callable(self.trans_name_prefix_fun):
                        trans_name_prefix_cur = self.trans_name_prefix_fun(
                            target_set_idx=target_set_idx,
                            target_comb=target_comb,
                            target_binary=target_binary,
                            target_comb_u=target_comb_u,
                            order=order,
                            order_max=order_max,
                        )
                    else:
                        trans_name_prefix_cur = self.trans_name_prefix.format(
                            target_comb=target_comb,
                            target_binary=target_binary,
                            target_comb_u=target_comb_u,
                            order=order,
                            order_max=order_max,
                        )
                    aut_name_cur += trans_name_prefix_cur
                    failure_state_name_cur += trans_name_prefix_cur
                    repair_state_name_cur += trans_name_prefix_cur

                target_comps_cur = [
                    self.system().component(self.targets[idx]) for idx in target_set_idx
                ]

                failure_cond_cur = self.get_failure_cond(
                    target_comps_cur, failure_var_params_cur
                )
                repair_cond_cur = self.get_repair_cond(
                    target_comps_cur, repair_var_params_cur
                )

                self.add_aut2st(
                    name=aut_name_cur,
                    st1=repair_state_name_cur,
                    st2=failure_state_name_cur,
                    init_st2=False,
                    trans_name_12_fmt="{st2}",
                    cond_occ_12=failure_cond_cur,
                    occ_law_12=self.set_occ_law_failure(failure_var_params_cur),
                    occ_interruptible_12=True,
                    effects_st2=failure_effects_cur,
                    effects_st2_format="records",
                    trans_name_21_fmt="{st1}",
                    cond_occ_21=repair_cond_cur,
                    occ_law_21=self.set_occ_law_repair(repair_var_params_cur),
                    occ_interruptible_21=True,
                    effects_st1=repair_effects_cur,
                    effects_st1_format="records",
                    step=self.step,
                )

    def get_failure_cond(self, target_comps, failure_param):
        if self.failure_cond is not True:

            def failure_cond_fun():
                return all(
                    [
                        comp.flows_in[flow].var_fed.value() == flow_value
                        for flow, flow_value in self.failure_cond.items()
                        for comp in target_comps
                    ]
                )

            return failure_cond_fun
        else:
            return True

    def get_repair_cond(self, target_comps, repair_param):
        if self.repair_cond is not True:

            def repair_cond_fun():
                return all(
                    [
                        comp.flows_in[flow].var_fed.value() == flow_value
                        for flow, flow_value in self.repair_cond.items()
                        for comp in target_comps
                    ]
                )

            return repair_cond_fun
        else:
            return True

    # TO BE OVERLOADED IF NEEDED
    def set_default_failure_param_name(self):
        pass

    # TO BE OVERLOADED IF NEEDED
    def set_default_repair_param_name(self):
        pass

    def is_occ_law_failure_active(self, params):
        return True

    def is_occ_law_repair_active(self, params):
        return True

    @staticmethod
    def _factorize_target_names(
        targets: list[str], rep_char="X", ignored_char=["_"], concat_char=["__"]
    ) -> str:
        """
        Creates a factorized name from a list of target component names.

        This utility method generates a compact representation of multiple target
        names by identifying common patterns and replacing differing characters
        with a placeholder. This is particularly useful for failure modes that
        affect multiple similar components.

        The algorithm works as follows:
        1. If targets have different lengths, concatenate with separator
        2. For same-length targets, compare character by character
        3. Keep common characters, replace differences with rep_char
        4. Ignore specified characters (like underscores) during comparison

        Parameters
        ----------
        targets : list[str]
            List of target component names to factorize
        rep_char : str, optional
            Character to use for differing positions (default: "X")
        ignored_char : list[str], optional
            Characters to ignore during comparison (default: ["_"])
        concat_char : list[str], optional
            Characters to use for concatenation when lengths differ (default: ["__"])

        Returns
        -------
        str
            Factorized name representing all targets

        Examples
        --------
        >>> _factorize_target_names(["pump1", "pump2", "pump3"])
        "pumpX"

        >>> _factorize_target_names(["motor_A1", "motor_B1"])
        "motor_X1"

        >>> _factorize_target_names(["component1", "very_long_name"])
        "component1__very_long_name"
        """
        if not targets:
            return ""
        if len(targets) == 1:
            return targets[0]

        first_len = len(targets[0])
        # If targets have different lengths, concatenate them
        if not all(len(t) == first_len for t in targets):
            return concat_char[0].join(targets)

        # Character-by-character comparison for same-length targets
        result_chars = []
        for i in range(first_len):
            ref_char = targets[0][i]

            # Skip ignored characters (keep them as-is)
            if ref_char in ignored_char:
                result_chars.append(ref_char)
                continue

            # Check if character is common across all targets
            is_common = all(t[i] == ref_char for t in targets)

            if is_common:
                result_chars.append(ref_char)
            else:
                result_chars.append(rep_char)

        return "".join(result_chars)


class ObjFailureModeExp(ObjFailureMode):

    def set_default_failure_param_name(self):
        if not self.failure_param_name:
            self.failure_param_name = ["lambda"]

    def set_default_repair_param_name(self):
        if not self.repair_param_name:
            self.repair_param_name = ["mu"]

    def set_default_failure_param(self):
        failure_param_diff = len(self.targets) - len(self.failure_param)
        if failure_param_diff > 0:
            self.failure_param += [(0,)] * failure_param_diff

    def set_default_repair_param(self):
        repair_param_diff = len(self.targets) - len(self.repair_param)
        if repair_param_diff > 0:
            self.repair_param += [(0,)] * repair_param_diff

    def is_occ_law_failure_active(self, params):
        return params[self.failure_param_name[0]].value() > 0

    def is_occ_law_repair_active(self, params):
        return params[self.repair_param_name[0]].value() > 0

    def set_occ_law_failure(self, params):
        return {"cls": "exp", "rate": params[self.failure_param_name[0]]}

    def set_occ_law_repair(self, params):
        return {"cls": "exp", "rate": params[self.repair_param_name[0]]}

    def get_failure_cond(self, target_comps, failure_param):
        if self.failure_cond is not True:

            def failure_cond_fun():
                return failure_param[self.failure_param_name[0]].bValue() and all(
                    [
                        comp.flows_in[flow].var_fed.value() == flow_value
                        for flow, flow_value in self.failure_cond.items()
                        for comp in target_comps
                    ]
                )

        else:

            def failure_cond_fun():
                return failure_param[self.failure_param_name[0]].bValue()

        return failure_cond_fun

    def get_repair_cond(self, target_comps, repair_param):
        if self.repair_cond is not True:

            def repair_cond_fun():
                return repair_param[self.repair_param_name[0]].bValue() and all(
                    [
                        comp.flows_in[flow].var_fed.value() == flow_value
                        for flow, flow_value in self.repair_cond.items()
                        for comp in target_comps
                    ]
                )

        else:

            def repair_cond_fun():
                return repair_param[self.repair_param_name[0]].bValue()

        return repair_cond_fun


class ObjFailureModeDelay(ObjFailureMode):
    def set_default_failure_param_name(self):
        if not self.failure_param_name:
            self.failure_param_name = ["ttf"]

    def set_default_repair_param_name(self):
        if not self.repair_param_name:
            self.repair_param_name = ["ttr"]

    def set_default_failure_param(self):
        failure_param_diff = len(self.targets) - len(self.failure_param)
        if failure_param_diff > 0:
            self.failure_param += [(0,)] * failure_param_diff

    def set_default_repair_param(self):
        repair_param_diff = len(self.targets) - len(self.repair_param)
        if repair_param_diff > 0:
            self.repair_param += [(0,)] * repair_param_diff

    def set_occ_law_failure(self, params):
        return {"cls": "delay", "time": params[self.failure_param_name[0]]}

    def set_occ_law_repair(self, params):
        return {"cls": "delay", "time": params[self.repair_param_name[0]]}
