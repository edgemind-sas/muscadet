import pytest


def test():
    my_rbd = __import__("rbd_01").my_rbd
    assert all(my_rbd.indicators["T_is_ok_fed_in"].values["values"] == 1)

    my_rbd.deleteSys()
