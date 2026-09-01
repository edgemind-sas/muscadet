"""Three instances of one controller class trip at three different levels.

What this unit pins down
------------------------
A controller class declares the levels its outputs switch at. Two instances of
that class routinely want two different ones -- three pumps of one model, three
starting levels -- and until now that tuning had NOWHERE to go: the bridge built
an ``ObjCtrl`` from the class template alone, so an instance tuned away from its
class simulated at the class value with nothing raised anywhere. A reliability
figure that is wrong and looks right, which is the worst failure a safety tool
has.

The channel is the one the capacity numbers already ride on: the model
component's ``attributes`` list. What is new is the ADDRESS. Every threshold
shares a single role, ``controller_threshold``, and its identity -- the output,
the position in the emission grammar, the edge -- travels in the attribute NAME.
That is the platform's choice, made because a role per threshold would have
capped in silence the number of thresholds the grammar leaves free.

The four things that had to be proved
-------------------------------------
* the tuning reaches the ENGINE and not merely the structure. Two instances of
  one class, driven by one rising level, are watched switching at their own
  dates; a tuning that stopped at the parse layer would have them switch
  together at the class level;
* a threshold NESTED inside a combination is reachable and tunable, the grammar
  being recursive and the address being a chain of operand indices;
* an untuned instance keeps following its class, and the inherit sentinel
  (``value: None``) is the same thing as no entry at all;
* everything unreachable is REFUSED and named: an unknown output, an operand
  chain that leads nowhere, an edge the operator does not carry, a value that is
  not a real number, and two thresholds projecting onto one name.

The montage
-----------
One tank starts empty and is filled at 1 per unit time, so its level reads ``t``
and every date below is also a level. Three pumps of one class read it. Their
class starts at 5; ``PUMP_LOW`` is tuned to 2, ``PUMP_HIGH`` to 8, and
``PUMP_CLASS`` is not tuned at all. Beside that, a second output combines an
unreachable comparison with an unreachable band, and ``PUMP_LOW`` alone tunes
that band's activation edge down to 3 -- the nested case, at a date of its own so
it cannot be confused with either of the others.

Requires PyCATSHOO native libraries for the montage (the refusals are pure).
"""

import pytest

#: Every KB class of this montage is prefixed with the module's own name, so a
#: class of one test file can never be read as a class of another.
PREFIX = "CtrlThresholdOverride"

TANK_CLASS = f"{PREFIX}Tank"
FILLER_CLASS = f"{PREFIX}Filler"
PUMP_CLASS_NAME = f"{PREFIX}Pump"

#: The tank starts empty and is filled at one per unit time, so its level reads
#: ``t`` and every threshold below is also the date it is crossed at.
FILL_RATE = 1.0

#: What the CLASS declares its pumps start at.
CLASS_START = 5.0

#: What the two tuned instances start at instead.
LOW_START = 2.0
HIGH_START = 8.0

#: The nested band's class levels, put past the horizon so the class alone
#: never fires it: what fires is the tuning, and nothing else.
UNREACHABLE = 100.0
CLASS_RELEASE = 1.0

#: What ``PUMP_LOW`` tunes that nested band to.
LOW_ALARM_ACTIVATE = 3.0
LOW_ALARM_RELEASE = 0.5

#: How far either side of a date the trajectory is read. The response is pinned
#: to that bracket, which needs nothing of how the solver lands on a crossing it
#: root-finds.
BRACKET = 0.1

#: Tolerance on a reading compared to the number the montage puts there.
READING_TOL = 0.01


# ---------------------------------------------------------------------------
# Payload builders (canonical {model, kb} shape)
# ---------------------------------------------------------------------------


def _tank_template() -> dict:
    """A volume, observable as a LEVEL under the capacity name ``level``."""
    return {
        "name": TANK_CLASS,
        "interfaces": {
            "q__input": {
                "name": "q",
                "port_type": {"general": "input"},
                "flow_family": "continuous",
            }
        },
        "capacities": [
            {
                "name": "level",
                "flow": "q",
                "volume": 1000.0,
                "content_init": {"q": 0.0},
                "fill_rate": "inf",
            }
        ],
    }


