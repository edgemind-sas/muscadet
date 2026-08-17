"""A transfer that cannot be supplied is capped, and the shortfall is readable.

Saturation is a legitimate physical state: a wall conducts what the stream
brings it, and no more. An INVISIBLE saturation is not, and it is the defect
class this release has spent its life closing -- a valve declared stuck that
never blocks, a plant reporting availability through a mode that never fired.

So a pair publishes two numbers of its own, ``{pair}_requested`` and
``{pair}_moved``, rather than leaving the gap to be inferred from the balances,
where a rule set's draw and a capacity's fill are mixed into the same figures.

The engine constraint this unit exists around: PyCATSHOO refuses ``setValue``
on a variable its solver does not know about during the differential
resolution, and the refusal lands at the first integration step rather than at
declaration. Both publications are therefore declared explicit at the pre-run
step, beside the capability channel, which is what
``test_the_publications_are_declared_to_the_solver`` proves by the simple fact
that the model runs at all.
"""

import pytest

import cod3s
import muscadet

TSA_CLOCK = 5.0

#: The supply the saturated wall stands behind, and what it asks for.
TSA_SUPPLY = 1.0
TSA_ASKED = 5.0

#: The comfortable case, well inside its supply.
TSA_EASY = 3.0
TSA_EASY_SUPPLY = 10.0


def fixed(value):
    return muscadet.Transfer(fun=lambda comp: value, continuous=True)


class TsaSource(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="x", var_fed_default=kwargs["rate"])


class TsaWall(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="x", var_demand_default=TSA_EASY_SUPPLY)
        self.add_flow_continuous_out(name="x")
        self.add_transfer("conduct", flows=["x", "x"], equation=fixed(kwargs["rate"]))


class TsaSink(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="x", var_demand_default=TSA_EASY_SUPPLY)


def build_system():
    system = muscadet.System(name="TsaSys")

    for prefix, supply, asked in (
        ("SAT", TSA_SUPPLY, TSA_ASKED),
        ("EASY", TSA_EASY_SUPPLY, TSA_EASY),
    ):
        system.add_component(name=f"SRC_{prefix}", cls="TsaSource", rate=supply)
        system.add_component(name=f"WALL_{prefix}", cls="TsaWall", rate=asked)
        system.add_component(name=f"SNK_{prefix}", cls="TsaSink")
        system.connect_flow(
            source=f"SRC_{prefix}", target=f"WALL_{prefix}", flow_name="x"
        )
        system.connect_flow(
            source=f"WALL_{prefix}", target=f"SNK_{prefix}", flow_name="x"
        )

    system.comp["SRC_SAT"].add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": TSA_CLOCK},
        cond_occ_21=False,
    )

    return system


@pytest.fixture(scope="module")
def the_system():
    system = build_system()
    system.isimu_start()
    system.isimu_step_forward()
    yield system


def pair_of(system, comp_name):
    return system.comp[comp_name].transfers["conduct"]


def published(system, comp_name):
    """The two numbers as the MODEL carries them, not as Python remembers them."""
    pair = pair_of(system, comp_name)
    return pair.var_requested.value(), pair.var_moved.value()


def test_the_publications_are_declared_to_the_solver(the_system):
    """The model ran, so the pre-run declaration happened.

    Without it the production sweep's setValue is refused at the first
    integration step -- far from the declaration that caused it, and with a
    message about the differential system rather than about the pair.
    """
    requested, moved = published(the_system, "WALL_SAT")

    assert requested > 0.0
    assert moved > 0.0


def test_a_saturated_transfer_moves_what_was_available(the_system):
    """Covers AE4: 5 asked of a supply of 1, and neither side exceeds it."""
    requested, moved = published(the_system, "WALL_SAT")

    assert requested == pytest.approx(TSA_ASKED)
    assert moved == pytest.approx(TSA_SUPPLY)


def test_the_two_numbers_differ_and_both_are_readable(the_system):
    """Covers AE5. The gap is published, not inferred from the balances."""
    requested, moved = published(the_system, "WALL_SAT")

    assert requested != pytest.approx(moved)
    assert pair_of(the_system, "WALL_SAT").shortfall == pytest.approx(
        TSA_ASKED - TSA_SUPPLY
    )


def test_an_unsaturated_transfer_reports_the_two_equal(the_system):
    requested, moved = published(the_system, "WALL_EASY")

    assert requested == pytest.approx(TSA_EASY)
    assert moved == pytest.approx(TSA_EASY)
    assert pair_of(the_system, "WALL_EASY").shortfall == pytest.approx(0.0)


def test_neither_side_exceeds_what_was_available(the_system):
    """The cap is on the pair, so both balances move by the same capped value."""
    consumption, production = the_system.comp["WALL_SAT"].evaluate_production()

    assert consumption["x"] == pytest.approx(TSA_SUPPLY)
    assert production["x"] == pytest.approx(TSA_SUPPLY)


def test_the_readback_is_per_step_state_and_not_a_latch(the_system):
    """A pair whose supply recovers reports the two equal again.

    Written rather than accumulated: a latch would make the first saturation of
    a mission look permanent, which is exactly the wrong reading for a
    transient one.
    """
    wall = the_system.comp["WALL_EASY"]
    pair = wall.transfers["conduct"]

    pair.last_requested, pair.last_moved = 99.0, 1.0
    pair.publish()
    assert pair.var_moved.value() == pytest.approx(1.0)

    wall.compute_production()
    assert pair.var_requested.value() == pytest.approx(pair.var_moved.value())


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
