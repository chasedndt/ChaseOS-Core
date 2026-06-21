"""Emit-only install-script generator for runtime agents — Stage 3 of the
activation contract.

Mirrors the ChaseOS installer doctrine (``runtime/installer/bootstrap.py``):
read-only against the host, review-first, **never** executes a host mutation. It
assembles a per-runtime bundle of reviewable scripts (and writes them to vault/
dir artifacts on request); the operator reviews and runs them. No secrets are
ever emitted — only commented placeholders.

ChaseOS does not vendor third-party runtime binaries. The emitted scripts ensure
prerequisites and the runtime home, and mark a clearly-commented placeholder
where the operator drops the official Hermes / OpenClaw install command.

See ``06_AGENTS/Runtime-Setup-and-Activation-Architecture.md`` (Stage 3) and the
per-runtime activation runbooks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from runtime.install_safety.preflight import run_preflight
from runtime.installer.bootstrap import build_windows_stage1


_REVIEW_FIRST = (
    "Review every emitted script before running it. ChaseOS does not run them for "
    "you, and never pipes a remote script straight into a shell (no curl | bash)."
)


RUNTIME_INSTALL_TARGETS: dict[str, dict[str, Any]] = {
    "hermes": {
        "platform": "wsl",
        "uninstall": (
            "Remove ~/.local/bin/hermes and ~/runtimes/hermes-home inside WSL; "
            "full teardown: wsl --unregister Ubuntu."
        ),
    },
    "openclaw": {
        "platform": "windows",
        "uninstall": "Uninstall OpenClaw per its docs and remove %USERPROFILE%\\.openclaw.",
    },
}


def _authority() -> dict[str, bool]:
    return {
        "read_only": True,
        "host_mutation_performed": False,
        "executes_scripts": False,
        "writes_secrets": False,
        "provider_calls_performed": False,
    }


def _hermes_stage2() -> str:
    return """#!/usr/bin/env bash
# ChaseOS Runtime Install - Hermes (Stage 2 of 2, inside Ubuntu/WSL)
# REVIEW before running. Read-only to your Windows host. Emit-only: ChaseOS did
# NOT run this for you.
set -euo pipefail

echo "[1/3] Ensuring ~/.local/bin is on PATH..."
mkdir -p "$HOME/.local/bin"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) : ;;
  *) echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.profile" ;;
esac

echo "[2/3] Install the Hermes runtime binary..."
# ChaseOS does NOT vendor third-party runtime binaries. Install Hermes per its own
# documentation so that `hermes` resolves on your WSL PATH (e.g. ~/.local/bin/hermes).
# Replace the placeholder below with the official Hermes install command:
#   <hermes-install-command-here>
if command -v hermes >/dev/null 2>&1; then
  echo "  hermes found: $(command -v hermes)"
else
  echo "  hermes NOT installed yet - add the official install command above, then re-run"
fi

echo "[3/3] Creating the private Hermes home (no secrets)..."
HERMES_HOME="${CHASEOS_HERMES_HOME:-${HERMES_HOME:-$HOME/runtimes/hermes-home}}"
mkdir -p "$HERMES_HOME"
if [ ! -f "$HERMES_HOME/.env" ]; then
  cat > "$HERMES_HOME/.env" <<'ENV'
# Hermes private home env - fill these in YOURSELF. ChaseOS never stores secrets.
# Discord gateway (leave commented until you set real values):
# HERMES_DISCORD_BOT_TOKEN=
# GATEWAY_ALLOWED_USERS=
# Model provider is provider-agnostic - Hermes resolves its own model/keys via
# model_config.yaml. Do NOT hardcode provider keys here.
ENV
  echo "  wrote $HERMES_HOME/.env (placeholders only)"
fi

echo ""
echo "Hermes runtime files prepared. Back in ChaseOS:"
echo "  chaseos runtime activate --runtime hermes --dry-run --json   # resume the checklist"
echo "  chaseos runtime hermes-gateway-config --action status        # gateway readiness"
"""


def _hermes_guide() -> str:
    return """# Hermes Runtime Install (WSL)

> Emit-only. Review each script, then run it yourself. See
> `06_AGENTS/Hermes-Activation-Runbook.md` for the full 7-stage procedure.

## Stage 1 - Windows (PowerShell): ensure WSL2 Ubuntu (reversible)

    ./install-hermes-stage1.ps1

(If Ubuntu is already installed this is a no-op. Reversible: `wsl --unregister Ubuntu`.)

## Stage 2 - inside Ubuntu

    wsl -d Ubuntu
    bash install-hermes-stage2.sh

This ensures `~/.local/bin` is on PATH, marks where to drop the official Hermes
install command, and creates the private Hermes home `~/runtimes/hermes-home/.env`
(placeholders only - you add real values yourself; ChaseOS never stores secrets).

## Then, back in ChaseOS

    chaseos runtime activate --runtime hermes --dry-run --json

## Uninstall
Remove `~/.local/bin/hermes` and `~/runtimes/hermes-home`; full teardown:
`wsl --unregister Ubuntu`.
"""


def _openclaw_script() -> str:
    return """# ChaseOS Runtime Install - OpenClaw (Windows, Node 24)
# REVIEW before running. Emit-only: ChaseOS did NOT run this for you.

# [1/3] Ensure Node 24 is present (winget shown; nvm-windows is an alternative)
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
  Write-Host "Node not found. Install Node 24, e.g.:"
  Write-Host "  winget install OpenJS.NodeJS.LTS"
} else {
  node --version
}

