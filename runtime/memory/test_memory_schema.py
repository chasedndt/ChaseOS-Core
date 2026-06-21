from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.memory.context_events import ContextEvent
from runtime.memory.feedback_rules import FeedbackRule, evaluate_feedback
from runtime.memory.memory_atoms import MemoryAtom
from runtime.memory.personal_map import PersonalMapEdge, PersonalMapGraph, PersonalMapNode
from runtime.common.evidence import EvidenceRef


def test_context_event_schema_blocks_unapproved_canonical_writeback() -> None:
    event = ContextEvent(
        event_id="event-1",
        event_type="feedback",
        summary="Operator corrected a Pulse card.",
        source_path="runtime/pulse/deck.json",
        source_type="pulse_feedback",
        canonical_writeback_target="02_KNOWLEDGE/Example.md",
    )

    try:
        event.validate()
    except ValueError as exc:
        assert "writeback_allowed" in str(exc)
    else:
        raise AssertionError("canonical writeback target should require approval")


def test_memory_atom_stays_candidate_without_promotion() -> None:
    atom = MemoryAtom(
        atom_id="atom-1",
        layer="B",
        scope="user",
        content="User prefers future-facing Pulse cards.",
        evidence=[
            EvidenceRef(
                source_path="06_AGENTS/ChaseOS-Pulse-Architecture.md",
                source_type="architecture_doc",
                summary="Pulse design requires future-facing cards.",
            )
        ],
    )

    payload = atom.to_dict()
    assert payload["status"] == "candidate"
    assert payload["promotion_state"] == "none"
    assert payload["canonical_writeback_enabled"] is False


def test_personal_map_graph_nodes_and_edges_validate() -> None:
    graph = PersonalMapGraph(graph_id="personal-map")
    graph.add_node(PersonalMapNode(node_id="user", node_type="person", label="Operator"))
    graph.add_node(PersonalMapNode(node_id="chaseos", node_type="project", label="ChaseOS"))
    graph.add_edge(
        PersonalMapEdge(
            edge_id="edge-1",
            source_node_id="user",
            target_node_id="chaseos",
            relation="builds",
            confidence=0.9,
        )
    )

    payload = graph.to_dict()
    assert payload["nodes"]["user"]["node_type"] == "person"
    assert payload["edges"]["edge-1"]["relation"] == "builds"
    assert payload["canonical_writeback_enabled"] is False


def test_personal_map_supports_master_context_domain_nodes() -> None:
    graph = PersonalMapGraph(graph_id="personal-map-domain")
    graph.add_node(
        PersonalMapNode(
            node_id="business-os",
            node_type="business_os",
            label="Business OS",
            tags=["shopify", "wordpress", "content"],
        )
    )

    payload = graph.to_dict()
    assert payload["nodes"]["business-os"]["node_type"] == "business_os"


def test_feedback_rules_create_candidates_only() -> None:
    result = evaluate_feedback("mark_personal_map_candidate")
    assert result.creates_personal_map_candidate is True
    assert result.requires_operator_review is True
    assert result.canonical_writeback_allowed is False


def test_pulse_feedback_rule_is_durable_candidate_only() -> None:
    result = evaluate_feedback("promote_to_memory")
    rule = FeedbackRule(
        rule_id="feedback-rule-1",
        rule_type="create_memory_candidate",
        target_type="card_type",
        target="memory_update",
        source_card_id="pulse-card-1",
        reason="Operator requested promotion to memory review.",
    )

    payload = rule.to_dict()
    assert result.creates_memory_candidate is True
    assert result.requires_operator_review is True
    assert payload["status"] == "candidate"
    assert payload["canonical_writeback_allowed"] is False


def test_invalid_personal_map_node_type_fails() -> None:
    node = PersonalMapNode(node_id="bad", node_type="secret", label="Bad")
    try:
        node.validate()
    except ValueError as exc:
        assert "node_type" in str(exc)
    else:
        raise AssertionError("invalid node_type should fail")