def _filler_template() -> dict:
    return {
        "name": FILLER_CLASS,
        "interfaces": {
            "q__output": {
                "name": "q",
                "port_type": {"general": "output"},
                "flow_family": "continuous",
                "production_profile": {"cls": "constant", "value": FILL_RATE},
            }
        },
    }


def _pump_template() -> dict:
    """The controller class: one level read, two outputs, three thresholds.

    ``run`` carries its threshold on the ROOT operator, ``alarm`` carries two of
    them one level down, inside a combination. Between them they cover both
    addresses a threshold can have.
    """
    return {
        "name": PUMP_CLASS_NAME,
        "metadata": {"controller": True},
        "controls_in": [{"name": "level", "kind": "level"}],
        "controls_out": [
            {
                "name": "run",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "level",
                    "operator": ">=",
                    "threshold": CLASS_START,
                },
            },
            {
                "name": "alarm",
                "kind": "bool",
                "emit": {
                    "op": "combine",
                    "logic": "or",
                    "operands": [
                        {
                            "op": "compare",
                            "input": "level",
                            "operator": ">=",
                            "threshold": UNREACHABLE,
                        },
                        {
                            "op": "band",
                            "input": "level",
                            "direction": "above",
                            "activate": UNREACHABLE,
                            "release": CLASS_RELEASE,
                        },
                    ],
                },
            },
        ],
    }


def _threshold_attr(name: str, value) -> dict:
    return {"name": name, "role": "controller_threshold", "value": value}


def _connection(source_id, source_iface, target_id, target_iface) -> dict:
    return {
        "component_source": source_id,
        "interface_source": source_iface,
        "component_target": target_id,
        "interface_target": target_iface,
    }


def build_payload() -> dict:
    """The montage, as the platform would export it once thresholds resolve."""
    components = {
        "id-fil": {"name": "FILL", "class_name": FILLER_CLASS, "attributes": []},
        "id-tank": {"name": "TANK", "class_name": TANK_CLASS, "attributes": []},
        "id-low": {
            "name": "PUMP_LOW",
            "class_name": PUMP_CLASS_NAME,
            "attributes": [
                _threshold_attr("run__threshold", LOW_START),
                _threshold_attr("alarm__op1__activate", LOW_ALARM_ACTIVATE),
                _threshold_attr("alarm__op1__release", LOW_ALARM_RELEASE),
            ],
        },
        "id-high": {
            "name": "PUMP_HIGH",
            "class_name": PUMP_CLASS_NAME,
            "attributes": [_threshold_attr("run__threshold", HIGH_START)],
        },
        "id-cls": {
            "name": "PUMP_CLASS",
            "class_name": PUMP_CLASS_NAME,
            # The inherit sentinel: an attribute that EXISTS and says "take the
            # class's". Read as no entry at all, on both other override sides.
            "attributes": [_threshold_attr("run__threshold", None)],
        },
    }
    connections = {
        "c-fill": _connection("id-fil", "q", "id-tank", "q"),
        "c-obs-low": _connection("id-tank", "level", "id-low", "level"),
        "c-obs-high": _connection("id-tank", "level", "id-high", "level"),
        "c-obs-cls": _connection("id-tank", "level", "id-cls", "level"),
    }

    return {
        "model": {
            "name": "CtrlThresholdOverrideModel",
            "kb": {"name": "CtrlThresholdKB", "version": "0.0.1"},
            "elements": {"components": components, "connections": connections},
        },
        "kb": {
            "name": "CtrlThresholdKB",
            "version": "0.0.1",
            "component_templates": {
                TANK_CLASS: _tank_template(),
                FILLER_CLASS: _filler_template(),
                PUMP_CLASS_NAME: _pump_template(),
            },
            "interface_templates": {},
        },
    }


def _parse(payload):
    from muscadet.importers.cod3s_platform import parse_platform_export

    return parse_platform_export(payload)


def _refused(payload, match):
    from muscadet.importers.cod3s_platform import Cod3sPlatformImportError

    with pytest.raises(Cod3sPlatformImportError, match=match):
        _parse(payload)


def _controller(ctx, name):
    return next(comp for comp in ctx.components if comp.name == name).controller


