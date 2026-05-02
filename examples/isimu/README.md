# Interactive simulation examples

Each module here exposes a `build()` function returning a populated
[`muscadet.System`][muscadet] designed to illustrate a specific flow-propagation
behaviour. They can be driven either through the
[`cod3s-isimu`][cod3s-isimu] Textual TUI or run as plain Python scripts.

## Examples

| Module | Demonstrates |
|---|---|
| `rbd_kn` | k/n threshold logic on `FlowIn` (3 sources, k=2). Failures on S1, S2 cross the threshold both ways. |
| `trigger_source` | Warm standby via `FlowOutOnTrigger`. S2 activates instantly when S1 fails, deactivates on repair. |
| `datacenter_lite` | Composite `var_prod_cond` — `(P1 OR P2) AND C` on a server output. Step through power and cooling failures. |
| `inverter_chain` | `FlowOut(negate=True)` — two inverters in series, even-parity output. |
| `cyber_3comp` | Cascading cyber compromise modes (MdC) on a 3-component system, replicating the IMdR P23-4 atelier example. Two cascade mechanisms shown side by side: MdC_B gated by MdC_A's automaton state, MdC_proc gated by a propagated dormant "service" flow. |
| `power_plant` | Mini electricity production plant with cold-redundant cooling (`FlowOutOnTrigger`). Combines hardware failure modes (Grid, both pumps) with a four-step IT→OT cyber kill chain (phishing → lateral movement → coordinated pump exploits) that defeats the redundancy. The probabilistic study reveals competing sequences: cold redundancy mitigates single hardware failures but a coordinated cyber attack disables both pumps in parallel. |

All the behaviours above are also covered by tests in `tests/`, but the
examples here are tuned for visual stepping rather than assertions:
deterministic delay failure modes and clear single-character snapshot
output.

## Running with cod3s-isimu (TUI)

Since cod3s 1.1.x, ``textual`` is a regular dependency and ``cod3s-isimu``
ships with cod3s (no extra needed)::

```sh
uv pip install -e ../../cod3s     # or: pip install -e ../../cod3s
cod3s-isimu --factory examples.isimu.rbd_kn:build
cod3s-isimu --factory examples.isimu.trigger_source:build
cod3s-isimu --factory examples.isimu.datacenter_lite:build
cod3s-isimu --factory examples.isimu.inverter_chain:build
cod3s-isimu --factory examples.isimu.cyber_3comp:build
cod3s-isimu --factory examples.isimu.power_plant:build
```

## Running as a plain Python script

Each module has a `run()` function that steps through a scripted timeline
and prints a one-line snapshot per event. No extra dependency::

```sh
python -m examples.isimu.rbd_kn
python -m examples.isimu.trigger_source
python -m examples.isimu.datacenter_lite
python -m examples.isimu.inverter_chain
python -m examples.isimu.cyber_3comp
python -m examples.isimu.power_plant
```

Sample output (`rbd_kn`)::

```
INITIAL          t=0  | S1.f1=1 | S2.f1=1 | S3.f1=1 | T.f1.sum=3 | T.f1=1
AFTER S1 fail    t=5  | S1.f1=0 | S2.f1=1 | S3.f1=1 | T.f1.sum=2 | T.f1=1
AFTER S2 fail    t=10 | S1.f1=0 | S2.f1=0 | S3.f1=1 | T.f1.sum=1 | T.f1=0
AFTER S2 repair  t=15 | S1.f1=1 | S2.f1=1 | S3.f1=1 | T.f1.sum=3 | T.f1=1
```

## Sequence analysis with `run-cod3s-study` (YAML)

