"""Sanitizer rewrite modes for ChaseOS Core export previews."""

from __future__ import annotations

import re


class CoreExportSanitizerError(ValueError):
    """Raised when a sanitizer cannot be applied safely."""


_LOCAL_PATH_RE = re.compile(r"(?:/mnt/[a-z]/Users/[^\s)\]>'\"]+|[A-Z]:\\\\Users\\\\[^\s)\]>'\"]+)", re.IGNORECASE)
_FOLDER_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    # Genericize personal-instance operational folder references in public docs
    # into framework-example paths (the sanitizer's stated job). Order does not
    # matter — these prefixes do not overlap.
    ("00_HOME/", "docs/framework-home/"),
    ("07_LOGS/", "docs/framework-logs/"),
    ("runtime/agent_bus/", "runtime/agent-bus-example/"),
    (".chaseos/", "config/chaseos-example/"),
)
_RUNTIME_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("discord_instance_bindings.yaml", "control_surface_bindings.example.yaml"),
    ("openclaw.json", "runtime-adapter.example.json"),
)


def framework_docs_v1(text: str) -> str:
    """Rewrite mixed personal-instance doc text into public-Core-safe preview text.

    This is intentionally conservative and deterministic. It removes local path
    specificity and converts private ChaseOS folder references into framework
    example paths so the scanner can validate the rendered output rather than
    approving the live source text directly.
    """

    rewritten = _LOCAL_PATH_RE.sub("[LOCAL_PATH_REDACTED]", text)
    for old, new in _FOLDER_REPLACEMENTS:
        rewritten = rewritten.replace(old, new)
    for old, new in _RUNTIME_REPLACEMENTS:
        rewritten = rewritten.replace(old, new)
    return rewritten


_SANITIZERS = {
    "framework_docs_v1": framework_docs_v1,
}


def apply_sanitizer(name: str, text: str) -> str:
    try:
        sanitizer = _SANITIZERS[name]
    except KeyError as exc:
        raise CoreExportSanitizerError(f"unknown sanitizer: {name}") from exc
    return sanitizer(text)
