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
from .flow import FlowIn, FlowOut, FlowOutOnTrigger, FlowOutTempo
import cod3s
import re
import warnings
import copy
import itertools
from colored import fg, attr
import importlib
import typing
import pydantic

# class TransitionEffect(BaseModel):
#     var_name: str = \
#         pydantic.Field(..., description="Variable name")
#     var_value: typing.Any = \
#         pydantic.Field(..., description="Variable value to be affected")


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
    set_flows(**kwargs):
        Sets up the flows for the component.
    pat_to_var_value(*pat_value_list):
        Converts pattern-value pairs to variable-value pairs.
    add_automaton_flow(aut):
        Adds an automaton to the component.
    compute_effects_tuples(effects_str=None):
        Computes the effects tuples from a string.
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
        # self.flows_io = {}  # TODO: Implement FlowIO class

        self.params = {}
        # self.automata = {} Already initialize in COD3S PycComponent
        self.has_default_out_automata = create_default_out_automata

        if partial_init:
            # In this cas you need to explicitly call add_flow and set_flows to
            # Create complete the object creation
            pass
        else:
            # TOREMOVE?: WHAT IS THE POINT TO PASS METADATA HERE SINCE IT IS ALREADY AN ATTRIBUTE ?
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
        """
        flow_specs = copy.deepcopy(flow_specs)

        # Postprocess : var_prod_cond
        if var_prod_cond := flow_specs.get("var_prod_cond"):
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
                        if fcond := self.flows_in.get(flow_disj):
                            flow_disj_tiny = [fcond]
                        elif fcond := self.flows_out.get(flow_disj):
                            flow_disj_tiny = [fcond]
                        else:
                            raise ValueError(
                                f"Object {self.name()}: Flow {flow_disj} does not exist as input nor output flow (you must create it before using it in a FlowOut condition)"
                            )
                    elif isinstance(flow_disj, (list, set, tuple)):
                        flow_disj_tiny = []
                        for flow_name in list(flow_disj):
                            if fcond := self.flows_in.get(flow_name):
                                flow_disj_tiny.append(fcond)
                            elif fcond := self.flows_out.get(flow_name):
                                flow_disj_tiny.append(fcond)
                            else:
                                raise ValueError(
                                    f"Object {self.name()}: Flows {flow_name} does not as input nor output flow (you must create it before using it in a FlowOut condition)"
                                )

                    else:
                        raise ValueError(
                            f"Bad format for production condition structure : {flow_disj}"
                        )
                    var_prod_cond_tiny.append(flow_disj_tiny)
            else:
                raise ValueError(
                    f"Bad format for main conjonctive format of production condition : {var_prod_cond}"
                )

            flow_specs["var_prod_cond"] = var_prod_cond_tiny

        # Postprocess : other attributes...
        if occ_enable_flow := flow_specs.get("occ_enable_flow"):
            occ_clsname = occ_enable_flow.get("cls")
            if "OccDistribution" not in occ_clsname:
                occ_clsname = occ_clsname.capitalize() + "OccDistribution"
                occ_enable_flow["cls"] = occ_clsname

            flow_specs["occ_enable_flow"] = occ_enable_flow

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
            - cls: Flow class name (e.g., "FlowIn", "FlowOut", "FlowOutTempo")
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

        if isinstance(flow, FlowIn):
            if flow.name in self.flows_in:
                raise ValueError(f"Input flow {flow.name} already exists")
            else:
                self.flows_in[flow.name] = flow
        elif isinstance(flow, FlowOut):
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

    # def add_flow_io(self, **params):
    #     """
    #     Adds an input/output flow to the component.

    #     Parameters
    #     ----------
    #     **params : dict
    #         Parameters for the input/output flow.
    #     """
    #     flow_name = params.get("name")
    #     if not (flow_name in self.flows_io):
    #         self.flows_io[flow_name] = FlowIO(**params)
    #     else:
    #         raise ValueError(f"Input/Output flow {flow_name} already exists")

    # DEPRACATED
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
        #     ipdb.set_trace()
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
        flow_name = params.get("name")

        if not (flow_name in self.flows_out):
            self.flows_out[flow_name] = FlowOutOnTrigger(**params)
        else:
            raise ValueError(f"Output (on trigger) flow {flow_name} already exists")

    # def get_flows(self, cls=["In", "IO", "Out", "OutOnTrigger"]):

    #     class_list = [globals()[f"Flow{suffix}"]
    #                   for suffix in cls]
    #     return [flow for flow in self.flows
    #             if isinstance(flows, class_list)]

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
        flow_list = (
            list(self.flows_in.values())
            # + list(self.flows_io.values())  # TODO: Implement FlowIO class
            + list(self.flows_out.values())
        )

        for flow in flow_list:
            # Complete flow setup process
            flow.add_variables(self)
            flow.add_mb(self)
            flow.update_sensitive_methods(self)
            flow.add_automata(self)

            # Add default failure automata for output flows if enabled
            if self.has_default_out_automata and isinstance(flow, FlowOut):
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

        Parameters
        ----------
        effects_str : str, optional
            The effects string.

        Returns
        -------
        list of tuples
            The effects tuples.
        """
        if not effects_str:
            return []

        effects_strlist = effects_str.split(",")

        effects_tuplelist = []
        for effects in effects_strlist:
            effects_val = not effects.startswith("!")
            effects_bis = effects.replace("!", "")
            effects_tuplelist_cur = [
                (var.basename(), effects_val)
                for var in self.variables()
                if re.search(effects_bis, var.basename())
            ]

            effects_tuplelist += effects_tuplelist_cur

        return effects_tuplelist

    def add_atm2states(
        self,
        name,
        st1="absent",
        st2="present",
        init_st2=False,
        cond_occ_12=True,
        occ_law_12={"cls": "delay", "time": 0},
        occ_interruptible_12=True,
        effects_12=[],
        cond_occ_21=True,
        occ_law_21={"cls": "delay", "time": 0},
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
        """

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
            aut.get_transition_by_name(trans_name_12).bkd.setCondition(cond_occ_12)

        elif isinstance(cond_occ_12, str):
            aut.get_transition_by_name(trans_name_12).bkd.setCondition(
                self.variable(cond_occ_12)
            )
        else:
            raise ValueError(
                f"Condition '{cond_occ_12}' for transition {trans_name_12} not supported"
            )

        # Effects
        st2_bkd = aut.get_state_by_name(st2_name).bkd
        var_value_list_12 = self.pat_to_var_value_list(*effects_12)
        if len(var_value_list_12) > 0:

            def sensitive_method_12():
                if st2_bkd.isActive():
                    [var.setValue(value) for var, value in var_value_list_12]

            # setattr(comp.bkd, method_name, sensitive_method)
            method_name_12 = f"effect_{self.name()}_{trans_name_12}"
            aut.bkd.addSensitiveMethod(method_name_12, sensitive_method_12)
            [
                var.addSensitiveMethod(method_name_12, sensitive_method_12)
                for var, value in var_value_list_12
            ]

        # Jump 2 -> 1
        # -----------
        # Conditions
        trans_name_21 = f"{name}_{st2}_{st1}"
        if isinstance(cond_occ_21, bool):
            aut.get_transition_by_name(trans_name_21).bkd.setCondition(cond_occ_21)

        elif isinstance(cond_occ_21, str):
            aut.get_transition_by_name(trans_name_21).bkd.setCondition(
                self.variable(cond_occ_21)
            )
        else:
            raise ValueError(
                f"Condition '{cond_occ_21}' for transition {trans_name_21} not supported"
            )
        # Effects
        st1_bkd = aut.get_state_by_name(st1_name).bkd
        var_value_list_21 = self.pat_to_var_value_list(*effects_21)
        if len(var_value_list_21) > 0:

            def sensitive_method_21():
                if st1_bkd.isActive():
                    [var.setValue(value) for var, value in var_value_list_21]

            # setattr(comp.bkd, method_name, sensitive_method)
            method_name_21 = f"effect_{self.name()}_{trans_name_21}"
            aut.bkd.addSensitiveMethod(method_name_21, sensitive_method_21)
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
    step : optional
        Step parameter for automaton transitions

    Methods
    -------
    get_failure_cond(target_comps, failure_cond)
        Creates a failure condition function for the given target components
    get_repair_cond(target_comps, repair_cond)
        Creates a repair condition function for the given target components
    set_default_failure_param_name()
        Sets default failure parameter names (to be overridden in subclasses)
    set_default_repair_param_name()
        Sets default repair parameter names (to be overridden in subclasses)

    Examples
    --------
    >>> # Create a failure mode affecting components "pump1" and "pump2"
    >>> fm = ObjFailureMode(
    ...     fm_name="common_cause",
    ...     targets=["pump1", "pump2"],
    ...     failure_effects={"flow": False},
    ...     repair_effects={"flow": True}
    ... )
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
        trans_name_prefix="__cc_{target_comb}",
        step=None,
        **kwargs,
    ):
        # __import__("ipdb").set_trace()

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

            for target_set_idx in itertools.combinations(range(order_max), order):

                failure_effects_cur = []
                for target_idx in target_set_idx:
                    comp_target_cur = self.system().component(self.targets[target_idx])
                    for flow_name_pat, val in self.failure_effects.items():
                        for fo_name, fo in comp_target_cur.flows_out.items():
                            if re.search(f"^{flow_name_pat}$", fo_name):
                                failure_effects_cur.append(
                                    {
                                        "var": fo.var_fed_available,
                                        "value": val,
                                    }
                                )
                repair_effects_cur = []
                for target_idx in target_set_idx:
                    comp_target_cur = self.system().component(self.targets[target_idx])
                    for flow_name_pat, val in self.repair_effects.items():
                        for fo_name, fo in comp_target_cur.flows_out.items():
                            if re.search(f"^{flow_name_pat}$", fo_name):
                                repair_effects_cur.append(
                                    {
                                        "var": fo.var_fed_available,
                                        "value": val,
                                    }
                                )

                repair_effects_cur = [
                    {
                        "var": self.system()
                        .component(self.targets[target_idx])
                        .flows_out[flow_name]
                        .var_fed_available,
                        "value": val,
                    }
                    for target_idx in target_set_idx
                    for flow_name, val in self.repair_effects.items()
                ]

                fm_name_cur = fm_name
                if order_max > 1:
                    target_comb = "".join([str(i + 1) for i in target_set_idx])
                    fm_name_cur += self.trans_name_prefix.format(
                        target_comb=target_comb
                    )

                # __import__("ipdb").set_trace()
                target_comps_cur = [
                    self.system().component(self.targets[idx]) for idx in target_set_idx
                ]
                if self.failure_cond is not True:
                    failure_cond_cur = self.get_failure_cond(
                        target_comps_cur, self.failure_cond
                    )
                else:
                    failure_cond_cur = self.failure_cond

                if self.repair_cond is not True:
                    repair_cond_cur = self.get_repair_cond(
                        target_comps_cur, self.repair_cond
                    )
                else:
                    repair_cond_cur = self.repair_cond

                # if fm_name_cur == "frun__cc_134":
                #     __import__("ipdb").set_trace()
                self.add_aut2st(
                    name=fm_name_cur,
                    st1=self.repair_state,
                    st2=self.failure_state,
                    init_st2=False,
                    trans_name_12_fmt="{name}__{st2}",
                    cond_occ_12=failure_cond_cur,
                    occ_law_12=self.set_occ_law_failure(failure_var_params_cur),
                    occ_interruptible_12=True,
                    effects_st2=failure_effects_cur,
                    effects_st2_format="records",
                    trans_name_21_fmt="{name}__{st1}",
                    cond_occ_21=repair_cond_cur,
                    occ_law_21=self.set_occ_law_repair(repair_var_params_cur),
                    occ_interruptible_21=True,
                    effects_st1=repair_effects_cur,
                    effects_st1_format="records",
                    step=self.step,
                )

    def get_failure_cond(self, target_comps, failure_cond):
        def failure_cond_fun():
            return all(
                [
                    comp.flows_in[flow].var_fed.value() == flow_value
                    for flow, flow_value in failure_cond.items()
                    for comp in target_comps
                ]
            )

        return failure_cond_fun

    def get_repair_cond(self, target_comps, repair_cond):
        def repair_cond_fun():
            # repair_cond = self.repair_cond
            return all(
                [
                    comp.flows_in[flow].var_fed.value() == flow_value
                    for flow, flow_value in repair_cond.items()
                    for comp in target_comps
                ]
            )

        return repair_cond_fun

        # __import__("ipdb").set_trace()

    # TO BE OVERLOADED IF NEEDED
    def set_default_failure_param_name(self):
        pass

    # TO BE OVERLOADED IF NEEDED
    def set_default_repair_param_name(self):
        pass

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

    def set_occ_law_failure(self, params):
        return {"cls": "exp", "rate": params[self.failure_param_name[0]]}

    def set_occ_law_repair(self, params):
        return {"cls": "exp", "rate": params[self.repair_param_name[0]]}


class ObjFailureModeDelay(ObjFailureMode):
    def set_default_failure_param_name(self, param_name=None):
        self.failure_param_name = "ttf" or param_name

    def set_default_repair_param_name(self, param_name=None):
        self.repair_param_name = "ttr" or param_name

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
