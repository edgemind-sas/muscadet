import importlib

from .capacity import (
    COMBINE_MAX,
    COMBINE_MEAN,
    COMBINE_MEDIAN,
    COMBINE_MIN,
    COMBINE_POLICIES,
    COMBINE_SUM,
    Capacity,
    CapacityFlow,
    MeasurementIn,
    MeasurementOut,
    combine,
    combine_max,
    combine_mean,
    combine_median,
    combine_min,
    combine_sum,
)
from .declare import (
    ComponentSpecError,
    SystemSpecError,
    build_component,
    build_system,
    check_spec,
    check_system_spec,
    component_spec,
    system_spec,
)
from .engine import (
    ENGINE_ENTRY_POINT_GROUP,
    REFERENCE_ENGINE,
    Engine,
    EngineAlreadyRegisteredError,
    EngineError,
    EngineRunnerMissingError,
    UnknownEngineError,
    get_engine,
    is_reference_engine,
    register_engine,
    registered_engines,
    reset_engines,
    unregister_engine,
)
from .flow import (
    FlowDiscrete,
    FlowDiscreteIn,
    FlowDiscreteOut,
    FlowDiscreteOutOnTrigger,
    FlowDiscreteOutTempo,
    FlowIn,
    FlowOut,
    FlowOutOnTrigger,
    FlowOutTempo,
)
from .flow_continuous import (
    FlowContinuous,
    FlowContinuousIn,
    FlowContinuousOut,
)
from .obj import ObjFlow
from .obj_ctrl import (
    AGGREGATION_CROSSING_CAP,
    AGGREGATION_KINK_POLICIES,
    AGGREGATION_SMOOTH_POLICIES,
    CONTROL_AGGREGATIONS,
    CTRL_BAND_ABOVE,
    CTRL_BAND_BELOW,
    CTRL_BAND_DIRECTIONS,
    CTRL_BOOL_OPERATORS,
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
    CtrlSignalOut,
    ObjCtrl,
    build_ctrl_node,
    crossing_count,
    crossing_pairs,
)
from .obj_logic import LogicAnd, LogicOr, ObjLogicGate
from .ordering import (
    CommandedRateLoopError,
    CommandedRateSelfLoopError,
    ContinuousFlowCycleError,
    ControllerSignalCycleError,
    RateComparisonLoopError,
    RateObservationLoopError,
)
from .profile import Profile, SinusoidalProfile, build_profile
from .rules import Rule, RuleOperand, RuleSet
from .system import ModelChangedAfterPrerunError, System
from .transfer import (
    ConductiveTransfer,
    Transfer,
    TransferPair,
    build_transfer,
    resolve_operand,
)
from .version import __version__


def __getattr__(name):
    """Bind ``muscadet.conformance`` on first use, and only then (PEP 562).

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

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
