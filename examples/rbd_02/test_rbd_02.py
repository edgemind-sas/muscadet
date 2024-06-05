import pytest


def test():

    my_rbd = __import__("rbd_02").my_rbd

    assert all(my_rbd.indicators["S_is_ok_fed_out"].values["values"] == 1)
    B1_val = my_rbd.indicators["B1_is_ok_fed_out"].values["values"]
    assert B1_val.index[B1_val.diff() != 0].to_list() == [
        0,
        167,
        250,
        417,
        500,
        667,
        750,
        916,
    ]
    B2_val = my_rbd.indicators["B2_is_ok_fed_out"].values["values"]
    assert B2_val.index[B2_val.diff() != 0].to_list() == [0, 334, 458, 791, 916]
    T_val = my_rbd.indicators["T_is_ok_fed_in"].values["values"]
    assert T_val.index[T_val.diff() != 0].to_list() == [
        0,
        167,
        250,
        334,
        500,
        667,
        750,
        791,
    ]

    my_rbd.deleteSys()
