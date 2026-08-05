from pathlib import Path

from runtime.connections.manifests import list_provider_manifests, load_manifest
from runtime.connections.store import db_path_for_vault, init_store, list_connections, registry_overview, seed_manifest_connection


def test_bundled_connection_manifests_have_safe_defaults():
    manifests = list_provider_manifests()
    ids = {manifest.id for manifest in manifests}
    assert {
        "discord",
        "github",
        "imessage_mac",
        "local_files",
        "slack",
        "telegram",
        "whatsapp_business_cloud",
        "whatsapp_personal_lab",
    }.issubset(ids)
    for manifest in manifests:
        assert manifest.default_profile() == "read_only"
        assert manifest.capabilities
        write_like = [
            cap for cap in manifest.capabilities
            if cap.action_type in {"write", "delete", "external_egress"}
        ]
        assert all(cap.safety_level in {"approval_required", "restricted", "disabled"} for cap in write_like)
        assert not any(cap.enabled_by_default for cap in write_like)


def test_connections_store_schema_and_seed(tmp_path: Path):
    db_path = db_path_for_vault(tmp_path)
    init_result = init_store(db_path)
    assert init_result["schema_version"] == "connections.registry.v1"
    assert init_result["table_count"] >= 10

    manifest = load_manifest("local_files")
    connection_id = seed_manifest_connection(db_path, manifest, owner_user_id="test_operator")
    assert connection_id.startswith("conn_local_files_")

    rows = list_connections(db_path)
    assert len(rows) == 1
    assert rows[0]["provider"] == "local_files"
    assert rows[0]["permission_profile"] == "read_only"

    overview = registry_overview(tmp_path)
    assert overview["db_exists"] is True
    assert overview["policy"]["require_approval_for_writes"] is True
    assert overview["connections"][0]["id"] == connection_id
