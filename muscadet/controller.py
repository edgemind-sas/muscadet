"""The controller, as a declaration.

``ObjCtrl`` describes a controller: which quantities it observes, which
signals and readings it publishes, and the closed grammar each output emits
(:mod:`muscadet.ctrl_grammar`). It holds data and builds nothing. What runs a
controller belongs to an engine, which receives the declaration through the
component builder it registered (:func:`muscadet.engine.component_builder`).

This module loads on a machine where no engine is installed.
"""

from .common import copy_declaration
from .ctrl_grammar import (
    CONTROLLER_DECLARATION_KEYS,
    CtrlNode,
    build_ctrl_node,
    check_declaration_keys,
)

#: The declaration kind of a controller, as :mod:`muscadet.declare` reads it.
KIND = "controller"

#: The class name a controller declaration carries.
CLS = "ObjCtrl"


class ObjCtrl:
    """A controller: it observes quantities and publishes signals.

    Parameters
    ----------
    name : str
        Component name.
    controls_in : list of dict, optional
        Observation inputs, each ``{"name": ..., **keys}``.
    controls_out : list of dict, optional
        Outputs, each ``{"name": ..., "kind": "bool"|"value", ...}``, with the
        grammar it emits under ``emit``.
    label, description, metadata
        As on every component.

    Raises
    ------
    ValueError
        On a key no controller reads, and on an emission the closed grammar
        does not carry. The refusals are the ones a live controller gives, in
        the same words, and they cost no engine object.
    """

    #: The sections a controller is declared with.
    DECLARATION_KEYS = CONTROLLER_DECLARATION_KEYS

    def __init__(self, name, label=None, description=None, metadata=None, **kwargs):
        check_declaration_keys(f"Object {name}: {CLS}", kwargs, self.DECLARATION_KEYS)

        for entry in kwargs.get("controls_out") or []:
            build_ctrl_node(
                f"Object {name}: controller output "
                f"{dict(entry).get('name')!r} emit",
                dict(entry).get("emit"),
            )

        self.name = name
        self.label = label
        self.description = description
        self.metadata = copy_declaration(metadata or {})
        self.controls_in = [
            copy_declaration(dict(entry)) for entry in kwargs.get("controls_in") or []
        ]
        self.controls_out = [
            _output_entry(entry) for entry in kwargs.get("controls_out") or []
        ]

    def declaration(self):
        """The controller as a component declaration, checked by its reader."""
        spec = {
            "name": self.name,
            "kind": KIND,
            "cls": CLS,
            "source_cls": type(self).__name__,
            "controls_in": copy_declaration(self.controls_in),
            "controls_out": copy_declaration(self.controls_out),
        }
        if self.label is not None:
            spec["label"] = self.label
        if self.description is not None:
            spec["description"] = self.description
        if self.metadata:
            spec["metadata"] = copy_declaration(self.metadata)
        return spec


def _output_entry(entry):
    """One output as data: a grammar handed as nodes is written as a mapping."""
    entry = copy_declaration(dict(entry))
    if _holds_a_node(entry.get("emit")):
        from .declare import _ctrl_emit_spec

        entry["emit"] = _ctrl_emit_spec(build_ctrl_node("emit", entry["emit"]))
    return entry


def _holds_a_node(value):
    """True when a grammar node sits anywhere in ``value``."""
    if isinstance(value, CtrlNode):
        return True
    if isinstance(value, dict):
        return any(_holds_a_node(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_holds_a_node(item) for item in value)
    return False
