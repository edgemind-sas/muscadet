"""
This example demonstrates how to create a basic Reliability Block Diagram (RBD) using the MUSCADET datacenter library.
The RBD consists of two Generator in parallel used as source components, two block component in parallel and a target component.
The Generator produces an elec flow, which is propagated through the Battery and the Electrical Panel.

Components:
- Generator: Produces a functional flow named "elec".
- ElectricalPanel: Receives and propagates the "elec" flow. 
- Battery: Receives the "elec" flow from the block.

Failure Modes:
- Generator S1 and S2, ElectricalPanel Panel and Battery Server: Fails stochastically after approximativally 4 time units and repairs after 4 time units.

Indicators:
- Monitors the "elec_fed_out" status for components S1, S2, Panel, and Server.

Simulation:
- Runs a simulation for 24 time units to observe flow propagation and the impact of failures.
"""

import muscadet
import muscadet.kb.datacenter as dc

# Global attributes
# ==================
            
# System building
# ===============
# System init
my_rbd = muscadet.System(name="Server RBD")

# Add components
my_rbd.add_component(cls="Generator", name="S1")
my_rbd.add_component(cls="Generator", name="S2")
my_rbd.add_component(cls="ElectricalPanel", name="Panel")
my_rbd.add_component(cls="Battery", name="Server")

# Add stochastic failure mode to Generator S1
my_rbd.comp["S1"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="elec_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("elec_fed_available_out", False)],
    repair_rate=1 / 4,
)

# Add stochastic failure mode to Generator S2
my_rbd.comp["S2"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="elec_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("elec_fed_available_out", False)],
    repair_rate=1 / 4,
)

# Add stochastic failure mode to Panel
my_rbd.comp["Panel"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="elec_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("elec_fed_available_out", False)],
    repair_rate=1 / 4,
)

# Add stochastic failure mode to Server
my_rbd.comp["Server"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="elec_fed_out",
    failure_rate=1 / 4,
    failure_effects=[("elec_fed_available_out", False)],
    repair_rate=1 / 4,
)

# Connect components
my_rbd.auto_connect("S1", "Panel")
my_rbd.auto_connect("S2", "Panel")
my_rbd.auto_connect("Panel", "Server")

# System simulation
# =================
my_rbd.add_indicator_var(
    component=".*",
    var=".*fed_out",
    stats=["mean"],
)

my_rbd.add_indicator_var(
    component="Server",
    var="elec_fed_in",
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
    markers=False, title="Flow monitoring in the electrical RBD", facet_row="name"
)

# Uncomment to save graphic on disk
fig_indics_filename = "indics.png"
fig_indics.write_image(fig_indics_filename)

# Display graphic in browser
fig_indics.show()
