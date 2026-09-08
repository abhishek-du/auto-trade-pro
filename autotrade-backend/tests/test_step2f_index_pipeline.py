"""STEP 2F — the ^NSEI / ^NSEBANK / NIFTYBEES daily pipeline, end to end.

Tiers are labelled because "the helper is correct" and "production is safe" are
different claims, and only the third tier supports the second:

    UNIT                     — contract / resolver logic in isolation
    INTEGRATION              — the real persistence path against a real database
    SCHEDULE-PRODUCTION-PATH — the wiring that decides whether any of it runs

The indices have exactly one writer. The NSE equity sync's universe is 33
symbols and contains no index (verified below), so if
sync_regime_daily_candles_kite does not run, ^NSEI and ^NSEBANK go stale — which
is what happened while it lived inside a ~1,400-symbol crawl on a backlogged
queue and fired once in a full session.
"""
from __future__ import annotations

import ast
import datetime as dt
import inspect

import pytest
import pytest_asyncio

from utils.candle_contract import (
    DailyConvention,
    DailyPolicy,
    daily_policy,
    resolve_daily_session_date,
    validate_canonical_daily_equity_candle as validate,
)

REGIME = ("^NSEI", "^NSEBANK", "NIFTYBEES.NS")
CANON_T = dt.time(3, 45)


def _bar(sym, ts, **kw):
    d = {"symbol": sym, "timeframe": "1d", "open": 100.0, "high": 110.0,
         "low": 95.0, "close": 105.0, "volume": 1234.0, "timestamp": ts}
    d.update(kw)
    return d


# ═══ UNIT ════════════════════════════════════════════════════════════════════

class TestUnitContract:
    """The contract each regime symbol is held to."""

    @pytest.mark.parametrize("sym", REGIME)
    def test_policy_is_canonical(self, sym):
        assert daily_policy(sym) is DailyPolicy.CANONICAL

    @pytest.mark.parametrize("sym", REGIME)
    def test_canonical_accepted_legacy_rejected(self, sym):
        assert validate(_bar(sym, dt.datetime(2026, 9, 3, 3, 45)))[0] is True
        ok, why = validate(_bar(sym, dt.datetime(2026, 9, 3, 0, 0)))
        assert ok is False and "00:00" in why
        ok, why = validate(_bar(sym, dt.datetime(2026, 9, 2, 18, 30)))
        assert ok is False and "18:30" in why

    def test_bsesn_excluded_regardless_of_timestamp(self):
        assert daily_policy("^BSESN") is DailyPolicy.EXCLUDED
        assert validate(_bar("^BSESN", dt.datetime(2026, 9, 3, 3, 45)))[0] is False


class TestUnitResolver:
    """Mixed legacy conventions must resolve without look-ahead (case 13/14)."""

    def test_the_three_conventions_map_to_their_real_sessions(self):
        assert resolve_daily_session_date(
            "RELIANCE.NS", "1d", dt.datetime(2026, 9, 2, 3, 45)
        ).session_date == dt.date(2026, 9, 2)
        assert resolve_daily_session_date(
            "RELIANCE.NS", "1d", dt.datetime(2026, 9, 2, 18, 30)
        ).session_date == dt.date(2026, 9, 3)
        assert resolve_daily_session_date(
            "RELIANCE.NS", "1d", dt.datetime(2026, 9, 4, 0, 0)
        ).session_date == dt.date(2026, 9, 4)

    @pytest.mark.parametrize("sym", REGIME)
    def test_index_current_session_close_maps_to_its_own_session(self, sym):
        """Case 14: a canonical bar stamped D belongs to session D, not D+1."""
        d = dt.date(2026, 9, 3)
        res = resolve_daily_session_date(
            sym, "1d", dt.datetime.combine(d, CANON_T))
        assert res.usable and res.session_date == d
        assert res.convention is DailyConvention.CANONICAL_0345


# ═══ INTEGRATION — real persistence path, real database ══════════════════════

