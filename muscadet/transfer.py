"""Transfer pairs: a quantity a component computes and moves between two balances.

MUSCADET transports extensive quantities **pulled by demand**: a consumer asks,
a producer delivers the lesser of what it can make and what was asked. That is
right for matter a component decides to take. It is the wrong vehicle for a
quantity that moves because a **gradient** makes it move -- heat across an
exchanger wall, hydrogen through a membrane, charge through a resistance.

A **transfer pair** is that second vehicle. A component declares two of its
continuous flows and an equation returning a signed quantity; the library moves
that quantity from one flow's balance to the other's, draws what it needs from
upstream, and holds the two sides equal. Nothing else in the library can say
"move exactly this much, which I computed": production is always
``declared x factor``, so a component could express a proportion of what
arrived or a proportion of a constant, never an absolute computed quantity
drawn from a named supplier.

Two shapes, one notion
----------------------
A pair names two flows, not two ports (KD3), and the degenerate case is the one
a reader is most likely to miss::

    add_transfer(name="exchange", flows=("heat_water", "heat_air"), ...)
        the two-stream exchanger: both streams keep transiting and the pair
        moves a quantity BETWEEN their balances, as a signed delta on top

    add_transfer(name="wall", flows=("heat", "heat"), ...)
        the metered conduit: what crosses the component IS the computed
        quantity, so the pair REPLACES that flow's identity transfer

The distinction is load-bearing and the two cannot share one rule. Remove a
two-flow pair's streams from the identity transfer and the exchanger's streams
stop crossing the component at all; leave a conduit's flow in it and the stream
crosses twice, once by transfer and once by the pair.

The sign is the direction
-------------------------
The equation returns a **signed** quantity and the library routes the sign
(KD1). A model never writes a direction clamp, and a tank that warms past its
environment starts losing heat with no declaration changing.

This is where a transfer parts company with :mod:`muscadet.profile`, whose
pattern it otherwise follows exactly. A profile *scales* production, so a
negative factor would mean a negative quantity and is refused. A negative
transfer means the other direction, and refusing it would delete half the
behaviour.

Why the equation is a declared object, and not a callable
---------------------------------------------------------
The library has both spellings already. ``allocation_fun`` and ``combine_fun``
are bare callables carrying no attestation. :class:`muscadet.Profile` is an
object whose ``continuous`` flag has **no default** and whose bare-callable form
is refused outright. A transfer equation belongs on the second, for the reason
``muscadet/profile.py`` spells out at length:

The equation is read from inside the sweeps, which the PDMP solver evaluates at
the integration points **it** chooses. A smooth law is integrated to the
solver's own accuracy -- the error control sees the curve bend and shortens the
step. A **discontinuous** law is not: a thermostat that switches at a threshold
is crossed inside a step, and the post-jump quantity is integrated over an
interval that partly precedes it, with no error the solver can detect. Making
that correct needs a **watched transition** at the breakpoint, which muscadet
does not derive from a Python function.

Continuity cannot be inferred from a callable, so it is **declared**. That is
the whole reason a bare callable is refused: wrapping it is the point at which
the modeller states what muscadet cannot check.
"""

import math
import typing

import Pycatshoo as pyc
import pydantic
from colored import attr, fg

import cod3s

#: What a modeller is told when the equation is not a declared-continuous one.
#: It names the mechanism that would be needed rather than only the rule broken.
_CONTINUITY_MESSAGE = (
    "a transfer equation must be a muscadet.Transfer declared CONTINUOUS in "
    "the quantities it reads, built as muscadet.Transfer(fun, continuous=True) "
    "or as one of the shipped shapes (muscadet.ConductiveTransfer). The flag "
    "is an attestation: muscadet cannot inspect a callable for continuity, so "
    "it asks. A DISCONTINUOUS law -- a thermostat switching at a threshold, a "
    "schedule, a lookup table -- needs a watched transition at each of its "
    "breakpoints, so the solver stops AT the jump instead of crossing it "
    "inside an integration step and overshooting by that step; muscadet does "
    "not derive those transitions, so such a law is refused rather than "
    "silently integrated wrong"
)


