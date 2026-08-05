"""Load provider manifests — bundled ones, and your own.

Connections describe *what a provider is allowed to do* as data rather than code: each
capability declares an action type, a safety level, and whether it is enabled by default.
Core's rule is that nothing dangerous is on out of the box — write and external-egress
capabilities ship disabled and approval-gated.

Manifest loading takes an injectable ``manifest_dir``, so you can point Core at your own
provider definitions without forking the repo.

Run:  python examples/03_load_connection_manifests.py
"""

import tempfile
from pathlib import Path

from runtime.connections.manifests import (
    list_provider_manifests,
    load_manifest,
    parse_manifest,
)

CUSTOM_MANIFEST = """
id: internal_wiki
name: Internal Wiki
category: knowledge
version: 0.1.0
status: alpha
supports:
  data_source: true
  write_actions: true
connection_modes:
  - read_only
capabilities:
  - tool_name: wiki.search
    action_type: read
    safety_level: safe
    enabled_by_default: true
    description: Full-text search over wiki pages.
  - tool_name: wiki.edit_page
    action_type: write
    safety_level: sensitive
    enabled_by_default: false
    description: Edit a wiki page. Approval-gated.
"""


def main() -> None:
    # 1. Manifests bundled with Core.
    bundled = list_provider_manifests()
    print(f"bundled providers: {len(bundled)}")
    for manifest in bundled:
        writes = sum(1 for c in manifest.capabilities if c.action_type != "read")
        enabled_writes = sum(
            1 for c in manifest.capabilities
            if c.action_type != "read" and c.enabled_by_default
        )
        print(
            f"  {manifest.id:<26} {manifest.status:<6} "
            f"capabilities={len(manifest.capabilities):<3} "
            f"write/egress={writes} (enabled by default: {enabled_writes})"
        )
        # Core's safety posture: no write capability is on by default, anywhere.
        assert enabled_writes == 0, (
            f"{manifest.id} enables a write capability by default"
        )

    # 2. Inspect one provider in detail.
    github = load_manifest("github")
    print(f"\n{github.name} ({github.id}) — {github.category}, status={github.status}")
    for capability in github.capabilities:
        state = "on " if capability.enabled_by_default else "off"
        print(
            f"  [{state}] {capability.tool_name:<28} "
            f"{capability.action_type:<8} {capability.safety_level}"
        )

    # 3. Your own manifest directory — no fork required.
    with tempfile.TemporaryDirectory() as tmp:
        custom_dir = Path(tmp)
        (custom_dir / "internal_wiki.yaml").write_text(CUSTOM_MANIFEST, encoding="utf-8")

        mine = list_provider_manifests(manifest_dir=custom_dir)
        print(f"\ncustom manifest dir: {len(mine)} provider(s)")
        for manifest in mine:
            print(f"  {manifest.id} — {manifest.name}")
            for capability in manifest.capabilities:
                state = "on " if capability.enabled_by_default else "off"
                print(f"    [{state}] {capability.tool_name} ({capability.safety_level})")

        wiki = load_manifest("internal_wiki", manifest_dir=custom_dir)
        assert wiki.name == "Internal Wiki"
        edit = next(c for c in wiki.capabilities if c.tool_name == "wiki.edit_page")
        assert edit.enabled_by_default is False, "write capability must default to off"

    # 4. Manifests can also be parsed straight from a dict (e.g. fetched from a service).
    parsed = parse_manifest(
        {
            "id": "in_memory",
            "name": "In-memory provider",
            "category": "custom",
            "version": "0.0.1",
            "status": "experimental",
            "capabilities": [],
        }
    )
    print(f"\nparsed from dict: {parsed.id} ({parsed.status})")

    print("\nOK")


if __name__ == "__main__":
    main()
