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

import subprocess
import sys
import textwrap

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


# ----------------------------------------------------------------------
# Cost and loops: the settle must stay cheap and must never hang a start
# ----------------------------------------------------------------------


@pytest.mark.parametrize("backwards", [False, True], ids=["forward", "backward"])
def test_a_long_chain_settles_in_a_few_sweeps_whatever_its_order(
    backwards, monkeypatch
):
    """One sweep per hop made a 200-block chain 40 to 80 times slower."""
    from muscadet import initial_state

    sweeps = []
    settle = initial_state.settle_initial_state
    monkeypatch.setattr(
        initial_state,
        "settle_initial_state",
        lambda system: sweeps.append(settle(system)) or sweeps[-1],
    )

    names = ["S"] + [f"B{i}" for i in range(100)] + ["T"]
    system = muscadet.System(name=f"IfpLong{backwards}")
    try:
        for name in reversed(names) if backwards else names:
            system.add_component(name=name, cls=CLASSES[name[0]])
        for upstream, downstream in zip(names, names[1:]):
            system.connect_flow(source=upstream, target=downstream, flow_name="is_ok")
        system.isimu_start()
        assert system.comp["T"].flows_in["is_ok"].var_fed.value() is True
        system.isimu_stop()
    finally:
        system.deleteSys()

    assert sweeps and max(sweeps) <= 3


LOOP_MODEL = """
import cod3s, muscadet

class IfpLoopNode(muscadet.ObjFlow):
    def add_flows(self, neg=False, **kw):
        super().add_flows(**kw)
        self.add_flow_in(name="a")
        self.add_flow_out(
            name="a", var_prod_default=True, var_prod_cond=["a"], negate=neg
        )

system = muscadet.System(name="IfpLoop")
system.add_component(name="A", cls="IfpLoopNode", neg={neg_a})
system.add_component(name="B", cls="IfpLoopNode", neg={neg_b})
system.connect_flow(source="A", target="B", flow_name="a")
system.connect_flow(source="B", target="A", flow_name="a")
try:
    system.isimu_start()
    print("STARTED", system.comp["A"].flows_out["a"].var_fed.value())
    system.isimu_stop()
except muscadet.InitialFixpointError as error:
    print("REFUSED", sorted(error.variables))
system.deleteSys()
cod3s.terminate_session()
"""


def run_loop(neg_a, neg_b):
    """Start the two-node loop in a child process: a hang must fail, not stall."""
    code = textwrap.dedent(LOOP_MODEL).format(neg_a=neg_a, neg_b=neg_b)
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout


def test_a_loop_with_two_fixpoints_settles_and_starts():
    """A latch: two negations. The evaluation order once kept it from settling."""
    assert "STARTED" in run_loop(True, True)


def test_a_loop_with_no_fixpoint_is_refused_naming_what_moves():
    """One negation: no state agrees with itself, so there is no initial state.

    Refused rather than started: a start used to hang there for ever."""
    out = run_loop(True, False)
    assert "REFUSED" in out
    assert "a_fed_out" in out


def test_the_refusal_is_a_value_error():
    assert issubclass(muscadet.InitialFixpointError, ValueError)


@pytest.mark.parametrize("order", [["S", "G", "T"], ["T", "S", "G"]], ids=ORDER_IDS)
def test_a_combinational_gate_settles_in_both_orders(order):
    """The gate's result is part of the fixpoint, wherever its target was
    declared. A gate resolves its sources at construction, so it always comes
    after them."""
    system = muscadet.System(name=f"IfpGate{''.join(order)}")
    try:
        for name in order:
            if name == "G":
                system.add_component(
                    name="G",
                    cls="ObjLogicGate",
                    kind="and",
                    cond=[[{"obj": "S", "attr": "is_ok_fed_out", "value": True}]],
                    out_elements=["is_ok"],
                )
            else:
                system.add_component(name=name, cls=CLASSES[name])
        system.connect("G", "is_ok_out", "T", "is_ok_in")
        assert occurrences(system, True) == [0.0, 0.0]
    finally:
        system.deleteSys()


def test_delete():
    cod3s.terminate_session()
