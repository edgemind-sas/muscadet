# Changelog

Releases before 5.0.0 are recorded in the git tags (`git tag`, `0.6.x` through
`4.4.0`) and in the commit history; this file starts here rather than
reconstructing them.

## 5.1.0 (2026-09-07)

The COD3S Platform bridge carries the two fields 5.0.0 added to a capacity.
Additive on every side: no model, no knowledge base and no spec written against
5.0.0 changes behaviour.

### Added

- **`serve_rate` and `serve_cond` on a `capacities` entry** of a COD3S Platform
  class template, and `serve_cond_inner_mode` beside them. The importer keeps a
  closed key allowlist, so a knowledge base carrying a commanded battery was
  REFUSED by name rather than imported with its command dropped -- the loud
  failure, but a failure all the same: the platform surface could not be built
  at all.
- **`_SUPPORTS_CAPACITY_SERVE_COMMAND`**, the capability marker the platform
  probes before exposing the two fields, the eleventh of the family.
- `CapacitySpec` carries `serve_rate`, `serve_cond` and `serve_cond_inner_mode`.
  An undeclared field stays `None` (`()` for the condition) and reaches
  `add_capacity` as an ABSENCE: the two rate defaults are opposite -- a
  `fill_rate` of 0 claims nothing, a `serve_rate` of `inf` caps nothing -- so a
  default invented here would be a declaration nobody wrote.

### One grammar, not two

`serve_cond` is written in the nested operand form `prod_cond` already uses on
a port, down to the shapes muscadet normalises: a bare operand becomes one
group of one, and an operand found where a group was expected becomes a group
of its own. A parse layer accepting only the strictest form would teach a
second grammar by refusing what the engine accepts.

**`serve_cond_inner_mode` defaults to `"and"` here and not to muscadet's own
`"or"`**, and that is the point rather than an oversight. The importer has read
`logic_inner_mode` as `"and"` since the platform's 3.0.0 schema, outer-OR /
inner-AND, which is what the knowledge-base editor displays. Left to the engine
default, one nested list would mean a disjunction of conjunctions on a port and
the converse on a volume. Declared explicitly, `"or"` still selects the other
reading; declared without a `serve_cond` to qualify, it is refused, exactly as
`logic_inner_mode` without a `prod_cond` is.

### Refused at the parse layer, naming the class

Refused here rather than left to muscadet, whose `ValueError` names a muscadet
field on a muscadet class and tells a knowledge-base author nothing about the
class they wrote:

- a `serve_rate` that is negative or NaN, read by the same predicate as
  `fill_rate` because the engine validates the two with one predicate;
- a `serve_cond` operand naming no interface of the class, or naming one of the
  class's own capacities -- refused as a capacity, since a condition names the
  FLOW a volume holds and never the volume. Refused whichever order the two
  capacities are declared in;
- an operand carrying an unsupported comparison operator, a comparison with no
  value, a value with no operator, or a `negate` beside a comparison -- the
  three shape rules `muscadet.rules.validate_operand_shape` enforces. The third
  was previously left to the engine, which reports it as a *production
  condition* operand for something the author declared as a discharge command;
- `serve_cond_inner_mode` outside `and` / `or`, the empty string included. An
  unset select serialised as `""` reads as *declared* to the "no `serve_cond`
  to qualify" guard and would have read as *absent* to the default, taking
  outer-OR / inner-AND without ever meeting the closed set;
- **a `serve_rate` or a `serve_cond` on a volume nothing draws from**: no held
  flow is a continuous output of the class, and no rule set consumes one, so
  neither field would ever be read. `CapacityContinuous` refuses the same shape
  by name on `ports="in"`; this bridge builds plain `ObjFlow` components, so
  without the check the model imported clean, wired its command port, and the
  declaration changed nothing at any point of the run. The two routes out are
  those `muscadet.ordering.commanded_discharge_outputs` enumerates -- the
  identity transfer of a same-named output, and a rule set consuming the held
  flow -- so a pass-through and a hopper releasing into its rules both keep
  building.

### Accepted, where a rule guard refuses

**A command operand may name a flow a capacity shares its name with.** The two
vocabularies are one, but the engine resolves them through two functions that
disagree on this single point: `_resolve_rule_flow` tests the capacities first
and refuses outright (R29), while `apply_prod_cond` resolves `flows_in` then
`flows_out` and never looks at a capacity. `add_capacity(name=X, flow=X)` is
the spelling R49 calls the most natural there is, so mirroring the guard's
refusal onto a command would refuse a model the engine builds. `_OperandSite`
records both halves in one place rather than leaving the sites to look
interchangeable.

