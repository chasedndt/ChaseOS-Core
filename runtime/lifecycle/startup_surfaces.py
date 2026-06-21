"""Read-only runtime startup surface report for Studio-facing controls.

This module turns lifecycle-declared startup surfaces into a stable report that
Studio can render later. It does not enable, disable, register, unregister, or
write host startup state.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.lifecycle.health_cli import LIFECYCLE_DIR, load_lifecycle_record
from runtime.lifecycle.coordination_watch_supervisor import (
    get_supervised_coordination_watch_status,
    start_supervised_coordination_watch,
    stop_supervised_coordination_watch,
)
from runtime.lifecycle.coordination_watch_bootstrap import (
    apply_coordination_watch_bootstrap,
    build_coordination_watch_activation_report,
    install_coordination_watch_bootstrap,
    unregister_coordination_watch_bootstrap,
)

ROOT = Path(__file__).resolve().parents[2]
STARTUP_SURFACE_APPROVAL_DIR = ROOT / "07_LOGS" / "Agent-Activity" / "_runtime_startup_surface_approvals"
STARTUP_SURFACE_MUTATION_DIR = ROOT / "runtime" / "lifecycle" / "run" / "startup-surface-mutations"
VALID_TOGGLE_INTENTS = {"enable", "disable"}
VALID_APPROVAL_DECISIONS = {"approved", "denied"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def list_lifecycle_runtime_ids() -> list[str]:
    return sorted(path.name.removesuffix(".lifecycle.yaml") for path in LIFECYCLE_DIR.glob("*.lifecycle.yaml"))


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _windows_drive_path_to_wsl(raw_path: str) -> Path | None:
    """Translate a Windows drive path into its WSL mount path when needed."""
    text = str(raw_path or "").strip()
    if len(text) < 3 or text[1] != ":" or text[2] not in {"\\", "/"}:
        return None
    drive = text[0].lower()
    if not drive.isalpha():
        return None
    normalized = text.replace("\\", "/")
    return Path("/mnt") / drive / normalized[3:].lstrip("/")


def _wsl_mount_path_to_windows(raw_path: Any) -> str | None:
    """Translate /mnt/<drive>/... paths into Windows batch-file paths."""
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text.startswith("/mnt/") or len(text) < 8:
        return None
    drive = text[5]
    if not drive.isalpha() or text[6] != "/":
        return None
    rest = text[7:].replace("/", "\\")
    return f"{drive.upper()}:\\{rest}"


def _resolve_path(raw_path: Any) -> Path | None:
    if raw_path in (None, ""):
        return None
    text = str(raw_path)
    windows_path = _windows_drive_path_to_wsl(text) if os.name != "nt" else None
    if windows_path is not None:
        return windows_path
    path = Path(text)
    if path.is_absolute():
        return path
    return ROOT / path


def _path_present(raw_path: Any) -> bool:
    path = _resolve_path(raw_path)
    return bool(path and path.exists())


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    return int(value)


def _read_text_if_present(raw_path: Any) -> str | None:
    path = _resolve_path(raw_path)
    if not path or not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def _normalize_cmd_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _normalize_windows_path_text(value: Any) -> str:
    return str(value or "").replace("\\\\", "\\")


def _windows_user_profile_from_vault(vault_root: Path | None = None) -> str | None:
    """Infer C:\\Users\\<name> from a WSL-mounted vault path when Windows env is absent."""
    raw_root = str(vault_root or ROOT).replace("\\", "/")
    raw_parts = raw_root.split("/")
    if len(raw_parts) >= 5 and raw_parts[1] == "mnt" and raw_parts[3].lower() == "users":
        drive = raw_parts[2].upper()
        username = raw_parts[4]
        return f"{drive}:\\Users\\{username}"
    root = (vault_root or ROOT).resolve()
    parts = root.parts
    if len(parts) >= 5 and parts[1] == "mnt" and parts[3].lower() == "users":
        drive = parts[2].upper()
        username = parts[4]
        return f"{drive}:\\Users\\{username}"
    return None


def _host_user_profile(vault_root: Path | None = None) -> str:
    raw = os.environ.get("USERPROFILE")
    if raw:
        return _normalize_windows_path_text(raw)
    inferred = _windows_user_profile_from_vault(vault_root)
    if inferred:
        return _normalize_windows_path_text(inferred)
    return _normalize_windows_path_text(str(Path.home()))


def _host_appdata(vault_root: Path | None = None) -> str:
    raw = os.environ.get("APPDATA")
    if raw:
        return _normalize_windows_path_text(raw)
    return _normalize_windows_path_text(
        _host_user_profile(vault_root) + "\\AppData\\Roaming"
    )


def _host_startup_dir(vault_root: Path | None = None) -> str:
    return _normalize_windows_path_text(
        _host_appdata(vault_root) + "\\Microsoft\\Windows\\Start Menu\\Programs\\Startup"
    )


def _host_username(vault_root: Path | None = None) -> str:
    return Path(_host_user_profile(vault_root)).name or os.environ.get("USERNAME") or ""


def _windows_vault_path(vault_root: Path | None = None) -> str:
    raw_root = str(vault_root or ROOT)
    translated = _wsl_mount_path_to_windows(raw_root)
    if translated:
        return _normalize_windows_path_text(translated)
    root = Path(raw_root).resolve()
    translated = _wsl_mount_path_to_windows(root)
    return _normalize_windows_path_text(translated or str(root))


def _wsl_vault_path(vault_root: Path | None = None) -> str:
    windows_path = _windows_vault_path(vault_root)
    translated = _windows_drive_path_to_wsl(windows_path)
    return str(translated).replace("\\", "/") if translated is not None else windows_path.replace("\\", "/")


def _wsl_distro_name(config: dict[str, Any]) -> str:
    return (
        os.environ.get("CHASEOS_HERMES_WSL_DISTRO")
        or os.environ.get("WSL_DISTRO_NAME")
        or str(config.get("wsl_distro") or "").strip()
        or "Ubuntu"
    )


def _wsl_user_name(config: dict[str, Any]) -> str:
    configured = str(config.get("wsl_user") or "").strip()
    if configured and "{" not in configured:
        return configured
    return os.environ.get("CHASEOS_HERMES_WSL_USER") or ""


def _dynamic_gateway_context(config: dict[str, Any], vault_root: Path | None = None) -> dict[str, str]:
    user_profile = _host_user_profile(vault_root)
    windows_vault = _windows_vault_path(vault_root)
    return {
        "windows_userprofile": user_profile,
        "windows_username": _host_username(vault_root),
        "windows_appdata": _host_appdata(vault_root),
        "windows_startup_dir": _host_startup_dir(vault_root),
        "windows_vault_path": windows_vault,
        "wsl_vault_path": _wsl_vault_path(vault_root),
        "wsl_distro": _wsl_distro_name(config),
        "wsl_user": _wsl_user_name(config),
    }


def _expand_dynamic_gateway_value(value: Any, context: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    expanded = value
    for key, replacement in context.items():
        expanded = expanded.replace("{" + key + "}", replacement)
    return _normalize_windows_path_text(expanded) if "\\" in expanded else expanded


def resolve_gateway_surface_config(config: dict[str, Any], vault_root: str | Path | None = None) -> dict[str, Any]:
    """Resolve portable Hermes/OpenClaw gateway placeholders for the current host.

    The lifecycle file can declare user-independent paths such as
    ``{windows_userprofile}`` and ``{wsl_vault_path}``. Resolution is local and
    does not launch WSL or read private runtime config.
    """
    resolved = dict(config or {})
    if str(resolved.get("path_resolution") or "").strip().lower() not in {"dynamic", "dynamic-user"}:
        return resolved
    root = Path(vault_root) if vault_root else ROOT
    context = _dynamic_gateway_context(resolved, root)
    for key in (
        "launcher_path",
        "target_path",
        "windows_vault_path",
        "coordination_daemon_loop_path",
        "coordination_daemon_log_path",
        "python_path",
        "wsl_distro",
        "wsl_user",
        "wsl_workdir",
        "wsl_command",
        "diagnostic_log_path",
    ):
        if key in resolved:
            resolved[key] = _expand_dynamic_gateway_value(resolved.get(key), context)
    if os.environ.get("CHASEOS_HERMES_WSL_DISTRO") or os.environ.get("WSL_DISTRO_NAME") or not str(resolved.get("wsl_distro") or "").strip():
        resolved["wsl_distro"] = context["wsl_distro"]
    if os.environ.get("CHASEOS_HERMES_WSL_USER") or "wsl_user" not in resolved:
        resolved["wsl_user"] = context["wsl_user"]
    resolved["path_resolution"] = "dynamic-user"
    resolved["path_resolution_context"] = {
        "windows_userprofile_resolved": bool(context["windows_userprofile"]),
        "windows_startup_dir_resolved": bool(context["windows_startup_dir"]),
        "windows_vault_path": context["windows_vault_path"],
        "wsl_vault_path": context["wsl_vault_path"],
        "wsl_distro": resolved.get("wsl_distro"),
        "wsl_user_mode": "explicit" if str(resolved.get("wsl_user") or "").strip() else "default-wsl-user",
        "launches_wsl_during_resolution": False,
        "reads_private_hermes_env": False,
    }
    return resolved


def _resolve_surface_config(runtime_id: str, surface_id: str, config: dict[str, Any]) -> dict[str, Any]:
    if str(config.get("current_state_source") or "") == "host_startup_file":
        return resolve_gateway_surface_config(config)
    return dict(config or {})


def _batch_ping_delay(seconds: int) -> str:
    # ping -n waits count - 1 seconds against loopback and works in minimized CMD.
    return f"ping -n {max(2, int(seconds) + 1)} 127.0.0.1 >nul"


def load_startup_surfaces_config(runtime_id: str) -> dict[str, Any]:
    record = load_lifecycle_record(runtime_id)
    surfaces = record.get("startup_surfaces") or {}
    if not isinstance(surfaces, dict):
        raise ValueError(f"startup_surfaces must be a mapping for runtime: {runtime_id}")
    return surfaces


def _gateway_launcher_profile(config: dict[str, Any]) -> dict[str, Any] | None:
    config = resolve_gateway_surface_config(config)
    launch_kind = str(config.get("launch_kind") or "").strip().lower()
    if not launch_kind:
        return None
    return {
        "launch_kind": launch_kind,
        "launcher_template_version": config.get("launcher_template_version"),
        "wsl_distro": config.get("wsl_distro"),
        "wsl_user": config.get("wsl_user"),
        "wsl_workdir": config.get("wsl_workdir"),
        "wsl_command": config.get("wsl_command"),
        "diagnostic_log_path": _normalize_windows_path_text(config.get("diagnostic_log_path")),
        "retry_attempts": _coerce_int(config.get("retry_attempts"), default=None),
        "retry_delay_seconds": _coerce_int(config.get("retry_delay_seconds"), default=None),
    }


def _gateway_target_launcher_contents(config: dict[str, Any]) -> str | None:
    config = resolve_gateway_surface_config(config)
    profile = _gateway_launcher_profile(config)
    if not profile or profile.get("launch_kind") != "wsl":
        return None

    target_path = str(config.get("target_path") or "").strip()
    target_dir = _normalize_windows_path_text(str(Path(target_path).parent)) if target_path else ""
    distro = str(profile.get("wsl_distro") or "").strip()
    user = str(profile.get("wsl_user") or "").strip()
    workdir = str(profile.get("wsl_workdir") or "").strip()
    command = str(profile.get("wsl_command") or "").strip()
    log_path = _normalize_windows_path_text(profile.get("diagnostic_log_path")).strip()
    retry_attempts = int(profile.get("retry_attempts") or 1)
    retry_delay = int(profile.get("retry_delay_seconds") or 0)
    version = str(profile.get("launcher_template_version") or "managed")
    vault_root = _normalize_windows_path_text(
        config.get("windows_vault_path")
        or _wsl_mount_path_to_windows(workdir)
        or str(ROOT)
    )
    daemon_loop_path = _normalize_windows_path_text(
        config.get("coordination_daemon_loop_path")
        or (f"{target_dir}\\hermes-daemon-loop.cmd" if target_dir else "")
    )
    daemon_log_path = _normalize_windows_path_text(
        config.get("coordination_daemon_log_path")
        or f"{vault_root}\\runtime\\lifecycle\\run\\hermes-daemon.log"
    )
    python_path = _normalize_windows_path_text(
        config.get("python_path")
        or f"{vault_root}\\.venv\\Scripts\\python.exe"
    )

    if not distro or not workdir or not command:
        return None

    log_lines = []
    if log_path:
        log_lines.extend(
            [
                f'set "HERMES_GATEWAY_LOG={log_path}"',
                'echo.>>"%HERMES_GATEWAY_LOG%"',
                'echo [%DATE% %TIME%] Hermes gateway startup begin>>"%HERMES_GATEWAY_LOG%"',
            ]
        )
    else:
        log_lines.append('set "HERMES_GATEWAY_LOG=NUL"')

    return "\r\n".join(
        [
            "@echo off",
            "setlocal EnableExtensions EnableDelayedExpansion",
            f"rem Hermes Gateway ({version})",
            'rem Starts the Windows-side ChaseOS Hermes daemon independently of the WSL messaging gateway.',
            'rem WSL startup supports both Store WSLService and legacy LxssManager hosts.',
            'set "HERMES_SERVICE_MARKER=hermes"',
            'set "HERMES_SERVICE_KIND=gateway"',
            'set "HERMES_GATEWAY_MODE=wsl-hidden-background"',
            f'set "VAULT_ROOT={vault_root}"',
            f'set "PYTHON_EXE={python_path}"',
            'if not exist "%PYTHON_EXE%" if exist "%VAULT_ROOT%\\.venv-win314\\Scripts\\python.exe" set "PYTHON_EXE=%VAULT_ROOT%\\.venv-win314\\Scripts\\python.exe"',
            'if not exist "%PYTHON_EXE%" if exist "%VAULT_ROOT%\\.venv-win\\Scripts\\python.exe" set "PYTHON_EXE=%VAULT_ROOT%\\.venv-win\\Scripts\\python.exe"',
            f'set "HERMES_DAEMON_LOOP={daemon_loop_path}"',
            f'set "HERMES_DAEMON_LOG={daemon_log_path}"',
            'set "HERMES_GATEWAY_RUNTIME_LOG=%USERPROFILE%\\.hermes\\gateway-runtime.log"',
            'set "HERMES_GATEWAY_RUNTIME_ERR=%USERPROFILE%\\.hermes\\gateway-runtime.err.log"',
            'set "HERMES_DAEMON_PROBE=%TEMP%\\chaseos-hermes-daemon-probe.txt"',
            f'set "HERMES_WSL_DISTRO={distro}"',
            f'set "HERMES_WSL_USER={user}"',
            'set "HERMES_WSL_USER_ARG="',
            'if defined HERMES_WSL_USER set "HERMES_WSL_USER_ARG=-u %HERMES_WSL_USER%"',
            f'set "HERMES_WSL_WORKDIR={workdir}"',
            f'set "HERMES_WSL_COMMAND=cd "{workdir}" && {command}"',
            *log_lines,
            'echo [%DATE% %TIME%] Hermes gateway launcher does not auto-start the ChaseOS coordination daemon; daemon startup is controlled by Studio runtime controls or Task Scheduler only>>"%HERMES_GATEWAY_LOG%"',
            'echo [%DATE% %TIME%] WSL service diagnostics begin>>"%HERMES_GATEWAY_LOG%"',
            'for %%S in (WSLService LxssManager) do (',
            '  sc query "%%S" >nul 2>&1',
            '  if "!ERRORLEVEL!"=="0" (',
            '    sc query "%%S" | find "RUNNING" >nul 2>&1',
            '    if "!ERRORLEVEL!"=="0" (',
            '      echo [%DATE% %TIME%] %%S RUNNING>>"%HERMES_GATEWAY_LOG%"',
            '    ) else (',
            '      echo [%DATE% %TIME%] %%S present but not RUNNING; wsl.exe will attempt startup>>"%HERMES_GATEWAY_LOG%"',
            '    )',
            '  ) else (',
            '    echo [%DATE% %TIME%] %%S not installed or not visible>>"%HERMES_GATEWAY_LOG%"',
            '  )',
            ')',
            'echo [%DATE% %TIME%] warming WSL distro %HERMES_WSL_DISTRO%>>"%HERMES_GATEWAY_LOG%"',
            f"for /L %%I in (1,1,{retry_attempts}) do (",
            '  echo [%DATE% %TIME%] wake attempt %%I: hidden wsl.exe -d %HERMES_WSL_DISTRO%>>"%HERMES_GATEWAY_LOG%"',
            "  powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command \"$psi=[System.Diagnostics.ProcessStartInfo]::new(); $psi.FileName='wsl.exe'; $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true; $psi.WindowStyle=[System.Diagnostics.ProcessWindowStyle]::Hidden; $userArg=''; if ($env:HERMES_WSL_USER) { $userArg=' -u ' + $env:HERMES_WSL_USER }; $psi.Arguments='-d ' + $env:HERMES_WSL_DISTRO + $userArg + ' -- bash -lc \\\"cd \\\\\\\"' + $env:HERMES_WSL_WORKDIR + '\\\\\\\" && true\\\"'; $p=[System.Diagnostics.Process]::Start($psi); $p.WaitForExit(); exit $p.ExitCode\" >>\"%HERMES_GATEWAY_LOG%\" 2>&1",
            '  set "HERMES_WAKE_EXIT=!ERRORLEVEL!"',
            '  echo [%DATE% %TIME%] wake attempt %%I exit !HERMES_WAKE_EXIT!>>"%HERMES_GATEWAY_LOG%"',
            '  if "!HERMES_WAKE_EXIT!"=="0" goto hermes_wsl_ready',
            f"  if %%I LSS {retry_attempts} {_batch_ping_delay(retry_delay)}",
            ")",
            'echo [%DATE% %TIME%] ERROR: WSL distro %HERMES_WSL_DISTRO% did not start; gateway skipped>>"%HERMES_GATEWAY_LOG%"',
            "exit /b !HERMES_WAKE_EXIT!",
            ":hermes_wsl_ready",
            'echo [%DATE% %TIME%] checking for an existing Hermes gateway process via hidden WSL>>"%HERMES_GATEWAY_LOG%"',
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command \"$psi=[System.Diagnostics.ProcessStartInfo]::new(); $psi.FileName='wsl.exe'; $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true; $psi.WindowStyle=[System.Diagnostics.ProcessWindowStyle]::Hidden; $userArg=''; if ($env:HERMES_WSL_USER) { $userArg=' -u ' + $env:HERMES_WSL_USER }; $psi.Arguments='-d ' + $env:HERMES_WSL_DISTRO + $userArg + ' -- pgrep -af \\\"gateway run\\\"'; $p=[System.Diagnostics.Process]::Start($psi); $p.WaitForExit(); exit $p.ExitCode\" >>\"%HERMES_GATEWAY_LOG%\" 2>&1",
            'if "!ERRORLEVEL!"=="0" (',
            '  echo [%DATE% %TIME%] Hermes gateway already running; foreground launch skipped>>"%HERMES_GATEWAY_LOG%"',
            '  exit /b 0',
            ')',
            'echo [%DATE% %TIME%] WSL distro %HERMES_WSL_DISTRO% ready; launching Hermes gateway hidden/background>>"%HERMES_GATEWAY_LOG%"',
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command \"$cmd=$env:HERMES_WSL_COMMAND.Replace('\\\"','\\\\\\\"'); $psi=[System.Diagnostics.ProcessStartInfo]::new(); $psi.FileName='wsl.exe'; $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true; $psi.WindowStyle=[System.Diagnostics.ProcessWindowStyle]::Hidden; $userArg=''; if ($env:HERMES_WSL_USER) { $userArg=' -u ' + $env:HERMES_WSL_USER }; $psi.Arguments='-d ' + $env:HERMES_WSL_DISTRO + $userArg + ' -- bash -lc \\\"' + $cmd + '\\\"'; [System.Diagnostics.Process]::Start($psi) | Out-Null\" >>\"%HERMES_GATEWAY_LOG%\" 2>&1",
            'set "HERMES_EXIT=!ERRORLEVEL!"',
            'echo [%DATE% %TIME%] hidden gateway launch exit !HERMES_EXIT!>>"%HERMES_GATEWAY_LOG%"',
            'exit /b !HERMES_EXIT!',
            "",
        ]
    )


def _gateway_startup_launcher_contents(config: dict[str, Any]) -> str | None:
    target_path = str(config.get("target_path") or "").strip()
    ui_label = str(config.get("ui_label") or "Runtime Gateway").strip()
    if not target_path:
        return None
    normalized_target = _normalize_windows_path_text(target_path)
    return "\r\n".join(
        [
            "Option Explicit",
            f"' ChaseOS managed Startup hidden delegate for {ui_label}",
            "' Terminal-spam guard: keep the Startup-folder entry out of cmd.exe/conhost.exe.",
            "Dim shell, target",
            'Set shell = CreateObject("WScript.Shell")',
            f'target = "{normalized_target}"',
            'shell.Run "\"" & target & "\"", 0, False',
            "",
        ]
    )


def _gateway_launcher_drift(config: dict[str, Any]) -> dict[str, Any] | None:
    expected = _gateway_target_launcher_contents(config)
    target_path = config.get("target_path")
    if expected is None or not target_path:
        return None
    current = _read_text_if_present(target_path)
    expected_digest = _json_digest({"launcher_contents": _normalize_cmd_text(expected)})
    current_digest = _json_digest({"launcher_contents": _normalize_cmd_text(current)}) if current is not None else None
    return {
        "managed": True,
        "target_path": target_path,
        "target_present": current is not None,
        "expected_sha256": expected_digest,
        "current_sha256": current_digest,
        "matches_expected": current is not None and _normalize_cmd_text(current) == _normalize_cmd_text(expected),
    }


def _base_surface(surface_id: str, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "surface_id": str(config.get("surface_id") or surface_id),
        "surface_type": str(config.get("surface_type") or surface_id),
        "ui_label": str(config.get("ui_label") or surface_id.replace("_", " ").title()),
        "supported": _coerce_bool(config.get("supported"), default=True),
        "toggle_supported": _coerce_bool(config.get("toggle_supported"), default=False),
        "mutation_status": str(config.get("mutation_status") or "planned"),
        "current_state_source": str(config.get("current_state_source") or "declared"),
        "startup_registration_kind": str(config.get("startup_registration_kind") or "none"),
        "status_command": config.get("status_command"),
        "enable_command": config.get("enable_command"),
        "disable_command": config.get("disable_command"),
        "notes": config.get("notes"),
        "read_only": True,
        "mutation_enabled": False,
    }


def _gateway_surface_state(surface_id: str, config: dict[str, Any]) -> dict[str, Any]:
    launcher_path = config.get("launcher_path")
    target_path = config.get("target_path")
    launcher_present = _path_present(launcher_path)
    target_present = _path_present(target_path)
    configured = launcher_present
    registered = launcher_present if config.get("startup_registration_kind") == "windows-startup-folder" else False
    surface = _base_surface(surface_id, config)
    launch_profile = _gateway_launcher_profile(config)
    launcher_drift = _gateway_launcher_drift(config)
    degraded = bool(launcher_drift and launcher_drift.get("target_present") and not launcher_drift.get("matches_expected"))
    if degraded:
        state = "degraded"
    else:
        state = "registered" if registered else ("configured" if configured else "off")
    evidence = {
        "launcher_path": launcher_path,
        "launcher_present": launcher_present,
        "target_path": target_path,
        "target_present": target_present,
    }
    if config.get("path_resolution_context"):
        evidence["path_resolution"] = config.get("path_resolution_context")
    if launch_profile:
        evidence["launch_profile"] = launch_profile
    if launcher_drift:
        evidence["managed_launcher"] = launcher_drift

    surface.update(
        {
            "state": state,
            "configured": configured,
            "registered": registered,
            "running": None,
            "proven_after_reboot": False,
            "degraded": degraded,
            "proof_state": state,
            "evidence": evidence,
            "proof_boundary": "Startup-folder presence is configuration/registration evidence only; it is not process liveness or post-reboot proof.",
        }
    )
    return surface


def _supervisor_surface_state(
    runtime_id: str,
    surface_id: str,
    config: dict[str, Any],
    supervisor_status: dict[str, Any],
) -> dict[str, Any]:
    configured = bool(supervisor_status.get("supervision_enabled"))
    running = bool(supervisor_status.get("running"))
    degraded = bool(supervisor_status.get("stale_state")) or (configured and bool(supervisor_status.get("state_present")) and not running)
    if running:
        state = "running"
    elif degraded:
        state = "degraded"
    elif configured:
        state = "configured"
    else:
        state = "off"

    surface = _base_surface(surface_id, config)
    surface.update(
        {
            "state": state,
            "configured": configured,
            "registered": None,
            "running": running,
            "proven_after_reboot": False,
            "degraded": degraded,
            "proof_state": state,
            "evidence": {
                "state_file": supervisor_status.get("state_file"),
                "state_present": supervisor_status.get("state_present"),
                "log_file": supervisor_status.get("log_file"),
                "pid": supervisor_status.get("pid"),
                "started_at": supervisor_status.get("started_at"),
            },
            "source_status": supervisor_status,
        }
    )
    return surface


def _bootstrap_surface_state(
    runtime_id: str,
    surface_id: str,
    config: dict[str, Any],
    activation_report: dict[str, Any],
) -> dict[str, Any]:
    checks = dict(activation_report.get("checks") or {})
    proof = dict(activation_report.get("activation_proof") or {})
    configured = bool(checks.get("installed"))
    registered = bool(checks.get("scheduler_registered"))
    running = bool(checks.get("supervisor_running"))
    heartbeat_fresh = bool(checks.get("heartbeat_fresh"))
    proven_after_reboot = bool(activation_report.get("proof_complete"))
    degraded = registered and (not running or not heartbeat_fresh)

    if proven_after_reboot:
        state = "proven-after-reboot"
    elif degraded:
        state = "degraded"
    elif running and heartbeat_fresh:
        state = "running"
    elif registered:
        state = "registered"
    elif configured:
        state = "configured"
    else:
        state = "off"

    surface = _base_surface(surface_id, config)
    surface.update(
        {
            "state": state,
            "configured": configured,
            "registered": registered,
            "running": running,
            "proven_after_reboot": proven_after_reboot,
            "degraded": degraded,
            "proof_state": activation_report.get("activation_state") or state,
            "evidence": {
                "task_name": activation_report.get("task_name"),
                "registration_kind": activation_report.get("registration_kind"),
                "proof_ready": activation_report.get("proof_ready"),
                "proof_complete": activation_report.get("proof_complete"),
                "missing_evidence": proof.get("missing_evidence") or [],
                "evidence_paths": proof.get("evidence_paths") or {},
            },
            "source_report": activation_report,
        }
    )
    return surface


def _declared_surface_state(surface_id: str, config: dict[str, Any]) -> dict[str, Any]:
    surface = _base_surface(surface_id, config)
    surface.update(
        {
            "state": "configured" if surface["supported"] else "unsupported",
            "configured": surface["supported"],
            "registered": None,
            "running": None,
            "proven_after_reboot": False,
            "degraded": False,
            "proof_state": "declared-only",
            "evidence": {},
        }
    )
    return surface


def _passive_declared_surface_state(surface_id: str, config: dict[str, Any], *, skip_reason: str) -> dict[str, Any]:
    surface = _declared_surface_state(surface_id, config)
    surface["proof_state"] = "declared-only-live-process-probe-skipped"
    surface["evidence"] = {
        "live_process_probe_skipped": True,
        "skip_reason": skip_reason,
        "status_command": surface.get("status_command"),
    }
    return surface


def build_runtime_startup_surfaces_report(runtime_id: str, *, probe_processes: bool = False) -> dict[str, Any]:
    record = load_lifecycle_record(runtime_id)
    runtime_name = str((record.get("coordination_watch") or {}).get("runtime_name") or runtime_id)
    surfaces_config = load_startup_surfaces_config(runtime_id)

    supervisor_status: dict[str, Any] | None = None
    activation_report: dict[str, Any] | None = None
    surfaces: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for surface_id, raw_config in surfaces_config.items():
        config = _resolve_surface_config(runtime_id, str(surface_id), dict(raw_config or {}))
        source = str(config.get("current_state_source") or "").strip()
        try:
            if source == "host_startup_file":
                surface = _gateway_surface_state(str(surface_id), config)
            elif source == "coordination_watch_supervisor":
                if probe_processes:
                    if supervisor_status is None:
                        supervisor_status = get_supervised_coordination_watch_status(runtime_id)
                    surface = _supervisor_surface_state(runtime_id, str(surface_id), config, supervisor_status)
                else:
                    surface = _passive_declared_surface_state(
                        str(surface_id),
                        config,
                        skip_reason="process_probing_disabled_for_passive_page_load",
                    )
            elif source == "coordination_watch_bootstrap":
                if probe_processes:
                    if activation_report is None:
                        activation_report = build_coordination_watch_activation_report(runtime_id)
                    surface = _bootstrap_surface_state(runtime_id, str(surface_id), config, activation_report)
                else:
                    surface = _passive_declared_surface_state(
                        str(surface_id),
                        config,
                        skip_reason="process_probing_disabled_for_passive_page_load",
                    )
            else:
                surface = _declared_surface_state(str(surface_id), config)
        except Exception as exc:
            surface = _base_surface(str(surface_id), config)
            surface.update(
                {
                    "state": "degraded",
                    "configured": False,
                    "registered": None,
                    "running": None,
                    "proven_after_reboot": False,
                    "degraded": True,
                    "proof_state": "error",
                    "evidence": {},
                    "error": str(exc),
                }
            )
            errors.append({"surface_id": str(surface_id), "error": str(exc)})
        surfaces.append(surface)

    return {
        "runtime_id": runtime_id,
        "runtime_name": runtime_name,
        "platform": record.get("platform"),
        "lifecycle_mode": record.get("lifecycle_mode"),
        "surface_count": len(surfaces),
        "toggle_supported_count": sum(1 for surface in surfaces if surface.get("toggle_supported")),
        "ui_ready": bool(surfaces),
        "read_only": True,
        "process_probe_enabled": bool(probe_processes),
        "mutation_enabled": False,
        "surfaces": surfaces,
        "errors": errors,
    }


def build_startup_surfaces_report(runtime_id: str | None = None, *, probe_processes: bool = False) -> dict[str, Any]:
    target = (runtime_id or "all").strip().lower()
    runtime_ids = list_lifecycle_runtime_ids() if target in {"", "all", "*"} else [target]

    runtimes = []
    errors: list[dict[str, str]] = []
    for rid in runtime_ids:
        try:
            runtimes.append(build_runtime_startup_surfaces_report(rid, probe_processes=probe_processes))
        except Exception as exc:
            errors.append({"runtime_id": rid, "error": str(exc)})

    surfaces = [surface for runtime in runtimes for surface in runtime.get("surfaces", [])]
    return {
        "action": "startup-surfaces",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "read_only": True,
        "process_probe_enabled": bool(probe_processes),
        "mutation_enabled": False,
        "runtime_count": len(runtimes),
        "surface_count": len(surfaces),
        "toggle_supported_count": sum(1 for surface in surfaces if surface.get("toggle_supported")),
        "summary": {
            "configured": sum(1 for surface in surfaces if surface.get("configured")),
            "registered": sum(1 for surface in surfaces if surface.get("registered") is True),
            "running": sum(1 for surface in surfaces if surface.get("running") is True),
            "degraded": sum(1 for surface in surfaces if surface.get("degraded")),
            "proven_after_reboot": sum(1 for surface in surfaces if surface.get("proven_after_reboot")),
        },
        "runtimes": runtimes,
        "errors": errors,
        "boundary": "Report only. It does not enable, disable, register, unregister, start, stop, or mutate host startup state.",
    }


def _settings_entry(runtime_id: str, surface: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    surface_id = str(surface.get("surface_id") or "")
    launch_profile = _gateway_launcher_profile(config)
    launcher_contents = _gateway_target_launcher_contents(config)
    managed_launcher = dict((surface.get("evidence") or {}).get("managed_launcher") or {})
    entry = {
        "surface_id": surface_id,
        "ui_label": surface.get("ui_label"),
        "control_type": "toggle" if surface.get("toggle_supported") else "status",
        "user_manageable": bool(surface.get("toggle_supported")),
        "current_state": surface.get("state"),
        "startup_registration_kind": surface.get("startup_registration_kind") or config.get("startup_registration_kind"),
        "desired_state_persistence": "startup-surface-mutation-log",
        "cli_mutation_enabled": bool(surface.get("toggle_supported")),
        "studio_cli_control_enabled": bool(surface.get("toggle_supported")),
        "studio_local_app_control_enabled": bool(surface.get("toggle_supported")),
        "studio_visual_toggle_built": bool(surface.get("toggle_supported")),
        "studio_mutation_enabled": False,
        "settings_write_enabled": bool(surface.get("toggle_supported")),
        "read_only": True,
        "commands": {
            "status": f"chaseos runtime startup-surfaces --runtime {runtime_id} --json",
            "enable": f"chaseos runtime startup-surface-toggle --runtime {runtime_id} --surface {surface_id} --intent enable --confirm",
            "disable": f"chaseos runtime startup-surface-toggle --runtime {runtime_id} --surface {surface_id} --intent disable --confirm",
            "dry_run_enable": f"chaseos runtime startup-surface-toggle --runtime {runtime_id} --surface {surface_id} --intent enable --dry-run --json",
            "dry_run_disable": f"chaseos runtime startup-surface-toggle --runtime {runtime_id} --surface {surface_id} --intent disable --dry-run --json",
            "plan_enable": f"chaseos runtime startup-surface-toggle-plan --runtime {runtime_id} --surface {surface_id} --intent enable --json",
            "plan_disable": f"chaseos runtime startup-surface-toggle-plan --runtime {runtime_id} --surface {surface_id} --intent disable --json",
            "contract_enable": f"chaseos runtime startup-surface-mutation-contract --runtime {runtime_id} --surface {surface_id} --intent enable --json",
            "contract_disable": f"chaseos runtime startup-surface-mutation-contract --runtime {runtime_id} --surface {surface_id} --intent disable --json",
            "executor_preflight": f"chaseos runtime startup-surface-executor-preflight --runtime {runtime_id} --surface {surface_id} --intent <enable|disable> --gate-approval-id <id> --plan-digest <sha256> --json",
        },
        "studio_contract": {
            "view": "Runtime Cockpit",
            "control": "startup-surface-toggle",
            "must_show_current_state": True,
            "must_show_proof_boundary": True,
            "must_require_operator_confirmation": True,
            "must_use_service_layer": True,
        },
        "blocked_until": [
            "broad ChaseOS Studio desktop Runtime Cockpit adopts the localhost app/service contract",
            "approval-artifact flow replaces direct --confirm for higher-risk managed deployments",
        ],
    }
    if launch_profile:
        entry["launch_profile"] = launch_profile
    if launcher_contents is not None:
        entry["managed_target_launcher"] = {
            **managed_launcher,
            "expected_contents": launcher_contents,
        }
    return entry


def build_startup_surface_settings_report(runtime_id: str | None = None, *, probe_processes: bool = False) -> dict[str, Any]:
    """Build the user/Studio-facing startup control settings model without mutating settings."""
    target = (runtime_id or "all").strip().lower()
    runtime_ids = list_lifecycle_runtime_ids() if target in {"", "all", "*"} else [target]

    runtimes: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for rid in runtime_ids:
        try:
            runtime_report = build_runtime_startup_surfaces_report(rid, probe_processes=probe_processes)
            configs = load_startup_surfaces_config(rid)
            settings = []
            for surface in runtime_report.get("surfaces", []):
                surface_id = str(surface.get("surface_id") or "")
                settings.append(_settings_entry(rid, surface, dict(configs.get(surface_id) or {})))
            runtimes.append(
                {
                    "runtime_id": runtime_report.get("runtime_id"),
                    "runtime_name": runtime_report.get("runtime_name"),
                    "platform": runtime_report.get("platform"),
                    "lifecycle_mode": runtime_report.get("lifecycle_mode"),
                    "ui_ready": runtime_report.get("ui_ready"),
                    "read_only": True,
                    "settings_write_enabled": any(setting.get("settings_write_enabled") for setting in settings),
                    "mutation_enabled": any(setting.get("cli_mutation_enabled") for setting in settings),
                    "settings": settings,
                    "errors": runtime_report.get("errors") or [],
                }
            )
        except Exception as exc:
            errors.append({"runtime_id": rid, "error": str(exc)})

    return {
        "action": "startup-surface-settings",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "read_only": True,
        "process_probe_enabled": bool(probe_processes),
        "settings_write_enabled": any(runtime.get("settings_write_enabled") for runtime in runtimes),
        "mutation_enabled": any(runtime.get("mutation_enabled") for runtime in runtimes),
        "runtime_count": len(runtimes),
        "surface_count": sum(len(runtime.get("settings") or []) for runtime in runtimes),
        "runtimes": runtimes,
        "errors": errors,
        "boundary": "Settings model only. Use startup-surface-toggle with --confirm for runtime CLI mutation, studio runtime-startup-controls --action toggle --confirm-action for the Studio CLI wrapper, or studio runtime-startup-controls-app for the localhost visual wrapper. Broad Studio desktop integration and approval-artifact consumption remain unbuilt.",
    }


def _surface_config(runtime_id: str, surface_id: str) -> dict[str, Any]:
    configs = load_startup_surfaces_config(runtime_id)
    config = configs.get(surface_id)
    if not isinstance(config, dict):
        available = ", ".join(str(item) for item in configs)
        raise ValueError(f"unknown startup surface {surface_id!r}; available surfaces: {available}")
    return _resolve_surface_config(runtime_id, surface_id, dict(config))


def _startup_surface_write_targets(config: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    source = str(config.get("current_state_source") or "")
    if source == "host_startup_file":
        for key in ("launcher_path", "target_path"):
            value = config.get(key)
            if value:
                targets.append(str(value))
    targets.append(str(STARTUP_SURFACE_MUTATION_DIR / "events.jsonl"))
    return list(dict.fromkeys(targets))


def build_startup_surface_gate_context(runtime_id: str, surface_id: str, intent: str) -> dict[str, Any]:
    """Return Gate policy inputs for a concrete startup surface toggle."""
    normalized_runtime = (runtime_id or "").strip().lower()
    normalized_surface = (surface_id or "").strip()
    normalized_intent = (intent or "").strip().lower()
    if not normalized_runtime or normalized_runtime in {"all", "*"}:
        raise ValueError("startup-surface toggle requires one concrete --runtime value")
    if not normalized_surface:
        raise ValueError("startup-surface toggle requires --surface")
    if normalized_intent not in VALID_TOGGLE_INTENTS:
        raise ValueError("startup-surface toggle requires --intent enable|disable")

    config = _surface_config(normalized_runtime, normalized_surface)
    runtime_report = build_runtime_startup_surfaces_report(normalized_runtime)
    surface = _find_surface(runtime_report, normalized_surface)
    if surface is None:
        raise ValueError(f"unknown startup surface {normalized_surface!r}")
    return {
        "runtime_id": normalized_runtime,
        "surface_id": normalized_surface,
        "intent": normalized_intent,
        "operation": _future_gate_operation(surface, normalized_intent),
        "external_api": _future_external_api(surface),
        "external_side_effect": True,
        "write_targets": _startup_surface_write_targets(config),
    }


def _safe_runtime_slug(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    return "".join(char for char in text if char.isalnum() or char == "-") or "runtime"


def _mutation_id(runtime_id: str, surface_id: str, intent: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = _json_digest({"runtime_id": runtime_id, "surface_id": surface_id, "intent": intent, "generated_at": stamp})[:10]
    return f"startup-surface-{_safe_runtime_slug(runtime_id)}-{_safe_runtime_slug(surface_id)}-{intent}-{stamp}-{digest}"


def _inline_startup_approval_record(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    requested_by: str,
    confirmed: bool,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "approval_kind": "inline_operator_confirmation",
        "approval_required": True,
        "approval_recorded": bool(confirmed and not dry_run),
        "approval_scope": "runtime_startup_surface_host_mutation",
        "requested_by": requested_by,
        "runtime_id": runtime_id,
        "surface_id": surface_id,
        "intent": intent,
        "confirmed": bool(confirmed),
        "dry_run": bool(dry_run),
        "recorded_at_utc": _utc_now_iso() if confirmed and not dry_run else None,
        "note": "Live startup-surface toggles require explicit operator confirmation and write this approval record to the mutation marker/event log.",
    }


def _write_text_with_parent(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8", newline="")


def _read_backup_text(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def _append_startup_surface_event(event: dict[str, Any]) -> Path:
    STARTUP_SURFACE_MUTATION_DIR.mkdir(parents=True, exist_ok=True)
    event_path = STARTUP_SURFACE_MUTATION_DIR / "events.jsonl"
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")
    return event_path


def _write_startup_surface_marker(mutation_id: str, payload: dict[str, Any]) -> Path:
    STARTUP_SURFACE_MUTATION_DIR.mkdir(parents=True, exist_ok=True)
    marker_path = STARTUP_SURFACE_MUTATION_DIR / f"{mutation_id}.json"
    marker_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return marker_path


def _execute_gateway_toggle(config: dict[str, Any], intent: str, dry_run: bool) -> dict[str, Any]:
    launcher_path = _resolve_path(config.get("launcher_path"))
    target_path = _resolve_path(config.get("target_path"))
    if launcher_path is None:
        raise ValueError("gateway startup surface is missing launcher_path")
    startup_contents = _gateway_startup_launcher_contents(config)
    target_contents = _gateway_target_launcher_contents(config)
    if intent == "enable" and startup_contents is None:
        raise ValueError("gateway startup surface is missing target_path")

    actions: list[dict[str, Any]] = []
    backups: dict[str, str | None] = {}
    if target_path is not None and target_contents is not None:
        backups["target_path_previous_contents"] = _read_backup_text(target_path)
        actions.append(
            {
                "action": "write-managed-target-launcher",
                "path": str(target_path),
                "attempted": not dry_run,
            }
        )
        if not dry_run:
            _write_text_with_parent(target_path, target_contents)

    backups["launcher_path_previous_contents"] = _read_backup_text(launcher_path)
    if intent == "enable":
        actions.append(
            {
                "action": "write-startup-folder-launcher",
                "path": str(launcher_path),
                "attempted": not dry_run,
            }
        )
        if not dry_run:
            _write_text_with_parent(launcher_path, startup_contents or "")
    else:
        actions.append(
            {
                "action": "remove-startup-folder-launcher",
                "path": str(launcher_path),
                "present_before": launcher_path.exists(),
                "attempted": not dry_run,
            }
        )
        if not dry_run and launcher_path.exists():
            launcher_path.unlink()

    return {
        "surface_executor": "host_startup_file",
        "actions": actions,
        "backups_captured": sorted(key for key, value in backups.items() if value is not None),
        "target_path": str(target_path) if target_path else None,
        "launcher_path": str(launcher_path),
    }


def _execute_supervisor_toggle(runtime_id: str, intent: str, dry_run: bool, interval_seconds: int | None) -> dict[str, Any]:
    action = "start" if intent == "enable" else "stop"
    result: dict[str, Any] | None = None
    if not dry_run:
        if action == "start":
            result = start_supervised_coordination_watch(runtime_id, interval_seconds=interval_seconds)
        else:
            result = stop_supervised_coordination_watch(runtime_id)
    return {
        "surface_executor": "coordination_watch_supervisor",
        "action": action,
        "attempted": not dry_run,
        "result": result,
    }


def _execute_bootstrap_toggle(runtime_id: str, intent: str, dry_run: bool) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    if intent == "enable":
        actions.append({"action": "install", "attempted": not dry_run})
        actions.append({"action": "apply", "attempted": not dry_run})
        install_result = None
        apply_result = None
        if not dry_run:
            install_result = install_coordination_watch_bootstrap(runtime_id)
            apply_result = apply_coordination_watch_bootstrap(runtime_id)
        return {
            "surface_executor": "coordination_watch_bootstrap",
            "actions": actions,
            "install_result": install_result,
            "apply_result": apply_result,
        }

    actions.append({"action": "unregister", "attempted": not dry_run})
    unregister_result = None
    if not dry_run:
        unregister_result = unregister_coordination_watch_bootstrap(runtime_id)
    return {
        "surface_executor": "coordination_watch_bootstrap",
        "actions": actions,
        "unregister_result": unregister_result,
    }


def execute_startup_surface_toggle(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    confirm: bool = False,
    dry_run: bool = False,
    requested_by: str = "operator",
    interval_seconds: int | None = None,
) -> dict[str, Any]:
    """Enable or disable one declared startup surface through the lifecycle model."""
    normalized_runtime = (runtime_id or "").strip().lower()
    normalized_surface = (surface_id or "").strip()
    normalized_intent = (intent or "").strip().lower()
    if normalized_intent not in VALID_TOGGLE_INTENTS:
        raise ValueError("startup-surface toggle requires --intent enable|disable")
    if not dry_run and not confirm:
        raise ValueError("startup-surface toggle requires --confirm for live enable/disable")

    plan = build_startup_surface_toggle_plan(normalized_runtime, normalized_surface, normalized_intent)
    surface = dict(plan.get("surface") or {})
    config = _surface_config(normalized_runtime, normalized_surface)
    supported = bool(surface.get("supported"))
    toggle_supported = bool(surface.get("toggle_supported"))
    before = build_runtime_startup_surfaces_report(normalized_runtime)
    before_surface = _find_surface(before, normalized_surface) or {}
    mutation_id = _mutation_id(normalized_runtime, normalized_surface, normalized_intent)
    approval_record = _inline_startup_approval_record(
        normalized_runtime,
        normalized_surface,
        normalized_intent,
        requested_by=requested_by,
        confirmed=confirm,
        dry_run=dry_run,
    )

    if not supported or not toggle_supported:
        raise ValueError(f"startup surface {normalized_surface!r} does not support toggles")

    source = str(surface.get("current_state_source") or "")
    execution: dict[str, Any]
    if source == "host_startup_file":
        execution = _execute_gateway_toggle(config, normalized_intent, dry_run)
    elif source == "coordination_watch_supervisor":
        execution = _execute_supervisor_toggle(normalized_runtime, normalized_intent, dry_run, interval_seconds)
    elif source == "coordination_watch_bootstrap":
        execution = _execute_bootstrap_toggle(normalized_runtime, normalized_intent, dry_run)
    else:
        raise ValueError(f"startup surface {normalized_surface!r} has no executor for source {source!r}")

    after = build_runtime_startup_surfaces_report(normalized_runtime)
    after_surface = _find_surface(after, normalized_surface) or {}
    marker_payload = {
        "schema_version": 1,
        "mutation_id": mutation_id,
        "generated_at_utc": _utc_now_iso(),
        "requested_by": requested_by,
        "runtime_id": normalized_runtime,
        "surface_id": normalized_surface,
        "intent": normalized_intent,
        "dry_run": dry_run,
        "confirmed": confirm,
        "before_state": before_surface.get("state"),
        "after_state": after_surface.get("state"),
        "target_state": plan.get("target_state"),
        "gate_operation": _future_gate_operation(surface, normalized_intent),
        "external_api": _future_external_api(surface),
        "write_targets": _startup_surface_write_targets(config),
        "approval_record": approval_record,
        "execution": execution,
    }
    event_path = None
    marker_path = None
    if not dry_run:
        marker_path = _write_startup_surface_marker(mutation_id, marker_payload)
        event_path = _append_startup_surface_event(
            {
                "event_type": "startup_surface_toggle",
                "mutation_id": mutation_id,
                "generated_at_utc": marker_payload["generated_at_utc"],
                "runtime_id": normalized_runtime,
                "surface_id": normalized_surface,
                "intent": normalized_intent,
                "before_state": before_surface.get("state"),
                "after_state": after_surface.get("state"),
                "target_state": plan.get("target_state"),
                "approval_required": approval_record["approval_required"],
                "approval_recorded": approval_record["approval_recorded"],
                "approval_kind": approval_record["approval_kind"],
            }
        )

    return {
        "action": "startup-surface-toggle",
        "schema_version": 1,
        "generated_at_utc": marker_payload["generated_at_utc"],
        "runtime_id": normalized_runtime,
        "runtime_name": plan.get("runtime_name"),
        "surface_id": normalized_surface,
        "intent": normalized_intent,
        "confirmed": confirm,
        "dry_run": dry_run,
        "read_only": False,
        "mutation_enabled": True,
        "executes_mutation": not dry_run,
        "mutation_id": mutation_id,
        "approval_required": approval_record["approval_required"],
        "approval_recorded": approval_record["approval_recorded"],
        "approval_record": approval_record,
        "before_state": before_surface.get("state"),
        "after_state": after_surface.get("state"),
        "target_state": plan.get("target_state"),
        "target_reached": after_surface.get("state") == plan.get("target_state"),
        "marker_path": str(marker_path) if marker_path else None,
        "event_log_path": str(event_path) if event_path else None,
        "gate_operation": marker_payload["gate_operation"],
        "external_api": marker_payload["external_api"],
        "write_targets": marker_payload["write_targets"],
        "execution": execution,
        "verification_commands": _verification_commands(normalized_runtime, surface),
        "source_plan": plan,
        "boundary": "Mutates only the selected declared startup surface. It does not change runtime permissions, secrets, workflow authority, or canonical knowledge.",
    }


def _find_surface(runtime_report: dict[str, Any], surface_id: str) -> dict[str, Any] | None:
    for surface in runtime_report.get("surfaces", []):
        if surface.get("surface_id") == surface_id:
            return surface
    return None


def _plan_steps_for_surface(runtime_id: str, surface: dict[str, Any], intent: str) -> list[dict[str, Any]]:
    source = str(surface.get("current_state_source") or "")
    surface_id = str(surface.get("surface_id") or "")
    status_command = surface.get("status_command")

    if source == "host_startup_file":
        if intent == "enable":
            return [
                {
                    "step_id": "verify-target-launcher",
                    "kind": "read",
                    "implemented": True,
                    "description": "Confirm the declared gateway target launcher exists before any future Startup-folder registration.",
                    "mutates": False,
                },
                {
                    "step_id": "register-startup-launcher",
                    "kind": "service-layer-mutation",
                    "implemented": True,
                    "description": "Create or repair the declared Windows Startup-folder launcher through startup-surface-toggle.",
                    "mutates": True,
                },
                {
                    "step_id": "verify-startup-surface",
                    "kind": "read",
                    "implemented": True,
                    "command": status_command,
                    "description": "Re-run the startup-surfaces report and confirm the gateway surface is registered.",
                    "mutates": False,
                },
            ]
        return [
            {
                "step_id": "remove-startup-launcher",
                "kind": "service-layer-mutation",
                "implemented": True,
                "description": "Remove the declared Windows Startup-folder launcher through startup-surface-toggle.",
                "mutates": True,
            },
            {
                "step_id": "verify-startup-surface",
                "kind": "read",
                "implemented": True,
                "command": status_command,
                "description": "Re-run the startup-surfaces report and confirm the gateway surface is off.",
                "mutates": False,
            },
        ]

    if source == "coordination_watch_supervisor":
        action = "start" if intent == "enable" else "stop"
        return [
            {
                "step_id": f"{action}-supervisor",
                "kind": "service-layer-mutation",
                "implemented": True,
                "command": f"chaseos runtime coordination-watch-supervisor --runtime {runtime_id} --action {action} --json",
                "description": f"Run the coordination-watch supervisor {action} action through startup-surface-toggle.",
                "mutates": True,
            },
            {
                "step_id": "verify-supervisor",
                "kind": "read",
                "implemented": True,
                "command": status_command,
                "description": "Check current-session supervisor state after the future mutation.",
                "mutates": False,
            },
        ]

    if source == "coordination_watch_bootstrap":
        if intent == "enable":
            return [
                {
                    "step_id": "review-activation-checklist",
                    "kind": "read",
                    "implemented": True,
                    "command": surface.get("enable_command"),
                    "description": "Review the activation checklist and missing proof before any host registration.",
                    "mutates": False,
                },
                {
                    "step_id": "apply-or-handoff-registration",
                    "kind": "service-layer-mutation",
                    "implemented": True,
                    "command": f"chaseos runtime coordination-watch-bootstrap --runtime {runtime_id} --action apply --json",
                    "description": "Register the declared Task Scheduler startup entry through startup-surface-toggle; host permission failures still surface honestly.",
                    "mutates": True,
                },
                {
                    "step_id": "verify-activation",
                    "kind": "read",
                    "implemented": True,
                    "command": status_command,
                    "description": "Verify scheduler registration, supervisor liveness, heartbeat freshness, and proof gaps.",
                    "mutates": False,
                },
            ]
        return [
            {
                "step_id": "review-current-activation",
                "kind": "read",
                "implemented": True,
                "command": status_command,
                "description": "Review the activation report before unregistering startup ownership.",
                "mutates": False,
            },
            {
                "step_id": "unregister-bootstrap",
                "kind": "service-layer-mutation",
                "implemented": True,
                "command": f"chaseos runtime coordination-watch-bootstrap --runtime {runtime_id} --action unregister --json",
                "description": "Unregister the declared Task Scheduler startup entry through startup-surface-toggle.",
                "mutates": True,
            },
            {
                "step_id": "remove-bootstrap-artifacts",
                "kind": "manual-follow-up",
                "implemented": False,
                "command": f"chaseos runtime coordination-watch-bootstrap --runtime {runtime_id} --action remove --json",
                "description": "Optionally remove ChaseOS-owned bootstrap artifacts after unregistering host startup.",
                "mutates": True,
            },
            {
                "step_id": "verify-activation",
                "kind": "read",
                "implemented": True,
                "command": status_command,
                "description": "Confirm startup registration is absent while preserving the audit trail.",
                "mutates": False,
            },
        ]

    return [
        {
            "step_id": f"{surface_id}-{intent}-not-supported",
            "kind": "blocked",
            "implemented": False,
            "description": "This startup surface does not declare a supported service-layer plan.",
            "mutates": False,
        }
    ]


def _target_state_for_surface(surface: dict[str, Any], intent: str) -> str:
    if intent == "disable":
        return "off"
    source = str(surface.get("current_state_source") or "")
    if source == "coordination_watch_supervisor":
        return "running"
    if source == "coordination_watch_bootstrap":
        return "registered"
    if source == "host_startup_file":
        return "registered"
    return "configured"


def _future_gate_operation(surface: dict[str, Any], intent: str) -> str:
    surface_id = str(surface.get("surface_id") or "unknown")
    return f"lifecycle.startup_surface.{surface_id}.{intent}"


def _future_external_api(surface: dict[str, Any]) -> str:
    source = str(surface.get("current_state_source") or "")
    registration_kind = str(surface.get("startup_registration_kind") or "")
    if source == "host_startup_file" or registration_kind == "windows-startup-folder":
        return "host.startup_folder"
    if source == "coordination_watch_supervisor":
        return "host.process"
    if source == "coordination_watch_bootstrap":
        return "host.scheduler"
    return "none"


def _future_write_target_categories(surface: dict[str, Any]) -> list[str]:
    source = str(surface.get("current_state_source") or "")
    if source == "host_startup_file":
        return ["runtime_lifecycle_state", "host_startup_registration"]
    if source in {"coordination_watch_supervisor", "coordination_watch_bootstrap"}:
        return ["runtime_lifecycle_state"]
    return []


def _approval_evidence_required(surface: dict[str, Any], intent: str) -> list[str]:
    evidence = [
        "operator intent captured for the exact runtime, surface, and enable/disable action",
        "fresh startup-surfaces report snapshot",
        "toggle plan payload including current_state, target_state, and mutating steps",
        "declared affected paths or host registration target",
        "post-mutation verification command",
        "rollback plan for the inverse action",
        "Agent Activity audit target for the executor run",
    ]
    if str(surface.get("current_state_source") or "") == "coordination_watch_bootstrap":
        evidence.append("bootstrap activation-report evidence before and after scheduler mutation")
    if intent == "disable":
        evidence.append("operator acknowledgement that the selected autostart surface will be removed or stopped")
    return evidence


def _preflight_checks_for_surface(surface: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "check_id": "concrete-runtime",
            "description": "Runtime id is concrete; all/* targets are not allowed for mutations.",
            "required": True,
        },
        {
            "check_id": "declared-surface",
            "description": "Startup surface exists in the runtime lifecycle record and declares toggle support.",
            "required": True,
        },
        {
            "check_id": "fresh-read-model",
            "description": "Executor must read startup-surfaces immediately before mutation and reject stale UI state.",
            "required": True,
        },
        {
            "check_id": "operator-confirmation",
            "description": "Executor must require a current-session operator confirmation bound to the plan digest.",
            "required": True,
        },
        {
            "check_id": "no-secret-read",
            "description": "Executor must not read secrets, provider config values, tokens, or unrelated personal files.",
            "required": True,
        },
        {
            "check_id": "host-boundary",
            "description": f"Executor host boundary is {surface.get('startup_registration_kind') or surface.get('current_state_source') or 'declared-only'}.",
            "required": True,
        },
    ]


def _verification_commands(runtime_id: str, surface: dict[str, Any]) -> list[str]:
    commands = [f"chaseos runtime startup-surfaces --runtime {runtime_id} --json"]
    status_command = surface.get("status_command")
    if status_command:
        commands.append(str(status_command))
    if str(surface.get("current_state_source") or "") == "coordination_watch_bootstrap":
        commands.append(f"chaseos runtime coordination-watch-bootstrap --runtime {runtime_id} --action activation-report --json")
    return list(dict.fromkeys(commands))


def _rollback_plan(runtime_id: str, surface: dict[str, Any], intent: str) -> dict[str, Any]:
    source = str(surface.get("current_state_source") or "")
    inverse_intent = "disable" if intent == "enable" else "enable"
    steps: list[dict[str, Any]]

    if source == "host_startup_file":
        if intent == "enable":
            steps = [
                {
                    "step_id": "remove-created-startup-launcher",
                    "description": "Remove the Startup-folder launcher created by the failed enable operation.",
                    "command": None,
                }
            ]
        else:
            steps = [
                {
                    "step_id": "restore-declared-startup-launcher",
                    "description": "Restore the declared gateway startup launcher from lifecycle metadata or the executor backup.",
                    "command": None,
                }
            ]
    elif source == "coordination_watch_supervisor":
        action = "stop" if intent == "enable" else "start"
        steps = [
            {
                "step_id": f"{action}-supervisor-rollback",
                "description": f"Run the inverse supervisor action after a failed {intent}.",
                "command": f"chaseos runtime coordination-watch-supervisor --runtime {runtime_id} --action {action} --json",
            }
        ]
    elif source == "coordination_watch_bootstrap":
        action = "unregister" if intent == "enable" else "apply"
        steps = [
            {
                "step_id": f"{action}-bootstrap-rollback",
                "description": f"Run the inverse bootstrap action after a failed {intent}.",
                "command": f"chaseos runtime coordination-watch-bootstrap --runtime {runtime_id} --action {action} --json",
            },
            {
                "step_id": "verify-rollback",
                "description": "Re-run activation-report and startup-surfaces after rollback.",
                "command": f"chaseos runtime coordination-watch-bootstrap --runtime {runtime_id} --action activation-report --json",
            },
        ]
    else:
        steps = [
            {
                "step_id": "manual-rollback-required",
                "description": "No declared rollback command exists for this startup surface.",
                "command": None,
            }
        ]

    return {
        "inverse_intent": inverse_intent,
        "required": True,
        "steps": steps,
    }


def _plan_digest_payload(contract: dict[str, Any]) -> dict[str, Any]:
    source_plan = dict(contract.get("source_plan") or {})
    source_plan.pop("generated_at_utc", None)
    return {
        "runtime_id": contract.get("runtime_id"),
        "surface_id": contract.get("surface_id"),
        "intent": contract.get("intent"),
        "current_state": contract.get("current_state"),
        "target_state": contract.get("target_state"),
        "required_gate_operation": contract.get("required_gate_operation"),
        "required_write_target_categories": contract.get("required_write_target_categories"),
        "external_api": contract.get("external_api"),
        "execution_steps": contract.get("execution_steps"),
        "verification_commands": contract.get("verification_commands"),
        "rollback_plan": contract.get("rollback_plan"),
        "source_plan": source_plan,
    }


def _safe_approval_artifact_path(gate_approval_id: str) -> Path:
    normalized = (gate_approval_id or "").strip()
    if not normalized:
        raise ValueError("startup-surface executor preflight requires --gate-approval-id")
    if Path(normalized).name != normalized or normalized in {".", ".."}:
        raise ValueError("gate approval id must be a file-safe id, not a path")
    if not all(char.isalnum() or char in {"-", "_", "."} for char in normalized):
        raise ValueError("gate approval id may only contain letters, numbers, dash, underscore, and dot")
    return STARTUP_SURFACE_APPROVAL_DIR / f"{normalized}.json"


def _safe_create_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2) + "\n")


def _safe_approval_decision_path(gate_approval_id: str) -> Path:
    approval_path = _safe_approval_artifact_path(gate_approval_id)
    return approval_path.parent / "decisions" / approval_path.name


def _safe_approval_consumption_path(gate_approval_id: str) -> Path:
    approval_path = _safe_approval_artifact_path(gate_approval_id)
    return approval_path.parent / "consumptions" / approval_path.name


def _startup_surface_idempotency_marker_path(contract: dict[str, Any], plan_digest: str) -> Path:
    return STARTUP_SURFACE_MUTATION_DIR / (
        f"{contract.get('runtime_id')}-{contract.get('surface_id')}-{contract.get('intent')}-{plan_digest[:12]}.json"
    )


def _approval_request_id(contract: dict[str, Any], requested_by: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = _json_digest(
        {
            "runtime_id": contract.get("runtime_id"),
            "surface_id": contract.get("surface_id"),
            "intent": contract.get("intent"),
            "plan": _plan_digest_payload(contract),
            "requested_by": requested_by,
            "requested_at": stamp,
        }
    )[:12]
    return f"startup-appr-{contract.get('runtime_id')}-{contract.get('surface_id')}-{contract.get('intent')}-{stamp}-{digest}"


def _load_startup_surface_approval_decision(gate_approval_id: str) -> dict[str, Any]:
    path = _safe_approval_decision_path(gate_approval_id)
    if not path.exists():
        return {"present": False, "path": str(path), "payload": None, "error": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"present": True, "path": str(path), "payload": None, "error": f"approval-decision-invalid-json: {exc}"}
    if not isinstance(payload, dict):
        return {"present": True, "path": str(path), "payload": None, "error": "approval-decision-not-object"}
    return {"present": True, "path": str(path), "payload": payload, "error": None}


def _load_startup_surface_approval_consumption(gate_approval_id: str) -> dict[str, Any]:
    path = _safe_approval_consumption_path(gate_approval_id)
    if not path.exists():
        return {"present": False, "path": str(path), "payload": None, "error": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"present": True, "path": str(path), "payload": None, "error": f"approval-consumption-invalid-json: {exc}"}
    if not isinstance(payload, dict):
        return {"present": True, "path": str(path), "payload": None, "error": "approval-consumption-not-object"}
    return {"present": True, "path": str(path), "payload": payload, "error": None}


def _load_startup_surface_approval_artifact(gate_approval_id: str) -> dict[str, Any]:
    path = _safe_approval_artifact_path(gate_approval_id)
    if not path.exists():
        return {
            "present": False,
            "path": str(path),
            "payload": None,
            "error": "approval-artifact-missing",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "present": True,
            "path": str(path),
            "payload": None,
            "error": f"approval-artifact-invalid-json: {exc}",
        }
    if not isinstance(payload, dict):
        return {
            "present": True,
            "path": str(path),
            "payload": None,
            "error": "approval-artifact-not-object",
        }
    decision = _load_startup_surface_approval_decision(gate_approval_id)
    if decision.get("error"):
        return {
            "present": True,
            "path": str(path),
            "payload": payload,
            "error": decision.get("error"),
        }
    decision_payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else None
    if decision_payload:
        payload = dict(payload)
        payload["request_status"] = payload.get("approval_status") or payload.get("status") or "pending"
        payload["approval_status"] = decision_payload.get("decision")
        payload["decision"] = decision_payload.get("decision")
        payload["decision_artifact_path"] = decision.get("path")
        payload["decision_recorded_at_utc"] = decision_payload.get("decided_at_utc")
        payload["decided_by"] = decision_payload.get("decided_by")
        payload["decision_reason"] = decision_payload.get("reason")
    consumption = _load_startup_surface_approval_consumption(gate_approval_id)
    if consumption.get("error"):
        return {
            "present": True,
            "path": str(path),
            "payload": payload,
            "error": consumption.get("error"),
        }
    consumption_payload = consumption.get("payload") if isinstance(consumption.get("payload"), dict) else None
    if consumption_payload:
        payload = dict(payload)
        payload["approval_consumed"] = True
        payload["consumed"] = True
        payload["consumption_artifact_path"] = consumption.get("path")
        payload["consumed_at_utc"] = consumption_payload.get("consumed_at_utc")
    return {
        "present": True,
        "path": str(path),
        "payload": payload,
        "error": None,
    }


def _approval_status(payload: dict[str, Any]) -> str:
    return str(payload.get("approval_status") or payload.get("status") or payload.get("decision") or "").strip().lower()


def _check_item(check_id: str, passed: bool, detail: str, *, required: bool = True) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "passed": bool(passed),
        "required": required,
        "detail": detail,
    }


def build_startup_surface_approval_request(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    gate_approval_id: str | None = None,
    requested_by: str = "operator",
    write: bool = False,
) -> dict[str, Any]:
    """Build or persist a startup-surface approval request without host mutation."""
    contract = build_startup_surface_mutation_contract(runtime_id, surface_id, intent)
    requester = (requested_by or "operator").strip() or "operator"
    approval_id = (gate_approval_id or "").strip() or _approval_request_id(contract, requester)
    approval_path = _safe_approval_artifact_path(approval_id)
    plan_digest = _json_digest(_plan_digest_payload(contract))
    now = _utc_now_iso()
    payload = {
        "schema_version": 1,
        "artifact_type": "runtime_startup_surface_approval_request",
        "gate_approval_id": approval_id,
        "approval_status": "pending",
        "status": "pending_operator_decision",
        "requested_by": requester,
        "requested_at_utc": now,
        "runtime_id": contract.get("runtime_id"),
        "runtime_name": contract.get("runtime_name"),
        "surface_id": contract.get("surface_id"),
        "intent": contract.get("intent"),
        "current_state": contract.get("current_state"),
        "target_state": contract.get("target_state"),
        "required_gate_operation": contract.get("required_gate_operation"),
        "required_write_target_categories": contract.get("required_write_target_categories"),
        "external_api": contract.get("external_api"),
        "external_side_effect": contract.get("external_side_effect"),
        "plan_digest_sha256": plan_digest,
        "approval_evidence_required": contract.get("approval_evidence_required"),
        "verification_commands": contract.get("verification_commands"),
        "rollback_plan": contract.get("rollback_plan"),
        "approval_consumed": False,
        "consumed": False,
        "decision_required": True,
        "decision_artifact_path": str(_safe_approval_decision_path(approval_id)),
        "boundary": "Approval request only. This artifact does not enable execution, consume approval, write idempotency markers, or mutate host startup/scheduler/process state.",
    }
    result = {
        "action": "startup-surface-approval-request",
        "schema_version": 1,
        "generated_at_utc": now,
        "runtime_id": contract.get("runtime_id"),
        "runtime_name": contract.get("runtime_name"),
        "surface_id": contract.get("surface_id"),
        "intent": contract.get("intent"),
        "gate_approval_id": approval_id,
        "approval_artifact_path": str(approval_path),
        "decision_artifact_path": payload["decision_artifact_path"],
        "plan_digest_sha256": plan_digest,
        "write_enabled": bool(write),
        "written": False,
        "read_only": not bool(write),
        "mutation_enabled": False,
        "execution_enabled": False,
        "host_mutation_attempted": False,
        "approval_request": payload,
        "source_contract": contract,
        "boundary": "Writes only a repo-local approval request when --write-approval-request is supplied; no startup/scheduler/process mutation is reachable.",
    }
    if not write:
        return result
    if approval_path.exists():
        raise ValueError(f"approval request already exists: {approval_path}")
    approval_path.parent.mkdir(parents=True, exist_ok=True)
    approval_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    result["written"] = True
    result["read_only"] = False
    return result


def build_startup_surface_approval_decision(
    gate_approval_id: str,
    decision: str,
    *,
    decided_by: str = "operator",
    reason: str | None = None,
    write: bool = False,
) -> dict[str, Any]:
    """Build or persist an immutable decision artifact for a startup-surface approval request."""
    approval_path = _safe_approval_artifact_path(gate_approval_id)
    decision_path = _safe_approval_decision_path(gate_approval_id)
    normalized_decision = (decision or "").strip().lower()
    if normalized_decision not in VALID_APPROVAL_DECISIONS:
        raise ValueError("startup-surface approval decision requires --decision approved|denied")
    if not approval_path.exists():
        raise ValueError(f"approval request does not exist: {approval_path}")
    raw_request = json.loads(approval_path.read_text(encoding="utf-8"))
    if not isinstance(raw_request, dict):
        raise ValueError(f"approval request is not a JSON object: {approval_path}")
    actor = (decided_by or "operator").strip() or "operator"
    now = _utc_now_iso()
    payload = {
        "schema_version": 1,
        "artifact_type": "runtime_startup_surface_approval_decision",
        "gate_approval_id": gate_approval_id,
        "decision": normalized_decision,
        "approval_status": normalized_decision,
        "decided_by": actor,
        "decided_at_utc": now,
        "reason": reason,
        "runtime_id": raw_request.get("runtime_id"),
        "surface_id": raw_request.get("surface_id"),
        "intent": raw_request.get("intent"),
        "required_gate_operation": raw_request.get("required_gate_operation"),
        "plan_digest_sha256": raw_request.get("plan_digest_sha256"),
        "request_artifact_path": str(approval_path),
        "decision_artifact_path": str(decision_path),
        "approval_consumed": False,
        "boundary": "Decision artifact only. This does not consume approval, write idempotency markers, or mutate host startup/scheduler/process state.",
    }
    result = {
        "action": "startup-surface-approval-decision",
        "schema_version": 1,
        "generated_at_utc": now,
        "gate_approval_id": gate_approval_id,
        "decision": normalized_decision,
        "request_artifact_path": str(approval_path),
        "decision_artifact_path": str(decision_path),
        "runtime_id": raw_request.get("runtime_id"),
        "surface_id": raw_request.get("surface_id"),
        "intent": raw_request.get("intent"),
        "write_enabled": bool(write),
        "written": False,
        "read_only": not bool(write),
        "mutation_enabled": False,
        "execution_enabled": False,
        "host_mutation_attempted": False,
        "decision_record": payload,
        "boundary": "Writes only a repo-local immutable decision artifact when --write-approval-decision is supplied; no startup/scheduler/process mutation is reachable.",
    }
    if not write:
        return result
    if decision_path.exists():
        raise ValueError(f"approval decision already exists: {decision_path}")
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    result["written"] = True
    result["read_only"] = False
    return result


def build_startup_surface_mutation_contract(runtime_id: str, surface_id: str, intent: str) -> dict[str, Any]:
    """Build the future mutation approval/execution contract without executing it."""
    plan = build_startup_surface_toggle_plan(runtime_id, surface_id, intent)
    surface = dict(plan.get("surface") or {})
    normalized_runtime = str(plan.get("runtime_id") or "")
    normalized_intent = str(plan.get("intent") or "")
    gate_operation = _future_gate_operation(surface, normalized_intent)
    external_api = _future_external_api(surface)

    blocked_reasons = list(plan.get("blocked_reasons") or [])
    blocked_reasons.extend(
        [
            "approval-driven-host-mutation-executor-not-built",
        ]
    )

    return {
        "action": "startup-surface-mutation-contract",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "runtime_id": normalized_runtime,
        "runtime_name": plan.get("runtime_name"),
        "surface_id": plan.get("surface_id"),
        "intent": normalized_intent,
        "current_state": plan.get("current_state"),
        "target_state": plan.get("target_state"),
        "read_only": True,
        "mutation_enabled": False,
        "execution_enabled": False,
        "executes_mutation": False,
        "executor_implemented": True,
        "requires_operator_confirmation": True,
        "contract_status": "cli-confirm-executor-built-approval-flow-pending" if blocked_reasons else "ready",
        "blocked_reasons": blocked_reasons,
        "required_gate_operation": gate_operation,
        "gate_operation_allowlisted": True,
        "gate_policy_required": True,
        "required_write_target_categories": _future_write_target_categories(surface),
        "external_api": external_api,
        "external_api_allowlisted": external_api in {"host.process", "host.scheduler", "host.startup_folder"},
        "external_side_effect": True,
        "approval_evidence_required": _approval_evidence_required(surface, normalized_intent),
        "preflight_checks": _preflight_checks_for_surface(surface),
        "execution_steps": [
            {
                **step,
                "requires_operator_confirmation": bool(step.get("mutates")),
                "requires_gate_operation": gate_operation if step.get("mutates") else None,
            }
            for step in plan.get("steps", [])
        ],
        "verification_commands": _verification_commands(normalized_runtime, surface),
        "rollback_plan": _rollback_plan(normalized_runtime, surface, normalized_intent),
        "audit_records_required": [
            "runtime/lifecycle bootstrap or supervisor event record when the underlying command writes one",
            "07_LOGS/Agent-Activity/YYYY-MM-DD-runtime-startup-surface-mutation-<runtime>-<surface>.md",
            "operator-facing execution result with plan digest, approval id, before/after state, verification result, and rollback result when used",
        ],
        "source_plan": plan,
        "boundary": "Contract only. Use startup-surface-toggle with --confirm for the live CLI path. This command does not enable, disable, register, unregister, start, stop, remove, create, edit, or call host startup/scheduler/process mutation surfaces.",
    }


def build_startup_surface_approval_consumption(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    gate_approval_id: str,
    plan_digest: str,
    consumed_by: str = "operator",
    write: bool = False,
) -> dict[str, Any]:
    """Consume an approved startup-surface approval and write an exact-once marker without host mutation."""
    preflight = build_startup_surface_executor_preflight(
        runtime_id,
        surface_id,
        intent,
        gate_approval_id=gate_approval_id,
        plan_digest=plan_digest,
    )
    consumption_path = _safe_approval_consumption_path(gate_approval_id)
    marker_path = Path(str(preflight.get("idempotency_marker_path") or ""))
    current_digest = str(preflight.get("current_plan_digest_sha256") or "")
    actor = (consumed_by or "operator").strip() or "operator"
    now = _utc_now_iso()
    ready = bool((preflight.get("approval_consumption_preflight") or {}).get("ready"))

    consumption_payload = {
        "schema_version": 1,
        "artifact_type": "runtime_startup_surface_approval_consumption",
        "gate_approval_id": gate_approval_id,
        "runtime_id": preflight.get("runtime_id"),
        "surface_id": preflight.get("surface_id"),
        "intent": preflight.get("intent"),
        "target_state": preflight.get("target_state"),
        "plan_digest_sha256": current_digest,
        "required_gate_operation": preflight.get("required_gate_operation"),
        "consumed_by": actor,
        "consumed_at_utc": now,
        "idempotency_marker_path": str(marker_path),
        "host_mutation_attempted": False,
        "startup_surface_mutation_executed": False,
        "boundary": "Repo-local approval consumption record only. No Startup-folder, Task Scheduler, process, or host mutation is executed.",
    }
    marker_payload = dict(((preflight.get("idempotency_marker_contract") or {}).get("marker_payload_preview") or {}))
    marker_payload.update(
        {
            "written_at_utc": now,
            "written_by": actor,
            "approval_consumption_path": str(consumption_path),
            "host_mutation_attempted": False,
            "startup_surface_mutation_executed": False,
            "boundary": "Exact-once marker only. This marker records approval consumption readiness and does not prove host startup mutation occurred.",
        }
    )

    result = {
        "action": "startup-surface-approval-consumption",
        "schema_version": 1,
        "generated_at_utc": now,
        "runtime_id": preflight.get("runtime_id"),
        "runtime_name": preflight.get("runtime_name"),
        "surface_id": preflight.get("surface_id"),
        "intent": preflight.get("intent"),
        "gate_approval_id": gate_approval_id,
        "plan_digest_sha256": current_digest,
        "requested_plan_digest_sha256": plan_digest,
        "write_enabled": bool(write),
        "read_only": not bool(write),
        "ready": ready,
        "approval_would_be_consumed": ready,
        "idempotency_marker_would_be_written": ready,
        "approval_consumed": False,
        "idempotency_marker_written": False,
        "approval_consumption_path": str(consumption_path),
        "idempotency_marker_path": str(marker_path),
        "atomic_write_rule": "create-new-only",
        "overwrite_allowed": False,
        "executes_mutation": False,
        "startup_surface_mutation_executed": False,
        "host_mutation_attempted": False,
        "preflight": preflight,
        "approval_consumption_record": consumption_payload,
        "idempotency_marker_record": marker_payload,
        "blocked_reasons": [] if ready else list(preflight.get("blocked_reasons") or []),
        "boundary": "Consumes approval and writes an exact-once marker only when write=True; no host startup, scheduler, or process mutation is reachable.",
    }
    if not write:
        return result
    if consumption_path.exists():
        raise ValueError(f"approval already consumed: {consumption_path}")
    if marker_path.exists():
        raise ValueError(f"idempotency marker already exists: {marker_path}")
    if not ready:
        raise ValueError("startup surface approval consumption is not ready: " + ", ".join(result["blocked_reasons"]))
    _safe_create_json(marker_path, marker_payload)
    try:
        _safe_create_json(consumption_path, consumption_payload)
    except FileExistsError:
        raise ValueError(f"approval already consumed: {consumption_path}") from None
    result["read_only"] = False
    result["approval_consumed"] = True
    result["idempotency_marker_written"] = True
    return result


def _startup_surface_host_mutation_audit_record_preview(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    gate_approval_id: str,
    plan_digest: str,
    target_reached: bool | None = None,
) -> dict[str, Any]:
    filename_slug = f"hermes-optimus-runtime-startup-surface-mutation-{_safe_runtime_slug(runtime_id)}-{_safe_runtime_slug(surface_id)}"
    return {
        "required": True,
        "written": False,
        "filename_slug": filename_slug,
        "path_pattern": f"07_LOGS/Agent-Activity/YYYY-MM-DD-{filename_slug}.md",
        "required_graph_links": [
            "[[Hermes-Runtime-Profile]]",
            "[[HERMES]]",
            "[[Agent-Activity-Index]]",
        ],
        "required_fields": [
            "runtime_id",
            "surface_id",
            "intent",
            "gate_approval_id",
            "plan_digest_sha256",
            "before_state",
            "after_state",
            "target_reached",
            "verification_result",
            "rollback_result",
        ],
        "payload_preview": {
            "runtime": "hermes",
            "runtime_id": runtime_id,
            "surface_id": surface_id,
            "intent": intent,
            "gate_approval_id": gate_approval_id,
            "plan_digest_sha256": plan_digest,
            "target_reached": target_reached,
            "links": [
                "[[Hermes-Runtime-Profile]]",
                "[[HERMES]]",
                "[[Agent-Activity-Index]]",
            ],
        },
        "boundary": "Audit template only unless a separately reviewed Agent-Activity write path is enabled; no canonical promotion authority is granted.",
    }


def _verify_gateway_host_mutation(config: dict[str, Any], intent: str) -> dict[str, Any]:
    launcher_path = _resolve_path(config.get("launcher_path"))
    target_path = _resolve_path(config.get("target_path"))
    startup_contents = _gateway_startup_launcher_contents(config)
    target_contents = _gateway_target_launcher_contents(config)
    launcher_text = _read_backup_text(launcher_path) if launcher_path else None
    target_text = _read_backup_text(target_path) if target_path else None
    launcher_matches = (
        _normalize_cmd_text(launcher_text) == _normalize_cmd_text(startup_contents)
        if startup_contents is not None and launcher_text is not None
        else False
    )
    target_matches = (
        _normalize_cmd_text(target_text) == _normalize_cmd_text(target_contents)
        if target_contents is not None and target_text is not None
        else target_contents is None
    )
    target_reached = bool(launcher_path and launcher_path.exists() and launcher_matches and target_matches) if intent == "enable" else bool(launcher_path and not launcher_path.exists())
    return {
        "verification_enabled": True,
        "target_state": "registered" if intent == "enable" else "not_registered",
        "target_reached": target_reached,
        "launcher_path": str(launcher_path) if launcher_path else None,
        "target_path": str(target_path) if target_path else None,
        "launcher_present": bool(launcher_path and launcher_path.exists()),
        "target_present": bool(target_path and target_path.exists()),
        "launcher_matches_expected": launcher_matches,
        "target_matches_expected": target_matches,
    }


def execute_startup_surface_host_mutation(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    gate_approval_id: str,
    plan_digest: str,
    operator_confirmation: str,
    executor_enabled: bool = False,
    live_smoke_approved: bool = False,
    requested_by: str = "operator",
) -> dict[str, Any]:
    """Evaluate the gated Windows Startup-folder host adapter, fail-closed.

    This lower-phase executor is intentionally narrower than the older generic
    startup-surface toggle helper: it only considers host_startup_file /
    windows-startup-folder surfaces and reports readiness evidence. Even exact
    approval + current digest + confirmation + executor flags are not enough for
    live writes until final host-boundary, rollback, verification,
    approval-to-mutation envelope, and Agent-Activity audit-template policy gates
    are finalized. Task Scheduler, registry, service, canonical, provider, and
    Agent Bus mutations are not reachable here.
    """
    normalized_runtime = (runtime_id or "").strip().lower()
    normalized_surface = (surface_id or "").strip()
    normalized_intent = (intent or "").strip().lower()
    if normalized_intent not in VALID_TOGGLE_INTENTS:
        raise ValueError("startup host mutation executor requires --intent enable|disable")
    if not gate_approval_id or not plan_digest:
        raise ValueError("startup host mutation executor requires exact --gate-approval-id and --plan-digest")

    preflight = build_startup_surface_executor_preflight(
        normalized_runtime,
        normalized_surface,
        normalized_intent,
        gate_approval_id=gate_approval_id,
        plan_digest=plan_digest,
    )
    contract = preflight.get("source_contract") or build_startup_surface_mutation_contract(
        normalized_runtime,
        normalized_surface,
        normalized_intent,
    )
    surface = (contract.get("source_plan") or {}).get("surface") or {}
    config = _surface_config(normalized_runtime, normalized_surface)
    expected_confirmation_parts = [normalized_runtime, normalized_surface, normalized_intent, gate_approval_id, str(preflight.get("current_plan_digest_sha256") or "")]
    confirmation_text = (operator_confirmation or "").strip()
    confirmation_ok = all(part and part in confirmation_text for part in expected_confirmation_parts)
    adapter_supported = (
        str(surface.get("current_state_source") or config.get("current_state_source") or "") == "host_startup_file"
        and str(surface.get("startup_registration_kind") or config.get("startup_registration_kind") or "") == "windows-startup-folder"
        and str(contract.get("external_api") or "") == "host.startup_folder"
    )
    audit_record = _startup_surface_host_mutation_audit_record_preview(
        normalized_runtime,
        normalized_surface,
        normalized_intent,
        gate_approval_id=gate_approval_id,
        plan_digest=str(preflight.get("current_plan_digest_sha256") or plan_digest),
    )
    host_boundary_policy = build_startup_surface_host_boundary_policy_report(
        normalized_runtime,
        normalized_surface,
        normalized_intent,
        gate_approval_id=gate_approval_id,
        plan_digest=plan_digest,
    )
    audit_template = build_startup_surface_host_mutation_audit_template_report(
        normalized_runtime,
        normalized_surface,
        normalized_intent,
        gate_approval_id=gate_approval_id,
        plan_digest=plan_digest,
    )
    host_policy_confirmation = host_boundary_policy.get("operator_confirmation_policy") or {}
    host_policy_rollback = host_boundary_policy.get("rollback_policy") or {}
    host_policy_verification = host_boundary_policy.get("verification_evidence") or {}
    readiness_prerequisites = ((host_boundary_policy.get("transaction_order") or {}).get("source_readiness") or {}).get("prerequisites") or {}
    production_envelope = readiness_prerequisites.get("production_approval_to_mutation_envelope") or {}
    final_policy_gates = {
        "host_boundary_policy_finalized": host_boundary_policy.get("policy_status") in {"finalized", "ready", "approved"},
        "operator_confirmation_policy_finalized": bool(host_policy_confirmation.get("finalized")),
        "rollback_policy_finalized": bool(host_policy_rollback.get("finalized")),
        "post_mutation_verification_policy_finalized": bool(host_policy_verification.get("finalized")),
        "production_approval_to_mutation_envelope_enabled": bool(production_envelope.get("ready")),
        "agent_activity_audit_template_finalized": (
            audit_template.get("audit_template_status") in {"finalized", "ready", "approved"}
            and bool((audit_template.get("agent_activity_template") or {}).get("finalized"))
        ),
    }
    final_policy_gates["all_finalized"] = all(final_policy_gates.values())
    final_policy_blockers = {
        "host_boundary_policy_finalized": "wsl-windows-host-boundary-policy-not-finalized",
        "operator_confirmation_policy_finalized": "operator-confirmation-policy-not-finalized",
        "rollback_policy_finalized": "rollback-recovery-policy-not-finalized",
        "post_mutation_verification_policy_finalized": "post-mutation-verification-policy-not-finalized",
        "production_approval_to_mutation_envelope_enabled": "production-approval-to-mutation-envelope-not-enabled",
        "agent_activity_audit_template_finalized": "agent-activity-audit-template-not-finalized",
    }

    blocked_reasons: list[str] = []
    if not executor_enabled:
        blocked_reasons.append("host-executor-disabled-by-default")
    if not live_smoke_approved:
        blocked_reasons.append("separate-live-smoke-approval-required")
    if not bool((preflight.get("approval_consumption_preflight") or {}).get("ready")):
        blocked_reasons.extend(list(preflight.get("blocked_reasons") or []))
    if not confirmation_ok:
        blocked_reasons.append("operator-confirmation-missing-runtime-surface-intent-approval-id-or-plan-digest")
    if not adapter_supported:
        blocked_reasons.append("unsupported-host-mutation-adapter")
    for gate, reason in final_policy_blockers.items():
        if not final_policy_gates.get(gate):
            blocked_reasons.append(reason)

    final_execution_allowed = bool(
        executor_enabled
        and live_smoke_approved
        and confirmation_ok
        and adapter_supported
        and final_policy_gates["all_finalized"]
        and not blocked_reasons
    )

    base = {
        "action": "startup-surface-host-mutation-executor",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "runtime_id": normalized_runtime,
        "runtime_name": contract.get("runtime_name"),
        "surface_id": normalized_surface,
        "intent": normalized_intent,
        "gate_approval_id": gate_approval_id,
        "plan_digest_sha256": preflight.get("current_plan_digest_sha256"),
        "requested_plan_digest_sha256": plan_digest,
        "selected_adapter": "windows-startup-folder" if adapter_supported else None,
        "executor_enabled": bool(executor_enabled),
        "execution_enabled": final_execution_allowed,
        "startup_folder_mutation_enabled": bool(final_execution_allowed),
        "task_scheduler_mutation_enabled": False,
        "task_scheduler_mutation_attempted": False,
        "approval_consumed": False,
        "idempotency_marker_written": False,
        "host_mutation_attempted": False,
        "startup_surface_mutation_executed": False,
        "preflight": preflight,
        "host_boundary_policy": host_boundary_policy,
        "agent_activity_audit_template": audit_template,
        "final_policy_gates": final_policy_gates,
        "agent_activity_audit": audit_record,
        "verification": {"verification_enabled": False, "target_reached": False},
        "rollback": {
            "available": adapter_supported,
            "automatic_rollback_attempted": False,
            "operator_review_required_on_failure": True,
            "rollback_plan": contract.get("rollback_plan"),
        },
        "blocked_reasons": list(dict.fromkeys(blocked_reasons)),
        "boundary": "Lower-phase gated Windows Startup-folder adapter only. No Task Scheduler, registry, service, provider, Agent Bus, Gate, or canonical mutation authority is expanded.",
    }
    if blocked_reasons:
        return base

    consumption = build_startup_surface_approval_consumption(
        normalized_runtime,
        normalized_surface,
        normalized_intent,
        gate_approval_id=gate_approval_id,
        plan_digest=plan_digest,
        consumed_by=requested_by,
        write=True,
    )
    before = build_runtime_startup_surfaces_report(normalized_runtime)
    before_surface = _find_surface(before, normalized_surface) or {}
    execution = _execute_gateway_toggle(config, normalized_intent, dry_run=False)
    verification = _verify_gateway_host_mutation(config, normalized_intent)
    after = build_runtime_startup_surfaces_report(normalized_runtime)
    after_surface = _find_surface(after, normalized_surface) or {}
    target_reached = bool(verification.get("target_reached"))
    audit_record = _startup_surface_host_mutation_audit_record_preview(
        normalized_runtime,
        normalized_surface,
        normalized_intent,
        gate_approval_id=gate_approval_id,
        plan_digest=str(preflight.get("current_plan_digest_sha256") or plan_digest),
        target_reached=target_reached,
    )
    event_path = _append_startup_surface_event(
        {
            "event_type": "startup_surface_host_mutation_executor",
            "generated_at_utc": _utc_now_iso(),
            "runtime_id": normalized_runtime,
            "surface_id": normalized_surface,
            "intent": normalized_intent,
            "gate_approval_id": gate_approval_id,
            "plan_digest_sha256": preflight.get("current_plan_digest_sha256"),
            "target_reached": target_reached,
            "host_mutation_attempted": True,
            "startup_surface_mutation_executed": True,
        }
    )
    base.update(
        {
            "execution_enabled": True,
            "approval_consumed": bool(consumption.get("approval_consumed")),
            "idempotency_marker_written": bool(consumption.get("idempotency_marker_written")),
            "host_mutation_attempted": True,
            "startup_surface_mutation_executed": True,
            "target_reached": target_reached,
            "before_state": before_surface.get("state"),
            "after_state": after_surface.get("state"),
            "execution": execution,
            "verification": verification,
            "approval_consumption": consumption,
            "agent_activity_audit": audit_record,
            "event_log_path": str(event_path),
            "blocked_reasons": [] if target_reached else ["post-mutation-verification-failed"],
        }
    )
    return base


def build_startup_surface_executor_preflight(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    gate_approval_id: str,
    plan_digest: str,
) -> dict[str, Any]:
    """Validate future executor readiness without consuming approval or mutating host state."""
    contract = build_startup_surface_mutation_contract(runtime_id, surface_id, intent)
    current_digest = _json_digest(_plan_digest_payload(contract))
    expected_digest = (plan_digest or "").strip().lower()
    approval = _load_startup_surface_approval_artifact(gate_approval_id)
    consumption = _load_startup_surface_approval_consumption(gate_approval_id)
    payload = approval.get("payload") if isinstance(approval.get("payload"), dict) else {}
    status = _approval_status(payload)
    expected_gate_operation = str(contract.get("required_gate_operation") or "")
    idempotency_marker_path = _startup_surface_idempotency_marker_path(contract, current_digest)
    approval_consumed = _coerce_bool(payload.get("approval_consumed") or payload.get("consumed"), default=False) or bool(consumption.get("present"))

    approval_checks = [
        _check_item("approval-artifact-present", bool(approval.get("present")), str(approval.get("path"))),
        _check_item("approval-artifact-json-valid", not approval.get("error"), str(approval.get("error") or "valid")),
        _check_item("approval-status-approved", status in {"approved", "granted"}, f"status={status or 'missing'}"),
        _check_item("approval-not-consumed", not approval_consumed, f"consumed={approval_consumed}"),
        _check_item("approval-runtime-match", payload.get("runtime_id") == contract.get("runtime_id"), f"approval runtime={payload.get('runtime_id')!r}"),
        _check_item("approval-surface-match", payload.get("surface_id") == contract.get("surface_id"), f"approval surface={payload.get('surface_id')!r}"),
        _check_item("approval-intent-match", payload.get("intent") == contract.get("intent"), f"approval intent={payload.get('intent')!r}"),
        _check_item(
            "approval-gate-operation-match",
            payload.get("required_gate_operation") == expected_gate_operation,
            f"approval gate={payload.get('required_gate_operation')!r}",
        ),
        _check_item(
            "approval-plan-digest-match",
            str(payload.get("plan_digest_sha256") or "").lower() == current_digest,
            f"approval digest={payload.get('plan_digest_sha256')!r}",
        ),
    ]
    request_checks = [
        _check_item("requested-plan-digest-present", bool(expected_digest), "plan digest was supplied" if expected_digest else "missing --plan-digest"),
        _check_item("requested-plan-digest-current", expected_digest == current_digest, f"current digest={current_digest}"),
        _check_item("fresh-state-snapshot-built", True, f"current_state={contract.get('current_state')!r}"),
        _check_item("idempotency-marker-absent", not idempotency_marker_path.exists(), str(idempotency_marker_path)),
        _check_item("executor-implemented", True, "startup-surface CLI mutation executor and approval-artifact consumption marker writer exist"),
        _check_item("execution-enabled", False, "execution remains disabled in this preflight"),
    ]
    checks = approval_checks + request_checks
    failed_required = [check["check_id"] for check in checks if check.get("required") and not check.get("passed")]
    readiness_blockers = [check_id for check_id in failed_required if check_id != "execution-enabled"]
    marker_present = idempotency_marker_path.exists()
    approval_consumption_ready = not readiness_blockers
    marker_payload_preview = {
        "schema_version": 1,
        "marker_kind": "startup-surface-mutation-idempotency",
        "runtime_id": contract.get("runtime_id"),
        "surface_id": contract.get("surface_id"),
        "intent": contract.get("intent"),
        "target_state": contract.get("target_state"),
        "gate_approval_id": gate_approval_id,
        "plan_digest_sha256": current_digest,
        "required_gate_operation": expected_gate_operation,
        "marker_path": str(idempotency_marker_path),
        "atomic_write_rule": "create-new-only",
    }

    blocked_reasons = list(dict.fromkeys(list(contract.get("blocked_reasons") or []) + failed_required + ["startup-surface-approval-preflight-only"]))
    return {
        "action": "startup-surface-executor-preflight",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "runtime_id": contract.get("runtime_id"),
        "runtime_name": contract.get("runtime_name"),
        "surface_id": contract.get("surface_id"),
        "intent": contract.get("intent"),
        "current_state": contract.get("current_state"),
        "target_state": contract.get("target_state"),
        "read_only": True,
        "mutation_enabled": False,
        "execution_enabled": False,
        "executes_mutation": False,
        "executor_implemented": True,
        "executor_invocation_allowed": False,
        "approval_consumption_preflight": {
            "ready": approval_consumption_ready,
            "would_consume_approval": approval_consumption_ready,
            "consumption_enabled": False,
            "consumption_built": True,
            "consumption_attempted": False,
            "approval_consumed": False,
            "precondition_blockers": readiness_blockers,
            "approval_artifact_path": str(approval.get("path") or ""),
            "approval_status": status or None,
            "gate_approval_id": gate_approval_id,
            "boundary": "No mutation. This preflight validates whether exact-once approval consumption would be allowed; it does not consume the approval artifact.",
        },
        "idempotency_marker_contract": {
            "ready": approval_consumption_ready,
            "would_write_marker": approval_consumption_ready,
            "write_enabled": False,
            "write_built": True,
            "write_attempted": False,
            "marker_written": False,
            "marker_present": marker_present,
            "marker_path": str(idempotency_marker_path),
            "atomic_write_rule": "create-new-only",
            "overwrite_allowed": False,
            "duplicate_replay_blocked": marker_present,
            "marker_payload_preview": marker_payload_preview,
            "boundary": "No marker write. Future execution must create this marker atomically and refuse duplicates/replays if it already exists.",
        },
        "execution_gate": {
            "would_execute_mutation": False,
            "execution_enabled": False,
            "all_preconditions_met_except_execution_enabled": approval_consumption_ready,
            "blocked_until_consumption_and_marker_writer": True,
            "boundary": "No Startup-folder, Task Scheduler, process, or host mutation is reachable from this preflight.",
        },
        "approval_consumed": False,
        "idempotency_marker_written": False,
        "host_mutation_attempted": False,
        "preflight_status": "blocked",
        "blocked_reasons": blocked_reasons,
        "gate_approval_id": gate_approval_id,
        "approval_artifact": {
            "present": approval.get("present"),
            "path": approval.get("path"),
            "error": approval.get("error"),
            "status": status or None,
        },
        "approval_consumption_artifact": {
            "present": consumption.get("present"),
            "path": consumption.get("path"),
            "error": consumption.get("error"),
        },
        "required_gate_operation": expected_gate_operation,
        "gate_operation_allowlisted": bool(contract.get("gate_operation_allowlisted")),
        "external_api": contract.get("external_api"),
        "external_api_allowlisted": contract.get("external_api_allowlisted"),
        "external_side_effect": True,
        "current_plan_digest_sha256": current_digest,
        "requested_plan_digest_sha256": expected_digest or None,
        "idempotency_marker_path": str(idempotency_marker_path),
        "idempotency_marker_present": idempotency_marker_path.exists(),
        "checks": checks,
        "verification_commands": contract.get("verification_commands"),
        "rollback_plan": contract.get("rollback_plan"),
        "source_contract": contract,
        "boundary": "Preflight only. This command does not consume approval, write idempotency markers, enable execution, or call Startup-folder, scheduler, process, start, stop, register, unregister, create, edit, or remove operations.",
    }


def build_startup_surface_transaction_order_report(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    gate_approval_id: str,
    plan_digest: str,
) -> dict[str, Any]:
    """Model the future approval-to-host-mutation transaction order without executing it."""
    preflight = build_startup_surface_executor_preflight(
        runtime_id,
        surface_id,
        intent,
        gate_approval_id=gate_approval_id,
        plan_digest=plan_digest,
    )
    consumption = preflight.get("approval_consumption_preflight") or {}
    marker = preflight.get("idempotency_marker_contract") or {}
    execution_gate = preflight.get("execution_gate") or {}
    approval_ready = bool(consumption.get("ready"))
    marker_absent = not bool(marker.get("marker_present"))
    ready_for_future_executor = approval_ready and marker_absent
    blocked_reasons = list(preflight.get("blocked_reasons") or [])
    transaction_status = "ready_for_future_executor_blocked_now" if ready_for_future_executor else "blocked"

    transaction_order = [
        {
            "order": 1,
            "step_id": "validate-approval",
            "description": "Validate approval artifact, decision, runtime/surface/intent, gate operation, and plan digest.",
            "enabled_now": True,
            "implemented_now": True,
            "would_mutate_host_when_enabled": False,
            "status": "ready" if approval_ready else "blocked",
            "source_checks": [
                "approval-artifact-present",
                "approval-status-approved",
                "approval-not-consumed",
                "approval-runtime-match",
                "approval-surface-match",
                "approval-intent-match",
                "approval-gate-operation-match",
                "approval-plan-digest-match",
                "requested-plan-digest-current",
            ],
        },
        {
            "order": 2,
            "step_id": "validate-idempotency-marker-absence",
            "description": "Refuse duplicate/replay if the exact-once marker for this plan digest already exists.",
            "enabled_now": True,
            "implemented_now": True,
            "would_mutate_host_when_enabled": False,
            "status": "ready" if marker_absent else "blocked",
            "marker_path": marker.get("marker_path"),
            "atomic_write_rule": marker.get("atomic_write_rule"),
        },
        {
            "order": 3,
            "step_id": "execute-host-startup-mutation",
            "description": "Future executor performs the host Startup-folder, Task Scheduler, or process mutation only after approval and replay checks pass.",
            "enabled_now": False,
            "implemented_now": False,
            "would_mutate_host_when_enabled": True,
            "status": "blocked_not_enabled",
            "required_gate_operation": preflight.get("required_gate_operation"),
            "external_api": preflight.get("external_api"),
        },
        {
            "order": 4,
            "step_id": "verify-target-state",
            "description": "Verify the requested target state after the future host mutation before declaring success.",
            "enabled_now": False,
            "implemented_now": True,
            "would_mutate_host_when_enabled": False,
            "status": "blocked_until_host_mutation_enabled",
            "verification_commands": preflight.get("verification_commands") or [],
        },
        {
            "order": 5,
            "step_id": "write-or-retain-idempotency-marker",
            "description": "Write the exact-once marker only after verified success; retain/annotate failure state according to policy if mutation result is ambiguous.",
            "enabled_now": False,
            "implemented_now": True,
            "would_mutate_host_when_enabled": False,
            "status": "blocked_until_target_verified",
            "marker_path": marker.get("marker_path"),
            "atomic_write_rule": marker.get("atomic_write_rule"),
            "overwrite_allowed": marker.get("overwrite_allowed"),
        },
        {
            "order": 6,
            "step_id": "emit-agent-activity-audit",
            "description": "Emit an Agent-Activity audit record containing approval id, plan digest, before/after state, verification result, and failure/rollback posture.",
            "enabled_now": False,
            "implemented_now": False,
            "would_mutate_host_when_enabled": False,
            "status": "blocked_until_execution_result_exists",
            "audit_target": "07_LOGS/Agent-Activity/YYYY-MM-DD-runtime-startup-surface-mutation-<runtime>-<surface>.md",
        },
    ]

    return {
        "action": "startup-surface-transaction-order-report",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "runtime_id": preflight.get("runtime_id"),
        "runtime_name": preflight.get("runtime_name"),
        "surface_id": preflight.get("surface_id"),
        "intent": preflight.get("intent"),
        "gate_approval_id": gate_approval_id,
        "plan_digest_sha256": preflight.get("current_plan_digest_sha256"),
        "requested_plan_digest_sha256": preflight.get("requested_plan_digest_sha256"),
        "transaction_status": transaction_status,
        "read_only": True,
        "mutation_enabled": False,
        "execution_enabled": False,
        "executes_mutation": False,
        "executor_invocation_allowed": False,
        "approval_consumed": False,
        "idempotency_marker_written": False,
        "host_mutation_attempted": False,
        "startup_surface_mutation_executed": False,
        "duplicate_replay_blocked": bool(marker.get("duplicate_replay_blocked")),
        "approval_ready": approval_ready,
        "idempotency_marker_absent": marker_absent,
        "blocked_reasons": blocked_reasons,
        "transaction_order": transaction_order,
        "verification_gate": {
            "would_verify_after_mutation": True,
            "verification_enabled_now": False,
            "target_state": preflight.get("target_state"),
            "verification_commands": preflight.get("verification_commands") or [],
        },
        "failure_policy": {
            "host_mutation_failure": {
                "write_success_marker": False,
                "consume_approval": False,
                "emit_failure_audit": True,
                "operator_review_required": True,
            },
            "verification_failure": {
                "write_success_marker": False,
                "attempt_rollback_without_operator_confirmation": False,
                "rollback_plan": preflight.get("rollback_plan"),
                "operator_review_required": True,
            },
            "marker_write_failure_after_verified_mutation": {
                "do_not_retry_host_mutation_automatically": True,
                "emit_ambiguous_state_audit": True,
                "operator_review_required": True,
            },
        },
        "source_preflight": preflight,
        "boundary": "Transaction-order report only. No approval consumption, idempotency marker write, Startup-folder change, Task Scheduler mutation, process mutation, or host startup mutation is performed.",
    }


def build_startup_surface_executor_readiness_report(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    gate_approval_id: str | None = None,
    plan_digest: str | None = None,
) -> dict[str, Any]:
    """Report whether the future host-mutation executor is eligible to be enabled.

    This is a fail-closed/read-only packet. It intentionally does not consume
    approvals, write idempotency markers, or call host startup mutation surfaces.
    """
    contract = build_startup_surface_mutation_contract(runtime_id, surface_id, intent)
    surface = contract.get("source_plan", {}).get("surface") or {}
    registration_kind = str(surface.get("startup_registration_kind") or surface.get("registration_kind") or "unknown")
    external_api = str(contract.get("external_api") or "unknown")
    is_wsl = bool(os.environ.get("WSL_DISTRO_NAME") or "microsoft" in os.uname().release.lower()) if hasattr(os, "uname") else False
    has_transaction_material = bool((gate_approval_id or "").strip() and (plan_digest or "").strip())
    transaction_report: dict[str, Any] | None = None
    transaction_ready = False
    transaction_blockers: list[str] = []
    if has_transaction_material:
        try:
            transaction_report = build_startup_surface_transaction_order_report(
                runtime_id,
                surface_id,
                intent,
                gate_approval_id=str(gate_approval_id or ""),
                plan_digest=str(plan_digest or ""),
            )
            transaction_ready = transaction_report.get("transaction_status") == "ready_for_future_executor_blocked_now"
            transaction_blockers = list(transaction_report.get("blocked_reasons") or [])
        except ValueError as exc:
            transaction_blockers = [str(exc)]
    else:
        transaction_blockers = ["transaction-order-material-missing"]

    prerequisites = {
        "transaction_order": {
            "ready": transaction_ready,
            "required": True,
            "source": "runtime startup-surface-transaction-order",
            "blocked_reasons": [] if transaction_ready else transaction_blockers,
        },
        "approval_gate": {
            "ready": bool(transaction_report and transaction_report.get("approval_ready")),
            "required": True,
            "gate_approval_id": gate_approval_id,
        },
        "exact_once_idempotency": {
            "ready": bool(transaction_report and transaction_report.get("idempotency_marker_absent")),
            "required": True,
            "marker_path": ((transaction_report or {}).get("source_preflight") or {}).get("idempotency_marker_path"),
            "atomic_write_rule": "create-new-only",
        },
        "host_mutation_backend_enabled": {
            "ready": False,
            "required": True,
            "startup_folder_mutation_enabled": False,
            "task_scheduler_mutation_enabled": False,
            "reason": "host mutation backend is intentionally disabled",
        },
        "operator_confirmation_policy": {
            "ready": False,
            "required": True,
            "requires_explicit_operator_confirmation": True,
            "reason": "final confirmation policy is not finalized",
        },
        "rollback_recovery_policy": {
            "ready": False,
            "required": True,
            "rollback_plan_present": bool(contract.get("rollback_plan")),
            "reason": "rollback/recovery policy is not finalized for live host mutation",
        },
        "post_mutation_verification_policy": {
            "ready": False,
            "required": True,
            "verification_commands_present": bool(contract.get("verification_commands")),
            "reason": "post-mutation verification policy is not finalized for live host mutation",
        },
        "host_boundary_policy": {
            "ready": False,
            "required": True,
            "running_under_wsl": is_wsl,
            "external_api": external_api,
            "registration_kind": registration_kind,
            "reason": "WSL/Windows host-boundary mutation policy is not finalized",
        },
        "production_approval_to_mutation_envelope": {
            "ready": False,
            "required": True,
            "reason": "no production approval-to-mutation envelope is enabled",
        },
        "agent_activity_audit_template": {
            "ready": False,
            "required": True,
            "reason": "final host-mutation Agent-Activity audit template is not finalized",
        },
    }
    blocker_map = {
        "transaction_order": "transaction-order-not-ready",
        "approval_gate": "approval-gate-not-ready",
        "exact_once_idempotency": "exact-once-idempotency-not-ready",
        "host_mutation_backend_enabled": "host-mutation-backend-not-enabled",
        "operator_confirmation_policy": "operator-confirmation-policy-not-finalized",
        "rollback_recovery_policy": "rollback-recovery-policy-not-finalized",
        "post_mutation_verification_policy": "post-mutation-verification-policy-not-finalized",
        "host_boundary_policy": "wsl-windows-host-boundary-policy-not-finalized",
        "production_approval_to_mutation_envelope": "production-approval-to-mutation-envelope-not-enabled",
        "agent_activity_audit_template": "agent-activity-audit-template-not-finalized",
    }
    blocked_reasons = [reason for key, reason in blocker_map.items() if not prerequisites[key].get("ready")]
    if not transaction_ready:
        for reason in transaction_blockers:
            if reason and reason not in blocked_reasons:
                blocked_reasons.append(reason)

    return {
        "action": "startup-surface-executor-readiness",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "runtime_id": contract.get("runtime_id"),
        "runtime_name": contract.get("runtime_name"),
        "surface_id": contract.get("surface_id"),
        "intent": contract.get("intent"),
        "gate_approval_id": gate_approval_id,
        "plan_digest_sha256": transaction_report.get("plan_digest_sha256") if transaction_report else contract.get("plan_digest_sha256"),
        "read_only": True,
        "executor_enabled_now": False,
        "eligible_for_future_enablement": False,
        "mutation_enabled": False,
        "execution_enabled": False,
        "executes_mutation": False,
        "startup_folder_mutation_enabled": False,
        "task_scheduler_mutation_enabled": False,
        "host_mutation_attempted": False,
        "startup_surface_mutation_executed": False,
        "approval_consumed": False,
        "idempotency_marker_written": False,
        "readiness_status": "blocked",
        "blocked_reasons": blocked_reasons,
        "prerequisites": prerequisites,
        "transaction_order": {
            "ready": transaction_ready,
            "provided": has_transaction_material,
            "status": transaction_report.get("transaction_status") if transaction_report else "missing",
            "report": transaction_report,
        },
        "platform_support": {
            "running_under_wsl": is_wsl,
            "external_api": external_api,
            "registration_kind": registration_kind,
            "startup_folder_supported_by_contract": external_api == "host.startup_folder",
            "task_scheduler_supported_by_contract": external_api == "host.scheduler",
            "host_boundary_policy_finalized": False,
        },
        "enablement_decision": {
            "may_enable_executor_now": False,
            "requires_operator_confirmation": True,
            "requires_gate_approval": True,
            "requires_exact_once_marker": True,
            "requires_verified_transaction_order": True,
            "requires_finalized_failure_policy": True,
            "decision": "deny",
        },
        "source_contract": contract,
        "boundary": "Executor readiness packet only. No approval consumption, marker write, Startup-folder mutation, Task Scheduler mutation, process mutation, or host startup mutation is performed.",
    }


def build_startup_surface_host_boundary_policy_report(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    gate_approval_id: str | None = None,
    plan_digest: str | None = None,
) -> dict[str, Any]:
    """Model WSL/Windows host-boundary policy required before executor enablement.

    This packet is read-only and fail-closed. It defines the policy gates for a
    future host mutation executor without consuming approvals, writing markers,
    or touching Windows Startup-folder / Task Scheduler state.
    """
    contract = build_startup_surface_mutation_contract(runtime_id, surface_id, intent)
    normalized_runtime = str(contract.get("runtime_id") or runtime_id)
    normalized_surface = str(contract.get("surface_id") or surface_id)
    normalized_intent = str(contract.get("intent") or intent)
    config = _surface_config(normalized_runtime, normalized_surface)
    external_api = str(contract.get("external_api") or "unknown")
    registration_kind = str(config.get("startup_registration_kind") or "unknown")
    source = str(config.get("current_state_source") or "unknown")
    is_wsl = bool(os.environ.get("WSL_DISTRO_NAME") or "microsoft" in os.uname().release.lower()) if hasattr(os, "uname") else False
    has_transaction_material = bool((gate_approval_id or "").strip() and (plan_digest or "").strip())

    readiness = build_startup_surface_executor_readiness_report(
        normalized_runtime,
        normalized_surface,
        normalized_intent,
        gate_approval_id=gate_approval_id,
        plan_digest=plan_digest,
    )
    transaction = readiness.get("transaction_order") or {}
    transaction_blockers = list((transaction.get("report") or {}).get("blocked_reasons") or [])
    if not has_transaction_material:
        transaction_blockers.append("transaction-order-material-missing")

    path_keys = ["launcher_path", "target_path", "diagnostic_log_path"]
    declared_paths: dict[str, dict[str, Any]] = {}
    translated_count = 0
    for key in path_keys:
        raw = config.get(key)
        if not raw:
            continue
        translated = _windows_drive_path_to_wsl(str(raw))
        resolved = _resolve_path(raw)
        if translated is not None:
            translated_count += 1
        declared_paths[key] = {
            "declared": str(raw),
            "resolved_for_current_host": str(resolved) if resolved else None,
            "windows_drive_path": _windows_drive_path_to_wsl(str(raw)) is not None,
            "translated_for_wsl": translated is not None,
        }

    selected_api_allowed = external_api in {"host.startup_folder", "host.scheduler", "process.local"}
    blocked_reasons = list(dict.fromkeys([
        *transaction_blockers,
        "wsl-windows-host-boundary-policy-not-approved",
        "operator-confirmation-wording-not-approved",
        "rollback-policy-not-approved",
        "post-mutation-verification-evidence-not-approved",
        "host-executor-still-disabled",
    ]))

    return {
        "action": "startup-surface-host-boundary-policy",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "runtime_id": normalized_runtime,
        "runtime_name": contract.get("runtime_name"),
        "surface_id": normalized_surface,
        "intent": normalized_intent,
        "gate_approval_id": gate_approval_id,
        "plan_digest_sha256": readiness.get("plan_digest_sha256") or contract.get("plan_digest_sha256"),
        "policy_status": "blocked",
        "read_only": True,
        "mutation_enabled": False,
        "execution_enabled": False,
        "executor_enabled_now": False,
        "eligible_for_future_enablement": False,
        "executes_mutation": False,
        "host_mutation_attempted": False,
        "startup_surface_mutation_executed": False,
        "approval_consumed": False,
        "idempotency_marker_written": False,
        "blocked_reasons": blocked_reasons,
        "allowed_host_apis": {
            "selected_api": external_api,
            "api_allowlisted_by_contract": selected_api_allowed,
            "startup_folder_allowed": external_api == "host.startup_folder",
            "task_scheduler_allowed": external_api == "host.scheduler",
            "process_local_allowed": external_api == "process.local",
            "forbidden_apis": ["browser.ambient", "shell.unbounded", "credential.raw", "canonical.vault.write"],
        },
        "wsl_windows_boundary": {
            "running_under_wsl": is_wsl,
            "source": source,
            "registration_kind": registration_kind,
            "host_executor_must_run_on_windows_side": external_api in {"host.startup_folder", "host.scheduler"},
            "wsl_may_prepare_read_only_plan": True,
            "wsl_may_directly_mutate_windows_startup": False,
            "windows_target_paths_translated_for_wsl": translated_count > 0,
            "declared_paths": declared_paths,
            "required_host_identity": "Windows host owned by the operator; no remote host or ambient shell expansion",
        },
        "operator_confirmation_policy": {
            "finalized": False,
            "required_confirmation_phrase": (
                f"I understand this may change Windows startup behavior for {normalized_runtime}/{normalized_surface} "
                f"and I approve {normalized_intent}."
            ),
            "must_name_runtime_surface_intent": True,
            "must_include_gate_approval_id": True,
            "must_include_plan_digest": True,
            "discord_or_chat_intent_alone_sufficient": False,
        },
        "rollback_policy": {
            "finalized": False,
            "rollback_plan_present": bool(contract.get("rollback_plan")),
            "rollback_plan": contract.get("rollback_plan"),
            "automatic_rollback_enabled": False,
            "operator_confirmation_required_for_rollback": True,
            "rollback_targets": _startup_surface_write_targets(config),
        },
        "verification_evidence": {
            "finalized": False,
            "required_before_success_marker": True,
            "verification_commands": contract.get("verification_commands") or [],
            "must_record_before_state": True,
            "must_record_after_state": True,
            "must_record_target_reached": True,
            "must_emit_agent_activity_audit": True,
        },
        "transaction_order": {
            "provided": has_transaction_material,
            "ready": bool(transaction.get("ready")),
            "status": transaction.get("status") if has_transaction_material else "missing",
            "blocked_reasons": list(dict.fromkeys(transaction_blockers)),
            "source_readiness": readiness,
        },
        "enablement_decision": {
            "may_enable_executor_now": False,
            "decision": "deny",
            "requires_policy_approval": True,
            "requires_executor_enablement_pass": True,
        },
        "source_contract": contract,
        "boundary": "Host-boundary policy packet only. No approval consumption, idempotency marker write, Startup-folder mutation, Task Scheduler mutation, process mutation, or host startup mutation is performed.",
    }


def build_startup_surface_host_mutation_audit_template_report(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    gate_approval_id: str | None = None,
    plan_digest: str | None = None,
) -> dict[str, Any]:
    """Define required audit evidence for a future host mutation executor.

    This is a read-only template packet. It does not consume approvals, write
    idempotency markers, emit Agent-Activity records, or mutate host startup
    state. It only defines the evidence a future executor must produce before a
    durable success marker can be accepted.
    """
    host_policy = build_startup_surface_host_boundary_policy_report(
        runtime_id,
        surface_id,
        intent,
        gate_approval_id=gate_approval_id,
        plan_digest=plan_digest,
    )
    contract = host_policy.get("source_contract") or build_startup_surface_mutation_contract(runtime_id, surface_id, intent)
    normalized_runtime = str(host_policy.get("runtime_id") or runtime_id)
    normalized_surface = str(host_policy.get("surface_id") or surface_id)
    normalized_intent = str(host_policy.get("intent") or intent)
    has_transaction_material = bool((gate_approval_id or "").strip() and (plan_digest or "").strip())
    transaction = host_policy.get("transaction_order") or {}
    transaction_blockers = list(transaction.get("blocked_reasons") or [])
    if not has_transaction_material:
        transaction_blockers.append("transaction-order-material-missing")

    required_evidence_fields = [
        "runtime_id",
        "surface_id",
        "intent",
        "gate_approval_id",
        "plan_digest_sha256",
        "host_boundary_policy_status",
        "selected_host_api",
        "before_state",
        "after_state",
        "target_state",
        "target_reached",
        "verification_commands",
        "verification_result",
        "rollback_plan",
        "rollback_result",
        "approval_consumption_id",
        "idempotency_marker_path",
        "operator_confirmation_phrase",
        "host_mutation_attempted",
        "startup_surface_mutation_executed",
        "timestamp_utc",
    ]
    blocked_reasons = list(dict.fromkeys([
        *transaction_blockers,
        "audit-template-not-approved",
        "success-marker-acceptance-policy-not-approved",
        "host-executor-still-disabled",
    ]))
    filename_slug = f"hermes-optimus-runtime-startup-surface-mutation-{normalized_runtime}-{normalized_surface}"
    return {
        "action": "startup-surface-host-mutation-audit-template",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "runtime_id": normalized_runtime,
        "runtime_name": contract.get("runtime_name"),
        "surface_id": normalized_surface,
        "intent": normalized_intent,
        "gate_approval_id": gate_approval_id,
        "plan_digest_sha256": host_policy.get("plan_digest_sha256") or contract.get("plan_digest_sha256"),
        "audit_template_status": "blocked",
        "read_only": True,
        "mutation_enabled": False,
        "execution_enabled": False,
        "executor_enabled_now": False,
        "executes_mutation": False,
        "host_mutation_attempted": False,
        "startup_surface_mutation_executed": False,
        "approval_consumed": False,
        "idempotency_marker_written": False,
        "blocked_reasons": blocked_reasons,
        "required_evidence_fields": required_evidence_fields,
        "agent_activity_template": {
            "required": True,
            "finalized": False,
            "filename_slug": filename_slug,
            "path_pattern": f"07_LOGS/Agent-Activity/YYYY-MM-DD-{filename_slug}.md",
            "required_frontmatter": {
                "runtime": "hermes",
                "runtime_node": "[[Hermes-Runtime-Profile]]",
                "root_node": "[[HERMES]]",
                "index": "[[Agent-Activity-Index]]",
                "status": "host-mutation-audit-required",
            },
            "required_graph_links": [
                "[[Hermes-Runtime-Profile]]",
                "[[HERMES]]",
                "[[Agent-Activity-Index]]",
            ],
            "required_sections": [
                "Summary",
                "Authority envelope",
                "Before/after startup state",
                "Host boundary and selected API",
                "Approval consumption and idempotency marker",
                "Verification evidence",
                "Rollback result",
                "Boundaries preserved",
            ],
        },
        "success_marker_acceptance": {
            "allowed_now": False,
            "requires_agent_activity_record": True,
            "requires_all_evidence_fields": True,
            "requires_verified_target_reached": True,
            "requires_rollback_result": True,
            "requires_no_unreported_host_error": True,
            "marker_write_allowed_before_audit": False,
        },
        "transaction_order": {
            "provided": has_transaction_material,
            "ready": bool(transaction.get("ready")),
            "status": transaction.get("status") if has_transaction_material else "missing",
            "blocked_reasons": list(dict.fromkeys(transaction_blockers)),
        },
        "host_boundary_policy": host_policy,
        "source_contract": contract,
        "boundary": "Audit template packet only. No approval consumption, idempotency marker write, Agent-Activity write, Startup-folder mutation, Task Scheduler mutation, process mutation, or host startup mutation is performed.",
    }


def build_startup_surface_success_marker_evidence_verifier_report(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    gate_approval_id: str | None = None,
    plan_digest: str | None = None,
    candidate_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only verifier for future host-mutation success-marker evidence.

    This verifier scores candidate evidence against the audit template and keeps
    success-marker acceptance blocked until ChaseOS policy explicitly approves
    that acceptance path. It does not consume approvals, write markers, emit
    Agent-Activity records, or mutate host startup state.
    """
    audit_template = build_startup_surface_host_mutation_audit_template_report(
        runtime_id,
        surface_id,
        intent,
        gate_approval_id=gate_approval_id,
        plan_digest=plan_digest,
    )
    candidate = candidate_evidence if isinstance(candidate_evidence, dict) else {}
    candidate_present = bool(candidate)
    required_fields = list(audit_template.get("required_evidence_fields") or [])
    missing_fields = [
        field
        for field in required_fields
        if field not in candidate or candidate.get(field) is None or candidate.get(field) == ""
    ]
    agent_template = audit_template.get("agent_activity_template") or {}
    required_graph_links = list(agent_template.get("required_graph_links") or [])
    candidate_graph_links = set(candidate.get("agent_activity_graph_links") or [])
    missing_graph_links = [link for link in required_graph_links if link not in candidate_graph_links]
    required_sections = list(agent_template.get("required_sections") or [])
    candidate_sections = set(candidate.get("agent_activity_sections") or [])
    missing_sections = [section for section in required_sections if section not in candidate_sections]

    expected_runtime = str(audit_template.get("runtime_id") or runtime_id)
    expected_surface = str(audit_template.get("surface_id") or surface_id)
    expected_intent = str(audit_template.get("intent") or intent)
    expected_digest = audit_template.get("plan_digest_sha256")
    matches_transaction = bool(candidate_present)
    transaction_mismatches: list[str] = []
    comparisons = {
        "runtime_id": expected_runtime,
        "surface_id": expected_surface,
        "intent": expected_intent,
        "gate_approval_id": gate_approval_id,
        "plan_digest_sha256": expected_digest,
    }
    for field, expected in comparisons.items():
        if expected is None:
            continue
        if str(candidate.get(field)) != str(expected):
            matches_transaction = False
            transaction_mismatches.append(field)

    verification_result = candidate.get("verification_result") if isinstance(candidate.get("verification_result"), dict) else {}
    rollback_result = candidate.get("rollback_result") if isinstance(candidate.get("rollback_result"), dict) else {}
    target_reached_verified = candidate.get("target_reached") is True and bool(verification_result.get("ok", True))
    rollback_recorded = bool(rollback_result)
    evidence_complete = bool(
        candidate_present
        and not missing_fields
        and not missing_graph_links
        and not missing_sections
        and target_reached_verified
        and rollback_recorded
        and matches_transaction
    )

    blocked_reasons: list[str] = []
    if not candidate_present:
        blocked_reasons.append("candidate-evidence-missing")
    if missing_fields:
        blocked_reasons.append("required-evidence-fields-missing")
    if missing_graph_links:
        blocked_reasons.append("agent-activity-graph-links-missing")
    if missing_sections:
        blocked_reasons.append("agent-activity-sections-missing")
    if not target_reached_verified:
        blocked_reasons.append("target-reached-not-verified")
    if not rollback_recorded:
        blocked_reasons.append("rollback-result-missing")
    if not matches_transaction:
        blocked_reasons.append("transaction-evidence-mismatch")
    blocked_reasons.extend(
        [
            "audit-template-not-approved",
            "success-marker-acceptance-policy-not-approved",
            "host-executor-still-disabled",
        ]
    )
    blocked_reasons = list(dict.fromkeys([*blocked_reasons, *list(audit_template.get("blocked_reasons") or [])]))

    return {
        "action": "startup-surface-success-marker-evidence-verifier",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "runtime_id": expected_runtime,
        "runtime_name": audit_template.get("runtime_name"),
        "surface_id": expected_surface,
        "intent": expected_intent,
        "gate_approval_id": gate_approval_id,
        "plan_digest_sha256": expected_digest,
        "verifier_status": "complete-but-blocked" if evidence_complete else "rejected",
        "read_only": True,
        "mutation_enabled": False,
        "execution_enabled": False,
        "executor_enabled_now": False,
        "executes_mutation": False,
        "host_mutation_attempted": False,
        "startup_surface_mutation_executed": False,
        "approval_consumed": False,
        "idempotency_marker_written": False,
        "candidate_evidence_present": candidate_present,
        "evidence_check": {
            "evidence_complete": evidence_complete,
            "required_fields_present": not missing_fields,
            "missing_required_fields": missing_fields,
            "graph_links_present": not missing_graph_links,
            "missing_graph_links": missing_graph_links,
            "sections_present": not missing_sections,
            "missing_sections": missing_sections,
            "target_reached_verified": target_reached_verified,
            "rollback_result_recorded": rollback_recorded,
            "candidate_matches_transaction": matches_transaction,
            "transaction_mismatches": transaction_mismatches,
        },
        "success_marker_acceptance": {
            "allowed_now": False,
            "decision": "deny",
            "would_accept_if_policy_enabled_and_evidence_complete": evidence_complete,
            "marker_write_allowed": False,
            "requires_gate_promotion": True,
            "requires_policy_approval": True,
        },
        "blocked_reasons": blocked_reasons,
        "audit_template": audit_template,
        "boundary": "Success-marker evidence verifier only. No approval consumption, idempotency marker write, Agent-Activity write, Startup-folder mutation, Task Scheduler mutation, process mutation, success-marker acceptance, or host startup mutation is performed.",
    }


