"""Safe path-based workspace mode inference."""

from __future__ import annotations

from pathlib import Path


PATH_MODE_RULES: tuple[tuple[str, str], ...] = (
    ("01_PROJECTS/University/", "study_research"),
    ("01_PROJECTS/ChaseOS/", "runtime_agent_ops"),
    ("01_PROJECTS/", "founder_venture"),
    ("06_AGENTS/", "runtime_agent_ops"),
    ("runtime/", "runtime_agent_ops"),
    ("04_SOPS/", "business_ops"),
    ("00_HOME/", "personal_os"),
    ("07_LOGS/Build-Logs/", "runtime_agent_ops"),
    ("07_LOGS/Agent-Activity/", "runtime_agent_ops"),
    ("02_KNOWLEDGE/Business-Ops/", "business_ops"),
)


def normalize_workspace_path(path: str | Path, *, vault_root: str | Path | None = None) -> str:
    raw_path = Path(path)
    if vault_root is not None:
        try:
            raw_path = raw_path.resolve().relative_to(Path(vault_root).resolve())
        except (OSError, ValueError):
            pass
    normalized = str(raw_path).replace("\\", "/").lstrip("./").rstrip("/")
    if not normalized:
        return ""
    last_part = normalized.rsplit("/", 1)[-1]
    if "." in last_part:
        return normalized
    return normalized + "/"


def infer_workspace_mode(path: str | Path, *, vault_root: str | Path | None = None) -> str:
    """Infer a workspace mode from a path, returning unknown when uncertain."""

    normalized = normalize_workspace_path(path, vault_root=vault_root)
    for prefix, mode in PATH_MODE_RULES:
        if normalized == prefix or normalized.startswith(prefix):
            return mode
    return "unknown"


def is_runtime_mode_path(path: str | Path, *, vault_root: str | Path | None = None) -> bool:
    return infer_workspace_mode(path, vault_root=vault_root) == "runtime_agent_ops"
