"""Per-operand negation in FlowOut.var_prod_cond.

A production-condition operand may be given as the historical plain flow-name
string (non-negated) OR as a ``{"name": str, "negate": True}`` mapping, in which
case the operand is evaluated as ``NOT flow.var_fed``. Negation is stored in the
index-aligned ``var_prod_cond_negate`` matrix; an all-non-negated condition
leaves the matrix empty so the evaluation stays byte-identical to legacy
muscadet.

The system feeds a unit-under-test with two constant inputs — ``t`` (True) and
``f`` (False) — via a source's ``var_prod_default``, then reads a battery of
output flows whose production conditions exercise negation in both inner modes,
single and double groups. Cf. per-operand-negation ADR (2026-07-22).
"""

import muscadet

import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():
    class Source(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            # Constant boolean sources: t stays True, f stays False.
            self.add_flow(dict(cls="FlowOut", name="t", var_prod_default=True))
            self.add_flow(dict(cls="FlowOut", name="f", var_prod_default=False))

    class Uut(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            # Inputs must exist before the outputs that reference them.
            self.add_flow(dict(cls="FlowIn", name="t", logic="and"))
            self.add_flow(dict(cls="FlowIn", name="f", logic="and"))

            # 1. NOT f = NOT False = True (single negated operand).
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="neg_f",
                    var_prod_cond=[[{"name": "f", "negate": True}]],
                )
            )
            # 2. NOT t = NOT True = False.
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="neg_t",
                    var_prod_cond=[[{"name": "t", "negate": True}]],
                )
            )
            # 3. (NOT f) AND t = True AND True = True  (the "(NON B) ET C" case).
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="nf_and_t",
                    var_prod_cond=[[{"name": "f", "negate": True}, "t"]],
                    var_prod_cond_inner_mode="and",
                )
            )
            # 4. (NOT t) AND t = False AND True = False.
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="nt_and_t",
                    var_prod_cond=[[{"name": "t", "negate": True}, "t"]],
                    var_prod_cond_inner_mode="and",
                )
            )
            # 5. Plain non-negated t = True (parity: no negation matrix).
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="parity_t",
                    var_prod_cond=["t"],
                )
            )
            # 6. (NOT t) OR (NOT f) = False OR True = True  (cold-standby
            #    "secours (NON A) OU (NON B)"; inner_mode 'and' => outer OR).
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="secours",
                    var_prod_cond=[
                        [{"name": "t", "negate": True}],
                        [{"name": "f", "negate": True}],
                    ],
                    var_prod_cond_inner_mode="and",
                )
            )
            # 7. Explicit negate=False behaves exactly like a plain string and
            #    is normalised away (no matrix) -> t = True.
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="explicit_false",
                    var_prod_cond=[[{"name": "t", "negate": False}]],
                )
            )

    class Target(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="t", logic="and"))
            self.add_flow(dict(cls="FlowIn", name="f", logic="and"))

    system = muscadet.System(name="SysNeg")
    system.add_component(name="S", cls="Source")
    system.add_component(name="U", cls="Uut")
    system.auto_connect("S", "U")

    return system


EXPECTED = {
    "neg_f": True,
    "neg_t": False,
    "nf_and_t": True,
    "nt_and_t": False,
    "parity_t": True,
    "secours": True,
    "explicit_false": True,
}


def test_operand_negation_truth_table(the_system):
    the_system.isimu_start()

    # Sanity: the constant inputs propagate as declared.
    assert the_system.comp["U"].flows_in["t"].var_fed.value() is True
    assert the_system.comp["U"].flows_in["f"].var_fed.value() is False

    for name, expected in EXPECTED.items():
        got = the_system.comp["U"].flows_out[name].var_fed.value()
        assert got is expected, f"{name}: expected {expected}, got {got}"

    the_system.isimu_stop()


def test_negate_matrix_is_index_aligned(the_system):
    flows = the_system.comp["U"].flows_out
    # Negated operands populate the matrix, aligned with var_prod_cond.
    assert flows["neg_f"].var_prod_cond_negate == [[True]]
    assert flows["nf_and_t"].var_prod_cond_negate == [[True, False]]
    assert flows["secours"].var_prod_cond_negate == [[True], [True]]


def test_non_negated_leaves_empty_matrix(the_system):
    # Parity: a plain-string condition and an explicit negate=False both keep
    # the empty matrix (fast, byte-identical evaluation path).
    flows = the_system.comp["U"].flows_out
    assert flows["parity_t"].var_prod_cond_negate == []
    assert flows["explicit_false"].var_prod_cond_negate == []


def test_repr_marks_negated_operands(the_system):
    # The textual condition prefixes negated operands with '¬'.
    text = str(the_system.comp["U"].flows_out["nf_and_t"])
    assert "¬f" in text
    assert "¬t" not in text  # t is non-negated in this output


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
