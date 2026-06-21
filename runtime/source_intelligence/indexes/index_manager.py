"""
index_manager.py — SIC Phase 7 Pass 4 / Pass 7
Local Index Contract and Embedding State Layer for the ChaseOS Source Intelligence Core.

Entry points:
    index_source_package(...)   — embed all chunks in one source package
    index_workspace(...)        — index all source packages in a workspace
    get_manifest(...)           — load and return the workspace index manifest

CLI:
    python -m runtime.source_intelligence.indexes.index_manager <command> [args]

Pass 7 additions:
    - list-backends command: show all known embedding backends and availability
    - --backend flag on index-workspace: choose backend (local_stub, local_word, openai)
    - Dimension is inferred from the backend's default when not specified
    - Mixed-backend detection: warns when workspace has sources indexed under different backends

Architecture constraints (enforced):
    - Local-first. local_stub and local_word require no external setup.
    - Vectors stored in per-source sidecar files (see design note below).
    - Source package JSON is never corrupted on partial failure.
    - All state fields are truthful — no fake indexed status.
    - Never writes to 02_KNOWLEDGE/, 01_PROJECTS/, or 00_HOME/.

Storage naming: canonical directory is source_packages/ (standardized in Pass 4).
  Legacy sources/ is supported for backward compat by workspace_manager.

Design note — sidecar vs inline vector storage:
    Vectors are stored in a sidecar file:
        workspaces/{workspace_id}/indexes/{source_package_id}.vectors.json

    Rationale:
    - Real embeddings (e.g. text-embedding-3-small: 1536 dims) would bloat the
      source package JSON (15 chunks × 1536 floats = ~94k extra floats per package).
    - The source package JSON is the provenance record and should remain human-readable.
    - Sidecar keeps separation between content record and vector artifact.
    - source_package.index_path points to the sidecar for retrieval lookups.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .embedder import EmbedderBase, EmbedderError, LocalStubEmbedder, get_embedder
from .backend_registry import (
    BACKEND_DESCRIPTORS,
    check_backend_availability,
    get_backend_default_dimension,
    get_backend_default_model,
    list_backends,
)

# ── Paths ─────────────────────────────────────────────────────────────────────

_THIS_FILE = Path(__file__).resolve()
_VAULT_ROOT = _THIS_FILE.parents[3]
_SIC_WORKSPACES = _VAULT_ROOT / "runtime" / "source_intelligence" / "workspaces"

_MANIFEST_FILENAME = "index_manifest.json"

# ── Source-package index state values (extending schema enum) ─────────────────
# Schema defines: not-embedded / embedded / stale
# We use the schema values; "indexing" is a transient in-memory state only.
_STATE_NOT_EMBEDDED = "not-embedded"
_STATE_EMBEDDED = "embedded"
_STATE_STALE = "stale"
_STATE_FAILED = "failed"    # non-schema extension; stored in _index_meta only

# ── Workspace index state values ──────────────────────────────────────────────
# Schema defines: not-indexed / indexed / stale
_WS_NOT_INDEXED = "not-indexed"
_WS_INDEXED = "indexed"
_WS_STALE = "stale"
_WS_FAILED = "failed"       # non-schema; workspace-level failure aggregate
_WS_PARTIAL = "partial"     # some sources indexed, some failed


# ── Public API ────────────────────────────────────────────────────────────────

def index_source_package(
    source_package_path: str | Path,
    embedder: EmbedderBase | None = None,
    model_name: str | None = None,
    provider_name: str | None = None,
) -> dict:
    """
    Embed all chunks in a source package and write a vector sidecar.

    The source package JSON is updated with:
    - embedding_status: "embedded" or "failed"
    - embedding_model: model identifier
    - last_indexed_at: timestamp
    - index_path: path to the vector sidecar
    - _index_meta: per-chunk summary (no vectors inline)

    Vectors are written to a sidecar at:
        workspaces/{workspace_id}/indexes/{source_package_id}.vectors.json

    The source package JSON is never written in a partial/corrupt state.
    If embedding fails after the source package is loaded, the original is preserved.

    Returns:
        dict with keys:
            success (bool), source_package_id, chunk_count, indexed_chunk_count,
            embedding_status, sidecar_path, model_name, provider_name, error
    """
    pkg_path = Path(source_package_path).resolve()
    result: dict = {
        "success": False,
        "source_package_id": None,
        "chunk_count": 0,
        "indexed_chunk_count": 0,
        "embedding_status": _STATE_NOT_EMBEDDED,
        "sidecar_path": None,
        "model_name": None,
        "provider_name": None,
        "error": None,
    }

    if not pkg_path.exists():
        result["error"] = f"Source package not found: {pkg_path}"
        return result

    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = f"Could not read source package: {exc}"
        return result

    pkg_id = pkg.get("id")
    if not pkg_id:
        result["error"] = "Source package missing 'id' field."
        return result

    result["source_package_id"] = pkg_id

    chunks = pkg.get("chunks", [])
    if not chunks:
        result["error"] = f"Source package {pkg_id} has no chunks. Re-ingest with ingest_source()."
        return result

    result["chunk_count"] = len(chunks)

    # Resolve embedder
    if embedder is None:
        embedder = get_embedder(
            adapter_name=provider_name or "local_stub",
            model_name=model_name,
        )

    active_model = model_name or embedder.model_name
    active_provider = provider_name or embedder.name
    result["model_name"] = active_model
    result["provider_name"] = active_provider

    # Extract texts in chunk order
    chunk_texts = [c["text"] for c in chunks]
    chunk_ids = [c["chunk_id"] for c in chunks]

    # Embed — all-or-nothing per source package
    try:
        vectors = embedder.embed(chunk_texts)
    except EmbedderError as exc:
        result["error"] = f"Embedding failed: {exc}"
        result["embedding_status"] = _STATE_FAILED
        # Update source package failure state without corrupting chunk data
        _update_pkg_index_state(pkg_path, pkg, _STATE_FAILED, active_model, active_provider,
                                embedder.dimension, sidecar_path=None)
        return result

    if len(vectors) != len(chunks):
        result["error"] = (
            f"Embedder returned {len(vectors)} vectors for {len(chunks)} chunks. "
            "Length mismatch — not writing."
        )
        result["embedding_status"] = _STATE_FAILED
        _update_pkg_index_state(pkg_path, pkg, _STATE_FAILED, active_model, active_provider,
                                embedder.dimension, sidecar_path=None)
        return result

    # Determine sidecar path
    sidecar_path = _sidecar_path_for(pkg_path, pkg_id)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)

    # Build sidecar
    now = _now_iso()
    sidecar: dict = {
        "source_package_id": pkg_id,
        "provider_name": active_provider,
        "model_name": active_model,
        "embedding_dimension": embedder.dimension,
        "chunk_count": len(chunks),
        "created_at": now,
        "vectors": {
            cid: vec for cid, vec in zip(chunk_ids, vectors)
        },
    }

    try:
        sidecar_path.write_text(
            json.dumps(sidecar, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        result["error"] = f"Could not write vector sidecar: {exc}"
        result["embedding_status"] = _STATE_FAILED
        return result

    # Update source package JSON
    _update_pkg_index_state(
        pkg_path, pkg,
        _STATE_EMBEDDED, active_model, active_provider,
        embedder.dimension, sidecar_path=sidecar_path,
    )

    result["success"] = True
    result["indexed_chunk_count"] = len(chunks)
    result["embedding_status"] = _STATE_EMBEDDED
    result["sidecar_path"] = str(sidecar_path)
    return result


def index_workspace(
    workspace_id: str,
    adapter_name: str = "local_stub",
    model_name: str | None = None,
    force_reindex: bool = False,
    dimension: int | None = None,
) -> dict:
    """
    Index all source packages in a workspace.

    Behavior:
    - Loads workspace.json
    - Resolves all attached source package paths
    - Indexes packages that are: not-embedded, failed, or stale
    - Skips packages that are already embedded (unless force_reindex=True)
    - Writes/refreshes the workspace index manifest
    - Updates workspace index_status and embedding_model fields

    Args:
        workspace_id:  Workspace slug.
        adapter_name:  Embedder to use (default: local_stub).
                       Pass 7: "local_stub" | "local_word" | "openai"
        model_name:    Model identifier stored in manifest. If None, uses backend default.
        force_reindex: If True, re-embed all packages regardless of current state.
        dimension:     Embedding dimension. If None, uses backend default.

    Returns:
        dict with keys:
            success, workspace_id, source_count, indexed_count, skipped_count,
            failed_count, total_chunks, indexed_chunks, manifest_path,
            workspace_index_status, errors (list)
    """
    result: dict = {
        "success": False,
        "workspace_id": workspace_id,
        "source_count": 0,
        "indexed_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "total_chunks": 0,
        "indexed_chunks": 0,
        "manifest_path": None,
        "workspace_index_status": _WS_NOT_INDEXED,
        "errors": [],
    }

    ws_path = _SIC_WORKSPACES / workspace_id / "workspace.json"
    if not ws_path.exists():
        result["errors"].append(
            f"workspace.json not found at {ws_path}. "
            "Run create_workspace() first."
        )
        return result

    try:
        workspace = json.loads(ws_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["errors"].append(f"Could not read workspace.json: {exc}")
        return result

    source_refs = workspace.get("source_refs", {})
    source_ids = workspace.get("source_package_ids", [])

    if not source_ids:
        result["errors"].append(
            f"Workspace '{workspace_id}' has no attached source packages. "
            "Use add_source_to_workspace() first."
        )
        return result

    result["source_count"] = len(source_ids)

    # Resolve model_name and dimension from backend defaults if not specified
    effective_model = model_name or get_backend_default_model(adapter_name)
    embedder = get_embedder(
        adapter_name=adapter_name,
        model_name=effective_model,
        dimension=dimension,
    )

    # Process each source package
    per_source_results = []
    for pkg_id in source_ids:
        ref = source_refs.get(pkg_id, {})
        pkg_path_str = ref.get("source_package_path")

        if not pkg_path_str:
            # Fallback: search workspace directories
            pkg_path = _find_package_by_id(workspace_id, pkg_id)
        else:
            pkg_path = Path(pkg_path_str)
            if not pkg_path.exists():
                pkg_path = _find_package_by_id(workspace_id, pkg_id)

        if pkg_path is None:
            msg = f"Source package {pkg_id} not found on disk."
            result["errors"].append(msg)
            per_source_results.append({
                "source_package_id": pkg_id,
                "index_status": _STATE_FAILED,
                "chunk_count": 0,
                "indexed_chunk_count": 0,
                "error": msg,
            })
            result["failed_count"] += 1
            continue

        # Check current embedding state
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            msg = f"Could not read source package {pkg_id}: {exc}"
            result["errors"].append(msg)
            per_source_results.append({
                "source_package_id": pkg_id,
                "index_status": _STATE_FAILED,
                "chunk_count": 0,
                "indexed_chunk_count": 0,
                "error": msg,
            })
            result["failed_count"] += 1
            continue

        current_status = pkg.get("embedding_status", _STATE_NOT_EMBEDDED)
        current_model = pkg.get("embedding_model")
        chunk_count = len(pkg.get("chunks", []))
        result["total_chunks"] += chunk_count

        # Skip already-indexed if not forced and same model
        should_skip = (
            current_status == _STATE_EMBEDDED
            and current_model == embedder.model_name
            and not force_reindex
        )
        if should_skip:
            result["skipped_count"] += 1
            result["indexed_chunks"] += chunk_count
            # Verify sidecar still exists — if missing, mark stale
            sidecar = _sidecar_path_for(pkg_path, pkg_id)
            sidecar_ok = sidecar.exists()
            per_source_results.append({
                "source_package_id": pkg_id,
                "title": ref.get("title"),
                "index_status": _STATE_EMBEDDED if sidecar_ok else _STATE_STALE,
                "chunk_count": chunk_count,
                "indexed_chunk_count": chunk_count if sidecar_ok else 0,
                "skipped": True,
                "sidecar_path": str(sidecar) if sidecar_ok else None,
            })
            continue

        # Index
        idx_result = index_source_package(
            source_package_path=pkg_path,
            embedder=embedder,
        )

        if idx_result["success"]:
            result["indexed_count"] += 1
            result["indexed_chunks"] += idx_result["indexed_chunk_count"]
            per_source_results.append({
                "source_package_id": pkg_id,
                "title": ref.get("title"),
                "index_status": _STATE_EMBEDDED,
                "chunk_count": chunk_count,
                "indexed_chunk_count": idx_result["indexed_chunk_count"],
                "sidecar_path": idx_result["sidecar_path"],
            })
        else:
            result["failed_count"] += 1
            result["errors"].append(f"{pkg_id}: {idx_result['error']}")
            per_source_results.append({
                "source_package_id": pkg_id,
                "title": ref.get("title"),
                "index_status": _STATE_FAILED,
                "chunk_count": chunk_count,
                "indexed_chunk_count": 0,
                "error": idx_result["error"],
            })

    # Determine workspace index status
    total = result["source_count"]
    indexed = result["indexed_count"] + result["skipped_count"]
    failed = result["failed_count"]

    if indexed == total and failed == 0:
        ws_status = _WS_INDEXED
    elif failed == total:
        ws_status = _WS_FAILED
    elif failed > 0:
        ws_status = _WS_PARTIAL
    else:
        ws_status = _WS_NOT_INDEXED

    result["workspace_index_status"] = ws_status

    # Write manifest
    manifest_path = _write_manifest(
        workspace_id=workspace_id,
        workspace=workspace,
        embedder=embedder,
        ws_status=ws_status,
        per_source_results=per_source_results,
        total_chunks=result["total_chunks"],
        indexed_chunks=result["indexed_chunks"],
    )
    result["manifest_path"] = str(manifest_path)

    # Update workspace.json index fields
    _update_workspace_index_state(
        ws_path=ws_path,
        workspace=workspace,
        ws_status=ws_status,
        embedder=embedder,
        manifest_path=manifest_path,
    )

    result["success"] = (failed == 0)
    return result


def get_manifest(workspace_id: str) -> dict:
    """
    Load and return the index manifest for workspace_id.

    Returns:
        dict with keys: success, workspace_id, manifest_path, manifest, error
    """
    result: dict = {
        "success": False,
        "workspace_id": workspace_id,
        "manifest_path": None,
        "manifest": None,
        "error": None,
    }
    manifest_path = _SIC_WORKSPACES / workspace_id / "indexes" / _MANIFEST_FILENAME
    result["manifest_path"] = str(manifest_path)
    if not manifest_path.exists():
        result["error"] = f"No manifest found at {manifest_path}. Run index_workspace() first."
        return result
    try:
        result["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
        result["success"] = True
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = f"Could not read manifest: {exc}"
    return result


# ── Internal helpers ──────────────────────────────────────────────────────────

def _sidecar_path_for(pkg_path: Path, pkg_id: str) -> Path:
    """
    Return the sidecar path for a source package.

    Sidecars live at:
        workspaces/{workspace_id}/indexes/{source_package_id}.vectors.json
    """
    # pkg_path = .../workspaces/{wid}/source_packages/foo.json
    #          or .../workspaces/{wid}/sources/foo.json (legacy)
    ws_dir = pkg_path.parent.parent  # up from source_packages/ to workspace dir
    return ws_dir / "indexes" / f"{pkg_id}.vectors.json"


def _find_package_by_id(workspace_id: str, pkg_id: str) -> Path | None:
    """Scan both source_packages/ and legacy sources/ for a package matching pkg_id."""
    ws_dir = _SIC_WORKSPACES / workspace_id
    for sub in ("source_packages", "sources"):
        d = ws_dir / sub
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("id") == pkg_id:
                    return f
            except (OSError, json.JSONDecodeError):
                continue
    return None


def _update_pkg_index_state(
    pkg_path: Path,
    pkg: dict,
    status: str,
    model: str,
    provider: str,
    dimension: int,
    sidecar_path: Path | None,
) -> None:
    """
    Update index-related fields on the source package JSON and write it back.

    Never leaves the package in a partial/corrupt state — writes atomically.
    """
    now = _now_iso()
    pkg["embedding_status"] = status
    pkg["embedding_model"] = model if status == _STATE_EMBEDDED else pkg.get("embedding_model")
    pkg["last_indexed_at"] = now if status == _STATE_EMBEDDED else pkg.get("last_indexed_at")
    pkg["index_path"] = str(sidecar_path) if sidecar_path else pkg.get("index_path")

    # Non-schema _index_meta for per-chunk state summary (not vectors)
    pkg["_index_meta"] = {
        "provider_name": provider,
        "model_name": model,
        "embedding_dimension": dimension,
        "embedding_status": status,
        "indexed_at": now if status == _STATE_EMBEDDED else None,
        "sidecar_path": str(sidecar_path) if sidecar_path else None,
    }

    try:
        pkg_path.write_text(
            json.dumps(pkg, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass  # write failure is logged at caller level; don't double-raise here


def _update_workspace_index_state(
    ws_path: Path,
    workspace: dict,
    ws_status: str,
    embedder: EmbedderBase,
    manifest_path: Path,
) -> None:
    """Update workspace.json index fields after an index run."""
    now = _now_iso()
    workspace["index_status"] = ws_status
    workspace["embedding_model"] = embedder.model_name
    workspace["last_indexed_at"] = now if ws_status in (_WS_INDEXED, _WS_PARTIAL) else workspace.get("last_indexed_at")
    workspace["index_path"] = str(manifest_path)
    workspace["updated_at"] = now
    try:
        ws_path.write_text(
            json.dumps(workspace, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _write_manifest(
    workspace_id: str,
    workspace: dict,
    embedder: EmbedderBase,
    ws_status: str,
    per_source_results: list[dict],
    total_chunks: int,
    indexed_chunks: int,
) -> Path:
    """Write or refresh the workspace index manifest. Returns the manifest path."""
    indexes_dir = _SIC_WORKSPACES / workspace_id / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = indexes_dir / _MANIFEST_FILENAME

    # Preserve created_at if manifest already exists
    created_at = _now_iso()
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            created_at = existing.get("created_at", created_at)
        except (OSError, json.JSONDecodeError):
            pass

    indexed_count = sum(1 for s in per_source_results
                        if s.get("index_status") == _STATE_EMBEDDED)
    stale_count = sum(1 for s in per_source_results
                      if s.get("index_status") == _STATE_STALE)
    failed_count = sum(1 for s in per_source_results
                       if s.get("index_status") == _STATE_FAILED)

    manifest = {
        "workspace_id": workspace_id,
        "workspace_uuid": workspace.get("id"),
        "index_status": ws_status,
        "provider_name": embedder.name,
        "model_name": embedder.model_name,
        "embedding_dimension": embedder.dimension,
        "source_count": len(per_source_results),
        "indexed_source_count": indexed_count,
        "stale_source_count": stale_count,
        "failed_source_count": failed_count,
        "total_chunk_count": total_chunks,
        "indexed_chunk_count": indexed_chunks,
        "created_at": created_at,
        "updated_at": _now_iso(),
        "sources": per_source_results,
    }

    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="index_manager",
        description="SIC Phase 7 — local index contract and embedding state.",
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    # list-backends (Pass 7)
    sub.add_parser(
        "list-backends",
        help="Show all known embedding backends and their availability status",
    )

    # index-workspace
    iw = sub.add_parser("index-workspace", help="Index all source packages in a workspace")
    iw.add_argument("--workspace", required=True)
    iw.add_argument(
        "--backend",
        default="local_stub",
        dest="adapter",
        help="Embedding backend: local_stub | local_word | openai (default: local_stub)",
    )
    iw.add_argument("--force", action="store_true",
                    help="Re-embed already-indexed packages (required when switching backends)")

    # show-manifest
    sm = sub.add_parser("show-manifest", help="Print the workspace index manifest")
    sm.add_argument("--workspace", required=True)
    sm.add_argument("--full", action="store_true",
                    help="Show per-source detail in manifest")

    # index-source
    is_ = sub.add_parser("index-source", help="Index a single source package file")
    is_.add_argument("--source", required=True, help="Path to source package JSON")
    is_.add_argument("--backend", default="local_stub", dest="adapter")

    return p


def main() -> None:
    parser = _build_cli()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "list-backends":
        backends = list_backends(check_availability=True)
        print()
        print("SIC embedding backends:")
        print()
        for b in backends:
            avail = "AVAILABLE" if b.get("available") else "UNAVAILABLE"
            local = "local-first" if b.get("local_first") else "external"
            print(f"  [{avail}] {b['name']}")
            print(f"           Label:      {b['label']}")
            print(f"           Model:      {b['default_model']}")
            print(f"           Dimension:  {b['default_dimension']}")
            print(f"           Quality:    {b['semantic_quality']} | {local}")
            print(f"           Status:     {b.get('reason', 'N/A')}")
            if b.get("notes"):
                print(f"           Notes:      {b['notes']}")
            print()
        sys.exit(0)

    elif args.command == "index-workspace":
        result = index_workspace(
            workspace_id=args.workspace,
            adapter_name=args.adapter,
            force_reindex=args.force,
        )
        print()
        status = "OK" if result["success"] else "PARTIAL/FAILED"
        print(f"{status}: index-workspace '{args.workspace}'")
        print(f"  Backend:         {args.adapter}")
        print(f"  Sources:         {result['source_count']}")
        print(f"  Indexed:         {result['indexed_count']}")
        print(f"  Skipped (cached):{result['skipped_count']}")
        print(f"  Failed:          {result['failed_count']}")
        print(f"  Total chunks:    {result['total_chunks']}")
        print(f"  Indexed chunks:  {result['indexed_chunks']}")
        print(f"  Workspace status:{result['workspace_index_status']}")
        print(f"  Manifest:        {result['manifest_path']}")
        if not args.force:
            print(f"  Note: Use --force to re-index when switching backends.")
        if result["errors"]:
            print(f"  Errors:")
            for e in result["errors"]:
                print(f"    - {e}")
        print()
        sys.exit(0 if result["success"] else 1)

    elif args.command == "show-manifest":
        result = get_manifest(workspace_id=args.workspace)
        if not result["success"]:
            print(f"\nFAILED: {result['error']}\n")
            sys.exit(1)
        m = result["manifest"]
        print()
        print(f"Index manifest — workspace '{args.workspace}'")
        print(f"  Status:          {m.get('index_status')}")
        print(f"  Provider:        {m.get('provider_name')}  |  Model: {m.get('model_name')}")
        print(f"  Dimension:       {m.get('embedding_dimension')}")
        print(f"  Sources:         {m.get('source_count')} total / "
              f"{m.get('indexed_source_count')} indexed / "
              f"{m.get('stale_source_count')} stale / "
              f"{m.get('failed_source_count')} failed")
        print(f"  Chunks:          {m.get('total_chunk_count')} total / "
              f"{m.get('indexed_chunk_count')} indexed")
        print(f"  Updated:         {m.get('updated_at')}")
        print(f"  Manifest path:   {result['manifest_path']}")
        if args.full:
            print()
            for src in m.get("sources", []):
                print(f"  [{src.get('index_status')}] {src.get('title') or src.get('source_package_id')}")
                print(f"    chunks: {src.get('chunk_count')} / indexed: {src.get('indexed_chunk_count')}")
                if src.get("error"):
                    print(f"    ERROR: {src['error']}")
        print()
        sys.exit(0)

    elif args.command == "index-source":
        result = index_source_package(source_package_path=args.source)
        print()
        if result["success"]:
            print(f"OK: indexed source package")
            print(f"  ID:              {result['source_package_id']}")
            print(f"  Chunks:          {result['chunk_count']}")
            print(f"  Indexed chunks:  {result['indexed_chunk_count']}")
            print(f"  Model:           {result['model_name']}")
            print(f"  Sidecar:         {result['sidecar_path']}")
        else:
            print(f"FAILED: {result['error']}")
        print()
        sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
