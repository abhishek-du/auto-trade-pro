"""Integrity tests for the candle pipeline fixes of 2026-08-24.

Three defects were found by comparing the database against a direct Kite API
scan of the 24 Aug close:

  * 5m bars stopped at 14:50 IST and 1h at 14:15 IST, because those timeframes
    were fetched per-symbol from yfinance by a task that averaged 657 s against
    a 300 s beat. Fixed by deriving them from the 1m bars already in Postgres.
  * ``sync_long_tail_intraday`` carried two gates that were near-complements,
    so it could only run in a 30-minute sliver once a day.
  * ``hub_universe`` ranks purely on 30-day average turnover, so it could not
    see MARATHON (Rs 135cr on a Rs 0.79cr average) or a stock that listed that
    morning (LALITHAA, Rs 2,459cr — the largest turnover on the exchange).

The database-backed tests run inside a SAVEPOINT that is always rolled back, so
they exercise the real Postgres SQL — ``make_interval``, ``!~``, the ``<>
'NaN'`` guard, ``array_agg`` ordering — without touching live rows. They skip
rather than fail when Postgres is unreachable, so the unit suite still runs on
a bare checkout.
"""

from __future__ import annotations

import datetime as dt

import pytest

pytestmark = pytest.mark.asyncio


# ── DB harness ───────────────────────────────────────────────────────────────

async def _session_or_skip():
    """A session inside an outer transaction the caller must roll back.

    ``join_transaction_mode="create_savepoint"`` is what makes this safe: the
    code under test calls ``session.commit()``, and without it that commit
    would land on the live table. With it, the commit releases a SAVEPOINT and
    the outer transaction still rolls back everything.
    """
    try:
        from sqlalchemy.ext.asyncio import AsyncSession
        from db.database import engine
    except Exception as exc:                       # pragma: no cover
        pytest.skip(f"db layer unavailable: {exc}")

    try:
        conn = await engine.connect()
        trans = await conn.begin()
    except Exception as exc:
        pytest.skip(f"postgres unreachable: {exc}")

    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
    return session, conn, trans


async def _insert_1m(session, symbol, start: dt.datetime, bars):
    """bars: list of (open, high, low, close, volume), one per minute."""
    from sqlalchemy import text
    for i, (o, h, low, c, v) in enumerate(bars):
        await session.execute(
            text(
                "INSERT INTO candles (symbol, timeframe, open, high, low, close,"
                " volume, timestamp) VALUES (:s,'1m',:o,:h,:l,:c,:v,:t)"
            ),
            {"s": symbol, "o": o, "h": h, "l": low, "c": c, "v": v,
             "t": start + dt.timedelta(minutes=i)},
        )


async def _insert_1d(session, symbol, days):
    """days: list of (date, close, volume)."""
    from sqlalchemy import text
    for d, c, v in days:
        await session.execute(
            text(
                "INSERT INTO candles (symbol, timeframe, open, high, low, close,"
                " volume, timestamp) VALUES (:s,'1d',:c,:c,:c,:c,:v,:t)"
            ),
            {"s": symbol, "c": c, "v": v, "t": dt.datetime.combine(d, dt.time(18, 30))},
        )


# ── resampler ────────────────────────────────────────────────────────────────

