"""
This example demonstrates how to create a basic Reliability Block Diagram (RBD) using the MUSCADET framework.
The RBD consists of two source components in parallel, the second will start only if the first one is KO, one block component and a target component.
The sources produce a functional flow, which is propagated through the blocks to the target.
The example also includes the addition of deterministic failure modes to the main source, indicators, and running a simulation to observe flow propagation and the impact of failures.

Components:
- Source: Produces a functional flow named "is_ok".
- SourceTrigger: this source produces a flow when the main source is unable to provide the "is_ok" flow.
- Block: Receives and propagates the "is_ok" flow. 
- Target: Receives the "is_ok" flow from the block.

Failure Modes:
- Source S: Fails deterministically after 6 time units and repairs after 6 time units.

Indicators:
- Monitors the "is_ok_fed_out" status for components S1, S2, and B.
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
my_rbd.comp["S1"].add_exp_failure_mode(
    name="failure_deterministic",
    failure_cond="is_ok_fed_out",
    failure_rate=1 / 6,
    failure_effects=[("is_ok_fed_available_out", False)],
    repair_rate=1 / 2,
)

my_rbd.comp["S2"].add_exp_failure_mode(
    name="failure_deterministic",
    failure_cond="is_ok_fed_out",
    failure_rate=1 / 3,
    failure_effects=[("is_ok_fed_available_out", False)],
    repair_rate=1 / 2,
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

__import__("ipdb").set_trace()

# System simulation
# =================
my_rbd.simulate(
    {
        "nb_runs": 1,
        "schedule": [{"start": 0, "end": 24, "nvalues": 1000}],
    }
)

import Pycatshoo as pyc

analyser = pyc.CAnalyser(my_rbd)
analyser.keepFilteredSeq(True)

analyser.printFilteredSeq(100, "sequences.xml", "PySeq.xsl")
