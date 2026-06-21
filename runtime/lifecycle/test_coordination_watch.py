"""coordination-watch lifecycle tests for ChaseOS.

Verifies:
- lifecycle records expose coordination_watch configuration for known runtimes
- run_coordination_watch uses watch_once for one-shot mode
- run_coordination_watch uses run_watch_loop for interval mode

Run:
    .venv/Scripts/python.exe -m pytest runtime/lifecycle/test_coordination_watch.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.lifecycle.health_cli import load_lifecycle_record
from runtime.lifecycle.coordination_watch import run_coordination_watch  # type: ignore


def test_openclaw_lifecycle_record_declares_coordination_watch_defaults():
    record = load_lifecycle_record("openclaw")
    watch = record.get("coordination_watch") or {}

    assert watch.get("enabled") is True
    assert int(watch.get("interval_seconds")) > 0
    assert isinstance(watch.get("claim_next"), bool)


def test_run_coordination_watch_once_uses_watch_once(monkeypatch):
    captured: dict[str, object] = {}

    def fake_watch_once(vault_root, *, runtime, claim_next=False, stale_after_seconds=None, now_iso=None):
        captured["runtime"] = runtime
        captured["claim_next"] = claim_next
        captured["stale_after_seconds"] = stale_after_seconds
        return {"runtime": runtime, "open_task_count": 1}

    monkeypatch.setattr("runtime.lifecycle.coordination_watch.bus.watch_once", fake_watch_once)

    result = run_coordination_watch("hermes", once=True)

    assert result["mode"] == "once"
    assert result["result"] == {"runtime": "Hermes", "open_task_count": 1}
    assert captured["runtime"] == "Hermes"


def test_run_coordination_watch_interval_uses_run_watch_loop(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_watch_loop(vault_root, *, runtime, interval_seconds, claim_next=False, stale_after_seconds=None):
        captured["runtime"] = runtime
        captured["interval_seconds"] = interval_seconds
        captured["claim_next"] = claim_next
        captured["stale_after_seconds"] = stale_after_seconds

    monkeypatch.setattr("runtime.lifecycle.coordination_watch.bus.run_watch_loop", fake_run_watch_loop)

    result = run_coordination_watch("openclaw")

    assert result["mode"] == "loop"
    assert result["runtime"] == "OpenClaw"
    assert int(captured["interval_seconds"]) > 0
    assert captured["runtime"] == "OpenClaw"
