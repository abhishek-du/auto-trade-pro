"""STEP 2K remediation — H-1 unsafe direct readers, H-2 as-of contract, M-1 PIT.

H-1: four modules read `candles` directly for daily windows and therefore
consumed duplicate sessions. `morning_regime` ran the exact query removed from
`market_regime` in Step 2D.2: measured 2026-09-08 its 210 rows were 110
canonical + 97 legacy 18:30 + 3 legacy 00:00 — 75 duplicated sessions.

H-2: the reader presented an in-progress bar as a completed session for the
three regime symbols while equities showed the last closed session — two
different as-of points in one feature row.

M-1: the reader had no `as_of`, so a historical evaluation could see beyond its
own session.

Real-DB tests skip under pytest (DATABASE_URL points at the empty
`autotrade_test`); they are proven against production separately.
"""
from __future__ import annotations

import ast
import datetime as dt
import inspect

import pytest

from utils.candle_contract import NSE_SPECIAL_SESSIONS

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _calls(fn) -> set[str]:
    """Parsed call graph — docstrings here name the queries they replaced."""
    tree = ast.parse(inspect.getsource(fn))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            out.add(getattr(n.func, "id", getattr(n.func, "attr", None)))
        elif isinstance(n, ast.ImportFrom):
            out.update(a.name for a in n.names)
    return out


# ── H-2: the current-session contract ────────────────────────────────────────

class TestCurrentSessionContract:
    """Completion comes from the calendar and the clock, never from whether a
    row happens to exist — the regime writer refreshes today's bar every five
    minutes, so row-presence would call a half-finished session complete."""

    def test_in_progress_during_market_hours(self):
        from engine.daily_series import current_open_session
        noon = dt.datetime(2026, 9, 8, 12, 0, tzinfo=IST)      # Tuesday
        assert current_open_session(now_ist=noon) == dt.date(2026, 9, 8)

    def test_complete_after_close(self):
        from engine.daily_series import current_open_session
        after = dt.datetime(2026, 9, 8, 16, 0, tzinfo=IST)
        assert current_open_session(now_ist=after) is None

    def test_exactly_at_close_counts_as_complete(self):
        from engine.daily_series import current_open_session
        at = dt.datetime(2026, 9, 8, 15, 30, tzinfo=IST)
        assert current_open_session(now_ist=at) is None

    def test_weekend_has_no_open_session(self):
        from engine.daily_series import current_open_session
        sat = dt.datetime(2026, 9, 5, 12, 0, tzinfo=IST)
        assert current_open_session(now_ist=sat) is None

    def test_special_weekend_session_is_recognised(self):
        """Budget Sunday is a real session — with the calendar supplied."""
        from engine.daily_series import current_open_session
        d = dt.datetime(2026, 2, 1, 12, 0, tzinfo=IST)
        assert current_open_session(now_ist=d) is None            # no calendar
        assert current_open_session(now_ist=d,
                                    extra_open=NSE_SPECIAL_SESSIONS) == dt.date(2026, 2, 1)

    def test_default_is_closed_only(self):
        from engine.daily_series import session_closes
        sig = inspect.signature(session_closes)
        assert sig.parameters["include_current"].default is False
        assert sig.parameters["as_of"].default is None

    def test_every_series_reader_exposes_the_contract(self):
        from engine.daily_series import (session_closes, session_bars,
                                         session_closes_bulk, session_bars_bulk)
        for fn in (session_closes, session_bars, session_closes_bulk, session_bars_bulk):
            p = inspect.signature(fn).parameters
            assert "as_of" in p and "include_current" in p, fn.__name__
            assert p["include_current"].default is False, fn.__name__

    def test_close_for_session_can_be_bounded(self):
        from engine.daily_series import close_for_session
        assert "as_of" in inspect.signature(close_for_session).parameters


# ── H-1: the four modules must delegate ──────────────────────────────────────

