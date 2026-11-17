# AGENTS.md

## Scope and intent

These instructions apply to the whole repository. MUSCADET is a Python 3.10
library built on COD3S and the native PyCATSHOO engine. It models discrete and
continuous flows, capacities, transformations, controllers, failure modes, and
their simulation ordering.

Make changes conservatively. The public API is used both from Python subclasses
and from data declarations imported from COD3S Platform. A change that looks
local can affect declaration round trips, engine object names, equation order,
or old models using compatibility aliases.

## Repository map

- `muscadet/obj.py`: the central `ObjFlow` declaration surface. Continuous
  evaluation, capability, and derating functions are implemented in separate
  modules and bound here as methods to preserve override points.
- `muscadet/flow.py`: discrete flow models and the permanent legacy aliases
  (`FlowIn`, `FlowOut`, `FlowOutTempo`, `FlowOutOnTrigger`).
- `muscadet/flow_continuous.py`: continuous ports, allocation policies, demand
  channels, and production profiles.
- `muscadet/evaluation.py`, `muscadet/capability.py`: the ordered capability,
  upstream-demand, and downstream-production sweeps. Solver callbacks must not
  create or register engine objects.
- `muscadet/capacity.py`, `muscadet/rules.py`, `muscadet/transfer.py`,
  `muscadet/mixture.py`: storage, transformations, transfer pairs, and grouped
  mixture draws.
- `muscadet/ordering.py`: graph reconstruction, algebraic-cycle diagnostics,
  loop detection, and unique PDMP equation ordering.
- `muscadet/system.py`: system lifecycle, shared pre-run preparation, PDMP
  manager ownership, model freezing, and connection helpers.
- `muscadet/declare.py`: canonical data declaration/build/read-back path. Its
  declaration section order is semantic, not cosmetic.
- `muscadet/importers/cod3s_platform.py`: a deliberately split parse/apply
  bridge for Platform exports. Parsing should remain engine-free.
- `muscadet/obj_ctrl.py`: controller grammar and runtime.
- `muscadet/kb/`: shipped reusable component classes.
- `tests/`: executable specification. Most simulation modules own one
  module-scoped system and explicitly release it in a final `test_delete`.
- `examples/`: user-facing models; `examples/isimu/` exposes `build()` factories
  for `cod3s-isimu`.
- `README.md`, `docs/USE_CASES.md`, `CHANGELOG.md`: the public semantics,
  validated physical examples, compatibility notes, and migration record.
- `docs/plans/` and `docs/brainstorms/`: design history. Use them to understand
  settled invariants, but verify the current implementation and tests.

## Core invariants

Preserve these unless a change explicitly redesigns and documents them:

1. Continuous evaluation has three globally ordered bands: capability,
   reverse-topological demand, then topological production. Capacity equations
   run afterwards; controller/signal equations have their own ordered band.
   Every registered equation must receive a distinct integer order.
2. Continuous algebraic cycles are rejected unless an integrated capacity
   genuinely breaks the dependency. Do not weaken cycle diagnostics merely to
   make a model start.
3. Both `simulate()` and the interactive engine primitive `startInteractive()`
   must execute the same idempotent pre-run preparation before any equation is
   registered. Hook a new step on the primitive, not on the `isimu_start()`
   wrapper: `cod3s-isimu` and `isimu_start_cli` call `startInteractive()`
   directly and never go through the wrapper.
4. A continuous system is frozen after its first pre-run because PyCATSHOO
   cannot unregister equations. Adding a late continuous component, connection,
   sourced measurement, or other equation-bearing declaration must fail loudly.
   Purely discrete systems retain their historical mutability.
5. Continuous solver callbacks are repeated within an integration step. Keep
   them deterministic and free of topology mutation, registrations, and other
   one-time side effects.
6. Conservation matters: delivered input must be consumed, stored, transferred,
   or explicitly released; produced output must be delivered, stored, or remain
   accounted for. Add balance assertions when changing allocation or transfer
   logic.
7. Declaration order in `muscadet.declare.DECLARATION_SECTIONS` encodes real
   dependencies and validation reachability. Do not alphabetize or casually
   reorder it. Keep build -> read-back -> rebuild round trips stable.
8. Platform import stays fail-fast. Unknown keys, unsupported combinations, and
   information that would otherwise be silently dropped must raise a specific
   `Cod3sPlatformImportError` naming the class/component/field involved.
9. Discrete classes are canonically `FlowDiscrete*`, but the old `FlowIn` /
   `FlowOut*` names are permanent aliases in the inheritance chain. Never
   mechanically migrate existing code or emitted Platform `cls` strings.
10. Preserve public exports from `muscadet/__init__.py`, engine-facing variable,
    automaton, state, transition, message-box, and indicator names unless the
    change is intentionally breaking and carries migration documentation.
11. A capacity, rule, transfer pair, measurement, controller, and mixture each
    own distinct semantics. Reuse their shared validation helpers rather than
    implementing a slightly different grammar in an importer or builder.
12. Float values crossing PyCATSHOO have roughly seven significant digits.
    Tests of physical balances should use tolerances justified by scale and
    cancellation, normally via `pytest.approx`.

## Environment and commands

