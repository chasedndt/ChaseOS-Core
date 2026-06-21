"""
bus.py — ChaseOS Agent Coordination Bus (Public API)
=====================================================

This module is the single entry point for all agent bus operations. It exposes
the same public API regardless of which storage backend is configured. Callers
(handlers, workflows, tests, CLI) import from here and never touch the backend
directly.

WHAT THIS FILE DOES
-------------------
1. Validates inputs (runtime names, intent, priority, priority ceiling)
2. Generates IDs and timestamps
3. Delegates all storage operations to get_backend(vault_root)
4. Provides orchestration functions (watch_once, run_watch_loop) that compose
   multiple storage operations into higher-level behaviors

WHAT THIS FILE DOES NOT DO
---------------------------
- Touch SQLite directly (that is SQLiteBackend's job)
- Know the difference between local and server mode (that is backend_loader's job)
- Generate output or write files (that is the handler's job)

BACKWARD COMPATIBILITY
----------------------
All public function signatures are identical to the pre-abstraction bus.py.
No caller needs to change. The only internal change is that storage operations
now route through get_backend(vault_root) instead of direct SQLite calls.

PUBLIC API
----------
  init_db(vault_root)          — initialize storage for vault_root
  db_path(vault_root)          — path to SQLite file (local mode only)
  create_task(vault_root, ...) — create a new coordination task
  list_tasks(vault_root, ...)  — list tasks with optional filters
  list_heartbeats(vault_root)  — list all runtime heartbeat records
  claim_task(vault_root, ...)  — atomically claim an open task
  update_task_status(...)      — update task status + append event
  upsert_heartbeat(...)        — upsert runtime liveness record
  mark_stale_tasks(...)        — expire tasks from stale runtimes
  reclaim_task(...)            — re-open a task from a stale runtime
  watch_once(...)              — single watch cycle (expire + claim + heartbeat)
  run_watch_loop(...)          — polling watch loop
  get_known_runtimes(...)      — set of valid runtime bus_names
  get_bus_mode(vault_root)     — return current backend mode string ("local" | "server")
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backend_loader import get_backend

# Built-in fallback runtime set for repo-local bootstrap defaults.
# Live validation should use get_known_runtimes(); the SQLite and JSON schemas are runtime-generic.
# Keep this as a conservative seed set, not a lifecycle/reachability list: a runtime
# can be registered even when its current process/session is offline.
RUNTIMES = {"Archon", "Codex", "Hermes", "OpenClaw"}
CONTROL_SURFACE_SENDERS = {"Operator"}
ACTIVE_TASK_STATES = {"open", "claimed", "in_progress", "blocked", "review"}

# Priority rank for ceiling enforcement — lower = less urgent; critical is highest.
_PRIORITY_RANKS: dict[str, int] = {"low": 0, "normal": 1, "high": 2, "critical": 3}


# ── Utility ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _repo_root_from_module() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_discord_bindings(vault_root: Path | None) -> dict[str, Any]:
    if vault_root is None:
        return {}
    path = Path(vault_root) / ".chaseos" / "discord_instance_bindings.yaml"
    if not path.exists():
        return {}

    try:
        import yaml as _yaml  # type: ignore
    except Exception:
        _yaml = None

    text = path.read_text(encoding="utf-8")
    if _yaml is not None:
        try:
            data = _yaml.safe_load(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass

    from runtime.lifecycle.health_cli import _parse_simple_yaml  # lazy import; bounded fallback already used in repo

    parsed = _parse_simple_yaml(text)
    return parsed if isinstance(parsed, dict) else {}


def _find_discord_channel_binding(bindings: dict[str, Any], source_channel_id: str) -> tuple[str | None, dict[str, Any] | None]:
    for section_name in ("primary_channels", "supplemental_channels"):
        section = bindings.get(section_name) or {}
        if not isinstance(section, dict):
            continue
        for binding_name, binding in section.items():
            if not isinstance(binding, dict):
                continue
            if str(binding.get("id") or "").strip() == source_channel_id:
                return str(binding_name), binding
    return None, None


def _normalize_ingress_context(
    ingress_context: dict[str, Any] | None,
    *,
    recipient: str,
    request: str,
    expected_output: str,
) -> tuple[dict[str, Any] | None, str | None]:
    if ingress_context is None:
        return None, None

    normalized = {str(key): value for key, value in ingress_context.items() if value not in (None, "")}
    source_platform = str(normalized.get("source_platform") or "").strip().lower()

    if source_platform == "discord":
        source_channel_id = str(normalized.get("source_channel_id") or "").strip()
        if not source_channel_id:
            return None, "Discord ingress_context requires source_channel_id."

        source_thread_id = str(normalized.get("source_thread_id") or "").strip()
        if not normalized.get("conversation_key"):
            parts = ["discord", source_channel_id]
            if source_thread_id:
                parts.append(source_thread_id)
            normalized["conversation_key"] = ":".join(parts)

        if not normalized.get("control_plane_route") and normalized.get("conversation_key"):
            normalized["control_plane_route"] = normalized["conversation_key"]

    work_fingerprint = None
    if source_platform and str(normalized.get("origin_message_id") or "").strip():
        work_fingerprint = f"{source_platform}:{recipient}:{str(normalized['origin_message_id']).strip()}"
    elif normalized.get("conversation_key"):
        request_basis = " ".join(request.split())
        output_basis = " ".join(expected_output.split())
        digest = hashlib.sha256(f"{recipient}|{request_basis}|{output_basis}".encode("utf-8")).hexdigest()[:16]
        work_fingerprint = f"{normalized['conversation_key']}:{digest}"

    return normalized or None, work_fingerprint


def _normalize_execution_constraints(
    execution_constraints: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize optional task-level execution constraints.

    These constraints describe the requested execution envelope for the
    recipient runtime. They do not grant authority; recipients may only narrow
    their behavior from this metadata.
    """
    if execution_constraints is None:
        return None, None
    if not isinstance(execution_constraints, dict):
        return None, "execution_constraints must be an object"

    allowed_keys = {
        "allow_shell_commands",
        "allow_live_subprocess",
        "allowed_write_paths",
        "write_policy",
    }
    unknown_keys = sorted(str(key) for key in execution_constraints if key not in allowed_keys)
    if unknown_keys:
        return None, f"execution_constraints contains unsupported keys: {unknown_keys}"

    normalized: dict[str, Any] = {}
    for key in ("allow_shell_commands", "allow_live_subprocess"):
        if key in execution_constraints:
            value = execution_constraints[key]
            if not isinstance(value, bool):
                return None, f"execution_constraints.{key} must be a boolean"
            normalized[key] = value

    if "allowed_write_paths" in execution_constraints:
        value = execution_constraints["allowed_write_paths"]
        if not isinstance(value, list):
            return None, "execution_constraints.allowed_write_paths must be a list of strings"
        paths: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                return None, "execution_constraints.allowed_write_paths must contain only non-empty strings"
            paths.append(item.strip())
        normalized["allowed_write_paths"] = paths

    if "write_policy" in execution_constraints:
        value = execution_constraints["write_policy"]
        if value not in {"adapter-default", "declared-paths", "none"}:
            return None, "execution_constraints.write_policy must be one of: adapter-default, declared-paths, none"
        if value == "none" and normalized.get("allowed_write_paths"):
            return None, "execution_constraints.write_policy=none cannot include allowed_write_paths"
        normalized["write_policy"] = value
        if value == "none" and "allowed_write_paths" not in normalized:
            normalized["allowed_write_paths"] = []

    return normalized or None, None


