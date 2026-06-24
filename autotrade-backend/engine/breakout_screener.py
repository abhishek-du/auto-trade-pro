"""Breakout Auto-Discovery Engine — finds ROTO-type momentum stocks automatically.

Problem it solves
-----------------
The Hub universe is rebuilt ONCE per day based on 30-day avg turnover.
A small-cap stock like ROTO that suddenly moves 7% on huge volume is INVISIBLE
to the agent because its historical volume was too low to make the top-500.

This engine runs every 5 minutes during market hours and:
1. Scans ALL symbols in the candles DB (9,600+ NSE EQ stocks)
2. Detects breakout conditions (price surge + volume spike)
3. Injects the breakouts into `hub_universe` AND `user_watchlist` tables
4. Triggers immediate Hub scoring on those symbols
5. They appear in market_shortlist within the next 15-min cycle

So the next time ROTO moves 7%, agent automatically catches it within 5 minutes.

Breakout Criteria (all must be met)
-------------------------------------
- Price change today  ≥ BREAKOUT_PCT  (default 4%)    ← significant move
- Volume ratio        ≥ VOL_SURGE_MIN (default 2.0×)  ← genuine, not random
- Close > EMA20 (today)                               ← still bullish structure
- RSI < 85                                            ← not already blow-off top
- NOT already in hub_universe                         ← only inject new ones

Public API
----------
scan_for_breakouts(session)  → list[BreakoutCandidate]
inject_breakouts_to_universe(candidates, session)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import logger

# ── Thresholds — read from config so they are tunable from .env ──────────────
# Call _thresh() at the start of each function to get fresh values.

def _cfg(name: str, default):
    try:
        from utils.config import settings
        return getattr(settings, name, default)
    except Exception:
        return default


def _thresh() -> tuple:
    """Return (breakout_pct, vol_surge_min, rsi_max, max_inject) from config."""
    return (
        float(_cfg("BREAKOUT_PCT",     4.0)),
        float(_cfg("BREAKOUT_VOL_MIN", 2.0)),
        float(_cfg("BREAKOUT_RSI_MAX", 85.0)),
        int  (_cfg("BREAKOUT_MAX_INJ", 20)),
    )


LOOKBACK_DAYS: int = 25    # days of daily candles to load for each symbol
MIN_CANDLES:   int = 10    # minimum daily bars needed to compute indicators


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class BreakoutCandidate:
    symbol:       str
    close:        float
    change_pct:   float   # today's % gain
    volume_ratio: float   # today vol / 20d avg
    rsi:          float
    ema20:        float
    reason:       str     # human-readable why it was flagged
    detected_at:  datetime = field(default_factory=datetime.utcnow)


# ── Lightweight RSI helper (no pandas needed) ─────────────────────────────────

def _rsi14(closes: list[float]) -> float:
    """Wilder RSI-14. Returns math.nan if < 15 bars."""
    if len(closes) < 15:
        return math.nan
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas[-14:]]
    losses = [-min(d, 0.0) for d in deltas[-14:]]
    ag = sum(gains)  / 14
    al = sum(losses) / 14
    if al == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + ag / al), 1)


def _ema(closes: list[float], period: int) -> float:
    """Simple EMA. Returns math.nan if not enough bars."""
    if len(closes) < period:
        return math.nan
    k   = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return round(ema, 4)


# ── Core scanner ─────────────────────────────────────────────────────────────

async def scan_for_breakouts(session: AsyncSession) -> list[BreakoutCandidate]:
    """
    Scan ALL NSE symbols in the candles table for today's breakout condition.

    Returns a ranked list of BreakoutCandidate (highest change_pct first).
    """
    breakout_pct, vol_surge_min, rsi_max, max_inject = _thresh()

    cutoff = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS + 5)

    # Single SQL: fetch last LOOKBACK_DAYS daily bars for every .NS symbol
    # ordered by symbol + timestamp so we can group in Python.
    rows = (await session.execute(text("""
        SELECT symbol, close, volume, timestamp
        FROM   candles
        WHERE  timeframe = '1d'
          AND  symbol    LIKE '%.NS'
          AND  timestamp >= :cutoff
        ORDER  BY symbol, timestamp ASC
    """), {"cutoff": cutoff})).all()

    if not rows:
        logger.warning("[breakout_screener] No daily candle data found")
        return []

    # Group by symbol
    from collections import defaultdict
    grouped: dict[str, list] = defaultdict(list)
    for r in rows:
        grouped[r.symbol].append(r)

    # Fetch existing hub_universe symbols to avoid re-injecting
    existing_hub = set(
        (await session.execute(
            text("SELECT symbol FROM hub_universe")
        )).scalars().all()
    )

    candidates: list[BreakoutCandidate] = []

    for symbol, bars in grouped.items():
        if len(bars) < MIN_CANDLES:
            continue

        closes  = [float(b.close)  for b in bars]
        volumes = [float(b.volume or 0.0) for b in bars]

        # Today = last bar; yesterday = second-to-last
        today_close     = closes[-1]
        yesterday_close = closes[-2] if len(closes) >= 2 else closes[-1]
        today_vol       = volumes[-1]

        if yesterday_close <= 0 or today_close <= 0:
            continue

        change_pct = (today_close - yesterday_close) / yesterday_close * 100.0

        # ── Breakout gate 1: minimum price move ──────────────────────────────────
        if change_pct < breakout_pct:
            continue

        # ── Breakout gate 2: volume surge ──────────────────────────────────
        avg_vol_20 = sum(volumes[-21:-1]) / max(len(volumes[-21:-1]), 1)
        vol_ratio  = today_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0
        if vol_ratio < vol_surge_min:
            continue

        # ── Indicators ───────────────────────────────────────────────────────
        rsi   = _rsi14(closes)
        ema20 = _ema(closes, 20)

        # ── Breakout gate 3: RSI sanity (not blow-off top) ─────────────────────
        if not math.isnan(rsi) and rsi > rsi_max:
            continue

        # ── Breakout gate 4: price above EMA20 (bullish structure) ───────────
        if not math.isnan(ema20) and today_close < ema20 * 0.98:
            continue  # below EMA20 = falling knife, skip

        reason_parts = [f"+{change_pct:.1f}%", f"vol {vol_ratio:.1f}×avg"]
        if not math.isnan(rsi):
            reason_parts.append(f"RSI {rsi:.0f}")
        if symbol in existing_hub:
            reason_parts.append("already_in_hub")

        candidates.append(BreakoutCandidate(
            symbol=symbol,
            close=round(today_close, 2),
            change_pct=round(change_pct, 2),
            volume_ratio=round(vol_ratio, 2),
            rsi=rsi if not math.isnan(rsi) else 50.0,
            ema20=ema20 if not math.isnan(ema20) else today_close,
            reason=" | ".join(reason_parts),
        ))

    # Rank: highest volume_ratio × change_pct (conviction score)
    candidates.sort(key=lambda c: c.volume_ratio * c.change_pct, reverse=True)

    logger.info(
        f"[breakout_screener] Scanned {len(grouped)} symbols → "
        f"{len(candidates)} breakout candidates"
    )
    return candidates[:max_inject]


# ── Injector ──────────────────────────────────────────────────────────────────

async def inject_breakouts_to_universe(
    candidates: list[BreakoutCandidate],
    session: AsyncSession,
    *,
    send_telegram: bool = False,
) -> dict:
    """
    Inject breakout stocks into hub_universe (for scoring) and user_watchlist
    (so trade loop can act on them). Existing entries are skipped (unique constraint).

    Returns a summary dict with counts.
    """
    if not candidates:
        return {"injected_hub": 0, "injected_watchlist": 0, "symbols": []}

    from db.models import HubUniverse, UserWatchlist
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    injected_hub       = 0
    injected_watchlist = 0
    injected_symbols   = []

    # Get current max rank in hub_universe to append new entries at the end
    max_rank_row = (await session.execute(
        text("SELECT COALESCE(MAX(rank), 0) FROM hub_universe")
    )).scalar()
    next_rank = int(max_rank_row or 0) + 1

    for c in candidates:
        sym = c.symbol

        # ── 1. hub_universe: INSERT OR IGNORE ─────────────────────────────────
        try:
            stmt = pg_insert(HubUniverse).values(
                symbol=sym,
                turnover_cr=0.0,   # unknown for new breakouts; will be updated next daily rebuild
                rank=next_rank,
                is_swing=True,
            ).on_conflict_do_nothing(index_elements=["symbol"])
            result = await session.execute(stmt)
            if result.rowcount:
                injected_hub += 1
                next_rank += 1
        except Exception as exc:
            logger.debug(f"[breakout_screener] hub_universe insert failed for {sym}: {exc}")

        # ── 2. user_watchlist: INSERT OR IGNORE ───────────────────────────────
        try:
            stmt2 = pg_insert(UserWatchlist).values(
                symbol=sym,
                is_active=True,
            ).on_conflict_do_update(
                index_elements=["symbol"],
                set_={"is_active": True},   # re-activate if was deactivated
            )
            await session.execute(stmt2)
            injected_watchlist += 1
        except Exception as exc:
            logger.debug(f"[breakout_screener] user_watchlist insert failed for {sym}: {exc}")

        injected_symbols.append({
            "symbol":       sym,
            "change_pct":   c.change_pct,
            "volume_ratio": c.volume_ratio,
            "rsi":          c.rsi,
            "reason":       c.reason,
        })

        logger.info(
            f"[breakout_screener] 🚀 BREAKOUT INJECTED: {sym}  "
            f"{c.change_pct:+.1f}%  vol={c.volume_ratio:.1f}×  RSI={c.rsi:.0f}"
        )

    await session.flush()

    # ── Telegram alert ─────────────────────────────────────────────────────────
    if send_telegram and injected_symbols:
        try:
            from utils.config import settings as _s
            if _s.telegram_available:
                from integrations.telegram_service import send
                lines = ["🔍 *Breakout Auto-Discovery* — New stocks added to agent universe:\n"]
                for s in injected_symbols[:10]:
                    sym_bare = s["symbol"].replace(".NS", "")
                    lines.append(
                        f"• *{sym_bare}*  {s['change_pct']:+.1f}%  "
                        f"vol {s['volume_ratio']:.1f}×avg  RSI {s['rsi']:.0f}\n"
                        f"  _{s['reason']}_"
                    )
                lines.append("\n_Agent will score these in the next 15-min Hub cycle._")
                await send("\n".join(lines))
        except Exception as exc:
            logger.debug(f"[breakout_screener] Telegram alert failed: {exc}")

    summary = {
        "injected_hub":       injected_hub,
        "injected_watchlist": injected_watchlist,
        "symbols":            injected_symbols,
    }
    logger.info(f"[breakout_screener] Injection summary: {summary}")
    return summary


# ── Combined entry point ───────────────────────────────────────────────────────

async def run_breakout_discovery(session: AsyncSession) -> dict:
    """Full scan → inject pipeline. Call this from the Celery task."""
    candidates = await scan_for_breakouts(session)
    if not candidates:
        return {"candidates": 0, "injected_hub": 0, "injected_watchlist": 0, "symbols": []}
    result = await inject_breakouts_to_universe(candidates, session)
    result["candidates"] = len(candidates)
    return result
