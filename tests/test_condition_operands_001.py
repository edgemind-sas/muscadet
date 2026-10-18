"""One enumeration of the operand vocabulary for the loop detection (R50, R52).

``ordering.condition_carriers`` walks outputs, then capacities, then mixture
groups, each with its operands. The order is what every site walked them in and
is kept because a refusal names the first comparison it meets.
"""

import cod3s

import muscadet
from muscadet.ordering import (
    carrier_drives,
    carrier_gates_production,
    condition_carriers,
)


class CoCommanded(muscadet.ObjFlow):
    """An output, a capacity and a machine, each commanded on one signal."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="cmd")
        for flow in ("q", "air"):
            self.add_flow_continuous_in(name=flow)
        self.add_flow_continuous_out(name="q", var_prod_cond=["cmd"])
        self.add_capacity(
            name="tank", flow="q", side="out", capacity=10.0, serve_cond=["cmd"]
        )
        self.add_mixture_in(name="fan", flows=["air"], flow_rate=1.0, run_cond=["cmd"])


def test_carriers_come_in_output_capacity_group_order():
    system = muscadet.System(name="CondOperands")
    try:
        comp = system.add_component(name="C", cls="CoCommanded")
        carriers = list(condition_carriers(comp))
        cmd = comp.flows_in["cmd"]

        kinds = [type(carrier).__name__ for carrier, _ in carriers]
        assert kinds == ["FlowContinuousOut", "Capacity", "MixtureIn"]
        assert all(
            [source for source, _ in operands] == [cmd] for _, operands in carriers
        )

        group = comp.mixtures["fan"]
        assert carrier_drives(comp, group) == set()
        assert carrier_gates_production(comp, group) is False
    finally:
        system.deleteSys()


def test_delete():
    cod3s.terminate_session()
