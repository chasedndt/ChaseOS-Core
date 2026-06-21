"""Privacy scanner for ChaseOS Core export candidates."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_BUILTIN_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "local_paths",
        re.compile(r"(?:/mnt/[a-z]/Users/[^\s)\]>'\"]+|[A-Z]:\\\\Users\\\\[^\s)\]>'\"]+)", re.IGNORECASE),
        "local Windows/WSL user path found",
    ),
    (
        "discord_or_webhook",
        re.compile(r"discord(?:app)?\.com/api/webhooks/[^\s)>'\"]+|[0-9]{17,20}", re.IGNORECASE),
        "Discord/webhook-like value found",
    ),
    (
        "secret_value_like",
        re.compile(
            r"(?<![A-Za-z0-9_-])(?:sk-proj-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|xai-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|ya29\.[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_-])",
            re.IGNORECASE,
        ),
        "secret-like credential value found",
    ),
    (
        # `/home/<user>/` and macOS `/Users/<user>/`. The `/Users/` branch uses a
        # negative lookbehind so it does NOT double-match the WSL `/mnt/<drive>/Users/`
        # form already covered by `local_paths` above.
        "posix_home_path",
        re.compile(r"(?:/home/[A-Za-z0-9._-]+/|(?<!/mnt/[a-z])/Users/[A-Za-z0-9._-]+/)", re.IGNORECASE),
        "POSIX/macOS personal home path found",
    ),
    (
        "private_key_block",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
        "private key material found",
    ),
    # NOTE: a generic keyword-based `credential_assignment` heuristic was evaluated
    # and rejected for this BLOCKING scanner — over a Python codebase it
    # false-positives on function calls (`api_key = _get_api_key(...)`), config
    # keys, and even this pattern file itself. Deeper credential/PII coverage
    # (entropy, placeholder-aware) belongs in `runtime/repo_secret_audit.py`,
    # which should be wired into the export verify step (follow-up).
)


def scan_text(text: str, *, display_path: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for family, pattern, message in _BUILTIN_PATTERNS:
            if pattern.search(line):
                findings.append(
                    {
                        "file": display_path,
                        "line": line_number,
                        "pattern_family": family,
                        "message": message,
                    }
                )
    return findings


def scan_text_file(path: Path, *, display_path: str) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [
            {
                "file": display_path,
                "line": None,
                "pattern_family": "binary_or_non_utf8",
                "message": "candidate file is not UTF-8 text",
            }
        ]
    return scan_text(text, display_path=display_path)


def scan_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    blocking_findings: list[dict[str, Any]] = []
    for candidate in candidates:
        rendered_text = candidate.get("preview_text")
        if isinstance(rendered_text, str):
            blocking_findings.extend(scan_text(rendered_text, display_path=str(candidate.get("target", candidate.get("source", "<rendered>")))))
            continue
        source_path = candidate.get("source_path")
        if not isinstance(source_path, Path):
            continue
        if source_path.is_file():
            blocking_findings.extend(scan_text_file(source_path, display_path=str(candidate.get("source", source_path))))
    return {
        "ok": not blocking_findings,
        "blocking_findings": blocking_findings,
        "blocking_count": len(blocking_findings),
    }
