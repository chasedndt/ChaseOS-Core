"""`chaseos doctor install-safety` - read-only install-safety diagnostic.

Inspects only. No writes. Produces the operator-facing view required by the
spec: detected disks, boot mode, Windows dual-boot risk hints, a likely
RST/Optane warning, a recommended install path, and a "do not continue without
backup" warning when relevant - all anchored to the No-Main-Disk-Mutation
default.
"""

from __future__ import annotations

from typing import Optional

from runtime.install_safety import (
    ADVANCED_INSTALL_SAFETY_CHECKLIST,
    MAIN_DISK_MUTATION_POLICY,
)
from runtime.install_safety.probe import probe_host
from runtime.install_safety.report import build_readiness_report

_BACKUP_WARNING = (
    "DO NOT CONTINUE WITHOUT A FULL, VERIFIED BACKUP. Any change to the main OS "
    "disk (resize, repartition, bootloader) can render Windows unbootable."
)


def _dual_boot_hints(facts: dict, report: dict) -> list[str]:
    hints: list[str] = []
    risk = report.get("risk", {})

    # Hard blockers double as the most important hints.
    hints.extend(risk.get("blockers", []))

    if facts.get("bitlocker_protected") is False and facts.get("bitlocker_present"):
        hints.append(
            "BitLocker is available on this machine but currently off - re-check "
            "before install in case it is enabled later."
        )
    if facts.get("secure_boot") is True:
        hints.append(
            "Secure Boot is ON - install media must be signed (most modern Linux/ChaseOS "
            "installers are). Disable only if the installer requires it."
        )
    if facts.get("firmware_type") == "BIOS":
        hints.append(
            "Legacy BIOS boot mode detected - mixing UEFI and legacy installs on one "
            "disk is a common dual-boot failure. Match the existing OS boot mode."
        )
    if facts.get("physical_disk_count") and facts["physical_disk_count"] >= 2:
        hints.append(
            "More than one physical disk detected - installing to a separate disk "
            "avoids touching the Windows disk entirely (recommended)."
        )
    if facts.get("firmware_type") is None:
        hints.append("Boot mode could not be confirmed - verify UEFI vs Legacy before installing.")
    return hints


def _rst_warning(facts: dict) -> Optional[str]:
    if facts.get("rst_or_optane") is True:
        return (
            "LIKELY INTEL RST / RAID / OPTANE: your NVMe disk may be invisible to "
            "non-Windows installers. Set the storage controller to AHCI/NVMe in "
            "firmware first (this can require a Windows repair boot)."
        )
    if facts.get("rst_or_optane") is None:
        return (
            "Could not confirm storage controller mode - if a Linux/ChaseOS installer "
            "cannot see your NVMe disk, suspect Intel RST/RAID and switch to AHCI."
        )
    return None


def _backup_warning(report: dict) -> Optional[str]:
    risk = report.get("risk", {})
    level = risk.get("level")
    intent = report.get("install_intent", {})
    if risk.get("blockers"):
        return _BACKUP_WARNING
    if level in {"high", "critical"}:
        return _BACKUP_WARNING
    if intent.get("same_disk_dual_boot"):
        return _BACKUP_WARNING
    return None


def build_install_safety_report(
    *,
    intent: Optional[str] = None,
    facts: Optional[dict] = None,
    runner=None,
    system: Optional[str] = None,
) -> dict:
    """Assemble the full read-only install-safety diagnostic.

    Args:
        intent: declared install intent (vm / spare-disk / same-disk) or None.
        facts: pre-collected probe facts (skips probing - used by tests).
        runner / system: forwarded to ``probe_host`` when ``facts`` is None.

    Returns:
        A dict with the probe facts, the readiness report, and the operator view.
    """
    if facts is None:
        facts = probe_host(runner=runner, system=system)
    report = build_readiness_report(facts, intent=intent)

    operator_view = {
        "detected_disks": report["storage"]["disks"],
        "boot_mode": report["boot"]["boot_mode"],
        "dual_boot_risk_hints": _dual_boot_hints(facts, report),
        "rst_optane_warning": _rst_warning(facts),
        "recommended_install_path": report["risk"]["recommended_path"],
        "coexist_wsl_action": report.get("coexist", {}).get("recommended_action"),
        "backup_warning": _backup_warning(report),
        "main_disk_mutation_default": MAIN_DISK_MUTATION_POLICY,
        "advanced_install_checklist": list(ADVANCED_INSTALL_SAFETY_CHECKLIST),
        "risk_level": report["risk"]["level"],
    }

    return {
        "read_only": True,
        "facts": facts,
        "report": report,
        "operator_view": operator_view,
    }


def render_install_safety_text(diagnostic: dict) -> str:
    """Render the diagnostic as a human-readable, copy-pasteable report."""
    view = diagnostic["operator_view"]
    facts = diagnostic["facts"]
    lines: list[str] = []
    add = lines.append

    add("ChaseOS Doctor - Install Safety (read-only; nothing was changed)")
    add("=" * 62)
    add("")
    add(f"Default posture: {view['main_disk_mutation_default']}")
    add("")

    add(f"Machine        : {facts.get('vendor') or 'unknown'} {facts.get('model') or ''}".rstrip())
    add(f"Boot mode      : {view['boot_mode']}")
    add(f"Risk level     : {view['risk_level'].upper()}")
    add("")

    add("Detected disks:")
    if view["detected_disks"]:
        for disk in view["detected_disks"]:
            add(f"  - {disk}")
    else:
        add("  - (none detected / could not enumerate)")
    add("")

    if view["rst_optane_warning"]:
        add("Storage controller warning:")
        add(f"  ! {view['rst_optane_warning']}")
        add("")

    add("Dual-boot risk hints:")
    if view["dual_boot_risk_hints"]:
        for hint in view["dual_boot_risk_hints"]:
            add(f"  - {hint}")
    else:
        add("  - No specific risk hints detected for the current host.")
    add("")

    add("Recommended install path:")
    add(f"  {view['recommended_install_path']}")
    add("")

    if view.get("coexist_wsl_action"):
        add("Safest option (coexist / WSL):")
        add(f"  {view['coexist_wsl_action']}")
        add("")

    if view["backup_warning"]:
        add("!! " + view["backup_warning"])
        add("")
        add("Advanced Install safety checklist (clear ALL before any main-disk change):")
        for item in view["advanced_install_checklist"]:
            add(f"  [ ] {item}")
        add("")

    errors = facts.get("probe_errors") or []
    if errors:
        add("Probe notes (some facts unavailable):")
        for err in errors:
            add(f"  - {err}")
        add("")

    return "\n".join(lines).rstrip() + "\n"
