"""
hermes_research_synthesis.py — Hermes Research Synthesis Workflow

Reads SIC workspace outputs for a declared workspace and produces a structured
synthesis draft captured to quarantine. Hermes Workflow Class 2.

Inputs:
  workspace_id    str    (required) SIC workspace ID to synthesize
  output_limit    int    (optional, default 3) max recent outputs to include
  synthesize      bool   (optional, default False) use LLM if API key available (opt-in only)

Outputs:
  writebacks      list   quarantine draft + agent-activity audit
  workspace_id    str    echoed
  outputs_read    int    number of workspace outputs consumed
  synthesis_used  bool   True if LLM synthesis was performed
  captured_path   str | None  quarantine path of the synthesis draft

Governance boundary:
  Synthesis drafts are quarantine captures only — never direct canonical writes.
  Promotion to 02_KNOWLEDGE/ requires explicit operator Gate endorsement.

AOR engine registration:
  _handlers["hermes_research_synthesis"] = run_hermes_research_synthesis
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


def _now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# ── Workspace output loading ──────────────────────────────────────────────────

def _load_workspace_outputs(
    vault_root: Path,
    workspace_id: str,
    limit: int,
) -> list[dict]:
    """Load up to `limit` most recent JSON outputs from a SIC workspace."""
    outputs_dir = (
        vault_root / "runtime" / "source_intelligence"
        / "workspaces" / workspace_id / "outputs"
    )
    if not outputs_dir.exists():
        return []
    files = sorted(outputs_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    results: list[dict] = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_source_file"] = str(f.relative_to(vault_root)).replace("\\", "/")
            results.append(data)
        except Exception:  # noqa: BLE001
            pass
    return results


# ── Synthesis ─────────────────────────────────────────────────────────────────

def _build_structured_summary(workspace_id: str, outputs: list[dict]) -> str:
    """Build a structured Markdown synthesis summary from workspace outputs."""
    lines = [
        f"# Research Synthesis — {workspace_id}",
        f"",
        f"**Workspace:** `{workspace_id}`",
        f"**Generated:** {_now_iso()[:19]}Z",
        f"**Outputs included:** {len(outputs)}",
        f"**Synthesis type:** structured-summary",
        f"",
        "---",
        "",
    ]
    for i, output in enumerate(outputs, 1):
        output_id = output.get("output_id") or output.get("_source_file", f"output-{i}")
        output_type = output.get("output_type", "unknown")
        content = output.get("content") or output.get("text") or ""
        if len(content) > 2000:
            content = content[:2000].rstrip() + "\n\n_[truncated at 2000 chars]_"
        lines += [
            f"## Output {i}: `{output_id}`",
            f"**Type:** {output_type}",
            "",
            content or "_No content field found in output._",
            "",
            "---",
            "",
        ]
    lines += [
        "## Governance Boundary",
        "",
        "This synthesis draft is a **quarantine capture only**.",
        "It is NOT canonical knowledge. Promotion to `02_KNOWLEDGE/` requires",
        "explicit operator review and Gate endorsement.",
        "",
    ]
    return "\n".join(lines)


def _execute_llm_synthesis(
    workspace_id: str,
    outputs: list[dict],
    vault_root: Path | None = None,
) -> Optional[str]:
    """
    Attempt LLM synthesis via the shared execution adapter (Hermes model chain).

    Fail-open: returns None if the adapter call fails or no API key is configured.
    Routes through execute_synthesis() — never calls providers directly.
    """
    # Prepare source text from outputs (capped at 6000 chars total)
    source_chunks: list[str] = []
    total = 0
    for output in outputs:
        content = str(output.get("content") or output.get("text") or "")
        if total + len(content) > 6000:
            content = content[:6000 - total]
        source_chunks.append(content)
        total += len(content)
        if total >= 6000:
            break
    source_text = "\n\n---\n\n".join(source_chunks)

    # Prompt-injection boundary: workspace outputs are UNTRUSTED captured/retrieved
    # content. Wrap them so the model treats them as data, not instructions.
    from runtime.security.prompt_guard import PROMPT_GUARD_PREAMBLE, wrap_untrusted

    prompt_user = (
        f"Summarize the following workspace outputs from the '{workspace_id}' research workspace "
        "into a concise, structured briefing. Focus on key findings, patterns, and actionable "
        "signals. Do not add information not present in the source material.\n\n"
        f"{wrap_untrusted(source_text, label='research-workspace-outputs')}"
    )

    try:
        from runtime.execution_adapters.execute import execute_synthesis  # noqa: PLC0415
        result = execute_synthesis(
            prompt_system=(
                f"{PROMPT_GUARD_PREAMBLE}\n\n"
                "You are a research synthesis assistant for ChaseOS. "
                "Produce a concise, structured briefing from the provided workspace outputs."
            ),
            prompt_user=prompt_user,
            execution_adapter="hermes",
            vault_root=vault_root or Path.cwd(),
        )
        return result.text.strip() or None
    except Exception:  # noqa: BLE001
        return None


# ── Quarantine capture ────────────────────────────────────────────────────────

def _capture_to_quarantine(
    vault_root: Path,
    workspace_id: str,
    content: str,
    synthesis_used: bool,
) -> Optional[str]:
    """
    Capture synthesis draft to quarantine via capture_content().
    Returns the relative path of the captured file, or None on failure.
    """
    try:
        from runtime.capture.capture import capture_content
        title = f"Hermes Research Synthesis — {workspace_id} — {_now_date()}"
        result = capture_content(
            content=content,
            title=title,
            input_class="digest",
            source=f"hermes-research-synthesis:{workspace_id}",
            origin_kind="synthesized",
            domain_hint="research",
            vault_root=vault_root,
        )
        if result.get("is_duplicate"):
            return None
        return result.get("capture_path") or result.get("content_path")
    except Exception:  # noqa: BLE001
        return None


# ── Audit log ────────────────────────────────────────────────────────────────

def _build_audit_content(
    workspace_id: str,
    outputs_read: int,
    synthesis_used: bool,
    captured_path: Optional[str],
    run_iso: str,
) -> str:
    return f"""---
