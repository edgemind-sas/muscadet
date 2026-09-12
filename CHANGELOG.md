# Changelog

Releases before 5.0.0 are recorded in the git tags (`git tag`, `0.6.x` through
`4.4.0`) and in the commit history; this file starts here rather than
reconstructing them.

## 5.3.1 (2026-09-12)

Maintenance over 5.3.0: one line of `pyproject.toml`, no muscadet code and no
muscadet behaviour. The embedded cod3s ref moves from 1.16.1 to 1.17.0.

It exists for the reason 5.2.1 existed over 5.2.0, and it is the same line: a
downstream consumer cannot raise its own cod3s ref above the one muscadet
embeds, uv refusing to resolve two URLs for the same package, so the two pins
move together or neither does. What the consumer gains is
`SimulationConfig.pdmp_dt`, the base integration step of the continuous solver,
instead of running on PyCATSHOO's own 0.01.

5.3.0 carried 1.16.1 because it was cut from 5.2.0 and the bump lived on
`maint/5.2.x`. That left the declaration of a standalone mode and the
integration step on two branches no tag joined, so a consumer had to choose one
or the other. This release joins them, and is the first tag to carry both.

Validated by running the 5.3.0 suite unchanged against cod3s 1.17.0: 1806
passed, 2 skipped.

## 5.3.0 (2026-09-12)

A system declaration now comes out of every system muscadet can build, and
goes into JSON. Two shapes stopped it before, and both are ordinary rather
than exotic: a component that holds no flow, which is what a mode declared on
its own is, and an audit trail keyed by a pair, which is what the COD3S
Platform importer wrote on every component it built.

### It is cut from 5.2.0, not from 5.2.1

5.2.1 is a maintenance release on `maint/5.2.x`: one line of `pyproject.toml`,
moving the embedded cod3s ref from 1.16.1 to 1.17.0, no muscadet code. This
release does not carry it. It is cut from the branch the declaration was built
on, whose cod3s ref is still 1.16.1, and a consumer has to pin cod3s to the
same ref as the one embedded here -- uv refuses to resolve two URLs for the
same package. So the choice is not "which cod3s is newer" but "which cod3s the
consumer pins", and the consumer this release exists for pins 1.16.1. What
1.17.0 adds is `SimulationConfig.pdmp_dt`, the base integration step of the
continuous solver; whoever needs it moves both refs together, one commit away.

### Added

- **A two-state mode declared as a component declares itself.** `system_spec`
  iterated `flows_in` over everything `system.comp` held, and such a mode
  holds none: two of the six interactive examples died on `AttributeError:
  'ObjFMDelay' object has no attribute 'flows_in'`, from inside a dict
  comprehension, before any engine saw anything. `component_spec` now
  dispatches on what the object IS -- `muscadet.ObjFlow`, `cod3s.ObjMode2S`
  and its subclasses, `cod3s.ObjEvent` -- and refuses anything else by a
  message naming the class. A standalone mode gets its OWN entry rather than
  being skipped: a mode declared ON a component is already written into that
  component's `failure_modes`, but a standalone one is recorded nowhere else,
  so skipping it would lose the model and not the decoration (`cyber_3comp`
  would declare three components and none of the compromise cascade that IS
  the example).
- **`cod3s.ObjEvent` declares itself too, comparison included.** The platform
  translator synthesises one per study event and one per indicator whose
  formula has more than one clause, so it is not a corner of the corpus. The
  six comparisons it compiles to are `operator` module singletons, so identity
  gives the spelling back exactly. `cod3s.ObjDegMode` stays refused by name:
  it holds a list of states rather than two, and no vocabulary fits it.
- **`kind` says which shape an entry is**, and its value for a mode is
  `two_state_mode` (`COMPONENT_KIND_TWO_STATE_MODE`). It covers three families
  -- `cod3s.ObjEvent`, the `cod3s.ObjFM` family and `cod3s.ObjMode2S` -- and
  only the middle one is a failure, so the value names the two states the
  three share rather than the nature of the most common one. Not `mode` nor
  `standalone_mode`: both would collide with `ObjDegMode`, whose constructor
  is not this one. No release ever published another spelling.
- **`component_build_order` and `component_references`** on the package
  surface, so a caller validating a batch reads the function the build reads.

### Changed

