"""Vault-root path policy helpers for AOR workflow enforcement."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable


class AORPathPolicyError(ValueError):
    """Raised when an AOR path declaration exceeds the current repo boundary."""


_DECLARED_AT_RUNTIME_MARKERS = (
    "(declared at runtime)",
    "declared at runtime",
)


def is_runtime_declared_placeholder(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return any(marker in text for marker in _DECLARED_AT_RUNTIME_MARKERS)


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def normalize_vault_relative_path(
    path_raw: Any,
    field_name: str,
    *,
    allow_current_dir: bool = False,
) -> str:
    """Return a normalized vault-relative path or raise AORPathPolicyError.

    AOR executable read/write declarations are vault-root relative. Absolute,
    drive-qualified, and parent-traversal paths are blocked before execution.
    """
    raw = str(path_raw or "").strip()
    if not raw:
        raise AORPathPolicyError(f"{field_name} must be a non-empty vault-relative path")

    normalized = raw.replace("\\", "/").strip()
    if normalized == ".":
        if allow_current_dir:
            return "."
        raise AORPathPolicyError(f"{field_name} may not target the vault root directly")

    windows_path = PureWindowsPath(raw)
    posix_path = PurePosixPath(normalized)
    if windows_path.drive or windows_path.root or posix_path.is_absolute():
        raise AORPathPolicyError(f"{field_name} must be relative to the vault root")

    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        if allow_current_dir:
            return "."
        raise AORPathPolicyError(f"{field_name} may not target the vault root directly")
    if any(part == ".." for part in parts):
        raise AORPathPolicyError(f"{field_name} may not leave the vault root")

    result = "/".join(parts)
    if normalized.endswith("/"):
        result += "/"
    return result


def validate_vault_relative_path_list(
    paths: Any,
    field_name: str,
    *,
    allow_current_dir: bool = False,
    skip_runtime_placeholders: bool = False,
) -> list[str]:
    if not isinstance(paths, list):
        raise AORPathPolicyError(f"{field_name} must be a list")

    normalized: list[str] = []
    for index, item in enumerate(paths):
        item_field = f"{field_name}[{index}]"
        if skip_runtime_placeholders and is_runtime_declared_placeholder(item):
            continue
        normalized.append(
            normalize_vault_relative_path(
                item,
                item_field,
                allow_current_dir=allow_current_dir,
            )
        )
    return normalized


def resolve_vault_relative_path(vault_root: Path, path_raw: Any, field_name: str) -> Path:
    root = vault_root.resolve()
    normalized = normalize_vault_relative_path(path_raw, field_name)
    resolved = (root / normalized).resolve()
    if not path_is_relative_to(resolved, root):
        raise AORPathPolicyError(f"{field_name} resolves outside the vault root")
    return resolved


def path_within_any_target(path: Any, targets: Iterable[Any]) -> bool:
    try:
        normalized_path = normalize_vault_relative_path(path, "path")
    except AORPathPolicyError:
        return False

    for target in targets:
        try:
            normalized_target = normalize_vault_relative_path(target, "target")
        except AORPathPolicyError:
            continue
        base = normalized_target.rstrip("/")
        candidate = normalized_path.rstrip("/") if normalized_path.endswith("/") else normalized_path
        if candidate == base or candidate.startswith(base + "/"):
            return True
    return False


def resolved_path_within_any_target(
    resolved_path: Path,
    vault_root: Path,
    targets: Iterable[Any],
) -> bool:
    root = vault_root.resolve()
    if not path_is_relative_to(resolved_path, root):
        return False

    for target in targets:
        try:
            target_path = resolve_vault_relative_path(root, target, "target")
        except AORPathPolicyError:
            continue
        if resolved_path == target_path or path_is_relative_to(resolved_path, target_path):
            return True
    return False


def validate_repo_scope(repo_scope: Any, field_name: str = "repo_scope") -> None:
    """Validate the current AOR repo-scope contract.

    Current executable AOR writeback is vault-root only. A manifest may name the
    primary repo as ".". Cross-repo access must carry an explicit policy reference
    before future evaluators can interpret it, and extra directories remain
    non-executable until that evaluator exists.
    """
    if repo_scope is None:
        return
    if not isinstance(repo_scope, dict):
        raise AORPathPolicyError(f"{field_name} must be a mapping")

    primary_repo = repo_scope.get("primary_repo", ".")
    primary = normalize_vault_relative_path(
        primary_repo,
        f"{field_name}.primary_repo",
        allow_current_dir=True,
    )
    if primary != ".":
        raise AORPathPolicyError(
            f"{field_name}.primary_repo must be '.' for current vault-root-only AOR execution"
        )

    extra_dirs = repo_scope.get("extra_dirs") or []
    if not isinstance(extra_dirs, list):
        raise AORPathPolicyError(f"{field_name}.extra_dirs must be a list")

    cross_repo_access = bool(
        repo_scope.get("cross_repo_access", repo_scope.get("cross_repo_edits_allowed", False))
    )
    policy_ref = repo_scope.get("policy_ref") or repo_scope.get("policy_path")

    if cross_repo_access and not policy_ref:
        raise AORPathPolicyError(
            f"{field_name}.cross_repo_access requires policy_ref or policy_path"
        )
    if extra_dirs:
        raise AORPathPolicyError(
            f"{field_name}.extra_dirs is not executable yet; current AOR writes are vault-root-only"
        )
