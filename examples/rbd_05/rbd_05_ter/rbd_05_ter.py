"""
This example demonstrates how to create a basic Reliability Block Diagram (RBD) using the MUSCADET framework.
The RBD consists of two source components in parallel, the second will start only if the first one is KO and a target component.
The sources produce a functional flow, which is propagated directly to the target.
The example also includes the addition of deterministic failure modes to the main source, indicators, and running a simulation to observe flow propagation and the impact of failures.
The first source will stop at 3 then the second source will stop at 8. The simulation should end at 8 with the sequence "S1 and S2".

In this example, the target "T.is_ok_fed_in" is set into the system "my_rbd". Each simulation must end the first time the target is triggered.
All transitions are monitored, and the sequences leading to the target are stored in XML and html files.

Components:
- Source: Produces a functional flow named "is_ok".
- SourceTrigger: this source produces a flow when the main source is unable to provide the "is_ok" flow.
- Target: Receives the "is_ok" flow from the block.

Failure Modes:
- Source S: Fails deterministically after 3 time units and repairs after 10 time units.
- Source S: Fails deterministically after 8 time units and repairs after 10 time units.

Indicators:
- Monitors the "is_ok_fed_out" status for components S1 and S2.
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


class SourceTrigger(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_out_on_trigger(
            name=flow1,
            trigger_time_up=0,
            trigger_time_down=0,
            trigger_logic="and",
            var_prod_default=True,
        )
        
class Target(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name=flow1,
            logic="or",
        )


# System building
# ===============

# System init
my_rbd = muscadet.System(name="My first RBD")

# Add components
my_rbd.add_component(cls="Source", name="S1")
my_rbd.add_component(cls="SourceTrigger", name="S2")
my_rbd.add_component(cls="Target", name="T")

# Add deterministic failure mode to source S1
my_rbd.comp["S1"].add_delay_failure_mode(
    name="failure_deterministic",
    failure_cond="is_ok_fed_out",
    failure_time=3,
    failure_effects=[("is_ok_fed_available_out", False)],
    repair_time=10,
)

# Add deterministic failure mode to source S2
my_rbd.comp["S2"].add_delay_failure_mode(
    name="failure_deterministic",
    failure_cond="is_ok_fed_out",
    failure_time=8,
    failure_effects=[("is_ok_fed_available_out", False)],
    repair_time=10,
)

# Connect components
my_rbd.connect_trigger("S1", "S2", flow1)
my_rbd.auto_connect("S1", "T")
my_rbd.auto_connect("S2", "T")

# Configure sequences
# -------------------
my_rbd.addTarget("top_event", "T.is_ok_fed_in", "VAR", "!=", 1)
my_rbd.monitorTransition("#.*")
# my_rbd.setKeepFilteredSeqForInd(False)

my_rbd.add_indicator_var(
    component="S.*",
    var="is_ok_fed_out",
    stats=["mean"],
)

my_rbd.add_indicator_var(
    component="T",
    var="is_ok_fed_in",
    stats=["mean"],
)

#__import__("ipdb").set_trace()

# System simulation
# =================
my_rbd.simulate(
    {
        "nb_runs": 1,
        "schedule": [{"start": 0, "end": 24, "nvalues": 1000}],
    }
)

fig_indics = my_rbd.indic_px_line(
    markers=False, title="Flow monitoring in the RBD", facet_row="name"
)

# Uncomment to display graphic in browser
fig_indics.show()

import Pycatshoo as pyc

analyser = pyc.CAnalyser(my_rbd)
analyser.keepFilteredSeq(True)

analyser.printFilteredSeq(100, "sequences.xml", "PySeq.xsl")

