"""Image generation for ESP32 e-ink display.

All arrows are vector-drawn, single bundled font for text, optimized for minimal 1-bit PNG.
"""

import io
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

from .datamodels import Departure
from .utils import bearing_to_cardinal
from .utils import get_initial_bearing

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 400, 300

BADGE_W = 90
BADGE_H = 70
BADGE_R = 16
BADGE_MINUTES_CAP = 99
BADGE_LABEL_FONT_PT = 13
BADGE_OUTLINE_WIDTH = 1
BADGE_DIGIT_KERN_PX = -4

FONT_PATH = Path(__file__).parent / "fonts" / "NotoSans-Bold.ttf"

TITLE_FONT_PT = 18
LABEL_FONT_PT = 40
BADGE_FONT_PT = 58
TIME_SIZE_RATIO = 0.98
DATE_TIME_GAP_PX = 8
HEADER_BASELINE_OFFSET_PX = 4
DIVIDER_LINE_WIDTH = 2
ARROW_STROKE_WIDTH = 3
ARROW_INSET_PX = 4
RING_THICKNESS = 5
ARROWHEAD_HALF_WIDTH = 12
ARROW_SIZE_RATIO_NUM = 9
ARROW_SIZE_RATIO_DENOM = 10

MONTH_ABBREV = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass
class Layout:
    """Layout constants."""

    margin: int = 16
    padding: int = 8
    badge_gap: int = 6
    header_height: int = 42
    header_top: int = 12
    quadrant_header_height: int = 52
    quadrant_header_top: int = 8
    arrow_label_gap: int = 12
    arrow_size: int = 48


@dataclass
class QuadrantData:
    """One quadrant's display data: label, arrow, and departure minutes with line names."""

    label: str
    arrow: str
    departures: list[tuple[int, str]]  # (minutes, line_name)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load bundled Noto Sans font, fallback to Pillow default."""
    if FONT_PATH.exists():
        try:
            return ImageFont.truetype(str(FONT_PATH), size)
        except OSError:
            logger.warning(f"Failed to load font from {FONT_PATH}, using default")
    return ImageFont.load_default()


def compute_direction(dep: Departure) -> str | None:
    """Return direction symbol (↑ ↓ ↻ ↺ ← →) from departure bearing and line, or None if unknown."""
    if not dep.stop or not dep.stop.location or not dep.destination or not dep.destination.location:
        return None
    line = dep.line.name
    start = dep.stop.location
    end = dep.destination.location
    bearing = get_initial_bearing(start.latitude, start.longitude, end.latitude, end.longitude)
    cardinal = bearing_to_cardinal(bearing)
    if line == "S41":
        return "↻"
    if line == "S42":
        return "↺"
    if cardinal in ("→", "↓") and line in ("S8", "S85"):
        return "↻"
    if cardinal == "←" and line == "S1":
        return "↓"
    return cardinal


def filter_and_group(
    departures: list[Departure],
    now: datetime,
    quadrants_config: list[dict],
    min_minutes: int = 5,
    max_per_quadrant: int = 2,
) -> list[QuadrantData]:
    """Filter departures by min_minutes and group into quadrants per config. Returns one QuadrantData per config entry."""
    lines_by_key = {q["key"]: set(q["lines"]) for q in quadrants_config}
    direction_by_key = {q["key"]: q["direction"] for q in quadrants_config}
    groups: dict[str, list[tuple[int, str]]] = {q["key"]: [] for q in quadrants_config}

    for dep in departures:
        line = dep.line.name
        direction = compute_direction(dep)
        if not direction:
            continue

        minutes = int((dep.when - now).total_seconds() / 60)
        if minutes <= min_minutes:
            continue

        for key, lines in lines_by_key.items():
            if line in lines and direction_by_key[key] == direction:
                groups[key].append((minutes, line))
                break

    for key in groups:
        groups[key] = sorted(groups[key], key=lambda x: x[0])[:max_per_quadrant]

    return [QuadrantData(label=q["label"], arrow=q["direction"], departures=groups[q["key"]]) for q in quadrants_config]


