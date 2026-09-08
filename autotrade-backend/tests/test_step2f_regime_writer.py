"""STEP 2F — the active daily writer: source, contract, and refresh semantics.

`sync_regime_daily_candles_kite` kept ^NSEI, ^NSEBANK and NIFTYBEES.NS on the
legacy 00:00 convention long after every other symbol moved to canonical 03:45.
Two independent faults made that possible, and both are pinned here:

  1. It called get_kite_historical() directly, bypassing fetch_nse_candles()
     where the Step 2D.0.1 index delegation lives, then re-anchored every bar to
     midnight IST by hand.
  2. Even at the choke point the contract would not have stopped it: the guard
     governed NSE_EQUITY and returned (True, None) for indices and ETFs.

A third fault appeared when Step 2D.2 removed that writer's blind
`DELETE ... >= now() - 15 days`: with ON CONFLICT DO NOTHING the writer could no
longer correct its own current-session bar, so today's row froze at whatever
mid-session snapshot landed first.

Writes here go to `autotrade_test`, which is empty; conftest aborts the run if
that ever resolves to the production database.
"""
from __future__ import annotations

import ast
import datetime as dt
import inspect

import pytest
import pytest_asyncio

from utils.candle_contract import (
    DailyPolicy,
    InstrumentClass,
    classify_instrument,
    daily_policy,
    validate_canonical_daily_equity_candle as validate,
)

REGIME_SYMS = ("NIFTYBEES.NS", "^NSEI", "^NSEBANK")
CANON = dt.time(3, 45)


def _bar(sym, ts, **kw):
    d = {"symbol": sym, "timeframe": "1d", "open": 100.0, "high": 105.0,
         "low": 99.0, "close": 103.0, "volume": 1000.0, "timestamp": ts}
    d.update(kw)
    return d


# ── Part A/B — the regime symbols now carry the canonical contract ───────────

class TestRegimeSymbolContract:

    @pytest.mark.parametrize("sym", REGIME_SYMS)
    def test_canonical_policy(self, sym):
        assert daily_policy(sym) is DailyPolicy.CANONICAL

    @pytest.mark.parametrize("sym", REGIME_SYMS)
    def test_canonical_0345_is_accepted(self, sym):
        ok, why = validate(_bar(sym, dt.datetime(2026, 9, 3, 3, 45)))
        assert ok is True, why

    @pytest.mark.parametrize("sym", REGIME_SYMS)
    def test_legacy_0000_is_now_rejected(self, sym):
        """The exact bar the writer used to produce every five minutes."""
        ok, why = validate(_bar(sym, dt.datetime(2026, 9, 3, 0, 0)))
        assert ok is False and "00:00" in why

    @pytest.mark.parametrize("sym", REGIME_SYMS)
    def test_legacy_1830_is_rejected(self, sym):
        ok, why = validate(_bar(sym, dt.datetime(2026, 9, 2, 18, 30)))
        assert ok is False and "18:30" in why

    @pytest.mark.parametrize("sym", REGIME_SYMS)
    def test_arbitrary_time_is_rejected(self, sym):
        ok, why = validate(_bar(sym, dt.datetime(2026, 9, 3, 7, 12)))
        assert ok is False and "not the canonical session open" in why

    def test_index_and_etf_classes_are_what_we_think(self):
        assert classify_instrument("^NSEI") is InstrumentClass.NSE_INDEX
        assert classify_instrument("^NSEBANK") is InstrumentClass.NSE_INDEX
        assert classify_instrument("NIFTYBEES.NS") is InstrumentClass.ETF_OR_INAV


# ── Part F — no BSE in the active NSE regime pipeline ────────────────────────

class TestBseRemoved:

    def test_bsesn_is_not_in_the_regime_universe(self):
        from crawler.india_price_feed import _REGIME_DAILY_SYMBOLS
        assert "^BSESN" not in _REGIME_DAILY_SYMBOLS
        assert set(_REGIME_DAILY_SYMBOLS) == set(REGIME_SYMS)

    def test_every_regime_symbol_is_nse(self):
        from crawler.india_price_feed import _REGIME_DAILY_SYMBOLS
        for s in _REGIME_DAILY_SYMBOLS:
            assert classify_instrument(s) is not InstrumentClass.OTHER_INDEX
            assert classify_instrument(s) is not InstrumentClass.NON_NSE

    def test_bsesn_is_rejected_even_with_a_canonical_timestamp(self):
        """Belt and braces: if it ever returns to a symbol list, the choke
        point still refuses it."""
        ok, why = validate(_bar("^BSESN", dt.datetime(2026, 9, 3, 3, 45)))
        assert ok is False and "excluded" in why


# ── Part A — the source path, asserted against the AST ───────────────────────

