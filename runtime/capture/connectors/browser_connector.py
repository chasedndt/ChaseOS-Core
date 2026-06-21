"""
browser_connector.py — ChaseOS Phase 8 Pass 7
Browser/saved-HTML connector for the Connector / Capture layer.

Accepts a local .html file (browser "save page" export or any saved HTML),
extracts title and body content, converts HTML to markdown-like normalized text,
and returns a ContentPacket ready for the standard capture pipeline.

DEFAULT INPUT CLASS: 'source'
    Browser-captured pages are external source articles — discrete external
    web content units authored independently. They map cleanly to 'source'
    (quarantine: Sources/, knowledge_class: source-derived).
    The operator may override with --class.

    Rationale: the same reasoning as rss_connector.py. Per-page web captures
    are individual source articles. 'digest' applies only when one file contains
    an aggregated multi-topic summary. 'source' is the correct default.

DEFAULT SOURCE PLATFORM: 'web'
    Generic web source platform unless overridden with --source.
    The operator may provide a specific slug (e.g. "ft-com", "reuters",
    "bloomberg") when capturing from a known publication.

TITLE EXTRACTION PRECEDENCE:
    1. CLI --title argument           (explicit operator override — highest priority)
    2. HTML <title> element           (browser tab/page title)
    3. First <h1> element in body     (article headline)
    4. Filename stem                  (fallback: "fed-article.html" → "fed article")

HTML → MARKDOWN STRATEGY (stdlib only, no external deps):
    Uses html.parser.HTMLParser from the standard library.

    Preserved structure:
        - Headings h1–h6   → # through ######
        - Paragraphs       → blank-line-separated paragraphs
        - Unordered lists  → "- item" per list item
        - Ordered lists    → "1. item", "2. item", etc.
        - Links <a href>   → [text](url) — external hrefs preserved
        - Emphasis strong/b  → **text**
        - Emphasis em/i      → *text*
        - Line breaks br   → newline
        - Code / pre       → `code` for inline; ```\\nblock\\n``` for pre
        - Blockquotes      → > text (if blockquote element present)
        - Tables           → pipe-delimited rows (best-effort)
        - Horizontal rules → ---

    Stripped (discarded) elements:
        - <script>, <style>, <noscript> — executable / style noise
        - <nav>, <aside>, <footer>      — navigation boilerplate
        - HTML comments
        - All tag attributes except href/src on link elements

    HONEST LIMITATIONS:
        - Static HTML only — JS-rendered content not captured
        - No authentication or session cookie support
        - No perfect boilerplate removal — site headers/footers remain in
          documents that do not use semantic HTML5 tags (nav, footer, aside)
        - No Readability-style article extraction heuristics
        - No screenshot or PDF OCR
        - File-based only — no live URL fetching in this pass
        - Character encoding: reads UTF-8 first, falls back to latin-1
        - Malformed HTML is handled gracefully (html.parser is a tag-soup parser)

    The extraction is deterministic and inspectable. Operators review output
    in quarantine before promotion. Article extraction heuristics can be added
    in a future pass.

CAPTURE METHOD: "browser"
    capture_method field is "browser" for all captures from this connector.

QUARANTINE DOCTRINE:
    All captures land in 03_INPUTS/00_QUARANTINE/[class]/ (default: Sources/).
    NOT ingested into SIC at capture time.
    Pipeline: HTML file → parse → ContentPacket → capture_content() → quarantine.
    No auto-promotion. No SIC trigger.

DEDUP:
    Standard dedup registry (Pass 6) applies. Same HTML content captured
    twice returns is_duplicate=True on the second attempt. Dedup key is
    SHA-256 of the extracted markdown text (not the raw HTML), since that
    is what gets written to quarantine.

PROVENANCE FIELDS:
    original_name           — filename (e.g. "fed-article.html")
    original_path_or_uri    — resolved absolute path to the source HTML file
    detected_mime           — "text/html; charset=utf-8" (source was HTML)
    source_url              — CLI --url if provided, else None
    capture_method          — "browser"
    extra_metadata:
        source_file         — filename of the source HTML file
        html_title          — extracted <title> element text (if found)
        html_h1             — first <h1> text in body (if found)
        title_source        — origin of the resolved title:
                              "cli" | "html_title" | "html_h1" | "filename"
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from ..content_packet import ContentPacket, INPUT_CLASS_SOURCE


# ── Public exception type ──────────────────────────────────────────────────────

class HTMLParseError(Exception):
    """Raised when the HTML file cannot be parsed at all."""


# ── Internal constants ─────────────────────────────────────────────────────────

# Tags whose content should be entirely discarded (including child text)
_DISCARD_TAGS: frozenset[str] = frozenset({
    "script", "style", "noscript", "nav", "aside", "footer",
})

# Tags that map to markdown heading syntax
_HEADING_TAGS: frozenset[str] = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

# Tags that should introduce block-level spacing
_BLOCK_TAGS: frozenset[str] = frozenset({
    "p", "div", "section", "article", "main", "blockquote",
    "ul", "ol", "table", "figure",
})

_DEFAULT_SOURCE_PLATFORM = "web"

# Hard ceiling on raw HTML input size — protects html_to_markdown() from
# pathological inputs (full-site exports, CDN-included JS bundles, etc.).
MAX_HTML_INPUT_CHARS: int = 500_000


# ── HTML → Markdown converter ──────────────────────────────────────────────────

class _HTMLConverter(HTMLParser):
    """
    Converts an HTML document to markdown-like normalized text.

    Lenient parser: html.parser handles malformed / tag-soup HTML gracefully.
    Does not raise on malformed input.

    Design goals:
      - Deterministic: same input → same output every time
      - Inspectable: output is readable plain text / markdown in quarantine
      - Honest: does not claim to perfectly extract article body
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

        # Output accumulation
        self._output: list[str] = []
        self._pending_newlines: int = 0   # pending blank lines before next content

        # Discard depth tracking (for nested discardable tags)
        self._discard_depth: int = 0

        # State flags
        self._in_title: bool = False
        self._in_heading: str | None = None   # current heading tag (h1..h6) or None
        self._in_pre: bool = False
        self._in_blockquote: bool = False

        # Ordered list counter stack (for nested ol support)
        self._ol_stack: list[int] = []
        self._in_ol: bool = False

        # Anchor (link) accumulation
        self._in_a: str | None = None      # href of current <a>, or None
        self._a_buffer: list[str] = []     # text accumulating inside <a>

        # Title extraction
        self._title_parts: list[str] = []
        self._h1_text: str | None = None   # first <h1> text found

    # ── Tag handlers ──────────────────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Inside a discarded element: only track nesting depth
        if self._discard_depth > 0:
            if tag in _DISCARD_TAGS:
                self._discard_depth += 1
            return

        # Entering a discard zone
        if tag in _DISCARD_TAGS:
            self._discard_depth += 1
            return

        # <title> — accumulate separately for title extraction
        if tag == "title":
            self._in_title = True
            return

        # Heading tags
        if tag in _HEADING_TAGS:
            self._flush_pending()
            level = int(tag[1])
            self._output.append("\n" + "#" * level + " ")
            self._in_heading = tag
            return

        # Block-level spacing
        if tag in _BLOCK_TAGS:
            self._pending_newlines = max(self._pending_newlines, 2)
            if tag == "blockquote":
                self._in_blockquote = True
            elif tag in ("ul", "ol"):
                self._pending_newlines = max(self._pending_newlines, 1)
            if tag == "ol":
                self._ol_stack.append(0)
                self._in_ol = True
            elif tag == "ul":
                self._in_ol = False
            return

        if tag == "li":
            self._flush_pending()
            if self._ol_stack:
                self._ol_stack[-1] += 1
                self._output.append(f"\n{self._ol_stack[-1]}. ")
            else:
                self._output.append("\n- ")
            return

        if tag == "br":
            self._output.append("\n")
            return

        if tag == "hr":
            self._flush_pending()
            self._output.append("\n---\n")
            return

        # Links — accumulate text, capture href
        if tag == "a":
            attrs_dict = dict(attrs)
            href = attrs_dict.get("href") or ""
            # Only capture external / absolute hrefs (not anchors or javascript)
            if href and not href.startswith("#") and not href.lower().startswith("javascript"):
                self._in_a = href
            else:
                self._in_a = None
            self._a_buffer = []
            return

        # Inline formatting — skip if inside a link (avoid syntax contamination)
        if self._in_a is not None:
            return

        if tag in ("strong", "b"):
            self._flush_pending()
            self._output.append("**")
            return

        if tag in ("em", "i"):
            self._flush_pending()
            self._output.append("*")
            return

        if tag == "code" and not self._in_pre:
            self._output.append("`")
            return

        if tag == "pre":
            self._pending_newlines = max(self._pending_newlines, 1)
            self._flush_pending()
            self._output.append("\n```\n")
            self._in_pre = True
            return

        # Table cells — add separator
        if tag in ("td", "th"):
            self._output.append(" | ")
            return

        if tag == "tr":
            self._flush_pending()
            return

    def handle_endtag(self, tag: str) -> None:
        # Inside a discarded element
        if self._discard_depth > 0:
            if tag in _DISCARD_TAGS:
                self._discard_depth -= 1
            return

        if tag == "title":
            self._in_title = False
            return

        if tag in _HEADING_TAGS:
            self._output.append("\n")
            if tag == "h1":
                # Capture first h1 text (strip the "# " prefix we added)
                full = "".join(self._output)
                lines = full.split("\n")
                for line in reversed(lines):
                    stripped = line.lstrip("#").strip()
                    if stripped:
                        if self._h1_text is None:
                            self._h1_text = stripped
                        break
            self._in_heading = None
            self._pending_newlines = max(self._pending_newlines, 1)
            return

        if tag in _BLOCK_TAGS:
            self._pending_newlines = max(self._pending_newlines, 2)
            if tag == "blockquote":
                self._in_blockquote = False
            if tag == "ol" and self._ol_stack:
                self._ol_stack.pop()
                self._in_ol = bool(self._ol_stack)
            elif tag == "ul":
                self._in_ol = bool(self._ol_stack)
            return

        if tag == "li":
            return

        # Close link: emit [text](href)
        if tag == "a":
            text = "".join(self._a_buffer).strip()
            href = self._in_a
            if text and href:
                self._flush_pending()
                self._output.append(f"[{text}]({href})")
            elif text:
                self._flush_pending()
                self._output.append(text)
            self._in_a = None
            self._a_buffer = []
            return

        if self._in_a is not None:
            return

        if tag in ("strong", "b"):
            self._output.append("**")
            return

        if tag in ("em", "i"):
            self._output.append("*")
            return

        if tag == "code" and not self._in_pre:
            self._output.append("`")
            return

        if tag == "pre":
            self._in_pre = False
            self._output.append("\n```\n")
            self._pending_newlines = max(self._pending_newlines, 1)
            return

        if tag == "tr":
            self._output.append("\n")
            return

    def handle_data(self, data: str) -> None:
        # Inside a discard zone
        if self._discard_depth > 0:
            return

        # Accumulate title text
        if self._in_title:
            self._title_parts.append(data)
            return

        # Normalize whitespace (preserve newlines in pre blocks)
        if not self._in_pre:
            text = re.sub(r"[\t ]+", " ", data)
            text = re.sub(r"\n+", " ", text)
        else:
            text = data

        if not text.strip():
            return

        self._flush_pending()

        # Inside a link: accumulate to buffer (not output)
        if self._in_a is not None:
            self._a_buffer.append(text)
            return

        # Blockquote prefix
        if self._in_blockquote and "\n" in text:
            lines = text.split("\n")
            text = "\n".join("> " + line if line.strip() else line for line in lines)

        self._output.append(text)

    def handle_comment(self, data: str) -> None:
        # Discard HTML comments
        pass

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _flush_pending(self) -> None:
        """Insert pending blank lines before content, if there is prior content."""
        if self._pending_newlines > 0:
            current = "".join(self._output)
            if current.strip():
                self._output.append("\n" * self._pending_newlines)
        self._pending_newlines = 0

    # ── Result accessors ───────────────────────────────────────────────────────

    @property
    def html_title(self) -> str | None:
        """Extracted text from the <title> element, or None."""
        t = "".join(self._title_parts).strip()
        return t if t else None

    @property
    def first_h1(self) -> str | None:
        """Text of the first <h1> element in the body, or None."""
        return self._h1_text

    def get_markdown(self) -> str:
        """
        Return the accumulated markdown-like text, cleaned up.

        Collapses runs of 3+ blank lines to 2 and strips leading/trailing
        whitespace.
        """
        text = "".join(self._output)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


