"""Codex bus adapter scaffold.

Codex is registered as a worker on the ChaseOS agent bus, not as a core
runtime writer. Import from ``runtime.adapters.codex.bus_adapter`` for the
strict packet/event helpers used by tests and future live Codex process wiring.
"""

from .bus_adapter import (
    CODEX_BUS_NAME,
    CodexArtifact,
    CodexResult,
    CodexTaskPacket,
    build_codex_task_packet,
    codex_result_event,
    mock_codex_result_for_task,
)
from .daemon import (
    MockCodexExecutor,
    SubprocessCodexExecutor,
    get_codex_daemon_readiness,
    run_codex_daemon_loop,
    run_codex_daemon_once,
)

__all__ = [
    "CODEX_BUS_NAME",
    "CodexArtifact",
    "CodexResult",
    "CodexTaskPacket",
    "build_codex_task_packet",
    "codex_result_event",
    "mock_codex_result_for_task",
    "MockCodexExecutor",
    "SubprocessCodexExecutor",
    "get_codex_daemon_readiness",
    "run_codex_daemon_loop",
    "run_codex_daemon_once",
]
