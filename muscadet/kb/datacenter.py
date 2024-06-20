import muscadet
import muscadet.kb.electric as elec

# Components classes
# ==================
class Generator(elec.SourceElec):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


class TableauElectrique(elec.DipoleElec):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)


class Batterie(elec.DipoleElec):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
