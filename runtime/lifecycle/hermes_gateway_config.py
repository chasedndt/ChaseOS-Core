"""Safe Hermes gateway .env bootstrap/status helpers.

This module manages only a bounded ChaseOS-owned gateway block. It never
returns secret values and preserves user-owned .env lines. On Windows, callers
can prefer the configured WSL Hermes environment so Studio buttons update the
same active Hermes ``.env`` file that the WSL gateway reads.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.lifecycle.health_cli import load_lifecycle_record
from runtime.lifecycle.startup_surfaces import resolve_gateway_surface_config

ROOT = Path(__file__).resolve().parents[2]


def _windows_no_window_subprocess_kwargs() -> dict[str, Any]:
    """Return Windows subprocess options that suppress transient consoles.

    The gateway config bridge shells out to ``wsl.exe``; without these flags it
    flashes a short-lived conhost window every time a Studio gateway button is
    used. CREATE_NO_WINDOW plus hidden STARTUPINFO keeps the bridge invisible.
    """
    if os.name != "nt":
        return {}
    kwargs: dict[str, Any] = {"creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)}
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0) or 0)
        startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0) or 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs


MANAGED_BEGIN = "# BEGIN CHASEOS MANAGED HERMES GATEWAY"
MANAGED_END = "# END CHASEOS MANAGED HERMES GATEWAY"

# Confirmed from Hermes gateway source:
# gateway/run.py checks GATEWAY_ALLOWED_USERS / GATEWAY_ALLOW_ALL_USERS and
# platform-specific allowlist vars; gateway/config.py bridges Discord/Telegram
# config keys into these env vars.
PLATFORM_CREDENTIAL_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
)

ALLOWLIST_KEYS = (
    "GATEWAY_ALLOW_ALL_USERS",
    "GATEWAY_ALLOWED_USERS",
    "TELEGRAM_ALLOWED_USERS",
    "TELEGRAM_GROUP_ALLOWED_USERS",
    "TELEGRAM_GROUP_ALLOWED_CHATS",
    "DISCORD_ALLOWED_USERS",
    "DISCORD_ALLOWED_ROLES",
    "DISCORD_ALLOWED_CHANNELS",
)

MANAGED_BLOCK_KEYS = (
    "GATEWAY_ALLOW_ALL_USERS",
    "GATEWAY_ALLOWED_USERS",
    "TELEGRAM_ALLOWED_USERS",
    "TELEGRAM_GROUP_ALLOWED_USERS",
    "TELEGRAM_GROUP_ALLOWED_CHATS",
    "DISCORD_ALLOWED_USERS",
    "DISCORD_ALLOWED_ROLES",
    "DISCORD_ALLOWED_CHANNELS",
)

SECRETISH_KEY_PATTERN = re.compile(
    r"(TOKEN|SECRET|KEY|PASSWORD|PASS|AUTH|COOKIE|ID|USER|CHANNEL|GUILD|EMAIL|PHONE)",
    re.I,
)

_WSL_BRIDGE_CODE = """
import json
import sys

from runtime.lifecycle.hermes_gateway_config import run_hermes_gateway_config_action

