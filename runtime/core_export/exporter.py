"""Dry-run exporter for a Git-safe ChaseOS Core tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import shlex
import shutil

from runtime.core_export.manifest import CoreExportManifestError, load_manifest
from runtime.core_export.sanitizers import CoreExportSanitizerError, apply_sanitizer
from runtime.core_export.scanner import scan_candidates, scan_text_file


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _path_has_traversal(path_text: str) -> bool:
    path = Path(path_text)
    return path.is_absolute() or any(part == ".." for part in path.parts)


def _matches_exclude(relative_path: str, excludes: list[str]) -> bool:
    normalized = relative_path.replace("\\", "/")
    for raw in excludes:
        pattern = str(raw).replace("\\", "/")
        if pattern.endswith("/"):
            if normalized == pattern.rstrip("/") or normalized.startswith(pattern):
                return True
        elif normalized == pattern:
            return True
    return False


def _read_text(path: Path, *, label: str) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        return None, f"candidate is not UTF-8 text: {label}"


def _resolve_template(source_root: Path, template: str) -> tuple[Path | None, str | None]:
    if _path_has_traversal(template):
        return None, f"template path is unsafe: {template}"
    template_path = (source_root / template).resolve()
    if not _is_relative_to(template_path, source_root.resolve()):
        return None, f"template path escapes source root: {template}"
    if not template_path.exists() or not template_path.is_file():
        return None, f"template file missing: {template}"
    return template_path, None


def _attach_rendered_preview(candidate: dict[str, Any], item: dict[str, Any], source_root: Path) -> str | None:
    mode = str(candidate["mode"])
    if mode == "copy":
        return None
    if mode == "sanitized_rewrite":
        sanitizer = str(item.get("sanitizer") or "")
        if not sanitizer:
            return "sanitized_rewrite requires sanitizer"
        text, error = _read_text(candidate["source_path"], label=str(candidate["source"]))
        if error:
            return error
        try:
            preview_text = apply_sanitizer(sanitizer, text or "")
        except CoreExportSanitizerError as exc:
            return str(exc)
        candidate["sanitizer"] = sanitizer
        candidate["rewrite_applied"] = preview_text != text
    elif mode == "core_template":
        template = str(item.get("template") or "")
        if not template:
            return "core_template requires template"
        template_path, error = _resolve_template(source_root, template)
        if error:
            return error
        preview_text, error = _read_text(template_path or source_root, label=template)
        if error:
            return error
        candidate["template"] = template
        candidate["rewrite_applied"] = True
    else:
        return f"unsupported core export candidate mode: {mode}"

    candidate["preview_text"] = preview_text or ""
    candidate["preview_sha256"] = hashlib.sha256((preview_text or "").encode("utf-8")).hexdigest()
    candidate["preview_bytes"] = len((preview_text or "").encode("utf-8"))
    return None


def _build_candidates(source_root: Path, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    candidates: list[dict[str, Any]] = []
    issues: list[str] = []
    excludes = [str(item) for item in manifest.get("exclude_always") or []]
    for item in manifest.get("include") or []:
        source = str(item["source"])
        target = str(item["target"])
        mode = str(item.get("mode") or "copy")
        if _path_has_traversal(source) or _path_has_traversal(target):
            issues.append(f"manifest include uses unsafe path traversal or absolute path: {source} -> {target}")
            continue
        if _matches_exclude(source, excludes) or _matches_exclude(target, excludes):
            issues.append(f"manifest include is blocked by exclude_always: {source} -> {target}")
            continue
        source_path = (source_root / source).resolve()
        if not _is_relative_to(source_path, source_root.resolve()):
            issues.append(f"candidate source escapes source root: {source}")
            continue
        if not source_path.exists():
            issues.append(f"candidate source missing: {source}")
            continue
        candidate = {
            "source": source,
            "target": target,
            "mode": mode,
            "source_path": source_path,
            "would_write": True,
        }
        render_error = _attach_rendered_preview(candidate, item, source_root)
        if render_error:
            issues.append(render_error)
        candidates.append(candidate)
    return candidates, issues


def _safe_report_dir(report_dir: str | Path, *, source_root: str | Path, target_root: str | Path) -> Path:
    source = Path(source_root).expanduser().resolve()
    target = Path(target_root).expanduser().resolve()
    resolved = Path(report_dir).expanduser().resolve()
    allowed_base = (source / "core_export" / "reports").resolve()
    if not _is_relative_to(resolved, allowed_base):
        raise ValueError(f"core export report dir must be under {allowed_base}")
    if resolved == target or _is_relative_to(resolved, target):
        raise ValueError("core export report dir must not be inside export target")
    return resolved


def _preview_output_path(report_dir: Path, target: str) -> Path:
    if _path_has_traversal(target):
        raise ValueError(f"candidate target is unsafe for preview artifact: {target}")
    preview_path = (report_dir / "previews" / target).resolve()
    if not _is_relative_to(preview_path, (report_dir / "previews").resolve()):
        raise ValueError(f"candidate target escapes preview artifact dir: {target}")
    return preview_path


def _candidate_preview_text(report: dict[str, Any], candidate: dict[str, Any]) -> str:
    preview_text = candidate.get("preview_text")
    if isinstance(preview_text, str):
        return preview_text
    source_root = Path(str(report["source_root"]))
    source_path = source_root / str(candidate["source"])
    text, error = _read_text(source_path, label=str(candidate["source"]))
    if error:
        raise ValueError(error)
    return text or ""


def write_dry_run_review_artifacts(report: dict[str, Any], report_dir: str | Path) -> dict[str, Any]:
    """Write local-only dry-run review artifacts without touching the export target."""

    if not report.get("dry_run") or report.get("writes_performed"):
        raise ValueError("review artifacts can only be written from a no-write dry-run report")
    if not report.get("ok"):
        raise ValueError("review artifacts require a scanner-clean dry-run report")

    resolved_report_dir = _safe_report_dir(
        report_dir,
        source_root=str(report["source_root"]),
        target_root=str(report["target_root"]),
    )
    resolved_report_dir.mkdir(parents=True, exist_ok=True)

    preview_files: list[str] = []
    rendered_preview_metadata: dict[str, dict[str, Any]] = {}
    for candidate in report.get("candidates") or []:
        target = str(candidate["target"])
        preview_path = _preview_output_path(resolved_report_dir, target)
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_text = _candidate_preview_text(report, candidate)
        preview_path.write_text(preview_text, encoding="utf-8")
        preview_files.append(str(preview_path))
        rendered_preview_metadata[target] = {
            "preview_sha256": hashlib.sha256(preview_text.encode("utf-8")).hexdigest(),
            "preview_bytes": len(preview_text.encode("utf-8")),
        }

    stripped_candidates: list[dict[str, Any]] = []
    for candidate in report.get("candidates") or []:
        stripped = {key: value for key, value in candidate.items() if key != "preview_text"}
        stripped.update(rendered_preview_metadata.get(str(candidate.get("target")), {}))
        stripped_candidates.append(stripped)

    review_payload = {
        **report,
        "candidates": stripped_candidates,
        "preview_text_omitted": True,
    }
    report_json = resolved_report_dir / "core-export-dry-run-report.json"
    report_json.write_text(json.dumps(review_payload, indent=2), encoding="utf-8")

    return {
        "report_dir": str(resolved_report_dir),
        "report_json": str(report_json),
        "preview_files": preview_files,
        "preview_count": len(preview_files),
    }


def _manual_review_summary(manual_review_path: str | Path | None, *, report_dir: str | Path) -> dict[str, Any]:
    path = Path(manual_review_path).expanduser().resolve() if manual_review_path else (Path(report_dir).expanduser().resolve() / "manual-preview-review-pass2.md")
    if not path.is_file():
        return {
            "path": str(path),
            "exists": False,
            "verdict": "missing",
            "blocking_issues": ["manual review pass artifact is missing"],
        }

    text = path.read_text(encoding="utf-8")
    lowered = text.lower()
    if "overall verdict: pass" in lowered or "overall verdict: pass / ready" in lowered:
        verdict = "pass"
        blocking_issues: list[str] = []
    elif "overall verdict: blocked" in lowered or "blocked" in lowered:
        verdict = "blocked"
        blocking_issues = ["manual review verdict is blocked"]
    else:
        verdict = "unknown"
        blocking_issues = ["manual review artifact does not contain an explicit PASS verdict"]

    return {
        "path": str(path),
        "exists": True,
        "verdict": verdict,
        "blocking_issues": blocking_issues,
    }


def verify_dry_run_review_artifacts(
    *,
    source_root: str | Path,
    target_root: str | Path,
    manifest_path: str | Path,
    report_dir: str | Path,
    allow_existing_target: bool = False,
) -> dict[str, Any]:
    """Verify local-only review artifacts before any real export is considered."""

    source = Path(source_root).expanduser().resolve()
    target = Path(target_root).expanduser().resolve()
    manifest_file = Path(manifest_path).expanduser().resolve()
    blocking_issues: list[str] = []
    hash_mismatches: list[dict[str, Any]] = []
    missing_previews: list[str] = []
    preview_findings: list[dict[str, Any]] = []
    preview_files: list[str] = []

    try:
        resolved_report_dir = _safe_report_dir(report_dir, source_root=source, target_root=target)
    except ValueError as exc:
        resolved_report_dir = Path(report_dir).expanduser().resolve()
        blocking_issues.append(str(exc))

    report_json = resolved_report_dir / "core-export-dry-run-report.json"
    report_exists = report_json.is_file()
    report_payload: dict[str, Any] = {}
    if not report_exists:
        blocking_issues.append(f"dry-run review report missing: {report_json}")
    else:
        try:
            report_payload = json.loads(report_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            blocking_issues.append(f"dry-run review report is not valid JSON: {exc}")

    if target.exists() and not allow_existing_target:
        blocking_issues.append("export target exists; review artifact verification requires target absence")
    if report_payload:
        if not report_payload.get("dry_run") or report_payload.get("writes_performed"):
            blocking_issues.append("review report is not a no-write dry-run report")
        if Path(str(report_payload.get("source_root", ""))).resolve() != source:
            blocking_issues.append("review report source_root does not match requested source root")
        if Path(str(report_payload.get("target_root", ""))).resolve() != target:
            blocking_issues.append("review report target_root does not match requested target root")
        if Path(str(report_payload.get("manifest_path", ""))).resolve() != manifest_file:
            blocking_issues.append("review report manifest_path does not match requested manifest")

    for candidate in report_payload.get("candidates") or []:
        target_path = str(candidate.get("target", ""))
        if not target_path:
            blocking_issues.append("review report candidate missing target")
            continue
        try:
            preview_path = _preview_output_path(resolved_report_dir, target_path)
        except ValueError as exc:
            blocking_issues.append(str(exc))
            continue
        if not preview_path.is_file():
            missing_previews.append(target_path)
            continue
        preview_files.append(str(preview_path))
        preview_findings.extend(scan_text_file(preview_path, display_path=target_path))
        text = preview_path.read_text(encoding="utf-8")
        actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        actual_bytes = len(text.encode("utf-8"))
        expected_sha = candidate.get("preview_sha256")
        expected_bytes = candidate.get("preview_bytes")
        if expected_sha and expected_sha != actual_sha:
            hash_mismatches.append(
                {
                    "target": target_path,
                    "expected_sha256": expected_sha,
                    "actual_sha256": actual_sha,
                }
            )
        if expected_bytes is not None and expected_bytes != actual_bytes:
            hash_mismatches.append(
                {
                    "target": target_path,
                    "expected_bytes": expected_bytes,
                    "actual_bytes": actual_bytes,
                }
            )

    if missing_previews:
        blocking_issues.append("review preview artifacts are missing")
    if preview_findings:
        blocking_issues.append("privacy scanner found blocking preview content")
    if hash_mismatches:
        blocking_issues.append("review preview artifacts no longer match the recorded dry-run report")

    candidate_count = len(report_payload.get("candidates") or [])
    if report_payload and len(preview_files) != candidate_count:
        blocking_issues.append("review preview count does not match dry-run candidate count")

    return {
        "ok": not blocking_issues,
        "action": "core-export.verify-report",
        "source_root": str(source),
        "target_root": str(target),
        "manifest_path": str(manifest_file),
        "report_dir": str(resolved_report_dir),
        "report_json": str(report_json),
        "report_exists": report_exists,
        "target_exists": target.exists(),
        "candidate_count": candidate_count,
        "preview_count": len(preview_files),
        "preview_files": preview_files,
        "missing_previews": missing_previews,
        "hash_mismatches": hash_mismatches,
        "preview_scan": {
            "ok": not preview_findings,
            "blocking_findings": preview_findings,
            "blocking_count": len(preview_findings),
        },
        "blocking_issues": blocking_issues,
        "next_action": "manual review may continue; do not build/export yet" if not blocking_issues else "fix review artifact blockers before any export",
    }


def build_core_export_readiness(
    *,
    source_root: str | Path,
    target_root: str | Path,
    manifest_path: str | Path,
    report_dir: str | Path,
    manual_review_path: str | Path | None = None,
    allow_existing_target: bool = False,
) -> dict[str, Any]:
    """Summarize non-mutating Core export readiness before any real export/Git step."""

    verifier = verify_dry_run_review_artifacts(
        source_root=source_root,
        target_root=target_root,
        manifest_path=manifest_path,
        report_dir=report_dir,
        allow_existing_target=allow_existing_target,
    )
    manual_review = _manual_review_summary(manual_review_path, report_dir=report_dir)
    blocking_issues: list[str] = []
    if not verifier.get("ok"):
        blocking_issues.append("dry-run review verifier is not clean")
    blocking_issues.extend(manual_review.get("blocking_issues") or [])
    if verifier.get("target_exists") and not allow_existing_target:
        blocking_issues.append("export target already exists")

    ready = not blocking_issues
    return {
        "ok": ready,
        "action": "core-export.readiness",
        "readiness_status": "ready_for_gate_governed_export" if ready else "blocked",
        "source_root": verifier.get("source_root"),
        "target_root": verifier.get("target_root"),
        "manifest_path": verifier.get("manifest_path"),
        "report_dir": verifier.get("report_dir"),
        "candidate_count": verifier.get("candidate_count", 0),
        "preview_count": verifier.get("preview_count", 0),
        "target_exists": verifier.get("target_exists", False),
        "update_existing_target": allow_existing_target,
        "writes_performed": False,
        "git_init_allowed": False,
        "real_export_allowed_without_gate": False,
        "manual_review": manual_review,
        "verifier": {
            "ok": verifier.get("ok", False),
            "report_exists": verifier.get("report_exists", False),
            "preview_scan": verifier.get("preview_scan"),
            "hash_mismatches": verifier.get("hash_mismatches", []),
            "blocking_issues": verifier.get("blocking_issues", []),
        },
        "blocking_issues": blocking_issues,
        "next_action": "operator may approve a separate Gate-governed real-export pass" if ready else "fix readiness blockers before export/Git initialization",
    }


def build_core_export_request(
    *,
    source_root: str | Path,
    target_root: str | Path,
    manifest_path: str | Path,
    report_dir: str | Path,
    manual_review_path: str | Path | None = None,
    requested_by: str | None = None,
) -> dict[str, Any]:
    """Build a non-mutating approval packet for a later Gate-governed Core export."""

    readiness = build_core_export_readiness(
        source_root=source_root,
        target_root=target_root,
        manifest_path=manifest_path,
        report_dir=report_dir,
        manual_review_path=manual_review_path,
    )
    blocking_issues: list[str] = []
    if not readiness.get("ok"):
        blocking_issues.append("core export readiness gate is not clean")

    command_parts = [
        "python3",
        "chaseos.py",
        "core-export",
        "export",
        "--source-root",
        str(readiness.get("source_root") or Path(source_root).expanduser().resolve()),
        "--target",
        str(readiness.get("target_root") or Path(target_root).expanduser().resolve()),
        "--manifest",
        str(readiness.get("manifest_path") or Path(manifest_path).expanduser().resolve()),
        "--report-dir",
        str(readiness.get("report_dir") or Path(report_dir).expanduser().resolve()),
    ]
    manual_path = readiness.get("manual_review", {}).get("path") or manual_review_path
    if manual_path:
        command_parts.extend(["--manual-review", str(manual_path)])
    command_parts.extend(["--operator-approval-ref", "<APPROVAL_REF>", "--confirm", "--json"])

    ready = not blocking_issues
    command_preview = shlex.join(command_parts)
    approval_scope = "create local Core export tree only; no Git init, commit, push, publication, or canonical promotion"
    approval_packet = {
        "operation": "core_export.real_export",
        "scope": approval_scope,
        "requested_by": requested_by,
        "source_root": readiness.get("source_root"),
        "target_root": readiness.get("target_root"),
        "manifest_path": readiness.get("manifest_path"),
        "report_dir": readiness.get("report_dir"),
        "manual_review_path": manual_path,
        "readiness_status": readiness.get("readiness_status"),
        "candidate_count": readiness.get("candidate_count", 0),
        "preview_count": readiness.get("preview_count", 0),
        "approval_apply_command": command_preview,
        "requires_operator_approval_ref": True,
        "requires_confirm_flag": True,
        "git_initialization_included": False,
        "publication_included": False,
    }
    return {
        "ok": ready,
        "action": "core-export.request-export",
        "request_status": "approval_request_ready" if ready else "blocked",
        "approval_request_ready": ready,
        "approval_required": ready,
        "approval_request_written": False,
        "writes_performed": False,
        "target_created": False,
        "git_initialized": False,
        "publication_performed": False,
        "requested_by": requested_by,
        "approval_operation": "core_export.real_export",
        "approval_scope": approval_scope,
        "approval_packet": approval_packet,
        "source_root": readiness.get("source_root"),
        "target_root": readiness.get("target_root"),
        "manifest_path": readiness.get("manifest_path"),
        "report_dir": readiness.get("report_dir"),
        "manual_review": readiness.get("manual_review"),
        "readiness_status": readiness.get("readiness_status"),
        "candidate_count": readiness.get("candidate_count", 0),
        "preview_count": readiness.get("preview_count", 0),
        "operator_command_preview": command_preview,
        "blocking_issues": blocking_issues,
        "readiness": readiness,
        "next_action": "operator may approve and supply an approval reference for local export only" if ready else "fix readiness blockers before requesting export approval",
    }


def build_core_export_next_step(
    *,
    source_root: str | Path,
    target_root: str | Path,
    manifest_path: str | Path,
    report_dir: str | Path,
    manual_review_path: str | Path | None = None,
    requested_by: str | None = None,
) -> dict[str, Any]:
    """Report the next manual decision point in the Git-safe Core export lane."""

    target = Path(target_root).expanduser().resolve()
    target_exists = target.exists()
    readiness = build_core_export_readiness(
        source_root=source_root,
        target_root=target_root,
        manifest_path=manifest_path,
        report_dir=report_dir,
        manual_review_path=manual_review_path,
    )

    base: dict[str, Any] = {
        "action": "core-export.next-step",
        "source_root": readiness.get("source_root"),
        "target_root": str(target),
        "manifest_path": readiness.get("manifest_path"),
        "report_dir": readiness.get("report_dir"),
        "manual_review": readiness.get("manual_review"),
        "readiness_status": readiness.get("readiness_status"),
        "target_exists": target_exists,
        "writes_performed": False,
        "target_created": False,
        "git_initialized": False,
        "publication_performed": False,
        "requested_by": requested_by,
        "readiness": readiness,
        "export_verification": None,
        "export_verification_status": None,
        "operator_command_preview": None,
    }

    if not target_exists:
        request = build_core_export_request(
            source_root=source_root,
            target_root=target_root,
            manifest_path=manifest_path,
            report_dir=report_dir,
            manual_review_path=manual_review_path,
            requested_by=requested_by,
        )
        ready = bool(request.get("ok"))
        return {
            **base,
            "ok": ready,
            "stage": "ready_for_manual_export_approval" if ready else "blocked_before_manual_export_approval",
            "manual_step_required": ready,
            "manual_step_kind": "approve_local_export" if ready else "none",
            "approval_operation": "core_export.real_export" if ready else None,
            "approval_scope": request.get("approval_scope") if ready else None,
            "operator_command_preview": request.get("operator_command_preview") if ready else None,
            "blocking_issues": request.get("blocking_issues", []),
            "next_action": (
                "Manual step required: approve local Core export only, supply an approval reference, then run the previewed export command. Git init remains separate."
                if ready
                else "No manual approval is ready yet; fix readiness blockers first."
            ),
            "request": request,
        }

    verification = verify_exported_core_tree(
        source_root=source_root,
        target_root=target_root,
        manifest_path=manifest_path,
        report_dir=report_dir,
        manual_review_path=manual_review_path,
    )
    verified = bool(verification.get("ok"))
    return {
        **base,
        "ok": verified,
        "stage": "ready_for_manual_git_init_approval" if verified else "blocked_after_local_export",
        "manual_step_required": verified,
        "manual_step_kind": "approve_git_init" if verified else "none",
        "approval_operation": "core_export.git_init" if verified else None,
        "approval_scope": (
            "initialize Git inside the verified local Core export tree only; no commit, push, publication, or canonical promotion"
            if verified
            else None
        ),
        "export_verification": verification,
        "export_verification_status": verification.get("verification_status"),
        "blocking_issues": verification.get("blocking_issues", []),
        "next_action": (
            "Manual step required: decide whether to approve Git initialization as a separate operation. No Git command is auto-run by this surface."
            if verified
            else "Do not request Git init; fix export verification blockers first."
        ),
    }


def build_core_export_public_repo_gates(
    *,
    source_root: str | Path,
    target_root: str | Path,
    manifest_path: str | Path,
    report_dir: str | Path,
    manual_review_path: str | Path | None = None,
    requested_by: str | None = None,
) -> dict[str, Any]:
    """Expose read-only public Core repository gates without performing Git/publication work."""

    target = Path(target_root).expanduser().resolve()
    next_step = build_core_export_next_step(
        source_root=source_root,
        target_root=target,
        manifest_path=manifest_path,
        report_dir=report_dir,
        manual_review_path=manual_review_path,
        requested_by=requested_by,
    )
    target_exists = target.exists()
    target_is_dir = target.is_dir()
    git_dir_exists = (target / ".git").exists()
    license_exists = any((target / name).is_file() for name in ("LICENSE", "LICENSE.md", "COPYING")) if target_is_dir else False
    gitignore_exists = (target / ".gitignore").is_file() if target_is_dir else False
    export_verified = next_step.get("stage") == "ready_for_manual_git_init_approval"

    if git_dir_exists:
        lane_status = "blocked_unapproved_git_detected"
    elif export_verified:
        lane_status = "blocked_at_git_init_approval"
    elif target_exists:
        lane_status = "blocked_after_local_export_verification"
    else:
        lane_status = "blocked_before_local_export"

    gates = [
        {
            "id": "local_export_verified",
            "label": "Local Core export exists and verifies clean",
            "status": "ready" if export_verified else "blocked",
            "authority_operation": "core_export.real_export",
            "runtime_may_execute_now": False,
            "evidence": {
                "target_exists": target_exists,
                "export_verification_status": next_step.get("export_verification_status"),
            },
        },
        {
            "id": "license_choice",
            "label": "Public license choice recorded for Core",
            "status": "artifact_present_unapproved" if license_exists else "pending_operator_decision",
            "authority_operation": "core_export.license_choice",
            "runtime_may_execute_now": False,
            "evidence": {"license_file_present": license_exists},
        },
        {
            "id": "public_gitignore",
            "label": "Public .gitignore reviewed for generated Core repo",
            "status": "artifact_present_unapproved" if gitignore_exists else "pending_operator_decision",
            "authority_operation": "core_export.public_gitignore",
            "runtime_may_execute_now": False,
            "evidence": {"gitignore_file_present": gitignore_exists},
        },
        {
            "id": "git_initialization",
            "label": "Initialize Git in the verified local Core export target",
            "status": "policy_violation_existing_git" if git_dir_exists else ("pending_operator_approval" if export_verified else "blocked"),
            "authority_operation": "core_export.git_init",
            "runtime_may_execute_now": False,
            "evidence": {"git_dir_present": git_dir_exists},
        },
        {
            "id": "initial_commit",
            "label": "Create first public Core commit",
            "status": "blocked_separate_gate",
            "authority_operation": "core_export.initial_commit",
            "runtime_may_execute_now": False,
            "evidence": {"requires_git_initialization_first": True},
        },
        {
            "id": "remote_creation",
            "label": "Create/add public repository remote",
            "status": "blocked_separate_gate",
            "authority_operation": "core_export.remote_creation",
            "runtime_may_execute_now": False,
            "evidence": {"requires_initial_commit_first": True},
        },
        {
            "id": "push_publication",
            "label": "Push/publish Core repository",
            "status": "blocked_separate_gate",
            "authority_operation": "core_export.push_publication",
            "runtime_may_execute_now": False,
            "evidence": {"requires_remote_creation_first": True},
        },
        {
            "id": "canonical_promotion",
            "label": "Promote public Core repo status into canonical ChaseOS truth",
            "status": "blocked_separate_gate",
            "authority_operation": "core_export.canonical_promotion",
            "runtime_may_execute_now": False,
            "evidence": {"requires_publication_review_first": True},
        },
    ]

    return {
        "ok": True,
        "action": "core-export.public-repo-gates",
        "source_root": next_step.get("source_root"),
        "target_root": str(target),
        "manifest_path": next_step.get("manifest_path"),
        "report_dir": next_step.get("report_dir"),
        "manual_review": next_step.get("manual_review"),
        "requested_by": requested_by,
        "public_repo_gate_status": lane_status,
        "read_only": True,
        "writes_performed": False,
        "target_created": False,
        "git_initialized": False,
        "commit_performed": False,
        "remote_created": False,
        "publication_performed": False,
        "canonical_promotion_performed": False,
        "approval_consumed": False,
        "authority_flags": {
            "license_choice_allowed": False,
            "public_gitignore_write_allowed": False,
            "git_init_allowed": False,
            "commit_allowed": False,
            "remote_creation_allowed": False,
            "push_publication_allowed": False,
            "canonical_promotion_allowed": False,
        },
        "gates": gates,
        "next_step": next_step,
        "next_action": (
            "Request a separate operator/Gate decision for Git initialization only; license, .gitignore, commit, remote, push/publication, and canonical promotion remain separate gates."
            if export_verified and not git_dir_exists
            else "Resolve local export/readiness blockers before any public repository gate can advance."
        ),
    }


def export_verified_core_tree(
    *,
    source_root: str | Path,
    target_root: str | Path,
    manifest_path: str | Path,
    report_dir: str | Path,
    manual_review_path: str | Path | None = None,
    operator_approval_ref: str | None = None,
    confirm: bool = False,
    update_existing: bool = False,
) -> dict[str, Any]:
    """Create a local Core export tree from verified preview artifacts, without Git init."""

    target = Path(target_root).expanduser().resolve()
    blocking_issues: list[str] = []
    written_files: list[str] = []

    readiness = build_core_export_readiness(
        source_root=source_root,
        target_root=target_root,
        manifest_path=manifest_path,
        report_dir=report_dir,
        manual_review_path=manual_review_path,
        allow_existing_target=update_existing,
    )
    if not readiness.get("ok"):
        blocking_issues.append("core export readiness gate is not clean")
    if not confirm:
        blocking_issues.append("real Core export requires --confirm")
    if not operator_approval_ref:
        blocking_issues.append("real Core export requires an operator approval reference")
    existing_status_payload: dict[str, Any] = {}
    if target.exists() and not update_existing:
        blocking_issues.append("export target already exists; refusing to overwrite")
    elif target.exists():
        if not target.is_dir():
            blocking_issues.append("export target exists but is not a directory")
        if (target / ".git").exists():
            blocking_issues.append("export target contains .git; refusing update-existing export")
        status_path = target / "CORE_EXPORT_STATUS.json"
        if not status_path.is_file():
            blocking_issues.append("update-existing export requires CORE_EXPORT_STATUS.json from a prior Core export")
        else:
            try:
                existing_status_payload = json.loads(status_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                blocking_issues.append(f"existing CORE_EXPORT_STATUS.json is not valid JSON: {exc}")
            if existing_status_payload:
                if existing_status_payload.get("git_initialized") is not False:
                    blocking_issues.append("existing export status does not preserve git_initialized=false")
                if existing_status_payload.get("publication_performed") is not False:
                    blocking_issues.append("existing export status does not preserve publication_performed=false")

    if blocking_issues:
        return {
            "ok": False,
            "action": "core-export.export",
            "source_root": readiness.get("source_root"),
            "target_root": str(target),
            "manifest_path": readiness.get("manifest_path"),
            "report_dir": readiness.get("report_dir"),
            "operator_approval_ref": operator_approval_ref,
            "readiness_status": readiness.get("readiness_status"),
            "writes_performed": False,
            "target_created": False,
            "target_updated": False,
            "update_existing": update_existing,
            "git_initialized": False,
            "written_count": 0,
            "written_files": [],
            "blocking_issues": blocking_issues,
            "readiness": readiness,
        }

    report_json = Path(str(readiness["report_dir"])) / "core-export-dry-run-report.json"
    report_payload = json.loads(report_json.read_text(encoding="utf-8"))

    target_preexisting = target.exists()
    if target_preexisting:
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.mkdir(parents=False, exist_ok=False)
    try:
        for candidate in report_payload.get("candidates") or []:
            relative_target = str(candidate.get("target") or "")
            if _path_has_traversal(relative_target):
                raise ValueError(f"candidate target is unsafe for export: {relative_target}")
            preview_path = _preview_output_path(Path(str(readiness["report_dir"])), relative_target)
            destination = (target / relative_target).resolve()
            if not _is_relative_to(destination, target):
                raise ValueError(f"candidate target escapes export target: {relative_target}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(preview_path, destination)
            written_files.append(str(destination))

        export_record = {
            "generated_by": "chaseos core-export export",
            "operator_approval_ref": operator_approval_ref,
            "source_root": readiness.get("source_root"),
            "manifest_path": readiness.get("manifest_path"),
            "report_dir": readiness.get("report_dir"),
            "manual_review": readiness.get("manual_review"),
            "candidate_count": readiness.get("candidate_count"),
            "preview_count": readiness.get("preview_count"),
            "update_existing": update_existing,
            "previous_export_status": existing_status_payload if update_existing else None,
            "git_initialized": False,
            "publication_performed": False,
        }
        export_record_path = target / "CORE_EXPORT_STATUS.json"
        export_record_path.write_text(json.dumps(export_record, indent=2), encoding="utf-8")
        written_files.append(str(export_record_path))
    except Exception:
        # Fail closed for newly-created exports. For update-existing exports,
        # preserve the prior target instead of deleting an operator-visible tree.
        if not target_preexisting:
            shutil.rmtree(target, ignore_errors=True)
        raise

    return {
        "ok": True,
        "action": "core-export.export",
        "source_root": readiness.get("source_root"),
        "target_root": str(target),
        "manifest_path": readiness.get("manifest_path"),
        "report_dir": readiness.get("report_dir"),
        "operator_approval_ref": operator_approval_ref,
        "readiness_status": readiness.get("readiness_status"),
        "writes_performed": True,
        "target_created": not target_preexisting,
        "target_updated": target_preexisting,
        "update_existing": update_existing,
        "git_initialized": False,
        "publication_performed": False,
        "written_count": len(written_files),
        "written_files": written_files,
        "blocking_issues": [],
        "readiness": readiness,
        "next_action": "inspect exported tree; Git initialization remains a separate approval step",
    }


def verify_exported_core_tree(
    *,
    source_root: str | Path,
    target_root: str | Path,
    manifest_path: str | Path,
    report_dir: str | Path,
    manual_review_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify an exported Core tree without mutating it or initializing Git."""

    target = Path(target_root).expanduser().resolve()
    blocking_issues: list[str] = []
    missing_files: list[str] = []
    hash_mismatches: list[dict[str, Any]] = []
    verified_files: list[str] = []
    export_scan_findings: list[dict[str, Any]] = []
    status_payload: dict[str, Any] = {}

    status_path = target / "CORE_EXPORT_STATUS.json"
    readiness = build_core_export_readiness(
        source_root=source_root,
        target_root=target_root,
        manifest_path=manifest_path,
        report_dir=report_dir,
        manual_review_path=manual_review_path,
    )
    report_dir_path = Path(str(readiness.get("report_dir") or report_dir))
    report_json = report_dir_path / "core-export-dry-run-report.json"
    report_payload: dict[str, Any] = {}
    if report_json.is_file():
        try:
            report_payload = json.loads(report_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            blocking_issues.append(f"dry-run review report is not valid JSON: {exc}")
    else:
        blocking_issues.append(f"dry-run review report missing: {report_json}")

    if not target.exists():
        blocking_issues.append("export target is missing; run only after approved local export")
    elif not target.is_dir():
        blocking_issues.append("export target exists but is not a directory")

    git_dir = target / ".git"
    if git_dir.exists():
        blocking_issues.append("export target already contains .git; Git initialization is outside this verification step")

    if status_path.is_file():
        try:
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            blocking_issues.append(f"CORE_EXPORT_STATUS.json is not valid JSON: {exc}")
    else:
        blocking_issues.append("CORE_EXPORT_STATUS.json is missing")

    if status_payload:
        if status_payload.get("git_initialized") is not False:
            blocking_issues.append("CORE_EXPORT_STATUS.json does not preserve git_initialized=false")
        if status_payload.get("publication_performed") is not False:
            blocking_issues.append("CORE_EXPORT_STATUS.json does not preserve publication_performed=false")

    for candidate in report_payload.get("candidates") or []:
        relative_target = str(candidate.get("target") or "")
        if not relative_target:
            blocking_issues.append("dry-run report candidate missing target")
            continue
        if _path_has_traversal(relative_target):
            blocking_issues.append(f"candidate target is unsafe for export verification: {relative_target}")
            continue
        destination = (target / relative_target).resolve()
        if not _is_relative_to(destination, target):
            blocking_issues.append(f"candidate target escapes export target: {relative_target}")
            continue
        if not destination.is_file():
            missing_files.append(relative_target)
            continue
        text = destination.read_text(encoding="utf-8")
        actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        actual_bytes = len(text.encode("utf-8"))
        expected_sha = candidate.get("preview_sha256")
        expected_bytes = candidate.get("preview_bytes")
        if expected_sha and expected_sha != actual_sha:
            hash_mismatches.append(
                {"target": relative_target, "expected_sha256": expected_sha, "actual_sha256": actual_sha}
            )
        if expected_bytes is not None and expected_bytes != actual_bytes:
            hash_mismatches.append(
                {"target": relative_target, "expected_bytes": expected_bytes, "actual_bytes": actual_bytes}
            )
        verified_files.append(str(destination))
        export_scan_findings.extend(scan_text_file(destination, display_path=relative_target))

    candidate_count = len(report_payload.get("candidates") or [])
    if missing_files:
        blocking_issues.append("exported Core files are missing")
    if hash_mismatches:
        blocking_issues.append("exported Core files do not match verified preview hashes")
    if export_scan_findings:
        blocking_issues.append("privacy scanner found blocking exported content")
    if report_payload and len(verified_files) != candidate_count:
        blocking_issues.append("exported file count does not match dry-run candidate count")

    return {
        "ok": not blocking_issues,
        "action": "core-export.verify-export",
        "verification_status": "export_verified" if not blocking_issues else "blocked",
        "source_root": readiness.get("source_root"),
        "target_root": str(target),
        "manifest_path": readiness.get("manifest_path"),
        "report_dir": str(report_dir_path),
        "manual_review": readiness.get("manual_review"),
        "readiness_status": readiness.get("readiness_status"),
        "writes_performed": False,
        "target_exists": target.exists(),
        "status_file_exists": status_path.is_file(),
        "git_initialized": git_dir.exists(),
        "publication_performed": bool(status_payload.get("publication_performed", False)),
        "candidate_count": candidate_count,
        "verified_file_count": len(verified_files),
        "verified_files": verified_files,
        "missing_files": missing_files,
        "hash_mismatches": hash_mismatches,
        "export_scan": {
            "ok": not export_scan_findings,
            "blocking_findings": export_scan_findings,
            "blocking_count": len(export_scan_findings),
        },
        "status_payload": status_payload,
        "blocking_issues": blocking_issues,
        "next_action": "operator may consider a separate Git-init approval" if not blocking_issues else "fix export verification blockers before Git initialization",
    }


def build_dry_run_report(
    *,
    source_root: str | Path,
    target_root: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Return a fail-closed dry-run report without writing to the export target."""

    source = Path(source_root).expanduser().resolve()
    target = Path(target_root).expanduser().resolve()
    manifest_file = Path(manifest_path).expanduser().resolve()
    blocking_issues: list[str] = []

    if not source.exists() or not source.is_dir():
        blocking_issues.append(f"source root does not exist or is not a directory: {source}")
    if source == target:
        blocking_issues.append("export target must not equal source root")
    elif _is_relative_to(target, source):
        blocking_issues.append("export target must not be inside source root")

    manifest: dict[str, Any] = {}
    candidates: list[dict[str, Any]] = []
    scanner = {"ok": True, "blocking_findings": [], "blocking_count": 0}
    try:
        manifest = load_manifest(manifest_file)
    except CoreExportManifestError as exc:
        blocking_issues.append(str(exc))

    if manifest and source.exists():
        candidates, candidate_issues = _build_candidates(source, manifest)
        blocking_issues.extend(candidate_issues)
        scanner = scan_candidates(candidates)
        if not scanner["ok"]:
            blocking_issues.append("privacy scanner found blocking candidate content")

    public_candidates = [
        {key: value for key, value in candidate.items() if key != "source_path"}
        for candidate in candidates
    ]
    report = {
        "ok": not blocking_issues,
        "dry_run": True,
        "writes_performed": False,
        "source_root": str(source),
        "target_root": str(target),
        "target_exists": target.exists(),
        "would_create_target": not target.exists(),
        "manifest_path": str(manifest_file),
        "manifest_mode": manifest.get("mode") if manifest else None,
        "candidate_count": len(public_candidates),
        "candidates": public_candidates,
        "scanner": scanner,
        "blocking_issues": blocking_issues,
        "next_action": "fix blocking issues before build" if blocking_issues else "review dry-run report before any real export",
    }
    return report
