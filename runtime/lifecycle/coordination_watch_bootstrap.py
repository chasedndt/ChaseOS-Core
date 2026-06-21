"""ChaseOS coordination-watch bootstrap registration foothold.

Builds bounded host-registration artifacts for lifecycle-owned coordination-watch
supervisors. This does not claim that host registration has already been applied;
it creates the launcher and machine-readable registration bundle that ChaseOS would
use for host-level autostart ownership.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.lifecycle.coordination_watch import _coerce_bool, load_coordination_watch_config
from runtime.lifecycle.coordination_watch_supervisor import (
    _resolve_output_path,
    _windows_no_window_subprocess_kwargs,
    build_supervised_coordination_watch_plan,
    get_supervised_coordination_watch_status,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BOOTSTRAP_DIR = ROOT / "runtime" / "lifecycle" / "bootstrap"
BINDINGS_DIR = ROOT / "runtime" / "bindings"


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    return int(value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_windows_path(path: Path) -> str:
    raw = str(path)
    if raw.startswith("/mnt/") and len(raw) > 6:
        drive = raw[5].upper()
        rest = raw[6:].replace("/", "\\")
        return f"{drive}:{rest}"
    return raw.replace("/", "\\")


def _load_runtime_bootstrap_example(runtime_id: str) -> dict[str, Any]:
    path = BINDINGS_DIR / f"{runtime_id}.bootstrap.example.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _resolve_repo_root_for_runtime(runtime_id: str, prefer: str) -> str:
    bootstrap = _load_runtime_bootstrap_example(runtime_id)
    resolution = bootstrap.get("repo_root_resolution") or {}
    candidates = resolution.get("candidate_paths") or []
    if prefer == "wsl":
        for candidate in candidates:
            raw = str(candidate)
            if raw.startswith("/mnt/"):
                return raw
        return str(ROOT)
    for candidate in candidates:
        raw = str(candidate)
        if len(raw) > 2 and raw[1:3] in {":/", ":\\"}:
            return raw.replace("/", "\\")
    return _to_windows_path(ROOT)


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _format_powershell_array(values: list[str]) -> str:
    return "@(" + ", ".join(_powershell_quote(str(value)) for value in values) + ")"


def _read_latest_bootstrap_event(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"error": "invalid_event_log", "path": str(path)}


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {"error": "invalid_json_file", "path": str(path)}


def _evidence_identity_issue(plan: dict[str, Any], evidence: dict[str, Any] | None) -> str | None:
    if not isinstance(evidence, dict) or not evidence:
        return "missing"
    if evidence.get("error"):
        return str(evidence.get("error"))

    expected_runtime_id = str(plan.get("runtime_id") or "").strip().lower()
    observed_runtime_id = str(evidence.get("runtime_id") or "").strip().lower()
    if expected_runtime_id and not observed_runtime_id:
        return "runtime_id_missing"
    if expected_runtime_id and observed_runtime_id != expected_runtime_id:
        return "runtime_id_mismatch"

    expected_task_name = str(plan.get("task_name") or "").strip()
    observed_task_name = str(evidence.get("task_name") or "").strip()
    if expected_task_name and not observed_task_name:
        return "task_name_missing"
    if expected_task_name and observed_task_name != expected_task_name:
        return "task_name_mismatch"

    expected_registration_kind = str(plan.get("registration_kind") or "").strip()
    observed_registration_kind = str(evidence.get("registration_kind") or "").strip()
    if expected_registration_kind and not observed_registration_kind:
        return "registration_kind_missing"
    if expected_registration_kind and observed_registration_kind != expected_registration_kind:
        return "registration_kind_mismatch"

    return None


def _returncode_zero(evidence: dict[str, Any]) -> bool:
    try:
        return int(evidence.get("verification_returncode")) == 0
    except (TypeError, ValueError):
        return False


def _scheduler_evidence_confirmed(plan: dict[str, Any], evidence: dict[str, Any] | None) -> tuple[bool, str | None]:
    issue = _evidence_identity_issue(plan, evidence)
    if issue:
        return False, issue
    assert evidence is not None

    if not bool(evidence.get("scheduler_registered")):
        return False, "scheduler_not_registered"
    if not _returncode_zero(evidence):
        return False, "scheduler_query_nonzero"

    expected_task_name = str(plan.get("task_name") or "").strip()
    scheduler_output = str(evidence.get("verification_stdout") or evidence.get("stdout") or "")
    if expected_task_name and expected_task_name not in scheduler_output:
        return False, "task_name_missing_from_scheduler_output"

    return True, None


def _success_evidence_confirmed(plan: dict[str, Any], evidence: dict[str, Any] | None) -> tuple[bool, str | None]:
    scheduler_confirmed, reason = _scheduler_evidence_confirmed(plan, evidence)
    if not scheduler_confirmed:
        return False, reason
    assert evidence is not None

    if not bool(evidence.get("supervisor_state_present")):
        return False, "supervisor_state_missing"
    if not bool(evidence.get("supervisor_log_present")):
        return False, "supervisor_log_missing"
    if not bool(evidence.get("success_observed")):
        return False, "success_not_observed"

    return True, None


def _reboot_evidence_confirmed(plan: dict[str, Any], evidence: dict[str, Any] | None) -> tuple[bool, str | None]:
    scheduler_confirmed, reason = _scheduler_evidence_confirmed(plan, evidence)
    if not scheduler_confirmed:
        return False, reason
    assert evidence is not None

    if not bool(evidence.get("supervisor_state_present")):
        return False, "supervisor_state_missing"
    if not bool(evidence.get("supervisor_log_present")):
        return False, "supervisor_log_missing"
    if not bool(evidence.get("reboot_observed")):
        return False, "reboot_not_observed_after_bundle_preparation"
    if not bool(evidence.get("scheduled_task_ran_after_boot")):
        return False, "scheduled_task_not_observed_after_boot"
    if not bool(evidence.get("scheduled_task_last_result_ok")):
        return False, "scheduled_task_last_result_not_ok"
    if not bool(evidence.get("success_observed")):
        return False, "success_not_observed"

    return True, None


def _append_bootstrap_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def _build_bootstrap_event(
    runtime_id: str,
    runtime_name: str | None,
    action: str,
    **fields: Any,
) -> dict[str, Any]:
    event = {
        "timestamp_utc": _utc_now_iso(),
        "runtime_id": runtime_id,
        "runtime_name": runtime_name,
        "action": action,
    }
    event.update(fields)
    return event


def _build_bootstrap_event_log_path(runtime_id: str) -> Path:
    return ROOT / "runtime" / "lifecycle" / "run" / f"{runtime_id}-coordination-watch-bootstrap-events.jsonl"


def _build_success_activity_record_path(runtime_id: str, timestamp_utc: str) -> Path:
    date_part = timestamp_utc[:10]
    time_compact = timestamp_utc[11:19].replace(":", "")
    return ROOT / "07_LOGS" / "Agent-Activity" / (
        f"{date_part}-{runtime_id}-coordination-watch-bootstrap-success-{time_compact.lower()}z.md"
    )


def _write_success_activity_record(plan: dict[str, Any], record: dict[str, Any]) -> Path:
    runtime_id = str(record.get("runtime_id") or plan.get("runtime_id") or "runtime")
    runtime_name = str(record.get("runtime_name") or plan.get("runtime_name") or runtime_id)
    timestamp_utc = str(record.get("timestamp_utc") or _utc_now_iso())
    activity_path = _build_success_activity_record_path(runtime_id, timestamp_utc)
    activity_path.parent.mkdir(parents=True, exist_ok=True)

    success_record_path = str(plan.get("success_record_path") or "")
    event_log_path = str(plan.get("event_log_path") or "")
    state_file = str(record.get("supervisor_state_file") or "")
    log_file = str(record.get("supervisor_log_file") or "")
    query_command = record.get("verification_command") or []
    query_command_text = " ".join(str(part) for part in query_command)

    content = f"""---
type: agent-session-log
date: {timestamp_utc[:10]}
agent: {runtime_name}
session-type: runtime bootstrap success audit
triggered-by: coordination-watch bootstrap success capture
---

# Agent Session Log — {timestamp_utc[:10]}

## Session Metadata

**Date:** {timestamp_utc}  
**Agent / System:** {runtime_name}  
**Session type:** Runtime bootstrap success audit  
**Triggered by:** Coordination-watch bootstrap success capture  
**Duration (approx):** N/A

---

## Task Summary

**What was requested or triggered:**  
Capture confirmed startup success evidence after host bootstrap registration and supervisor persistence checks.

**Scope:**
- query scheduler registration truth
- confirm expected supervisor state/log artifacts
- bind the observed success state into Agent Activity visibility

**What was NOT in scope:**
- changing bootstrap registration state
- expanding runtime authority
- canonical knowledge writeback

---

## Inputs Used

| Input | Type | Location |
|-------|------|----------|
| bootstrap success record | Machine-readable runtime artifact | `{success_record_path}` |
| bootstrap event log | Runtime event history | `{event_log_path}` |
| scheduler query | Host verification command | `{query_command_text}` |
| supervisor state file | Runtime lifecycle evidence | `{state_file}` |
| supervisor log file | Runtime lifecycle evidence | `{log_file}` |

---

## Actions Taken

1. Queried the configured host scheduler surface for `{plan.get("task_name")}`.
2. Confirmed the expected supervisor state and log files are present.
3. Wrote a durable success-state JSON record.
4. Promoted the confirmed result into the Agent Activity audit surface.

---

## Outputs Produced

