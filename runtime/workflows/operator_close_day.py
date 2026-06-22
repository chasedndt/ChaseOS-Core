"""
operator_close_day.py -- ChaseOS AOR Phase 9 Briefing V2

End-of-day close workflow handler.

Implements the close-day four-layer model:
  [CANONICAL]       Phase state at close — from vault files
  [CARRY-FORWARD]   Structured open loops for tomorrow's operator_today
  [RUNTIME RECORD]  Today's AOR workflow outcomes
  [SYNTHESIS]       Delta from morning + session summary + close checklist

Design authority: 06_AGENTS/Operator-Briefing-Architecture.md
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


class WorkflowExecutionError(RuntimeError):
    """Fail-closed workflow error for bounded AOR handlers."""


@dataclass
class TodayBuildActivity:
    """Build logs with today's date prefix."""
    log_files: list[str] = field(default_factory=list)
    count: int = 0


@dataclass
class LoopRecord:
    """A single open loop with its carry-forward status."""
    text: str
    status: str  # "open" | "resolved" | "deferred" | "new"


def run_operator_close_day(inputs: dict, vault_root: Path) -> dict:
    run_date = _resolve_run_date(inputs.get("date"))
    operator_open_loops: list[str] = _parse_list_input(inputs.get("open_loops", ""))
    operator_notes: str = str(inputs.get("notes") or "").strip()

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
            f"operator_close_day required context missing: {missing}"
        )

    files_read: list[str] = []

    # ── Layer 1: CANONICAL reads ────────────────────────────────────────────────
    now_text = now_path.read_text(encoding="utf-8")
    files_read.append("00_HOME/Now.md")

    current_phase = _extract_phase_line(now_text)
    next_pass_action = _extract_next_pass_action(now_text)

    today_builds = _summarize_today_builds(build_logs_dir, vault_root, run_date)
    files_read.append(str(build_logs_dir.relative_to(vault_root)).replace("\\", "/"))

    recent_decisions = _summarize_recent_files(
        decision_ledger_dir,
        vault_root,
        exclude_names={"Index.md"},
        limit=3,
    )
    files_read.append(str(decision_ledger_dir.relative_to(vault_root)).replace("\\", "/"))

    quarantine_summary = _summarize_quarantine(inputs_root, vault_root)
    files_read.append(str(inputs_root.relative_to(vault_root)).replace("\\", "/"))

    # ── Layer 2: DELTA — compare morning brief vs close loops ─────────────────
    morning_brief = {"date": "none", "open_loops": [], "files_read": []}
    if briefs_dir.exists():
        morning_brief = _read_morning_brief(briefs_dir, run_date, vault_root)
    files_read.extend(morning_brief["files_read"])

    # Compute carry-forward loops: merge morning + operator-provided
    carry_forward_loops = _compute_carry_forward_loops(
        morning_loops=morning_brief["open_loops"],
        close_loops=operator_open_loops,
    )

    # ── Layer 3: RUNTIME RECORD — today's AOR activity ─────────────────────────
    aor_today: list[dict] = []
    aor_files_read: list[str] = []
    if activity_dir.exists():
        aor_today, aor_files_read = _read_agent_activity_today(activity_dir, vault_root, run_date)
    files_read.extend(aor_files_read)

    # ── Layer 4: SYNTHESIS — session close evaluation ──────────────────────────
    session_close_checks = _evaluate_session_close_checklist(
        today_builds=today_builds,
        recent_decisions=recent_decisions,
        current_phase=current_phase,
    )

    summary = {
        "date": run_date.isoformat(),
        # Layer 1 canonical
        "current_phase": current_phase,
        "next_pass_action": next_pass_action,
        "today_builds": today_builds.log_files,
        "today_build_count": today_builds.count,
        "recent_decisions": recent_decisions,
        "quarantine_total_pending": quarantine_summary["total_pending"],
        "quarantine_per_class": quarantine_summary["per_class"],
        # Layer 2 carry-forward
        "morning_brief_date": morning_brief["date"],
        "morning_open_loops": morning_brief["open_loops"],
        "carry_forward_loops": [
            {"text": lp.text, "status": lp.status} for lp in carry_forward_loops
        ],
        "operator_open_loops": operator_open_loops,
        "operator_notes": operator_notes,
        # Layer 3 runtime record
        "aor_today": aor_today,
        # Layer 4 synthesis
        "session_close_checks": session_close_checks,
        # Metadata
        "files_read": sorted(dict.fromkeys(files_read)),
    }

    relative_output_path = (
        Path("07_LOGS") / "Operator-Briefs" / f"{run_date.isoformat()}-operator-close-day.md"
    )
    content = _render_markdown_close_note_v2(summary)

    return {
        "handler_status": "executed",
        "workflow_id": "operator_close_day",
        "date": run_date.isoformat(),
        "files_read": summary["files_read"],
        "carry_forward_loop_count": len(carry_forward_loops),
        "summary": {
            "today_build_count": today_builds.count,
            "quarantine_total_pending": quarantine_summary["total_pending"],
            "operator_open_loops_count": len(operator_open_loops),
            "carry_forward_loops_count": len(carry_forward_loops),
            "aor_runs_today": len(aor_today),
            "session_close_checks_passed": sum(
                1 for v in session_close_checks.values() if v == "ok"
            ),
        },
        "writebacks": [
            {
                "path": str(relative_output_path).replace("\\", "/"),
                "content": content,
                "content_type": "text/markdown",
            }
        ],
    }


