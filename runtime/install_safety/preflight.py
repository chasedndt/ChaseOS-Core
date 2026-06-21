"""Install-Safety Preflight Wizard.

`run_preflight()` combines read-only probe facts with operator-declared answers
(intent, "do you have a verified backup?", "do you have recovery media?", target
disk) and produces a single decision object:

- the Hardware Readiness Report (risk + reversibility, backup-aware)
- safe install modes presented in safety order, with a loud default
- the **recovery-media gate** result (Orange/Red paths are refused until recovery
  media + a verified backup are confirmed)
- a **plan-then-confirm** install plan with a typed-confirmation token and a
  rollback card — for destructive (same-disk) intents only

This module NEVER executes a destructive change. It stops at the plan. Host
mutation is a separate, later, gated pass (see Install-Safety-Preflight-Architecture).
"""

from __future__ import annotations

from typing import Optional

from runtime.install_safety import ADVANCED_INSTALL_SAFETY_CHECKLIST
from runtime.install_safety.probe import probe_host
from runtime.install_safety.report import (
    INTENT_EXTERNAL_DISK,
    INTENT_SAME_DISK,
    INTENT_SPARE_DISK,
    INTENT_VM,
    RISK_BLOCKED,
    RISK_CRITICAL,
    RISK_HIGH,
    VALID_INTENTS,
    build_readiness_report,
)

# Safe install modes, safest first. `default` marks the loud recommendation when
# the host scores yellow or worse.
SAFE_INSTALL_MODES = (
    {
        "id": "coexist",
        "label": "Coexist (no repartition) - WSL2 / VM on your existing OS",
        "intent": INTENT_VM,
        "touches_partitions": False,
    },
    {
        "id": "spare-disk",
        "label": "Install to a separate internal disk",
        "intent": INTENT_SPARE_DISK,
        "touches_partitions": True,
    },
    {
        "id": "external-ssd",
        "label": "Install to an external SSD / USB drive",
        "intent": INTENT_EXTERNAL_DISK,
        "touches_partitions": True,
    },
    {
        "id": "advanced-dual-boot",
        "label": "Advanced dual-boot on the same disk (manual partitioning)",
        "intent": INTENT_SAME_DISK,
        "touches_partitions": True,
    },
    {
        "id": "experimental",
        "label": "Experimental / unsupported storage layout",
        "intent": None,
        "touches_partitions": True,
    },
)

# Risk levels that require the recovery-media gate to pass before proceeding.
_GATED_LEVELS = (RISK_HIGH, RISK_CRITICAL, RISK_BLOCKED)


def _recovery_media_gate(
    *,
    level: str,
    intent: Optional[str],
    backup_verified: Optional[bool],
    recovery_usb: Optional[bool],
) -> dict:
    """Decide whether the recovery-media gate passes.

    For Orange/Red paths (and any same-disk intent), the user must confirm both a
    verified backup and recovery media before proceeding. Reversible paths
    (VM/spare/external) do not require the gate.
    """
    requires_gate = level in _GATED_LEVELS or intent == INTENT_SAME_DISK
    missing: list[str] = []
    if requires_gate:
        if backup_verified is not True:
            missing.append("A full, verified backup of the OS disk (confirm with --backup yes).")
        if recovery_usb is not True:
            missing.append("Bootable recovery/repair media for the existing OS (confirm with --recovery-usb yes).")
    return {
        "required": requires_gate,
        "passed": not requires_gate or not missing,
        "missing": missing,
    }


def _confirmation_token(intent: Optional[str], target_disk: Optional[int]) -> str:
    """Deterministic typed-confirmation token (no clock/random — reproducible)."""
    tgt = "disk" + str(target_disk) if target_disk is not None else "unspecified"
    return f"CONFIRM-{(intent or 'unknown').upper()}-{tgt.upper()}"


