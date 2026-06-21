from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.agent_bus import bus
from runtime.agent_bus.backend_loader import get_backend
from runtime.agent_bus.router import get_runtime_liveness, route_task_type


def _print_json(data: object) -> int:
    print(json.dumps(data, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    tasks = bus.list_tasks(ROOT)
    liveness = get_runtime_liveness(ROOT)
    heartbeat_rows = get_backend(ROOT).list_heartbeats()
    payload = {
        "runtime_bus": {
            "task_count": len(tasks),
            "open_task_count": len([t for t in tasks if t.get("status") == "open"]),
            "owned_task_count": len([t for t in tasks if t.get("owner")]),
            "heartbeat_row_count": len(heartbeat_rows),
            "runtimes": {
                name: {
                    "runtime_name": live.runtime_name,
                    "last_seen": live.last_seen,
                    "status": live.status,
                    "health": live.health,
                    "age_seconds": live.age_seconds,
                    "is_stale": live.is_stale,
                    "stale_threshold_seconds": live.stale_threshold_seconds,
                    "heartbeat_instances": [
                        row
                        for row in heartbeat_rows
                        if row.get("runtime") == name
                    ],
                }
                for name, live in liveness.items()
            },
        }
    }
    return _print_json(payload) if args.json else _print_json(payload)


def cmd_task_list(args: argparse.Namespace) -> int:
    payload = bus.list_tasks(ROOT, recipient=args.to, status=args.status, owner=args.owner)
    if args.limit is not None:
        payload = payload[: max(0, int(args.limit))]
    return _print_json(payload)


def cmd_task_create(args: argparse.Namespace) -> int:
    ingress_context = {
        key: value
        for key, value in {
            "source_platform": args.source_platform,
            "source_channel_id": args.source_channel_id,
            "source_thread_id": args.source_thread_id,
            "source_channel_class": args.source_channel_class,
            "conversation_key": args.conversation_key,
            "origin_message_id": args.origin_message_id,
            "control_plane_route": args.control_plane_route,
        }.items()
        if value is not None
    }
    execution_constraints = {
        key: value
        for key, value in {
            "allow_shell_commands": False if getattr(args, "no_shell_commands", False) else None,
            "allow_live_subprocess": False if getattr(args, "no_live_subprocess", False) else None,
            "write_policy": getattr(args, "write_policy", None),
            "allowed_write_paths": getattr(args, "allowed_write_path", None),
        }.items()
        if value is not None
    }
    payload = bus.create_task(
        ROOT,
        sender=args.sender,
        recipient=args.to,
        intent=args.intent,
        priority=args.priority,
        request=args.request,
        expected_output=args.expected_output,
        notes=args.notes,
        ingress_context=ingress_context or None,
        work_fingerprint=args.work_fingerprint,
        execution_constraints=execution_constraints or None,
    )
    return _print_json(payload)



def cmd_ingress_discord(args: argparse.Namespace) -> int:
    payload = bus.translate_discord_control_plane_request(
        ROOT,
        recipient=args.to,
        intent=args.intent,
        priority=args.priority,
        request=args.request,
        expected_output=args.expected_output,
        notes=args.notes,
        source_channel_id=args.source_channel_id,
        source_thread_id=args.source_thread_id,
        source_channel_class=args.source_channel_class,
        origin_message_id=args.origin_message_id,
        control_plane_route=args.control_plane_route,
        work_fingerprint=args.work_fingerprint,
        coordination_sensitive=args.coordination_sensitive,
    )
    return _print_json(payload)



def cmd_task_claim(args: argparse.Namespace) -> int:
    payload = bus.claim_task(
        ROOT,
        task_id=args.task_id,
        runtime=args.runtime,
        runtime_instance_id=getattr(args, "runtime_instance_id", None),
    )
    return _print_json(payload)



def cmd_task_update(args: argparse.Namespace) -> int:
    payload = bus.update_task_status(
        ROOT,
        task_id=args.task_id,
        runtime=args.runtime,
        status=args.status,
        event_type=args.event_type,
        message=args.message,
        artifacts=args.artifact or None,
    )
    return _print_json(payload)


def cmd_task_cleanup(args: argparse.Namespace) -> int:
    payload = bus.cleanup_tasks(
        ROOT,
        runtime=args.runtime,
        recipient=args.to,
        sender=args.sender,
        owner=args.owner,
        status=args.status,
        request_exact=args.request_exact,
        request_contains=args.request_contains,
        updated_before=args.updated_before,
        work_fingerprint=args.work_fingerprint,
        conversation_key=args.conversation_key,
        origin_message_id=args.origin_message_id,
        limit=args.limit,
        reason=args.reason,
        apply=args.apply,
    )
    return _print_json(payload)


def cmd_task_reclaim(args: argparse.Namespace) -> int:
    payload = bus.reclaim_task(
        ROOT,
        task_id=args.task_id,
        new_runtime=args.runtime,
        reason=args.reason or "Reclaimed through promoted ChaseOS shell.",
    )
    return _print_json(payload)


def cmd_heartbeat(args: argparse.Namespace) -> int:
    payload = bus.upsert_heartbeat(
        ROOT,
        runtime=args.runtime,
        status=args.status,
        health=args.health,
        current_task_id=args.current_task_id,
        summary=args.summary,
        runtime_instance_id=getattr(args, "runtime_instance_id", None),
        heartbeat_scope=getattr(args, "heartbeat_scope", "runtime"),
        control_surface=getattr(args, "control_surface", None),
        control_surface_key=getattr(args, "control_surface_key", None),
    )
    return _print_json(payload)


def cmd_expire_stale(args: argparse.Namespace) -> int:
    payload = bus.mark_stale_tasks(ROOT, max_age_seconds=args.max_age_seconds)
    return _print_json(payload)


def cmd_watch(args: argparse.Namespace) -> int:
    if getattr(args, "interval", None) is not None:
        bus.run_watch_loop(
            ROOT,
            runtime=args.runtime,
            interval_seconds=args.interval,
            claim_next=args.claim_next,
            stale_after_seconds=args.stale_after_seconds,
            runtime_instance_id=getattr(args, "runtime_instance_id", None),
            control_surface=getattr(args, "control_surface", None),
            control_surface_key=getattr(args, "control_surface_key", None),
        )
        return 0

    payload = bus.watch_once(
        ROOT,
        runtime=args.runtime,
        claim_next=args.claim_next,
        stale_after_seconds=args.stale_after_seconds,
        runtime_instance_id=getattr(args, "runtime_instance_id", None),
        control_surface=getattr(args, "control_surface", None),
        control_surface_key=getattr(args, "control_surface_key", None),
    )
    return _print_json(payload)


def cmd_route(args: argparse.Namespace) -> int:
    result = route_task_type(args.task_type, ROOT)
    payload = {
        "task_type": result.task_type,
        "eligible_runtimes": result.eligible_runtimes,
        "live_runtimes": result.live_runtimes,
        "stale_runtimes": result.stale_runtimes,
        "at_capacity_runtimes": result.at_capacity_runtimes,
        "available_runtimes": result.available_runtimes,
        "all_registered": result.all_registered,
        "recommended": result.recommended,
        "reason": result.reason,
    }
    return _print_json(payload)


def cmd_runtimes(args: argparse.Namespace) -> int:
    from runtime.agent_bus.capabilities import load_all_capabilities

    all_caps = load_all_capabilities(ROOT)
    liveness = get_runtime_liveness(ROOT)
    heartbeat_rows = get_backend(ROOT).list_heartbeats()
    payload = []
    for runtime_name, caps in sorted(all_caps.items()):
        live = liveness.get(caps.bus_name)
        runtime_heartbeats = [row for row in heartbeat_rows if row.get("runtime") == caps.bus_name]
        payload.append(
            {
                "runtime_name": runtime_name,
                "bus_name": caps.bus_name,
                "display_name": caps.display_name,
                "description": caps.description,
                "handles": [
                    {
                        "task_type": item.task_type,
                        "priority": item.priority,
                        "notes": item.notes,
                    }
                    for item in caps.handles
                ],
                "max_concurrent_tasks": caps.max_concurrent_tasks,
                "heartbeat_stale_seconds": caps.heartbeat_stale_seconds,
                "priority_ceiling": caps.priority_ceiling,
                "liveness": None
                if live is None
                else {
                    "last_seen": live.last_seen,
                    "status": live.status,
                    "health": live.health,
                    "age_seconds": live.age_seconds,
                    "is_stale": live.is_stale,
                },
                "heartbeat_instances": runtime_heartbeats,
                "heartbeat_instance_count": len(runtime_heartbeats),
            }
        )
    return _print_json(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ChaseOS agent-bus CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(func=cmd_status)

    ingress_parser = subparsers.add_parser("ingress")
    ingress_sub = ingress_parser.add_subparsers(dest="ingress_command", required=True)

    ingress_discord = ingress_sub.add_parser("discord")
    ingress_discord.add_argument("--to", required=True)
    ingress_discord.add_argument("--intent", default="TASK")
    ingress_discord.add_argument("--priority", default="normal")
    ingress_discord.add_argument("--request", required=True)
    ingress_discord.add_argument("--expected-output", required=True)
    ingress_discord.add_argument("--notes", default=None)
    ingress_discord.add_argument("--source-channel-id", required=True)
    ingress_discord.add_argument("--source-thread-id", default=None)
    ingress_discord.add_argument("--source-channel-class", default=None)
    ingress_discord.add_argument("--origin-message-id", default=None)
    ingress_discord.add_argument("--control-plane-route", default=None)
    ingress_discord.add_argument("--work-fingerprint", default=None)
    ingress_discord.add_argument("--coordination-sensitive", action="store_true")
    ingress_discord.add_argument("--json", action="store_true")
    ingress_discord.set_defaults(func=cmd_ingress_discord)

    task_parser = subparsers.add_parser("task")
    task_sub = task_parser.add_subparsers(dest="task_command", required=True)

    task_list = task_sub.add_parser("list")
    task_list.add_argument("--to", default=None)
    task_list.add_argument("--status", default=None)
    task_list.add_argument("--owner", default=None)
    task_list.add_argument("--limit", type=int, default=None)
    task_list.set_defaults(func=cmd_task_list)

    task_create = task_sub.add_parser("create")
    task_create.add_argument("--sender", required=True)
    task_create.add_argument("--to", required=True)
    task_create.add_argument("--intent", default="TASK")
    task_create.add_argument("--priority", default="normal")
    task_create.add_argument("--request", required=True)
    task_create.add_argument("--expected-output", required=True)
    task_create.add_argument("--notes", default=None)
    task_create.add_argument("--source-platform", default=None)
    task_create.add_argument("--source-channel-id", default=None)
    task_create.add_argument("--source-thread-id", default=None)
    task_create.add_argument("--source-channel-class", default=None)
    task_create.add_argument("--conversation-key", default=None)
    task_create.add_argument("--origin-message-id", default=None)
    task_create.add_argument("--control-plane-route", default=None)
    task_create.add_argument("--work-fingerprint", default=None)
    task_create.add_argument("--no-shell-commands", action="store_true")
    task_create.add_argument("--no-live-subprocess", action="store_true")
    task_create.add_argument("--write-policy", choices=["adapter-default", "declared-paths", "none"], default=None)
    task_create.add_argument("--allowed-write-path", action="append", default=None)
    task_create.set_defaults(func=cmd_task_create)

    task_claim = task_sub.add_parser("claim")
    task_claim.add_argument("task_id")
    task_claim.add_argument("--runtime", required=True)
    task_claim.add_argument("--runtime-instance-id", default=None)
    task_claim.set_defaults(func=cmd_task_claim)

    task_update = task_sub.add_parser("update")
    task_update.add_argument("task_id")
    task_update.add_argument("--runtime", required=True)
    task_update.add_argument("--status", required=True)
    task_update.add_argument("--event-type", required=True)
    task_update.add_argument("--message", required=True)
    task_update.add_argument("--artifact", action="append")
    task_update.set_defaults(func=cmd_task_update)

    task_cleanup = task_sub.add_parser("cleanup")
    task_cleanup.add_argument("--runtime", required=True)
    task_cleanup.add_argument("--to", default=None)
    task_cleanup.add_argument("--sender", default=None)
    task_cleanup.add_argument("--owner", default=None)
    task_cleanup.add_argument("--status", default=None)
    task_cleanup.add_argument("--request-exact", default=None)
    task_cleanup.add_argument("--request-contains", default=None)
    task_cleanup.add_argument("--updated-before", default=None)
    task_cleanup.add_argument("--work-fingerprint", default=None)
    task_cleanup.add_argument("--conversation-key", default=None)
    task_cleanup.add_argument("--origin-message-id", default=None)
    task_cleanup.add_argument("--limit", type=int, default=None)
    task_cleanup.add_argument("--reason", default="Queue hygiene cleanup")
    task_cleanup.add_argument("--apply", action="store_true", help="Cancel the selected tasks; requires explicit --status open")
    task_cleanup.set_defaults(func=cmd_task_cleanup)

    task_reclaim = task_sub.add_parser("reclaim")
    task_reclaim.add_argument("task_id")
    task_reclaim.add_argument("--runtime", required=True)
    task_reclaim.add_argument("--reason", default=None)
    task_reclaim.set_defaults(func=cmd_task_reclaim)

    heartbeat = subparsers.add_parser("heartbeat")
    heartbeat.add_argument("--runtime", required=True)
    heartbeat.add_argument("--status", required=True)
    heartbeat.add_argument("--health", required=True)
    heartbeat.add_argument("--current-task-id", default=None)
    heartbeat.add_argument("--summary", default=None)
    heartbeat.add_argument("--runtime-instance-id", default=None)
    heartbeat.add_argument("--heartbeat-scope", choices=["runtime", "instance"], default="runtime")
    heartbeat.add_argument("--control-surface", default=None)
    heartbeat.add_argument("--control-surface-key", default=None)
    heartbeat.set_defaults(func=cmd_heartbeat)

    expire = subparsers.add_parser("expire-stale")
    expire.add_argument("--max-age-seconds", type=int, required=True)
    expire.set_defaults(func=cmd_expire_stale)

    watch = subparsers.add_parser("watch")
    watch.add_argument("--runtime", required=True)
    watch.add_argument("--claim-next", action="store_true")
    watch.add_argument("--stale-after-seconds", type=int, default=None)
    watch.add_argument("--runtime-instance-id", default=None)
    watch.add_argument("--control-surface", default=None)
    watch.add_argument("--control-surface-key", default=None)
    watch_mode = watch.add_mutually_exclusive_group()
    watch_mode.add_argument("--once", action="store_true")
    watch_mode.add_argument("--interval", type=int, default=None)
    watch.set_defaults(func=cmd_watch)

    route = subparsers.add_parser("route")
    route.add_argument("task_type")
    route.set_defaults(func=cmd_route)

    runtimes = subparsers.add_parser("runtimes")
    runtimes.set_defaults(func=cmd_runtimes)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
