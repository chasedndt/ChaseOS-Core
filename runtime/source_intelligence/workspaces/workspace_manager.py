"""
workspace_manager.py — SIC Phase 7 Pass 3
Workspace Record Management for the ChaseOS Source Intelligence Core.

Entry points:
    create_workspace(...)            — create or return existing workspace record
    load_workspace(...)              — read workspace.json
    add_source_to_workspace(...)     — link an ingested Source Package into a workspace
    remove_source_from_workspace(...)— detach source membership (does not delete package)
    list_workspace_sources(...)      — return source membership refs
    update_workspace_metadata(...)   — patch name/description/domain/tags

CLI:
    python -m runtime.source_intelligence.workspaces.workspace_manager <command> [args]

Architecture constraints (enforced):
    - Local-first only. No API calls, no embeddings, no retrieval.
    - workspace.json references Source Packages — never absorbs them.
    - All state fields are truthful. No fake completeness.
    - Writes stay inside runtime/source_intelligence/workspaces/ only.
    - Never touches 02_KNOWLEDGE/, 01_PROJECTS/, or 00_HOME/.

Schema conformance: runtime/source_intelligence/schemas/workspace_schema.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


# ── Runtime paths ─────────────────────────────────────────────────────────────

_THIS_FILE = Path(__file__).resolve()
# runtime/source_intelligence/workspaces/workspace_manager.py
# → vault root is 3 levels up
_VAULT_ROOT = _THIS_FILE.parents[3]
_SIC_WORKSPACES = _VAULT_ROOT / "runtime" / "source_intelligence" / "workspaces"

_WORKSPACE_FILENAME = "workspace.json"

# ── Validation ────────────────────────────────────────────────────────────────

_VALID_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,79}$")


def validate_workspace_id(workspace_id: str) -> None:
    """
    Raise WorkspaceValidationError if workspace_id is not filesystem-safe.

    Rules:
    - 1–80 characters
    - lowercase letters, digits, hyphens, underscores only
    - must start with a letter or digit (not a separator)
    """
    if not workspace_id or not isinstance(workspace_id, str):
        raise WorkspaceValidationError("workspace_id must be a non-empty string.")
    if not _VALID_ID_RE.match(workspace_id):
        raise WorkspaceValidationError(
            f"Invalid workspace_id '{workspace_id}'. "
            "Must be 1–80 chars, lowercase alphanumeric, hyphens, or underscores, "
            "starting with a letter or digit."
        )


def validate_source_reference(pkg: dict, path: Path) -> None:
    """
    Raise WorkspaceValidationError if pkg does not look like a SIC Source Package.

    Light validation — confirms the minimum fields needed to trust the artifact.
    """
    required = ("id", "source_type", "extraction_status", "normalized_text_char_count")
    missing = [f for f in required if f not in pkg]
    if missing:
        raise WorkspaceValidationError(
            f"Source package at {path} is missing required fields: {missing}. "
            "Re-run ingest_source() to rebuild it."
        )
    if pkg.get("extraction_status") not in ("complete", "in-progress"):
        raise WorkspaceValidationError(
            f"Source package {pkg.get('id')} has extraction_status="
            f"'{pkg.get('extraction_status')}'. Only 'complete' or 'in-progress' "
            "packages may be added to a workspace."
        )


def ensure_workspace_dir(workspace_id: str) -> Path:
    """Create and return the workspace directory, creating it if missing."""
    ws_dir = _SIC_WORKSPACES / workspace_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    return ws_dir


# ── Core functions ────────────────────────────────────────────────────────────

def create_workspace(
    workspace_id: str,
    domain: str | None = None,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    overwrite: bool = False,
    created_by: str = "sic-pass3",
) -> dict:
    """
    Create a new workspace record at:
        runtime/source_intelligence/workspaces/{workspace_id}/workspace.json

    If workspace.json already exists and overwrite=False, returns the existing
    workspace cleanly (idempotent). If overwrite=True, re-initializes the record
    (clears all source membership — use with care).

    Args:
        workspace_id:  Filesystem-safe slug (e.g. "phase7-test", "trading-research").
        domain:        ChaseOS domain (e.g. "TradingSystems", "AI"). Optional.
        title:         Human-readable workspace name. Defaults to workspace_id.
        description:   Workspace purpose. Defaults to empty string.
        tags:          User-defined tag list. Optional.
        overwrite:     If True and workspace exists, replace it. Default False.
        created_by:    Audit tag.

    Returns:
        dict with keys: success, workspace_id, workspace_path, workspace, error, created (bool)
    """
    result: dict = {
        "success": False,
        "workspace_id": workspace_id,
        "workspace_path": None,
        "workspace": None,
        "created": False,
        "error": None,
    }

    try:
        validate_workspace_id(workspace_id)
    except WorkspaceValidationError as exc:
        result["error"] = str(exc)
        return result

    ws_dir = ensure_workspace_dir(workspace_id)
    ws_path = ws_dir / _WORKSPACE_FILENAME
    result["workspace_path"] = str(ws_path)

    # Return existing workspace if not overwriting
    if ws_path.exists() and not overwrite:
        try:
            existing = _read_workspace_json(ws_path)
            result["success"] = True
            result["workspace"] = existing
            result["created"] = False
            return result
        except WorkspaceIOError as exc:
            result["error"] = str(exc)
            return result

    now = _now_iso()
    today = _today()
    workspace_uuid = str(uuid.uuid4())

    # Derive default_promotion_target from domain
    promotion_target = None
    if domain:
        promotion_target = f"02_KNOWLEDGE/{domain}/"

    workspace: dict = {
        # 2.1 Identity
        "id": workspace_uuid,
        "slug": workspace_id,          # extension: slug is the directory name
        "name": title or workspace_id,
        "description": description or "",
        "created_at": now,
        "updated_at": now,
        "status": "active",

        # 2.2 Domain and Project Grouping
        "domain": domain,
        "project_id": None,
        "project_name": None,
        "tags": tags or [],

        # 2.3 Source Membership
        # source_package_ids: schema-required UUID list
        "source_package_ids": [],
        "source_count": 0,
        "sources_summary": None,
        # source_refs: extension — lightweight per-source metadata keyed by package_id
        # not in schema but additive and non-conflicting
        "source_refs": {},

        # 2.4 Index State — truthfully not indexed yet
        "index_status": "not-indexed",
        "index_path": None,
        "last_indexed_at": None,
        "embedding_model": None,

        # 2.5 Query Scope
        "query_scope": "workspace-only",
        "retrieval_top_k": 5,
        "retrieval_min_score": None,

        # 2.6 Allowed Output Classes — null means all allowed
        "allowed_output_classes": None,

        # 2.7 Outputs — empty on creation
        "outputs": [],
        "output_count": 0,

        # 2.8 Writeback Rules
        "default_promotion_target": promotion_target,
        "promotion_requires_review": True,  # immutable — never set to False
        "last_promotion_at": None,

        # Manager metadata (non-schema audit)
        "_manager_meta": {
            "created_by": created_by,
            "sic_phase": "phase7-pass3",
            "vault_root": str(_VAULT_ROOT),
        },
    }

    try:
        _write_workspace_json(ws_path, workspace)
    except WorkspaceIOError as exc:
        result["error"] = str(exc)
        return result

    result["success"] = True
    result["workspace"] = workspace
    result["created"] = True
    return result


def load_workspace(workspace_id: str) -> dict:
    """
    Load and return the workspace.json for workspace_id.

    Returns:
        dict with keys: success, workspace_id, workspace_path, workspace, error

    Raises nothing — errors are returned in result["error"].
    """
    result: dict = {
        "success": False,
        "workspace_id": workspace_id,
        "workspace_path": None,
        "workspace": None,
        "error": None,
    }

    try:
        validate_workspace_id(workspace_id)
    except WorkspaceValidationError as exc:
        result["error"] = str(exc)
        return result

    ws_path = _SIC_WORKSPACES / workspace_id / _WORKSPACE_FILENAME
    result["workspace_path"] = str(ws_path)

    if not ws_path.exists():
        result["error"] = (
            f"Workspace '{workspace_id}' not found at {ws_path}. "
            "Run create_workspace() first."
        )
        return result

    try:
        workspace = _read_workspace_json(ws_path)
    except WorkspaceIOError as exc:
        result["error"] = str(exc)
        return result

    result["success"] = True
    result["workspace"] = workspace
    return result


def add_source_to_workspace(
    workspace_id: str,
    source_package_path: str | Path | None = None,
    source_package_id: str | None = None,
) -> dict:
    """
    Link an ingested Source Package into a workspace record.

    The workspace stores only a lightweight reference — not the full package.
    If the same source_package_id is already a member, returns idempotent success.

    Resolution priority:
    1. source_package_path (explicit path to the .json file)
    2. source_package_id  (scans workspace source_packages/ directory for matching ID)

    At least one of source_package_path or source_package_id must be provided.

    Returns:
        dict with keys: success, workspace_id, source_package_id, added (bool), error
    """
    result: dict = {
        "success": False,
        "workspace_id": workspace_id,
        "source_package_id": None,
        "added": False,
        "error": None,
    }

    try:
        validate_workspace_id(workspace_id)
    except WorkspaceValidationError as exc:
        result["error"] = str(exc)
        return result

    if not source_package_path and not source_package_id:
        result["error"] = "Either source_package_path or source_package_id must be provided."
        return result

    # Resolve package path
    pkg_path, resolve_error = _resolve_package_path(
        workspace_id, source_package_path, source_package_id
    )
    if resolve_error:
        result["error"] = resolve_error
        return result

    # Load package
    try:
        pkg = _read_json(pkg_path)
    except WorkspaceIOError as exc:
        result["error"] = str(exc)
        return result

    # Validate it looks like a real SIC source package
    try:
        validate_source_reference(pkg, pkg_path)
    except WorkspaceValidationError as exc:
        result["error"] = str(exc)
        return result

    pkg_id = pkg["id"]
    result["source_package_id"] = pkg_id

    # Load workspace
    ws_result = load_workspace(workspace_id)
    if not ws_result["success"]:
        result["error"] = ws_result["error"]
        return result

    workspace = ws_result["workspace"]

    # Idempotency check
    if pkg_id in workspace.get("source_package_ids", []):
        result["success"] = True
        result["added"] = False  # already a member
        return result

    # Build lightweight source reference
    source_ref = _build_source_ref(pkg, pkg_path)

    # Mutate workspace record
    workspace.setdefault("source_package_ids", []).append(pkg_id)
    workspace["source_count"] = len(workspace["source_package_ids"])
    workspace.setdefault("source_refs", {})[pkg_id] = source_ref
    workspace["updated_at"] = _now_iso()

    # If sources were previously indexed, mark index stale
    if workspace.get("index_status") == "indexed":
        workspace["index_status"] = "stale"

    # Persist
    ws_path = _SIC_WORKSPACES / workspace_id / _WORKSPACE_FILENAME
    try:
        _write_workspace_json(ws_path, workspace)
    except WorkspaceIOError as exc:
        result["error"] = str(exc)
        return result

    result["success"] = True
    result["added"] = True
    return result


def remove_source_from_workspace(workspace_id: str, source_package_id: str) -> dict:
    """
    Detach a Source Package from a workspace by removing its membership entry.

    Does NOT delete the source package JSON — only removes it from workspace.json.

    Returns:
        dict with keys: success, workspace_id, source_package_id, removed (bool), error
    """
    result: dict = {
        "success": False,
        "workspace_id": workspace_id,
        "source_package_id": source_package_id,
        "removed": False,
        "error": None,
    }

    try:
        validate_workspace_id(workspace_id)
    except WorkspaceValidationError as exc:
        result["error"] = str(exc)
        return result

    if not source_package_id:
        result["error"] = "source_package_id is required."
        return result

    ws_result = load_workspace(workspace_id)
    if not ws_result["success"]:
        result["error"] = ws_result["error"]
        return result

    workspace = ws_result["workspace"]
    ids: list = workspace.get("source_package_ids", [])

    if source_package_id not in ids:
        # Already absent — idempotent success
        result["success"] = True
        result["removed"] = False
        return result

    ids.remove(source_package_id)
    workspace["source_package_ids"] = ids
    workspace["source_count"] = len(ids)
    workspace.get("source_refs", {}).pop(source_package_id, None)
    workspace["updated_at"] = _now_iso()

    ws_path = _SIC_WORKSPACES / workspace_id / _WORKSPACE_FILENAME
    try:
        _write_workspace_json(ws_path, workspace)
    except WorkspaceIOError as exc:
        result["error"] = str(exc)
        return result

    result["success"] = True
    result["removed"] = True
    return result


def list_workspace_sources(workspace_id: str) -> dict:
    """
    Return lightweight source membership entries from workspace.json.

    Returns:
        dict with keys: success, workspace_id, source_count, sources (list), error
        Each source entry is the lightweight ref dict from source_refs.
    """
    result: dict = {
        "success": False,
        "workspace_id": workspace_id,
        "source_count": 0,
        "sources": [],
        "error": None,
    }

    ws_result = load_workspace(workspace_id)
    if not ws_result["success"]:
        result["error"] = ws_result["error"]
        return result

    workspace = ws_result["workspace"]
    refs = workspace.get("source_refs", {})
    ids = workspace.get("source_package_ids", [])

    # Return in membership order
    sources = [refs[pid] for pid in ids if pid in refs]
    # Include IDs that have no ref entry (shouldn't happen, but be defensive)
    no_ref_ids = [pid for pid in ids if pid not in refs]
    for pid in no_ref_ids:
        sources.append({"source_package_id": pid, "_warning": "no ref entry found"})

    result["success"] = True
    result["source_count"] = workspace.get("source_count", len(ids))
    result["sources"] = sources
    return result


def update_workspace_metadata(
    workspace_id: str,
    name: str | None = None,
    description: str | None = None,
    domain: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
) -> dict:
    """
    Patch mutable metadata fields on an existing workspace.

    Only provided (non-None) fields are updated. Structural fields (id, slug,
    source membership, outputs) are never touched by this function.

    Returns:
        dict with keys: success, workspace_id, workspace, error
    """
    result: dict = {
        "success": False,
        "workspace_id": workspace_id,
        "workspace": None,
        "error": None,
    }

    ws_result = load_workspace(workspace_id)
    if not ws_result["success"]:
        result["error"] = ws_result["error"]
        return result

    workspace = ws_result["workspace"]
    changed = False

    if name is not None:
        workspace["name"] = name
        changed = True
    if description is not None:
        workspace["description"] = description
        changed = True
    if domain is not None:
        workspace["domain"] = domain
        workspace["default_promotion_target"] = f"02_KNOWLEDGE/{domain}/"
        changed = True
    if tags is not None:
        workspace["tags"] = tags
        changed = True
    if status is not None:
        valid_statuses = ("active", "archived", "draft")
        if status not in valid_statuses:
            result["error"] = f"Invalid status '{status}'. Must be: {valid_statuses}"
            return result
        workspace["status"] = status
        changed = True

    if not changed:
        result["success"] = True
        result["workspace"] = workspace
        return result

    workspace["updated_at"] = _now_iso()
    ws_path = _SIC_WORKSPACES / workspace_id / _WORKSPACE_FILENAME
    try:
        _write_workspace_json(ws_path, workspace)
    except WorkspaceIOError as exc:
        result["error"] = str(exc)
        return result

    result["success"] = True
    result["workspace"] = workspace
    return result


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_source_ref(pkg: dict, pkg_path: Path) -> dict:
    """
    Build a lightweight source reference from a full Source Package dict.

    This is what gets stored in workspace.source_refs — not the full package.
    """
    meta = pkg.get("_builder_meta", {})
    return {
        "source_package_id": pkg["id"],
        "source_package_path": str(pkg_path),
        "source_type": pkg.get("source_type"),
        "title": pkg.get("title"),
        "origin_path": pkg.get("origin_path"),
        "chunk_count": pkg.get("chunk_count", 0),
        "normalized_text_char_count": pkg.get("normalized_text_char_count", 0),
        "extraction_status": pkg.get("extraction_status"),
        "injection_scan_status": pkg.get("injection_scan_status"),
        "user_trust_level": pkg.get("user_trust_level"),
        "embedding_status": pkg.get("embedding_status"),
        "content_hash_sha256": meta.get("content_hash_sha256"),
        "domain": meta.get("domain"),
        "package_created_date": pkg.get("package_created_date"),
        "added_at": _now_iso(),
    }


def _resolve_package_path(
    workspace_id: str,
    source_package_path: str | Path | None,
    source_package_id: str | None,
) -> tuple[Path | None, str | None]:
    """
    Resolve source package path from explicit path or ID lookup.

    Returns (resolved_path, error_str). error_str is None on success.
    """
    if source_package_path:
        p = Path(source_package_path)
        if not p.is_absolute():
            p = _VAULT_ROOT / p
        p = p.resolve()
        if not p.exists():
            return None, f"Source package file not found: {p}"
        return p, None

    # Canonical directory is source_packages/ (standardized in Pass 4).
    # Backward-compat fallback: also check sources/ for packages written by Pass 2.
    ws_dir = _SIC_WORKSPACES / workspace_id
    search_dirs = []
    canonical = ws_dir / "source_packages"
    legacy = ws_dir / "sources"
    if canonical.exists():
        search_dirs.append(canonical)
    if legacy.exists():
        search_dirs.append(legacy)

    if not search_dirs:
        return None, (
            f"No source_packages/ directory found for workspace '{workspace_id}'. "
            "Ingest at least one source first with ingest_source()."
        )

    for search_dir in search_dirs:
        for candidate in search_dir.glob("*.json"):
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if data.get("id") == source_package_id:
                    return candidate, None
            except (json.JSONDecodeError, OSError):
                continue

    return None, (
        f"Source package with id '{source_package_id}' not found "
        f"in {ws_dir}/source_packages/ (or legacy sources/). "
        "Check the ID or provide source_package_path directly."
    )


def _read_workspace_json(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise WorkspaceIOError(f"Could not read workspace.json at {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkspaceIOError(f"workspace.json at {path} is not valid JSON: {exc}") from exc


def _write_workspace_json(path: Path, workspace: dict) -> None:
    try:
        path.write_text(
            json.dumps(workspace, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise WorkspaceIOError(f"Could not write workspace.json at {path}: {exc}") from exc


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorkspaceIOError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise WorkspaceIOError(f"Invalid JSON at {path}: {exc}") from exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Custom exceptions ─────────────────────────────────────────────────────────

class WorkspaceValidationError(Exception):
    pass


class WorkspaceIOError(Exception):
    pass


class WorkspaceNotFoundError(Exception):
    pass


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="workspace_manager",
        description="SIC Phase 7 — workspace record management.",
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    # create
    c = sub.add_parser("create", help="Create a new workspace record")
    c.add_argument("--workspace", required=True, help="Workspace ID/slug")
    c.add_argument("--title", default=None)
    c.add_argument("--description", default=None)
    c.add_argument("--domain", default=None)
    c.add_argument("--tags", nargs="*", default=None, metavar="TAG")
    c.add_argument("--overwrite", action="store_true", help="Re-initialize existing workspace")

    # add-source
    a = sub.add_parser("add-source", help="Add an ingested source package to a workspace")
    a.add_argument("--workspace", required=True)
    a.add_argument("--source", default=None, dest="source_path",
                   help="Path to source package JSON")
    a.add_argument("--source-id", default=None, dest="source_id",
                   help="Source package UUID (scans workspace source_packages/)")

    # remove-source
    r = sub.add_parser("remove-source", help="Detach a source from workspace (keeps JSON)")
    r.add_argument("--workspace", required=True)
    r.add_argument("--source-id", required=True, dest="source_id")

    # list-sources
    ls = sub.add_parser("list-sources", help="List sources in a workspace")
    ls.add_argument("--workspace", required=True)
    ls.add_argument("--verbose", "-v", action="store_true",
                    help="Show all ref fields per source")

    # show
    sh = sub.add_parser("show", help="Print workspace.json summary")
    sh.add_argument("--workspace", required=True)

    # update
    u = sub.add_parser("update", help="Patch workspace metadata")
    u.add_argument("--workspace", required=True)
    u.add_argument("--title", default=None)
    u.add_argument("--description", default=None)
    u.add_argument("--domain", default=None)
    u.add_argument("--status", default=None, choices=["active", "archived", "draft"])
    u.add_argument("--tags", nargs="*", default=None, metavar="TAG")

    return p


def main() -> None:
    parser = _build_cli()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # ── create ────────────────────────────────────────────────────────────────
    if args.command == "create":
        result = create_workspace(
            workspace_id=args.workspace,
            domain=args.domain,
            title=args.title,
            description=args.description,
            tags=args.tags,
            overwrite=args.overwrite,
        )
        if result["success"]:
            verb = "Created" if result["created"] else "Already exists (returned)"
            ws = result["workspace"]
            print(f"\nOK: {verb}")
            print(f"  Workspace ID:  {args.workspace}")
            print(f"  UUID:          {ws['id']}")
            print(f"  Name:          {ws['name']}")
            print(f"  Domain:        {ws.get('domain') or '(none)'}")
            print(f"  Status:        {ws['status']}")
            print(f"  Sources:       {ws['source_count']}")
            print(f"  Path:          {result['workspace_path']}")
        else:
            print(f"\nFAILED: {result['error']}")
        print()
        sys.exit(0 if result["success"] else 1)

    # ── add-source ────────────────────────────────────────────────────────────
    elif args.command == "add-source":
        result = add_source_to_workspace(
            workspace_id=args.workspace,
            source_package_path=args.source_path,
            source_package_id=args.source_id,
        )
        if result["success"]:
            verb = "Added" if result["added"] else "Already a member (idempotent)"
            print(f"\nOK: {verb}")
            print(f"  Workspace:         {args.workspace}")
            print(f"  Source package ID: {result['source_package_id']}")
        else:
            print(f"\nFAILED: {result['error']}")
        print()
        sys.exit(0 if result["success"] else 1)

    # ── remove-source ─────────────────────────────────────────────────────────
    elif args.command == "remove-source":
        result = remove_source_from_workspace(
            workspace_id=args.workspace,
            source_package_id=args.source_id,
        )
        if result["success"]:
            verb = "Detached" if result["removed"] else "Already absent (idempotent)"
            print(f"\nOK: {verb}")
            print(f"  Workspace:         {args.workspace}")
            print(f"  Source package ID: {args.source_id}")
        else:
            print(f"\nFAILED: {result['error']}")
        print()
        sys.exit(0 if result["success"] else 1)

    # ── list-sources ──────────────────────────────────────────────────────────
    elif args.command == "list-sources":
        result = list_workspace_sources(workspace_id=args.workspace)
        if result["success"]:
            print(f"\nWorkspace '{args.workspace}' - {result['source_count']} source(s)\n")
            for i, src in enumerate(result["sources"], 1):
                print(f"  [{i}] {src.get('title', '(no title)')}")
                print(f"      ID:     {src.get('source_package_id')}")
                print(f"      Type:   {src.get('source_type')}")
                print(f"      Chunks: {src.get('chunk_count')}")
                print(f"      Trust:  {src.get('user_trust_level')}  |  "
                      f"Scan: {src.get('injection_scan_status')}")
                if args.verbose:
                    print(f"      Path:   {src.get('source_package_path')}")
                    print(f"      Origin: {src.get('origin_path')}")
                    print(f"      Hash:   {src.get('content_hash_sha256', '(none)')}")
                    print(f"      Added:  {src.get('added_at')}")
                print()
        else:
            print(f"\nFAILED: {result['error']}")
            print()
        sys.exit(0 if result["success"] else 1)

    # ── show ──────────────────────────────────────────────────────────────────
    elif args.command == "show":
        result = load_workspace(workspace_id=args.workspace)
        if result["success"]:
            ws = result["workspace"]
            # Print without source_refs and outputs detail for readability
            summary = {k: v for k, v in ws.items()
                       if k not in ("source_refs", "outputs", "normalized_text")}
            summary["source_refs_count"] = len(ws.get("source_refs", {}))
            summary["outputs_count"] = len(ws.get("outputs", []))
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            print(f"\nFAILED: {result['error']}")
        sys.exit(0 if result["success"] else 1)

    # ── update ────────────────────────────────────────────────────────────────
    elif args.command == "update":
        result = update_workspace_metadata(
            workspace_id=args.workspace,
            name=args.title,
            description=args.description,
            domain=args.domain,
            tags=args.tags,
            status=args.status,
        )
        if result["success"]:
            print(f"\nOK: Workspace '{args.workspace}' updated")
        else:
            print(f"\nFAILED: {result['error']}")
        print()
        sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
