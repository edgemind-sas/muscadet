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
now computed BEFORE its effects are resolved, which was the only reordering in
the block. (Since R-9 the resolution happens inside `_build_fm_automaton`,
which the engine calls with the name already settled — the same ordering,
obtained structurally instead of by hand.)

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

### R-5. Operand shape validation is duplicated (P1, maintainability + code-reuse) — FIXED

*Fixed by the suggested shape, which held up.* The three rules now live once,
in `muscadet.rules`, as `check_operand_pairing`, `check_operand_operator` and
`check_operand_negation`, composed by `validate_operand_shape`. Both sites call
it: `RuleOperand.check_operand_shape` and `ObjFlow.postprocess_flow_specs`.

Two parameters carry the difference, not two implementations: the **label** to
prefix the message with, and the phrase naming what a comparison is on that side
(`"a numeric operand"` for a guard, `"a comparison against a continuous
quantity"` for a production condition, `GUARD_COMPARISON_KIND` /
`PROD_COND_COMPARISON_KIND`). Both wordings are byte-identical to what they were
— checked by rendering every message both sides can raise, at `75f8027` and
after, and diffing.

Two things the suggestion did not anticipate, and which were left alone rather
than papered over:

- **`RuleOperand.check_op` stays a field validator.** Moving the operator check
  into the model validator would have reordered it against the pairing check,
  changing which of two applicable errors an operand with an unknown operator
  AND a missing value reports. The field validator now delegates to the shared
  function, and the model validator calls the composite — the operator check
  runs twice, harmlessly, and the observable order is unchanged on both sides.
- **The `port` check is NOT shared.** It is a fourth rule the finding does not
  name, and the two messages differ structurally rather than only in label (the
  guard separates with a colon, the production condition does not). Unifying it
  would have changed a wording rather than removed a duplication. The
  divergence is recorded in a comment at both sites.

Parity tests in `tests/test_operand_shape_parity_001.py`: the two directions
accept and refuse the same ten bad and nine good shapes, for the same reason,
and the two wordings are pinned in full.

### R-6. `obj.py` doubled to 3632 lines (P2, maintainability) — FIXED

*Fixed, as a pure move.* The two blocks are now modules of their own, as
functions over a component — the shape `muscadet.ordering` and
`muscadet.capacity` already use:

- `muscadet/evaluation.py` (883 lines), the two-sweep evaluation, 23 functions;
- `muscadet/derating.py` (219 lines), the derating engine, 6 functions.

`ObjFlow` binds each under its own name in a class-body assignment
(`compute_demand = evaluation.compute_demand`), so every call site, every
`super()` chain and every override point is untouched — which matters
particularly for `compute_demand` and `compute_production`, which
`muscadet.ordering` looks up BY NAME. `obj.py` went from 3799 lines at
`75f8027` to 2840.

**The derating mechanism's semantics were not touched.** One variable per
(automaton, output), composed by minimum at read time, written and released
exactly when they were before: the move is provably byte-for-byte, so it cannot
have changed them.

Two independent verifications, because "the tests still pass" is not the bar for
moving conservation-critical numerical code:

1. **Textual.** Each of the 29 moved functions is the method's source at
   `75f8027` with one indentation level removed and `self` renamed to `comp` —
   nothing else. 27 are character-for-character identical; the other 2
   (`evaluation.apply_production`, `derating.match_continuous_outputs`) are
   black re-wrapping a line that gained four columns of headroom at module
   level, and parse to an identical AST. Checked by re-deriving them from the
   reference checkout and diffing, plus asserting each name is no longer
   *defined* on `ObjFlow` and resolves to the module's own function object.
2. **Numerical.** Every number the two sweeps and the derating engine compute,
   over every model the whole suite builds, recorded through wrappers on the 29
   entry points and written out at full `repr` precision: **8 373 107 records**,
   before and after, byte-identical (sha256 `5e52f429…`). The method was
   validated first by capturing the trace twice at `75f8027` and confirming it
   is bit-reproducible.

### R-7. Smaller API surface gaps (P3, api-contract) — FIXED

- `ContinuousFlowCycleError` and its subclass `RateComparisonLoopError` are
  exported from the package root. A consumer catches
  `muscadet.ContinuousFlowCycleError`, and one `except` covers both shapes of
  first-run refusal.
