import Pycatshoo as pyc
import typing
import pydantic
import pkg_resources
import pyctools
from .common import get_pyc_type
installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
if 'ipdb' in installed_pkg:
    import ipdb  # noqa: F401


    
class FlowModel(pydantic.BaseModel):

    name: str = pydantic.Field(..., description="Flow name")
    var_type: str = pydantic.Field('bool', description="Flow type")
    var_fed_default: typing.Any = pydantic.Field(None, description="Flow default value")

    var_fed: typing.Any = \
        pydantic.Field(None, description="Component flow fed")
    var_fed_available: typing.Any = \
        pydantic.Field(None, description="Flow available fed")

    sm_flow_fed_fun: typing.Any = \
        pydantic.Field(None, description="set flow sensitive method")
    
    sm_flow_fed_name: typing.Any = \
        pydantic.Field(None, description="set flow sensitive method")

    
    @classmethod
    def get_clsname(basecls, **specs):
        port_name = specs.pop("port")
        if port_name == "io":
            port_name = "IO"
        else:
            port_name = port_name.capitalize()
        clsname = f"Flow{port_name}"
        return clsname

    def add_variables(self, comp,
                      **kwargs):

        py_type, pyc_type = get_pyc_type(self.var_type)

        var_fed_default = py_type() if self.var_fed_default is None \
            else self.var_fed_default

        self.var_fed = \
            comp.addVariable(f"{self.name}_fed",
                             pyc_type, py_type(var_fed_default))

        self.var_fed_available = \
            comp.addVariable(f"{self.name}_fed_available",
                                 pyc.TVarType.t_bool, True)
        self.var_fed_available.setReinitialized(True)


    def add_automata(self, comp):
        pass
    
class FlowIn(FlowModel):

    var_in: typing.Any = \
        pydantic.Field(None, description="Flow input")
    logic: str = \
        pydantic.Field("and", description="Flow input logic and ; or ; k/n")
    
    
    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, **kwargs)

        self.var_in = \
            comp.addReference(f"{self.name}_in")

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_in")
        comp.addMessageBoxImport(f"{self.name}_in",
                                     self.var_in, self.name)

    def create_sensitive_set_flow_fed_in(self):

        def sensitive_set_flow_template():
            # Reminder the value pass in andValue and orValue is
            # the returned value in the case of no connection
            if self.logic == "and":
                self.var_fed.setValue(
                    self.var_in.andValue(self.var_fed_default) and
                    self.var_fed_available.value())
            elif self.logic == "or":
                self.var_fed.setValue(
                    self.var_in.orValue(self.var_fed_default) and
                    self.var_fed_available.value())
            else:
                raise ValueError("FlowIn logic must be 'and' or 'or'")

        return sensitive_set_flow_template

    def update_sensitive_methods(self, comp):
        self.sm_flow_fed_fun = self.create_sensitive_set_flow_fed_in()
        self.sm_flow_fed_name = f"set_{self.name}_fed"
        self.var_in.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun)

        self.var_fed_available.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun)
        
        comp.addStartMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

    
