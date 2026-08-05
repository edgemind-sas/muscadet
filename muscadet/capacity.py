"""Capacity declaration model and measurement link.

A **capacity** is a volume a component holds over one or more of its continuous
flows. It is declared with :meth:`muscadet.ObjFlow.add_capacity`, **independently
of the component's transformation rules** (KD14): a buffer can then be added to
an existing model without touching its transformation logic, and one rule set
can be reused with different buffering.

Declaration
-----------
>>> self.add_capacity(name="cuve", flow="H2O", capacity=1000)   # doctest: +SKIP

>>> self.add_capacity(                                          # doctest: +SKIP
...     name="cuve",
...     flows=[
...         {"name": "H2O", "weight": 1},
...         {"name": "additif", "weight": 2},
...     ],
...     capacity=1000,
...     side="in",
...     content_init={"H2O": 0, "additif": 0},
... )

``flows`` is a list of flow names or of mappings carrying ``name`` and
``weight``; ``weight`` defaults to 1 and expresses how much *volume* one unit of
that flow occupies. ``capacity`` is a single scalar: the volume the held flows
**share**. ``side`` places the whole capacity upstream (``"in"``) or downstream
(``"out"``) of the component's rules, and every held flow must resolve to that
same side.

Variables (per capacity ``c``, per held flow ``f``)
---------------------------------------------------
=============================  =========  ==============================================
Variable                       Kind       Meaning
=============================  =========  ==============================================
``c_qty_f``    (``t_double``)  ODE        raw quantity of ``f`` currently held
``c_qty``      (``t_double``)  ODE        total raw quantity held, all flows
``c_fill_f``   (``t_double``)  explicit   weighted fill of ``f``: ``qty * weight / volume``
``c_fill``     (``t_double``)  explicit   total weighted fill, the sum of the per-flow fills
``c_inflow_f`` (``t_double``)  input      rate at which ``f`` currently ENTERS the capacity
``c_outflow_f``(``t_double``)  input      rate at which ``f`` currently LEAVES the capacity
=============================  =========  ==============================================

Per KTD11 the capacity owns one ODE variable per held flow plus a total, and one
empty/full automaton driven by the total *weighted* fill. Fills are real
PyCATSHOO variables rather than Python properties (R28), so they reach the
simulation indicators.

``c_inflow_f`` / ``c_outflow_f`` are the capacity's two hooks onto the four hops
of KTD13: the sweeps that compute demand and allocation (U8/U10) write them, and
the capacity's equation integrates their imbalance. Nothing in this module
computes them.

Measurement link (R33)
----------------------
A capacity publishes its level through a **read-only export**: the message box
``{c}_level_out`` carries the aliases ``{c}_level`` (total raw quantity) and
``{c}_fill`` (total weighted fill). An observing component declares the matching
import with :meth:`muscadet.ObjFlow.add_measurement_in`, which creates the
message box ``{c}_level_in`` importing the same two aliases into *references*.
A reference has no ``setValue``, so the importing component cannot write the
level; the link carries no quantity and takes part in no allocation.

Scope
-----
This module is the capacity's own declaration, integration and bookkeeping.
Demand computation, allocation, rule evaluation and equation ordering live in
later units; :meth:`Capacity.serve_limit` and :meth:`Capacity.accept_limit` are
the bounds those units read, and :meth:`Capacity.split_draw` is the composition
rule they apply to a withdrawal.
"""

import math
import typing

import Pycatshoo as pyc
import pydantic
from colored import attr, fg

import cod3s

#: The two sides a capacity may sit on, relative to the component's rules.
SIDES = ("in", "out")

#: Occurrence law of the empty/full transitions: they fire as soon as their
#: condition holds. Registered as watched transitions, so the solver stops the
#: integration exactly at the crossing rather than at the following step (R7).
_INSTANT_OCC_LAW = {"cls": "delay", "time": 0}


