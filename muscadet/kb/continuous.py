"""The continuous components MUSCADET ships (R32).

Seven domain-neutral shapes, and nothing else: a **source** holding a declared
rate, a **sinusoidal source** whose rate follows a curve of simulation time, a
**transformer** taking its rules as parameters, a **capacity** wrapping the
volume declaration, a **consumer** publishing a declared demand, and a **sensor**
reading a capacity level and driving a discrete control output, and an
**exchange** moving a computed quantity because a gradient makes it move.

Only shapes that appeared TWICE outside the library ship here. An electrolyser
with a membrane leak percentage or a battery with a start-up policy stays with
the project that needs it: these seven carry no chemistry, no electricity and no
hydraulics, in their names or in their parameters.

The sensor is the load-bearing one. A rule guard cannot reference a capacity
level (R29) -- that restriction removes a class of mode chattering by
construction -- so reading a level over a MEASUREMENT link and driving a
discrete control port is the only sanctioned way to gate production on a level
(F4). Because a control loop closed through a threshold oscillates around it,
the sensor carries a **deadband**: the level at which it activates and the
level at which it releases. A single threshold is the degenerate case where the
two coincide.

Every one of them is composition over the declaration API: no component here
writes an equation, reads a level in Python or subclasses a flow.
"""

import re

import muscadet

#: Flow name used when a component declares none.
DEFAULT_FLOW = "q"


def _allocation_keys():
    """The allocation parameters a continuous output declares (R16, R17).

    Derived from :class:`muscadet.FlowContinuousOut` rather than repeated
    here, so a fifth allocation field added to the flow is forwarded by every
    component below without a second edit. The prefix is the whole contract:
    ``allocated`` -- the split the last production sweep computed -- is
    runtime state rather than a declaration, and does not carry it.
    """
    return tuple(
        sorted(
            name
            for name in muscadet.FlowContinuousOut.model_fields
            if name.startswith("allocation")
        )
    )


#: Allocation parameters a continuous output accepts (R16, R17), forwarded
#: verbatim so a caller declares a policy without subclassing.
ALLOCATION_KEYS = _allocation_keys()


def flow_declarations(spec, default_name=None, **defaults):
    """Normalise a flow declaration into a list of parameter mappings.

    Accepts what a modeller naturally writes -- a name, a list of names, a
    mapping, a list of mappings -- and always returns mappings carrying at
    least ``name``. ``defaults`` fills the keys a declaration leaves out.

    Parameters
    ----------
    spec : str or dict or list or None
        The declaration to normalise.
    default_name : str, optional
        Flow name used when ``spec`` is None.
    **defaults : dict
        Values applied to every entry that does not carry the key itself.

    Returns
    -------
    list of dict
        One mapping per declared flow, in declaration order.
    """
    if spec is None:
        spec = [] if default_name is None else [default_name]
    if isinstance(spec, (str, dict)):
        spec = [spec]

    entries = []
    for item in spec:
        entry = dict(name=item) if isinstance(item, str) else dict(item)
        if "name" not in entry:
            raise ValueError(f"Flow declaration {item} carries no name")
        for key, value in defaults.items():
            entry.setdefault(key, value)
        entries.append(entry)

    return entries


def allocation_params(kwargs):
    """The allocation parameters declared on a component, if any (R16, R17)."""
    return {key: kwargs[key] for key in ALLOCATION_KEYS if key in kwargs}


class ContinuousComponent(muscadet.ObjFlow):
    """Shared base of the shipped components: a CHECKED declaration.

    Every component here reads its parameters with ``kwargs.get(key,
    default)``, so a misspelled key would otherwise be swallowed and its
    parameter silently take its default -- indistinguishable, for a numeric
    parameter, from a legitimate zero. Everywhere else in this release a
    malformed declaration is refused at declaration time naming the component
    and the offending item, and this base is what makes the KB layer do the
    same.

    The accepted set is DECLARED, not inferred: a component lists in
    :attr:`DECLARATION_KEYS` exactly what it reads. The check unions that
    attribute over the MRO rather than reading it off the class, so a project
    subclassing one of these five to add a parameter of its own declares that
    one key and inherits the rest.
    """

    #: Declaration keys every continuous component accepts. ``metadata`` is
    #: put back into the kwargs by :meth:`muscadet.ObjFlow.__init__` before it
    #: calls ``add_flows``, so it reaches every component here whether or not
    #: a caller declared it. Everything else the constructor's own signature
    #: consumes -- ``name``, ``label``, ``description``, ``partial_init``,
    #: ``create_default_out_automata`` -- never gets this far.
    DECLARATION_KEYS = ("metadata",)

    @classmethod
    def accepted_declaration_keys(cls):
        """Every declaration key ``cls`` accepts, its bases' included."""
        keys = set()
        for klass in cls.__mro__:
            keys.update(klass.__dict__.get("DECLARATION_KEYS", ()))
        return keys

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        accepted = self.accepted_declaration_keys()
        unknown = sorted(set(kwargs) - accepted)

        if unknown:
            plural = "s" if len(unknown) > 1 else ""
            unknown_str = ", ".join(repr(key) for key in unknown)
            accepted_str = ", ".join(sorted(accepted))
            raise ValueError(
                f"Object {self.name()}: {type(self).__name__} does not "
                f"accept declaration key{plural} {unknown_str}; "
                f"it accepts {accepted_str}"
            )


