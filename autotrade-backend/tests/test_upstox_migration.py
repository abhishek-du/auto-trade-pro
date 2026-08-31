"""Kite -> Upstox migration: the contracts that must not move.

Kite Connect's token expired on 2026-08-31 ("Invalid `api_key` or
`access_token`", verified live) and Upstox is now the sole broker backend.

The migration strategy is ADAPTERS, not rewrites: every seam function keeps its
name, signature and return shape, so price_feed, market_snapshot, live_snapshot,
the exit loop, the resampler and the news engine are untouched. These tests pin
those shapes -- if an adapter drifts, the failure surfaces here rather than as a
silently empty price dict in the 5-second stop-loss loop.
"""
from __future__ import annotations

import datetime as dt
import inspect

import pytest


# ── instrument identity ──────────────────────────────────────────────────────
class TestInstrumentKeys:
    def test_key_is_constructed_deterministically(self):
        from crawler.upstox_instruments import instrument_key_for
        assert instrument_key_for("INE002A01018") == "NSE_EQ|INE002A01018"

    @pytest.mark.parametrize("bad", ["", None, "TOOSHORT", "XX002A01018XXXX"])
    def test_a_bad_isin_yields_no_key_rather_than_a_wrong_one(self, bad):
        from crawler.upstox_instruments import instrument_key_for
        assert instrument_key_for(bad) is None

    def test_search_returns_a_three_way_outcome(self):
        """"not listed" and "rate limited" must be distinguishable. The first
        migration run recorded 10 real NSE stocks (AJANTPHARM, AIAENG, AJMERA)
        as unresolvable when they were merely 429s."""
        from crawler.upstox_instruments import search_instrument
        src = inspect.getsource(search_instrument)
        for outcome in ('"ok"', '"not_listed"', '"error"'):
            assert outcome in src

    def test_search_retries_on_rate_limit(self):
        from crawler.upstox_instruments import search_instrument
        src = inspect.getsource(search_instrument)
        assert "429" in src and "backoff" in src

    def test_the_sync_skips_non_equity_series(self):
        """4,301 of the NSE 'EQ' rows are State Development Loans, 1,300 are
        NCDs. An alphabetical pass spends its whole budget on 0ABCL31-N0."""
        from crawler.upstox_instruments import sync_upstox_instrument_keys
        src = inspect.getsource(sync_upstox_instrument_keys)
        assert "-[A-Z0-9]{2}$" in src
        assert "hub_universe" in src, "hub members must be resolved first"

    def test_sync_is_incremental_and_therefore_restartable(self):
        from crawler.upstox_instruments import sync_upstox_instrument_keys
        sig = inspect.signature(sync_upstox_instrument_keys)
        assert sig.parameters["only_missing"].default is True

    def test_nse_only(self):
        """Step 2A: no BSE instrument may enter an active path."""
        import crawler.upstox_instruments as m
        assert m._NSE_EQ == "NSE_EQ"
        assert "BSE_EQ" not in inspect.getsource(m.search_instrument)


# ── quote adapter shapes ─────────────────────────────────────────────────────
class TestQuoteContracts:
    def test_get_live_prices_shape_is_unchanged(self):
        from crawler.upstox_quotes import get_live_prices
        src = inspect.getsource(get_live_prices)
        for field in ('"price"', '"last_price"', '"change"', '"change_pct"'):
            assert field in src

    def test_get_full_quote_shape_is_unchanged(self):
        from crawler.upstox_quotes import get_full_quote
        src = inspect.getsource(get_full_quote)
        for field in ("symbol", "last_price", "ohlc", "volume", "bid", "ask",
                      "oi", "buy_depth", "sell_depth", "change", "change_pct",
                      "instrument_token", "last_trade_time"):
            assert f'"{field}"' in src, f"get_full_quote dropped {field}"

    def test_exit_bucket_isolation_survives(self):
        """KITE_EXIT_RPS was a reserved bucket so a quote flood could never
        delay a stop-loss. That property is load-bearing."""
        from crawler.upstox_quotes import get_live_prices
        sig = inspect.signature(get_live_prices)
        assert "exit_bucket" in sig.parameters
        assert sig.parameters["exit_bucket"].default is False
        assert "exit_bucket=exit_bucket" in inspect.getsource(get_live_prices)

    def test_an_unmapped_symbol_is_dropped_not_guessed(self):
        from crawler.upstox_quotes import _to_key
        assert _to_key("DEFINITELY_NOT_A_REAL_SYMBOL.NS") is None
        assert _to_key("") is None
        assert _to_key(None) is None

    def test_batch_size_accounts_for_longer_upstox_keys(self):
        import crawler.upstox_quotes as q
        assert q._BATCH <= 250, (
            "Upstox instrument_keys are ~60% longer than Kite's; a 500-key URL "
            "risks the same 'query too long' rejection Kite chunking existed for"
        )


