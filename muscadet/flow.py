import Pycatshoo as pyc
import typing
import pydantic

import cod3s
from .common import get_pyc_type


class FlowModel(cod3s.ObjCOD3S):

    name: str = pydantic.Field(..., description="Flow name")

    var_type: str = pydantic.Field("bool", description="Flow type")

    var_fed_default: typing.Any = pydantic.Field(None, description="Flow default value")

    var_fed: typing.Any = pydantic.Field(None, description="Component flow fed")

    var_fed_available: typing.Any = pydantic.Field(
        None, description="Flow available fed"
    )

    sm_flow_fed_fun: typing.Any = pydantic.Field(
        None, description="set flow sensitive method"
    )

    sm_flow_fed_name: typing.Any = pydantic.Field(
        None, description="set flow sensitive method"
    )

    @classmethod
    def get_clsname(basecls, **specs):
        port_name = specs.pop("port")
        if port_name == "io":
            port_name = "IO"
        else:
            port_name = port_name.capitalize()
        clsname = f"Flow{port_name}"
        return clsname

    def add_variables(self, comp, port, **kwargs):

        py_type, pyc_type = get_pyc_type(self.var_type)

        self.var_fed_default = (
            py_type() if self.var_fed_default is None else self.var_fed_default
        )

        # ipdb.set_trace()
        self.var_fed = comp.addVariable(
            f"{self.name}_fed_{port}", pyc_type, py_type(self.var_fed_default)
        )
        # self.var_fed_available = \ *)
        #     comp.addVariable(f"{self.name}_fed_available_{port}", *)
        #                      pyc.TVarType.t_bool, True) *)
        # self.var_fed_available.setReinitialized(True) *)

        # self.var_fed = \
        #     comp.addVariable(f"{self.name}_fed",
        #                      pyc_type, py_type(var_fed_default))

        # self.var_fed_available = \
        #     comp.addVariable(f"{self.name}_fed_available",
        #                          pyc.TVarType.t_bool, True)
        # self.var_fed_available.setReinitialized(True)

    def add_automata(self, comp):
        pass


class FlowIn(FlowModel):

    var_in: typing.Any = pydantic.Field(
        None, description="Reference to collect external flow connections"
    )

    var_in_default: typing.Any = pydantic.Field(
        False, description="Flow input value when not connected"
    )

    var_available_in_default: typing.Any = pydantic.Field(
        True, description="Flow available input value when not connected"
    )

    logic: str = pydantic.Field("or", description="Flow input logic and ; or ; k/n")

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, port="in", **kwargs)

        self.var_in = comp.addReference(f"{self.name}_in")

        self.var_fed_available = comp.addReference(f"{self.name}_fed_available_in")

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_in")
        comp.addMessageBoxImport(f"{self.name}_in", self.var_in, self.name)

        comp.addMessageBox(f"{self.name}_available_in")
        comp.addMessageBoxImport(
            f"{self.name}_available_in",
            self.var_fed_available,
            f"{self.name}_available",
        )

    def create_sensitive_set_flow_fed_in(self):
        # Reminder the value pass in andValue and orValue is
        # the returned value in the case of no connection

        if self.logic == "and":

            def sensitive_set_flow_template():
                self.var_fed.setValue(
                    self.var_in.andValue(self.var_in_default)
                    and self.var_fed_available.andValue(self.var_available_in_default)
                )

        elif self.logic == "or":

            def sensitive_set_flow_template():
                self.var_fed.setValue(
                    self.var_in.orValue(self.var_in_default)
                    and self.var_fed_available.orValue(self.var_available_in_default)
                )

        else:
            raise ValueError("FlowIn logic must be 'and' or 'or'")

        return sensitive_set_flow_template

    def update_sensitive_methods(self, comp):
        self.sm_flow_fed_fun = self.create_sensitive_set_flow_fed_in()
        self.sm_flow_fed_name = f"set_{self.name}_fed_in"
        self.var_in.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        self.var_fed_available.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun
        )

        comp.addStartMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)


