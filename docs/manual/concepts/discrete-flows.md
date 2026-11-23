# Discrete flows

A discrete flow is a boolean signal: a component is fed with it or not. It is
the vocabulary of reliability block diagrams, where the flow stands for the
availability of a function.

## Propagation

An output produces its flow when its **production condition** holds, and
passes it to every connected input. An input combines what arrives on its
connections with a **logic**:

| Logic | The input is fed when |
|---|---|
| `"or"` | at least one connection carries the flow |
| `"and"` | every connection carries it |
| integer `k` | at least `k` connections carry it (a k-out-of-n vote) |

A failure mode cuts the propagation: its effect clamps the availability of an
output, which then stops producing whatever its condition says. Propagation is
instantaneous. Between two events nothing moves, so a purely discrete model
costs nothing to simulate between them.

<!-- figure: discrete-or -->

## Declaring it

```python
class Block(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="is_ok", logic="or")
        self.add_flow_out(
            name="is_ok",
            var_prod_default=False,
            var_prod_cond=["is_ok"],   # produce when the input is fed
        )
```

`var_prod_cond` is a condition in conjunctive normal form: a list of clauses,
each clause a list of operands, an operand naming an input, optionally
negated or compared with a value. The [Getting started](../getting-started.md)
page builds a complete block diagram this way, failures included.

## Variables

Each discrete flow creates a few variables, read by indicators and by
failure-mode effects:

| Variable | Meaning |
|---|---|
| `{flow}_fed_in` | the input is fed, after its logic |
| `{flow}_fed_available_in` | the input can accept the flow |
| `{flow}_prod` | the output's production condition holds |
| `{flow}_prod_available` | the output can produce, which a failure clamps |
| `{flow}_fed_out` | the output actually delivers the flow |
