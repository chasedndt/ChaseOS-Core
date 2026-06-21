"""ChaseOS runtime lifecycle health foothold.

Reads machine-readable lifecycle records and runs the declared health command.
Phase 9 foothold only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shlex
import sqlite3
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False


LIFECYCLE_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
HEALTH_STATUS_HEALTHY = "healthy"
HEALTH_STATUS_UNAVAILABLE = "unavailable"
HEALTH_STATUS_NOT_CONFIGURED = "not_configured"
HEALTH_STATUS_INVALID_RUNTIME = "invalid_runtime"


def _authority_flags() -> dict[str, bool]:
    return {
        "read_only": True,
        "mutates_runtime": False,
        "starts_gateway": False,
        "stops_gateway": False,
        "writes_performed": False,
    }


def _health_result(
    *,
    runtime_id: str,
    kind: str,
    status: str,
    timeout_seconds: int,
    probe_label: str | None = None,
    probe_notes: str | None = None,
    command: str | None = None,
    expected_hint: str | None = None,
    candidate_urls: list[str] | None = None,
    candidate_ports: list[int] | None = None,
    probes: list[dict[str, Any]] | None = None,
    detected_url: str | None = None,
    timed_out: bool = False,
    status_code: int | None = None,
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    healthy = status == HEALTH_STATUS_HEALTHY
    errors: list[dict[str, str]] = []
    if blocked_reason:
        errors.append({"code": blocked_reason, "message": blocked_reason})

    urls = candidate_urls or []
    return {
        "runtime_id": runtime_id,
        "kind": kind,
        "status": status,
        "healthy": healthy,
        "gateway_detected": healthy,
        "detected_url": detected_url,
        "url": detected_url,
        "candidate_urls": urls,
        "urls": urls,
        "candidate_ports": candidate_ports or [],
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "status_code": status_code,
        "returncode": returncode,
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "failure_reason": blocked_reason,
        "blocked_reason": blocked_reason,
        "expected_hint": expected_hint,
        "probe_label": probe_label,
        "probe_notes": probe_notes,
        "probes": probes or [],
        "writes_performed": False,
        "authority_flags": _authority_flags(),
        "errors": errors,
        "warnings": [],
    }


def _effective_timeout(configured_timeout: Any, requested_timeout: int) -> int:
    requested = int(requested_timeout or 5)
    if configured_timeout in (None, ""):
        return requested
    return min(int(configured_timeout), requested)


def _coerce_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """L-1 fix: use yaml.safe_load (PyYAML) when available; fall back to the
    hand-rolled parser only when PyYAML is not installed.  yaml.safe_load is
    safe against arbitrary-object deserialization and handles the full YAML 1.1
    spec — including quoted strings, nested mappings, and lists — that the
    bespoke parser partially supported."""
    if _YAML_AVAILABLE:
        result = _yaml.safe_load(text)
        return result if isinstance(result, dict) else {}

    # ── Fallback: hand-rolled minimal parser (kept for environments without PyYAML) ──
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


def _resolve_vault_root(vault_root: str | Path | None = None) -> Path:
    return Path(vault_root).resolve() if vault_root else REPO_ROOT


def _lifecycle_dir(vault_root: str | Path | None = None) -> Path:
    if vault_root is None:
        return LIFECYCLE_DIR
    return _resolve_vault_root(vault_root) / "runtime" / "lifecycle"


def load_lifecycle_record(runtime_id: str, vault_root: str | Path | None = None) -> dict[str, Any]:
    path = _lifecycle_dir(vault_root) / f"{runtime_id}.lifecycle.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No lifecycle record found for runtime: {runtime_id}")
    return _parse_simple_yaml(path.read_text(encoding="utf-8"))


def _status_is_success(status_code: int | None, success_status: Any) -> bool:
    if status_code is None:
        return False
    if isinstance(success_status, (list, tuple, set)):
        return status_code in {int(item) for item in success_status}
    return status_code == int(success_status)


def _probe_http_url(url: str, success_status: Any, timeout_seconds: int) -> dict[str, Any]:
    # M-4 security fix: validate URL scheme before urlopen to prevent file:// / ftp:// SSRF
    if not (url.startswith("http://") or url.startswith("https://")):
        return {
            "success": False,
            "status_code": None,
            "body": "",
            "timed_out": False,
            "error_text": f"URL scheme not allowed: {url!r} (must be http:// or https://)",
            "failure_reason": "invalid_url_scheme",
        }
    timed_out = False
    status_code = None
    body = ""
    error_text = ""
    failure_reason = None
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            status_code = response.getcode()
            raw = response.read(512)
            body = raw.decode("utf-8", errors="ignore") if raw else ""
        success = _status_is_success(status_code, success_status)
        if not success:
            failure_reason = f"unexpected_status:{status_code}"
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        success = _status_is_success(status_code, success_status)
        if not success:
            error_text = str(exc)
            failure_reason = f"http_error:{exc.code}"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        timed_out = isinstance(reason, TimeoutError)
        success = False
        error_text = str(exc)
        failure_reason = "timeout" if timed_out else "connection_error"
    except TimeoutError as exc:
        timed_out = True
        success = False
        error_text = str(exc)
        failure_reason = "timeout"

    return {
        "url": url,
        "timed_out": timed_out,
        "status_code": status_code,
        "healthy": success,
        "stdout": body,
        "stderr": error_text,
        "failure_reason": failure_reason,
    }


def _parse_iso_timestamp(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _load_heartbeat_capability(runtime_id: str, vault_root: Path) -> tuple[Any | None, str | None]:
    try:
        from runtime.agent_bus.capabilities import CapabilityError, load_runtime_capabilities
    except Exception as exc:
        return None, f"capability_loader_unavailable:{exc}"

    try:
        return load_runtime_capabilities(runtime_id, vault_root), None
    except CapabilityError as exc:
        return None, str(exc)


def _read_heartbeat_rows(vault_root: Path, bus_name: str) -> tuple[list[dict[str, Any]], str | None, str]:
    db_path = vault_root / "runtime" / "agent_bus" / "agent_bus.sqlite"
    if not db_path.exists():
        return [], "heartbeat_store_missing", str(db_path)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM heartbeats WHERE runtime = ? ORDER BY last_seen DESC",
                (bus_name,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return [], "heartbeat_table_missing", str(db_path)
        return [], "heartbeat_read_error", str(db_path)
    except sqlite3.Error:
        return [], "heartbeat_read_error", str(db_path)

    return [dict(row) for row in rows], None, str(db_path)


def _heartbeat_health_result(
    *,
    runtime_id: str,
    health: dict[str, Any],
    timeout_seconds: int,
    vault_root: Path,
) -> dict[str, Any]:
    expected_hint = health.get("expected_hint")
    caps, capability_error = _load_heartbeat_capability(runtime_id, vault_root)
    if caps is None:
        result = _health_result(
            runtime_id=runtime_id,
            kind="heartbeat",
            status=HEALTH_STATUS_NOT_CONFIGURED,
            timeout_seconds=timeout_seconds,
            expected_hint=expected_hint,
            blocked_reason="heartbeat_capability_manifest_missing",
        )
        result.update(
            {
                "heartbeat_runtime": None,
                "heartbeat_present": False,
                "heartbeat_fresh": False,
                "heartbeat_count": 0,
                "heartbeat_error": capability_error,
                "gateway_detected": False,
            }
        )
        return result

    bus_name = str(getattr(caps, "bus_name", runtime_id))
    stale_after = int(getattr(caps, "heartbeat_stale_seconds", 900))
    rows, read_error, heartbeat_store_path = _read_heartbeat_rows(vault_root, bus_name)
    now = dt.datetime.now(dt.timezone.utc)

    freshest: dict[str, Any] | None = None
    freshest_dt: dt.datetime | None = None
    for row in rows:
        parsed = _parse_iso_timestamp(row.get("last_seen"))
        if parsed is None:
            continue
        if freshest_dt is None or parsed > freshest_dt:
            freshest = row
            freshest_dt = parsed

    age_seconds = (now - freshest_dt).total_seconds() if freshest_dt is not None else None
    heartbeat_present = bool(rows)
    heartbeat_fresh = bool(age_seconds is not None and age_seconds <= stale_after)
    latest_status = freshest.get("status") if freshest else None
    latest_health = freshest.get("health") if freshest else None

    blocked_reason = read_error
    if blocked_reason is None and not heartbeat_present:
        blocked_reason = "heartbeat_missing"
    elif blocked_reason is None and freshest is None:
        blocked_reason = "heartbeat_last_seen_invalid"
    elif blocked_reason is None and not heartbeat_fresh:
        blocked_reason = "heartbeat_stale"
    elif blocked_reason is None and latest_status == "offline":
        blocked_reason = "heartbeat_status_offline"
    elif blocked_reason is None and latest_health != "ok":
        blocked_reason = f"heartbeat_health_{latest_health or 'unknown'}"

    healthy = blocked_reason is None
    probe = {
        "kind": "heartbeat",
        "runtime": bus_name,
        "heartbeat_store_path": heartbeat_store_path,
        "heartbeat_present": heartbeat_present,
        "heartbeat_fresh": heartbeat_fresh,
        "heartbeat_count": len(rows),
        "last_seen": freshest.get("last_seen") if freshest else None,
        "age_seconds": age_seconds,
        "stale_after_seconds": stale_after,
        "status": latest_status,
        "health": latest_health,
        "healthy": healthy,
        "failure_reason": blocked_reason,
    }
    result = _health_result(
        runtime_id=runtime_id,
        kind="heartbeat",
        status=HEALTH_STATUS_HEALTHY if healthy else HEALTH_STATUS_UNAVAILABLE,
        timeout_seconds=timeout_seconds,
        expected_hint=expected_hint,
        blocked_reason=blocked_reason,
        probes=[probe],
    )
    result.update(
        {
            "gateway_detected": False,
            "detected_url": None,
            "url": None,
            "heartbeat_runtime": bus_name,
            "heartbeat_present": heartbeat_present,
            "heartbeat_fresh": heartbeat_fresh,
            "heartbeat_count": len(rows),
            "heartbeat_age_seconds": age_seconds,
            "heartbeat_stale_after_seconds": stale_after,
            "heartbeat_status": latest_status,
            "heartbeat_health": latest_health,
            "heartbeat_store_path": heartbeat_store_path,
            "latest_heartbeat": freshest,
            "heartbeats": rows,
        }
    )
    return result


def check_health(
    runtime_id: str,
    timeout_seconds: int = 5,
    vault_root: str | Path | None = None,
) -> dict[str, Any]:
    try:
        record = load_lifecycle_record(runtime_id, vault_root=vault_root)
    except FileNotFoundError:
        return _health_result(
            runtime_id=runtime_id,
            kind="unknown",
            status=HEALTH_STATUS_INVALID_RUNTIME,
            timeout_seconds=int(timeout_seconds or 5),
            blocked_reason="lifecycle_record_missing",
        )

    health = record.get("health", {})
    if not isinstance(health, dict) or not health:
        return _health_result(
            runtime_id=runtime_id,
            kind="unknown",
            status=HEALTH_STATUS_NOT_CONFIGURED,
            timeout_seconds=int(timeout_seconds or 5),
            blocked_reason="health_not_configured",
        )

    kind = health.get("kind", "command")
    configured_timeout = health.get("timeout_seconds")
    effective_timeout = _effective_timeout(configured_timeout, timeout_seconds)

    if kind == "heartbeat":
        return _heartbeat_health_result(
            runtime_id=runtime_id,
            health=health,
            timeout_seconds=effective_timeout,
            vault_root=_resolve_vault_root(vault_root),
        )

    if kind == "http":
        urls = health.get("urls") or []
        if not urls and health.get("url"):
            urls = [health.get("url")]
        success_status = health.get("success_statuses") or health.get("success_status", 200)
        probe_label = health.get("probe_label")
        probe_notes = health.get("notes")
        candidate_ports = health.get("candidate_ports") or []
        if not urls:
            return _health_result(
                runtime_id=runtime_id,
                kind=str(kind),
                status=HEALTH_STATUS_NOT_CONFIGURED,
                timeout_seconds=effective_timeout,
                probe_label=probe_label,
                probe_notes=probe_notes,
                candidate_ports=[int(port) for port in candidate_ports],
                blocked_reason="health_url_not_configured",
            )

        probe_results = [_probe_http_url(str(url), success_status, effective_timeout) for url in urls]
        winning = next((item for item in probe_results if item.get("healthy")), probe_results[0])
        healthy = bool(winning.get("healthy", False))
        blocked_reason = None if healthy else (winning.get("failure_reason") or "gateway_unavailable")

        return _health_result(
            runtime_id=runtime_id,
            kind=str(kind),
            status=HEALTH_STATUS_HEALTHY if healthy else HEALTH_STATUS_UNAVAILABLE,
            timeout_seconds=effective_timeout,
            probe_label=probe_label,
            probe_notes=probe_notes,
            detected_url=winning.get("url") if healthy else None,
            candidate_urls=[str(url) for url in urls],
            candidate_ports=[int(port) for port in candidate_ports],
            timed_out=bool(winning.get("timed_out", False)),
            status_code=winning.get("status_code"),
            stdout=winning.get("stdout", ""),
            stderr=winning.get("stderr", ""),
            blocked_reason=blocked_reason,
            probes=probe_results,
        )

    command = health.get("command")
    expected_hint = health.get("expected_hint")

    if not command:
        return _health_result(
            runtime_id=runtime_id,
            kind=str(kind),
            status=HEALTH_STATUS_NOT_CONFIGURED,
            timeout_seconds=effective_timeout,
            expected_hint=expected_hint,
            blocked_reason=f"health_{kind}_probe_not_configured",
        )

    timed_out = False
    try:
        import os as _os
        # H-3 fix: shell=False — command is split with shlex to handle quoted args
        # correctly without exposing shell metacharacters to interpretation.
        _cmd_list = shlex.split(command)
        _hkw: dict = {"capture_output": True, "text": True, "shell": False, "timeout": effective_timeout}
        if _os.name == "nt":
            _hkw["creationflags"] = subprocess.CREATE_NO_WINDOW
        completed = subprocess.run(_cmd_list, **_hkw)
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        success = completed.returncode == 0
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or "").strip() if exc.stdout else ""
        stderr = (exc.stderr or "").strip() if exc.stderr else ""
        success = False
        completed = None

    if success and expected_hint:
        success = expected_hint.lower() in stdout.lower()

    returncode = None if completed is None else completed.returncode
    blocked_reason = None
    if not success:
        blocked_reason = "timeout" if timed_out else f"command_exit_{returncode}"

    return _health_result(
        runtime_id=runtime_id,
        kind=str(kind),
        command=str(command),
        status=HEALTH_STATUS_HEALTHY if success else HEALTH_STATUS_UNAVAILABLE,
        timeout_seconds=effective_timeout,
        timed_out=timed_out,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        expected_hint=expected_hint,
        blocked_reason=blocked_reason,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="ChaseOS runtime lifecycle health foothold")
    parser.add_argument("runtime_id", help="Runtime id to health-check")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument("--timeout", type=int, default=5, help="Timeout in seconds for the health command")
    parser.add_argument("--vault-root", default=None, metavar="PATH", help="Override vault root path")
    args = parser.parse_args()

    result = check_health(args.runtime_id, timeout_seconds=args.timeout, vault_root=args.vault_root)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Runtime health: {args.runtime_id}")
        if result.get("command"):
            print(f"  command: {result['command']}")
        print(f"  status: {result['status']}")
        print(f"  healthy: {result['healthy']}")
        if result.get("returncode") is not None:
            print(f"  returncode: {result['returncode']}")
        print(f"  timed_out: {result['timed_out']}")
        if result.get("blocked_reason"):
            print(f"  blocked_reason: {result['blocked_reason']}")
        if result.get('stdout'):
            print(f"  stdout: {result['stdout']}")
        if result.get('stderr'):
            print(f"  stderr: {result['stderr']}")
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
