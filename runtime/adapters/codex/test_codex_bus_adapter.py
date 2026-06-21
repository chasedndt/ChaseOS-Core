from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from runtime.adapters.codex.bus_adapter import (
    CODEX_BUS_NAME,
    build_codex_task_packet,
    codex_result_event,
    mock_codex_result_for_task,
)
from runtime.agent_bus.capabilities import discover_runtime_names, load_all_capabilities
from runtime.agent_bus.bus import create_task, init_db, list_tasks, watch_once


def test_codex_adapter_schemas_are_strict_and_codex_scoped():
    base = Path(__file__).parent
    task_schema = json.loads((base / "codex-task.schema.json").read_text(encoding="utf-8"))
    result_schema = json.loads((base / "codex-result.schema.json").read_text(encoding="utf-8"))

    assert task_schema["additionalProperties"] is False
    assert task_schema["properties"]["to"] == {"const": CODEX_BUS_NAME}
    assert task_schema["properties"]["task_type"]["enum"] == [
        "code.review",
        "code.patch",
        "repo.inspect",
        "test.run",
    ]
    assert task_schema["properties"]["allow_shell_commands"] == {"type": "boolean"}
    assert "allow_shell_commands" in task_schema["required"]
    assert task_schema["properties"]["allow_live_subprocess"] == {"type": "boolean"}
    assert "allow_live_subprocess" in task_schema["required"]
    assert result_schema["additionalProperties"] is False
    assert result_schema["properties"]["from"] == {"const": CODEX_BUS_NAME}
    assert result_schema["properties"]["event_type"]["enum"] == [
        "proposal",
        "patch",
        "risk",
        "blocked",
        "complete",
    ]


def test_codex_runtime_registers_as_bus_capability_from_repo_root():
    vault = Path(__file__).resolve().parents[3]

    assert "codex" in discover_runtime_names(vault)
    caps = load_all_capabilities(vault)

    assert caps["codex"].bus_name == CODEX_BUS_NAME
    assert caps["codex"].can_handle("code.patch")
    assert caps["codex"].can_handle("repo.inspect")


def test_codex_can_receive_bus_task_and_mock_result_without_core_write():
    root = Path(".codex_tmp_test") / f"codex-bus-adapter-{uuid.uuid4().hex}"
    try:
        vault = root / "vault"
        vault.mkdir(parents=True)
        (vault / "CLAUDE.md").write_text("# test vault", encoding="utf-8")
        (vault / "runtime" / "agent_bus").mkdir(parents=True)

        for runtime_name, bus_name in [("openclaw", "OpenClaw"), ("codex", "Codex")]:
            runtime_dir = vault / "runtime" / runtime_name
            runtime_dir.mkdir(parents=True)
            (runtime_dir / "capabilities.yaml").write_text(
                f"bus_name: {bus_name}\n"
                "heartbeat_stale_seconds: 900\n"
                "max_concurrent_tasks: 1\n"
                "priority_ceiling: normal\n"
                "handles:\n"
                "  - task_type: code.patch\n",
                encoding="utf-8",
            )

        init_db(vault)
        created = create_task(
            vault,
            sender="OpenClaw",
            recipient="Codex",
            request="Inspect Pulse Phase A/B scaffold and propose a bus-safe patch.",
            expected_output="Return proposal/patch artifacts only; do not mutate memory/core state directly.",
        )
        assert created["created"] is True

        watch = watch_once(vault, runtime="Codex", claim_next=True)
        assert watch["claimed_task_id"] == created["task_id"]

        task = list_tasks(vault, recipient="Codex", owner="Codex")[0]
        packet = build_codex_task_packet(task=task, repo_root=vault, task_type="code.patch")
        assert packet["to"] == "Codex"
        assert "Do not mutate ChaseOS memory/core state directly." in packet["constraints"]
        assert packet["allow_shell_commands"] is True
        assert packet["allow_live_subprocess"] is True

        event = mock_codex_result_for_task(packet)
        assert event["from"] == "Codex"
        assert event["event_type"] == "proposal"
        assert event["artifacts"][0]["path"] == "runtime/adapters/codex/README.md"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_codex_result_event_rejects_unbounded_event_type():
    try:
        codex_result_event(
            task_id="task-1",
            run_id="run-1",
            event_type="memory_write",  # type: ignore[arg-type]
            summary="should fail",
        )
    except ValueError as exc:
        assert "Unsupported Codex result event_type" in str(exc)
    else:
        raise AssertionError("Codex adapter must reject direct memory-write style events")


def test_codex_task_packet_can_structurally_disable_shell_commands():
    packet = build_codex_task_packet(
        task={
            "task_id": "task-no-shell",
            "run_id": "run-no-shell",
            "request": "Return text only.",
            "expected_output": "No shell command use.",
        },
        repo_root=Path.cwd(),
        task_type="repo.inspect",
        allow_shell_commands=False,
    )

    assert packet["allow_shell_commands"] is False


def test_codex_task_packet_maps_agent_bus_execution_constraints():
    packet = build_codex_task_packet(
        task={
            "task_id": "task-constraints",
            "run_id": "run-constraints",
            "request": "Return text only.",
            "expected_output": "No subprocess or shell use.",
            "execution_constraints": {
                "allow_shell_commands": False,
                "allow_live_subprocess": False,
                "write_policy": "none",
            },
        },
        repo_root=Path.cwd(),
        task_type="repo.inspect",
    )

    assert packet["allow_shell_commands"] is False
    assert packet["allow_live_subprocess"] is False
    assert packet["allowed_write_paths"] == []
