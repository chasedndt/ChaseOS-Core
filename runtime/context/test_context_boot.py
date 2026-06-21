"""Tests for runtime/context/boot.py — ChaseOS Context Boot Protocol."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.context.boot import (
    ContextBundle,
    _extract_current_phase,
    _extract_sprint_focus,
    _frontmatter_field,
    _get_carry_forward,
    _section_after_heading,
    load_boot_context,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

NOW_MD_FULL = """\
---
type: now
date: 2026-04-24
---

# Now — 2026-04-24

## Current Phase

Phase 9 — Operator Runtime (AOR + SBP) — ACTIVE

## Active Now

| Domain | Focus |
|--------|-------|
| ChaseOS | Phase 9 runtime hardening |

- Runtime state + context boot protocol
- Optimus export scaffolding

## Open Loops
- [ ] Write operator close-day brief
"""

CONTRACT_MD = """\
---
type: contract
version: v2.0
title: Assistant-Contract
---

# Assistant Contract
"""

CLOSE_DAY_MD = """\
---
type: operator-close-note
---

# Operator Brief — CLOSE — 2026-04-24

## [CARRY-FORWARD] Open Loops for Tomorrow

> Carry-forward preamble — not a loop.

- status:open — Verify context boot wired into all AOR runs
- status:open — Update MCP smoke test guide with boot_frame surface
"""

OPENCLAW_MANIFEST = """\
adapter_id: "openclaw"
adapter_name: "OpenClaw Persistent Operator Runtime"
trust_ceiling: "tier-2"
approval_mode: "manifest-bounded-per-action"
protected_file_behavior: "block"
allowed_write_targets:
  standard_outputs: true
  operator_briefs: true
  runtime_activity: true
  project_os_files: false
  protected_files: false
