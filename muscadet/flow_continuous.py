"""Continuous (real-valued) flow primitives.

This module hosts the *continuous* flow family: :class:`FlowContinuousIn` and
:class:`FlowContinuousOut`. They sit on the shared :class:`~muscadet.flow.FlowModel`
base **beside** the discrete family (``FlowDiscreteIn`` / ``FlowDiscreteOut`` and
their legacy aliases), never on top of it: the two families are parallel, and the
discrete boolean semantics stay strictly untouched.

Naming convention
-----------------
Continuous flows mirror the discrete naming convention so that
``System.connect_flow`` — which wires message boxes by string concatenation,
``{flow_name}_out`` on the source to ``{flow_name}_in`` on the target — keeps
working unchanged, and therefore ``auto_connect`` too.

Variables (per flow named ``f``):

===============================  ===========  =======================================
Variable                         Owner        Meaning
===============================  ===========  =======================================
``f_fed_out``  (``t_double``)    out flow     value produced / delivered downstream
``f_demand_in`` (reference)      out flow     demands published by the consumers
``f_fed_in``   (``t_double``)    in flow      value received, sum of the connections
``f_in``       (reference)       in flow      upstream produced values
``f_demand_out`` (``t_double``)  in flow      demand published upstream
===============================  ===========  =======================================

The ``_in`` / ``_out`` suffix denotes the **direction of travel** of the quantity,
exactly like the discrete ``{name}_trigger_in`` channel carried by an *output*
flow: an input flow receives ``fed`` and emits ``demand``; an output flow emits
``fed`` and receives ``demand``.

Message boxes: a continuous flow uses a **single bidirectional** message box per
port — ``f_out`` on the producer, ``f_in`` on the consumer — carrying two aliases:

- alias ``f``          — data channel, exported by the out flow, imported by the in flow
- alias ``f_demand``   — demand channel, exported by the in flow, imported by the out flow

One ``connect(src, "f_out", tgt, "f_in")`` therefore wires *both* directions, and
the demand alias can collide neither with the data channel nor with the discrete
``{name}_available_in`` / ``{name}_available_out`` / ``{name}_trigger_in`` boxes.

Scope
-----
This module declares the channels only. Transformation rules, capacities,
equation registration, evaluation ordering, demand computation and allocation
live in later units. A continuous output with no rules simply holds its declared
default value.
"""

import typing

import Pycatshoo as pyc
import pydantic
from colored import fg, attr

from .flow import FlowModel


class FlowContinuous(FlowModel):
    """Shared base of the continuous flow family.

    Exists so a continuous flow can be told apart from a discrete one with a
    single ``isinstance(flow, FlowContinuous)`` check — used by the connection
    type check and by every consumer that must enumerate only the continuous
    flows of a component.
    """

    var_type: str = pydantic.Field(
        "float", description="Flow type (real-valued for a continuous flow)"
    )

    var_demand: typing.Any = pydantic.Field(
        None,
        description=(
            "Demand channel endpoint: a variable on an input flow (the demand "
            "this consumer publishes upstream), a reference on an output flow "
            "(the demands published by the downstream consumers)."
        ),
    )

    def get_flow_type_color(self) -> str:
        """Return the color formatting for continuous flow types."""
        return f"{attr('bold')}{fg('cyan')}"

    @classmethod
    def get_format_class_name(cls) -> str:
        """Return the color formatting for continuous flow class names."""
        return f"{fg('cyan')}"

    def format_flow_value(self, value) -> str:
        """Format a real flow value."""
        try:
            return f"{fg('green')}{float(value):g}{attr('reset')}"
        except (TypeError, ValueError):
            return str(value)

    def get_var_demand_value(self):
        """Return the current value of this flow's demand endpoint."""
        raise NotImplementedError

    def __repr__(self) -> str:
        # NOTE: deliberately does NOT call FlowModel.__repr__ -- the discrete
        # representation reads ``var_fed_available``, the boolean availability
        # gate, which a continuous flow does not carry (a continuous output
        # expresses a total loss of production by a zero rate, not by a gate).
        flow_type = self.__class__.__name__
        var_fed = self.var_fed.value() if self.var_fed is not None else "N/A"

        return (
            f"{self.get_flow_type_color()}{flow_type}{attr('reset')} "
            f"{fg('blue')}{self.name}{attr('reset')} "
            f"[{self.var_type}] = {self.format_flow_value(var_fed)} "
            f"[{self.format_flow_value(self.var_fed_default)}]"
        )

    def __str__(self) -> str:
        flow_type = self.__class__.__name__
        var_fed = self.var_fed.value() if self.var_fed is not None else "N/A"

        lines = [
            f"{self.get_flow_type_color()}{flow_type}{attr('reset')} "
            f"{fg('blue')}{self.name}{attr('reset')}",
            f"  {fg('white')}Type{attr('reset')}: {self.var_type}",
            f"  {fg('white')}Value{attr('reset')}: {self.format_flow_value(var_fed)}",
            f"  {fg('white')}Default{attr('reset')}: "
            f"{self.format_flow_value(self.var_fed_default)}",
        ]
        return "\n".join(lines)


