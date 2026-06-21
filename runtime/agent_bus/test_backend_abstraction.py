"""
test_backend_abstraction.py — BusBackend Contract Tests
=========================================================

These tests define the CONTRACT that every BusBackend implementation must satisfy.
They run against SQLiteBackend today. When ServerBackend is implemented (Phase 10),
run the same suite against it by parameterizing the fixture.

WHY THESE TESTS EXIST
---------------------
The abstraction layer is only as good as the contract it enforces. If a new backend
is added and these tests pass, the rest of ChaseOS (handlers, workflows, engine, CLI)
is guaranteed to work with it — because they all go through bus.py, which goes through
the BusBackend interface.

These tests are deliberately independent of SQLite internals. They use only the
public BusBackend interface. If a test needs to inspect state, it does so through
the backend's own methods, not by opening a SQLite connection directly.

HOW TO ADD A NEW BACKEND TO THE TEST SUITE
-------------------------------------------
1. Create a pytest fixture that yields your backend instance (initialized)
2. Pass it to the TestBusBackendContract class via parametrize
   (or subclass TestBusBackendContract and override the `backend` fixture)
3. Run `pytest test_backend_abstraction.py` — all contract tests must pass
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.agent_bus.backends.base import BusBackend, BackendInitError
from runtime.agent_bus.backends.sqlite_backend import SQLiteBackend
from runtime.agent_bus.backend_loader import get_backend, clear_backend_cache


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sqlite_backend(tmp_path: Path) -> SQLiteBackend:
    """Fresh SQLiteBackend for each test. Schema applied via init()."""
    backend = SQLiteBackend(tmp_path / "vault")
    backend.init()
    return backend


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Minimal vault for backend_loader tests."""
    v = tmp_path / "vault"
    v.mkdir()
    (v / "CLAUDE.md").write_text("# test", encoding="utf-8")
    return v


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_task(
    backend: BusBackend,
    *,
    task_id: str = "task-abc123",
    sender: str = "OpenClaw",
    recipient: str = "Hermes",
    request: str = "do work",
    ingress_context: dict | None = None,
    work_fingerprint: str | None = None,
) -> dict:
    """Helper: create a task via the backend interface and return the result."""
    import uuid
    return backend.create_task(
        task_id=task_id,
        run_id=f"run-{uuid.uuid4().hex[:8]}",
        sender=sender,
        recipient=recipient,
        intent="TASK",
        priority="normal",
        request=request,
        expected_output="a result",
        depends_on=[],
        notes=None,
        expires_at=None,
        now_iso=_now(),
        ingress_context=ingress_context,
        work_fingerprint=work_fingerprint,
    )


# ── Backend Contract Tests ────────────────────────────────────────────────────

