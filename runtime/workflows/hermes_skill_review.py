"""
hermes_skill_review.py — Hermes Skill Quarantine Review Workflow

Scans 03_INPUTS/00_QUARANTINE/ for AI-generated or synthesized content candidates
and produces a structured review proposal. Hermes Workflow Class 3.

Governance boundary:
  - Read-only scan of quarantine; writes proposal report + audit log only
  - Never auto-promotes, auto-endorses, or auto-applies any quarantined item
  - All promotion requires explicit operator Gate endorsement

Inputs:
  min_items     int   (optional, default 0) minimum items before writing report
  max_scan      int   (optional, default 50) max files to scan
  filter_class  str   (optional) filter to a specific input_class; default all

Outputs:
  writebacks    list  skill-review report + agent-activity audit
  items_scanned int   total quarantine files scanned
  candidates    int   items flagged as skill/learning candidates
  report_path   str   relative path of the review report

AOR engine registration:
  _handlers["hermes_skill_review"] = run_hermes_skill_review
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class WorkflowExecutionError(Exception):
    """Fail-closed execution errors that map to AOR escalation."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Candidate detection ───────────────────────────────────────────────────────

_SKILL_ORIGIN_KINDS = {"ai-generated", "synthesized"}
_SKILL_INPUT_CLASSES = {"generated-ideas", "digest"}
_HERMES_SOURCE_PREFIXES = ("hermes-", "hermes_")


def _is_skill_candidate(meta: dict) -> bool:
    """Return True if this sidecar looks like a Hermes skill/learning candidate."""
    origin_kind = (meta.get("origin_kind") or "").lower()
    input_class = (meta.get("input_class") or "").lower()
    source = (meta.get("source") or "").lower()
    source_platform = (meta.get("source_platform") or "").lower()

    if origin_kind in _SKILL_ORIGIN_KINDS:
        return True
    if input_class == "generated-ideas":
        return True
    if any(source.startswith(p) for p in _HERMES_SOURCE_PREFIXES):
        return True
    if source_platform in ("hermes", "llm"):
        return True
    return False


def _format_candidate(meta: dict, content_path: str) -> str:
    """Format a single candidate entry for the review report."""
    title = meta.get("title") or meta.get("slug") or Path(content_path).name
    origin = meta.get("origin_kind", "unknown")
    cls = meta.get("input_class", "unknown")
    source = meta.get("source", "unknown")
    captured = (meta.get("captured_at") or "")[:19]
    domain = meta.get("domain_hint", "")
    project = meta.get("project_hint", "")

    lines = [
        f"### `{content_path}`",
        f"- **Title:** {title}",
        f"- **Origin:** {origin} | **Class:** {cls}",
        f"- **Source:** {source}",
        f"- **Captured:** {captured}",
    ]
    if domain:
        lines.append(f"- **Domain hint:** {domain}")
    if project:
        lines.append(f"- **Project hint:** {project}")
    lines += [
        "",
        "**Review actions:** [ ] promote  [ ] quarantine-extend  [ ] reject",
        "",
    ]
    return "\n".join(lines)


# ── Quarantine scan ───────────────────────────────────────────────────────────

def _scan_quarantine(
    vault_root: Path,
    max_scan: int,
    filter_class: Optional[str],
) -> tuple[list[dict], int]:
    """
    Scan quarantine for .meta.json sidecars.

    Returns (candidates, total_scanned).
    """
    quarantine_root = vault_root / "03_INPUTS" / "00_QUARANTINE"
    if not quarantine_root.exists():
        return [], 0

    meta_files = sorted(quarantine_root.rglob("*.meta.json"))[:max_scan]
    total_scanned = len(meta_files)
    candidates: list[dict] = []

    for meta_path in meta_files:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue

        if filter_class and (meta.get("input_class") or "") != filter_class:
            continue

        if _is_skill_candidate(meta):
            content_file = meta_path.parent / meta_path.name.replace(".meta.json", "")
            try:
                rel = str(meta_path.relative_to(vault_root)).replace("\\", "/")
            except ValueError:
                rel = str(meta_path)
            candidates.append({"meta": meta, "rel_path": rel})

    return candidates, total_scanned


