"""
operator_today.py -- ChaseOS AOR Phase 9 Briefing V2

Implements the four-layer briefing model:
  [CANONICAL]       Current vault state — exactly as found, no synthesis
  [CARRY-FORWARD]   Context from the most recent close note
  [SOURCED]         Operational context from real runtime data
  [SYNTHESIS]       AI-synthesized recommendations, clearly labeled

Design authority: 06_AGENTS/Operator-Briefing-Architecture.md
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable


# Generic, non-personal example domain→project-OS map. Operators replace this with their
# own projects (e.g. via a vault config). Core ships illustrative defaults only — no
# instance/personal project names.
ACTIVE_NOW_PROJECT_MAP: dict[str, str] = {
    "ChaseOS / System Infrastructure": "01_PROJECTS/ChaseOS/ChaseOS-OS.md",
    "Example Domain": "01_PROJECTS/Example/Example-OS.md",
}


class WorkflowExecutionError(RuntimeError):
    """Fail-closed workflow error for bounded AOR handlers."""


@dataclass
class ProjectSnapshot:
    domain: str
    project_path: str
    open_loops: list[str]
    next_actions: list[str]


def run_operator_today(inputs: dict, vault_root: Path) -> dict:
    run_date = _resolve_run_date(inputs.get("date"))
    output_format = (inputs.get("output_format") or "markdown").strip().lower()
    if output_format not in {"markdown", "json"}:
        raise WorkflowExecutionError(
            f"unsupported output_format={output_format!r}; expected 'markdown' or 'json'"
        )

    now_path = vault_root / "00_HOME" / "Now.md"
    inputs_root = vault_root / "03_INPUTS"
    build_logs_dir = vault_root / "07_LOGS" / "Build-Logs"
    decision_ledger_dir = vault_root / "07_LOGS" / "Decision-Ledger"
    briefs_dir = vault_root / "07_LOGS" / "Operator-Briefs"
    activity_dir = vault_root / "07_LOGS" / "Agent-Activity"

    required_paths = [now_path, inputs_root, build_logs_dir, decision_ledger_dir]
    missing = [str(path.relative_to(vault_root)) for path in required_paths if not path.exists()]
    if missing:
        raise WorkflowExecutionError(
            f"operator_today required context missing: {missing}"
        )

    files_read: list[str] = []

    # ── Layer 1: CANONICAL reads ────────────────────────────────────────────────
    now_text = _read_text(now_path)
    files_read.append("00_HOME/Now.md")

    current_phase = _extract_section(now_text, "## Current Phase")
    next_phase_action = _extract_next_phase_action(now_text)
    active_rows = _extract_active_now_rows(now_text)
    if not active_rows:
        raise WorkflowExecutionError(
            "operator_today could not resolve any Active Now rows from 00_HOME/Now.md"
        )

    project_snapshots: list[ProjectSnapshot] = []
    for row in active_rows:
        domain = row["domain"]
        project_rel = ACTIVE_NOW_PROJECT_MAP.get(domain)
        if project_rel is None:
            raise WorkflowExecutionError(
                f"operator_today has no project file mapping for active domain {domain!r}"
            )
        project_path = vault_root / project_rel
        if not project_path.exists():
            raise WorkflowExecutionError(
                f"operator_today required project file missing: {project_rel}"
            )
        project_text = _read_text(project_path)
        files_read.append(project_rel)
        project_snapshots.append(
            ProjectSnapshot(
                domain=domain,
                project_path=project_rel,
                open_loops=_extract_checklist_items(project_text, {"open loops"})[:3],
                next_actions=_extract_checklist_items(
                    project_text, {"immediate next actions", "next actions"}
                )[:3],
            )
        )

    # ROADMAP.md phase line (canonical — narrow scope, first 30 lines only)
    roadmap_phase = _read_roadmap_phase_line(vault_root)
    if roadmap_phase:
        files_read.append("ROADMAP.md")

    # ── Layer 1: contradiction detection ───────────────────────────────────────
    contradictions = _detect_contradictions(current_phase, roadmap_phase)

    # ── Layer 2: CARRY-FORWARD read ────────────────────────────────────────────
    carry_forward = {"date": "none", "items": [], "files_read": []}
    if briefs_dir.exists():
        carry_forward = _read_carry_forward(briefs_dir, vault_root)
    files_read.extend(carry_forward["files_read"])

    # ── Layer 3: SOURCED reads ─────────────────────────────────────────────────
    intake_surface = _resolve_intake_surface(inputs_root)
    quarantine_summary = _summarize_quarantine(intake_surface, vault_root)
    files_read.extend(quarantine_summary["files_read"])

    build_log_summary = _summarize_recent_files(
        build_logs_dir,
        vault_root,
        exclude_names={"Build-Logs-Index.md"},
        limit=5,
    )
    files_read.extend(build_log_summary["files_read"])

    decision_summary = _read_decision_ledger(decision_ledger_dir, vault_root)
    files_read.extend(decision_summary["files_read"])

    aor_activity = {"runs": [], "success": 0, "escalated": 0, "failed": 0, "files_read": []}
    if activity_dir.exists():
        aor_activity = _read_agent_activity_summary(activity_dir, vault_root, cutoff_hours=48)
    files_read.extend(aor_activity["files_read"])

    scorecard_data = _read_scorecard_summaries(vault_root)
    files_read.extend(scorecard_data["files_read"])

    # ── Layer 4: SYNTHESIS ─────────────────────────────────────────────────────
    synthesis = _build_synthesis(
        next_phase_action=next_phase_action,
        quarantine_total=quarantine_summary["total_pending"],
        project_snapshots=project_snapshots,
        carry_forward_items=carry_forward["items"],
    )

    summary = {
        "date": run_date.isoformat(),
        # Layer 1
        "current_phase": current_phase,
        "active_focus": active_rows,
        "project_snapshots": [
            {
                "domain": s.domain,
                "project_path": s.project_path,
                "open_loops": s.open_loops,
                "next_actions": s.next_actions,
            }
            for s in project_snapshots
        ],
        "roadmap_phase_line": roadmap_phase,
        "contradictions": contradictions,
        # Layer 2
        "carry_forward": carry_forward,
        # Layer 3
        "quarantine": {
            "total_pending": quarantine_summary["total_pending"],
            "per_class": quarantine_summary["per_class"],
            "recent_items": quarantine_summary["recent_items"],
        },
        "recent_build_logs": build_log_summary["entries"],
        "recent_decisions": decision_summary["entries"],
        "aor_activity": aor_activity,
        "scorecards": scorecard_data,
        # Layer 4
        "synthesis": synthesis,
        # Metadata
        "files_read": sorted(dict.fromkeys(files_read)),
    }

    relative_output_path = Path("07_LOGS") / "Operator-Briefs" / (
        f"{run_date.isoformat()}-operator-today.{'json' if output_format == 'json' else 'md'}"
    )

    content = (
        json.dumps(summary, indent=2, ensure_ascii=False)
        if output_format == "json"
        else _render_markdown_brief_v2(summary)
    )

    return {
        "handler_status": "executed",
        "workflow_id": "operator_today",
        "date": run_date.isoformat(),
        "output_format": output_format,
        "files_read": summary["files_read"],
        "contradictions_flagged": len(contradictions),
        "carry_forward_date": carry_forward["date"],
        "summary": {
            "quarantine_total_pending": quarantine_summary["total_pending"],
            "build_log_count_considered": len(build_log_summary["entries"]),
            "decision_count_considered": len(decision_summary["entries"]),
            "active_domain_count": len(active_rows),
            "carry_forward_items": len(carry_forward["items"]),
            "contradictions": len(contradictions),
            "aor_runs_last_48h": len(aor_activity["runs"]),
        },
        "writebacks": [
            {
                "path": str(relative_output_path).replace("\\", "/"),
                "content": content,
                "content_type": "application/json" if output_format == "json" else "text/markdown",
            }
        ],
    }


# ── Carry-forward reader ───────────────────────────────────────────────────────

def _read_carry_forward(briefs_dir: Path, vault_root: Path) -> dict:
    """Read the most recent operator_close_day note and extract carry-forward items."""
    # Match both v1 and v2 close note filename patterns
    close_notes = sorted(
        [
            p for p in briefs_dir.iterdir()
            if p.is_file() and p.suffix == ".md"
            and ("close-day" in p.name or "close_day" in p.name)
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not close_notes:
        return {"date": "none", "items": [], "files_read": []}

    most_recent = close_notes[0]
    rel_path = str(most_recent.relative_to(vault_root)).replace("\\", "/")
    text = _read_text(most_recent)

    # Try v2 carry-forward section first
    items = _extract_carry_forward_v2(text)

    # Fall back to v1 operator-provided open loops section
    if not items:
        items = _extract_carry_forward_v1(text)

    # Extract date from filename (YYYY-MM-DD prefix)
    cf_date = _extract_date_from_filename(most_recent.name)

    return {"date": cf_date, "items": items, "files_read": [rel_path]}


def _extract_carry_forward_v2(text: str) -> list[str]:
    """Parse v2 carry-forward section: lines with 'status:X — text'."""
    items: list[str] = []
    in_section = False
    for line in text.splitlines():
        if "## [CARRY-FORWARD]" in line and "Open Loops for Tomorrow" in line:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        # Match: - status:open — loop text
        m = re.match(r"^- status:(\w+)\s+[—-]+\s+(.+)$", line.strip())
        if m:
            status, text_part = m.group(1), m.group(2).strip()
            if status != "resolved":
                items.append(f"[{status}] {text_part}")
    return items


def _extract_carry_forward_v1(text: str) -> list[str]:
    """Parse v1 close note: Operator-Provided Open Loops section."""
    items: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## Operator-Provided Open Loops"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and "None provided" not in stripped:
            items.append(stripped[2:].strip())
    return items


def _extract_date_from_filename(name: str) -> str:
    """Extract YYYY-MM-DD prefix from filename, or return 'unknown'."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", name)
    return m.group(1) if m else "unknown"


