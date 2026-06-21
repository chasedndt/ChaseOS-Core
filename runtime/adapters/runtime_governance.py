"""Governance checks for high-privilege ChaseOS runtime adapters.

This module validates the current OpenClaw/Hermes authority envelope without
executing either runtime. It is intentionally read-only: config in, stable
JSON-style report out.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

try:
    import yaml as _pyyaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - exercised by monkeypatch tests.
    _pyyaml = None


REQUIRED_DENIED_WRITE_TARGETS = {
    "00_HOME/**",
    "01_PROJECTS/**",
    "02_KNOWLEDGE/**",
    "03_INPUTS/**",
    "04_SOPS/**",
    "05_TEMPLATES/**",
    "06_AGENTS/**",
    "runtime/**",
    ".claude/**",
    ".codex/**",
}

REQUIRED_HERMES_FORBIDDEN_COMMANDS = {
    "shell.execute",
    "shell.git",
    "filesystem.delete",
    "filesystem.rename",
    "filesystem.move",
    "network.http",
    "connector.invoke",
    "credential.read",
    "vault.promote",
}

APPROVED_HERMES_WORKFLOWS = {
    "hermes_operator_today_shadow",
    "hermes_review_execute",
    "hermes_watch",
}

FORBIDDEN_OPENCLAW_CAPABILITY_TERMS = (
    "shell",
    "network",
    "credential",
    "secret",
    "wallet",
    "exchange",
    "trade",
    "trading",
    "connector",
)

SHARED_RPGL_EXECUTION_MARKERS = {
    "imports_governance_layer": "from runtime.providers.governance_layer import",
    "imports_route_task": "route_task",
    "imports_rate_limit_marker": "mark_primary_rate_limited",
    "imports_unhealthy_marker": "mark_primary_unhealthy",
    "imports_capability_gate": "is_task_allowed_for_strength",
    "imports_high_authority_classes": "HIGH_AUTHORITY_TASK_CLASSES",
}

DIRECT_PROVIDER_CALL_MARKERS = (
    "_call_anthropic(",
    "urllib.request.urlopen",
    "from anthropic",
    "import anthropic",
    "from openai",
    "import openai",
    "execute_local_ollama_fallback_stream",
)

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def vault_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _strip_comment_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    return line.rstrip("\n")


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if raw == "":
        return ""
    if raw in {"[]", "[ ]"}:
        return []
    if raw in {"{}", "{ }"}:
        return {}
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    if raw.lower() in {"null", "none", "~"}:
        return None
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    if re.fullmatch(r"-?\d+", raw):
        try:
            return int(raw)
        except ValueError:
            return raw
    return raw


def _split_key_value(text: str) -> tuple[str, str] | None:
    if ":" not in text:
        return None
    key, value = text.split(":", 1)
    key = key.strip()
    if not key or not _KEY_PATTERN.match(key):
        return None
    return key, value.strip()


def _logical_yaml_lines(text: str) -> list[tuple[int, str, str]]:
    lines: list[tuple[int, str, str]] = []
    for raw_line in text.splitlines():
        line = _strip_comment_line(raw_line)
        if line is None:
            continue
        indent = len(line) - len(line.lstrip(" "))
        lines.append((indent, line.strip(), line))
    return lines


def _parse_block_scalar(
    lines: list[tuple[int, str, str]],
    start: int,
    parent_indent: int,
) -> tuple[str, int]:
    block: list[tuple[int, str]] = []
    index = start
    while index < len(lines):
        indent, _stripped, raw = lines[index]
        if indent <= parent_indent:
            break
        block.append((indent, raw))
        index += 1
    if not block:
        return "", index
    base_indent = min(indent for indent, raw in block if raw.strip()) if any(raw.strip() for _, raw in block) else parent_indent + 2
    return "\n".join(raw[base_indent:] if len(raw) >= base_indent else "" for _, raw in block).rstrip(), index


def _parse_yaml_block(lines: list[tuple[int, str, str]], start: int, indent: int) -> tuple[Any, int]:
    if start >= len(lines):
        return {}, start
    current_indent, stripped, _raw = lines[start]
    if current_indent < indent:
        return {}, start
    if stripped.startswith("- "):
        return _parse_yaml_list(lines, start, current_indent)
    return _parse_yaml_mapping(lines, start, current_indent)


def _parse_yaml_mapping(lines: list[tuple[int, str, str]], start: int, indent: int) -> tuple[dict[str, Any], int]:
    data: dict[str, Any] = {}
    index = start
    while index < len(lines):
        current_indent, stripped, _raw = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            break
        if stripped.startswith("- "):
            break
        split = _split_key_value(stripped)
        if split is None:
            raise ValueError(f"unsupported YAML line: {stripped!r}")
        key, value = split
        if value in {"|", ">"}:
            data[key], index = _parse_block_scalar(lines, index + 1, current_indent)
            continue
        if value == "":
            next_index = index + 1
            if next_index >= len(lines) or lines[next_index][0] <= current_indent:
                data[key] = {}
                index = next_index
                continue
            data[key], index = _parse_yaml_block(lines, next_index, lines[next_index][0])
            continue
        data[key] = _parse_scalar(value)
        index += 1
    return data, index


def _parse_yaml_list(lines: list[tuple[int, str, str]], start: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    index = start
    while index < len(lines):
        current_indent, stripped, _raw = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or not stripped.startswith("- "):
            break
        item_text = stripped[2:].strip()
        if item_text == "":
            next_index = index + 1
            if next_index >= len(lines) or lines[next_index][0] <= current_indent:
                items.append({})
                index = next_index
                continue
            item, index = _parse_yaml_block(lines, next_index, lines[next_index][0])
            items.append(item)
            continue
        split = _split_key_value(item_text)
        if split is not None:
            key, value = split
            item: dict[str, Any] = {key: _parse_scalar(value) if value else {}}
            next_index = index + 1
            if next_index < len(lines) and lines[next_index][0] > current_indent:
                nested, next_index = _parse_yaml_mapping(lines, next_index, lines[next_index][0])
                item.update(nested)
            items.append(item)
            index = next_index
            continue
        items.append(_parse_scalar(item_text))
        index += 1
    return items, index


def _safe_load_yaml_subset(text: str) -> Any:
    lines = _logical_yaml_lines(text)
    if not lines:
        return {}
    parsed, index = _parse_yaml_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError(f"unsupported YAML structure near line: {lines[index][1]!r}")
    return parsed


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if _pyyaml is not None:
        loaded = _pyyaml.safe_load(text) or {}
    else:
        loaded = _safe_load_yaml_subset(text) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded


def _issue(issues: list[str], code: str, message: str) -> None:
    issues.append(f"{code}: {message}")


def _manifest_checks(adapter_id: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    denied = set(manifest.get("explicitly_denied_write_targets") or [])
    allowed_writes = manifest.get("allowed_write_targets") or {}
    promotion = manifest.get("promotion_behavior") or {}
    external = manifest.get("external_side_effect_policy") or {}
    coordination = manifest.get("coordination_policy") or {}

    required_denied_present = REQUIRED_DENIED_WRITE_TARGETS.issubset(denied)
    if not required_denied_present:
        missing = sorted(REQUIRED_DENIED_WRITE_TARGETS - denied)
        _issue(issues, f"{adapter_id}.denied_write_targets", f"missing denied targets: {missing}")

    protected_write_flags_blocked = all(
        allowed_writes.get(flag) is False
        for flag in ("project_os_files", "knowledge_notes", "inputs_folder", "protected_files")
    )
    if not protected_write_flags_blocked:
        _issue(
            issues,
            f"{adapter_id}.allowed_write_targets",
            "project, knowledge, inputs, and protected write flags must stay false",
        )

    promotion_blocked = (
        promotion.get("may_promote_to_knowledge") == "no"
        and promotion.get("autonomous_promotion") is False
    )
    if not promotion_blocked:
        _issue(
            issues,
            f"{adapter_id}.promotion",
            "canonical promotion must remain blocked and non-autonomous",
        )

    external_blocked = (
        external.get("may_call_external_apis") == "no"
        and external.get("may_write_to_external_systems") == "no"
    )
    if not external_blocked:
        _issue(issues, f"{adapter_id}.external", "external API/write side effects must remain disabled")

    bus_required = (
        coordination.get("cross_runtime_coordination") == "bus-required"
        and coordination.get("direct_runtime_state_in_chat") is False
        and coordination.get("coordination_source_of_truth") == "runtime/agent_bus/"
    )
    if not bus_required:
        _issue(
            issues,
            f"{adapter_id}.coordination",
            "runtime coordination must remain bus-required, not chat-state driven",
        )

    if manifest.get("status") != "active":
        _issue(issues, f"{adapter_id}.status", "adapter manifest must remain active")
    if manifest.get("trust_ceiling") != "tier-2":
        _issue(issues, f"{adapter_id}.trust_ceiling", "trust ceiling must remain tier-2")
    if manifest.get("protected_file_behavior") != "block":
        _issue(issues, f"{adapter_id}.protected_files", "protected-file behavior must be block")
    if manifest.get("approval_mode") != "manifest-bounded-per-action":
        _issue(issues, f"{adapter_id}.approval", "approval mode must remain manifest-bounded-per-action")

    return {
        "status": manifest.get("status"),
        "trust_ceiling": manifest.get("trust_ceiling"),
        "allowed_task_types": list(manifest.get("allowed_task_types") or []),
        "checks": {
            "required_denied_write_targets_present": required_denied_present,
            "protected_write_flags_blocked": protected_write_flags_blocked,
            "promotion_blocked": promotion_blocked,
            "external_side_effects_blocked": external_blocked,
            "coordination_bus_required": bus_required,
            "protected_file_behavior_blocks": manifest.get("protected_file_behavior") == "block",
            "approval_mode_manifest_bounded": manifest.get("approval_mode")
            == "manifest-bounded-per-action",
        },
        "blocking_issues": issues,
    }


def _hermes_config_checks(config: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    approved_workflows = list(config.get("approved_workflows") or [])
    forbidden_commands = set(config.get("forbidden_command_families") or [])
    allowed_commands = set(config.get("allowed_command_families") or [])
    connector_policy = config.get("connector_policy") or {}
    approval_model = config.get("approval_model") or {}
    promotion = config.get("promotion_writeback_rules") or {}

    bounded_workflows_only = (
        config.get("status") == "shadow-active"
        and config.get("mode") == "approval-first-shadow"
        and set(approved_workflows) == APPROVED_HERMES_WORKFLOWS
    )
    if not bounded_workflows_only:
        _issue(
            issues,
            "hermes.config.bounded_workflows_only",
            (
                "Hermes config must remain approval-first shadow with exactly the "
                f"bounded workflow set: {sorted(APPROVED_HERMES_WORKFLOWS)}"
            ),
        )

    forbidden_commands_present = REQUIRED_HERMES_FORBIDDEN_COMMANDS.issubset(forbidden_commands)
    if not forbidden_commands_present:
        missing = sorted(REQUIRED_HERMES_FORBIDDEN_COMMANDS - forbidden_commands)
        _issue(issues, "hermes.config.forbidden_commands", f"missing forbidden command families: {missing}")

    allowed_command_scope_ok = allowed_commands.issubset(
        {
            "filesystem.read_text",
            "filesystem.list_directory",
            "filesystem.ensure_directory",
            "filesystem.write_markdown",
        }
    )
    if not allowed_command_scope_ok:
        _issue(
            issues,
            "hermes.config.allowed_commands",
            "allowed command families must remain filesystem-only shadow commands",
        )

    connectors_disabled = (
        connector_policy.get("network_connectors") == "disabled"
        and connector_policy.get("gateway_inputs") == "disabled"
        and connector_policy.get("delivery_connectors") == "disabled"
        and connector_policy.get("local_repo_only") is True
    )
    if not connectors_disabled:
        _issue(issues, "hermes.config.connectors", "network, gateway, and delivery connectors must stay disabled")

    approval_default_deny = (
        approval_model.get("default") == "deny"
        and "any connector use" in (approval_model.get("escalation_required_for") or [])
        and "any attempt to mutate canonical state" in (approval_model.get("escalation_required_for") or [])
    )
    if not approval_default_deny:
        _issue(issues, "hermes.config.approval", "approval model must stay default-deny with connector/canonical escalation")

    promotion_blocked = (
        promotion.get("canonical_promotion") == "forbidden"
        and promotion.get("writeback_mode") == "draft-and-audit-only"
        and promotion.get("may_edit_canonical_state") is False
        and promotion.get("may_write_project_os") is False
        and promotion.get("may_write_governance_docs") is False
    )
    if not promotion_blocked:
        _issue(issues, "hermes.config.promotion", "Hermes writeback must remain draft/audit only")

    return {
        "status": config.get("status"),
        "mode": config.get("mode"),
        "approved_workflows": approved_workflows,
        "checks": {
            "bounded_workflows_only": bounded_workflows_only,
            "forbidden_commands_present": forbidden_commands_present,
            "allowed_command_scope_filesystem_only": allowed_command_scope_ok,
            "connectors_disabled": connectors_disabled,
            "approval_default_deny": approval_default_deny,
            "promotion_writeback_blocked": promotion_blocked,
        },
        "blocking_issues": issues,
    }


def _openclaw_capability_checks(capabilities: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    handles = list(capabilities.get("handles") or [])
    handle_text = " ".join(
        f"{item.get('task_type', '')} {item.get('priority', '')} {item.get('notes', '')}"
        for item in handles
        if isinstance(item, Mapping)
    ).lower()

    forbidden_terms_absent = all(term not in handle_text for term in FORBIDDEN_OPENCLAW_CAPABILITY_TERMS)
    if not forbidden_terms_absent:
        present = [term for term in FORBIDDEN_OPENCLAW_CAPABILITY_TERMS if term in handle_text]
        _issue(issues, "openclaw.capabilities.forbidden_terms", f"forbidden capability terms present: {present}")

    concurrency_bounded = capabilities.get("max_concurrent_tasks") == 3
    if not concurrency_bounded:
        _issue(issues, "openclaw.capabilities.concurrency", "max_concurrent_tasks must remain 3")

    priority_ceiling_bounded = capabilities.get("priority_ceiling") == "normal"
    if not priority_ceiling_bounded:
        _issue(issues, "openclaw.capabilities.priority", "priority ceiling must remain normal")

    bus_identity_ok = capabilities.get("runtime") == "openclaw" and capabilities.get("bus_name") == "OpenClaw"
    if not bus_identity_ok:
        _issue(issues, "openclaw.capabilities.identity", "runtime/bus identity must remain openclaw/OpenClaw")

    return {
        "runtime": capabilities.get("runtime"),
        "bus_name": capabilities.get("bus_name"),
        "handled_task_types": [
            item.get("task_type") for item in handles if isinstance(item, Mapping) and item.get("task_type")
        ],
        "max_concurrent_tasks": capabilities.get("max_concurrent_tasks"),
        "priority_ceiling": capabilities.get("priority_ceiling"),
        "checks": {
            "forbidden_capability_terms_absent": forbidden_terms_absent,
            "concurrency_bounded": concurrency_bounded,
            "priority_ceiling_bounded": priority_ceiling_bounded,
            "bus_identity_ok": bus_identity_ok,
        },
        "blocking_issues": issues,
    }


def _read_source(root: Path, relative_path: str, issues: list[str]) -> str:
    path = root / relative_path
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _issue(issues, "rpgl.source.read", f"could not read {relative_path}: {exc}")
        return ""


def _no_direct_provider_markers(source: str) -> tuple[bool, list[str]]:
    present = [marker for marker in DIRECT_PROVIDER_CALL_MARKERS if marker in source]
    return not present, present


def build_rpgl_adapter_consumption_report(vault_root_path: Path | None = None) -> dict[str, Any]:
    """Build a read-only source-level report proving Hermes/OpenClaw consume shared RPGL routing."""

    root = Path(vault_root_path) if vault_root_path is not None else vault_root()
    issues: list[str] = []

    execution_text = _read_source(root, "runtime/execution_adapters/execute.py", issues)
    hermes_text = _read_source(root, "runtime/workflows/hermes_review_execute.py", issues)
    openclaw_text = _read_source(root, "runtime/workflows/openclaw_watch.py", issues)

    shared_checks = {
        check_name: marker in execution_text
        for check_name, marker in SHARED_RPGL_EXECUTION_MARKERS.items()
    }
    fallback_denial_pos = execution_text.find("not is_task_allowed_for_strength")
    fallback_activation_pos = execution_text.find('event_type="provider.fallback_activated"')
    shared_checks["fallback_activation_after_capability_gate"] = (
        fallback_denial_pos >= 0
        and fallback_activation_pos >= 0
        and fallback_denial_pos < fallback_activation_pos
    )
    shared_checks["high_authority_queues_before_fallback"] = (
        "normalized_task_class in HIGH_AUTHORITY_TASK_CLASSES" in execution_text
        and "decision = route_task(" in execution_text
        and "RPGL queued high-authority task" in execution_text
    )

    for check_name, passed in shared_checks.items():
        if not passed:
            _issue(
                issues,
                f"rpgl.shared_execution_adapter.{check_name}",
                "runtime/execution_adapters/execute.py must keep provider routing behind RPGL",
            )

    hermes_no_direct, hermes_direct_markers = _no_direct_provider_markers(hermes_text)
    hermes_checks = {
        "imports_shared_execution_adapter": "from runtime.execution_adapters.execute import execute_synthesis" in hermes_text,
        "uses_hermes_adapter_identity": 'execution_adapter="hermes"' in hermes_text,
        "no_direct_provider_calls": hermes_no_direct,
    }
    for check_name, passed in hermes_checks.items():
        if not passed:
            _issue(
                issues,
                f"rpgl.hermes.{check_name}",
                "Hermes synthesis must consume the shared execution adapter and not direct provider calls",
            )

    openclaw_no_direct, openclaw_direct_markers = _no_direct_provider_markers(openclaw_text)
    openclaw_checks = {
        "no_direct_provider_calls": openclaw_no_direct,
        "bus_dispatch_only": "runtime.agent_bus.bus" in openclaw_text and "_TASK_DISPATCH" in openclaw_text,
        "does_not_import_shared_synthesis_adapter": "execute_synthesis" not in openclaw_text,
    }
    for check_name, passed in openclaw_checks.items():
        if not passed:
            _issue(
                issues,
                f"rpgl.openclaw.{check_name}",
                "OpenClaw watch must stay bus-dispatch-only and must not own provider fallback logic",
            )

    return {
        "ok": not issues,
        "scope": "hermes-openclaw-rpgl-consumption",
        "shared_execution_adapter": {
            "path": "runtime/execution_adapters/execute.py",
            "checks": shared_checks,
        },
        "hermes": {
            "path": "runtime/workflows/hermes_review_execute.py",
            "checks": hermes_checks,
            "direct_provider_markers_present": hermes_direct_markers,
        },
        "openclaw": {
            "path": "runtime/workflows/openclaw_watch.py",
            "checks": openclaw_checks,
            "direct_provider_markers_present": openclaw_direct_markers,
        },
        "blocking_issues": issues,
    }


def evaluate_runtime_adapter_governance(
    *,
    openclaw_manifest: Mapping[str, Any],
    hermes_manifest: Mapping[str, Any],
    hermes_config: Mapping[str, Any],
    openclaw_capabilities: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate already-loaded OpenClaw/Hermes governance mappings."""

    openclaw = _manifest_checks("openclaw", deepcopy(openclaw_manifest))
    hermes = _manifest_checks("hermes", deepcopy(hermes_manifest))
    hermes_config_report = _hermes_config_checks(deepcopy(hermes_config))
    openclaw_capability_report = _openclaw_capability_checks(deepcopy(openclaw_capabilities))

    cross_runtime_issues: list[str] = []
    same_ceiling = openclaw["trust_ceiling"] == "tier-2" and hermes["trust_ceiling"] == "tier-2"
    both_fail_closed = (
        openclaw["checks"]["promotion_blocked"]
        and hermes["checks"]["promotion_blocked"]
        and openclaw["checks"]["external_side_effects_blocked"]
        and hermes["checks"]["external_side_effects_blocked"]
    )
    both_bus_required = (
        openclaw["checks"]["coordination_bus_required"]
        and hermes["checks"]["coordination_bus_required"]
    )

    if not same_ceiling:
        _issue(cross_runtime_issues, "cross_runtime.ceiling", "OpenClaw and Hermes must share the tier-2 ceiling")
    if not both_fail_closed:
        _issue(
            cross_runtime_issues,
            "cross_runtime.fail_closed",
            "both runtimes must remain promotion/external-side-effect fail-closed",
        )
    if not both_bus_required:
        _issue(cross_runtime_issues, "cross_runtime.bus", "both runtimes must coordinate through runtime/agent_bus/")

    blocking_issues = (
        openclaw["blocking_issues"]
        + hermes["blocking_issues"]
        + hermes_config_report["blocking_issues"]
        + openclaw_capability_report["blocking_issues"]
        + cross_runtime_issues
    )

    return {
        "ok": not blocking_issues,
        "scope": "openclaw-hermes-runtime-adapter-governance",
        "adapters": {
            "openclaw": openclaw,
            "hermes": hermes,
        },
        "runtime_configs": {
            "hermes": hermes_config_report,
            "openclaw_capabilities": openclaw_capability_report,
        },
        "cross_runtime": {
            "same_tier2_ceiling": same_ceiling,
            "both_fail_closed_for_promotion_and_external_side_effects": both_fail_closed,
            "both_use_agent_bus_for_runtime_coordination": both_bus_required,
            "blocking_issues": cross_runtime_issues,
        },
        "blocking_issues": blocking_issues,
    }


