"""A machine that moves a mixture at ONE declared volumetric rate (R51).

Every other mechanism of the module asks for a quantity **per flow**: a
consumer's ``var_demand_default``, a rule's ``cons`` coefficients, a capacity's
``serve_rate``. That is the wrong shape for a machine that displaces volume. A
ventilation extractor moves cubic metres of whatever is in the room, and what
leaves per constituent is not declarable separately: the two are tied by the
composition of the volume,

.. code-block:: text

    out_AIR = R (1 - x)          out_H2 = R x

so a model written with two independent demands cannot express the single
degree of freedom the physics has. Measured on the ventilated room of issue #4,
a room fed 50 of air and 2 of hydrogen behind an outlet asking 25 of each
released 25 of air against 50 arriving, and its air content rose from 90 to 340
over ten time units while the hydrogen share stayed at exactly zero.

**The rate belongs to the machine, the composition stays in the volume.** A room
does not decide its own ventilation; two fans on one room are two components,
where a field on the capacity would be one number for the whole volume; a pump
fails, derates and is commanded, which a component already knows how to do; and
the same object serves a pipe carrying a mixture, where a capacity field would
only ever have served volumes. But a machine displaces volume and has no way to
know ``x``, which lives in the capacity and nowhere else. So the contract is cut
in two, and this is the dual of what :meth:`muscadet.capacity.Capacity.split_draw`
already does on the production side.

The split rule, ``w_f`` being the volume one unit of ``f`` occupies:

.. code-block:: text

    out_f  =  R . m_f / sum_g ( m_g . w_g )

``sum_f out_f . w_f = R``, so the volume extracted is exactly ``R``; with every
weight at 1 it is ``split_draw``'s raw share, so **no existing model moves**; and
the denominator is :meth:`muscadet.capacity.Capacity.occupied_volume`, recomputed
from the ODE levels rather than read off an explicit variable, so it carries no
one-step lag.

What ``flow_rate`` does NOT say, and must be read here
-----------------------------------------------------
The key is spelled ``flow_rate`` for continuity with the vocabulary a modeller
already has. Two things it does not carry, and each is a way to read a model
wrong:

* **it is a VOLUME per unit of time, not a quantity.** Every other rate in the
  module is a quantity rate -- ``rate`` on a source, ``fill_rate`` and
  ``serve_rate`` on a capacity, what ``{f}_fed_out`` publishes. With every
  ``weight`` at 1 the two coincide numerically, so a model can be written, run
  and believed for a long time before the difference surfaces: it surfaces the
  day a weight differs, which is also the day it matters;
* **it is NOT per flow.** It is ONE rate for the whole group, and that is the
  point of the notion. ``flow_rate=50`` beside ``flows=["AIR", "H2"]`` does not
  mean 50 of each: it means 50 of the mixture, split at the composition. The
  two outlet rates of a ventilated volume are one degree of freedom, not two,
  and a declaration offering two is the defect this closes.

A port, by the way, declares no rate at all. What a continuous output carries
is computed by the production sweep and published on ``{f}_fed_out``; the only
declarable figure on a port is ``var_fed_default``, the fallback of an output no
rule and no capacity governs. This is the first DECLARED throughput in the
module, and it belongs to a component rather than to a port.

Where the group is resolved, and why there
------------------------------------------
A PyCATSHOO message box carries a float, so the fact that several demands are
ONE demand cannot travel on ``{f}_demand``. It is resolved **structurally at the
pre-run step** instead, which already walks the whole connection graph and has
every connection in place: :func:`resolve_mixture_groups` binds each group to the
one capacity that can compose it, and the sweeps then read the binding off that
capacity. It runs BEFORE ``register_equation_order`` so that a refused model
registers no equation and stays refusable at the next entry point, which is the
property ``System.prerun`` rests on.

Two alternatives were considered and rejected:

* **the machine reads the composition** over a ``kind="ratio"`` measurement link
  and publishes ``R(1-x)`` and ``Rx`` itself. Nothing today lets a demand depend
  on a reading -- ``var_demand_default`` is a constant field read as it stands in
  ``evaluate_demand``, a rule's ``cons`` is a ``Dict[str, float]``, and no
  controller writes a demand -- so it needs a new mechanism of the same size; the
  signal band runs AFTER production, so the demand of one step would use the
  composition of the previous one; and a fan measures nothing in real life;
* **the capability channel carries the composition.** A stocked volume publishes
  ``serve_limit``, which is ``inf`` unless a ceiling is declared, so the two
  capabilities are ``inf`` and ``inf`` and carry no ratio at all.
"""

