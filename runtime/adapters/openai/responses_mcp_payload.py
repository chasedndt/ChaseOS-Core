"""Dry-run Responses API remote MCP payload builder.

The builder emits an auditable JSON template for a future Responses API call.
It never imports an OpenAI client, reads API keys, or performs a network call.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FORBIDDEN_DATA_CLASSES = [
    "secrets",
    "credentials",
    "wallet_keys",
    "seed_phrases",
    "exchange_api_keys",
    "raw_personal_files",
    "protected_canonical_state",
]

FORBIDDEN_TOOL_NAMES = {
    "execute_trade",
    "place_order",
    "sign_transaction",
    "send_discord_message",
    "write_file",
    "shell",
}


class ResponsesMCPPolicyError(ValueError):
    """Raised when a dry-run payload would violate ChaseOS policy."""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_safe_label(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value))


def build_responses_mcp_payload(
    *,
    prompt: str,
    server_label: str,
    server_url: str | None = None,
    connector_id: str | None = None,
    allowed_tools: list[str] | None = None,
    require_approval: str | dict[str, Any] = "always",
    model: str = "gpt-5.5",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a policy-marked Responses API MCP payload template.

    Exactly one of ``server_url`` or ``connector_id`` must be provided. The
    resulting object is a dry-run request record, not a live API call.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ResponsesMCPPolicyError("prompt is required")
    if not _is_safe_label(server_label):
        raise ResponsesMCPPolicyError("server_label must be 1-64 safe identifier characters")
    if bool(server_url) == bool(connector_id):
        raise ResponsesMCPPolicyError("provide exactly one of server_url or connector_id")
    if server_url and not server_url.startswith("https://"):
        raise ResponsesMCPPolicyError("remote MCP server_url must be https")
    if require_approval == "never":
        raise ResponsesMCPPolicyError("ChaseOS first-pass MCP calls must require approval")

    tools = list(allowed_tools or [])
    blocked = sorted(set(tools).intersection(FORBIDDEN_TOOL_NAMES))
    if blocked:
        raise ResponsesMCPPolicyError(f"forbidden MCP tools requested: {blocked}")

    mcp_tool: dict[str, Any] = {
        "type": "mcp",
        "server_label": server_label,
        "require_approval": require_approval,
    }
    if server_url:
        mcp_tool["server_url"] = server_url
    if connector_id:
        mcp_tool["connector_id"] = connector_id
    if tools:
        mcp_tool["allowed_tools"] = tools

    request_metadata = {
        "chaseos_adapter": "responses_api_mcp",
        "dry_run": True,
        "live_api_call": False,
        "approval_required": True,
        "created_at_utc": _utc_stamp(),
        "data_sharing_warning": (
            "Remote MCP calls may share prompt/tool-call data with a third-party server; "
            "operator approval and audit are required before live use."
        ),
        "forbidden_data_classes": list(FORBIDDEN_DATA_CLASSES),
    }
    request_metadata.update(metadata or {})

    return {
        "dry_run": True,
        "api": "responses.create",
        "model": model,
        "input": prompt.strip(),
        "tools": [mcp_tool],
        "metadata": request_metadata,
    }


def validate_payload_policy(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a dry-run payload against first-pass ChaseOS MCP policy."""
    errors: list[str] = []
    if payload.get("dry_run") is not True:
        errors.append("payload must be marked dry_run=true")
    if payload.get("metadata", {}).get("live_api_call") is not False:
        errors.append("live_api_call must be false")

    tools = payload.get("tools")
    if not isinstance(tools, list) or not tools:
        errors.append("payload must include at least one tool")
    else:
        for tool in tools:
            if tool.get("type") != "mcp":
                errors.append("only MCP tools are allowed in this builder")
            if tool.get("require_approval") == "never":
                errors.append("require_approval=never is forbidden")
            allowed = tool.get("allowed_tools") or []
            if isinstance(allowed, list):
                blocked = sorted(set(allowed).intersection(FORBIDDEN_TOOL_NAMES))
                if blocked:
                    errors.append(f"forbidden tool names: {blocked}")
            if not (tool.get("server_url") or tool.get("connector_id")):
                errors.append("MCP tool must provide server_url or connector_id")

    return {"ok": not errors, "errors": errors}


def write_payload_draft(
    payload: dict[str, Any],
    *,
    vault_root: Path,
    descriptor: str = "responses-mcp-payload",
) -> Path:
    """Write a dry-run payload under Agent-Activity for audit review."""
    verdict = validate_payload_policy(payload)
    if not verdict["ok"]:
        raise ResponsesMCPPolicyError("; ".join(verdict["errors"]))
    if not re.fullmatch(r"[a-z0-9_.-]{1,80}", descriptor):
        raise ResponsesMCPPolicyError("descriptor must be kebab/safe-case")

    out_dir = vault_root / "07_LOGS" / "Agent-Activity" / "_dry_run_payloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{descriptor}.json"
    out_path = out_dir / filename
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path