def _emit(ctx, comp_name, output_name):
    controller = _controller(ctx, comp_name)
    return controller.output_named(output_name).emit


# ===========================================================================
# 1. The capability marker the platform probes before translating
# ===========================================================================


class TestCapabilityMarker:
    def test_marker_is_declared_true_and_readable_from_outside(self):
        # Read with getattr(module, marker, False) by the platform's translator,
        # which refuses to translate a tuned model when it answers False. What
        # the refusal prevents is a controller tripping at the class level in a
        # study that ran to completion and reported a figure.
        from muscadet.importers import cod3s_platform

        assert (
            getattr(cod3s_platform, "_SUPPORTS_CONTROLLER_THRESHOLD_OVERRIDE", False)
            is True
        )


# ===========================================================================
# 2. The address of a threshold
# ===========================================================================


class TestTheProjectedName:
    def test_a_root_threshold_is_the_output_and_the_edge(self):
        from muscadet.importers.cod3s_platform import (
            controller_threshold_attribute_name,
        )

        assert controller_threshold_attribute_name("run", (), "threshold") == (
            "run__threshold"
        )

    def test_a_nested_edge_carries_its_whole_operand_chain(self):
        from muscadet.importers.cod3s_platform import (
            controller_threshold_attribute_name,
        )

        assert controller_threshold_attribute_name("alarm", (1,), "activate") == (
            "alarm__op1__activate"
        )
        assert controller_threshold_attribute_name("alarm", (1, 0), "release") == (
            "alarm__op1__op0__release"
        )

    def test_a_name_past_the_budget_is_cut_on_a_separator_and_digested(self):
        """The mirror of the platform's truncation, which is the only reason a
        long output name still resolves rather than being refused as unknown."""
        from muscadet.importers.cod3s_platform import (
            CONTROLLER_THRESHOLD_NAME_MAX_LENGTH,
            controller_threshold_attribute_name,
        )

        long_output = "x" * 200
        name = controller_threshold_attribute_name(long_output, (3,), "threshold")

        assert len(name) == CONTROLLER_THRESHOLD_NAME_MAX_LENGTH
        # The cut never leaves half a separator behind.
        assert not name.startswith("_") and "___" not in name
        # Deterministic, so the two ends land on the same string.
        assert name == controller_threshold_attribute_name(
            long_output, (3,), "threshold"
        )
        # And two identities that share the truncated prefix stay apart.
        assert name != controller_threshold_attribute_name(
            long_output, (4,), "threshold"
        )


class TestTheEdgesRestatedHereAreTheGrammarsOwn:
    """The restatement is what makes an unreachable override a PARSE-time
    refusal; this is what stops it drifting from the grammar it restates."""

    def test_every_restated_edge_is_a_parameter_the_grammar_declares(self):
        from muscadet import obj_ctrl
        from muscadet.importers.cod3s_platform import CONTROLLER_THRESHOLD_EDGES

        restated = {
            edge for edges in CONTROLLER_THRESHOLD_EDGES.values() for edge in edges
        }
        assert restated == set(obj_ctrl.CTRL_PARAMS)

    def test_the_operators_carrying_them_are_the_grammars_own(self):
        from muscadet import obj_ctrl
        from muscadet.importers.cod3s_platform import (
            CONTROLLER_THRESHOLD_EDGES,
            _CONTROLLER_COMBINE_OP,
        )

        assert CONTROLLER_THRESHOLD_EDGES[obj_ctrl.CTRL_OP_COMPARE] == (
            obj_ctrl.CTRL_PARAM_THRESHOLD,
        )
        assert CONTROLLER_THRESHOLD_EDGES[obj_ctrl.CTRL_OP_BAND] == (
            obj_ctrl.CTRL_PARAM_ACTIVATE,
            obj_ctrl.CTRL_PARAM_RELEASE,
        )
        assert _CONTROLLER_COMBINE_OP == obj_ctrl.CTRL_OP_COMBINE

    def test_a_republished_gain_is_deliberately_not_a_threshold(self):
        """It scales a publication, it decides nothing, and it already has an
        endpoint of its own (``{output}_level_gain``)."""
        from muscadet import obj_ctrl
        from muscadet.importers.cod3s_platform import CONTROLLER_THRESHOLD_EDGES

        assert obj_ctrl.CTRL_OP_REPUBLISH not in CONTROLLER_THRESHOLD_EDGES