# ── Morning brief reader ───────────────────────────────────────────────────────

def _read_morning_brief(briefs_dir: Path, run_date: date, vault_root: Path) -> dict:
    """Read today's operator_today brief (if it exists) to get morning open loops."""
    date_str = run_date.isoformat()
    brief_path = briefs_dir / f"{date_str}-operator-today.md"

    if not brief_path.exists():
        return {"date": "none", "open_loops": [], "files_read": []}

    rel_path = str(brief_path.relative_to(vault_root)).replace("\\", "/")
    text = brief_path.read_text(encoding="utf-8")

    # Extract carry-forward items from v2 format (already-carried items)
    open_loops = _extract_morning_open_loops(text)

    return {
        "date": date_str,
        "open_loops": open_loops,
        "files_read": [rel_path],
    }


def _extract_morning_open_loops(text: str) -> list[str]:
    """Extract open loops from a morning brief — from CARRY-FORWARD or SYNTHESIS sections."""
    items: list[str] = []

    # Try [CARRY-FORWARD] section in v2 morning brief
    in_cf = False
    for line in text.splitlines():
        if "## [CARRY-FORWARD:" in line:
            in_cf = True
            continue
        if in_cf and line.startswith("## "):
            break
        if not in_cf:
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and not stripped.startswith("- >") and "No carry-forward" not in stripped and "No open loops" not in stripped:
            items.append(stripped[2:].strip())

    return items


# ── AOR activity reader (today only) ──────────────────────────────────────────

def _read_agent_activity_today(
    activity_dir: Path,
    vault_root: Path,
    run_date: date,
) -> tuple[list[dict], list[str]]:
    """Read today's AOR activity records."""
    date_prefix = run_date.strftime("%Y%m%d")
    runs: list[dict] = []
    files_read: list[str] = []

    for path in sorted(activity_dir.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        if not path.name.startswith(date_prefix):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        runs.append({
            "workflow_id": data.get("workflow_id", "unknown"),
            "status": data.get("status", "unknown"),
            "timestamp": data.get("timestamp_utc", ""),
            "stage_reached": data.get("stage_reached", ""),
        })
        files_read.append(str(path.relative_to(vault_root)).replace("\\", "/"))

    return runs, files_read


# ── Carry-forward loop computation ────────────────────────────────────────────

def _compute_carry_forward_loops(
    morning_loops: list[str],
    close_loops: list[str],
) -> list[LoopRecord]:
    """Compute carry-forward loop status from morning and close-of-day inputs."""
    result: list[LoopRecord] = []

    # Items from morning that are not resolved — carry as "open"
    # (We can't detect resolution without operator input; carry all forward as open)
    for item in morning_loops:
        # Check if the operator marked this resolved in close_loops with "resolved:" prefix
        resolved_markers = [
            cl for cl in close_loops
            if cl.lower().startswith("resolved:") and item.lower() in cl.lower()
        ]
        if resolved_markers:
            result.append(LoopRecord(text=item, status="resolved"))
        else:
            result.append(LoopRecord(text=item, status="open"))

    # Operator-provided close loops not already in morning loops
    for cl in close_loops:
        # Skip "resolved:" prefixed items (they're markers, not new loops)
        if cl.lower().startswith("resolved:"):
            continue
        # Check if this is new (not from morning)
        is_new = not any(item.lower() in cl.lower() or cl.lower() in item.lower() for item in morning_loops)
        if is_new:
            result.append(LoopRecord(text=cl, status="new"))

    return result


# ── Helpers (shared with operator_today, kept minimal) ────────────────────────

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


def _parse_list_input(raw: object) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in str(raw).split(";") if item.strip()]