class SourceContinuous(ContinuousComponent):
    """A continuous output delivering a declared rate.

    What it delivers is reconciled with the demand like any other production:
    an output holding a rate nobody asks for delivers less.

    Declared with a ``control``, it becomes the producing component of the
    sensor pattern (F4): it gains a discrete input of that name and a rule set
    guarded on it, so a sensor's control signal gates the rate without any
    subclassing.

    Parameters
    ----------
    flow : str, optional
        Name of the continuous output. Defaults to ``"q"``.
    rate : float, optional
        The rate it can produce. Defaults to 0.
    control : str, optional
        Name of a discrete input gating the rate. Left out, the source produces
        unconditionally.
    control_logic : str or int, optional
        Input logic of the control port, as ``add_flow_in`` takes it.
    profile : muscadet.Profile or dict, optional
        A declared CONTINUOUS function of simulation time the rate is
        multiplied by. Composed with any derating by PRODUCT, never by minimum.
    allocation, allocation_shares, allocation_priorities, allocation_fun
        Forwarded to the output flow, where an insufficient supply is split.
    """

    DECLARATION_KEYS = (
        "flow",
        "rate",
        "control",
        "control_logic",
        "profile",
    ) + ALLOCATION_KEYS

    #: Rate a source produces when it declares none. A subclass whose profile
    #: carries the whole quantity -- :class:`SourceSinusoidalContinuous`, whose
    #: amplitude and offset are in flow units -- raises this to 1 so that the
    #: nominal it scales is neutral.
    DEFAULT_RATE = 0.0

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        flow = kwargs.get("flow", DEFAULT_FLOW)
        rate = float(kwargs.get("rate", self.DEFAULT_RATE))
        control = kwargs.get("control")

        self.add_flow_continuous_out(
            name=flow,
            var_fed_default=rate,
            **self.profile_params(kwargs),
            **allocation_params(kwargs),
        )

        if control is None:
            return

        self.add_flow_in(name=control, logic=kwargs.get("control_logic", "and"))
        self.add_rules(
            name=f"{flow}_control",
            rules=[
                dict(name="supply", cond=control, prod={flow: rate}),
                dict(name="idle", cond=f"not {control}", prod={flow: 0.0}),
            ],
        )

    def profile_params(self, kwargs):
        """The ``profile`` parameter of the output flow, if one is declared.

        The seam :class:`SourceSinusoidalContinuous` overrides to BUILD its
        profile out of its own declaration keys instead of taking one ready
        made.
        """
        profile = kwargs.get("profile")

        return {} if profile is None else {"profile": profile}


