"""The continuous components MUSCADET ships (R32).

Five domain-neutral shapes, and nothing else: a **source** holding a declared
rate, a **transformer** taking its rules as parameters, a **capacity** wrapping
the volume declaration, a **consumer** publishing a declared demand, and a
**sensor** reading a capacity level and driving a discrete control output.

Only shapes that appeared TWICE outside the library ship here. An electrolyser
with a membrane leak percentage or a battery with a start-up policy stays with
the project that needs it: these five carry no chemistry, no electricity and no
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

#: Allocation parameters a continuous output accepts (R16, R17), forwarded
#: verbatim so a caller declares a policy without subclassing.
ALLOCATION_KEYS = (
    "allocation",
    "allocation_shares",
    "allocation_priorities",
    "allocation_fun",
)


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


class SourceContinuous(muscadet.ObjFlow):
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
    allocation, allocation_shares, allocation_priorities, allocation_fun
        Forwarded to the output flow, where an insufficient supply is split.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        flow = kwargs.get("flow", DEFAULT_FLOW)
        rate = float(kwargs.get("rate", 0.0))
        control = kwargs.get("control")

        self.add_flow_continuous_out(
            name=flow,
            var_fed_default=rate,
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


class TransformerContinuous(muscadet.ObjFlow):
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
        component apply to every one of them.
    controls : str or dict or list, optional
        Discrete inputs the guards read, ``logic`` defaulting to ``"and"``.
    rules : list, optional
        The rule set, as :meth:`muscadet.ObjFlow.add_rules` takes it.
    rules_name : str, optional
        Name of the declared rule set. Defaults to ``"transform"``.
    """

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


class CapacityContinuous(muscadet.ObjFlow):
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
      beyond what its consumers draw, until it is full (R36).
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


class ConsumerContinuous(muscadet.ObjFlow):
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


class SensorContinuous(muscadet.ObjFlow):
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
    measurement : str, optional
        Name of the observed capacity, which is also the measurement channel
        name: wire it with ``system.connect(holder, f"{measurement}_level_out",
        sensor, f"{measurement}_level_in")``. Defaults to ``"capacity"``.
    control : str, optional
        Name of the discrete control output. Defaults to ``"control"``.
    activate : float
        Level at which the control output comes on.
    release : float, optional
        Level at which it goes off. Defaults to ``activate``.
    direction : str, optional
        ``"above"`` (default) or ``"below"``.
    level_default : float, optional
        What the measurement reads while unconnected.

    Raises
    ------
    ValueError
        If ``direction`` is neither ``"above"`` nor ``"below"``, or if the
        deadband is declared the wrong way round for that direction.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        measurement = kwargs.get("measurement", "capacity")
        control = kwargs.get("control", "control")
        direction = kwargs.get("direction", "above")

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

        measurement_params = {}
        if "level_default" in kwargs:
            measurement_params["level_default"] = kwargs["level_default"]
        self.add_measurement_in(name=measurement, **measurement_params)

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

    def set_flows(self, **kwargs):
        # The band automaton reads the two edge outputs' state variables, so it
        # is declared once those variables exist -- which is what set_flows
        # creates.
        super().set_flows(**kwargs)

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
