"""ChaseOS Hardware Readiness Report - the exportable share-with-support artifact.

`build_readiness_report(facts, intent)` maps read-only probe facts onto the
canonical readiness schema and runs the risk/recommended-path engine. The output
is intentionally safe to share: it carries hardware capability flags and risk
guidance only - no secrets, no BitLocker recovery keys, no serial numbers.

Schema (stable; consumed by support/devs):

    machine:        vendor, model, cpu, ram
    storage:        disks, nvme_detected, intel_optane_or_rst_possible, bitlocker_possible
    boot:           uefi, secure_boot, boot_mode
    install_intent: vm, spare_disk, same_disk_dual_boot
    risk:           level, blockers, recommended_path
"""

from __future__ import annotations

from typing import Any, Optional

# Accepted install-intent selectors (operator-declared; the host cannot infer
# what the user means to do).
INTENT_VM = "vm"
INTENT_SPARE_DISK = "spare-disk"
INTENT_EXTERNAL_DISK = "external-disk"
INTENT_SAME_DISK = "same-disk"
VALID_INTENTS = (INTENT_VM, INTENT_SPARE_DISK, INTENT_EXTERNAL_DISK, INTENT_SAME_DISK)

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_CRITICAL = "critical"
RISK_BLOCKED = "blocked"

# Reversibility axis — independent of risk. "Can I undo it?" not "how bad if it breaks?"
REV_FULL = "fully-reversible"               # VM / WSL — delete and it's gone
REV_SNAPSHOT = "reversible-with-snapshot"   # spare/external disk, undo map captured
REV_HARD = "hard-to-reverse"               # same-disk shrink
REV_IRREVERSIBLE = "irreversible-without-backup"

# Blast radius — what is at stake if the action goes wrong.
BLAST_NONE = "none"
BLAST_TARGET = "target-disk-only"
BLAST_MAIN = "main-os-disk"
BLAST_SHARED = "all-data-on-shared-disk"

# Minimum EFI System Partition size (MB) below which a second bootloader is at risk.
_MIN_ESP_MB = 200

# Minimum useful same-disk install footprint (GB). If the OS partition cannot shrink
# by at least this much, a same-disk install effectively will not fit.
_MIN_SHRINK_GB = 25

_UNKNOWN = "unknown"


def _ram_label(facts: dict) -> Any:
    gb = facts.get("ram_gb")
    return f"{gb} GB" if gb else _UNKNOWN


def _disk_labels(facts: dict) -> list[str]:
    labels: list[str] = []
    for disk in facts.get("disks") or []:
        size = disk.get("size_gb")
        size_txt = f"{size} GB" if size else "unknown size"
        bus = disk.get("bus_type") or "unknown bus"
        media = disk.get("media_type") or "unknown media"
        name = disk.get("name") or "Unknown disk"
        labels.append(f"{name} - {size_txt}, {bus}/{media}")
    return labels


def _bool_or_unknown(value: Optional[bool]) -> Any:
    return value if isinstance(value, bool) else _UNKNOWN


def _boot_mode(facts: dict) -> str:
    fw = facts.get("firmware_type")
    if fw == "UEFI":
        return "UEFI"
    if fw == "BIOS":
        return "Legacy BIOS"
    return _UNKNOWN


