"""THE session-correct reader for daily candles. One implementation.

WHY THIS EXISTS
---------------
`candles` holds three daily timestamp conventions at once (Step 2C.1):

    03:45 UTC  canonical — session open, current writer
    18:30 UTC  legacy    — the session date MINUS one
    00:00 UTC  legacy    — the session date, adjusted-price series

Every reader that grouped rows by calendar date was therefore wrong in two
independent ways, and both were measured on live data:

  1. SESSION COLLISION. An 18:30 bar shares a calendar date with the 03:45 bar
     of the SAME date but belongs to the NEXT session, so a date-keyed reader
     could hand a session the following day's close. `_aligned_closes` did
     exactly that until Step 2D.0.1.

  2. DUPLICATE SESSIONS. `ORDER BY timestamp DESC LIMIT 220` on NIFTYBEES.NS
     returned 220 rows covering only 80 distinct sessions — 140 repeats. The
     returns series built from it had 111 of 219 values exactly zero (51%),
     because consecutive "days" were the same session under two conventions,
     and volatility came out 39% too low.

PRICE BASIS IS A SEPARATE PROBLEM FROM SESSION DATE
---------------------------------------------------
The 00:00 series is corporate-action ADJUSTED (19.0% of its closes carry more
than two decimal places, versus 0.0% of the 18:30 series — NSE quotes to two).
Upstox 03:45/18:30 is RAW. Splicing them inside one returns series injects a
step change at the join that looks exactly like a real move.

So `single_basis=True` (the default) never mixes: when a window contains any raw
Upstox rows, the adjusted 00:00 rows are dropped from it; when a window is
entirely 00:00 — which is the case for ^NSEI, whose only source is the yfinance
series — it is used consistently. One basis per series, always.

NSE WEEKEND SESSIONS (corrected in Step 2H.1)
---------------------------------------------
NSE runs a handful of WEEKEND sessions — Union Budget Saturdays, Diwali Muhurat,
and the occasional disaster-recovery drill. Resolving those needs the exchange
calendar, and this module previously refused them because no caller ever passed
one. That was defensible while nothing stored those bars; once the canonical
backfill began writing them it silently discarded real data — 10 of RELIANCE's
2,480 canonical rows.

The shared curated calendar (utils.candle_contract.NSE_SPECIAL_SESSIONS) is now
the default here, so writer and reader agree. It is ten specific documented
dates, not a rule that weekends are tradeable: an ordinary Saturday is still
refused, and a caller can still pass its own set, or an empty set for strict
weekday-only behaviour.

Callers get the convention back so they can audit what they were served.
"""
from __future__ import annotations

import datetime as _dt
import math as _math

from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.candle_contract import (
    NSE_SPECIAL_SESSIONS,
    DailyConvention,
    convention_rank,
    is_nse_trading_session,
    resolve_daily_session_date,
)
from utils.logger import logger

# Rows to pull per distinct session wanted. Measured: NIFTYBEES.NS needs 496
# rows to yield its newest 220 sessions (~2.3x). 4x plus a floor is comfortable
# headroom without dragging the whole history back for a 20-session request.
_OVERFETCH = 4
_MIN_FETCH = 200

_RAW_TIMES = ("03:45", "18:30")           # Upstox, unadjusted
_ALL_TIMES = ("03:45", "18:30", "00:00")



# ── The as-of / current-session contract (Step 2K, H-2 + M-1) ────────────────

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
NSE_CLOSE_IST = _dt.time(15, 30)


def current_open_session(
    *, now_ist: _dt.datetime | None = None,
    holidays: set[str] | None = None,
    extra_open: set[str] | None = None,
) -> _dt.date | None:
    """The session that is IN PROGRESS right now, or None.

    Determined from the NSE calendar and the clock — deliberately NOT from
    whether the database happens to hold a row. A row's existence says only that
    a writer ran; it says nothing about whether the session has finished. The
    regime writer refreshes today's bar every five minutes, so "a row exists"
    would classify a half-finished session as complete.

    A session is in progress when today is a trading day and the clock is before
    15:30 IST. After the close the day's bar is settled and counts as completed.
    """
    now = now_ist or _dt.datetime.now(_IST)
    today = now.date()
    if not is_nse_trading_session(today, holidays, extra_open):
        return None
    return today if now.time() < NSE_CLOSE_IST else None


