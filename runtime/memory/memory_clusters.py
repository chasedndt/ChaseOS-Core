"""Memory cluster schema for Context Memory Core."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from runtime.common.evidence import EvidenceRef, now_utc


MEMORY_CLUSTER_STATUSES = {"candidate", "active", "archived", "rejected"}


@dataclass
class MemoryCluster:
    cluster_id: str
    label: str
    description: str = ""
    memory_ids: list[str] = field(default_factory=list)
    related_projects: list[str] = field(default_factory=list)
    related_agents: list[str] = field(default_factory=list)
    related_card_types: list[str] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    status: str = "candidate"
    created_at: str = field(default_factory=now_utc)
    updated_at: str = field(default_factory=now_utc)
    canonical_writeback_enabled: bool = False

    def validate(self) -> None:
        if not self.cluster_id:
            raise ValueError("cluster_id is required")
        if not self.label:
            raise ValueError("label is required")
        if self.status not in MEMORY_CLUSTER_STATUSES:
            raise ValueError(f"status must be one of {sorted(MEMORY_CLUSTER_STATUSES)}")
        if self.canonical_writeback_enabled:
            raise ValueError("memory clusters cannot enable canonical writeback")
        for item in self.evidence:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
