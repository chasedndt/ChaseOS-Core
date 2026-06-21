"""Runtime Startup Doctor models for ChaseOS Studio.

The doctor is product-facing and safe by default: it reads declared lifecycle
records, private gateway config redacted status, and Studio runtime-service
models. It does not start processes, probe providers, display secrets, or mutate
host startup unless a separate operator-approved action is invoked elsewhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.lifecycle.health_cli import load_lifecycle_record
from runtime.lifecycle.hermes_gateway_config import build_hermes_gateway_config_status


SURFACE_ID = "runtime_doctor"
MODEL_VERSION = "runtime.doctor.v1"
PRODUCT_SURFACE = "Settings → Runtime Services"


def _runtime_lifecycle_dir(vault: Path) -> Path:
    return vault / "runtime" / "lifecycle"


def discover_lifecycle_runtime_ids(vault_root: str | Path) -> list[str]:
    """Return all declared runtime lifecycle ids, with live runtimes first."""

    vault = Path(vault_root).resolve()
    preferred = ["hermes", "openclaw"]
    discovered = []
    lifecycle_dir = _runtime_lifecycle_dir(vault)
    if lifecycle_dir.exists():
        for path in sorted(lifecycle_dir.glob("*.lifecycle.yaml")):
            runtime_id = path.name.removesuffix(".lifecycle.yaml").strip().lower()
            if runtime_id:
                discovered.append(runtime_id)
    ordered: list[str] = []
    for runtime_id in preferred + discovered:
        if runtime_id not in ordered:
            ordered.append(runtime_id)
    return ordered


def build_first_run_bootstrap_model(
    vault_root: str | Path,
    *,
    runtime_id: str = "all",
) -> dict[str, Any]:
    """Build the Studio first-run runtime bootstrap card model."""

    vault = Path(vault_root).resolve()
    target = str(runtime_id or "all").strip().lower()
    hermes_status = build_hermes_gateway_config_status(vault_root=vault)
    gateway_ready = bool(hermes_status.get("gateway_ready"))
    problem_label = (
        "Runtime services ready"
        if gateway_ready
        else "Gateway allowlist/config incomplete"
    )
    return {
        "ok": True,
        "surface": "studio_settings_runtime_services",
        "shown_in": PRODUCT_SURFACE,
        "shown_for_runtime": target,
        "title": "First-run Runtime Setup",
        "problem_label": problem_label,
        "description": (
            "Use Startup Doctor to verify lifecycle records, gateway config, "
            "allowlists, autostart registration, daemon heartbeat posture, and "
            "runtime cards before relying on background agents."
        ),
        "primary_cta_label": "Run Startup Doctor",
        "config_cta_label": "Fix Gateway Config",
        "doctor_command": "chaseos runtime doctor --runtime all --json",
        "hermes_config_command": "chaseos runtime hermes-gateway-config --action status --json",
        "host_mutation_on_page_load": False,
        "raw_secret_values_displayed": False,
        "applies_to_runtime_ids": discover_lifecycle_runtime_ids(vault),
        "hermes_gateway_status": {
            "gateway_ready": gateway_ready,
            "allowlist_status": hermes_status.get("allowlist_status"),
            "messaging_platform_status": hermes_status.get("messaging_platform_status"),
            "redacted_status_only": True,
        },
    }


def _lifecycle_check(vault: Path, runtime_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        record = load_lifecycle_record(runtime_id, vault)
    except Exception as exc:  # pragma: no cover - exercised through bad local state
        return None, {
            "ok": False,
            "status": "missing_or_invalid",
            "detail": str(exc),
        }
    return record, {
        "ok": True,
        "status": "declared",
        "platform": record.get("platform"),
        "lifecycle_mode": record.get("lifecycle_mode"),
        "runtime_name": (record.get("coordination_watch") or {}).get("runtime_name") or runtime_id,
    }


def _startup_surface_check(record: dict[str, Any] | None) -> dict[str, Any]:
    surfaces = record.get("startup_surfaces") if isinstance(record, dict) else {}
    if not isinstance(surfaces, dict):
        surfaces = {}
    return {
        "ok": bool(surfaces),
        "status": "declared" if surfaces else "not_declared",
        "surface_ids": sorted(surfaces.keys()),
        "gateway_declared": "gateway" in surfaces,
        "daemon_declared": "daemon" in surfaces or "coordination_watch" in surfaces,
    }


def _binary_presence_check(vault: Path, runtime_id: str, runner=None) -> dict[str, Any]:
    """Optional Stage-1 binary-presence probe (gated behind ``probe_binaries``).

    Informational: ``ok`` reflects that the probe ran, not whether the binary is
    installed — an absent runtime is a normal pre-activation state, not a doctor
    failure, so this never flips ``startup_posture_ok``.
    """
    try:
        from runtime.lifecycle.runtime_detect import detect_runtime

        detection = detect_runtime(runtime_id, vault, runner=runner)
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": True, "status": "probe_error", "present": False, "detail": str(exc)}
    return {
        "ok": True,
        "status": detection.get("status"),
        "present": bool(detection.get("present")),
        "version": detection.get("version"),
        "supported": bool(detection.get("supported", True)),
        "companion": detection.get("companion"),
        "detect_method": detection.get("detect_method"),
    }


def build_runtime_doctor_report(
    vault_root: str | Path,
    *,
    runtime_id: str = "all",
    probe_processes: bool = False,
    probe_binaries: bool = False,
    runner=None,
) -> dict[str, Any]:
    """Build a safe runtime startup diagnostic report."""

    vault = Path(vault_root).resolve()
    target = str(runtime_id or "all").strip().lower()
    runtime_ids = discover_lifecycle_runtime_ids(vault) if target == "all" else [target]
    runtime_reports: list[dict[str, Any]] = []
    aggregate_ok = True

    for rid in runtime_ids:
        record, lifecycle = _lifecycle_check(vault, rid)
        checks: dict[str, Any] = {
            "lifecycle_record": lifecycle,
            "startup_surfaces": _startup_surface_check(record),
            "process_probe": {
                "ok": True,
                "status": "skipped" if not probe_processes else "delegated_to_runtime_services_refresh",
                "probe_processes": bool(probe_processes),
            },
        }
        if rid == "hermes":
            gateway_status = build_hermes_gateway_config_status(vault_root=vault)
            checks["gateway_config"] = {
                "ok": bool(gateway_status.get("gateway_ready")),
                "status": "ready" if gateway_status.get("gateway_ready") else "needs_first_run_config",
                "allowlist_status": gateway_status.get("allowlist_status"),
                "messaging_platform_status": gateway_status.get("messaging_platform_status"),
                "redacted_status_only": True,
            }
        if probe_binaries:
            checks["binary_presence"] = _binary_presence_check(vault, rid, runner)
        report_ok = all(bool(item.get("ok")) for item in checks.values())
        aggregate_ok = aggregate_ok and report_ok
        runtime_reports.append(
            {
                "runtime_id": rid,
                "ok": report_ok,
                "checks": checks,
                "next_step": "Open Settings → Runtime Services and use Run Startup Doctor/Fix Gateway Config"
                if not report_ok
                else "Runtime startup posture declared",
            }
        )

    single = runtime_reports[0] if len(runtime_reports) == 1 else None
    return {
        "ok": True,
        "surface": SURFACE_ID,
        "model_version": MODEL_VERSION,
        "runtime_id": target,
        "shown_in": PRODUCT_SURFACE,
        "startup_posture_ok": aggregate_ok,
        "runtime_count": len(runtime_reports),
        "runtimes": runtime_reports,
        "checks": single["checks"] if single else {},
        "first_run_bootstrap": build_first_run_bootstrap_model(vault, runtime_id=target),
        "authority": {
            "read_only_status_visible": True,
            "host_mutation_on_page_load": False,
            "provider_calls_allowed": False,
            "connector_calls_allowed": False,
            "canonical_mutation_allowed": False,
            "secret_values_displayed": False,
        },
        "security": {
            "secret_values_included": False,
            "raw_credentials_included": False,
            "redacted_status_only": True,
        },
    }