class SourceSinusoidalContinuous(SourceContinuous):
    """A source whose rate follows a sinusoid of simulation time.

    The shipped time profile: a solar curve, a daily demand cycle, a seasonal
    swing. It is a plain :class:`SourceContinuous` carrying a
    :class:`muscadet.SinusoidalProfile`, so everything a source does -- the
    ``control`` port of the sensor pattern, the allocation policies, a failure
    mode derating it -- applies here unchanged.

    ``amplitude`` and ``offset`` are in **flow units**, exactly as they are in
    the reference implementation this shape is ported from, which is why
    ``rate`` defaults to 1 here rather than to 0: the profile carries the
    quantity, and ``rate`` is a further nominal multiplier for a caller who
    wants one.

    **Composition.** The profile multiplies; a derating takes the minimum among
    deratings and then multiplies in turn. A panel at 0.3 of its curve that a
    failure mode has cut to 0.5 produces 0.15 of ``rate``, not 0.3.

    **Non-negative by declaration.** ``value_min`` defaults to 0 and may not be
    negative -- unlike the reference implementation, whose ``-inf`` default
    admitted a negative production. No model there relied on it: every
    declaration site clamped at 0, and the few that did not were arranged so
    the curve stayed non-negative over the simulated window. A negative
    quantity in a conserved-flow model would be clamped away on a plain output
    and would drain a buffered one, so it is refused here instead.

    Parameters
    ----------
    flow : str, optional
        Name of the continuous output. Defaults to ``"q"``.
    rate : float, optional
        Nominal the sinusoid scales. Defaults to 1, so the curve is the
        production.
    amplitude : float, optional
        Half the peak-to-peak swing, in flow units. Defaults to 1.
    period : float, optional
        Duration of one cycle. Must be strictly positive. Defaults to ``2*pi``.
    phase_shift : float, optional
        Time at which the sine crosses zero going up. Defaults to 0.
    offset : float, optional
        Value the sinusoid oscillates around. Defaults to 0.
    value_min : float, optional
        Lower clamp, defaulting to 0 and refused below it.
    value_max : float, optional
        Upper clamp. Defaults to ``math.inf``.
    control, control_logic, allocation, allocation_shares, ...
        As on :class:`SourceContinuous`.

    Raises
    ------
    ValueError
        If ``period`` is not strictly positive, if ``value_min`` is negative,
        if the clamps are the wrong way round, or if a ``profile`` is declared
        alongside the sinusoid parameters -- this component builds its own.
    """

    DECLARATION_KEYS = (
        "amplitude",
        "period",
        "phase_shift",
        "offset",
        "value_min",
        "value_max",
    )

    #: The sinusoid is expressed in flow units, so the nominal it scales is 1.
    DEFAULT_RATE = 1.0

    #: Sinusoid parameters, mapped onto the profile's own argument names.
    PROFILE_KEYS = ("amplitude", "period", "phase_shift", "offset")

    def profile_params(self, kwargs):
        if kwargs.get("profile") is not None:
            raise ValueError(
                f"Object {self.name()}: {type(self).__name__} builds its own "
                "profile out of amplitude / period / phase_shift / offset, so "
                "it does not take a 'profile' of its own. Declare a plain "
                "SourceContinuous to carry an arbitrary profile"
            )

        params = {key: kwargs[key] for key in self.PROFILE_KEYS if key in kwargs}

        for key in ("value_min", "value_max"):
            if key in kwargs:
                params[key] = kwargs[key]

        return {"profile": muscadet.SinusoidalProfile(**params)}


class TransformerContinuous(ContinuousComponent):
    """Continuous inputs turned into continuous outputs by declared rules.

    The rules are a PARAMETER, so a caller states its ``cons`` and ``prod``
    coefficients -- and the guards selecting between them -- without writing a
    subclass. Correlated outputs scale together, since a rule set is declared
    on the component and not one output at a time.

    Parameters
    ----------
    flows_in : str or dict or list, optional
        Continuous inputs, as names or as mappings of ``add_flow_continuous_in``
        parameters.
    flows_out : str or dict or list, optional
        Continuous outputs, same forms. Allocation parameters declared on the
        component apply to every one of them, and an entry may carry its own
        ``profile`` -- the mapping is forwarded verbatim to
        ``add_flow_continuous_out``.
    controls : str or dict or list, optional
        Discrete inputs the guards read, ``logic`` defaulting to ``"and"``.
    rules : list, optional
        The rule set, as :meth:`muscadet.ObjFlow.add_rules` takes it.
    rules_name : str, optional
        Name of the declared rule set. Defaults to ``"transform"``.
    """

    DECLARATION_KEYS = (
        "flows_in",
        "flows_out",
        "controls",
        "rules",
        "rules_name",
    ) + ALLOCATION_KEYS

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        for entry in flow_declarations(kwargs.get("flows_in")):
            self.add_flow_continuous_in(**entry)

        allocation = allocation_params(kwargs)
        for entry in flow_declarations(kwargs.get("flows_out")):
            self.add_flow_continuous_out(**dict(allocation, **entry))

        for entry in flow_declarations(kwargs.get("controls"), logic="and"):
            self.add_flow_in(**entry)

        rules = kwargs.get("rules")
        if rules:
            self.add_rules(name=kwargs.get("rules_name", "transform"), rules=rules)


