# Residual Review Findings — feat/continuous-flows-2.0.0

Six reviewers examined the branch diff against `30a0f97` (correctness, api-contract, adversarial, testing, maintainability, project-standards). The P0 and the four P1 conservation and control defects were fixed in `e617c41`. What follows is what was **not** applied.

No issue tracker is declared for this repository, so this committed file is the durable record.

**Coverage note.** The cross-model adversarial pass did not run: no peer route is configured in this checkout, so no egress allowlist sanctions sending the diff to an external provider. The adversarial lens ran in-process instead. Agreement between reviewers here is agreement between separately dispatched contexts on one model, not across model families.

---

## Correctness and semantics

### R-1. The first sample of a batch schedule over-counts delivery (P2, adversarial) — FIXED

*Fixed.* A producer that has allocated nothing now hands out a **provisional
split** (`FlowContinuousOut.provisional_share`) instead of its raw exported
value, so the shares of one output's consumers never total more than what it
exports. The split is recomputed on every read rather than seeded once: a
component whose `compute_production` writes its exported variable itself never
allocates at all, and a stored seed would freeze there while the variable went
on moving. A demand of zero before the first sweep is read as "not derived yet"
and widened to unbounded, which keeps a guarded consumer from latching on an
idle rule it then derives a zero demand from. `FlowContinuousOut.allocated` is
also dropped at the start of every Monte Carlo sequence, being Python state no
engine reinitialisation touches. Regression tests in
`tests/test_demand_allocation_001.py`; the assertion in
`tests/test_flow_continuous_001.py::test_several_producers_sum` encoded the bug
(it asserted a consumer receiving 3.25 from producers delivering 2.25) and was
corrected to what the run settles at.

Before the first production sweep runs, `FlowContinuousIn.get_delivered` falls back to the producer's raw exported value, because the allocation dict is empty until a production equation has executed — which happens strictly after t=0. Every consumer of one producer therefore reads the producer's whole output at instant 0.

Reproduced: one source at rate 10 feeding two consumers demanding 2 and 3, `schedule=[{start: 0, end: 10, nvalues: 3}]` — both consumers record 10.0 at instant 0.0 (20 units received against 10 produced), correcting to 2.0 and 3.0 from instant 5.0.

**Why this matters more than its severity suggests:** `start: 0` is the documented schedule shape, and the golden-CSV suite is the release oracle. The wrong sample lands in every reference file generated before this is fixed. An interactive session that never advances time sees only the wrong reading.

*Suggested direction:* seed the allocation at t=0 with a start method on the producing component, the way `FlowContinuousIn.update_sensitive_methods` already seeds the input mirror.

### R-2. A rate threshold can close an instantaneous loop the acyclicity check accepts (P2, adversarial) — FIXED

*Fixed.* The first-run check now also walks the discrete channels a comparison
on a continuous **input flow** drives, and refuses a path returning to a
component upstream of that flow whose own production depends on the arriving
signal (`muscadet.ordering.find_rate_comparison_loops`,
`RateComparisonLoopError`, a subclass of `ContinuousFlowCycleError`). The walk
follows a signal through a mode automaton as well as through production
conditions, so a deadband declared over a rate is caught too.

*The suggested deadband exemption was not applied, and the suggestion was
wrong.* Measured on the reported model with `activate=8` / `release=3` against
a source at 10: identical flip dates with and without the band (first at
6.25e-4, ~1400 flips per unit of simulated time either way). A deadband damps a
value that moves *through* the band; a rate jumps across it, crossing both
edges in one step, so the band is never inhabited. The refusal therefore does
not depend on it.

Comparisons on a capacity level read over a measurement link are untouched at
three independent points, so the sensor pattern of AE18 keeps building.
Regression tests, including four near misses that must NOT be refused, in
`tests/test_ordering_001.py`. The constraint is documented in the README beside
the threshold-alarm example.

`R29` refuses only a guard naming a *capacity*, and the acyclicity check drops every non-continuous channel. So a discrete output thresholded on a continuous *rate* and wired back to a producer's guard closes a genuine within-instant loop with no integrated state to break it, and the first-run check passes it.

Reproduced: a source producing 10 while its control port is unfed, feeding a component whose comparison `q >= 5` drives that control port. The graph reports clean, then the model flips regime every 6.25e-4 of simulated time indefinitely — over a realistic horizon, millions of transitions per run. A study that silently never finishes rather than a model that is refused.

