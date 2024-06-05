from system import my_rbd
import pytest

def test():
    assert all(my_rbd.indicators["T_is_ok_fed_in"].values["values"] == 1)
