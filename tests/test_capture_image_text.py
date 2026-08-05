from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from runtime.capture.visual_capture.image_text import (
    build_local_image_text_status,
    capture_local_image_text,
    extract_local_image_text,
)
from runtime.capture.visual_capture.local_image_text_engine import render_pixel_text_png


def _write_test_image(tmp_path: Path, text: tuple[str, ...] = ("CORE IMAGE TEXT",)) -> Path:
    image_bytes, _width, _height = render_pixel_text_png(text, scale=8, min_width=520, min_height=160)
    image_path = tmp_path / "evidence" / "capture.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image_bytes)
    return image_path


def test_core_local_image_text_extracts_explicit_png(tmp_path: Path) -> None:
    image_path = _write_test_image(tmp_path, ("CORE IMAGE TEXT", "LOCAL ONLY"))

    result = extract_local_image_text(image_path)

    assert result.text == "CORE IMAGE TEXT\nLOCAL ONLY"
    assert result.engine_id == "chaseos-builtin-local-image-text"
    assert result.text_sha256
    assert result.image_sha256


def test_core_capture_local_image_text_writes_quarantine_only(tmp_path: Path) -> None:
    image_path = _write_test_image(tmp_path, ("CAPTURE TO MARKDOWN",))

    result = capture_local_image_text(
        image_path,
        vault_root=tmp_path,
        title="Image text proof",
        project_hint="chaseos-core",
    )

    content_path = Path(result["content_path"])
    sidecar_path = Path(result["sidecar_path"])
    assert content_path.read_text(encoding="utf-8") == "CAPTURE TO MARKDOWN"
    assert "03_INPUTS/00_QUARANTINE" in content_path.as_posix()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["capture_method"] == "local_image_text"
    assert sidecar["source_platform"] == "local-image-text"
    authority = sidecar["extra_metadata"]["authority"]
    assert authority["cloud_ocr_allowed"] is False
    assert authority["provider_call_allowed"] is False
    assert authority["canonical_promotion_allowed"] is False
    assert authority["sic_ingestion_allowed_at_capture_time"] is False


def test_core_capture_image_text_status_is_local_only() -> None:
    status = build_local_image_text_status()

    assert status["engine"]["available"] is True
    assert status["policy"]["cloud_ocr_allowed"] is False
    assert status["policy"]["provider_call_allowed"] is False
    assert status["policy"]["writes_only_through_quarantine_capture"] is True


def test_core_cli_capture_image_text_json(tmp_path: Path) -> None:
    image_path = _write_test_image(tmp_path, ("CLI IMAGE TEXT",))

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "runtime.cli.core_main",
            "capture",
            "image-text",
            str(image_path),
            "--vault-root",
            str(tmp_path),
            "--title",
            "CLI image proof",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "capture.image_text"
    assert payload["result"]["local_image_text"]["text"] == "CLI IMAGE TEXT"
    assert Path(payload["result"]["content_path"]).is_file()