async def test_resampled_5m_bar_matches_its_source_1m_bars():
    """O/H/L/C/V must be first-open, max-high, min-low, last-close, sum-volume.

    The first version of the upsert left `open` untouched on conflict, which
    welded a stale yfinance open onto a Kite-derived high/low/close: BLUESTONE
    kept open=834.60 when its true open from the 1m bars was 835.00. A bar
    mixed from two feeds is worse than either feed alone, so every column is
    asserted here, not just the close.
    """
    from crawler.candle_resampler import resample_intraday

    session, conn, trans = await _session_or_skip()
    try:
        sym = "__TEST_RESAMPLE__.NS"
        base = dt.datetime(2026, 8, 24, 5, 0)      # 05:00 UTC, a clean 5m boundary
        # deliberately non-monotonic so first/last differ from min/max
        bars = [
            (100.0, 104.0,  99.0, 103.0, 10.0),
            (103.0, 103.5, 101.0, 101.5, 20.0),
            (101.5, 107.0, 101.0, 106.0, 30.0),
            (106.0, 106.5,  98.0, 102.0, 40.0),
            (102.0, 105.0, 100.0, 100.5, 50.0),
        ]
        await _insert_1m(session, sym, base, bars)

        # `now` an hour later so the 05:00 bucket is closed, not forming
        await resample_intraday(
            session, timeframes=("5m",), lookback_minutes=120,
            now=dt.datetime(2026, 8, 24, 6, 0),
        )

        from sqlalchemy import text
        row = (await session.execute(
            text("SELECT open, high, low, close, volume FROM candles "
                 "WHERE symbol=:s AND timeframe='5m' AND timestamp=:t"),
            {"s": sym, "t": base},
        )).fetchone()

        assert row is not None, "no 5m bar was produced for a full 5-minute window"
        o, h, low, c, v = (float(x) for x in row)
        assert o == pytest.approx(100.0), "open must be the FIRST 1m bar's open"
        assert h == pytest.approx(107.0), "high must be the max across the window"
        assert low == pytest.approx(98.0), "low must be the min across the window"
        assert c == pytest.approx(100.5), "close must be the LAST 1m bar's close"
        assert v == pytest.approx(150.0), "volume must be the sum"
    finally:
        await trans.rollback()
        await conn.close()


async def test_rewriting_a_bucket_replaces_every_column():
    """The upsert must overwrite an existing bar wholesale, `open` included.

    This is the path the first version got wrong. Older 5m/1h rows in this
    table came from yfinance; leaving `open` alone on conflict welded a
    yfinance open onto a Kite-derived high/low/close and produced a bar from
    two feeds that no consumer could identify. A fresh-symbol insert never
    reaches ON CONFLICT, so the bug survived a passing insert-path test —
    this seeds a conflicting bar first, deliberately.
    """
    from crawler.candle_resampler import resample_intraday
    from sqlalchemy import text

    session, conn, trans = await _session_or_skip()
    try:
        sym = "__TEST_UPSERT__.NS"
        base = dt.datetime(2026, 8, 24, 5, 0)

        # a pre-existing 5m bar from "another feed", wrong on every column
        await session.execute(
            text("INSERT INTO candles (symbol, timeframe, open, high, low, close,"
                 " volume, timestamp) VALUES (:s,'5m',999.0,999.0,999.0,999.0,7.0,:t)"),
            {"s": sym, "t": base},
        )
        await _insert_1m(session, sym, base, [
            (100.0, 104.0,  99.0, 103.0, 10.0),
            (103.0, 103.5, 101.0, 101.5, 20.0),
            (101.5, 107.0, 101.0, 106.0, 30.0),
            (106.0, 106.5,  98.0, 102.0, 40.0),
            (102.0, 105.0, 100.0, 100.5, 50.0),
        ])

        await resample_intraday(
            session, timeframes=("5m",), lookback_minutes=120,
            now=dt.datetime(2026, 8, 24, 6, 0),
        )

        row = (await session.execute(
            text("SELECT open, high, low, close, volume FROM candles "
                 "WHERE symbol=:s AND timeframe='5m' AND timestamp=:t"),
            {"s": sym, "t": base},
        )).fetchone()
        o, h, low, c, v = (float(x) for x in row)
        assert o == pytest.approx(100.0), "stale `open` survived the upsert"
        assert h == pytest.approx(107.0), "stale `high` survived the upsert"
        assert low == pytest.approx(98.0), "stale `low` survived the upsert"
        assert c == pytest.approx(100.5)
        assert v == pytest.approx(150.0)
    finally:
        await trans.rollback()
        await conn.close()


