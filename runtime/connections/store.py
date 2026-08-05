"""SQLite registry store for ChaseOS Connections.

The store is local-first and audit-oriented. It creates schema only; it does not
store provider tokens, call OAuth endpoints, or ingest provider content.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from runtime.connections.manifests import list_provider_manifests
from runtime.connections.models import ConnectionManifest, utc_now

DEFAULT_DB_RELATIVE_PATH = Path(".chaseos") / "connections.db"
SCHEMA_VERSION = "connections.registry.v1"

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS connections (
      id TEXT PRIMARY KEY,
      provider TEXT NOT NULL,
      display_name TEXT NOT NULL,
      owner_user_id TEXT NOT NULL,
      workspace_id TEXT,
      account_ref TEXT,
      status TEXT NOT NULL,
      auth_method TEXT NOT NULL,
      permission_profile TEXT NOT NULL DEFAULT 'read_only',
      scopes_json TEXT NOT NULL DEFAULT '[]',
      metadata_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      last_success_at TEXT,
      last_error TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS connection_capabilities (
      id TEXT PRIMARY KEY,
      connection_id TEXT NOT NULL REFERENCES connections(id),
      tool_name TEXT NOT NULL,
      action_type TEXT NOT NULL,
      safety_level TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 0,
      constraints_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(connection_id, tool_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_jobs (
      id TEXT PRIMARY KEY,
      connection_id TEXT NOT NULL REFERENCES connections(id),
      sync_type TEXT NOT NULL,
      status TEXT NOT NULL,
      cursor TEXT,
      started_at TEXT,
      finished_at TEXT,
      items_seen INTEGER DEFAULT 0,
      items_written INTEGER DEFAULT 0,
      error TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_items (
      id TEXT PRIMARY KEY,
      provider TEXT NOT NULL,
      connection_id TEXT NOT NULL REFERENCES connections(id),
      source_type TEXT NOT NULL,
      external_id TEXT NOT NULL,
      parent_external_id TEXT,
      canonical_url TEXT,
      title TEXT,
      author_ref TEXT,
      occurred_at TEXT,
      ingested_at TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      raw_json_path TEXT,
      text_content TEXT,
      metadata_json TEXT NOT NULL DEFAULT '{}',
      privacy_level TEXT NOT NULL DEFAULT 'normal',
      UNIQUE(provider, connection_id, external_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entities (
      id TEXT PRIMARY KEY,
      type TEXT NOT NULL,
      name TEXT NOT NULL,
      canonical_key TEXT,
      aliases_json TEXT NOT NULL DEFAULT '[]',
      metadata_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS relationships (
      id TEXT PRIMARY KEY,
      subject_entity_id TEXT NOT NULL REFERENCES entities(id),
      predicate TEXT NOT NULL,
      object_entity_id TEXT REFERENCES entities(id),
      object_value TEXT,
      confidence REAL NOT NULL DEFAULT 0.7,
      valid_from TEXT,
      valid_to TEXT,
      observed_at TEXT NOT NULL,
      source_item_id TEXT REFERENCES source_items(id),
      extraction_model TEXT,
      metadata_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memories (
      id TEXT PRIMARY KEY,
      namespace TEXT NOT NULL,
      memory_type TEXT NOT NULL,
      content TEXT NOT NULL,
      confidence REAL NOT NULL DEFAULT 0.7,
      source_item_id TEXT REFERENCES source_items(id),
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      valid_until TEXT,
      pinned INTEGER NOT NULL DEFAULT 0,
      sensitive INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_invocations (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      connection_id TEXT,
      tool_name TEXT NOT NULL,
      action_type TEXT NOT NULL,
      request_json TEXT NOT NULL,
      response_summary TEXT,
      status TEXT NOT NULL,
      approval_id TEXT,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS approvals (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      requested_by TEXT NOT NULL,
      approval_type TEXT NOT NULL,
      target_provider TEXT NOT NULL,
      target_ref TEXT,
      preview_text TEXT,
      status TEXT NOT NULL,
      decided_by TEXT,
      created_at TEXT NOT NULL,
      decided_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS connection_registry_meta (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
]


def db_path_for_vault(vault_root: Path) -> Path:
    return vault_root / DEFAULT_DB_RELATIVE_PATH


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_store(db_path: Path) -> dict[str, Any]:
    with _connect(db_path) as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.execute(
            "INSERT OR REPLACE INTO connection_registry_meta(key, value, updated_at) VALUES (?, ?, ?)",
            ("schema_version", SCHEMA_VERSION, utc_now()),
        )
        table_count = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    return {"db_path": str(db_path), "schema_version": SCHEMA_VERSION, "table_count": int(table_count)}


def seed_manifest_connection(
    db_path: Path,
    manifest: ConnectionManifest,
    *,
    owner_user_id: str = "local_operator",
    status: str = "disconnected",
) -> str:
    """Create an audit-visible placeholder connection row from a provider manifest."""
    now = utc_now()
    connection_id = f"conn_{manifest.id}_{uuid.uuid4().hex[:10]}"
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO connections(
              id, provider, display_name, owner_user_id, workspace_id, account_ref,
              status, auth_method, permission_profile, scopes_json, metadata_json,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                connection_id,
                manifest.id,
                manifest.name,
                owner_user_id,
                None,
                None,
                status,
                str(manifest.auth.get("type") or "none"),
                manifest.default_profile(),
                json.dumps([]),
                json.dumps({"manifest_version": manifest.version, "manifest_status": manifest.status}),
                now,
                now,
            ),
        )
        for cap in manifest.capabilities:
            conn.execute(
                """
                INSERT INTO connection_capabilities(
                  id, connection_id, tool_name, action_type, safety_level, enabled,
                  constraints_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"cap_{uuid.uuid4().hex[:12]}",
                    connection_id,
                    cap.tool_name,
                    cap.action_type,
                    cap.safety_level,
                    1 if cap.enabled_by_default else 0,
                    json.dumps(cap.constraints, sort_keys=True),
                    now,
                    now,
                ),
            )
    return connection_id


def list_connections(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, provider, display_name, status, auth_method, permission_profile, updated_at, last_success_at, last_error FROM connections ORDER BY updated_at DESC, provider ASC"
        ).fetchall()
    return [dict(row) for row in rows]


def registry_overview(vault_root: Path) -> dict[str, Any]:
    db_path = db_path_for_vault(vault_root)
    manifests = list_provider_manifests()
    connections = list_connections(db_path)
    return {
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "available_providers": [manifest.to_dict() for manifest in manifests],
        "connections": connections,
        "policy": {
            "default_permission_profile": "read_only",
            "require_approval_for_writes": True,
            "enable_ambient_by_default": False,
            "personal_private_sources_opt_in": True,
            "log_all_tool_invocations": True,
            "redact_secrets_before_context": True,
            "separate_private_source_from_public_egress": True,
        },
    }
