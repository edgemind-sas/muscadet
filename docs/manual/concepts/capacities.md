# Capacities

A capacity is a volume holding a quantity. Its level is the only thing in a
continuous model that **integrates**: it moves by what enters minus what
leaves,

```text
d level / dt  =  inflow(t) - outflow(t)
```

and an automaton with three states, empty, partial and full, watches its
bounds. The crossing of a bound is dated exactly by the solver, and the level
is put back on the bound.

## Three states

The same volume, a fill claim of 10, in each of its states:

=== "Empty"

    <!-- figure: capacity-empty -->

=== "Partial"

    <!-- figure: capacity-partial -->

=== "Full"

    <!-- figure: capacity-full -->

| State | Asks upstream | Serves downstream |
|---|---|---|
| empty | the demand crossing it, plus its fill claim | only what arrives |
| partial | the demand crossing it, plus its fill claim | the demand, out of its stock |
| full | only the demand crossing it | the demand |

## What a capacity declares

```python
system.add_component(
    name="TANK",
    cls="CapacityContinuous",
    flow="q",
    capacity=100.0,          # the volume
    content_init={"q": 50.0},
    fill_rate=10.0,          # what it claims for itself while it has room
    serve_rate=40.0,         # a ceiling on what it releases (optional)
)
```

| Key | Meaning | Default |
|---|---|---|
| `fill_rate` | a **claim**: what the volume asks for itself, on top of the demand crossing it | `0`, a pure pass-through buffer |
| `serve_rate` | a **ceiling**: what the volume may release at most | `math.inf`, no ceiling |
| `serve_cond` | a **command**: a condition that must hold for the volume to release anything | none, always released |

`fill_rate` and `serve_rate` are not a pair. The first makes a tank fill, the
second only caps what leaves it. At the default `fill_rate=0` a volume asks
exactly for what passes through, so it never stocks up.

## Mixtures and readings

Several flows may share one volume, each with a `weight`, the volume one unit
of it occupies: a room holding air and leaked hydrogen. What leaves is drawn
at the composition of what is held.

A level is read only through a **measurement link**, a read-only channel
carrying no quantity. A sensor compares it with thresholds and drives a
discrete command: that is how a level closes a [control loop](index.md).
