# Automata

The two-state automaton is the building block of everything discrete in a
muscadet model: commands, failures, repairs. Each of its two transitions
carries a **condition**, an **occurrence law** and **effects**:

| Part | What it says |
|---|---|
| condition | when the transition may fire |
| occurrence law | how long after the condition holds: a delay (zero for a command), or a random law for a failure |
| effects | which variables the new state clamps, and to what |

## Commands on thresholds

A command compares a measurement with two thresholds. It switches on when the
measurement crosses the activation threshold, and back off only when it
crosses the release threshold. The gap between the two, the hysteresis, keeps
a closed loop from chattering around a single value.

<!-- figure: automaton-thresholds -->

The crossing is watched by the solver, so the transition fires at the instant
the measurement reaches the threshold, not at the next integration step.
`SensorContinuous` ships this pattern with its deadband (`activate`,
`release`). A controller may first combine several readings: the median of
three sensors rejects a single wild one, and an integer logic on discrete
inputs gives a k-out-of-n vote.

## Failure modes

A failure mode is the same automaton, with a random law on its transitions:

```python
comp.add_exp_failure_mode(
    name="breakdown",
    failure_rate=1e-3,                  # per unit of time
    repair_rate=1e-1,
    failure_effects=[("q", 0.0)],       # cut the output q
)
comp.add_delay_failure_mode(
    name="wear",
    failure_time=500,
    repair_time=20,
    failure_effects=[("q", 0.5)],       # derate q to half
)
```

On a discrete output an effect clamps its availability. On a continuous
output it is a **derating**: the fraction of the production the mode leaves.
Several active deratings on one output combine by **minimum**, so repairing
one mode while another still holds gives the remaining one's value, whatever
the order of the events. A derating also scales what the component draws: a
dead output consumes nothing.

## Time in an interactive session

`isimu_step_forward()` advances to the next **dated** transition and
integrates the continuous flows on the way. `isimu_step_to(date)` advances to
a date, stopping exactly at each event due before it. A purely continuous
model has no dated transition, so only `isimu_step_to` moves its clock.