# ===========================================================================
# 3. The pure parse layer folds the tuning into the instance's declaration
# ===========================================================================


class TestTheParseLayerFoldsTheTuning:
    def test_the_tuned_instance_carries_its_own_root_threshold(self):
        ctx = _parse(build_payload())

        assert _emit(ctx, "PUMP_LOW", "run")["threshold"] == LOW_START
        assert _emit(ctx, "PUMP_HIGH", "run")["threshold"] == HIGH_START

    def test_an_untuned_instance_keeps_the_class_value(self):
        """And the inherit sentinel is the same thing as no entry at all."""
        ctx = _parse(build_payload())

        assert _emit(ctx, "PUMP_CLASS", "run")["threshold"] == CLASS_START

    def test_a_nested_edge_is_reached_through_its_operand_chain(self):
        ctx = _parse(build_payload())

        band = _emit(ctx, "PUMP_LOW", "alarm")["operands"][1]
        assert band["activate"] == LOW_ALARM_ACTIVATE
        assert band["release"] == LOW_ALARM_RELEASE
        # The sibling operand, which nothing tuned, is untouched.
        assert _emit(ctx, "PUMP_LOW", "alarm")["operands"][0]["threshold"] == (
            UNREACHABLE
        )

    def test_the_class_declaration_is_not_mutated_by_one_instance(self):
        """Three components share one class template. A fold that edited it in
        place would give the first instance's levels to the other two."""
        ctx = _parse(build_payload())

        assert _emit(ctx, "PUMP_HIGH", "alarm")["operands"][1]["activate"] == (
            UNREACHABLE
        )
        assert _emit(ctx, "PUMP_CLASS", "alarm")["operands"][1]["activate"] == (
            UNREACHABLE
        )

    def test_the_tuning_is_preserved_on_the_spec_for_a_later_reader(self):
        ctx = _parse(build_payload())
        low = next(comp for comp in ctx.components if comp.name == "PUMP_LOW")

        assert low.metadata["controller_threshold_overrides"] == {
            ("run__threshold", "controller_threshold"): LOW_START,
            ("alarm__op1__activate", "controller_threshold"): LOW_ALARM_ACTIVATE,
            ("alarm__op1__release", "controller_threshold"): LOW_ALARM_RELEASE,
        }
        # An untuned instance carries an empty index, the sentinel dropped.
        untuned = next(comp for comp in ctx.components if comp.name == "PUMP_CLASS")
        assert untuned.metadata["controller_threshold_overrides"] == {}


# ===========================================================================
# 4. Everything unreachable is refused, and named
# ===========================================================================


def _tune(payload, name, value):
    """Put one override on ``PUMP_LOW`` and return the payload."""
    attrs = payload["model"]["elements"]["components"]["id-low"]["attributes"]
    attrs.append(_threshold_attr(name, value))
    return payload


class TestAnUnreachableOverrideIsRefused:
    def test_an_override_naming_an_unknown_output(self):
        _refused(
            _tune(build_payload(), "nope__threshold", 1.0),
            "which its class does not declare",
        )

    def test_an_operand_chain_that_leads_nowhere(self):
        """``run`` is a comparison, so it has no operand 0 to descend into."""
        _refused(
            _tune(build_payload(), "run__op0__threshold", 1.0),
            "which its class does not declare",
        )

    def test_an_edge_the_operator_does_not_carry(self):
        """A comparison has a threshold, not an activation edge."""
        _refused(
            _tune(build_payload(), "run__activate", 1.0),
            "which its class does not declare",
        )

    def test_the_refusal_names_what_the_class_does_declare(self):
        """A reader told only that the name is wrong cannot act."""
        from muscadet.importers.cod3s_platform import Cod3sPlatformImportError

        with pytest.raises(Cod3sPlatformImportError) as caught:
            _parse(_tune(build_payload(), "run__activate", 1.0))

        message = str(caught.value)
        assert "'run__activate'" in message
        for declared in (
            "run__threshold",
            "alarm__op0__threshold",
            "alarm__op1__activate",
            "alarm__op1__release",
        ):
            assert declared in message

    def test_a_value_that_is_not_a_real_number(self):
        _refused(
            _tune(build_payload(), "run__threshold", "high"),
            "must be a real number",
        )

    def test_a_boolean_value_is_refused_rather_than_coerced(self):
        """``True`` would become a perfectly plausible level of 1.0."""
        _refused(
            _tune(build_payload(), "run__threshold", True),
            "must be a real number, got the boolean",
        )

    def test_an_infinite_value(self):
        _refused(
            _tune(build_payload(), "run__threshold", float("inf")),
            "must be a finite real number",
        )