class TestWriterSourcePath:
    """Source text is not evidence: this function's comments legitimately name
    get_kite_historical and the DELETE while explaining what they replaced."""

    @staticmethod
    def _names(fn):
        tree = ast.parse(inspect.getsource(fn))
        out = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                out.add(getattr(f, "id", getattr(f, "attr", None)))
            elif isinstance(n, ast.ImportFrom):
                out.update(a.name for a in n.names)
        return out

    def test_uses_the_canonical_upstox_fetch(self):
        from crawler.india_price_feed import sync_regime_daily_candles_kite as w
        names = self._names(w)
        assert "get_upstox_candles_for_range" in names
        assert "get_kite_historical" not in names

    def test_no_destructive_delete(self):
        from crawler.india_price_feed import sync_regime_daily_candles_kite as w
        names = self._names(w)
        assert "delete" not in names and "_delete" not in names

    def test_does_not_re_anchor_timestamps_by_hand(self):
        """The midnight-IST re-anchor is what produced the 00:00 series."""
        from crawler.india_price_feed import sync_regime_daily_candles_kite as w
        tree = ast.parse(inspect.getsource(w))
        assigns = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Subscript) for t in n.targets)]
        for a in assigns:
            src = ast.dump(a)
            assert "timestamp" not in src or "datetime" not in src, \
                "writer is rewriting c['timestamp'] again"

    def test_opts_into_the_scoped_current_session_refresh(self):
        from crawler.india_price_feed import sync_regime_daily_candles_kite as w
        assert "refresh_current_session" in inspect.getsource(w)


# ── Part C/D — refresh semantics, against a real database ────────────────────

class TestCurrentSessionRefresh:
    pytestmark = pytest.mark.asyncio

    @pytest_asyncio.fixture
    async def db(self):
        from sqlalchemy import text
        from db.database import AsyncSessionLocal
        syms = ["ZZTEST_REFRESH.NS", "ZZTEST_OTHER.NS"]
        async with AsyncSessionLocal() as s:
            await s.execute(text("DELETE FROM candles WHERE symbol = ANY(:s)"), {"s": syms})
            await s.commit()
        yield AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            await s.execute(text("DELETE FROM candles WHERE symbol = ANY(:s)"), {"s": syms})
            await s.commit()

    async def test_current_session_row_is_refreshed_in_place(self, db):
        """The 2D.2 regression: a 5-minute writer must be able to correct today."""
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        today = dt.datetime.utcnow().date()
        ts = dt.datetime.combine(today, CANON)
        sym = "ZZTEST_REFRESH.NS"

        async with db() as s:
            await save_candles_to_db([_bar(sym, ts, close=100.0, high=101.0)], s,
                                     source="t", enforce_contract=False,
                                     refresh_current_session=True)
            await save_candles_to_db([_bar(sym, ts, close=250.0, high=260.0)], s,
                                     source="t", enforce_contract=False,
                                     refresh_current_session=True)
            rows = (await s.execute(text(
                "SELECT close, high FROM candles WHERE symbol=:s AND timeframe='1d'"),
                {"s": sym})).all()

        assert len(rows) == 1, "refresh must update in place, not add a row"
        assert rows[0][0] == 250.0 and rows[0][1] == 260.0

    async def test_without_the_flag_the_row_is_left_alone(self, db):
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        ts = dt.datetime.combine(dt.datetime.utcnow().date(), CANON)
        sym = "ZZTEST_REFRESH.NS"
        async with db() as s:
            await save_candles_to_db([_bar(sym, ts, close=100.0)], s,
                                     source="t", enforce_contract=False)
            await save_candles_to_db([_bar(sym, ts, close=250.0)], s,
                                     source="t", enforce_contract=False)
            close = (await s.execute(text(
                "SELECT close FROM candles WHERE symbol=:s"), {"s": sym})).scalar()
        assert close == 100.0, "default must stay insert-only"

    async def test_a_prior_session_is_never_rewritten(self, db):
        """The refresh must not reach history, even with the flag on."""
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        old_ts = dt.datetime.combine(
            dt.datetime.utcnow().date() - dt.timedelta(days=5), CANON)
        sym = "ZZTEST_REFRESH.NS"
        async with db() as s:
            await save_candles_to_db([_bar(sym, old_ts, close=100.0)], s,
                                     source="t", enforce_contract=False,
                                     refresh_current_session=True)
            await save_candles_to_db([_bar(sym, old_ts, close=999.0)], s,
                                     source="t", enforce_contract=False,
                                     refresh_current_session=True)
            close = (await s.execute(text(
                "SELECT close FROM candles WHERE symbol=:s"), {"s": sym})).scalar()
        assert close == 100.0, "a historical bar was rewritten"

    async def test_writer_cannot_touch_another_symbols_row(self, db):
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        ts = dt.datetime.combine(dt.datetime.utcnow().date(), CANON)
        async with db() as s:
            await save_candles_to_db([_bar("ZZTEST_OTHER.NS", ts, close=42.0)], s,
                                     source="t", enforce_contract=False)
            await save_candles_to_db([_bar("ZZTEST_REFRESH.NS", ts, close=250.0)], s,
                                     source="t", enforce_contract=False,
                                     refresh_current_session=True)
            other = (await s.execute(text(
                "SELECT close FROM candles WHERE symbol='ZZTEST_OTHER.NS'"))).scalar()
        assert other == 42.0

    async def test_no_rows_are_deleted_by_a_write(self, db):
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        today = dt.datetime.utcnow().date()
        sym = "ZZTEST_REFRESH.NS"
        hist = [_bar(sym, dt.datetime.combine(today - dt.timedelta(days=d), CANON),
                     close=float(d)) for d in range(2, 12)]
        async with db() as s:
            await save_candles_to_db(hist, s, source="t", enforce_contract=False)
            before = (await s.execute(text(
                "SELECT count(*) FROM candles WHERE symbol=:s"), {"s": sym})).scalar()
            await save_candles_to_db(
                [_bar(sym, dt.datetime.combine(today, CANON), close=1.0)], s,
                source="t", enforce_contract=False, refresh_current_session=True)
            after = (await s.execute(text(
                "SELECT count(*) FROM candles WHERE symbol=:s"), {"s": sym})).scalar()
        assert before == 10
        assert after == 11, "history must survive a current-session write"

    async def test_legacy_conventions_are_not_recreated(self, db):
        """Mixed history must not tempt the writer into a legacy timestamp."""
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        today = dt.datetime.utcnow().date()
        sym = "ZZTEST_REFRESH.NS"
        legacy = [
            _bar(sym, dt.datetime.combine(today - dt.timedelta(days=3), dt.time(0, 0))),
            _bar(sym, dt.datetime.combine(today - dt.timedelta(days=3), dt.time(18, 30))),
        ]
        async with db() as s:
            await save_candles_to_db(legacy, s, source="t", enforce_contract=False)
            # now write through the ENFORCED path, as production does
            n = await save_candles_to_db(
                [_bar(sym, dt.datetime.combine(today, dt.time(0, 0)))], s,
                source="t", refresh_current_session=True)
            times = {r[0] for r in (await s.execute(text(
                "SELECT to_char(timestamp,'HH24:MI') FROM candles WHERE symbol=:s"),
                {"s": sym})).all()}
        assert n == 0, "the contract must reject a 00:00 bar"
        assert times == {"00:00", "18:30"}, "no new convention should appear"


