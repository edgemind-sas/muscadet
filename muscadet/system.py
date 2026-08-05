import cod3s

from .obj_logic import LogicOr, LogicAnd
from .flow_continuous import FlowContinuous
import re
import copy

#: Name of the single PDMP manager a muscadet system owns. Every continuous
#: declaration registers on *this* manager, which is what makes PyCATSHOO
#: sequence equation methods of *different* components against each other
#: (verified: flipping the order integers flips the observed call sequence).
PDMP_MANAGER_NAME = "muscadet_pdmp"


class System(cod3s.PycSystem):
    """A muscadet system.

    Beyond the connection helpers, this class owns the two pieces the
    continuous layer needs and that its ``cod3s`` base does not provide.

    PDMP manager ownership (KTD5)
    -----------------------------
    The system owns **one** PDMP manager, created lazily on the first
    continuous declaration and reached by components through
    ``self.system().get_or_create_pdmp_manager()``. A system whose components
    declare only discrete flows never creates one, which is what keeps purely
    discrete models identical to 1.x.

    The pre-run step (KTD14)
    ------------------------
    muscadet has no build step: the modeller adds components, connects them,
    then simulates. The only moment where every connection exists and no
    equation has run yet is the *start of a run* -- and the two run entry
    points do not converge in ``cod3s``: :meth:`simulate` goes through
    ``prepare_simu``, while :meth:`isimu_start` calls the engine directly.
    Both are overridden here so they call :meth:`prerun` first.

    :meth:`prerun` is the idempotent driver; :meth:`prerun_step` is the
    extension point that does the actual work (empty here, filled by the
    ordering unit). ``prerun_done`` / ``prerun_count`` are the inspection
    accessors.
    """

    # ------------------------------------------------------------------
    # PDMP manager ownership
    # ------------------------------------------------------------------

    @property
    def pdmp_manager(self):
        """The system's PDMP manager, or ``None`` while it is purely discrete.

        Deliberately read-only and lazily backed: a discrete-only system must
        never carry one, and no ``__init__`` override is needed to hold it.
        """
        return getattr(self, "_pdmp_manager", None)

    def get_or_create_pdmp_manager(self):
        """Return the system's PDMP manager, creating it on first need.

        This is the entry point components use, through ``self.system()``,
        when a declaration needs the continuous solver.
        """
        manager = self.pdmp_manager
        if manager is None:
            manager = self.addPDMPManager(PDMP_MANAGER_NAME)
            self._pdmp_manager = manager
        return manager

    @staticmethod
    def component_has_continuous_flow(comp):
        """True when ``comp`` declares at least one continuous flow."""
        for flows_attr in ("flows_in", "flows_out"):
            flows = getattr(comp, flows_attr, None) or {}
            for flow in flows.values():
                if isinstance(flow, FlowContinuous):
                    return True
        return False

    def add_component(self, **comp_specs):
        """Add a component, creating the PDMP manager on the first continuous one.

        This is where "lazily created on the first continuous declaration"
        actually happens: a component carrying a continuous flow is the first
        thing that makes the system continuous.
        """
        comp = super().add_component(**comp_specs)

        if comp is not None and self.component_has_continuous_flow(comp):
            self.get_or_create_pdmp_manager()

        return comp

    # ------------------------------------------------------------------
    # PDMP registration helpers
    # ------------------------------------------------------------------
    #
    # PyCATSHOO does NOT freeze registration at component construction:
    # ``addODEVariable`` / ``addExplicitVariable`` / ``addEquationMethod`` /
    # ``addWatchedTransition`` all accept calls made long after the components
    # exist -- including from the pre-run step, on both the batch and the
    # interactive path. That is what makes the graph-derived ordering possible.

    def pdmp_add_ode_variable(self, var):
        """Register ``var`` as a variable integrated by the ODE solver."""
        manager = self.get_or_create_pdmp_manager()
        manager.addODEVariable(self._unwrap_bkd(var))
        return manager

    def pdmp_add_explicit_variable(self, var):
        """Register ``var`` as a variable computed alongside the ODE system."""
        manager = self.get_or_create_pdmp_manager()
        manager.addExplicitVariable(self._unwrap_bkd(var))
        return manager

    def pdmp_add_equation_method(self, method_name, comp, order):
        """Register ``comp.method_name`` as an equation method at ``order``.

        ``order`` is mandatory and must be an ``int``: PyCATSHOO falls back to
        alphabetical equation-name order when two equations share an order
        value, so a derived evaluation order only holds if every equation gets
        a distinct integer (KTD3). The caller owns that allocation.
        """
        if not isinstance(order, int) or isinstance(order, bool):
            raise TypeError(
                f"PDMP equation order must be an int, got {type(order).__name__} "
                f"for {method_name!r}"
            )
        manager = self.get_or_create_pdmp_manager()
        manager.addEquationMethod(method_name, comp, order)
        return manager

    def pdmp_add_watched_transition(self, transition):
        """Register ``transition`` as a stop condition of the integration.

        Accepts either a ``cod3s.PycTransition`` wrapper or the raw backend
        transition, mirroring what ``PycComponent.add_aut2st`` does with its
        ``pdmp_managers`` parameter.
        """
        manager = self.get_or_create_pdmp_manager()
        manager.addWatchedTransition(self._unwrap_bkd(transition))
        return manager

    @staticmethod
    def _unwrap_bkd(obj):
        """Return the PyCATSHOO backend object behind a cod3s wrapper."""
        bkd = getattr(obj, "_bkd", None)
        return obj if bkd is None else bkd

    # ------------------------------------------------------------------
    # The shared pre-run step
    # ------------------------------------------------------------------

    @property
    def prerun_done(self):
        """True once the pre-run step has run for the current engine system."""
        return getattr(self, "_prerun_done", False)

    @property
    def prerun_count(self):
        """How many times :meth:`prerun_step` was actually invoked.

        Purely diagnostic, and therefore *not* reset by :meth:`deleteSys`: it
        is what a test asserts on to prove the step ran exactly once across a
        restart or a second run.
        """
        return getattr(self, "_prerun_count", 0)

    def prerun(self):
        """Run the pre-run step once, before either run entry point starts.

        Idempotent: a second run, or a restart after a stop, is a no-op.

        Returns
        -------
        bool
            True when :meth:`prerun_step` was invoked by this call, False when
            it had already run.
        """
        if self.prerun_done:
            return False

        self._prerun_done = True
        self._prerun_count = self.prerun_count + 1
        self.prerun_step()

        return True

    def prerun_step(self):
        """The work done once, at the start of the first run.

        Extension point: this is where the evaluation order is computed from
        the connection graph and registered on the PDMP manager. It sees the
        fully connected system and no equation has run yet.

        A purely discrete system reaches this too, and it must stay a no-op
        there -- no manager, no registration, nothing observable.
        """
        return None

    # ------------------------------------------------------------------
    # Run entry points -- both must go through the pre-run step
    # ------------------------------------------------------------------

    def simulate(self, *args, **kwargs):
        """Batch (Monte Carlo) run, preceded by the pre-run step."""
        self.prerun()
        return super().simulate(*args, **kwargs)

    def isimu_start(self, *args, **kwargs):
        """Interactive session start, preceded by the pre-run step.

        ``cod3s.PycSystem.isimu_start`` never touches ``prepare_simu``, so a
        step wired only into :meth:`simulate` would silently do nothing here.
        """
        self.prerun()
        return super().isimu_start(*args, **kwargs)

    def deleteSys(self, *args, **kwargs):
        """Delete the engine system and release everything bound to it.

        Dropping the manager handle and the pre-run flag is what lets a
        following test module build a clean system in the same process.
        """
        self._pdmp_manager = None
        self._prerun_done = False
        return super().deleteSys(*args, **kwargs)

    def auto_connect(
        self,
        source,
        target,
        available_connect=False,
        logger=None,
    ):

        obj_source_list = [
            obj for obj in self.comp.keys() if re.search(f"^({source})$", obj)
        ]

        conn_list = []
        for src in obj_source_list:

            conn_list += [
                {
                    "source": src,
                    "target": obj,
                }
                for obj in self.comp.keys()
                if re.search(f"^({source})({target})$", src + obj)
            ]

        connections_created = []

        for conn in conn_list:
            # Test to ensure source is different from target
            # Could happen with regex
            if conn["source"] != conn["target"]:
                conn_created_cur = self.auto_connect_flows(
                    source=conn["source"],
                    target=conn["target"],
                    available_connect=available_connect,
                    logger=logger,
                )
                connections_created.extend(conn_created_cur)

        return connections_created

    def auto_connect_flows(
        self,
        source,
        target,
        available_connect=False,
        logger=None,
    ):
        """
        Connects flows between a source and a target component.

        Args:
            source (str): The source component.
            target (str): The target component.
            available_connect (bool, optional): Whether to include an "_available" suffix in the flow name. Defaults to False.
            logger (logging.Logger, optional): Logger for debug messages. Defaults to None.

        Returns:
            list: A list of connections created.
        """

        connections_list = []

        available_suffix = "_available" if available_connect else ""

        for flow_out in self.comp[source].flows_out:

            if flow_out in self.comp[target].flows_in:
                flow_name = f"{flow_out}{available_suffix}"

                connection = self.connect_flow(
                    source=source,
                    target=target,
                    flow_name=flow_name,
                    flow_key=flow_out,
                    logger=logger,
                )
                if not (connection is None):
                    connections_list.append(connection)
        return connections_list

    def check_comp_attributes(self, comp_name, attr_cond_list):
        comp = self.comp[comp_name]

        return any(
            [
                all(
                    [
                        (
                            re.search(f"^{val}$", getattr(comp, attr))
                            if isinstance(val, str)
                            else getattr(comp, attr) == val
                        )
                        for attr, val in auth_cond.items()
                    ]
                )
                for auth_cond in attr_cond_list
            ]
        )

    def connect_flow(
        self,
        source,
        target,
        flow_name,
        out_suffix="_out",
        in_suffix="_in",
        check_authorization=True,
        flow_key=None,
        logger=None,
    ):
        """
        Connects a specific flow between a source and target component.

        Args:
            source (str): The source component.
            target (str): The target component.
            flow_name (str): The flow message-box prefix used to build the
                source/target message boxes (i.e. ``{flow_name}{out_suffix}``
                and ``{flow_name}{in_suffix}``). May carry an ``_available``
                suffix when wiring the availability channel.
            out_suffix (str): Source message-box suffix.
            in_suffix (str): Target message-box suffix.
            check_authorization (bool): When True, validate connection against
                ``component_authorized`` patterns declared on the flow.
            flow_key (str, optional): Key used to look up the flow definition
                in ``flows_out``/``flows_in`` for the authorization check.
                Defaults to ``flow_name`` (backward-compatible). Must be set
                explicitly when ``flow_name`` carries a suffix that is not a
                key in the flow dicts (e.g. ``"f1_available"``).
            logger (logging.Logger, optional): Logger for debug messages.

        Returns:
            dict or None: The connection details if created, otherwise None.
        """
        if flow_key is None:
            flow_key = flow_name

        connection = None
        if self.comp[source].is_connected_to(target, flow_name):
            if not (logger is None):
                logger.debug(f"!!! {source} -- {flow_name} --> {target} already exists")
        else:

            if check_authorization:
                source_flow = self.comp[source].flows_out[flow_key]
                target_flow = self.comp[target].flows_in[flow_key]

                source_is_continuous = isinstance(source_flow, FlowContinuous)
                target_is_continuous = isinstance(target_flow, FlowContinuous)
                if source_is_continuous != target_is_continuous:
                    source_kind = "continuous" if source_is_continuous else "discrete"
                    target_kind = "continuous" if target_is_continuous else "discrete"
                    raise ValueError(
                        f"Cannot connect {source}.{flow_key} "
                        f"({source_kind} {type(source_flow).__name__}) to "
                        f"{target}.{flow_key} "
                        f"({target_kind} {type(target_flow).__name__}): "
                        "continuous and discrete flows cannot be connected"
                    )

                source_flow_comp_auth_pat = source_flow.component_authorized
                check_source_auth = self.check_comp_attributes(
                    target, source_flow_comp_auth_pat
                )
                if not check_source_auth:
                    if logger is not None:
                        logger.debug(
                            f"!!! {source} -- {flow_name} --> {target} not authorized by {source}"
                        )
                    return None

                target_flow_comp_auth_pat = target_flow.component_authorized
                check_target_auth = self.check_comp_attributes(
                    source, target_flow_comp_auth_pat
                )
                if not check_target_auth:
                    if logger is not None:
                        logger.debug(
                            f"!!! {source} -- {flow_name} --> {target} not authorized by {target}"
                        )
                    return None

            self.connect(
                source, f"{flow_name}{out_suffix}", target, f"{flow_name}{in_suffix}"
            )
            connection = {
                "source": source,
                "flow": flow_name,
                "target": target,
            }

            if not (logger is None):
                logger.debug(f"{source} -- {flow_name} --> {target}")
        return connection

    def connect_trigger(
        self,
        source,
        target,
        flow_name,
        logger=None,
    ):
        """
        Connects output flow from the source component to the trigger_in flow of the target component.

        Args:
            source (str): The source component.
            target (str): The target component.
            flow_name (str): The name of the flow to connect.
            logger (logging.Logger, optional): Logger for debug messages. Defaults to None.
        """
        self.connect_flow(
            source=source,
            target=target,
            flow_name=flow_name,
            out_suffix="_out",
            in_suffix="_trigger_in",
            check_authorization=False,
        )

    def clean_comp_flow_specs(self, comp_flow_specs):
        # Scan input components
        comp_flow_specs_clean = copy.deepcopy(comp_flow_specs)
        if isinstance(comp_flow_specs_clean, list):
            comp_flow_specs_clean = {k: ".*" for k in comp_flow_specs_clean}
        elif isinstance(comp_flow_specs_clean, dict):
            pass
        else:
            raise ValueError(
                f"Component/flow specification {type(comp_flow_specs_clean)} not supported"
            )
        return comp_flow_specs_clean

    def get_comp_flow_in_from_specs(self, comp_flow_specs):

        comp_flow_specs_clean = self.clean_comp_flow_specs(comp_flow_specs)

        flows_in = []
        comp_in = []
        for comp_pat, flow_pat in comp_flow_specs_clean.items():
            comp_list = [
                obj for obj in self.comp.keys() if re.search(f"^({comp_pat})$", obj)
            ]
            for comp_name in comp_list:
                flows_in_new = [
                    flow
                    for flow in self.comp[comp_name].flows_out
                    if re.search(f"^({flow_pat})$", flow)
                ]

                if flows_in_new:
                    flows_in.extend(flows_in_new)
                    comp_in.append(comp_name)

        return list(set(comp_in)), list(set(flows_in))

    def add_logic_or(self, name, comp_in_specs, on_available=False, **params):
        """ """
        comp_in, flows_in = self.get_comp_flow_in_from_specs(comp_in_specs)

        # Set metadata from input comp if needed
        metadata = params.pop("metadata", {})
        if comp_in:
            for comp_in_name_cur in comp_in:
                comp_in_cur = self.comp[comp_in_name_cur]
                for key, val in comp_in_cur.metadata.items():
                    metadata.setdefault(key, val)

        self.add_component(
            cls="LogicOr",
            name=name,
            flows_in=flows_in,
            var_in_default=on_available,
            var_available_in_default=not on_available,
            metadata=metadata,
            **params,
        )

        for comp in comp_in_specs:
            self.auto_connect(comp, name, available_connect=on_available)

    def add_logic_and(self, name, comp_in_specs, on_available=False, **params):
        """ """
        comp_in, flows_in = self.get_comp_flow_in_from_specs(comp_in_specs)

        # Set metadata from input comp if needed
        metadata = params.pop("metadata", {})
        if comp_in:
            for comp_in_name_cur in comp_in:
                comp_in_cur = self.comp[comp_in_name_cur]
                for key, val in comp_in_cur.metadata.items():
                    metadata.setdefault(key, val)

        self.add_component(
            cls="LogicAnd",
            name=name,
            flows_in=flows_in,
            var_in_default=on_available,
            var_available_in_default=not on_available,
            metadata=metadata,
            **params,
        )

        for comp in comp_in_specs:
            self.auto_connect(comp, name, available_connect=on_available)