# ── HTML file loading ──────────────────────────────────────────────────────────

def load_html_file(file_path: str | Path) -> str:
    """
    Load an HTML file from disk.

    Tries UTF-8 first; falls back to latin-1 with error replacement.

    Raises:
        FileNotFoundError: if the file does not exist.
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"HTML file not found: {file_path}")
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="latin-1", errors="replace")


# ── HTML → Markdown conversion ─────────────────────────────────────────────────

def html_to_markdown(html_content: str) -> tuple[str, str | None, str | None]:
    """
    Convert an HTML string to markdown-like normalized text.

    Returns:
        (markdown_text, html_title, first_h1)
        markdown_text: normalized text for quarantine content file
        html_title:    text of the <title> element, or None
        first_h1:      text of the first <h1> body element, or None

    Does not raise on malformed HTML — html.parser is lenient (tag-soup).
    """
    converter = _HTMLConverter()
    try:
        converter.feed(html_content)
        converter.close()
    except Exception:
        # Fallback: strip all tags crudely
        text = re.sub(r"<[^>]+>", "", html_content)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text, None, None

    return converter.get_markdown(), converter.html_title, converter.first_h1


# ── Title resolution ───────────────────────────────────────────────────────────

def resolve_title(
    *,
    cli_title: str | None,
    html_title: str | None,
    first_h1: str | None,
    filename: str,
) -> tuple[str, str]:
    """
    Resolve the best available title using the defined precedence order.

    Precedence:
        1. cli_title    — operator explicitly provided via --title (highest)
        2. html_title   — extracted from <title> element
        3. first_h1     — text of first <h1> in body
        4. filename     — filename stem, lightly normalized (fallback)

    Returns:
        (resolved_title, title_source)
        title_source: "cli" | "html_title" | "html_h1" | "filename"
    """
    if cli_title and cli_title.strip():
        return cli_title.strip(), "cli"

    if html_title and html_title.strip():
        return html_title.strip(), "html_title"

    if first_h1 and first_h1.strip():
        return first_h1.strip(), "html_h1"

    # Filename stem fallback: replace underscores/hyphens with spaces
    stem = Path(filename).stem
    title = re.sub(r"[_\-]+", " ", stem).strip()
    return (title if title else "Untitled"), "filename"


# ── Public API ─────────────────────────────────────────────────────────────────

def capture_from_browser(
    *,
    file_path: str | Path,
    title: str | None = None,
    source_url: str | None = None,
    author: str | None = None,
    input_class: str = INPUT_CLASS_SOURCE,
    source_platform: str = _DEFAULT_SOURCE_PLATFORM,
    workspace_hint: str | None = None,
    domain_hint: str | None = None,
    project_hint: str | None = None,
    topic_hint: str | None = None,
    event_date_hint: str | None = None,
    origin_kind: str | None = None,
    desired_output_kind: str | None = None,
) -> ContentPacket:
    """
    Load a local HTML file and return a ContentPacket for the capture pipeline.

    This is the primary entry point for the browser connector.
    It performs HTML loading, title extraction, HTML-to-markdown conversion,
    and ContentPacket construction. It does NOT write to the vault — call
    capture_content(packet) to complete the capture.

    Title resolution precedence:
        1. CLI --title argument (explicit operator override — highest priority)
        2. HTML <title> element
        3. First <h1> element in document body
        4. Filename stem (lightly normalized)

    Content:
        The markdown-like extracted text is written as the quarantine content.
        The original HTML is NOT stored in quarantine — only the extracted text.
        This keeps quarantine files readable and SIC-ready.

    Args:
        file_path:           Path to the local .html file (or any HTML content file).
        title:               CLI title override (highest precedence).
        source_url:          URL of the original web page, if known (--url).
        author:              Author or creator, if known.
        input_class:         Intake class. Default: "source".
        source_platform:     Source platform slug. Default: "web".
        workspace_hint:      Optional SIC workspace name for future ingestion.
        domain_hint:         Semantic breadcrumb hint — ChaseOS domain.
        project_hint:        Semantic breadcrumb hint — active project.
        topic_hint:          Semantic breadcrumb hint — subject label.
        event_date_hint:     Semantic breadcrumb hint — ISO 8601 event date.
        origin_kind:         Semantic breadcrumb hint — content authorship origin.
        desired_output_kind: Semantic breadcrumb hint — intended output type.

    Returns:
        A fully-populated ContentPacket ready for capture_content().

    Raises:
        FileNotFoundError: If file_path does not exist.
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"HTML file not found: {file_path}")

    html_content = load_html_file(p)
    if len(html_content) > MAX_HTML_INPUT_CHARS:
        raise ValueError(
            f"HTML input too large: {len(html_content):,} chars exceeds "
            f"MAX_HTML_INPUT_CHARS={MAX_HTML_INPUT_CHARS:,}"
        )
    markdown_text, html_title, first_h1 = html_to_markdown(html_content)

    if not markdown_text:
        markdown_text = "(No extractable text content found in this HTML file.)"

    resolved_title, title_source = resolve_title(
        cli_title=title,
        html_title=html_title,
        first_h1=first_h1,
        filename=p.name,
    )

    return ContentPacket(
        content=markdown_text,
        input_class=input_class,
        source_platform=source_platform,
        title=resolved_title,
        source_url=source_url,
        author=author,
        original_name=p.name,
        original_path_or_uri=str(p.resolve()),
        detected_mime="text/html; charset=utf-8",
        workspace_hint=workspace_hint,
        domain_hint=domain_hint,
        project_hint=project_hint,
        topic_hint=topic_hint,
        event_date_hint=event_date_hint,
        origin_kind=origin_kind,
        desired_output_kind=desired_output_kind,
        extra_metadata={
            "source_file":  p.name,
            "html_title":   html_title,
            "html_h1":      first_h1,
            "title_source": title_source,
        },
        capture_method="browser",
    )
