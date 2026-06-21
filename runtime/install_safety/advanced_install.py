"""Advanced Install Executor — the gated, bounded host-mutating path.

Same-disk dual-boot is the one genuinely dangerous install path. This module is the
*only* place in ChaseOS that can mutate the host disk, and it is built fail-closed:

- **Dry-run by default.** Every action runs nothing unless ``execute=True`` is passed
  explicitly (CLI ``--execute``).
- **Hard readiness gate.** Every precondition must be explicitly satisfied — anything
  unknown (``None``) counts as NOT satisfied. No gate, no execution.
- **Snapshot-first.** The bounded shrink refuses unless a partition snapshot (undo map)
  was captured this session.
- **Bounded.** ChaseOS performs only the one recoverable destructive step it can do
  from Windows — shrinking the OS partition (``Resize-Partition``). It never creates
  the Linux partition or writes a bootloader — that stays with the OS installer.
- **Typed confirmation + explicit backup flag** are required to execute the shrink.

Reversible prep (suspend BitLocker, disable Fast Startup) is offered separately; it
mutates the host but is reversible and documented with its reverse command.

The subprocess runner is injectable (``runner=``) so the whole module is testable
without touching a real disk.
"""

from __future__ import annotations

import platform
import subprocess
from typing import Any, Callable, Optional

from runtime.install_safety.preflight import _confirmation_token

# Safety margin (GB) kept free above the requested shrink, and the minimum useful
# install footprint. A shrink request must fit inside the true shrinkable space.
_SHRINK_MARGIN_GB = 10
_MIN_INSTALL_GB = 25


def _default_runner(script: str) -> tuple[int, str, str]:
    """Run a PowerShell script; return (returncode, stdout, stderr).

    This is the ONLY path that can actually mutate the host, and it is reached only
    when a caller passes ``execute=True`` past the readiness gate.
    """
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": 600}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def expected_confirm_token(target_disk: Optional[int]) -> str:
    """The typed-confirmation token the operator must supply (matches preflight)."""
    return _confirmation_token("same-disk", target_disk)


# --------------------------------------------------------------------------- #
# Readiness gate
# --------------------------------------------------------------------------- #

def _bitlocker_clear(facts: dict) -> bool:
    """BitLocker is OK to repartition only if explicitly off/suspended."""
    if facts.get("bitlocker_protected") is True:
        return False
    if facts.get("bitlocker_protected") is False:
        return True
    # Unknown protection state: only OK if we positively know BitLocker isn't present.
    return facts.get("bitlocker_present") is False


def _target_disk_healthy(facts: dict, target_disk: Optional[int]) -> bool:
    for disk in facts.get("disks") or []:
        if disk.get("number") == target_disk or (target_disk is None and disk.get("is_system_disk")):
            return (disk.get("health") or "").strip().lower() == "healthy"
    return False


