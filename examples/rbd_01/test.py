from system import my_rbd
import pytest


def test_simulation_results():
    # Get the indicator results
    indicators = my_rbd.indicators

    # Check if the mean value of the indicator is as expected
    assert (
        indicators["T"]["is_ok_fed_in"]["mean"] == 1.0
    ), "The mean value of the indicator is not as expected."


if __name__ == "__main__":
    pytest.main()
