---
title: A multi-flow volumetric machine, and the group demand it publishes - Design note
type: design
date: 2026-09-14
topic: multiflow-volumetric-machine
issue: 4
artifact_readiness: implemented
execution: done
---

# A multi-flow volumetric machine, and the group demand it publishes

Design note for issue #4, *Capacity: a mixture cannot be ventilated, the inflow
bypasses the composition*. Nothing here is implemented. Every number quoted was
measured on this checkout at 5.1.0, with the engine, and the probes are listed
at the end.

Companion figure: `docs/review/2026-09-13-capacite-melange-ventile.svg`.

**Implemented in 5.2.0.** `muscadet/mixture.py`, `Capacity.mixture_share`, the
four sweep touch points, `MixturePumpContinuous`, and
`tests/test_mixture_ventilation_001.py`. Two things moved between this note and
the code, both measured during the work and both recorded in section 9 below.

---

## 1. What the issue reports, and what it misses

The issue reports two defects in `evaluation.draw_from_capacity` and proposes an
opt-in (`mixing=True`) under which the inflow is integrated before the draw is
composed, and the whole request is composed rather than only its excess:

```python
# today
served = min(request, transit + split_draw(beyond))
# asked for
served = min(request, split_draw(sum(requests.values())))
```

Both defects are real and the reading of the code is exact. Two things are
missing.

### 1.1 There is a third per-constituent bound, and it is downstream

`deliver_output` calls `allocate_output`, which caps each consumer's share at
the demand that consumer published, and `set_outflow` then records that cap. So
the per-constituent ceiling does not live only in the draw.

Measured: removing the `min(request, ...)` from `draw_from_capacity` altogether
changes **nothing**. Total leaving the volume 26.294 with the cap and 26.294
without it, identical at every step. On the issue's model the room receives 50
of air and releases 25, so its air content rises from 90 to **340 over ten time
units**, which is also the denominator of the share being reported.

### 1.2 The proposed formula alone does not restore the physics

Measured against the issue's own reference integrator:

| What computes the volume's output | H2 share at t=10, phase 1 | H2 share at t=10, phase 2 | total leaving |
|---|---|---|---|
| reference, well-mixed volume | 0.03829 | 0.00015 | 52 |
| muscadet 5.1.0 | 0.00000 | 0.00274 | 27 |
| the issue's formula | 0.02589 | 0.00073 | 26.3 |
| one volumetric rate, composed | **0.03834** | **0.00018** | **52.000** |

The issue's formula unblocks the rise of the share and leaves the extraction
rate wrong, because the air leg is still capped by its own declared demand.

---

## 2. The hypothesis this note works under

Settled with the maintainer on 2026-09-13, and it is **not** the one the issue's
reference integrator uses. Air enters at `Q`, hydrogen at `q`, and the
**mixture** leaves at `Q`: one single extraction rate, composed at the volume's
own composition.

```
out_AIR = Q (1 - x)
out_H2  = Q x                         x = m_H2 / (m_AIR + m_H2)

dm_AIR/dt = Q - Q(1-x) = Q x
dm_H2 /dt = q - Q x
dM    /dt = (Q + q) - Q = q           the total is NOT conserved
dx    /dt = [ q - (Q+q) x ] / M(t)    M(t) = V + q t
```

Phase 1, supply open, has a closed form:

```
x(t) = q/(Q+q) . [ 1 - (V / (V + q t))^((Q+q)/q) ]
```

The **share plateau is unchanged**, `x* = q/(Q+q) = 0.03846`: it is the ratio of
the inlet rates and does not depend on the extraction rate. The **quantity** of
H2 never plateaus, it grows at `x*.q = 0.0769` per unit, because the volume
itself grows by `q`.

Phase 2, supply cut, `dM/dt = 0` and the share decays exactly:

```
x(t) = x0 . exp(- Q t / M0)           tau = M0/Q = 93.46/50 = 1.869
```

The time constant is `M0/Q`, not `V/Q`: what counts is the total present,
hydrogen included.

**Consequence for the acceptance criterion.** The issue's reproducer integrates
`dH2/dt = q - x(Q+q)`, which assumes the mixture leaves at `Q+q` and the total
is constant. Under the hypothesis above the H2 quantity at t=10 is **4.2078, not
3.5834**. A fix validated against the reproducer as written would be judged
against a reference that does not match the stated hypothesis. The reproducer's
reference has to be corrected along with the fix.

Measured: a model with one volumetric extraction rate `R = Q` reproduces both
closed forms to the displayed digit, over ten steps, in both phases.

---

## 3. The contract

### 3.1 The rate belongs to a machine, not to the volume

An earlier sketch put the rate on `Capacity`, as a common `serve_rate`. Rejected
in favour of a machine, for three reasons that are not stylistic:

- a room does not decide its own ventilation, an extractor does. **Two fans on
  one room** are two components; one field on the capacity is one number for the
  whole volume and cannot express them;
