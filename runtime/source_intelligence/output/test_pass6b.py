"""
test_pass6b.py -- SIC Phase 7 Pass 6B
Tests for workspace-local output persistence and output type contract alignment.

Run from vault root:
    python -m runtime.source_intelligence.output.test_pass6b

Test coverage:
    1.  output_store.save_output() writes file + updates workspace.json
    2.  output_store.list_outputs() returns lightweight refs
    3.  output_store.load_output() by filename
    4.  output_store.load_output() by output_id UUID
    5.  output_store.load_output() error on missing workspace
    6.  generate_and_persist() persists to disk and returns persist fields
    7.  generate_and_persist() persisted file contains full output object
    8.  workspace.json outputs[] ref is lightweight (no generated_text body)
    9.  idea_generation_draft: endorsement_status=unendorsed, knowledge_class=generated-ideas
    10. alias: comparison -> comparison_note resolves correctly
    11. alias: idea_generation -> idea_generation_draft resolves correctly
    12. retrieval-failed status skips persistence
    13. multiple canonical types (briefing, source_summary, comparison_note)
    14. no vault writeback -- 02_KNOWLEDGE/ not touched
    15. weak evidence count warning on low-evidence types
    16. Live E2E: query phase7-test, generate 3 types with persist, inspect one
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

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

_VAULT_ROOT = Path(__file__).resolve().parents[3]
_SIC_WORKSPACES = _VAULT_ROOT / "runtime" / "source_intelligence" / "workspaces"
_TEST_WS_ID = "pass6b-test-tmp"
_LIVE_WS_ID = "phase7-test"


def _make_evidence_packet(
    n: int = 1,
    source_title: str = "Test Source",
    chunk_text: str = "Funding rates in perpetual futures markets represent the cost of carry.",
) -> dict:
    return {
        "workspace_id":          _TEST_WS_ID,
        "source_package_id":     f"pkg-{n:04d}",
        "source_title":          source_title,
        "source_slug":           source_title.lower().replace(" ", "-"),
        "source_path":           f"/fake/path/pkg-{n:04d}.json",
        "source_type":           "research-digest",
        "chunk_id":              f"pkg-{n:04d}_c0000",
        "chunk_index":           0,
        "similarity_score":      round(0.92 - n * 0.04, 4),
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
    workspace_id: str = _TEST_WS_ID,
    query_text: str = "What are crypto perpetual funding rates?",
) -> dict:
    packets = [_make_evidence_packet(i + 1) for i in range(n_packets)]
    return {
        "workspace_id":            workspace_id,
        "query_text":              query_text,
        "retrieval_status":        "ok",
        "provider_name":           "local_stub",
        "model_name":              "local-test-embedding-v1",
        "source_count_considered": n_packets,
        "chunk_count_considered":  n_packets,
        "top_k":                   5,
        "result_count":            n_packets,
        "evidence_packets":        packets,
        "warnings":                [],
        "errors":                  [],
    }


def _make_generation_result_stub(
    output_type: str = "briefing",
    workspace_id: str = _TEST_WS_ID,
) -> dict:
    """Minimal generation_result dict sufficient for save_output()."""
    return {
        "workspace_id":          workspace_id,
        "query_text":            "What are crypto perpetual funding rates?",
        "output_type":           output_type,
        "output_type_raw":       output_type,
        "knowledge_class":       "synthesized",
        "endorsement_status":    None,
        "generated_text":        "[STUB OUTPUT] placeholder text for testing",
        "evidence_packets":      [_make_evidence_packet(1), _make_evidence_packet(2)],
        "evidence_count":        2,
        "citations":             [
            {"citation_index": 1, "source_title": "Test Source", "source_package_id": "pkg-0001",
             "source_type": "research-digest", "chunk_id": "pkg-0001_c0000", "chunk_index": 0,
             "section_heading": "Section 1", "similarity_score": 0.88, "char_count": 64},
        ],
        "provider_name":         "local_stub",
        "model_name":            "stub-generation-v1",
        "token_count":           None,
        "generation_status":     "ok-stub",
        "vault_writeback_candidate": False,
        "writeback_path_hint":   None,
        "warnings":              [],
        "errors":                [],
    }


def _setup_tmp_workspace(ws_id: str = _TEST_WS_ID) -> Path:
    """Create a minimal workspace.json for a temporary test workspace."""
    ws_dir = _SIC_WORKSPACES / ws_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    ws_json = {
        "id":           str(uuid.uuid4()),
        "slug":         ws_id,
        "name":         "Pass 6B Temp Test Workspace",
        "status":       "active",
        "outputs":      [],
        "output_count": 0,
    }
    (ws_dir / "workspace.json").write_text(
        json.dumps(ws_json, indent=2), encoding="utf-8"
    )
    return ws_dir


def _teardown_tmp_workspace(ws_id: str = _TEST_WS_ID) -> None:
    ws_dir = _SIC_WORKSPACES / ws_id
    if ws_dir.exists():
        shutil.rmtree(ws_dir)


# ── Unit tests: output_store ───────────────────────────────────────────────────


def test_save_output_writes_file() -> None:
    """Test 1 -- save_output writes JSON file and returns success."""
    from runtime.source_intelligence.output import output_store

    _setup_tmp_workspace()
    try:
        gen_result = _make_generation_result_stub("briefing")
        result = output_store.save_output(_TEST_WS_ID, gen_result)

        _assert(result["success"] is True,
                "t01_save: success=True",
                f"got {result}")
        _assert(result["output_id"] is not None,
                "t01_save: output_id set")
        _assert(result["output_path"] is not None,
                "t01_save: output_path set")
        _assert(result["output_filename"] is not None,
                "t01_save: output_filename set")
        _assert(result["error"] is None,
                "t01_save: no error",
                f"got {result['error']}")

        # File must actually exist on disk
        out_path = Path(result["output_path"])
        _assert(out_path.exists(),
                "t01_save: file exists on disk",
                f"missing: {out_path}")

        # File must be valid JSON with output_id
        if out_path.exists():
            data = json.loads(out_path.read_text(encoding="utf-8"))
            _assert(data.get("output_id") == result["output_id"],
                    "t01_save: file output_id matches result")
            _assert(data.get("output_type") == "briefing",
                    "t01_save: file output_type correct",
                    f"got {data.get('output_type')!r}")
            _assert(data.get("status") == "intermediate",
                    "t01_save: status=intermediate",
                    f"got {data.get('status')!r}")
    finally:
        _teardown_tmp_workspace()


def test_save_output_updates_workspace_json() -> None:
    """Test 2 -- save_output records lightweight ref in workspace.json outputs[]."""
    from runtime.source_intelligence.output import output_store

    _setup_tmp_workspace()
    try:
        gen_result = _make_generation_result_stub("briefing")
        result = output_store.save_output(_TEST_WS_ID, gen_result)

        ws_path = _SIC_WORKSPACES / _TEST_WS_ID / "workspace.json"
        workspace = json.loads(ws_path.read_text(encoding="utf-8"))

        _assert(len(workspace.get("outputs", [])) == 1,
                "t02_ws_update: 1 ref in outputs[]",
                f"got {len(workspace.get('outputs', []))}")
        _assert(workspace.get("output_count") == 1,
                "t02_ws_update: output_count=1",
                f"got {workspace.get('output_count')}")

        ref = workspace["outputs"][0]
        _assert(ref.get("output_id") == result["output_id"],
                "t02_ws_update: ref output_id matches")
        _assert(ref.get("output_type") == "briefing",
                "t02_ws_update: ref output_type correct")
        _assert(ref.get("output_filename") == result["output_filename"],
                "t02_ws_update: ref filename matches")

        # Ref must NOT contain generated_text (lightweight only)
        _assert("generated_text" not in ref,
                "t02_ws_update: ref does not contain generated_text body")
    finally:
        _teardown_tmp_workspace()


def test_list_outputs() -> None:
    """Test 3 -- list_outputs returns refs from workspace.json."""
    from runtime.source_intelligence.output import output_store

    _setup_tmp_workspace()
    try:
        # Save two outputs
        output_store.save_output(_TEST_WS_ID, _make_generation_result_stub("briefing"))
        output_store.save_output(_TEST_WS_ID, _make_generation_result_stub("faq"))

        result = output_store.list_outputs(_TEST_WS_ID)

        _assert(result["success"] is True,
                "t03_list: success=True",
                f"got {result}")
        _assert(result["output_count"] == 2,
                "t03_list: output_count=2",
                f"got {result['output_count']}")
        _assert(len(result["outputs"]) == 2,
                "t03_list: 2 outputs in list",
                f"got {len(result['outputs'])}")

        types = {r["output_type"] for r in result["outputs"]}
        _assert("briefing" in types and "faq" in types,
                "t03_list: both output types present",
                f"got {types}")
    finally:
        _teardown_tmp_workspace()


def test_load_output_by_filename() -> None:
    """Test 4 -- load_output resolves by filename."""
    from runtime.source_intelligence.output import output_store

    _setup_tmp_workspace()
    try:
        gen_result = _make_generation_result_stub("study_guide")
        save_result = output_store.save_output(_TEST_WS_ID, gen_result)
        filename = save_result["output_filename"]

        load_result = output_store.load_output(_TEST_WS_ID, filename)

        _assert(load_result["success"] is True,
                "t04_load_filename: success=True",
                f"got {load_result}")
        _assert(load_result["output"] is not None,
                "t04_load_filename: output not None")
        _assert(load_result["output"].get("output_id") == save_result["output_id"],
                "t04_load_filename: output_id matches")
        _assert(load_result["output"].get("output_type") == "study_guide",
                "t04_load_filename: output_type correct")

        # Full body must include generated_text
        _assert("generated_text" in load_result["output"],
                "t04_load_filename: has generated_text")
        _assert("citations" in load_result["output"],
                "t04_load_filename: has citations")
        _assert("evidence_packet_refs" in load_result["output"],
                "t04_load_filename: has evidence_packet_refs")
    finally:
        _teardown_tmp_workspace()


def test_load_output_by_id() -> None:
    """Test 5 -- load_output resolves by output_id UUID."""
    from runtime.source_intelligence.output import output_store

    _setup_tmp_workspace()
    try:
        gen_result = _make_generation_result_stub("comparison_note")
        save_result = output_store.save_output(_TEST_WS_ID, gen_result)
        output_id = save_result["output_id"]

        load_result = output_store.load_output(_TEST_WS_ID, output_id)

        _assert(load_result["success"] is True,
                "t05_load_id: success=True",
                f"got {load_result}")
        _assert(load_result["output"].get("output_id") == output_id,
                "t05_load_id: output_id matches")
        _assert(load_result["output"].get("output_type") == "comparison_note",
                "t05_load_id: output_type correct")
    finally:
        _teardown_tmp_workspace()


def test_load_output_missing_workspace() -> None:
    """Test 6 -- load_output returns error for missing workspace."""
    from runtime.source_intelligence.output import output_store

    result = output_store.load_output("does-not-exist-xyz", "some-file.json")

    _assert(result["success"] is False,
            "t06_load_missing: success=False")
    _assert(result["error"] is not None,
            "t06_load_missing: error set")
    _assert(result["output"] is None,
            "t06_load_missing: output is None")


def test_save_output_rejected_for_failed_status() -> None:
    """Test 7 -- save_output rejects generation_status != ok / ok-stub."""
    from runtime.source_intelligence.output import output_store

    _setup_tmp_workspace()
    try:
        gen_result = _make_generation_result_stub("briefing")
        gen_result["generation_status"] = "retrieval-failed"

        result = output_store.save_output(_TEST_WS_ID, gen_result)

        _assert(result["success"] is False,
                "t07_reject_failed: success=False",
                f"got {result}")
        _assert(result["error"] is not None,
                "t07_reject_failed: error describes reason")
    finally:
        _teardown_tmp_workspace()


# ── Unit tests: generate_and_persist ──────────────────────────────────────────


def test_generate_and_persist_returns_persist_fields() -> None:
    """Test 8 -- generate_and_persist augments result with persist fields."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_and_persist,
    )

    _setup_tmp_workspace()
    try:
        evidence = _make_ok_evidence_result(n_packets=3, workspace_id=_TEST_WS_ID)
        task_spec = {"output_type": "briefing", "query_text": "What are funding rates?"}

        result = generate_and_persist(
            workspace_id=_TEST_WS_ID,
            evidence_result=evidence,
            task_spec=task_spec,
            adapter=StubGenerationAdapter(),
        )

        _assert("persisted" in result,
                "t08_persist_fields: persisted key present")
        _assert("persist_output_id" in result,
                "t08_persist_fields: persist_output_id key present")
        _assert("persist_path" in result,
                "t08_persist_fields: persist_path key present")
        _assert("persist_filename" in result,
                "t08_persist_fields: persist_filename key present")
        _assert("persist_error" in result,
                "t08_persist_fields: persist_error key present")

        # Stub generates ok-stub -- persistence should succeed
        _assert(result["persisted"] is True,
                "t08_persist_fields: persisted=True for ok-stub",
                f"generation_status={result['generation_status']!r}, "
                f"persist_error={result.get('persist_error')!r}")
        _assert(result["persist_output_id"] is not None,
                "t08_persist_fields: persist_output_id set")
        _assert(result["persist_filename"] is not None,
                "t08_persist_fields: persist_filename set")
    finally:
        _teardown_tmp_workspace()