# ── Part A — which classes route to the canonical fetch ──────────────────────

class TestCanonicalDelegation:
    """fetch_nse_candles delegates exactly the classes Upstox can serve.

    Getting this set wrong is silent in both directions: too narrow and a class
    keeps arriving on the legacy 18:30 path (which is how ETFs stayed legacy);
    too wide and a class whose symbols have no Upstox instrument key fails
    closed and stops receiving daily bars altogether.
    """

    def test_delegated_classes(self):
        import inspect
        from crawler import india_price_feed as f
        src = inspect.getsource(f.fetch_nse_candles)
        for cls in ("NSE_EQUITY", "NSE_INDEX", "ETF_OR_INAV"):
            assert cls in src, f"{cls} must route to the canonical fetch"
        # The series forms must NOT delegate — Upstox has no key for them.
        head = src[:src.index("_delegate_daily_equity_to_canonical(symbol, period)")]
        assert "NON_EQUITY_SERIES" not in head

    @pytest.mark.parametrize("sym", ["SIMBHALS-BZ.NS", "AAREYDRUGS-BE.NS",
                                     "AAKAAR-SM.NS", "719GS2060-GS.NS"])
    def test_series_symbols_keep_the_legacy_path(self, sym):
        """Measured 2026-09-07: none of these resolve to an Upstox key."""
        assert daily_policy(sym) is DailyPolicy.LEGACY_EXEMPT
        ok, _ = validate(_bar(sym, dt.datetime(2026, 9, 2, 18, 30)))
        assert ok is True

    @pytest.mark.parametrize("sym", ["NIFTYBEES.NS", "ABSLBANETF.NS"])
    def test_etfs_are_held_to_the_canonical_contract(self, sym):
        assert daily_policy(sym) is DailyPolicy.CANONICAL
        assert validate(_bar(sym, dt.datetime(2026, 9, 2, 18, 30)))[0] is False
        assert validate(_bar(sym, dt.datetime(2026, 9, 3, 3, 45)))[0] is True
