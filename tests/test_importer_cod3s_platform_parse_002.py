"""Unit tests — KB lookup map building (pure, no muscadet runtime)."""

from muscadet.importers.cod3s_platform import _build_kb_lookup


def test_empty_kb():
    assert _build_kb_lookup({}) == {}
    assert _build_kb_lookup({"component_templates": {}}) == {}


def test_single_class_with_one_input_one_output():
    kb = {
        "component_templates": {
            "MyClass": {
                "interfaces": {
                    "in_a__input": {
                        "name": "in_a",
                        "port_type": {"general": "input"},
                    },
                    "out_a__output": {
                        "name": "out_a",
                        "port_type": {"general": "output"},
                        "prod_cond": [["in_a"]],
                    },
                },
            }
        }
    }
    lookup = _build_kb_lookup(kb)
    assert "MyClass" in lookup
    assert len(lookup["MyClass"]) == 2
    flows_by_name = {f.name: f for f in lookup["MyClass"]}
    assert flows_by_name["in_a"].direction == "input"
    assert flows_by_name["in_a"].logic == "or"  # default
    assert flows_by_name["out_a"].direction == "output"
    assert flows_by_name["out_a"].logic == [["in_a"]]


def test_multiple_classes():
    kb = {
        "component_templates": {
            "A": {"interfaces": {}},
            "B": {"interfaces": {}},
            "C": {"interfaces": {}},
        }
    }
    lookup = _build_kb_lookup(kb)
    assert set(lookup) == {"A", "B", "C"}
    assert all(v == [] for v in lookup.values())


def test_dict_key_naming_is_ignored_in_favor_of_port_type():
    """Even if the dict key has an unconventional suffix, ``port_type.general``
    is the source of truth for direction."""
    kb = {
        "component_templates": {
            "X": {
                "interfaces": {
                    "weird_key_name": {
                        "name": "out_a",
                        "port_type": {"general": "output"},
                        "prod_cond": [],
                    }
                }
            }
        }
    }
    lookup = _build_kb_lookup(kb)
    assert lookup["X"][0].direction == "output"
    assert lookup["X"][0].name == "out_a"
