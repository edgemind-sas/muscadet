"""A standalone mode over overlapping combinations gates each target once.

A second-order standalone mode builds one automaton per target combination,
so a target is covered by several of them: the order-1 one on that target and
the order-2 one on both. ``ObjFailureMode`` resolves its flow-name patterns
itself, per combination, in ``_build_fm_automaton``; up to 5.15.0 it then
handed each combination its records, and every combination clamped the
target's availability gate on its own. A combination at rest kept asserting
``repair_effects`` on a target that another combination had put in ``occ``:
an availability held on both faces never settled (cod3s#4, the run spun with
no diagnostic).

Since cod3s 1.23.0 the engine clamps a target once over its covering
combinations, ``failure_effects`` while one of them is in ``occ`` and
``repair_effects`` only while all of them are at rest, and lets a subclass
feed that clamp its own records. The availability gates now go through it;
the deratings of continuous outputs stay per automaton, composed by minimum.

Delay laws give one deterministic timeline: order 1 fails at 2 and repairs
after 2, order 2 fails at 3 and repairs after 10. cc_1 / cc_2: occ 2, rep 4,
occ 6, rep 8, occ 10, rep 12, occ 14. cc_1_2: occ 3, rep 13. Sampled at the
schedule below, each target is covered by a combination in ``occ`` everywhere
but at 0 and 13.5, and at 2.5 only by its order-1 combination.
"""

import cod3s
import pytest
from cod3s.pycatshoo.system import PycMCSimulationParam

import muscadet

#: The wrapper classes warn on instantiation; the deprecation itself is
#: asserted in tests/test_objfailuremode_deprecation.py.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SCHEDULE = [0.0, 2.5, 3.5, 4.5, 12.5, 13.5, 14.5]
#: Each target covered by a combination in occ, per SCHEDULE instant.
IN_OCC = [False, True, True, True, True, False, True]


class CcfShSource(muscadet.ObjFlow):
    """One discrete output a mode can gate."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="f1", var_prod_default=True)


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="CcfShared")
    for name in ("CcfShA", "CcfShB"):
        system.add_component(name=name, cls="CcfShSource")

    # The cod3s#4 shape: the availability is held on both faces.
    system.add_component(
        cls="ObjFailureModeDelay",
        fm_name="ccf",
        targets=["CcfShA", "CcfShB"],
        failure_effects={"f1": False},
        repair_effects={"f1": True},
        failure_param=[2.0, 3.0],
        repair_param=[2.0, 10.0],
    )
    return system


def test_the_mode_shares_its_clamps(the_system):
    mode = the_system.comp["CcfShX__ccf"]
    assert mode.shared_target_clamps is True
    # The declared patterns still read back as declared.
    assert mode.failure_effects == {"f1": False}
    assert mode.repair_effects == {"f1": True}


def test_availability_follows_the_covering_combinations(the_system):
    indicators = {
        name: the_system.add_indicator_var(
            component=name,
            var=the_system.comp[name].flows_out["f1"].var_fed.basename(),
            stats=["mean"],
            name=f"{name}_f1",
        )[0]
        for name in ("CcfShA", "CcfShB")
    }
    the_system.simulate(PycMCSimulationParam(nb_runs=1, schedule=SCHEDULE, seed=1))
    expected = [0.0 if occ else 1.0 for occ in IN_OCC]
    for name, indicator in indicators.items():
        assert indicator.values["values"].to_list() == expected, name


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
