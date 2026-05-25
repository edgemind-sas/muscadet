from .obj import ObjFlow
import re

import Pycatshoo as Pyc
import cod3s
from cod3s.pycatshoo.common import sanitize_cond_format, prepare_attr_tree


class LogicBase(ObjFlow):

    def add_flows(self, time_to_true=0, time_to_false=0, init=True, **kwargs):

        super().add_flows(**kwargs)

        self.add_flow_out_tempo(
            name="flow",
            flow_init_state="start" if init else "stop",
            time_to_start_flow=time_to_true,
            time_to_stop_flow=time_to_false,
            var_prod_cond=list(self.flows_in),
            **kwargs,
        )


class LogicOr(LogicBase):

    def add_flows(self, flows_in, **kwargs):

        var_in_default = kwargs.pop("var_in_default")
        var_available_in_default = kwargs.pop("var_available_in_default")

        for flow in flows_in:
            self.add_flow_in(
                name=flow,
                logic="or",
                var_in_default=var_in_default,
                var_available_in_default=var_available_in_default,
            )

        super().add_flows(**kwargs)


class LogicAnd(LogicBase):

    def add_flows(self, flows_in, **kwargs):

        var_in_default = kwargs.pop("var_in_default")
        var_available_in_default = kwargs.pop("var_available_in_default")

        for flow in flows_in:
            self.add_flow_in(
                name=flow,
                logic="and",
                var_in_default=var_in_default,
                var_available_in_default=var_available_in_default,
            )

        super().add_flows(**kwargs)


class ObjLogicGate(cod3s.PycComponent):
    """Combinational logic gate (OR / AND / k-of-n) — automaton-free.

    A logic gate reads the observable variables of its connected source
    components directly via the ``cond`` mechanism (same nested OR-of-AND
    form as ``ObjEvent``, resolved by ``prepare_attr_tree``). A *sensitive
    method* — not an automaton — recomputes a boolean ``result`` variable
    whenever any referenced source variable changes, and ``result`` is
    exported through one message box per downstream flow element so that a
    regular muscadet ``FlowIn`` consumes the gate output.

    Reading source variables directly means heterogeneous source flow names
    need NO input message-box plumbing: the gate is decoupled from flow
    types and is a pure boolean gate.

    Args:
        name: component name.
        cond: nested list of leaves ``{obj, attr, value}`` referencing source
            variables (e.g. ``<flow>_fed_out`` for the fed channel, or
            ``<flow>_fed_available_out`` for the availability channel). Built
            from the gate's inbound connections by the translator/importer.
            By convention each source is its own unit clause
            (``[[s1], [s2], ...]``) so ``kind`` alone selects the aggregation.
        out_elements: downstream flow element names (the ``FlowIn`` names of
            the gate's targets). One export message box ``{elem}_out`` is
            created per element, exporting ``result`` under element ``elem``.
        kind: ``"or"`` | ``"and"`` | ``"k"``.
        k: threshold for ``kind == "k"`` (number of fed inputs required).

    Edge cases (vacuous): an OR gate with no inputs is False, an AND gate
    with no inputs is True, a k gate with no inputs is False
    (Python ``any([])`` / ``all([])`` / ``sum([]) >= k``).
    """

    def __init__(self, name, cond=None, out_elements=None, kind="or", k=None, **kwargs):
        super().__init__(name, **kwargs)

        self.logic_kind = kind
        self.logic_k = k
        self.result = self.addVariable("result", Pyc.TVarType.t_bool, False)

        inner_logic, outer_logic = self._resolve_logic(kind, k)
        cond_bis = prepare_attr_tree(
            sanitize_cond_format(cond or []), system=self.system()
        )

        def recompute():
            value = outer_logic(
                [
                    inner_logic(
                        [
                            (
                                getattr(leaf["attr"], leaf["attr_val_name"])()
                                == leaf.get("value", True)
                            )
                            for leaf in clause
                        ]
                    )
                    for clause in cond_bis
                ]
            )
            self.result.setValue(bool(value))

        self._recompute = recompute

        # No automaton: register the recompute on every referenced source
        # variable, so a change of any input re-evaluates the gate.
        for clause in cond_bis:
            for leaf in clause:
                leaf["attr"].addSensitiveMethod(f"logic_gate_{name}", recompute)

        # Expose ``result`` on the output interface, one box per downstream
        # element name (so ``connect_flow``/``connect`` to a FlowIn matches).
        for elem in out_elements or []:
            self.addMessageBox(f"{elem}_out")
            self.addMessageBoxExport(f"{elem}_out", self.result, elem)

        recompute()  # seed the initial value

    @staticmethod
    def _resolve_logic(kind, k):
        """Return ``(inner_logic, outer_logic)`` for unit-clause conds."""
        if kind == "or":
            return all, any
        if kind == "and":
            return all, all
        if kind == "k":
            if not isinstance(k, int) or isinstance(k, bool) or k < 1:
                raise ValueError(
                    f"ObjLogicGate kind='k' requires an int k>=1, got {k!r}"
                )
            return all, (lambda flags: sum(1 for flag in flags if flag) >= k)
        raise ValueError(
            f"ObjLogicGate: unknown kind {kind!r} (expected 'or', 'and' or 'k')"
        )
