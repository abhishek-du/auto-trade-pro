"""STEP 2C.2 — one canonical daily writer for NSE equities.

Step 2C.1 found THREE live daily timestamp conventions writing to one table from
eleven possible persistence paths. These tests pin the architecture that replaced
that: a single contract (utils/candle_contract), enforced at the single
persistence choke point (price_feed.save_candles_to_db), with every other daily
path delegating to the one canonical producer.

All deterministic — no live market, no network.
"""
from __future__ import annotations

import datetime as dt
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from utils.candle_contract import (
    CANONICAL_DAILY_UTC_TIME,
    InstrumentClass,
    classify_instrument,
    filter_canonical_candles,
    validate_canonical_daily_equity_candle as validate,
)

CANON = dt.datetime(2026, 9, 3, 3, 45)          # Thursday session, 09:15 IST


def bar(**kw):
    b = dict(symbol="RELIANCE.NS", timeframe="1d", open=10.0, high=12.0,
             low=9.0, close=11.0, volume=100.0, timestamp=CANON)
    b.update(kw)
    return b


# ── Phase B — the contract ───────────────────────────────────────────────────

class TestInstrumentClassification:

    @pytest.mark.parametrize("sym,klass", [
        ("RELIANCE.NS",      InstrumentClass.NSE_EQUITY),
        ("TCS.NS",           InstrumentClass.NSE_EQUITY),
        ("^NSEI",            InstrumentClass.NSE_INDEX),
        ("^NSEBANK",         InstrumentClass.NSE_INDEX),
        ("NIFTYBEES.NS",     InstrumentClass.ETF_OR_INAV),
        ("AONENFINAV.NS",    InstrumentClass.ETF_OR_INAV),
        ("719GS2060-GS.NS",  InstrumentClass.NON_EQUITY_SERIES),
        ("ABC-SG.NS",        InstrumentClass.NON_EQUITY_SERIES),
        ("RELIANCE.BO",      InstrumentClass.NON_NSE),
        ("",                 InstrumentClass.UNKNOWN),
        (None,               InstrumentClass.UNKNOWN),
    ])
    def test_classification(self, sym, klass):
        assert classify_instrument(sym) is klass

    def test_canonical_time_is_session_open(self):
        """09:15 IST == 03:45 UTC. One constant, not restated per module."""
        assert CANONICAL_DAILY_UTC_TIME == dt.time(3, 45)


class TestContractAccepts:

    def test_canonical_equity_daily_bar(self):
        assert validate(bar()) == (True, None)

    @pytest.mark.parametrize("tf", ["1m", "5m", "15m", "1h"])
    def test_intraday_is_not_governed(self, tf):
        """Only '1d' equity rows are governed — intraday passes untouched."""
        ok, why = validate(bar(timeframe=tf, timestamp=dt.datetime(2026, 9, 3, 5, 17)))
        assert ok is True and why is None

    @pytest.mark.parametrize("sym", ["^NSEI", "NIFTYBEES.NS", "719GS2060-GS.NS"])
    def test_non_equity_daily_passes_through(self, sym):
        """Indices/ETFs/debt keep their own handling. ^NSEI has no Upstox key,
        so forcing the equity contract on it would reject legitimate data."""
        ok, _ = validate(bar(symbol=sym, timestamp=dt.datetime(2026, 9, 3, 0, 0)))
        assert ok is True


# ── Phase J — negative tests ─────────────────────────────────────────────────

class TestContractRejects:

    def test_rejects_the_dead_0000_series(self):
        ok, why = validate(bar(timestamp=dt.datetime(2026, 9, 3, 0, 0)))
        assert ok is False and "00:00" in why and "pre-split" in why

    def test_rejects_the_1830_offset_series(self):
        ok, why = validate(bar(timestamp=dt.datetime(2026, 9, 2, 18, 30)))
        assert ok is False and "18:30" in why and "offset" in why

    def test_rejects_an_arbitrary_time(self):
        ok, why = validate(bar(timestamp=dt.datetime(2026, 9, 3, 7, 12)))
        assert ok is False and "not the canonical session open" in why

    def test_rejects_a_future_timestamp(self):
        ok, why = validate(bar(timestamp=dt.datetime(2099, 1, 1, 3, 45)))
        assert ok is False and "future" in why

    def test_rejects_a_weekend_session(self):
        ok, why = validate(bar(timestamp=dt.datetime(2026, 9, 5, 3, 45)),   # Saturday
                           now_utc=dt.datetime(2026, 9, 30))
        assert ok is False and "weekend" in why

    def test_rejects_an_nse_holiday(self):
        """26-Aug-2026 is Id-E-Milad, confirmed against Upstox /market/holidays."""
        ok, why = validate(bar(timestamp=dt.datetime(2026, 8, 26, 3, 45)),
                           holidays={"2026-08-26"})
        assert ok is False and "holiday" in why

    def test_rejects_a_timezone_aware_timestamp(self):
        ok, why = validate(bar(timestamp=dt.datetime(2026, 9, 3, 3, 45, tzinfo=dt.timezone.utc)))
        assert ok is False and "naive UTC" in why

    def test_rejects_a_non_datetime_timestamp(self):
        ok, why = validate(bar(timestamp="2026-09-03T03:45:00"))
        assert ok is False and "expected datetime" in why

    def test_batch_filter_separates_and_counts(self):
        good = bar()
        batch = [good,
                 bar(timestamp=dt.datetime(2026, 9, 2, 18, 30)),
                 bar(timestamp=dt.datetime(2026, 9, 3, 0, 0)),
                 bar(symbol="^NSEI", timestamp=dt.datetime(2026, 9, 3, 0, 0))]
        kept, rejected = filter_canonical_candles(batch, source="unit-test")
        assert len(kept) == 2                      # canonical equity + the index
        assert sum(rejected.values()) == 2


