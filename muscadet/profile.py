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
               x  production gate (R44: 1, or 0 when the condition fails)

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

import bisect
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


def _validated_clamp(shape, value_min, value_max):
    """Check and normalise the ``[value_min, value_max]`` clamp of a shape.

    Shared by every modulated shape so that the bounds are refused the same way
    everywhere.

    Parameters
    ----------
    shape : str
        The class name, used in the error messages.
    value_min, value_max : float
        The lower and upper clamp.

    Returns
    -------
    tuple of float
        ``(value_min, value_max)`` as floats.

    Raises
    ------
    ValueError
        If ``value_min`` is negative, or if the bounds are the wrong way round.
    """
    value_min = float(value_min)
    value_max = float(value_max)

    if value_min < 0.0:
        raise ValueError(
            f"{shape}: value_min may not be negative, got "
            f"{value_min:g}. A profile SCALES production, so a negative "
            "factor would mean a negative quantity: muscadet models no "
            "reverse flow, and such a value is clamped away on a plain "
            "output while it drains a buffered one. Use value_min=0 to cut "
            "the negative half-cycle, or add an offset to lift the whole "
            "curve above zero"
        )

    if value_max < value_min:
        raise ValueError(
            f"{shape}: clamps declared the wrong way round, "
            f"value_min={value_min:g} exceeds value_max={value_max:g}"
        )

    return value_min, value_max