def _rollback_card(intent: Optional[str]) -> list[str]:
    card = [
        "If the install fails or Windows won't boot:",
        "  1. Boot the recovery/repair USB you prepared.",
        "  2. Restore the bootloader (Windows: bootrec /fixmbr, /fixboot, /rebuildbcd; or 'Startup Repair').",
        "  3. If BitLocker prompts for a key, enter the recovery key you saved before starting.",
        "  4. Compare the live layout against the partition snapshot (undo map) you captured.",
    ]
    if intent == INTENT_SAME_DISK:
        card.append("  5. Delete only the new ChaseOS/Linux partition(s); leave the original Windows partitions intact.")
    return card


def _build_install_plan(
    *,
    intent: Optional[str],
    target_disk: Optional[int],
    gate: dict,
    blocked: bool,
) -> Optional[dict]:
    """Build a plan-then-confirm install plan for destructive intents only.

    Returns None for non-destructive (VM) intents. The plan describes intended
    operations and a typed-confirmation token; it NEVER executes anything.
    """
    if intent == INTENT_VM:
        return None  # nothing to plan — no partitions touched
    if intent != INTENT_SAME_DISK:
        # Spare/external are reversible; provide a light plan, no typed token needed.
        return {
            "intent": intent,
            "executable": False,
            "destructive": False,
            "steps": [
                f"Select target disk {target_disk if target_disk is not None else '(confirm during install)'} only.",
                "Do NOT select the OS disk in the installer's disk picker.",
                "Disconnecting the OS disk during install is the safest variant.",
            ],
            "confirmation_required": False,
            "rollback_card": _rollback_card(intent),
            "note": "ChaseOS does not execute this plan; it is guidance for the official installer.",
        }

    # Same-disk (destructive) plan — gated, typed confirmation, never auto-run.
    steps = [
        "Capture a partition snapshot (undo map) and store it off the target disk.",
        "Verify the full backup restores (don't trust an unverified backup).",
        "Suspend BitLocker / save the recovery key; switch storage controller to AHCI if RST/Optane.",
        "Disable Windows Fast Startup; boot Windows cleanly so the NTFS dirty bit is clear.",
        f"Shrink the OS partition on disk {target_disk if target_disk is not None else '(unspecified)'} to create free space.",
        "Install ChaseOS into the new free space; install the bootloader.",
        "Reboot and confirm BOTH operating systems boot.",
    ]
    return {
        "intent": intent,
        "executable": False,  # ChaseOS never executes destructive steps in this build
        "destructive": True,
        "steps": steps,
        "confirmation_required": True,
        "confirmation_token": _confirmation_token(intent, target_disk),
        "confirmation_satisfied": False,
        "blocked": blocked,
        "advanced_install_checklist": list(ADVANCED_INSTALL_SAFETY_CHECKLIST),
        "rollback_card": _rollback_card(intent),
        "note": (
            "PLAN ONLY. ChaseOS will not perform any of these steps. Even with the typed "
            "token, host mutation is out of scope for this build — run the steps yourself "
            "with the official installer."
        ),
    }


