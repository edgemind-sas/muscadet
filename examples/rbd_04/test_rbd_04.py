import pytest


def test():

    my_rbd = __import__("rbd_04").my_rbd

    S1_val = my_rbd.indicators["S1_is_ok_fed_out"].values["values"]
    assert S1_val.index[S1_val.diff() != 0].to_list() == [0, 250, 500, 750]
    S2_val = my_rbd.indicators["S2_is_ok_fed_out"].values["values"]
    assert S2_val.index[S2_val.diff() != 0].to_list() == [0, 292, 500, 791]
    T_val = my_rbd.indicators["T_is_ok_fed_in"].values["values"]
    assert T_val.index[T_val.diff() != 0].to_list() == [0, 250, 292, 750, 791]

    my_rbd.deleteSys()