def _fresh_instant_occ_law():
    """A private copy of the instantaneous law.

    ``TransitionModel.sanitize_occ_law`` rewrites the ``cls`` entry in place,
    so a shared mapping would be capitalised twice.
    """
    return dict(_INSTANT_OCC_LAW)


class CapacityFlow(cod3s.ObjCOD3S):
    """One flow held by a capacity, and the volume one unit of it occupies."""

    name: str = pydantic.Field(..., description="Name of the held flow")

    weight: float = pydantic.Field(
        1.0,
        description=(
            "Volume one unit of this flow occupies in the capacity. Governs "
            "occupancy only -- NOT how a withdrawal is composed (R35)."
        ),
    )

    side: typing.Optional[str] = pydantic.Field(
        None,
        description=(
            "Side this flow resolved against, 'in' or 'out'. Written back by "
            "ObjFlow.add_capacity; every flow of one capacity must share it."
        ),
    )

    @pydantic.field_validator("weight")
    @classmethod
    def check_weight(cls, value):
        if not (value > 0):
            raise ValueError(
                f"Capacity flow weight must be strictly positive, got {value}"
            )
        return value

    @pydantic.field_validator("side")
    @classmethod
    def check_side(cls, value):
        if value is not None and value not in SIDES:
            raise ValueError(f"Capacity flow side must be 'in' or 'out', got {value!r}")
        return value

    def __repr__(self) -> str:
        return f"{self.name}(x{self.weight:g})"

    def __str__(self) -> str:
        return self.__repr__()


