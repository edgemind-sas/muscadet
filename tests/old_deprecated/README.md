# Deprecated tests — not collected by pytest

Tests in this directory cover muscadet features that have been
**superseded or abandoned**. They are kept around for reference only,
not as a regression gate. ``pytest.ini`` lists this directory under
``norecursedirs`` so the regular ``pytest`` command skips it.

## Contents

- ``test_cod3s_wrapper_001.py`` / ``test_cod3s_wrapper_002.py`` —
  exercised the ``muscadet.cod3s_wrapper`` Pydantic-based KB integration
  (``KBMuscadet``, ``ObjFlowClass``, ``ObjFlowInstance``,
  ``SystemMuscadet``). The COD3S Platform now ships its own KB
  representation and the canonical entry point for importing models is
  :mod:`muscadet.importers.cod3s_platform`. The wrapper module remains
  in the source tree for backwards-compatible imports but is no longer
  the recommended path.

If you're adding new tests, target the importer plugin namespace
under ``muscadet/importers/`` instead.
