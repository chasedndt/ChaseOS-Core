"""
delivery_adapters.py — SBP Delivery Adapter Layer (Phase 9)

Provides the generic DeliveryAdapter protocol and concrete/stub implementations.
Delivery adapters send SBP output to end consumers after vault writeback.

Concrete implementations:
  VaultLocalDeliveryAdapter — vault writeback confirmed (Pass 1A)
  DiscordDeliveryAdapter    — Discord webhook via per-pipeline or DISCORD_WEBHOOK_URL env var (Pass 1D)
  WhopDeliveryAdapter       — Whop community forum post via WHOP_API_KEY env var (Pass 1E)

Stub implementations (wire in respective instance pipeline passes):
  EmailDeliveryAdapterStub, SlackDeliveryAdapterStub

No credentials are handled here. Credential handling follows Credential-Boundaries-SOP.md.
External delivery endpoints are advisory-declared in the manifest; actual credential
access happens through the declared adapter layer at instance pipeline time.
Concrete external sends are checked through ChaseOS Gate runtime operations before
network I/O.

Public API:
    get_delivery_adapter(adapter_type) -> DeliveryAdapter
    DeliveryAdapter (ABC)
    VaultLocalDeliveryAdapter
    DiscordDeliveryAdapter
    WhopDeliveryAdapter
    DiscordDeliveryAdapterStub  (kept for tests)
    EmailDeliveryAdapterStub
    SlackDeliveryAdapterStub
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

# Reach the Gate through the Core port (ADR-0014), not the proprietary Control Kernel
# directly — keeps sbp (and therefore the AOR engine that imports it module-level)
# Core-extractable. In the monorepo this delegates to runtime.chaseos_gate; in Core it
# falls back to the deny-by-default gate. (Decoupled 2026-06-22, ADR-0015 HIGH cluster.)
from runtime.gate_interface import check_runtime_operation
from runtime.sbp.delivery_health import classify_delivery_failure, record_delivery_health_event


DISCORD_DELIVERY_OPERATION = "sbp.delivery.discord.webhook_send"
DISCORD_DELIVERY_API = "delivery.discord_webhook"
WHOP_DELIVERY_OPERATION = "sbp.delivery.whop.post"
WHOP_DELIVERY_API = "delivery.whop_api"


def _context_vault_root(context: dict) -> str | None:
    vault_root = context.get("vault_root")
    if not vault_root:
        return None
    return str(vault_root)


def _record_delivery_health(
    context: dict,
    *,
    event_type: str,
    adapter_id: str,
    surface: str,
    provider: str,
    delivery_target: str | None = None,
    failure_detail: str | None = None,
    data: dict | None = None,
) -> None:
    """Best-effort delivery telemetry; delivery return values remain authoritative."""
    vault_root = _context_vault_root(context)
    if not vault_root:
        return
    record_delivery_health_event(
        vault_root,
        event_type=event_type,
        adapter_id=adapter_id,
        provider=provider,
        surface=surface,
        pipeline_id=str(context.get("pipeline_id") or "unknown"),
        delivery_target=delivery_target,
        channel_hint=context.get("channel_hint") or None,
        run_date=context.get("date") or None,
        failure_reason=classify_delivery_failure(failure_detail) if failure_detail else None,
        error=failure_detail,
        data=data or {},
    )


def _check_delivery_policy(operation: str, external_api: str) -> tuple[bool, str]:
    """Gate-check an SBP external delivery write before network I/O."""
    return check_runtime_operation(
        operation,
        external_api=external_api,
        external_side_effect=True,
    )


class DeliveryAdapter(ABC):
    """Abstract base for SBP delivery adapters."""
    adapter_type: str = ""

    @abstractmethod
    def deliver(self, content: str, context: dict) -> dict:
        """Deliver content to the adapter's endpoint.

        Returns dict with:
          success: bool
          details: str — human-readable delivery result
          stub: bool — True if this is a stub implementation
        """
        ...


class VaultLocalDeliveryAdapter(DeliveryAdapter):
    """Vault-local delivery — content is already written in the AOR writeback stage.

    This is the only concrete delivery adapter in Pass 1A.
    It confirms that vault writeback occurred; no further external action is taken.
    For pipelines that only need vault persistence, this is the correct default.
    """
    adapter_type = "vault-local"

    def deliver(self, content: str, context: dict) -> dict:
        return {
            "success": True,
            "details": "vault-local: content written to vault in AOR writeback stage",
            "stub": False,
        }


class DiscordDeliveryAdapter(DeliveryAdapter):
    """Real Discord webhook delivery (Pass 1D).

    Webhook URL resolution order (per Credential-Boundaries-SOP.md — no credentials in code):
      1. context["webhook_env_var"] — per-pipeline env var name declared in the manifest
      2. DISCORD_WEBHOOK_URL — global fallback env var

    This lets each SBP pipeline point to its own Discord server by declaring
    `webhook_env_var: MY_PIPELINE_DISCORD_WEBHOOK_URL` in its manifest delivery adapter config.
    Operators set the env var; the name is safe to put in the manifest.

    Long content is chunked into multiple embeds (max 4096 chars each, max 10 embeds per POST)
    rather than truncated. Each chunk after the first is sent as a separate webhook POST.

    Uses stdlib urllib.request only (no external HTTP deps).
    """
    adapter_type = "discord"
    DEFAULT_WEBHOOK_ENV_VAR = "DISCORD_WEBHOOK_URL"
    EMBED_CHAR_LIMIT = 4000   # 4096 limit; leave 96 chars of headroom
    MAX_EMBEDS_PER_POST = 10
    EMBED_COLOR = 3447003     # Discord blue — neutral for any server

    def _resolve_webhook_url(self, context: dict) -> tuple[str, str]:
        """Return (url, env_var_name). URL is empty string if not configured."""
        per_pipeline_env_var = context.get("webhook_env_var") or ""
        if per_pipeline_env_var:
            url = os.environ.get(per_pipeline_env_var, "").strip()
            return url, per_pipeline_env_var
        url = os.environ.get(self.DEFAULT_WEBHOOK_ENV_VAR, "").strip()
        return url, self.DEFAULT_WEBHOOK_ENV_VAR

    def _chunk_content(self, content: str) -> list[str]:
        """Split content into <=EMBED_CHAR_LIMIT chunks, breaking on newlines where possible."""
        if len(content) <= self.EMBED_CHAR_LIMIT:
            return [content]
        chunks = []
        remaining = content
        while remaining:
            if len(remaining) <= self.EMBED_CHAR_LIMIT:
                chunks.append(remaining)
                break
            # Find last newline within the char limit
            split_at = remaining.rfind("\n", 0, self.EMBED_CHAR_LIMIT)
            if split_at == -1:
                split_at = self.EMBED_CHAR_LIMIT
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        return chunks

    # M-6 security fix: validate that the webhook URL is an actual Discord webhook
    _DISCORD_WEBHOOK_PREFIX = "https://discord.com/api/webhooks/"

    def _validate_webhook_url(self, url: str) -> str | None:
        """Return error string if URL is not a valid Discord webhook, else None."""
        if not url:
            return "Webhook URL is empty — set the env var referenced in webhook_env_var"
        if not url.startswith(self._DISCORD_WEBHOOK_PREFIX):
            return (
                f"Discord webhook URL must start with {self._DISCORD_WEBHOOK_PREFIX!r}; "
                f"got {url[:60]!r}"
            )
        return None

    def _post_embeds(self, webhook_url: str, embeds: list[dict]) -> tuple[bool, str]:
        """POST a list of embeds to the webhook. Returns (success, details)."""
        url_error = self._validate_webhook_url(webhook_url)
        if url_error:
            return False, f"discord: {url_error}"
        allowed, reason = _check_delivery_policy(DISCORD_DELIVERY_OPERATION, DISCORD_DELIVERY_API)
        if not allowed:
            return False, f"discord: Gate blocked delivery: {reason}"
        payload = json.dumps({"embeds": embeds}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status_code = resp.status
        except urllib.error.HTTPError as exc:
            return False, f"discord: HTTP {exc.code} from webhook endpoint"
        except (urllib.error.URLError, OSError) as exc:
            return False, f"discord: network error — {exc}"
        if status_code in (200, 204):
            return True, f"discord: delivered (HTTP {status_code})"
        return False, f"discord: unexpected HTTP {status_code} from webhook"

    def _write_draft(self, content: str, context: dict) -> dict:
        """Write content to vault draft dir; do NOT post to Discord."""
        vault_root = _context_vault_root(context)
        if not vault_root:
            return {
                "success": False,
                "details": "discord: draft_review_required but vault_root missing; delivery skipped",
                "draft_written": False,
                "stub": False,
            }
        pipeline_id = context.get("pipeline_id", "sbp")
        run_date = context.get("date", "unknown-date")
        draft_dir = Path(vault_root) / "07_LOGS" / "SBP-Runs" / "_drafts"
        draft_dir.mkdir(parents=True, exist_ok=True)
        draft_filename = f"{run_date}-{pipeline_id}-discord-draft.md"
        draft_path = draft_dir / draft_filename
        draft_path.write_text(content, encoding="utf-8")
        detail = (
            f"discord: draft_review_required=true — content written to "
            f"07_LOGS/SBP-Runs/_drafts/{draft_filename}; "
            "promote with: chaseos sbp promote-draft to send to Discord"
        )
        _record_delivery_health(
            context,
            event_type="delivery.draft_written",
            adapter_id=self.adapter_type,
            provider="discord",
            surface=DISCORD_DELIVERY_API,
            delivery_target=f"draft:{draft_path}",
            data={"draft_path": str(draft_path), "content_length": len(content)},
        )
        return {
            "success": False,
            "details": detail,
            "draft_written": True,
            "draft_path": str(draft_path),
            "stub": False,
        }

    def deliver(self, content: str, context: dict) -> dict:
        if context.get("draft_review_required"):
            return self._write_draft(content, context)

        webhook_url, env_var_used = self._resolve_webhook_url(context)
        if not webhook_url:
            detail = (
                f"discord: {env_var_used} env var not set; "
                "set it to the Discord webhook URL to enable delivery; "
                "content written to vault only"
            )
            _record_delivery_health(
                context,
                event_type="delivery.attempt_failed",
                adapter_id=self.adapter_type,
                provider="discord",
                surface=DISCORD_DELIVERY_API,
                delivery_target=f"env:{env_var_used}",
                failure_detail=detail,
                data={
                    "env_var": env_var_used,
                    "content_length": len(content),
                },
            )
            return {
                "success": False,
                "details": detail,
                "stub": False,
            }

        pipeline_id = context.get("pipeline_id", "sbp")
        run_date = context.get("date", "")
        channel_hint = context.get("channel_hint", "")
        embed_title = f"{pipeline_id} — {run_date}" if run_date else f"{pipeline_id} digest"
        if channel_hint:
            embed_title = f"{embed_title} ({channel_hint})"

        chunks = self._chunk_content(content)
        total_chunks = len(chunks)
        posts_sent = 0
        errors = []

        for i, chunk in enumerate(chunks):
            # Build embed for this chunk
            title = embed_title if i == 0 else f"{embed_title} (cont. {i + 1}/{total_chunks})"
            embed = {
                "title": title,
                "description": chunk,
                "color": self.EMBED_COLOR,
            }
            ok, detail = self._post_embeds(webhook_url, [embed])
            if ok:
                posts_sent += 1
            else:
                errors.append(detail)
                break  # stop on first network failure

        if errors:
            detail = f"discord: {errors[0]} (sent {posts_sent}/{total_chunks} chunks)"
            _record_delivery_health(
                context,
                event_type="delivery.attempt_failed",
                adapter_id=self.adapter_type,
                provider="discord",
                surface=DISCORD_DELIVERY_API,
                delivery_target=f"env:{env_var_used}",
                failure_detail=detail,
                data={
                    "env_var": env_var_used,
                    "chunks_sent": posts_sent,
                    "chunks_total": total_chunks,
                    "content_length": len(content),
                },
            )
            return {
                "success": False,
                "details": detail,
                "stub": False,
                "chunks_sent": posts_sent,
                "chunks_total": total_chunks,
            }
        _record_delivery_health(
            context,
            event_type="delivery.attempt_succeeded",
            adapter_id=self.adapter_type,
            provider="discord",
            surface=DISCORD_DELIVERY_API,
            delivery_target=f"env:{env_var_used}",
            data={
                "env_var": env_var_used,
                "chunks_sent": posts_sent,
                "chunks_total": total_chunks,
                "content_length": len(content),
            },
        )
        return {
            "success": True,
            "details": (
                f"discord: delivered {total_chunks} embed(s) to webhook "
                f"(env_var: {env_var_used})"
            ),
            "stub": False,
            "chunks_sent": posts_sent,
            "chunks_total": total_chunks,
        }


class DiscordDeliveryAdapterStub(DeliveryAdapter):
    """Retained stub — kept for tests and backwards compatibility.

    The active registry entry for 'discord' is now DiscordDeliveryAdapter (Pass 1B).
    This stub is NOT registered in _ADAPTER_REGISTRY.
    """
    adapter_type = "discord-stub"

    def deliver(self, content: str, context: dict) -> dict:
        return {
            "success": False,
            "details": "discord-stub: not active; use DiscordDeliveryAdapter",
            "stub": True,
        }


class EmailDeliveryAdapterStub(DeliveryAdapter):
    """Stub — Email delivery. Not implemented in Pass 1A."""
    adapter_type = "email"

    def deliver(self, content: str, context: dict) -> dict:
        return {
            "success": False,
            "details": "email: delivery stub — not implemented in SBP Pass 1A",
            "stub": True,
        }


class WhopDeliveryAdapter(DeliveryAdapter):
    """Real Whop community forum post delivery (Pass 1E).

    Whop API v5 — creates a post in a forum experience (community channel).
    Endpoint: POST https://api.whop.com/v5/posts

    API key resolution order (per Credential-Boundaries-SOP.md — no credentials in code):
      1. context["webhook_env_var"] — per-pipeline env var name declared in the manifest
      2. WHOP_API_KEY — global fallback env var

    Channel:
      context["channel_id"] — the Whop forum_experience_id (e.g. "exp_XXXXXXXXXX").
      Declared in the manifest delivery adapter block as `channel_id: exp_XXXXXXXXXX`.
      Required: delivery fails fast if not set rather than silently posting nowhere.

    Long content is chunked into multiple posts (max POST_CHAR_LIMIT chars each).
    Each chunk after the first is posted as a separate forum entry with a continuation title.

    Uses stdlib urllib.request only (no external HTTP deps).
    """
    adapter_type = "whop"
    API_BASE = "https://api.whop.com"
    DEFAULT_API_KEY_ENV_VAR = "WHOP_API_KEY"
    POST_CHAR_LIMIT = 8000  # Conservative ceiling; Whop forum posts support markdown

    def _resolve_api_key(self, context: dict) -> tuple[str, str]:
        """Return (api_key, env_var_name). Key is empty string if not configured."""
        per_pipeline_env_var = context.get("webhook_env_var") or ""
        if per_pipeline_env_var:
            key = os.environ.get(per_pipeline_env_var, "").strip()
            return key, per_pipeline_env_var
        key = os.environ.get(self.DEFAULT_API_KEY_ENV_VAR, "").strip()
        return key, self.DEFAULT_API_KEY_ENV_VAR

    def _chunk_content(self, content: str) -> list[str]:
        """Split content into <=POST_CHAR_LIMIT chunks, breaking on newlines where possible."""
        if len(content) <= self.POST_CHAR_LIMIT:
            return [content]
        chunks = []
        remaining = content
        while remaining:
            if len(remaining) <= self.POST_CHAR_LIMIT:
                chunks.append(remaining)
                break
            split_at = remaining.rfind("\n", 0, self.POST_CHAR_LIMIT)
            if split_at == -1:
                split_at = self.POST_CHAR_LIMIT
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        return chunks

    def _post_whop(
        self, api_key: str, forum_experience_id: str, title: str, content: str
    ) -> tuple[bool, str]:
        """POST one forum entry to Whop API. Returns (success, details)."""
        allowed, reason = _check_delivery_policy(WHOP_DELIVERY_OPERATION, WHOP_DELIVERY_API)
        if not allowed:
            return False, f"whop: Gate blocked delivery: {reason}"
        payload = json.dumps({
            "forum_experience_id": forum_experience_id,
            "title": title,
            "content": content,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.API_BASE}/v5/posts",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status_code = resp.status
        except urllib.error.HTTPError as exc:
            return False, f"whop: HTTP {exc.code} from API endpoint"
        except (urllib.error.URLError, OSError) as exc:
            return False, f"whop: network error — {exc}"
        if status_code in (200, 201):
            return True, f"whop: delivered (HTTP {status_code})"
        return False, f"whop: unexpected HTTP {status_code}"

    def deliver(self, content: str, context: dict) -> dict:
        api_key, env_var_used = self._resolve_api_key(context)
        if not api_key:
            detail = (
                f"whop: {env_var_used} env var not set; "
                "set it to your Whop API key to enable delivery; "
                "content written to vault only"
            )
            _record_delivery_health(
                context,
                event_type="delivery.attempt_failed",
                adapter_id=self.adapter_type,
                provider="whop",
                surface=WHOP_DELIVERY_API,
                delivery_target=f"env:{env_var_used}",
                failure_detail=detail,
                data={
                    "env_var": env_var_used,
                    "content_length": len(content),
                },
            )
            return {
                "success": False,
                "details": detail,
                "stub": False,
            }

        forum_experience_id = (context.get("channel_id") or "").strip()
        if not forum_experience_id:
            detail = (
                "whop: channel_id not set; "
                "add `channel_id: exp_XXXXXXXXXX` to the delivery adapter block in the manifest"
            )
            _record_delivery_health(
                context,
                event_type="delivery.attempt_failed",
                adapter_id=self.adapter_type,
                provider="whop",
                surface=WHOP_DELIVERY_API,
                delivery_target=f"env:{env_var_used}",
                failure_detail=detail,
                data={
                    "env_var": env_var_used,
                    "channel_id_declared": False,
                    "content_length": len(content),
                },
            )
            return {
                "success": False,
                "details": detail,
                "stub": False,
            }

        pipeline_id = context.get("pipeline_id", "sbp")
        run_date = context.get("date", "")
        channel_hint = context.get("channel_hint", "")
        base_title = f"{pipeline_id} — {run_date}" if run_date else f"{pipeline_id} digest"
        if channel_hint:
            base_title = f"{base_title} ({channel_hint})"

        chunks = self._chunk_content(content)
        total_chunks = len(chunks)
        posts_sent = 0
        errors = []

        for i, chunk in enumerate(chunks):
            title = base_title if i == 0 else f"{base_title} (cont. {i + 1}/{total_chunks})"
            ok, detail = self._post_whop(api_key, forum_experience_id, title, chunk)
            if ok:
                posts_sent += 1
            else:
                errors.append(detail)
                break  # stop on first network failure

        if errors:
            detail = f"{errors[0]} (sent {posts_sent}/{total_chunks} posts)"
            _record_delivery_health(
                context,
                event_type="delivery.attempt_failed",
                adapter_id=self.adapter_type,
                provider="whop",
                surface=WHOP_DELIVERY_API,
                delivery_target=f"whop-forum:{forum_experience_id}",
                failure_detail=detail,
                data={
                    "env_var": env_var_used,
                    "posts_sent": posts_sent,
                    "posts_total": total_chunks,
                    "content_length": len(content),
                },
            )
            return {
                "success": False,
                "details": detail,
                "stub": False,
                "posts_sent": posts_sent,
                "posts_total": total_chunks,
            }
        _record_delivery_health(
            context,
            event_type="delivery.attempt_succeeded",
            adapter_id=self.adapter_type,
            provider="whop",
            surface=WHOP_DELIVERY_API,
            delivery_target=f"whop-forum:{forum_experience_id}",
            data={
                "env_var": env_var_used,
                "posts_sent": posts_sent,
                "posts_total": total_chunks,
                "content_length": len(content),
            },
        )
        return {
            "success": True,
            "details": (
                f"whop: delivered {total_chunks} post(s) to forum {forum_experience_id} "
                f"(env_var: {env_var_used})"
            ),
            "stub": False,
            "posts_sent": posts_sent,
            "posts_total": total_chunks,
        }


class SlackDeliveryAdapterStub(DeliveryAdapter):
    """Stub — Slack webhook delivery. Not implemented in Pass 1A."""
    adapter_type = "slack"

    def deliver(self, content: str, context: dict) -> dict:
        return {
            "success": False,
            "details": "slack: delivery stub — not implemented in SBP Pass 1A",
            "stub": True,
        }


_ADAPTER_REGISTRY: dict[str, type[DeliveryAdapter]] = {
    "vault-local": VaultLocalDeliveryAdapter,
    "discord": DiscordDeliveryAdapter,    # real implementation (Pass 1D)
    "whop": WhopDeliveryAdapter,          # real implementation (Pass 1E)
    "email": EmailDeliveryAdapterStub,
    "slack": SlackDeliveryAdapterStub,
}


def get_delivery_adapter(adapter_type: str) -> DeliveryAdapter:
    """Factory: return DeliveryAdapter instance for the given adapter type."""
    cls = _ADAPTER_REGISTRY.get(adapter_type)
    if cls is None:
        raise ValueError(
            f"unknown delivery adapter type '{adapter_type}'; "
            f"valid types: {sorted(_ADAPTER_REGISTRY)}"
        )
    return cls()
