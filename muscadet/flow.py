import Pycatshoo as pyc
import typing
import pydantic
from colored import fg, attr

import cod3s
from .common import fresh_instant_occ_law, get_pyc_type


def _prod_cond_matrix_entry(matrix, i, j, default=None):
    """Entry ``(i, j)`` of a matrix aligned index-for-index with ``var_prod_cond``.

    A missing entry -- a shorter row, a shorter matrix, the EMPTY matrix that
    is every such matrix's default -- yields ``default``, so a partial matrix is
    safe and an empty one means "no operand carries this".
    """
    if i < len(matrix) and j < len(matrix[i]):
        return matrix[i][j]
    return default


def _prod_cond_quantity_reader(source):
    """Zero-arg reader of the quantity a COMPARISON operand reads.

    Every flow answers :meth:`FlowModel.live_value`; a measurement link is not
    a flow and answers ``get_level``, the level of the capacity it observes.
    """
    reader = getattr(source, "live_value", None)
    return reader if reader is not None else source.get_level


def _prod_cond_state_reader(source):
    """Reader of a plain operand: is ``source`` fed."""
    return lambda: source.var_fed.value()


def _prod_cond_negated_reader(source):
    """Reader of a negated operand: is ``source`` NOT fed."""
    return lambda: not source.var_fed.value()


def _prod_cond_compare_reader(source, comparator, threshold):
    """Reader of a comparison operand: what ``source`` carries, against
    ``threshold`` (R22).

    The quantity is read LIVE and not from a mirror refreshed between
    integration steps, for the very reason a rule guard is (R12): the solver
    root-finds a crossing, and a mirror would lag one step behind the value
    being watched.
    """
    read = _prod_cond_quantity_reader(source)
    return lambda: bool(comparator(float(read()), threshold))


def _prod_cond_threshold_condition(read, comparator, threshold, above):
    """Condition of one crossing transition of a comparison operand.

    ``above`` selects the direction: the transition INTO the "above" state
    fires when the comparison holds, the one back out of it when it stops
    holding.
    """

    def condition():
        holds = bool(comparator(float(read()), threshold))
        return holds if above else (not holds)

    return condition


class FlowModel(cod3s.ObjCOD3S):

    name: str = pydantic.Field(..., description="Flow name")

    var_type: str = pydantic.Field("bool", description="Flow type")

    var_fed_default: typing.Any = pydantic.Field(None, description="Flow default value")

    var_fed: typing.Any = pydantic.Field(None, description="Component flow fed")

    var_fed_available: typing.Any = pydantic.Field(
        None, description="Flow available fed"
    )

    sm_flow_fed_fun: typing.Any = pydantic.Field(
        None, description="set flow sensitive method"
    )

    sm_flow_fed_name: typing.Any = pydantic.Field(
        None, description="set flow sensitive method"
    )
    component_authorized: typing.Optional[typing.List[typing.Dict[str, str]]] = (
        pydantic.Field(
            [{"class_name_bkd": ".*"}],
            description="List of authorized components to be connected",
        )
    )

    combine: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description=(
            "Declared ONLY so it can be refused by name (R37). A combination "
            "policy belongs to a measurement channel and never to a flow; "
            "pydantic ignores unknown keys, so without this field the key would "
            "be swallowed and the flow would go on summing while the model read "
            "as if it took a median."
        ),
    )

    combine_fun: typing.Any = pydantic.Field(
        None,
        exclude=True,
        repr=False,
        description="Refused by name for the same reason as ``combine`` (R37).",
    )

    @pydantic.model_validator(mode="after")
    def check_combine_is_not_a_flow_policy(self):
        """Refuse a combination policy on a FLOW, at declaration time (R37).

        A continuous input is the SUM of its connections, permanently: it
        carries a conserved quantity, and the median of three pipes delivering
        water creates or destroys matter. A discrete input already votes, with
        an integer ``logic`` -- ``logic=2`` over three connections is a
        2-out-of-3 majority.

        This is what makes the restriction unreachable rather than merely
        documented. Pydantic's default is to IGNORE an unknown key, so a
        ``combine="median"`` written on a flow would be accepted, dropped, and
        the flow would keep summing: the model would read as a vote and behave
        as a sum, which is the worst of the three possible outcomes.
        """
        if self.combine is None and self.combine_fun is None:
            return self

        raise ValueError(
            f"Flow {self.name}: a combination policy cannot be declared on a "
            "flow (R37). A continuous flow carries a CONSERVED quantity and is "
            "the sum of its connections; taking a mean or a median of it would "
            "create or destroy matter. Combine readings on a MEASUREMENT "
            "channel instead -- add_measurement_in(name, combine='median') -- "
            "or, to vote on discrete inputs, declare an integer logic: "
            "add_flow_in(name, logic=2) is a 2-out-of-3 majority"
        )

    @classmethod
    def get_clsname(basecls, **specs):
        port_name = specs.pop("port")
        if port_name == "io":
            port_name = "IO"
        else:
            port_name = port_name.capitalize()
        clsname = f"Flow{port_name}"
        return clsname

    def initial_fed_value(self):
        """The value ``{name}_fed_{port}`` is CREATED at.

        The declared default, and the override point for a flow whose value at
        instant 0 is not simply that -- a continuous output carrying a time
        profile, whose production at 0 is the default scaled by the profile
        there. It is the engine's init value, restored at the start of every
        Monte Carlo sequence, so it must be a pure function of the declaration.
        """
        return self.var_fed_default

    def add_variables(self, comp, port, **kwargs):

        py_type, pyc_type = get_pyc_type(self.var_type)

        self.var_fed_default = (
            py_type() if self.var_fed_default is None else self.var_fed_default
        )

        self.var_fed = comp.addVariable(
            f"{self.name}_fed_{port}", pyc_type, py_type(self.initial_fed_value())
        )

    def add_automata(self, comp):
        pass

    def live_value(self):
        """The quantity this flow carries right now.

        The single reading every COMPARISON operand goes through, whichever
        direction of the discrete/continuous interoperation declares it: a rule
        guard reading a flow (R21), a discrete production condition reading a
        continuous one (R22). Overridden where ``var_fed`` is a mirror rather
        than the value itself.
        """
        return self.var_fed.value()

    @property
    def is_continuous(self) -> bool:
        """False on the discrete family, True on the continuous one.

        What tells a comparison operand whether its crossing must be WATCHED by
        the solver: a discrete flow announces its own change through
        ``var_fed``, a continuous one moves between integration steps and
        announces nothing.
        """
        return False

    def get_flow_type_color(self) -> str:
        """Return the color formatting for flow type. Can be overridden in subclasses."""
        return f"{attr('bold')}{fg('white')}"

    @classmethod
    def get_format_class_name(cls) -> str:
        """Return the color formatting for this flow class name. Can be overridden in subclasses."""
        return f"{attr('bold')}{fg('white')}"

    def format_boolean_value(self, value) -> str:
        """Format boolean values with appropriate colors."""
        if isinstance(value, bool):
            if value:
                return f"{fg('green')}{value}{attr('reset')}"
            else:
                return f"{fg('yellow')}{value}{attr('reset')}"
        return str(value)

    def get_var_fed_available(self):
        return self.var_fed_available.value()

    def format_var_fed_available(self, is_available) -> str:
        """Format var_fed_available value with appropriate colors. Can be overridden in subclasses."""
        availability_symbol = (
            f"{fg('green')}✓{attr('reset')}"
            if is_available
            else f"{fg('red')}✗{attr('reset')}"
        )
        return availability_symbol

    def __str__(self) -> str:
        flow_type = self.__class__.__name__

        # Get values safely, handling cases where variables might not be initialized

        var_fed = self.var_fed.value()
        var_fed_default = self.var_fed_default

        # Format values with appropriate colors
        formatted_var_fed = self.format_boolean_value(var_fed)
        formatted_var_fed_default = self.format_boolean_value(var_fed_default)
        availability_symbol = self.format_var_fed_available(
            self.get_var_fed_available()
        )

        lines = [
            f"{self.get_flow_type_color()}{flow_type}{attr('reset')} {fg('blue')}{self.name}{attr('reset')}",
            f"  {fg('white')}Type{attr('reset')}: {self.var_type}",
            f"  {fg('white')}Fed{attr('reset')}: {formatted_var_fed}",
            f"  {fg('white')}Default{attr('reset')}: {formatted_var_fed_default}",
            f"  {fg('white')}Available{attr('reset')}: {availability_symbol}",
        ]
        return "\n".join(lines)

    def __repr__(self) -> str:
        flow_type = self.__class__.__name__

        # Get values safely, handling cases where variables might not be initialized
        var_fed = self.var_fed.value() if self.var_fed else "N/A"
        var_fed_default = (
            self.var_fed_default if hasattr(self, "var_fed_default") else "N/A"
        )

        # Format values with appropriate colors
        formatted_var_fed = self.format_boolean_value(var_fed)
        formatted_var_fed_default = self.format_boolean_value(var_fed_default)
        availability_symbol = self.format_var_fed_available(
            self.get_var_fed_available()
        )

        return (
            f"{self.get_flow_type_color()}{flow_type}{attr('reset')} "
            f"{fg('blue')}{self.name}{attr('reset')} "
            f"[{self.var_type}] = {formatted_var_fed} "
            f"[{formatted_var_fed_default}] "
            f"{availability_symbol}"
        )


