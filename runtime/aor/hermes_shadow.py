"""
hermes_shadow.py - Hermes shadow workflow handlers for AOR.

This module implements the narrowest possible Hermes runtime proof:
`hermes_operator_today_shadow`.

The workflow is intentionally fail-closed:
  - reads only manifest-declared files that also exist in .chaseos/hermes_config.yaml
  - writes only markdown artifacts inside declared draft/audit locations
  - never mutates canonical state
  - never uses shell commands or connectors
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(".chaseos/hermes_config.yaml")


def _coerce_yaml_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _next_yaml_content_index(lines: list[str], start: int) -> int:
    j = start
    while j < len(lines):
        s = lines[j].strip()
        if s and not s.startswith("#") and s != "---":
            return j
        j += 1
    return j


def _parse_yaml_block(lines: list[str], start: int, indent: int) -> tuple[Any, int]:
    i = _next_yaml_content_index(lines, start)
    if i >= len(lines):
        return {}, i

    raw = lines[i].rstrip()
    current_indent = len(raw) - len(raw.lstrip(" "))
    stripped = raw.strip()
    if current_indent < indent:
        if not (stripped.startswith("- ") and current_indent == max(0, indent - 2)):
            return {}, i

    if stripped.startswith("- ") and current_indent in {indent, max(0, indent - 2)}:
        list_indent = current_indent
        items: list[Any] = []
        while i < len(lines):
            i = _next_yaml_content_index(lines, i)
            if i >= len(lines):
                break
            raw = lines[i].rstrip()
            current_indent = len(raw) - len(raw.lstrip(" "))
            stripped = raw.strip()
            if current_indent != list_indent or not stripped.startswith("- "):
                break
            items.append(_coerce_yaml_scalar(stripped[2:].strip()))
            i += 1
        return items, i

    mapping: dict[str, Any] = {}
    while i < len(lines):
        i = _next_yaml_content_index(lines, i)
        if i >= len(lines):
            break
        raw = lines[i].rstrip()
        current_indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if current_indent < indent:
            break
        if current_indent != indent or ":" not in stripped:
            raise ValueError(f"Unsupported YAML syntax on line {i + 1}: {raw}")
        key, rest = stripped.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        if rest:
            mapping[key] = _coerce_yaml_scalar(rest)
            i += 1
        else:
            child, next_i = _parse_yaml_block(lines, i + 1, indent + 2)
            mapping[key] = child
            i = next_i
    return mapping, i


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data, _ = _parse_yaml_block(text.splitlines(), 0, 0)
    if not isinstance(data, dict):
        raise ValueError(f"expected YAML mapping at {path}")
    return data


def _normalise(path_value: str) -> str:
    return path_value.replace("\\", "/")


def _path_is_allowed(rel_path: str, allowlist: list[str]) -> bool:
    rel_norm = _normalise(rel_path)
    for allowed in allowlist:
        allowed_norm = _normalise(allowed)
        if allowed_norm.endswith("/"):
            if rel_norm.startswith(allowed_norm):
                return True
        elif rel_norm == allowed_norm:
            return True
    return False


def _assert_allowed(rel_path: str, allowlist: list[str], kind: str) -> None:
    if not _path_is_allowed(rel_path, allowlist):
        raise ValueError(f"{kind} path outside allowlist: {rel_path}")


def _load_declared_context(
    manifest: dict[str, Any],
    vault_root: Path,
    config: dict[str, Any],
) -> dict[str, str]:
    reads = manifest.get("required_reads", [])
    if not isinstance(reads, list) or not reads:
        raise ValueError("manifest required_reads must be a non-empty list")

    allowlist = config.get("readable_path_allowlist", [])
    context: dict[str, str] = {}
    for rel_path in reads:
        _assert_allowed(rel_path, allowlist, "read")
        full_path = vault_root / rel_path
        if not full_path.exists():
            raise ValueError(f"declared read missing on disk: {rel_path}")
        context[rel_path] = full_path.read_text(encoding="utf-8")
    return context


def _extract_repo_truth(context: dict[str, str]) -> dict[str, str]:
    now_text = context["00_HOME/Now.md"]
    permission_log = context[
        "07_LOGS/Build-Logs/2026-04-09-ChaseOS-hermes-permission-matrix-closure.md"
    ]
    integration_log = context[
        "07_LOGS/Build-Logs/2026-04-08-ChaseOS-hermes-integration-binding-pass.md"
    ]

    phase_line = next(
        (
            line.strip().lstrip("#").strip()
            for line in now_text.splitlines()
            if "Phase 9" in line
        ),
        "Phase 9 status line not found.",
    )
    closure_present = (
        "Open Loops After This Pass" in permission_log
        and "None from the Hermes planning/binding chain." in permission_log
    )
    docs_only_binding = "docs-only" in integration_log.lower()

    return {
        "phase_status": phase_line,
        "permission_closure_present": "yes" if closure_present else "no",
        "binding_pass_was_docs_only": "yes" if docs_only_binding else "no",
        "hermes_scope_boundary": (
            "Hermes remains shadow-only in this repo: one approved workflow, draft/audit writes only, "
            "with connectors, shell execution, canonical promotion, and broader workflow enablement blocked."
        ),
    }


def _build_paths(run_date: str, workflow_id: str) -> dict[str, str]:
    return {
        "draft_operator_brief_path": (
            f"07_LOGS/Operator-Briefs/_drafts/{run_date}-{workflow_id}.md"
        ),
        "agent_activity_log_path": (
            f"07_LOGS/Agent-Activity/{run_date}-hermes-{workflow_id}.md"
        ),
        "build_log_path": (
            f"07_LOGS/Build-Logs/{run_date}-ChaseOS-hermes-runtime-activation-pass.md"
        ),
        "archive_note_path": (
            f"99_ARCHIVE/Documentation-History/{run_date}_hermes-runtime-activation-pass.md"
        ),
    }


def run_hermes_operator_today_shadow(
    *,
    inputs: dict[str, Any],
    vault_root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    config_path = vault_root / CONFIG_PATH
    if not config_path.exists():
        raise ValueError(f"missing Hermes runtime config: {CONFIG_PATH}")

    config = _load_yaml(config_path)
    approval_model = config.get("approval_model", {})
    connector_policy = config.get("connector_policy", {})
    promotion_rules = config.get("promotion_writeback_rules", {})

    if approval_model.get("default") != "deny":
        raise ValueError("Hermes runtime config is not fail-closed")
    if connector_policy.get("network_connectors") != "disabled":
        raise ValueError("network connectors must be disabled for shadow workflow")
    if promotion_rules.get("canonical_promotion") != "forbidden":
        raise ValueError("canonical promotion must remain forbidden")

    approved_workflows = config.get("approved_workflows", [])
    workflow_id = manifest.get("id", "")
    if workflow_id not in approved_workflows:
        raise ValueError(f"workflow not approved in Hermes config: {workflow_id}")

    context = _load_declared_context(manifest, vault_root, config)
    repo_truth = _extract_repo_truth(context)

    run_date = str(inputs.get("date") or datetime.now(timezone.utc).date().isoformat())
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    operator_focus = str(inputs.get("operator_focus") or "Hermes runtime activation pass")
    paths = _build_paths(run_date, workflow_id)

    reads_list = sorted(context.keys())
    forbidden_paths = config.get("forbidden_path_list", [])
    allowed_commands = config.get("allowed_command_families", [])
    forbidden_commands = config.get("forbidden_command_families", [])

    draft_operator_brief = f"""---
