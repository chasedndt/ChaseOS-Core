"""Read-only host inspection for ChaseOS install-safety.

`probe_host()` collects hardware/boot facts the install-safety surfaces need.
It is strictly read-only: it runs only inspection commands (PowerShell CIM /
Storage queries on Windows), never writes, and fails open - any probe that
errors leaves its fact `None` and records a line in `probe_errors`, so a partial
result is always usable.

The single subprocess collector is injectable (`runner=`) and the platform is
overridable (`system=`) so the module is fully testable without a live host.
"""

from __future__ import annotations

import json
import platform
import subprocess
from typing import Any, Callable, Optional


# Read-only PowerShell collector. Emits a single compact JSON object. Every
# sub-probe is wrapped so one failure does not abort the rest. No state is
# changed by any of these cmdlets - they are pure queries.
_WINDOWS_COLLECTOR = r"""
$ErrorActionPreference = 'SilentlyContinue'
$cs  = Get-CimInstance Win32_ComputerSystem
$cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
# Media type by FriendlyName (Get-Disk lacks MediaType; Get-PhysicalDisk has it).
$mediaMap = @{}
try { Get-PhysicalDisk | ForEach-Object { $mediaMap["$($_.FriendlyName)"] = "$($_.MediaType)" } } catch {}
# Disks from Get-Disk: richer than Get-PhysicalDisk (partition style, system/boot
# flags, health, largest unallocated extent). All read-only queries.
# Windows Recovery / OEM recovery partition GptType GUID (clobbering this kills
# manufacturer factory-restore / Windows RE). Type 'Recovery' is also matched below.
# MSR (Microsoft Reserved) is intentionally NOT counted here - it is protected but is
# not a factory-restore partition, so counting it would overstate the warning.
$recoveryGpt = @(
  '{de94bba4-06d1-4d40-a16a-bfd50179d6ac}'   # Windows Recovery Environment
)
$pd  = @()
try {
  $pd = Get-Disk | ForEach-Object {
    $dnum = [int]$_.Number
    $parts = @()
    try { $parts = @(Get-Partition -DiskNumber $dnum) } catch {}
    # MBR caps at 4 primary partitions; on MBR a logical partition has Type 'Logical'.
    $primary = @($parts | Where-Object { "$($_.Type)" -ne 'Logical' }).Count
    $recovery = @($parts | Where-Object {
      "$($_.Type)" -eq 'Recovery' -or ($recoveryGpt -contains "$($_.GptType)")
    }).Count
    [pscustomobject]@{
      number          = $dnum
      name            = "$($_.FriendlyName)"
      media           = "$($mediaMap["$($_.FriendlyName)"])"
      bus             = "$($_.BusType)"
      size            = [int64]$_.Size
      partition_style = "$($_.PartitionStyle)"
      is_system       = [bool]$_.IsSystem
      is_boot         = [bool]$_.IsBoot
      removable       = ("$($_.BusType)" -in @('USB','SD','MMC'))
      health          = "$($_.HealthStatus)"
      free            = [int64]$_.LargestFreeExtent
      partition_count = [int]@($parts).Count
      primary_count   = [int]$primary
      recovery_count  = [int]$recovery
    }
  }
} catch {}
# True shrinkable space on the OS partition (Windows blocks shrinking past unmovable
# files; this is the real "can same-disk even fit ChaseOS?" number).
$sysLetter = "$env:SystemDrive".TrimEnd(':')
$osShrink = $null
try {
  $sp = Get-PartitionSupportedSize -DriveLetter $sysLetter
  $cp = Get-Partition -DriveLetter $sysLetter
  $osShrink = [int64]($cp.Size - $sp.SizeMin)
} catch {}
# EFI System Partitions (size matters: a full/undersized ESP blocks a 2nd bootloader).
$esp = @()
try {
  $esp = Get-Partition | Where-Object { $_.GptType -eq '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' } | ForEach-Object {
    [pscustomobject]@{ disk = [int]$_.DiskNumber; size = [int64]$_.Size }
  }
} catch {}
$ctrls = @()
try { $ctrls += (Get-CimInstance Win32_SCSIController | Select-Object -ExpandProperty Name) } catch {}
try { $ctrls += (Get-CimInstance Win32_IDEController  | Select-Object -ExpandProperty Name) } catch {}
$fw = $null
try { $fw = "$((Get-ComputerInfo -Property BiosFirmwareType).BiosFirmwareType)" } catch {}
$sb = $null
try { $sb = [bool](Confirm-SecureBootUEFI) } catch {}
$bl = $null
try { $bl = "$((Get-BitLockerVolume -MountPoint $env:SystemDrive).ProtectionStatus)" } catch {}
$tpm = $null
try { $tpm = [bool]((Get-Tpm).TpmPresent) } catch {}
# Fast Startup / hybrid boot leaves NTFS hibernated - corrupts a shared partition.
$fast = $null
try { $fast = [bool]((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power').HiberbootEnabled) } catch {}
# NTFS dirty bit on the system drive - uncleanly unmounted / mid-update hazard.
$dirty = $null
try {
  $dq = (fsutil dirty query $env:SystemDrive) 2>$null
  if ($dq -match 'is\s+Dirty') { $dirty = $true }
  elseif ($dq -match 'NOT\s+Dirty') { $dirty = $false }
} catch {}
# Pending reboot / mid-update - repartitioning while updates are pending risks corruption.
$pendingReboot = $false
try {
  if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') { $pendingReboot = $true }
  if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') { $pendingReboot = $true }
  $pfro = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue)
  if ($pfro -and $pfro.PendingFileRenameOperations) { $pendingReboot = $true }
} catch {}
# WSL availability - the safest "coexist" install target (no partitions touched).
$wslAvailable = $false
$wslDistros = @()
$wslDefaultVersion = $null
try {
  $raw = & wsl.exe -l -q 2>$null
  if ($LASTEXITCODE -eq 0 -or $raw) {
    $wslAvailable = $true
    $wslDistros = $raw | ForEach-Object { ($_ -replace "`0","").Trim() } | Where-Object { $_ -ne '' }
  }
} catch {}
try {
  $st = ((& wsl.exe --status 2>$null) -join "`n") -replace "`0",""
  if ($st -match 'Default Version:\s*(\d)') { $wslDefaultVersion = [int]$matches[1] }
} catch {}
$out = [pscustomobject]@{
  vendor          = "$($cs.Manufacturer)"
  model           = "$($cs.Model)"
  cpu             = "$cpu"
  ram_bytes       = [int64]$cs.TotalPhysicalMemory
  physical_disks  = $pd
  esp             = $esp
  controllers     = $ctrls
  firmware        = $fw
  secure_boot     = $sb
  bitlocker       = $bl
  tpm_present     = $tpm
  fast_startup    = $fast
  dirty_bit       = $dirty
  pending_reboot  = $pendingReboot
  os_drive        = $sysLetter
  os_shrinkable   = $osShrink
  wsl_available   = $wslAvailable
  wsl_distros     = $wslDistros
  wsl_version     = $wslDefaultVersion
}
$out | ConvertTo-Json -Depth 5 -Compress
"""

