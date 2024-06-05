import pytest
from muscadet import System
from examples.rbd_01.system import my_rbd

def test_system_initialization():
    assert isinstance(my_rbd, System)
    assert my_rbd.name() == "My first RBD"

def test_components_added():
    components = my_rbd.comp.keys()
    assert "S" in components
    assert "B1" in components
    assert "B2" in components
    assert "T" in components

def test_connections():
    assert my_rbd.comp["S"].is_connected_to("B1", "is_ok")
    assert my_rbd.comp["S"].is_connected_to("B2", "is_ok")
    assert my_rbd.comp["B1"].is_connected_to("T", "is_ok")
    assert my_rbd.comp["B2"].is_connected_to("T", "is_ok")

def test_indicators():
    indicators = my_rbd.indicators.keys()
    assert "T_is_ok_fed_in_mean" in indicators

def test_simulation():
    my_rbd.simulate(
        {
            "nb_runs": 1,
            "schedule": [{"start": 0, "end": 24, "nvalues": 23}],
        }
    )
    results = my_rbd.get_results()
    assert results is not None
    assert "T_is_ok_fed_in_mean" in results
