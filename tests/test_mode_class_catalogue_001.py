"""The table of shipped mode classes says what the live tree says.

``declare.SHIPPED_MODE_CLASSES`` lets a declaration naming a cod3s mode class
be checked without the engine. It is a copy of a fact cod3s owns, so this
module compares it with the live subclass tree: a mode class cod3s adds,
removes or re-parents turns it red instead of leaving the table stale.
"""

import cod3s

from muscadet import declare


def _live_family(cls):
    for family, _vocabulary in declare.MODE_VOCABULARIES:
        if issubclass(cls, getattr(cod3s, family)):
            return family
    return None


def test_every_listed_class_speaks_the_family_the_table_gives_it():
    for name, family in declare.SHIPPED_MODE_CLASSES.items():
        assert _live_family(getattr(cod3s, name)) == family, name


def test_every_mode_class_cod3s_ships_is_listed():
    shipped = {
        cls.__name__
        for cls in cod3s.PycComponent.get_subclasses()
        if cls.__module__.startswith("cod3s.") and _live_family(cls) is not None
    }

    assert shipped == set(declare.SHIPPED_MODE_CLASSES)


def test_the_table_and_the_live_tree_give_one_vocabulary():
    for name in declare.SHIPPED_MODE_CLASSES:
        _, from_table = declare._mode_class(name, "test")
        from_tree = declare.mode_vocabulary(getattr(cod3s, name), "test")

        assert from_table is from_tree, name
