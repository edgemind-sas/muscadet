"""Interactive simulation factories for muscadet systems.

Each module in this package exposes a top-level ``build(...)`` callable
returning a populated :class:`muscadet.System`. Drive them either from
:command:`cod3s-isimu` (the Textual TUI shipped with the
``cod3s[isimu]`` extra) or from the lightweight ``run`` helpers below
which step through transitions and print flow values.

Example
-------

TUI (requires ``pip install -e ".../cod3s[isimu]"``)::

    cod3s-isimu --factory examples.isimu.rbd_kn:build

Python (no extra dependency)::

    from examples.isimu import rbd_kn
    rbd_kn.run()
"""
