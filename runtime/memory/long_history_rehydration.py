"""Bounded long-history rehydration manifest builder for Phase 11 Chat.

This module defines the lower-phase restore contract that Phase 11 Chat may
consume later.  It is deliberately read-only: it builds an operator-visible
manifest from approved summaries and source hashes, but it does not write a
restore manifest, replay provider thread state, inject raw history, or promote
anything into canonical memory.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

RETENTION_CLASS = "operator-history-retention-governed"
PRIVACY_SCOPE = "operator-local-vault-scoped"
SURFACE_ID = "bounded_long_history_rehydration_loader"
MODEL_VERSION = "runtime.memory.long_history_rehydration.v1"
RESTORE_MODE = "bounded-summary-manifest-restore"
MANIFEST_OWNER = "runtime.memory.long_history_rehydration"
PHASE11_CONSUMER_ROLE = "consumer_of_restore_manifest"

_ALLOWED_SOURCE_ROOTS = (
    "07_LOGS/Conversations/",
    "07_LOGS/Agent-Activity/",
    "07_LOGS/Operator-Briefs/",
)
_RAW_HISTORY_KEYS = {
    "raw_history",
    "raw_transcript",
    "full_history",
    "messages",
    "conversation_messages",
    "content_raw",
}
_PROVIDER_STATE_KEYS = {
    "provider_thread_state",
    "provider_state",
    "thread_state",
    "openai_thread_id",
    "anthropic_conversation_id",
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _estimate_tokens(text: str) -> int:
    """Conservative, dependency-free token estimate for bounded windows."""

    words = len(str(text or "").split())
    chars = len(str(text or ""))
    return max(words, (chars + 3) // 4, 1 if text else 0)


def _relative_source_path(source_path: Any) -> str:
    value = str(source_path or "").replace("\\", "/").strip().lstrip("/")
    while "//" in value:
        value = value.replace("//", "/")
    return value


def _source_allowed(path: str) -> bool:
    return bool(path) and ".." not in Path(path).parts and path.startswith(_ALLOWED_SOURCE_ROOTS)


def _file_sha256(vault: Path, rel_path: str) -> str | None:
    target = (vault / rel_path).resolve()
    try:
        target.relative_to(vault)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _has_any(source: dict[str, Any], keys: set[str]) -> bool:
    return any(key in source and source.get(key) not in (None, "", [], {}) for key in keys)


def _public_source_record(
    *,
    index: int,
    source_path: str,
    source_sha256: str,
    summary: str,
    token_estimate: int,
    audit_refs: list[str],
) -> dict[str, Any]:
    return {
        "chunk_id": f"restore-chunk-{index:03d}-{_digest(source_path + source_sha256)[:12]}",
        "source_path": source_path,
        "source_sha256": source_sha256,
        "content_kind": "bounded_summary",
        "content": summary,
        "token_estimate": token_estimate,
        "audit_refs": audit_refs,
        "canonical_memory": False,
        "hidden_memory": False,
        "raw_history_included": False,
        "provider_hidden_state_included": False,
        "provenance_visible": True,
    }


def build_bounded_long_history_restore_manifest(
    vault_root: str | Path,
    *,
    session_id: str,
    sources: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    max_restore_tokens: int = 1200,
    max_restore_sources: int = 8,
    restore_window_label: str = "phase11-chat-resume-window",
) -> dict[str, Any]:
    """Build a read-only long-history restore manifest from governed summaries.

    The function may read source files only to verify their hashes.  It returns
    bounded summary chunks with visible provenance; it never returns raw source
    file text, opaque provider state, or write-capable/canonical permissions.
    """

    vault = Path(vault_root).resolve()
    normalized_sources = list(sources or [])
    blocked_reasons: list[str] = []
    restored_chunks: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    seen_paths: dict[str, str] = {}
    used_tokens = 0

    if not session_id:
        blocked_reasons.append("session_id_required")
    if max_restore_tokens <= 0:
        blocked_reasons.append("positive_token_budget_required")
    if max_restore_sources <= 0:
        blocked_reasons.append("positive_source_limit_required")

    for source_index, raw_source in enumerate(normalized_sources, start=1):
        source = dict(raw_source or {})
        source_path = _relative_source_path(source.get("source_path"))
        expected_sha = str(source.get("source_sha256") or "").strip().lower()
        summary = " ".join(str(source.get("bounded_summary") or "").split())
        audit_refs = [str(ref) for ref in source.get("audit_refs") or []]
        source_blockers: list[str] = []

        if not _source_allowed(source_path):
            source_blockers.append("restore_source_path_not_allowed")
        if not expected_sha or len(expected_sha) != 64:
            source_blockers.append("source_sha256_required")
        if not summary:
            source_blockers.append("bounded_summary_required")
        if source.get("approved_for_restore") is not True:
            source_blockers.append("source_not_approved_for_restore")
        if source.get("retention_class") != RETENTION_CLASS or source.get("privacy_scope") != PRIVACY_SCOPE:
            source_blockers.append("retention_privacy_policy_mismatch")
        if _has_any(source, _RAW_HISTORY_KEYS):
            source_blockers.append("raw_full_history_auto_injection_requested")
        if _has_any(source, _PROVIDER_STATE_KEYS):
            source_blockers.append("provider_hidden_state_restore_requested")

        if source_path in seen_paths and seen_paths[source_path] != expected_sha:
            source_blockers.append("conflicting_restore_source_record")
        elif source_path:
            seen_paths[source_path] = expected_sha

        actual_sha = _file_sha256(vault, source_path) if source_path else None
        if actual_sha is None:
            source_blockers.append("restore_source_not_found_or_unreadable")
        elif expected_sha and actual_sha != expected_sha:
            source_blockers.append("source_hash_mismatch")

        token_estimate = _estimate_tokens(summary)
        if len(restored_chunks) >= max_restore_sources:
            source_blockers.append("source_window_limit_exceeded")
        if used_tokens + token_estimate > max_restore_tokens:
            source_blockers.append("token_budget_exceeded")

        blocked_reasons.extend(source_blockers)
        source_records.append(
            {
                "source_path": source_path,
                "source_sha256": expected_sha,
                "actual_sha256_verified": actual_sha == expected_sha if actual_sha and expected_sha else False,
                "approved_for_restore": source.get("approved_for_restore") is True,
                "retention_class": source.get("retention_class"),
                "privacy_scope": source.get("privacy_scope"),
                "bounded_summary_present": bool(summary),
                "token_estimate": token_estimate,
                "accepted_for_restore": not source_blockers,
                "blocked_reasons": list(dict.fromkeys(source_blockers)),
                "audit_refs": audit_refs,
            }
        )

        if source_blockers:
            continue

        restored_chunks.append(
            _public_source_record(
                index=len(restored_chunks) + 1,
                source_path=source_path,
                source_sha256=expected_sha,
                summary=summary,
                token_estimate=token_estimate,
                audit_refs=audit_refs,
            )
        )
        used_tokens += token_estimate

    unique_blockers = list(dict.fromkeys(blocked_reasons))
    restore_ready = bool(restored_chunks) and not unique_blockers

    return {
        "ok": True,
        "surface": SURFACE_ID,
        "model_version": MODEL_VERSION,
        "generated_at_utc": _now_utc(),
        "vault_root": str(vault),
        "session_id": session_id,
        "read_only": True,
        "restore_mode": RESTORE_MODE,
        "summary": {
            "restore_ready": restore_ready,
            "restored_source_count": len(restored_chunks),
            "candidate_source_count": len(normalized_sources),
            "max_restore_tokens": max_restore_tokens,
            "used_restore_tokens": used_tokens,
            "max_restore_sources": max_restore_sources,
            "restore_window_label": restore_window_label,
            "operator_visible_manifest_required": True,
            "source_hashes_required": True,
            "bounded_summaries_only": True,
            "conflict_rule": "current Gate/AOR policy and live approval envelope override restored context",
        },
        "restore_manifest_schema": {
            "required_fields_per_source": [
                "source_path",
                "source_sha256",
                "bounded_summary",
                "approved_for_restore",
                "retention_class",
                "privacy_scope",
            ],
            "optional_fields_per_source": ["audit_refs", "summary_created_at_utc", "summary_author"],
            "prohibited_fields_per_source": sorted(_RAW_HISTORY_KEYS | _PROVIDER_STATE_KEYS),
            "limits": {
                "max_restore_tokens": max_restore_tokens,
                "max_restore_sources": max_restore_sources,
                "allowed_source_roots": list(_ALLOWED_SOURCE_ROOTS),
            },
            "output_chunks": [
                "chunk_id",
                "source_path",
                "source_sha256",
                "content_kind=bounded_summary",
                "content",
                "token_estimate",
                "audit_refs",
                "provenance_visible",
            ],
        },
        "safe_restore_algorithm": [
            "normalize_candidate_source_paths_under_allowed_log_roots",
            "verify_each_source_hash_against_inspectable_file_bytes",
            "require_operator_or_gate_approval_marker_per_source",
            "require_retention_and_privacy_scope_match",
            "reject_raw_history_or_provider_hidden_state_fields",
            "apply_source_count_and_token_window_limits_before_emitting_chunks",
            "emit_operator_visible_manifest_with_source_hashes_and_audit_refs",
            "treat_current_gate_aor_policy_as_authoritative_on_conflict",
        ],
        "source_records": source_records,
        "restore_chunks": restored_chunks,
        "blocked_reasons": unique_blockers,
        "retention_privacy_checks": {
            "required_retention_class": RETENTION_CLASS,
            "required_privacy_scope": PRIVACY_SCOPE,
            "all_accepted_sources_retention_privacy_checked": all(
                record["accepted_for_restore"]
                and record["retention_class"] == RETENTION_CLASS
                and record["privacy_scope"] == PRIVACY_SCOPE
                for record in source_records
                if record["accepted_for_restore"]
            ),
            "secrets_policy": "raw credentials, tokens, API keys, and secret-bearing excerpts must not appear in bounded summaries",
            "retention_manager_required_for_delete_export": True,
        },
        "conflict_handling": {
            "duplicate_path_different_hash_blocks_restore": True,
            "hash_mismatch_blocks_restore": True,
            "current_policy_wins_over_restored_context": True,
            "restored_context_cannot_override_gate_or_canonical_truth": True,
        },
        "ownership": {
            "manifest_owner_surface": MANIFEST_OWNER,
            "phase11_chat_role": PHASE11_CONSUMER_ROLE,
            "lower_phase_owner": "runtime/context or runtime/memory recovery under AOR/Gate governance",
        },
        "authority": {
            "read_only": True,
            "phase11_chat_memory_owner": False,
            "restore_manifest_write_allowed": False,
            "conversation_log_write_allowed": False,
            "canonical_promotion_allowed": False,
            "canonical_writeback_allowed": False,
            "raw_full_history_auto_injection_allowed": False,
            "provider_hidden_state_restore_allowed": False,
            "hidden_memory_allowed": False,
            "approval_consumption_allowed": False,
            "provider_calls_allowed": False,
            "runtime_dispatch_allowed": False,
            "agent_bus_task_write_allowed": False,
            "browser_control_allowed": False,
        },
        "no_hidden_memory_no_canonical_proof": {
            "raw_source_text_returned": False,
            "provider_thread_state_returned": False,
            "hidden_cache_written": False,
            "manifest_file_written": False,
            "canonical_promotion_performed": False,
            "canonical_writeback_performed": False,
            "restore_chunks_are_bounded_summaries": True,
            "source_hashes_visible": True,
            "operator_visible_manifest": True,
        },
    }


__all__ = [
    "PRIVACY_SCOPE",
    "RETENTION_CLASS",
    "build_bounded_long_history_restore_manifest",
]
