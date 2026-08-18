# Worked models

Three complete MUSCADET models, each carrying figures that can be checked
outside the library. They exist as executable tests rather than as prose, so
they cannot drift from the code they document: every number below is asserted.

| Model | What it demonstrates | Checked against |
|---|---|---|
| [Heated tank](#the-heated-tank) | discrete regulation, redundancy, feared events, and where the knowledge base stops | a published dynamic-reliability benchmark |
| [Counter-flow exchanger](#the-counter-flow-exchanger) | transfer pairs carrying a computed quantity between two balances | a closed-form correlation from the heat-transfer literature |
| [Domestic hot water](#the-domestic-hot-water-circuit) | rules with coefficients, capacity, thermostat, redundancy and standing loss in one system | elementary physics on ordinary domestic figures |

Run any of them with `pytest`:

```sh
.venv/bin/python -m pytest tests/test_heated_tank_001.py -v
.venv/bin/python -m pytest tests/test_literature_validation_001.py -v
.venv/bin/python -m pytest tests/test_domestic_heating_001.py -v
```

---

## The heated tank

`tests/test_heated_tank_001.py`

The classical dynamic-reliability benchmark: a tank whose level is regulated
between two thresholds by two pumps and a valve, with dry-out, overflow and
overheating as the feared events. It is the standard case for comparing
approaches that mix continuous dynamics with discrete failures, which is
exactly what MUSCADET 2.0 claims to do.

**Origins**, obtained through OpenAlex rather than from memory:

- T. Aldemir, "Computer-Assisted Markov Failure Modeling of Process Control
  Systems", *IEEE Transactions on Reliability* (1987),
  DOI `10.1109/tr.1987.5222318`
- M. Marseguerra and E. Zio, "Monte Carlo approach to PSA for dynamic process
  systems", *Reliability Engineering & System Safety* (1996),
  DOI `10.1016/0951-8320(95)00131-x`

The parameter set is the one shipped with PyCATSHOO
(`Samples/HeatedTank/S8d`), so the figures are directly comparable:

| Parameter | Value |
|---|---|
| tank section | 1 |
| dry-out level | 4 |
| overflow level | 10 |
| regulation band | 6 to 8 |
| nominal flow, per pump and per valve | 1.5 |
| failure rates `lambda0` (P1 / P2 / V3) | 0.0022831 / 0.0028571 / 0.0015625 per hour |

### What the model reproduces

The regulation is the benchmark's, deadband included: both pumps run below 6
and stop above 8, the valve opens above 8 and shuts below 6. The level
therefore cycles between the thresholds with a period of **exactly 2 hours**
(2/3.0 h filling against two pumps, 2/1.5 h draining through one valve), which
the test asserts rather than describes.

Both feared events are driven deterministically: both pumps stuck on take the
level to overflow, and dead pumps with a stuck valve take it to dry-out, each
arriving when the ramp says it should.

**Stuck closed** is a derating of the continuous output to 0, the library's own
idiom. **Stuck on** has no idiom, and the model says so: a derating can only
subtract, so forced production goes through a discrete output of the
component's own, produced unconditionally with its availability initially
false, which the failure mode clamps true and a third rule guard reads back.

### What it does not reproduce, and why

Four boundary tests state the limits rather than papering over them. Two still
stand:

- **the temperature ODE is not ported.** `dT/dt = (sum_i q_i (T_i - T) + power)
  / (L x area)` needs a rate proportional to a component's own integrated
  state. The transfer pair now covers part of this, but the advection terms,
  what a stream brings in and takes out, need the carried-flow notion that has
  not shipped;
- **the temperature-dependent failure rate is not ported.** `add_exp_failure_mode`
  already creates a real variable for the rate and hands the *variable object*
  to PyCATSHOO's `newLaw`, so the parameter is a model variable rather than a
  literal. What is missing is `ITransition.setModifiable`: without it the
  engine draws every firing time from the parameter's initial value, so writing
  the variable during a run changes nothing.

One boundary has since moved, and the test says so: a measurement channel now
publishes each constituent, so `heat / water` is observable. What no
declaration reaches yet is a *watched threshold* on one constituent, because a
sensor's band is built from the `{name, op, value}` operand vocabulary, which
names a channel and has no slot for a constituent of one.

### A note on cost

The benchmark's Monte-Carlo estimate is out of reach here and is deliberately
not attempted: one 200 h sequence costs about 37 s, so the reference's 1000
sequences over 1000 h would run for days. What the module asserts instead is
the deterministic sequence behind each feared event, which is what a
regression needs.

---

## The counter-flow exchanger

`tests/test_literature_validation_001.py`

A test that only compares MUSCADET to MUSCADET can be uniformly wrong. This one
compares it to a result computed outside it: the counter-flow
effectiveness-NTU relation of R. K. Shah and D. P. Sekulic, *Fundamentals of
Heat Exchanger Design*, Wiley (2003), DOI `10.1002/9780470172605`.

### What is validated, and what is assumed

The distinction is easy to fudge, so the module states it. The correlation is
an **input**: a single-node component cannot derive a counter-flow
arrangement's effectiveness, and pretending otherwise would make the test
circular.

What is checked is everything downstream of it, and those are the properties a
defect in the sweeps would break:

| Quantity | Value |
|---|---|
| hot stream capacity rate | 2000 W/K at 80 degrees in |
| cold stream capacity rate | 4000 W/K at 20 degrees in |
| `UA` | 2000 W/K, so NTU = 1 and Cr = 0.5 |
| effectiveness | 0.5647 |
| duty crossing the wall | 67768 W |
| hot outlet | 46.116 degrees |
| cold outlet | 36.942 degrees |

plus: the energy balance closes, no temperature crossing occurs, and the
smaller capacity rate swings twice as far as the larger one.

The exchanger is a two-flow transfer pair whose equation forms both
temperatures itself, from the raw enthalpy rates the component receives divided
by the declared capacity rates. That is the transfer-pair contract exercised on
a real correlation rather than on a constant: MUSCADET hands the equation no
intensive property.

### What it measured about the platform

A quantity crossing a PyCATSHOO variable carries about **seven significant
digits**, not the fifteen a Python float suggests. A balance that closes
exactly in Python reads `240000.0078125` against `240000.0`, and `0.0078125` is
exactly half an ulp of single precision at that magnitude.

Nothing leaks; it is representation. But it bounds every assertion in the
project, and the file encodes both consequences rather than working around
them. See the *Modelling pitfalls* section of the README.

---

## The domestic hot-water circuit

`tests/test_domestic_heating_001.py`

The integration case. Every mechanism the continuous layer carries meets on one
small system that a heating engineer would recognise:

- a **heat pump**, which is exactly a rule with two coefficients: the
  coefficient of performance is the ratio of the produced to the consumed
  coefficient and nothing else;
- an **electric resistance** as backup, the same rule at COP 1, which is what
  makes the redundancy interesting rather than cosmetic: it delivers the same
  heat for three and a half times the electricity, and it runs only when the
  pump is out;
- a **capacity** holding the cylinder's stored energy, integrated by the solver;
- a **thermostat** with a deadband, whose band is what stops the loop
  chattering around a single setpoint;
- a **failure mode** derating the pump's output to nothing;
- a **transfer pair** for the standing loss, the one quantity here that moves
  because a gradient makes it move rather than because somebody asked for it.

### The figures

Ordinary domestic values, chosen so every one can be checked by hand:

| Quantity | Value |
|---|---|
| cylinder | 200 L, water at 4.185 kJ/(kg.K), so 0.2325 kWh/K |
| heat pump | 2 kW electrical, COP 3.5, so 7 kW thermal |
| resistive backup | 3 kW electrical, COP 1 |
| thermostat | on below 55 degrees, off above 60 |
| standing loss | 1.5 kWh per day at a 45 K difference |
| heat-up, 15 to 60 degrees | **1.4946 h** |

The model asserts the pump's rated thermal output, the COP as a *ratio of two
measured quantities* rather than as a value read back, that the backup stays
off while the pump is healthy, that the standing loss follows the temperature
difference, and that the tank heats at the net rate, measurably slower than the
pump alone because of the loss.

### The three lessons it produced

Building this model was more instructive than the model. All three are now in
the README's *Modelling pitfalls* section:

1. **The time unit decides whether a model is simulable.** In seconds, 19 s of
   wall clock for 300 s of simulated time; in hours, the whole file in under a
   second. Bisecting showed that removing the thermostat *and* the transfer
   pair changed nothing.
2. **A rule's coefficients are a ratio, not a rating.** Behind a shared supply
   and a tank asking without bound, the 2 kW pump ran at scale 50 and delivered
   350 kW. Each appliance now sits on its own rated supply, which is where a
   breaker lives in the real installation.
3. **An infinite source is not a large one.** `rate=math.inf` split between two
   finite consumers delivered NaN into the tank level.

### Two reading rules

Both are general, and both are encoded in the file:

- the **initial condition is read before any step**. The first integration
  jumps straight to the next watched crossing, so a reading taken past it is no
  longer a reading of a cold tank;
- **stepping stops on transitions, not on dates.** A helper that advances "to
  t = 0.7 h" will actually stop at whatever crossing comes first.

### What it works around

The cylinder stores **heat only**, with the water mass a declared constant.
That is not convenience: a watched threshold cannot name one constituent of a
multi-constituent volume yet, so a thermostat on a water-plus-heat tank would
compare against the sum of a mass and an energy. Holding heat alone keeps the
level proportional to the temperature and the thresholds exact.

This is the same boundary the heated tank records, and it is the natural next
piece of work: extending the operand vocabulary so a guard can name a
constituent would let both models drop their workaround.
