import muscadet
import cod3s
import pytest
import kb_project


@pytest.fixture(scope="module")
def the_system():
    system = muscadet.System(name="Sys")

    # Create coin toss component
    comp = system.add_component(name="S", cls="Start")

    # # Create automaton for coin toss
    # automaton = PycAutomaton(
    #     name="aut_ok_nok",
    #     states=["ok", "nok"],
    #     init_state="ok",
    #     transitions=[
    #         {
    #             "name": "ok_nok",
    #             "source": "ok",
    #             "target": "nok",
    #             "is_interruptible": False,
    #             "occ_law": {"cls": "exp", "rate": 1 / 5},
    #         },
    #     ],
    # )

    # # Add automaton to coin_comp
    # automaton.update_bkd(comp)

    # # Add indicator for current state
    # system.add_indicator(
    #     component="C",
    #     attr_type="ST",
    #     attr_name="nok",
    #     stats=["mean", "P25", "P75"],
    #     measure="sojourn-time",
    # )

    # system.add_indicator(
    #     name_pattern="{component}_st_{attr_name}_sj_stdev",
    #     component="C",
    #     attr_type="ST",
    #     attr_name="nok",
    #     stats=["stddev"],
    #     measure="sojourn-time",
    # )

    return system


def test_system(the_system):
    pass
    # Run simulation
    # schedule = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    # simu_params = cod3s.PycMCSimulationParam(
    #     nb_runs=10000, schedule=schedule, seed=56000
    # )
    # the_system.simulate(simu_params)

    # # Check results
    # assert "C_st_nok_sj_stdev" in the_system.indicators.keys()

    # assert "C_nok_sojourn-time" in the_system.indicators.keys()
    # ind_val = the_system.indicators["C_nok_sojourn-time"]

    # # Check that we have results for all scheduled times
    # assert ind_val.instants == schedule
    # assert ind_val.values["values"].to_list() == [
    #     0.092775359749794,
    #     0.3478492796421051,
    #     0.7367357015609741,
    #     1.2380776405334473,
    #     1.830515742301941,
    #     2.499711513519287,
    #     3.22802472114563,
    #     4.002827167510986,
    #     4.817336082458496,
    #     5.664624214172363,
    #     0.0,
    #     0.0,
    #     0.0,
    #     0.0,
    #     0.0,
    #     0.0,
    #     0.0682295486330986,
    #     1.0682295560836792,
    #     2.0682296752929688,
    #     3.0682296752929688,
    #     0.0,
    #     0.5303405523300171,
    #     1.530340552330017,
    #     2.5303406715393066,
    #     3.5303406715393066,
    #     4.530340671539307,
    #     5.530340671539307,
    #     6.530340671539307,
    #     7.530340671539307,
    #     8.530340194702148,
    # ]
    # # for mean_value in results["values"]:
    # #     assert ind_even_val["values"] == [
    # #         0.45 <= mean_value <= 0.55
    # #     ), f"Mean value {mean_value} is not close to 0.5"


def test_delete(the_system):
    the_system.deleteSys()
    cod3s.terminate_session()
