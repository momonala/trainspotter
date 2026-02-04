"""Tests for departure image generation."""

from datetime import datetime
from datetime import timezone

from src.image_generator import QuadrantData
from src.image_generator import render_image


def _sample_quadrants_data() -> list[QuadrantData]:
    return [
        QuadrantData(label="S1/26", arrow="↑", departures=[(3, "S1"), (8, "S26")]),
        QuadrantData(label="S1/26", arrow="↓", departures=[]),
        QuadrantData(label="S8/85", arrow="↑", departures=[(5, "S8")]),
        QuadrantData(label="S8/85", arrow="↻", departures=[(12, "S85"), (15, "S8")]),
    ]


def test_render_image_returns_png_bytes():
    quadrants_data = _sample_quadrants_data()
    ts = datetime(2025, 2, 5, 14, 30, tzinfo=timezone.utc)
    data = render_image(quadrants_data, "Test Station", ts)
    assert isinstance(data, bytes)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_image_empty_departures():
    quadrants_data = [
        QuadrantData(label="S1/26", arrow="↑", departures=[]),
        QuadrantData(label="S1/26", arrow="↓", departures=[]),
        QuadrantData(label="S8/85", arrow="↑", departures=[]),
        QuadrantData(label="S8/85", arrow="↻", departures=[]),
    ]
    ts = datetime(2025, 2, 5, 14, 30, tzinfo=timezone.utc)
    data = render_image(quadrants_data, "Test Station", ts)
    assert isinstance(data, bytes)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