### Unchanged, and worth knowing

- **A per-instance override of the two fields does not exist.** As with
  `fill_rate`, they are declared on the class template. An attribute carrying an
  unregistered role is logged and dropped by `_build_overrides_index`, so such
  an override is visible in the log rather than silent, but it does not apply.

## 5.0.0 (2026-09-07)

The batch of three defects living in the discrete/continuous interoperation:
a boolean operand that did not say what it meant, an instantaneous loop nothing
refused, and a capacity that could be neither rated nor commanded.

**A major release because two shapes that build in 4.4.0 are refused at
startup.** They are listed first and on their own, because they are the whole
of the migration: every other refusal below needs one of the two new fields and
therefore cannot touch a model written against 4.4.0. The refusal is the
intended behaviour in both cases, the algebraic dependency having always been
there with only the diagnostic missing, but a model that ran and no longer
starts is breaking from the caller's point of view however good the reason.

### Breaking: what can refuse a 4.4.0 model

- **A rate commanded by a threshold on its own observed value.** A continuous
  output gated by a production condition on an observed quantity (R44, 4.3.0),
  whose verdict leaves as that same rate and is observed back onto itself.
  Refused with `muscadet.CommandedRateLoopError`.
  *Rewrite*: observe a **capacity level** rather than a rate. A level is
  integrated and carried between instants, so it breaks the loop; put a volume
  between the producer and the reading and threshold its level.

- **A boolean operand naming a continuous flow now compares.** Written
  `{"name": "q"}` on a rate, an operand meant "differs from zero" and behaved
  that way; it is now normalised to `q != 0` at resolution, with a negated one
  becoming `q == 0`. The verdict is unchanged on every float, and four things
  around it are not:

  - the operand becomes **visible to the loop detectors**, so a model gating a
    producer on a boolean read of a rate arriving from it is refused where it
    built. *Rewrite*: as above, threshold a level;
  - it is read **live** instead of from a mirror refreshed between integration
    steps;
  - its crossing gains a **watched automaton**, so it fires at the crossing
    rather than at the following step. An interactive session spends its first
    `isimu_step_forward` settling that automaton at t = 0 and reaches the first
    dated transition on the second: a driver stepping a fixed number of times,
    or asserting which transition fires first, sees a change with no change of
    its own;
  - the **stored form changes**, so `muscadet.component_spec` writes
    `{"name": "q", "op": "!=", "value": 0.0}` where 4.4.0 wrote `{"name": "q"}`.
    A golden-file comparison of an exported spec, or a byte-for-byte platform
    round trip, sees it.

  Measured before the change over 80 condition sites across this repository,
  its knowledge bases, its tests and examples, and the platform backend: **zero**
  boolean operands on continuous flows. The migration is expected to be empty
  in practice; it is listed because it cannot be guaranteed empty elsewhere.

### Added

- **`serve_rate` on a capacity**: a ceiling on the rate a volume releases, per
  held flow, `math.inf` by default. It is a *ceiling*, not the twin of
  `fill_rate`, which is a *claim*: one caps what leaves, the other asks for
  what enters. `{capacity}_serve_rate_{flow}` is a public variable created at
  the declared value that a failure mode clamps by name to throttle a
  discharge, exactly as `{flow}_out_rate` does for a continuous output.
  MUSCADET never writes it. The ceiling bounds the delivery *and* the
  capability, and holds on both sides of a capacity.
- **`serve_cond` on a capacity**: a condition commanding what a volume
  releases, in the operand vocabulary a production condition already uses. This
  is the half a production condition cannot reach: that one gates what a
  component *produces*, and what leaves a volume is *stock*, so a control
  signal wired to a battery gated its charge and never its discharge. Composes
  with `serve_rate` by **branching**, never by product (`inf * 0` is NaN, which
  would poison every level downstream instead of stopping it).

  A commanded halt stops the **discharge**, and whether the charge survives it
  is a matter of declaration: a volume carries upstream what it may release
  plus what its `fill_rate` claims for itself, so at the documented default
  `fill_rate=0` the halt stops the way in as well. Declare the `fill_rate` a
  battery meant to charge while shed really has. The capability announces zero
  either way, so nothing downstream sizes itself as though the volume were
  pouring.
- **`control` and `control_logic` on `CapacityContinuous`**: `control` declares
  a discrete input the `serve_cond` then names, and gates nothing by itself,
  which keeps what the port does visible in the condition rather than implied
  by the key. `control_logic` is that port's input logic, as `add_flow_in`
  takes it, defaulting to `"and"`: two redundant command lines wired to one
  capacity therefore both have to hold unless an `"or"` is declared.
