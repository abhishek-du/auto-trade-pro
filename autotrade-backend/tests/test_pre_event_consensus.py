"""Tests for engine/pre_event_expectation_gap/consensus.py (P2-1, 2026-08-17).

The provider is the thing that finally makes "expectation gap" mean what the
strategy's name claims. Its most important property is knowing when it does NOT
have an answer: coverage of Indian small/mid-caps is thin (0 of the 11-name
forensic loss cluster has any analyst coverage), so returning None is the
common case and must be cheap, quiet, and never fabricate an anchor.

No network: yfinance is always mocked.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import engine.pre_event_expectation_gap.consensus as cons


class _FakeEstimates:
    """Minimal stand-in for the yfinance earnings_estimate DataFrame."""

    def __init__(self, rows: dict):
        self._rows = rows
        self.empty = not rows
        self.index = list(rows.keys())

    @property
    def loc(self):
        outer = self

        class _Loc:
            def __getitem__(self, key):
                period, col = key
                return outer._rows[period][col]
        return _Loc()


def _patch_yf(rows):
    ticker = MagicMock()
    ticker.earnings_estimate = _FakeEstimates(rows)
    mod = MagicMock()
    mod.Ticker = MagicMock(return_value=ticker)
    return patch.dict("sys.modules", {"yfinance": mod})


def _no_redis():
    """Force the cache to be unavailable so tests exercise the fetch path.
    Mirrors production behaviour when Redis is down -- must degrade, not fail."""
    return patch("utils.cache.get_redis", side_effect=RuntimeError("no redis"))


class TestConsensusFetch:
    @pytest.mark.asyncio
    async def test_returns_growth_and_timestamp_when_well_covered(self):
        rows = {"0q": {"growth": 0.2172, "numberOfAnalysts": 9}}
        with _patch_yf(rows), _no_redis():
            out = await cons.fetch_consensus_growth("ZYDUSLIFE.NS", want_annual=False)
        assert out is not None
        growth, known_at = out
        assert growth == pytest.approx(0.2172)
        assert isinstance(known_at, datetime)

    @pytest.mark.asyncio
    async def test_thin_coverage_is_treated_as_unavailable(self):
        """1-2 analysts is not a market consensus -- the whole point of the
        anchor is that it represents what the MARKET expects."""
        rows = {"0q": {"growth": 0.65, "numberOfAnalysts": 1}}
        with _patch_yf(rows), _no_redis():
            assert await cons.fetch_consensus_growth("OIL.NS", want_annual=False) is None

    @pytest.mark.asyncio
    async def test_nan_growth_is_unavailable(self):
        """yfinance yields NaN rather than None for missing values, and
        NaN != NaN -- a plain `if not growth` check would let it through."""
        nan = float("nan")
        with _patch_yf({"0q": {"growth": nan, "numberOfAnalysts": 5}}), _no_redis():
            assert await cons.fetch_consensus_growth("CPPLUS.NS", want_annual=False) is None

    @pytest.mark.asyncio
    async def test_nan_analyst_count_is_unavailable(self):
        nan = float("nan")
        with _patch_yf({"0q": {"growth": 0.2, "numberOfAnalysts": nan}}), _no_redis():
            assert await cons.fetch_consensus_growth("EPACKPEB.NS", want_annual=False) is None

    @pytest.mark.asyncio
    async def test_uncovered_symbol_returns_none(self):
        """The common case for this universe -- empty frame, no exception."""
        with _patch_yf({}), _no_redis():
            assert await cons.fetch_consensus_growth("GENESYS.NS", want_annual=False) is None

    @pytest.mark.asyncio
    async def test_annual_and_quarterly_select_different_periods(self):
        """The consensus must match the nowcast's own dimension -- comparing a
        quarterly implied trend against an annual consensus is meaningless."""
        rows = {"0q": {"growth": 0.10, "numberOfAnalysts": 5},
                "0y": {"growth": 0.40, "numberOfAnalysts": 5}}
        with _patch_yf(rows), _no_redis():
            q = await cons.fetch_consensus_growth("HAL.NS", want_annual=False)
            y = await cons.fetch_consensus_growth("HAL.NS", want_annual=True)
        assert q[0] == pytest.approx(0.10)
        assert y[0] == pytest.approx(0.40)

    @pytest.mark.asyncio
    async def test_missing_requested_period_is_unavailable(self):
        """Annual asked for, only quarterly published -> None, not a silent
        substitution of the wrong dimension."""
        with _patch_yf({"0q": {"growth": 0.10, "numberOfAnalysts": 9}}), _no_redis():
            assert await cons.fetch_consensus_growth("HAL.NS", want_annual=True) is None

    @pytest.mark.asyncio
    async def test_provider_exception_returns_none(self):
        mod = MagicMock()
        mod.Ticker = MagicMock(side_effect=RuntimeError("429 rate limited"))
        with patch.dict("sys.modules", {"yfinance": mod}), _no_redis():
            assert await cons.fetch_consensus_growth("ANY.NS", want_annual=False) is None


class TestCaching:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_the_provider(self):
        import json
        redis = MagicMock()
        redis.get = AsyncMock(return_value=json.dumps(
            {"growth": 0.31, "known_at": datetime(2026, 8, 17, 10, 0).isoformat()}))
        redis.set = AsyncMock()
        mod = MagicMock()
        with patch("utils.cache.get_redis", return_value=redis), \
             patch.dict("sys.modules", {"yfinance": mod}):
            out = await cons.fetch_consensus_growth("HAL.NS", want_annual=False)
        assert out[0] == pytest.approx(0.31)
        mod.Ticker.assert_not_called()

    @pytest.mark.asyncio
    async def test_misses_are_negatively_cached(self):
        """~70% of this universe has no coverage; without negative caching every
        scan would re-hit a rate-limited API for every uncovered symbol."""
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()
        with patch("utils.cache.get_redis", return_value=redis), _patch_yf({}):
            assert await cons.fetch_consensus_growth("GENESYS.NS", want_annual=False) is None
        redis.set.assert_awaited_once()
        assert "miss" in redis.set.await_args.args[1]

    @pytest.mark.asyncio
    async def test_cached_miss_is_honoured(self):
        import json
        redis = MagicMock()
        redis.get = AsyncMock(return_value=json.dumps({"miss": True}))
        redis.set = AsyncMock()
        mod = MagicMock()
        with patch("utils.cache.get_redis", return_value=redis), \
             patch.dict("sys.modules", {"yfinance": mod}):
            assert await cons.fetch_consensus_growth("GENESYS.NS", want_annual=False) is None
        mod.Ticker.assert_not_called()


class TestSymbolNormalisation:
    def test_suffixed_symbols_pass_through(self):
        assert cons._to_yf_symbol("HAL.NS") == "HAL.NS"
        assert cons._to_yf_symbol("DIVISLAB.BO") == "DIVISLAB.BO"

    def test_bare_symbol_gets_nse_suffix(self):
        assert cons._to_yf_symbol("HAL") == "HAL.NS"
