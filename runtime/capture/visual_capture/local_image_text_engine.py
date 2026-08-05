"""Repo-owned local image text engine for controlled Capture to Markdown images."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import statistics
import struct
import sys
import zlib


ENGINE_ID = "chaseos-builtin-local-image-text"

FONT_5X7: dict[str, tuple[str, ...]] = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "?": ("11110", "00001", "00001", "00110", "00100", "00000", "00100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "11100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}


@dataclass(frozen=True)
class DecodedGlyph:
    char: str
    x1: int
    y1: int
    x2: int
    y2: int
    scale: int


@dataclass(frozen=True)
class NormalizedTemplateGlyph:
    char: str
    pattern: tuple[str, ...]
    bits: int
    aspect_ratio: float
    source: str


def local_image_text_engine_command() -> list[str]:
    return [sys.executable, str(Path(__file__).resolve())]


def render_pixel_text_png(
    lines: tuple[str, ...],
    *,
    scale: int = 10,
    margin: int | None = None,
    char_spacing: int | None = None,
    line_spacing: int | None = None,
    foreground: tuple[int, int, int] = (8, 14, 28),
    background: tuple[int, int, int] = (252, 252, 250),
    min_width: int = 0,
    min_height: int = 0,
) -> tuple[bytes, int, int]:
    normalized = tuple(_normalize_line(line) for line in lines)
    if not normalized:
        normalized = ("",)
    margin = 4 * scale if margin is None else max(0, int(margin))
    char_spacing = 2 * scale if char_spacing is None else max(0, int(char_spacing))
    line_spacing = 3 * scale if line_spacing is None else max(0, int(line_spacing))
    char_width = 5 * scale
    char_height = 7 * scale
    max_chars = max(1, max(len(line) for line in normalized))
    width = max(min_width, margin * 2 + max_chars * char_width + max(0, max_chars - 1) * char_spacing)
    height = max(min_height, margin * 2 + len(normalized) * char_height + max(0, len(normalized) - 1) * line_spacing)
    pixels = bytearray(bytes(background) * width * height)
    y = margin
    for line in normalized:
        x = margin
        for char in line:
            pattern = FONT_5X7.get(char, FONT_5X7["?"])
            _draw_char(pixels, width, height, x, y, pattern, scale, foreground)
            x += char_width + char_spacing
        y += char_height + line_spacing
    return _encode_png_rgb(width, height, pixels), width, height


def render_common_font_text_png(
    lines: tuple[str, ...],
    *,
    font_family: str = "Segoe UI",
    point_size: int = 32,
    foreground: tuple[int, int, int] = (22, 28, 38),
    background: tuple[int, int, int] = (250, 250, 248),
    min_width: int = 0,
    min_height: int = 0,
    margin: int = 32,
) -> tuple[bytes, int, int]:
    """Render screenshot-like text with the local Studio font stack.

    This helper is used by the fixture proof lane. Extraction itself still
    reads only an explicit local PNG path.
    """

    width, height, rows = _render_system_font_rgb_rows(
        lines,
        font_family=font_family,
        point_size=point_size,
        foreground=foreground,
        background=background,
        min_width=min_width,
        min_height=min_height,
        margin=margin,
    )
    rgb = bytearray(b"".join(rows))
    return _encode_png_rgb(width, height, rgb), width, height


def extract_text_from_pixel_image(file_path: str | Path) -> str:
    width, height, rows = _read_png_rgb(Path(file_path))
    background = _estimate_background(rows, width, height)
    threshold = _foreground_threshold(rows, background)
    grid_text = _extract_grid_text(rows, width, height, background, threshold)
    if grid_text:
        return grid_text

    component_text = _extract_component_text(rows, width, height, background, threshold)
    if component_text:
        return component_text
    return ""


def _extract_grid_text(
    rows: list[bytes],
    width: int,
    height: int,
    background: int,
    threshold: int,
) -> str:
    decoded_lines: list[str] = []
    for y1, y2 in _row_bands(rows, width, height, background, threshold):
        decoded = _decode_row_band(rows, width, y1, y2, background, threshold)
        if decoded:
            decoded_lines.append(decoded)
    return "\n".join(decoded_lines).strip()


def _extract_component_text(
    rows: list[bytes],
    width: int,
    height: int,
    background: int,
    threshold: int,
) -> str:
    glyphs: list[DecodedGlyph] = []
    for component in _connected_components(rows, width, height, background, threshold):
        glyph = _classify_common_font_component(rows, component, background, threshold)
        if glyph is None:
            glyph = _classify_component(rows, component, background, threshold)
        if glyph is not None:
            glyphs.append(glyph)
    return _assemble_lines(glyphs)


def _row_bands(
    rows: list[bytes],
    width: int,
    height: int,
    background: int,
    threshold: int,
) -> list[tuple[int, int]]:
    row_counts = [
        sum(1 for x in range(width) if _is_foreground(rows, x, y, background, threshold))
        for y in range(height)
    ]
    active_rows = [count >= 2 for count in row_counts]
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for y, active in enumerate(active_rows):
        if active and start is None:
            start = y
        elif not active and start is not None:
            if y - start >= 3:
                bands.append((start, y - 1))
            start = None
    if start is not None and height - start >= 3:
        bands.append((start, height - 1))
    return bands


def _decode_row_band(
    rows: list[bytes],
    width: int,
    y1: int,
    y2: int,
    background: int,
    threshold: int,
) -> str:
    band_height = y2 - y1 + 1
    estimated_scale = max(1, round(band_height / 7))
    scale_candidates = (
        [estimated_scale]
        if estimated_scale >= 2 and abs(band_height - 7 * estimated_scale) <= max(2, estimated_scale)
        else []
    )
    if not scale_candidates:
        return ""
    foreground_columns = [
        x
        for x in range(width)
        if any(_is_foreground(rows, x, y, background, threshold) for y in range(y1, y2 + 1))
    ]
    if not foreground_columns:
        return ""
    first_col = min(foreground_columns)
    last_col = max(foreground_columns)
    best: tuple[float, str] | None = None
    for scale in scale_candidates:
        spacing_candidates = sorted(
            {
                scale,
                2 * scale,
                3 * scale,
            }
        )
        for leading_blank_rows in range(1):
            y_origin = y1 - leading_blank_rows * scale
            if y_origin < 0 or y_origin + 7 * scale > len(rows):
                continue
            for spacing in spacing_candidates:
                advance = 5 * scale + spacing
                for leading_blank_columns in range(3):
                    base_origin = first_col - leading_blank_columns * scale
                    for jitter in (0,):
                        x_origin = base_origin + jitter
                        if x_origin < 0:
                            continue
                        decoded, score, visible_count = _decode_grid_candidate(
                            rows,
                            width,
                            x_origin,
                            y_origin,
                            last_col,
                            scale,
                            advance,
                            background,
                            threshold,
                        )
                        if visible_count < 2:
                            continue
                        candidate = (score, decoded)
                        if best is None or candidate < best:
                            best = candidate
    if best is None:
        return ""
    score, decoded = best
    if score > 0.12:
        return ""
    return decoded.strip()


def _decode_grid_candidate(
    rows: list[bytes],
    width: int,
    x_origin: int,
    y_origin: int,
    last_col: int,
    scale: int,
    advance: int,
    background: int,
    threshold: int,
) -> tuple[str, float, int]:
    chars: list[str] = []
    scores: list[float] = []
    visible_count = 0
    x = x_origin
    while x <= last_col + scale:
        pattern = _cell_pattern(rows, width, x, y_origin, scale, background, threshold)
        char, score = _classify_cell_pattern(pattern)
        chars.append(char)
        scores.append(score)
        if char != " ":
            visible_count += 1
        x += advance
    decoded = "".join(chars).rstrip()
    meaningful_scores = [
        score
        for char, score in zip(decoded, scores)
        if char != " "
    ]
    average_score = statistics.mean(meaningful_scores) if meaningful_scores else 1.0
    return decoded, average_score, visible_count


def _grid_gap_foreground_ratio(
    rows: list[bytes],
    width: int,
    x_origin: int,
    y_origin: int,
    last_col: int,
    scale: int,
    advance: int,
    background: int,
    threshold: int,
) -> float:
    total_foreground = 0
    foreground_in_gaps = 0
    line_height = 7 * scale
    cell_width = 5 * scale
    for y in range(y_origin, min(len(rows), y_origin + line_height)):
        if y < 0:
            continue
        for x in range(0, width):
            if not _is_foreground(rows, x, y, background, threshold):
                continue
            total_foreground += 1
            if x < x_origin or x > last_col:
                foreground_in_gaps += 1
                continue
            relative_x = x - x_origin
            within_cell = relative_x % advance < cell_width
            if not within_cell:
                foreground_in_gaps += 1
    return foreground_in_gaps / max(1, total_foreground)


def _cell_pattern(
    rows: list[bytes],
    width: int,
    x: int,
    y: int,
    scale: int,
    background: int,
    threshold: int,
) -> tuple[str, ...]:
    height = len(rows)
    output: list[str] = []
    for row_index in range(7):
        bits: list[str] = []
        for col_index in range(5):
            px = x + col_index * scale + scale // 2
            py = y + row_index * scale + scale // 2
            bits.append(
                "1"
                if 0 <= px < width
                and 0 <= py < height
                and _is_foreground(rows, px, py, background, threshold)
                else "0"
            )
        output.append("".join(bits))
    return tuple(output)


def _classify_cell_pattern(pattern: tuple[str, ...]) -> tuple[str, float]:
    if not any("1" in row for row in pattern):
        return " ", 0.0
    best: tuple[float, str] | None = None
    for char, candidate in FONT_5X7.items():
        score = _pattern_distance(pattern, candidate)
        option = (score, char)
        if best is None or option < best:
            best = option
    if best is None:
        return "?", 1.0
    return best[1], best[0]


def _normalize_line(value: str) -> str:
    normalized = str(value or "").upper()
    unsupported = sorted({char for char in normalized if char not in FONT_5X7})
    if unsupported:
        raise ValueError(f"Unsupported pixel text characters: {''.join(unsupported)}")
    return normalized


def _draw_char(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    pattern: tuple[str, ...],
    scale: int,
    color: tuple[int, int, int],
) -> None:
    for row_index, row in enumerate(pattern):
        for col_index, flag in enumerate(row):
            if flag != "1":
                continue
            for dy in range(scale):
                for dx in range(scale):
                    px = x + col_index * scale + dx
                    py = y + row_index * scale + dy
                    if 0 <= px < width and 0 <= py < height:
                        offset = (py * width + px) * 3
                        pixels[offset : offset + 3] = bytes(color)


def _encode_png_rgb(width: int, height: int, rgb: bytearray) -> bytes:
    raw = bytearray()
    row_bytes = width * 3
    for y in range(height):
        raw.append(0)
        start = y * row_bytes
        raw.extend(rgb[start : start + row_bytes])
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
            _png_chunk(b"IEND", b""),
        ]
    )


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    payload = chunk_type + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


def _read_png_rgb(path: Path) -> tuple[int, int, list[bytes]]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Image text engine only accepts PNG files.")
    pos = 8
    width = height = bit_depth = color_type = 0
    idat = bytearray()
    while pos < len(data):
        if pos + 8 > len(data):
            raise ValueError("PNG file is truncated.")
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk_data = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if bit_depth != 8 or color_type not in {2, 6} or compression != 0 or filter_method != 0 or interlace != 0:
                raise ValueError("Image text engine supports non-interlaced 8-bit RGB or RGBA PNG files.")
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
    if width <= 0 or height <= 0 or not idat:
        raise ValueError("PNG file has no readable image data.")
    channels = 4 if color_type == 6 else 3
    raw = zlib.decompress(bytes(idat))
    source_row_bytes = width * channels
    rows: list[bytes] = []
    offset = 0
    previous = bytes(width * channels)
    for _ in range(height):
        filter_type = raw[offset]
        start = offset + 1
        row = bytearray(raw[start : start + source_row_bytes])
        row = _unfilter_row(filter_type, row, previous, channels)
        previous = bytes(row)
        if channels == 4:
            rgb = bytearray()
            for index in range(0, len(row), 4):
                rgb.extend(row[index : index + 3])
            rows.append(bytes(rgb))
        else:
            rows.append(bytes(row))
        offset = start + source_row_bytes
    return width, height, rows


def _unfilter_row(filter_type: int, row: bytearray, previous: bytes, channels: int) -> bytearray:
    if filter_type == 0:
        return row
    for index in range(len(row)):
        left = row[index - channels] if index >= channels else 0
        up = previous[index] if previous else 0
        upper_left = previous[index - channels] if previous and index >= channels else 0
        if filter_type == 1:
            row[index] = (row[index] + left) & 0xFF
        elif filter_type == 2:
            row[index] = (row[index] + up) & 0xFF
        elif filter_type == 3:
            row[index] = (row[index] + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            row[index] = (row[index] + _paeth(left, up, upper_left)) & 0xFF
        else:
            raise ValueError(f"Unsupported PNG filter type: {filter_type}")
    return row


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    left_dist = abs(estimate - left)
    up_dist = abs(estimate - up)
    upper_left_dist = abs(estimate - upper_left)
    if left_dist <= up_dist and left_dist <= upper_left_dist:
        return left
    if up_dist <= upper_left_dist:
        return up
    return upper_left


def _estimate_background(rows: list[bytes], width: int, height: int) -> int:
    samples: list[int] = []
    sample_size = min(24, width, height)
    positions = (
        (0, 0),
        (max(0, width - sample_size), 0),
        (0, max(0, height - sample_size)),
        (max(0, width - sample_size), max(0, height - sample_size)),
    )
    for start_x, start_y in positions:
        for y in range(start_y, min(height, start_y + sample_size)):
            row = rows[y]
            for x in range(start_x, min(width, start_x + sample_size)):
                samples.append(_brightness(row, x))
    return int(statistics.median(samples)) if samples else 255


def _foreground_threshold(rows: list[bytes], background: int) -> int:
    max_diff = 0
    for row in rows:
        for x in range(0, len(row), 3):
            value = (row[x] * 299 + row[x + 1] * 587 + row[x + 2] * 114) // 1000
            max_diff = max(max_diff, abs(value - background))
    return max(24, int(max_diff * 0.35))


def _brightness(row: bytes, x: int) -> int:
    base = x * 3
    return (row[base] * 299 + row[base + 1] * 587 + row[base + 2] * 114) // 1000


def _is_foreground(rows: list[bytes], x: int, y: int, background: int, threshold: int) -> bool:
    return abs(_brightness(rows[y], x) - background) >= threshold


def _connected_components(
    rows: list[bytes],
    width: int,
    height: int,
    background: int,
    threshold: int,
) -> list[tuple[int, int, int, int, int]]:
    visited = bytearray(width * height)
    components: list[tuple[int, int, int, int, int]] = []
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if visited[index] or not _is_foreground(rows, x, y, background, threshold):
                continue
            stack = [(x, y)]
            visited[index] = 1
            x1 = x2 = x
            y1 = y2 = y
            count = 0
            while stack:
                cx, cy = stack.pop()
                count += 1
                x1 = min(x1, cx)
                x2 = max(x2, cx)
                y1 = min(y1, cy)
                y2 = max(y2, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    next_index = ny * width + nx
                    if visited[next_index] or not _is_foreground(rows, nx, ny, background, threshold):
                        continue
                    visited[next_index] = 1
                    stack.append((nx, ny))
            if count >= 12:
                components.append((x1, y1, x2, y2, count))
    return components


def _classify_component(
    rows: list[bytes],
    component: tuple[int, int, int, int, int],
    background: int,
    threshold: int,
) -> DecodedGlyph | None:
    x1, y1, x2, y2, count = component
    width = x2 - x1 + 1
    height = y2 - y1 + 1
    if width < 2 or height < 2 or count < 12:
        return None
    best: tuple[float, str, int] | None = None
    for char, pattern in _CROPPED_FONT.items():
        if char == " ":
            continue
        pattern_height = len(pattern)
        pattern_width = len(pattern[0]) if pattern else 0
        if pattern_width <= 0 or pattern_height <= 0:
            continue
        scale_candidates = {
            max(1, round(width / pattern_width)),
            max(1, round(height / pattern_height)),
        }
        for scale in scale_candidates:
            expected_width = pattern_width * scale
            expected_height = pattern_height * scale
            tolerance = max(2, scale)
            if abs(width - expected_width) > tolerance or abs(height - expected_height) > tolerance:
                continue
            actual = _component_pattern(rows, x1, y1, pattern_width, pattern_height, scale, background, threshold)
            score = _pattern_distance(actual, pattern)
            candidate = (score, char, scale)
            if best is None or candidate < best:
                best = candidate
    if best is None or best[0] > 0.14:
        return None
    return DecodedGlyph(char=best[1], x1=x1, y1=y1, x2=x2, y2=y2, scale=best[2])


_QT_TEMPLATE_WIDTH = 18
_QT_TEMPLATE_HEIGHT = 28
_QT_TEMPLATE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_QT_TEMPLATE_FONTS = ("Segoe UI",)
_QT_TEMPLATE_CACHE: tuple[NormalizedTemplateGlyph, ...] | None = None
_QT_APPLICATION = None


def _classify_common_font_component(
    rows: list[bytes],
    component: tuple[int, int, int, int, int],
    background: int,
    threshold: int,
) -> DecodedGlyph | None:
    x1, y1, x2, y2, count = component
    width = x2 - x1 + 1
    height = y2 - y1 + 1
    if width < 3 or height < 8 or count < 16:
        return None
    if height > len(rows) * 0.8 or width > (len(rows[0]) // 3) * 0.35:
        return None
    if width / max(1, height) > 1.45:
        return None

    templates = _common_font_template_catalog()
    if not templates:
        return None
    actual = _normalized_component_pattern(
        rows,
        x1,
        y1,
        x2,
        y2,
        background,
        threshold,
        _QT_TEMPLATE_WIDTH,
        _QT_TEMPLATE_HEIGHT,
    )
    actual_bits = _pattern_bits(actual)
    aspect_ratio = width / max(1, height)
    best: tuple[float, NormalizedTemplateGlyph] | None = None
    for template in templates:
        distance = (actual_bits ^ template.bits).bit_count() / (_QT_TEMPLATE_WIDTH * _QT_TEMPLATE_HEIGHT)
        aspect_penalty = min(0.28, abs(aspect_ratio - template.aspect_ratio) * 0.22)
        candidate = (distance + aspect_penalty, template)
        if best is None or candidate[0] < best[0]:
            best = candidate
    if best is None or best[0] > 0.31:
        return None
    return DecodedGlyph(
        char=best[1].char,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        scale=max(1, round(height / 7)),
    )


def _common_font_template_catalog() -> tuple[NormalizedTemplateGlyph, ...]:
    global _QT_TEMPLATE_CACHE
    if _QT_TEMPLATE_CACHE is not None:
        return _QT_TEMPLATE_CACHE
    try:
        _ensure_qt_application()
    except Exception:
        _QT_TEMPLATE_CACHE = ()
        return _QT_TEMPLATE_CACHE

    templates: list[NormalizedTemplateGlyph] = []
    for family in _QT_TEMPLATE_FONTS:
        for point_size in (32,):
            for bold in (False,):
                for char in _QT_TEMPLATE_CHARS:
                    template = _render_common_font_template(
                        char,
                        font_family=family,
                        point_size=point_size,
                        bold=bold,
                    )
                    if template is not None:
                        templates.append(template)
    _QT_TEMPLATE_CACHE = tuple(templates)
    return _QT_TEMPLATE_CACHE


def _render_common_font_template(
    char: str,
    *,
    font_family: str,
    point_size: int,
    bold: bool,
) -> NormalizedTemplateGlyph | None:
    try:
        width, height, rows = _render_system_font_rgb_rows(
            (char,),
            font_family=font_family,
            point_size=point_size,
            bold=bold,
            foreground=(0, 0, 0),
            background=(255, 255, 255),
            min_width=96,
            min_height=96,
            margin=24,
        )
        background = _estimate_background(rows, width, height)
        threshold = _foreground_threshold(rows, background)
        bounds = _foreground_bounds(rows, width, height, background, threshold)
        if bounds is None:
            return None
        x1, y1, x2, y2 = bounds
        pattern = _normalized_component_pattern(
            rows,
            x1,
            y1,
            x2,
            y2,
            background,
            threshold,
            _QT_TEMPLATE_WIDTH,
            _QT_TEMPLATE_HEIGHT,
        )
        aspect_ratio = (x2 - x1 + 1) / max(1, y2 - y1 + 1)
        return NormalizedTemplateGlyph(
            char=char.upper(),
            pattern=pattern,
            bits=_pattern_bits(pattern),
            aspect_ratio=aspect_ratio,
            source=f"{font_family}:{point_size}:{'bold' if bold else 'regular'}",
        )
    except Exception:
        return None


def _component_pattern(
    rows: list[bytes],
    x: int,
    y: int,
    pattern_width: int,
    pattern_height: int,
    scale: int,
    background: int,
    threshold: int,
) -> tuple[str, ...]:
    output: list[str] = []
    image_width = len(rows[0]) // 3
    image_height = len(rows)
    for row_index in range(pattern_height):
        bits: list[str] = []
        for col_index in range(pattern_width):
            dark = 0
            total = 0
            for dy in range(scale):
                py = y + row_index * scale + dy
                if py < 0 or py >= image_height:
                    continue
                for dx in range(scale):
                    px = x + col_index * scale + dx
                    if px < 0 or px >= image_width:
                        continue
                    total += 1
                    if _is_foreground(rows, px, py, background, threshold):
                        dark += 1
            bits.append("1" if total and dark / total >= 0.45 else "0")
        output.append("".join(bits))
    return tuple(output)


def _normalized_component_pattern(
    rows: list[bytes],
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    background: int,
    threshold: int,
    output_width: int,
    output_height: int,
) -> tuple[str, ...]:
    image_width = len(rows[0]) // 3
    image_height = len(rows)
    source_width = max(1, x2 - x1 + 1)
    source_height = max(1, y2 - y1 + 1)
    output: list[str] = []
    for row_index in range(output_height):
        sy1 = y1 + (row_index * source_height) // output_height
        sy2 = y1 + ((row_index + 1) * source_height + output_height - 1) // output_height
        bits: list[str] = []
        for col_index in range(output_width):
            sx1 = x1 + (col_index * source_width) // output_width
            sx2 = x1 + ((col_index + 1) * source_width + output_width - 1) // output_width
            dark = 0
            total = 0
            for py in range(max(0, sy1), min(image_height, sy2)):
                for px in range(max(0, sx1), min(image_width, sx2)):
                    total += 1
                    if _is_foreground(rows, px, py, background, threshold):
                        dark += 1
            bits.append("1" if total and dark / total >= 0.20 else "0")
        output.append("".join(bits))
    return tuple(output)


def _pattern_distance(actual: tuple[str, ...], expected: tuple[str, ...]) -> float:
    total = 0
    misses = 0
    for actual_row, expected_row in zip(actual, expected):
        for actual_bit, expected_bit in zip(actual_row, expected_row):
            total += 1
            if actual_bit != expected_bit:
                misses += 1
    return misses / max(1, total)


def _pattern_bits(pattern: tuple[str, ...]) -> int:
    bits = 0
    for row in pattern:
        for flag in row:
            bits = (bits << 1) | (1 if flag == "1" else 0)
    return bits


def _assemble_lines(glyphs: list[DecodedGlyph]) -> str:
    if not glyphs:
        return ""
    sorted_glyphs = sorted(glyphs, key=lambda glyph: (glyph.y1, glyph.x1))
    lines: list[list[DecodedGlyph]] = []
    for glyph in sorted_glyphs:
        center_y = (glyph.y1 + glyph.y2) / 2
        for line in lines:
            line_center = statistics.mean((item.y1 + item.y2) / 2 for item in line)
            line_scale = max(1, round(statistics.median(item.scale for item in line)))
            if abs(center_y - line_center) <= max(6, line_scale * 4):
                line.append(glyph)
                break
        else:
            lines.append([glyph])
    decoded_lines: list[str] = []
    for line in lines:
        ordered = sorted(line, key=lambda glyph: glyph.x1)
        scale = max(1, round(statistics.median(glyph.scale for glyph in ordered)))
        gaps = [ordered[index].x1 - ordered[index - 1].x2 - 1 for index in range(1, len(ordered))]
        normal_gaps = [gap for gap in gaps if 0 <= gap <= scale * 4]
        normal_gap = int(statistics.median(normal_gaps)) if normal_gaps else scale * 2
        full_width = 5 * scale
        char_advance = full_width + max(1, normal_gap)
        chars: list[str] = [ordered[0].char]
        for index in range(1, len(ordered)):
            gap = ordered[index].x1 - ordered[index - 1].x2 - 1
            space_gap_threshold = max(scale * 2, normal_gap * 2.5, full_width * 0.35)
            if gap > space_gap_threshold:
                spaces = max(1, round((gap - normal_gap) / max(1, char_advance)))
                chars.extend(" " * spaces)
            chars.append(ordered[index].char)
        decoded_lines.append("".join(chars).rstrip())
    return "\n".join(line for line in decoded_lines if line.strip()).strip()


def _crop_pattern(pattern: tuple[str, ...]) -> tuple[str, ...]:
    rows = [index for index, row in enumerate(pattern) if "1" in row]
    cols = [
        index
        for index in range(len(pattern[0]))
        if any(row[index] == "1" for row in pattern)
    ]
    if not rows or not cols:
        return ()
    return tuple(row[min(cols) : max(cols) + 1] for row in pattern[min(rows) : max(rows) + 1])


_CROPPED_FONT = {char: _crop_pattern(pattern) for char, pattern in FONT_5X7.items() if char != " "}


def _foreground_bounds(
    rows: list[bytes],
    width: int,
    height: int,
    background: int,
    threshold: int,
) -> tuple[int, int, int, int] | None:
    xs: list[int] = []
    ys: list[int] = []
    for y in range(height):
        for x in range(width):
            if _is_foreground(rows, x, y, background, threshold):
                xs.append(x)
                ys.append(y)
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _ensure_qt_application():
    global _QT_APPLICATION
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["chaseos-local-image-text-engine"])
    _QT_APPLICATION = app
    return app


def _render_system_font_rgb_rows(
    lines: tuple[str, ...],
    *,
    font_family: str,
    point_size: int,
    foreground: tuple[int, int, int],
    background: tuple[int, int, int],
    min_width: int,
    min_height: int,
    margin: int,
    bold: bool = False,
) -> tuple[int, int, list[bytes]]:
    if sys.platform != "win32":
        image = _render_qt_text_image(
            lines,
            font_family=font_family,
            point_size=point_size,
            foreground=foreground,
            background=background,
            min_width=min_width,
            min_height=min_height,
            margin=margin,
            bold=bold,
        )
        return _qimage_rgb_rows(image)

    import ctypes
    from ctypes import wintypes

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

    class TEXTMETRICW(ctypes.Structure):
        _fields_ = [
            ("tmHeight", wintypes.LONG),
            ("tmAscent", wintypes.LONG),
            ("tmDescent", wintypes.LONG),
            ("tmInternalLeading", wintypes.LONG),
            ("tmExternalLeading", wintypes.LONG),
            ("tmAveCharWidth", wintypes.LONG),
            ("tmMaxCharWidth", wintypes.LONG),
            ("tmWeight", wintypes.LONG),
            ("tmOverhang", wintypes.LONG),
            ("tmDigitizedAspectX", wintypes.LONG),
            ("tmDigitizedAspectY", wintypes.LONG),
            ("tmFirstChar", wintypes.WCHAR),
            ("tmLastChar", wintypes.WCHAR),
            ("tmDefaultChar", wintypes.WCHAR),
            ("tmBreakChar", wintypes.WCHAR),
            ("tmItalic", ctypes.c_byte),
            ("tmUnderlined", ctypes.c_byte),
            ("tmStruckOut", ctypes.c_byte),
            ("tmPitchAndFamily", ctypes.c_byte),
            ("tmCharSet", ctypes.c_byte),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateFontW.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPCWSTR,
    ]
    gdi32.CreateFontW.restype = wintypes.HANDLE
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetTextMetricsW.argtypes = [wintypes.HDC, ctypes.POINTER(TEXTMETRICW)]
    gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(SIZE)]
    gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
    gdi32.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int]

    normalized = tuple(str(line or "").upper() for line in lines) or ("",)
    font_pixel_height = -max(8, round(int(point_size) * 96 / 72))
    weight = 700 if bold else 400
    hdc = gdi32.CreateCompatibleDC(0)
    if not hdc:
        raise RuntimeError("Could not create an in-memory text rendering context.")
    font = gdi32.CreateFontW(font_pixel_height, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 0, font_family)
    old_font = old_bitmap = bitmap = None
    try:
        old_font = gdi32.SelectObject(hdc, font)
        text_metric = TEXTMETRICW()
        if not gdi32.GetTextMetricsW(hdc, ctypes.byref(text_metric)):
            raise RuntimeError("Could not measure local system font text.")
        line_height = max(1, int(text_metric.tmHeight + text_metric.tmExternalLeading))
        line_sizes: list[SIZE] = []
        for line in normalized:
            size = SIZE()
            gdi32.GetTextExtentPoint32W(hdc, line or " ", len(line or " "), ctypes.byref(size))
            line_sizes.append(size)
        width = max(int(min_width), max((int(size.cx) for size in line_sizes), default=1) + margin * 2)
        height = max(int(min_height), line_height * len(normalized) + margin * 2)

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(hdc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise RuntimeError("Could not allocate local system font render buffer.")
        old_bitmap = gdi32.SelectObject(hdc, bitmap)
        buffer = (ctypes.c_ubyte * (width * height * 4)).from_address(bits.value)
        br, bg, bb = background
        for index in range(0, len(buffer), 4):
            buffer[index] = bb
            buffer[index + 1] = bg
            buffer[index + 2] = br
            buffer[index + 3] = 0
        gdi32.SetBkMode(hdc, 1)
        fr, fg, fb = foreground
        gdi32.SetTextColor(hdc, fr | (fg << 8) | (fb << 16))
        y = margin
        for line in normalized:
            gdi32.TextOutW(hdc, margin, y, line, len(line))
            y += line_height

        rows: list[bytes] = []
        for row_index in range(height):
            row = bytearray()
            row_start = row_index * width * 4
            for pixel_index in range(row_start, row_start + width * 4, 4):
                row.extend(
                    (
                        buffer[pixel_index + 2],
                        buffer[pixel_index + 1],
                        buffer[pixel_index],
                    )
                )
            rows.append(bytes(row))
        return width, height, rows
    finally:
        if old_bitmap:
            gdi32.SelectObject(hdc, old_bitmap)
        if old_font:
            gdi32.SelectObject(hdc, old_font)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if font:
            gdi32.DeleteObject(font)
        gdi32.DeleteDC(hdc)


def _render_qt_text_image(
    lines: tuple[str, ...],
    *,
    font_family: str,
    point_size: int,
    foreground: tuple[int, int, int],
    background: tuple[int, int, int],
    min_width: int,
    min_height: int,
    margin: int,
    bold: bool = False,
):
    _ensure_qt_application()
    from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter

    normalized = tuple(str(line or "").upper() for line in lines) or ("",)
    font = QFont(font_family, int(point_size))
    if bold:
        font.setBold(True)
    metrics = QFontMetrics(font)
    line_height = max(1, metrics.lineSpacing())
    width = max(
        int(min_width),
        int(max((metrics.horizontalAdvance(line or " ") for line in normalized), default=1) + margin * 2),
    )
    height = max(
        int(min_height),
        int(line_height * len(normalized) + margin * 2),
    )
    image = QImage(width, height, QImage.Format.Format_RGB888)
    image.fill(QColor(*background))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setFont(font)
    painter.setPen(QColor(*foreground))
    y = margin
    for line in normalized:
        painter.drawText(margin, y + metrics.ascent(), line)
        y += line_height
    painter.end()
    return image


def _qimage_to_png_bytes(image) -> bytes:
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def _qimage_rgb_rows(image) -> tuple[int, int, list[bytes]]:
    width = int(image.width())
    height = int(image.height())
    rows: list[bytes] = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            color = image.pixelColor(x, y)
            row.extend((color.red(), color.green(), color.blue()))
        rows.append(bytes(row))
    return width, height, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract text from ChaseOS pixel-text PNG images.")
    parser.add_argument("image_path")
    args = parser.parse_args(argv)
    try:
        text = extract_text_from_pixel_image(args.image_path)
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
