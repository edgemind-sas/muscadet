import Pycatshoo as pyc
import typing
import pydantic
import pkg_resources
installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
if 'ipdb' in installed_pkg:
    import ipdb  # noqa: F401


def get_pyc_type(var_type):
    if var_type == 'bool':
        return (bool, pyc.TVarType.t_bool)
    elif var_type == 'int':
        return (int, pyc.TVarType.t_integer)
    elif var_type == 'float':
        return (float, pyc.TVarType.t_double)
    else:
        raise ValueError(
            f"Type {var_type} not supported by PyCATSHOO")

    
class FlowModel(pydantic.BaseModel):

    name: str = pydantic.Field(..., description="Flow name")
    var_type: str = pydantic.Field('bool', description="Flow type")
    default: typing.Any = pydantic.Field(None, description="Flow default value")

    var_fed: typing.Any = \
        pydantic.Field(None, description="Component flow fed")
    var_available_fed: typing.Any = \
        pydantic.Field(None, description="Flow available fed")

    
    @classmethod
    def get_clsname(basecls, **specs):
        port_name = specs.pop("port")
        if port_name == "io":
            port_name = "IO"
        else:
            port_name = port_name.capitalize()
        clsname = f"Flow{port_name}"
        return clsname

    def add_variables(self, comp_bkd,
                      **kwargs):

        py_type, pyc_type = get_pyc_type(self.var_type)

        default = py_type() if self.default is None else self.default

        self.var_fed = \
            comp_bkd.addVariable(f"{self.name}_fed",
                                 pyc_type, py_type(default))

        self.var_available_fed = \
            comp_bkd.addVariable(f"{self.name}_available_fed",
                                 pyc.TVarType.t_bool, True)

    
class FlowIn(FlowModel):

    var_in: typing.Any = \
        pydantic.Field(None, description="Flow input")
    
    def add_variables(self, comp_bkd, **kwargs):

        super().add_variables(comp_bkd, **kwargs)

        self.var_in = \
            comp_bkd.addReference(f"{self.name}_in")

    def add_mb(self, comp_bkd, **kwargs):

        comp_bkd.addMessageBox(f"{self.name}_in")
        comp_bkd.addMessageBoxImport(f"{self.name}_in",
                                     self.var_in, self.name)

    def create_sensitive_set_flow_fed_in(self):

        def sensitive_set_flow_template():
            # print(self.report_status())
            # ipdb.set_trace()
            # NOTE : Explain why False in andValue
            self.var_fed.setValue(
                self.var_in.andValue(False) and
                self.var_available_fed.value())

        return sensitive_set_flow_template

    def update_sensitive_methods(comp_bkd, self):
        sens_meth_flow_fed = self.create_sensitive_set_flow_fed_in()
        sens_meth_flow_fed_name = f"set_{self.name}_fed"
        self.var_in.addSensitiveMethod(
            sens_meth_flow_fed_name, sens_meth_flow_fed)

        self.var_available_fed.addSensitiveMethod(
            sens_meth_flow_fed_name, sens_meth_flow_fed)
        
        comp_bkd.addStartMethod(sens_meth_flow_fed_name, sens_meth_flow_fed)

    
