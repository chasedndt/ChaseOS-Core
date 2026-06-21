"""
test_agent_bus.py — ChaseOS dual-runtime coordination bus tests

Verifies:
- SQLite schema/bootstrap initialization succeeds
- task listing and summary work from a temporary vault
- claiming a task records owner/status and emits an event
- claiming fails cleanly for wrong runtime recipients
- heartbeat upserts work
- stale-task marking expires old work and records events
- watch_once can optionally claim the next open task for a runtime

Run:
    python -m pytest runtime/agent_bus/test_agent_bus.py -v
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.agent_bus.bus import (
    cleanup_tasks,
    create_task,
    evaluate_task_claimability,
    init_db,
    list_tasks,
    claim_task,
    update_task_status,
    upsert_heartbeat,
    mark_stale_tasks,
    translate_discord_control_plane_request,
    watch_once,
    run_watch_loop,
    db_path,
)


def test_task_packet_schema_runtime_identity_is_generic():
    schema = json.loads((Path(__file__).with_name("task-packet.schema.json")).read_text(encoding="utf-8"))

    assert schema["properties"]["from"] == {"type": "string", "minLength": 1}
    assert schema["properties"]["to"] == {"type": "string", "minLength": 1}
    assert schema["properties"]["owner"] == {"type": ["string", "null"]}
    assert schema["properties"]["execution_constraints"]["properties"]["allow_shell_commands"] == {"type": "boolean"}
    assert schema["properties"]["execution_constraints"]["properties"]["allow_live_subprocess"] == {"type": "boolean"}


def test_event_schema_runtime_identity_is_generic():
    schema = json.loads((Path(__file__).with_name("event.schema.json")).read_text(encoding="utf-8"))

    assert schema["properties"]["from"] == {"type": "string", "minLength": 1}


def test_create_task_persists_execution_constraints():
    root = Path(".codex_tmp_test") / f"agent-bus-execution-constraints-{uuid.uuid4().hex}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        vault = _make_test_vault(root, extra_runtimes=[("codex", "Codex")])
        init_db(vault)

        result = create_task(
            vault,
            sender="OpenClaw",
            recipient="Codex",
            request="Inspect without shell.",
            expected_output="Return a blocked/proposal artifact.",
            execution_constraints={
                "allow_shell_commands": False,
                "allow_live_subprocess": False,
                "write_policy": "none",
            },
        )

        assert result["created"] is True
        tasks = list_tasks(vault, recipient="Codex")
        assert tasks[0]["execution_constraints"] == {
            "allow_shell_commands": False,
            "allow_live_subprocess": False,
            "write_policy": "none",
            "allowed_write_paths": [],
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_create_task_rejects_invalid_execution_constraints():
    root = Path(".codex_tmp_test") / f"agent-bus-invalid-execution-constraints-{uuid.uuid4().hex}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        vault = _make_test_vault(root, extra_runtimes=[("codex", "Codex")])
        init_db(vault)

        result = create_task(
            vault,
            sender="OpenClaw",
            recipient="Codex",
            request="Inspect.",
            expected_output="Return result.",
            execution_constraints={"allow_shell_commands": "false"},
        )

        assert result["created"] is False
        assert "allow_shell_commands must be a boolean" in result["reason"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _make_test_vault(tmp_path: Path, extra_runtimes: list[tuple[str, str]] | None = None) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "CLAUDE.md").write_text("# test vault", encoding="utf-8")
    (vault / "runtime" / "agent_bus").mkdir(parents=True)
    (vault / ".chaseos").mkdir(parents=True)
    (vault / ".chaseos" / "discord_instance_bindings.yaml").write_text(
        """
schema_version: "1.0"
primary_channels:
  control_plane_routing:
    id: "1493226873080119397"
    name: "chaseos-ops"
    channel_class: control-plane-routing
    bound: true
    interactive_eligible_runtimes:
      - openclaw
      - hermes
  runtime_chat_hermes:
    id: "1493226848409358426"
    name: "hermes-chat"
    channel_class: runtime-chat
    bound: true
    interactive_eligible_runtimes:
      - hermes
  approvals:
    id: "1493337616467230910"
    name: "approvals"
    channel_class: approvals
    bound: true
    interactive_eligible_runtimes:
      - operator_only
supplemental_channels: {}
        """.strip()
        + "\n",
        encoding="utf-8",
    )
    runtime_pairs = [("openclaw", "OpenClaw"), ("hermes", "Hermes")]
    if extra_runtimes:
        runtime_pairs.extend(extra_runtimes)
    for runtime_name, bus_name in runtime_pairs:
        runtime_dir = vault / "runtime" / runtime_name
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "capabilities.yaml").write_text(
            f"bus_name: {bus_name}\nheartbeat_stale_seconds: 900\nhandles:\n  - task_type: review\n",
            encoding="utf-8",
        )
    return vault


def _seed_task(
    vault: Path,
    *,
    task_id: str = "task-001",
    recipient: str = "OpenClaw",
    sender: str = "Hermes",
    status: str = "open",
    owner: str | None = None,
    request: str = "Do the thing",
    created_at: str = "2026-04-24T00:00:00+00:00",
    updated_at: str = "2026-04-24T00:00:00+00:00",
    ingress_context: dict | None = None,
    work_fingerprint: str | None = None,
) -> None:
    init_db(vault)
    conn = sqlite3.connect(db_path(vault))
    conn.execute(
        """
        INSERT INTO tasks (
            task_id, run_id, reply_to, sender, recipient, intent, status, priority, owner,
            request, expected_output, depends_on_json, artifacts_json, ingress_context_json, work_fingerprint, notes, created_at, updated_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            "run-001",
            None,
            sender,
            recipient,
            "TASK",
            status,
            "normal",
            owner,
            request,
            "Return result",
            json.dumps([]),
            json.dumps([]),
            json.dumps(ingress_context or {}),
            work_fingerprint,
            None,
            created_at,
            updated_at,
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO events (
            event_id, task_id, run_id, sender, event_type, message, artifacts_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"evt-{task_id}-created",
            task_id,
            "run-001",
            sender,
            "created",
            "task created",
            json.dumps([]),
            created_at,
        ),
    )
    conn.commit()
    conn.close()