# ── Roadmap phase reader ───────────────────────────────────────────────────────

def _read_roadmap_phase_line(vault_root: Path) -> str:
    """Read the first 30 lines of ROADMAP.md and extract the active phase line."""
    roadmap_path = vault_root / "ROADMAP.md"
    if not roadmap_path.exists():
        return ""
    lines = _read_text(roadmap_path).splitlines()[:30]
    for line in lines:
        stripped = line.strip()
        if "Phase" in stripped and ("ACTIVE" in stripped or "active" in stripped):
            return stripped.lstrip("#").strip()
    return ""


# ── Agent activity reader ──────────────────────────────────────────────────────

def _read_agent_activity_summary(
    activity_dir: Path,
    vault_root: Path,
    cutoff_hours: int = 48,
) -> dict:
    """Read AOR activity JSON files from the last N hours. Status fields only."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=cutoff_hours)
    runs: list[dict] = []
    files_read: list[str] = []

    for path in sorted(activity_dir.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        # Parse timestamp from filename: YYYYMMDD-HHMMSS__workflow__hash.json
        ts = _parse_activity_timestamp(path.name)
        if ts is None or ts < cutoff:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        runs.append({
            "workflow_id": data.get("workflow_id", "unknown"),
            "status": data.get("status", "unknown"),
            "timestamp": data.get("timestamp_utc", ""),
        })
        files_read.append(str(path.relative_to(vault_root)).replace("\\", "/"))

    success = sum(1 for r in runs if r["status"] == "success")
    escalated = sum(1 for r in runs if r["status"] == "escalated")
    failed = sum(1 for r in runs if r["status"] not in {"success", "escalated", "dry_run_ok"})

    return {
        "runs": runs,
        "success": success,
        "escalated": escalated,
        "failed": failed,
        "files_read": [str(activity_dir.relative_to(vault_root)).replace("\\", "/")] if runs else [],
    }


def _parse_activity_timestamp(filename: str) -> datetime | None:
    """Parse datetime from YYYYMMDD-HHMMSS__... filename pattern."""
    m = re.match(r"^(\d{8})-(\d{6})__", filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


# ── Contradiction detection ────────────────────────────────────────────────────

def _detect_contradictions(now_phase: str, roadmap_phase: str) -> list[str]:
    """Flag explicit contradictions between canonical inputs."""
    contradictions: list[str] = []

    if not now_phase or not roadmap_phase:
        return contradictions

    # Extract phase numbers from each source
    now_numbers = set(re.findall(r"Phase\s+(\d+)", now_phase))
    roadmap_numbers = set(re.findall(r"Phase\s+(\d+)", roadmap_phase))

    if now_numbers and roadmap_numbers and now_numbers != roadmap_numbers:
        contradictions.append(
            f"Phase number mismatch: Now.md references Phase {', '.join(sorted(now_numbers))} "
            f"but ROADMAP.md active line references Phase {', '.join(sorted(roadmap_numbers))}. "
            f"Verify which is authoritative before acting on phase assumptions."
        )

    return contradictions


# ── Decision ledger reader ─────────────────────────────────────────────────────

def _read_decision_ledger(ledger_dir: Path, vault_root: Path) -> dict:
    """Read Decision-Ledger/Index.md and recent decision files."""
    if not ledger_dir.exists():
        raise WorkflowExecutionError(
            f"required runtime directory missing: {ledger_dir.relative_to(vault_root)}"
        )

    files_read: list[str] = []
    entries: list[str] = []

    # Read Index.md if present
    index_path = ledger_dir / "Index.md"
    if index_path.exists():
        files_read.append(str(index_path.relative_to(vault_root)).replace("\\", "/"))
        # Extract table rows from the index — last 7 days
        cutoff_date = (date.today() - timedelta(days=7)).isoformat()
        index_text = _read_text(index_path)
        for line in index_text.splitlines():
            if not line.startswith("| 20"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 3 and cells[0] >= cutoff_date:
                entries.append(f"{cells[0]} — {cells[2]}")
        # If no recent entries, fall back to listing all
        if not entries:
            for line in index_text.splitlines():
                if not line.startswith("| 20"):
                    continue
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) >= 3:
                    entries.append(f"{cells[0]} — {cells[2]}")
    else:
        # Fall back to listing recent files
        files = [
            p for p in ledger_dir.iterdir()
            if p.is_file() and p.name != "Index.md"
        ]
        for path in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
            entries.append(str(path.relative_to(vault_root)).replace("\\", "/"))
            files_read.append(str(path.relative_to(vault_root)).replace("\\", "/"))

    return {"entries": entries, "files_read": files_read}


# ── Synthesis builder ──────────────────────────────────────────────────────────

def _build_synthesis(
    next_phase_action: str | None,
    quarantine_total: int,
    project_snapshots: Iterable[ProjectSnapshot],
    carry_forward_items: list[str],
) -> dict:
    """Build the Layer 4 synthesis recommendations."""
    priority_actions: list[str] = []

    if next_phase_action:
        priority_actions.append(
            f"{next_phase_action} [derived from: Now.md next-pass line]"
        )
    if quarantine_total > 0:
        priority_actions.append(
            f"Review {quarantine_total} pending quarantine item(s) before starting new promotion work. "
            f"[derived from: SOURCED quarantine queue]"
        )

    for snapshot in project_snapshots:
        candidate = snapshot.next_actions[:1] or snapshot.open_loops[:1]
        if candidate:
            priority_actions.append(
                f"{snapshot.domain}: {candidate[0]} [derived from: {snapshot.project_path}]"
            )
        if len(priority_actions) >= 5:
            break

    if not priority_actions:
        priority_actions.append(
            "Re-anchor on 00_HOME/Now.md before making any Phase 9 scope changes. "
            "[derived from: no specific next action found]"
        )

    open_loop_check: list[str] = []
    for item in carry_forward_items[:5]:
        open_loop_check.append(f"[carried] {item}")
    if not open_loop_check:
        open_loop_check.append("No carry-forward open loops. (No prior close note, or no loops recorded.)")

    return {
        "priority_actions": priority_actions[:5],
        "open_loop_check": open_loop_check,
    }


# ── Existing helper functions (unchanged) ──────────────────────────────────────

def _resolve_run_date(raw_value: object) -> date:
    if raw_value in (None, ""):
        return date.today()
    if isinstance(raw_value, date):
        return raw_value
    try:
        return date.fromisoformat(str(raw_value))
    except ValueError as exc:
        raise WorkflowExecutionError(
            f"invalid date input {raw_value!r}; expected YYYY-MM-DD"
        ) from exc


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    capture = False

    for line in lines:
        if line.startswith("## ") and line.strip() == heading:
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        if capture and line.strip():
            return line.strip().lstrip("#").strip()

    raise WorkflowExecutionError(f"required section {heading!r} missing from 00_HOME/Now.md")


def _extract_next_phase_action(text: str) -> str | None:
    for line in text.splitlines():
        match = re.search(r"Pass \d+ \(NEXT\):\s*(.+)$", line)
        if match:
            return match.group(1).strip()
    return None


def _extract_active_now_rows(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    in_section = False
    rows: list[dict[str, str]] = []

    for line in lines:
        if line.startswith("## ") and "Active Now" in line:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section or not line.startswith("|"):
            continue

        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 2:
            continue
        if cells[0] in {"Domain", "--------"}:
            continue
        rows.append({"domain": cells[0], "focus": cells[1]})

    return rows


def _extract_checklist_items(text: str, section_names: set[str]) -> list[str]:
    items: list[str] = []
    active_heading = ""

    for line in text.splitlines():
        if line.startswith("## "):
            active_heading = line[3:].strip().lower()
            continue
        if not any(sn in active_heading for sn in section_names):
            continue
        match = re.match(r"^- \[ \] (.+)$", line.strip())
        if match:
            items.append(match.group(1).strip())

    return items


def _summarize_quarantine(quarantine_root: Path, vault_root: Path) -> dict:
    per_class: dict[str, int] = {}
    recent_items: list[str] = []
    files_read = [str(quarantine_root.relative_to(vault_root)).replace("\\", "/")]

    content_files: list[Path] = []
    for class_dir in sorted(p for p in quarantine_root.iterdir() if p.is_dir()):
        class_files = [
            path for path in class_dir.glob("*.md")
            if path.is_file() and _is_queue_content_file(path)
        ]
        count = len(class_files)
        per_class[class_dir.name] = count
        content_files.extend(class_files)

    for path in sorted(content_files, key=lambda item: item.stat().st_mtime, reverse=True)[:5]:
        recent_items.append(str(path.relative_to(vault_root)).replace("\\", "/"))

    return {
        "total_pending": sum(per_class.values()),
        "per_class": per_class,
        "recent_items": recent_items,
        "files_read": files_read,
    }


def _resolve_intake_surface(inputs_root: Path) -> Path:
    quarantine_root = inputs_root / "00_QUARANTINE"
    if quarantine_root.exists():
        return quarantine_root
    return inputs_root


def _is_queue_content_file(path: Path) -> bool:
    name = path.name
    if name == "README.md":
        return False
    if name.endswith("-Index.md"):
        return False
    return True


def _summarize_recent_files(
    directory: Path,
    vault_root: Path,
    exclude_names: set[str],
    limit: int,
) -> dict:
    if not directory.exists():
        raise WorkflowExecutionError(
            f"required runtime directory missing: {directory.relative_to(vault_root)}"
        )

    files = [
        path for path in directory.iterdir()
        if path.is_file() and path.name not in exclude_names
    ]
    entries = [
        str(path.relative_to(vault_root)).replace("\\", "/")
        for path in sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)[:limit]
    ]
    files_read = [str(directory.relative_to(vault_root)).replace("\\", "/")]
    return {"entries": entries, "files_read": files_read}


# ── Scorecard reader ──────────────────────────────────────────────────────────

def _read_scorecard_summaries(vault_root: Path) -> dict:
    """Read all available agent scorecard summaries. Fail-open."""
    try:
        from runtime.memory.scorecards.scorecard_updater import (
            list_scorecards,
            scorecard_summary_text,
        )
        runtime_ids = list_scorecards(vault_root)
        summaries = [scorecard_summary_text(rid, vault_root) for rid in runtime_ids]
        sc_dir = vault_root / "runtime" / "memory" / "scorecards"
        files_read = [
            str((sc_dir / f"{rid}.json").relative_to(vault_root)).replace("\\", "/")
            for rid in runtime_ids
            if (sc_dir / f"{rid}.json").exists()
        ]
        return {"summaries": summaries, "runtime_ids": runtime_ids, "files_read": files_read}
    except Exception:  # noqa: BLE001
        return {"summaries": [], "runtime_ids": [], "files_read": []}


# ── V2 Markdown renderer ───────────────────────────────────────────────────────

def _render_markdown_brief_v2(summary: dict) -> str:
    generated_at = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
    cf_date = summary["carry_forward"]["date"]
    files_read_list = "\n".join(f"  - `{p}`" for p in summary["files_read"])

    lines = [
        "---",
        "type: operator-brief",
        "workflow: operator_today",
        "version: v2",
        f"date: {summary['date']}",
        f"generated_at: {generated_at}",
        "source: aor",
        "briefing_model: four-layer",
        "---",
        "",
        f"# Operator Brief — OPEN — {summary['date']}",
        "",
        f"**Generated by:** AOR / operator_today v2",
        f"**Carry-forward from:** {cf_date}",
        "**Files read:**",
        files_read_list,
        "",
        "---",
        "",
        "## [CANONICAL] Current State",
        "",
        f"> Source: `00_HOME/Now.md` · `ROADMAP.md` · active Project-OS files",
        "",
        f"**Current Phase:** {summary['current_phase']}",
    ]

    if summary.get("roadmap_phase_line"):
        lines.append(f"**ROADMAP active line:** {summary['roadmap_phase_line']}")

    if summary["contradictions"]:
        lines.extend(["", "**⚠ CONTRADICTIONS FLAGGED:**"])
        for c in summary["contradictions"]:
            lines.append(f"- {c}")

    lines.extend(["", "**Active Domains:**"])
    for row in summary["active_focus"]:
        lines.append(f"- {row['domain']}: {row['focus']}")

    lines.extend(["", "**Project State:**"])
    for snapshot in summary["project_snapshots"]:
        lines.append(f"### {snapshot['domain']}")
        lines.append(f"  Source: `{snapshot['project_path']}`")
        if snapshot["open_loops"]:
            lines.append("  Open loops:")
            lines.extend([f"    - {item}" for item in snapshot["open_loops"]])
        if snapshot["next_actions"]:
            lines.append("  Next actions:")
            lines.extend([f"    - {item}" for item in snapshot["next_actions"]])
        if not snapshot["open_loops"] and not snapshot["next_actions"]:
            lines.append("  No checklist-style open loops or next actions found.")

    # Layer 2
    lines.extend(["", "---", ""])
    if cf_date == "none" or cf_date == "unknown":
        lines.extend([
            "## [CARRY-FORWARD: none] No Prior Close Note",
            "",
            "> No operator_close_day note found. No carry-forward context available.",
            "> This is expected on the first run of the day when no close note was written yesterday.",
        ])
    else:
        lines.extend([
            f"## [CARRY-FORWARD: {cf_date}] Open Loops from Yesterday",
            "",
            f"> Source: most recent close note — `{summary['carry_forward']['files_read'][0] if summary['carry_forward']['files_read'] else 'unknown'}`",
        ])
        if summary["carry_forward"]["items"]:
            lines.append("")
            for item in summary["carry_forward"]["items"]:
                lines.append(f"- {item}")
        else:
            lines.extend(["", "- No open loops recorded in the prior close note."])

    # Layer 3
    lines.extend(["", "---", "", "## [SOURCED] Operational Context", ""])

    lines.extend([
        "### Build Activity",
        f"> Source: `07_LOGS/Build-Logs/` (last 5 files by modification time)",
    ])
    if summary["recent_build_logs"]:
        for entry in summary["recent_build_logs"]:
            lines.append(f"- `{entry}`")
    else:
        lines.append("- No recent build logs found.")

    lines.extend(["", "### AOR Activity (last 48h)"])
    aor = summary["aor_activity"]
    if aor["runs"]:
        lines.append(
            f"> {len(aor['runs'])} run(s): {aor['success']} success · "
            f"{aor['escalated']} escalated · {aor['failed']} failed"
        )
        for run in aor["runs"][-5:]:  # Last 5 most recent
            lines.append(f"- {run['workflow_id']}: {run['status']}")
    else:
        lines.append("- No AOR activity records found in last 48h.")

    lines.extend(["", "### Agent Scorecards"])
    sc = summary.get("scorecards", {})
    sc_summaries = sc.get("summaries", [])
    if sc_summaries:
        lines.append(f"> Source: `runtime/memory/scorecards/` ({len(sc_summaries)} runtime(s))")
        lines.append("")
        for entry in sc_summaries:
            lines.append("```")
            lines.append(entry)
            lines.append("```")
    else:
        lines.append("> No scorecard data found — no AOR executions recorded yet.")

    lines.extend(["", "### Quarantine Queue", f"> Source: `03_INPUTS/`"])
    lines.append(f"- Pending captures: {summary['quarantine']['total_pending']}")
    for class_name, count in summary["quarantine"]["per_class"].items():
        lines.append(f"  - {class_name}: {count}")
    if summary["quarantine"]["recent_items"]:
        lines.append("- Recent quarantine items:")
        lines.extend([f"  - `{item}`" for item in summary["quarantine"]["recent_items"]])

    lines.extend(["", "### Recent Decisions", f"> Source: `07_LOGS/Decision-Ledger/Index.md`"])
    if summary["recent_decisions"]:
        for entry in summary["recent_decisions"]:
            lines.append(f"- {entry}")
    else:
        lines.append("- No decision ledger entries found.")

    # Layer 4
    lines.extend([
        "", "---", "",
        "## [SYNTHESIS] Today's Recommendations",
        "",
        "> **This section is AI-synthesized analysis.** It is a starting point for your judgment, not a replacement for it.",
        "> Sources used are referenced inline. Do not treat this section as canonical state.",
        "",
        "### Priority Synthesis",
    ])
    synth = summary["synthesis"]
    for action in synth["priority_actions"]:
        lines.append(f"- {action}")

    lines.extend(["", "### Open Loop Check"])
    for item in synth["open_loop_check"]:
        lines.append(f"- {item}")

    lines.extend([
        "", "---", "",
        "*Operator Brief — written to `07_LOGS/Operator-Briefs/` only — not canonical state*",
        "*All synthesis in this document is labeled [SYNTHESIS] and is AI-generated analysis.*",
        "*Act on it at your discretion. Never embed [SYNTHESIS] content in Now.md, Project-OS files, or ROADMAP.md.*",
    ])

    return "\n".join(lines)
