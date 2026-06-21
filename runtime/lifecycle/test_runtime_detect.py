"""Tests for runtime binary detection (Stage 1) + the doctor binary-presence check.

All subprocess probing is injected via a fake runner — these tests never shell out.
"""

from __future__ import annotations

from pathlib import Path

from runtime.lifecycle.runtime_detect import (
    STATUS_ABSENT,
    STATUS_PRESENT,
    STATUS_UNKNOWN,
    STATUS_WRONG_VERSION,
    _probe_one,
    detect_runtime,
)
from runtime.lifecycle.runtime_doctor import build_runtime_doctor_report


VAULT_ROOT = Path(__file__).resolve().parents[2]

_MUTATION_TOKENS = ("install", "rm ", "apt", "npm install", "del ", "uninstall", "Remove-Item")


def _make_runner(responses):
    """responses: list of (matcher(cmd)->bool, result_dict). First match wins;
    default is a not_found result."""
    calls: list[list[str]] = []

    def runner(cmd):
        calls.append(list(cmd))
        for matcher, resp in responses:
            if matcher(cmd):
                return resp
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": "not_found"}

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _ok(stdout: str):
    return {"ok": True, "returncode": 0, "stdout": stdout, "stderr": ""}


def _not_found():
    return {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": "not_found"}


def _has(token):
    return lambda cmd: any(token in part for part in cmd)


# ── OpenClaw (host binary + Node companion) ─────────────────────────────────────

def test_openclaw_present_with_node24():
    runner = _make_runner([
        (lambda c: c[:1] == ["openclaw"], _ok("openclaw 1.4.0")),
        (lambda c: c[:1] == ["node"], _ok("v24.2.0")),
    ])
    det = detect_runtime("openclaw", VAULT_ROOT, runner=runner)
    assert det["supported"] is True
    assert det["present"] is True
    assert det["status"] == STATUS_PRESENT
    assert det["version"] == "1.4.0"
    assert det["companion"]["name"] == "node"
    assert det["companion"]["present"] is True
    assert det["companion"]["version_ok"] is True


def test_openclaw_absent():
    runner = _make_runner([])  # everything → not_found
    det = detect_runtime("openclaw", VAULT_ROOT, runner=runner)
    assert det["present"] is False
    assert det["status"] == STATUS_ABSENT


def test_openclaw_present_but_node_too_old():
    runner = _make_runner([
        (lambda c: c[:1] == ["openclaw"], _ok("openclaw 1.4.0")),
        (lambda c: c[:1] == ["node"], _ok("v18.19.0")),
    ])
    det = detect_runtime("openclaw", VAULT_ROOT, runner=runner)
    assert det["present"] is True  # openclaw itself is present
    assert det["companion"]["present"] is True
    assert det["companion"]["version_ok"] is False
    assert det["companion"]["status"] == STATUS_WRONG_VERSION


# ── Hermes (WSL binary) ─────────────────────────────────────────────────────────

def test_hermes_present_via_wsl():
    runner = _make_runner([
        (_has("hermes --version"), _ok("hermes 0.9.1")),
    ])
    det = detect_runtime("hermes", VAULT_ROOT, runner=runner)
    assert det["platform"] == "wsl"
    assert det["present"] is True
    assert det["status"] == STATUS_PRESENT
    # the probe must go through WSL, not the Windows host
    assert any(call[:1] == ["wsl"] for call in runner.calls)


def test_hermes_absent():
    runner = _make_runner([])
    det = detect_runtime("hermes", VAULT_ROOT, runner=runner)
    assert det["present"] is False
    assert det["status"] == STATUS_ABSENT


# ── Fail-closed behavior ────────────────────────────────────────────────────────

def test_unknown_runtime_is_unsupported_and_unknown():
    runner = _make_runner([])
    det = detect_runtime("totally-made-up", VAULT_ROOT, runner=runner)
    assert det["supported"] is False
    assert det["status"] == STATUS_UNKNOWN
    assert det["present"] is False


def test_timeout_or_error_is_unknown_not_absent():
    runner = _make_runner([
        (lambda c: c[:1] == ["openclaw"], {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": "timeout"}),
    ])
    det = detect_runtime("openclaw", VAULT_ROOT, runner=runner)
    assert det["present"] is False
    assert det["status"] == STATUS_UNKNOWN


def test_bad_runner_result_is_unknown():
    def runner(cmd):
        return "not-a-dict"  # misbehaving injected runner
    det = detect_runtime("openclaw", VAULT_ROOT, runner=runner)  # type: ignore[arg-type]
    assert det["status"] == STATUS_UNKNOWN


def test_probe_one_wrong_version_path():
    runner = _make_runner([(lambda c: True, _ok("v18.0.0"))])
    probe = _probe_one(runner, ["node", "--version"], min_major=24)
    assert probe["present"] is True
    assert probe["version_ok"] is False
    assert probe["status"] == STATUS_WRONG_VERSION


# ── Governance: read-only, no host mutation ────────────────────────────────────

def test_detection_is_read_only_and_issues_only_probe_commands():
    runner = _make_runner([
        (lambda c: c[:1] == ["openclaw"], _ok("openclaw 1.4.0")),
        (lambda c: c[:1] == ["node"], _ok("v24.2.0")),
    ])
    det = detect_runtime("openclaw", VAULT_ROOT, runner=runner)
    assert det["authority"]["read_only"] is True
    assert det["authority"]["host_mutation_performed"] is False
    # every issued command is a read-only probe — no install/remove tokens
    for call in runner.calls:
        joined = " ".join(call)
        assert not any(tok in joined for tok in _MUTATION_TOKENS), joined


# ── Doctor integration ──────────────────────────────────────────────────────────

def test_doctor_omits_binary_presence_by_default():
    report = build_runtime_doctor_report(VAULT_ROOT, runtime_id="openclaw", probe_processes=False)
    runtime0 = report["runtimes"][0]
    assert "binary_presence" not in runtime0["checks"]


def test_doctor_includes_binary_presence_when_requested():
    runner = _make_runner([
        (lambda c: c[:1] == ["openclaw"], _ok("openclaw 1.4.0")),
        (lambda c: c[:1] == ["node"], _ok("v24.2.0")),
    ])
    report = build_runtime_doctor_report(
        VAULT_ROOT, runtime_id="openclaw", probe_binaries=True, runner=runner
    )
    check = report["runtimes"][0]["checks"]["binary_presence"]
    assert check["ok"] is True  # informational — does not flip posture
    assert check["present"] is True
    assert check["status"] == STATUS_PRESENT


def test_doctor_binary_absent_does_not_flip_posture():
    runner = _make_runner([])  # absent
    report = build_runtime_doctor_report(
        VAULT_ROOT, runtime_id="openclaw", probe_binaries=True, runner=runner
    )
    check = report["runtimes"][0]["checks"]["binary_presence"]
    assert check["ok"] is True
    assert check["present"] is False
    assert check["status"] == STATUS_ABSENT
