"""The combinational logic gate, as a declaration.

``ObjLogicGate`` describes a gate: which source variables it reads, how it
aggregates them, and under which element names it publishes its result. It
holds data and builds nothing. What evaluates a gate during a run belongs to
an engine, which receives the declaration through the component builder it
registered (:func:`muscadet.engine.component_builder`).

This module loads on a machine where no engine is installed.
"""

from cod3s.specs.conditions import sanitize_cond_format

from .common import copy_declaration, resolve_gate_logic

#: The declaration kind of a gate, as :mod:`muscadet.declare` reads it.
KIND = "logic_gate"

#: The class name a gate declaration carries.
CLS = "ObjLogicGate"


class ObjLogicGate:
    """Combinational logic gate (OR / AND / k-of-n), automaton-free.

    A gate reads the observable variables of its source components through
    ``cond`` (the nested OR-of-AND form of a cod3s condition) and publishes a
    boolean result under one output element per downstream flow input.

    Args:
        name: component name.
        cond: nested list of leaves ``{obj, attr, value}`` naming source
            variables. By convention each source is its own unit clause, so
            ``kind`` alone selects the aggregation.
        out_elements: downstream flow element names the result is published
            under.
        kind: ``"or"`` | ``"and"`` | ``"k"``.
        k: threshold for ``kind == "k"``.
        label, description, metadata: as on every component.

    Edge cases (vacuous): an OR gate with no inputs is False, an AND gate with
    no inputs is True, a k gate with no inputs is False.
    """

    def __init__(
        self,
        name,
        cond=None,
        out_elements=None,
        kind="or",
        k=None,
        label=None,
        description=None,
        metadata=None,
    ):
        resolve_gate_logic(kind, k)
        self.name = name
        self.cond = copy_declaration(sanitize_cond_format(cond or []))
        self.out_elements = list(out_elements or [])
        self.logic_kind = kind
        self.logic_k = k
        self.label = label
        self.description = description
        self.metadata = copy_declaration(metadata or {})

    @staticmethod
    def _resolve_logic(kind, k):
        """Return ``(inner_logic, outer_logic)`` for unit-clause conds."""
        return resolve_gate_logic(kind, k)

    def declaration(self):
        """The gate as a component declaration, checked by its reader."""
        spec = {
            "name": self.name,
            "kind": KIND,
            "cls": CLS,
            "source_cls": type(self).__name__,
            "logic_kind": self.logic_kind,
            "k": self.logic_k,
            "cond": copy_declaration(self.cond),
            "out_elements": list(self.out_elements),
        }
        if self.label is not None:
            spec["label"] = self.label
        if self.description is not None:
            spec["description"] = self.description
        if self.metadata:
            spec["metadata"] = copy_declaration(self.metadata)
        return spec
