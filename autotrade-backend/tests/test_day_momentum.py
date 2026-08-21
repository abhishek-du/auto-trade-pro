"""F1 DAY_MOMENTUM — pure trend capture, no chart pattern required (2026-08-21).

WHY IT EXISTS
-------------
Every other F1 rule needs a SHAPE: an opening-range break, a gap, a pivot touch,
an engulfing candle. A stock that simply grinds up all session on heavy volume
matches none of them.

Measured on the 21-Aug session: of 29 stocks that cleared volume +
intraday-momentum + VWAP screens, F1's existing rules fired on exactly ONE
(NETWEB, and only SCALP). NCC +6.9% on 13.2x volume, JINDALSAW +7.8% on 17.5x,
THOMASCOOK +12.1% — all invisible, with good data and fresh bars.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.tactical_rules import F1_RULES, day_momentum


def _frame(n=60, base=100.0, vol=5000.0, drift=0.0):
    """A rising intraday tape. `closed()` drops the last row, so the newest
    CLOSED bar is index n-2."""
    px = [base + drift * i for i in range(n)]
    return pd.DataFrame({
        "open":  px, "high": [p + 0.3 for p in px], "low": [p - 0.3 for p in px],
        "close": px, "volume": [vol] * n,
    })


def _daily(n=25, vol=20000.0):
    return pd.DataFrame({
        "open": [100.0]*n, "high": [101.0]*n, "low": [99.0]*n,
        "close": [100.0]*n, "volume": [vol]*n,
    })


class TestFiresOnPureTrend:

    def test_a_trending_high_volume_name_fires(self):
        # 60 bars drifting +0.1 => ~+5.9% on the day, closing at the high.
        df = _frame(drift=0.1)
        live = float(df["close"].iloc[-2]) + 0.3
        sigs = day_momentum("X.NS", df, _daily(), live)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.strategy_name == "DAY_MOMENTUM" and s.sub_pipeline == "F1"
        assert s.side == "BUY" and s.is_sane()
        assert s.stop_loss < s.entry_price < s.target

    def test_target_is_at_least_2R(self):
        """Floored at 2R so the rule cannot emit a structurally
        negative-expectancy trade."""
        df = _frame(drift=0.1)
        live = float(df["close"].iloc[-2]) + 0.3
        s = day_momentum("X.NS", df, _daily(), live)[0]
        rr = (s.target - s.entry_price) / (s.entry_price - s.stop_loss)
        assert rr >= 1.99

    def test_registered_in_f1(self):
        assert "DAY_MOMENTUM" in F1_RULES


class TestEachGateBlocks:
    """All four conditions must hold — individually each is common."""

    def test_low_volume_blocks(self):
        df = _frame(drift=0.1, vol=100.0)          # RVOL far below 2
        live = float(df["close"].iloc[-2]) + 0.3
        assert day_momentum("X.NS", df, _daily(), live) == []

    def test_faded_off_the_high_blocks(self):
        """The 21-Aug lesson: THOMASCOOK, KWIL, IIFL and MANINDS all gave back
        their gains within the hour. A rule that buys a stale snapshot would
        have bought the top.

        Deliberately isolates the RANGE gate: the tape carries one thin-volume
        spike that stretches the day's high without moving VWAP much, so the
        live price still clears VWAP and the 2% gain but sits mid-range. An
        earlier version of this test used a bottom-of-range price, which the
        VWAP gate rejected anyway — so it passed even with the range check
        deleted, and proved nothing.
        """
        df = _frame(drift=0.1)
        # One low-volume spike bar: widens the range, barely touches VWAP.
        df.loc[10, "high"] = 200.0
        df.loc[10, "volume"] = 1.0
        hi = float(df["high"].max()); lo = float(df["low"].min())
        live = float(df["close"].iloc[-2])          # ~+5.9%, above VWAP
        range_pos = (live - lo) / (hi - lo)
        assert range_pos < 0.70, "fixture must sit BELOW the range gate"

        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        vwap = float((tp * df["volume"]).sum() / df["volume"].sum())
        assert live >= vwap * 1.005, "fixture must CLEAR the vwap gate"

        assert day_momentum("X.NS", df, _daily(), live) == []

    def test_below_vwap_blocks(self):
        df = _frame(drift=0.1)
        assert day_momentum("X.NS", df, _daily(), float(df["low"].min())) == []

    def test_flat_tape_blocks(self):
        """No gain — volume alone is not momentum."""
        df = _frame(drift=0.0)
        live = float(df["close"].iloc[-2])
        assert day_momentum("X.NS", df, _daily(), live) == []

    def test_missing_daily_history_blocks(self):
        """No 20-day baseline means RVOL is unknowable; do not guess."""
        df = _frame(drift=0.1)
        live = float(df["close"].iloc[-2]) + 0.3
        assert day_momentum("X.NS", df, None, live) == []
        assert day_momentum("X.NS", df, _daily(n=3), live) == []


class TestRvolBaseline:

    def test_today_is_excluded_from_its_own_baseline(self):
        """Including today's bar in the 20-day mean damps the very surge being
        measured — the flaw audit D5 flagged in _momentum_breakout_score."""
        import inspect

        from engine import tactical_rules

        src = inspect.getsource(tactical_rules.day_momentum)
        assert "df_daily.iloc[:-1]" in src


class TestCapacity:
    """The rule is useless if the top-N cut discards it every cycle."""

    def test_signal_capacity_was_raised(self):
        from utils.config import settings

        # 21-Aug measured raw=404 -> kept=15 -> persisted=5: 99% discarded, and
        # GAP_AND_GO (96-99) took the whole top-5 over VOLUME_BREAKOUT (72-74).
        assert settings.TACTICAL_MAX_SIGNALS_PER_CYCLE >= 40
        assert settings.TACTICAL_TOP_N >= 15

    def test_executor_calls_the_rule(self):
        import inspect

        from engine import tactical_executor

        assert "rules.day_momentum(" in inspect.getsource(tactical_executor)


class TestDayMoveReference:
    """The day move is measured from PREVIOUS CLOSE, not the frame's first bar.

    The executor fetches 200 one-minute bars while an NSE session is 375
    minutes, so `df_1m.open.iloc[0]` is a midday bar's open. Measured
    2026-08-21: that made BALRAMCHIN read -0.32% when its real intraday move was
    -2.41% and its day change was -4.92%. Every gain/loss gate was scoring a
    partial window.
    """

    def test_uses_previous_close_not_the_frames_first_bar(self):
        import inspect

        from engine import tactical_rules

        src = inspect.getsource(tactical_rules.day_momentum)
        assert "_prev_close(df_daily" in src
        assert "live_price / day_open" not in src

    def test_prev_close_is_date_aware(self):
        """Whether the last daily bar is TODAY decides which row is the previous
        close. Measured 2026-08-21: the daily backfill was two sessions behind,
        so a blind iloc[-2] reached back an extra day and reported HSCL down
        12.78% against a real -7.07%."""
        import pandas as pd

        from engine.tactical_rules import _prev_close

        old = pd.DataFrame({"close": [750.25, 702.90],
                            "timestamp": pd.to_datetime(["2026-08-18", "2026-08-19"])})
        assert _prev_close(old, 0.0) == pytest.approx(702.90), (
            "stale frame: the newest bar IS the previous close"
        )

        today = pd.Timestamp.utcnow().normalize()
        fresh = pd.DataFrame({"close": [702.90, 654.40],
                              "timestamp": [today - pd.Timedelta(days=1), today]})
        assert _prev_close(fresh, 0.0) == pytest.approx(702.90), (
            "fresh frame: today's bar must be skipped"
        )

    def test_falls_back_when_daily_is_unusable(self):
        from engine.tactical_rules import _prev_close

        assert _prev_close(None, 123.0) == pytest.approx(123.0)
