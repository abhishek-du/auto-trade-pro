"""India-specific trading signal generator combining 15 algorithm inputs.

Weights
-------
Candlestick patterns    :  20 %
Technical indicators    :  35 %  (RSI, MACD, BB, EMA, ATR, Stochastic,
                                   Supertrend, VWAP, Ichimoku, ADX, EMA Ribbon)
FII / DII institutional :  20 %
News sentiment          :  10 %
Sector rotation         :  10 %  (PCR score for index symbols)
India VIX               :   5 %

RBI proximity modifier  : -30 % damping when a meeting is within 3 days.

Public API
----------
generate_india_signal(symbol, timeframe, candles_df, session) -> TradingSignal
analyze_all_india_symbols(session)                            -> list[TradingSignal]
"""

from __future__ import annotations

import math
from datetime import datetime

import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.fii_dii_crawler import calculate_fii_dii_score
from crawler.india_price_feed import is_nse_market_open
from crawler.news_crawler import get_market_sentiment
from crawler.options_chain import calculate_options_score
from crawler.price_feed import get_latest_candles
from db.models import FIIDIIFlow, OptionsChainSnapshot
from engine.candlestick import detect_patterns, get_pattern_summary
from engine.india_specific import (
    calculate_india_vix_score,
    calculate_sector_rotation_score,
    get_rbi_event_proximity_score,
    SECTOR_MAP,
)
from engine.indicators import IndicatorSignals, compute_indicators, suggest_stop_loss, suggest_take_profit
from engine.signal_generator import TradingSignal, save_signal
from utils.config import settings
from utils.logger import logger

# ── Constants ─────────────────────────────────────────────────────────────────

_BUY_THRESHOLD:    float = 25.0
_SELL_THRESHOLD:   float = -25.0
_MAX_PATTERN_SCORE: float = 9.0   # mirrors signal_generator normalisation ceiling

# yfinance ticker → NSE options chain symbol name
_OPTIONS_SYMBOL_MAP: dict[str, str] = {
    "^NSEI":    "NIFTY",
    "^NSEBANK": "BANKNIFTY",
}


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _fetch_latest_options_snapshot(
    options_symbol: str,
    session: AsyncSession,
) -> OptionsChainSnapshot | None:
    """Return most recent OptionsChainSnapshot for NIFTY or BANKNIFTY."""
    return (await session.execute(
        select(OptionsChainSnapshot)
        .where(OptionsChainSnapshot.symbol == options_symbol)
        .order_by(desc(OptionsChainSnapshot.snapshot_at))
        .limit(1)
    )).scalars().first()


async def _fetch_latest_fii_net(session: AsyncSession) -> float:
    """Return the most recent FII net buy value (Cr) from DB, or 0."""
    row = (await session.execute(
        select(FIIDIIFlow).order_by(desc(FIIDIIFlow.date)).limit(1)
    )).scalars().first()
    return float(row.fii_net_buy) if row else 0.0


