"""Small run packages, written as data, for the tests of the pure declaration.

A run package is a platform export and a study. These are synthetic on
purpose: a chain of identical units is the smallest export on which a study
has something to say -- a common cause over several targets, an indicator
pattern that designates several variables, a value change.
"""


def chain_export(units, name="Chain"):
    """A source feeding ``units`` identical units in a row, as an export.

    Every unit relays the discrete flow ``sig``: it produces it while it
    receives it. The components are ``SRC`` then ``U0001`` ... in the order of
    the chain.
    """
    components = {"c0": {"name": "SRC", "class_name": "Source", "attributes": []}}
    connections = {}
    for index in range(1, units + 1):
        components[f"c{index}"] = {
            "name": f"U{index:04d}",
            "class_name": "Unit",
            "attributes": [],
        }
        connections[f"k{index}"] = {
            "component_source": f"c{index - 1}",
            "component_target": f"c{index}",
            "interface_source": "sig",
            "interface_target": "sig",
        }
    return {
        "model": {
            "name": name,
            "kb": {"name": "ChainKB", "version": "1.0.0"},
            "elements": {"components": components, "connections": connections},
        },
        "kb": {
            "component_templates": {
                "Source": {
                    "interfaces": {
                        "sig__output": {
                            "name": "sig",
                            "port_type": {"general": "output"},
                            "prod_cond": [],
                        }
                    }
                },
                "Unit": {
                    "interfaces": {
                        "sig__input": {
                            "name": "sig",
                            "port_type": {"general": "input"},
                        },
                        "sig__output": {
                            "name": "sig",
                            "port_type": {"general": "output"},
                            "prod_cond": [[{"name": "sig", "port": "in"}]],
                        },
                    }
                },
            }
        },
    }


def mode(name, targets, occ_rates, repair_rates, **fields):
    """One ``ObjMode2S`` of a study, in the form the platform emits it."""
    return {
        "cls": "ObjMode2S",
        "fm_name": name,
        "targets": list(targets),
        "occ_law": {"cls": "exp", "rate": list(occ_rates)},
        "occ_param_name": ["lambda"],
        "not_occ_law": {"cls": "exp", "rate": list(repair_rates)},
        "not_occ_param_name": ["mu"],
        "not_occ_state": "rep",
        "occ_effects": {"sig_fed_available_out": False},
        **fields,
    }


def indicator(component, attr_name, attr_type="VAR", **fields):
    """One indicator of a study, designating what it observes by two patterns."""
    return {
        "component": component,
        "attr_name": attr_name,
        "attr_type": attr_type,
        "measure": "value",
        "stats": ["mean"],
        **fields,
    }


def study(name="study", **sections):
    """A study holding ``sections`` and nothing else."""
    return {"name": name, **sections}
