"""Time profiles: a declared function of simulation time scaling an output.

A continuous output's production is otherwise either a constant -- the rate the
flow was declared with -- or whatever its transformation rule computes from what
its inputs deliver. A **profile** is the third term: a declared function of the
simulation clock that scales what the output produces, so a solar curve, a daily
demand cycle or a seasonal derate is expressed once, on the flow, instead of
being wired as a component that recomputes an equation of its own.

The composition rule (and why a profile is NOT a rate)
------------------------------------------------------
Production is the PRODUCT of three independent terms::

    produced = what the rule (or the declared rate) produces
               x  profile(t)
               x  min(out_rate, per-mode deratings)

The profile is a **separate channel** from the derating rate on purpose, and
folding it into ``{flow}_out_rate`` would be silently wrong. Deratings compose
by **minimum**: two failure modes leaving 0.5 and 0.8 of an output leave 0.5,
which is what makes them order-independent and safe on repair (R20, KD11). A
profile is not a competing degradation, it is the size of the thing being
degraded: a panel at 30 % irradiance that is ALSO 50 % derated produces 15 %,
not 30 %. Written into the shared rate variable the two would fold by minimum,
production would read 0.5 instead of 0.15, and nothing would signal it.

So: profile and derating compose by product, deratings still compose by minimum
among themselves, and neither mechanism can be expressed through the other.

Why only CONTINUOUS profiles, and how the line is drawn
-------------------------------------------------------
A profile is read from inside the production equation, which the PDMP solver
evaluates at the integration points it chooses. A **smooth** profile is
therefore integrated to the solver's own accuracy: the error control sees the
curve bend and shortens the step.

A **discontinuous** profile -- a step at t=8, a schedule, a lookup table -- is
not. The solver has no reason to place a step boundary at the jump, so it
crosses the breakpoint inside a step and integrates the post-jump value over an
interval that partly precedes it: the model overshoots by up to one step, with
no error the solver can detect. Making that correct needs a **watched
transition** at every breakpoint, so the solver stops AT the jump and restarts
after it -- the same mechanism a rule guard's threshold compiles into (R21).
muscadet does not derive those transitions from a Python callable, and will not
integrate a jump it has not been told about.

Continuity cannot be inferred from a callable, so it is **declared**:
:class:`Profile` takes a ``continuous`` flag with no default, and anything other
than an explicit ``True`` is refused at declaration time with the message above.
That is why a bare callable is not accepted as a profile: wrapping it is the
point at which the modeller states what muscadet cannot check.

Non-negativity
--------------
A profile factor is refused below zero, at declaration for the shipped shapes
and at evaluation for a callable. A negative production in a conserved-quantity
flow model means either nothing at all or a reverse flow, and muscadet models
neither: :func:`muscadet.evaluation.deliver_output` clamps a delivered quantity
at zero, so a negative factor would silently vanish on a plain output -- and
would silently DRAIN an output capacity, which integrates what it is handed.
"""

import math
import typing

#: The neutral profile factor: an output nothing profiles produces what its
#: rule (or its declared rate) says, unscaled.
NOMINAL_FACTOR = 1.0

#: How the published profile variable of a continuous output is named. Created
#: only on an output that DECLARES a profile, and written by the production
#: sweep: it is a read-only publication of the factor currently applied, the
#: counterpart of a capacity's level channel. It is NOT an input -- writing it
#: has no effect, since the next evaluation overwrites it from the profile.
PROFILE_VAR_FMT = "{flow}_out_profile"

#: What a modeller is told when a profile is not a declared-continuous one. It
#: names the mechanism that would be needed rather than only the rule broken.
_CONTINUITY_MESSAGE = (
    "a profile must be a muscadet.Profile declared CONTINUOUS in t, built as "
    "muscadet.Profile(fun, continuous=True) or as one of the shipped shapes "
    "(muscadet.SinusoidalProfile). The flag is an attestation: muscadet cannot "
    "inspect a callable for continuity, so it asks. A DISCONTINUOUS profile -- "
    "a step, a schedule, a lookup table -- needs a watched transition at each "
    "of its breakpoints, so the solver stops AT the jump instead of crossing "
    "it inside an integration step and overshooting by that step; muscadet "
    "does not derive those transitions, so such a profile is refused rather "
    "than silently integrated wrong"
)


