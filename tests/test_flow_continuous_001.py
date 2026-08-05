import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():
    """Continuous flow primitives: real values travel between components."""

    class CSource(muscadet.ObjFlow):
        """Continuous producer declared through the dedicated method."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_out(
                name="q", var_fed_default=kwargs.get("rate", 2.5)
            )

    class CSourceDict(muscadet.ObjFlow):
        """Same producer, declared through the dict form."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(
                dict(
                    cls="FlowContinuousOut",
                    name="q",
                    var_fed_default=kwargs.get("rate", 2.5),
                )
            )

    class CSink(muscadet.ObjFlow):
        """Continuous consumer declared through the dedicated method."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_continuous_in(
                name="q",
                var_in_default=kwargs.get("in_default", 0.0),
                var_demand_default=kwargs.get("demand", 0.0),
            )

    class CSinkDict(muscadet.ObjFlow):
        """Same consumer, declared through the dict form."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(
                dict(
                    cls="FlowContinuousIn",
                    name="q",
                    var_in_default=kwargs.get("in_default", 0.0),
                    var_demand_default=kwargs.get("demand", 0.0),
                )
            )

    class CMixed(muscadet.ObjFlow):
        """A component mixing a discrete and a continuous flow."""

        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="b", logic="and")
            self.add_flow_out(name="b", var_prod_cond=["b"])
            self.add_flow_continuous_in(name="q")
            self.add_flow_continuous_out(name="q", var_fed_default=1.0)

    system = muscadet.System(name="Sys")

    # -- Scenario: a non-integer real round-trips between two components
    system.add_component(name="SRC", cls="CSource", rate=2.5)
    system.add_component(name="SNK", cls="CSink", demand=4.5)
    system.auto_connect("SRC", "SNK")

    # -- Scenario: two producers feeding one continuous input deliver the sum
    system.add_component(name="PA", cls="CSource", rate=2.5)
    system.add_component(name="PB", cls="CSource", rate=0.75)
    system.add_component(name="SUM", cls="CSink", demand=1.5)
    system.auto_connect("PA|PB", "SUM")

    # -- Scenario: the dict form is equivalent to the method form
    system.add_component(name="SRCD", cls="CSourceDict", rate=2.5)
    system.add_component(name="SNKD", cls="CSinkDict", demand=4.5)
    system.auto_connect("SRCD", "SNKD")

    # -- Scenario: an unconnected output holds its declared default
    system.add_component(name="HOLD", cls="CSource", rate=3.75)

    # -- Scenario: an unconnected input reads its declared default
    system.add_component(name="LONE", cls="CSink", in_default=1.25)

    # -- Discrete and continuous flows coexisting on one component
    system.add_component(name="MIX", cls="CMixed")

    return system


def test_value_round_trip(the_system):
    """A non-integer real travels from a continuous out to a continuous in."""
    the_system.isimu_start()

    src = the_system.comp["SRC"].flows_out["q"]
    snk = the_system.comp["SNK"].flows_in["q"]

    assert src.var_fed.value() == pytest.approx(2.5)
    assert snk.var_fed.value() == pytest.approx(2.5)

    the_system.isimu_stop()


def test_several_producers_sum(the_system):
    """Two producers feeding one continuous input deliver their sum."""
    the_system.isimu_start()

    assert the_system.comp["PA"].flows_out["q"].var_fed.value() == pytest.approx(2.5)
    assert the_system.comp["PB"].flows_out["q"].var_fed.value() == pytest.approx(0.75)
    assert the_system.comp["SUM"].flows_in["q"].var_fed.value() == pytest.approx(3.25)

    the_system.isimu_stop()


def test_dict_form_equivalent_to_method_form(the_system):
    """A continuous flow declared as a dict behaves like the method form."""
    the_system.isimu_start()

    src_m = the_system.comp["SRC"].flows_out["q"]
    src_d = the_system.comp["SRCD"].flows_out["q"]
    snk_m = the_system.comp["SNK"].flows_in["q"]
    snk_d = the_system.comp["SNKD"].flows_in["q"]

    # Same runtime classes
    assert type(src_d) is type(src_m) is muscadet.FlowContinuousOut
    assert type(snk_d) is type(snk_m) is muscadet.FlowContinuousIn

    # Same declared variables
    assert src_d.var_fed.basename() == src_m.var_fed.basename()
    assert src_d.var_demand.basename() == src_m.var_demand.basename()
    assert snk_d.var_fed.basename() == snk_m.var_fed.basename()
    assert snk_d.var_in.basename() == snk_m.var_in.basename()
    assert snk_d.var_demand.basename() == snk_m.var_demand.basename()

    # Same runtime values
    assert src_d.var_fed.value() == pytest.approx(src_m.var_fed.value())
    assert snk_d.var_fed.value() == pytest.approx(snk_m.var_fed.value())
    assert snk_d.get_var_demand_value() == pytest.approx(snk_m.get_var_demand_value())

    the_system.isimu_stop()


