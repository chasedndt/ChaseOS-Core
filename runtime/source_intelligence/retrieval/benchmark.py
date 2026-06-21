"""
benchmark.py — SIC Phase 7 Pass 7
Retrieval Quality Benchmark for the ChaseOS Source Intelligence Core.

Entry point:
    run_benchmark(workspace_id, queries, backends, top_k) -> dict

This module answers: "Does a non-stub embedding backend improve retrieval
quality enough to matter for real workspace queries?"

Design principles:
  - Honest engineering benchmark, not marketing copy
  - Runs all comparison backends on the same workspace and queries
  - Reports ranking shifts, score distributions, and lexical relevance signals
  - Restores the workspace to its original backend state after comparison
  - Does not claim semantic superiority without evidence
  - Handles missing/unavailable backends gracefully

Comparison methodology:
  - For each backend, re-index the workspace (force=True) then run all queries
  - Record: top-k chunk IDs, similarity scores, source titles, timing
  - Compute: ranking overlap (Jaccard@k), top-source agreement, score distribution
  - Flag: whether results look topically relevant based on keyword presence
  - Report: honest per-backend + cross-backend comparison

Limitation note:
  Without human relevance judgments, we can't compute precision/recall.
  Instead, we use lexical proxy scoring: does the highest-scoring source
  contain query keywords? This is a necessary honest proxy for unsupervised
  benchmark without ground truth labels.

Architecture constraints:
  - No vault writeback. No note promotion. No Gate bypass.
  - Re-indexing is temporary and workspace-local only.
  - Original backend is restored after benchmark completes.
  - No external network calls unless the openai backend is tested.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..indexes.index_manager import index_workspace, get_manifest
from ..indexes.backend_registry import check_backend_availability, list_backends
from .retriever import query_workspace


# ── Public API ─────────────────────────────────────────────────────────────────

def run_benchmark(
    workspace_id: str,
    queries: list[str],
    backends: list[str] | None = None,
    top_k: int = 5,
    restore_original: bool = True,
) -> dict:
    """
    Run a retrieval quality benchmark comparing multiple embedding backends.

    For each backend:
    1. Re-index the workspace under that backend
    2. Run all queries and record top-k results with scores
    3. Compute per-query relevance proxy (keyword overlap with chunk text)

    After all backends, optionally restore the workspace to its original backend.

    Args:
        workspace_id:     Workspace slug (directory name).
        queries:          List of query strings to benchmark. At least 1 required.
        backends:         List of backend names to compare. Defaults to
                          ["local_stub", "local_word"] (both always available).
                          Unavailable backends are skipped with an explanation.
        top_k:            Number of top results to compare per query (default 5).
        restore_original: If True, re-index under the original backend after
                          benchmark completes. Default True.

    Returns:
        dict with keys:
            workspace_id          — the benchmarked workspace slug
            queries               — the query list
            top_k                 — the top-k setting used
            backends_requested    — the backends that were requested
            backends_run          — the backends that actually ran
            backends_skipped      — backends that were unavailable
            original_backend      — backend before benchmark started
            restored_to_original  — whether the original backend was restored
            per_backend_results   — list of per-backend result dicts
            cross_backend_comparison — cross-backend ranking comparison
            benchmark_status      — "complete" | "partial" | "failed"
            warnings              — list of non-fatal warning strings
            errors                — list of error strings
    """
    result = _empty_result(workspace_id, queries, top_k)

    if not queries:
        result["benchmark_status"] = "failed"
        result["errors"].append("queries list must not be empty.")
        return result

    # Default backends if not specified
    if backends is None:
        backends = ["local_stub", "local_word"]

    result["backends_requested"] = backends

    # Read current workspace manifest to record original backend
    manifest_result = get_manifest(workspace_id)
    original_backend = "local_stub"  # safe default
    original_model   = None
    if manifest_result["success"] and manifest_result["manifest"]:
        m = manifest_result["manifest"]
        original_backend = m.get("provider_name", "local_stub")
        original_model   = m.get("model_name")
    result["original_backend"] = original_backend

    # Check availability for each requested backend
    backends_to_run: list[str] = []
    for b in backends:
        avail = check_backend_availability(b)
        if avail.get("available"):
            backends_to_run.append(b)
        else:
            reason = avail.get("reason", "unavailable")
            result["backends_skipped"].append({
                "backend": b,
                "reason": reason,
            })
            result["warnings"].append(
                f"Backend '{b}' skipped: {reason}"
            )

    result["backends_run"] = backends_to_run

    if not backends_to_run:
        result["benchmark_status"] = "failed"
        result["errors"].append(
            "No available backends to run. "
            "local_stub and local_word are always available. "
            "Check that your backends list includes at least one of them."
        )
        return result

    # Run each backend
    per_backend: list[dict] = []

    for backend_name in backends_to_run:
        backend_result = _run_single_backend(
            workspace_id=workspace_id,
            backend_name=backend_name,
            queries=queries,
            top_k=top_k,
        )
        per_backend.append(backend_result)
        if backend_result.get("errors"):
            for e in backend_result["errors"]:
                result["warnings"].append(f"[{backend_name}] {e}")

    result["per_backend_results"] = per_backend

    # Cross-backend comparison
    if len(per_backend) >= 2:
        result["cross_backend_comparison"] = _compare_backends(
            per_backend=per_backend,
            queries=queries,
            top_k=top_k,
        )

    # Restore original backend if requested
    if restore_original and original_backend in backends_to_run:
        # Original backend was one of the ones we tested — workspace is already in that state
        # (if it was the last backend run) or we need to re-index to restore it
        last_run_backend = backends_to_run[-1]
        if last_run_backend != original_backend:
            restore_result = index_workspace(
                workspace_id=workspace_id,
                adapter_name=original_backend,
                model_name=original_model,
                force_reindex=True,
            )
            if restore_result["success"]:
                result["restored_to_original"] = True
            else:
                result["restored_to_original"] = False
                result["warnings"].append(
                    f"Could not restore original backend '{original_backend}': "
                    f"{restore_result.get('errors', [])}"
                )
        else:
            # Last backend run was the original — no restore needed
            result["restored_to_original"] = True
    elif restore_original and original_backend not in backends_to_run:
        # Original backend not in the test list — restore it explicitly
        restore_result = index_workspace(
            workspace_id=workspace_id,
            adapter_name=original_backend,
            model_name=original_model,
            force_reindex=True,
        )
        if restore_result["success"]:
            result["restored_to_original"] = True
        else:
            result["restored_to_original"] = False
            result["warnings"].append(
                f"Could not restore original backend '{original_backend}'."
            )
    else:
        # restore_original=False — leave in last-run state
        result["restored_to_original"] = False

    # Determine benchmark status
    ran_count = len(per_backend)
    failed_count = sum(1 for b in per_backend if not b.get("success"))

    if ran_count == 0 or failed_count == ran_count:
        result["benchmark_status"] = "failed"
    elif failed_count > 0:
        result["benchmark_status"] = "partial"
    else:
        result["benchmark_status"] = "complete"

    return result


# ── Per-backend runner ─────────────────────────────────────────────────────────

def _run_single_backend(
    workspace_id: str,
    backend_name: str,
    queries: list[str],
    top_k: int,
) -> dict:
    """
    Index workspace under one backend, run all queries, return results.

    Always uses force_reindex=True to ensure vectors match the target backend.
    """
    bresult: dict = {
        "backend":           backend_name,
        "success":           False,
        "model_name":        None,
        "embedding_dimension": None,
        "index_time_sec":    None,
        "query_results":     [],
        "per_query_stats":   [],
        "errors":            [],
        "warnings":          [],
    }

    # ── 1. Re-index workspace under this backend ───────────────────────────────
    t0 = time.monotonic()
    idx_result = index_workspace(
        workspace_id=workspace_id,
        adapter_name=backend_name,
        force_reindex=True,
    )
    index_time = time.monotonic() - t0
    bresult["index_time_sec"] = round(index_time, 3)

    if not idx_result["success"]:
        bresult["errors"].append(
            f"Indexing failed: {idx_result.get('errors', [])}"
        )
        return bresult

    # Read dimension from manifest
    manifest_result = get_manifest(workspace_id)
    if manifest_result["success"]:
        m = manifest_result["manifest"]
        bresult["model_name"]           = m.get("model_name")
        bresult["embedding_dimension"]  = m.get("embedding_dimension")

    # ── 2. Run all queries ─────────────────────────────────────────────────────
    all_query_results: list[dict] = []
    per_query_stats: list[dict]   = []

    for query_text in queries:
        t1 = time.monotonic()
        qresult = query_workspace(
            workspace_id=workspace_id,
            query_text=query_text,
            top_k=top_k,
        )
        query_time = time.monotonic() - t1

        # Extract top-k chunk IDs and source titles for comparison
        packets = qresult.get("evidence_packets", [])
        top_chunk_ids    = [p["chunk_id"]     for p in packets]
        top_source_ids   = [p["source_package_id"] for p in packets]
        top_source_titles = [p.get("source_title", "unknown") for p in packets]
        top_scores       = [p["similarity_score"]  for p in packets]

        # Lexical relevance proxy:
        # Count how many query terms appear in each top-k chunk text.
        # This is not ground-truth evaluation — it is a lexical signal proxy.
        query_terms = set(query_text.lower().split())
        lexical_hits = []
        for p in packets:
            chunk_text = (p.get("chunk_text") or "").lower()
            terms_found = sum(1 for t in query_terms if t in chunk_text)
            lexical_hits.append(terms_found)

        stats = {
            "query":              query_text,
            "query_time_sec":     round(query_time, 3),
            "retrieval_status":   qresult.get("retrieval_status"),
            "result_count":       qresult.get("result_count", 0),
            "top_chunk_ids":      top_chunk_ids,
            "top_source_titles":  top_source_titles,
            "top_scores":         top_scores,
            "score_max":          max(top_scores) if top_scores else None,
            "score_min":          min(top_scores) if top_scores else None,
            "score_mean":         (
                round(sum(top_scores) / len(top_scores), 4)
                if top_scores else None
            ),
            "lexical_hits_per_result": lexical_hits,
            "total_lexical_hits":      sum(lexical_hits),
            "warnings":           qresult.get("warnings", []),
        }

        all_query_results.append({
            "query":           query_text,
            "evidence_packets": [
                {
                    "rank":             i + 1,
                    "chunk_id":         p["chunk_id"],
                    "chunk_index":      p.get("chunk_index"),
                    "source_title":     p.get("source_title"),
                    "source_package_id": p.get("source_package_id"),
                    "similarity_score": p["similarity_score"],
                    "snippet":          (p.get("chunk_text") or "")[:150],
                }
                for i, p in enumerate(packets)
            ],
        })
        per_query_stats.append(stats)

    bresult["query_results"]   = all_query_results
    bresult["per_query_stats"] = per_query_stats
    bresult["success"]         = True
    return bresult


# ── Cross-backend comparison ───────────────────────────────────────────────────

def _compare_backends(
    per_backend: list[dict],
    queries: list[str],
    top_k: int,
) -> dict:
    """
    Compare results across two or more backends for the same queries.

    Reports:
    - Jaccard similarity of top-k chunk sets (ranking overlap)
    - Whether top-1 source agrees across backends
    - Score distribution comparison (mean scores)
    - Lexical signal comparison (which backend found more query-term matches)
    - Overall interpretation
    """
    comparison: dict = {
        "backend_names":      [b["backend"] for b in per_backend],
        "per_query":          [],
        "aggregate":          {},
        "interpretation":     [],
    }

    # Build query → results map per backend
    backend_stats: dict[str, list[dict]] = {}
    for b in per_backend:
        backend_stats[b["backend"]] = b.get("per_query_stats", [])

    # Per-query comparison
    for q_idx, query in enumerate(queries):
        q_comparison: dict = {
            "query":               query,
            "per_backend_summary": [],
            "jaccard_pairs":       [],
            "top1_source_agreement": None,
            "lexical_winner":      None,
            "notes":               [],
        }

        # Collect per-backend stats for this query
        this_query_stats: dict[str, dict] = {}
        for bname, stats_list in backend_stats.items():
            if q_idx < len(stats_list):
                this_query_stats[bname] = stats_list[q_idx]

        # Per-backend summary for this query
        for bname, stats in this_query_stats.items():
            q_comparison["per_backend_summary"].append({
                "backend":        bname,
                "retrieval_status": stats.get("retrieval_status"),
                "result_count":   stats.get("result_count"),
                "score_max":      stats.get("score_max"),
                "score_mean":     stats.get("score_mean"),
                "total_lexical_hits": stats.get("total_lexical_hits"),
                "top1_source":    stats.get("top_source_titles", [None])[0],
            })

        # Jaccard similarity of top-k chunk sets between all pairs
        bnames = list(this_query_stats.keys())
        for i in range(len(bnames)):
            for j in range(i + 1, len(bnames)):
                a_name, b_name = bnames[i], bnames[j]
                a_chunks = set(this_query_stats[a_name].get("top_chunk_ids", []))
                b_chunks = set(this_query_stats[b_name].get("top_chunk_ids", []))
                if a_chunks or b_chunks:
                    jaccard = len(a_chunks & b_chunks) / len(a_chunks | b_chunks)
                else:
                    jaccard = 1.0
                q_comparison["jaccard_pairs"].append({
                    "backends":         f"{a_name} vs {b_name}",
                    "jaccard_at_k":     round(jaccard, 3),
                    "shared_chunks":    len(a_chunks & b_chunks),
                    "union_chunks":     len(a_chunks | b_chunks),
                })

        # Top-1 source agreement
        top1_sources = [
            stats.get("top_source_titles", [None])[0]
            for stats in this_query_stats.values()
        ]
        unique_top1 = set(t for t in top1_sources if t is not None)
        q_comparison["top1_source_agreement"] = len(unique_top1) == 1

        # Lexical winner: which backend had more lexical hits in top-k?
        lexical_by_backend = {
            bname: stats.get("total_lexical_hits", 0)
            for bname, stats in this_query_stats.items()
        }
        if lexical_by_backend:
            max_lex = max(lexical_by_backend.values())
            winners = [b for b, s in lexical_by_backend.items() if s == max_lex]
            q_comparison["lexical_winner"] = winners[0] if len(winners) == 1 else "tie"

        # Notes
        for pair_info in q_comparison["jaccard_pairs"]:
            j = pair_info["jaccard_at_k"]
            if j == 1.0:
                q_comparison["notes"].append(
                    f"Ranking overlap: {pair_info['backends']} → perfect overlap "
                    f"(Jaccard=1.0). Both backends return identical top-{top_k} chunks."
                )
            elif j >= 0.5:
                q_comparison["notes"].append(
                    f"Ranking overlap: {pair_info['backends']} → high overlap "
                    f"(Jaccard={j:.2f}). Backends mostly agree on top results."
                )
            elif j >= 0.2:
                q_comparison["notes"].append(
                    f"Ranking overlap: {pair_info['backends']} → moderate overlap "
                    f"(Jaccard={j:.2f}). Backends show some ranking shifts."
                )
            else:
                q_comparison["notes"].append(
                    f"Ranking overlap: {pair_info['backends']} → low overlap "
                    f"(Jaccard={j:.2f}). Backends rank substantially different chunks first."
                )

        comparison["per_query"].append(q_comparison)

    # Aggregate stats
    if comparison["per_query"]:
        # Mean Jaccard across all queries and all backend pairs
        all_jaccards: list[float] = [
            pair["jaccard_at_k"]
            for q in comparison["per_query"]
            for pair in q.get("jaccard_pairs", [])
        ]
        comparison["aggregate"]["mean_jaccard_all_queries"] = (
            round(sum(all_jaccards) / len(all_jaccards), 3) if all_jaccards else None
        )
        comparison["aggregate"]["top1_agreement_rate"] = (
            sum(1 for q in comparison["per_query"] if q.get("top1_source_agreement"))
            / len(comparison["per_query"])
        )

        # Lexical winner tallies across queries
        lexical_tallies: dict[str, int] = {}
        for q in comparison["per_query"]:
            winner = q.get("lexical_winner")
            if winner and winner != "tie":
                lexical_tallies[winner] = lexical_tallies.get(winner, 0) + 1
        comparison["aggregate"]["lexical_winner_tally"] = lexical_tallies

        # Per-backend mean scores across all queries
        mean_scores: dict[str, list[float]] = {b["backend"]: [] for b in per_backend}
        for q in comparison["per_query"]:
            for bsummary in q.get("per_backend_summary", []):
                bname = bsummary["backend"]
                score_mean = bsummary.get("score_mean")
                if score_mean is not None:
                    mean_scores[bname].append(score_mean)
        comparison["aggregate"]["per_backend_mean_score"] = {
            bname: round(sum(scores) / len(scores), 4) if scores else None
            for bname, scores in mean_scores.items()
        }

    # Interpretation: honest summary of what the benchmark found
    interpretation = _generate_interpretation(
        comparison=comparison,
        per_backend=per_backend,
        top_k=top_k,
    )
    comparison["interpretation"] = interpretation

    return comparison


def _generate_interpretation(
    comparison: dict,
    per_backend: list[dict],
    top_k: int,
) -> list[str]:
    """
    Generate an honest, factual interpretation of the benchmark results.
    Does not overclaim. States what the data shows.
    """
    lines = []
    agg = comparison.get("aggregate", {})
    mean_jaccard = agg.get("mean_jaccard_all_queries")
    top1_rate    = agg.get("top1_agreement_rate")
    lex_tally    = agg.get("lexical_winner_tally", {})

    lines.append("=== BENCHMARK INTERPRETATION ===")
    lines.append("")
    lines.append(f"Backends compared: {[b['backend'] for b in per_backend]}")
    lines.append(f"Top-k evaluated:   {top_k}")
    lines.append("")

    if mean_jaccard is not None:
        if mean_jaccard >= 0.8:
            lines.append(
                f"Ranking overlap (mean Jaccard@{top_k} = {mean_jaccard:.2f}): "
                "Very high. Backends return nearly identical top-k chunks. "
                "The embedding backend choice has minimal impact on which chunks are returned."
            )
        elif mean_jaccard >= 0.4:
            lines.append(
                f"Ranking overlap (mean Jaccard@{top_k} = {mean_jaccard:.2f}): "
                "Moderate. Backends return partially overlapping top-k chunks. "
                "Some queries show material ranking shifts between backends."
            )
        else:
            lines.append(
                f"Ranking overlap (mean Jaccard@{top_k} = {mean_jaccard:.2f}): "
                "Low. Backends return substantially different top-k chunks. "
                "The embedding backend meaningfully changes what evidence is retrieved."
            )
        lines.append("")

    if top1_rate is not None:
        pct = round(top1_rate * 100)
        lines.append(
            f"Top-1 source agreement: {pct}% of queries return the same top source "
            f"across all backends tested."
        )
        lines.append("")

    if lex_tally:
        winner = max(lex_tally, key=lex_tally.get)
        lines.append(
            f"Lexical signal (proxy relevance): '{winner}' ranked more query-term-matching "
            f"chunks in the top-{top_k} more often ({lex_tally[winner]}/"
            f"{sum(lex_tally.values())} queries where a winner was clear)."
        )
        lines.append("")

    # Backend-specific notes
    for b in per_backend:
        bname = b["backend"]
        dim   = b.get("embedding_dimension")
        lines.append(
            f"Backend '{bname}' (dim={dim}): "
            + _backend_quality_note(bname)
        )

    lines.append("")
    lines.append(
        "Methodology note: relevance is estimated using lexical keyword overlap "
        "between query terms and chunk text. Without human relevance judgments, "
        "this is a proxy signal — not a ground-truth evaluation. Ranking differences "
        "shown here reflect lexical signal strength, not confirmed semantic quality."
    )

    return lines


def _backend_quality_note(backend_name: str) -> str:
    """Return a brief honest quality characterization for a backend."""
    notes = {
        "local_stub":  (
            "Deterministic hash embedding. No semantic or lexical signal. "
            "Rankings for real queries are essentially arbitrary. "
            "Correct use: testing the index/retrieval contract, not real information retrieval."
        ),
        "local_word":  (
            "Word frequency hash projection. Lexical signal present: "
            "texts sharing vocabulary rank higher. "
            "No cross-word semantic understanding (synonyms are unrelated). "
            "Better than stub for real queries; limited by vocabulary overlap only."
        ),
        "openai":      (
            "Trained semantic embedding model. Captures meaning beyond vocabulary overlap. "
            "Requires external API call with user-supplied credentials. "
            "Highest quality but not local-first."
        ),
    }
    return notes.get(backend_name, "No description available.")


# ── Internal helpers ───────────────────────────────────────────────────────────

def _empty_result(workspace_id: str, queries: list[str], top_k: int) -> dict:
    return {
        "workspace_id":             workspace_id,
        "queries":                  queries,
        "top_k":                    top_k,
        "backends_requested":       [],
        "backends_run":             [],
        "backends_skipped":         [],
        "original_backend":         None,
        "restored_to_original":     False,
        "per_backend_results":      [],
        "cross_backend_comparison": None,
        "benchmark_status":         "unknown",
        "warnings":                 [],
        "errors":                   [],
    }