def build_startup_surface_success_marker_acceptance_policy_report(
    runtime_id: str,
    surface_id: str,
    intent: str,
    *,
    gate_approval_id: str | None = None,
    plan_digest: str | None = None,
    candidate_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only success-marker acceptance policy packet.

    This reports whether a future success-marker candidate would satisfy the
    evidence policy, but it never writes the marker and never authorizes host
    startup mutation. It is the policy/acceptance layer above the evidence
    verifier and remains fail-closed until ChaseOS explicitly approves marker
    acceptance.
    """
    verifier = build_startup_surface_success_marker_evidence_verifier_report(
        runtime_id,
        surface_id,
        intent,
        gate_approval_id=gate_approval_id,
        plan_digest=plan_digest,
        candidate_evidence=candidate_evidence,
    )
    evidence_check = verifier.get("evidence_check") or {}
    evidence_complete = bool(evidence_check.get("evidence_complete"))
    expected_digest = verifier.get("plan_digest_sha256")
    marker_slug = "-".join(
        [
            "startup-surface-success-marker",
            _safe_runtime_slug(verifier.get("runtime_id") or runtime_id),
            _safe_runtime_slug(verifier.get("surface_id") or surface_id),
            _safe_runtime_slug(verifier.get("intent") or intent),
        ]
    )
    marker_path = STARTUP_SURFACE_MUTATION_DIR / "success-markers" / f"{marker_slug}.json"

    policy_requirements = {
        "verified_evidence_required": True,
        "gate_policy_approval_required": True,
        "operator_final_confirmation_required": True,
        "approval_consumption_required": True,
        "idempotency_marker_required": True,
        "success_marker_write_gate_required": True,
        "host_executor_completion_required": True,
        "agent_activity_log_required": True,
        "rollback_evidence_required": True,
        "no_runtime_self_certification": True,
    }
    blocked_reasons: list[str] = []
    if not evidence_complete:
        blocked_reasons.append("verified-evidence-missing")
    blocked_reasons.extend(
        [
            "success-marker-acceptance-policy-not-approved",
            "success-marker-write-gate-not-approved",
            "host-executor-still-disabled",
            "approval-consumption-not-enabled-for-success-marker",
            "idempotency-marker-write-not-enabled-for-success-marker",
            "operator-final-confirmation-missing",
        ]
    )
    blocked_reasons = list(dict.fromkeys([*blocked_reasons, *list(verifier.get("blocked_reasons") or [])]))

    return {
        "action": "startup-surface-success-marker-acceptance-policy",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "runtime_id": verifier.get("runtime_id"),
        "runtime_name": verifier.get("runtime_name"),
        "surface_id": verifier.get("surface_id"),
        "intent": verifier.get("intent"),
        "gate_approval_id": gate_approval_id,
        "plan_digest_sha256": expected_digest,
        "acceptance_policy_status": "blocked",
        "read_only": True,
        "mutation_enabled": False,
        "execution_enabled": False,
        "executor_enabled_now": False,
        "executes_mutation": False,
        "host_mutation_attempted": False,
        "startup_surface_mutation_executed": False,
        "approval_consumed": False,
        "idempotency_marker_written": False,
        "success_marker_written": False,
        "policy_requirements": policy_requirements,
        "success_marker_acceptance": {
            "allowed_now": False,
            "decision": "deny",
            "would_accept_if_policy_enabled": evidence_complete,
            "marker_write_allowed": False,
            "marker_path": str(marker_path),
            "marker_present": marker_path.exists(),
            "requires_gate_promotion": True,
            "requires_policy_approval": True,
            "requires_operator_final_confirmation": True,
        },
        "evidence_verifier": verifier,
        "blocked_reasons": blocked_reasons,
        "boundary": "Success-marker acceptance policy report only. No success marker, approval consumption, idempotency marker, Agent-Activity log, Startup-folder mutation, Task Scheduler mutation, process mutation, or host startup mutation is written or accepted.",
    }


def build_startup_surface_toggle_plan(runtime_id: str, surface_id: str, intent: str) -> dict[str, Any]:
    normalized_runtime = (runtime_id or "").strip().lower()
    normalized_surface = (surface_id or "").strip()
    normalized_intent = (intent or "").strip().lower()

    if not normalized_runtime or normalized_runtime in {"all", "*"}:
        raise ValueError("startup-surface toggle planning requires one concrete --runtime value")
    if not normalized_surface:
        raise ValueError("startup-surface toggle planning requires --surface")
    if normalized_intent not in VALID_TOGGLE_INTENTS:
        raise ValueError("startup-surface toggle planning requires --intent enable|disable")

    runtime_report = build_runtime_startup_surfaces_report(normalized_runtime)
    surface = _find_surface(runtime_report, normalized_surface)
    if surface is None:
        available = [item.get("surface_id") for item in runtime_report.get("surfaces", [])]
        raise ValueError(f"unknown startup surface {normalized_surface!r}; available surfaces: {', '.join(str(item) for item in available)}")

    supported = bool(surface.get("supported"))
    toggle_supported = bool(surface.get("toggle_supported"))
    steps = _plan_steps_for_surface(normalized_runtime, surface, normalized_intent) if supported and toggle_supported else []
    blocked_reasons = []
    if not supported:
        blocked_reasons.append("surface-not-supported")
    if not toggle_supported:
        blocked_reasons.append("toggle-not-supported")

    return {
        "action": "startup-surface-toggle-plan",
        "schema_version": 1,
        "generated_at_utc": _utc_now_iso(),
        "runtime_id": normalized_runtime,
        "runtime_name": runtime_report.get("runtime_name"),
        "surface_id": normalized_surface,
        "intent": normalized_intent,
        "current_state": surface.get("state"),
        "target_state": _target_state_for_surface(surface, normalized_intent),
        "read_only": True,
        "mutation_enabled": False,
        "executes_mutation": False,
        "plan_status": "service-layer-ready" if not blocked_reasons else "blocked",
        "blocked_reasons": blocked_reasons,
        "surface": surface,
        "steps": steps,
        "future_mutation_required": any(step.get("mutates") for step in steps),
        "boundary": "Plan only. This command does not enable, disable, register, unregister, start, stop, remove, create, or edit host startup state.",
    }
