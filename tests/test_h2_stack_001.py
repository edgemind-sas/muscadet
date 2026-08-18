"""An industrial hydrogen slice, composed of shipped components only.

The four-component electrolysis plant of the IMDR "Industrie 4.0" study, whose
figures are open data: a water source, a battery, an electrolyser stack and a
local hydrogen tank, with a delay failure mode on the stack.

It earns its place here for one reason above the others: **not a single
component is subclassed**. What the original model expressed by subclassing a
flow object and overriding ``compute_iflow`` in Python, MUSCADET expresses as
declared ``cons`` / ``prod`` coefficients on a rule, and the whole plant is
four ``add_component`` calls against ``muscadet.kb.continuous``. That is the
strongest claim the knowledge base can make, and it is asserted below rather
than argued.

The plant
---------
=========  ==========================  ===================================
Component  Shipped class               Declaration
=========  ==========================  ===================================
``S_H2O``  ``SourceContinuous``        water at a rate of 2
``B1``     ``CapacityContinuous``      battery, capacity 100, stocked at 100
``Electro``  ``TransformerContinuous``  ``4 H2O + 1 Elec -> 1 H2 + 1 O2``
``Local``  ``CapacityContinuous``      tank, capacity 6, stocked at 3
=========  ==========================  ===================================

``df_H2`` is a delay failure mode on the stack: it fails at t = 2, is repaired
at t = 4, and derates every continuous output to zero while it holds.

What it demonstrates
--------------------
**The limiting reagent, as coefficients.** ``min(H2O/4, Elec/1)`` is exactly
what ``cons={"H2O": 4, "Elec": 1}`` states. Water arrives at 2 against a
coefficient of 4, so the stack runs at scale 0.5 and no more, however much
electricity the battery holds.

**Two correlated outputs on one rule.** H2 and O2 are produced by the same
rule, so they scale together and one derating takes both down. That is why a
rule set is declared on the COMPONENT and not one output at a time.

**A demand bounded by what the other inputs can honour.** The stack asks its
battery for **0.5** of electricity, not its nominal 1. That is the capability
sweep: the water can only sustain a scale of 0.5, so asking for a full unit
would claim electricity no reaction could use. The original model asked for
the nominal 1, was delivered 1, reacted 0.5, and the missing 0.5 entered no
reaction, no stock and no output.

**A derated component draws nothing.** While ``df_H2`` holds, the stack's water
intake reads 0 rather than the nominal 2: a dead output produces nothing and
therefore consumes nothing, so it does not go on emptying the tanks behind it
for the rest of the mission.

**Conservation across the mission.** The battery falls 100 to 98.5 and the tank
rises 3 to 4.5: 1.5 of electricity for 1.5 of hydrogen, which is the rule's
one-for-one coefficient.
"""

import math

import pytest

import cod3s
import muscadet
import muscadet.kb.continuous  # noqa: F401  -- registers the shipped classes

# The plant, as the study declares it
# ===================================

H2_SOURCE_RATE = 2.0  # S_H2O: flow_nominal
H2_BATTERY_CAPACITY = 100.0  # B1: capacity
H2_BATTERY_CONTENT = 100.0  # B1: content_ini
H2_TANK_CAPACITY = 6.0  # Local: capacity
H2_TANK_CONTENT = 3.0  # Local: content_ini

#: The stack's reaction: 4 water and 1 electricity give 1 hydrogen and 1 oxygen.
H2_CONS = {"H2O": 4.0, "Elec": 1.0}
H2_PROD = {"H2": 1.0, "O2": 1.0}

#: What the tank claims for itself while it has room. The study's tank claims
#: without bound and its stack answers by falling back to its nominal output;
#: naming the number is how that sentinel reads in MUSCADET, which propagates an
#: unbounded claim as "deliver whatever you can" and has no nominal fallback.
H2_TANK_FILL_RATE = 1.0

H2_FAILURE_TIME = 2.0
H2_REPAIR_TIME = 2.0

#: The scale the water holds the reaction to: 2 arriving against a coefficient
#: of 4. The battery could sustain 100, so the water is the limiting reagent.
H2_SCALE = H2_SOURCE_RATE / H2_CONS["H2O"]

#: The six instants the study samples.
H2_SCHEDULE = {"nb_runs": 1, "schedule": [{"start": 0, "end": 5, "nvalues": 6}]}

