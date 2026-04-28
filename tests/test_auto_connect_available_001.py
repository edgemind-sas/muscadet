"""Validate that auto_connect(available_connect=True) wires the
``var_fed_available`` reference of FlowIn to the corresponding
``var_fed_available`` variable of upstream FlowOut.

Regression test for: previous code raised ``KeyError: 'f1_available'``
because the authorization check looked up ``flows_out["f1_available"]``
which is not a real key (the key is ``"f1"``).
"""

import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():

    class Source(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_out(name="f1", var_prod_default=True)

    class Target(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow_in(name="f1", logic="and")

    system = muscadet.System(name="Sys")
    system.add_component(name="S1", cls="Source")
    system.add_component(name="S2", cls="Source")
    system.add_component(name="T", cls="Target")

    # Wire the value channel
    conn_value = system.auto_connect("S.*", "T")
    # Wire the availability channel — this used to raise KeyError
    conn_avail = system.auto_connect("S.*", "T", available_connect=True)

    system.conn_value = conn_value
    system.conn_avail = conn_avail
    return system


def test_value_channel_connected(the_system):
    """auto_connect (default) returned the expected connections on f1."""
    assert the_system.conn_value == [
        {"source": "S1", "flow": "f1", "target": "T"},
        {"source": "S2", "flow": "f1", "target": "T"},
    ]


def test_available_channel_connected(the_system):
    """auto_connect(available_connect=True) returns connections on f1_available."""
    assert the_system.conn_avail == [
        {"source": "S1", "flow": "f1_available", "target": "T"},
        {"source": "S2", "flow": "f1_available", "target": "T"},
    ]


def test_references_have_three_connections(the_system):
    """Both ``var_in`` and ``var_fed_available`` references are wired to 2 sources."""
    flow_in = the_system.comp["T"].flows_in["f1"]
    assert flow_in.var_in.nbCnx() == 2
    assert flow_in.var_fed_available.nbCnx() == 2


def test_simulation_runs(the_system):
    """Sanity: simulation starts and feeds T via both channels."""
    the_system.isimu_start()
    flow_in = the_system.comp["T"].flows_in["f1"]
    assert flow_in.var_fed.value() is True
    the_system.isimu_stop()


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