def validate_runtime_adapter_governance(vault_root_path: Path | None = None) -> dict[str, Any]:
    """Load current repo config and validate OpenClaw/Hermes governance."""

    root = Path(vault_root_path) if vault_root_path is not None else vault_root()
    try:
        report = evaluate_runtime_adapter_governance(
            openclaw_manifest=_load_yaml(root / "runtime/policy/adapters/openclaw.yaml"),
            hermes_manifest=_load_yaml(root / "runtime/policy/adapters/hermes.yaml"),
            hermes_config=_load_yaml(root / ".chaseos/hermes_config.yaml"),
            openclaw_capabilities=_load_yaml(root / "runtime/openclaw/capabilities.yaml"),
        )
        rpgl_consumption = build_rpgl_adapter_consumption_report(root)
        report["rpgl_consumption"] = rpgl_consumption
        report["blocking_issues"] = report["blocking_issues"] + rpgl_consumption["blocking_issues"]
        report["ok"] = not report["blocking_issues"]
        return report
    except Exception as exc:  # pragma: no cover - defensive CLI/report guard
        return {
            "ok": False,
            "scope": "openclaw-hermes-runtime-adapter-governance",
            "adapters": {},
            "runtime_configs": {},
            "cross_runtime": {},
            "rpgl_consumption": {},
            "blocking_issues": [f"load_error: {exc}"],
        }