def _apply_as_of(
    ordered: list, *, as_of: _dt.date | None, include_current: bool,
    holidays: set[str] | None, extra_open: set[str] | None, symbol: str,
) -> list:
    """Trim resolved sessions to the as-of / completed-session contract.

    `as_of` is applied to the RESOLVED session date, never to the raw stored
    timestamp — an 18:30 row carries the calendar date of the day before its
    session, so filtering on the timestamp would keep a session that is actually
    after the cutoff.
    """
    if as_of is not None:
        ordered = [x for x in ordered if x[0] <= as_of]
    if not include_current:
        cur = current_open_session(holidays=holidays, extra_open=extra_open)
        if cur is not None:
            before = len(ordered)
            ordered = [x for x in ordered if x[0] != cur]
            if before != len(ordered):
                logger.debug(
                    f"[daily_series] {symbol}: excluded in-progress session {cur} "
                    f"(include_current=False)")
    return ordered


async def _preferred_times(
    symbol: str, session: AsyncSession, *, single_basis: bool = True,
    basis: str | None = None,
) -> tuple[str, ...]:
    """Which timestamp-of-day family to read for `symbol`, decided ONCE.

    The basis is chosen BEFORE fetching, not by filtering afterwards. An earlier
    version over-fetched N*4 rows, resolved them, then dropped one basis; when
    the dropped basis dominated the newest rows that left far too few sessions —
    NIFTYBEES.NS returned 16 of a requested 220, below the 60-session floor at
    which market_regime fails closed and blocks every new entry.

    Deciding per SYMBOL rather than per CALL also keeps separate lookups
    consistent: replay reads an entry close and an exit close in two queries, and
    a return computed from a raw entry against an adjusted exit is a corporate
    action masquerading as a move.
    """
    if basis == "raw":
        # Caller requires UNADJUSTED prices and would rather have nothing than
        # an adjusted substitute. The corporate-action detector is the case:
        # it compares a daily close against the next morning's raw intraday
        # open, so an adjusted close on one side of that comparison IS the
        # split it is trying to detect.
        return _RAW_TIMES
    if not single_basis:
        return _ALL_TIMES
    cov = {r[0]: r[1] for r in (await session.execute(_text("""
        SELECT to_char(timestamp,'HH24:MI') hm, max(timestamp)::date
        FROM candles WHERE symbol = :s AND timeframe = '1d'
          AND to_char(timestamp,'HH24:MI') IN ('03:45','18:30','00:00')
        GROUP BY 1
    """), {"s": symbol})).all()}
    raw_max = max((v for k, v in cov.items() if k in _RAW_TIMES), default=None)
    adj_max = cov.get("00:00")
    if raw_max and adj_max:
        # Whichever basis reaches furthest forward wins; raw takes a tie because
        # it needs no adjustment assumptions. Recency matters because these feed
        # a TRADING GATE: a pure-but-stale series is a worse input than a
        # consistent one that is current.
        keep_raw = raw_max >= adj_max
        logger.debug(
            f"[daily_series] {symbol}: basis="
            f"{'raw Upstox' if keep_raw else 'adjusted 00:00'} "
            f"(raw to {raw_max}, adjusted to {adj_max})"
        )
        return _RAW_TIMES if keep_raw else ("00:00",)
    if adj_max:
        return ("00:00",)
    if raw_max:
        return _RAW_TIMES
    return _ALL_TIMES