The sensor deadband is the sanctioned damping, but nothing steers a modeller to it on this path: the shipped sensor's deadband applies to a capacity level, and the README's alarm example thresholds a rate.

*Suggested direction:* extend the first-run check to walk discrete channels feeding a guard or comparison operand back to their drivers, refusing a path that returns upstream of the continuous edge it reads — lifted when the closing path carries a deadband. Minimum viable alternative: document the constraint in the README.

### R-3. A misspelled parameter on a shipped component is silently ignored (P2, api-contract) — FIXED

*Fixed.* The five components now derive from one
`muscadet.kb.continuous.ContinuousComponent`, whose `add_flows` refuses any
kwargs key outside the accepted set, naming the component, the class and every
key it could not place. Each component declares its own set in
`DECLARATION_KEYS`; the check unions that attribute over the MRO, so a project
subclassing one of the five declares only the key it adds and inherits the
rest.

The accepted set was established empirically rather than guessed, because the
risk to manage is the opposite of the bug: an over-strict check would refuse
working models. `ObjFlow.__init__` consumes `name`, `label`, `description`,
`partial_init` and `create_default_out_automata` in its own signature and puts
`metadata` back into the kwargs before calling `add_flows`; `PycComponent`
swallows everything else without reading it. Instrumenting the five
`add_flows` across the whole suite (`--runslow`), the example and the README
yielded exactly the documented per-class keys plus `metadata`, and nothing
else. A test asserts each class's accepted set equals a declaration exercising
every one of its keys, so a key added without a test fails loudly.

`ALLOCATION_KEYS` is no longer hard-coded: it is derived from
`FlowContinuousOut.model_fields` on the `allocation` prefix, so a fifth
allocation field is forwarded with no second edit. `allocated` — the split the
last sweep computed — is runtime state and deliberately outside the prefix.

Regression tests in `tests/test_kb_continuous_001.py`.

All five components in `muscadet/kb/continuous.py` read declaration keys with `kwargs.get(key, default)` and never reject an unknown key.

Reproduced: `add_component(cls="SourceContinuous", name="SRC", raet=5.0)` builds, connects and simulates without error, delivering 0.0. The same shape silently swallows `content_init`, `fill_rate`, `release` and the four allocation keys — precisely the numeric parameters whose absence is indistinguishable from a legitimate zero.

Everywhere else in this release a malformed declaration is refused at declaration time with an error naming the component and the offending item. The KB layer is the exception.

*Suggested direction:* a per-class accepted-key set, raising on any leftover key.

### R-4. A standalone failure mode targeting a continuous output crashes (P2, correctness) — FIXED

*Fixed by routing it properly, not by refusing it* — the first of the two
suggested options, which turned out to be a local correction. `ObjFailureMode`
now resolves its effects through `resolve_effects_on`, which branches on the
flow family: a continuous output goes through the target's
`add_derating(mode_key, flow_name)`, a discrete one keeps `var_fed_available`,
and an output carrying neither raises naming both. Nothing about the standalone
path forced a design change: the derating variable is allocated on the TARGET
component, which already exists when the mode is declared, and PyCATSHOO
accepts `addVariable` there; the pre-run sweep registration happens later
still, so the new variable is picked up by `get_effective_rate` like any other.

Two things had to follow the routing. The derating key is per AUTOMATON
(`{fm component}__{automaton}`), not per mode, because a second-order mode
builds one automaton per combination of its targets and two of them would
otherwise clamp one variable — the last-writer-wins failure R18 exists to
prevent. And `release_deratings_on` gives the direction that names nothing a
return to nominal, a derating having no per-step reset; it is the standalone
counterpart of `ObjFlow.release_deratings`. The automaton's name is therefore
now computed BEFORE its effects are resolved, which is the only reordering in
the block.

The pattern-matched-nothing guard fires correctly again: `fo_found` used to be
set even where the effect produced was unusable, which suppressed it on exactly
the path that crashed.

Regression tests in `tests/test_standalone_fm_continuous_001.py`; documented in
the README beside the derating section.

Declaring a standalone `ObjFailureModeExp` / `ObjFailureModeDelay` whose effect pattern matches a continuous output aborts construction with `AttributeError: 'NoneType' object has no attribute 'addSensitiveMethod'`, naming neither the component, the flow, nor the derating API that exists for this case. `ObjFailureMode` resolves every effect to `var_fed_available`, which a continuous output never declares.