async def test_forming_bucket_is_not_written():
    """A bucket whose window has not elapsed must not be published.

    Publishing it would hand readers a bar whose high/low/close mutate under
    them, and `compute_indicators` can only exclude a forming bar it can
    identify by timestamp — not one that silently changes.
    """
    from crawler.candle_resampler import resample_intraday
    from sqlalchemy import text

    session, conn, trans = await _session_or_skip()
    try:
        sym = "__TEST_FORMING__.NS"
        base = dt.datetime(2026, 8, 24, 5, 0)
        await _insert_1m(session, sym, base, [(10.0, 11.0, 9.0, 10.5, 1.0)] * 5)

        # now sits INSIDE the 05:05 bucket, so 05:00 is closed but 05:05 is not
        await _insert_1m(session, sym, dt.datetime(2026, 8, 24, 5, 5),
                         [(10.5, 12.0, 10.0, 11.5, 2.0)] * 2)
        await resample_intraday(
            session, timeframes=("5m",), lookback_minutes=60,
            now=dt.datetime(2026, 8, 24, 5, 7),
        )

        stamps = [r[0] for r in (await session.execute(
            text("SELECT timestamp FROM candles WHERE symbol=:s AND timeframe='5m'"
                 " ORDER BY timestamp"), {"s": sym},
        )).fetchall()]
        assert dt.datetime(2026, 8, 24, 5, 0) in stamps, "closed bucket must be written"
        assert dt.datetime(2026, 8, 24, 5, 5) not in stamps, \
            "the bucket still forming at `now` must NOT be written"
    finally:
        await trans.rollback()
        await conn.close()


async def test_hourly_buckets_open_on_the_nse_session_grid():
    """1h bars must open at :45 — NSE opens 09:15 IST = 03:45 UTC.

    Plain epoch flooring would open the hourly bar at 03:00 UTC, half an hour
    before the exchange, and would not line up with the 1h bars already in the
    table (03:45, 04:45, ...) that every consumer reads.
    """
    from crawler.candle_resampler import resample_intraday
    from sqlalchemy import text

    session, conn, trans = await _session_or_skip()
    try:
        sym = "__TEST_HOURGRID__.NS"
        # a full hour of 1m bars starting exactly at the open
        await _insert_1m(session, sym, dt.datetime(2026, 8, 24, 3, 45),
                         [(50.0, 51.0, 49.0, 50.5, 1.0)] * 60)
        await resample_intraday(
            session, timeframes=("1h",), lookback_minutes=180,
            now=dt.datetime(2026, 8, 24, 5, 0),
        )
        stamps = [r[0] for r in (await session.execute(
            text("SELECT timestamp FROM candles WHERE symbol=:s AND timeframe='1h'"),
            {"s": sym},
        )).fetchall()]
        assert stamps, "no hourly bar produced"
        assert all(t.minute == 45 for t in stamps), \
            f"hourly bars must open at :45, got {stamps}"
    finally:
        await trans.rollback()
        await conn.close()


# ── hub_universe fast lane ───────────────────────────────────────────────────

async def test_fast_lane_admits_a_stock_that_woke_up():
    """One huge recent session must get a symbol in, whatever its average.

    This is MARATHON on 24 Aug: a Rs 0.79cr 30-day average — six times under
    the Rs 5cr bar — and Rs 135cr on the day. Ranking on the average alone can
    never see that stock, which is exactly the move worth scanning for.
    """
    from engine.hub_universe import rebuild_hub_universe
    from sqlalchemy import text

    session, conn, trans = await _session_or_skip()
    try:
        sym = "__TESTWOKEUP__.NS"
        today = dt.date.today()
        # The 30-day AVERAGE must stay under the Rs 5cr threshold, or the
        # ordinary ranked query admits the symbol and the fast lane is never
        # exercised. A first draft used 20 quiet days at Rs 0.8cr plus one
        # Rs 100cr day -- an average of Rs 5.52cr, over the bar -- so the test
        # passed with the fast lane deleted entirely.
        # 26 sessions at Rs 0.01cr + one at Rs 50cr -> average Rs 1.86cr.
        quiet = [(today - dt.timedelta(days=d), 10.0, 10_000.0) for d in range(2, 28)]
        spike = [(today - dt.timedelta(days=1), 100.0, 5_000_000.0)]   # Rs 50cr
        await _insert_1d(session, sym, quiet + spike)

        summary = await rebuild_hub_universe(
            session, top_n=20000, min_turnover_cr=5.0,
            fast_lane_min_turnover_cr=5.0,
        )
        row = (await session.execute(
            text("SELECT rank FROM hub_universe WHERE symbol=:s"), {"s": sym},
        )).fetchone()
        assert row is not None, "a symbol with one huge recent session must be admitted"
        # Ranked entries occupy 1..ranked; the fast lane appends after them.
        # Asserting the rank proves WHICH path admitted it, so deleting the
        # fast lane fails this test instead of silently passing.
        assert row[0] > summary["ranked"], (
            "symbol was admitted by the turnover ranking, not the fast lane — "
            "the test data no longer isolates the fast lane"
        )
    finally:
        await trans.rollback()
        await conn.close()


