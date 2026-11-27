"""Flow components whose production is a logic of their inputs.

``LogicOr`` and ``LogicAnd`` are flow components. The combinational gate
``ObjLogicGate`` is not one and no longer lives here: muscadet declares it
(:mod:`muscadet.logic_gate`) and the engine builds it.
"""

from .obj import ObjFlow


class LogicBase(ObjFlow):

    def add_flows(self, time_to_true=0, time_to_false=0, init=True, **kwargs):

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

    def add_flows(self, flows_in, **kwargs):

        var_in_default = kwargs.pop("var_in_default")
        var_available_in_default = kwargs.pop("var_available_in_default")

        for flow in flows_in:
            self.add_flow_in(
                name=flow,
                logic="or",
                var_in_default=var_in_default,
                var_available_in_default=var_available_in_default,
            )

        super().add_flows(**kwargs)


class LogicAnd(LogicBase):

    def add_flows(self, flows_in, **kwargs):

        var_in_default = kwargs.pop("var_in_default")
        var_available_in_default = kwargs.pop("var_available_in_default")

        for flow in flows_in:
            self.add_flow_in(
                name=flow,
                logic="and",
                var_in_default=var_in_default,
                var_available_in_default=var_available_in_default,
            )

        super().add_flows(**kwargs)
