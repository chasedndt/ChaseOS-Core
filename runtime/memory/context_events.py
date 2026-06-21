"""Context event schema for the ChaseOS Context Memory Core."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from runtime.common.evidence import EvidenceRef, now_utc


CONTEXT_EVENT_TYPES = {
    "observation",
    "correction",
    "decision",
    "preference",
    "feedback",
    "project_state",
    "schedule",
    "runtime_reflection",
}


@dataclass
class ContextEvent:
    event_id: str
    event_type: str
    summary: str
    source_path: str
    source_type: str
    observed_at: str = field(default_factory=now_utc)
    actors: list[str] = field(default_factory=list)
    related_nodes: list[str] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    trust_label: str = "unverified"
    canonical_writeback_target: str | None = None
    writeback_allowed: bool = False

    def validate(self) -> None:
        if not self.event_id:
            raise ValueError("event_id is required")
        if self.event_type not in CONTEXT_EVENT_TYPES:
            raise ValueError(f"event_type must be one of {sorted(CONTEXT_EVENT_TYPES)}")
        if not self.summary:
            raise ValueError("summary is required")
        if not self.source_path:
            raise ValueError("source_path is required")
        if not self.source_type:
            raise ValueError("source_type is required")
        if self.canonical_writeback_target and not self.writeback_allowed:
            raise ValueError("canonical writeback targets require explicit writeback_allowed")
        for item in self.evidence:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