class Transfer:
    """A continuous, signed quantity a component moves between two balances.

    Parameters
    ----------
    fun : callable
        ``fun(comp) -> float``, the **signed** quantity to move per unit time.
        Positive moves from the pair's first flow to its second; negative moves
        the other way at the absolute value. It receives the component, and
        reads whatever raw quantities it needs from it (KD2): a capacity's
        level through ``comp.capacities[...].get_quantity(flow)``, a reading
        through ``comp.measurements_in[...].get_level(flow)``, an input's
        delivery. muscadet hands it no intensive property; the equation forms
        whatever ratio it needs.
    continuous : bool
        The modeller's attestation that ``fun`` is continuous in the quantities
        it reads. There is no default and nothing but an explicit ``True`` is
        accepted -- see the module docstring for why this cannot be inferred.
    name : str, optional
        A label for messages. Defaults to the class name.

    Raises
    ------
    ValueError
        If ``fun`` is not callable, or if ``continuous`` is not ``True``.
    """

    def __init__(self, fun=None, continuous=None, name=None):
        if not callable(fun):
            raise ValueError(
                f"Transfer: {fun!r} is not callable; a transfer is built over "
                "a function of the component, fun(comp) -> float"
            )

        if continuous is not True:
            raise ValueError(f"Transfer: {_CONTINUITY_MESSAGE}")

        self.fun = fun
        self.continuous = True
        self.name = name or type(self).__name__

    def evaluate(self, comp):
        """The raw signed quantity, before any check. The override point."""
        return self.fun(comp)

    def quantity(self, comp, pair_name=None) -> float:
        """The signed quantity to move, right now.

        Read on every evaluation of the sweeps, so it must stay a pure function
        of the component's current values.

        A **negative** value is returned unchanged: the sign is the direction
        (KD1), which is where this parts company with
        :meth:`muscadet.Profile.factor`. An infinite value is allowed and means
        "move whatever the supply allows" -- the caller caps it before anything
        is written, so it never reaches a balance.

        Raises
        ------
        ValueError
            If the equation returns something that is not a real number, or
            NaN. Both are model errors rather than numerical accidents, and a
            NaN reaching a balance propagates silently through every level
            downstream of it.
        """
        value = self.evaluate(comp)

        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"Transfer {self.name}{self._on(pair_name)}: returned "
                f"{value!r}, which is not a real number"
            ) from None

        if math.isnan(value):
            raise ValueError(f"Transfer {self.name}{self._on(pair_name)}: returned NaN")

        return value

    @staticmethod
    def _on(pair_name):
        return "" if pair_name is None else f" on transfer pair {pair_name}"

    def __repr__(self):
        return f"{type(self).__name__}(continuous=True)"


# ----------------------------------------------------------------------
# Operands: what a shipped shape reads a potential from
# ----------------------------------------------------------------------


def resolve_operand(comp, spec, where=""):
    """The current value of one declared potential operand.

    Two forms, and deliberately only two:

    - ``{"const": x}`` -- a fixed potential, an environment held at a value;
    - ``{"measurement": name}`` -- a reading arriving on a measurement channel,
      optionally narrowed to one constituent with ``"flow"``.

    Forming a ratio from a volume's own contents is **not** an operand form. A
    measurement channel already publishes each constituent it names, so a
    component dividing two of them publishes the intensive property itself --
    which is what a probe reporting kelvin from joules and kilograms is. The
    shipped shapes read a potential; they do not compute one.

    Raises
    ------
    ValueError
        For an unknown operand form, or a measurement channel the component
        does not declare.
    """
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        return float(spec)

    if isinstance(spec, dict):
        if "const" in spec:
            return float(spec["const"])

        if "measurement" in spec:
            name = spec["measurement"]
            channel = comp.measurements_in.get(name)
            if channel is None:
                raise ValueError(
                    f"Transfer operand{where}: {name!r} is not a measurement "
                    f"channel of {comp.name()}; declare it with "
                    "add_measurement_in before reading it"
                )
            return float(channel.get_level(spec.get("flow")))

    raise ValueError(
        f"Transfer operand{where}: {spec!r} is not a potential. Use a number, "
        '{"const": value} for a fixed potential, or {"measurement": name} '
        '(optionally with "flow") to read one over a measurement channel'
    )


