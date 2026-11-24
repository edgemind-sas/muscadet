"""A pure consumer takes its demand and hands the rest back.

A consumer carrying no rule publishes its declared demand on its input. Every
connection carries that WHOLE demand, so each supplier answers within it and
their sum may exceed it: sources of 9 and 3 behind a demand of 6 delivered 9.
A rule-bearing consumer has always handed the surplus back (R-12); the pure
consumer was left uncapped on the ground that it is the sink and takes what it
is given, which holds with one supplier only.

It is now capped at the demand it published and releases the rest in the
proportion each supplier delivered, through the same release a rule uses. So a
plain consumer and a 1:1 machine settle on the same rates, which this module
pins on several supply shapes. An unbounded claim caps nothing, and a sink
declared to take whatever it is given still does.

PyCATSHOO forbids more than one live system per process, so each scenario is
built, stepped once, read and deleted before the next one starts.
"""

import math

import cod3s
import pytest

import muscadet

# Imported for their side effect: a component class resolves by name.
from muscadet.kb.continuous import (  # noqa: F401
    ConsumerContinuous,
    SourceContinuous,
    TransformerContinuous,
)

SR_CLOCK = 1.0
SR_NEED = 6.0


def run(offers, demand, machine):
    """Rates each source delivers and what the receiver gets, after one step."""
    system = muscadet.System(name="SinkRelease")
    try:
        for i, offer in enumerate(offers):
            system.add_component(
                name=f"S{i}", cls="SourceContinuous", flow="q", rate=offer
            )
        if machine:
            system.add_component(
                name="R",
                cls="TransformerContinuous",
                flows_in=["q"],
                flows_out=["p"],
                rules=[dict(name="r", cons={"q": 1.0}, prod={"p": 1.0})],
            )
            system.add_component(
                name="C", cls="ConsumerContinuous", flow="p", demand=demand
            )
            system.connect_flow(source="R", target="C", flow_name="p")
        else:
            system.add_component(
                name="R", cls="ConsumerContinuous", flow="q", demand=demand
            )
        for i in range(len(offers)):
            system.connect_flow(source=f"S{i}", target="R", flow_name="q")
        system.comp["R"].add_atm2states(
            name="sr_clock",
            st1="s0",
            st2="s1",
            occ_law_12={"cls": "delay", "time": SR_CLOCK},
            cond_occ_21=False,
        )

        system.isimu_start()
        system.isimu_step_forward()

        sources = [
            system.comp[f"S{i}"].flows_out["q"].var_fed.value()
            for i in range(len(offers))
        ]
        return sources, system.comp["R"].flows_in["q"].var_fed.value()
    finally:
        system.isimu_stop()
        system.deleteSys()


@pytest.mark.parametrize(
    "offers, sources, got",
    [
        ((10.0,), [6.0], 6.0),
        ((9.0, 3.0), [4.0, 2.0], 6.0),
        ((4.0, 3.0), [24.0 / 7.0, 18.0 / 7.0], 6.0),
        ((5.0, 5.0, 5.0), [2.0, 2.0, 2.0], 6.0),
        ((2.0, 3.0), [2.0, 3.0], 5.0),
    ],
    ids=["one-source", "9-3", "4-3", "5-5-5", "short-supply"],
)
def test_a_pure_sink_takes_its_demand(offers, sources, got):
    """The surplus is handed back in proportion to what each source gave."""
    delivered, received = run(offers, SR_NEED, machine=False)
    assert delivered == pytest.approx(sources, abs=1e-5)
    assert received == pytest.approx(got, abs=1e-5)


@pytest.mark.parametrize("offers", [(9.0, 3.0), (4.0, 3.0), (5.0, 5.0, 5.0)])
def test_a_pure_sink_and_a_machine_agree(offers):
    """Whether or not the consumer carries a rule does not change the split."""
    sink = run(offers, SR_NEED, machine=False)
    machine = run(offers, SR_NEED, machine=True)
    assert sink[0] == pytest.approx(machine[0], abs=1e-5)
    assert sink[1] == pytest.approx(machine[1], abs=1e-5)


@pytest.mark.parametrize("offers", [(9.0, 3.0), (5.0, 5.0, 5.0)])
def test_an_unbounded_sink_still_takes_everything(offers):
    """``demand=inf`` caps nothing: every source delivers its whole offer."""
    delivered, received = run(offers, math.inf, machine=False)
    assert delivered == pytest.approx(list(offers))
    assert received == pytest.approx(sum(offers))


def test_delete():
    cod3s.terminate_session()
