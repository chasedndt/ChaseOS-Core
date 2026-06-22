"""
trace_idea.py -- ChaseOS AOR Phase 9 provenance foothold

Read-only lineage tracing workflow.

This first implementation pass favors honest partial traces over invented
completeness. It traverses provenance blocks when present, acquisition artifact
references, source-package refs, and audit/log file refs, then writes a bounded
trace report to 07_LOGS/Trace-Reports/.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


class WorkflowExecutionError(RuntimeError):
    """Fail-closed workflow error for trace_idea handler."""


@dataclass
class TraceNode:
    key: str
    artifact_id: str | None
    artifact_type: str | None
    path: str
    classification: str
    relation: str
    exists: bool
    created_at: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "path": self.path,
            "classification": self.classification,
            "relation": self.relation,
            "exists": self.exists,
            "created_at": self.created_at,
            "note": self.note,
        }


def run_trace_idea(inputs: dict, vault_root: Path) -> dict:
    artifact_id = _resolve_artifact_id(inputs)
    run_date = _resolve_run_date(inputs.get("date"))

    root_record = _find_artifact_record(artifact_id, vault_root)
    trace_result = _build_trace_result(artifact_id=artifact_id, root_record=root_record, vault_root=vault_root)

    relative_output_path = Path("07_LOGS") / "Trace-Reports" / f"{run_date.isoformat()}-trace-{_slugify(artifact_id)}.md"
    content = _render_trace_report(run_date=run_date, artifact_id=artifact_id, trace_result=trace_result)

    return {
        "handler_status": "executed",
        "workflow_id": "trace_idea",
        "date": run_date.isoformat(),
        "trace_result": trace_result,
        "summary": trace_result["summary"],
        "writebacks": [
            {
                "path": str(relative_output_path).replace("\\", "/"),
                "content": content,
                "content_type": "text/markdown",
            }
        ],
    }


def _resolve_artifact_id(inputs: dict[str, Any]) -> str:
    for field in ("artifact_id", "target_id", "idea_id"):
        value = str(inputs.get(field, "")).strip()
        if value:
            return value
    raise WorkflowExecutionError("trace_idea requires artifact_id (or target_id / idea_id)")


def _resolve_run_date(raw_value: object) -> date:
    if raw_value in (None, ""):
        return date.today()
    if isinstance(raw_value, date):
        return raw_value
    try:
        return date.fromisoformat(str(raw_value))
    except ValueError as exc:
        raise WorkflowExecutionError(f"invalid date input {raw_value!r}; expected YYYY-MM-DD") from exc


def _build_trace_result(artifact_id: str, root_record: dict[str, Any] | None, vault_root: Path) -> dict[str, Any]:
    if root_record is None:
        return {
            "artifact_id": artifact_id,
            "found": False,
            "message": f"Artifact {artifact_id!r} not found in declared trace surfaces.",
            "lineage_items": [],
            "summary": {
                "source_artifact_count": 0,
                "derived_artifact_count": 0,
                "audit_artifact_count": 0,
                "gap_count": 1,
            },
            "gaps": [f"No declared artifact or provenance block was found for {artifact_id!r}."],
        }

    queue: deque[tuple[dict[str, Any], str]] = deque([(root_record, "queried_artifact")])
    lineage_items: list[TraceNode] = []
    seen: set[str] = set()
    gaps: list[str] = []

    while queue:
        record, relation = queue.popleft()
        node = _node_from_record(record, relation=relation, vault_root=vault_root)
        if node.key in seen:
            continue
        seen.add(node.key)
        lineage_items.append(node)

        for ref_kind, ref_value, child_relation in _iter_related_refs(record):
            if ref_kind == "id":
                child_record = _find_artifact_record(ref_value, vault_root)
                if child_record is None:
                    gaps.append(f"Referenced artifact id {ref_value!r} could not be resolved from {node.path}.")
                    continue
                queue.append((child_record, child_relation))
                continue

            path_record = _record_from_path(ref_value, vault_root)
            if path_record is None:
                gaps.append(f"Referenced path {ref_value!r} could not be resolved from {node.path}.")
                continue
            queue.append((path_record, child_relation))

    summary = {
        "source_artifact_count": sum(1 for item in lineage_items if item.classification == "source_artifact"),
        "derived_artifact_count": sum(1 for item in lineage_items if item.classification == "derived_artifact"),
        "audit_artifact_count": sum(1 for item in lineage_items if item.classification == "audit_artifact"),
        "gap_count": len(gaps),
    }

    return {
        "artifact_id": artifact_id,
        "found": True,
        "message": f"Trace resolved {len(lineage_items)} lineage item(s) for {artifact_id!r}.",
        "lineage_items": [item.to_dict() for item in lineage_items],
        "summary": summary,
        "gaps": gaps,
    }


def _node_from_record(record: dict[str, Any], relation: str, vault_root: Path) -> TraceNode:
    relative_path = _relative_path(record["path"], vault_root)
    return TraceNode(
        key=record["key"],
        artifact_id=record.get("artifact_id"),
        artifact_type=record.get("artifact_type"),
        path=relative_path,
        classification=record["classification"],
        relation=relation,
        exists=True,
        created_at=record.get("created_at"),
        note=record.get("note"),
    )


def _iter_related_refs(record: dict[str, Any]) -> list[tuple[str, str, str]]:
    data = record.get("data") or {}
    refs: list[tuple[str, str, str]] = []

    provenance = data.get("provenance") if isinstance(data, dict) else None
    if isinstance(provenance, dict):
        for source_id in provenance.get("source_ids", []) or []:
            if isinstance(source_id, str) and source_id.strip():
                refs.append(("id", source_id.strip(), "provenance_source_id"))
        for source_ref in provenance.get("source_refs", []) or []:
            if isinstance(source_ref, str) and source_ref.strip():
                refs.append(("path", source_ref.strip(), "provenance_source_ref"))
        for audit_ref in provenance.get("audit_refs", []) or []:
            if isinstance(audit_ref, str) and audit_ref.strip():
                refs.append(("path", audit_ref.strip(), "audit_ref"))
        for entry in provenance.get("lineage_chain", []) or []:
            if isinstance(entry, dict):
                ref = entry.get("ref")
                if isinstance(ref, str) and ref.strip():
                    refs.append(("path", ref.strip(), f"lineage_chain:{entry.get('stage', 'unknown')}"))

    for field in ("source_packet_refs",):
        for value in data.get(field, []) or []:
            if isinstance(value, str) and value.strip():
                refs.append(("id", value.strip(), field))

    normalized_source_pack_ref = data.get("normalized_source_pack_ref")
    if isinstance(normalized_source_pack_ref, str) and normalized_source_pack_ref.strip():
        refs.append(("id", normalized_source_pack_ref.strip(), "normalized_source_pack_ref"))

    source_origin = data.get("source_origin") if isinstance(data, dict) else None
    source_id = data.get("source_id")
    if (
        isinstance(source_id, str)
        and source_id.strip()
        and not (isinstance(source_origin, dict) and isinstance(source_origin.get("ref"), str) and source_origin.get("ref", "").strip())
    ):
        refs.append(("id", source_id.strip(), "source_id"))

    if isinstance(source_origin, dict):
        ref = source_origin.get("ref")
        if isinstance(ref, str) and ref.strip():
            refs.append(("path", ref.strip(), "source_origin"))

    audit = data.get("audit") if isinstance(data, dict) else None
    if isinstance(audit, dict):
        activity_ref = audit.get("activity_log_ref")
        if isinstance(activity_ref, str) and activity_ref.strip():
            refs.append(("path", activity_ref.strip(), "activity_log_ref"))

    deduped: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ref_kind, ref_value, relation in refs:
        key = (ref_kind, ref_value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((ref_kind, ref_value, relation))
    return deduped


def _find_artifact_record(artifact_id: str, vault_root: Path) -> dict[str, Any] | None:
    for root in _artifact_roots(vault_root):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            data = _read_json(path)
            if not isinstance(data, dict):
                continue
            if data.get("artifact_id") == artifact_id:
                return _record_from_json(path, data, vault_root)
            if data.get("id") == artifact_id:
                return _record_from_json(path, data, vault_root)
            provenance = data.get("provenance")
            if isinstance(provenance, dict) and artifact_id in (provenance.get("source_ids") or []):
                return _record_from_json(path, data, vault_root)
    return None


def _artifact_roots(vault_root: Path) -> list[Path]:
    return [
        vault_root / "runtime" / "workflows",
        vault_root / "runtime" / "acquisition",
        vault_root / "runtime" / "source_intelligence",
    ]


def _record_from_path(relative_path: str, vault_root: Path) -> dict[str, Any] | None:
    path = vault_root / relative_path
    if not path.exists():
        return None

    if path.suffix.lower() == ".json":
        data = _read_json(path)
        if isinstance(data, dict):
            return _record_from_json(path, data, vault_root)

    classification = "audit_artifact" if "07_LOGS" in path.parts else "source_artifact"
    return {
        "key": f"path::{relative_path}",
        "artifact_id": None,
        "artifact_type": "log_ref" if classification == "audit_artifact" else "file_ref",
        "path": path,
        "classification": classification,
        "created_at": None,
        "data": {},
        "note": "path reference only; structured artifact metadata not available",
    }


def _record_from_json(path: Path, data: dict[str, Any], vault_root: Path) -> dict[str, Any]:
    artifact_id = data.get("artifact_id") or data.get("id")
    artifact_type = data.get("artifact_type") or ("source_package" if data.get("origin_path") else "json_record")
    classification = _classify_record(path, data)
    key_value = artifact_id if isinstance(artifact_id, str) and artifact_id.strip() else _relative_path(path, vault_root)
    return {
        "key": f"artifact::{key_value}",
        "artifact_id": artifact_id if isinstance(artifact_id, str) else data.get("id") if isinstance(data.get("id"), str) else None,
        "artifact_type": artifact_type,
        "path": path,
        "classification": classification,
        "created_at": _first_string(data, "created_at", "updated_at", "package_created_date"),
        "data": data,
        "note": None,
    }


def _classify_record(path: Path, data: dict[str, Any]) -> str:
    artifact_type = str(data.get("artifact_type") or "").strip()
    if "07_LOGS" in path.parts:
        return "audit_artifact"
    if artifact_type == "source_packet":
        return "source_artifact"
    if data.get("origin_path") or data.get("source_type"):
        return "source_artifact"
    if artifact_type in {"normalized_source_pack", "briefing_ready_input_set", "generated_output"}:
        return "derived_artifact"
    return "derived_artifact"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _render_trace_report(run_date: date, artifact_id: str, trace_result: dict[str, Any]) -> str:
    lines = [
        "---",
        "type: trace-report",
        f"date: {run_date.isoformat()}",
        f"artifact_id: {artifact_id}",
        f"found: {str(trace_result['found']).lower()}",
        "workflow: trace_idea",
        "---",
        "",
        f"# Trace Report — {artifact_id}",
        "",
        f"**Status:** {trace_result['message']}",
        "",
        "## Summary",
        "",
    ]

    summary = trace_result["summary"]
    for key in ("source_artifact_count", "derived_artifact_count", "audit_artifact_count", "gap_count"):
        lines.append(f"- **{key.replace('_', ' ').title()}**: {summary[key]}")

    lines.extend([
        "",
        "## Lineage Items",
        "",
    ])

    if not trace_result["lineage_items"]:
        lines.append("- No lineage items were resolved.")
    else:
        for item in trace_result["lineage_items"]:
            artifact_label = item.get("artifact_id") or item.get("path")
            lines.append(
                f"- `{artifact_label}` — {item['classification']} via `{item['relation']}`"
                f" (`{item['path']}`)"
            )

    lines.extend([
        "",
        "## Gaps",
        "",
    ])
    if not trace_result["gaps"]:
        lines.append("- No unresolved gaps were encountered in this trace pass.")
    else:
        for gap in trace_result["gaps"]:
            lines.append(f"- {gap}")

    lines.extend([
        "",
        "> This report is derivative, not sovereign truth. Missing lineage is reported as a gap rather than invented.",
        "",
    ])
    return "\n".join(lines)


def _first_string(data: dict[str, Any], *fields: str) -> str | None:
    for field in fields:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _relative_path(path: Path | str, vault_root: Path) -> str:
    path_obj = Path(path)
    try:
        return str(path_obj.relative_to(vault_root)).replace("\\", "/")
    except ValueError:
        return str(path_obj).replace("\\", "/")


def _slugify(value: str) -> str:
    output = []
    previous_dash = False
    for char in value.strip().lower():
        if char.isalnum():
            output.append(char)
            previous_dash = False
        elif not previous_dash:
            output.append("-")
            previous_dash = True
    slug = "".join(output).strip("-")
    return slug or "trace"
