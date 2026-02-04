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

# Badges
BADGE_W = 86
BADGE_H = 68
BADGE_R = 14

FONT_PATH = Path(__file__).parent / "fonts" / "NotoSans-Bold.ttf"


@dataclass
class Layout:
    """Layout constants."""

    margin: int = 16
    padding: int = 6
    badge_gap: int = 12
    header_height: int = 42
    header_top: int = 12
    quadrant_header_height: int = 52
    quadrant_header_top: int = 8
    arrow_label_gap: int = 10
    arrow_size: int = 40


@dataclass
class QuadrantData:
    """One quadrant's display data: label, arrow, and departure minutes."""

    label: str
    arrow: str
    minutes_list: list[int]


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
    groups: dict[str, list[int]] = {q["key"]: [] for q in quadrants_config}

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
                groups[key].append(minutes)
                break

    for key in groups:
        groups[key] = sorted(groups[key])[:max_per_quadrant]

    return [
        QuadrantData(label=q["label"], arrow=q["direction"], minutes_list=groups[q["key"]]) for q in quadrants_config
    ]


def _draw_arrow(draw: ImageDraw.ImageDraw, x: int, y: int, direction: str, size: int = 22) -> int:
    """Draw arrow as vector only (no font glyphs). Returns width used."""
    w, h = size, size
    sw = 3

    if direction == "←":
        sx = x + w * 2 // 3
        draw.rectangle([x, y + h // 2 - sw // 2, sx, y + h // 2 + sw // 2], fill=0)
        draw.polygon([(x, y + h // 2), (x + w // 3, y + 4), (x + w // 3, y + h - 4)], fill=0)
        return w + 4
    if direction == "↺":
        cx_ring = x + w // 2
        cy_ring = y + h // 2
        r_outer = (min(w, h) // 2 - 2) * 9 // 10
        ring_thick = 4
        r_inner = max(1, r_outer - ring_thick)
        r_mid = (r_outer + r_inner) // 2
        outer_bbox = [cx_ring - r_outer, cy_ring - r_outer, cx_ring + r_outer, cy_ring + r_outer]
        draw.ellipse(outer_bbox, fill=0)
        draw.ellipse(
            [cx_ring - r_inner, cy_ring - r_inner, cx_ring + r_inner, cy_ring + r_inner],
            fill=1,
        )
        # Remove right half of ring so arc is on the left (PIL: 0°=3h, 90°=6h). Keep left: 270°–90°.
        draw.pieslice(outer_bbox, start=90, end=270, fill=1)
        # Anticlockwise arrowhead at 9 o'clock, tip pointing left
        tip_x = cx_ring - r_mid
        tip_y = cy_ring
        half_w = 12
        draw.polygon(
            [
                (tip_x - 4, tip_y),
                (tip_x + half_w, tip_y - half_w),
                (tip_x + half_w, tip_y + half_w),
            ],
            fill=0,
        )
        return w + 4
    if direction == "↓":
        sy = y + h * 2 // 3
        draw.rectangle([x + w // 2 - sw // 2, y, x + w // 2 + sw // 2, sy], fill=0)
        draw.polygon([(x + w // 2, y + h), (x + 4, y + h - h // 3), (x + w - 4, y + h - h // 3)], fill=0)
        return w + 4
    if direction == "↑":
        sy = y + h // 3
        draw.rectangle([x + w // 2 - sw // 2, sy, x + w // 2 + sw // 2, y + h], fill=0)
        draw.polygon([(x + w // 2, y), (x + 4, y + h // 3), (x + w - 4, y + h // 3)], fill=0)
        return w + 4
    if direction == "↻":
        cx_ring = x + w // 2
        cy_ring = y + h // 2
        r_outer = min(w, h) // 2 - 2
        ring_thick = 4
        r_inner = max(1, r_outer - ring_thick)
        r_mid = (r_outer + r_inner) // 2
        outer_bbox = [cx_ring - r_outer, cy_ring - r_outer, cx_ring + r_outer, cy_ring + r_outer]
        draw.ellipse(outer_bbox, fill=0)
        draw.ellipse(
            [cx_ring - r_inner, cy_ring - r_inner, cx_ring + r_inner, cy_ring + r_inner],
            fill=1,
        )
        # Remove ~60° of ring near the arrow (PIL: 0°=3 o'clock, 90°=6 o'clock)
        draw.pieslice(outer_bbox, start=15, end=60, fill=1)
        # Clockwise arrowhead on the right (3 o'clock), tip pointing down
        tip_x = cx_ring + r_mid
        tip_y = cy_ring + 10
        base_y = cy_ring - 2
        half_w = 12
        draw.polygon(
            [
                (tip_x, tip_y),
                (tip_x - half_w, base_y),
                (tip_x + half_w, base_y),
            ],
            fill=0,
        )
        return w + 4
    if direction == "→":
        sx = x + w // 3
        draw.rectangle([sx, y + h // 2 - sw // 2, x + w, y + h // 2 + sw // 2], fill=0)
        draw.polygon([(x + w, y + h // 2), (x + w - w // 3, y + 4), (x + w - w // 3, y + h - 4)], fill=0)
        return w + 4
    return 0


def _draw_badge(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    minutes: int,
    number_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    m_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    """Draw badge with fixed size; number + small 'm' centered, baselines aligned."""
    num_str = f"{min(minutes, 99)}"
    m_str = "m"
    bbox_num = draw.textbbox((0, 0), num_str, font=number_font)
    bbox_m = draw.textbbox((0, 0), m_str, font=m_font)
    w_num = bbox_num[2] - bbox_num[0]
    h_num = bbox_num[3] - bbox_num[1]
    w_m = bbox_m[2] - bbox_m[0]
    total_w = w_num + w_m
    x = cx - BADGE_W // 2
    y = cy - BADGE_H // 2
    draw.rounded_rectangle([x, y, x + BADGE_W, y + BADGE_H], radius=BADGE_R, fill=0)
    text_left = x + (BADGE_W - total_w) // 2
    baseline_y = cy + h_num // 2
    num_x = text_left - bbox_num[0]
    num_y = baseline_y - bbox_num[3]
    m_x = text_left + w_num - bbox_m[0]
    m_y = baseline_y - bbox_m[3]
    draw.text((num_x, num_y), num_str, font=number_font, fill=1)
    draw.text((m_x, m_y), m_str, font=m_font, fill=1)


def _draw_quadrant(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    arrow: str,
    label: str,
    minutes_list: list[int],
    layout: Layout,
    fonts: dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    """Draw one quadrant: vector arrow + label + badges."""
    ax = x + layout.padding
    ay = y + layout.quadrant_header_top
    arrow_draw_size = layout.arrow_size * 9 // 10  # 10% smaller
    aw = _draw_arrow(draw, ax, ay, arrow, size=arrow_draw_size)

    label_font = fonts["label"]
    label_bbox = draw.textbbox((0, 0), label, font=label_font)
    arrow_cy = ay + arrow_draw_size // 2
    label_y = arrow_cy - (label_bbox[1] + label_bbox[3]) // 2
    draw.text((ax + aw + layout.arrow_label_gap, label_y), label, font=label_font, fill=0)

    badge_top = y + layout.quadrant_header_height + layout.quadrant_header_top
    badge_cy = badge_top + (h - (badge_top - y) - layout.padding) // 2

    if not minutes_list:
        label_font = fonts["label"]
        bbox = draw.textbbox((0, 0), "—", font=label_font)
        dw, dh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((x + (w - dw) // 2 - bbox[0], badge_cy - dh // 2 - bbox[1]), "—", font=label_font, fill=0)
        return

    badge_font = fonts["badge"]
    badge_m_font = fonts["badge_m"]
    total = BADGE_W * 2 + layout.badge_gap
    start_x = x + (w - total) // 2 + BADGE_W // 2
    _draw_badge(draw, start_x, badge_cy, minutes_list[0], badge_font, badge_m_font)
    if len(minutes_list) == 2:
        _draw_badge(draw, start_x + BADGE_W + layout.badge_gap, badge_cy, minutes_list[1], badge_font, badge_m_font)


def render_image(
    quadrants_data: list[QuadrantData],
    station_name: str,
    timestamp: datetime,
) -> bytes:
    """Generate optimized 1-bit PNG for e-ink display. quadrants_data must have 4 items (top-left, top-right, bottom-left, bottom-right)."""
    layout = Layout()
    badge_num_pt = 50
    label_num_pt = 35
    fonts = {
        "title": _load_font(22),
        "time": _load_font(15),
        "label": _load_font(label_num_pt),
        "badge": _load_font(badge_num_pt),
        "badge_m": _load_font(badge_num_pt // 3),
    }

    img = Image.new("1", (WIDTH, HEIGHT), color=1)
    draw = ImageDraw.Draw(img)

    # Header: station name
    draw.text((layout.margin, layout.header_top), station_name, font=fonts["title"], fill=0)

    # Header: timestamp
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    time_text = f"{timestamp.day} {months[timestamp.month - 1]} {timestamp.strftime('%H:%M')}"
    bbox = draw.textbbox((0, 0), time_text, font=fonts["title"])
    tx = WIDTH - layout.margin - (bbox[2] - bbox[0])
    draw.text((tx, layout.header_top), time_text, font=fonts["title"], fill=0)

    # Divider line
    div_y = layout.header_height
    draw.line([(layout.margin, div_y), (WIDTH - layout.margin, div_y)], fill=0, width=1)

    # Quadrant dividers
    mid_x = WIDTH // 2
    mid_y = (div_y + HEIGHT) // 2
    quad_h = (HEIGHT - div_y) // 2
    draw.line([(mid_x, div_y), (mid_x, HEIGHT)], fill=0, width=1)
    draw.line([(layout.margin, mid_y), (WIDTH - layout.margin, mid_y)], fill=0, width=1)

    # Draw quadrants in order: top-left, top-right, bottom-left, bottom-right (pad to 4 if needed)
    quadrant_rects = [
        (0, div_y, mid_x, quad_h),
        (mid_x, div_y, mid_x, quad_h),
        (0, mid_y, mid_x, quad_h),
        (mid_x, mid_y, mid_x, quad_h),
    ]
    padded = list(quadrants_data[:4])
    while len(padded) < 4:
        padded.append(QuadrantData(label="—", arrow="↑", minutes_list=[]))
    for (qx, qy, qw, qh), data in zip(quadrant_rects, padded, strict=True):
        _draw_quadrant(draw, qx, qy, qw, qh, data.arrow, data.label, data.minutes_list, layout, fonts)

    # Optimize PNG for minimal size
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    logger.info(f"Generated PNG: {len(data)/1024:.1f}KB")
    return data
