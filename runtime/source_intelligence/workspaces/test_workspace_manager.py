from __future__ import annotations

import json
from pathlib import Path

from runtime.source_intelligence.workspaces import workspace_manager


def test_load_workspace_accepts_utf8_bom(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(workspace_manager, "_SIC_WORKSPACES", tmp_path)
    workspace_id = "bom-workspace"
    workspace_dir = tmp_path / workspace_id
    workspace_dir.mkdir(parents=True)
    payload = {
        "id": "workspace-record-id",
        "slug": workspace_id,
        "name": "BOM workspace",
        "source_refs": {},
    }
    (workspace_dir / "workspace.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8")
    )

    result = workspace_manager.load_workspace(workspace_id)

    assert result["success"] is True
    assert result["workspace"] == payload
