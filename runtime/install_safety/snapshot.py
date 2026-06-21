"""Partition-table snapshot — the install-safety **undo map**.

`capture_partition_snapshot()` reads the current disk + partition layout (read-only)
and returns a structured record: the "what it looked like before" reference an
operator can use to recover from a failed install. It is the highest-trust feature
of the install-safety layer.

Strictly read-only against the host (PowerShell Storage queries only, fail-open).
The only write it ever performs is, optionally, persisting the snapshot record to a
**vault audit artifact** via ``write_snapshot()`` — never to a physical disk.

The subprocess collector is injectable (`runner=`) and the platform is overridable
(`system=`) so the module is fully testable without a live host.
"""

from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

# Read-only layout collector: disks and their partitions. No state is changed.
_SNAPSHOT_COLLECTOR = r"""
$ErrorActionPreference = 'SilentlyContinue'
$disks = @()
try {
  $disks = Get-Disk | ForEach-Object {
    $d = $_
    $parts = @()
    try {
      $parts = Get-Partition -DiskNumber $d.Number | ForEach-Object {
        [pscustomobject]@{
          number    = [int]$_.PartitionNumber
          drive     = "$($_.DriveLetter)"
          type      = "$($_.Type)"
          gpt_type  = "$($_.GptType)"
          size      = [int64]$_.Size
          offset    = [int64]$_.Offset
        }
      }
    } catch {}
    [pscustomobject]@{
      number          = [int]$d.Number
      name            = "$($d.FriendlyName)"
      bus             = "$($d.BusType)"
      size            = [int64]$d.Size
      partition_style = "$($d.PartitionStyle)"
      is_system       = [bool]$d.IsSystem
      is_boot         = [bool]$d.IsBoot
      partitions      = $parts
    }
  }
} catch {}
[pscustomobject]@{ disks = $disks } | ConvertTo-Json -Depth 6 -Compress
"""


def _default_runner(script: str) -> Optional[str]:
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": 60}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or "").strip()
    return out or None


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _gb(value: Any) -> Optional[float]:
    try:
        return round(int(value) / (1024**3), 1) if value not in (None, "", 0, "0") else None
    except (ValueError, TypeError):
        return None


def capture_partition_snapshot(
    *,
    runner: Optional[Callable[[str], Optional[str]]] = None,
    system: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> dict:
    """Capture the current partition layout. Read-only, fail-open.

    Args:
        runner: injectable PowerShell collector (returns stdout or None).
        system: platform override ("Windows"/"Linux"/"Darwin").
        timestamp: caller-supplied ISO timestamp for the record (the module does
            not call the clock itself, keeping it deterministic/testable).

    Returns:
        A snapshot record: ``{captured_at, platform, read_only, disks[], errors[]}``.
        Each disk carries its partitions (number, drive, type, size_gb, offset).
    """
    system = system or platform.system()
    record: dict[str, Any] = {
        "captured_at": timestamp,
        "platform": system,
        "read_only": True,
        "disks": [],
        "errors": [],
    }

    if system != "Windows":
        record["errors"].append(
            f"partition snapshot is currently Windows-only; platform '{system}' not inspected"
        )
        return record

    raw = (runner or _default_runner)(_SNAPSHOT_COLLECTOR)
    if not raw:
        record["errors"].append("partition collector returned no output (snapshot skipped)")
        return record
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        record["errors"].append("could not parse partition collector JSON output")
        return record

    for d in _as_list(data.get("disks")):
        if not isinstance(d, dict):
            continue
        partitions = []
        for p in _as_list(d.get("partitions")):
            if not isinstance(p, dict):
                continue
            partitions.append({
                "number": p.get("number"),
                "drive": (str(p.get("drive") or "").strip() or None),
                "type": (str(p.get("type") or "").strip() or None),
                "gpt_type": (str(p.get("gpt_type") or "").strip() or None),
                "size_gb": _gb(p.get("size")),
                "offset": p.get("offset"),
            })
        record["disks"].append({
            "number": d.get("number"),
            "name": (str(d.get("name") or "").strip() or "Unknown disk"),
            "bus_type": (str(d.get("bus") or "").strip() or "unknown"),
            "size_gb": _gb(d.get("size")),
            "partition_style": (str(d.get("partition_style") or "").strip() or None),
            "is_system_disk": bool(d.get("is_system")) or bool(d.get("is_boot")),
            "partitions": partitions,
        })

    if not record["disks"]:
        record["errors"].append("no disks enumerated for snapshot")
    return record


def render_snapshot_text(record: dict) -> str:
    """Render a snapshot as a human-readable undo map."""
    lines: list[str] = []
    add = lines.append
    add("ChaseOS Install-Safety - Partition Snapshot (UNDO MAP)")
    add("=" * 56)
    add(f"Captured at : {record.get('captured_at') or 'unknown'}")
    add(f"Platform    : {record.get('platform')}")
    add("Read-only   : nothing on disk was changed to produce this snapshot.")
    add("")
    add("Keep this file. If an install goes wrong, this is the layout to restore to.")
    add("")
    for disk in record.get("disks") or []:
        sysflag = " [SYSTEM/OS DISK]" if disk.get("is_system_disk") else ""
        add(f"Disk {disk.get('number')}: {disk.get('name')} - {disk.get('size_gb')} GB, "
            f"{disk.get('bus_type')}, {disk.get('partition_style')}{sysflag}")
        parts = disk.get("partitions") or []
        if not parts:
            add("  (no partitions enumerated)")
        for p in parts:
            drive = f"{p['drive']}: " if p.get("drive") else ""
            add(f"  - Part {p.get('number')}: {drive}{p.get('type') or 'unknown'} "
                f"- {p.get('size_gb')} GB @ offset {p.get('offset')}")
        add("")
    for err in record.get("errors") or []:
        add(f"NOTE: {err}")
    return "\n".join(lines).rstrip() + "\n"


def write_snapshot(record: dict, out_path: str | Path) -> Path:
    """Persist a snapshot record (JSON) to a vault audit artifact.

    This writes to the vault only — never to a physical disk. The caller chooses
    the path (typically under ``07_LOGS/Install-Safety/``).
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path
