"""ChaseOS installer bootstrap — multi-stage setup scripts + bundle assembly.

Generates the scripts and config a fresh ChaseOS install needs, gated by the
read-only install-safety preflight. Emit-only: nothing here runs a host mutation;
the operator reviews and runs the scripts. See the package docstring for doctrine.

Flow the bundle implements (the safe, fully-reversible coexist path):
    Stage 1 (Windows PowerShell) : ensure WSL2 + Ubuntu (reversible)
    Stage 2 (Ubuntu bash)        : install OpenCore + deps + launch Studio
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from runtime.install_safety.preflight import run_preflight
from runtime.installer.config_templates import build_config_templates

# Canonical installer constants (re-exported by the package __init__).
EDITION_OPENCORE = "opencore"
EDITION_FULL = "full"
VALID_EDITIONS = (EDITION_OPENCORE, EDITION_FULL)
DEFAULT_OPENCORE_REPO = "https://github.com/chaseos/opencore"
PRODUCT_DOMAIN = "https://chaseos.ai"
DEFAULT_TARGET_DIR = "~/chaseos"


def build_windows_stage1(facts: dict) -> str:
    """Stage 1 (Windows PowerShell): ensure WSL2 Ubuntu. Fully reversible."""
    available = facts.get("wsl_available")
    ubuntu = facts.get("wsl_ubuntu_present")
    lines = [
        "# ChaseOS Installer - Stage 1 of 2 (Windows): ensure WSL2 + Ubuntu",
        "# REVIEW before running. Fully reversible:  wsl --unregister Ubuntu",
        "# This does NOT touch your partitions or your Windows install.",
        "",
    ]
    if available and ubuntu:
        lines += [
            "# WSL Ubuntu is already installed - nothing to do in Stage 1.",
            "wsl -l -v",
        ]
    else:
        lines += [
            "wsl --install -d Ubuntu",
            "# Reboot if prompted, complete the Ubuntu first-run user setup, then:",
        ]
    lines += [
        "",
        "# Then run Stage 2 INSIDE Ubuntu:",
        "#   wsl -d Ubuntu",
        "#   bash chaseos-install-stage2.sh",
    ]
    return "\n".join(lines) + "\n"


def build_ubuntu_stage2(*, edition: str, repo_url: str, target_dir: str) -> str:
    """Stage 2 (Ubuntu bash): install ChaseOS + deps and launch Studio."""
    return f"""#!/usr/bin/env bash
# ChaseOS Installer - Stage 2 of 2 (Ubuntu): install {edition} + launch Studio
# REVIEW before running. Read-only to your Windows host; installs into {target_dir}.
set -euo pipefail

echo "[1/5] Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git

echo "[2/5] Fetching ChaseOS ({edition})..."
if [ ! -d "{target_dir}/.git" ]; then
  git clone "{repo_url}" "{target_dir}"
fi
cd "{target_dir}"

echo "[3/5] Creating virtual environment..."
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e .

echo "[4/5] Applying first-run config (edit config/.env with your own keys)..."
[ -f .env ] || cp .env.example .env 2>/dev/null || true

echo "[5/5] Verifying install..."
chaseos doctor || true

echo ""
echo "ChaseOS is installed. Launch Studio:"
echo "  chaseos studio shell                  # desktop shell (primary surface)"
echo "  # or the web fallback at http://localhost:8772"
echo ""
echo "To remove everything: rm -rf \\"{target_dir}\\"  (and: wsl --unregister Ubuntu)"
"""


def _install_guide(*, edition: str, repo_url: str, target_dir: str, status: str) -> str:
    return f"""# ChaseOS Install Guide ({edition})

> Safest path: ChaseOS runs inside WSL2 Ubuntu — fully reversible, your Windows
> install and partitions are never touched. Preflight status: **{status}**.

## Before you start
Run the safety preflight (read-only):

    chaseos install-safety preflight

If it reports BLOCKED, resolve the blockers first — do not proceed.

## Stage 1 — Windows (PowerShell)
Review then run:

    ./chaseos-install-stage1.ps1

(If WSL Ubuntu is already installed, Stage 1 is a no-op.)

## Stage 2 — inside Ubuntu

    wsl -d Ubuntu
    bash chaseos-install-stage2.sh

This installs ChaseOS ({edition}) from `{repo_url}` into `{target_dir}`,
creates a virtualenv, and verifies the install.

## Launch Studio

    chaseos studio shell        # desktop shell (primary surface)
    # or open the web fallback at http://localhost:8772

## Configuration
- `config/chaseos.config.yaml` — first-run config (no secrets, safe to commit).
- `config/studio.config.yaml`  — Studio defaults.
- `.env` — copy from `.env.example`; put your own provider keys here. ChaseOS never
  calls a provider directly — each runtime resolves its own keys via model_config.yaml.

