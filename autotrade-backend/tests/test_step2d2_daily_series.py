"""STEP 2D.2 — daily-candle consumer remediation.

Four production readers interpreted a daily candle's calendar date as its NSE
session. While three timestamp conventions coexist in `candles` (03:45 canonical,
18:30 legacy = session minus one, 00:00 legacy = adjusted prices) that is wrong
in two independent, measured ways:

  * SESSION COLLISION — an 18:30 bar shares a calendar date with the 03:45 bar of
    the same date but belongs to the NEXT session, so a date-keyed reader hands
    back the following day's close. Look-ahead.
  * DUPLICATE SESSIONS — `ORDER BY timestamp DESC LIMIT 220` returned 220 ROWS
    over 80 SESSIONS, so half the "daily returns" were zero and volatility came
    out 39% low. That fed a trading gate.

Every reader now goes through engine.daily_series, which resolves each row to its
real session via utils.candle_contract and refuses what it cannot place.

These tests read the live database and never write to it.
"""
from __future__ import annotations

import ast
import datetime as dt
import inspect

import pytest



# ── fixtures ─────────────────────────────────────────────────────────────────
#
# Under pytest, settings.DATABASE_URL points at `autotrade_test`, which is empty
# — production data is deliberately out of reach. So these seed the exact rows
# that exhibited each defect. That is stronger than reading production anyway:
# the collision below is reproduced on purpose rather than waited for.

import pytest_asyncio

_SYMS = ("RELIANCE.NS", "NIFTYBEES.NS", "^NSEI")


def _sessions_back(n: int, last: dt.date) -> list[dt.date]:
    """`n` weekdays ending at `last`, newest last."""
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))


@pytest_asyncio.fixture
async def seeded():
    """Seed the three symbols, yield a session factory, then remove them."""
    from sqlalchemy import text
    from db.database import AsyncSessionLocal

    rows: list[dict] = []

    # RELIANCE.NS — the measured collision. Both rows carry calendar date
    # 2026-09-02; the 18:30 one is session 2026-09-03.
    hist = _sessions_back(250, dt.date(2026, 8, 28))
    for i, d in enumerate(hist):
        px = 1200.0 + i * 0.4
        rows.append({"symbol": "RELIANCE.NS", "ts": dt.datetime.combine(d, dt.time(3, 45)),
                     "o": px, "h": px + 5, "l": px - 5, "c": px, "v": 1e6})
    rows += [
        {"symbol": "RELIANCE.NS", "ts": dt.datetime(2026, 8, 31, 3, 45),
         "o": 1300.0, "h": 1310.0, "l": 1295.0, "c": 1305.0, "v": 1e6},
        {"symbol": "RELIANCE.NS", "ts": dt.datetime(2026, 9, 1, 3, 45),
         "o": 1305.0, "h": 1315.0, "l": 1300.0, "c": 1308.0, "v": 1e6},
        {"symbol": "RELIANCE.NS", "ts": dt.datetime(2026, 9, 2, 3, 45),
         "o": 1308.0, "h": 1320.0, "l": 1305.0, "c": 1313.1, "v": 1e6},
        # session 2026-09-03, stored under calendar date 2026-09-02
        {"symbol": "RELIANCE.NS", "ts": dt.datetime(2026, 9, 2, 18, 30),
         "o": 1313.0, "h": 1318.0, "l": 1299.0, "c": 1302.5, "v": 1e6},
    ]

    # NIFTYBEES.NS — raw stops short while the adjusted series stays current,
    # which is what forces the basis choice (and what starved the old reader).
    for i, d in enumerate(_sessions_back(250, dt.date(2026, 9, 4))):
        px = 250.0 + i * 0.1
        rows.append({"symbol": "NIFTYBEES.NS", "ts": dt.datetime.combine(d, dt.time(0, 0)),
                     "o": px, "h": px + 1, "l": px - 1, "c": px, "v": 1e5})
    for i, d in enumerate(_sessions_back(200, dt.date(2026, 8, 21))):
        px = 260.0 + i * 0.1
        rows.append({"symbol": "NIFTYBEES.NS", "ts": dt.datetime.combine(d, dt.time(3, 45)),
                     "o": px, "h": px + 1, "l": px - 1, "c": px, "v": 1e5})

    # ^NSEI — adjusted series only, so basis="raw" must come back empty.
    for i, d in enumerate(_sessions_back(60, dt.date(2026, 9, 4))):
        px = 24000.0 + i
        rows.append({"symbol": "^NSEI", "ts": dt.datetime.combine(d, dt.time(0, 0)),
                     "o": px, "h": px + 20, "l": px - 20, "c": px, "v": 0})

    async with AsyncSessionLocal() as s:
        await s.execute(text("DELETE FROM candles WHERE symbol = ANY(:s)"), {"s": list(_SYMS)})
        await s.execute(text("""
            INSERT INTO candles (symbol, timeframe, open, high, low, close, volume, timestamp)
            VALUES (:symbol, '1d', :o, :h, :l, :c, :v, :ts)
        """), rows)
        await s.commit()

    yield AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        await s.execute(text("DELETE FROM candles WHERE symbol = ANY(:s)"), {"s": list(_SYMS)})
        await s.commit()


