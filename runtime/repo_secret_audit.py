"""Repo-safe secret audit classifier.

This scanner is intentionally conservative about what it reads and what it
emits: private environment files are skipped, known sentinels/placeholders are
reported separately from high-confidence live secrets, and raw matched values are
never included in the returned payload.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SURFACE = "repo_safe_secret_audit"
MODEL_VERSION = "chaseos.repo_secret_audit.v1"

_PRIVATE_ENV_NAMES = {".env", ".env.local", ".envrc"}
_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".pytest_tmp_env",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}
_TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".env.example",
    ".example",
    ".htm",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
    # Key/cert material (PEM blocks). *.key/*.pem are gitignored, but local copies
    # accidentally placed in the tree should still be flagged (defense-in-depth).
    ".pem",
    ".key",
    ".crt",
    ".cer",
    ".pkcs8",
}
# Common extensionless private-key filenames (id_rsa, etc.) — scanned for PEM blocks.
_KEY_FILENAMES = {"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"}
_PLACEHOLDER_MARKERS = (
    "YOUR_",
    "_HERE",
    "PLACEHOLDER",
    "REPLACE_ME",
    "EXAMPLE",
    "DUMMY",
    "FAKE",
    "TODO",
    "<",
    ">",
    "${",
)
_ALLOWLISTED_SENTINELS = frozenset(
    {
        "secret-token",
        "sentinel-token",
        "dummy-token",
        "example-token",
        "not-a-secret",
        "test-token",
    }
)


@dataclass(frozen=True)
class SecretPattern:
    provider: str
    pattern: re.Pattern[str]
    evidence_type: str


_SECRET_PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern("perplexity", re.compile(r"\bpplx-[A-Za-z0-9_-]{20,}\b"), "perplexity_api_key"),
    SecretPattern("perplexity", re.compile(r"\bpplx-[A-Z0-9_]*(?:YOUR|PLACEHOLDER|KEY|TOKEN|HERE)[A-Z0-9_]*\b"), "perplexity_api_key_placeholder"),
    SecretPattern("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), "anthropic_api_key"),
    SecretPattern("openai", re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b"), "openai_api_key"),
    SecretPattern("xai", re.compile(r"\bxai-[A-Za-z0-9]{40,}\b"), "xai_api_key"),
    SecretPattern("github", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{32,}\b"), "github_token"),
    SecretPattern("gitlab", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), "gitlab_token"),
    SecretPattern("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "slack_token"),
    SecretPattern("google", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "google_api_key"),
    SecretPattern("google", re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}\b"), "google_oauth_token"),
    SecretPattern("telegram", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), "telegram_bot_token"),
    SecretPattern(
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
        "private_key_block",
    ),
    SecretPattern(
        "discord",
        re.compile(r"https://discord(?:app)?\.com/api/webhooks/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+"),
        "discord_webhook_url",
    ),
)
_SENTINEL_PATTERN = re.compile(r"\b(?:secret-token|sentinel-token|dummy-token|example-token|not-a-secret|test-token)\b", re.IGNORECASE)


def _is_private_env_file(path: Path) -> bool:
    name = path.name
    if name in _PRIVATE_ENV_NAMES:
        return True
    if name.startswith(".env.") and not name.endswith((".example", ".sample", ".template")):
        return True
    return False


def _looks_text_readable(path: Path) -> bool:
    if _is_private_env_file(path):
        return False
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return True
    if path.name.endswith((".env.example", ".env.sample", ".env.template")):
        return True
    if path.name in {"README", "LICENSE", "Dockerfile"}:
        return True
    if path.name in _KEY_FILENAMES:
        return True
    return False


_FileIdentity = tuple[int, int]


def _file_identity(path: Path) -> _FileIdentity | None:
    """Return a same-file identity without opening or reading file contents."""
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return (stat_result.st_dev, stat_result.st_ino)


def _collect_private_env_identities(root: Path) -> set[_FileIdentity]:
    """Collect same-file identities for private env files without reading them."""
    identities: set[_FileIdentity] = set()
    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)
        rel_parts = current_path.relative_to(root).parts if current_path != root else ()
        if len(rel_parts) >= 2 and rel_parts[0] == "core_export" and rel_parts[1] == "reports":
            dirnames[:] = []
            continue
        dirnames[:] = sorted(name for name in dirnames if name not in _EXCLUDED_DIRS)
        for filename in filenames:
            candidate = current_path / filename
            if not _is_private_env_file(candidate):
                continue
            identity = _file_identity(candidate)
            if identity is not None:
                identities.add(identity)
    return identities


def _is_private_env_alias(path: Path, *, private_env_identities: set[_FileIdentity]) -> bool:
    """Return True when a candidate path aliases a private env file.

    The audit must decide this before read_text.  A text-looking symlink such as
    docs/env-link.md -> ../.env, or a hardlink such as docs/env-hardlink.md that
    shares the same device/inode as .env, must be counted as a private-file skip
    instead of being opened through the alias.
    """
    if _is_private_env_file(path):
        return True
    if path.is_symlink():
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return False
        if _is_private_env_file(resolved):
            return True
    identity = _file_identity(path)
    return identity is not None and identity in private_env_identities


def _iter_candidate_files(root: Path) -> Iterable[Path]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        for rel in proc.stdout.splitlines():
            if not rel:
                continue
            parts = Path(rel).parts
            if any(part in _EXCLUDED_DIRS for part in parts[:-1]):
                continue
            if len(parts) >= 2 and parts[0] == "core_export" and parts[1] == "reports":
                continue
            yield root / rel
        return

    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)
        rel_parts = current_path.relative_to(root).parts if current_path != root else ()
        if len(rel_parts) >= 2 and rel_parts[0] == "core_export" and rel_parts[1] == "reports":
            dirnames[:] = []
            continue
        dirnames[:] = sorted(name for name in dirnames if name not in _EXCLUDED_DIRS)
        for filename in sorted(filenames):
            yield current_path / filename


def _is_placeholder(value: str) -> bool:
    upper = value.upper()
    if any(marker in upper for marker in _PLACEHOLDER_MARKERS):
        return True
    # A Discord webhook URL whose final token segment is short is a test/example
    # fixture, not a live webhook (real webhook tokens are ~60+ chars). This keeps
    # genuine webhooks flagged while not crying wolf over `.../token` test data.
    low = value.lower()
    if "discord" in low and "/webhooks/" in low:
        token = value.rstrip("/").rsplit("/", 1)[-1]
        if len(token) < 30:
            return True
    return False


def _redacted_preview(value: str, *, provider: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{provider}:redacted:sha256:{digest}"


def _finding(
    *,
    root: Path,
    path: Path,
    line_number: int,
    classification: str,
    provider: str,
    evidence_type: str,
    value: str,
) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "line": line_number,
        "classification": classification,
        "provider": provider,
        "evidence_type": evidence_type,
        "redacted_preview": _redacted_preview(value, provider=provider),
    }


def audit_repo_secrets(root: str | Path, *, max_file_bytes: int = 1_000_000, max_scan_files: int = 5_000) -> dict[str, Any]:
    """Scan a repository without reading private env files or emitting secrets."""
    root_path = Path(root).resolve()
    findings: list[dict[str, Any]] = []
    skipped_private_files: list[str] = []
    skipped_non_text = 0
    skipped_large = 0
    scanned_files = 0
    candidate_file_count = 0
    truncated_by_file_limit = False

    if not root_path.exists():
        return {
            "ok": False,
            "surface": SURFACE,
            "model_version": MODEL_VERSION,
            "error": f"root does not exist: {root_path}",
            "summary": {},
            "findings": [],
            "skipped_private_files": [],
        }

    private_env_identities = _collect_private_env_identities(root_path)

    for path in _iter_candidate_files(root_path):
        candidate_file_count += 1
        if max_scan_files and candidate_file_count > max_scan_files:
            truncated_by_file_limit = True
            break
        rel = path.relative_to(root_path).as_posix()
        if _is_private_env_alias(path, private_env_identities=private_env_identities):
            skipped_private_files.append(rel)
            continue
        if path.is_symlink():
            skipped_non_text += 1
            continue
        if not _looks_text_readable(path):
            skipped_non_text += 1
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                skipped_large += 1
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped_non_text += 1
            continue
        scanned_files += 1
        for line_number, line in enumerate(text.splitlines(), start=1):
            for sentinel_match in _SENTINEL_PATTERN.finditer(line):
                sentinel = sentinel_match.group(0)
                findings.append(
                    _finding(
                        root=root_path,
                        path=path,
                        line_number=line_number,
                        classification="allowlisted_sentinel",
                        provider="sentinel",
                        evidence_type="known_sentinel_token",
                        value=sentinel,
                    )
                )
            for secret_pattern in _SECRET_PATTERNS:
                for match in secret_pattern.pattern.finditer(line):
                    value = match.group(0)
                    classification = "placeholder_or_doc_reference" if _is_placeholder(value) else "high_confidence_secret"
                    findings.append(
                        _finding(
                            root=root_path,
                            path=path,
                            line_number=line_number,
                            classification=classification,
                            provider=secret_pattern.provider,
                            evidence_type=secret_pattern.evidence_type,
                            value=value,
                        )
                    )

    high_confidence_count = sum(1 for item in findings if item["classification"] == "high_confidence_secret")
    placeholder_count = sum(
        1 for item in findings if item["classification"] in {"placeholder_or_doc_reference", "allowlisted_sentinel"}
    )
    return {
        "ok": high_confidence_count == 0,
        "surface": SURFACE,
        "model_version": MODEL_VERSION,
        "root": str(root_path),
        "boundary": {
            "read_only": True,
            "private_env_values_read": False,
            "raw_secret_values_emitted": False,
            "canonical_mutation_performed": False,
        },
        "summary": {
            "scanned_file_count": scanned_files,
            "candidate_file_count": candidate_file_count,
            "truncated_by_file_limit": truncated_by_file_limit,
            "max_scan_files": max_scan_files,
            "finding_count": len(findings),
            "high_confidence_secret_count": high_confidence_count,
            "placeholder_or_doc_reference_count": placeholder_count,
            "private_file_skipped_count": len(skipped_private_files),
            "non_text_file_skipped_count": skipped_non_text,
            "large_file_skipped_count": skipped_large,
        },
        "findings": findings,
        "skipped_private_files": skipped_private_files,
        "allowlisted_sentinel_count": len(_ALLOWLISTED_SENTINELS),
    }


def format_repo_secret_audit(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "Repo-safe secret audit:",
        f"  ok: {report.get('ok')}",
        f"  scanned_files: {summary.get('scanned_file_count', 0)}",
        f"  high_confidence_secret_count: {summary.get('high_confidence_secret_count', 0)}",
        f"  placeholder_or_doc_reference_count: {summary.get('placeholder_or_doc_reference_count', 0)}",
        f"  private_file_skipped_count: {summary.get('private_file_skipped_count', 0)}",
        "  raw_secret_values_emitted: False",
    ]
    for finding in (report.get("findings") or [])[:20]:
        lines.append(
            "  - {classification} {provider} {evidence_type} at {path}:{line} ({redacted_preview})".format(**finding)
        )
    return "\n".join(lines)
