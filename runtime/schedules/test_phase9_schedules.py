"""
test_phase9_schedules.py — ChaseOS Phase 9 Schedule Intent Layer Tests

Coverage:
  Loader:
  - load_schedule: valid schedule loads and validates correctly
  - load_schedule: missing schedule file returns None
  - load_schedule: missing required fields raises ValueError
  - load_schedule: schedule_id mismatch raises ValueError
  - load_schedule: invalid runtime_adapter_target raises ValueError (fail closed)
  - load_schedule: invalid delivery.primary_target raises ValueError (fail closed)
  - load_schedule: invalid cadence.type raises ValueError
  - load_schedule: cron type missing cron_expression raises ValueError
  - load_schedule: invalid approval_policy raises ValueError
  - load_schedule: unschedulable task_type raises ValueError
  - load_schedule: non-existent workflow_id raises ValueError
  - load_schedule: enabled=True with inactive workflow raises ValueError
  - load_schedule: disabled schedule loads even if workflow would be inactive
  - list_schedules: returns all valid schedules; skips index.yaml and _schema files
  - list_schedules: empty directory returns empty list
  - list_schedules: invalid file skipped with warning
  - validate_all_schedules: returns empty list when all valid
  - validate_all_schedules: returns error entries for invalid schedules
  - enable_schedule: changes enabled: false -> true; returns True
  - enable_schedule: already enabled returns False
  - enable_schedule: non-existent schedule raises ValueError
  - disable_schedule: changes enabled: true -> false; returns True
  - disable_schedule: already disabled returns False
  - state_change_log: enable writes log entry to Schedule-State/schedule_state_log.jsonl
  - real_schedules: both seed schedule files load and validate against real registry

Running:
  PYTHONIOENCODING=utf-8 python runtime/schedules/test_phase9_schedules.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure project root is on sys.path
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from runtime.schedules.loader import (
    ScheduleIntent,
    load_schedule,
    list_schedules,
    validate_all_schedules,
    enable_schedule,
    disable_schedule,
    export_schedules_for_adapter,
    _get_state_log_path,
    VALID_RUNTIME_ADAPTERS,
    SCHEDULABLE_TASK_TYPES,
)

# ── Test harness ──────────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0
_ERRORS: list[str] = []
_TESTS: list[tuple[str, object]] = []


def _register(label: str):
    def decorator(fn):
        _TESTS.append((label, fn))
        return fn
    return decorator


def _run_test(label: str, fn) -> None:
    global _PASS, _FAIL
    try:
        fn()
        _PASS += 1
        print(f"  PASS")
    except AssertionError as exc:
        _FAIL += 1
        msg = f"FAIL: {label}: {exc}"
        _ERRORS.append(msg)
        print(f"  FAIL: {exc}")
    except Exception as exc:
        _FAIL += 1
        msg = f"ERROR: {label}: {type(exc).__name__}: {exc}"
        _ERRORS.append(msg)
        print(f"  ERROR: {type(exc).__name__}: {exc}")


# ── Test vault builder ────────────────────────────────────────────────────────

def _make_test_vault(tmp_dir: Path) -> Path:
    """Create a minimal fake ChaseOS vault with a workflow registry."""
    vault = tmp_dir / "vault"
    vault.mkdir()
    (vault / "CLAUDE.md").write_text("# CLAUDE.md", encoding="utf-8")

    # Workflow registry with an active operator-briefing workflow
    registry_dir = vault / "runtime" / "workflows" / "registry"
    registry_dir.mkdir(parents=True)
    _write_workflow(registry_dir, "test_workflow", "operator-briefing", "no_protected_file_writes", "active")

    # Schedule intent dir
    schedules_dir = vault / "runtime" / "schedules"
    schedules_dir.mkdir(parents=True)

    return vault


def _write_workflow(registry_dir: Path, wf_id: str, task_type: str, ceiling: str, status: str) -> None:
    content = f"""id: {wf_id}
name: "Test Workflow"
version: "1.0"
description: "Test workflow"
task_type: {task_type}
role_card: operator-briefing
trigger_type: manual
owner: operator
status: {status}
permission_ceiling: {ceiling}
writeback_targets:
  - "07_LOGS/Operator-Briefs/"
