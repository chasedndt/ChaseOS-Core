"""
test_incident_review.py — INC: operator tooling to review/clear the incident backlog.

The behavior tripwire alerts on unreviewed incident candidates and nothing
previously marked them reviewed. These tests cover the stats / dedup / review
surface that clears the backlog (and the guard against an unfiltered blanket
clear).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.memory.incident_review import (
    dedup_incidents,
    incident_stats,
    list_incidents,
    reason_signature,
    review_incidents,
)


def _seed(vault_root: Path, runtime_id: str = "openclaw") -> Path:
    repair_dir = vault_root / "runtime" / "memory" / "repair"
    repair_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        {
            "workflow_id": "sbp_strikezone_digest",
            "outcome": "escalated",
            "escalation_reason": "Missing API key: environment variable 'ANTHROPIC_API_KEY' is not set.",
            "notes": "",
            "recorded_at": f"2026-05-19T23:55:{i:02d}.000000+00:00",
            "operator_reviewed": False,
        }
        for i in range(10)
    ]
    candidates += [
        {
            "workflow_id": "hermes_operator_today_shadow",
            "outcome": "escalated",
            "escalation_reason": "workflow 'hermes_operator_today_shadow' not found in registry",
            "notes": "",
            "recorded_at": "2026-05-01T00:00:00.000000+00:00",
            "operator_reviewed": False,
        }
        for _ in range(3)
    ]
    path = repair_dir / f"{runtime_id}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "layer": "C",
                "memory_family": "execution_repair",
                "runtime_id": runtime_id,
                "status": "active",
                "repair_patterns": [],
                "incident_candidates": candidates,
                "governance_boundary": "operator review only",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_reason_signature_groups_known_classes():
    assert reason_signature("... 'ANTHROPIC_API_KEY' is not set") == "anthropic_api_key_config_gap"
    assert reason_signature("workflow 'x' not found in registry") == "workflow_not_in_registry"
    assert reason_signature("task_type 'y' is not in the task type table") == "task_type_not_in_table"


def test_stats_counts_unreviewed(tmp_path):
    _seed(tmp_path)
    stats = incident_stats("openclaw", tmp_path)
    assert stats["total"] == 13
    assert stats["unreviewed"] == 13
    assert stats["by_signature"]["anthropic_api_key_config_gap"]["total"] == 10


def test_dedup_collapses_identical(tmp_path):
    _seed(tmp_path)
    preview = dedup_incidents("openclaw", tmp_path, dry_run=True)
    assert preview["before"] == 13 and preview["after"] == 2 and preview["collapsed"] == 11
    # dry-run did not mutate
    assert incident_stats("openclaw", tmp_path)["total"] == 13

    applied = dedup_incidents("openclaw", tmp_path, dry_run=False)
    assert applied["after"] == 2
    rows = list_incidents("openclaw", tmp_path)
    assert len(rows) == 2
    anth = next(r for r in rows if reason_signature(r["escalation_reason"]) == "anthropic_api_key_config_gap")
    assert anth["duplicate_count"] == 10
    assert anth["first_recorded_at"] <= anth["last_recorded_at"]


def test_review_requires_a_filter(tmp_path):
    _seed(tmp_path)
    with pytest.raises(ValueError):
        review_incidents("openclaw", tmp_path, dry_run=False)


def test_review_marks_only_matching_signature(tmp_path):
    _seed(tmp_path)
    preview = review_incidents(
        "openclaw", tmp_path, signature="anthropic_api_key_config_gap", dry_run=True
    )
    assert preview["matched"] == 10 and preview["marked_reviewed"] == 10
    assert incident_stats("openclaw", tmp_path)["unreviewed"] == 13  # dry-run unchanged

    applied = review_incidents(
        "openclaw",
        tmp_path,
        signature="anthropic_api_key_config_gap",
        note="stale per provider-agnostic rule",
        dry_run=False,
    )
    assert applied["marked_reviewed"] == 10
    stats = incident_stats("openclaw", tmp_path)
    assert stats["unreviewed"] == 3  # only the non-anthropic remain
    cleared = list_incidents("openclaw", tmp_path, signature="anthropic_api_key_config_gap")
    assert all(c["operator_reviewed"] for c in cleared)
    assert all(c["resolution_note"] == "stale per provider-agnostic rule" for c in cleared)
