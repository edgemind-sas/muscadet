"""Connection type checking: a continuous flow and a discrete flow never wire
together, regardless of direction or connection path (``connect_flow``,
``auto_connect``). ``connect_trigger`` is unaffected since it always pairs a
discrete trigger source with a discrete triggered output.
"""

import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():
    """One system hosting every connection-type-check scenario.

    Each scenario uses its own dedicated pair of component names so the
    mismatched-type connections that are expected to raise never touch the
    ones that are expected to succeed.
    """

    class DiscOut(muscadet.ObjFlow):
        """A plain discrete output flow named ``x``."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out(name="x", var_prod_default=True)

    class DiscIn(muscadet.ObjFlow):
        """A plain discrete input flow named ``x``."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="x", logic="or")

    class ContOut(muscadet.ObjFlow):
        """A plain continuous output flow named ``x``."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_out(name="x", var_fed_default=1.0)

    class ContIn(muscadet.ObjFlow):
        """A plain continuous input flow named ``x``."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_in(name="x")

    class MixedSource(muscadet.ObjFlow):
        """Carries a discrete out, a continuous out, and a third output
        (``bad``) that is discrete here but continuous on ``MixedTarget``."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out(name="d", var_prod_default=True)
            self.add_flow_continuous_out(name="c", var_fed_default=2.0)
            self.add_flow_out(name="bad", var_prod_default=True)

    class MixedTarget(muscadet.ObjFlow):
        """Mirrors ``MixedSource`` except ``bad``, declared continuous here."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="d", logic="or")
            self.add_flow_continuous_in(name="c")
            self.add_flow_continuous_in(name="bad")

    class PickyOut(muscadet.ObjFlow):
        """A discrete output authorizing only components labelled ``OK``."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out(
                name="p",
                var_prod_default=True,
                component_authorized=[{"label": "OK"}],
            )

    class TrigSource(muscadet.ObjFlow):
        """Plain discrete output feeding a trigger, as in the existing
        ``FlowOutOnTrigger`` connection pattern."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out(name="trig", var_prod_default=True)

    class TrigTarget(muscadet.ObjFlow):
        """Discrete output activated by the incoming trigger."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out_on_trigger(
                name="trig",
                trigger_time_up=0,
                trigger_time_down=0,
                trigger_logic="and",
                var_prod_default=True,
            )

    system = muscadet.System(name="SysConnectTypeCheck")

    # -- AE19: continuous output -> same-named discrete input
    system.add_component(name="CONT_OUT", cls="ContOut")
    system.add_component(name="DISC_IN", cls="DiscIn")

    # -- Reverse direction: discrete output -> same-named continuous input
    system.add_component(name="DISC_OUT", cls="DiscOut")
    system.add_component(name="CONT_IN", cls="ContIn")

    # -- Discrete-to-discrete, unaffected
    system.add_component(name="DISC_OUT2", cls="DiscOut")
    system.add_component(name="DISC_IN2", cls="DiscIn")

    # -- Continuous-to-continuous, unaffected
    system.add_component(name="CONT_OUT2", cls="ContOut")
    system.add_component(name="CONT_IN2", cls="ContIn")

    # -- auto_connect over components carrying both families
    system.add_component(name="MIXA", cls="MixedSource")
    system.add_component(name="MIXB", cls="MixedTarget")

    # -- connect_trigger, unaffected
    system.add_component(name="TRIG_SRC", cls="TrigSource")
    system.add_component(name="TRIG_TGT", cls="TrigTarget")

    # -- A denied connection whose target carries no matching input flow
    system.add_component(name="PICKY", cls="PickyOut")
    system.add_component(name="NOPE", cls="DiscIn")

    # -- The type check on the route that skips the authorization check
    system.add_component(name="CONT_OUT3", cls="ContOut")
    system.add_component(name="DISC_IN3", cls="DiscIn")

    return system


def test_continuous_out_to_discrete_in_raises(the_system):
    """Covers AE19: a continuous output connected to a same-named discrete
    input raises, and the message names both components and the flow."""

    with pytest.raises(ValueError) as exc_info:
        the_system.connect_flow("CONT_OUT", "DISC_IN", "x")

    message = str(exc_info.value)
    assert "CONT_OUT" in message
    assert "DISC_IN" in message
    assert "x" in message

    assert not the_system.comp["CONT_OUT"].is_connected_to("DISC_IN", "x")


def test_discrete_out_to_continuous_in_raises(the_system):
    """The reverse direction raises the same way."""

    with pytest.raises(ValueError) as exc_info:
        the_system.connect_flow("DISC_OUT", "CONT_IN", "x")

    message = str(exc_info.value)
    assert "DISC_OUT" in message
    assert "CONT_IN" in message
    assert "x" in message

    assert not the_system.comp["DISC_OUT"].is_connected_to("CONT_IN", "x")


def test_discrete_to_discrete_unaffected(the_system):
    """Discrete-to-discrete connections are unaffected."""

    connection = the_system.connect_flow("DISC_OUT2", "DISC_IN2", "x")

    assert connection == {"source": "DISC_OUT2", "flow": "x", "target": "DISC_IN2"}
    assert the_system.comp["DISC_OUT2"].is_connected_to("DISC_IN2", "x")


def test_continuous_to_continuous_unaffected(the_system):
    """Continuous-to-continuous connections are unaffected."""

    connection = the_system.connect_flow("CONT_OUT2", "CONT_IN2", "x")

    assert connection == {"source": "CONT_OUT2", "flow": "x", "target": "CONT_IN2"}
    assert the_system.comp["CONT_OUT2"].is_connected_to("CONT_IN2", "x")


def test_auto_connect_connects_matches_and_raises_on_mismatch(the_system):
    """``auto_connect`` over two components that each carry both families
    connects only the matching pairs and raises on the mismatched one."""

    with pytest.raises(ValueError) as exc_info:
        the_system.auto_connect("MIXA", "MIXB")

    message = str(exc_info.value)
    assert "MIXA" in message
    assert "MIXB" in message
    assert "bad" in message

    # The matching pairs, declared before "bad", were connected first.
    assert the_system.comp["MIXA"].is_connected_to("MIXB", "d")
    assert the_system.comp["MIXA"].is_connected_to("MIXB", "c")
    # The mismatched pair never got wired.
    assert not the_system.comp["MIXA"].is_connected_to("MIXB", "bad")


def test_connect_trigger_still_works(the_system):
    """``connect_trigger`` is unaffected by the type check."""

    the_system.connect_trigger("TRIG_SRC", "TRIG_TGT", "trig")

    assert the_system.comp["TRIG_SRC"].is_connected_to("TRIG_TGT", "trig")


def test_an_unauthorized_connection_returns_none_rather_than_raising(the_system):
    """The 1.x contract: a connection the source denies returns None.

    ``PICKY`` authorizes only components labelled ``OK``, and ``NOPE`` carries
    no input flow named ``p`` at all. 1.x refused the connection on the
    authorization pattern and returned None without ever looking the target
    flow up; resolving that flow eagerly, above the early return, turns a
    refusal into a KeyError and breaks every model that relies on
    ``component_authorized`` to filter a broad set of candidate targets.
    """
    assert "p" not in the_system.comp["NOPE"].flows_in

    result = the_system.connect_flow(source="PICKY", target="NOPE", flow_name="p")

    assert result is None
    assert not the_system.comp["PICKY"].is_connected_to("NOPE", "p")


def test_the_type_check_runs_even_without_the_authorization_check(the_system):
    """A mismatch is a model error on EVERY route into ``connect_flow``.

    ``check_authorization=False`` is what ``connect_trigger`` passes, and a raw
    ``System.connect()`` bypasses the flow layer entirely; neither makes a
    continuous-to-discrete connection any less wrong. Gating the type check on
    the authorization flag let the mismatch through on exactly those routes.
    """
    with pytest.raises(ValueError, match="continuous and discrete"):
        the_system.connect_flow(
            source="CONT_OUT3",
            target="DISC_IN3",
            flow_name="x",
            check_authorization=False,
        )

    assert not the_system.comp["CONT_OUT3"].is_connected_to("DISC_IN3", "x")


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
