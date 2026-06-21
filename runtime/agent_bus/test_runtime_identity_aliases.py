from __future__ import annotations

import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.agent_bus.bus import (
    claim_task,
    create_task,
    db_path,
    init_db,
    list_heartbeats,
    list_tasks,
    update_task_status,
    upsert_heartbeat,
)
from runtime.agent_bus.capabilities import CapabilityError, resolve_runtime_identity


def _make_alias_vault() -> tuple[Path, Path]:
    root = Path(".codex_tmp_test") / f"agent-bus-runtime-aliases-{uuid.uuid4().hex}"
    vault = root / "vault"
    (vault / "runtime" / "agent_bus").mkdir(parents=True, exist_ok=True)
    for runtime_name, body in {
        "hermes": "bus_name: Hermes\nheartbeat_stale_seconds: 900\nhandles:\n  - task_type: review\n",
        "openclaw": "bus_name: OpenClaw\nheartbeat_stale_seconds: 900\nhandles:\n  - task_type: review\n",
        "archon": "bus_name: Archon\nheartbeat_stale_seconds: 900\nhandles:\n  - task_type: implementation\n  - task_type: code-review\n  - task_type: architecture-review\n",
        "codex": """
bus_name: Codex
personal_runtime_name: Axiom-Codex
retained_runtime_name: Axiom-Codex
legacy_personal_runtime_names:
  - Codex-ChaseOS-Worker
heartbeat_stale_seconds: 900
handles:
  - task_type: code.patch
""".lstrip(),
    }.items():
        runtime_dir = vault / "runtime" / runtime_name
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "capabilities.yaml").write_text(body, encoding="utf-8")
    init_db(vault)
    return root, vault


def test_resolve_runtime_identity_maps_codex_alias_to_canonical_bus_name():
    root, vault = _make_alias_vault()
    try:
        identity = resolve_runtime_identity(vault, "Axiom-Codex")

        assert identity.bus_name == "Codex"
        assert identity.runtime_name == "codex"
        assert identity.matched_as in {"personal_runtime_name", "retained_runtime_name"}
        assert identity.runtime_instance_id_hint == "Axiom-Codex"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_registered_runtime_identity_is_independent_from_heartbeat_liveness():
    root, vault = _make_alias_vault()
    try:
        openclaw = resolve_runtime_identity(vault, "OpenClaw")
        archon = resolve_runtime_identity(vault, "Archon")

        assert openclaw.bus_name == "OpenClaw"
        assert archon.bus_name == "Archon"

        # No heartbeat has been written for either runtime, but both remain valid
        # bus identities and can receive handoff packets. Reachability is a router
        # concern, not an identity-registration concern.
        openclaw_task = create_task(
            vault,
            sender="Hermes",
            recipient="OpenClaw",
            intent="REVIEW",
            request="Review the handoff.",
            expected_output="Return review notes.",
        )
        archon_task = create_task(
            vault,
            sender="Hermes",
            recipient="Archon",
            intent="REVIEW",
            request="Review the implementation.",
            expected_output="Return engineering notes.",
        )

        assert openclaw_task["created"] is True
        assert archon_task["created"] is True
        assert {task["recipient"] for task in list_tasks(vault)} >= {"OpenClaw", "Archon"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_resolve_runtime_identity_rejects_unknown_alias():
    root, vault = _make_alias_vault()
    try:
        try:
            resolve_runtime_identity(vault, "NotARealRuntime")
        except ValueError as exc:
            assert "Unknown runtime identity" in str(exc)
            assert "Codex" in str(exc)
        else:
            raise AssertionError("unknown runtime alias should fail closed")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_create_task_canonicalizes_sender_alias_but_preserves_storage_bus_names():
    root, vault = _make_alias_vault()
    try:
        result = create_task(
            vault,
            sender="Axiom-Codex",
            recipient="Hermes",
            request="Inspect a repo slice.",
            expected_output="Return a proposal.",
        )

        assert result["created"] is True
        task = list_tasks(vault, recipient="Hermes")[0]
        assert task["sender"] == "Codex"
        assert task["recipient"] == "Hermes"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_upsert_heartbeat_canonicalizes_alias_to_instance_heartbeat():
    root, vault = _make_alias_vault()
    try:
        upsert_heartbeat(
            vault,
            runtime="Axiom-Codex",
            status="idle",
            health="ok",
            summary="alias heartbeat",
            now_iso="2026-04-30T00:00:00+00:00",
        )

        rows = list_heartbeats(vault, runtime="Codex")
        assert len(rows) == 1
        assert rows[0]["runtime"] == "Codex"
        assert rows[0]["runtime_instance_id"] == "Axiom-Codex"
        assert rows[0]["heartbeat_scope"] == "instance"
        assert rows[0]["heartbeat_key"] == "Codex:Axiom-Codex"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_create_task_invalid_sender_error_preserves_requested_alias():
    root, vault = _make_alias_vault()
    try:
        result = create_task(
            vault,
            sender="BadRuntime",
            recipient="Hermes",
            request="Inspect.",
            expected_output="Return result.",
        )

        assert result["created"] is False
        assert "Unknown sender runtime: 'BadRuntime'" in result["reason"]
        assert "'None'" not in result["reason"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_runtime_identity_collision_fails_closed_across_runtimes():
    root, vault = _make_alias_vault()
    try:
        rival_dir = vault / "runtime" / "rival"
        rival_dir.mkdir(parents=True, exist_ok=True)
        (rival_dir / "capabilities.yaml").write_text(
            """
bus_name: Rival
personal_runtime_name: Axiom-Codex
heartbeat_stale_seconds: 900
handles:
  - task_type: review
""".lstrip(),
            encoding="utf-8",
        )

        try:
            resolve_runtime_identity(vault, "Axiom-Codex")
        except CapabilityError as exc:
            assert "Runtime identity collision" in str(exc)
            assert "Axiom-Codex" in str(exc)
        else:
            raise AssertionError("duplicate runtime aliases should fail closed")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_claim_and_update_accept_alias_runtime_and_store_canonical_owner():
    root, vault = _make_alias_vault()
    try:
        created = create_task(
            vault,
            sender="Hermes",
            recipient="Codex",
            request="Patch this.",
            expected_output="Return patch artifact.",
        )
        task_id = created["task_id"]

        claim = claim_task(vault, task_id=task_id, runtime="Axiom-Codex")
        assert claim["claimed"] is True

        task = list_tasks(vault, recipient="Codex")[0]
        assert task["owner"] == "Codex"
        assert task["owner_instance"] == "Axiom-Codex"

        update = update_task_status(
            vault,
            task_id=task_id,
            runtime="Axiom-Codex",
            status="done",
            event_type="completed",
            message="Done from alias runtime.",
        )
        assert update["updated"] is True

        conn = sqlite3.connect(db_path(vault))
        event_sender = conn.execute(
            "SELECT sender FROM events WHERE task_id = ? AND event_type = 'completed'",
            (task_id,),
        ).fetchone()[0]
        conn.close()
        assert event_sender == "Codex"
    finally:
        shutil.rmtree(root, ignore_errors=True)
