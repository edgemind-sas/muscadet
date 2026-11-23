# API reference

Generated from the docstrings of the sources. Every name listed here is
importable from the package root (`import muscadet`), except the knowledge
base classes, which live in `muscadet.kb`.

| Page | Contents |
|---|---|
| [System](system.md) | `System`: components, connections, simulation, the pre-run step |
| [Components](components.md) | `ObjFlow`, the logic gates |
| [Discrete flows](flows-discrete.md) | `FlowDiscreteIn`, `FlowDiscreteOut` and their variants |
| [Continuous flows](flows-continuous.md) | `FlowContinuousIn`, `FlowContinuousOut` |
| [Rules](rules.md) | `Rule`, `RuleOperand`, `RuleSet` |
| [Capacities and measurements](capacities.md) | `Capacity`, `MeasurementIn`, `MeasurementOut` |
| [Mixtures](mixtures.md) | `MixtureIn`, `MixtureDraw` |
| [Time profiles](profiles.md) | `Profile` and the shipped shapes |
| [Transfer pairs](transfers.md) | `Transfer`, `ConductiveTransfer`, `TransferPair` |
| [Failure modes](failure-modes.md) | `ObjFailureMode`, `ObjFailureModeExp`, `ObjFailureModeDelay` |
| [Controllers](controllers.md) | `ObjCtrl` |
| [Declaring from data](declare.md) | `build_component`, `component_spec`, `build_system` |
| [Engines and conformance](engines.md) | engine registry, run targets, known deviations |
| [Knowledge bases](kb.md) | `muscadet.kb.rbd`, `muscadet.kb.continuous` |