def _build_india_reasoning(
    symbol:          str,
    indicators:      IndicatorSignals,
    pattern_summary: dict,
    fii_net_buy:     float,
    vix_score:       float,
    sector_score:    float,
    rbi_modifier:    float,
    snapshot:        OptionsChainSnapshot | None,
) -> list[str]:
    """Build plain-English reasoning bullet points for an India signal."""
    points: list[str] = []
    nan = math.isnan

    # ── FII / DII flow ────────────────────────────────────────────────────────
    if not nan(fii_net_buy):
        if fii_net_buy > 0:
            points.append(
                f"FII net flow: {fii_net_buy:,.0f} Cr — bullish institutional activity"
            )
        elif fii_net_buy < 0:
            points.append(
                f"FII net flow: {fii_net_buy:,.0f} Cr — bearish institutional selling"
            )

    # ── India VIX ─────────────────────────────────────────────────────────────
    if vix_score >= 20:
        points.append(
            "India VIX elevated — contrarian buy zone, fear at extreme levels"
        )
    elif vix_score == 15.0:
        points.append(
            "India VIX at low levels — low fear market, trend signals more reliable"
        )
    elif vix_score == -10.0:
        points.append(
            "India VIX at complacency lows — elevated risk of correction"
        )

    # ── Sector rotation ───────────────────────────────────────────────────────
    sector = SECTOR_MAP.get(symbol)
    if sector and sector_score != 0:
        direction  = "outperforming" if sector_score > 0 else "underperforming"
        magnitude  = abs(sector_score)
        approx_pct = "5%+" if magnitude >= 25 else "2–5%"
        points.append(
            f"Sector {sector} {direction} NIFTY by approx {approx_pct} over last 30 days"
        )

    # ── Supertrend ────────────────────────────────────────────────────────────
    if not nan(indicators.supertrend):
        side = "above" if indicators.supertrend_direction == "BULLISH" else "below"
        points.append(
            f"Supertrend: {indicators.supertrend_direction} — "
            f"line at {indicators.supertrend:.2f}, price {side} support"
        )

    # ── Ichimoku Cloud ────────────────────────────────────────────────────────
    if indicators.ichimoku_signal in ("STRONG_BUY", "BUY"):
        points.append(
            "Ichimoku: price above cloud, Tenkan above Kijun — strong uptrend confirmed"
        )
    elif indicators.ichimoku_signal in ("STRONG_SELL", "SELL"):
        points.append(
            "Ichimoku: price below cloud, Tenkan below Kijun — strong downtrend confirmed"
        )

    # ── PCR / options chain (index symbols only) ──────────────────────────────
    if snapshot:
        pcr = snapshot.pcr
        if pcr > 1.5:
            points.append(
                f"PCR: {pcr:.2f} — extreme put buying = contrarian BUY signal"
            )
        elif pcr > 1.2:
            points.append(
                f"PCR: {pcr:.2f} — bearish sentiment = contrarian BUY signal"
            )
        elif pcr < 0.5:
            points.append(
                f"PCR: {pcr:.2f} — extreme call buying = contrarian SELL signal"
            )
        elif pcr < 0.8:
            points.append(
                f"PCR: {pcr:.2f} — bullish sentiment = contrarian SELL signal"
            )

    # ── RBI proximity ─────────────────────────────────────────────────────────
    if rbi_modifier == -10.0:
        points.append("RBI meeting within 3 days — reducing position size by 30%")
    elif rbi_modifier == 5.0:
        points.append(
            "Post-RBI announcement — policy clarity supports position confidence"
        )

    # ── ADX trend strength ────────────────────────────────────────────────────
    if not nan(indicators.adx):
        if indicators.adx_trend_strength == "STRONG":
            points.append(
                f"ADX at {indicators.adx:.1f} — strong trending market, "
                f"{indicators.adx_direction.lower()} direction"
            )
        elif indicators.adx_trend_strength == "NONE":
            points.append(
                f"ADX at {indicators.adx:.1f} — no clear trend, ranging conditions"
            )

    # ── EMA Ribbon ────────────────────────────────────────────────────────────
    if indicators.ema_ribbon_state == "BULLISH_SPREAD":
        points.append("EMA Ribbon fully fanned bullish — strong uptrend momentum")
    elif indicators.ema_ribbon_state == "BEARISH_SPREAD":
        points.append("EMA Ribbon fully fanned bearish — strong downtrend momentum")
    elif indicators.ema_ribbon_state == "COMPRESSED":
        points.append("EMA Ribbon compressed — consolidation zone, breakout pending")

    # ── RSI ───────────────────────────────────────────────────────────────────
    if not nan(indicators.rsi):
        if indicators.rsi_signal == "OVERSOLD":
            points.append(
                f"RSI at {indicators.rsi:.1f} — oversold, potential reversal opportunity"
            )
        elif indicators.rsi_signal == "OVERBOUGHT":
            points.append(
                f"RSI at {indicators.rsi:.1f} — overbought, caution on new long positions"
            )

    # ── Candlestick patterns ──────────────────────────────────────────────────
    if pattern_summary["count"] > 0 and pattern_summary.get("strongest_pattern"):
        points.append(
            f"Candlestick: {pattern_summary['strongest_pattern']} "
            f"({pattern_summary['direction']}) — "
            f"{pattern_summary['count']} pattern(s) on latest bar"
        )

    return points


# ── Core signal generator ─────────────────────────────────────────────────────

