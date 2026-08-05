"""
runtime/cli/core_main.py — the Lean ChaseOS Core CLI (MIT).

A focused operator entrypoint for the open MIT Core. It wires **only Core command
families** and calls the Core building blocks (`runtime.commerce`, `runtime.aor`,
`runtime.schedules`, `runtime.capture`, …) **directly** — it never imports the full
operator god-module (`runtime.cli.main`), the proprietary Studio/Control-Kernel surfaces,
or any instance-specific command families. That keeps it import-clean for Core
(ADR-0015 CLI split): the full `main.py` remains the proprietary operator CLI.

Console entrypoint in the Core export: ``chaseos = "runtime.cli.core_main:main"``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

CORE_CLI_VERSION = "0.1.0-core"


# ── shared helpers ───────────────────────────────────────────────────────────────
def _resolve_vault(explicit: Optional[str]) -> Path:
    root = explicit or os.environ.get("CHASEOS_VAULT_ROOT") or os.getcwd()
    return Path(root)


def _commerce_db(vault_root: Path) -> Path:
    return vault_root / ".chaseos" / "commerce.db"


def _emit(args: argparse.Namespace, action: str, payload: Any) -> int:
    """Human or JSON output. JSON uses the canonical envelope shape."""
    if getattr(args, "output_json", False):
        print(json.dumps({"ok": True, "action": action, "result": payload, "errors": [], "warnings": []}, indent=2, default=str))
    return 0


# ── version / doctor ─────────────────────────────────────────────────────────────
def cmd_version(args: argparse.Namespace) -> int:
    if getattr(args, "output_json", False):
        return _emit(args, "version", {"core_cli_version": CORE_CLI_VERSION})
    print(f"ChaseOS Core CLI {CORE_CLI_VERSION}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Lightweight Core health check: confirm the Core building blocks import + vault is present."""
    vault = _resolve_vault(getattr(args, "vault_root", None))
    checks: list[dict[str, Any]] = []
    for mod in ("runtime.aor.engine", "runtime.commerce.catalog", "runtime.capture.capture",
                "runtime.schedules.loader", "runtime.gate_interface"):
        try:
            __import__(mod)
            checks.append({"check": mod, "ok": True})
        except Exception as exc:  # noqa: BLE001
            checks.append({"check": mod, "ok": False, "error": str(exc)})
    # Report which marker satisfied the check, not just a boolean: `.chaseos` can exist in
    # a home directory that is not a vault, and a bare `ok` there reads as false confidence.
    markers = [name for name in ("00_HOME", ".chaseos") if (vault / name).exists()]
    vault_ok = bool(markers)
    vault_check: dict[str, Any] = {"check": "vault_root", "ok": vault_ok, "path": str(vault)}
    if vault_ok:
        vault_check["matched"] = markers
    else:
        vault_check["error"] = (
            f"no vault markers (00_HOME/ or .chaseos/) found in {vault}. "
            "Pass --vault-root <path>, or run from your vault directory."
        )
    checks.append(vault_check)
    ok = all(c["ok"] for c in checks)
    if getattr(args, "output_json", False):
        print(json.dumps({"ok": ok, "action": "doctor", "result": {"checks": checks}, "errors": [], "warnings": []}, indent=2))
        return 0 if ok else 1
    print(f"ChaseOS Core doctor — vault={vault}")
    for c in checks:
        print(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['check']}" + (f"  {c.get('error','')}" if not c["ok"] else ""))
    return 0 if ok else 1


# ── commerce (read-only) ─────────────────────────────────────────────────────────
def cmd_commerce_catalog(args: argparse.Namespace) -> int:
    from runtime.commerce import catalog as cat
    c = cat.load_catalog()
    plans = cat.list_plans(c)
    if getattr(args, "output_json", False):
        return _emit(args, "commerce.catalog", {"catalog_version": c.get("catalog_version"), "plans": plans})
    print(f"ChaseOS commerce catalogue (v{c.get('catalog_version')}) — {len(plans)} plans")
    for p in plans:
        print(f"  {p.get('plan_id') or p.get('id') or '?'}: {p.get('name', '')}")
    return 0


