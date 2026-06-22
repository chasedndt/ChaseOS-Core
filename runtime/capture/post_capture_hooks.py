"""
post_capture_hooks.py — ChaseOS Phase 9 Connector Automation

Post-capture hook dispatch. Called by capture_content() after a successful
new capture. Hooks are origin_kind-driven and opt-in via auto_link flag.

Currently registered hooks:
    meeting-transcript  →  meeting_ingest_linker AOR workflow

Fail-open: hook errors NEVER affect the primary capture result.
The capture always succeeds or fails on its own terms; hooks are enrichment only.

Public API:
    run_post_capture_hooks(origin_kind, capture_result, vault_root, auto_link) -> dict
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


_MEETING_TRANSCRIPT_ORIGIN = "meeting-transcript"


def run_post_capture_hooks(
    origin_kind: str,
    capture_result: dict[str, Any],
    vault_root: Path,
    auto_link: bool = False,
) -> dict[str, Any]:
    """
    Run post-capture hooks based on the captured content's origin_kind.

    Parameters
    ----------
    origin_kind : str
        Content's origin_kind value from the ContentPacket (may be empty string).
    capture_result : dict
        The successful capture result dict from capture_content().
    vault_root : Path
        Vault root path.
    auto_link : bool
        If True, dispatch meeting_ingest_linker for meeting-transcript captures.
        Default False — opt-in only.

    Returns
    -------
    dict
        Hook results keyed by hook name. Empty if no hooks ran.
        Never raises — all exceptions are caught and returned as hook_error status.
    """
    hooks: dict[str, Any] = {}

    if origin_kind == _MEETING_TRANSCRIPT_ORIGIN and auto_link:
        hooks["meeting_ingest"] = _dispatch_meeting_ingest(capture_result, vault_root)

    return hooks


def _dispatch_meeting_ingest(
    capture_result: dict[str, Any],
    vault_root: Path,
) -> dict[str, Any]:
    """
    Dispatch meeting_ingest_linker AOR workflow for a captured transcript.

    Imports AOR lazily to avoid circular import (capture layer must not
    import AOR at module level).
    """
    try:
        from runtime.aor.engine import run_workflow  # lazy import

        content_path = capture_result.get("content_path", "")
        capture_id = capture_result.get("capture_id", "")

        if not content_path:
            return {"status": "skipped", "reason": "no content_path in capture result"}

        r = run_workflow(
            "meeting_ingest_linker",
            inputs={
                "transcript_path": str(content_path),
                "capture_id": str(capture_id),
            },
            vault_root=vault_root,
        )

        files = r.outputs.get("writeback", {}).get("files_written", [])
        return {
            "status": r.status,
            "stage_reached": r.stage_reached,
            "proposal_files": files,
            "escalation_reason": r.escalation_reason,
        }

    except Exception as exc:  # noqa: BLE001
        return {"status": "hook_error", "error": str(exc)[:200]}
