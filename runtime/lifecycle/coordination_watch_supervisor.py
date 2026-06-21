"""ChaseOS coordination-watch supervision foothold.

Provides a bounded local supervisor for lifecycle-owned coordination-watch loops.
This is not full OS service management yet; it is a ChaseOS-owned bootstrap layer
that can plan, start, inspect, and stop the background loop process per runtime.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.lifecycle.coordination_watch import _coerce_bool, load_coordination_watch_config
from runtime.lifecycle.health_cli import load_lifecycle_record

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_DIR = ROOT / "runtime" / "lifecycle" / "run"
DEFAULT_REGISTRY_PATH = ROOT / "runtime" / "lifecycle" / "runtime-registry.json"
DEFAULT_MAINTENANCE_PATH = ROOT / "runtime" / "lifecycle" / "state" / "maintenance-mode.json"
DEFAULT_LOCK_DIR = ROOT / "runtime" / "lifecycle" / "locks"
DEFAULT_EVENT_LOG = DEFAULT_RUN_DIR / "coordination-watch-supervisor-events.jsonl"
DEFAULT_LOCK_TTL_SECONDS = 900

# ── Module logger ─────────────────────────────────────────────────────────────
_log = logging.getLogger("chaseos.lifecycle.supervisor")

# ── Subprocess result cache (TTL-based, per runtime_id) ──────────────────────
# Prevents calling tasklist/wsl/powershell more than once per TTL window even
# when get_dashboard() is called from multiple paths in the same cycle.
_STATUS_CACHE_TTL = 45.0          # seconds between live subprocess probes
_status_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_status_cache_lock = threading.Lock()


def _windows_no_window_subprocess_kwargs(*, detached: bool = False) -> dict[str, Any]:
    """Return Windows subprocess options that suppress transient consoles.

    Live terminal-spam captures showed daemon and WSL bridge children allocating
    short-lived conhost windows even when the parent was launched from a hidden
    surface.  Pair CREATE_NO_WINDOW with hidden STARTUPINFO, and use
    DETACHED_PROCESS only for long-lived daemon Popen launches where we do not
    need child console inheritance.
    """
    if os.name != "nt":
        return {}
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    if detached:
        flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0) or 0)
    kwargs: dict[str, Any] = {"creationflags": flags}
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0) or 0)
        startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0) or 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json_object(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {"error": "invalid_json", "path": str(path)}
    if not isinstance(loaded, dict):
        return {"error": "invalid_json_object", "path": str(path)}
    return loaded


def _write_json_object_if_missing(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _is_wsl_environment() -> bool:
    if os.name == "nt":
        return False
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "microsoft" in release.lower() or "wsl" in release.lower()


def _windows_host_path_for_wsl(path: Path) -> Path:
    """Return a Windows-native path for repo-local Windows tools when called from WSL.

    WSL can launch Windows executables, but Windows Python does not reliably
    interpret `/mnt/c/...` arguments as Windows paths; it can see them as
    `C:\\mnt\\c\\...` and fail to open chaseos.py. Convert mounted drive paths
    back to `C:\\...` before passing them to Windows-hosted supervisors.
    """
    if os.name == "nt" or not _is_wsl_environment():
        return path
    resolved = path.resolve()
    parts = resolved.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "mnt" and len(parts[2]) == 1:
        drive = parts[2].upper()
        remainder = "\\".join(parts[3:])
        return Path(f"{drive}:\\{remainder}" if remainder else f"{drive}:\\")
    return path


def _windows_to_wsl_vault_path(path: str | Path) -> str:
    """Translate a Windows vault path to its WSL `/mnt/<drive>/...` equivalent.

    Generic — derives from the drive letter, never hardcodes a user/path. So
    `C:\\Users\\alice\\chaseos` -> `/mnt/c/Users/alice/chaseos` for ANY user.
    Already-POSIX paths and UNC/other shapes are returned unchanged.
    """
    raw = str(path).strip()
    if not raw:
        return raw
    # Already a POSIX/WSL path.
    if raw.startswith("/"):
        return raw
    drive = ""
    rest = raw
    if len(raw) >= 2 and raw[1] == ":" and raw[0].isalpha():
        drive = raw[0].lower()
        rest = raw[2:]
    else:
        return raw  # not a drive-letter path; leave as-is
    rest = rest.replace("\\", "/").lstrip("/")
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


def _resolve_consumer_vault_root(config: dict[str, Any]) -> str:
    """Resolve the `--vault-root` to pass to the runtime daemon, portably.

    The vault-root MUST be valid on the filesystem of the process that actually
    runs the command — otherwise the daemon opens the wrong (or no) Agent Bus.

    Resolution order (Core-portable; no hardcoded user path):
      1. Explicit `vault_root` override — the per-user knob: another user declares
         where their SHARED vault lives from the daemon's own filesystem view.
      2. If `launch_via_wsl` is enabled, the command is wrapped in `wsl -d <distro>`
         and runs INSIDE WSL, so translate the supervisor ROOT to `/mnt/<drive>/...`.
      3. Otherwise the supervisor ROOT (the daemon runs on this same host).

    The goal is always the same: the daemon must open the SAME Agent Bus file Studio
    writes to (`<vault>/runtime/agent_bus/agent_bus.sqlite`).
    """
    override = str(config.get("vault_root") or "").strip()
    supervisor_host = str(config.get("supervisor_host") or config.get("platform") or "").strip().lower()
    if override:
        if supervisor_host == "windows" and not bool(config.get("launch_via_wsl")):
            return str(_windows_host_path_for_wsl(Path(override)))
        return override
    if bool(config.get("launch_via_wsl")):
        return _windows_to_wsl_vault_path(ROOT)
    if supervisor_host == "windows":
        return str(_windows_host_path_for_wsl(ROOT))
    return str(ROOT)


def _wsl_chaseos_entrypoint() -> str:
    """The repo chaseos.py as seen from inside WSL (`/mnt/<drive>/...`)."""
    return _windows_to_wsl_vault_path(ROOT / "chaseos.py")


def _coordination_watch_python_executable(supervisor_host: str | None) -> str:
    if str(supervisor_host or "").strip().lower() == "windows":
        # Prefer the historical Windows venv path when present, but do not
        # hard-code it as the only valid product layout. Modern ChaseOS Studio
        # installs can use versioned Windows venvs (for example .venv-win314),
        # and a missing .venv/Scripts/python.exe must not prevent the runtime
        # daemon from starting at ChaseOS launch.
        candidates = [
            # Runtime daemons are long-lived background consumers launched from
            # Studio/Task Scheduler surfaces. Prefer the Windows GUI interpreter
            # so daemon starts cannot allocate a transient console/conhost window.
            ROOT / ".venv" / "Scripts" / "pythonw.exe",
            ROOT / ".venv-win314" / "Scripts" / "pythonw.exe",
            ROOT / ".venv-win" / "Scripts" / "pythonw.exe",
            ROOT / ".venv" / "Scripts" / "python.exe",
            ROOT / ".venv-win314" / "Scripts" / "python.exe",
            ROOT / ".venv-win" / "Scripts" / "python.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate.resolve() if _is_wsl_environment() else _windows_host_path_for_wsl(candidate))
        fallback = candidates[0]
        return str(fallback.resolve() if _is_wsl_environment() else _windows_host_path_for_wsl(fallback))
    # POSIX host (native Linux / macOS / WSL distro). Prefer the project's local
    # venv interpreter so daemon launches resolve the same dependencies the
    # Studio/CLI uses, then fall back to the running interpreter. This mirrors the
    # Windows branch's "prefer project venv, never hard-fail" contract.
    posix_candidates = [
        ROOT / ".venv" / "bin" / "python",
        ROOT / ".venv" / "bin" / "python3",
    ]
    for candidate in posix_candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return sys.executable


def _coordination_watch_chaseos_entrypoint(supervisor_host: str | None) -> str:
    if str(supervisor_host or "").strip().lower() == "windows":
        return str(_windows_host_path_for_wsl(ROOT / "chaseos.py"))
    return str(ROOT / "chaseos.py")


def _resolve_output_path(raw_path: str | None, runtime_id: str, suffix: str) -> Path:
    if raw_path:
        candidate = Path(str(raw_path))
        if candidate.is_absolute():
            return candidate
        return ROOT / candidate
    return DEFAULT_RUN_DIR / f"{runtime_id}-coordination-watch.{suffix}"


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    return int(value)


def load_runtime_registry() -> dict[str, Any]:
    return _read_json_object(DEFAULT_REGISTRY_PATH, default={"runtimes": {}})


def get_runtime_registry_entry(runtime_id: str) -> dict[str, Any]:
    registry = load_runtime_registry()
    runtimes = registry.get("runtimes") or {}
    entry = runtimes.get(str(runtime_id).strip().lower()) or {}
    return dict(entry) if isinstance(entry, dict) else {}


def _default_maintenance_payload() -> dict[str, Any]:
    return {
        "schema_version": "2026-05-05.runtime-maintenance-mode.v1",
        "active": False,
        "all_runtimes": False,
        "runtimes": {},
        "notes": "Set active/all_runtimes or runtimes.<runtime_id>.active true during manual repair/OAuth/config/Discord/gateway work.",
    }


def ensure_maintenance_mode_file() -> Path:
    _write_json_object_if_missing(DEFAULT_MAINTENANCE_PATH, _default_maintenance_payload())
    return DEFAULT_MAINTENANCE_PATH


def get_maintenance_mode_status(runtime_id: str) -> dict[str, Any]:
    path = ensure_maintenance_mode_file()
    payload = _read_json_object(path, default=_default_maintenance_payload())
    runtime_key = str(runtime_id).strip().lower()
    runtime_payload = (payload.get("runtimes") or {}).get(runtime_key) or {}
    active = bool(payload.get("all_runtimes") or (payload.get("active") and payload.get("all_runtimes")) or runtime_payload.get("active"))
    legacy_markers: list[dict[str, Any]] = []
    state_dir = DEFAULT_MAINTENANCE_PATH.parent
    if state_dir.exists():
        for marker_path in sorted(state_dir.glob("maintenance-*.json")):
            if marker_path == path:
                continue
            marker_payload = _read_json_object(marker_path, default={})
            scope = str(marker_payload.get("scope") or marker_payload.get("runtime") or "").strip().lower()
            marker_active = bool(marker_payload.get("maintenanceMode") or marker_payload.get("active"))
            applies = marker_active and scope in {"", "all", "runtimes", runtime_key}
            if applies or scope == runtime_key:
                legacy_markers.append(
                    {
                        "path": str(marker_path),
                        "active": marker_active,
                        "scope": scope or None,
                        "reason": marker_payload.get("reason"),
                    }
                )
                active = active or applies
    active_marker = next((marker for marker in legacy_markers if marker.get("active")), None)
    return {
        "path": str(path),
        "active": active,
        "all_runtimes": bool(payload.get("all_runtimes")),
        "runtime_active": bool(runtime_payload.get("active")),
        "legacy_markers": legacy_markers,
        "reason": runtime_payload.get("reason") or payload.get("reason") or (active_marker or {}).get("reason"),
        "payload_error": payload.get("error"),
    }


def _append_supervisor_event(payload: dict[str, Any]) -> None:
    DEFAULT_EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    event = {"timestamp_utc": _utc_now_iso(), **payload}
    with DEFAULT_EVENT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lock_path(runtime_id: str) -> Path:
    return DEFAULT_LOCK_DIR / f"{str(runtime_id).strip().lower()}.lock"


def read_runtime_lock(runtime_id: str) -> dict[str, Any]:
    path = _lock_path(runtime_id)
    payload = _read_json_object(path, default={})
    timestamp = _parse_iso_timestamp(str(payload.get("timestamp_utc") or ""))
    age_seconds = None
    fresh = False
    if timestamp is not None:
        age_seconds = max(0, int((datetime.now(timezone.utc) - timestamp).total_seconds()))
        fresh = age_seconds <= int(payload.get("ttl_seconds") or DEFAULT_LOCK_TTL_SECONDS)
    return {
        "path": str(path),
        "present": path.exists(),
        "fresh": fresh,
        "age_seconds": age_seconds,
        "payload": payload,
    }


def write_runtime_lock(runtime_id: str, *, pid: int | None = None, action: str = "start") -> None:
    path = _lock_path(runtime_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "runtime_id": runtime_id,
                "pid": pid,
                "action": action,
                "timestamp_utc": _utc_now_iso(),
                "ttl_seconds": DEFAULT_LOCK_TTL_SECONDS,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def clear_runtime_lock(runtime_id: str) -> None:
    path = _lock_path(runtime_id)
    if path.exists():
        path.unlink()


def _safe_unlink(path: Path) -> bool:
    try:
        if path.exists() and path.is_file():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def _run_port_command(command: list[str], timeout: int = 5) -> subprocess.CompletedProcess[str] | None:
    t0 = time.monotonic()
    label = command[0] if command else "?"
    _log.debug("subprocess START  cmd=%s timeout=%ss", label, timeout)
    try:
        kwargs: dict = {"capture_output": True, "text": True, "check": False, "timeout": timeout}
        kwargs.update(_windows_no_window_subprocess_kwargs())
        result = subprocess.run(command, **kwargs)
        elapsed = time.monotonic() - t0
        _log.debug("subprocess OK     cmd=%s rc=%s elapsed=%.3fs", label, result.returncode, elapsed)
        return result
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        _log.warning("subprocess TIMEOUT cmd=%s elapsed=%.3fs", label, elapsed)
        return None
    except OSError as exc:
        elapsed = time.monotonic() - t0
        _log.warning("subprocess OSERROR cmd=%s err=%s elapsed=%.3fs", label, exc, elapsed)
        return None


def _process_name_for_pid(pid: int, *, platform: str | None = None) -> str | None:
    if pid <= 0:
        return None
    platform_name = str(platform or "").strip().lower()
    if platform_name == "windows" or os.name == "nt":
        command_name = "tasklist" if os.name == "nt" else "tasklist.exe"
        completed = _run_port_command([command_name, "/FI", f"PID eq {pid}"])
        output = f"{getattr(completed, 'stdout', '')}\n{getattr(completed, 'stderr', '')}" if completed else ""
        for line in output.splitlines():
            if str(pid) in line and "PID" not in line:
                return line.split()[0] if line.split() else None
    return None


def _sanitize_status_text(value: str | None) -> str | None:
    if value is None:
        return None
    import re

    sanitized = re.sub(r"(?i)(token|secret|password|refresh[_-]?token|access[_-]?token)=\S+", r"\1=<redacted>", str(value))
    sanitized = re.sub(r"(?i)(--(?:token|secret|password|refresh-token|access-token))\s+\S+", r"\1 <redacted>", sanitized)
    return sanitized


def _process_command_line_for_pid(pid: int, *, platform: str | None = None) -> str | None:
    if pid <= 0:
        return None
    platform_name = str(platform or "").strip().lower()
    if platform_name == "windows" or os.name == "nt":
        ps = f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\" -ErrorAction SilentlyContinue; if ($p) {{ [Console]::Write($p.CommandLine) }}"
        completed = _run_port_command(["powershell.exe", "-NoProfile", "-Command", ps])
        raw = str(getattr(completed, "stdout", "") or "").strip() if completed else ""
        return _sanitize_status_text(raw) or None
    return None


def _socket_port_listening(port: int, *, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    """Return local TCP listen truth without spawning PowerShell/netstat/ss.

    Studio polls runtime status from a Windows pythonw.exe GUI process. External
    port-owner probes such as PowerShell Get-NetTCPConnection can allocate
    short-lived console hosts on every poll; the UI only needs listening truth,
    not PID ownership, so use a socket probe for the passive status path.
    """
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def get_port_ownership_status(runtime_id: str, *, require_owner: bool = True) -> dict[str, Any]:
    entry = get_runtime_registry_entry(runtime_id)
    port = entry.get("gateway_port")
    if port in (None, ""):
        return {"checked": False, "reason": "no_gateway_port_in_registry", "registry_entry": entry}
    platform = str(entry.get("os_boundary") or "").lower()
    if not require_owner:
        listening = _socket_port_listening(int(port))
        return {
            "checked": True,
            "runtime_id": runtime_id,
            "port": int(port),
            "listening": listening,
            "pid": None,
            "process_name": None,
            "command_line": None,
            "belongs_to_runtime": bool(listening),
            "conflict": False,
            "dashboard_url": entry.get("dashboard_url"),
            "probe_method": "socket_no_subprocess",
            "ownership_probe_skipped": True,
        }
    pid = None
    if "windows" in platform or str(runtime_id).strip().lower() == "openclaw":
        ps = (
            f"$c = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; "
            "if ($c) { [Console]::Write($c.OwningProcess) }"
        )
        completed = _run_port_command(["powershell.exe", "-NoProfile", "-Command", ps])
        raw = str(getattr(completed, "stdout", "") or "").strip() if completed else ""
        if raw.isdigit():
            pid = int(raw)
    else:
        completed = _run_port_command(["bash", "-lc", f"ss -ltnp 'sport = :{int(port)}' 2>/dev/null | tail -n +2"])
        raw = str(getattr(completed, "stdout", "") or "") if completed else ""
        import re

        match = re.search(r"pid=(\d+)", raw)
        if match:
            pid = int(match.group(1))
        elif raw.strip():
            pid = -1
    listening = pid is not None
    owner_platform = "windows" if "windows" in platform else None
    process_name = _process_name_for_pid(pid or 0, platform=owner_platform)
    command_line = _process_command_line_for_pid(pid or 0, platform=owner_platform)
    markers = [str(marker).lower() for marker in (entry.get("runtime_process_markers") or [])]
    identity_text = f"{process_name or ''} {command_line or ''} {pid or ''}".lower()
    belongs_to_runtime = bool(listening and (not markers or any(marker in identity_text for marker in markers)))
    return {
        "checked": True,
        "runtime_id": runtime_id,
        "port": int(port),
        "listening": listening,
        "pid": pid,
        "process_name": process_name,
        "command_line": command_line,
        "belongs_to_runtime": belongs_to_runtime,
        "conflict": bool(listening and not belongs_to_runtime),
        "dashboard_url": entry.get("dashboard_url"),
    }


def _pid_running(pid: int, *, platform: str | None = None, wsl_distro: str | None = None) -> bool:
    if pid <= 0:
        return False
    platform_name = str(platform or "").strip().lower()
    if platform_name == "windows" and os.name != "nt":
        tasklist_result = _pid_running_tasklist(pid)
        if tasklist_result is not None:
            return tasklist_result
        powershell_result = _pid_running_powershell(pid)
        if powershell_result is not None:
            return powershell_result
        return False
    if os.name == "nt" and platform_name == "wsl":
        wsl_result = _pid_running_wsl(pid, wsl_distro=wsl_distro)
        if wsl_result is not None:
            return wsl_result
        return False
    if os.name == "nt":
        tasklist_result = _pid_running_tasklist(pid)
        if tasklist_result is not None:
            return tasklist_result
        api_result = _pid_running_windows_api(pid)
        if api_result is not None:
            return api_result
        powershell_result = _pid_running_powershell(pid)
        if powershell_result is not None:
            return powershell_result
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_running_wsl(pid: int, *, wsl_distro: str | None = None) -> bool | None:
    """Return WSL PID truth from a Windows host, or None when unavailable."""
    command = ["wsl.exe"]
    distro = str(wsl_distro or "").strip()
    if distro:
        command.extend(["-d", distro])
    command.extend(["--", "bash", "-lc", f"ps -p {int(pid)} -o pid= >/dev/null"])
    t0 = time.monotonic()
    _log.debug("subprocess START  cmd=wsl.exe(bash) pid=%s", pid)
    try:
        kwargs: dict = {"capture_output": True, "text": True, "check": False, "timeout": 5}
        kwargs.update(_windows_no_window_subprocess_kwargs())
        completed = subprocess.run(command, **kwargs)
        _log.debug("subprocess OK     cmd=wsl.exe(bash) pid=%s rc=%s elapsed=%.3fs",
                   pid, completed.returncode, time.monotonic() - t0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.warning("subprocess FAIL   cmd=wsl.exe(bash) pid=%s err=%s elapsed=%.3fs",
                     pid, exc, time.monotonic() - t0)
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def _pid_running_tasklist(pid: int) -> bool | None:
    """Return Windows tasklist PID truth, or None when tasklist is unavailable."""
    command_name = "tasklist" if os.name == "nt" else "tasklist.exe"
    t0 = time.monotonic()
    _log.debug("subprocess START  cmd=%s pid=%s", command_name, pid)
    try:
        kwargs: dict = {
            "capture_output": True, "text": True, "check": False, "timeout": 5,
        }
        kwargs.update(_windows_no_window_subprocess_kwargs())
        completed = subprocess.run([command_name, "/FI", f"PID eq {pid}"], **kwargs)
        _log.debug("subprocess OK     cmd=%s pid=%s rc=%s elapsed=%.3fs",
                   command_name, pid, completed.returncode, time.monotonic() - t0)
    except subprocess.TimeoutExpired:
        _log.warning("subprocess TIMEOUT cmd=%s pid=%s elapsed=%.3fs",
                     command_name, pid, time.monotonic() - t0)
        return None
    except OSError as exc:
        _log.warning("subprocess OSERROR cmd=%s pid=%s err=%s elapsed=%.3fs",
                     command_name, pid, exc, time.monotonic() - t0)
        return None
    output = f"{completed.stdout}\n{completed.stderr}"
    if "Access denied" in output or "Access is denied" in output:
        return None
    if "No tasks are running" in output:
        return False
    if completed.returncode not in (0, None) and str(pid) not in output:
        return None
    return str(pid) in output


def _pid_running_windows_api(pid: int) -> bool | None:
    """Return Windows API PID truth, or None when the API cannot answer."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        process_query_limited_information = 0x1000
        still_active = 259

        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            last_error = kernel32.GetLastError()
            if last_error == 87:  # ERROR_INVALID_PARAMETER: PID does not exist.
                return False
            return None

        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _pid_running_powershell(pid: int) -> bool | None:
    """Return PowerShell Get-Process PID truth, or None when unavailable."""
    command = (
        f"$p = Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; "
        "if ($p) { exit 0 } else { exit 1 }"
    )
    t0 = time.monotonic()
    _log.debug("subprocess START  cmd=powershell.exe pid=%s", pid)
    try:
        kwargs: dict = {
            "capture_output": True, "text": True, "check": False, "timeout": 5,
        }
        kwargs.update(_windows_no_window_subprocess_kwargs())
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            **kwargs,
        )
        _log.debug("subprocess OK     cmd=powershell.exe pid=%s rc=%s elapsed=%.3fs",
                   pid, completed.returncode, time.monotonic() - t0)
    except subprocess.TimeoutExpired:
        _log.warning("subprocess TIMEOUT cmd=powershell.exe pid=%s elapsed=%.3fs",
                     pid, time.monotonic() - t0)
        return None
    except OSError as exc:
        _log.warning("subprocess OSERROR cmd=powershell.exe pid=%s err=%s elapsed=%.3fs",
                     pid, exc, time.monotonic() - t0)
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def _terminate_pid(
    pid: int,
    force: bool = False,
    *,
    platform: str | None = None,
    wsl_distro: str | None = None,
) -> None:
    if pid <= 0:
        return
    platform_name = str(platform or "").strip().lower()
    if os.name == "nt" and platform_name == "wsl":
        command = ["wsl.exe"]
        distro = str(wsl_distro or "").strip()
        if distro:
            command.extend(["-d", distro])
        signal_name = "-KILL" if force else "-TERM"
        command.extend(["--", "kill", signal_name, str(pid)])
        _kwargs: dict = {"capture_output": True, "text": True, "check": False, "timeout": 5}
        _kwargs.update(_windows_no_window_subprocess_kwargs())
        subprocess.run(command, **_kwargs)
        return
    if platform_name == "windows" and os.name != "nt":
        command = ["taskkill.exe", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        _kwargs = {"capture_output": True, "text": True, "check": False}
        _kwargs.update(_windows_no_window_subprocess_kwargs())
        subprocess.run(command, **_kwargs)
        return
    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        _kwargs = {"capture_output": True, "text": True, "check": False}
        _kwargs.update(_windows_no_window_subprocess_kwargs())
        subprocess.run(command, **_kwargs)
        return
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def load_coordination_watch_supervision_config(runtime_id: str) -> dict[str, Any]:
    record = load_lifecycle_record(runtime_id)
    watch = load_coordination_watch_config(runtime_id)
    supervision = dict(watch.get("supervision") or {})
    bootstrap = dict(watch.get("bootstrap") or {})
    if not supervision:
        raise ValueError(f"No coordination_watch.supervision record found for runtime: {runtime_id}")

    runtime_name = str(watch.get("runtime_name") or runtime_id)
    interval_seconds = _coerce_int(watch.get("interval_seconds"), default=None)
    stale_after_seconds = _coerce_int(watch.get("stale_after_seconds"), default=None)
    supervisor_host = str(bootstrap.get("supervisor_host") or "").strip().lower()
    runtime_platform = str(record.get("platform") or "")
    supervisor_platform = "windows" if supervisor_host == "windows" else runtime_platform

    state_file = _resolve_output_path(supervision.get("state_file"), runtime_id, "json")
    log_file = _resolve_output_path(supervision.get("log_file"), runtime_id, "log")

    return {
        "runtime_id": runtime_id,
        "runtime_name": runtime_name,
        "watch_enabled": _coerce_bool(watch.get("enabled"), default=False),
        "interval_seconds": interval_seconds,
        "claim_next": _coerce_bool(watch.get("claim_next"), default=False),
        "stale_after_seconds": stale_after_seconds,
        "platform": supervisor_platform,
        "runtime_platform": runtime_platform,
        "supervisor_host": supervisor_host,
        "wsl_distro": bootstrap.get("wsl_distro"),
        # Per-user portability knob: where the SHARED vault lives from the runtime's
        # own filesystem perspective. Empty => auto-resolve (see _resolve_consumer_vault_root).
        "vault_root": str(supervision.get("vault_root") or watch.get("vault_root") or "").strip() or None,
        # Opt-in: launch the consumer INSIDE WSL via `wsl -d <distro>` (for a
        # credentialed in-WSL Hermes). Off by default => launch on the supervisor host.
        "launch_via_wsl": _coerce_bool(supervision.get("launch_via_wsl"), default=False),
        "wsl_python": str(supervision.get("wsl_python") or bootstrap.get("wsl_python") or "python3").strip(),
        "supervision_enabled": _coerce_bool(supervision.get("enabled"), default=False),
        "autostart": _coerce_bool(supervision.get("autostart"), default=False),
        "restart_policy": str(supervision.get("restart_policy") or "manual"),
        "state_file": str(state_file),
        "log_file": str(log_file),
        "notes": supervision.get("notes") or watch.get("notes"),
    }


def build_supervised_coordination_watch_plan(
    runtime_id: str,
    *,
    interval_seconds: int | None = None,
) -> dict[str, Any]:
    config = load_coordination_watch_supervision_config(runtime_id)
    effective_interval = interval_seconds if interval_seconds is not None else config.get("interval_seconds")
    if effective_interval in (None, ""):
        raise ValueError(f"No interval_seconds configured for runtime: {runtime_id}")

    runtime_key = str(runtime_id).strip().lower()
    vault_root_arg = _resolve_consumer_vault_root(config)
    if bool(config.get("launch_via_wsl")):
        # Run the consumer INSIDE WSL so the credentialed in-WSL runtime claims the
        # tasks; the repo + vault are reached via /mnt. distro/python are configurable.
        distro = str(config.get("wsl_distro") or "Ubuntu").strip() or "Ubuntu"
        command = [
            "wsl", "-d", distro,
            str(config.get("wsl_python") or "python3"),
            _wsl_chaseos_entrypoint(),
            "runtime", "daemon",
            "--runtime", runtime_id,
            "--daemon-interval", str(int(effective_interval)),
            "--daemon-max-tasks", "5",
            "--vault-root", vault_root_arg,
        ]
    else:
        command = [
            _coordination_watch_python_executable(config.get("supervisor_host") or config.get("platform")),
            _coordination_watch_chaseos_entrypoint(config.get("supervisor_host") or config.get("platform")),
            "runtime", "daemon",
            "--runtime", runtime_id,
            "--daemon-interval", str(int(effective_interval)),
            "--daemon-max-tasks", "5",
            "--vault-root", vault_root_arg,
        ]
    if runtime_key == "hermes":
        command.append("--synthesize")

    return {
        "runtime_id": runtime_id,
        "runtime_name": config.get("runtime_name"),
        "watch_enabled": bool(config.get("watch_enabled")),
        "supervision_enabled": bool(config.get("supervision_enabled")),
        "autostart": bool(config.get("autostart")),
        "restart_policy": config.get("restart_policy"),
        "interval_seconds": int(effective_interval),
        "claim_next": bool(config.get("claim_next")),
        "stale_after_seconds": config.get("stale_after_seconds"),
        "platform": config.get("platform"),
        "runtime_platform": config.get("runtime_platform"),
        "supervisor_host": config.get("supervisor_host"),
        "wsl_distro": config.get("wsl_distro"),
        "state_file": config.get("state_file"),
        "log_file": config.get("log_file"),
        "notes": config.get("notes"),
        "command": command,
        "synthesize": runtime_key == "hermes",
    }


def _read_state_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"error": "invalid_state_file", "path": str(path)}


