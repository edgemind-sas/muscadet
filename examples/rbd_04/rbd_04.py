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
import muscadet.utils.common as util

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
            trigger_time_up=1,
            trigger_time_down=1,
            trigger_logic="and",
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
my_rbd.add_component(cls="Source", name="S1")
my_rbd.add_component(cls="SourceTrigger", name="S2")
my_rbd.add_component(cls="Block", name="B")
my_rbd.add_component(cls="Target", name="T")

# Add deterministic failure mode to block B1
util.add_flow_delay(
    my_rbd.comp["S1"],
    name="failure_deterministic",
    flow_name=flow1,
    failure_time=6,
    repair_time=6,
)

# Connect components
# my_rbd.connect("S1", flow1 + "_out", "S2", flow1 + "_trigger_in")
my_rbd.auto_connect_trigger("S1", "S2", flow1)
my_rbd.auto_connect("S1", "B")
my_rbd.auto_connect("S2", "B")
# my_rbd.auto_connect_io("S2", "B", flow1)
my_rbd.auto_connect("B", "T")

# System simulation
# =================
# util.show_all_indicators_of_component(
#    sys=my_rbd,
#    comp=my_rbd.comp["T"],
#    nb_run=1000,
# )

util.show_all_indicators_of_system(
    sys=my_rbd,
    var=".*_fed_out",
    nb_run=1000,
)