class FlowOut(FlowModel):

    var_prod: typing.Any = pydantic.Field(None, description="Flow production")
    var_prod_available: typing.Any = pydantic.Field(
        None, description="Indicates if the flow production condition are met"
    )
    var_prod_cond: list = pydantic.Field(
        [],
        description="Flow production conditions in conjonctive way [(C11 or C12 or ... or C1_k1) and (C21 or ... C2_k2) and ... and (Cn1 or ... or Cn_kn)]",
    )
    var_prod_default: typing.Any = pydantic.Field(
        False, description="Flow production default value"
    )
    negate: bool = pydantic.Field(
        False, description="Indicates if the flow output is negated"
    )
    # var_out: typing.Any = \
    #     pydantic.Field(None, description="Flow output")
    # var_out_available: typing.Any = \
    #     pydantic.Field(None, description="Flow available out")

    # @pydantic.validator('var_prod_cond')
    # def check_var_prod_cond(cls, v):
    #     ipdb.set_trace()

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, port="out", **kwargs)

        py_type, pyc_type = get_pyc_type(self.var_type)

        self.var_fed_available = comp.addVariable(
            f"{self.name}_fed_available_out", pyc.TVarType.t_bool, True
        )
        self.var_fed_available.setReinitialized(True)

        self.var_prod_default = (
            py_type() if self.var_prod_default is None else self.var_prod_default
        )

        self.var_prod = comp.addVariable(
            f"{self.name}_prod", pyc_type, self.var_prod_default
        )

        self.var_prod_available = comp.addVariable(
            f"{self.name}_prod_available", pyc.TVarType.t_bool, self.var_prod_default
        )

        # TO DO NOT .setReinitialized(True)
        # BECAUSE var_prod_available is driven by tempo mecanisms
        # self.var_prod_available.setReinitialized(True)

        # self.var_out = \
        #     comp.addVariable(f"{self.name}_out",
        #                      pyc_type, py_type())

        # self.var_out_available = \
        #     comp.addVariable(f"{self.name}_out_available",
        #                      pyc.TVarType.t_bool, True)

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_out")
        comp.addMessageBoxExport(f"{self.name}_out", self.var_fed, self.name)

        comp.addMessageBox(f"{self.name}_available_out")
        comp.addMessageBoxExport(
            f"{self.name}_available_out",
            self.var_fed_available,
            f"{self.name}_available",
        )

    def create_sensitive_set_flow_fed_out(self):

        if not self.negate:

            def sensitive_set_flow_template():
                self.var_prod.setValue(self.var_prod_available.value())
                self.var_fed.setValue(
                    self.var_prod.value() and self.var_fed_available.value()
                )

        else:

            def sensitive_set_flow_template():
                self.var_prod.setValue(self.var_prod_available.value())
                self.var_fed.setValue(
                    not (self.var_prod.value() and self.var_fed_available.value())
                )

        return sensitive_set_flow_template

    # def create_sensitive_set_flow_out(self):

    #     def sensitive_set_flow_out_template():
    #         self.var_out.setValue(
    #             self.var_fed.value() and
    #             self.var_out_available.value())

    #     return sensitive_set_flow_out_template

    # def create_sensitive_set_flow_prod(self):

    #     def sensitive_set_flow_prod_template():
    #         self.var_prod.setValue(
    #             self.var_prod_available.value())

    #     return sensitive_set_flow_prod_template

    def create_sensitive_set_flow_prod_available(self):

        def sensitive_set_flow_prod_available_template():

            # DEBUG
            # for flow_disj in self.var_prod_cond:
            #     for flow in flow_disj:
            #         comp = flow.var_fed.parent().basename()
            #         flow_val = flow.var_fed.value()
            #         print(f"{comp}: {flow.name}.var_fed = {flow_val}")
            #         ipdb.set_trace()

            val = all(
                [
                    any([flow.var_fed.value() for flow in flow_disj])
                    for flow_disj in self.var_prod_cond
                ]
            )

            self.var_prod_available.setValue(val)

        return sensitive_set_flow_prod_available_template

    def update_sensitive_methods(self, comp):

        # Update flow fed
        self.sm_flow_fed_fun = self.create_sensitive_set_flow_fed_out()
        self.sm_flow_fed_name = f"set_{self.name}_fed_out"
        # > if prod or fed available change
        self.var_prod.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)
        self.var_fed_available.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun
        )
        # > if flow prod available changes
        self.var_prod_available.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun
        )

        # Start method
        comp.addStartMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        # Update flow out
        # sens_meth_flow_out = self.create_sensitive_set_flow_out()
        # sens_meth_flow_out_name = f"set_{self.name}_out"
        # # > if flow fed or flow out available change
        # self.var_fed.addSensitiveMethod(
        #     sens_meth_flow_out_name, sens_meth_flow_out)
        # self.var_out_available.addSensitiveMethod(
        #     sens_meth_flow_out_name, sens_meth_flow_out)

        # # Prod
        # sens_meth_flow_prod = self.create_sensitive_set_flow_prod()
        # sens_meth_flow_prod_name = f"set_{self.name}_prod"
        # # > if flow prod available changes
        # self.var_prod_available.addSensitiveMethod(
        #     sens_meth_flow_prod_name, sens_meth_flow_prod)

        # Prod available
        sm_flow_prod_available_fun = self.create_sensitive_set_flow_prod_available()
        sm_flow_prod_available_name = f"set_{self.name}_prod_available"

        # Add prod available update method to be sensitive to input changes
        for flow_disj in self.var_prod_cond:
            for flow in flow_disj:
                # ipdb.set_trace()
                flow.var_fed.addSensitiveMethod(
                    sm_flow_prod_available_name, sm_flow_prod_available_fun
                )


