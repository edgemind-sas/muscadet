import muscadet
import muscadet.kb.electric as elec
import muscadet.kb.hydraulic as hydr

# Components classes
# ==================
class Generator(elec.SourceElec):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


class ElectricalPanel(elec.DipoleElec):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


class Battery(elec.DipoleElec):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        
        
class Pump(hydr.SourceHydr):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        
        
class Valve(hydr.UserHydr):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        
        
class AirConditioning(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name="elec",
        )
        
        self.add_flow_in(
            name="hydr",
        )

        self.add_flow_out(
            name="hydr_hot",
            var_prod_cond=[
                "elec",
                "hydr",
            ],
        )
