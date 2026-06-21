"""Core-only config templates the ChaseOS installer ships.

`build_config_templates()` returns a {relative_path: content} map of the config
files a fresh install needs. These are **Core** (safe to commit / publish): they
contain NO secrets. Credentials live only in a local ``.env`` the operator fills in
(see the generated ``.env.example``); runtimes resolve their own provider keys via
their ``model_config.yaml`` — ChaseOS itself never calls a provider directly.
"""

from __future__ import annotations

from typing import Optional


def _first_run_config(edition: str, vault_root: str) -> str:
    return f"""# ChaseOS first-run configuration ({edition} edition)
# Safe to commit: contains NO secrets. Credentials go in .env (see .env.example).
edition: {edition}
vault_root: "{vault_root}"

studio:
  # PyWebView desktop shell is the primary surface; 8772 is the web fallback.
  web_fallback_port: 8772
  open_on_launch: true

runtime:
  default_adapter: openclaw
  # Provider calls are opt-in per runtime via that runtime's model_config.yaml.
  # ChaseOS never calls a model provider directly (provider-agnostic routing).
  provider_calls_enabled: false

install_safety:
  # No main-disk mutation without explicit Advanced Install + safety checklist.
  main_disk_mutation_default: false

telemetry:
  enabled: false
"""


def _studio_config() -> str:
    return """# ChaseOS Studio configuration (Core defaults)
shell:
  dev_mode: false
  web_fallback_port: 8772
graph:
  engine: 3d-force-graph
  scan_node_limit: 2000
approvals:
  # Write actions are approval-gated by the Studio service layer.
  require_approval_for_writes: true
"""


def _env_example() -> str:
    return """# ChaseOS environment template — COPY to `.env` and fill in locally.
# NEVER commit your real .env. Every value here is OPTIONAL and provider-agnostic:
# each runtime resolves the credentials it needs via its own model_config.yaml.

# --- LLM / provider credentials (only the ones your runtimes use) ---
# OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
# XAI_API_KEY=
# PERPLEXITY_API_KEY=

# --- Optional delivery surfaces (only if you wire them) ---
# DISCORD_WEBHOOK_URL=
# STRIKEZONE_DISCORD_WEBHOOK_URL=
# WHOP_API_KEY=

# --- Optional runtimes ---
# N8N_BASE_URL=
# N8N_API_KEY=
"""


def build_config_templates(
    *,
    edition: str = "opencore",
    vault_root: str = "~/chaseos-vault",
) -> dict:
    """Return the Core-only config files a fresh install ships with.

    Returns:
        {relative_path: file_content}. All Core, no secrets.
    """
    return {
        "config/chaseos.config.yaml": _first_run_config(edition, vault_root),
        "config/studio.config.yaml": _studio_config(),
        ".env.example": _env_example(),
    }
