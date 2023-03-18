import Pycatshoo as pyc
# ipdb is a debugger (pip install ipdb)
import pydantic
import typing
from .flow import FlowObj
import pkg_resources
installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
if 'ipdb' in installed_pkg:
    import ipdb  # noqa: F401


         
class FlowObj(pyc.CComponent):

    # @pydantic.validator('flows', pre=True)
    # def check_flows(cls, value, values, **kwargs):
    #     value = [PycFlowModel.from_dict(**v) for v in value]
    #     return value

    # @pydantic.validator('automata', pre=True)
    # def check_automata(cls, value, values, **kwargs):
    #     value = [PycAutomaton(**v) for v in value]
    #     return value

    
    def __init__(self, name,
                 flows: typing.List[FlowObj] = [],
                 **kwargs):

        pyc.CComponent.__init__(self, name)

        self.flows = flows

        self.set_flows()


    def report_status(self):
        sys = self.system()
        comp_status = []
        comp_status.append(f"{self.name} at t={sys.currentTime()}")

        for flow_name, flow in self.flow_fed.items():
            comp_status.append(f"Flow {flow_name} fed = {flow.value()}")

        comp_status_str = "\n".join(comp_status)
        return comp_status_str
        
    def set_flows(self):

        for flow in self.flows:

            flow.add_variables(self)
            flow.add_mb(self)
            flow.update_sensitive_methods(self)
