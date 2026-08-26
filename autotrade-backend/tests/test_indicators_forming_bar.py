"""D5 regression — compute_indicators must be able to drop the forming bar.

Every indicator is read off close[-1] and no caller truncated, so an in-session
run over 1d candles scored a partially-formed daily bar. `exclude_forming_bar`
lets a caller opt out; the default must stay unchanged so the 13 existing
production call sites are untouched.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.indicators import compute_indicators


def _df(n=120, last_close=None):
    rng = np.random.default_rng(7)
    closes = 100 + np.cumsum(rng.normal(0, 1, n))
    if last_close is not None:
        closes[-1] = last_close
    return pd.DataFrame({
        "open":   closes - 0.5,
        "high":   closes + 1.0,
        "low":    closes - 1.0,
        "close":  closes,
        "volume": rng.integers(500_000, 1_500_000, n),
    })


class TestExcludeFormingBar:

    def test_default_is_unchanged(self):
        """Existing call sites must see identical behaviour."""
        df = _df()
        assert compute_indicators(df).rsi == compute_indicators(df, exclude_forming_bar=False).rsi

    def test_equivalent_to_truncating_manually(self):
        df = _df()
        excluded = compute_indicators(df, exclude_forming_bar=True)
        truncated = compute_indicators(df.iloc[:-1])
        assert excluded.rsi == pytest.approx(truncated.rsi, nan_ok=True)
        assert excluded.composite_score == pytest.approx(truncated.composite_score, nan_ok=True)

    def test_forming_bar_actually_changes_the_answer(self):
        """Guards against the flag being silently a no-op."""
        df = _df(last_close=10_000.0)      # absurd forming bar
        with_bar = compute_indicators(df, exclude_forming_bar=False)
        without  = compute_indicators(df, exclude_forming_bar=True)
        assert with_bar.rsi != without.rsi

    def test_input_frame_is_not_mutated(self):
        df = _df()
        before = len(df)
        compute_indicators(df, exclude_forming_bar=True)
        assert len(df) == before

    def test_single_row_frame_is_safe(self):
        """Dropping the only bar must not hand the indicators an empty frame."""
        result = compute_indicators(_df(1), exclude_forming_bar=True)
        assert result is not None

    def test_keyword_only(self):
        """Positional use would silently bind to the wrong argument later."""
        with pytest.raises(TypeError):
            compute_indicators(_df(), True)