class Profile:
    """A continuous function of simulation time scaling an output's production.

    Parameters
    ----------
    fun : callable
        ``fun(time) -> float``, the factor production is multiplied by. Must be
        continuous in ``time`` and must never return a negative value.
    continuous : bool
        The modeller's attestation that ``fun`` is continuous. There is no
        default and nothing but an explicit ``True`` is accepted -- see the
        module docstring for why this cannot be inferred.
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
                f"Profile: {fun!r} is not callable; a profile is built over a "
                "function of simulation time, fun(time) -> float"
            )

        if continuous is not True:
            raise ValueError(f"Profile: {_CONTINUITY_MESSAGE}")

        self.fun = fun
        self.continuous = True
        self.name = name or type(self).__name__

    def evaluate(self, time):
        """The raw factor at ``time``, before any check. The override point."""
        return self.fun(float(time))

    def factor(self, time, flow_name=None):
        """The factor production is multiplied by at ``time``.

        Read on every evaluation of the production sweep, so it must stay a
        pure function of ``time``.

        Raises
        ------
        ValueError
            If the profile returns something that is not a real number, or a
            negative one. Both are model errors rather than numerical accidents:
            a negative factor would be clamped away on a plain output and would
            drain a buffered one, so it is named where it happens instead.
        """
        value = self.evaluate(time)

        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"Profile {self.name}{self._on(flow_name)}: returned "
                f"{value!r} at t={time}, which is not a real number"
            ) from None

        if math.isnan(value):
            raise ValueError(
                f"Profile {self.name}{self._on(flow_name)}: returned NaN at "
                f"t={time}"
            )

        if value < 0.0:
            raise ValueError(
                f"Profile {self.name}{self._on(flow_name)}: returned {value:g} "
                f"at t={time}, and a profile factor may not be negative. "
                "Production is a conserved quantity here and muscadet models no "
                "reverse flow: a negative factor is clamped away on a plain "
                "output and drains a buffered one. Clamp the profile at 0"
            )

        return value

    @staticmethod
    def _on(flow_name):
        return "" if flow_name is None else f" on output flow {flow_name}"

    def __repr__(self):
        return f"{type(self).__name__}(continuous=True)"


class SinusoidalProfile(Profile):
    """A sinusoid of simulation time, clamped between two bounds.

    ``amplitude * sin(2*pi * (t - phase_shift) / period) + offset``, clamped
    into ``[value_min, value_max]``. Continuous by construction -- a clamped
    sinusoid is still continuous, since the clamp meets the curve where it
    crosses the bound -- so it needs no attestation of its own.

    Parameters
    ----------
    amplitude : float, optional
        Half the peak-to-peak swing. Defaults to 1.
    period : float, optional
        Duration of one cycle, in the model's time unit. Must be strictly
        positive. Defaults to ``2*pi``, so that with no other parameter the
        profile is ``sin(t)``.
    phase_shift : float, optional
        Time at which the sine crosses zero going up. Defaults to 0.
    offset : float, optional
        Value the sinusoid oscillates around. Defaults to 0.
    value_min : float, optional
        Lower clamp. Defaults to 0, and may not be negative: see the module
        docstring on why a negative factor is refused rather than reinterpreted
        as a reverse flow.
    value_max : float, optional
        Upper clamp. Defaults to ``math.inf``.

    Raises
    ------
    ValueError
        If ``period`` is not strictly positive, if ``value_min`` is negative, or
        if the clamps are declared the wrong way round.
    """

    def __init__(
        self,
        amplitude=1.0,
        period=2 * math.pi,
        phase_shift=0.0,
        offset=0.0,
        value_min=0.0,
        value_max=math.inf,
        name=None,
    ):
        period = float(period)
        if not period > 0.0:
            raise ValueError(
                f"SinusoidalProfile: period must be strictly positive, got "
                f"{period:g}; it is the duration of one cycle"
            )

        value_min = float(value_min)
        value_max = float(value_max)

        if value_min < 0.0:
            raise ValueError(
                f"SinusoidalProfile: value_min may not be negative, got "
                f"{value_min:g}. A profile SCALES production, so a negative "
                "factor would mean a negative quantity: muscadet models no "
                "reverse flow, and such a value is clamped away on a plain "
                "output while it drains a buffered one. Use value_min=0 to cut "
                "the negative half-cycle, or add an offset to lift the whole "
                "curve above zero"
            )

        if value_max < value_min:
            raise ValueError(
                f"SinusoidalProfile: clamps declared the wrong way round, "
                f"value_min={value_min:g} exceeds value_max={value_max:g}"
            )

        self.amplitude = float(amplitude)
        self.period = period
        self.phase_shift = float(phase_shift)
        self.offset = float(offset)
        self.value_min = value_min
        self.value_max = value_max

        super().__init__(fun=self._sinusoid, continuous=True, name=name)

    def _sinusoid(self, time):
        angle = (time - self.phase_shift) * (2 * math.pi / self.period)
        value = self.amplitude * math.sin(angle) + self.offset

        return min(max(self.value_min, value), self.value_max)

    def __repr__(self):
        return (
            f"SinusoidalProfile(amplitude={self.amplitude:g}, "
            f"period={self.period:g}, phase_shift={self.phase_shift:g}, "
            f"offset={self.offset:g}, value_min={self.value_min:g}, "
            f"value_max={self.value_max:g})"
        )


#: The profile shapes a declaration may name by string, for the ``{"cls": ...}``
#: form the flow and component declarations already use elsewhere.
PROFILE_CLASSES: typing.Dict[str, type] = {
    "Profile": Profile,
    "SinusoidalProfile": SinusoidalProfile,
}


def build_profile(spec, flow_name=None):
    """Normalise a declared profile into a :class:`Profile`, or refuse it.

    Accepts a :class:`Profile` -- returned unchanged -- and the ``{"cls": ...}``
    mapping form the rest of the declaration API uses. A **bare callable is
    deliberately refused**: it carries no continuity attestation, and that
    attestation is the one thing muscadet cannot work out for itself.

    Parameters
    ----------
    spec : Profile or dict or None
        The declaration. ``None`` means "no profile", and yields ``None``.
    flow_name : str, optional
        Named in the error messages.

    Returns
    -------
    Profile or None

    Raises
    ------
    ValueError
        For a bare callable, an unknown ``cls``, a mapping without ``cls``, or
        anything else that is not a profile.
    """
    if spec is None or isinstance(spec, Profile):
        return spec

    where = Profile._on(flow_name)

    if isinstance(spec, dict):
        params = dict(spec)
        clsname = params.pop("cls", None)

        if clsname is None:
            raise ValueError(
                f"Profile declaration{where}: a mapping must carry a 'cls' key "
                f"naming the profile shape, one of "
                f"{', '.join(sorted(PROFILE_CLASSES))}"
            )

        cls = PROFILE_CLASSES.get(clsname)
        if cls is None:
            raise ValueError(
                f"Profile declaration{where}: unknown profile class "
                f"{clsname!r}, expected one of "
                f"{', '.join(sorted(PROFILE_CLASSES))}"
            )

        return cls(**params)

    if callable(spec):
        raise ValueError(f"Profile declaration{where}: {_CONTINUITY_MESSAGE}")

    raise ValueError(
        f"Profile declaration{where}: {spec!r} is not a profile. "
        f"{_CONTINUITY_MESSAGE}"
    )
