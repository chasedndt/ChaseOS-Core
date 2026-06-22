"""Tests for runtime provider/fallback governance status CLI."""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path


_HERE = Path(__file__).resolve()
_VAULT_ROOT = _HERE.parents[2]
if str(_VAULT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VAULT_ROOT))

import runtime.cli.main as cli  # noqa: E402
from runtime.agent_bus.bus import claim_task, create_task, upsert_heartbeat  # noqa: E402
from runtime.agent_bus.backend_loader import clear_backend_cache  # noqa: E402
from runtime.acquisition.connector_health import ConnectorHealthEvent, append_connector_health_event  # noqa: E402
from runtime.providers.governance_status import build_runtime_provider_status  # noqa: E402
from runtime.providers.state_ledger import ProviderStateEvent, append_provider_state_event  # noqa: E402
from runtime.sbp.delivery_health import DeliveryHealthEvent, append_delivery_health_event  # noqa: E402


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_runtime_provider_vault() -> Path:
    vault = _VAULT_ROOT / ".codex_tmp_test" / "runtime-provider-status" / uuid.uuid4().hex / "vault"
    vault.mkdir(parents=True)
    _write_text(vault / "CLAUDE.md", "# ChaseOS\n")
    _write_text(
        vault / ".chaseos" / "config.yaml",
        "\n".join(
            [
                "default_provider: openai",
                "default_runtime: OpenClaw",
                'log_verbosity: "normal"',
                'scaffold_profile: "default"',
                "scaffold_defaults:",
                "  project_root: null",
                "  workspace_root: null",
            ]
        )
        + "\n",
    )
    _write_text(
        vault / "runtime" / "openclaw" / "capabilities.yaml",
        "\n".join(
            [
                "runtime: openclaw",
                "bus_name: OpenClaw",
                'display_name: "OpenClaw"',
                'description: "Primary operator runtime"',
                "handles:",
                "  - task_type: operator-briefing",
                "    priority: primary",
                "max_concurrent_tasks: 3",
                "heartbeat_stale_seconds: 900",
                "priority_ceiling: normal",
            ]
        )
        + "\n",
    )
    _write_text(
        vault / "runtime" / "hermes" / "capabilities.yaml",
        "\n".join(
            [
                "runtime: hermes",
                "bus_name: Hermes",
                'display_name: "Hermes"',
                'description: "Review runtime"',
                "handles:",
                "  - task_type: review",
                "    priority: primary",
                "max_concurrent_tasks: 2",
                "heartbeat_stale_seconds: 900",
                "priority_ceiling: high",
            ]
        )
        + "\n",
    )
    _write_text(
        vault / "runtime" / "openclaw" / "model_config.yaml",
        "\n".join(
            [
                "runtime: openclaw",
                "primary:",
                "  model_id: claude-sonnet-4-6",
                "  max_tokens: 4096",
                "  temperature: 0.3",
                "fallbacks:",
                "  - model_id: claude-haiku-4-5-20251001",
                "    max_tokens: 4096",
                "    temperature: 0.3",
            ]
        )
        + "\n",
    )
    _write_text(
        vault / "runtime" / "hermes" / "model_config.yaml",
        "\n".join(
            [
                "runtime: hermes",
                "primary:",
                "  model_id: claude-opus-4-7",
                "  max_tokens: 8192",
                "  temperature: 0.2",
                "fallbacks:",
                "  - model_id: claude-sonnet-4-6",
                "    max_tokens: 8192",
                "    temperature: 0.2",
            ]
        )
        + "\n",
    )
    clear_backend_cache(vault)
    upsert_heartbeat(
        vault,
        runtime="OpenClaw",
        status="busy",
        health="ok",
        summary="test heartbeat",
    )
    upsert_heartbeat(
        vault,
        runtime="Hermes",
        status="idle",
        health="ok",
        summary="test heartbeat",
    )
    claimed = create_task(
        vault,
        sender="Hermes",
        recipient="OpenClaw",
        intent="TASK",
        priority="normal",
        request="Run operator briefing",
        expected_output="Briefing",
    )
    claim_task(vault, task_id=claimed["task_id"], runtime="OpenClaw")
    create_task(
        vault,
        sender="OpenClaw",
        recipient="Hermes",
        intent="TASK",
        priority="normal",
        request="Review output",
        expected_output="Review",
    )
    return vault


def _cleanup_runtime_provider_vault(vault: Path) -> None:
    clear_backend_cache(vault)
    root = (_VAULT_ROOT / ".codex_tmp_test" / "runtime-provider-status").resolve()
    target = vault.parent.resolve()
    if target.parent == root:
        shutil.rmtree(target, ignore_errors=True)