def test_init_db_creates_sqlite_store(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    path = init_db(vault)
    assert path.exists(), "agent bus sqlite file should be created"


def test_init_db_migrates_legacy_runtime_constraints_for_future_runtime(tmp_path: Path):
    vault = _make_test_vault(tmp_path, extra_runtimes=[("sentinel", "Sentinel")])
    db = vault / "runtime" / "agent_bus" / "agent_bus.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE tasks (
          task_id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL,
          reply_to TEXT,
          sender TEXT NOT NULL CHECK (sender IN ('Hermes', 'OpenClaw')),
          recipient TEXT NOT NULL CHECK (recipient IN ('Hermes', 'OpenClaw')),
          intent TEXT NOT NULL CHECK (intent IN ('TASK', 'RESULT', 'BLOCKER', 'REVIEW', 'QUESTION', 'NOTICE')),
          status TEXT NOT NULL CHECK (status IN ('open', 'claimed', 'in_progress', 'blocked', 'review', 'done', 'cancelled', 'expired')),
          priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'critical')),
          owner TEXT CHECK (owner IN ('Hermes', 'OpenClaw')),
          request TEXT NOT NULL,
          expected_output TEXT NOT NULL,
          depends_on_json TEXT NOT NULL DEFAULT '[]',
          artifacts_json TEXT NOT NULL DEFAULT '[]',
          notes TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          expires_at TEXT
        );
        CREATE TABLE events (
          event_id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL,
          run_id TEXT NOT NULL,
          sender TEXT NOT NULL CHECK (sender IN ('Hermes', 'OpenClaw')),
          event_type TEXT NOT NULL CHECK (event_type IN ('created', 'claimed', 'started', 'blocked', 'review_requested', 'review_completed', 'result_attached', 'completed', 'cancelled', 'expired', 'notice')),
          message TEXT NOT NULL,
          artifacts_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL
        );
        CREATE TABLE heartbeats (
          runtime TEXT PRIMARY KEY CHECK (runtime IN ('Hermes', 'OpenClaw')),
          status TEXT NOT NULL CHECK (status IN ('idle', 'busy', 'blocked', 'offline')),
          current_task_id TEXT,
          health TEXT NOT NULL CHECK (health IN ('ok', 'degraded', 'error')),
          summary TEXT,
          last_seen TEXT NOT NULL
        );
        CREATE TABLE locks (
          lock_name TEXT PRIMARY KEY,
          owner_runtime TEXT NOT NULL CHECK (owner_runtime IN ('Hermes', 'OpenClaw')),
          acquired_at TEXT NOT NULL,
          expires_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    init_db(vault)
    result = upsert_heartbeat(vault, runtime="Sentinel", status="idle", health="ok", summary="migrated")

    assert result["runtime"] == "Sentinel"


def test_list_tasks_filters_by_recipient(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(vault, task_id="task-openclaw", recipient="OpenClaw")
    _seed_task(vault, task_id="task-hermes", recipient="Hermes")

    openclaw_tasks = list_tasks(vault, recipient="OpenClaw")
    hermes_tasks = list_tasks(vault, recipient="Hermes")

    assert [t["task_id"] for t in openclaw_tasks] == ["task-openclaw"]
    assert [t["task_id"] for t in hermes_tasks] == ["task-hermes"]


def test_cleanup_tasks_preview_matches_noisy_open_backlog_without_mutation(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(vault, task_id="task-noise-1", sender="Hermes", recipient="OpenClaw", status="open", request="test")
    _seed_task(vault, task_id="task-noise-2", sender="Hermes", recipient="OpenClaw", status="open", request="test")
    _seed_task(vault, task_id="task-keep", sender="Hermes", recipient="OpenClaw", status="open", request="real work")

    result = cleanup_tasks(
        vault,
        runtime="Hermes",
        sender="Hermes",
        recipient="OpenClaw",
        status="open",
        request_exact="test",
        apply=False,
    )

    assert result["matched_count"] == 2
    assert result["updated_count"] == 0
    assert result["apply"] is False
    assert [task["task_id"] for task in result["matched_tasks"]] == ["task-noise-1", "task-noise-2"]
    assert [task["status"] for task in list_tasks(vault, recipient="OpenClaw")] == ["open", "open", "open"]


def test_cleanup_tasks_limited_preview_separates_total_matches_from_selected_tasks(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(vault, task_id="task-noise-1", sender="Hermes", recipient="OpenClaw", status="open", request="test")
    _seed_task(vault, task_id="task-noise-2", sender="Hermes", recipient="OpenClaw", status="open", request="test")
    _seed_task(vault, task_id="task-noise-3", sender="Hermes", recipient="OpenClaw", status="open", request="test")

    result = cleanup_tasks(
        vault,
        runtime="Hermes",
        sender="Hermes",
        recipient="OpenClaw",
        status="open",
        request_exact="test",
        apply=False,
        limit=1,
    )

    assert result["matched_count"] == 3
    assert result["selected_count"] == 1
    assert result["matched_task_ids"] == ["task-noise-1", "task-noise-2", "task-noise-3"]
    assert result["selected_task_ids"] == ["task-noise-1"]
    assert [task["task_id"] for task in result["selected_tasks"]] == ["task-noise-1"]
    assert [task["task_id"] for task in result["matched_tasks"]] == ["task-noise-1"]


def test_cleanup_tasks_apply_requires_explicit_open_status_filter(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(vault, task_id="task-open", sender="Hermes", recipient="OpenClaw", status="open", request="test")
    _seed_task(vault, task_id="task-claimed", sender="Hermes", recipient="OpenClaw", status="claimed", owner="OpenClaw", request="test")

    missing_status = cleanup_tasks(
        vault,
        runtime="Hermes",
        sender="Hermes",
        recipient="OpenClaw",
        request_exact="test",
        apply=True,
    )

    assert missing_status["ok"] is False
    assert "explicit status=open" in missing_status["reason"]
    assert missing_status["updated_count"] == 0

    active_status = cleanup_tasks(
        vault,
        runtime="Hermes",
        sender="Hermes",
        recipient="OpenClaw",
        status="claimed",
        request_exact="test",
        apply=True,
    )

    assert active_status["ok"] is False
    assert "explicit status=open" in active_status["reason"]
    assert active_status["updated_count"] == 0
    statuses_by_id = {task["task_id"]: task["status"] for task in list_tasks(vault, recipient="OpenClaw")}
    assert statuses_by_id == {"task-open": "open", "task-claimed": "claimed"}



def test_cleanup_tasks_can_preview_specific_ingress_lane_without_mutation(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(
        vault,
        task_id="task-lane-a-1",
        sender="Hermes",
        recipient="OpenClaw",
        status="open",
        request="test",
        ingress_context={
            "source_platform": "discord",
            "conversation_key": "discord:ops:thread-a",
            "origin_message_id": "message-a",
        },
        work_fingerprint="discord:OpenClaw:message-a",
    )
    _seed_task(
        vault,
        task_id="task-lane-a-2",
        sender="Hermes",
        recipient="OpenClaw",
        status="open",
        request="test",
        ingress_context={
            "source_platform": "discord",
            "conversation_key": "discord:ops:thread-a",
            "origin_message_id": "message-b",
        },
        work_fingerprint="discord:OpenClaw:message-b",
    )
    _seed_task(
        vault,
        task_id="task-lane-b",
        sender="Hermes",
        recipient="OpenClaw",
        status="open",
        request="test",
        ingress_context={
            "source_platform": "discord",
            "conversation_key": "discord:ops:thread-b",
            "origin_message_id": "message-c",
        },
        work_fingerprint="discord:OpenClaw:message-c",
    )

    result = cleanup_tasks(
        vault,
        runtime="Hermes",
        recipient="OpenClaw",
        status="open",
        conversation_key="discord:ops:thread-a",
        limit=1,
    )

    assert result["ok"] is True
    assert result["matched_count"] == 2
    assert result["selected_count"] == 1
    assert result["matched_task_ids"] == ["task-lane-a-1", "task-lane-a-2"]
    assert result["selected_task_ids"] == ["task-lane-a-1"]
    assert result["filters"]["conversation_key"] == "discord:ops:thread-a"
    assert result["updated_count"] == 0
    assert [task["task_id"] for task in list_tasks(vault, recipient="OpenClaw", status="open")] == [
        "task-lane-a-1",
        "task-lane-a-2",
        "task-lane-b",
    ]


def test_cleanup_tasks_can_filter_by_work_fingerprint_and_origin_message(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(
        vault,
        task_id="task-message-a",
        sender="Hermes",
        recipient="OpenClaw",
        status="open",
        request="test",
        ingress_context={"origin_message_id": "message-a", "conversation_key": "discord:ops:thread-a"},
        work_fingerprint="discord:OpenClaw:message-a",
    )
    _seed_task(
        vault,
        task_id="task-message-b",
        sender="Hermes",
        recipient="OpenClaw",
        status="open",
        request="test",
        ingress_context={"origin_message_id": "message-b", "conversation_key": "discord:ops:thread-a"},
        work_fingerprint="discord:OpenClaw:message-b",
    )

    by_fingerprint = cleanup_tasks(
        vault,
        runtime="Hermes",
        recipient="OpenClaw",
        status="open",
        work_fingerprint="discord:OpenClaw:message-a",
    )
    by_origin = cleanup_tasks(
        vault,
        runtime="Hermes",
        recipient="OpenClaw",
        status="open",
        origin_message_id="message-b",
    )

    assert by_fingerprint["matched_task_ids"] == ["task-message-a"]
    assert by_origin["matched_task_ids"] == ["task-message-b"]


def test_cleanup_tasks_apply_cancels_limited_matching_tasks(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(vault, task_id="task-noise-1", sender="Hermes", recipient="OpenClaw", status="open", request="test")
    _seed_task(vault, task_id="task-noise-2", sender="Hermes", recipient="OpenClaw", status="open", request="test")
    _seed_task(vault, task_id="task-keep", sender="OpenClaw", recipient="Hermes", status="open", request="real work")

    result = cleanup_tasks(
        vault,
        runtime="Hermes",
        sender="Hermes",
        recipient="OpenClaw",
        status="open",
        request_exact="test",
        apply=True,
        limit=1,
        reason="Queue hygiene cleanup",
    )

    assert result["matched_count"] == 2
    assert result["updated_count"] == 1
    assert result["updated_task_ids"] == ["task-noise-1"]
    assert list_tasks(vault, recipient="OpenClaw", status="cancelled")[0]["task_id"] == "task-noise-1"
    assert [task["task_id"] for task in list_tasks(vault, recipient="OpenClaw", status="open")] == ["task-noise-2"]


def test_claim_task_updates_owner_and_status(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(vault, task_id="task-claim")

    result = claim_task(vault, task_id="task-claim", runtime="OpenClaw")
    assert result["claimed"] is True

    tasks = list_tasks(vault, recipient="OpenClaw")
    assert tasks[0]["owner"] == "OpenClaw"
    assert tasks[0]["status"] == "claimed"


def test_claim_task_rejects_wrong_runtime(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(vault, task_id="task-wrong", recipient="OpenClaw")

    result = claim_task(vault, task_id="task-wrong", runtime="Hermes")
    assert result["claimed"] is False
    assert "recipient" in result["reason"].lower()


def test_update_task_open_clears_owner(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(vault, task_id="task-reset", recipient="OpenClaw")
    claim_task(vault, task_id="task-reset", runtime="OpenClaw")

    result = update_task_status(
        vault,
        task_id="task-reset",
        runtime="OpenClaw",
        status="open",
        event_type="notice",
        message="re-open task",
    )
    assert result["updated"] is True

    task = list_tasks(vault, recipient="OpenClaw", status="open")[0]
    assert task["owner"] is None


def test_upsert_heartbeat_persists_runtime_state(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    init_db(vault)

    result = upsert_heartbeat(vault, runtime="Hermes", status="idle", health="ok", summary="ready")
    assert result["runtime"] == "Hermes"
    assert result["heartbeat_scope"] == "runtime"

    conn = sqlite3.connect(db_path(vault))
    row = conn.execute(
        "SELECT heartbeat_key, runtime, heartbeat_scope, status, health, summary FROM heartbeats WHERE heartbeat_key='Hermes'"
    ).fetchone()
    conn.close()
    assert row == ("Hermes", "Hermes", "runtime", "idle", "ok", "ready")


def test_upsert_heartbeat_persists_instance_scoped_runtime_state(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    init_db(vault)

    result = upsert_heartbeat(
        vault,
        runtime="Hermes",
        runtime_instance_id="discord-thread-1496197360382906398",
        heartbeat_scope="instance",
        control_surface="discord",
        control_surface_key="discord:1493226848409358426:1496197360382906398",
        status="busy",
        health="ok",
        summary="thread-bound coordination watch",
    )
    assert result["runtime"] == "Hermes"
    assert result["heartbeat_scope"] == "instance"
    assert result["runtime_instance_id"] == "discord-thread-1496197360382906398"

    conn = sqlite3.connect(db_path(vault))
    row = conn.execute(
        "SELECT heartbeat_key, runtime, runtime_instance_id, heartbeat_scope, control_surface, control_surface_key, status FROM heartbeats WHERE heartbeat_key=?",
        ("Hermes:discord-thread-1496197360382906398",),
    ).fetchone()
    conn.close()
    assert row == (
        "Hermes:discord-thread-1496197360382906398",
        "Hermes",
        "discord-thread-1496197360382906398",
        "instance",
        "discord",
        "discord:1493226848409358426:1496197360382906398",
        "busy",
    )


def test_create_task_persists_ingress_context_metadata(tmp_path: Path):
    vault = _make_test_vault(tmp_path)

    result = create_task(
        vault,
        sender="Hermes",
        recipient="OpenClaw",
        intent="TASK",
        priority="normal",
        request="Handle shared ops task",
        expected_output="A structured result",
        now_iso="2026-04-26T12:00:00+00:00",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906398",
            "source_channel_class": "runtime-chat",
            "conversation_key": "discord:1493226848409358426:1496197360382906398",
            "origin_message_id": "1497000000000000001",
        },
        work_fingerprint="discord-hermes-thread-work-001",
    )

    assert result["created"] is True

    tasks = list_tasks(vault, recipient="OpenClaw")
    assert tasks[0]["work_fingerprint"] == "discord-hermes-thread-work-001"
    assert tasks[0]["ingress_context"]["source_platform"] == "discord"
    assert tasks[0]["ingress_context"]["source_thread_id"] == "1496197360382906398"


def test_upsert_heartbeat_accepts_future_runtime_from_capabilities(tmp_path: Path):
    vault = _make_test_vault(tmp_path, extra_runtimes=[("sentinel", "Sentinel")])
    init_db(vault)

    result = upsert_heartbeat(vault, runtime="Sentinel", status="idle", health="ok", summary="future runtime ready")

    assert result["runtime"] == "Sentinel"

    conn = sqlite3.connect(db_path(vault))
    row = conn.execute(
        "SELECT heartbeat_key, runtime, heartbeat_scope, status, health, summary FROM heartbeats WHERE heartbeat_key='Sentinel'"
    ).fetchone()
    conn.close()
    assert row == ("Sentinel", "Sentinel", "runtime", "idle", "ok", "future runtime ready")


def test_create_task_rejects_active_duplicate_work_fingerprint(tmp_path: Path):
    vault = _make_test_vault(tmp_path)

    first = create_task(
        vault,
        sender="Hermes",
        recipient="OpenClaw",
        intent="TASK",
        priority="normal",
        request="Handle mirrored Discord work item",
        expected_output="A structured result",
        now_iso="2026-04-26T12:00:00+00:00",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906398",
            "conversation_key": "discord:1493226848409358426:1496197360382906398",
        },
        work_fingerprint="discord-shared-work-001",
    )
    duplicate = create_task(
        vault,
        sender="Hermes",
        recipient="OpenClaw",
        intent="TASK",
        priority="normal",
        request="Same work mirrored into another lane",
        expected_output="A structured result",
        now_iso="2026-04-26T12:01:00+00:00",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906400",
            "conversation_key": "discord:1493226848409358426:1496197360382906400",
        },
        work_fingerprint="discord-shared-work-001",
    )

    assert first["created"] is True
    assert duplicate["created"] is False
    assert duplicate["duplicate_task_id"] == first["task_id"]
    assert "work_fingerprint" in duplicate["reason"]


def test_create_task_allows_distinct_thread_work_when_fingerprint_differs(tmp_path: Path):
    vault = _make_test_vault(tmp_path)

    first = create_task(
        vault,
        sender="Hermes",
        recipient="OpenClaw",
        request="Thread one work",
        expected_output="result",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "thread-1",
            "conversation_key": "discord:1493226848409358426:thread-1",
        },
        work_fingerprint="discord-thread-1-work",
    )
    second = create_task(
        vault,
        sender="Hermes",
        recipient="OpenClaw",
        request="Thread two work",
        expected_output="result",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "thread-2",
            "conversation_key": "discord:1493226848409358426:thread-2",
        },
        work_fingerprint="discord-thread-2-work",
    )

    assert first["created"] is True
    assert second["created"] is True
    tasks = list_tasks(vault, recipient="OpenClaw")
    assert [task["work_fingerprint"] for task in tasks] == [
        "discord-thread-1-work",
        "discord-thread-2-work",
    ]


def test_create_task_derives_discord_conversation_key_when_missing(tmp_path: Path):
    vault = _make_test_vault(tmp_path)

    created = create_task(
        vault,
        sender="Hermes",
        recipient="OpenClaw",
        request="Handle thread work",
        expected_output="result",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906398",
            "source_channel_class": "runtime-chat",
            "origin_message_id": "1497000000000000001",
        },
        work_fingerprint="discord-thread-work-derived-conversation",
    )

    assert created["created"] is True
    task = list_tasks(vault, recipient="OpenClaw")[0]
    assert task["ingress_context"]["conversation_key"] == "discord:1493226848409358426:1496197360382906398"


def test_create_task_derives_work_fingerprint_from_discord_origin_message_when_missing(tmp_path: Path):
    vault = _make_test_vault(tmp_path)

    first = create_task(
        vault,
        sender="Hermes",
        recipient="OpenClaw",
        request="Handle mirrored Discord work item",
        expected_output="result",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906398",
            "origin_message_id": "1497000000000000001",
        },
    )
    duplicate = create_task(
        vault,
        sender="Hermes",
        recipient="OpenClaw",
        request="Handle mirrored Discord work item",
        expected_output="result",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906398",
            "origin_message_id": "1497000000000000001",
        },
    )

    assert first["created"] is True
    assert duplicate["created"] is False
    assert duplicate["duplicate_task_id"] == first["task_id"]

    task = list_tasks(vault, recipient="OpenClaw")[0]
    assert task["work_fingerprint"] == "discord:OpenClaw:1497000000000000001"
    assert task["ingress_context"]["conversation_key"] == "discord:1493226848409358426:1496197360382906398"


def test_create_task_rejects_discord_ingress_without_source_channel_id(tmp_path: Path):
    vault = _make_test_vault(tmp_path)

    created = create_task(
        vault,
        sender="Hermes",
        recipient="OpenClaw",
        request="Handle malformed Discord work",
        expected_output="result",
        ingress_context={
            "source_platform": "discord",
            "source_thread_id": "1496197360382906398",
            "origin_message_id": "1497000000000000001",
        },
    )

    assert created["created"] is False
    assert "source_channel_id" in created["reason"]


def test_translate_discord_control_plane_request_creates_bus_task_for_control_plane_channel(tmp_path: Path):
    vault = _make_test_vault(tmp_path)

    result = translate_discord_control_plane_request(
        vault,
        recipient="OpenClaw",
        request="Prepare a ChaseOS Pulse card for tomorrow's proactive briefing review.",
        expected_output="Structured review result",
        source_channel_id="1493226873080119397",
        source_thread_id="1496197360382906398",
        origin_message_id="1497000000000000001",
        coordination_sensitive=True,
        now_iso="2026-04-26T13:00:00+00:00",
    )

    assert result["translated"] is True
    assert result["created"] is True
    assert result["channel_binding_name"] == "chaseos-ops"
    task = list_tasks(vault, recipient="OpenClaw")[0]
    assert task["sender"] == "Operator"
    assert task["ingress_context"]["source_platform"] == "discord"
    assert task["ingress_context"]["source_channel_class"] == "control-plane-routing"
    assert task["ingress_context"]["conversation_key"] == "discord:1493226873080119397:1496197360382906398"
    assert task["ingress_context"]["feature_id"] == "pulse"
    assert task["ingress_context"]["skill_id"] == "pulse-review-queue"
    assert result["feature_invocation"]["feature_id"] == "pulse"
    assert result["feature_invocation"]["source_surface"] == "discord_control_plane"
    assert [event["event_type"] for event in result["feature_invocation"]["product_events"]] == [
        "feature.invoked",
        "skill.used",
        "feature.output.created",
    ]
    assert result["feature_invocation"]["product_events"][1]["skill_id"] == "pulse-review-queue"
    ledger_path = vault / "07_LOGS" / "Feature-Usage-Ledger" / "feature-usage.jsonl"
    assert ledger_path.exists()


def test_translate_discord_control_plane_request_leaves_runtime_chat_advisory_by_default(tmp_path: Path):
    vault = _make_test_vault(tmp_path)

    result = translate_discord_control_plane_request(
        vault,
        recipient="Hermes",
        request="What are you working on?",
        expected_output="Status summary",
        source_channel_id="1493226848409358426",
        origin_message_id="1497000000000000002",
        coordination_sensitive=False,
    )

    assert result["translated"] is False
    assert result["classification"] == "advisory_only"
    assert list_tasks(vault, recipient="Hermes") == []


def test_translate_discord_control_plane_request_rejects_noninteractive_channel(tmp_path: Path):
    vault = _make_test_vault(tmp_path)

    result = translate_discord_control_plane_request(
        vault,
        recipient="Hermes",
        request="Run the workflow",
        expected_output="Shadow output",
        source_channel_id="1493337616467230910",
        origin_message_id="1497000000000000003",
        coordination_sensitive=True,
    )

    assert result["translated"] is False
    assert result["classification"] == "forbidden_channel_class"
    assert "approvals" in result["reason"]


def test_translate_discord_control_plane_request_rejects_runtime_chat_for_wrong_runtime(tmp_path: Path):
    vault = _make_test_vault(tmp_path)

    result = translate_discord_control_plane_request(
        vault,
        recipient="OpenClaw",
        request="Do OpenClaw work from Hermes lane",
        expected_output="Result",
        source_channel_id="1493226848409358426",
        origin_message_id="1497000000000000004",
        coordination_sensitive=True,
    )

    assert result["translated"] is False
    assert result["classification"] == "runtime_not_interactive_in_channel"
    assert "OpenClaw" in result["reason"]


def test_mark_stale_tasks_expires_old_task_only_when_owner_runtime_is_stale(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(
        vault,
        task_id="task-stale",
        recipient="OpenClaw",
        status="in_progress",
        owner="OpenClaw",
        created_at="2026-04-24T00:00:00+00:00",
        updated_at="2026-04-24T00:00:00+00:00",
    )
    upsert_heartbeat(
        vault,
        runtime="OpenClaw",
        status="busy",
        health="ok",
        summary="stale owner",
        now_iso="2026-04-24T00:00:00+00:00",
    )

    result = mark_stale_tasks(vault, max_age_seconds=60, now_iso="2026-04-24T02:00:00+00:00")
    assert result["expired_count"] == 1

    conn = sqlite3.connect(db_path(vault))
    status = conn.execute("SELECT status FROM tasks WHERE task_id='task-stale'").fetchone()[0]
    event_count = conn.execute("SELECT count(*) FROM events WHERE task_id='task-stale' AND event_type='expired'").fetchone()[0]
    conn.close()

    assert status == "expired"
    assert event_count == 1


def test_mark_stale_tasks_does_not_expire_open_unowned_tasks(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(
        vault,
        task_id="task-open",
        recipient="Hermes",
        status="open",
        owner=None,
        created_at="2026-04-24T00:00:00+00:00",
        updated_at="2026-04-24T00:00:00+00:00",
    )
    upsert_heartbeat(
        vault,
        runtime="Hermes",
        status="offline",
        health="degraded",
        summary="fresh heartbeat but offline status",
        now_iso="2026-04-24T01:59:30+00:00",
    )

    result = mark_stale_tasks(vault, max_age_seconds=0, now_iso="2026-04-24T02:00:00+00:00")
    assert result["expired_count"] == 0

    task = list_tasks(vault, recipient="Hermes")[0]
    assert task["status"] == "open"


def test_mark_stale_tasks_does_not_expire_active_task_when_owner_heartbeat_is_fresh(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(hours=2)).isoformat()
    fresh_heartbeat = (now - timedelta(seconds=30)).isoformat()

    _seed_task(
        vault,
        task_id="task-fresh-owner",
        recipient="Hermes",
        status="blocked",
        owner="Hermes",
        created_at=old_time,
        updated_at=old_time,
    )
    upsert_heartbeat(
        vault,
        runtime="Hermes",
        status="offline",
        health="degraded",
        summary="fresh heartbeat",
        now_iso=fresh_heartbeat,
    )

    result = mark_stale_tasks(vault, max_age_seconds=60, now_iso=now.isoformat())
    assert result["expired_count"] == 0

    task = list_tasks(vault, recipient="Hermes")[0]
    assert task["status"] == "blocked"


def test_watch_once_skips_open_task_that_conflicts_with_active_conversation_lane(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(
        vault,
        task_id="task-active-lane",
        recipient="OpenClaw",
        status="claimed",
        owner="OpenClaw",
        created_at="2026-04-24T00:00:00+00:00",
        updated_at="2026-04-24T00:00:00+00:00",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906398",
            "conversation_key": "discord:1493226848409358426:1496197360382906398",
            "origin_message_id": "1497000000000000001",
        },
        work_fingerprint="lane-active-001",
    )
    _seed_task(
        vault,
        task_id="task-open-same-lane",
        recipient="OpenClaw",
        status="open",
        created_at="2026-04-24T00:01:00+00:00",
        updated_at="2026-04-24T00:01:00+00:00",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906398",
            "conversation_key": "discord:1493226848409358426:1496197360382906398",
            "origin_message_id": "1497000000000000001",
        },
        work_fingerprint="lane-open-002",
    )
    _seed_task(
        vault,
        task_id="task-open-other-lane",
        recipient="OpenClaw",
        status="open",
        created_at="2026-04-24T00:02:00+00:00",
        updated_at="2026-04-24T00:02:00+00:00",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906400",
            "conversation_key": "discord:1493226848409358426:1496197360382906400",
            "origin_message_id": "1497000000000000002",
        },
        work_fingerprint="lane-open-003",
    )

    summary = watch_once(vault, runtime="OpenClaw", claim_next=True, now_iso="2026-04-24T00:10:00+00:00")

    assert summary["claimed_task_id"] == "task-open-other-lane"
    assert summary["skipped_conflict_count"] == 1
    assert summary["skipped_conflicts"][0]["task_id"] == "task-open-same-lane"
    assert summary["skipped_conflicts"][0]["conflicts"][0]["task_id"] == "task-active-lane"
    tasks = {t["task_id"]: t for t in list_tasks(vault, recipient="OpenClaw")}
    assert tasks["task-open-same-lane"]["status"] == "open"
    assert tasks["task-open-other-lane"]["status"] == "claimed"


def test_claim_task_rejects_active_discord_lane_conflict(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(
        vault,
        task_id="task-active-lane",
        recipient="OpenClaw",
        status="claimed",
        owner="OpenClaw",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906398",
            "conversation_key": "discord:1493226848409358426:1496197360382906398",
        },
        work_fingerprint="lane-active-001",
    )
    _seed_task(
        vault,
        task_id="task-open-same-lane",
        recipient="OpenClaw",
        status="open",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906398",
            "conversation_key": "discord:1493226848409358426:1496197360382906398",
        },
        work_fingerprint="lane-open-002",
    )

    claimability = evaluate_task_claimability(vault, task_id="task-open-same-lane", runtime="OpenClaw")
    result = claim_task(vault, task_id="task-open-same-lane", runtime="OpenClaw")

    assert claimability["claimable"] is False
    assert claimability["conflicts"][0]["reason"] == "conversation_key"
    assert result["claimed"] is False
    assert result["reason"] == "active lane conflict for runtime"
    assert result["conflicts"][0]["task_id"] == "task-active-lane"
    assert list_tasks(vault, recipient="OpenClaw", status="open")[0]["task_id"] == "task-open-same-lane"


def test_claim_task_allows_different_discord_thread_lane(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(
        vault,
        task_id="task-active-lane",
        recipient="OpenClaw",
        status="claimed",
        owner="OpenClaw",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906398",
            "conversation_key": "discord:1493226848409358426:1496197360382906398",
        },
        work_fingerprint="lane-active-001",
    )
    _seed_task(
        vault,
        task_id="task-open-other-lane",
        recipient="OpenClaw",
        status="open",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906400",
            "conversation_key": "discord:1493226848409358426:1496197360382906400",
        },
        work_fingerprint="lane-open-002",
    )

    result = claim_task(vault, task_id="task-open-other-lane", runtime="OpenClaw")

    assert result["claimed"] is True
    tasks = {t["task_id"]: t for t in list_tasks(vault, recipient="OpenClaw")}
    assert tasks["task-open-other-lane"]["status"] == "claimed"


def test_openclaw_claim_persists_discord_thread_owner_instance(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(
        vault,
        task_id="task-openclaw-thread",
        recipient="OpenClaw",
        status="open",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906400",
            "conversation_key": "discord:1493226848409358426:1496197360382906400",
        },
        work_fingerprint="openclaw-thread-work-001",
    )

    result = claim_task(vault, task_id="task-openclaw-thread", runtime="OpenClaw")

    assert result["claimed"] is True
    assert result["owner_instance"] == "discord-thread-1496197360382906400"
    task = list_tasks(vault, recipient="OpenClaw")[0]
    assert task["owner"] == "OpenClaw"
    assert task["owner_instance"] == "discord-thread-1496197360382906400"


def test_shared_runtime_channel_claims_persist_channel_owner_instance(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    shared_channel_id = "1493226873080119397"
    ingress_context = {
        "source_platform": "discord",
        "source_channel_id": shared_channel_id,
        "source_channel_class": "control-plane-routing",
        "conversation_key": f"discord:{shared_channel_id}",
        "control_plane_route": "shared-runtime-channel",
    }
    _seed_task(
        vault,
        task_id="task-openclaw-shared-channel",
        recipient="OpenClaw",
        sender="Hermes",
        status="open",
        ingress_context=ingress_context,
        work_fingerprint="shared-runtime-channel-openclaw-001",
    )
    _seed_task(
        vault,
        task_id="task-hermes-shared-channel",
        recipient="Hermes",
        sender="OpenClaw",
        status="open",
        ingress_context=ingress_context,
        work_fingerprint="shared-runtime-channel-hermes-001",
    )

    openclaw_result = claim_task(vault, task_id="task-openclaw-shared-channel", runtime="OpenClaw")
    hermes_result = claim_task(vault, task_id="task-hermes-shared-channel", runtime="Hermes")

    assert openclaw_result["claimed"] is True
    assert hermes_result["claimed"] is True
    assert openclaw_result["owner_instance"] == f"discord-channel-{shared_channel_id}"
    assert hermes_result["owner_instance"] == f"discord-channel-{shared_channel_id}"
    tasks = {task["task_id"]: task for task in list_tasks(vault)}
    assert tasks["task-openclaw-shared-channel"]["owner_instance"] == f"discord-channel-{shared_channel_id}"
    assert tasks["task-hermes-shared-channel"]["owner_instance"] == f"discord-channel-{shared_channel_id}"


def test_watch_once_emits_instance_scoped_heartbeat_for_claimed_discord_lane(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(
        vault,
        task_id="task-discord-lane",
        recipient="OpenClaw",
        status="open",
        created_at="2026-04-24T00:00:00+00:00",
        updated_at="2026-04-24T00:00:00+00:00",
        ingress_context={
            "source_platform": "discord",
            "source_channel_id": "1493226848409358426",
            "source_thread_id": "1496197360382906398",
            "conversation_key": "discord:1493226848409358426:1496197360382906398",
            "origin_message_id": "1497000000000000001",
        },
        work_fingerprint="lane-heartbeat-001",
    )

    summary = watch_once(vault, runtime="OpenClaw", claim_next=True, now_iso="2026-04-24T00:10:00+00:00")

    assert summary["claimed_task_id"] == "task-discord-lane"
    conn = sqlite3.connect(db_path(vault))
    row = conn.execute(
        "SELECT heartbeat_key, runtime_instance_id, heartbeat_scope, control_surface, control_surface_key, current_task_id FROM heartbeats WHERE heartbeat_key=?",
        ("OpenClaw:discord-thread-1496197360382906398",),
    ).fetchone()
    conn.close()
    assert row == (
        "OpenClaw:discord-thread-1496197360382906398",
        "discord-thread-1496197360382906398",
        "instance",
        "discord",
        "discord:1493226848409358426:1496197360382906398",
        "task-discord-lane",
    )


def test_run_watch_loop_rejects_non_positive_interval(tmp_path: Path):
    vault = _make_test_vault(tmp_path)

    with pytest.raises(ValueError, match="interval_seconds must be greater than 0"):
        run_watch_loop(vault, runtime="Hermes", interval_seconds=0)

    with pytest.raises(ValueError, match="interval_seconds must be greater than 0"):
        run_watch_loop(vault, runtime="Hermes", interval_seconds=-1)


def test_run_watch_loop_preserves_explicit_instance_scope(monkeypatch, tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_watch_once(root, **kwargs):
        calls.append({"root": root, **kwargs})
        raise KeyboardInterrupt

    monkeypatch.setattr("runtime.agent_bus.bus.watch_once", fake_watch_once)

    try:
        run_watch_loop(
            vault,
            runtime="Hermes",
            interval_seconds=30,
            claim_next=True,
            stale_after_seconds=300,
            runtime_instance_id="discord-thread-1496197360382906398",
            control_surface="discord",
            control_surface_key="discord:1493226848409358426:1496197360382906398",
        )
    except KeyboardInterrupt:
        pass

    assert calls == [
        {
            "root": vault,
            "runtime": "Hermes",
            "claim_next": True,
            "stale_after_seconds": 300,
            "runtime_instance_id": "discord-thread-1496197360382906398",
            "control_surface": "discord",
            "control_surface_key": "discord:1493226848409358426:1496197360382906398",
        }
    ]



def test_watch_once_can_claim_next_task(tmp_path: Path):
    vault = _make_test_vault(tmp_path)
    _seed_task(vault, task_id="task-001", recipient="OpenClaw", created_at="2026-04-24T00:00:00+00:00", updated_at="2026-04-24T00:00:00+00:00")
    _seed_task(vault, task_id="task-002", recipient="OpenClaw", created_at="2026-04-24T00:05:00+00:00", updated_at="2026-04-24T00:05:00+00:00")

    summary = watch_once(vault, runtime="OpenClaw", claim_next=True, now_iso="2026-04-24T00:10:00+00:00")

    assert summary["claimed_task_id"] == "task-001", "watch_once should claim the oldest open task"
    tasks = list_tasks(vault, recipient="OpenClaw")
    task_map = {t["task_id"]: t for t in tasks}
    assert task_map["task-001"]["status"] == "claimed"
    assert task_map["task-001"]["owner"] == "OpenClaw"
    assert task_map["task-002"]["status"] == "open"
