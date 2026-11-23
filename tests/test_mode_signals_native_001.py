"""A standalone cod3s mode is visible to the loop analysis, and the façades are gone.

``muscadet.ObjFailureMode*`` were removed in 6.0.0. They were the only
standalone modes that published what they read and wrote on their targets
(``ObjFlow.mode_signals``), so a native ``cod3s.ObjFM*`` -- what the COD3S
Platform emits -- was invisible to ``muscadet.ordering``. The pre-run step now
publishes for every ``cod3s.ObjMode2S``, once per (mode, target).

Pinned here on a purely discrete system, where the pre-run step registers
nothing and only publishes:

- one entry per target, keyed ``{mode}@{target}``, so the targets of one
  common-cause mode stay apart;
- the effects named by exact basename, a key the target does not hold left out;
- a condition leaf naming ANOTHER component left out of this target's reads;
- the publication is idempotent, as a retried pre-run step needs it to be;
- the three removed class names refused by name, with their replacement.
"""

import cod3s
import pytest

import muscadet
from muscadet.mode_signals import condition_reads, publish_mode_signals


class MsnTarget(muscadet.ObjFlow):
    """One discrete input a condition can read, one output a mode can gate."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="cmd")
        self.add_flow_out(name="f", var_prod_default=True)


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="ModeSignalsNative")

    for name in ("MsnA", "MsnB", "MsnC"):
        system.add_component(name=name, cls="MsnTarget")

    system.add_component(
        cls="ObjFMExp",
        fm_name="ccf",
        targets=["MsnA", "MsnB"],
        target_name="MsnAB",
        failure_cond=[
            [
                {"attr": "cmd_fed_in", "value": True},
                {"obj": "MsnC", "attr": "f_fed_out", "value": True},
            ]
        ],
        failure_effects={"f_fed_available_out": False},
        failure_param=[0.1, 0.01],
        repair_param=[1.0, 1.0],
    )

    yield system


def test_nothing_is_published_at_declaration(the_system):
    for name in ("MsnA", "MsnB", "MsnC"):
        assert the_system.comp[name].mode_signals == {}, name


def test_the_pre_run_step_publishes_one_entry_per_target(the_system):
    the_system.prerun()

    expected = {
        "conditions": ["cmd_fed_in"],
        "effects": ["f_fed_available_out"],
    }
    assert the_system.comp["MsnA"].mode_signals == {"MsnAB__ccf@MsnA": expected}
    assert the_system.comp["MsnB"].mode_signals == {"MsnAB__ccf@MsnB": expected}
    # Read by the condition, but not a target: nothing is clamped on it.
    assert the_system.comp["MsnC"].mode_signals == {}


def test_the_publication_is_idempotent(the_system):
    before = {
        name: dict(the_system.comp[name].mode_signals) for name in ("MsnA", "MsnB")
    }
    publish_mode_signals(the_system)
    after = {
        name: dict(the_system.comp[name].mode_signals) for name in ("MsnA", "MsnB")
    }
    assert after == before


def test_a_callable_or_constant_condition_reads_nothing(the_system):
    target = the_system.comp["MsnA"]
    assert condition_reads(True, target) == set()
    assert condition_reads(lambda: True, target) == set()
    assert condition_reads(None, target) == set()


@pytest.mark.parametrize(
    "removed, replacement",
    [
        ("ObjFailureMode", "ObjFM"),
        ("ObjFailureModeExp", "ObjFMExp"),
        ("ObjFailureModeDelay", "ObjFMDelay"),
    ],
)
def test_a_removed_facade_is_refused_by_name(the_system, removed, replacement):
    with pytest.raises(TypeError, match=f"cls='{replacement}'"):
        the_system.add_component(
            cls=removed, fm_name="gone", targets=["MsnA"], failure_effects={"f": 0}
        )

    assert not hasattr(muscadet.obj, removed)


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
