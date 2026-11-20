"""A noisy Monte Carlo study, run as a script, for the multi-rank checks.

Not a test module: ``tests/test_profile_noise_001.py`` launches it under
``mpirun``, because a PyCATSHOO process holds one MPI session and one system,
so a multi-rank run cannot happen inside the pytest process.

Usage::

    python profile_noise_mpi_study.py <seed> <output.json>

Rank 0 writes the indicator values (mean and standard deviation of the
published profile factor of two components, at every sample instant) to
``output.json``; the other ranks exit inside ``simulate``.
"""

import json
import sys

import muscadet

#: Sample instants of the study.
SCHEDULE = [float(t) for t in range(25)]

#: Replicas simulated, spread over the ranks.
NB_RUNS = 16


class NzMpiPlant(muscadet.ObjFlow):
    """A noisy sinusoidal source."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(
            name="X",
            var_fed_default=1.0,
            profile=muscadet.SinusoidalProfile(
                amplitude=0.5,
                period=12.0,
                offset=1.0,
                noise_amplitude=0.3,
                noise_time_scale=1.5,
            ),
        )


class NzMpiSink(muscadet.ObjFlow):
    """A consumer asking for more than is ever produced."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_in(name="X", var_demand_default=100.0)


def main(seed, output):
    system = muscadet.System(name="NzMpi")
    for name in ("A", "B"):
        system.add_component(name=name, cls="NzMpiPlant")
        system.add_component(name=f"{name}_SINK", cls="NzMpiSink")
        system.connect_flow(source=name, target=f"{name}_SINK", flow_name="X")
        system.add_indicator_var(
            component=name, var="^X_out_profile$", stats=["mean", "stddev"]
        )

    system.simulate({"nb_runs": NB_RUNS, "schedule": SCHEDULE, "seed": seed})

    result = {
        name: [
            [row["stat"], float(row["instant"]), float(row["values"])]
            for _, row in indic.values.iterrows()
        ]
        for name, indic in system.indicators.items()
    }
    with open(output, "w") as stream:
        json.dump(result, stream)


if __name__ == "__main__":
    main(int(sys.argv[1]), sys.argv[2])