# ── candle adapter ───────────────────────────────────────────────────────────
class TestCandleContracts:
    def test_candle_dict_matches_save_candles_to_db(self):
        from crawler.upstox_candles import get_upstox_candles_for_range
        src = inspect.getsource(get_upstox_candles_for_range)
        for field in ("symbol", "timeframe", "open", "high", "low", "close",
                      "volume", "timestamp"):
            assert f'"{field}"' in src

    def test_signature_matches_the_kite_function_it_replaces(self):
        from crawler.upstox_candles import get_upstox_candles_for_range
        from crawler.zerodha_historical import get_kite_candles_for_range
        a = list(inspect.signature(get_upstox_candles_for_range).parameters)
        b = list(inspect.signature(get_kite_candles_for_range).parameters)
        assert a == b, f"signature drift: {a} vs {b}"

    def test_timestamps_are_converted_to_naive_utc(self):
        """Every candle row in this DB is naive UTC. Upstox returns +05:30.
        Getting this wrong shifts every bar by 5h30m -- the same class of bug
        that once put 4,159 news rows in the future."""
        from crawler.upstox_candles import _to_naive_utc
        got = _to_naive_utc("2026-08-31T09:15:00+05:30")
        assert got == dt.datetime(2026, 8, 31, 3, 45)
        assert got.tzinfo is None

    def test_bad_timestamps_return_none_rather_than_now(self):
        from crawler.upstox_candles import _to_naive_utc
        for bad in ("", None, "not-a-date"):
            assert _to_naive_utc(bad) is None

    def test_interval_map_covers_the_project_vocabulary(self):
        from crawler.upstox_candles import _INTERVAL_MAP
        for tf in ("1m", "5m", "15m", "1h", "1d", "minute", "day"):
            assert tf in _INTERVAL_MAP

    def test_timeframe_label_is_normalised_for_the_resampler(self):
        from crawler.upstox_candles import get_upstox_candles_for_range
        src = inspect.getsource(get_upstox_candles_for_range)
        assert '"minute": "1m"' in src and '"day": "1d"' in src


