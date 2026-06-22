"""
runtime/commerce/ledger.py — append-only double-entry ledger (ADR-0005).

**Test-mode only.** No real money moves and no provider/network calls happen here.
The internal ledger is *authoritative*; external billing providers (``billing.py``)
reconcile against it, never the other way round.

Money is ``amount_minor`` (int, minor units) + a 3-letter ``currency``; never float.
Every monetary event is a balanced transaction (sum of debit amounts == sum of credit
amounts). Balances are *derived* from entries — business/UI code never mutates a
balance directly. Operations are idempotent on ``idempotency_key``.

Balance buckets per account (reservation state machine, ADR-0005):
``customer:<account>:available`` and ``customer:<account>:reserved``. The contra
accounts ``external`` (funds in/out) and ``revenue`` (captured charges) balance each
transaction. Flow: top_up → available; reserve (available→reserved); capture (reserved
→ revenue for the actual charge, remainder back to available, cost/margin recorded);
release (reserved→available); refund (available→external).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from runtime.commerce import store

_AVAILABLE = "available"
_RESERVED = "reserved"
_EXTERNAL = "external"
_REVENUE = "revenue"

# Ledger transaction kind → canonical domain event (runtime.commerce.events).
# Emitted atomically with the transaction so the canonical event stream reflects
# ledger activity. Kept in sync with events.CANONICAL_EVENTS.
_KIND_TO_EVENT = {
    "top_up": "balance.topup_succeeded",
    "reserve": "balance.reserved",
    "capture": "balance.captured",
    "release": "balance.released",
    "refund": "balance.refunded",
}


class LedgerError(ValueError):
    """Invalid ledger operation (bad money, unbalanced transaction, etc.)."""


class InsufficientFundsError(LedgerError):
    """Not enough available/reserved balance for the requested operation."""


def _acct(account_id: str, bucket: str) -> str:
    return f"customer:{account_id}:{bucket}"


def _validate_money(amount_minor: int, currency: str) -> None:
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
        raise LedgerError("amount_minor must be an int in minor units (never float)")
    if amount_minor <= 0:
        raise LedgerError("amount_minor must be positive")
    if not (isinstance(currency, str) and len(currency) == 3):
        raise LedgerError("currency must be a 3-letter code")


def _existing_txn(conn: sqlite3.Connection, idempotency_key: str) -> Optional[str]:
    row = conn.execute(
        "SELECT txn_id FROM ledger_transactions WHERE idempotency_key=?", (idempotency_key,)
    ).fetchone()
    return row["txn_id"] if row else None


def _balance_of(conn: sqlite3.Connection, ledger_account: str, currency: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE direction WHEN 'credit' THEN amount_minor ELSE -amount_minor END),0) AS b "
        "FROM ledger_entries WHERE ledger_account=? AND currency=?",
        (ledger_account, currency),
    ).fetchone()
    return int(row["b"])


def _post(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    currency: str,
    kind: str,
    idempotency_key: str,
    entries: list[tuple[str, str, int]],
    metadata: Optional[dict] = None,
) -> dict[str, Any]:
    """Insert a balanced transaction + its entries. Caller has checked idempotency."""
    debits = sum(a for (_, d, a) in entries if d == "debit")
    credits = sum(a for (_, d, a) in entries if d == "credit")
    if debits != credits:
        raise LedgerError(f"unbalanced transaction: debits={debits} != credits={credits}")
    if debits == 0:
        raise LedgerError("transaction has no monetary movement")
    txn_id = store.new_id("ltx")
    ts = store.now_iso()
    cur = conn.execute(
        "INSERT OR IGNORE INTO ledger_transactions"
        "(txn_id,account_id,currency,kind,idempotency_key,occurred_at,metadata) VALUES (?,?,?,?,?,?,?)",
        (txn_id, account_id, currency, kind, idempotency_key, ts, json.dumps(metadata or {})),
    )
    if cur.rowcount != 1:  # lost an idempotency race — return the winner, post nothing
        return {"txn_id": _existing_txn(conn, idempotency_key) or txn_id, "inserted": False}
    for ledger_account, direction, amount in entries:
        conn.execute(
            "INSERT INTO ledger_entries"
            "(entry_id,txn_id,ledger_account,direction,amount_minor,currency,occurred_at) VALUES (?,?,?,?,?,?,?)",
            (store.new_id("len"), txn_id, ledger_account, direction, amount, currency, ts),
        )
    # Emit the canonical domain event atomically with the transaction.
    event_type = _KIND_TO_EVENT.get(kind)
    if event_type:
        conn.execute(
            "INSERT OR IGNORE INTO domain_events"
            "(event_id,event_type,schema_version,actor,account_id,workspace_id,occurred_at,idempotency_key,audit_ref,metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (store.new_id("evt"), event_type, store.SCHEMA_VERSION, None, account_id, None, ts,
             f"ledgerevt:{idempotency_key}", txn_id, json.dumps(metadata or {})),
        )
    return {"txn_id": txn_id, "inserted": True}


def top_up(db_path, *, account_id, amount_minor, idempotency_key, currency="GBP", metadata=None) -> dict[str, Any]:
    """Credit the customer's available balance from an external funding source."""
    _validate_money(amount_minor, currency)
    conn = store.connect(db_path)
    try:
        existing = _existing_txn(conn, idempotency_key)
        if existing:
            return {"txn_id": existing, "inserted": False}
        res = _post(
            conn, account_id=account_id, currency=currency, kind="top_up", idempotency_key=idempotency_key,
            entries=[(_acct(account_id, _AVAILABLE), "credit", amount_minor), (_EXTERNAL, "debit", amount_minor)],
            metadata=metadata,
        )
        conn.commit()
        return res
    finally:
        conn.close()