def cmd_commerce_entitlement(args: argparse.Namespace) -> int:
    from runtime.commerce import entitlements as ent
    res = ent.check(args.plan_id, args.feature_id)
    if getattr(args, "output_json", False):
        return _emit(args, "commerce.entitlement", res)
    print(f"entitlement {args.plan_id}/{args.feature_id}: allowed={res.get('allowed')} "
          f"reason={res.get('reason_code')} upgrade={res.get('upgrade_target')}")
    return 0


def cmd_commerce_flags(args: argparse.Namespace) -> int:
    from runtime.commerce import flags as fl
    data = fl.load_flags()
    flags = data.get("flags", data)
    if getattr(args, "output_json", False):
        return _emit(args, "commerce.flags", flags)
    print("ChaseOS commerce feature flags:")
    items = flags.items() if isinstance(flags, dict) else []
    for k, v in items:
        print(f"  {k}: {v}")
    return 0


def cmd_commerce_admin(args: argparse.Namespace) -> int:
    from runtime.commerce import admin, store
    db = _commerce_db(_resolve_vault(getattr(args, "vault_root", None)))
    store.init_store(db)
    ov = admin.overview(db)
    if getattr(args, "output_json", False):
        return _emit(args, "commerce.admin", ov)
    print(f"ChaseOS commerce admin (read-only)  schema_version={ov.get('schema_version')}")
    print(f"  accounts={ov.get('accounts_total')} by_plan={ov.get('accounts_by_plan')}  billing={ov.get('billing_status')}")
    return 0


def cmd_commerce_ledger(args: argparse.Namespace) -> int:
    from runtime.commerce import ledger, store
    db = _commerce_db(_resolve_vault(getattr(args, "vault_root", None)))
    store.init_store(db)
    bal = ledger.balance(db, args.account, (getattr(args, "currency", None) or "GBP").upper())
    if getattr(args, "output_json", False):
        return _emit(args, "commerce.ledger", bal)
    print(f"ledger {args.account} {bal['currency']}: available={bal['available_minor']} "
          f"reserved={bal['reserved_minor']} total={bal['total_minor']} (minor units)")
    return 0


# ── run (AOR) / schedule ─────────────────────────────────────────────────────────
def cmd_run(args: argparse.Namespace) -> int:
    from runtime.aor.engine import run_workflow
    res = run_workflow(args.workflow_id, vault_root=_resolve_vault(getattr(args, "vault_root", None)),
                       dry_run=getattr(args, "dry_run", False))
    try:
        import dataclasses
        payload = dataclasses.asdict(res) if dataclasses.is_dataclass(res) else (res if isinstance(res, dict) else vars(res))
    except Exception:  # noqa: BLE001
        payload = {"result": str(res)}
    if getattr(args, "output_json", False):
        print(json.dumps({"ok": True, "action": "run", "result": payload, "errors": [], "warnings": []}, indent=2, default=str))
        return 0
    print(f"run {args.workflow_id}: status={payload.get('status', payload.get('outcome', '?'))}")
    return 0


def cmd_decision_route_inspect(args: argparse.Namespace) -> int:
    """Inspect a decision contract without dispatching work or consuming approval."""
    from runtime.decision_router import (
        DecisionContractError,
        inspect_decision_contract,
        load_decision_contract,
    )

    try:
        contract = load_decision_contract(args.path)
        result = inspect_decision_contract(contract)
    except DecisionContractError as exc:
        if getattr(args, "output_json", False):
            print(json.dumps({
                "ok": False,
                "action": "decision-route.inspect",
                "result": None,
                "errors": [str(exc)],
                "warnings": [],
            }, indent=2))
        else:
            print(f"decision route: invalid contract — {exc}", file=sys.stderr)
        return 2

    if getattr(args, "output_json", False):
        print(json.dumps({
            "ok": result["ok"],
            "action": "decision-route.inspect",
            "result": result,
            "errors": result["violations"],
            "warnings": [],
        }, indent=2, default=str))
    else:
        print(
            f"decision route {result['decision_id']}: status={result['status']} "
            f"modalities={','.join(result['required_modalities'])} "
            f"approval_required={result['approval_plan']['required']}"
        )
        for violation in result["violations"]:
            print(f"  BLOCK {violation['code']}: {violation['message']}")
    return 0 if result["ok"] else 2


