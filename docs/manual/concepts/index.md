# Modelling concepts

muscadet narrows the general formalism of its simulator, hybrid stochastic
automata, down to **components connected by flows**. A model mirrors the
physical architecture of the system: each component carries its local
behaviour, and what it receives, transforms and passes on travels along the
connections.

The pages of this section explain each building block with an animated
figure. Use the buttons under a figure to step through it, or **Play** to run
all its steps. Every number drawn on a continuous-flow figure is simulated by
the test suite (`tests/test_docs_concepts_001.py`), so the figures show what
muscadet actually computes.

## Four hypotheses

| Hypothesis | Building block | Page |
|---|---|---|
| Components exchange **oriented flows**, boolean or real-valued | flows | [Discrete flows](discrete-flows.md), [Continuous flows](continuous-flows.md) |
| A process consumes and produces in **fixed proportions** | rules | [Transformation](transformation.md) |
| Quantities can be **stored** in bounded volumes | capacities | [Capacities](capacities.md) |
| Components interact through **commands on thresholds** and random events | automata | [Automata](automata.md) |

## A model as a control loop

The building blocks fit together as a control loop. The process is made of
flows, their transformations and the volumes holding them. A sensor reads a
level or a rate and compares it with thresholds. A controller, a boolean
automaton, turns those comparisons into an order. An actuator is a production
condition on a component, which the order switches on or off.

<!-- figure: control-loop -->

The vocabulary is borrowed from control engineering. muscadet does not carry
out a control-structure analysis: it represents the loop so that its
scenarios can be simulated and quantified.

## Two flow families

| | Discrete | Continuous |
|---|---|---|
| Carries | a boolean: fed or not | a rate, in units per unit of time |
| Combined by | logic: `and`, `or`, k out of n | sum of the connections, split by an allocation policy |
| Changes | at events, when an automaton fires | at every instant, integrated by the PDMP solver |
| Typical use | availability of a function, a command | matter, energy, information throughput |

The two families never connect to each other: a continuous output wired to a
discrete input is refused, whatever the route. They meet through
**conditions**: a rule guard or a production condition may compare a rate
with a threshold, and a boolean input may gate a continuous production.
