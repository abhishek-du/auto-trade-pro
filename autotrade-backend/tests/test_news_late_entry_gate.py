"""Tests for the news-entry chase guards (2026-08-18).

Two gates sit in news_discovery_engine before a news trade executes:

  1c. MULTI-SESSION — has the catalyst already played out over the last few
      SESSIONS (including the overnight gap)?
  1b. INTRADAY      — has it played out in the last ~30 minutes?

Only 1b existed. It missed BSE.NS on 18-Aug in two independent ways:

  * It reads 15-minute bars, and none were stored for that day, so
    `if _candles:` was False and the guard was skipped entirely — a
    fail-open that produced no log line and no trade rejection.
  * Even with data, ~30 minutes cannot see a multi-session move. Jefferies
    downgraded BSE on 17-Aug; it had already fallen 7.1% across four
    sessions (11-Aug -2.18%, 12-Aug -0.66%, 13-Aug -1.62%, 16-Aug -2.86%).
    We shorted the next morning at 3263 — below the 3283 the news itself
    quoted as the low — and it bounced.

The intraday gate's own docstring already named this failure mode
("TVSMOTOR at the day high after a 2-session +10% run"), so the intent
predates the bug; only the 30-minute half was implemented.

These tests exercise the decision rule directly against the real numbers,
rather than mocking the whole execution path.
"""
from __future__ import annotations

import pytest

from utils.config import settings


def _would_block(side: str, entry: float, ref_close: float,
                 max_move_pct: float | None = None) -> bool:
    """The multi-session gate's decision rule, mirrored from
    news_discovery_engine so the thresholds stay honest to config."""
    max_move = (max_move_pct
                if max_move_pct is not None
                else float(getattr(settings, "NEWS_MAX_MULTISESSION_MOVE_PCT", 5.0))) / 100.0
    if ref_close <= 0:
        return False
    move = (entry - ref_close) / ref_close
    return (side == "BUY" and move > max_move) or (side == "SELL" and move < -max_move)


class TestMultiSessionGate:
    def test_blocks_the_bse_short_that_motivated_this(self):
        """The real trade: entry 3263.32 against a close of 3526.00 three
        sessions earlier — a 7.45% fall already banked before we shorted."""
        assert _would_block("SELL", entry=3263.32, ref_close=3526.00) is True

    def test_allows_a_long_that_has_not_run(self):
        """HILINFRA, opened the same morning on an earnings beat: +2.56% over
        the window. A genuine catalyst that has not been priced in yet must
        still trade — the gate is a chase filter, not a blanket block."""
        assert _would_block("BUY", entry=46.11, ref_close=44.96) is False

    def test_blocks_an_extended_long(self):
        """Mirror image of the BSE case on the buy side."""
        assert _would_block("BUY", entry=115.0, ref_close=100.0) is True

    def test_does_not_block_a_move_against_the_trade(self):
        """A stock that FELL is not 'chased' by buying it — the gate must only
        fire on moves in the trade's own direction, or it would veto exactly
        the dip-entries we want."""
        assert _would_block("BUY", entry=90.0, ref_close=100.0) is False
        assert _would_block("SELL", entry=110.0, ref_close=100.0) is False

    @pytest.mark.parametrize("side,entry,ref", [
        ("BUY", 104.9, 100.0),   # +4.9%, just inside
        ("SELL", 95.1, 100.0),   # -4.9%, just inside
    ])
    def test_just_inside_the_threshold_is_allowed(self, side, entry, ref):
        assert _would_block(side, entry, ref) is False

    @pytest.mark.parametrize("side,entry,ref", [
        ("BUY", 105.1, 100.0),
        ("SELL", 94.9, 100.0),
    ])
    def test_just_outside_the_threshold_is_blocked(self, side, entry, ref):
        assert _would_block(side, entry, ref) is True

    def test_zero_or_missing_reference_fails_open(self):
        """A data gap must not halt all news trading. authorize_trade_intent is
        the gate that fails closed; this one is a timing filter."""
        assert _would_block("SELL", entry=3263.32, ref_close=0.0) is False


class TestConfigIsSane:
    def test_thresholds_are_present_and_ordered(self):
        intraday = float(settings.NEWS_MAX_PRE_ENTRY_SPIKE_PCT)
        multi = float(settings.NEWS_MAX_MULTISESSION_MOVE_PCT)
        look = int(settings.NEWS_MULTISESSION_LOOKBACK_DAYS)
        assert 0 < intraday < 100
        assert 0 < multi < 100
        assert look >= 1
        # A multi-session window covers more time than 30 minutes, so its
        # tolerance must be the looser of the two — otherwise the wider window
        # would veto everything the narrow one already permits.
        assert multi >= intraday
