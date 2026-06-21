"""Focused tests for the Phase 9 provenance validator foothold."""

from __future__ import annotations

import json
import sys
from pathlib import Path


_HERE = Path(__file__).resolve()
_VAULT_ROOT = _HERE.parents[2]
if str(_VAULT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VAULT_ROOT))

from runtime.schemas.provenance_validator import (  # noqa: E402
    is_valid_provenance_block,
    validate_provenance_block,
)


_FIXTURES = _HERE.parent / "fixtures" / "provenance"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_minimal_valid_block_passes() -> None:
    data = {
        "source_ids": ["spkg-123"],
        "processing_stage": "source_package",
        "verification_status": "unverified",
        "lineage_chain": [
            {
                "stage": "raw_capture",
                "ref": "03_INPUTS/00_QUARANTINE/source/example.md",
                "timestamp": "2026-04-24T00:00:00Z",
            }
        ],
        "created_at": "2026-04-24T00:00:00Z",
        "last_modified_at": "2026-04-24T00:00:00Z",
        "operator_reviewed_at": None,
        "source_refs": ["03_INPUTS/00_QUARANTINE/source/example.md"],
        "audit_refs": ["07_LOGS/Agent-Activity/example.md"],
    }
    assert validate_provenance_block(data) == []
    assert is_valid_provenance_block(data) is True


def test_missing_required_field_fails() -> None:
    data = _load_fixture("minimal_valid.json")
    data.pop("source_ids")
    errors = validate_provenance_block(data)
    assert any("source_ids" in error for error in errors)
    assert is_valid_provenance_block(data) is False


def test_invalid_processing_stage_fails() -> None:
    data = _load_fixture("minimal_valid.json")
    data["processing_stage"] = "magic_stage"
    errors = validate_provenance_block(data)
    assert any("processing_stage" in error for error in errors)


def test_invalid_verification_status_fails() -> None:
    data = _load_fixture("minimal_valid.json")
    data["verification_status"] = "totally_verified"
    errors = validate_provenance_block(data)
    assert any("verification_status" in error for error in errors)


def test_malformed_lineage_entry_fails() -> None:
    data = _load_fixture("minimal_valid.json")
    data["lineage_chain"] = [{"stage": "raw_capture", "timestamp": "2026-04-24T00:00:00Z"}]
    errors = validate_provenance_block(data)
    assert any("lineage_chain" in error for error in errors)


def test_fixture_examples_validate() -> None:
    for name in [
        "minimal_valid.json",
        "source_package_linked.json",
        "acquisition_packet_linked.json",
        "generated_output_linked.json",
    ]:
        data = _load_fixture(name)
        errors = validate_provenance_block(data)
        assert errors == [], f"{name} failed validation: {errors}"
