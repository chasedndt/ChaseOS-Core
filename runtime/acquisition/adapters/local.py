"""Conservative local acquisition adapter for Pass 1A."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.acquisition.plan import AcquisitionPlan, AcquisitionSource
from runtime.acquisition.validators import (
    AcquisitionValidationError,
    READABLE_SUFFIXES,
    path_is_relative_to,
)


@dataclass(frozen=True)
class AcquiredSource:
    source: AcquisitionSource
    content: str
    sidecar: dict[str, Any] | None = None


class LocalDeclaredSourceAdapter:
    """Read only source files declared by an acquisition plan.

    This adapter intentionally supports only local repo-controlled reads:
    vault files, log files, quarantine/manual artifacts, browser artifacts that
    already exist as files, and prior run/archive refs. It does not call live
    connectors, network APIs, browser automation, MCP, delivery adapters, or
    schedulers.
    """

    adapter_id = "local_declared_source"

    def acquire(self, plan: AcquisitionPlan, vault_root: Path) -> list[AcquiredSource]:
        root = vault_root.resolve()
        acquired: list[AcquiredSource] = []
        for source in plan.sources:
            content = self._read_text(source.path, root)
            sidecar = self._read_sidecar(source.sidecar_path, root) if source.sidecar_path else None
            acquired.append(AcquiredSource(source=source, content=content, sidecar=sidecar))
        return acquired

    def _resolve_existing_file(self, rel_path: str, root: Path) -> Path:
        full_path = (root / rel_path).resolve()
        if not path_is_relative_to(full_path, root):
            raise AcquisitionValidationError(f"declared source resolves outside vault root: {rel_path}")
        if not full_path.exists() or not full_path.is_file():
            raise AcquisitionValidationError(f"declared source does not exist or is not a file: {rel_path}")
        if full_path.suffix.lower() not in READABLE_SUFFIXES:
            raise AcquisitionValidationError(f"unsupported declared source file type: {rel_path}")
        return full_path

    def _read_text(self, rel_path: str, root: Path) -> str:
        full_path = self._resolve_existing_file(rel_path, root)
        return full_path.read_text(encoding="utf-8", errors="replace")

    def _read_sidecar(self, rel_path: str, root: Path) -> dict[str, Any]:
        full_path = self._resolve_existing_file(rel_path, root)
        if full_path.suffix.lower() != ".json":
            raise AcquisitionValidationError(f"sidecar must be JSON: {rel_path}")
        try:
            loaded = json.loads(full_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise AcquisitionValidationError(f"sidecar is not valid JSON: {rel_path}") from exc
        if not isinstance(loaded, dict):
            raise AcquisitionValidationError(f"sidecar JSON must be an object: {rel_path}")
        return loaded
