"""Slow-Momentum Screener — finds stocks with sustained 30-day uptrends.

Problem it solves
-----------------
The breakout_screener only catches SINGLE-DAY spikes (≥4% move + 2× volume).
That misses stocks like:
  • SAKSOFT:    +55% over 30 days (gradual grind, not one explosive day)
  • JTEKTINDIA: +16% over 30 days
  • SIGNPOST:   +26% over 30 days
  • ATULAUTO:   +12% over 30 days

These are the stocks Eagle Eyes recommends — slow momentum plays that are NOT
in hub_universe due to low turnover, but have been quietly compounding for weeks.

Criteria (all must be met)
---------------------------
- 30-day price return  ≥ MOMENTUM_MIN_RETURN_PCT  (default 10%)
- 30-day price return  ≤ MOMENTUM_MAX_RETURN_PCT  (default 100%, avoids parabolic traps)
- Close > EMA20                                    (still in uptrend)
- RSI < MOMENTUM_RSI_MAX                           (not overbought, default 80)
- Volume trend: last-5d avg vol ≥ 0.5× first-10d avg  (volume not collapsing)
- NOT already in hub_universe                      (avoid duplicates)

Public API
----------
scan_for_momentum(session)                  → list[MomentumCandidate]
inject_momentum_to_universe(candidates, s)  → dict
run_momentum_discovery(session)             → dict
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import logger


# ── Config helpers ────────────────────────────────────────────────────────────

def _cfg(name: str, default):
    try:
        from utils.config import settings
        return getattr(settings, name, default)
    except Exception:
        return default


def _thresholds() -> tuple:
    """Return (min_return, max_return, rsi_max, max_inject) from config."""
    return (
        float(_cfg("MOMENTUM_MIN_RETURN_PCT",  10.0)),
        float(_cfg("MOMENTUM_MAX_RETURN_PCT", 100.0)),
        float(_cfg("MOMENTUM_RSI_MAX",         80.0)),
        int  (_cfg("MOMENTUM_MAX_INJ",         30)),
    )


LOOKBACK_DAYS: int = 37   # days of 1d candles to load (need 30 for 30d return + buffer)
MIN_CANDLES:   int = 25   # minimum bars needed for reliable signal


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class MomentumCandidate:
    symbol:         str
    close:          float
    return_30d:     float   # % price change over ~30 trading days
    volume_trend:   float   # recent_avg_vol / early_avg_vol ratio
    rsi:            float
    ema20:          float
    reason:         str
    detected_at:    datetime = field(default_factory=datetime.utcnow)


# ── Lightweight indicator helpers ─────────────────────────────────────────────

def _rsi14(closes: list[float]) -> float:
    """Wilder RSI-14. Returns math.nan if < 15 bars."""
    if len(closes) < 15:
        return math.nan
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas[-14:]]
    losses = [-min(d, 0.0) for d in deltas[-14:]]
    ag = sum(gains) / 14
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

async def scan_for_momentum(session: AsyncSession) -> list[MomentumCandidate]:
    """Scan ALL NSE symbols for 30-day sustained momentum.

    Returns a ranked list of MomentumCandidate (highest 30d-return first).
    Skips symbols already in hub_universe — they're already being scored.
    """
    min_ret, max_ret, rsi_max, max_inject = _thresholds()

    cutoff = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS + 5)

    # Load last LOOKBACK_DAYS daily bars for every .NS symbol
    rows = (await session.execute(text("""
        SELECT symbol, close, volume, timestamp
        FROM   candles
        WHERE  timeframe = '1d'
          AND  symbol    LIKE '%.NS'
          AND  timestamp >= :cutoff
        ORDER  BY symbol, timestamp ASC
    """), {"cutoff": cutoff})).all()

    if not rows:
        logger.warning("[momentum_screener] No daily candle data found")
        return []

    from collections import defaultdict
    grouped: dict[str, list] = defaultdict(list)
    for r in rows:
        grouped[r.symbol].append(r)

    # Symbols already in hub_universe — don't re-inject
    existing_hub = set(
        (await session.execute(text("SELECT symbol FROM hub_universe"))).scalars().all()
    )

    candidates: list[MomentumCandidate] = []

    for symbol, bars in grouped.items():
        if symbol in existing_hub:
            continue
        if len(bars) < MIN_CANDLES:
            continue

        closes  = [float(b.close)  for b in bars]
        volumes = [float(b.volume or 0.0) for b in bars]

        today_close = closes[-1]
        # 30d return: compare current close vs close ~30 trading days ago
        base_idx   = max(0, len(closes) - 31)
        base_close = closes[base_idx]

        if base_close <= 0 or today_close <= 0:
            continue

        return_30d = (today_close - base_close) / base_close * 100.0

        # ── Gate 1: minimum sustained return ──────────────────────────────────
        if return_30d < min_ret:
            continue

        # ── Gate 2: not a parabolic blow-off (pump&dump guard) ────────────────
        if return_30d > max_ret:
            continue

        # ── Indicators ────────────────────────────────────────────────────────
        rsi   = _rsi14(closes)
        ema20 = _ema(closes, 20)

        # ── Gate 3: RSI not overbought ────────────────────────────────────────
        if not math.isnan(rsi) and rsi > rsi_max:
            continue

        # ── Gate 4: price above EMA20 (still in uptrend) ─────────────────────
        if not math.isnan(ema20) and today_close < ema20 * 0.97:
            continue  # broke below EMA20 = trend ended

        # ── Gate 5: Volume not collapsing ────────────────────────────────────
        early_vols  = volumes[:10]
        recent_vols = volumes[-5:]
        avg_early   = sum(early_vols) / max(len(early_vols), 1)
        avg_recent  = sum(recent_vols) / max(len(recent_vols), 1)
        vol_trend   = avg_recent / avg_early if avg_early > 0 else 0.0

        if vol_trend < 0.5:   # volume can't have dropped by more than 50%
            continue

        reason_parts = [
            f"30d={return_30d:+.1f}%",
            f"vol_trend={vol_trend:.1f}x",
        ]
        if not math.isnan(rsi):
            reason_parts.append(f"RSI={rsi:.0f}")

        candidates.append(MomentumCandidate(
            symbol       = symbol,
            close        = round(today_close, 2),
            return_30d   = round(return_30d, 2),
            volume_trend = round(vol_trend, 2),
            rsi          = rsi if not math.isnan(rsi) else 50.0,
            ema20        = ema20 if not math.isnan(ema20) else today_close,
            reason       = " | ".join(reason_parts),
        ))

    # Rank: highest 30-day return first
    candidates.sort(key=lambda c: c.return_30d, reverse=True)

    logger.info(
        f"[momentum_screener] Scanned {len(grouped)} symbols → "
        f"{len(candidates)} momentum candidates "
        f"(min={min_ret}% max={max_ret}% rsi_max={rsi_max})"
    )
    return candidates[:max_inject]


# ── Injector ─────────────────────────────────────────────────────────────────

async def inject_momentum_to_universe(
    candidates: list[MomentumCandidate],
    session: AsyncSession,
    *,
    send_telegram: bool = False,
) -> dict:
    """Inject slow-momentum stocks into hub_universe + user_watchlist."""
    if not candidates:
        return {"injected_hub": 0, "injected_watchlist": 0, "symbols": []}

    from db.models import HubUniverse, UserWatchlist
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    injected_hub       = 0
    injected_watchlist = 0
    injected_symbols   = []

    max_rank_row = (await session.execute(
        text("SELECT COALESCE(MAX(rank), 0) FROM hub_universe")
    )).scalar()
    next_rank = int(max_rank_row or 0) + 1

    for c in candidates:
        sym = c.symbol

        # hub_universe: INSERT OR IGNORE
        try:
            stmt = pg_insert(HubUniverse).values(
                symbol      = sym,
                turnover_cr = 0.0,
                rank        = next_rank,
                is_swing    = True,
            ).on_conflict_do_nothing(index_elements=["symbol"])
            result = await session.execute(stmt)
            if result.rowcount:
                injected_hub += 1
                next_rank += 1
        except Exception as exc:
            logger.debug(f"[momentum_screener] hub insert failed {sym}: {exc}")

        # user_watchlist: INSERT OR REACTIVATE
        try:
            stmt2 = pg_insert(UserWatchlist).values(
                symbol    = sym,
                is_active = True,
            ).on_conflict_do_update(
                index_elements=["symbol"],
                set_={"is_active": True},
            )
            await session.execute(stmt2)
            injected_watchlist += 1
        except Exception as exc:
            logger.debug(f"[momentum_screener] watchlist insert failed {sym}: {exc}")

        injected_symbols.append({
            "symbol":       sym,
            "return_30d":   c.return_30d,
            "volume_trend": c.volume_trend,
            "rsi":          c.rsi,
            "reason":       c.reason,
        })

        logger.info(
            f"[momentum_screener] 📈 INJECTED: {sym}  "
            f"{c.return_30d:+.1f}% 30d  vol={c.volume_trend:.1f}x  RSI={c.rsi:.0f}"
        )

    await session.flush()

    # Telegram alert
    if send_telegram and injected_symbols:
        try:
            from utils.config import settings as _s
            if _s.telegram_available:
                from integrations.telegram_service import send
                lines = ["📈 *Slow-Momentum Discovery* — Sustained uptrend stocks:\n"]
                for s in injected_symbols[:10]:
                    bare = s["symbol"].replace(".NS", "")
                    lines.append(
                        f"• *{bare}*  {s['return_30d']:+.1f}% (30d)  "
                        f"vol {s['volume_trend']:.1f}×  RSI {s['rsi']:.0f}\n"
                        f"  _{s['reason']}_"
                    )
                lines.append("\n_These will be Hub-scored in the next 15-min cycle._")
                await send("\n".join(lines))
        except Exception as exc:
            logger.debug(f"[momentum_screener] Telegram failed: {exc}")

    summary = {
        "injected_hub":       injected_hub,
        "injected_watchlist": injected_watchlist,
        "symbols":            injected_symbols,
    }
    logger.info(f"[momentum_screener] Injection summary: {summary}")
    return summary


# ── Combined entry point ──────────────────────────────────────────────────────

async def run_momentum_discovery(session: AsyncSession) -> dict:
    """Full scan → inject pipeline. Call this from the Celery task."""
    candidates = await scan_for_momentum(session)
    if not candidates:
        return {"candidates": 0, "injected_hub": 0, "injected_watchlist": 0, "symbols": []}
    result = await inject_momentum_to_universe(candidates, session, send_telegram=True)
    result["candidates"] = len(candidates)
    return result
