"""``muscadet.ObjFailureMode*`` sit on the cod3s mode engine (no fork left).

The three classes used to REIMPLEMENT ``cod3s.ObjFM``: the same template hooks,
the same common-cause combinatorics, the same ``trans_name_prefix`` mechanics,
all on top of ``cod3s.PycComponent``. They are now thin subclasses of the
matching ``cod3s.ObjFM*``, which owns all of it through ``ObjMode2S``.

What this module pins is the seam that made the convergence possible without
moving a single generated name -- the part a future refactor is most likely to
get wrong:

* the class hierarchy itself, so a re-fork is caught;
* the muscadet spelling of effects, a REGEX over the target's FLOW names
  (``{"f.*": False}``, ``{".*": 0.0}``) resolving to what the flow offers a
  mode -- the availability variable of a discrete output, the derating
  variable of a continuous one. A native cod3s mode resolves by exact variable
  basename instead, and the two contracts have to coexist;
* the inversion the seam rests on: the effects are resolved in
  ``_build_fm_automaton``, which carries the automaton's NAME but not the
  combination it was built for, so the combination is recovered from the name
  through the same helper the engine names it with. A custom
  ``trans_name_prefix`` has to survive that round trip;
* the names themselves, spelled out here so a regression does not need the
  full before/after model dump to be seen;
* the refusal of the ``cod3s.ObjFM`` keywords whose effects the muscadet
  resolution cannot honour, rather than a silently effect-less mode.

One live system per process, so everything shares one.
"""

import cod3s
import muscadet
import pytest

from cod3s.pycatshoo.component import ObjMode2S
from muscadet.obj import ObjFailureMode, ObjFailureModeDelay, ObjFailureModeExp

#: The wrapper classes warn on instantiation; the deprecation itself is
#: asserted in tests/test_objfailuremode_deprecation.py.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

#: What the continuous output produces, and what the wildcard mode leaves.
FM_ENGINE_RATE = 10.0
FM_ENGINE_DERATED = 0.25


class FmEngineSource(muscadet.ObjFlow):
    """Feeds the gated targets, so their condition can hold."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="c1", var_prod_default=True)


class FmEngineTarget(muscadet.ObjFlow):
    """Two discrete outputs a pattern can match together, one input to gate on."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="c1")
        self.add_flow_out(name="f1", var_prod_default=True)
        self.add_flow_out(name="f2", var_prod_default=True)


class FmEngineMixed(muscadet.ObjFlow):
    """Both families of output, which is what makes ``".*"`` interesting."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=FM_ENGINE_RATE)
        self.add_flow_out(name="alive", var_prod_default=True)


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="FmEngine")

    system.add_component(name="FmSrc", cls="FmEngineSource")
    for name in ("FmA1", "FmA2", "FmGated", "FmStarved"):
        system.add_component(name=name, cls="FmEngineTarget")
    system.add_component(name="FmMix", cls="FmEngineMixed")

    # Everything but FmStarved is fed, so the dict-shorthand condition holds
    # on one gated mode and not on the other.
    for name in ("FmA1", "FmA2", "FmGated"):
        system.connect("FmSrc", "c1_out", name, "c1_in")

    # Second-order mode, effects declared as a regex over the flow names, and
    # a custom combination suffix the name inversion has to cope with.
    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="fmcc",
        targets=["FmA1", "FmA2"],
        failure_effects={"f.*": False},
        trans_name_prefix="__bin_{target_binary}",
        failure_param=[0.1, 0.2],
        repair_param=[0.3, 0.4],
    )

    # The muscadet dict shorthand: gate on the target's INPUT flows.
    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="fmgate",
        targets=["FmGated"],
        failure_effects={"f1": False},
        failure_cond={"c1": True},
        failure_param=[0.1],
        repair_param=[0.1],
    )
    system.add_component(
        cls="ObjFailureModeExp",
        fm_name="fmgate",
        targets=["FmStarved"],
        failure_effects={"f1": False},
        failure_cond={"c1": True},
        failure_param=[0.1],
        repair_param=[0.1],
    )

    # A wildcard against a component carrying both families at once.
    system.add_component(
        cls="ObjFailureModeDelay",
        fm_name="fmwild",
        targets=["FmMix"],
        failure_effects={".*": FM_ENGINE_DERATED},
        failure_param=[2.0],
        repair_param=[1e6],
    )

    return system


class TestFmEngineHierarchy:
    """The fork is gone: the three classes ARE cod3s modes."""

    def test_base_is_a_cod3s_fm(self):
        assert issubclass(ObjFailureMode, cod3s.ObjFM)
        assert issubclass(ObjFailureMode, ObjMode2S)

    def test_flavours_derive_from_their_cod3s_counterpart(self):
        assert issubclass(ObjFailureModeExp, cod3s.ObjFMExp)
        assert issubclass(ObjFailureModeDelay, cod3s.ObjFMDelay)

    def test_flavours_still_derive_from_the_muscadet_base(self):
        # 1.x ``isinstance(fm, ObjFailureMode)`` keeps holding.
        assert issubclass(ObjFailureModeExp, ObjFailureMode)
        assert issubclass(ObjFailureModeDelay, ObjFailureMode)

    def test_occurrence_law_hooks_come_from_cod3s(self):
        # Nothing left to maintain here: the law hooks are the engine's.
        for hook in (
            "set_occ_law_failure",
            "set_occ_law_repair",
            "set_default_failure_param_name",
            "set_default_repair_param_name",
        ):
            assert hook not in vars(ObjFailureModeExp)
            assert hook not in vars(ObjFailureModeDelay)
            assert hook not in vars(ObjFailureMode)


class TestFmEngineNames:
    """The generated names are the engine's, and they are the 1.x ones."""

    def test_component_name(self, the_system):
        # ``{factorized targets}__{fm_name}``, unchanged.
        assert "FmAX__fmcc" in the_system.comp
        assert "FmGated__fmgate" in the_system.comp
        assert "FmMix__fmwild" in the_system.comp

    def test_combination_automata(self, the_system):
        fm = the_system.comp["FmAX__fmcc"]
        assert list(fm.automata_d) == [
            "fmcc__bin_10",
            "fmcc__bin_01",
            "fmcc__bin_11",
        ]

    def test_states_and_transitions(self, the_system):
        aut = the_system.comp["FmAX__fmcc"].automata_d["fmcc__bin_11"]
        assert [st.name for st in aut.states] == ["rep__bin_11", "occ__bin_11"]
        assert [tr.name for tr in aut.transitions] == ["occ__bin_11", "rep__bin_11"]

    def test_single_target_mode_keeps_the_bare_names(self, the_system):
        aut = the_system.comp["FmGated__fmgate"].automata_d["fmgate"]
        assert [st.name for st in aut.states] == ["rep", "occ"]
        assert [tr.name for tr in aut.transitions] == ["occ", "rep"]

    def test_per_order_parameter_variables(self, the_system):
        fm = the_system.comp["FmAX__fmcc"]
        assert [v.basename() for v in fm.variables()] == [
            "lambda__1_o_2",
            "lambda__2_o_2",
            "mu__1_o_2",
            "mu__2_o_2",
        ]

    def test_single_target_parameters_carry_no_order_suffix(self, the_system):
        fm = the_system.comp["FmGated__fmgate"]
        assert [v.basename() for v in fm.variables()] == ["lambda", "mu"]