class FlowDiscrete(FlowModel):
    """Shared base of the discrete flow family.

    The mirror of :class:`muscadet.flow_continuous.FlowContinuous`: it exists so
    that a discrete flow is told apart by a positive
    ``isinstance(flow, FlowDiscrete)`` test rather than by negating the
    continuous one. Negation is what mis-labels a future third family -- every
    flow that is not continuous is not thereby discrete.

    A pure marker: it declares no field and no behaviour of its own. It is
    inserted between :class:`FlowModel` and the two discrete leaves, so every
    ``isinstance`` relation that held before it existed still holds -- including
    the legacy names, which sit further down the same chain
    (``FlowModel -> FlowDiscrete -> FlowDiscreteOut -> FlowOut -> ...``).
    """


class FlowDiscreteIn(FlowDiscrete):

    var_in: typing.Any = pydantic.Field(
        None, description="Reference to collect external flow connections"
    )

    var_in_default: typing.Any = pydantic.Field(
        False, description="Flow input value when not connected"
    )

    var_available_in_default: typing.Any = pydantic.Field(
        True, description="Flow available input value when not connected"
    )

    logic: typing.Union[str, int] = pydantic.Field(
        "or", description="Flow input logic: 'and', 'or', or int k (at-least-k)"
    )

    def get_flow_type_color(self) -> str:
        """Return the color formatting for FlowIn type in orange."""
        return f"{attr('bold')}{fg('orange_1')}"

    @classmethod
    def get_format_class_name(cls) -> str:
        """Return the color formatting for FlowIn class name."""
        return f"{fg('orange_1')}"

    def get_var_fed_available(self):
        if self.logic == "and":
            return self.var_fed_available.andValue(self.var_available_in_default)
        elif self.logic == "or":
            return self.var_fed_available.orValue(self.var_available_in_default)
        elif isinstance(self.logic, int):
            if self.var_fed_available.nbCnx() == 0:
                return self.var_available_in_default
            return self.var_fed_available.sumValue(0) >= self.logic
        else:
            raise ValueError("FlowIn logic must be 'and', 'or', or a positive integer")

    def get_logic_color(self) -> str:
        """Return the color formatting for logic type."""
        if self.logic == "and":
            return f"{fg('magenta')}{self.logic}{attr('reset')}"
        elif self.logic == "or":
            return f"{fg('cyan')}{self.logic}{attr('reset')}"
        elif isinstance(self.logic, int):
            return f"{fg('yellow')}>={self.logic}{attr('reset')}"
        else:
            return f"{fg('red')}{self.logic}{attr('reset')}"

    def _get_var_in_value(self):
        """Compute the aggregated input value based on logic type."""
        if self.logic == "and":
            return self.var_in.andValue(self.var_in_default)
        elif self.logic == "or":
            return self.var_in.orValue(self.var_in_default)
        elif isinstance(self.logic, int):
            if self.var_in.nbCnx() == 0:
                return self.var_in_default
            return self.var_in.sumValue(0) >= self.logic
        else:
            raise ValueError("FlowIn logic must be 'and', 'or', or a positive integer")

    def __repr__(self) -> str:
        base_str = super().__repr__()

        # Get var_in value safely
        try:
            var_in_value = self._get_var_in_value()
        except:
            var_in_value = "N/A"

        # Format var_in value with appropriate colors
        formatted_var_in = self.format_boolean_value(var_in_value)

        return f"{base_str} | in ({self.get_logic_color()}): {formatted_var_in}"

    def __str__(self) -> str:
        base_repr = super().__str__()

        # Get var_in value safely
        try:
            var_in_value = self._get_var_in_value()
        except:
            var_in_value = "N/A"

        # Format var_in value with appropriate colors
        formatted_var_in = self.format_boolean_value(var_in_value)

        # Add var_in and logic information to the base representation
        additional_lines = [
            f"  {fg('white')}Input{attr('reset')}: {formatted_var_in}",
            f"  {fg('white')}Logic{attr('reset')}: {self.get_logic_color()}",
        ]
        return f"{base_repr}\n" + "\n".join(additional_lines)

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, port="in", **kwargs)

        self.var_in = comp.addReference(f"{self.name}_in")

        self.var_fed_available = comp.addReference(f"{self.name}_fed_available_in")

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_in")
        comp.addMessageBoxImport(f"{self.name}_in", self.var_in, self.name)

        comp.addMessageBox(f"{self.name}_available_in")
        comp.addMessageBoxImport(
            f"{self.name}_available_in",
            self.var_fed_available,
            f"{self.name}_available",
        )

    def create_sensitive_set_flow_fed_in(self):
        # Reminder the value pass in andValue and orValue is
        # the returned value in the case of no connection

        if self.logic == "and":

            def sensitive_set_flow_template():
                self.var_fed.setValue(
                    self.var_in.andValue(self.var_in_default)
                    and self.var_fed_available.andValue(self.var_available_in_default)
                )

        elif self.logic == "or":

            def sensitive_set_flow_template():
                self.var_fed.setValue(
                    self.var_in.orValue(self.var_in_default)
                    and self.var_fed_available.orValue(self.var_available_in_default)
                )

        elif isinstance(self.logic, int):
            k = self.logic

            def sensitive_set_flow_template():
                if self.var_in.nbCnx() == 0:
                    in_ok = self.var_in_default
                else:
                    in_ok = self.var_in.sumValue(0) >= k

                if self.var_fed_available.nbCnx() == 0:
                    avail_ok = self.var_available_in_default
                else:
                    avail_ok = self.var_fed_available.sumValue(0) >= k

                self.var_fed.setValue(in_ok and avail_ok)

        else:
            raise ValueError("FlowIn logic must be 'and', 'or', or a positive integer")

        return sensitive_set_flow_template

    def update_sensitive_methods(self, comp):
        self.sm_flow_fed_fun = self.create_sensitive_set_flow_fed_in()
        self.sm_flow_fed_name = f"set_{self.name}_fed_in"
        self.var_in.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        self.var_fed_available.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun
        )

        comp.addStartMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)


