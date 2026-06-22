"""
index.py — ChaseOS Graph Substrate: In-Memory Index Layer

GraphIndex builds derived, ephemeral lookup tables over a GraphSnapshot.
The snapshot is always the source of truth; indexes are rebuilt from it.

Index strategy:
  node_by_id                  — O(1) node lookup
  outgoing_edges_by_source    — forward traversal
  incoming_edges_by_target    — reverse traversal
  edges_by_relation           — filter by relation type
  nodes_by_source_file        — "what did this file produce?"
  nodes_by_type               — "show me all workflows / all classes"
  nodes_by_domain             — "what's in the aor domain?"
  community_by_node           — "what community does this node belong to?"
  nodes_by_community          — "what nodes share this community?"

All operations are O(1) or O(k) where k is the result size.
No mutable graph library objects. Dicts and lists only.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .artifact import GraphSnapshot, GraphNode, GraphEdge


class GraphIndex:
    """
    In-memory index over a GraphSnapshot.

    Build once from a snapshot; treat as read-only.
    Rebuild after any snapshot update.
    """

    def __init__(self, snapshot: GraphSnapshot) -> None:
        self._snapshot = snapshot
        self._build(snapshot)

    def _build(self, snapshot: GraphSnapshot) -> None:
        # Primary node index
        self.node_by_id: dict[str, GraphNode] = {
            n.node_id: n for n in snapshot.nodes
        }

        # Edge traversal indexes
        self.outgoing_edges: dict[str, list[GraphEdge]] = defaultdict(list)
        self.incoming_edges: dict[str, list[GraphEdge]] = defaultdict(list)
        self.edges_by_relation: dict[str, list[GraphEdge]] = defaultdict(list)

        for edge in snapshot.edges:
            self.outgoing_edges[edge.source_id].append(edge)
            self.incoming_edges[edge.target_id].append(edge)
            self.edges_by_relation[edge.relation].append(edge)

        # Structural indexes
        self.nodes_by_source_file: dict[str, list[GraphNode]] = defaultdict(list)
        self.nodes_by_type: dict[str, list[GraphNode]] = defaultdict(list)
        self.nodes_by_domain: dict[str, list[GraphNode]] = defaultdict(list)

        for node in snapshot.nodes:
            self.nodes_by_source_file[node.source_file].append(node)
            self.nodes_by_type[node.node_type].append(node)
            if node.domain:
                self.nodes_by_domain[node.domain].append(node)

        # Community indexes
        self.community_by_node: dict[str, int] = dict(snapshot.community_assignments)
        self.nodes_by_community: dict[int, list[str]] = defaultdict(list)
        for node_id, community_id in snapshot.community_assignments.items():
            self.nodes_by_community[community_id].append(node_id)

    # ── Traversal helpers ─────────────────────────────────────────────────────

    def neighbors_out(self, node_id: str) -> list[GraphNode]:
        """Nodes reachable from node_id via outgoing edges."""
        edges = self.outgoing_edges.get(node_id, [])
        result = []
        for edge in edges:
            target = self.node_by_id.get(edge.target_id)
            if target is not None:
                result.append(target)
        return result

    def neighbors_in(self, node_id: str) -> list[GraphNode]:
        """Nodes that have edges pointing to node_id."""
        edges = self.incoming_edges.get(node_id, [])
        result = []
        for edge in edges:
            source = self.node_by_id.get(edge.source_id)
            if source is not None:
                result.append(source)
        return result

    def neighbors_all(self, node_id: str) -> list[GraphNode]:
        """All neighbors (in + out, deduplicated)."""
        seen: set[str] = {node_id}
        result: list[GraphNode] = []
        for node in self.neighbors_out(node_id) + self.neighbors_in(node_id):
            if node.node_id not in seen:
                seen.add(node.node_id)
                result.append(node)
        return result

    def degree(self, node_id: str) -> int:
        """Total edge count (in + out) for a node."""
        return len(self.outgoing_edges.get(node_id, [])) + len(self.incoming_edges.get(node_id, []))

    def out_degree(self, node_id: str) -> int:
        return len(self.outgoing_edges.get(node_id, []))

    def in_degree(self, node_id: str) -> int:
        return len(self.incoming_edges.get(node_id, []))

    # ── Query helpers ─────────────────────────────────────────────────────────

    def search_by_label(self, term: str, case_sensitive: bool = False) -> list[GraphNode]:
        """Return nodes whose label contains term."""
        if not case_sensitive:
            term = term.lower()
        result = []
        for node in self._snapshot.nodes:
            label = node.label if case_sensitive else node.label.lower()
            if term in label:
                result.append(node)
        return result

    def search_by_property(self, key: str, value: str) -> list[GraphNode]:
        """Return nodes with a matching property key=value."""
        result = []
        for node in self._snapshot.nodes:
            if str(node.properties.get(key, "")) == value:
                result.append(node)
        return result

    def get_edges_between(self, source_id: str, target_id: str) -> list[GraphEdge]:
        """Return all edges from source to target."""
        return [
            e for e in self.outgoing_edges.get(source_id, [])
            if e.target_id == target_id
        ]

    # ── Adjacency list (for topology algorithms) ──────────────────────────────

    def adjacency_list(self, directed: bool = True) -> dict[str, list[str]]:
        """
        Return adjacency as {node_id: [neighbor_id, ...]} for algorithm use.

        directed=True: outgoing edges only (for directed algorithms)
        directed=False: both directions (for undirected algorithms like component detection)
        """
        adj: dict[str, list[str]] = {n.node_id: [] for n in self._snapshot.nodes}
        for edge in self._snapshot.edges:
            if edge.source_id in adj:
                adj[edge.source_id].append(edge.target_id)
            if not directed and edge.target_id in adj:
                adj[edge.target_id].append(edge.source_id)
        return adj

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        snapshot = self._snapshot
        node_type_counts: dict[str, int] = {}
        for node in snapshot.nodes:
            node_type_counts[node.node_type] = node_type_counts.get(node.node_type, 0) + 1

        relation_counts: dict[str, int] = {}
        for edge in snapshot.edges:
            relation_counts[edge.relation] = relation_counts.get(edge.relation, 0) + 1

        confidence_counts: dict[str, int] = {}
        for edge in snapshot.edges:
            confidence_counts[edge.confidence] = confidence_counts.get(edge.confidence, 0) + 1

        community_sizes: dict[int, int] = {}
        for community_id in snapshot.community_assignments.values():
            community_sizes[community_id] = community_sizes.get(community_id, 0) + 1

        return {
            "node_count": snapshot.node_count,
            "edge_count": snapshot.edge_count,
            "node_types": node_type_counts,
            "relation_types": relation_counts,
            "edge_confidence": confidence_counts,
            "community_count": len(community_sizes),
            "community_sizes": dict(sorted(community_sizes.items())),
            "source_files": len(self.nodes_by_source_file),
            "snapshot_id": snapshot.snapshot_id,
            "created_at": snapshot.created_at,
        }