class ConductiveTransfer(Transfer):
    """``G x (potential_a - potential_b)``, the generalised transport law.

    A rate of an extensive quantity driven by the difference of its conjugate
    intensive potential. It is not thermal: it covers heat through an
    exchanger, moles through a membrane, charge through a resistance and fluid
    through a pipe, which is why one shape serves every case the knowledge base
    currently needs.

    Continuous by construction -- an affine function of two potentials is
    continuous wherever they are -- so it carries no attestation of its own.
    The sign follows the difference, so the transfer reverses on its own when
    the second potential overtakes the first.

    Parameters
    ----------
    conductance : float
        The proportionality constant ``G``. Must not be negative: a negative
        conductance drives the quantity UP the gradient, which is not transport
        and is refused rather than silently modelled.
    potential_a, potential_b : dict or float
        The two potentials, in the forms :func:`resolve_operand` accepts.
    name : str, optional
        A label for messages.
    """

    def __init__(self, conductance=None, potential_a=None, potential_b=None, name=None):
        if conductance is None:
            raise ValueError("ConductiveTransfer: conductance is required")

        conductance = float(conductance)
        if conductance < 0.0:
            raise ValueError(
                f"ConductiveTransfer: conductance may not be negative, got "
                f"{conductance:g}. A negative conductance would drive the "
                "quantity UP its own gradient, which is not transport. To "
                "reverse the direction, swap the two potentials"
            )

        if potential_a is None or potential_b is None:
            raise ValueError(
                "ConductiveTransfer: both potential_a and potential_b are "
                "required; the quantity moved is their difference"
            )

        self.conductance = conductance
        self.potential_a = potential_a
        self.potential_b = potential_b

        super().__init__(fun=self._conduction, continuous=True, name=name)

    def _conduction(self, comp):
        where = f" of {self.name}"
        a = resolve_operand(comp, self.potential_a, where)
        b = resolve_operand(comp, self.potential_b, where)

        return self.conductance * (a - b)

    def __repr__(self):
        return (
            f"ConductiveTransfer(conductance={self.conductance:g}, "
            f"potential_a={self.potential_a!r}, potential_b={self.potential_b!r})"
        )


#: The transfer shapes a declaration may name by string, for the
#: ``{"cls": ...}`` form the rest of the declaration API already uses.
TRANSFER_CLASSES: typing.Dict[str, type] = {
    "ConductiveTransfer": ConductiveTransfer,
}


