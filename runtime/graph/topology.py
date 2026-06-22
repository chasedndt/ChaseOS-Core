"""
topology.py — ChaseOS Graph Substrate: Algorithm Layer

Pure-Python topology algorithms operating over GraphIndex and GraphSnapshot.
No external graph library required.

Algorithms in pass 1:
  connected_components    — BFS over undirected adjacency
  label_propagation       — simple community detection (iterative label update)
  degree_centrality       — normalized (in+out) / (n-1)
  bfs_shortest_path       — unweighted BFS shortest path
  top_by_degree           — most connected nodes
  cross_domain_edges      — edges spanning different domains
  isolated_nodes          — nodes with zero edges

Design rule:
  Algorithms operate on the adjacency representation from GraphIndex.
  They return plain Python dicts/lists/primitives — no library objects.
  This layer is replaceable: a future pass could swap in NetworkX or rustworkx
  behind this same interface without changing callers.

The topology service is called by the builder after snapshot assembly.
Community assignments are written back into the snapshot as the only
mutation of the canonical artifact.
"""

from __future__ import annotations

import random
from collections import deque
from typing import Optional

from .artifact import GraphSnapshot, Confidence
from .index import GraphIndex


# ── Connected components ──────────────────────────────────────────────────────

def connected_components(index: GraphIndex) -> list[list[str]]:
    """
    BFS-based connected components detection (undirected).

    Returns a list of components, each being a list of node_ids.
    Sorted by component size (largest first).
    """
    adj = index.adjacency_list(directed=False)
    visited: set[str] = set()
    components: list[list[str]] = []

    for start_node in adj:
        if start_node in visited:
            continue
        # BFS from start_node
        component: list[str] = []
        queue: deque[str] = deque([start_node])
        visited.add(start_node)
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in adj.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        components.append(component)

    return sorted(components, key=len, reverse=True)


# ── Label propagation community detection ─────────────────────────────────────

def label_propagation(
    index: GraphIndex,
    max_iterations: int = 10,
    seed: Optional[int] = None,
) -> dict[str, int]:
    """
    Simple label propagation community detection.

    Each node starts with a unique community label (its own node_id hash).
    On each iteration, each node adopts the most common label among its neighbors.
    Ties are broken randomly.

    Returns: {node_id: community_id} mapping.
    community_id is an integer assigned by ranking final communities by size.
    """
    nodes = [n.node_id for n in index._snapshot.nodes]
    if not nodes:
        return {}

    adj = index.adjacency_list(directed=False)

    # Initialize: each node gets its own label
    labels: dict[str, int] = {node_id: i for i, node_id in enumerate(nodes)}

    rng = random.Random(seed if seed is not None else 42)

    for _ in range(max_iterations):
        changed = False
        shuffled = list(nodes)
        rng.shuffle(shuffled)

        for node_id in shuffled:
            neighbors = adj.get(node_id, [])
            if not neighbors:
                continue  # isolated nodes keep their label

            # Count neighbor labels
            label_counts: dict[int, int] = {}
            for neighbor_id in neighbors:
                if neighbor_id in labels:
                    lbl = labels[neighbor_id]
                    label_counts[lbl] = label_counts.get(lbl, 0) + 1

            if not label_counts:
                continue

            # Find max count
            max_count = max(label_counts.values())
            candidates = [lbl for lbl, cnt in label_counts.items() if cnt == max_count]

            # Pick randomly from tied candidates
            chosen = rng.choice(candidates)
            if chosen != labels[node_id]:
                labels[node_id] = chosen
                changed = True

        if not changed:
            break

    # Renumber communities: 0, 1, 2, ... by size (largest = 0)
    community_to_members: dict[int, list[str]] = {}
    for node_id, lbl in labels.items():
        community_to_members.setdefault(lbl, []).append(node_id)

    # Sort by community size descending, assign new IDs
    sorted_communities = sorted(community_to_members.items(), key=lambda x: len(x[1]), reverse=True)
    old_to_new: dict[int, int] = {old: new for new, (old, _) in enumerate(sorted_communities)}

    return {node_id: old_to_new[lbl] for node_id, lbl in labels.items()}


# ── Degree centrality ──────────────────────────────────────────────────────────

def degree_centrality(index: GraphIndex) -> dict[str, float]:
    """
    Normalized degree centrality: (in_degree + out_degree) / (n - 1).

    Returns {node_id: centrality_score}.
    Score is 0.0 for isolated nodes, 1.0 for nodes connected to all others.
    """
    n = len(index._snapshot.nodes)
    if n <= 1:
        return {node_id: 0.0 for node_id in index.node_by_id}

    normalizer = n - 1
    return {
        node_id: (index.in_degree(node_id) + index.out_degree(node_id)) / normalizer
        for node_id in index.node_by_id
    }


# ── BFS shortest path ─────────────────────────────────────────────────────────

