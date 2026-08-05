import Pycatshoo as pyc

#: Occurrence law of an instantaneous transition: it fires as soon as its
#: condition holds. Every automaton muscadet builds to watch a crossing uses
#: it -- a rule set's mode automaton (R12), a capacity's empty/full bounds
#: (R7) and a discrete production condition's threshold (R22) -- and every one
#: of them registers its transitions as WATCHED, so the solver stops the
#: integration exactly at the crossing rather than at the following step.
#:
#: Lives here, in the dependency-free utility module, so the three units that
#: need it can share one definition without importing one another.
INSTANT_OCC_LAW = {"cls": "delay", "time": 0}


def fresh_instant_occ_law():
    """A private copy of :data:`INSTANT_OCC_LAW`.

    ``TransitionModel.sanitize_occ_law`` rewrites the ``cls`` entry in place,
    so a shared mapping would be capitalised twice.
    """
    return dict(INSTANT_OCC_LAW)


def entity_label(kind, info, quote=False):
    """``kind`` followed by the entity's own name, when validation already has it.

    A ``field_validator`` fires while the model is still being built, so it
    cannot reach ``self``: the entity's name is available through
    ``pydantic.ValidationInfo`` only once the ``name`` field has itself
    validated, which holds for every model that declares ``name`` first. This
    is what lets a low-level field message say WHICH capacity, held flow or
    guard operand is wrong, the way the sibling ``model_validator`` messages
    already do -- and fall back to the bare ``kind`` when it cannot.

    Parameters
    ----------
    kind : str
        What the entity is, e.g. ``"Capacity"``.
    info : pydantic.ValidationInfo or None
        The validation context the field validator received.
    quote : bool
        True to render the name with ``repr``, matching the sibling messages of
        the models that quote it.
    """
    name = (getattr(info, "data", None) or {}).get("name")
    if not name:
        return kind
    return f"{kind} {name!r}" if quote else f"{kind} {name}"


def get_pyc_type(var_type):
    if var_type == 'bool':
        return (bool, pyc.TVarType.t_bool)
    elif var_type == 'int':
        return (int, pyc.TVarType.t_integer)
    elif var_type == 'float':
        return (float, pyc.TVarType.t_double)
    else:
        raise ValueError(
            f"Type {var_type} not supported by PyCATSHOO")