async def test_fast_lane_ignores_a_stock_that_merely_trades_thinly():
    """Sparse history must NOT be mistaken for a new listing.

    An early draft admitted anything with few recent bars, which matched every
    symbol that simply did not print on most days — 1,034 of them — and buried
    the universe in illiquid noise. The fast lane must key on how much money
    actually changed hands, never on how sparse the history looks.
    """
    from engine.hub_universe import rebuild_hub_universe
    from sqlalchemy import text

    session, conn, trans = await _session_or_skip()
    try:
        sym = "__TESTTHIN__.NS"
        today = dt.date.today()
        # long-established (first bar 300 days ago) but only prints occasionally,
        # and never for meaningful money
        old = [(today - dt.timedelta(days=300), 10.0, 100.0)]
        sparse = [(today - dt.timedelta(days=d), 10.0, 100.0) for d in (3, 9, 17)]
        await _insert_1d(session, sym, old + sparse)

        await rebuild_hub_universe(
            session, top_n=20000, min_turnover_cr=5.0,
            fast_lane_min_turnover_cr=5.0,
        )
        present = (await session.execute(
            text("SELECT COUNT(*) FROM hub_universe WHERE symbol=:s"), {"s": sym},
        )).scalar()
        assert present == 0, "a symbol that never traded real money must not be admitted"
    finally:
        await trans.rollback()
        await conn.close()


# ── daily-coverage watchdog ──────────────────────────────────────────────────

_WATCHDOG_SQL = """
WITH per_day AS (
    SELECT timestamp::date AS d, COUNT(DISTINCT symbol) AS n
    FROM candles
    WHERE timeframe = '1d'
      AND timestamp >= CURRENT_DATE - 30
    GROUP BY 1
)
SELECT
  (SELECT COALESCE(MAX(n), 0) FROM per_day WHERE d >= CURRENT_DATE - 4),
  (SELECT COALESCE(MAX(n), 0) FROM per_day)
"""

_WATCHDOG_SQL_OLD = """
SELECT
  (SELECT COUNT(DISTINCT symbol) FROM candles
     WHERE timeframe='1d' AND timestamp::date = (
       SELECT MAX(timestamp)::date FROM candles WHERE timeframe='1d')),
  (SELECT COUNT(DISTINCT symbol) FROM candles
     WHERE timeframe='1d'
       AND timestamp::date < (SELECT MAX(timestamp)::date FROM candles WHERE timeframe='1d')
       AND timestamp >= CURRENT_DATE - 7)
"""


