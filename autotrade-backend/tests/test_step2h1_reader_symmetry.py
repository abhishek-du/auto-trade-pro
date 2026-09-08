"""STEP 2H.1 — reader/writer symmetry for NSE special sessions.

The Step 2H backfill taught the WRITER to store the ten weekend dates NSE
actually trades (Union Budget Saturdays, Diwali Muhurat, three disaster-recovery
sessions) by threading `extra_open` through the contract. The READER was not
taught the same thing: every function in engine.daily_series already accepted
`extra_open`, but no caller ever supplied one, so it was always None and the
resolver refused every weekend row.

The result was data written correctly and then silently discarded — 10 of
RELIANCE's 2,480 canonical rows. These tests pin both halves of the symmetry and
the boundary that must NOT move: an ordinary Saturday is still not a session.
"""
from __future__ import annotations

import datetime as dt

import pytest

from utils.candle_contract import (
    NSE_SPECIAL_SESSIONS,
    DailyConvention,
    resolve_daily_session_date,
    validate_canonical_daily_equity_candle as validate,
)

CANON = dt.time(3, 45)
FAR_FUTURE = dt.datetime(2027, 6, 1)


def _bar(d: dt.date, *, sym="RELIANCE.NS", t=CANON, close=100.0):
    return {"symbol": sym, "timeframe": "1d", "open": 99.0, "high": 101.0,
            "low": 98.0, "close": close, "volume": 1000.0,
            "timestamp": dt.datetime.combine(d, t)}


# ── A. the boundary that must not move ───────────────────────────────────────

class TestOrdinaryWeekendStillRejected:

    @pytest.mark.parametrize("d", [dt.date(2026, 9, 5), dt.date(2026, 9, 6),
                                   dt.date(2025, 7, 12), dt.date(2024, 6, 30)])
    def test_writer_rejects_an_ordinary_weekend(self, d):
        assert d.weekday() >= 5
        ok, why = validate(_bar(d), extra_open=NSE_SPECIAL_SESSIONS,
                           now_utc=FAR_FUTURE)
        assert ok is False and "weekend" in why

    @pytest.mark.parametrize("d", [dt.date(2026, 9, 5), dt.date(2026, 9, 6)])
    def test_reader_rejects_an_ordinary_weekend(self, d):
        r = resolve_daily_session_date("RELIANCE.NS", "1d",
                                       dt.datetime.combine(d, CANON),
                                       extra_open=NSE_SPECIAL_SESSIONS,
                                       now_utc=FAR_FUTURE)
        assert not r.usable

    def test_the_calendar_is_not_a_weekend_rule(self):
        """Ten specific dates, not 'weekends are tradeable'."""
        assert len(NSE_SPECIAL_SESSIONS) == 10
        for d in NSE_SPECIAL_SESSIONS:
            assert dt.date.fromisoformat(d).weekday() >= 5


# ── B/C. writer and reader agree on all ten ──────────────────────────────────

class TestWriterReaderAgreement:

    @pytest.mark.parametrize("iso", sorted(NSE_SPECIAL_SESSIONS))
    def test_writer_accepts_each_declared_session(self, iso):
        ok, why = validate(_bar(dt.date.fromisoformat(iso)),
                           extra_open=NSE_SPECIAL_SESSIONS, now_utc=FAR_FUTURE)
        assert ok is True, f"{iso}: {why}"

    @pytest.mark.parametrize("iso", sorted(NSE_SPECIAL_SESSIONS))
    def test_reader_resolves_each_declared_session_to_itself(self, iso):
        d = dt.date.fromisoformat(iso)
        r = resolve_daily_session_date("RELIANCE.NS", "1d",
                                       dt.datetime.combine(d, CANON),
                                       extra_open=NSE_SPECIAL_SESSIONS,
                                       now_utc=FAR_FUTURE)
        assert r.usable and r.session_date == d
        assert r.convention is DailyConvention.CANONICAL_0345

    def test_2019_muhurat_specifically(self):
        """The date the failing pilot first reported."""
        d = dt.date(2019, 10, 27)
        assert validate(_bar(d), extra_open=NSE_SPECIAL_SESSIONS, now_utc=FAR_FUTURE)[0]
        assert resolve_daily_session_date(
            "RELIANCE.NS", "1d", dt.datetime.combine(d, CANON),
            extra_open=NSE_SPECIAL_SESSIONS, now_utc=FAR_FUTURE).session_date == d

    def test_without_the_calendar_both_halves_refuse(self):
        """Default stays strict — the calendar is opt-in at the contract level."""
        d = dt.date(2019, 10, 27)
        assert validate(_bar(d), now_utc=FAR_FUTURE)[0] is False
        assert not resolve_daily_session_date(
            "RELIANCE.NS", "1d", dt.datetime.combine(d, CANON),
            now_utc=FAR_FUTURE).usable


# ── D. canonical timestamp is unaffected ─────────────────────────────────────