def _collapse_to_sessions(
    rows, symbol: str, *, holidays, extra_open
) -> dict[_dt.date, tuple[int, tuple]]:
    """Resolve raw rows to sessions, keeping the best-ranked row per session.

    READER/WRITER SYMMETRY (Step 2H.1)
    ----------------------------------
    `extra_open` names the dates NSE traded that the weekday rule would refuse.
    Every function in this module already accepted it — but no caller ever
    supplied one, so it was always None and the resolver refused every weekend
    session. Once the backfill began STORING those bars that became a real loss:
    10 of RELIANCE's 2,480 canonical rows were written correctly and then
    dropped at read time.

    So when a caller says nothing, the shared curated calendar applies. Passing
    an explicit set still overrides it, and passing an empty set restores the
    strict weekday-only behaviour. An ordinary Saturday is still refused — the
    calendar holds ten specific documented dates, not a rule about weekends.
    """
    if extra_open is None:
        extra_open = NSE_SPECIAL_SESSIONS
    best: dict[_dt.date, tuple[int, tuple]] = {}
    refused = 0
    for row in rows:
        # NaN prices are stored, rarely but really: THELEELA.NS and LTFOODS.NS
        # both carry a 2026-06-22 daily bar with NaN OHLC and a genuine volume,
        # a yfinance artifact. One NaN entering a returns series propagates
        # through every EMA, ROC and stdev computed from it, so it is dropped
        # here rather than left to surface as an unexplained NaN in a regime
        # score. (Postgres cannot filter these in SQL the obvious way: it
        # defines NaN = NaN as TRUE, so `close <> close` matches nothing.)
        if any(v is not None and isinstance(v, float) and _math.isnan(v)
               for v in row[1:]):
            refused += 1
            continue
        res = resolve_daily_session_date(
            symbol, "1d", row[0], holidays=holidays, extra_open=extra_open)
        if not res.usable or res.session_date is None:
            refused += 1
            continue
        rank = convention_rank(res.convention)
        prev = best.get(res.session_date)
        if prev is None or rank < prev[0]:
            best[res.session_date] = (rank, (*row[1:], res.convention))
    if refused:
        logger.debug(f"[daily_series] {symbol}: refused {refused} unresolvable daily rows")
    return best


async def session_closes(
    symbol: str,
    session: AsyncSession,
    *,
    sessions: int = 220,
    as_of: _dt.date | None = None,
    include_current: bool = False,
    holidays: set[str] | None = None,
    extra_open: set[str] | None = None,
    single_basis: bool = True,
    basis: str | None = None,
) -> list[tuple[_dt.date, float, str]]:
    """Newest `sessions` daily closes, one row per NSE session, oldest first.

    THE AS-OF CONTRACT (Step 2K)
    ----------------------------
    `include_current=False` — the DEFAULT — returns COMPLETED sessions only. An
    in-progress bar never appears in output that presents itself as finished
    sessions. Completion is decided by the NSE calendar and the clock, not by
    whether a row exists: the regime writer refreshes today's bar every five
    minutes, so row-presence would have called a half-finished session complete.
    Measured 2026-09-08 at 15:14 IST, NIFTYBEES's newest "session" was that day's
    partial bar (270.05 stored against 270.22 live) while RELIANCE correctly
    showed the previous close — two different as-of points in one feature row.

    `include_current=True` is the explicit opt-in for a live intraday gate that
    genuinely wants the forming bar.

    `as_of=date(...)` bounds the result to sessions on or before that date, for
    point-in-time evaluation. It filters the RESOLVED session, not the stored
    timestamp — an 18:30 row is dated the day before the session it describes,
    so filtering the raw timestamp would let the next session through.

    Returns (session_date, close, convention). Never returns two entries for one
    session, never returns a row whose convention could not be recognised, and —
    with `single_basis` — never mixes adjusted and raw prices in one result.
    """
    times = await _preferred_times(symbol, session,
                                   single_basis=single_basis, basis=basis)

    limit = max(_MIN_FETCH, (sessions + 5) * _OVERFETCH)
    rows = (await session.execute(_text("""
        SELECT timestamp, close FROM candles
        WHERE symbol = :s AND timeframe = '1d'
          AND to_char(timestamp,'HH24:MI') = ANY(:t)
        ORDER BY timestamp DESC LIMIT :n
    """), {"s": symbol, "t": list(times), "n": limit})).all()

    best = _collapse_to_sessions(rows, symbol, holidays=holidays, extra_open=extra_open)

    ordered = sorted(best.items())
    ordered = _apply_as_of(ordered, as_of=as_of, include_current=include_current,
                           holidays=holidays, extra_open=extra_open, symbol=symbol)

    out = [(d, v[0], v[1].value) for d, (_, v) in ordered]
    return out[-sessions:] if sessions else out


async def session_close_series(
    symbol: str,
    session: AsyncSession,
    *,
    sessions: int = 220,
    **kw,
) -> list[float]:
    """Closes only, oldest -> newest. The shape indicator code expects."""
    return [c for _, c, _ in await session_closes(symbol, session,
                                                  sessions=sessions, **kw)]


