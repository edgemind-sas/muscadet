import muscadet
import pytest

def test_simulation_results():
    # System init
    my_rbd = muscadet.System(name="My first RBD")

    # Add components
    my_rbd.add_component(cls="Source", name="S")
    my_rbd.add_component(cls="Block", name="B1")
    my_rbd.add_component(cls="Block", name="B2")
    my_rbd.add_component(cls="Target", name="T")

    # Connect components
    my_rbd.connect("S", "is_ok_out", "B1", "is_ok_in")
    my_rbd.connect("S", "is_ok_out", "B2", "is_ok_in")
    my_rbd.connect("B1", "is_ok_out", "T", "is_ok_in")
    my_rbd.connect("B2", "is_ok_out", "T", "is_ok_in")

    # Add indicators
    my_rbd.add_indicator_var(
        component="T",
        var="is_ok_fed_in",
        stats=["mean", "stddev"],
    )

    # System simulation
    my_rbd.simulate(
        {
            "nb_runs": 1,
            "schedule": [{"start": 0, "end": 24, "nvalues": 23}],
        }
    )

    # Get the indicator results
    indicators = my_rbd.indicators

    # Check if the mean value of the indicator is as expected
    assert indicators["T"]["is_ok_fed_in"]["mean"] == 1.0, "The mean value of the indicator is not as expected."

    # Check if the standard deviation of the indicator is as expected
    assert indicators["T"]["is_ok_fed_in"]["stddev"] == 0.0, "The standard deviation of the indicator is not as expected."

if __name__ == "__main__":
    pytest.main()
