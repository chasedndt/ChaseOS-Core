"""
test_generator.py — SIC Phase 7 Pass 6
Tests for the Output Generation Layer.

Run from the vault root:
    python -m runtime.source_intelligence.output.test_generator

All tests use mock evidence results — no live workspace or API calls needed.
An end-to-end section (test 10) optionally runs against the live phase7-test workspace.
"""

from __future__ import annotations

import os
import sys


# ── Test harness ───────────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0


def _ok(name: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  [PASS] {name}")


def _fail(name: str, reason: str) -> None:
    global _FAIL
    _FAIL += 1
    print(f"  [FAIL] {name}: {reason}")


def _assert(cond: bool, test_name: str, reason: str = "") -> None:
    if cond:
        _ok(test_name)
    else:
        _fail(test_name, reason or "assertion failed")


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_evidence_packet(
    n: int = 1,
    source_title: str = "Test Source",
    source_type: str = "research-digest",
    chunk_text: str = "This is a test chunk with some content about funding rates.",
) -> dict:
    return {
        "workspace_id":          "test-workspace",
        "source_package_id":     f"pkg-{n:04d}",
        "source_title":          source_title,
        "source_slug":           source_title.lower().replace(" ", "-"),
        "source_path":           f"/fake/path/pkg-{n:04d}.json",
        "source_type":           source_type,
        "chunk_id":              f"pkg-{n:04d}_c0000",
        "chunk_index":           0,
        "similarity_score":      round(0.9 - n * 0.05, 4),
        "chunk_text":            chunk_text,
        "section_heading":       f"Section {n}",
        "char_count":            len(chunk_text),
        "provider_name":         "local_stub",
        "model_name":            "local-test-embedding-v1",
        "user_trust_level":      "untrusted",
        "injection_scan_status": "not-scanned",
        "sidecar_path":          f"/fake/path/pkg-{n:04d}.vectors.json",
    }


def _make_ok_evidence_result(
    n_packets: int = 3,
    workspace_id: str = "test-workspace",
    query_text: str = "test query about funding rates",
) -> dict:
    packets = [_make_evidence_packet(i + 1) for i in range(n_packets)]
    return {
        "workspace_id":             workspace_id,
        "query_text":               query_text,
        "retrieval_status":         "ok",
        "provider_name":            "local_stub",
        "model_name":               "local-test-embedding-v1",
        "source_count_considered":  n_packets,
        "chunk_count_considered":   n_packets,
        "top_k":                    5,
        "result_count":             n_packets,
        "evidence_packets":         packets,
        "warnings":                 [],
        "errors":                   [],
    }


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_stub_adapter_basic() -> None:
    """Test 1 — generate_output with stub adapter returns ok-stub status."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result(n_packets=3)
    task_spec = {"output_type": "qa_answer", "query_text": "What are funding rates?"}

    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    _assert(result["generation_status"] == "ok-stub",
            "test_stub_basic: status=ok-stub",
            f"got {result['generation_status']!r}")
    _assert(result["evidence_count"] == 3,
            "test_stub_basic: evidence_count=3",
            f"got {result['evidence_count']}")
    _assert(len(result["citations"]) == 3,
            "test_stub_basic: 3 citations",
            f"got {len(result['citations'])}")
    _assert(result["output_type"] == "qa_answer",
            "test_stub_basic: output_type preserved",
            f"got {result['output_type']!r}")
    _assert(result["knowledge_class"] == "synthesized",
            "test_stub_basic: knowledge_class=synthesized",
            f"got {result['knowledge_class']!r}")
    _assert("[STUB OUTPUT" in result["generated_text"],
            "test_stub_basic: stub label in generated_text")
    _assert(result["vault_writeback_candidate"] is False,
            "test_stub_basic: stub never vault candidate")


def test_no_evidence_packets() -> None:
    """Test 2 — generate_output returns no-evidence when packets list is empty."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result(n_packets=0)
    task_spec = {"output_type": "qa_answer"}

    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    _assert(result["generation_status"] == "no-evidence",
            "test_no_evidence: status=no-evidence",
            f"got {result['generation_status']!r}")
    _assert(len(result["errors"]) > 0,
            "test_no_evidence: errors populated")


def test_retrieval_failed_status() -> None:
    """Test 3 — generate_output returns retrieval-failed for bad retrieval status."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    for bad_status in ("no-workspace", "not-indexed", "query-failed", "empty-workspace"):
        evidence = _make_ok_evidence_result()
        evidence["retrieval_status"] = bad_status
        task_spec = {"output_type": "qa_answer"}

        result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

        _assert(result["generation_status"] == "retrieval-failed",
                f"test_retrieval_failed: bad_status={bad_status!r} yields retrieval-failed",
                f"got {result['generation_status']!r}")


def test_invalid_output_type() -> None:
    """Test 4 — generate_output returns generation-failed for unknown output_type."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result()
    task_spec = {"output_type": "invalid_type_xyz"}

    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    _assert(result["generation_status"] == "generation-failed",
            "test_invalid_output_type: status=generation-failed",
            f"got {result['generation_status']!r}")
    _assert(any("Unknown output_type" in e for e in result["errors"]),
            "test_invalid_output_type: error mentions unknown output_type")


def test_missing_output_type() -> None:
    """Test 5 — generate_output returns generation-failed when output_type missing."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result()
    task_spec = {}  # no output_type

    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    _assert(result["generation_status"] == "generation-failed",
            "test_missing_output_type: status=generation-failed",
            f"got {result['generation_status']!r}")


def test_knowledge_class_mapping() -> None:
    """Test 6 — all output types map to correct ChaseOS knowledge classes."""
    from runtime.source_intelligence.output.prompt_builder import (
        OUTPUT_TYPE_KNOWLEDGE_CLASS,
        get_knowledge_class,
    )

    expected = {
        "source_summary":  "source-derived",
        "faq":             "synthesized",
        "briefing":        "synthesized",
        "synthesis_draft": "synthesized",
        "study_guide":     "synthesized",
        "comparison":      "synthesized",
        "timeline":        "synthesized",
        "qa_answer":       "synthesized",
        "idea_generation": "generated-ideas",
    }

    all_correct = True
    for otype, expected_class in expected.items():
        got = get_knowledge_class(otype)
        if got != expected_class:
            _fail(f"test_knowledge_class: {otype}",
                  f"expected {expected_class!r}, got {got!r}")
            all_correct = False

    if all_correct:
        _ok(f"test_knowledge_class_mapping: all {len(expected)} types correct")


def test_vault_writeback_candidate_logic() -> None:
    """Test 7 — vault_writeback_candidate is False for stub, True for ok with evidence."""
    from runtime.source_intelligence.output.prompt_builder import is_vault_writeback_candidate

    # Stub is never a candidate
    _assert(
        is_vault_writeback_candidate("qa_answer", 3, "ok-stub") is False,
        "test_writeback: ok-stub yields False",
    )
    # ok with 1 packet, min=1 -> candidate
    _assert(
        is_vault_writeback_candidate("qa_answer", 1, "ok") is True,
        "test_writeback: ok + 1 packet (min=1) yields True",
    )
    # ok with 1 packet but min=2 (faq) -> not candidate
    _assert(
        is_vault_writeback_candidate("faq", 1, "ok") is False,
        "test_writeback: ok + 1 packet (min=2) yields False",
    )
    # ok with 2 packets, min=2 (faq) -> candidate
    _assert(
        is_vault_writeback_candidate("faq", 2, "ok") is True,
        "test_writeback: ok + 2 packets (min=2) yields True",
    )
    # generation-failed -> never candidate
    _assert(
        is_vault_writeback_candidate("qa_answer", 5, "generation-failed") is False,
        "test_writeback: generation-failed yields False",
    )


def test_citation_structure() -> None:
    """Test 8 — build_citations returns correct 1-indexed citation list."""
    from runtime.source_intelligence.output.prompt_builder import build_citations

    packets = [
        _make_evidence_packet(1, source_title="Alpha Source"),
        _make_evidence_packet(2, source_title="Beta Source"),
        _make_evidence_packet(3, source_title="Gamma Source"),
    ]
    citations = build_citations(packets)

    _assert(len(citations) == 3,
            "test_citations: 3 citations for 3 packets")
    _assert(citations[0]["citation_index"] == 1,
            "test_citations: first citation index = 1",
            f"got {citations[0]['citation_index']}")
    _assert(citations[2]["citation_index"] == 3,
            "test_citations: third citation index = 3",
            f"got {citations[2]['citation_index']}")
    _assert(citations[0]["source_title"] == "Alpha Source",
            "test_citations: source_title preserved",
            f"got {citations[0]['source_title']!r}")
    _assert("similarity_score" in citations[0],
            "test_citations: similarity_score present")
    _assert("chunk_id" in citations[0],
            "test_citations: chunk_id present")


def test_prompt_contains_evidence() -> None:
    """Test 9 — build_prompt includes evidence text and [Source N] markers."""
    from runtime.source_intelligence.output.prompt_builder import build_prompt

    evidence = _make_ok_evidence_result(n_packets=2, query_text="What is basis risk?")
    task_spec = {"output_type": "qa_answer", "query_text": "What is basis risk?"}

    prompt = build_prompt(evidence, task_spec)

    _assert("[Source 1]" in prompt,
            "test_prompt: [Source 1] in prompt")
    _assert("[Source 2]" in prompt,
            "test_prompt: [Source 2] in prompt")
    _assert("[Source 3]" not in prompt,
            "test_prompt: no spurious [Source 3]")
    _assert("basis risk" in prompt,
            "test_prompt: query_text in prompt")
    _assert("=== SOURCE EVIDENCE ===" in prompt,
            "test_prompt: source evidence section present")
    _assert("=== TASK ===" in prompt,
            "test_prompt: task section present")
    _assert("knowledge class: synthesized" in prompt,
            "test_prompt: knowledge class annotation in prompt")


def test_idea_generation_endorsement() -> None:
    """Test 10 — idea_generation output has endorsement_status=unendorsed."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result(n_packets=2)
    task_spec = {
        "output_type":  "idea_generation",
        "query_text":   "Propose a novel hypothesis about funding rate patterns",
        "instructions": "Be speculative",
    }

    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    # Pass 6B: "idea_generation" is now an alias resolving to "idea_generation_draft"
    _assert(result["output_type"] == "idea_generation_draft",
            "test_idea_gen: output_type=idea_generation_draft (alias resolved)",
            f"got {result['output_type']!r}")
    _assert(result["knowledge_class"] == "generated-ideas",
            "test_idea_gen: knowledge_class=generated-ideas",
            f"got {result['knowledge_class']!r}")
    _assert(result["endorsement_status"] == "unendorsed",
            "test_idea_gen: endorsement_status=unendorsed",
            f"got {result['endorsement_status']!r}")


def test_get_generation_adapter_no_key() -> None:
    """Test 11 — get_generation_adapter returns stub when ANTHROPIC_API_KEY absent."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        get_generation_adapter,
    )

    # Temporarily remove the API key if set
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        adapter = get_generation_adapter(provider_name=None)
        _assert(isinstance(adapter, StubGenerationAdapter),
                "test_get_adapter_no_key: returns StubGenerationAdapter",
                f"got {type(adapter).__name__}")
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_all_output_types_run() -> None:
    """Test 12 — all valid output types run without exception using stub adapter."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )
    from runtime.source_intelligence.output.prompt_builder import VALID_OUTPUT_TYPES

    adapter = StubGenerationAdapter()
    all_ok = True

    for otype in sorted(VALID_OUTPUT_TYPES):
        evidence = _make_ok_evidence_result(n_packets=2)
        task_spec = {"output_type": otype, "query_text": f"test query for {otype}"}
        try:
            result = generate_output(evidence, task_spec, adapter=adapter)
            if result["generation_status"] not in ("ok-stub", "ok"):
                _fail(f"test_all_types: {otype}",
                      f"status={result['generation_status']!r}")
                all_ok = False
        except Exception as exc:  # noqa: BLE001
            _fail(f"test_all_types: {otype}", f"raised {exc}")
            all_ok = False

    if all_ok:
        _ok(f"test_all_output_types_run: all {len(VALID_OUTPUT_TYPES)} types ok")


def test_warnings_forwarded() -> None:
    """Test 13 — retrieval warnings are forwarded into the generation result."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result(n_packets=2)
    evidence["retrieval_status"] = "ok-partial"
    evidence["warnings"] = ["Source X skipped: sidecar missing."]

    task_spec = {"output_type": "qa_answer"}
    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    _assert(any("sidecar" in w for w in result["warnings"]),
            "test_warnings_forwarded: retrieval warning forwarded",
            f"got warnings: {result['warnings']}")


def test_query_text_precedence() -> None:
    """Test 14 — task_spec query_text overrides evidence_result query_text."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result(query_text="original query from retrieval")
    task_spec = {
        "output_type": "qa_answer",
        "query_text":  "overriding query from task_spec",
    }

    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    _assert(result["query_text"] == "overriding query from task_spec",
            "test_query_precedence: task_spec query_text wins",
            f"got {result['query_text']!r}")


# ── End-to-end test against live workspace (optional) ─────────────────────────


def test_e2e_live_workspace() -> None:
    """Test 15 (E2E) — run full pipeline against live phase7-test workspace if available."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )
    from runtime.source_intelligence.retrieval.retriever import query_workspace

    evidence = query_workspace(
        workspace_id="phase7-test",
        query_text="What are the key takeaways about funding rates and market microstructure?",
        top_k=3,
    )

    if evidence["retrieval_status"] not in ("ok", "ok-partial", "ok-stale"):
        print(f"  [SKIP] test_e2e_live: workspace unavailable "
              f"(status={evidence['retrieval_status']!r})")
        return

    task_spec = {
        "output_type": "qa_answer",
        "query_text":  "What are the key takeaways about funding rates?",
    }

    result = generate_output(
        evidence_result=evidence,
        task_spec=task_spec,
        adapter=StubGenerationAdapter(),
    )

    _assert(result["generation_status"] in ("ok", "ok-stub"),
            "test_e2e_live: generation_status ok/ok-stub",
            f"got {result['generation_status']!r}")
    _assert(result["evidence_count"] > 0,
            "test_e2e_live: evidence_count > 0",
            f"got {result['evidence_count']}")
    _assert(len(result["citations"]) == result["evidence_count"],
            "test_e2e_live: citations match evidence_count")
    _assert(result["workspace_id"] == "phase7-test",
            "test_e2e_live: workspace_id preserved")

    # Confirm no vault writes occurred — output is data only
    _assert(result.get("vault_writeback_candidate") is not None,
            "test_e2e_live: vault_writeback_candidate field present")


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    print()
    print("SIC Phase 7 Pass 6 — Output Generation Layer Tests")
    print("=" * 55)
    print()

    tests = [
        test_stub_adapter_basic,
        test_no_evidence_packets,
        test_retrieval_failed_status,
        test_invalid_output_type,
        test_missing_output_type,
        test_knowledge_class_mapping,
        test_vault_writeback_candidate_logic,
        test_citation_structure,
        test_prompt_contains_evidence,
        test_idea_generation_endorsement,
        test_get_generation_adapter_no_key,
        test_all_output_types_run,
        test_warnings_forwarded,
        test_query_text_precedence,
        test_e2e_live_workspace,
    ]

    for test_fn in tests:
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001
            _fail(test_fn.__name__, f"raised unexpectedly: {exc}")

    print()
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    print()

    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()