def test_generate_and_persist_file_on_disk() -> None:
    """Test 9 -- generate_and_persist writes actual file to workspace outputs/."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_and_persist,
    )

    _setup_tmp_workspace()
    try:
        evidence = _make_ok_evidence_result(n_packets=3, workspace_id=_TEST_WS_ID)
        task_spec = {"output_type": "faq", "query_text": "Explain funding rate mechanisms"}

        result = generate_and_persist(
            workspace_id=_TEST_WS_ID,
            evidence_result=evidence,
            task_spec=task_spec,
            adapter=StubGenerationAdapter(),
        )

        out_path = Path(result.get("persist_path", ""))
        _assert(out_path.exists(),
                "t09_file_on_disk: file exists at persist_path",
                f"path: {out_path}")

        if out_path.exists():
            outputs_dir = out_path.parent
            _assert(outputs_dir.name == "outputs",
                    "t09_file_on_disk: file is inside outputs/ dir",
                    f"parent: {outputs_dir.name}")
            data = json.loads(out_path.read_text(encoding="utf-8"))
            _assert(data.get("output_type") == "faq",
                    "t09_file_on_disk: persisted file has correct output_type")
            _assert("generated_text" in data,
                    "t09_file_on_disk: persisted file has generated_text")
            _assert("evidence_packet_refs" in data,
                    "t09_file_on_disk: persisted file has evidence_packet_refs")
            _assert("citations" in data,
                    "t09_file_on_disk: persisted file has citations")
    finally:
        _teardown_tmp_workspace()


def test_generate_and_persist_skipped_on_failed_generation() -> None:
    """Test 10 -- generate_and_persist skips persist when generation_status is not ok/ok-stub."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_and_persist,
    )

    _setup_tmp_workspace()
    try:
        # Evidence with retrieval failure
        evidence = _make_ok_evidence_result(workspace_id=_TEST_WS_ID)
        evidence["retrieval_status"] = "no-index"

        task_spec = {"output_type": "briefing"}
        result = generate_and_persist(
            workspace_id=_TEST_WS_ID,
            evidence_result=evidence,
            task_spec=task_spec,
            adapter=StubGenerationAdapter(),
        )

        _assert(result["persisted"] is False,
                "t10_skip_persist: persisted=False on retrieval-failed",
                f"generation_status={result['generation_status']!r}")
        _assert(result["persist_error"] is not None,
                "t10_skip_persist: persist_error explains skip",
                f"got None")
        _assert(result["persist_output_id"] is None,
                "t10_skip_persist: persist_output_id is None")
    finally:
        _teardown_tmp_workspace()


