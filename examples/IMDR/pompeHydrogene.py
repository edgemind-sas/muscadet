import muscadet
import muscadet.kb.datacenter as dc

# Global Class
# ===============
class Electrolyseur(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name="elec",
        )
        
        self.add_flow_in(
            name="hydr",
        )

        self.add_flow_out(
            name="hydrogene",
            var_prod_cond=[
                "elec",
                "hydr",
            ],
        )
    
   
class BatterieHydrogene(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name="hydrogene",
        )

        self.add_flow_out(
            name="hydrogene",
            var_prod_cond=[
                "hydrogene",
            ],
        )
    
   
class PumpFromStock(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name="hydrogene",
        )

        self.add_flow_out(
            name="hydrogene",
            var_prod_cond=[
                "hydrogene",
            ],
        )
    
   
class Compresseur(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name="hydrogene",
        )

        self.add_flow_out(
            name="hydrogene",
            var_prod_cond=[
                "hydrogene",
            ],
        )
        
         
# System building
# ===============
# System init
my_rbd = muscadet.System(name="Server RBD")

# Add components
my_rbd.add_component(cls="Generator", name="G1")
my_rbd.add_component(cls="Pump", name="P1")
my_rbd.add_component(cls="Electrolyseur", name="EauToH2")
my_rbd.add_component(cls="PumpFromStock", name="P2")
my_rbd.add_component(cls="BatterieHydrogene", name="BP")
my_rbd.add_component(cls="Compresseur", name="BPtoHP")
my_rbd.add_component(cls="BatterieHydrogene", name="HP")
my_rbd.add_component(cls="PumpFromStock", name="P3")

# Add stochastic failure mode to Generator S1
my_rbd.comp["P1"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="hydr_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("hydr_fed_available_out", False)],
    repair_rate=1 / 4,
)

my_rbd.comp["EauToH2"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="hydrogene_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("hydrogene_fed_available_out", False)],
    repair_rate=1 / 4,
)

my_rbd.comp["BP"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="hydrogene_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("hydrogene_fed_available_out", False)],
    repair_rate=1 / 4,
)

my_rbd.comp["BPtoHP"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="hydrogene_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("hydrogene_fed_available_out", False)],
    repair_rate=1 / 4,
)

my_rbd.comp["HP"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="hydrogene_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("hydrogene_fed_available_out", False)],
    repair_rate=1 / 4,
)

my_rbd.comp["P2"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="hydrogene_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("hydrogene_fed_available_out", False)],
    repair_rate=1 / 4,
)


# Connect components
my_rbd.auto_connect("G1", "EauToH2")
my_rbd.auto_connect("P1", "EauToH2")
my_rbd.auto_connect("EauToH2", "P2")
my_rbd.auto_connect("P2", "BP")
my_rbd.auto_connect("BP", "BPtoHP")
my_rbd.auto_connect("BPtoHP", "HP")
my_rbd.auto_connect("HP", "P3")


# System simulation
# =================
my_rbd.add_indicator_var(
    component=".*",
    var=".*fed_out",
    stats=["mean"],
)

my_rbd.simulate(
    {
        "nb_runs": 1000,
        "schedule": [{"start": 0, "end": 24, "nvalues": 1000}],
        "seed": 2024,
    }
)

fig_indics = my_rbd.indic_px_line(
    markers=False, title="Flow monitoring in the hydrogene", facet_row="name"
)

# Uncomment to save graphic on disk
fig_indics_filename = "indics.png"
fig_indics.write_image(fig_indics_filename)

# Display graphic in browser
fig_indics.show()
