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

## Silent wrongness, promoted out of "known limitations"

Both entries below were recorded rather than fixed at the time. They share the
property this branch has been bitten by twice: neither produces an error, both
produce a **quietly wrong number**. They were closed for that reason.

### R-10. An unconnected continuous output made a component demand without bound (P2, correctness) — FIXED

*Fixed by dropping it from the maximum, not by forbidding it.* An output
nothing is connected to is asked for nothing, and
`FlowContinuousOut.get_demand_bound` reports that absence as `inf` — "nobody is
throttling me". `get_demand_scale` fed that straight into a **maximum** over the
active rule's `prod` coefficients, so one unwired output out-voted every
connected one and the component claimed its whole upstream supply.

Reproduced: a source able to supply 10, feeding a transformer whose `good`
output is consumed at 5 and whose `vent` output is unwired, alongside a rival
consumer also asking for 5. The transformer published `inf` upstream, took
6.67 of the 10 under the proportional policy and left the rival — which asked
for exactly what it needed — short at 3.33. It now publishes 5 and the two get
5 each.

The filter is `ObjFlow.output_constrains_demand` (in `muscadet/evaluation.py`,
bound in `obj.py`), and it is **structural**: it asks whether anything can ask
this output for a quantity, never what the demand's value is. That distinction
is the whole design, and it keeps three cases apart:

- **no connection** — dropped. A deliberately unwired output is a legitimate
  model (a vent), so it is neither refused nor allowed to demand;
- **connected, demanding zero** — kept, at scale zero. "Nobody is asking" and
  "somebody is asking for nothing" are different models;
- **an `inf` published BY a connected consumer** — kept, and still unbounded: a
  capacity claiming its fill rate (R36) means "deliver whatever you can", and
  reading the value instead of the wiring would have silently thrown that away.

A **discrete** output named in a `prod` map is dropped on the same grounds: a
boolean production is not a quantity and carries no demand channel. That was
the same defect on a second path, previously untested.

**The behavioural consequence, stated plainly.** A rule whose outputs are *all*
unconnected now reaches the `not scales` branch and runs at its **nominal**
scale — claiming exactly its declared `cons` coefficients — where it previously
ran at whatever its supply allowed. That is what closes the limitation for a
single-output transformer, and it is not free: nine branch-added tests
depended on the old semantics, in models whose outputs were left unwired
because the test only ever read `var_fed`.

None of them was weakened. Eight were corrected by declaring the downstream
they had been getting implicitly — a consumer with a large demand, which
restores byte-identical behaviour and states out loud what an acceptance
example about a *delivered* quantity ought to say
(`tests/test_rules_guards_001.py` AE1–AE6 and `PLAIN`,
`tests/test_rules_eval_001.py` `BOTTLER`, `tests/test_derating_001.py`
`ZEROED`, `tests/test_out_rate_native_mode_001.py` `ZEROED`,
`tests/test_discrete_continuous_interop.py` `MIXED` on both entry points). The
ninth,
`tests/test_demand_allocation_001.py::test_a_catalyst_coefficient_demands_nothing_and_publishes_no_nan`,
**encoded the defect**: its docstring opened with "the rule's output is
connected to nobody, so nothing throttles it and the scale it would run at is
unbounded" and it asserted `math.isinf(demand_fuel)`. Its subject — a `cons`
coefficient of 0 claiming a real 0 rather than `0 × inf` = NaN — is untouched
and still asserted; the three assertions that were consequences of the unbounded
premise were corrected to the fixed values, and the `0 × inf` arithmetic itself
was **moved, not dropped**, to a scenario where it survives R-10: a connected
consumer publishing an unbounded demand.

**Closed on the rule-LESS path too, after a decision.** `evaluate_demand`'s
other branch — the R31 identity transfer of a component declaring no rule —
carried an output's demand across unchanged, unbounded one included. Measured
before the fix: a pass-through pipe whose output is unwired, sharing a source
of 10 with a consumer asking for 5, took 6.67 to the consumer's 3.33 — the same
shape as the fixed case. The filter does not transpose mechanically, a transfer
having no declared coefficients and therefore no nominal scale to fall back to,
so what a rule-less pipe asks for when nothing consumes it — 0, what arrives,
or unbounded — was put to the user as a design decision.

