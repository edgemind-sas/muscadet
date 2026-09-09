# Changelog

Releases before 5.0.0 are recorded in the git tags (`git tag`, `0.6.x` through
`4.4.0`) and in the commit history; this file starts here rather than
reconstructing them.

## 5.0.1 (2026-09-09)

Maintenance on the 5.0 line, one line of pyproject and nothing else: the
embedded cod3s ref moves from 1.16.1 to 1.17.0.

No muscadet code changed, and no behaviour of muscadet changed. The release
exists because a downstream consumer cannot raise its own cod3s ref above the
one muscadet embeds: uv refuses to resolve two URLs for the same package, so
the two pins move together or neither does.

What the consumer gains is `SimulationConfig.pdmp_dt`, which lets a study set
the base integration step of the continuous solver. Until it existed nothing
anywhere set that step, so every study ran on PyCATSHOO's own 0.01 and paid
five hundred network re-evaluations per unit of simulated time. muscadet is
where the step is spent -- the solver calls back into its equations -- but not
where it is chosen.

Validated by running the 5.0.0 suite unchanged against cod3s 1.17.0: 1584
passed, 2 skipped. The single commit 1.17.0 adds over 1.16.1 is additive, an
optional field read between the system build and the simulation.

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
- **A `serve_rate` or a `serve_cond` on `CapacityContinuous(ports="in")`**, an
  accumulator: it declares no output and no rule, so nothing would ever read
  them. The refusal is on that knowledge-base class; a hand-written component
  declaring an equally inert volume through `add_capacity` is not yet refused.
- **A capacity carries upstream only what it may release.** The demand a volume
  publishes to its producer is capped by its `serve_rate` and its `serve_cond`,
  on top of the `fill_rate` it claims for itself. Without the cap a buffer at
  `fill_rate=0` became an accumulator the moment a ceiling was declared:
  measured, a volume at `serve_rate=40` between a source of 100 and a load of
  100 rose by 60 per unit of time.
- `capacity_breaks_inbound` no longer tears an edge when the volume's discharge
  condition reads the arriving flow: what leaves then depends algebraically on
  what arrives, so the level no longer stands between the two, and the loop is
  refused rather than given an evaluation order.

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