def reserve(db_path, *, account_id, amount_minor, idempotency_key, currency="GBP", metadata=None) -> dict[str, Any]:
    """Move available → reserved (worst-case hold before a metered operation)."""
    _validate_money(amount_minor, currency)
    conn = store.connect(db_path)
    try:
        existing = _existing_txn(conn, idempotency_key)
        if existing:
            return {"txn_id": existing, "inserted": False}
        avail = _balance_of(conn, _acct(account_id, _AVAILABLE), currency)
        if avail < amount_minor:
            raise InsufficientFundsError(f"available {avail} < reserve {amount_minor} {currency}")
        res = _post(
            conn, account_id=account_id, currency=currency, kind="reserve", idempotency_key=idempotency_key,
            entries=[(_acct(account_id, _AVAILABLE), "debit", amount_minor), (_acct(account_id, _RESERVED), "credit", amount_minor)],
            metadata=metadata,
        )
        conn.commit()
        return res
    finally:
        conn.close()


def capture(
    db_path, *, account_id, reserved_minor, actual_minor, idempotency_key,
    currency="GBP", provider_cost_minor=0, metadata=None,
) -> dict[str, Any]:
    """Settle a reservation: charge ``actual_minor`` to revenue, return the remainder to
    available, and record provider cost + margin. ``actual_minor`` ∈ [0, reserved_minor]."""
    _validate_money(reserved_minor, currency)
    if not isinstance(actual_minor, int) or isinstance(actual_minor, bool) or not (0 <= actual_minor <= reserved_minor):
        raise LedgerError("actual_minor must be an int in [0, reserved_minor]")
    if not isinstance(provider_cost_minor, int) or isinstance(provider_cost_minor, bool) or provider_cost_minor < 0:
        raise LedgerError("provider_cost_minor must be a non-negative int")
    conn = store.connect(db_path)
    try:
        existing = _existing_txn(conn, idempotency_key)
        if existing:
            return {"txn_id": existing, "inserted": False}
        reserved_bal = _balance_of(conn, _acct(account_id, _RESERVED), currency)
        if reserved_bal < reserved_minor:
            raise InsufficientFundsError(f"reserved {reserved_bal} < capture-against {reserved_minor} {currency}")
        remainder = reserved_minor - actual_minor
        entries: list[tuple[str, str, int]] = [(_acct(account_id, _RESERVED), "debit", reserved_minor)]
        if actual_minor > 0:
            entries.append((_REVENUE, "credit", actual_minor))
        if remainder > 0:
            entries.append((_acct(account_id, _AVAILABLE), "credit", remainder))
        md = {
            **(metadata or {}),
            "provider_cost_minor": provider_cost_minor,
            "charge_minor": actual_minor,
            "margin_minor": actual_minor - provider_cost_minor,
        }
        res = _post(
            conn, account_id=account_id, currency=currency, kind="capture",
            idempotency_key=idempotency_key, entries=entries, metadata=md,
        )
        conn.commit()
        return res
    finally:
        conn.close()


