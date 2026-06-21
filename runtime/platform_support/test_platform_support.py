"""Tests for runtime.platform_support cross-platform host helpers.

Run:
    .venv-win314/Scripts/python.exe -m pytest runtime/platform_support/test_platform_support.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import runtime.platform_support as ps  # type: ignore  # noqa: E402


def test_current_os_windows(monkeypatch):
    monkeypatch.setattr(ps, "_os_name", lambda: "nt")
    assert ps.current_os() == ps.WINDOWS
    assert ps.is_windows() is True


def test_current_os_linux(monkeypatch):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setattr(ps, "_sys_platform", lambda: "linux")
    assert ps.current_os() == ps.LINUX
    assert ps.is_windows() is False
    assert ps.is_macos() is False


def test_current_os_macos(monkeypatch):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setattr(ps, "_sys_platform", lambda: "darwin")
    assert ps.current_os() == ps.MACOS
    assert ps.is_macos() is True


def test_is_wsl_env_var(monkeypatch):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert ps.is_wsl() is True


def test_is_wsl_false_on_windows(monkeypatch):
    monkeypatch.setattr(ps, "_os_name", lambda: "nt")
    assert ps.is_wsl() is False


def test_no_window_kwargs_empty_on_posix(monkeypatch):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    assert ps.no_window_subprocess_kwargs() == {}


def test_no_window_kwargs_sets_flags_on_windows(monkeypatch):
    monkeypatch.setattr(ps, "_os_name", lambda: "nt")
    monkeypatch.setattr(ps.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(ps.subprocess, "DETACHED_PROCESS", 0x00000008, raising=False)
    kwargs = ps.no_window_subprocess_kwargs(detached=True)
    assert int(kwargs["creationflags"]) & 0x08000000
    assert int(kwargs["creationflags"]) & 0x00000008


def test_venv_candidates_windows_order(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "_os_name", lambda: "nt")
    cands = ps.venv_python_candidates(tmp_path, prefer_gui=True)
    names = [c.name for c in cands]
    # GUI interpreter must be preferred first.
    assert names[0] == "pythonw.exe"
    assert any(c.parts[-2] == "Scripts" for c in cands)
    assert any(".venv-win314" in str(c) for c in cands)


def test_venv_candidates_posix(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    cands = ps.venv_python_candidates(tmp_path)
    assert cands[0] == tmp_path / ".venv" / "bin" / "python"


def test_project_python_executable_prefers_existing_venv(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    assert ps.project_python_executable(tmp_path) == str(venv_python.resolve())


def test_project_python_executable_falls_back_to_sys_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    assert ps.project_python_executable(tmp_path) == ps.sys.executable


def test_default_autostart_kind_windows(monkeypatch):
    monkeypatch.setattr(ps, "_os_name", lambda: "nt")
    assert ps.default_autostart_kind() == "windows-task-scheduler"


def test_default_autostart_kind_macos(monkeypatch):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setattr(ps, "_sys_platform", lambda: "darwin")
    assert ps.default_autostart_kind() == "launchd"


def test_default_autostart_kind_linux_systemd(monkeypatch):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setattr(ps, "_sys_platform", lambda: "linux")
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)
    assert ps.default_autostart_kind() == "systemd-user"


def test_default_autostart_kind_linux_cron_when_no_systemd(monkeypatch):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setattr(ps, "_sys_platform", lambda: "linux")
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert ps.default_autostart_kind() == "cron"


def test_reveal_in_file_manager_linux_uses_xdg_open(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setattr(ps, "_sys_platform", lambda: "linux")
    calls = {}

    def fake_popen(args, **kwargs):
        calls["args"] = args
        return object()

    monkeypatch.setattr(ps.subprocess, "Popen", fake_popen)
    result = ps.reveal_in_file_manager(tmp_path)
    assert result["ok"] is True
    assert result["method"] == "xdg-open"
    assert calls["args"][0] == "xdg-open"


def test_to_vault_relative_under_root(tmp_path):
    vault = tmp_path
    target = vault / "runtime" / "lifecycle" / "run" / "hermes.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    rel = ps.to_vault_relative(target, vault)
    assert rel == "runtime/lifecycle/run/hermes.json"  # POSIX separators


def test_to_vault_relative_outside_root_returns_absolute(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "elsewhere" / "log.txt"
    outside.parent.mkdir(parents=True)
    outside.write_text("x", encoding="utf-8")
    result = ps.to_vault_relative(outside, vault)
    assert result == str(outside)


def test_resolve_vault_path_roundtrip(tmp_path):
    vault = tmp_path
    target = vault / "runtime" / "lifecycle" / "run" / "hermes.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    rel = ps.to_vault_relative(target, vault)
    # Re-resolves onto the current vault root (simulating the other boot).
    resolved = ps.resolve_vault_path(rel, vault)
    assert resolved == vault / "runtime" / "lifecycle" / "run" / "hermes.json"


def test_resolve_vault_path_absolute_passthrough(tmp_path):
    abs_path = tmp_path / "abs.json"
    assert ps.resolve_vault_path(str(abs_path), tmp_path / "other") == abs_path


def test_acquire_single_instance_windows_is_noop():
    # On Windows the named-mutex path owns single-instance; helper no-ops.
    if ps._os_name() != "nt":
        return
    acquired, handle = ps.acquire_single_instance("ignored.lock")
    assert acquired is True
    assert handle is None


def test_acquire_single_instance_posix_acquires(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setattr(ps, "_posix_flock_nonblocking", lambda handle: True)
    lock = tmp_path / "studio.lock"
    acquired, handle = ps.acquire_single_instance(lock)
    try:
        assert acquired is True
        assert handle is not None
        assert lock.exists()
    finally:
        if handle is not None:
            handle.close()


def test_acquire_single_instance_posix_contended(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setattr(ps, "_posix_flock_nonblocking", lambda handle: False)
    acquired, handle = ps.acquire_single_instance(tmp_path / "studio.lock")
    assert acquired is False
    assert handle is None


def test_reveal_in_file_manager_soft_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setattr(ps, "_sys_platform", lambda: "linux")

    def boom(args, **kwargs):
        raise FileNotFoundError("no xdg-open")

    monkeypatch.setattr(ps.subprocess, "Popen", boom)
    result = ps.reveal_in_file_manager(tmp_path)
    assert result["ok"] is False
    assert "no xdg-open" in result["error"]
