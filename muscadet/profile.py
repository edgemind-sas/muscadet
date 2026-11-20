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

Smoothed noise
--------------
Every modulated shape (sinusoid, trapezoid, piecewise-linear) takes an optional
noise modifier, so the factor is::

    factor(t) = clamp(base(t) + noise(t), value_min, value_max)

``noise`` is a sequence of NODES every ``noise_time_scale``: node ``k`` sits at
``t_k = k * noise_time_scale`` and carries ``noise_amplitude * z_k``, with
``z_k`` a standard normal. Between two nodes the value follows a smoothstep,
``z_k + (z_{k+1} - z_k) * s(u)`` with ``s(u) = 3u^2 - 2u^3``: continuous and
differentiable, so the solver's error control sees it like any smooth curve.
``z_0`` is fixed at 0, so the factor at instant 0 is the noise-free one on
every replica: the value published at the start of a sequence does not depend
on the order start methods run in, and does not jump at the first step.

``z_k`` comes from a COUNTER-BASED generator (:func:`noise_node`): a pure
function of the replica seed, the flow key and ``k``, written out below so that
another engine and an editor preview can reproduce it to the bit. Nothing is
drawn during integration; an evaluation is O(1) whatever ``t``.

The **replica seed** is drawn once per Monte Carlo sequence, from the system's
own generator, by ONE start method registered at the pre-run step
(:func:`register_noise_seed`) only when at least one profile of the system
carries noise. A model without noise therefore consumes exactly the draws it
did before. Before any sequence -- at declaration -- the seed is
:data:`NOISE_SEED_BEFORE_ANY_SEQUENCE`, which, with ``z_0 = 0``, changes no
initial value.

