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
``{c}_fill`` (total weighted fill), plus ``{c}_level_{f}`` and ``{c}_fill_{f}``
for every held flow. An observing component declares the matching import with
:meth:`muscadet.ObjFlow.add_measurement_in`, which creates the message box
``{c}_level_in`` importing the totals, and the constituents its ``flows=`` list
names, into *references*. A reference has no ``setValue``, so the importing
component cannot write the level; the link carries no quantity and takes part
in no allocation.

**Why per constituent.** The total is the one reading an INTENSIVE property
cannot be recovered from: a volume holding water and heat is at a temperature
of ``heat / water``, while ``{c}_level`` is the raw sum, which is neither term
and is not a quantity of anything when the constituents differ in nature. An
observer that wants the ratio needs both terms, so both are published, and an
instrument republishing a reading (:class:`MeasurementOut`) carries them too --
an observer cannot tell a capacity from a republisher, and that has to stay
true. Aliases are built in :func:`level_alias` / :func:`fill_alias`, in one
place, because a drift of one underscore between the two sides fails the whole
``connect`` rather than only the constituent.

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
from .flow_continuous import (
    FlowContinuousOut,
    rate_alias,
    rate_observation_box,
)

#: The two sides a capacity may sit on, relative to the component's rules.
SIDES = ("in", "out")

#: A measurement channel reads a capacity LEVEL: an integrated state, the kind
#: muscadet has always carried. The default, and byte-identical to 1.x.
MEASUREMENT_LEVEL = "level"

#: A measurement channel reads the RATE a continuous output delivers (R38). Same
#: observer, same read-only construction, a different quantity: the box pair is
#: ``{f}_rate_out`` / ``{f}_rate_in`` instead of ``{c}_level_out`` /
#: ``{c}_level_in``, and there is no fill and no constituent behind it.
MEASUREMENT_RATE = "rate"

#: Every nature a measurement channel may be declared with.
MEASUREMENT_KINDS = (MEASUREMENT_LEVEL, MEASUREMENT_RATE)


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


def level_alias(channel, flow=None):
    """The message-box alias carrying a measurement channel's level.

    ``flow=None`` is the WHOLE volume, the alias muscadet has always
    published. Naming a held flow gives that constituent's own level.

    Publisher and observer both build their alias here on purpose. The two
    sides are matched by string equality inside PyCATSHOO, and a mismatch is
    not a soft failure: the exporting box refuses the whole ``connect``, so a
    convention that drifted by one underscore would take the total down with
    the constituent.
    """
    return f"{channel}_level" if flow is None else f"{channel}_level_{flow}"


