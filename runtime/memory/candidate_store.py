"""Pending-review candidate store for Context Memory Core.

The store writes append-only review candidates under the Pulse log tree. Initial
imports create candidates only; later review/edit helpers append new candidate
states with history. Live apply is intentionally implemented separately in the
Personal Map governed apply lane.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from runtime.memory.personal_map import PersonalMapEdge, PersonalMapNode
from runtime.common.evidence import EvidenceRef, now_utc

MEMORY_CANDIDATE_ROOT = Path("07_LOGS") / "Pulse-Decks" / "memory-candidates"
PERSONAL_MAP_CANDIDATE_ROOT = MEMORY_CANDIDATE_ROOT / "personal-map"
PENDING_REVIEW = "pending_review"
APPROVED = "approved"
REJECTED = "rejected"
DEFERRED = "deferred"
STALE = "stale"
ESCALATED = "escalated"
APPLIED = "applied"
PERSONAL_MAP_CANDIDATE_TYPES = {"node", "edge"}
PERSONAL_MAP_CANDIDATE_STATUSES = {
    PENDING_REVIEW,
    APPROVED,
    REJECTED,
    DEFERRED,
    STALE,
    ESCALATED,
    APPLIED,
}
PERSONAL_MAP_BLOCKED_EFFECTS = (
    "personal_map_mutation",
    "memory_approval",
    "task_creation",
    "project_file_mutation",
    "knowledge_promotion",
    "canonical_writeback",
    "second_datastore_write",
)
SECRET_LIKE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|auth[_-]?token|session[_-]?cookie|password)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
)


def _vault_path(vault_root: str | Path) -> Path:
    return Path(vault_root).resolve()


def _assert_inside(child: Path, parent: Path, message: str) -> None:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise ValueError(message) from exc


def _relative_to_vault(vault: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(vault).as_posix()
    except ValueError:
        return str(path.resolve())


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    if not slug or slug in {".", ".."} or ".." in slug:
        raise ValueError("candidate slug is invalid")
    return slug


def _date_slug(created_at: str) -> str:
    candidate = (created_at or now_utc())[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
        return now_utc()[:10]
    return candidate


def _candidate_id(prefix: str, *parts: str) -> str:
    seed = "|".join(parts)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _evidence_from_dict(items: list[Any]) -> list[EvidenceRef]:
    return [item if isinstance(item, EvidenceRef) else EvidenceRef(**item) for item in items]


def _merge_evidence_preserving_existing(
    existing_items: list[Any],
    update_items: list[Any] | None,
) -> list[EvidenceRef]:
    """Merge evidence edits without allowing updates to erase source evidence."""

    merged: list[EvidenceRef] = []
    seen: set[str] = set()
    for item in _evidence_from_dict(existing_items) + _evidence_from_dict(update_items or []):
        key = json.dumps(asdict(item), sort_keys=True, default=str)
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def _node_from_dict(data: dict[str, Any] | PersonalMapNode | None) -> PersonalMapNode | None:
    if data is None or isinstance(data, PersonalMapNode):
        return data
    return PersonalMapNode.from_dict(data)


def _edge_from_dict(data: dict[str, Any] | PersonalMapEdge | None) -> PersonalMapEdge | None:
    if data is None or isinstance(data, PersonalMapEdge):
        return data
    return PersonalMapEdge.from_dict(data)


def _scan_payload_for_secret_like_content(payload: Any) -> dict[str, Any]:
    text = json.dumps(payload, sort_keys=True, default=str)
    matched = [pattern.pattern for pattern in SECRET_LIKE_PATTERNS if pattern.search(text)]
    return {
        "passed": not matched,
        "blocked_secret_like_content": bool(matched),
        "scanner_classes": matched,
    }


def _assert_no_secret_like_content(payload: Any) -> dict[str, Any]:
    scan = _scan_payload_for_secret_like_content(payload)
    if not scan["passed"]:
        raise ValueError("secret-like content is not allowed in Personal Map candidates")
    return scan


@dataclass
class PersonalMapCandidate:
    """A governed Personal Map node or edge update candidate."""

    candidate_id: str
    candidate_type: str
    reason: str
    node: PersonalMapNode | None = None
    edge: PersonalMapEdge | None = None
    source_card_id: str | None = None
    source_feedback_candidate_id: str | None = None
    source_deck_path: str | None = None
    created_at: str = field(default_factory=now_utc)
    status: str = PENDING_REVIEW
    review_required: bool = True
    candidate_only: bool = True
    canonical_writeback_allowed: bool = False
    applied_to_personal_map: bool = False
    mutates_canonical_state: bool = False
    approves_memory: bool = False
    creates_task: bool = False
    second_datastore_write_allowed: bool = False
    confidence: float = 0.5
    data_class: str = "unspecified"
    sensitivity: str = "local_private"
    no_secret_scan: dict[str, Any] = field(default_factory=lambda: {"passed": True})
    status_history: list[dict[str, Any]] = field(default_factory=list)
    revisions: list[dict[str, Any]] = field(default_factory=list)
    reviewer: str | None = None
    reviewed_at: str | None = None

    def validate(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if self.candidate_type not in PERSONAL_MAP_CANDIDATE_TYPES:
            raise ValueError(
                f"candidate_type must be one of {sorted(PERSONAL_MAP_CANDIDATE_TYPES)}"
            )
        if not self.reason:
            raise ValueError("candidate reason is required")
        if self.status not in PERSONAL_MAP_CANDIDATE_STATUSES:
            raise ValueError(f"status must be one of {sorted(PERSONAL_MAP_CANDIDATE_STATUSES)}")
        if self.candidate_type == "node":
            if self.node is None or self.edge is not None:
                raise ValueError("node candidates require exactly one node")
            self.node.validate()
            _assert_no_secret_like_content(asdict(self.node))
        if self.candidate_type == "edge":
            if self.edge is None or self.node is not None:
                raise ValueError("edge candidates require exactly one edge")
            self.edge.validate()
            _assert_no_secret_like_content(asdict(self.edge))
        if not self.review_required:
            raise ValueError("personal map candidates require review")
        if not self.candidate_only:
            raise ValueError("personal map candidates must remain candidate-only until apply")
        if self.canonical_writeback_allowed:
            raise ValueError("personal map candidates cannot allow canonical writeback")
        if self.applied_to_personal_map and self.status != APPLIED:
            raise ValueError("applied_to_personal_map is only valid for applied candidate records")
        if self.mutates_canonical_state:
            raise ValueError("personal map candidates cannot mutate canonical state")
        if self.approves_memory:
            raise ValueError("personal map candidates cannot approve memory")
        if self.creates_task:
            raise ValueError("personal map candidates cannot create tasks directly")
        if self.second_datastore_write_allowed:
            raise ValueError("personal map candidates cannot write a second datastore")
        if not self.no_secret_scan.get("passed", False):
            raise ValueError("personal map candidate must pass no-secret scan")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonalMapCandidate":
        candidate = cls(
            candidate_id=str(data.get("candidate_id") or ""),
            candidate_type=str(data.get("candidate_type") or ""),
            reason=str(data.get("reason") or ""),
            node=_node_from_dict(data.get("node")),
            edge=_edge_from_dict(data.get("edge")),
            source_card_id=data.get("source_card_id"),
            source_feedback_candidate_id=data.get("source_feedback_candidate_id"),
            source_deck_path=data.get("source_deck_path"),
            created_at=str(data.get("created_at") or now_utc()),
            status=str(data.get("status") or PENDING_REVIEW),
            review_required=bool(data.get("review_required", True)),
            candidate_only=bool(data.get("candidate_only", True)),
            canonical_writeback_allowed=bool(data.get("canonical_writeback_allowed", False)),
            applied_to_personal_map=bool(data.get("applied_to_personal_map", False)),
            mutates_canonical_state=bool(data.get("mutates_canonical_state", False)),
            approves_memory=bool(data.get("approves_memory", False)),
            creates_task=bool(data.get("creates_task", False)),
            second_datastore_write_allowed=bool(data.get("second_datastore_write_allowed", False)),
            confidence=float(data.get("confidence", 0.5)),
            data_class=str(data.get("data_class") or "unspecified"),
            sensitivity=str(data.get("sensitivity") or "local_private"),
            no_secret_scan=dict(data.get("no_secret_scan") or {"passed": True}),
            status_history=list(data.get("status_history") or []),
            revisions=list(data.get("revisions") or []),
            reviewer=data.get("reviewer"),
            reviewed_at=data.get("reviewed_at"),
        )
        candidate.validate()
        return candidate


@dataclass
class PersonalMapCandidateArtifact:
    path: str
    candidate_id: str
    candidate_type: str
    status: str = PENDING_REVIEW
    canonical_writeback_allowed: bool = False
    second_datastore_write_allowed: bool = False

    def validate(self) -> None:
        if not self.path:
            raise ValueError("candidate artifact path is required")
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if self.candidate_type not in PERSONAL_MAP_CANDIDATE_TYPES:
            raise ValueError(
                f"candidate_type must be one of {sorted(PERSONAL_MAP_CANDIDATE_TYPES)}"
            )
        if self.status not in PERSONAL_MAP_CANDIDATE_STATUSES:
            raise ValueError("candidate artifact status is invalid")
        if self.canonical_writeback_allowed:
            raise ValueError("candidate artifacts cannot allow canonical writeback")
        if self.second_datastore_write_allowed:
            raise ValueError("candidate artifacts cannot write a second datastore")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass
class PersonalMapCandidateQueue:
    generated_at: str = field(default_factory=now_utc)
    items: list[PersonalMapCandidate] = field(default_factory=list)
    source_log_paths: list[str] = field(default_factory=list)
    queue_status: str = "read_only"
    writes: list[str] = field(default_factory=list)
    canonical_writeback_allowed: bool = False
    second_datastore_write_allowed: bool = False
    blocked_effects: tuple[str, ...] = PERSONAL_MAP_BLOCKED_EFFECTS

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def pending_count(self) -> int:
        return sum(1 for item in self.items if item.status == PENDING_REVIEW)

    def validate(self) -> None:
        if self.queue_status != "read_only":
            raise ValueError("personal map candidate queue is read-only")
        if self.writes:
            raise ValueError("personal map candidate queue cannot declare writes")
        if self.canonical_writeback_allowed:
            raise ValueError("personal map candidate queue cannot allow canonical writeback")
        if self.second_datastore_write_allowed:
            raise ValueError("personal map candidate queue cannot write a second datastore")
        if set(self.blocked_effects) != set(PERSONAL_MAP_BLOCKED_EFFECTS):
            raise ValueError("personal map candidate queue must declare blocked effects")
        for item in self.items:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "generated_at": self.generated_at,
            "queue_status": self.queue_status,
            "item_count": self.item_count,
            "pending_count": self.pending_count,
            "source_log_paths": list(self.source_log_paths),
            "writes": list(self.writes),
            "canonical_writeback_allowed": self.canonical_writeback_allowed,
            "second_datastore_write_allowed": self.second_datastore_write_allowed,
            "blocked_effects": list(self.blocked_effects),
            "items": [item.to_dict() for item in self.items],
        }


def _build_history(status: str, timestamp: str, actor: str | None = None, reason: str | None = None) -> dict[str, Any]:
    payload = {"status": status, "timestamp": timestamp}
    if actor:
        payload["actor"] = actor
    if reason:
        payload["reason"] = reason
    return payload


def build_personal_map_node_candidate(
    node: PersonalMapNode,
    *,
    reason: str,
    source_card_id: str | None = None,
    source_feedback_candidate_id: str | None = None,
    source_deck_path: str | None = None,
    created_at: str | None = None,
    data_class: str = "unspecified",
    confidence: float = 0.5,
    sensitivity: str = "local_private",
) -> PersonalMapCandidate:
    node.validate()
    scan = _assert_no_secret_like_content(asdict(node))
    timestamp = created_at or now_utc()
    candidate = PersonalMapCandidate(
        candidate_id=_candidate_id("personal-map-node-candidate", node.node_id, timestamp, reason),
        candidate_type="node",
        reason=reason,
        node=node,
        source_card_id=source_card_id,
        source_feedback_candidate_id=source_feedback_candidate_id,
        source_deck_path=source_deck_path,
        created_at=timestamp,
        data_class=data_class,
        confidence=confidence,
        sensitivity=sensitivity,
        no_secret_scan=scan,
        status_history=[_build_history(PENDING_REVIEW, timestamp, reason=reason)],
    )
    candidate.validate()
    return candidate


def build_personal_map_edge_candidate(
    edge: PersonalMapEdge,
    *,
    reason: str,
    source_card_id: str | None = None,
    source_feedback_candidate_id: str | None = None,
    source_deck_path: str | None = None,
    created_at: str | None = None,
    data_class: str = "unspecified",
    confidence: float = 0.5,
    sensitivity: str = "local_private",
) -> PersonalMapCandidate:
    edge.validate()
    scan = _assert_no_secret_like_content(asdict(edge))
    timestamp = created_at or now_utc()
    candidate = PersonalMapCandidate(
        candidate_id=_candidate_id("personal-map-edge-candidate", edge.edge_id, timestamp, reason),
        candidate_type="edge",
        reason=reason,
        edge=edge,
        source_card_id=source_card_id,
        source_feedback_candidate_id=source_feedback_candidate_id,
        source_deck_path=source_deck_path,
        created_at=timestamp,
        data_class=data_class,
        confidence=confidence,
        sensitivity=sensitivity,
        no_secret_scan=scan,
        status_history=[_build_history(PENDING_REVIEW, timestamp, reason=reason)],
    )
    candidate.validate()
    return candidate


def personal_map_candidate_log_path(
    vault_root: str | Path,
    *,
    created_at: str | None = None,
) -> Path:
    vault = _vault_path(vault_root)
    root = (vault / PERSONAL_MAP_CANDIDATE_ROOT).resolve()
    path = root / f"{_date_slug(created_at or now_utc())}-personal-map-candidates.jsonl"
    _assert_inside(path, root, "personal map candidate logs must stay inside personal-map/")
    return path


def _append_candidate_record(vault_root: str | Path, candidate: PersonalMapCandidate) -> Path:
    candidate.validate()
    vault = _vault_path(vault_root)
    path = personal_map_candidate_log_path(vault, created_at=candidate.created_at)
    root = (vault / PERSONAL_MAP_CANDIDATE_ROOT).resolve()
    _assert_inside(path, root, "personal map candidate logs must stay inside personal-map/")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(candidate.to_dict(), sort_keys=True))
        handle.write("\n")
    return path


def persist_personal_map_candidate(
    vault_root: str | Path,
    candidate: PersonalMapCandidate,
) -> PersonalMapCandidateArtifact:
    path = _append_candidate_record(vault_root, candidate)
    vault = _vault_path(vault_root)
    artifact = PersonalMapCandidateArtifact(
        path=_relative_to_vault(vault, path),
        candidate_id=candidate.candidate_id,
        candidate_type=candidate.candidate_type,
        status=candidate.status,
        canonical_writeback_allowed=False,
        second_datastore_write_allowed=False,
    )
    artifact.validate()
    return artifact


def load_personal_map_candidates(
    vault_root: str | Path,
    *,
    log_path: str | Path | None = None,
) -> list[PersonalMapCandidate]:
    vault = _vault_path(vault_root)
    root = (vault / PERSONAL_MAP_CANDIDATE_ROOT).resolve()
    if log_path is None:
        paths = sorted(root.glob("*-personal-map-candidates.jsonl")) if root.exists() else []
    else:
        target = Path(log_path)
        target = target if target.is_absolute() else vault / target
        _assert_inside(target, root, "personal map candidate logs must stay inside personal-map/")
        paths = [target]

    latest: dict[str, PersonalMapCandidate] = {}
    order: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        _assert_inside(path, root, "personal map candidate logs must stay inside personal-map/")
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                candidate = PersonalMapCandidate.from_dict(json.loads(line))
                if candidate.candidate_id not in latest:
                    order.append(candidate.candidate_id)
                latest[candidate.candidate_id] = candidate
    return [latest[candidate_id] for candidate_id in order]


def _source_log_paths(vault: Path, log_path: str | Path | None) -> list[str]:
    root = (vault / PERSONAL_MAP_CANDIDATE_ROOT).resolve()
    if log_path is not None:
        target = Path(log_path)
        target = target if target.is_absolute() else vault / target
        return [_relative_to_vault(vault, target)]
    if not root.exists():
        return []
    return [
        _relative_to_vault(vault, path)
        for path in sorted(root.glob("*-personal-map-candidates.jsonl"))
    ]


def build_personal_map_candidate_queue(
    vault_root: str | Path,
    *,
    log_path: str | Path | None = None,
) -> PersonalMapCandidateQueue:
    vault = _vault_path(vault_root)
    items = [
        item
        for item in load_personal_map_candidates(vault, log_path=log_path)
        if item.status == PENDING_REVIEW
    ]
    queue = PersonalMapCandidateQueue(
        items=items,
        source_log_paths=_source_log_paths(vault, log_path),
    )
    queue.validate()
    return queue


def _approved_source_path(vault: Path, source_path: str | Path, approved_sources: list[str | Path]) -> Path:
    target = Path(source_path)
    target = target if target.is_absolute() else vault / target
    target = target.resolve()
    _assert_inside(target, vault, "Personal Map imports require vault-local source paths")
    approved = []
    for source in approved_sources:
        approved_path = Path(source)
        approved_path = approved_path if approved_path.is_absolute() else vault / approved_path
        approved.append(approved_path.resolve())
    if target not in approved:
        raise ValueError("Personal Map import source is not in the approved source allowlist")
    if not target.exists():
        raise ValueError("Personal Map import source does not exist")
    return target


def _source_evidence(vault: Path, source: Path, timestamp: str) -> EvidenceRef:
    return EvidenceRef(
        source_path=_relative_to_vault(vault, source),
        source_type="approved_personal_map_import_source",
        summary="Operator-approved local source for Personal Map candidate import.",
        trust_label="operator_approved_local_source",
        observed_at=timestamp,
    )


def import_personal_map_candidates_from_source(
    vault_root: str | Path,
    source_path: str | Path,
    *,
    approved_sources: list[str | Path],
    data_class: str = "unspecified",
    created_at: str | None = None,
) -> list[PersonalMapCandidateArtifact]:
    """Import JSON node/edge proposals from an approved local vault source.

    Expected JSON shape: {"nodes": [{...}], "edges": [{...}]}. The import is
    candidate-only; it does not approve, apply, or mutate protected docs.
    """

    vault = _vault_path(vault_root)
    created_at = created_at or now_utc()
    source = _approved_source_path(vault, source_path, approved_sources)
    payload = json.loads(source.read_text(encoding="utf-8"))
    _assert_no_secret_like_content(payload)
    evidence = _source_evidence(vault, source, created_at)
    candidates: list[PersonalMapCandidate] = []
    for node_payload in payload.get("nodes", []):
        node_data = dict(node_payload)
        node_data.setdefault("evidence", [])
        node_data["evidence"] = _evidence_from_dict(node_data["evidence"]) + [evidence]
        node = PersonalMapNode.from_dict(node_data)
        candidates.append(
            build_personal_map_node_candidate(
                node,
                reason="Imported from operator-approved local Personal Map source.",
                source_deck_path=_relative_to_vault(vault, source),
                created_at=created_at,
                data_class=data_class,
                confidence=float(node_payload.get("confidence", 0.5)),
            )
        )
    for edge_payload in payload.get("edges", []):
        edge_data = dict(edge_payload)
        edge_data.setdefault("evidence", [])
        edge_data["evidence"] = _evidence_from_dict(edge_data["evidence"]) + [evidence]
        edge = PersonalMapEdge.from_dict(edge_data)
        candidates.append(
            build_personal_map_edge_candidate(
                edge,
                reason="Imported from operator-approved local Personal Map source.",
                source_deck_path=_relative_to_vault(vault, source),
                created_at=created_at,
                data_class=data_class,
                confidence=float(edge_payload.get("confidence", 0.5)),
            )
        )
    return [persist_personal_map_candidate(vault, candidate) for candidate in candidates]


def _load_candidate_or_raise(vault_root: str | Path, candidate_id: str) -> PersonalMapCandidate:
    for candidate in load_personal_map_candidates(vault_root):
        if candidate.candidate_id == candidate_id:
            return candidate
    raise ValueError(f"Personal Map candidate not found: {candidate_id}")


def edit_personal_map_candidate(
    vault_root: str | Path,
    candidate_id: str,
    payload_updates: dict[str, Any],
    *,
    editor: str = "operator",
    edited_at: str | None = None,
) -> PersonalMapCandidate:
    candidate = _load_candidate_or_raise(vault_root, candidate_id)
    if candidate.status != PENDING_REVIEW:
        raise ValueError("only pending Personal Map candidates may be edited")
    edited_at = edited_at or now_utc()
    before = candidate.to_dict()
    if candidate.candidate_type == "node" and candidate.node is not None:
        node_payload = asdict(candidate.node)
        safe_updates = dict(payload_updates)
        candidate_reason = safe_updates.pop("reason", None)
        if "evidence" in safe_updates:
            safe_updates["evidence"] = _merge_evidence_preserving_existing(
                node_payload.get("evidence", []),
                safe_updates.get("evidence"),
            )
        node_payload.update(safe_updates)
        candidate.node = PersonalMapNode.from_dict(node_payload)
        if candidate_reason is not None:
            candidate.reason = str(candidate_reason)
    elif candidate.candidate_type == "edge" and candidate.edge is not None:
        edge_payload = asdict(candidate.edge)
        safe_updates = dict(payload_updates)
        candidate_reason = safe_updates.pop("reason", None)
        if "evidence" in safe_updates:
            safe_updates["evidence"] = _merge_evidence_preserving_existing(
                edge_payload.get("evidence", []),
                safe_updates.get("evidence"),
            )
        edge_payload.update(safe_updates)
        candidate.edge = PersonalMapEdge.from_dict(edge_payload)
        if candidate_reason is not None:
            candidate.reason = str(candidate_reason)
    else:
        raise ValueError("candidate payload is invalid")
    candidate.validate()
    candidate.no_secret_scan = _assert_no_secret_like_content(asdict(candidate))
    candidate.revisions.append(
        {
            "editor": editor,
            "edited_at": edited_at,
            "before": before,
            "after": candidate.to_dict(),
        }
    )
    candidate.status_history.append(_build_history(PENDING_REVIEW, edited_at, actor=editor, reason="edited"))
    _append_candidate_record(vault_root, candidate)
    return candidate


def approve_personal_map_candidate(
    vault_root: str | Path,
    candidate_id: str,
    *,
    reviewer: str = "operator",
    reviewed_at: str | None = None,
) -> PersonalMapCandidate:
    candidate = _load_candidate_or_raise(vault_root, candidate_id)
    if candidate.status != PENDING_REVIEW:
        raise ValueError("only pending Personal Map candidates may be approved")
    reviewed_at = reviewed_at or now_utc()
    candidate.status = APPROVED
    candidate.reviewer = reviewer
    candidate.reviewed_at = reviewed_at
    candidate.status_history.append(_build_history(APPROVED, reviewed_at, actor=reviewer, reason="approved"))
    candidate.no_secret_scan = _assert_no_secret_like_content(
        asdict(candidate.node) if candidate.node is not None else asdict(candidate.edge)
    )
    _append_candidate_record(vault_root, candidate)
    return candidate
