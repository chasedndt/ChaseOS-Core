"""Read-only launch-smoke inventory tests."""

from __future__ import annotations

from pathlib import Path

from runtime.lifecycle.launch_smoke_inventory import build_launch_smoke_inventory


def test_launch_smoke_inventory_is_read_only_and_covers_runtime_surfaces(tmp_path):
    vault = tmp_path
    lifecycle_dir = vault / "runtime" / "lifecycle"
    lifecycle_dir.mkdir(parents=True)
    (lifecycle_dir / "hermes.lifecycle.yaml").write_text("runtime: hermes\n", encoding="utf-8")
    (lifecycle_dir / "openclaw.lifecycle.yaml").write_text("runtime: openclaw\n", encoding="utf-8")

    result = build_launch_smoke_inventory(vault_root=vault)

    assert result["action"] == "launch-smoke-inventory"
    assert result["read_only"] is True
    assert result["mutation_enabled"] is False
    assert result["vault_root"] == str(vault)
    assert {item["runtime_id"] for item in result["lifecycle_files"]} >= {"hermes", "openclaw"}
    assert all(item["exists"] for item in result["lifecycle_files"] if item["runtime_id"] in {"hermes", "openclaw"})

    status_commands = {item["id"]: item["command"] for item in result["status_commands"]}
    assert status_commands["startup-surfaces"] == "chaseos runtime startup-surfaces --runtime all --json"
    assert status_commands["hermes-gateway-config"] == "chaseos runtime hermes-gateway-config --action status --json"
    assert status_commands["coordination-watch-bootstrap"] == "chaseos runtime coordination-watch-bootstrap --runtime hermes --action activation-report --json"

    pytest_targets = {target["path"] for target in result["pytest_targets"]}
    assert "runtime/lifecycle/test_startup_surfaces.py" in pytest_targets
    assert "runtime/lifecycle/test_hermes_gateway_config.py" in pytest_targets
    assert "runtime/test_runtime_startup_surfaces_cli.py" in pytest_targets
    assert "runtime/test_chaseos_startup_surfaces_cli.py" in pytest_targets


def test_launch_smoke_inventory_reports_missing_lifecycle_files_without_writes(tmp_path):
    result = build_launch_smoke_inventory(vault_root=tmp_path)

    missing = {item["runtime_id"]: item for item in result["lifecycle_files"]}
    assert missing["hermes"]["exists"] is False
    assert missing["openclaw"]["exists"] is False
    assert result["write_targets"] == []
    assert result["notes"]
