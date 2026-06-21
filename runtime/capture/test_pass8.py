"""
test_pass8.py — ChaseOS Phase 8 Pass 1 Test Suite
Connector / Capture Automation — end-to-end tests

Run:
    python -m runtime.capture.test_pass8

Tests:
    T01  ContentPacket: valid construction
    T02  ContentPacket: empty content raises ValueError
    T03  ContentPacket: invalid input_class raises ValueError
    T04  ContentPacket: captured_at defaults to UTC ISO timestamp
    T05  ContentPacket: knowledge_class journal → user-origin
    T06  ContentPacket: knowledge_class non-journal → source-derived
    T07  router: make_title_slug basic cases
    T08  router: make_title_slug truncation
    T09  router: make_title_slug empty → "untitled"
    T10  router: make_source_slug normalization
    T11  router: make_filename format YYYYMMDD-HHMMSS__class__source__slug.md
    T12  router: route_input_class returns correct subfolder path
    T13  router: route_input_class unknown class raises ValueError
    T14  router: resolve_unique_path no collision
    T15  router: resolve_unique_path collision → _2 suffix
    T16  intake_writer: write_intake creates content file
    T17  intake_writer: write_intake creates sidecar .meta.json
    T18  intake_writer: sidecar schema fields present and correct
    T19  intake_writer: sidecar content_sha256 matches content
    T20  intake_writer: sidecar promotion_status = quarantine
    T21  intake_writer: collision resolution (_2 suffix) in write_intake
    T22  cli_connector: capture_from_cli from file
    T23  cli_connector: capture_from_cli file not found raises FileNotFoundError
    T24  capture: capture_content end-to-end (full pipeline)
    T25  WORKED EXAMPLE: transcript from youtube — full pipeline, verified output
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import traceback
import uuid
from pathlib import Path

# ── Test runner ────────────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0
_ERRORS: list[str] = []


def _ok(name: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  PASS  {name}")


def _fail(name: str, reason: str) -> None:
    global _FAIL
    _FAIL += 1
    _ERRORS.append(f"{name}: {reason}")
    print(f"  FAIL  {name}: {reason}")


def _assert(cond: bool, name: str, msg: str = "") -> None:
    if cond:
        _ok(name)
    else:
        _fail(name, msg or "assertion failed")


def _run_test(label: str, fn) -> None:
    try:
        fn()
    except Exception as exc:
        _fail(label, f"EXCEPTION: {exc}\n{traceback.format_exc()}")


# ── Imports under test ─────────────────────────────────────────────────────────

from runtime.capture.content_packet import (
    ContentPacket,
    VALID_INPUT_CLASSES,
    INPUT_CLASS_JOURNAL,
    INPUT_CLASS_TRANSCRIPT,
    INPUT_CLASS_DIGEST,
    INPUT_CLASS_SOURCE,
    INPUT_CLASS_YOUTUBE_NOTE,
)
from runtime.capture.router import (
    make_title_slug,
    make_source_slug,
    make_filename,
    route_input_class,
    resolve_unique_path,
)
from runtime.capture.intake_writer import write_intake
from runtime.capture.connectors.cli_connector import capture_from_cli
from runtime.capture.capture import capture_content


# ── T01–T06: ContentPacket ─────────────────────────────────────────────────────

def test_t01():
    p = ContentPacket(
        content="Hello world",
        input_class="transcript",
        source_platform="youtube",
        title="Test Title",
    )
    _assert(p.content == "Hello world", "T01a")
    _assert(p.input_class == "transcript", "T01b")
    _assert(p.capture_method == "cli", "T01c")
    _assert(p.injection_scan == "not-scanned", "T01d")


def test_t02():
    try:
        ContentPacket(content="", input_class="transcript",
                      source_platform="youtube", title="T")
        _fail("T02", "Should have raised ValueError for empty content")
    except ValueError:
        _ok("T02")


def test_t03():
    try:
        ContentPacket(content="x", input_class="not_a_class",
                      source_platform="youtube", title="T")
        _fail("T03", "Should have raised ValueError for invalid input_class")
    except ValueError:
        _ok("T03")


def test_t04():
    p = ContentPacket(content="x", input_class="transcript",
                      source_platform="youtube", title="T")
    _assert(p.captured_at is not None, "T04a", "captured_at should default")
    _assert("T" in p.captured_at or "+" in p.captured_at or "Z" in p.captured_at,
            "T04b", f"captured_at not ISO-like: {p.captured_at}")


def test_t05():
    p = ContentPacket(content="x", input_class="journal",
                      source_platform="manual", title="T")
    _assert(p.knowledge_class == "user-origin", "T05")


def test_t06():
    for cls in ["transcript", "digest", "source", "youtube_note"]:
        p = ContentPacket(content="x", input_class=cls,
                          source_platform="youtube", title="T")
        _assert(p.knowledge_class == "source-derived", f"T06-{cls}")


# ── T07–T15: Router ────────────────────────────────────────────────────────────

def test_t07():
    _assert(make_title_slug("Market Microstructure Lecture") ==
            "market-microstructure-lecture", "T07a")
    _assert(make_title_slug("  Multi-Agent  Tool Use  ") ==
            "multi-agent-tool-use", "T07b")
    _assert(make_title_slug("Crypto Perps: Funding Rates Q1 2026") ==
            "crypto-perps-funding-rates-q1-2026", "T07c")


def test_t08():
    long_title = "A" * 60
    slug = make_title_slug(long_title, max_len=50)
    _assert(len(slug) <= 50, "T08a", f"slug too long: {len(slug)}")
    _assert(not slug.endswith("-"), "T08b", "slug ends with hyphen")


def test_t09():
    _assert(make_title_slug("") == "untitled", "T09a")
    _assert(make_title_slug("!!!") == "untitled", "T09b")


def test_t10():
    _assert(make_source_slug("YouTube") == "youtube", "T10a")
    _assert(make_source_slug("Perplexity AI") == "perplexity-ai", "T10b")
    _assert(make_source_slug("") == "unknown", "T10c")


def test_t11():
    filename = make_filename(
        input_class="transcript",
        source_platform="youtube",
        title="Order Flow and Market Microstructure",
        captured_at="2026-03-27T14:30:22+00:00",
    )
    _assert(filename.endswith(".md"), "T11a")
    parts = filename.replace(".md", "").split("__")
    _assert(len(parts) == 4, "T11b", f"Expected 4 parts, got {len(parts)}: {parts}")
    _assert(parts[0] == "20260327-143022", "T11c", f"timestamp wrong: {parts[0]}")
    _assert(parts[1] == "transcript", "T11d", f"class wrong: {parts[1]}")
    _assert(parts[2] == "youtube", "T11e", f"source wrong: {parts[2]}")
    _assert("order-flow" in parts[3], "T11f", f"slug wrong: {parts[3]}")


def test_t12():
    # Pass 2: routing now targets 03_INPUTS/00_QUARANTINE/[class]/
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        path = route_input_class("transcript", vault)
        _assert(path == vault / "03_INPUTS" / "00_QUARANTINE" / "Transcript-Raw", "T12a")
        path2 = route_input_class("youtube_note", vault)
        _assert(path2 == vault / "03_INPUTS" / "00_QUARANTINE" / "YouTube-Notes", "T12b")


def test_t13():
    try:
        route_input_class("bogus", Path("/tmp"))
        _fail("T13", "Should have raised ValueError")
    except ValueError:
        _ok("T13")


def test_t14():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        result = resolve_unique_path(d, "test.md")
        _assert(result == d / "test.md", "T14")


def test_t15():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "test.md").write_text("x")
        result = resolve_unique_path(d, "test.md")
        _assert(result == d / "test_2.md", "T15a", f"Got: {result}")
        (d / "test_2.md").write_text("x")
        result2 = resolve_unique_path(d, "test.md")
        _assert(result2 == d / "test_3.md", "T15b")


# ── T16–T21: intake_writer ─────────────────────────────────────────────────────

def _make_packet(**kwargs) -> ContentPacket:
    defaults = dict(
        content="This is test content about market microstructure.",
        input_class="transcript",
        source_platform="youtube",
        title="Order Flow and Market Microstructure",
    )
    defaults.update(kwargs)
    return ContentPacket(**defaults)


def test_t16():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "03_INPUTS").mkdir()
        packet = _make_packet()
        result = write_intake(packet, vault)
        content_path = Path(result["content_path"])
        _assert(content_path.exists(), "T16a", "content file not created")
        text = content_path.read_text(encoding="utf-8")
        _assert(text == packet.content, "T16b", "content mismatch")


def test_t17():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "03_INPUTS").mkdir()
        packet = _make_packet()
        result = write_intake(packet, vault)
        sidecar_path = Path(result["sidecar_path"])
        _assert(sidecar_path.exists(), "T17a", "sidecar not created")
        _assert(sidecar_path.suffix == ".json", "T17b")
        _assert(".meta" in sidecar_path.name, "T17c", f"expected .meta.json, got {sidecar_path.name}")


def test_t18():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "03_INPUTS").mkdir()
        packet = _make_packet(
            source_url="https://youtube.com/watch?v=example",
            author="Turney Stevens",
        )
        result = write_intake(packet, vault)
        sidecar = json.loads(Path(result["sidecar_path"]).read_text())

        required_fields = [
            "schema_version", "capture_id", "content_filename",
            "content_sha256", "input_class", "source_platform",
            "title", "captured_at", "capture_method",
            "source_url", "author", "knowledge_class",
            "injection_scan", "promotion_status", "extra_metadata",
            # v8.2 additions
            "original_name", "original_path_or_uri", "detected_mime",
            # v8.3 additions (semantic breadcrumbs)
            "domain_hint", "project_hint", "topic_hint",
            "event_date_hint", "origin_kind", "desired_output_kind",
            "route_reason", "quarantine_status", "workspace_hint",
            "source_package_status",
        ]
        for f in required_fields:
            _assert(f in sidecar, f"T18-{f}", f"missing field: {f}")

        _assert(sidecar["schema_version"] == "8.3", "T18-schema-version")
        _assert(sidecar["input_class"] == "transcript", "T18-input-class")
        _assert(sidecar["source_url"] == "https://youtube.com/watch?v=example", "T18-url")
        _assert(sidecar["author"] == "Turney Stevens", "T18-author")
        _assert(sidecar["knowledge_class"] == "source-derived", "T18-knowledge-class")


def test_t19():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "03_INPUTS").mkdir()
        packet = _make_packet()
        result = write_intake(packet, vault)
        sidecar = json.loads(Path(result["sidecar_path"]).read_text())
        expected_sha = hashlib.sha256(packet.content.encode("utf-8")).hexdigest()
        _assert(sidecar["content_sha256"] == expected_sha, "T19")


def test_t20():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "03_INPUTS").mkdir()
        result = write_intake(_make_packet(), vault)
        sidecar = json.loads(Path(result["sidecar_path"]).read_text())
        _assert(sidecar["promotion_status"] == "quarantine", "T20")


def test_t21():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "03_INPUTS").mkdir()
        packet = _make_packet()
        r1 = write_intake(packet, vault)
        r2 = write_intake(packet, vault)
        _assert(r1["filename"] != r2["filename"], "T21a", "collision not resolved")
        _assert("_2" in r2["filename"], "T21b", f"expected _2 suffix in: {r2['filename']}")


# ── T22–T23: cli_connector ─────────────────────────────────────────────────────

def test_t22():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "input.txt"
        f.write_text("This is captured content.", encoding="utf-8")
        packet = capture_from_cli(
            input_class="source",
            source_platform="web",
            title="DeFi Lending Mechanics",
            file_path=str(f),
        )
        _assert(packet.content == "This is captured content.", "T22a")
        _assert(packet.input_class == "source", "T22b")
        _assert(packet.source_platform == "web", "T22c")
        _assert(packet.title == "DeFi Lending Mechanics", "T22d")


def test_t23():
    try:
        capture_from_cli(
            input_class="source",
            source_platform="web",
            title="T",
            file_path="/nonexistent/path/file.txt",
        )
        _fail("T23", "Should have raised FileNotFoundError")
    except FileNotFoundError:
        _ok("T23")


# ── T24: capture.capture_content (public API) ──────────────────────────────────

def test_t24():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "03_INPUTS").mkdir()
        packet = ContentPacket(
            content="Yield farming mechanics and impermanent loss.",
            input_class="digest",
            source_platform="perplexity",
            title="DeFi Yield Farming Overview",
            source_url="https://perplexity.ai/search/example",
        )
        result = capture_content(packet, vault_root=vault)

        _assert("content_path" in result, "T24a")
        _assert("sidecar_path" in result, "T24b")
        _assert("capture_id" in result, "T24c")
        _assert("content_sha256" in result, "T24d")
        _assert(Path(result["content_path"]).exists(), "T24e")
        _assert(Path(result["sidecar_path"]).exists(), "T24f")
        _assert("digest" in result["filename"], "T24g", f"class missing from filename: {result['filename']}")
        _assert("perplexity" in result["filename"], "T24h", f"source missing: {result['filename']}")


# ── T25: Worked End-to-End Example ────────────────────────────────────────────

def test_t25():
    """
    WORKED EXAMPLE — Phase 8 Hardening Addendum requirement (F).

    Scenario:
        Chase has saved a YouTube transcript about order flow and market
        microstructure. He runs the capture CLI. The system should:
          1. Route to 03_INPUTS/00_QUARANTINE/Transcript-Raw/
          2. Name the file: YYYYMMDD-HHMMSS__transcript__youtube__order-flow-*.md
          3. Write raw content to the .md file (no frontmatter)
          4. Write all metadata to the .meta.json sidecar
          5. Return capture_id, content_sha256, paths
          6. Sidecar promotion_status = "quarantine"
    """
    transcript_text = (
        "Lecture 4: Order Flow and Market Microstructure\n\n"
        "Today we discuss how order flow imbalance drives price discovery "
        "in limit order book markets. The key insight is that informed traders "
        "systematically pick off stale quotes, creating adverse selection for "
        "market makers. This dynamic explains bid-ask spread widening around "
        "information events."
    )

    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "03_INPUTS").mkdir()

        # Build packet as the CLI connector would
        packet = ContentPacket(
            content=transcript_text,
            input_class="transcript",
            source_platform="youtube",
            title="Order Flow and Market Microstructure — Lecture 4",
            source_url="https://www.youtube.com/watch?v=example123",
            author="Prof. Albert Kyle",
            capture_method="cli",
        )

        result = capture_content(packet, vault_root=vault)

        # Verify routing (Pass 2: quarantine boundary is 00_QUARANTINE/)
        content_path = Path(result["content_path"])
        _assert(
            "00_QUARANTINE" in str(content_path),
            "T25-quarantine-dir",
            f"Expected 00_QUARANTINE in path: {content_path}",
        )
        _assert(
            "Transcript-Raw" in str(content_path),
            "T25-routing",
            f"Expected Transcript-Raw in path: {content_path}",
        )

        # Verify naming convention: YYYYMMDD-HHMMSS__transcript__youtube__slug.md
        filename = result["filename"]
        parts = filename.replace(".md", "").split("__")
        _assert(len(parts) == 4, "T25-filename-parts", f"Expected 4 parts: {parts}")
        _assert(len(parts[0]) == 15, "T25-timestamp", f"Timestamp wrong length: {parts[0]}")
        _assert("-" in parts[0], "T25-timestamp-dash", f"Timestamp missing dash: {parts[0]}")
        _assert(parts[1] == "transcript", "T25-class", f"Class wrong: {parts[1]}")
        _assert(parts[2] == "youtube", "T25-source", f"Source wrong: {parts[2]}")
        _assert("order-flow" in parts[3], "T25-slug", f"Slug wrong: {parts[3]}")

        # Verify content file (raw text only, no frontmatter)
        text = content_path.read_text(encoding="utf-8")
        _assert(text == transcript_text, "T25-content", "Content mismatch")
        _assert(not text.startswith("---"), "T25-no-frontmatter", "Content file has frontmatter")

        # Verify sidecar
        sidecar = json.loads(Path(result["sidecar_path"]).read_text())
        _assert(sidecar["promotion_status"] == "quarantine", "T25-quarantine")
        _assert(sidecar["input_class"] == "transcript", "T25-sidecar-class")
        _assert(sidecar["source_platform"] == "youtube", "T25-sidecar-source")
        _assert(sidecar["author"] == "Prof. Albert Kyle", "T25-sidecar-author")
        _assert(sidecar["source_url"] == "https://www.youtube.com/watch?v=example123", "T25-url")
        _assert(sidecar["knowledge_class"] == "source-derived", "T25-knowledge-class")
        _assert(sidecar["injection_scan"] == "not-scanned", "T25-injection-scan")

        # Verify SHA-256
        expected_sha = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()
        _assert(sidecar["content_sha256"] == expected_sha, "T25-sha256")

        # Print the worked example output
        print()
        print("  === WORKED EXAMPLE OUTPUT ===")
        print(f"  Filename:  {filename}")
        print(f"  Location:  03_INPUTS/00_QUARANTINE/Transcript-Raw/")
        print(f"  Sidecar:   {Path(result['sidecar_path']).name}")
        print(f"  Capture ID: {result['capture_id']}")
        print(f"  SHA-256:   {result['content_sha256'][:32]}...")
        print(f"  Sidecar promotion_status: {sidecar['promotion_status']}")
        print()


# ── Runner ─────────────────────────────────────────────────────────────────────

_TESTS = [
    ("T01  ContentPacket: valid construction", test_t01),
    ("T02  ContentPacket: empty content raises ValueError", test_t02),
    ("T03  ContentPacket: invalid input_class raises ValueError", test_t03),
    ("T04  ContentPacket: captured_at defaults to UTC ISO timestamp", test_t04),
    ("T05  ContentPacket: knowledge_class journal -> user-origin", test_t05),
    ("T06  ContentPacket: knowledge_class non-journal -> source-derived", test_t06),
    ("T07  router: make_title_slug basic cases", test_t07),
    ("T08  router: make_title_slug truncation", test_t08),
    ("T09  router: make_title_slug empty -> untitled", test_t09),
    ("T10  router: make_source_slug normalization", test_t10),
    ("T11  router: make_filename format", test_t11),
    ("T12  router: route_input_class subfolder path", test_t12),
    ("T13  router: route_input_class unknown raises ValueError", test_t13),
    ("T14  router: resolve_unique_path no collision", test_t14),
    ("T15  router: resolve_unique_path collision -> _2", test_t15),
    ("T16  intake_writer: write_intake creates content file", test_t16),
    ("T17  intake_writer: write_intake creates sidecar .meta.json", test_t17),
    ("T18  intake_writer: sidecar schema fields present and correct", test_t18),
    ("T19  intake_writer: sidecar content_sha256 matches content", test_t19),
    ("T20  intake_writer: sidecar promotion_status = quarantine", test_t20),
    ("T21  intake_writer: collision resolution (_2 suffix)", test_t21),
    ("T22  cli_connector: capture_from_cli from file", test_t22),
    ("T23  cli_connector: file not found raises FileNotFoundError", test_t23),
    ("T24  capture: capture_content end-to-end", test_t24),
    ("T25  WORKED EXAMPLE: transcript from youtube — full pipeline", test_t25),
]


def main() -> int:
    print("ChaseOS Phase 8 Pass 1 — Test Suite")
    print("=" * 60)
    for label, fn in _TESTS:
        print(f"\n[{label}]")
        _run_test(label, fn)

    print()
    print("=" * 60)
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _ERRORS:
        print("\nFailures:")
        for e in _ERRORS:
            print(f"  - {e}")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
