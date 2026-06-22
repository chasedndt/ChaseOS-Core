"""
diff.py — ChaseOS Graph Substrate: Snapshot Diffing (Pass 2)

Compares two GraphSnapshots using their stable node/edge IDs.

Because IDs are deterministic (SHA-256 of type:source_file:label), comparing
two snapshots of the same corpus at different points in time is a clean
set subtraction — no position or ordering artifacts.

Public API:
  diff_snapshots(before, after)       → SnapshotDiff
  render_diff_report(diff)            → str (Markdown report)

Design:
  - Added nodes/edges: in `after` but not in `before`
  - Removed nodes/edges: in `before` but not in `after`
  - Changed nodes: same node_id, but label, type, or confidence differs
  - Community shift: nodes whose community_id changed between snapshots
  - No fuzzy matching — same ID = same node; different ID = different node
  - All fields on SnapshotDiff are plain lists/dicts for easy serialization
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .artifact import GraphSnapshot, GraphNode, GraphEdge


# ── Result model ─────────────────────────────────────────────────────────────

@dataclass
class NodeChange:
    """A node whose ID is unchanged but whose content has changed."""
    node_id: str
    before: GraphNode
    after: GraphNode
    changed_fields: list[str]  # which fields differ


@dataclass
class SnapshotDiff:
    """
    The diff between two GraphSnapshots.

    All fields use stable IDs, making this artifact serializable and
    suitable for build log attachment.
    """
    before_snapshot_id: str
    after_snapshot_id: str
    before_created_at: str
    after_created_at: str

    # Nodes
    added_nodes: list[GraphNode] = field(default_factory=list)
    removed_nodes: list[GraphNode] = field(default_factory=list)
    changed_nodes: list[NodeChange] = field(default_factory=list)

    # Edges
    added_edges: list[GraphEdge] = field(default_factory=list)
    removed_edges: list[GraphEdge] = field(default_factory=list)

    # Community shifts: node_id → (before_community, after_community)
    community_shifts: dict[str, tuple[Optional[int], Optional[int]]] = field(default_factory=dict)

    # Summary counts
    @property
    def nodes_added(self) -> int:
        return len(self.added_nodes)

    @property
    def nodes_removed(self) -> int:
        return len(self.removed_nodes)

    @property
    def nodes_changed(self) -> int:
        return len(self.changed_nodes)

    @property
    def edges_added(self) -> int:
        return len(self.added_edges)

    @property
    def edges_removed(self) -> int:
        return len(self.removed_edges)

    @property
    def community_shifts_count(self) -> int:
        return len(self.community_shifts)

    @property
    def is_clean(self) -> bool:
        """True if the snapshots are structurally identical (ignoring metadata)."""
        return (
            self.nodes_added == 0
            and self.nodes_removed == 0
            and self.nodes_changed == 0
            and self.edges_added == 0
            and self.edges_removed == 0
        )

    def summary(self) -> dict:
        return {
            "before_snapshot_id": self.before_snapshot_id,
            "after_snapshot_id": self.after_snapshot_id,
            "nodes_added": self.nodes_added,
            "nodes_removed": self.nodes_removed,
            "nodes_changed": self.nodes_changed,
            "edges_added": self.edges_added,
            "edges_removed": self.edges_removed,
            "community_shifts": self.community_shifts_count,
            "is_clean": self.is_clean,
        }


# ── Diff computation ──────────────────────────────────────────────────────────

_NODE_COMPARE_FIELDS = ("label", "node_type", "source_file", "domain", "confidence")


def _compare_nodes(before: GraphNode, after: GraphNode) -> list[str]:
    """Return list of field names that differ between two nodes with the same ID."""
    changed = []
    for f in _NODE_COMPARE_FIELDS:
        if getattr(before, f) != getattr(after, f):
            changed.append(f)
    return changed


def diff_snapshots(before: GraphSnapshot, after: GraphSnapshot) -> SnapshotDiff:
    """
    Compute the structural diff between two snapshots.

    Uses stable node/edge IDs — same ID means same structural entity.
    Content changes (label, type, confidence) on same-ID nodes are surfaced
    as NodeChange entries.

    Parameters
    ----------
    before : GraphSnapshot
        The earlier snapshot (baseline).
    after : GraphSnapshot
        The later snapshot (comparison target).

    Returns
    -------
    SnapshotDiff with all added/removed/changed nodes and edges, plus
    community assignment shifts for nodes that appear in both snapshots.
    """
    diff = SnapshotDiff(
        before_snapshot_id=before.snapshot_id,
        after_snapshot_id=after.snapshot_id,
        before_created_at=before.created_at,
        after_created_at=after.created_at,
    )

    # ── Node diff ─────────────────────────────────────────────────────────────
    before_nodes: dict[str, GraphNode] = {n.node_id: n for n in before.nodes}
    after_nodes:  dict[str, GraphNode] = {n.node_id: n for n in after.nodes}

    before_ids = set(before_nodes)
    after_ids  = set(after_nodes)

    diff.added_nodes   = [after_nodes[nid]  for nid in sorted(after_ids  - before_ids)]
    diff.removed_nodes = [before_nodes[nid] for nid in sorted(before_ids - after_ids)]

    # Changed: same ID, different content
    for nid in sorted(before_ids & after_ids):
        changed_fields = _compare_nodes(before_nodes[nid], after_nodes[nid])
        if changed_fields:
            diff.changed_nodes.append(NodeChange(
                node_id=nid,
                before=before_nodes[nid],
                after=after_nodes[nid],
                changed_fields=changed_fields,
            ))

    # ── Edge diff ─────────────────────────────────────────────────────────────
    before_edges: dict[str, GraphEdge] = {e.edge_id: e for e in before.edges}
    after_edges:  dict[str, GraphEdge] = {e.edge_id: e for e in after.edges}

    before_edge_ids = set(before_edges)
    after_edge_ids  = set(after_edges)

    diff.added_edges   = [after_edges[eid]  for eid in sorted(after_edge_ids  - before_edge_ids)]
    diff.removed_edges = [before_edges[eid] for eid in sorted(before_edge_ids - after_edge_ids)]

    # ── Community shift ───────────────────────────────────────────────────────
    common_ids = before_ids & after_ids
    for nid in sorted(common_ids):
        bc = before.community_assignments.get(nid)
        ac = after.community_assignments.get(nid)
        if bc != ac:
            diff.community_shifts[nid] = (bc, ac)

    return diff


# ── Report rendering ──────────────────────────────────────────────────────────

def render_diff_report(diff: SnapshotDiff, *, max_items: int = 20) -> str:
    """
    Render a SnapshotDiff as a Markdown report.

    Parameters
    ----------
    diff : SnapshotDiff
    max_items : int
        Maximum entries per section before truncation notice is shown.

    Returns
    -------
    Markdown string suitable for writing to 07_LOGS/Graph-Reports/.
    """
    lines: list[str] = []

    # Header
    lines += [
        "# ChaseOS Graph Substrate — Snapshot Diff Report",
        "",
        f"**Before:** `{diff.before_snapshot_id[:12]}` ({diff.before_created_at[:19]})",
        f"**After:**  `{diff.after_snapshot_id[:12]}` ({diff.after_created_at[:19]})",
        "",
    ]

    # Summary table
    lines += [
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Nodes added | {diff.nodes_added} |",
        f"| Nodes removed | {diff.nodes_removed} |",
        f"| Nodes changed | {diff.nodes_changed} |",
        f"| Edges added | {diff.edges_added} |",
        f"| Edges removed | {diff.edges_removed} |",
        f"| Community shifts | {diff.community_shifts_count} |",
        f"| Clean (no structural change) | {'Yes' if diff.is_clean else 'No'} |",
        "",
    ]

    if diff.is_clean:
        lines.append("_Snapshots are structurally identical. No changes detected._")
        lines.append("")
        return "\n".join(lines)

    # Added nodes
    if diff.added_nodes:
        lines += ["## Added Nodes", ""]
        shown = diff.added_nodes[:max_items]
        for n in shown:
            lines.append(f"- `{n.node_id[:10]}` **{n.label}** ({n.node_type}) — `{n.source_file}` [{n.confidence}]")
        if len(diff.added_nodes) > max_items:
            lines.append(f"- _…and {len(diff.added_nodes) - max_items} more_")
        lines.append("")

    # Removed nodes
    if diff.removed_nodes:
        lines += ["## Removed Nodes", ""]
        shown = diff.removed_nodes[:max_items]
        for n in shown:
            lines.append(f"- `{n.node_id[:10]}` **{n.label}** ({n.node_type}) — `{n.source_file}` [{n.confidence}]")
        if len(diff.removed_nodes) > max_items:
            lines.append(f"- _…and {len(diff.removed_nodes) - max_items} more_")
        lines.append("")

    # Changed nodes
    if diff.changed_nodes:
        lines += ["## Changed Nodes", ""]
        shown = diff.changed_nodes[:max_items]
        for c in shown:
            fields_str = ", ".join(c.changed_fields)
            lines.append(f"- `{c.node_id[:10]}` **{c.after.label}** — changed: {fields_str}")
        if len(diff.changed_nodes) > max_items:
            lines.append(f"- _…and {len(diff.changed_nodes) - max_items} more_")
        lines.append("")

    # Added edges
    if diff.added_edges:
        lines += ["## Added Edges", ""]
        shown = diff.added_edges[:max_items]
        for e in shown:
            lines.append(
                f"- `{e.source_id[:8]}` —[{e.relation}]→ `{e.target_id[:8]}` [{e.confidence}]"
            )
        if len(diff.added_edges) > max_items:
            lines.append(f"- _…and {len(diff.added_edges) - max_items} more_")
        lines.append("")

    # Removed edges
    if diff.removed_edges:
        lines += ["## Removed Edges", ""]
        shown = diff.removed_edges[:max_items]
        for e in shown:
            lines.append(
                f"- `{e.source_id[:8]}` —[{e.relation}]→ `{e.target_id[:8]}` [{e.confidence}]"
            )
        if len(diff.removed_edges) > max_items:
            lines.append(f"- _…and {len(diff.removed_edges) - max_items} more_")
        lines.append("")

    # Community shifts (sample)
    if diff.community_shifts:
        lines += ["## Community Shifts (sample)", ""]
        items = list(diff.community_shifts.items())[:max_items]
        for nid, (bc, ac) in items:
            lines.append(f"- `{nid[:10]}` community {bc} → {ac}")
        if len(diff.community_shifts) > max_items:
            lines.append(f"- _…and {len(diff.community_shifts) - max_items} more_")
        lines.append("")

    lines.append("---")
    lines.append("_Generated by ChaseOS Graph Substrate diff.py — Pass 2_")
    lines.append("")

    return "\n".join(lines)
