"""coordination-watch supervisor lifecycle tests for ChaseOS.

Verifies:
- lifecycle records expose coordination_watch supervision configuration
- plan generation resolves the promoted ChaseOS command surface
- start/status/stop use bounded local state files for supervision

Run:
    .venv/Scripts/python.exe -m pytest runtime/lifecycle/test_coordination_watch_supervisor.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.lifecycle.coordination_watch_supervisor import (  # type: ignore
    build_supervised_coordination_watch_plan,
    cleanup_stale_supervised_coordination_watch,
    get_supervised_coordination_watch_status,
    load_coordination_watch_supervision_config,
    start_supervised_coordination_watch,
    stop_supervised_coordination_watch,
)


def test_openclaw_lifecycle_record_declares_coordination_watch_supervision_defaults():
    config = load_coordination_watch_supervision_config("openclaw")

    assert config["watch_enabled"] is True
    assert config["supervision_enabled"] is True
    assert config["restart_policy"] == "manual"
    assert config["state_file"].endswith("openclaw-coordination-watch.json")


def test_build_supervised_plan_uses_runtime_daemon_command_for_chat_dispatch():
    plan = build_supervised_coordination_watch_plan("hermes")

    assert plan["runtime_id"] == "hermes"
    assert plan["interval_seconds"] == 30
    assert plan["command"][2:5] == ["runtime", "daemon", "--runtime"]
    assert plan["command"][5] == "hermes"
    assert "--daemon-interval" in plan["command"]
    assert "--interval" not in plan["command"]
    assert "--synthesize" in plan["command"]
    assert plan["synthesize"] is True
    assert plan["claim_next"] is True


def test_openclaw_supervised_plan_uses_daemon_without_synthesis():
    plan = build_supervised_coordination_watch_plan("openclaw")

    assert plan["runtime_id"] == "openclaw"
    assert plan["command"][2:5] == ["runtime", "daemon", "--runtime"]
    assert plan["command"][5] == "openclaw"
    assert "--daemon-interval" in plan["command"]
    assert "--interval" not in plan["command"]
    assert "--synthesize" not in plan["command"]
    assert plan["synthesize"] is False


def test_windows_hosted_supervisor_plan_uses_repo_windows_venv_from_wsl():
    hermes_plan = build_supervised_coordination_watch_plan("hermes")
    openclaw_plan = build_supervised_coordination_watch_plan("openclaw")

    hermes_python = hermes_plan["command"][0].replace("\\", "/")
    openclaw_python = openclaw_plan["command"][0].replace("\\", "/")

    assert hermes_plan["supervisor_host"] == "windows"
    assert hermes_python.endswith(("/.venv/Scripts/pythonw.exe", "/.venv-win314/Scripts/pythonw.exe", "/.venv-win/Scripts/pythonw.exe", "/.venv/Scripts/python.exe", "/.venv-win314/Scripts/python.exe", "/.venv-win/Scripts/python.exe"))
    assert openclaw_plan["platform"] == "windows"
    assert openclaw_python.endswith(("/.venv/Scripts/pythonw.exe", "/.venv-win314/Scripts/pythonw.exe", "/.venv-win/Scripts/pythonw.exe", "/.venv/Scripts/python.exe", "/.venv-win314/Scripts/python.exe", "/.venv-win/Scripts/python.exe"))
    assert "uv" not in hermes_python.lower()
    assert "uv" not in openclaw_python.lower()


def test_windows_hosted_supervisor_python_prefers_existing_versioned_venv(monkeypatch):
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    fake_root = supervisor.Path("C:/Vault")
    monkeypatch.setattr(supervisor, "ROOT", fake_root)
    monkeypatch.setattr(supervisor.Path, "exists", lambda self: str(self).replace("\\", "/").endswith(".venv-win314/Scripts/pythonw.exe"))

    resolved = supervisor._coordination_watch_python_executable("windows").replace("\\", "/")

    assert resolved.endswith("/.venv-win314/Scripts/pythonw.exe")


def test_pid_running_windows_platform_from_wsl_uses_windows_process_probe(monkeypatch):
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    calls: list[tuple[list[str], int | None]] = []

    class Result:
        returncode = 0
        stdout = "Image Name                     PID Session Name        Session#    Mem Usage\npython.exe                   23036 Console                    1     10,000 K"
        stderr = ""

    def fake_run(command, capture_output=True, text=True, check=False, timeout=None, **kwargs):
        calls.append((command, timeout))
        return Result()

    monkeypatch.setattr(supervisor.os, "name", "posix", raising=False)
    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    assert supervisor._pid_running(23036, platform="windows") is True
    assert calls == [(["tasklist.exe", "/FI", "PID eq 23036"], 5)]


def test_pid_running_tasklist_times_out_instead_of_hanging(monkeypatch):
    import subprocess
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    calls: list[tuple[list[str], int | None]] = []

    def fake_run(command, capture_output=True, text=True, check=False, timeout=None, **kwargs):
        calls.append((command, timeout))
        raise subprocess.TimeoutExpired(command, timeout or 0)

    monkeypatch.setattr(supervisor.os, "name", "posix", raising=False)
    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    assert supervisor._pid_running(23036, platform="windows") is False
    assert calls[0] == (["tasklist.exe", "/FI", "PID eq 23036"], 5)
    assert calls[1][0][0] == "powershell.exe"
    assert calls[1][1] == 5


def test_passive_port_status_uses_socket_without_subprocess(monkeypatch):
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    monkeypatch.setattr(
        supervisor,
        "get_runtime_registry_entry",
        lambda runtime_id: {
            "runtime_id": runtime_id,
            "gateway_port": 18789,
            "os_boundary": "windows",
            "dashboard_url": "http://127.0.0.1:18789/",
            "runtime_process_markers": ["openclaw"],
        },
    )
    monkeypatch.setattr(supervisor, "_socket_port_listening", lambda port: True)

    def fail_if_subprocess(command, timeout=5):
        raise AssertionError(f"passive status must not spawn subprocess: {command}")

    monkeypatch.setattr(supervisor, "_run_port_command", fail_if_subprocess)

    status = supervisor.get_port_ownership_status("openclaw", require_owner=False)

    assert status["checked"] is True
    assert status["listening"] is True
    assert status["belongs_to_runtime"] is True
    assert status["conflict"] is False
    assert status["probe_method"] == "socket_no_subprocess"
    assert status["ownership_probe_skipped"] is True


def test_terminate_windows_platform_from_wsl_uses_taskkill_exe(monkeypatch):
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    calls: list[list[str]] = []

    def fake_run(command, capture_output=True, text=True, check=False, **kwargs):
        calls.append(command)

    monkeypatch.setattr(supervisor.os, "name", "posix", raising=False)
    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    supervisor._terminate_pid(9872, platform="windows", force=True)

    assert calls == [["taskkill.exe", "/PID", "9872", "/T", "/F"]]


def test_passive_supervised_status_does_not_probe_pid_or_spawn_port_commands(monkeypatch, tmp_path):
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    state_file = tmp_path / "hermes-watch.json"
    state_file.write_text(
        json.dumps({"pid": 1234, "status": "running", "started_at": "2026-06-14T00:00:00Z"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        supervisor,
        "build_supervised_coordination_watch_plan",
        lambda runtime_id: {
            "runtime_id": runtime_id,
            "runtime_name": "Hermes",
            "supervision_enabled": True,
            "watch_enabled": True,
            "autostart": True,
            "restart_policy": "manual",
            "interval_seconds": 30,
            "claim_next": True,
            "stale_after_seconds": 900,
            "platform": "windows",
            "runtime_platform": "wsl",
            "supervisor_host": "windows",
            "wsl_distro": "Ubuntu",
            "state_file": str(state_file),
            "log_file": str(tmp_path / "hermes-watch.log"),
            "command": ["pythonw.exe", "-m", "runtime.cli.main"],
        },
    )
    monkeypatch.setattr(supervisor, "read_runtime_lock", lambda runtime_id: {"present": False})
    monkeypatch.setattr(supervisor, "get_maintenance_mode_status", lambda runtime_id: {"active": False})
    monkeypatch.setattr(
        supervisor,
        "get_port_ownership_status",
        lambda runtime_id, require_owner=False: {
            "checked": True,
            "port": 9119,
            "listening": True,
            "conflict": False,
            "probe_method": "socket_no_subprocess",
        },
    )

    def fail_pid_probe(*args, **kwargs):
        raise AssertionError("passive Studio status must not probe process liveness")

    monkeypatch.setattr(supervisor, "_pid_running", fail_pid_probe)

    status = supervisor._get_supervised_coordination_watch_status_live("hermes")

    assert status["running"] is True
    assert status["process_probe_skipped"] is True
    assert status["process_probe_reason"] == "passive_status_no_subprocess"


def test_start_supervised_coordination_watch_writes_state_file(monkeypatch, tmp_path):
    state_file = tmp_path / "hermes-watch.json"
    log_file = tmp_path / "hermes-watch.log"

    plan = {
        "runtime_id": "hermes",
        "runtime_name": "Hermes",
        "watch_enabled": True,
        "supervision_enabled": True,
        "autostart": False,
        "restart_policy": "manual",
        "interval_seconds": 30,
        "claim_next": False,
        "stale_after_seconds": 900,
        "state_file": str(state_file),
        "log_file": str(log_file),
        "command": [sys.executable, "fake-chaseos.py", "runtime", "coordination-watch"],
    }

    class FakeProcess:
        pid = 4321

    captured: dict[str, object] = {}

    def fake_build(runtime_id: str, *, interval_seconds=None):
        captured["runtime_id"] = runtime_id
        captured["interval_seconds"] = interval_seconds
        return dict(plan)

    def fake_status(runtime_id: str):
        return {
            "action": "status",
            "runtime_id": runtime_id,
            "running": False,
            "state_present": False,
        }

    def fake_popen(command, cwd=None, stdout=None, stderr=None, start_new_session=None, **kwargs):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["start_new_session"] = start_new_session
        captured["creationflags"] = kwargs.get("creationflags")
        return FakeProcess()

    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.build_supervised_coordination_watch_plan", fake_build)
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_supervised_coordination_watch_status", fake_status)
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_maintenance_mode_status", lambda runtime_id: {"active": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_port_ownership_status", lambda runtime_id: {"checked": True, "conflict": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.read_runtime_lock", lambda runtime_id: {"present": False, "fresh": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.write_runtime_lock", lambda runtime_id, **kwargs: None)
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor._append_supervisor_event", lambda payload: None)
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.subprocess.Popen", fake_popen)

    result = start_supervised_coordination_watch("hermes")

    assert result["started"] is True
    assert result["pid"] == 4321
    assert captured["runtime_id"] == "hermes"
    assert captured["cwd"] is not None
    assert state_file.exists()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["pid"] == 4321
    assert state["runtime_id"] == "hermes"


def test_supervised_coordination_watch_status_reports_running_from_state_file(monkeypatch, tmp_path):
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    supervisor._status_cache.clear()
    state_file = tmp_path / "openclaw-watch.json"
    log_file = tmp_path / "openclaw-watch.log"
    state_file.write_text(
        json.dumps(
            {
                "runtime_id": "openclaw",
                "pid": 9876,
                "started_at": "2026-04-25T05:00:00Z",
                "last_action": "start",
                "status": "running",
            }
        ),
        encoding="utf-8",
    )

    def fake_build(runtime_id: str, *, interval_seconds=None):
        return {
            "runtime_id": runtime_id,
            "runtime_name": "OpenClaw",
            "watch_enabled": True,
            "supervision_enabled": True,
            "autostart": True,
            "restart_policy": "manual",
            "interval_seconds": 30,
            "claim_next": True,
            "stale_after_seconds": 900,
            "state_file": str(state_file),
            "log_file": str(log_file),
            "command": [sys.executable, "chaseos.py", "runtime", "coordination-watch"],
        }

    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.build_supervised_coordination_watch_plan", fake_build)
    monkeypatch.setattr(
        "runtime.lifecycle.coordination_watch_supervisor.get_port_ownership_status",
        lambda runtime_id, require_owner=False: {"checked": True, "listening": True, "conflict": False},
    )
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor._pid_running", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("passive status must not probe pid")))

    result = get_supervised_coordination_watch_status("openclaw")

    assert result["running"] is True
    assert result["process_probe_skipped"] is True
    assert result["pid"] == 9876
    assert result["autostart"] is True


def test_supervised_coordination_watch_done_state_is_not_stale(monkeypatch, tmp_path):
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    supervisor._status_cache.clear()
    state_file = tmp_path / "openclaw-watch.json"
    log_file = tmp_path / "openclaw-watch.log"
    state_file.write_text(
        json.dumps(
            {
                "runtime_id": "openclaw",
                "pid": 999999,
                "started_at": "2026-05-20T23:15:15Z",
                "ended_at": "2026-05-20T23:15:16Z",
                "status": "done",
            }
        ),
        encoding="utf-8",
    )

    def fake_build(runtime_id: str, *, interval_seconds=None):
        return {
            "runtime_id": runtime_id,
            "runtime_name": "OpenClaw",
            "watch_enabled": True,
            "supervision_enabled": True,
            "autostart": True,
            "restart_policy": "manual",
            "interval_seconds": 30,
            "claim_next": True,
            "stale_after_seconds": 900,
            "state_file": str(state_file),
            "log_file": str(log_file),
            "command": [sys.executable, "chaseos.py", "runtime", "coordination-watch"],
        }

    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.build_supervised_coordination_watch_plan", fake_build)
    monkeypatch.setattr(
        "runtime.lifecycle.coordination_watch_supervisor._pid_running",
        lambda pid, **kwargs: (_ for _ in ()).throw(AssertionError("completed state must not probe pid")),
    )

    result = get_supervised_coordination_watch_status("openclaw")

    assert result["running"] is False
    assert result["completed_state"] is True
    assert result["stale_state"] is False
    assert result["state_status"] == "done"


def test_pid_running_windows_falls_back_when_tasklist_access_denied(monkeypatch):
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    calls: list[tuple[list[str], int | None]] = []

    class Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, capture_output=True, text=True, check=False, timeout=None, **kwargs):
        calls.append((command, timeout))
        if command[0] == "tasklist":
            return Result(1, stderr="ERROR: Access denied")
        return Result(0)

    monkeypatch.setattr(supervisor.os, "name", "nt", raising=False)
    monkeypatch.setattr(supervisor, "_pid_running_windows_api", lambda pid: None)
    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    assert supervisor._pid_running(52104) is True
    assert calls[0][0][0] == "tasklist"
    assert calls[0][1] == 5
    assert calls[1][0][0] == "powershell.exe"
    assert calls[1][1] == 5


def test_pid_running_windows_uses_wsl_for_wsl_lifecycle_records(monkeypatch):
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, capture_output=True, text=True, check=False, timeout=None, **kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr(supervisor.os, "name", "nt", raising=False)
    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    assert supervisor._pid_running(427, platform="wsl", wsl_distro="Ubuntu") is True
    assert calls == [["wsl.exe", "-d", "Ubuntu", "--", "bash", "-lc", "ps -p 427 -o pid= >/dev/null"]]


def test_terminate_pid_wsl_passes_no_window_creationflags(monkeypatch):
    """_terminate_pid must hide the wsl.exe kill console (terminal-spam guard)."""
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["creationflags"] = kwargs.get("creationflags")
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(supervisor.os, "name", "nt", raising=False)
    monkeypatch.setattr(supervisor.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    supervisor._terminate_pid(427, force=True, platform="wsl", wsl_distro="Ubuntu")

    assert captured["command"][0] == "wsl.exe"
    assert int(captured["creationflags"]) & 0x08000000


def test_terminate_pid_windows_taskkill_passes_no_window_creationflags(monkeypatch):
    """_terminate_pid must hide the taskkill console (terminal-spam guard)."""
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["creationflags"] = kwargs.get("creationflags")
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(supervisor.os, "name", "nt", raising=False)
    monkeypatch.setattr(supervisor.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    supervisor._terminate_pid(2468, force=True, platform="windows")

    assert captured["command"][0] == "taskkill"
    assert int(captured["creationflags"]) & 0x08000000


def test_stop_supervised_coordination_watch_kills_pid_and_clears_state(monkeypatch, tmp_path):
    state_file = tmp_path / "hermes-watch.json"
    state_file.write_text(json.dumps({"runtime_id": "hermes", "pid": 2468}), encoding="utf-8")

    def fake_status(runtime_id: str):
        return {
            "action": "status",
            "runtime_id": runtime_id,
            "runtime_name": "Hermes",
            "running": True,
            "state_present": True,
            "pid": 2468,
            "state_file": str(state_file),
            "log_file": str(tmp_path / "hermes-watch.log"),
        }

    kill_calls: list[tuple[int, int]] = []
    running_checks = iter([False, False])

    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_supervised_coordination_watch_status", fake_status)
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_maintenance_mode_status", lambda runtime_id: {"active": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.clear_runtime_lock", lambda runtime_id: None)
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor._append_supervisor_event", lambda payload: None)
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor._terminate_pid", lambda pid, force=False, **kwargs: kill_calls.append((pid, force)))
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor._pid_running", lambda pid, **kwargs: next(running_checks))

    result = stop_supervised_coordination_watch("hermes")

    assert result["stopped"] is True
    assert result["forced"] is False
    assert kill_calls == [(2468, False)]
    assert not state_file.exists()



def test_start_supervisor_skips_when_maintenance_mode_active(monkeypatch):
    def fake_build(runtime_id: str, *, interval_seconds=None):
        return {
            "runtime_id": runtime_id,
            "runtime_name": "OpenClaw",
            "watch_enabled": True,
            "supervision_enabled": True,
            "interval_seconds": 30,
            "state_file": "unused.json",
            "log_file": "unused.log",
            "command": ["python", "chaseos.py"],
        }

    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.build_supervised_coordination_watch_plan", fake_build)
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_maintenance_mode_status", lambda runtime_id: {"active": True, "reason": "manual repair"})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_port_ownership_status", lambda runtime_id: {"checked": True, "conflict": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.read_runtime_lock", lambda runtime_id: {"present": False, "fresh": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor._append_supervisor_event", lambda payload: None)

    result = start_supervised_coordination_watch("openclaw")

    assert result["started"] is False
    assert result["skipped"] is True
    assert result["skipped_reason"] == "maintenance_mode_active"


def test_start_supervisor_refuses_non_runtime_port_conflict(monkeypatch):
    def fake_build(runtime_id: str, *, interval_seconds=None):
        return {
            "runtime_id": runtime_id,
            "runtime_name": "OpenClaw",
            "watch_enabled": True,
            "supervision_enabled": True,
            "interval_seconds": 30,
            "state_file": "unused.json",
            "log_file": "unused.log",
            "command": ["python", "chaseos.py"],
        }

    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.build_supervised_coordination_watch_plan", fake_build)
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_maintenance_mode_status", lambda runtime_id: {"active": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_port_ownership_status", lambda runtime_id: {"checked": True, "port": 18789, "conflict": True, "pid": 999})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.read_runtime_lock", lambda runtime_id: {"present": False, "fresh": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor._append_supervisor_event", lambda payload: None)

    result = start_supervised_coordination_watch("openclaw")

    assert result["started"] is False
    assert result["skipped_reason"] == "port_conflict"


def test_start_supervisor_dry_run_does_not_spawn(monkeypatch, tmp_path):
    def fake_build(runtime_id: str, *, interval_seconds=None):
        return {
            "runtime_id": runtime_id,
            "runtime_name": "Hermes",
            "watch_enabled": True,
            "supervision_enabled": True,
            "interval_seconds": 30,
            "state_file": str(tmp_path / "state.json"),
            "log_file": str(tmp_path / "watch.log"),
            "command": ["python", "chaseos.py"],
        }

    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.build_supervised_coordination_watch_plan", fake_build)
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_maintenance_mode_status", lambda runtime_id: {"active": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_port_ownership_status", lambda runtime_id: {"checked": True, "conflict": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.read_runtime_lock", lambda runtime_id: {"present": False, "fresh": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_supervised_coordination_watch_status", lambda runtime_id: {"running": False, "state_present": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor._append_supervisor_event", lambda payload: None)
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.subprocess.Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dry-run spawned process")))

    result = start_supervised_coordination_watch("hermes", dry_run=True)

    assert result["dry_run"] is True
    assert result["would_start"] is True
    assert not (tmp_path / "state.json").exists()

def test_cleanup_stale_supervisor_dry_run_reports_without_deleting(monkeypatch, tmp_path):
    state_file = tmp_path / "openclaw-watch.json"
    lock_file = tmp_path / "openclaw.lock"
    state_file.write_text(json.dumps({"runtime_id": "openclaw", "pid": 111}), encoding="utf-8")
    lock_file.write_text(json.dumps({"runtime_id": "openclaw"}), encoding="utf-8")

    monkeypatch.setattr(
        "runtime.lifecycle.coordination_watch_supervisor.get_supervised_coordination_watch_status",
        lambda runtime_id: {
            "runtime_id": runtime_id,
            "runtime_name": "OpenClaw",
            "running": False,
            "state_present": True,
            "stale_state": True,
            "state_file": str(state_file),
            "log_file": str(tmp_path / "openclaw-watch.log"),
            "port_status": {"checked": True, "port": 18789, "listening": True, "belongs_to_runtime": True},
        },
    )
    monkeypatch.setattr(
        "runtime.lifecycle.coordination_watch_supervisor.read_runtime_lock",
        lambda runtime_id: {"path": str(lock_file), "present": True, "fresh": False},
    )
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_maintenance_mode_status", lambda runtime_id: {"active": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor._append_supervisor_event", lambda payload: None)

    result = cleanup_stale_supervised_coordination_watch("openclaw", dry_run=True)

    assert result["dry_run"] is True
    assert result["would_clean"] == [str(state_file), str(lock_file)]
    assert state_file.exists()
    assert lock_file.exists()


def test_cleanup_stale_supervisor_removes_only_stale_state_and_lock(monkeypatch, tmp_path):
    state_file = tmp_path / "hermes-watch.json"
    lock_file = tmp_path / "hermes.lock"
    log_file = tmp_path / "hermes-watch.log"
    state_file.write_text(json.dumps({"runtime_id": "hermes", "pid": 222}), encoding="utf-8")
    lock_file.write_text(json.dumps({"runtime_id": "hermes"}), encoding="utf-8")
    log_file.write_text("historical log must remain", encoding="utf-8")

    monkeypatch.setattr(
        "runtime.lifecycle.coordination_watch_supervisor.get_supervised_coordination_watch_status",
        lambda runtime_id: {
            "runtime_id": runtime_id,
            "runtime_name": "Hermes",
            "running": False,
            "state_present": True,
            "stale_state": True,
            "state_file": str(state_file),
            "log_file": str(log_file),
            "port_status": {"checked": True, "port": 9119, "listening": False},
        },
    )
    monkeypatch.setattr(
        "runtime.lifecycle.coordination_watch_supervisor.read_runtime_lock",
        lambda runtime_id: {"path": str(lock_file), "present": True, "fresh": False},
    )
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_maintenance_mode_status", lambda runtime_id: {"active": True, "reason": "manual repair"})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor._append_supervisor_event", lambda payload: None)

    result = cleanup_stale_supervised_coordination_watch("hermes")

    assert result["cleaned"] == [str(state_file), str(lock_file)]
    assert not state_file.exists()
    assert not lock_file.exists()
    assert log_file.exists()
    assert "maintenance_mode" in result["left_untouched"]


def test_cleanup_stale_supervisor_skips_live_state(monkeypatch, tmp_path):
    state_file = tmp_path / "openclaw-watch.json"
    state_file.write_text(json.dumps({"runtime_id": "openclaw", "pid": 333}), encoding="utf-8")

    monkeypatch.setattr(
        "runtime.lifecycle.coordination_watch_supervisor.get_supervised_coordination_watch_status",
        lambda runtime_id: {
            "runtime_id": runtime_id,
            "runtime_name": "OpenClaw",
            "running": True,
            "state_present": True,
            "state_file": str(state_file),
            "port_status": {"checked": True, "port": 18789, "listening": True, "belongs_to_runtime": True},
        },
    )
    monkeypatch.setattr(
        "runtime.lifecycle.coordination_watch_supervisor.read_runtime_lock",
        lambda runtime_id: {"path": str(tmp_path / "openclaw.lock"), "present": False, "fresh": False},
    )
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor.get_maintenance_mode_status", lambda runtime_id: {"active": False})
    monkeypatch.setattr("runtime.lifecycle.coordination_watch_supervisor._append_supervisor_event", lambda payload: None)

    result = cleanup_stale_supervised_coordination_watch("openclaw")

    assert result["skipped"] is True
    assert result["skipped_reason"] == "no_stale_supervisor_state"
    assert state_file.exists()

def test_maintenance_status_reads_legacy_runtime_marker(monkeypatch, tmp_path):
    import runtime.lifecycle.coordination_watch_supervisor as supervisor

    canonical = tmp_path / "maintenance-mode.json"
    canonical.write_text(json.dumps({"active": False, "all_runtimes": False, "runtimes": {}}), encoding="utf-8")
    legacy = tmp_path / "maintenance-openclaw-watch.json"
    legacy.write_text(json.dumps({"maintenanceMode": True, "scope": "openclaw", "reason": "manual repair"}), encoding="utf-8")

    monkeypatch.setattr(supervisor, "DEFAULT_MAINTENANCE_PATH", canonical)

    status = supervisor.get_maintenance_mode_status("openclaw")

    assert status["active"] is True
    assert status["reason"] == "manual repair"
    assert status["legacy_markers"][0]["path"] == str(legacy)
