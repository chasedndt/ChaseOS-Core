"""ChaseOS Installer — preflight-gated system setup + Studio access.

This package prepares (never auto-runs) a real ChaseOS install:

- ``config_templates.py`` — the Core-only config files the installer ships
  (first-run config, Studio config, env template). No secrets, ever.
- ``bootstrap.py``        — generates the multi-stage setup scripts (Windows
  stage-1 WSL2 + Ubuntu stage-2 ChaseOS install + Studio launch), assembles the
  full install bundle (gated by the install-safety preflight), and renders/writes
  it to vault artifacts.

Doctrine: the installer is **read-only against the host**. It inspects, gates,
generates scripts/config, and writes vault artifacts. It NEVER executes a host
mutation — the operator reviews and runs the emitted scripts. See
``06_AGENTS/ChaseOS-Installer-and-Distribution-Architecture.md`` and
``06_AGENTS/Install-Safety-Preflight-Architecture.md``.
"""

from __future__ import annotations

from runtime.installer.config_templates import (  # noqa: E402
    build_config_templates,
)
from runtime.installer.bootstrap import (  # noqa: E402
    EDITION_OPENCORE,
    EDITION_FULL,
    VALID_EDITIONS,
    DEFAULT_OPENCORE_REPO,
    PRODUCT_DOMAIN,
    prepare_install_bundle,
    write_install_bundle,
    render_install_bundle_text,
    build_windows_stage1,
    build_ubuntu_stage2,
)

__all__ = [
    "EDITION_OPENCORE",
    "EDITION_FULL",
    "VALID_EDITIONS",
    "DEFAULT_OPENCORE_REPO",
    "PRODUCT_DOMAIN",
    "build_config_templates",
    "prepare_install_bundle",
    "write_install_bundle",
    "render_install_bundle_text",
    "build_windows_stage1",
    "build_ubuntu_stage2",
]
