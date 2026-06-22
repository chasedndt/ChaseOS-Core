"""
artifact.py — ChaseOS Graph Substrate: Canonical Snapshot Model

The GraphSnapshot is the source of truth for the graph substrate.
It is serializable, deterministic, and library-independent.

Every node and edge carries:
- a stable ID (derived from content, not position)
- a confidence marker (EXTRACTED | INFERRED | AMBIGUOUS)
- provenance metadata

Community assignments and build metadata are top-level on the snapshot,
not embedded in individual nodes — keeping the artifact clean for diffing.

Design principle: the artifact is the truth. Indexes are derived. Backends are adapters.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ── Confidence vocabulary ────────────────────────────────────────────────────

class Confidence:
    """Confidence/trust markers for nodes and edges."""
    EXTRACTED = "EXTRACTED"   # directly observed from source structure
    INFERRED  = "INFERRED"    # derived by pattern or heuristic, not direct observation
    AMBIGUOUS = "AMBIGUOUS"   # conflicting signals; requires human review

    ALL = frozenset([EXTRACTED, INFERRED, AMBIGUOUS])


# ── Node type vocabulary ─────────────────────────────────────────────────────

class NodeType:
    FILE            = "file"            # a source file in the corpus
    PYTHON_CLASS    = "python_class"    # a class definition
    PYTHON_FUNCTION = "python_function" # a function or method definition
    PYTHON_IMPORT   = "python_import"   # an imported module/symbol
    WORKFLOW        = "workflow"        # an AOR workflow manifest
    MANIFEST_FIELD  = "manifest_field"  # a key field within a manifest
    DOC_SECTION     = "doc_section"     # a heading-delimited section in a markdown doc
    WIKILINK_REF    = "wikilink_ref"    # a wikilink target referenced from a doc
    FRONTMATTER_KEY = "frontmatter_key" # a frontmatter key-value pair


# ── Relation vocabulary ──────────────────────────────────────────────────────

class Relation:
    IMPORTS              = "imports"               # file imports module/symbol
    DEFINES              = "defines"               # file or class defines function/class
    INHERITS             = "inherits"              # class inherits from another
    CALLS                = "calls"                 # function calls another (inferred, pass 1 limited)
    REFERENCES           = "references"            # doc references another doc via wikilink
    WORKFLOW_DECLARES    = "workflow_declares"      # workflow manifest declares a field
    WORKFLOW_LINKS_FILE  = "workflow_links_file"    # manifest references a Python handler file
    SECTION_LINKS        = "section_links"         # heading links to another doc section
    FILE_CONTAINS        = "file_contains"         # file contains a class/function/section


# ── Core data model ──────────────────────────────────────────────────────────

@dataclass
class GraphNode:
    """
    A node in the ChaseOS graph substrate.

    node_id is stable and deterministic — derived from node_type + source_file + label.
    It must not change when re-extracting the same source.
    """
    node_id: str
    label: str
    node_type: str          # use NodeType constants
    source_file: str        # vault-relative path
    source_line: Optional[int]
    domain: Optional[str]   # vault domain hint (e.g. "aor", "capture", "sic")
    project: Optional[str]  # vault project hint
    properties: dict[str, Any]
    confidence: str         # use Confidence constants
    provenance: str         # how this node was produced (e.g. "python_ast:import")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GraphNode":
        return cls(**d)


@dataclass
class GraphEdge:
    """
    A directed edge in the ChaseOS graph substrate.

    edge_id is stable and deterministic — derived from source_id + relation + target_id.
    Edges are directed: source → target.
    """
    edge_id: str
    source_id: str
    target_id: str
    relation: str           # use Relation constants
    confidence: str         # use Confidence constants
    properties: dict[str, Any]
    provenance: str         # how this edge was produced

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GraphEdge":
        return cls(**d)


@dataclass
class GraphSnapshot:
    """
    The canonical ChaseOS graph artifact.

    This is the source of truth. In-memory indexes, topology results, and
    query results are all derived from this artifact.

    Serializes to/from JSON. Deterministic given the same source corpus.
    """
    snapshot_id: str
    created_at: str                         # ISO 8601 UTC
    vault_root: str                         # absolute path at extraction time
    extraction_scope: list[str]             # vault-relative paths extracted
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    community_assignments: dict[str, int]   # node_id → community_id (populated by topology)
    build_info: dict[str, Any]              # extraction stats, version, timing
    metadata: dict[str, Any]               # free-form user/system metadata

    # ── Identity helpers ──────────────────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "vault_root": self.vault_root,
            "extraction_scope": self.extraction_scope,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "community_assignments": self.community_assignments,
            "build_info": self.build_info,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def save(self, path: Path) -> None:
        """Write snapshot to disk as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict) -> "GraphSnapshot":
        return cls(
            snapshot_id=d["snapshot_id"],
            created_at=d["created_at"],
            vault_root=d["vault_root"],
            extraction_scope=d.get("extraction_scope", []),
            nodes=[GraphNode.from_dict(n) for n in d.get("nodes", [])],
            edges=[GraphEdge.from_dict(e) for e in d.get("edges", [])],
            community_assignments=d.get("community_assignments", {}),
            build_info=d.get("build_info", {}),
            metadata=d.get("metadata", {}),
        )

    @classmethod
    def load(cls, path: Path) -> "GraphSnapshot":
        """Load snapshot from a JSON file."""
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


# ── ID generation ─────────────────────────────────────────────────────────────

def make_node_id(node_type: str, source_file: str, label: str) -> str:
    """
    Produce a stable deterministic node ID.

    IDs are derived from content identity, not position or creation order.
    Re-extracting the same source always produces the same ID.
    """
    canonical = f"{node_type}:{source_file}:{label}"
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def make_edge_id(source_id: str, relation: str, target_id: str) -> str:
    """
    Produce a stable deterministic edge ID.

    Derived from source, relation, and target — not from order of creation.
    """
    canonical = f"{source_id}:{relation}:{target_id}"
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def make_snapshot_id() -> str:
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Node/edge builders ────────────────────────────────────────────────────────

def make_node(
    label: str,
    node_type: str,
    source_file: str,
    *,
    source_line: Optional[int] = None,
    domain: Optional[str] = None,
    project: Optional[str] = None,
    properties: Optional[dict] = None,
    confidence: str = Confidence.EXTRACTED,
    provenance: str = "",
) -> GraphNode:
    return GraphNode(
        node_id=make_node_id(node_type, source_file, label),
        label=label,
        node_type=node_type,
        source_file=source_file,
        source_line=source_line,
        domain=domain,
        project=project,
        properties=properties or {},
        confidence=confidence,
        provenance=provenance,
    )


def make_edge(
    source_id: str,
    target_id: str,
    relation: str,
    *,
    confidence: str = Confidence.EXTRACTED,
    properties: Optional[dict] = None,
    provenance: str = "",
) -> GraphEdge:
    return GraphEdge(
        edge_id=make_edge_id(source_id, relation, target_id),
        source_id=source_id,
        target_id=target_id,
        relation=relation,
        confidence=confidence,
        properties=properties or {},
        provenance=provenance,
    )
