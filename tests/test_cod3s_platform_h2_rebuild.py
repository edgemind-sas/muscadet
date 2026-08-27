"""The H2 electrolysis plant, rebuilt from a COD3S Platform export payload.

``tests/test_h2_stack_001.py`` declares the same four components DIRECTLY,
against the shipped classes of ``muscadet.kb.continuous``. This module declares
them as DATA -- a payload of the shape the platform exports -- and asserts the
two builds agree.

What "agree" means here, precisely:

* **structure**: the same flows on the same sides and of the same families, the
  same capacities with the same volumes, sides, contents and weights, and the
  same rule set with the same ``cons`` / ``prod`` coefficients;
* **behaviour**: the same sampled trajectories, instant by instant, over the
  reference's own schedule and its own indicator variables.

What it deliberately does NOT mean is an identity of CLASS. A declaration
carried by data is expanded onto the generic ``muscadet.ObjFlow``, so the
rebuilt ``S_H2O`` is an ``ObjFlow`` where the reference's is a
``SourceContinuous``. That is the whole point of the structural declaration:
the shipped class is a place to write a declaration, and the same declaration
written as data builds the same plant.

The failure mode is added after the import, exactly as the reference declares
it: failure modes are not in the importer's scope yet. What the payload DOES
carry is the ``deratings`` section, which pre-allocates the variable
``df_H2`` will clamp -- the platform emits the pair, muscadet allocates the
variable, and the mode declared afterwards finds it and reuses it.
"""

import math

import pytest

import cod3s
import muscadet
import muscadet.kb.continuous  # noqa: F401  -- registers the shipped classes

from muscadet.importers.cod3s_platform import system_from_export

from test_h2_stack_001 import (
    H2_BATTERY_CAPACITY,
    H2_BATTERY_CONTENT,
    H2_CONS,
    H2_FAILURE_TIME,
    H2_OBSERVED,
    H2_PROD,
    H2_REPAIR_TIME,
    H2_SCALE,
    H2_SCHEDULE,
    H2_SOURCE_RATE,
    H2_TANK_CAPACITY,
    H2_TANK_CONTENT,
    H2_TANK_FILL_RATE,
)

# ---------------------------------------------------------------------------
# The payload: the same plant, written as the platform would export it
# ---------------------------------------------------------------------------


def _continuous(name, direction, rate=None):
    """One continuous interface of the KB, with its nominal rate if it has one."""
    interface = {
        "name": name,
        "port_type": {"general": direction},
        "flow_family": "continuous",
    }
    if rate is not None:
        key = "production_profile" if direction == "output" else "demand_profile"
        interface[key] = {"cls": "constant", "value": rate}
    return interface


#: ``SourceContinuous(flow="H2O", rate=2)`` -- one continuous output at a rate.
WATER_SOURCE_TEMPLATE = {
    "interfaces": {"H2O__output": _continuous("H2O", "output", H2_SOURCE_RATE)},
}

#: ``CapacityContinuous(flow="Elec", ports="out", ...)`` -- a reservoir: the
#: held flow is carried on the output side alone, and the capacity sits there.
BATTERY_TEMPLATE = {
    "interfaces": {"Elec__output": _continuous("Elec", "output")},
    "capacities": [
        {
            "name": "battery",
            "flows": [{"name": "Elec"}],
            "volume": H2_BATTERY_CAPACITY,
            "side": "out",
            "content_init": {"Elec": H2_BATTERY_CONTENT},
        }
    ],
}

#: ``TransformerContinuous(flows_in=..., flows_out=..., rules=...)``. The rule
#: SET is named ``transform``, which is the shipped class's own default.
STACK_TEMPLATE = {
    "interfaces": {
        "H2O__input": _continuous("H2O", "input"),
        "Elec__input": _continuous("Elec", "input"),
        "H2__output": _continuous("H2", "output"),
        "O2__output": _continuous("O2", "output"),
    },
    "rule_sets": [
        {
            "name": "transform",
            "rules": [{"name": "electrolysis", "cons": H2_CONS, "prod": H2_PROD}],
        }
    ],
}

#: ``CapacityContinuous(flow="H2", ports="both", fill_rate=1)`` -- a buffer:
#: the held flow is carried on BOTH sides and the capacity sits downstream.
TANK_TEMPLATE = {
    "interfaces": {
        "H2__input": _continuous("H2", "input"),
        "H2__output": _continuous("H2", "output"),
    },
    "capacities": [
        {
            "name": "tank",
            "flows": [{"name": "H2"}],
            "volume": H2_TANK_CAPACITY,
            "side": "out",
            "content_init": {"H2": H2_TANK_CONTENT},
            "fill_rate": H2_TANK_FILL_RATE,
        }
    ],
}


def _connection(source, target, flow):
    return {
        "component_source": source,
        "component_target": target,
        "interface_source": flow,
        "interface_target": flow,
    }


