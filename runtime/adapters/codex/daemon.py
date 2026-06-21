"""Live Codex runtime daemon for the ChaseOS Agent Bus.

The daemon is intentionally small and bus-bound:

1. claim one task addressed to ``Codex``;
2. convert it to a strict Codex task packet;
3. execute either a live ``codex exec`` subprocess or a deterministic mock executor;
4. persist reviewable artifacts under ``runtime/adapters/codex/runs/``;
5. write a standard agent-bus event/status back to the task.

Codex result kinds (proposal/patch/risk/blocked/complete) are adapter-level
semantics. The shared agent bus still receives standard event types such as
``result_attached``, ``blocked``, and ``completed`` so existing bus schemas and
SQLite constraints remain stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Protocol

from runtime.agent_bus.bus import get_bus_mode, list_tasks, update_task_status, watch_once
from runtime.agent_bus.capabilities import load_all_capabilities
from runtime.adapters.event_emitter import (
    RuntimeEventEmissionError,
    emit_artifact_created_event,
    emit_file_written_event,
)
from runtime.adapters.codex.bus_adapter import (
    CODEX_BUS_NAME,
    CodexArtifact,
    CodexResult,
    build_codex_task_packet,
    mock_codex_result_for_task,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value).strip("-") or "codex-task"


def codex_daemon_config_path(vault_root: str | Path) -> Path:
    """Return the repo-local Codex daemon config path."""

    return Path(vault_root) / "runtime" / "adapters" / "codex" / "codex-daemon.config.json"


def load_codex_daemon_config(vault_root: str | Path) -> dict[str, Any]:
    """Load optional repo-local daemon config. Missing config is allowed."""

    path = codex_daemon_config_path(vault_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_error": f"Invalid JSON in {path}: {exc}"}
    return data if isinstance(data, dict) else {"_error": f"Config must be a JSON object: {path}"}


def resolve_codex_binary(vault_root: str | Path, requested_binary: str = "codex") -> dict[str, Any]:
    """Resolve Codex binary from explicit arg, env, config, then PATH.

    Precedence:
    1. explicit non-default --codex-binary value
    2. CHASEOS_CODEX_BINARY environment variable
    3. runtime/adapters/codex/codex-daemon.config.json: codex_binary
    4. PATH lookup for requested/default binary
    """

    config = load_codex_daemon_config(vault_root)
    env_binary = os.environ.get("CHASEOS_CODEX_BINARY")
    config_binary = str(config.get("codex_binary") or "").strip() if not config.get("_error") else ""
    source = "argument"
    candidate = requested_binary
    if requested_binary == "codex" and env_binary:
        candidate = env_binary
        source = "env:CHASEOS_CODEX_BINARY"
    elif requested_binary == "codex" and config_binary:
        candidate = config_binary
        source = "config:codex_binary"

    if any(sep in candidate for sep in ("/", "\\")) or candidate.lower().endswith((".exe", ".cmd", ".bat", ".ps1")):
        path = Path(candidate).expanduser()
        resolved = str(path) if path.exists() else None
    else:
        resolved = shutil.which(candidate)

    return {
        "requested": requested_binary,
        "candidate": candidate,
        "source": source,
        "path": resolved,
        "config_path": str(codex_daemon_config_path(vault_root)),
        "config_error": config.get("_error"),
    }


class CodexExecutor(Protocol):
    """Executor boundary for live Codex or deterministic tests."""

    def run(self, task_packet: dict[str, Any], *, run_dir: Path) -> dict[str, Any]:
        """Return a CodexResult-compatible adapter event dict."""


@dataclass
class MockCodexExecutor:
    """Deterministic executor used for tests and safe daemon smoke checks."""

    def run(self, task_packet: dict[str, Any], *, run_dir: Path) -> dict[str, Any]:
        return mock_codex_result_for_task(task_packet)


@dataclass
class SubprocessCodexExecutor:
    """Run the local Codex CLI against a bounded task packet."""

    codex_binary: str = "codex"
    timeout_seconds: int = 900

    def run(self, task_packet: dict[str, Any], *, run_dir: Path) -> dict[str, Any]:
        if not _live_subprocess_allowed(task_packet):
            artifact_path = _write_live_subprocess_policy_block(task_packet, run_dir)
            return CodexResult(
                task_id=str(task_packet["task_id"]),
                run_id=str(task_packet["run_id"]),
                event_type="blocked",
                summary=(
                    "Codex live subprocess was not started because the task packet "
                    "sets allow_live_subprocess=false."
                ),
                artifacts=[
                    CodexArtifact(
                        "markdown",
                        _relative_to_repo(artifact_path, Path(task_packet["repo_root"])),
                        "Codex live-subprocess policy block.",
                    )
                ],
            ).to_event()

        if not _shell_commands_allowed(task_packet):
            artifact_path = _write_shell_policy_block(task_packet, run_dir)
            return CodexResult(
                task_id=str(task_packet["task_id"]),
                run_id=str(task_packet["run_id"]),
                event_type="blocked",
                summary=(
                    "Codex live subprocess was not started because the task packet "
                    "sets allow_shell_commands=false. The current Codex CLI executor "
                    "cannot structurally prevent nested shell-tool use after launch."
                ),
                artifacts=[
                    CodexArtifact(
                        "markdown",
                        _relative_to_repo(artifact_path, Path(task_packet["repo_root"])),
                        "Codex shell-command policy block.",
                    )
                ],
            ).to_event()

        resolved = resolve_codex_binary(task_packet["repo_root"], self.codex_binary)
        codex_executable = resolved.get("path")
        if not codex_executable:
            return CodexResult(
                task_id=str(task_packet["task_id"]),
                run_id=str(task_packet["run_id"]),
                event_type="blocked",
                summary=(
                    f"Codex CLI binary not found. requested={resolved['requested']} "
                    f"candidate={resolved['candidate']} source={resolved['source']}"
                ),
            ).to_event()

        packet_path = run_dir / "codex-task-packet.json"
        packet_path.write_text(json.dumps(task_packet, indent=2), encoding="utf-8")
        prompt = _build_codex_prompt(task_packet, packet_path)

        command = _codex_exec_command(codex_executable, prompt, task_packet)

        try:
            completed = subprocess.run(
                command,
                cwd=str(task_packet["repo_root"]),
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return CodexResult(
                task_id=str(task_packet["task_id"]),
                run_id=str(task_packet["run_id"]),
                event_type="blocked",
                summary=f"Codex CLI timed out after {self.timeout_seconds}s: {exc}",
            ).to_event()

        stdout_path = run_dir / "codex-stdout.md"
        stderr_path = run_dir / "codex-stderr.log"
        stdout_path.write_text(completed.stdout or "", encoding="utf-8")
        stderr_path.write_text(completed.stderr or "", encoding="utf-8")

        artifact_paths = [
            CodexArtifact("markdown", _relative_to_repo(stdout_path, Path(task_packet["repo_root"])), "Codex CLI stdout."),
            CodexArtifact("log", _relative_to_repo(stderr_path, Path(task_packet["repo_root"])), "Codex CLI stderr."),
        ]
        if completed.returncode != 0:
            return CodexResult(
                task_id=str(task_packet["task_id"]),
                run_id=str(task_packet["run_id"]),
                event_type="blocked",
                summary=f"Codex CLI exited non-zero ({completed.returncode}). See artifacts.",
                artifacts=artifact_paths,
            ).to_event()

        return CodexResult(
            task_id=str(task_packet["task_id"]),
            run_id=str(task_packet["run_id"]),
            event_type="proposal",
            summary="Codex CLI completed and returned reviewable output artifacts.",
            artifacts=artifact_paths,
        ).to_event()


def _codex_exec_command(codex_executable: str, prompt: str, task_packet: dict[str, Any]) -> list[str]:
    """Build the Codex CLI command for a bounded task packet."""

    command = [codex_executable, "exec", "--skip-git-repo-check", "--ephemeral"]
    allowed_write_roots = _resolved_allowed_write_roots(task_packet)
    if not allowed_write_roots:
        command.extend(["--sandbox", "read-only"])
    else:
        command.extend(["--sandbox", "workspace-write", "--cd", str(allowed_write_roots[0])])
        for extra_root in allowed_write_roots[1:]:
            command.extend(["--add-dir", str(extra_root)])
    command.append("-")
    return command


def _resolved_allowed_write_roots(task_packet: dict[str, Any]) -> list[Path]:
    """Return repo-confined allowed write roots for the nested Codex CLI."""

    raw_paths = task_packet.get("allowed_write_paths") or []
    if not isinstance(raw_paths, list):
        return []

    repo_root = Path(str(task_packet.get("repo_root") or ".")).resolve()
    roots: list[Path] = []
    for raw_path in raw_paths:
        raw_text = str(raw_path).strip()
        if not raw_text:
            continue
        candidate = Path(raw_text)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _shell_commands_allowed(task_packet: dict[str, Any]) -> bool:
    """Return whether the live Codex subprocess may use shell-capable execution."""

    return bool(task_packet.get("allow_shell_commands", True))


def _live_subprocess_allowed(task_packet: dict[str, Any]) -> bool:
    """Return whether the live Codex subprocess may be started at all."""

    return bool(task_packet.get("allow_live_subprocess", True))


def _write_live_subprocess_policy_block(task_packet: dict[str, Any], run_dir: Path) -> Path:
    """Write a reviewable artifact explaining a pre-spawn live-subprocess block."""

    path = run_dir / "codex-live-subprocess-policy-block.md"
    path.write_text(
        "\n".join(
            [
                "# Codex Live Subprocess Policy Block",
                "",
                f"Task: `{task_packet.get('task_id')}`",
                f"Run: `{task_packet.get('run_id')}`",
                "",
                "The Codex live subprocess was not started.",
                "",
                "Reason: the task packet sets `allow_live_subprocess: false`.",
                "",
                "Result: ChaseOS returned a blocked adapter event before resolving or spawning the Codex CLI binary.",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_shell_policy_block(task_packet: dict[str, Any], run_dir: Path) -> Path:
    """Write a reviewable artifact explaining a pre-spawn no-shell block."""

    path = run_dir / "codex-shell-policy-block.md"
    path.write_text(
        "\n".join(
            [
                "# Codex Shell Policy Block",
                "",
                f"Task: `{task_packet.get('task_id')}`",
                f"Run: `{task_packet.get('run_id')}`",
                "",
                "The Codex live subprocess was not started.",
                "",
                "Reason: the task packet sets `allow_shell_commands: false`, and this adapter cannot structurally disable nested shell-tool use inside a launched Codex CLI process.",
                "",
                "Result: ChaseOS returned a blocked adapter event before resolving or spawning the Codex CLI binary.",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _build_codex_prompt(task_packet: dict[str, Any], packet_path: Path) -> str:
    packet_json = json.dumps(task_packet, indent=2, sort_keys=True)
    return (
        "You are Codex joining the ChaseOS agent bus as worker `Codex`.\n"
        f"The bounded task packet was written to: {packet_path}\n"
        "Use the embedded packet below as the source of truth so simple read-only tasks do not need a shell command just to inspect their request.\n"
        "Bounded task packet:\n"
        "```json\n"
        f"{packet_json}\n"
        "```\n"
        f"Live subprocess allowance: {str(_live_subprocess_allowed(task_packet)).lower()}. "
        f"Shell command allowance: {str(_shell_commands_allowed(task_packet)).lower()}. "
        "Run shell commands only when shell command allowance is true and they are necessary for bounded read-only inspection or requested tests. "
        "Do not edit files unless the task packet explicitly asks for edits and declares allowed write paths. "
        "If `allowed_write_paths` is empty, treat this as a hard no-write packet: do not write code files, docs, build logs, daily notes, "
        "documentation-history notes, agent-activity logs, indexes, or runtime state; no build-log/daily/archive/index writeback; return text output only. "
        "Follow its constraints exactly. Do not directly mutate Pulse memory, Personal Map, "
        "R&D truth-state records, or governed core state unless explicitly authorized. "
        "Prefer reviewable patches/artifacts and state tests run."
    )


def _relative_to_repo(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _run_dir(vault_root: Path, task_id: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = vault_root / "runtime" / "adapters" / "codex" / "runs" / f"{stamp}-{_safe_name(task_id)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _artifact_paths(adapter_event: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for artifact in adapter_event.get("artifacts", []) or []:
        if isinstance(artifact, dict) and artifact.get("path"):
            paths.append(str(artifact["path"]))
        elif isinstance(artifact, str):
            paths.append(artifact)
    return paths


def _emit_codex_runtime_activity(
    root: Path,
    *,
    task_id: str,
    run_id: str,
    run_dir: Path,
    artifact_paths: list[str],
) -> dict[str, Any]:
    """Emit visibility-only Codex daemon file/artifact activity.

    Runtime activity emission is advisory telemetry for Studio Graph/Chat. It is
    deliberately fail-open so event-spool problems cannot alter Agent Bus task
    claim/result semantics.
    """

    emitted: list[dict[str, Any]] = []
    errors: list[str] = []

    def _capture(callable_name: str, **kwargs: Any) -> None:
        try:
            emitted.append(kwargs.pop("_fn")(**kwargs))
        except (RuntimeEventEmissionError, OSError, ValueError) as exc:
            errors.append(f"{callable_name}:{str(exc)[:180]}")

    common = {
        "adapter_id": "codex",
        "runtime_name": "Codex",
        "runtime_type": "bounded_development_worker",
        "run_id": str(run_id),
        "session_id": str(task_id),
        "payload": {"task_id": str(task_id), "source": "codex_daemon"},
    }
    packet_path = _relative_to_repo(run_dir / "codex-task-packet.json", root)
    result_path = _relative_to_repo(run_dir / "codex-adapter-result.json", root)
    for path, scope in (
        (packet_path, "codex_task_packet"),
        (result_path, "codex_adapter_result"),
    ):
        _capture(
            "file.written",
            _fn=emit_file_written_event,
            vault_root=root,
            path=path,
            summary=f"Codex daemon wrote {scope.replace('_', ' ')}.",
            write_scope=scope,
            **common,
        )

    for artifact_path in artifact_paths:
        _capture(
            "artifact.created",
            _fn=emit_artifact_created_event,
            vault_root=root,
            artifact_path=artifact_path,
            summary="Codex daemon produced a reviewable artifact.",
            artifact_kind="codex_review_artifact",
            **common,
        )

    return {
        "ok": not errors,
        "emitted_count": len(emitted),
        "errors": errors,
        "event_ids": [str(event.get("id") or "") for event in emitted],
        "event_types": [str(event.get("event_type") or "") for event in emitted],
    }


def _bus_status_and_event(adapter_event_type: str) -> tuple[str, str]:
    if adapter_event_type == "blocked":
        return "blocked", "blocked"
    if adapter_event_type == "complete":
        return "done", "completed"
    return "done", "result_attached"


def codex_cli_connect_guidance(vault_root: str | Path, candidate: str = "codex") -> dict[str, Any]:
    """Return operator-facing commands for connecting a local Codex CLI."""

    config_path = codex_daemon_config_path(vault_root)
    return {
        "requires_cli": True,
        "why": "The ChaseOS Codex daemon executes bounded bus packets through a local `codex exec` subprocess.",
        "if_you_use_codex_app": "Install/sign in to the Codex CLI too, or point CHASEOS_CODEX_BINARY/codex-daemon.config.json at an existing CLI executable.",
        "verify_command": "codex --version",
        "readiness_command": "python -m chaseos agent-bus codex-daemon --readiness --json",
        "smoke_command": "python -m chaseos agent-bus codex-daemon --once --executor mock --json",
        "live_command": f"python -m chaseos agent-bus codex-daemon --interval 30 --executor codex --codex-binary {candidate}",
        "env_override_powershell": "$env:CHASEOS_CODEX_BINARY=\"C:\\path\\to\\codex.exe\"",
        "config_path": str(config_path),
    }


def resolve_codex_runtime_instance_id(vault_root: str | Path) -> str | None:
    """Return the retained Codex runtime instance name from capabilities."""

    try:
        codex_caps = load_all_capabilities(Path(vault_root)).get("codex")
    except Exception:
        return None
    if codex_caps is None:
        return None
    return codex_caps.retained_runtime_name or codex_caps.personal_runtime_name or None


def get_codex_daemon_readiness(
    vault_root: str | Path,
    *,
    codex_binary: str = "codex",
) -> dict[str, Any]:
    """Return a bounded readiness report for activating Codex as a bus daemon."""

    root = Path(vault_root)
    resolved_binary = resolve_codex_binary(root, codex_binary)
    binary_path = resolved_binary.get("path")
    caps = load_all_capabilities(root)
    codex_caps = caps.get("codex")
    open_tasks = list_tasks(root, recipient=CODEX_BUS_NAME, status="open")
    claimed_tasks = list_tasks(root, recipient=CODEX_BUS_NAME, owner=CODEX_BUS_NAME)
    capability_task_types = [cap.task_type for cap in codex_caps.handles] if codex_caps else []
    runtime_instance_id = resolve_codex_runtime_instance_id(root)
    checks = {
        "capability_manifest": codex_caps is not None and codex_caps.bus_name == CODEX_BUS_NAME,
        "codex_binary": binary_path is not None,
        "can_handle_code_patch": "code.patch" in capability_task_types,
        "artifact_run_dir_parent": (root / "runtime" / "adapters" / "codex").exists(),
    }
    blocking_reasons = [name for name, passed in checks.items() if not passed]
    connect_guidance = codex_cli_connect_guidance(root, str(resolved_binary["candidate"]))
    return {
        "ok": all(checks.values()),
        "runtime": CODEX_BUS_NAME,
        "runtime_instance_id": runtime_instance_id,
        "bus_mode": get_bus_mode(root),
        "checks": checks,
        "blocking_reasons": blocking_reasons,
        "codex_binary": codex_binary,
        "codex_binary_resolution": resolved_binary,
        "codex_binary_path": binary_path,
        "capability_task_types": capability_task_types,
        "open_task_count": len(open_tasks),
        "claimed_task_count": len(claimed_tasks),
        "smoke_command": connect_guidance["smoke_command"],
        "live_command": connect_guidance["live_command"],
        "connect_guidance": connect_guidance,
        "operator_fix": (
            "Install/sign in to Codex CLI on PATH, set CHASEOS_CODEX_BINARY, or set codex_binary in "
            f"{codex_daemon_config_path(root)}"
            if not binary_path else None
        ),
    }


def run_codex_daemon_once(
    vault_root: str | Path,
    *,
    executor: CodexExecutor | None = None,
    task_type: str = "code.patch",
    allow_shell_commands: bool = True,
    stale_after_seconds: int | None = None,
) -> dict[str, Any]:
    """Run one Codex daemon cycle: heartbeat, claim, execute, write result."""

    root = Path(vault_root)
    runtime_instance_id = resolve_codex_runtime_instance_id(root)
    watch = watch_once(
        root,
        runtime=CODEX_BUS_NAME,
        claim_next=True,
        stale_after_seconds=stale_after_seconds,
        runtime_instance_id=runtime_instance_id,
        control_surface="codex-cli" if runtime_instance_id else None,
        control_surface_key="codex-daemon" if runtime_instance_id else None,
    )
    task_id = watch.get("claimed_task_id")
    if not task_id:
        return {
            "ok": True,
            "runtime": CODEX_BUS_NAME,
            "runtime_instance_id": runtime_instance_id,
            "claimed_task_id": None,
            "watch": watch,
        }

    tasks = list_tasks(root, recipient=CODEX_BUS_NAME, owner=CODEX_BUS_NAME)
    task = next((item for item in tasks if item.get("task_id") == task_id), None)
    if task is None:
        return {"ok": False, "runtime": CODEX_BUS_NAME, "claimed_task_id": task_id, "reason": "claimed task not found"}

    update_task_status(
        root,
        task_id=str(task_id),
        runtime=CODEX_BUS_NAME,
        status="in_progress",
        event_type="started",
        message="Codex live daemon started bounded task execution.",
    )

    run_dir = _run_dir(root, str(task_id))
    packet = build_codex_task_packet(
        task=task,
        repo_root=root,
        task_type=task_type,
        allow_shell_commands=allow_shell_commands,
    )
    (run_dir / "codex-task-packet.json").write_text(json.dumps(packet, indent=2), encoding="utf-8")

    active_executor = executor or SubprocessCodexExecutor()
    adapter_event = active_executor.run(packet, run_dir=run_dir)
    (run_dir / "codex-adapter-result.json").write_text(json.dumps(adapter_event, indent=2), encoding="utf-8")

    status, bus_event_type = _bus_status_and_event(str(adapter_event.get("event_type") or "proposal"))
    artifact_paths = _artifact_paths(adapter_event)
    result_artifact = _relative_to_repo(run_dir / "codex-adapter-result.json", root)
    if result_artifact not in artifact_paths:
        artifact_paths.append(result_artifact)
    runtime_activity_events = _emit_codex_runtime_activity(
        root,
        task_id=str(task_id),
        run_id=str(task.get("run_id") or task_id),
        run_dir=run_dir,
        artifact_paths=artifact_paths,
    )

    update = update_task_status(
        root,
        task_id=str(task_id),
        runtime=CODEX_BUS_NAME,
        status=status,
        event_type=bus_event_type,
        message=f"Codex {adapter_event.get('event_type', 'result')}: {adapter_event.get('message', '')}",
        artifacts=artifact_paths,
    )
    return {
        "ok": bool(update.get("updated")),
        "runtime": CODEX_BUS_NAME,
        "runtime_instance_id": runtime_instance_id,
        "claimed_task_id": task_id,
        "task_type": task_type,
        "run_dir": _relative_to_repo(run_dir, root),
        "adapter_event_type": adapter_event.get("event_type"),
        "bus_status": status,
        "bus_event_type": bus_event_type,
        "artifacts": artifact_paths,
        "runtime_activity_events": runtime_activity_events,
        "update": update,
        "watch": watch,
    }


def run_codex_daemon_loop(
    vault_root: str | Path,
    *,
    interval_seconds: int,
    executor: CodexExecutor | None = None,
    task_type: str = "code.patch",
    allow_shell_commands: bool = True,
    stale_after_seconds: int | None = None,
) -> None:
    """Run Codex as a polling bus daemon until interrupted."""

    while True:
        run_codex_daemon_once(
            vault_root,
            executor=executor,
            task_type=task_type,
            allow_shell_commands=allow_shell_commands,
            stale_after_seconds=stale_after_seconds,
        )
        time.sleep(interval_seconds)