def get_supervised_coordination_watch_status(runtime_id: str) -> dict[str, Any]:
    """Return passive supervision status for a runtime, with a TTL result cache.

    Studio opens and polls this path frequently. Keep it subprocess-free: no
    tasklist.exe, PowerShell, WSL, schtasks, or shell port tools. Active
    lifecycle actions can perform deeper checks; the UI status path must not
    allocate short-lived console hosts.
    """
    now = time.monotonic()
    with _status_cache_lock:
        cached = _status_cache.get(runtime_id)
        if cached is not None:
            cached_at, cached_result = cached
            age = now - cached_at
            if age < _STATUS_CACHE_TTL:
                _log.debug("status cache HIT  runtime=%s age=%.1fs", runtime_id, age)
                return dict(cached_result)   # shallow copy — safe for read-only consumers
            _log.debug("status cache MISS runtime=%s age=%.1fs (TTL=%.0fs)",
                       runtime_id, age, _STATUS_CACHE_TTL)
        else:
            _log.debug("status cache COLD runtime=%s", runtime_id)

    result = _get_supervised_coordination_watch_status_live(runtime_id)

    with _status_cache_lock:
        _status_cache[runtime_id] = (time.monotonic(), result)

    return result


def _get_supervised_coordination_watch_status_live(runtime_id: str) -> dict[str, Any]:
    """Uncached passive implementation — does not spawn process probes."""
    plan = build_supervised_coordination_watch_plan(runtime_id)
    state_path = Path(str(plan["state_file"]))
    state = _read_state_file(state_path)

    status: dict[str, Any] = {
        "action": "status",
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "supervision_enabled": bool(plan.get("supervision_enabled")),
        "watch_enabled": bool(plan.get("watch_enabled")),
        "autostart": bool(plan.get("autostart")),
        "restart_policy": plan.get("restart_policy"),
        "interval_seconds": plan.get("interval_seconds"),
        "claim_next": bool(plan.get("claim_next")),
        "stale_after_seconds": plan.get("stale_after_seconds"),
        "platform": plan.get("platform"),
        "runtime_platform": plan.get("runtime_platform"),
        "supervisor_host": plan.get("supervisor_host"),
        "wsl_distro": plan.get("wsl_distro"),
        "state_file": str(state_path),
        "log_file": str(plan.get("log_file")),
        "event_log": str(DEFAULT_EVENT_LOG),
        "lock": read_runtime_lock(runtime_id),
        "maintenance_mode": get_maintenance_mode_status(runtime_id),
        "port_status": get_port_ownership_status(runtime_id, require_owner=False),
        "command": plan.get("command"),
        "running": False,
        "state_present": state is not None,
    }

    if not state:
        return status

    if state.get("error"):
        status["state_error"] = state.get("error")
        return status

    pid = int(state.get("pid") or 0)
    status.update(
        {
            "pid": pid,
            "started_at": state.get("started_at"),
            "ended_at": state.get("ended_at"),
            "state_status": state.get("status"),
            "last_action": state.get("last_action"),
            "command": state.get("command") or status.get("command"),
        }
    )

    state_status = str(state.get("status") or "").strip().lower()
    if state.get("ended_at") or state_status in {"done", "stopped", "failed", "error"}:
        status["completed_state"] = True
        status["stale_state"] = False
        return status

    port_status = status.get("port_status") or {}
    status["running"] = bool(state_status in {"running", "started", "active"} and port_status.get("listening"))
    status["process_probe_skipped"] = True
    status["process_probe_reason"] = "passive_status_no_subprocess"
    status["stale_state"] = bool(state_status in {"running", "started", "active"} and not port_status.get("listening"))
    return status


