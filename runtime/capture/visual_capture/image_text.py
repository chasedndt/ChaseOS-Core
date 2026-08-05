"""Local image text capture support for ChaseOS Core.

This module keeps image-to-text handling optional, local-only, and explicit:
- only caller-provided local PNG paths are read;
- no cloud OCR/provider calls are made;
- extracted text is still written only through the normal quarantine capture path;
- Capture does not promote to SIC or canonical knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

from runtime.capture.content_packet import ContentPacket
from runtime.capture.capture import capture_content
from runtime.capture.visual_capture.local_image_text_engine import (
    ENGINE_ID,
    extract_text_from_pixel_image,
    local_image_text_engine_command,
)

LOCAL_IMAGE_TEXT_POLICY_ID = "capture_to_markdown.local_image_text.v1"
ALLOWED_IMAGE_SUFFIXES = {".png"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class LocalImageTextCaptureError(ValueError):
    """Raised when local image text extraction or capture cannot continue."""


@dataclass(frozen=True)
class LocalImageTextResult:
    text: str
    text_sha256: str
    text_char_count: int
    image_path: str
    image_sha256: str
    image_size_bytes: int
    engine_id: str = ENGINE_ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "text_extracted",
            "engine": {
                "engine_id": self.engine_id,
                "protocol": "in_process_png_pixel_text",
                "command": local_image_text_engine_command(),
                "provider_call_allowed": False,
                "cloud_ocr_allowed": False,
            },
            "text": self.text,
            "text_sha256": self.text_sha256,
            "text_char_count": self.text_char_count,
            "image": {
                "path": self.image_path,
                "sha256": self.image_sha256,
                "size_bytes": self.image_size_bytes,
            },
            "policy": local_image_text_policy(),
        }


def local_image_text_policy() -> dict[str, Any]:
    return {
        "policy_id": LOCAL_IMAGE_TEXT_POLICY_ID,
        "status": "allowed_optional_local_core_lane",
        "requires_explicit_local_image_path": True,
        "supported_extensions": sorted(ALLOWED_IMAGE_SUFFIXES),
        "writes_on_preview": False,
        "writes_only_through_quarantine_capture": True,
        "cloud_ocr_allowed": False,
        "provider_call_allowed": False,
        "ambient_screen_capture_allowed": False,
        "active_window_capture_allowed": False,
        "browser_profile_access_allowed": False,
        "canonical_promotion_allowed": False,
        "sic_ingestion_allowed_at_capture_time": False,
    }


def build_local_image_text_status() -> dict[str, Any]:
    engine_path = Path(local_image_text_engine_command()[-1])
    return {
        "ok": True,
        "surface": "capture_to_markdown_local_image_text",
        "policy": local_image_text_policy(),
        "engine": {
            "available": engine_path.is_file(),
            "engine_id": ENGINE_ID,
            "protocol": "in_process_png_pixel_text",
            "path": str(engine_path),
            "cloud_ocr_allowed": False,
            "provider_call_allowed": False,
        },
        "authority": {
            "reads_explicit_local_image_only": True,
            "writes_raw_quarantine_markdown_only_when_capture_command_invoked": True,
            "writes_canonical_knowledge": False,
            "starts_browser": False,
            "captures_screen": False,
            "calls_external_provider": False,
        },
    }


def extract_local_image_text(image_path: str | Path) -> LocalImageTextResult:
    path = Path(image_path).expanduser().resolve()
    _validate_image_path(path)
    data = path.read_bytes()
    text = _normalize_text(extract_text_from_pixel_image(path))
    if not text:
        raise LocalImageTextCaptureError("Local image text engine returned no text.")
    return LocalImageTextResult(
        text=text,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text_char_count=len(text),
        image_path=str(path),
        image_sha256=hashlib.sha256(data).hexdigest(),
        image_size_bytes=len(data),
    )


def capture_local_image_text(
    image_path: str | Path,
    *,
    vault_root: str | Path,
    title: str,
    input_class: str = "source",
    source_platform: str = "local-image-text",
    domain_hint: str | None = None,
    project_hint: str | None = None,
    topic_hint: str | None = None,
    origin_kind: str | None = None,
) -> dict[str, Any]:
    extraction = extract_local_image_text(image_path)
    packet = ContentPacket(
        content=extraction.text,
        input_class=input_class,
        source_platform=source_platform,
        title=title,
        original_name=Path(extraction.image_path).name,
        original_path_or_uri=extraction.image_path,
        detected_mime="image/png; extracted-text=utf-8",
        domain_hint=domain_hint,
        project_hint=project_hint,
        topic_hint=topic_hint,
        origin_kind=origin_kind,
        capture_method="local_image_text",
        extra_metadata={
            "local_image_text": extraction.to_dict(),
            "authority": {
                "cloud_ocr_allowed": False,
                "provider_call_allowed": False,
                "canonical_promotion_allowed": False,
                "sic_ingestion_allowed_at_capture_time": False,
            },
        },
    )
    result = capture_content(packet, Path(vault_root))
    result["local_image_text"] = extraction.to_dict()
    return result


def _validate_image_path(path: Path) -> None:
    if not path.exists():
        raise LocalImageTextCaptureError(f"Image path does not exist: {path}")
    if not path.is_file():
        raise LocalImageTextCaptureError(f"Image path is not a file: {path}")
    if path.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        raise LocalImageTextCaptureError(
            "Core local image text currently supports explicit PNG files only."
        )
    size = path.stat().st_size
    if size <= 32:
        raise LocalImageTextCaptureError("Image file is too small to be valid PNG evidence.")
    if size > MAX_IMAGE_BYTES:
        raise LocalImageTextCaptureError(
            f"Image file exceeds {MAX_IMAGE_BYTES:,} byte local-image-text limit."
        )
    signature = path.read_bytes()[:8]
    if signature != b"\x89PNG\r\n\x1a\n":
        raise LocalImageTextCaptureError("Image file does not have a PNG signature.")


def _normalize_text(value: str) -> str:
    lines = [line.rstrip() for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line.strip()).strip()