def _coerce_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    return value



def _parse_simple_yaml(text: str) -> dict[str, Any]:
    lines = text.splitlines()

    def parse_mapping(start_index: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        i = start_index
        while i < len(lines):
            line = lines[i].rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            current_indent = len(line) - len(line.lstrip(" "))
            if current_indent < indent:
                break
            if current_indent > indent:
                i += 1
                continue
            if ":" not in stripped or stripped.startswith("- "):
                i += 1
                continue

            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value != "":
                result[key] = _coerce_scalar(value)
                i += 1
                continue

            j = i + 1
            while j < len(lines):
                candidate = lines[j].rstrip()
                candidate_stripped = candidate.strip()
                if not candidate_stripped or candidate_stripped.startswith("#"):
                    j += 1
                    continue
                break

            if j >= len(lines):
                result[key] = {}
                i = j
                continue

            next_line = lines[j].rstrip()
            next_stripped = next_line.strip()
            next_indent = len(next_line) - len(next_line.lstrip(" "))
            if next_indent <= current_indent:
                result[key] = {}
                i = j
                continue

            if next_stripped.startswith("- "):
                items: list[Any] = []
                i = j
                while i < len(lines):
                    item_line = lines[i].rstrip()
                    item_stripped = item_line.strip()
                    if not item_stripped or item_stripped.startswith("#"):
                        i += 1
                        continue
                    item_indent = len(item_line) - len(item_line.lstrip(" "))
                    if item_indent < next_indent:
                        break
                    if item_indent == next_indent and item_stripped.startswith("- "):
                        items.append(_coerce_scalar(item_stripped[2:].strip()))
                    i += 1
                result[key] = items
                continue

            nested, next_index = parse_mapping(j, next_indent)
            result[key] = nested
            i = next_index
        return result, i

    parsed, _ = parse_mapping(0, 0)
    return parsed



def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None  # type: ignore
    if yaml is not None:
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    return _parse_simple_yaml(text)



def _load_discord_bindings(vault_root: Path | None) -> dict[str, Any]:
    root = Path(vault_root) if vault_root else _repo_root_from_module()
    return _load_yaml(root / ".chaseos" / "discord_instance_bindings.yaml")



def _iter_discord_channels(bindings: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for section_name in ("primary_channels", "supplemental_channels"):
        section = bindings.get(section_name)
        if not isinstance(section, dict):
            continue
        for binding_key, payload in section.items():
            if isinstance(payload, dict):
                rows.append((binding_key, payload))
    return rows



def _resolve_discord_channel_binding(
    vault_root: Path | None,
    *,
    source_channel_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    bindings = _load_discord_bindings(vault_root)
    for binding_key, payload in _iter_discord_channels(bindings):
        if str(payload.get("id") or "").strip() == source_channel_id:
            return binding_key, payload
    return None, None



def translate_discord_control_plane_request(
    vault_root: Path | None,
    *,
    recipient: str,
    request: str,
    expected_output: str,
    source_channel_id: str,
    source_thread_id: str | None = None,
    source_channel_class: str | None = None,
    origin_message_id: str | None = None,
    control_plane_route: str | None = None,
    work_fingerprint: str | None = None,
    intent: str = "TASK",
    priority: str = "normal",
    notes: str | None = None,
    coordination_sensitive: bool = False,
    now_iso: str | None = None,
) -> dict[str, Any]:
    source_channel_id = str(source_channel_id or "").strip()
    if not source_channel_id:
        return {
            "translated": False,
            "classification": "invalid_ingress",
            "reason": "Discord control-plane translation requires source_channel_id.",
        }

    binding_key, binding = _resolve_discord_channel_binding(vault_root, source_channel_id=source_channel_id)
    if binding is None:
        return {
            "translated": False,
            "classification": "unbound_channel",
            "reason": f"No bound Discord channel matches source_channel_id '{source_channel_id}'.",
        }

    if binding.get("bound") is False:
        return {
            "translated": False,
            "classification": "unbound_channel",
            "reason": f"Discord channel '{binding_key}' is declared but not bound.",
        }

    bound_channel_class = str(binding.get("channel_class") or "").strip()
    if source_channel_class and source_channel_class != bound_channel_class:
        return {
            "translated": False,
            "classification": "channel_class_mismatch",
            "reason": (
                f"Discord channel '{binding_key}' resolves to channel_class '{bound_channel_class}', "
                f"not '{source_channel_class}'."
            ),
        }

    effective_channel_class = source_channel_class or bound_channel_class
    interactive_runtimes = {
        str(item).strip().lower()
        for item in (binding.get("interactive_eligible_runtimes") or [])
        if str(item).strip()
    }

    if not coordination_sensitive:
        return {
            "translated": False,
            "classification": "advisory_only",
            "reason": (
                f"Discord channel_class '{effective_channel_class}' remains advisory until the request "
                f"is explicitly classified coordination-sensitive."
            ),
            "channel_binding_name": str(binding.get("name") or binding_key),
            "channel_class": effective_channel_class,
        }

    if effective_channel_class in {"approvals", "audit-writeback", "alerts", "debug", "docs-archive"}:
        return {
            "translated": False,
            "classification": "forbidden_channel_class",
            "reason": (
                f"Discord channel_class '{effective_channel_class}' is not an interactive coordination ingress surface."
            ),
            "channel_binding_name": str(binding.get("name") or binding_key),
            "channel_class": effective_channel_class,
        }

    if effective_channel_class == "runtime-chat" and recipient.lower() not in interactive_runtimes:
        return {
            "translated": False,
            "classification": "runtime_not_interactive_in_channel",
            "reason": (
                f"Runtime '{recipient}' is not interactive-eligible for Discord channel '{binding.get('name') or binding_key}'."
            ),
            "channel_binding_name": str(binding.get("name") or binding_key),
            "channel_class": effective_channel_class,
        }

    selected_feature: dict[str, Any] | None = None
    try:
        from runtime.studio.feature_usage_ledger import route_natural_language_feature

        selected_feature = route_natural_language_feature(request)
    except Exception:  # noqa: BLE001 - feature routing must never block bus translation
        selected_feature = None

    ingress_context = {
        "source_platform": "discord",
        "source_channel_id": source_channel_id,
        "source_channel_class": effective_channel_class,
        "origin_message_id": origin_message_id,
        "control_plane_route": control_plane_route,
        "channel_binding_key": binding_key,
        "channel_binding_name": str(binding.get("name") or binding_key),
        "translation_source": "discord-control-plane",
    }
    if selected_feature:
        ingress_context.update({
            "feature_id": selected_feature.get("feature_id", ""),
            "feature_name": selected_feature.get("feature_name", ""),
            "skill_id": selected_feature.get("skill_id", ""),
            "action_id": selected_feature.get("action_id", ""),
            "review_surface": selected_feature.get("review_surface", ""),
        })
        feature_notes = "\n".join([
            f"feature_id: {selected_feature.get('feature_id', '')}",
            f"feature_name: {selected_feature.get('feature_name', '')}",
            f"skill_id: {selected_feature.get('skill_id', '')}",
            f"action_id: {selected_feature.get('action_id', '')}",
            f"review_surface: {selected_feature.get('review_surface', '')}",
            f"visual_qa_required: {json.dumps(bool(selected_feature.get('visual_qa_required')))}",
        ])
        notes = f"{notes}\n{feature_notes}" if notes else feature_notes
    if source_thread_id:
        ingress_context["source_thread_id"] = source_thread_id

    result = create_task(
        vault_root,
        sender="Operator",
        recipient=recipient,
        intent=intent,
        priority=priority,
        request=request,
        expected_output=expected_output,
        notes=notes,
        now_iso=now_iso,
        ingress_context=ingress_context,
        work_fingerprint=work_fingerprint,
        allow_external_sender=True,
    )
    feature_invocation: dict[str, Any] | None = None
    if result.get("created") and selected_feature:
        try:
            from runtime.studio.feature_usage_ledger import append_feature_usage_record

            thread_key = source_thread_id or source_channel_id
            ledger_result = append_feature_usage_record(
                vault_root or _repo_root_from_module(),
                session_id=f"discord-{source_channel_id}",
                thread_id=str(thread_key or ""),
                run_id=str(result.get("run_id") or result.get("task_id") or ""),
                task_id=str(result.get("task_id") or ""),
                runtime_id=str(recipient or ""),
                message_id=str(origin_message_id or ""),
                selected_feature=selected_feature,
                action="discord_control_plane.natural_language_route",
                source_surface="discord_control_plane",
            )
            if ledger_result.get("ok"):
                feature_invocation = dict(ledger_result["record"])
                feature_invocation["ledger_record_path"] = ledger_result["ledger_record_path"]
                feature_invocation["proof_artifact_path"] = ledger_result["proof_artifact_path"]
                feature_invocation["product_events"] = ledger_result.get("product_events", [])
        except Exception as exc:  # noqa: BLE001 - visibility ledger must not block bus task creation
            feature_invocation = {
                "feature_id": selected_feature.get("feature_id", ""),
                "skill_id": selected_feature.get("skill_id", ""),
                "action_id": selected_feature.get("action_id", ""),
                "ledger_error": f"feature_usage_ledger_failed:{str(exc)[:160]}",
            }
    return {
        "translated": bool(result.get("created")),
        "classification": "coordination_sensitive",
        "channel_binding_name": str(binding.get("name") or binding_key),
        "channel_class": effective_channel_class,
        "feature_invocation": feature_invocation or selected_feature or {},
        **result,
    }


def get_known_runtimes(vault_root: Path | None = None) -> set[str]:
    """Return valid runtime bus_names from capability manifests, with fallback."""
    if vault_root is not None:
        try:
            from runtime.agent_bus.capabilities import load_all_capabilities
            caps = load_all_capabilities(vault_root)
            if caps:
                return {c.bus_name for c in caps.values()}
        except Exception:
            pass
    return RUNTIMES


def _resolve_runtime_identity_or_error(
    vault_root: Path | None,
    name: str,
    *,
    allow_external_sender: bool = False,
) -> tuple[str | None, str | None, str | None]:
    """Return (canonical_bus_name, instance_hint, error)."""
    if allow_external_sender and name in CONTROL_SURFACE_SENDERS:
        return name, None, None
    known = get_known_runtimes(vault_root)
    if vault_root is None:
        if name in known:
            return name, None, None
        return None, None, f"Unknown runtime: '{name}'. Known: {sorted(known)}"
    try:
        from runtime.agent_bus.capabilities import CapabilityError, resolve_runtime_identity
        identity = resolve_runtime_identity(vault_root, name)
        return identity.bus_name, identity.runtime_instance_id_hint, None
    except ValueError:
        if name in known:
            return name, None, None
        return None, None, f"Unknown runtime identity: '{name}'. Known: {sorted(known)}"
    except CapabilityError as exc:
        return None, None, f"Runtime capability resolution failed: {exc}"


# ── Storage init (backward compat) ────────────────────────────────────────────

def init_db(vault_root: Path | None = None) -> Path:
    """Initialize bus storage for vault_root. Returns SQLite path (local mode).

    Calling this explicitly is not required — get_backend() calls init() at
    instantiation time. This function exists for backward compatibility with
    test helpers that call init_db() to set up a fresh vault.
    """
    root = Path(vault_root) if vault_root else _repo_root_from_module()
    backend = get_backend(root)
    backend.init()
    # Return SQLite path for local mode (test compat). Server mode: return None.
    try:
        from .backends.sqlite_backend import SQLiteBackend
        if isinstance(backend, SQLiteBackend):
            return backend.db_path
    except Exception:
        pass
    return root / "runtime" / "agent_bus" / "agent_bus.sqlite"


def db_path(vault_root: Path | None = None) -> Path:
    """Return the SQLite database path (local mode only).

    Used by tests for direct SQLite introspection (reading event tables, etc.).
    In server mode this returns the local path where SQLite WOULD live, even
    though the server backend does not use it — this is intentional to avoid
    breaking test helpers that use db_path() for introspection.
    """
    root = Path(vault_root) if vault_root else _repo_root_from_module()
    return root / "runtime" / "agent_bus" / "agent_bus.sqlite"


# ── Task operations ───────────────────────────────────────────────────────────

def create_task(
    vault_root: Path | None,
    *,
    task_id: str | None = None,
    sender: str,
    recipient: str,
    intent: str = "TASK",
    priority: str = "normal",
    request: str,
    expected_output: str,
    depends_on: list[str] | None = None,
    notes: str | None = None,
    expires_at: str | None = None,
    now_iso: str | None = None,
    ingress_context: dict[str, Any] | None = None,
    work_fingerprint: str | None = None,
    execution_constraints: dict[str, Any] | None = None,
    allow_external_sender: bool = False,
) -> dict[str, Any]:
    """Create a new task packet on the coordination bus.

    Validates sender, recipient, intent, priority, and priority ceiling before
    delegating to the backend. ID and timestamp generation happens here so
    backends receive fully-formed data.

    Returns {'created': True, 'task_id': ...} on success.
    Returns {'created': False, 'reason': ...} on any validation or storage failure.
    """
    requested_sender = sender
    requested_recipient = recipient
    sender, _sender_instance_hint, sender_error = _resolve_runtime_identity_or_error(
        vault_root,
        requested_sender,
        allow_external_sender=allow_external_sender,
    )
    if sender_error:
        return {"created": False, "reason": f"Unknown sender runtime: '{requested_sender}'. {sender_error}"}
    recipient, _recipient_instance_hint, recipient_error = _resolve_runtime_identity_or_error(vault_root, requested_recipient)
    if recipient_error:
        return {"created": False, "reason": recipient_error}

    valid_intents = {"TASK", "RESULT", "BLOCKER", "REVIEW", "QUESTION", "NOTICE"}
    if intent not in valid_intents:
        return {"created": False, "reason": f"Invalid intent '{intent}'. Must be one of {sorted(valid_intents)}"}

    valid_priorities = {"low", "normal", "high", "critical"}
    if priority not in valid_priorities:
        return {"created": False, "reason": f"Invalid priority '{priority}'. Must be one of {sorted(valid_priorities)}"}

    # L-3 security fix: cap notes field to prevent memory amplification
    _MAX_NOTES_CHARS = 4096
    if notes and len(notes) > _MAX_NOTES_CHARS:
        return {"created": False, "reason": f"notes field exceeds {_MAX_NOTES_CHARS} characters"}

    # Priority ceiling check — fail-open if capabilities unavailable.
    if vault_root is not None:
        try:
            from runtime.agent_bus.capabilities import load_all_capabilities
            all_caps = load_all_capabilities(vault_root)
            recipient_caps = next((c for c in all_caps.values() if c.bus_name == recipient), None)
            if recipient_caps is not None:
                ceiling_rank = _PRIORITY_RANKS.get(recipient_caps.priority_ceiling, 99)
                task_rank = _PRIORITY_RANKS.get(priority, 1)
                if task_rank > ceiling_rank:
                    return {
                        "created": False,
                        "reason": (
                            f"Task priority '{priority}' exceeds '{recipient}' priority_ceiling "
                            f"'{recipient_caps.priority_ceiling}'. Lower the task priority or "
                            f"route through operator escalation."
                        ),
                    }
        except Exception:
            pass

    normalized_ingress, inferred_fingerprint_or_error = _normalize_ingress_context(
        ingress_context,
        recipient=recipient,
        request=request,
        expected_output=expected_output,
    )
    if ingress_context is not None and normalized_ingress is None and inferred_fingerprint_or_error:
        return {"created": False, "reason": inferred_fingerprint_or_error}

    effective_work_fingerprint = work_fingerprint or inferred_fingerprint_or_error
    normalized_execution_constraints, constraints_error = _normalize_execution_constraints(execution_constraints)
    if constraints_error:
        return {"created": False, "reason": constraints_error}

    now = now_iso or _now_iso()
    tid = task_id or f"task-{uuid.uuid4().hex[:12]}"
    run_id = f"run-{uuid.uuid4().hex[:8]}"

    try:
        return get_backend(vault_root).create_task(
            task_id=tid,
            run_id=run_id,
            sender=sender,
            recipient=recipient,
            intent=intent,
            priority=priority,
            request=request,
            expected_output=expected_output,
            depends_on=depends_on or [],
            notes=notes,
            expires_at=expires_at,
            now_iso=now,
            ingress_context=normalized_ingress,
            work_fingerprint=effective_work_fingerprint,
            execution_constraints=normalized_execution_constraints,
        )
    except Exception as exc:
        return {"created": False, "reason": f"Agent Bus storage error: {exc}"}


def list_tasks(
    vault_root: Path | None = None,
    *,
    recipient: str | None = None,
    status: str | None = None,
    owner: str | None = None,
) -> list[dict[str, Any]]:
    """List tasks with optional filters. Returns list ordered by created_at ASC."""
    if recipient is not None:
        recipient, _recipient_hint, recipient_error = _resolve_runtime_identity_or_error(vault_root, recipient)
        if recipient_error:
            raise ValueError(recipient_error)
    if owner is not None:
        owner, _owner_hint, owner_error = _resolve_runtime_identity_or_error(vault_root, owner)
        if owner_error:
            raise ValueError(owner_error)
    return get_backend(vault_root).list_tasks(
        recipient=recipient, status=status, owner=owner
    )


def list_heartbeats(
    vault_root: Path | None = None,
    *,
    runtime: str | None = None,
) -> list[dict[str, Any]]:
    """Return all current heartbeat records.

    Optionally filter by runtime bus_name. Both runtime-scoped and instance-scoped
    rows are returned. Callers can reduce by freshness using heartbeat_stale_seconds
    from the runtime's capabilities manifest.
    """
    rows = get_backend(vault_root).list_heartbeats()
    if runtime is not None:
        runtime, _runtime_hint, runtime_error = _resolve_runtime_identity_or_error(vault_root, runtime)
        if runtime_error:
            return []
        rows = [r for r in rows if r.get("runtime") == runtime]
    return rows


def get_bus_mode(vault_root: Path | None = None) -> str:
    """Return the current backend mode string as configured in bus_config.yaml.

    Returns "local" or "server". Falls back to "local" if config is missing or corrupt
    (consistent with backend_loader fail-open policy).
    """
    from .backend_loader import _read_bus_config
    root = Path(vault_root) if vault_root else _repo_root_from_module()
    config = _read_bus_config(root)
    return config.get("mode", "local")


def _task_claim_lane(task: dict[str, Any]) -> dict[str, str | None]:
    ingress = task.get("ingress_context") or {}
    return {
        "owner_instance": str(task.get("owner_instance") or "").strip() or None,
        "work_fingerprint": str(task.get("work_fingerprint") or "").strip() or None,
        "origin_message_id": str(ingress.get("origin_message_id") or "").strip() or None,
        "conversation_key": str(ingress.get("conversation_key") or "").strip() or None,
        "source_platform": str(ingress.get("source_platform") or "").strip() or None,
        "source_channel_id": str(ingress.get("source_channel_id") or "").strip() or None,
        "source_thread_id": str(ingress.get("source_thread_id") or "").strip() or None,
    }


def _task_claim_conflicts(
    vault_root: Path | None,
    *,
    task: dict[str, Any],
    runtime: str,
) -> list[dict[str, Any]]:
    lane = _task_claim_lane(task)
    comparable_keys = ("work_fingerprint", "origin_message_id", "conversation_key")
    if not any(lane.get(key) for key in comparable_keys):
        return []

    conflicts: list[dict[str, Any]] = []
    active_tasks = [
        active
        for active in list_tasks(vault_root, recipient=runtime, owner=runtime)
        if active.get("status") in ACTIVE_TASK_STATES
        and active.get("task_id") != task.get("task_id")
    ]
    for active in active_tasks:
        active_lane = _task_claim_lane(active)
        for key in comparable_keys:
            if lane.get(key) and lane.get(key) == active_lane.get(key):
                conflicts.append(
                    {
                        "task_id": active.get("task_id"),
                        "status": active.get("status"),
                        "owner": active.get("owner"),
                        "reason": key,
                        "value": lane[key],
                    }
                )
                break
    return conflicts


def _derive_runtime_instance_id_from_lane(lane: dict[str, str | None]) -> str | None:
    if lane.get("owner_instance"):
        return lane["owner_instance"]
    source_platform = str(lane.get("source_platform") or "").strip().lower()
    conversation_key = str(lane.get("conversation_key") or "").strip()
    if source_platform == "discord" and conversation_key:
        source_thread_id = str(lane.get("source_thread_id") or "").strip()
        source_channel_id = str(lane.get("source_channel_id") or "").strip()
        if source_thread_id:
            return f"discord-thread-{source_thread_id}"
        if source_channel_id:
            return f"discord-channel-{source_channel_id}"
        return f"discord-lane-{conversation_key.replace(':', '-')}"
    return None


def evaluate_task_claimability(
    vault_root: Path | None,
    *,
    task_id: str,
    runtime: str,
) -> dict[str, Any]:
    """Return whether a runtime may claim a task under bus lane policy."""
    runtime, _runtime_instance_hint, runtime_error = _resolve_runtime_identity_or_error(vault_root, runtime)
    if runtime_error:
        raise ValueError(runtime_error)

    task = get_backend(vault_root).get_task(task_id)
    if task is None:
        return {
            "claimable": False,
            "task_id": task_id,
            "runtime": runtime,
            "reason": "task not found",
            "lane": {},
            "conflicts": [],
        }

    lane = _task_claim_lane(task)
    if task.get("recipient") != runtime:
        return {
            "claimable": False,
            "task_id": task_id,
            "runtime": runtime,
            "reason": f"task recipient is {task.get('recipient')}, not {runtime}",
            "lane": lane,
            "conflicts": [],
        }

    if task.get("status") != "open" or task.get("owner") is not None:
        return {
            "claimable": False,
            "task_id": task_id,
            "runtime": runtime,
            "reason": f"task is not claimable (status={task.get('status')}, owner={task.get('owner')})",
            "lane": lane,
            "conflicts": [],
        }

    conflicts = _task_claim_conflicts(vault_root, task=task, runtime=runtime)
    if conflicts:
        return {
            "claimable": False,
            "task_id": task_id,
            "runtime": runtime,
            "reason": "active lane conflict for runtime",
            "lane": lane,
            "conflicts": conflicts,
        }

    return {
        "claimable": True,
        "task_id": task_id,
        "runtime": runtime,
        "reason": "claimable",
        "lane": lane,
        "conflicts": [],
    }


def claim_task(
    vault_root: Path | None,
    *,
    task_id: str,
    runtime: str,
    now_iso: str | None = None,
    runtime_instance_id: str | None = None,
) -> dict[str, Any]:
    """Atomically claim an open task for runtime. Returns {'claimed': bool, ...}."""
    canonical_runtime, alias_instance_hint, runtime_error = _resolve_runtime_identity_or_error(vault_root, runtime)
    if runtime_error:
        raise ValueError(runtime_error)
    runtime = canonical_runtime
    claimability = evaluate_task_claimability(vault_root, task_id=task_id, runtime=runtime)
    if not claimability["claimable"]:
        return {
            "claimed": False,
            "task_id": task_id,
            "runtime": runtime,
            "reason": claimability["reason"],
            "lane": claimability["lane"],
            "conflicts": claimability["conflicts"],
        }

    result = get_backend(vault_root).claim_task(
        task_id=task_id,
        runtime=runtime,
        now_iso=now_iso or _now_iso(),
        lane_guard=claimability["lane"],
        runtime_instance_id=runtime_instance_id or alias_instance_hint or _derive_runtime_instance_id_from_lane(claimability["lane"]),
    )
    if result.get("claimed"):
        result["lane"] = claimability["lane"]
        result["owner_instance"] = result.get("owner_instance") or _derive_runtime_instance_id_from_lane(claimability["lane"])
    return result


def update_task_status(
    vault_root: Path | None,
    *,
    task_id: str,
    runtime: str,
    status: str,
    event_type: str,
    message: str,
    artifacts: list[str] | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Update task status and append an event. Returns {'updated': bool, ...}."""
    runtime, _runtime_instance_hint, runtime_error = _resolve_runtime_identity_or_error(vault_root, runtime)
    if runtime_error:
        raise ValueError(runtime_error)
    return get_backend(vault_root).update_task_status(
        task_id=task_id,
        runtime=runtime,
        status=status,
        event_type=event_type,
        message=message,
        artifacts=artifacts,
        now_iso=now_iso or _now_iso(),
    )


def cleanup_tasks(
    vault_root: Path | None,
    *,
    runtime: str,
    recipient: str | None = None,
    sender: str | None = None,
    owner: str | None = None,
    status: str | None = None,
    request_exact: str | None = None,
    request_contains: str | None = None,
    updated_before: str | None = None,
    work_fingerprint: str | None = None,
    conversation_key: str | None = None,
    origin_message_id: str | None = None,
    limit: int | None = None,
    reason: str = "Queue hygiene cleanup",
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or cancel matched noisy backlog tasks through bounded filters.

    This is intentionally implemented above the backend layer so cleanup policy stays
    explicit and testable without mutating the storage contract. By default it is
    preview-only (`apply=False`).
    """
    runtime, _runtime_instance_hint, runtime_error = _resolve_runtime_identity_or_error(vault_root, runtime)
    if runtime_error:
        raise ValueError(runtime_error)
    if recipient:
        recipient, _recipient_hint, recipient_error = _resolve_runtime_identity_or_error(vault_root, recipient)
        if recipient_error:
            raise ValueError(recipient_error)
    if sender:
        sender, _sender_hint, sender_error = _resolve_runtime_identity_or_error(vault_root, sender, allow_external_sender=True)
        if sender_error:
            raise ValueError(sender_error)
    if owner:
        owner, _owner_hint, owner_error = _resolve_runtime_identity_or_error(vault_root, owner)
        if owner_error:
            raise ValueError(owner_error)

    if not any([
        recipient,
        sender,
        owner,
        request_exact,
        request_contains,
        updated_before,
        work_fingerprint,
        conversation_key,
        origin_message_id,
    ]):
        return {
            "ok": False,
            "reason": (
                "cleanup requires at least one discriminator filter: recipient, sender, owner, "
                "request_exact, request_contains, updated_before, work_fingerprint, "
                "conversation_key, or origin_message_id"
            ),
            "apply": apply,
            "matched_count": 0,
            "selected_count": 0,
            "matched_tasks": [],
            "matched_task_ids": [],
            "selected_tasks": [],
            "selected_task_ids": [],
            "updated_count": 0,
            "updated_task_ids": [],
            "skipped": [],
        }

    tasks = list_tasks(vault_root, recipient=recipient, status=status, owner=owner)
    cutoff = _parse_iso(updated_before) if updated_before else None

    def _matches(task: dict[str, Any]) -> bool:
        if sender is not None and task.get("sender") != sender:
            return False
        ingress = task.get("ingress_context") or {}
        if work_fingerprint is not None and task.get("work_fingerprint") != work_fingerprint:
            return False
        if conversation_key is not None and ingress.get("conversation_key") != conversation_key:
            return False
        if origin_message_id is not None and ingress.get("origin_message_id") != origin_message_id:
            return False
        request_value = str(task.get("request") or "")
        if request_exact is not None and request_value != request_exact:
            return False
        if request_contains is not None and request_contains not in request_value:
            return False
        if cutoff is not None:
            updated_at = str(task.get("updated_at") or "")
            if not updated_at:
                return False
            if _parse_iso(updated_at) >= cutoff:
                return False
        return True

    matched_tasks = [task for task in tasks if _matches(task)]
    selected_tasks = matched_tasks[: max(limit, 0)] if limit is not None else matched_tasks

    result: dict[str, Any] = {
        "ok": True,
        "runtime": runtime,
        "apply": apply,
        "reason": reason,
        "filters": {
            "recipient": recipient,
            "sender": sender,
            "owner": owner,
            "status": status,
            "request_exact": request_exact,
            "request_contains": request_contains,
            "updated_before": updated_before,
            "work_fingerprint": work_fingerprint,
            "conversation_key": conversation_key,
            "origin_message_id": origin_message_id,
            "limit": limit,
        },
        "matched_count": len(matched_tasks),
        "selected_count": len(selected_tasks),
        "matched_tasks": selected_tasks,
        "matched_task_ids": [task.get("task_id") for task in matched_tasks],
        "selected_tasks": selected_tasks,
        "selected_task_ids": [task.get("task_id") for task in selected_tasks],
        "updated_count": 0,
        "updated_task_ids": [],
        "skipped": [],
    }
    if apply and status != "open":
        result["ok"] = False
        result["reason"] = "cleanup apply requires explicit status=open to avoid cancelling active or mixed-state work"
        return result

    if not apply:
        return result

    updated_task_ids: list[str] = []
    skipped: list[dict[str, Any]] = []
    for task in selected_tasks:
        update_result = update_task_status(
            vault_root,
            task_id=str(task["task_id"]),
            runtime=runtime,
            status="cancelled",
            event_type="cancelled",
            message=reason,
        )
        if update_result.get("updated"):
            updated_task_ids.append(str(task["task_id"]))
        else:
            skipped.append(
                {
                    "task_id": task.get("task_id"),
                    "reason": update_result.get("reason", "update failed"),
                }
            )

    result["updated_count"] = len(updated_task_ids)
    result["updated_task_ids"] = updated_task_ids
    result["skipped"] = skipped
    return result


def upsert_heartbeat(
    vault_root: Path | None,
    *,
    runtime: str,
    status: str,
    health: str,
    current_task_id: str | None = None,
    summary: str | None = None,
    now_iso: str | None = None,
    runtime_instance_id: str | None = None,
    heartbeat_scope: str = "runtime",
    control_surface: str | None = None,
    control_surface_key: str | None = None,
) -> dict[str, Any]:
    """Insert or update a runtime or runtime-instance liveness record."""
    runtime, alias_instance_hint, runtime_error = _resolve_runtime_identity_or_error(vault_root, runtime)
    if runtime_error:
        raise ValueError(runtime_error)
    if runtime_instance_id is None and alias_instance_hint:
        runtime_instance_id = alias_instance_hint
        heartbeat_scope = "instance"
    return get_backend(vault_root).upsert_heartbeat(
        runtime=runtime,
        status=status,
        health=health,
        current_task_id=current_task_id,
        summary=summary,
        now_iso=now_iso or _now_iso(),
        runtime_instance_id=runtime_instance_id,
        heartbeat_scope=heartbeat_scope,
        control_surface=control_surface,
        control_surface_key=control_surface_key,
    )


def mark_stale_tasks(
    vault_root: Path | None,
    *,
    max_age_seconds: int,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Expire tasks owned by stale runtimes exceeding max_age_seconds.

    Staleness is determined here (via the router) before delegating storage
    to the backend. The backend receives stale_runtimes as a resolved set —
    it does not query heartbeats.
    """
    now_str = now_iso or _now_iso()
    stale_runtimes: set[str] = set()
    if vault_root is not None:
        try:
            from runtime.agent_bus.router import get_stale_runtimes
            stale_runtimes = set(get_stale_runtimes(vault_root))
        except Exception:
            pass
    return get_backend(vault_root).mark_stale_tasks(
        max_age_seconds=max_age_seconds,
        stale_runtimes=stale_runtimes,
        now_iso=now_str,
    )


def reclaim_task(
    vault_root: Path | None,
    *,
    task_id: str,
    new_runtime: str,
    reason: str = "Reclaimed due to stale owning runtime.",
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Re-open a task owned by a stale runtime for another runtime to claim.

    Does NOT check staleness — verify via get_stale_runtimes() before calling.
    """
    new_runtime, _runtime_instance_hint, runtime_error = _resolve_runtime_identity_or_error(vault_root, new_runtime)
    if runtime_error:
        return {"reclaimed": False, "reason": runtime_error}
    return get_backend(vault_root).reclaim_task(
        task_id=task_id,
        new_runtime=new_runtime,
        reason=reason,
        now_iso=now_iso or _now_iso(),
    )


# ── Orchestration (composes storage operations) ───────────────────────────────

def _select_next_open_task(
    vault_root: Path | None,
    *,
    runtime: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    tasks = list_tasks(vault_root, recipient=runtime, status="open")
    skipped_conflicts: list[dict[str, Any]] = []
    for task in tasks:
        claimability = evaluate_task_claimability(vault_root, task_id=task["task_id"], runtime=runtime)
        if claimability["claimable"]:
            return task, skipped_conflicts
        if claimability["conflicts"]:
            skipped_conflicts.append(
                {
                    "task_id": task["task_id"],
                    "reason": claimability["reason"],
                    "lane": claimability["lane"],
                    "conflicts": claimability["conflicts"],
                }
            )
    return None, skipped_conflicts


def _pick_next_open_task(vault_root: Path | None, *, runtime: str) -> dict[str, Any] | None:
    task, _skipped_conflicts = _select_next_open_task(vault_root, runtime=runtime)
    return task


def _derive_watch_heartbeat_scope(runtime: str, task: dict[str, Any] | None) -> dict[str, Any]:
    if not task:
        return {
            "runtime_instance_id": None,
            "heartbeat_scope": "runtime",
            "control_surface": None,
            "control_surface_key": None,
        }
    ingress = task.get("ingress_context") or {}
    owner_instance = str(task.get("owner_instance") or "").strip()
    if owner_instance:
        source_platform = str(ingress.get("source_platform") or "").strip().lower()
        conversation_key = str(ingress.get("conversation_key") or "").strip()
        return {
            "runtime_instance_id": owner_instance,
            "heartbeat_scope": "instance",
            "control_surface": source_platform or None,
            "control_surface_key": conversation_key or None,
        }
    source_platform = str(ingress.get("source_platform") or "").strip().lower()
    conversation_key = str(ingress.get("conversation_key") or "").strip()
    if source_platform == "discord" and conversation_key:
        source_thread_id = str(ingress.get("source_thread_id") or "").strip()
        source_channel_id = str(ingress.get("source_channel_id") or "").strip()
        runtime_instance_id = f"discord-lane-{conversation_key.replace(':', '-')}"
        if source_channel_id:
            runtime_instance_id = f"discord-channel-{source_channel_id}"
        if source_thread_id:
            runtime_instance_id = f"discord-thread-{source_thread_id}"
        return {
            "runtime_instance_id": runtime_instance_id,
            "heartbeat_scope": "instance",
            "control_surface": "discord",
            "control_surface_key": conversation_key,
        }
    return {
        "runtime_instance_id": None,
        "heartbeat_scope": "runtime",
        "control_surface": source_platform or None,
        "control_surface_key": conversation_key or None,
    }


def watch_once(
    vault_root: Path | None,
    *,
    runtime: str,
    claim_next: bool = False,
    stale_after_seconds: int | None = None,
    runtime_instance_id: str | None = None,
    control_surface: str | None = None,
    control_surface_key: str | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Single watch cycle: expire stale tasks, optionally claim next, upsert heartbeat."""
    runtime, alias_instance_hint, runtime_error = _resolve_runtime_identity_or_error(vault_root, runtime)
    if runtime_error:
        raise ValueError(runtime_error)
    if runtime_instance_id is None and alias_instance_hint:
        runtime_instance_id = alias_instance_hint
    now = now_iso or _now_iso()
    expired: dict[str, Any] = {"expired_count": 0, "task_ids": []}
    if stale_after_seconds is not None:
        expired = mark_stale_tasks(vault_root, max_age_seconds=stale_after_seconds, now_iso=now)

    claimed_task_id: str | None = None
    skipped_conflicts: list[dict[str, Any]] = []
    if claim_next:
        next_task, skipped_conflicts = _select_next_open_task(vault_root, runtime=runtime)
        if next_task is not None:
            result = claim_task(
                vault_root,
                task_id=next_task["task_id"],
                runtime=runtime,
                runtime_instance_id=runtime_instance_id,
                now_iso=now,
            )
            if result["claimed"]:
                claimed_task_id = next_task["task_id"]

    open_tasks = list_tasks(vault_root, recipient=runtime, status="open")
    claimed_tasks = list_tasks(vault_root, recipient=runtime, owner=runtime)
    claimed_task = None
    if claimed_task_id is not None:
        claimed_task = next((task for task in claimed_tasks if task.get("task_id") == claimed_task_id), None)
    heartbeat_scope = _derive_watch_heartbeat_scope(runtime, claimed_task)
    if runtime_instance_id:
        heartbeat_scope = {
            "runtime_instance_id": runtime_instance_id,
            "heartbeat_scope": "instance",
            "control_surface": control_surface or heartbeat_scope.get("control_surface"),
            "control_surface_key": control_surface_key or heartbeat_scope.get("control_surface_key"),
        }
    upsert_heartbeat(
        vault_root,
        runtime=runtime,
        status="busy" if claimed_task_id else "idle",
        health="ok",
        current_task_id=claimed_task_id,
        summary=f"open={len(open_tasks)} claimed_by_runtime={len(claimed_tasks)}",
        runtime_instance_id=heartbeat_scope["runtime_instance_id"],
        heartbeat_scope=heartbeat_scope["heartbeat_scope"],
        control_surface=heartbeat_scope["control_surface"],
        control_surface_key=heartbeat_scope["control_surface_key"],
        now_iso=now,
    )

    return {
        "runtime": runtime,
        "now": now,
        "claimed_task_id": claimed_task_id,
        "open_task_count": len(open_tasks),
        "claimed_task_count": len(claimed_tasks),
        "skipped_conflict_count": len(skipped_conflicts),
        "skipped_conflicts": skipped_conflicts,
        "expired_count": expired.get("expired_count", 0),
        "expired_task_ids": expired.get("task_ids", []),
    }


def run_watch_loop(
    vault_root: Path | None,
    *,
    runtime: str,
    interval_seconds: int,
    claim_next: bool = False,
    stale_after_seconds: int | None = None,
    runtime_instance_id: str | None = None,
    control_surface: str | None = None,
    control_surface_key: str | None = None,
) -> None:
    """Polling watch loop. Runs watch_once every interval_seconds indefinitely."""
    if interval_seconds <= 0:
        raise ValueError("watch interval_seconds must be greater than 0")
    while True:
        watch_once(
            vault_root,
            runtime=runtime,
            claim_next=claim_next,
            stale_after_seconds=stale_after_seconds,
            runtime_instance_id=runtime_instance_id,
            control_surface=control_surface,
            control_surface_key=control_surface_key,
        )
        time.sleep(interval_seconds)


def get_task(vault_root: Path | None, task_id: str) -> dict[str, Any] | None:
    """Return a single task by task_id, or None if not found."""
    return get_backend(vault_root).get_task(task_id)


def list_events(vault_root: Path | None, task_id: str) -> list[dict[str, Any]]:
    """Return all events for a task ordered by created_at ASC."""
    return get_backend(vault_root).list_events(task_id)
