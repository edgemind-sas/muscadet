import muscadet

# Global attributes
# ==================
flow_name = "elec"


# Components classes
# ==================
class SourceElec(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_out(
            name=flow_name,
            var_prod_default=True,
        )


class UserElec(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name=flow_name,
            logic="and",
        )


class DipoleElec(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name=flow_name,
        )

        self.add_flow_out(
            name=flow_name,
            var_prod_cond=[
                flow_name,
            ],
        )
