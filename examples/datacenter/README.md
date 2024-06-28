# muscadet

## Getting started

The MUSCADET framework come with a list of libraries that contains some generic types of components:

The most common library is `rbd`
- **Sources:** Components capable of producing a functional flow
- **Blocks:** Components that can receive and propagate flows
- **Targets:** Components that receive the flows

First, import the `muscadet.kb.rbd` library:

```python
import muscadet.kb.rbd as rbd
```

### Electric Devices

We propose creating electrical components using the MUSCADET electric library. This knowlegde base consists of three types of components:

- **SourceElec:** Components capable of producing an elec flow
- **DipoleElec:** Components that can receive and propagate elec flows
- **UserElec:** Components that receive the flows elec

The `SourceElec` is the equivalent to a `Source`, the `DipoleElec` is the equivalent to a `Block` and the `UserElec` is the equivalent to a `Target`. But the generic `is_ok` flow is replaced by the `elec` flow.

First, import the `muscadet.kb.electric` library:

```python
import muscadet.kb.electric as elec
```

### Hydraulic Devices

We propose creating electrical components using the MUSCADET hydraulic library.

- **SourceHydr:** Produces a hydraulic flow.
- **UserHydr:** Receives and propagates hydraulic flows.
- **DipoleHydr:** Receives hydraulic flows.

The `SourceHydr` is the equivalent to a `Source`, the `DipoleHydr` is the equivalent to a `Block` and the `UserHydr` is the equivalent to a `Target`. But the generic `is_ok` flow is replace by the flow `hydr`.

First, import the `muscadet.kb.hydraulic` library:

```python
import muscadet.kb.hydraulic as hydr
```

### Complexe Devices

We propose creating electric device automatically using the MUSCADET datacenter library. 

- **Generator:** This is an instance of `SourceElec`
- **ElectricalPanel:** This is an instance of `DipoleElec`
- **Battery:** This is an instance of `DipoleElec`

To use these components, first import the `datacenter` library:

```python
import muscadet.kb.datacenter as dc
```

Now, create the `Datacenter` components like this:

```python
# Add components
my_rbd.add_component(cls="Generator", name="S1")
my_rbd.add_component(cls="ElectricalPanel", name="Panel")
my_rbd.add_component(cls="Battery", name="Server")
```

The code for this example is available [here](examples/datacenter/datacenter_01.py).

To create hydraulic components using the MUSCADET datacenter library. 

- **Pump:** This is an instance of `SourceHydr`
- **Valve:** This is an instance of `DipoleHydr`

Now, create the `hydraulic` components like this:

```python
# Add components
my_rbd.add_component(cls="Pump", name="P1")
my_rbd.add_component(cls="Valve", name="V1")
```

More complex components can be created to use multiple flows.
For example, it is possible to create a component using `elec` and `hydr` flows.
This example shows how to create a `AirConditioning` component.

```python
class AirConditioning(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)

        self.add_flow_in(
            name="elec",
        )
        
        self.add_flow_in(
            name="hydr",
        )

        self.add_flow_out(
            name="hydr_chaud",
            var_prod_cond=[
                "elec",
                "hydr",
            ],
        )
```

As the component is fed with `elec` and `hydr` flows, two source must be connected to the `AirConditioning`.

```python
# Add components
my_rbd.add_component(cls="Generator", name="S1")
my_rbd.add_component(cls="Pump", name="P1")
my_rbd.add_component(cls="AirConditioning", name="air")

# Connect components
my_rbd.auto_connect("S1", "air")
my_rbd.auto_connect("P1", "air")
```

![Results](./examples/datacenter/datacenter_2.png)

We observe that the `AirConditioning` target is correctly fed if there are flow propagation from `S1` and `P1`.

The code for this example is available [here](examples/datacenter/datacenter_02.py).
