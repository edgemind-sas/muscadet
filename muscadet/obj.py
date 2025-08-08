"""
This module defines the ObjFlow class, which is used to model components in a discrete stochastic flow system.
The ObjFlow class provides methods to add and manage input, output, and input/output flows, as well as to set up
automata and failure modes for the components.
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

        This method processes the var_prod_cond parameter in flow specifications to convert
        string or list-based production conditions into the internal format required by the system.
        It handles single conditions, conjunctive conditions (AND), and disjunctive conditions (OR).

        Parameters
        ----------
        flow_specs : dict
            Flow specifications dictionary containing parameters for the flow.
            The var_prod_cond key can contain:
            - A single string (flow name)
            - A list of strings (OR condition)
            - A list of lists (AND of ORs condition)

        Returns
        -------
        dict
            A copy of the input flow_specs with processed var_prod_cond if present.
            The var_prod_cond is converted into a list of lists format where:
            - Outer list represents AND conditions
            - Inner lists represent OR conditions
            - Each condition references the actual flow object instead of its name
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
        Sets up the flows for the component.

        Parameters
        ----------
        **kwargs : dict
            Additional parameters for setting up the flows.
        """

        flow_list = (
            list(self.flows_in.values())
            # + list(self.flows_io.values())  # TODO: Implement FlowIO class
            + list(self.flows_out.values())
        )

        for flow in flow_list:
            # if flow.name == "pae_std":
            #     ipdb.set_trace()
            flow.add_variables(self)
            flow.add_mb(self)
            flow.update_sensitive_methods(self)
            flow.add_automata(self)

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

        # Get powerset of targets indices
        # targets_powerset_idx = list(
        #     itertools.chain.from_iterable(
        #         itertools.combinations(range(len(targets)), n)
        #         for n in range(1, len(targets) + 1)
        #     )
        # )

        # for flow_name in effect_flows:
        #     for target in self.targets:
        #         # for target_set_idx in targets_powerset_idx:
        #         #     for target_idx in target_set_idx:
        #         # target = targets[target_idx]
        #         var_name = f"{target}_{flow_name}_fed_control"
        #         # __import__("ipdb").set_trace()
        #         var = self.addVariable(var_name, pyc.TVarType.t_bool, True)
        #         var.setReinitialized(True)

        #         mb_name = f"{target}_{flow_name}_fed_control_out"
        #         self.addMessageBox(mb_name)
        #         self.addMessageBoxExport(
        #             mb_name,
        #             var,
        #             f"{flow_name}_fed_control",
        #         )
        #         target_comp = self.system().component(target)
        #         self.system().connect(
        #             self,
        #             mb_name,
        #             target_comp,
        #             f"{flow_name}_fed_control_in",
        #         )

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

                # if order_max > 1:
                #     repair_param_name = f"{repair_param_name}__{order}_o_{order_max}"
                # repair_param_cur = repair_param[order - 1] or 0
                # repair_param_var = self.addVariable(
                #     repair_param_name, pyc.TVarType.t_double, repair_param_cur
                # )

            for target_set_idx in itertools.combinations(range(order_max), order):

                # failure_effects_cur = {
                #     f"{self.targets[target_idx]}_{flow_name}_fed_control": val
                #     for target_idx in target_set_idx
                #     for flow_name, val in failure_effects.items()
                # }
                # __import__("ipdb").set_trace()
                failure_effects_cur = [
                    {
                        "var": self.system()
                        .component(self.targets[target_idx])
                        .flows_out[flow_name]
                        .var_fed_available,
                        "value": val,
                    }
                    for target_idx in target_set_idx
                    for flow_name, val in self.failure_effects.items()
                ]
                # __import__("ipdb").set_trace()

                # repair_effects_cur = {
                #     f"{self.targets[target_idx]}_{flow_name}_fed_control": val
                #     for target_idx in target_set_idx
                #     for flow_name, val in repair_effects.items()
                # }
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
        if not targets:
            return ""
        if len(targets) == 1:
            return targets[0]

        first_len = len(targets[0])
        if not all(len(t) == first_len for t in targets):
            return concat_char[0].join(targets)

        result_chars = []
        for i in range(first_len):
            ref_char = targets[0][i]

            if ref_char in ignored_char:
                result_chars.append(ref_char)
                continue

            is_common = all(t[i] == ref_char for t in targets)

            if is_common:
                result_chars.append(ref_char)
            else:
                result_chars.append(rep_char)

        return "".join(result_chars)


class ObjFailureModeExp(ObjFailureMode):

    # def __init__(
    #     self,
    #     fm_name,
    #     failure_param_name="lambda",
    #     repair_param_name="mu",
    #     repair_param=[],
    #     **kwargs,
    # ):
    #     # AI? Is that the correct syntax to call the parent class constructor ?
    #     super().__init__(
    #         fm_name,
    #         failure_param_name=failure_param_name,
    #         repair_param_name=repair_param_name,
    #         **kwargs,
    #     )

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

    # def __init__(
    #     self,
    #     fm_name,
    #     failure_param_name="ttf",
    #     repair_param_name="ttr",
    #     **kwargs,
    # ):

    #     super().__init__(
    #         fm_name,
    #         failure_param_name=failure_param_name,
    #         repair_param_name=repair_param_name,
    #         **kwargs,
    #     )
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
