"""Path F — data access for the Tactical pipeline.

Every read in this module is READ-ONLY against existing tables. Path F writes
to exactly one table, `tactical_signals`, and that write lives in
`tactical_executor.py`.

Why this module exists at all rather than calling `get_latest_candles` directly:

1. `crawler.price_feed.get_latest_candles` returns ORM rows **newest-first**.
   Every indicator in the codebase expects oldest-first. Getting that backwards
   silently inverts every trend calculation, so the reversal happens once, here.
2. The forming-bar problem (audit D5) is handled centrally. Callers ask for a
   DataFrame and get one whose last row may still be forming; they then pass
   `exclude_forming_bar=True` to `compute_indicators`. This module never
   pre-truncates, because doing it in both places would silently drop two bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.price_feed import get_latest_candles
from utils import config as settings_mod_pkg
from utils.config import settings as settings_mod
from db.models import HubUniverse
from utils.logger import logger

# ── Dedicated Path F log sink ────────────────────────────────────────────────
# Tactical output goes to its own file as well as the shared app log, so a
# shadow-mode evaluation can be read without grepping through the news engine's
# volume. Respects the pytest guard added in 9a17f76: under pytest the shared
# sink already redirects, and we must not create a second production file.
def _install_tactical_sink() -> None:
    import os
    import sys

    if os.getenv("PYTEST_VERSION") or os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return
    try:
        logger.add(
            "logs/tactical_pipeline.log",
            rotation="00:00",
            retention="14 days",
            level="INFO",
            filter=lambda rec: "tactical" in rec["name"],
        )
    except Exception as exc:  # pragma: no cover - sink setup must never break a scan
        logger.debug(f"[tactical] dedicated log sink not installed: {exc}")


_install_tactical_sink()

IST = ZoneInfo("Asia/Kolkata")

# Working index symbols, confirmed against crawler/zerodha_instruments.py.
NIFTY_SYMBOL = "^NSEI"
VIX_SYMBOL = "^INDIAVIX"

# NSE session, IST. The opening range is the first 15 minutes.
SESSION_OPEN = (9, 15)
ORB_END = (9, 30)
SESSION_CLOSE = (15, 30)
# Path F stops originating before the close so it never proposes an entry that
# could not be managed; mirrors the 15:20 cutoff the India loop uses.
ENTRY_CUTOFF = (15, 20)


@dataclass(frozen=True)
class MarketContext:
    """Cross-cutting state fetched once per cycle, not per symbol."""

    vix: float | None = None
    nifty_price: float | None = None
    now_ist: datetime | None = None

    @property
    def vix_elevated(self) -> bool:
        from utils.config import settings

        if self.vix is None:
            return False
        return self.vix > float(getattr(settings, "TACTICAL_VIX_THRESHOLD", 25.0))


def now_ist() -> datetime:
    return datetime.now(IST)


def _at(d: datetime, hm: tuple[int, int]) -> datetime:
    return d.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)


def in_entry_window(when: datetime | None = None) -> bool:
    """True when a NEW tactical signal may be generated.

    Deliberately stricter than the exit window: origination stops at 15:20 IST
    even though the session runs to 15:30.
    """
    t = when or now_ist()
    if t.weekday() >= 5:
        return False
    return _at(t, SESSION_OPEN) <= t <= _at(t, ENTRY_CUTOFF)


def orb_window(when: datetime | None = None) -> tuple[datetime, datetime]:
    """(start, end) of today's opening range, in IST."""
    t = when or now_ist()
    return _at(t, SESSION_OPEN), _at(t, ORB_END)


# How stale the newest bar may be before we refuse the frame entirely, per
# timeframe (minutes).
#
# Calibrated against measured behaviour, not the bar size: `kite_live_candles`
# runs every 3 minutes but fetches thousands of symbols per run (soft limit
# 1200s), so the newest 1m bar normally trails 15-20 minutes. A threshold at the
# bar size would refuse a perfectly healthy feed.
#
# These are wide enough for that batch lag and still catch a dead feed by orders
# of magnitude — the 5m outage found on 2026-08-20 was 1,277 minutes stale.
#
# CAVEAT worth remembering when reading shadow results: an F1 signal's
# indicators can therefore be up to ~20 minutes old even when the feed is
# "healthy". Entry price is live (market_snapshot), but RSI/VWAP/pivots are not.
# That is a data-infrastructure limit, not something the rules can compensate
# for, and it is a genuine weakness of intraday signals on this feed.
MAX_BAR_AGE_MIN = {"1m": 30, "5m": 45, "15m": 90, "1h": 240, "1d": 5760}


