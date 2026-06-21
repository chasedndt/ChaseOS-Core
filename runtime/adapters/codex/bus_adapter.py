"""Codex adapter helpers for the ChaseOS Agent Bus.

This is intentionally a boundary layer, not a direct Codex process launcher.
The contract is:

- ChaseOS/OpenClaw creates bounded task packets for ``Codex``.
- Codex returns structured proposals, risks, blockers, or patch artifacts.
- ChaseOS/OpenClaw remains the arbiter for memory/core writes and patch apply.

The live executor can be wired later behind these pure helpers without changing
bus schemas or tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
import uuid

CODEX_BUS_NAME = "Codex"
CODEX_TASK_TYPES = {"code.review", "code.patch", "repo.inspect", "test.run"}
CODEX_EVENT_TYPES = {"proposal", "patch", "risk", "blocked", "complete"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CodexTaskPacket:
    """Strict input packet handed to Codex from the agent bus."""

    task_id: str
    run_id: str
    task_type: str
    request: str
    expected_output: str
    repo_root: str
    constraints: list[str] = field(default_factory=list)
    allowed_write_paths: list[str] = field(default_factory=list)
    allow_shell_commands: bool = True
    allow_live_subprocess: bool = True
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        if self.task_type not in CODEX_TASK_TYPES:
            raise ValueError(f"Unsupported Codex task_type: {self.task_type}")
        if not self.task_id or not self.run_id:
            raise ValueError("Codex task packets require task_id and run_id")
        if not self.request.strip() or not self.expected_output.strip():
            raise ValueError("Codex task packets require request and expected_output")
        return {
            "schema_version": "1.0",
            "task_id": self.task_id,
            "run_id": self.run_id,
            "to": CODEX_BUS_NAME,
            "task_type": self.task_type,
            "request": self.request,
            "expected_output": self.expected_output,
            "repo_root": self.repo_root,
            "constraints": list(self.constraints),
            "allowed_write_paths": list(self.allowed_write_paths),
            "allow_shell_commands": bool(self.allow_shell_commands),
            "allow_live_subprocess": bool(self.allow_live_subprocess),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class CodexArtifact:
    """An artifact produced by Codex for ChaseOS/OpenClaw review."""

    artifact_type: Literal["diff", "markdown", "json", "log"]
    path: str
    description: str

    def to_dict(self) -> dict[str, str]:
        if not self.path.strip():
            raise ValueError("Codex artifacts require a path")
        return {
            "artifact_type": self.artifact_type,
            "path": self.path,
            "description": self.description,
        }


@dataclass(frozen=True)
class CodexResult:
    """Structured Codex result normalized into bus-safe event payloads."""

    task_id: str
    run_id: str
    event_type: Literal["proposal", "patch", "risk", "blocked", "complete"]
    summary: str
    artifacts: list[CodexArtifact] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)

    def to_event(self) -> dict[str, Any]:
        if self.event_type not in CODEX_EVENT_TYPES:
            raise ValueError(f"Unsupported Codex result event_type: {self.event_type}")
        return {
            "event_id": f"evt-codex-{uuid.uuid4().hex[:12]}",
            "task_id": self.task_id,
            "run_id": self.run_id,
            "from": CODEX_BUS_NAME,
            "event_type": self.event_type,
            "message": self.summary,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "created_at": self.created_at,
        }


def build_codex_task_packet(
    *,
    task: dict[str, Any],
    repo_root: str | Path,
    task_type: str,
    constraints: list[str] | None = None,
    allowed_write_paths: list[str] | None = None,
    allow_shell_commands: bool = True,
    allow_live_subprocess: bool = True,
) -> dict[str, Any]:
    """Convert a ChaseOS bus task row into the strict Codex handoff packet."""
    task_execution_constraints = task.get("execution_constraints") or {}
    if not isinstance(task_execution_constraints, dict):
        task_execution_constraints = {}

    task_allowed_write_paths = task_execution_constraints.get("allowed_write_paths")
    effective_allowed_write_paths = allowed_write_paths
    if isinstance(task_allowed_write_paths, list):
        effective_allowed_write_paths = [str(item) for item in task_allowed_write_paths if str(item).strip()]
    elif task_execution_constraints.get("write_policy") == "none":
        effective_allowed_write_paths = []

    task_allow_shell = task_execution_constraints.get("allow_shell_commands", True)
    effective_allow_shell = bool(allow_shell_commands) and bool(task_allow_shell)
    task_allow_live_subprocess = task_execution_constraints.get("allow_live_subprocess", True)
    effective_allow_live_subprocess = bool(allow_live_subprocess) and bool(task_allow_live_subprocess)

    return CodexTaskPacket(
        task_id=str(task.get("task_id") or ""),
        run_id=str(task.get("run_id") or ""),
        task_type=task_type,
        request=str(task.get("request") or ""),
        expected_output=str(task.get("expected_output") or ""),
        repo_root=str(repo_root),
        constraints=constraints or [
            "Do not mutate ChaseOS memory/core state directly.",
            "Return patches/proposals/artifacts for ChaseOS/OpenClaw review.",
            "Prefer Phase A/B scaffolding and strict schemas before UI work.",
        ],
        allowed_write_paths=effective_allowed_write_paths or [],
        allow_shell_commands=effective_allow_shell,
        allow_live_subprocess=effective_allow_live_subprocess,
    ).to_dict()


def codex_result_event(
    *,
    task_id: str,
    run_id: str,
    event_type: Literal["proposal", "patch", "risk", "blocked", "complete"],
    summary: str,
    artifacts: list[CodexArtifact] | None = None,
) -> dict[str, Any]:
    """Build a structured Codex-originated adapter event."""

    return CodexResult(
        task_id=task_id,
        run_id=run_id,
        event_type=event_type,
        summary=summary,
        artifacts=artifacts or [],
    ).to_event()


def mock_codex_result_for_task(task_packet: dict[str, Any]) -> dict[str, Any]:
    """Deterministic mock result used to test the bus boundary before live Codex wiring."""

    return codex_result_event(
        task_id=str(task_packet["task_id"]),
        run_id=str(task_packet["run_id"]),
        event_type="proposal",
        summary=(
            "Codex adapter mock accepted the task. Live executor should return "
            "proposal/patch/risk/blocked/complete events without direct memory writes."
        ),
        artifacts=[
            CodexArtifact(
                artifact_type="markdown",
                path="runtime/adapters/codex/README.md",
                description="Codex bus adapter contract and operating boundary.",
            )
        ],
    )
