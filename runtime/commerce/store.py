"""
runtime/commerce/store.py — commerce SQLite store (ADR-0005/0007/0012).

Local/test-mode persistence for the commercial foundation, mirroring the canonical
agent_bus SQLiteBackend pattern: idempotent init, WAL, and a stored schema_version
(PRAGMA user_version) from day one so versioned migrations are possible and a future
Neon swap has a real version number to migrate from. Pure stdlib (sqlite3).

Holds the APPEND-ONLY usage/cost/audit events, the local account/workspace/
membership model, and (v3, ADR-0005) the append-only double-entry ledger tables.
The ledger is **test-mode only**: no real money moves, no provider/network calls,
and live charges remain feature-flagged OFF behind the launch gate (ADR-0006). The
double-entry LOGIC lives in ``ledger.py``; billing providers in ``billing.py``.

Money: amount_minor INTEGER (minor units), never float. Usage quantities are stored
as TEXT and summed with decimal.Decimal to stay exact.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 3  # v1 accounts/usage/cost/audit; v2 grants/prices/aggregates/events/roles/subs; v3 double-entry ledger + billing webhooks


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# Back-compat alias (older callers used the private name).
_now_iso = now_iso


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


_new_id = new_id


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Sibling modules (grants/pricing/aggregation/events) use store.connect().
_connect = connect


_DDL = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (account_id, user_id)
);
CREATE TABLE IF NOT EXISTS usage_events (
    event_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    account_id TEXT,
    workspace_id TEXT,
    user_id TEXT,
    meter_id TEXT NOT NULL,
    quantity TEXT NOT NULL,
    unit TEXT,
    occurred_at TEXT NOT NULL,
    provider TEXT,
    provider_reference TEXT,
    metadata TEXT
);
CREATE TABLE IF NOT EXISTS provider_cost_events (
    cost_event_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    usage_event_id TEXT,
    provider TEXT NOT NULL,
    amount_minor INTEGER NOT NULL,
    currency TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    metadata TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
    audit_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT,
    account_id TEXT,
    workspace_id TEXT,
    occurred_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    idempotency_key TEXT,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS ix_usage_account ON usage_events(account_id);
CREATE INDEX IF NOT EXISTS ix_usage_meter ON usage_events(meter_id);
CREATE INDEX IF NOT EXISTS ix_cost_usage ON provider_cost_events(usage_event_id);
CREATE INDEX IF NOT EXISTS ix_audit_type ON audit_events(event_type);
"""

# Schema v2 additions (entitlement grants, price versions, usage rollups, domain
# events, RBAC roles, api keys, subscription/billing refs). Adding these to an
# existing v1 store is the migration (IF NOT EXISTS + a user_version bump).
_DDL_V2 = """
CREATE TABLE IF NOT EXISTS roles (
    role_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,
    description TEXT
);
CREATE TABLE IF NOT EXISTS api_keys (
    api_key_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT,
    prefix TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS subscriptions (
    subscription_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    status TEXT NOT NULL,
    billing_customer_ref TEXT,
    current_period_end TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_customers (
    account_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_customer_ref TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entitlement_grants (
    grant_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    feature_id TEXT NOT NULL,
    source TEXT NOT NULL,
    allow INTEGER NOT NULL,
    limit_value TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    metadata TEXT
);
CREATE TABLE IF NOT EXISTS price_versions (
    price_version_id TEXT PRIMARY KEY,
    meter_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    unit_amount_minor TEXT NOT NULL,
    version INTEGER NOT NULL,
    effective_from TEXT NOT NULL,
    retired_at TEXT,
    orchestration_fee_minor INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage_aggregates (
    aggregate_id TEXT PRIMARY KEY,
    account_id TEXT,
    meter_id TEXT NOT NULL,
    period TEXT NOT NULL,
    quantity TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    computed_at TEXT NOT NULL,
    UNIQUE(account_id, meter_id, period)
);
CREATE TABLE IF NOT EXISTS domain_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    actor TEXT,
    account_id TEXT,
    workspace_id TEXT,
    occurred_at TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    audit_ref TEXT,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS ix_grants_account ON entitlement_grants(account_id);
CREATE INDEX IF NOT EXISTS ix_grants_feature ON entitlement_grants(feature_id);
CREATE INDEX IF NOT EXISTS ix_prices_meter ON price_versions(meter_id);
CREATE INDEX IF NOT EXISTS ix_domevents_type ON domain_events(event_type);
CREATE INDEX IF NOT EXISTS ix_domevents_account ON domain_events(account_id);
"""

# Schema v3 additions (ADR-0005/0006): the append-only double-entry ledger
# (transactions + balanced entries) and billing webhook idempotency. Adding these to
# an existing v2 store is the migration (IF NOT EXISTS + a user_version bump 2→3).
# Test-mode only — no real money, no provider/network calls.
_DDL_V3 = """
CREATE TABLE IF NOT EXISTS ledger_transactions (
    txn_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    occurred_at TEXT NOT NULL,
    metadata TEXT
);
CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id TEXT PRIMARY KEY,
    txn_id TEXT NOT NULL,
    ledger_account TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount_minor INTEGER NOT NULL,
    currency TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_webhook_events (
    webhook_event_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    event_type TEXT NOT NULL,
    received_at TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0,
    ledger_txn_id TEXT,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS ix_ledger_entries_txn ON ledger_entries(txn_id);
CREATE INDEX IF NOT EXISTS ix_ledger_entries_acct ON ledger_entries(ledger_account);
CREATE INDEX IF NOT EXISTS ix_ledger_txn_account ON ledger_transactions(account_id);
"""


