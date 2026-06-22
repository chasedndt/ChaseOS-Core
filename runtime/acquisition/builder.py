"""Source-pack builder substrate for Acquisition + Normalization Pass 1A."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import AcquiredSource, LocalDeclaredSourceAdapter
from .models import BriefingReadyInputSet, NormalizedSourcePack, SourcePacket
from .plan import AcquisitionPlan, validate_acquisition_plan
from .source_classes import default_origin_kind_for_source_class
from .validators import (
    OWNER_LAYER,
    SCHEMA_VERSION,
    AcquisitionValidationError,
    validate_briefing_ready_input_set,
    validate_normalized_source_pack,
    validate_source_packet,
    validate_write_path,
)


WORKFLOW_ID = "source_pack_builder"
MAX_NORMALIZED_CHARS = 50_000


class AcquisitionBuildError(RuntimeError):
    """Fail-closed build error for Acquisition + Normalization Pass 1A."""


@dataclass
class AcquisitionBuildResult:
    plan_id: str
    files_read: list[str]
    source_packet_paths: list[str]
    normalized_source_pack_path: str
    briefing_ready_input_set_path: str
    source_packets: list[dict[str, Any]]
    normalized_source_pack: dict[str, Any]
    briefing_ready_input_set: dict[str, Any]
    writebacks: list[dict[str, Any]]

    def to_aor_result(self, objective: dict[str, Any], project_scope: str = "", **extra: Any) -> dict[str, Any]:
        result = {
            "workflow": WORKFLOW_ID,
            "plan_id": self.plan_id,
            "project_scope": project_scope,
            "objective": objective,
            "files_read": self.files_read,
            "source_packet_paths": self.source_packet_paths,
            "normalized_source_pack_path": self.normalized_source_pack_path,
            "briefing_ready_input_set_path": self.briefing_ready_input_set_path,
            "artifact_types": [
                "briefing_ready_input_set",
                "normalized_source_pack",
                "source_packet",
            ],
            "writebacks": self.writebacks,
        }
        result.update(extra)
        return result


class SourcePackBuilder:
    """Build source_packet, normalized_source_pack, and briefing_ready_input_set artifacts."""

    def __init__(self, adapter: LocalDeclaredSourceAdapter | None = None) -> None:
        self.adapter = adapter or LocalDeclaredSourceAdapter()

    def build(self, plan: AcquisitionPlan, vault_root: Path) -> AcquisitionBuildResult:
        acquired = self.adapter.acquire(plan, vault_root)
        if not acquired:
            raise AcquisitionBuildError("acquisition plan produced no acquired sources")

        packets = [
            self._build_source_packet(plan, item, index).to_dict()
            for index, item in enumerate(acquired, start=1)
        ]
        for packet in packets:
            validate_source_packet(packet)

        normalized_pack = self._build_normalized_source_pack(plan, packets).to_dict()
        validate_normalized_source_pack(normalized_pack)

        briefing_input = self._build_briefing_ready_input_set(plan, packets, normalized_pack).to_dict()
        validate_briefing_ready_input_set(briefing_input)

        source_packet_paths: list[str] = []
        writebacks: list[dict[str, Any]] = []
        for index, packet in enumerate(packets, start=1):
            path = validate_write_path(f"{plan.output_targets.pack_root}/source_packet_{index:03d}.json")
            source_packet_paths.append(path)
            writebacks.append({"path": path, "content": _json_dump(packet), "content_type": "application/json"})

        normalized_pack_path = validate_write_path(f"{plan.output_targets.pack_root}/normalized_source_pack.json")
        briefing_input_path = validate_write_path(f"{plan.output_targets.pack_root}/briefing_ready_input_set.json")
        writebacks.extend([
            {
                "path": normalized_pack_path,
                "content": _json_dump(normalized_pack),
                "content_type": "application/json",
            },
            {
                "path": briefing_input_path,
                "content": _json_dump(briefing_input),
                "content_type": "application/json",
            },
        ])

        return AcquisitionBuildResult(
            plan_id=plan.plan_id,
            files_read=list(plan.scope.read_scope),
            source_packet_paths=source_packet_paths,
            normalized_source_pack_path=normalized_pack_path,
            briefing_ready_input_set_path=briefing_input_path,
            source_packets=packets,
            normalized_source_pack=normalized_pack,
            briefing_ready_input_set=briefing_input,
            writebacks=writebacks,
        )

    def build_and_write(self, plan: AcquisitionPlan, vault_root: Path) -> AcquisitionBuildResult:
        """Build and write artifacts to declared non-canonical output paths."""
        result = self.build(plan, vault_root)
        root = vault_root.resolve()
        for writeback in result.writebacks:
            destination = (root / writeback["path"]).resolve()
            if not _path_is_relative_to(destination, root):
                raise AcquisitionBuildError(f"writeback escapes vault root: {writeback['path']}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(str(writeback["content"]), encoding="utf-8")
        if plan.output_targets.latest_pointer_path:
            pointer = {
                "schema_version": SCHEMA_VERSION,
                "pointer_type": "briefing_ready_input_set_latest",
                "briefing_ready_input_set_path": result.briefing_ready_input_set_path,
                "generated_at": plan.created_at,
                "plan_id": result.plan_id,
                "source_packet_count": len(result.source_packets),
            }
            pointer_dest = (root / plan.output_targets.latest_pointer_path).resolve()
            if not _path_is_relative_to(pointer_dest, root):
                raise AcquisitionBuildError(
                    f"latest_pointer_path escapes vault root: {plan.output_targets.latest_pointer_path}"
                )
            pointer_dest.parent.mkdir(parents=True, exist_ok=True)
            pointer_dest.write_text(_json_dump(pointer), encoding="utf-8")
        return result

    def _base_envelope(self, plan: AcquisitionPlan, artifact_id: str, artifact_type: str) -> dict[str, Any]:
        return {
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "schema_version": SCHEMA_VERSION,
            "created_at": plan.created_at,
            "owner_layer": OWNER_LAYER,
            "owning_workflow": WORKFLOW_ID,
            "objective": _objective_dict(plan),
            "acquirer": _acquirer_dict(plan),
            "scope": {
                "read_scope": list(plan.scope.read_scope),
                "browser_scope": list(plan.scope.browser_scope),
                "network_scope": list(plan.scope.network_scope),
                "cadence_or_trigger": plan.trigger,
            },
            "promotion": {
                "status": plan.promotion.status,
                "allowed_next_steps": list(plan.promotion.allowed_next_steps),
                "canonical_mutation_allowed": False,
            },
            "audit": {
                "activity_log_ref": plan.audit.activity_log_ref,
                "source_hashes": [],
                "audit_required": plan.audit.audit_required,
            },
        }

    def _build_source_packet(self, plan: AcquisitionPlan, acquired: AcquiredSource, index: int) -> SourcePacket:
        source = acquired.source
        normalized_text = acquired.content[:MAX_NORMALIZED_CHARS]
        content_hash = _sha256(normalized_text)
        artifact_id = f"sp_{plan.plan_id}_{index:03d}_{content_hash[:8]}"
        captured_at = source.captured_at or plan.created_at
        origin_kind = source.origin_kind or _default_origin_kind(source.source_class)
        source_origin = {
            "kind": origin_kind,
            "ref": source.path,
            "display_name": source.display_name,
        }
        if source.declared_url:
            source_origin["declared_url"] = source.declared_url

        raw_pointer = {"path": source.path, "sidecar_ref": source.sidecar_path}
        provenance = {
            "source_origin": source_origin,
            "acquisition_method": source.acquisition_method,
            "acquirer": _acquirer_dict(plan),
            "captured_at": captured_at,
            "source_event_at": source.source_event_at,
            "freshness_window": source.freshness_window,
            "content_sha256": content_hash,
            "sidecar_ref": source.sidecar_path,
            "audit_ref": source.audit_ref,
            "raw_pointer": raw_pointer,
            "representation_level": "normalized",
            "transformation_chain": [
                "file_read",
                "normalize_text",
                "trust_freshness_evaluation",
            ],
        }
        transformation_chain = [
            {
                "step_id": "file_read",
                "performed_by": plan.acquirer.identity,
                "method": source.acquisition_method,
                "timestamp": plan.created_at,
                "input_ref": source.path,
                "output_ref": artifact_id,
                "representation_level": "raw",
            },
            {
                "step_id": "normalize_text",
                "performed_by": WORKFLOW_ID,
                "method": "text_normalization",
                "timestamp": plan.created_at,
                "input_ref": source.path,
                "output_ref": artifact_id,
                "representation_level": "normalized",
            },
            {
                "step_id": "trust_freshness_evaluation",
                "performed_by": WORKFLOW_ID,
                "method": "declared_metadata_evaluation",
                "timestamp": plan.created_at,
                "input_ref": artifact_id,
                "output_ref": artifact_id,
                "representation_level": "normalized",
            },
        ]

        envelope = self._base_envelope(plan, artifact_id, "source_packet")
        envelope["audit"]["source_hashes"] = [content_hash]
        return SourcePacket(
            **envelope,
            source_id=source.source_id,
            source_class=source.source_class,
            source_origin=source_origin,
            acquisition_method=source.acquisition_method,
            provenance=provenance,
            trust_evaluation={
                "base_trust_tier": source.base_trust_tier,
                "assigned_by": WORKFLOW_ID,
                "confidence": source.confidence,
                "quality_marker": source.quality_marker,
                "source_quality_notes": list(source.source_quality_notes),
                "contradiction_refs": list(source.contradiction_refs),
                "operator_approval_state": source.operator_approval_state,
                "actionability": source.actionability,
            },
            freshness={
                "source_event_at": source.source_event_at,
                "captured_at": captured_at,
                "freshness_window": source.freshness_window,
                "expires_at": source.expires_at,
                "staleness_policy": source.staleness_policy,
                "time_sensitive_domain": source.time_sensitive_domain,
            },
            transformation_chain=transformation_chain,
            raw_pointer=raw_pointer,
            content_sha256=content_hash,
            normalized_text=normalized_text,
            sidecar=acquired.sidecar,
        )

    def _build_normalized_source_pack(self, plan: AcquisitionPlan, packets: list[dict[str, Any]]) -> NormalizedSourcePack:
        artifact_id = f"nsp_{plan.plan_id}"
        envelope = self._base_envelope(plan, artifact_id, "normalized_source_pack")
        envelope["audit"]["source_hashes"] = [packet["content_sha256"] for packet in packets]
        return NormalizedSourcePack(
            **envelope,
            items=[
                {"artifact_id": packet["artifact_id"], "source_origin": packet["source_origin"]}
                for packet in packets
            ],
            source_packet_refs=[packet["artifact_id"] for packet in packets],
            source_packet_count=len(packets),
            trust_summary=_trust_summary(packets),
            freshness_summary=_freshness_summary(packets),
            transformation_chain=[
                {
                    "step_id": "source_pack_assembly",
                    "performed_by": WORKFLOW_ID,
                    "method": "first_wave_pack_assembly",
                    "timestamp": plan.created_at,
                    "input_ref": [packet["artifact_id"] for packet in packets],
                    "output_ref": artifact_id,
                    "representation_level": "normalized",
                }
            ],
            excluded_sources=[],
            known_gaps=[],
        )

    def _build_briefing_ready_input_set(
        self,
        plan: AcquisitionPlan,
        packets: list[dict[str, Any]],
        normalized_pack: dict[str, Any],
    ) -> BriefingReadyInputSet:
        artifact_id = f"bris_{plan.plan_id}"
        envelope = self._base_envelope(plan, artifact_id, "briefing_ready_input_set")
        envelope["audit"]["source_hashes"] = [packet["content_sha256"] for packet in packets]
        sections: dict[str, list[dict[str, Any]]] = {}
        for packet in packets:
            source_class = packet["source_class"]
            sections.setdefault(source_class, []).append({
                "source_packet_ref": packet["artifact_id"],
                "display_name": packet["source_origin"]["display_name"],
                "origin_ref": packet["source_origin"]["ref"],
                "freshness": packet["freshness"]["freshness_window"],
                "base_trust_tier": packet["trust_evaluation"]["base_trust_tier"],
                "actionability": packet["trust_evaluation"]["actionability"],
            })

        return BriefingReadyInputSet(
            **envelope,
            normalized_source_pack_ref=normalized_pack["artifact_id"],
            sections=sections,
            trust_summary=_trust_summary(packets),
            freshness_summary=_freshness_summary(packets),
            actionability={
                "allowed_use": "briefing_only",
                "blocked_actions": [
                    "canonical_knowledge_promotion",
                    "project_os_mutation",
                    "trade_execution",
                    "external_delivery",
                    "mcp_scope_expansion",
                    "browser_authority_expansion",
                ],
            },
            source_refs=[packet["artifact_id"] for packet in packets],
            transformation_chain=[
                {
                    "step_id": "briefing_input_set_assembly",
                    "performed_by": WORKFLOW_ID,
                    "method": "first_wave_briefing_input_assembly",
                    "timestamp": plan.created_at,
                    "input_ref": normalized_pack["artifact_id"],
                    "output_ref": artifact_id,
                    "representation_level": "normalized",
                }
            ],
        )


def build_from_plan(plan_raw: dict[str, Any], vault_root: Path, write: bool = False) -> AcquisitionBuildResult:
    plan = validate_acquisition_plan(plan_raw)
    builder = SourcePackBuilder()
    return builder.build_and_write(plan, vault_root) if write else builder.build(plan, vault_root)


def _objective_dict(plan: AcquisitionPlan) -> dict[str, Any]:
    return {
        "title": plan.objective,
        "requested_by": plan.requested_by,
        "downstream_target": plan.downstream_target,
    }


def _acquirer_dict(plan: AcquisitionPlan) -> dict[str, Any]:
    return {
        "identity": plan.acquirer.identity,
        "runtime_id": plan.acquirer.runtime_id,
        "trust_tier_ceiling": plan.acquirer.trust_tier_ceiling,
        "adapter_id": plan.acquirer.adapter_id,
        "role_card": plan.acquirer.role_card,
    }


def _default_origin_kind(source_class: str) -> str:
    try:
        return default_origin_kind_for_source_class(source_class)
    except ValueError:
        return "manual_import"


def _trust_summary(packets: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "tier1_count": 0,
        "tier2_count": 0,
        "tier3_count": 0,
        "tier4_count": 0,
        "conflicts": [],
    }
    for packet in packets:
        tier = int(packet["trust_evaluation"]["base_trust_tier"])
        key = f"tier{tier}_count"
        if key in summary:
            summary[key] += 1
        if packet["trust_evaluation"].get("quality_marker") == "conflicting":
            summary["conflicts"].append(packet["artifact_id"])
    return summary


def _freshness_summary(packets: list[dict[str, Any]]) -> dict[str, Any]:
    stale_items = []
    unknown_items = []
    for packet in packets:
        freshness = packet["freshness"]
        if freshness.get("freshness_window") == "historical" or freshness.get("staleness_policy") == "requires_refresh":
            stale_items.append(packet["artifact_id"])
        if freshness.get("freshness_window") == "unknown":
            unknown_items.append(packet["artifact_id"])
    return {
        "stale_items": stale_items,
        "unknown_freshness_items": unknown_items,
        "missing_required_sources": [],
    }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _json_dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _load_plan_file(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AcquisitionBuildError(f"plan file is not valid JSON: {path}") from exc
    if not isinstance(loaded, dict):
        raise AcquisitionBuildError("plan file must contain a JSON object")
    return loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Acquisition + Normalization Pass 1A artifacts.")
    parser.add_argument("--plan", required=True, help="Path to acquisition plan JSON")
    parser.add_argument("--vault-root", default=".", help="Vault root; defaults to current directory")
    parser.add_argument("--write", action="store_true", help="Write artifacts to declared output targets")
    args = parser.parse_args(argv)

    vault_root = Path(args.vault_root).resolve()
    plan_path = Path(args.plan)
    if not plan_path.is_absolute():
        plan_path = vault_root / plan_path
    try:
        result = build_from_plan(_load_plan_file(plan_path), vault_root=vault_root, write=args.write)
    except (AcquisitionValidationError, AcquisitionBuildError) as exc:
        print(f"acquisition build failed: {exc}")
        return 1

    print(json.dumps({
        "plan_id": result.plan_id,
        "source_packet_paths": result.source_packet_paths,
        "normalized_source_pack_path": result.normalized_source_pack_path,
        "briefing_ready_input_set_path": result.briefing_ready_input_set_path,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