def _draw_arrow(draw: ImageDraw.ImageDraw, x: int, y: int, direction: str, size: int = 22) -> int:
    """Draw arrow as vector only (no font glyphs). Returns width used."""
    w, h = size, size
    sw = ARROW_STROKE_WIDTH
    inset = ARROW_INSET_PX
    half = ARROWHEAD_HALF_WIDTH

    if direction == "←":
        sx = x + w * 2 // 3
        draw.rectangle([x, y + h // 2 - sw // 2, sx, y + h // 2 + sw // 2], fill=0)
        draw.polygon([(x, y + h // 2), (x + w // 3, y + inset), (x + w // 3, y + h - inset)], fill=0)
        return w + inset
    if direction == "↺":
        cx_ring = x + w // 2
        cy_ring = y + h // 2
        r_outer = (min(w, h) // 2 - 2) * ARROW_SIZE_RATIO_NUM // ARROW_SIZE_RATIO_DENOM
        r_inner = max(1, r_outer - RING_THICKNESS)
        r_mid = (r_outer + r_inner) // 2
        outer_bbox = [cx_ring - r_outer, cy_ring - r_outer, cx_ring + r_outer, cy_ring + r_outer]
        draw.ellipse(outer_bbox, fill=0)
        draw.ellipse(
            [cx_ring - r_inner, cy_ring - r_inner, cx_ring + r_inner, cy_ring + r_inner],
            fill=1,
        )
        draw.pieslice(outer_bbox, start=90, end=270, fill=1)
        tip_x = cx_ring - r_mid
        tip_y = cy_ring
        draw.polygon(
            [(tip_x - inset, tip_y), (tip_x + half, tip_y - half), (tip_x + half, tip_y + half)],
            fill=0,
        )
        return w + inset
    if direction == "↓":
        sy = y + h * 2 // 3
        draw.rectangle([x + w // 2 - sw // 2, y, x + w // 2 + sw // 2, sy], fill=0)
        draw.polygon([(x + w // 2, y + h), (x + inset, y + h - h // 3), (x + w - inset, y + h - h // 3)], fill=0)
        return w + inset
    if direction == "↑":
        sy = y + h // 3
        draw.rectangle([x + w // 2 - sw // 2, sy, x + w // 2 + sw // 2, y + h], fill=0)
        draw.polygon([(x + w // 2, y), (x + inset, y + h // 3), (x + w - inset, y + h // 3)], fill=0)
        return w + inset
    if direction == "↻":
        cx_ring = x + w // 2
        cy_ring = y + h // 2
        r_outer = min(w, h) // 2 - 2
        r_inner = max(1, r_outer - RING_THICKNESS)
        r_mid = (r_outer + r_inner) // 2
        outer_bbox = [cx_ring - r_outer, cy_ring - r_outer, cx_ring + r_outer, cy_ring + r_outer]
        draw.ellipse(outer_bbox, fill=0)
        draw.ellipse(
            [cx_ring - r_inner, cy_ring - r_inner, cx_ring + r_inner, cy_ring + r_inner],
            fill=1,
        )
        draw.pieslice(outer_bbox, start=15, end=60, fill=1)
        tip_x = cx_ring + r_mid
        tip_y = cy_ring + 10
        base_y = cy_ring - 2
        draw.polygon(
            [(tip_x, tip_y), (tip_x - half, base_y), (tip_x + half, base_y)],
            fill=0,
        )
        return w + inset
    if direction == "→":
        sx = x + w // 3
        draw.rectangle([sx, y + h // 2 - sw // 2, x + w, y + h // 2 + sw // 2], fill=0)
        draw.polygon([(x + w, y + h // 2), (x + w - w // 3, y + inset), (x + w - w // 3, y + h - inset)], fill=0)
        return w + inset
    return 0


def _draw_badge(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    minutes: int,
    number_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    label_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    line_label: str = "",
) -> None:
    """Draw badge: large number + small line name, centered and baseline-aligned."""
    num_str = f"{min(minutes, BADGE_MINUTES_CAP)}"
    suffix = line_label or "m"
    bbox_suffix = draw.textbbox((0, 0), suffix, font=label_font)
    w_suffix = bbox_suffix[2] - bbox_suffix[0]

    if len(num_str) >= 2:
        digit_bboxes = [draw.textbbox((0, 0), d, font=number_font) for d in num_str]
        digit_widths = [bb[2] - bb[0] for bb in digit_bboxes]
        w_num = sum(digit_widths) + BADGE_DIGIT_KERN_PX * (len(num_str) - 1)
        h_num = max(bb[3] - bb[1] for bb in digit_bboxes)
    else:
        bbox_num = draw.textbbox((0, 0), num_str, font=number_font)
        digit_bboxes = [bbox_num]
        digit_widths = [bbox_num[2] - bbox_num[0]]
        w_num = digit_widths[0]
        h_num = bbox_num[3] - bbox_num[1]

    total_w = w_num + w_suffix
    x = cx - BADGE_W // 2
    y = cy - BADGE_H // 2
    draw.rounded_rectangle([x, y, x + BADGE_W, y + BADGE_H], radius=BADGE_R, outline=0, width=BADGE_OUTLINE_WIDTH)
    text_left = x + (BADGE_W - total_w) // 2
    baseline_y = cy + h_num // 2

    cursor_x = text_left
    for i, digit in enumerate(num_str):
        dx = cursor_x - digit_bboxes[i][0]
        dy = baseline_y - digit_bboxes[i][3]
        draw.text((dx, dy), digit, font=number_font, fill=0)
        cursor_x += digit_widths[i] + (BADGE_DIGIT_KERN_PX if i < len(num_str) - 1 else 0)

    suffix_x = cursor_x - bbox_suffix[0]
    suffix_y = baseline_y - bbox_suffix[3]
    draw.text((suffix_x, suffix_y), suffix, font=label_font, fill=0)


def _draw_quadrant(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    arrow: str,
    label: str,
    departures: list[tuple[int, str]],
    layout: Layout,
    fonts: dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    """Draw one quadrant: vector arrow + label + badges."""
    ax = x + layout.padding
    ay = y + layout.quadrant_header_top
    arrow_draw_size = layout.arrow_size * ARROW_SIZE_RATIO_NUM // ARROW_SIZE_RATIO_DENOM
    aw = _draw_arrow(draw, ax, ay, arrow, size=arrow_draw_size)

    label_font = fonts["label"]
    label_bbox = draw.textbbox((0, 0), label, font=label_font)
    arrow_cy = ay + arrow_draw_size // 2
    label_y = arrow_cy - (label_bbox[1] + label_bbox[3]) // 2
    draw.text((ax + aw + layout.arrow_label_gap, label_y), label, font=label_font, fill=0)

    badge_top = y + layout.quadrant_header_height + layout.quadrant_header_top
    badge_cy = badge_top + (h - (badge_top - y) - layout.padding) // 2

    if not departures:
        label_font = fonts["label"]
        bbox = draw.textbbox((0, 0), "—", font=label_font)
        dw, dh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((x + (w - dw) // 2 - bbox[0], badge_cy - dh // 2 - bbox[1]), "—", font=label_font, fill=0)
        return

    badge_font = fonts["badge"]
    badge_label_font = fonts["badge_label"]
    total = BADGE_W * 2 + layout.badge_gap
    start_x = x + (w - total) // 2 + BADGE_W // 2
    minutes_0, line_0 = departures[0]
    _draw_badge(draw, start_x, badge_cy, minutes_0, badge_font, badge_label_font, line_0)
    if len(departures) == 2:
        minutes_1, line_1 = departures[1]
        _draw_badge(
            draw, start_x + BADGE_W + layout.badge_gap, badge_cy, minutes_1, badge_font, badge_label_font, line_1
        )


def render_image(
    quadrants_data: list[QuadrantData],
    station_name: str,
    timestamp: datetime,
) -> bytes:
    """Generate optimized 1-bit PNG for e-ink display. quadrants_data must have 4 items (top-left, top-right, bottom-left, bottom-right)."""
    layout = Layout()
    time_pt = max(1, int(LABEL_FONT_PT * TIME_SIZE_RATIO))
    fonts = {
        "title": _load_font(TITLE_FONT_PT),
        "time": _load_font(time_pt),
        "label": _load_font(LABEL_FONT_PT),
        "date": _load_font(LABEL_FONT_PT // 2),
        "badge": _load_font(BADGE_FONT_PT),
        "badge_label": _load_font(BADGE_LABEL_FONT_PT),
    }

    img = Image.new("1", (WIDTH, HEIGHT), color=1)
    draw = ImageDraw.Draw(img)

    div_y = layout.header_height
    baseline_y = div_y - HEADER_BASELINE_OFFSET_PX

    bbox_station = draw.textbbox((0, 0), station_name, font=fonts["title"])
    station_y = baseline_y - bbox_station[3]
    draw.text((layout.margin, station_y), station_name, font=fonts["title"], fill=0)

    date_str = f"{timestamp.day} {MONTH_ABBREV[timestamp.month - 1]}"
    time_str = timestamp.strftime("%H:%M")
    bbox_date = draw.textbbox((0, 0), date_str, font=fonts["date"])
    bbox_time = draw.textbbox((0, 0), time_str, font=fonts["time"])
    date_w = bbox_date[2] - bbox_date[0]
    time_w = bbox_time[2] - bbox_time[0]
    total_w = date_w + DATE_TIME_GAP_PX + time_w
    time_y = baseline_y - bbox_time[3]
    date_y = baseline_y - bbox_date[3]
    start_x = WIDTH - layout.margin - total_w
    draw.text((start_x, date_y), date_str, font=fonts["date"], fill=0)
    draw.text((start_x + date_w + DATE_TIME_GAP_PX, time_y), time_str, font=fonts["time"], fill=0)

    draw.line([(0, div_y), (WIDTH, div_y)], fill=0, width=DIVIDER_LINE_WIDTH)

    mid_x = WIDTH // 2
    mid_y = (div_y + HEIGHT) // 2
    quad_h = (HEIGHT - div_y) // 2
    draw.line([(mid_x, div_y), (mid_x, HEIGHT)], fill=0, width=DIVIDER_LINE_WIDTH)
    draw.line([(0, mid_y), (WIDTH, mid_y)], fill=0, width=DIVIDER_LINE_WIDTH)
    quadrant_rects = [
        (0, div_y, mid_x, quad_h),
        (mid_x, div_y, mid_x, quad_h),
        (0, mid_y, mid_x, quad_h),
        (mid_x, mid_y, mid_x, quad_h),
    ]
    padded = list(quadrants_data[:4])
    while len(padded) < 4:
        padded.append(QuadrantData(label="—", arrow="↑", departures=[]))
    for (qx, qy, qw, qh), data in zip(quadrant_rects, padded, strict=True):
        _draw_quadrant(draw, qx, qy, qw, qh, data.arrow, data.label, data.departures, layout, fonts)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
