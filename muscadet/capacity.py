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
the bounds those units read, :meth:`Capacity.demand_claim` is what a capacity
makes of the demand crossing it -- filling while it has room (R36), throttling
once full (R7) -- and :meth:`Capacity.split_draw` is the composition rule they
apply to a withdrawal.
"""

import math
import statistics
import typing

import Pycatshoo as pyc
import pydantic
from colored import attr, fg

import cod3s

from .common import entity_label, fresh_instant_occ_law

#: The two sides a capacity may sit on, relative to the component's rules.
SIDES = ("in", "out")


# ----------------------------------------------------------------------
# Combination policies (R37) -- how ONE measurement channel reduces the
# several readings arriving on it to a single number
# ----------------------------------------------------------------------
#
# The mirror image of the allocation policies of ``muscadet.flow_continuous``.
# An output declares how it SPLITS one quantity among several consumers; a
# measurement input declares how it COMBINES the several readings its producers
# publish. Same shape, same extension point: a named policy, or a Python
# callable used in preference to it.
#
# Deliberately confined to the MEASUREMENT channel, and unreachable from a flow.
# A continuous flow carries a conserved quantity: taking the median of three
# pipes delivering water would create or destroy matter, and a continuous input
# is therefore, and permanently, the SUM of its connections. See
# ``FlowModel.check_combine_is_not_a_flow_policy``, which refuses the key by
# name rather than leaving the restriction to a docstring.


#: Add the readings up. What a single-connection channel has always done, and
#: therefore the only policy that leaves an existing model unchanged.
COMBINE_SUM = "sum"

#: The arithmetic mean of the readings. Rejects nothing: one wild reading drags
#: the estimate by its full deviation divided by the connection count.
COMBINE_MEAN = "mean"

#: The median of the readings -- the estimator redundant sensors exist for. With
#: an odd number of readings a single stuck or wild one cannot move it at all,
#: which is precisely what a mean does not give.
COMBINE_MEDIAN = "median"

#: The smallest reading. A conservative reading of a redundant set.
COMBINE_MIN = "min"

#: The largest reading. The pessimistic counterpart of :data:`COMBINE_MIN`.
COMBINE_MAX = "max"

#: Every policy name a measurement channel may declare.
COMBINE_POLICIES = (
    COMBINE_SUM,
    COMBINE_MEAN,
    COMBINE_MEDIAN,
    COMBINE_MIN,
    COMBINE_MAX,
)


def combine_sum(values):
    """Sum of the readings."""
    return float(sum(values))


def combine_mean(values):
    """Arithmetic mean of the readings."""
    return float(statistics.fmean(values))


def combine_median(values):
    """Median of the readings.

    On an even count this is the mean of the two central readings, as
    ``statistics.median`` defines it. Two sensors therefore give no rejection at
    all -- a redundant set is sized odd for a reason, and the library does not
    pretend otherwise by inventing a tie-break.
    """
    return float(statistics.median(values))


def combine_min(values):
    """Smallest reading."""
    return float(min(values))


def combine_max(values):
    """Largest reading."""
    return float(max(values))


#: The named policies, resolved to the function each one designates.
COMBINE_FUNCTIONS = {
    COMBINE_SUM: combine_sum,
    COMBINE_MEAN: combine_mean,
    COMBINE_MEDIAN: combine_median,
    COMBINE_MIN: combine_min,
    COMBINE_MAX: combine_max,
}


def combine(values, policy=None, combine_fun=None, default=0.0):
    """Reduce the readings of one measurement channel to a single number.

    Parameters
    ----------
    values : sequence of float
        One reading per connection, in connection order.
    policy : str, optional
        A name from :data:`COMBINE_POLICIES`. Defaults to :data:`COMBINE_SUM`,
        which is the identity on a single reading.
    combine_fun : callable, optional
        ``f(values) -> float``, the Python extension point, used in
        **preference** to the named policy -- exactly as ``allocation_fun`` is
        used in preference to ``allocation``.
    default : float, optional
        What an EMPTY reading list combines to. A channel connected to nobody
        reads its declared default rather than raising, which is what a
        single-connection measurement has always done.

    Returns
    -------
    float
    """
    values = list(values)

    if not values:
        return float(default)

    if combine_fun is not None:
        return float(combine_fun(values))

    return COMBINE_FUNCTIONS[COMBINE_SUM if policy is None else policy](values)


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
    def check_weight(cls, value, info):
        if not (value > 0):
            raise ValueError(
                f"{entity_label('Capacity flow', info)}: weight must be strictly "
                f"positive, got {value}"
            )
        return value

    @pydantic.field_validator("side")
    @classmethod
    def check_side(cls, value, info):
        if value is not None and value not in SIDES:
            raise ValueError(
                f"{entity_label('Capacity flow', info)}: side must be 'in' or "
                f"'out', got {value!r}"
            )
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

    fill_rate: float = pydantic.Field(
        0.0,
        description=(
            "Rate this capacity claims for itself, on EACH held flow, over and "
            "above the demand it already carries, for as long as it has room "
            "(R36). 0 -- the default -- is a pure buffer: it asks for exactly "
            "what passes through it and therefore never stocks up. math.inf is "
            "'whatever the producer can deliver', which needs no bound of its "
            "own since a delivery is already the lesser of production and "
            "demand (R6)."
        ),
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

    #: Memoised :attr:`flow_names`. A private attribute rather than a field: it
    #: is derived from ``flows`` and must never reach a dump.
    _flow_names: typing.Optional[typing.List[str]] = pydantic.PrivateAttr(default=None)

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
    def check_capacity(cls, value, info):
        if not (value > 0):
            raise ValueError(
                f"{entity_label('Capacity', info)}: volume must be strictly "
                f"positive, got {value}"
            )
        return value

    @pydantic.field_validator("side")
    @classmethod
    def check_side(cls, value, info):
        if value not in SIDES:
            raise ValueError(
                f"{entity_label('Capacity', info)}: side must be 'in' or 'out', "
                f"got {value!r}"
            )
        return value

    @pydantic.field_validator("fill_rate")
    @classmethod
    def check_fill_rate(cls, value, info):
        if value < 0 or value != value:  # negative or NaN
            raise ValueError(
                f"{entity_label('Capacity', info)}: fill rate must be positive "
                f"or zero, got {value}"
            )
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
        """The held flow names, in declaration order.

        Memoised: the held flows are settled once :meth:`check_declaration` has
        validated them -- nothing reassigns ``flows`` afterwards, and the only
        thing resolution writes back is each entry's ``side`` -- while this is
        read once per held flow per equation evaluation.
        """
        if self._flow_names is None:
            self._flow_names = [entry.name for entry in self.flows]
        return self._flow_names

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
                    "occ_law": fresh_instant_occ_law(),
                },
                {
                    "name": trans_names["partial_empty"],
                    "source": st_partial,
                    "target": st_empty,
                    "is_interruptible": True,
                    "occ_law": fresh_instant_occ_law(),
                },
                {
                    "name": trans_names["partial_full"],
                    "source": st_partial,
                    "target": st_full,
                    "is_interruptible": True,
                    "occ_law": fresh_instant_occ_law(),
                },
                {
                    "name": trans_names["full_partial"],
                    "source": st_full,
                    "target": st_partial,
                    "is_interruptible": True,
                    "occ_law": fresh_instant_occ_law(),
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

        system.pdmp_add_watched_automaton(self.automaton)

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

        Bounded **per constituent as well as per capacity**. The empty/full
        automaton watches the TOTAL weighted fill, so a volume holding 0 of one
        flow and plenty of another is not empty and would otherwise serve the
        depleted one without limit -- a model would consume a reagent it does
        not hold, and that constituent's level would go negative. A flow holding
        nothing can only serve what currently transits through it, which is the
        same R7 bound the automaton applies to the whole volume.

        The two bounds are distinct on purpose: the automaton stays the volume
        bound, watched and crossed exactly, and the per-flow test below only
        stops a constituent going negative. On a single-flow capacity they
        coincide -- a null quantity is a null fill -- so nothing changes there.

        The withdrawal side needs no such guard: :meth:`split_draw` composes a
        draw at each flow's RAW share of the total, so a nearly depleted
        constituent is served in proportion to what is left of it and decays
        towards zero instead of crossing it.
        """
        if flow_name is not None and self.get_quantity(flow_name) <= 0.0:
            return self.get_inflow(flow_name)

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

    def fill_claim(self, flow_name=None) -> float:
        """What the capacity claims for ITSELF, on top of what it passes on (R36).

        Its declared :attr:`fill_rate` for as long as there is room, and zero
        once full -- the moment the throttling half of R7 takes over and cuts
        the demand down to what currently leaves it.

        A capacity is entitled to claim while any room is left, so the claim
        does NOT taper with the remaining headroom: a claim shrinking to zero as
        the level approaches the volume would make a tank fill asymptotically
        and never reach the bound its own automaton watches.
        """
        if self.is_full:
            return 0.0
        return float(self.fill_rate)

    def demand_claim(self, demand, flow_name=None) -> float:
        """The demand the capacity carries upstream, given the one it holds.

        The two halves of a capacity's effect on demand, in one closed-form
        expression of values the reverse sweep already knows (KTD12):

        - **filling** (R36) -- while there is room it ADDS its declared fill
          rate to the demand passing through it, so it accumulates whatever its
          producer delivers beyond what its consumers draw. Nothing here bounds
          that claim: the delivery is already the lesser of production and
          demand (R6), so the producer's own capability is what a tank fills at;
        - **throttling** (R7) -- once full the claim collapses to zero and the
          accept bound cuts the demand down to what currently leaves it, so the
          producer feeding a capacity at its volume delivers less (AE11).

        Parameters
        ----------
        demand : float
            The demand already carried through the capacity -- what the
            downstream asks of an output capacity, what the rules ask of an
            input one. Possibly ``math.inf``.
        flow_name : str, optional
            The held flow the demand bears on.

        Returns
        -------
        float
            The demand to carry further upstream, possibly ``math.inf``.
        """
        return min(demand + self.fill_claim(flow_name), self.accept_limit(flow_name))

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
    """The importing side of a measurement link (R33, R37).

    Declared with :meth:`muscadet.ObjFlow.add_measurement_in` on the component
    that *observes* a level. Both endpoints are PyCATSHOO references, which
    carry no setter: the observing component reads the level and can never
    write it. The link exchanges no quantity and enters no allocation.

    **One source, or several with a stated policy.** By default the channel
    observes exactly one publisher, and its reading is that publisher's --
    ``setCnctMax(1)`` refuses a second connection, as it always has. Declaring
    ``combine`` (or ``combine_fun``) lifts the cap and states how the several
    readings reduce to one: ``"median"`` is what a redundant sensor set exists
    for, and ``"sum"`` is the generalisation of the single-source case. There is
    no way to reach many-to-one WITHOUT saying how the readings combine, which
    is the point -- a silent sum over redundant sensors is a wrong model, not a
    default.
    """

    name: str = pydantic.Field(
        ...,
        description=(
            "Measurement channel name. Matches the observed publisher's name, "
            "which is what makes the exported and imported aliases line up."
        ),
    )

    level_default: float = pydantic.Field(
        0.0, description="Level read while the link is not connected"
    )

    fill_default: float = pydantic.Field(
        0.0, description="Fill read while the link is not connected"
    )

    combine: typing.Optional[str] = pydantic.Field(
        None,
        description=(
            "How the readings of SEVERAL publishers reduce to one (R37): a "
            "name from COMBINE_POLICIES. None -- the default -- is the "
            "single-source channel muscadet has always had, capped at one "
            "connection. Declaring any policy lifts the cap."
        ),
    )

    combine_fun: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description=(
            "The Python extension point of R37: a callable "
            "``f(values) -> float`` over the per-connection readings, used in "
            "PREFERENCE to the declared policy -- the mirror of "
            "``FlowContinuousOut.allocation_fun``. Declaring it also lifts the "
            "single-connection cap."
        ),
    )

    var_level: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Reference on the observed level"
    )

    var_fill: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Reference on the observed fill"
    )

    @pydantic.model_validator(mode="after")
    def check_combine(self):
        """Refuse an unusable combination policy at DECLARATION time.

        Like every other malformed declaration in this release: a misspelt
        policy would otherwise only be found on the first reading, and a
        measurement is read from inside a threshold condition where the
        exception has nowhere useful to surface.
        """
        if self.combine is not None and self.combine not in COMBINE_POLICIES:
            raise ValueError(
                f"Measurement channel {self.name}: unknown combination policy "
                f"{self.combine!r}, expected one of {', '.join(COMBINE_POLICIES)}"
            )

        if self.combine_fun is not None and not callable(self.combine_fun):
            raise ValueError(
                f"Measurement channel {self.name}: combine_fun must be a "
                f"callable ``f(values) -> float``, got {self.combine_fun!r}"
            )

        return self

    @property
    def combines_several(self) -> bool:
        """True when this channel was declared as a many-to-one reading."""
        return self.combine is not None or self.combine_fun is not None

    def add_variables(self, comp):
        self.var_level = comp.addReference(f"{self.name}_level_in")
        self.var_fill = comp.addReference(f"{self.name}_fill_in")

        if not self.combines_several:
            # A measurement observes exactly one publisher unless the
            # declaration says how several of them combine.
            self.var_level.setCnctMax(1)
            self.var_fill.setCnctMax(1)

    def add_mb(self, comp):
        mb_name = f"{self.name}_level_in"
        comp.addMessageBox(mb_name)
        comp.addMessageBoxImport(mb_name, self.var_level, f"{self.name}_level")
        comp.addMessageBoxImport(mb_name, self.var_fill, f"{self.name}_fill")

    def readings(self, var, default) -> typing.List[float]:
        """The per-connection readings of one reference, in connection order.

        The whole reason a combination policy needs a channel of its own: a
        median cannot be recovered from a sum, so the individual values must be
        reachable. ``sumValue`` is what the single-source path uses and it
        collapses them irreversibly.
        """
        if var is None:
            return []

        return [float(var.value(index)) for index in range(var.cnctCount())]

    def get_level(self) -> float:
        """The level read on this channel, combined over its publishers."""
        if not self.combines_several:
            return self.var_level.sumValue(self.level_default)

        return combine(
            self.readings(self.var_level, self.level_default),
            policy=self.combine,
            combine_fun=self.combine_fun,
            default=self.level_default,
        )

    def get_fill(self) -> float:
        """The fill read on this channel, combined over its publishers."""
        if not self.combines_several:
            return self.var_fill.sumValue(self.fill_default)

        return combine(
            self.readings(self.var_fill, self.fill_default),
            policy=self.combine,
            combine_fun=self.combine_fun,
            default=self.fill_default,
        )

    @property
    def is_connected(self) -> bool:
        """True once the link is wired to at least one publisher."""
        return self.var_level is not None and self.var_level.nbCnx() > 0

    def __repr__(self) -> str:
        policy = f" [{self.combine}]" if self.combine else ""
        return (
            f"{fg('cyan')}{self.__class__.__name__}{attr('reset')} "
            f"{fg('blue')}{self.name}{attr('reset')}{policy} = "
            f"{self.get_level() if self.var_level is not None else 'N/A'}"
        )

    def __str__(self) -> str:
        return self.__repr__()