def h2_payload():
    """The plant of ``test_h2_stack_001`` as a platform export payload."""
    return {
        "model": {
            "name": "H2StackRebuilt",
            "kb": {"name": "H2KB", "version": "1.0.0"},
            "elements": {
                "components": {
                    "id-source": {
                        "name": "S_H2O",
                        "class_name": "WaterSource",
                        "attributes": [],
                    },
                    "id-battery": {
                        "name": "B1",
                        "class_name": "Battery",
                        "attributes": [],
                    },
                    "id-stack": {
                        "name": "Electro",
                        "class_name": "Stack",
                        "attributes": [],
                        # The pair the platform emits for the mode declared on
                        # this component: one variable per (mode, output).
                        "deratings": [
                            {"mode": "df_H2", "flow": "H2"},
                            {"mode": "df_H2", "flow": "O2"},
                        ],
                    },
                    "id-tank": {
                        "name": "Local",
                        "class_name": "Tank",
                        "attributes": [],
                    },
                },
                "connections": {
                    "c1": _connection("id-source", "id-stack", "H2O"),
                    "c2": _connection("id-battery", "id-stack", "Elec"),
                    "c3": _connection("id-stack", "id-tank", "H2"),
                },
            },
        },
        "kb": {
            "component_templates": {
                "WaterSource": WATER_SOURCE_TEMPLATE,
                "Battery": BATTERY_TEMPLATE,
                "Stack": STACK_TEMPLATE,
                "Tank": TANK_TEMPLATE,
            }
        },
    }


# ---------------------------------------------------------------------------
# The two systems
# ---------------------------------------------------------------------------


def build_reference():
    """The reference plant, declared directly against the shipped classes."""
    system = muscadet.System(name="H2StackRef")

    system.add_component(
        name="S_H2O", cls="SourceContinuous", flow="H2O", rate=H2_SOURCE_RATE
    )
    system.add_component(
        name="B1",
        cls="CapacityContinuous",
        flow="Elec",
        ports="out",
        capacity=H2_BATTERY_CAPACITY,
        content_init={"Elec": H2_BATTERY_CONTENT},
        capacity_name="battery",
    )
    system.add_component(
        name="Electro",
        cls="TransformerContinuous",
        flows_in=list(H2_CONS),
        flows_out=list(H2_PROD),
        rules=[dict(name="electrolysis", cons=H2_CONS, prod=H2_PROD)],
    )
    system.add_component(
        name="Local",
        cls="CapacityContinuous",
        flow="H2",
        ports="both",
        capacity=H2_TANK_CAPACITY,
        content_init={"H2": H2_TANK_CONTENT},
        capacity_name="tank",
        fill_rate=H2_TANK_FILL_RATE,
    )

    system.connect_flow(source="S_H2O", target="Electro", flow_name="H2O")
    system.connect_flow(source="B1", target="Electro", flow_name="Elec")
    system.connect_flow(source="Electro", target="Local", flow_name="H2")

    return system


def build_rebuilt():
    """The same plant, imported from the payload above."""
    return system_from_export(
        h2_payload(),
        name="H2StackRebuilt",
        create_default_out_automata=False,
    )


def finish(system):
    """The declaration the importer does not carry yet, plus the indicators."""
    system.comp["Electro"].add_delay_failure_mode(
        name="df_H2",
        failure_time=H2_FAILURE_TIME,
        repair_time=H2_REPAIR_TIME,
        failure_effects=[(".*", 0.0)],
    )
    for component, var in H2_OBSERVED:
        system.add_indicator_var(
            component=f"^{component}$", var=f"^{var}$", stats=["mean"]
        )
    return system


def sampled(system):
    """Simulate once and read every indicator's sampled trajectory."""
    system.simulate(H2_SCHEDULE)

    samples = {}
    for record in system.indic_to_frame().to_dict("records"):
        if record["stat"] != "mean":
            continue
        samples.setdefault(record["name"], {})[record["instant"]] = record["values"]

    return {
        name: [values[instant] for instant in sorted(values)]
        for name, values in samples.items()
    }


# ---------------------------------------------------------------------------
# Snapshots: PyCATSHOO holds ONE system at a time
# ---------------------------------------------------------------------------
#
# ``Il est interdit de construire plus d'un systeme``. The two builds therefore
# never coexist: each is raised, read into plain data, simulated, and torn down
# before the other is raised. Everything compared below is that data, which is
# also what makes the comparison honest -- nothing is read off a live engine
# object whose identity could stand in for its declaration.


def flow_shape(comp):
    """The declared flows of a component, by side, name and family."""
    return {
        side: {name: type(flow).__name__ for name, flow in flows.items()}
        for side, flows in (("in", comp.flows_in), ("out", comp.flows_out))
    }


def capacity_shape(comp):
    return {
        name: {
            "volume": capacity.capacity,
            "side": capacity.side,
            "content_init": dict(capacity.content_init),
            "fill_rate": capacity.fill_rate,
            "flows": [
                (entry.name, entry.weight, entry.side) for entry in capacity.flows
            ],
        }
        for name, capacity in comp.capacities.items()
    }


