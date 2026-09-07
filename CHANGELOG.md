# Changelog

## 5.0.0

The batch of three defects living in the discrete/continuous interoperation:
a boolean operand that did not say what it meant, an instantaneous loop nothing
refused, and a capacity that could be neither rated nor commanded.

A major release because **two of the three refuse at startup models that build
today**. The refusal is the intended behaviour in both cases: the algebraic
dependency was always there, only the diagnostic was missing. A model that ran
and no longer starts is breaking from the caller's point of view, however good
the reason.

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
  would poison every level downstream instead of stopping it). A commanded halt
  stops the discharge, not the volume: charging stays available and the
  capability announces zero.
- **`control` on `CapacityContinuous`**: declares a discrete input the
  `serve_cond` then names. It gates nothing by itself, which keeps what the
  port does visible in the condition rather than implied by the key.
- **`CommandedRateLoopError`** and **`CommandedRateSelfLoopError`**, both
  subclasses of `ContinuousFlowCycleError`, so one `except
  muscadet.ContinuousFlowCycleError` still catches every first-run refusal.
- `isort` is declared among the development dependencies. It was configured in
  `pyproject.toml` and documented as a command, and was not installed.

### Now refused at startup

Each of these built and ran before. For each, what to write instead.

- **A rate commanded by a threshold on its own observed value.** A continuous
  output gated by a comparison on an observed quantity, whose verdict leaves as
  that same rate and is observed back onto itself. Refused with
  `CommandedRateLoopError`.
  *Rewrite*: observe a **capacity level** rather than a rate. A level is
  integrated and carried between instants, so it breaks the loop; put a volume
  between the producer and the reading and threshold its level.

- **A discharge commanded by a reading of the rate it itself serves.** A
  `serve_cond` naming the continuous output that discharge feeds. It crosses no
  connection at all, which is why no walk reported it. Refused with
  `CommandedRateSelfLoopError`.
  *Rewrite*: command the discharge on something the volume does not itself
  produce, a boolean signal or a capacity level read over a measurement link.

- **A discharge commanded by a signal derived from the rate that volume
  delivers.** The same physics as the two above, with the verdict travelling as
  a discrete signal. Refused with `RateComparisonLoopError` or
  `RateObservationLoopError` depending on how the threshold reaches the rate.
  All three refusals now name the **capacity** the command is declared on, not
  only the component.
  *Rewrite*: as above, threshold a level rather than a rate.

- **A boolean operand naming a continuous flow now compares.** Written
  `{"name": "q"}` on a rate, an operand meant "differs from zero" and behaved
  that way; it is now normalised to `q != 0` at resolution, with a negated one
  becoming `q == 0`. The verdict is unchanged on every float, but the operand
  becomes visible to the loop detectors, so a model gating a producer on a
  boolean read of a rate arriving from it is refused where it built.
  *Rewrite*: as above. The refusal names the loop.

  Two further consequences of the same normalisation, on models that keep
  building: the operand is read **live** instead of from a mirror refreshed
  between integration steps, and its crossing gains a **watched automaton**, so
  it fires at the crossing rather than at the following step. An interactive
  session spends its first `isimu_step_forward` settling that automaton at
  t = 0 and reaches the first dated transition on the second: a driver stepping
  a fixed number of times, or asserting which transition fires first, sees a
  change with no change of its own.

### Changed

- **A capacity carries upstream only what it may release.** The demand a volume
  publishes to its producer is now capped by its `serve_rate` and its
  `serve_cond`, on top of the `fill_rate` it claims for itself. Without the cap
  a buffer at the documented default `fill_rate=0` ("a pure pass-through
  buffer, it never stocks up") became an accumulator the moment a ceiling was
  declared: measured, a volume at `serve_rate=40` between a source of 100 and a
  load of 100 rose by 60 per unit of time. This only bites a model that
  declares one of the two new fields, every other model being unchanged.
  *Rewrite*: declare the `fill_rate` the buffer really has.
- **A `serve_rate` or a `serve_cond` on an accumulator is refused**
  (`CapacityContinuous(ports="in")`): it declares no output and no rule, so
  nothing would ever read them.
- `capacity_breaks_inbound` no longer tears an edge when the volume's discharge
  condition reads the arriving flow: what leaves then depends algebraically on
  what arrives, so the level no longer stands between the two. Reachable only
  with a `serve_cond`, so again no existing model moves.

### Fixed

- A capacity's discharge condition is read by the **five** places the operand
  vocabulary is walked, not four: seeding, gating, and the taint fixpoint where
  a capacity is a *hop* rather than a seed or a gate.
- An infinite `serve_rate` is omitted from a component spec. `json` writes a
  non-finite float as the non-standard literal `Infinity`, which would have
  made every capacity spec fail a strict reader, including those of models
  declared before the field existed.
- `SignalConnection` carries the name a signal **arrives** under, so a
  cross-named connection is rendered with the box that exists.
