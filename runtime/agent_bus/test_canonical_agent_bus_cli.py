"""Canonical ``chaseos agent-bus`` smoke coverage.

These tests deliberately invoke the top-level ``chaseos.py`` entrypoint rather
than ``runtime/agent_bus/cli.py``. Side-effecting commands run against a temp
vault and created tasks are cancelled with a unique cleanup marker.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CHASEOS = ROOT / "chaseos.py"
SMOKE_TMP = ROOT / "runtime" / "agent_bus" / ".canonical_cli_smoke_tmp"


@pytest.fixture
def smoke_workspace() -> Path:
    run_root = SMOKE_TMP / f"run-{uuid.uuid4().hex[:12]}"
    run_root.mkdir(parents=True, exist_ok=False)
    try:
        yield run_root
    finally:
        resolved_run_root = run_root.resolve()
        resolved_smoke_tmp = SMOKE_TMP.resolve()
        if resolved_smoke_tmp in resolved_run_root.parents and resolved_run_root.exists():
            shutil.rmtree(resolved_run_root, ignore_errors=True)
        try:
            SMOKE_TMP.rmdir()
        except OSError:
            pass


def _make_temp_vault(workspace: Path) -> Path:
    vault = workspace / "vault"
    (vault / "runtime" / "agent_bus").mkdir(parents=True)
    (vault / ".chaseos").mkdir(parents=True)
    (vault / "CLAUDE.md").write_text("# temp ChaseOS test vault\n", encoding="utf-8")
    (vault / ".chaseos" / "discord_instance_bindings.yaml").write_text(
        textwrap.dedent(
            """\
            schema_version: "1.0"
            primary_channels:
              hermes_runtime_chat:
                id: "test-hermes-runtime-chat"
                name: "hermes-runtime-chat"
                channel_class: runtime-chat
                bound: true
                interactive_eligible_runtimes:
                  - hermes
              ops_control_plane:
                id: "test-ops-control-plane"
                name: "chaseos-ops-test"
                channel_class: control-plane-routing
                bound: true
                interactive_eligible_runtimes:
                  - hermes
                  - openclaw
            supplemental_channels: {}
            """
        ),
        encoding="utf-8",
    )
    return vault


def _cleanup_marker() -> str:
    return f"codex-agent-bus-smoke-cleanup-{uuid.uuid4().hex[:8]}"


def _run_chaseos(vault: Path, *args: str) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            str(CHASEOS),
            *args,
            "--vault-root",
            str(vault),
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert completed.returncode == 0, (
        f"command failed: {' '.join(args)}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    envelope = json.loads(completed.stdout)
    assert envelope["ok"] is True
    assert envelope["errors"] == []
    return envelope["result"]


def _task(vault: Path, task_id: str) -> dict:
    conn = sqlite3.connect(vault / "runtime" / "agent_bus" / "agent_bus.sqlite")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    conn.close()
    assert row is not None
    task = dict(row)
    task["ingress_context"] = json.loads(task.pop("ingress_context_json") or "{}")
    return task


def _task_events(vault: Path, task_id: str) -> list[dict]:
    conn = sqlite3.connect(vault / "runtime" / "agent_bus" / "agent_bus.sqlite")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT event_type, message FROM events WHERE task_id = ? ORDER BY created_at",
        (task_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _real_bus_has_marker(marker: str) -> bool:
    db = ROOT / "runtime" / "agent_bus" / "agent_bus.sqlite"
    if not db.exists():
        return False
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        task_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM tasks
            WHERE request LIKE ?
               OR expected_output LIKE ?
               OR COALESCE(notes, '') LIKE ?
            """,
            (f"%{marker}%", f"%{marker}%", f"%{marker}%"),
        ).fetchone()[0]
        heartbeat_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM heartbeats
            WHERE COALESCE(summary, '') LIKE ?
               OR COALESCE(current_task_id, '') LIKE ?
            """,
            (f"%{marker}%", f"%{marker}%"),
        ).fetchone()[0]
    finally:
        conn.close()
    return bool(task_count or heartbeat_count)


def _cancel_task(vault: Path, task_id: str, *, runtime: str, marker: str) -> None:
    result = _run_chaseos(
        vault,
        "agent-bus",
        "task",
        "update",
        task_id,
        "--runtime",
        runtime,
        "--status",
        "cancelled",
        "--message",
        marker,
    )
    assert result["updated"] is True


def test_canonical_agent_bus_create_watch_reclaim_and_cleanup(smoke_workspace: Path) -> None:
    vault = _make_temp_vault(smoke_workspace)
    marker = _cleanup_marker()

    created = _run_chaseos(
        vault,
        "agent-bus",
        "task",
        "create",
        "--sender",
        "Hermes",
        "--to",
        "OpenClaw",
        "--intent",
        "TASK",
        "--priority",
        "normal",
        "--request",
        f"{marker} canonical create smoke",
        "--expected-output",
        "task is claimed, reclaimed, and cancelled from canonical CLI",
        "--notes",
        marker,
    )
    task_id = created["task_id"]
    assert created["created"] is True
    assert _task(vault, task_id)["status"] == "open"

    heartbeat = _run_chaseos(
        vault,
        "agent-bus",
        "heartbeat",
        "--runtime",
        "OpenClaw",
        "--status",
        "idle",
        "--health",
        "ok",
        "--summary",
        marker,
    )
    assert heartbeat["runtime"] == "OpenClaw"
    assert heartbeat["summary"] == marker

    watched = _run_chaseos(
        vault,
        "agent-bus",
        "watch",
        "--runtime",
        "OpenClaw",
        "--once",
        "--claim-next",
    )
    assert watched["claimed_task_id"] == task_id
    assert _task(vault, task_id)["owner"] == "OpenClaw"

    reclaimed = _run_chaseos(
        vault,
        "agent-bus",
        "task",
        "reclaim",
        task_id,
        "--runtime",
        "Hermes",
        "--reason",
        marker,
    )
    assert reclaimed["reclaimed"] is True
    reopened = _task(vault, task_id)
    assert reopened["recipient"] == "Hermes"
    assert reopened["status"] == "open"
    assert reopened["owner"] is None

    _cancel_task(vault, task_id, runtime="Hermes", marker=marker)

    cancelled = _task(vault, task_id)
    assert cancelled["status"] == "cancelled"
    assert any(marker in event["message"] for event in _task_events(vault, task_id))
    assert _real_bus_has_marker(marker) is False


def test_canonical_agent_bus_ingress_discord_uses_temp_vault_and_cleanup(smoke_workspace: Path) -> None:
    vault = _make_temp_vault(smoke_workspace)
    marker = _cleanup_marker()

    translated = _run_chaseos(
        vault,
        "agent-bus",
        "ingress",
        "discord",
        "--to",
        "Hermes",
        "--intent",
        "TASK",
        "--priority",
        "normal",
        "--request",
        f"{marker} canonical discord ingress smoke",
        "--expected-output",
        "translated task is cancelled after smoke test",
        "--notes",
        marker,
        "--source-channel-id",
        "test-hermes-runtime-chat",
        "--source-thread-id",
        "thread-001",
        "--source-channel-class",
        "runtime-chat",
        "--origin-message-id",
        "message-001",
        "--coordination-sensitive",
    )
    task_id = translated["task_id"]
    assert translated["translated"] is True
    assert translated["classification"] == "coordination_sensitive"

    task = _task(vault, task_id)
    assert task["sender"] == "Operator"
    assert task["recipient"] == "Hermes"
    assert task["ingress_context"]["source_platform"] == "discord"
    assert task["ingress_context"]["source_channel_id"] == "test-hermes-runtime-chat"
    assert task["ingress_context"]["source_thread_id"] == "thread-001"

    _cancel_task(vault, task_id, runtime="Hermes", marker=marker)

    assert _task(vault, task_id)["status"] == "cancelled"
    assert _real_bus_has_marker(marker) is False
