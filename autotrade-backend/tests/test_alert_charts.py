"""Tests for chart image generation (Phase 4) -- integrations/alerts/charts.py
and telegram_service._post_photo.

Run:
    cd autotrade-backend
    .venv/bin/python -m pytest tests/test_alert_charts.py -v --tb=short
"""
from __future__ import annotations

import io

import pandas as pd
import pytest
from PIL import Image

from integrations.alerts.charts import build_entry_chart


def _assert_valid_png(png_bytes: bytes) -> None:
    assert png_bytes and len(png_bytes) > 0
    img = Image.open(io.BytesIO(png_bytes))
    img.verify()  # raises if the PNG is corrupt/truncated


def test_build_entry_chart_with_candle_data():
    df = pd.DataFrame({"close": [100.0, 101.5, 99.0, 103.0, 105.0, 104.0]})
    png = build_entry_chart("TCS", entry=100.0, stop=95.0, target=110.0, df=df)
    _assert_valid_png(png)


def test_build_entry_chart_without_candle_data():
    """No df available (self-fetch found nothing) -- must still produce a
    valid chart, just with a flat reference line instead of a price series."""
    png = build_entry_chart("TCS", entry=100.0, stop=95.0, target=110.0, df=None)
    _assert_valid_png(png)


def test_build_entry_chart_with_empty_dataframe():
    png = build_entry_chart("TCS", entry=100.0, stop=95.0, target=110.0, df=pd.DataFrame())
    _assert_valid_png(png)


def test_build_entry_chart_short_side():
    """SELL trades have target < entry < stop -- must not crash on that ordering."""
    png = build_entry_chart("TCS", entry=100.0, stop=105.0, target=90.0, df=None)
    _assert_valid_png(png)


@pytest.mark.asyncio
async def test_post_photo_suppressed_in_test_env():
    """Mirrors _post()'s own PYTEST_CURRENT_TEST guard test -- pytest always
    sets this env var, so _post_photo must return None (never actually hit
    the network) when called from inside any test, even without explicit
    mocking."""
    from integrations.telegram_service import _post_photo
    result = await _post_photo(b"not-a-real-png", caption="test")
    assert result is None