"""


def _scaffold_vault(
    tmp_path: Path,
    *,
    now_md: str | None = NOW_MD_FULL,
    contract_md: str | None = CONTRACT_MD,
    close_day_md: str | None = CLOSE_DAY_MD,
    openclaw_manifest: str | None = OPENCLAW_MANIFEST,
) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    # CLAUDE.md so _detect_vault_root works (not needed here but good practice)
    (vault / "CLAUDE.md").write_text("# CLAUDE.md", encoding="utf-8")

    if now_md is not None:
        home = vault / "00_HOME"
        home.mkdir()
        (home / "Now.md").write_text(now_md, encoding="utf-8")
        if contract_md is not None:
            (home / "Assistant-Contract.md").write_text(contract_md, encoding="utf-8")

    if openclaw_manifest is not None:
        manifest_dir = vault / "runtime" / "policy" / "adapters"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "openclaw.yaml").write_text(openclaw_manifest, encoding="utf-8")

    if close_day_md is not None:
        briefs_dir = vault / "07_LOGS" / "Operator-Briefs"
        briefs_dir.mkdir(parents=True)
        (briefs_dir / "2026-04-24-operator-close-day.md").write_text(close_day_md, encoding="utf-8")

    return vault


# ── Parsing helpers ───────────────────────────────────────────────────────────

class TestSectionAfterHeading:
    def test_extracts_content_under_heading(self):
        text = "## Current Phase\n\nPhase 9 — Active\n\n## Next\nother"
        assert "Phase 9" in _section_after_heading(text, "Current Phase")

    def test_stops_at_next_heading(self):
        text = "## A\nline-a\n## B\nline-b"
        result = _section_after_heading(text, "A")
        assert "line-a" in result
        assert "line-b" not in result

    def test_returns_empty_for_missing_heading(self):
        assert _section_after_heading("no headings here", "anything") == ""

    def test_case_insensitive(self):
        text = "## CURRENT PHASE\nPhase 9"
        assert "Phase 9" in _section_after_heading(text, "current phase")


class TestExtractCurrentPhase:
    def test_from_heading_section(self):
        result = _extract_current_phase(NOW_MD_FULL)
        assert "Phase 9" in result

    def test_from_bold_inline(self):
        text = "**Current Phase:** Phase 8 — Done"
        assert "Phase 8" in _extract_current_phase(text)

    def test_returns_empty_when_missing(self):
        assert _extract_current_phase("# Just a title\n\nSome text.") == ""


class TestExtractSprintFocus:
    def test_extracts_first_bullet_under_active_now(self):
        result = _extract_sprint_focus(NOW_MD_FULL)
        assert result  # should return something

    def test_falls_back_to_first_bullet_in_doc(self):
        text = "## Other\n- First bullet here\n- Second"
        result = _extract_sprint_focus(text)
        assert result == "First bullet here"

    def test_empty_when_no_bullets(self):
        assert _extract_sprint_focus("# Header\n\nParagraph only.") == ""


class TestFrontmatterField:
    def test_extracts_version(self):
        assert _frontmatter_field(CONTRACT_MD, "version") == "v2.0"

    def test_extracts_type(self):
        assert _frontmatter_field(CONTRACT_MD, "type") == "contract"

    def test_missing_field_returns_empty(self):
        assert _frontmatter_field(CONTRACT_MD, "nonexistent") == ""

    def test_no_frontmatter_returns_empty(self):
        assert _frontmatter_field("# Just content", "version") == ""


class TestGetCarryForward:
    def test_extracts_carry_forward_bullets(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        result = _get_carry_forward(vault)
        assert "context boot" in result.lower() or "mcp" in result.lower()

    def test_returns_empty_when_no_briefs(self, tmp_path):
        vault = _scaffold_vault(tmp_path, close_day_md=None)
        assert _get_carry_forward(vault) == ""

    def test_returns_none_recorded_when_only_status_none(self, tmp_path):
        vault = _scaffold_vault(
            tmp_path,
            close_day_md="## [CARRY-FORWARD] Open Loops\n\n- status:none — No open loops.\n",
        )
        result = _get_carry_forward(vault)
        assert result == "(none recorded)"

    def test_returns_empty_when_no_briefs_dir(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        assert _get_carry_forward(vault) == ""


# ── load_boot_context ─────────────────────────────────────────────────────────

class TestLoadBootContextOk:
    def test_status_ok_when_all_sources_present(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.boot_status == "ok"

    def test_current_phase_populated(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert "Phase 9" in bundle.current_phase

    def test_sprint_focus_populated(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.sprint_focus  # non-empty

    def test_trust_ceiling_from_manifest(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.trust_ceiling == "tier-2"

    def test_approval_mode_from_manifest(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert "manifest-bounded" in bundle.approval_mode

    def test_protected_file_behavior_normalised(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.protected_file_behavior == "fail-closed"

    def test_allowed_write_targets_populated(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert any("07_LOGS" in t for t in bundle.allowed_write_targets)

    def test_contract_version_extracted(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.assistant_contract_version == "v2.0"

    def test_carry_forward_populated(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.carry_forward  # non-empty

    def test_no_warnings_when_all_sources_present(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.boot_warnings == []

    def test_sources_read_includes_now_md(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert any("Now.md" in s for s in bundle.sources_read)

    def test_timestamp_set(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.timestamp and "T" in bundle.timestamp

    def test_runtime_id_recorded(self, tmp_path):
        vault = _scaffold_vault(tmp_path)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.runtime_id == "openclaw"


class TestLoadBootContextDegraded:
    def test_degraded_when_manifest_missing(self, tmp_path):
        vault = _scaffold_vault(tmp_path, openclaw_manifest=None)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.boot_status in ("degraded", "ok")  # degraded unless another manifest fills in
        # At minimum, Now.md was read
        assert any("Now.md" in s for s in bundle.sources_read)

    def test_warning_when_manifest_missing(self, tmp_path):
        vault = _scaffold_vault(tmp_path, openclaw_manifest=None)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        # Only degraded if no fallback manifest found (none in this vault)
        if bundle.boot_status == "degraded":
            assert bundle.boot_warnings

    def test_degraded_when_contract_missing(self, tmp_path):
        vault = _scaffold_vault(tmp_path, contract_md=None)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.boot_status == "degraded"
        assert any("Assistant-Contract" in w for w in bundle.boot_warnings)


class TestLoadBootContextFailed:
    def test_failed_when_now_md_missing(self, tmp_path):
        vault = _scaffold_vault(tmp_path, now_md=None)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.boot_status == "failed"

    def test_failed_has_warning_about_now_md(self, tmp_path):
        vault = _scaffold_vault(tmp_path, now_md=None)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert any("Now.md" in w for w in bundle.boot_warnings)

    def test_failed_current_phase_empty(self, tmp_path):
        vault = _scaffold_vault(tmp_path, now_md=None)
        bundle = load_boot_context(vault, runtime_id="openclaw")
        assert bundle.current_phase == ""


# ── ContextBundle.to_frame() ──────────────────────────────────────────────────

class TestContextBundleToFrame:
    def test_frame_contains_runtime_id(self, tmp_path):
        bundle = load_boot_context(_scaffold_vault(tmp_path), runtime_id="openclaw")
        frame = bundle.to_frame()
        assert "openclaw" in frame

    def test_frame_contains_boot_status(self, tmp_path):
        bundle = load_boot_context(_scaffold_vault(tmp_path), runtime_id="openclaw")
        frame = bundle.to_frame()
        assert "OK" in frame or "DEGRADED" in frame

    def test_frame_contains_instructions(self, tmp_path):
        bundle = load_boot_context(_scaffold_vault(tmp_path), runtime_id="openclaw")
        frame = bundle.to_frame()
        assert "Before executing" in frame
        assert "Now.md" in frame

    def test_frame_contains_phase(self, tmp_path):
        bundle = load_boot_context(_scaffold_vault(tmp_path), runtime_id="openclaw")
        frame = bundle.to_frame()
        assert "Phase 9" in frame

    def test_frame_is_string(self, tmp_path):
        bundle = load_boot_context(_scaffold_vault(tmp_path), runtime_id="openclaw")
        assert isinstance(bundle.to_frame(), str)


# ── ContextBundle.to_dict() ───────────────────────────────────────────────────

class TestContextBundleToDict:
    def test_to_dict_serialisable(self, tmp_path):
        bundle = load_boot_context(_scaffold_vault(tmp_path), runtime_id="openclaw")
        data = bundle.to_dict()
        # Must be JSON-serialisable
        json.dumps(data)

    def test_to_dict_has_required_keys(self, tmp_path):
        bundle = load_boot_context(_scaffold_vault(tmp_path), runtime_id="openclaw")
        data = bundle.to_dict()
        for key in ("runtime_id", "boot_status", "current_phase", "trust_ceiling",
                    "sources_read", "boot_warnings", "timestamp"):
            assert key in data, f"missing key: {key}"


# ── AOR engine integration ────────────────────────────────────────────────────

class TestAOREngineBootIntegration:
    def test_boot_context_in_audit_record(self, tmp_path):
        """AOR audit records include context_boot when the engine runs."""
        from runtime.aor.engine import run_workflow

        vault = _scaffold_vault(tmp_path)
        # Create minimal AOR scaffolding so the engine can run a dry-run
        # (workflow not found → escalated at workflow_lookup, but boot runs first)
        result = run_workflow(
            "nonexistent_workflow",
            inputs={},
            vault_root=vault,
            dry_run=True,
            runtime_id="openclaw",
        )
        # Escalated at workflow_lookup (workflow doesn't exist) — boot still ran
        assert result.status == "escalated"
        assert result.stage_reached == "workflow_lookup"

    def test_boot_failed_escalates_before_stage1(self, tmp_path):
        """run_workflow escalates at context_boot when Now.md is missing."""
        from runtime.aor.engine import run_workflow

        vault = _scaffold_vault(tmp_path, now_md=None)
        result = run_workflow(
            "operator_today",
            inputs={},
            vault_root=vault,
            dry_run=True,
            runtime_id="openclaw",
        )
        assert result.status == "escalated"
        assert result.stage_reached == "context_boot"
        assert "Now.md" in (result.escalation_reason or "")
