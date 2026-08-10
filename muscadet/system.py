import cod3s

from .obj_logic import LogicOr, LogicAnd
from .flow_continuous import FlowContinuous
from .ordering import (
    CAPACITY_ORDER_BASE,
    MEASUREMENT_ORDER_BASE,
    EquationRegistration,
    build_continuous_flow_graph,
    component_is_continuous,
    register_equation_order,
)
import re
import copy

#: Name of the single PDMP manager a muscadet system owns. Every continuous
#: declaration registers on *this* manager, which is what makes PyCATSHOO
#: sequence equation methods of *different* components against each other
#: (verified: flipping the order integers flips the observed call sequence).
PDMP_MANAGER_NAME = "muscadet_pdmp"

#: Message-box suffixes an OUTPUT flow exports, longest first so that
#: ``{f}_available_out`` resolves to ``f`` rather than to ``f_available``. A
#: name matching none of them belongs to no flow -- a measurement link
#: (``{c}_level_out``), a logic gate's export -- and is left alone.
FLOW_OUT_SUFFIXES = ("_available_out", "_out")

#: Same for the boxes an INPUT side offers. ``_trigger_in`` is the third
#: channel of a discrete trigger flow, whose flow object lives in ``flows_out``
#: on the receiving component -- see :meth:`System.flow_behind_message_box`.
FLOW_IN_SUFFIXES = ("_available_in", "_trigger_in", "_in")


class ModelChangedAfterPrerunError(ValueError):
    """The continuous model grew after the pre-run step registered its equations.

    The pre-run step runs **once**, at the start of the first run: it derives
    the evaluation order from the whole connection graph and registers the two
    sweep equations of every continuous component on the PDMP manager. A
    continuous component -- or a continuous connection -- appearing after that
    is never registered, and there is no way to register it correctly
    afterwards:

    * PyCATSHOO **refuses** to register an equation the manager already holds
      (``[E]L'ODE <comp>.<method> appartient déjà au PDMP muscadet_pdmp``), and
      ``IPDMPManager`` exposes no removal counterpart. The equation set of a
      manager is append-only;
    * the order is derived **globally** from the graph, so a late component
      does not merely add equations, it renumbers existing ones -- and those
      are exactly the registrations that cannot be redone. Appending the new
      equations above every order already taken is all that is left, and it
      puts a demand equation above production equations, breaking the band
      separation :meth:`muscadet.ObjFlow.get_output_request` relies on.

    Left undiagnosed, the late component runs **inert**: its rules never
    evaluate, its outputs hold their declared defaults, and the demand it
    should have published upstream is missing -- so the components feeding it
    silently produce less too. The acyclicity check of R30 is skipped along
    with the rest, so a loop closed after the first run is not refused either.

    Raised from :meth:`muscadet.System.prerun`, which both run entry points go
    through, so it fires at ``simulate()`` / ``isimu_start()`` -- before any
    result exists to be wrong.
    """


