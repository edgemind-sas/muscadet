import muscadet

# Global attributes
# ==================
flow_name = "hydr"


# Components classes
# ==================
class SourceHydr(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_out(
            name=flow_name,
            var_prod_default=True,
        )


class UserHydr(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name=flow_name,
            logic="and",
        )
        
        
class DipoleHydr(muscadet.ObjFlow):
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
        
        
# Components classes
# ==================
class Pump(SourceHydr):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        
        
class Valve(UserHydr):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
