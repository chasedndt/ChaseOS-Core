"""
test_agent_bus_cli.py — ChaseOS agent-bus promoted watch CLI tests

Verifies:
- watch command defaults to one-shot summary mode
- watch command can enter interval loop mode
- interval mode forwards runtime/claim-next/stale-after settings to run_watch_loop()

Run:
    .venv/Scripts/python.exe -m pytest runtime/agent_bus/test_agent_bus_cli.py -q
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.agent_bus import cli



def test_agent_bus_cli_script_bootstraps_repo_root():
    cli_path = Path(__file__).resolve().parent / "cli.py"
    completed = subprocess.run(
        [sys.executable, str(cli_path), "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )

    assert completed.returncode == 0
    assert "ChaseOS agent-bus CLI" in completed.stdout


def test_cmd_discord_ingress_translation_forwards_coordination_request(monkeypatch):
    captured: dict[str, object] = {}

    def fake_translate(root, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return {"translated": True, "created": True, "task_id": "task-ops"}

    def fake_print_json(payload):
        captured["payload"] = payload
        return 0

    monkeypatch.setattr(cli.bus, "translate_discord_control_plane_request", fake_translate)
    monkeypatch.setattr(cli, "_print_json", fake_print_json)

    args = argparse.Namespace(
        to="OpenClaw",
        intent="TASK",
        priority="high",
        request="Coordinate review execution",
        expected_output="Structured review result",
        notes="from chaseos-ops",
        source_channel_id="1493226873080119397",
        source_thread_id="1496197360382906398",
        source_channel_class=None,
        origin_message_id="1497000000000000001",
        control_plane_route=None,
        work_fingerprint=None,
        coordination_sensitive=True,
    )

    result = cli.cmd_ingress_discord(args)

    assert result == 0
    assert captured["recipient"] == "OpenClaw"
    assert captured["coordination_sensitive"] is True
    assert captured["source_channel_id"] == "1493226873080119397"
    assert captured["payload"] == {"translated": True, "created": True, "task_id": "task-ops"}


def test_cmd_task_create_forwards_ingress_context_and_fingerprint(monkeypatch):
    captured: dict[str, object] = {}

    def fake_create_task(root, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return {"created": True, "task_id": "task-123"}

    def fake_print_json(payload):
        captured["payload"] = payload
        return 0

    monkeypatch.setattr(cli.bus, "create_task", fake_create_task)
    monkeypatch.setattr(cli, "_print_json", fake_print_json)

    args = argparse.Namespace(
        sender="Hermes",
        to="OpenClaw",
        intent="TASK",
        priority="normal",
        request="Do the work",
        expected_output="Return result",
        notes="lane-aware",
        source_platform="discord",
        source_channel_id="1493226848409358426",
        source_thread_id="1496197360382906398",
        source_channel_class="runtime-chat",
        conversation_key="discord:1493226848409358426:1496197360382906398",
        origin_message_id="1497000000000000001",
        control_plane_route="discord:1493226848409358426:1496197360382906398",
        work_fingerprint="discord-shared-work-001",
        no_shell_commands=True,
        no_live_subprocess=True,
        write_policy="none",
        allowed_write_path=None,
    )

    result = cli.cmd_task_create(args)

    assert result == 0
    assert captured["sender"] == "Hermes"
    assert captured["recipient"] == "OpenClaw"
    assert captured["ingress_context"] == {
        "source_platform": "discord",
        "source_channel_id": "1493226848409358426",
        "source_thread_id": "1496197360382906398",
        "source_channel_class": "runtime-chat",
        "conversation_key": "discord:1493226848409358426:1496197360382906398",
        "origin_message_id": "1497000000000000001",
        "control_plane_route": "discord:1493226848409358426:1496197360382906398",
    }
    assert captured["work_fingerprint"] == "discord-shared-work-001"
    assert captured["execution_constraints"] == {
        "allow_shell_commands": False,
        "allow_live_subprocess": False,
        "write_policy": "none",
    }
    assert captured["payload"] == {"created": True, "task_id": "task-123"}


def test_cmd_task_cleanup_forwards_filters_and_apply(monkeypatch):
    captured: dict[str, object] = {}

    def fake_cleanup_tasks(root, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return {"matched_count": 2, "updated_count": 2, "updated_task_ids": ["task-a", "task-b"]}

    def fake_print_json(payload):
        captured["payload"] = payload
        return 0

    monkeypatch.setattr(cli.bus, "cleanup_tasks", fake_cleanup_tasks)
    monkeypatch.setattr(cli, "_print_json", fake_print_json)

    args = argparse.Namespace(
        runtime="Hermes",
        to="OpenClaw",
        sender="Hermes",
        owner=None,
        status="open",
        request_exact="test",
        request_contains=None,
        updated_before=None,
        work_fingerprint="discord:OpenClaw:message-a",
        conversation_key="discord:ops:thread-a",
        origin_message_id="message-a",
        limit=5,
        reason="Queue hygiene cleanup",
        apply=True,
    )

    result = cli.cmd_task_cleanup(args)

    assert result == 0
    assert captured["runtime"] == "Hermes"
    assert captured["recipient"] == "OpenClaw"
    assert captured["sender"] == "Hermes"
    assert captured["status"] == "open"
    assert captured["request_exact"] == "test"
    assert captured["work_fingerprint"] == "discord:OpenClaw:message-a"
    assert captured["conversation_key"] == "discord:ops:thread-a"
    assert captured["origin_message_id"] == "message-a"
    assert captured["limit"] == 5
    assert captured["apply"] is True
    assert captured["payload"]["updated_count"] == 2


def test_cmd_watch_once_uses_watch_once(monkeypatch):
    captured: dict[str, object] = {}

    def fake_watch_once(
        root,
        *,
        runtime,
        claim_next=False,
        stale_after_seconds=None,
        runtime_instance_id=None,
        control_surface=None,
        control_surface_key=None,
    ):
        captured["root"] = root
        captured["runtime"] = runtime
        captured["claim_next"] = claim_next
        captured["stale_after_seconds"] = stale_after_seconds
        captured["runtime_instance_id"] = runtime_instance_id
        captured["control_surface"] = control_surface
        captured["control_surface_key"] = control_surface_key
        return {"runtime": runtime, "open_task_count": 2}

    def fake_print_json(payload):
        captured["payload"] = payload
        return 0

    monkeypatch.setattr(cli.bus, "watch_once", fake_watch_once)
    monkeypatch.setattr(cli, "_print_json", fake_print_json)

    args = argparse.Namespace(
        runtime="Hermes",
        claim_next=True,
        stale_after_seconds=120,
        once=True,
        interval=None,
    )

    result = cli.cmd_watch(args)

    assert result == 0
    assert captured["runtime"] == "Hermes"
    assert captured["claim_next"] is True
    assert captured["stale_after_seconds"] == 120
    assert captured["payload"] == {"runtime": "Hermes", "open_task_count": 2}


def test_cmd_watch_interval_uses_run_watch_loop(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_watch_loop(
        root,
        *,
        runtime,
        interval_seconds,
        claim_next=False,
        stale_after_seconds=None,
        runtime_instance_id=None,
        control_surface=None,
        control_surface_key=None,
    ):
        captured["root"] = root
        captured["runtime"] = runtime
        captured["interval_seconds"] = interval_seconds
        captured["claim_next"] = claim_next
        captured["stale_after_seconds"] = stale_after_seconds
        captured["runtime_instance_id"] = runtime_instance_id
        captured["control_surface"] = control_surface
        captured["control_surface_key"] = control_surface_key

    monkeypatch.setattr(cli.bus, "run_watch_loop", fake_run_watch_loop)

    args = argparse.Namespace(
        runtime="OpenClaw",
        claim_next=True,
        stale_after_seconds=300,
        once=False,
        interval=15,
    )

    result = cli.cmd_watch(args)

    assert result == 0
    assert captured["runtime"] == "OpenClaw"
    assert captured["interval_seconds"] == 15
    assert captured["claim_next"] is True
    assert captured["stale_after_seconds"] == 300


def test_cmd_watch_once_forwards_instance_scope(monkeypatch):
    captured: dict[str, object] = {}

    def fake_watch_once(root, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return {"runtime": kwargs["runtime"], "open_task_count": 2}

    def fake_print_json(payload):
        captured["payload"] = payload
        return 0

    monkeypatch.setattr(cli.bus, "watch_once", fake_watch_once)
    monkeypatch.setattr(cli, "_print_json", fake_print_json)

    args = argparse.Namespace(
        runtime="Hermes",
        claim_next=True,
        stale_after_seconds=120,
        once=True,
        interval=None,
        runtime_instance_id="discord-thread-1496197360382906398",
        control_surface="discord",
        control_surface_key="discord:1493226848409358426:1496197360382906398",
    )

    result = cli.cmd_watch(args)

    assert result == 0
    assert captured["runtime_instance_id"] == "discord-thread-1496197360382906398"
    assert captured["control_surface"] == "discord"
    assert captured["control_surface_key"] == "discord:1493226848409358426:1496197360382906398"



def test_cmd_watch_interval_forwards_instance_scope(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_watch_loop(root, **kwargs):
        captured["root"] = root
        captured.update(kwargs)

    monkeypatch.setattr(cli.bus, "run_watch_loop", fake_run_watch_loop)

    args = argparse.Namespace(
        runtime="OpenClaw",
        claim_next=True,
        stale_after_seconds=300,
        once=False,
        interval=15,
        runtime_instance_id="discord-thread-1496197360382906398",
        control_surface="discord",
        control_surface_key="discord:1493226848409358426:1496197360382906398",
    )

    result = cli.cmd_watch(args)

    assert result == 0
    assert captured["runtime_instance_id"] == "discord-thread-1496197360382906398"
    assert captured["control_surface"] == "discord"
    assert captured["control_surface_key"] == "discord:1493226848409358426:1496197360382906398"



def test_cmd_heartbeat_forwards_instance_scope_fields(monkeypatch):
    captured: dict[str, object] = {}

    def fake_upsert_heartbeat(root, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return {"runtime": kwargs["runtime"], "heartbeat_scope": kwargs["heartbeat_scope"]}

    def fake_print_json(payload):
        captured["payload"] = payload
        return 0

    monkeypatch.setattr(cli.bus, "upsert_heartbeat", fake_upsert_heartbeat)
    monkeypatch.setattr(cli, "_print_json", fake_print_json)

    args = argparse.Namespace(
        runtime="OpenClaw",
        status="busy",
        health="ok",
        current_task_id="task-123",
        summary="discord lane active",
        runtime_instance_id="discord-thread-1496197360382906398",
        heartbeat_scope="instance",
        control_surface="discord",
        control_surface_key="discord:1493226848409358426:1496197360382906398",
    )

    result = cli.cmd_heartbeat(args)

    assert result == 0
    assert captured["runtime"] == "OpenClaw"
    assert captured["runtime_instance_id"] == "discord-thread-1496197360382906398"
    assert captured["heartbeat_scope"] == "instance"
    assert captured["control_surface"] == "discord"
    assert captured["control_surface_key"] == "discord:1493226848409358426:1496197360382906398"
    assert captured["payload"] == {"runtime": "OpenClaw", "heartbeat_scope": "instance"}


def test_cmd_runtimes_includes_heartbeat_instances(monkeypatch):
    captured: dict[str, object] = {}

    class FakeCaps:
        def __init__(self):
            self.bus_name = "OpenClaw"
            self.display_name = "OpenClaw"
            self.description = "Executor"
            self.handles = []
            self.max_concurrent_tasks = 3
            self.heartbeat_stale_seconds = 900
            self.priority_ceiling = "normal"

    class FakeLive:
        runtime_name = "openclaw"
        last_seen = "2026-04-26T16:20:00+00:00"
        status = "busy"
        health = "ok"
        age_seconds = 5.0
        is_stale = False
        stale_threshold_seconds = 900

    class FakeBackend:
        def list_heartbeats(self):
            return [
                {
                    "heartbeat_key": "OpenClaw",
                    "runtime": "OpenClaw",
                    "heartbeat_scope": "runtime",
                    "last_seen": "2026-04-26T16:19:00+00:00",
                },
                {
                    "heartbeat_key": "OpenClaw:discord-thread-1",
                    "runtime": "OpenClaw",
                    "runtime_instance_id": "discord-thread-1",
                    "heartbeat_scope": "instance",
                    "control_surface": "discord",
                    "control_surface_key": "discord:ops:thread-1",
                    "last_seen": "2026-04-26T16:20:00+00:00",
                },
            ]

    def fake_print_json(payload):
        captured["payload"] = payload
        return 0

    monkeypatch.setattr(cli, "_print_json", fake_print_json)
    monkeypatch.setattr("runtime.agent_bus.capabilities.load_all_capabilities", lambda root: {"openclaw": FakeCaps()})
    monkeypatch.setattr(cli, "get_runtime_liveness", lambda root: {"OpenClaw": FakeLive()})
    monkeypatch.setattr(cli, "get_backend", lambda root: FakeBackend())

    result = cli.cmd_runtimes(argparse.Namespace())

    assert result == 0
    payload = captured["payload"]
    assert isinstance(payload, list)
    assert payload[0]["bus_name"] == "OpenClaw"
    assert payload[0]["heartbeat_instance_count"] == 2
    assert payload[0]["heartbeat_instances"][1]["heartbeat_scope"] == "instance"


def test_build_parser_accepts_watch_interval():
    parser = cli.build_parser()
    args = parser.parse_args([
        "watch",
        "--runtime",
        "Hermes",
        "--interval",
        "20",
        "--claim-next",
    ])

    assert args.runtime == "Hermes"
    assert args.interval == 20
    assert args.claim_next is True
    assert args.once is False


def test_build_parser_accepts_instance_scoped_heartbeat_flags():
    parser = cli.build_parser()
    args = parser.parse_args([
        "heartbeat",
        "--runtime",
        "OpenClaw",
        "--status",
        "busy",
        "--health",
        "ok",
        "--runtime-instance-id",
        "discord-thread-1496197360382906398",
        "--heartbeat-scope",
        "instance",
        "--control-surface",
        "discord",
        "--control-surface-key",
        "discord:1493226848409358426:1496197360382906398",
    ])

    assert args.runtime == "OpenClaw"
    assert args.runtime_instance_id == "discord-thread-1496197360382906398"
    assert args.heartbeat_scope == "instance"
    assert args.control_surface == "discord"
    assert args.control_surface_key == "discord:1493226848409358426:1496197360382906398"


def test_build_parser_accepts_codex_heartbeat_runtime():
    parser = cli.build_parser()
    args = parser.parse_args([
        "heartbeat",
        "--runtime",
        "Codex",
        "--status",
        "idle",
        "--health",
        "ok",
        "--runtime-instance-id",
        "codex-cli-runtime-integration",
        "--heartbeat-scope",
        "instance",
        "--control-surface",
        "codex-cli",
        "--control-surface-key",
        "runtime-integration-codex-bus",
    ])

    assert args.runtime == "Codex"
    assert args.runtime_instance_id == "codex-cli-runtime-integration"
    assert args.heartbeat_scope == "instance"