def check_readiness(
    *,
    facts: dict,
    target_disk: Optional[int],
    shrink_gb: Optional[int],
    backup_verified: Optional[bool],
    recovery_usb: Optional[bool],
    snapshot_captured: Optional[bool],
    confirm_token: Optional[str],
) -> dict:
    """Evaluate every hard precondition for a bounded same-disk shrink. Fail-closed.

    Returns ``{ready, checks[], unmet[]}``. ``ready`` is True only if every check is OK.
    """
    expected = expected_confirm_token(target_disk)
    shrinkable = facts.get("os_shrinkable_gb")

    checks: list[dict] = []

    def add(key: str, ok: bool, detail: str) -> None:
        checks.append({"key": key, "ok": bool(ok), "detail": detail})

    add("verified_backup", backup_verified is True,
        "A full, verified backup must be confirmed (--backup yes / --i-have-verified-backup).")
    add("recovery_media", recovery_usb is True,
        "Bootable recovery media for the existing OS must be confirmed (--recovery-usb yes).")
    add("snapshot_captured", snapshot_captured is True,
        "A partition snapshot (undo map) must be captured first.")
    add("typed_confirmation", bool(confirm_token) and confirm_token == expected,
        f"Typed confirmation token must equal {expected}.")
    add("controller_ahci", facts.get("rst_or_optane") is False,
        "Storage controller must be AHCI/NVMe (not Intel RST/RAID/Optane).")
    add("bitlocker_clear", _bitlocker_clear(facts),
        "BitLocker must be off or suspended (run reversible prep first).")
    add("fast_startup_off", facts.get("fast_startup_enabled") is False,
        "Windows Fast Startup must be disabled (run reversible prep first).")
    add("dirty_bit_clear", facts.get("dirty_bit") is False,
        "The NTFS dirty bit must be clear (boot Windows cleanly / chkdsk first).")
    add("no_pending_reboot", facts.get("pending_reboot") is False,
        "No Windows update/reboot may be pending.")
    add("disk_healthy", _target_disk_healthy(facts, target_disk),
        "The target disk must report SMART health 'Healthy'.")

    shrink_ok = (
        isinstance(shrink_gb, int) and shrink_gb >= _MIN_INSTALL_GB
        and isinstance(shrinkable, int) and shrink_gb + _SHRINK_MARGIN_GB <= shrinkable
    )
    add("shrink_fits", shrink_ok,
        f"Requested shrink must be >= {_MIN_INSTALL_GB} GB and fit within shrinkable "
        f"space minus a {_SHRINK_MARGIN_GB} GB margin (have "
        f"{shrinkable if shrinkable is not None else 'unknown'} GB).")

    unmet = [c["detail"] for c in checks if not c["ok"]]
    return {"ready": not unmet, "checks": checks, "unmet": unmet,
            "expected_confirm_token": expected}


# --------------------------------------------------------------------------- #
# Reversible prep (mutating but reversible)
# --------------------------------------------------------------------------- #

def build_reversible_prep_plan(facts: dict) -> list[dict]:
    """Build the reversible prep steps needed before a shrink (only those that apply)."""
    plan: list[dict] = []
    drive = (facts.get("os_partition_drive") or "C")
    if facts.get("bitlocker_protected") is True:
        plan.append({
            "id": "suspend_bitlocker",
            "description": "Suspend BitLocker on the OS drive (re-locks automatically on reboot).",
            "command": f"Suspend-BitLocker -MountPoint \"{drive}:\" -RebootCount 0",
            "reverse_command": f"Resume-BitLocker -MountPoint \"{drive}:\"",
            "reversible": True,
        })
    if facts.get("fast_startup_enabled") is True:
        plan.append({
            "id": "disable_fast_startup",
            "description": "Disable Windows Fast Startup (so NTFS isn't left hibernated).",
            "command": "Set-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Power' -Name HiberbootEnabled -Value 0",
            "reverse_command": "Set-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Power' -Name HiberbootEnabled -Value 1",
            "reversible": True,
        })
    return plan


def run_reversible_prep(
    *,
    facts: dict,
    execute: bool = False,
    runner: Optional[Callable[[str], tuple[int, str, str]]] = None,
    system: Optional[str] = None,
) -> dict:
    """Run (or, by default, preview) the reversible prep steps. Dry-run unless execute."""
    system = system or platform.system()
    plan = build_reversible_prep_plan(facts)
    result = {"dry_run": not execute, "platform": system, "steps": [], "executed": False}

    if not execute:
        result["steps"] = [dict(s, status="planned") for s in plan]
        return result
    if system != "Windows":
        result["steps"] = [dict(s, status="skipped-non-windows") for s in plan]
        return result

    run = runner or _default_runner
    result["executed"] = True
    for step in plan:
        rc, out, err = run(step["command"])
        result["steps"].append(dict(step, status="ok" if rc == 0 else "failed",
                                    returncode=rc, stderr=err))
    return result


# --------------------------------------------------------------------------- #
# Bounded partition shrink (the one destructive step)
# --------------------------------------------------------------------------- #

def build_shrink_command(drive: str, shrink_gb: int) -> str:
    """PowerShell that shrinks the OS partition by ``shrink_gb`` (current size computed
    at runtime). This is the only destructive command ChaseOS will run."""
    return (
        f"$p = Get-Partition -DriveLetter {drive}; "
        f"$new = [int64]$p.Size - [int64]({int(shrink_gb)} * 1GB); "
        f"Resize-Partition -DriveLetter {drive} -Size $new"
    )