payload = json.loads(sys.stdin.read() or sys.argv[1])
result = run_hermes_gateway_config_action(**payload)
print(json.dumps(result, sort_keys=True))
""".strip()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_env_path() -> Path:
    for env_name in ("CHASEOS_HERMES_HOME", "HERMES_HOME"):
        hermes_home = os.environ.get(env_name, "").strip()
        if hermes_home:
            return Path(hermes_home).expanduser() / ".env"
    chaseos_home = Path.home() / "runtimes" / "hermes-home"
    if chaseos_home.exists() or os.environ.get("WSL_DISTRO_NAME"):
        return chaseos_home / ".env"
    return Path.home() / ".hermes" / ".env"


def _coerce_path(path: str | Path | None) -> Path:
    return Path(path).expanduser() if path is not None else _default_env_path()


def _normalize_vault_root(vault_root: str | Path | None = None) -> Path:
    return Path(vault_root).resolve() if vault_root is not None else ROOT


def _detect_wsl_distro() -> str | None:
    value = os.environ.get("WSL_DISTRO_NAME", "").strip()
    if value:
        return value
    try:
        text = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").lower()
        if "microsoft" in text or "wsl" in text:
            return "unknown-wsl-distro"
    except OSError:
        pass
    return None


def _read_env_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _parse_env_assignments(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def _configured(value: str | None) -> bool:
    raw = (value or "").strip()
    if not raw:
        return False
    if raw.lower() in {"false", "0", "no", "none", "null"}:
        return False
    return True


def _normalize_allowed_users(allowed_users: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if allowed_users is None:
        return []
    raw_items = allowed_users.split(",") if isinstance(allowed_users, str) else [str(item) for item in allowed_users]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        result.append(cleaned)
        seen.add(cleaned)
    return result


def _merge_allowed_csv(existing: str | None, additions: list[str]) -> str:
    merged = _normalize_allowed_users(existing)
    seen = set(merged)
    for item in additions:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return ",".join(merged)


def _redacted_key_status(values: dict[str, str], keys: tuple[str, ...]) -> dict[str, dict[str, bool]]:
    return {
        key: {
            "present": key in values,
            "configured": _configured(values.get(key)),
        }
        for key in keys
    }


def _has_managed_block(text: str) -> bool:
    return MANAGED_BEGIN in text and MANAGED_END in text


def _build_managed_block(
    existing_values: dict[str, str] | None = None,
    *,
    allowed_users: str | list[str] | tuple[str, ...] | None = None,
    allow_all: bool | None = None,
) -> str:
    existing_values = existing_values or {}
    user_additions = _normalize_allowed_users(allowed_users)
    lines = [
        MANAGED_BEGIN,
        "# Set platform credentials and platform-specific allowlists here.",
        "# Do not commit this file. ChaseOS reports status only with redacted yes/no fields.",
    ]
    for key in MANAGED_BLOCK_KEYS:
        if key == "GATEWAY_ALLOW_ALL_USERS":
            if allow_all is None:
                value = existing_values.get(key, "false").strip() or "false"
            else:
                value = "true" if allow_all else "false"
        elif key == "GATEWAY_ALLOWED_USERS":
            value = _merge_allowed_csv(existing_values.get(key, ""), user_additions)
        else:
            value = existing_values.get(key, "").strip()
        lines.append(f"{key}={value}")
    lines.append(MANAGED_END)
    return "\n".join(lines) + "\n"


def _upsert_managed_block(text: str, block: str) -> tuple[str, str]:
    if MANAGED_BEGIN in text and MANAGED_END in text:
        pattern = re.compile(
            re.escape(MANAGED_BEGIN) + r".*?" + re.escape(MANAGED_END) + r"\n?",
            re.DOTALL,
        )
        return pattern.sub(block, text, count=1), "updated"
    if text and not text.endswith("\n"):
        text += "\n"
    if text.strip():
        text += "\n"
    return text + block, "created"


def _write_template_file() -> Path:
    template_path = ROOT / "runtime" / "lifecycle" / "templates" / "hermes.gateway.env.template"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(_build_managed_block({}), encoding="utf-8")
    return template_path


def _append_config_event(vault_root: str | Path | None, payload: dict[str, Any]) -> None:
    vault = _normalize_vault_root(vault_root)
    path = vault / "runtime" / "lifecycle" / "run" / "hermes-gateway-config-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "surface": "runtime.lifecycle.hermes_gateway_config",
        **payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _read_chaseos_operator_binding(vault_root: str | Path | None = None) -> dict[str, Any]:
    vault = _normalize_vault_root(vault_root)
    path = vault / ".chaseos" / "discord_instance_bindings.yaml"
    if not path.exists():
        return {"available": False, "source": str(path), "value_redacted": True}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {
            "available": False,
            "source": str(path),
            "value_redacted": True,
            "error": "unreadable_binding_file",
        }
    in_operator = False
    for line in text.splitlines():
        if re.match(r"^operator:\s*$", line):
            in_operator = True
            continue
        if in_operator and line and not line.startswith((" ", "\t")):
            break
        if in_operator:
            match = re.match(r"\s*user_id:\s*[\"']?([^\"'#\s]+)", line)
            if match and match.group(1).strip():
                return {
                    "available": True,
                    "source": str(path),
                    "value_redacted": True,
                    "value": match.group(1).strip(),
                }
    return {"available": False, "source": str(path), "value_redacted": True}


def _public_operator_binding(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(binding.get("available")),
        "source": binding.get("source"),
        "value_redacted": True,
    }


def _redaction_guard(payload: Any) -> bool:
    """Return True when the payload appears free of raw secret/id values."""

    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, str) and SECRETISH_KEY_PATTERN.search(str(key)):
                return False
            if not _redaction_guard(value):
                return False
    elif isinstance(payload, list):
        return all(_redaction_guard(item) for item in payload)
    return True


def build_hermes_gateway_config_status(
    env_path: str | Path | None = None,
    *,
    vault_root: str | Path | None = None,
) -> dict[str, Any]:
    path = _coerce_path(env_path)
    text = _read_env_text(path)
    values = _parse_env_assignments(text)
    platform_keys = _redacted_key_status(values, PLATFORM_CREDENTIAL_KEYS)
    allowlist_keys = _redacted_key_status(values, ALLOWLIST_KEYS)
    any_platform_configured = any(item["configured"] for item in platform_keys.values())
    any_allowlist_configured = any(item["configured"] for item in allowlist_keys.values())
    payload: dict[str, Any] = {
        "ok": True,
        "action": "status",
        "env_path": str(path),
        "env_present": path.exists(),
        "managed_block_present": _has_managed_block(text),
        "platform_keys": platform_keys,
        "allowlist_keys": allowlist_keys,
        "messaging_platform_status": "configured" if any_platform_configured else "configured_but_disabled",
        "allowlist_status": "configured" if any_allowlist_configured else "missing",
        "gateway_ready": bool(any_platform_configured and any_allowlist_configured),
        "chaseos_operator_binding": _public_operator_binding(_read_chaseos_operator_binding(vault_root)),
        "redacted_status_only": True,
    }
    payload["redaction_guard_passed"] = _redaction_guard(payload)
    return payload


def build_hermes_gateway_config_plan(
    env_path: str | Path | None = None,
    *,
    vault_root: str | Path | None = None,
) -> dict[str, Any]:
    path = _coerce_path(env_path)
    template_path = ROOT / "runtime" / "lifecycle" / "templates" / "hermes.gateway.env.template"
    status = build_hermes_gateway_config_status(path, vault_root=vault_root)
    return {
        "ok": True,
        "action": "plan",
        "repo_root": str(ROOT),
        "env_path": str(path),
        "template_path": str(template_path),
        "detected": {
            "posix_user": os.environ.get("USER") or os.environ.get("USERNAME") or None,
            "home": str(Path.home()),
            "platform": platform.system(),
            "wsl_distro": _detect_wsl_distro(),
        },
        "will_create_env_if_missing": not path.exists(),
        "will_preserve_user_owned_lines": True,
        "will_upsert_managed_block": True,
        "backup_required_before_apply": True,
        "supports_chaseos_operator_button": bool(status.get("chaseos_operator_binding", {}).get("available")),
        "supported_button_actions": [
            "add_chaseos_operator_to_gateway_allowed_users",
            "set_gateway_allowed_users",
        ],
        "status": status,
        "redacted_status_only": True,
    }


def backup_hermes_gateway_config(env_path: str | Path | None = None) -> dict[str, Any]:
    path = _coerce_path(env_path)
    stamp = _utc_stamp()
    backup_dir = path.parent / "backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / ".env.bak"
    copied = False
    if path.exists():
        shutil.copy2(path, backup_path)
        copied = True
    return {
        "ok": True,
        "action": "backup",
        "env_path": str(path),
        "backup_dir": str(backup_dir),
        "backup_path": str(backup_path) if copied else None,
        "env_present": path.exists(),
        "backup_created": copied,
        "redacted_status_only": True,
    }


def apply_hermes_gateway_config(
    env_path: str | Path | None = None,
    *,
    vault_root: str | Path | None = None,
    allowed_users: str | list[str] | tuple[str, ...] | None = None,
    allow_all: bool | None = None,
    use_chaseos_operator: bool = False,
    confirm: bool = False,
    requested_by: str = "operator",
    dry_run: bool = False,
) -> dict[str, Any]:
    path = _coerce_path(env_path)
    if not dry_run and not confirm:
        return {
            "ok": False,
            "action": "apply",
            "error": "confirm_required",
            "redacted_status_only": True,
        }
    operator_binding = _read_chaseos_operator_binding(vault_root)
    operator_users: list[str] = []
    if use_chaseos_operator:
        if not operator_binding.get("available") or not operator_binding.get("value"):
            return {
                "ok": False,
                "action": "apply",
                "error": "chaseos_operator_binding_missing",
                "chaseos_operator_binding": _public_operator_binding(operator_binding),
                "redacted_status_only": True,
            }
        operator_users = [str(operator_binding["value"])]
    additions = _normalize_allowed_users(allowed_users) + operator_users
    before_status = build_hermes_gateway_config_status(path, vault_root=vault_root)
    if dry_run:
        return {
            "ok": True,
            "action": "apply",
            "dry_run": True,
            "env_path": str(path),
            "will_backup_before_write": True,
            "will_preserve_user_owned_lines": True,
            "will_upsert_managed_block": True,
            "allowed_user_addition_count": len(additions),
            "allow_all_requested": bool(allow_all),
            "chaseos_operator_binding": _public_operator_binding(operator_binding),
            "status": before_status,
            "redacted_status_only": True,
        }
    backup = backup_hermes_gateway_config(path)
    text = _read_env_text(path)
    existing_values = _parse_env_assignments(text)
    block = _build_managed_block(existing_values, allowed_users=additions, allow_all=allow_all)
    updated, block_action = _upsert_managed_block(text, block)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    template_path = _write_template_file()
    after_status = build_hermes_gateway_config_status(path, vault_root=vault_root)
    mutation_id = f"hermes-gateway-config-apply-{_utc_stamp()}"
    result = {
        "ok": True,
        "action": "apply",
        "mutation_id": mutation_id,
        "env_path": str(path),
        "backup": backup,
        "managed_block_action": block_action,
        "template_path": str(template_path),
        "env_created": not before_status["env_present"],
        "preserved_user_owned_lines": True,
        "allowed_user_addition_count": len(additions),
        "allow_all_requested": bool(allow_all),
        "approval_record": {
            "approval_kind": "operator_confirmed_private_hermes_gateway_config",
            "approval_required": True,
            "approval_recorded": True,
            "approval_scope": "private_hermes_gateway_allowlist_config",
            "requested_by": requested_by,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "raw_values_logged": False,
            "backup_before_write": bool(backup.get("ok")),
        },
        "chaseos_operator_binding": _public_operator_binding(operator_binding),
        "status": after_status,
        "redacted_status_only": True,
    }
    _append_config_event(
        vault_root,
        {
            "action": "apply",
            "mutation_id": mutation_id,
            "managed_block_action": block_action,
            "env_path": str(path),
            "backup_created": bool(backup.get("backup_created")),
            "allowed_user_addition_count": len(additions),
            "allow_all_requested": bool(allow_all),
            "approval_recorded": True,
            "raw_values_logged": False,
        },
    )
    return result


def _wsl_gateway_context(vault_root: str | Path | None = None) -> dict[str, Any]:
    vault = _normalize_vault_root(vault_root)
    record = load_lifecycle_record("hermes", vault)
    surfaces = record.get("startup_surfaces") if isinstance(record.get("startup_surfaces"), dict) else {}
    gateway = surfaces.get("gateway") if isinstance(surfaces.get("gateway"), dict) else {}
    resolved = resolve_gateway_surface_config(gateway, vault)
    return {
        "vault_root": str(vault),
        "wsl_vault_path": str(resolved.get("wsl_workdir") or resolved.get("wsl_vault_path") or ""),
        "wsl_distro": str(resolved.get("wsl_distro") or "Ubuntu"),
        "wsl_user": str(resolved.get("wsl_user") or ""),
    }


def _run_hermes_gateway_config_in_wsl(
    *,
    action: str,
    vault_root: str | Path | None = None,
    allowed_users: str | list[str] | tuple[str, ...] | None = None,
    allow_all: bool | None = None,
    use_chaseos_operator: bool = False,
    confirm: bool = False,
    requested_by: str = "operator",
    dry_run: bool = False,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    context = _wsl_gateway_context(vault_root)
    distro = context["wsl_distro"]
    wsl_vault_path = context["wsl_vault_path"]
    if not distro or not wsl_vault_path:
        return {
            "ok": False,
            "action": action,
            "error": "wsl_context_unavailable",
            "redacted_status_only": True,
        }
    payload = {
        "action": action,
        "env_path": None,
        "vault_root": wsl_vault_path,
        "allowed_users": allowed_users,
        "allow_all": allow_all,
        "use_chaseos_operator": use_chaseos_operator,
        "confirm": confirm,
        "requested_by": requested_by,
        "dry_run": dry_run,
        "prefer_wsl": False,
    }
    args = ["wsl.exe", "-d", distro]
    if context.get("wsl_user"):
        args.extend(["-u", str(context["wsl_user"])])
    args.extend(["--cd", wsl_vault_path, "--", "python3", "-c", _WSL_BRIDGE_CODE])
    try:
        completed = subprocess.run(
            args,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            **_windows_no_window_subprocess_kwargs(),
        )
    except Exception as exc:
        return {
            "ok": False,
            "action": action,
            "error": "wsl_config_bridge_failed",
            "error_type": type(exc).__name__,
            "wsl_distro": distro,
            "redacted_status_only": True,
        }
    if completed.returncode != 0:
        return {
            "ok": False,
            "action": action,
            "error": "wsl_config_bridge_nonzero",
            "returncode": completed.returncode,
            "stderr_redacted": bool(completed.stderr),
            "wsl_distro": distro,
            "redacted_status_only": True,
        }
    try:
        result = json.loads(completed.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "action": action,
            "error": "wsl_config_bridge_bad_json",
            "stdout_redacted": bool(completed.stdout),
            "redacted_status_only": True,
        }
    if isinstance(result, dict):
        result["execution_surface"] = "wsl"
        result["wsl_distro"] = distro
        result["wsl_vault_path"] = wsl_vault_path
        return result
    return {
        "ok": False,
        "action": action,
        "error": "wsl_config_bridge_unexpected_result",
        "redacted_status_only": True,
    }


def build_hermes_gateway_config_control_model(
    vault_root: str | Path | None = None,
    *,
    probe_wsl: bool = False,
) -> dict[str, Any]:
    vault = _normalize_vault_root(vault_root)
    operator_binding = _public_operator_binding(_read_chaseos_operator_binding(vault))
    model: dict[str, Any] = {
        "ok": True,
        "surface": "hermes_gateway_config_controls",
        "config_owner": "Hermes private active .env",
        "redacted_status_only": True,
        "raw_values_included": False,
        "chaseos_operator_binding": operator_binding,
        "supported_actions": [
            "check_status",
            "add_chaseos_operator",
            "set_gateway_allowed_users",
        ],
        "button_labels": {
            "check_status": "Check Allowlist",
            "add_chaseos_operator": "Add ChaseOS Operator",
            "set_gateway_allowed_users": "Set Allowed IDs",
        },
        "status": "probe_not_run",
        "requires_wsl_for_live_config": os.name == "nt",
    }
    if probe_wsl:
        model["status"] = run_hermes_gateway_config_action("status", vault_root=vault, prefer_wsl=True)
    return model


def run_hermes_gateway_config_action(
    action: str,
    env_path: str | Path | None = None,
    *,
    vault_root: str | Path | None = None,
    allowed_users: str | list[str] | tuple[str, ...] | None = None,
    allow_all: bool | None = None,
    use_chaseos_operator: bool = False,
    confirm: bool = False,
    requested_by: str = "operator",
    dry_run: bool = False,
    prefer_wsl: bool = True,
) -> dict[str, Any]:
    normalized = (action or "status").strip().lower()
    if (
        prefer_wsl
        and env_path is None
        and os.name == "nt"
        and shutil.which("wsl.exe")
        and normalized in {"plan", "status", "backup", "apply"}
    ):
        return _run_hermes_gateway_config_in_wsl(
            action=normalized,
            vault_root=vault_root,
            allowed_users=allowed_users,
            allow_all=allow_all,
            use_chaseos_operator=use_chaseos_operator,
            confirm=confirm,
            requested_by=requested_by,
            dry_run=dry_run,
        )
    if normalized == "plan":
        return build_hermes_gateway_config_plan(env_path, vault_root=vault_root)
    if normalized == "backup":
        return backup_hermes_gateway_config(env_path)
    if normalized == "apply":
        return apply_hermes_gateway_config(
            env_path,
            vault_root=vault_root,
            allowed_users=allowed_users,
            allow_all=allow_all,
            use_chaseos_operator=use_chaseos_operator,
            confirm=confirm,
            requested_by=requested_by,
            dry_run=dry_run,
        )
    if normalized == "status":
        return build_hermes_gateway_config_status(env_path, vault_root=vault_root)
    return {"ok": False, "action": normalized, "error": "unsupported_action"}