async def test_daily_watchdog_stays_quiet_when_a_small_partial_write_is_newest():
    """A 33-symbol write must not read as a 99% coverage collapse.

    Several tasks write 1d rows for tiny symbol sets — kite_sync_candles covers
    a 33-name watchlist at 10:00 UTC — while the full ~8,000-symbol write lands
    the next morning. Keying the check on MAX(timestamp) therefore names a date
    holding a handful of symbols and screams collapse every afternoon on
    perfectly healthy data. Measured on 24 Aug the old query reported 4 symbols
    against a baseline of 8,410; the replacement reported 8,071 against 8,179.
    An alert that fires daily on healthy data is worse than none, because it
    teaches everyone to ignore it.
    """
    from sqlalchemy import text

    session, conn, trans = await _session_or_skip()
    try:
        today = dt.date.today()
        # a healthy full-universe write two days ago ...
        for i in range(400):
            await _insert_1d(session, f"__WD_FULL_{i}__.NS",
                             [(today - dt.timedelta(days=2), 10.0, 1000.0)])
        # ... then a tiny partial write that is NEWER
        for i in range(5):
            await _insert_1d(session, f"__WD_PART_{i}__.NS",
                             [(today, 10.0, 1000.0)])

        newest, baseline = (await session.execute(text(_WATCHDOG_SQL))).fetchone()
        assert baseline >= 400
        assert newest >= baseline * 0.5, (
            f"watchdog would false-alarm: newest={newest} baseline={baseline}"
        )

        # and confirm the OLD formulation really did false-alarm on this shape,
        # so this test is anchored to a defect rather than to an assumption
        old_newest, old_baseline = (
            await session.execute(text(_WATCHDOG_SQL_OLD))
        ).fetchone()
        assert old_baseline > 100 and old_newest < old_baseline * 0.5, (
            "the pre-fix query no longer false-alarms on this shape; "
            "re-derive what this test is guarding"
        )
    finally:
        await trans.rollback()
        await conn.close()


@pytest.mark.parametrize(
    "newest,baseline,stalled,why",
    [
        (8071, 8179, False, "healthy: full write landed in the window"),
        (4,    8410, True,  "the real 24 Aug stall shape — nothing but index rows"),
        (12,   8000, True,  "a token write is still a stall"),
        (4000, 8000, False, "exactly at the 50% floor is not a stall"),
        (3999, 8000, True,  "just under the floor is"),
        (5,    100,  False, "cold start: too little history to judge, stay quiet"),
        (0,    0,    False, "empty table must not alert"),
    ],
)
async def test_daily_coverage_stall_decision(newest, baseline, stalled, why):
    """The watchdog's verdict, tested directly rather than through the query.

    A cold start is the case worth pinning: with almost no history every
    comparison looks like a collapse, and an alert nobody can act on trains
    people to ignore the one that matters.
    """
    from tasks.india_tasks import daily_coverage_is_stalled
    assert daily_coverage_is_stalled(newest, baseline, 0.5) is stalled, why


# ── long-tail gating (no DB needed) ──────────────────────────────────────────

@pytest.mark.parametrize(
    "hour,minute,weekday,market_open,expect_run",
    [
        (11, 0,  0, True,  False),   # mid-session: leave the slot to trading
        (16, 0,  0, False, True),    # after close on a weekday: this is its window
        (20, 0,  0, False, True),    # later the same evening: still fine
        (8,  0,  0, False, False),   # pre-open: the session has not happened yet
        (16, 0,  5, False, False),   # Saturday: nothing to sync
    ],
)
async def test_long_tail_runs_after_close_and_only_then(
    monkeypatch, hour, minute, weekday, market_open, expect_run
):
    """The two gates must not cancel each other out.

    Until 24 Aug the wrapper required `_is_india_trading_window()` (09:15-16:00
    IST) while the body refused when `is_nse_market_open()` (09:15-15:30) was
    true. Between them only 15:30-16:00 survived — once a day at best against
    a 30-minute beat — so long-tail coverage silently stopped being refreshed.
    """
    import datetime as _dt
    import tasks.india_tasks as it
    import crawler.india_price_feed as ipf

    class _FixedNow(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            base = _dt.datetime(2026, 8, 24, hour, minute)
            # 24 Aug 2026 is a Monday; shift to land on the requested weekday
            base += _dt.timedelta(days=weekday)
            return base.replace(tzinfo=tz) if tz else base

    monkeypatch.setattr(it.datetime, "datetime", _FixedNow)
    monkeypatch.setattr(ipf, "is_nse_market_open", lambda: market_open)

    called = {"n": 0}

    async def _fake_sync(session):
        called["n"] += 1
        return {"ok": True}

    import crawler.upstox_historical as uh
    monkeypatch.setattr(uh, "sync_long_tail_intraday_upstox", _fake_sync, raising=False)

    result = await it._sync_long_tail_intraday()

    if expect_run:
        assert "skipped" not in result, f"should have run, got {result}"
    else:
        assert result.get("skipped"), f"should have skipped, got {result}"
