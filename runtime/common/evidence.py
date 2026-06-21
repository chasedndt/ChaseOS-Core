"""
runtime/common/evidence.py — Core evidence primitives (dependency-free).

Hosts the small, widely-shared evidence/time primitives so Core modules (e.g.
``runtime.memory``) do not depend on ``runtime.pulse`` (a non-Core module).
``runtime.pulse.card_schema`` re-exports these for backward compatibility, so
existing consumers are unaffected.

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def now_utc() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EvidenceRef:
    """A reference to a piece of source evidence backing a claim or atom."""

    source_path: str
    source_type: str
    summary: str
    trust_label: str = "unverified"
    observed_at: str | None = None
    quote: str | None = None
    source_url: str | None = None

    def validate(self) -> None:
        if not self.source_path:
            raise ValueError("evidence.source_path is required")
        if not self.source_type:
            raise ValueError("evidence.source_type is required")
        if not self.summary:
            raise ValueError("evidence.summary is required")
