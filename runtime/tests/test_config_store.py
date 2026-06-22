"""Tests for bounded Phase 9 config-store surface."""

from __future__ import annotations

import json
import sys
from pathlib import Path


_HERE = Path(__file__).resolve()
_VAULT_ROOT = _HERE.parents[2]
if str(_VAULT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VAULT_ROOT))

import runtime.cli.main as cli  # noqa: E402
from runtime.config.settings_summary import build_settings_summary  # noqa: E402
from runtime.config.store import ensure_config_store, load_config_store, validate_config_store  # noqa: E402


def _make_min_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "CLAUDE.md").write_text("# ChaseOS", encoding="utf-8")
    return vault


class TestConfigStoreModule:
    def test_ensure_config_store_seeds_default_file(self, tmp_path: Path) -> None:
        vault = _make_min_vault(tmp_path)
        path = ensure_config_store(vault_root=vault)
        payload = load_config_store(vault_root=vault)

        assert path == vault / ".chaseos" / "config.yaml"
        assert path.exists()
        assert payload["default_provider"] is None
        assert payload["log_verbosity"] == "normal"
        assert payload["scaffold_profile"] == "default"

    def test_settings_summary_uses_defaults_without_seeding_config(self, tmp_path: Path) -> None:
        vault = _make_min_vault(tmp_path)
        config_path = vault / ".chaseos" / "config.yaml"

        payload = build_settings_summary(vault_root=vault)

        assert payload["read_only"] is True
        assert payload["mutates_config"] is False
        assert payload["authority_expansion"] is False
        assert payload["config"]["config_present"] is False
        assert payload["config"]["using_defaults"] is True
        assert payload["config"]["values"]["log_verbosity"] == "normal"
        assert config_path.exists() is False

    def test_settings_summary_blocks_unknown_configured_defaults(self, tmp_path: Path) -> None:
        vault = _make_min_vault(tmp_path)
        config_path = ensure_config_store(vault_root=vault)
        config_path.write_text(
            "\n".join(
                [
                    "default_provider: ghost_provider",
                    "default_runtime: ghost_runtime",
                    "log_verbosity: normal",
                    "scaffold_profile: default",
                    "scaffold_defaults:",
                    "  project_root: null",
                    "  workspace_root: null",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        payload = build_settings_summary(vault_root=vault)

        assert payload["settings_posture"] == "blocked"
        codes = {item["code"] for item in payload["attention_items"]}
        assert "unknown_default_provider" in codes
        assert "unknown_default_runtime" in codes

    def test_settings_summary_surfaces_nb017_gate_coverage_readiness(self, tmp_path: Path) -> None:
        vault = _make_min_vault(tmp_path)

        payload = build_settings_summary(vault_root=vault)
        readiness = payload["gate_coverage_readiness"]

        assert readiness["backlog_id"] == "NB-017"
        assert readiness["read_only"] is True
        assert readiness["authority_expansion"] is False
        assert readiness["approval_consumed"] is False
        assert readiness["host_mutation_attempted"] is False
        assert readiness["browser_action_executed"] is False
        assert readiness["canonical_writeback_allowed"] is False
        assert readiness["coverage_status"] == "partial_hardening_readiness_visible"
        assert readiness["studio_mapping"] == ["Approvals", "Settings", "Browser Runtime"]

        surface_ids = {surface["surface_id"] for surface in readiness["surfaces"]}
        assert {
            "gateway_workflow_dispatch",
            "studio_settings_gate_readiness",
            "lifecycle_host_side_effects",
            "browser_operator_actions",
            "browser_cdp_read_only_proof",
        }.issubset(surface_ids)

        browser_cdp = next(
            surface for surface in readiness["surfaces"] if surface["surface_id"] == "browser_cdp_read_only_proof"
        )
        assert browser_cdp["approval_schema_id"] == "bosl.cdp_read_only_proof.v1"
        assert browser_cdp["approval_required"] is True
        assert browser_cdp["allowed_now"] is False
        assert browser_cdp["executes_browser"] is False

        lifecycle = next(
            surface for surface in readiness["surfaces"] if surface["surface_id"] == "lifecycle_host_side_effects"
        )
        assert lifecycle["side_effect_class"] == "host_mutation"
        assert lifecycle["requires_operator_review_before_expansion"] is True


class TestConfigStoreCli:
    def test_config_list_json(self, tmp_path: Path, capsys) -> None:
        vault = _make_min_vault(tmp_path)

        exit_code = cli.main(["config", "list", "--vault-root", str(vault), "--json"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)

        assert exit_code == 0
        assert payload["ok"] is True
        assert payload["action"] == "config.list"
        assert payload["result"]["default_provider"] is None
        assert payload["result"]["log_verbosity"] == "normal"

    def test_config_validate_json_reports_ready_posture(self, tmp_path: Path, capsys) -> None:
        vault = _make_min_vault(tmp_path)

        exit_code = cli.main(["config", "validate", "--vault-root", str(vault), "--json"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)

        assert exit_code == 0
        assert payload["ok"] is True
        assert payload["action"] == "config.validate"
        assert payload["result"]["ok"] is True
        assert payload["result"]["posture"] == "ready"
        assert payload["result"]["read_only"] is True
        assert payload["result"]["schema"]["allowed_top_level_keys"]
        assert payload["result"]["issues"] == []

    def test_config_summary_json_reports_settings_posture(self, tmp_path: Path, capsys) -> None:
        vault = _make_min_vault(tmp_path)

        exit_code = cli.main(["config", "summary", "--vault-root", str(vault), "--json"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)

        assert exit_code == 0
        assert payload["ok"] is True
        assert payload["action"] == "config.summary"
        assert payload["result"]["settings_posture"] in {"ready", "degraded"}
        assert payload["result"]["config"]["config_present"] is False
        assert payload["result"]["governance"]["non_secret_config_only"] is True
        assert payload["result"]["governance"]["gate_overrides_config"] is True
        assert payload["result"]["governance"]["config_grants_authority"] is False

    def test_config_validate_reports_unknown_and_secret_keys(self, tmp_path: Path, capsys) -> None:
        vault = _make_min_vault(tmp_path)
        config_path = ensure_config_store(vault_root=vault)
        config_path.write_text(
            "\n".join(
                [
                    "default_provider: null",
                    "log_verbosity: normal",
                    "api_key: should-not-be-here",
                    "scaffold_defaults:",
                    "  project_root: 01_PROJECTS",
                    "  token: should-not-be-here",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = validate_config_store(vault_root=vault)
        assert result["ok"] is False
        assert result["posture"] == "blocked"
        issue_codes = {issue["code"] for issue in result["issues"]}
        assert "unknown_top_level_key" in issue_codes
        assert "secret_like_key" in issue_codes

        exit_code = cli.main(["config", "validate", "--vault-root", str(vault), "--json"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)

        assert exit_code == 1
        assert payload["ok"] is False
        assert payload["result"]["posture"] == "blocked"
        assert payload["result"]["mutates_config"] is False

    def test_config_set_json_persists_value(self, tmp_path: Path, capsys) -> None:
        vault = _make_min_vault(tmp_path)

        exit_code = cli.main(
            [
                "config",
                "set",
                "default_provider",
                "openai",
                "--vault-root",
                str(vault),
                "--json",
            ]
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        stored = load_config_store(vault_root=vault)

        assert exit_code == 0
        assert payload["ok"] is True
        assert payload["action"] == "config.set"
        assert payload["result"]["key"] == "default_provider"
        assert payload["result"]["value"] == "openai"
        assert stored["default_provider"] == "openai"

    def test_config_set_supports_nested_scaffold_default(self, tmp_path: Path, capsys) -> None:
        vault = _make_min_vault(tmp_path)

        exit_code = cli.main(
            [
                "config",
                "set",
                "scaffold_defaults.project_root",
                "01_PROJECTS",
                "--vault-root",
                str(vault),
                "--json",
            ]
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        stored = load_config_store(vault_root=vault)

        assert exit_code == 0
        assert payload["ok"] is True
        assert payload["action"] == "config.set"
        assert payload["result"]["key"] == "scaffold_defaults.project_root"
        assert stored["scaffold_defaults"]["project_root"] == "01_PROJECTS"

    def test_config_set_rejects_unknown_top_level_key(self, tmp_path: Path, capsys) -> None:
        vault = _make_min_vault(tmp_path)

        exit_code = cli.main(
            [
                "config",
                "set",
                "danger_mode",
                "on",
                "--vault-root",
                str(vault),
                "--json",
            ]
        )
        captured = capsys.readouterr()

        assert exit_code == 1
        assert "Unknown config key" in captured.err