type: operator-brief-draft
runtime: hermes
workflow_id: {workflow_id}
date: {run_date}
status: draft
mode: shadow
---

# Draft Operator Brief - Hermes Shadow

## Operator Focus

- {operator_focus}

## Repo-Truth Snapshot

- {repo_truth["phase_status"]}
- Hermes permission-matrix closure present locally: {repo_truth["permission_closure_present"]}
- Hermes 2026-04-08 integration pass was docs-only: {repo_truth["binding_pass_was_docs_only"]}
- {repo_truth["hermes_scope_boundary"]}

## Declared Context Read

""" + "\n".join(f"- `{path}`" for path in reads_list) + """

## Shadow Guardrails

- Draft only. No canonical truth mutated.
- No promotion to `02_KNOWLEDGE/`.
- No edits to `00_HOME/Now.md`, `01_PROJECTS/`, or governance docs.
- No networked connectors, shell commands, or multi-repo access.

## Suggested Next Step

- Review this draft output as proof that Hermes can orient and write audit artifacts inside a fail-closed boundary before any broader handler activation is considered.
"""

    activity_log = f"""---
type: agent-activity
runtime: hermes
workflow_id: {workflow_id}
date: {run_date}
status: complete
mode: shadow
---

# Agent Activity Log - Hermes Shadow