The **flow key** (``noise_key``) makes two flows' noises independent. When a
noisy profile is attached to a component's flow without one, it is computed as
:func:`flow_noise_key` of ``"<component>/<flow>"`` and stored on the profile,
so the declaration carries it and another engine receives the same key. Two
components of the same class thus get independent noises.
"""

import bisect
import inspect
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

    #: A profile built over a callable carries no noise modifier.
    has_noise = False

    def attached_to(self, component_name, flow_name):
        """This profile as carried by ``flow_name`` of ``component_name``.

        The override point for a profile whose behaviour depends on the flow
        carrying it (the noise of the shipped shapes); unchanged here.
        """
        return self

    def declaration_omits(self, param):
        """True for a constructor parameter a declaration leaves out. None here."""
        return False

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


# ----------------------------------------------------------------------
# Smoothed noise: a counter-based generator and its interpolation
# ----------------------------------------------------------------------

_MASK64 = 0xFFFFFFFFFFFFFFFF

#: FNV-1a 64-bit parameters, for :func:`flow_noise_key`.
_FNV_OFFSET_BASIS = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3

#: The replica seed a noisy profile holds before any sequence has started, at
#: declaration time. With ``z_0 = 0`` it influences no value at instant 0; it
#: only makes an evaluation outside a run deterministic.
NOISE_SEED_BEFORE_ANY_SEQUENCE = 0

#: The flow key a noisy profile evaluated before being attached to any flow
#: uses. Attaching it to a component's flow replaces it with
#: :func:`flow_noise_key` unless a key was declared.
UNATTACHED_NOISE_KEY = 0

#: Name of the start method that draws the replica seed of every noisy profile.
NOISE_SEED_METHOD_NAME = "seed_profile_noise"

#: The constructor parameters of the noise modifier, written to a declaration
#: only when the profile carries noise.
NOISE_PARAMETERS = ("noise_amplitude", "noise_time_scale", "noise_key")


def fnv1a_64(text):
    """FNV-1a 64-bit hash of the UTF-8 bytes of ``text``.

    Offset basis ``0xcbf29ce484222325``, prime ``0x100000001b3``: each byte is
    XORed in, then the value is multiplied by the prime, modulo 2**64. Written
    out rather than Python's ``hash()``, which is salted per process.

    Returns
    -------
    int
        In ``[0, 2**64)``.
    """
    value = _FNV_OFFSET_BASIS
    for byte in text.encode("utf-8"):
        value ^= byte
        value = (value * _FNV_PRIME) & _MASK64
    return value


def flow_noise_key(component_name, flow_name):
    """The 64-bit flow key of ``flow_name`` on ``component_name``.

    :func:`fnv1a_64` of ``"<component>/<flow>"``. The same on every engine
    and in every process, so a declaration and a rerun agree on it.
    """
    return fnv1a_64(f"{component_name}/{flow_name}")


def _splitmix64(value):
    """One SplitMix64 output for the state ``value`` (a 64-bit mixing step)."""
    value = (value + 0x9E3779B97F4A7C15) & _MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK64
    return value ^ (value >> 31)


def replica_seed(draw):
    """The integer replica seed of a uniform draw in ``[0, 1)``.

    ``floor(draw * 2**53)``: the 53 bits of a double's mantissa, so two
    different draws give two different seeds.
    """
    return int(float(draw) * 9007199254740992.0) & _MASK64


def noise_node(seed, key, index):
    """The standard normal ``z_index`` of the noise of one flow in one replica.

    ``z_0`` is 0 by definition. Otherwise, all arithmetic modulo 2**64 and
    ``splitmix64`` the usual SplitMix64 output function::

        h  = splitmix64(splitmix64(splitmix64(seed) ^ key) ^ index)
        u1 = ((h >> 11) + 1) / 2**53                  # in (0, 1]
        u2 = (splitmix64(h) >> 11) / 2**53            # in [0, 1)
        z  = sqrt(-2 ln u1) * cos(2 pi u2)            # Box-Muller

    A pure function of its three arguments: there is no generator state, so a
    node is the same whatever was evaluated before it.
    """
    if index == 0:
        return 0.0

    mixed = _splitmix64(_splitmix64(_splitmix64(seed) ^ key) ^ (index & _MASK64))
    u1 = ((mixed >> 11) + 1) * 1.1102230246251565e-16
    u2 = (_splitmix64(mixed) >> 11) * 1.1102230246251565e-16

    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _smoothstep(u):
    """``3u^2 - 2u^3``: 0 and 1 at the ends, with zero slope at both."""
    return u * u * (3.0 - 2.0 * u)


def register_noise_seed(system):
    """Register the start method drawing the replica seed of the noisy profiles.

    Called once, from the pre-run step. Collects the profiles that carry noise
    on every flow of every component; when there is none, registers NOTHING,
    so a model without noise consumes the same draws as before the noise
    existed. Otherwise registers ONE start method, on the first component
    carrying a noisy profile, which draws a single ``system.RNG().drand()`` per
    sequence and hands it to every noisy profile; each derives its own stream
    from it through its flow key.

    Returns
    -------
    list
        The noisy profiles seeded per sequence. Empty when nothing is
        registered, and on a second call.
    """
    if getattr(system, "_noise_seed_registered", False):
        return []

    host = None
    profiles = []
    for comp in system.comp.values():
        for flows in (getattr(comp, "flows_out", {}), getattr(comp, "flows_in", {})):
            for flow in flows.values():
                profile = getattr(flow, "profile", None)
                if profile is not None and getattr(profile, "has_noise", False):
                    profiles.append(profile)
                    if host is None:
                        host = comp

    if not profiles:
        return []

    def seed_profiles():
        draw = system.RNG().drand()
        for profile in profiles:
            profile.seed_noise(draw)

    host.addStartMethod(NOISE_SEED_METHOD_NAME, seed_profiles)

    # PyCATSHOO stores a sequence in which no transition drew a date as THE
    # deterministic sequence and replays it instead of simulating the next one
    # (on by default). A draw made in a start method does not count, so a
    # noisy model with no stochastic transition would give every replica the
    # noise of the first. Off only here: a model without noise keeps it.
    system.setStoreDeterministicSeq(False)
    # Kept alive on the system for the lifetime of the engine registration.
    system._noise_seed_method = seed_profiles
    system._noise_seed_registered = True

    return profiles


class _ModulatedProfile(Profile):
    """A shipped shape: a base curve, a clamp, and an optional noise modifier.

    ``factor(t) = clamp(base(t) + noise(t), value_min, value_max)``. Subclasses
    implement :meth:`_base`, unclamped, and call :meth:`_init_modulation` from
    their constructor. See the module docstring for the noise.
    """

    def _init_modulation(
        self, shape, value_min, value_max, noise_amplitude, noise_time_scale, noise_key
    ):
        value_min, value_max = _validated_clamp(shape, value_min, value_max)
        noise_amplitude, noise_time_scale, noise_key = _validated_noise(
            shape, noise_amplitude, noise_time_scale, noise_key
        )

        self.value_min = value_min
        self.value_max = value_max
        self.noise_amplitude = noise_amplitude
        self.noise_time_scale = noise_time_scale
        self.noise_key = noise_key
        self.noise_seed = NOISE_SEED_BEFORE_ANY_SEQUENCE
        self._noise_node_pair = None

    @property
    def has_noise(self):
        """True when this profile carries a noise modifier."""
        return self.noise_amplitude > 0.0

    def _base(self, time):
        raise NotImplementedError

    def _modulated(self, time):
        value = self._base(time)

        if self.noise_amplitude > 0.0:
            value += self.noise(time)

        return _clamp(value, self.value_min, self.value_max)

    def seed_noise(self, draw):
        """Take the replica seed of a new sequence, from a uniform ``draw``."""
        self.noise_seed = replica_seed(draw)

    def noise(self, time):
        """The noise added to the base curve at ``time``, before the clamp.

        0 on a profile without noise. Otherwise ``noise_amplitude`` times the
        smoothstep interpolation between the two nodes around ``time``, for
        the current replica seed. O(1): two nodes, cached while ``time`` stays
        between the same two.
        """
        if not self.noise_amplitude > 0.0:
            return 0.0

        position = time / self.noise_time_scale
        index = math.floor(position)
        key = UNATTACHED_NOISE_KEY if self.noise_key is None else self.noise_key

        pair = self._noise_node_pair
        if pair is None or pair[0] != (self.noise_seed, key, index):
            pair = (
                (self.noise_seed, key, index),
                noise_node(self.noise_seed, key, index),
                noise_node(self.noise_seed, key, index + 1),
            )
            self._noise_node_pair = pair

        _, start, end = pair
        z = start + (end - start) * _smoothstep(position - index)

        return self.noise_amplitude * z

    def attached_to(self, component_name, flow_name):
        """This profile as carried by ``flow_name`` of ``component_name``.

        A noisy profile without a declared ``noise_key`` gets the flow's key
        (:func:`flow_noise_key`). It is a NEW profile rather than this one
        modified, because the same object may be handed to several flows --
        two components of one class -- and each must keep its own key.
        Anything else is returned unchanged.
        """
        if not self.has_noise or self.noise_key is not None:
            return self

        params = {
            name: getattr(self, name)
            for name in inspect.signature(type(self).__init__).parameters
            if name != "self" and hasattr(self, name)
        }
        params["noise_key"] = flow_noise_key(component_name, flow_name)

        return type(self)(**params)

    def declaration_omits(self, param):
        """True for a constructor parameter a declaration leaves out.

        The noise parameters are written only when the profile carries noise,
        so the declaration of a noise-free profile is the one it always was.
        """
        return param in NOISE_PARAMETERS and not self.has_noise

    def _noise_repr(self):
        if not self.has_noise:
            return ""
        return (
            f", noise_amplitude={self.noise_amplitude:g}, "
            f"noise_time_scale={self.noise_time_scale:g}, "
            f"noise_key={self.noise_key!r}"
        )


def _validated_noise(shape, noise_amplitude, noise_time_scale, noise_key):
    """Check and normalise the noise parameters of a shape.

    Returns
    -------
    tuple
        ``(noise_amplitude, noise_time_scale, noise_key)``: a float, a float or
        ``None``, an int or ``None``.

    Raises
    ------
    ValueError
        If the amplitude is negative or not finite, if only one of the
        amplitude and the time scale is given, if the time scale is not
        strictly positive, or if the key is not an integer in ``[0, 2**64)``.
    """
    noise_amplitude = float(noise_amplitude)
    if not (noise_amplitude >= 0.0 and math.isfinite(noise_amplitude)):
        raise ValueError(
            f"{shape}: noise_amplitude must be a finite non-negative number, "
            f"got {noise_amplitude:g}"
        )

    if noise_amplitude > 0.0:
        if noise_time_scale is None:
            raise ValueError(
                f"{shape}: noise_amplitude={noise_amplitude:g} needs a "
                "noise_time_scale, the spacing of the noise nodes"
            )
        noise_time_scale = float(noise_time_scale)
        if not (noise_time_scale > 0.0 and math.isfinite(noise_time_scale)):
            raise ValueError(
                f"{shape}: noise_time_scale must be strictly positive and "
                f"finite, got {noise_time_scale:g}; it is the spacing of the "
                "noise nodes"
            )
    elif noise_time_scale is not None:
        raise ValueError(
            f"{shape}: noise_time_scale={noise_time_scale!r} is declared without "
            "noise; give a positive noise_amplitude, or leave both out"
        )

    if noise_key is not None:
        if isinstance(noise_key, bool) or not isinstance(noise_key, int):
            raise ValueError(
                f"{shape}: noise_key must be an integer, got {noise_key!r}"
            )
        if not 0 <= noise_key <= _MASK64:
            raise ValueError(
                f"{shape}: noise_key must fit in 64 unsigned bits, got {noise_key}"
            )

    return noise_amplitude, noise_time_scale, noise_key


class SinusoidalProfile(_ModulatedProfile):
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
    noise_amplitude : float, optional
        Standard deviation of the smoothed noise added to the curve before the
        clamp. Defaults to 0, no noise. See the module docstring.
    noise_time_scale : float, optional
        Spacing of the noise nodes, strictly positive. Required when
        ``noise_amplitude`` is positive, refused otherwise.
    noise_key : int, optional
        64-bit flow key of the noise. Computed from the component and flow
        names when the profile is attached to a flow, if left out.

    Raises
    ------
    ValueError
        If ``period`` is not strictly positive, if ``value_min`` is negative, if
        the clamps are declared the wrong way round, or if the noise parameters
        are invalid.
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
        noise_amplitude=0.0,
        noise_time_scale=None,
        noise_key=None,
    ):
        period = float(period)
        if not period > 0.0:
            raise ValueError(
                f"SinusoidalProfile: period must be strictly positive, got "
                f"{period:g}; it is the duration of one cycle"
            )

        self._init_modulation(
            "SinusoidalProfile",
            value_min,
            value_max,
            noise_amplitude,
            noise_time_scale,
            noise_key,
        )

        self.amplitude = float(amplitude)
        self.period = period
        self.phase_shift = float(phase_shift)
        self.offset = float(offset)

        super().__init__(fun=self._modulated, continuous=True, name=name)

    def _base(self, time):
        angle = (time - self.phase_shift) * (2 * math.pi / self.period)
        return self.amplitude * math.sin(angle) + self.offset

    def __repr__(self):
        return (
            f"SinusoidalProfile(amplitude={self.amplitude:g}, "
            f"period={self.period:g}, phase_shift={self.phase_shift:g}, "
            f"offset={self.offset:g}, value_min={self.value_min:g}, "
            f"value_max={self.value_max:g}{self._noise_repr()})"
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


class TrapezoidProfile(_ModulatedProfile):
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
    noise_amplitude : float, optional
        Standard deviation of the smoothed noise added to the curve before the
        clamp. Defaults to 0, no noise. See the module docstring.
    noise_time_scale : float, optional
        Spacing of the noise nodes, strictly positive. Required when
        ``noise_amplitude`` is positive, refused otherwise.
    noise_key : int, optional
        64-bit flow key of the noise. Computed from the component and flow
        names when the profile is attached to a flow, if left out.

    Raises
    ------
    ValueError
        If a ramp is not strictly positive, if the period is not strictly
        positive, if the plateau is negative, if the plateau and both ramps do
        not fit in one period, if a level is negative, or if the clamp or the
        noise parameters are invalid.
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
        noise_amplitude=0.0,
        noise_time_scale=None,
        noise_key=None,
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

        self._init_modulation(
            shape, value_min, value_max, noise_amplitude, noise_time_scale, noise_key
        )

        self.low = low
        self.high = high
        self.period = period
        self.high_duration = high_duration
        self.ramp_duration = ramp_duration
        self.phase_shift = float(phase_shift)

        self._fall_start = ramp_duration + high_duration
        self._fall_end = self._fall_start + ramp_duration

        super().__init__(fun=self._modulated, continuous=True, name=shape)

    def _base(self, time):
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

        return value

    def __repr__(self):
        return (
            f"TrapezoidProfile(low={self.low:g}, high={self.high:g}, "
            f"period={self.period:g}, high_duration={self.high_duration:g}, "
            f"ramp_duration={self.ramp_duration:g}, "
            f"phase_shift={self.phase_shift:g}, value_min={self.value_min:g}, "
            f"value_max={self.value_max:g}{self._noise_repr()})"
        )


class PiecewiseLinearProfile(_ModulatedProfile):
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
    noise_amplitude : float, optional
        Standard deviation of the smoothed noise added to the curve before the
        clamp. Defaults to 0, no noise. See the module docstring.
    noise_time_scale : float, optional
        Spacing of the noise nodes, strictly positive. Required when
        ``noise_amplitude`` is positive, refused otherwise.
    noise_key : int, optional
        64-bit flow key of the noise. Computed from the component and flow
        names when the profile is attached to a flow, if left out.

    Raises
    ------
    ValueError
        If the points break any rule above, if the period is not strictly
        positive, or if the clamp or the noise parameters are invalid.
    """

    def __init__(
        self,
        points,
        period,
        phase_shift=0.0,
        value_min=0.0,
        value_max=math.inf,
        noise_amplitude=0.0,
        noise_time_scale=None,
        noise_key=None,
    ):
        shape = "PiecewiseLinearProfile"
        period = _validated_period(shape, period)
        points = self._validated_points(shape, points, period)
        self._init_modulation(
            shape, value_min, value_max, noise_amplitude, noise_time_scale, noise_key
        )

        self.points = points
        self.period = period
        self.phase_shift = float(phase_shift)

        self._instants = tuple(instant for instant, _ in points)
        self._factors = tuple(factor for _, factor in points)

        super().__init__(fun=self._modulated, continuous=True, name=shape)

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

    def _base(self, time):
        u = (time - self.phase_shift) % self.period
        instants = self._instants
        factors = self._factors

        index = bisect.bisect_right(instants, u) - 1
        start, value_start = instants[index], factors[index]

        if index + 1 < len(instants):
            end, value_end = instants[index + 1], factors[index + 1]
        else:
            end, value_end = self.period, factors[0]

        return value_start + (value_end - value_start) * ((u - start) / (end - start))

    def __repr__(self):
        return (
            f"PiecewiseLinearProfile(points={self.points!r}, "
            f"period={self.period:g}, phase_shift={self.phase_shift:g}, "
            f"value_min={self.value_min:g}, value_max={self.value_max:g}"
            f"{self._noise_repr()})"
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
