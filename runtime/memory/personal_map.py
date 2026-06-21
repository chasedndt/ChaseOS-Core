"""Personal Map / User Profile Graph schema and governed applied persistence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from runtime.common.evidence import EvidenceRef, now_utc

PERSONAL_MAP_NODE_TYPES = {
    "person",
    "goal",
    "project",
    "domain",
    "value",
    "doctrine",
    "habit",
    "cadence",
    "skill",
    "constraint",
    "preference",
    "commitment",
    "event",
    "business_os",
    "learning_map",
    "content_map",
    "trading_map",
}

APPLIED_PERSONAL_MAP_GRAPH = Path("runtime") / "memory" / "personal-map" / "graph.json"
APPLIED_PERSONAL_MAP_AUDIT_ROOT = Path("07_LOGS") / "Pulse-Decks" / "personal-map-apply-registry"
PROTECTED_PERSONAL_MAP_WRITE_TARGETS = [
    "SOUL.md",
    "00_HOME/Operating-System.md",
    "00_HOME/Principles.md",
    "02_KNOWLEDGE/",
]


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


def _evidence_from_dict(items: list[Any]) -> list[EvidenceRef]:
    return [item if isinstance(item, EvidenceRef) else EvidenceRef(**item) for item in items]


def personal_map_graph_path() -> Path:
    """Return the governed applied Personal Map graph path relative to vault root."""

    return APPLIED_PERSONAL_MAP_GRAPH


@dataclass
class PersonalMapNode:
    node_id: str
    node_type: str
    label: str
    summary: str = ""
    evidence: list[EvidenceRef] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=now_utc)
    status: str = "candidate"
    history: list[dict[str, Any]] = field(default_factory=list)

    def validate(self) -> None:
        if not self.node_id:
            raise ValueError("node_id is required")
        if self.node_type not in PERSONAL_MAP_NODE_TYPES:
            raise ValueError(f"node_type must be one of {sorted(PERSONAL_MAP_NODE_TYPES)}")
        if not self.label:
            raise ValueError("label is required")
        for item in self.evidence:
            item.validate()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonalMapNode":
        payload = dict(data)
        payload["evidence"] = _evidence_from_dict(payload.get("evidence", []))
        return cls(**payload)


@dataclass
class PersonalMapEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    relation: str
    evidence: list[EvidenceRef] = field(default_factory=list)
    confidence: float = 0.0
    updated_at: str = field(default_factory=now_utc)
    status: str = "candidate"
    history: list[dict[str, Any]] = field(default_factory=list)

    def validate(self) -> None:
        if not self.edge_id:
            raise ValueError("edge_id is required")
        if not self.source_node_id or not self.target_node_id:
            raise ValueError("edge source and target are required")
        if not self.relation:
            raise ValueError("edge relation is required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("edge confidence must be between 0.0 and 1.0")
        for item in self.evidence:
            item.validate()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonalMapEdge":
        payload = dict(data)
        payload["evidence"] = _evidence_from_dict(payload.get("evidence", []))
        return cls(**payload)


@dataclass
class PersonalMapGraph:
    graph_id: str
    nodes: dict[str, PersonalMapNode] = field(default_factory=dict)
    edges: dict[str, PersonalMapEdge] = field(default_factory=dict)
    canonical_writeback_enabled: bool = False
    updated_at: str = field(default_factory=now_utc)
    apply_history: list[dict[str, Any]] = field(default_factory=list)

    def add_node(self, node: PersonalMapNode) -> None:
        node.validate()
        self.nodes[node.node_id] = node
        self.updated_at = now_utc()

    def add_edge(self, edge: PersonalMapEdge) -> None:
        edge.validate()
        if edge.source_node_id not in self.nodes or edge.target_node_id not in self.nodes:
            raise ValueError("edge endpoints must exist before adding edge")
        self.edges[edge.edge_id] = edge
        self.updated_at = now_utc()

    def validate(self) -> None:
        if not self.graph_id:
            raise ValueError("graph_id is required")
        if self.canonical_writeback_enabled:
            raise ValueError("personal map graph cannot enable canonical writeback by default")
        for node in self.nodes.values():
            node.validate()
        for edge in self.edges.values():
            edge.validate()
            if edge.source_node_id not in self.nodes or edge.target_node_id not in self.nodes:
                raise ValueError("edge endpoints must exist in graph")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "graph_id": self.graph_id,
            "canonical_writeback_enabled": self.canonical_writeback_enabled,
            "updated_at": self.updated_at,
            "apply_history": list(self.apply_history),
            "nodes": {key: asdict(value) for key, value in self.nodes.items()},
            "edges": {key: asdict(value) for key, value in self.edges.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonalMapGraph":
        graph = cls(
            graph_id=str(data.get("graph_id") or "personal-map"),
            canonical_writeback_enabled=bool(data.get("canonical_writeback_enabled", False)),
            updated_at=str(data.get("updated_at") or now_utc()),
            apply_history=list(data.get("apply_history") or []),
        )
        graph.nodes = {
            key: PersonalMapNode.from_dict(value)
            for key, value in (data.get("nodes") or {}).items()
        }
        graph.edges = {
            key: PersonalMapEdge.from_dict(value)
            for key, value in (data.get("edges") or {}).items()
        }
        graph.validate()
        return graph


def load_applied_personal_map_graph(vault_root: str | Path) -> PersonalMapGraph:
    vault = _vault_path(vault_root)
    path = (vault / APPLIED_PERSONAL_MAP_GRAPH).resolve()
    _assert_inside(path, vault, "personal map graph must stay inside vault root")
    if not path.exists():
        return PersonalMapGraph(graph_id="personal-map")
    return PersonalMapGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_applied_personal_map_graph(vault_root: str | Path, graph: PersonalMapGraph) -> Path:
    vault = _vault_path(vault_root)
    path = (vault / APPLIED_PERSONAL_MAP_GRAPH).resolve()
    _assert_inside(path, vault, "personal map graph must stay inside vault root")
    graph.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def personal_map_graph_hash(graph: PersonalMapGraph) -> str:
    payload = json.dumps(graph.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _audit_path(vault: Path, applied_at: str) -> Path:
    date_slug = applied_at[:10] if applied_at and len(applied_at) >= 10 else now_utc()[:10]
    root = (vault / APPLIED_PERSONAL_MAP_AUDIT_ROOT).resolve()
    path = root / f"{date_slug}-personal-map-apply.jsonl"
    _assert_inside(path, root, "personal map apply audit must stay inside apply registry")
    return path


def _ready_candidates(vault_root: str | Path):
    from runtime.memory.candidate_store import load_personal_map_candidates

    return [
        candidate
        for candidate in load_personal_map_candidates(vault_root)
        if candidate.status == "approved"
    ]


def _candidate_applied_key(candidate: Any) -> str:
    return hashlib.sha256(candidate.to_dict().__repr__().encode("utf-8")).hexdigest()


def build_personal_map_apply_preview(vault_root: str | Path) -> dict[str, Any]:
    graph = load_applied_personal_map_graph(vault_root)
    ready = _ready_candidates(vault_root)
    planned_nodes: list[str] = []
    planned_edges: list[str] = []
    already_applied: list[str] = []
    for candidate in ready:
        if any(entry.get("candidate_id") == candidate.candidate_id for entry in graph.apply_history):
            already_applied.append(candidate.candidate_id)
            continue
        if candidate.candidate_type == "node" and candidate.node is not None:
            planned_nodes.append(candidate.node.node_id)
        elif candidate.candidate_type == "edge" and candidate.edge is not None:
            planned_edges.append(candidate.edge.edge_id)
    return {
        "target_path": APPLIED_PERSONAL_MAP_GRAPH.as_posix(),
        "graph_present_before": bool(graph.nodes or graph.edges),
        "graph_node_count_before": len(graph.nodes),
        "graph_edge_count_before": len(graph.edges),
        "graph_hash_before": personal_map_graph_hash(graph),
        "ready_candidate_count": len(ready),
        "already_applied_candidate_count": len(already_applied),
        "planned_node_writes": planned_nodes,
        "planned_edge_writes": planned_edges,
        "candidate_ids": [candidate.candidate_id for candidate in ready],
        "blocked_writes": list(PROTECTED_PERSONAL_MAP_WRITE_TARGETS),
        "canonical_writeback_allowed": False,
        "protected_docs_mutated": False,
        "external_provider_calls": 0,
        "connector_calls": 0,
    }


def apply_approved_personal_map_candidates(
    vault_root: str | Path,
    *,
    operator_confirmed: bool = False,
    applied_at: str | None = None,
) -> dict[str, Any]:
    """Apply approved candidates to runtime Personal Map state only.

    This is the governed explicit apply lane: it writes only graph.json plus an
    audit JSONL record, never protected identity/control docs or canonical
    knowledge.
    """

    if not operator_confirmed:
        raise ValueError("operator_confirmed=True is required for Personal Map apply")
    vault = _vault_path(vault_root)
    applied_at = applied_at or now_utc()
    graph = load_applied_personal_map_graph(vault)
    before_hash = personal_map_graph_hash(graph)
    ready = _ready_candidates(vault)
    applied_nodes: list[str] = []
    applied_edges: list[str] = []
    already_applied = 0
    entries: list[dict[str, Any]] = []

    for candidate in ready:
        if any(entry.get("candidate_id") == candidate.candidate_id for entry in graph.apply_history):
            already_applied += 1
            continue
        if candidate.candidate_type == "node" and candidate.node is not None:
            node = PersonalMapNode.from_dict(asdict(candidate.node))
            node.status = "applied"
            node.updated_at = applied_at
            node.history.append(
                {"event": "applied", "candidate_id": candidate.candidate_id, "applied_at": applied_at}
            )
            graph.nodes[node.node_id] = node
            applied_nodes.append(node.node_id)
        elif candidate.candidate_type == "edge" and candidate.edge is not None:
            edge = PersonalMapEdge.from_dict(asdict(candidate.edge))
            edge.status = "applied"
            edge.updated_at = applied_at
            edge.history.append(
                {"event": "applied", "candidate_id": candidate.candidate_id, "applied_at": applied_at}
            )
            if edge.source_node_id not in graph.nodes or edge.target_node_id not in graph.nodes:
                raise ValueError("edge candidate endpoints must exist before apply")
            graph.edges[edge.edge_id] = edge
            applied_edges.append(edge.edge_id)
        else:
            continue
        entry = {
            "candidate_id": candidate.candidate_id,
            "candidate_type": candidate.candidate_type,
            "idempotency_key": _candidate_applied_key(candidate),
            "applied_at": applied_at,
        }
        graph.apply_history.append(entry)
        entries.append(entry)

    graph.updated_at = applied_at
    if applied_nodes or applied_edges:
        save_applied_personal_map_graph(vault, graph)
    after_hash = personal_map_graph_hash(graph)
    audit = {
        "applied_at": applied_at,
        "target_path": APPLIED_PERSONAL_MAP_GRAPH.as_posix(),
        "graph_hash_before": before_hash,
        "graph_hash_after": after_hash,
        "applied_node_ids": applied_nodes,
        "applied_edge_ids": applied_edges,
        "already_applied_candidate_count": already_applied,
        "entries": entries,
        "blocked_writes": list(PROTECTED_PERSONAL_MAP_WRITE_TARGETS),
        "protected_docs_mutated": False,
        "canonical_writeback_allowed": False,
        "external_provider_calls": 0,
        "connector_calls": 0,
        "writes": [APPLIED_PERSONAL_MAP_GRAPH.as_posix()],
    }
    if applied_nodes or applied_edges:
        path = _audit_path(vault, applied_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(audit, sort_keys=True))
            handle.write("\n")
        audit["writes"].append(_relative_to_vault(vault, path))
    return audit
