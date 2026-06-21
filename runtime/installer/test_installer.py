"""Tests for the ChaseOS installer bundle (prepare-only, preflight-gated)."""

from __future__ import annotations

import json
from pathlib import Path

from runtime.install_safety.probe import probe_host
from runtime.installer import (
    DEFAULT_OPENCORE_REPO,
    EDITION_OPENCORE,
    build_config_templates,
    prepare_install_bundle,
    render_install_bundle_text,
    write_install_bundle,
)
from runtime.installer.bootstrap import build_ubuntu_stage2, build_windows_stage1


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


# --- config templates ------------------------------------------------------

def test_config_templates_are_secret_free():
    t = build_config_templates(edition=EDITION_OPENCORE)
    assert "config/chaseos.config.yaml" in t
    assert ".env.example" in t
    blob = "\n".join(t.values())
    # No real secret values - only commented placeholders.
    assert "sk-" not in blob
    for line in blob.splitlines():
        if "API_KEY" in line:
            assert line.strip().startswith("#")  # all commented


def test_config_declares_provider_agnostic():
    t = build_config_templates()
    cfg = t["config/chaseos.config.yaml"]
    assert "provider_calls_enabled: false" in cfg
    assert "model_config.yaml" in cfg  # provider-agnostic note


# --- stage scripts ---------------------------------------------------------

def test_stage1_skips_when_ubuntu_present():
    facts = probe_host(runner=_runner(_host_json()), system="Windows")
    s1 = build_windows_stage1(facts)
    assert "already installed" in s1
    assert "wsl --unregister Ubuntu" in s1  # reversible note


def test_stage1_installs_when_absent():
    facts = probe_host(runner=_runner(_host_json(wsl_available=False, wsl_distros=[])),
                       system="Windows")
    s1 = build_windows_stage1(facts)
    assert "wsl --install -d Ubuntu" in s1


def test_stage2_clones_repo_and_launches_studio():
    s2 = build_ubuntu_stage2(edition="opencore", repo_url=DEFAULT_OPENCORE_REPO,
                             target_dir="~/chaseos")
    assert DEFAULT_OPENCORE_REPO in s2
    assert "python3 -m venv .venv" in s2
    assert "pip install -e ." in s2
    assert "chaseos studio shell" in s2
    assert "8772" in s2  # web fallback


# --- bundle assembly -------------------------------------------------------

def test_bundle_ready_has_all_files():
    facts = probe_host(runner=_runner(_host_json()), system="Windows")
    b = prepare_install_bundle(facts=facts)
    assert b["status"] == "ready"
    files = b["files"]
    assert "chaseos-install-stage1.ps1" in files
    assert "chaseos-install-stage2.sh" in files
    assert "INSTALL.md" in files
    assert "config/chaseos.config.yaml" in files
    assert b["repo_url"] == DEFAULT_OPENCORE_REPO


def test_bundle_blocked_when_preflight_blocks():
    # dirty bit forces a blocked preflight even on the coexist/vm path? No - dirty bit
    # only hard-blocks same-disk. Use a failing disk health (hard block any intent).
    facts = probe_host(runner=_runner(_host_json()), system="Windows")
    facts["disks"][0]["health"] = "Unhealthy"
    b = prepare_install_bundle(facts=facts)
    assert b["status"] == "blocked"
    assert b["files"] == {}


def test_bundle_custom_repo_and_target():
    facts = probe_host(runner=_runner(_host_json()), system="Windows")
    b = prepare_install_bundle(facts=facts, repo_url="https://github.com/me/fork",
                               target_dir="/opt/chaseos")
    assert b["repo_url"] == "https://github.com/me/fork"
    assert "https://github.com/me/fork" in b["files"]["chaseos-install-stage2.sh"]
    assert "/opt/chaseos" in b["files"]["chaseos-install-stage2.sh"]


def test_bundle_web_command_is_review_first_not_curl_bash():
    facts = probe_host(runner=_runner(_host_json()), system="Windows")
    b = prepare_install_bundle(facts=facts)
    cmd = b["web_install_command"]
    assert "git clone" in cmd
    assert "less chaseos-install-stage2.sh" in cmd  # review step
    assert "curl" not in cmd and "| bash" not in cmd  # no untrusted auto-install


def test_bundle_writes_files(tmp_path):
    facts = probe_host(runner=_runner(_host_json()), system="Windows")
    b = prepare_install_bundle(facts=facts)
    written = write_install_bundle(b, tmp_path)
    assert len(written) == len(b["files"])
    assert (tmp_path / "chaseos-install-stage2.sh").exists()
    assert (tmp_path / "config" / "chaseos.config.yaml").exists()


def test_bundle_render_text_smoke():
    facts = probe_host(runner=_runner(_host_json()), system="Windows")
    text = render_install_bundle_text(prepare_install_bundle(facts=facts))
    assert "Installer" in text
    assert "Launch Studio" in text
    assert "Review every script" in text


def test_inside_wsl_bundle_is_ready():
    facts = probe_host(system="Linux", wsl_marker=True)
    b = prepare_install_bundle(facts=facts)
    assert b["status"] == "ready"
    assert "chaseos-install-stage2.sh" in b["files"]