def _host_blockers(facts: dict, intent: Optional[str]) -> tuple[list[str], list[str]]:
    """Return (hard_blockers, warnings). Hard blockers can force a BLOCKED level."""
    blockers: list[str] = []
    hard: list[str] = []

    rst = facts.get("rst_or_optane") is True
    bitlocker_on = facts.get("bitlocker_protected") is True
    disk_count = facts.get("physical_disk_count")
    single_disk = disk_count == 1
    touches_main_disk = intent == INTENT_SAME_DISK

    if rst:
        blockers.append(
            "Intel RST / RAID / Optane storage mode detected - non-Windows installers "
            "may not see your NVMe disk. Switch the SATA/NVMe controller to AHCI in "
            "firmware FIRST, and prep Windows to boot in AHCI (Safe Mode) or it will "
            "BSOD. See the dedicated RST->AHCI flow before any same-disk install."
        )
    if bitlocker_on:
        blockers.append(
            "BitLocker is ON on the system drive - save your recovery key and SUSPEND "
            "BitLocker before any firmware/boot change or repartition (TPM-bound keys "
            "re-prompt after Secure Boot changes)."
        )
    if facts.get("dirty_bit") is True:
        msg = (
            "The system volume's NTFS dirty bit is set (uncleanly unmounted or mid-update). "
            "Repartitioning a dirty volume risks corruption - boot Windows cleanly and run "
            "chkdsk first."
        )
        blockers.append(msg)
        if touches_main_disk:
            hard.append(msg)
    if facts.get("fast_startup_enabled") is True:
        blockers.append(
            "Windows Fast Startup / hibernation is ON - it leaves the NTFS partition in a "
            "hibernated state. Another OS mounting it can corrupt it. Disable Fast Startup "
            "before dual-booting."
        )
    if facts.get("pending_reboot") is True:
        msg = (
            "A Windows reboot/update is pending - repartitioning before it completes risks "
            "corruption. Reboot and let updates finish, then re-check."
        )
        blockers.append(msg)
        if touches_main_disk:
            hard.append(msg)

    # Per-disk hazards.
    for disk in facts.get("disks") or []:
        health = (disk.get("health") or "").strip().lower()
        if health and health not in {"healthy"}:
            msg = f"Disk '{disk.get('name')}' reports health '{disk.get('health')}' - do not install onto a failing disk."
            blockers.append(msg)
            hard.append(msg)
        esp = disk.get("esp_size_mb")
        if disk.get("is_system_disk") and isinstance(esp, int) and esp < _MIN_ESP_MB:
            blockers.append(
                f"The EFI System Partition on the OS disk is only {esp} MB - it may be too "
                "small/full for a second bootloader. A same-disk install may fail to boot."
            )
        # MBR 4-primary-partition cap on the OS disk (a silent installer failure).
        primaries = disk.get("primary_partition_count")
        if (
            disk.get("is_system_disk")
            and disk.get("partition_style") == "MBR"
            and isinstance(primaries, int)
            and primaries >= 4
        ):
            blockers.append(
                f"OS disk is MBR and already has {primaries} primary partitions (MBR's max). "
                "A new partition cannot be created without deleting one or converting the disk "
                "to GPT - a same-disk install will silently fail to partition."
            )
        # OEM / Windows recovery partitions on the OS disk (clobbering kills factory restore).
        recovery = disk.get("recovery_partition_count")
        if touches_main_disk and disk.get("is_system_disk") and isinstance(recovery, int) and recovery > 0:
            blockers.append(
                f"The OS disk has {recovery} OEM/recovery partition(s). Shrinking or "
                "repartitioning can clobber your manufacturer factory-restore / Windows "
                "Recovery - identify and preserve these partitions before any change."
            )

    # True shrinkable space on the OS partition - the real "can it even fit?" number.
    shrink = facts.get("os_shrinkable_gb")
    if touches_main_disk and isinstance(shrink, int):
        if shrink < _MIN_SHRINK_GB:
            blockers.append(
                f"The Windows partition can only shrink by about {shrink} GB "
                f"(minimum useful install is ~{_MIN_SHRINK_GB} GB). Windows blocks shrinking "
                "past unmovable files - free up space, disable hibernation/Fast Startup and the "
                "page file, defragment, then re-check before a same-disk install."
            )

    # Firmware vs partition-style mismatch on the OS disk (a top boot-failure cause).
    fw = facts.get("firmware_type")
    for disk in facts.get("disks") or []:
        if not disk.get("is_system_disk"):
            continue
        style = disk.get("partition_style")
        if fw == "UEFI" and style == "MBR":
            blockers.append(
                "OS disk is MBR but firmware is UEFI - mixed UEFI/MBR dual-boot is a common "
                "boot failure. Match the existing OS boot mode."
            )
        if fw == "BIOS" and style == "GPT":
            blockers.append(
                "OS disk is GPT but firmware is Legacy BIOS - confirm the boot mode before "
                "installing; mixing schemes commonly breaks boot."
            )

    if touches_main_disk and single_disk:
        blockers.append(
            "Only one physical disk detected - a same-disk dual-boot would share the "
            "Windows disk. A full, verified backup is required before proceeding."
        )

    return blockers, hard


def _coexist_block(facts: dict) -> dict:
    """Summarize the safest 'coexist' (no-repartition) option: WSL availability + the
    concrete next action. WSL2 Ubuntu is the recommended fully-reversible target."""
    inside = facts.get("running_inside_wsl") is True
    available = facts.get("wsl_available")
    ubuntu = facts.get("wsl_ubuntu_present")
    version = facts.get("wsl_default_version")

    if inside:
        action = (
            "You are already inside WSL - install ChaseOS here. No host partitions are "
            "touched and removing it is fully reversible."
        )
    elif available and ubuntu:
        action = (
            "WSL Ubuntu is installed - install ChaseOS inside it (`wsl -d Ubuntu`). "
            "Fully reversible, no partitions touched."
        )
    elif available and ubuntu is False:
        action = (
            "WSL is available but Ubuntu is not installed - run `wsl --install -d Ubuntu`, "
            "then install ChaseOS inside it. Fully reversible."
        )
    elif available is False:
        action = (
            "WSL is not installed - run `wsl --install -d Ubuntu` (one command, reboots once) "
            "for the safest fully-reversible install target."
        )
    else:
        action = (
            "Could not confirm WSL status - `wsl --install -d Ubuntu` enables the safest "
            "fully-reversible coexist target."
        )
    return {
        "running_inside_wsl": inside,
        "wsl_available": _bool_or_unknown(available),
        "wsl_ubuntu_present": _bool_or_unknown(ubuntu),
        "wsl_default_version": version if version is not None else _UNKNOWN,
        "wsl_distros": list(facts.get("wsl_distros") or []),
        "recommended_action": action,
    }


