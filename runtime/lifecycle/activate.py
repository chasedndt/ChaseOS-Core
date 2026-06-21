"""Runtime activation orchestrator — chains the 7-stage activation contract.

`build_activation_plan` is a read-only, resumable checklist: it derives each
stage's status from existing state (detection, lifecycle record, runtime memory,
gateway config, startup-mutation records, coordination-watch state) and reports
the current stage + next operator action. It is a DRIVER, not new authority —
every host-mutating effect is handed off to an existing governed command.

`run_activation` performs only the one clearly-safe, reversible action on an
explicit confirm: emitting the Stage-3 install scripts to `dist/installer/`.
Register / secrets / startup / daemon stages are handed off as exact commands
(they already have their own confirm/Gate paths). Dry-run is the default.

See `06_AGENTS/Runtime-Setup-and-Activation-Architecture.md` (the design) and the
per-runtime activation runbooks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from runtime.lifecycle.runtime_detect import (
    STATUS_PRESENT,
    DETECT_STRATEGY,
    detect_runtime,
    _default_runner,
)
from runtime.lifecycle.health_cli import load_lifecycle_record


_REPO_ROOT = Path(__file__).resolve().parents[2]

# ordered stage keys + display names
_STAGE_ORDER = [
    ("detect", "Detect"),
    ("preflight", "Preflight"),
    ("install", "Install"),
    ("configure", "Configure"),
    ("secrets", "Secrets"),
    ("register_startup", "Register startup"),
    ("launch_verify", "Launch + verify"),
]

_TS_RE = re.compile(r"(\d{8}T\d{6}Z|\d{8}|\d{14})")


def _vault(vault_root: str | Path | None) -> Path:
    return Path(vault_root).resolve() if vault_root else _REPO_ROOT


def _stage(num: int, key: str, name: str, status: str, detail: str, next_command: str | None = None, **extra: Any) -> dict[str, Any]:
    entry = {"stage": num, "key": key, "name": name, "status": status, "detail": detail}
    if next_command:
        entry["next_command"] = next_command
    entry.update(extra)
    return entry


def _is_registered(vault: Path, rid: str) -> bool:
    return (vault / "runtime" / "memory" / "adapters" / rid / "profile.json").exists()


def _lifecycle_loads(vault: Path, rid: str) -> bool:
    try:
        load_lifecycle_record(rid, vault)
        return True
    except Exception:
        return False


def _wsl_available(runner) -> bool | None:
    res = runner(["wsl", "-l", "-v"])
    if not isinstance(res, dict):
        return None
    if res.get("ok") and res.get("returncode") == 0:
        return True
    if res.get("error") == "not_found":
        return False
    return None


def _startup_enabled(vault: Path, rid: str) -> bool | None:
    """Best-effort: True if the most recent startup-surface mutation for this
    runtime is an enable. None if no records / cannot tell."""
    mut_dir = vault / "runtime" / "lifecycle" / "run" / "startup-surface-mutations"
    if not mut_dir.exists():
        return None
    prefix = f"startup-surface-{rid}-"
    enable_ts: list[str] = []
    disable_ts: list[str] = []
    for path in mut_dir.glob(f"{prefix}*.json"):
        name = path.name
        match = _TS_RE.search(name)
        ts = match.group(0) if match else name
        if "-enable-" in name:
            enable_ts.append(ts)
        elif "-disable-" in name:
            disable_ts.append(ts)
    if not enable_ts and not disable_ts:
        return None
    latest_enable = max(enable_ts) if enable_ts else ""
    latest_disable = max(disable_ts) if disable_ts else ""
    return latest_enable > latest_disable


def _launch_state_present(vault: Path, rid: str) -> bool:
    return (vault / "runtime" / "lifecycle" / "run" / f"{rid}-coordination-watch.json").exists()


def _governance_ok(vault: Path) -> bool | None:
    try:
        from runtime.adapters.runtime_governance import validate_runtime_adapter_governance

        report = validate_runtime_adapter_governance(vault)
        return bool(report.get("ok"))
    except Exception:
        return None


def _hermes_gateway_ready(vault: Path) -> bool | None:
    try:
        from runtime.lifecycle.hermes_gateway_config import build_hermes_gateway_config_status

        status = build_hermes_gateway_config_status(vault_root=vault)
        return bool(status.get("gateway_ready"))
    except Exception:
        return None


def build_activation_plan(
    runtime_id: str,
    vault_root: str | Path | None = None,
    *,
    runner=None,
    probe_running: bool = False,
) -> dict[str, Any]:
    """Build the read-only, resumable 7-stage activation plan for ``runtime_id``."""
    rid = str(runtime_id or "").strip().lower()
    vault = _vault(vault_root)
    run = runner or _default_runner

    if rid not in DETECT_STRATEGY:
        return {
            "ok": True,
            "surface": "runtime_activation",
            "runtime_id": rid,
            "overall": "unsupported",
            "supported": False,
            "stages": [],
            "current_stage": None,
            "next_operator_action": f"No activation profile for runtime '{rid}'.",
            "authority": {"read_only": True, "host_mutation_performed": False, "writes_performed": False},
        }

    detection = detect_runtime(rid, vault, runner=runner, probe_running=probe_running)
    present = detection.get("status") == STATUS_PRESENT
    platform = detection.get("platform")
    companion = detection.get("companion") or {}
    stages: list[dict[str, Any]] = []

    # 1. Detect — informational, always satisfied (it ran)
    stages.append(_stage(
        1, "detect", "Detect", "satisfied",
        f"binary status={detection.get('status')} version={detection.get('version')}",
        detection_status=detection.get("status"), present=present,
    ))

    # 2. Preflight — host prerequisites for the runtime
    if present:
        stages.append(_stage(2, "preflight", "Preflight", "satisfied", "runtime already installed; host supports it"))
    elif platform == "windows":
        node_ok = bool(companion.get("present") and companion.get("version_ok"))
        if node_ok:
            stages.append(_stage(2, "preflight", "Preflight", "satisfied", "Node 24+ present"))
        else:
            stages.append(_stage(
                2, "preflight", "Preflight", "pending",
                f"Node 24+ required (found {companion.get('version') or 'none'})",
                next_command="Install Node 24 (e.g. winget install OpenJS.NodeJS.LTS)",
            ))
    else:  # wsl
        wsl = _wsl_available(run)
        if wsl is True:
            stages.append(_stage(2, "preflight", "Preflight", "satisfied", "WSL available"))
        elif wsl is False:
            stages.append(_stage(
                2, "preflight", "Preflight", "blocked", "WSL not available",
                next_command="wsl --install -d Ubuntu",
            ))
        else:
            stages.append(_stage(
                2, "preflight", "Preflight", "pending", "WSL availability unconfirmed",
                next_command="wsl -l -v",
            ))

    preflight_ok = stages[-1]["status"] == "satisfied"
    preflight_blocked = stages[-1]["status"] == "blocked"

    # 3. Install — emit-only install scripts; satisfied when the binary is present
    if present:
        stages.append(_stage(3, "install", "Install", "satisfied", "runtime binary present"))
    elif preflight_blocked:
        stages.append(_stage(3, "install", "Install", "blocked", "preflight must pass before install"))
    else:
        stages.append(_stage(
            3, "install", "Install", "pending",
            "runtime binary not found; emit reviewed install scripts (operator runs them)",
            next_command=f"chaseos runtime activate --runtime {rid} --confirm  (emits dist/installer/runtimes/{rid}/)",
        ))

    # 4. Configure (non-secret) — registered + lifecycle record present
    registered = _is_registered(vault, rid)
    lifecycle_ok = _lifecycle_loads(vault, rid)
    governance_ok = _governance_ok(vault)
    if registered and lifecycle_ok:
        stages.append(_stage(
            4, "configure", "Configure", "satisfied",
            "runtime registered + lifecycle record present",
            governance_ok=governance_ok,
        ))
    else:
        missing = []
        if not registered:
            missing.append("not registered")
        if not lifecycle_ok:
            missing.append("lifecycle record missing")
        stages.append(_stage(
            4, "configure", "Configure", "pending", "; ".join(missing),
            next_command=f"chaseos agent register {rid}",
            governance_ok=governance_ok,
        ))

    # 5. Secrets (reference-only)
    if rid == "hermes":
        gw = _hermes_gateway_ready(vault)
        if gw is True:
            stages.append(_stage(5, "secrets", "Secrets", "satisfied", "gateway config ready (redacted)"))
        else:
            stages.append(_stage(
                5, "secrets", "Secrets", "pending",
                "gateway allowlist/messaging config not ready",
                next_command="chaseos runtime hermes-gateway-config --action status   (set values in Hermes home .env)",
            ))
    else:
        stages.append(_stage(
            5, "secrets", "Secrets", "satisfied",
            "runtime owns its own credentials (provider-agnostic; ChaseOS stores nothing)",
        ))

    # 6. Register startup (host-mutating — handed off, never performed here)
    enabled = _startup_enabled(vault, rid)
    if enabled is True:
        stages.append(_stage(6, "register_startup", "Register startup", "satisfied", "startup surface enabled (approval-recorded)"))
    else:
        stages.append(_stage(
            6, "register_startup", "Register startup", "pending",
            "startup not registered" if enabled is None else "startup most recently disabled",
            next_command=f"chaseos runtime startup-surface-toggle --runtime {rid} --surface gateway --intent enable --confirm",
        ))

    # 7. Launch + verify
    running = bool(detection.get("running")) if probe_running else None
    launched = running if running is not None else _launch_state_present(vault, rid)
    if launched:
        stages.append(_stage(7, "launch_verify", "Launch + verify", "satisfied", "lane running / coordination-watch state present"))
    else:
        stages.append(_stage(
            7, "launch_verify", "Launch + verify", "pending",
            "lane not started / unverified",
            next_command=f"chaseos runtime daemon --runtime {rid} --daemon-interval 30   (then: chaseos runtime doctor --runtime {rid})",
        ))

    # overall + current stage
    current = next((s for s in stages if s["status"] in ("pending", "blocked")), None)
    if any(s["status"] == "blocked" for s in stages):
        overall = "blocked"
    elif all(s["status"] in ("satisfied", "skipped") for s in stages):
        overall = "activated"
    elif current and current["key"] == "install" and not present:
        overall = "in-progress"
    else:
        overall = "in-progress"

    return {
        "ok": True,
        "surface": "runtime_activation",
        "runtime_id": rid,
        "supported": True,
        "platform": platform,
        "overall": overall,
        "stages": stages,
        "current_stage": current["key"] if current else None,
        "next_operator_action": (current.get("next_command") if current else "none — activated") if current else "none — activated",
        "governance_ok": governance_ok,
        "detection": detection,
        "authority": {
            "read_only": True,
            "host_mutation_performed": False,
            "writes_performed": False,
            "provider_calls_performed": False,
            "secret_values_read": False,
        },
    }


def run_activation(
    runtime_id: str,
    vault_root: str | Path | None = None,
    *,
    confirm: bool = False,
    dry_run: bool = True,
    runner=None,
    install_facts: Optional[dict] = None,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Drive activation. Dry-run by default: returns the plan, performs nothing.

    With ``confirm=True`` and ``dry_run=False`` it performs the single safe,
    reversible action — emitting the Stage-3 install scripts to ``dist/installer/
    runtimes/<id>/`` when the binary is absent. All other stages remain hand-off
    (their exact commands are in the plan).
    """
    rid = str(runtime_id or "").strip().lower()
    vault = _vault(vault_root)
    plan = build_activation_plan(rid, vault, runner=runner)

    if dry_run or not confirm:
        plan["mode"] = "dry-run"
        plan["actions_performed"] = []
        return plan

    actions: list[dict[str, Any]] = []
    install_stage = next((s for s in plan["stages"] if s["key"] == "install"), None)
    if install_stage is not None and install_stage["status"] == "pending":
        from runtime.installer.runtime_install import (
            build_runtime_install_bundle,
            write_runtime_install_bundle,
        )

        bundle = build_runtime_install_bundle(rid, facts=install_facts)
        if bundle.get("status") == "ready":
            base = Path(out_dir) if out_dir else (vault / "dist" / "installer" / "runtimes" / rid)
            written = write_runtime_install_bundle(bundle, base)
            actions.append({
                "stage": "install",
                "action": "emitted_install_scripts",
                "out_dir": str(base),
                "paths": written,
            })
        else:
            actions.append({
                "stage": "install",
                "action": f"install_bundle_{bundle.get('status')}",
                "detail": bundle.get("detail") or "see preflight blockers",
            })

    plan["mode"] = "execute"
    plan["actions_performed"] = actions
    plan["authority"]["writes_performed"] = bool(actions)
    return plan
