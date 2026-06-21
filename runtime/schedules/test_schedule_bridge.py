"""
test_schedule_bridge.py — Phase 9 OpenClaw Schedule-Source Sync Bridge Tests

Verifies:
- export_schedules_for_adapter returns the correct schedules for openclaw
- adapter filtering works (n8n gets nothing from current schedules)
- no duplicate schedule truth exists (each workflow_id appears once per adapter)
- both operator_today and operator_close_day are present and correct
- the export includes the right fields for adapter consumption
- duplicate workflow detection raises ValueError
- disabled schedules are excluded by default, included with enabled_only=False
- MCP scope was not changed (no schedule.intent.read resource added)

Run: python -m pytest runtime/schedules/test_schedule_bridge.py -v
Or:  python runtime/schedules/test_schedule_bridge.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make repo root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.schedules.loader import (
    export_schedules_for_adapter,
    list_schedules,
    load_schedule,
    ScheduleIntent,
)

# ── Test Registry ──────────────────────────────────────────────────────────────

_TESTS: list[tuple[str, object]] = []


def _register(label: str):
    def decorator(fn):
        _TESTS.append((label, fn))
        return fn
    return decorator


def _run_test(label: str, fn) -> None:
    try:
        fn()
        print(f"  PASS  {label}")
    except Exception as exc:
        print(f"  FAIL  {label}")
        print(f"        {exc}")
        raise


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_test_vault(tmp_dir: Path) -> Path:
    vault = tmp_dir / "vault"
    vault.mkdir()
    (vault / "CLAUDE.md").write_text("# test", encoding="utf-8")
    (vault / "runtime").mkdir()
    (vault / "runtime" / "schedules").mkdir()
    (vault / "runtime" / "workflows").mkdir()
    (vault / "runtime" / "workflows" / "registry").mkdir()
    return vault


def _write_workflow(
    registry_dir: Path,
    wf_id: str,
    task_type: str = "operator-briefing",
    ceiling: str = "no_protected_file_writes",
    status: str = "active",
    runtime_adapter: str | None = None,
    coordination_sensitive: bool = False,
) -> None:
    runtime_adapter_block = f"runtime_adapter: {runtime_adapter}\n" if runtime_adapter else ""
    coordination_block = ""
    if coordination_sensitive:
        coordination_block = """coordination_requirements:
  coordination_sensitive: true
  via: "runtime/agent_bus/"
  partner_runtime: "OpenClaw"
  execution_mode: "poll-and-claim"
"""
    content = f"""id: {wf_id}
name: "{wf_id}"
version: "1.0"
description: "test workflow"
task_type: {task_type}
role_card: operator-briefing
trigger_type: manual
owner: operator
status: {status}
{runtime_adapter_block}\
permission_ceiling: {ceiling}
inputs: []
outputs: []
writeback_targets:
  - "07_LOGS/Operator-Briefs/"
failure_behavior: escalate
rollback_path: "delete partial"
approval_rule: none
audit_expectations: []
{coordination_block}\
"""
    (registry_dir / f"{wf_id}.yaml").write_text(content, encoding="utf-8")


def _write_schedule(
    schedules_dir: Path,
    schedule_id: str,
    *,
    workflow_id: str = "test_workflow",
    enabled: bool = True,
    adapter: str = "openclaw",
    shadow_mode: bool = False,
    delivery_target: str = "vault-local",
    cron_expression: str = "0 7 * * 1-5",
    timezone: str = "America/New_York",
    allowed_types: list[str] | None = None,
) -> None:
    allowed = allowed_types or ["operator-briefing"]
    allowed_block = "\n".join(f"  - {task_type}" for task_type in allowed)
    content = f"""schedule_id: {schedule_id}
workflow_id: {workflow_id}
owner: operator
cadence:
  type: cron
  cron_expression: "{cron_expression}"
  timezone: {timezone}
  event_type: null
  event_source: null
trigger_source: {adapter}
runtime_adapter_target: {adapter}
delivery:
  primary_target: {delivery_target}
  vault_writeback_targets:
    - "07_LOGS/Operator-Briefs/"
  external_delivery_declared: false
  vault_local_only: true
approval_policy: none
enabled: {str(enabled).lower()}
shadow_mode: {str(shadow_mode).lower()}
failure_behavior: escalate
audit_requirements:
  - workflow_id
  - schedule_id
  - trigger_time
  - status
  - files_written