async def close_for_session(
    symbol: str,
    target: _dt.date,
    session: AsyncSession,
    *,
    tol_days: int = 4,
    holidays: set[str] | None = None,
    extra_open: set[str] | None = None,
    basis: str | None = None,
    as_of: _dt.date | None = None,
) -> float | None:
    """Close for `target`, or the nearest session within `tol_days`.

    Nearest is measured in SESSION dates, not calendar dates. The previous
    implementation took `min(|row.timestamp.date() - target|)`, which on mixed
    conventions could pick an 18:30 row whose calendar date matched `target`
    exactly while its session was the following day — handing a replay the
    future. Returns None rather than guessing when nothing is in tolerance.
    """
    times = await _preferred_times(symbol, session, basis=basis)
    # +3 calendar days of slack so an 18:30 row sitting one day BEFORE the
    # tolerance edge — whose session falls inside it — is still fetched.
    lo = _dt.datetime.combine(target - _dt.timedelta(days=tol_days + 3), _dt.time.min)
    hi = _dt.datetime.combine(target + _dt.timedelta(days=tol_days + 3), _dt.time.max)
    rows = (await session.execute(_text("""
        SELECT timestamp, close FROM candles
        WHERE symbol = :s AND timeframe = '1d'
          AND to_char(timestamp,'HH24:MI') = ANY(:t)
          AND timestamp >= :lo AND timestamp <= :hi
        ORDER BY timestamp ASC
    """), {"s": symbol, "t": list(times), "lo": lo, "hi": hi})).all()

    best = _collapse_to_sessions(rows, symbol, holidays=holidays, extra_open=extra_open)
    if as_of is not None:
        # Never reach forward past the evaluation date. Without this the
        # nearest-session search can legitimately pick target+1 and hand a
        # point-in-time caller the next session's close.
        best = {d: v for d, v in best.items() if d <= as_of}
    if not best:
        return None
    nearest = min(best, key=lambda d: abs((d - target).days))
    if abs((nearest - target).days) > tol_days:
        return None                      # fail closed rather than reach further
    return float(best[nearest][1][0])


async def session_bars(
    symbol: str,
    start: _dt.date,
    end: _dt.date,
    session: AsyncSession,
    *,
    holidays: set[str] | None = None,
    extra_open: set[str] | None = None,
    basis: str | None = None,
    as_of: _dt.date | None = None,
    include_current: bool = False,
) -> list[tuple[_dt.date, float, float, float, float, float]]:
    """Bars whose SESSION falls in [start, end], oldest first.

    (session_date, open, high, low, close, volume). Bounded by session, not by calendar
    date — the distinction decides whether a hold-window MFE/MAE is honest. An
    18:30 row carrying the calendar date `end` belongs to the session AFTER
    `end`, so a plain `timestamp <= end` range let a high or low from a session
    beyond the exit set the excursion. One bar per session, one basis.
    """
    times = await _preferred_times(symbol, session, basis=basis)
    lo = _dt.datetime.combine(start - _dt.timedelta(days=4), _dt.time.min)
    hi = _dt.datetime.combine(end + _dt.timedelta(days=4), _dt.time.max)
    rows = (await session.execute(_text("""
        SELECT timestamp, open, high, low, close, volume FROM candles
        WHERE symbol = :s AND timeframe = '1d'
          AND to_char(timestamp,'HH24:MI') = ANY(:t)
          AND timestamp >= :lo AND timestamp <= :hi
        ORDER BY timestamp ASC
    """), {"s": symbol, "t": list(times), "lo": lo, "hi": hi})).all()

    best = _collapse_to_sessions(rows, symbol, holidays=holidays, extra_open=extra_open)
    ordered = _apply_as_of(sorted(best.items()), as_of=as_of,
                           include_current=include_current, holidays=holidays,
                           extra_open=extra_open, symbol=symbol)
    return [
        (d, float(v[0]), float(v[1]), float(v[2]), float(v[3]), float(v[4] or 0))
        for d, (_, v) in ordered
        if start <= d <= end
    ]