**Decided: nothing.** An unwired output constrains nothing on both paths. The
argument is consistency rather than physics. Read as physics the unbounded
answer is the faithful one, an open pipe end being a discharge to atmosphere —
but leaving it there would mean one physical arrangement answers differently
depending on whether the modeller happened to write a rule, which is the part
no user could be expected to predict. A deliberate vent stays modellable, and
more legibly, by declaring the discharge as a consumer with its own demand.

Two facts settled it beyond the consistency argument. The unbounded reading was
never actually implemented: `regularize_demands` rewrites `inf` as the whole
quantity available before any split, so the pipe was treated as having asked
for exactly the supply — a true unbounded demand would have taken all 10, not
6.67. And the artefact scales: two dangling pipes both regularise to 10, driving
the real consumer from 3.33 down to 2.0. The share a wired consumer received
depended on how many unconnected outputs existed elsewhere in the model.

Regression tests in `tests/test_passthrough_unwired_demand_001.py`; the AE9
model in `test_rules_eval_001.py` now wires the sink it was previously drawing
through an unwired output, with its asserted values unchanged.

Regression tests in `tests/test_unconnected_output_demand_001.py` (10 tests, 6
of which fail against `dabc2b1`). Documented in the README beside the
transformation-rule key points.

### R-11. A component added after the pre-run step ran inert, with no diagnostic (P2, correctness) — FIXED, as a refusal

*Established experimentally that it cannot be fixed any other way, then
refused.* The pre-run step derives the equation order from the whole connection
graph and registers the sweep equations; `_prerun_done` makes it one-shot. A
continuous component added afterwards was never registered.

Reproduced: `SRC -> MID -> SNK` run interactively, then a converter branch
`MID -> CONV -> SNK2` added and the session restarted. Nothing is registered for
`CONV` or `SNK2`, `CONV` produces **0.0** against 3.0 for the same model built in
one go, and `MID` demands **4.0** upstream instead of 10.0 — so the component
that was already there produces less too. The run completes normally. Worse, a
continuous **cycle** closed after the first run is not refused either: the R30
check lives in the same one-shot step, and the same graph that raises
`ContinuousFlowCycleError: MID -> MID2 -> LOOP -> MID closes a loop` when the
check is allowed to run simulates happily when it is not.

**Why a refusal and not an incremental pre-run.** Measured against the engine,
not read off the documentation:

1. `addEquationMethod` on a `(component, method)` the manager already holds
   **raises**: `PycException: [E]L'ODE SNK1.compute_demand appartient déjà au
   PDMP muscadet_pdmp`. `IPDMPManager` exposes no removal counterpart
   (`addAlgebraicVariable`, `addEquationMethod`, `addExplicitVariable`,
   `addODEVariable`, `addWatchedTransition`, … and nothing that removes). A
   manager's equation set is append-only.
2. Registering *new* equations after a run **is** accepted — verified — but only
   at orders above every one already taken, since the taken ones cannot be
   moved. The order is derived globally from the graph, so a late component
   does not merely add equations: it renumbers existing ones, and those are
   exactly the registrations that can no longer be redone.
