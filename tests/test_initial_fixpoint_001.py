"""The initial state is the deterministic fixpoint, whatever the declaration order (#12).

At t = 0 the deterministic interactions are first stabilised to their fixpoint,
which IS the initial state; only then does whatever can fire at t = 0 fire, and
that is what an indicator counts as an occurrence. Up to 5.13.0 muscadet did
not reach that fixpoint inside the start phase: the start methods ran in
declaration order, a component started before its supplier read the supplier's
default, and the truth arrived afterwards through a sensitive method, which
PyCATSHOO counts as a departure. The same Source -> Target model reported 0 or 1
occurrence of "target fed" depending on which component was declared first.

Since 5.14.0 the start phase ends on a settle step that re-evaluates every
discrete start method until nothing moves, and the effects of a mode's INITIAL
state belong to that fixpoint: a mode declared directly in its failed state
(``init_st2=True``) used to leave its effect unapplied, so the scenario did not
start from the failed state at all. What is asserted here:

* the issue's model counts 0 occurrences of "target fed" in both orders, and a
  three-component chain declared backwards does too;
* an initiating failure written as a delay-0 transition still counts ONE
  occurrence of "target not fed" in both orders: it fires after the fixpoint;
* a mode starting in its failed state starts the system failed, counts no
  occurrence (it is the initial state), and its repair restores the flow.
"""

import cod3s
import pytest

import muscadet
import muscadet.kb.rbd  # noqa: F401  (registers Source / Block / Target)

#: The schedule every counted run uses.
IFP_SCHEDULE = {"nb_runs": 1, "schedule": [0.0, 1.0], "seed": 42}
#: When a failed initial state is repaired.
IFP_REPAIR = 0.5

CLASSES = {"S": "Source", "B": "Block", "T": "Target"}


def build_chain(name, order):
    """Source -> (Block ->) Target, the components created in ``order``."""
    system = muscadet.System(name=name)
    for comp_name in order:
        system.add_component(name=comp_name, cls=CLASSES[comp_name])
    if "B" in order:
        system.connect_flow(source="S", target="B", flow_name="is_ok")
        system.connect_flow(source="B", target="T", flow_name="is_ok")
    else:
        system.connect_flow(source="S", target="T", flow_name="is_ok")
    return system


def occurrences(system, value_test):
    """Simulate once and count the occurrences of ``T.is_ok_fed_in == value``."""
    system.add_indicator_var(
        name="T_fed",
        component="^T$",
        var="^is_ok_fed_in$",
        operator="==",
        value_test=value_test,
        measure="nb-occurrences",
        stats=["mean"],
    )
    system.simulate(IFP_SCHEDULE)
    table = next(iter(system.indicators.values())).values
    return table[table["stat"] == "mean"]["values"].tolist()


ORDERS = [["S", "T"], ["T", "S"]]
ORDER_IDS = ["source_first", "target_first"]


@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_a_predicate_true_at_the_fixpoint_is_not_an_occurrence(order):
    system = build_chain(f"IfpFed{''.join(order)}", order)
    try:
        assert occurrences(system, True) == [0.0, 0.0]
    finally:
        system.deleteSys()


def test_a_chain_declared_backwards_settles_too():
    system = build_chain("IfpChainBackwards", ["T", "B", "S"])
    try:
        assert occurrences(system, True) == [0.0, 0.0]
    finally:
        system.deleteSys()


@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_an_initiating_failure_at_t0_is_one_occurrence(order):
    """A delay-0 transition fires AFTER the fixpoint: it is an event."""
    system = build_chain(f"IfpDelay0{''.join(order)}", order)
    try:
        system.comp["S"].add_atm2states(
            name="initiator",
            occ_law_12={"cls": "delay", "time": 0},
            effects_12=[("is_ok", False)],
            cond_occ_21=False,
        )
        assert occurrences(system, False) == [1.0, 1.0]
    finally:
        system.deleteSys()


def add_failed_initial_state(system):
    system.comp["S"].add_atm2states(
        name="initial",
        init_st2=True,
        effects_12=[("is_ok", False)],
        effects_21=[("is_ok", True)],
        occ_law_21={"cls": "delay", "time": IFP_REPAIR},
        # Once repaired it stays repaired: the default delay-0 failure would
        # fail it again at once.
        cond_occ_12=False,
    )


@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_a_mode_starting_failed_starts_the_system_failed(order):
    system = build_chain(f"IfpInitFailed{''.join(order)}", order)
    try:
        add_failed_initial_state(system)
        system.isimu_start()
        target = system.comp["T"].flows_in["is_ok"]
        assert system.comp["S"].flows_out["is_ok"].var_fed.value() is False
        assert target.var_fed.value() is False

        system.isimu_step_to(IFP_REPAIR + 0.25)
        assert target.var_fed.value() is True
        system.isimu_stop()
    finally:
        system.deleteSys()


@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_a_failed_initial_state_is_not_an_occurrence(order):
    """The failure is the starting condition; its REPAIR is the event."""
    system = build_chain(f"IfpInitCount{''.join(order)}", order)
    try:
        add_failed_initial_state(system)
        assert occurrences(system, False) == [0.0, 0.0]
    finally:
        system.deleteSys()


class IfpGated(muscadet.ObjFlow):
    """A discrete output gated on an input nobody feeds."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="x", var_in_default=False)
        self.add_flow_out(name="y", var_prod_default=True, var_prod_cond=["x"])
        self.add_flow_out(name="z", var_prod_default=False)


def test_a_declared_condition_holds_at_t0_and_no_condition_keeps_the_default():
    """``y`` used to stay at its default True until ``x`` moved, which it never
    does; ``z`` declares no condition, so its default IS its value."""
    system = muscadet.System(name="IfpGated")
    try:
        comp = system.add_component(name="G", cls="IfpGated")
        system.isimu_start()
        assert comp.flows_out["y"].var_fed.value() is False
        assert comp.flows_out["z"].var_fed.value() is False
        system.isimu_stop()
    finally:
        system.deleteSys()


def test_delete():
    cod3s.terminate_session()
