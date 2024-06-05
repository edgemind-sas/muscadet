import muscadet

# Components classes
# ==================


class Source(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_out(
            name="is_ok",
            var_prod_default=True,
        )


class Block(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name="is_ok",
        )

        self.add_flow_out(
            name="is_ok",
            var_prod_cond=[
                "is_ok",
            ],
        )


class Target(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name="is_ok",
            logic="and",
        )


# System building
# ===============

# System init
my_rbd = muscadet.System(name="My first RBD")

# Add components
my_rbd.add_component(cls="Source", name="S")
my_rbd.add_component(cls="Block", name="B1")
my_rbd.add_component(cls="Block", name="B2")
my_rbd.add_component(cls="Target", name="T")

# Connect components
my_rbd.connect("S", "is_ok_out", "B1", "is_ok_in")
my_rbd.connect("S", "is_ok_out", "B2", "is_ok_in")
my_rbd.connect("B1", "is_ok_out", "T", "is_ok_in")
my_rbd.connect("B2", "is_ok_out", "T", "is_ok_in")

# Add indicators
my_rbd.add_indicator_var(
    component="T",
    var="is_ok_fed_in",
    stats=["mean", "stddev"],
)

# System simulation
# =================

my_rbd.simulate(
    {
        "nb_runs": 1,
        "schedule": [{"start": 0, "end": 24, "nvalues": 23}],
    }
)


fig_indics = my_rbd.indic_px_line()

# Uncomment to display the graphic
# fig_indics.show()