def _reversibility(intent: Optional[str], backup_verified: Optional[bool]) -> str:
    if intent == INTENT_VM:
        return REV_FULL
    if intent in (INTENT_SPARE_DISK, INTENT_EXTERNAL_DISK):
        return REV_SNAPSHOT
    if intent == INTENT_SAME_DISK:
        return REV_HARD if backup_verified else REV_IRREVERSIBLE
    return REV_IRREVERSIBLE if not backup_verified else REV_HARD


def _blast_radius(intent: Optional[str], facts: dict) -> str:
    if intent == INTENT_VM:
        return BLAST_NONE
    if intent in (INTENT_SPARE_DISK, INTENT_EXTERNAL_DISK):
        return BLAST_TARGET
    if intent == INTENT_SAME_DISK:
        return BLAST_SHARED if facts.get("physical_disk_count") == 1 else BLAST_MAIN
    return BLAST_MAIN


def _assess_risk(facts: dict, intent: Optional[str], backup_verified: Optional[bool] = None) -> dict:
    """Compute risk level, reversibility, blast radius, blockers, recommended path.

    Risk and reversibility are independent axes. The decision rule: a high-risk
    action with a verified backup may proceed (recoverable); a medium-risk action
    that is irreversible-without-backup is blocked. Reversibility is the gate.
    """
    blockers, hard = _host_blockers(facts, intent)
    rst = facts.get("rst_or_optane") is True
    bitlocker_on = facts.get("bitlocker_protected") is True
    disk_count = facts.get("physical_disk_count")

    reversibility = _reversibility(intent, backup_verified)
    blast = _blast_radius(intent, facts)

    # ---- level ----
    if hard:
        level = RISK_BLOCKED
    elif intent == INTENT_VM:
        level = RISK_LOW
    elif intent in (INTENT_SPARE_DISK, INTENT_EXTERNAL_DISK):
        level = RISK_MEDIUM if (disk_count or 0) < 2 and intent == INTENT_SPARE_DISK else RISK_LOW
    elif intent == INTENT_SAME_DISK:
        if rst and bitlocker_on:
            level = RISK_CRITICAL
        elif blockers:
            level = RISK_HIGH
        else:
            level = RISK_MEDIUM
    else:  # unknown intent - judge by host hazards, recommend the safest path
        level = RISK_HIGH if blockers else RISK_MEDIUM

    # NOTE: the backup/recovery-media *blocking* decision lives in preflight.py,
    # where the operator actually answers "do you have a verified backup / recovery
    # media?". This report stays informational: it reports raw risk, reversibility,
    # and blast radius, and only escalates to BLOCKED for genuine HARD technical
    # blocks (failing disk, dirty volume on a same-disk install) computed above.

    # ---- recommended path ----
    if level == RISK_BLOCKED:
        recommended = (
            "BLOCKED - do not proceed. Resolve the hard blockers above, or choose a fully "
            "reversible path (VM / WSL) or a separate target disk instead."
        )
    elif intent == INTENT_VM:
        recommended = (
            "Run ChaseOS in a virtual machine or WSL on your existing OS. "
            "Your physical disks are never touched - safest, fully reversible path."
        )
    elif intent == INTENT_EXTERNAL_DISK:
        recommended = (
            "Install ChaseOS to the external SSD/USB drive only. Your internal OS disk "
            "is untouched and the install is removed by unplugging the drive."
        )
    elif intent == INTENT_SPARE_DISK and (disk_count or 0) >= 2:
        recommended = (
            "Install ChaseOS to the spare/secondary disk only. Do not select the "
            "Windows disk during install. Disconnecting the Windows disk during "
            "install is the safest variant."
        )
    elif intent == INTENT_SPARE_DISK:
        recommended = (
            "Spare-disk install was selected but a second physical disk was not "
            "clearly detected. Confirm the target disk first, or use a VM."
        )
    elif intent == INTENT_SAME_DISK:
        if blockers:
            recommended = (
                "Resolve the blockers above first. Prefer a VM or a spare disk. "
                "Same-disk dual-boot is Advanced Install only - allowed after a full "
                "backup and passing the safety checklist."
            )
        else:
            recommended = (
                "Same-disk dual-boot is possible but is Advanced Install: take a full "
                "backup and pass the safety checklist before any change to this disk."
            )
    else:
        recommended = (
            "Start with a VM or a spare disk. Avoid same-disk dual-boot unless you "
            "back up first and explicitly choose Advanced Install."
        )

    return {
        "level": level,
        "reversibility": reversibility,
        "blast_radius": blast,
        "blockers": blockers,
        "recommended_path": recommended,
    }


