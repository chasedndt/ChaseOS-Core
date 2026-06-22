"""
builder.py — ChaseOS Graph Substrate: Build Orchestrator

Orchestrates the full pipeline:
  extraction → deduplication → snapshot assembly → index build → topology →
  (optional: resolver pass) → (optional save)

Public API:
  build_snapshot(vault_root, scope, ...)         → GraphSnapshot
  build_index(snapshot)                          → GraphIndex
  build_query_service(snapshot, index)           → GraphQueryService
  full_pipeline(vault_root, scope, ...)          → (snapshot, index, query_service)
  save_snapshot(snapshot, output_path)           → Path

Default pass-1 scope constants:
  DEFAULT_CODE_SCOPE    — runtime subdirectories
  DEFAULT_MANIFEST_SCOPE — workflow manifest registry
  DEFAULT_DOC_SCOPE     — key architecture docs

Pass 2 additions:
  full_pipeline accepts resolve_imports=True to run the cross-file resolver
  pass after extraction. This adds INFERRED IMPORT_RESOLVES_TO edges that link
  import nodes to their actual definition files, enabling real cross-domain edges.

Design:
  The builder is the only place that mutates the snapshot post-construction.
  It writes community_assignments back into the snapshot after topology runs.
  All other layers treat the snapshot as immutable.

  Deduplication strategy:
  - Nodes: deduplicate by node_id (deterministic ID = same content = same node)
  - Edges: deduplicate by edge_id (same source + relation + target = same edge)
  - When duplicate nodes exist, keep the one with higher confidence
    (EXTRACTED > INFERRED > AMBIGUOUS)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .artifact import (
    GraphSnapshot, GraphNode, GraphEdge,
    Confidence, make_snapshot_id, utc_now_iso,
)
from .extractor import (
    ExtractionResult,
    PythonExtractor,
    YAMLManifestExtractor,
    MarkdownExtractor,
)
from .index import GraphIndex
from .topology import label_propagation
from .query import GraphQueryService
from .resolver import resolve_imports, apply_resolved_edges


# ── Default pass-1 scope ──────────────────────────────────────────────────────

DEFAULT_CODE_SCOPE = [
    "runtime/aor",
    "runtime/workflows",
    "runtime/capture",
    "runtime/cli",
    "runtime/graph",
]

DEFAULT_MANIFEST_SCOPE = [
    "runtime/workflows/registry",
]

DEFAULT_DOC_SCOPE = [
    "CLAUDE.md",
    "README.md",
    "ROADMAP.md",
    "06_AGENTS/Autonomous-Operator-Runtime.md",
    "06_AGENTS/SIC-Architecture.md",
    "06_AGENTS/Vault-Map.md",
    "06_AGENTS/Feature-Fit-Register.md",
]

# Vault-wide doc scope: all Markdown in 06_AGENTS/ + top-level anchors.
# MarkdownExtractor.extract_files() handles directories via rglob("*.md").
VAULT_WIDE_DOC_SCOPE = [
    "CLAUDE.md",
    "README.md",
    "ROADMAP.md",
    "06_AGENTS/",
]

# Python files to exclude from extraction (test data, tmp files, etc.)
_DEFAULT_PY_EXCLUDES = [
    "_tmp_tests",
    "__pycache__",
]


# ── Confidence ordering for deduplication ─────────────────────────────────────

_CONFIDENCE_RANK = {
    Confidence.EXTRACTED: 3,
    Confidence.INFERRED:  2,
    Confidence.AMBIGUOUS: 1,
}


def _higher_confidence(a: str, b: str) -> str:
    """Return the confidence label with higher rank."""
    return a if _CONFIDENCE_RANK.get(a, 0) >= _CONFIDENCE_RANK.get(b, 0) else b


# ── Deduplication ─────────────────────────────────────────────────────────────

def _dedup_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    """
    Deduplicate nodes by node_id.

    When duplicates exist, keep the node with the highest confidence.
    This handles cases where the same class is referenced as a base
    (INFERRED) in one file and defined (EXTRACTED) in another.
    """
    by_id: dict[str, GraphNode] = {}
    for node in nodes:
        existing = by_id.get(node.node_id)
        if existing is None:
            by_id[node.node_id] = node
        else:
            # Keep the higher-confidence version
            if _CONFIDENCE_RANK.get(node.confidence, 0) > _CONFIDENCE_RANK.get(existing.confidence, 0):
                by_id[node.node_id] = node
    return list(by_id.values())


def _dedup_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    """
    Deduplicate edges by edge_id.

    When duplicate edges exist, keep the one with higher confidence.
    """
    by_id: dict[str, GraphEdge] = {}
    for edge in edges:
        existing = by_id.get(edge.edge_id)
        if existing is None:
            by_id[edge.edge_id] = edge
        else:
            if _CONFIDENCE_RANK.get(edge.confidence, 0) > _CONFIDENCE_RANK.get(existing.confidence, 0):
                by_id[edge.edge_id] = edge
    return list(by_id.values())


def _drop_dangling_edges(nodes: list[GraphNode], edges: list[GraphEdge]) -> list[GraphEdge]:
    """Remove edges where source or target node does not exist in the node set."""
    node_ids = {n.node_id for n in nodes}
    return [e for e in edges if e.source_id in node_ids and e.target_id in node_ids]


# ── Build pipeline ────────────────────────────────────────────────────────────

def build_snapshot(
    vault_root: Path,
    *,
    code_scope: Optional[list[str]] = None,
    manifest_scope: Optional[list[str]] = None,
    doc_scope: Optional[list[str]] = None,
    py_excludes: Optional[list[str]] = None,
    topology_seed: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> GraphSnapshot:
    """
    Build a GraphSnapshot from the given vault root and scope.

    Parameters
    ----------
    vault_root : Path
        Absolute path to the ChaseOS vault root.
    code_scope : list[str], optional
        Vault-relative paths to extract Python code from. Defaults to DEFAULT_CODE_SCOPE.
    manifest_scope : list[str], optional
        Vault-relative paths to extract YAML manifests from. Defaults to DEFAULT_MANIFEST_SCOPE.
    doc_scope : list[str], optional
        Vault-relative paths (files or dirs) to extract Markdown from. Defaults to DEFAULT_DOC_SCOPE.
    py_excludes : list[str], optional
        Substrings to exclude from Python extraction. Defaults to _DEFAULT_PY_EXCLUDES.
    topology_seed : int, optional
        Random seed for label propagation (reproducibility).
    metadata : dict, optional
        Free-form metadata to embed in the snapshot.

    Returns
    -------
    GraphSnapshot with community_assignments populated.
    """
    start_time = time.monotonic()

    code_scope    = code_scope    or DEFAULT_CODE_SCOPE
    manifest_scope = manifest_scope or DEFAULT_MANIFEST_SCOPE
    doc_scope     = doc_scope     or DEFAULT_DOC_SCOPE
    py_excludes   = py_excludes   or _DEFAULT_PY_EXCLUDES

    vault_root = vault_root.resolve()
    all_nodes: list[GraphNode] = []
    all_edges: list[GraphEdge] = []
    all_errors: list[str] = []
    source_files: list[str] = []
    scope_paths: list[str] = []

    # ── Python extraction ──────────────────────────────────────────────────
    py_extractor = PythonExtractor()
    for scope_path in code_scope:
        full_path = vault_root / scope_path
        scope_paths.append(scope_path)
        if full_path.is_dir():
            result = py_extractor.extract_directory(
                full_path, vault_root, exclude_patterns=py_excludes
            )
            all_nodes.extend(result.nodes)
            all_edges.extend(result.edges)
            all_errors.extend(result.errors)
            source_files.extend(result.source_files)
        elif full_path.is_file() and full_path.suffix == ".py":
            result = py_extractor.extract_file(full_path, vault_root)
            all_nodes.extend(result.nodes)
            all_edges.extend(result.edges)
            all_errors.extend(result.errors)
            source_files.extend(result.source_files)

    # ── YAML manifest extraction ───────────────────────────────────────────
    yaml_extractor = YAMLManifestExtractor()
    for scope_path in manifest_scope:
        full_path = vault_root / scope_path
        scope_paths.append(scope_path)
        if full_path.is_dir():
            result = yaml_extractor.extract_directory(full_path, vault_root)
            all_nodes.extend(result.nodes)
            all_edges.extend(result.edges)
            all_errors.extend(result.errors)
            source_files.extend(result.source_files)
        elif full_path.is_file() and full_path.suffix in (".yaml", ".yml"):
            result = yaml_extractor.extract_file(full_path, vault_root)
            all_nodes.extend(result.nodes)
            all_edges.extend(result.edges)
            all_errors.extend(result.errors)
            source_files.extend(result.source_files)

    # ── Markdown extraction ────────────────────────────────────────────────
    md_extractor = MarkdownExtractor()
    md_paths: list[Path] = []
    for scope_path in doc_scope:
        full_path = vault_root / scope_path
        scope_paths.append(scope_path)
        md_paths.append(full_path)

    result = md_extractor.extract_files(md_paths, vault_root)
    all_nodes.extend(result.nodes)
    all_edges.extend(result.edges)
    all_errors.extend(result.errors)
    source_files.extend(result.source_files)

    # ── Deduplication ──────────────────────────────────────────────────────
    deduped_nodes = _dedup_nodes(all_nodes)
    deduped_edges = _dedup_edges(all_edges)
    clean_edges   = _drop_dangling_edges(deduped_nodes, deduped_edges)

    elapsed_extraction = time.monotonic() - start_time

    # ── Snapshot assembly ──────────────────────────────────────────────────
    snapshot = GraphSnapshot(
        snapshot_id=make_snapshot_id(),
        created_at=utc_now_iso(),
        vault_root=str(vault_root),
        extraction_scope=list(dict.fromkeys(scope_paths)),  # deduplicate, preserve order
        nodes=deduped_nodes,
        edges=clean_edges,
        community_assignments={},  # populated by topology below
        build_info={
            "raw_nodes": len(all_nodes),
            "raw_edges": len(all_edges),
            "deduped_nodes": len(deduped_nodes),
            "deduped_edges": len(deduped_edges),
            "clean_edges": len(clean_edges),
            "source_files": len(set(source_files)),
            "errors": len(all_errors),
            "error_list": all_errors[:20],  # cap for artifact size
            "extraction_seconds": round(elapsed_extraction, 3),
            "substrate_version": "1.0",
        },
        metadata=metadata or {},
    )

    # ── Topology pass ──────────────────────────────────────────────────────
    topo_start = time.monotonic()
    index = GraphIndex(snapshot)
    communities = label_propagation(index, seed=topology_seed)
    snapshot.community_assignments = communities

    elapsed_topo = time.monotonic() - topo_start
    snapshot.build_info["topology_seconds"] = round(elapsed_topo, 3)
    snapshot.build_info["community_count"] = len(set(communities.values()))

    return snapshot


def load_latest_snapshot(vault_root: Path) -> Optional[GraphSnapshot]:
    """
    Load the most recently saved GraphSnapshot from 07_LOGS/Graph-Snapshots/.

    Returns None if no snapshot files exist or if the directory is missing.
    Fail-open: JSON decode errors or other read failures return None.
    """
    snapshot_dir = vault_root / "07_LOGS" / "Graph-Snapshots"
    if not snapshot_dir.is_dir():
        return None
    snapshots = sorted(snapshot_dir.glob("graph_snapshot_*.json"), reverse=True)
    if not snapshots:
        return None
    try:
        return GraphSnapshot.load(snapshots[0])
    except Exception:  # noqa: BLE001
        return None


def build_index(snapshot: GraphSnapshot) -> GraphIndex:
    """Build an in-memory index from a snapshot."""
    return GraphIndex(snapshot)


def build_query_service(snapshot: GraphSnapshot, index: GraphIndex) -> GraphQueryService:
    """Build a query service from snapshot + index."""
    return GraphQueryService(snapshot, index)


def full_pipeline(
    vault_root: Path,
    *,
    resolve_imports_pass: bool = False,
    **kwargs,
) -> tuple[GraphSnapshot, GraphIndex, GraphQueryService]:
    """
    Run the full extraction → topology → query pipeline.

    Parameters
    ----------
    vault_root : Path
        Absolute path to the vault root.
    resolve_imports_pass : bool
        If True, run the cross-file import resolver after extraction.
        This adds INFERRED IMPORT_RESOLVES_TO edges and re-runs topology
        so community assignments reflect the richer graph. Default False
        (pass 1 behaviour).
    **kwargs
        Forwarded to build_snapshot.

    Returns (snapshot, index, query_service) ready for use.
    """
    snapshot = build_snapshot(vault_root, **kwargs)

    if resolve_imports_pass:
        resolved_edges = resolve_imports(snapshot, vault_root)
        if resolved_edges:
            snapshot = apply_resolved_edges(snapshot, resolved_edges)
            # Re-run topology on the enriched graph
            index = GraphIndex(snapshot)
            communities = label_propagation(index, seed=kwargs.get("topology_seed"))
            snapshot.community_assignments = communities
            snapshot.build_info["community_count"] = len(set(communities.values()))

    index = build_index(snapshot)
    query_service = build_query_service(snapshot, index)
    return snapshot, index, query_service


def save_snapshot(
    snapshot: GraphSnapshot,
    output_dir: Path,
    *,
    filename: Optional[str] = None,
) -> Path:
    """
    Save snapshot to disk as JSON.

    Default filename: graph_snapshot_YYYYMMDD-HHMMSS__<id[:8]>.json
    Returns the path where the snapshot was saved.
    """
    if filename is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"graph_snapshot_{ts}__{snapshot.snapshot_id[:8]}.json"

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    snapshot.save(output_path)
    return output_path
