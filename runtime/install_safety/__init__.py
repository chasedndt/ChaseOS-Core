"""ChaseOS Install-Safety - read-only host inspection for safe onboarding.

This package powers three install-safety surfaces, all read-only:

- ``probe.py``  - inspects the host (disks, boot mode, BitLocker, Intel
  RST/Optane/RAID hints). Never writes, never mutates, fail-open.
- ``report.py`` - turns probe facts into the exportable **ChaseOS Hardware
  Readiness Report** (the canonical share-with-support schema) plus a risk
  assessment and a recommended install path.
- ``doctor.py`` - the operator-facing ``chaseos doctor install-safety`` view:
  detected disks, boot mode, dual-boot risk hints, RST/Optane warning,
  recommended path, and a "do not continue without backup" warning.

Governing default - **No Main Disk Mutation** (see ``MAIN_DISK_MUTATION_DEFAULT``
and ``06_AGENTS/Install-Safety-Preflight-Architecture.md``): ChaseOS onboarding never resizes,
erases, or installs a bootloader to the user's main OS disk unless the user
explicitly chooses Advanced Install and passes the safety checklist.
"""

from __future__ import annotations

# --- Governing default posture -------------------------------------------------
# ChaseOS will NOT resize, erase, or install a bootloader to the main OS disk
# unless the user explicitly chooses Advanced Install AND passes the safety
# checklist. This is the trust-anchor default for early installer/onboarding.
MAIN_DISK_MUTATION_DEFAULT = False

MAIN_DISK_MUTATION_POLICY = (
    "ChaseOS will not resize, erase, or install a bootloader to your main OS "
    "disk unless you explicitly choose Advanced Install and pass the safety "
    "checklist."
)

# The checklist that Advanced Install (same-disk mutation) requires the operator
# to clear before any write to the main OS disk is permitted.
ADVANCED_INSTALL_SAFETY_CHECKLIST = (
    "Full, verified backup of the main OS disk exists and was tested",
    "BitLocker (or other full-disk encryption) is suspended or decrypted, recovery key saved",
    "Storage controller is in AHCI/NVMe mode (not Intel RST/RAID/Optane) so the installer can see the disk",
    "A bootable recovery/repair USB for the existing OS is on hand",
    "You have explicitly chosen Advanced Install with eyes open to the risk",
)

from runtime.install_safety.probe import probe_host  # noqa: E402
from runtime.install_safety.report import build_readiness_report, render_report_yaml  # noqa: E402
from runtime.install_safety.doctor import build_install_safety_report  # noqa: E402
from runtime.install_safety.snapshot import (  # noqa: E402
    capture_partition_snapshot,
    render_snapshot_text,
    write_snapshot,
)
from runtime.install_safety.preflight import run_preflight, render_preflight_text  # noqa: E402
from runtime.install_safety.installer import (  # noqa: E402
    prepare_install_session,
    write_install_session,
    render_install_session_text,
)
from runtime.install_safety.advanced_install import (  # noqa: E402
    check_readiness,
    run_reversible_prep,
    execute_partition_shrink,
    expected_confirm_token,
    render_readiness_text,
    render_shrink_text,
)

__all__ = [
    "MAIN_DISK_MUTATION_DEFAULT",
    "MAIN_DISK_MUTATION_POLICY",
    "ADVANCED_INSTALL_SAFETY_CHECKLIST",
    "probe_host",
    "build_readiness_report",
    "render_report_yaml",
    "build_install_safety_report",
    "capture_partition_snapshot",
    "render_snapshot_text",
    "write_snapshot",
    "run_preflight",
    "render_preflight_text",
    "prepare_install_session",
    "write_install_session",
    "render_install_session_text",
    "check_readiness",
    "run_reversible_prep",
    "execute_partition_shrink",
    "expected_confirm_token",
    "render_readiness_text",
    "render_shrink_text",
]
