---
title: The control grammar as an ObjFlow declaration - design note
type: design
date: 2026-09-15
topic: ctrl-grammar-objflow
artifact_readiness: framing
execution: none
---

# The control grammar as an ObjFlow declaration

Framing note for the fusion the maintainer asked about on 2026-09-15: can the
sensor and the controller live behind ONE object, configured per instance to be
a sensor, an automaton, or both? Nothing here is implemented. Everything quoted
was measured the same day, by grafting `ObjCtrl`'s output machinery onto a live
`ObjFlow` at runtime and running a model through it. The probe stands beside
this note: `2026-09-15-001-ctrl-grammar-objflow-probe.py`. It ran against
`master` (2c273ce, 5.1.0), which makes it a baseline result rather than one
carried by a feature branch.

---

## 1. The question, and the direction that holds

The question as asked was: one generic `ObjCtrl`, configured per instance. The
measured answer inverts the direction, and the inversion is what makes it
cheap.

**Fusing BEHIND `ObjCtrl`** (every component becomes a controller) is the
reading the foundation commit already took and refused (78848a3, "a controller
is a peer of ObjFlow, not a subclass"). A peer carries no `flows_out`, no
`connect_flow` route and no `add_flows` / `set_flows` cycle; inheriting would
have been a surface break, not a reimplementation. Nothing measured here
reopens that.

**Porting the grammar ONTO `ObjFlow`** is the inverse, and it is the shape the
rest of the continuous side already has. Everywhere else, the mechanism lives
in a declaration held by the component and the KB classes only fill it:
`capacities`, `rule_sets`, `transfers`, `measurements_in` / `measurements_out`,
`mixtures`. The controller is the one mechanism that lives in a CLASS. Moving
it into a declaration makes a producing component able to compute its own
verdict, and turns `ObjCtrl` into a thin component declaring only those two
sections, and `SensorContinuous` into a template like the other seven.

**What the foundation argument actually protects survives untouched.** "A
controller carries information rather than a conserved quantity" is an argument
about CHANNELS: an observation link never enters the continuous-flow graph,
which is what makes a measurement free for the allocation and for the
acyclicity check. Fusing the classes does not fuse the channels. The
`controller_signal_links` sort already treats an `ObjFlow` instrument and a
controller as ONE population for the same reason: what the order guarantees is
about the publication, not about the class publishing it.

## 2. What the probe measured

| Fact | Figure |
|---|---|
| methods borrowed from `ObjCtrl`, UNCHANGED | 31 |
| containers they need | 11 dicts, one set, one flag |
| attribute collisions between the two classes | 1 |
| doors missing for self-gating | 1 |
| verdict crossing, band 20/50 | switches between the observations at 24 and 18 |

- **The graft is mechanical.** Not one line of the emit machinery was
  rewritten. The component builds, registers its control equation at the
  pre-run step, and its watched band automaton fires at the threshold. The
  volume is the work; the risk is not.
- **There is exactly ONE collision, and it IS the fusion.** `ObjFlow` writes
  `measurements_in` as a dict; `ObjCtrl` exposes it as a read-only property
  aliasing `controls_in`. Two names for one concept, both already typed
  `Dict[str, MeasurementIn]`. Resolving toward unification is one line; the
  question is only which name survives (section 5).
- **The verdict computes inside the component, and the crossing is dated.**
  The probe's pump watches a tank through a band 20/50; the verdict flips
  between the observation at 24 and the one at 18.
- **Exactly one door is missing.** `var_prod_cond=["enough"]` is refused:
  `Object PUMP: Flow enough does not exist as input nor output flow (you must
  create it before using it in a FlowOut condition)`. The operand resolution
  looks in `flows_in`, then `flows_out`, then, for COMPARISONS only, in
  `measurements_in`. A control output is none of the three.
- **What is NOT yet proven: production actually gated by the verdict.** The
  probe computes the verdict; it does not throttle the pump with it. The door
  is the remaining unknown and the first thing an implementation must prove.

**The modelling gain, per level-gated production:** today three components,
one measurement link and one control-flow connection; fused, two components
and one measurement link.

## 3. The contract

```python
class Pump(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_control_in(name="tank")
        self.add_control_out(
            name="enough", kind="bool",
            emit={"op": "band", "input": "tank",
                  "direction": "below", "activate": 20.0, "release": 50.0},
        )
        self.add_flow_continuous_out(name="q", var_fed_default=4.0,
                                     var_prod_cond=["enough"])
```

An ordinary component observes, decides, and gates its own production. A
sensor stops being a component you are forced to interpose.

## 4. The pieces, and their order

### 4.1 Extract the output machinery into a shared base

The bulk. `obj_ctrl.py` is ~2900 lines and the emit half (readers, parameter
variables, forcing, blinding, band and compare automata, republication,
seeding, the control equation) is what moves into a base shared by both
classes. Mechanical by the probe's evidence, but it is the volume of the job,
and every generated automaton, state and variable name must stay byte
identical: downstream indicators reference them by name.

### 4.2 The operand door

`apply_prod_cond`'s resolution gains a fourth lookup: control outputs, for
BOOLEAN operands, read through an adapter. The adapter is needed because a
`CtrlSignalOut` exposes `var` / `var_available` where a flow exposes `var_fed`
/ `live_value()`; the R12 rule (live, never mirrored) applies unchanged, and
reading `var` directly satisfies it, the band automaton being what dates the
crossing.

Comparisons against VALUE outputs are deliberately out of v1: a value verdict
derived from a rate and gating that rate's producer is exactly the shape the
R47 refusal exists for, and it must go through the retest below before it is
allowed, not after.

### 4.3 The availability box (open)

A controller boolean output exports `{name}_out` only; a discrete flow output
also exports `{name}_available_out`. Two options:

- **(a), recommended for v1:** the fused verdict stays box-only. Internal
  self-gating reads `var` directly; external consumers keep wiring with the
  raw `connect`, as they do with `ObjCtrl` today. `connect_flow` stays
  unavailable to a control output, and saying so by name keeps the R-15
  habit.
- **(b), later:** grant the availability channel, which makes the verdict a
  full discrete-flow citizen: consumer-side `and` / `or` / `k` logic, the
  ok/nok pair, `connect_flow`. This can be added without moving (a)'s
  models, which is why (a) first costs nothing later.

### 4.4 The loop retest (must be measured before delivery)

The probe's verdict derives from an integrated LEVEL: the exact near miss R47
leaves alone, which is why it builds. The case to retest is a verdict derived
from a RATE (a comparison on an arriving flow, or on a rate observation)
gating that flow's producer, now that a control output is a carrier the walk
does not know. The seeds of `find_rate_observation_loops` collect flow
production conditions, rule guards and discharge conditions; whether the new
door must enter those seed sets is a measurement, not a judgement call, and
it decides 4.2's scope.

### 4.5 The declaration order moves

`DECLARATION_SECTIONS` already carries `controls_in` and `controls_out`
(`declare.py` resolves the sections lazily and refuses one a component cannot
build; today that refusal is what keeps `ObjFlow` honest). But `controls_out`
sits AFTER `flows`, and a component gating its own production needs the
verdict to exist before `add_flow` resolves `var_prod_cond`: the probe had to
declare the control output first. So `controls_out` moves before `flows`,
with the usual reasoning: the thing doing the refusing has to exist first.
Nothing in the emit machinery reads the flow declarations, so nothing else
moves.

## 5. What stays additive, what breaks

**Additive, one release:** `ObjFlow` gains the two methods, the containers and
the door. Every existing model is untouched; `SensorContinuous` and `ObjCtrl`
keep working unchanged; the suite must stay green with no test modified. This
is measurable, and it is the gate.

**Later cleanup, separate release, breaking only in names:** the two existing
surfaces then become thin, as 6babf2a once re-ground the sensor without moving
its surface. `SensorContinuous` reimplemented over the declaration, its keys
and outputs preserved; `ObjCtrl` reduced to a component declaring only the two
sections; the `measurements_in` / `controls_in` duplicate collapsed to one
name, with the other kept as an alias for one release.

## 6. Where this sits in the unification pass

The pass inventoried on 2026-09-15 (appendix A) had eight candidates. This is
the ninth, and it reorders two of them: candidate 4 (`control` /
`control_logic` naming across `SourceContinuous`, `CapacityContinuous` and the
sensor) and candidate 6 (the condition-operand enumeration) both rewrite
`SensorContinuous`, so they wait for this one rather than landing twice.
Recommended order: the small additive candidates (1, 2, 8) first; then this;
then 4 and 6 on top of the reimplemented sensor.

## 7. Open questions

- the availability box (4.3);
- comparisons against value outputs, held out of v1 pending 4.4;
- what a control output's spec entry looks like: `component_spec` expands onto
  `ObjFlow`, so the emit tree must walk back the way `_prod_cond_spec` walks a
  production condition back, and the automata the emit builds must be marked
  `derived` with the same care `add_band_memory` documents;
- the platform importer: `controls_in` / `controls_out` exist there for
  controller templates; whether `ObjFlow` templates gain them is a platform
  question this note only records.

## Appendix A: the unification candidates, as inventoried

| # | Duplicated | Where |
|---|---|---|
| 1 | the public clamp point (created at a declared value, muscadet never writes it) | `{f}_out_rate`, `{m}_level_gain`, `{c}_serve_rate_{f}`, and missing for `flow_rate` |
| 2 | declared continuous function with a continuity attestation | `profile.py` and `transfer.py`, two copies of one mechanism |
| 3 | `allocation`, `allocation_shares`, `allocation_priorities`, `allocation_fun` | three KB producer components |
| 4 | `control` / `control_logic` | `SourceContinuous`, `CapacityContinuous`; the sensor's `control` means something else |
| 5 | four per-kind defaults on a channel reading one `kind` | `MeasurementIn` |
| 6 | boolean production condition, five enumerations, fields spelled twice | `var_prod_cond*` on a flow, `serve_cond*` on a capacity |
| 7 | resolved state stored as a declarable field | `serve_cond_negate` / `serve_cond_compare` skipped by name in both dump loops |
| 8 | the applied production factor never published | profile, deratings, `out_rate` recomposable three ways |
| 9 | this note: the control grammar as a declaration | `ObjCtrl`'s emit half, the sensor |