- a pump **fails, derates and is commanded**. All of that already exists on a
  component: failure modes, `{flow}_out_rate`, production conditions. On the
  capacity it would have meant duplicating `serve_rate` and `serve_cond` in a
  "common" variant;
- the same machine serves elsewhere than on a room: a pump on a pipe carrying a
  mixture, a compressor, a blower. A capacity field would only ever have served
  volumes.

### 3.2 The machine declares the rate, the volume composes it

The composition lives in the capacity and nowhere else: a machine displaces
volume and has no way to know `x`. So the contract is cut in two, and this is
the dual of what already exists on the production side, where
`Capacity.split_draw` composes a draw.

```python
comp.add_mixture_in(name="extraction", flows=["AIR", "H2"], volumetric_rate=50.0)
```

Shipped form, in `muscadet/kb/continuous.py`, name to settle:
`MixturePumpContinuous(flows=[...], volumetric_rate=..., ports="in"|"both")`.

`ports="both"` needs nothing new on the way out: the machine's outputs are
ordinary continuous outputs, and the R31 identity transfer carries each
constituent across, so the composition is preserved without a second mechanism.

### 3.3 The weighted split, in one line

`weight` is the volume one unit of a flow occupies. A machine moving `R` volume
per unit time takes a volume `R.phi_f` of each constituent, with
`phi_f = m_f w_f / sum_g m_g w_g`, which in quantity is:

```
out_f  =  R . m_f / sum_g (m_g . w_g)
```

Checks:

- `sum_f out_f . w_f = R`, the volume extracted is exactly `R`;
- with every weight at 1 this is `split_draw`'s raw share, so **nothing existing
  moves**;
- `sum_g m_g w_g` is already an accessor: `current_fill() * capacity`,
  recomputed from the ODE levels rather than read off the explicit `var_fill`,
  so it carries no one-step lag. That accessor exists for this reason and is the
  one to use.

**`R` is a VOLUME rate**, where every other rate in the module (`rate`,
`fill_rate`, `serve_rate`, `var_demand_default`) is a quantity rate. The
declaration key must say so, or a modeller writes 50 expecting 50 of matter.
Hence `volumetric_rate` and not `rate`.

---

## 4. The mechanism, sweep by sweep

### 4.1 The draw: the issue's formula, and why it becomes exact

Once the request arriving at the volume is itself composed, the issue's formula
is **exact**, and it is exact for a reason rather than by luck. With
`req_f = R m_f / S` and `S = sum_g m_g w_g`:

```
sum_f req_f              = R M / S                  M = sum_g m_g
split_draw(R M / S)_f    = (R M / S) . (m_f / M)    split_draw uses the RAW share
                         = R m_f / S = req_f
```

The formula is **idempotent on an already composed request**, and it drops the
transit short-circuit. Measured over both phases and two weightings (1/1 and
1/0.5), it matches the reference to the fifth decimal.

### 4.2 A composed demand alone is not enough either

Measured. A composed group demand run through **today's** draw is exact in phase
1 and about **150 times** the reference in phase 2: share 0.02723 against 0.00018 at
t=10.

The cause is `beyond = sum_f max(req_f - transit_f, 0)`, which sums the
shortfalls of every constituent and then re-composes the sum. When the air
arrives above its request and the hydrogen below its own, the air's surplus
swallows the hydrogen's shortfall, and the hydrogen recovers only its share of a
residue that does not concern it.

**So the work is exactly two pieces, and neither works without the other:**

| Piece | What it gives | Without the other |
|---|---|---|
| the machine, and a group demand composed at the volume | phase 1 exact | phase 2 at ~150x |
| the draw: the issue's formula, without the transit | phase 2 exact | share pinned at zero |

### 4.3 The opt-in moves from the volume to the machine

`mixing=True` on `Capacity` becomes unnecessary, and dropping it is an
improvement rather than a saving. The composed behaviour is **triggered by the
presence of a group consumer**, so:

- there is nothing new to declare on the volume, and no way to set a flag and
  forget the demand side, which would leave a model half-corrected and silent;
- a volume drawn by ordinary per-flow consumers keeps today's transit behaviour,
  which is correct for a mono-flow buffer and is what "the default is unchanged"
  has to mean.

### 4.4 Where the group is resolved, and the risk on this point

A message box carries a float, so a group identity cannot travel on
`{f}_demand`. The proposal is to resolve the group **structurally at the pre-run
step**, which already walks the whole connection graph, derives the equation
order and refuses cycles (`System.prerun`, `model_signature`,
`ModelChangedAfterPrerunError`): for each capacity, the set of consumers drawing
several of its held flows as one group is known there, once, and stored.

This is the least settled part of the note. The alternatives considered:

- **the machine reads the composition** over a `kind="ratio"` measurement link
  and publishes `R(1-x)` and `Rx` itself. Rejected: nothing today lets a demand
  depend on a reading (`var_demand_default` is a constant field read directly in
  `evaluate_demand`, a rule's `cons` is a `Dict[str, float]`, and no controller
  writes a demand), so it needs a new mechanism of the same size; the signal band
  runs **after** production, so the demand of step n would use the composition of
  step n-1; and a fan measures nothing in real life;