- `muscadet.FlowDiscrete` mirrors `FlowContinuous`: a pure marker declaring no
  field, inserted between `FlowModel` and the two canonical roots
  (`FlowModel -> FlowDiscrete -> FlowDiscreteIn -> FlowIn`, and the same on the
  output side). Every 1.x `isinstance` relation still holds and the legacy names
  keep their place inside the canonical chain, so the family is now told apart
  by a positive test instead of by negating the continuous one.
  `tests/test_flow_rename_aliases.py::test_inheritance_chain_shape` asserted the
  exact `__bases__` tuple of the canonical roots; it was updated to pin the new
  link as well — the chain gained a root, the guarantee it protects did not
  weaken.
- The pre-run graph walk resolves engine names through
  `ordering.engine_name_index`, shared by the two walks that both built the same
  index, and skipping an entry with no callable `name()` the way the surrounding
  code already resolves `system.comp` defensively.

Tests in `tests/test_ordering_001.py` and `tests/test_flow_rename_aliases.py`.

### R-8. Line length in new error messages (P3, project-standards) — FIXED

*Closed as a side effect of R-5, which is what they were.* The four
branch-added over-length lines in `obj.py` — at 212, 187, 169 and 143 characters
— were exactly the four production-condition operand messages. Three moved into
`muscadet.rules`, wrapped; the fourth (`'value' must be a number`) was wrapped in
place. `git diff 30a0f97 -- muscadet/obj.py` now reports **zero** added lines
over 88. The ~47 pre-existing ones were left alone.

### R-9. `ObjFailureMode*` was a fork of `cod3s.ObjFM*` (P2, maintainability) — FIXED

*Fixed by re-converging on the engine, not by re-deriving it.* The three
classes reimplemented the whole `cod3s.ObjFM` family on top of
`cod3s.PycComponent`: the same template hook names (`set_occ_law_failure`,
`get_failure_cond`, `set_default_failure_param_name`, …), the same common-cause
combinatorics, the same `trans_name_prefix` mechanics, the same per-order
parameter naming. They are now subclasses of it:

```
cod3s.ObjMode2S -> cod3s.ObjFM      -> muscadet.ObjFailureMode
                   cod3s.ObjFMExp   -> muscadet.ObjFailureModeExp(ObjFailureMode, cod3s.ObjFMExp)
                   cod3s.ObjFMDelay -> muscadet.ObjFailureModeDelay(ObjFailureMode, cod3s.ObjFMDelay)
```

