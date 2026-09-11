"""The same document, rebuilt from three key orders, declares the same system.

``test_system_build_order_001`` pins the derived order on data alone. This
module pins the claim that order is for: ``build_system`` raises the SAME
system from a document whatever order its ``components`` are written in, and
the proof is the document the rebuilt system declares back.

Three orders are exercised, and the third is what makes the statement a
property rather than two examples:

- the order the declaration came out in;
- its SORTED form, which is what any consumer may hand back -- a formatter, a
  round trip through a database, an engine whose maps are ordered by key
  (``serde_json`` over a ``BTreeMap``, which is the RAICHU path);
- a PERMUTATION drawn from a fixed seed, which covers the orders neither of
  the other two produces.

The subject is the H2 electrolysis plant, the hardest continuous case shipped:
capacities, a rule with a limiting reagent, a derating failure mode and an
indicator. Its components reference each other only through ``connections``,
which ``build_system`` wires once every component exists -- so this module is
the guard that the whole function stays order-insensitive, beside the guard on
the ordering itself.

**One live system at a time.** PyCATSHOO forbids more than one per process, so
the fixture builds, declares, tears the session down, and builds again, four
times over. That constraint is the reason the comparison is made on DOCUMENTS
rather than on systems: two documents compare without anything being alive.
"""

import json
import random

import cod3s
import pytest

import muscadet
import muscadet.kb.continuous  # noqa: F401  -- registers the shipped classes
from muscadet.declare import SystemSpecError, build_system, system_spec

CONS = {"H2O": 4, "Elec": 1}
PROD = {"H2": 1, "O2": 1}

#: The seed the permutation is drawn with, so a failure is reproducible.
SEED = 20260910


def build_by_hand(system):
    system.add_component(name="S_H2O", cls="SourceContinuous", flow="H2O", rate=2)
    system.add_component(
        name="B1",
        cls="CapacityContinuous",
        flow="Elec",
        ports="out",
        capacity=100,
        content_init={"Elec": 100},
        capacity_name="battery",
    )
    system.add_component(
        name="Electro",
        cls="TransformerContinuous",
        flows_in=list(CONS),
        flows_out=list(PROD),
        rules=[dict(name="electrolysis", cons=CONS, prod=PROD)],
    )
    system.add_component(
        name="Local",
        cls="CapacityContinuous",
        flow="H2",
        ports="both",
        capacity=6,
        content_init={"H2": 3},
        capacity_name="tank",
        fill_rate=1,
    )
    system.connect_flow(source="S_H2O", target="Electro", flow_name="H2O")
    system.connect_flow(source="B1", target="Electro", flow_name="Elec")
    system.connect_flow(source="Electro", target="Local", flow_name="H2")
    system.comp["Electro"].add_delay_failure_mode(
        name="df_H2", failure_time=2, repair_time=2, failure_effects=[(".*", 0.0)]
    )
    system.add_indicator_var(component="^Local$", var="^tank_qty_H2$", stats=["mean"])
    return system


def a_cyclic_document():
    """Two components, each one's failure mode conditioned on the other's.

    The shape is the platform's "condition on another mode" (``ref_mode_mdd``),
    filed here inside a component's ``failure_modes`` section so that every
    muscadet reads the declaration: the SUBJECT is the loop, not the section
    that carries it.
    """

    def conditioned_on(name, other):
        return {
            "name": name,
            "failure_modes": [
                {
                    "cls": "exp",
                    "name": "fm",
                    "failure_rate": 1e-4,
                    "failure_cond": [
                        [{"attr": "occ", "obj": other, "ope": "==", "value": True}]
                    ],
                }
            ],
        }

    return {
        "version": "1.0.0",
        "name": "Cyclic",
        "components": {
            "Fissure": conditioned_on("Fissure", "Detect"),
            "Detect": conditioned_on("Detect", "Fissure"),
        },
    }


def reordered(document, keys):
    """The same declaration, its ``components`` filed in ``keys`` order."""
    out = dict(document)
    out["components"] = {key: document["components"][key] for key in keys}
    return out


def without_provenance(components):
    """Strip ``source_cls``: origin, not identity.

    A rebuilt component's origin IS the document, so it reports ``ObjFlow``
    where the hand-built one reported ``SourceContinuous``. Two components are
    the same when they declare the same thing.
    """
    return {
        name: {k: v for k, v in spec.items() if k != "source_cls"}
        for name, spec in components.items()
    }


def comparable(document):
    """A declaration reduced to what two orders of it must agree on."""
    out = {k: v for k, v in document.items() if k != "name"}
    out["components"] = without_provenance(document["components"])
    return out