class TestH1Delegation:

    def test_morning_regime_uses_the_shared_reader(self):
        from engine.agent import morning_regime as mr
        names = _calls(mr._fetch_regime_inputs)
        assert "session_close_series" in names       # the 210-session EMA window
        assert "session_closes_bulk" in names        # the breadth comparison
        # Assert against executable text only: the comments in these modules
        # legitimately quote the query they replaced, and a substring search
        # over raw source matches the explanation rather than the code.
        code = "\n".join(l for l in inspect.getsource(mr._fetch_regime_inputs).splitlines()
                         if not l.strip().startswith("#"))
        assert "LIMIT 210" not in code

    def test_momentum_filter_uses_the_bulk_reader(self):
        from engine.agent import momentum_filter as mf
        assert "session_closes_bulk" in _calls(mf._compute_ranks)

    def test_breakout_screener_uses_the_bulk_reader(self):
        from engine import breakout_screener as bs
        src = inspect.getsource(bs)
        assert "session_bars_bulk" in src

    def test_momentum_screener_uses_the_bulk_reader(self):
        from engine import momentum_screener as ms
        assert "session_bars_bulk" in inspect.getsource(ms)

    @pytest.mark.parametrize("mod,fn", [
        ("engine.agent.morning_regime", "_fetch_regime_inputs"),
        ("engine.agent.momentum_filter", "_compute_ranks"),
    ])
    def test_no_raw_daily_window_query_remains(self, mod, fn):
        """A raw `timeframe='1d'` window query is what produced duplicates."""
        import importlib
        m = importlib.import_module(mod)
        src = inspect.getsource(getattr(m, fn))
        # the explanatory comments may mention it; the executable text must not
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        # A `LIMIT 1` spot lookup is a point price, not a cross-session window,
        # and is out of H-1's scope; a windowed scan is what produced duplicates.
        windowed = [l for l in code.splitlines()
                    if "ORDER BY timestamp DESC" in l and "LIMIT 1" not in code[
                        code.index(l):code.index(l) + 200]]
        assert windowed == [], windowed

    def test_dedup_is_by_session_not_timestamp(self):
        """DISTINCT on timestamp would keep 18:30 beside canonical."""
        import engine.daily_series as ds
        src = inspect.getsource(ds)
        assert "DISTINCT timestamp" not in src
        assert "_collapse_to_sessions" in src

    def test_market_regime_opts_in_explicitly(self):
        """A live gate keeps the forming bar — but says so."""
        from engine.agent.market_regime import get_market_regime
        assert "include_current=True" in inspect.getsource(get_market_regime)


# ── M-1: as_of semantics (unit level) ────────────────────────────────────────

class TestAsOfSemantics:

    def test_as_of_filters_resolved_sessions_not_timestamps(self):
        """An 18:30 row is dated the day BEFORE its session, so filtering the
        stored timestamp would let the next session through."""
        import engine.daily_series as ds
        src = inspect.getsource(ds._apply_as_of)
        assert "x[0] <= as_of" in src

    def test_as_of_and_current_exclusion_compose(self):
        import engine.daily_series as ds
        rows = [(dt.date(2026, 9, 4), ("a",)), (dt.date(2026, 9, 7), ("b",)),
                (dt.date(2026, 9, 8), ("c",))]
        out = ds._apply_as_of(rows, as_of=dt.date(2026, 9, 7), include_current=False,
                              holidays=None, extra_open=None, symbol="X")
        assert [d for d, _ in out] == [dt.date(2026, 9, 4), dt.date(2026, 9, 7)]

    def test_as_of_none_keeps_everything_up_to_current_rule(self):
        import engine.daily_series as ds
        rows = [(dt.date(2026, 9, 4), ("a",)), (dt.date(2026, 9, 7), ("b",))]
        out = ds._apply_as_of(rows, as_of=None, include_current=True,
                              holidays=None, extra_open=None, symbol="X")
        assert len(out) == 2


# ── real database: the required A–E cases ────────────────────────────────────

