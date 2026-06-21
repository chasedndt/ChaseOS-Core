"""
rss_connector.py — ChaseOS Phase 8 Pass 5
RSS/Atom feed connector for the Connector / Capture layer.

Fetches a feed URL, parses RSS 2.0 or Atom 1.0, normalizes each entry into a
ContentPacket, and returns them ready for capture_content(). No vault I/O here.

DEFAULT INPUT CLASS: 'source'
    RSS/Atom feed items are external source articles — discrete external web
    content units authored independently. They map cleanly to 'source'
    (quarantine: Sources/, knowledge_class: source-derived).

    'digest' is more appropriate for a single curated newsletter captured as
    one aggregated piece. Per-item feed ingestion → individual source articles
    → class 'source'. The operator may override with --class.

PARSING STRATEGY: stdlib only (no external dependencies)
    Uses urllib.request for HTTP fetch and xml.etree.ElementTree for XML parsing.
    Supported:
        RSS 2.0        — most common; <rss><channel><item> structure
        Atom 1.0       — modern feeds; <feed xmlns=...><entry> structure
    Limitations (honest):
        RSS 0.91/0.92  — partial; common fields (title, link, description) work
        dc:creator     — Dublin Core author not extracted (author field is None)
        media:*        — Media RSS extensions not extracted
        iTunes/podcast — Extension fields ignored
        Malformed XML  — raises FeedParseError with context; other items not affected
        Relative URLs  — not resolved (feed items must have absolute links)
        Charset detect — always reads as UTF-8 with error replacement fallback
        Auth-gated feeds — no authentication support (public feeds only)

QUARANTINE DOCTRINE:
    All captured feed items land in 03_INPUTS/00_QUARANTINE/[class]/ (default: Sources/).
    NOT ingested into SIC at capture time.
    Pipeline: fetch → parse → ContentPacket → capture_content() → quarantine
    No promotion. No SIC trigger. No deduplication (dedup is a future pass).

PROVENANCE FIELDS per item:
    source_url              — item link (original article URL)
    original_path_or_uri   — same as item link
    original_name          — item GUID or link (item identity at source)
    event_date_hint        — item pubDate/published parsed to YYYY-MM-DD (if available)
                             CLI --event-date overrides per-item dates if provided
    source_platform        — derived from feed hostname slug (e.g. "reuters-com")
                             CLI --source overrides
    extra_metadata:
        feed_url            — the URL that was fetched
        feed_title          — the feed's channel/feed title
        feed_type           — "rss" or "atom"
        item_guid           — item GUID if present
        item_author         — item author if present
        published_raw       — raw pubDate/published string (unparsed)
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from ..content_packet import ContentPacket, INPUT_CLASS_SOURCE


# ── Feed type constants ────────────────────────────────────────────────────────

FEED_TYPE_RSS  = "rss"
FEED_TYPE_ATOM = "atom"

# Hard ceiling on items returned per feed — prevents unbounded memory growth on
# huge feeds (e.g. full-archive RSS exports). Operator --limit may further reduce
# but cannot exceed this value.
MAX_ITEMS_PER_FEED = 200

_ATOM_NS = "http://www.w3.org/2005/Atom"


# ── Public exception types ─────────────────────────────────────────────────────

class FeedFetchError(Exception):
    """Raised when the HTTP fetch of a feed URL fails."""


class FeedParseError(Exception):
    """Raised when the feed XML cannot be parsed as RSS or Atom."""


# ── HTTP fetch ─────────────────────────────────────────────────────────────────

def fetch_feed(url: str, timeout: int = 30) -> str:
    """
    Fetch feed content from a URL and return the raw XML string.

    Uses urllib.request (stdlib). Sends a minimal User-Agent identifying ChaseOS.
    Always decodes as UTF-8 with error replacement.

    Raises:
        FeedFetchError: if the fetch fails for any reason (network, HTTP error, etc.)
    """
    try:
        # SSRF guard: scheme allowlist + private/loopback/metadata-IP block +
        # redirect re-validation. Blocks feeds pointed at internal/metadata hosts.
        from runtime.net.egress_guard import EgressBlocked, safe_urlopen

        try:
            resp_cm = safe_urlopen(
                url,
                headers={"User-Agent": "ChaseOS/1.0 FeedConnector (local-first; not a bot)"},
                timeout=timeout,
            )
        except EgressBlocked as exc:
            raise FeedFetchError(f"Blocked by egress policy: {exc}") from exc
        with resp_cm as resp:
            raw = resp.read()
            # Attempt charset from Content-Type header; fallback to utf-8
            charset = "utf-8"
            ct = resp.headers.get("Content-Type", "")
            if "charset=" in ct:
                try:
                    charset = ct.split("charset=")[-1].strip().rstrip(";").strip()
                except Exception:
                    charset = "utf-8"
            return raw.decode(charset, errors="replace")
    except FeedFetchError:
        raise
    except Exception as exc:
        raise FeedFetchError(f"Failed to fetch {url}: {exc}") from exc


# ── Source platform derivation ─────────────────────────────────────────────────

def derive_source_platform(feed_url: str) -> str:
    """
    Derive a ContentPacket source_platform slug from the feed URL hostname.

    Examples:
        "https://feeds.reuters.com/reuters/businessNews" → "feeds-reuters-com"
        "https://www.ft.com/rss/home/uk"                → "ft-com"
        "https://news.ycombinator.com/rss"              → "news-ycombinator-com"

    Falls back to "rss" if URL is unparseable.
    """
    try:
        netloc = urllib.parse.urlparse(feed_url).netloc
        if not netloc:
            return "rss"
        # Strip port (e.g. example.com:8080 → example.com)
        netloc = netloc.split(":")[0]
        # Strip www. prefix (www.ft.com → ft.com)
        netloc = re.sub(r"^www\.", "", netloc.lower())
        # Slugify: non-alnum → hyphen
        slug = re.sub(r"[^a-z0-9]+", "-", netloc).strip("-")
        return slug or "rss"
    except Exception:
        return "rss"


# ── Date parsing ───────────────────────────────────────────────────────────────

def parse_feed_date(date_str: str | None) -> str | None:
    """
    Parse an RSS pubDate (RFC 2822) or Atom published (ISO 8601) string to YYYY-MM-DD.

    Returns None if the string is absent or unparseable.
    Does not raise — failures are silently discarded.
    """
    if not date_str:
        return None
    date_str = date_str.strip()

    # Try RFC 2822 (RSS pubDate: "Mon, 28 Mar 2026 14:30:00 +0000")
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.date().isoformat()
    except Exception:
        pass

    # Try ISO 8601 date prefix (Atom published: "2026-03-28T14:30:00Z")
    try:
        date_part = date_str[:10]
        datetime.strptime(date_part, "%Y-%m-%d")
        return date_part
    except Exception:
        pass

    return None


# ── Content text builder ───────────────────────────────────────────────────────

def build_item_content(
    title: str,
    link: str | None,
    description: str | None,
    author: str | None,
    pub_date_raw: str | None,
) -> str:
    """
    Build a readable plain-text representation of a feed item.

    The resulting text is written as the content file in quarantine.
    HTML tags are stripped. Common entities are decoded.
    No frontmatter — quarantine files carry raw content only.
    """
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")

    if link:
        lines.append(f"Source: {link}")
    if author:
        lines.append(f"Author: {author}")
    if pub_date_raw:
        lines.append(f"Published: {pub_date_raw}")
    lines.append("")

    if description:
        # Strip HTML tags
        clean = re.sub(r"<[^>]+>", "", description)
        # Decode common HTML entities
        clean = (
            clean
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&apos;", "'")
            .replace("&nbsp;", " ")
        )
        # Normalize whitespace
        clean = re.sub(r"\n{3,}", "\n\n", clean.strip())
        lines.append(clean)
    else:
        lines.append("(no description available)")

    return "\n".join(lines)


# ── RSS 2.0 parser ─────────────────────────────────────────────────────────────

def parse_rss(xml_text: str) -> tuple[str | None, list[dict]]:
    """
    Parse an RSS 2.0 feed.

    Returns:
        (feed_title, items)

    Each item dict has keys:
        title, link, description, author, pub_date, guid

    Raises:
        FeedParseError: if the XML is malformed or has no <channel> element.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FeedParseError(f"XML parse error in RSS feed: {exc}") from exc

    channel = root.find("channel")
    if channel is None:
        raise FeedParseError("No <channel> element found — may not be a valid RSS feed")

    def _text(el, tag: str) -> str | None:
        child = el.find(tag)
        return child.text if child is not None and child.text else None

    feed_title = _text(channel, "title")

    items: list[dict] = []
    for item_el in channel.findall("item"):
        items.append({
            "title":       _text(item_el, "title"),
            "link":        _text(item_el, "link"),
            "description": _text(item_el, "description"),
            "author":      _text(item_el, "author"),
            "pub_date":    _text(item_el, "pubDate"),
            "guid":        _text(item_el, "guid"),
        })

    return feed_title, items


