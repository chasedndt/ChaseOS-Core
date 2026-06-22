"""First-wave Acquisition + Normalization artifact objects."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SourcePacket:
    artifact_id: str
    artifact_type: str
    schema_version: str
    created_at: str
    owner_layer: str
    owning_workflow: str
    objective: dict[str, Any]
    acquirer: dict[str, Any]
    scope: dict[str, Any]
    promotion: dict[str, Any]
    audit: dict[str, Any]
    source_id: str
    source_class: str
    source_origin: dict[str, Any]
    acquisition_method: str
    provenance: dict[str, Any]
    trust_evaluation: dict[str, Any]
    freshness: dict[str, Any]
    transformation_chain: list[dict[str, Any]]
    raw_pointer: dict[str, Any]
    content_sha256: str
    normalized_text: str
    sidecar: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedSourcePack:
    artifact_id: str
    artifact_type: str
    schema_version: str
    created_at: str
    owner_layer: str
    owning_workflow: str
    objective: dict[str, Any]
    acquirer: dict[str, Any]
    scope: dict[str, Any]
    promotion: dict[str, Any]
    audit: dict[str, Any]
    items: list[dict[str, Any]]
    source_packet_refs: list[str]
    source_packet_count: int
    trust_summary: dict[str, Any]
    freshness_summary: dict[str, Any]
    transformation_chain: list[dict[str, Any]]
    excluded_sources: list[str] = field(default_factory=list)
    known_gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BriefingReadyInputSet:
    artifact_id: str
    artifact_type: str
    schema_version: str
    created_at: str
    owner_layer: str
    owning_workflow: str
    objective: dict[str, Any]
    acquirer: dict[str, Any]
    scope: dict[str, Any]
    promotion: dict[str, Any]
    audit: dict[str, Any]
    normalized_source_pack_ref: str
    sections: dict[str, list[dict[str, Any]]]
    trust_summary: dict[str, Any]
    freshness_summary: dict[str, Any]
    actionability: dict[str, Any]
    source_refs: list[str]
    transformation_chain: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
