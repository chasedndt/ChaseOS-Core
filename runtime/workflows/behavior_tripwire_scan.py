"""
behavior_tripwire_scan.py — ChaseOS AOR Phase 9 (Detective Layer)

Governed AOR handler that runs the read-only behavior tripwire and persists its
report. ChaseOS has a strong PREVENTIVE layer (Gate, protected-file guard, role
cards); this is the scheduled DETECTIVE pass that actually exercises
`runtime.agent_control.behavior_tripwire` — which was built but never wired to
run automatically.

Alert-only by design: it never blocks, mutates runtime state, or calls a
provider. It reads signals the system already emits (gate denials, escalated
runs, side-effect events, unreviewed incident candidates) and writes a JSON +
Markdown report under 07_LOGS/Runtime-Audits/. The workflow itself completes
successfully even when a HIGH anomaly is found — the alert lives in the report.

Direct path: `python -m runtime.agent_control.behavior_tripwire --write`.
Governed path: `chaseos run behavior_tripwire_scan` (adds role-card governance,
permission ceiling, audit trail).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.agent_control.behavior_tripwire import (
    DEFAULT_WINDOW_HOURS,
    scan_behavior,
)

_AUDITS_DIR_REL = "07_LOGS/Runtime-Audits"


class WorkflowExecutionError(RuntimeError):
    """Fail-closed workflow error for bounded AOR handlers."""


def _render_markdown(report_dict: dict[str, Any]) -> str:
    counts = report_dict.get("counts", {})
    findings = report_dict.get("findings", [])
    lines = [
        "---",
        "workflow_id: behavior_tripwire_scan",
        f"scanned_at: {report_dict.get('scanned_at')}",
        f"window_hours: {report_dict.get('window_hours')}",
        f"ok: {report_dict.get('ok')}",
        f"highest_severity: {report_dict.get('highest_severity')}",
        "---",
        "",
        "# Behavior Tripwire Scan",
        "",
        f"**Result:** {'CLEAN' if report_dict.get('ok') else 'ALERT'}  ",
        f"**Highest severity:** {report_dict.get('highest_severity') or 'none'}  ",
        f"**Window:** {report_dict.get('window_hours')}h  ",
        f"**Findings:** {counts.get('findings', 0)} "
        f"(high={counts.get('high', 0)}, medium={counts.get('medium', 0)}, low={counts.get('low', 0)})",
        "",
    ]
    if findings:
        lines += ["## Findings", "", "| Severity | Signal | Count | Detail |", "|---|---|---|---|"]
        for f in findings:
            detail = str(f.get("detail", "")).replace("|", "\\|")
            lines.append(
                f"| {str(f.get('severity', '')).upper()} | {f.get('signal', '')} "
                f"| {f.get('count', 0)} | {detail} |"
            )
        lines.append("")
        for f in findings:
            samples = f.get("samples") or []
            if samples:
                lines.append(f"### {f.get('signal', '')} samples")
                lines += [f"- `{str(s).replace('`', '')}`" for s in samples]
                lines.append("")
    else:
        lines += ["No anomalies detected in the scan window.", ""]
    lines += [
        "---",
        "",
        "*Detective layer — alert-only. This report blocks nothing. "
        "Review HIGH/MEDIUM findings and mark incident candidates reviewed "
        "with `chaseos incident review`.*",
        "",
        "*Graph links: [[behavior-tripwire]] · [[Agent-Activity-Index]]*",
    ]
    return "\n".join(lines)


def run_behavior_tripwire_scan(inputs: dict[str, Any], vault_root: Path) -> dict[str, Any]:
    """
    AOR handler for behavior_tripwire_scan.

    Inputs:
        window_hours: rolling window for the scan (default: 24).

    Returns an AOR writeback dict with the report JSON + Markdown under
    07_LOGS/Runtime-Audits/. handler_status is always "complete" when the scan
    runs; anomalies are reported in the content, not as a workflow failure.
    """
    try:
        window_hours = max(1, int(inputs.get("window_hours", DEFAULT_WINDOW_HOURS)))
    except (TypeError, ValueError):
        window_hours = DEFAULT_WINDOW_HOURS

    try:
        report = scan_behavior(vault_root, window_hours=window_hours)
    except Exception as exc:  # noqa: BLE001
        raise WorkflowExecutionError(f"behavior tripwire scan failed: {exc}") from exc

    report_dict = report.to_dict()
    stamp = (
        str(report.scanned_at)
        .replace(":", "")
        .replace("-", "")
        .replace("Z", "")
        .split(".")[0]
    )
    json_path = f"{_AUDITS_DIR_REL}/tripwire-{stamp}.json"
    md_path = f"{_AUDITS_DIR_REL}/tripwire-{stamp}.md"

    return {
        "handler_status": "complete",
        "status": "complete",
        "writebacks": [
            {"path": json_path, "content": json.dumps(report_dict, indent=2)},
            {"path": md_path, "content": _render_markdown(report_dict)},
        ],
        "tripwire_ok": report.ok,
        "highest_severity": report.highest_severity,
        "findings_count": report.counts.get("findings", 0),
        "counts": report.counts,
        "window_hours": window_hours,
        "report_json_path": json_path,
        "report_md_path": md_path,
    }