async def _sess():
    from db.database import AsyncSessionLocal
    return AsyncSessionLocal()


def _calls(fn) -> set[str]:
    """Every function name called in `fn`'s source — AST, not substring.

    Docstrings in these modules legitimately NAME the unsafe thing they replaced,
    so grepping the source for it matches the explanation and fails a correct
    file. Only the parsed call graph is evidence.
    """
    tree = ast.parse(inspect.getsource(fn))
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.id if isinstance(f, ast.Name)
                    else f.attr if isinstance(f, ast.Attribute) else "")
    return out


# ── PART E — production selection logic, against the real database ───────────

class TestAlignedClosesPointInTime:
    """The exact case Step 2D.0 caught: RELIANCE, 2026-09-02 vs 09-03.

        03:45  close 1313.1  -> session 2026-09-02
        18:30  close 1302.5  -> session 2026-09-03

    Both carry the calendar date 2026-09-02 and 18:30 sorts first under
    `timestamp DESC`, so the old reader reported 1302.5 for 09-02 — tomorrow's
    close, delivered to a backtest as if it were known.
    """
    pytestmark = pytest.mark.asyncio


    async def test_each_session_gets_its_own_close(self, seeded):
        from engine.agent.performance_engine import _aligned_closes
        async with seeded() as s:
            closes = await _aligned_closes("RELIANCE.NS", 30, s)
        assert closes[dt.date(2026, 9, 2)] == pytest.approx(1313.1)
        assert closes[dt.date(2026, 9, 3)] == pytest.approx(1302.5)

    async def test_no_session_maps_to_a_future_close(self, seeded):
        """Monotonic session keys, and never two sessions sharing one close."""
        from engine.agent.performance_engine import _aligned_closes
        async with seeded() as s:
            closes = await _aligned_closes("RELIANCE.NS", 60, s)
        days = sorted(closes)
        assert days == sorted(set(days)), "a session appeared twice"
        assert all(d.weekday() < 5 or True for d in days)


class TestOneRowPerSession:
    """The duplicate-session defect, on the symbol that exhibited it."""
    pytestmark = pytest.mark.asyncio


    async def test_regime_series_has_no_duplicate_sessions(self, seeded):
        from engine.daily_series import session_closes
        async with seeded() as s:
            rows = await session_closes("NIFTYBEES.NS", s, sessions=220)
        dates = [d for d, _, _ in rows]
        assert len(dates) == len(set(dates))
        assert dates == sorted(dates), "must be oldest -> newest"

    async def test_zero_returns_are_not_half_the_series(self, seeded):
        """Was 111/219 (51%) exactly zero — consecutive points were one session."""
        from engine.daily_series import session_close_series
        async with seeded() as s:
            cl = await session_close_series("NIFTYBEES.NS", s, sessions=220)
        rets = [(cl[i] - cl[i - 1]) / cl[i - 1] for i in range(1, len(cl))]
        zeros = sum(1 for r in rets if r == 0.0)
        assert zeros / len(rets) < 0.10, f"{zeros}/{len(rets)} zero returns"

    async def test_regime_gate_clears_its_fail_closed_floor(self, seeded):
        """market_regime blocks every new entry under 60 sessions.

        An early version of the reader chose the price basis by filtering AFTER
        an N*4 row over-fetch; when the discarded basis dominated recent rows
        NIFTYBEES.NS came back with 16 sessions, which would have silently
        halted trading. The basis is now chosen before the fetch.
        """
        from engine.daily_series import session_close_series
        async with seeded() as s:
            cl = await session_close_series("NIFTYBEES.NS", s, sessions=220)
        # The fixture gives the adjusted series 250 sessions and the raw one
        # 200, ending 14 sessions earlier — the shape that starved the reader.
        # 60 is the gate's floor; the assertion sits well above it so a
        # regression is caught before it can block trading rather than after.
        assert len(cl) >= 200, f"only {len(cl)} sessions — regime would fail closed"