import math
import typing

import cod3s
import Pycatshoo as pyc
import pydantic

from .flow import add_cond_threshold_automata, cond_readers, prod_cond_holds


class MixtureGroupError(ValueError):
    """A mixture group no single volume can compose for.

    Raised at the pre-run step, never at declaration time: whether a group's
    flows all come from one volume is a property of the CONNECTIONS, and those
    do not exist while ``add_flows`` runs.
    """


class MixtureIn(cod3s.ObjCOD3S):
    """A set of continuous inputs a component draws together, as one mixture.

    What is declared is a single **volumetric** rate over several flows. What
    each flow then carries is decided by the composition of the volume the
    group draws from, not by this declaration.
    """

    name: str = pydantic.Field(..., description="Group name, unique on the component")

    flows: typing.List[str] = pydantic.Field(
        ...,
        description=(
            "The continuous INPUT flows drawn together. A group of one is "
            "legitimate and means out = R / w, a volumetric pump on a "
            "single-species line."
        ),
    )

    flow_rate: float = pydantic.Field(
        ...,
        description=(
            "ONE rate for the whole group, and a VOLUME per unit of time rather "
            "than a quantity. Not a rate per flow: flow_rate=50 over two flows "
            "moves 50 of the mixture, not 50 of each. What each constituent "
            "contributes to it is the composition's business, never this "
            "declaration's."
        ),
    )

    run_cond: list = pydantic.Field(
        default_factory=list,
        description=(
            "Condition commanding the machine on and off (R52), in the operand "
            "vocabulary a discharge command uses (R49): the same shapes, the "
            "same conjunctive-normal form, the same comparison grammar, "
            "resolved by the same implementation. EMPTY -- the default -- "
            "holds, so a group declaring none runs whenever its rate allows, "
            "which is every model written before the field existed. Stored "
            "RESOLVED once the component has read it, the negation and the "
            "comparison lifted into the two matrices below."
        ),
    )

    run_cond_negate: list = pydantic.Field(
        default_factory=list,
        description=(
            "Per-operand negation matrix of 'run_cond', aligned index-for-index "
            "with it. Empty unless an operand is negated."
        ),
    )

    run_cond_compare: list = pydantic.Field(
        default_factory=list,
        description=(
            "Per-operand comparison matrix of 'run_cond', aligned index-for-index "
            "with it. Empty unless an operand compares."
        ),
    )

    run_cond_inner_mode: str = pydantic.Field(
        "or",
        description=(
            "How 'run_cond' reads its two levels, exactly as "
            "'serve_cond_inner_mode' does: 'or' is a conjunction of "
            "disjunctions, 'and' the other way round."
        ),
    )

    #: Backend handle of ``{name}_flow_rate``, created by :meth:`add_variables`.
    #: Private because it holds a PyCATSHOO object and no spec may carry it.
    _var_rate: typing.Any = pydantic.PrivateAttr(default=None)

    #: Memoised readers of :attr:`run_cond`, built on first use.
    _run_cond_readers: typing.Any = pydantic.PrivateAttr(default=None)

    #: Engine key of the component the pre-run step bound this group to, written
    #: by :func:`resolve_mixture_groups` and read by the loop detection, which
    #: has to know whose delivery the command decides (R52).
    _producer: typing.Optional[str] = pydantic.PrivateAttr(default=None)

    @pydantic.field_validator("flows", mode="before")
    @classmethod
    def check_flows(cls, value):
        """A group names at least one flow, each of them once."""
        if isinstance(value, str):
            value = [value]

        names = [str(entry) for entry in (value or [])]

        if not names:
            raise ValueError("A mixture group names at least one flow")

        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"Mixture group flows must be distinct, repeated: "
                f"{', '.join(duplicates)}"
            )

        return names

    @pydantic.field_validator("flow_rate")
    @classmethod
    def check_rate(cls, value):
        """The rate is a finite, non-negative volume per unit of time.

        Zero is legitimate and means a stopped machine. ``math.inf`` is refused:
        an unbounded volumetric draw composes as ``inf * share``, which is
        ``NaN`` the moment a constituent stands at zero, and a ``NaN`` in a level
        propagates into everything downstream of the volume rather than stopping
        there. A model wanting "take whatever there is" declares a large finite
        rate, as R-9 already asks of a source.
        """
        value = float(value)

        if math.isnan(value) or value < 0.0:
            raise ValueError(
                f"flow_rate must be zero or positive, got {value}. A "
                f"direction is the connection's, never the rate's."
            )

        if math.isinf(value):
            raise ValueError(
                "flow_rate must be finite: an unbounded volumetric draw "
                "composes as inf * share, which is NaN on a constituent standing "
                "at zero. Declare a large finite rate instead."
            )

        return value

    @pydantic.field_validator("run_cond_inner_mode")
    @classmethod
    def check_run_cond_inner_mode(cls, value):
        """Refuse a mode that is not one of the two, as a capacity does.

        ``prod_cond_holds`` reads anything that is not the literal ``"or"`` as
        ``"and"``, so ``"OR"`` would silently invert the whole command.
        """
        if value not in ("and", "or"):
            raise ValueError(
                f"run_cond_inner_mode must be 'and' or 'or', got {value!r}"
            )
        return value

    def rate_var_name(self) -> str:
        """Basename of the public variable carrying this group's rate (R52)."""
        return f"{self.name}_flow_rate"

    def add_variables(self, comp):
        """Publish the rate as ``{group}_flow_rate`` on the consuming component.

        Created at the declared ``flow_rate``, public and writable, and muscadet
        never writes it: it is the endpoint a failure mode clamps by name to stop
        or throttle the machine, exactly as ``{capacity}_serve_rate_{flow}`` is for
        a discharge ceiling (R48). For the same reason it is neither declared
        explicit to the PDMP nor owned by the solver: its only writer acts from a
        transition's reset map, never during the differential resolution.
        """
        self._var_rate = comp.addVariable(
            self.rate_var_name(), pyc.TVarType.t_double, float(self.flow_rate)
        )

    def runs(self) -> bool:
        """True when the command lets the machine run (R52).

        An EMPTY condition holds, so an uncommanded group pays a single truth
        test. Read live rather than mirrored, for the reason a discharge command
        is (R49): a comparison against a continuous quantity would otherwise lag
        one step behind the value it watches.
        """
        if not self.run_cond:
            return True

        if self._run_cond_readers is None:
            self._run_cond_readers = cond_readers(
                self.run_cond, self.run_cond_negate, self.run_cond_compare
            )

        return prod_cond_holds(self._run_cond_readers, self.run_cond_inner_mode)

    def add_run_cond_automata(self, comp):
        """Watch every continuous threshold the command carries (R52).

        The watched two-state automaton a discharge command's comparison gets,
        from the same implementation, so a level crossing inside an integration
        step starts or stops the machine at the crossing rather than one step
        late. The ``_run`` tag keeps the states apart from those of a flow or a
        capacity of the same name, the engine's states being one namespace.
        """
        return add_cond_threshold_automata(
            comp, self.run_cond, self.run_cond_compare, f"{self.name}_run"
        )

    def rate(self) -> float:
        """The volume this group moves per unit of time, read live.

        Zero while the command does not hold, the published rate otherwise:
        composed by BRANCHING, never by product, for the reason the discharge
        command gives (R49). Read from the public variable, so a failure mode
        that clamped it is honoured within the instant; falls back to the
        declaration before :meth:`add_variables` has run, which is what lets a
        group be inspected outside a built system.

        Clamped at zero: a mode writing a negative value states a direction, and
        the direction of a draw is the connection's, never the rate's -- the
        rule :meth:`check_rate` applies to the declaration.
        """
        if not self.runs():
            return 0.0

        if self._var_rate is None:
            return float(self.flow_rate)

        return max(float(self._var_rate.value()), 0.0)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__} {self.name} "
            f"[{', '.join(self.flows)}] @ {self.flow_rate:g}"
        )

    def __str__(self) -> str:
        return repr(self)


