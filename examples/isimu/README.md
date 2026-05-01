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
```

## Running as a plain Python script

Each module has a `run()` function that steps through a scripted timeline
and prints a one-line snapshot per event. No extra dependency::

```sh
python -m examples.isimu.rbd_kn
python -m examples.isimu.trigger_source
python -m examples.isimu.datacenter_lite
python -m examples.isimu.inverter_chain
```

Sample output (`rbd_kn`)::

```
INITIAL          t=0  | S1.f1=1 | S2.f1=1 | S3.f1=1 | T.f1.sum=3 | T.f1=1
AFTER S1 fail    t=5  | S1.f1=0 | S2.f1=1 | S3.f1=1 | T.f1.sum=2 | T.f1=1
AFTER S2 fail    t=10 | S1.f1=0 | S2.f1=0 | S3.f1=1 | T.f1.sum=1 | T.f1=0
AFTER S2 repair  t=15 | S1.f1=1 | S2.f1=1 | S3.f1=1 | T.f1.sum=3 | T.f1=1
```

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