def build_readiness_report(
    facts: dict,
    intent: Optional[str] = None,
    *,
    backup_verified: Optional[bool] = None,
) -> dict:
    """Build the canonical Hardware Readiness Report from probe facts.

    Args:
        facts: output of ``probe_host()``.
        intent: one of ``VALID_INTENTS`` or None (unknown). Operator-declared.
        backup_verified: whether a full, verified backup is confirmed. Feeds the
            reversibility gate (an irreversible same-disk action with no backup is
            BLOCKED). None = unconfirmed.

    Returns:
        A dict matching the readiness schema, plus a ``_meta`` block carrying
        non-schema context (probe errors, the main-disk-mutation default).
    """
    norm_intent = intent if intent in VALID_INTENTS else None

    report = {
        "machine": {
            "vendor": facts.get("vendor") or _UNKNOWN,
            "model": facts.get("model") or _UNKNOWN,
            "cpu": facts.get("cpu") or _UNKNOWN,
            "ram": _ram_label(facts),
        },
        "storage": {
            "disks": _disk_labels(facts),
            "nvme_detected": _bool_or_unknown(facts.get("nvme_detected")),
            "intel_optane_or_rst_possible": _bool_or_unknown(facts.get("rst_or_optane")),
            "bitlocker_possible": _bool_or_unknown(
                facts.get("bitlocker_protected")
                if facts.get("bitlocker_protected") is not None
                else facts.get("bitlocker_present")
            ),
            "os_partition_shrinkable_gb": (
                facts.get("os_shrinkable_gb")
                if facts.get("os_shrinkable_gb") is not None else _UNKNOWN
            ),
        },
        "boot": {
            "uefi": _bool_or_unknown(
                True if facts.get("firmware_type") == "UEFI"
                else (False if facts.get("firmware_type") == "BIOS" else None)
            ),
            "secure_boot": _bool_or_unknown(facts.get("secure_boot")),
            "boot_mode": _boot_mode(facts),
        },
        "install_intent": {
            "vm": norm_intent == INTENT_VM,
            "spare_disk": norm_intent == INTENT_SPARE_DISK,
            "external_disk": norm_intent == INTENT_EXTERNAL_DISK,
            "same_disk_dual_boot": norm_intent == INTENT_SAME_DISK,
        },
        "coexist": _coexist_block(facts),
        "risk": _assess_risk(facts, norm_intent, backup_verified=backup_verified),
    }
    report["_meta"] = {
        "main_disk_mutation_default_enabled": False,
        "intent_declared": norm_intent or _UNKNOWN,
        "probe_errors": list(facts.get("probe_errors") or []),
        "read_only": True,
    }
    return report


# ---------------------------------------------------------------------------
# Dependency-free YAML rendering (stdlib-only, per the standing decision).
# ---------------------------------------------------------------------------


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return _UNKNOWN
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if text == "":
        return '""'
    # Quote anything with characters that would break a bare YAML scalar.
    if any(ch in text for ch in (":", "#", "'", '"')) or text.strip() != text:
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _yaml_lines(obj: Any, indent: int = 0) -> list[str]:
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, dict):
                lines.append(f"{pad}{key}:")
                lines.extend(_yaml_lines(value, indent + 1))
            elif isinstance(value, list):
                if not value:
                    lines.append(f"{pad}{key}: []")
                else:
                    lines.append(f"{pad}{key}:")
                    for item in value:
                        if isinstance(item, (dict, list)):
                            nested = _yaml_lines(item, indent + 2)
                            # Fold first nested line onto the dash.
                            first = nested[0].lstrip() if nested else ""
                            lines.append(f"{pad}  - {first}")
                            lines.extend(nested[1:])
                        else:
                            lines.append(f"{pad}  - {_yaml_scalar(item)}")
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(value)}")
    else:
        lines.append(f"{pad}{_yaml_scalar(obj)}")
    return lines


def render_report_yaml(report: dict, *, include_meta: bool = True) -> str:
    """Render a readiness report as YAML text (stdlib-only)."""
    payload = dict(report)
    if not include_meta:
        payload.pop("_meta", None)
    return "\n".join(_yaml_lines(payload)) + "\n"
