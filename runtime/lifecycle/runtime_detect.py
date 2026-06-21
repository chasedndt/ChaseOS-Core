"""ChaseOS runtime binary detection — Stage 1 of the activation contract.

Read-only. Detects whether a runtime agent's program is present on this host,
its version, and (optionally) whether it is currently running. Fail-closed:
anything it cannot confirm is reported as ``unknown``, which callers treat as
``absent``. It never mutates the host — the only subprocess calls are read-only
presence/version probes, and the runner is injectable so tests never shell out.

See ``06_AGENTS/Runtime-Setup-and-Activation-Architecture.md`` (Stage 1) and the
per-runtime activation runbooks. Detection also backs the optional
``--probe-binaries`` check in ``runtime/lifecycle/runtime_doctor.py``.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

try:
    from runtime.lifecycle.health_cli import check_health, load_lifecycle_record
except ModuleNotFoundError:  # pragma: no cover - direct script compatibility
    from health_cli import check_health, load_lifecycle_record  # type: ignore


STATUS_PRESENT = "present"
STATUS_ABSENT = "absent"
STATUS_WRONG_VERSION = "present-wrong-version"
STATUS_UNKNOWN = "unknown"

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")

# Declarative per-runtime detection strategy. A new runtime is a table entry.
#   platform     — "windows" (host binary) or "wsl" (binary inside a WSL distro)
#   version_cmd  — argv list (windows) used to probe presence + version
#   wsl_version  — shell command (wsl) run via `bash -lc` so ~/.local/bin is on PATH
#   min_major    — minimum acceptable major version of the runtime binary (None = any)
#   companion    — an optional required host tool (e.g. Node 24 for OpenClaw)
DETECT_STRATEGY: dict[str, dict[str, Any]] = {
    "openclaw": {
        "platform": "windows",
        "version_cmd": ["openclaw", "--version"],
        "min_major": None,
        "companion": {
            "name": "node",
            "version_cmd": ["node", "--version"],
            "min_major": 24,
        },
    },
    "hermes": {
        "platform": "wsl",
        "wsl_version": "hermes --version",
        "min_major": None,
        "companion": None,
    },
}


def _authority() -> dict[str, bool]:
    return {
        "read_only": True,
        "host_mutation_performed": False,
        "writes_performed": False,
        "provider_calls_performed": False,
        "secret_values_read": False,
    }


def _default_runner(cmd: list[str], *, timeout: int = 8) -> dict[str, Any]:
    """Run a read-only probe command. Never raises — returns a structured result."""
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": timeout}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        proc = subprocess.run(cmd, **kwargs)
        return {
            "ok": True,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": "not_found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": "timeout"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": str(exc)}


def _parse_major(text: str) -> tuple[str | None, int | None]:
    if not text:
        return None, None
    match = _VERSION_RE.search(text)
    if not match:
        return None, None
    version = match.group(0)
    try:
        return version, int(match.group(1))
    except (TypeError, ValueError):
        return version, None


def _wsl_distro(record: dict[str, Any]) -> str:
    surfaces = record.get("startup_surfaces") if isinstance(record, dict) else None
    if isinstance(surfaces, dict):
        gateway = surfaces.get("gateway")
        if isinstance(gateway, dict) and gateway.get("wsl_distro"):
            return str(gateway["wsl_distro"])
    coordination = record.get("coordination_watch") if isinstance(record, dict) else None
    if isinstance(coordination, dict):
        bootstrap = coordination.get("bootstrap")
        if isinstance(bootstrap, dict) and bootstrap.get("wsl_distro"):
            return str(bootstrap["wsl_distro"])
    return "Ubuntu"


def _probe_one(
    runner: Callable[[list[str]], dict[str, Any]],
    cmd: list[str],
    min_major: int | None,
) -> dict[str, Any]:
    """Probe a single binary via its version command. Fail-closed."""
    result = runner(cmd)
    if not isinstance(result, dict):  # a misbehaving injected runner → unknown
        return {
            "present": False, "version": None, "version_ok": False, "major": None,
            "status": STATUS_UNKNOWN, "command": cmd, "raw": {"error": "bad_runner_result"},
        }
    ran = bool(result.get("ok"))
    rc = result.get("returncode")
    combined = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
    version, major = _parse_major(combined)

    if not ran and result.get("error") == "not_found":
        status, present = STATUS_ABSENT, False
    elif not ran:  # timeout / unexpected error → cannot confirm
        status, present = STATUS_UNKNOWN, False
    elif rc == 0 and version is not None:
        present = True
        if min_major is not None and major is not None and major < min_major:
            status = STATUS_WRONG_VERSION
        else:
            status = STATUS_PRESENT
    elif rc == 0:
        # ran cleanly but no parseable version — treat as present, version unknown
        present, status = True, STATUS_PRESENT
    else:
        # non-zero exit with no version — cannot positively confirm presence
        present, status = False, STATUS_UNKNOWN

    version_ok = present and (min_major is None or major is None or major >= min_major)
    return {
        "present": present,
        "version": version,
        "version_ok": version_ok,
        "major": major,
        "status": status,
        "command": cmd,
        "raw": {"returncode": rc, "ran": ran, "error": result.get("error")},
    }


def detect_runtime(
    runtime_id: str,
    vault_root: str | Path | None = None,
    *,
    runner: Callable[[list[str]], dict[str, Any]] | None = None,
    probe_running: bool = False,
) -> dict[str, Any]:
    """Detect whether ``runtime_id`` is installed/runnable on this host.

    Returns a stable dict with ``status`` in {present, absent,
    present-wrong-version, unknown}. ``unknown`` means "could not confirm" and is
    treated as absent by callers. Read-only; never mutates the host.
    """
    rid = str(runtime_id or "").strip().lower()
    run = runner or _default_runner
    strategy = DETECT_STRATEGY.get(rid)

    if strategy is None:
        return {
            "runtime_id": rid,
            "supported": False,
            "present": False,
            "status": STATUS_UNKNOWN,
            "detail": f"No detection strategy for runtime '{rid}'.",
            "authority": _authority(),
            "probes": [],
        }

    platform = strategy.get("platform")
    probes: list[dict[str, Any]] = []
    companion_report: dict[str, Any] | None = None

    if platform == "wsl":
        try:
            record = load_lifecycle_record(rid, vault_root)
        except Exception:
            record = {}
        distro = _wsl_distro(record)
        shell_cmd = str(strategy.get("wsl_version") or "")
        cmd = ["wsl", "-d", distro, "bash", "-lc", shell_cmd]
        detect_method = f"wsl:{distro}:bash -lc {shell_cmd!r}"
        binary = _probe_one(run, cmd, strategy.get("min_major"))
    else:  # windows host binary
        cmd = list(strategy.get("version_cmd") or [])
        detect_method = f"host:{' '.join(cmd)}"
        binary = _probe_one(run, cmd, strategy.get("min_major"))
        companion = strategy.get("companion")
        if isinstance(companion, dict):
            comp_probe = _probe_one(run, list(companion["version_cmd"]), companion.get("min_major"))
            companion_report = {
                "name": companion.get("name"),
                "present": comp_probe["present"],
                "version": comp_probe["version"],
                "version_ok": comp_probe["version_ok"],
                "min_major": companion.get("min_major"),
                "status": comp_probe["status"],
            }
            probes.append({"role": "companion", **comp_probe})

    probes.insert(0, {"role": "binary", **binary})

    running = False
    health_status = "not_probed"
    if probe_running:
        try:
            health = check_health(rid, vault_root=vault_root)
            running = bool(health.get("healthy"))
            health_status = str(health.get("status"))
        except Exception as exc:  # pragma: no cover - defensive
            running, health_status = False, f"probe_error:{exc}"

    return {
        "runtime_id": rid,
        "supported": True,
        "platform": platform,
        "present": binary["present"],
        "version": binary["version"],
        "version_ok": binary["version_ok"],
        "status": binary["status"],
        "companion": companion_report,
        "running": running,
        "health_status": health_status,
        "detect_method": detect_method,
        "authority": _authority(),
        "probes": probes,
    }
