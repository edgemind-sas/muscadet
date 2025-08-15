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
                    cls="FlowIn",
                    name="c1",
                )
            )
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="c1",
                )
            )
            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="c2",
                    component_authorized=[],
                )
            )

    class CompB(muscadet.ObjFlow):

        def add_flows(self, **kwargs):

            super().add_flows(**kwargs)

            self.add_flow(
                dict(
                    cls="FlowIn",
                    name="c1",
                )
            )

            self.add_flow(
                dict(
                    cls="FlowIn",
                    name="c2",
                )
            )

            self.add_flow(
                dict(
                    cls="FlowIn",
                    name="c3",
                )
            )

            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="c1",
                )
            )

            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="c4",
                )
            )

    class CompC(muscadet.ObjFlow):

        def add_flows(self, **kwargs):

            super().add_flows(**kwargs)

            self.add_flow(
                dict(
                    cls="FlowIn",
                    name="c2",
                )
            )

            self.add_flow(
                dict(
                    cls="FlowIn",
                    name="c3",
                )
            )

            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="c4",
                )
            )

            self.add_flow(
                dict(
                    cls="FlowOut",
                    name="c1",
                )
            )

    system = muscadet.System(name="Sys")

    system.add_component(name="CA", cls="CompA")
    system.add_component(name="CB", cls="CompB")
    system.add_component(name="CC", cls="CompC")

    return system


def test_system(the_system):

    connections = the_system.auto_connect(".*", ".*")

    assert connections == [
        {"source": "CA", "flow": "c1", "target": "CB"},
        {"source": "CB", "flow": "c1", "target": "CA"},
        {"source": "CC", "flow": "c1", "target": "CA"},
        {"source": "CC", "flow": "c1", "target": "CB"},
    ]

    # assert the_system.comp[cname].flows_out[fname].var_fed.value() is True


def test_delete(the_system):

    the_system.deleteSys()
    cod3s.terminate_session()