#: Which sides a capacity component carries its held flows on, and the capacity
#: side each implies.
CAPACITY_PORTS = {"both": "out", "in": "in", "out": "out"}


class CapacityContinuous(ContinuousComponent):
    """A volume held over one or more continuous flows.

    Wraps the capacity declaration on a component of its own. Several
    constituents may share ONE volume: each carries its own ``weight``, the
    volume a unit of it occupies, and a draw on the whole is composed at the
    constituents' raw-quantity share (R35) -- a mixture cannot serve a pure
    constituent.

    ``ports`` says which sides the held flows are carried on, which is what
    decides whether the volume can accumulate:

    * ``"both"`` -- a **buffer**. Each flow is declared on both sides, so what
      the volume does not hold back it transfers unchanged. What it takes in is
      what is asked of it downstream, so it smooths a shortage rather than
      stocking up -- unless a ``fill_rate`` is declared, in which case it also
      claims that rate for itself and accumulates whatever its producer delivers
      beyond what its consumers draw, until it is full (R36). The claim does not
      depend on anything being wired downstream, so this is also the shape of a
      tank at the END of a chain: with its own output connected to nothing, the
      claim is the whole of what it asks for and what arrives stays in the
      volume.
    * ``"in"`` -- an **accumulator**. Inputs only: it claims a declared
      ``demand`` of its own rather than one mapped from a downstream, and once
      at its volume it accepts only what leaves it, so the demand it publishes
      upstream collapses and its producer delivers less (R7, AE11). Note that
      the production sweep fills an input capacity from the RULES that draw on
      it, and an accumulator declares none: its transit is written by whatever
      drives the model, the way ``Capacity.set_inflow`` is used elsewhere.
    * ``"out"`` -- a **reservoir**. Outputs only: it starts stocked and serves
      what is drawn from it, at the raw composition of what it holds.

    Parameters
    ----------
    flow : str or dict, optional
        Short form for the single-flow case.
    flows : str or dict or list, optional
        General form: names, or mappings carrying ``name``, ``weight`` and,
        for an accumulator, ``demand``.
    capacity : float
        The volume the held flows SHARE.
    ports : str, optional
        ``"both"`` (default), ``"in"`` or ``"out"``.
    side : str, optional
        Side the capacity itself sits on. Defaults to the one ``ports``
        implies.
    demand : float, optional
        Demand claimed on every input, for an accumulator. Defaults to 0.
    fill_rate : float, optional
        Rate the volume claims for ITSELF while it has room, on top of the
        demand crossing it (R36). Defaults to 0 -- a pure buffer, which asks
        for exactly what passes through it. ``math.inf`` means "whatever the
        producer can deliver": a tank connected to a pump fills at the pump's
        rate, since a delivery is already the lesser of production and demand.
    content_init : dict, optional
        Initial raw quantity per held flow.
    capacity_name : str, optional
        Name of the declared capacity, and therefore of the measurement channel
        a sensor observes it through. Defaults to ``"capacity"``.
    allocation, allocation_shares, allocation_priorities, allocation_fun
        Forwarded to every output flow.
    """

    DECLARATION_KEYS = (
        "flow",
        "flows",
        "capacity",
        "ports",
        "side",
        "demand",
        "fill_rate",
        "content_init",
        "capacity_name",
    ) + ALLOCATION_KEYS

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        ports = kwargs.get("ports", "both")
        if ports not in CAPACITY_PORTS:
            raise ValueError(
                f"Unknown capacity ports '{ports}': use 'both', 'in' or 'out'"
            )

        entries = flow_declarations(
            kwargs.get("flows", kwargs.get("flow")),
            default_name=DEFAULT_FLOW,
            demand=kwargs.get("demand", 0.0),
        )
        allocation = allocation_params(kwargs)

        for entry in entries:
            if ports in ("both", "in"):
                self.add_flow_continuous_in(
                    name=entry["name"],
                    var_demand_default=float(entry["demand"]),
                )
            if ports in ("both", "out"):
                self.add_flow_continuous_out(name=entry["name"], **allocation)

        self.add_capacity(
            name=kwargs.get("capacity_name", "capacity"),
            flows=[
                {key: value for key, value in entry.items() if key != "demand"}
                for entry in entries
            ],
            capacity=kwargs.get("capacity"),
            side=kwargs.get("side", CAPACITY_PORTS[ports]),
            content_init=kwargs.get("content_init"),
            fill_rate=float(kwargs.get("fill_rate", 0.0)),
        )