class FlowIn(FlowDiscreteIn):
    """Legacy name of :class:`FlowDiscreteIn`.

    Kept permanently as a real subclass (never an assignment alias) so that
    instances built through it report ``FlowIn`` as their runtime class name.
    """

    pass


class FlowDiscreteOut(FlowDiscrete):

    # --- POINT À TRANCHER (2026-06-15) : var_is_active vs var_fed_available_out_init ---
    # var_is_active et var_fed_available sont deux portes booléennes ET sur var_fed
    # (var_fed = var_prod AND var_is_active AND var_fed_available, cf.
    # create_sensitive_set_flow_fed_out). Elles semblent redondantes, à UNE
    # différence près : var_fed_available est EXPORTÉE sur le canal availability
    # (MessageBox `{name}_available_out`, lue en aval par FlowIn.var_fed_available,
    # les portes logiques en mode check_fed=False et la viz) ; var_is_active est
    # purement LOCALE (n'intervient que dans la formule var_fed).
    #
    # Usage côté plateforme cod3s (fonction de service, prototype 2026-06-15) :
    # un flux dormant par défaut (activé par un mode de défaillance). Implémenté
    # via var_is_active_default=False (porte locale → l'aval reste "disponible
    # mais non alimenté"). Décision métier prise : il serait acceptable que le
    # flux dormant apparaisse plutôt "indisponible" en aval — ce qui plaiderait
    # pour introduire un `var_fed_available_out_init` (init configurable de
    # var_fed_available, aujourd'hui câblé en dur à True) et déprécier
    # var_is_active. NON FAIT pour l'instant : on conserve var_is_active.
    # À trancher : consolider sur var_fed_available_out_init (sémantique
    # "indisponible") OU garder la séparation availability/activation.
    # ----------------------------------------------------------------------------
    var_is_active: typing.Any = pydantic.Field(
        None, description="Indicating if the flow out is active or not"
    )
    var_is_active_default: bool = pydantic.Field(
        True, description="Indicating the default activation status"
    )

    # Configurable init of var_fed_available (the `{name}_fed_available_out`
    # availability gate). Historically hard-wired to True ; exposed here so a
    # flow can start UNAVAILABLE (dormant) and be woken by an effect setting
    # var_fed_available_out True — orthogonally to prod_cond. Default True =
    # byte-identical legacy behaviour. cf. the "POINT À TRANCHER" note above.
    #
    # NOTE on "reinitialized": var_fed_available is normally reset to this init
    # value at EVERY PyCATSHOO step (setReinitialized(True)). The init is always
    # honoured at t=0 AND at the start of each Monte-Carlo sequence (the engine
    # restores declared init values between sequences regardless of the flag).
    # When var_fed_available_out_reset is False the per-step reinitialisation is
    # disabled (setReinitialized(False)), so this value is NOT re-applied within a
    # sequence — the gate then keeps whatever value it was last written to.
    var_fed_available_out_init: bool = pydantic.Field(
        True,
        description=(
            "Initial value of var_fed_available_out (at t=0 and at the start of "
            "each MC sequence). Also the per-step reinitialised value UNLESS "
            "var_fed_available_out_reset is False."
        ),
    )

    # Availability-gate reset control. When True (default) var_fed_available is
    # reinitialised to var_fed_available_out_init at every step
    # (setReinitialized(True)) — the legacy, byte-identical behaviour. When False
    # the per-step reinitialisation is disabled (setReinitialized(False)): the
    # gate is PERSISTENT and MEMORISES its last value within a sequence instead of
    # reverting to var_fed_available_out_init. Use for persistent detection /
    # persistent alarm / memorised fault. The init is still honoured at t=0 and
    # between MC sequences (no inter-sequence leak).
    #
    # WRITE-SAFETY INVARIANT (both-pulse): a gate that is not reinitialised
    # (reset=False) can no longer "fall back to rest" on its own. The ONLY safe
    # way to write a non-reinitialised gate is a PULSE (a transient state drained
    # via inst/delay(0)), applied on BOTH polarities (set AND clear). A standing
    # level clamp of either polarity coexisting with an opposite writer has no
    # fixpoint -> silent non-deterministic hang on a fraction of MC sequences.
    # cf. CLAUDE.md.
    var_fed_available_out_reset: bool = pydantic.Field(
        True,
        description=(
            "If True (default), the availability gate var_fed_available_out is "
            "reinitialised to its init at every step (setReinitialized(True)) — "
            "legacy behaviour. If False, it is not reinitialised "
            "(setReinitialized(False)) and memorises its value within a sequence "
            "(persistent). Still restored to its init at t=0 and between MC "
            "sequences. Write a non-reinitialised gate with pulses on both "
            "polarities only (see docs)."
        ),
    )

    var_prod: typing.Any = pydantic.Field(None, description="Flow production")
    var_prod_available: typing.Any = pydantic.Field(
        None, description="Indicates if the flow production condition are met"
    )
    var_prod_cond: list = pydantic.Field(
        [],
        description="Flow production condition [(C11 <BoolOpeA> C12 <BoolOpeA> ... <BoolOpeA> C1_k1) <BoolOpeB> (C21 <BoolOpeA> ... <BoolOpeA> C2_k2) <BoolOpeB> ... <BoolOpeB> (Cn1 <BoolOpeA> ... <BoolOpeA> Cn_kn)] where both <BoolOpeA> and <BoolOpeB> are boolean operators set by attribute 'var_prod_cond_inner_mode'",
    )
    var_prod_cond_negate: list = pydantic.Field(
        default_factory=list,
        description="Per-operand negation matrix aligned index-for-index with 'var_prod_cond' (list[list[bool]]): when var_prod_cond_negate[i][j] is True the j-th operand of the i-th group is evaluated as NOT(flow.var_fed) instead of flow.var_fed. An EMPTY matrix (the default) means no operand is negated -> the evaluation is byte-identical to the historical behaviour. Built index-aligned by ObjFlow.postprocess_flow_specs from the '{name, negate}' operand form; the plain string operand form yields no negation.",
    )
    var_prod_cond_compare: list = pydantic.Field(
        default_factory=list,
        description="Per-operand comparison matrix aligned index-for-index with 'var_prod_cond' (list[list[dict|None]]): when var_prod_cond_compare[i][j] is {'op': str, 'value': float} the j-th operand of the i-th group compares the LIVE quantity that operand reads against the threshold, instead of reading a boolean state (R22) -- which is how a continuous quantity conditions a discrete output. An EMPTY matrix (the default) means no operand compares -> the evaluation is byte-identical to the historical behaviour. Built index-aligned by ObjFlow.postprocess_flow_specs from the '{name, op, value}' operand form, the very vocabulary a rule guard operand uses.",
    )
    sm_prod_available_fun: typing.Any = pydantic.Field(
        None, description="Production condition sensitive method"
    )
    sm_prod_available_name: typing.Any = pydantic.Field(
        None, description="Production condition sensitive method name"
    )
    var_prod_cond_inner_mode: str = pydantic.Field(
        "or",
        description="Flow production condition expression mode: 'or' means var_prod is evaluated like [(C11 or C12 or ... or C1_k1) and (C21 or ... C2_k2) and ... and (Cn1 or ... or Cn_kn)], 'and' means evaluation like [(C11 and C12 and ... and C1_k1) or (C21 and ... and C2_k2) or ... or (Cn1 and ... and Cn_kn)]",
    )
    # var_fed_control: typing.Any = pydantic.Field(
    #     None,
    #     description="Input available control to make flow controllable by external component",
    # )

    var_prod_default: typing.Any = pydantic.Field(
        False, description="Flow production default value"
    )

    negate: bool = pydantic.Field(
        False, description="Indicates if the flow output is negated"
    )
    # var_out: typing.Any = \
    #     pydantic.Field(None, description="Flow output")
    # var_out_available: typing.Any = \
    #     pydantic.Field(None, description="Flow available out")

    # @pydantic.validator('var_prod_cond')
    # def check_var_prod_cond(cls, v):
    #     ipdb.set_trace()

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, port="out", **kwargs)

        py_type, pyc_type = get_pyc_type(self.var_type)

        self.var_fed_available = comp.addVariable(
            f"{self.name}_fed_available_out",
            pyc.TVarType.t_bool,
            self.var_fed_available_out_init,
        )
        # reset=True (default): reinitialise to init at every step (legacy
        # behaviour). reset=False: keep last value within a sequence (persistent
        # memory). Inherited by the dynamic subclasses FlowOutTempo /
        # FlowOutOnTrigger (their var_fed still ANDs var_fed_available), where
        # persistence is allowed and composable.
        self.var_fed_available.setReinitialized(self.var_fed_available_out_reset)

        self.var_is_active = comp.addVariable(
            f"{self.name}_is_active",
            pyc.TVarType.t_bool,
            self.var_is_active_default,
        )
        self.var_is_active.setReinitialized(True)

        self.var_prod_default = (
            py_type() if self.var_prod_default is None else self.var_prod_default
        )

        self.var_prod = comp.addVariable(
            f"{self.name}_prod", pyc_type, self.var_prod_default
        )

        self.var_prod_available = comp.addVariable(
            f"{self.name}_prod_available", pyc.TVarType.t_bool, self.var_prod_default
        )

        # self.var_fed_control = comp.addReference(f"{self.name}_fed_control")

        # TO DO NOT .setReinitialized(True)
        # BECAUSE var_prod_available is driven by tempo mecanisms
        # self.var_prod_available.setReinitialized(True)

        # self.var_out = \
        #     comp.addVariable(f"{self.name}_out",
        #                      pyc_type, py_type())

        # self.var_out_available = \
        #     comp.addVariable(f"{self.name}_out_available",
        #                      pyc.TVarType.t_bool, True)

    def get_flow_type_color(self) -> str:
        """Return the color formatting for FlowOut type in green."""
        return f"{attr('bold')}{fg('steel_blue_1a')}"

    @classmethod
    def get_format_class_name(cls) -> str:
        """Return the color formatting for FlowOut class name."""
        return f"{fg('steel_blue_1a')}"

    def _prod_cond_operand_label(self, flow_inner, i, j) -> str:
        """Operand name for the textual condition.

        Prefixed with '¬' when the aligned ``var_prod_cond_negate`` entry marks
        it negated, rendered as the comparison itself when the aligned
        ``var_prod_cond_compare`` entry makes it one.
        """
        compare = _prod_cond_matrix_entry(self.var_prod_cond_compare, i, j)
        if compare is not None:
            return f"{flow_inner.name} {compare['op']} {compare['value']:g}"

        neg = self.var_prod_cond_negate
        negated = i < len(neg) and j < len(neg[i]) and neg[i][j]
        return f"¬{flow_inner.name}" if negated else flow_inner.name

    def _prod_cond_text(self, ope_inner: str, ope_outer: str) -> str:
        """Render ``var_prod_cond`` as a boolean expression, honouring the
        per-operand negation matrix."""
        return ope_outer.join(
            [
                ope_inner.join(
                    [
                        self._prod_cond_operand_label(flow, i, j)
                        for j, flow in enumerate(flow_inner)
                    ]
                )
                for i, flow_inner in enumerate(self.var_prod_cond)
            ]
        )

    def __repr__(self) -> str:
        base_str = super().__repr__()

        # Get production condition information
        if self.var_prod_cond_inner_mode == "or":
            ope_inner = " or "
            ope_outer = " and "
        else:
            ope_inner = " and "
            ope_outer = " or "

        if self.var_prod_cond:
            cond_info = f"cond := {self._prod_cond_text(ope_inner, ope_outer)}"
        else:
            cond_info = "no cond"

        # Get production value safel
        prod_value = self.var_prod.value()

        formatted_prod = self.format_boolean_value(prod_value)

        return f"{base_str} | prod: {formatted_prod} | {cond_info}"

    def __str__(self) -> str:
        base_repr = super().__str__()

        # Get production condition information
        if self.var_prod_cond_inner_mode == "or":
            ope_inner = " or "
            ope_outer = " and "
        else:
            ope_inner = " and "
            ope_outer = " or "

        if self.var_prod_cond:
            cond_info = self._prod_cond_text(ope_inner, ope_outer)
        else:
            cond_info = "No conditions"

        # Get production value safely
        prod_value = self.var_prod.value()
        formatted_prod = self.format_boolean_value(prod_value)

        # Add production and condition information to the base representation
        additional_lines = [
            f"  {fg('white')}Production{attr('reset')}: {formatted_prod}",
            f"  {fg('white')}Conditions{attr('reset')}: {cond_info}",
        ]

        if self.negate:
            additional_lines.append(
                f"  {fg('white')}Negated{attr('reset')}: {fg('red')}Yes{attr('reset')}"
            )

        return f"{base_repr}\n" + "\n".join(additional_lines)

    def add_mb(self, comp, **kwargs):

        comp.addMessageBox(f"{self.name}_out")
        comp.addMessageBoxExport(f"{self.name}_out", self.var_fed, self.name)

        # comp.addMessageBox(f"{self.name}_fed_control_in")
        # comp.addMessageBoxImport(
        #     f"{self.name}_fed_control_in",
        #     self.var_fed_control,
        #     f"{self.name}_fed_control",
        # )
        comp.addMessageBox(f"{self.name}_available_out")
        comp.addMessageBoxExport(
            f"{self.name}_available_out",
            self.var_fed_available,
            f"{self.name}_available",
        )

    def create_sensitive_set_flow_fed_out(self):

        if not self.negate:

            def sensitive_set_flow_template():
                self.var_prod.setValue(self.var_prod_available.value())
                self.var_fed.setValue(
                    self.var_prod.value()
                    and self.var_is_active.value()
                    and self.var_fed_available.value()
                    #                    and self.var_fed_control.andValue(True)
                )

        else:

            def sensitive_set_flow_template():
                self.var_prod.setValue(self.var_prod_available.value())
                self.var_fed.setValue(
                    not (
                        self.var_prod.value()
                        and self.var_is_active.value()
                        and self.var_fed_available.value()
                        #                        and self.var_fed_control.andValue(True)
                    )
                )

        return sensitive_set_flow_template

    # def create_sensitive_set_flow_out(self):

    #     def sensitive_set_flow_out_template():
    #         self.var_out.setValue(
    #             self.var_fed.value() and
    #             self.var_out_available.value())

    #     return sensitive_set_flow_out_template

    # def create_sensitive_set_flow_prod(self):

    #     def sensitive_set_flow_prod_template():
    #         self.var_prod.setValue(
    #             self.var_prod_available.value())

    #     return sensitive_set_flow_prod_template

    def build_prod_cond_readers(self):
        """One zero-arg reader per ``var_prod_cond`` operand, in CNF shape.

        Resolving what each operand reads ONCE, here, is what keeps the
        extended evaluation a plain ``all(any(...))`` over callables: the
        comparator of a comparison operand is looked up at wiring time, not on
        every evaluation.

        The readers close over the resolved operand objects and read their
        variables lazily, so a flow whose variables are declared after this
        runs is still read correctly.
        """
        # Local import, and NOT a cycle break: ``rules`` imports nothing from
        # muscadet, so a module-level import would work. It is deferred because
        # this module is the discrete-flow layer and only this one method needs
        # the rules unit -- for its comparison vocabulary, so a guard (R21) and
        # a discrete production condition (R22) compare a quantity the same way.
        from .rules import comparator as get_comparator

        readers = []
        for i, flow_outer in enumerate(self.var_prod_cond):
            row = []
            for j, source in enumerate(flow_outer):
                compare = _prod_cond_matrix_entry(self.var_prod_cond_compare, i, j)
                if compare is not None:
                    row.append(
                        _prod_cond_compare_reader(
                            source,
                            get_comparator(compare["op"]),
                            float(compare["value"]),
                        )
                    )
                elif _prod_cond_matrix_entry(self.var_prod_cond_negate, i, j, False):
                    row.append(_prod_cond_negated_reader(source))
                else:
                    row.append(_prod_cond_state_reader(source))
            readers.append(row)

        return readers

    def create_sensitive_set_flow_prod_available(self):

        # When every operand is a plain read of a flow state -- no negation, no
        # comparison, the empty-matrix default of both -- keep the historical
        # closures verbatim: the evaluation is byte-identical to pre-negation
        # muscadet. The reader-based closures only run when the component
        # actually declares a negated or a comparison operand.
        extended = bool(self.var_prod_cond_negate) or bool(self.var_prod_cond_compare)

        if self.var_prod_cond_inner_mode == "or":

            if not extended:

                def sensitive_set_flow_prod_available_template():
                    # for flow_disj in self.var_prod_cond:
                    #     for flow in flow_disj:
                    #         comp = flow.var_fed.parent().basename()
                    #         flow_val = flow.var_fed.value()
                    #         print(f"{comp}: {flow.name}.var_fed = {flow_val}")
                    #         ipdb.set_trace()

                    # [(C11 or C12 or ... or C1_k1) and (C21 or ... C2_k2) and ... and (Cn1 or ... or Cn_kn)]
                    val = all(
                        [
                            any(
                                [
                                    flow_inner.var_fed.value()
                                    for flow_inner in flow_outer
                                ]
                            )
                            for flow_outer in self.var_prod_cond
                        ]
                    )

                    self.var_prod_available.setValue(val)

            else:

                readers = self.build_prod_cond_readers()

                def sensitive_set_flow_prod_available_template():
                    # Same evaluation, per-operand negation and comparisons
                    # applied by the readers.
                    val = all([any([read() for read in row]) for row in readers])

                    self.var_prod_available.setValue(val)

        elif self.var_prod_cond_inner_mode == "and":

            if not extended:

                def sensitive_set_flow_prod_available_template():

                    # [(C11 and C12 and ... and C1_k1) or (C21 and ... and C2_k2) or ... or (Cn1 and ... and Cn_kn)]
                    val = any(
                        [
                            all(
                                [
                                    flow_inner.var_fed.value()
                                    for flow_inner in flow_outer
                                ]
                            )
                            for flow_outer in self.var_prod_cond
                        ]
                    )

                    self.var_prod_available.setValue(val)

            else:

                readers = self.build_prod_cond_readers()

                def sensitive_set_flow_prod_available_template():
                    # Same evaluation, per-operand negation and comparisons
                    # applied by the readers.
                    val = any([all([read() for read in row]) for row in readers])

                    self.var_prod_available.setValue(val)

        else:
            raise ValueError("var_prod_cond_inner_mode must be 'and' or 'or'")

        return sensitive_set_flow_prod_available_template

    def update_sensitive_methods(self, comp):

        # Update flow fed
        self.sm_flow_fed_fun = self.create_sensitive_set_flow_fed_out()
        self.sm_flow_fed_name = f"set_{self.name}_fed_out"
        # > if prod or fed available change
        self.var_prod.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)
        self.var_fed_available.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun
        )
        # > if flow prod available changes
        self.var_prod_available.addSensitiveMethod(
            self.sm_flow_fed_name, self.sm_flow_fed_fun
        )
        # self.var_fed_control.addSensitiveMethod(
        #     self.sm_flow_fed_name, self.sm_flow_fed_fun
        # )

        # Start method
        comp.addStartMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        # Update flow out
        # sens_meth_flow_out = self.create_sensitive_set_flow_out()
        # sens_meth_flow_out_name = f"set_{self.name}_out"
        # # > if flow fed or flow out available change
        # self.var_fed.addSensitiveMethod(
        #     sens_meth_flow_out_name, sens_meth_flow_out)
        # self.var_out_available.addSensitiveMethod(
        #     sens_meth_flow_out_name, sens_meth_flow_out)

        # # Prod
        # sens_meth_flow_prod = self.create_sensitive_set_flow_prod()
        # sens_meth_flow_prod_name = f"set_{self.name}_prod"
        # # > if flow prod available changes
        # self.var_prod_available.addSensitiveMethod(
        #     sens_meth_flow_prod_name, sens_meth_flow_prod)

        # Prod available
        sm_flow_prod_available_fun = self.create_sensitive_set_flow_prod_available()
        sm_flow_prod_available_name = f"set_{self.name}_prod_available"

        # Kept on the flow so add_automata can wire the very same method onto
        # the threshold automata it builds -- exactly what FlowDiscreteOutTempo
        # does with sm_flow_fed_fun.
        self.sm_prod_available_fun = sm_flow_prod_available_fun
        self.sm_prod_available_name = sm_flow_prod_available_name

        # Add prod available update method to be sensitive to input changes
        for flow_outer in self.var_prod_cond:
            for flow_inner in flow_outer:
                # ipdb.set_trace()
                # A measurement link is not a flow and declares no var_fed at
                # all: a comparison operand reading one is re-evaluated by the
                # watched threshold automaton add_automata builds for it. The
                # test is on the ATTRIBUTE and not on its value, so a flow whose
                # variables are not declared yet still raises exactly as before.
                if not hasattr(flow_inner, "var_fed"):
                    continue
                flow_inner.var_fed.addSensitiveMethod(
                    sm_flow_prod_available_name, sm_flow_prod_available_fun
                )

        # A negated operand can make the production condition TRUE while every
        # referenced input stays at its initial (False) value — in which case
        # none of the change-subscriptions above ever fires and var_prod_available
        # would be stuck at its default. A comparison operand has the same gap: a
        # continuous quantity may already sit above its threshold at t=0. Seed the
        # correct value at t=0 with a start method. GATED on the extended operand
        # forms so a plain condition keeps the exact legacy wiring
        # (change-subscriptions only) -> byte-identical.
        if self.var_prod_cond_negate or self.var_prod_cond_compare:
            comp.addStartMethod(sm_flow_prod_available_name, sm_flow_prod_available_fun)

    def add_automata(self, comp, **kwargs):

        super().add_automata(comp, **kwargs)

        self.add_threshold_automata(comp)

    def add_threshold_automata(self, comp):
        """Watch every continuous threshold this production condition carries (R22).

        A comparison operand reading a CONTINUOUS quantity gets a two-state
        automaton -- below the threshold, above it -- whose two instantaneous
        transitions are registered as WATCHED. The solver then stops the
        integration AT the crossing instead of noticing it at the following
        step, which is exactly what a rule guard's mode automaton buys on the
        other direction of the interoperation (R12).

        The automaton is NOT what the condition reads: the closures built by
        :meth:`build_prod_cond_readers` read the quantity live, so the two can
        never disagree on the value. What the automaton contributes is the stop
        at the right date, and the notification that re-runs the condition
        there -- a continuous quantity moving inside an integration step
        announces no change of its own, so nothing else would.

        A comparison on a DISCRETE flow needs none of this: that flow announces
        its own change through ``var_fed``, and watching it would drag a purely
        discrete model onto the PDMP solver.

        Returns
        -------
        list
            The automata built, empty when the condition carries no comparison.
        """
        if not self.var_prod_cond_compare:
            return []

        # Local import, and NOT a cycle break: ``rules`` imports nothing from
        # muscadet, so a module-level import would work. It is deferred because
        # this module is the discrete-flow layer and only this one method needs
        # the rules unit -- for its comparison vocabulary, so a guard (R21) and
        # a discrete production condition (R22) compare a quantity the same way.
        from .rules import comparator as get_comparator

        system = comp.system()
        automata = []

        for i, flow_outer in enumerate(self.var_prod_cond):
            for j, source in enumerate(flow_outer):
                compare = _prod_cond_matrix_entry(self.var_prod_cond_compare, i, j)
                if compare is None:
                    continue
                # Anything that is not a flow -- a measurement link -- carries a
                # continuous quantity too, hence the True default.
                if not getattr(source, "is_continuous", True):
                    continue

                read = _prod_cond_quantity_reader(source)
                compare_fun = get_comparator(compare["op"])
                threshold = float(compare["value"])

                base = f"{self.name}_cond_{i}_{j}"
                st_below = f"{base}_below"
                st_above = f"{base}_above"
                trans_up = f"{base}_cross_up"
                trans_down = f"{base}_cross_down"

                aut = cod3s.PycAutomaton(
                    name=f"{comp.name()}_{base}_threshold",
                    states=[st_below, st_above],
                    # The state the comparison designates cannot be known here:
                    # the instantaneous transitions settle the automaton at t=0,
                    # which is why they must be watched rather than merely
                    # conditioned.
                    init_state=st_below,
                    transitions=[
                        {
                            "name": trans_up,
                            "source": st_below,
                            "target": st_above,
                            "is_interruptible": True,
                            # A fresh mapping per transition: cod3s rewrites the
                            # 'cls' entry in place while sanitizing it.
                            "occ_law": fresh_instant_occ_law(),
                        },
                        {
                            "name": trans_down,
                            "source": st_above,
                            "target": st_below,
                            "is_interruptible": True,
                            "occ_law": fresh_instant_occ_law(),
                        },
                    ],
                )
                aut.update_bkd(comp)

                aut.get_transition_by_name(trans_up)._bkd.setCondition(
                    _prod_cond_threshold_condition(read, compare_fun, threshold, True)
                )
                aut.get_transition_by_name(trans_down)._bkd.setCondition(
                    _prod_cond_threshold_condition(read, compare_fun, threshold, False)
                )

                if self.sm_prod_available_fun is not None:
                    aut._bkd.addSensitiveMethod(
                        self.sm_prod_available_name, self.sm_prod_available_fun
                    )

                system.pdmp_add_watched_automaton(aut)

                comp.automata_d[aut.name] = aut
                automata.append(aut)

        return automata