A pattern like `".*"` on a component mixing both families hits it without the modeller intending to derate anything.

*Suggested direction:* branch on the flow family and route a continuous output through `add_derating`, whose docstring already says it is public so a mode declared outside the component can allocate and target the variable. If that is out of scope, raise an error naming the component, the flow and `add_derating`.

---

## Structure

### R-5. Operand shape validation is duplicated (P1, maintainability + code-reuse)

The three shape rules for a comparison operand — `op` and `value` given together, `op` in the known set, `negate` not combined with a comparison — are implemented twice: as pydantic validators on `RuleOperand`, and by hand inside `ObjFlow.postprocess_flow_specs`. The code comment names the duplication without resolving it.

The module docstring states that a rule guard and a discrete production condition share one comparison vocabulary and one implementation. They do not. The two already word their errors differently, so a future change tightening one would silently diverge the accepted comparisons of the two directions.

Deferred twice during this run because the consolidation must preserve both error wordings exactly. The safe shape is now known: extract the predicate logic into one function taking the label as a parameter, called from both sites.

### R-6. `obj.py` doubled to 3632 lines (P2, maintainability)

The growth is concentrated in two blocks that behave like standalone algorithms over a component: the two-sweep evaluation (~770 lines) and the derating engine (~160 lines). Neither needs to be a method rather than a function taking the component, and this release established that pattern in the sibling modules.

Left alone deliberately: extracting it would move conservation-critical code whose numerical output is validated against reference files, which is not a change to make in the same pass that fixed those defects.

### R-7. Smaller API surface gaps (P3, api-contract)

- `ContinuousFlowCycleError` is a documented first-class model error with structured attributes, but is not exported from the package root — a consumer must catch bare `ValueError` or import a module the public API never mentions.
- No `FlowDiscrete` marker mirrors the exported `FlowContinuous`, so enumerating discrete flows means negating the continuous test, which would mis-label any future third family.
- The pre-run graph walk calls `comp.name()` unconditionally on every entry of the public `comp` dict, while the function three lines later resolves the same objects defensively.

### R-8. Line length in new error messages (P3, project-standards)

Four new f-string error messages in `obj.py` exceed the 88-character convention, one at 212. Black does not wrap string literals, so they will keep exceeding it. Additive to roughly 47 pre-existing over-length lines in the same file.

---

## Test coverage gaps worth closing

- No assertion that a `shares` output with a connected consumer absent from the share map behaves as intended — the configuration `check_allocation` cannot validate, because it runs before any consumer is connected.
- ~~No test reads continuous values at instant 0 of a batch schedule, so R-1 is entirely unasserted despite `start: 0` being the documented shape.~~ Closed with R-1.
- ~~No test exercises a control loop closed by a rate comparison rather than a capacity level, so the claim that the within-instant case is removed is never tested against the path that defeats it (R-2).~~ Closed with R-2.
- ~~No test asserts the five shipped components reject an unknown declaration key (R-3).~~ Closed with R-3.
- ~~No test declares a standalone failure mode against a component carrying continuous outputs (R-4).~~ Closed with R-4.
- No parity test over the two operand-shape validators (R-5).
- The multi-flow capacity's per-constituent bound added in `e617c41` has no watched transition, so a depleted constituent overshoots by one integration step (−0.005 observed against −25.0 before the fix). Acceptable, but the exact-crossing guarantee that holds for the volume bound does not hold per constituent.

## Known limitations, recorded rather than fixed

- An input's demand is computed from the active rule's declared coefficients, so a component capped by a scarce input still claims its nominal demand on the others and over-claims a shared upstream supply. Documented in the plan's Scope Boundaries; correcting it needs a second demand pass or an iterative solve.
- `get_demand_scale` takes the maximum over the active rule's produced coefficients, so a transformer with one unconnected output demands without bound upstream. An unwired output — a vent, or a model still being assembled — silently maximises consumption.
- `accept_limit` is read during the demand sweep but the outflow it reads is written during the production sweep, so a full capacity throttles on the previous evaluation's figure. Inherent to the two-sweep design and absorbed by repeated evaluation, but it is a read before the sweep that writes it.
- The custom allocation extension point never clamps a proposed split's total to what is available; a rule that over-proposes creates quantity.
- The pre-run step is one-shot per engine system, so components added after the first run cycle never have their sweep equations registered and run inert, with no diagnostic.