class TestFmEngineRegexEffects:
    """Effects are named by FLOW and matched as a regular expression."""

    def test_pattern_resolves_to_the_availability_gates(self, the_system):
        fm = the_system.comp["FmAX__fmcc"]
        records = fm.resolve_effects_on(
            the_system.comp["FmA1"], {"f.*": False}, "probe_key", "Failure"
        )
        assert [r["var"].basename() for r in records] == [
            "f1_fed_available_out",
            "f2_fed_available_out",
        ]
        assert all(r["value"] is False for r in records)

    def test_pattern_is_anchored_on_the_whole_flow_name(self, the_system):
        fm = the_system.comp["FmAX__fmcc"]
        records = fm.resolve_effects_on(
            the_system.comp["FmA1"], {"f1": False}, "probe_key", "Failure"
        )
        assert [r["var"].basename() for r in records] == ["f1_fed_available_out"]

    def test_pattern_matching_nothing_is_refused(self, the_system):
        fm = the_system.comp["FmAX__fmcc"]
        with pytest.raises(ValueError, match="does not match any flow out"):
            fm.resolve_effects_on(
                the_system.comp["FmA1"], {"nope": False}, "probe_key", "Failure"
            )

    def test_declared_effects_read_back(self, the_system):
        # The engine is handed empty dicts (a flow pattern is not one of its
        # variable names); the declared ones are what the attribute exposes.
        assert the_system.comp["FmAX__fmcc"].failure_effects == {"f.*": False}
        assert the_system.comp["FmMix__fmwild"].failure_effects == {
            ".*": FM_ENGINE_DERATED
        }


class TestFmEngineDeratings:
    """A continuous output is reached through a derating the AUTOMATON owns."""

    def test_wildcard_reaches_both_families(self, the_system):
        mix = the_system.comp["FmMix"]
        # The discrete side keeps its availability gate...
        assert mix.flows_out["alive"].var_fed_available is not None
        # ...and the continuous side got a derating variable of its own.
        assert "FmMix__fmwild__fmwild" in mix.flows_out["q"].derating

    def test_derating_variable_is_keyed_per_automaton(self, the_system):
        mix = the_system.comp["FmMix"]
        derating_vars = mix.derating_vars_of("FmMix__fmwild__fmwild")
        assert list(derating_vars) == ["FmMix__fmwild__fmwild_derating_q"]

    def test_cc_mode_does_not_derate_a_discrete_only_target(self, the_system):
        assert (
            the_system.comp["FmA1"].derating_vars_of("FmAX__fmcc__fmcc__bin_11") == {}
        )


class TestFmEngineDictCondition:
    """``failure_cond={flow: value}`` still reads the target's INPUT flows."""

    def test_gate_follows_the_target_feed(self, the_system):
        the_system.isimu_start()
        fireable = {
            (tr.comp_name, tr.name) for tr in the_system.isimu_fireable_transitions()
        }
        # FmGated is fed with c1, so its guard holds.
        assert ("FmGated__fmgate", "occ") in fireable
        # FmStarved is not, so it is not fireable at all.
        assert ("FmStarved__fmgate", "occ") not in fireable


class TestFmEngineRefusedKeywords:
    """The cod3s keywords the muscadet resolution cannot honour are refused."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"behaviour": "external"},
            {"failure_effects_trans": {"f1_fed_available_out": False}},
            {"repair_effects_trans": {"f1_fed_available_out": True}},
        ],
    )
    def test_refused(self, the_system, kwargs):
        with pytest.raises(ValueError, match="not supported by the muscadet"):
            ObjFailureModeExp(
                fm_name="fmrefused",
                targets=["FmA1"],
                failure_effects={"f1": False},
                **kwargs,
            )


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