class FlowOut(FlowDiscreteOut):
    """Legacy name of :class:`FlowDiscreteOut`.

    Kept permanently as a real subclass (never an assignment alias) so that
    instances built through it report ``FlowOut`` as their runtime class name.
    It deliberately sits *between* :class:`FlowDiscreteOut` and the canonical
    tempo / trigger classes so that every ``isinstance(flow, FlowOut)``
    relation that held before the rename still holds.
    """

    pass


class FlowDiscreteOutTempo(FlowOut):

    occ_enable_flow: typing.Optional[
        typing.Union[dict, cod3s.OccurrenceDistributionModel]
    ] = pydantic.Field(
        None,
        description=(
            "Temporisation law to let the flow out (any cod3s occurrence "
            "distribution: delay / exp / inst). None = no enabling temporisation. "
            "When BOTH occ_enable_flow and occ_disable_flow are None the "
            "FlowOutTempo carries no automaton and behaves EXACTLY like a plain "
            "FlowOut."
        ),
    )
    occ_disable_flow: typing.Optional[
        typing.Union[dict, cod3s.OccurrenceDistributionModel]
    ] = pydantic.Field(
        None,
        description="Temporisation law to block the flow out (see occ_enable_flow).",
    )
    # time_to_start_flow: float = \
    #     pydantic.Field(0, description="Start flow out temporisation")
    # time_to_stop_flow: float = \
    #     pydantic.Field(0, description="Stop flow out temporisation")
    state_enable_name: str = pydantic.Field(
        "enabled", description="Name of the enable state"
    )
    # TO IMPLEMENT
    state_enabling_name: str = pydantic.Field(
        "enabling", description="Name of the enabling state"
    )
    # TO IMPLEMENT
    state_disabling_name: str = pydantic.Field(
        "disabling", description="Name of the disabling state"
    )

    state_disable_name: str = pydantic.Field(
        "disabled", description="Name of the disable state"
    )
    init_enable: bool = pydantic.Field(
        False,
        description="Indicates if flow init state is enabled or disabled (default disabled",
    )
    # NOTE: Seems to be a cod3s.StateModel object so that bkd suffix is not relevant...
    # neither the typing.Any...
    state_enable_bkd: typing.Any = pydantic.Field(
        None, description="Enable state backend"
    )

    def add_automata(self, comp, **kwargs):

        super().add_automata(comp, **kwargs)

        # No temporisation law at all -> no enable/disable automaton: the flow
        # behaves EXACTLY like a plain FlowOut (production tracks
        # var_prod_available, cf. create_sensitive_set_flow_fed_out which falls
        # back to the FlowOut sensitive method when state_enable_bkd is None).
        if self.occ_enable_flow is None and self.occ_disable_flow is None:
            return

        # When only one side is temporised, the other transition is immediate
        # (delay 0) so the two-state automaton stays well-formed.
        occ_enable = (
            self.occ_enable_flow
            if self.occ_enable_flow is not None
            else {"cls": "delay", "time": 0}
        )
        occ_disable = (
            self.occ_disable_flow
            if self.occ_disable_flow is not None
            else {"cls": "delay", "time": 0}
        )

        aut = cod3s.PycAutomaton(
            name=f"{self.name}_out_tempo",
            states=[self.state_disable_name, self.state_enable_name],
            init_state=(
                self.state_enable_name if self.init_enable else self.state_disable_name
            ),
            transitions=[
                {
                    "name": f"{self.name}_enable",
                    "source": self.state_disable_name,
                    "target": self.state_enable_name,
                    "is_interruptible": True,
                    "occ_law": occ_enable,
                },
                {
                    "name": f"{self.name}_disable",
                    "source": self.state_enable_name,
                    "target": self.state_disable_name,
                    "is_interruptible": True,
                    "occ_law": occ_disable,
                },
            ],
        )
        aut.update_bkd(comp)

        trans_name = f"{self.name}_enable"
        cond_method_name = f"cond_{comp.name}_{aut.name}_{trans_name}"

        def cond_method_enable():
            return self.var_prod_available.value()

        aut.get_transition_by_name(trans_name)._bkd.setCondition(
            cond_method_name, cond_method_enable
        )

        trans_name = f"{self.name}_disable"
        # Recompute the condition method name for the second transition: reusing the
        # `_enable` name registered above would advertise a `_disable` transition under
        # an `_enable` condition, and would break silently should PyCATSHOO ever key
        # registered conditions by name.
        cond_method_name = f"cond_{comp.name}_{aut.name}_{trans_name}"

        def cond_method_disable():
            return not self.var_prod_available.value()

        aut.get_transition_by_name(trans_name)._bkd.setCondition(
            cond_method_name, cond_method_disable
        )

        # if self.trigger_logic == "and":
        #     def cond_method_21():
        #         return self.var_trigger_in.andValue(False)
        # elif self.trigger_logic == "or":
        #     def cond_method_21():
        #         return self.var_trigger_in.orValue(False)
        # else:
        #     raise ValueError("trigger logic must be 'and' or 'or'")
        self.state_enable_bkd = aut.get_state_by_name(self.state_enable_name)

        aut._bkd.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        comp.automata_d[aut.name] = aut

    # Overloaded from class FlowOut
    def create_sensitive_set_flow_fed_out(self):

        # No temporisation law -> no automaton is built (see add_automata) ->
        # reuse the plain FlowOut sensitive method verbatim: production tracks
        # var_prod_available, byte-identical to a FlowOut. The condition is on
        # the LAWS (known now) and not on state_enable_bkd, which add_automata
        # sets AFTER this method runs during wiring (the tempo closures below
        # read state_enable_bkd lazily at simulation time, when it is set).
        if self.occ_enable_flow is None and self.occ_disable_flow is None:
            return super().create_sensitive_set_flow_fed_out()

        if not self.negate:

            def sensitive_set_flow_template():
                # self.var_prod.setValue(
                #     self.flow_start._bkd.isActive() and
                #     self.var_prod_available.value())
                self.var_prod.setValue(self.state_enable_bkd._bkd.isActive())

                self.var_fed.setValue(
                    self.var_prod.value()
                    and self.var_fed_available.value()
                    #                    and self.var_fed_control.andValue(True)
                )

        else:

            def sensitive_set_flow_template():
                # self.var_prod.setValue(
                #     self.flow_start._bkd.isActive() and
                #     self.var_prod_available.value())
                self.var_prod.setValue(self.state_enable_bkd._bkd.isActive())

                self.var_fed.setValue(
                    not (
                        self.var_prod.value()
                        and self.var_fed_available.value()
                        #                        and self.var_fed_control.andValue(True)
                    )
                )

        return sensitive_set_flow_template


