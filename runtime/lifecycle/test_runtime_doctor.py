"""Runtime doctor model for first-run/autostart diagnostics."""

from __future__ import annotations

from pathlib import Path

from runtime.lifecycle.runtime_doctor import build_runtime_doctor_report


VAULT_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_doctor_reports_hermes_allowlist_bootstrap_route() -> None:
    report = build_runtime_doctor_report(VAULT_ROOT, runtime_id="hermes", probe_processes=False)

    assert report["ok"] is True
    assert report["surface"] == "runtime_doctor"
    assert report["runtime_id"] == "hermes"
    assert report["shown_in"] == "Settings → Runtime Services"
    assert report["checks"]["lifecycle_record"]["ok"] is True
    assert "gateway_config" in report["checks"]
    assert report["first_run_bootstrap"]["primary_cta_label"] == "Run Startup Doctor"
    assert report["first_run_bootstrap"]["config_cta_label"] == "Fix Gateway Config"
    assert report["first_run_bootstrap"]["host_mutation_on_page_load"] is False
    assert report["authority"]["provider_calls_allowed"] is False
    assert report["security"]["secret_values_included"] is False


def test_runtime_doctor_supports_non_hermes_runtime_cards_without_hermes_allowlist() -> None:
    report = build_runtime_doctor_report(VAULT_ROOT, runtime_id="archon", probe_processes=False)

    assert report["ok"] is True
    assert report["runtime_id"] == "archon"
    assert report["checks"]["lifecycle_record"]["ok"] is True
    assert "gateway_config" not in report["checks"]
    assert report["first_run_bootstrap"]["shown_for_runtime"] == "archon"
