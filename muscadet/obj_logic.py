from .obj import ObjFlow
import pkg_resources
installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
import re
# ipdb is a debugger (pip install ipdb)
if 'ipdb' in installed_pkg:
    import ipdb  # noqa: F401


class LogicBase(ObjFlow):

    def add_flows(self, 
                  time_to_true=0,
                  time_to_false=0,
                  init=True,
                  **kwargs):

        super().add_flows(**kwargs)

        self.add_flow_out_tempo(
            name="flow",
            flow_init_state="start" if init else "stop",
            time_to_start_flow=time_to_true,
            time_to_stop_flow=time_to_false,
            var_prod_cond=list(self.flows_in),
            **kwargs,
        )


class LogicOr(LogicBase):

    def add_flows(self, flows_in,
                  **kwargs):

        var_in_default = kwargs.pop("var_in_default")
        var_available_in_default = kwargs.pop("var_available_in_default")

        for flow in flows_in:
            self.add_flow_in(name=flow,
                             logic="or",
                             var_in_default=var_in_default,
                             var_available_in_default=var_available_in_default,
                             )

        super().add_flows(**kwargs)


class LogicAnd(LogicBase):

    def add_flows(self, flows_in,
                  **kwargs):

        var_in_default = kwargs.pop("var_in_default")
        var_available_in_default = kwargs.pop("var_available_in_default")

        for flow in flows_in:
            self.add_flow_in(name=flow,
                             logic="and",
                             var_in_default=var_in_default,
                             var_available_in_default=var_available_in_default,
                             )


        super().add_flows(**kwargs)