# [2/3] Install the OpenClaw runtime
# ChaseOS does NOT vendor third-party runtime binaries. Install OpenClaw per its
# own documentation so that `openclaw` resolves on your PATH.
# Replace the placeholder below with the official OpenClaw install command:
#   <openclaw-install-command-here>
if (Get-Command openclaw -ErrorAction SilentlyContinue) {
  openclaw --version
} else {
  Write-Host "openclaw NOT installed yet - add the official install command above"
}

# [3/3] Create the OpenClaw workspace home (no secrets)
$ocHome = Join-Path $env:USERPROFILE ".openclaw"
New-Item -ItemType Directory -Force -Path $ocHome | Out-Null
Write-Host "OpenClaw home: $ocHome"
Write-Host ""
Write-Host "Back in ChaseOS:"
Write-Host "  chaseos runtime activate --runtime openclaw --dry-run --json"
"""


def _openclaw_guide() -> str:
    return """# OpenClaw Runtime Install (Windows)

> Emit-only. Review the script, then run it yourself. See
> `06_AGENTS/OpenClaw-Activation-Runbook.md` for the full 7-stage procedure.

## Install

    ./install-openclaw.ps1

This ensures Node 24 is present, marks where to drop the official OpenClaw install
command, and creates the OpenClaw workspace home `%USERPROFILE%\\.openclaw`. It
writes no secrets.

## Then, back in ChaseOS

    chaseos runtime activate --runtime openclaw --dry-run --json

## Uninstall
Uninstall OpenClaw per its docs and remove `%USERPROFILE%\\.openclaw`.
"""


def build_runtime_install_bundle(
    runtime_id: str,
    *,
    facts: Optional[dict] = None,
    runner=None,
    system: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble an emit-only install bundle for ``runtime_id``.

    For WSL-hosted runtimes (Hermes) the bundle is gated by the install-safety
    coexist preflight; if blocked, no scripts are emitted. Read-only: nothing
    here mutates the host.
    """
    rid = str(runtime_id or "").strip().lower()
    target = RUNTIME_INSTALL_TARGETS.get(rid)
    if target is None:
        return {
            "runtime_id": rid,
            "read_only": True,
            "status": "unsupported",
            "platform": None,
            "files": {},
            "detail": f"No install profile for runtime '{rid}'.",
            "authority": _authority(),
        }

    if target["platform"] == "wsl":
        preflight = run_preflight(intent="vm", facts=facts, runner=runner, system=system)
        if preflight.get("decision") == "blocked":
            return {
                "runtime_id": rid,
                "read_only": True,
                "status": "blocked",
                "platform": "wsl",
                "files": {},
                "preflight": preflight,
                "uninstall": target["uninstall"],
                "web_install_note": _REVIEW_FIRST,
                "authority": _authority(),
            }
        pf_facts = preflight.get("facts", {})
        files = {
            "install-hermes-stage1.ps1": build_windows_stage1(pf_facts),
            "install-hermes-stage2.sh": _hermes_stage2(),
            "INSTALL-hermes.md": _hermes_guide(),
        }
        return {
            "runtime_id": rid,
            "read_only": True,
            "status": "ready",
            "platform": "wsl",
            "files": files,
            "preflight": preflight,
            "uninstall": target["uninstall"],
            "web_install_note": _REVIEW_FIRST,
            "authority": _authority(),
        }

    # Windows host runtime (OpenClaw) — no WSL preflight gate required.
    files = {
        "install-openclaw.ps1": _openclaw_script(),
        "INSTALL-openclaw.md": _openclaw_guide(),
    }
    return {
        "runtime_id": rid,
        "read_only": True,
        "status": "ready",
        "platform": "windows",
        "files": files,
        "preflight": None,
        "uninstall": target["uninstall"],
        "web_install_note": _REVIEW_FIRST,
        "authority": _authority(),
    }


def write_runtime_install_bundle(bundle: dict, out_dir: str | Path) -> list[str]:
    """Write every bundle file under ``out_dir``. Vault/dir artifacts only — never
    a host mutation. Returns the list of written paths."""
    base = Path(out_dir)
    written: list[str] = []
    for rel, content in (bundle.get("files") or {}).items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return written


def render_runtime_install_bundle_text(bundle: dict) -> str:
    """Human-readable summary of a runtime install bundle."""
    lines: list[str] = []
    add = lines.append
    add(f"ChaseOS Runtime Install - {bundle.get('runtime_id')} (read-only; nothing was changed)")
    add("=" * 68)
    add(f"Platform : {bundle.get('platform')}")
    add(f"Status   : {bundle.get('status')}")
    add("")
    if bundle.get("status") == "blocked":
        add("INSTALL BLOCKED by the safety preflight - resolve these first:")
        for b in (bundle.get("preflight", {}) or {}).get("blockers", []):
            add(f"  ! {b}")
        return "\n".join(lines).rstrip() + "\n"
    if bundle.get("status") == "unsupported":
        add(bundle.get("detail", "Unsupported runtime."))
        return "\n".join(lines).rstrip() + "\n"
    add("Bundle files prepared:")
    for rel in sorted(bundle.get("files") or {}):
        add(f"  - {rel}")
    add("")
    add(_REVIEW_FIRST)
    return "\n".join(lines).rstrip() + "\n"
