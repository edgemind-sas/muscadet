def add_flow_delay(
    obj,
    name,
    flow_name="is_ok",
    failure_time=0,
    failure_param_name="ttf",
    repair_cond=True,
    repair_time=0,
    repair_effects=[],
    repair_param_name="ttr",
):
    obj.add_delay_failure_mode(
        name=name,
        failure_cond=flow_name + "_fed_out",
        failure_time=failure_time,
        failure_effects=[(flow_name + "_fed_available_out", False)],
        repair_cond=repair_cond,
        repair_time=repair_time,
        repair_effects=repair_effects,
        repair_param_name=repair_param_name,
    )


def show_all_indicators_of_component(
    sys,
    comp,
    var=".*",
    nb_run=1,
    start_time=0,
    end_time=24,
    nb_values=1000,
):
    if isinstance(comp, str):
        comp_name = comp
    else:
        comp_name = comp.basename()

    sys.add_indicator_var(
        component=comp_name,
        var=".*",
        stats=["mean"],
    )

    # System simulation
    # =================
    sys.simulate(
        {
            "nb_runs": nb_run,
            "schedule": [{"start": start_time, "end": end_time, "nvalues": nb_values}],
        }
    )

    fig_indics = sys.indic_px_line(
        markers=False, title=comp_name + " flow monitoring in the RBD", facet_row="name"
    )

    # Display graphic in browser
    fig_indics.show()


def show_all_indicators_of_system(
    sys,
    var=".*",
    comp=".*",
    nb_run=1,
    start_time=0,
    end_time=24,
    nb_values=1000,
):
    if isinstance(comp, str):
        comp_name = comp
    else:
        comp_name = comp.basename()
    sys.add_indicator_var(
        component=comp_name,
        var=var,
        stats=["mean"],
    )

    # System simulation
    # =================
    sys.simulate(
        {
            "nb_runs": nb_run,
            "schedule": [{"start": start_time, "end": end_time, "nvalues": nb_values}],
        }
    )

    fig_indics = sys.indic_px_line(
        markers=False, title=var + " flow monitoring in the RBD", facet_row="name"
    )

    # Display graphic in browser
    fig_indics.show()
