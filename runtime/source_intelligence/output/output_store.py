"""
output_store.py — SIC Phase 7 Pass 6B
Workspace-local output persistence for the SIC Output Generation Layer.

This module owns:
  - persisting generated output objects to workspace-local storage
  - recording lightweight output refs in workspace.json outputs[]
  - listing stored outputs for a workspace
  - loading a stored output by ID or filename

Storage layout:
  runtime/source_intelligence/workspaces/{workspace_id}/outputs/{filename}.json

Filename convention:
  {YYYYMMDD_HHMMSS}_{output_type}_{query_slug}.json
  Example: 20260325_143022_briefing_what-are-the-main-takeaway.json

Architecture constraints (enforced):
  - Never writes to 02_KNOWLEDGE/, 01_PROJECTS/, 00_HOME/, or any vault location.
  - Never performs vault promotion. That is a human-gated session.
  - Workspace.json outputs[] array holds lightweight refs only — not full bodies.
  - Full output body is in the individual output JSON file.
  - promotion_candidate=True means eligible for review, NOT promoted.
  - All outputs start with status="intermediate".
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .prompt_builder import (
    build_evidence_packet_refs,
    get_knowledge_class,
    is_non_canonical_by_default,
    is_vault_writeback_candidate,
    make_output_slug,
    resolve_output_type,
)

# ── Paths ──────────────────────────────────────────────────────────────────────

_THIS_FILE    = Path(__file__).resolve()
# output/output_store.py -> parents[0]=output parents[1]=source_intelligence parents[2]=runtime parents[3]=vault
_VAULT_ROOT   = _THIS_FILE.parents[3]
_SIC_WORKSPACES = _VAULT_ROOT / "runtime" / "source_intelligence" / "workspaces"

_WORKSPACE_FILENAME = "workspace.json"
_OUTPUTS_DIRNAME    = "outputs"


# ── Public API ─────────────────────────────────────────────────────────────────


def save_output(workspace_id: str, generation_result: dict) -> dict:
    """
    Persist a generated output to workspace-local storage and record a ref
    in workspace.json outputs[].

    The persisted file contains the full output object (generated_text, citations,
    evidence_packet_refs, all metadata). workspace.json receives a lightweight ref
    (no body text — only metadata sufficient to list/filter/inspect outputs).

    Args:
        workspace_id:       Workspace slug (directory name).
        generation_result:  Output dict from generate_output().

    Returns:
        dict with keys:
            success        — bool
            output_id      — UUID of the persisted output
            output_path    — absolute path to the persisted output JSON
            output_filename — basename of the output file
            error          — error message string or None
    """
    result: dict = {
        "success":        False,
        "output_id":      None,
        "output_path":    None,
        "output_filename": None,
        "error":          None,
    }

    # ── 1. Validate generation_result is usable ────────────────────────────────
    gen_status = generation_result.get("generation_status", "unknown")
    if gen_status not in ("ok", "ok-stub"):
        result["error"] = (
            f"Cannot persist output with generation_status='{gen_status}'. "
            "Only 'ok' and 'ok-stub' outputs may be persisted."
        )
        return result

    output_type_raw = generation_result.get("output_type")
    if not output_type_raw:
        result["error"] = "generation_result missing 'output_type'."
        return result

    output_type = resolve_output_type(output_type_raw)
    query_text  = generation_result.get("query_text", "")

    # ── 2. Build output directory ──────────────────────────────────────────────
    ws_dir      = _SIC_WORKSPACES / workspace_id
    outputs_dir = ws_dir / _OUTPUTS_DIRNAME

    try:
        outputs_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["error"] = f"Could not create outputs/ directory: {exc}"
        return result

    # ── 3. Build output object ─────────────────────────────────────────────────
    now_iso    = _now_iso()
    output_id  = str(uuid.uuid4())
    evidence_packets  = generation_result.get("evidence_packets", [])
    evidence_count    = generation_result.get("evidence_count", len(evidence_packets))
    promotion_candidate = is_vault_writeback_candidate(
        output_type=output_type,
        evidence_count=evidence_count,
        generation_status=gen_status,
    )
    endorsement_status = "unendorsed" if is_non_canonical_by_default(output_type) else None
    knowledge_class    = get_knowledge_class(output_type)

    output_obj: dict = {
        # Identity
        "output_id":          output_id,
        "workspace_id":       workspace_id,
        "output_type":        output_type,
        "query_text":         query_text,
        "created_at":         now_iso,

        # Status
        "status":             "intermediate",
        "generation_status":  gen_status,
        "provider_name":      generation_result.get("provider_name"),
        "model_name":         generation_result.get("model_name"),
        "token_count":        generation_result.get("token_count"),

        # Content
        "generated_text":     generation_result.get("generated_text", ""),

        # Evidence and citations
        "evidence_packet_refs": build_evidence_packet_refs(evidence_packets),
        "evidence_count":       evidence_count,
        "citations":            generation_result.get("citations", []),

        # Classification and promotion
        "suggested_knowledge_class": knowledge_class,
        "promotion_candidate":       promotion_candidate,
        "endorsement_status":        endorsement_status,
        "promoted_path":             None,
        "promoted_at":               None,

        # Metadata
        "warnings":  generation_result.get("warnings", []),
        "notes":     "",
        "output_path": None,  # filled after write
    }

    # ── 4. Determine filename and write ───────────────────────────────────────
    ts_str   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug     = make_output_slug(query_text, max_len=32)
    filename = f"{ts_str}_{output_type}_{slug}.json"
    out_path = outputs_dir / filename

    output_obj["output_path"] = str(out_path)

    try:
        out_path.write_text(
            json.dumps(output_obj, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        result["error"] = f"Could not write output file at {out_path}: {exc}"
        return result

    # ── 5. Record lightweight ref in workspace.json ────────────────────────────
    ws_path = ws_dir / _WORKSPACE_FILENAME
    if ws_path.exists():
        try:
            workspace = json.loads(ws_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Non-fatal — output file is already written; warn but don't fail
            result["success"]        = True
            result["output_id"]      = output_id
            result["output_path"]    = str(out_path)
            result["output_filename"] = filename
            result["error"]          = (
                f"Output saved to {out_path} but could not update workspace.json: {exc}"
            )
            return result

        ref = _build_output_ref(output_obj, filename)
        workspace.setdefault("outputs", []).append(ref)
        workspace["output_count"] = len(workspace["outputs"])
        workspace["updated_at"] = now_iso

        try:
            ws_path.write_text(
                json.dumps(workspace, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            result["success"]        = True
            result["output_id"]      = output_id
            result["output_path"]    = str(out_path)
            result["output_filename"] = filename
            result["error"]          = (
                f"Output saved to {out_path} but could not write workspace.json: {exc}"
            )
            return result

    result["success"]        = True
    result["output_id"]      = output_id
    result["output_path"]    = str(out_path)
    result["output_filename"] = filename
    return result


def list_outputs(workspace_id: str) -> dict:
    """
    List all stored output refs for a workspace from workspace.json outputs[].

    Returns lightweight refs — not full output bodies. Use load_output() for full body.

    Args:
        workspace_id:  Workspace slug.

    Returns:
        dict with keys:
            success       — bool
            workspace_id  — str
            output_count  — int
            outputs       — list of lightweight output ref dicts
            error         — error message string or None
    """
    result: dict = {
        "success":      False,
        "workspace_id": workspace_id,
        "output_count": 0,
        "outputs":      [],
        "error":        None,
    }

    ws_path = _SIC_WORKSPACES / workspace_id / _WORKSPACE_FILENAME

    if not ws_path.exists():
        result["error"] = (
            f"Workspace '{workspace_id}' not found at {ws_path}. "
            "Run create_workspace() first."
        )
        return result

    try:
        workspace = json.loads(ws_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = f"Could not read workspace.json: {exc}"
        return result

    outputs = workspace.get("outputs", [])
    result["success"]      = True
    result["output_count"] = len(outputs)
    result["outputs"]      = outputs
    return result


def load_output(workspace_id: str, output_id_or_filename: str) -> dict:
    """
    Load a full stored output object from workspace-local storage.

    Accepts either an output_id (UUID) or a filename (e.g.
    '20260325_143022_briefing_funding-rates.json').

    Resolution order:
    1. Scan outputs/ directory for a file whose name matches output_id_or_filename.
    2. Scan outputs/ directory for a file whose content["output_id"] matches.
    3. Check workspace.json outputs[] refs for matching output_id and derive filename.

    Args:
        workspace_id:            Workspace slug.
        output_id_or_filename:   Output UUID or output filename.

    Returns:
        dict with keys:
            success     — bool
            output      — the full output object dict or None
            output_path — absolute path to the output file
            error       — error message string or None
    """
    result: dict = {
        "success":     False,
        "output":      None,
        "output_path": None,
        "error":       None,
    }

    outputs_dir = _SIC_WORKSPACES / workspace_id / _OUTPUTS_DIRNAME

    if not outputs_dir.exists():
        result["error"] = (
            f"No outputs/ directory found for workspace '{workspace_id}'. "
            "Generate and persist at least one output first."
        )
        return result

    target = output_id_or_filename

    # ── Pass 1: exact filename match ───────────────────────────────────────────
    candidate = outputs_dir / target
    if candidate.exists() and candidate.suffix == ".json":
        return _load_output_file(candidate, result)

    # ── Pass 2: scan for output_id match in file content ──────────────────────
    for f in sorted(outputs_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("output_id") == target:
                return _load_output_file(f, result)
        except (OSError, json.JSONDecodeError):
            continue

    # ── Pass 3: check workspace.json refs for filename hint ───────────────────
    ws_path = _SIC_WORKSPACES / workspace_id / _WORKSPACE_FILENAME
    if ws_path.exists():
        try:
            workspace = json.loads(ws_path.read_text(encoding="utf-8"))
            for ref in workspace.get("outputs", []):
                if ref.get("output_id") == target:
                    fname = ref.get("output_filename")
                    if fname:
                        f = outputs_dir / fname
                        if f.exists():
                            return _load_output_file(f, result)
        except (OSError, json.JSONDecodeError):
            pass

    result["error"] = (
        f"Output '{target}' not found in workspace '{workspace_id}' outputs/. "
        "Use list_outputs() to see available outputs."
    )
    return result


# ── Internal helpers ───────────────────────────────────────────────────────────


def _build_output_ref(output_obj: dict, filename: str) -> dict:
    """
    Build a lightweight output ref for storage in workspace.json outputs[].

    The ref contains metadata sufficient to list, filter, and identify outputs
    without loading the full output body.
    """
    return {
        "output_id":             output_obj["output_id"],
        "output_type":           output_obj["output_type"],
        "query_text":            (output_obj.get("query_text") or "")[:120],
        "created_at":            output_obj["created_at"],
        "status":                output_obj["status"],
        "generation_status":     output_obj["generation_status"],
        "suggested_knowledge_class": output_obj["suggested_knowledge_class"],
        "promotion_candidate":   output_obj["promotion_candidate"],
        "endorsement_status":    output_obj["endorsement_status"],
        "evidence_count":        output_obj["evidence_count"],
        "provider_name":         output_obj["provider_name"],
        "model_name":            output_obj["model_name"],
        "output_filename":       filename,
    }


def _load_output_file(path: Path, result: dict) -> dict:
    """Load a single output JSON file and populate a result dict."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        result["success"]     = True
        result["output"]      = data
        result["output_path"] = str(path)
        return result
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = f"Could not read output file at {path}: {exc}"
        return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
