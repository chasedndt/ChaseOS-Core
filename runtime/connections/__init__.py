"""ChaseOS Connections registry primitives.

Connections are first-class local-first OS resources: provider manifests,
permission profiles, capability discovery, local registry state, and audit-ready
SQLite storage. This package intentionally does not authenticate to providers or
perform network calls; provider adapters and MCP clients sit behind these
contracts.
"""

from runtime.connections.manifests import DEFAULT_MANIFEST_DIR, list_provider_manifests, load_manifest
from runtime.connections.store import DEFAULT_DB_RELATIVE_PATH, init_store, registry_overview

__all__ = [
    "DEFAULT_DB_RELATIVE_PATH",
    "DEFAULT_MANIFEST_DIR",
    "init_store",
    "list_provider_manifests",
    "load_manifest",
    "registry_overview",
]
