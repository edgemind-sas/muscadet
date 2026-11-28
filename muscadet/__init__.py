"""muscadet: a declarative description of flow systems, and the engines that run it.

Importing this package never fails for want of an engine.

- The DECLARATIVE part (:mod:`muscadet.declare`, :mod:`muscadet.engine`,
  :mod:`muscadet.importers`, :mod:`muscadet.profile`, the controller grammar)
  loads on a machine where PyCATSHOO is not installed.
- The EXECUTION part (``ObjFlow``, ``System``, the flow and capacity classes)
  drives PyCATSHOO. Where the engine is installed it is loaded at import, as
  it always was, so every component class is registered and resolves by its
  ``cls`` name. Where it is not, reaching one of its names raises an
  ``ImportError`` naming PyCATSHOO.
"""

import importlib

from .combine_policies import (
    combine,
    COMBINE_MAX,
    combine_max,
    COMBINE_MEAN,
    combine_mean,
    COMBINE_MEDIAN,
    combine_median,
    COMBINE_MIN,
    combine_min,
    COMBINE_POLICIES,
    COMBINE_SUM,
    combine_sum,
)
from .ctrl_grammar import (
    AGGREGATION_CROSSING_CAP,
    AGGREGATION_KINK_POLICIES,
    AGGREGATION_SMOOTH_POLICIES,
    build_ctrl_node,
    CONTROL_AGGREGATIONS,
    crossing_count,
    crossing_pairs,
    CTRL_BAND_ABOVE,
    CTRL_BAND_BELOW,
    CTRL_BAND_DIRECTIONS,
    CTRL_BOOL_OPERATORS,
    CTRL_IN_SERVICE,
    CTRL_LOGIC_AND,
    CTRL_LOGIC_K,
    CTRL_LOGIC_NOT,
    CTRL_LOGIC_OR,
    CTRL_LOGICS,
    CTRL_OP_BAND,
    CTRL_OP_COMBINE,
    CTRL_OP_COMPARE,
    CTRL_OP_REPUBLISH,
    CTRL_OPERATORS,
    CTRL_OUT_BOOL,
    CTRL_OUT_KINDS,
    CTRL_OUT_VALUE,
    CTRL_VALUE_OPERATORS,
    CtrlBand,
    CtrlCombine,
    CtrlCompare,
    CtrlNode,
    CtrlRepublish,
)
from .declare import (
    build_component,
    build_system,
    check_spec,
    check_system_spec,
    component_build_order,
    COMPONENT_REFERENCE_KEYS,
    component_references,
    component_spec,
    ComponentSpecError,
    declared_events,
    declared_occurrence_state,
    GENERATED_INDICATORS,
    generated_indicators,
    GENERATED_INDICATORS_DEFAULT,
    system_spec,
    SystemSpecError,
)
from .engine import (
    check_run_targets,
    component_builder,
    ComponentBuilderMissingError,
    discovery_failure,
    Engine,
    ENGINE_ENTRY_POINT_GROUP,
    EngineAlreadyRegisteredError,
    EngineError,
    EngineRunnerMissingError,
    find_component_builder,
    get_engine,
    is_reference_engine,
    isimu_start_declaration_on,
    REFERENCE_ENGINE,
    register_engine,
    registered_engines,
    reset_engines,
    run_declaration_on,
    run_target_names,
    RUN_TARGETS,
    run_targets,
    RunTargetError,
    simulate_declaration_on,
    UnknownEngineError,
    unregister_engine,
)
from .initial_state import InitialFixpointError
from .controller import ObjCtrl
from .logic_gate import ObjLogicGate
from .profile import (
    build_profile,
    PiecewiseLinearProfile,
    Profile,
    SinusoidalProfile,
    TrapezoidProfile,
)
from .rules import (
    Rule,
    RuleOperand,
    RuleSet,
)
from .version import __version__

#: Engine-bound public names, each with the submodule that defines it.
_ENGINE_EXPORTS = {
    "Capacity": "capacity",
    "CapacityFlow": "capacity",
    "MeasurementIn": "capacity",
    "MeasurementOut": "capacity",
    "UnboundedRateError": "evaluation",
    "FlowDiscrete": "flow",
    "FlowDiscreteIn": "flow",
    "FlowDiscreteOut": "flow",
    "FlowDiscreteOutOnTrigger": "flow",
    "FlowDiscreteOutTempo": "flow",
    "FlowIn": "flow",
    "FlowOut": "flow",
    "FlowOutOnTrigger": "flow",
    "FlowOutTempo": "flow",
    "FlowContinuous": "flow_continuous",
    "FlowContinuousIn": "flow_continuous",
    "FlowContinuousOut": "flow_continuous",
    "MixtureDraw": "mixture",
    "MixtureGroupError": "mixture",
    "MixtureIn": "mixture",
    "ObjFlow": "obj",
    "LogicAnd": "obj_logic",
    "LogicOr": "obj_logic",
    "CommandedRateLoopError": "ordering",
    "CommandedRateSelfLoopError": "ordering",
    "ContinuousFlowCycleError": "ordering",
    "ControllerSignalCycleError": "ordering",
    "RateComparisonLoopError": "ordering",
    "RateObservationLoopError": "ordering",
    "ModelChangedAfterPrerunError": "system",
    "System": "system",
    "ConductiveTransfer": "transfer",
    "Transfer": "transfer",
    "TransferPair": "transfer",
    "build_transfer": "transfer",
    "resolve_operand": "transfer",
}

