from __future__ import annotations

import json
import socket
import urllib.request
from pathlib import Path

from runtime.memory.intelligence import (
    analyze_candidates,
    classify_candidate,
    emit_eval_artifact,
    retrieve_relevant,
)


def test_classifies_memory_candidate_categories() -> None:
    examples = {
        "I prefer concise summaries.": "preference",
        "Project ChaseOS uses local-first runtime memory.": "project",
        "Profile: user is a systems builder.": "profile",
        "Goal: ship Memory Intelligence Harness v0.1.": "goal",
        "OpenClaw status is active.": "entity",
        "On 2026-06-25 Hermes launched the bounded lane.": "event",
        "Fact: Python 3.11 is required.": "fact",
        "Please remember the Discord lane boundary.": "request",
        "Random note without strong signals.": "general",
    }

    observed = {text: classify_candidate(text) for text in examples}

    assert observed == examples


def test_parses_simple_entity_attribute_claims_and_supersedes_older_claims() -> None:
    candidates = analyze_candidates(
        [
            "OpenClaw status is planned.",
            "OpenClaw status is active.",
        ]
    )

    first, second = candidates
    assert first.category == "entity"
    assert first.entity == "OpenClaw"
    assert first.attribute == "status"
    assert first.value == "planned"
    assert first.superseded_by == second.id
    assert second.supersedes == first.id
    assert second.entity == "OpenClaw"
    assert second.attribute == "status"
    assert second.value == "active"


def test_detects_near_duplicate_lexical_candidates() -> None:
    candidates = analyze_candidates(
        [
            "I prefer concise summaries.",
            "I prefer concise summary",
            "Goal: ship Memory Intelligence Harness v0.1.",
        ]
    )

    assert candidates[0].is_near_duplicate is False
    assert candidates[1].is_near_duplicate is True
    assert candidates[1].duplicate_of == candidates[0].id
    assert candidates[2].is_near_duplicate is False


def test_retrieves_by_deterministic_keyword_relevance() -> None:
    candidates = analyze_candidates(
        [
            "Please remember the Discord lane boundary.",
            "Goal: ship Memory Intelligence Harness v0.1.",
            "Project ChaseOS uses local-first runtime memory.",
        ]
    )

    results = retrieve_relevant(candidates, "memory harness", limit=2)

    assert [item.id for item in results] == [candidates[1].id, candidates[2].id]
    assert [item.relevance_score for item in results] == [2, 1]


def test_emits_eval_artifact_with_counts_and_no_authority_side_effects(tmp_path) -> None:
    candidates = analyze_candidates(
        [
            "I prefer concise summaries.",
            "I prefer concise summary",
            "OpenClaw status is planned.",
            "OpenClaw status is active.",
            "Please remember the Discord lane boundary.",
        ]
    )
    artifact_path = tmp_path / "memory-intelligence-eval.json"

    payload = emit_eval_artifact(candidates, artifact_path)

    assert artifact_path.exists()
    written = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert written == payload
    assert written["schema_version"] == "memory-intelligence-eval.v0.1"
    assert written["counts"] == {
        "total_candidates": 5,
        "near_duplicates": 1,
        "superseded_claims": 1,
        "categories": {
            "entity": 2,
            "preference": 2,
            "request": 1,
        },
    }
    assert written["authority_flags"] == {
        "network_call_performed": False,
        "provider_call_performed": False,
        "hermes_memory_mutated": False,
        "runtime_memory_provider_switched": False,
        "canonical_truth_promoted": False,
        "external_source_used": False,
    }


def test_fixture_eval_matches_expected_pass_fail_counts_and_retrieval_probe(tmp_path) -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "runtime"
        / "memory"
        / "fixtures"
        / "memory-intelligence-eval-v0.1.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    candidates = analyze_candidates(item["text"] for item in fixture["candidates"])

    payload = emit_eval_artifact(candidates, tmp_path / "fixture-eval.json")
    retrieval_probe = [
        {"id": item.id, "score": item.relevance_score}
        for item in retrieve_relevant(candidates, "memory harness", limit=2)
    ]
    checks = {
        "counts": payload["counts"] == fixture["counts"],
        "authority_flags": payload["authority_flags"] == fixture["authority_flags"],
        "retrieval_probe": retrieval_probe == fixture["retrieval_probe"],
    }

    assert checks == {
        "counts": True,
        "authority_flags": True,
        "retrieval_probe": True,
    }
    assert {"passed": sum(checks.values()), "failed": 0, "total": len(checks)} == {
        "passed": 3,
        "failed": 0,
        "total": 3,
    }


def test_authority_boundary_avoids_network_provider_hermes_writes_and_runtime_switch(
    monkeypatch,
    tmp_path,
) -> None:
    def fail_network(*_args, **_kwargs):
        raise AssertionError("memory intelligence harness must not reach the network")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    candidates = analyze_candidates(
        [
            "Project ChaseOS uses local-first runtime memory.",
            "OpenClaw status is planned.",
            "OpenClaw status is active.",
        ]
    )
    payload = emit_eval_artifact(candidates, tmp_path / "eval" / "artifact.json")
    results = retrieve_relevant(candidates, "runtime memory", limit=2)

    assert [item.id for item in results] == ["candidate_1"]
    assert payload["authority_flags"] == {
        "network_call_performed": False,
        "provider_call_performed": False,
        "hermes_memory_mutated": False,
        "runtime_memory_provider_switched": False,
        "canonical_truth_promoted": False,
        "external_source_used": False,
    }
    assert not (tmp_path / "home" / ".hermes").exists()
