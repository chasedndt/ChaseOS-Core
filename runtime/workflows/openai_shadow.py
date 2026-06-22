"""OpenAI shadow operator/research workflow.

This handler is local-only and dry-run oriented. It prepares a draft
operator/research brief and audit note through AOR writeback. It never calls
OpenAI, n8n, Discord, shell, or external services.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

from runtime.aor.registry import _parse_simple_yaml
from runtime.adapters.n8n.workflow_policy import build_n8n_call_draft
from runtime.adapters.openai.responses_mcp_payload import build_responses_mcp_payload


class WorkflowExecutionError(RuntimeError):
    """Fail-closed workflow error surfaced as AOR escalation."""


CONFIG_REL = Path("runtime/policy/adapters/openai_config.yaml")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text)
    else:
        data = _parse_simple_yaml(text)
    if not isinstance(data, dict):
        raise WorkflowExecutionError(f"{path} did not parse as a mapping")
    return data


def _normalize_rel(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("/")


def _is_within_any(path: str, roots: list[str]) -> bool:
    normalized = _normalize_rel(path)
    for root in roots:
        root_norm = _normalize_rel(root).rstrip("/")
        if normalized == root_norm or normalized.startswith(root_norm + "/"):
            return True
    return False


def _read_declared_context(vault_root: Path, manifest: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    allowlist = [_normalize_rel(item) for item in config.get("readable_path_allowlist", [])]
    context: dict[str, str] = {}
    for rel in manifest.get("required_reads", []):
        rel_norm = _normalize_rel(str(rel))
        if not _is_within_any(rel_norm, allowlist):
            raise WorkflowExecutionError(f"undeclared read blocked: {rel_norm}")
        full = vault_root / rel_norm
        if full.is_dir():
            entries = [child.name for child in sorted(full.iterdir()) if not child.name.startswith(".")]
            context[rel_norm] = "\n".join(entries[:20])
        elif full.is_file():
            context[rel_norm] = full.read_text(encoding="utf-8", errors="replace")[:5000]
        else:
            raise WorkflowExecutionError(f"declared read missing: {rel_norm}")
    return context


def _extract_summary(context: dict[str, str]) -> list[str]:
    bullets: list[str] = []
    for rel, text in context.items():
        first_line = next((line.strip("# ").strip() for line in text.splitlines() if line.strip()), "")
        if first_line:
            bullets.append(f"- {rel}: {first_line[:180]}")
    return bullets[:8]


def _safe_run_label(value: Any) -> str:
    raw = str(value or "run").lower().strip()
    safe = re.sub(r"[^a-z0-9_.-]+", "-", raw).strip("-")
    return safe[:60] or "run"


def run_openai_operator_research_shadow(
    *,
    inputs: dict[str, Any],
    vault_root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    config_path = vault_root / CONFIG_REL
    if not config_path.exists():
        raise WorkflowExecutionError(f"OpenAI config missing: {CONFIG_REL}")
    config = _load_yaml(config_path)

    if config.get("status") != "shadow_proof":
        raise WorkflowExecutionError("OpenAI adapter must remain status=shadow_proof for this workflow")
    if config.get("live_api_calls", {}).get("enabled") is not False:
        raise WorkflowExecutionError("live OpenAI API calls are disabled for this workflow")
    if config.get("canonical_writeback", {}).get("enabled") is not False:
        raise WorkflowExecutionError("canonical writeback must remain disabled")
    if manifest.get("id") not in config.get("approved_workflows", []):
        raise WorkflowExecutionError("workflow is not approved in openai_config.yaml")

    context = _read_declared_context(vault_root, manifest, config)
    now = _utc_now()
    date = now.strftime("%Y-%m-%d")
    label = _safe_run_label(inputs.get("run_label") or now.strftime("%H%M%S"))

    responses_payload = build_responses_mcp_payload(
        prompt="Draft an operator/research brief from the ChaseOS context packet only.",
        server_label="chaseos_runtime_mcp",
        server_url="https://example.invalid/mcp",
        allowed_tools=["chaseos.current_state", "chaseos.create_research_digest_draft"],
        require_approval="always",
        metadata={"workflow_id": manifest.get("id"), "note": "template only; no live call"},
    )

    n8n_draft: dict[str, Any] | None = None
    n8n_registry = vault_root / "runtime" / "policy" / "adapters" / "n8n_workflows.yaml"
    if n8n_registry.exists():
        n8n_draft = build_n8n_call_draft(
            workflow_id="capture_research_digest",
            registry_path=n8n_registry,
            caller="openai_operator_research_shadow",
            payload={"brief_status": "draft_only", "source": "openai_shadow"},
        )

    draft_path = f"07_LOGS/Operator-Briefs/_drafts/{date}-openai-operator-research-shadow-{label}.md"
    audit_path = f"07_LOGS/Agent-Activity/{date}-openai-openai_operator_research_shadow-{label}.md"
    declared_targets = list(manifest.get("writeback_targets", []))
    for candidate in (draft_path, audit_path):
        if not _is_within_any(candidate, declared_targets):
            raise WorkflowExecutionError(f"handler writeback outside declared targets: {candidate}")

    summary = _extract_summary(context)
    draft_lines = [
        "---",
        "type: operator-research-brief-draft",
        "runtime: OpenAI",
        "workflow_id: openai_operator_research_shadow",
        "status: DRAFT",
        "live_api_calls: false",
        "canonical_writeback: false",
        f"created_at_utc: {now.isoformat()}",
        "---",
        "",
        "# OpenAI Operator Research Shadow Draft",
        "",
        "## Repo Context Packet",
        *summary,
        "",
        "## Draft Brief",
        "This local shadow pass prepared a brief frame from declared ChaseOS context only.",
        "No OpenAI API call, remote MCP call, n8n execution, Discord send, shell command, or canonical writeback occurred.",
        "",
        "## Responses API MCP Payload Template",
        f"- api: {responses_payload['api']}",
        f"- dry_run: {responses_payload['dry_run']}",
        f"- require_approval: {responses_payload['tools'][0]['require_approval']}",
        f"- allowed_tools: {responses_payload['tools'][0].get('allowed_tools', [])}",
        "",
        "## n8n Draft Request",
        f"- prepared: {bool(n8n_draft)}",
        f"- workflow_id: {n8n_draft.get('workflow_id') if n8n_draft else 'not_available'}",
        f"- live_http_call: {n8n_draft.get('live_http_call') if n8n_draft else False}",
        "",
        "## Forbidden This Pass",
        "- canonical Now.md mutation",
        "- Project-OS mutation",
        "- 02_KNOWLEDGE promotion",
        "- Discord or Telegram live send",
        "- trading execution or wallet/exchange signing",
    ]
    audit_lines = [
        "# OpenAI Shadow Workflow Activity",
        "",
        f"- Runtime: Codex-executed local handler for OpenAI adapter foundation",
        f"- Workflow: {manifest.get('id')}",
        f"- Created UTC: {now.isoformat()}",
        "- Live OpenAI API call: false",
        "- Live n8n HTTP call: false",
        "- Canonical writeback: false",
        f"- Files read: {', '.join(context.keys())}",
        f"- Files proposed for AOR writeback: {draft_path}, {audit_path}",
        "",
        "## Boundary Result",
        "The workflow stayed inside draft/audit write targets and prepared only dry-run request templates.",
    ]

    return {
        "status": "shadow_draft_created",
        "live_api_calls": False,
        "canonical_writeback": False,
        "writebacks": [
            {"path": draft_path, "content": "\n".join(draft_lines) + "\n"},
            {"path": audit_path, "content": "\n".join(audit_lines) + "\n"},
        ],
        "dry_run_payloads": {
            "responses_mcp": responses_payload,
            "n8n": n8n_draft,
        },
    }