| Output | Type | Written to |
|--------|------|------------|
| success-state record | Runtime lifecycle JSON | `{success_record_path}` |
| success activity record | Agent activity markdown | `{activity_path}` |

---

## Open Loops / Flags

- [ ] Re-run capture after a future restart/logon cycle if host startup behavior changes.

---

## Agent Notes

ChaseOS observed a confirmed coordination-watch startup success for `{runtime_name}` at capture time: scheduler registration was present and both expected supervisor evidence files existed. This is runtime/audit visibility only; it does not expand authority or bypass lifecycle governance.

---

*Graph links: [[Agent-Activity-Index]] · [[Build-Logs-Index]] · [[Runtime-InterAgent-Coordination-Bus]]*
"""
    activity_path.write_text(content, encoding="utf-8")
    return activity_path


def _record_bootstrap_event(plan: dict[str, Any], action: str, **fields: Any) -> None:
    event_log_path = Path(str(plan.get("event_log_path") or _build_bootstrap_event_log_path(str(plan.get("runtime_id") or "runtime"))))
    _append_bootstrap_event(
        event_log_path,
        _build_bootstrap_event(
            str(plan.get("runtime_id") or ""),
            plan.get("runtime_name"),
            action,
            task_name=plan.get("task_name"),
            registration_kind=plan.get("registration_kind"),
            **fields,
        ),
    )


def _build_windows_launcher_contents(runtime_id: str, runtime_name: str) -> str:
    return (
        "@echo off\r\n"
        "setlocal EnableExtensions\r\n"
        "set \"BOOTSTRAP_DIR=%~dp0\"\r\n"
        "for %%I in (\"%BOOTSTRAP_DIR%..\\..\\..\") do set \"VAULT=%%~fI\"\r\n"
        "set \"PYTHON=%VAULT%\\.venv\\Scripts\\python.exe\"\r\n"
        "if not exist \"%PYTHON%\" if exist \"%VAULT%\\.venv-win314\\Scripts\\python.exe\" set \"PYTHON=%VAULT%\\.venv-win314\\Scripts\\python.exe\"\r\n"
        "if not exist \"%PYTHON%\" if exist \"%VAULT%\\.venv-win\\Scripts\\python.exe\" set \"PYTHON=%VAULT%\\.venv-win\\Scripts\\python.exe\"\r\n"
        "cd /d \"%VAULT%\"\r\n"
        "set \"CHASEOS_COORDINATION_LOG=%VAULT%\\runtime\\lifecycle\\run\\coordination-watch-bootstrap.log\"\r\n"
        f"powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command \"Add-Content -Path $env:CHASEOS_COORDINATION_LOG -Value ('[' + (Get-Date).ToString('s') + '] coordination-watch bootstrap hidden start'); $argsList=@('\\\"%VAULT%\\chaseos.py\\\"','runtime','coordination-watch-supervisor','--runtime','{runtime_id}','--action','start'); $psi=[System.Diagnostics.ProcessStartInfo]::new(); $psi.FileName=$env:PYTHON; $psi.Arguments=($argsList -join ' '); $psi.WorkingDirectory=$env:VAULT; $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true; $psi.WindowStyle=[System.Diagnostics.ProcessWindowStyle]::Hidden; [System.Diagnostics.Process]::Start($psi) | Out-Null; exit 0\"\r\n"
    )


def _build_wsl_launcher_contents(runtime_id: str, runtime_name: str, distro: str) -> str:
    repo_root = _resolve_repo_root_for_runtime(runtime_id, prefer="wsl")
    safe_repo_root = str(repo_root).replace('"', '\\"')
    return (
        "@echo off\r\n"
        "setlocal EnableExtensions\r\n"
        f"set \"CHASEOS_WSL_ARGS=-d {distro} -- bash -lc \\\"cd \\\\\\\"{safe_repo_root}\\\\\\\" && python3 chaseos.py runtime coordination-watch-supervisor --runtime {runtime_id} --action start\\\"\"\r\n"
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command \"$psi=[System.Diagnostics.ProcessStartInfo]::new(); $psi.FileName='wsl.exe'; $psi.Arguments=$env:CHASEOS_WSL_ARGS; $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true; $psi.WindowStyle=[System.Diagnostics.ProcessWindowStyle]::Hidden; [System.Diagnostics.Process]::Start($psi) | Out-Null\" >nul 2>&1\r\n"
    )


def _build_vbs_hidden_delegate_contents(target_windows_path: str) -> str:
    safe_target = str(target_windows_path).replace('"', '""')
    return "\r\n".join(
        [
            "Option Explicit",
            "' ChaseOS managed hidden scheduled-task delegate.",
            "' Terminal-spam guard: Task Scheduler starts wscript.exe, not cmd.exe/conhost.exe.",
            "Dim shell, target",
            'Set shell = CreateObject("WScript.Shell")',
            f'target = "{safe_target}"',
            'shell.Run "cmd.exe /d /c """ & target & """", 0, False',
            "",
        ]
    )


def load_coordination_watch_bootstrap_config(runtime_id: str) -> dict[str, Any]:
    watch = load_coordination_watch_config(runtime_id)
    bootstrap = dict(watch.get("bootstrap") or {})
    if not bootstrap:
        raise ValueError(f"No coordination_watch.bootstrap record found for runtime: {runtime_id}")

    runtime_name = str(watch.get("runtime_name") or runtime_id)
    launcher_suffix = "vbs" if runtime_id == "hermes" else "cmd"
    launcher_path = _resolve_output_path(bootstrap.get("launcher_path"), runtime_id, launcher_suffix)
    registration_artifact = _resolve_output_path(bootstrap.get("registration_artifact"), runtime_id, "json")
    handoff_script_path = _resolve_output_path(bootstrap.get("handoff_script_path"), runtime_id, "ps1")
    handoff_artifact_path = _resolve_output_path(bootstrap.get("handoff_artifact_path"), runtime_id, "json")
    reboot_verify_script_path = _resolve_output_path(bootstrap.get("reboot_verify_script_path"), runtime_id, "ps1")
    reboot_verify_artifact_path = _resolve_output_path(bootstrap.get("reboot_verify_artifact_path"), runtime_id, "json")
    reboot_verify_result_path = _resolve_output_path(bootstrap.get("reboot_verify_result_path"), runtime_id, "json")
    success_record_path = _resolve_output_path(bootstrap.get("success_record_path"), runtime_id, "json")
    event_log_path = _resolve_output_path(bootstrap.get("event_log_path"), runtime_id, "jsonl")

    return {
        "runtime_id": runtime_id,
        "runtime_name": runtime_name,
        "bootstrap_enabled": _coerce_bool(bootstrap.get("enabled"), default=False),
        "registration_kind": str(bootstrap.get("registration_kind") or "windows-task-scheduler"),
        "trigger": str(bootstrap.get("trigger") or "on-logon"),
        "task_name": str(bootstrap.get("task_name") or f"ChaseOS-{runtime_name}-Coordination-Watch"),
        "launcher_path": str(launcher_path),
        "registration_artifact": str(registration_artifact),
        "handoff_script_path": str(handoff_script_path),
        "handoff_artifact_path": str(handoff_artifact_path),
        "reboot_verify_script_path": str(reboot_verify_script_path),
        "reboot_verify_artifact_path": str(reboot_verify_artifact_path),
        "reboot_verify_result_path": str(reboot_verify_result_path),
        "success_record_path": str(success_record_path),
        "event_log_path": str(event_log_path),
        "wsl_distro": bootstrap.get("wsl_distro"),
        "supervisor_host": str(bootstrap.get("supervisor_host") or "").strip().lower(),
        "notes": bootstrap.get("notes") or watch.get("notes"),
        "interval_seconds": _coerce_int(watch.get("interval_seconds"), default=30),
    }


def build_coordination_watch_bootstrap_plan(runtime_id: str) -> dict[str, Any]:
    config = load_coordination_watch_bootstrap_config(runtime_id)
    registration_kind = config.get("registration_kind")
    task_name = str(config.get("task_name"))
    launcher_path = Path(str(config.get("launcher_path")))

    if registration_kind != "windows-task-scheduler":
        raise ValueError(f"Unsupported registration_kind for runtime {runtime_id}: {registration_kind}")

    if str(config.get("wsl_distro") or "").strip() and config.get("supervisor_host") != "windows":
        worker_launcher_contents = _build_wsl_launcher_contents(
            runtime_id,
            str(config.get("runtime_name")),
            str(config.get("wsl_distro")),
        )
    else:
        worker_launcher_contents = _build_windows_launcher_contents(runtime_id, str(config.get("runtime_name")))

    launcher_windows = _to_windows_path(launcher_path)
    worker_launcher_path: Path | None = None
    worker_launcher_windows: str | None = None
    task_run_command = launcher_windows
    if launcher_path.suffix.lower() == ".vbs":
        worker_launcher_path = launcher_path.with_suffix(".cmd")
        worker_launcher_windows = _to_windows_path(worker_launcher_path)
        launcher_contents = _build_vbs_hidden_delegate_contents(worker_launcher_windows)
        task_run_command = f'wscript.exe "{launcher_windows}"'
    else:
        launcher_contents = worker_launcher_contents

    register_command = [
        "schtasks",
        "/Create",
        "/SC",
        "ONLOGON",
        "/TN",
        task_name,
        "/TR",
        task_run_command,
        "/F",
    ]
    unregister_command = ["schtasks", "/Delete", "/TN", task_name, "/F"]

    return {
        "runtime_id": runtime_id,
        "runtime_name": config.get("runtime_name"),
        "bootstrap_enabled": bool(config.get("bootstrap_enabled")),
        "registration_kind": registration_kind,
        "trigger": config.get("trigger"),
        "task_name": task_name,
        "launcher_path": str(launcher_path),
        "worker_launcher_path": str(worker_launcher_path) if worker_launcher_path else None,
        "registration_artifact": str(config.get("registration_artifact")),
        "handoff_script_path": str(config.get("handoff_script_path")),
        "handoff_artifact_path": str(config.get("handoff_artifact_path")),
        "reboot_verify_script_path": str(config.get("reboot_verify_script_path")),
        "reboot_verify_artifact_path": str(config.get("reboot_verify_artifact_path")),
        "reboot_verify_result_path": str(config.get("reboot_verify_result_path")),
        "success_record_path": str(config.get("success_record_path")),
        "event_log_path": str(config.get("event_log_path")),
        "launcher_contents": launcher_contents,
        "worker_launcher_contents": worker_launcher_contents if worker_launcher_path else None,
        "register_command": register_command,
        "unregister_command": unregister_command,
        "wsl_distro": config.get("wsl_distro"),
        "supervisor_host": config.get("supervisor_host"),
        "notes": config.get("notes"),
        "interval_seconds": config.get("interval_seconds"),
    }


_POSIX_REGISTRATION_KINDS = ("systemd-user", "cron")


def _posix_supervisor_launcher_contents(runtime_id: str, runtime_name: str) -> str:
    """A portable bash launcher that starts the coordination-watch supervisor.

    Resolves the interpreter at run time (project ``.venv/bin/python`` first,
    then ``python3``/``python`` on PATH) so the generated script is not pinned to
    the build host's interpreter. Mirrors the Windows launcher's behavior on a
    POSIX host (native Linux / macOS / WSL distro).
    """
    return (
        "#!/usr/bin/env bash\n"
        "# ChaseOS managed coordination-watch launcher (POSIX).\n"
        f"# runtime: {runtime_id} ({runtime_name})\n"
        "set -euo pipefail\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'VAULT="$(cd "$SCRIPT_DIR/../../.." && pwd)"\n'
        'cd "$VAULT"\n'
        'PYTHON="$VAULT/.venv/bin/python"\n'
        '[ -x "$PYTHON" ] || PYTHON="$(command -v python3 || command -v python)"\n'
        'LOG="$VAULT/runtime/lifecycle/run/coordination-watch-bootstrap.log"\n'
        'mkdir -p "$(dirname "$LOG")"\n'
        f'echo "[$(date -Iseconds)] coordination-watch bootstrap start ({runtime_id})" >> "$LOG"\n'
        f'exec "$PYTHON" chaseos.py runtime coordination-watch-supervisor '
        f'--runtime {runtime_id} --action start\n'
    )


def _systemd_unit_contents(runtime_name: str, launcher_sh: Path, log_path: Path, vault_root: Path) -> str:
    return (
        "[Unit]\n"
        f"Description=ChaseOS {runtime_name} Coordination Watch\n"
        "After=default.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"WorkingDirectory={vault_root}\n"
        f"ExecStart=/usr/bin/env bash {launcher_sh}\n"
        f"StandardOutput=append:{log_path}\n"
        f"StandardError=append:{log_path}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def build_posix_coordination_watch_bootstrap_plan(
    runtime_id: str, *, registration_kind: str
) -> dict[str, Any]:
    """Build a Linux/macOS autostart plan for the coordination-watch supervisor.

    Additive sibling of ``build_coordination_watch_bootstrap_plan`` (which is
    Windows-Task-Scheduler-only). This produces a ``systemd-user`` or ``cron``
    plan: a portable ``.sh`` launcher plus the unit/crontab artifact and the
    register/unregister commands. It is plan-only — actually writing the unit
    file / crontab and running ``systemctl``/``crontab`` is the Linux-host apply
    step, exactly as the Windows plan is separate from its apply.
    """
    kind = str(registration_kind or "").strip().lower()
    if kind not in _POSIX_REGISTRATION_KINDS:
        raise ValueError(
            f"Unsupported POSIX registration_kind: {registration_kind!r} "
            f"(expected one of {_POSIX_REGISTRATION_KINDS})"
        )

    config = load_coordination_watch_bootstrap_config(runtime_id)
    runtime_name = str(config.get("runtime_name") or runtime_id)
    task_name = str(config.get("task_name"))
    launcher_sh = _resolve_output_path(None, runtime_id, "sh")
    log_path = ROOT / "runtime" / "lifecycle" / "run" / "coordination-watch-bootstrap.log"
    launcher_contents = _posix_supervisor_launcher_contents(runtime_id, runtime_name)

    plan: dict[str, Any] = {
        "runtime_id": runtime_id,
        "runtime_name": runtime_name,
        "bootstrap_enabled": bool(config.get("bootstrap_enabled")),
        "registration_kind": kind,
        "trigger": "on-login" if kind == "systemd-user" else "on-reboot",
        "task_name": task_name,
        "launcher_path": str(launcher_sh),
        "launcher_contents": launcher_contents,
        "success_record_path": str(config.get("success_record_path")),
        "event_log_path": str(config.get("event_log_path")),
        "interval_seconds": config.get("interval_seconds"),
        "notes": config.get("notes"),
    }

    if kind == "systemd-user":
        unit_name = f"chaseos-{runtime_id}-coordination-watch.service"
        # POSIX target path — built as a literal string so the build host's
        # path separators (e.g. Windows '\') never leak into a Linux unit path.
        unit_path = f"~/.config/systemd/user/{unit_name}"
        plan.update(
            {
                "unit_name": unit_name,
                "unit_file_path": unit_path,
                "unit_file_contents": _systemd_unit_contents(
                    runtime_name, launcher_sh, log_path, ROOT
                ),
                "register_commands": [
                    ["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", "--now", unit_name],
                ],
                "register_command": ["systemctl", "--user", "enable", "--now", unit_name],
                "unregister_command": ["systemctl", "--user", "disable", "--now", unit_name],
                "verify_command": ["systemctl", "--user", "is-enabled", unit_name],
            }
        )
    else:  # cron
        cron_line = f"@reboot /usr/bin/env bash {launcher_sh}  # chaseos:{runtime_id}-coordination-watch"
        marker = f"chaseos:{runtime_id}-coordination-watch"
        plan.update(
            {
                "cron_line": cron_line,
                "cron_marker": marker,
                # Append our line idempotently (strip any prior chaseos line for this runtime first).
                "register_command": [
                    "bash",
                    "-lc",
                    f"( crontab -l 2>/dev/null | grep -v '{marker}'; echo {json.dumps(cron_line)} ) | crontab -",
                ],
                "unregister_command": [
                    "bash",
                    "-lc",
                    f"crontab -l 2>/dev/null | grep -v '{marker}' | crontab -",
                ],
                "verify_command": ["bash", "-lc", f"crontab -l 2>/dev/null | grep -F '{marker}'"],
            }
        )

    return plan


def build_coordination_watch_bootstrap_plan_for_kind(
    runtime_id: str, *, registration_kind: str | None = None
) -> dict[str, Any]:
    """Route to the Windows or POSIX bootstrap plan builder by registration kind.

    When ``registration_kind`` is None, the host's native default is used
    (``windows-task-scheduler`` on Windows, ``systemd-user``/``cron`` on Linux,
    ``launchd`` on macOS — launchd not yet implemented, falls back to cron-style).
    """
    from runtime import platform_support

    kind = str(registration_kind or platform_support.default_autostart_kind()).strip().lower()
    if kind == "windows-task-scheduler":
        return build_coordination_watch_bootstrap_plan(runtime_id)
    if kind == "launchd":
        # launchd plist generation is not yet implemented; cron is the portable
        # POSIX fallback that works on macOS too.
        kind = "cron"
    return build_posix_coordination_watch_bootstrap_plan(runtime_id, registration_kind=kind)


def _run_registration_command(command: list[str]) -> dict[str, Any]:
    try:
        _kwargs: dict[str, Any] = {"capture_output": True, "text": True, "check": False}
        _kwargs.update(_windows_no_window_subprocess_kwargs())
        completed = subprocess.run(command, **_kwargs)
    except FileNotFoundError as exc:
        executable = str(command[0]) if command else str(getattr(exc, "filename", "") or "unknown")
        return {
            "command": command,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "available": False,
            "unavailable_reason": f"executable-not-found: {executable}",
            "error": str(exc),
        }
    return {
        "command": command,
        "returncode": int(completed.returncode),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "available": True,
        "unavailable_reason": None,
        "error": None,
    }


def _build_handoff_script_contents(plan: dict[str, Any]) -> str:
    task_name = str(plan.get("task_name") or "")
    register_command = [str(part) for part in list(plan.get("register_command") or [])]
    unregister_command = [str(part) for part in list(plan.get("unregister_command") or [])]
    verify_command = ["schtasks", "/Query", "/TN", task_name]

    return "\r\n".join(
        [
            '$ErrorActionPreference = "Stop"',
            f"$taskName = {_powershell_quote(task_name)}",
            f"$registerArgs = {_format_powershell_array(register_command[1:])}",
            f"$verifyArgs = {_format_powershell_array(verify_command[1:])}",
            f"$removeArgs = {_format_powershell_array(unregister_command[1:])}",
            'Write-Host "Requesting elevation for ChaseOS coordination-watch bootstrap registration..."',
            '$process = Start-Process -FilePath "schtasks.exe" -ArgumentList $registerArgs -Verb RunAs -Wait -PassThru',
            'Write-Host "Scheduler registration exit code:" $process.ExitCode',
            'Write-Host "Verifying Task Scheduler registration..."',
            'schtasks.exe $verifyArgs',
            'Write-Host "To unregister later from an elevated PowerShell session, run:"',
            'Write-Host (("schtasks.exe " + ($removeArgs -join " ")))',
            'exit $process.ExitCode',
            '',
        ]
    )


def _build_reboot_verification_script_contents(
    plan: dict[str, Any],
    verification_command: list[str],
    supervisor_state_file: str,
    supervisor_log_file: str,
    result_output_path: str,
    prepared_at_utc: str,
) -> str:
    task_name = str(plan.get("task_name") or "")
    return "\r\n".join(
        [
            '$ErrorActionPreference = "Stop"',
            '$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path',
            '$vaultRoot = Resolve-Path (Join-Path $scriptRoot "..\\..\\..")',
            f"$taskName = {_powershell_quote(task_name)}",
            f"$preparedAtUtc = ([datetime]::Parse({_powershell_quote(prepared_at_utc)})).ToUniversalTime()",
            f"$verifyArgs = {_format_powershell_array(verification_command[1:])}",
            f"$supervisorState = Join-Path $vaultRoot {_powershell_quote('runtime/lifecycle/run/' + str(plan.get('runtime_id') or '') + '-coordination-watch.json')}",
            f"$supervisorLog = Join-Path $vaultRoot {_powershell_quote('runtime/lifecycle/run/' + str(plan.get('runtime_id') or '') + '-coordination-watch.log')}",
            f"$resultOutputPath = Join-Path $vaultRoot {_powershell_quote('runtime/lifecycle/run/' + str(plan.get('runtime_id') or '') + '-coordination-watch-reboot-verify-result.json')}",
            '$schedulerRegistered = $false',
            '$verificationStdout = ""',
            '$verificationStderr = ""',
            '$verificationReturnCode = 0',
            '$schedulerRegistrationEvidence = "not-checked"',
            '$bootTimeUtc = $null',
            '$bootTimeError = ""',
            '$rebootObserved = $false',
            '$taskLastRunTimeUtc = $null',
            '$taskLastResult = $null',
            '$taskInfoError = ""',
            '$scheduledTaskRanAfterBoot = $false',
            '$scheduledTaskLastResultOk = $false',
            'Write-Host "Checking Task Scheduler registration after reboot/logon..."',
            'try {',
            '  $verificationOutput = & schtasks.exe $verifyArgs 2>&1',
            '  if ($null -ne $LASTEXITCODE) { $verificationReturnCode = [int]$LASTEXITCODE } else { $verificationReturnCode = 0 }',
            '  $verificationStdout = ($verificationOutput | Out-String)',
            '  $schedulerRegistered = ($verificationReturnCode -eq 0 -and $verificationStdout -like ("*" + $taskName + "*"))',
            '} catch {',
            '  $verificationStderr = ($_ | Out-String)',
            '  if ($null -ne $_.Exception -and $null -ne $_.Exception.HResult) { $verificationReturnCode = [int]$_.Exception.HResult } else { $verificationReturnCode = 1 }',
            '}',
            'if ($schedulerRegistered) {',
            '  $schedulerRegistrationEvidence = "returncode-zero-task-name-present"',
            '} elseif ($verificationReturnCode -ne 0) {',
            '  $schedulerRegistrationEvidence = "scheduler-query-nonzero"',
            '} else {',
            '  $schedulerRegistrationEvidence = "task-name-missing-from-output"',
            '}',
            'try {',
            '  $os = Get-CimInstance Win32_OperatingSystem',
            '  $rawBootTime = $os.LastBootUpTime',
            '  if ($rawBootTime -is [datetime]) {',
            '    $bootTimeUtc = $rawBootTime.ToUniversalTime()',
            '  } else {',
            '    $bootTimeUtc = ([Management.ManagementDateTimeConverter]::ToDateTime([string]$rawBootTime)).ToUniversalTime()',
            '  }',
            '  $rebootObserved = ($bootTimeUtc -gt $preparedAtUtc)',
            '} catch {',
            '  $bootTimeError = ($_ | Out-String).Trim()',
            '}',
            'try {',
            '  $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName',
            '  if ($null -ne $taskInfo) {',
            '    if ($taskInfo.LastRunTime -is [datetime] -and $taskInfo.LastRunTime.Year -gt 2000) {',
            '      $taskLastRunTimeUtc = $taskInfo.LastRunTime.ToUniversalTime()',
            '    }',
            '    if ($null -ne $taskInfo.LastTaskResult) { $taskLastResult = [int]$taskInfo.LastTaskResult }',
            '  }',
            '  if ($null -ne $bootTimeUtc -and $null -ne $taskLastRunTimeUtc) {',
            '    $scheduledTaskRanAfterBoot = ($taskLastRunTimeUtc -ge $bootTimeUtc)',
            '  }',
            '  if ($null -ne $taskLastResult) {',
            '    $scheduledTaskLastResultOk = ($taskLastResult -eq 0 -or $taskLastResult -eq 267009)',
            '  }',
            '} catch {',
            '  $taskInfoError = ($_ | Out-String).Trim()',
            '}',
            '$supervisorStatePresent = Test-Path $supervisorState',
            '$supervisorLogPresent = Test-Path $supervisorLog',
            'Write-Host "Prepared verification at UTC:" $preparedAtUtc.ToString("o")',
            'if ($null -ne $bootTimeUtc) { Write-Host "Current boot time UTC:" $bootTimeUtc.ToString("o") } else { Write-Host "Current boot time unavailable:" $bootTimeError }',
            'if ($null -ne $taskLastRunTimeUtc) { Write-Host "Scheduled task last run UTC:" $taskLastRunTimeUtc.ToString("o") } else { Write-Host "Scheduled task last run unavailable:" $taskInfoError }',
            'Write-Host "Expected supervisor state file:" $supervisorState',
            'if ($supervisorStatePresent) { Write-Host "Supervisor state file present." } else { Write-Host "Supervisor state file missing." }',
            'Write-Host "Expected supervisor log file:" $supervisorLog',
            'if ($supervisorLogPresent) { Write-Host "Supervisor log file present." } else { Write-Host "Supervisor log file missing." }',
            '$result = @{',
            '  timestamp_utc = ((Get-Date).ToUniversalTime().ToString("o") -replace "\\+00:00$", "Z")',
            '  prepared_at_utc = ($preparedAtUtc.ToString("o") -replace "\\+00:00$", "Z")',
            f"  runtime_id = {_powershell_quote(str(plan.get('runtime_id') or ''))}",
            f"  runtime_name = {_powershell_quote(str(plan.get('runtime_name') or ''))}",
            f"  task_name = {_powershell_quote(str(plan.get('task_name') or ''))}",
            f"  registration_kind = {_powershell_quote(str(plan.get('registration_kind') or ''))}",
            '  scheduler_registered = $schedulerRegistered',
            '  verification_returncode = $verificationReturnCode',
            '  verification_stdout = $verificationStdout.Trim()',
            '  verification_stderr = $verificationStderr.Trim()',
            '  verification_command = @("schtasks") + $verifyArgs',
            '  scheduler_registration_evidence = $schedulerRegistrationEvidence',
            '  current_boot_time_utc = if ($null -ne $bootTimeUtc) { ($bootTimeUtc.ToString("o") -replace "\\+00:00$", "Z") } else { $null }',
            '  boot_time_error = $bootTimeError',
            '  reboot_observed = $rebootObserved',
            '  task_last_run_time_utc = if ($null -ne $taskLastRunTimeUtc) { ($taskLastRunTimeUtc.ToString("o") -replace "\\+00:00$", "Z") } else { $null }',
            '  task_last_result = $taskLastResult',
            '  task_info_error = $taskInfoError',
            '  scheduled_task_ran_after_boot = $scheduledTaskRanAfterBoot',
            '  scheduled_task_last_result_ok = $scheduledTaskLastResultOk',
            '  supervisor_state_file = $supervisorState',
            '  supervisor_state_present = $supervisorStatePresent',
            '  supervisor_log_file = $supervisorLog',
            '  supervisor_log_present = $supervisorLogPresent',
            '  success_observed = ($schedulerRegistered -and $supervisorStatePresent -and $supervisorLogPresent -and $rebootObserved -and $scheduledTaskRanAfterBoot -and $scheduledTaskLastResultOk)',
            '}',
            '$result | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $resultOutputPath',
            'Write-Host "Reboot verification result written to:" $resultOutputPath',
            'Write-Host "Proof requires: scheduler registered, reboot after bundle preparation, scheduled task ran after boot, and supervisor artifacts present."',
            '',
        ]
    )


def install_coordination_watch_bootstrap(runtime_id: str) -> dict[str, Any]:
    plan = build_coordination_watch_bootstrap_plan(runtime_id)
    if not plan.get("bootstrap_enabled"):
        raise ValueError(f"coordination_watch bootstrap disabled for runtime: {runtime_id}")

    launcher_path = Path(str(plan.get("launcher_path")))
    worker_launcher_path = Path(str(plan.get("worker_launcher_path"))) if plan.get("worker_launcher_path") else None
    artifact_path = Path(str(plan.get("registration_artifact")))
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    if worker_launcher_path is not None:
        worker_launcher_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    launcher_path.write_text(str(plan.get("launcher_contents") or ""), encoding="utf-8")
    if worker_launcher_path is not None:
        worker_launcher_path.write_text(str(plan.get("worker_launcher_contents") or ""), encoding="utf-8")
    artifact_payload = {
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "registration_kind": plan.get("registration_kind"),
        "trigger": plan.get("trigger"),
        "task_name": plan.get("task_name"),
        "launcher_path": str(launcher_path),
        "worker_launcher_path": str(worker_launcher_path) if worker_launcher_path else None,
        "register_command": plan.get("register_command"),
        "unregister_command": plan.get("unregister_command"),
        "event_log_path": plan.get("event_log_path"),
        "wsl_distro": plan.get("wsl_distro"),
        "notes": plan.get("notes"),
    }
    artifact_path.write_text(json.dumps(artifact_payload, indent=2), encoding="utf-8")
    _record_bootstrap_event(plan, "install", installed=True, launcher_path=str(launcher_path), registration_artifact=str(artifact_path))

    return {
        "action": "install",
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "installed": True,
        "launcher_path": str(launcher_path),
        "worker_launcher_path": str(worker_launcher_path) if worker_launcher_path else None,
        "registration_artifact": str(artifact_path),
        "task_name": plan.get("task_name"),
        "register_command": plan.get("register_command"),
    }


def apply_coordination_watch_bootstrap(runtime_id: str) -> dict[str, Any]:
    install_result = install_coordination_watch_bootstrap(runtime_id)
    command_result = _run_registration_command(list(install_result.get("register_command") or []))
    elevation_required = "access is denied" in str(command_result.get("stderr") or "").lower()
    plan = build_coordination_watch_bootstrap_plan(runtime_id)
    _record_bootstrap_event(
        plan,
        "apply",
        applied=command_result.get("returncode") == 0,
        returncode=command_result.get("returncode"),
        stderr=command_result.get("stderr"),
        stdout=command_result.get("stdout"),
        elevation_required=elevation_required,
    )
    return {
        "action": "apply",
        "runtime_id": runtime_id,
        "runtime_name": install_result.get("runtime_name"),
        "applied": command_result.get("returncode") == 0,
        "task_name": install_result.get("task_name"),
        "launcher_path": install_result.get("launcher_path"),
        "registration_artifact": install_result.get("registration_artifact"),
        "register_command": command_result.get("command"),
        "stdout": command_result.get("stdout"),
        "stderr": command_result.get("stderr"),
        "returncode": command_result.get("returncode"),
        "elevation_required": elevation_required,
        "suggested_next_action": "handoff" if elevation_required else None,
    }


def get_coordination_watch_bootstrap_status(runtime_id: str) -> dict[str, Any]:
    plan = build_coordination_watch_bootstrap_plan(runtime_id)
    launcher_path = Path(str(plan.get("launcher_path")))
    artifact_path = Path(str(plan.get("registration_artifact")))
    handoff_script_path = Path(str(plan.get("handoff_script_path")))
    handoff_artifact_path = Path(str(plan.get("handoff_artifact_path")))
    reboot_verify_script_path = Path(str(plan.get("reboot_verify_script_path")))
    reboot_verify_artifact_path = Path(str(plan.get("reboot_verify_artifact_path")))
    reboot_verify_result_path = Path(str(plan.get("reboot_verify_result_path")))
    success_record_path = Path(str(plan.get("success_record_path")))
    event_log_path = Path(str(plan.get("event_log_path")))
    latest_event = _read_latest_bootstrap_event(event_log_path)
    latest_success_record = _read_json_file(success_record_path)
    latest_reboot_verify_result = _read_json_file(reboot_verify_result_path)

    return {
        "action": "status",
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "bootstrap_enabled": bool(plan.get("bootstrap_enabled")),
        "registration_kind": plan.get("registration_kind"),
        "trigger": plan.get("trigger"),
        "task_name": plan.get("task_name"),
        "launcher_path": str(launcher_path),
        "registration_artifact": str(artifact_path),
        "handoff_script_path": str(handoff_script_path),
        "handoff_artifact_path": str(handoff_artifact_path),
        "reboot_verify_script_path": str(reboot_verify_script_path),
        "reboot_verify_artifact_path": str(reboot_verify_artifact_path),
        "reboot_verify_result_path": str(reboot_verify_result_path),
        "success_record_path": str(success_record_path),
        "event_log_path": str(event_log_path),
        "launcher_present": launcher_path.exists(),
        "artifact_present": artifact_path.exists(),
        "handoff_script_present": handoff_script_path.exists(),
        "handoff_artifact_present": handoff_artifact_path.exists(),
        "reboot_verify_script_present": reboot_verify_script_path.exists(),
        "reboot_verify_artifact_present": reboot_verify_artifact_path.exists(),
        "reboot_verify_result_present": reboot_verify_result_path.exists(),
        "success_record_present": success_record_path.exists(),
        "event_log_present": event_log_path.exists(),
        "installed": launcher_path.exists() and artifact_path.exists(),
        "handoff_ready": handoff_script_path.exists() and handoff_artifact_path.exists(),
        "reboot_verification_ready": reboot_verify_script_path.exists() and reboot_verify_artifact_path.exists(),
        "latest_event": latest_event,
        "latest_success_record": latest_success_record,
        "latest_reboot_verify_result": latest_reboot_verify_result,
        "register_command": plan.get("register_command"),
        "unregister_command": plan.get("unregister_command"),
        "wsl_distro": plan.get("wsl_distro"),
    }


def verify_coordination_watch_bootstrap(runtime_id: str) -> dict[str, Any]:
    plan = build_coordination_watch_bootstrap_plan(runtime_id)
    command = ["schtasks", "/Query", "/TN", str(plan.get("task_name"))]
    command_result = _run_registration_command(command)
    registered = command_result.get("returncode") == 0 and str(plan.get("task_name")) in str(command_result.get("stdout") or "")
    _record_bootstrap_event(
        plan,
        "verify",
        registered=registered,
        returncode=command_result.get("returncode"),
        stderr=command_result.get("stderr"),
        stdout=command_result.get("stdout"),
    )
    return {
        "action": "verify",
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "task_name": plan.get("task_name"),
        "registration_kind": plan.get("registration_kind"),
        "registered": registered,
        "query_command": command_result.get("command"),
        "stdout": command_result.get("stdout"),
        "stderr": command_result.get("stderr"),
        "returncode": command_result.get("returncode"),
    }


def build_coordination_watch_activation_report(runtime_id: str, *, now_iso: str | None = None) -> dict[str, Any]:
    """Build a read-only activation proof report for coordination-watch startup.

    This aggregates the host scheduler query, local supervisor state, latest
    success/reboot evidence, and current agent-bus heartbeat liveness. It does
    not write bootstrap events or mutate host registration.
    """
    from runtime.agent_bus import bus

    plan = build_coordination_watch_bootstrap_plan(runtime_id)
    bootstrap_status = get_coordination_watch_bootstrap_status(runtime_id)
    supervisor_status = get_supervised_coordination_watch_status(runtime_id)
    runtime_name = str(plan.get("runtime_name") or runtime_id)
    now = _parse_iso_timestamp(now_iso) or datetime.now(timezone.utc)

    scheduler_command = ["schtasks", "/Query", "/TN", str(plan.get("task_name"))]
    scheduler_result = _run_registration_command(scheduler_command)
    scheduler_available = bool(scheduler_result.get("available", True))
    scheduler_registered = (
        scheduler_available
        and scheduler_result.get("returncode") == 0
        and str(plan.get("task_name")) in str(scheduler_result.get("stdout") or "")
    )

    try:
        heartbeats = bus.list_heartbeats(ROOT, runtime=runtime_name)
    except Exception as exc:
        heartbeats = []
        heartbeat_error = str(exc)
    else:
        heartbeat_error = None

    latest_heartbeat = None
    latest_heartbeat_seen = None
    for heartbeat in heartbeats:
        seen = _parse_iso_timestamp(str(heartbeat.get("last_seen") or ""))
        if seen is None:
            continue
        if latest_heartbeat_seen is None or seen > latest_heartbeat_seen:
            latest_heartbeat_seen = seen
            latest_heartbeat = heartbeat

    supervisor_interval = supervisor_status.get("interval_seconds")
    stale_after = supervisor_status.get("stale_after_seconds")
    try:
        heartbeat_stale_after_seconds = int(stale_after) if stale_after not in (None, "") else None
    except (TypeError, ValueError):
        heartbeat_stale_after_seconds = None
    if heartbeat_stale_after_seconds is None:
        try:
            heartbeat_stale_after_seconds = max(int(supervisor_interval or plan.get("interval_seconds") or 30) * 3, 90)
        except (TypeError, ValueError):
            heartbeat_stale_after_seconds = 90

    heartbeat_age_seconds = None
    heartbeat_fresh = False
    if latest_heartbeat_seen is not None:
        heartbeat_age_seconds = max(0, int((now - latest_heartbeat_seen).total_seconds()))
        heartbeat_fresh = heartbeat_age_seconds <= heartbeat_stale_after_seconds

    latest_success_record = bootstrap_status.get("latest_success_record")
    success_observed, success_evidence_issue = _success_evidence_confirmed(plan, latest_success_record)
    latest_reboot_verify_result = bootstrap_status.get("latest_reboot_verify_result")
    reboot_success_observed, reboot_evidence_issue = _reboot_evidence_confirmed(plan, latest_reboot_verify_result)

    checks = {
        "bootstrap_enabled": bool(bootstrap_status.get("bootstrap_enabled")),
        "installed": bool(bootstrap_status.get("installed")),
        "handoff_ready": bool(bootstrap_status.get("handoff_ready")),
        "reboot_verification_ready": bool(bootstrap_status.get("reboot_verification_ready")),
        "scheduler_available": scheduler_available,
        "scheduler_registered": scheduler_registered,
        "supervisor_state_present": bool(supervisor_status.get("state_present")),
        "supervisor_running": bool(supervisor_status.get("running")),
        "heartbeat_present": latest_heartbeat is not None,
        "heartbeat_fresh": heartbeat_fresh,
        "success_observed": success_observed,
        "reboot_success_observed": reboot_success_observed,
    }

    live_liveness_ready = checks["scheduler_registered"] and checks["supervisor_running"] and checks["heartbeat_fresh"]
    proof_complete = live_liveness_ready and checks["success_observed"] and checks["reboot_success_observed"]
    evidence_requirements = {
        "host_startup_registered": checks["scheduler_registered"],
        "supervisor_running": checks["supervisor_running"],
        "heartbeat_fresh": checks["heartbeat_fresh"],
        "success_record_observed": checks["success_observed"],
        "reboot_verification_observed": checks["reboot_success_observed"],
    }
    missing_evidence = [
        requirement
        for requirement, observed in evidence_requirements.items()
        if not observed
    ]
    checks["live_liveness_ready"] = live_liveness_ready
    checks["proof_complete"] = proof_complete

    next_actions: list[str] = []
    if not scheduler_available:
        next_actions.append("run-on-windows-host-or-handoff")
    elif not checks["installed"]:
        next_actions.append("install")
    if scheduler_available and checks["installed"] and not checks["scheduler_registered"]:
        next_actions.append("apply-or-handoff")
    if checks["scheduler_registered"] and not checks["supervisor_running"]:
        next_actions.append("start-or-wait-for-logon")
    if checks["scheduler_registered"] and checks["supervisor_running"] and not checks["heartbeat_fresh"]:
        next_actions.append("run-coordination-watch-once-or-check-loop")
    if checks["scheduler_registered"] and checks["supervisor_running"] and checks["heartbeat_fresh"] and not checks["success_observed"]:
        next_actions.append("capture-success")
    if checks["success_observed"] and not checks["reboot_success_observed"]:
        next_actions.append("run-reboot-verify-after-next-logon")

    proof_ready = live_liveness_ready
    if not scheduler_available:
        activation_state = "degraded"
    elif proof_complete:
        activation_state = "proven"
    elif proof_ready:
        if checks["success_observed"]:
            activation_state = "live-awaiting-reboot-proof"
        else:
            activation_state = "live-unrecorded"
    elif checks["installed"] or checks["scheduler_registered"] or checks["supervisor_state_present"] or checks["heartbeat_present"]:
        activation_state = "partial"
    else:
        activation_state = "inactive"

    return {
        "action": "activation-report",
        "runtime_id": runtime_id,
        "runtime_name": runtime_name,
        "task_name": plan.get("task_name"),
        "registration_kind": plan.get("registration_kind"),
        "activation_state": activation_state,
        "proof_ready": proof_ready,
        "proof_complete": proof_complete,
        "checks": checks,
        "activation_proof": {
            "proof_complete": proof_complete,
            "live_liveness_ready": live_liveness_ready,
            "required_evidence": evidence_requirements,
            "missing_evidence": missing_evidence,
            "evidence_validation": {
                "success_record_confirmed": success_observed,
                "success_record_issue": success_evidence_issue,
                "reboot_verify_result_confirmed": reboot_success_observed,
                "reboot_verify_result_issue": reboot_evidence_issue,
            },
            "evidence_paths": {
                "success_record_path": bootstrap_status.get("success_record_path"),
                "reboot_verify_result_path": bootstrap_status.get("reboot_verify_result_path"),
                "supervisor_state_file": supervisor_status.get("state_file"),
                "supervisor_log_file": supervisor_status.get("log_file"),
                "event_log_path": bootstrap_status.get("event_log_path"),
            },
        },
        "next_actions": next_actions,
        "scheduler": {
            "registered": scheduler_registered,
            "available": scheduler_available,
            "query_command": scheduler_result.get("command"),
            "returncode": scheduler_result.get("returncode"),
            "stdout": scheduler_result.get("stdout"),
            "stderr": scheduler_result.get("stderr"),
            "unavailable_reason": scheduler_result.get("unavailable_reason"),
            "error": scheduler_result.get("error"),
        },
        "supervisor": supervisor_status,
        "heartbeat": {
            "present": latest_heartbeat is not None,
            "fresh": heartbeat_fresh,
            "age_seconds": heartbeat_age_seconds,
            "stale_after_seconds": heartbeat_stale_after_seconds,
            "latest": latest_heartbeat,
            "error": heartbeat_error,
        },
        "latest_success_record": latest_success_record,
        "latest_reboot_verify_result": latest_reboot_verify_result,
        "bootstrap_status": bootstrap_status,
    }


def _activation_cli_command(runtime_id: str, action: str) -> list[str]:
    return [
        "chaseos",
        "runtime",
        "coordination-watch-bootstrap",
        "--runtime",
        runtime_id,
        "--action",
        action,
        "--json",
    ]


def _activation_supervisor_command(runtime_id: str, action: str) -> list[str]:
    return [
        "chaseos",
        "runtime",
        "coordination-watch-supervisor",
        "--runtime",
        runtime_id,
        "--action",
        action,
        "--json",
    ]


def _activation_watch_once_command(runtime_id: str) -> list[str]:
    return [
        "chaseos",
        "runtime",
        "coordination-watch",
        "--runtime",
        runtime_id,
        "--once",
        "--json",
    ]


def _activation_step(
    step_id: str,
    label: str,
    status: str,
    *,
    command: list[str] | None = None,
    host_command: str | None = None,
    evidence_key: str | None = None,
    depends_on: list[str] | None = None,
    external_side_effect: bool = False,
    requires_elevation: bool = False,
    host_action_required: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    step = {
        "step_id": step_id,
        "label": label,
        "status": status,
        "command": command,
        "host_command": host_command,
        "evidence_key": evidence_key,
        "depends_on": depends_on or [],
        "external_side_effect": external_side_effect,
        "requires_elevation": requires_elevation,
        "host_action_required": host_action_required,
    }
    if notes:
        step["notes"] = notes
    return step


def build_coordination_watch_activation_checklist(
    runtime_id: str,
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Return an operator-safe activation checklist for host startup proof.

    The checklist is read-only. It builds on activation-report evidence and maps
    the missing proof chain to explicit commands or host-side actions.
    """
    report = build_coordination_watch_activation_report(runtime_id, now_iso=now_iso)
    checks = dict(report.get("checks") or {})
    proof = dict(report.get("activation_proof") or {})
    paths = dict(proof.get("evidence_paths") or {})
    bootstrap_status = dict(report.get("bootstrap_status") or {})

    handoff_script_path = bootstrap_status.get("handoff_script_path")
    reboot_verify_script_path = bootstrap_status.get("reboot_verify_script_path")

    steps: list[dict[str, Any]] = []

    steps.append(
        _activation_step(
            "install-artifacts",
            "Write local bootstrap launcher and registration artifact",
            "complete" if checks.get("installed") else "ready",
            command=_activation_cli_command(runtime_id, "install"),
            evidence_key="installed",
            notes="Writes runtime-local bootstrap artifacts only.",
        )
    )

    handoff_status = "complete" if checks.get("handoff_ready") else ("ready" if checks.get("installed") else "waiting")
    steps.append(
        _activation_step(
            "generate-elevated-handoff",
            "Generate elevated Task Scheduler handoff bundle",
            handoff_status,
            command=_activation_cli_command(runtime_id, "handoff"),
            evidence_key="handoff_ready",
            depends_on=["install-artifacts"],
            requires_elevation=False,
            notes="Prepares a PowerShell/UAC handoff bundle; it does not mutate the scheduler by itself.",
        )
    )

    register_status = "complete" if checks.get("scheduler_registered") else (
        "ready" if checks.get("installed") or checks.get("handoff_ready") else "waiting"
    )
    steps.append(
        _activation_step(
            "register-host-startup",
            "Register the coordination-watch supervisor with host startup",
            register_status,
            command=_activation_cli_command(runtime_id, "apply"),
            host_command=f"PowerShell -ExecutionPolicy Bypass -File {handoff_script_path}" if handoff_script_path else None,
            evidence_key="host_startup_registered",
            depends_on=["install-artifacts", "generate-elevated-handoff"],
            external_side_effect=True,
            requires_elevation=True,
            host_action_required=register_status != "complete",
            notes="This is the first host-mutating step. Use the generated handoff when the current shell is not elevated.",
        )
    )

    steps.append(
        _activation_step(
            "verify-host-registration",
            "Verify host scheduler registration",
            "complete" if checks.get("scheduler_registered") else ("ready" if checks.get("installed") else "waiting"),
            command=_activation_cli_command(runtime_id, "verify"),
            evidence_key="host_startup_registered",
            depends_on=["register-host-startup"],
            notes="Read-only scheduler query.",
        )
    )

    steps.append(
        _activation_step(
            "start-or-confirm-supervisor",
            "Start or confirm the supervised coordination-watch loop",
            "complete" if checks.get("supervisor_running") else ("ready" if checks.get("scheduler_registered") else "waiting"),
            command=_activation_supervisor_command(runtime_id, "start"),
            evidence_key="supervisor_running",
            depends_on=["verify-host-registration"],
            external_side_effect=True,
            notes="Starts a bounded local background process when the scheduler has not already launched it.",
        )
    )

    steps.append(
        _activation_step(
            "publish-fresh-heartbeat",
            "Publish or confirm a fresh coordination-bus heartbeat",
            "complete" if checks.get("heartbeat_fresh") else ("ready" if checks.get("supervisor_running") else "waiting"),
            command=_activation_watch_once_command(runtime_id),
            evidence_key="heartbeat_fresh",
            depends_on=["start-or-confirm-supervisor"],
        )
    )

    live_ready = bool(checks.get("scheduler_registered") and checks.get("supervisor_running") and checks.get("heartbeat_fresh"))
    steps.append(
        _activation_step(
            "capture-success-state",
            "Capture durable scheduler plus supervisor success evidence",
            "complete" if checks.get("success_observed") else ("ready" if live_ready else "waiting"),
            command=_activation_cli_command(runtime_id, "capture-success"),
            evidence_key="success_record_observed",
            depends_on=["verify-host-registration", "start-or-confirm-supervisor", "publish-fresh-heartbeat"],
            notes=f"Success record path: {paths.get('success_record_path')}",
        )
    )

    reboot_bundle_status = "complete" if checks.get("reboot_verification_ready") else (
        "ready" if checks.get("installed") or checks.get("scheduler_registered") else "waiting"
    )
    steps.append(
        _activation_step(
            "prepare-reboot-verification",
            "Prepare post-reboot verification bundle",
            reboot_bundle_status,
            command=_activation_cli_command(runtime_id, "reboot-verify"),
            evidence_key="reboot_verification_ready",
            depends_on=["install-artifacts"],
            notes=f"Result path: {paths.get('reboot_verify_result_path')}",
        )
    )

    reboot_verify_status = "complete" if checks.get("reboot_success_observed") else (
        "host-action-required" if checks.get("reboot_verification_ready") and checks.get("scheduler_registered") else "waiting"
    )
    steps.append(
        _activation_step(
            "run-post-reboot-verification",
            "Run post-reboot/logon verification on the host",
            reboot_verify_status,
            host_command=f"PowerShell -ExecutionPolicy Bypass -File {reboot_verify_script_path}" if reboot_verify_script_path else None,
            evidence_key="reboot_verification_observed",
            depends_on=["register-host-startup", "prepare-reboot-verification"],
            host_action_required=reboot_verify_status != "complete",
            notes="Run only after a successful elevated registration and a host reboot/logon cycle.",
        )
    )

    reconcile_status = "complete" if report.get("proof_complete") else (
        "ready" if checks.get("reboot_success_observed") else "waiting"
    )
    steps.append(
        _activation_step(
            "reconcile-reboot-result",
            "Import reboot verification result into success-state evidence",
            reconcile_status,
            command=_activation_cli_command(runtime_id, "reconcile-reboot-result"),
            evidence_key="proof_complete",
            depends_on=["run-post-reboot-verification"],
        )
    )

    current_step = next((step for step in steps if step["status"] != "complete"), None)
    ready_commands = [
        step["command"]
        for step in steps
        if step.get("status") == "ready" and step.get("command")
    ]
    host_required_steps = [
        step
        for step in steps
        if step.get("status") == "host-action-required" or (
            step.get("host_action_required") and step.get("status") != "complete"
        )
    ]

    return {
        "action": "activation-checklist",
        "runtime_id": runtime_id,
        "runtime_name": report.get("runtime_name"),
        "task_name": report.get("task_name"),
        "registration_kind": report.get("registration_kind"),
        "activation_state": report.get("activation_state"),
        "proof_ready": report.get("proof_ready"),
        "proof_complete": report.get("proof_complete"),
        "current_step": current_step,
        "ready_commands": ready_commands,
        "host_required_steps": host_required_steps,
        "steps": steps,
        "missing_evidence": proof.get("missing_evidence") or [],
        "evidence_paths": paths,
        "source_report": report,
    }


