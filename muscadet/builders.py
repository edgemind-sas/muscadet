"""COD3S study system builders provided by muscadet.

Currently exposes :class:`PlatformExportBuilder`, which satisfies the
:class:`cod3s.scripts.builders.SystemBuilder` Protocol so a muscadet
flow system can be plugged into ``cod3s.scripts.study_runner.run_study``
when the model comes from a COD3S Platform JSON export.

Why does this live in muscadet and not cod3s-lib? Because muscadet
owns the COD3S Platform importer (:mod:`muscadet.importers.cod3s_platform`)
and cod3s-lib must not depend on muscadet. The builder is the natural
glue: cod3s-lib defines the Protocol, muscadet provides one concrete
implementation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Union


class PlatformExportBuilder:
    """Build a muscadet ``System`` from a COD3S Platform JSON export.

    Wraps :func:`muscadet.importers.cod3s_platform.system_from_export`.
    Accepts either:

    - a path (``Path`` / ``str``) to a JSON file holding the export
      payload (lazy: read on :meth:`build`),
    - a pre-loaded ``dict`` payload (eg. obtained via
      ``GET /modelisation/{name}/export?include_kb=true``).

    Args:
        export: Path to the JSON export file or a pre-loaded payload dict.
        name: Optional system name override (defaults to
            ``payload['model']['name']``).
        system_class: Optional muscadet ``System`` subclass to instantiate.
        create_default_out_automata: Forwarded to
            :func:`system_from_export`. Default ``True`` matches the
            importer's lean defaults.

    Example:

    .. code-block:: python

        from muscadet.builders import PlatformExportBuilder
        from cod3s.scripts.study_runner import run_study

        builder = PlatformExportBuilder("model_export.json")
        run_study(
            system_builder=builder,
            study="study.yaml",
            results_dir="./results",
        )
    """

    def __init__(
        self,
        export: Union[Path, str, dict],
        *,
        name: Optional[str] = None,
        system_class: Optional[type] = None,
        create_default_out_automata: bool = True,
    ) -> None:
        if isinstance(export, dict):
            self._payload: Optional[dict] = export
            self._path: Optional[Path] = None
        else:
            self._payload = None
            self._path = Path(export)
        self.name = name
        self.system_class = system_class
        self.create_default_out_automata = create_default_out_automata

    def _load_payload(self) -> dict:
        """Return the payload dict, reading from disk on first call."""
        if self._payload is not None:
            return self._payload
        if self._path is None:
            raise RuntimeError(
                "PlatformExportBuilder: neither payload nor path was provided."
            )
        if not self._path.exists():
            raise FileNotFoundError(
                f"Platform export file not found: {self._path}"
            )
        self._payload = json.loads(self._path.read_text())
        return self._payload

    def build(self, *, logger: Any = None) -> Any:
        """Construct and return the populated muscadet System.

        Lazy-imports :func:`system_from_export` to keep the builder
        class itself importable without the PyCATSHOO runtime.
        """
        from muscadet.importers.cod3s_platform import system_from_export

        payload = self._load_payload()
        if logger is not None:
            comp_count = len(
                (payload.get("model") or {}).get("elements", {}).get(
                    "components", {}
                )
            )
            logger.info1(
                f"Building muscadet system from platform export "
                f"({comp_count} component(s))"
            )
        return system_from_export(
            payload,
            name=self.name,
            system_class=self.system_class,
            create_default_out_automata=self.create_default_out_automata,
        )

    def __repr__(self) -> str:
        if self._path is not None:
            return f"PlatformExportBuilder(export={str(self._path)!r})"
        return "PlatformExportBuilder(export=<dict>)"


__all__ = ["PlatformExportBuilder"]
