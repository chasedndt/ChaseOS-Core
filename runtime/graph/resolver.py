"""
resolver.py — ChaseOS Graph Substrate: Cross-File Symbol Resolution (Pass 2)

Resolves Python import nodes to their actual definition files in the vault.

Pass 1 limitation: import nodes carry the domain of the *importing* file,
not the imported module's actual location. This produces zero genuine
cross-domain edges because both endpoints of file→import edges share the
same source file (and thus the same domain).

Pass 2 fix: build a SymbolIndex from all FILE nodes in the snapshot, then
for each PYTHON_IMPORT node whose module path maps to a known vault file,
emit an INFERRED IMPORT_RESOLVES_TO edge from the import node to the target
file node. These edges produce real cross-domain structure:
  runtime/aor/engine.py → import "runtime.workflows.operator_today" → resolves to
  runtime/workflows/operator_today.py
  → aor→workflows cross-domain edge is now visible.

Public API:
  SymbolIndex(snapshot)                        — build module → file_node_id index
  resolve_imports(snapshot, vault_root)        → list[GraphEdge]  (new INFERRED edges)
  apply_resolved_edges(snapshot, edges)        → GraphSnapshot    (new snapshot with edges added)

Design rules:
  - All produced edges are INFERRED confidence (never EXTRACTED — we are matching
    heuristically, not observing a definitive structural fact)
  - No nodes are created; only edges between existing nodes
  - Module path matching is conservative: exact match first, suffix fallback second
  - If no file node matches a module path, the import is silently skipped (fail-open)
  - vault_root is needed only for path normalization; no file I/O in resolve step
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .artifact import (
    GraphSnapshot, GraphEdge,
    NodeType, Confidence,
    make_edge, make_node_id,
)

# New relation type for resolved import edges (additive — does not break pass 1)
IMPORT_RESOLVES_TO = "import_resolves_to"


class SymbolIndex:
    """
    Maps Python module paths to file node_ids found in a GraphSnapshot.

    Two lookup strategies, in priority order:
    1. Exact match: "runtime.aor.engine" → "runtime/aor/engine.py"
    2. Suffix match: if the module label ends with the last N components of a known
       file path (e.g. ".engine" matches "runtime/aor/engine.py")

    Index is built once at construction; lookup is O(1) for exact, O(k) for suffix.
    """

    def __init__(self, snapshot: GraphSnapshot) -> None:
        # file_path → node_id for all FILE nodes
        self._file_to_id: dict[str, str] = {}
        # module_path → node_id (dotted module → file node)
        self._module_to_id: dict[str, str] = {}

        for node in snapshot.nodes:
            if node.node_type != NodeType.FILE:
                continue
            rel = node.source_file.replace("\\", "/")
            self._file_to_id[rel] = node.node_id

            # Build module-path variants for this file
            if rel.endswith(".py"):
                # e.g. "runtime/aor/engine.py" → "runtime.aor.engine"
                module_path = rel[:-3].replace("/", ".")
                self._module_to_id[module_path] = node.node_id
                # Also index without leading "runtime." prefix
                # e.g. "aor.engine" and "engine" as shorter aliases
                parts = module_path.split(".")
                for start in range(1, len(parts)):
                    short = ".".join(parts[start:])
                    if short and short not in self._module_to_id:
                        self._module_to_id[short] = node.node_id

    def lookup(self, module_label: str) -> Optional[str]:
        """
        Resolve a module label to a file node_id.

        module_label may be:
        - A full dotted module: "runtime.aor.engine"
        - A from-import module: "runtime.aor.engine" (from "from runtime.aor.engine import X")
        - A stdlib or external name: "os", "json" — these will return None (not in vault)

        Returns node_id of the target FILE node, or None if unresolvable.
        """
        # Strip any trailing .* or .ClassName — we want the module, not the symbol
        base = _strip_symbol_suffix(module_label)

        # Exact match (covers full and shortened dotted paths)
        if base in self._module_to_id:
            return self._module_to_id[base]

        # Suffix walk: try progressively shorter suffixes
        parts = base.split(".")
        for start in range(1, len(parts)):
            candidate = ".".join(parts[start:])
            if candidate in self._module_to_id:
                return self._module_to_id[candidate]

        return None

    def all_file_paths(self) -> list[str]:
        return list(self._file_to_id.keys())

    def __len__(self) -> int:
        return len(self._module_to_id)


def _strip_symbol_suffix(module_label: str) -> str:
    """
    Reduce a from-import label to its module path.

    "runtime.aor.engine.AOREngine"  → "runtime.aor.engine"
    "runtime.aor.engine.run_stage"  → "runtime.aor.engine"
    "os.path"                       → "os.path"  (left as-is if no file matches anyway)

    Heuristic: if the last component starts with an uppercase letter, it is likely
    a class name; strip it. If the last component looks like a function name that
    doesn't exist as a module, we leave it (the lookup will just miss gracefully).
    """
    parts = module_label.split(".")
    if len(parts) > 1 and parts[-1] and parts[-1][0].isupper():
        return ".".join(parts[:-1])
    return module_label


def resolve_imports(
    snapshot: GraphSnapshot,
    vault_root: Optional[Path] = None,  # kept for API symmetry; not used in pass 2
) -> list[GraphEdge]:
    """
    Produce INFERRED IMPORT_RESOLVES_TO edges for resolvable import nodes.

    For each PYTHON_IMPORT node in the snapshot:
    1. Try to resolve its label to a file node in the vault via SymbolIndex
    2. If resolved, emit an edge: import_node → target_file_node

    Returns a list of new GraphEdge objects (INFERRED confidence).
    These edges are NOT yet in the snapshot — call apply_resolved_edges to add them.

    No file I/O is performed. All resolution uses the snapshot's existing nodes.
    """
    sym_index = SymbolIndex(snapshot)
    import_node_ids = {
        n.node_id: n
        for n in snapshot.nodes
        if n.node_type == NodeType.PYTHON_IMPORT
    }

    resolved_edges: list[GraphEdge] = []
    seen_edges: set[str] = set()

    for node in snapshot.nodes:
        if node.node_type != NodeType.PYTHON_IMPORT:
            continue

        # The label is either "module.name" (import) or "module.Symbol" (from import)
        target_id = sym_index.lookup(node.label)
        if target_id is None:
            continue

        # Don't self-loop: skip if the import is in the same file as the target
        # (this shouldn't happen, but guard anyway)
        target_node = next(
            (n for n in snapshot.nodes if n.node_id == target_id), None
        )
        if target_node and target_node.source_file == node.source_file:
            continue

        edge = make_edge(
            source_id=node.node_id,
            target_id=target_id,
            relation=IMPORT_RESOLVES_TO,
            confidence=Confidence.INFERRED,
            properties={
                "resolved_from": node.label,
                "resolved_to": target_node.source_file if target_node else "",
            },
            provenance="resolver:import_to_file",
        )

        # Dedup by edge_id before accumulating
        if edge.edge_id not in seen_edges:
            seen_edges.add(edge.edge_id)
            resolved_edges.append(edge)

    return resolved_edges


def apply_resolved_edges(
    snapshot: GraphSnapshot,
    resolved_edges: list[GraphEdge],
) -> GraphSnapshot:
    """
    Return a new GraphSnapshot with resolved import edges added.

    The snapshot's edges list is extended; all other fields are preserved.
    The snapshot_id is regenerated to mark it as a derived artifact.
    build_info is updated with resolver stats.

    This does NOT re-run topology — the caller should rebuild the index and
    re-run label_propagation if community assignments need to reflect the new edges.
    """
    from .artifact import make_snapshot_id, utc_now_iso

    existing_edge_ids = {e.edge_id for e in snapshot.edges}
    new_edges = [e for e in resolved_edges if e.edge_id not in existing_edge_ids]

    updated_build_info = dict(snapshot.build_info)
    updated_build_info["resolver_new_edges"] = len(new_edges)
    updated_build_info["resolver_total_resolved"] = len(resolved_edges)
    updated_build_info["substrate_version"] = "2.0"

    return GraphSnapshot(
        snapshot_id=make_snapshot_id(),
        created_at=utc_now_iso(),
        vault_root=snapshot.vault_root,
        extraction_scope=snapshot.extraction_scope,
        nodes=list(snapshot.nodes),  # unchanged
        edges=list(snapshot.edges) + new_edges,
        community_assignments=dict(snapshot.community_assignments),
        build_info=updated_build_info,
        metadata=dict(snapshot.metadata),
    )


def cross_domain_resolved_edges(
    snapshot: GraphSnapshot,
) -> list[dict]:
    """
    Return all IMPORT_RESOLVES_TO edges that cross domain boundaries.

    Each result dict contains:
    - edge: the GraphEdge
    - source_node: the PYTHON_IMPORT node
    - target_node: the FILE node it resolves to
    - source_domain / target_domain
    """
    node_by_id = {n.node_id: n for n in snapshot.nodes}
    results = []

    for edge in snapshot.edges:
        if edge.relation != IMPORT_RESOLVES_TO:
            continue
        src = node_by_id.get(edge.source_id)
        tgt = node_by_id.get(edge.target_id)
        if not src or not tgt:
            continue
        if src.domain and tgt.domain and src.domain != tgt.domain:
            results.append({
                "edge": edge,
                "source_node": src,
                "target_node": tgt,
                "source_domain": src.domain,
                "target_domain": tgt.domain,
            })

    return results