def remove_coordination_watch_bootstrap(runtime_id: str) -> dict[str, Any]:
    plan = build_coordination_watch_bootstrap_plan(runtime_id)
    launcher_path = Path(str(plan.get("launcher_path")))
    artifact_path = Path(str(plan.get("registration_artifact")))
    handoff_script_path = Path(str(plan.get("handoff_script_path")))
    handoff_artifact_path = Path(str(plan.get("handoff_artifact_path")))
    reboot_verify_script_path = Path(str(plan.get("reboot_verify_script_path")))
    reboot_verify_artifact_path = Path(str(plan.get("reboot_verify_artifact_path")))

    removed_any = False
    if launcher_path.exists():
        launcher_path.unlink()
        removed_any = True
    if artifact_path.exists():
        artifact_path.unlink()
        removed_any = True
    if handoff_script_path.exists():
        handoff_script_path.unlink()
        removed_any = True
    if handoff_artifact_path.exists():
        handoff_artifact_path.unlink()
        removed_any = True
    if reboot_verify_script_path.exists():
        reboot_verify_script_path.unlink()
        removed_any = True
    if reboot_verify_artifact_path.exists():
        reboot_verify_artifact_path.unlink()
        removed_any = True

    _record_bootstrap_event(plan, "remove", removed=removed_any)
    return {
        "action": "remove",
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "removed": removed_any,
        "launcher_path": str(launcher_path),
        "registration_artifact": str(artifact_path),
        "handoff_script_path": str(handoff_script_path),
        "handoff_artifact_path": str(handoff_artifact_path),
        "task_name": plan.get("task_name"),
        "unregister_command": plan.get("unregister_command"),
    }


