"""ObjLogicGate — combinational logic gates (OR / AND / k-of-n) over source
observable variables, automaton-free, exported to downstream FlowIns.

Static behaviour: heterogeneous source flow names, OR / AND / k-of-n, and
broadcast (one gate feeding several targets). Dynamic recompute (a source
toggled by a failure mid-simulation) is covered in
``test_logic_gate_dynamic.py``.
"""

import muscadet
import cod3s
import pytest


class SA(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="fa", var_prod_default=True)  # ON


class SB(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="fb", var_prod_default=False)  # OFF


class SC(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="fc", var_prod_default=True)  # ON


class Sink(muscadet.ObjFlow):
    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="g", logic="or")


def _leaf(obj, flow):
    return {"obj": obj, "attr": f"{flow}_fed_out", "value": True}


def _units(*pairs):
    """One unit clause per source — ``kind`` alone selects the aggregation."""
    return [[_leaf(obj, flow)] for (obj, flow) in pairs]


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="Sys")
    system.add_component(name="SA", cls="SA")
    system.add_component(name="SB", cls="SB")
    system.add_component(name="SC", cls="SC")

    sinks = [
        "SinkOr",
        "SinkAndTF",
        "SinkAndTT",
        "SinkK2",
        "SinkK3",
        "SinkBC1",
        "SinkBC2",
    ]
    for s in sinks:
        system.add_component(name=s, cls="Sink")

    # OR(fa=ON, fb=OFF) = True ; heterogeneous flow names fa/fb.
    system.add_component(
        name="GOR", cls="ObjLogicGate", kind="or",
        cond=_units(("SA", "fa"), ("SB", "fb")), out_elements=["g"],
    )
    # AND(fa=ON, fb=OFF) = False.
    system.add_component(
        name="GAND_TF", cls="ObjLogicGate", kind="and",
        cond=_units(("SA", "fa"), ("SB", "fb")), out_elements=["g"],
    )
    # AND(fa=ON, fc=ON) = True.
    system.add_component(
        name="GAND_TT", cls="ObjLogicGate", kind="and",
        cond=_units(("SA", "fa"), ("SC", "fc")), out_elements=["g"],
    )
    # k=2 of (fa=ON, fb=OFF, fc=ON) -> 2 fed -> True.
    system.add_component(
        name="GK2", cls="ObjLogicGate", kind="k", k=2,
        cond=_units(("SA", "fa"), ("SB", "fb"), ("SC", "fc")), out_elements=["g"],
    )
    # k=3 of the same -> only 2 fed -> False.
    system.add_component(
        name="GK3", cls="ObjLogicGate", kind="k", k=3,
        cond=_units(("SA", "fa"), ("SB", "fb"), ("SC", "fc")), out_elements=["g"],
    )
    # Broadcast: one gate result to two targets.
    system.add_component(
        name="GBC", cls="ObjLogicGate", kind="or",
        cond=_units(("SA", "fa")), out_elements=["g"],
    )

    pairs = [
        ("GOR", "SinkOr"),
        ("GAND_TF", "SinkAndTF"),
        ("GAND_TT", "SinkAndTT"),
        ("GK2", "SinkK2"),
        ("GK3", "SinkK3"),
        ("GBC", "SinkBC1"),
        ("GBC", "SinkBC2"),
    ]
    for gate, sink in pairs:
        system.connect(gate, "g_out", sink, "g_in")
    return system


def _fed(system, sink):
    return system.comp[sink].flows_in["g"].var_fed.value()


def test_or_true(the_system):
    the_system.isimu_start()
    try:
        assert _fed(the_system, "SinkOr") is True
    finally:
        the_system.isimu_stop()


def test_and_false_when_one_off(the_system):
    the_system.isimu_start()
    try:
        assert _fed(the_system, "SinkAndTF") is False
    finally:
        the_system.isimu_stop()


def test_and_true_when_all_on(the_system):
    the_system.isimu_start()
    try:
        assert _fed(the_system, "SinkAndTT") is True
    finally:
        the_system.isimu_stop()


def test_kofn(the_system):
    the_system.isimu_start()
    try:
        assert _fed(the_system, "SinkK2") is True   # 2 of 3 fed, k=2
        assert _fed(the_system, "SinkK3") is False  # 2 of 3 fed, k=3
    finally:
        the_system.isimu_stop()


def test_broadcast_to_two_targets(the_system):
    the_system.isimu_start()
    try:
        assert _fed(the_system, "SinkBC1") is True
        assert _fed(the_system, "SinkBC2") is True
    finally:
        the_system.isimu_stop()


def test_invalid_kind_rejected():
    with pytest.raises(ValueError):
        muscadet.ObjLogicGate._resolve_logic("xor", None)


def test_k_requires_positive_int():
    with pytest.raises(ValueError):
        muscadet.ObjLogicGate._resolve_logic("k", 0)


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
