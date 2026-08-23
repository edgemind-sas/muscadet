"""Round trips a spec has to survive, and the shapes that used to break them.

``muscadet.declare`` exists so a model can arrive as data: a COD3S Platform
export, a YAML knowledge base, a generated study. What that asks of
``component_spec`` / ``build_component`` is not merely that they run, but that
what comes back declares the SAME component -- a spec that rebuilds something
subtly different is worse than one that refuses, because nothing points at the
difference.

Each test below pins one shape that came back different, or not at all.
"""

import Pycatshoo as Pyc
import cod3s
import pytest
from cod3s.pycatshoo.automaton import DelayOccDistribution

import muscadet
from muscadet.declare import ComponentSpecError
from muscadet.kb.continuous import SourceSinusoidalContinuous  # noqa: F401

PROFILE_SAMPLE_TIMES = (0.0, 3.0, 6.0, 12.0, 18.0)
TEMPO_DELAY = 7.0
EXP_RATE = 0.1


class RtDiscrete(muscadet.ObjFlow):
    """One discrete output, enough to hang an automaton on."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="f", var_prod_default=True)


class RtTempo(muscadet.ObjFlow):
    """A temporised output whose law is given as an OBJECT, not a mapping.

    The mapping spelling ``{"cls": "delay", "time": 7}`` stays a plain dict on
    the flow and always round-tripped; only the object spelling went through
    pydantic's union serialisation, which is what dropped its parameters.
    """

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out_tempo(
            name="b",
            var_prod_default=True,
            occ_enable_flow=DelayOccDistribution(time=TEMPO_DELAY),
        )


# ---------------------------------------------------------------------------
# A time profile, in both the hand-written and the read-back direction
# ---------------------------------------------------------------------------
def test_a_profile_mapping_builds_a_flow():
    """``{"cls": "SinusoidalProfile", ...}`` is documented as working wherever
    the object does. ``add_flow`` runs the whole flow mapping through
    ``ObjCOD3S.from_dict``, which resolves every nested ``cls`` against the
    ObjCOD3S registry -- and a Profile is not an ObjCOD3S.
    """
    system = muscadet.System(name="DclRtProfileMap")
    try:
        comp = muscadet.build_component(
            system,
            {
                "name": "C",
                "flows": [
                    {
                        "cls": "FlowContinuousOut",
                        "name": "q",
                        "var_fed_default": 5.0,
                        "profile": {
                            "cls": "SinusoidalProfile",
                            "amplitude": 3.0,
                            "period": 24.0,
                        },
                    }
                ],
            },
        )

        profile = comp.flows_out["q"].profile
        assert type(profile).__name__ == "SinusoidalProfile"
        assert profile.amplitude == pytest.approx(3.0)
        assert profile.period == pytest.approx(24.0)
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_a_profiled_output_survives_a_round_trip():
    """One of the six shipped continuous components had no round trip at all."""
    system = muscadet.System(name="DclRtProfile")
    try:
        comp = muscadet.build_component(
            system,
            {
                "name": "SIN",
                "cls": "SourceSinusoidalContinuous",
                "params": {
                    "flow": "q",
                    "amplitude": 3.0,
                    "period": 24.0,
                    "offset": 5.0,
                },
            },
        )
        rebuilt = muscadet.build_component(
            system, dict(muscadet.component_spec(comp), name="SIN2")
        )

        # Same curve, not merely the same class: the parameters are what a
        # spec has to carry.
        original = comp.flows_out["q"].profile
        copy = rebuilt.flows_out["q"].profile
        assert [original.factor(t) for t in PROFILE_SAMPLE_TIMES] == [
            copy.factor(t) for t in PROFILE_SAMPLE_TIMES
        ]
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ---------------------------------------------------------------------------
# An occurrence law declared as an object
# ---------------------------------------------------------------------------
def test_a_tempo_law_keeps_its_parameters():
    """Pydantic serialises a union-typed field through its DECLARED member.

    ``occ_enable_flow`` is ``Union[dict, OccurrenceDistributionModel]`` and the
    base carries no fields, so a ``DelayOccDistribution(time=7)`` came out as
    ``{"cls": "DelayOccDistribution"}`` and rebuilt at ``time=0``: a seven-unit
    temporisation became instantaneous, silently.
    """
    system = muscadet.System(name="DclRtTempo")
    try:
        system.add_component(name="C", cls="RtTempo")
        spec = muscadet.component_spec(system.comp["C"])

        assert spec["flows"][0]["occ_enable_flow"]["time"] == pytest.approx(TEMPO_DELAY)

        rebuilt = muscadet.build_component(system, dict(spec, name="C2"))
        assert rebuilt.flows_out["b"].occ_enable_flow.time == pytest.approx(TEMPO_DELAY)
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ---------------------------------------------------------------------------
# An occurrence law holding an engine handle
# ---------------------------------------------------------------------------
def test_an_automaton_whose_rate_is_a_variable_rebuilds():
    """A law may hold the PyCATSHOO variable its rate lives in.

    That is what ``add_exp_failure_mode`` itself writes, so an indicator can
    reference the rate by name. ``copy.deepcopy`` on such a declaration raises
    ``Pickling of "Pycatshoo.IVariable" instances is not enabled`` -- which is
    why ``common.copy_declaration`` exists, and why this path must use it.
    """
    system = muscadet.System(name="DclRtVarLaw")
    try:
        system.add_component(name="C", cls="RtDiscrete")
        comp = system.comp["C"]
        rate = comp.addVariable("lambda_x", Pyc.TVarType.t_double, EXP_RATE)
        comp.add_atm2states(
            name="m",
            occ_law_12={"cls": "exp", "rate": rate},
            effects_12=[("f", False)],
        )

        rebuilt = muscadet.build_component(
            system, dict(muscadet.component_spec(comp), name="C2")
        )

        assert "C2_m" in rebuilt.automata_d
    finally:
        system.deleteSys()
        cod3s.terminate_session()


# ---------------------------------------------------------------------------
# A duplicate instance name
# ---------------------------------------------------------------------------
def test_a_duplicate_name_is_refused_by_name():
    """``add_component`` warns and returns None on a name already held.

    Every line after it dereferenced that None, so the caller got
    ``'NoneType' object has no attribute 'metadata'``: a traceback naming
    neither the spec nor the name that collided.
    """
    system = muscadet.System(name="DclRtDuplicate")
    try:
        spec = {
            "name": "C",
            "flows": [{"cls": "FlowOut", "name": "f", "var_prod_default": True}],
        }
        muscadet.build_component(system, spec)

        with pytest.raises(ComponentSpecError) as error:
            muscadet.build_component(system, spec)

        assert "C" in str(error.value)
        assert "already holds" in str(error.value)
    finally:
        system.deleteSys()
        cod3s.terminate_session()


def test_delete():
    """Each test deletes its own system; this closes the session."""
    cod3s.terminate_session()