# ── Unit tests: output type contract and aliases ───────────────────────────────


def test_idea_generation_draft_non_canonical() -> None:
    """Test 11 -- idea_generation_draft outputs are non-canonical: unendorsed + generated-ideas."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result(n_packets=3)
    task_spec = {"output_type": "idea_generation_draft",
                 "query_text": "What novel trading strategies emerge from order flow data?"}

    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    _assert(result["generation_status"] == "ok-stub",
            "t11_idea_gen: status=ok-stub",
            f"got {result['generation_status']!r}")
    _assert(result["output_type"] == "idea_generation_draft",
            "t11_idea_gen: output_type=idea_generation_draft",
            f"got {result['output_type']!r}")
    _assert(result["knowledge_class"] == "generated-ideas",
            "t11_idea_gen: knowledge_class=generated-ideas",
            f"got {result['knowledge_class']!r}")
    _assert(result["endorsement_status"] == "unendorsed",
            "t11_idea_gen: endorsement_status=unendorsed",
            f"got {result['endorsement_status']!r}")
    # Stub outputs are never vault writeback candidates
    _assert(result["vault_writeback_candidate"] is False,
            "t11_idea_gen: vault_writeback_candidate=False for stub")


def test_alias_comparison_resolves_to_comparison_note() -> None:
    """Test 12 -- alias 'comparison' resolves to canonical 'comparison_note'."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result(n_packets=3)
    task_spec = {"output_type": "comparison",  # alias
                 "query_text": "Compare funding rate approaches"}

    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    _assert(result["generation_status"] == "ok-stub",
            "t12_alias_comparison: generation ok-stub",
            f"got {result['generation_status']!r}")
    _assert(result["output_type"] == "comparison_note",
            "t12_alias_comparison: output_type resolved to comparison_note",
            f"got {result['output_type']!r}")
    _assert(result["output_type_raw"] == "comparison",
            "t12_alias_comparison: output_type_raw preserves original",
            f"got {result['output_type_raw']!r}")
    _assert(result["knowledge_class"] == "synthesized",
            "t12_alias_comparison: knowledge_class=synthesized",
            f"got {result['knowledge_class']!r}")


