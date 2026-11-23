# Continuous flows

A continuous flow carries a rate: units of matter, energy or information per
unit of time. Rates are recomputed at every instant the solver evaluates the
model; only the [capacities](capacities.md) integrate them into quantities.

## Three passes per evaluation

At each evaluation muscadet sweeps the connection graph three times, then
integrates the levels:

| Pass | Direction | What travels | Variable on the output |
|---|---|---|---|
| 1. offer | downstream | what each producer could deliver if asked without bound | `{flow}_capability_out` |
| 2. demand | upstream | what each consumer asks for | `{flow}_demand_in` |
| 3. production | downstream | the lesser of offer and demand | `{flow}_fed_out` |

The order of the passes is derived from the connection graph: no model ever
writes an equation order. A loop that no capacity breaks is refused at the
first run, because nothing could decide which rate comes first.

## One source, one consumer

=== "Balanced"

    <!-- figure: continuous-balanced -->

=== "Demand above the offer"

    <!-- figure: continuous-short -->

=== "Demand below the offer"

    <!-- figure: continuous-demand-led -->

The delivery is always `min(offer, demand)`. The demand is the consumer's
declared need, never reduced to what it can get, so the shortfall remains
readable as the gap between the two.

## A shortage among several consumers

When several consumers ask for more than the supply, the output splits it by
its **allocation policy**. The default is proportional to the demands, so
every consumer is served at the same ratio of its need.

<!-- figure: continuous-shortage -->

Other policies are declared on the output:

| `allocation` | Split |
|---|---|
| `"proportional"` (default) | in proportion to the demands |
| `"shares"` | fixed shares, `allocation_shares={"C1": 0.25, "C2": 0.75}` |
| `"priority"` | by rank, `allocation_priorities={"C1": 1, "C2": 2}` |
| `allocation_fun=f` | any Python function of the supply and the demands |

## Why the offer goes first

A consumer that knows what its suppliers can offer never asks for more of one
input than its scarcest other input lets it use. Without that, it would claim
its nominal share and distort the split among its neighbours.

<!-- figure: continuous-offer-first -->

## Several sources, one consumer

A demand is not split between sources: each is asked for the whole of it and
answers within its offer. The consumer then takes what it uses and hands the
surplus back.

<!-- figure: continuous-two-sources -->

!!! note "A pure sink keeps everything"
    The surplus is handed back by a component whose rule or transfer sizes
    what it uses. A consumer carrying no rule (`ConsumerContinuous`) is the
    sink itself: fed 9 by these two sources for a demand of 6, it keeps the 9.
    Put a machine in front of it to cap what it takes.

## Declaring it

```python
system.add_component(name="S", cls="SourceContinuous", flow="q", rate=12.0)
system.add_component(name="C1", cls="ConsumerContinuous", flow="q", demand=6.0)
system.add_component(name="C2", cls="ConsumerContinuous", flow="q", demand=12.0)
system.connect_flow(source="S", target="C1", flow_name="q")
system.connect_flow(source="S", target="C2", flow_name="q")
```

One connection wires the three channels at once: the rate downstream, the
demand upstream and the offer downstream share one message box.
