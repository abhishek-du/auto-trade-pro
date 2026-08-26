"""Historical candle fetcher backed by Kite Connect.

Uses the official kiteconnect library to pull OHLCV candles for an
instrument token, normalises to the project's `Candle` schema, and
persists via the existing `save_candles_to_db` helper.

Supported timeframes are mapped to Kite's interval strings via INTERVAL_MAP.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from crawler.price_feed import save_candles_to_db
from crawler.zerodha_instruments import get_token
from utils.config import settings
from utils.logger import logger

# ── Interval map ─────────────────────────────────────────────────────────────

INTERVAL_MAP: dict[str, str] = {
    "1m":  "minute",
    "3m":  "3minute",
    "5m":  "5minute",
    "10m": "10minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h":  "60minute",
    "1d":  "day",
}


def _to_kite_interval(tf: str) -> str:
    return INTERVAL_MAP.get(tf, tf)


# ── Raw fetch ────────────────────────────────────────────────────────────────

async def get_kite_candles_for_range(
    symbol: str,
    from_date: _dt.date | _dt.datetime | str,
    to_date: _dt.date | _dt.datetime | str,
    interval: str = "1d",
    oi: bool = False,
) -> list[dict]:
    """Fetch raw candles for a symbol over [from_date, to_date].

    Returns a list of dicts in save_candles_to_db format.
    """
    token = get_token(symbol)
    if token is None:
        logger.debug(f"[zerodha_historical] No instrument token for {symbol}")
        return []

    from crawler.zerodha_kite_lib import get_historical_data

    kite_interval = _to_kite_interval(interval)
    raw = []
    for attempt in range(4):
        try:
            raw = await asyncio.to_thread(
                get_historical_data,
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval=kite_interval,
                oi=oi,
            )
            break
        except Exception as exc:
            if "Too many requests" in str(exc) or "429" in str(exc):
                if attempt < 3:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
            logger.warning(f"[zerodha_historical] Fetch failed for {symbol}: {exc}")
            return []

    # Normalise into Candle DB row format
    tf_reverse = {v: k for k, v in INTERVAL_MAP.items()}
    tf = tf_reverse.get(kite_interval, interval)
    # Preserve the caller's exchange suffix (.BO must NOT become .NS -- was
    # silently corrupting every BSE candle save into a wrong ".BO.NS" symbol
    # before this fix, 2026-07-28). Only a genuinely bare/unsuffixed symbol
    # defaults to .NS, matching this function's pre-existing NSE-default
    # behaviour for callers that never pass a suffix at all.
    if symbol.endswith(".NS") or symbol.endswith(".BO") or symbol.startswith("^"):
        sym_save = symbol
    else:
        sym_save = f"{symbol}.NS"

    candles: list[dict] = []
    for c in raw:
        ts = c.get("date") or c.get("timestamp")
        if ts is None:
            continue
        if isinstance(ts, str):
            try:
                ts = _dt.datetime.fromisoformat(ts)
            except ValueError:
                continue
        if isinstance(ts, _dt.datetime) and ts.tzinfo is not None:
            ts = ts.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        candles.append({
            "symbol":    sym_save,
            "timeframe": tf,
            "open":      float(c.get("open", 0.0)),
            "high":      float(c.get("high", 0.0)),
            "low":       float(c.get("low", 0.0)),
            "close":     float(c.get("close", 0.0)),
            "volume":    float(c.get("volume", 0)),
            "timestamp": ts,
        })
    return candles


# ── DB sync — single symbol ──────────────────────────────────────────────────

async def sync_kite_candles(
    symbol: str,
    timeframe: str,
    days_back: int,
    session: AsyncSession,
) -> dict:
    """Fetch [days_back] days of candles for symbol/timeframe and save to DB."""
    to_date = _dt.date.today()
    from_date = to_date - _dt.timedelta(days=days_back)
    candles = await get_kite_candles_for_range(symbol, from_date, to_date, interval=timeframe)
    if not candles:
        return {"symbol": symbol, "timeframe": timeframe, "saved": 0, "fetched": 0}
    saved = await save_candles_to_db(candles, session)
    return {"symbol": symbol, "timeframe": timeframe, "saved": saved, "fetched": len(candles)}


# ── DB sync — all NSE symbols ────────────────────────────────────────────────

async def sync_all_nse_candles(
    session: AsyncSession,
    *,
    timeframe: str = "1d",
    days_back: int = 120,
    delay_sec: float = 0.5,
) -> dict:
    """Iterate settings.nse_symbols (+ mid caps) and persist daily candles.

    ``delay_sec`` defaults to 0.5 to stay under Kite's 3 req/sec historical
    rate limit with headroom — 0.35 (≈2.85 req/sec) hit 429s in practice.
    """
    symbols: Iterable[str] = settings.nse_symbols + settings.nse_mid_symbols
    total_saved = 0
    total_fetched = 0
    errors: list[str] = []

    for sym in symbols:
        try:
            result = await sync_kite_candles(sym, timeframe, days_back, session)
            total_saved += result.get("saved", 0)
            total_fetched += result.get("fetched", 0)
        except Exception as exc:
            errors.append(f"{sym}: {exc}")
            logger.warning(f"[zerodha_historical] {sym} sync error: {exc}")
        await asyncio.sleep(delay_sec)

    try:
        await session.commit()
    except Exception:
        await session.rollback()

    summary = {
        "symbols": len(list(symbols)),
        "fetched": total_fetched,
        "saved":   total_saved,
        "errors":  errors,
    }
    logger.info(f"[zerodha_historical] sync_all_nse_candles → {summary}")
    return summary


# ── DB sync — FULL NSE universe from kite_instruments ────────────────────────

async def sync_full_nse_universe(
    session: AsyncSession,
    *,
    days_back: int = 7,
    delay_sec: float = 0.5,
) -> dict:
    """Incrementally refresh daily candles for EVERY NSE EQ instrument.

    Reads instrument tokens straight from `kite_instruments` (not the curated
    watchlist) so the agent's full-market universe stays current. Designed for
    the weekly beat task: ``days_back=7`` keeps the latest week of bars fresh.

    Idempotent (ON CONFLICT DO NOTHING). Skips symbols whose token is missing.
    """
    import datetime as _d
    from sqlalchemy import text as _text

    # Excludes GOI/SDL government bonds & T-bills — Zerodha tags them
    # instrument_type='EQ' same as real equities with no other distinguishing
    # field, they're ~55% of this query's rows, and their numeric-coded
    # tradingsymbols (e.g. "182D100926-TB") sort alphabetically ahead of nearly
    # every real ticker — wasting the bulk of this rate-limited Kite historical
    # API budget on non-equity instruments. See _backfill_hub_1d_candles for
    # the full writeup (same bug, found in production 2026-07-06).
    rows = (await session.execute(_text("""
        SELECT tradingsymbol, instrument_token
        FROM kite_instruments
        WHERE segment='NSE' AND instrument_type='EQ'
          AND name != '' AND instrument_token > 0
          AND name NOT ILIKE 'GOI %' AND name NOT ILIKE 'SDL %'
        ORDER BY tradingsymbol
    """))).all()

    kite = None
    try:
        from crawler.zerodha_kite_lib import get_kite
        kite = get_kite()
        kite.profile()  # verify token before the long loop
    except Exception as exc:
        logger.warning(f"[sync_full_nse_universe] Zerodha not authenticated: {exc}")
        return {"symbols": 0, "saved": 0, "error": "not_authenticated"}

    to_date   = _d.date.today()
    from_date = to_date - _d.timedelta(days=int(days_back * 1.6) + 3)

    total_saved = ok = empty = 0
    pending: list[dict] = []

    for sym, token in rows:
        try:
            raw = await asyncio.to_thread(
                kite.historical_data,
                instrument_token=token, from_date=from_date,
                to_date=to_date, interval="day",
            )
        except Exception:
            raw = []
        if raw:
            ok += 1
            for c in raw:
                ts = c.get("date")
                if ts is None:
                    continue
                if isinstance(ts, _d.datetime) and ts.tzinfo is not None:
                    ts = ts.astimezone(_d.timezone.utc).replace(tzinfo=None)
                pending.append({
                    "symbol": f"{sym}.NS", "timeframe": "1d",
                    "open": float(c.get("open", 0.0)), "high": float(c.get("high", 0.0)),
                    "low": float(c.get("low", 0.0)), "close": float(c.get("close", 0.0)),
                    "volume": float(c.get("volume", 0) or 0), "timestamp": ts,
                })
        else:
            empty += 1

        if len(pending) >= 5000:
            try:
                total_saved += await save_candles_to_db(pending, session)
                await session.commit()
            except Exception:
                await session.rollback()
            pending = []
        await asyncio.sleep(delay_sec)

    if pending:
        try:
            total_saved += await save_candles_to_db(pending, session)
            await session.commit()
        except Exception:
            await session.rollback()

    summary = {"symbols": len(rows), "fetched_ok": ok, "empty": empty, "saved": total_saved}
    logger.info(f"[sync_full_nse_universe] → {summary}")
    return summary


# ── DB sync — FULL BSE universe from kite_instruments (2026-07-31) ───────────

async def sync_full_bse_universe(
    session: AsyncSession,
    *,
    days_back: int = 7,
    delay_sec: float = 0.5,
) -> dict:
    """Incrementally refresh daily candles for EVERY BSE EQ instrument.

    Direct mirror of sync_full_nse_universe() above -- same GOI/SDL-bond
    exclusion (BSE's instrument master has the same miscategorisation issue),
    same 0.5s/symbol Kite rate-limit headroom, same batched-insert pattern.
    Written as this codebase's NSE/BSE convention: separate symbol spaces
    (.NS vs .BO suffix), so a twin function rather than an exchange parameter
    on the existing one. Added after JUMBO.BO (a real, tradeable BSE
    micro-cap DIRECT_NEWS fired on) turned out to have zero candle history --
    BSE had no full-universe sync of any kind before this.
    """
    import datetime as _d
    from sqlalchemy import text as _text

    rows = (await session.execute(_text("""
        SELECT tradingsymbol, instrument_token
        FROM kite_instruments
        WHERE segment='BSE' AND instrument_type='EQ'
          AND name != '' AND instrument_token > 0
          AND name NOT ILIKE 'GOI %' AND name NOT ILIKE 'SDL %'
        ORDER BY tradingsymbol
    """))).all()

    kite = None
    try:
        from crawler.zerodha_kite_lib import get_kite
        kite = get_kite()
        kite.profile()  # verify token before the long loop
    except Exception as exc:
        logger.warning(f"[sync_full_bse_universe] Zerodha not authenticated: {exc}")
        return {"symbols": 0, "saved": 0, "error": "not_authenticated"}

    to_date   = _d.date.today()
    from_date = to_date - _d.timedelta(days=int(days_back * 1.6) + 3)

    total_saved = ok = empty = 0
    pending: list[dict] = []

    for sym, token in rows:
        try:
            raw = await asyncio.to_thread(
                kite.historical_data,
                instrument_token=token, from_date=from_date,
                to_date=to_date, interval="day",
            )
        except Exception:
            raw = []
        if raw:
            ok += 1
            for c in raw:
                ts = c.get("date")
                if ts is None:
                    continue
                if isinstance(ts, _d.datetime) and ts.tzinfo is not None:
                    ts = ts.astimezone(_d.timezone.utc).replace(tzinfo=None)
                pending.append({
                    "symbol": f"{sym}.BO", "timeframe": "1d",
                    "open": float(c.get("open", 0.0)), "high": float(c.get("high", 0.0)),
                    "low": float(c.get("low", 0.0)), "close": float(c.get("close", 0.0)),
                    "volume": float(c.get("volume", 0) or 0), "timestamp": ts,
                })
        else:
            empty += 1

        if len(pending) >= 5000:
            try:
                total_saved += await save_candles_to_db(pending, session)
                await session.commit()
            except Exception:
                await session.rollback()
            pending = []
        await asyncio.sleep(delay_sec)

    if pending:
        try:
            total_saved += await save_candles_to_db(pending, session)
            await session.commit()
        except Exception:
            await session.rollback()

    summary = {"symbols": len(rows), "fetched_ok": ok, "empty": empty, "saved": total_saved}
    logger.info(f"[sync_full_bse_universe] → {summary}")
    return summary


# ── Live 1-minute candle sync (runs every 60 s during market hours) ───────────

async def sync_live_1m_candles(
    session: AsyncSession,
    symbols: list[str] | None = None,
    *,
    concurrency: int = 3,
    delay_sec: float = 0.1,
) -> dict:
    """Fetch today's 1-minute candles from Kite for every watched symbol.

    Designed to be called every 3 min while NSE is open. Uses upsert so
    repeated runs for the same bar are idempotent. Fetches concurrently
    (semaphore=3); hub_universe has grown well past the original "500+
    symbols in ~90s" estimate (2,569 as of 2026-08-04), so a run logging
    past _SLOW_RUN_WARN_SEC below is a signal this task's Celery
    soft_time_limit/time_limit (tasks/india_tasks.py::kite_live_candles_task)
    needs re-tuning again, not something to silently absorb.
    Symbols default to the hub universe from DB; falls back to nse_symbols.
    """
    import time as _time
    from zoneinfo import ZoneInfo

    _SLOW_RUN_WARN_SEC = 900
    _run_start = _time.monotonic()

    _IST = ZoneInfo("Asia/Kolkata")
    now_ist = _dt.datetime.now(_IST).replace(tzinfo=None)
    day_open_ist = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    to_dt   = now_ist

    if symbols is None:
        symbols = list(settings.nse_symbols) + list(getattr(settings, "nse_mid_symbols", []))

    # ── Per-symbol delta window (2026-08-26, phase 18) ───────────────────────
    #
    # This used to fetch [09:15 .. now] for EVERY symbol on EVERY run. Measured
    # production consequence on 2026-08-26:
    #
    #   14:51:20  {'symbols': 2560, 'candles': 738902, 'saved': 65495}  1107s
    #   15:13:24  {'symbols': 2560, 'candles': 807064, 'saved': 16679}
    #   15:39:35  {'symbols': 2560, 'candles': 863744, 'saved': 20491}
    #
    # ~800,000 candles fetched to persist ~20,000 — a 97.5% waste ratio that
    # grows all day. A run took 10-18 minutes against a 3-minute beat schedule,
    # and the Redis NX lock in kite_live_candles_task silently dropped every
    # dispatch that arrived meanwhile (~125 dispatched, 37 completed), so the
    # EFFECTIVE refresh cadence became the run duration. Measured live lag was
    # p50 16 min across 1,743 symbols, 60% of them over 10 minutes stale.
    #
    # Measured cost of the two window shapes on the live Kite API (10 symbols):
    #   full day 09:15-15:30 : 0.170s mean, 365 bars/symbol
    #   5-minute delta       : 0.044s mean, 1.5 bars/symbol
    #
    # Asking only for what is missing is therefore ~3.9x cheaper per request and
    # ~75x cheaper in rows written. Nothing else changes: same symbols, same
    # concurrency, same delay, same beat schedule, same Redis lock, same
    # on_conflict_do_nothing upsert.
    #
    # FAIL-SAFE: if the lookup raises for any reason, _from_by_symbol stays
    # empty and every symbol falls back to day_open_ist — i.e. exactly the old
    # behaviour. A symbol with no bar today is not in the map and also falls
    # back, so its first fetch of the day is still the full window.
    def _stored_symbol(s: str) -> str:
        # Must mirror get_kite_candles_for_range's sym_save rule above, or the
        # lookup silently misses and every symbol degrades to the full window.
        if s.endswith(".NS") or s.endswith(".BO") or s.startswith("^"):
            return s
        return f"{s}.NS"

    _from_by_symbol: dict[str, _dt.datetime] = {}
    _t_lookup = _time.monotonic()
    try:
        from sqlalchemy import text as _text
        _day_start_utc = (
            day_open_ist.replace(tzinfo=_IST)
            .astimezone(_dt.timezone.utc)
            .replace(tzinfo=None)
        )
        _stored_to_input = {_stored_symbol(s): s for s in symbols}
        _rows = (await session.execute(
            _text(
                "SELECT symbol, MAX(timestamp) AS mx FROM candles "
                "WHERE timeframe = '1m' AND timestamp >= :day_start "
                "AND symbol = ANY(:syms) GROUP BY symbol"
            ),
            {"day_start": _day_start_utc, "syms": list(_stored_to_input.keys())},
        )).all()
        for _r in _rows:
            _orig = _stored_to_input.get(_r.symbol)
            if _orig is None or _r.mx is None:
                continue
            # candles.timestamp is UTC-naive (see get_kite_candles_for_range,
            # which converts Kite's tz-aware IST to UTC before storing). Kite
            # expects exchange-local naive datetimes, so convert back.
            _from_by_symbol[_orig] = (
                _r.mx.replace(tzinfo=_dt.timezone.utc)
                .astimezone(_IST)
                .replace(tzinfo=None)
            )
    except Exception as exc:
        _from_by_symbol = {}
        logger.warning(
            f"[live_1m] delta-window lookup failed, falling back to full-day "
            f"window for all symbols: {type(exc).__name__}"
        )
    _lookup_ms = int((_time.monotonic() - _t_lookup) * 1000)

    sem = asyncio.Semaphore(concurrency)
    errors: list[str] = []

    async def _fetch(sym: str) -> list[dict]:
        async with sem:
            result = await get_kite_candles_for_range(
                sym, _from_by_symbol.get(sym, day_open_ist), to_dt, interval="1m"
            )
            await asyncio.sleep(delay_sec)
            return result or []

    _t_fetch = _time.monotonic()
    results = await asyncio.gather(*[_fetch(s) for s in symbols], return_exceptions=True)
    _fetch_ms = int((_time.monotonic() - _t_fetch) * 1000)

    _t_transform = _time.monotonic()
    all_candles: list[dict] = []
    completed = 0
    for sym, res in zip(symbols, results):
        if isinstance(res, Exception):
            errors.append(f"{sym}:{res}")
        else:
            completed += 1
            all_candles.extend(res)
    _transform_ms = int((_time.monotonic() - _t_transform) * 1000)

    saved = 0
    _t_db = _time.monotonic()
    if all_candles:
        saved = await save_candles_to_db(all_candles, session)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
    _db_ms = int((_time.monotonic() - _t_db) * 1000)

    elapsed = _time.monotonic() - _run_start
    summary = {
        "symbols": len(symbols),
        "candles": len(all_candles),
        "saved":   saved,
        "errors":  len(errors),
        # Observability (2026-08-26, phase 18). Phase 17 could only derive the
        # transform+DB share as an arithmetic residual of the total; these
        # split it for real. Monotonic throughout. No symbol payloads, no
        # credentials, no request bodies are logged.
        "completed":   completed,
        "delta_syms":  len(_from_by_symbol),
        "lookup_ms":   _lookup_ms,
        "fetch_ms":    _fetch_ms,
        "transform_ms": _transform_ms,
        "db_ms":       _db_ms,
    }
    if elapsed >= _SLOW_RUN_WARN_SEC:
        logger.warning(f"[live_1m] slow run ({elapsed:.0f}s) → {summary}")
    else:
        logger.info(f"[live_1m] → {summary}")
    return summary