class ConsumerContinuous(ContinuousComponent):
    """A continuous input publishing a declared demand.

    A pure consumer has no output to map a demand back from, so what it asks
    for is what it was declared with, and it takes whatever allocation gives
    it.

    Parameters
    ----------
    flow : str or dict, optional
        Name of the continuous input. Defaults to ``"q"``.
    demand : float, optional
        What it asks for. Defaults to 0.
    flows : str or dict or list, optional
        Several inputs at once, each carrying its own ``demand``.
    """

    # No allocation keys: a pure consumer declares no output to split.
    DECLARATION_KEYS = ("flow", "flows", "demand")

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        entries = flow_declarations(
            kwargs.get("flows", kwargs.get("flow")),
            default_name=DEFAULT_FLOW,
            demand=kwargs.get("demand", 0.0),
        )

        for entry in entries:
            self.add_flow_continuous_in(
                name=entry["name"],
                var_demand_default=float(entry["demand"]),
            )


class SensorContinuous(ContinuousComponent):
    """A capacity level read over a measurement link, driving a control port.

    The only sanctioned way to gate production on a level (R29, F4): the sensor
    observes a capacity, its watched threshold fires AT the crossing, and the
    discrete output it drives is what a producing component's rule guard reads.

    **The deadband.** ``activate`` is the level at which the control output
    comes on, ``release`` the level at which it goes off. Between them the
    output holds whatever it already was, so a loop closed through the sensor
    settles at the band edges instead of chattering around a single value.
    Declaring no ``release`` makes the two coincide: the degenerate single
    threshold, where the band is empty and the output switches at ``activate``.

    ``direction`` says which way the level activates it: ``"above"`` for a
    high-level detector, in which case ``release`` must not exceed ``activate``;
    ``"below"`` for a low-level one, in which case it must not fall below it.

    Everything here is declaration. The two band edges are comparison operands
    on discrete outputs -- exactly the ``{name, op, value}`` vocabulary a rule
    guard uses -- and the memory that holds the output inside the band is a
    two-state automaton clamping its availability. No Python reads the level.

    Parameters
    ----------
    **Redundancy (R37).** ``combine`` makes the observed channel a many-to-one
    one: several instruments publish a reading, and the sensor votes on them.
    ``"median"`` is what redundancy exists for -- with an odd number of
    publishers a single stuck or wild reading cannot move it, while a mean is
    dragged by its full deviation over the count. Declaring nothing keeps the
    channel capped at one publisher, exactly as before.

    ``publish`` makes this sensor one of those instruments: it republishes what
    it reads under a channel name of its own, multiplied by the public
    ``{publish}_level_gain``. That gain is the endpoint a failure mode clamps --
    0 for a dead instrument, 5 for a wild one -- and it is what lets a redundant
    set be exercised at all.

    Parameters
    ----------
    measurement : str, optional
        Name of the observed capacity, which is also the measurement channel
        name: wire it with ``system.connect(holder, f"{measurement}_level_out",
        sensor, f"{measurement}_level_in")``. Defaults to ``"capacity"``.
    control : str, optional
        Name of the discrete control output. Defaults to ``"control"``.
    activate : float
        Level at which the control output comes on. A sensor that only
        republishes may leave it out, and then declares no control port at all.
    release : float, optional
        Level at which it goes off. Defaults to ``activate``.
    direction : str, optional
        ``"above"`` (default) or ``"below"``.
    level_default : float, optional
        What the measurement reads while unconnected.
    combine : str, optional
        How several publishers' readings reduce to one (R37): ``"median"``,
        ``"mean"``, ``"sum"``, ``"min"``, ``"max"``. Declaring it is what lifts
        the one-publisher cap.
    combine_fun : callable, optional
        ``f(values) -> float``, used in preference to ``combine``.
    publish : str or bool, optional
        Republish the observed reading under this channel name, so this sensor
        can itself be voted on. ``True`` republishes under ``measurement``.
    gain : float, optional
        Initial value of ``{publish}_level_gain``. Defaults to 1.

    Raises
    ------
    ValueError
        If ``direction`` is neither ``"above"`` nor ``"below"``, or if the
        deadband is declared the wrong way round for that direction, or if the
        sensor neither thresholds nor publishes, or if a declaration key is not
        one this component reads.
    """

    # No allocation keys: the sensor's outputs are discrete control ports.
    DECLARATION_KEYS = (
        "measurement",
        "control",
        "activate",
        "release",
        "direction",
        "level_default",
        "combine",
        "combine_fun",
        "publish",
        "gain",
    )

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        measurement = kwargs.get("measurement", "capacity")
        control = kwargs.get("control", "control")
        direction = kwargs.get("direction", "above")

        publish = kwargs.get("publish", None)
        if publish is True:
            publish = measurement
        elif publish is False:
            publish = None

        if "activate" not in kwargs and publish is None:
            raise ValueError(
                "A sensor must declare the level it activates at, or a "
                "'publish' channel to republish its reading on"
            )

        if "activate" not in kwargs:
            # A pure republisher: it reads and reports, and gates nothing.
            self.add_measurement_in(
                name=measurement, **self._measurement_params(kwargs)
            )
            self.add_measurement_out(
                name=publish,
                source=measurement,
                gain_default=float(kwargs.get("gain", 1.0)),
            )
            return

        if "activate" not in kwargs:
            raise ValueError("A sensor must declare the level it activates at")
        activate = float(kwargs["activate"])
        release = float(kwargs.get("release", activate))

        # The band edges. The release edge is deliberately STRICT, so the
        # degenerate case -- the two thresholds coinciding -- leaves the two
        # comparisons mutually exclusive and the output switches exactly at the
        # single threshold, with no level at which both edges hold at once.
        if direction == "above":
            if release > activate:
                raise ValueError(
                    f"Sensor deadband declared the wrong way round: a sensor "
                    f"activating above {activate} releases at or below it, "
                    f"not at {release}"
                )
            activate_op, release_op = ">=", "<"
        elif direction == "below":
            if release < activate:
                raise ValueError(
                    f"Sensor deadband declared the wrong way round: a sensor "
                    f"activating below {activate} releases at or above it, "
                    f"not at {release}"
                )
            activate_op, release_op = "<=", ">"
        else:
            raise ValueError(
                f"Unknown sensor direction '{direction}': use 'above' or 'below'"
            )

        self.add_measurement_in(name=measurement, **self._measurement_params(kwargs))

        if publish is not None:
            self.add_measurement_out(
                name=publish,
                source=measurement,
                gain_default=float(kwargs.get("gain", 1.0)),
            )

        for suffix, op, value in (
            ("activate", activate_op, activate),
            ("release", release_op, release),
        ):
            self.add_flow(
                dict(
                    cls="FlowDiscreteOut",
                    name=f"{control}_{suffix}",
                    var_prod_cond=[{"name": measurement, "op": op, "value": value}],
                )
            )

        # The control port itself. It produces unconditionally and its
        # AVAILABILITY is what the band clamps, so the state the level left it
        # in survives inside the band.
        self.add_flow(
            dict(
                cls="FlowDiscreteOut",
                name=control,
                var_prod_default=True,
                var_fed_available_out_init=False,
            )
        )

    @staticmethod
    def _measurement_params(kwargs):
        """The measurement-channel parameters this declaration carries (R37).

        Only the keys actually declared are forwarded, so a sensor that says
        nothing about combination keeps the single-publisher channel it has
        always had -- byte-identically, ``combine`` never even reaching the
        model.
        """
        params = {}
        for source, target in (
            ("level_default", "level_default"),
            ("combine", "combine"),
            ("combine_fun", "combine_fun"),
        ):
            if source in kwargs:
                params[target] = kwargs[source]
        return params

    def set_flows(self, **kwargs):
        # The band automaton reads the two edge outputs' state variables, so it
        # is declared once those variables exist -- which is what set_flows
        # creates.
        super().set_flows(**kwargs)

        # A pure republisher declares no control port, hence no band automaton.
        if "activate" not in kwargs:
            return

        control = kwargs.get("control", "control")

        # A mode effect resolves its target by an UNANCHORED regex over the
        # component's variable basenames, so the pattern is anchored here: the
        # band clamps the control port's own availability and must never reach
        # the two edge outputs sitting next to it.
        available = rf"^{re.escape(control)}_fed_available_out$"

        self.add_atm2states(
            name=f"{control}_band",
            st1="released",
            st2="activated",
            init_st2=False,
            cond_occ_12=f"{control}_activate_fed_out",
            cond_occ_21=f"{control}_release_fed_out",
            effects_12=[(available, True)],
            effects_21=[(available, False)],
        )