async def generate_india_signal(
    symbol:     str,
    timeframe:  str,
    candles_df: pd.DataFrame,
    session:    AsyncSession,
) -> TradingSignal:
    """Generate a BUY / SELL / HOLD decision for a single Indian market symbol.

    Combines 15 algorithm inputs across technical, fundamental, and
    sentiment dimensions, weighted specifically for NSE market dynamics.

    Parameters
    ----------
    symbol      : NSE ticker, e.g. 'TCS.NS', '^NSEI'.
    timeframe   : Candle interval, e.g. '1d'.
    candles_df  : OHLCV DataFrame with at least 5 rows (50+ recommended).
    session     : Active async SQLAlchemy session.

    Returns
    -------
    TradingSignal — always returns, never raises.
    """
    now = datetime.utcnow()

    # ── Fallback for empty / insufficient data ────────────────────────────────
    if candles_df.empty or len(candles_df) < 20:
        logger.warning(
            f"Skipping {symbol} — only {len(candles_df)} candles, need 20+"
        )
        entry = float(candles_df["close"].iloc[-1]) if not candles_df.empty else 0.0
        return TradingSignal(
            symbol=symbol, timeframe=timeframe, action="HOLD",
            confidence=0.0, entry_price=entry,
            stop_loss=entry, take_profit=entry,
            pattern_score=0.0, indicator_score=0.0,
            sentiment_score=0.0, final_score=0.0,
            patterns_detected=[],
            reasoning_points=[f"Insufficient candle data ({len(candles_df)} rows, need 20+) — defaulting to HOLD"],
            timestamp=now,
        )

    # ── Step 1: Technical analysis ────────────────────────────────────────────
    patterns        = detect_patterns(candles_df)
    pattern_summary = get_pattern_summary(patterns)
    pattern_score   = max(-100.0, min(100.0,
        pattern_summary["total_score"] / _MAX_PATTERN_SCORE * 100.0
    ))
    patterns_names  = [p.name for p in patterns]

    indicators = compute_indicators(candles_df)

    try:
        news_score = (await get_market_sentiment(symbol, session)) * 100.0
    except Exception as exc:
        logger.warning(f"generate_india_signal: sentiment fetch failed for {symbol}: {exc}")
        news_score = 0.0

    # ── Step 2: Indian-specific scores ───────────────────────────────────────
    try:
        fii_score = await calculate_fii_dii_score(session)
    except Exception as exc:
        logger.warning(f"generate_india_signal: FII score failed: {exc}")
        fii_score = 0.0

    try:
        vix_score = await calculate_india_vix_score(session)
    except Exception as exc:
        logger.warning(f"generate_india_signal: VIX score failed: {exc}")
        vix_score = 0.0

    try:
        sector_score = await calculate_sector_rotation_score(symbol, session)
    except Exception as exc:
        logger.warning(f"generate_india_signal: sector score failed: {exc}")
        sector_score = 0.0

    rbi_modifier = get_rbi_event_proximity_score()

    # Options chain PCR — only for Nifty / BankNifty index symbols
    snapshot: OptionsChainSnapshot | None = None
    pcr_score = 0.0
    options_symbol = _OPTIONS_SYMBOL_MAP.get(symbol)
    if options_symbol:
        try:
            snapshot = await _fetch_latest_options_snapshot(options_symbol, session)
            if snapshot:
                current_price = float(candles_df["close"].iloc[-1])
                pcr_score = calculate_options_score(
                    snapshot.pcr, snapshot.max_pain, current_price
                )
        except Exception as exc:
            logger.warning(
                f"generate_india_signal: options score failed for {symbol}: {exc}"
            )

    # Raw FII net for reasoning text
    fii_net_buy = 0.0
    try:
        fii_net_buy = await _fetch_latest_fii_net(session)
    except Exception:
        pass

    # ── Step 3: Weighted final score ──────────────────────────────────────────
    # For index symbols, PCR replaces sector rotation (no SECTOR_MAP entry)
    rotation_contrib = (pcr_score if options_symbol else sector_score) * 0.10

    final_score = (
        pattern_score              * 0.20
        + indicators.composite_score * 0.35
        + news_score               * 0.10
        + fii_score                * 0.20
        + vix_score                * 0.05
        + rotation_contrib
    )

    # RBI event damping — reduce conviction by 30 % near MPC meetings
    if rbi_modifier != 0:
        final_score *= 0.7

    final_score = max(-100.0, min(100.0, final_score))

    # ── Step 4: Decision thresholds ───────────────────────────────────────────
    # Extra guards: no BUY when overbought or VIX in crash territory (>40)
    vix_crash = (vix_score == 25.0)   # VIX > 40 — scale in carefully

    if (final_score > _BUY_THRESHOLD
            and indicators.rsi_signal != "OVERBOUGHT"
            and not vix_crash):
        action = "BUY"
    elif (final_score < _SELL_THRESHOLD
            and indicators.rsi_signal != "OVERSOLD"):
        action = "SELL"
    else:
        action = "HOLD"

    confidence = min(abs(final_score), 100.0)

    # ── Price levels ──────────────────────────────────────────────────────────
    entry_price = float(candles_df["close"].iloc[-1])
    atr         = indicators.atr if not math.isnan(indicators.atr) else entry_price * 0.001
    direction   = action if action != "HOLD" else "BUY"
    stop_loss   = suggest_stop_loss(entry_price, direction, atr)
    take_profit = suggest_take_profit(entry_price, stop_loss, direction)

    # ── Step 5: Reasoning ─────────────────────────────────────────────────────
    reasoning = _build_india_reasoning(
        symbol, indicators, pattern_summary,
        fii_net_buy, vix_score, sector_score,
        rbi_modifier, snapshot,
    )

    logger.info(
        f"INDIA SIGNAL │ {action:<4} {symbol:<18} │ "
        f"Confidence: {confidence:.0f}% │ "
        f"Score: {final_score:+.1f} │ "
        f"ind={indicators.composite_score:+.1f} "
        f"fii={fii_score:+.1f} "
        f"vix={vix_score:+.1f} "
        f"sect={sector_score:+.1f} "
        f"rbi={rbi_modifier:+.0f}"
    )

    return TradingSignal(
        symbol=symbol,
        timeframe=timeframe,
        action=action,
        confidence=round(confidence, 2),
        entry_price=round(entry_price, 6),
        stop_loss=round(stop_loss, 6),
        take_profit=round(take_profit, 6),
        pattern_score=round(pattern_score, 2),
        indicator_score=round(indicators.composite_score, 2),
        sentiment_score=round(news_score, 2),
        final_score=round(final_score, 2),
        patterns_detected=patterns_names,
        reasoning_points=reasoning,
        timestamp=now,
    )


