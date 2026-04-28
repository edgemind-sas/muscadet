"""Validate that auto_connect(available_connect=True) honors the
``component_authorized`` patterns declared on the canonical flow,
i.e. uses ``flows_out["f1"]`` / ``flows_in["f1"]`` for the auth
check (not the suffixed name ``"f1_available"``).
"""

import muscadet
import cod3s
import pytest


@pytest.fixture(scope="module")
def the_system():

    class CompA(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="f1",
                    component_authorized=[{"class_name_bkd": "CompB"}],
                )
            )

    class CompB(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="f1"))

    class CompC(muscadet.ObjFlow):
        def add_flows(self, **kwargs):
            super().add_flows(**kwargs)
            self.add_flow(dict(cls="FlowIn", name="f1"))

    system = muscadet.System(name="Sys")
    system.add_component(name="CA", cls="CompA")
    system.add_component(name="CB", cls="CompB")
    system.add_component(name="CC", cls="CompC")

    return system


def test_value_channel_respects_authorization(the_system):
    """CA->CB is authorized, CA->CC is not (component_authorized=CompB only)."""
    conns = the_system.auto_connect("CA", ".*")
    assert conns == [{"source": "CA", "flow": "f1", "target": "CB"}]


def test_available_channel_respects_authorization(the_system):
    """Same authorization rules apply on the availability channel."""
    conns = the_system.auto_connect("CA", ".*", available_connect=True)
    assert conns == [{"source": "CA", "flow": "f1_available", "target": "CB"}]


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
