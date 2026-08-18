"""Test-suite infrastructure: give the engine back what a finished module held.

Why this exists
---------------
PyCATSHOO objects live in C++ and are released when the Python wrappers holding
them are collected. Every test module here builds one system and ends on a
``test_delete`` that calls ``deleteSys()`` and ``cod3s.terminate_session()`` --
but pytest keeps a module-scoped fixture's value, the frames of every
``pytest.raises`` and the reports of every test alive well past that point, and
those form reference CYCLES. A cycle is freed by the cyclic collector and by
nothing else, so the engine objects behind them survive until a collection
happens to run.

Left to chance, that is exactly what it sounds like. The suite was found to
pass at pytest's default verbosity and to collapse under ``-q`` -- same test
order, same tests, byte-identical up to the failing point -- because printing
one line per test rather than one dot allocates enough to trip the cyclic
collector often enough to stay under the engine's ceiling. Past that ceiling
the failure is not local: ``system.connect`` starts raising an empty
``PycException`` and every later module fails with it.

So the collection point is declared rather than left to the allocation pattern
of the terminal reporter. Once per MODULE, not once per test: a module is what
owns a system, and per-test collection costs about three times the suite's
runtime for no further benefit.
"""

import gc


def pytest_runtest_teardown(item, nextitem):
    """Collect when the module that owned a system is done with it."""
    if nextitem is None or nextitem.module is not item.module:
        gc.collect()