def run_preflight(
    *,
    intent: Optional[str] = None,
    backup_verified: Optional[bool] = None,
    recovery_usb: Optional[bool] = None,
    target_disk: Optional[int] = None,
    facts: Optional[dict] = None,
    runner=None,
    system: Optional[str] = None,
) -> dict:
    """Run the full read-only install-safety preflight and return a decision object.

    Args:
        intent: declared install intent (one of VALID_INTENTS) or None.
        backup_verified: True if a full verified backup is confirmed.
        recovery_usb: True if bootable recovery media is confirmed.
        target_disk: disk number the user intends to install to (informational).
        facts: pre-collected probe facts (skips probing — used by tests).
        runner / system: forwarded to ``probe_host`` when ``facts`` is None.
    """
    if facts is None:
        facts = probe_host(runner=runner, system=system)

    norm_intent = intent if intent in VALID_INTENTS else None
    report = build_readiness_report(facts, intent=norm_intent, backup_verified=backup_verified)
    level = report["risk"]["level"]

    gate = _recovery_media_gate(
        level=level, intent=norm_intent,
        backup_verified=backup_verified, recovery_usb=recovery_usb,
    )

    # Final decision. Blocked risk OR a failed required gate => do not proceed.
    blocked = level == RISK_BLOCKED or not gate["passed"]
    if not norm_intent:
        decision = "choose-a-safe-mode"
    elif blocked:
        decision = "blocked"
    elif norm_intent == INTENT_VM:
        decision = "proceed-fully-reversible"
    elif norm_intent in (INTENT_SPARE_DISK, INTENT_EXTERNAL_DISK):
        decision = "proceed-reversible-with-snapshot"
    else:
        decision = "advanced-install-with-confirmation"

    plan = _build_install_plan(
        intent=norm_intent, target_disk=target_disk, gate=gate, blocked=blocked,
    )

    coexist = report.get("coexist", {})
    inside_wsl = facts.get("running_inside_wsl") is True

    # Loud default mode: safest mode that satisfies intent; otherwise coexist. When
    # already inside WSL, coexist is always the loud default regardless of intent.
    default_mode = "coexist"
    if not inside_wsl:
        for mode in SAFE_INSTALL_MODES:
            if norm_intent and mode["intent"] == norm_intent:
                default_mode = mode["id"]
                break

    # Attach the concrete WSL coexist action to the coexist mode.
    modes = [dict(m) for m in SAFE_INSTALL_MODES]
    for mode in modes:
        if mode["id"] == "coexist":
            mode["action"] = coexist.get("recommended_action")

    if inside_wsl and not norm_intent:
        decision = "proceed-fully-reversible"

    return {
        "read_only": True,
        "decision": decision,
        "proceed": (not blocked and norm_intent is not None) or inside_wsl,
        "intent": norm_intent,
        "risk_level": level,
        "reversibility": report["risk"]["reversibility"],
        "blast_radius": report["risk"]["blast_radius"],
        "blockers": report["risk"]["blockers"],
        "recovery_media_gate": gate,
        "coexist": coexist,
        "running_inside_wsl": inside_wsl,
        "safe_install_modes": modes,
        "default_mode": default_mode,
        "install_plan": plan,
        "report": report,
        "facts": facts,
    }


def render_preflight_text(result: dict) -> str:
    """Render a preflight decision as a human-readable report."""
    lines: list[str] = []
    add = lines.append
    add("ChaseOS Install-Safety - Preflight (read-only; nothing was changed)")
    add("=" * 64)
    add(f"Declared intent : {result.get('intent') or '(none - choose a safe mode)'}")
    add(f"Risk level      : {str(result.get('risk_level')).upper()}")
    add(f"Reversibility   : {result.get('reversibility')}")
    add(f"Blast radius    : {result.get('blast_radius')}")
    add(f"Decision        : {result.get('decision')}")
    add("")

    gate = result.get("recovery_media_gate") or {}
    if gate.get("required"):
        add(f"Recovery-media gate: {'PASSED' if gate.get('passed') else 'BLOCKED'}")
        for m in gate.get("missing") or []:
            add(f"  - MISSING: {m}")
        add("")

    if result.get("blockers"):
        add("Blockers:")
        for b in result["blockers"]:
            add(f"  ! {b}")
        add("")

    coexist = result.get("coexist") or {}
    if coexist.get("recommended_action"):
        add("Coexist (WSL) - safest, fully reversible:")
        add(f"  {coexist['recommended_action']}")
        add("")

    add("Safe install modes (safest first):")
    for mode in result.get("safe_install_modes") or []:
        mark = " <- recommended default" if mode["id"] == result.get("default_mode") else ""
        add(f"  - {mode['label']}{mark}")
    add("")

    plan = result.get("install_plan")
    if plan:
        add("Install plan (PLAN ONLY - ChaseOS does not execute it):")
        for i, step in enumerate(plan.get("steps") or [], 1):
            add(f"  {i}. {step}")
        if plan.get("confirmation_required"):
            add("")
            add(f"  To proceed manually, type this confirmation token: {plan.get('confirmation_token')}")
        add("")
        add("Rollback card:")
        for line in plan.get("rollback_card") or []:
            add(f"  {line}")
        add("")

    return "\n".join(lines).rstrip() + "\n"
