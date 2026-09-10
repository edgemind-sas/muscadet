"""The build order a system declaration is rebuilt in, derived from what it says.

**A JSON object's key order carries no meaning.** A formatter re-serialises it
sorted, a diff tool does, a round trip through a database does, and above all
an engine whose maps are ordered by key does -- ``serde_json`` over a
``BTreeMap`` sorts, which is exactly the RAICHU path this declaration exists
for. A document that rebuilds only in the order it happened to be written in is
therefore not an exchange format, whatever ``version`` it carries.

It was one until this module's subject existed. Measured 2026-09-10 on a
platform study of six modes, ``build_system`` rebuilt the document from its
insertion order and raised ``KeyError`` on the SAME document with its keys
sorted: a mode whose ``occ_cond`` names another mode's ``occ`` state resolves
that name against the LIVE system while it is being built, so the referenced
mode has to exist already, and sorted order put all six referencing modes ahead
of all six referenced ones.

This module works on DATA alone -- no system is built, so there is nothing to
tear down. That is the point of :func:`component_build_order` being a function
over the document rather than a loop inside the build: the order is decidable,
and a cycle refusable, before an engine object exists.

The shapes exercised here are the platform's own: a mode filed as a component
of its own, carrying ``targets`` and an ``occ_cond`` clause naming another
mode. ``check_spec`` does not read those keys on every muscadet -- reading a
standalone mode back is a ticket of its own -- but the ORDER never depended on
being able to build them, and pinning the real shape is what makes this module
still true the day it can.
"""

import json
import random

import pytest

from muscadet.declare import (
    COMPONENT_REFERENCE_KEYS,
    SystemSpecError,
    check_system_spec,
    component_build_order,
    component_references,
)

#: The seed the permutation is drawn with. Fixed, so a failure is reproducible;
#: a permutation rather than the sorted form alone, because sorting is only ONE
#: of the orders a consumer may hand back and the property claimed is about all
#: of them.
SEED = 20260910

#: How many permutations one property is checked over. Seven components have
#: 5040 orders; twenty draws cost microseconds and cover the shapes neither
#: insertion nor sorting produces.
DRAWS = 20


def a_mode(name, target, conditioned_on=None):
    """One mode declared as a component, the way the platform emits one.

    ``targets`` names what the mode acts on and ``occ_cond`` is the platform's
    "condition on another mode" (``ref_mode_mdd``): a detection mode cannot
    occur until the crack it detects has.
    """
    spec = {
        "name": name,
        "kind": "failure_mode",
        "cls": "ObjFMExp",
        "fm_name": name.split("__")[-1],
        "targets": [target],
        "failure_param": [1e-4],
    }
    if conditioned_on is not None:
        spec["occ_cond"] = [
            [{"attr": "occ", "obj": conditioned_on, "ope": "==", "value": True}]
        ]
    return spec


def the_study():
    """The measured study, reduced: one rail, three cracks, three detections.

    Each detection names its own crack, and every mode targets the rail. The
    document is written the way the platform writes it -- the crack before the
    detection that names it -- which is exactly why insertion order used to
    work and nothing else did.
    """
    document = {"Rail_1__Rail": {"name": "Rail_1__Rail"}}
    for index in (1, 2, 3):
        crack = f"Rail_1__Rail__Rail__Fissure_{index}_O"
        detection = f"Rail_1__Rail__Rail__Detect_{index}_O"
        document[crack] = a_mode(crack, "Rail_1__Rail")
        document[detection] = a_mode(detection, "Rail_1__Rail", conditioned_on=crack)
    return document


def reordered(document, keys):
    return {key: document[key] for key in keys}


def permutations(document, draws=DRAWS):
    """``draws`` orders of ``document``'s keys, drawn from :data:`SEED`."""
    rng = random.Random(SEED)
    keys = list(document)
    for _ in range(draws):
        drawn = list(keys)
        rng.shuffle(drawn)
        yield drawn


def assert_order_holds(document, order):
    """Every component comes after everything its declaration names."""
    declared = {key: key for key in document}
    position = {name: index for index, name in enumerate(order)}

    assert sorted(order) == sorted(document), "the order lost or invented a component"

    for name, comp_spec in document.items():
        for referenced in component_references(comp_spec, declared):
            assert position[referenced] < position[name], (
                f"{name!r} is built before {referenced!r}, which it names: the "
                f"engine resolves that name against the live system and would "
                f"raise KeyError on it"
            )


# ----------------------------------------------------------------------
# The property: one document, every order, the same build order out
# ----------------------------------------------------------------------


def test_the_document_rebuilds_in_the_order_it_was_written():
    """The order that used to work by chance still works, on purpose."""
    document = the_study()
    assert_order_holds(document, component_build_order(document))


