"""Tests for Tier 2 — macro context aggregation and the regime overlay.

The aggregation rules here are not stylistic choices; each one was forced by
replaying the aggregator against real Tier 1 output for 13-18 Aug 2026 (146
distinct macro headlines, scored by the live model). The first version
averaged all themes into one signed number, and on 18-Aug that read NORMAL
(-7.5) on a day carrying four bearish themes — OIL -23, GEOPOLITICS -20,
RATES -13, CURRENCY -10 — because GROWTH +19 and TRADE +7 cancelled them
algebraically. Hence: risks compound, bullish themes never offset them, and
the gating number is separated from the informational one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from engine.agent.market_regime import classify_regime
from engine.macro_context import (
    MAX_REGIME_PENALTY, MacroContext, aggregate_macro_reads,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _read(theme, score, conf=0.8, age_h=0.0, headline="h"):
    return {
        "headline": headline, "theme": theme, "direction":
            "BEARISH" if score < 0 else "BULLISH",
        "macro_score": score, "confidence": conf,
        "ts": NOW - timedelta(hours=age_h), "is_actionable": True,
    }


class TestBullishThemesNeverOffsetRisk:
    """The 18-Aug failure, pinned."""

    def test_bullish_theme_does_not_cancel_bearish_ones(self):
        bearish_only = aggregate_macro_reads([
            _read("OIL", -0.6), _read("GEOPOLITICS", -0.5),
            _read("RATES", -0.4), _read("CURRENCY", -0.3),
        ], now=NOW)
        with_bulls = aggregate_macro_reads([
            _read("OIL", -0.6), _read("GEOPOLITICS", -0.5),
            _read("RATES", -0.4), _read("CURRENCY", -0.3),
            _read("GROWTH", 0.9), _read("TRADE", 0.7),
        ], now=NOW)
        assert with_bulls.risk_score == pytest.approx(bearish_only.risk_score)
        assert with_bulls.regime_penalty == pytest.approx(bearish_only.regime_penalty)

    def test_bias_still_reports_the_bullish_side(self):
        """Bullish macro is not discarded — it just does not gate."""
        ctx = aggregate_macro_reads([_read("GROWTH", 0.9), _read("TRADE", 0.8)], now=NOW)
        assert ctx.bias > 0
        assert ctx.risk_score == 0.0
        assert ctx.regime_penalty == 0.0

    def test_all_bullish_day_applies_no_penalty(self):
        ctx = aggregate_macro_reads([_read("GROWTH", 1.0, conf=1.0)], now=NOW)
        assert ctx.regime_penalty == 0.0


class TestRisksCompound:
    def test_more_bearish_themes_never_lowers_risk(self):
        """Averaging allowed a fifth risk to REDUCE the reading. Monotonicity
        is the property that bug violated."""
        prev = -1.0
        for n in range(1, 6):
            ctx = aggregate_macro_reads(
                [_read(f"T{i}", -0.5) for i in range(n)], now=NOW)
            assert ctx.risk_score >= prev
            prev = ctx.risk_score

    def test_diminishing_returns(self):
        """Broad mild unease must not outrank one severe shock."""
        severe = aggregate_macro_reads([_read("GEOPOLITICS", -0.95, conf=0.95)], now=NOW)
        broad = aggregate_macro_reads(
            [_read(f"T{i}", -0.25) for i in range(5)], now=NOW)
        assert severe.risk_score > broad.risk_score

    def test_worst_headline_wins_within_a_theme(self):
        """A crisis is one severe headline plus ambient follow-up chatter;
        averaging inside a theme would dilute the severe one away."""
        ctx = aggregate_macro_reads([
            _read("OIL", -0.9), _read("OIL", -0.1), _read("OIL", -0.1),
        ], now=NOW)
        one = aggregate_macro_reads([_read("OIL", -0.9)], now=NOW)
        assert ctx.risk_score == pytest.approx(one.risk_score)


class TestConfidenceAndDecay:
    def test_low_confidence_reduces_risk(self):
        hi = aggregate_macro_reads([_read("OIL", -0.8, conf=0.9)], now=NOW)
        lo = aggregate_macro_reads([_read("OIL", -0.8, conf=0.2)], now=NOW)
        assert lo.risk_score < hi.risk_score

    def test_stale_news_decays(self):
        fresh = aggregate_macro_reads([_read("OIL", -0.8, age_h=0)], now=NOW)
        old = aggregate_macro_reads([_read("OIL", -0.8, age_h=24)], now=NOW)
        assert old.risk_score < fresh.risk_score

    def test_naive_timestamp_is_treated_as_utc(self):
        """Mixed tz-awareness caused a real 4,183-row corruption in this repo
        before; a naive ts must not raise or silently skew the decay."""
        r = _read("OIL", -0.8)
        r["ts"] = NOW.replace(tzinfo=None)
        ctx = aggregate_macro_reads([r], now=NOW)
        assert ctx.risk_score > 0


class TestSingleThemeCap:
    def test_single_theme_is_discounted(self):
        """One theme is the shape both syndication and a one-off model error
        produce, so it cannot reach full weight."""
        one = aggregate_macro_reads([_read("OIL", -0.8)], now=NOW)
        two = aggregate_macro_reads([_read("OIL", -0.8), _read("RATES", -0.8)], now=NOW)
        assert one.regime_penalty < two.regime_penalty


class TestEmptyAndDegenerate:
    def test_no_reads_is_stale_and_harmless(self):
        ctx = aggregate_macro_reads([], now=NOW)
        assert ctx.is_stale is True
        assert ctx.regime_penalty == 0.0

    def test_reads_without_scores_are_stale(self):
        ctx = aggregate_macro_reads([{"theme": "OIL", "macro_score": None,
                                      "confidence": 0.8, "ts": NOW}], now=NOW)
        assert ctx.is_stale is True
        assert ctx.regime_penalty == 0.0

    def test_penalty_is_bounded(self):
        ctx = aggregate_macro_reads(
            [_read(f"T{i}", -1.0, conf=1.0) for i in range(8)], now=NOW)
        assert 0.0 <= ctx.regime_penalty <= MAX_REGIME_PENALTY

    def test_roundtrip_through_cache_shape(self):
        ctx = aggregate_macro_reads(
            [_read("OIL", -0.7), _read("RATES", -0.5)], now=NOW)
        back = MacroContext.from_dict(ctx.to_dict())
        assert back.risk_score == pytest.approx(ctx.risk_score)
        assert back.regime_penalty == pytest.approx(ctx.regime_penalty)
        assert back.themes == ctx.themes


class TestFetchCutoffIsNaiveUtc:
    """news_items.published_at is TIMESTAMP WITHOUT TIME ZONE holding UTC.

    Passing a tz-aware bound makes asyncpg raise "can't subtract offset-naive
    and offset-aware datetimes". Because get_macro_context swallows fetch
    errors to stay fail-silent, that would have disabled the entire overlay
    without a single failing test or an error in the logs — it only surfaced
    when real rows were round-tripped through the query. Cheap to pin, so
    pinned.
    """

    @pytest.mark.asyncio
    async def test_cutoff_passed_to_sql_is_naive(self):
        from unittest.mock import AsyncMock, MagicMock

        from engine.macro_context import _fetch_actionable_macro

        captured = {}

        async def _execute(stmt, params=None):
            captured["params"] = params
            res = MagicMock()
            res.mappings.return_value.all.return_value = []
            return res

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_execute)

        await _fetch_actionable_macro(session)
        cutoff = captured["params"]["cutoff"]
        assert cutoff.tzinfo is None, "cutoff must be naive UTC to match the column type"


class TestRegimeOverlayIsDowngradeOnly:
    """The overlay must only ever add caution."""

    @staticmethod
    def _closes(trend=1.0006, n=260, start=100.0):
        return pd.Series([start * (trend ** i) for i in range(n)], dtype=float)

    def test_penalty_lowers_the_composite(self):
        c = self._closes()
        base = classify_regime(c, vix=15.0, macro_penalty=0.0)
        pen = classify_regime(c, vix=15.0, macro_penalty=10.0)
        assert pen.score == pytest.approx(base.score - 10.0, abs=0.11)

    def test_zero_penalty_is_identical_to_before_the_overlay(self):
        """The kill-switch path: disabled overlay must not perturb anything."""
        c = self._closes()
        a = classify_regime(c, vix=15.0)
        b = classify_regime(c, vix=15.0, macro_penalty=0.0)
        assert (a.state, a.score, a.size_mult) == (b.state, b.score, b.size_mult)

    def test_negative_penalty_cannot_upgrade(self):
        """A negative value would ADD to the composite — the one thing this
        overlay must never be able to do, whatever the caller passes."""
        c = self._closes()
        base = classify_regime(c, vix=15.0, macro_penalty=0.0)
        sneaky = classify_regime(c, vix=15.0, macro_penalty=-40.0)
        assert sneaky.score <= base.score

    def test_penalty_is_clamped_at_the_maximum(self):
        c = self._closes()
        at_max = classify_regime(c, vix=15.0, macro_penalty=MAX_REGIME_PENALTY)
        absurd = classify_regime(c, vix=15.0, macro_penalty=500.0)
        assert absurd.score == pytest.approx(at_max.score)

    def test_penalty_can_downgrade_state_near_a_boundary(self):
        """Measured: a 7-point penalty changes the state on 15.8% of the last
        480 sessions, so the overlay is not decorative."""
        c = self._closes(trend=1.00035)
        base = classify_regime(c, vix=15.0, macro_penalty=0.0)
        pen = classify_regime(c, vix=15.0, macro_penalty=MAX_REGIME_PENALTY)
        assert pen.score < base.score
        # Never upgraded.
        order = ["STRONG_BEAR", "WEAK_BEAR", "SIDEWAYS", "MODERATE_BULL", "STRONG_BULL"]
        assert order.index(pen.state) <= order.index(base.state)

    def test_signals_expose_the_overlay(self):
        """The pre-macro composite must stay visible, or a state change becomes
        unexplainable in the logs."""
        c = self._closes()
        r = classify_regime(c, vix=15.0, macro_penalty=6.0)
        assert r.signals["macro_penalty"] == pytest.approx(6.0)
        assert r.signals["composite_pre_macro"] > r.signals["composite"]
