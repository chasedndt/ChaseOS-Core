"""
test_capabilities_router.py — Agent Bus Capability-Aware Routing Tests

Covers:
  - runtime/agent_bus/capabilities.py (manifest loader, discovery, eligibility)
  - runtime/agent_bus/router.py (liveness, route_task_type, stale detection)
  - runtime/agent_bus/bus.py additions (get_known_runtimes, create_task, reclaim_task)
  - Real openclaw/hermes capabilities.yaml files
"""
from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from runtime.agent_bus.capabilities import (
    RuntimeCapability,
    RuntimeCapabilities,
    load_runtime_capabilities,
    load_all_capabilities,
    get_eligible_runtimes,
    discover_runtime_names,
    CapabilityError,
)
from runtime.agent_bus.router import (
    route_task_type,
    get_stale_runtimes,
    get_runtime_liveness,
    RouteResult,
    RouterError,
)
from runtime.agent_bus.bus import (
    init_db,
    db_path,
    get_known_runtimes,
    create_task,
    reclaim_task,
    upsert_heartbeat,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    (v / "CLAUDE.md").write_text("# test", encoding="utf-8")
    (v / "runtime" / "agent_bus").mkdir(parents=True)
    return v


def _write_caps(vault: Path, runtime: str, content: str) -> None:
    d = vault / "runtime" / runtime
    d.mkdir(parents=True, exist_ok=True)
    (d / "capabilities.yaml").write_text(content, encoding="utf-8")


def _seed_task(vault: Path, *, task_id: str = "task-001", recipient: str = "OpenClaw",
               sender: str = "Hermes", status: str = "open",
               owner: str | None = None,
               created_at: str = "2026-04-24T00:00:00+00:00",
               updated_at: str = "2026-04-24T00:00:00+00:00") -> None:
    init_db(vault)
    conn = sqlite3.connect(db_path(vault))
    conn.execute("""
        INSERT INTO tasks (task_id, run_id, sender, recipient, intent, status, priority,
                           owner, request, expected_output, depends_on_json, artifacts_json,
                           notes, created_at, updated_at, expires_at)
        VALUES (?, 'run-001', ?, ?, 'TASK', ?, 'normal', ?, 'do thing', 'result', '[]', '[]',
                NULL, ?, ?, NULL)
    """, (task_id, sender, recipient, status, owner, created_at, updated_at))
    conn.commit()
    conn.close()


# ── RuntimeCapability ──────────────────────────────────────────────────────────

class TestRuntimeCapability:
    def test_basic_construction(self):
        cap = RuntimeCapability(task_type="operator-briefing", priority="primary")
        assert cap.task_type == "operator-briefing"
        assert cap.priority == "primary"
        assert cap.priority_rank == 0

    def test_secondary_priority_rank(self):
        cap = RuntimeCapability(task_type="review", priority="secondary")
        assert cap.priority_rank == 1

    def test_invalid_priority_raises(self):
        with pytest.raises(CapabilityError, match="priority"):
            RuntimeCapability(task_type="t", priority="ultra")

    def test_empty_task_type_raises(self):
        with pytest.raises(CapabilityError):
            RuntimeCapability(task_type="")


# ── RuntimeCapabilities ────────────────────────────────────────────────────────

class TestRuntimeCapabilities:
    def _make(self, handles=None):
        return RuntimeCapabilities(
            runtime_name="test", bus_name="Test",
            display_name="Test", description="desc",
            handles=handles or [
                RuntimeCapability("operator-briefing", "primary"),
                RuntimeCapability("review", "secondary"),
            ],
        )

    def test_can_handle_true(self):
        assert self._make().can_handle("operator-briefing")

    def test_can_handle_false(self):
        assert not self._make().can_handle("unknown-type")

    def test_priority_for_primary(self):
        assert self._make().priority_for("operator-briefing") == 0

    def test_priority_for_secondary(self):
        assert self._make().priority_for("review") == 1

    def test_priority_for_unknown(self):
        assert self._make().priority_for("nope") == 99


# ── Capability loader ──────────────────────────────────────────────────────────

class TestLoadRuntimeCapabilities:
    def _write(self, vault, runtime, content):
        _write_caps(vault, runtime, content)

    def test_loads_openclaw_style(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "openclaw", textwrap.dedent("""\
            runtime: openclaw
            bus_name: OpenClaw
            display_name: OpenClaw
            description: Primary synthesizer
            handles:
              - task_type: operator-briefing
                priority: primary
              - task_type: review
                priority: secondary
            max_concurrent_tasks: 3
            heartbeat_stale_seconds: 900
        """))
        caps = load_runtime_capabilities("openclaw", vault)
        assert caps.bus_name == "OpenClaw"
        assert caps.can_handle("operator-briefing")
        assert caps.priority_for("operator-briefing") == 0
        assert caps.max_concurrent_tasks == 3
        assert caps.heartbeat_stale_seconds == 900

    def test_missing_file_raises(self, tmp_path):
        vault = _vault(tmp_path)
        with pytest.raises(CapabilityError, match="No capabilities.yaml"):
            load_runtime_capabilities("nonexistent", vault)

    def test_string_shorthand_handle(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "test", "bus_name: Test\nhandles:\n  - operator-briefing\n")
        caps = load_runtime_capabilities("test", vault)
        assert caps.can_handle("operator-briefing")

    def test_missing_task_type_in_handle_raises(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "test", "bus_name: Test\nhandles:\n  - priority: primary\n")
        with pytest.raises(CapabilityError, match="task_type"):
            load_runtime_capabilities("test", vault)

    def test_invalid_yaml_raises(self, tmp_path):
        vault = _vault(tmp_path)
        d = vault / "runtime" / "badruntime"
        d.mkdir(parents=True)
        (d / "capabilities.yaml").write_text(":\n  invalid: [yaml", encoding="utf-8")
        with pytest.raises(CapabilityError):
            load_runtime_capabilities("badruntime", vault)

    def test_fallback_parser_without_pyyaml(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "openclaw", textwrap.dedent("""\
            runtime: openclaw
            bus_name: OpenClaw
            display_name: "OpenClaw"
            description: "Primary operator synthesizer"
            handles:
              - task_type: operator-briefing
                priority: primary
                notes: "operator_today and operator_close_day"
              - task_type: review
                priority: secondary
            max_concurrent_tasks: 3
            heartbeat_stale_seconds: 900
            priority_ceiling: normal
        """))
        import runtime.agent_bus.capabilities as caps_mod
        with patch.object(caps_mod, "yaml", None):
            caps = load_runtime_capabilities("openclaw", vault)
        assert caps.bus_name == "OpenClaw"
        assert caps.display_name == "OpenClaw"
        assert caps.description == "Primary operator synthesizer"
        assert caps.priority_for("operator-briefing") == 0
        assert caps.priority_for("review") == 1
        assert caps.max_concurrent_tasks == 3

    def test_fallback_parser_allows_nested_list_under_mapping(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "openclaw", textwrap.dedent("""\
            runtime: openclaw
            bus_name: OpenClaw
            handles:
              - task_type: operator-briefing
                priority: primary
            governance_notes:
              validated_by: "runtime/agent_bus/capabilities.py"
              forbidden_expansion:
                - credential_access
                - autonomous_canonical_writeback
        """))
        import runtime.agent_bus.capabilities as caps_mod
        with patch.object(caps_mod, "yaml", None):
            caps = load_runtime_capabilities("openclaw", vault)
        assert caps.bus_name == "OpenClaw"
        assert caps.can_handle("operator-briefing")

    def test_bus_name_falls_back_to_runtime(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "test", "handles:\n  - operator-briefing\n")
        caps = load_runtime_capabilities("test", vault)
        assert caps.bus_name  # should be non-empty


# ── Discovery ──────────────────────────────────────────────────────────────────

class TestDiscoverRuntimeNames:
    def test_finds_runtimes_with_yaml(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "openclaw", "bus_name: OpenClaw\nhandles: []\n")
        _write_caps(vault, "hermes", "bus_name: Hermes\nhandles: []\n")
        names = discover_runtime_names(vault)
        assert "openclaw" in names
        assert "hermes" in names

    def test_ignores_dirs_without_yaml(self, tmp_path):
        vault = _vault(tmp_path)
        (vault / "runtime" / "orphan").mkdir(parents=True)
        names = discover_runtime_names(vault)
        assert "orphan" not in names

    def test_empty_runtime_dir(self, tmp_path):
        vault = _vault(tmp_path)
        names = discover_runtime_names(vault)
        assert "agent_bus" not in names  # agent_bus has no capabilities.yaml


# ── get_eligible_runtimes ──────────────────────────────────────────────────────

class TestGetEligibleRuntimes:
    def _setup(self, vault):
        _write_caps(vault, "openclaw", textwrap.dedent("""\
            bus_name: OpenClaw
            handles:
              - task_type: operator-briefing
                priority: primary
              - task_type: review
                priority: secondary
        """))
        _write_caps(vault, "hermes", textwrap.dedent("""\
            bus_name: Hermes
            handles:
              - task_type: review
                priority: primary
              - task_type: planning
                priority: primary
        """))

    def test_single_eligible_runtime(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        eligible = get_eligible_runtimes("planning", vault)
        assert eligible == ["Hermes"]

    def test_multiple_eligible_sorted_by_priority(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        # Hermes=primary, OpenClaw=secondary for review
        eligible = get_eligible_runtimes("review", vault)
        assert eligible[0] == "Hermes"
        assert "OpenClaw" in eligible

    def test_no_eligible_runtime(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        eligible = get_eligible_runtimes("unknown-task-type", vault)
        assert eligible == []

    def test_exclusive_capability(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        eligible = get_eligible_runtimes("operator-briefing", vault)
        assert "OpenClaw" in eligible
        assert "Hermes" not in eligible


# ── get_known_runtimes ─────────────────────────────────────────────────────────

class TestGetKnownRuntimes:
    def test_fallback_without_vault(self):
        known = get_known_runtimes(None)
        assert "Hermes" in known
        assert "OpenClaw" in known

    def test_uses_capabilities_when_available(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "openclaw", "bus_name: OpenClaw\nhandles: []\n")
        known = get_known_runtimes(vault)
        assert "OpenClaw" in known

    def test_fallback_on_empty_vault(self, tmp_path):
        vault = _vault(tmp_path)
        known = get_known_runtimes(vault)
        # No capabilities.yaml → fallback to hard-coded set
        assert "Hermes" in known or "OpenClaw" in known


# ── create_task ────────────────────────────────────────────────────────────────

class TestCreateTask:
    def test_creates_task_successfully(self, tmp_path):
        vault = _vault(tmp_path)
        result = create_task(
            vault,
            sender="Hermes",
            recipient="OpenClaw",
            request="Run operator_today",
            expected_output="Brief in 07_LOGS/Operator-Briefs/",
        )
        assert result["created"] is True
        assert result["task_id"].startswith("task-")
        assert result["sender"] == "Hermes"
        assert result["recipient"] == "OpenClaw"

    def test_invalid_sender_rejected(self, tmp_path):
        vault = _vault(tmp_path)
        result = create_task(
            vault,
            sender="UnknownRuntime",
            recipient="OpenClaw",
            request="do thing",
            expected_output="result",
        )
        assert result["created"] is False
        assert "Unknown sender" in result["reason"]

    def test_invalid_recipient_rejected(self, tmp_path):
        vault = _vault(tmp_path)
        result = create_task(
            vault,
            sender="Hermes",
            recipient="Ghost",
            request="do thing",
            expected_output="result",
        )
        assert result["created"] is False

    def test_invalid_intent_rejected(self, tmp_path):
        vault = _vault(tmp_path)
        result = create_task(
            vault,
            sender="Hermes",
            recipient="OpenClaw",
            intent="INVALID",
            request="do thing",
            expected_output="result",
        )
        assert result["created"] is False
        assert "intent" in result["reason"].lower()

    def test_task_appears_in_list(self, tmp_path):
        vault = _vault(tmp_path)
        result = create_task(
            vault,
            sender="Hermes",
            recipient="OpenClaw",
            request="verify acquisition pack",
            expected_output="confirmation",
        )
        assert result["created"] is True
        from runtime.agent_bus.bus import list_tasks
        tasks = list_tasks(vault, recipient="OpenClaw", status="open")
        ids = [t["task_id"] for t in tasks]
        assert result["task_id"] in ids

    def test_custom_task_id(self, tmp_path):
        vault = _vault(tmp_path)
        result = create_task(
            vault,
            task_id="my-custom-task-001",
            sender="OpenClaw",
            recipient="Hermes",
            request="review digest",
            expected_output="review result",
        )
        assert result["created"] is True
        assert result["task_id"] == "my-custom-task-001"


# ── reclaim_task ───────────────────────────────────────────────────────────────

class TestReclaimTask:
    def test_reclaims_task_from_stale_owner(self, tmp_path):
        vault = _vault(tmp_path)
        _seed_task(vault, task_id="task-r1", recipient="OpenClaw",
                   sender="Hermes", status="claimed", owner="OpenClaw")
        result = reclaim_task(vault, task_id="task-r1", new_runtime="Hermes")
        assert result["reclaimed"] is True
        assert result["previous_owner"] == "OpenClaw"
        assert result["new_recipient"] == "Hermes"

    def test_task_status_reset_to_open(self, tmp_path):
        vault = _vault(tmp_path)
        _seed_task(vault, task_id="task-r2", recipient="OpenClaw",
                   sender="Hermes", status="in_progress", owner="OpenClaw")
        reclaim_task(vault, task_id="task-r2", new_runtime="Hermes")
        from runtime.agent_bus.bus import list_tasks
        tasks = list_tasks(vault, status="open")
        ids = [t["task_id"] for t in tasks]
        assert "task-r2" in ids

    def test_cannot_reclaim_own_task(self, tmp_path):
        vault = _vault(tmp_path)
        _seed_task(vault, task_id="task-r3", recipient="OpenClaw",
                   sender="Hermes", status="claimed", owner="OpenClaw")
        result = reclaim_task(vault, task_id="task-r3", new_runtime="OpenClaw")
        assert result["reclaimed"] is False
        assert "already owned" in result["reason"]

    def test_cannot_reclaim_done_task(self, tmp_path):
        vault = _vault(tmp_path)
        _seed_task(vault, task_id="task-r4", recipient="OpenClaw",
                   sender="Hermes", status="done", owner="OpenClaw")
        result = reclaim_task(vault, task_id="task-r4", new_runtime="Hermes")
        assert result["reclaimed"] is False
        assert "not reclaimable" in result["reason"]

    def test_unknown_runtime_rejected(self, tmp_path):
        vault = _vault(tmp_path)
        _seed_task(vault, task_id="task-r5")
        result = reclaim_task(vault, task_id="task-r5", new_runtime="Ghost")
        assert result["reclaimed"] is False

    def test_missing_task_returns_false(self, tmp_path):
        vault = _vault(tmp_path)
        init_db(vault)
        result = reclaim_task(vault, task_id="nonexistent", new_runtime="Hermes")
        assert result["reclaimed"] is False


# ── Router — liveness ──────────────────────────────────────────────────────────

class TestGetRuntimeLiveness:
    def _setup_caps(self, vault):
        _write_caps(vault, "openclaw", "bus_name: OpenClaw\nheartbeat_stale_seconds: 900\nhandles: []\n")
        _write_caps(vault, "hermes", "bus_name: Hermes\nheartbeat_stale_seconds: 900\nhandles: []\n")

    def test_no_heartbeat_is_stale(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup_caps(vault)
        liveness = get_runtime_liveness(vault)
        assert liveness["OpenClaw"].is_stale is True
        assert liveness["Hermes"].is_stale is True

    def test_fresh_heartbeat_is_not_stale(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup_caps(vault)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        upsert_heartbeat(vault, runtime="OpenClaw", status="idle", health="ok", now_iso=now)
        liveness = get_runtime_liveness(vault)
        assert liveness["OpenClaw"].is_stale is False

    def test_stale_override_threshold(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup_caps(vault)
        # Heartbeat from well in the past
        old_ts = "2020-01-01T00:00:00+00:00"
        upsert_heartbeat(vault, runtime="OpenClaw", status="idle", health="ok", now_iso=old_ts)
        liveness = get_runtime_liveness(vault, stale_override_seconds=10)
        assert liveness["OpenClaw"].is_stale is True

    def test_freshest_instance_heartbeat_keeps_runtime_live(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup_caps(vault)
        upsert_heartbeat(vault, runtime="OpenClaw", status="idle", health="ok", now_iso="2020-01-01T00:00:00+00:00")
        upsert_heartbeat(
            vault,
            runtime="OpenClaw",
            runtime_instance_id="discord-thread-1",
            heartbeat_scope="instance",
            control_surface="discord",
            control_surface_key="discord:chan:thread-1",
            status="busy",
            health="ok",
            now_iso="2099-01-01T00:00:00+00:00",
        )
        liveness = get_runtime_liveness(vault)
        assert liveness["OpenClaw"].is_stale is False
        assert liveness["OpenClaw"].status == "busy"

    def test_invalid_runtime_heartbeat_does_not_override_fresh_instance(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup_caps(vault)
        upsert_heartbeat(
            vault,
            runtime="OpenClaw",
            runtime_instance_id="discord-thread-2",
            heartbeat_scope="instance",
            control_surface="discord",
            control_surface_key="discord:chan:thread-2",
            status="idle",
            health="ok",
            now_iso="2099-01-01T00:00:00+00:00",
        )
        conn = sqlite3.connect(db_path(vault))
        conn.execute(
            "INSERT OR REPLACE INTO heartbeats (heartbeat_key, runtime, runtime_instance_id, heartbeat_scope, control_surface, control_surface_key, status, current_task_id, health, summary, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("OpenClaw", "OpenClaw", None, "runtime", None, None, "idle", None, "ok", "broken", "not-a-date"),
        )
        conn.commit()
        conn.close()
        liveness = get_runtime_liveness(vault)
        assert liveness["OpenClaw"].is_stale is False
        assert liveness["OpenClaw"].last_seen == "2099-01-01T00:00:00+00:00"


# ── Router — route_task_type ───────────────────────────────────────────────────

class TestRouteTaskType:
    def _setup(self, vault):
        _write_caps(vault, "openclaw", textwrap.dedent("""\
            bus_name: OpenClaw
            heartbeat_stale_seconds: 900
            handles:
              - task_type: operator-briefing
                priority: primary
              - task_type: review
                priority: secondary
        """))
        _write_caps(vault, "hermes", textwrap.dedent("""\
            bus_name: Hermes
            heartbeat_stale_seconds: 900
            handles:
              - task_type: review
                priority: primary
              - task_type: planning
                priority: primary
        """))

    def test_no_eligible_runtime(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        result = route_task_type("unknown-task", vault)
        assert result.recommended is None
        assert result.eligible_runtimes == []

    def test_no_live_runtimes_returns_none_recommended(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        # No heartbeats → all stale
        result = route_task_type("operator-briefing", vault)
        assert result.recommended is None
        assert "OpenClaw" in result.stale_runtimes

    def test_live_runtime_is_recommended(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        upsert_heartbeat(vault, runtime="OpenClaw", status="idle", health="ok", now_iso=now)
        result = route_task_type("operator-briefing", vault)
        assert result.recommended == "OpenClaw"
        assert "OpenClaw" in result.live_runtimes

    def test_highest_priority_live_wins(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        upsert_heartbeat(vault, runtime="OpenClaw", status="idle", health="ok", now_iso=now)
        upsert_heartbeat(vault, runtime="Hermes", status="idle", health="ok", now_iso=now)
        # Hermes=primary for review, OpenClaw=secondary
        result = route_task_type("review", vault)
        assert result.recommended == "Hermes"

    def test_stale_runtime_not_recommended(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        old = "2020-01-01T00:00:00+00:00"
        # OpenClaw stale, Hermes fresh — but hermes only handles review+planning
        upsert_heartbeat(vault, runtime="OpenClaw", status="idle", health="ok", now_iso=old)
        upsert_heartbeat(vault, runtime="Hermes", status="idle", health="ok", now_iso=now)
        result = route_task_type("operator-briefing", vault)
        # Only OpenClaw can do operator-briefing, but it's stale
        assert result.recommended is None
        assert "OpenClaw" in result.stale_runtimes

    def test_result_fields_populated(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        result = route_task_type("review", vault)
        assert isinstance(result, RouteResult)
        assert result.task_type == "review"
        assert isinstance(result.eligible_runtimes, list)
        assert isinstance(result.all_registered, list)
        assert isinstance(result.reason, str)


# ── get_stale_runtimes ─────────────────────────────────────────────────────────

class TestGetStaleRuntimes:
    def test_no_heartbeats_all_stale(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "openclaw", "bus_name: OpenClaw\nheartbeat_stale_seconds: 900\nhandles: []\n")
        _write_caps(vault, "hermes", "bus_name: Hermes\nheartbeat_stale_seconds: 900\nhandles: []\n")
        stale = get_stale_runtimes(vault)
        assert "OpenClaw" in stale
        assert "Hermes" in stale

    def test_fresh_heartbeat_not_stale(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "openclaw", "bus_name: OpenClaw\nheartbeat_stale_seconds: 900\nhandles: []\n")
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        upsert_heartbeat(vault, runtime="OpenClaw", status="idle", health="ok", now_iso=now)
        stale = get_stale_runtimes(vault)
        assert "OpenClaw" not in stale

    def test_threshold_override(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "openclaw", "bus_name: OpenClaw\nheartbeat_stale_seconds: 9999\nhandles: []\n")
        # 9999s config threshold — override with 1s
        from datetime import datetime, timezone
        old = "2020-01-01T00:00:00+00:00"
        upsert_heartbeat(vault, runtime="OpenClaw", status="idle", health="ok", now_iso=old)
        stale = get_stale_runtimes(vault, threshold_seconds=1)
        assert "OpenClaw" in stale

    def test_fresh_instance_heartbeat_prevents_runtime_from_being_marked_stale(self, tmp_path):
        vault = _vault(tmp_path)
        _write_caps(vault, "openclaw", "bus_name: OpenClaw\nheartbeat_stale_seconds: 900\nhandles: []\n")
        upsert_heartbeat(vault, runtime="OpenClaw", status="idle", health="ok", now_iso="2020-01-01T00:00:00+00:00")
        upsert_heartbeat(
            vault,
            runtime="OpenClaw",
            runtime_instance_id="discord-thread-fresh",
            heartbeat_scope="instance",
            control_surface="discord",
            control_surface_key="discord:ops:thread-fresh",
            status="busy",
            health="ok",
            now_iso="2099-01-01T00:00:00+00:00",
        )
        stale = get_stale_runtimes(vault)
        assert "OpenClaw" not in stale


# ── Real capabilities.yaml validation ─────────────────────────────────────────

class TestRealCapabilityFiles:
    def _vault_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def test_openclaw_caps_load(self):
        caps = load_runtime_capabilities("openclaw", self._vault_root())
        assert caps.bus_name == "OpenClaw"
        assert len(caps.handles) >= 1

    def test_hermes_caps_load(self):
        caps = load_runtime_capabilities("hermes", self._vault_root())
        assert caps.bus_name == "Hermes"
        assert len(caps.handles) >= 1

    def test_openclaw_handles_operator_briefing(self):
        caps = load_runtime_capabilities("openclaw", self._vault_root())
        assert caps.can_handle("operator-briefing")

    def test_hermes_handles_review(self):
        caps = load_runtime_capabilities("hermes", self._vault_root())
        assert caps.can_handle("review")

    def test_openclaw_is_primary_for_scheduled_briefing(self):
        caps = load_runtime_capabilities("openclaw", self._vault_root())
        assert caps.priority_for("scheduled-briefing") == 0  # primary

    def test_hermes_is_primary_for_planning(self):
        caps = load_runtime_capabilities("hermes", self._vault_root())
        assert caps.priority_for("planning") == 0  # primary

    def test_all_caps_loads_both(self):
        all_caps = load_all_capabilities(self._vault_root())
        assert "openclaw" in all_caps
        assert "hermes" in all_caps

    def test_real_route_operator_briefing(self):
        """operator-briefing → OpenClaw is eligible (liveness not checked here)."""
        eligible = get_eligible_runtimes("operator-briefing", self._vault_root())
        assert "OpenClaw" in eligible

    def test_real_route_review_hermes_primary(self):
        eligible = get_eligible_runtimes("review", self._vault_root())
        assert len(eligible) >= 1
        # Hermes should be first (primary)
        assert eligible[0] == "Hermes"


# ── Router — concurrent load (max_concurrent_tasks) ───────────────────────────

class TestConcurrentLoadEnforcement:
    def _setup(self, vault):
        _write_caps(vault, "openclaw", textwrap.dedent("""\
            bus_name: OpenClaw
            heartbeat_stale_seconds: 900
            max_concurrent_tasks: 2
            handles:
              - task_type: operator-briefing
                priority: primary
              - task_type: review
                priority: secondary
        """))
        _write_caps(vault, "hermes", textwrap.dedent("""\
            bus_name: Hermes
            heartbeat_stale_seconds: 900
            max_concurrent_tasks: 2
            handles:
              - task_type: review
                priority: primary
              - task_type: planning
                priority: primary
        """))

    def _set_fresh_heartbeat(self, vault, runtime):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        upsert_heartbeat(vault, runtime=runtime, status="idle", health="ok", now_iso=now)

    def test_available_runtime_recommended(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        self._set_fresh_heartbeat(vault, "OpenClaw")
        # No active tasks — OpenClaw is available
        result = route_task_type("operator-briefing", vault)
        assert result.recommended == "OpenClaw"
        assert "OpenClaw" in result.available_runtimes
        assert "OpenClaw" not in result.at_capacity_runtimes

    def test_at_capacity_runtime_skipped_when_alternative_available(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        self._set_fresh_heartbeat(vault, "OpenClaw")
        self._set_fresh_heartbeat(vault, "Hermes")
        # Fill Hermes to capacity for review (2/2)
        _seed_task(vault, task_id="r-a", recipient="Hermes", sender="OpenClaw",
                   status="claimed", owner="Hermes")
        _seed_task(vault, task_id="r-b", recipient="Hermes", sender="OpenClaw",
                   status="in_progress", owner="Hermes")
        result = route_task_type("review", vault)
        # Hermes primary but at capacity — OpenClaw secondary but available
        assert result.recommended == "OpenClaw"
        assert "Hermes" in result.at_capacity_runtimes
        assert "OpenClaw" in result.available_runtimes

    def test_at_capacity_runtime_listed(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        self._set_fresh_heartbeat(vault, "OpenClaw")
        # Fill OpenClaw to capacity (2/2)
        _seed_task(vault, task_id="c-1", recipient="OpenClaw", sender="Hermes",
                   status="claimed", owner="OpenClaw")
        _seed_task(vault, task_id="c-2", recipient="OpenClaw", sender="Hermes",
                   status="in_progress", owner="OpenClaw")
        result = route_task_type("operator-briefing", vault)
        assert "OpenClaw" in result.at_capacity_runtimes
        assert "OpenClaw" not in result.available_runtimes

    def test_all_live_at_capacity_still_recommends(self, tmp_path):
        """If all live runtimes are at capacity, recommend the first live one with a warning."""
        vault = _vault(tmp_path)
        self._setup(vault)
        self._set_fresh_heartbeat(vault, "Hermes")
        # Fill Hermes to capacity (2/2) for review
        _seed_task(vault, task_id="ac-1", recipient="Hermes", sender="OpenClaw",
                   status="claimed", owner="Hermes")
        _seed_task(vault, task_id="ac-2", recipient="Hermes", sender="OpenClaw",
                   status="in_progress", owner="Hermes")
        result = route_task_type("planning", vault)
        # planning → only Hermes handles it; Hermes is live but at capacity
        assert result.recommended == "Hermes"
        assert "at capacity" in result.reason.lower()
        assert "Hermes" in result.at_capacity_runtimes

    def test_result_has_new_fields(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        result = route_task_type("review", vault)
        assert hasattr(result, "at_capacity_runtimes")
        assert hasattr(result, "available_runtimes")
        assert isinstance(result.at_capacity_runtimes, list)
        assert isinstance(result.available_runtimes, list)

    def test_under_capacity_not_flagged(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup(vault)
        self._set_fresh_heartbeat(vault, "OpenClaw")
        # Only 1 task owned (max is 2) — still available
        _seed_task(vault, task_id="uc-1", recipient="OpenClaw", sender="Hermes",
                   status="claimed", owner="OpenClaw")
        result = route_task_type("operator-briefing", vault)
        assert "OpenClaw" in result.available_runtimes
        assert "OpenClaw" not in result.at_capacity_runtimes


# ── Bus — priority_ceiling enforcement ────────────────────────────────────────

class TestPriorityCeilingEnforcement:
    def _setup_caps(self, vault):
        """OpenClaw: ceiling=normal. Hermes: ceiling=high."""
        _write_caps(vault, "openclaw", textwrap.dedent("""\
            bus_name: OpenClaw
            priority_ceiling: normal
            handles:
              - task_type: operator-briefing
                priority: primary
        """))
        _write_caps(vault, "hermes", textwrap.dedent("""\
            bus_name: Hermes
            priority_ceiling: high
            handles:
              - task_type: review
                priority: primary
        """))

    def test_task_within_ceiling_allowed(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup_caps(vault)
        result = create_task(
            vault,
            sender="Hermes",
            recipient="OpenClaw",
            request="run briefing",
            expected_output="brief",
            priority="normal",
        )
        assert result["created"] is True

    def test_low_priority_within_ceiling_allowed(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup_caps(vault)
        result = create_task(
            vault,
            sender="Hermes",
            recipient="OpenClaw",
            request="low-urgency task",
            expected_output="result",
            priority="low",
        )
        assert result["created"] is True

    def test_task_exceeds_ceiling_rejected(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup_caps(vault)
        # OpenClaw ceiling=normal; sending "high" should be rejected
        result = create_task(
            vault,
            sender="Hermes",
            recipient="OpenClaw",
            request="urgent briefing",
            expected_output="brief",
            priority="high",
        )
        assert result["created"] is False
        assert "priority_ceiling" in result["reason"]

    def test_critical_exceeds_normal_ceiling(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup_caps(vault)
        result = create_task(
            vault,
            sender="Hermes",
            recipient="OpenClaw",
            request="emergency task",
            expected_output="result",
            priority="critical",
        )
        assert result["created"] is False

    def test_high_within_hermes_ceiling(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup_caps(vault)
        # Hermes ceiling=high; "high" task to Hermes should be allowed
        result = create_task(
            vault,
            sender="OpenClaw",
            recipient="Hermes",
            request="urgent review",
            expected_output="review result",
            priority="high",
        )
        assert result["created"] is True

    def test_critical_exceeds_hermes_ceiling(self, tmp_path):
        vault = _vault(tmp_path)
        self._setup_caps(vault)
        # Hermes ceiling=high; "critical" should be rejected
        result = create_task(
            vault,
            sender="OpenClaw",
            recipient="Hermes",
            request="critical review",
            expected_output="result",
            priority="critical",
        )
        assert result["created"] is False

    def test_ceiling_check_skipped_without_vault(self):
        # vault_root=None → no caps → ceiling check is skipped entirely
        result = create_task(
            None,
            sender="Hermes",
            recipient="OpenClaw",
            request="test",
            expected_output="result",
            priority="critical",
        )
        # Will likely fail at SQL schema enforcement, but NOT at ceiling check
        assert "priority_ceiling" not in result.get("reason", "")

    def test_real_openclaw_ceiling_is_normal(self):
        """Real capabilities.yaml: OpenClaw priority_ceiling=normal; high task rejected."""
        vault_root = Path(__file__).resolve().parents[2]
        result = create_task(
            vault_root,
            sender="Hermes",
            recipient="OpenClaw",
            request="high-priority test",
            expected_output="result",
            priority="high",
        )
        assert result["created"] is False
        assert "priority_ceiling" in result["reason"]

    def test_real_hermes_ceiling_is_high(self):
        """Real capabilities.yaml: Hermes priority_ceiling=high; high task allowed."""
        vault_root = Path(__file__).resolve().parents[2]
        result = create_task(
            vault_root,
            sender="OpenClaw",
            recipient="Hermes",
            request="high-priority review",
            expected_output="result",
            priority="high",
        )
        # Should be created (ceiling=high allows high)
        assert result["created"] is True