# ── Batch analyser ────────────────────────────────────────────────────────────

async def analyze_all_india_symbols(
    session: AsyncSession,
    ignore_market_hours: bool = False,
) -> list[TradingSignal]:
    """Run India signal generation for every symbol in all_indian_symbols.

    For each symbol:
      1. Fetches the last 200 hourly candles from the DB.
      2. Skips symbols with fewer than 20 candles.
      3. Generates a signal via generate_india_signal, saves it, continues on error.

    Returns ALL signals (HOLD + BUY + SELL) sorted by abs(final_score) descending.
    Callers should filter for s.action in ("BUY", "SELL") when they need only
    actionable signals. Returning all signals lets the seed endpoint report
    accurate symbols_analysed counts even when no symbol crosses the threshold.
    The caller owns the transaction (no commit here).
    Pass ignore_market_hours=True to run outside NSE trading hours (e.g. seed/test).
    """
    timeframe   = "1h"
    all_symbols = settings.all_indian_symbols
    all_signals: list[TradingSignal] = []
    skipped     = 0

    logger.info(
        f"[india_signals] Starting analysis — "
        f"{len(all_symbols)} symbols  timeframe={timeframe}  "
        f"market_open={is_nse_market_open()}  ignore_hours={ignore_market_hours}"
    )

    for symbol in all_symbols:
        try:
            candle_rows = await get_latest_candles(symbol, timeframe, 200, session)

            if len(candle_rows) < 20:
                logger.warning(
                    f"Skipping {symbol} — only {len(candle_rows)} candles, need 20+"
                )
                skipped += 1
                continue

            # get_latest_candles returns newest-first; reverse for chronological order
            candle_rows = list(reversed(candle_rows))
            df = pd.DataFrame([
                {
                    "open":      c.open,
                    "high":      c.high,
                    "low":       c.low,
                    "close":     c.close,
                    "volume":    c.volume,
                    "timestamp": c.timestamp,
                }
                for c in candle_rows
            ])

            signal = await generate_india_signal(symbol, timeframe, df, session)
            logger.info(
                f"[india_signals] {symbol:<20}  action={signal.action:<4}  "
                f"score={signal.final_score:+.3f}  confidence={signal.confidence:.2f}"
            )
            await save_signal(signal, session)
            all_signals.append(signal)

        except Exception as exc:
            logger.error(
                f"analyze_all_india_symbols: error processing {symbol}: {exc}"
            )

    all_signals.sort(key=lambda s: abs(s.final_score), reverse=True)
    actionable = [s for s in all_signals if s.action in ("BUY", "SELL")]

    logger.info(
        f"━━ analyze_all_india_symbols DONE ━━  "
        f"processed={len(all_signals)}  skipped={skipped}  "
        f"actionable={len(actionable)}  "
        f"(BUY={sum(1 for s in actionable if s.action == 'BUY')}  "
        f"SELL={sum(1 for s in actionable if s.action == 'SELL')})"
    )
    return all_signals