async def _bulk_resolved(
    symbols: list[str], session: AsyncSession, *, sessions: int,
    as_of: _dt.date | None, include_current: bool,
    holidays: set[str] | None, extra_open: set[str] | None,
) -> dict[str, list[tuple]]:
    """Shared engine for the bulk readers: one query, the SAME resolver.

    The screeners and the momentum filter scan hundreds of symbols per cycle; a
    per-symbol round trip would turn one query into hundreds. So this fetches
    once and then runs `_collapse_to_sessions` per symbol — not a second
    implementation of session resolution, and deliberately not a SQL-side
    `DISTINCT` on timestamp, which deduplicates ROWS rather than resolving
    SESSIONS and would still keep an 18:30 bar beside the canonical bar for the
    same session.

    Returns {symbol: [(session_date, open, high, low, close, volume, convention)]}
    oldest first.
    """
    if not symbols:
        return {}
    syms = list(symbols)

    rows = (await session.execute(_text("""
        SELECT symbol, timestamp, open, high, low, close, volume FROM candles
        WHERE symbol = ANY(:syms) AND timeframe = '1d'
          AND to_char(timestamp,'HH24:MI') = ANY(:t)
        ORDER BY symbol, timestamp DESC
    """), {"syms": syms, "t": list(_ALL_TIMES)})).all()

    cov: dict[str, dict[str, _dt.date]] = {}
    for sym, hm, mx in (await session.execute(_text("""
        SELECT symbol, to_char(timestamp,'HH24:MI') hm, max(timestamp)::date
        FROM candles WHERE symbol = ANY(:syms) AND timeframe = '1d'
          AND to_char(timestamp,'HH24:MI') = ANY(:t)
        GROUP BY 1, 2
    """), {"syms": syms, "t": list(_ALL_TIMES)})).all():
        cov.setdefault(sym, {})[hm] = mx

    per: dict[str, list] = {}
    for r in rows:
        per.setdefault(r[0], []).append(tuple(r[1:]))

    out: dict[str, list[tuple]] = {}
    for sym, raw in per.items():
        c = cov.get(sym, {})
        raw_max = max((v for k, v in c.items() if k in _RAW_TIMES), default=None)
        adj_max = c.get("00:00")
        if raw_max and adj_max:
            keep = _RAW_TIMES if raw_max >= adj_max else ("00:00",)
        elif adj_max:
            keep = ("00:00",)
        else:
            keep = _RAW_TIMES
        kept = [x for x in raw if x[0].strftime("%H:%M") in keep]

        best = _collapse_to_sessions(kept, sym, holidays=holidays, extra_open=extra_open)
        ordered = _apply_as_of(sorted(best.items()), as_of=as_of,
                               include_current=include_current, holidays=holidays,
                               extra_open=extra_open, symbol=sym)
        series = [(d, float(v[0]), float(v[1]), float(v[2]), float(v[3]),
                   float(v[4] or 0), v[5].value) for d, (_, v) in ordered]
        out[sym] = series[-sessions:] if sessions else series
    return out


async def session_closes_bulk(
    symbols: list[str],
    session: AsyncSession,
    *,
    sessions: int = 220,
    as_of: _dt.date | None = None,
    include_current: bool = False,
    holidays: set[str] | None = None,
    extra_open: set[str] | None = None,
) -> dict[str, list[tuple[_dt.date, float, str]]]:
    """session_closes() for many symbols in ONE query. (date, close, convention)."""
    full = await _bulk_resolved(symbols, session, sessions=sessions, as_of=as_of,
                                include_current=include_current, holidays=holidays,
                                extra_open=extra_open)
    return {k: [(d, c, conv) for d, _o, _h, _l, c, _v, conv in v] for k, v in full.items()}


async def session_bars_bulk(
    symbols: list[str],
    session: AsyncSession,
    *,
    sessions: int = 220,
    as_of: _dt.date | None = None,
    include_current: bool = False,
    holidays: set[str] | None = None,
    extra_open: set[str] | None = None,
) -> dict[str, list[tuple[_dt.date, float, float, float, float, float, str]]]:
    """Full OHLCV per session for many symbols in ONE query."""
    return await _bulk_resolved(symbols, session, sessions=sessions, as_of=as_of,
                                include_current=include_current, holidays=holidays,
                                extra_open=extra_open)
