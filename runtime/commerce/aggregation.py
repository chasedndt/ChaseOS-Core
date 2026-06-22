"""
runtime/commerce/aggregation.py — usage rollups (ADR-0005, handover 11 Phase 2).

Rolls append-only usage_events into usage_aggregates per (account, meter, period),
decimal-exact. Idempotent + recomputable: a re-run for a period replaces that
period's aggregate rows. Emits `usage.aggregated`. Pure stdlib over store.

`period` is the YYYY-MM month derived from occurred_at.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Optional

from runtime.commerce import events, store


def _period_of(occurred_at: str) -> str:
    return str(occurred_at)[:7] if occurred_at else "unknown"


def compute_aggregates(db_path, *, period: Optional[str] = None, actor: str = "system") -> dict[str, Any]:
    """Recompute usage_aggregates. If period given, only that YYYY-MM; else all.

    Returns {periods, groups_written, total_events}.
    """
    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT account_id, meter_id, quantity, occurred_at FROM usage_events"
        ).fetchall()

        groups: dict[tuple, list] = defaultdict(list)
        for r in rows:
            per = _period_of(r["occurred_at"])
            if period and per != period:
                continue
            groups[(r["account_id"], r["meter_id"], per)].append(Decimal(r["quantity"]))

        periods_touched = sorted({k[2] for k in groups})
        # Replace existing aggregates for the touched (account,meter,period) groups.
        for (acct, meter, per), quantities in groups.items():
            conn.execute(
                "DELETE FROM usage_aggregates WHERE "
                "(account_id IS ?) AND meter_id=? AND period=?",
                (acct, meter, per),
            )
            conn.execute(
                """INSERT INTO usage_aggregates
                   (aggregate_id,account_id,meter_id,period,quantity,event_count,computed_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (store.new_id("agg"), acct, meter, per, str(sum(quantities, Decimal(0))),
                 len(quantities), store.now_iso()),
            )
        conn.commit()
        total_events = sum(len(v) for v in groups.values())
        groups_written = len(groups)
    finally:
        conn.close()

    for per in periods_touched:
        events.emit(db_path, "usage.aggregated", actor=actor,
                    metadata={"period": per}, idempotency_key=None)
    return {"periods": periods_touched, "groups_written": groups_written, "total_events": total_events}


def get_aggregates(
    db_path,
    *,
    account_id: Optional[str] = None,
    period: Optional[str] = None,
) -> list[dict]:
    clauses, params = [], []
    if account_id is not None:
        clauses.append("account_id IS ?"); params.append(account_id)
    if period:
        clauses.append("period = ?"); params.append(period)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = store.connect(db_path)
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM usage_aggregates{where} ORDER BY period DESC, meter_id", params
        ).fetchall()]
    finally:
        conn.close()