def cmd_schedule_list(args: argparse.Namespace) -> int:
    from runtime.schedules.loader import list_schedules
    items = list_schedules(_resolve_vault(getattr(args, "vault_root", None)))
    rows = [{"id": getattr(s, "schedule_id", None), "workflow": getattr(s, "workflow_id", None),
             "enabled": getattr(s, "enabled", None), "cron": getattr(s, "cron", None)} for s in items]
    if getattr(args, "output_json", False):
        return _emit(args, "schedule.list", rows)
    print(f"ChaseOS schedules ({len(rows)}):")
    for r in rows:
        print(f"  {r['id']}  workflow={r['workflow']}  enabled={r['enabled']}  cron={r['cron']}")
    return 0


# ── capture ──────────────────────────────────────────────────────────────────────
def _do_capture(args: argparse.Namespace, file_path: Optional[str]) -> int:
    from runtime.capture.connectors.cli_connector import capture_from_cli
    from runtime.capture.capture import capture_content
    packet = capture_from_cli(
        input_class=getattr(args, "input_class", "source"),
        source_platform=getattr(args, "source", "cli"),
        title=args.title or (Path(file_path).name if file_path else "stdin-capture"),
        file_path=file_path,
        domain_hint=getattr(args, "domain", None),
        project_hint=getattr(args, "project", None),
        topic_hint=getattr(args, "topic", None),
        origin_kind=getattr(args, "origin_kind", None),
    )
    res = capture_content(packet, _resolve_vault(getattr(args, "vault_root", None)))
    if getattr(args, "output_json", False):
        return _emit(args, "capture", res)
    if res.get("is_duplicate"):
        print("capture: duplicate content (SHA already known) — not written")
    else:
        print(f"captured: {res.get('content_path') or res.get('relative_path') or res.get('capture_id') or 'ok'}")
    return 0


def cmd_capture_file(args: argparse.Namespace) -> int:
    return _do_capture(args, args.path)


def cmd_capture_stdin(args: argparse.Namespace) -> int:
    return _do_capture(args, None)


def cmd_capture_image_text(args: argparse.Namespace) -> int:
    from runtime.capture.visual_capture.image_text import capture_local_image_text

    res = capture_local_image_text(
        args.path,
        vault_root=_resolve_vault(getattr(args, "vault_root", None)),
        title=args.title or Path(args.path).stem,
        input_class=getattr(args, "input_class", "source"),
        source_platform=getattr(args, "source", "local-image-text"),
        domain_hint=getattr(args, "domain", None),
        project_hint=getattr(args, "project", None),
        topic_hint=getattr(args, "topic", None),
        origin_kind=getattr(args, "origin_kind", None),
    )
    if getattr(args, "output_json", False):
        return _emit(args, "capture.image_text", res)
    if res.get("is_duplicate"):
        print("capture image-text: duplicate extracted text (SHA already known) — not written")
    else:
        print(f"captured image text: {res.get('content_path') or res.get('capture_id') or 'ok'}")
    return 0


def cmd_capture_image_text_status(args: argparse.Namespace) -> int:
    from runtime.capture.visual_capture.image_text import build_local_image_text_status

    res = build_local_image_text_status()
    if getattr(args, "output_json", False):
        return _emit(args, "capture.image_text.status", res)
    engine = res.get("engine", {})
    print(
        "capture image-text status: "
        f"engine={engine.get('engine_id')} available={engine.get('available')} "
        "cloud_ocr_allowed=False provider_call_allowed=False"
    )
    return 0


# ── connections ──────────────────────────────────────────────────────────────────
def cmd_connections_providers(args: argparse.Namespace) -> int:
    from runtime.connections import list_provider_manifests

    providers = [manifest.to_dict() for manifest in list_provider_manifests()]
    if getattr(args, "output_json", False):
        return _emit(args, "connections.providers", providers)
    print(f"ChaseOS connection providers ({len(providers)}):")
    for provider in providers:
        caps = provider.get("capabilities", [])
        print(
            f"  {provider.get('id')}: {provider.get('name')} "
            f"status={provider.get('status')} default={provider.get('safety', {}).get('default_profile', 'read_only')} "
            f"capabilities={len(caps)}"
        )
    return 0


