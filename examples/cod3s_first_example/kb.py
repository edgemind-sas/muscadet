import muscadet

class Source(muscadet.ObjFlow):

    def add_flows(self, **kwargs):

        super().add_flows(**kwargs)

        self.add_flow_out(
            name="flow",
        )

class Block(muscadet.ObjFlow):

    def add_flows(self, **kwargs):

        super().add_flows(**kwargs)

        self.add_flow_in(
            name="flow",
        )

        self.add_flow_out(
            name="flow",
            var_prod_cond=[
                "flow",
            ]
        )