class MixtureDraw(cod3s.ObjCOD3S):
    """What a capacity is told at the pre-run step: who draws it, and how fast.

    Held on :attr:`muscadet.capacity.Capacity.mixture`, written by
    :func:`resolve_mixture_groups` and by nothing else.
    """

    consumer: str = pydantic.Field(
        ...,
        description=(
            "ENGINE name of the consuming component, which is the key an "
            "allocation is written under."
        ),
    )

    group: str = pydantic.Field(..., description="Name of the group on that consumer")

    mixture_group: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description=(
            "The consumer's MixtureIn itself. Held by reference rather than "
            "copying its rate, so that a failure mode clamping "
            "{group}_flow_rate reaches the volume within the instant (R52)."
        ),
    )

    def rate(self) -> float:
        """The volume the machine moves per unit of time, read live."""
        return self.mixture_group.rate()

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__} {self.consumer}.{self.group} "
            f"@ {self.rate():g}"
        )

    def __str__(self) -> str:
        return repr(self)


def component_mixtures(comp):
    """The mixture groups declared on ``comp``, or an empty mapping."""
    return getattr(comp, "mixtures", None) or {}


def clear_mixture_bindings(system):
    """Drop every binding a previous resolution wrote.

    The resolution is idempotent rather than incremental: a second pre-run of an
    unchanged model must land on exactly the same bindings, and a model refused
    half way must not leave a volume believing it serves a group.
    """
    for comp in (getattr(system, "comp", None) or {}).values():
        for capacity in (getattr(comp, "capacities", None) or {}).values():
            capacity.mixture_draws = []
        for group in component_mixtures(comp).values():
            group._producer = None