def execute_partition_shrink(
    *,
    facts: dict,
    target_disk: Optional[int],
    shrink_gb: int,
    backup_verified: Optional[bool],
    recovery_usb: Optional[bool],
    snapshot_captured: Optional[bool],
    confirm_token: Optional[str],
    execute: bool = False,
    runner: Optional[Callable[[str], tuple[int, str, str]]] = None,
    system: Optional[str] = None,
) -> dict:
    """Perform (or preview) the bounded OS-partition shrink. Fail-closed.

    Dry-run by default: returns the readiness verdict + the exact command, runs nothing.
    With ``execute=True`` it runs ONLY if every readiness check passes; otherwise it
    refuses. Never creates partitions or writes a bootloader.
    """
    system = system or platform.system()
    drive = (facts.get("os_partition_drive") or "C")
    readiness = check_readiness(
        facts=facts, target_disk=target_disk, shrink_gb=shrink_gb,
        backup_verified=backup_verified, recovery_usb=recovery_usb,
        snapshot_captured=snapshot_captured, confirm_token=confirm_token,
    )
    command = build_shrink_command(drive, shrink_gb)
    handoff = [
        "Boot your Linux/ChaseOS installer from USB.",
        "Install into the new UNALLOCATED space only (do NOT touch the Windows partition).",
        "Let the installer set up the bootloader (GRUB/systemd-boot) for dual-boot.",
        "Reboot and confirm BOTH operating systems start.",
    ]
    base = {
        "drive": drive,
        "shrink_gb": shrink_gb,
        "readiness": readiness,
        "command": command,
        "post_shrink_handoff": handoff,
        "executed": False,
    }

    if not execute:
        return {**base, "dry_run": True, "status": "dry-run"}

    if not readiness["ready"]:
        return {**base, "dry_run": False, "status": "refused",
                "reason": "readiness gate not satisfied", "unmet": readiness["unmet"]}

    if system != "Windows":
        return {**base, "dry_run": False, "status": "refused",
                "reason": f"partition shrink is Windows-only; platform '{system}'"}

    run = runner or _default_runner
    rc, out, err = run(command)
    return {**base, "dry_run": False, "executed": True,
            "status": "ok" if rc == 0 else "failed",
            "returncode": rc, "stdout": out, "stderr": err}


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def render_readiness_text(readiness: dict) -> str:
    lines = ["Advanced Install - Readiness Gate", "=" * 40,
             f"Ready: {'YES' if readiness.get('ready') else 'NO'}", ""]
    for c in readiness.get("checks", []):
        mark = "[x]" if c["ok"] else "[ ]"
        lines.append(f"  {mark} {c['key']}")
        if not c["ok"]:
            lines.append(f"       - {c['detail']}")
    return "\n".join(lines).rstrip() + "\n"


def render_shrink_text(result: dict) -> str:
    lines = ["Advanced Install - Bounded Partition Shrink", "=" * 44,
             f"Drive    : {result.get('drive')}:",
             f"Shrink   : {result.get('shrink_gb')} GB",
             f"Status   : {result.get('status')}",
             f"Dry-run  : {result.get('dry_run')}", ""]
    if result.get("status") in {"dry-run", "refused"}:
        lines.append("Readiness:")
        lines.append(render_readiness_text(result.get("readiness", {})).rstrip())
        lines.append("")
    lines.append("Command that WOULD run (review it):")
    lines.append(f"  {result.get('command')}")
    lines.append("")
    if result.get("status") == "dry-run":
        lines.append("This was a DRY RUN - nothing was changed. Re-run with --execute "
                     "(and all gates satisfied) to perform the shrink.")
    elif result.get("status") == "refused":
        lines.append("REFUSED - the readiness gate is not satisfied. Unmet:")
        for u in result.get("unmet", []):
            lines.append(f"  ! {u}")
    elif result.get("status") == "ok":
        lines.append("Shrink completed. Next (handed to the OS installer):")
        for s in result.get("post_shrink_handoff", []):
            lines.append(f"  - {s}")
    else:
        lines.append(f"Shrink FAILED (rc={result.get('returncode')}): {result.get('stderr')}")
    return "\n".join(lines).rstrip() + "\n"
