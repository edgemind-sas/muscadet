"""Building a declaration at 100, 500, 1 000 and 10 000 components.

The pure road has one budget: what the live road costs on the same package.
Each tier builds one run package -- a chain of units, a failure mode on one
unit in ten, an indicator on every unit -- by both roads, checks the two
documents are the same, and fails if declaring from data took longer than
materialising the system and reading it back.

Measured 2026-11-26 on the export alone, same machine, one process per tier:

========  ==========  ==========================
units     pure road   live road (build + read)
========  ==========  ==========================
100       0.005 s     0.09 s
500       0.023 s     0.46 s
1 000     0.044 s     0.95 s
10 000    0.58 s      13.8 s
========  ==========  ==========================

Both are linear in the number of components. The live road is timed through
the importer's own ``apply_to_system``, without the double reading the suite
wraps around it, so the budget is the live road's and nothing else's.
"""

import time

import pytest

from muscadet import package_declaration
from muscadet.importers import cod3s_platform as importer
from tests import double_reading
from tests.package_models import chain_export, indicator, mode, study

#: The last tier costs the LIVE road a minute and a half with its study, so it
#: runs on request (``--runslow``), like every other slow test of the suite.
TIERS = (100, 500, 1_000, pytest.param(10_000, marks=pytest.mark.slow))


def package(units):
    names = [f"U{index:04d}" for index in range(1, units + 1)]
    return chain_export(units, name=f"Scaling{units}"), study(
        failure_modes=[
            mode(f"loss_{name}", [name], [1e-4], [0.1], behaviour="external")
            for name in names[::10]
        ],
        indicators=[
            indicator(f"^{name}$", "^sig_fed_out$", name=f"{name}_fed")
            for name in names
        ],
    )


@pytest.mark.parametrize("units", TIERS)
def test_the_pure_road_stays_within_the_budget_of_the_live_one(units, monkeypatch):
    export, a_study = package(units)
    monkeypatch.setattr(
        importer, "apply_to_system", double_reading.ORIGINAL["apply_to_system"]
    )

    started = time.perf_counter()
    pure = package_declaration.package_declaration(export, a_study)
    pure_seconds = time.perf_counter() - started

    started = time.perf_counter()
    live, added = double_reading.live_package_declaration(export, a_study)
    live_seconds = time.perf_counter() - started

    assert added == len(a_study["failure_modes"])
    assert double_reading.differences(double_reading.canonical(pure), live) == []
    assert pure_seconds <= live_seconds, (
        f"{units} components: the pure road took {pure_seconds:.3f} s, over the "
        f"{live_seconds:.3f} s of the live road it replaces"
    )


def test_the_pure_road_is_linear_in_the_number_of_components():
    """Ten times the components, not a hundred times the work."""
    timings = {}
    for units in (1_000, 10_000):
        export, a_study = package(units)
        started = time.perf_counter()
        package_declaration.package_declaration(export, a_study)
        timings[units] = time.perf_counter() - started

    assert timings[10_000] <= 30 * timings[1_000]
