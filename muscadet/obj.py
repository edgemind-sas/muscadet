import Pycatshoo as pyc
import pydantic
import typing
from .core import BaseModel
from .flow import FlowIn, FlowOut, FlowIO, FlowOutOnTrigger
import pyctools
import pkg_resources
import re
installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
# ipdb is a debugger (pip install ipdb)
if 'ipdb' in installed_pkg:
    import ipdb  # noqa: F401


class TransitionEffect(BaseModel):
    var_name: str = \
        pydantic.Field(..., description="Variable name")
    var_value: typing.Any = \
        pydantic.Field(..., description="Variable value to be affected")


class ObjFlow(pyc.CComponent):

    # @pydantic.validator('flows', pre=True)
    # def check_flows(cls, value, values, **kwargs):
    #     value = [PycFlowModel.from_dict(**v) for v in value]
    #     return value

    # @pydantic.validator('automata', pre=True)
    # def check_automata(cls, value, values, **kwargs):
    #     value = [PycAutomaton(**v) for v in value]
    #     return value


    def __init__(self, name, **kwargs):

        pyc.CComponent.__init__(self, name)

        self.flows_in = {}
        self.flows_out = {}
        self.flows_io = {}

        self.params = {}
        self.automata = {}

        self.add_flows(**kwargs)
        
        self.set_flows()


    # def report_status(self):
    #     sys = self.system()
    #     comp_status = []
    #     comp_status.append(f"{self.name} at t={sys.currentTime()}")

    #     for flow_name, flow in self.flow_fed.items():
    #         comp_status.append(f"Flow {flow_name} fed = {flow.value()}")

    #     comp_status_str = "\n".join(comp_status)
    #     return comp_status_str

    def add_flows(self, **kwargs):
        # TO BE OVERLOADED
        pass
    
    def add_flow_in(self, **params):
        flow_name = params.get("name")
        if not (flow_name in self.flows_in):
            self.flows_in[flow_name] = FlowIn(**params)
        else:
            raise ValueError(f"Input flow {flow_name} already exists")

    def add_flow_io(self, **params):
        flow_name = params.get("name")
        if not (flow_name in self.flows_io):
            self.flows_io[flow_name] = FlowIO(**params)
        else:
            raise ValueError(f"Input/Output flow {flow_name} already exists")

    def add_flow_out(self, **params):
        flow_name = params.get("name")

        if params.get("var_prod_cond"):
            params["var_prod_cond"] = \
                [[self.flows_in[flow_name] for flow_name in flow_conj]
                 for flow_conj in params.get("var_prod_cond")]

        if not (flow_name in self.flows_out):
            self.flows_out[flow_name] = FlowOut(**params)
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
        
    def set_flows(self):

        flow_list = list(self.flows_in.values()) + \
            list(self.flows_io.values()) + \
            list(self.flows_out.values())
        
        for flow in flow_list:
            if flow.name == "pae_std":
                ipdb.set_trace()
            flow.add_variables(self)
            flow.add_mb(self)
            flow.update_sensitive_methods(self)
            flow.add_automata(self)

    def pat_to_var_value(self, *pat_value_list):

        variables = self.getVariables()

        var_value_list = []

        for pat, value in pat_value_list:
            var_list = [
                (var, value) for var in variables
                if re.search(pat, var.basename())]

            var_value_list.extend(var_list)

        return var_value_list
    
    def add_automaton_flow(self, aut):

        aut_bis = pyctools.PycAutomaton(**aut)
        aut_bis.update_bkd(self)

        self.automata[aut_bis.name] = aut_bis

    def compute_effects_tuples(self, effects_str=None):
        if not effects_str:
            return []

        effects_strlist = effects_str.split(",")

        effects_tuplelist = []
        for effects in effects_strlist:
            effects_val = not effects.startswith("!")
            effects_bis = effects.replace("!", "")
            effects_tuplelist_cur = \
                [(var.basename(), effects_val) for var in self.getVariables()
                 if re.search(effects_bis, var.basename())]

            effects_tuplelist += effects_tuplelist_cur

        return effects_tuplelist
            

    def add_atm2states(self, name,
                       st1="absent",
                       st2="present",
                       init_st2=False,
                       cond_occ_12=True,
                       occ_law_12={"dist": "delay", "time":0},
                       occ_interruptible_12=True,
                       effects_12=[],
                       cond_occ_21=True,
                       occ_law_21={"dist": "delay", "time":0},
                       occ_interruptible_21=True,
                       effects_21=[],
                       ):

        st1_name = f"{name}_{st1}"
        st2_name = f"{name}_{st2}"

        aut = \
            pyctools.PycAutomaton(
                name=f"{self.name()}_{name}",
                states=[st1_name, st2_name],
                init_state=st2_name if init_st2 else st1_name,
                transitions=[
                    {"name": f"{name}_{st1}_{st2}",
                     "source": f"{st1_name}",
                     "target": f"{st2_name}",
                     "is_interruptible": occ_interruptible_12,
                     "occ_law": occ_law_12},
                    {"name": f"{name}_{st2}_{st1}",
                     "source": f"{st2_name}",
                     "target": f"{st1_name}",
                     "is_interruptible": occ_interruptible_21,
                     "occ_law": occ_law_21},
                ])

        aut.update_bkd(self)

        # Jump 1 -> 2
        # -----------
        # Conditions
        trans_name_12 = f"{name}_{st1}_{st2}"
        if isinstance(cond_occ_12, bool):
            aut.get_transition_by_name(trans_name_12)\
               .bkd.setCondition(cond_occ_12)

        elif isinstance(cond_occ_12, str):
            aut.get_transition_by_name(trans_name_12).bkd.setCondition(
                self.getVariable(cond_occ_12))
        else:
            raise ValueError(f"Condition '{cond_occ_12}' for transition {trans_name_12} not supported")

        # Effects
        st2_bkd = aut.get_state_by_name(st2_name).bkd
        var_value_list_12 = self.pat_to_var_value(*effects_12)
        if len(var_value_list_12) > 0:
            def sensitive_method_12():
                if st2_bkd.isActive():
                    [var.setValue(value) for var, value in var_value_list_12]

            #setattr(comp.bkd, method_name, sensitive_method)
            method_name_12 = f"effect_{self.name()}_{trans_name_12}"
            aut.bkd.addSensitiveMethod(method_name_12, sensitive_method_12)
            [var.addSensitiveMethod(method_name_12, sensitive_method_12)
             for var, value in var_value_list_12]
        
        # Jump 2 -> 1
        # -----------
        # Conditions
        trans_name_21 = f"{name}_{st2}_{st1}"
        if isinstance(cond_occ_21, bool):
            aut.get_transition_by_name(trans_name_21)\
               .bkd.setCondition(cond_occ_21)

        elif isinstance(cond_occ_21, str):
            aut.get_transition_by_name(trans_name_21).bkd.setCondition(
                self.getVariable(cond_occ_21))
        else:
            raise ValueError(f"Condition '{cond_occ_21}' for transition {trans_name_21} not supported")
        # Effects
        st1_bkd = aut.get_state_by_name(st1_name).bkd
        var_value_list_21 = self.pat_to_var_value(*effects_21)
        if len(var_value_list_21) > 0:
            def sensitive_method_21():
                if st1_bkd.isActive():
                    [var.setValue(value) for var, value in var_value_list_21]

            #setattr(comp.bkd, method_name, sensitive_method)
            method_name_21 = f"effect_{self.name()}_{trans_name_21}"
            aut.bkd.addSensitiveMethod(method_name_21, sensitive_method_21)
            [var.addSensitiveMethod(method_name_21, sensitive_method_21)
             for var, value in var_value_list_21]

        # Update automata dict
        # --------------------
        self.automata[aut.name] = aut


    def add_exp_failure_mode(
            self,
            name,
            failure_cond=True,
            failure_rate=0,
            failure_effects=[],
            failure_param_name="lambda",
            repair_cond=True,
            repair_rate=0,
            repair_effects=[],
            repair_param_name="mu",
            ):

        # Create lambda/mu parameter for failure mode name
        failure_rate_name = f"{name}_{failure_param_name}"
        self.params[failure_rate_name] = \
            self.addVariable(failure_rate_name,
                             pyc.TVarType.t_double,
                             failure_rate)
        repair_rate_name = f"{name}_{repair_param_name}"
        self.params[repair_rate_name] = \
            self.addVariable(repair_rate_name,
                             pyc.TVarType.t_double,
                             repair_rate)
        
        self.add_atm2states(
            name=name,
            st1="absent",
            st2="present",
            init_st2=False,
            cond_occ_12=failure_cond,
            occ_law_12={"dist": "exp", "rate": self.params[failure_rate_name]},
            occ_interruptible_12=True,
            effects_12=failure_effects,
            cond_occ_21=repair_cond,
            occ_law_21={"dist": "exp", "rate": self.params[repair_rate_name]},
            occ_interruptible_21=True,
            effects_21=repair_effects,
        )

    def add_delay_failure_mode(
            self,
            name,
            failure_cond=True,
            failure_time=0,
            failure_effects=[],
            failure_param_name="ttf",
            repair_cond=True,
            repair_time=0,
            repair_effects=[],
            repair_param_name="ttr",
            ):

        # Create lambda/mu parameter for failure mode name
        failure_time_name = f"{name}_{failure_param_name}"
        self.params[failure_time_name] = \
            self.addVariable(failure_time_name,
                             pyc.TVarType.t_double,
                             failure_time)
        repair_time_name = f"{name}_{repair_param_name}"
        self.params[repair_time_name] = \
            self.addVariable(repair_time_name,
                             pyc.TVarType.t_double,
                             repair_time)
        
        self.add_atm2states(
            name=name,
            st1="absent",
            st2="present",
            init_st2=False,
            cond_occ_12=failure_cond,
            occ_law_12={"dist": "delay", "time": self.params[failure_time_name]},
            occ_interruptible_12=True,
            effects_12=failure_effects,
            cond_occ_21=repair_cond,
            occ_law_21={"dist": "delay", "time": self.params[repair_time_name]},
            occ_interruptible_21=True,
            effects_21=repair_effects,
        )

            
            
