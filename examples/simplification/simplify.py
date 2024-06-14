"""
This example demonstrates how to create a basic Reliability Block Diagram (RBD) using the MUSCADET framework.
The RBD consists of a source component, two block components in parallel, and a target component.
The source produces a functional flow, which is propagated through the blocks to the target.
The example also includes the addition of deterministic failure modes to the blocks, indicators, and running a simulation to observe flow propagation and the impact of failures.

Components:
- Source: Produces a functional flow named "is_ok".
- Block: Receives and propagates the "is_ok" flow. Two blocks (B1 and B2) are used in parallel.
- Target: Receives the "is_ok" flow from the blocks.

Failure Modes:
- Block B1: Fails deterministically after 4 time units and repairs after 2 time units.
- Block B2: Fails deterministically after 8 time units and repairs after 3 time units.

Indicators:
- Monitors the "is_ok_fed_out" status for components S, B1, and B2.
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
my_rbd.add_component(cls="Target", name="T")

# Add deterministic failure mode to block B1
util.add_flow_delay(
    my_rbd.comp["B1"],
    name="failure_deterministic",
    flow_name=flow1,
    failure_time=8,
    repair_time=3,
)

# Add stochastic failure mode to block B3
my_rbd.comp["B2"].add_exp_failure_mode(
    name="failure_stochastic",
    failure_cond="is_ok_fed_out",
    failure_rate=1 / 8,
    failure_effects=[("is_ok_fed_available_out", False)],
    repair_rate=1 / 4,
)

# Connect components
my_rbd.auto_connect("S", "B1")
my_rbd.auto_connect("S", "B2")
my_rbd.auto_connect("B1", "T")
my_rbd.auto_connect("B2", "T")

# System simulation
# =================
util.show_all_indicators_of_component(
    sys=my_rbd,
    comp=my_rbd.comp["T"],
    nb_run=1000,
)

# util.show_all_indicators_of_system(
#    sys=my_rbd,
#    var=".*_fed_out",
#    nb_run=1000,
# )