def test_alias_idea_generation_resolves_to_draft() -> None:
    """Test 13 -- alias 'idea_generation' resolves to canonical 'idea_generation_draft'."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result(n_packets=2)
    task_spec = {"output_type": "idea_generation",  # alias
                 "query_text": "Generate ideas about market microstructure"}

    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    _assert(result["output_type"] == "idea_generation_draft",
            "t13_alias_idea_gen: resolved to idea_generation_draft",
            f"got {result['output_type']!r}")
    _assert(result["output_type_raw"] == "idea_generation",
            "t13_alias_idea_gen: raw preserves alias",
            f"got {result['output_type_raw']!r}")
    _assert(result["endorsement_status"] == "unendorsed",
            "t13_alias_idea_gen: endorsement_status=unendorsed (alias goes through same path)",
            f"got {result['endorsement_status']!r}")
    _assert(result["knowledge_class"] == "generated-ideas",
            "t13_alias_idea_gen: knowledge_class=generated-ideas",
            f"got {result['knowledge_class']!r}")


def test_source_summary_knowledge_class() -> None:
    """Test 14 -- source_summary outputs have knowledge_class=source-derived."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result(n_packets=2)
    task_spec = {"output_type": "source_summary",
                 "query_text": "Summarize this source"}

    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    _assert(result["knowledge_class"] == "source-derived",
            "t14_source_summary: knowledge_class=source-derived",
            f"got {result['knowledge_class']!r}")
    _assert(result["endorsement_status"] is None,
            "t14_source_summary: endorsement_status=None (canonical by default)",
            f"got {result['endorsement_status']!r}")