allowed_workflow_task_types:
{allowed_block}
provenance:
  created_by: operator
  created_at: "2026-04-15T00:00:00Z"
  rationale: "test schedule"
"""
    (schedules_dir / f"{schedule_id}.yaml").write_text(content, encoding="utf-8")


# ── Tests ──────────────────────────────────────────────────────────────────────

def _write_command_schedule(
    schedules_dir: Path,
    schedule_id: str,
    *,
    command_id: str = "events.watch",
    command: str = "chaseos events watch --once --execute",
    enabled: bool = True,
    adapter: str = "openclaw",
    cron_expression: str = "* * * * *",
    timezone: str = "America/New_York",
) -> None:
    content = f"""schedule_id: {schedule_id}
schedule_kind: command
command_id: {command_id}
command: "{command}"
owner: operator
cadence:
  type: cron
  cron_expression: "{cron_expression}"
  timezone: {timezone}
  event_type: null
  event_source: null
trigger_source: {adapter}
runtime_adapter_target: {adapter}
delivery:
  primary_target: vault-local
  vault_writeback_targets:
    - "runtime/events/"
  external_delivery_declared: false
  vault_local_only: true
approval_policy: none
enabled: {str(enabled).lower()}
shadow_mode: false
failure_behavior: escalate
audit_requirements:
  - schedule_id
  - command_id
  - trigger_time
  - event_dispatch_status
allowed_workflow_task_types: []
allowed_command_ids:
  - {command_id}
provenance:
  created_by: operator
  created_at: "2026-04-27T00:00:00Z"
  rationale: "test command schedule"