class TestTwoThresholdsProjectingOntoOneName:
    """The projection is not injective and cannot be made so: the separator is
    an ordinary character run. So injectivity is VERIFIED over the set that
    exists, and the refusal names BOTH sources rather than the name they landed
    on -- keeping the first would leave the second untunable in silence."""

    @staticmethod
    def _colliding_payload():
        payload = build_payload()
        outputs = payload["kb"]["component_templates"][PUMP_CLASS_NAME]["controls_out"]
        # An output literally named ``alarm__op0`` produces, for its own root
        # threshold, the name ``alarm``'s first operand produces.
        outputs.append(
            {
                "name": "alarm__op0",
                "kind": "bool",
                "emit": {
                    "op": "compare",
                    "input": "level",
                    "operator": ">=",
                    "threshold": UNREACHABLE,
                },
            }
        )
        return payload

    def test_a_collision_is_refused_naming_both_thresholds(self):
        from muscadet.importers.cod3s_platform import Cod3sPlatformImportError

        with pytest.raises(Cod3sPlatformImportError) as caught:
            _parse(self._colliding_payload())

        message = str(caught.value)
        assert "'alarm__op0__threshold'" in message
        assert "output 'alarm'.operands[0] edge 'threshold'" in message
        assert "output 'alarm__op0' edge 'threshold'" in message

    def test_a_class_nobody_tunes_still_builds(self):
        """The collision costs addressability and nothing else, so it is
        refused where it bites: on a component someone tuned."""
        payload = self._colliding_payload()
        for cid in ("id-low", "id-high", "id-cls"):
            payload["model"]["elements"]["components"][cid]["attributes"] = []

        ctx = _parse(payload)
        assert _emit(ctx, "PUMP_LOW", "run")["threshold"] == CLASS_START


# ===========================================================================
# 5. What the ENGINE runs at
# ===========================================================================
#
# The only test that proves anything. Everything above verifies the plumbing:
# that the number reaches the declaration. This one drives a session and reads
# the DATES three instances of one class switch at, which is the observable a
# tuning that stopped short of the engine could not produce.


@pytest.fixture(scope="module")
def montage():
    import cod3s
    from muscadet.importers.cod3s_platform import system_from_export

    system = system_from_export(build_payload())
    try:
        yield system
    finally:
        try:
            system.deleteSys()
        except Exception:
            pass
        cod3s.terminate_session()


def _signals(system, output_name):
    return {
        name: system.comp[name].controls_out[output_name].get_signal()
        for name in ("PUMP_LOW", "PUMP_CLASS", "PUMP_HIGH")
    }


class TestTheThresholdVariableTheEngineReads:
    def test_each_instance_created_its_own_variable_at_its_own_level(self, montage):
        """R44 makes a threshold a variable of the model. The tuning is what
        that variable is CREATED at, so PyCATSHOO takes it as the initial value
        and restores it between Monte Carlo sequences."""
        assert montage.comp["PUMP_LOW"].emit_params["run_threshold"].value() == (
            pytest.approx(LOW_START)
        )
        assert montage.comp["PUMP_HIGH"].emit_params["run_threshold"].value() == (
            pytest.approx(HIGH_START)
        )
        assert montage.comp["PUMP_CLASS"].emit_params["run_threshold"].value() == (
            pytest.approx(CLASS_START)
        )

    def test_the_nested_band_edges_are_the_tuned_ones(self, montage):
        """The engine names a threshold from its POSITION in the tree, which is
        a different spelling of the same identity the attribute name carries."""
        params = montage.comp["PUMP_LOW"].emit_params
        assert params["alarm_operand_1_activate"].value() == (
            pytest.approx(LOW_ALARM_ACTIVATE)
        )
        assert params["alarm_operand_1_release"].value() == (
            pytest.approx(LOW_ALARM_RELEASE)
        )
        assert montage.comp["PUMP_HIGH"].emit_params[
            "alarm_operand_1_activate"
        ].value() == pytest.approx(UNREACHABLE)