def test_the_document_rebuilds_from_its_sorted_form():
    """The order that used to raise KeyError.

    ``Detect`` sorts before ``Fissure``, so the sorted document puts all three
    detections ahead of the cracks they name. This is the failure that was
    measured, stated as the property it violates.
    """
    document = reordered(the_study(), sorted(the_study()))
    order = component_build_order(document)

    assert_order_holds(document, order)
    assert order != list(document), (
        "the sorted document is exactly the one whose written order does not "
        "build: an order equal to it would mean nothing was derived"
    )


def test_the_document_rebuilds_from_any_permutation_of_its_keys():
    """Stronger than either: the orders neither insertion nor sorting makes."""
    reference = the_study()
    for keys in permutations(reference):
        document = reordered(reference, keys)
        assert_order_holds(document, component_build_order(document))


def test_every_permutation_yields_the_same_dependency_relation():
    """The derived order is a function of the DOCUMENT, not of its key order.

    Two permutations may legitimately produce two different build orders --
    the references only constrain some pairs -- but each one satisfies the
    same relation, and each one carries the same components.
    """
    reference = the_study()
    for keys in permutations(reference):
        order = component_build_order(reordered(reference, keys))
        assert set(order) == set(reference)
        assert len(order) == len(reference)


def test_a_document_that_survives_json_survives_the_order_too():
    """The crossing that reorders in practice: dumped sorted, read back."""
    reference = the_study()
    document = json.loads(json.dumps(reference, sort_keys=True))
    assert list(document) != list(reference), "sort_keys did not reorder anything"
    assert_order_holds(document, component_build_order(document))


# ----------------------------------------------------------------------
# What a reference is, and what it is not
# ----------------------------------------------------------------------


def test_a_condition_naming_another_mode_is_a_reference():
    document = the_study()
    declared = {key: key for key in document}
    detection = "Rail_1__Rail__Rail__Detect_2_O"

    assert component_references(document[detection], declared) == [
        "Rail_1__Rail",
        "Rail_1__Rail__Rail__Fissure_2_O",
    ]


def test_a_target_is_a_reference_whether_it_is_a_list_or_a_name():
    """``targets`` takes one name or many, and ``target_name`` the single one."""
    declared = {"A": "A", "B": "B", "C": "C"}

    assert component_references({"name": "M", "targets": ["A", "B"]}, declared) == [
        "A",
        "B",
    ]
    assert component_references({"name": "M", "targets": "A"}, declared) == ["A"]
    assert component_references({"name": "M", "target_name": "C"}, declared) == ["C"]


def test_a_reference_nested_deep_in_a_condition_tree_is_found():
    """A condition tree is a list of OR clauses of AND clauses of leaves.

    Found by walking the declaration rather than by looking in the places a
    reference is expected: the depth is the format's, not muscadet's, and a
    clause one level deeper than foreseen would otherwise be invisible.
    """
    declared = {"M": "M", "A": "A", "B": "B"}
    spec = {
        "name": "M",
        "occ_cond": [
            [{"attr": "occ", "obj": "A", "value": True}],
            [
                {"attr": "occ", "obj": "B", "value": False},
                {"attr": "occ", "obj": "A", "value": True},
            ],
        ],
    }

    assert component_references(spec, declared) == ["A", "B"]


def test_a_name_the_document_does_not_declare_is_not_a_reference():
    """``build_system`` fills a system the caller may already have filled.

    A reference to something built outside the document is legitimate, so it
    is dropped rather than refused -- refusing it would break the documented
    ``build_system(spec, system=already_built)`` route.
    """
    declared = {"M": "M"}
    spec = a_mode("M", "BUILT_ELSEWHERE", conditioned_on="ALSO_ELSEWHERE")

    assert component_references(spec, declared) == []
    assert component_build_order({"M": spec}) == ["M"]


def test_a_component_naming_itself_is_not_a_reference():
    """Otherwise every self-conditioned mode would read as a one-node cycle."""
    spec = a_mode("M", "M", conditioned_on="M")

    assert component_references(spec, {"M": "M"}) == []
    assert component_build_order({"M": spec}) == ["M"]


def test_the_reference_vocabulary_is_written_down_once():
    """Pinned so the walk keeps a declared vocabulary and not an ad-hoc one."""
    assert COMPONENT_REFERENCE_KEYS == {"obj", "targets", "target_name"}


# ----------------------------------------------------------------------
# A document that constrains nothing keeps the order it was given
# ----------------------------------------------------------------------


def test_a_document_without_references_is_built_in_its_own_order():
    """The derived order is the document's everywhere the references leave it
    free: any topological order would build, and only this one leaves the four
    fifths of documents that reference nothing exactly as they were."""
    document = {"c": {"name": "c"}, "a": {"name": "a"}, "b": {"name": "b"}}

    assert component_build_order(document) == ["c", "a", "b"]