def start_supervised_coordination_watch(
    runtime_id: str,
    *,
    interval_seconds: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    plan = build_supervised_coordination_watch_plan(runtime_id, interval_seconds=interval_seconds)
    if not plan.get("watch_enabled"):
        raise ValueError(f"coordination_watch disabled for runtime: {runtime_id}")
    if not plan.get("supervision_enabled"):
        raise ValueError(f"coordination_watch supervision disabled for runtime: {runtime_id}")

    maintenance = get_maintenance_mode_status(runtime_id)
    port_status = get_port_ownership_status(runtime_id)
    lock_status = read_runtime_lock(runtime_id)
    decision_context = {
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "action_requested": "start",
        "dry_run": dry_run,
        "maintenance_mode": maintenance,
        "port_status": port_status,
        "lock": lock_status,
        "command": plan.get("command"),
    }

    if maintenance.get("active"):
        result = {
            "action": "start",
            "runtime_id": runtime_id,
            "runtime_name": plan.get("runtime_name"),
            "started": False,
            "skipped": True,
            "skipped_reason": "maintenance_mode_active",
            **decision_context,
        }
        _append_supervisor_event({**decision_context, "decision": "skip", "result": "maintenance_mode_active"})
        return result

    if lock_status.get("present") and lock_status.get("fresh"):
        result = {
            "action": "start",
            "runtime_id": runtime_id,
            "runtime_name": plan.get("runtime_name"),
            "started": False,
            "skipped": True,
            "skipped_reason": "fresh_lock_present",
            **decision_context,
        }
        _append_supervisor_event({**decision_context, "decision": "skip", "result": "fresh_lock_present"})
        return result

    if port_status.get("conflict"):
        result = {
            "action": "start",
            "runtime_id": runtime_id,
            "runtime_name": plan.get("runtime_name"),
            "started": False,
            "skipped": True,
            "skipped_reason": "port_conflict",
            **decision_context,
        }
        _append_supervisor_event({**decision_context, "decision": "skip", "result": "port_conflict"})
        return result

    existing = get_supervised_coordination_watch_status(runtime_id)
    if existing.get("running"):
        existing["action"] = "start"
        existing["already_running"] = True
        existing["dry_run"] = dry_run
        _append_supervisor_event({**decision_context, "decision": "skip", "result": "already_running", "existing_pid": existing.get("pid")})
        return existing

    if dry_run:
        result = {
            "action": "start",
            "runtime_id": runtime_id,
            "runtime_name": plan.get("runtime_name"),
            "started": False,
            "dry_run": True,
            "would_start": True,
            "state_file": str(plan["state_file"]),
            "log_file": str(plan["log_file"]),
            **decision_context,
        }
        _append_supervisor_event({**decision_context, "decision": "dry_run", "result": "would_start"})
        return result

    state_path = Path(str(plan["state_file"]))
    log_path = Path(str(plan["log_file"]))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_handle = log_path.open("a", encoding="utf-8")
    try:
        popen_kwargs: dict[str, Any] = {
            "cwd": ROOT,
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "start_new_session": True,
        }
        popen_kwargs.update(_windows_no_window_subprocess_kwargs(detached=True))
        process = subprocess.Popen(plan["command"], **popen_kwargs)
    finally:
        log_handle.close()

    write_runtime_lock(runtime_id, pid=int(process.pid), action="start")
    metadata = {
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "pid": int(process.pid),
        "started_at": _utc_now_iso(),
        "last_action": "start",
        "interval_seconds": int(plan["interval_seconds"]),
        "claim_next": bool(plan.get("claim_next")),
        "stale_after_seconds": plan.get("stale_after_seconds"),
        "platform": plan.get("platform"),
        "runtime_platform": plan.get("runtime_platform"),
        "supervisor_host": plan.get("supervisor_host"),
        "wsl_distro": plan.get("wsl_distro"),
        "state_file": str(state_path),
        "log_file": str(log_path),
        "command": plan.get("command"),
        "synthesize": bool(plan.get("synthesize")),
        "restart_policy": plan.get("restart_policy"),
        "autostart": bool(plan.get("autostart")),
        "port_status_at_start": port_status,
        "maintenance_mode_at_start": maintenance,
    }
    state_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    result = {
        "action": "start",
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "started": True,
        "pid": int(process.pid),
        "interval_seconds": int(plan["interval_seconds"]),
        "claim_next": bool(plan.get("claim_next")),
        "stale_after_seconds": plan.get("stale_after_seconds"),
        "state_file": str(state_path),
        "log_file": str(log_path),
        "command": plan.get("command"),
        "autostart": bool(plan.get("autostart")),
        "restart_policy": plan.get("restart_policy"),
        "maintenance_mode": maintenance,
        "port_status": port_status,
        "lock": read_runtime_lock(runtime_id),
    }
    _append_supervisor_event({**decision_context, "decision": "execute", "result": "started", "pid": int(process.pid)})
    return result


def cleanup_stale_supervised_coordination_watch(
    runtime_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove only stale supervisor-owned state for a runtime.

    This action never starts/stops/kills runtime processes and intentionally leaves
    maintenance markers, logs, bootstrap artifacts, and runtime config/auth files untouched.
    """
    status = get_supervised_coordination_watch_status(runtime_id)
    state_path = Path(str(status.get("state_file")))
    lock_status = read_runtime_lock(runtime_id)
    maintenance = get_maintenance_mode_status(runtime_id)
    port_status = status.get("port_status") or get_port_ownership_status(runtime_id)

    stale_state = bool(status.get("state_present") and status.get("stale_state") and not status.get("running"))
    stale_lock = bool(lock_status.get("present") and not lock_status.get("fresh"))
    state_candidate = str(state_path) if stale_state else None
    lock_candidate = str(lock_status.get("path")) if stale_lock else None
    would_clean = [candidate for candidate in [state_candidate, lock_candidate] if candidate]

    result: dict[str, Any] = {
        "action": "cleanup-stale",
        "runtime_id": runtime_id,
        "runtime_name": status.get("runtime_name"),
        "dry_run": dry_run,
        "running": bool(status.get("running")),
        "state_present": bool(status.get("state_present")),
        "stale_state": stale_state,
        "state_file": str(state_path),
        "log_file": status.get("log_file"),
        "lock": lock_status,
        "stale_lock": stale_lock,
        "maintenance_mode": maintenance,
        "port_status": port_status,
        "would_clean": would_clean,
        "cleaned": [],
        "left_untouched": [
            "maintenance_mode",
            "coordination_watch_logs",
            "bootstrap_registration_handoff_reboot_verify_artifacts",
            "openclaw_config_auth_backups_logs",
            "hermes_config_auth_backups_logs",
            "runtime_gateway_processes",
        ],
    }

    decision = "dry_run" if dry_run else "execute"
    event_result = "would_clean" if dry_run and would_clean else "cleaned"
    if not would_clean:
        decision = "skip" if not dry_run else "dry_run"
        event_result = "no_stale_state"
        result["skipped"] = True
        result["skipped_reason"] = "no_stale_supervisor_state"
    elif dry_run:
        result["would_clean_stale_state"] = stale_state
        result["would_clean_stale_lock"] = stale_lock
    else:
        cleaned: list[str] = []
        if stale_state and _safe_unlink(state_path):
            cleaned.append(str(state_path))
        if stale_lock and _safe_unlink(Path(str(lock_status.get("path")))):
            cleaned.append(str(lock_status.get("path")))
        result["cleaned"] = cleaned
        event_result = "cleaned" if cleaned else "cleanup_failed_or_nothing_removed"

    _append_supervisor_event(
        {
            "runtime_id": runtime_id,
            "runtime_name": status.get("runtime_name"),
            "action_requested": "cleanup-stale",
            "dry_run": dry_run,
            "maintenance_mode": maintenance,
            "port_status": port_status,
            "lock": lock_status,
            "state_file": str(state_path),
            "stale_state": stale_state,
            "stale_lock": stale_lock,
            "would_clean": would_clean,
            "cleaned": result.get("cleaned", []),
            "decision": decision,
            "result": event_result,
        }
    )
    return result


def stop_supervised_coordination_watch(
    runtime_id: str,
    *,
    timeout_seconds: float = 2.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    status = get_supervised_coordination_watch_status(runtime_id)
    state_path = Path(str(status.get("state_file")))
    maintenance = get_maintenance_mode_status(runtime_id)

    if maintenance.get("active"):
        result = dict(status)
        result.update(
            {
                "action": "stop",
                "stopped": False,
                "skipped": True,
                "skipped_reason": "maintenance_mode_active",
                "dry_run": dry_run,
                "maintenance_mode": maintenance,
            }
        )
        _append_supervisor_event(
            {
                "runtime_id": runtime_id,
                "runtime_name": status.get("runtime_name"),
                "action_requested": "stop",
                "dry_run": dry_run,
                "maintenance_mode": maintenance,
                "port_status": status.get("port_status"),
                "lock": status.get("lock"),
                "decision": "skip",
                "result": "maintenance_mode_active",
            }
        )
        return result

    if dry_run:
        result = dict(status)
        result.update(
            {
                "action": "stop",
                "stopped": False,
                "dry_run": True,
                "would_stop": bool(status.get("running")),
                "would_clear_stale_state": bool(status.get("state_present") and not status.get("running")),
            }
        )
        _append_supervisor_event(
            {
                "runtime_id": runtime_id,
                "runtime_name": status.get("runtime_name"),
                "action_requested": "stop",
                "dry_run": True,
                "maintenance_mode": maintenance,
                "port_status": status.get("port_status"),
                "lock": status.get("lock"),
                "decision": "dry_run",
                "result": "would_stop" if status.get("running") else "would_not_stop",
            }
        )
        return result

    if not status.get("state_present"):
        status["action"] = "stop"
        status["stopped"] = False
        status["reason"] = "not_running"
        clear_runtime_lock(runtime_id)
        _append_supervisor_event({"runtime_id": runtime_id, "runtime_name": status.get("runtime_name"), "action_requested": "stop", "decision": "skip", "result": "not_running"})
        return status

    pid = int(status.get("pid") or 0)
    if pid <= 0 or not status.get("running"):
        if state_path.exists():
            state_path.unlink()
        clear_runtime_lock(runtime_id)
        status["action"] = "stop"
        status["stopped"] = False
        status["cleared_stale_state"] = True
        status["reason"] = "stale_state"
        _append_supervisor_event({"runtime_id": runtime_id, "runtime_name": status.get("runtime_name"), "action_requested": "stop", "decision": "execute", "result": "cleared_stale_state"})
        return status

    platform = str(status.get("platform") or "")
    wsl_distro = str(status.get("wsl_distro") or "")
    _terminate_pid(pid, force=False, platform=platform, wsl_distro=wsl_distro)
    deadline = time.time() + max(timeout_seconds, 0.1)
    while time.time() < deadline:
        if not _pid_running(pid, platform=platform, wsl_distro=wsl_distro):
            break
        time.sleep(0.05)

    forced = False
    if _pid_running(pid, platform=platform, wsl_distro=wsl_distro):
        _terminate_pid(pid, force=True, platform=platform, wsl_distro=wsl_distro)
        forced = True

    if state_path.exists():
        state_path.unlink()
    clear_runtime_lock(runtime_id)

    result = {
        "action": "stop",
        "runtime_id": runtime_id,
        "runtime_name": status.get("runtime_name"),
        "stopped": True,
        "pid": pid,
        "forced": forced,
        "state_file": str(state_path),
        "log_file": status.get("log_file"),
        "maintenance_mode": maintenance,
        "port_status": status.get("port_status"),
    }
    _append_supervisor_event({"runtime_id": runtime_id, "runtime_name": status.get("runtime_name"), "action_requested": "stop", "decision": "execute", "result": "stopped", "pid": pid, "forced": forced})
    return result
