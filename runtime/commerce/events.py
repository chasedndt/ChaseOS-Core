"""
runtime/commerce/events.py — canonical domain events (handover 12 §8).

Append-only domain-event emission into the `domain_events` table. Every event
carries event_id, schema_version, actor, account/workspace, timestamp, optional
idempotency_key, optional audit_ref, and safe metadata. Pure stdlib over store.

Emission is best-effort and never the source of truth for money — the ledger
(Phase E) and the append-only usage/cost tables remain authoritative.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from runtime.commerce import store

CANONICAL_EVENTS = frozenset({
    "account.created",
    "subscription.started",
    "subscription.changed",
    "entitlement.granted",
    "entitlement.expired",
    "usage.recorded",
    "usage.aggregated",
    "balance.topup_succeeded",
    "balance.reserved",
    "balance.captured",
    "balance.released",
    "balance.refunded",
    "job.queued",
    "job.started",
    "job.completed",
    "job.failed",
    "deployment.created",
    "runtime.provisioned",
    "runtime.unhealthy",
    "marketplace.listing_published",
    "marketplace.order_paid",
    "marketplace.refund_created",
    "marketplace.payout_created",
    "provider.cost_ingested",
    "reconciliation.failed",
})


def emit(
    db_path,
    event_type: str,
    *,
    actor: Optional[str] = None,
    account_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    audit_ref: Optional[str] = None,
    metadata: Optional[dict] = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Append a canonical domain event. Idempotent on idempotency_key if given.

    strict=True rejects event types outside CANONICAL_EVENTS (catches typos).
    Returns {event_id, inserted}.
    """
    if strict and event_type not in CANONICAL_EVENTS:
        raise ValueError(f"unknown domain event type: {event_type!r}")
    eid = store.new_id("evt")
    conn = store.connect(db_path)
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO domain_events
               (event_id,event_type,schema_version,actor,account_id,workspace_id,occurred_at,idempotency_key,audit_ref,metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (eid, event_type, store.SCHEMA_VERSION, actor, account_id, workspace_id,
             store.now_iso(), idempotency_key, audit_ref, json.dumps(metadata or {})),
        )
        conn.commit()
        inserted = cur.rowcount == 1
        if not inserted and idempotency_key:
            row = conn.execute(
                "SELECT event_id FROM domain_events WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row:
                eid = row["event_id"]
        return {"event_id": eid, "inserted": inserted}
    finally:
        conn.close()


def list_events(
    db_path,
    *,
    event_type: Optional[str] = None,
    account_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    clauses, params = [], []
    if event_type:
        clauses.append("event_type = ?"); params.append(event_type)
    if account_id:
        clauses.append("account_id = ?"); params.append(account_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT * FROM domain_events{where} ORDER BY occurred_at DESC, event_id DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