class TestBusBackendContract:
    """Contract tests for BusBackend. Must pass for every backend implementation."""

    # ── init ──────────────────────────────────────────────────────────────────

    def test_init_is_idempotent(self, sqlite_backend: SQLiteBackend):
        """Calling init() multiple times must not corrupt data or raise."""
        sqlite_backend.init()
        sqlite_backend.init()
        sqlite_backend.init()
        # Should be able to list tasks after repeated init
        assert sqlite_backend.list_tasks() == []

    # ── create_task ───────────────────────────────────────────────────────────

    def test_create_task_returns_created_true(self, sqlite_backend: SQLiteBackend):
        result = _make_task(sqlite_backend)
        assert result["created"] is True
        assert result["task_id"] == "task-abc123"
        assert result["sender"] == "OpenClaw"
        assert result["recipient"] == "Hermes"

    def test_create_task_appears_in_list(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend)
        tasks = sqlite_backend.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "task-abc123"
        assert tasks[0]["status"] == "open"

    def test_create_task_notes_stored(self, sqlite_backend: SQLiteBackend):
        import uuid
        sqlite_backend.create_task(
            task_id="task-notes",
            run_id=f"run-{uuid.uuid4().hex[:8]}",
            sender="OpenClaw",
            recipient="Hermes",
            intent="TASK",
            priority="normal",
            request="work",
            expected_output="result",
            depends_on=[],
            notes="artifact_path: 07_LOGS/test.md",
            expires_at=None,
            now_iso=_now(),
        )
        tasks = sqlite_backend.list_tasks()
        assert tasks[0]["notes"] == "artifact_path: 07_LOGS/test.md"

    def test_create_two_tasks_both_appear(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-1")
        _make_task(sqlite_backend, task_id="task-2")
        tasks = sqlite_backend.list_tasks()
        ids = {t["task_id"] for t in tasks}
        assert {"task-1", "task-2"} == ids

    # ── list_tasks ────────────────────────────────────────────────────────────

    def test_list_tasks_filter_by_recipient(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="t1", recipient="Hermes")
        _make_task(sqlite_backend, task_id="t2", recipient="OpenClaw")
        hermes = sqlite_backend.list_tasks(recipient="Hermes")
        assert len(hermes) == 1
        assert hermes[0]["task_id"] == "t1"

    def test_list_tasks_filter_by_status(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="t1")
        _make_task(sqlite_backend, task_id="t2")
        sqlite_backend.claim_task(task_id="t1", runtime="Hermes", now_iso=_now())
        open_tasks = sqlite_backend.list_tasks(status="open")
        claimed_tasks = sqlite_backend.list_tasks(status="claimed")
        assert len(open_tasks) == 1
        assert len(claimed_tasks) == 1

    def test_list_tasks_ordered_by_created_at(self, sqlite_backend: SQLiteBackend):
        """Tasks returned in FIFO order."""
        import time
        _make_task(sqlite_backend, task_id="first")
        time.sleep(0.01)
        _make_task(sqlite_backend, task_id="second")
        tasks = sqlite_backend.list_tasks()
        assert tasks[0]["task_id"] == "first"
        assert tasks[1]["task_id"] == "second"

    # ── get_task ──────────────────────────────────────────────────────────────

    def test_get_task_returns_task(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-get")
        task = sqlite_backend.get_task("task-get")
        assert task is not None
        assert task["task_id"] == "task-get"

    def test_get_task_returns_none_for_missing(self, sqlite_backend: SQLiteBackend):
        assert sqlite_backend.get_task("task-nonexistent") is None

    # ── claim_task ────────────────────────────────────────────────────────────

    def test_claim_task_succeeds_for_correct_recipient(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-claim", recipient="Hermes")
        result = sqlite_backend.claim_task(task_id="task-claim", runtime="Hermes", now_iso=_now())
        assert result["claimed"] is True

    def test_claim_task_changes_status_to_claimed(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-claim", recipient="Hermes")
        sqlite_backend.claim_task(task_id="task-claim", runtime="Hermes", now_iso=_now())
        task = sqlite_backend.get_task("task-claim")
        assert task["status"] == "claimed"
        assert task["owner"] == "Hermes"

    def test_claim_task_persists_explicit_owner_instance(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-claim-instance", recipient="Hermes")
        result = sqlite_backend.claim_task(
            task_id="task-claim-instance",
            runtime="Hermes",
            runtime_instance_id="discord-thread-1496197360382906398",
            now_iso=_now(),
        )

        task = sqlite_backend.get_task("task-claim-instance")
        assert result["claimed"] is True
        assert result["owner_instance"] == "discord-thread-1496197360382906398"
        assert task["owner"] == "Hermes"
        assert task["owner_instance"] == "discord-thread-1496197360382906398"

    def test_claim_task_fails_for_wrong_recipient(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-claim", recipient="Hermes")
        result = sqlite_backend.claim_task(task_id="task-claim", runtime="OpenClaw", now_iso=_now())
        assert result["claimed"] is False
        assert "not OpenClaw" in result["reason"]

    def test_claim_task_fails_for_already_claimed(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-claim", recipient="Hermes")
        sqlite_backend.claim_task(task_id="task-claim", runtime="Hermes", now_iso=_now())
        result = sqlite_backend.claim_task(task_id="task-claim", runtime="Hermes", now_iso=_now())
        assert result["claimed"] is False

    def test_claim_task_fails_for_missing_task(self, sqlite_backend: SQLiteBackend):
        result = sqlite_backend.claim_task(task_id="task-missing", runtime="Hermes", now_iso=_now())
        assert result["claimed"] is False

    def test_claim_task_blocks_active_same_lane_inside_backend(self, sqlite_backend: SQLiteBackend):
        _make_task(
            sqlite_backend,
            task_id="task-active-lane",
            recipient="Hermes",
            ingress_context={
                "source_platform": "discord",
                "source_channel_id": "1493226848409358426",
                "source_thread_id": "1496197360382906398",
                "conversation_key": "discord:1493226848409358426:1496197360382906398",
            },
            work_fingerprint="lane-active-001",
        )
        _make_task(
            sqlite_backend,
            task_id="task-open-same-lane",
            recipient="Hermes",
            ingress_context={
                "source_platform": "discord",
                "source_channel_id": "1493226848409358426",
                "source_thread_id": "1496197360382906398",
                "conversation_key": "discord:1493226848409358426:1496197360382906398",
            },
            work_fingerprint="lane-open-002",
        )
        sqlite_backend.claim_task(task_id="task-active-lane", runtime="Hermes", now_iso=_now())

        result = sqlite_backend.claim_task(task_id="task-open-same-lane", runtime="Hermes", now_iso=_now())

        assert result["claimed"] is False
        assert result["reason"] == "active lane conflict for runtime"
        assert result["conflicts"][0]["task_id"] == "task-active-lane"
        assert result["conflicts"][0]["reason"] == "conversation_key"
        assert sqlite_backend.get_task("task-open-same-lane")["status"] == "open"

    # ── update_task_status ────────────────────────────────────────────────────

    def test_update_task_status_changes_status(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-upd", recipient="Hermes")
        sqlite_backend.claim_task(task_id="task-upd", runtime="Hermes", now_iso=_now())
        result = sqlite_backend.update_task_status(
            task_id="task-upd",
            runtime="Hermes",
            status="done",
            event_type="result_attached",
            message="Review complete.",
            artifacts=None,
            now_iso=_now(),
        )
        assert result["updated"] is True
        assert sqlite_backend.get_task("task-upd")["status"] == "done"

    def test_update_task_status_fails_for_missing(self, sqlite_backend: SQLiteBackend):
        result = sqlite_backend.update_task_status(
            task_id="task-ghost",
            runtime="Hermes",
            status="done",
            event_type="result_attached",
            message=".",
            artifacts=None,
            now_iso=_now(),
        )
        assert result["updated"] is False

    def test_update_task_status_fails_for_wrong_owner(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-own", recipient="Hermes")
        sqlite_backend.claim_task(task_id="task-own", runtime="Hermes", now_iso=_now())
        result = sqlite_backend.update_task_status(
            task_id="task-own",
            runtime="OpenClaw",
            status="done",
            event_type="result_attached",
            message=".",
            artifacts=None,
            now_iso=_now(),
        )
        assert result["updated"] is False

    # ── heartbeats ────────────────────────────────────────────────────────────

    def test_upsert_heartbeat_returns_heartbeat(self, sqlite_backend: SQLiteBackend):
        result = sqlite_backend.upsert_heartbeat(
            runtime="Hermes", status="idle", health="ok",
            current_task_id=None, summary="test", now_iso=_now(),
        )
        assert result["runtime"] == "Hermes"
        assert result["status"] == "idle"

    def test_upsert_heartbeat_is_idempotent(self, sqlite_backend: SQLiteBackend):
        for _ in range(3):
            sqlite_backend.upsert_heartbeat(
                runtime="Hermes", status="idle", health="ok",
                current_task_id=None, summary=None, now_iso=_now(),
            )
        beats = sqlite_backend.list_heartbeats()
        assert len([b for b in beats if b["runtime"] == "Hermes"]) == 1

    def test_list_heartbeats_returns_all(self, sqlite_backend: SQLiteBackend):
        sqlite_backend.upsert_heartbeat(
            runtime="Hermes", status="idle", health="ok",
            current_task_id=None, summary=None, now_iso=_now(),
        )
        sqlite_backend.upsert_heartbeat(
            runtime="OpenClaw", status="busy", health="ok",
            current_task_id="task-x", summary=None, now_iso=_now(),
        )
        beats = sqlite_backend.list_heartbeats()
        runtimes = {b["runtime"] for b in beats}
        assert "Hermes" in runtimes
        assert "OpenClaw" in runtimes

    # ── mark_stale_tasks ──────────────────────────────────────────────────────

    def test_mark_stale_tasks_expires_old_tasks(self, sqlite_backend: SQLiteBackend):
        from datetime import timedelta
        _make_task(sqlite_backend, task_id="task-stale", recipient="Hermes")
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=3600)).isoformat()
        sqlite_backend.claim_task(task_id="task-stale", runtime="Hermes", now_iso=old_time)
        # Force the updated_at to be old
        import sqlite3
        conn = sqlite3.connect(sqlite_backend.db_path)
        conn.execute("UPDATE tasks SET updated_at = ? WHERE task_id = ?", (old_time, "task-stale"))
        conn.commit()
        conn.close()

        result = sqlite_backend.mark_stale_tasks(
            max_age_seconds=60,
            stale_runtimes={"Hermes"},
            now_iso=_now(),
        )
        assert result["expired_count"] == 1
        assert "task-stale" in result["task_ids"]
        assert sqlite_backend.get_task("task-stale")["status"] == "expired"

    def test_mark_stale_tasks_skips_non_stale_runtimes(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-active", recipient="Hermes")
        sqlite_backend.claim_task(task_id="task-active", runtime="Hermes", now_iso=_now())
        result = sqlite_backend.mark_stale_tasks(
            max_age_seconds=60,
            stale_runtimes=set(),  # no stale runtimes
            now_iso=_now(),
        )
        assert result["expired_count"] == 0

    # ── reclaim_task ──────────────────────────────────────────────────────────

    def test_reclaim_task_succeeds(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-rec", recipient="Hermes")
        sqlite_backend.claim_task(task_id="task-rec", runtime="Hermes", now_iso=_now())
        result = sqlite_backend.reclaim_task(
            task_id="task-rec",
            new_runtime="OpenClaw",
            reason="Hermes went offline.",
            now_iso=_now(),
        )
        assert result["reclaimed"] is True
        task = sqlite_backend.get_task("task-rec")
        assert task["status"] == "open"
        assert task["owner"] is None
        assert task["recipient"] == "OpenClaw"

    def test_reclaim_task_fails_for_own_task(self, sqlite_backend: SQLiteBackend):
        _make_task(sqlite_backend, task_id="task-rec", recipient="Hermes")
        sqlite_backend.claim_task(task_id="task-rec", runtime="Hermes", now_iso=_now())
        result = sqlite_backend.reclaim_task(
            task_id="task-rec", new_runtime="Hermes",
            reason=".", now_iso=_now(),
        )
        assert result["reclaimed"] is False

    def test_reclaim_task_fails_for_missing(self, sqlite_backend: SQLiteBackend):
        result = sqlite_backend.reclaim_task(
            task_id="task-ghost", new_runtime="OpenClaw",
            reason=".", now_iso=_now(),
        )
        assert result["reclaimed"] is False


# ── Backend Loader Tests ──────────────────────────────────────────────────────

class TestBackendLoader:
    def setup_method(self):
        clear_backend_cache()

    def teardown_method(self):
        clear_backend_cache()

    def test_get_backend_returns_sqlite_by_default(self, vault: Path):
        """No bus_config.yaml → default to SQLiteBackend."""
        backend = get_backend(vault)
        assert isinstance(backend, SQLiteBackend)

    def test_get_backend_returns_same_instance(self, vault: Path):
        """Same vault_root → same backend instance (cached)."""
        b1 = get_backend(vault)
        b2 = get_backend(vault)
        assert b1 is b2

    def test_get_backend_returns_sqlite_for_explicit_local_config(self, vault: Path, tmp_path: Path):
        """mode: local in bus_config.yaml → SQLiteBackend."""
        config_dir = vault / "runtime" / "agent_bus"
        config_dir.mkdir(parents=True)
        (config_dir / "bus_config.yaml").write_text(
            "mode: local\nlocal:\n", encoding="utf-8"
        )
        backend = get_backend(vault)
        assert isinstance(backend, SQLiteBackend)

    def test_get_backend_raises_for_server_mode(self, vault: Path):
        """mode: server → NotImplementedError (Phase 10)."""
        config_dir = vault / "runtime" / "agent_bus"
        config_dir.mkdir(parents=True)
        (config_dir / "bus_config.yaml").write_text(
            "mode: server\nserver:\n  api_url: http://localhost:8765\n",
            encoding="utf-8",
        )
        with pytest.raises(NotImplementedError, match="server mode"):
            get_backend(vault)

    def test_get_backend_raises_for_unknown_mode(self, vault: Path):
        """Unknown mode → ValueError."""
        config_dir = vault / "runtime" / "agent_bus"
        config_dir.mkdir(parents=True)
        (config_dir / "bus_config.yaml").write_text(
            "mode: postgres\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="Unknown agent bus mode"):
            get_backend(vault)

    def test_get_backend_defaults_to_local_for_invalid_config(self, vault: Path):
        """Corrupt config file → fall back to local mode, not crash."""
        config_dir = vault / "runtime" / "agent_bus"
        config_dir.mkdir(parents=True)
        (config_dir / "bus_config.yaml").write_text(
            ":::not valid yaml:::", encoding="utf-8"
        )
        backend = get_backend(vault)
        assert isinstance(backend, SQLiteBackend)

    def test_clear_cache_forces_reinstantiation(self, vault: Path):
        """clear_backend_cache() forces new instantiation on next get_backend()."""
        b1 = get_backend(vault)
        clear_backend_cache(vault)
        b2 = get_backend(vault)
        assert b1 is not b2

    def test_none_vault_root_uses_repo_root(self):
        """vault_root=None uses repo root (backward compat)."""
        clear_backend_cache(None)
        backend = get_backend(None)
        assert isinstance(backend, SQLiteBackend)
        clear_backend_cache(None)


# ── SQLiteBackend-specific tests ──────────────────────────────────────────────

class TestSQLiteBackendSpecific:
    """Tests for SQLiteBackend internals that are local-mode specific."""

    def test_db_path_property(self, tmp_path: Path):
        vault = tmp_path / "vault"
        backend = SQLiteBackend(vault)
        assert backend.db_path == vault / "runtime" / "agent_bus" / "agent_bus.sqlite"

    def test_init_creates_db_file(self, tmp_path: Path):
        vault = tmp_path / "vault"
        backend = SQLiteBackend(vault)
        backend.init()
        assert backend.db_path.exists()

    def test_init_creates_parent_dirs(self, tmp_path: Path):
        vault = tmp_path / "deeply" / "nested" / "vault"
        backend = SQLiteBackend(vault)
        backend.init()
        assert backend.db_path.exists()

    def test_backend_init_error_on_unwritable_path(self, tmp_path: Path):
        """BackendInitError raised when storage cannot be initialized."""
        # Point schema at a nonexistent path to force init failure
        vault = tmp_path / "vault"
        backend = SQLiteBackend(vault)
        # Patch the schema path to a missing file to force failure
        backend._schema_file = Path("/nonexistent/schema.sql")
        with pytest.raises(BackendInitError):
            backend.init()
