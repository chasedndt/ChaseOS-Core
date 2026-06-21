"""
retriever.py — SIC Phase 7 Pass 5 / Pass 7
Local Retrieval Contract + Evidence Query Layer for the ChaseOS Source Intelligence Core.

Entry point:
    query_workspace(workspace_id, query_text, top_k=5, ...) -> dict

Pass 7 addition:
    benchmark subcommand — compare retrieval quality across embedding backends.

Architecture constraints (enforced):
  - Local-first. local_stub and local_word require no external setup.
  - Vectors are read from per-source sidecar files produced in index_manager.
  - Source package JSONs are read for chunk texts and metadata.
  - No prose answer generation. No note drafts. No markdown promotion.
  - No writes to 02_KNOWLEDGE/, 01_PROJECTS/, 00_HOME/, or the vault.
  - Retrieval results are evidence candidates, NOT canonical knowledge.
  - Nothing in this pass bypasses the Gate.

CLI:
    python -m runtime.source_intelligence.retrieval.retriever query \\
      --workspace <workspace_id> --query <text> [--top-k N]

    python -m runtime.source_intelligence.retrieval.retriever inspect \\
      --workspace <workspace_id>

    python -m runtime.source_intelligence.retrieval.retriever benchmark \\
      --workspace <workspace_id> --queries-file <path> [--backends local_stub,local_word]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ..indexes.embedder import EmbedderBase, EmbedderError, LocalStubEmbedder, get_embedder
from .similarity import rank_chunks

try:
    from runtime.aor.context_governance import cgl_trust_level_from_sic
    _CGL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CGL_AVAILABLE = False
    def cgl_trust_level_from_sic(v):  # type: ignore[misc]
        return "untrusted"

# ── Paths ─────────────────────────────────────────────────────────────────────

_THIS_FILE = Path(__file__).resolve()
# retrieval/retriever.py is at: vault/runtime/source_intelligence/retrieval/retriever.py
# parents[0]=retrieval/ parents[1]=source_intelligence/ parents[2]=runtime/ parents[3]=vault/
_VAULT_ROOT = _THIS_FILE.parents[3]
_SIC_WORKSPACES = _VAULT_ROOT / "runtime" / "source_intelligence" / "workspaces"

_WORKSPACE_FILENAME = "workspace.json"
_MANIFEST_FILENAME = "index_manifest.json"

# ── Retrieval status constants ─────────────────────────────────────────────────
# Returned in result["retrieval_status"]. Never raised as exceptions.

_STATUS_OK          = "ok"               # clean index, results returned
_STATUS_OK_PARTIAL  = "ok-partial"       # some sources skipped; results from indexed only
_STATUS_OK_STALE    = "ok-stale"         # index stale; results returned with warning
_STATUS_NOT_INDEXED = "not-indexed"      # no index manifest or manifest says not-indexed
_STATUS_EMPTY       = "empty-workspace"  # no source packages attached
_STATUS_NO_WS       = "no-workspace"     # workspace.json not found or unreadable
_STATUS_QUERY_FAIL  = "query-failed"     # embedder failure on query text
_STATUS_NO_VECTORS  = "no-vectors"       # all sidecars missing/unreadable


# ── Public API ────────────────────────────────────────────────────────────────

def query_workspace(
    workspace_id: str,
    query_text: str,
    top_k: int = 5,
    embedder: EmbedderBase | None = None,
    model_name: str | None = None,
    provider_name: str | None = None,
) -> dict:
    """
    Query a workspace by embedding similarity against all indexed chunks.

    Loads the workspace record, reads the index manifest, embeds the query
    text, scores every available chunk vector via cosine similarity, and
    returns the top-k evidence packets with full source/chunk provenance.

    This function never raises. All failure modes produce a result dict with
    a descriptive retrieval_status and populated errors/warnings lists.

    Args:
        workspace_id:  Workspace slug (the directory name under workspaces/).
        query_text:    Natural language query or topic string to search for.
        top_k:         Maximum number of evidence packets to return (default 5).
        embedder:      Optional pre-constructed EmbedderBase instance.
                       If None, the embedder is resolved from the workspace
                       index manifest (uses the model that produced the index).
        model_name:    Model override for get_embedder(). Ignored if embedder given.
        provider_name: Provider override for get_embedder(). Ignored if embedder given.

    Returns:
        dict with keys:
            workspace_id          — the queried workspace slug
            query_text            — the original query string
            retrieval_status      — one of: ok / ok-partial / ok-stale /
                                    not-indexed / empty-workspace / no-workspace /
                                    query-failed / no-vectors
            provider_name         — embedder provider used for this query
            model_name            — embedder model used for this query
            source_count_considered  — number of source packages scored
            chunk_count_considered   — total chunks scored across all sources
            top_k                 — requested result cap
            result_count          — actual number of evidence packets returned
            evidence_packets      — list of evidence packet dicts (see below)
            warnings              — list of non-fatal warning strings
            errors                — list of error strings (present when status != ok)

        Each evidence packet contains:
            workspace_id, source_package_id, source_title, source_slug,
            source_path, source_type, chunk_id, chunk_index, similarity_score,
            chunk_text, section_heading, char_count, provider_name, model_name,
            user_trust_level, injection_scan_status, sidecar_path
    """
    result = _empty_result(workspace_id, query_text, top_k)

    # ── 1. Load workspace ──────────────────────────────────────────────────────
    ws_dir = _SIC_WORKSPACES / workspace_id
    ws_path = ws_dir / _WORKSPACE_FILENAME

    if not ws_path.exists():
        result["retrieval_status"] = _STATUS_NO_WS
        result["errors"].append(
            f"workspace.json not found at {ws_path}. "
            "Run create_workspace() and add_source_to_workspace() first."
        )
        return result

    try:
        workspace = json.loads(ws_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["retrieval_status"] = _STATUS_NO_WS
        result["errors"].append(f"Could not read workspace.json: {exc}")
        return result

    source_ids: list[str] = workspace.get("source_package_ids", [])
    source_refs: dict = workspace.get("source_refs", {})

    if not source_ids:
        result["retrieval_status"] = _STATUS_EMPTY
        result["errors"].append(
            f"Workspace '{workspace_id}' has no attached source packages. "
            "Use add_source_to_workspace() first."
        )
        return result

    # ── 2. Load index manifest ─────────────────────────────────────────────────
    manifest_path = ws_dir / "indexes" / _MANIFEST_FILENAME

    if not manifest_path.exists():
        result["retrieval_status"] = _STATUS_NOT_INDEXED
        result["errors"].append(
            f"No index manifest found for workspace '{workspace_id}'. "
            "Run index_workspace() first."
        )
        return result

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["retrieval_status"] = _STATUS_NOT_INDEXED
        result["errors"].append(f"Could not read index manifest: {exc}")
        return result

    manifest_status = manifest.get("index_status", "not-indexed")

    if manifest_status == "not-indexed":
        result["retrieval_status"] = _STATUS_NOT_INDEXED
        result["errors"].append(
            f"Workspace '{workspace_id}' manifest reports index_status='not-indexed'. "
            "Run index_workspace() first."
        )
        return result

    if manifest_status in ("stale", "partial"):
        result["warnings"].append(
            f"Workspace index status is '{manifest_status}'. "
            "Results may be incomplete or outdated. Run index_workspace() to refresh."
        )

    # ── 3. Resolve embedder ────────────────────────────────────────────────────
    # Default: use the same provider/model/dimension that produced the index.
    # This ensures query vectors are in the same embedding space as chunk vectors.
    index_provider  = manifest.get("provider_name", "local_stub")
    index_model     = manifest.get("model_name", "local-test-embedding-v1")
    index_dimension = manifest.get("embedding_dimension", 64)

    active_provider = provider_name or index_provider
    active_model    = model_name    or index_model

    if embedder is None:
        try:
            embedder = get_embedder(
                adapter_name=active_provider,
                model_name=active_model,
                dimension=index_dimension,
            )
        except EmbedderError as exc:
            # Requested provider unavailable — fall back to local_stub
            result["warnings"].append(
                f"Provider '{active_provider}' unavailable: {exc}. "
                f"Falling back to local_stub (dimension={index_dimension})."
            )
            active_provider = "local_stub"
            embedder = LocalStubEmbedder(dimension=index_dimension)

    result["provider_name"] = embedder.name
    result["model_name"]    = embedder.model_name

    # Warn on dimension mismatch — scores will be meaningless
    if hasattr(embedder, "dimension") and embedder.dimension != index_dimension:
        result["warnings"].append(
            f"Embedder dimension ({embedder.dimension}) differs from index "
            f"dimension ({index_dimension}). Similarity scores will be unreliable. "
            "Use the same model for indexing and querying."
        )

    # ── 4. Embed the query ─────────────────────────────────────────────────────
    try:
        query_vector: list[float] = embedder.embed([query_text])[0]
    except EmbedderError as exc:
        result["retrieval_status"] = _STATUS_QUERY_FAIL
        result["errors"].append(f"Failed to embed query: {exc}")
        return result

    # ── 5. Score all indexed chunks ────────────────────────────────────────────
    # Build a fast lookup from manifest: pkg_id → manifest source entry
    manifest_sources: dict[str, dict] = {
        s["source_package_id"]: s for s in manifest.get("sources", [])
    }

    # Accumulate (score, pkg_id, chunk_id, evidence_meta) across all sources
    all_hits: list[tuple[float, str, str, dict]] = []
    skipped: list[str] = []
    sources_considered = 0
    chunks_considered  = 0

    for pkg_id in source_ids:
        ms = manifest_sources.get(pkg_id)

        # Source not in manifest — workspace and index are out of sync
        if ms is None:
            result["warnings"].append(
                f"Source {pkg_id} is attached to workspace but absent from index manifest. "
                "Re-run index_workspace() to synchronize."
            )
            skipped.append(pkg_id)
            continue

        # Source not yet embedded
        if ms.get("index_status") != "embedded":
            result["warnings"].append(
                f"Source '{ms.get('title') or pkg_id}' "
                f"(status: {ms.get('index_status', 'unknown')}) is not indexed. Skipping."
            )
            skipped.append(pkg_id)
            continue

        # ── Load vector sidecar ────────────────────────────────────────────────
        # Canonical path derived from workspace directory (portable across systems).
        sidecar_path = ws_dir / "indexes" / f"{pkg_id}.vectors.json"

        if not sidecar_path.exists():
            # Try stored absolute path as fallback (for same-machine continuity)
            stored = ms.get("sidecar_path")
            if stored:
                alt = Path(stored)
                if alt.exists():
                    sidecar_path = alt

        if not sidecar_path.exists():
            result["warnings"].append(
                f"Vector sidecar missing for source '{ms.get('title') or pkg_id}'. "
                "Re-run index_workspace() to rebuild."
            )
            skipped.append(pkg_id)
            continue

        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result["warnings"].append(
                f"Could not read sidecar for {pkg_id}: {exc}. Skipping."
            )
            skipped.append(pkg_id)
            continue

        vectors: dict[str, list[float]] = sidecar.get("vectors", {})
        if not vectors:
            result["warnings"].append(
                f"Sidecar for '{ms.get('title') or pkg_id}' has no vectors. "
                "Re-run index_workspace()."
            )
            skipped.append(pkg_id)
            continue

        # ── Load source package for chunk texts ────────────────────────────────
        ref = source_refs.get(pkg_id, {})
        pkg = _load_source_package(pkg_id, ref, ws_dir)

        chunk_lookup: dict[str, dict] = {}
        if pkg is not None:
            for c in pkg.get("chunks", []):
                cid = c.get("chunk_id")
                if cid:
                    chunk_lookup[cid] = c

        # ── Score all chunks in this source ────────────────────────────────────
        scored_chunks = rank_chunks(query_vector, vectors)
        chunks_considered += len(scored_chunks)

        for chunk_id, score in scored_chunks:
            chunk = chunk_lookup.get(chunk_id, {})
            evidence = {
                "workspace_id":          workspace_id,
                "source_package_id":     pkg_id,
                "source_title":          ref.get("title") or (pkg.get("title") if pkg else None) or pkg_id,
                "source_slug":           _make_slug(pkg_id, ref, pkg),
                "source_path":           ref.get("source_package_path") or (pkg.get("origin_path") if pkg else None),
                "source_type":           ref.get("source_type") or (pkg.get("source_type") if pkg else None),
                "chunk_id":              chunk_id,
                "chunk_index":           chunk.get("chunk_index"),
                "similarity_score":      round(score, 6),
                "chunk_text":            chunk.get("text", ""),
                "section_heading":       chunk.get("section_heading"),
                "char_count":            chunk.get("char_count"),
                "provider_name":         sidecar.get("provider_name"),
                "model_name":            sidecar.get("model_name"),
                "user_trust_level":      ref.get("user_trust_level") or (pkg.get("user_trust_level") if pkg else None),
                "injection_scan_status": ref.get("injection_scan_status") or (pkg.get("injection_scan_status") if pkg else None),
                "sidecar_path":          str(sidecar_path),
                "cgl_trust_level":       cgl_trust_level_from_sic(
                    ref.get("user_trust_level") or (pkg.get("user_trust_level") if pkg else None)
                ),
            }
            all_hits.append((score, pkg_id, chunk_id, evidence))

        sources_considered += 1

    result["source_count_considered"] = sources_considered
    result["chunk_count_considered"]  = chunks_considered

    # ── 6. Handle empty results ────────────────────────────────────────────────
    if not all_hits:
        if skipped:
            result["retrieval_status"] = _STATUS_NO_VECTORS
            result["errors"].append(
                f"No readable vector sidecars found ({len(skipped)} source(s) skipped). "
                "Re-run index_workspace() to rebuild the index."
            )
        else:
            result["retrieval_status"] = _STATUS_EMPTY
            result["errors"].append("No chunks available to score.")
        return result

    # ── 7. Sort globally and select top-k ──────────────────────────────────────
    # Primary: score descending. Secondary: (pkg_id, chunk_id) ascending for determinism.
    all_hits.sort(key=lambda x: (-x[0], x[1], x[2]))
    top_hits = all_hits[:top_k]

    result["evidence_packets"] = [ev for _, _, _, ev in top_hits]
    result["result_count"]     = len(top_hits)

    # ── 8. Final status ────────────────────────────────────────────────────────
    if skipped:
        result["retrieval_status"] = _STATUS_OK_PARTIAL
    elif manifest_status == "stale":
        result["retrieval_status"] = _STATUS_OK_STALE
    else:
        result["retrieval_status"] = _STATUS_OK

    return result


# ── Internal helpers ───────────────────────────────────────────────────────────

def _empty_result(workspace_id: str, query_text: str, top_k: int) -> dict:
    """Return a zero-state result dict."""
    return {
        "workspace_id":             workspace_id,
        "query_text":               query_text,
        "retrieval_status":         "unknown",
        "provider_name":            None,
        "model_name":               None,
        "source_count_considered":  0,
        "chunk_count_considered":   0,
        "top_k":                    top_k,
        "result_count":             0,
        "evidence_packets":         [],
        "warnings":                 [],
        "errors":                   [],
    }


def _load_source_package(pkg_id: str, ref: dict, ws_dir: Path) -> dict | None:
    """
    Load a source package JSON file for chunk text and metadata lookup.

    Resolution order:
    1. Path stored in source_refs (may be an absolute path from a prior run).
    2. Scan workspace source_packages/ directory for matching id.
    3. Scan workspace sources/ directory (legacy naming) for matching id.

    Returns the parsed dict, or None if not found or unreadable.
    """
    # Try stored path first
    stored = ref.get("source_package_path")
    if stored:
        p = Path(stored)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass  # fall through to scan

    # Scan workspace subdirectories
    for sub in ("source_packages", "sources"):
        d = ws_dir / sub
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("id") == pkg_id:
                    return data
            except (OSError, json.JSONDecodeError):
                continue

    return None


def _make_slug(pkg_id: str, ref: dict, pkg: dict | None) -> str:
    """
    Return a short human-readable slug for a source package.

    Tries the title from source_refs, then the package title, then
    falls back to the first 8 characters of the package ID.
    """
    title = ref.get("title") or (pkg.get("title") if pkg else None)
    if title:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return slug[:60]
    return pkg_id[:8]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="retriever",
        description="SIC Phase 7 — local workspace retrieval and benchmark.",
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    # query
    q = sub.add_parser("query", help="Query a workspace and return top-k evidence packets")
    q.add_argument("--workspace", required=True, metavar="WORKSPACE_ID",
                   help="Workspace slug to query")
    q.add_argument("--query",     required=True, metavar="TEXT",
                   help="Query string to embed and score against indexed chunks")
    q.add_argument("--top-k",    type=int, default=5, metavar="N",
                   help="Maximum evidence packets to return (default: 5)")
    q.add_argument("--full",     action="store_true",
                   help="Print full chunk text for each result")

    # inspect
    i = sub.add_parser("inspect", help="Inspect workspace index state without querying")
    i.add_argument("--workspace", required=True, metavar="WORKSPACE_ID")

    # benchmark (Pass 7)
    b = sub.add_parser(
        "benchmark",
        help="Compare retrieval quality across embedding backends on a real workspace",
    )
    b.add_argument("--workspace",    required=True, metavar="WORKSPACE_ID")
    b.add_argument("--queries-file", required=False, default=None, metavar="PATH",
                   help="Text file with one query per line. "
                        "If not provided, uses built-in test queries.")
    b.add_argument("--query",        action="append", dest="extra_queries", metavar="TEXT",
                   help="Add a single query string (repeatable). "
                        "Combined with --queries-file if both provided.")
    b.add_argument("--backends",     default="local_stub,local_word", metavar="BACKEND_LIST",
                   help="Comma-separated list of backends to compare "
                        "(default: local_stub,local_word)")
    b.add_argument("--top-k",        type=int, default=5, metavar="N")
    b.add_argument("--no-restore",   action="store_true",
                   help="Leave workspace in last-benchmarked-backend state "
                        "(default: restore to original backend after benchmark)")
    b.add_argument("--json",         action="store_true",
                   help="Output full benchmark result as JSON")

    return p


def _print_query_result(result: dict, full_text: bool = False) -> None:
    """Print a query result to stdout in a readable format."""
    print()
    status = result["retrieval_status"]
    ws     = result["workspace_id"]
    print(f"Query result — workspace '{ws}'")
    print(f"  Status:           {status}")
    print(f"  Query:            {result['query_text']!r}")
    print(f"  Provider:         {result['provider_name']}  |  Model: {result['model_name']}")
    print(f"  Sources scored:   {result['source_count_considered']}")
    print(f"  Chunks scored:    {result['chunk_count_considered']}")
    print(f"  Top-k requested:  {result['top_k']}")
    print(f"  Results returned: {result['result_count']}")

    if result["warnings"]:
        print()
        print("  Warnings:")
        for w in result["warnings"]:
            print(f"    [WARN] {w}")

    if result["errors"]:
        print()
        print("  Errors:")
        for e in result["errors"]:
            print(f"    [ERR]  {e}")

    packets = result.get("evidence_packets", [])
    if packets:
        print()
        print(f"  Top {len(packets)} evidence packet(s):")
        for i, pkt in enumerate(packets, 1):
            print()
            print(f"  [{i}] score={pkt['similarity_score']:.4f}  "
                  f"source='{pkt['source_title']}'  "
                  f"chunk={pkt['chunk_index']}")
            print(f"       source_type:   {pkt['source_type']}")
            print(f"       chunk_id:      {pkt['chunk_id']}")
            print(f"       trust:         {pkt['user_trust_level']}  |  "
                  f"scan: {pkt['injection_scan_status']}")
            if pkt.get("section_heading"):
                print(f"       heading:       {pkt['section_heading']}")
            if full_text:
                text = pkt.get("chunk_text", "")
                print(f"       text ({pkt.get('char_count', '?')} chars):")
                # Indent and wrap at 80 chars
                for line in text.splitlines():
                    print(f"         {line}")
            else:
                snippet = (pkt.get("chunk_text") or "")[:120].replace("\n", " ")
                print(f"       snippet:       {snippet!r}")
    print()


def _print_inspect_result(workspace_id: str) -> None:
    """Print workspace index state for inspection."""
    ws_dir = _SIC_WORKSPACES / workspace_id
    ws_path = ws_dir / _WORKSPACE_FILENAME
    manifest_path = ws_dir / "indexes" / _MANIFEST_FILENAME

    print()
    print(f"Workspace inspect — '{workspace_id}'")

    if not ws_path.exists():
        print(f"  ERROR: workspace.json not found at {ws_path}")
        print()
        return

    try:
        ws = json.loads(ws_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ERROR: could not read workspace.json: {exc}")
        print()
        return

    print(f"  Name:             {ws.get('name')}")
    print(f"  Domain:           {ws.get('domain')}")
    print(f"  Source count:     {ws.get('source_count', len(ws.get('source_package_ids', [])))}")
    print(f"  Index status:     {ws.get('index_status')}")
    print(f"  Embedding model:  {ws.get('embedding_model')}")
    print(f"  Last indexed:     {ws.get('last_indexed_at')}")

    if not manifest_path.exists():
        print(f"  Manifest:         NOT FOUND — run index_workspace() first")
        print()
        return

    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  Manifest:         ERROR reading — {exc}")
        print()
        return

    print()
    print(f"  Index manifest:")
    print(f"    Status:         {m.get('index_status')}")
    print(f"    Provider:       {m.get('provider_name')}  |  Model: {m.get('model_name')}")
    print(f"    Dimension:      {m.get('embedding_dimension')}")
    print(f"    Sources:        {m.get('indexed_source_count')}/{m.get('source_count')} indexed, "
          f"{m.get('stale_source_count')} stale, {m.get('failed_source_count')} failed")
    print(f"    Chunks:         {m.get('indexed_chunk_count')}/{m.get('total_chunk_count')} indexed")
    print()
    print(f"  Sources:")
    for src in m.get("sources", []):
        sidecar_exists = Path(src.get("sidecar_path", "")).exists() if src.get("sidecar_path") else False
        # Also check canonical path
        pkg_id = src.get("source_package_id", "")
        canonical_sidecar = ws_dir / "indexes" / f"{pkg_id}.vectors.json"
        sidecar_ok = sidecar_exists or canonical_sidecar.exists()
        sidecar_flag = "OK" if sidecar_ok else "MISSING"
        print(f"    [{src.get('index_status')}] {src.get('title') or src.get('source_package_id')}")
        print(f"           chunks: {src.get('indexed_chunk_count')}/{src.get('chunk_count')} "
              f"| sidecar: {sidecar_flag}")
    print()


def main() -> None:
    parser = _build_cli()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "query":
        result = query_workspace(
            workspace_id=args.workspace,
            query_text=args.query,
            top_k=args.top_k,
        )
        _print_query_result(result, full_text=args.full)
        ok = result["retrieval_status"] in (_STATUS_OK, _STATUS_OK_PARTIAL, _STATUS_OK_STALE)
        sys.exit(0 if ok else 1)

    elif args.command == "inspect":
        _print_inspect_result(args.workspace)
        sys.exit(0)

    elif args.command == "benchmark":
        import json as _json
        from .benchmark import run_benchmark

        # Collect queries
        queries: list[str] = []

        if args.queries_file:
            qpath = Path(args.queries_file)
            if not qpath.exists():
                print(f"\nERROR: queries file not found: {qpath}\n")
                sys.exit(1)
            lines = qpath.read_text(encoding="utf-8").splitlines()
            queries.extend(q.strip() for q in lines if q.strip())

        if args.extra_queries:
            queries.extend(args.extra_queries)

        if not queries:
            # Built-in test queries for the phase7-test workspace
            queries = [
                "market microstructure order flow imbalance",
                "multi-agent AI systems tool use patterns",
                "cryptocurrency perpetual futures funding rate",
                "trading strategy analysis",
            ]
            print(
                "\nNo queries provided — using built-in test queries for phase7-test workspace."
            )

        # Parse backend list
        backends = [b.strip() for b in args.backends.split(",") if b.strip()]

        result = run_benchmark(
            workspace_id=args.workspace,
            queries=queries,
            backends=backends,
            top_k=args.top_k,
            restore_original=not args.no_restore,
        )

        if args.json:
            print(_json.dumps(result, indent=2, ensure_ascii=False))
        else:
            _print_benchmark_result(result)

        ok = result["benchmark_status"] in ("complete", "partial")
        sys.exit(0 if ok else 1)


def _print_benchmark_result(result: dict) -> None:
    """Print benchmark results in a readable format."""
    print()
    print(f"=== SIC RETRIEVAL QUALITY BENCHMARK ===")
    print(f"Workspace:         {result['workspace_id']}")
    print(f"Status:            {result['benchmark_status']}")
    print(f"Backends run:      {result['backends_run']}")
    print(f"Backends skipped:  {[b['backend'] for b in result['backends_skipped']]}")
    print(f"Original backend:  {result['original_backend']}")
    print(f"Restored:          {result['restored_to_original']}")
    print(f"Queries:           {len(result['queries'])}")
    print(f"Top-k:             {result['top_k']}")
    print()

    if result["warnings"]:
        print("Warnings:")
        for w in result["warnings"]:
            print(f"  [WARN] {w}")
        print()

    if result["errors"]:
        print("Errors:")
        for e in result["errors"]:
            print(f"  [ERR] {e}")
        print()

    # Per-backend summary
    for b in result.get("per_backend_results", []):
        print(f"--- Backend: {b['backend']} ---")
        print(f"  Model:      {b.get('model_name')}  (dim={b.get('embedding_dimension')})")
        print(f"  Index time: {b.get('index_time_sec')}s")
        print()
        for qs in b.get("per_query_stats", []):
            print(f"  Query: {qs['query']!r}")
            print(f"    Status:         {qs['retrieval_status']}")
            print(f"    Results:        {qs['result_count']}")
            print(f"    Score max/mean: {qs['score_max']} / {qs['score_mean']}")
            print(f"    Lexical hits:   {qs['total_lexical_hits']}")
            print(f"    Top sources:    {qs['top_source_titles'][:3]}")
            print()

    # Cross-backend comparison
    cbc = result.get("cross_backend_comparison")
    if cbc:
        print("--- Cross-Backend Comparison ---")
        agg = cbc.get("aggregate", {})
        print(f"  Mean Jaccard@k:         {agg.get('mean_jaccard_all_queries')}")
        print(f"  Top-1 agreement rate:   {agg.get('top1_agreement_rate')}")
        print(f"  Lexical winner tally:   {agg.get('lexical_winner_tally')}")
        print(f"  Mean scores per backend: {agg.get('per_backend_mean_score')}")
        print()

        print("Per-query comparisons:")
        for q in cbc.get("per_query", []):
            print(f"  Query: {q['query']!r}")
            for pair in q.get("jaccard_pairs", []):
                print(f"    {pair['backends']}: Jaccard={pair['jaccard_at_k']:.3f} "
                      f"({pair['shared_chunks']} shared / {pair['union_chunks']} union)")
            for note in q.get("notes", []):
                print(f"    NOTE: {note}")
            print()

        # Interpretation
        if cbc.get("interpretation"):
            print()
            for line in cbc["interpretation"]:
                print(f"  {line}")
        print()


if __name__ == "__main__":
    main()