The `cyber_3comp` example also ships a pair of YAML specs to drive the
[`run-cod3s-study`](https://github.com/edgemind-sas/cod3s) CLI. The
study declares the three MdC, two redoubt-event targets (loss of
`F1`/`F2` on the process), Monte-Carlo parameters, and indicator
exports — and produces a sequence analysis ``sequences.xml`` listing
the paths leading to each target::

```sh
cd examples/isimu
run-cod3s-study --model cyber_3comp_model.yaml \
    --study-specs cyber_3comp_study.yaml \
    --log-level INFO
```

Outputs (under `cyber_3comp_study/`, gitignored):

```
sequences.xml      # the cascade as a sequence (3 transitions, P=1)
sequences.html     # human-readable XSLT render
proc_outputs.csv   # F1/F2 mean values over the schedule
srv_service.csv    # f_service mean values over the schedule
pyc_param.xml      # final PyCATSHOO parameter dump
```

Expected `sequences.xml` content (one deterministic sequence, P=1)::

```xml
<SEQ N="1" P="1" C="process_F1_lost">
    <BR T="10"> <TR NAME="Srv__mdc_a.occ"   .../></BR>
    <BR T="15"> <TR NAME="Srv__mdc_b.occ"   .../></BR>
    <BR T="23"> <TR NAME="Proc__mdc_proc.occ" .../></BR>
</SEQ>
```

Same cascade as the `cod3s-isimu` factory and `python -m` runner — the
YAML path adds the redoubt-event analysis on top.

### Probabilistic variant (`cyber_3comp_study_exp.yaml`)

A second study spec layered on the same model adds three classical
hardware failure modes (MdD on Alim, on Srv.f_orange, on Srv.f_blue)
in parallel with the three MdC, all with **exponential occurrence
laws**. Monte-Carlo simulation produces a distribution of competing
sequences (cyber path vs. hardware paths) leading to each redoubt
event::

```sh
cd examples/isimu
run-cod3s-study --model cyber_3comp_model.yaml \
    --study-specs cyber_3comp_study_exp.yaml \
    --log-level INFO
```

With 1000 runs over a 200 h horizon and the chosen rates (cyber attack
faster than alim hardware failure), the resulting `sequences.xml`
shows ~12 distinct paths, e.g.::

  P=0.489  MdC_A → MdC_B → MdC_proc       (pure cyber cascade)
  P=0.157  hw_alim                         (single power-supply failure)
  P=0.096  hw_srv_blue                     (loses F2 only)
  P=0.089  hw_srv_orange                   (loses F1 only)
  P=0.048  MdC_A → MdC_B → hw_alim         (cyber attack pre-empted by alim)
  P=0.034  MdC_A → MdC_B → hw_srv_orange   (mixed cyber + hardware)
  ...

Demonstrates the slide-58 perspective of combined sûreté + sécurité
analysis on a single model — the same MdC formalism layered on top of
the classical MdD.

### Power plant with cold redundancy (`power_plant_study.yaml`)

A larger study showcasing the same approach on a five-component model
with active cold redundancy (`FlowOutOnTrigger`)::

```sh
cd examples/isimu
run-cod3s-study --model power_plant_model.yaml \
    --study-specs power_plant_study.yaml \
    --log-level INFO
```

The redundancy mitigates a single hardware failure (PumpB takes over
when PumpA fails). But a four-step cyber kill chain — phishing,
lateral movement, then coordinated PumpA disable + backup interlock
inhibition — defeats it. The Monte-Carlo distribution typically shows
~15 distinct sequences with empirical probabilities, mixing pure-cyber,
hardware-only, and interleaved paths.

A subtle artefact worth noting: a single `hw_pumpA` failure reaches
the redoubt event in the model because PyCATSHOO processes the
trigger automaton's `up` transition as a separate event at the same
simulation time, leaving a momentary gap during which Plant
electricity is False. This faithfully represents the brief glitch on
real switchover hardware. To remove it, model the redundancy through
a single `or`-combined input rather than via FlowOutOnTrigger.

## Writing your own factory

Any callable returning a populated `muscadet.System` works as a factory.
Minimum skeleton::

```python
import muscadet

class MyComp(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="f", var_prod_default=True)

def build() -> muscadet.System:
    system = muscadet.System(name="my_demo")
    system.add_component(name="C", cls="MyComp")
    return system
```

Then point `cod3s-isimu` at it::

```sh
cod3s-isimu --factory my_module:build
```

[muscadet]: https://github.com/edgemind-sas/muscadet
[cod3s-isimu]: https://github.com/edgemind-sas/cod3s
