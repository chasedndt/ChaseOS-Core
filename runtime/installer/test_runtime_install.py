"""Tests for the emit-only runtime install-script generator (Stage 3).

Reuses the install-safety probe facts pattern from test_installer.py. Emit-only:
these tests assemble bundles and write them to a tmp dir — never a host mutation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from runtime.install_safety.probe import probe_host
from runtime.installer.runtime_install import (
    build_runtime_install_bundle,
    render_runtime_install_bundle_text,
    write_runtime_install_bundle,
)


def _host_json(**overrides) -> str:
    base = {
        "vendor": "Dell", "model": "XPS", "cpu": "i7", "ram_bytes": 17179869184,
        "physical_disks": [{
            "number": 0, "name": "OS", "media": "SSD", "bus": "NVMe", "size": 512 * 1024**3,
            "partition_style": "GPT", "is_system": True, "is_boot": True, "removable": False,
            "health": "Healthy", "free": 100 * 1024**3,
            "partition_count": 3, "primary_count": 3, "recovery_count": 0,
        }],
        "esp": [{"disk": 0, "size": 260 * 1024**2}],
        "controllers": ["Standard NVM Express Controller"],
        "firmware": "Uefi", "secure_boot": True, "bitlocker": "Off", "tpm_present": True,
        "fast_startup": False, "dirty_bit": False, "pending_reboot": False,
        "os_drive": "C", "os_shrinkable": 200 * 1024**3,
        "wsl_available": True, "wsl_distros": ["Ubuntu"], "wsl_version": 2,
    }
    base.update(overrides)
    return json.dumps(base)


def _runner(s):
    return lambda _script: s


def _ready_facts():
    return probe_host(runner=_runner(_host_json()), system="Windows")


_SECRET_ASSIGN = re.compile(r"(TOKEN|API_KEY|SECRET|PASSWORD)\w*\s*=\s*\S")


def _assert_no_live_secrets(bundle: dict) -> None:
    blob = "\n".join((bundle.get("files") or {}).values())
    assert "sk-" not in blob
    for line in blob.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not _SECRET_ASSIGN.search(stripped), f"live secret assignment leaked: {stripped!r}"


# ── Hermes (WSL, preflight-gated) ───────────────────────────────────────────────

def test_hermes_bundle_ready_has_all_files():
    bundle = build_runtime_install_bundle("hermes", facts=_ready_facts())
    assert bundle["status"] == "ready"
    assert bundle["platform"] == "wsl"
    files = bundle["files"]
    assert "install-hermes-stage1.ps1" in files
    assert "install-hermes-stage2.sh" in files
    assert "INSTALL-hermes.md" in files


def test_hermes_bundle_blocked_emits_no_scripts():
    facts = _ready_facts()
    facts["disks"][0]["health"] = "Unhealthy"  # hard-blocks any intent
    bundle = build_runtime_install_bundle("hermes", facts=facts)
    assert bundle["status"] == "blocked"
    assert bundle["files"] == {}


def test_hermes_stage2_creates_home_and_marks_install_placeholder():
    bundle = build_runtime_install_bundle("hermes", facts=_ready_facts())
    stage2 = bundle["files"]["install-hermes-stage2.sh"]
    assert "runtimes/hermes-home" in stage2
    assert "hermes-install-command-here" in stage2  # honest placeholder, not fabricated
    assert "command -v hermes" in stage2


# ── OpenClaw (Windows host) ─────────────────────────────────────────────────────

def test_openclaw_bundle_ready_has_all_files():
    bundle = build_runtime_install_bundle("openclaw", facts=_ready_facts())
    assert bundle["status"] == "ready"
    assert bundle["platform"] == "windows"
    assert "install-openclaw.ps1" in bundle["files"]
    assert "INSTALL-openclaw.md" in bundle["files"]


def test_openclaw_script_ensures_node24_and_workspace():
    bundle = build_runtime_install_bundle("openclaw", facts=_ready_facts())
    script = bundle["files"]["install-openclaw.ps1"]
    assert "node --version" in script
    assert ".openclaw" in script
    assert "openclaw-install-command-here" in script  # honest placeholder


# ── Unsupported ─────────────────────────────────────────────────────────────────

def test_unsupported_runtime_emits_no_scripts():
    bundle = build_runtime_install_bundle("made-up-runtime", facts=_ready_facts())
    assert bundle["status"] == "unsupported"
    assert bundle["files"] == {}


# ── Governance: no secrets, emit-only, review-first ─────────────────────────────

def test_no_live_secrets_in_any_runtime_bundle():
    for rid in ("hermes", "openclaw"):
        _assert_no_live_secrets(build_runtime_install_bundle(rid, facts=_ready_facts()))


_CURL_PIPE = re.compile(r"curl[^\n]*\|\s*(bash|sh)\b")


def test_bundles_are_read_only_and_emit_only():
    for rid in ("hermes", "openclaw"):
        bundle = build_runtime_install_bundle(rid, facts=_ready_facts())
        assert bundle["read_only"] is True
        assert bundle["authority"]["host_mutation_performed"] is False
        assert bundle["authority"]["executes_scripts"] is False
        # the note states the review-first policy explicitly
        assert "Review" in bundle["web_install_note"]
        # no emitted script actually pipes a remote download straight into a shell
        blob = "\n".join((bundle.get("files") or {}).values())
        assert not _CURL_PIPE.search(blob)


def test_write_runtime_install_bundle_writes_files(tmp_path):
    bundle = build_runtime_install_bundle("openclaw", facts=_ready_facts())
    written = write_runtime_install_bundle(bundle, tmp_path / "openclaw")
    assert written
    for path in written:
        assert Path(path).is_file()
    assert (tmp_path / "openclaw" / "install-openclaw.ps1").is_file()


def test_render_text_summarizes_ready_bundle():
    text = render_runtime_install_bundle_text(
        build_runtime_install_bundle("hermes", facts=_ready_facts())
    )
    assert "Hermes".lower() in text.lower()
    assert "install-hermes-stage2.sh" in text