def describe_model_change(before, after):
    """Describe how a system's continuous graph changed, as a list of phrases.

    Parameters
    ----------
    before, after : tuple
        Signatures as returned by :meth:`muscadet.System.model_signature`:
        ``(node names, continuous connections)``.

    Returns
    -------
    list of str
        Empty when the two signatures agree.
    """
    nodes_before, cnx_before = before
    nodes_after, cnx_after = after

    changes = []

    added = [name for name in nodes_after if name not in set(nodes_before)]
    if added:
        changes.append(f"components added since: {', '.join(added)}")

    removed = [name for name in nodes_before if name not in set(nodes_after)]
    if removed:
        changes.append(f"components removed since: {', '.join(removed)}")

    def label(cnct):
        return f"{cnct.source}.{cnct.flow}_out -> {cnct.target}.{cnct.flow}_in"

    # Only over the components present on BOTH sides: a connection to a
    # component already reported as added says nothing more.
    common = set(nodes_before) & set(nodes_after)

    def between_common(connections):
        return [
            cnct
            for cnct in connections
            if cnct.source in common and cnct.target in common
        ]

    kept_before = between_common(cnx_before)
    kept_after = between_common(cnx_after)

    new_cnx = [cnct for cnct in kept_after if cnct not in set(kept_before)]
    if new_cnx:
        changes.append(
            "connections added since: " + ", ".join(label(c) for c in new_cnx)
        )

    gone_cnx = [cnct for cnct in kept_before if cnct not in set(kept_after)]
    if gone_cnx:
        changes.append(
            "connections removed since: " + ", ".join(label(c) for c in gone_cnx)
        )

    return changes


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

    :meth:`prerun` is the idempotent driver; :meth:`prerun_step` derives the
    evaluation order from the connection graph and registers the sweep
    equations. ``prerun_done`` / ``prerun_count`` are the inspection accessors,
    and ``equation_order`` is the derived order itself.

    The step is one-shot **per engine system**, and cannot be otherwise:
    PyCATSHOO refuses to register an equation its PDMP manager already holds
    and offers no way to remove one, while the order is derived globally, so a
    late component renumbers registrations that can no longer be redone. A
    continuous component or connection appearing after the step is therefore
    refused at the next entry point, by
    :meth:`check_model_unchanged_since_prerun`, rather than left to run inert.
    ``model_signature`` is what the two states are compared through, and it is
    empty on a purely discrete system -- such a system keeps growing between
    runs exactly as it did in 1.x.

    Equation ordering (R8, R30)
    ---------------------------
    The order is never declared by a model author: :mod:`muscadet.ordering`
    reads the topology back from the engine, sorts it, and hands every equation
    a distinct integer. Those integers are banded -- the demand sweep from 0,
    the production sweep straight after, and the capacity equations from
    :data:`~muscadet.ordering.CAPACITY_ORDER_BASE`, so they integrate last.
    ``_capacity_equation_order_next`` below is what makes the capacity unit's
    provisional counter draw from that top band instead of from 0.
    """

    #: Read by ``muscadet.capacity.allocate_capacity_equation_order`` at
    #: capacity *declaration* time, long before any graph exists. Starting it at
    #: the capacity band is how the ordering module's allocation supersedes that
    #: provisional counter: capacity equations stay distinct from one another
    #: AND from every graph-derived order, and they run after both sweeps.
    #: Read here resolves to the class attribute; the first allocation writes an
    #: instance attribute, so systems never share a counter.
    _capacity_equation_order_next = CAPACITY_ORDER_BASE

    #: Same mechanism for the published-measurement equations of R37, one band
    #: higher: a republished reading is taken from a level a capacity integrates,
    #: so it is refreshed after every capacity equation has run.
    _measurement_equation_order_next = MEASUREMENT_ORDER_BASE

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
        """True when ``comp`` declares at least one continuous flow.

        Delegates to :func:`muscadet.ordering.component_is_continuous` so that
        "this component is continuous" has ONE definition: a component could
        otherwise count as continuous for the PDMP manager created here and as
        discrete for the equation graph, or the reverse.
        """
        return component_is_continuous(comp)

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

        # Recorded here rather than at each call site: this is the one funnel
        # every equation goes through -- the two graph-derived sweeps, the
        # capacity equations, and anything a model registers by hand -- so it is
        # the only place where "no two equations share an order" is checkable.
        self.equation_registrations.append(
            EquationRegistration(comp=comp.basename(), method=method_name, order=order)
        )

        return manager

    @property
    def equation_registrations(self):
        """Every PDMP equation registered on this system, in registration order.

        A list of :class:`~muscadet.ordering.EquationRegistration`. Cleared by
        :meth:`deleteSys`, like everything else bound to the engine system.
        """
        if not hasattr(self, "_equation_registrations"):
            self._equation_registrations = []
        return self._equation_registrations

    def pdmp_add_watched_transition(self, transition):
        """Register ``transition`` as a stop condition of the integration.

        Accepts either a ``cod3s.PycTransition`` wrapper or the raw backend
        transition, mirroring what ``PycComponent.add_aut2st`` does with its
        ``pdmp_managers`` parameter.
        """
        manager = self.get_or_create_pdmp_manager()
        manager.addWatchedTransition(self._unwrap_bkd(transition))
        return manager

    def pdmp_add_watched_automaton(self, automaton):
        """Register EVERY transition of ``automaton`` as a stop condition.

        The three automata muscadet builds to catch a crossing -- a rule set's
        mode automaton (R12), a capacity's empty/full bounds (R7) and the
        threshold automaton of a discrete production condition (R22) -- differ
        in shape but register identically: all of their transitions are
        watched, so the solver stops the integration AT the crossing instead of
        noticing it at the following step.

        Returns
        -------
        The PDMP manager, or None for an automaton carrying no transition --
        which registers nothing and therefore creates no manager either.
        """
        manager = None
        for transition in automaton.transitions:
            manager = self.pdmp_add_watched_transition(transition)
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

    def model_signature(self):
        """The continuous-flow model as the pre-run step sees it.

        ``(node names, continuous connections)``, read back from the engine
        rather than from anything muscadet caches, so it reflects exactly what
        :meth:`prerun_step` would derive an order from. Empty on a purely
        discrete system, which is what keeps such a system free to grow after
        a run exactly as it did in 1.x.
        """
        graph = build_continuous_flow_graph(self)

        return (tuple(graph.nodes), tuple(graph.connections))

    def check_model_unchanged_since_prerun(self):
        """Refuse a run whose continuous model grew after the pre-run step.

        The pre-run step is **one-shot per engine system**, and it has to be:
        PyCATSHOO refuses to re-register an equation its manager already holds
        and offers no way to remove one, while the order is derived globally
        and a late component renumbers existing equations. So a second pass
        cannot register a late component *correctly*, and registering it
        incorrectly is worse than not registering it at all.

        What is left is to say so. Without this, a component added after the
        first run cycle runs inert -- no rule evaluated, no demand published,
        its outputs frozen at their declared defaults -- and the acyclicity
        check of R30 never sees the connections that arrived with it. Both are
        silent: the run completes and every number it produces is wrong.

        A purely discrete system is untouched: its signature is empty on both
        sides however many components it gains, so 1.x models keep growing
        between runs.

        Raises
        ------
        ModelChangedAfterPrerunError
            Naming what appeared or disappeared since the pre-run step.
        """
        before = getattr(self, "_prerun_signature", None)

        if before is None:
            return

        changes = describe_model_change(before, self.model_signature())

        if not changes:
            return

        raise ModelChangedAfterPrerunError(
            f"System {self.name()}: the continuous-flow model changed after "
            f"the pre-run step ({'; '.join(changes)}). That step runs once, at "
            "the start of the first run: it derives the evaluation order from "
            "the whole connection graph and registers the sweep equations of "
            "every continuous component. It cannot run again -- PyCATSHOO "
            "refuses to re-register an equation its PDMP manager already "
            "holds, and the order is derived globally, so a late component "
            "renumbers equations that can no longer be renumbered. Anything "
            "added since would therefore run inert, contributing nothing and "
            "publishing no demand upstream. Assemble the whole system -- every "
            "component and every connection -- before the first simulate() / "
            "isimu_start()"
        )

    def prerun(self):
        """Run the pre-run step once, before either run entry point starts.

        Idempotent: a second run, or a restart after a stop, is a no-op --
        provided the model is still the one the step ran on. A model that grew
        since is refused by :meth:`check_model_unchanged_since_prerun` rather
        than run with the late part inert.

        A pre-run that RAISED did not run: the flag is set once
        :meth:`prerun_step` has returned, never before it is called. A model
        error is raised by :func:`muscadet.ordering.compute_equation_order`,
        which derives the whole order before a single equation is registered,
        so a refused step leaves the manager exactly as it found it and the
        next entry point re-derives from scratch and reports the same error
        again.

        Setting the flag first made the failure **one-shot instead of the step**:
        a script catching the model error and running again -- which is what a
        notebook, a study driver and an interactive session all do -- got a run
        that completed with no diagnostic at all and zero sweep equations
        registered, presenting the declared defaults as results.

        Returns
        -------
        bool
            True when :meth:`prerun_step` was invoked by this call, False when
            it had already run.

        Raises
        ------
        ModelChangedAfterPrerunError
            When a continuous component or connection appeared since.
        """
        if self.prerun_done:
            self.check_model_unchanged_since_prerun()
            return False

        # Counted per ATTEMPT, which is what it says: a step that raised was
        # invoked, and a model refused twice is honestly reported as two.
        self._prerun_count = self.prerun_count + 1

        self.prerun_step()

        # Both recorded AFTER the step, and only once it has not raised: a
        # system whose pre-run raised -- on a cycle (R30), or on a rate
        # comparison loop -- registered nothing, so it has not run and there is
        # no baseline for a later run to be compared against either.
        self._prerun_done = True
        self._prerun_signature = self.model_signature()

        return True

    @property
    def equation_order(self):
        """The order derived at the pre-run step, or None before it ran.

        A :class:`~muscadet.ordering.EquationOrder`: the graph it was derived
        from, the two sweep sequences, and what each equation was registered
        with. This is the inspection surface -- the derived order is asserted
        directly rather than inferred from simulation output.
        """
        return getattr(self, "_equation_order", None)

    def prerun_step(self):
        """Derive the evaluation order and register the sweep equations.

        Runs once, at the start of the first run: every connection exists and
        no equation has run yet. The continuous-flow graph is read back from
        the engine, sorted twice -- demand in reverse-topological order,
        production in topological order -- and each sweep equation is
        registered with a distinct increasing integer.

        A purely discrete system reaches this too and stays a no-op there: an
        empty graph registers nothing and never creates a PDMP manager.

        Raises
        ------
        muscadet.ordering.ContinuousFlowCycleError
            When the continuous-flow graph is cyclic (R30). Measurement links
            and the discrete control flows built on them are not continuous
            flows and never take part in the check.
        """
        self._equation_order = register_equation_order(self)
        return self._equation_order

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

    def startInteractive(self, *args, **kwargs):
        """Enter interactive mode, preceded by the pre-run step.

        The engine primitive, and the real entry point of the interactive
        path: ``cod3s.pycatshoo.isimu.engine.ISimuEngine.start`` -- which is
        what ``isimu_start_cli`` and the TUI drive -- calls
        ``system.startInteractive()`` directly and never goes through
        :meth:`isimu_start`. Hooked only onto the two wrappers, the pre-run
        step silently did not run there: no equation was registered, every
        sweep was inert, and a Src -> Tank chain reported a level of 0 while
        its source advertised its full rate, with no exception and no
        diagnostic.

        Overriding the primitive rather than adding a third wrapper is what
        makes the hook independent of which of the two an entry point happens
        to call: :meth:`isimu_start` reaches it through
        ``PycSystem.isimu_start``, and :meth:`prerun` is idempotent, so the
        second call is a no-op.
        """
        self.prerun()
        return super().startInteractive(*args, **kwargs)

    def deleteSys(self, *args, **kwargs):
        """Delete the engine system and release everything bound to it.

        Dropping the manager handle, the pre-run flag, the derived order and
        the equation registry is what lets a following test module build a
        clean system in the same process.
        """
        self._pdmp_manager = None
        self._prerun_done = False
        self._prerun_signature = None
        self._equation_order = None
        self._equation_registrations = []
        return super().deleteSys(*args, **kwargs)

    # ------------------------------------------------------------------
    # Connection type checking (AE19)
    # ------------------------------------------------------------------

    def flow_behind_message_box(self, comp_name, mb_name, port):
        """Return the flow object a message-box name designates, or None.

        The resolution the type check of AE19 rests on when it is applied to a
        RAW connection -- :meth:`connect`, which takes message-box names and
        knows nothing about flows. It is deliberately conservative: a box that
        does not resolve to a declared flow of the right direction answers
        None, and a check between two Nones refuses nothing.

        That is what keeps the three legitimate raw connections working:

        * a **measurement link** (``{c}_level_out`` / ``{c}_level_in``, the
          wiring the README prescribes) resolves to no flow at all -- the
          stripped name is ``{c}_level``, which is not a flow key -- so it is
          never judged. A measurement carries a reading, not a quantity, and
          it belongs to neither family;
        * a **logic gate**'s export, which no flow object stands behind;
        * a **trigger** (``{f}_trigger_in``), whose flow object lives in the
          receiving component's ``flows_out`` -- it is an output activated by
          the incoming signal, not an input flow -- and is looked up there.

        Parameters
        ----------
        comp_name : str
            Key of the component in ``self.comp``.
        mb_name : str
            Message-box name, e.g. ``"q_out"`` or ``"q_trigger_in"``.
        port : str
            ``"out"`` for the sending side, ``"in"`` for the receiving one.

        Returns
        -------
        muscadet.flow.FlowModel or None
        """
        comp = self.comp.get(comp_name)

        if comp is None or not mb_name:
            return None

        outgoing = port == "out"
        suffixes = FLOW_OUT_SUFFIXES if outgoing else FLOW_IN_SUFFIXES
        flows = getattr(comp, "flows_out" if outgoing else "flows_in", None) or {}

        for suffix in suffixes:
            if not mb_name.endswith(suffix):
                continue

            flow_name = mb_name[: -len(suffix)]
            flow = flows.get(flow_name)

            if flow is None and suffix == "_trigger_in":
                # The trigger channel of a FlowDiscreteOutOnTrigger: the flow
                # it belongs to is an OUTPUT of the receiving component.
                flow = (getattr(comp, "flows_out", None) or {}).get(flow_name)

            return flow

        return None

    def check_flow_families(
        self,
        source,
        source_flow_name,
        source_flow,
        target,
        target_flow_name,
        target_flow,
    ):
        """Refuse a connection joining a continuous flow to a discrete one (AE19).

        The single implementation of the rule, so that every route into a
        connection reports it identically: the flow layer
        (:meth:`connect_flow`, :meth:`auto_connect`, :meth:`connect_trigger`)
        and the raw :meth:`connect` the README prescribes for measurement links
        and that four shipped examples use for everything else.

        Checked only when BOTH sides resolve to a declared flow. A name
        designating nothing on one side is not a mismatch -- it is a
        measurement link, a logic gate's export, or a connection a model
        author is free to make outside the flow vocabulary.

        Raises
        ------
        ValueError
            Naming both components, both flows and both runtime classes.
        """
        if source_flow is None or target_flow is None:
            return

        source_is_continuous = isinstance(source_flow, FlowContinuous)
        target_is_continuous = isinstance(target_flow, FlowContinuous)

        if source_is_continuous == target_is_continuous:
            return

        source_kind = "continuous" if source_is_continuous else "discrete"
        target_kind = "continuous" if target_is_continuous else "discrete"

        raise ValueError(
            f"Cannot connect {source}.{source_flow_name} "
            f"({source_kind} {type(source_flow).__name__}) to "
            f"{target}.{target_flow_name} "
            f"({target_kind} {type(target_flow).__name__}): "
            "continuous and discrete flows cannot be connected"
        )

    def connect(
        self,
        component_source,
        interface_source,
        component_target,
        interface_target,
        *args,
        **kwargs,
    ):
        """Wire two message boxes, refusing a continuous/discrete mismatch.

        The base-class connection, and the one the README, the shipped examples
        and the measurement-link wiring all use. The type check of AE19 lived
        only inside :meth:`connect_flow`, so this route accepted a discrete
        output feeding a continuous input **silently** -- and a boolean signal
        then reads as a mass flow of one unit per unit time, feeding every
        downstream balance, capacity level and indicator with a quantity
        nothing produced.

        Everything that is not a flow-to-flow connection is untouched: see
        :meth:`flow_behind_message_box` for how a measurement link, a logic
        gate's export and a trigger stay outside the judgement.
        """
        self.check_flow_families(
            component_source,
            interface_source,
            self.flow_behind_message_box(component_source, interface_source, "out"),
            component_target,
            interface_target,
            self.flow_behind_message_box(component_target, interface_target, "in"),
        )

        return super().connect(
            component_source,
            interface_source,
            component_target,
            interface_target,
            *args,
            **kwargs,
        )

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

            # The type check runs on EVERY route into connect_flow, whether or
            # not authorization is being checked: connect_trigger and the raw
            # System.connect() the README prescribes for measurement links wire
            # real quantities too, and a continuous/discrete mismatch is a model
            # error however the connection was declared.
            #
            # Resolved with .get() and checked only when BOTH sides resolve: a
            # flow key naming nothing on one side is not a mismatch, and this
            # lookup must not turn the "not authorized" case below -- which
            # returns None without ever looking at the target -- into a
            # KeyError. That was the 1.x contract for a denied connection.
            source_flow = self.comp[source].flows_out.get(flow_key)
            target_flow = self.comp[target].flows_in.get(flow_key)

            self.check_flow_families(
                source, flow_key, source_flow, target, flow_key, target_flow
            )

            if check_authorization:
                # Indexed, not .get(): a missing source flow raised here in 1.x
                # and must go on raising.
                source_flow_comp_auth_pat = (
                    self.comp[source].flows_out[flow_key].component_authorized
                )
                check_source_auth = self.check_comp_attributes(
                    target, source_flow_comp_auth_pat
                )
                if not check_source_auth:
                    if logger is not None:
                        logger.debug(
                            f"!!! {source} -- {flow_name} --> {target} not authorized by {source}"
                        )
                    return None

                # Indexed for the same reason, and reached only once the source
                # has authorized the target -- exactly where 1.x looked it up.
                target_flow_comp_auth_pat = (
                    self.comp[target].flows_in[flow_key].component_authorized
                )
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
