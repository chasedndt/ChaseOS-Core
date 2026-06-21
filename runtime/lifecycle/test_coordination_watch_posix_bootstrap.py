"""Tests for POSIX (systemd-user / cron) coordination-watch bootstrap plans.

Additive Linux/macOS autostart parity for the dual-boot scenario. The Windows
Task Scheduler path is covered by test_coordination_watch_bootstrap.py.

Run:
    .venv-win314/Scripts/python.exe -m pytest runtime/lifecycle/test_coordination_watch_posix_bootstrap.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # type: ignore  # noqa: E402

from runtime.lifecycle.coordination_watch_bootstrap import (  # type: ignore  # noqa: E402
    build_coordination_watch_bootstrap_plan_for_kind,
    build_posix_coordination_watch_bootstrap_plan,
)

RUNTIME = "hermes"


def test_systemd_plan_shape_and_commands():
    plan = build_posix_coordination_watch_bootstrap_plan(RUNTIME, registration_kind="systemd-user")
    assert plan["registration_kind"] == "systemd-user"
    assert plan["trigger"] == "on-login"
    assert plan["launcher_path"].endswith(".sh")
    assert plan["unit_name"] == f"chaseos-{RUNTIME}-coordination-watch.service"
    assert ".config/systemd/user" in plan["unit_file_path"]
    # Unit must reference the launcher and install to the user default target.
    assert "ExecStart=/usr/bin/env bash" in plan["unit_file_contents"]
    assert "WantedBy=default.target" in plan["unit_file_contents"]
    assert plan["register_command"] == ["systemctl", "--user", "enable", "--now", plan["unit_name"]]
    assert plan["unregister_command"] == ["systemctl", "--user", "disable", "--now", plan["unit_name"]]
    assert ["systemctl", "--user", "daemon-reload"] in plan["register_commands"]


def test_systemd_launcher_is_portable_bash():
    plan = build_posix_coordination_watch_bootstrap_plan(RUNTIME, registration_kind="systemd-user")
    launcher = plan["launcher_contents"]
    assert launcher.startswith("#!/usr/bin/env bash")
    # Resolves interpreter at runtime — not pinned to a build-host path.
    assert '.venv/bin/python' in launcher
    assert "command -v python3" in launcher
    assert f"--runtime {RUNTIME} --action start" in launcher
    # No Windows artifacts leaked in.
    assert "powershell" not in launcher.lower()
    assert ".exe" not in launcher


def test_cron_plan_idempotent_marker():
    plan = build_posix_coordination_watch_bootstrap_plan(RUNTIME, registration_kind="cron")
    assert plan["registration_kind"] == "cron"
    assert plan["trigger"] == "on-reboot"
    marker = plan["cron_marker"]
    assert marker == f"chaseos:{RUNTIME}-coordination-watch"
    assert plan["cron_line"].startswith("@reboot")
    assert marker in plan["cron_line"]
    # Register strips any prior chaseos line for this runtime before appending.
    register = plan["register_command"]
    assert register[0] == "bash"
    assert f"grep -v '{marker}'" in register[2]
    assert "| crontab -" in register[2]
    # Unregister removes the line.
    assert f"grep -v '{marker}'" in plan["unregister_command"][2]


def test_unsupported_kind_rejected():
    with pytest.raises(ValueError):
        build_posix_coordination_watch_bootstrap_plan(RUNTIME, registration_kind="initd")


def test_dispatcher_routes_windows(monkeypatch):
    import runtime.platform_support as ps

    monkeypatch.setattr(ps, "_os_name", lambda: "nt")
    plan = build_coordination_watch_bootstrap_plan_for_kind(RUNTIME)
    assert plan["registration_kind"] == "windows-task-scheduler"
    assert plan["register_command"][0] == "schtasks"


def test_dispatcher_routes_systemd(monkeypatch):
    import runtime.platform_support as ps

    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setattr(ps, "_sys_platform", lambda: "linux")
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)
    plan = build_coordination_watch_bootstrap_plan_for_kind(RUNTIME)
    assert plan["registration_kind"] == "systemd-user"


def test_dispatcher_macos_falls_back_to_cron(monkeypatch):
    import runtime.platform_support as ps

    monkeypatch.setattr(ps, "_os_name", lambda: "posix")
    monkeypatch.setattr(ps, "_sys_platform", lambda: "darwin")
    plan = build_coordination_watch_bootstrap_plan_for_kind(RUNTIME)
    assert plan["registration_kind"] == "cron"


def test_dispatcher_explicit_kind_overrides_host(monkeypatch):
    import runtime.platform_support as ps

    # Even on a Windows host, an explicit cron request must produce a cron plan
    # (supports generating Linux artifacts from the Windows boot for the other OS).
    monkeypatch.setattr(ps, "_os_name", lambda: "nt")
    plan = build_coordination_watch_bootstrap_plan_for_kind(RUNTIME, registration_kind="cron")
    assert plan["registration_kind"] == "cron"