class TestIntegrationPersistence:
    pytestmark = pytest.mark.asyncio

    @pytest_asyncio.fixture
    async def db(self):
        from sqlalchemy import text
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            await s.execute(text(
                "DELETE FROM candles WHERE symbol = ANY(:s)"), {"s": list(REGIME)})
            await s.commit()
        yield AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            await s.execute(text(
                "DELETE FROM candles WHERE symbol = ANY(:s)"), {"s": list(REGIME)})
            await s.commit()

    @pytest.mark.parametrize("sym", REGIME)
    async def test_canonical_bar_survives_the_production_write(self, sym, db):
        """Cases 1-3: normalized bar -> choke point -> row on disk at 03:45."""
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        ts = dt.datetime.combine(dt.datetime.utcnow().date(), CANON_T)
        async with db() as s:
            n = await save_candles_to_db([_bar(sym, ts)], s, source="test-2f",
                                         refresh_current_session=True)
            row = (await s.execute(text(
                "SELECT to_char(timestamp,'HH24:MI'), close FROM candles "
                "WHERE symbol=:s AND timeframe='1d'"), {"s": sym})).first()
        assert n == 1
        assert row[0] == "03:45" and row[1] == 105.0

    @pytest.mark.parametrize("sym", REGIME)
    async def test_a_0000_bar_cannot_become_a_production_row(self, sym, db):
        """Case 4 — through the real write path, not the validator alone."""
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        ts = dt.datetime.combine(dt.datetime.utcnow().date(), dt.time(0, 0))
        async with db() as s:
            n = await save_candles_to_db([_bar(sym, ts)], s, source="test-2f",
                                         refresh_current_session=True)
            cnt = (await s.execute(text(
                "SELECT count(*) FROM candles WHERE symbol=:s"), {"s": sym})).scalar()
        assert n == 0 and cnt == 0

    @pytest.mark.parametrize("sym", REGIME)
    async def test_an_1830_bar_cannot_become_a_production_row(self, sym, db):
        """Case 5."""
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        ts = dt.datetime.combine(
            dt.datetime.utcnow().date() - dt.timedelta(days=1), dt.time(18, 30))
        async with db() as s:
            n = await save_candles_to_db([_bar(sym, ts)], s, source="test-2f",
                                         refresh_current_session=True)
            cnt = (await s.execute(text(
                "SELECT count(*) FROM candles WHERE symbol=:s"), {"s": sym})).scalar()
        assert n == 0 and cnt == 0

    async def test_current_session_refresh_updates_the_same_row(self, db):
        """Case 6."""
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        ts = dt.datetime.combine(dt.datetime.utcnow().date(), CANON_T)
        async with db() as s:
            await save_candles_to_db([_bar("^NSEI", ts, close=23751.75)], s,
                                     source="t", refresh_current_session=True)
            await save_candles_to_db([_bar("^NSEI", ts, close=23779.15)], s,
                                     source="t", refresh_current_session=True)
            rows = (await s.execute(text(
                "SELECT close FROM candles WHERE symbol='^NSEI'"))).all()
        assert len(rows) == 1 and rows[0][0] == 23779.15

    async def test_historical_rows_are_immutable_under_normal_writes(self, db):
        """Case 7 — the settled-bar guarantee."""
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        old = dt.datetime.combine(
            dt.datetime.utcnow().date() - dt.timedelta(days=6), CANON_T)
        async with db() as s:
            await save_candles_to_db([_bar("^NSEI", old, close=111.0)], s,
                                     source="t", refresh_current_session=True)
            await save_candles_to_db([_bar("^NSEI", old, close=999.0)], s,
                                     source="t", refresh_current_session=True)
            close = (await s.execute(text(
                "SELECT close FROM candles WHERE symbol='^NSEI'"))).scalar()
        assert close == 111.0

    async def test_duplicate_scheduled_execution_is_idempotent(self, db):
        """Case 8: the same batch twice must not double rows."""
        from sqlalchemy import text
        from crawler.price_feed import save_candles_to_db

        today = dt.datetime.utcnow().date()
        batch = [_bar(s, dt.datetime.combine(today - dt.timedelta(days=d), CANON_T))
                 for s in REGIME for d in range(0, 5)]
        async with db() as s:
            await save_candles_to_db(batch, s, source="t", refresh_current_session=True)
            first = (await s.execute(text(
                "SELECT count(*) FROM candles WHERE symbol = ANY(:s)"),
                {"s": list(REGIME)})).scalar()
            await save_candles_to_db(batch, s, source="t", refresh_current_session=True)
            second = (await s.execute(text(
                "SELECT count(*) FROM candles WHERE symbol = ANY(:s)"),
                {"s": list(REGIME)})).scalar()
            convs = {r[0] for r in (await s.execute(text(
                "SELECT DISTINCT to_char(timestamp,'HH24:MI') FROM candles "
                "WHERE symbol = ANY(:s)"), {"s": list(REGIME)})).all()}
        assert first == second, "a second run created rows"
        assert convs == {"03:45"}


# ═══ SCHEDULE / PRODUCTION PATH ══════════════════════════════════════════════

