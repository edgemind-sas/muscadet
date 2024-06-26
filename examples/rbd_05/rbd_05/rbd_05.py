"""
This example demonstrates how to create a basic Reliability Block Diagram (RBD) using the MUSCADET BDD framework.
The RBD consists of two source components in parallel, the second will start only if the first one is KO, two block component in parallel and a target component.
The sources produce a functional flow, which is propagated through the blocks to the target.
The example also includes the addition of stochastics failures modes to the main source, indicators, and running a simulation to observe flow propagation and the impact of failures.

Components:
- Source: Produces a functional flow named "is_ok".
- SourceTrigger: this source produces a flow when the main source is unable to provide the "is_ok" flow.
- Block: Receives and propagates the "is_ok" flow. 
- Target: Receives the "is_ok" flow from the block.

Failure Modes:
- Source S1, Block B1 and B2: Fails stochastically after approximativally 4 time units and repairs after 4 time units.
- SourceTrigger S2: Fails stochastically after approximativally 8 time units and repairs after 8 time units.

Indicators:
- Monitors the "is_ok_fed_out" status for components S1, S2, B1, and B2.
- Monitors the "is_ok_fed_in" status for component T.

Simulation:
- Runs a simulation for 24 time units to observe flow propagation and the impact of failures.
"""

import muscadet
import muscadet.kb.rbd as rbd

# Global attributes
# ==================
flow1 = "is_ok"
            
# System building
# ===============
# System init
my_rbd = muscadet.System(name="My first RBD")

# Add components
my_rbd.add_component(cls="Source", name="S1")
my_rbd.add_component(cls="SourceTrigger", name="S2")
my_rbd.add_component(cls="Block", name="B1")
my_rbd.add_component(cls="Block", name="B2")
my_rbd.add_component(cls="Target", name="T")

# Add stochastic failure mode to block S1
my_rbd.comp["S1"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="is_ok_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("is_ok_fed_available_out", False)],
    repair_rate=1 / 4,
)

# Add stochastic failure mode to block S2
my_rbd.comp["S2"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="is_ok_fed_out",
    failure_rate=1 / 8,
    failure_effects=[("is_ok_fed_available_out", False)],
    repair_rate=1 / 8,
)

# Add stochastic failure mode to block B1
my_rbd.comp["B1"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="is_ok_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("is_ok_fed_available_out", False)],
    repair_rate=1 / 4,
)

# Add stochastic failure mode to block B2
my_rbd.comp["B2"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="is_ok_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("is_ok_fed_available_out", False)],
    repair_rate=1 / 4,
)

# Connect components
my_rbd.connect_trigger("S1", "S2", flow1)
my_rbd.auto_connect("S1", "B1")
my_rbd.auto_connect("S1", "B2")
my_rbd.auto_connect("S2", "B1")
my_rbd.auto_connect("S2", "B2")
my_rbd.connect_flow("B1", "T", flow1)
my_rbd.connect_flow("B2", "T", flow1)

# System simulation
# =================
my_rbd.add_indicator_var(
    component=".*",
    var=".*fed_out",
    stats=["mean"],
)

my_rbd.add_indicator_var(
    component="T",
    var="is_ok_fed_in",
    stats=["mean"],
)

my_rbd.simulate(
    {
        "nb_runs": 10000,
        "schedule": [{"start": 0, "end": 24, "nvalues": 1000}],
        "seed": 2024,
    }
)

fig_indics = my_rbd.indic_px_line(
    markers=False, title="Flow monitoring in the RBD", facet_row="name"
)

# Uncomment to save graphic on disk
# fig_indics_filename = "indics.png"
# fig_indics.write_image(fig_indics_filename)

# Display graphic in browser
# fig_indics.show()
