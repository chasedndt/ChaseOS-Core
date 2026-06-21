from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from runtime.adapters.codex.daemon import SubprocessCodexExecutor, _build_codex_prompt, _codex_exec_command


def test_no_write_task_uses_read_only_codex_sandbox():
    prompt = "bounded task"
    task_packet = {"allowed_write_paths": []}

    command = _codex_exec_command("codex", prompt, task_packet)

    assert command[:2] == ["codex", "exec"]
    assert "--skip-git-repo-check" in command
    assert "--ephemeral" in command
    assert "--sandbox" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[-1] == "-"


def test_declared_write_paths_constrain_codex_workspace_root():
    prompt = "bounded task"
    root = Path.cwd()
    task_packet = {
        "repo_root": str(root),
        "allowed_write_paths": [
            "runtime/adapters/codex/runs",
            "runtime/adapters/codex/tmp",
        ],
    }

    command = _codex_exec_command("codex", prompt, task_packet)

    assert "--sandbox" in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert "--cd" in command
    assert command[command.index("--cd") + 1] == str((root / "runtime/adapters/codex/runs").resolve())
    assert "--add-dir" in command
    assert command[command.index("--add-dir") + 1] == str((root / "runtime/adapters/codex/tmp").resolve())
    assert command[-1] == "-"


def test_declared_write_paths_ignore_paths_outside_repo_root():
    prompt = "bounded task"
    task_packet = {
        "repo_root": str(Path.cwd()),
        "allowed_write_paths": [".."],
    }

    command = _codex_exec_command("codex", prompt, task_packet)

    assert "--sandbox" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--cd" not in command
    assert "--add-dir" not in command


def test_live_subprocess_sends_only_bounded_prompt_on_stdin(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("runtime.adapters.codex.daemon.subprocess.run", fake_run)
    root = Path(".codex_tmp_test") / f"codex-stdin-unit-{uuid.uuid4().hex}"
    run_dir = root / "run"
    run_dir.mkdir(parents=True)
    packet = {
        "task_id": "task-stdin",
        "run_id": "run-stdin",
        "repo_root": str(Path.cwd()),
        "allowed_write_paths": ["runtime/adapters/codex/runs"],
        "allow_shell_commands": True,
        "allow_live_subprocess": True,
    }

    try:
        result = SubprocessCodexExecutor(codex_binary=sys.executable).run(packet, run_dir=run_dir)

        assert result["event_type"] == "proposal"
        assert captured["kwargs"]["input"].startswith("You are Codex joining the ChaseOS agent bus")
        assert "Bounded task packet:" in captured["kwargs"]["input"]
        assert captured["command"][-1] == "-"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_write_prompt_blocks_chaseos_writeback_surfaces():
    prompt = _build_codex_prompt(
        {
            "request": "Return a read-only bus summary.",
            "expected_output": "Text only.",
            "allowed_write_paths": [],
        },
        Path("runtime/adapters/codex/runs/example/codex-task-packet.json"),
    )

    assert "allowed_write_paths" in prompt
    assert "hard no-write" in prompt
    assert "build-log/daily/archive/index writeback" in prompt
    assert "Return a read-only bus summary." in prompt
    assert "Text only." in prompt


def test_prompt_records_shell_command_allowance_false():
    prompt = _build_codex_prompt(
        {"allowed_write_paths": [], "allow_shell_commands": False},
        Path("runtime/adapters/codex/runs/example/codex-task-packet.json"),
    )

    assert "Shell command allowance: false" in prompt


def test_prompt_records_live_subprocess_allowance_false():
    prompt = _build_codex_prompt(
        {"allowed_write_paths": [], "allow_live_subprocess": False},
        Path("runtime/adapters/codex/runs/example/codex-task-packet.json"),
    )

    assert "Live subprocess allowance: false" in prompt


def test_no_shell_packet_blocks_before_subprocess_spawn(monkeypatch):
    def fail_if_spawned(*args, **kwargs):  # pragma: no cover - called only on regression
        raise AssertionError("subprocess.run must not be called for no-shell packets")

    monkeypatch.setattr("runtime.adapters.codex.daemon.subprocess.run", fail_if_spawned)
    root = Path(".codex_tmp_test") / f"codex-no-shell-policy-unit-{uuid.uuid4().hex}"
    run_dir = root / "run"
    run_dir.mkdir(parents=True)
    packet = {
        "task_id": "task-no-shell",
        "run_id": "run-no-shell",
        "repo_root": str(Path.cwd()),
        "allowed_write_paths": [],
        "allow_shell_commands": False,
    }

    try:
        result = SubprocessCodexExecutor(codex_binary="definitely-missing-codex-bin").run(packet, run_dir=run_dir)

        assert result["event_type"] == "blocked"
        assert "allow_shell_commands=false" in result["message"]
        assert result["artifacts"][0]["path"].endswith("run/codex-shell-policy-block.md")
        assert (run_dir / "codex-shell-policy-block.md").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_live_subprocess_packet_blocks_before_subprocess_spawn(monkeypatch):
    def fail_if_spawned(*args, **kwargs):  # pragma: no cover - called only on regression
        raise AssertionError("subprocess.run must not be called for no-live-subprocess packets")

    monkeypatch.setattr("runtime.adapters.codex.daemon.subprocess.run", fail_if_spawned)
    root = Path(".codex_tmp_test") / f"codex-no-live-policy-unit-{uuid.uuid4().hex}"
    run_dir = root / "run"
    run_dir.mkdir(parents=True)
    packet = {
        "task_id": "task-no-live",
        "run_id": "run-no-live",
        "repo_root": str(Path.cwd()),
        "allowed_write_paths": [],
        "allow_shell_commands": True,
        "allow_live_subprocess": False,
    }

    try:
        result = SubprocessCodexExecutor(codex_binary="definitely-missing-codex-bin").run(packet, run_dir=run_dir)

        assert result["event_type"] == "blocked"
        assert "allow_live_subprocess=false" in result["message"]
        assert result["artifacts"][0]["path"].endswith("run/codex-live-subprocess-policy-block.md")
        assert (run_dir / "codex-live-subprocess-policy-block.md").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)
