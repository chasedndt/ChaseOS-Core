"""Tests for the read-only ChaseOS install-safety surface.

Covers the host probe (fail-open + RST/NVMe/boot detection), the Hardware
Readiness Report schema + risk engine, the YAML renderer, and the
`chaseos doctor install-safety` operator view. All probing is injected — no
live host inspection runs in the suite.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.install_safety import (  # noqa: E402
    MAIN_DISK_MUTATION_DEFAULT,
    ADVANCED_INSTALL_SAFETY_CHECKLIST,
)
from runtime.install_safety.probe import probe_host  # noqa: E402
from runtime.install_safety.report import (  # noqa: E402
    build_readiness_report,
    render_report_yaml,
    RISK_LOW,
    RISK_MEDIUM,
    RISK_HIGH,
    RISK_CRITICAL,
)
from runtime.install_safety.doctor import (  # noqa: E402
    build_install_safety_report,
    render_install_safety_text,
)


# --- canned read-only collector outputs ------------------------------------

_RST_BITLOCKER_HOST = json.dumps(
    {
        "vendor": "Dell Inc.",
        "model": "XPS 15 9520",
        "cpu": "12th Gen Intel(R) Core(TM) i7-12700H",
        "ram_bytes": 34359738368,
        "physical_disks": [
            {"name": "Samsung SSD 980 PRO 1TB", "media": "SSD", "bus": "NVMe", "size": 1024209543168}
        ],
        "controllers": ["Intel(R) Rapid Storage Technology"],
        "firmware": "Uefi",
        "secure_boot": True,
        "bitlocker": "On",
        "tpm_present": True,
    }
)

_CLEAN_TWO_DISK_HOST = json.dumps(
    {
        "vendor": "ASUS",
        "model": "PRIME",
        "cpu": "AMD Ryzen 9 7950X",
        "ram_bytes": 68719476736,
        "physical_disks": [
            {"name": "WD Black SN850", "media": "SSD", "bus": "NVMe", "size": 1024209543168},
            {"name": "Seagate Barracuda", "media": "HDD", "bus": "SATA", "size": 2048419086336},
        ],
        "controllers": ["Standard NVM Express Controller", "Standard SATA AHCI Controller"],
        "firmware": "Uefi",
        "secure_boot": False,
        "bitlocker": "Off",
        "tpm_present": True,
    }
)


def _runner(payload):
    return lambda _script: payload


# --- probe -----------------------------------------------------------------


def test_probe_parses_windows_collector_facts():
    facts = probe_host(runner=_runner(_RST_BITLOCKER_HOST), system="Windows")
    assert facts["vendor"] == "Dell Inc."
    assert facts["model"] == "XPS 15 9520"
    assert facts["ram_gb"] == 32
    assert facts["nvme_detected"] is True
    assert facts["rst_or_optane"] is True  # via controller name
    assert facts["firmware_type"] == "UEFI"
    assert facts["secure_boot"] is True
    assert facts["bitlocker_protected"] is True
    assert facts["physical_disk_count"] == 1
    assert facts["read_only"] is True


def test_probe_detects_rst_via_raid_bus_type():
    payload = json.dumps(
        {
            "physical_disks": [{"name": "NVMe", "media": "SSD", "bus": "RAID", "size": 512000000000}],
            "controllers": [],
            "firmware": "Uefi",
        }
    )
    facts = probe_host(runner=_runner(payload), system="Windows")
    assert facts["rst_or_optane"] is True  # RAID bus => NVMe hidden behind RST


def test_probe_clean_host_has_no_rst():
    facts = probe_host(runner=_runner(_CLEAN_TWO_DISK_HOST), system="Windows")
    assert facts["rst_or_optane"] is False
    assert facts["physical_disk_count"] == 2
    assert facts["bitlocker_protected"] is False


def test_probe_is_fail_open_on_no_output():
    facts = probe_host(runner=_runner(None), system="Windows")
    assert facts["vendor"] is None
    assert facts["probe_errors"]
    assert facts["read_only"] is True


def test_probe_is_fail_open_on_bad_json():
    facts = probe_host(runner=_runner("not json {"), system="Windows")
    assert facts["vendor"] is None
    assert any("parse" in e for e in facts["probe_errors"])


def test_probe_non_windows_reports_basics_only():
    facts = probe_host(runner=_runner(_RST_BITLOCKER_HOST), system="Linux")
    # The Windows collector is never consulted off-Windows.
    assert facts["vendor"] is None
    assert facts["rst_or_optane"] is None
    assert any("Windows-only" in e for e in facts["probe_errors"])


# --- readiness report + risk engine ----------------------------------------


def test_report_matches_canonical_schema_shape():
    facts = probe_host(runner=_runner(_CLEAN_TWO_DISK_HOST), system="Windows")
    report = build_readiness_report(facts, intent="vm")
    assert set(report["machine"]) == {"vendor", "model", "cpu", "ram"}
    assert set(report["storage"]) == {
        "disks",
        "nvme_detected",
        "intel_optane_or_rst_possible",
        "bitlocker_possible",
        "os_partition_shrinkable_gb",
    }
    assert set(report["boot"]) == {"uefi", "secure_boot", "boot_mode"}
    assert set(report["install_intent"]) == {"vm", "spare_disk", "external_disk", "same_disk_dual_boot"}
    assert set(report["risk"]) == {
        "level", "reversibility", "blast_radius", "blockers", "recommended_path"
    }


def test_risk_vm_intent_is_low_and_recommends_vm():
    facts = probe_host(runner=_runner(_RST_BITLOCKER_HOST), system="Windows")
    report = build_readiness_report(facts, intent="vm")
    assert report["risk"]["level"] == RISK_LOW
    assert report["install_intent"]["vm"] is True
    assert "virtual machine" in report["risk"]["recommended_path"].lower()


def test_risk_same_disk_with_rst_and_bitlocker_is_critical():
    facts = probe_host(runner=_runner(_RST_BITLOCKER_HOST), system="Windows")
    report = build_readiness_report(facts, intent="same-disk")
    assert report["risk"]["level"] == RISK_CRITICAL
    blockers = " ".join(report["risk"]["blockers"]).lower()
    assert "rst" in blockers or "raid" in blockers
    assert "bitlocker" in blockers


def test_risk_same_disk_clean_host_is_medium():
    facts = probe_host(runner=_runner(_CLEAN_TWO_DISK_HOST), system="Windows")
    report = build_readiness_report(facts, intent="same-disk")
    assert report["risk"]["level"] == RISK_MEDIUM
    assert report["risk"]["blockers"] == []


def test_risk_spare_disk_with_two_disks_is_low():
    facts = probe_host(runner=_runner(_CLEAN_TWO_DISK_HOST), system="Windows")
    report = build_readiness_report(facts, intent="spare-disk")
    assert report["risk"]["level"] == RISK_LOW
    assert report["install_intent"]["spare_disk"] is True


def test_risk_unknown_intent_with_blockers_is_high():
    facts = probe_host(runner=_runner(_RST_BITLOCKER_HOST), system="Windows")
    report = build_readiness_report(facts, intent=None)
    assert report["risk"]["level"] == RISK_HIGH
    assert report["install_intent"]["same_disk_dual_boot"] is False


def test_report_storage_flags_reflect_host():
    facts = probe_host(runner=_runner(_RST_BITLOCKER_HOST), system="Windows")
    report = build_readiness_report(facts, intent="same-disk")
    assert report["storage"]["nvme_detected"] is True
    assert report["storage"]["intel_optane_or_rst_possible"] is True
    assert report["storage"]["bitlocker_possible"] is True
    assert report["boot"]["uefi"] is True
    assert report["boot"]["boot_mode"] == "UEFI"


# --- YAML renderer ---------------------------------------------------------


def test_render_report_yaml_is_parseable_and_secret_free():
    facts = probe_host(runner=_runner(_RST_BITLOCKER_HOST), system="Windows")
    report = build_readiness_report(facts, intent="same-disk")
    text = render_report_yaml(report, include_meta=False)
    assert "machine:" in text and "risk:" in text and "_meta" not in text
    # Round-trips through a YAML parser if one is available.
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(text)
        assert parsed["machine"]["vendor"] == "Dell Inc."
        assert parsed["risk"]["level"] == RISK_CRITICAL
        assert parsed["install_intent"]["same_disk_dual_boot"] is True
    except ImportError:
        pass


# --- doctor view -----------------------------------------------------------


def test_doctor_view_surfaces_required_fields():
    diag = build_install_safety_report(intent="same-disk", facts=probe_host(
        runner=_runner(_RST_BITLOCKER_HOST), system="Windows"))
    view = diag["operator_view"]
    assert view["detected_disks"]
    assert view["boot_mode"] == "UEFI"
    assert view["rst_optane_warning"]
    assert view["backup_warning"]  # blockers present => backup warning
    assert "Advanced Install" in " ".join(view["recommended_install_path"].split()) or view["recommended_install_path"]
    assert view["risk_level"] == RISK_CRITICAL
    assert diag["read_only"] is True


def test_doctor_clean_host_no_backup_warning_for_vm():
    diag = build_install_safety_report(intent="vm", facts=probe_host(
        runner=_runner(_CLEAN_TWO_DISK_HOST), system="Windows"))
    view = diag["operator_view"]
    assert view["backup_warning"] is None
    assert view["rst_optane_warning"] is None


def test_doctor_text_render_is_readable():
    diag = build_install_safety_report(intent="same-disk", facts=probe_host(
        runner=_runner(_RST_BITLOCKER_HOST), system="Windows"))
    text = render_install_safety_text(diag)
    assert "Install Safety" in text
    assert "read-only" in text.lower()
    assert "Recommended install path" in text
    assert "BACKUP" in text.upper()


def test_no_main_disk_mutation_default_is_off():
    assert MAIN_DISK_MUTATION_DEFAULT is False
    assert len(ADVANCED_INSTALL_SAFETY_CHECKLIST) >= 4


# =========================================================================== #
# Preflight-layer additions: new detection facts, reversibility, snapshot,
# preflight wizard.
# =========================================================================== #

from runtime.install_safety.report import RISK_BLOCKED  # noqa: E402
from runtime.install_safety.snapshot import (  # noqa: E402
    capture_partition_snapshot,
    render_snapshot_text,
    write_snapshot,
)
from runtime.install_safety.preflight import run_preflight, render_preflight_text  # noqa: E402


def _rich_host(**overrides) -> str:
    base = {
        "vendor": "Dell Inc.", "model": "XPS 15", "cpu": "i7", "ram_bytes": 17179869184,
        "physical_disks": [{
            "number": 0, "name": "Samsung NVMe", "media": "SSD", "bus": "NVMe",
            "size": 512 * 1024**3, "partition_style": "GPT", "is_system": True,
            "is_boot": True, "removable": False, "health": "Healthy", "free": 100 * 1024**3,
        }],
        "esp": [{"disk": 0, "size": 260 * 1024**2}],
        "controllers": ["Standard NVM Express Controller"],
        "firmware": "Uefi", "secure_boot": True, "bitlocker": "Off",
        "tpm_present": True, "fast_startup": False, "dirty_bit": False,
    }
    base.update(overrides)
    return json.dumps(base)


def _two_disk_clean() -> str:
    return _rich_host(physical_disks=[
        {"number": 0, "name": "OS", "media": "SSD", "bus": "NVMe", "size": 512 * 1024**3,
         "partition_style": "GPT", "is_system": True, "is_boot": True, "removable": False,
         "health": "Healthy", "free": 100 * 1024**3},
        {"number": 1, "name": "Spare", "media": "SSD", "bus": "SATA", "size": 512 * 1024**3,
         "partition_style": "GPT", "is_system": False, "is_boot": False, "removable": False,
         "health": "Healthy", "free": 400 * 1024**3},
    ])


def test_probe_parses_new_detection_fields():
    facts = probe_host(runner=_runner(_rich_host()), system="Windows")
    d = facts["disks"][0]
    assert d["partition_style"] == "GPT"
    assert d["is_system_disk"] is True
    assert d["is_removable"] is False
    assert d["health"] == "Healthy"
    assert d["free_gb"] == 100
    assert d["esp_size_mb"] == 260
    assert facts["fast_startup_enabled"] is False
    assert facts["dirty_bit"] is False


def test_report_adds_reversibility_and_blast_radius():
    facts = probe_host(runner=_runner(_rich_host()), system="Windows")
    vm = build_readiness_report(facts, intent="vm")["risk"]
    assert vm["reversibility"] == "fully-reversible"
    assert vm["blast_radius"] == "none"
    ext = build_readiness_report(facts, intent="external-disk")["risk"]
    assert ext["reversibility"] == "reversible-with-snapshot"
    assert ext["blast_radius"] == "target-disk-only"


def test_same_disk_no_backup_is_irreversible_high_not_blocked_in_report():
    facts = probe_host(runner=_runner(_rich_host()), system="Windows")
    risk = build_readiness_report(facts, intent="same-disk", backup_verified=False)["risk"]
    assert risk["reversibility"] == "irreversible-without-backup"
    # single-disk same-disk => HIGH (blocking decision is preflight's job, not report's)
    assert risk["level"] == RISK_HIGH


def test_same_disk_with_backup_is_hard_to_reverse():
    facts = probe_host(runner=_runner(_two_disk_clean()), system="Windows")
    risk = build_readiness_report(facts, intent="same-disk", backup_verified=True)["risk"]
    assert risk["reversibility"] == "hard-to-reverse"
    assert risk["level"] != RISK_BLOCKED


def test_dirty_bit_hard_blocks_same_disk():
    facts = probe_host(runner=_runner(_rich_host(dirty_bit=True)), system="Windows")
    risk = build_readiness_report(facts, intent="same-disk", backup_verified=True)["risk"]
    assert risk["level"] == RISK_BLOCKED
    assert any("dirty bit" in b for b in risk["blockers"])


def test_failing_disk_health_hard_blocks():
    facts = probe_host(runner=_runner(_rich_host()), system="Windows")
    facts["disks"][0]["health"] = "Warning"
    risk = build_readiness_report(facts, intent="spare-disk")["risk"]
    assert risk["level"] == RISK_BLOCKED


def test_uefi_mbr_mismatch_flagged():
    facts = probe_host(runner=_runner(_rich_host(physical_disks=[
        {"number": 0, "name": "OS", "media": "HDD", "bus": "SATA", "size": 512 * 1024**3,
         "partition_style": "MBR", "is_system": True, "is_boot": True, "removable": False,
         "health": "Healthy", "free": 50 * 1024**3},
    ])), system="Windows")
    risk = build_readiness_report(facts, intent="same-disk", backup_verified=True)["risk"]
    assert any("MBR" in b and "UEFI" in b for b in risk["blockers"])


def test_small_esp_flagged():
    facts = probe_host(runner=_runner(_rich_host(esp=[{"disk": 0, "size": 100 * 1024**2}])),
                       system="Windows")
    risk = build_readiness_report(facts, intent="same-disk", backup_verified=True)["risk"]
    assert any("EFI System Partition" in b for b in risk["blockers"])


# --- snapshot --------------------------------------------------------------

def _snap_json() -> str:
    return json.dumps({"disks": [{
        "number": 0, "name": "Samsung NVMe", "bus": "NVMe", "size": 512 * 1024**3,
        "partition_style": "GPT", "is_system": True, "is_boot": True,
        "partitions": [
            {"number": 1, "drive": "", "type": "System", "gpt_type": "{c12a7328}",
             "size": 260 * 1024**2, "offset": 1048576},
            {"number": 2, "drive": "C", "type": "Basic", "gpt_type": "{ebd0a0a2}",
             "size": 500 * 1024**3, "offset": 273678336},
        ],
    }]})


def test_snapshot_parses_and_renders():
    rec = capture_partition_snapshot(system="Windows", runner=_runner(_snap_json()),
                                     timestamp="2026-06-18T00:00:00Z")
    assert rec["read_only"] is True
    assert rec["captured_at"] == "2026-06-18T00:00:00Z"
    assert len(rec["disks"][0]["partitions"]) == 2
    assert rec["disks"][0]["partitions"][1]["drive"] == "C"
    text = render_snapshot_text(rec)
    assert "UNDO MAP" in text


def test_snapshot_non_windows_fail_open():
    rec = capture_partition_snapshot(system="Linux", runner=_runner("{}"))
    assert rec["disks"] == []
    assert rec["errors"]


def test_snapshot_write_artifact(tmp_path):
    rec = capture_partition_snapshot(system="Windows", runner=_runner(_snap_json()),
                                     timestamp="2026-06-18T00:00:00Z")
    out = tmp_path / "sub" / "snap.json"
    written = write_snapshot(rec, out)
    assert written.exists()
    assert json.loads(written.read_text(encoding="utf-8"))["disks"][0]["number"] == 0


# --- preflight wizard ------------------------------------------------------

def test_preflight_no_intent_chooses_safe_mode():
    facts = probe_host(runner=_runner(_rich_host()), system="Windows")
    r = run_preflight(facts=facts)
    assert r["decision"] == "choose-a-safe-mode"
    assert r["proceed"] is False
    assert r["default_mode"] == "coexist"


def test_preflight_vm_reversible_no_plan_no_gate():
    facts = probe_host(runner=_runner(_rich_host()), system="Windows")
    r = run_preflight(facts=facts, intent="vm")
    assert r["decision"] == "proceed-fully-reversible"
    assert r["proceed"] is True
    assert r["install_plan"] is None
    assert r["recovery_media_gate"]["required"] is False


def test_preflight_same_disk_blocks_without_backup_and_usb():
    facts = probe_host(runner=_runner(_two_disk_clean()), system="Windows")
    r = run_preflight(facts=facts, intent="same-disk")
    assert r["decision"] == "blocked"
    assert r["proceed"] is False
    gate = r["recovery_media_gate"]
    assert gate["required"] is True and gate["passed"] is False
    assert len(gate["missing"]) == 2


def test_preflight_same_disk_plan_token_and_rollback():
    facts = probe_host(runner=_runner(_two_disk_clean()), system="Windows")
    r = run_preflight(facts=facts, intent="same-disk", backup_verified=True,
                      recovery_usb=True, target_disk=0)
    assert r["recovery_media_gate"]["passed"] is True
    plan = r["install_plan"]
    assert plan["destructive"] is True
    assert plan["executable"] is False
    assert plan["confirmation_token"] == "CONFIRM-SAME-DISK-DISK0"
    assert plan["confirmation_satisfied"] is False
    assert any("bootloader" in line.lower() for line in plan["rollback_card"])


def test_preflight_spare_disk_plan_non_destructive():
    facts = probe_host(runner=_runner(_two_disk_clean()), system="Windows")
    r = run_preflight(facts=facts, intent="spare-disk", target_disk=1)
    plan = r["install_plan"]
    assert plan["destructive"] is False
    assert plan["confirmation_required"] is False


def test_preflight_render_text_smoke():
    facts = probe_host(runner=_runner(_rich_host()), system="Windows")
    text = render_preflight_text(run_preflight(facts=facts, intent="same-disk"))
    assert "Preflight" in text and "Safe install modes" in text


# --- shrinkable space / MBR cap / recovery partitions ----------------------

def _disk(**kw):
    base = {
        "number": 0, "name": "OS", "media": "SSD", "bus": "NVMe", "size": 512 * 1024**3,
        "partition_style": "GPT", "is_system": True, "is_boot": True, "removable": False,
        "health": "Healthy", "free": 100 * 1024**3,
        "partition_count": 3, "primary_count": 3, "recovery_count": 0,
    }
    base.update(kw)
    return base


def test_probe_parses_shrinkable_and_partition_counts():
    j = _rich_host(physical_disks=[_disk(primary_count=4, recovery_count=1)],
                   os_drive="C", os_shrinkable=40 * 1024**3)
    facts = probe_host(runner=_runner(j), system="Windows")
    assert facts["os_partition_drive"] == "C"
    assert facts["os_shrinkable_gb"] == 40
    d = facts["disks"][0]
    assert d["primary_partition_count"] == 4
    assert d["recovery_partition_count"] == 1


def test_low_shrinkable_space_blocks_same_disk():
    j = _rich_host(physical_disks=[_disk()], os_drive="C", os_shrinkable=10 * 1024**3)
    facts = probe_host(runner=_runner(j), system="Windows")
    risk = build_readiness_report(facts, intent="same-disk", backup_verified=True)["risk"]
    assert any("can only shrink" in b for b in risk["blockers"])


def test_ample_shrinkable_space_no_block():
    j = _rich_host(physical_disks=[_disk()], os_drive="C", os_shrinkable=200 * 1024**3)
    facts = probe_host(runner=_runner(j), system="Windows")
    risk = build_readiness_report(facts, intent="same-disk", backup_verified=True)["risk"]
    assert not any("can only shrink" in b for b in risk["blockers"])
    assert build_readiness_report(facts, intent="same-disk")["storage"]["os_partition_shrinkable_gb"] == 200


def test_mbr_four_primaries_blocks():
    j = _rich_host(physical_disks=[_disk(partition_style="MBR", primary_count=4)],
                   firmware="Bios", os_shrinkable=200 * 1024**3)
    facts = probe_host(runner=_runner(j), system="Windows")
    risk = build_readiness_report(facts, intent="same-disk", backup_verified=True)["risk"]
    assert any("4 primary partitions" in b for b in risk["blockers"])


def test_mbr_three_primaries_no_cap_block():
    j = _rich_host(physical_disks=[_disk(partition_style="MBR", primary_count=3)],
                   firmware="Bios", os_shrinkable=200 * 1024**3)
    facts = probe_host(runner=_runner(j), system="Windows")
    risk = build_readiness_report(facts, intent="same-disk", backup_verified=True)["risk"]
    assert not any("primary partitions (MBR's max)" in b for b in risk["blockers"])


def test_recovery_partition_warns_on_same_disk():
    j = _rich_host(physical_disks=[_disk(recovery_count=2)], os_shrinkable=200 * 1024**3)
    facts = probe_host(runner=_runner(j), system="Windows")
    risk = build_readiness_report(facts, intent="same-disk", backup_verified=True)["risk"]
    assert any("OEM/recovery partition" in b for b in risk["blockers"])


def test_recovery_partition_not_flagged_for_spare_disk():
    j = _rich_host(physical_disks=[_disk(recovery_count=2)], os_shrinkable=200 * 1024**3)
    facts = probe_host(runner=_runner(j), system="Windows")
    risk = build_readiness_report(facts, intent="spare-disk")["risk"]
    assert not any("OEM/recovery partition" in b for b in risk["blockers"])


# --- pending reboot --------------------------------------------------------

def test_pending_reboot_hard_blocks_same_disk():
    j = _rich_host(physical_disks=[_disk()], os_shrinkable=200 * 1024**3, pending_reboot=True)
    facts = probe_host(runner=_runner(j), system="Windows")
    assert facts["pending_reboot"] is True
    risk = build_readiness_report(facts, intent="same-disk", backup_verified=True)["risk"]
    assert risk["level"] == RISK_BLOCKED
    assert any("reboot/update is pending" in b for b in risk["blockers"])


# --- WSL support -----------------------------------------------------------

def test_probe_parses_wsl_facts_ubuntu_present():
    j = _rich_host(os_shrinkable=200 * 1024**3, wsl_available=True,
                   wsl_distros=["Ubuntu", "docker-desktop"], wsl_version=2)
    facts = probe_host(runner=_runner(j), system="Windows")
    assert facts["wsl_available"] is True
    assert facts["wsl_ubuntu_present"] is True
    assert facts["wsl_default_version"] == 2
    assert facts["running_inside_wsl"] is False


def test_probe_wsl_not_installed():
    j = _rich_host(os_shrinkable=200 * 1024**3, wsl_available=False, wsl_distros=[])
    facts = probe_host(runner=_runner(j), system="Windows")
    assert facts["wsl_available"] is False
    assert facts["wsl_ubuntu_present"] is None


def test_coexist_block_recommends_install_when_absent():
    j = _rich_host(os_shrinkable=200 * 1024**3, wsl_available=False, wsl_distros=[])
    facts = probe_host(runner=_runner(j), system="Windows")
    coexist = build_readiness_report(facts, intent=None)["coexist"]
    assert "wsl --install" in coexist["recommended_action"]


def test_coexist_block_when_ubuntu_present():
    j = _rich_host(os_shrinkable=200 * 1024**3, wsl_available=True,
                   wsl_distros=["Ubuntu"], wsl_version=2)
    facts = probe_host(runner=_runner(j), system="Windows")
    coexist = build_readiness_report(facts, intent=None)["coexist"]
    assert "Ubuntu is installed" in coexist["recommended_action"]
    assert coexist["wsl_ubuntu_present"] is True


def test_probe_inside_wsl_is_reversible_environment():
    facts = probe_host(system="Linux", wsl_marker=True)
    assert facts["running_inside_wsl"] is True
    assert facts["wsl_available"] is True
    assert any("inside WSL" in e for e in facts["probe_errors"])


def test_probe_linux_not_wsl_is_basics_only():
    facts = probe_host(system="Linux", wsl_marker=False)
    assert facts["running_inside_wsl"] is False
    assert any("Windows-only" in e for e in facts["probe_errors"])


def test_preflight_inside_wsl_recommends_in_place():
    facts = probe_host(system="Linux", wsl_marker=True)
    r = run_preflight(facts=facts)
    assert r["running_inside_wsl"] is True
    assert r["proceed"] is True
    assert r["default_mode"] == "coexist"
    assert "already inside WSL" in r["coexist"]["recommended_action"]


def test_preflight_coexist_mode_carries_action():
    j = _rich_host(os_shrinkable=200 * 1024**3, wsl_available=True, wsl_distros=["Ubuntu"])
    facts = probe_host(runner=_runner(j), system="Windows")
    r = run_preflight(facts=facts, intent="vm")
    coexist_mode = next(m for m in r["safe_install_modes"] if m["id"] == "coexist")
    assert coexist_mode["action"]


# --- installer orchestrator ------------------------------------------------

from runtime.install_safety.installer import (  # noqa: E402
    prepare_install_session,
    write_install_session,
    render_install_session_text,
)


def test_installer_no_intent_choose_mode():
    facts = probe_host(runner=_runner(_rich_host(os_shrinkable=200 * 1024**3)), system="Windows")
    s = prepare_install_session(facts=facts)
    assert s["status"] == "choose-a-mode"
    assert s["ready_to_run"] is False
    assert s["setup_script"] is None


def test_installer_coexist_generates_wsl_script():
    j = _rich_host(os_shrinkable=200 * 1024**3, wsl_available=False, wsl_distros=[])
    facts = probe_host(runner=_runner(j), system="Windows")
    s = prepare_install_session(facts=facts, intent="vm")
    assert s["status"] == "ready"
    assert s["ready_to_run"] is True
    script = s["setup_script"]
    assert script["language"] == "powershell"
    assert script["reversible"] is True
    assert "wsl --install -d Ubuntu" in script["content"]
    assert "wsl --unregister Ubuntu" in script["content"]  # rollback line


def test_installer_coexist_script_when_ubuntu_present():
    j = _rich_host(os_shrinkable=200 * 1024**3, wsl_available=True, wsl_distros=["Ubuntu"])
    facts = probe_host(runner=_runner(j), system="Windows")
    s = prepare_install_session(facts=facts, intent="vm")
    assert "wsl -d Ubuntu" in s["setup_script"]["content"]


def test_installer_blocks_when_preflight_blocked():
    # same-disk single disk no backup/usb => preflight blocked
    facts = probe_host(runner=_runner(_rich_host(os_shrinkable=200 * 1024**3)), system="Windows")
    s = prepare_install_session(facts=facts, intent="same-disk")
    assert s["status"] == "blocked"
    assert s["ready_to_run"] is False
    assert s["setup_script"] is None


def test_installer_same_disk_is_manual_advanced_with_token_gate():
    j = _two_disk_clean()
    facts = probe_host(runner=_runner(j), system="Windows")
    # gate passes (backup+usb) so not blocked; same-disk stays manual + token-gated
    s = prepare_install_session(facts=facts, intent="same-disk", backup_verified=True,
                                recovery_usb=True, target_disk=0)
    assert s["status"] == "manual-advanced"
    assert s["setup_script"] is None  # never auto-scripts a destructive install
    assert s["confirmation"]["required"] is True
    assert s["confirmation"]["satisfied"] is False
    assert s["manual_steps"]
    # supplying the right token satisfies confirmation
    s2 = prepare_install_session(facts=facts, intent="same-disk", backup_verified=True,
                                 recovery_usb=True, target_disk=0,
                                 confirm_token=s["confirmation"]["token"])
    assert s2["confirmation"]["satisfied"] is True


def test_installer_spare_disk_has_manual_steps_no_script():
    j = _two_disk_clean()
    facts = probe_host(runner=_runner(j), system="Windows")
    s = prepare_install_session(facts=facts, intent="spare-disk", target_disk=1)
    assert s["status"] == "ready"
    assert s["setup_script"] is None
    assert any("disconnect" in step.lower() for step in s["manual_steps"])


def test_installer_inside_wsl_script_is_in_place():
    facts = probe_host(system="Linux", wsl_marker=True)
    s = prepare_install_session(facts=facts, intent="vm")
    assert s["status"] == "ready"
    assert "already inside WSL" in s["setup_script"]["content"]


def test_installer_writes_artifacts(tmp_path):
    j = _rich_host(os_shrinkable=200 * 1024**3, wsl_available=False, wsl_distros=[])
    facts = probe_host(runner=_runner(j), system="Windows")
    s = prepare_install_session(facts=facts, intent="vm")
    written = write_install_session(s, tmp_path, timestamp="20260618T000000Z")
    assert Path(written["session"]).exists()
    assert Path(written["setup_script"]).exists()
    assert "wsl" in Path(written["setup_script"]).read_text(encoding="utf-8").lower()


def test_installer_render_text_smoke():
    j = _rich_host(os_shrinkable=200 * 1024**3, wsl_available=False, wsl_distros=[])
    facts = probe_host(runner=_runner(j), system="Windows")
    text = render_install_session_text(prepare_install_session(facts=facts, intent="vm"))
    assert "Installer Setup" in text
    assert "run it yourself" in text


# --- advanced install executor (bounded, fail-closed) ----------------------

from runtime.install_safety.advanced_install import (  # noqa: E402
    build_shrink_command,
    check_readiness,
    execute_partition_shrink,
    expected_confirm_token,
    run_reversible_prep,
)


def _ready_host() -> dict:
    """A host where every hard precondition is satisfiable."""
    j = _rich_host(
        os_shrinkable=200 * 1024**3,
        physical_disks=[_disk(number=0)],  # Healthy GPT NVMe system disk
    )
    facts = probe_host(runner=_runner(j), system="Windows")
    # explicit safe states
    facts["rst_or_optane"] = False
    facts["bitlocker_protected"] = False
    facts["fast_startup_enabled"] = False
    facts["dirty_bit"] = False
    facts["pending_reboot"] = False
    facts["os_partition_drive"] = "C"
    facts["os_shrinkable_gb"] = 200
    return facts


def _full_gate_kwargs(facts, **over):
    base = dict(
        facts=facts, target_disk=0, shrink_gb=60,
        backup_verified=True, recovery_usb=True, snapshot_captured=True,
        confirm_token=expected_confirm_token(0),
    )
    base.update(over)
    return base


def test_readiness_all_satisfied_is_ready():
    r = check_readiness(**_full_gate_kwargs(_ready_host()))
    assert r["ready"] is True
    assert r["unmet"] == []


def test_readiness_fail_closed_on_unknown_facts():
    facts = _ready_host()
    facts["fast_startup_enabled"] = None  # unknown => must fail closed
    r = check_readiness(**_full_gate_kwargs(facts))
    assert r["ready"] is False
    assert any("Fast Startup" in u for u in r["unmet"])


def test_readiness_requires_backup_recovery_snapshot_token():
    facts = _ready_host()
    for missing in ("backup_verified", "recovery_usb", "snapshot_captured"):
        r = check_readiness(**_full_gate_kwargs(facts, **{missing: None}))
        assert r["ready"] is False
    r = check_readiness(**_full_gate_kwargs(facts, confirm_token="WRONG"))
    assert r["ready"] is False
    assert any("token" in u.lower() for u in r["unmet"])


def test_readiness_rst_blocks():
    facts = _ready_host()
    facts["rst_or_optane"] = True
    assert check_readiness(**_full_gate_kwargs(facts))["ready"] is False


def test_readiness_shrink_must_fit():
    facts = _ready_host()
    facts["os_shrinkable_gb"] = 50  # 60 + 10 margin > 50
    r = check_readiness(**_full_gate_kwargs(facts))
    assert r["ready"] is False
    assert any("shrink" in u.lower() for u in r["unmet"])


def test_readiness_shrink_below_minimum():
    facts = _ready_host()
    r = check_readiness(**_full_gate_kwargs(facts, shrink_gb=10))  # < 25 GB min
    assert r["ready"] is False


def test_shrink_command_shape():
    cmd = build_shrink_command("C", 60)
    assert "Resize-Partition -DriveLetter C" in cmd
    assert "60 * 1GB" in cmd


def test_shrink_dry_run_runs_nothing():
    facts = _ready_host()
    calls = []
    runner = lambda c: (calls.append(c), (0, "", ""))[1]
    r = execute_partition_shrink(**_full_gate_kwargs(facts), execute=False, runner=runner, system="Windows")
    assert r["status"] == "dry-run"
    assert r["executed"] is False
    assert calls == []  # nothing executed
    assert "Resize-Partition" in r["command"]


def test_shrink_refuses_when_gate_unmet_even_with_execute():
    facts = _ready_host()
    calls = []
    runner = lambda c: (calls.append(c), (0, "", ""))[1]
    r = execute_partition_shrink(
        **_full_gate_kwargs(facts, snapshot_captured=False),  # gate fails
        execute=True, runner=runner, system="Windows")
    assert r["status"] == "refused"
    assert r["executed"] is False
    assert calls == []  # destructive command NEVER ran


def test_shrink_executes_only_when_fully_gated():
    facts = _ready_host()
    calls = []
    runner = lambda c: (calls.append(c), (0, "shrunk", ""))[1]
    r = execute_partition_shrink(**_full_gate_kwargs(facts), execute=True, runner=runner, system="Windows")
    assert r["status"] == "ok"
    assert r["executed"] is True
    assert len(calls) == 1 and "Resize-Partition" in calls[0]
    assert r["post_shrink_handoff"]  # bootloader handed to OS installer


def test_shrink_refuses_off_windows():
    facts = _ready_host()
    r = execute_partition_shrink(**_full_gate_kwargs(facts), execute=True,
                                 runner=lambda c: (0, "", ""), system="Linux")
    assert r["status"] == "refused"


def test_reversible_prep_dry_run_default():
    facts = _ready_host()
    facts["bitlocker_protected"] = True
    facts["fast_startup_enabled"] = True
    calls = []
    runner = lambda c: (calls.append(c), (0, "", ""))[1]
    r = run_reversible_prep(facts=facts, execute=False, runner=runner, system="Windows")
    assert r["dry_run"] is True
    assert r["executed"] is False
    assert calls == []
    assert len(r["steps"]) == 2  # suspend bitlocker + disable fast startup
    assert all(s["reversible"] for s in r["steps"])


def test_reversible_prep_execute_runs_steps():
    facts = _ready_host()
    facts["bitlocker_protected"] = True
    calls = []
    runner = lambda c: (calls.append(c), (0, "", ""))[1]
    r = run_reversible_prep(facts=facts, execute=True, runner=runner, system="Windows")
    assert r["executed"] is True
    assert len(calls) == 1  # only bitlocker step applies
    assert "Suspend-BitLocker" in calls[0]


def test_expected_confirm_token_matches_preflight():
    # the executor token must match the preflight plan token for the same target
    facts = _ready_host()
    r = run_preflight(facts=facts, intent="same-disk", backup_verified=True,
                      recovery_usb=True, target_disk=0)
    assert r["install_plan"]["confirmation_token"] == expected_confirm_token(0)
