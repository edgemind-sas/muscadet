"""Tests for the DeprecationWarning on muscadet.ObjFailureMode*.

These wrappers are kept for backward compatibility but the modern
canonical implementation lives in cod3s lib (``cod3s.ObjFM`` /
``cod3s.ObjFMExp`` / ``cod3s.ObjFMDelay``). New code should use the
cod3s classes directly.
"""

from __future__ import annotations

import warnings

import cod3s
import pytest

# Importing muscadet pulls PyCATSHOO; skip the whole module when it's
# unavailable in the test venv.
pyc_available = True
try:
    import muscadet  # noqa: F401
    from muscadet.obj import ObjFailureMode, ObjFailureModeExp, ObjFailureModeDelay  # noqa: F401
except Exception:
    pyc_available = False

pytestmark = pytest.mark.skipif(
    not pyc_available, reason="PyCATSHOO not available in test venv"
)


@pytest.fixture(scope="module")
def _shared_system():
    """Single muscadet System shared across all FM-deprecation tests.

    PyCATSHOO refuses to instantiate a second top-level System within
    the same process ("Interdit de construire plus d'un système"), so
    we share one instance across tests. FM construction may still
    raise downstream errors (missing targets, etc.) — we don't care,
    only the DeprecationWarning is asserted.

    Yields the system and tears it down on module exit so downstream
    test modules see a clean PyCATSHOO state.
    """
    try:
        import muscadet

        system = muscadet.System(name="dep_test_shared")
    except Exception as e:
        pytest.skip(f"muscadet/PyCATSHOO unavailable: {e}")
        return
    yield system
    try:
        system.deleteSys()
    except Exception:
        pass
    cod3s.terminate_session()


def _instantiate_and_capture_warnings(fm_class, **kwargs):
    """Try to instantiate ``fm_class``, capture any DeprecationWarning.

    Returns the list of captured DeprecationWarnings even when the
    constructor raises later (the warning fires at the start of __init__).
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            fm_class(**kwargs)
        except Exception:
            # Downstream errors (missing PyCATSHOO context, target
            # resolution failure, ...) are irrelevant to this test.
            pass
    return [w for w in caught if issubclass(w.category, DeprecationWarning)]


class TestObjFailureModeDeprecation:
    def test_deprecation_warning_on_exp(self, _shared_system):
        from muscadet.obj import ObjFailureModeExp

        dep = _instantiate_and_capture_warnings(
            ObjFailureModeExp,
            fm_name="m_exp_test",
            targets=["A"],
            failure_param=[1.0],
            repair_param=[1.0],
        )
        assert any("ObjFailureModeExp" in str(w.message) for w in dep), (
            f"Expected DeprecationWarning mentioning ObjFailureModeExp, "
            f"got: {[str(w.message) for w in dep]}"
        )

    def test_warning_points_to_cod3s_replacement(self, _shared_system):
        from muscadet.obj import ObjFailureModeExp

        dep = _instantiate_and_capture_warnings(
            ObjFailureModeExp, fm_name="m_replace_test", targets=["A"]
        )
        assert any("cod3s.ObjFMExp" in str(w.message) for w in dep), (
            f"Warning should point to cod3s.ObjFMExp; "
            f"got: {[str(w.message) for w in dep]}"
        )

    def test_delay_subclass_also_warned(self, _shared_system):
        from muscadet.obj import ObjFailureModeDelay

        dep = _instantiate_and_capture_warnings(
            ObjFailureModeDelay, fm_name="m_delay_test", targets=["A"]
        )
        assert any(
            "ObjFailureModeDelay" in str(w.message) for w in dep
        ), f"Expected ObjFailureModeDelay deprecation warning; got: {[str(w.message) for w in dep]}"