class TestCanonicalTimestamp:

    @pytest.mark.parametrize("iso", sorted(NSE_SPECIAL_SESSIONS))
    def test_special_sessions_are_still_0345_only(self, iso):
        d = dt.date.fromisoformat(iso)
        assert validate(_bar(d, t=dt.time(0, 0)), extra_open=NSE_SPECIAL_SESSIONS,
                        now_utc=FAR_FUTURE)[0] is False
        assert validate(_bar(d, t=dt.time(18, 30)), extra_open=NSE_SPECIAL_SESSIONS,
                        now_utc=FAR_FUTURE)[0] is False
        assert validate(_bar(d, t=CANON), extra_open=NSE_SPECIAL_SESSIONS,
                        now_utc=FAR_FUTURE)[0] is True


# ── one calendar, one source ─────────────────────────────────────────────────

class TestSingleCalendarSource:

    def test_reader_and_backfill_share_the_same_object(self):
        from scripts import oneoff_upstox_backfill as bf
        import engine.daily_series as ds
        assert bf.NSE_SPECIAL_SESSIONS is NSE_SPECIAL_SESSIONS
        assert ds.NSE_SPECIAL_SESSIONS is NSE_SPECIAL_SESSIONS

    def test_the_calendar_is_immutable(self):
        assert isinstance(NSE_SPECIAL_SESSIONS, frozenset)

    def test_reader_defaults_to_it_but_honours_an_override(self):
        import inspect
        import engine.daily_series as ds
        src = inspect.getsource(ds._collapse_to_sessions)
        assert "if extra_open is None:" in src
        assert "NSE_SPECIAL_SESSIONS" in src


# ── E/F/G/H. against the real database, via the production reader ────────────

class TestRealDatabaseReadPath:
    """DB canonical row -> daily_series -> resolved session. Not the helper in
    isolation: the actual production path the consumers call.

    Under pytest, settings.DATABASE_URL points at `autotrade_test`, which is
    empty — conftest aborts the run if it ever resolves to production. These
    therefore SKIP here and are proven against the real database separately;
    the evidence is in the Step 2H.1 report.
    """
    pytestmark = pytest.mark.asyncio

    async def test_stored_special_sessions_are_returned(self):
        from sqlalchemy import text
        from db.database import AsyncSessionLocal
        from engine.daily_series import session_closes

        async with AsyncSessionLocal() as s:
            stored = (await s.execute(text(
                "SELECT timestamp, close FROM candles WHERE symbol='RELIANCE.NS' "
                "  AND timeframe='1d' AND to_char(timestamp,'HH24:MI')='03:45' "
                "  AND extract(dow from timestamp) IN (0,6) ORDER BY timestamp"))).all()
            if not stored:
                pytest.skip("no canonical special-session rows in this database")
            got = {d: c for d, c, _ in await session_closes("RELIANCE.NS", s, sessions=4000)}

        for ts, close in stored:
            d = ts.date()
            assert d in got, f"{d} stored but dropped by the reader"
            assert got[d] == pytest.approx(close)

    async def test_reader_returns_no_undeclared_weekend(self):
        from db.database import AsyncSessionLocal
        from engine.daily_series import session_closes

        async with AsyncSessionLocal() as s:
            rows = await session_closes("RELIANCE.NS", s, sessions=4000)
        if not rows:
            pytest.skip("no RELIANCE.NS history")
        bad = [d for d, _, _ in rows
               if d.weekday() >= 5 and d.isoformat() not in NSE_SPECIAL_SESSIONS]
        assert bad == [], f"undeclared weekend sessions returned: {bad}"

    async def test_no_look_ahead_after_the_change(self):
        from db.database import AsyncSessionLocal
        from engine.agent.performance_engine import _aligned_closes

        async with AsyncSessionLocal() as s:
            ac = await _aligned_closes("RELIANCE.NS", 30, s)
        if dt.date(2026, 9, 2) not in ac:
            pytest.skip("RELIANCE.NS 2026-09-02 not present")
        assert ac[dt.date(2026, 9, 2)] == pytest.approx(1313.1)
        assert ac[dt.date(2026, 9, 3)] == pytest.approx(1302.5)
        assert ac[dt.date(2026, 9, 4)] == pytest.approx(1322.0)

    async def test_series_stays_unique_and_ordered(self):
        from db.database import AsyncSessionLocal
        from engine.daily_series import session_closes

        async with AsyncSessionLocal() as s:
            for sym in ("RELIANCE.NS", "TCS.NS", "NIFTYBEES.NS"):
                rows = await session_closes(sym, s, sessions=4000)
                if not rows:
                    continue
                ds = [d for d, _, _ in rows]
                assert len(set(ds)) == len(ds), f"{sym}: duplicate session"
                assert ds == sorted(ds), f"{sym}: not ordered"

    async def test_regime_consumer_still_functions(self):
        """market_regime reads NIFTYBEES through this path and fails closed
        below 60 sessions — the change must not starve it."""
        from db.database import AsyncSessionLocal
        from engine.daily_series import session_close_series

        async with AsyncSessionLocal() as s:
            cl = await session_close_series("NIFTYBEES.NS", s, sessions=220)
        if not cl:
            pytest.skip("no NIFTYBEES.NS history in this database")
        assert len(cl) >= 60, f"only {len(cl)} sessions — regime would fail closed"
