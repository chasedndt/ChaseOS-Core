"""
runtime/commerce/entitlements.py — entitlement resolver (ADR-0004).

Answers: "is feature X available to this plan/account, and if not, why + upgrade to
what?" Returns the handover contract object (12 §3). Default-DENY. Pure function over
the catalogue + optional explicit grants — no plan == "pro" checks anywhere else.

An entitlement is distinct from a feature flag (runtime/commerce/flags.py). The app
gate is: flag_enabled(feature) AND check(plan, feature).allowed.

Grant precedence (highest first): support_override > enterprise_contract >
add_on / marketplace / trial / promotional > plan. A grant may force allow or deny
and may carry a limit; expired grants are ignored.
"""

from __future__ import annotations

from typing import Any, Optional

from runtime.commerce import catalog as _catalog

# Higher number = higher precedence.
_SOURCE_PRECEDENCE = {
    "plan": 10,
    "trial": 20,
    "promotional": 30,
    "marketplace": 40,
    "add_on": 50,
    "enterprise_contract": 60,
    "support_override": 70,
}


def _grant_active(grant: dict, now_iso: Optional[str]) -> bool:
    exp = grant.get("expires_at")
    if not exp:
        return True
    if now_iso is None:
        return True  # caller did not supply a clock; treat undated comparison as active
    return str(now_iso) < str(exp)


def check(
    plan_id: str,
    feature_id: str,
    *,
    grants: Optional[list[dict]] = None,
    now_iso: Optional[str] = None,
    catalog: dict | None = None,
) -> dict[str, Any]:
    """
    Resolve an entitlement.

    grants: optional list of {feature_id, source, allow(bool), limit?, expires_at?}.
    Returns: {feature_id, allowed, source, plan_id, limit, usage, reason_code, upgrade_target}.
    """
    cat = catalog or _catalog.load_catalog() if catalog is None else catalog
    cat = cat or _catalog._default_catalog()

    if _catalog.get_feature(feature_id, cat) is None:
        return {
            "feature_id": feature_id,
            "allowed": False,
            "source": None,
            "plan_id": plan_id,
            "limit": None,
            "usage": None,
            "reason_code": "unknown_feature",
            "upgrade_target": None,
        }

    # 1) Explicit grants take precedence over the plan, highest-precedence first.
    applicable = [
        g for g in (grants or [])
        if g.get("feature_id") == feature_id and _grant_active(g, now_iso)
    ]
    applicable.sort(key=lambda g: _SOURCE_PRECEDENCE.get(g.get("source", "plan"), 0), reverse=True)
    if applicable:
        g = applicable[0]
        allowed = bool(g.get("allow", True))
        return {
            "feature_id": feature_id,
            "allowed": allowed,
            "source": g.get("source", "add_on"),
            "plan_id": plan_id,
            "limit": g.get("limit"),
            "usage": None,
            "reason_code": "granted" if allowed else "grant_denied",
            "upgrade_target": None,
        }

    # 2) Plan entitlement.
    if feature_id in _catalog.features_for_plan(plan_id, cat):
        return {
            "feature_id": feature_id,
            "allowed": True,
            "source": "plan",
            "plan_id": plan_id,
            "limit": None,
            "usage": None,
            "reason_code": "included_in_plan",
            "upgrade_target": None,
        }

    # 3) Denied — compute the cheapest plan that would unlock it.
    candidates = _catalog.plans_with_feature(feature_id, cat)
    upgrade_target = None
    if candidates:
        this = _catalog.get_plan(plan_id, cat) or {}
        this_rank = this.get("rank", -1)
        higher = [pid for pid in candidates
                  if (_catalog.get_plan(pid, cat) or {}).get("rank", 0) > this_rank]
        upgrade_target = (higher or candidates)[0]

    return {
        "feature_id": feature_id,
        "allowed": False,
        "source": "plan",
        "plan_id": plan_id,
        "limit": None,
        "usage": None,
        "reason_code": "plan_upgrade_required" if upgrade_target else "feature_unavailable",
        "upgrade_target": upgrade_target,
    }


def check_account(
    db_path,
    account_id: str,
    feature_id: str,
    *,
    now_iso: Optional[str] = None,
    catalog: dict | None = None,
) -> dict[str, Any]:
    """Resolve an entitlement for a stored account: loads its plan + active grants.

    This is the account-scoped resolver (handover 12 §3). Returns the same contract
    object as check(), with account_id attached. Denies if the account is unknown.
    """
    from runtime.commerce import grants as _grants
    from runtime.commerce import store as _store

    acct = _store.get_account(db_path, account_id)
    if acct is None:
        return {
            "feature_id": feature_id,
            "allowed": False,
            "source": None,
            "plan_id": None,
            "account_id": account_id,
            "limit": None,
            "usage": None,
            "reason_code": "account_not_found",
            "upgrade_target": None,
        }
    account_grants = _grants.list_grants(db_path, account_id, active_only=True, now_iso=now_iso)
    result = check(acct["plan_id"], feature_id, grants=account_grants, now_iso=now_iso, catalog=catalog)
    result["account_id"] = account_id
    return result
