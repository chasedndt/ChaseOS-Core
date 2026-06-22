"""
runtime/graph — ChaseOS Graph Substrate

A ChaseOS-native graph substrate for corpus, repo, and runtime navigation.

Architecture (Pass 1):
  artifact.py   — canonical GraphSnapshot model; serializable source of truth
  index.py      — derived in-memory indexes over a snapshot
  extractor.py  — deterministic extraction: Python AST, YAML manifests, Markdown
  topology.py   — algorithm layer: clustering, centrality, paths (pure Python)
  reporter.py   — operator-facing graph report generation
  query.py      — graph-first query and routing service
  builder.py    — orchestrator: extraction → snapshot → index → topology

Pass 2 additions:
  resolver.py   — cross-file import resolution; IMPORT_RESOLVES_TO edges; SymbolIndex
  diff.py       — snapshot diffing; SnapshotDiff; render_diff_report
  advisory.py   — AOR advisory narrowing seam; AdvisoryNarrowingResult

The graph artifact is always the source of truth.
In-memory indexes and topology results are derived and ephemeral.
No graph library is the architecture — all backends are adapters behind topology.py.
"""

from .artifact import GraphNode, GraphEdge, GraphSnapshot, Confidence
from .index import GraphIndex
from .builder import build_snapshot, full_pipeline
from .resolver import SymbolIndex, resolve_imports, apply_resolved_edges
from .diff import diff_snapshots, render_diff_report, SnapshotDiff
from .advisory import advise_required_reads, AdvisoryNarrowingResult

__all__ = [
    # Pass 1
    "GraphNode",
    "GraphEdge",
    "GraphSnapshot",
    "Confidence",
    "GraphIndex",
    "build_snapshot",
    "full_pipeline",
    # Pass 2
    "SymbolIndex",
    "resolve_imports",
    "apply_resolved_edges",
    "diff_snapshots",
    "render_diff_report",
    "SnapshotDiff",
    "advise_required_reads",
    "AdvisoryNarrowingResult",
]
