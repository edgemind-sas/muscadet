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