class FlowContinuousIn(FlowContinuous):
    """Real-valued input flow.

    Aggregates every incoming connection by **sum**: several producers feeding
    one continuous input deliver their sum. An input with no connection reads
    ``var_in_default``.
    """

    var_in: typing.Any = pydantic.Field(
        None, description="Reference collecting the upstream produced values"
    )

    var_in_default: float = pydantic.Field(
        0.0, description="Flow input value when not connected"
    )

    var_demand_default: float = pydantic.Field(
        0.0,
        description=(
            "Initial value of the demand this consumer publishes upstream. The "
            "demand COMPUTATION is not part of the flow primitives."
        ),
    )

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, port="in", **kwargs)

        # Data channel: upstream produced values, aggregated by sum.
        self.var_in = comp.addReference(f"{self.name}_in")

        # Demand channel: what this consumer asks its producers for.
        self.var_demand = comp.addVariable(
            f"{self.name}_demand_out",
            pyc.TVarType.t_double,
            float(self.var_demand_default),
        )

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_in")
        comp.addMessageBoxImport(f"{self.name}_in", self.var_in, self.name)
        comp.addMessageBoxExport(
            f"{self.name}_in", self.var_demand, f"{self.name}_demand"
        )

    def get_var_demand_value(self):
        """Return the demand this consumer currently publishes upstream."""
        return self.var_demand.value()

    def create_sensitive_set_flow_fed_in(self):
        """Build the closure aggregating the incoming connections.

        ``IReference.sumValue(def)`` sums every connection and returns ``def``
        when the reference carries no connection at all, which is exactly the
        "unconnected input reads its declared default" semantics.
        """

        def sensitive_set_flow_template():
            self.var_fed.setValue(self.var_in.sumValue(self.var_in_default))

        return sensitive_set_flow_template

    def update_sensitive_methods(self, comp):
        self.sm_flow_fed_fun = self.create_sensitive_set_flow_fed_in()
        self.sm_flow_fed_name = f"set_{self.name}_fed_in"

        self.var_in.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        # Seeds the value at t=0 (and for an unconnected input, whose reference
        # never notifies a change).
        comp.addStartMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

    def __repr__(self) -> str:
        base_str = super().__repr__()
        demand = self.var_demand.value() if self.var_demand is not None else "N/A"
        return f"{base_str} | demand: {self.format_flow_value(demand)}"

    def __str__(self) -> str:
        base_repr = super().__str__()
        demand = self.var_demand.value() if self.var_demand is not None else "N/A"
        return (
            f"{base_repr}\n"
            f"  {fg('white')}Demand{attr('reset')}: {self.format_flow_value(demand)}"
        )


class FlowContinuousOut(FlowContinuous):
    """Real-valued output flow.

    Carries a single rate variable (``{name}_fed_out``) exported downstream, and
    imports the demands published by its consumers. With no transformation rule
    and no capacity the rate simply holds ``var_fed_default``.
    """

    var_demand_in_default: float = pydantic.Field(
        0.0,
        description="Aggregated demand value read when no consumer is connected",
    )

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, port="out", **kwargs)

        # Demand channel: the demands published by the downstream consumers.
        self.var_demand = comp.addReference(f"{self.name}_demand_in")

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_out")
        comp.addMessageBoxExport(f"{self.name}_out", self.var_fed, self.name)
        comp.addMessageBoxImport(
            f"{self.name}_out", self.var_demand, f"{self.name}_demand"
        )

    def get_var_demand_value(self):
        """Return the total demand published by the downstream consumers."""
        return self.var_demand.sumValue(self.var_demand_in_default)

    def update_sensitive_methods(self, comp):
        # No transformation rule and no capacity at this stage: the produced
        # rate holds the value it was declared with. Rule-driven recomputation
        # is installed by the rule/equation layer.
        pass

    def __repr__(self) -> str:
        base_str = super().__repr__()
        try:
            demand = self.get_var_demand_value()
        except Exception:
            demand = "N/A"
        return f"{base_str} | demand: {self.format_flow_value(demand)}"

    def __str__(self) -> str:
        base_repr = super().__str__()
        try:
            demand = self.get_var_demand_value()
        except Exception:
            demand = "N/A"
        return (
            f"{base_repr}\n"
            f"  {fg('white')}Demand{attr('reset')}: {self.format_flow_value(demand)}"
        )