- **the capability channel carries the composition**. Rejected: a stocked volume
  publishes `serve_limit`, which is `inf` unless a ceiling is declared, so the
  capabilities of the two constituents are `inf` and `inf` and carry no ratio.

### 4.5 The allocation cap does not dissolve on its own

The composed share `R m_f / S` can **exceed `R`** as soon as a weight is below 1:
a volume holding only H2 at `weight 0.5` gives `S = M/2`, hence a composed share
of `2R`. So publishing `R` on each member flow and letting `allocate_output` cap
at it is not safe in general. The allocation has to know the group and cap at the
composed share, which is what the volume computed.

### 4.6 Acyclicity

A group demand makes the demand published at the volume depend on the volume's
own composition, which is an **integrated state**, not an algebraic value. That
is the same argument `ordering.capacity_breaks_inbound` already rests on (R-14),
so no new cycle is introduced. To be asserted by test rather than assumed.

---

## 5. Refusals to declare

The module's habit is to refuse by name rather than to accept and be silently
wrong. Five:

1. **a group whose flows are not all held by ONE capacity of ONE producer.**
   There is then no composition to compose against, and the demand is undefined;
2. **a held flow drawn by a group and by an ordinary consumer at once.** The
   arbitration between a composed share and a per-flow request is not obvious,
   and a silent wrong answer is the failure mode being fixed. Refused in v1,
   recorded rather than designed;
3. **a group naming a discrete flow, a measurement channel or a capacity.** Same
   grounds as `_resolve_rule_flow`: none of the three carries a conserved
   quantity;
4. **a negative `volumetric_rate`.** A direction is the connection's, as for a
   conduit transfer pair (KD1). Zero is legitimate and means a stopped machine;
5. **a group of one flow is ALLOWED**, and is meaningful: `out = R/w`, a
   volumetric pump on a single-species line. No special case needed.

---

## 6. Impact

**On existing models: none.** No model declares a group; `split_draw` is
untouched; the weighted composition is a new function reached only through a
group; the draw's transit behaviour is unchanged for every per-flow consumer.
This is measurable rather than asserted: the full suite must be green with no
test modified.

**On the issue's reproducer:** it has to be rewritten to declare the machine,
**and** its reference integrator corrected to the `Q`-out hypothesis, per 2
above. Both halves, or the criterion is wrong by construction.

**On the documentation:** a section in the module's `CLAUDE.md` beside the
capacity and transfer-pair material, README, and the figure refreshed.

---

## 7. What stays open

- the group identity at the producing end (4.4) is the design risk of this note;
- a group spanning **two producers**, which is refused in v1;
- what a group consumer's **capability** is, and what the machine publishes
  downstream when it discharges;
- whether a group should also be expressible on the **output** side, which no
  case so far requires.

---

## 8. What the implementation changed about this note

Two things, both measured rather than reasoned, and both now in the shipped
documentation.

**A machine carrying its draw onward destroys matter, so it is refused.** The
note assumed a `ports="both"` shape would need nothing of its own, the R31
identity transfer carrying each constituent across. It conserves only while the
outlet asks for at least what the machine draws. Measured on a pump at
`volumetric_rate=50` behind a load asking 5: drawn 49.16 of air, delivered 5,
and **44.16 per unit of time entered no balance**. The group draws the whole
volumetric rate, what leaves is capped by the demand downstream, and nothing
bounds the one by the other. Bounding it needs the machine's downstream demand
to reach the volume that holds the composition, which is a mechanism of its own.
Refused by name at declaration, and `MixturePumpContinuous` carries no
`ports="both"`.

**The opt-in left the volume entirely.** Section 4.3 proposed dropping
`mixing=True`; the implementation confirms it costs nothing: the composed
behaviour is triggered by the presence of a group consumer, read off
`Capacity.serves_a_mixture`, which the pre-run resolution writes. A volume drawn
per flow keeps the 5.1.0 behaviour byte for byte, which the suite measures both
ways -- 1627 tests unchanged and green, plus 27 new.

**What section 4.5 predicted held.** The composed share does exceed `R` when a
weight is below 1, and `allocate_output` had to be told: a volume holding
hydrogen alone at `weight 0.5` has an occupied volume of half its raw total,
hence a share of `2 R`.

---

## 9. Probes

Written for this note, kept out of the repository, under the session scratchpad:

| Probe | What it measured |
|---|---|
| `repro4.py` | the issue's reproducer, reproduced unchanged on this checkout |
| `probe4.py` | per-constituent inflow, outflow, level and demand at each step |
| `probe_fix.py` | the issue's formula, with and without its per-flow cap |
| `probe_c.py`, `probe_c2.py` | one volumetric rate composed at the mixture |
| `probe_q.py` | the `Q`-out hypothesis against its closed form, both phases |
| `probe_group.py` | a composed group demand through three draw rules, weights 1/1 and 1/0.5 |
