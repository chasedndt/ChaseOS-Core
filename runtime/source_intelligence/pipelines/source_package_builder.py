"""
source_package_builder.py — SIC Phase 7 Pass 2
Source Package Builder MVP for the ChaseOS Source Intelligence Core.

Entry points:
    ingest_source(...)          — main function; returns result dict
    CLI: python -m runtime.source_intelligence.pipelines.source_package_builder --input <path> --workspace <id>

Architecture constraints (enforced):
    - Local-first only. No API calls, no embeddings, no retrieval.
    - Writes to runtime/source_intelligence/workspaces/{workspace_id}/source_packages/ only.
    - Never writes to 02_KNOWLEDGE/, 01_PROJECTS/, or 00_HOME/.
    - Trust state defaults to conservative (untrusted / not-scanned).
    - All state fields are truthful — no fake completeness.

Schema conformance: runtime/source_intelligence/schemas/source_package_schema.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .chunking import chunk_text, chunks_to_dicts
from .normalization import normalize_text_with_metadata
from .text_extractors import (
    ExtractionError,
    UnsupportedSourceTypeError,
    detect_source_type,
    extract_text,
)

try:
    from runtime.schemas.provenance_block import make_from_source_package as _make_provenance
    _PROVENANCE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROVENANCE_AVAILABLE = False

# ── Vault root resolution ─────────────────────────────────────────────────────

_THIS_FILE = Path(__file__).resolve()
# runtime/source_intelligence/pipelines/source_package_builder.py
# → vault root is 3 levels up
_VAULT_ROOT = _THIS_FILE.parents[3]
_SIC_WORKSPACES = _VAULT_ROOT / "runtime" / "source_intelligence" / "workspaces"


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_source(
    input_path: str | Path,
    workspace_id: str,
    domain: str | None = None,
    source_type: str | None = None,
    user_trust_level: str = "untrusted",
    title: str | None = None,
    author: str | None = None,
    origin_url: str | None = None,
    publication_date: str | None = None,
    chunk_size: int = 1200,
    chunk_overlap: int = 150,
    created_by: str = "sic-pass2",
) -> dict:
    """
    Ingest a source file from 03_INPUTS/ (or any local path) into the SIC runtime store.

    Produces a Source Package JSON conforming to source_package_schema.md and writes it to:
        runtime/source_intelligence/workspaces/{workspace_id}/source_packages/{slug}_{id_prefix}.json

    Args:
        input_path:       Path to the source file (str or Path).
        workspace_id:     Required. ID/slug of the target workspace.
        domain:           Optional. ChaseOS domain (e.g. "TradingSystems").
        source_type:      Optional override. If None, auto-detected.
        user_trust_level: "untrusted" | "reviewed" | "trusted". Default: "untrusted".
        title:            Optional. Defaults to filename stem.
        author:           Optional. Speaker / author field.
        origin_url:       Optional. Source URL if web-derived.
        publication_date: Optional. "YYYY-MM-DD" string.
        chunk_size:       Target max chars per chunk (default 1200).
        chunk_overlap:    Overlap chars between chunks (default 150).
        created_by:       Audit tag for package provenance.

    Returns:
        dict with keys:
            success (bool)
            source_package_id (str)
            output_path (str)
            source_type (str)
            extraction_status (str)
            chunk_count (int)
            normalized_text_char_count (int)
            error (str | None)
            package (dict)  — full Source Package dict
    """
    input_path = Path(input_path).resolve()
    result: dict = {
        "success": False,
        "source_package_id": None,
        "output_path": None,
        "source_type": None,
        "extraction_status": "pending",
        "chunk_count": 0,
        "normalized_text_char_count": 0,
        "error": None,
        "package": None,
    }

    # ── Validation ────────────────────────────────────────────────────────────
    if not workspace_id or not workspace_id.strip():
        result["error"] = "workspace_id is required and must not be empty."
        return result

    workspace_id = workspace_id.strip()

    if user_trust_level not in ("untrusted", "reviewed", "trusted"):
        result["error"] = (
            f"Invalid user_trust_level '{user_trust_level}'. "
            "Must be: untrusted | reviewed | trusted"
        )
        return result

    if not input_path.exists():
        result["error"] = f"Input file not found: {input_path}"
        return result

    if not input_path.is_file():
        result["error"] = f"Input path is not a file: {input_path}"
        return result

    # ── Source type detection ─────────────────────────────────────────────────
    try:
        detected_type = detect_source_type(input_path, source_type)
    except ValueError as exc:
        result["error"] = str(exc)
        return result

    result["source_type"] = detected_type

    # ── Text extraction ───────────────────────────────────────────────────────
    result["extraction_status"] = "in-progress"
    extraction_method = "direct-text"
    extraction_notes = None

    try:
        raw_text, extraction_method, extraction_notes = extract_text(input_path, detected_type)
    except UnsupportedSourceTypeError as exc:
        result["extraction_status"] = "failed"
        result["error"] = str(exc)
        return result
    except ExtractionError as exc:
        result["extraction_status"] = "failed"
        result["error"] = str(exc)
        return result

    # ── Normalization ─────────────────────────────────────────────────────────
    normalized, source_text_quality = normalize_text_with_metadata(raw_text)

    if not normalized.strip():
        result["extraction_status"] = "failed"
        result["error"] = "Normalized text is empty after processing."
        return result

    result["extraction_status"] = "complete"
    result["normalized_text_char_count"] = len(normalized)
    if source_text_quality["encoding_repair_applied"]:
        extraction_notes = _append_note(
            extraction_notes,
            "Common UTF-8/Windows-1252 mojibake repaired before normalization.",
        )

    # ── IDs and timestamps ────────────────────────────────────────────────────
    source_package_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    today = now.strftime("%Y-%m-%d")

    # Content hash (SHA-256 of normalized text, first 12 hex chars as slug suffix)
    content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    hash_prefix = content_hash[:12]

    # File metadata
    file_stat = input_path.stat()
    intake_date = datetime.fromtimestamp(file_stat.st_ctime, tz=timezone.utc).strftime("%Y-%m-%d")

    # ── Chunking ──────────────────────────────────────────────────────────────
    chunks = chunk_text(
        normalized_text=normalized,
        source_package_id=source_package_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    result["chunk_count"] = len(chunks)

    # ── Source Package assembly ───────────────────────────────────────────────
    resolved_title = title or _title_from_path(input_path)

    package = {
        # 2.1 Identity
        "id": source_package_id,
        "title": resolved_title,
        "source_type": detected_type,
        "created_at": now_iso,
        "updated_at": now_iso,

        # 2.2 Provenance
        "origin_path": str(input_path),
        "origin_url": origin_url,
        "author": author,
        "publication_date": publication_date,
        "intake_date": intake_date,
        "package_created_date": today,

        # 2.3 Extraction Status
        "extraction_status": "complete",
        "extraction_method": extraction_method,
        "extraction_notes": extraction_notes,
        "chunk_count": len(chunks),

        # 2.4 Normalized Text
        "normalized_text": normalized,
        "normalized_text_char_count": len(normalized),
        "chunks": chunks_to_dicts(chunks),
        "source_text_quality": source_text_quality,

        # 2.5 Trust State
        # injection_scan_status is truthfully "not-scanned" — no scanner implemented yet
        "injection_scan_status": "not-scanned",
        "injection_scan_notes": (
            "Automated injection scan not yet implemented (Phase 7 Pass 2). "
            "Manual review required before promotion."
        ),
        "user_trust_level": user_trust_level,
        "trust_notes": None,

        # 2.6 Workspace Assignment
        "workspace_ids": [workspace_id],
        "workspace_labels": [workspace_id],

        # 2.7 Index State — truthfully not embedded yet
        "embedding_status": "not-embedded",
        "embedding_model": None,
        "index_path": None,
        "last_indexed_at": None,

        # Builder metadata (non-schema audit field)
        "_builder_meta": {
            "created_by": created_by,
            "sic_phase": "phase7-pass2",
            "content_hash_sha256": content_hash,
            "domain": domain,
            "vault_root": str(_VAULT_ROOT),
            "source_text_quality": source_text_quality,
        },
    }

    # ── Provenance block (Phase 9 Feature 11) ────────────────────────────────
    if _PROVENANCE_AVAILABLE:
        try:
            package["provenance"] = _make_provenance(package).to_dict()
        except Exception:  # pragma: no cover — fail-open; provenance is additive
            pass

    result["package"] = package

    # ── Write to SIC runtime store ────────────────────────────────────────────
    output_path, write_error = _write_package(package, workspace_id, input_path, hash_prefix)
    if write_error:
        result["extraction_status"] = "complete"  # extraction succeeded
        result["error"] = f"Package assembled but write failed: {write_error}"
        return result

    result["success"] = True
    result["source_package_id"] = source_package_id
    result["output_path"] = str(output_path)

    return result


# ── Storage ───────────────────────────────────────────────────────────────────

def _write_package(
    package: dict,
    workspace_id: str,
    input_path: Path,
    hash_prefix: str,
) -> tuple[Path | None, str | None]:
    """
    Write the Source Package JSON to:
        runtime/source_intelligence/workspaces/{workspace_id}/source_packages/{slug}_{hash_prefix}.json

    Canonical directory: source_packages/ (standardized in Pass 4).
    Backward compat: sources/ is the legacy name from Pass 2 test outputs — not used for new writes.

    Returns (output_path, error_str). error_str is None on success.
    """
    slug = _filename_slug(input_path.stem)
    filename = f"{slug}_{hash_prefix}.json"

    out_dir = _SIC_WORKSPACES / workspace_id / "source_packages"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return None, f"Could not create output directory {out_dir}: {exc}"

    out_path = out_dir / filename
    try:
        out_path.write_text(
            json.dumps(package, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        return None, f"Could not write package JSON: {exc}"

    return out_path, None


# ── Utilities ─────────────────────────────────────────────────────────────────

def _title_from_path(path: Path) -> str:
    """Derive a human-readable title from the filename stem."""
    stem = path.stem
    # Strip leading date prefix YYYY-MM-DD_ or YYYY-MM-DD-
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}[-_]", "", stem)
    # Replace hyphens/underscores with spaces and title-case
    return stem.replace("-", " ").replace("_", " ").title()


def _filename_slug(stem: str) -> str:
    """Convert a filename stem to a safe lowercase slug."""
    # Strip leading date prefix
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}[-_]", "", stem)
    # Lowercase, replace non-alphanum with hyphens, collapse runs
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return slug[:60]  # cap length


def _append_note(existing: str | None, note: str) -> str:
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing} {note}"


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_cli_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="source_package_builder",
        description="SIC Phase 7 — ingest a source file into the Source Intelligence Core.",
    )
    p.add_argument("--input", required=True, help="Path to source file in 03_INPUTS/ or local path")
    p.add_argument("--workspace", required=True, help="Workspace ID/slug (e.g. phase7-test)")
    p.add_argument("--domain", default=None, help="ChaseOS domain (e.g. TradingSystems)")
    p.add_argument("--source-type", default=None, dest="source_type",
                   help="Override auto-detected source type")
    p.add_argument("--trust", default="untrusted",
                   choices=["untrusted", "reviewed", "trusted"],
                   help="user_trust_level (default: untrusted)")
    p.add_argument("--title", default=None, help="Override title")
    p.add_argument("--author", default=None, help="Author or speaker")
    p.add_argument("--chunk-size", type=int, default=1200, dest="chunk_size")
    p.add_argument("--chunk-overlap", type=int, default=150, dest="chunk_overlap")
    p.add_argument("--json", action="store_true", dest="output_json",
                   help="Print full result JSON instead of summary")
    return p


def main() -> None:
    parser = _build_cli_parser()
    args = parser.parse_args()

    result = ingest_source(
        input_path=args.input,
        workspace_id=args.workspace,
        domain=args.domain,
        source_type=args.source_type,
        user_trust_level=args.trust,
        title=args.title,
        author=args.author,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )

    if args.output_json:
        # Print without the full normalized_text and chunks for readability
        slim = {k: v for k, v in result.items() if k not in ("package",)}
        if result.get("package"):
            slim["package"] = {
                k: v for k, v in result["package"].items()
                if k not in ("normalized_text", "chunks")
            }
            slim["package"]["chunks_preview"] = (
                result["package"]["chunks"][:2] if result["package"]["chunks"] else []
            )
        print(json.dumps(slim, indent=2, ensure_ascii=False))
        sys.exit(0 if result["success"] else 1)

    # Human-readable summary
    print()
    if result["success"]:
        print("OK: Source Package built successfully")
    else:
        print("FAILED: Source Package build FAILED")

    print(f"  Input:             {args.input}")
    print(f"  Source type:       {result['source_type'] or 'unknown'}")
    print(f"  Extraction status: {result['extraction_status']}")
    print(f"  Normalized chars:  {result['normalized_text_char_count']}")
    print(f"  Chunk count:       {result['chunk_count']}")

    if result["success"]:
        print(f"  Package ID:        {result['source_package_id']}")
        print(f"  Output path:       {result['output_path']}")
    else:
        print(f"  Error:             {result['error']}")

    print()
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