def _extract_phase_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and "Phase 9" in stripped:
            return stripped.lstrip("#").strip()
    for line in text.splitlines():
        stripped = line.strip()
        if "Phase 9" in stripped and stripped:
            return stripped
    raise WorkflowExecutionError(
        "operator_close_day could not locate Phase 9 status line in 00_HOME/Now.md"
    )


def _extract_next_pass_action(text: str) -> Optional[str]:
    for line in text.splitlines():
        match = re.search(r"Pass \d+ \(NEXT\):\s*(.+)$", line)
        if match:
            return match.group(1).strip()
    return None


def _summarize_today_builds(
    build_logs_dir: Path,
    vault_root: Path,
    run_date: date,
) -> TodayBuildActivity:
    date_prefix = run_date.isoformat()
    log_files = [
        str(path.relative_to(vault_root)).replace("\\", "/")
        for path in sorted(build_logs_dir.iterdir())
        if path.is_file()
        and path.name.startswith(date_prefix)
        and path.name != "Build-Logs-Index.md"
    ]
    return TodayBuildActivity(log_files=log_files, count=len(log_files))


def _summarize_recent_files(
    directory: Path,
    vault_root: Path,
    exclude_names: set[str],
    limit: int,
) -> list[str]:
    if not directory.exists():
        raise WorkflowExecutionError(
            f"required runtime directory missing: {directory.relative_to(vault_root)}"
        )
    files = [
        path for path in directory.iterdir()
        if path.is_file() and path.name not in exclude_names
    ]
    return [
        str(path.relative_to(vault_root)).replace("\\", "/")
        for path in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    ]


def _summarize_quarantine(inputs_root: Path, vault_root: Path) -> dict:
    quarantine_root = inputs_root / "00_QUARANTINE"
    if not quarantine_root.exists():
        quarantine_root = inputs_root

    per_class: dict[str, int] = {}
    for class_dir in sorted(p for p in quarantine_root.iterdir() if p.is_dir()):
        count = sum(
            1 for path in class_dir.glob("*.md")
            if path.is_file() and path.name not in {"README.md"} and not path.name.endswith("-Index.md")
        )
        per_class[class_dir.name] = count

    return {
        "total_pending": sum(per_class.values()),
        "per_class": per_class,
    }


def _evaluate_session_close_checklist(
    today_builds: TodayBuildActivity,
    recent_decisions: list[str],
    current_phase: str,
) -> dict[str, str]:
    return {
        "meaningful_output_written_to_vault": (
            "ok" if today_builds.count > 0 else "flag — no build logs found for today"
        ),
        "build_log_created": (
            "ok" if today_builds.count > 0 else "flag — no build log for today"
        ),
        "recent_decisions_present": (
            "ok" if recent_decisions else "flag — decision ledger appears empty"
        ),
        "phase_is_current": (
            "ok" if "Phase 9" in current_phase else "flag — phase status unclear"
        ),
    }


# ── V2 Markdown renderer ───────────────────────────────────────────────────────