def _clamp(value, value_min, value_max):
    """``value`` clamped into ``[value_min, value_max]``."""
    return min(max(value_min, value), value_max)


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

        value_min, value_max = _validated_clamp(
            "SinusoidalProfile", value_min, value_max
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

        return _clamp(value, self.value_min, self.value_max)

    def __repr__(self):
        return (
            f"SinusoidalProfile(amplitude={self.amplitude:g}, "
            f"period={self.period:g}, phase_shift={self.phase_shift:g}, "
            f"offset={self.offset:g}, value_min={self.value_min:g}, "
            f"value_max={self.value_max:g})"
        )


def _validated_period(shape, period):
    """``period`` as a float, refused unless strictly positive."""
    period = float(period)
    if not period > 0.0:
        raise ValueError(
            f"{shape}: period must be strictly positive, got {period:g}; it is "
            "the duration of one cycle"
        )
    return period


class TrapezoidProfile(Profile):
    """A periodic square wave whose edges are linear ramps.

    Within one period, with ``u = (t - phase_shift) mod period``: a linear ramp
    from ``low`` to ``high`` on ``[0, ramp_duration)``, ``high`` on
    ``[ramp_duration, ramp_duration + high_duration)``, a linear ramp back to
    ``low`` over the next ``ramp_duration``, and ``low`` for the rest of the
    period. The result is clamped into ``[value_min, value_max]``.

    The ramps are what keeps the wave continuous: a zero ramp would be a jump,
    which the solver would integrate across without stopping at it, so it is
    refused. Short ramps are fine; the solver's error control shortens its step
    around them.

    Parameters
    ----------
    low : float
        Factor outside the plateau. May not be negative.
    high : float
        Factor on the plateau. May not be negative.
    period : float
        Duration of one cycle, strictly positive.
    high_duration : float
        Duration of the plateau, between the two ramps. May be 0.
    ramp_duration : float
        Duration of each ramp, strictly positive.
    phase_shift : float, optional
        Time at which the rising ramp starts. Defaults to 0.
    value_min : float, optional
        Lower clamp. Defaults to 0, and may not be negative.
    value_max : float, optional
        Upper clamp. Defaults to ``math.inf``.

    Raises
    ------
    ValueError
        If a ramp is not strictly positive, if the period is not strictly
        positive, if the plateau is negative, if the plateau and both ramps do
        not fit in one period, if a level is negative, or if the clamp is
        invalid.
    """

    def __init__(
        self,
        low,
        high,
        period,
        high_duration,
        ramp_duration,
        phase_shift=0.0,
        value_min=0.0,
        value_max=math.inf,
    ):
        shape = "TrapezoidProfile"
        period = _validated_period(shape, period)
        low = float(low)
        high = float(high)
        high_duration = float(high_duration)
        ramp_duration = float(ramp_duration)

        if not ramp_duration > 0.0:
            raise ValueError(
                f"{shape}: ramp_duration must be strictly positive, got "
                f"{ramp_duration:g}. A zero ramp is a jump, a discontinuity the "
                "solver would integrate across without stopping at it; give the "
                "edge a duration, however short"
            )

        if high_duration < 0.0:
            raise ValueError(
                f"{shape}: high_duration may not be negative, got {high_duration:g}"
            )

        if 2.0 * ramp_duration + high_duration > period:
            raise ValueError(
                f"{shape}: the plateau ({high_duration:g}) and its two ramps "
                f"({ramp_duration:g} each) last longer than the period "
                f"({period:g})"
            )

        for label, level in (("low", low), ("high", high)):
            if level < 0.0:
                raise ValueError(
                    f"{shape}: {label} may not be negative, got {level:g}; a "
                    "profile factor scales production and muscadet models no "
                    "reverse flow"
                )

        value_min, value_max = _validated_clamp(shape, value_min, value_max)

        self.low = low
        self.high = high
        self.period = period
        self.high_duration = high_duration
        self.ramp_duration = ramp_duration
        self.phase_shift = float(phase_shift)
        self.value_min = value_min
        self.value_max = value_max

        self._fall_start = ramp_duration + high_duration
        self._fall_end = self._fall_start + ramp_duration

        super().__init__(fun=self._trapezoid, continuous=True, name=shape)

    def _trapezoid(self, time):
        u = (time - self.phase_shift) % self.period

        if u < self.ramp_duration:
            value = self.low + (self.high - self.low) * (u / self.ramp_duration)
        elif u < self._fall_start:
            value = self.high
        elif u < self._fall_end:
            value = self.high + (self.low - self.high) * (
                (u - self._fall_start) / self.ramp_duration
            )
        else:
            value = self.low

        return _clamp(value, self.value_min, self.value_max)

    def __repr__(self):
        return (
            f"TrapezoidProfile(low={self.low:g}, high={self.high:g}, "
            f"period={self.period:g}, high_duration={self.high_duration:g}, "
            f"ramp_duration={self.ramp_duration:g}, "
            f"phase_shift={self.phase_shift:g}, value_min={self.value_min:g}, "
            f"value_max={self.value_max:g})"
        )


class PiecewiseLinearProfile(Profile):
    """A drawn periodic curve: points joined by segments.

    ``points`` lists ``[instant, factor]`` pairs within one period. With
    ``u = (t - phase_shift) mod period``, the factor is interpolated linearly
    between the two points around ``u``; the last point is joined to the FIRST
    one, one period later, so the curve repeats without a jump. The result is
    clamped into ``[value_min, value_max]``.

    Evaluation is a binary search over the instants, with no allocation.

    Parameters
    ----------
    points : list of [float, float]
        At least two ``[instant, factor]`` pairs. The first instant is exactly
        0, instants are strictly increasing and below ``period``, and factors
        are not negative. Stored as a list of two-float lists.
    period : float
        Duration of one cycle, strictly positive.
    phase_shift : float, optional
        Time at which the first point is reached. Defaults to 0.
    value_min : float, optional
        Lower clamp. Defaults to 0, and may not be negative.
    value_max : float, optional
        Upper clamp. Defaults to ``math.inf``.

    Raises
    ------
    ValueError
        If the points break any rule above, if the period is not strictly
        positive, or if the clamp is invalid.
    """

    def __init__(
        self,
        points,
        period,
        phase_shift=0.0,
        value_min=0.0,
        value_max=math.inf,
    ):
        shape = "PiecewiseLinearProfile"
        period = _validated_period(shape, period)
        points = self._validated_points(shape, points, period)
        value_min, value_max = _validated_clamp(shape, value_min, value_max)

        self.points = points
        self.period = period
        self.phase_shift = float(phase_shift)
        self.value_min = value_min
        self.value_max = value_max

        self._instants = tuple(instant for instant, _ in points)
        self._factors = tuple(factor for _, factor in points)

        super().__init__(fun=self._piecewise, continuous=True, name=shape)

    @staticmethod
    def _validated_points(shape, points, period):
        try:
            pairs = [list(pair) for pair in points]
        except TypeError:
            raise ValueError(
                f"{shape}: points must be a list of [instant, factor] pairs, "
                f"got {points!r}"
            ) from None

        if len(pairs) < 2:
            raise ValueError(
                f"{shape}: at least two points are needed, got {len(pairs)}"
            )

        normalised = []
        for index, pair in enumerate(pairs):
            if len(pair) != 2:
                raise ValueError(
                    f"{shape}: point {index} is {pair!r}, not an "
                    "[instant, factor] pair"
                )
            instant, factor = float(pair[0]), float(pair[1])

            if factor < 0.0:
                raise ValueError(
                    f"{shape}: point {index} has factor {factor:g}, and a "
                    "profile factor may not be negative"
                )
            if not instant < period:
                raise ValueError(
                    f"{shape}: point {index} is at instant {instant:g}, which "
                    f"is not below the period ({period:g}); the curve returns "
                    "to its first point at the period by itself"
                )
            if normalised and not instant > normalised[-1][0]:
                raise ValueError(
                    f"{shape}: instants must be strictly increasing, point "
                    f"{index} at {instant:g} follows {normalised[-1][0]:g}"
                )
            normalised.append([instant, factor])

        if normalised[0][0] != 0.0:
            raise ValueError(
                f"{shape}: the first point must be at instant 0, got "
                f"{normalised[0][0]:g}"
            )

        return normalised

    def _piecewise(self, time):
        u = (time - self.phase_shift) % self.period
        instants = self._instants
        factors = self._factors

        index = bisect.bisect_right(instants, u) - 1
        start, value_start = instants[index], factors[index]

        if index + 1 < len(instants):
            end, value_end = instants[index + 1], factors[index + 1]
        else:
            end, value_end = self.period, factors[0]

        value = value_start + (value_end - value_start) * ((u - start) / (end - start))

        return _clamp(value, self.value_min, self.value_max)

    def __repr__(self):
        return (
            f"PiecewiseLinearProfile(points={self.points!r}, "
            f"period={self.period:g}, phase_shift={self.phase_shift:g}, "
            f"value_min={self.value_min:g}, value_max={self.value_max:g})"
        )


#: The profile shapes a declaration may name by string, for the ``{"cls": ...}``
#: form the flow and component declarations already use elsewhere.
PROFILE_CLASSES: typing.Dict[str, type] = {
    "Profile": Profile,
    "SinusoidalProfile": SinusoidalProfile,
    "TrapezoidProfile": TrapezoidProfile,
    "PiecewiseLinearProfile": PiecewiseLinearProfile,
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