class FlowOutTempo(FlowOut):
    occ_enable_flow: dict = pydantic.Field(
        {"cls": "delay", "time": 0}, description="Temporisation law to let flow out"
    )
    occ_disable_flow: dict = pydantic.Field(
        {"cls": "delay", "time": 0}, description="Temporisation law to block flow out"
    )
    # time_to_start_flow: float = \
    #     pydantic.Field(0, description="Start flow out temporisation")
    # time_to_stop_flow: float = \
    #     pydantic.Field(0, description="Stop flow out temporisation")
    state_enable_name: str = pydantic.Field(
        "enabled", description="Name of the enable state"
    )
    state_enabling_name: str = pydantic.Field(
        "enabling", description="Name of the enable state"
    )

    state_disable_name: str = pydantic.Field(
        "disabled", description="Name of the disable state"
    )
    init_enable: bool = pydantic.Field(
        False,
        description="Indicates if flow init state is enabled or disabled (default disabled",
    )
    state_enable_bkd: typing.Any = pydantic.Field(
        None, description="Enable state backend"
    )

    def add_automata(self, comp, **kwargs):

        super().add_automata(comp, **kwargs)

        aut = cod3s.PycAutomaton(
            name=f"{self.name}_out_tempo",
            states=[self.state_disable_name, self.state_enable_name],
            init_state=(
                self.state_enable_name if self.init_enable else self.state_disable_name
            ),
            transitions=[
                {
                    "name": f"{self.name}_enable",
                    "source": self.state_disable_name,
                    "target": self.state_enable_name,
                    "is_interruptible": True,
                    "occ_law": self.occ_enable_flow,
                },
                {
                    "name": f"{self.name}_disable",
                    "source": self.state_enable_name,
                    "target": self.state_disable_name,
                    "is_interruptible": True,
                    "occ_law": self.occ_disable_flow,
                },
            ],
        )
        aut.update_bkd(comp)

        trans_name = f"{self.name}_enable"
        cond_method_name = f"cond_{comp.name}_{aut.name}_{trans_name}"

        def cond_method_enable():
            return self.var_prod_available.value()

        aut.get_transition_by_name(trans_name).bkd.setCondition(
            cond_method_name, cond_method_enable
        )

        trans_name = f"{self.name}_disable"

        def cond_method_disable():
            return not self.var_prod_available.value()

        aut.get_transition_by_name(trans_name).bkd.setCondition(
            cond_method_name, cond_method_disable
        )

        # cond_method_name = f"cond_{comp.name}_{aut.name}_{trans_name}"
        # if self.trigger_logic == "and":
        #     def cond_method_21():
        #         return self.var_trigger_in.andValue(False)
        # elif self.trigger_logic == "or":
        #     def cond_method_21():
        #         return self.var_trigger_in.orValue(False)
        # else:
        #     raise ValueError("trigger logic must be 'and' or 'or'")
        self.state_enable_bkd = aut.get_state_by_name(self.state_enable_name)

        aut.bkd.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        comp.automata[aut.name] = aut

    # Overloaded from class FlowOut
    def create_sensitive_set_flow_fed_out(self):

        if not self.negate:

            def sensitive_set_flow_template():
                # self.var_prod.setValue(
                #     self.flow_start.bkd.isActive() and
                #     self.var_prod_available.value())
                self.var_prod.setValue(self.state_enable_bkd.bkd.isActive())

                self.var_fed.setValue(
                    self.var_prod.value() and self.var_fed_available.value()
                )

        else:

            def sensitive_set_flow_template():
                # self.var_prod.setValue(
                #     self.flow_start.bkd.isActive() and
                #     self.var_prod_available.value())
                self.var_prod.setValue(self.state_enable_bkd.bkd.isActive())

                self.var_fed.setValue(
                    not (self.var_prod.value() and self.var_fed_available.value())
                )

        return sensitive_set_flow_template