class Capacity(cod3s.ObjCOD3S):
    """A volume held by a component over one or more of its continuous flows."""

    name: str = pydantic.Field(..., description="Capacity name")

    flows: typing.List[CapacityFlow] = pydantic.Field(
        ..., description="Flows the capacity holds, with their volume weights"
    )

    capacity: float = pydantic.Field(..., description="The volume the held flows share")

    side: str = pydantic.Field(
        "in",
        description=(
            "Side of the component's rules the capacity sits on: 'in' places "
            "it upstream of them, 'out' downstream."
        ),
    )

    content_init: typing.Dict[str, float] = pydantic.Field(
        default_factory=dict,
        description="Initial raw quantity per held flow; omitted flows start at 0",
    )

    # -- Backend handles. Never serialised: the declaration above is enough to
    # -- rebuild them, and they hold PyCATSHOO objects.
    var_qty: typing.Dict[str, typing.Any] = pydantic.Field(
        default_factory=dict,
        exclude=True,
        repr=False,
        description="Per-flow raw quantity ODE variables",
    )

    var_fill: typing.Dict[str, typing.Any] = pydantic.Field(
        default_factory=dict,
        exclude=True,
        repr=False,
        description="Per-flow weighted fill explicit variables",
    )

    var_inflow: typing.Dict[str, typing.Any] = pydantic.Field(
        default_factory=dict,
        exclude=True,
        repr=False,
        description="Per-flow rate entering the capacity, written by the sweeps",
    )

    var_outflow: typing.Dict[str, typing.Any] = pydantic.Field(
        default_factory=dict,
        exclude=True,
        repr=False,
        description="Per-flow rate leaving the capacity, written by the sweeps",
    )

    var_qty_total: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Total raw quantity ODE variable"
    )

    var_fill_total: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description="Total weighted fill explicit variable",
    )

    automaton: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="The empty/full automaton"
    )

    state_empty: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Backend 'empty' state"
    )

    state_full: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Backend 'full' state"
    )

    # ------------------------------------------------------------------
    # Declaration-time validation
    # ------------------------------------------------------------------

    @pydantic.field_validator("flows", mode="before")
    @classmethod
    def normalize_flows(cls, value):
        """Accept a name, a mapping, or a list mixing both."""
        if value is None:
            raise ValueError("A capacity must hold at least one flow")
        if isinstance(value, (str, dict, CapacityFlow)):
            value = [value]
        if not isinstance(value, (list, set, tuple)):
            raise ValueError(f"Bad format for capacity 'flows' : {value!r}")

        entries = []
        for item in value:
            if isinstance(item, str):
                entries.append({"name": item})
            elif isinstance(item, (dict, CapacityFlow)):
                entries.append(item)
            else:
                raise ValueError(f"Bad format for capacity flow entry : {item!r}")
        return entries

    @pydantic.field_validator("capacity")
    @classmethod
    def check_capacity(cls, value):
        if not (value > 0):
            raise ValueError(f"Capacity volume must be strictly positive, got {value}")
        return value

    @pydantic.field_validator("side")
    @classmethod
    def check_side(cls, value):
        if value not in SIDES:
            raise ValueError(f"Capacity side must be 'in' or 'out', got {value!r}")
        return value

    @pydantic.model_validator(mode="after")
    def check_declaration(self):
        if not self.flows:
            raise ValueError(f"Capacity {self.name} must hold at least one flow")

        names = [entry.name for entry in self.flows]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"Capacity {self.name} holds the same flow twice : "
                f"{', '.join(duplicates)}"
            )

        unknown = sorted(set(self.content_init) - set(names))
        if unknown:
            raise ValueError(
                f"Capacity {self.name}: 'content_init' names flows the capacity "
                f"does not hold : {', '.join(unknown)}"
            )

        # A held flow missing from content_init simply starts empty.
        for name in names:
            self.content_init.setdefault(name, 0.0)

        return self

    # ------------------------------------------------------------------
    # Declaration accessors
    # ------------------------------------------------------------------

    @property
    def flow_names(self) -> typing.List[str]:
        """The held flow names, in declaration order."""
        return [entry.name for entry in self.flows]

    def flow_entry(self, flow_name) -> CapacityFlow:
        """The declaration entry of one held flow."""
        for entry in self.flows:
            if entry.name == flow_name:
                return entry
        raise ValueError(f"Capacity {self.name} does not hold flow {flow_name}")

    def weight_of(self, flow_name) -> float:
        """The volume one unit of ``flow_name`` occupies."""
        return self.flow_entry(flow_name).weight

    # ------------------------------------------------------------------
    # Backend construction
    # ------------------------------------------------------------------

    def add_variables(self, comp):
        """Declare the quantity, fill and transit variables on ``comp``."""
        qty_total = 0.0
        fill_total = 0.0

        for entry in self.flows:
            qty_init = float(self.content_init.get(entry.name, 0.0))
            fill_init = qty_init * entry.weight / self.capacity

            self.var_qty[entry.name] = comp.addVariable(
                f"{self.name}_qty_{entry.name}", pyc.TVarType.t_double, qty_init
            )
            self.var_fill[entry.name] = comp.addVariable(
                f"{self.name}_fill_{entry.name}", pyc.TVarType.t_double, fill_init
            )
            self.var_inflow[entry.name] = comp.addVariable(
                f"{self.name}_inflow_{entry.name}", pyc.TVarType.t_double, 0.0
            )
            self.var_outflow[entry.name] = comp.addVariable(
                f"{self.name}_outflow_{entry.name}", pyc.TVarType.t_double, 0.0
            )

            qty_total += qty_init
            fill_total += fill_init

        self.var_qty_total = comp.addVariable(
            f"{self.name}_qty", pyc.TVarType.t_double, qty_total
        )
        self.var_fill_total = comp.addVariable(
            f"{self.name}_fill", pyc.TVarType.t_double, fill_total
        )

    def add_mb(self, comp):
        """Publish the level as a read-only export (R33).

        Only exports: the observing side imports into references, which carry
        no setter at all, so the link cannot be written from downstream.
        """
        mb_name = f"{self.name}_level_out"
        comp.addMessageBox(mb_name)
        comp.addMessageBoxExport(mb_name, self.var_qty_total, f"{self.name}_level")
        comp.addMessageBoxExport(mb_name, self.var_fill_total, f"{self.name}_fill")

    def add_automaton(self, comp):
        """Build the empty/full automaton driven by the total weighted fill."""
        st_empty = f"{self.name}_empty"
        st_partial = f"{self.name}_partial"
        st_full = f"{self.name}_full"

        fill_init = self.total_fill()
        if fill_init <= 0.0:
            init_state = st_empty
        elif fill_init >= 1.0:
            init_state = st_full
        else:
            init_state = st_partial

        trans_names = {
            "empty_partial": f"{self.name}_empty_partial",
            "partial_empty": f"{self.name}_partial_empty",
            "partial_full": f"{self.name}_partial_full",
            "full_partial": f"{self.name}_full_partial",
        }

        aut = cod3s.PycAutomaton(
            name=f"{comp.name()}_{self.name}_bounds",
            states=[st_empty, st_partial, st_full],
            init_state=init_state,
            transitions=[
                {
                    "name": trans_names["empty_partial"],
                    "source": st_empty,
                    "target": st_partial,
                    "is_interruptible": True,
                    "occ_law": _fresh_instant_occ_law(),
                },
                {
                    "name": trans_names["partial_empty"],
                    "source": st_partial,
                    "target": st_empty,
                    "is_interruptible": True,
                    "occ_law": _fresh_instant_occ_law(),
                },
                {
                    "name": trans_names["partial_full"],
                    "source": st_partial,
                    "target": st_full,
                    "is_interruptible": True,
                    "occ_law": _fresh_instant_occ_law(),
                },
                {
                    "name": trans_names["full_partial"],
                    "source": st_full,
                    "target": st_partial,
                    "is_interruptible": True,
                    "occ_law": _fresh_instant_occ_law(),
                },
            ],
        )
        aut.update_bkd(comp)

        # Conditions read the ODE quantities directly rather than the explicit
        # fill variables: the bound is a property of the integrated state, and
        # the solver root-finds the crossing on it.
        conditions = {
            "empty_partial": lambda: self.total_fill() > 0.0,
            "partial_empty": lambda: self.total_fill() <= 0.0,
            "partial_full": lambda: self.total_fill() >= 1.0,
            "full_partial": lambda: self.total_fill() < 1.0,
        }
        for key, condition in conditions.items():
            aut.get_transition_by_name(trans_names[key])._bkd.setCondition(condition)

        self.automaton = aut
        self.state_empty = aut.get_state_by_name(st_empty)._bkd
        self.state_full = aut.get_state_by_name(st_full)._bkd

        comp.automata_d[aut.name] = aut

        return aut

    def register(self, system):
        """Register the capacity's variables and bound transitions on the PDMP.

        The empty/full transitions are registered as **watched** so a bound is
        crossed exactly rather than at the next integration step (R7).
        """
        for entry in self.flows:
            system.pdmp_add_ode_variable(self.var_qty[entry.name])
            system.pdmp_add_explicit_variable(self.var_fill[entry.name])
        system.pdmp_add_ode_variable(self.var_qty_total)
        system.pdmp_add_explicit_variable(self.var_fill_total)

        for transition in self.automaton.transitions:
            system.pdmp_add_watched_transition(transition)

    # ------------------------------------------------------------------
    # The capacity's own equation
    # ------------------------------------------------------------------

    def compute(self):
        """Integrate the levels and refresh the fills.

        One integration step's worth of work: every level integrates the
        imbalance between what enters and what leaves, and the reported fills
        follow the quantities.
        """
        rate_total = 0.0
        fill_total = 0.0

        for entry in self.flows:
            rate = (
                self.var_inflow[entry.name].value()
                - self.var_outflow[entry.name].value()
            )
            self.var_qty[entry.name].setDvdtODE(rate)
            rate_total += rate

            fill = self.var_qty[entry.name].value() * entry.weight / self.capacity
            self.var_fill[entry.name].setValue(fill)
            fill_total += fill

        self.var_qty_total.setDvdtODE(rate_total)
        self.var_fill_total.setValue(fill_total)

    # ------------------------------------------------------------------
    # Reported state (R28)
    # ------------------------------------------------------------------

    def get_quantity(self, flow_name=None) -> float:
        """The raw quantity held, of one flow or of the capacity as a whole."""
        if flow_name is None:
            return sum(var.value() for var in self.var_qty.values())
        return self.var_qty[flow_name].value()

    def get_fill(self, flow_name=None) -> float:
        """The weighted fill reported, of one flow or of the whole capacity."""
        if flow_name is None:
            return self.var_fill_total.value()
        return self.var_fill[flow_name].value()

    def total_quantity(self) -> float:
        """The total raw quantity, recomputed from the per-flow levels."""
        return self.get_quantity()

    def total_fill(self) -> float:
        """The total weighted fill, recomputed from the per-flow levels.

        Recomputed rather than read back from ``var_fill_total`` so the bound
        conditions never lag one integration step behind the levels.
        """
        return (
            sum(self.var_qty[entry.name].value() * entry.weight for entry in self.flows)
            / self.capacity
        )

    # ------------------------------------------------------------------
    # Transit -- the two hooks the sweeps write (KTD13)
    # ------------------------------------------------------------------

    def get_inflow(self, flow_name=None) -> float:
        """The rate currently entering the capacity."""
        if flow_name is None:
            return sum(var.value() for var in self.var_inflow.values())
        return self.var_inflow[flow_name].value()

    def set_inflow(self, flow_name, rate):
        """Set the rate currently entering the capacity for one flow."""
        self.var_inflow[flow_name].setValue(float(rate))

    def get_outflow(self, flow_name=None) -> float:
        """The rate currently leaving the capacity."""
        if flow_name is None:
            return sum(var.value() for var in self.var_outflow.values())
        return self.var_outflow[flow_name].value()

    def set_outflow(self, flow_name, rate):
        """Set the rate currently leaving the capacity for one flow."""
        self.var_outflow[flow_name].setValue(float(rate))

    # ------------------------------------------------------------------
    # Bounds (R7)
    # ------------------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        """True while the capacity holds nothing."""
        if self.state_empty is None:
            return self.total_fill() <= 0.0
        return bool(self.state_empty.isActive())

    @property
    def is_full(self) -> bool:
        """True once the capacity has reached its volume."""
        if self.state_full is None:
            return self.total_fill() >= 1.0
        return bool(self.state_full.isActive())

    def serve_limit(self, flow_name=None) -> float:
        """What the capacity can serve onward.

        Unbounded while it holds something; once empty, limited to what
        currently transits through it -- what enters it right now (R7).
        """
        if not self.is_empty:
            return math.inf
        return self.get_inflow(flow_name)

    def accept_limit(self, flow_name=None) -> float:
        """What the capacity can accept.

        Unbounded while there is room; once full, limited to what currently
        transits through it -- what leaves it right now, which is what makes a
        full capacity reduce the demand it propagates upstream (R7).
        """
        if not self.is_full:
            return math.inf
        return self.get_outflow(flow_name)

    # ------------------------------------------------------------------
    # Extraction (R35)
    # ------------------------------------------------------------------

    def split_draw(self, quantity) -> typing.Dict[str, float]:
        """Split a draw over the held flows, at their **raw** quantity share.

        Weights govern how much volume a unit occupies, not how a withdrawal is
        composed, so the share is computed on raw quantities. Each share is
        clamped to the unit interval, so a negative content cannot invert the
        split, and a capacity holding nothing serves zero of everything rather
        than dividing by a null total.
        """
        quantity = float(quantity)
        total = self.total_quantity()

        if total <= 0.0:
            return {entry.name: 0.0 for entry in self.flows}

        draw = {}
        for entry in self.flows:
            share = self.var_qty[entry.name].value() / total
            share = min(max(share, 0.0), 1.0)
            draw[entry.name] = quantity * share
        return draw

    # ------------------------------------------------------------------
    # Representation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        held = ", ".join(repr(entry) for entry in self.flows)
        return (
            f"{fg('cyan')}{self.__class__.__name__}{attr('reset')} "
            f"{fg('blue')}{self.name}{attr('reset')} "
            f"[{self.side}] {held} / {self.capacity:g}"
        )

    def __str__(self) -> str:
        lines = [self.__repr__()]
        for entry in self.flows:
            lines.append(
                f"  {fg('white')}{entry.name}{attr('reset')}: "
                f"qty={self.get_quantity(entry.name):g} "
                f"fill={self.get_fill(entry.name):g} "
                f"(weight {entry.weight:g})"
            )
        lines.append(
            f"  {fg('white')}total{attr('reset')}: "
            f"qty={self.total_quantity():g} fill={self.total_fill():g}"
        )
        return "\n".join(lines)


