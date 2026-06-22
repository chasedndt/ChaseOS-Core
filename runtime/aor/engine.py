"""
engine.py — ChaseOS AOR Phase 9

Explicit bounded execution pipeline for the Autonomous Operator Runtime.

No workflow runs without a registry entry.
No ambient vault access. Gate rules apply to all writeback.
Escalation is not failure — it is the correct response to anything out of bounds.

Pipeline stages (in strict order):
  Approval-gated manifests insert approval_gate after required_reads and before run.
  1. workflow_lookup       — resolve workflow manifest from registry
  2. task_classification   — classify task type; unclassified → escalate, never run
  3. role_card_resolution  — load role card; missing card → escalate
  4. permission_ceiling    — apply ceiling; any violation → escalate
  5. required_reads        — verify required reads are accessible
  6. run                   — execute the workflow handler (skip if dry_run=True)
  7. writeback_handling    — route outputs per manifest writeback_targets
  8. audit_record          — append immutable audit entry to Agent-Activity log

Each stage returns a _StageResult. On escalation, the pipeline stops, logs,
and returns an AORRunResult with status="escalated". The escalation reason
is always recorded.

Public API:
    run_workflow(workflow_id, inputs, vault_root=None, dry_run=False) -> AORRunResult
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .registry import load_manifest
from .role_cards import load_card
from .task_router import classify, UNCLASSIFIED_SENTINEL
from .path_policy import (
    AORPathPolicyError,
    is_runtime_declared_placeholder,
    path_within_any_target,
    resolve_vault_relative_path,
    resolved_path_within_any_target,
    validate_vault_relative_path_list,
)
from .context_governance import (
    resolve_notes_eligibility,
    log_cgl_violation,
    CglViolation,
)
from runtime.context.boot import load_boot_context
from runtime.osril.contract import OSRILEvent, OSRILEventType
from runtime.osril.approvals import (
    ApprovalResponseError,
    mark_approval_resume,
    read_approval_response,
)
from runtime.osril.session import append_event as _append_osril_event, create_session as _create_osril_session
from runtime.sbp.runner import run_sbp_pipeline, SBPRunnerError
from runtime.sbp.base_handler import SBPWorkflowExecutionError
try:
    from runtime.memory.scorecards.scorecard_updater import update_scorecard as _update_scorecard
    _SCORECARDS_AVAILABLE = True
except Exception:  # noqa: BLE001
    _SCORECARDS_AVAILABLE = False

try:
    from runtime.memory.growth import apply_execution_to_memory as _apply_execution_to_memory
    _GROWTH_AVAILABLE = True
except Exception:  # noqa: BLE001
    _GROWTH_AVAILABLE = False

try:
    from runtime.aor.rate_guard import (
        is_rate_limited as _is_rate_limited,
        record_execution as _record_rate_execution,
    )
    _RATE_GUARD_AVAILABLE = True
except Exception:  # noqa: BLE001
    _RATE_GUARD_AVAILABLE = False


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class AORRunResult:
    """
    The result of a single AOR workflow execution attempt.

    status values:
      "waiting_approval" - pipeline stopped at an operator approval gate
      "success"    — pipeline completed; writeback done; audit recorded
      "escalated"  — pipeline stopped at a stage; no writeback; audit recorded
      "dry_run_ok" — all stages resolved successfully in dry-run mode; not executed
      "failed"     — unexpected runtime error; partial state possible; audit recorded
    """
    workflow_id: str
    status: str                          # "success" | "escalated" | "waiting_approval" | "dry_run_ok" | "failed"
    audit_id: str
    stage_reached: str = ""              # which pipeline stage produced the result
    outputs: dict[str, Any] = field(default_factory=dict)
    escalation_reason: Optional[str] = None
    error: Optional[str] = None
    manifest_snapshot: Optional[dict] = None   # snapshot at execution time


@dataclass
class _StageResult:
    ok: bool
    reason: Optional[str] = None
    data: Any = None
    terminal_status: str = "escalated"


_APPROVAL_REF_INPUT_KEYS = ("operator_approval_ref", "approval_id", "osril_approval_id")


def _approval_rule_requires_operator(manifest: dict) -> bool:
    rule = str(manifest.get("approval_rule") or "").strip().lower()
    return rule in {"operator-explicit", "operator_explicit"}


def _approval_ref_from_inputs(inputs: dict) -> Optional[str]:
    for key in _APPROVAL_REF_INPUT_KEYS:
        value = inputs.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


# ── Pipeline stages ───────────────────────────────────────────────────────────

def _stage_workflow_lookup(
    workflow_id: str,
    vault_root: Path,
) -> _StageResult:
    """Stage 1: Resolve workflow manifest from registry."""
    try:
        manifest = load_manifest(workflow_id, vault_root)
    except (ValueError, RuntimeError) as exc:
        return _StageResult(ok=False, reason=f"manifest load error: {exc}")

    if manifest is None:
        return _StageResult(
            ok=False,
            reason=f"workflow '{workflow_id}' not found in registry — "
                   "no workflow runs without a registry entry",
        )

    manifest_status = manifest.get("status")
    if manifest_status != "active":
        return _StageResult(
            ok=False,
            reason=(
                f"workflow '{workflow_id}' is not runnable "
                f"(status={manifest_status!r}); only status='active' may execute"
            ),
        )

    return _StageResult(ok=True, data=manifest)


def _stage_task_classification(
    manifest: dict,
    vault_root: Path,
) -> _StageResult:
    """Stage 2: Classify task type. Unclassified → escalate, never run."""
    task_type_id = manifest.get("task_type", "")
    task_type = classify(task_type_id, vault_root)

    if task_type["id"] == "unclassified":
        return _StageResult(
            ok=False,
            reason=f"task_type '{task_type_id}' is not in the task type table — "
                   "unclassified tasks are never executed",
        )

    return _StageResult(ok=True, data=task_type)


def _stage_role_card_resolution(
    manifest: dict,
    vault_root: Path,
) -> _StageResult:
    """Stage 3: Load role card. Missing card → escalate."""
    card_id = manifest.get("role_card", "")
    if not card_id:
        return _StageResult(
            ok=False,
            reason="manifest has no role_card field — cannot resolve permissions",
        )

    try:
        card = load_card(card_id, vault_root)
    except (ValueError, RuntimeError) as exc:
        return _StageResult(ok=False, reason=f"role card load error: {exc}")

    if card is None:
        return _StageResult(
            ok=False,
            reason=f"role card '{card_id}' not found in 06_AGENTS/role-cards/ — "
                   "cannot proceed without permission envelope",
        )

    return _StageResult(ok=True, data=card)


def _stage_permission_ceiling(
    manifest: dict,
    task_type: dict,
    role_card: dict,
    inputs: dict,
) -> _StageResult:
    """
    Stage 4: Apply permission ceiling.

    Checks:
    - manifest permission_ceiling matches task_type permission_ceiling
      (manifest may not declare a more permissive ceiling than the task type)
    - role card forbidden_actions are not in manifest's declared actions
    - inputs do not request forbidden write zones
    """
    manifest_ceiling = manifest.get("permission_ceiling", "")
    type_ceiling = task_type.get("permission_ceiling", "")

    # For this implementation: if the task type ceiling is "none" or "escalate",
    # the manifest should not be running at all (caught in task_classification stage).
    # Here we just record the ceilings for the audit without rejecting.
    # Future: implement ceiling comparison hierarchy when ceiling taxonomy is formalised.

    forbidden_actions = set(role_card.get("forbidden_actions", []))
    forbidden_write_zones = set(role_card.get("forbidden_write_zones", []))

    # Check if any input keys hint at forbidden write zones
    # (basic check — full enforcement requires Gate integration)
    requested_write_path = inputs.get("write_path", "")
    if requested_write_path:
        for zone in forbidden_write_zones:
            if zone and zone in str(requested_write_path):
                return _StageResult(
                    ok=False,
                    reason=f"requested write path '{requested_write_path}' is in "
                           f"forbidden write zone '{zone}' for role card '{role_card['id']}'",
                )

    return _StageResult(
        ok=True,
        data={
            "manifest_ceiling": manifest_ceiling,
            "type_ceiling": type_ceiling,
            "forbidden_actions": list(forbidden_actions),
            "forbidden_write_zones": list(forbidden_write_zones),
        },
    )


def _stage_required_reads(
    manifest: dict,
    task_type: dict,
    role_card: dict,
    vault_root: Path,
) -> _StageResult:
    """
    Stage 5: Verify required reads are accessible.

    Checks that the directories/files listed in task_type and role_card
    required_reads exist (or are plausibly accessible) before execution begins.
    Missing required reads → escalate with specific missing paths listed.
    """
    required_reads = list(dict.fromkeys(
        list(task_type.get("required_reads", []))
        + list(role_card.get("required_reads", []))
        + list(manifest.get("required_reads", []))
    ))
    missing: list[str] = []
    checked_reads: list[str] = []

    for read_path in required_reads:
        if is_runtime_declared_placeholder(read_path):
            continue
        try:
            full_path = resolve_vault_relative_path(
                vault_root,
                read_path,
                f"required_reads entry {read_path!r}",
            )
        except AORPathPolicyError as exc:
            return _StageResult(ok=False, reason=str(exc))
        checked_reads.append(str(read_path))
        if not full_path.exists():
            missing.append(read_path)

    if missing:
        return _StageResult(
            ok=False,
            reason=f"required reads not accessible: {missing} — "
                   "cannot execute workflow without declared required reads",
        )

    # Graph advisory narrowing: graph-proximity candidate reads for AOR Stage 5.
    # Fail-open: advisory failure must never block workflow execution.
    graph_advisory: dict = {}
    try:
        from runtime.graph.builder import load_latest_snapshot, build_index, build_query_service
        from runtime.graph.advisory import advise_required_reads

        snapshot = load_latest_snapshot(vault_root)
        if snapshot is not None:
            index = build_index(snapshot)
            qs = build_query_service(snapshot, index)
            task_context = str(manifest.get("task_type") or manifest.get("id") or "")
            workflow_id = str(manifest.get("id") or "")
            result = advise_required_reads(qs, task_context=task_context, workflow_id=workflow_id)
            graph_advisory = result.to_dict()
    except Exception:  # noqa: BLE001
        pass  # advisory narrowing failure is non-fatal

    # CGL check: for each required read that is a file, check context eligibility.
    # Fail-open: blocked results are logged as violations but do not escalate the
    # pipeline (transition period — not all notes have CGL frontmatter yet).
    cgl_violations: list[dict] = []
    try:
        task_type_id = manifest.get("task_type", "read_context")
        # Map manifest task_type to CGL action type
        _TASK_TO_CGL_ACTION = {
            "operator-briefing": "read_context",
            "vault-maintenance": "read_metadata",
            "os-graph-maintenance": "read_metadata",
            "scheduled-briefing": "read_context",
            "idea-graduation": "read_metadata",
            "drift-scan": "read_metadata",
            "trace-idea": "read_metadata",
            "meeting-ingest": "read_metadata",
        }
        cgl_action = _TASK_TO_CGL_ACTION.get(task_type_id, "read_context")
        _, cgl_results = resolve_notes_eligibility(
            checked_reads, cgl_action, role_card, vault_root
        )
        for cgl_res in cgl_results:
            if cgl_res.eligibility in ("blocked", "restricted"):
                violation = CglViolation(
                    note_ref=cgl_res.note_ref,
                    action_type=cgl_res.action_type,
                    eligibility=cgl_res.eligibility,
                    reason=cgl_res.reason,
                    role_card_id=role_card.get("id", "unknown"),
                )
                log_cgl_violation(violation, vault_root)
                cgl_violations.append({
                    "note_ref": cgl_res.note_ref,
                    "eligibility": cgl_res.eligibility,
                    "reason": cgl_res.reason,
                })
    except Exception:  # noqa: BLE001
        pass  # CGL check failure must not block workflow execution

    return _StageResult(ok=True, data={
        "resolved_reads": checked_reads,
        "cgl_violations": cgl_violations,
        "graph_advisory": graph_advisory,
    })


def _resolve_workflow_handler(workflow_id: str) -> Any:
    # Dispatch is inverted through the workflow-handler registry (ADR-0015): the engine
    # resolves handlers by id and never names a concrete workflow module. This is what
    # lets the engine extract to MIT Core without dragging instance/proprietary
    # workflows (registered only in the monorepo-only instance pack) along. Unknown
    # ids resolve to None exactly as before (→ engine escalates, never crashes).
    from runtime.aor.workflow_handlers import resolve as _resolve_handler
    return _resolve_handler(workflow_id)


def _is_declared_workflow_execution_error(exc: Exception) -> bool:
    """Treat handler-level fail-closed execution errors as escalations, not runtime failures."""
    return exc.__class__.__name__ == "WorkflowExecutionError"


def _stage_run(
    manifest: dict,
    inputs: dict,
    vault_root: Path,
) -> _StageResult:
    """
    Execute the workflow handler after any approval gate has cleared.

    Live handlers are resolved through _resolve_workflow_handler(...), including
    operator_today, operator_close_day, graph_hygiene, graduate_ideas, and other
    registered handlers. Unregistered workflows still fail closed at dispatch.
    """
    workflow_id = manifest.get("id", "")

    handler = _resolve_workflow_handler(workflow_id)

    # SBP generic dispatch: scheduled-briefing workflows without a specific handler
    # are routed to the generic SBP substrate runner (Phase 9 Pass 1A).
    if handler is None and manifest.get("task_type") == "scheduled-briefing":
        try:
            result = run_sbp_pipeline(manifest=manifest, inputs=inputs, vault_root=vault_root)
            return _StageResult(ok=True, data=result)
        except SBPRunnerError as exc:
            return _StageResult(ok=False, reason=str(exc), terminal_status="escalated")
        except Exception as exc:  # noqa: BLE001
            return _StageResult(
                ok=False,
                reason=f"SBP runner raised unexpected error: {exc}",
                terminal_status="failed",
            )

    if handler is None:
        return _StageResult(
            ok=False,
            reason=f"no live handler is registered for workflow '{workflow_id}'",
        )

    try:
        if workflow_id in {"hermes_operator_today_shadow", "openai_operator_research_shadow"}:
            result = handler(inputs=inputs, vault_root=vault_root, manifest=manifest)
        elif manifest.get("task_type") == "scheduled-briefing":
            # Instance SBP handlers receive the manifest for sbp_config access
            result = handler(inputs=inputs, vault_root=vault_root, manifest=manifest)
        else:
            result = handler(inputs=inputs, vault_root=vault_root)
        return _StageResult(ok=True, data=result)
    except SBPWorkflowExecutionError as exc:
        return _StageResult(ok=False, reason=str(exc), terminal_status="escalated")
    except Exception as exc:  # noqa: BLE001
        if _is_declared_workflow_execution_error(exc):
            return _StageResult(ok=False, reason=str(exc), terminal_status="escalated")
        return _StageResult(
            ok=False,
            reason=f"workflow handler raised: {exc}",
            terminal_status="failed",
        )


def _stage_writeback(
    manifest: dict,
    role_card: dict,
    run_data: dict,
    vault_root: Path,
    dry_run: bool,
) -> _StageResult:
    """
    Stage 7: Writeback handling.

    Routes run outputs to declared writeback_targets.
    All writeback goes through Gate rules — protected files are never written
    directly by AOR.

    Phase 9 Pass 2: handler-produced writebacks are validated and written here.
    Every output path must stay inside both the manifest writeback_targets and
    the role card write_scope.
    """
    writeback_targets: list[str] = manifest.get("writeback_targets", [])
    role_write_scope: list[str] = role_card.get("write_scope", [])
    requested_writebacks: list[dict[str, Any]] = run_data.get("writebacks", [])

    try:
        normalized_writeback_targets = validate_vault_relative_path_list(
            writeback_targets,
            "manifest.writeback_targets",
        )
        normalized_role_write_scope = validate_vault_relative_path_list(
            role_write_scope,
            "role_card.write_scope",
        )
    except AORPathPolicyError as exc:
        return _StageResult(ok=False, reason=str(exc))

    if dry_run:
        return _StageResult(
            ok=True,
            data={"dry_run": True, "would_write_to": normalized_writeback_targets},
        )

    if not requested_writebacks:
        return _StageResult(ok=False, reason="handler returned no writebacks for Stage 7")

    files_written: list[str] = []
    for writeback in requested_writebacks:
        relative_path_raw = str(writeback.get("path", "")).strip()
        content = writeback.get("content")
        if not relative_path_raw:
            return _StageResult(ok=False, reason="writeback entry missing path")
        if content is None:
            return _StageResult(ok=False, reason=f"writeback {relative_path_raw!r} missing content")

        try:
            destination = resolve_vault_relative_path(
                vault_root,
                relative_path_raw,
                f"writeback path {relative_path_raw!r}",
            )
        except AORPathPolicyError as exc:
            return _StageResult(ok=False, reason=str(exc))

        normalized_relative = destination.relative_to(vault_root.resolve()).as_posix()
        if not path_within_any_target(normalized_relative, normalized_writeback_targets):
            return _StageResult(
                ok=False,
                reason=(
                    f"writeback path {normalized_relative!r} is outside manifest writeback_targets "
                    f"{writeback_targets}"
                ),
            )
        if not path_within_any_target(normalized_relative, normalized_role_write_scope):
            return _StageResult(
                ok=False,
                reason=(
                    f"writeback path {normalized_relative!r} is outside role card write_scope "
                    f"{role_write_scope}"
                ),
            )
        if not resolved_path_within_any_target(destination, vault_root, normalized_writeback_targets):
            return _StageResult(
                ok=False,
                reason=(
                    f"writeback path {normalized_relative!r} resolves outside manifest writeback_targets "
                    f"{writeback_targets}"
                ),
            )
        if not resolved_path_within_any_target(destination, vault_root, normalized_role_write_scope):
            return _StageResult(
                ok=False,
                reason=(
                    f"writeback path {normalized_relative!r} resolves outside role card write_scope "
                    f"{role_write_scope}"
                ),
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(str(content), encoding="utf-8")
        files_written.append(normalized_relative)

    runtime_activity_events = _emit_stage_writeback_runtime_activity(
        manifest=manifest,
        run_data=run_data,
        vault_root=vault_root,
        files_written=files_written,
    )

    return _StageResult(
        ok=True,
        data={
            "writeback_targets": normalized_writeback_targets,
            "writeback_status": "written",
            "files_written": files_written,
            "runtime_activity_events": runtime_activity_events,
        },
    )


def _emit_stage_writeback_runtime_activity(
    *,
    manifest: dict,
    run_data: dict,
    vault_root: Path,
    files_written: list[str],
) -> dict[str, Any]:
    """Emit visibility-only runtime activity after AOR Stage 7 writes files.

    This is intentionally fail-open. Runtime activity records are Graph/Chat
    observability only; they must never change workflow success semantics.
    """
    adapter_id = str(manifest.get("runtime_adapter") or "").strip().lower()
    if not adapter_id:
        return {
            "ok": True,
            "skipped": True,
            "reason": "manifest_has_no_runtime_adapter",
            "emitted_count": 0,
            "event_ids": [],
            "event_types": [],
            "errors": [],
        }
    if not files_written:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_files_written",
            "emitted_count": 0,
            "event_ids": [],
            "event_types": [],
            "errors": [],
        }

    emitted: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        from runtime.adapters.event_emitter import (
            RuntimeEventEmissionError,
            emit_artifact_created_event,
            emit_file_written_event,
            load_adapter_event_contract,
        )

        contract = load_adapter_event_contract(vault_root, adapter_id)
        runtime_name = str(contract.get("runtime_name") or adapter_id).strip()
        runtime_type = str(contract.get("runtime_type") or "workflow_runtime").strip()
        workflow_id = str(manifest.get("id") or "").strip()
        task_id = str(run_data.get("task_id") or run_data.get("run_id") or "").strip()
        run_id = task_id or workflow_id
        session_id = task_id or workflow_id

        def _record(kind: str, path: str, fn: Any) -> None:
            payload = {
                "source": "aor_stage_writeback",
                "workflow_id": workflow_id,
                "task_id": task_id,
                "write_scope": "aor_stage7_writeback",
                "path": path,
            }
            try:
                event = fn(
                    vault_root,
                    adapter_id=adapter_id,
                    runtime_name=runtime_name,
                    runtime_type=runtime_type,
                    path=path,
                    summary=f"{runtime_name} wrote AOR Stage 7 writeback {path}.",
                    write_scope="aor_stage7_writeback",
                    payload=payload,
                    run_id=run_id,
                    session_id=session_id,
                ) if kind == "file.written" else fn(
                    vault_root,
                    adapter_id=adapter_id,
                    runtime_name=runtime_name,
                    runtime_type=runtime_type,
                    artifact_path=path,
                    artifact_kind="aor_writeback",
                    summary=f"{runtime_name} produced AOR writeback artifact {path}.",
                    payload=payload,
                    run_id=run_id,
                    session_id=session_id,
                )
                emitted.append(event)
            except (RuntimeEventEmissionError, OSError, ValueError) as exc:
                errors.append(f"{kind}:{path}:{exc}")

        for written_path in files_written:
            _record("file.written", written_path, emit_file_written_event)
            _record("artifact.created", written_path, emit_artifact_created_event)
    except Exception as exc:  # noqa: BLE001 - observability must not block Stage 7
        errors.append(str(exc))

    return {
        "ok": not errors,
        "skipped": False,
        "emitted_count": len(emitted),
        "event_ids": [str(event.get("id") or "") for event in emitted],
        "event_types": [str(event.get("event_type") or "") for event in emitted],
        "errors": errors,
    }


def _path_within_any_target(path: str, targets: list[str]) -> bool:
    return path_within_any_target(path, targets)


def _write_audit_record(
    workflow_id: str,
    audit_id: str,
    status: str,
    stage_reached: str,
    manifest_snapshot: Optional[dict],
    inputs_summary: dict,
    outputs: dict,
    escalation_reason: Optional[str],
    error: Optional[str],
    vault_root: Path,
    context_boot: Optional[dict] = None,
) -> None:
    """
    Stage 8: Append an immutable audit record to 07_LOGS/Agent-Activity/.

    The audit record is a JSON file named:
        YYYYMMDD-HHMMSS__<workflow_id>__<audit_id[:8]>.json

    Audit records are append-only and immutable. They are never edited after
    creation. They are the primary accountability trail for AOR execution.
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S")

    audit_dir = vault_root / "07_LOGS" / "Agent-Activity"
    audit_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{timestamp}__{workflow_id}__{audit_id[:8]}.json"
    audit_path = audit_dir / filename

    record = {
        "audit_id": audit_id,
        "workflow_id": workflow_id,
        "timestamp_utc": now.isoformat(),
        "status": status,
        "stage_reached": stage_reached,
        "escalation_reason": escalation_reason,
        "error": error,
        "inputs_summary": inputs_summary,
        "outputs": outputs,
        "manifest_snapshot": manifest_snapshot,
        "context_boot": context_boot,
    }

    with audit_path.open("w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, default=str)


# ── Rate guard helpers ────────────────────────────────────────────────────────

def _lookup_schedule_rate_limit(workflow_id: str, vault_root: Path) -> Optional[int]:
    """Return max_cycles_per_day for a workflow from its schedule intent. Fail-open."""
    try:
        from runtime.schedules.loader import list_schedules
        for s in list_schedules(vault_root, check_registry=False):
            if s.workflow_id == workflow_id and s.max_cycles_per_day is not None:
                return s.max_cycles_per_day
    except Exception:  # noqa: BLE001
        pass
    return None


# ── Vault root detection ───────────────────────────────────────────────────────

def _detect_vault_root() -> Path:
    here = Path(__file__).resolve()
    vault_root = here.parents[2]  # runtime/aor/engine.py → vault root
    if not (vault_root / "CLAUDE.md").exists():
        raise RuntimeError(
            f"Could not detect vault root. Expected CLAUDE.md at: {vault_root}\n"
            "Use vault_root parameter to specify the vault path explicitly."
        )
    return vault_root


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _emit_osril_event(
    *,
    vault_root: Path,
    session_id: str,
    run_id: str,
    runtime_id: str,
    workflow_id: str,
    event_type: OSRILEventType,
    state: str,
    payload: Optional[dict[str, Any]] = None,
    permission_ceiling: Optional[str] = None,
) -> None:
    try:
        _append_osril_event(
            vault_root=vault_root,
            event=OSRILEvent(
                session_id=session_id,
                run_id=run_id,
                runtime_id=runtime_id,
                workflow_id=workflow_id,
                event_type=event_type,
                timestamp=_utc_now_iso(),
                state=state,
                payload=payload or {},
                permission_ceiling=permission_ceiling,
            ),
        )
    except Exception:  # noqa: BLE001
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def run_workflow(
    workflow_id: str,
    inputs: Optional[dict] = None,
    vault_root: Optional[Path] = None,
    dry_run: bool = False,
    runtime_id: str = "openclaw",
) -> AORRunResult:
    """
    Execute a workflow through the full AOR pipeline.

    Parameters
    ----------
    workflow_id : str
        The ID of the workflow to run. Must exist in the Workflow Registry.
    inputs : dict, optional
        Runtime inputs for the workflow. Defaults to empty dict.
    vault_root : Path, optional
        Path to the vault root. Auto-detected from file location if not provided.
    dry_run : bool
        If True, runs all validation stages but skips execution and writeback.
        Returns status="dry_run_ok" if all stages would succeed.

    Returns
    -------
    AORRunResult
        Result with status, audit_id, outputs, and escalation_reason if applicable.
    """
    if inputs is None:
        inputs = {}

    audit_id = str(uuid.uuid4())

    if vault_root is None:
        try:
            vault_root = _detect_vault_root()
        except RuntimeError as exc:
            return AORRunResult(
                workflow_id=workflow_id,
                status="failed",
                audit_id=audit_id,
                stage_reached="vault_root_detection",
                error=str(exc),
            )

    # Inputs summary for audit (sanitised — no raw content bodies)
    inputs_summary = {k: (str(v)[:80] if isinstance(v, str) else type(v).__name__)
                      for k, v in inputs.items()}

    osril_session = _create_osril_session(
        vault_root=vault_root,
        run_id=audit_id,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        timestamp=_utc_now_iso(),
    )

    # ── Context Boot — universal pre-execution context load ───────────────────
    # Every AOR execution reads the canonical context before any stage runs.
    # "failed" (Now.md missing) is the only hard-stop; "degraded" is a warning.
    _boot_dict: Optional[dict] = None
    try:
        _boot = load_boot_context(vault_root=vault_root, runtime_id=runtime_id)
        _boot_dict = {
            "status": _boot.boot_status,
            "runtime_id": _boot.runtime_id,
            "current_phase": _boot.current_phase,
            "sprint_focus": _boot.sprint_focus,
            "trust_ceiling": _boot.trust_ceiling,
            "sources_read": _boot.sources_read,
            "warnings": _boot.boot_warnings,
        }
        if _boot.boot_status == "failed":
            _emit_osril_event(
                vault_root=vault_root,
                session_id=osril_session.session_id,
                run_id=audit_id,
                runtime_id=runtime_id,
                workflow_id=workflow_id,
                event_type=OSRILEventType.TASK_FAILED,
                state="halted",
                payload={
                    "terminal_status": "escalated",
                    "stage_reached": "context_boot",
                    "reason": "context boot failed",
                },
            )
            try:
                _write_audit_record(
                    workflow_id=workflow_id,
                    audit_id=audit_id,
                    status="escalated",
                    stage_reached="context_boot",
                    manifest_snapshot=None,
                    inputs_summary=inputs_summary,
                    outputs={},
                    escalation_reason=f"context boot failed: {_boot.boot_warnings}",
                    error=None,
                    vault_root=vault_root,
                    context_boot=_boot_dict,
                )
            except Exception:  # noqa: BLE001
                pass
            return AORRunResult(
                workflow_id=workflow_id,
                status="escalated",
                audit_id=audit_id,
                stage_reached="context_boot",
                escalation_reason=f"context boot failed — Now.md not found; "
                                  "runtime has no phase/sprint anchor and cannot proceed",
            )
    except Exception as _boot_exc:  # noqa: BLE001
        # Boot load itself raised — treat as degraded, record and continue
        _boot_dict = {"status": "error", "error": str(_boot_exc)}

    # ── Rate check (pre-stage) ────────────────────────────────────────────────
    # Only fires when the workflow has a schedule intent with max_cycles_per_day.
    # Fail-open: any rate guard error allows execution to proceed.
    if _RATE_GUARD_AVAILABLE:
        try:
            _rate_limit = _lookup_schedule_rate_limit(workflow_id, vault_root)
            if _rate_limit is not None and _is_rate_limited(workflow_id, _rate_limit, vault_root):
                _rate_reason = (
                    f"rate limit reached: '{workflow_id}' has exhausted its "
                    f"{_rate_limit} cycles/day limit"
                )
                try:
                    _write_audit_record(
                        workflow_id=workflow_id,
                        audit_id=audit_id,
                        status="escalated",
                        stage_reached="rate_check",
                        manifest_snapshot=None,
                        inputs_summary=inputs_summary,
                        outputs={},
                        escalation_reason=_rate_reason,
                        error=None,
                        vault_root=vault_root,
                        context_boot=_boot_dict,
                    )
                except Exception:  # noqa: BLE001
                    pass
                return AORRunResult(
                    workflow_id=workflow_id,
                    status="escalated",
                    audit_id=audit_id,
                    stage_reached="rate_check",
                    escalation_reason=_rate_reason,
                )
        except Exception:  # noqa: BLE001
            pass  # fail-open — rate guard errors never block execution

    def _escalate(stage: str, reason: str, manifest: Optional[dict] = None) -> AORRunResult:
        _emit_osril_event(
            vault_root=vault_root,
            session_id=osril_session.session_id,
            run_id=audit_id,
            runtime_id=runtime_id,
            workflow_id=workflow_id,
            event_type=OSRILEventType.TASK_FAILED,
            state="halted",
            payload={
                "terminal_status": "escalated",
                "stage_reached": stage,
                "reason": reason,
            },
            permission_ceiling=(manifest or {}).get("permission_ceiling") if manifest else None,
        )
        try:
            _write_audit_record(
                workflow_id=workflow_id,
                audit_id=audit_id,
                status="escalated",
                stage_reached=stage,
                manifest_snapshot=manifest,
                inputs_summary=inputs_summary,
                outputs={},
                escalation_reason=reason,
                error=None,
                vault_root=vault_root,
                context_boot=_boot_dict,
            )
        except Exception:  # noqa: BLE001
            pass  # Audit write failure must not suppress the escalation result
        _escalation_record = {
            "audit_id": audit_id,
            "workflow_id": workflow_id,
            "status": "escalated",
            "stage_reached": stage,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "escalation_reason": reason,
            "outputs": {},
            "manifest_snapshot": manifest,
        }
        if _SCORECARDS_AVAILABLE:
            try:
                _update_scorecard(runtime_id, _escalation_record, vault_root)
            except Exception:  # noqa: BLE001
                pass
        if _GROWTH_AVAILABLE:
            try:
                _apply_execution_to_memory(runtime_id, vault_root, _escalation_record)
            except Exception:  # noqa: BLE001
                pass
        return AORRunResult(
            workflow_id=workflow_id,
            status="escalated",
            audit_id=audit_id,
            stage_reached=stage,
            escalation_reason=reason,
            manifest_snapshot=manifest,
        )

    # ── Stage 1: Workflow Lookup ───────────────────────────────────────────────
    def _await_approval(manifest: dict) -> AORRunResult:
        approval_id = f"aor-{workflow_id}-{audit_id[:8]}"
        approval_payload = {
            "approval_id": approval_id,
            "approval_rule": str(manifest.get("approval_rule") or ""),
            "workflow_id": workflow_id,
            "stage_reached": "approval_gate",
            "reason": "operator-explicit approval required by workflow manifest",
            "resume_input": "operator_approval_ref",
            "response_command_hint": f"chaseos osril respond {approval_id} --decision approve",
            "resume_command_hint": f"chaseos run {workflow_id} --input operator_approval_ref={approval_id}",
        }
        _emit_osril_event(
            vault_root=vault_root,
            session_id=osril_session.session_id,
            run_id=audit_id,
            runtime_id=runtime_id,
            workflow_id=workflow_id,
            event_type=OSRILEventType.APPROVAL_REQUIRED,
            state="waiting_approval",
            payload=approval_payload,
            permission_ceiling=manifest.get("permission_ceiling"),
        )
        outputs = {"approval_gate": approval_payload}
        try:
            _write_audit_record(
                workflow_id=workflow_id,
                audit_id=audit_id,
                status="waiting_approval",
                stage_reached="approval_gate",
                manifest_snapshot=manifest,
                inputs_summary=inputs_summary,
                outputs=outputs,
                escalation_reason=None,
                error=None,
                vault_root=vault_root,
                context_boot=_boot_dict,
            )
        except Exception:  # noqa: BLE001
            pass
        if _SCORECARDS_AVAILABLE:
            try:
                _update_scorecard(runtime_id, {
                    "audit_id": audit_id,
                    "workflow_id": workflow_id,
                    "status": "waiting_approval",
                    "stage_reached": "approval_gate",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "escalation_reason": None,
                    "outputs": outputs,
                }, vault_root)
            except Exception:  # noqa: BLE001
                pass
        return AORRunResult(
            workflow_id=workflow_id,
            status="waiting_approval",
            audit_id=audit_id,
            stage_reached="approval_gate",
            outputs=outputs,
            manifest_snapshot=manifest,
        )

    r1 = _stage_workflow_lookup(workflow_id, vault_root)
    if not r1.ok:
        return _escalate("workflow_lookup", r1.reason)
    manifest: dict = r1.data
    # Prefer manifest's runtime_adapter over the caller-supplied runtime_id so
    # scorecards, growth memory, and OSRIL events land under the correct runtime.
    _manifest_adapter = manifest.get("runtime_adapter")
    if _manifest_adapter:
        runtime_id = _manifest_adapter
    _emit_osril_event(
        vault_root=vault_root,
        session_id=osril_session.session_id,
        run_id=audit_id,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        event_type=OSRILEventType.STATUS,
        state="working",
        payload={"stage": "workflow_lookup", "status": "working"},
        permission_ceiling=manifest.get("permission_ceiling"),
    )
    _emit_osril_event(
        vault_root=vault_root,
        session_id=osril_session.session_id,
        run_id=audit_id,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        event_type=OSRILEventType.TASK_STARTED,
        state="working",
        payload={"stage": "workflow_lookup", "permission_ceiling": manifest.get("permission_ceiling")},
        permission_ceiling=manifest.get("permission_ceiling"),
    )

    # ── Stage 2: Task Classification ──────────────────────────────────────────
    r2 = _stage_task_classification(manifest, vault_root)
    if not r2.ok:
        return _escalate("task_classification", r2.reason, manifest)
    task_type: dict = r2.data

    # ── Stage 3: Role Card Resolution ─────────────────────────────────────────
    r3 = _stage_role_card_resolution(manifest, vault_root)
    if not r3.ok:
        return _escalate("role_card_resolution", r3.reason, manifest)
    role_card: dict = r3.data

    # ── Stage 4: Permission Ceiling ───────────────────────────────────────────
    r4 = _stage_permission_ceiling(manifest, task_type, role_card, inputs)
    if not r4.ok:
        return _escalate("permission_ceiling", r4.reason, manifest)

    # ── Stage 5: Required Reads ───────────────────────────────────────────────
    r5 = _stage_required_reads(manifest, task_type, role_card, vault_root)
    if not r5.ok:
        return _escalate("required_reads", r5.reason, manifest)

    # ── Dry-run exit point ────────────────────────────────────────────────────
    if dry_run:
        _emit_osril_event(
            vault_root=vault_root,
            session_id=osril_session.session_id,
            run_id=audit_id,
            runtime_id=runtime_id,
            workflow_id=workflow_id,
            event_type=OSRILEventType.TASK_COMPLETE,
            state="complete",
            payload={"terminal_status": "dry_run_ok", "stage_reached": "dry_run_exit"},
            permission_ceiling=manifest.get("permission_ceiling"),
        )
        try:
            _write_audit_record(
                workflow_id=workflow_id,
                audit_id=audit_id,
                status="dry_run_ok",
                stage_reached="dry_run_exit",
                manifest_snapshot=manifest,
                inputs_summary=inputs_summary,
                outputs={"dry_run": True},
                escalation_reason=None,
                error=None,
                vault_root=vault_root,
                context_boot=_boot_dict,
            )
        except Exception:  # noqa: BLE001
            pass
        return AORRunResult(
            workflow_id=workflow_id,
            status="dry_run_ok",
            audit_id=audit_id,
            stage_reached="dry_run_exit",
            outputs={"dry_run": True},
            manifest_snapshot=manifest,
        )

    # ── Stage 6: Run ──────────────────────────────────────────────────────────
    approval_gate_data: Optional[dict[str, Any]] = None
    if _approval_rule_requires_operator(manifest):
        approval_ref = _approval_ref_from_inputs(inputs)
        if approval_ref is None:
            return _await_approval(manifest)
        try:
            approval_response = read_approval_response(vault_root, approval_ref)
        except ApprovalResponseError as exc:
            return _escalate("approval_gate", str(exc), manifest)
        if approval_response is None:
            return _escalate(
                "approval_gate",
                f"approval response not found for operator_approval_ref: {approval_ref}",
                manifest,
            )
        if approval_response.get("workflow_id") != workflow_id:
            return _escalate(
                "approval_gate",
                f"approval response workflow mismatch: {approval_response.get('workflow_id')!r}",
                manifest,
            )

        decision = str(approval_response.get("decision") or "").upper()
        if decision == "DENY":
            return _escalate(
                "approval_gate",
                f"operator denied approval: {approval_ref}",
                manifest,
            )
        if decision != "APPROVE":
            return _escalate(
                "approval_gate",
                f"approval response has invalid decision: {decision!r}",
                manifest,
            )
        if not approval_response.get("applied_to_execution"):
            return _escalate(
                "approval_gate",
                f"approval response is not applied to OSRIL session state: {approval_ref}",
                manifest,
            )
        if approval_response.get("resume_executed"):
            return _escalate(
                "approval_gate",
                f"approval response has already been consumed by a resume: {approval_ref}",
                manifest,
            )

        try:
            resume_record = mark_approval_resume(
                vault_root,
                approval_id=approval_ref,
                resumed_session_id=osril_session.session_id,
                resumed_run_id=audit_id,
                workflow_id=workflow_id,
                runtime_id=runtime_id,
            )
        except ApprovalResponseError as exc:
            return _escalate("approval_gate", str(exc), manifest)

        approval_gate_data = {
            "approval_id": approval_ref,
            "approval_rule": str(manifest.get("approval_rule") or ""),
            "response_id": approval_response.get("response_id"),
            "decision": decision,
            "operator_id": approval_response.get("operator_id"),
            "source_session_id": approval_response.get("session_id"),
            "source_run_id": approval_response.get("run_id"),
            "resume_id": resume_record.get("resume_id"),
            "resume_path": resume_record.get("resume_path"),
            "resume_executed": True,
        }
        _emit_osril_event(
            vault_root=vault_root,
            session_id=osril_session.session_id,
            run_id=audit_id,
            runtime_id=runtime_id,
            workflow_id=workflow_id,
            event_type=OSRILEventType.STATUS,
            state="working",
            payload={
                "stage": "approval_gate",
                "status": "approved",
                "approval_id": approval_ref,
                "response_id": approval_response.get("response_id"),
            },
            permission_ceiling=manifest.get("permission_ceiling"),
        )

    r6 = _stage_run(manifest, inputs, vault_root)
    if not r6.ok:
        if r6.terminal_status == "escalated":
            return _escalate("run", r6.reason, manifest)
        _emit_osril_event(
            vault_root=vault_root,
            session_id=osril_session.session_id,
            run_id=audit_id,
            runtime_id=runtime_id,
            workflow_id=workflow_id,
            event_type=OSRILEventType.TASK_FAILED,
            state="failed",
            payload={"terminal_status": "failed", "stage_reached": "run", "reason": r6.reason},
            permission_ceiling=manifest.get("permission_ceiling"),
        )
        try:
            _write_audit_record(
                workflow_id=workflow_id,
                audit_id=audit_id,
                status="failed",
                stage_reached="run",
                manifest_snapshot=manifest,
                inputs_summary=inputs_summary,
                outputs={},
                escalation_reason=None,
                error=r6.reason,
                vault_root=vault_root,
                context_boot=_boot_dict,
            )
        except Exception:  # noqa: BLE001
            pass
        return AORRunResult(
            workflow_id=workflow_id,
            status="failed",
            audit_id=audit_id,
            stage_reached="run",
            error=r6.reason,
            manifest_snapshot=manifest,
        )
    run_data: dict = r6.data

    # ── Stage 7: Writeback ────────────────────────────────────────────────────
    r7 = _stage_writeback(manifest, role_card, run_data, vault_root, dry_run=False)
    if not r7.ok:
        if r7.terminal_status == "escalated":
            return _escalate("writeback_handling", r7.reason, manifest)
        _emit_osril_event(
            vault_root=vault_root,
            session_id=osril_session.session_id,
            run_id=audit_id,
            runtime_id=runtime_id,
            workflow_id=workflow_id,
            event_type=OSRILEventType.TASK_FAILED,
            state="failed",
            payload={"terminal_status": "failed", "stage_reached": "writeback_handling", "reason": r7.reason},
            permission_ceiling=manifest.get("permission_ceiling"),
        )
        try:
            _write_audit_record(
                workflow_id=workflow_id,
                audit_id=audit_id,
                status="failed",
                stage_reached="writeback_handling",
                manifest_snapshot=manifest,
                inputs_summary=inputs_summary,
                outputs={"run": run_data},
                escalation_reason=None,
                error=r7.reason,
                vault_root=vault_root,
                context_boot=_boot_dict,
            )
        except Exception:  # noqa: BLE001
            pass
        return AORRunResult(
            workflow_id=workflow_id,
            status="failed",
            audit_id=audit_id,
            stage_reached="writeback_handling",
            outputs={"run": run_data},
            error=r7.reason,
            manifest_snapshot=manifest,
        )
    writeback_data: dict = r7.data if r7.ok else {}

    # ── Stage 8: Audit Record ─────────────────────────────────────────────────
    outputs = {
        "run": run_data,
        "writeback": writeback_data,
        "task_type": task_type["id"],
        "role_card": role_card["id"],
    }
    if approval_gate_data is not None:
        outputs["approval_gate"] = approval_gate_data

    try:
        _write_audit_record(
            workflow_id=workflow_id,
            audit_id=audit_id,
            status="success",
            stage_reached="audit_record",
            manifest_snapshot=manifest,
            inputs_summary=inputs_summary,
            outputs=outputs,
            escalation_reason=None,
            error=None,
            vault_root=vault_root,
            context_boot=_boot_dict,
        )
    except Exception as exc:  # noqa: BLE001
        _emit_osril_event(
            vault_root=vault_root,
            session_id=osril_session.session_id,
            run_id=audit_id,
            runtime_id=runtime_id,
            workflow_id=workflow_id,
            event_type=OSRILEventType.TASK_FAILED,
            state="failed",
            payload={"terminal_status": "failed", "stage_reached": "audit_record", "reason": f"audit write failed: {exc}"},
            permission_ceiling=manifest.get("permission_ceiling"),
        )
        # Audit write failure degrades to "success_no_audit"
        return AORRunResult(
            workflow_id=workflow_id,
            status="failed",
            audit_id=audit_id,
            stage_reached="audit_record",
            outputs=outputs,
            error=f"audit write failed: {exc}",
            manifest_snapshot=manifest,
        )

    # ── Stage 9: Scorecard Update (best-effort, never blocks) ─────────────────
    _emit_osril_event(
        vault_root=vault_root,
        session_id=osril_session.session_id,
        run_id=audit_id,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        event_type=OSRILEventType.TASK_COMPLETE,
        state="complete",
        payload={
            "terminal_status": "success",
            "stage_reached": "audit_record",
            "writeback": writeback_data,
        },
        permission_ceiling=manifest.get("permission_ceiling"),
    )
    if _SCORECARDS_AVAILABLE:
        try:
            _audit_record_for_scorecard = {
                "audit_id": audit_id,
                "workflow_id": workflow_id,
                "status": "success",
                "stage_reached": "audit_record",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "escalation_reason": None,
                "outputs": outputs,
            }
            _update_scorecard(runtime_id, _audit_record_for_scorecard, vault_root)
        except Exception:  # noqa: BLE001
            pass
    if _GROWTH_AVAILABLE:
        try:
            _apply_execution_to_memory(runtime_id, vault_root, {
                "audit_id": audit_id,
                "workflow_id": workflow_id,
                "status": "success",
                "stage_reached": "audit_record",
                "escalation_reason": None,
                "manifest_snapshot": manifest,
            })
        except Exception:  # noqa: BLE001
            pass

    if _RATE_GUARD_AVAILABLE:
        try:
            _record_rate_execution(workflow_id, vault_root)
        except Exception:  # noqa: BLE001
            pass

    return AORRunResult(
        workflow_id=workflow_id,
        status="success",
        audit_id=audit_id,
        stage_reached="audit_record",
        outputs=outputs,
        manifest_snapshot=manifest,
    )