def rule_shape(comp):
    return {
        name: [
            {
                "name": rule.name,
                "cons": dict(rule.cons),
                "prod": dict(rule.prod),
                "cond": [
                    (op.name, op.port, op.negate, op.op, op.value) for op in rule.cond
                ],
            }
            for rule in rule_set.rules
        ]
        for name, rule_set in comp.rule_sets.items()
    }


def derating_shape(comp):
    """The derating variables allocated on each continuous output, by name."""
    return {
        flow_name: sorted(flow.derating_var_name(mode) for mode in flow.derating)
        for flow_name, flow in comp.flows_continuous_out.items()
    }


def structure_of(system):
    return {
        name: {
            "cls": type(comp).__name__,
            "flows": flow_shape(comp),
            "capacities": capacity_shape(comp),
            "rules": rule_shape(comp),
            "deratings": derating_shape(comp),
            "spec": muscadet.component_spec(comp),
        }
        for name, comp in system.comp.items()
    }


def snapshot(builder):
    """Raise a plant, read it into data, simulate it, and tear it down."""
    system = finish(builder())
    try:
        return {"structure": structure_of(system), "run": sampled(system)}
    finally:
        system.deleteSys()
        cod3s.terminate_session()


@pytest.fixture(scope="module")
def both():
    return snapshot(build_reference), snapshot(build_rebuilt)


COMPONENTS = ("S_H2O", "B1", "Electro", "Local")


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_the_same_components_are_declared(both):
    reference, rebuilt = both
    assert sorted(rebuilt["structure"]) == sorted(reference["structure"])
    assert set(COMPONENTS) <= set(rebuilt["structure"])


def test_no_component_is_the_shipped_class(both):
    """The one difference that is assumed: a data declaration is an ObjFlow.

    The reference is four shipped classes; the rebuild is four generic
    components carrying the same declaration. Asserted rather than glossed
    over, so the equivalence claimed below is read for what it is.
    """
    reference, rebuilt = both
    assert [reference["structure"][name]["cls"] for name in COMPONENTS] == [
        "SourceContinuous",
        "CapacityContinuous",
        "TransformerContinuous",
        "CapacityContinuous",
    ]
    assert {rebuilt["structure"][name]["cls"] for name in COMPONENTS} == {"ObjFlow"}


@pytest.mark.parametrize("name", COMPONENTS)
@pytest.mark.parametrize("facet", ["flows", "capacities", "rules", "deratings"])
def test_the_declared_facets_are_the_same(both, name, facet):
    reference, rebuilt = both
    assert rebuilt["structure"][name][facet] == reference["structure"][name][facet]


def test_the_declaration_reads_back_identically(both):
    """``component_spec`` of both builds agree, bar the class it was read from.

    The strongest structural statement available: what muscadet itself
    considers the declaration of each component.
    """
    reference, rebuilt = both
    for name in COMPONENTS:
        left = dict(reference["structure"][name]["spec"])
        right = dict(rebuilt["structure"][name]["spec"])
        left.pop("source_cls")
        right.pop("source_cls")
        assert right == left, name


def test_the_derating_variables_were_preallocated(both):
    """The payload's ``deratings`` section allocated what the mode then found."""
    _, rebuilt = both
    assert rebuilt["structure"]["Electro"]["deratings"] == {
        "H2": ["df_H2_derating_H2"],
        "O2": ["df_H2_derating_O2"],
    }


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_every_trajectory_matches(both):
    reference, rebuilt = both
    assert set(rebuilt["run"]) == set(reference["run"])

    for name, values in reference["run"].items():
        assert rebuilt["run"][name] == pytest.approx(values, nan_ok=True), name


def test_the_plant_still_behaves_as_the_reference_documents(both):
    """A handful of the reference's own claims, read off the REBUILT run.

    Matching trajectories would also be satisfied by two identically broken
    models, so the numbers the reference argues for are checked directly.
    """
    _, rebuilt = both
    run = rebuilt["run"]

    def trace(component, var):
        return run[f"{component}_{var}"]

    running = (1, 2, 5)
    down = (3, 4)

    for instant in running:
        assert trace("Electro", "H2_fed_out")[instant] == pytest.approx(H2_SCALE)
        assert trace("Electro", "Elec_demand_out")[instant] == pytest.approx(
            H2_SCALE * H2_CONS["Elec"]
        )
        assert math.isinf(trace("B1", "Elec_capability_out")[instant])

    for instant in down:
        assert trace("Electro", "df_H2_derating_H2")[instant] == 0.0
        assert trace("Electro", "H2O_fed_in")[instant] == pytest.approx(0.0)

    assert trace("Local", "tank_qty_H2")[-1] == pytest.approx(4.5)
    assert trace("B1", "battery_qty_Elec")[-1] == pytest.approx(98.5)