async def get_candles_df(
    symbol: str,
    timeframe: str,
    count: int,
    session: AsyncSession,
    *,
    before: datetime | None = None,
    allow_stale: bool = False,
) -> pd.DataFrame | None:
    """Oldest-first OHLCV DataFrame, or None when there is not enough data.

    The returned frame's LAST row may be a still-forming bar — that is
    deliberate. Pass `exclude_forming_bar=True` to `compute_indicators` rather
    than truncating here (audit D5); truncating in both places drops two bars.

    `before` threads through to `get_latest_candles` for point-in-time replay.
    """
    try:
        rows = await get_latest_candles(symbol, timeframe, count, session, before=before)
    except Exception as exc:
        logger.debug(f"[tactical] candles {symbol}/{timeframe} failed: {exc}")
        return None

    if not rows or len(rows) < 5:
        rows = []

    # ── Fast lane: splice in tick-built bars before judging freshness ────────
    # Order matters. The DB's newest 1m bar can be 20-40 minutes old, so if the
    # staleness check ran first it would reject the frame and the fast bars
    # would never get a chance to rescue it — which is exactly the state the
    # audit found F1 in. Merge first, then judge the merged frame.
    fast_used = False
    if (
        timeframe == "1m"
        and before is None
        and bool(getattr(settings_mod, "TACTICAL_FAST_CANDLE_ENABLED", True))
    ):
        db_df = _rows_to_df(rows) if rows else None
        merged = await _merge_fast_candles(symbol, db_df)
        if merged is not None and len(merged) >= 5:
            newest = merged["timestamp"].max()
            age_min = (datetime.utcnow() - pd.Timestamp(newest).to_pydatetime()).total_seconds() / 60.0
            fast_limit = float(getattr(settings_mod, "TACTICAL_FAST_CANDLE_MAX_AGE_MIN", 2))
            if age_min <= max(fast_limit, MAX_BAR_AGE_MIN.get("1m", 30)):
                return merged
            fast_used = True

    if not rows:
        return None

    # Fail CLOSED on a stale feed (same posture as the D3 price fix).
    #
    # Found live 2026-08-20: the 5m feed had been dead for ~21 hours while the
    # 1m feed was healthy, and F4 happily produced 14 "oversold rebound" signals
    # an hour by computing RSI and Bollinger bands on yesterday's bars and
    # comparing them to today's live price. Those signals looked entirely
    # confident and were meaningless. A pipeline that cannot tell stale data
    # from fresh will always eventually trade on the stale kind.
    #
    # `before` is point-in-time replay, where old bars are the whole point, so
    # the check is skipped there.
    if before is None and not allow_stale:
        newest = rows[0].timestamp          # newest-first
        max_age = MAX_BAR_AGE_MIN.get(timeframe)
        if newest is not None and max_age is not None:
            age_min = (datetime.utcnow() - newest).total_seconds() / 60.0
            if age_min > max_age:
                # DEBUG, not WARNING: a dead feed means EVERY symbol trips
                # this, and warning per symbol produced 364 lines in 15 minutes.
                # The executor emits one aggregate warning per cycle instead.
                logger.debug(
                    f"[tactical] {symbol}/{timeframe}: newest bar is "
                    f"{age_min:.0f} min old (max {max_age}) — refusing the frame"
                )
                return None

    return _rows_to_df(rows)


def _rows_to_df(rows) -> pd.DataFrame:
    """ORM rows (NEWEST-first, as get_latest_candles returns them) -> oldest-first frame.

    The reversal happens here and nowhere else; every indicator in the codebase
    expects oldest-first, and getting it backwards silently inverts every trend.
    """
    rows = list(reversed(rows))
    return pd.DataFrame(
        {
            "open": [r.open for r in rows],
            "high": [r.high for r in rows],
            "low": [r.low for r in rows],
            "close": [r.close for r in rows],
            "volume": [r.volume for r in rows],
            "timestamp": [r.timestamp for r in rows],
        }
    )