def unregister_coordination_watch_bootstrap(runtime_id: str) -> dict[str, Any]:
    plan = build_coordination_watch_bootstrap_plan(runtime_id)
    command_result = _run_registration_command(list(plan.get("unregister_command") or []))
    _record_bootstrap_event(
        plan,
        "unregister",
        unregistered=command_result.get("returncode") == 0,
        returncode=command_result.get("returncode"),
        stderr=command_result.get("stderr"),
        stdout=command_result.get("stdout"),
    )
    return {
        "action": "unregister",
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "task_name": plan.get("task_name"),
        "unregistered": command_result.get("returncode") == 0,
        "unregister_command": command_result.get("command"),
        "stdout": command_result.get("stdout"),
        "stderr": command_result.get("stderr"),
        "returncode": command_result.get("returncode"),
    }


def write_coordination_watch_bootstrap_handoff_bundle(runtime_id: str) -> dict[str, Any]:
    plan = build_coordination_watch_bootstrap_plan(runtime_id)
    install_result = install_coordination_watch_bootstrap(runtime_id)
    handoff_script_path = Path(str(plan.get("handoff_script_path")))
    handoff_artifact_path = Path(str(plan.get("handoff_artifact_path")))
    handoff_script_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_artifact_path.parent.mkdir(parents=True, exist_ok=True)

    verify_command = ["schtasks", "/Query", "/TN", str(plan.get("task_name"))]
    handoff_script_path.write_text(_build_handoff_script_contents(plan), encoding="utf-8")
    handoff_payload = {
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "task_name": plan.get("task_name"),
        "registration_kind": plan.get("registration_kind"),
        "launcher_path": install_result.get("launcher_path"),
        "registration_artifact": install_result.get("registration_artifact"),
        "event_log_path": plan.get("event_log_path"),
        "register_command": plan.get("register_command"),
        "verify_command": verify_command,
        "unregister_command": plan.get("unregister_command"),
        "requires_elevation": True,
        "manual_steps": [
            f"Run PowerShell -ExecutionPolicy Bypass -File {_to_windows_path(handoff_script_path)} and approve the UAC prompt.",
            f"Re-run verify after approval: schtasks /Query /TN {plan.get('task_name')}",
        ],
        "notes": plan.get("notes"),
    }
    handoff_artifact_path.write_text(json.dumps(handoff_payload, indent=2), encoding="utf-8")
    _record_bootstrap_event(plan, "handoff", handoff_ready=True, handoff_script_path=str(handoff_script_path), handoff_artifact_path=str(handoff_artifact_path))

    return {
        "action": "handoff",
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "task_name": plan.get("task_name"),
        "registration_kind": plan.get("registration_kind"),
        "launcher_path": install_result.get("launcher_path"),
        "registration_artifact": install_result.get("registration_artifact"),
        "handoff_script_path": str(handoff_script_path),
        "handoff_artifact_path": str(handoff_artifact_path),
        "handoff_ready": True,
        "requires_elevation": True,
        "register_command": plan.get("register_command"),
        "query_command": verify_command,
        "unregister_command": plan.get("unregister_command"),
        "wsl_distro": plan.get("wsl_distro"),
    }


