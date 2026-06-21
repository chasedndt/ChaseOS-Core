from __future__ import annotations

import hashlib
from pathlib import Path

from runtime.memory.long_history_rehydration import (
    PRIVACY_SCOPE,
    RETENTION_CLASS,
    build_bounded_long_history_restore_manifest,
)


def _write_source(root: Path, rel: str, text: str) -> str:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_restore_manifest_uses_approved_summaries_with_visible_source_hashes_without_writes(tmp_path: Path) -> None:
    source_hash = _write_source(
        tmp_path,
        "07_LOGS/Conversations/session-1.md",
        "# Session 1\n\nRaw conversation details stay in the inspected source record.",
    )

    manifest = build_bounded_long_history_restore_manifest(
        tmp_path,
        session_id="phase11-goal-1",
        sources=[
            {
                "source_path": "07_LOGS/Conversations/session-1.md",
                "source_sha256": source_hash,
                "bounded_summary": "Operator asked to keep Phase 11 Chat resumable from governed summaries.",
                "approved_for_restore": True,
                "retention_class": RETENTION_CLASS,
                "privacy_scope": PRIVACY_SCOPE,
                "audit_refs": ["07_LOGS/Agent-Activity/2026-05-11-hermes-optimus-example.md"],
            }
        ],
    )

    assert manifest["ok"] is True
    assert manifest["read_only"] is True
    assert manifest["summary"]["restore_ready"] is True
    assert manifest["summary"]["restored_source_count"] == 1
    assert manifest["restore_chunks"][0]["source_sha256"] == source_hash
    assert manifest["restore_chunks"][0]["content"] == "Operator asked to keep Phase 11 Chat resumable from governed summaries."
    assert "Raw conversation details" not in str(manifest)
    assert manifest["authority"]["canonical_promotion_allowed"] is False
    assert manifest["authority"]["provider_hidden_state_restore_allowed"] is False
    assert manifest["authority"]["raw_full_history_auto_injection_allowed"] is False
    assert not (tmp_path / "runtime" / "memory" / "restore-manifests").exists()


def test_raw_history_or_provider_hidden_state_requests_are_denied(tmp_path: Path) -> None:
    source_hash = _write_source(tmp_path, "07_LOGS/Conversations/session-2.md", "raw transcript")

    manifest = build_bounded_long_history_restore_manifest(
        tmp_path,
        session_id="phase11-goal-2",
        sources=[
            {
                "source_path": "07_LOGS/Conversations/session-2.md",
                "source_sha256": source_hash,
                "bounded_summary": "Safe summary only.",
                "raw_history": "full raw transcript must not be injected",
                "provider_thread_state": {"thread_id": "opaque-provider-state"},
                "approved_for_restore": True,
                "retention_class": RETENTION_CLASS,
                "privacy_scope": PRIVACY_SCOPE,
            }
        ],
    )

    assert manifest["summary"]["restore_ready"] is False
    assert "raw_full_history_auto_injection_requested" in manifest["blocked_reasons"]
    assert "provider_hidden_state_restore_requested" in manifest["blocked_reasons"]
    assert manifest["summary"]["restored_source_count"] == 0
    assert "full raw transcript" not in str(manifest)
    assert "opaque-provider-state" not in str(manifest)


def test_token_window_limits_and_retention_privacy_checks_are_enforced(tmp_path: Path) -> None:
    good_hash = _write_source(tmp_path, "07_LOGS/Conversations/session-3.md", "governed source")
    blocked_hash = _write_source(tmp_path, "07_LOGS/Conversations/session-4.md", "ungoverned source")

    manifest = build_bounded_long_history_restore_manifest(
        tmp_path,
        session_id="phase11-goal-3",
        max_restore_tokens=10,
        max_restore_sources=2,
        sources=[
            {
                "source_path": "07_LOGS/Conversations/session-3.md",
                "source_sha256": good_hash,
                "bounded_summary": "short safe summary",
                "approved_for_restore": True,
                "retention_class": RETENTION_CLASS,
                "privacy_scope": PRIVACY_SCOPE,
            },
            {
                "source_path": "07_LOGS/Conversations/session-4.md",
                "source_sha256": blocked_hash,
                "bounded_summary": "this summary has many words and should exceed the deliberately tiny token budget",
                "approved_for_restore": True,
                "retention_class": "missing-retention-review",
                "privacy_scope": "cross-user",
            },
        ],
    )

    assert manifest["summary"]["restore_ready"] is False
    assert manifest["summary"]["restored_source_count"] == 1
    assert "token_budget_exceeded" in manifest["blocked_reasons"]
    assert "retention_privacy_policy_mismatch" in manifest["blocked_reasons"]
    assert manifest["restore_chunks"][0]["source_path"] == "07_LOGS/Conversations/session-3.md"


def test_hash_mismatch_and_conflicting_source_records_block_restore(tmp_path: Path) -> None:
    source_hash = _write_source(tmp_path, "07_LOGS/Conversations/session-5.md", "actual source text")

    manifest = build_bounded_long_history_restore_manifest(
        tmp_path,
        session_id="phase11-goal-4",
        sources=[
            {
                "source_path": "07_LOGS/Conversations/session-5.md",
                "source_sha256": "0" * 64,
                "bounded_summary": "Summary with wrong source hash.",
                "approved_for_restore": True,
                "retention_class": RETENTION_CLASS,
                "privacy_scope": PRIVACY_SCOPE,
            },
            {
                "source_path": "07_LOGS/Conversations/session-5.md",
                "source_sha256": source_hash,
                "bounded_summary": "Conflicting summary for same path.",
                "approved_for_restore": True,
                "retention_class": RETENTION_CLASS,
                "privacy_scope": PRIVACY_SCOPE,
            },
        ],
    )

    assert manifest["summary"]["restore_ready"] is False
    assert "source_hash_mismatch" in manifest["blocked_reasons"]
    assert "conflicting_restore_source_record" in manifest["blocked_reasons"]
    assert manifest["summary"]["restored_source_count"] == 0


def test_phase11_chat_remains_consumer_not_memory_owner(tmp_path: Path) -> None:
    manifest = build_bounded_long_history_restore_manifest(
        tmp_path,
        session_id="phase11-goal-5",
        sources=[],
    )

    assert manifest["ownership"]["manifest_owner_surface"] == "runtime.memory.long_history_rehydration"
    assert manifest["ownership"]["phase11_chat_role"] == "consumer_of_restore_manifest"
    assert manifest["authority"]["phase11_chat_memory_owner"] is False
    assert manifest["summary"]["operator_visible_manifest_required"] is True