class FlowOut(FlowModel):

    var_prod: typing.Any = \
        pydantic.Field(None, description="Flow production")
    var_prod_available: typing.Any = \
        pydantic.Field(None, description="Flow production available")
    var_prod_cond: list = \
        pydantic.Field([], description="Flow production conditions")
    var_prod_default: typing.Any = \
        pydantic.Field(None, description="Flow production default value")
    var_out: typing.Any = \
        pydantic.Field(None, description="Flow output")
    var_out_available: typing.Any = \
        pydantic.Field(None, description="Flow available out")

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, **kwargs)

        py_type, pyc_type = get_pyc_type(self.var_type)

        var_prod_default = py_type() if self.var_prod_default is None \
            else self.var_prod_default

        self.var_prod = \
            comp.addVariable(f"{self.name}_prod",
                             pyc_type, var_prod_default)

        self.var_prod_available = \
            comp.addVariable(f"{self.name}_prod_available",
                             pyc.TVarType.t_bool, True)

        self.var_out = \
            comp.addVariable(f"{self.name}_out",
                             pyc_type, py_type())

        self.var_out_available = \
            comp.addVariable(f"{self.name}_out_available",
                             pyc.TVarType.t_bool, True)
            

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_out")
        comp.addMessageBoxExport(f"{self.name}_out",
                                     self.var_out, self.name)

    def create_sensitive_set_flow_fed(self):

        def sensitive_set_flow_template():

            self.var_prod.setValue(
                self.var_prod_available.value())

            self.var_fed.setValue(
                self.var_prod.value() and
                self.var_fed_available.value())

        return sensitive_set_flow_template

    def create_sensitive_set_flow_out(self):

        def sensitive_set_flow_out_template():
            self.var_out.setValue(
                self.var_fed.value() and
                self.var_out_available.value())

        return sensitive_set_flow_out_template

    # def create_sensitive_set_flow_prod(self):

    #     def sensitive_set_flow_prod_template():
    #         self.var_prod.setValue(
    #             self.var_prod_available.value())

    #     return sensitive_set_flow_prod_template

    
    def create_sensitive_set_flow_prod_available(self):

        def sensitive_set_flow_prod_available_template():
            val = any([
                all([flow.var_fed.value() for flow in flow_conj])
                for flow_conj in self.var_prod_cond])

            self.var_prod_available.setValue(val)

        return sensitive_set_flow_prod_available_template


    def update_sensitive_methods(self, comp):

        # Update flow fed
        self.sm_flow_fed_fun = self.create_sensitive_set_flow_fed()
        self.sm_flow_fed_name = f"set_{self.name}_fed"
        # > if prod or fed available change
        self.var_prod.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun)
        self.var_fed_available.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun)
        # > if flow prod available changes
        self.var_prod_available.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun)

        # Start method
        comp.addStartMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        # Update flow out
        sens_meth_flow_out = self.create_sensitive_set_flow_out()
        sens_meth_flow_out_name = f"set_{self.name}_out"
        # > if flow fed or flow out available change
        self.var_fed.addSensitiveMethod(
            sens_meth_flow_out_name, sens_meth_flow_out)
        self.var_out_available.addSensitiveMethod(
            sens_meth_flow_out_name, sens_meth_flow_out)

        # # Prod
        # sens_meth_flow_prod = self.create_sensitive_set_flow_prod()
        # sens_meth_flow_prod_name = f"set_{self.name}_prod"
        # # > if flow prod available changes
        # self.var_prod_available.addSensitiveMethod(
        #     sens_meth_flow_prod_name, sens_meth_flow_prod)
        
        # Prod available
        sm_flow_prod_available_fun = \
            self.create_sensitive_set_flow_prod_available()
        sm_flow_prod_available_name = f"set_{self.name}_prod_available"

        # Add prod available update method to be sensitive to input changes
        for flow_conj in self.var_prod_cond:
            for flow in flow_conj:
                #ipdb.set_trace()
                flow.var_fed.addSensitiveMethod(
                    sm_flow_prod_available_name, sm_flow_prod_available_fun)



