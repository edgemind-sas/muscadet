# Transformation

A component turns inputs into outputs through **rules**. A rule states what
is consumed and what is produced, with fixed coefficients, like a balance
sheet of the machine:

```python
system.add_component(
    name="ELY",
    cls="TransformerContinuous",
    flows_in=["water", "power"],
    flows_out=["h2", "o2"],
    rules=[dict(
        name="electrolysis",
        cons={"water": 9.0, "power": 50.0},   # kg/h of water, kW
        prod={"h2": 1.0, "o2": 8.0},          # kg/h
    )],
)
```

The coefficients are a **ratio, not a rating**. The rule runs at a scale `s`:
it draws `9 s` of water and `50 s` of power, and makes `s` of hydrogen and
`8 s` of oxygen. The scale is the scarcest factor among the inputs available
and the outputs asked for. Every flow then follows in the declared
proportions, and what is not transformed is not drawn.

=== "The demand decides"

    <!-- figure: rule-demand-led -->

=== "An input is short"

    <!-- figure: rule-input-limited -->

## Consequences of the scale rule

- **An unwired output constrains nothing.** A vent left unconnected never
  holds the rule back.
- **A by-product asked too little holds back the main product.** A connected
  output asking for zero stops the rule, since the surplus could not be
  delivered.
- **A rated machine needs a rated supply.** With an ample supply and an
  unbounded demand, the scale has no limit: a 2 kW heat pump ran at scale 50
  and delivered 350 kW. What caps a machine is what it can draw.
- **The efficiency is constant.** A rule is linear. A machine whose
  efficiency varies with its load is approached by several rules, each
  guarded by a condition, of which one is active at a time.

## Guards

A rule may carry a guard, `cond`, written structurally or as a string:

```python
rules=[
    dict(name="low", cond="power < 200", cons={...}, prod={...}),
    dict(name="high", cond="power >= 200", cons={...}, prod={...}),
]
```

Guards compile into a watched automaton, so a threshold switches the active
rule exactly at the crossing. A rule without a guard is the default of its
set. Two guards true at once raise an error naming both rules.
