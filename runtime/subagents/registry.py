"""Filesystem-backed sub-agent preset registry."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from runtime.workspace_modes.loader import parse_profile_text

from .models import SubAgentPreset, SubAgentValidationError


DEFAULT_PRESET_ROOT = Path("subagents") / "presets"


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SubAgentValidationError("sub-agent preset markdown has no YAML frontmatter")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise SubAgentValidationError("sub-agent preset frontmatter is not closed")


def load_preset_file(path: str | Path, *, vault_root: str | Path | None = None) -> SubAgentPreset:
    preset_path = Path(path)
    text = preset_path.read_text(encoding="utf-8")
    if preset_path.suffix.lower() not in {".md", ".markdown"}:
        raise SubAgentValidationError(f"sub-agent preset must be markdown: {preset_path}")
    frontmatter, body = _split_frontmatter(text)
    try:
        data = parse_profile_text(frontmatter)
    except ValueError as exc:
        raise SubAgentValidationError(f"failed to parse preset frontmatter: {preset_path}") from exc
    source_path = str(preset_path)
    if vault_root is not None:
        try:
            source_path = str(preset_path.relative_to(Path(vault_root)))
        except ValueError:
            source_path = str(preset_path)
    return SubAgentPreset.from_mapping(data, instructions=body, source_path=source_path)


def default_preset_root(vault_root: str | Path | None = None) -> Path:
    if vault_root is None:
        return DEFAULT_PRESET_ROOT
    return Path(vault_root) / DEFAULT_PRESET_ROOT


class SubAgentRegistry:
    """Loads built-in and user-defined preset markdown files without executing them."""

    def __init__(
        self,
        *,
        vault_root: str | Path | None = None,
        preset_roots: Iterable[str | Path] | None = None,
    ) -> None:
        self.vault_root = Path(vault_root) if vault_root is not None else Path.cwd()
        roots = tuple(Path(root) for root in preset_roots) if preset_roots is not None else (
            default_preset_root(self.vault_root),
        )
        self.preset_roots = roots

    def iter_preset_paths(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        for root in self.preset_roots:
            if not root.exists():
                continue
            paths.extend(sorted(path for path in root.rglob("*.md") if path.is_file()))
        return tuple(paths)

    def list_presets(self) -> tuple[SubAgentPreset, ...]:
        presets = [
            load_preset_file(path, vault_root=self.vault_root)
            for path in self.iter_preset_paths()
        ]
        return tuple(sorted(presets, key=lambda preset: preset.id))

    def get_preset(self, preset_id: str) -> SubAgentPreset:
        for preset in self.list_presets():
            if preset.id == preset_id:
                return preset
        raise KeyError(f"unknown sub-agent preset: {preset_id}")

    def validate_all(self) -> tuple[str, ...]:
        errors: list[str] = []
        seen_ids: set[str] = set()
        for path in self.iter_preset_paths():
            try:
                preset = load_preset_file(path, vault_root=self.vault_root)
            except (OSError, SubAgentValidationError) as exc:
                errors.append(f"{path}: {exc}")
                continue
            if preset.id in seen_ids:
                errors.append(f"{path}: duplicate preset id {preset.id!r}")
            seen_ids.add(preset.id)
        return tuple(errors)