- **`muscadet.CommandedRateLoopError`**, a subclass of
  `RateObservationLoopError` and therefore of `ContinuousFlowCycleError`. A
  caller narrowing on `RateObservationLoopError` to report "a threshold on an
  observed rate drives a discrete signal" now also catches this shape, where no
  discrete signal exists: narrow on `CommandedRateLoopError` first if the two
  need telling apart.
- **`muscadet.CommandedRateSelfLoopError`**, a direct subclass of
  `ContinuousFlowCycleError`. One `except muscadet.ContinuousFlowCycleError`
  still catches every first-run refusal.
- `isort` is declared among the development dependencies. It was configured in
  `pyproject.toml` and documented as a command, and was not installed.

### Refused on the new fields

None of these can touch a 4.4.0 model: each needs `serve_rate` or `serve_cond`.

- **A discharge commanded by a reading of the rate it itself serves.** A
  `serve_cond` naming the continuous output that discharge feeds. It crosses no
  connection at all, which is why no walk reports it. Refused with
  `CommandedRateSelfLoopError`.
  *Rewrite*: command the discharge on something the volume does not itself
  produce, a boolean signal or a capacity level read over a measurement link.
- **A discharge commanded by a signal derived from the rate that volume
  delivers.** The same physics, with the verdict travelling as a discrete
  signal. Refused with `RateComparisonLoopError` or `RateObservationLoopError`
  depending on how the threshold reaches the rate. All three refusals now name
  the **capacity** the command is declared on, not only the component.
  *Rewrite*: derive the command from a **capacity level** instead of a rate,
  or command a volume that produces no part of what the threshold reads, which
  is what a battery backing a plant on one bus does.
- **A `serve_rate` or a `serve_cond` on `CapacityContinuous(ports="in")`**, an
  accumulator: it declares no output and no rule, so nothing would ever read
  them. The refusal is on that knowledge-base class; a hand-written component
  declaring an equally inert volume through `add_capacity` is not yet refused.
  *Rewrite*: declare `ports="both"` to give the volume a way out, or drop the
  field.
- **A capacity carries upstream only what it may release.** The demand a volume
  publishes to its producer is capped by its `serve_rate` and its `serve_cond`,
  on top of the `fill_rate` it claims for itself. Without the cap a buffer at
  `fill_rate=0` became an accumulator the moment a ceiling was declared:
  measured, a volume at `serve_rate=40` between a source of 100 and a load of
  100 rose by 60 per unit of time.
  *Rewrite*: declare the `fill_rate` the buffer really has. A volume that is
  meant to accumulate says so, rather than accumulating out of a demand it
  cannot honour.
- `capacity_breaks_inbound` no longer tears an edge when the volume's discharge
  condition reads the arriving flow: what leaves then depends algebraically on
  what arrives, so the level no longer stands between the two, and the loop is
  refused rather than given an evaluation order.
  *Rewrite*: command the discharge on a **level** rather than on the flow the
  volume buffers, which is what puts an integrated state back between the two
  ends.

### Still accepted, and worth knowing

- **A discharge thresholded on a rate arriving at its own component.** The
  comparison and the command then sit on one component, so no edge of the graph
  runs between them and no signal leaves; what closes is the demand the volume
  publishes upstream, capped by what it may release. Such a montage admits two
  fixpoints when the threshold sits between the two regimes, and the solver
  keeps whichever the first evaluation reached. Threshold a **level** and the
  question does not arise.
- **Two sibling producers on one bus**, with a controller observing one and
  commanding the other, which is the shape a battery backing a plant takes.
  Sibling coupling runs sideways through a shared demand and traverses no edge,
  so neither detector sees it; widening the criterion would refuse the ordinary
  main-plus-backup shape.

### Fixed

Repairs to behaviour **released in 4.4.0 or earlier**:

- `SignalConnection` carries the name a signal **arrives** under, so a loop
  closed through a cross-named connection is rendered with the box that exists
  rather than one that does not.

Repairs to code introduced **within this release**, listed for the record
rather than for migration, no released version having carried the defect:

- a capacity's discharge condition is read by the **five** places the operand
  vocabulary is walked, not four: seeding, gating, and the taint fixpoint where
  a capacity is a *hop* rather than a seed or a gate;
- an infinite `serve_rate` is omitted from a component spec. `json` writes a
  non-finite float as the non-standard literal `Infinity`, which would have
  made every capacity spec fail a strict reader, including those of models
  declared before the field existed.