class TransferPair(cod3s.ObjCOD3S):
    """One declared pair: two flow names and the equation moving between them.

    The equation (:class:`Transfer`) says HOW MUCH; the pair says BETWEEN WHAT.
    They are separate objects because one equation shape serves any pair of
    flows, and because the pair is what the sweeps iterate over.

    :attr:`is_conduit` is the distinction the whole notion turns on, and the
    two shapes take different paths through the production sweep -- see the
    module docstring.
    """

    name: str = pydantic.Field(..., description="Pair name, unique on the component")

    flows: typing.List[str] = pydantic.Field(
        ...,
        description=(
            "The two continuous flow names, source first. A positive quantity "
            "moves from the first to the second. Naming the same flow twice is "
            "the metered conduit."
        ),
    )

    equation: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description="The declared Transfer returning the signed quantity",
    )

    last_requested: float = pydantic.Field(
        0.0,
        description=(
            "The SIGNED quantity the equation returned at the last evaluation. "
            "Kept beside last_moved so the shortfall of a saturated transfer is "
            "readable rather than inferred from the balances (KD5) -- and so "
            "that a conduit computing a reversal shows up as a negative ask "
            "against a zero crossing rather than as a plausible number."
        ),
    )

    last_moved: float = pydantic.Field(
        0.0,
        description=(
            "The SIGNED quantity actually moved: positive from source to "
            "destination, negative the other way. Never negative on a conduit, "
            "whose direction is its connection's."
        ),
    )

    var_requested: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Published {pair}_requested"
    )

    var_moved: typing.Any = pydantic.Field(
        None, exclude=True, repr=False, description="Published {pair}_moved"
    )

    def add_variables(self, comp):
        """Declare the two read-only publications of this pair on ``comp``.

        A saturated transfer is a legitimate physical state; an INVISIBLE one is
        the defect class this release has spent its life closing (KD5). The gap
        between what the equation asked for and what the supply allowed is
        therefore published rather than left to be inferred from the balances,
        where a rule set's draw and a capacity's fill are mixed into the same
        numbers.
        """
        self.var_requested = comp.addVariable(
            f"{self.name}_requested", pyc.TVarType.t_double, 0.0
        )
        self.var_moved = comp.addVariable(
            f"{self.name}_moved", pyc.TVarType.t_double, 0.0
        )

    def every_variable(self):
        """Every variable an equation writes, for the pre-run PDMP declaration."""
        return [var for var in (self.var_requested, self.var_moved) if var is not None]

    def publish(self):
        """Write the last evaluation's two magnitudes onto the model."""
        if self.var_requested is not None:
            self.var_requested.setValue(float(self.last_requested))
            self.var_moved.setValue(float(self.last_moved))

    @property
    def shortfall(self) -> float:
        """What the last evaluation asked for and could not move.

        Compared on MAGNITUDES, because both readings are signed: a conduit
        that computed a reversal asked for something and moved nothing, and
        that gap is exactly as real as a saturated one.
        """
        return max(abs(self.last_requested) - abs(self.last_moved), 0.0)

    @pydantic.field_validator("flows")
    @classmethod
    def check_two_flows(cls, value):
        if len(value) != 2:
            raise ValueError(
                f"A transfer pair names exactly two flows, got {len(value)}: "
                f"{value!r}. Name the same flow twice for a metered conduit"
            )
        return value

    @property
    def is_conduit(self) -> bool:
        """True when the pair meters one flow's transit rather than moving between two."""
        return self.flows[0] == self.flows[1]

    @property
    def source(self) -> str:
        """The flow a positive quantity leaves."""
        return self.flows[0]

    @property
    def destination(self) -> str:
        """The flow a positive quantity enters."""
        return self.flows[1]

    def quantity(self, comp) -> float:
        """The signed quantity this pair moves right now."""
        return self.equation.quantity(comp, self.name)

    def directed(self, comp) -> typing.Tuple[str, str, float]:
        """``(from_flow, to_flow, magnitude)`` with the sign already routed.

        The whole of KD1 in one place: a model never writes a direction clamp,
        because the library reads the sign here and hands back an unsigned
        magnitude with the two ends already in the right order.
        """
        value = self.quantity(comp)

        if value < 0.0:
            return self.destination, self.source, -value

        return self.source, self.destination, value

    def __repr__(self) -> str:
        shape = "conduit" if self.is_conduit else "pair"
        return (
            f"{fg('cyan')}TransferPair{attr('reset')} "
            f"{fg('blue')}{self.name}{attr('reset')} "
            f"[{shape}] {self.source} -> {self.destination}"
        )

    def __str__(self) -> str:
        return self.__repr__()


def build_transfer(spec, pair_name=None):
    """Normalise a declared equation into a :class:`Transfer`, or refuse it.

    Accepts a :class:`Transfer` -- returned unchanged -- and the
    ``{"cls": ...}`` mapping form. A **bare callable is deliberately refused**:
    it carries no continuity attestation, and that attestation is the one thing
    muscadet cannot work out for itself.

    ``Transfer`` itself is absent from :data:`TRANSFER_CLASSES` on purpose. Its
    whole content is a Python function, which no mapping can carry, so naming
    it there would offer a serialised form that cannot be serialised.

    Parameters
    ----------
    spec : Transfer or dict
        The declaration.
    pair_name : str, optional
        Named in the error messages.

    Returns
    -------
    Transfer

    Raises
    ------
    ValueError
        For a bare callable, an unknown ``cls``, a mapping without ``cls``, or
        anything else that is not a transfer equation.
    """
    if isinstance(spec, Transfer):
        return spec

    where = Transfer._on(pair_name)

    if isinstance(spec, dict):
        params = dict(spec)
        clsname = params.pop("cls", None)

        if clsname is None:
            raise ValueError(
                f"Transfer declaration{where}: a mapping must carry a 'cls' "
                f"key naming the equation shape, one of "
                f"{', '.join(sorted(TRANSFER_CLASSES))}"
            )

        cls = TRANSFER_CLASSES.get(clsname)
        if cls is None:
            raise ValueError(
                f"Transfer declaration{where}: unknown transfer class "
                f"{clsname!r}, expected one of "
                f"{', '.join(sorted(TRANSFER_CLASSES))}"
            )

        return cls(**params)

    if callable(spec):
        raise ValueError(f"Transfer declaration{where}: {_CONTINUITY_MESSAGE}")

    raise ValueError(
        f"Transfer declaration{where}: {spec!r} is not a transfer equation. "
        f"{_CONTINUITY_MESSAGE}"
    )