def fill_alias(channel, flow=None):
    """The alias carrying a channel's weighted fill. See :func:`level_alias`."""
    return f"{channel}_fill" if flow is None else f"{channel}_fill_{flow}"


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

        self.check_content_init()

        return self

    def check_content_init(self):
        """Refuse a starting content the capacity could never reach (R-5).

        ``capacity`` is checked strictly positive and ``fill_rate`` non-negative
        at declaration; the content they bound was not, and both ways out of
        the interval produce plausible-but-wrong numbers with no diagnostic:

        * **over the volume** -- ``capacity=100, content_init={"q": 500}``
          builds a tank at five times its own volume. The empty/full automaton
          initialises ``full``, the producer upstream is throttled from t=0,
          and the bound violation is the one thing that automaton cannot
          report, since the level is already past it when the run starts.
        * **negative** -- a negative level propagates into
          :meth:`split_draw`, whose share clamp exists precisely "so a negative
          content cannot invert the split". The clamp works around a state that
          is refused here instead.

        The bound is on the **weighted** total, not per flow: several
        constituents share one volume, and ``weight`` is how much of it a unit
        of each occupies. Two flows at 60 each, weighted 1 and 1, overfill a
        volume of 100 that neither of them exceeds on its own.
        """
        for name, quantity in self.content_init.items():
            if quantity < 0 or quantity != quantity:  # negative or NaN
                raise ValueError(
                    f"Capacity {self.name}: initial content of {name!r} must "
                    f"be positive or zero, got {quantity}"
                )

        weights = {entry.name: entry.weight for entry in self.flows}
        occupied = sum(
            quantity * weights[name] for name, quantity in self.content_init.items()
        )

        if occupied > self.capacity:
            held = ", ".join(
                f"{name}={self.content_init[name]:g}x{weights[name]:g}"
                for name in weights
                if self.content_init.get(name)
            )
            raise ValueError(
                f"Capacity {self.name}: initial content occupies {occupied:g} "
                f"of a volume of {self.capacity:g} ({held}); a capacity cannot "
                "start beyond the bound its own empty/full automaton watches"
            )

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
        """Publish the level as a read-only export (R33), total AND per flow.

        Only exports: the observing side imports into references, which carry
        no setter at all, so the link cannot be written from downstream.

        **Why the totals are not enough.** The total is precisely the thing an
        intensive property cannot be recovered from. A volume holding water and
        heat has a temperature of ``heat / water``, while ``{c}_level`` is
        their weighted sum, which is neither term. An observer wanting the
        ratio needs both, so both are published, under
        :func:`level_alias` / :func:`fill_alias`.

        **The extra aliases cost an existing model nothing**, measured rather
        than assumed: PyCATSHOO matches an import to an export by alias and
        simply ignores an export nobody imports, so a 1.x observer importing
        ``{c}_level`` alone still connects to this wider box and still reads
        the total. The reverse is refused, and refused *atomically* -- an
        import naming an alias the box does not export fails the whole
        ``connect``, taking the total with it -- which is why
        :meth:`muscadet.System.check_measurement_constituents` intercepts that
        case ahead of the engine and names what the volume actually holds.
        """
        mb_name = f"{self.name}_level_out"
        comp.addMessageBox(mb_name)
        comp.addMessageBoxExport(mb_name, self.var_qty_total, level_alias(self.name))
        comp.addMessageBoxExport(mb_name, self.var_fill_total, fill_alias(self.name))

        for entry in self.flows:
            comp.addMessageBoxExport(
                mb_name,
                self.var_qty[entry.name],
                level_alias(self.name, entry.name),
            )
            comp.addMessageBoxExport(
                mb_name,
                self.var_fill[entry.name],
                fill_alias(self.name, entry.name),
            )

    def published_flows(self):
        """Names of the constituents this capacity publishes per flow."""
        return [entry.name for entry in self.flows]

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

        # The reset map, on the automaton rather than on either state: it fires
        # on every change, and both directions may need it. Named per capacity
        # AND per component, two components being free to hold a capacity of
        # the same name.
        aut._bkd.addSensitiveMethod(
            f"clamp_{comp.name()}_{self.name}", self.clamp_to_bounds
        )

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

    def current_fill(self, flow_name=None) -> float:
        """The weighted fill recomputed from the ODE levels, of one flow or all.

        Recomputed rather than read back from ``var_fill`` / ``var_fill_total``,
        which are EXPLICIT variables the capacity equation writes: reading one
        back from another equation of the same step lags one integration step
        behind the levels. Everything that must not lag -- the bound
        conditions, and what a republisher hands an observer -- comes here.
        """
        if flow_name is None:
            return (
                sum(
                    self.var_qty[entry.name].value() * entry.weight
                    for entry in self.flows
                )
                / self.capacity
            )

        return (
            self.var_qty[flow_name].value() * self.weight_of(flow_name) / self.capacity
        )

    def total_fill(self) -> float:
        """The total weighted fill, recomputed from the per-flow levels."""
        return self.current_fill()

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

    def clamp_to_bounds(self) -> None:
        """Put the integrated state back ON a bound the solver just crossed.

        The PDMP reset map of this capacity, called when the empty/full
        automaton changes state. The crossing is root-found to ``dtCond``, so
        the solver stops just PAST the bound and leaves a residue there: a tank
        of 60 settles at 60.0026 with the default 0.001, and a tank draining to
        empty at -0.00086. Left alone the residue is permanent -- nothing pulls
        the level back -- so a level a modeller reads, an indicator, and the
        share clamp in :meth:`split_draw` all face a state outside the volume
        the model declares.

        Taking it back costs a conservation error of the same size, bounded by
        ``dtCond`` times the crossing rate and paid once per crossing. That
        trade is deliberate: a bound violation propagates, a one-off local
        error does not.

        **Who pays, at the full bound, is not "everyone".** The volume is
        shared, so scaling every constituent back is the tempting rule and it
        is wrong: it takes matter from a constituent that never moved. Measured
        on a tank holding water and syrup where only water flows in, scaling
        removed 0.0013 of syrup that no consumer ever received and no balance
        records -- small, but cumulative over every crossing and inexplicable
        by any flow. The excess is therefore charged to the constituents that
        were flowing IN, at their weighted share of that inflow. A constituent
        at rest pays nothing.

        **The empty bound needs no such rule.** ``total_fill() <= 0`` requires
        every constituent to be at or below zero, so there is no bystander to
        protect, and only the negative ones are lifted -- which is the per-flow
        guard :meth:`serve_limit` already describes, applied to the state
        rather than to the draw.
        """
        if self.is_empty:
            for entry in self.flows:
                var = self.var_qty[entry.name]
                if var.value() < 0.0:
                    var.setValue(0.0)
            self.resync_total()
            return

        if not self.is_full:
            return

        fill = self.total_fill()
        if fill <= 1.0:
            return

        excess = (fill - 1.0) * self.capacity  # weighted volume in excess

        # The constituents that were entering, at their weighted contribution.
        weighted_inflow = {
            entry.name: max(self.get_inflow(entry.name), 0.0) * entry.weight
            for entry in self.flows
        }
        payers = [entry for entry in self.flows if weighted_inflow[entry.name] > 0.0]
        if not payers:
            # Nothing was flowing in, so nothing caused the excess and there is
            # no defensible payer. Leaving the residue beats inventing one.
            return

        # A payer is charged its share of the excess, but never more than it
        # HOLDS: charging past that would push a constituent negative, which is
        # the very state this reset map exists to remove. What one payer cannot
        # cover falls to the others that still hold something, so a bounded
        # number of passes settles it. This has resisted every attempt to
        # trigger it -- a payer's holding is its inflow integrated since it
        # started, and its charge is that same inflow over one crossing search,
        # so it holds more than it owes unless it started flowing AT the
        # crossing. The guard is for that case, and costs one comparison.
        remaining = excess
        for _ in range(len(payers)):
            if remaining <= 0.0:
                break
            able = [entry for entry in payers if self.var_qty[entry.name].value() > 0.0]
            share_total = sum(weighted_inflow[entry.name] for entry in able)
            if not able or share_total <= 0.0:
                break

            unpaid = 0.0
            for entry in able:
                var = self.var_qty[entry.name]
                # The charge, converted from weighted volume to raw quantity.
                charge = (
                    remaining * weighted_inflow[entry.name] / share_total / entry.weight
                )
                paid = min(charge, var.value())
                var.setValue(var.value() - paid)
                unpaid += (charge - paid) * entry.weight
            remaining = unpaid

        self.resync_total()

    def resync_total(self) -> None:
        """Put ``var_qty_total`` back in step with the constituents.

        The total is an ODE variable of its own, integrating the sum of the
        per-flow rates rather than being derived from them, and it is the one
        exported on the measurement box as ``{c}_level``. Rewriting the
        constituents therefore leaves it behind, and it does not catch up: the
        solver integrates it onwards from wherever it was.

        The consequence is the one :meth:`clamp_to_bounds` exists to prevent,
        moved one channel over. Measured on a tank of 60 filled past its bound:
        ``get_quantity()`` read 60.000000 while an observer wired to the
        measurement link read 60.002605 -- the same tank reporting two levels
        depending on how it is asked, permanently, with the error re-accruing
        at every crossing. A sensor thresholding on that link, or an indicator
        naming it, sees a state outside the declared volume.

        ``var_fill_total`` needs no such treatment: it is explicit, recomputed
        from the per-flow quantities at every evaluation of ``compute``.
        """
        self.var_qty_total.setValue(sum(var.value() for var in self.var_qty.values()))

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

    **One observer, two natures of channel** (R38). ``kind="level"`` -- the
    default -- reads a capacity level or a republished reading, and is what this
    class has always been. ``kind="rate"`` reads what a continuous output
    DELIVERS, over the ``{f}_rate_out`` box that output publishes. Everything
    that makes an observer an observer is shared and stays shared: the endpoint
    is a reference, so the reading cannot be written; the link carries no
    quantity and no demand, so the observer takes no share of what it watches;
    and the same ``setCnctMax(1)`` / ``combine`` rule governs how many
    publishers one channel may read. Only the box name, the alias and the
    absence of a fill differ, which is why this is one class with a nature and
    not two.
    """

    name: str = pydantic.Field(
        ...,
        description=(
            "Measurement channel name. Matches the observed publisher's name, "
            "which is what makes the exported and imported aliases line up."
        ),
    )

    kind: str = pydantic.Field(
        MEASUREMENT_LEVEL,
        description=(
            "What this channel reads: 'level', a capacity level or a "
            "republished reading (the default, and what muscadet has always "
            "carried), or 'rate', the quantity a continuous output delivers "
            "(R38). A rate channel imports over '{f}_rate_in' instead of "
            "'{c}_level_in', reads no fill and carries no constituent."
        ),
    )

    flows: typing.List[str] = pydantic.Field(
        default_factory=list,
        description=(
            "Constituents of the observed volume to read individually, BESIDE "
            "the total this channel has always carried. Empty -- the default "
            "-- imports the total alone and is byte-identical to 1.x. Naming "
            "a flow is what lets an observer form an intensive property, which "
            "the total cannot be divided back into."
        ),
    )

    level_default: float = pydantic.Field(
        0.0, description="Level read while the link is not connected"
    )

    fill_default: float = pydantic.Field(
        0.0, description="Fill read while the link is not connected"
    )

    rate_default: float = pydantic.Field(
        0.0,
        description=(
            "Rate read while a kind='rate' link is not connected. Kept apart "
            "from level_default rather than folded into it: the two are read "
            "on different channels and a model declaring both natures would "
            "otherwise have to give one number two meanings."
        ),
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

    var_level_flow: typing.Dict[str, typing.Any] = pydantic.Field(
        default_factory=dict,
        exclude=True,
        repr=False,
        description="Per-constituent level references, keyed by flow name",
    )

    var_fill_flow: typing.Dict[str, typing.Any] = pydantic.Field(
        default_factory=dict,
        exclude=True,
        repr=False,
        description="Per-constituent fill references, keyed by flow name",
    )

    @pydantic.model_validator(mode="after")
    def check_kind(self):
        """Refuse an unknown channel nature at DECLARATION time (R38).

        A misspelt nature would otherwise build a box under a name no publisher
        exports, and the mistake would only surface at ``connect``, as an engine
        message naming a missing box rather than the typo that caused it.
        """
        if self.kind not in MEASUREMENT_KINDS:
            raise ValueError(
                f"Measurement channel {self.name}: unknown kind {self.kind!r}, "
                f"expected one of {', '.join(MEASUREMENT_KINDS)}"
            )

        return self

    @pydantic.model_validator(mode="after")
    def check_flows(self):
        """Refuse a malformed constituent list at DECLARATION time.

        A duplicate would declare the same reference twice under the same
        alias, which PyCATSHOO refuses far from the declaration that caused it.

        A constituent declared on a RATE channel is deliberately NOT refused
        here, and it is the one case where the mistake is better caught later:
        the reading of a rate has no constituent, but neither does a level
        published by a capacity that does not hold the named flow, and
        :meth:`muscadet.System.check_measurement_constituents` already tells a
        modeller which constituents the publisher on the other end actually
        carries. Two refusals with two different vocabularies for one mistake
        would be worse than one.
        """
        seen = set()
        for flow_name in self.flows:
            if flow_name in seen:
                raise ValueError(
                    f"Measurement channel {self.name}: constituent "
                    f"{flow_name!r} is declared more than once"
                )
            seen.add(flow_name)

        return self

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

    @property
    def reads_a_rate(self) -> bool:
        """True when this channel observes a delivered rate rather than a level."""
        return self.kind == MEASUREMENT_RATE

    def box_name(self) -> str:
        """Name of the box this channel imports on, per its nature (R38)."""
        if self.reads_a_rate:
            return rate_observation_box(self.name, "in")

        return f"{self.name}_level_in"

    def add_variables(self, comp):
        if self.reads_a_rate:
            # One reference and no more: a rate carries no fill -- there is no
            # volume it is a fraction of -- and no constituent. It is held in
            # ``var_level``, the channel's single reading handle, so that
            # everything downstream of it (the connection cap, ``reduce``,
            # ``is_connected``) stays one implementation.
            self.var_level = comp.addReference(self.box_name())
        else:
            self.var_level = comp.addReference(f"{self.name}_level_in")
            self.var_fill = comp.addReference(f"{self.name}_fill_in")

            for flow_name in self.flows:
                self.var_level_flow[flow_name] = comp.addReference(
                    f"{self.name}_level_{flow_name}_in"
                )
                self.var_fill_flow[flow_name] = comp.addReference(
                    f"{self.name}_fill_{flow_name}_in"
                )

        if not self.combines_several:
            # A measurement observes exactly one publisher unless the
            # declaration says how several of them combine. The cap belongs on
            # every reference of the channel, constituents included: they all
            # come from the one box, so a second publisher would otherwise be
            # capped on the total and silently summed on the constituents.
            for var in self.every_reference():
                var.setCnctMax(1)

    def published_flows(self):
        """Constituents this channel can hand on: the ones it reads.

        Named to match :meth:`Capacity.published_flows` so a republisher can
        check its source without caring which of the two it resolved to -- the
        two ARE interchangeable as sources, which is what makes a chain of
        republishers possible at all.
        """
        return list(self.flows)

    def every_reference(self):
        """Every reference this channel reads, totals and constituents."""
        if self.reads_a_rate:
            return [self.var_level]

        return (
            [self.var_level, self.var_fill]
            + list(self.var_level_flow.values())
            + list(self.var_fill_flow.values())
        )

    def add_mb(self, comp):
        if self.reads_a_rate:
            self.add_rate_mb(comp)
            return

        mb_name = self.box_name()
        comp.addMessageBox(mb_name)
        comp.addMessageBoxImport(mb_name, self.var_level, level_alias(self.name))
        comp.addMessageBoxImport(mb_name, self.var_fill, fill_alias(self.name))

        for flow_name in self.flows:
            comp.addMessageBoxImport(
                mb_name,
                self.var_level_flow[flow_name],
                level_alias(self.name, flow_name),
            )
            comp.addMessageBoxImport(
                mb_name,
                self.var_fill_flow[flow_name],
                fill_alias(self.name, flow_name),
            )

    def add_rate_mb(self, comp: typing.Any) -> None:
        """Import a delivered rate, one alias and one reference (R38).

        The publishing half is
        :meth:`muscadet.flow_continuous.FlowContinuousOut.add_rate_observation_mb`,
        and the alias is built by the same
        :func:`muscadet.flow_continuous.rate_alias` so the two cannot drift.

        The clash refused below is the observer's half of KD19: a channel named
        ``q`` imports on ``q_rate_in``, which is also the data channel of an
        input flow named ``q_rate``. This check sees only the flows declared
        BEFORE the channel -- the canonical declaration order puts measurement
        channels first, precisely because a discrete output may compare one --
        so it is a better message where it can be given, not a guarantee. In
        the other order the engine still refuses the duplicate box, naming it.
        """
        clash = rate_alias(self.name)
        flows_in = getattr(comp, "flows_in", None) or {}

        if clash in flows_in:
            raise ValueError(
                f"Measurement channel {self.name}: its rate import box "
                f"{self.box_name()!r} is derived from the channel name, and "
                f"input flow {clash!r} claims the very same box as its own "
                "data channel. Rename one of the two"
            )

        mb_name = self.box_name()
        comp.addMessageBox(mb_name)
        comp.addMessageBoxImport(mb_name, self.var_level, rate_alias(self.name))

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

    def reduce(self, var, default) -> float:
        """One reference's readings, reduced by this channel's policy.

        The single-source path stays ``sumValue``, which is what it always was:
        the identity on one connection, and the only reduction reachable
        without declaring a policy.
        """
        if not self.combines_several:
            return var.sumValue(default)

        return combine(
            self.readings(var, default),
            policy=self.combine,
            combine_fun=self.combine_fun,
            default=default,
        )

    def resolve(self, mapping, flow_name, kind):
        """The per-constituent reference for ``flow_name``, or a clear refusal."""
        var = mapping.get(flow_name)
        if var is not None:
            return var

        declared = ", ".join(self.flows) if self.flows else "none"
        raise ValueError(
            f"Measurement channel {self.name}: no {kind} is read for "
            f"constituent {flow_name!r}; this channel declares {declared}. "
            "Add it to the channel's flows= list to import it"
        )

    def get_reading(self, flow: typing.Optional[str] = None) -> float:
        """What this channel reads, whatever its nature (R38).

        The single accessor everything that consumes a measurement goes
        through: a comparison operand of a rule guard or of a discrete
        production condition (R21, R22), a transfer potential, a republishing
        instrument. It answers a level on a level channel and a rate on a rate
        one, which is exactly what those consumers want -- a number to threshold
        or to put in a law -- and it is why a rate channel needed no second
        observer class.

        :meth:`get_level` and :meth:`get_rate` are the named readings, and each
        refuses the nature it is not: naming the quantity is worth a refusal in
        a model, and worth nothing in the generic path above.
        """
        if self.reads_a_rate:
            if flow is not None:
                raise ValueError(
                    f"Measurement channel {self.name}: it reads a RATE, which "
                    f"has no constituent, so {flow!r} cannot be read on it. A "
                    "constituent is a substance a volume holds"
                )

            return self.reduce(self.var_level, self.rate_default)

        if flow is None:
            return self.reduce(self.var_level, self.level_default)

        return self.reduce(
            self.resolve(self.var_level_flow, flow, "level"), self.level_default
        )

    def get_level(self, flow=None) -> float:
        """The level read on this channel, combined over its publishers.

        ``flow=None`` is the whole volume. Naming a declared constituent gives
        that constituent's own level, which is what an intensive property is
        formed from.
        """
        self.require_kind(MEASUREMENT_LEVEL, "get_level", "get_rate")

        return self.get_reading(flow)

    def get_rate(self) -> float:
        """The delivered rate read on this channel (R38).

        No ``flow`` argument, and not by omission: a rate is one number, so
        there is no constituent of it to name.
        """
        self.require_kind(MEASUREMENT_RATE, "get_rate", "get_level")

        return self.get_reading()

    def get_fill(self, flow=None) -> float:
        """The fill read on this channel. See :meth:`get_level` for ``flow``."""
        self.require_kind(MEASUREMENT_LEVEL, "get_fill", "get_rate")

        if flow is None:
            return self.reduce(self.var_fill, self.fill_default)

        return self.reduce(
            self.resolve(self.var_fill_flow, flow, "fill"), self.fill_default
        )

    def require_kind(self, kind: str, asked: str, instead: str) -> None:
        """Refuse a reading this channel's nature does not carry (R38).

        Refused rather than answered approximately: a rate returned under
        ``get_level`` would read as an integrated state to everything that
        thresholds one, and the two behave differently the moment a loop is
        closed through them -- a level breaks an algebraic loop, a rate does
        not. Naming the right accessor in the message keeps the correction one
        edit long.
        """
        if self.kind == kind:
            return

        raise ValueError(
            f"Measurement channel {self.name}: it reads a {self.kind}, so "
            f"{asked}() has nothing to answer. Read it with {instead}(), or "
            f"declare the channel with kind={kind!r}"
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
            f"{self.get_reading() if self.var_level is not None else 'N/A'}"
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
    edge of the continuous flow graph.

    ``source`` accepted a LEVEL and nothing else, on the ground that a
    republished reading then stays an integrated state and the R30 acyclicity
    argument holds. Since R38 a continuous output publishes the rate it
    delivers, an instrument may read one, and that ground no longer covers the
    whole of what may be republished. **The reason that remains true is the
    structural one, and it is the only one this class ever needed**: the box is
    export only and carries no demand alias, so a reading moves no quantity,
    takes no share of what it watches and adds no edge to the graph the
    acyclicity check walks. That is what makes a republication safe as a matter
    of TRANSPORT, whatever it republishes.

    What it does not make safe is a LOOP. An integrated level breaks one -- it
    is the state a differential equation carries between instants -- while a
    rate is algebraic: a comparison against a rate is a function of that rate at
    this very instant. Wire such a comparison back to the component producing
    the rate and the two regimes select each other within one instant, which is
    the chatter of R30. :func:`muscadet.ordering.find_rate_comparison_loops`
    catches that shape when the rate arrives over a continuous INPUT and does
    not walk measurement links;
    :func:`muscadet.ordering.find_rate_observation_loops` is the second path
    that does walk them (R43), and it follows a republication: this channel
    carries the mark of whatever its ``source`` reads, so a rate republished
    here and thresholded back onto its own producer is refused at the first run,
    however many instruments stand in between.
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

    flows: typing.List[str] = pydantic.Field(
        default_factory=list,
        description=(
            "Constituents republished individually beside the total. Kept in "
            "step with MeasurementIn.flows on purpose: an observer cannot tell "
            "a capacity from a republisher, so an instrument standing between "
            "a multi-constituent volume and a voter has to be able to carry "
            "what the volume publishes."
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

    var_level_flow: typing.Dict[str, typing.Any] = pydantic.Field(
        default_factory=dict,
        exclude=True,
        repr=False,
        description="Per-constituent published level variables, by flow name",
    )

    var_fill_flow: typing.Dict[str, typing.Any] = pydantic.Field(
        default_factory=dict,
        exclude=True,
        repr=False,
        description="Per-constituent published fill variables, by flow name",
    )

    @pydantic.model_validator(mode="after")
    def check_flows(self):
        """Refuse a duplicate constituent at declaration time."""
        seen = set()
        for flow_name in self.flows:
            if flow_name in seen:
                raise ValueError(
                    f"Published measurement {self.name}: constituent "
                    f"{flow_name!r} is declared more than once"
                )
            seen.add(flow_name)

        return self

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

        for flow_name in self.flows:
            self.var_level_flow[flow_name] = comp.addVariable(
                f"{self.name}_level_{flow_name}",
                pyc.TVarType.t_double,
                float(self.level_default),
            )
            self.var_fill_flow[flow_name] = comp.addVariable(
                f"{self.name}_fill_{flow_name}",
                pyc.TVarType.t_double,
                float(self.fill_default),
            )

    def add_mb(self, comp):
        """Publish the reading as a read-only export, capacity-compatible."""
        mb_name = f"{self.name}_level_out"
        comp.addMessageBox(mb_name)
        comp.addMessageBoxExport(mb_name, self.var_level, level_alias(self.name))
        comp.addMessageBoxExport(mb_name, self.var_fill, fill_alias(self.name))

        for flow_name in self.flows:
            comp.addMessageBoxExport(
                mb_name,
                self.var_level_flow[flow_name],
                level_alias(self.name, flow_name),
            )
            comp.addMessageBoxExport(
                mb_name,
                self.var_fill_flow[flow_name],
                fill_alias(self.name, flow_name),
            )

    def published_flows(self):
        """Names of the constituents this channel publishes per flow."""
        return list(self.flows)

    def every_variable(self):
        """Every variable the equation writes: totals and constituents.

        What :meth:`muscadet.ObjFlow.add_measurement_out` declares explicit to
        the solver. It deliberately leaves out ``var_gain``, which is an INPUT
        a failure mode clamps and which muscadet never writes.
        """
        return (
            [self.var_level, self.var_fill]
            + list(self.var_level_flow.values())
            + list(self.var_fill_flow.values())
        )

    def get_gain(self) -> float:
        """The factor currently applied to everything this channel publishes."""
        return self.var_gain.value() if self.var_gain is not None else 1.0

    def publish(self, level, fill=None, flow=None):
        """Write one reading, gain applied. ``fill`` defaults to the level.

        The gain is the SAME for the total and for every constituent: one
        instrument publishes one set of readings, so a mode that kills it kills
        all of them. A per-constituent gain would model a probe that fails on
        the heat channel while staying honest on the water channel, which is
        two instruments, and two instruments are two components.
        """
        gain = self.get_gain()
        var_level = self.var_level if flow is None else self.var_level_flow[flow]
        var_fill = self.var_fill if flow is None else self.var_fill_flow[flow]

        var_level.setValue(float(level) * gain)
        var_fill.setValue(float(level if fill is None else fill) * gain)

    def resolve_source(self, comp):
        """The capacity, measurement channel or continuous output this reads.

        The three are interchangeable as sources and nothing downstream tells
        them apart: what an instrument publishes is a reading, and an observer
        of that reading cannot -- and must not be able to -- tell which of the
        three it came from. A continuous output joins the list under R38, which
        is what lets an instrument report a delivered rate.
        """
        capacity = comp.capacities.get(self.source)
        if capacity is not None:
            return capacity

        measurement = comp.measurements_in.get(self.source)
        if measurement is not None:
            return measurement

        flow = comp.flows_continuous_out.get(self.source)
        if flow is not None:
            return flow

        raise ValueError(
            f"Object {comp.name()}: published measurement {self.name} declares "
            f"source {self.source!r}, which is neither a capacity, nor a "
            "measurement channel, nor a continuous output flow of this "
            "component"
        )

    def read_source(self, comp, flow=None):
        """The ``(level, fill)`` pair the declared source currently holds.

        ``flow=None`` is the whole volume. The three source kinds answer the
        same question under different method names, which is the only reason
        this dispatches at all.

        A continuous output answers a **rate and no fill**: a fill is the
        fraction of a volume that is occupied, and a rate is not in a volume.
        ``None`` is returned rather than a zero, so :meth:`publish` falls back
        to the reading itself and an observer reads the same number on both
        aliases instead of a fill that would look like an empty tank.
        """
        source = self.resolve_source(comp)

        if isinstance(source, Capacity):
            # current_fill, never get_fill: the latter reads back the variable
            # the capacity equation writes, which lags a step behind the level
            # this very call is republishing.
            return source.get_quantity(flow), source.current_fill(flow)

        if isinstance(source, FlowContinuousOut):
            # ``flow`` is always None here: published_flows() is empty on a
            # continuous output, so check_source_carries refused any
            # constituent at declaration.
            return source.live_value(), None

        if source.reads_a_rate:
            # An instrument republishing what a FIRST instrument read off a
            # rate: same answer, one hop further out.
            return source.get_reading(), None

        return source.get_reading(flow), source.get_fill(flow)

    def compute(self, comp):
        """Refresh the published readings from the source, if one is declared."""
        if self.source is None:
            return

        level, fill = self.read_source(comp)
        self.publish(level, fill)

        for flow_name in self.flows:
            level, fill = self.read_source(comp, flow_name)
            self.publish(level, fill, flow=flow_name)

    def read_published(self, mapping, var_total, flow, kind) -> float:
        """One published variable's current value, total or per constituent.

        A constituent this channel does not publish is **refused**, not read as
        zero. The symmetric :meth:`MeasurementIn.get_level` refuses it, and an
        observer is not supposed to be able to tell a capacity from a
        republisher: a plausible zero here against a naming error there is
        exactly the difference that invariant forbids -- and zero is the one
        wrong answer that looks like a real reading of an empty volume.
        """
        if flow is None:
            return var_total.value() if var_total is not None else 0.0

        var = mapping.get(flow)
        if var is None:
            declared = ", ".join(self.flows) if self.flows else "none"
            raise ValueError(
                f"Published measurement {self.name}: no {kind} is published "
                f"for constituent {flow!r}; this channel declares {declared}. "
                "Add it to the channel's flows= list to publish it"
            )

        return var.value()

    def get_level(self, flow=None) -> float:
        """The level currently published, total or for one constituent."""
        return self.read_published(self.var_level_flow, self.var_level, flow, "level")

    def get_fill(self, flow=None) -> float:
        """The fill currently published, total or for one constituent."""
        return self.read_published(self.var_fill_flow, self.var_fill, flow, "fill")

    def check_source_carries(self, comp):
        """Refuse a constituent the declared source does not hold.

        Called at declaration, where the mistake was made. Left to the first
        integration step it surfaces as a bare ``KeyError`` out of a PDMP
        equation, naming neither the component, nor the channel, nor the
        volume's actual contents.

        :meth:`muscadet.System.check_measurement_constituents` does this for the
        OBSERVING side of the same mistake; this is the publishing side, which
        had no equivalent.
        """
        if self.source is None or not self.flows:
            return

        published = self.resolve_source(comp).published_flows()
        missing = [name for name in self.flows if name not in published]

        if not missing:
            return

        plural = "s" if len(missing) > 1 else ""
        available = ", ".join(published) if published else "none"
        raise ValueError(
            f"Object {comp.name()}: published measurement {self.name} "
            f"republishes constituent{plural} "
            f"{', '.join(repr(name) for name in missing)} of {self.source!r}, "
            f"which holds {available}"
        )

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