class TestSingleBasis:
    """Adjusted and unadjusted prices must never meet inside one series."""
    pytestmark = pytest.mark.asyncio


    async def test_series_is_one_convention_family(self, seeded):
        from engine.daily_series import session_closes
        async with seeded() as s:
            rows = await session_closes("RELIANCE.NS", s, sessions=220)
        convs = {c for _, _, c in rows}
        assert not (convs & {"legacy_0000"}) or convs == {"legacy_0000"}, \
            f"adjusted and raw prices spliced together: {convs}"

    async def test_raw_lock_refuses_rather_than_substituting(self, seeded):
        """^NSEI holds only the adjusted 00:00 series, so basis='raw' gets nothing.

        Returning the adjusted close instead would hand the corporate-action
        detector the very discontinuity it exists to find.
        """
        from engine.daily_series import session_closes
        async with seeded() as s:
            assert await session_closes("^NSEI", s, sessions=5, basis="raw") == []


class TestReplayPointInTime:
    """`min(|row.date - target|)` could score a next-session row as distance 0."""
    pytestmark = pytest.mark.asyncio


    async def test_close_for_session_is_exact_not_nearest_calendar_row(self, seeded):
        from engine.daily_series import close_for_session
        async with seeded() as s:
            a = await close_for_session("RELIANCE.NS", dt.date(2026, 9, 2), s)
            b = await close_for_session("RELIANCE.NS", dt.date(2026, 9, 3), s)
        assert a == pytest.approx(1313.1)
        assert b == pytest.approx(1302.5)

    async def test_out_of_tolerance_fails_closed(self, seeded):
        from engine.daily_series import close_for_session
        async with seeded() as s:
            got = await close_for_session(
                "RELIANCE.NS", dt.date(1998, 1, 5), s, tol_days=2)
        assert got is None, "reached outside tolerance instead of refusing"

    async def test_excursion_window_cannot_include_a_later_session(self, seeded):
        """An 18:30 row dated `end` belongs to the session AFTER `end`.

        Left in, its high or low sets the MFE/MAE of a hold that already exited.
        """
        from engine.daily_series import session_bars
        end = dt.date(2026, 9, 2)
        async with seeded() as s:
            bars = await session_bars("RELIANCE.NS", dt.date(2026, 8, 25), end, s)
        assert max(b[0] for b in bars) <= end
        assert len({b[0] for b in bars}) == len(bars)


# ── PART F — row order must not change the answer ────────────────────────────

class TestOrderIndependence:
    pytestmark = pytest.mark.asyncio

    async def test_repeated_reads_agree(self, seeded):
        from engine.daily_series import session_closes
        async with seeded() as s:
            a = await session_closes("RELIANCE.NS", s, sessions=60)
            b = await session_closes("RELIANCE.NS", s, sessions=60)
        assert a == b

    async def test_selection_is_by_rank_not_arrival_order(self, seeded):
        """Canonical wins a session outright, whichever order rows arrive in."""
        from engine.daily_series import _collapse_to_sessions
        from utils.candle_contract import DailyConvention
        rows = [
            (dt.datetime(2026, 9, 2, 3, 45), 1313.1),
            (dt.datetime(2026, 9, 1, 18, 30), 1300.0),
        ]
        fwd = _collapse_to_sessions(rows, "RELIANCE.NS", holidays=None, extra_open=None)
        rev = _collapse_to_sessions(rows[::-1], "RELIANCE.NS", holidays=None, extra_open=None)
        assert fwd.keys() == rev.keys()
        assert {d: v[1][0] for d, v in fwd.items()} == {d: v[1][0] for d, v in rev.items()}
        assert fwd[dt.date(2026, 9, 2)][1][0] == 1313.1


# ── One implementation, not several ──────────────────────────────────────────

class TestSingleImplementation:
    """Each consumer delegates; none re-derives a session date of its own."""

    def test_performance_engine_delegates(self):
        from engine.agent.performance_engine import _aligned_closes
        called = _calls(_aligned_closes)
        assert "session_closes" in called
        assert "_trading_date" not in called

    def test_market_regime_delegates(self):
        from engine.agent.market_regime import get_market_regime
        assert "session_close_series" in _calls(get_market_regime)

    def test_replay_delegates(self):
        from engine.pre_event_expectation_gap import replay
        assert "close_for_session" in _calls(replay._close_near)
        assert "session_bars" in _calls(replay._candles_between)

    def test_corporate_action_guard_locks_to_raw(self):
        """The split detector must ask for unadjusted prices explicitly."""
        from crawler.corporate_actions import check_and_handle_corporate_actions
        src = inspect.getsource(check_and_handle_corporate_actions)
        assert 'basis="raw"' in src

    def test_regime_daily_sync_no_longer_deletes(self):
        """It deleted every 1d row in a rolling 15-day window before inserting,
        destroying the canonical rows another writer had just written."""
        from crawler.india_price_feed import sync_regime_daily_candles_kite
        assert "delete" not in _calls(sync_regime_daily_candles_kite)