def cmd_connections_list(args: argparse.Namespace) -> int:
    from runtime.connections import registry_overview

    vault = _resolve_vault(getattr(args, "vault_root", None))
    overview = registry_overview(vault)
    if getattr(args, "output_json", False):
        return _emit(args, "connections.list", overview)
    print(f"ChaseOS Connections registry: db={overview['db_path']} exists={overview['db_exists']}")
    print(f"  providers_available={len(overview['available_providers'])} connections={len(overview['connections'])}")
    for conn in overview["connections"]:
        print(
            f"  {conn['id']} provider={conn['provider']} status={conn['status']} "
            f"profile={conn['permission_profile']} updated={conn['updated_at']}"
        )
    return 0


def cmd_connections_init(args: argparse.Namespace) -> int:
    from runtime.connections.store import db_path_for_vault, init_store

    vault = _resolve_vault(getattr(args, "vault_root", None))
    result = init_store(db_path_for_vault(vault))
    if getattr(args, "output_json", False):
        return _emit(args, "connections.init", result)
    print(
        "ChaseOS Connections store initialized: "
        f"db={result['db_path']} schema={result['schema_version']} tables={result['table_count']}"
    )
    return 0


def cmd_connections_seed(args: argparse.Namespace) -> int:
    from runtime.connections import load_manifest
    from runtime.connections.store import db_path_for_vault, init_store, seed_manifest_connection

    vault = _resolve_vault(getattr(args, "vault_root", None))
    db_path = db_path_for_vault(vault)
    init_store(db_path)
    manifest = load_manifest(args.provider)
    connection_id = seed_manifest_connection(
        db_path,
        manifest,
        owner_user_id=getattr(args, "owner_user_id", None) or "local_operator",
        status="disconnected",
    )
    payload = {"connection_id": connection_id, "provider": manifest.id, "db_path": str(db_path)}
    if getattr(args, "output_json", False):
        return _emit(args, "connections.seed", payload)
    print(f"seeded disconnected {manifest.id} connection: {connection_id}")
    return 0


# ── parser ───────────────────────────────────────────────────────────────────────
def _add_capture_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--class", dest="input_class", default="source", help="Input class (default: source)")
    sp.add_argument("--source", default="cli", help="Short source platform id (default: cli)")
    sp.add_argument("--title", default=None)
    sp.add_argument("--domain", default=None)
    sp.add_argument("--project", default=None)
    sp.add_argument("--topic", default=None)
    sp.add_argument("--origin-kind", dest="origin_kind", default=None)
    sp.add_argument("--vault-root", default=None, metavar="PATH")
    sp.add_argument("--json", action="store_true", dest="output_json")