@pytest.fixture(scope="module")
def the_orders():
    """Refuse a cycle, then declare once and rebuild from three key orders."""
    # The cycle first, and on a system of its own, so that the refusal is
    # measured where it matters: BEFORE anything is built. A system that came
    # back holding half a model would be worse than one that refused.
    guard = muscadet.System(name="BuildOrderCycle")
    refusal = None
    try:
        build_system(a_cyclic_document(), system=guard)
    except SystemSpecError as error:
        refusal = error
    built_anyway = sorted(guard.comp or {})
    guard.deleteSys()
    cod3s.terminate_session()

    original = build_by_hand(muscadet.System(name="BuildOrderRef"))
    declared = json.loads(json.dumps(system_spec(original)))
    original.deleteSys()
    cod3s.terminate_session()

    keys = list(declared["components"])

    in_sorted_order = sorted(keys)
    system = build_system(
        reordered(declared, in_sorted_order), system=muscadet.System(name="OrderSorted")
    )
    from_sorted = system_spec(system)
    system.deleteSys()
    cod3s.terminate_session()

    rng = random.Random(SEED)
    in_drawn_order = list(keys)
    rng.shuffle(in_drawn_order)
    system = build_system(
        reordered(declared, in_drawn_order), system=muscadet.System(name="OrderDrawn")
    )
    from_drawn = system_spec(system)

    yield {
        "system": system,
        "declared": declared,
        "sorted_keys": in_sorted_order,
        "drawn_keys": in_drawn_order,
        "from_sorted": from_sorted,
        "from_drawn": from_drawn,
        "cycle_refusal": refusal,
        "cycle_built_anyway": built_anyway,
    }


# ----------------------------------------------------------------------
# The orders really are three different orders
# ----------------------------------------------------------------------


def test_the_three_orders_differ(the_orders):
    """Otherwise the module proves nothing and says it does."""
    written = list(the_orders["declared"]["components"])

    assert the_orders["sorted_keys"] != written
    assert the_orders["drawn_keys"] != written
    assert the_orders["drawn_keys"] != the_orders["sorted_keys"]


# ----------------------------------------------------------------------
# The claim: same document, any order, same system
# ----------------------------------------------------------------------


def test_the_sorted_document_rebuilds(the_orders):
    """The order a consumer is entitled to hand back."""
    assert comparable(the_orders["from_sorted"]) == comparable(the_orders["declared"])


def test_a_drawn_order_rebuilds(the_orders):
    """And the orders no consumer produces on purpose."""
    assert comparable(the_orders["from_drawn"]) == comparable(the_orders["declared"])


def test_the_two_rebuilds_declare_the_same_document(the_orders):
    """The sharpest form of the claim: two orders, one system.

    Compared without stripping anything -- both sides are rebuilds, so their
    provenance agrees too. Only the system's own name, which is the caller's
    and not the document's subject, is left out.
    """
    left = {k: v for k, v in the_orders["from_sorted"].items() if k != "name"}
    right = {k: v for k, v in the_orders["from_drawn"].items() if k != "name"}

    assert left == right


def test_the_wiring_survives_every_order(the_orders):
    """Connections are wired once every component exists, and stay identical.

    Pinned apart because this is the part a reordered build could plausibly
    break: a connection names two components, and both have to be there.
    """
    assert (
        the_orders["from_sorted"]["connections"]
        == the_orders["declared"]["connections"]
    )
    assert (
        the_orders["from_drawn"]["connections"] == the_orders["declared"]["connections"]
    )


def test_the_failure_mode_survives_every_order(the_orders):
    """A mode lost in a reordered crossing would make a plant look reliable."""
    for document in (the_orders["from_sorted"], the_orders["from_drawn"]):
        assert document["components"]["Electro"].get("failure_modes")


# ----------------------------------------------------------------------
# The cycle, at the build scale
# ----------------------------------------------------------------------


def test_a_cycle_is_refused_by_name_and_not_by_a_key_error(the_orders):
    """``KeyError: 'Rail_1__Rail__Rail__Fissure_O'`` is what the engine says.

    It names neither the document, nor the component that referenced it, nor
    the fact that both are declared a few keys apart. ``build_system`` says
    which components form the loop instead.
    """
    refusal = the_orders["cycle_refusal"]

    assert refusal is not None, "a cyclic document built without complaining"
    assert isinstance(refusal, SystemSpecError)
    assert not isinstance(refusal, KeyError)
    assert "'Fissure'" in str(refusal) and "'Detect'" in str(refusal)
    assert "cycle" in str(refusal)


def test_a_refused_cycle_leaves_the_system_untouched(the_orders):
    """Refused BEFORE the first component, not halfway through.

    A system carrying the half of a cyclic document that happened to be built
    first is a system nobody can explain, and it would be handed back to the
    caller, who owns it.
    """
    assert the_orders["cycle_built_anyway"] == []


def test_delete(the_orders):
    the_orders["system"].deleteSys()
    cod3s.terminate_session()
