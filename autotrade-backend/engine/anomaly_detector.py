"""Real-time price/volume anomaly scoring — Phase 1 of the pre-event market
anomaly engine (2026-07-23).

Origin: the Nestlé forensic audit (docs/NEWS_INGESTION_LATENCY_FORENSIC_AUDIT.md)
found the real bottleneck wasn't crawler latency — Nestlé's price had already
moved 11:09-11:11 IST, before the 11:11:18 NSE filing our crawler eventually
caught. This module scores how abnormal a stock's current price/volume
behaviour is relative to its own history and to the broader market, so a
stock can be escalated to catalyst investigation (news_discovery_engine.py's
_investigate_anomaly_catalyst) before the exchange filing even lands.

Deliberately scores ONLY -- it never constructs a trade. Doing so would
reintroduce the exact independent TECHNICAL-strategy trade origination the
News-Only pivot (commit 9f19111) hard-blocks in engine/decision_router.py.
A high anomaly score is a trigger for faster catalyst discovery; if no real
catalyst (earnings event, NSE filing, news item) is found, nothing
downstream of this module ever runs.

Rule-based, not ML, by design (see the report review this followed): the
gap here is real-time signal capture, not modeling sophistication, and
simple thresholds are auditable in a way a trained model isn't.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from crawler.price_feed import get_latest_candles
from crawler.market_snapshot import get_market_snapshot
from utils.logger import logger

_BASELINE_TIMEFRAME = "5m"
_BASELINE_LOOKBACK_BARS = 2000   # ~30 trading days of 5-min bars (6.25hr session)
_BASELINE_MIN_BARS = 100        # don't trust a baseline built on too little history
_NIFTY_SYMBOL = "^NSEI"

# Tiers exactly as specified in the approved plan / user's own review of the
# anomaly-detection report: <60 normal, 60-75 MONITOR (log only), 75-90
# ALERT (log + eligible for investigation), >90 INVESTIGATE (triggers
# catalyst discovery).
_MONITOR_THRESHOLD = 60.0
_ALERT_THRESHOLD = 75.0
_INVESTIGATE_THRESHOLD = 90.0

# Market-wide-move gate ("Case D" in the user's review): a stock moving with
# the whole market isn't a stock-specific anomaly. INVESTIGATE only fires if
# the stock is also outperforming/underperforming NIFTY by at least this many
# percentage points.
_MIN_RELATIVE_STRENGTH_PCT = 1.0

_baseline_stats_cache: dict[str, dict] = {}


@dataclass
class AnomalyReading:
    symbol: str
    price_z: float
    volume_ratio: float
    relative_strength: float
    vwap_deviation: float
    anomaly_score: float
    tier: str  # "NORMAL" | "MONITOR" | "ALERT" | "INVESTIGATE"


def _tier_for_score(score: float) -> str:
    if score >= _INVESTIGATE_THRESHOLD:
        return "INVESTIGATE"
    if score >= _ALERT_THRESHOLD:
        return "ALERT"
    if score >= _MONITOR_THRESHOLD:
        return "MONITOR"
    return "NORMAL"


def reset_baseline_cache() -> None:
    """Test helper / manual ops override — forces the next call to
    recompute every symbol's baseline instead of using today's cache."""
    _baseline_stats_cache.clear()


async def _get_baseline_stats(symbol: str, session: AsyncSession) -> dict | None:
    """Per-symbol baseline (mean/std of 5-min returns, avg 5-min volume),
    recomputed once per calendar day and cached in-process for the rest of
    the day -- this is a historical-distribution snapshot, it doesn't need
    to be fresher than daily. Returns None (fail-closed) if there isn't
    enough candle history to trust the baseline."""
    today = date.today()
    cached = _baseline_stats_cache.get(symbol)
    if cached and cached.get("computed_date") == today:
        return cached

    bars = await get_latest_candles(symbol, _BASELINE_TIMEFRAME, _BASELINE_LOOKBACK_BARS, session)
    if len(bars) < _BASELINE_MIN_BARS:
        logger.debug(f"[anomaly] {symbol}: only {len(bars)} baseline bars (need {_BASELINE_MIN_BARS}+), skipping")
        return None

    bars = list(reversed(bars))  # DB returns newest-first; want oldest->newest
    returns = [
        (cur.close - prev.close) / prev.close
        for prev, cur in zip(bars, bars[1:])
        if prev.close and prev.close > 0
    ]
    volumes = [b.volume for b in bars if b.volume]
    if len(returns) < _BASELINE_MIN_BARS - 1 or not volumes:
        return None

    stats = {
        "mean_5min_ret":   statistics.mean(returns),
        "std_5min_ret":    statistics.pstdev(returns) or 1e-6,
        "avg_5min_volume": statistics.mean(volumes) or 1.0,
        "computed_date":   today,
    }
    _baseline_stats_cache[symbol] = stats
    return stats


def _compute_score(price_z: float, volume_ratio: float, vwap_deviation: float) -> float:
    """Weighted composite 0-100 from price/volume/VWAP alone, transparent by
    construction (see module docstring on why rule-based beats ML for
    Phase 1). Deliberately does NOT include relative_strength -- that stays
    a separate gate (see get_anomaly_reading's market-wide-move check)
    rather than a scored component, so a strong market-wide move can never
    reach INVESTIGATE on its own merely by being large; it must also be
    genuinely stock-specific."""
    z_component = min(abs(price_z) / 5.0, 1.0) * 60                    # up to 60 pts
    vol_component = min(max(volume_ratio - 1.0, 0.0) / 5.0, 1.0) * 35  # up to 35 pts
    vwap_component = min(abs(vwap_deviation) / 0.03, 1.0) * 5          # up to 5 pts
    return round(z_component + vol_component + vwap_component, 1)


async def get_anomaly_reading(symbol: str, session: AsyncSession) -> AnomalyReading | None:
    """Score `symbol`'s current price/volume behaviour against its own
    history and against NIFTY. Returns None (fail-closed) if there isn't
    enough baseline history or live data to score confidently -- an
    unscoreable symbol is silently skipped, never flagged."""
    baseline = await _get_baseline_stats(symbol, session)
    if baseline is None:
        return None

    recent = await get_latest_candles(symbol, _BASELINE_TIMEFRAME, 3, session)
    if len(recent) < 2:
        return None
    recent = list(reversed(recent))  # oldest -> newest
    prev_close = recent[-2].close
    cur = recent[-1]
    if not prev_close or prev_close <= 0:
        return None

    cur_ret = (cur.close - prev_close) / prev_close
    price_z = (cur_ret - baseline["mean_5min_ret"]) / baseline["std_5min_ret"]
    volume_ratio = (cur.volume or 0.0) / baseline["avg_5min_volume"]

    # VWAP deviation approximation: Phase 1 has no continuous tick feed, so
    # there's no true intraday VWAP to compare against. Approximate VWAP as
    # the volume-weighted typical price ((H+L+C)/3) of the same recent bars.
    typical_prices = [(b.high + b.low + b.close) / 3.0 for b in recent]
    vols = [b.volume or 0.0 for b in recent]
    total_vol = sum(vols) or 1.0
    approx_vwap = sum(tp * v for tp, v in zip(typical_prices, vols)) / total_vol
    vwap_deviation = (cur.close - approx_vwap) / approx_vwap if approx_vwap > 0 else 0.0

    relative_strength = 0.0
    nifty_snap = await get_market_snapshot(_NIFTY_SYMBOL)
    if nifty_snap and nifty_snap.change_pct is not None:
        stock_snap = await get_market_snapshot(symbol)
        stock_change_pct = (
            stock_snap.change_pct if stock_snap and stock_snap.change_pct is not None
            else cur_ret * 100.0
        )
        relative_strength = stock_change_pct - nifty_snap.change_pct

    anomaly_score = _compute_score(price_z, volume_ratio, vwap_deviation)
    tier = _tier_for_score(anomaly_score)

    if tier == "INVESTIGATE" and abs(relative_strength) < _MIN_RELATIVE_STRENGTH_PCT:
        tier = "ALERT"

    return AnomalyReading(
        symbol=symbol, price_z=round(price_z, 2), volume_ratio=round(volume_ratio, 2),
        relative_strength=round(relative_strength, 2), vwap_deviation=round(vwap_deviation, 4),
        anomaly_score=anomaly_score, tier=tier,
    )