# ── Phase G — the guard sits at the choke point ──────────────────────────────

class TestGuardAtPersistence:

    def test_save_candles_to_db_enforces_by_default(self):
        from crawler.price_feed import save_candles_to_db
        sig = inspect.signature(save_candles_to_db)
        assert sig.parameters["enforce_contract"].default is True
        assert "source" in sig.parameters, "provenance must be recordable"

    def test_guard_is_actually_invoked(self):
        """Statically bind the choke point to the contract module."""
        src = inspect.getsource(
            __import__("crawler.price_feed", fromlist=["x"]).save_candles_to_db)
        assert "filter_canonical_candles" in src
        assert "enforce_contract" in src

    @pytest.mark.asyncio
    async def test_non_canonical_rows_never_reach_the_insert(self):
        from crawler import price_feed
        seen = {}

        class _Sess:
            async def execute(self, stmt):
                seen["called"] = True
                class R: rowcount = 0
                return R()
            async def commit(self): pass
            async def rollback(self): pass

        n = await price_feed.save_candles_to_db(
            [bar(timestamp=dt.datetime(2026, 9, 2, 18, 30))], _Sess(), source="test")
        assert n == 0
        assert "called" not in seen, "a rejected batch must not reach the DB at all"


# ── Phase C/D — delegation and fail-closed ───────────────────────────────────

class TestFetchNseCandlesDelegates:

    def test_equity_daily_delegates_to_the_canonical_producer(self):
        from crawler import india_price_feed as ipf
        src = inspect.getsource(ipf.fetch_nse_candles)
        assert "_delegate_daily_equity_to_canonical" in src
        assert "classify_instrument" in src

    def test_delegation_helper_uses_only_the_canonical_implementation(self):
        """Asserted against the AST, not the source text.

        The helper's own log message legitimately contains the word "yfinance"
        while explaining that the fallback is disabled; a substring search over
        source would match that prose and fail for the wrong reason.
        """
        import ast

        from crawler import india_price_feed as ipf

        # getsource() directly — cleandoc() strips the body's indentation
        # relative to the `def` line and makes the source unparseable.
        tree = ast.parse(inspect.getsource(ipf._delegate_daily_equity_to_canonical))

        called = {getattr(n.func, "id", getattr(n.func, "attr", None))
                  for n in ast.walk(tree) if isinstance(n, ast.Call)}
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                imported.add(n.module or "")
                imported.update(a.name for a in n.names)

        assert "get_upstox_candles_for_range" in imported | called
        # No provider fallback and no second timestamp conversion may live here.
        for banned in ("yfinance", "yf", "fetch_candles_yfinance"):
            assert banned not in imported and banned not in called
        assert "astimezone" not in called

    def test_equity_daily_fails_closed_when_upstox_is_empty(self):
        from crawler import india_price_feed as ipf
        with patch("crawler.upstox_candles.get_upstox_candles_for_range",
                   AsyncMock(return_value=[])):
            out = ipf.fetch_nse_candles("RELIANCE.NS", interval="1d", period="10d")
        assert out == [], "must return empty, never a yfinance-derived 00:00 bar"

    def test_equity_daily_returns_canonical_timestamps(self):
        from crawler import india_price_feed as ipf
        fake = [{"symbol": "RELIANCE.NS", "timeframe": "1d", "open": 1.0, "high": 2.0,
                 "low": 0.5, "close": 1.5, "volume": 10.0, "timestamp": CANON}]
        with patch("crawler.upstox_candles.get_upstox_candles_for_range",
                   AsyncMock(return_value=fake)):
            out = ipf.fetch_nse_candles("RELIANCE.NS", interval="1d", period="10d")
        assert out and out[0]["timestamp"].time() == CANONICAL_DAILY_UTC_TIME


class TestLegacyBackfillScriptIsDisabled:

    def test_script_refuses_to_run_without_an_explicit_override(self):
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "scripts" / "backfill_1d_candles.py"
        text = src.read_text()
        assert "ALLOW_LEGACY_1D_BACKFILL" in text
        assert "raise SystemExit" in text
        # The guard must precede the yfinance import that does the writing.
        assert text.index("raise SystemExit") < text.index("import yfinance")