The double base keeps both `isinstance(fm, ObjFailureMode)` — the 1.x relation
— and `isinstance(fm, cod3s.ObjFMExp)`; C3 linearises it as
`[ObjFailureModeExp, ObjFailureMode, ObjFMExp, ObjFM, ObjMode2S, …]`, which is
the order the condition composition needs (the muscadet dict shorthand
compiles *under* `ObjFMExp`'s rate gate, not over it). `obj.py` lost 723 lines;
what replaced them is `muscadet/failure_mode.py`, whose executable content is
five methods.

**The seam for the regex effects.** muscadet spells its effects as a regular
expression over the target's **flow** names, resolved to what each match
offers a mode (the availability variable of a discrete output, the derating
variable of a continuous one). The engine resolves effect keys as **exact
variable basenames**, inline in `ObjMode2S.__init__`, with no override point —
so the two contracts could not be reconciled there. The seam used instead is
`_build_fm_automaton`, the documented extension hook of the ObjFM family,
called once per built combination: the engine is handed **empty** effect dicts
(it would refuse a flow name as an unknown variable), the declared ones are
resolved in the hook, and they are restored onto `fm.failure_effects` /
`fm.repair_effects` — legacy views on the engine's storage — once construction
is over.

The hook carries the automaton's *name* but not the combination it was built
for, which the resolution needs (the effects bear on that combination's targets
only). The combination is recovered from the name through
`cod3s.pycatshoo.fm_wiring.cc_comb_suffix`, the same helper the engine names it
with, fed the same `trans_name_prefix` / `trans_name_prefix_fun` — so the
inversion cannot drift from the naming. It is exercised on both the
format-string path (`tests/test_comp_failure_008/009.py`) and the callable path
with dropped orders (`tests/test_comp_failure_010.py`, 5 targets, 16 of 31
combinations built).

**Nothing about the deratings changed.** One variable per (automaton, output),
keyed `{fm component basename}__{automaton}`, folded by minimum at read time,
alongside the shared `{flow}_out_rate` of KD10. `resolve_effects_on` and
`release_deratings_on` moved without an edit.

**Name identity, verified mechanically** — the bar R-6 set. A pytest plugin
wrapping `PycComponent.add_aut2st` and `PycSystem.deleteSys` records, for every
model the suite builds: every automaton name, its state names, its transition
names and their occurrence laws (down to the parameter *variable* each law
points at), every effect record with its component-qualified target variable
and value, and every component's full variable list. 977 records, confirmed
bit-reproducible by capturing it twice before the change. Diffed before and
after: **every valid model is byte-identical**, 7 of the 977 records differ, in
two classes, neither of them a name:

1. `cond_12` / `cond_21` on the four `ObjFailureModeDelay` automata whose
   condition is a bare `True` go from the literal `True` to a callable
   returning it. That is `cod3s.ObjFM`'s deliberate late-bound constant, which
   makes rebinding `fm.failure_cond` after construction take effect; the native
   `cod3s.ObjFMDelay` path in this same suite already behaves that way. Exact
   parity was reachable only by introspecting which subclass hook wrapped the
   condition, which is more fragile than the delta it would remove.
2. Three components in `tests/test_objfailuremode_deprecation.py` — modes
   deliberately declared against a component that does not exist, whose
   exception the test swallows — lose their `lambda` / `mu` / `ttf` / `ttr`
   variables. The engine's `_dry_run_resolve_effects` refuses a missing target
   *before* allocating anything, instead of leaving a half-built component
   behind. See the limitation recorded below.

Regression tests in `tests/test_fm_cod3s_engine_001.py` (hierarchy, the regex
spelling, the name inversion under a custom prefix, the derating key, the dict
condition, the refused keywords). No pre-existing test file was modified.

---

## Test coverage gaps worth closing

- No assertion that a `shares` output with a connected consumer absent from the share map behaves as intended — the configuration `check_allocation` cannot validate, because it runs before any consumer is connected.
- ~~No test reads continuous values at instant 0 of a batch schedule, so R-1 is entirely unasserted despite `start: 0` being the documented shape.~~ Closed with R-1.
- ~~No test exercises a control loop closed by a rate comparison rather than a capacity level, so the claim that the within-instant case is removed is never tested against the path that defeats it (R-2).~~ Closed with R-2.
- ~~No test asserts the five shipped components reject an unknown declaration key (R-3).~~ Closed with R-3.
- ~~No test declares a standalone failure mode against a component carrying continuous outputs (R-4).~~ Closed with R-4.
- ~~No parity test over the two operand-shape validators (R-5).~~ Closed with R-5.
- The multi-flow capacity's per-constituent bound added in `e617c41` has no watched transition, so a depleted constituent overshoots by one integration step (−0.005 observed against −25.0 before the fix). Acceptable, but the exact-crossing guarantee that holds for the volume bound does not hold per constituent.

## Known limitations, recorded rather than fixed

- An input's demand is computed from the active rule's declared coefficients, so a component capped by a scarce input still claims its nominal demand on the others and over-claims a shared upstream supply. Documented in the plan's Scope Boundaries; correcting it needs a second demand pass or an iterative solve.
- `get_demand_scale` takes the maximum over the active rule's produced coefficients, so a transformer with one unconnected output demands without bound upstream. An unwired output — a vent, or a model still being assembled — silently maximises consumption.
- `accept_limit` is read during the demand sweep but the outflow it reads is written during the production sweep, so a full capacity throttles on the previous evaluation's figure. Inherent to the two-sweep design and absorbed by repeated evaluation, but it is a read before the sweep that writes it.
- The custom allocation extension point never clamps a proposed split's total to what is available; a rule that over-proposes creates quantity.
- The pre-run step is one-shot per engine system, so components added after the first run cycle never have their sweep equations registered and run inert, with no diagnostic.
- **(R-9)** A standalone failure mode declared against a component that does not exist yet now raises at construction (`Mode 'X': target component 'Y' not found in the system. Create the targets before the mode.`) instead of building a parameter-less shell. Inherited from the engine's fail-fast resolution, and a fix rather than a regression — but a model that declared its modes before their targets *and* left every occurrence rate at zero used to build silently and no longer does.
- **(R-9)** `behaviour`, `failure_effects_trans` and `repair_effects_trans` are refused by `ObjFailureMode*`. The engine routes all three through its exact-variable-name effect resolution, which a muscadet flow pattern never matches, so accepting them would build a silently effect-less mode. Supporting them would mean giving `ObjMode2S` an overridable effect-resolution hook for the external and trans-based paths — the same seam the level effects found in `_build_fm_automaton`, which those two paths do not go through. `cod3s.ObjFM*` covers them today.
