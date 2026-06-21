"""
backend_loader.py — Agent Bus Backend Loader
============================================

Reads bus_config.yaml from the vault, instantiates the correct BusBackend
implementation, and caches it per vault_root for the process lifetime.

WHY CACHING PER VAULT ROOT
---------------------------
Most ChaseOS processes operate on a single vault. Caching by vault_root means:
  - The SQLite connection setup (schema apply, WAL mode) happens once per process
  - Tests can use multiple temp vaults in parallel without interference
  - A future multi-vault setup (e.g. running ChaseOS against two vaults in one
    process) works correctly without shared state

WHY NOT A MODULE-LEVEL SINGLETON
----------------------------------
A module-level singleton would break tests that use different temp vault dirs.
Per-vault caching is the correct granularity.

CONFIG FILE LOCATION
--------------------
{vault_root}/runtime/agent_bus/bus_config.yaml

If the file is missing, mode defaults to 'local' (SQLiteBackend). This means
existing vaults without bus_config.yaml continue to work unchanged.

ADDING A NEW BACKEND
--------------------
1. Subclass BusBackend in backends/<your_backend>.py
2. Implement all abstract methods
3. Add a branch in _instantiate_backend() below
4. Add your config block to bus_config.yaml
5. Document in Agent-Bus-Backend-Architecture.md
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .backends.base import BusBackend, BackendInitError

# Per-vault backend cache. Thread-safe via _cache_lock.
_cache: dict[Path, BusBackend] = {}
_cache_lock = threading.Lock()

# Default config when bus_config.yaml is absent
_DEFAULT_CONFIG: dict[str, Any] = {"mode": "local", "local": {}}


def _read_bus_config(vault_root: Path) -> dict[str, Any]:
    """Read bus_config.yaml from the vault. Returns default config if absent."""
    config_path = vault_root / "runtime" / "agent_bus" / "bus_config.yaml"
    if not config_path.exists():
        return _DEFAULT_CONFIG.copy()
    try:
        import yaml  # PyYAML is a ChaseOS dependency
        with config_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return _DEFAULT_CONFIG.copy()
        return data
    except Exception:
        # Unreadable or invalid config — fall back to local mode rather than
        # crashing the entire bus. A bad config file should not prevent local
        # workflows from running.
        return _DEFAULT_CONFIG.copy()


def _instantiate_backend(vault_root: Path, config: dict[str, Any]) -> BusBackend:
    """Instantiate the backend declared in config. Fail-closed on unknown mode."""
    mode = config.get("mode", "local")

    if mode == "local":
        from .backends.sqlite_backend import SQLiteBackend
        local_cfg = config.get("local") or {}
        backend = SQLiteBackend(vault_root, local_cfg)
        backend.init()
        return backend

    if mode == "server":
        # Server backend is Phase 10. The stub raises a clear, actionable error
        # rather than silently failing or falling back to local mode.
        # Falling back silently would mean a mis-configured server deployment
        # creates tasks in a local SQLite file that the remote server never sees.
        raise NotImplementedError(
            "ChaseOS agent bus server mode is not yet implemented (Phase 10). "
            "To use a server backend: wait for Phase 10, or set mode: local in "
            "runtime/agent_bus/bus_config.yaml to continue using SQLite."
        )

    raise ValueError(
        f"Unknown agent bus mode: {mode!r}. "
        f"Valid modes: 'local', 'server'. "
        f"Check runtime/agent_bus/bus_config.yaml."
    )


def get_backend(vault_root: Path | None) -> BusBackend:
    """Return the cached BusBackend for vault_root. Instantiates on first call.

    vault_root=None is treated as the repo root (backward compat with legacy
    bus.py callers that passed None for the real vault root).

    Thread-safe. Multiple threads calling get_backend() for the same vault_root
    concurrently will all receive the same backend instance.
    """
    from pathlib import Path as _Path
    if vault_root is None:
        # Legacy: derive vault root from module location
        vault_root = Path(__file__).resolve().parents[2]
    resolved = _Path(vault_root).resolve()

    with _cache_lock:
        if resolved not in _cache:
            config = _read_bus_config(resolved)
            _cache[resolved] = _instantiate_backend(resolved, config)
        return _cache[resolved]


def clear_backend_cache(vault_root: Path | None = None) -> None:
    """Remove cached backend(s). Used in tests to force re-instantiation.

    vault_root=None clears ALL cached backends. Provide vault_root to clear
    only that vault's backend (e.g. after changing bus_config.yaml in tests).
    """
    with _cache_lock:
        if vault_root is None:
            _cache.clear()
        else:
            resolved = Path(vault_root).resolve()
            _cache.pop(resolved, None)