The supported interpreter is exactly Python `>=3.10,<3.11`; `.python-version`
is `3.10`. A populated `.venv` is normally present. The runtime dependency on
COD3S is pinned to a Git tag in `pyproject.toml`; bump it only with the MUSCADET
release that requires the new engine behavior.

Prefer the repository environment explicitly:

```sh
.venv/bin/python -m pytest tests/path_or_file.py -q
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/ --runslow -q
.venv/bin/black --check muscadet tests
.venv/bin/isort --check-only muscadet tests
```

Always scope pytest to `tests/`. Test configuration lives in the
`[tool.pytest.ini_options]` table of `pyproject.toml` alone: pytest reads the
first inifile it finds and never merges two, so do not add a `pytest.ini`,
`tox.ini` or `setup.cfg` section beside it (see `CONVENTIONS.md`). Slow tests
are skipped unless `--runslow` is supplied.

Black and isort pass. Do not advertise mypy or Ruff as required gates yet: mypy is
configured for Python 3.9 even though the current mypy requires 3.10+, native
dependencies lack stubs, and the existing tree has known typing/lint debt. Run
them for targeted guidance if useful, and report pre-existing findings
separately from regressions introduced by the change.

## Testing with PyCATSHOO

PyCATSHOO owns native, process-global resources and effectively permits only one
live system in several test scenarios. Test structure is therefore part of
correctness:

- Prefer one system per test module with a module-scoped fixture when several
  assertions inspect one simulation.
- A test module that builds a system must finish with a `test_delete` that calls
  `system.deleteSys()` and `cod3s.terminate_session()`.
- If a test needs two systems, fully tear down the first before constructing the
  second. Do not rely on Python reference counting alone.
- `tests/conftest.py` forces cyclic garbage collection at module boundaries.
  Keep that safeguard; the suite otherwise depends on reporter verbosity and
  may exhaust engine resources.
- Put teardown in defensive `try/finally` paths when a fixture can fail before
  its final test. Preserve the original exception when cleanup also fails.
- Exercise both batch and interactive entry points when changing pre-run,
  ordering, watched transitions, or controller behavior.
- For a defect, first add the smallest regression reproducer. Assert the
  semantic result and, where relevant, conservation, exact diagnostic content,
  graph/order metadata, declaration round trip, and teardown.

## Coding conventions

Follow `CONVENTIONS.md` and the surrounding code:

- Write code, docstrings, user-facing diagnostics, documentation, and commits in
  English.
- Use four spaces, Black's 88-column format, isort's Black profile,
  `snake_case` functions/variables, and `CamelCase` classes.
- Add type hints and useful docstrings to new public APIs. Prefer Pydantic v2
  models and validators for declarative data structures.
- Keep imports grouped standard library, third party, then local.
- Raise specific exceptions with messages that identify the model entity and
  invalid value. A rejected declaration is preferable to a successful run that
  silently ignores information.
- Avoid mutable default arguments in new code. Some legacy public signatures
  still contain them; do not copy that pattern.
- Do not catch `Exception` merely to hide a model or engine error. Representation
  helpers may degrade to `"N/A"`, but runtime and declaration paths must remain
  fail-fast.
- Use the existing declaration-copy helpers when retaining caller-supplied
  mappings/lists; do not mutate input payloads.
- Preserve declaration order in mappings where it drives deterministic tie
  breaking or serialization.
- Keep parse/data layers independent from PyCATSHOO wherever possible; create
  native objects only in the apply/runtime layer.

## Change workflow

Before editing:

1. Read the relevant public documentation, implementation module, neighboring
   tests, and recent changelog/design notes.
2. Check `git status`; preserve unrelated tracked and untracked user changes.
3. Identify every surface affected: Python subclass API, data declaration,
   package-root export, Platform importer, runtime ordering, docs, and examples.

While editing:

1. Keep patches narrow and add tests beside the existing feature family.
2. Reuse the canonical parser/normalizer/validator instead of duplicating it.
3. Maintain backward-compatible defaults. Absence and an explicit neutral value
   are not always equivalent in this codebase.
4. When a model becomes invalid, raise before registering irreversible engine
   state, ideally during declaration parsing or the shared pre-run step.
5. Update capability markers used by downstream Platform code when adding a
   bridge feature.

Before handing off:

1. Run the focused test file(s), then the full non-slow suite when feasible.
2. Run Black and isort checks on touched Python trees.
3. If simulation semantics changed, run the relevant physical/use-case test and
   both run entry points where applicable.
4. Update README/API examples and `CHANGELOG.md` for user-visible behavior,
   refusals, compatibility changes, and migration instructions.
5. Report commands and exact outcomes, including skips/warnings and any check
   that remains unavailable or pre-existing.

## Documentation and release expectations

Documentation in this project explains not only what was added, but why a
model is accepted or refused and how to rewrite a newly invalid model. Match
that standard. New physical functionality should include an independently
checkable invariant or closed-form/reference comparison when possible.

Version comes from `muscadet/version.py`. Packaging uses setuptools, while
development dependencies are declared under `[tool.uv]`. Do not create or
commit generated simulation study directories, caches, compiled presentations,
or `uv.lock`; the current `.gitignore` intentionally excludes them.