failure_behavior: escalate
"""
    (registry_dir / f"{wf_id}.yaml").write_text(content, encoding="utf-8")


def _write_schedule(schedules_dir: Path, schedule_id: str, *, workflow_id: str = "test_workflow",
                    enabled: bool = True, adapter: str = "openclaw", delivery_target: str = "vault-local",
                    approval: str = "none", cadence_type: str = "cron",
                    allowed_types: list | None = None) -> None:
    allowed = allowed_types if allowed_types is not None else ["operator-briefing"]
    allowed_str = "\n".join(f"  - {t}" for t in allowed)
    cron_fields = ""
    if cadence_type == "cron":
        cron_fields = '  cron_expression: "0 7 * * 1-5"\n  timezone: America/New_York'
    else:
        cron_fields = "  event_type: null\n  event_source: null"

    content = f"""schedule_id: {schedule_id}
workflow_id: {workflow_id}
owner: operator
cadence:
  type: {cadence_type}
{cron_fields}
trigger_source: {adapter}
runtime_adapter_target: {adapter}
delivery:
  primary_target: {delivery_target}
  vault_writeback_targets:
    - "07_LOGS/Operator-Briefs/"
  external_delivery_declared: false
  vault_local_only: true
approval_policy: {approval}
enabled: {str(enabled).lower()}
shadow_mode: false
failure_behavior: escalate
audit_requirements:
  - workflow_id
  - schedule_id
  - trigger_time
  - status
  - files_written
allowed_workflow_task_types:
{allowed_str}
provenance:
  created_by: operator
  created_at: "2026-04-15T00:00:00Z"
  rationale: Test schedule.
notes: Test notes.
"""
    (schedules_dir / f"{schedule_id}.yaml").write_text(content, encoding="utf-8")


# ── Tests ─────────────────────────────────────────────────────────────────────

@_register("load_schedule: valid schedule loads correctly")
def test_load_valid():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        _write_schedule(vault / "runtime" / "schedules", "test-sched-001")
        intent = load_schedule("test-sched-001", vault)
        assert intent is not None, "Expected ScheduleIntent, got None"
        assert intent.schedule_id == "test-sched-001"
        assert intent.workflow_id == "test_workflow"
        assert intent.enabled is True
        assert intent.runtime_adapter_target == "openclaw"


@_register("load_schedule: missing file returns None")
def test_load_missing():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        result = load_schedule("nonexistent-schedule", vault)
        assert result is None, "Expected None for missing schedule"


@_register("load_schedule: missing required fields raises ValueError")
def test_load_missing_required_fields():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        # Write a partial schedule missing many fields
        (schedules_dir / "bad-sched.yaml").write_text(
            "schedule_id: bad-sched\nworkflow_id: test_workflow\n",
            encoding="utf-8"
        )
        try:
            load_schedule("bad-sched", vault)
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "missing required fields" in str(exc)


@_register("load_schedule: schedule_id mismatch raises ValueError")
def test_load_id_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        _write_schedule(schedules_dir, "correct-id")
        # Read the content and write it with a wrong filename
        content = (schedules_dir / "correct-id.yaml").read_text()
        (schedules_dir / "wrong-filename.yaml").write_text(content, encoding="utf-8")
        try:
            load_schedule("wrong-filename", vault)
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "must match filename stem" in str(exc)


@_register("load_schedule: invalid runtime_adapter_target fails closed")
def test_load_invalid_adapter():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        _write_schedule(vault / "runtime" / "schedules", "bad-adapter-sched",
                        adapter="zapier")  # Not a valid adapter
        try:
            load_schedule("bad-adapter-sched", vault)
            assert False, "Should have raised ValueError for invalid adapter"
        except ValueError as exc:
            assert "not a registered adapter" in str(exc)


@_register("load_schedule: invalid delivery.primary_target fails closed")
def test_load_invalid_delivery_target():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        _write_schedule(vault / "runtime" / "schedules", "bad-delivery-sched",
                        delivery_target="telegram")  # Not valid
        try:
            load_schedule("bad-delivery-sched", vault)
            assert False, "Should have raised ValueError for invalid delivery target"
        except ValueError as exc:
            assert "not a valid delivery target" in str(exc)


@_register("load_schedule: invalid cadence.type raises ValueError")
def test_load_invalid_cadence():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        _write_schedule(schedules_dir, "bad-cadence-sched", cadence_type="polling")
        try:
            load_schedule("bad-cadence-sched", vault)
            assert False, "Should have raised ValueError for invalid cadence"
        except ValueError as exc:
            assert "cadence.type" in str(exc)


@_register("load_schedule: cron missing cron_expression raises ValueError")
def test_load_cron_missing_expression():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        # Write a schedule with cadence.type=cron but no cron_expression
        content = """schedule_id: no-cron-expr
workflow_id: test_workflow
owner: operator
cadence:
  type: cron
  timezone: America/New_York
trigger_source: openclaw
runtime_adapter_target: openclaw
delivery:
  primary_target: vault-local
  vault_writeback_targets:
    - "07_LOGS/Operator-Briefs/"
  external_delivery_declared: false
  vault_local_only: true
