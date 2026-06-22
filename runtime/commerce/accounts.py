"""
runtime/commerce/accounts.py — account / RBAC / subscription orchestration (ADR-0012, handover 12).

Higher-level account lifecycle over the store: provisioning (emitting account.created),
subscription create/change (emitting subscription.started/changed), RBAC role
catalogue seeding, api-key + billing-customer refs (test-mode stubs — NO Stripe, NO
secrets stored). Keeps store.py low-level (no event dependency) and avoids an
import cycle (accounts → store, events).
"""

from __future__ import annotations

from typing import Any, Optional

from runtime.commerce import events, store

# Admin RBAC roles (handover 04 §12) + basic account roles. scope = admin | account.
DEFAULT_ROLES = [
    ("support_read", "Support (read-only)", "admin"),
    ("billing_ops", "Billing operations", "admin"),
    ("marketplace_ops", "Marketplace operations", "admin"),
    ("risk_ops", "Risk operations", "admin"),
    ("engineering_admin", "Engineering admin", "admin"),
    ("security_admin", "Security admin", "admin"),
    ("super_admin", "Super admin", "admin"),
    ("owner", "Account owner", "account"),
    ("admin", "Account admin", "account"),
    ("member", "Account member", "account"),
]


def seed_roles(db_path) -> int:
    """Idempotently seed the role catalogue. Returns the number of roles present."""
    conn = store.connect(db_path)
    try:
        for role_id, name, scope in DEFAULT_ROLES:
            conn.execute(
                "INSERT OR IGNORE INTO roles(role_id,name,scope,description) VALUES (?,?,?,?)",
                (role_id, name, scope, name),
            )
        conn.commit()
        return conn.execute("SELECT COUNT(*) AS n FROM roles").fetchone()["n"]
    finally:
        conn.close()


def list_roles(db_path, *, scope: Optional[str] = None) -> list[dict]:
    conn = store.connect(db_path)
    try:
        if scope:
            rows = conn.execute("SELECT * FROM roles WHERE scope=? ORDER BY role_id", (scope,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM roles ORDER BY scope, role_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def provision_account(
    db_path,
    *,
    name: str,
    plan_id: str = "community",
    actor: str = "operator",
    owner_user_id: Optional[str] = None,
) -> str:
    """Create an account, optionally its owner membership, and emit account.created."""
    aid = store.create_account(db_path, name=name, plan_id=plan_id)
    if owner_user_id:
        store.add_membership(db_path, account_id=aid, user_id=owner_user_id, role="owner")
    events.emit(db_path, "account.created", actor=actor, account_id=aid,
                metadata={"name": name, "plan_id": plan_id})
    return aid


def create_subscription(
    db_path,
    *,
    account_id: str,
    plan_id: str,
    status: str = "active",
    billing_customer_ref: Optional[str] = None,
    current_period_end: Optional[str] = None,
    actor: str = "operator",
) -> str:
    """Record a subscription (test-mode; no Stripe) and emit subscription.started."""
    sid = store.new_id("sub")
    conn = store.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO subscriptions
               (subscription_id,account_id,plan_id,status,billing_customer_ref,current_period_end,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (sid, account_id, plan_id, status, billing_customer_ref, current_period_end, store.now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    events.emit(db_path, "subscription.started", actor=actor, account_id=account_id,
                metadata={"subscription_id": sid, "plan_id": plan_id, "status": status})
    return sid


def change_plan(db_path, *, account_id: str, new_plan_id: str, actor: str = "operator") -> None:
    """Change an account's plan and emit subscription.changed."""
    old = store.get_account(db_path, account_id)
    store.set_account_plan(db_path, account_id, new_plan_id)
    events.emit(db_path, "subscription.changed", actor=actor, account_id=account_id,
                metadata={"from": (old or {}).get("plan_id"), "to": new_plan_id})


def link_billing_customer(db_path, *, account_id: str, provider: str, provider_customer_ref: Optional[str] = None) -> None:
    """Store a billing-customer REFERENCE only (test-mode; no Stripe, no secret)."""
    conn = store.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO billing_customers(account_id,provider,provider_customer_ref,created_at) VALUES (?,?,?,?)",
            (account_id, provider, provider_customer_ref, store.now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def create_api_key(db_path, *, account_id: str, name: str, prefix: str = "ck_test") -> str:
    """Record an api-key metadata row (prefix only — NO secret material stored)."""
    kid = store.new_id("apikey")
    conn = store.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO api_keys(api_key_id,account_id,name,prefix,created_at,revoked_at) VALUES (?,?,?,?,?,?)",
            (kid, account_id, name, prefix, store.now_iso(), None),
        )
        conn.commit()
    finally:
        conn.close()
    return kid