def write_coordination_watch_bootstrap_reboot_verification_bundle(runtime_id: str) -> dict[str, Any]:
    plan = build_coordination_watch_bootstrap_plan(runtime_id)
    supervisor_plan = build_supervised_coordination_watch_plan(runtime_id)
    reboot_verify_script_path = Path(str(plan.get("reboot_verify_script_path")))
    reboot_verify_artifact_path = Path(str(plan.get("reboot_verify_artifact_path")))
    reboot_verify_result_path = Path(str(plan.get("reboot_verify_result_path")))
    prepared_at_utc = _utc_now_iso()
    reboot_verify_script_path.parent.mkdir(parents=True, exist_ok=True)
    reboot_verify_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    reboot_verify_result_path.parent.mkdir(parents=True, exist_ok=True)

    verification_command = ["schtasks", "/Query", "/TN", str(plan.get("task_name"))]
    supervisor_state_file = str(plan.get("state_file") or supervisor_plan.get("state_file") or "")
    supervisor_log_file = str(plan.get("log_file") or supervisor_plan.get("log_file") or "")
    reboot_verify_script_path.write_text(
        _build_reboot_verification_script_contents(
            plan,
            verification_command,
            supervisor_state_file,
            supervisor_log_file,
            str(reboot_verify_result_path),
            prepared_at_utc,
        ),
        encoding="utf-8",
    )
    reboot_payload = {
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "task_name": plan.get("task_name"),
        "registration_kind": plan.get("registration_kind"),
        "prepared_at_utc": prepared_at_utc,
        "event_log_path": plan.get("event_log_path"),
        "verification_command": verification_command,
        "expected_supervisor_state_file": supervisor_state_file,
        "expected_supervisor_log_file": supervisor_log_file,
        "result_output_path": str(reboot_verify_result_path),
        "manual_steps": [
            "Run this bundle after a successful elevated registration and a host reboot/logon cycle.",
            f"Confirm the scheduled task still resolves: schtasks /Query /TN {plan.get('task_name')}",
            "Confirm the scheduled task LastRunTime is after the current boot time.",
            "Confirm supervisor state/log artifacts reappear as expected for the coordination-watch loop.",
        ],
        "notes": plan.get("notes"),
    }
    reboot_verify_artifact_path.write_text(json.dumps(reboot_payload, indent=2), encoding="utf-8")
    _record_bootstrap_event(
        plan,
        "reboot-verify",
        reboot_verification_ready=True,
        reboot_verify_script_path=str(reboot_verify_script_path),
        reboot_verify_artifact_path=str(reboot_verify_artifact_path),
        reboot_verify_result_path=str(reboot_verify_result_path),
    )

    return {
        "action": "reboot-verify",
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "task_name": plan.get("task_name"),
        "registration_kind": plan.get("registration_kind"),
        "reboot_verify_script_path": str(reboot_verify_script_path),
        "reboot_verify_artifact_path": str(reboot_verify_artifact_path),
        "reboot_verify_result_path": str(reboot_verify_result_path),
        "prepared_at_utc": prepared_at_utc,
        "reboot_verification_ready": True,
        "query_command": verification_command,
        "expected_supervisor_state_file": supervisor_state_file,
        "expected_supervisor_log_file": supervisor_log_file,
        "wsl_distro": plan.get("wsl_distro"),
    }