def release(db_path, *, account_id, amount_minor, idempotency_key, currency="GBP", metadata=None) -> dict[str, Any]:
    """Cancel a reservation: reserved → available."""
    _validate_money(amount_minor, currency)
    conn = store.connect(db_path)
    try:
        existing = _existing_txn(conn, idempotency_key)
        if existing:
            return {"txn_id": existing, "inserted": False}
        reserved_bal = _balance_of(conn, _acct(account_id, _RESERVED), currency)
        if reserved_bal < amount_minor:
            raise InsufficientFundsError(f"reserved {reserved_bal} < release {amount_minor} {currency}")
        res = _post(
            conn, account_id=account_id, currency=currency, kind="release", idempotency_key=idempotency_key,
            entries=[(_acct(account_id, _RESERVED), "debit", amount_minor), (_acct(account_id, _AVAILABLE), "credit", amount_minor)],
            metadata=metadata,
        )
        conn.commit()
        return res
    finally:
        conn.close()


def refund(db_path, *, account_id, amount_minor, idempotency_key, currency="GBP", metadata=None) -> dict[str, Any]:
    """Move available → external (funds out). Live refunds remain launch-gated."""
    _validate_money(amount_minor, currency)
    conn = store.connect(db_path)
    try:
        existing = _existing_txn(conn, idempotency_key)
        if existing:
            return {"txn_id": existing, "inserted": False}
        avail = _balance_of(conn, _acct(account_id, _AVAILABLE), currency)
        if avail < amount_minor:
            raise InsufficientFundsError(f"available {avail} < refund {amount_minor} {currency}")
        res = _post(
            conn, account_id=account_id, currency=currency, kind="refund", idempotency_key=idempotency_key,
            entries=[(_acct(account_id, _AVAILABLE), "debit", amount_minor), (_EXTERNAL, "credit", amount_minor)],
            metadata=metadata,
        )
        conn.commit()
        return res
    finally:
        conn.close()


def balance(db_path, account_id: str, currency: str = "GBP") -> dict[str, Any]:
    """Derived balance for an account: available + reserved (minor units)."""
    conn = store.connect(db_path)
    try:
        avail = _balance_of(conn, _acct(account_id, _AVAILABLE), currency)
        reserved = _balance_of(conn, _acct(account_id, _RESERVED), currency)
    finally:
        conn.close()
    return {
        "account_id": account_id, "currency": currency,
        "available_minor": avail, "reserved_minor": reserved, "total_minor": avail + reserved,
    }


def list_transactions(db_path, account_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT txn_id,kind,currency,occurred_at,metadata FROM ledger_transactions "
            "WHERE account_id=? ORDER BY occurred_at DESC, txn_id DESC LIMIT ?",
            (account_id, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def assert_ledger_balanced(db_path, currency: Optional[str] = None) -> bool:
    """Global invariant: across the whole ledger, total credits == total debits."""
    conn = store.connect(db_path)
    try:
        where, params = "", []
        if currency:
            where, params = " WHERE currency=?", [currency]
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE direction WHEN 'credit' THEN amount_minor ELSE 0 END),0) AS c, "
            "COALESCE(SUM(CASE direction WHEN 'debit' THEN amount_minor ELSE 0 END),0) AS d "
            f"FROM ledger_entries{where}",
            params,
        ).fetchone()
        if int(row["c"]) != int(row["d"]):
            raise LedgerError(f"LEDGER UNBALANCED: credits={row['c']} debits={row['d']}")
        return True
    finally:
        conn.close()