def test_low_evidence_count_warning() -> None:
    """Test 15 -- output types requiring 2+ packets warn when only 1 provided."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_output,
    )

    evidence = _make_ok_evidence_result(n_packets=1)  # only 1 packet
    task_spec = {"output_type": "briefing",  # requires min 2
                 "query_text": "Briefing on funding rates"}

    result = generate_output(evidence, task_spec, adapter=StubGenerationAdapter())

    _assert(result["generation_status"] == "ok-stub",
            "t15_low_evidence: still generates despite low count",
            f"got {result['generation_status']!r}")
    _assert(len(result["warnings"]) > 0,
            "t15_low_evidence: warning issued for low evidence count",
            "expected at least one warning")
    warning_text = " ".join(result["warnings"])
    _assert("recommends" in warning_text.lower() or "minimum" in warning_text.lower()
            or "at least" in warning_text.lower(),
            "t15_low_evidence: warning text describes the threshold issue",
            f"got: {warning_text!r}")


def test_multiple_canonical_types_persist() -> None:
    """Test 16 -- generate_and_persist works correctly for 3 different canonical types."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_and_persist,
    )
    from runtime.source_intelligence.output import output_store

    _setup_tmp_workspace()
    try:
        output_types = ["briefing", "source_summary", "comparison_note"]
        results = []

        for otype in output_types:
            evidence = _make_ok_evidence_result(n_packets=3, workspace_id=_TEST_WS_ID)
            task_spec = {
                "output_type": otype,
                "query_text": f"Test query for {otype}",
            }
            r = generate_and_persist(
                workspace_id=_TEST_WS_ID,
                evidence_result=evidence,
                task_spec=task_spec,
                adapter=StubGenerationAdapter(),
            )
            results.append(r)

        # All 3 persisted
        for otype, r in zip(output_types, results):
            _assert(r["persisted"] is True,
                    f"t16_multi_types: {otype} persisted=True",
                    f"generation_status={r['generation_status']!r}, "
                    f"persist_error={r.get('persist_error')!r}")

        # workspace.json shows 3 refs
        list_result = output_store.list_outputs(_TEST_WS_ID)
        _assert(list_result["output_count"] == 3,
                "t16_multi_types: workspace.json shows 3 outputs",
                f"got {list_result['output_count']}")

        stored_types = {ref["output_type"] for ref in list_result["outputs"]}
        for otype in output_types:
            _assert(otype in stored_types,
                    f"t16_multi_types: {otype} appears in workspace.json refs",
                    f"stored types: {stored_types}")
    finally:
        _teardown_tmp_workspace()


