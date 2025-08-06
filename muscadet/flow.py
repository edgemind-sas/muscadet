import Pycatshoo as pyc
import typing
import pydantic
from colored import fg, attr

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

    def get_flow_type_color(self) -> str:
        """Return the color formatting for flow type. Can be overridden in subclasses."""
        return f"{attr('bold')}{fg('white')}"

    @classmethod
    def get_format_class_name(cls) -> str:
        """Return the color formatting for this flow class name. Can be overridden in subclasses."""
        return f"{attr('bold')}{fg('white')}"

    def format_boolean_value(self, value) -> str:
        """Format boolean values with appropriate colors."""
        if isinstance(value, bool):
            if value:
                return f"{fg('green')}{value}{attr('reset')}"
            else:
                return f"{fg('yellow')}{value}{attr('reset')}"
        return str(value)

    def get_var_fed_available(self):
        return self.var_fed_available.value()

    def format_var_fed_available(self, is_available) -> str:
        """Format var_fed_available value with appropriate colors. Can be overridden in subclasses."""
        availability_symbol = (
            f"{fg('green')}✓{attr('reset')}"
            if is_available
            else f"{fg('red')}✗{attr('reset')}"
        )
        return availability_symbol

    def __str__(self) -> str:
        flow_type = self.__class__.__name__

        # Get values safely, handling cases where variables might not be initialized

        var_fed = self.var_fed.value()
        var_fed_default = self.var_fed_default

        # Format values with appropriate colors
        formatted_var_fed = self.format_boolean_value(var_fed)
        formatted_var_fed_default = self.format_boolean_value(var_fed_default)
        availability_symbol = self.format_var_fed_available(
            self.get_var_fed_available()
        )

        lines = [
            f"{self.get_flow_type_color()}{flow_type}{attr('reset')} {fg('blue')}{self.name}{attr('reset')}",
            f"  {fg('white')}Type{attr('reset')}: {self.var_type}",
            f"  {fg('white')}Fed{attr('reset')}: {formatted_var_fed}",
            f"  {fg('white')}Default{attr('reset')}: {formatted_var_fed_default}",
            f"  {fg('white')}Available{attr('reset')}: {availability_symbol}",
        ]
        return "\n".join(lines)

    def __repr__(self) -> str:
        flow_type = self.__class__.__name__

        # Get values safely, handling cases where variables might not be initialized
        var_fed = self.var_fed.value() if self.var_fed else "N/A"
        var_fed_default = (
            self.var_fed_default if hasattr(self, "var_fed_default") else "N/A"
        )

        # Format values with appropriate colors
        formatted_var_fed = self.format_boolean_value(var_fed)
        formatted_var_fed_default = self.format_boolean_value(var_fed_default)
        availability_symbol = self.format_var_fed_available(
            self.get_var_fed_available()
        )

        return (
            f"{self.get_flow_type_color()}{flow_type}{attr('reset')} "
            f"{fg('blue')}{self.name}{attr('reset')} "
            f"[{self.var_type}] = {formatted_var_fed} "
            f"[{formatted_var_fed_default}] "
            f"{availability_symbol}"
        )


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

    def get_flow_type_color(self) -> str:
        """Return the color formatting for FlowIn type in orange."""
        return f"{attr('bold')}{fg('orange_1')}"

    @classmethod
    def get_format_class_name(cls) -> str:
        """Return the color formatting for FlowIn class name."""
        return f"{fg('orange_1')}"

    def get_var_fed_available(self):
        if self.logic == "and":
            return self.var_fed_available.andValue(self.var_available_in_default)
        elif self.logic == "or":
            return self.var_fed_available.orValue(self.var_available_in_default)
        else:
            raise ValueError("FlowIn logic must be 'and' or 'or'")

    def get_logic_color(self) -> str:
        """Return the color formatting for logic type."""
        if self.logic == "and":
            return f"{fg('magenta')}{self.logic}{attr('reset')}"
        elif self.logic == "or":
            return f"{fg('cyan')}{self.logic}{attr('reset')}"
        else:
            return f"{fg('red')}{self.logic}{attr('reset')}"

    def __repr__(self) -> str:
        base_str = super().__repr__()

        # Get var_in value safely
        try:
            if self.logic == "and":
                var_in_value = self.var_in.andValue(self.var_in_default)
            elif self.logic == "or":
                var_in_value = self.var_in.orValue(self.var_in_default)
            else:
                raise ValueError("FlowIn logic must be 'and' or 'or'")

        except:
            var_in_value = "N/A"

        # Format var_in value with appropriate colors
        formatted_var_in = self.format_boolean_value(var_in_value)

        return f"{base_str} | in ({self.get_logic_color()}): {formatted_var_in}"

    def __str__(self) -> str:
        base_repr = super().__str__()

        # Get var_in value safely
        try:
            if self.logic == "and":
                var_in_value = self.var_in.andValue(self.var_in_default)
            elif self.logic == "or":
                var_in_value = self.var_in.orValue(self.var_in_default)
            else:
                raise ValueError("FlowIn logic must be 'and' or 'or'")

        except:
            var_in_value = "N/A"

        # Format var_in value with appropriate colors
        formatted_var_in = self.format_boolean_value(var_in_value)

        # Add var_in and logic information to the base representation
        additional_lines = [
            f"  {fg('white')}Input{attr('reset')}: {formatted_var_in}",
            f"  {fg('white')}Logic{attr('reset')}: {self.get_logic_color()}",
        ]
        return f"{base_repr}\n" + "\n".join(additional_lines)

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
        description="Flow production condition [(C11 <BoolOpeA> C12 <BoolOpeA> ... <BoolOpeA> C1_k1) <BoolOpeB> (C21 <BoolOpeA> ... <BoolOpeA> C2_k2) <BoolOpeB> ... <BoolOpeB> (Cn1 <BoolOpeA> ... <BoolOpeA> Cn_kn)] where both <BoolOpeA> and <BoolOpeB> are boolean operators set by attribute 'var_prod_cond_inner_mode'",
    )
    var_prod_cond_inner_mode: str = pydantic.Field(
        "or",
        description="Flow production condition expression mode: 'or' means var_prod is evaluated like [(C11 or C12 or ... or C1_k1) and (C21 or ... C2_k2) and ... and (Cn1 or ... or Cn_kn)], 'and' means evaluation like [(C11 and C12 and ... and C1_k1) or (C21 and ... and C2_k2) or ... or (Cn1 and ... and Cn_kn)]",
    )
    # var_fed_control: typing.Any = pydantic.Field(
    #     None,
    #     description="Input available control to make flow controllable by external component",
    # )

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

        # self.var_fed_control = comp.addReference(f"{self.name}_fed_control")

        # TO DO NOT .setReinitialized(True)
        # BECAUSE var_prod_available is driven by tempo mecanisms
        # self.var_prod_available.setReinitialized(True)

        # self.var_out = \
        #     comp.addVariable(f"{self.name}_out",
        #                      pyc_type, py_type())

        # self.var_out_available = \
        #     comp.addVariable(f"{self.name}_out_available",
        #                      pyc.TVarType.t_bool, True)

    def get_flow_type_color(self) -> str:
        """Return the color formatting for FlowOut type in green."""
        return f"{attr('bold')}{fg('steel_blue_1a')}"

    @classmethod
    def get_format_class_name(cls) -> str:
        """Return the color formatting for FlowOut class name."""
        return f"{fg('steel_blue_1a')}"

    def __repr__(self) -> str:
        base_str = super().__repr__()

        # Get production condition information
        if self.var_prod_cond_inner_mode == "or":
            ope_inner = " or "
            ope_outer = " and "
        else:
            ope_inner = " and "
            ope_outer = " or "

        if self.var_prod_cond:
            cond_info = f"cond := {ope_outer.join([ope_inner.join([flow.name for flow in flow_inner]) for flow_inner in self.var_prod_cond])}"
        else:
            cond_info = "no cond"

        # Get production value safel
        prod_value = self.var_prod.value()

        formatted_prod = self.format_boolean_value(prod_value)

        return f"{base_str} | prod: {formatted_prod} | {cond_info}"

    def __str__(self) -> str:
        base_repr = super().__str__()

        # Get production condition information
        if self.var_prod_cond_inner_mode == "or":
            ope_inner = " or "
            ope_outer = " and "
        else:
            ope_inner = " and "
            ope_outer = " or "

        if self.var_prod_cond:
            cond_info = f"{ope_outer.join([ope_inner.join([flow.name for flow in flow_inner]) for flow_inner in self.var_prod_cond])}"
        else:
            cond_info = "No conditions"

        # Get production value safely
        prod_value = self.var_prod.value()
        formatted_prod = self.format_boolean_value(prod_value)

        # Add production and condition information to the base representation
        additional_lines = [
            f"  {fg('white')}Production{attr('reset')}: {formatted_prod}",
            f"  {fg('white')}Conditions{attr('reset')}: {cond_info}",
        ]

        if self.negate:
            additional_lines.append(
                f"  {fg('white')}Negated{attr('reset')}: {fg('red')}Yes{attr('reset')}"
            )

        return f"{base_repr}\n" + "\n".join(additional_lines)

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_out")
        comp.addMessageBoxExport(f"{self.name}_out", self.var_fed, self.name)

        # comp.addMessageBox(f"{self.name}_fed_control_in")
        # comp.addMessageBoxImport(
        #     f"{self.name}_fed_control_in",
        #     self.var_fed_control,
        #     f"{self.name}_fed_control",
        # )
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
                    self.var_prod.value()
                    and self.var_fed_available.value()
                    #                    and self.var_fed_control.andValue(True)
                )

        else:

            def sensitive_set_flow_template():
                self.var_prod.setValue(self.var_prod_available.value())
                self.var_fed.setValue(
                    not (
                        self.var_prod.value()
                        and self.var_fed_available.value()
                        #                        and self.var_fed_control.andValue(True)
                    )
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

        if self.var_prod_cond_inner_mode == "or":

            def sensitive_set_flow_prod_available_template():
                # for flow_disj in self.var_prod_cond:
                #     for flow in flow_disj:
                #         comp = flow.var_fed.parent().basename()
                #         flow_val = flow.var_fed.value()
                #         print(f"{comp}: {flow.name}.var_fed = {flow_val}")
                #         ipdb.set_trace()

                # [(C11 or C12 or ... or C1_k1) and (C21 or ... C2_k2) and ... and (Cn1 or ... or Cn_kn)]
                val = all(
                    [
                        any([flow_inner.var_fed.value() for flow_inner in flow_outer])
                        for flow_outer in self.var_prod_cond
                    ]
                )

                self.var_prod_available.setValue(val)

        elif self.var_prod_cond_inner_mode == "and":

            def sensitive_set_flow_prod_available_template():

                # [(C11 and C12 and ... and C1_k1) or (C21 and ... and C2_k2) or ... or (Cn1 and ... and Cn_kn)]
                val = any(
                    [
                        all([flow_inner.var_fed.value() for flow_inner in flow_outer])
                        for flow_outer in self.var_prod_cond
                    ]
                )

                self.var_prod_available.setValue(val)

        else:
            raise ValueError("var_prod_cond_inner_mode must be 'and' or 'or'")

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
        # self.var_fed_control.addSensitiveMethod(
        #     self.sm_flow_fed_name, self.sm_flow_fed_fun
        # )

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
        for flow_outer in self.var_prod_cond:
            for flow_inner in flow_outer:
                # ipdb.set_trace()
                flow_inner.var_fed.addSensitiveMethod(
                    sm_flow_prod_available_name, sm_flow_prod_available_fun
                )


class FlowOutTempo(FlowOut):

    occ_enable_flow: typing.Union[dict | cod3s.OccurrenceDistributionModel] = (
        pydantic.Field(
            {"cls": "delay", "time": 0}, description="Temporisation law to let flow out"
        )
    )
    occ_disable_flow: typing.Union[dict | cod3s.OccurrenceDistributionModel] = (
        pydantic.Field(
            {"cls": "delay", "time": 0},
            description="Temporisation law to block flow out",
        )
    )
    # time_to_start_flow: float = \
    #     pydantic.Field(0, description="Start flow out temporisation")
    # time_to_stop_flow: float = \
    #     pydantic.Field(0, description="Stop flow out temporisation")
    state_enable_name: str = pydantic.Field(
        "enabled", description="Name of the enable state"
    )
    # TO IMPLEMENT
    state_enabling_name: str = pydantic.Field(
        "enabling", description="Name of the enabling state"
    )
    # TO IMPLEMENT
    state_enabling_name: str = pydantic.Field(
        "disabling", description="Name of the disabling state"
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

        comp.automata_d[aut.name] = aut

    # Overloaded from class FlowOut
    def create_sensitive_set_flow_fed_out(self):

        if not self.negate:

            def sensitive_set_flow_template():
                # self.var_prod.setValue(
                #     self.flow_start.bkd.isActive() and
                #     self.var_prod_available.value())
                self.var_prod.setValue(self.state_enable_bkd.bkd.isActive())

                self.var_fed.setValue(
                    self.var_prod.value()
                    and self.var_fed_available.value()
                    #                    and self.var_fed_control.andValue(True)
                )

        else:

            def sensitive_set_flow_template():
                # self.var_prod.setValue(
                #     self.flow_start.bkd.isActive() and
                #     self.var_prod_available.value())
                self.var_prod.setValue(self.state_enable_bkd.bkd.isActive())

                self.var_fed.setValue(
                    not (
                        self.var_prod.value()
                        and self.var_fed_available.value()
                        #                        and self.var_fed_control.andValue(True)
                    )
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

        comp.automata_d[aut.name] = aut

    # Overloaded from class FlowOut
    def create_sensitive_set_flow_fed_out(self):

        def sensitive_set_flow_template():
            if not self.negate:
                self.var_prod.setValue(
                    self.trigger_up.bkd.isActive() and self.var_prod_available.value()
                )

                self.var_fed.setValue(
                    self.var_prod.value()
                    and self.var_fed_available.value()
                    #                    and self.var_fed_control.andValue(True)
                )
            else:
                self.var_prod.setValue(
                    self.trigger_up.bkd.isActive() and self.var_prod_available.value()
                )

                self.var_fed.setValue(
                    not (
                        self.var_prod.value()
                        and self.var_fed_available.value()
                        #                        and self.var_fed_control.andValue(True)
                    )
                )

        return sensitive_set_flow_template


# # TO BE UPDATED : MAKE IT INHERITING FROM IN AND OUT
# # With automatic out conditions ???
# class FlowIO(FlowModel):

#     var_in: typing.Any = pydantic.Field(None, description="Flow input")
#     var_out: typing.Any = pydantic.Field(None, description="Flow output")
#     var_out_available: typing.Any = pydantic.Field(
#         None, description="Flow available out"
#     )
#     logic: str = pydantic.Field("or", description="Flow input logic and ; or ; k/n")

#     def add_variables(self, comp, **kwargs):

#         super().add_variables(comp, **kwargs)

#         py_type, pyc_type = get_pyc_type(self.var_type)

#         self.var_in = comp.addReference(f"{self.name}_in")

#         self.var_out = comp.addVariable(f"{self.name}_out", pyc_type, py_type())

#         self.var_out_available = comp.addVariable(
#             f"{self.name}_out_available", pyc.TVarType.t_bool, True
#         )

#     def add_mb(self, comp, **kwargs):

#         comp.addMessageBox(f"{self.name}_in")
#         comp.addMessageBoxImport(f"{self.name}_in", self.var_in, self.name)
#         comp.addMessageBox(f"{self.name}_out")
#         comp.addMessageBoxExport(f"{self.name}_out", self.var_out, self.name)

#     def create_sensitive_set_flow_fed_in(self):

#         def sensitive_set_flow_template():
#             # Reminder the value pass in andValue and orValue is
#             # the returned value in the case of no connection
#             if self.logic == "and":
#                 self.var_fed.setValue(
#                     self.var_in.andValue(self.var_fed_default)
#                     and self.var_fed_available.value()
#                 )
#             elif self.logic == "or":
#                 self.var_fed.setValue(
#                     self.var_in.orValue(self.var_fed_default)
#                     and self.var_fed_available.value()
#                 )
#             else:
#                 raise ValueError("FlowIn logic must be 'and' or 'or'")

#         return sensitive_set_flow_template

#     def create_sensitive_set_flow_out(self):

#         def sensitive_set_flow_template():
#             self.var_out.setValue(
#                 self.var_fed.value() and self.var_out_available.value()
#             )

#         return sensitive_set_flow_template

#     def update_sensitive_methods(self, comp):
#         self.sm_flow_fed_fun = self.create_sensitive_set_flow_fed_in()
#         self.sm_flow_fed_name = f"set_{self.name}_fed"
#         self.var_in.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

#         self.var_fed_available.addSensitiveMethod(
#             self.sm_flow_fed_name, self.sm_flow_fed_fun
#         )

#         comp.addStartMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

#         sens_meth_flow_out = self.create_sensitive_set_flow_out()
#         sens_meth_flow_out_name = f"set_{self.name}_out"

#         self.var_fed.addSensitiveMethod(sens_meth_flow_out_name, sens_meth_flow_out)
#         self.var_out_available.addSensitiveMethod(
#             sens_meth_flow_out_name, sens_meth_flow_out
#         )