class FlowOut(FlowModel):

    var_prod: typing.Any = \
        pydantic.Field(None, description="Flow production")
    var_out: typing.Any = \
        pydantic.Field(None, description="Flow output")
    var_available_out: typing.Any = \
        pydantic.Field(None, description="Flow available out")

    def add_variables(self, comp_bkd, **kwargs):

        super().add_variables(comp_bkd, **kwargs)

        py_type, pyc_type = get_pyc_type(self.var_type)

        self.var_prod = \
            comp_bkd.addVariable(f"{self.name}_prod",
                                 pyc_type, py_type())

        self.var_out = \
            comp_bkd.addVariable(f"{self.name}_out",
                                 pyc_type, py_type())

        self.var_available_out = \
            comp_bkd.addVariable(f"{self.name}_available_out",
                                 pyc.TVarType.t_bool, True)

    def add_mb(self, comp_bkd, **kwargs):

        comp_bkd.addMessageBox(f"{self.name}_out")
        comp_bkd.addMessageBoxExport(f"{self.name}_out",
                                     self.var_out, self.name)

    def create_sensitive_set_flow_fed_out(self):

        def sensitive_set_flow_template():
            
            self.var_fed.setValue(
                self.var_prod.value() and
                self.var_available_fed.value())

        return sensitive_set_flow_template

    def create_sensitive_set_flow_out(self):

        def sensitive_set_flow_template():
            self.var_out.setValue(
                self.var_fed.value() and
                self.var_available_out.value())

        return sensitive_set_flow_template

    def update_sensitive_methods(self, comp_bkd):
        sens_meth_flow_fed = self.create_sensitive_set_flow_fed_out()
        sens_meth_flow_fed_name = f"set_{self.name}_fed"
        self.var_prod.addSensitiveMethod(
            sens_meth_flow_fed_name, sens_meth_flow_fed)

        self.var_available_fed.addSensitiveMethod(
            sens_meth_flow_fed_name, sens_meth_flow_fed)

        #ipdb.set_trace()
        comp_bkd.addStartMethod(sens_meth_flow_fed_name, sens_meth_flow_fed)

        sens_meth_flow_out = self.create_sensitive_set_flow_out()
        sens_meth_flow_out_name = f"set_{self.name}_out"

        self.var_fed.addSensitiveMethod(
            sens_meth_flow_out_name, sens_meth_flow_out)
        self.var_available_out.addSensitiveMethod(
            sens_meth_flow_out_name, sens_meth_flow_out)


class FlowIO(FlowModel):

    var_in: typing.Any = \
        pydantic.Field(None, description="Flow input")
    var_out: typing.Any = \
        pydantic.Field(None, description="Flow output")
    var_available_out: typing.Any = \
        pydantic.Field(None, description="Flow available out")

    def add_variables(self, comp_bkd, **kwargs):

        super().add_variables(comp_bkd, **kwargs)

        py_type, pyc_type = get_pyc_type(self.var_type)

        self.var_in = \
            comp_bkd.addReference(f"{self.name}_in")

        self.var_out = \
            comp_bkd.addVariable(f"{self.name}_out",
                                 pyc_type, py_type())

        self.var_available_out = \
            comp_bkd.addVariable(f"{self.name}_available_out",
                                 pyc.TVarType.t_bool, True)

    def add_mb(self, comp_bkd, **kwargs):

        comp_bkd.addMessageBox(f"{self.name}_in")
        comp_bkd.addMessageBoxImport(f"{self.name}_in",
                                     self.var_in, self.name)
        comp_bkd.addMessageBox(f"{self.name}_out")
        comp_bkd.addMessageBoxExport(f"{self.name}_out",
                                     self.var_out, self.name)

    def create_sensitive_set_flow_fed_in(self):

        def sensitive_set_flow_template():
            # print(self.report_status())
            # ipdb.set_trace()
            # NOTE : Explain why False in andValue
            self.var_fed.setValue(
                self.var_in.andValue(False) and
                self.var_available_fed.value())

        return sensitive_set_flow_template

    def create_sensitive_set_flow_out(self):

        def sensitive_set_flow_template():
            self.var_out.setValue(
                self.var_fed.value() and
                self.var_available_out.value())

        return sensitive_set_flow_template

    def update_sensitive_methods(self, comp_bkd):
        sens_meth_flow_fed = self.create_sensitive_set_flow_fed_in()
        sens_meth_flow_fed_name = f"set_{self.name}_fed"
        self.var_in.addSensitiveMethod(
            sens_meth_flow_fed_name, sens_meth_flow_fed)

        self.var_available_fed.addSensitiveMethod(
            sens_meth_flow_fed_name, sens_meth_flow_fed)

        comp_bkd.addStartMethod(sens_meth_flow_fed_name, sens_meth_flow_fed)

        sens_meth_flow_out = self.create_sensitive_set_flow_out()
        sens_meth_flow_out_name = f"set_{self.name}_out"

        self.var_fed.addSensitiveMethod(
            sens_meth_flow_out_name, sens_meth_flow_out)
        self.var_available_out.addSensitiveMethod(
            sens_meth_flow_out_name, sens_meth_flow_out)