class MeasurementIn(cod3s.ObjCOD3S):
    """The importing side of a measurement link (R33).

    Declared with :meth:`muscadet.ObjFlow.add_measurement_in` on the component
    that *observes* a capacity. Both endpoints are PyCATSHOO references, which
    carry no setter: the observing component reads the level and can never
    write it. The link exchanges no quantity and enters no allocation.
    """

    name: str = pydantic.Field(
        ...,
        description=(
            "Measurement channel name. Matches the observed capacity's name, "
            "which is what makes the exported and imported aliases line up."
        ),
    )

    level_default: float = pydantic.Field(
        0.0, description="Level read while the link is not connected"
    )

    fill_default: float = pydantic.Field(
        0.0, description="Fill read while the link is not connected"
    )

    var_level: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Reference on the observed level"
    )

    var_fill: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Reference on the observed fill"
    )

    def add_variables(self, comp):
        self.var_level = comp.addReference(f"{self.name}_level_in")
        self.var_fill = comp.addReference(f"{self.name}_fill_in")

        # A measurement observes exactly one capacity.
        self.var_level.setCnctMax(1)
        self.var_fill.setCnctMax(1)

    def add_mb(self, comp):
        mb_name = f"{self.name}_level_in"
        comp.addMessageBox(mb_name)
        comp.addMessageBoxImport(mb_name, self.var_level, f"{self.name}_level")
        comp.addMessageBoxImport(mb_name, self.var_fill, f"{self.name}_fill")

    def get_level(self) -> float:
        """The total raw quantity held by the observed capacity."""
        return self.var_level.sumValue(self.level_default)

    def get_fill(self) -> float:
        """The total weighted fill of the observed capacity."""
        return self.var_fill.sumValue(self.fill_default)

    @property
    def is_connected(self) -> bool:
        """True once the link is wired to a capacity's export."""
        return self.var_level is not None and self.var_level.nbCnx() > 0

    def __repr__(self) -> str:
        return (
            f"{fg('cyan')}{self.__class__.__name__}{attr('reset')} "
            f"{fg('blue')}{self.name}{attr('reset')} = "
            f"{self.get_level() if self.var_level is not None else 'N/A'}"
        )

    def __str__(self) -> str:
        return self.__repr__()


def allocate_capacity_equation_order(system) -> int:
    """Allocate a distinct PDMP equation order for a capacity equation.

    PyCATSHOO falls back to alphabetical equation-name order when two equations
    share an order value (KTD3), so each capacity equation must get its own
    integer. A capacity's equation only reads its transit variables and writes
    its own levels, so *any* distinct order is correct here; the graph-derived
    allocation of the ordering unit supersedes this one.
    """
    order = getattr(system, "_capacity_equation_order_next", 0)
    system._capacity_equation_order_next = order + 1
    return order