"""
    (schedules_dir / f"{schedule_id}.yaml").write_text(content, encoding="utf-8")


@_register("export: openclaw adapter returns matching schedules only")
def test_export_openclaw_filter(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(reg, "wf_a")
    _write_workflow(reg, "wf_b")
    _write_workflow(reg, "wf_n8n")

    _write_schedule(sched, "sch-a", workflow_id="wf_a", adapter="openclaw")
    _write_schedule(sched, "sch-b", workflow_id="wf_b", adapter="openclaw")
    _write_schedule(sched, "sch-n8n", workflow_id="wf_n8n", adapter="n8n")

    result = export_schedules_for_adapter("openclaw", vault)
    ids = [e["schedule_id"] for e in result]
    assert "sch-a" in ids
    assert "sch-b" in ids
    assert "sch-n8n" not in ids, "n8n schedule must not appear in openclaw export"


@_register("export: n8n adapter returns empty when no n8n schedules exist")
def test_export_n8n_empty(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(reg, "wf_a")
    _write_schedule(sched, "sch-a", workflow_id="wf_a", adapter="openclaw")

    result = export_schedules_for_adapter("n8n", vault)
    assert result == [], f"Expected empty list for n8n adapter, got {result}"


@_register("export: includes required fields for adapter consumption")
def test_export_includes_required_fields(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(reg, "operator_today")
    _write_schedule(sched, "sch-today", workflow_id="operator_today", adapter="openclaw")

    result = export_schedules_for_adapter("openclaw", vault)
    assert len(result) == 1
    entry = result[0]

    required = {
        "schedule_id", "workflow_id", "cadence_type", "cron_expression",
        "timezone", "enabled", "shadow_mode", "command",
        "approval_policy", "failure_behavior",
        "vault_writeback_targets", "audit_requirements",
    }
    missing = required - set(entry.keys())
    assert not missing, f"Missing required fields: {missing}"


@_register("export: command field is exact chaseos CLI invocation")
def test_export_command_format(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(reg, "operator_today")
    _write_schedule(sched, "sch-today", workflow_id="operator_today", adapter="openclaw")

    result = export_schedules_for_adapter("openclaw", vault)
    assert result[0]["command"] == "chaseos run operator_today"


@_register("export: command schedules emit exact allowlisted command")
def test_export_command_schedule(tmp_path):
    vault = _make_test_vault(tmp_path)
    sched = vault / "runtime" / "schedules"

    _write_command_schedule(sched, "sch-events-watch-every-minute")

    result = export_schedules_for_adapter("openclaw", vault)
    assert len(result) == 1
    entry = result[0]
    assert entry["schedule_kind"] == "command"
    assert entry["command_id"] == "events.watch"
    assert entry["workflow_id"] is None
    assert entry["command"] == "chaseos events watch --once --execute"


@_register("load_schedule: BOM-prefixed command schedule with empty task types loads")
def test_bom_prefixed_command_schedule_empty_task_types_loads(tmp_path):
    vault = _make_test_vault(tmp_path)
    schedules_dir = vault / "runtime" / "schedules"
    schedule_id = "sch-events-watch-bom"
    _write_command_schedule(schedules_dir, schedule_id)
    path = schedules_dir / f"{schedule_id}.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text, encoding="utf-8-sig")

    intent = load_schedule(schedule_id, vault, check_registry=False)
    assert intent is not None
    assert intent.schedule_kind == "command"
    assert intent.allowed_workflow_task_types == []
    assert intent.allowed_command_ids == ["events.watch"]


@_register("list_schedules: BOM-prefixed command schedule does not warn or skip")
def test_bom_prefixed_command_schedule_does_not_warn_or_skip(tmp_path, capsys):
    vault = _make_test_vault(tmp_path)
    schedules_dir = vault / "runtime" / "schedules"
    schedule_id = "sch-events-watch-bom-list"
    _write_command_schedule(schedules_dir, schedule_id)
    path = schedules_dir / f"{schedule_id}.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text, encoding="utf-8-sig")

    schedules = list_schedules(vault, check_registry=False)
    captured = capsys.readouterr()
    assert schedule_id in {item.schedule_id for item in schedules}
    assert "skipping" not in captured.err


@_register("export: coordination workflows include bus preflight flags")
def test_export_coordination_command_flags(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(
        reg,
        "hermes_watch",
        task_type="coordination",
        ceiling="bus_result_only",
        runtime_adapter="hermes",
        coordination_sensitive=True,
    )
    _write_schedule(
        sched,
        "sch-hermes-watch-every-minute",
        workflow_id="hermes_watch",
        adapter="openclaw",
        allowed_types=["coordination"],
    )

    result = export_schedules_for_adapter("openclaw", vault)
    assert result[0]["command"] == (
        "chaseos run hermes_watch --adapter hermes --coordination-via runtime/agent_bus/"
    )


@_register("export: disabled schedules excluded by default (enabled_only=True)")
def test_export_excludes_disabled(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(reg, "wf_enabled")
    _write_workflow(reg, "wf_disabled")

    _write_schedule(sched, "sch-enabled", workflow_id="wf_enabled", enabled=True)
    _write_schedule(sched, "sch-disabled", workflow_id="wf_disabled", enabled=False)

    result = export_schedules_for_adapter("openclaw", vault)
    ids = [e["schedule_id"] for e in result]
    assert "sch-enabled" in ids
    assert "sch-disabled" not in ids, "Disabled schedule must be excluded by default"


@_register("export: disabled schedules included when enabled_only=False")
def test_export_includes_disabled_when_all(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(reg, "wf_enabled")
    _write_workflow(reg, "wf_disabled")

    _write_schedule(sched, "sch-enabled", workflow_id="wf_enabled", enabled=True)
    _write_schedule(sched, "sch-disabled", workflow_id="wf_disabled", enabled=False)

    result = export_schedules_for_adapter("openclaw", vault, enabled_only=False)
    ids = [e["schedule_id"] for e in result]
    assert "sch-enabled" in ids
    assert "sch-disabled" in ids, "Disabled schedule must appear when enabled_only=False"


@_register("export: duplicate workflow_id for same adapter raises ValueError")
def test_export_duplicate_workflow_raises(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(reg, "operator_today")

    # Two schedules targeting the same workflow for the same adapter = error
    _write_schedule(sched, "sch-today-0700", workflow_id="operator_today", adapter="openclaw",
                    cron_expression="0 7 * * 1-5")
    _write_schedule(sched, "sch-today-0800", workflow_id="operator_today", adapter="openclaw",
                    cron_expression="0 8 * * 1-5")

    try:
        export_schedules_for_adapter("openclaw", vault)
        assert False, "Should have raised ValueError for duplicate workflow"
    except ValueError as exc:
        assert "operator_today" in str(exc)
        assert "double-execution" in str(exc)


@_register("export: duplicate command_id for same adapter raises ValueError")
def test_export_duplicate_command_raises(tmp_path):
    vault = _make_test_vault(tmp_path)
    sched = vault / "runtime" / "schedules"

    _write_command_schedule(sched, "sch-events-watch-a")
    _write_command_schedule(sched, "sch-events-watch-b")

    try:
        export_schedules_for_adapter("openclaw", vault)
        assert False, "Should have raised ValueError for duplicate command schedule"
    except ValueError as exc:
        assert "events.watch" in str(exc)
        assert "double-execution" in str(exc)


@_register("export: cron_expression and timezone are included correctly")
def test_export_cron_fields(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(reg, "operator_today")
    _write_schedule(
        sched, "sch-today", workflow_id="operator_today",
        cron_expression="0 7 * * 1-5", timezone="America/New_York"
    )

    result = export_schedules_for_adapter("openclaw", vault)
    assert result[0]["cron_expression"] == "0 7 * * 1-5"
    assert result[0]["timezone"] == "America/New_York"


@_register("export: result is sorted by schedule_id (deterministic order)")
def test_export_sorted(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(reg, "wf_z")
    _write_workflow(reg, "wf_a")

    _write_schedule(sched, "sch-z", workflow_id="wf_z")
    _write_schedule(sched, "sch-a", workflow_id="wf_a")

    result = export_schedules_for_adapter("openclaw", vault)
    ids = [e["schedule_id"] for e in result]
    assert ids == sorted(ids), f"Export must be sorted by schedule_id: {ids}"


@_register("no duplicate schedule truth: live vault operator_today appears once")
def test_live_operator_today_appears_once():
    """Verify that the live vault has exactly one schedule for operator_today."""
    vault_root = Path(__file__).resolve().parents[2]
    if not (vault_root / "CLAUDE.md").exists():
        print("    (skipped — vault root not detected)")
        return

    schedules = list_schedules(vault_root, check_registry=False)
    operator_today_schedules = [
        s for s in schedules
        if s.workflow_id == "operator_today"
    ]
    count = len(operator_today_schedules)
    assert count == 1, (
        f"Expected exactly 1 schedule for operator_today, found {count}: "
        f"{[s.schedule_id for s in operator_today_schedules]}"
    )


@_register("no duplicate schedule truth: live vault operator_close_day appears once")
def test_live_operator_close_day_appears_once():
    """Verify that the live vault has exactly one schedule for operator_close_day."""
    vault_root = Path(__file__).resolve().parents[2]
    if not (vault_root / "CLAUDE.md").exists():
        return

    schedules = list_schedules(vault_root, check_registry=False)
    close_day_schedules = [
        s for s in schedules
        if s.workflow_id == "operator_close_day"
    ]
    count = len(close_day_schedules)
    assert count == 1, (
        f"Expected exactly 1 schedule for operator_close_day, found {count}: "
        f"{[s.schedule_id for s in close_day_schedules]}"
    )


@_register("live vault: all schedules target hermes as primary with openclaw as fallback")
def test_live_schedules_target_hermes():
    """Verify all live schedules target hermes as primary and declare openclaw as fallback."""
    vault_root = Path(__file__).resolve().parents[2]
    if not (vault_root / "CLAUDE.md").exists():
        return

    schedules = list_schedules(vault_root, check_registry=False)
    for s in schedules:
        assert s.runtime_adapter_target == "hermes", (
            f"Schedule {s.schedule_id} targets adapter '{s.runtime_adapter_target}' "
            f"but expected 'hermes' as primary executor for all ChaseOS schedules"
        )
        assert s.runtime_adapter_fallback == "openclaw", (
            f"Schedule {s.schedule_id} has fallback '{s.runtime_adapter_fallback}' "
            f"but expected 'openclaw' as declared fallback"
        )


@_register("live vault: export_schedules_for_adapter hermes returns operator_today and operator_close_day")
def test_live_export_hermes():
    """Verify hermes export returns both active briefing workflows as primary."""
    vault_root = Path(__file__).resolve().parents[2]
    if not (vault_root / "CLAUDE.md").exists():
        return

    result = export_schedules_for_adapter("hermes", vault_root)
    primary = [e for e in result if not e.get("is_fallback")]
    workflow_ids = {e["workflow_id"] for e in primary}

    assert "operator_today" in workflow_ids, (
        f"operator_today not in hermes primary export: {workflow_ids}"
    )
    assert "operator_close_day" in workflow_ids, (
        f"operator_close_day not in hermes primary export: {workflow_ids}"
    )

    # All entries in the hermes export should be primary (is_fallback=False)
    for entry in result:
        assert entry.get("is_fallback") is False, (
            f"Schedule {entry['schedule_id']} has is_fallback=True in hermes export — "
            f"hermes is the primary executor, all entries should be is_fallback=False"
        )


@_register("live vault: openclaw export returns schedules as fallback (is_fallback=True)")
def test_live_export_openclaw_as_fallback():
    """Verify openclaw export returns schedules with is_fallback=True."""
    vault_root = Path(__file__).resolve().parents[2]
    if not (vault_root / "CLAUDE.md").exists():
        return

    result = export_schedules_for_adapter("openclaw", vault_root)
    workflow_ids = {e["workflow_id"] for e in result}

    assert "operator_today" in workflow_ids, (
        f"operator_today not in openclaw fallback export: {workflow_ids}"
    )
    assert "operator_close_day" in workflow_ids, (
        f"operator_close_day not in openclaw fallback export: {workflow_ids}"
    )

    # All entries in the openclaw fallback export should have is_fallback=True
    for entry in result:
        assert entry.get("is_fallback") is True, (
            f"Schedule {entry['schedule_id']} has is_fallback=False in openclaw export — "
            f"openclaw is the fallback executor, all entries should be is_fallback=True"
        )


@_register("fallback: schedule returned in fallback adapter export with is_fallback=True")
def test_fallback_export_flag(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(reg, "wf_primary")
    _write_schedule(sched, "sch-primary", workflow_id="wf_primary", adapter="hermes")
    # Patch in fallback field
    path = sched / "sch-primary.yaml"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "runtime_adapter_target: hermes",
        "runtime_adapter_target: hermes\nruntime_adapter_fallback: openclaw",
    )
    path.write_text(text, encoding="utf-8")

    # Hermes export: schedule appears as primary (is_fallback=False)
    hermes_result = export_schedules_for_adapter("hermes", vault)
    assert len(hermes_result) == 1, f"Expected 1 entry in hermes export, got {len(hermes_result)}"
    assert hermes_result[0]["is_fallback"] is False, "Primary should have is_fallback=False"
    assert hermes_result[0]["schedule_id"] == "sch-primary"

    # OpenClaw export: schedule appears as fallback (is_fallback=True)
    openclaw_result = export_schedules_for_adapter("openclaw", vault)
    assert len(openclaw_result) == 1, f"Expected 1 entry in openclaw export, got {len(openclaw_result)}"
    assert openclaw_result[0]["is_fallback"] is True, "Fallback should have is_fallback=True"
    assert openclaw_result[0]["schedule_id"] == "sch-primary"


@_register("fallback: n8n adapter sees nothing when not primary or fallback")
def test_fallback_no_match(tmp_path):
    vault = _make_test_vault(tmp_path)
    reg = vault / "runtime" / "workflows" / "registry"
    sched = vault / "runtime" / "schedules"

    _write_workflow(reg, "wf_hermes_only")
    _write_schedule(sched, "sch-hermes-only", workflow_id="wf_hermes_only", adapter="hermes")
    path = sched / "sch-hermes-only.yaml"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "runtime_adapter_target: hermes",
        "runtime_adapter_target: hermes\nruntime_adapter_fallback: openclaw",
    )
    path.write_text(text, encoding="utf-8")

    # n8n is neither primary nor fallback — gets empty export
    n8n_result = export_schedules_for_adapter("n8n", vault)
    assert n8n_result == [], f"Expected empty list for n8n, got {n8n_result}"


@_register("live vault: openclaw export has no duplicate workflows (no double-execution risk)")
def test_live_no_duplicate_execution():
    """Verify no duplicate workflow targets exist in the live vault for openclaw."""
    vault_root = Path(__file__).resolve().parents[2]
    if not (vault_root / "CLAUDE.md").exists():
        return

    # This will raise ValueError if duplicates exist
    result = export_schedules_for_adapter("openclaw", vault_root)
    workflow_ids = [
        e["workflow_id"] for e in result
        if e.get("schedule_kind", "workflow") == "workflow"
    ]
    unique_ids = list(set(workflow_ids))
    assert len(workflow_ids) == len(unique_ids), (
        f"Duplicate workflow IDs in openclaw export: {workflow_ids}"
    )


@_register("live vault: sch-operator-today-0700 has correct cron and timezone")
def test_live_today_cron():
    vault_root = Path(__file__).resolve().parents[2]
    if not (vault_root / "CLAUDE.md").exists():
        return

    intent = load_schedule("sch-operator-today-0700", vault_root, check_registry=False)
    assert intent is not None, "sch-operator-today-0700 not found in live vault"
    assert intent.cadence.cron_expression == "0 7 * * 1-5"
    assert intent.cadence.timezone == "America/New_York"
    assert intent.enabled is True
    assert intent.runtime_adapter_target == "hermes", (
        f"Expected hermes as primary executor, got: {intent.runtime_adapter_target}"
    )
    assert intent.runtime_adapter_fallback == "openclaw", (
        f"Expected openclaw as fallback, got: {intent.runtime_adapter_fallback}"
    )


@_register("live vault: sch-operator-close-day-1900 has correct cron and timezone")
def test_live_close_day_cron():
    vault_root = Path(__file__).resolve().parents[2]
    if not (vault_root / "CLAUDE.md").exists():
        return

    intent = load_schedule("sch-operator-close-day-1900", vault_root, check_registry=False)
    assert intent is not None, "sch-operator-close-day-1900 not found in live vault"
    assert intent.cadence.cron_expression == "0 19 * * 1-5"
    assert intent.cadence.timezone == "America/New_York"
    assert intent.enabled is True
    assert intent.runtime_adapter_target == "hermes", (
        f"Expected hermes as primary executor, got: {intent.runtime_adapter_target}"
    )
    assert intent.runtime_adapter_fallback == "openclaw", (
        f"Expected openclaw as fallback, got: {intent.runtime_adapter_fallback}"
    )


@_register("MCP scope not broadened: schedule surfaces remain in DEFERRED/EXCLUDED only")
def test_mcp_scope_unchanged():
    """
    Verify schedule.intent and schedule.proposal are not registered as active
    MCP resources or tools — they must only appear in DEFERRED_SURFACES or
    EXCLUDED_SURFACES guard lists (which block them, not enable them).
    """
    vault_root = Path(__file__).resolve().parents[2]
    mcp_dir = vault_root / "runtime" / "mcp"
    if not mcp_dir.exists():
        print("    (skipped — runtime/mcp/ not present)")
        return

    # Patterns that indicate an actual registration, not a guard
    # We look for these only in contexts that would register the surface as active
    activation_patterns = [
        # If schedule surfaces appear as registered resource URIs or tool names
        # (not inside DEFERRED_SURFACES/EXCLUDED_SURFACES guard lists)
        "@server.resource",
        "@server.tool",
        "register_resource",
        "register_tool",
    ]

    for py_file in mcp_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8", errors="ignore")

        # Only flag schedule names if they appear near actual registrations
        if "schedule.intent" in content or "schedule.proposal" in content:
            # Check that the file is safety.py (guard file) or a docs/comment-only reference
            rel = py_file.relative_to(vault_root)
            # safety.py is the deferred-surfaces guard — allowed to mention these by name
            if py_file.name == "safety.py":
                continue
            # Any other file mentioning schedule surfaces is a potential scope violation
            assert False, (
                f"MCP scope concern: 'schedule.intent' or 'schedule.proposal' found in "
                f"{rel} — these must only appear in safety.py DEFERRED_SURFACES guard list"
            )


# ── Test Runner ────────────────────────────────────────────────────────────────

def _run_all() -> int:
    print()
    print("=" * 60)
    print("Phase 9 — OpenClaw Schedule Bridge Tests")
    print("=" * 60)

    import shutil
    failures = 0
    tmp_root = (Path(__file__).resolve().parent / "_tmp_tests").resolve()
    expected_parent = Path(__file__).resolve().parent.resolve()
    if tmp_root.parent != expected_parent:
        raise RuntimeError(f"Refusing unsafe test temp root: {tmp_root}")
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_root.mkdir()

    for index, (label, fn) in enumerate(_TESTS, start=1):
        try:
            import inspect
            sig = inspect.signature(fn)
            if "tmp_path" in sig.parameters:
                case_root = tmp_root / f"case_{index:02d}"
                case_root.mkdir()
                fn(case_root)
            else:
                fn()
            print(f"  PASS  {label}")
        except Exception as exc:
            print(f"  FAIL  {label}")
            print(f"        {type(exc).__name__}: {exc}")
            failures += 1

    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    print()
    total = len(_TESTS)
    passed = total - failures
    print(f"Results: {passed}/{total} passed")
    print("=" * 60)
    print()
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
