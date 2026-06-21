"""
sqlite_backend.py — SQLiteBackend (Local Mode)
===============================================

Implements BusBackend using a local SQLite database. This is the default backend
for ChaseOS installations running on a single machine.

WHEN TO USE THIS BACKEND
-------------------------
Use SQLiteBackend (mode: local in bus_config.yaml) when:
  - All runtimes (OpenClaw, Hermes, etc.) run on the same machine
  - No web dashboard or mobile push is required
  - You want zero infrastructure — no server process, no network config
  - You are developing or testing ChaseOS locally

WHEN TO SWITCH TO SERVER MODE
------------------------------
Switch to ServerBackend (mode: server) when:
  - Runtimes run on separate machines or VPS instances
  - You need a live web or mobile dashboard with push updates
  - You want multi-user or multi-tenant operation
  See: Agent-Bus-Backend-Architecture.md § Server Mode

DATABASE LOCATION
-----------------
The SQLite file is at: {vault_root}/runtime/agent_bus/agent_bus.sqlite

Schema is defined in: runtime/agent_bus/sqlite_schema.sql
Schema is applied via executescript() — idempotent (CREATE TABLE IF NOT EXISTS).

CONCURRENCY
-----------
SQLite WAL mode is set at connection time. This allows concurrent reads with
one writer. For the ChaseOS use case (2-3 runtimes on one machine) this is
more than sufficient. Claim operations are atomic via SQLite's exclusive write
lock — two runtimes cannot simultaneously claim the same task.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import BusBackend, BackendInitError


class SQLiteBackend(BusBackend):
    """BusBackend implementation using a local SQLite file.

    Instantiated by backend_loader.get_backend() when mode=local.
    Do not instantiate directly — always use get_backend().
    """

    def __init__(self, vault_root: Path, config: dict | None = None) -> None:
        """
        vault_root: absolute path to the vault root directory.
        config: the 'local' block from bus_config.yaml (optional).
                Currently unused — reserved for future config (e.g. db_filename override).
        """
        self._vault_root = Path(vault_root).resolve()
        self._config = config or {}
        self._db_file = self._resolve_db_path()
        self._schema_file = self._resolve_schema_path()

    # ── Path resolution ───────────────────────────────────────────────────────

    def _resolve_db_path(self) -> Path:
        return self._vault_root / "runtime" / "agent_bus" / "agent_bus.sqlite"

    def _resolve_schema_path(self) -> Path:
        candidate = self._vault_root / "runtime" / "agent_bus" / "sqlite_schema.sql"
        if candidate.exists():
            return candidate
        # Fall back to schema file alongside this module's package
        return Path(__file__).resolve().parents[1] / "sqlite_schema.sql"

    @property
    def db_path(self) -> Path:
        """Expose the SQLite file path for test introspection and CLI display."""
        return self._db_file

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def init(self) -> None:
        """Create the SQLite file and apply schema. Idempotent."""
        last_exc: Exception | None = None
        for attempt in range(3):
            conn: sqlite3.Connection | None = None
            try:
                self._db_file.parent.mkdir(parents=True, exist_ok=True)
                schema = self._schema_file.read_text(encoding="utf-8")
                conn = sqlite3.connect(self._db_file, timeout=30)
                conn.execute("PRAGMA busy_timeout=30000")
                self._ensure_schema_compatibility(conn)
                conn.executescript(schema)
                conn.commit()
                conn.close()
                return
            except Exception as exc:
                last_exc = exc
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                if isinstance(exc, sqlite3.OperationalError) and "disk I/O error" in str(exc) and attempt < 2:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                break
        raise BackendInitError(
            f"SQLiteBackend: could not initialize database at {self._db_file}: {last_exc}"
        ) from last_exc

    def _ensure_schema_compatibility(self, conn: sqlite3.Connection) -> None:
        """Upgrade legacy local schemas in place.

        The original bootstrap hard-coded Hermes/OpenClaw runtime CHECK constraints and
        lacked ingress-context columns. Channel-aware coordination and future runtime
        support require a more generic schema, so older local stores are rebuilt in
        place while preserving existing rows.
        """
        table_sql = {
            row[0]: row[1] or ""
            for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
        }
        task_columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        heartbeat_columns = {row[1] for row in conn.execute("PRAGMA table_info(heartbeats)").fetchall()}
        needs_rebuild = False
        if "tasks" in table_sql and "ingress_context_json" not in task_columns:
            needs_rebuild = True
        if "heartbeats" in table_sql and "heartbeat_key" not in heartbeat_columns:
            needs_rebuild = True
        legacy_markers = [
            "CHECK (sender IN ('Hermes', 'OpenClaw'))",
            "CHECK (recipient IN ('Hermes', 'OpenClaw'))",
            "CHECK (owner IN ('Hermes', 'OpenClaw'))",
            "CHECK (runtime IN ('Hermes', 'OpenClaw'))",
            "CHECK (owner_runtime IN ('Hermes', 'OpenClaw'))",
        ]
        for sql in table_sql.values():
            if any(marker in sql for marker in legacy_markers):
                needs_rebuild = True
                break
        if "tasks" in table_sql and "owner_instance" not in task_columns and not needs_rebuild:
            conn.execute("ALTER TABLE tasks ADD COLUMN owner_instance TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_owner_instance ON tasks(owner, owner_instance)")
            task_columns.add("owner_instance")
        if "tasks" in table_sql and "execution_constraints_json" not in task_columns and not needs_rebuild:
            conn.execute("ALTER TABLE tasks ADD COLUMN execution_constraints_json TEXT NOT NULL DEFAULT '{}'")
            task_columns.add("execution_constraints_json")
        if not needs_rebuild:
            return

        conn.executescript(
            """
            ALTER TABLE tasks RENAME TO tasks_legacy;
            ALTER TABLE events RENAME TO events_legacy;
            ALTER TABLE heartbeats RENAME TO heartbeats_legacy;
            ALTER TABLE locks RENAME TO locks_legacy;
            """
        )
        schema = self._schema_file.read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.executescript(
            """
            INSERT INTO tasks (
              task_id, run_id, reply_to, sender, recipient, intent, status, priority, owner, owner_instance,
              request, expected_output, depends_on_json, artifacts_json, ingress_context_json,
              execution_constraints_json, work_fingerprint, notes, created_at, updated_at, expires_at
            )
            SELECT
              task_id, run_id, reply_to, sender, recipient, intent, status, priority, owner, NULL,
              request, expected_output, depends_on_json, artifacts_json, '{}',
              '{}', NULL, notes, created_at, updated_at, expires_at
            FROM tasks_legacy;

            INSERT INTO events (
              event_id, task_id, run_id, sender, event_type, message, artifacts_json, created_at
            )
            SELECT
              event_id, task_id, run_id, sender, event_type, message, artifacts_json, created_at
            FROM events_legacy;

            INSERT INTO heartbeats (
              heartbeat_key, runtime, runtime_instance_id, heartbeat_scope,
              control_surface, control_surface_key, status, current_task_id,
              health, summary, last_seen
            )
            SELECT
              runtime, runtime, NULL, 'runtime',
              NULL, NULL, status, current_task_id,
              health, summary, last_seen
            FROM heartbeats_legacy;

            INSERT INTO locks (
              lock_name, owner_runtime, acquired_at, expires_at
            )
            SELECT
              lock_name, owner_runtime, acquired_at, expires_at
            FROM locks_legacy;

            DROP TABLE tasks_legacy;
            DROP TABLE events_legacy;
            DROP TABLE heartbeats_legacy;
            DROP TABLE locks_legacy;
            """
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection. Calls init() to ensure schema is applied.

        Prefer WAL on normal local filesystems, but DrvFS/Windows-mounted vaults
        can intermittently raise ``sqlite3.OperationalError: disk I/O error``
        when switching journal mode. ChaseOS live Studio Chat depends on Agent
        Bus reads/writes from WSL against ``/mnt/c``; fail over to DELETE
        journaling instead of wedging the runtime daemon or leaving chat tasks
        stuck in progress.
        """
        self.init()
        last_exc: Exception | None = None
        for attempt in range(3):
            conn = sqlite3.connect(self._db_file, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA busy_timeout=30000")
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.OperationalError:
                    conn.execute("PRAGMA journal_mode=DELETE")
                return conn
            except sqlite3.OperationalError as exc:
                last_exc = exc
                conn.close()
                if "disk I/O error" in str(exc) and attempt < 2:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise
        raise last_exc or sqlite3.OperationalError("SQLite connection failed")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_iso(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _json_loads(value: str | None) -> Any:
        if not value:
            return []
        return json.loads(value)

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["depends_on"] = SQLiteBackend._json_loads(item.pop("depends_on_json", "[]"))
        item["artifacts"] = SQLiteBackend._json_loads(item.pop("artifacts_json", "[]"))
        item["ingress_context"] = SQLiteBackend._json_loads(item.pop("ingress_context_json", "{}")) or {}
        item["execution_constraints"] = SQLiteBackend._json_loads(item.pop("execution_constraints_json", "{}")) or {}
        return item

    @staticmethod
    def _row_claim_lane(row: sqlite3.Row, lane_guard: dict[str, Any] | None = None) -> dict[str, str | None]:
        ingress = SQLiteBackend._json_loads(row["ingress_context_json"]) or {}
        guard = lane_guard or {}
        return {
            "work_fingerprint": str(row["work_fingerprint"] or guard.get("work_fingerprint") or "").strip() or None,
            "origin_message_id": str(ingress.get("origin_message_id") or guard.get("origin_message_id") or "").strip() or None,
            "conversation_key": str(ingress.get("conversation_key") or guard.get("conversation_key") or "").strip() or None,
            "source_platform": str(ingress.get("source_platform") or guard.get("source_platform") or "").strip() or None,
            "source_channel_id": str(ingress.get("source_channel_id") or guard.get("source_channel_id") or "").strip() or None,
            "source_thread_id": str(ingress.get("source_thread_id") or guard.get("source_thread_id") or "").strip() or None,
        }

    @staticmethod
    def _derive_owner_instance(lane: dict[str, str | None], runtime_instance_id: str | None = None) -> str | None:
        if runtime_instance_id:
            return runtime_instance_id
        source_platform = str(lane.get("source_platform") or "").strip().lower()
        conversation_key = str(lane.get("conversation_key") or "").strip()
        if source_platform == "discord" and conversation_key:
            source_thread_id = str(lane.get("source_thread_id") or "").strip()
            source_channel_id = str(lane.get("source_channel_id") or "").strip()
            if source_thread_id:
                return f"discord-thread-{source_thread_id}"
            if source_channel_id:
                return f"discord-channel-{source_channel_id}"
            return f"discord-lane-{conversation_key.replace(':', '-')}"
        return None

    @staticmethod
    def _row_claim_conflicts(
        conn: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        runtime: str,
        lane_guard: dict[str, Any] | None = None,
    ) -> tuple[dict[str, str | None], list[dict[str, Any]]]:
        lane = SQLiteBackend._row_claim_lane(row, lane_guard=lane_guard)
        comparable_keys = ("work_fingerprint", "origin_message_id", "conversation_key")
        if not any(lane.get(key) for key in comparable_keys):
            return lane, []

        active_rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE recipient = ?
              AND owner = ?
              AND task_id != ?
              AND status IN ('open', 'claimed', 'in_progress', 'blocked', 'review')
            ORDER BY created_at ASC, task_id ASC
            """,
            (runtime, runtime, row["task_id"]),
        ).fetchall()
        conflicts: list[dict[str, Any]] = []
        for active in active_rows:
            active_lane = SQLiteBackend._row_claim_lane(active)
            for key in comparable_keys:
                if lane.get(key) and lane.get(key) == active_lane.get(key):
                    conflicts.append(
                        {
                            "task_id": active["task_id"],
                            "status": active["status"],
                            "owner": active["owner"],
                            "owner_instance": active["owner_instance"] if "owner_instance" in active.keys() else None,
                            "reason": key,
                            "value": lane[key],
                        }
                    )
                    break
        return lane, conflicts

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        run_id: str,
        sender: str,
        event_type: str,
        message: str,
        artifacts: list[str] | None = None,
        created_at: str | None = None,
    ) -> str:
        event_id = f"evt-{task_id}-{event_type}-{uuid.uuid4().hex[:8]}"
        conn.execute(
            """
            INSERT INTO events
              (event_id, task_id, run_id, sender, event_type, message, artifacts_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task_id,
                run_id,
                sender,
                event_type,
                message,
                json.dumps(artifacts or []),
                created_at or self._now_iso(),
            ),
        )
        return event_id

    # ── Task operations ───────────────────────────────────────────────────────

    def create_task(
        self,
        *,
        task_id: str,
        run_id: str,
        sender: str,
        recipient: str,
        intent: str,
        priority: str,
        request: str,
        expected_output: str,
        depends_on: list[str],
        notes: str | None,
        expires_at: str | None,
        now_iso: str,
        ingress_context: dict[str, Any] | None = None,
        work_fingerprint: str | None = None,
        execution_constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conn = self._connect()
        try:
            if work_fingerprint:
                duplicate = conn.execute(
                    """
                    SELECT task_id, status FROM tasks
                    WHERE recipient = ?
                      AND work_fingerprint = ?
                      AND status IN ('open', 'claimed', 'in_progress', 'blocked', 'review')
                    ORDER BY created_at ASC, task_id ASC
                    LIMIT 1
                    """,
                    (recipient, work_fingerprint),
                ).fetchone()
                if duplicate is not None:
                    conn.close()
                    return {
                        "created": False,
                        "reason": (
                            f"active task with work_fingerprint '{work_fingerprint}' already exists "
                            f"for recipient '{recipient}'"
                        ),
                        "task_id": task_id,
                        "duplicate_task_id": duplicate["task_id"],
                        "duplicate_status": duplicate["status"],
                    }
            conn.execute(
                """
                INSERT INTO tasks
                  (task_id, run_id, sender, recipient, intent, status, priority,
                   owner, owner_instance, request, expected_output, depends_on_json, artifacts_json,
                   ingress_context_json, execution_constraints_json, work_fingerprint, notes, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?, NULL, NULL, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id, run_id, sender, recipient, intent, priority,
                    request, expected_output,
                    json.dumps(depends_on),
                    json.dumps(ingress_context or {}),
                    json.dumps(execution_constraints or {}),
                    work_fingerprint,
                    notes,
                    now_iso, now_iso,
                    expires_at,
                ),
            )
            self._insert_event(
                conn,
                task_id=task_id,
                run_id=run_id,
                sender=sender,
                event_type="created",
                message=f"Task created by {sender} for {recipient}: {request[:120]}",
                created_at=now_iso,
            )
            conn.commit()
        except Exception as exc:
            conn.close()
            return {"created": False, "reason": f"DB error: {exc}", "task_id": task_id}
        conn.close()
        return {
            "created": True,
            "task_id": task_id,
            "run_id": run_id,
            "sender": sender,
            "recipient": recipient,
            "execution_constraints": execution_constraints or {},
        }

    def list_tasks(
        self,
        *,
        recipient: str | None = None,
        status: str | None = None,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        where: list[str] = []
        params: list[Any] = []
        if recipient:
            where.append("recipient = ?")
            params.append(recipient)
        if status:
            where.append("status = ?")
            params.append(status)
        if owner:
            where.append("owner = ?")
            params.append(owner)

        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at ASC, task_id ASC"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [self._row_to_task(row) for row in rows]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        conn.close()
        return self._row_to_task(row) if row else None

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY created_at ASC, event_id ASC",
            (task_id,),
        ).fetchall()
        conn.close()
        result = []
        for row in rows:
            item = dict(row)
            item["artifacts"] = self._json_loads(item.pop("artifacts_json", "[]"))
            result.append(item)
        return result

    def claim_task(
        self,
        *,
        task_id: str,
        runtime: str,
        now_iso: str,
        lane_guard: dict[str, Any] | None = None,
        runtime_instance_id: str | None = None,
    ) -> dict[str, Any]:
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")

        def fail(payload: dict[str, Any]) -> dict[str, Any]:
            conn.rollback()
            conn.close()
            return payload

        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return fail({"claimed": False, "task_id": task_id, "reason": "task not found"})

        if row["recipient"] != runtime:
            return fail({
                "claimed": False,
                "task_id": task_id,
                "reason": f"task recipient is {row['recipient']}, not {runtime}",
            })

        if row["status"] != "open" or row["owner"] is not None:
            return fail({
                "claimed": False,
                "task_id": task_id,
                "reason": f"task is not claimable (status={row['status']}, owner={row['owner']})",
            })

        lane, conflicts = self._row_claim_conflicts(
            conn,
            row=row,
            runtime=runtime,
            lane_guard=lane_guard,
        )
        if conflicts:
            return fail({
                "claimed": False,
                "task_id": task_id,
                "runtime": runtime,
                "reason": "active lane conflict for runtime",
                "lane": lane,
                "conflicts": conflicts,
            })

        owner_instance = self._derive_owner_instance(lane, runtime_instance_id=runtime_instance_id)
        conn.execute(
            "UPDATE tasks SET status = ?, owner = ?, owner_instance = ?, updated_at = ? WHERE task_id = ?",
            ("claimed", runtime, owner_instance, now_iso, task_id),
        )
        self._insert_event(
            conn,
            task_id=task_id,
            run_id=row["run_id"],
            sender=runtime,
            event_type="claimed",
            message=f"{runtime} claimed task {task_id}.",
            created_at=now_iso,
        )
        conn.commit()
        conn.close()
        return {
            "claimed": True,
            "task_id": task_id,
            "runtime": runtime,
            "owner_instance": owner_instance,
            "lane": lane,
        }

    def update_task_status(
        self,
        *,
        task_id: str,
        runtime: str,
        status: str,
        event_type: str,
        message: str,
        artifacts: list[str] | None,
        now_iso: str,
    ) -> dict[str, Any]:
        conn = self._connect()
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return {"updated": False, "reason": "task not found", "task_id": task_id}

        owner = row["owner"]
        if owner is not None and owner != runtime:
            conn.close()
            return {"updated": False, "reason": f"task owned by {owner}", "task_id": task_id}

        next_owner = runtime
        next_owner_instance = row["owner_instance"] if "owner_instance" in row.keys() else None
        if status == "open":
            next_owner = None
            next_owner_instance = None
        elif row["owner"] is not None:
            next_owner = row["owner"]

        conn.execute(
            "UPDATE tasks SET status = ?, owner = ?, owner_instance = ?, updated_at = ?, artifacts_json = ? WHERE task_id = ?",
            (
                status,
                next_owner,
                next_owner_instance,
                now_iso,
                json.dumps(artifacts or self._json_loads(row["artifacts_json"])),
                task_id,
            ),
        )
        self._insert_event(
            conn,
            task_id=task_id,
            run_id=row["run_id"],
            sender=runtime,
            event_type=event_type,
            message=message,
            artifacts=artifacts,
            created_at=now_iso,
        )
        conn.commit()
        conn.close()
        return {"updated": True, "task_id": task_id, "status": status}

    # ── Heartbeat operations ──────────────────────────────────────────────────

    def upsert_heartbeat(
        self,
        *,
        runtime: str,
        status: str,
        health: str,
        current_task_id: str | None,
        summary: str | None,
        now_iso: str,
        runtime_instance_id: str | None = None,
        heartbeat_scope: str = "runtime",
        control_surface: str | None = None,
        control_surface_key: str | None = None,
    ) -> dict[str, Any]:
        conn = self._connect()
        scope = heartbeat_scope if heartbeat_scope in {"runtime", "instance"} else "runtime"
        instance_id = runtime_instance_id or None
        if scope == "instance" and not instance_id:
            instance_id = f"{runtime.lower()}-instance"
        heartbeat_key = runtime if scope == "runtime" else f"{runtime}:{instance_id}"
        conn.execute(
            """
            INSERT OR REPLACE INTO heartbeats
              (heartbeat_key, runtime, runtime_instance_id, heartbeat_scope,
               control_surface, control_surface_key, status, current_task_id,
               health, summary, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                heartbeat_key,
                runtime,
                instance_id,
                scope,
                control_surface,
                control_surface_key,
                status,
                current_task_id,
                health,
                summary,
                now_iso,
            ),
        )
        conn.commit()
        conn.close()
        return {
            "heartbeat_key": heartbeat_key,
            "runtime": runtime,
            "runtime_instance_id": instance_id,
            "heartbeat_scope": scope,
            "control_surface": control_surface,
            "control_surface_key": control_surface_key,
            "status": status,
            "health": health,
            "current_task_id": current_task_id,
            "summary": summary,
            "last_seen": now_iso,
        }

    def list_heartbeats(self) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM heartbeats ORDER BY runtime ASC, heartbeat_scope ASC, last_seen DESC"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        conn.close()

        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["artifacts"] = self._json_loads(item.pop("artifacts_json", "[]"))
            events.append(item)
        return events

    # ── Maintenance operations ────────────────────────────────────────────────

    def mark_stale_tasks(
        self,
        *,
        max_age_seconds: int,
        stale_runtimes: set[str],
        now_iso: str,
    ) -> dict[str, Any]:
        now = self._parse_iso(now_iso)
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('claimed','in_progress','blocked','review') "
            "AND owner IS NOT NULL"
        ).fetchall()

        expired_ids: list[str] = []
        skipped_not_stale: list[str] = []

        for row in rows:
            owner = row["owner"]
            if not owner or owner not in stale_runtimes:
                skipped_not_stale.append(row["task_id"])
                continue
            updated_at = self._parse_iso(row["updated_at"])
            age = (now - updated_at).total_seconds()
            if age <= max_age_seconds:
                continue
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                ("expired", now_iso, row["task_id"]),
            )
            self._insert_event(
                conn,
                task_id=row["task_id"],
                run_id=row["run_id"],
                sender=owner,
                event_type="expired",
                message=(
                    f"Task expired: owning runtime '{owner}' was stale and "
                    f"task age exceeded {max_age_seconds}s."
                ),
                created_at=now_iso,
            )
            expired_ids.append(row["task_id"])

        conn.commit()
        conn.close()
        return {
            "expired_count": len(expired_ids),
            "task_ids": expired_ids,
            "skipped_not_stale_count": len(skipped_not_stale),
            "skipped_not_stale_task_ids": skipped_not_stale,
            "stale_runtimes": sorted(stale_runtimes),
            "now": now_iso,
        }

    def reclaim_task(
        self,
        *,
        task_id: str,
        new_runtime: str,
        reason: str,
        now_iso: str,
    ) -> dict[str, Any]:
        active_states = {"open", "claimed", "in_progress", "blocked", "review"}
        conn = self._connect()
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return {"reclaimed": False, "task_id": task_id, "reason": "Task not found"}

        current_owner = row["owner"]
        current_status = row["status"]

        if current_status not in active_states:
            conn.close()
            return {
                "reclaimed": False,
                "task_id": task_id,
                "reason": f"Task status '{current_status}' is not reclaimable (must be active)",
            }

        if current_owner == new_runtime:
            conn.close()
            return {
                "reclaimed": False,
                "task_id": task_id,
                "reason": f"Task already owned by {new_runtime}",
            }

        conn.execute(
            "UPDATE tasks SET status = 'open', owner = NULL, owner_instance = NULL, recipient = ?, updated_at = ? "
            "WHERE task_id = ?",
            (new_runtime, now_iso, task_id),
        )
        self._insert_event(
            conn,
            task_id=task_id,
            run_id=row["run_id"],
            sender=new_runtime,
            event_type="notice",
            message=f"Task reclaimed from {current_owner or 'unowned'} by {new_runtime}. {reason}",
            created_at=now_iso,
        )
        conn.commit()
        conn.close()
        return {
            "reclaimed": True,
            "task_id": task_id,
            "previous_owner": current_owner,
            "new_recipient": new_runtime,
        }