def init_store(db_path: str | Path) -> None:
    """Idempotent: create tables + run migrations, stamp PRAGMA user_version.

    Fresh DB → creates v1+v2+v3 tables, sets user_version=SCHEMA_VERSION. Existing
    older DB → the _DDL_V* scripts add new tables (IF NOT EXISTS) and the version bumps.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.executescript(_DDL)
        conn.executescript(_DDL_V2)
        conn.executescript(_DDL_V3)
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def get_schema_version(db_path: str | Path) -> int:
    conn = _connect(db_path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


# ── accounts / workspaces / memberships (local-test-mode; ADR-0012) ────────────
def create_account(db_path: str | Path, *, name: str, plan_id: str) -> str:
    aid = _new_id("acct")
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO accounts(account_id,name,plan_id,created_at) VALUES (?,?,?,?)",
            (aid, name, plan_id, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return aid


def set_account_plan(db_path: str | Path, account_id: str, plan_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE accounts SET plan_id=? WHERE account_id=?", (plan_id, account_id))
        conn.commit()
    finally:
        conn.close()


def get_account(db_path: str | Path, account_id: str) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_workspace(db_path: str | Path, *, account_id: str, name: str) -> str:
    wid = _new_id("ws")
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO workspaces(workspace_id,account_id,name,created_at) VALUES (?,?,?,?)",
            (wid, account_id, name, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return wid


def add_membership(db_path: str | Path, *, account_id: str, user_id: str, role: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO memberships(account_id,user_id,role,created_at) VALUES (?,?,?,?)",
            (account_id, user_id, role, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


# ── append-only events ─────────────────────────────────────────────────────────
def record_usage_event(
    db_path: str | Path,
    *,
    idempotency_key: str,
    meter_id: str,
    quantity: str | int | float | Decimal,
    account_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    unit: Optional[str] = None,
    occurred_at: Optional[str] = None,
    provider: Optional[str] = None,
    provider_reference: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict[str, Any]:
    """Append a usage event. Idempotent on idempotency_key (provider:job:dimension).

    Returns {event_id, inserted}. A duplicate key is a no-op with inserted=False.
    """
    qty = str(Decimal(str(quantity)))  # validate + normalise; raises on bad input
    eid = _new_id("use")
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO usage_events
               (event_id,idempotency_key,account_id,workspace_id,user_id,meter_id,
                quantity,unit,occurred_at,provider,provider_reference,metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, idempotency_key, account_id, workspace_id, user_id, meter_id, qty, unit,
             occurred_at or _now_iso(), provider, provider_reference,
             json.dumps(metadata or {})),
        )
        conn.commit()
        inserted = cur.rowcount == 1
        if not inserted:
            row = conn.execute(
                "SELECT event_id FROM usage_events WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            eid = row["event_id"] if row else eid
        return {"event_id": eid, "inserted": inserted}
    finally:
        conn.close()


def record_provider_cost_event(
    db_path: str | Path,
    *,
    idempotency_key: str,
    provider: str,
    amount_minor: int,
    currency: str,
    usage_event_id: Optional[str] = None,
    occurred_at: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict[str, Any]:
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
        raise ValueError("amount_minor must be an int in minor units")
    if not (isinstance(currency, str) and len(currency) == 3):
        raise ValueError("currency must be a 3-letter code")
    cid = _new_id("cost")
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO provider_cost_events
               (cost_event_id,idempotency_key,usage_event_id,provider,amount_minor,currency,occurred_at,metadata)
               VALUES (?,?,?,?,?,?,?,?)""",
            (cid, idempotency_key, usage_event_id, provider, amount_minor, currency,
             occurred_at or _now_iso(), json.dumps(metadata or {})),
        )
        conn.commit()
        return {"cost_event_id": cid, "inserted": cur.rowcount == 1}
    finally:
        conn.close()


def record_audit_event(
    db_path: str | Path,
    *,
    event_type: str,
    actor: Optional[str] = None,
    account_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    aid = _new_id("aud")
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO audit_events
               (audit_id,event_type,actor,account_id,workspace_id,occurred_at,schema_version,idempotency_key,metadata)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (aid, event_type, actor, account_id, workspace_id, _now_iso(), SCHEMA_VERSION,
             idempotency_key, json.dumps(metadata or {})),
        )
        conn.commit()
    finally:
        conn.close()
    return aid


# ── read aggregates (free basic Insights; admin read-only) ─────────────────────
def aggregate_usage(
    db_path: str | Path,
    *,
    account_id: Optional[str] = None,
    since: Optional[str] = None,
) -> dict[str, str]:
    """Sum usage quantity per meter (decimal-exact). Returns {meter_id: total_str}."""
    clauses, params = [], []
    if account_id:
        clauses.append("account_id = ?"); params.append(account_id)
    if since:
        clauses.append("occurred_at >= ?"); params.append(since)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT meter_id, quantity FROM usage_events{where}", params
        ).fetchall()
    finally:
        conn.close()
    totals: dict[str, Decimal] = {}
    for r in rows:
        totals[r["meter_id"]] = totals.get(r["meter_id"], Decimal(0)) + Decimal(r["quantity"])
    return {k: str(v) for k, v in totals.items()}


def provider_cost_total(db_path: str | Path, *, currency: str = "GBP") -> int:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_minor),0) AS t FROM provider_cost_events WHERE currency=?",
            (currency,),
        ).fetchone()
        return int(row["t"])
    finally:
        conn.close()