def test_no_vault_writeback() -> None:
    """Test 17 -- generate_and_persist does not modify any vault directories."""
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_and_persist,
    )

    _setup_tmp_workspace()
    try:
        vault_dirs_to_check = [
            _VAULT_ROOT / "02_KNOWLEDGE",
            _VAULT_ROOT / "01_PROJECTS",
            _VAULT_ROOT / "00_HOME",
        ]

        # Snapshot mtime for each vault dir before
        before_mtimes = {}
        for d in vault_dirs_to_check:
            if d.exists():
                before_mtimes[str(d)] = d.stat().st_mtime

        # Run generate_and_persist
        evidence = _make_ok_evidence_result(n_packets=3, workspace_id=_TEST_WS_ID)
        task_spec = {"output_type": "synthesis_draft", "query_text": "Synthesize themes"}
        generate_and_persist(
            workspace_id=_TEST_WS_ID,
            evidence_result=evidence,
            task_spec=task_spec,
            adapter=StubGenerationAdapter(),
        )

        # Verify vault dir mtimes unchanged
        for d in vault_dirs_to_check:
            if d.exists() and str(d) in before_mtimes:
                after_mtime = d.stat().st_mtime
                _assert(
                    after_mtime == before_mtimes[str(d)],
                    f"t17_no_vault_write: {d.name}/ not modified",
                    f"mtime changed: {before_mtimes[str(d)]} -> {after_mtime}",
                )
    finally:
        _teardown_tmp_workspace()


def test_output_stored_with_promotion_candidate_flag() -> None:
    """Test 18 -- persisted output stores promotion_candidate (advisory, never promoted)."""
    from runtime.source_intelligence.output import output_store

    _setup_tmp_workspace()
    try:
        # ok-stub never qualifies as promotion candidate -- test that field is present
        gen_result = _make_generation_result_stub("briefing")
        save_result = output_store.save_output(_TEST_WS_ID, gen_result)

        load_result = output_store.load_output(_TEST_WS_ID, save_result["output_id"])
        out = load_result["output"]

        _assert("promotion_candidate" in out,
                "t18_promotion_flag: promotion_candidate field present")
        _assert("promoted_path" in out,
                "t18_promotion_flag: promoted_path field present")
        _assert("promoted_at" in out,
                "t18_promotion_flag: promoted_at field present")
        _assert(out["promoted_path"] is None,
                "t18_promotion_flag: promoted_path=None (never promoted)")
        _assert(out["promoted_at"] is None,
                "t18_promotion_flag: promoted_at=None (never promoted)")
        _assert(out["status"] == "intermediate",
                "t18_promotion_flag: status=intermediate",
                f"got {out['status']!r}")
    finally:
        _teardown_tmp_workspace()