class FlowOutTempo(FlowDiscreteOutTempo):
    """Legacy name of :class:`FlowDiscreteOutTempo`."""

    pass


class FlowDiscreteOutOnTrigger(FlowOut):
    var_trigger_in: typing.Any = pydantic.Field(
        None, description="Trigger input reference"
    )
    trigger_time_up: float = pydantic.Field(
        0, description="Time to jump from down to up when trigger is activited"
    )
    trigger_time_down: float = pydantic.Field(
        0, description="Time to jump from up to down when trigger is activited"
    )
    trigger_logic: typing.Union[str, int] = pydantic.Field(
        "or", description="Flow input logic: 'and', 'or', or int k (at-least-k)"
    )
    trigger_up: typing.Any = pydantic.Field(None, description="Trigger up state")

    def add_variables(self, comp, **kwargs):

        super().add_variables(comp, **kwargs)

        self.var_trigger_in = comp.addReference(f"{self.name}_trigger_in")

    def add_mb(self, comp, **kwargs):

        super().add_mb(comp, **kwargs)

        comp.addMessageBox(f"{self.name}_trigger_in")
        comp.addMessageBoxImport(
            f"{self.name}_trigger_in", self.var_trigger_in, self.name
        )

    def add_automata(self, comp, **kwargs):

        super().add_automata(comp, **kwargs)

        aut = cod3s.PycAutomaton(
            name=f"{self.name}_trigger",
            states=["down", "up"],
            init_state="down",
            transitions=[
                {
                    "name": f"{self.name}_trigger_up",
                    "source": "down",
                    "target": "up",
                    "is_interruptible": True,
                    "occ_law": {"cls": "delay", "time": self.trigger_time_up},
                },
                {
                    "name": f"{self.name}_trigger_down",
                    "source": "up",
                    "target": "down",
                    "is_interruptible": True,
                    "occ_law": {"cls": "delay", "time": self.trigger_time_down},
                },
            ],
        )

        aut.update_bkd(comp)

        trans_name = f"{self.name}_trigger_up"
        cond_method_name = f"cond_{comp.name}_{aut.name}_{trans_name}"
        if self.trigger_logic == "and":

            def cond_method_12():
                return not (self.var_trigger_in.andValue(False))

        elif self.trigger_logic == "or":

            def cond_method_12():
                return not (self.var_trigger_in.orValue(False))

        elif isinstance(self.trigger_logic, int):
            k_up = self.trigger_logic

            def cond_method_12():
                return not (self.var_trigger_in.sumValue(0) >= k_up)

        else:
            raise ValueError("trigger logic must be 'and', 'or', or a positive integer")

        aut.get_transition_by_name(trans_name)._bkd.setCondition(
            cond_method_name, cond_method_12
        )

        trans_name = f"{self.name}_trigger_down"
        cond_method_name = f"cond_{comp.name}_{aut.name}_{trans_name}"
        if self.trigger_logic == "and":

            def cond_method_21():
                return self.var_trigger_in.andValue(False)

        elif self.trigger_logic == "or":

            def cond_method_21():
                return self.var_trigger_in.orValue(False)

        elif isinstance(self.trigger_logic, int):
            k_down = self.trigger_logic

            def cond_method_21():
                return self.var_trigger_in.sumValue(0) >= k_down

        else:
            raise ValueError("trigger logic must be 'and', 'or', or a positive integer")

        aut.get_transition_by_name(trans_name)._bkd.setCondition(
            cond_method_name, cond_method_21
        )

        self.trigger_up = aut.get_state_by_name("up")

        aut._bkd.addSensitiveMethod(self.sm_flow_fed_name, self.sm_flow_fed_fun)

        comp.automata_d[aut.name] = aut

    # Overloaded from class FlowOut
    def create_sensitive_set_flow_fed_out(self):

        def sensitive_set_flow_template():
            if not self.negate:
                self.var_prod.setValue(
                    self.trigger_up._bkd.isActive() and self.var_prod_available.value()
                )

                self.var_fed.setValue(
                    self.var_prod.value()
                    and self.var_fed_available.value()
                    #                    and self.var_fed_control.andValue(True)
                )
            else:
                self.var_prod.setValue(
                    self.trigger_up._bkd.isActive() and self.var_prod_available.value()
                )

                self.var_fed.setValue(
                    not (
                        self.var_prod.value()
                        and self.var_fed_available.value()
                        #                        and self.var_fed_control.andValue(True)
                    )
                )

        return sensitive_set_flow_template


class FlowOutOnTrigger(FlowDiscreteOutOnTrigger):
    """Legacy name of :class:`FlowDiscreteOutOnTrigger`."""

    pass