type: agent-activity
workflow: hermes_research_synthesis
workspace_id: {workspace_id}
runtime: Hermes
date: {run_iso[:10]}
synthesis_used: {synthesis_used}
outputs_read: {outputs_read}
authority: quarantine-capture-only
---

# Hermes Research Synthesis — {workspace_id}

**Date:** {run_iso}
**Workspace:** `{workspace_id}`
**Outputs read:** {outputs_read}
**LLM synthesis used:** {synthesis_used}
**Captured to:** `{captured_path or "none (duplicate or capture failed)"}`

## Boundary Statement

This workflow read SIC workspace outputs and captured a synthesis draft to quarantine.
It did not write to canonical knowledge, project OS files, or protected files.
Promotion to 02_KNOWLEDGE/ requires explicit operator Gate endorsement.
"""


# ── Public handler ────────────────────────────────────────────────────────────

def run_hermes_research_synthesis(
    inputs: dict[str, Any],
    vault_root: Path,
) -> dict[str, Any]:
    """
    Hermes Research Synthesis workflow handler.

    Reads SIC workspace outputs and captures a structured synthesis draft to quarantine.
    """
    workspace_id = str(inputs.get("workspace_id") or "").strip()
    if not workspace_id:
        raise WorkflowExecutionError("workspace_id is required")

    output_limit = int(inputs.get("output_limit") or 3)
    do_synthesize = bool(inputs.get("synthesize", False))  # opt-in only — never default to paid API calls

    # Load workspace outputs
    outputs = _load_workspace_outputs(vault_root, workspace_id, limit=max(1, output_limit))
    if not outputs:
        raise WorkflowExecutionError(
            f"workspace '{workspace_id}' has no outputs — "
            f"expected at runtime/source_intelligence/workspaces/{workspace_id}/outputs/"
        )

    # Build synthesis content
    synthesis_used = False
    content: str = ""

    if do_synthesize:
        llm_result = _execute_llm_synthesis(workspace_id, outputs, vault_root=vault_root)
        if llm_result:
            synthesis_used = True
            content = (
                f"# Research Synthesis (LLM) — {workspace_id}\n\n"
                f"**Generated:** {_now_iso()[:19]}Z\n"
                f"**Workspace:** `{workspace_id}`\n"
                f"**Outputs included:** {len(outputs)}\n"
                f"**Synthesis type:** llm-haiku\n\n"
                f"---\n\n"
                f"{llm_result}\n\n"
                f"---\n\n"
                f"## Governance Boundary\n\n"
                f"This synthesis draft is a **quarantine capture only**. "
                f"It is NOT canonical knowledge. Promotion to `02_KNOWLEDGE/` requires "
                f"explicit operator review and Gate endorsement.\n"
            )

    if not content:
        content = _build_structured_summary(workspace_id, outputs)

    # Capture to quarantine
    captured_path = _capture_to_quarantine(vault_root, workspace_id, content, synthesis_used)

    run_iso = _now_iso()
    audit_content = _build_audit_content(
        workspace_id=workspace_id,
        outputs_read=len(outputs),
        synthesis_used=synthesis_used,
        captured_path=captured_path,
        run_iso=run_iso,
    )

    ts = _now_ts()
    audit_filename = f"{ts}__hermes__research_synthesis__{workspace_id[:16]}.md"
    audit_path = f"07_LOGS/Agent-Activity/{audit_filename}"

    return {
        "workspace_id": workspace_id,
        "outputs_read": len(outputs),
        "synthesis_used": synthesis_used,
        "captured_path": captured_path,
        "writebacks": [
            {
                "path": audit_path,
                "content": audit_content,
            }
        ],
    }