#: Variables read back, in the study's own terms.
H2_OBSERVED = (
    ("Electro", "H2_fed_out"),
    ("Electro", "O2_fed_out"),
    ("Electro", "H2O_demand_out"),
    ("Electro", "Elec_demand_out"),
    ("Electro", "df_H2_derating_H2"),
    ("Electro", "df_H2_derating_O2"),
    ("Electro", "H2O_fed_in"),
    ("Electro", "Elec_fed_in"),
    ("Local", "tank_qty_H2"),
    ("B1", "Elec_capability_out"),
    ("B1", "battery_qty_Elec"),
    ("S_H2O", "H2O_fed_out"),
    ("S_H2O", "H2O_capability_out"),
)

#: Which shipped class each component is declared as. Asserted, because "no
#: component is subclassed" is the claim this module exists to make.
H2_CLASSES = {
    "S_H2O": "SourceContinuous",
    "B1": "CapacityContinuous",
    "Electro": "TransformerContinuous",
    "Local": "CapacityContinuous",
}


def build_system():
    system = muscadet.System(name="H2StackSys")

    system.add_component(
        name="S_H2O",
        cls="SourceContinuous",
        flow="H2O",
        rate=H2_SOURCE_RATE,
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
    # O2 is produced and deliberately wired to nothing, as in the study.

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


@pytest.fixture(scope="module")
def the_system():
    system = build_system()
    yield system


@pytest.fixture(scope="module")
def the_run(the_system):
    """Simulate once and read every indicator's sampled trajectory."""
    the_system.simulate(H2_SCHEDULE)

    samples = {}
    for record in the_system.indic_to_frame().to_dict("records"):
        if record["stat"] != "mean":
            continue
        samples.setdefault(record["name"], {})[record["instant"]] = record["values"]

    return {
        name: [values[instant] for instant in sorted(values)]
        for name, values in samples.items()
    }


def trace(run, component, var):
    return run[f"{component}_{var}"]


#: The instants the stack is down: it fails at 2 and is repaired at 4, so the
#: samples at 3 and 4 see it derated.
DOWN = (3, 4)

#: Instant 0 is not a reading of the plant: MUSCADET registers its sweeps as
#: PDMP equations, and a PDMP equation is not evaluated at t = 0.
RUNNING = (1, 2, 5)


# ----------------------------------------------------------------------
# The claim this module exists to make
# ----------------------------------------------------------------------


def test_no_component_is_subclassed(the_system):
    """Four shipped classes, four `add_component` calls, no Python overriding.

    The original expresses the stack by subclassing a flow object and writing
    `compute_iflow`; here the same reaction is two coefficient maps. Read off
    the runtime type rather than a declaration: what matters is that the object
    IS the shipped class and not a descendant of it.
    """
    for name, expected in H2_CLASSES.items():
        assert type(the_system.comp[name]).__name__ == expected


# ----------------------------------------------------------------------
# The limiting reagent
# ----------------------------------------------------------------------


def test_the_water_holds_the_reaction_to_half_a_unit(the_run):
    """`min(H2O/4, Elec/1)` with H2O at 2: scale 0.5, however full the battery."""
    assert H2_SCALE == pytest.approx(0.5)

    for instant in RUNNING:
        assert trace(the_run, "Electro", "H2_fed_out")[instant] == pytest.approx(
            H2_SCALE * H2_PROD["H2"]
        )


def test_the_battery_is_not_the_bottleneck(the_run):
    """It publishes an unbounded capability: a stocked volume serves without limit."""
    for instant in RUNNING:
        assert math.isinf(trace(the_run, "B1", "Elec_capability_out")[instant])

    for instant in RUNNING:
        assert trace(the_run, "S_H2O", "H2O_capability_out")[instant] == pytest.approx(
            H2_SOURCE_RATE
        )


def test_the_two_outputs_are_correlated(the_run):
    """One rule, so H2 and O2 move together at every instant."""
    assert trace(the_run, "Electro", "H2_fed_out") == pytest.approx(
        trace(the_run, "Electro", "O2_fed_out")
    )


# ----------------------------------------------------------------------
# The demand bounded by the other inputs
# ----------------------------------------------------------------------


def test_the_stack_asks_for_the_electricity_it_can_use(the_run):
    """0.5, not the nominal 1, and that is the whole of the capability sweep.

    Asking for a full unit would claim electricity no reaction could turn into
    hydrogen. The original model asked 1, was delivered 1, reacted 0.5, and the
    missing 0.5 entered no reaction, no stock and no output.
    """
    for instant in RUNNING:
        assert trace(the_run, "Electro", "Elec_demand_out")[instant] == pytest.approx(
            H2_SCALE * H2_CONS["Elec"]
        )


def test_the_water_demand_stays_at_the_declared_coefficient(the_run):
    """Nothing bounds the water: it is the reagent doing the limiting."""
    for instant in RUNNING:
        assert trace(the_run, "Electro", "H2O_demand_out")[instant] == pytest.approx(
            H2_CONS["H2O"]
        )


def test_what_is_asked_is_what_is_delivered_and_consumed(the_run):
    """Demand, delivery and consumption agree on the common path."""
    for instant in RUNNING:
        asked = trace(the_run, "Electro", "Elec_demand_out")[instant]
        received = trace(the_run, "Electro", "Elec_fed_in")[instant]

        assert received == pytest.approx(asked)


# ----------------------------------------------------------------------
# The failure
# ----------------------------------------------------------------------


def test_one_derating_takes_both_outputs_down(the_run):
    """`(".*", 0.0)` names every continuous output of the component."""
    for instant in DOWN:
        assert trace(the_run, "Electro", "df_H2_derating_H2")[instant] == 0.0
        assert trace(the_run, "Electro", "df_H2_derating_O2")[instant] == 0.0
        assert trace(the_run, "Electro", "H2_fed_out")[instant] == pytest.approx(0.0)
        assert trace(the_run, "Electro", "O2_fed_out")[instant] == pytest.approx(0.0)


def test_a_derated_stack_draws_nothing(the_run):
    """A dead output consumes nothing, so it stops emptying the tanks behind it.

    The water source reads 0 while the stack is down, where a model pushing its
    nominal regardless would keep sending 2 into a component that converts none
    of it -- water that is not converted, not stored and not leaked.
    """
    for instant in DOWN:
        assert trace(the_run, "Electro", "H2O_fed_in")[instant] == pytest.approx(0.0)
        assert trace(the_run, "S_H2O", "H2O_fed_out")[instant] == pytest.approx(0.0)
        assert trace(the_run, "Electro", "Elec_fed_in")[instant] == pytest.approx(0.0)


def test_the_stack_recovers_after_repair(the_run):
    """Repaired at t = 4, producing again at the sample that follows."""
    assert trace(the_run, "Electro", "df_H2_derating_H2")[5] == 1.0
    assert trace(the_run, "Electro", "H2_fed_out")[5] == pytest.approx(H2_SCALE)


# ----------------------------------------------------------------------
# Conservation over the mission
# ----------------------------------------------------------------------


def test_the_tank_gains_what_the_stack_produced(the_run):
    """3 to 4.5: three samples of production at 0.5 each."""
    tank = trace(the_run, "Local", "tank_qty_H2")

    assert tank[0] == pytest.approx(H2_TANK_CONTENT)
    assert tank[-1] == pytest.approx(4.5)


def test_the_battery_loses_what_the_reaction_used(the_run):
    """100 to 98.5, and 1.5 of electricity is exactly 1.5 of hydrogen."""
    battery = trace(the_run, "B1", "battery_qty_Elec")

    assert battery[0] == pytest.approx(H2_BATTERY_CONTENT)
    assert battery[-1] == pytest.approx(98.5)


def test_electricity_spent_equals_hydrogen_made(the_run):
    """The rule's one-for-one coefficient, over the whole mission."""
    spent = H2_BATTERY_CONTENT - trace(the_run, "B1", "battery_qty_Elec")[-1]
    made = trace(the_run, "Local", "tank_qty_H2")[-1] - H2_TANK_CONTENT

    assert spent == pytest.approx(made)
    assert spent == pytest.approx(1.5)


def test_instant_zero_reads_nothing(the_run):
    """A known artefact, pinned rather than worked around.

    MUSCADET registers its sweeps as PDMP equations only, and a PDMP equation
    is not evaluated at t = 0, so the first sample shows the declared initial
    state and no flow at all.
    """
    assert trace(the_run, "Electro", "H2_fed_out")[0] == pytest.approx(0.0)
    assert trace(the_run, "Electro", "Elec_demand_out")[0] == pytest.approx(0.0)
    assert trace(the_run, "Local", "tank_qty_H2")[0] == pytest.approx(H2_TANK_CONTENT)


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
