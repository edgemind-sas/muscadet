import Pycatshoo as pyc
import typing
import pydantic
from colored import fg, attr

import cod3s
from .common import get_pyc_type


def _prod_cond_operand_value(flow_inner, negate_matrix, i, j):
    """Return the effective boolean of one ``var_prod_cond`` operand.

    ``flow_inner`` is a resolved flow object; ``negate_matrix`` mirrors the
    shape of ``var_prod_cond`` (list[list[bool]]). When the aligned entry is
    True the operand is negated (``not var_fed``), otherwise the raw
    ``var_fed`` value is returned. A missing entry (shorter / empty matrix)
    defaults to non-negated, so a partial or empty matrix is safe.
    """
    value = flow_inner.var_fed.value()
    if i < len(negate_matrix) and j < len(negate_matrix[i]) and negate_matrix[i][j]:
        return not value
    return value


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

    @classmethod
    def get_clsname(basecls, **specs):
        port_name = specs.pop("port")
        if port_name == "io":
            port_name = "IO"
        else:
            port_name = port_name.capitalize()
        clsname = f"Flow{port_name}"
        return clsname

    def add_variables(self, comp, port, **kwargs):

        py_type, pyc_type = get_pyc_type(self.var_type)

        self.var_fed_default = (
            py_type() if self.var_fed_default is None else self.var_fed_default
        )

        self.var_fed = comp.addVariable(
            f"{self.name}_fed_{port}", pyc_type, py_type(self.var_fed_default)
        )

    def add_automata(self, comp):
        pass

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


class FlowDiscreteIn(FlowModel):

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


class FlowDiscreteOut(FlowModel):

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
        """Operand name for the textual condition, prefixed with '¬' when the
        aligned ``var_prod_cond_negate`` entry marks it negated."""
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

    def create_sensitive_set_flow_prod_available(self):

        # When no operand is negated (the empty matrix default) keep the
        # historical closures verbatim: the evaluation is byte-identical to
        # pre-negation muscadet. The negate-aware closures only run when the
        # component actually declares a negated operand.
        negate_matrix = self.var_prod_cond_negate

        if self.var_prod_cond_inner_mode == "or":

            if not negate_matrix:

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

                def sensitive_set_flow_prod_available_template():
                    # Same evaluation, with per-operand negation applied.
                    val = all(
                        [
                            any(
                                [
                                    _prod_cond_operand_value(
                                        flow_inner, negate_matrix, i, j
                                    )
                                    for j, flow_inner in enumerate(flow_outer)
                                ]
                            )
                            for i, flow_outer in enumerate(self.var_prod_cond)
                        ]
                    )

                    self.var_prod_available.setValue(val)

        elif self.var_prod_cond_inner_mode == "and":

            if not negate_matrix:

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

                def sensitive_set_flow_prod_available_template():
                    # Same evaluation, with per-operand negation applied.
                    val = any(
                        [
                            all(
                                [
                                    _prod_cond_operand_value(
                                        flow_inner, negate_matrix, i, j
                                    )
                                    for j, flow_inner in enumerate(flow_outer)
                                ]
                            )
                            for i, flow_outer in enumerate(self.var_prod_cond)
                        ]
                    )

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

        # Add prod available update method to be sensitive to input changes
        for flow_outer in self.var_prod_cond:
            for flow_inner in flow_outer:
                # ipdb.set_trace()
                flow_inner.var_fed.addSensitiveMethod(
                    sm_flow_prod_available_name, sm_flow_prod_available_fun
                )

        # A negated operand can make the production condition TRUE while every
        # referenced input stays at its initial (False) value — in which case
        # none of the change-subscriptions above ever fires and var_prod_available
        # would be stuck at its default. Seed the correct value at t=0 with a
        # start method. GATED on negation so a non-negated condition keeps the
        # exact legacy wiring (change-subscriptions only) -> byte-identical.
        if self.var_prod_cond_negate:
            comp.addStartMethod(sm_flow_prod_available_name, sm_flow_prod_available_fun)


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
