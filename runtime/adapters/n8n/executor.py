"""
executor.py — n8n Live Workflow Executor

Triggers n8n workflows via HTTP when the deployment is enabled and policy passes.
This is the live execution layer — companion to workflow_policy.py (dry-run) and
mcp_connection.py (readiness checks).

Two trigger paths:
  mcp_tool — POST to n8n MCP server (tools/call JSON-RPC)
  webhook   — POST to n8n webhook endpoint ({base_url}/webhook/{workflow_id})

Gate contract:
  - deployment.enabled: true in n8n_config.yaml (operator opt-in)
  - N8N_BASE_URL env var set
  - N8N_MCP_ACCESS_TOKEN env var set (for mcp_tool triggers)
  - workflow current_status must be "production_enabled" or "dry_run_candidate" with approved=True
  - blocked status workflows are always rejected, regardless of approval
  - caller must be in workflow.allowed_callers
  - production=True requires approved=True if approval_required=True

Fail-open for ChaseOS pipelines: a failed n8n call returns ok=False with details
rather than raising an exception, so SBP/AOR pipelines can log and continue.

Public API:
    execute_n8n_workflow(workflow_id, caller, payload, production, approved,
                         config_path, registry_path, environ) -> dict
    N8NExecutionError
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote as _url_quote
from urllib.request import Request, urlopen

from runtime.adapters.n8n.mcp_connection import N8NMCPConnectionError, resolve_connection
from runtime.adapters.n8n.workflow_policy import (
    N8NWorkflowPolicyError,
    load_workflow_registry,
    validate_registry,
    validate_workflow_policy,
)
from runtime.security.redaction import redact_obj as _redact_obj
from runtime.security.redaction import redact_text as _redact_text

DEFAULT_WEBHOOK_PATH = "webhook"
DEFAULT_TIMEOUT_S = 10.0


class N8NExecutionError(RuntimeError):
    """Raised when live n8n execution is blocked at the policy level.

    Network/HTTP errors are returned as ok=False dicts (fail-open).
    Policy violations that should never proceed (blocked workflow, forbidden action)
    raise this exception instead.
    """


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout_s: float,
) -> dict[str, Any]:
    """POST JSON to url. Returns dict with ok, http_status, response, error."""
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout_s) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "http_status": exc.code,
            "response": None,
            "response_excerpt": text[:500],
            "error": "http_error",
        }
    except URLError as exc:
        return {
            "ok": False,
            "http_status": None,
            "response": None,
            "error": "url_error",
            "reason": str(exc.reason),
        }
    except OSError as exc:
        return {
            "ok": False,
            "http_status": None,
            "response": None,
            "error": "os_error",
            "reason": str(exc),
        }

    parsed: dict[str, Any] | None = None
    try:
        candidate = json.loads(text)
        if isinstance(candidate, dict):
            parsed = candidate
    except json.JSONDecodeError:
        pass

    ok = 200 <= status < 300
    return {
        "ok": ok,
        "http_status": status,
        "response": parsed,
        "response_excerpt": None if parsed is not None else text[:500],
    }


def _trigger_via_webhook(
    *,
    base_url: str,
    workflow_id: str,
    payload: dict[str, Any],
    token: str | None,
    timeout_s: float,
) -> dict[str, Any]:
    # M-5 security fix: URL-encode workflow_id to prevent path traversal in webhook URL
    url = f"{base_url.rstrip('/')}/{DEFAULT_WEBHOOK_PATH}/{_url_quote(workflow_id, safe='')}"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _post_json(url, payload, headers=headers, timeout_s=timeout_s)


def _trigger_via_mcp_tool(
    *,
    mcp_server_url: str,
    workflow_id: str,
    payload: dict[str, Any],
    token: str,
    timeout_s: float,
) -> dict[str, Any]:
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {
            "name": workflow_id,
            "arguments": payload,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    return _post_json(mcp_server_url, rpc_payload, headers=headers, timeout_s=timeout_s)


def execute_n8n_workflow(
    workflow_id: str,
    *,
    caller: str,
    config_path: Path,
    registry_path: Path,
    payload: dict[str, Any] | None = None,
    production: bool = False,
    approved: bool = False,
    environ: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Execute an n8n workflow live.

    Returns a dict with:
      ok: bool
      live_http_call: bool (always True on success path)
      workflow_id: str
      caller: str
      trigger_type: str
      production: bool
      approved: bool
      executed_at_utc: str
      http_status: int | None
      response: dict | None
      error: str | None  (present on failure)
      policy: dict  (reads/writes/secrets from the workflow declaration)

    Raises N8NExecutionError if the workflow is policy-blocked before any HTTP call.
    Network/HTTP failures return ok=False rather than raising (fail-open for pipelines).
    """
    import os as _os
    env = environ if environ is not None else dict(_os.environ)

    # ── Resolve connection ────────────────────────────────────────────────────
    try:
        connection = resolve_connection(config_path=config_path, environ=env)
    except N8NMCPConnectionError as exc:
        raise N8NExecutionError(f"n8n connection config error: {exc}") from exc

    if not connection.safe_to_probe:
        reasons = "; ".join(connection.blocked_reasons)
        raise N8NExecutionError(
            f"n8n live execution blocked: {reasons}. "
            "Set N8N_BASE_URL and N8N_MCP_ACCESS_TOKEN, then set deployment.enabled=true "
            "and secrets_configured=true in n8n_config.yaml."
        )

    # ── Load and validate workflow registry ───────────────────────────────────
    registry = load_workflow_registry(registry_path)
    verdict = validate_registry(registry)
    if not verdict["ok"]:
        raise N8NExecutionError(f"n8n workflow registry invalid: {'; '.join(verdict['errors'])}")

    workflows = registry.get("workflows", [])
    workflow = next(
        (w for w in workflows if isinstance(w, dict) and w.get("workflow_id") == workflow_id),
        None,
    )
    if workflow is None:
        raise N8NExecutionError(f"unknown n8n workflow_id: {workflow_id!r}")

    # ── Caller check ──────────────────────────────────────────────────────────
    allowed_callers = workflow.get("allowed_callers") or []
    if caller not in allowed_callers:
        raise N8NExecutionError(
            f"caller {caller!r} is not allowed for workflow {workflow_id!r}; "
            f"allowed: {sorted(allowed_callers)}"
        )

    # ── Policy check ──────────────────────────────────────────────────────────
    policy_result = validate_workflow_policy(workflow, production=production, approved=approved)
    if not policy_result["ok"]:
        raise N8NExecutionError(
            f"workflow {workflow_id!r} policy blocked: {'; '.join(policy_result['errors'])}"
        )

    # ── Blocked status is a hard stop ─────────────────────────────────────────
    if workflow.get("current_status") == "blocked":
        raise N8NExecutionError(
            f"workflow {workflow_id!r} has current_status='blocked' and must not be executed"
        )

    # ── Determine trigger path ────────────────────────────────────────────────
    trigger_type = str(workflow.get("trigger_type") or "webhook")
    token = env.get(connection.token_env_var) or ""
    base_url = connection.base_url or ""

    if trigger_type == "mcp_tool":
        if not connection.mcp_server_url:
            raise N8NExecutionError("mcp_server_url could not be resolved from config")
        if not token:
            raise N8NExecutionError(
                f"n8n MCP access token env var '{connection.token_env_var}' is not set"
            )
        http_result = _trigger_via_mcp_tool(
            mcp_server_url=connection.mcp_server_url,
            workflow_id=workflow_id,
            payload=payload or {},
            token=token,
            timeout_s=timeout_s,
        )
    else:
        # webhook trigger — token optional (some n8n webhooks are public)
        http_result = _trigger_via_webhook(
            base_url=base_url,
            workflow_id=workflow_id,
            payload=payload or {},
            token=token or None,
            timeout_s=timeout_s,
        )

    return {
        "ok": http_result.get("ok", False),
        "live_http_call": True,
        "workflow_id": workflow_id,
        "caller": caller,
        "trigger_type": trigger_type,
        "production": production,
        "approved": approved,
        "executed_at_utc": _utc_stamp(),
        "http_status": http_result.get("http_status"),
        # Redact secret-like material before surfacing remote workflow output —
        # an n8n workflow response can echo tokens/keys (shared redaction).
        "response": _redact_obj(http_result.get("response")),
        "response_excerpt": _redact_text(http_result.get("response_excerpt") or "") or None,
        "error": http_result.get("error"),
        "reason": http_result.get("reason"),
        "policy": {
            "reads": workflow.get("reads", []),
            "writes": workflow.get("writes", []),
            "secrets_required": workflow.get("secrets_required", []),
            "current_status": workflow.get("current_status"),
        },
    }
