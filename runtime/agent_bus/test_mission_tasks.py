from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.agent_bus.mission_tasks import (
    MISSION_ALLOWED_WRITE_ROOTS,
    MISSION_TASK_TYPE,
    build_mission_task_packet,
    validate_mission_task_packet,
)


def _workspace_path() -> str:
    return "07_LOGS/VentureOps-Missions/2026-05-13_mission-chase-ai-runtime-governance-kit-dry-run"


def _approval_packet_path() -> str:
    return f"{_workspace_path()}/activation-approval-packet-draft.json"


def test_build_mission_task_packet_is_inert_and_valid() -> None:
    packet = build_mission_task_packet(
        mission_id="mission-chase-ai-runtime-governance-kit",
        mission_workspace_path=_workspace_path(),
        activation_approval_packet_path=_approval_packet_path(),
        task_id="mission-task-preview-test",
        run_id="mission-run-preview-test",
        created_at="2026-05-13T00:00:00+00:00",
    )

    assert packet["task_type"] == MISSION_TASK_TYPE
    assert packet["intent"] == "TASK"
    assert packet["status"] == "open"
    assert packet["from"] == "OpenClaw"
    assert packet["to"] == "Codex"
    assert packet["agent_bus_task_written"] is False
    assert packet["activation_performed"] is False
    assert packet["workflow_evolution_applied"] is False
    assert packet["execution_constraints"] == {
        "allow_shell_commands": False,
        "allow_live_subprocess": False,
        "write_policy": "declared-paths",
        "allowed_write_paths": list(MISSION_ALLOWED_WRITE_ROOTS),
    }
    assert validate_mission_task_packet(packet)["ok"] is True


def test_validate_mission_task_packet_rejects_high_authority_drift() -> None:
    packet = build_mission_task_packet(
        mission_id="mission-chase-ai-runtime-governance-kit",
        mission_workspace_path=_workspace_path(),
        activation_approval_packet_path=_approval_packet_path(),
        task_id="mission-task-preview-test",
        run_id="mission-run-preview-test",
        created_at="2026-05-13T00:00:00+00:00",
    )
    packet["activation_performed"] = True
    packet["execution_constraints"]["allow_shell_commands"] = True
    packet["execution_constraints"]["allowed_write_paths"] = ["00_HOME/"]

    result = validate_mission_task_packet(packet)

    assert result["ok"] is False
    assert "activation_performed must be false" in result["errors"]
    assert "execution_constraints.allow_shell_commands must be false" in result["errors"]
    assert any("allowed_write_paths entries must match" in error for error in result["errors"])


def test_validate_mission_task_packet_rejects_path_escape() -> None:
    packet = build_mission_task_packet(
        mission_id="mission-chase-ai-runtime-governance-kit",
        mission_workspace_path=_workspace_path(),
        activation_approval_packet_path=_approval_packet_path(),
        task_id="mission-task-preview-test",
        run_id="mission-run-preview-test",
        created_at="2026-05-13T00:00:00+00:00",
    )
    packet["mission_workspace_path"] = "../outside"

    result = validate_mission_task_packet(packet)

    assert result["ok"] is False
    assert "mission_workspace_path may not leave the vault root" in result["errors"]


def test_mission_task_packet_schema_tracks_runtime_contract() -> None:
    schema_path = Path(__file__).parent / "schemas" / "mission_task_packet.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert "task_type" in schema["required"]
    assert schema["properties"]["task_type"]["const"] == MISSION_TASK_TYPE
    assert schema["properties"]["execution_constraints"]["properties"]["allow_shell_commands"]["const"] is False
    assert schema["properties"]["execution_constraints"]["properties"]["allow_live_subprocess"]["const"] is False
    assert schema["properties"]["agent_bus_task_written"]["const"] is False


def test_build_mission_task_packet_raises_on_invalid_authority() -> None:
    with pytest.raises(ValueError, match="to must be one of"):
        build_mission_task_packet(
            mission_id="mission-chase-ai-runtime-governance-kit",
            mission_workspace_path=_workspace_path(),
            activation_approval_packet_path=_approval_packet_path(),
            recipient="UnknownRuntime",
        )