def resolve_mixture_groups(system):
    """Bind each declared mixture group to the volume that can compose for it.

    Called from the system's pre-run step, BEFORE any equation is registered, so
    that a refusal leaves the manager untouched and the step stays retryable.

    Returns
    -------
    list
        One ``(consumer key, group name, producer key, capacity name)`` tuple per
        binding, in declaration order. Empty when no group is declared, which is
        every model written before this notion existed.

    Raises
    ------
    MixtureGroupError
        When a group's flows do not all arrive from ONE capacity of ONE
        producer, when the group leaves out a constituent of that volume, when
        the producer serves those flows to anybody but mixture groups, or when
        a volume drawn by several groups allocates other than proportionally.
        Each message names the group, the component and the flows in cause.
    """
    # Deferred: muscadet.ordering imports the evaluation and capability units,
    # and this module is reached from muscadet.obj, which they must stay free of.
    from .ordering import build_continuous_flow_graph, engine_name_index

    components = getattr(system, "comp", None) or {}

    declared = [
        (key, comp, group)
        for key, comp in components.items()
        for group in component_mixtures(comp).values()
    ]

    clear_mixture_bindings(system)

    if not declared:
        return []

    graph = build_continuous_flow_graph(system)

    # (target, flow) -> the producers delivering it, and (source, flow) -> the
    # consumers drawing it. Both are needed: the first resolves the volume, the
    # second is what refuses a volume serving a group and somebody else at once.
    suppliers = {}
    customers = {}
    for cnct in graph.connections:
        suppliers.setdefault((cnct.target, cnct.flow), []).append(cnct.source)
        customers.setdefault((cnct.source, cnct.flow), []).append(cnct.target)

    # (producer, flow) -> the consumers drawing that flow as a mixture. What
    # the exclusivity rule exempts: several machines on one room are the point
    # of R52, a per-flow consumer beside them is still not arbitrated.
    group_consumers = {}
    for key, comp, group in declared:
        for flow_name in group.flows:
            for source in suppliers.get((key, flow_name), []):
                group_consumers.setdefault((source, flow_name), set()).add(key)

    resolved = []
    drawn = []

    for key, comp, group in declared:
        where = f"mixture group {group.name} of {key}"

        producer = _single_producer(where, group, key, suppliers)
        producer_comp = components[producer]

        capacity = _shared_capacity(where, group, producer, producer_comp)

        _check_covers_volume(where, group, producer, capacity)

        _check_exclusive(where, group, producer, key, customers, group_consumers)

        capacity.mixture_draws.append(
            MixtureDraw(
                consumer=comp.name(),
                group=group.name,
                mixture_group=group,
            )
        )

        group._producer = producer

        if (producer, capacity) not in drawn:
            drawn.append((producer, capacity))
        resolved.append((key, group.name, producer, capacity.name))

    for producer, capacity in drawn:
        _check_proportional(producer, components[producer], capacity)

    return resolved


def _single_producer(where, group, consumer_key, suppliers):
    """The one component delivering every flow of ``group``.

    A group demand is a statement about a composition, and a composition exists
    only inside one volume. Flows arriving from two places have no common
    composition to be split at, so there is nothing to compose and the
    declaration is refused rather than approximated.
    """
    producers = {}

    for flow_name in group.flows:
        sources = suppliers.get((consumer_key, flow_name), [])

        if not sources:
            raise MixtureGroupError(
                f"{where}: nothing is connected to {flow_name}. Every flow of a "
                f"mixture group is drawn from a volume, so each of them needs a "
                f"producer."
            )

        for source in sources:
            producers.setdefault(source, []).append(flow_name)

    if len(producers) > 1:
        detail = "; ".join(
            f"{source}: {', '.join(flows)}" for source, flows in producers.items()
        )
        raise MixtureGroupError(
            f"{where}: its flows arrive from several producers ({detail}). A "
            f"mixture is composed inside ONE volume; flows coming from "
            f"different places share no composition to be split at."
        )

    return next(iter(producers))


