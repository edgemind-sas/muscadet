"""The double reading: one export, two roads, one document.

A platform export reaches a system declaration by two roads. The LIVE road
materialises it on the engine and reads the system back
(``apply_to_system`` then ``muscadet.declare.system_spec``). The PURE road
writes the document from the parse context alone
(``muscadet.package_declaration.context_declaration``). The two documents must
be the same, key for key, and so must the names the document says each
component exposes and the names the engine really holds.

This module is the instrument. The root ``conftest.py`` installs it around
``apply_to_system``, so EVERY export any test of the suite builds is read both
ways, not a list of models somebody keeps up to date.
"""

import json

from muscadet import declare, package_declaration
from muscadet.importers import cod3s_platform as importer

#: Exports compared since the process started, for the suite's own report.
COMPARED = {"exports": 0}

#: The importer's own ``apply_to_system``, kept for a caller that has to time
#: the live road alone.
ORIGINAL = {}


class DoubleReadingMismatch(AssertionError):
    """The pure road and the live road disagree on one export."""


def canonical(document):
    """A document in the form two readings are compared in: keys sorted."""
    return json.loads(json.dumps(document, sort_keys=True))


def differences(pure, live, path="$", out=None, limit=40):
    """Every path where two canonical documents differ, as ``(path, pure, live)``."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    numbers = (int, float)
    if type(pure) is not type(live) and not (
        isinstance(pure, numbers)
        and isinstance(live, numbers)
        and not isinstance(pure, bool)
        and not isinstance(live, bool)
    ):
        out.append((path, pure, live))
    elif isinstance(pure, dict):
        for key in sorted(set(pure) | set(live)):
            if key not in pure:
                out.append((f"{path}.{key}", "<absent>", live[key]))
            elif key not in live:
                out.append((f"{path}.{key}", pure[key], "<absent>"))
            else:
                differences(pure[key], live[key], f"{path}.{key}", out, limit)
    elif isinstance(pure, list):
        if len(pure) != len(live):
            out.append((f"{path}[len]", len(pure), len(live)))
        for index, (left, right) in enumerate(zip(pure, live)):
            label = left.get("name", index) if isinstance(left, dict) else index
            differences(left, right, f"{path}[{label}]", out, limit)
    elif pure != live:
        out.append((path, pure, live))
    return out


def live_catalogue(system):
    """What each component of a live system exposes, as the engine lists it."""
    return {
        comp.basename(): {
            "variables": sorted(var.basename() for var in comp.variables()),
            "states": sorted(state.basename() for state in comp.states()),
            "automata": sorted(aut.basename() for aut in comp.automata()),
        }
        for comp in system.components("#.*", "#.*")
    }


def _report(title, diffs):
    lines = [title]
    lines += [
        f"  {path}\n      pure: {json.dumps(pure, default=str)[:300]}"
        f"\n      live: {json.dumps(live, default=str)[:300]}"
        for path, pure, live in diffs[:12]
    ]
    if len(diffs) > 12:
        lines.append(f"  ... and {len(diffs) - 12} more")
    return "\n".join(lines)


def compare(pure, system):
    """Compare a pure document to the live ``system`` it should describe."""
    live = canonical(declare.system_spec(system))
    diffs = differences(canonical(pure), live)
    if diffs:
        raise DoubleReadingMismatch(
            _report("the pure declaration differs from the live one:", diffs)
        )
    diffs = differences(
        canonical(package_declaration.component_catalogue(pure)),
        canonical(live_catalogue(system)),
    )
    if diffs:
        raise DoubleReadingMismatch(
            _report("the catalogue differs from what the engine exposes:", diffs)
        )
    COMPARED["exports"] += 1


def install():
    """Read every export the suite materialises by both roads.

    Wraps ``apply_to_system``. A system that already held components is left
    alone: the pure road describes an export, not what a test added before it.
    """
    original = importer.apply_to_system
    if getattr(original, "double_reading", False):
        return
    ORIGINAL["apply_to_system"] = original

    def apply_to_system(ctx, system, *, create_default_out_automata=True):
        fresh = not (getattr(system, "comp", None) or {})
        original(ctx, system, create_default_out_automata=create_default_out_automata)
        if fresh:
            name = system.name() if callable(getattr(system, "name", None)) else None
            try:
                pure = package_declaration.context_declaration(
                    ctx,
                    name=name,
                    create_default_out_automata=create_default_out_automata,
                )
                compare(pure, system)
            except BaseException:
                # The caller never receives the system, so nobody else can
                # drop it: left alive, it would fail every later build of the
                # process on a bare engine error naming nothing. Whatever the
                # pure road raised, a mismatch or a refusal the live road did
                # not make, the system goes first.
                tear_down(system)
                raise

    apply_to_system.double_reading = True
    apply_to_system.__doc__ = original.__doc__
    importer.apply_to_system = apply_to_system


def tear_down(system):
    """Drop a live system and close the engine session it was built in."""
    import cod3s

    system.deleteSys()
    cod3s.terminate_session()


def live_package_declaration(export, study, check_catalogue=True):
    """The declaration of a run package by the LIVE road, then the teardown.

    The export is materialised on the engine and the study applied on the live
    objects, section by section, by the helpers a run uses. ``(document,
    modes added)`` is returned, the second being what the live road counts.

    The catalogue read off that document is compared to what the engine
    exposes, modes included. ``check_catalogue=False`` is for a study the live
    road half-builds: a mode it logged as failed is in its document and builds
    nothing on the engine, so there is nothing to compare.
    """
    from cod3s.scripts.study_runner import (
        add_events,
        add_failure_modes,
        add_indicators,
        apply_attribute_overrides,
    )
    from cod3s.specs.study_yaml import StudyYaml

    study = StudyYaml.model_validate(study)
    system = importer.system_from_export(export)
    try:
        added = add_failure_modes(system, study.failure_modes)
        add_events(system, study.events)
        add_indicators(system, study.indicators)
        if study.attribute_overrides is not None:
            apply_attribute_overrides(system, study.attribute_overrides)
        live = canonical(declare.system_spec(system))
        names = check_catalogue and differences(
            canonical(package_declaration.component_catalogue(live)),
            canonical(live_catalogue(system)),
        )
        if names:
            raise DoubleReadingMismatch(
                _report("the catalogue differs from what the engine exposes:", names)
            )
        return live, added
    finally:
        tear_down(system)