def format_runtime_adapter_governance(report: Mapping[str, Any]) -> str:
    """Format the read-only runtime adapter governance report for CLI output."""

    lines = [
        "ChaseOS Runtime Adapter Governance",
        f"Status: {'ok' if report.get('ok') else 'blocked'}",
        f"Scope: {report.get('scope', 'unknown')}",
        "",
        "Adapters:",
    ]
    adapters = report.get("adapters") or {}
    for adapter_id in ("openclaw", "hermes"):
        adapter = adapters.get(adapter_id) or {}
        lines.append(
            f"- {adapter_id}: status={adapter.get('status', 'unknown')} "
            f"trust_ceiling={adapter.get('trust_ceiling', 'unknown')}"
        )

    rpgl = report.get("rpgl_consumption") or {}
    shared_checks = ((rpgl.get("shared_execution_adapter") or {}).get("checks") or {})
    hermes_checks = ((rpgl.get("hermes") or {}).get("checks") or {})
    openclaw_checks = ((rpgl.get("openclaw") or {}).get("checks") or {})
    lines.extend([
        "",
        "RPGL Consumption:",
        f"- shared execution adapter: {'ok' if shared_checks and all(shared_checks.values()) else 'blocked'}",
        f"- Hermes consumes shared adapter: {hermes_checks.get('imports_shared_execution_adapter', 'unknown')}",
        f"- Hermes direct provider calls absent: {hermes_checks.get('no_direct_provider_calls', 'unknown')}",
        f"- OpenClaw direct provider calls absent: {openclaw_checks.get('no_direct_provider_calls', 'unknown')}",
        f"- OpenClaw bus-dispatch-only: {openclaw_checks.get('bus_dispatch_only', 'unknown')}",
    ])

    issues = list(report.get("blocking_issues") or [])
    lines.append("")
    if issues:
        lines.append("Blocking Issues:")
        lines.extend(f"- {issue}" for issue in issues)
    else:
        lines.append("Blocking Issues: none")
    return "\n".join(lines)


def main() -> int:
    report = validate_runtime_adapter_governance()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