def test_output_with_no_rule_holds_its_default(the_system):
    """A continuous output with no rule and no capacity holds its default."""
    the_system.isimu_start()

    hold = the_system.comp["HOLD"].flows_out["q"]
    assert hold.var_fed.value() == pytest.approx(3.75)

    the_system.isimu_stop()


def test_unconnected_input_reads_its_default(the_system):
    """An unconnected continuous input reads its default, it does not raise."""
    the_system.isimu_start()

    lone = the_system.comp["LONE"].flows_in["q"]
    assert lone.var_in.nbCnx() == 0
    assert lone.var_fed.value() == pytest.approx(1.25)

    the_system.isimu_stop()


def test_demand_channel_is_wired_upstream(the_system):
    """The reverse-direction demand channel carries a real value upstream."""
    the_system.isimu_start()

    src = the_system.comp["SRC"].flows_out["q"]
    snk = the_system.comp["SNK"].flows_in["q"]

    # The consumer publishes its demand, the producer reads it back
    assert snk.get_var_demand_value() == pytest.approx(4.5)
    assert src.var_demand.nbCnx() == 1
    assert src.get_var_demand_value() == pytest.approx(4.5)

    # Several consumers would aggregate by sum; here two producers each see the
    # single demand published by SUM
    assert the_system.comp["PA"].flows_out["q"].get_var_demand_value() == pytest.approx(
        1.5
    )
    assert the_system.comp["PB"].flows_out["q"].get_var_demand_value() == pytest.approx(
        1.5
    )

    # An unconnected producer reads its declared no-consumer default
    assert the_system.comp["HOLD"].flows_out["q"].var_demand.nbCnx() == 0
    assert the_system.comp["HOLD"].flows_out["q"].get_var_demand_value() == 0.0

    the_system.isimu_stop()


def test_variable_and_message_box_naming(the_system):
    """The declared names are the ones the connection machinery relies on."""
    src = the_system.comp["SRC"].flows_out["q"]
    snk = the_system.comp["SNK"].flows_in["q"]

    assert src.var_fed.basename() == "q_fed_out"
    assert src.var_demand.basename() == "q_demand_in"
    assert snk.var_fed.basename() == "q_fed_in"
    assert snk.var_in.basename() == "q_in"
    assert snk.var_demand.basename() == "q_demand_out"

    # One bidirectional message box per port, named as connect_flow expects
    assert the_system.comp["SRC"].messageBox("q_out") is not None
    assert the_system.comp["SNK"].messageBox("q_in") is not None
    assert the_system.comp["SRC"].is_connected_to("SNK", "q")


def test_continuous_flows_are_typed_float(the_system):
    """Continuous flows are real-valued, discrete ones stay boolean."""
    mix = the_system.comp["MIX"]

    assert mix.flows_in["q"].var_type == "float"
    assert mix.flows_out["q"].var_type == "float"
    assert mix.flows_in["b"].var_type == "bool"
    assert mix.flows_out["b"].var_type == "bool"


def test_continuous_flows_are_distinguishable(the_system):
    """Continuous flows live in flows_in/flows_out and stay identifiable."""
    mix = the_system.comp["MIX"]

    # Stored alongside the discrete flows, so auto_connect finds them by name
    assert set(mix.flows_in) == {"b", "q"}
    assert set(mix.flows_out) == {"b", "q"}

    # ... and enumerable on their own
    assert set(mix.flows_continuous_in) == {"q"}
    assert set(mix.flows_continuous_out) == {"q"}

    assert isinstance(mix.flows_in["q"], muscadet.FlowContinuous)
    assert isinstance(mix.flows_out["q"], muscadet.FlowContinuous)
    assert not isinstance(mix.flows_in["b"], muscadet.FlowContinuous)
    assert not isinstance(mix.flows_out["b"], muscadet.FlowContinuous)

    # The two families are parallel: neither derives from the other
    assert not isinstance(mix.flows_in["q"], muscadet.FlowDiscreteIn)
    assert not isinstance(mix.flows_out["q"], muscadet.FlowDiscreteOut)
    assert not isinstance(mix.flows_in["b"], muscadet.FlowContinuousIn)
    assert not isinstance(mix.flows_out["b"], muscadet.FlowContinuousOut)


def test_duplicate_continuous_flow_raises(the_system):
    """Redeclaring an existing flow name is rejected, both API shapes."""
    mix = the_system.comp["MIX"]

    with pytest.raises(ValueError, match="Input flow q already exists"):
        mix.add_flow_continuous_in(name="q")

    with pytest.raises(ValueError, match="Output flow q already exists"):
        mix.add_flow(dict(cls="FlowContinuousOut", name="q"))

    # The rejected declarations left the component untouched
    assert set(mix.flows_continuous_in) == {"q"}
    assert set(mix.flows_continuous_out) == {"q"}


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