# ── parser ───────────────────────────────────────────────────────────────────────
def build_core_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chaseos", description="ChaseOS Core CLI (MIT)")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    sp = sub.add_parser("version", help="Show the Core CLI version")
    sp.add_argument("--json", action="store_true", dest="output_json")
    sp.set_defaults(func=cmd_version)

    sp = sub.add_parser("doctor", help="Core health check")
    sp.add_argument("--vault-root", default=None, metavar="PATH")
    sp.add_argument("--json", action="store_true", dest="output_json")
    sp.set_defaults(func=cmd_doctor)

    com = sub.add_parser("commerce", help="Commercial foundation (read-only)")
    com_sub = com.add_subparsers(dest="commerce_mode", metavar="MODE")
    com_sub.required = True
    s = com_sub.add_parser("catalog", help="List plans/prices/features")
    s.add_argument("--json", action="store_true", dest="output_json"); s.set_defaults(func=cmd_commerce_catalog)
    s = com_sub.add_parser("entitlement", help="Resolve an entitlement for a plan + feature")
    s.add_argument("plan_id", metavar="PLAN_ID"); s.add_argument("feature_id", metavar="FEATURE_ID")
    s.add_argument("--json", action="store_true", dest="output_json"); s.set_defaults(func=cmd_commerce_entitlement)
    s = com_sub.add_parser("flags", help="List feature flags")
    s.add_argument("--json", action="store_true", dest="output_json"); s.set_defaults(func=cmd_commerce_flags)
    s = com_sub.add_parser("admin", help="Read-only commerce admin overview")
    s.add_argument("--vault-root", default=None, metavar="PATH")
    s.add_argument("--json", action="store_true", dest="output_json"); s.set_defaults(func=cmd_commerce_admin)
    s = com_sub.add_parser("ledger", help="Read-only ledger balance")
    s.add_argument("account", metavar="ACCOUNT"); s.add_argument("--currency", default="GBP", metavar="CURRENCY")
    s.add_argument("--vault-root", default=None, metavar="PATH")
    s.add_argument("--json", action="store_true", dest="output_json"); s.set_defaults(func=cmd_commerce_ledger)

    sp = sub.add_parser("run", help="Run a bounded AOR workflow")
    sp.add_argument("workflow_id", metavar="WORKFLOW_ID")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--vault-root", default=None, metavar="PATH")
    sp.add_argument("--json", action="store_true", dest="output_json")
    sp.set_defaults(func=cmd_run)

    decision = sub.add_parser(
        "decision-route",
        help="Inspect decision modality and approval requirements (read-only)",
    )
    decision_sub = decision.add_subparsers(dest="decision_route_mode", metavar="MODE")
    decision_sub.required = True
    s = decision_sub.add_parser("inspect", help="Validate and inspect a JSON decision contract")
    s.add_argument("path", metavar="CONTRACT_JSON")
    s.add_argument("--json", action="store_true", dest="output_json")
    s.set_defaults(func=cmd_decision_route_inspect)

    cap = sub.add_parser("capture", help="Capture content into quarantine intake")
    cap_sub = cap.add_subparsers(dest="capture_mode", metavar="MODE")
    cap_sub.required = True
    s = cap_sub.add_parser("file", help="Capture a file")
    s.add_argument("path", metavar="PATH"); _add_capture_args(s); s.set_defaults(func=cmd_capture_file)
    s = cap_sub.add_parser("stdin", help="Capture from stdin")
    _add_capture_args(s); s.set_defaults(func=cmd_capture_stdin)
    img = cap_sub.add_parser("image-text", help="Extract text from an explicit local PNG image and capture it")
    img.add_argument("path", metavar="PATH")
    _add_capture_args(img)
    img.set_defaults(func=cmd_capture_image_text)
    s = cap_sub.add_parser("image-text-status", help="Show local image text engine posture")
    s.add_argument("--json", action="store_true", dest="output_json")
    s.set_defaults(func=cmd_capture_image_text_status)

    sch = sub.add_parser("schedule", help="Native schedule intents")
    sch_sub = sch.add_subparsers(dest="schedule_mode", metavar="MODE")
    sch_sub.required = True
    s = sch_sub.add_parser("list", help="List schedule intents")
    s.add_argument("--vault-root", default=None, metavar="PATH")
    s.add_argument("--json", action="store_true", dest="output_json"); s.set_defaults(func=cmd_schedule_list)

    conn = sub.add_parser("connections", help="Connection registry and provider manifests")
    conn_sub = conn.add_subparsers(dest="connections_mode", metavar="MODE")
    conn_sub.required = True
    s = conn_sub.add_parser("providers", help="List bundled provider manifests")
    s.add_argument("--json", action="store_true", dest="output_json"); s.set_defaults(func=cmd_connections_providers)
    s = conn_sub.add_parser("list", help="List local connection records and available providers")
    s.add_argument("--vault-root", default=None, metavar="PATH")
    s.add_argument("--json", action="store_true", dest="output_json"); s.set_defaults(func=cmd_connections_list)
    s = conn_sub.add_parser("init", help="Create the local-first Connections SQLite registry")
    s.add_argument("--vault-root", default=None, metavar="PATH")
    s.add_argument("--json", action="store_true", dest="output_json"); s.set_defaults(func=cmd_connections_init)
    s = conn_sub.add_parser("seed", help="Seed a disconnected placeholder connection from a provider manifest")
    s.add_argument("provider", metavar="PROVIDER")
    s.add_argument("--owner-user-id", default="local_operator")
    s.add_argument("--vault-root", default=None, metavar="PATH")
    s.add_argument("--json", action="store_true", dest="output_json"); s.set_defaults(func=cmd_connections_seed)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_core_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