#: The execution submodules, reachable as attributes of the package.
#: ``obj_ctrl`` now holds only the names of the controller grammar and is
#: kept here for that reach.
_ENGINE_SUBMODULES = (
    "capability",
    "derating",
    "mode_signals",
    "capacity",
    "evaluation",
    "flow",
    "flow_continuous",
    "mixture",
    "obj",
    "obj_ctrl",
    "obj_logic",
    "ordering",
    "system",
    "transfer",
)


def _engine_import(module, name):
    """Import an execution submodule, naming PyCATSHOO when it is what is missing."""
    try:
        return importlib.import_module(f".{module}", __name__)
    except ImportError as error:
        from cod3s.pycatshoo import EngineNotAvailableError, is_missing_engine

        if is_missing_engine(error) and not isinstance(error, EngineNotAvailableError):
            raise EngineNotAvailableError(
                f"'muscadet.{name}' needs the PyCATSHOO engine, which could not "
                f"be imported ({error}). The declarative part of muscadet "
                "(muscadet.declare, muscadet.engine, muscadet.importers) works "
                "without it."
            ) from error
        raise


def __getattr__(name):
    """Resolve what this package does not bind at import (PEP 562).

    Two kinds of name land here.

    **The execution layer**, where PyCATSHOO is not installed: a name of
    ``_ENGINE_EXPORTS`` or an execution submodule. It raises an ``ImportError``
    naming PyCATSHOO. Where the engine is installed these names are bound at
    import by :func:`_load_execution_layer` and never reach this function.

    **``muscadet.conformance``**, bound on first use, and only then.

    The conformance registry says where an engine departs from what muscadet
    defines. Three things follow from resolving it lazily rather than importing
    it at the top of this file, and all three are the point rather than a
    micro-optimisation:

    - it is reachable the way a reader expects, ``import muscadet`` then
      ``muscadet.conformance.describe("raichu")``, without being flattened in
      beside ``ObjFlow`` and ``FlowContinuousIn`` where it would read as one
      more modelling primitive. It is a statement ABOUT the library, not part
      of its vocabulary;
    - importing muscadet does not import it, so the registry is a leaf of the
      package graph with no exception at all -- which is what makes "nothing
      inside muscadet can refuse a run on the strength of a divergence" a
      property of the code rather than a promise
      (``tests/test_conformance_registry_001.py``);
    - ``python -m muscadet.conformance <engine>``, the consultation the README
      documents, runs clean. Imported here, the module would already be in
      ``sys.modules`` when runpy re-executes it, and every consultation would
      open on a RuntimeWarning about unpredictable behaviour.

    Imported through ``importlib`` and not with ``from . import conformance``:
    the ``from`` form asks the import machinery for the attribute once the
    submodule is loaded, which lands back in this function and recurses until
    the stack gives out. ``import_module`` returns the module and binds it on
    the package itself, so this runs exactly once.
    """
    if name == "conformance":
        return importlib.import_module(f".{name}", __name__)

    if name in _ENGINE_EXPORTS:
        value = getattr(_engine_import(_ENGINE_EXPORTS[name], name), name)
    elif name in _ENGINE_SUBMODULES:
        value = _engine_import(name, name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    globals()[name] = value
    return value


def __dir__():
    # Only what is bound: without the engine the execution names are not
    # listed, so a tool walking ``dir()`` does not trip on them. Ask
    # ``ENGINE_AVAILABLE`` rather than ``hasattr`` to detect the engine.
    return sorted(globals())


def _load_execution_layer():
    """Bind the whole execution layer now. False when PyCATSHOO is missing."""
    from cod3s.pycatshoo import is_missing_engine

    try:
        for name in (*_ENGINE_SUBMODULES, *sorted(_ENGINE_EXPORTS)):
            if name not in globals():
                __getattr__(name)
    except ImportError as error:
        if is_missing_engine(error):
            return False
        raise
    return True


#: Whether the execution layer is loaded, i.e. PyCATSHOO was importable when
#: this package was.
ENGINE_AVAILABLE = _load_execution_layer()