async def _merge_fast_candles(symbol: str, df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Append tick-built 1m bars that the DB has not caught up to yet.

    The DB path lags 15-40 minutes, so the recent bars are MISSING, not merely
    stale — replacing only the last row would not close that. This concatenates
    every fast bar newer than the DB's newest, deduplicated on timestamp with
    the DB winning (it is the authoritative aggregate; the fast bar is a
    5-second-sampled approximation, see crawler/live_candle_builder.py).
    """
    from crawler.live_candle_builder import read_fast_candles

    fast = await read_fast_candles(symbol)
    if not fast:
        return df

    fdf = pd.DataFrame(fast)
    if fdf.empty or "timestamp" not in fdf:
        return df
    fdf["timestamp"] = pd.to_datetime(fdf["timestamp"])
    fdf = fdf[["open", "high", "low", "close", "volume", "timestamp"]]

    if df is None or df.empty:
        return fdf.sort_values("timestamp").reset_index(drop=True)

    newest_db = pd.to_datetime(df["timestamp"]).max()
    fresh = fdf[fdf["timestamp"] > newest_db]
    if fresh.empty:
        return df

    merged = pd.concat([df, fresh], ignore_index=True)
    merged["timestamp"] = pd.to_datetime(merged["timestamp"])
    merged = (
        merged.drop_duplicates(subset="timestamp", keep="first")   # DB wins
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    logger.debug(
        f"[tactical] {symbol}: merged {len(fresh)} fast bar(s) onto {len(df)} DB bars"
    )
    return merged


async def get_live_price(symbol: str) -> float | None:
    """Live LTP via the shared MarketSnapshot service.

    Uses `get_market_snapshot` rather than `live_prices.get_price` because the
    snapshot is the same tick the rest of the system prices against, and since
    audit D3 it honours tick age instead of silently returning a stale tick.
    Returns None rather than a stale or zero price — callers must treat None as
    "no signal", never as 0.
    """
    try:
        from crawler.market_snapshot import get_market_snapshot

        snap = await get_market_snapshot(symbol)
        if snap and snap.ltp and snap.ltp > 0:
            return float(snap.ltp)
    except Exception as exc:
        logger.debug(f"[tactical] live price {symbol} failed: {exc}")
    return None


async def get_prices_batch(symbols: list[str]) -> dict[str, float]:
    """One batched LTP call for the whole universe.

    Measured on 2026-08-20: per-symbol `get_market_snapshot` cost ~3.4s/symbol
    (34.5s for 10), because each miss walks WS tick -> Kite REST -> yfinance.
    At that rate F1's 50-symbol universe would take ~3 minutes against a
    1-minute cadence — the pipeline could never complete a cycle.

    `zerodha_market.get_live_prices` chunks at 200 instruments per request, so
    the whole universe costs one or two calls, and it already draws on the
    shared Kite rate limiter added in audit D6.

    Returns {symbol: ltp}; symbols with no price are simply absent.
    """
    if not symbols:
        return {}
    try:
        from crawler.zerodha_market import get_live_prices

        quotes = await get_live_prices(symbols)
    except Exception as exc:
        logger.warning(f"[tactical] batch price fetch failed: {exc}")
        return {}

    out: dict[str, float] = {}
    for sym, data in (quotes or {}).items():
        try:
            px = float(data.get("price") or data.get("last_price") or 0)
            if px > 0:
                out[sym] = px
        except Exception:
            continue
    return out


async def get_market_context() -> MarketContext:
    """VIX + NIFTY, fetched once per cycle."""
    vix = await get_live_price(VIX_SYMBOL)
    nifty = await get_live_price(NIFTY_SYMBOL)
    return MarketContext(vix=vix, nifty_price=nifty, now_ist=now_ist())


async def get_universe(session: AsyncSession, limit: int) -> list[str]:
    """Top-N symbols from `hub_universe` by turnover rank. READ-ONLY.

    `hub_universe` is rebuilt daily as the top-N NSE equities by average daily
    turnover, so `rank` is already a liquidity ordering — which is what F1 needs
    and what the brief approximates as "Nifty 50". There is no Nifty-50
    constituent list in this repo (`_NIFTY50` in engine/india_specific.py is the
    index symbol "^NSEI", not a membership list), so turnover rank is the
    defensible substitute.
    """
    try:
        rows = (
            await session.execute(
                select(HubUniverse.symbol)
                .where(HubUniverse.rank > 0)
                .order_by(HubUniverse.rank.asc())
                .limit(limit)
            )
        ).scalars().all()
        return [s for s in rows if s]
    except Exception as exc:
        logger.warning(f"[tactical] universe fetch failed: {exc}")
        return []


async def get_symbols_with_timeframe(
    session: AsyncSession, timeframe: str, limit: int
) -> list[str]:
    """Universe intersected with symbols that actually have data for `timeframe`.

    F4 runs on 5m candles, which cover ~1,250 symbols versus 1m's ~4,300.
    Scanning a symbol with no 5m history just burns the cycle, so intersect
    first rather than discovering it per-symbol.
    """
    from sqlalchemy import text

    try:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT c.symbol
                      FROM hub_universe h
                      JOIN (SELECT DISTINCT symbol FROM candles
                             WHERE timeframe = :tf
                               AND timestamp > now() - interval '3 days') c
                        ON c.symbol = h.symbol
                     WHERE h.rank > 0
                     ORDER BY h.rank ASC
                     LIMIT :lim
                    """
                ),
                {"tf": timeframe, "lim": limit},
            )
        ).all()
        return [r[0] for r in rows]
    except Exception as exc:
        logger.warning(f"[tactical] {timeframe} universe fetch failed: {exc}")
        return []