class TestSchedulePath:
    """Whether the correct code is actually reachable in production.

    Unit and integration tiers both pass while a writer sits unscheduled behind
    a starved queue — that is exactly the state this phase found.
    """

    @staticmethod
    def _names(fn):
        t = ast.parse(inspect.getsource(fn))
        out = set()
        for n in ast.walk(t):
            if isinstance(n, ast.Call):
                out.add(getattr(n.func, "id", getattr(n.func, "attr", None)))
            elif isinstance(n, ast.ImportFrom):
                out.update(a.name for a in n.names)
        return out

    def test_the_task_is_registered(self):
        from tasks.celery_app import celery_app
        import tasks.india_tasks  # noqa: F401 — registers the task
        assert "tasks.sync_regime_daily_candles" in celery_app.tasks

    def test_beat_schedules_it(self):
        """Case 10a: a registered task nobody schedules never runs."""
        from tasks.celery_app import celery_app
        entries = [e for e in celery_app.conf.beat_schedule.values()
                   if e.get("task") == "tasks.sync_regime_daily_candles"]
        assert len(entries) == 1, "expected exactly one beat entry"
        assert entries[0]["schedule"] == 300

    def test_it_is_routed_off_the_backlogged_default_queue(self):
        from tasks.celery_app import celery_app
        r = celery_app.conf.task_routes.get("tasks.sync_regime_daily_candles")
        assert r and r["queue"] == "scan_queue"

    def test_the_task_calls_the_one_writer(self):
        """Case 10b: it must delegate, not reimplement."""
        import tasks.india_tasks as t
        names = self._names(t.sync_regime_daily_candles_task)
        assert "sync_regime_daily_candles_kite" in names

    def test_the_crawl_no_longer_calls_it(self):
        """Exactly one scheduled path — two would race on one row."""
        import inspect as i
        from crawler import india_price_feed as f
        src = i.getsource(f.run_india_price_crawl)
        tree = ast.parse(src)
        called = {getattr(n.func, "id", getattr(n.func, "attr", None))
                  for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "sync_regime_daily_candles_kite" not in called

    def test_the_writer_reaches_the_contract(self):
        """Case 11: it must persist through the guarded choke point."""
        from crawler.india_price_feed import sync_regime_daily_candles_kite as w
        names = self._names(w)
        assert "save_candles_to_db" in names
        src = inspect.getsource(w)
        assert "enforce_contract=False" not in src, "must not bypass the contract"
        assert "refresh_current_session=True" in src

    def test_upstox_is_the_only_source(self):
        from crawler.india_price_feed import sync_regime_daily_candles_kite as w
        names = self._names(w)
        assert "get_upstox_candles_for_range" in names
        for banned in ("get_kite_historical", "yf", "download"):
            assert banned not in names

    def test_bsesn_is_not_in_the_active_regime_universe(self):
        """Case 9."""
        from crawler.india_price_feed import _REGIME_DAILY_SYMBOLS
        assert "^BSESN" not in _REGIME_DAILY_SYMBOLS
        assert set(_REGIME_DAILY_SYMBOLS) == set(REGIME)

    def test_indices_have_no_other_writer(self):
        """The premise the whole pipeline rests on: the equity sync skips them,
        so this writer is their only source."""
        from utils.config import settings
        universe = list(settings.nse_symbols) + list(settings.nse_mid_symbols)
        assert "^NSEI" not in universe and "^NSEBANK" not in universe
        assert "NIFTYBEES.NS" in universe   # the ETF is covered by both


# ═══ INTEGRATION — the consumer that adding index rows newly exposed ══════════

class TestNiftyReturnConsumer:
    """Adding canonical index bars broke a reader that had been safe by accident.

    Step 2E classified portfolio_analytics.get_nifty_return as "SAFE in practice
    — latently fragile if a second convention ever appears", because ^NSEI then
    carried only the 00:00 series. Step 2F added 03:45 index bars, so the newest
    sessions started appearing twice and the `LIMIT days + 1` row window began
    differencing adjusted closes against raw ones.
    """
    pytestmark = pytest.mark.asyncio

    def test_it_uses_the_shared_session_reader(self):
        import inspect as i
        from engine.portfolio_analytics import get_nifty_return
        src = i.getsource(get_nifty_return)
        tree = ast.parse(src)
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                names.add(getattr(n.func, "id", getattr(n.func, "attr", None)))
            elif isinstance(n, ast.ImportFrom):
                names.update(a.name for a in n.names)
        assert "session_close_series" in names
        assert "select" not in names, "must not query candles directly any more"

    async def test_it_refuses_rather_than_annualizing_a_short_window(self):
        """11 canonical ^NSEI sessions annualized to -48.9%; that number would
        have flowed into Treynor and Jensen as a market return."""
        from db.database import AsyncSessionLocal
        from engine.portfolio_analytics import get_nifty_return
        from engine.daily_series import session_close_series

        async with AsyncSessionLocal() as s:
            n = len(await session_close_series("^NSEI", s, sessions=253))
            got = await get_nifty_return(s, days=252)
        if n < 60:
            assert got is None, f"annualized {n} sessions instead of refusing"
        else:
            assert got is None or -1.0 < got < 2.0