class TestTheInstancesSwitchAtTheirOwnDates:
    def test_one_rising_level_switches_three_pumps_at_three_dates(self, montage):
        """The measurement this whole unit exists for.

        The level reads ``t``, so each pump's date IS its threshold. Before the
        change all three would have switched together at 5, the class value,
        with the study running to completion either way.
        """
        montage.isimu_start()
        try:
            level = montage.comp["PUMP_LOW"].controls_in["level"]

            assert _signals(montage, "run") == {
                "PUMP_LOW": False,
                "PUMP_CLASS": False,
                "PUMP_HIGH": False,
            }

            montage.isimu_step_to(LOW_START - BRACKET)
            assert level.get_reading() == pytest.approx(
                LOW_START - BRACKET, abs=READING_TOL
            )
            assert _signals(montage, "run") == {
                "PUMP_LOW": False,
                "PUMP_CLASS": False,
                "PUMP_HIGH": False,
            }

            # 2 : the tuned-down instance alone.
            montage.isimu_step_to(LOW_START + BRACKET)
            assert _signals(montage, "run") == {
                "PUMP_LOW": True,
                "PUMP_CLASS": False,
                "PUMP_HIGH": False,
            }

            # 5 : the untuned instance joins it, at the class level.
            montage.isimu_step_to(CLASS_START + BRACKET)
            assert level.get_reading() == pytest.approx(
                CLASS_START + BRACKET, abs=READING_TOL
            )
            assert _signals(montage, "run") == {
                "PUMP_LOW": True,
                "PUMP_CLASS": True,
                "PUMP_HIGH": False,
            }

            # 8 : and the tuned-up one last.
            montage.isimu_step_to(HIGH_START + BRACKET)
            assert _signals(montage, "run") == {
                "PUMP_LOW": True,
                "PUMP_CLASS": True,
                "PUMP_HIGH": True,
            }
        finally:
            montage.isimu_stop()

    def test_a_nested_band_fires_on_the_instance_that_tuned_it_and_no_other(
        self, montage
    ):
        """At 3 -- a date of its own, so it cannot be read as either of the
        three above -- and only for the instance whose operand chain was tuned.
        """
        montage.isimu_start()
        try:
            montage.isimu_step_to(LOW_ALARM_ACTIVATE - BRACKET)
            assert _signals(montage, "alarm") == {
                "PUMP_LOW": False,
                "PUMP_CLASS": False,
                "PUMP_HIGH": False,
            }

            montage.isimu_step_to(LOW_ALARM_ACTIVATE + BRACKET)
            assert _signals(montage, "alarm") == {
                "PUMP_LOW": True,
                "PUMP_CLASS": False,
                "PUMP_HIGH": False,
            }

            # Past every date of the montage, the two untuned instances have
            # still never fired: their band sits past the horizon.
            montage.isimu_step_to(HIGH_START + BRACKET)
            assert _signals(montage, "alarm") == {
                "PUMP_LOW": True,
                "PUMP_CLASS": False,
                "PUMP_HIGH": False,
            }
        finally:
            montage.isimu_stop()

    def test_the_tuning_survives_on_the_built_component_for_a_later_reader(
        self, montage
    ):
        metadata = montage.comp["PUMP_LOW"].metadata

        assert metadata["controller"] is True
        assert metadata["class_name"] == PUMP_CLASS_NAME
        assert (
            metadata["controller_threshold_overrides"][
                ("run__threshold", "controller_threshold")
            ]
            == LOW_START
        )
        assert (
            montage.comp["PUMP_CLASS"].metadata["controller_threshold_overrides"] == {}
        )


def test_delete(montage):
    # Teardown is handled by the fixture; this keeps a stable last test.
    assert montage is not None