def bfs_shortest_path(
    index: GraphIndex,
    source_id: str,
    target_id: str,
    directed: bool = False,
) -> Optional[list[str]]:
    """
    BFS shortest path from source_id to target_id.

    directed=True: follows directed edges only (source→target direction)
    directed=False: treats graph as undirected

    Returns the path as a list of node_ids (inclusive of source and target),
    or None if no path exists.
    """
    if source_id == target_id:
        return [source_id]
    if source_id not in index.node_by_id or target_id not in index.node_by_id:
        return None

    adj = index.adjacency_list(directed=directed)
    visited: set[str] = {source_id}
    queue: deque[list[str]] = deque([[source_id]])

    while queue:
        path = queue.popleft()
        current = path[-1]
        for neighbor in adj.get(current, []):
            if neighbor == target_id:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(path + [neighbor])

    return None


# ── Convenience analytics ─────────────────────────────────────────────────────

def top_by_degree(index: GraphIndex, n: int = 10) -> list[dict]:
    """
    Return the top-n most connected nodes by total degree.

    Returns a list of dicts with node_id, label, node_type, degree, in_degree, out_degree.
    """
    scored = []
    for node in index._snapshot.nodes:
        scored.append({
            "node_id": node.node_id,
            "label": node.label,
            "node_type": node.node_type,
            "degree": index.degree(node.node_id),
            "in_degree": index.in_degree(node.node_id),
            "out_degree": index.out_degree(node.node_id),
            "domain": node.domain,
        })
    scored.sort(key=lambda x: x["degree"], reverse=True)
    return scored[:n]


def isolated_nodes(index: GraphIndex) -> list[str]:
    """Return node_ids with zero edges (no in or out connections)."""
    return [
        node.node_id
        for node in index._snapshot.nodes
        if index.degree(node.node_id) == 0
    ]


def cross_domain_edges(index: GraphIndex) -> list[dict]:
    """
    Return edges that cross domain boundaries.

    These are edges where source and target nodes have different (non-None) domains.
    Cross-domain edges are often architecturally significant or surprising.
    """
    results = []
    for edge in index._snapshot.edges:
        src_node = index.node_by_id.get(edge.source_id)
        tgt_node = index.node_by_id.get(edge.target_id)
        if src_node is None or tgt_node is None:
            continue
        src_domain = src_node.domain
        tgt_domain = tgt_node.domain
        if src_domain and tgt_domain and src_domain != tgt_domain:
            results.append({
                "edge_id": edge.edge_id,
                "relation": edge.relation,
                "source_label": src_node.label,
                "source_domain": src_domain,
                "target_label": tgt_node.label,
                "target_domain": tgt_domain,
                "confidence": edge.confidence,
            })
    return results


def ambiguous_edges(index: GraphIndex) -> list[dict]:
    """Return edges with confidence AMBIGUOUS or INFERRED."""
    results = []
    for edge in index._snapshot.edges:
        if edge.confidence in (Confidence.INFERRED, Confidence.AMBIGUOUS):
            src_node = index.node_by_id.get(edge.source_id)
            tgt_node = index.node_by_id.get(edge.target_id)
            results.append({
                "edge_id": edge.edge_id,
                "relation": edge.relation,
                "confidence": edge.confidence,
                "source_label": src_node.label if src_node else edge.source_id,
                "target_label": tgt_node.label if tgt_node else edge.target_id,
                "provenance": edge.provenance,
            })
    return results


def community_summary(index: GraphIndex) -> list[dict]:
    """
    Summarize each community: size, dominant node type, dominant domain, top members.

    Returns sorted by community size descending.
    """
    summaries = []
    for community_id, node_ids in sorted(index.nodes_by_community.items()):
        nodes = [index.node_by_id[nid] for nid in node_ids if nid in index.node_by_id]
        if not nodes:
            continue

        # Count node types
        type_counts: dict[str, int] = {}
        domain_counts: dict[str, int] = {}
        for node in nodes:
            type_counts[node.node_type] = type_counts.get(node.node_type, 0) + 1
            if node.domain:
                domain_counts[node.domain] = domain_counts.get(node.domain, 0) + 1

        dominant_type = max(type_counts, key=type_counts.__getitem__) if type_counts else "unknown"
        dominant_domain = max(domain_counts, key=domain_counts.__getitem__) if domain_counts else "unknown"

        # Top members by degree
        top_members = sorted(nodes, key=lambda n: index.degree(n.node_id), reverse=True)[:5]

        summaries.append({
            "community_id": community_id,
            "size": len(nodes),
            "dominant_type": dominant_type,
            "dominant_domain": dominant_domain,
            "type_breakdown": type_counts,
            "domain_breakdown": domain_counts,
            "top_members": [{"label": n.label, "type": n.node_type, "degree": index.degree(n.node_id)} for n in top_members],
        })

    return sorted(summaries, key=lambda x: x["size"], reverse=True)
