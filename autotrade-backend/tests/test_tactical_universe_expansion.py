"""Path F breadth expansion — universe, F4 trend rules, sector news fallback.

THE SESSION THAT FORCED THIS (2026-08-20)
-----------------------------------------
F1 scanned the 50 highest-turnover NSE names and was blind to 201 of the 206
stocks that actually moved that day, including all 14 sugar/ethanol names (best
turnover rank: BALRAMCHIN at 436). Separately, five sugar headlines were
ingested with tickers extracted and sentiment up to 0.89 and produced ZERO
CausalEvents, so NO EVENT -> NO TRADE kept the news engine out too.

These tests pin the three fixes so a later "simplification" cannot quietly
restore the old blindness.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from engine.event_classifier import detect_sector_theme
from engine.tactical_rules import (
    F4_RULES,
    volume_breakout_5m,
    vwap_crossover_5m,
)


# `closed()` drops the still-forming last bar, so for an n-row frame the newest
# CLOSED bar is index n-2. Getting this wrong silently tests nothing.
def _frame(n: int = 30, base: float = 100.0, vol: float = 1000.0) -> pd.DataFrame:
    return pd.DataFrame({
        "open": [base] * n, "high": [base + 1] * n, "low": [base - 1] * n,
        "close": [base] * n, "volume": [vol] * n,
    })


_LAST_CLOSED = 28          # for the default 30-row frame


class TestUniverseIsNoLongerAFixedTopN:

    def test_settings_describe_a_dynamic_filter_not_a_fixed_size(self):
        from utils.config import settings

        assert settings.TACTICAL_F1_MIN_TURNOVER_CR > 0
        assert settings.TACTICAL_F1_MIN_PRICE > 0
        # The whole point: the cap must be far above the old flat 50.
        assert settings.TACTICAL_F1_MAX_SYMBOLS > 50

    def test_cap_is_large_enough_to_reach_the_names_that_moved(self):
        """BALRAMCHIN sat at turnover rank 436 and was missed by the top-50.

        A cap of 500 admits only ~62 of the 206 movers and 2 of 14 sugar names
        (measured); the owner chose 1500, which reaches ~171 and 11. If someone
        lowers this back under 500, the sugar-cluster class of move is missed
        again -- so the floor is asserted, not the exact value.
        """
        from utils.config import settings

        assert settings.TACTICAL_F1_MAX_SYMBOLS >= 500, (
            "a cap under 500 cannot reach BALRAMCHIN at turnover rank 436"
        )

    def test_executor_calls_the_dynamic_universe(self):
        """The dead-config bug: the three new settings existed but nothing read
        them, because the call site still asked for TACTICAL_F1_UNIVERSE_SIZE
        and fell back to a hardcoded 50."""
        import inspect

        from engine import tactical_executor

        src = inspect.getsource(tactical_executor)
        assert "get_f1_universe(session)" in src
        assert "TACTICAL_F1_UNIVERSE_SIZE" not in src, (
            "executor still reads the retired fixed-size setting"
        )


class TestF4TrendRules:
    """F4 was mean-reversion only, so a sector trending all session produced
    nothing: a fade needs an overbought RSI *against* the move."""

    def test_f4_registry_lists_the_trend_rules(self):
        assert "VOLUME_BREAKOUT" in F4_RULES
        assert "VWAP_CROSSOVER" in F4_RULES
        # the original fade rules must survive the addition
        assert "OVERBOUGHT_FADE" in F4_RULES and "OVERSOLD_REBOUND" in F4_RULES

    def test_breakout_fires_on_new_high_with_volume(self):
        df = _frame()
        df.loc[_LAST_CLOSED, ["high", "close"]] = [110, 109]
        df.loc[_LAST_CLOSED, "volume"] = 3000
        sigs = volume_breakout_5m("X.NS", df, 109.0)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.side == "BUY" and s.sub_pipeline == "F4"
        assert s.is_sane(), "stop/target on the wrong side of entry"
        assert s.stop_loss < s.entry_price < s.target

    def test_breakout_requires_volume(self):
        """A new high on ordinary volume is not a breakout."""
        df = _frame()
        df.loc[_LAST_CLOSED, ["high", "close"]] = [110, 109]
        assert volume_breakout_5m("X.NS", df, 109.0) == []

    def test_breakout_requires_a_new_high(self):
        df = _frame()
        df.loc[_LAST_CLOSED, "volume"] = 5000
        assert volume_breakout_5m("X.NS", df, 100.0) == []

    def test_vwap_needs_two_consecutive_closes_above(self):
        """One close above VWAP is chop; two is a trend read."""
        one = _frame()
        one.loc[_LAST_CLOSED, "close"] = 106
        one.loc[_LAST_CLOSED, "volume"] = 3000
        assert vwap_crossover_5m("X.NS", one, 106.0) == []

        two = _frame()
        two.loc[_LAST_CLOSED - 1, "close"] = 104
        two.loc[_LAST_CLOSED, ["close", "high"]] = [106, 107]
        two.loc[_LAST_CLOSED, "volume"] = 3000
        sigs = vwap_crossover_5m("X.NS", two, 106.0)
        assert len(sigs) == 1 and sigs[0].is_sane()

    def test_flat_tape_produces_nothing(self):
        assert volume_breakout_5m("X.NS", _frame(), 100.0) == []
        assert vwap_crossover_5m("X.NS", _frame(), 100.0) == []


class TestSectorNewsFallback:
    """A CausalEvent AUTHORISES a trade, so this path must fail closed."""

    @pytest.mark.parametrize("headline,expected", [
        ("Sugar stocks Dhampur, Dwarikesh, Bajaj Hindusthan gain up to 13%", "Sugar"),
        ("Bajaj Hind, Dwarikesh, Renuka rally up to 14%; sugar stocks rise", "Sugar"),
        ("Bank stocks lead Nifty higher", "Banking"),
    ])
    def test_detects_real_sector_stories(self, headline, expected):
        assert detect_sector_theme(headline) == expected

    @pytest.mark.parametrize("headline", [
        "Balrampur Chini Q1 profit rises 12 percent",          # single company
        "Kothari Sugars And Chemicals Limited: Amalgamation/Merger",
        "RBI holds repo rate steady",                          # no sector keyword
        "",
    ])
    def test_rejects_non_sector_stories(self, headline):
        """Single-company news must NOT reach the sector path even when the
        company is in a themed industry -- hence the collective-cue rule."""
        assert detect_sector_theme(headline) is None

    def test_sector_map_would_have_been_unsafe(self):
        """Why the theme comes from the headline, not a ticker->sector lookup.

        Every sugar name is absent from both sector maps, so they all resolve to
        the same "Other" bucket -- making "all tickers share a sector" true for
        any set of unmapped, unrelated companies.
        """
        from engine.portfolio_service import NSE_SECTOR_MAP

        for sym in ("BALRAMCHIN.NS", "DWARKESH.NS", "BAJAJHIND.NS"):
            assert sym not in NSE_SECTOR_MAP

    @pytest.mark.asyncio
    async def test_fallback_is_disabled_by_its_flag(self):
        from crawler.event_pipeline import _try_sector_fallback

        with patch("utils.config.settings.NEWS_SECTOR_FALLBACK_ENABLED", False):
            created = await _try_sector_fallback(
                {"headline": "Sugar stocks rally", "articles": [{"id": 1}]},
                "Sugar stocks rally", {}, None,
            )
        assert created is False

    @pytest.mark.asyncio
    async def test_fallback_needs_enough_tickers(self):
        """MIN_TICKERS is 2 because measured extraction produced at most 2 per
        sugar headline; a 3 floor fires on none of them."""
        from crawler.event_pipeline import _try_sector_fallback

        class _Item:
            def __init__(self, tickers, score):
                self.tickers_affected, self.score = tickers, score

        class _Sess:
            def __init__(self): self.added = []
            def add(self, o): self.added.append(o)

        head = "Sugar stocks Dhampur, Dwarikesh gain 13%"
        cluster = {"headline": head, "articles": [{"id": 1}]}

        thin = _Sess()
        assert await _try_sector_fallback(
            cluster, head, {1: _Item(["ONLYONE.NS"], 0.9)}, thin) is False
        assert thin.added == []

        ok = _Sess()
        assert await _try_sector_fallback(
            cluster, head, {1: _Item(["A.NS", "B.NS"], 0.9)}, ok) is True
        ev = ok.added[0]
        assert ev.event_title == "SECTOR_MOMENTUM"
        assert ev.affected_sectors == ["Sugar"]
        assert sorted(ev.bullish_stocks) == ["A.NS", "B.NS"]
        # A sector read must not claim the confidence of a company fact.
        assert ev.importance < 95

    @pytest.mark.asyncio
    async def test_fallback_needs_a_strong_score(self):
        from crawler.event_pipeline import _try_sector_fallback

        class _Item:
            def __init__(self, tickers, score):
                self.tickers_affected, self.score = tickers, score

        class _Sess:
            def __init__(self): self.added = []
            def add(self, o): self.added.append(o)

        head = "Sugar stocks Dhampur, Dwarikesh gain 13%"
        sess = _Sess()
        assert await _try_sector_fallback(
            {"headline": head, "articles": [{"id": 1}]}, head,
            {1: _Item(["A.NS", "B.NS"], 0.1)}, sess) is False
        assert sess.added == []