# ── Atom 1.0 parser ────────────────────────────────────────────────────────────

def parse_atom(xml_text: str) -> tuple[str | None, list[dict]]:
    """
    Parse an Atom 1.0 feed.

    Returns:
        (feed_title, items)

    Each item dict has the same keys as parse_rss() for a unified interface.

    Raises:
        FeedParseError: if the XML is malformed.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FeedParseError(f"XML parse error in Atom feed: {exc}") from exc

    ns = {"a": _ATOM_NS}

    def _find(el, tag: str):
        # Try namespaced first, then bare (some feeds omit the namespace on children)
        result = el.find(f"a:{tag}", ns)
        if result is None:
            result = el.find(tag)
        return result

    def _findall(el, tag: str):
        results = el.findall(f"a:{tag}", ns)
        if not results:
            results = el.findall(tag)
        return results

    def _text(el, tag: str) -> str | None:
        child = _find(el, tag)
        return child.text if child is not None and child.text else None

    feed_title_el = _find(root, "title")
    feed_title = feed_title_el.text if feed_title_el is not None else None

    items: list[dict] = []
    for entry in _findall(root, "entry"):
        # Extract link: prefer rel="alternate", then first link with href
        link = None
        link_els = entry.findall(f"a:link", ns) or entry.findall("link")
        for lel in link_els:
            rel = lel.get("rel", "alternate")
            href = lel.get("href")
            if href and rel == "alternate":
                link = href
                break
        if link is None:
            for lel in link_els:
                href = lel.get("href")
                if href:
                    link = href
                    break

        # Content: prefer content, then summary
        content_el = _find(entry, "content") or _find(entry, "summary")
        description = content_el.text if content_el is not None else None

        # Author name
        author = None
        author_el = _find(entry, "author")
        if author_el is not None:
            name_el = _find(author_el, "name")
            author = name_el.text if name_el is not None else None

        # Date: prefer published, fall back to updated
        pub_date = _text(entry, "published") or _text(entry, "updated")

        items.append({
            "title":       _text(entry, "title"),
            "link":        link,
            "description": description,
            "author":      author,
            "pub_date":    pub_date,
            "guid":        _text(entry, "id"),
        })

    return feed_title, items


# ── Feed type detection ────────────────────────────────────────────────────────

def detect_and_parse(xml_text: str) -> tuple[str, str | None, list[dict]]:
    """
    Detect feed type (RSS or Atom) and parse it.

    Returns:
        (feed_type, feed_title, items)
        feed_type is FEED_TYPE_RSS or FEED_TYPE_ATOM.

    Detection strategy:
        1. Check for <rss in the first 500 chars → try RSS first
        2. Check for Atom namespace in the first 500 chars → try Atom first
        3. Fallback: try RSS, then Atom

    Raises:
        FeedParseError: if neither RSS nor Atom parsing succeeds.
    """
    probe = xml_text[:500]

    if "<rss" in probe:
        feed_title, items = parse_rss(xml_text)
        return FEED_TYPE_RSS, feed_title, items

    if _ATOM_NS in probe or "<feed" in probe:
        feed_title, items = parse_atom(xml_text)
        return FEED_TYPE_ATOM, feed_title, items

    # Ambiguous — try RSS, then Atom
    rss_exc = None
    try:
        feed_title, items = parse_rss(xml_text)
        if feed_title is not None or items:
            return FEED_TYPE_RSS, feed_title, items
    except FeedParseError as exc:
        rss_exc = exc

    try:
        feed_title, items = parse_atom(xml_text)
        return FEED_TYPE_ATOM, feed_title, items
    except FeedParseError as exc:
        raise FeedParseError(
            f"Could not parse feed as RSS or Atom. "
            f"RSS error: {rss_exc}. Atom error: {exc}"
        ) from exc


# ── ContentPacket normalization ────────────────────────────────────────────────

def items_to_packets(
    items: list[dict],
    *,
    feed_url: str,
    feed_title: str | None,
    feed_type: str,
    input_class: str = INPUT_CLASS_SOURCE,
    source_platform: str,
    domain_hint: str | None = None,
    project_hint: str | None = None,
    topic_hint: str | None = None,
    event_date_hint_override: str | None = None,
    origin_kind: str | None = None,
    desired_output_kind: str | None = None,
    workspace_hint: str | None = None,
    limit: int | None = None,
) -> tuple[list[ContentPacket], list[dict]]:
    """
    Normalize a list of feed item dicts into ContentPackets.

    Returns:
        (packets, skipped)
        packets: list of ContentPacket ready for capture_content()
        skipped: list of {title, reason} dicts for items not converted

    Skips items with no title, no link, and no description (nothing to capture).

    Per-item event_date_hint: parsed from item pub_date unless event_date_hint_override
    is provided by the operator (CLI --event-date). Override applies to ALL items.
    """
    # Hard ceiling first — operator limit cannot exceed MAX_ITEMS_PER_FEED
    items = items[:MAX_ITEMS_PER_FEED]
    if limit is not None:
        items = items[:limit]

    packets: list[ContentPacket] = []
    skipped: list[dict] = []

    for item in items:
        raw_title = (item.get("title") or "").strip()
        link = (item.get("link") or "").strip() or None
        description = item.get("description")

        # Build a usable title
        if raw_title:
            title = raw_title
        elif link:
            title = link
        else:
            skipped.append({
                "title": "(no title or link)",
                "reason": "item has no title and no link — nothing to identify it",
            })
            continue

        # Must have some content to capture
        if not description and not link:
            skipped.append({
                "title": title,
                "reason": "item has no description and no link — nothing to capture",
            })
            continue

        content = build_item_content(
            title=title,
            link=link,
            description=description,
            author=item.get("author"),
            pub_date_raw=item.get("pub_date"),
        )

        # Event date: use CLI override if provided; otherwise parse from item
        eff_event_date = event_date_hint_override or parse_feed_date(item.get("pub_date"))

        packet = ContentPacket(
            content=content,
            input_class=input_class,
            source_platform=source_platform,
            title=title,
            source_url=link,
            original_name=item.get("guid") or link,
            original_path_or_uri=link,
            workspace_hint=workspace_hint,
            domain_hint=domain_hint,
            project_hint=project_hint,
            topic_hint=topic_hint,
            event_date_hint=eff_event_date,
            origin_kind=origin_kind,
            desired_output_kind=desired_output_kind,
            extra_metadata={
                "feed_url":      feed_url,
                "feed_title":    feed_title,
                "feed_type":     feed_type,
                "item_guid":     item.get("guid"),
                "item_author":   item.get("author"),
                "published_raw": item.get("pub_date"),
            },
            capture_method="rss",
        )
        packets.append(packet)

    return packets, skipped


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_and_parse_feed(
    feed_url: str,
    *,
    limit: int | None = None,
    input_class: str = INPUT_CLASS_SOURCE,
    source_platform_override: str | None = None,
    domain_hint: str | None = None,
    project_hint: str | None = None,
    topic_hint: str | None = None,
    event_date_hint_override: str | None = None,
    origin_kind: str | None = None,
    desired_output_kind: str | None = None,
    workspace_hint: str | None = None,
) -> tuple[str | None, str, list[ContentPacket], list[dict]]:
    """
    Fetch an RSS/Atom feed and return normalized ContentPackets.

    This is the primary entry point for the RSS connector.
    It performs HTTP fetch, format detection, parsing, and normalization.
    It does NOT write to the vault — call capture_content(packet) for each packet.

    Args:
        feed_url:                 URL of the RSS or Atom feed.
        limit:                    Max number of items to normalize (None = all).
        input_class:              ContentPacket input_class. Default: "source".
        source_platform_override: Override derived source_platform slug.
        domain_hint:              Semantic hint — ChaseOS domain.
        project_hint:             Semantic hint — active project.
        topic_hint:               Semantic hint — subject label.
        event_date_hint_override: Overrides per-item date for ALL items.
        origin_kind:              Semantic hint — content authorship origin.
        desired_output_kind:      Semantic hint — intended output type.
        workspace_hint:           Optional SIC workspace name for future ingestion.

    Returns:
        (feed_title, feed_type, packets, skipped)
        feed_title:  str | None — title of the feed, if available
        feed_type:   "rss" | "atom"
        packets:     list[ContentPacket] — ready for capture_content()
        skipped:     list[{title, reason}] — items not normalized

    Raises:
        FeedFetchError:  if the URL cannot be fetched.
        FeedParseError:  if the content cannot be parsed as RSS or Atom.
    """
    xml_text = fetch_feed(feed_url)
    feed_type, feed_title, items = detect_and_parse(xml_text)

    source_platform = source_platform_override or derive_source_platform(feed_url)

    packets, skipped = items_to_packets(
        items,
        feed_url=feed_url,
        feed_title=feed_title,
        feed_type=feed_type,
        input_class=input_class,
        source_platform=source_platform,
        domain_hint=domain_hint,
        project_hint=project_hint,
        topic_hint=topic_hint,
        event_date_hint_override=event_date_hint_override,
        origin_kind=origin_kind,
        desired_output_kind=desired_output_kind,
        workspace_hint=workspace_hint,
        limit=limit,
    )

    return feed_title, feed_type, packets, skipped