# ── Live E2E tests: phase7-test workspace ─────────────────────────────────────


def test_live_e2e_generate_and_persist_three_types() -> None:
    """
    Test 19 (Live E2E) -- Query phase7-test workspace and generate 3 canonical
    output types with persistence. Verify files written, refs updated, no vault
    writeback.

    This test runs against the real phase7-test workspace using stub embeddings
    and stub generation adapter. No API calls are made.
    """
    from runtime.source_intelligence.retrieval.retriever import query_workspace
    from runtime.source_intelligence.output.generator import (
        StubGenerationAdapter,
        generate_and_persist,
    )
    from runtime.source_intelligence.output import output_store

    if not (_SIC_WORKSPACES / _LIVE_WS_ID).exists():
        _fail("t19_live_e2e", f"workspace '{_LIVE_WS_ID}' not found -- skipping")
        return

    adapter = StubGenerationAdapter()
    output_types_to_test = ["briefing", "source_summary", "idea_generation_draft"]

    live_results = []
    for otype in output_types_to_test:
        evidence = query_workspace(
            workspace_id=_LIVE_WS_ID,
            query_text="What are funding rates in perpetual futures?",
            top_k=3,
        )

        _assert(evidence["retrieval_status"] in ("ok", "ok-partial", "ok-stale"),
                f"t19_live_e2e: retrieval ok for {otype}",
                f"got {evidence['retrieval_status']!r}")

        task_spec = {
            "output_type": otype,
            "query_text":  "What are funding rates in perpetual futures?",
        }

        result = generate_and_persist(
            workspace_id=_LIVE_WS_ID,
            evidence_result=evidence,
            task_spec=task_spec,
            adapter=adapter,
        )
        live_results.append((otype, result))

        _assert(result["generation_status"] == "ok-stub",
                f"t19_live_e2e: {otype} generation_status=ok-stub",
                f"got {result['generation_status']!r}")
        _assert(result["persisted"] is True,
                f"t19_live_e2e: {otype} persisted=True",
                f"persist_error={result.get('persist_error')!r}")
        _assert(Path(result["persist_path"]).exists(),
                f"t19_live_e2e: {otype} output file exists on disk")

    # idea_generation_draft must be non-canonical
    for otype, r in live_results:
        if otype == "idea_generation_draft":
            _assert(r["endorsement_status"] == "unendorsed",
                    "t19_live_e2e: idea_generation_draft endorsement_status=unendorsed",
                    f"got {r.get('endorsement_status')!r}")
            _assert(r["knowledge_class"] == "generated-ideas",
                    "t19_live_e2e: idea_generation_draft knowledge_class=generated-ideas",
                    f"got {r.get('knowledge_class')!r}")

    # Inspect one stored output by ID
    if live_results:
        first_otype, first_result = live_results[0]
        first_id = first_result["persist_output_id"]
        load_result = output_store.load_output(_LIVE_WS_ID, first_id)

        _assert(load_result["success"] is True,
                "t19_live_e2e: inspect by ID success=True",
                f"error: {load_result.get('error')!r}")
        _assert(load_result["output"].get("output_id") == first_id,
                "t19_live_e2e: inspect output_id matches")
        _assert(load_result["output"].get("output_type") == first_otype,
                "t19_live_e2e: inspect output_type matches",
                f"got {load_result['output'].get('output_type')!r}")
        _assert("generated_text" in load_result["output"],
                "t19_live_e2e: inspected output has generated_text")
        _assert("evidence_packet_refs" in load_result["output"],
                "t19_live_e2e: inspected output has evidence_packet_refs")

    # Confirm no vault promotion occurred -- 02_KNOWLEDGE not touched
    knowledge_dir = _VAULT_ROOT / "02_KNOWLEDGE"
    if knowledge_dir.exists():
        # Check that no new SIC output files appeared in 02_KNOWLEDGE
        sic_files_in_vault = list(knowledge_dir.rglob("*_briefing_*.json")) + \
                             list(knowledge_dir.rglob("*_source_summary_*.json")) + \
                             list(knowledge_dir.rglob("*_idea_generation_draft_*.json"))
        _assert(len(sic_files_in_vault) == 0,
                "t19_live_e2e: no SIC output files in 02_KNOWLEDGE",
                f"found: {[str(f) for f in sic_files_in_vault]}")

    # Clean up the outputs we added to phase7-test to leave it clean
    outputs_dir = _SIC_WORKSPACES / _LIVE_WS_ID / "outputs"
    if outputs_dir.exists():
        for result_file in [r["persist_path"] for _, r in live_results
                            if r.get("persist_path")]:
            p = Path(result_file)
            if p.exists():
                p.unlink()

    # Rebuild workspace.json outputs[] to remove our test entries
    ws_path = _SIC_WORKSPACES / _LIVE_WS_ID / "workspace.json"
    if ws_path.exists():
        workspace = json.loads(ws_path.read_text(encoding="utf-8"))
        live_ids = {r["persist_output_id"] for _, r in live_results
                    if r.get("persist_output_id")}
        original_outputs = [
            ref for ref in workspace.get("outputs", [])
            if ref.get("output_id") not in live_ids
        ]
        workspace["outputs"] = original_outputs
        workspace["output_count"] = len(original_outputs)
        ws_path.write_text(json.dumps(workspace, indent=2, ensure_ascii=False),
                           encoding="utf-8")

    _ok("t19_live_e2e: phase7-test outputs cleaned up after test")