# ── websocket ────────────────────────────────────────────────────────────────
class TestWebSocketTicks:
    def test_full_feed_produces_every_field_the_system_reads(self):
        from crawler.upstox_websocket import _extract
        t = _extract({"fullFeed": {"marketFF": {
            "ltpc": {"ltp": 100.5, "cp": 99.0}, "vtt": 12345, "oi": 7,
            "marketOHLC": {"ohlc": [{"interval": "1d", "open": 99, "high": 101,
                                     "low": 98, "close": 100.5}]},
            "marketLevel": {"bidAskQuote": [{"bp": 100.4, "bq": 10,
                                             "sp": 100.6, "sq": 20}]}}}})
        for field in ("last_price", "volume_traded", "ohlc", "depth", "oi",
                      "total_buy_qty", "total_sell_qty", "change", "change_percent"):
            assert field in t, f"tick is missing {field}"
        assert t["last_price"] == 100.5
        assert t["volume_traded"] == 12345
        assert t["change"] == pytest.approx(1.5)
        assert t["depth"]["buy"][0] == {"price": 100.4, "quantity": 10}

    def test_ltpc_mode_returns_zeroes_not_garbage(self):
        """MODE_LTP carried no volume/depth under Kite either. Zeroes are the
        documented contract, not missing data."""
        from crawler.upstox_websocket import _extract
        t = _extract({"ltpc": {"ltp": 50.0, "cp": 49.0}})
        assert t["last_price"] == 50.0
        assert t["volume_traded"] == 0
        assert t["depth"] == {"buy": [], "sell": []}
        assert t["change"] == pytest.approx(1.0)

    def test_unknown_payload_returns_none(self):
        from crawler.upstox_websocket import _extract
        assert _extract({}) is None
        assert _extract({"junk": 1}) is None

    def test_reverse_lookup_is_a_dict_not_a_scan(self):
        """The previous implementation iterated the whole cache per tick."""
        import crawler.upstox_websocket as ws
        src = inspect.getsource(ws._on_message)
        assert "_REV.get(" in src
        assert "for sym, key in" not in src

    def test_mode_budgeting_mirrors_the_kite_split(self):
        import crawler.upstox_websocket as ws
        src = inspect.getsource(ws.start_upstox_websocket)
        assert "priority_symbols" in src
        assert '"full"' in src and '"ltpc"' in src
        assert ws._FULL_CAP == 2000 and ws._LTPC_CAP == 5000

    def test_a_universe_over_the_cap_is_reported_not_silently_truncated(self):
        import crawler.upstox_websocket as ws
        assert "exceeds the ltpc cap" in inspect.getsource(ws.start_upstox_websocket)

    def test_tick_parsing_never_raises(self):
        from crawler.upstox_websocket import _on_message
        for junk in (None, "string", 42, {"feeds": "notadict"}, {"feeds": {"k": None}}):
            _on_message(junk)   # must not raise


# ── rate limiting ────────────────────────────────────────────────────────────
class TestRateLimiter:
    def test_exit_bucket_is_separate_from_quotes(self):
        from crawler.upstox_limiter import Bucket, _rps
        assert Bucket.EXIT.value != Bucket.QUOTE.value
        assert _rps(Bucket.EXIT) > 0

    def test_it_fails_open_rather_than_wedging(self):
        import crawler.upstox_limiter as m
        assert "fail" in inspect.getsource(m.acquire).lower()
        from utils.config import settings
        assert settings.UPSTOX_LIMITER_MAX_WAIT > 0

    def test_quote_rps_respects_the_sustained_per_minute_budget(self):
        """500/min is 8.3/s. The 50/s figure is a burst ceiling; a 3,040-symbol
        sync paced at 50/s got 518 rate-limit errors on the first run."""
        from utils.config import settings
        assert settings.UPSTOX_QUOTE_RPS <= 10


# ── seam delegation ──────────────────────────────────────────────────────────
class TestSeamsDelegateToUpstox:
    def test_get_live_prices_delegates(self):
        import crawler.zerodha_market as zm
        assert "upstox_quotes" in inspect.getsource(zm.get_live_prices)

    def test_get_full_quote_delegates(self):
        import crawler.zerodha_market as zm
        assert "upstox_quotes" in inspect.getsource(zm.get_full_quote)

    def test_candles_delegate(self):
        import crawler.zerodha_historical as zh
        assert "upstox_candles" in inspect.getsource(zh.get_kite_candles_for_range)

    def test_ticker_task_starts_upstox(self):
        import tasks.india_tasks as t
        assert "start_upstox_websocket" in inspect.getsource(t.kite_start_ticker_task)

    def test_daily_key_sync_is_scheduled(self):
        from tasks.celery_app import celery_app
        entry = celery_app.conf.beat_schedule.get("sync-upstox-instrument-keys-daily")
        assert entry and entry["task"] == "tasks.sync_upstox_instrument_keys"


class TestPaperModeIntact:
    def test_paper_mode_still_true(self):
        from utils.config import settings
        assert settings.PAPER_MODE is True

    def test_no_strategy_parameter_moved(self):
        from utils.config import settings
        assert settings.TACTICAL_TOP_N == 15
        assert settings.V2_MIN_HOLD_MINUTES == 120
        assert settings.TRADING_STRATEGY_MODE == "V2"
        assert settings.NSE_ONLY_UNIVERSE is True