class FlowOutOnTrigger(FlowOut):
    var_trigger_in: typing.Any = \
        pydantic.Field(None, description="Trigger input reference")
    trigger_time_down_up: float = \
        pydantic.Field(0, description="Time to jump from down to up when trigger is activited")
    trigger_time_up_down: float = \
        pydantic.Field(0, description="Time to jump from up to down when trigger is activited")
    trigger_logic: str = \
        pydantic.Field("and", description="Flow input logic and ; or ; k/n")
    trigger_up: typing.Any = \
        pydantic.Field(None, description="Trigger up state")
    
    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, **kwargs)

        self.var_trigger_in = \
            comp.addReference(f"{self.name}_trigger_in")

    def add_mb(self, comp, **kwargs):

        super().add_mb(comp, **kwargs)

        comp.addMessageBox(f"{self.name}_trigger_in")
        comp.addMessageBoxImport(f"{self.name}_trigger_in",
                                     self.var_trigger_in, self.name)

    def add_automata(self, comp,
                     **kwargs):

        super().add_automata(comp, **kwargs)

        aut = \
            pyctools.PycAutomaton(
                name=f"{self.name}_trigger",
                states=["down", "up"],
                init_state="down",
                transitions=[
                    {"name": "trigger_down_up",
                     "source": "down",
                     "target": "up",
                     "occ_law": {"dist": "delay", "time": self.trigger_time_down_up}},
                    {"name": "trigger_up_down",
                     "source": "up",
                     "target": "down",
                     "occ_law": {"dist": "delay", "time": self.trigger_time_up_down}},
                ])

        aut.update_bkd(comp)
                       
        trans_name = "trigger_down_up"
        cond_method_name = f"cond_{comp.name}_{aut.name}_{trans_name}"
        if self.trigger_logic == "and":
            def cond_method_12():
                return not(self.var_trigger_in.andValue(False))
        elif self.trigger_logic == "or":
            def cond_method_12():
                return not(self.var_trigger_in.orValue(False))
        else:
            raise ValueError("trigger logic must be 'and' or 'or'")
        
        aut.get_transition_by_name(trans_name).bkd.setCondition(
            cond_method_name, cond_method_12)

        
        trans_name = "trigger_up_down"
        cond_method_name = f"cond_{comp.name}_{aut.name}_{trans_name}"
        if self.trigger_logic == "and":
            def cond_method_21():
                return self.var_trigger_in.andValue(False)
        elif self.trigger_logic == "or":
            def cond_method_21():
                return self.var_trigger_in.orValue(False)
        else:
            raise ValueError("trigger logic must be 'and' or 'or'")
        
        aut.get_transition_by_name(trans_name).bkd.setCondition(
            cond_method_name, cond_method_21)

        self.trigger_up = aut.get_state_by_name("up")

        aut.bkd.addSensitiveMethod(self.sm_flow_fed_name,
                                   self.sm_flow_fed_fun)
        
        comp.automata[aut.name] = aut

    # Overloaded from class FlowOut
    def create_sensitive_set_flow_fed(self):

        def sensitive_set_flow_template():
            self.var_prod.setValue(
                self.trigger_up.bkd.isActive() and
                self.var_prod_available.value())

            self.var_fed.setValue(
                self.var_prod.value() and
                self.var_fed_available.value())

        return sensitive_set_flow_template


class FlowIO(FlowModel):

    var_in: typing.Any = \
        pydantic.Field(None, description="Flow input")
    var_out: typing.Any = \
        pydantic.Field(None, description="Flow output")
    var_out_available: typing.Any = \
        pydantic.Field(None, description="Flow available out")
    logic: str = \
        pydantic.Field("and", description="Flow input logic and ; or ; k/n")

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, **kwargs)

        py_type, pyc_type = get_pyc_type(self.var_type)

        self.var_in = \
            comp.addReference(f"{self.name}_in")

        self.var_out = \
            comp.addVariable(f"{self.name}_out",
                                 pyc_type, py_type())

        self.var_out_available = \
            comp.addVariable(f"{self.name}_out_available",
                                 pyc.TVarType.t_bool, True)

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_in")
        comp.addMessageBoxImport(f"{self.name}_in",
                                     self.var_in, self.name)
        comp.addMessageBox(f"{self.name}_out")
        comp.addMessageBoxExport(f"{self.name}_out",
                                     self.var_out, self.name)

    def create_sensitive_set_flow_fed_in(self):

        def sensitive_set_flow_template():
            # Reminder the value pass in andValue and orValue is
            # the returned value in the case of no connection
            if self.logic == "and":
                self.var_fed.setValue(
                    self.var_in.andValue(self.var_fed_default) and
                    self.var_fed_available.value())
            elif self.logic == "or":
                self.var_fed.setValue(
                    self.var_in.orValue(self.var_fed_default) and
                    self.var_fed_available.value())
            else:
                raise ValueError("FlowIn logic must be 'and' or 'or'")

        return sensitive_set_flow_template

    def create_sensitive_set_flow_out(self):

        def sensitive_set_flow_template():
            self.var_out.setValue(
                self.var_fed.value() and
                self.var_out_available.value())

        return sensitive_set_flow_template

    def update_sensitive_methods(self, comp):
        self.sm_flow_fed_fun = self.create_sensitive_set_flow_fed_in()
        self.sm_flow_fed_name = f"set_{self.name}_fed"
        self.var_in.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun)

        self.var_fed_available.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun)

        comp.addStartMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        sens_meth_flow_out = self.create_sensitive_set_flow_out()
        sens_meth_flow_out_name = f"set_{self.name}_out"

        self.var_fed.addSensitiveMethod(
            sens_meth_flow_out_name, sens_meth_flow_out)
        self.var_out_available.addSensitiveMethod(
            sens_meth_flow_out_name, sens_meth_flow_out)
