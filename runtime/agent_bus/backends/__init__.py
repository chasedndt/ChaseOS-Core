"""
backends/__init__.py — Agent Bus Backend Package

Exports the BusBackend contract and the SQLiteBackend implementation.
Use backend_loader.get_backend(vault_root) to obtain the configured backend —
do not instantiate backends directly.
"""

from .base import BusBackend, BackendInitError
from .sqlite_backend import SQLiteBackend

__all__ = ["BusBackend", "BackendInitError", "SQLiteBackend"]