class MeasurementOut(cod3s.ObjCOD3S):
    """The publishing side of a measurement link, on a component (R37).

    A capacity publishes its own level (:meth:`Capacity.add_mb`); this is what
    lets **any** component publish one. Declared with
    :meth:`muscadet.ObjFlow.add_measurement_out`, it exports the very same two
    aliases under the very same box name, so an observer cannot tell a capacity
    from a republisher and needs no second kind of import.

    Why it has to exist
    -------------------
    Redundancy is not several observations of one tank -- those are identical
    and reject nothing. It is several *instruments*, each able to fail on its
    own, between the tank and whoever votes on them. An instrument is a
    component, so a component has to be able to publish a reading.

    What it publishes
    -----------------
    Either nothing of its own -- ``{name}_level`` is then a plain writable
    component variable, driven by a model, a test or a failure mode -- or, with
    ``source`` declared, the level of a capacity or of a measurement channel of
    the SAME component, refreshed at every integration step.

    ``{name}_level_gain`` is the public endpoint a failure mode clamps, created
    at 1 and multiplying whatever is published, exactly as ``{flow}_out_rate``
    does for a continuous output (KD10): a gain of 0 is a dead instrument, a
    gain of 5 a wild one. muscadet never writes it.

    Carries no quantity
    -------------------
    Like every measurement link: the box exports two doubles and imports
    nothing, there is no demand alias, no allocation, and the channel is not an
    edge of the continuous flow graph. ``source`` is restricted to a LEVEL --
    never a rate -- so a republished reading stays an integrated state, which is
    what keeps the R30 acyclicity argument intact.
    """

    name: str = pydantic.Field(
        ...,
        description=(
            "Published channel name. The observing side declares an import of "
            "the same name, which is what makes the aliases line up."
        ),
    )

    source: typing.Optional[str] = pydantic.Field(
        None,
        description=(
            "Name of a CAPACITY or of a measurement channel of the same "
            "component whose level is republished, refreshed at every "
            "integration step. None leaves the published variable a plain "
            "writable one nothing refreshes."
        ),
    )

    level_default: float = pydantic.Field(
        0.0, description="Level published before anything has been written"
    )

    fill_default: float = pydantic.Field(
        0.0, description="Fill published before anything has been written"
    )

    gain_default: float = pydantic.Field(
        1.0,
        description=(
            "Initial value of ``{name}_level_gain``, the factor everything "
            "published is multiplied by and the endpoint a failure mode clamps."
        ),
    )

    var_level: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Published level variable"
    )

    var_fill: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Published fill variable"
    )

    var_gain: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Public gain variable"
    )

    def add_variables(self, comp):
        self.var_level = comp.addVariable(
            f"{self.name}_level", pyc.TVarType.t_double, float(self.level_default)
        )
        self.var_fill = comp.addVariable(
            f"{self.name}_fill", pyc.TVarType.t_double, float(self.fill_default)
        )
        self.var_gain = comp.addVariable(
            f"{self.name}_level_gain", pyc.TVarType.t_double, float(self.gain_default)
        )
        # A gain a mode clamped must come back to what it was declared at when
        # the next Monte Carlo sequence starts, exactly like a derating.
        self.var_gain.setReinitialized(True)

    def add_mb(self, comp):
        """Publish the reading as a read-only export, capacity-compatible."""
        mb_name = f"{self.name}_level_out"
        comp.addMessageBox(mb_name)
        comp.addMessageBoxExport(mb_name, self.var_level, f"{self.name}_level")
        comp.addMessageBoxExport(mb_name, self.var_fill, f"{self.name}_fill")

    def get_gain(self) -> float:
        """The factor currently applied to everything this channel publishes."""
        return self.var_gain.value() if self.var_gain is not None else 1.0

    def publish(self, level, fill=None):
        """Write the reading, gain applied. ``fill`` defaults to the level."""
        gain = self.get_gain()
        self.var_level.setValue(float(level) * gain)
        self.var_fill.setValue(float(level if fill is None else fill) * gain)

    def read_source(self, comp):
        """The ``(level, fill)`` pair the declared source currently holds."""
        capacity = comp.capacities.get(self.source)
        if capacity is not None:
            return capacity.total_quantity(), capacity.total_fill()

        measurement = comp.measurements_in.get(self.source)
        if measurement is not None:
            return measurement.get_level(), measurement.get_fill()

        raise ValueError(
            f"Object {comp.name()}: published measurement {self.name} declares "
            f"source {self.source!r}, which is neither a capacity nor a "
            "measurement channel of this component"
        )

    def compute(self, comp):
        """Refresh the published reading from its source, if it declares one."""
        if self.source is None:
            return

        level, fill = self.read_source(comp)
        self.publish(level, fill)

    def get_level(self) -> float:
        """The level currently published."""
        return self.var_level.value() if self.var_level is not None else 0.0

    def get_fill(self) -> float:
        """The fill currently published."""
        return self.var_fill.value() if self.var_fill is not None else 0.0

    def __repr__(self) -> str:
        origin = f" <- {self.source}" if self.source else ""
        return (
            f"{fg('cyan')}{self.__class__.__name__}{attr('reset')} "
            f"{fg('blue')}{self.name}{attr('reset')}{origin} = "
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


def allocate_measurement_equation_order(system) -> int:
    """Allocate a distinct PDMP equation order for a published measurement.

    Its own band, ABOVE the capacity one: a republished reading is read from the
    level a capacity holds, so it must be refreshed once that level is current.
    Within the band the allocation is declaration order, which is what a *chain*
    of republishers depends on -- declare it upstream first. One hop, the shipped
    sensor's, needs nothing of the sort.
    """
    order = getattr(system, "_measurement_equation_order_next", 0)
    system._measurement_equation_order_next = order + 1
    return order