def _shared_capacity(where, group, producer, producer_comp):
    """The one output capacity of ``producer`` holding every flow of ``group``."""
    capacities = []

    for flow_name in group.flows:
        capacity = producer_comp.get_capacity_of_flow(flow_name, "out")

        if capacity is None:
            raise MixtureGroupError(
                f"{where}: {producer} delivers {flow_name} without a capacity "
                f"behind that output. A volumetric rate is split at a "
                f"composition, and only a volume has one. Declare the mixture's "
                f"flows in one capacity of side='out'."
            )

        capacities.append(capacity)

    names = {capacity.name for capacity in capacities}

    if len(names) > 1:
        raise MixtureGroupError(
            f"{where}: {producer} holds its flows in several volumes "
            f"({', '.join(sorted(names))}). One mixture is one composition, so "
            f"the group's flows must share a single capacity."
        )

    return capacities[0]


def _check_covers_volume(where, group, producer, capacity):
    """Refuse a group that leaves out a constituent of the volume it draws (R52).

    A volume drawn as a mixture serves mixture groups alone, so a constituent
    no group names would still be composed into the draw of the volume and
    sent down an output no machine is wired to: drawn out of the room, received
    by nobody, recorded by no balance. A machine moves a volume of whatever the
    room holds, so it names everything the room holds.
    """
    missing = [name for name in capacity.flow_names if name not in group.flows]

    if missing:
        raise MixtureGroupError(
            f"{where}: capacity {capacity.name} of {producer} also holds "
            f"{', '.join(missing)}, which the group does not draw. A machine "
            f"moves a volume of whatever the room holds, so a group names "
            f"every constituent of the volume it draws; what it left out would "
            f"leave the volume towards nobody."
        )


def _check_proportional(producer, producer_comp, capacity):
    """Refuse a volume drawn by several groups that allocates otherwise (R52).

    Several machines are served pro rata of their composed shares when the
    volume cannot serve their sum, and that pro-rata IS the proportional
    allocation. A declared ``shares`` or ``priority`` policy, or a Python
    ``allocation_fun``, would serve them by another rule while the model reads
    as ventilated at the composition. A volume drawn by ONE group has nobody to
    arbitrate against and keeps whatever it declared.
    """
    if len(capacity.mixture_draws) < 2:
        return

    from .flow_continuous import ALLOCATION_PROPORTIONAL

    for flow_name in capacity.flow_names:
        flow = (producer_comp.flows_out or {}).get(flow_name)

        if flow is None:
            continue

        custom = callable(getattr(flow, "allocation_fun", None))
        policy = getattr(flow, "allocation", ALLOCATION_PROPORTIONAL)

        if custom or policy != ALLOCATION_PROPORTIONAL:
            consumers = ", ".join(
                f"{draw.consumer}.{draw.group}" for draw in capacity.mixture_draws
            )
            declared = "allocation_fun" if custom else f"allocation={policy!r}"
            raise MixtureGroupError(
                f"capacity {capacity.name} of {producer} is drawn by several "
                f"mixture groups ({consumers}), and its output {flow_name} "
                f"declares {declared}. Machines on one volume are served pro "
                f"rata of their composed shares, which is the proportional "
                f"allocation: declare none on that output."
            )


def _check_exclusive(where, group, producer, consumer_key, customers, group_consumers):
    """Refuse a volume serving the group and an ordinary consumer at once.

    Recorded rather than designed: what a per-flow request and a composed share
    should do to each other is a genuine arbitration, and a silent wrong answer
    is exactly the failure this notion exists to remove. Another MIXTURE group
    on the same flow is not such a consumer (R52): each machine takes its own
    rate at the composition, and the draws add up.
    """
    for flow_name in group.flows:
        mixtures = group_consumers.get((producer, flow_name), set())
        others = sorted(
            {
                target
                for target in customers.get((producer, flow_name), [])
                if target != consumer_key and target not in mixtures
            }
        )

        if others:
            raise MixtureGroupError(
                f"{where}: {producer} also serves {flow_name} to "
                f"{', '.join(others)}. A volume drawn as a mixture serves that "
                f"mixture alone: arbitrating a composed share against a "
                f"per-flow request is not defined here."
            )