- **The COD3S Platform importer writes its three override audit trails as
  documents.** `instance_overrides`, `capacity_overrides` and
  `controller_threshold_overrides` were keyed by the `(name, role)` pair the
  apply layer looks a flow, a capacity or a threshold up by. No JSON object is
  keyed by a pair, so the declaration of ANY imported model stopped at
  `json.dumps`. Each bag is now a sorted LIST of `{"name", "role", "value"}`
  entries. The INTERNAL index is untouched and stays keyed by the pair, which
  is what makes the change small: not one model built changes. **BREAKING for
  a reader of those three metadata keys**, a mapping becomes a list; there is
  no such reader inside muscadet.

### Fixed

- **What muscadet reads back is a document, and it is checked as one.** A
  mapping keyed by a tuple walked through every per-field gate, because every
  VALUE under such a key serialises perfectly well, and died at the moment the
  declaration was written, on a `TypeError` naming a type and neither the
  component nor the field. `_checked_document` states the guarantee once, on
  the two read-back entry points, and names the path
  (`$.components.Rail_1.metadata.instance_overrides`). An integer key is
  refused too, though `json` accepts it: it comes back a string, so what is
  read is not what was written.
- **A declaration rebuilds in any key order.** The key order of a JSON object
  carries no meaning, and a Rust reader over a `BTreeMap` sorts -- which is
  exactly the path this declaration exists for. A mode whose `occ_cond` names
  another mode's state needs that mode to exist at construction, so sorted
  order put the six referencing modes ahead of the six referenced ones and the
  rebuild died on a `KeyError` naming nothing. `build_system` now DERIVES the
  order from what each declaration names, keeping the document's own order
  wherever the references leave it free. A cycle is refused by the names that
  form it, from `check_system_spec`, before the first component is created.

## 5.2.0 (2026-09-09)

muscadet becomes a modelling interface over more than one engine, and says
where an engine does what it defines but does it OTHERWISE. Additive on every
side: a run that names no engine takes the reference path it has always taken,
and nothing inside the package reads the new registry.

This release is also the first tag carrying the system declaration merged on
`master` after 5.1.0 was cut from a side branch.

### Added

- **An engine seam**, `muscadet.engine`. An engine registers itself by calling
  `register_engine`, or by advertising a `muscadet.engines` entry point its
  distribution carries, and a run selects one by name:
  `system.simulate(params, engine="raichu")`. The entry-point route is what
  removes the last import from the CALLER too, so the choice of engine is a
  setting rather than a line of code. What crosses the seam is the SYSTEM
  DECLARATION and never the live system, with run parameters travelling beside
  that document rather than inside it. The reference engine is not a plugin:
  naming `pycatshoo`, or naming nothing, takes the direct path `System` has
  always taken, and registering under that name is refused.
- **A conformance registry**, `muscadet.conformance`. Four semantic points,
  each with the rule muscadet settles on, why, and where it was settled; and
  one entry per engine that departs from it, with what the engine does
  instead, what it costs, and what -- if anything -- restores muscadet's
  meaning before a result is read. Both engines appear on both sides of the
  line: PyCATSHOO fails the first point (it observes an instant BEFORE the
  transitions due at it are resolved), RAICHU honours that one and fails the
  three interactive ones. Consultable with no platform, no model and no run:
  `python -m muscadet.conformance raichu`.
- **A system declares itself**, `muscadet.declare`: `system_spec`,
  `check_system_spec` and `build_system`. `component_spec` already read a
  component back; what no component knows is how it is wired and what is
  observed, which are exactly what stops a declaration from being a system.
  The document carries a semver version, refused by major rather than
  half-read.

### The two registries answer two questions

The conformance registry is NOT the capability matrix. The matrix says what an
engine knows how to do and guards a launch; this registry says where it does
it otherwise and guards nothing. They answer an unknown engine in opposite
directions, which is the shortest proof they are different objects: the matrix
answers "covers nothing", this one answers "nothing to report", and says so
through `is_assessed` rather than letting an empty tuple pass for a clean bill
of health.

That it cannot refuse a launch is structural rather than promised. Nothing
inside muscadet imports the module: the package `__getattr__` binds it on
first ACCESS (PEP 562), so importing muscadet does not even load it. Two tests
assert it, one by source walk and one by `sys.modules` in a subprocess.

### muscadet still imports no engine

The seam knows no engine name, and a test keeps it that way: the package is
parsed and any import beyond its declared dependencies fails it, including one
reached through `importlib.import_module`. It is written as an ALLOWLIST
rather than as a denylist of engine names, so it also refuses the third engine
nobody has written yet. A third engine adds its own conformance record through
`register_deviation` / `assess_engine`; what it cannot do is author the points
it is judged on, or the registry becomes self-certification.

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
