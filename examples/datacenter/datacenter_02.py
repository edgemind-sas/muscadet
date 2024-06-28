"""
This example demonstrates how to create a basic Reliability Block Diagram (RBD) using the MUSCADET BDD framework.
The RBD consists of two source components in parallel, the second will start only if the first one is KO, two block component in parallel and a target component.
The sources produce a functional flow, which is propagated through the blocks to the target.
The example also includes the addition of stochastics failures modes to the main source, indicators, and running a simulation to observe flow propagation and the impact of failures.

Components:
- Source: Produces a functional flow named "elec".
- SourceTrigger: this source produces a flow when the main source is unable to provide the "elec" flow.
- Block: Receives and propagates the "elec" flow. 
- Target: Receives the "elec" flow from the block.

Failure Modes:
- Source S1, Block B1 and B2: Fails stochastically after approximativally 4 time units and repairs after 4 time units.
- SourceTrigger S2: Fails stochastically after approximativally 8 time units and repairs after 8 time units.

Indicators:
- Monitors the "elec_fed_out" status for components S1, S2, B1, and B2.
- Monitors the "elec_fed_in" status for component T.

Simulation:
- Runs a simulation for 24 time units to observe flow propagation and the impact of failures.
"""

import muscadet
import muscadet.kb.datacenter as dc

# Global Class
# ===============
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
         
         
# System building
# ===============
# System init
my_rbd = muscadet.System(name="Server RBD")

# Add components
my_rbd.add_component(cls="Generator", name="S1")
my_rbd.add_component(cls="Pump", name="P1")
my_rbd.add_component(cls="AirConditioning", name="air")

# Add deterministic failure mode to Generator S1
my_rbd.comp["S1"].add_delay_failure_mode(
    name="failure_deterministic",
    failure_cond="elec_fed_out",
    failure_time=6,
    failure_effects=[("elec_fed_available_out", False)],
    repair_time=3,
)

# Add deterministic failure mode to Pump P1
my_rbd.comp["P1"].add_delay_failure_mode(
    name="failure_deterministic",
    failure_cond="hydr_fed_out",
    failure_time=4,
    failure_effects=[("hydr_fed_available_out", False)],
    repair_time=3,
)

# Connect components
my_rbd.auto_connect("S1", "air")
my_rbd.auto_connect("P1", "air")

# System simulation
# =================
my_rbd.add_indicator_var(
    component=".*",
    var=".*fed_out",
    stats=["mean"],
)

my_rbd.simulate(
    {
        "nb_runs": 1,
        "schedule": [{"start": 0, "end": 24, "nvalues": 1000}],
        "seed": 2024,
    }
)

fig_indics = my_rbd.indic_px_line(
    markers=False, title="Flow monitoring in the RBD", facet_row="name"
)

# Uncomment to save graphic on disk
fig_indics_filename = "datacenter_02.png"
fig_indics.write_image(fig_indics_filename)

# Display graphic in browser
fig_indics.show()
