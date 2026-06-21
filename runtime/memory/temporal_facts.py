"""Temporal fact schema for Context Memory Core."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from runtime.common.evidence import EvidenceRef, now_utc


TEMPORAL_FACT_STATUSES = {"candidate", "current", "expired", "superseded", "rejected"}


@dataclass
class TemporalFact:
    fact_id: str
    summary: str
    valid_from: str
    valid_until: str | None = None
    source_event_ids: list[str] = field(default_factory=list)
    related_node_ids: list[str] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "candidate"
    created_at: str = field(default_factory=now_utc)
    canonical_writeback_enabled: bool = False

    def validate(self) -> None:
        if not self.fact_id:
            raise ValueError("fact_id is required")
        if not self.summary:
            raise ValueError("summary is required")
        if not self.valid_from:
            raise ValueError("valid_from is required")
        if self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until cannot be before valid_from")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        if self.status not in TEMPORAL_FACT_STATUSES:
            raise ValueError(f"status must be one of {sorted(TEMPORAL_FACT_STATUSES)}")
        if self.canonical_writeback_enabled:
            raise ValueError("temporal facts cannot enable canonical writeback")
        for item in self.evidence:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