def test_runtime_provider_status_aggregates_models_queue_and_health() -> None:
    vault = _make_runtime_provider_vault()

    try:
        payload = build_runtime_provider_status(
            vault_root=vault,
            runtime_filter="all",
            stuck_after_seconds=0,
            probe_health=False,
        )

        assert payload["schema_version"] == 1
        assert payload["active_runtime"]["runtime_id"] == "OpenClaw"
        assert payload["operator_default_provider"]["provider_id"] == "openai"
        assert payload["provider_state_ledger"]["event_count"] == 0
        assert payload["adapter_health_rollup"]["status"] == "no_events"
        assert payload["adapter_health_rollup"]["provider_state_boundary"]["feeds_provider_state_ledger"] is False
        assert payload["rate_limit_state"]["status"] == "no_events"
        assert payload["rate_limit_state"]["tracked"] is True
        assert payload["cooldown_state"]["status"] == "no_events"
        assert payload["cooldown_state"]["tracked"] is True
        assert payload["recovery_to_primary"]["status"] == "no_events"
        assert payload["recovery_to_primary"]["tracked"] is True
        assert payload["queues"]["queued_count"] == 1
        assert payload["queues"]["no_chunk_count"] == 1
        assert payload["readiness_summary"]["posture"] == "degraded"
        assert payload["readiness_summary"]["provider_valid_count"] >= 1
        assert payload["readiness_summary"]["runtime_count"] == 2
        assert payload["readiness_summary"]["queue_no_chunk_count"] == 1
        assert payload["readiness_summary"]["adapter_health_status"] == "no_events"
        assert payload["readiness_summary"]["adapter_health_affects_provider_fallback"] is False
        assert "no_chunk_jobs" in payload["readiness_summary"]["degradation_reasons"]
        assert payload["operator_summary"]["status"] == "attention"
        assert payload["operator_summary"]["provider_runtime_posture"] == "degraded"
        assert payload["operator_summary"]["read_only"] is True
        assert payload["operator_summary"]["boundary"]["presentation_only"] is True
        assert payload["operator_summary"]["boundary"]["controls_provider_switching"] is False
        assert payload["operator_summary"]["model_route"]["primary"]["provider_id"] == "claude"
        assert payload["operator_summary"]["queue"]["no_chunk_count"] == 1
        assert "no_chunk_jobs" in {
            item["code"] for item in payload["operator_summary"]["attention_items"]
        }

        openclaw = next(item for item in payload["runtimes"] if item["bus_name"] == "OpenClaw")
        assert openclaw["adapter_health"]["heartbeat_health"] == "ok"
        assert openclaw["adapter_health"]["lifecycle_probe_checked"] is False
        assert openclaw["model_binding"]["primary"]["provider_id"] == "claude"
        assert openclaw["model_binding"]["fallback_count"] == 1
        assert openclaw["fallback_governance"]["active_fallback_source"] == "no_active_fallback_event"
    finally:
        _cleanup_runtime_provider_vault(vault)


def test_runtime_provider_status_includes_adjacent_adapter_health_rollup() -> None:
    vault = _make_runtime_provider_vault()

    try:
        append_connector_health_event(
            vault,
            ConnectorHealthEvent(
                event_type="connector.capture_failed",
                connector_id="rss_fetch",
                provider="rss",
                source_id="source://rss/test",
                surface="acquisition.rss_fetch",
                failure_reason="network_error",
                data={"source_class": "rss"},
            ),
        )
        append_delivery_health_event(
            vault,
            DeliveryHealthEvent(
                event_type="delivery.attempt_failed",
                adapter_id="discord",
                provider="discord",
                surface="delivery.discord_webhook",
                pipeline_id="sbp-test",
                delivery_target="env:TEST_DISCORD_WEBHOOK",
                failure_reason="credential_missing",
                data={"env_var": "TEST_DISCORD_WEBHOOK"},
            ),
        )

        payload = build_runtime_provider_status(
            vault_root=vault,
            runtime_filter="all",
            stuck_after_seconds=0,
            probe_health=False,
        )

        rollup = payload["adapter_health_rollup"]
        assert rollup["status"] == "attention"
        assert rollup["read_only"] is True
        assert rollup["provider_state_boundary"]["controls_provider_switching"] is False
        assert rollup["totals"]["event_count"] == 2
        assert rollup["totals"]["failed_count"] == 2
        assert rollup["lanes"]["connector_health"]["status"] == "attention"
        assert rollup["lanes"]["delivery_health"]["status"] == "attention"
        assert {surface["surface_id"] for surface in rollup["surfaces"]} == {"rss_fetch", "discord"}
        assert payload["provider_state_ledger"]["event_count"] == 0
        assert payload["rate_limit_state"]["status"] == "no_events"
        assert payload["cooldown_state"]["status"] == "no_events"
        assert payload["readiness_summary"]["adapter_health_status"] == "attention"
        assert payload["readiness_summary"]["adapter_health_failed_count"] == 2
        assert payload["readiness_summary"]["adapter_health_affects_provider_fallback"] is False
        operator = payload["operator_summary"]
        assert operator["status"] == "attention"
        assert operator["adapter_health"]["status"] == "attention"
        assert operator["adapter_health"]["failed_count"] == 2
        assert operator["adapter_health"]["affects_provider_fallback"] is False
        adapter_item = next(
            item for item in operator["attention_items"] if item["code"] == "adapter_health_attention"
        )
        assert adapter_item["affects_provider_fallback"] is False
        assert operator["provider_governance"]["provider_state_event_count"] == 0
    finally:
        _cleanup_runtime_provider_vault(vault)


