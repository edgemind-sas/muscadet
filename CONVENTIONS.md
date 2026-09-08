# Coding Conventions

## General conventions
### Python Style
- Use 4 spaces for indentation
- Maximum line length of 88 characters (Black formatter standard)
- Use docstrings for all classes and methods
- Include type hints for function parameters and return values
- Use pydantic >= 2.7 style everytime it is possible to design classes
- Use snake_case for variables and function names
- Use CamelCase for class names

### Import Order
- Standard library imports first
- Third-party library imports second
- Local application imports last
- Within each group, imports should be alphabetized

### Documentation
- All public methods and classes should have docstrings
- Use triple quotes for docstrings
- Include parameter descriptions in docstrings
- Use english for code documentation and git repo commit

### Error Handling
- Use specific exception types rather than generic exceptions
- Include descriptive error messages

### Testing
- Use pytest for testing
- Test files should be named test_*.py
- Run the suite through the project environment (`uv sync` once, then
  `uv run pytest` or `.venv/bin/python -m pytest`). muscadet pins Python
  `>=3.10,<3.11` because PyCATSHOO ships native libraries built for it, and the
  root `conftest.py` refuses a run under any interpreter that cannot import
  PyCATSHOO and cod3s rather than letting every test module fail on the same
  import
- Test configuration lives in `pyproject.toml` alone. pytest reads the first
  inifile it finds and never merges two, so adding a `pytest.ini`,
  `tox.ini` or `setup.cfg` section beside it silently disables `testpaths`,
  the registered markers and `addopts`
- `pytest` with no argument is scoped to `tests/` by `testpaths`. The sibling
  tests under `examples/` are run on demand (`pytest examples/<name>`), in a
  process of their own: PyCATSHOO state is process-global, and an example
  collected alongside the suite changes the outcome of tests that pass without
  it

## Project specific coventions
### Flow Classes
- Flow classes should inherit from appropriate base classes
- Flow classes should implement all required methods
- Use consistent naming for flow variables (var_fed, var_prod, etc.)
- Discrete flow classes are canonically named `FlowDiscrete*`; the former
  `FlowIn` / `FlowOut` / `FlowOutTempo` / `FlowOutOnTrigger` names remain as
  permanent aliases inside the canonical inheritance chain. Never rewrite
  existing code from a legacy name to its canonical one.
- Continuous flow classes are named `FlowContinuous*` and reuse the same
  `var_fed` naming, plus `var_demand` for the upstream demand channel