def test_only_the_constrained_pairs_move():
    """One reference reorders one pair, and leaves the rest where they were."""
    document = {
        "z": {"name": "z"},
        "needs_y": a_mode("needs_y", "z", conditioned_on="y"),
        "y": {"name": "y"},
        "w": {"name": "w"},
    }

    assert component_build_order(document) == ["z", "y", "needs_y", "w"]


def test_an_empty_document_has_an_empty_order():
    assert component_build_order({}) == []


# ----------------------------------------------------------------------
# The cycle: refused by the names that form it, never by a KeyError
# ----------------------------------------------------------------------


def test_a_two_mode_cycle_is_refused_by_both_names():
    """Two modes each conditioned on the other: no order satisfies them."""
    document = {
        "T": {"name": "T"},
        "A": a_mode("A", "T", conditioned_on="B"),
        "B": a_mode("B", "T", conditioned_on="A"),
    }

    with pytest.raises(SystemSpecError) as refusal:
        component_build_order(document)

    message = str(refusal.value)
    assert "'A'" in message and "'B'" in message
    assert "cycle" in message
    # The component that constrains nothing is not dragged into the message.
    assert "'T'" not in message


def test_a_three_mode_cycle_names_all_three_in_order():
    document = {
        "T": {"name": "T"},
        "A": a_mode("A", "T", conditioned_on="B"),
        "B": a_mode("B", "T", conditioned_on="C"),
        "C": a_mode("C", "T", conditioned_on="A"),
    }

    with pytest.raises(SystemSpecError) as refusal:
        component_build_order(document)

    assert "'A' -> 'B' -> 'C' -> 'A'" in str(refusal.value)


def test_a_cycle_through_targets_is_refused_too():
    """The loop does not have to run through conditions to exist."""
    document = {
        "A": {"name": "A", "targets": ["B"]},
        "B": {"name": "B", "targets": ["A"]},
    }

    with pytest.raises(SystemSpecError) as refusal:
        component_build_order(document)

    assert "'A' -> 'B' -> 'A'" in str(refusal.value)


def test_a_cycle_is_never_a_key_error():
    """The whole point of the refusal.

    ``KeyError: 'Rail_1__Rail__Rail__Fissure_O'`` is what the engine answers,
    and it names neither the document, nor the component that referenced it,
    nor the fact that both are declared a few keys apart. Pinned as a
    NON-KeyError so a future rewrite cannot regress to the engine's message.
    """
    document = {
        "A": a_mode("A", "A", conditioned_on="B"),
        "B": a_mode("B", "B", conditioned_on="A"),
    }

    with pytest.raises(SystemSpecError):
        component_build_order(document)

    try:
        component_build_order(document)
    except SystemSpecError as refusal:
        assert not isinstance(refusal, KeyError)


def test_a_cycle_survives_being_reordered():
    """A cycle is a property of the document, not of the order it is read in."""
    reference = {
        "A": a_mode("A", "A", conditioned_on="B"),
        "B": a_mode("B", "B", conditioned_on="C"),
        "C": a_mode("C", "C", conditioned_on="A"),
    }
    for keys in permutations(reference, draws=6):
        with pytest.raises(SystemSpecError, match="cycle"):
            component_build_order(reordered(reference, keys))


def test_a_cycle_is_refused_before_anything_is_built():
    """``check_system_spec`` validates without building, and this is checkable
    from the document alone, so a caller sorting a batch learns about it
    without paying for an engine.

    The declaration below carries the reference inside a ``failure_modes``
    entry, which every muscadet reads: the cycle is the subject here, not the
    shape that carries it.
    """

    def conditioned_on(name, other):
        return {
            "name": name,
            "failure_modes": [
                {
                    "cls": "exp",
                    "name": "fm",
                    "failure_cond": [
                        [{"attr": "occ", "obj": other, "ope": "==", "value": True}]
                    ],
                }
            ],
        }

    spec = {
        "version": "1.0.0",
        "name": "cyclic",
        "components": {
            "A": conditioned_on("A", "B"),
            "B": conditioned_on("B", "A"),
        },
    }

    with pytest.raises(SystemSpecError, match="cycle"):
        check_system_spec(spec)


def test_a_declaration_that_is_merely_reordered_still_validates():
    """The counterpart: reordering alone never turns a good document bad."""

    def conditioned_on(name, other):
        return {
            "name": name,
            "failure_modes": [
                {
                    "cls": "exp",
                    "name": "fm",
                    "failure_cond": [
                        [{"attr": "occ", "obj": other, "ope": "==", "value": True}]
                    ],
                }
            ],
        }

    components = {
        "Fissure": {"name": "Fissure"},
        "Detect": conditioned_on("Detect", "Fissure"),
    }
    for keys in permutations(components, draws=6):
        check_system_spec(
            {
                "version": "1.0.0",
                "name": "acyclic",
                "components": reordered(components, keys),
            }
        )