def _render_markdown_close_note_v2(summary: dict) -> str:
    generated_at = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
    files_read_list = "\n".join(f"  - `{p}`" for p in summary["files_read"])

    lines = [
        "---",
        "type: operator-close-note",
        "workflow: operator_close_day",
        "version: v2",
        f"date: {summary['date']}",
        f"generated_at: {generated_at}",
        "source: aor",
        "briefing_model: four-layer",
        "---",
        "",
        f"# Operator Brief — CLOSE — {summary['date']}",
        "",
        f"**Generated by:** AOR / operator_close_day v2",
        f"**Morning brief from:** {summary['morning_brief_date']}",
        "**Files read:**",
        files_read_list,
        "",
        "---",
        "",
        "## [CANONICAL] Phase State at Close",
        "",
        f"> Source: `00_HOME/Now.md`",
        "",
        f"**Current Phase:** {summary['current_phase']}",
    ]

    lines.extend(["", "**Today's Build Activity:**"])
    if summary["today_builds"]:
        lines.append(f"- {summary['today_build_count']} build log(s) written today:")
        for log in summary["today_builds"]:
            lines.append(f"  - `{log}`")
    else:
        lines.append("- No build logs with today's date prefix found.")

    lines.extend(["", "**Quarantine Queue:**"])
    lines.append(f"- Pending captures: {summary['quarantine_total_pending']}")
    for class_name, count in summary["quarantine_per_class"].items():
        lines.append(f"  - {class_name}: {count}")

    lines.extend(["", "**Recent Decisions:**"])
    if summary["recent_decisions"]:
        for entry in summary["recent_decisions"]:
            lines.append(f"- `{entry}`")
    else:
        lines.append("- No decision ledger entries found.")

    # Layer 2: Carry-Forward
    lines.extend([
        "", "---", "",
        "## [CARRY-FORWARD] Open Loops for Tomorrow",
        "",
        "> This section is read by operator_today v2 as Layer 2 carry-forward.",
        "> Format: `- status:X — loop text`",
        "> Statuses: open | resolved | deferred | new",
        "",
    ])

    carry_forward_loops = summary.get("carry_forward_loops", [])
    if carry_forward_loops:
        for lp in carry_forward_loops:
            status_icon = {"open": "⬜", "resolved": "✅", "deferred": "⏸", "new": "🆕"}.get(
                lp["status"], "⬜"
            )
            lines.append(f"- status:{lp['status']} — {lp['text']} {status_icon}")
    else:
        # Operator-provided loops not matched to morning (raw capture)
        if summary["operator_open_loops"]:
            for loop in summary["operator_open_loops"]:
                lines.append(f"- status:open — {loop} ⬜")
        else:
            lines.append("- status:none — No open loops recorded for today.")

    # Machine-readable open loops section for drift_scan compatibility.
    # drift_scan regex: ^#+\s*(open\s*loops?|carry.forward|unresolved) + "- [ ] text" items.
    open_items = [
        lp for lp in carry_forward_loops if lp["status"] in ("open", "new")
    ]
    if not open_items and summary.get("operator_open_loops"):
        open_items = [{"text": t, "status": "open"} for t in summary["operator_open_loops"]]

    lines.extend(["", "## Open Loops", ""])
    if open_items:
        lines.append("> Machine-readable section for drift_scan — mirrors the carry-forward above.")
        lines.append("")
        for lp in open_items:
            lines.append(f"- [ ] {lp['text']}")
    else:
        lines.append("> No open loops.")

    # Layer 3: Runtime Record
    lines.extend([
        "", "---", "",
        "## [RUNTIME RECORD] Today's AOR Activity",
        "",
        "> Source: `07_LOGS/Agent-Activity/` — today's run records",
        "",
    ])

    aor_today = summary.get("aor_today", [])
    if aor_today:
        success = sum(1 for r in aor_today if r["status"] == "success")
        escalated = sum(1 for r in aor_today if r["status"] == "escalated")
        failed = sum(1 for r in aor_today if r["status"] not in {"success", "escalated", "dry_run_ok"})
        lines.append(
            f"**Summary:** {len(aor_today)} run(s) — {success} success · {escalated} escalated · {failed} failed"
        )
        lines.append("")
        for run in aor_today:
            ts = run.get("timestamp", "")[:16].replace("T", " ")
            lines.append(f"- {run['workflow_id']}: {run['status']} ({ts})")
    else:
        lines.append("- No AOR activity records found for today.")

    # Layer 4: Synthesis — session summary
    lines.extend([
        "", "---", "",
        "## [SYNTHESIS] Session Summary",
        "",
        "> **This section is AI-synthesized analysis.** Not canonical state.",
        "",
        "### Delta from Morning",
    ])

    morning_loops = summary.get("morning_open_loops", [])
    if morning_loops:
        lines.append(f"> Morning brief had {len(morning_loops)} carry-forward item(s).")
        resolved = [lp for lp in carry_forward_loops if lp["status"] == "resolved"]
        still_open = [lp for lp in carry_forward_loops if lp["status"] == "open"]
        new_loops = [lp for lp in carry_forward_loops if lp["status"] == "new"]
        lines.append(f"- Resolved today: {len(resolved)}")
        lines.append(f"- Still open: {len(still_open)}")
        lines.append(f"- New loops added: {len(new_loops)}")
    else:
        lines.append("- No morning carry-forward to compare against. (First run or no morning brief today.)")

    lines.extend(["", "### Session-Close Checklist"])
    for check, status in summary["session_close_checks"].items():
        icon = "✅" if status == "ok" else "⚠️"
        label = check.replace("_", " ")
        lines.append(f"- {icon} {label}: {status}")

    lines.extend(["", "### Tomorrow Focus"])
    if summary["next_pass_action"]:
        lines.append(f"- Next pass action: {summary['next_pass_action']}")
    else:
        lines.append("- Check 00_HOME/Now.md for next sprint actions.")

    if summary["operator_notes"]:
        lines.extend(["", "### Operator Notes"])
        lines.append(summary["operator_notes"])

    lines.extend([
        "", "---", "",
        "*Operator Close Note — written to `07_LOGS/Operator-Briefs/` only — not canonical state*",
        "*Carry-forward section above is structured for operator_today v2 Layer 2 consumption.*",
    ])

    return "\n".join(lines)