3. Appending therefore places a demand equation above production equations,
   breaking the band separation `get_output_request` documents and relies on
   ("the WHOLE demand band below the WHOLE production band … every demand in
   the system is settled before the first production equation runs"). Verified:
   with the branch appended by hand, `MID2.compute_demand` landed at order 6
   while `MID.compute_production` held 4.

So the equivalence the acceptance bar asked for — a late-built model behaving
identically to the same system built in one go — is not reachable, and a
half-ordered registration is worse than none. `System.prerun` now calls
`check_model_unchanged_since_prerun`, which compares `model_signature()` — the
graph nodes and continuous connections, read back from the engine — against the
one recorded when the step ran, and raises `ModelChangedAfterPrerunError`
naming what changed:

```
System Plant: the continuous-flow model changed after the pre-run step
(components added since: CONV, SNK2). That step runs once, at the start of the
first run: ... Assemble the whole system -- every component and every
connection -- before the first simulate() / isimu_start()
```

It fires on **both** entry points, since both go through `prerun`, and it also
catches a *connection* added between two components that both already existed —
a new edge renumbers the order exactly as a new node does. Three things are
deliberately untouched: an unchanged restart is still the silent no-op it always
was (`prerun_count` stays 1), a purely discrete system has an empty signature on
both sides and keeps growing between runs exactly as in 1.x, and a first pre-run
that raised — on a cycle (R30), on a rate comparison loop — records no
signature, so the check stands aside rather than reporting a spurious change on
top of the real defect.

Regression tests in `tests/test_prerun_late_component_001.py` (10 tests, 6 of
which fail against `dabc2b1`). Documented in the README under "Assemble the
whole system before the first run".

---

## Test coverage gaps worth closing

- No assertion that a `shares` output with a connected consumer absent from the share map behaves as intended — the configuration `check_allocation` cannot validate, because it runs before any consumer is connected.
- ~~No test reads continuous values at instant 0 of a batch schedule, so R-1 is entirely unasserted despite `start: 0` being the documented shape.~~ Closed with R-1.
- ~~No test exercises a control loop closed by a rate comparison rather than a capacity level, so the claim that the within-instant case is removed is never tested against the path that defeats it (R-2).~~ Closed with R-2.
- ~~No test asserts the five shipped components reject an unknown declaration key (R-3).~~ Closed with R-3.
- ~~No test declares a standalone failure mode against a component carrying continuous outputs (R-4).~~ Closed with R-4.
- ~~No parity test over the two operand-shape validators (R-5).~~ Closed with R-5.
- ~~No test wires a rule with one connected and one unconnected output, so the unbounded-demand path is unasserted (R-10).~~ Closed with R-10.
- ~~No test adds a continuous component after a first run cycle, so the inert-component path is unasserted (R-11).~~ Closed with R-11.
- The multi-flow capacity's per-constituent bound added in `e617c41` has no watched transition, so a depleted constituent overshoots by one integration step (−0.005 observed against −25.0 before the fix). Acceptable, but the exact-crossing guarantee that holds for the volume bound does not hold per constituent.

## Known limitations, recorded rather than fixed

- An input's demand is computed from the active rule's declared coefficients, so a component capped by a scarce input still claims its nominal demand on the others and over-claims a shared upstream supply. Documented in the plan's Scope Boundaries; correcting it needs a second demand pass or an iterative solve.
- `accept_limit` is read during the demand sweep but the outflow it reads is written during the production sweep, so a full capacity throttles on the previous evaluation's figure. Inherent to the two-sweep design and absorbed by repeated evaluation, but it is a read before the sweep that writes it.
- The custom allocation extension point never clamps a proposed split's total to what is available; a rule that over-proposes creates quantity.
- **(R-10)** A rule-**less** pass-through whose output is unwired now transfers nothing, where it previously drew whatever its supply allowed. Same semantic change as the entry below, on the path that has no coefficients to fall back to, and settled by the same rule: an unwired output never makes its component compete upstream. A pipe modelling a discharge must declare the discharge as a consumer with its own demand.
- **(R-10)** A rule whose outputs are **all** unconnected now runs at its nominal scale: it claims exactly its declared `cons` coefficients and no longer draws an excess supply. That is the price of closing the single-output case of the limitation, and it is a real semantic change — a model relying on "an unwired output takes whatever you make" must now say so, by wiring a consumer and declaring its demand. A consequence rather than a defect: the alternative, keeping the unbounded fallback when no output constrains, would have left a one-output transformer maximising consumption, which is the limitation itself.
- **(R-11)** A system carrying continuous flows can no longer be extended between runs at all — the refusal covers a late connection as well as a late component, and there is no opt-out. Extending a model means building a fresh `System`. A purely discrete system is unaffected. The check costs one graph read per `simulate()` / `isimu_start()`, which an interactive session that starts and stops repeatedly pays each time.
- **(R-9)** A standalone failure mode declared against a component that does not exist yet now raises at construction (`Mode 'X': target component 'Y' not found in the system. Create the targets before the mode.`) instead of building a parameter-less shell. Inherited from the engine's fail-fast resolution, and a fix rather than a regression — but a model that declared its modes before their targets *and* left every occurrence rate at zero used to build silently and no longer does.
- **(R-9)** `behaviour`, `failure_effects_trans` and `repair_effects_trans` are refused by `ObjFailureMode*`. The engine routes all three through its exact-variable-name effect resolution, which a muscadet flow pattern never matches, so accepting them would build a silently effect-less mode. Supporting them would mean giving `ObjMode2S` an overridable effect-resolution hook for the external and trans-based paths — the same seam the level effects found in `_build_fm_automaton`, which those two paths do not go through. `cod3s.ObjFM*` covers them today.
