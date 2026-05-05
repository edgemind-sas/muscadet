"""Plugin namespace for importing models from external formats into ``muscadet``.

Each importer module under this package translates a foreign model
representation (typically a JSON / YAML payload) into a
``muscadet.System`` instance, ready to be consumed by ``cod3s-isimu``
or ``cod3s.simulate()``.

Available importers:

- ``cod3s_platform`` : COD3S Platform model export
  (``GET /modelisation/{name}/export?include_kb=true``). See
  :func:`muscadet.importers.cod3s_platform.system_from_export`.

The package follows a "one file per format" convention. Each module
exposes at least one public entry point named ``system_from_*``
returning a populated ``muscadet.System`` and a domain-specific
``*ImportError`` exception (subclass of :class:`ValueError`) so callers
can catch import failures specifically.

No registry / dynamic discovery is set up — explicit imports are
preferred for clarity. If the number of formats grows beyond a
handful, a registry can be added without breaking the explicit-import
form.
"""

from muscadet.importers.cod3s_platform import (
    Cod3sPlatformImportError,
    system_from_export,
)

__all__ = [
    "Cod3sPlatformImportError",
    "system_from_export",
]
