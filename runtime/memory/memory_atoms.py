"""Memory atom schema for governed ChaseOS memory candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from runtime.common.evidence import EvidenceRef, now_utc


MEMORY_LAYERS = {"A", "B", "C", "D", "E"}
MEMORY_STATUSES = {"candidate", "accepted", "rejected", "stale"}
PROMOTION_STATES = {"none", "candidate", "reviewed", "promoted"}


@dataclass
class MemoryAtom:
    atom_id: str
    layer: str
    scope: str
    content: str
    created_at: str = field(default_factory=now_utc)
    status: str = "candidate"
    promotion_state: str = "none"
    evidence: list[EvidenceRef] = field(default_factory=list)
    related_event_ids: list[str] = field(default_factory=list)
    related_node_ids: list[str] = field(default_factory=list)
    canonical_writeback_enabled: bool = False

    def validate(self) -> None:
        if not self.atom_id:
            raise ValueError("atom_id is required")
        if self.layer not in MEMORY_LAYERS:
            raise ValueError(f"layer must be one of {sorted(MEMORY_LAYERS)}")
        if not self.scope:
            raise ValueError("scope is required")
        if not self.content:
            raise ValueError("content is required")
        if self.status not in MEMORY_STATUSES:
            raise ValueError(f"status must be one of {sorted(MEMORY_STATUSES)}")
        if self.promotion_state not in PROMOTION_STATES:
            raise ValueError(f"promotion_state must be one of {sorted(PROMOTION_STATES)}")
        if self.canonical_writeback_enabled and self.promotion_state != "promoted":
            raise ValueError("canonical writeback requires promoted state")
        for item in self.evidence:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