class FlowOutOnTrigger(FlowOut):
    var_trigger_in: typing.Any = pydantic.Field(
        None, description="Trigger input reference"
    )
    trigger_time_up: float = pydantic.Field(
        0, description="Time to jump from down to up when trigger is activited"
    )
    trigger_time_down: float = pydantic.Field(
        0, description="Time to jump from up to down when trigger is activited"
    )
    trigger_logic: str = pydantic.Field(
        "or", description="Flow input logic and ; or ; k/n"
    )
    trigger_up: typing.Any = pydantic.Field(None, description="Trigger up state")

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, **kwargs)

        self.var_trigger_in = comp.addReference(f"{self.name}_trigger_in")

    def add_mb(self, comp, **kwargs):

        super().add_mb(comp, **kwargs)

        comp.addMessageBox(f"{self.name}_trigger_in")
        comp.addMessageBoxImport(
            f"{self.name}_trigger_in", self.var_trigger_in, self.name
        )

    def add_automata(self, comp, **kwargs):

        super().add_automata(comp, **kwargs)

        aut = cod3s.PycAutomaton(
            name=f"{self.name}_trigger",
            states=["down", "up"],
            init_state="down",
            transitions=[
                {
                    "name": f"{self.name}_trigger_up",
                    "source": "down",
                    "target": "up",
                    "is_interruptible": True,
                    "occ_law": {"cls": "delay", "time": self.trigger_time_up},
                },
                {
                    "name": f"{self.name}_trigger_down",
                    "source": "up",
                    "target": "down",
                    "is_interruptible": True,
                    "occ_law": {"cls": "delay", "time": self.trigger_time_down},
                },
            ],
        )

        aut.update_bkd(comp)

        trans_name = f"{self.name}_trigger_up"
        cond_method_name = f"cond_{comp.name}_{aut.name}_{trans_name}"
        if self.trigger_logic == "and":

            def cond_method_12():
                return not (self.var_trigger_in.andValue(False))

        elif self.trigger_logic == "or":

            def cond_method_12():
                return not (self.var_trigger_in.orValue(False))

        else:
            raise ValueError("trigger logic must be 'and' or 'or'")

        aut.get_transition_by_name(trans_name).bkd.setCondition(
            cond_method_name, cond_method_12
        )

        trans_name = f"{self.name}_trigger_down"
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
            cond_method_name, cond_method_21
        )

        self.trigger_up = aut.get_state_by_name("up")

        aut.bkd.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        comp.automata[aut.name] = aut

    # Overloaded from class FlowOut
    def create_sensitive_set_flow_fed_out(self):

        def sensitive_set_flow_template():
            if not self.negate:
                self.var_prod.setValue(
                    self.trigger_up.bkd.isActive() and self.var_prod_available.value()
                )

                self.var_fed.setValue(
                    self.var_prod.value() and self.var_fed_available.value()
                )
            else:
                self.var_prod.setValue(
                    self.trigger_up.bkd.isActive() and self.var_prod_available.value()
                )

                self.var_fed.setValue(
                    not (self.var_prod.value() and self.var_fed_available.value())
                )

        return sensitive_set_flow_template


