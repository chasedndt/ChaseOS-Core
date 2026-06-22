"""
query.py — ChaseOS Graph Substrate: Graph-First Query and Routing Service

The GraphQueryService provides graph-guided navigation before raw file search.

The key principle: before a runtime or operator makes a broad file-system search,
the graph substrate can narrow the target set using structural knowledge.

Public operations:
  search(terms)                — find nodes by label/property matching
  inspect_node(node_id)        — get node + all neighbors + community
  inspect_community(comm_id)   — get all nodes in a community + internal edges
  shortest_path(src, tgt)      — BFS path between two nodes
  narrow_to_relevant(terms)    — graph-first narrowing: return node_ids + source_files
  graph_stats()                — summary statistics
  files_for_community(comm_id) — source files for a community
  nodes_by_type(node_type)     — filtered node list

Design:
  All operations are read-only.
  All operations return plain dicts/lists — no graph objects escape this layer.
  This is the boundary between the graph substrate and callers (AOR, operator CLI, etc.).
"""

from __future__ import annotations

from typing import Optional

from .artifact import GraphSnapshot, GraphNode, GraphEdge
from .index import GraphIndex
from .topology import bfs_shortest_path


class GraphQueryService:
    """
    Graph-first query and routing service over a GraphSnapshot.

    Accepts a snapshot + index at construction; provides read-only query operations.
    Rebuild by constructing a new instance from an updated snapshot.
    """

    def __init__(self, snapshot: GraphSnapshot, index: GraphIndex) -> None:
        self._snapshot = snapshot
        self._index = index

    # ── Term search ───────────────────────────────────────────────────────────

    def search(
        self,
        terms: list[str],
        *,
        node_types: Optional[list[str]] = None,
        domains: Optional[list[str]] = None,
        confidence: Optional[list[str]] = None,
        max_results: int = 50,
    ) -> list[dict]:
        """
        Find nodes matching one or more search terms.

        Matches against: label, source_file, and string values in properties.

        Filters:
          node_types: limit to these node types (e.g. ["python_class", "workflow"])
          domains: limit to these domains (e.g. ["aor", "capture"])
          confidence: limit to these confidence levels

        Returns dicts with node info + degree + community.
        """
        if not terms:
            return []

        lower_terms = [t.lower() for t in terms]
        results = []

        for node in self._snapshot.nodes:
            # Apply filters
            if node_types and node.node_type not in node_types:
                continue
            if domains and (not node.domain or node.domain not in domains):
                continue
            if confidence and node.confidence not in confidence:
                continue

            # Match: label, source_file, or any property value
            search_corpus = " ".join([
                node.label.lower(),
                node.source_file.lower(),
                " ".join(str(v).lower() for v in node.properties.values()),
            ])

            if all(term in search_corpus for term in lower_terms):
                results.append(self._node_to_dict(node))

        results.sort(key=lambda x: x["degree"], reverse=True)
        return results[:max_results]

    # ── Node inspection ───────────────────────────────────────────────────────

    def inspect_node(self, node_id: str) -> Optional[dict]:
        """
        Inspect a node: return its properties, all neighbors, and community.

        Returns None if node_id is not in the graph.
        """
        node = self._index.node_by_id.get(node_id)
        if node is None:
            return None

        outgoing = self._index.outgoing_edges.get(node_id, [])
        incoming = self._index.incoming_edges.get(node_id, [])

        return {
            "node": self._node_to_dict(node),
            "community_id": self._index.community_by_node.get(node_id),
            "outgoing": [self._edge_to_dict(e) for e in outgoing],
            "incoming": [self._edge_to_dict(e) for e in incoming],
            "neighbors_out": [self._node_to_dict(n) for n in self._index.neighbors_out(node_id)],
            "neighbors_in": [self._node_to_dict(n) for n in self._index.neighbors_in(node_id)],
        }

    # ── Community inspection ──────────────────────────────────────────────────

    def inspect_community(self, community_id: int) -> Optional[dict]:
        """
        Inspect a community: all member nodes + internal edges + source files.

        Returns None if community_id not found.
        """
        member_ids = self._index.nodes_by_community.get(community_id)
        if member_ids is None:
            return None

        member_id_set = set(member_ids)
        members = [
            self._node_to_dict(self._index.node_by_id[nid])
            for nid in member_ids
            if nid in self._index.node_by_id
        ]

        # Internal edges: edges where both source and target are in this community
        internal_edges = [
            self._edge_to_dict(e)
            for e in self._snapshot.edges
            if e.source_id in member_id_set and e.target_id in member_id_set
        ]

        # External edges: edges crossing community boundary
        external_edges = [
            self._edge_to_dict(e)
            for e in self._snapshot.edges
            if (e.source_id in member_id_set) != (e.target_id in member_id_set)
        ]

        source_files = sorted({
            m["source_file"] for m in members if m["source_file"]
        })

        return {
            "community_id": community_id,
            "size": len(members),
            "members": sorted(members, key=lambda n: n["degree"], reverse=True),
            "internal_edges": internal_edges,
            "external_edge_count": len(external_edges),
            "source_files": source_files,
        }

    # ── Shortest path ─────────────────────────────────────────────────────────

    def shortest_path(
        self,
        source_id: str,
        target_id: str,
        directed: bool = False,
    ) -> Optional[dict]:
        """
        Find the shortest path between two nodes.

        Returns dict with path (list of node dicts) and length,
        or None if no path exists.
        """
        path_ids = bfs_shortest_path(self._index, source_id, target_id, directed=directed)
        if path_ids is None:
            return None

        path_nodes = []
        for nid in path_ids:
            node = self._index.node_by_id.get(nid)
            if node:
                path_nodes.append(self._node_to_dict(node))

        return {
            "source_id": source_id,
            "target_id": target_id,
            "length": len(path_ids) - 1,
            "path": path_nodes,
        }

    # ── Graph-first narrowing ─────────────────────────────────────────────────

    def narrow_to_relevant(
        self,
        terms: list[str],
        *,
        include_community_neighbors: bool = True,
        max_source_files: int = 20,
    ) -> dict:
        """
        Graph-first narrowing: given query terms, return the most relevant source files.

        This is the primary utility for pre-read narrowing:
        instead of searching all files, use graph structure to identify
        the most structurally relevant files for a given query.

        Strategy:
        1. Search for matching nodes
        2. If include_community_neighbors: expand to nodes in the same communities
        3. Collect source files from all matched nodes
        4. Sort by relevance score (match count × degree)

        Returns: {
            "matched_nodes": [...],
            "relevant_node_ids": [...],
            "source_files": [...],  # sorted by relevance
            "community_ids": [...],
        }
        """
        matched = self.search(terms, max_results=30)
        if not matched:
            return {
                "query_terms": terms,
                "matched_nodes": [],
                "relevant_node_ids": [],
                "source_files": [],
                "community_ids": [],
            }

        matched_ids = {m["node_id"] for m in matched}
        all_relevant_ids = set(matched_ids)
        community_ids: set[int] = set()

        if include_community_neighbors:
            for m in matched:
                comm_id = self._index.community_by_node.get(m["node_id"])
                if comm_id is not None:
                    community_ids.add(comm_id)
                    community_member_ids = self._index.nodes_by_community.get(comm_id, [])
                    all_relevant_ids.update(community_member_ids)

        # Score source files by how many relevant nodes come from them
        file_score: dict[str, float] = {}
        for node_id in all_relevant_ids:
            node = self._index.node_by_id.get(node_id)
            if node is None or not node.source_file:
                continue
            boost = 2.0 if node_id in matched_ids else 1.0
            degree_boost = 1.0 + (self._index.degree(node_id) / 10.0)
            file_score[node.source_file] = file_score.get(node.source_file, 0.0) + boost * degree_boost

        sorted_files = sorted(file_score.items(), key=lambda x: -x[1])
        top_files = [f for f, _ in sorted_files[:max_source_files]]

        return {
            "query_terms": terms,
            "matched_nodes": matched,
            "relevant_node_ids": list(all_relevant_ids),
            "source_files": top_files,
            "community_ids": sorted(community_ids),
        }

    # ── Statistics ────────────────────────────────────────────────────────────

    def graph_stats(self) -> dict:
        """Return graph-level statistics."""
        return self._index.stats()

    # ── Typed node listing ────────────────────────────────────────────────────

    def nodes_by_type(self, node_type: str) -> list[dict]:
        """Return all nodes of a given type, sorted by degree."""
        nodes = self._index.nodes_by_type.get(node_type, [])
        return sorted(
            [self._node_to_dict(n) for n in nodes],
            key=lambda x: x["degree"],
            reverse=True,
        )

    def files_for_community(self, community_id: int) -> list[str]:
        """Return all distinct source files for a community's nodes."""
        member_ids = self._index.nodes_by_community.get(community_id, [])
        files: set[str] = set()
        for node_id in member_ids:
            node = self._index.node_by_id.get(node_id)
            if node and node.source_file:
                files.add(node.source_file)
        return sorted(files)

    # ── Serialization helpers ─────────────────────────────────────────────────

    def _node_to_dict(self, node: GraphNode) -> dict:
        return {
            "node_id": node.node_id,
            "label": node.label,
            "node_type": node.node_type,
            "source_file": node.source_file,
            "source_line": node.source_line,
            "domain": node.domain,
            "confidence": node.confidence,
            "degree": self._index.degree(node.node_id),
            "in_degree": self._index.in_degree(node.node_id),
            "out_degree": self._index.out_degree(node.node_id),
            "community_id": self._index.community_by_node.get(node.node_id),
            "properties": node.properties,
        }

    def _edge_to_dict(self, edge: GraphEdge) -> dict:
        src = self._index.node_by_id.get(edge.source_id)
        tgt = self._index.node_by_id.get(edge.target_id)
        return {
            "edge_id": edge.edge_id,
            "relation": edge.relation,
            "source_id": edge.source_id,
            "source_label": src.label if src else edge.source_id,
            "target_id": edge.target_id,
            "target_label": tgt.label if tgt else edge.target_id,
            "confidence": edge.confidence,
            "properties": edge.properties,
        }