def test_runtime_provider_status_reads_provider_state_ledger() -> None:
    vault = _make_runtime_provider_vault()

    try:
        append_provider_state_event(
            vault,
            ProviderStateEvent(
                event_type="provider.rate_limited",
                runtime="openclaw",
                provider_id="claude",
                model_id="claude-sonnet-4-6",
                data={"retry_after_seconds": 3600},
                source={"surface": "test"},
            ),
        )
        append_provider_state_event(
            vault,
            ProviderStateEvent(
                event_type="provider.cooldown_started",
                runtime="openclaw",
                provider_id="claude",
                model_id="claude-sonnet-4-6",
                data={"cooldown_seconds": 3600},
                source={"surface": "test"},
            ),
        )
        append_provider_state_event(
            vault,
            ProviderStateEvent(
                event_type="provider.fallback_activated",
                runtime="openclaw",
                provider_id="claude",
                model_id="claude-haiku-4-5-20251001",
                data={
                    "primary_model_id": "claude-sonnet-4-6",
                    "fallback_model_id": "claude-haiku-4-5-20251001",
                    "reason": "rate_limit",
                },
                source={"surface": "test"},
            ),
        )

        payload = build_runtime_provider_status(
            vault_root=vault,
            runtime_filter="OpenClaw",
            stuck_after_seconds=0,
            probe_health=False,
        )

        assert payload["provider_state_ledger"]["event_count"] == 3
        assert payload["rate_limit_state"]["status"] == "active"
        assert payload["rate_limit_state"]["active_count"] == 1
        assert payload["cooldown_state"]["status"] == "active"
        assert payload["cooldown_state"]["active_count"] == 1
        assert payload["recovery_to_primary"]["status"] == "fallback_active"

        openclaw = payload["runtimes"][0]
        assert openclaw["bus_name"] == "OpenClaw"
        assert openclaw["fallback_governance"]["active_fallback_model"] == "claude-haiku-4-5-20251001"
        assert openclaw["fallback_governance"]["active_fallback_source"] == "provider_state_ledger"
        assert openclaw["fallback_governance"]["recovery_to_primary"]["status"] == "fallback_active"
        operator_codes = {item["code"] for item in payload["operator_summary"]["attention_items"]}
        assert {"active_rate_limit", "active_cooldown", "fallback_active"}.issubset(operator_codes)
        assert payload["operator_summary"]["provider_governance"]["rate_limit_status"] == "active"
        assert payload["operator_summary"]["provider_governance"]["cooldown_status"] == "active"
        assert payload["operator_summary"]["provider_governance"]["recovery_to_primary_status"] == "fallback_active"
        assert any(
            item["affects_provider_fallback"]
            for item in payload["operator_summary"]["attention_items"]
            if item["code"] in {"active_rate_limit", "active_cooldown", "fallback_active"}
        )
    finally:
        _cleanup_runtime_provider_vault(vault)


def test_runtime_provider_status_cli_json_contract(capsys) -> None:
    vault = _make_runtime_provider_vault()

    try:
        exit_code = cli.main(
            [
                "runtime",
                "provider-status",
                "--runtime",
                "all",
                "--stuck-after-seconds",
                "0",
                "--vault-root",
                str(vault),
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)

        assert exit_code == 0
        assert payload["ok"] is True
        assert payload["action"] == "runtime.provider-status"
        assert payload["result"]["active_runtime"]["runtime_id"] == "OpenClaw"
        assert payload["result"]["queues"]["no_chunk_count"] == 1
        assert payload["result"]["readiness_summary"]["posture"] == "degraded"
        assert payload["result"]["operator_summary"]["status"] == "attention"
        assert payload["result"]["operator_summary"]["boundary"]["controls_adapter_retries"] is False
    finally:
        _cleanup_runtime_provider_vault(vault)
