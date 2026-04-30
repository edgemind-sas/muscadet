import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():
    """Test FlowOut negate: output is inverted."""

    class Source(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out(name="f1", var_prod_default=True)

    class Inverter(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="f1", logic="and")
            self.add_flow_out(
                name="f1",
                var_prod_cond=["f1"],
                negate=True,
            )

    class Target(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="f1", logic="and")

    system = muscadet.System(name="Sys")
    system.add_component(name="S", cls="Source")
    system.add_component(name="INV", cls="Inverter")
    system.add_component(name="T", cls="Target")

    system.auto_connect("S", "INV")
    system.auto_connect("INV", "T")

    return system


def test_negate_inverts_output(the_system):
    """Source produces True, inverter negates it -> Target gets False."""
    the_system.isimu_start()

    # Source is producing
    assert the_system.comp["S"].flows_out["f1"].var_fed.value() is True
    # Inverter input is fed
    assert the_system.comp["INV"].flows_in["f1"].var_fed.value() is True
    # Inverter output is negated -> False
    assert the_system.comp["INV"].flows_out["f1"].var_fed.value() is False
    # Target receives the negated value
    assert the_system.comp["T"].flows_in["f1"].var_fed.value() is False

    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