# ── Report builder ────────────────────────────────────────────────────────────

def _build_review_report(candidates: list[dict], items_scanned: int) -> str:
    lines = [
        "# Hermes Skill Quarantine Review",
        "",
        f"**Generated:** {_now_iso()[:19]}Z",
        f"**Items scanned:** {items_scanned}",
        f"**Candidates identified:** {len(candidates)}",
        "",
        "---",
        "",
    ]

    if not candidates:
        lines += [
            "## No Candidates Found",
            "",
            "No AI-generated or synthesized items matching skill/learning criteria",
            "were found in the quarantine at this time.",
            "",
        ]
    else:
        lines += [
            "## Candidates for Operator Review",
            "",
            "Each item below is a quarantined artifact that may represent a skill,",
            "pattern, or learning artifact generated by Hermes or another AI runtime.",
            "No item is auto-promoted. Operator action required for each.",
            "",
        ]
        for i, candidate in enumerate(candidates, 1):
            lines.append(f"### Candidate {i}")
            meta = candidate["meta"]
            rel_path = candidate["rel_path"]
            lines.append(_format_candidate(meta, rel_path))

    lines += [
        "---",
        "",
        "## Governance Boundary",
        "",
        "This report is a **quarantine scan result only**.",
        "- No items have been promoted, endorsed, or applied",
        "- All promotion requires explicit operator Gate endorsement",
        "- Rejected items must be deleted manually by the operator",
        "- This report does not modify any quarantine files",
        "",
    ]
    return "\n".join(lines)


# ── Audit log ─────────────────────────────────────────────────────────────────

def _build_audit_content(
    items_scanned: int,
    candidates: int,
    report_path: Optional[str],
    run_iso: str,
) -> str:
    return f"""---
type: agent-activity
workflow: hermes_skill_review
runtime: Hermes
date: {run_iso[:10]}
items_scanned: {items_scanned}
candidates_identified: {candidates}
authority: read-scan-report-only
---

# Hermes Skill Quarantine Review

**Date:** {run_iso}
**Items scanned:** {items_scanned}
**Candidates identified:** {candidates}
**Report written to:** `{report_path or "none"}`

## Boundary Statement

This workflow scanned quarantine for AI-generated or synthesized artifacts.
It produced a review proposal only. It did not promote, endorse, apply, or
modify any quarantined item. Promotion requires explicit operator Gate endorsement.
"""


# ── Public handler ─────────────────────────────────────────────────────────────

def run_hermes_skill_review(
    inputs: dict[str, Any],
    vault_root: Path,
) -> dict[str, Any]:
    """
    Hermes Skill Quarantine Review workflow handler.

    Scans quarantine for AI-generated candidates and produces a review proposal.
    """
    max_scan = int(inputs.get("max_scan") or 50)
    min_items = int(inputs.get("min_items") or 0)
    filter_class = str(inputs.get("filter_class") or "").strip() or None

    candidates, items_scanned = _scan_quarantine(vault_root, max_scan, filter_class)

    run_iso = _now_iso()
    ts = _now_ts()

    report_path: Optional[str] = None
    report_content = _build_review_report(candidates, items_scanned)

    if candidates and len(candidates) >= min_items:
        skill_review_dir = vault_root / "07_LOGS" / "Skill-Review"
        skill_review_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{ts}__hermes__skill-review.md"
        full_path = skill_review_dir / filename
        full_path.write_text(report_content, encoding="utf-8")
        report_path = f"07_LOGS/Skill-Review/{filename}"

    audit_content = _build_audit_content(
        items_scanned=items_scanned,
        candidates=len(candidates),
        report_path=report_path,
        run_iso=run_iso,
    )
    audit_filename = f"{ts}__hermes__skill_review.md"
    audit_path = f"07_LOGS/Agent-Activity/{audit_filename}"

    writebacks = [{"path": audit_path, "content": audit_content}]
    if report_path:
        writebacks.append({"path": report_path, "content": report_content})

    return {
        "items_scanned": items_scanned,
        "candidates": len(candidates),
        "report_path": report_path,
        "writebacks": writebacks,
    }
