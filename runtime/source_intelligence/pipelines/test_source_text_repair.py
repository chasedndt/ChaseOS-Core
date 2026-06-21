from __future__ import annotations

import json
from pathlib import Path

from runtime.source_intelligence.pipelines import source_package_builder
from runtime.source_intelligence.pipelines.normalization import normalize_text_with_metadata


def test_normalize_text_with_metadata_repairs_common_windows_1252_mojibake() -> None:
    raw = (
        "Markdown processors \u00e2\u20ac\u201d don\u00e2\u20ac\u2122t "
        "\u00e2\u0153\u2026\u00c2\u00a0ship broken display text."
    )

    normalized, metadata = normalize_text_with_metadata(raw)

    assert "processors \u2014 don\u2019t \u2705\u00a0ship" in normalized
    assert "\u00e2" not in normalized
    assert metadata["policy_id"] == "source-intelligence-common-cp1252-mojibake-repair.v1"
    assert metadata["encoding_repair_applied"] is True
    assert metadata["encoding_repair_replacement_count"] == 3
    assert metadata["original_text_sha256"] != metadata["repaired_text_sha256"]
    assert metadata["normalized_text_sha256"]


def test_normalize_text_with_metadata_leaves_clean_unicode_unchanged() -> None:
    raw = "JotBird \u2192 \u00d7 and \u2705\u00a0Do this."

    normalized, metadata = normalize_text_with_metadata(raw)

    assert normalized == raw
    assert metadata["encoding_repair_applied"] is False
    assert metadata["encoding_repair_replacement_count"] == 0
    assert metadata["original_text_sha256"] == metadata["repaired_text_sha256"]


def test_source_package_builder_records_source_text_quality_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(source_package_builder, "_VAULT_ROOT", tmp_path)
    monkeypatch.setattr(
        source_package_builder,
        "_SIC_WORKSPACES",
        tmp_path / "runtime" / "source_intelligence" / "workspaces",
    )
    source_path = tmp_path / "captured-source.md"
    source_path.write_text(
        "# Capture\n\nDon\u00e2\u20ac\u2122t ship bad text \u00e2\u20ac\u201d fix it.",
        encoding="utf-8",
    )

    result = source_package_builder.ingest_source(
        source_path,
        workspace_id="repair-test",
        source_type="markdown",
        user_trust_level="reviewed",
    )

    assert result["success"] is True, result.get("error")
    package_path = Path(result["output_path"])
    package = json.loads(package_path.read_text(encoding="utf-8"))
    quality = package["source_text_quality"]
    assert quality["encoding_repair_applied"] is True
    assert "Don\u2019t ship bad text \u2014 fix it." in package["normalized_text"]
    assert "\u00e2" not in package["normalized_text"]
    assert "mojibake repaired" in package["extraction_notes"]
    assert package["_builder_meta"]["source_text_quality"] == quality
