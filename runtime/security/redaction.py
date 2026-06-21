"""
runtime/security/redaction.py — shared secret redaction for output surfaces.

Single source of truth so every surface that echoes captured, external, or
subprocess output redacts credential material the same way. Previously the
terminal adapter, session exports, and the n8n executor each redacted (or
failed to redact) independently — the terminal set was weaker (no Bearer
tokens, `=` only) and the n8n response was not redacted at all.

These are HIGH-PRECISION patterns: tuned to catch credential-like material
without shredding ordinary output. They are intentionally narrower than the
repo secret-audit scanner (which scans source files with placeholder
filtering); this module redacts live runtime output.

Public API:
    redact(text)                  -> str
    redact_text(text, counter)    -> str           (mutates counter by pattern name)
    redact_obj(value, counter)    -> value          (recurses dict/list/str)
    redact_with_report(value)     -> (value, RedactionReport)
    REDACTED, REDACTION_PATTERNS, RedactionReport
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

REDACTED = "[REDACTED]"

# (name, pattern). Order matters for *attribution* only (all redactions collapse
# to the same token): more-specific keys precede their generic supersets so the
# report counts them correctly — e.g. anthropic `sk-ant-` before openai `sk-`.
REDACTION_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "key_value_secret",
        re.compile(
            r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret"
            r"|token|password|passwd|secret)\s*[=:]\s*['\"]?[^'\"\s,;]+"
        ),
    ),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9_\-]{12,}")),
    ("xai_key", re.compile(r"xai-[A-Za-z0-9]{40,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("google_oauth_token", re.compile(r"ya29\.[0-9A-Za-z_\-]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
)


@dataclass(frozen=True)
class RedactionReport:
    applied: bool
    total_redactions: int
    by_pattern: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "total_redactions": self.total_redactions,
            "by_pattern": dict(self.by_pattern),
        }


def redact_text(text: str, counter: dict[str, int] | None = None) -> str:
    """Redact secret-like substrings, tallying hits per pattern in ``counter``."""
    if not text:
        return text
    if counter is None:
        counter = {}
    result = text
    for name, pattern in REDACTION_PATTERNS:
        def _sub(_match: "re.Match[str]", _name: str = name) -> str:
            counter[_name] = counter.get(_name, 0) + 1
            return REDACTED

        result = pattern.sub(_sub, result)
    return result


def redact_obj(value: Any, counter: dict[str, int] | None = None) -> Any:
    """Recursively redact strings inside dicts/lists; other types pass through."""
    if counter is None:
        counter = {}
    if isinstance(value, str):
        return redact_text(value, counter)
    if isinstance(value, dict):
        return {k: redact_obj(v, counter) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_obj(v, counter) for v in value]
    return value


def redact(text: str) -> str:
    """Convenience: redact a string without a report."""
    return redact_text(text, {})


def redact_with_report(value: Any) -> tuple[Any, RedactionReport]:
    """Redact a string or nested structure and return a RedactionReport."""
    counter: dict[str, int] = {}
    redacted = redact_obj(value, counter)
    total = sum(counter.values())
    return redacted, RedactionReport(applied=total > 0, total_redactions=total, by_pattern=dict(counter))