# TO BE UPDATED : MAKE IT INHERITING FROM IN AND OUT
# With automatic out conditions ???
class FlowIO(FlowModel):

    var_in: typing.Any = pydantic.Field(None, description="Flow input")
    var_out: typing.Any = pydantic.Field(None, description="Flow output")
    var_out_available: typing.Any = pydantic.Field(
        None, description="Flow available out"
    )
    logic: str = pydantic.Field("or", description="Flow input logic and ; or ; k/n")

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, **kwargs)

        py_type, pyc_type = get_pyc_type(self.var_type)

        self.var_in = comp.addReference(f"{self.name}_in")

        self.var_out = comp.addVariable(f"{self.name}_out", pyc_type, py_type())

        self.var_out_available = comp.addVariable(
            f"{self.name}_out_available", pyc.TVarType.t_bool, True
        )

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_in")
        comp.addMessageBoxImport(f"{self.name}_in", self.var_in, self.name)
        comp.addMessageBox(f"{self.name}_out")
        comp.addMessageBoxExport(f"{self.name}_out", self.var_out, self.name)

    def create_sensitive_set_flow_fed_in(self):

        def sensitive_set_flow_template():
            # Reminder the value pass in andValue and orValue is
            # the returned value in the case of no connection
            if self.logic == "and":
                self.var_fed.setValue(
                    self.var_in.andValue(self.var_fed_default)
                    and self.var_fed_available.value()
                )
            elif self.logic == "or":
                self.var_fed.setValue(
                    self.var_in.orValue(self.var_fed_default)
                    and self.var_fed_available.value()
                )
            else:
                raise ValueError("FlowIn logic must be 'and' or 'or'")

        return sensitive_set_flow_template

    def create_sensitive_set_flow_out(self):

        def sensitive_set_flow_template():
            self.var_out.setValue(
                self.var_fed.value() and self.var_out_available.value()
            )

        return sensitive_set_flow_template

    def update_sensitive_methods(self, comp):
        self.sm_flow_fed_fun = self.create_sensitive_set_flow_fed_in()
        self.sm_flow_fed_name = f"set_{self.name}_fed"
        self.var_in.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        self.var_fed_available.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun
        )

        comp.addStartMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        sens_meth_flow_out = self.create_sensitive_set_flow_out()
        sens_meth_flow_out_name = f"set_{self.name}_out"

        self.var_fed.addSensitiveMethod(sens_meth_flow_out_name, sens_meth_flow_out)
        self.var_out_available.addSensitiveMethod(
            sens_meth_flow_out_name, sens_meth_flow_out
        )
