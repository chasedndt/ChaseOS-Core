"""Tests for pending-review Context Memory Core candidate storage."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from runtime.memory.candidate_store import (
    PERSONAL_MAP_BLOCKED_EFFECTS,
    PersonalMapCandidate,
    build_personal_map_candidate_queue,
    build_personal_map_edge_candidate,
    build_personal_map_node_candidate,
    edit_personal_map_candidate,
    import_personal_map_candidates_from_source,
    load_personal_map_candidates,
    persist_personal_map_candidate,
)
from runtime.memory.personal_map import PersonalMapEdge, PersonalMapNode
from runtime.common.evidence import EvidenceRef


_TMP_ROOT = Path(__file__).resolve().parent / "_tmp_candidate_store"


class PersonalMapCandidateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = _TMP_ROOT
        self._clean_tmp_root()
        self.tmp_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._clean_tmp_root()

    def _clean_tmp_root(self) -> None:
        if self.tmp_root.exists():
            resolved = self.tmp_root.resolve()
            if resolved.name != "_tmp_candidate_store":
                raise AssertionError(f"refusing to remove unexpected path: {resolved}")
            shutil.rmtree(resolved)

    def _node(self) -> PersonalMapNode:
        return PersonalMapNode(
            node_id="personal_domain_business_os",
            node_type="business_os",
            label="Business OS",
            summary="Candidate business operating domain.",
        )

    def _edge(self) -> PersonalMapEdge:
        return PersonalMapEdge(
            edge_id="edge_business_os_content",
            source_node_id="personal_domain_business_os",
            target_node_id="domain_content_creation_edge",
            relation="depends_on",
            confidence=0.7,
        )

    def test_persists_and_loads_personal_map_node_candidate(self) -> None:
        candidate = build_personal_map_node_candidate(
            self._node(),
            reason="Pulse card suggested a Business OS Personal Map node.",
            source_card_id="pulse-card-001",
            created_at="2026-04-30T02:20:00+01:00",
        )

        artifact = persist_personal_map_candidate(self.tmp_root, candidate)
        loaded = load_personal_map_candidates(self.tmp_root)

        self.assertEqual(
            artifact.path,
            "07_LOGS/Pulse-Decks/memory-candidates/personal-map/"
            "2026-04-30-personal-map-candidates.jsonl",
        )
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].candidate_type, "node")
        self.assertEqual(loaded[0].node.label, "Business OS")
        self.assertTrue(loaded[0].review_required)
        self.assertTrue(loaded[0].candidate_only)
        self.assertFalse(loaded[0].applied_to_personal_map)
        self.assertFalse(loaded[0].canonical_writeback_allowed)
        self.assertFalse(loaded[0].second_datastore_write_allowed)

    def test_persists_edge_candidate_without_applying_graph_mutation(self) -> None:
        candidate = build_personal_map_edge_candidate(
            self._edge(),
            reason="Pulse card inferred a relationship for operator review.",
            created_at="2026-04-30T03:00:00+01:00",
        )

        persist_personal_map_candidate(self.tmp_root, candidate)
        loaded = load_personal_map_candidates(self.tmp_root)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].candidate_type, "edge")
        self.assertEqual(loaded[0].edge.relation, "depends_on")
        self.assertFalse(loaded[0].mutates_canonical_state)
        self.assertFalse(loaded[0].approves_memory)
        self.assertFalse(loaded[0].creates_task)

    def test_queue_is_read_only_and_declares_blocked_effects(self) -> None:
        persist_personal_map_candidate(
            self.tmp_root,
            build_personal_map_node_candidate(
                self._node(),
                reason="Queue visibility check.",
                created_at="2026-04-30T04:00:00+01:00",
            ),
        )
        before = sorted(path.as_posix() for path in self.tmp_root.rglob("*"))

        queue = build_personal_map_candidate_queue(self.tmp_root)
        after = sorted(path.as_posix() for path in self.tmp_root.rglob("*"))

        self.assertEqual(before, after)
        self.assertEqual(queue.queue_status, "read_only")
        self.assertEqual(queue.item_count, 1)
        self.assertEqual(queue.pending_count, 1)
        self.assertEqual(set(queue.to_dict()["blocked_effects"]), set(PERSONAL_MAP_BLOCKED_EFFECTS))
        self.assertEqual(queue.writes, [])
        self.assertFalse(queue.canonical_writeback_allowed)

    def test_empty_queue_does_not_create_candidate_folder(self) -> None:
        queue = build_personal_map_candidate_queue(self.tmp_root)

        self.assertEqual(queue.item_count, 0)
        self.assertFalse((self.tmp_root / "07_LOGS").exists())

    def test_rejects_canonical_or_second_datastore_effects(self) -> None:
        base = build_personal_map_node_candidate(
            self._node(),
            reason="Rejected flag check.",
            created_at="2026-04-30T05:00:00+01:00",
        ).to_dict()

        for forbidden_flag in (
            "canonical_writeback_allowed",
            "applied_to_personal_map",
            "mutates_canonical_state",
            "approves_memory",
            "creates_task",
            "second_datastore_write_allowed",
        ):
            payload = dict(base)
            payload[forbidden_flag] = True
            with self.subTest(forbidden_flag=forbidden_flag):
                with self.assertRaises(ValueError):
                    PersonalMapCandidate.from_dict(payload)

    def test_rejects_candidate_log_path_outside_personal_map_root(self) -> None:
        outside = self.tmp_root.parent / "outside-personal-map-candidates.jsonl"

        with self.assertRaises(ValueError):
            load_personal_map_candidates(self.tmp_root, log_path=outside)

    def test_candidate_edits_preserve_existing_node_evidence_when_merging_updates(self) -> None:
        source = self.tmp_root / "operator-approved-personal-map.json"
        source.write_text(
            json.dumps(
                {
                    "nodes": [
                        {
                            "node_id": "personal_project_pulse",
                            "node_type": "project",
                            "label": "Pulse",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        import_personal_map_candidates_from_source(
            self.tmp_root,
            source,
            approved_sources=[source],
            created_at="2026-05-12T00:00:00+00:00",
        )
        candidate = load_personal_map_candidates(self.tmp_root)[0]
        original_evidence = candidate.node.evidence[0]

        edited = edit_personal_map_candidate(
            self.tmp_root,
            candidate.candidate_id,
            {
                "label": "Pulse Memory",
                "evidence": [
                    EvidenceRef(
                        source_path="07_LOGS/Pulse-Decks/manual-review.md",
                        source_type="operator_review_note",
                        summary="Operator review clarified the node label.",
                        trust_label="operator_reviewed",
                        observed_at="2026-05-12T00:05:00+00:00",
                    )
                ],
            },
            editor="reviewer",
            edited_at="2026-05-12T00:10:00+00:00",
        )

        self.assertEqual(edited.node.label, "Pulse Memory")
        self.assertIn(original_evidence, edited.node.evidence)
        self.assertEqual(len(edited.node.evidence), 2)
        self.assertEqual(edited.status, "pending_review")
        self.assertEqual(edited.status_history[-1]["reason"], "edited")
        self.assertEqual(edited.revisions[-1]["editor"], "reviewer")
        self.assertEqual(
            edited.revisions[-1]["before"]["node"]["evidence"][0]["source_type"],
            "approved_personal_map_import_source",
        )
        self.assertEqual(len(edited.revisions[-1]["after"]["node"]["evidence"]), 2)

    def test_candidate_reason_edit_rejects_secret_like_content_without_appending_revision(self) -> None:
        candidate = build_personal_map_node_candidate(
            self._node(),
            reason="Safe operator review reason.",
            created_at="2026-05-12T00:00:00+00:00",
        )
        artifact = persist_personal_map_candidate(self.tmp_root, candidate)
        log_path = self.tmp_root / artifact.path
        before_log = log_path.read_text(encoding="utf-8")

        with self.assertRaises(ValueError):
            edit_personal_map_candidate(
                self.tmp_root,
                candidate.candidate_id,
                {"reason": "api_key=sk-test1234567890"},
                editor="reviewer",
                edited_at="2026-05-12T00:10:00+00:00",
            )

        after_log = log_path.read_text(encoding="utf-8")
        loaded = load_personal_map_candidates(self.tmp_root)
        self.assertEqual(after_log, before_log)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].reason, "Safe operator review reason.")
        self.assertEqual(loaded[0].revisions, [])
        self.assertEqual(len(loaded[0].status_history), 1)

    def test_candidate_edits_preserve_existing_edge_evidence_when_update_is_empty(self) -> None:
        source = self.tmp_root / "operator-approved-personal-map.json"
        source.write_text(
            json.dumps(
                {
                    "edges": [
                        {
                            "edge_id": "edge_pulse_depends_on_memory",
                            "source_node_id": "personal_project_pulse",
                            "target_node_id": "personal_domain_memory",
                            "relation": "depends_on",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        import_personal_map_candidates_from_source(
            self.tmp_root,
            source,
            approved_sources=[source],
            created_at="2026-05-12T00:00:00+00:00",
        )
        candidate = load_personal_map_candidates(self.tmp_root)[0]
        original_evidence = candidate.edge.evidence[0]

        edited = edit_personal_map_candidate(
            self.tmp_root,
            candidate.candidate_id,
            {"relation": "supports", "evidence": []},
            editor="reviewer",
            edited_at="2026-05-12T00:10:00+00:00",
        )

        self.assertEqual(edited.edge.relation, "supports")
        self.assertEqual(edited.edge.evidence, [original_evidence])
        self.assertEqual(edited.status, "pending_review")
        self.assertEqual(edited.status_history[-1]["reason"], "edited")
        self.assertEqual(edited.revisions[-1]["editor"], "reviewer")
        self.assertEqual(
            edited.revisions[-1]["after"]["edge"]["evidence"][0]["source_type"],
            "approved_personal_map_import_source",
        )


if __name__ == "__main__":
    unittest.main()
