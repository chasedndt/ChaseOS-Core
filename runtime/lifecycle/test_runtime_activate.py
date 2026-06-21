"""Tests for the runtime activation orchestrator (Stages 1-7 chaining).

Detection is injected via a fake runner; vault state is synthesized in tmp dirs.
These tests never shell out and never mutate the real vault.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from runtime.lifecycle.activate import build_activation_plan, run_activation


REPO = Path(__file__).resolve().parents[2]


def _vault(tmp_path: Path) -> Path:
    lc = tmp_path / "runtime" / "lifecycle"
    lc.mkdir(parents=True)
    shutil.copy(
        REPO / "runtime" / "lifecycle" / "openclaw.lifecycle.yaml",
        lc / "openclaw.lifecycle.yaml",
    )
    return tmp_path


def _runner(*, binary=True, node="v24.0.0"):
    def runner(cmd):
        head = cmd[:1]
        if head == ["openclaw"]:
            return ({"ok": True, "returncode": 0, "stdout": "openclaw 1.0.0", "stderr": ""}
                    if binary else {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": "not_found"})
        if head == ["node"]:
            return ({"ok": True, "returncode": 0, "stdout": node, "stderr": ""}
                    if node else {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": "not_found"})
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": "not_found"}
    return runner


def _register(vault: Path) -> None:
    p = vault / "runtime" / "memory" / "adapters" / "openclaw"
    p.mkdir(parents=True, exist_ok=True)
    (p / "profile.json").write_text("{}", encoding="utf-8")


def _enable_startup(vault: Path) -> None:
    d = vault / "runtime" / "lifecycle" / "run" / "startup-surface-mutations"
    d.mkdir(parents=True, exist_ok=True)
    (d / "startup-surface-openclaw-gateway-enable-20260101T000000Z-aaa.json").write_text("{}", encoding="utf-8")


def _launch(vault: Path) -> None:
    d = vault / "runtime" / "lifecycle" / "run"
    d.mkdir(parents=True, exist_ok=True)
    (d / "openclaw-coordination-watch.json").write_text("{}", encoding="utf-8")


# ── resume-from-each-stage matrix ───────────────────────────────────────────────

def test_resume_at_preflight_when_node_missing(tmp_path):
    vault = _vault(tmp_path)
    plan = build_activation_plan("openclaw", vault, runner=_runner(binary=False, node=""))
    assert plan["current_stage"] == "preflight"
    assert len(plan["stages"]) == 7


def test_resume_at_install_when_node_ok_binary_absent(tmp_path):
    vault = _vault(tmp_path)
    plan = build_activation_plan("openclaw", vault, runner=_runner(binary=False, node="v24.0.0"))
    assert plan["current_stage"] == "install"


def test_resume_at_configure_when_present_not_registered(tmp_path):
    vault = _vault(tmp_path)
    plan = build_activation_plan("openclaw", vault, runner=_runner(binary=True))
    assert plan["current_stage"] == "configure"


def test_resume_at_register_startup_when_registered(tmp_path):
    vault = _vault(tmp_path)
    _register(vault)
    plan = build_activation_plan("openclaw", vault, runner=_runner(binary=True))
    assert plan["current_stage"] == "register_startup"


def test_resume_at_launch_when_startup_enabled(tmp_path):
    vault = _vault(tmp_path)
    _register(vault)
    _enable_startup(vault)
    plan = build_activation_plan("openclaw", vault, runner=_runner(binary=True))
    assert plan["current_stage"] == "launch_verify"


def test_activated_when_all_satisfied(tmp_path):
    vault = _vault(tmp_path)
    _register(vault)
    _enable_startup(vault)
    _launch(vault)
    plan = build_activation_plan("openclaw", vault, runner=_runner(binary=True))
    assert plan["overall"] == "activated"
    assert plan["current_stage"] is None


# ── fail-closed / unsupported ───────────────────────────────────────────────────

def test_unsupported_runtime(tmp_path):
    plan = build_activation_plan("made-up", tmp_path, runner=_runner())
    assert plan["overall"] == "unsupported"
    assert plan["supported"] is False
    assert plan["stages"] == []


def test_hermes_plan_has_seven_stages_and_does_not_crash():
    # hermes absent + WSL not found via the fake runner → preflight blocks; read-only.
    runner = lambda cmd: {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": "not_found"}
    plan = build_activation_plan("hermes", REPO, runner=runner)
    assert len(plan["stages"]) == 7
    assert plan["overall"] in ("blocked", "in-progress")


# ── dry-run default + safe execute ──────────────────────────────────────────────

def test_dry_run_default_performs_nothing(tmp_path):
    vault = _vault(tmp_path)
    result = run_activation("openclaw", vault, runner=_runner(binary=False, node="v24.0.0"))
    assert result["mode"] == "dry-run"
    assert result["actions_performed"] == []
    assert result["authority"]["writes_performed"] is False


def test_confirm_execute_emits_install_scripts(tmp_path):
    vault = _vault(tmp_path)
    out = tmp_path / "out"
    result = run_activation(
        "openclaw", vault,
        confirm=True, dry_run=False,
        runner=_runner(binary=False, node="v24.0.0"),
        out_dir=out,
    )
    assert result["mode"] == "execute"
    actions = result["actions_performed"]
    assert any(a["action"] == "emitted_install_scripts" for a in actions)
    assert (out / "install-openclaw.ps1").is_file()
    assert result["authority"]["writes_performed"] is True


# ── governance: read-only plan, only probe commands ────────────────────────────

def test_plan_is_read_only_and_issues_only_probe_commands(tmp_path):
    vault = _vault(tmp_path)
    calls: list[list[str]] = []

    def recording(cmd):
        calls.append(list(cmd))
        return _runner(binary=True)(cmd)

    plan = build_activation_plan("openclaw", vault, runner=recording)
    assert plan["authority"]["read_only"] is True
    assert plan["authority"]["host_mutation_performed"] is False
    allowed_heads = {("openclaw",), ("node",), ("wsl",)}
    for call in calls:
        assert tuple(call[:1]) in allowed_heads, call


def test_plan_is_json_serializable(tmp_path):
    vault = _vault(tmp_path)
    plan = build_activation_plan("openclaw", vault, runner=_runner(binary=True))
    json.dumps(plan)  # must not raise