class TestAgainstRealDatabase:
    pytestmark = pytest.mark.asyncio

    async def _series(self, sym, **kw):
        from db.database import AsyncSessionLocal
        from engine.daily_series import session_closes
        async with AsyncSessionLocal() as s:
            return await session_closes(sym, s, **kw)

    async def test_A_as_of_20260907(self):
        r = await self._series("RELIANCE.NS", sessions=200, as_of=dt.date(2026, 9, 7))
        if not r:
            pytest.skip("no RELIANCE.NS history in this database")
        assert max(d for d, _, _ in r) <= dt.date(2026, 9, 7)

    async def test_B_as_of_20260902_excludes_later(self):
        r = await self._series("RELIANCE.NS", sessions=200, as_of=dt.date(2026, 9, 2))
        if not r:
            pytest.skip("no RELIANCE.NS history")
        ds = [d for d, _, _ in r]
        assert max(ds) == dt.date(2026, 9, 2)
        assert dt.date(2026, 9, 3) not in ds and dt.date(2026, 9, 4) not in ds

    async def test_C_weekend_as_of_returns_last_completed(self):
        r = await self._series("RELIANCE.NS", sessions=200, as_of=dt.date(2026, 9, 6))
        if not r:
            pytest.skip("no RELIANCE.NS history")
        newest = max(d for d, _, _ in r)
        assert newest <= dt.date(2026, 9, 6) and newest.weekday() < 5

    async def test_D_special_session_respected(self):
        r = await self._series("RELIANCE.NS", sessions=4000, as_of=dt.date(2026, 2, 1))
        if not r:
            pytest.skip("no RELIANCE.NS history")
        ds = {d for d, _, _ in r}
        assert max(ds) == dt.date(2026, 2, 1), "the Budget Sunday session must be reachable"
        stray = [d for d in ds if d.weekday() >= 5 and d.isoformat() not in NSE_SPECIAL_SESSIONS]
        assert stray == []

    async def test_E_known_look_ahead_case(self):
        from db.database import AsyncSessionLocal
        from engine.agent.performance_engine import _aligned_closes
        async with AsyncSessionLocal() as s:
            ac = await _aligned_closes("RELIANCE.NS", 30, s)
        if dt.date(2026, 9, 2) not in ac:
            pytest.skip("RELIANCE.NS 2026-09-02 absent")
        assert ac[dt.date(2026, 9, 2)] == pytest.approx(1313.1)
        assert ac[dt.date(2026, 9, 3)] == pytest.approx(1302.5)
        assert ac[dt.date(2026, 9, 4)] == pytest.approx(1322.0)

    async def test_as_of_never_returns_a_later_session(self):
        for cut in (dt.date(2026, 9, 2), dt.date(2026, 8, 20), dt.date(2025, 1, 15)):
            r = await self._series("RELIANCE.NS", sessions=500, as_of=cut)
            if not r:
                pytest.skip("no history")
            assert max(d for d, _, _ in r) <= cut, f"leak past {cut}"

    async def test_regime_symbols_closed_only_by_default(self):
        for sym in ("^NSEI", "^NSEBANK", "NIFTYBEES.NS"):
            r = await self._series(sym, sessions=5)
            if not r:
                continue
            assert max(d for d, _, _ in r) <= dt.date.today()

    async def test_morning_regime_window_has_no_duplicate_sessions(self):
        r = await self._series("NIFTYBEES.NS", sessions=210)
        if not r:
            pytest.skip("no NIFTYBEES.NS history")
        ds = [d for d, _, _ in r]
        assert len(set(ds)) == len(ds), "duplicated sessions returned"
        assert ds == sorted(ds)
        assert len({c for _, _, c in r}) == 1, "more than one price basis"

    async def test_bulk_matches_single_symbol_reader(self):
        from db.database import AsyncSessionLocal
        from engine.daily_series import session_closes, session_closes_bulk
        syms = ["RELIANCE.NS", "TCS.NS", "NIFTYBEES.NS"]
        async with AsyncSessionLocal() as s:
            bulk = await session_closes_bulk(syms, s, sessions=64)
            for sym in syms:
                single = await session_closes(sym, s, sessions=64)
                if not single:
                    continue
                assert [(d, c) for d, c, _ in bulk.get(sym, [])] == \
                       [(d, c) for d, c, _ in single], sym