## Uninstall (fully reversible)
    rm -rf "{target_dir}"
    wsl --unregister Ubuntu     # only if you no longer want the Ubuntu distro
"""


def _web_install_command(repo_url: str) -> str:
    """A review-first web install hint (NOT curl|bash — no untrusted auto-install)."""
    return (
        f"# Review-first install (see {PRODUCT_DOMAIN}/install):\n"
        f"#   1. git clone {repo_url} && cd opencore\n"
        f"#   2. less chaseos-install-stage2.sh    # read it before running\n"
        f"#   3. bash chaseos-install-stage2.sh"
    )


def prepare_install_bundle(
    *,
    edition: str = EDITION_OPENCORE,
    repo_url: Optional[str] = None,
    target_dir: str = DEFAULT_TARGET_DIR,
    vault_root: str = "~/chaseos-vault",
    backup_verified: Optional[bool] = None,
    recovery_usb: Optional[bool] = None,
    facts: Optional[dict] = None,
    runner=None,
    system: Optional[str] = None,
) -> dict:
    """Assemble a full install bundle, gated by the install-safety preflight.

    The bundle implements the safe coexist (WSL) path, so the preflight is run with
    the ``vm`` (coexist) intent. If the preflight is blocked, the bundle is blocked
    and no scripts are emitted.

    Returns a bundle dict: status, edition, repo_url, target_dir, files{}, preflight.
    """
    edition = edition if edition in VALID_EDITIONS else EDITION_OPENCORE
    repo_url = repo_url or DEFAULT_OPENCORE_REPO

    preflight = run_preflight(
        intent="vm",  # the installer's coexist/WSL path
        backup_verified=backup_verified, recovery_usb=recovery_usb,
        facts=facts, runner=runner, system=system,
    )
    facts = preflight.get("facts", {})
    blocked = preflight.get("decision") == "blocked"

    if blocked:
        return {
            "read_only": True,
            "status": "blocked",
            "edition": edition,
            "repo_url": repo_url,
            "target_dir": target_dir,
            "files": {},
            "web_install_command": _web_install_command(repo_url),
            "preflight": preflight,
        }

    files = dict(build_config_templates(edition=edition, vault_root=vault_root))
    files["chaseos-install-stage1.ps1"] = build_windows_stage1(facts)
    files["chaseos-install-stage2.sh"] = build_ubuntu_stage2(
        edition=edition, repo_url=repo_url, target_dir=target_dir)
    files["INSTALL.md"] = _install_guide(
        edition=edition, repo_url=repo_url, target_dir=target_dir, status="ready")

    return {
        "read_only": True,
        "status": "ready",
        "edition": edition,
        "repo_url": repo_url,
        "target_dir": target_dir,
        "vault_root": vault_root,
        "files": files,
        "web_install_command": _web_install_command(repo_url),
        "studio_launch": ["chaseos studio shell", "web fallback: http://localhost:8772"],
        "coexist": preflight.get("coexist"),
        "preflight": preflight,
    }


def write_install_bundle(bundle: dict, out_dir: str | Path) -> list[str]:
    """Write every bundle file under out_dir. Vault/dir artifacts only - never to a
    physical disk. Returns the list of written paths."""
    base = Path(out_dir)
    written: list[str] = []
    for rel, content in (bundle.get("files") or {}).items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return written


def render_install_bundle_text(bundle: dict) -> str:
    """Render an install bundle as a human-readable summary."""
    lines: list[str] = []
    add = lines.append
    add("ChaseOS Installer - Bundle Preparation (read-only; nothing was changed)")
    add("=" * 68)
    add(f"Edition   : {bundle.get('edition')}")
    add(f"Source    : {bundle.get('repo_url')}")
    add(f"Target    : {bundle.get('target_dir')}")
    add(f"Status    : {bundle.get('status')}")
    add("")

    if bundle.get("status") == "blocked":
        add("INSTALL BLOCKED by the safety preflight - resolve these first:")
        for b in bundle.get("preflight", {}).get("blockers", []):
            add(f"  ! {b}")
        coexist = (bundle.get("preflight", {}) or {}).get("coexist") or {}
        if coexist.get("recommended_action"):
            add("")
            add(f"Safest path: {coexist['recommended_action']}")
        return "\n".join(lines).rstrip() + "\n"

    add("Bundle files prepared:")
    for rel in sorted(bundle.get("files") or {}):
        add(f"  - {rel}")
    add("")
    add("Launch Studio after install:")
    for cmd in bundle.get("studio_launch") or []:
        add(f"  {cmd}")
    add("")
    add("Web install (review-first):")
    for line in (bundle.get("web_install_command") or "").splitlines():
        add(f"  {line}")
    add("")
    add("Review every script before running it. ChaseOS does not run them for you.")
    return "\n".join(lines).rstrip() + "\n"
