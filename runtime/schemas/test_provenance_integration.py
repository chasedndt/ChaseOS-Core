"""Integration tests for Phase 9 provenance substrate (Feature 11)."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_VAULT_ROOT = _HERE.parents[2]
if str(_VAULT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VAULT_ROOT))

from runtime.schemas.provenance_block import (  # noqa: E402
    ProvenanceBlock,
    append_lineage_step,
    make_from_sidecar,
    make_from_source_package,
    make_minimal,
    upgrade_verification_status,
)
from runtime.schemas.provenance_validator import is_valid_provenance_block  # noqa: E402
from runtime.schemas.promotion_check import (  # noqa: E402
    check_promotion_minimum,
    get_promotion_provenance_tier,
)


# ── make_minimal ──────────────────────────────────────────────────────────────


def test_make_minimal_returns_valid_block() -> None:
    block = make_minimal(
        source_ids=["cap-001"],
        stage="raw_capture",
        ref="03_INPUTS/00_QUARANTINE/source/test.md",
        audit_ref="07_LOGS/Agent-Activity/test.md",
        timestamp="2026-04-25T10:00:00+00:00",
    )
    assert isinstance(block, ProvenanceBlock)
    assert block.is_valid()
    assert block.validation_errors() == []


def test_make_minimal_uses_provided_timestamp() -> None:
    ts = "2026-04-25T09:00:00+00:00"
    block = make_minimal(["s1"], "quarantine", "path/file.md", "audit/ref.md", timestamp=ts)
    assert block.created_at == ts
    assert block.last_modified_at == ts
    assert block.lineage_chain[0]["timestamp"] == ts


def test_make_minimal_invalid_stage_raises() -> None:
    import pytest
    with pytest.raises(ValueError, match="Invalid stage"):
        make_minimal(["s1"], "not_a_stage", "ref", "audit")


def test_make_minimal_empty_source_ids_raises() -> None:
    import pytest
    with pytest.raises(ValueError, match="source_ids"):
        make_minimal([], "raw_capture", "ref", "audit")


def test_make_minimal_output_passes_validator() -> None:
    block = make_minimal(["x-001"], "promoted", "02_KNOWLEDGE/test.md", "07_LOGS/build.md")
    assert is_valid_provenance_block(block.to_dict())


# ── make_from_sidecar ─────────────────────────────────────────────────────────


def test_make_from_sidecar_happy_path() -> None:
    sidecar = {
        "capture_id": "uuid-abc-123",
        "content_sha256": "deadbeef" * 8,
        "captured_at": "2026-04-25T08:00:00+00:00",
    }
    block = make_from_sidecar(sidecar, "03_INPUTS/00_QUARANTINE/source/test.md")
    assert block.is_valid()
    assert "uuid-abc-123" in block.source_ids
    assert block.processing_stage == "raw_capture"
    assert block.verification_status == "unverified"
    assert block.operator_reviewed_at is None


def test_make_from_sidecar_sha256_added_as_second_source_id() -> None:
    sidecar = {
        "capture_id": "my-capture-id",
        "content_sha256": "abc123",
    }
    block = make_from_sidecar(sidecar, "path/file.md")
    assert "sha256:abc123" in block.source_ids


def test_make_from_sidecar_with_audit_ref() -> None:
    sidecar = {"capture_id": "cap-x", "captured_at": "2026-04-25T00:00:00Z"}
    block = make_from_sidecar(sidecar, "path/file.md", audit_ref="07_LOGS/act.md")
    assert "07_LOGS/act.md" in block.audit_refs


def test_make_from_sidecar_minimal_sidecar_still_valid() -> None:
    block = make_from_sidecar({}, "03_INPUTS/00_QUARANTINE/source/bare.md")
    assert block.is_valid()
    assert block.source_ids[0] == "unknown-capture"


# ── make_from_source_package ──────────────────────────────────────────────────


_SIC_PACKAGE: dict = {
    "id": "spkg-phase9-test-001",
    "origin_path": "03_INPUTS/00_QUARANTINE/source/report.md",
    "created_at": "2026-04-25T06:00:00+00:00",
    "updated_at": "2026-04-25T06:05:00+00:00",
    "_builder_meta": {
        "content_hash_sha256": "cafebabe" * 8,
    },
}


def test_make_from_source_package_happy_path() -> None:
    block = make_from_source_package(_SIC_PACKAGE)
    assert block.is_valid()
    assert "spkg-phase9-test-001" in block.source_ids
    assert block.processing_stage == "source_package"


def test_make_from_source_package_content_hash_in_source_ids() -> None:
    block = make_from_source_package(_SIC_PACKAGE)
    assert any("sha256:" in sid for sid in block.source_ids)


def test_make_from_source_package_lineage_has_raw_capture_and_package() -> None:
    block = make_from_source_package(_SIC_PACKAGE)
    stages = [e["stage"] for e in block.lineage_chain]
    assert "raw_capture" in stages
    assert "source_package" in stages


def test_make_from_source_package_without_origin_path() -> None:
    pkg = {**_SIC_PACKAGE, "origin_path": ""}
    block = make_from_source_package(pkg)
    # Valid block still built; source_refs and raw_capture step are absent
    assert block.is_valid()
    assert block.source_refs == []


def test_make_from_source_package_output_validates() -> None:
    block = make_from_source_package(_SIC_PACKAGE, audit_ref="07_LOGS/build.md")
    assert is_valid_provenance_block(block.to_dict())


# ── ProvenanceBlock round-trip ────────────────────────────────────────────────


def test_from_dict_roundtrip() -> None:
    block = make_from_source_package(_SIC_PACKAGE)
    d = block.to_dict()
    restored = ProvenanceBlock.from_dict(d)
    assert restored.to_dict() == d


# ── append_lineage_step ───────────────────────────────────────────────────────


def test_append_lineage_step_adds_entry() -> None:
    block = make_minimal(["s1"], "raw_capture", "path/f.md", "audit/a.md").to_dict()
    updated = append_lineage_step(block, "promoted", "02_KNOWLEDGE/note.md")
    assert len(updated["lineage_chain"]) == len(block["lineage_chain"]) + 1
    assert updated["lineage_chain"][-1]["stage"] == "promoted"


def test_append_lineage_step_does_not_mutate_original() -> None:
    block = make_minimal(["s1"], "raw_capture", "path/f.md", "audit/a.md").to_dict()
    original_len = len(block["lineage_chain"])
    append_lineage_step(block, "promoted", "02_KNOWLEDGE/note.md")
    assert len(block["lineage_chain"]) == original_len


def test_append_lineage_step_invalid_stage_raises() -> None:
    import pytest
    block = make_minimal(["s1"], "raw_capture", "path/f.md", "audit/a.md").to_dict()
    with pytest.raises(ValueError, match="Invalid stage"):
        append_lineage_step(block, "invented_stage", "ref")


def test_append_lineage_step_result_still_valid() -> None:
    block = make_minimal(["s1"], "raw_capture", "path/f.md", "audit/a.md").to_dict()
    updated = append_lineage_step(block, "source_package", "runtime/si/ws/spkg.json")
    assert is_valid_provenance_block(updated)


# ── upgrade_verification_status ───────────────────────────────────────────────


def test_upgrade_verification_unverified_to_reviewed() -> None:
    block = make_minimal(["s1"], "raw_capture", "f.md", "a.md").to_dict()
    updated = upgrade_verification_status(block, "operator_reviewed")
    assert updated["verification_status"] == "operator_reviewed"
    assert updated["operator_reviewed_at"] is not None


def test_upgrade_verification_skip_levels_allowed() -> None:
    block = make_minimal(["s1"], "raw_capture", "f.md", "a.md").to_dict()
    updated = upgrade_verification_status(block, "verified")
    assert updated["verification_status"] == "verified"


def test_upgrade_verification_same_level_noop() -> None:
    block = make_minimal(["s1"], "raw_capture", "f.md", "a.md").to_dict()
    updated = upgrade_verification_status(block, "unverified")
    assert updated["verification_status"] == "unverified"


def test_upgrade_verification_downgrade_blocked() -> None:
    import pytest
    block = make_minimal(["s1"], "raw_capture", "f.md", "a.md").to_dict()
    block["verification_status"] = "verified"
    with pytest.raises(ValueError, match="downgrade"):
        upgrade_verification_status(block, "unverified")


def test_upgrade_verification_does_not_mutate_original() -> None:
    block = make_minimal(["s1"], "raw_capture", "f.md", "a.md").to_dict()
    upgrade_verification_status(block, "operator_reviewed")
    assert block["verification_status"] == "unverified"


# ── check_promotion_minimum ───────────────────────────────────────────────────


def test_promotion_check_passes_with_minimum_fields() -> None:
    fm = {"verification_status": "operator_reviewed", "source_package_id": "spkg-001"}
    passes, errors = check_promotion_minimum(fm)
    assert passes
    assert errors == []


def test_promotion_check_passes_with_promoted_from() -> None:
    fm = {"verification_status": "reviewed", "promoted_from": "03_INPUTS/..."}
    passes, errors = check_promotion_minimum(fm)
    assert passes


def test_promotion_check_passes_with_provenance_block() -> None:
    fm = {"verification_status": "verified", "provenance": {"source_ids": ["x"]}}
    passes, errors = check_promotion_minimum(fm)
    assert passes


def test_promotion_check_fails_missing_verification_status() -> None:
    fm = {"source_package_id": "spkg-001"}
    passes, errors = check_promotion_minimum(fm)
    assert not passes
    assert any("verification_status" in e for e in errors)


def test_promotion_check_fails_missing_anchor() -> None:
    fm = {"verification_status": "unverified"}
    passes, errors = check_promotion_minimum(fm)
    assert not passes
    assert any("anchor" in e for e in errors)


def test_promotion_check_fails_empty_frontmatter() -> None:
    passes, errors = check_promotion_minimum({})
    assert not passes
    assert len(errors) == 2


# ── get_promotion_provenance_tier ─────────────────────────────────────────────


def test_tier_full_when_provenance_key_present() -> None:
    fm = {"verification_status": "verified", "provenance": {}}
    assert get_promotion_provenance_tier(fm) == "full"


def test_tier_partial_with_status_and_source_package_id() -> None:
    fm = {"verification_status": "reviewed", "source_package_id": "spkg-x"}
    assert get_promotion_provenance_tier(fm) == "partial"


def test_tier_minimal_with_status_only() -> None:
    fm = {"verification_status": "unverified"}
    assert get_promotion_provenance_tier(fm) == "minimal"


def test_tier_absent_with_nothing() -> None:
    assert get_promotion_provenance_tier({}) == "absent"


# ── SIC source package integration ───────────────────────────────────────────


def test_sic_ingest_source_adds_provenance_block(tmp_path: Path) -> None:
    """End-to-end: ingest_source() emits a package with a valid provenance block."""
    from runtime.source_intelligence.pipelines.source_package_builder import ingest_source

    content = "Market update: BTC tested support. Volume elevated.\n" * 10
    src = tmp_path / "market_note.md"
    src.write_text(content, encoding="utf-8")

    ws_dir = tmp_path / "workspaces"
    ws_dir.mkdir()

    import unittest.mock as mock
    with mock.patch(
        "runtime.source_intelligence.pipelines.source_package_builder._SIC_WORKSPACES",
        ws_dir,
    ):
        result = ingest_source(
            input_path=src,
            workspace_id="test-ws",
            domain="TradingSystems",
        )

    assert result["success"], result.get("error")
    package = result["package"]
    assert "provenance" in package, "SIC package must carry a provenance block"
    prov = package["provenance"]
    assert is_valid_provenance_block(prov), f"Provenance block invalid: {prov}"
    assert result["source_package_id"] in prov["source_ids"]