approval_policy: none
enabled: true
shadow_mode: false
failure_behavior: escalate
audit_requirements:
  - workflow_id
allowed_workflow_task_types:
  - operator-briefing
provenance:
  created_by: operator
  created_at: "2026-04-15T00:00:00Z"
  rationale: Test.
"""
        (schedules_dir / "no-cron-expr.yaml").write_text(content, encoding="utf-8")
        try:
            load_schedule("no-cron-expr", vault)
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "cron_expression" in str(exc)


@_register("load_schedule: invalid approval_policy raises ValueError")
def test_load_invalid_approval():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        _write_schedule(vault / "runtime" / "schedules", "bad-approval-sched",
                        approval="auto-approve")
        try:
            load_schedule("bad-approval-sched", vault)
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "approval_policy" in str(exc)


@_register("load_schedule: unschedulable task_type raises ValueError")
def test_load_unschedulable_task_type():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        # Create a vault-mutation workflow (unschedulable)
        registry_dir = vault / "runtime" / "workflows" / "registry"
        _write_workflow(registry_dir, "mutation_wf", "vault-mutation", "no_protected_file_writes", "active")
        _write_schedule(vault / "runtime" / "schedules", "bad-type-sched",
                        workflow_id="mutation_wf",
                        allowed_types=["vault-mutation"])
        try:
            load_schedule("bad-type-sched", vault)
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "not schedulable" in str(exc) or "not in" in str(exc)


@_register("load_schedule: non-existent workflow_id raises ValueError")
def test_load_nonexistent_workflow():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        _write_schedule(vault / "runtime" / "schedules", "ghost-wf-sched",
                        workflow_id="nonexistent_workflow")
        try:
            load_schedule("ghost-wf-sched", vault)
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "does not exist in runtime/workflows/registry/" in str(exc)


@_register("load_schedule: enabled=True with inactive workflow raises ValueError")
def test_load_enabled_inactive_workflow():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        registry_dir = vault / "runtime" / "workflows" / "registry"
        _write_workflow(registry_dir, "draft_wf", "operator-briefing", "no_protected_file_writes", "draft")
        _write_schedule(vault / "runtime" / "schedules", "enabled-draft-sched",
                        workflow_id="draft_wf", enabled=True)
        try:
            load_schedule("enabled-draft-sched", vault)
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "not runnable" in str(exc) or "status=" in str(exc)


@_register("load_schedule: disabled schedule with inactive workflow still loads")
def test_load_disabled_inactive_workflow():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        registry_dir = vault / "runtime" / "workflows" / "registry"
        _write_workflow(registry_dir, "draft_wf2", "operator-briefing", "no_protected_file_writes", "draft")
        _write_schedule(vault / "runtime" / "schedules", "disabled-draft-sched",
                        workflow_id="draft_wf2", enabled=False)
        # Disabled schedule pointing to inactive workflow should load fine
        intent = load_schedule("disabled-draft-sched", vault)
        assert intent is not None
        assert intent.enabled is False


@_register("list_schedules: returns all valid schedules; skips index.yaml and _schema files")
def test_list_schedules_filters():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        _write_schedule(schedules_dir, "sched-a")
        _write_schedule(schedules_dir, "sched-b", enabled=False)
        # index.yaml and _schema.yaml should be skipped
        (schedules_dir / "index.yaml").write_text("schema_version: '1.0'\nschedules: []\n")
        (schedules_dir / "_schema.yaml").write_text("# schema doc\n")
        schedules = list_schedules(vault)
        ids = [s.schedule_id for s in schedules]
        assert "sched-a" in ids, f"Expected sched-a in {ids}"
        assert "sched-b" in ids, f"Expected sched-b in {ids}"
        assert "index" not in ids, "index.yaml should be skipped"
        assert "_schema" not in ids, "_schema.yaml should be skipped"
        assert len(ids) == 2


@_register("list_schedules: empty directory returns empty list")
def test_list_schedules_empty():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules = list_schedules(vault)
        assert schedules == [], f"Expected empty list, got {schedules}"


@_register("list_schedules: invalid file is skipped with warning")
def test_list_schedules_skips_invalid(capsys=None):
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        _write_schedule(schedules_dir, "valid-one")
        # Write an invalid schedule (schedule_id mismatch)
        content = (schedules_dir / "valid-one.yaml").read_text()
        # Write it under a different name so id doesn't match
        (schedules_dir / "mismatch-name.yaml").write_text(content, encoding="utf-8")
        schedules = list_schedules(vault)
        ids = [s.schedule_id for s in schedules]
        assert "valid-one" in ids
        assert len(ids) == 1, f"Expected 1 valid schedule, got {ids}"


@_register("validate_all_schedules: returns empty list when all valid")
def test_validate_all_valid():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        _write_schedule(schedules_dir, "valid-sched-x")
        errors = validate_all_schedules(vault)
        assert errors == [], f"Expected no errors, got: {errors}"


@_register("validate_all_schedules: returns error entries for invalid schedules")
def test_validate_all_with_invalid():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        _write_schedule(schedules_dir, "valid-sched-y")
        _write_schedule(schedules_dir, "bad-adapter-sched-y", adapter="zapier")
        errors = validate_all_schedules(vault)
        error_ids = [e[0] for e in errors]
        assert "bad-adapter-sched-y" in error_ids, f"Expected bad-adapter-sched-y in errors: {error_ids}"
        assert "valid-sched-y" not in error_ids


@_register("enable_schedule: changes enabled: false -> true; returns True")
def test_enable_schedule():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        _write_schedule(schedules_dir, "toggle-sched", enabled=False)
        changed = enable_schedule("toggle-sched", vault)
        assert changed is True, "Expected True (state changed)"
        intent = load_schedule("toggle-sched", vault)
        assert intent.enabled is True, "Expected enabled=True after enable_schedule"


@_register("enable_schedule: already enabled returns False")
def test_enable_already_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        _write_schedule(vault / "runtime" / "schedules", "already-on", enabled=True)
        changed = enable_schedule("already-on", vault)
        assert changed is False, "Expected False (no state change)"


@_register("enable_schedule: non-existent schedule raises ValueError")
def test_enable_nonexistent():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        try:
            enable_schedule("ghost-schedule", vault)
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "not found in runtime/schedules/" in str(exc)


@_register("disable_schedule: changes enabled: true -> false; returns True")
def test_disable_schedule():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        _write_schedule(vault / "runtime" / "schedules", "on-sched", enabled=True)
        changed = disable_schedule("on-sched", vault)
        assert changed is True, "Expected True (state changed)"
        # After disabling, read raw file to check enabled is false
        sched_path = vault / "runtime" / "schedules" / "on-sched.yaml"
        import yaml as _yaml
        raw = _yaml.safe_load(sched_path.read_text())
        assert raw["enabled"] is False, f"Expected enabled=False, got {raw['enabled']}"


@_register("disable_schedule: already disabled returns False")
def test_disable_already_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        _write_schedule(vault / "runtime" / "schedules", "already-off", enabled=False)
        changed = disable_schedule("already-off", vault)
        assert changed is False, "Expected False (no state change)"


@_register("state_change_log: enable writes log entry to schedule_state_log.jsonl")
def test_state_change_log_written():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        _write_schedule(vault / "runtime" / "schedules", "log-test-sched", enabled=False)
        enable_schedule("log-test-sched", vault)
        log_path = _get_state_log_path(vault)
        assert log_path.exists(), f"State log not created at {log_path}"
        lines = [l.strip() for l in log_path.read_text().splitlines() if l.strip()]
        assert len(lines) >= 1, "Expected at least one log entry"
        entry = json.loads(lines[-1])
        assert entry["schedule_id"] == "log-test-sched"
        assert entry["action"] == "enable"
        assert entry["new_enabled"] is True
        assert entry["previous_enabled"] is False
        assert "timestamp_utc" in entry


@_register("real_schedules: both seed schedules load and validate against real registry")
def test_real_seed_schedules():
    """Load the actual schedule files from the real vault."""
    real_vault = _PROJECT_ROOT
    if not (real_vault / "CLAUDE.md").exists():
        print("  (skipping — real vault not at project root)")
        return

    schedules_dir = real_vault / "runtime" / "schedules"
    if not schedules_dir.exists():
        assert False, "runtime/schedules/ directory does not exist in real vault"

    # Validate today schedule
    intent = load_schedule("sch-operator-today-0700", real_vault)
    assert intent is not None, "sch-operator-today-0700 failed to load"
    assert intent.workflow_id == "operator_today"
    assert intent.runtime_adapter_target == "hermes", (
        f"Expected hermes as primary executor, got: {intent.runtime_adapter_target}"
    )
    assert intent.runtime_adapter_fallback == "openclaw", (
        f"Expected openclaw as fallback, got: {intent.runtime_adapter_fallback}"
    )
    assert isinstance(intent.enabled, bool)
    assert intent.cadence.type == "cron"

    # Validate close day schedule
    intent2 = load_schedule("sch-operator-close-day-1900", real_vault)
    assert intent2 is not None, "sch-operator-close-day-1900 failed to load"
    assert intent2.workflow_id == "operator_close_day"
    assert intent2.runtime_adapter_target == "hermes", (
        f"Expected hermes as primary executor, got: {intent2.runtime_adapter_target}"
    )
    assert intent2.runtime_adapter_fallback == "openclaw", (
        f"Expected openclaw as fallback, got: {intent2.runtime_adapter_fallback}"
    )
    assert isinstance(intent2.enabled, bool)

    # validate_all_schedules should return no errors
    errors = validate_all_schedules(real_vault)
    assert errors == [], f"Real schedules failed validation: {errors}"


@_register("real_schedules: disabled StrikeZone evidence gate remains declared but inactive")
def test_strikezone_daily_evidence_gate_declared_disabled_and_shadowed():
    real_vault = _PROJECT_ROOT
    if not (real_vault / "CLAUDE.md").exists():
        print("  (skipping — real vault not at project root)")
        return

    intent = load_schedule("sch-strikezone-daily-evidence-gate-0700", real_vault)
    assert intent is not None, "sch-strikezone-daily-evidence-gate-0700 failed to load"
    assert intent.workflow_id == "strikezone_daily_evidence_gate"
    assert intent.enabled is False
    assert intent.shadow_mode is True
    assert intent.approval_policy == "pre-delivery"
    assert "evidence-gated-market-analysis" in intent.allowed_workflow_task_types


@_register("hermes is a valid runtime_adapter_target")
def test_hermes_in_valid_adapters():
    assert "hermes" in VALID_RUNTIME_ADAPTERS, (
        f"'hermes' must be in VALID_RUNTIME_ADAPTERS; got: {sorted(VALID_RUNTIME_ADAPTERS)}"
    )


@_register("load_schedule: runtime_adapter_fallback is loaded correctly")
def test_load_fallback_field():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        # Write a schedule with hermes as primary and openclaw as fallback
        content = (schedules_dir / "test-sched-001.yaml").read_text() if False else None
        _write_schedule(schedules_dir, "fallback-test-sched", adapter="hermes")
        # Patch in the fallback field
        path = schedules_dir / "fallback-test-sched.yaml"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "runtime_adapter_target: hermes",
            "runtime_adapter_target: hermes\nruntime_adapter_fallback: openclaw",
        )
        path.write_text(text, encoding="utf-8")
        intent = load_schedule("fallback-test-sched", vault)
        assert intent is not None
        assert intent.runtime_adapter_target == "hermes"
        assert intent.runtime_adapter_fallback == "openclaw"


@_register("load_schedule: invalid runtime_adapter_fallback fails closed")
def test_load_invalid_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        _write_schedule(schedules_dir, "bad-fallback-sched", adapter="hermes")
        path = schedules_dir / "bad-fallback-sched.yaml"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "runtime_adapter_target: hermes",
            "runtime_adapter_target: hermes\nruntime_adapter_fallback: zapier",
        )
        path.write_text(text, encoding="utf-8")
        try:
            load_schedule("bad-fallback-sched", vault)
            assert False, "Should have raised ValueError for invalid fallback adapter"
        except ValueError as exc:
            assert "runtime_adapter_fallback" in str(exc)
            assert "not a registered adapter" in str(exc)


@_register("load_schedule: runtime_adapter_fallback same as primary raises ValueError")
def test_load_fallback_same_as_primary():
    with tempfile.TemporaryDirectory() as tmp:
        vault = _make_test_vault(Path(tmp))
        schedules_dir = vault / "runtime" / "schedules"
        _write_schedule(schedules_dir, "same-fallback-sched", adapter="hermes")
        path = schedules_dir / "same-fallback-sched.yaml"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "runtime_adapter_target: hermes",
            "runtime_adapter_target: hermes\nruntime_adapter_fallback: hermes",
        )
        path.write_text(text, encoding="utf-8")
        try:
            load_schedule("same-fallback-sched", vault)
            assert False, "Should have raised ValueError when fallback equals primary"
        except ValueError as exc:
            assert "must differ" in str(exc)


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("ChaseOS Phase 9 — Schedule Intent Layer Tests")
    print("=" * 55)

    for label, fn in _TESTS:
        print(f"\n[{label}]")
        _run_test(label, fn)

    print()
    print("=" * 55)
    print(f"Results: {_PASS} passed, {_FAIL} failed / {len(_TESTS)} total")

    if _ERRORS:
        print()
        print("Failures:")
        for err in _ERRORS:
            print(f"  {err}")

    sys.exit(0 if _FAIL == 0 else 1)