def capture_coordination_watch_bootstrap_success_state(runtime_id: str) -> dict[str, Any]:
    plan = build_coordination_watch_bootstrap_plan(runtime_id)
    supervisor_plan = build_supervised_coordination_watch_plan(runtime_id)
    success_record_path = Path(str(plan.get("success_record_path")))
    success_record_path.parent.mkdir(parents=True, exist_ok=True)

    verification = verify_coordination_watch_bootstrap(runtime_id)
    reboot_verify_result_path = Path(str(plan.get("reboot_verify_result_path"))) if plan.get("reboot_verify_result_path") else None
    reboot_verify_result = _read_json_file(reboot_verify_result_path) if reboot_verify_result_path else None
    evidence_source = "live_verify"
    reboot_verify_result_rejected_reason = None

    supervisor_state_file = Path(str(plan.get("state_file") or supervisor_plan.get("state_file") or "")) if (plan.get("state_file") or supervisor_plan.get("state_file")) else None
    supervisor_log_file = Path(str(plan.get("log_file") or supervisor_plan.get("log_file") or "")) if (plan.get("log_file") or supervisor_plan.get("log_file")) else None
    supervisor_state_present = bool(supervisor_state_file and supervisor_state_file.exists())
    supervisor_log_present = bool(supervisor_log_file and supervisor_log_file.exists())
    scheduler_registered = bool(verification.get("registered"))

    if isinstance(reboot_verify_result, dict) and reboot_verify_result and not reboot_verify_result.get("error"):
        reboot_verify_result_rejected_reason = _evidence_identity_issue(plan, reboot_verify_result)
        if reboot_verify_result_rejected_reason is None:
            _, reboot_verify_result_rejected_reason = _reboot_evidence_confirmed(plan, reboot_verify_result)
    if (
        isinstance(reboot_verify_result, dict)
        and reboot_verify_result
        and not reboot_verify_result.get("error")
        and reboot_verify_result_rejected_reason is None
    ):
        evidence_source = "reboot_verify_result"
        scheduler_registered = bool(reboot_verify_result.get("scheduler_registered"))
        supervisor_state_present = bool(reboot_verify_result.get("supervisor_state_present"))
        supervisor_log_present = bool(reboot_verify_result.get("supervisor_log_present"))
        if reboot_verify_result.get("supervisor_state_file"):
            supervisor_state_file = Path(str(reboot_verify_result.get("supervisor_state_file")))
        if reboot_verify_result.get("supervisor_log_file"):
            supervisor_log_file = Path(str(reboot_verify_result.get("supervisor_log_file")))
    success_observed = scheduler_registered and supervisor_state_present and supervisor_log_present
    agent_activity_record_path: Path | None = None

    record = {
        "timestamp_utc": _utc_now_iso(),
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "task_name": plan.get("task_name"),
        "registration_kind": plan.get("registration_kind"),
        "evidence_source": evidence_source,
        "reboot_verify_result_path": str(reboot_verify_result_path) if reboot_verify_result_path else None,
        "scheduler_registered": scheduler_registered,
        "verification_returncode": reboot_verify_result.get("verification_returncode") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else verification.get("returncode"),
        "verification_stdout": reboot_verify_result.get("verification_stdout") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else verification.get("stdout"),
        "verification_stderr": reboot_verify_result.get("verification_stderr") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else verification.get("stderr"),
        "verification_command": reboot_verify_result.get("verification_command") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else verification.get("query_command"),
        "scheduler_registration_evidence": reboot_verify_result.get("scheduler_registration_evidence") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else None,
        "prepared_at_utc": reboot_verify_result.get("prepared_at_utc") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else None,
        "current_boot_time_utc": reboot_verify_result.get("current_boot_time_utc") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else None,
        "reboot_observed": reboot_verify_result.get("reboot_observed") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else None,
        "task_last_run_time_utc": reboot_verify_result.get("task_last_run_time_utc") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else None,
        "task_last_result": reboot_verify_result.get("task_last_result") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else None,
        "scheduled_task_ran_after_boot": reboot_verify_result.get("scheduled_task_ran_after_boot") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else None,
        "scheduled_task_last_result_ok": reboot_verify_result.get("scheduled_task_last_result_ok") if evidence_source == "reboot_verify_result" and isinstance(reboot_verify_result, dict) else None,
        "reboot_verify_result_imported": evidence_source == "reboot_verify_result",
        "reboot_verify_result_rejected_reason": reboot_verify_result_rejected_reason,
        "supervisor_state_file": str(supervisor_state_file) if supervisor_state_file else None,
        "supervisor_state_present": supervisor_state_present,
        "supervisor_log_file": str(supervisor_log_file) if supervisor_log_file else None,
        "supervisor_log_present": supervisor_log_present,
        "success_observed": success_observed,
        "event_log_path": plan.get("event_log_path"),
        "agent_activity_record_path": None,
    }
    if success_observed:
        agent_activity_record_path = _write_success_activity_record(plan, record)
        record["agent_activity_record_path"] = str(agent_activity_record_path)

    success_record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    _record_bootstrap_event(
        plan,
        "capture-success",
        success_record_written=True,
        success_record_path=str(success_record_path),
        success_observed=success_observed,
        evidence_source=evidence_source,
        reboot_verify_result_path=str(reboot_verify_result_path) if reboot_verify_result_path else None,
        reboot_verify_result_imported=evidence_source == "reboot_verify_result",
        reboot_verify_result_rejected_reason=reboot_verify_result_rejected_reason,
        agent_activity_record_written=bool(agent_activity_record_path),
        agent_activity_record_path=str(agent_activity_record_path) if agent_activity_record_path else None,
    )

    return {
        "action": "capture-success",
        "runtime_id": runtime_id,
        "runtime_name": plan.get("runtime_name"),
        "task_name": plan.get("task_name"),
        "success_record_path": str(success_record_path),
        "success_record_written": True,
        "evidence_source": evidence_source,
        "reboot_verify_result_path": str(reboot_verify_result_path) if reboot_verify_result_path else None,
        "reboot_verify_result_imported": evidence_source == "reboot_verify_result",
        "reboot_verify_result_rejected_reason": reboot_verify_result_rejected_reason,
        "scheduler_registered": scheduler_registered,
        "supervisor_state_present": supervisor_state_present,
        "supervisor_log_present": supervisor_log_present,
        "success_observed": success_observed,
        "agent_activity_record_written": bool(agent_activity_record_path),
        "agent_activity_record_path": str(agent_activity_record_path) if agent_activity_record_path else None,
        "query_command": record.get("verification_command"),
        "expected_supervisor_state_file": str(supervisor_state_file) if supervisor_state_file else None,
        "expected_supervisor_log_file": str(supervisor_log_file) if supervisor_log_file else None,
        "wsl_distro": plan.get("wsl_distro"),
    }


def reconcile_coordination_watch_bootstrap_reboot_result(runtime_id: str) -> dict[str, Any]:
    result = dict(capture_coordination_watch_bootstrap_success_state(runtime_id))
    result["action"] = "reconcile-reboot-result"
    result["reconciled"] = result.get("evidence_source") == "reboot_verify_result"
    return result
