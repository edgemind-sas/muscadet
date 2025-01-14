import muscadet


class ObjProject(muscadet.ObjFlow):

    def __init__(self, name, create_default_out_automata=True, **kwargs):
        super().__init__(
            name, create_default_out_automata=create_default_out_automata, **kwargs
        )


class Start(ObjProject):

    def add_flows(self, **kwargs):

        super().add_flows(**kwargs)

        self.add_flow(
            dict(
                cls="FlowOut",
                name="flow",
                var_prod_default=True,
            )
        )


class Task(ObjProject):

    def add_flows(self, duration_mean=0, **kwargs):

        super().add_flows(**kwargs)

        self.add_flow(
            dict(
                cls="FlowIn",
                name="flow",
                logic="and",
            )
        )

        if duration_mean > 0:
            occ_law = dict(cls="exp", rate=1 / (duration_mean))
        else:
            occ_law = dict(cls="delay", time=0)

        self.add_flow(
            dict(
                cls="FlowOutTempo",
                name="flow",
                var_prod_cond=[
                    "flow",
                ],
                state_enable_name="Done",
                state_disable_name="Waiting",
                occ_enable_flow=occ_law,
            )
        )


class End(ObjProject):

    def add_flows(self, **kwargs):

        super().add_flows(**kwargs)

        self.add_flow(
            dict(
                cls="FlowIn",
                name="flow",
                logic="and",
            )
        )
