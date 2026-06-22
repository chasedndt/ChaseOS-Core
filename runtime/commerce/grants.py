"""
runtime/commerce/grants.py — persisted entitlement grants (ADR-0004, handover 12).

Durable `entitlement_grants` rows that the resolver layers over the plan. A grant
records source (add_on/trial/promotional/marketplace/enterprise_contract/
support_override), allow/deny, optional limit, and optional expiry. Granting and
revoking emit canonical domain events. Pure stdlib over store.

Writes here are commercial mutations; in Studio they must additionally route through
StudioService approval (ADR-0004). This module is the persistence + resolver-feed.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from runtime.commerce import events, store


def grant(
    db_path,
    *,
    account_id: str,
    feature_id: str,
    source: str,
    allow: bool = True,
    limit_value: Optional[str] = None,
    expires_at: Optional[str] = None,
    actor: str = "operator",
    metadata: Optional[dict] = None,
) -> str:
    gid = store.new_id("grant")
    conn = store.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO entitlement_grants
               (grant_id,account_id,feature_id,source,allow,limit_value,expires_at,created_at,revoked_at,metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (gid, account_id, feature_id, source, 1 if allow else 0, limit_value,
             expires_at, store.now_iso(), None, json.dumps(metadata or {})),
        )
        conn.commit()
    finally:
        conn.close()
    events.emit(db_path, "entitlement.granted", actor=actor, account_id=account_id,
                metadata={"feature_id": feature_id, "source": source, "allow": allow, "grant_id": gid})
    return gid


def revoke(db_path, grant_id: str, *, actor: str = "operator") -> bool:
    conn = store.connect(db_path)
    try:
        row = conn.execute("SELECT account_id, feature_id FROM entitlement_grants WHERE grant_id=?",
                           (grant_id,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE entitlement_grants SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL",
                     (store.now_iso(), grant_id))
        conn.commit()
        acct, feat = row["account_id"], row["feature_id"]
    finally:
        conn.close()
    events.emit(db_path, "entitlement.expired", actor=actor, account_id=acct,
                metadata={"feature_id": feat, "grant_id": grant_id, "reason": "revoked"})
    return True


def list_grants(
    db_path,
    account_id: str,
    *,
    active_only: bool = True,
    now_iso: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return grants for an account in the shape the resolver consumes.

    Shape: {feature_id, source, allow(bool), limit, expires_at}.
    active_only filters out revoked + expired grants.
    """
    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM entitlement_grants WHERE account_id=? ORDER BY created_at",
            (account_id,),
        ).fetchall()
    finally:
        conn.close()
    clock = now_iso or store.now_iso()
    out = []
    for r in rows:
        if active_only:
            if r["revoked_at"]:
                continue
            if r["expires_at"] and str(r["expires_at"]) <= str(clock):
                continue
        out.append({
            "feature_id": r["feature_id"],
            "source": r["source"],
            "allow": bool(r["allow"]),
            "limit": r["limit_value"],
            "expires_at": r["expires_at"],
        })
    return out