# Substrings (case-insensitive) in a storage controller name that indicate the
# Intel RST / RAID / Optane mode that hides NVMe disks from non-Windows
# installers - the single most common same-disk dual-boot blocker.
_RST_CONTROLLER_HINTS = ("rapid storage", "intel(r) rst", "intel rst", "optane", "raid")


def _default_runner(script: str) -> Optional[str]:
    """Run a read-only PowerShell collector script; return stdout or None."""
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": 60,
    }
    # Suppress the transient console window on Windows (read-only, but tidy).
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or "").strip()
    return out or None


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _truthy_bitlocker(raw: Any) -> Optional[bool]:
    """Map a Win32 BitLocker ProtectionStatus to on/off/unknown."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in {"1", "on"}:
        return True
    if text in {"0", "off"}:
        return False
    return None


def _empty_facts(system: str) -> dict:
    return {
        "platform": system,
        "vendor": None,
        "model": None,
        "cpu": None,
        "ram_bytes": None,
        "ram_gb": None,
        "disks": [],
        "physical_disk_count": None,
        "nvme_detected": None,
        "rst_or_optane": None,
        "firmware_type": None,
        "secure_boot": None,
        "bitlocker_protected": None,
        "bitlocker_present": None,
        "tpm_present": None,
        "fast_startup_enabled": None,
        "dirty_bit": None,
        "pending_reboot": None,
        "os_partition_drive": None,
        "os_shrinkable_gb": None,
        "wsl_available": None,
        "wsl_distros": [],
        "wsl_default_version": None,
        "wsl_ubuntu_present": None,
        "running_inside_wsl": None,
        "probe_errors": [],
        "read_only": True,
    }


def _detect_running_inside_wsl() -> bool:
    """Best-effort detection of running inside a WSL distro (read-only)."""
    import os
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="ignore") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def _ubuntu_in(distros: Any) -> Optional[bool]:
    if not distros:
        return None if distros is None else False
    return any("ubuntu" in str(d).lower() for d in distros)


def _gb(value: Any) -> Optional[int]:
    try:
        return round(int(value) / (1024**3)) if value not in (None, "", 0, "0") else None
    except (ValueError, TypeError):
        return None


def _mb(value: Any) -> Optional[int]:
    try:
        return round(int(value) / (1024**2)) if value not in (None, "", 0, "0") else None
    except (ValueError, TypeError):
        return None


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None and value != "" else None
    except (ValueError, TypeError):
        return None


def _norm_partition_style(raw: Any) -> Optional[str]:
    text = str(raw or "").strip().upper()
    if text in {"GPT"}:
        return "GPT"
    if text in {"MBR"}:
        return "MBR"
    if text in {"RAW"}:
        return "RAW"
    return None


def probe_host(
    *,
    runner: Optional[Callable[[str], Optional[str]]] = None,
    system: Optional[str] = None,
    wsl_marker: Optional[bool] = None,
) -> dict:
    """Inspect the host and return install-safety facts. Read-only, fail-open.

    Args:
        runner: callable taking a PowerShell script and returning stdout (or
            None). Defaults to a real read-only subprocess runner. Injectable
            for tests.
        system: platform name override ("Windows"/"Linux"/"Darwin"). Defaults
            to ``platform.system()``.
        wsl_marker: override for "are we running inside WSL?" (used on the
            non-Windows path). None = auto-detect. Injectable for tests.

    Returns:
        A facts dict. Any fact that could not be determined is ``None`` and a
        human-readable reason is appended to ``probe_errors``.
    """
    system = system or platform.system()
    facts = _empty_facts(system)
    # Cross-platform basics that never require a subprocess.
    facts["cpu"] = facts["cpu"] or (platform.processor() or None)

    if system != "Windows":
        in_wsl = wsl_marker if wsl_marker is not None else _detect_running_inside_wsl()
        facts["running_inside_wsl"] = in_wsl
        if in_wsl:
            # Running inside WSL Ubuntu IS the safe, fully-reversible coexist target.
            facts["wsl_available"] = True
            facts["probe_errors"].append(
                "running inside WSL: this is already a fully-reversible coexist environment "
                "(no host partitions are touched). Disk-level host inspection does not apply here."
            )
        else:
            facts["probe_errors"].append(
                f"host inspection is currently Windows-only; platform '{system}' reports basics only"
            )
        return facts

    run = runner or _default_runner
    raw = run(_WINDOWS_COLLECTOR)
    if not raw:
        facts["probe_errors"].append("PowerShell host collector returned no output (probe skipped)")
        return facts

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        facts["probe_errors"].append("could not parse host collector JSON output")
        return facts

    facts["vendor"] = (data.get("vendor") or None) or None
    facts["model"] = (data.get("model") or None) or None
    facts["cpu"] = (data.get("cpu") or None) or facts["cpu"]

    ram_bytes = data.get("ram_bytes")
    try:
        ram_bytes = int(ram_bytes) if ram_bytes not in (None, "", 0, "0") else None
    except (ValueError, TypeError):
        ram_bytes = None
    facts["ram_bytes"] = ram_bytes
    facts["ram_gb"] = round(ram_bytes / (1024**3)) if ram_bytes else None

    # ESP sizes keyed by disk number (largest ESP per disk wins).
    esp_by_disk: dict[int, int] = {}
    for entry in _as_list(data.get("esp")):
        if not isinstance(entry, dict):
            continue
        try:
            dnum = int(entry.get("disk"))
            dsize = int(entry.get("size") or 0)
        except (ValueError, TypeError):
            continue
        esp_by_disk[dnum] = max(esp_by_disk.get(dnum, 0), dsize)

    disks = []
    nvme = False
    rst = False
    for entry in _as_list(data.get("physical_disks")):
        if not isinstance(entry, dict):
            continue
        bus = str(entry.get("bus") or "").strip()
        media = str(entry.get("media") or "").strip()
        number = entry.get("number")
        try:
            number = int(number) if number is not None else None
        except (ValueError, TypeError):
            number = None
        disk = {
            "number": number,
            "name": (entry.get("name") or "").strip() or "Unknown disk",
            "media_type": media or "unknown",
            "bus_type": bus or "unknown",
            "size_gb": _gb(entry.get("size")),
            "partition_style": _norm_partition_style(entry.get("partition_style")),
            "is_system_disk": bool(entry.get("is_system")) or bool(entry.get("is_boot")),
            "is_removable": bool(entry.get("removable")),
            "free_gb": _gb(entry.get("free")),
            "health": str(entry.get("health") or "").strip() or None,
            "esp_size_mb": _mb(esp_by_disk.get(number)) if number is not None else None,
            "partition_count": _int(entry.get("partition_count")),
            "primary_partition_count": _int(entry.get("primary_count")),
            "recovery_partition_count": _int(entry.get("recovery_count")),
        }
        disks.append(disk)
        if bus.lower() == "nvme":
            nvme = True
        if bus.lower() == "raid":
            # NVMe behind a RAID/RST volume - the classic dual-boot blocker.
            rst = True
    facts["disks"] = disks
    facts["physical_disk_count"] = len(disks) if disks else None
    facts["nvme_detected"] = nvme if disks else None

    for name in _as_list(data.get("controllers")):
        lowered = str(name or "").lower()
        if any(hint in lowered for hint in _RST_CONTROLLER_HINTS):
            rst = True
    # rst is unknown (None) only if we had no disks AND no controllers to inspect.
    if disks or _as_list(data.get("controllers")):
        facts["rst_or_optane"] = rst
    else:
        facts["probe_errors"].append("no physical disks or storage controllers enumerated")

    firmware = str(data.get("firmware") or "").strip().lower()
    if firmware in {"uefi"}:
        facts["firmware_type"] = "UEFI"
    elif firmware in {"bios", "legacy"}:
        facts["firmware_type"] = "BIOS"
    elif firmware:
        facts["firmware_type"] = data.get("firmware")
    else:
        facts["probe_errors"].append("firmware/boot mode could not be determined")

    sb = data.get("secure_boot")
    facts["secure_boot"] = bool(sb) if isinstance(sb, bool) else None
    if facts["secure_boot"] is None:
        facts["probe_errors"].append("Secure Boot state could not be determined (often legacy BIOS or access denied)")

    facts["bitlocker_protected"] = _truthy_bitlocker(data.get("bitlocker"))
    # BitLocker subsystem present (a volume status was readable) → encryption is possible.
    facts["bitlocker_present"] = data.get("bitlocker") not in (None, "")
    tpm = data.get("tpm_present")
    facts["tpm_present"] = bool(tpm) if isinstance(tpm, bool) else None

    fast = data.get("fast_startup")
    facts["fast_startup_enabled"] = bool(fast) if isinstance(fast, bool) else None
    dirty = data.get("dirty_bit")
    facts["dirty_bit"] = bool(dirty) if isinstance(dirty, bool) else None
    pending = data.get("pending_reboot")
    facts["pending_reboot"] = bool(pending) if isinstance(pending, bool) else None

    facts["os_partition_drive"] = (str(data.get("os_drive") or "").strip() or None)
    facts["os_shrinkable_gb"] = _gb(data.get("os_shrinkable"))

    wsl_avail = data.get("wsl_available")
    facts["wsl_available"] = bool(wsl_avail) if isinstance(wsl_avail, bool) else None
    facts["wsl_distros"] = [str(d).strip() for d in _as_list(data.get("wsl_distros")) if str(d).strip()]
    facts["wsl_default_version"] = _int(data.get("wsl_version"))
    facts["wsl_ubuntu_present"] = _ubuntu_in(facts["wsl_distros"]) if facts["wsl_available"] else None
    facts["running_inside_wsl"] = False  # we reached the Windows path

    return facts
