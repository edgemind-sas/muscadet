import muscadet


class Start(muscadet.ObjFlow):

    def add_flows(self, **kwargs):

        super().add_flows(**kwargs)

        self.add_flow_out(
            name="flow",
            var_prod_default=True,
        )


class Task(muscadet.ObjFlow):

    def add_flows(self, duration_min=0, duration_max=0, **kwargs):

        super().add_flows(**kwargs)

        self.add_flow_in(
            name="flow",
            logic="and",
        )

        self.add_flow_out_tempo(
            name="flow",
            var_prod_cond=[
                "flow",
            ],
            state_enable_name="Done",
            state_disable_name="Waiting",
            occ_enable_flow=dict(cls="exp", rate=2 / (duration_min + duration_max)),
        )


class End(muscadet.ObjFlow):

    def add_flows(self, **kwargs):

        super().add_flows(**kwargs)

        self.add_flow_in(
            name="flow",
            logic="and",
        )
