"""ChaseOS Core/Personal export boundary tools.

The initial surface is dry-run only: it plans and scans a generated Core export
without creating the export target or initializing Git.
"""

from .exporter import build_dry_run_report

__all__ = ["build_dry_run_report"]