class ExchangeContinuous(ContinuousComponent):
    """A metered conduit: what crosses is a quantity a transfer law computes.

    The seventh shape, and the one that gave the transfer pair its name. Every
    component above moves a quantity because somebody ASKED for it. This one
    moves a quantity because a gradient makes it move: it carries one flow in
    and out, and a pair naming that flow twice meters the transit, so what
    crosses the component is exactly what the equation computed.

    That is what a wall between a tank and its environment is, and what a
    membrane between two half-cells is. Neither is a consumer, and shaping one
    as a consumer was measured to get exactly one property right: as an
    identity transfer the exchange conserves but its rate is a proportion of
    what arrived; as a source the rate is exact but the quantity appears from
    nowhere.

    The potentials it reads are declared, not computed here. A measurement
    channel publishes each constituent of a volume, so a component dividing two
    of them publishes an intensive property already: this one reads that
    property rather than deriving it, which is why it carries no chemistry and
    no thermodynamics in its parameters.

    Parameters
    ----------
    flow : str, optional
        The flow whose transit is metered, carried in and out. Defaults to
        ``"q"``.
    measurements : list of str, optional
        Measurement channels to open, so a potential can name one. Wire each
        with ``system.connect(holder, f"{name}_level_out", exchange,
        f"{name}_level_in")``.
    transfer : muscadet.Transfer or dict, optional
        The equation, given whole. Use it for a law the shipped shape does not
        cover. Mutually exclusive with the three parameters below.
    conductance : float, optional
        ``G`` of ``Q = G x (potential_a - potential_b)``.
    potential_a, potential_b : dict or float, optional
        The two potentials, in the forms :func:`muscadet.resolve_operand`
        accepts: a number, ``{"const": x}``, or ``{"measurement": name}``
        optionally narrowed to one constituent with ``"flow"``.
    transfer_name : str, optional
        Name of the declared pair, and therefore of its two published
        magnitudes ``{name}_requested`` / ``{name}_moved``. Defaults to
        ``"transfer"``.

    Raises
    ------
    ValueError
        If neither ``transfer`` nor a conduction triple is declared, if both
        are, or if a declaration key is not one this component reads.
    """

    DECLARATION_KEYS = (
        "flow",
        "measurements",
        "transfer",
        "conductance",
        "potential_a",
        "potential_b",
        "transfer_name",
    )

    #: The three keys that spell a conduction law inline.
    CONDUCTION_KEYS = ("conductance", "potential_a", "potential_b")

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        flow = kwargs.get("flow", DEFAULT_FLOW)
        self.add_flow_continuous_in(name=flow)
        self.add_flow_continuous_out(name=flow)

        for channel in kwargs.get("measurements") or []:
            # A bare name opens a total-only channel; a mapping opens the
            # constituents a per-constituent potential needs. Without the
            # second form the `{"measurement": n, "flow": f}` operand this
            # component's own docstring advertises can never resolve, and it
            # fails from inside a PDMP equation rather than at declaration.
            if isinstance(channel, dict):
                self.add_measurement_in(**channel)
            else:
                self.add_measurement_in(name=channel)

        self.add_transfer(
            name=kwargs.get("transfer_name", "transfer"),
            flows=[flow, flow],
            equation=self.declared_equation(kwargs),
        )

    def declared_equation(self, kwargs):
        """The equation, given whole or spelled as a conduction law.

        The two spellings are mutually exclusive rather than layered: a
        component carrying both would silently honour one of them, and which
        one is exactly the kind of thing a reader has to run the model to
        discover.
        """
        inline = [key for key in self.CONDUCTION_KEYS if key in kwargs]

        if "transfer" in kwargs:
            if inline:
                raise ValueError(
                    f"Object {self.name()}: declares both a whole 'transfer' "
                    f"and the conduction parameters {', '.join(inline)}. "
                    "Declare one or the other"
                )
            return kwargs["transfer"]

        if not inline:
            raise ValueError(
                f"Object {self.name()}: declares no transfer law. Give a whole "
                "'transfer', or the conduction triple 'conductance', "
                "'potential_a' and 'potential_b'"
            )

        return muscadet.ConductiveTransfer(
            conductance=kwargs.get("conductance"),
            potential_a=kwargs.get("potential_a"),
            potential_b=kwargs.get("potential_b"),
            name=kwargs.get("transfer_name", "transfer"),
        )
