from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.memory.memory_clusters import MemoryCluster
from runtime.memory.temporal_facts import TemporalFact
from runtime.common.evidence import EvidenceRef


def test_memory_cluster_keeps_related_operating_context_candidate_only() -> None:
    cluster = MemoryCluster(
        cluster_id="cluster_business_os",
        label="Business OS",
        description="User business and ecommerce operating domain.",
        memory_ids=["mem_business_001"],
        related_projects=["Drip and Drown Town UK"],
        related_agents=["openflow"],
        related_card_types=["Business OS Opportunity", "Manual Input Needed"],
        evidence=[
            EvidenceRef(
                source_path="06_AGENTS/Context-Memory-Core.md",
                source_type="architecture_doc",
                summary="Memory clusters group related memory atoms.",
            )
        ],
    )

    payload = cluster.to_dict()
    assert payload["status"] == "candidate"
    assert payload["canonical_writeback_enabled"] is False
    assert payload["related_card_types"] == ["Business OS Opportunity", "Manual Input Needed"]


def test_memory_cluster_blocks_canonical_writeback() -> None:
    cluster = MemoryCluster(
        cluster_id="cluster_bad",
        label="Bad",
        canonical_writeback_enabled=True,
    )

    try:
        cluster.validate()
    except ValueError as exc:
        assert "canonical writeback" in str(exc)
    else:
        raise AssertionError("memory cluster should block canonical writeback")


def test_temporal_fact_validates_validity_window() -> None:
    fact = TemporalFact(
        fact_id="fact_pulse_partial",
        summary="ChaseOS Pulse is a partial backend scaffold on 2026-04-29.",
        valid_from="2026-04-29",
        source_event_ids=["ctx_2026_04_29_pulse"],
        confidence=0.9,
        status="current",
    )

    payload = fact.to_dict()
    assert payload["valid_from"] == "2026-04-29"
    assert payload["valid_until"] is None
    assert payload["canonical_writeback_enabled"] is False


def test_temporal_fact_rejects_reversed_window() -> None:
    fact = TemporalFact(
        fact_id="fact_bad",
        summary="Bad temporal window.",
        valid_from="2026-04-29",
        valid_until="2026-04-28",
    )

    try:
        fact.validate()
    except ValueError as exc:
        assert "valid_until" in str(exc)
    else:
        raise AssertionError("temporal fact should reject reversed validity")
