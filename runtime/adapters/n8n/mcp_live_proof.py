"""Durable n8n MCP connection proof artifacts.

The proof runner records redacted readiness/probe evidence. It never configures
credentials, never stores credential values, and only attempts a live HTTP probe
when explicitly requested and the existing n8n connection policy says it is safe.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.adapters.n8n.mcp_connection import build_connection_readiness, probe_instance_mcp


PROOF_DIR = Path("07_LOGS/Agent-Activity/_n8n_mcp_proofs")


class N8NMCPLiveProofError(ValueError):
    """Raised when an n8n MCP proof artifact violates audit policy."""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_filename_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "n8n-mcp-proof"


def _ensure_child(parent: Path, child: Path) -> Path:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError as exc:
        raise N8NMCPLiveProofError(f"path escapes expected directory: {child}") from exc
    return child_resolved


def _proof_dir(vault_root: Path) -> Path:
    return _ensure_child(vault_root, vault_root / PROOF_DIR)


def _blocked_reasons(readiness: dict[str, Any]) -> list[str]:
    connection = readiness.get("connection") if isinstance(readiness, dict) else None
    registry = readiness.get("registry") if isinstance(readiness, dict) else None
    reasons: list[str] = []
    if isinstance(connection, dict):
        reasons.extend(str(item) for item in connection.get("blocked_reasons", []))
    if isinstance(registry, dict) and registry.get("ok") is not True:
        reasons.extend(str(item) for item in registry.get("errors", []))
    return reasons


def _summarize_probe_result(result: dict[str, Any]) -> dict[str, Any]:
    response = result.get("response")
    response_result = response.get("result") if isinstance(response, dict) else None
    summary: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "live_http_call": bool(result.get("live_http_call")),
        "blocked": bool(result.get("blocked")),
        "http_status": result.get("http_status"),
        "connection": result.get("connection"),
        "error": result.get("error"),
        "reason": result.get("reason"),
        "response_excerpt_logged": False,
        "credential_values_logged": False,
    }
    if isinstance(response, dict):
        summary["jsonrpc"] = response.get("jsonrpc")
        summary["has_error"] = "error" in response
    if isinstance(response_result, dict):
        server_info = response_result.get("serverInfo")
        capabilities = response_result.get("capabilities")
        summary["protocol_version"] = response_result.get("protocolVersion")
        summary["server_info"] = server_info if isinstance(server_info, dict) else None
        summary["capabilities_keys"] = sorted(capabilities) if isinstance(capabilities, dict) else []
    return summary


def build_mcp_connection_proof(
    *,
    config_path: Path,
    registry_path: Path,
    environ: dict[str, str] | None = None,
    live_probe: bool = False,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """Build a redacted n8n MCP proof artifact, optionally with an explicit live probe."""
    readiness = build_connection_readiness(
        config_path=config_path,
        registry_path=registry_path,
        environ=environ,
    )
    proof: dict[str, Any] = {
        "schema_version": "1.0",
        "proof_type": "n8n_mcp_connection_proof",
        "created_at_utc": _utc_stamp(),
        "config_path": str(config_path),
        "registry_path": str(registry_path),
        "ok": False,
        "proof_status": "blocked",
        "live_probe_requested": live_probe,
        "live_probe_attempted": False,
        "live_http_call": False,
        "credential_values_logged": False,
        "canonical_writeback": False,
        "readiness": readiness,
        "blocked_reasons": _blocked_reasons(readiness),
    }

    if not live_probe:
        if readiness.get("ok"):
            proof["ok"] = True
            proof["proof_status"] = "ready_not_probed"
        return proof

    if not readiness.get("ok"):
        proof["proof_status"] = "blocked_before_live_probe"
        return proof

    probe = probe_instance_mcp(
        config_path=config_path,
        environ=environ,
        timeout_s=timeout_s,
        request_id="n8n-mcp-live-proof",
    )
    probe_summary = _summarize_probe_result(probe)
    proof["probe"] = probe_summary
    proof["live_probe_attempted"] = bool(probe_summary.get("live_http_call"))
    proof["live_http_call"] = bool(probe_summary.get("live_http_call"))
    proof["ok"] = bool(probe_summary.get("ok"))
    proof["proof_status"] = "live_probe_passed" if proof["ok"] else "live_probe_failed"
    return proof


def write_mcp_connection_proof(
    proof: dict[str, Any],
    *,
    vault_root: Path,
    descriptor: str | None = None,
) -> Path:
    """Write an n8n MCP proof artifact to the Agent-Activity audit surface."""
    if proof.get("proof_type") != "n8n_mcp_connection_proof":
        raise N8NMCPLiveProofError("unexpected proof_type")
    if proof.get("credential_values_logged") is not False:
        raise N8NMCPLiveProofError("proof artifact must not log credential values")
    if proof.get("canonical_writeback") is not False:
        raise N8NMCPLiveProofError("proof artifact must not perform canonical writeback")
    serialized = json.dumps(proof, indent=2, sort_keys=True)
    out_dir = _proof_dir(vault_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = descriptor or str(proof.get("proof_status") or "n8n-mcp-proof")
    out_path = _ensure_child(out_dir, out_dir / f"{_utc_filename_stamp()}-{_safe_slug(suffix)}.json")
    out_path.write_text(serialized + "\n", encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Create a redacted n8n MCP connection proof artifact.")
    parser.add_argument("--config", default="runtime/policy/adapters/n8n_config.yaml")
    parser.add_argument("--registry", default="runtime/policy/adapters/n8n_workflows.yaml")
    parser.add_argument("--vault-root", default=".")
    parser.add_argument("--live-probe", action="store_true", help="Attempt an explicit safe live MCP probe.")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--write-proof", action="store_true", help="Write proof JSON under Agent-Activity.")
    parser.add_argument("--descriptor", default=None)
    args = parser.parse_args(argv)

    proof = build_mcp_connection_proof(
        config_path=Path(args.config),
        registry_path=Path(args.registry),
        live_probe=args.live_probe,
        timeout_s=args.timeout,
    )
    if args.write_proof:
        out_path = write_mcp_connection_proof(
            proof,
            vault_root=Path(args.vault_root),
            descriptor=args.descriptor,
        )
        proof["proof_path"] = str(out_path)
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