# ── Test runner ────────────────────────────────────────────────────────────────


def run_all() -> None:
    tests = [
        ("Unit: output_store.save_output writes file", test_save_output_writes_file),
        ("Unit: save_output updates workspace.json", test_save_output_updates_workspace_json),
        ("Unit: list_outputs returns refs", test_list_outputs),
        ("Unit: load_output by filename", test_load_output_by_filename),
        ("Unit: load_output by ID", test_load_output_by_id),
        ("Unit: load_output error on missing workspace", test_load_output_missing_workspace),
        ("Unit: save_output rejects failed status", test_save_output_rejected_for_failed_status),
        ("Unit: generate_and_persist returns persist fields", test_generate_and_persist_returns_persist_fields),
        ("Unit: generate_and_persist writes file on disk", test_generate_and_persist_file_on_disk),
        ("Unit: generate_and_persist skips on failed gen", test_generate_and_persist_skipped_on_failed_generation),
        ("Unit: idea_generation_draft non-canonical", test_idea_generation_draft_non_canonical),
        ("Unit: alias comparison -> comparison_note", test_alias_comparison_resolves_to_comparison_note),
        ("Unit: alias idea_generation -> idea_generation_draft", test_alias_idea_generation_resolves_to_draft),
        ("Unit: source_summary knowledge_class=source-derived", test_source_summary_knowledge_class),
        ("Unit: low evidence count warning", test_low_evidence_count_warning),
        ("Unit: multiple canonical types persist correctly", test_multiple_canonical_types_persist),
        ("Unit: no vault writeback on generate_and_persist", test_no_vault_writeback),
        ("Unit: persisted output has promotion_candidate fields", test_output_stored_with_promotion_candidate_flag),
        ("Live E2E: 3 types on phase7-test + inspect + cleanup", test_live_e2e_generate_and_persist_three_types),
    ]

    print()
    print("=" * 70)
    print("SIC Phase 7 Pass 6B -- Output Persistence + Contract Alignment Tests")
    print("=" * 70)
    print()

    for section_name, fn in tests:
        print(f"-- {section_name}")
        try:
            fn()
        except Exception as exc:
            _fail(section_name, f"EXCEPTION: {exc}")
        print()

    print("=" * 70)
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    print("=" * 70)
    print()

    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    run_all()
