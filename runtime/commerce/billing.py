"""
runtime/commerce/billing.py — billing provider abstraction (ADR-0006).

**Test-mode only.** Defines the ``BillingProvider`` port (the business contract, not
every Stripe feature) and a stdlib ``TestModeBillingProvider`` with **no network and no
``stripe`` dependency**. The internal ledger (``ledger.py``) stays authoritative; the
provider is the processor of record, reconciled against the ledger. Webhooks are
**HMAC-signed + idempotent**, mirroring Stripe's signed-webhook contract.

A real Stripe adapter (``stripe_testmode.py``, behind a ``pyproject`` extra using the
``stripe`` lib) is deferred; **live keys/charges remain launch-gated + feature-flagged
OFF** (handover 10 §8). Nothing here moves real money.
"""

from __future__ import annotations

import abc
import hashlib
import hmac
import json
from typing import Any, Optional

from runtime.commerce import ledger, store


class BillingError(RuntimeError):
    """Invalid billing operation."""


class WebhookVerificationError(BillingError):
    """Webhook signature did not verify."""


class BillingProvider(abc.ABC):
    """The billing port. ``TestModeBillingProvider`` is the dependency-free
    implementation; a Stripe adapter would implement the same contract."""

    provider_name: str = "abstract"

    @abc.abstractmethod
    def create_customer(self, *, account_id: str, email: Optional[str] = None) -> dict: ...

    @abc.abstractmethod
    def get_customer(self, account_id: str) -> Optional[dict]: ...

    @abc.abstractmethod
    def create_subscription(self, *, account_id: str, plan_id: str) -> dict: ...

    @abc.abstractmethod
    def cancel_subscription(self, subscription_id: str) -> dict: ...

    @abc.abstractmethod
    def create_topup_checkout(self, *, account_id: str, amount_minor: int, currency: str = "GBP") -> dict: ...

    @abc.abstractmethod
    def verify_and_handle_webhook(self, payload: bytes | str, signature: str) -> dict: ...

    @abc.abstractmethod
    def customer_portal_url(self, account_id: str) -> str: ...


def _sign(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


class TestModeBillingProvider(BillingProvider):
    """Dependency-free billing provider for local/test use. No network, no real money."""

    provider_name = "testmode"

    def __init__(self, db_path, *, webhook_secret: str) -> None:
        if not webhook_secret:
            raise BillingError("webhook_secret is required")
        self._db = db_path
        self._secret = webhook_secret
        store.init_store(db_path)

    # ── customers / subscriptions (persisted via store v2 tables) ────────────────
    def create_customer(self, *, account_id: str, email: Optional[str] = None) -> dict:
        conn = store.connect(self._db)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO billing_customers(account_id,provider,provider_customer_ref,created_at) VALUES (?,?,?,?)",
                (account_id, self.provider_name, store.new_id("cus_test"), store.now_iso()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM billing_customers WHERE account_id=?", (account_id,)).fetchone()
            return dict(row)
        finally:
            conn.close()

    def get_customer(self, account_id: str) -> Optional[dict]:
        conn = store.connect(self._db)
        try:
            row = conn.execute("SELECT * FROM billing_customers WHERE account_id=?", (account_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def create_subscription(self, *, account_id: str, plan_id: str) -> dict:
        if plan_id == "community":
            raise BillingError("community plan has no subscription")
        if plan_id == "enterprise":
            raise BillingError("enterprise is invoiced under contract, not self-serve subscription")
        sid = store.new_id("sub_test")
        conn = store.connect(self._db)
        try:
            conn.execute(
                "INSERT INTO subscriptions(subscription_id,account_id,plan_id,status,billing_customer_ref,current_period_end,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (sid, account_id, plan_id, "active", None, None, store.now_iso()),
            )
            conn.commit()
        finally:
            conn.close()
        return {"subscription_id": sid, "account_id": account_id, "plan_id": plan_id, "status": "active"}

    def cancel_subscription(self, subscription_id: str) -> dict:
        conn = store.connect(self._db)
        try:
            conn.execute("UPDATE subscriptions SET status='canceled' WHERE subscription_id=?", (subscription_id,))
            conn.commit()
        finally:
            conn.close()
        return {"subscription_id": subscription_id, "status": "canceled"}

    # ── top-up checkout → signed webhook → ledger credit ─────────────────────────
    def create_topup_checkout(self, *, account_id: str, amount_minor: int, currency: str = "GBP") -> dict:
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor <= 0:
            raise BillingError("amount_minor must be a positive int in minor units")
        if not (isinstance(currency, str) and len(currency) == 3):
            raise BillingError("currency must be a 3-letter code")
        checkout_id = store.new_id("cs_test")
        return {
            "checkout_id": checkout_id,
            "account_id": account_id,
            "amount_minor": amount_minor,
            "currency": currency,
            "url": f"https://testmode.local/checkout/{checkout_id}",
            "live": False,
        }

    def build_signed_webhook(self, *, event_id: str, event_type: str, data: dict) -> tuple[bytes, str]:
        """TEST HELPER: build a signed webhook (payload, signature) as a provider would send.

        A real provider does this on its servers; here it lets tests/operators exercise
        :meth:`verify_and_handle_webhook` without any network.
        """
        payload = json.dumps({"id": event_id, "type": event_type, "data": data}, sort_keys=True).encode("utf-8")
        return payload, _sign(self._secret, payload)

    def verify_and_handle_webhook(self, payload: bytes | str, signature: str) -> dict:
        """Verify the HMAC signature (constant-time), then handle idempotently.

        On ``topup.succeeded`` the customer's available balance is credited via the
        authoritative ledger. Duplicate events (same id) are a no-op.
        """
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        if not hmac.compare_digest(_sign(self._secret, raw), str(signature)):
            raise WebhookVerificationError("invalid webhook signature")
        event = json.loads(raw.decode("utf-8"))
        event_id, event_type, data = event.get("id"), event.get("type"), event.get("data") or {}
        if not event_id:
            raise BillingError("webhook missing event id")

        conn = store.connect(self._db)
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO billing_webhook_events"
                "(webhook_event_id,provider,event_type,received_at,processed,metadata) VALUES (?,?,?,?,0,?)",
                (event_id, self.provider_name, event_type, store.now_iso(), json.dumps(data)),
            )
            conn.commit()
            if cur.rowcount != 1:
                return {"event_id": event_id, "type": event_type, "handled": False, "duplicate": True}
        finally:
            conn.close()

        result: dict[str, Any] = {"event_id": event_id, "type": event_type, "handled": True, "duplicate": False}
        if event_type == "topup.succeeded":
            account_id = data.get("account_id")
            amount_minor = data.get("amount_minor")
            currency = data.get("currency", "GBP")
            if not account_id or not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
                raise BillingError("topup.succeeded requires account_id + int amount_minor")
            tx = ledger.top_up(
                self._db, account_id=account_id, amount_minor=amount_minor, currency=currency,
                idempotency_key=f"topup:{event_id}",
                metadata={"source": "billing.webhook", "provider": self.provider_name},
            )
            conn = store.connect(self._db)
            try:
                conn.execute(
                    "UPDATE billing_webhook_events SET processed=1, ledger_txn_id=? WHERE webhook_event_id=?",
                    (tx["txn_id"], event_id),
                )
                conn.commit()
            finally:
                conn.close()
            result.update(ledger_txn_id=tx["txn_id"], credited_minor=amount_minor, currency=currency)
        return result

    def customer_portal_url(self, account_id: str) -> str:
        return f"https://testmode.local/portal/{account_id}"
