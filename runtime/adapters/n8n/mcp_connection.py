"""n8n MCP connection readiness and local live-probe helpers.

This module does not configure credentials. It reads declared environment
variable names from the n8n adapter config, redacts sensitive values, and only
performs a live HTTP probe when explicitly requested and local-only policy
passes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from runtime.common.simple_yaml import parse_simple_yaml as _parse_simple_yaml
from runtime.adapters.n8n.workflow_policy import load_workflow_registry, validate_registry


DEFAULT_MCP_PATH = "/mcp-server/http"
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


class N8NMCPConnectionError(ValueError):
    """Raised when n8n MCP connection configuration is unsafe or invalid."""


@dataclass(frozen=True)
class N8NMCPConnection:
    enabled: bool
    base_url: str | None
    mcp_server_url: str | None
    token_present: bool
    token_env_var: str
    base_url_env_var: str
    local_only: bool
    safe_to_probe: bool
    blocked_reasons: tuple[str, ...]

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url_configured": bool(self.base_url),
            "mcp_server_url": self.mcp_server_url,
            "token_present": self.token_present,
            "token_env_var": self.token_env_var,
            "base_url_env_var": self.base_url_env_var,
            "local_only": self.local_only,
            "safe_to_probe": self.safe_to_probe,
            "blocked_reasons": list(self.blocked_reasons),
        }


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text)
    else:
        data = _parse_simple_yaml(text)
    if not isinstance(data, dict):
        raise N8NMCPConnectionError(f"{path} must parse as a mapping")
    return data


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().rstrip("/")
    return stripped or None


def _is_local_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.hostname in LOCAL_HOSTNAMES


def _mcp_url(base_url: str | None, path: str) -> str | None:
    if not base_url:
        return None
    return base_url.rstrip("/") + "/" + path.strip("/")


def resolve_connection(
    *,
    config_path: Path,
    environ: dict[str, str] | None = None,
) -> N8NMCPConnection:
    config = _load_yaml_mapping(config_path)
    deployment = config.get("deployment")
    if not isinstance(deployment, dict):
        raise N8NMCPConnectionError("deployment config is required")

    env = environ if environ is not None else os.environ
    base_url_env_var = str(deployment.get("base_url_env_var") or "N8N_BASE_URL")
    token_env_var = str(deployment.get("mcp_access_token_env_var") or "N8N_MCP_ACCESS_TOKEN")
    mcp_path = str(deployment.get("mcp_http_path") or DEFAULT_MCP_PATH)
    local_only = _as_bool(deployment.get("local_only"), default=True)
    enabled = _as_bool(deployment.get("enabled"), default=False)
    secrets_configured = _as_bool(deployment.get("secrets_configured"), default=False)

    base_url = _normalize_base_url(env.get(base_url_env_var))
    token_present = bool(env.get(token_env_var))
    mcp_server_url = _mcp_url(base_url, mcp_path)

    blocked: list[str] = []
    if not enabled:
        blocked.append("deployment.enabled is false")
    if not secrets_configured:
        blocked.append("deployment.secrets_configured is false")
    if not base_url:
        blocked.append(f"{base_url_env_var} is not set")
    if not token_present:
        blocked.append(f"{token_env_var} is not set")
    if base_url and local_only and not _is_local_url(base_url):
        blocked.append("non-local n8n base URL requires explicit approval")

    return N8NMCPConnection(
        enabled=enabled,
        base_url=base_url,
        mcp_server_url=mcp_server_url,
        token_present=token_present,
        token_env_var=token_env_var,
        base_url_env_var=base_url_env_var,
        local_only=local_only,
        safe_to_probe=not blocked,
        blocked_reasons=tuple(blocked),
    )


def build_connection_readiness(
    *,
    config_path: Path,
    registry_path: Path,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    connection = resolve_connection(config_path=config_path, environ=environ)
    registry = load_workflow_registry(registry_path)
    registry_verdict = validate_registry(registry)
    workflows = registry.get("workflows", []) if isinstance(registry, dict) else []
    exposed = [
        item.get("workflow_id")
        for item in workflows
        if isinstance(item, dict) and item.get("exposed_to_mcp") is True
    ]
    ok = connection.safe_to_probe and registry_verdict["ok"]
    blocked_reasons = list(connection.blocked_reasons) + list(registry_verdict["errors"])
    return {
        "ok": ok,
        "status": "ready" if ok else "blocked",
        "readiness_status": "ready" if ok else "blocked",
        "blocked_reason": None if ok else (blocked_reasons[0] if blocked_reasons else "n8n_readiness_blocked"),
        "blocked_reasons": blocked_reasons,
        "live_http_call": False,
        "connection": connection.to_redacted_dict(),
        "registry": {
            "ok": registry_verdict["ok"],
            "errors": registry_verdict["errors"],
            "workflow_count": registry_verdict["workflow_count"],
            "exposed_to_mcp": exposed,
            "expose_all_workflows": False,
        },
        "forbidden": {
            "credential_values_logged": False,
            "autonomous_canonical_writeback": False,
            "production_execution_without_approval": False,
            "trading_execution": False,
        },
        "writes_performed": False,
        "authority_flags": {
            "read_only": True,
            "mutates_workflows": False,
            "live_http_call": False,
            "writes_performed": False,
            "credential_values_logged": False,
        },
    }


def probe_instance_mcp(
    *,
    config_path: Path,
    environ: dict[str, str] | None = None,
    timeout_s: float = 5.0,
    request_id: str = "n8n-mcp-probe",
) -> dict[str, Any]:
    env = environ if environ is not None else os.environ
    connection = resolve_connection(config_path=config_path, environ=env)
    if not connection.safe_to_probe:
        return {
            "ok": False,
            "live_http_call": False,
            "blocked": True,
            "connection": connection.to_redacted_dict(),
        }
    token = env.get(connection.token_env_var)
    if not token or not connection.mcp_server_url:
        raise N8NMCPConnectionError("connection unexpectedly missing token or MCP URL")

    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "chaseos-n8n-mcp-probe", "version": "0.1.0"},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        connection.mcp_server_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - local-only guarded above
            text = response.read().decode("utf-8", errors="replace")
            status = response.status
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "live_http_call": True,
            "blocked": False,
            "http_status": exc.code,
            "connection": connection.to_redacted_dict(),
            "error": "http_error",
            "response_excerpt": text[:500],
        }
    except URLError as exc:
        return {
            "ok": False,
            "live_http_call": True,
            "blocked": False,
            "connection": connection.to_redacted_dict(),
            "error": "url_error",
            "reason": str(exc.reason),
        }

    parsed: dict[str, Any] | None = None
    try:
        candidate = json.loads(text)
        if isinstance(candidate, dict):
            parsed = candidate
    except json.JSONDecodeError:
        parsed = None

    return {
        "ok": 200 <= status < 300 and (parsed is None or "error" not in parsed),
        "live_http_call": True,
        "blocked": False,
        "http_status": status,
        "connection": connection.to_redacted_dict(),
        "response": parsed,
        "response_excerpt": None if parsed is not None else text[:500],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check n8n MCP connection readiness.")
    parser.add_argument("--config", default="runtime/policy/adapters/n8n_config.yaml")
    parser.add_argument("--registry", default="runtime/policy/adapters/n8n_workflows.yaml")
    parser.add_argument("--live-probe", action="store_true", help="Perform an explicit local-only live HTTP probe.")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    registry_path = Path(args.registry)
    if args.live_probe:
        result = probe_instance_mcp(config_path=config_path, timeout_s=args.timeout)
    else:
        result = build_connection_readiness(config_path=config_path, registry_path=registry_path)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