## Run

- Timestamp UTC: `{timestamp}`
- Workflow: `{workflow_id}`
- Approval model: `default={approval_model.get("default")}`
- Connector policy: `network_connectors={connector_policy.get("network_connectors")}`

## Reads

""" + "\n".join(f"- `{path}`" for path in reads_list) + """

## Writes

""" + "\n".join(f"- `{path}`" for path in paths.values()) + """

## Boundary Enforcement

- Allowed command families: """ + ", ".join(f"`{item}`" for item in allowed_commands) + """
- Forbidden command families: """ + ", ".join(f"`{item}`" for item in forbidden_commands) + """
- Forbidden path list: """ + ", ".join(f"`{item}`" for item in forbidden_paths) + """

## Result

- Completed in shadow mode with draft/audit outputs only.
- No canonical writeback performed.
- No connectors invoked.
"""

    build_log = f"""---
type: build-log
date: {run_date}
project: ChaseOS
descriptor: hermes-runtime-activation-pass
phase: 9
pass: Hermes Runtime Activation
status: complete
---

# Build Log - {run_date} - ChaseOS Hermes Runtime Activation Pass

## Pass Summary

Bounded runtime activation pass for Hermes completed in shadow mode.
This pass did not broaden Hermes into a general runtime.

## Runtime Boundary Adopted

- Reads only the manifest-declared Hermes activation context.
- Writes only to `07_LOGS/Agent-Activity/`, `07_LOGS/Build-Logs/`, `07_LOGS/Operator-Briefs/_drafts/`, and `99_ARCHIVE/Documentation-History/`.
- Connectors disabled.
- Shell command execution disabled.
- Canonical promotion forbidden.

## Workflow Proof

- Workflow: `{workflow_id}`
- Draft operator brief: `{paths["draft_operator_brief_path"]}`
- Agent activity log: `{paths["agent_activity_log_path"]}`
- Archive note: `{paths["archive_note_path"]}`

## Repo-Truth Snapshot

- {repo_truth["phase_status"]}
- Hermes permission closure present locally: {repo_truth["permission_closure_present"]}
- {repo_truth["hermes_scope_boundary"]}

## Non-Goals Honored

- No ambient vault access
- No canonical state mutation
- No networked connectors
- No claim of general production readiness
"""

    archive_note = f"""---
type: archive-note
date: {run_date}
project: ChaseOS
descriptor: hermes-runtime-activation-pass
status: archived
---

# Hermes Runtime Activation Pass - Historical Note

This note records the first bounded Hermes runtime activation proof in ChaseOS.
The activation was intentionally narrow:

- single workflow: `{workflow_id}`
- shadow mode only
- draft and audit outputs only
- no canonical writes
- no connectors
- no multi-repo access

The repo state at activation time reflected a narrow Hermes truth: AOR handler
dispatch is live for approved non-Hermes workflows, while Hermes itself remains
limited to this single shadow workflow. This pass therefore proves a safe
adapter boundary rather than a broad Hermes rollout.

## Outputs

""" + "\n".join(f"- `{path}`" for path in paths.values()) + """

## Sources Consulted

""" + "\n".join(f"- `{path}`" for path in reads_list) + """
"""

    writebacks = [
        {
            "path": paths["draft_operator_brief_path"],
            "content": draft_operator_brief,
            "content_type": "text/markdown",
        },
        {
            "path": paths["agent_activity_log_path"],
            "content": activity_log,
            "content_type": "text/markdown",
        },
        {
            "path": paths["build_log_path"],
            "content": build_log,
            "content_type": "text/markdown",
        },
        {
            "path": paths["archive_note_path"],
            "content": archive_note,
            "content_type": "text/markdown",
        },
    ]

    for item in writebacks:
        _assert_allowed(item["path"], config.get("writable_path_allowlist", []), "write")

    return {
        "handler_status": "completed",
        "workflow_id": workflow_id,
        "mode": "shadow",
        "repo_truth": repo_truth,
        "read_files": reads_list,
        "created_files": paths,
        "writebacks": writebacks,
    }
