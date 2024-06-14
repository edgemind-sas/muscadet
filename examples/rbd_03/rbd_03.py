"""
This example demonstrates how to create a basic Reliability Block Diagram (RBD) using the MUSCADET framework.
The RBD consists of a source component, two block components in parallel, and a target component.
The source produces a functional flow, which is propagated through the blocks to the target.
The example also includes the addition of deterministic failure modes to the blocks, indicators, and running a simulation to observe flow propagation and the impact of failures.

Components:
- Source: Produces a functional flow named "is_ok".
- Block: Receives and propagates the "is_ok" flow. Three blocks (B1, B2 and B3) are used in parallel.
- Target: Receives the "is_ok" flow from the blocks.

Failure Modes:
- Block B1: Fails deterministically after 4 time units and repairs after 2 time units.
- Block B2: Fails deterministically after 8 time units and repairs after 3 time units.
- Block B3: Fails stochastically after approximately 8 time units and repairs after 4 time units.

Indicators:
- Monitors the "is_ok_fed_out" status for components S, B1, B2, and B3.
- Monitors the "is_ok_fed_in" status for component T.

Simulation:
- Runs a simulation for 24 time units to observe flow propagation and the impact of failures.
"""

import muscadet

# Global attributes
# ==================
flow1 = "is_ok"

# Components classes
# ==================
class Source(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_out(
            name=flow1,
            var_prod_default=True,
        )


class Block(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name=flow1,
        )

        self.add_flow_out(
            name=flow1,
            var_prod_cond=[
                flow1,
            ],
        )


class Target(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name=flow1,
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
my_rbd.add_component(cls="Block", name="B3")
my_rbd.add_component(cls="Target", name="T")

# Add deterministic failure mode to block B1
my_rbd.comp["B1"].add_atm2states(
    name="failure_deterministic",
    occ_law_12={"cls": "delay", "time": 4},
    cond_occ_12="is_ok_fed_out",
    effects_12=[("is_ok_fed_available_out", False)],
    occ_law_21={"cls": "delay", "time": 2},
)

# Add deterministic failure mode to block B2
my_rbd.comp["B2"].add_delay_failure_mode(
    name="failure_deterministic",
    failure_cond="is_ok_fed_out",
    failure_time=8,
    failure_effects=[("is_ok_fed_available_out", False)],
    repair_time=3,
)

# Add stochastic failure mode to block B3
my_rbd.comp["B3"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="is_ok_fed_out",
    failure_rate=1/8,
    failure_effects=[("is_ok_fed_available_out", False)],
    repair_rate=1/4,
)

# Connect components
my_rbd.connect("S", "is_ok_out", "B1", "is_ok_in")
my_rbd.connect("S", "is_ok_out", "B2", "is_ok_in")
my_rbd.connect("S", "is_ok_out", "B3", "is_ok_in")
my_rbd.connect("B1", "is_ok_out", "T", "is_ok_in")
my_rbd.connect("B2", "is_ok_out", "T", "is_ok_in")
my_rbd.connect("B3", "is_ok_out", "T", "is_ok_in")

# Add indicator
my_rbd.add_indicator_var(
    component="S",
    var="is_ok_fed_out",
    stats=["mean"],
)
my_rbd.add_indicator_var(
    component="B1",
    var="is_ok_fed_out",
    stats=["mean"],
)
my_rbd.add_indicator_var(
    component="B2",
    var="is_ok_fed_out",
    stats=["mean"],
)
my_rbd.add_indicator_var(
    component="B3",
    var="is_ok_fed_out",
    stats=["mean"],
)
my_rbd.add_indicator_var(
    component="T",
    var="is_ok_fed_in",
    stats=["mean"],
)

# System simulation
# =================
my_rbd.simulate(
    {
        "nb_runs": 10000,
        "schedule": [{"start": 0, "end": 24, "nvalues": 1000}],
    }
)

fig_indics = my_rbd.indic_px_line(
    markers=False, title="Flow monitoring in the RBD", facet_row="name"
)

# Uncomment to save graphic on disk
# fig_indics_filename = "indics.png"
# fig_indics.write_image(fig_indics_filename)

# Uncomment to display graphic in browser
fig_indics.show()
