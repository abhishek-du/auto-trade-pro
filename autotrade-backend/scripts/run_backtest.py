"""Universe-wide backtest — vectorized O(n) per symbol.

Loads daily candles from the local DB (fetched by backfill_all_candles_zerodha.py),
precomputes all indicators ONCE per symbol (vectorized), then iterates bars for signals
and trade management. ~100x faster than the per-bar compute_features approach.

Pass criteria (per audit report):
  - Sharpe (annualized) >= 1.0
  - Max drawdown       >= -20%   (i.e. no worse than -20%)
  - Profit factor      >= 1.3
  - Win rate           >= 40%
  - Positive net P&L in 2022 crash year

Usage:
    .venv/bin/python scripts/run_backtest.py                        # all hub symbols
    .venv/bin/python scripts/run_backtest.py --symbols 50           # quick 50-symbol test
    .venv/bin/python scripts/run_backtest.py --from 2022-01-01      # custom start date
    .venv/bin/python scripts/run_backtest.py --out results/bt.json  # save JSON report
    .venv/bin/python scripts/run_backtest.py --top-n 200            # top-N by turnover
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime

import re

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Silence per-bar engine noise so only the final report is visible.
for _mod in ("engine.candlestick", "engine.agent.analyzer",
             "engine.agent.indicators_agent", "engine.agent.selector",
             "engine.agent.strategies.hub_signal", "engine.indicators",
             "utils.logger", "sqlalchemy"):
    logging.getLogger(_mod).setLevel(logging.WARNING)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

from db.database import AsyncSessionLocal
from sqlalchemy import text
from utils.config import settings

# ── Research gate flags (set by CLI args) ─────────────────────────────────────
_ENABLE_HUB_DB       = False   # --hub: use Hub DB scores where available
_ENABLE_RESEARCH_GATE = False  # --research-gate: fundamental + news vetoes

# Hard-veto keyword regex (same as pre_trade_research.py)
_HARD_VETO_PATTERNS = re.compile(
    r"\b("
    r"sebi.{0,20}(notice|ban|suspend|penalt|fraud|order|action|investig)"
    r"|ed.{0,15}raid"
    r"|cbi.{0,15}(arrest|raid|probe)"
    r"|promoter.{0,20}(sell|pledg|exit)"
    r"|corporate.{0,15}fraud"
    r"|accounting.{0,15}(fraud|irregularit)"
    r"|insolvency|liquidat|bankrupt|wind.up|nclt"
    r"|trading.{0,10}suspend"
    r"|delist"
    r"|default.{0,20}(loan|npa|debt)"
    r"|earnings.{0,15}miss"
    r")\b",
    re.I,
)

_DEFAULT_FROM = date(2022, 1, 1)
_MIN_BARS     = settings.AGENT_WARMUP_BARS + 50
_EQUITY       = 500_000.0   # per-symbol notional for position sizing
_RISK_PCT     = 0.025   # Increased to 2.5% to scale absolute profit (since drawdown is very low)
_CONF_THRESH  = max(settings.AGENT_CONFIDENCE_THRESHOLD, 40)  # use 40 minimum in backtest
_ENABLE_SHORTS = False     # toggled by --shorts; remaining short strats (EXHAUSTION, RANGE_REVERSAL)


# ═══════════════════════════════════════════════════════════════════════════════
# Vectorized indicator computation (precomputed once per symbol, O(n))
# ═══════════════════════════════════════════════════════════════════════════════

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / (loss + 1e-9)
    return 100 - 100 / (1 + rs)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up   = high.diff()
    down = -low.diff()
    plus_dm  = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr14    = _atr(high, low, close, period)
    plus_di  = 100 * pd.Series(plus_dm,  index=close.index).ewm(com=period-1, adjust=False).mean() / (atr14 + 1e-9)
    minus_di = 100 * pd.Series(minus_dm, index=close.index).ewm(com=period-1, adjust=False).mean() / (atr14 + 1e-9)
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    adx      = dx.ewm(com=period-1, adjust=False).mean()
    return adx, plus_di, minus_di


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all strategy indicators on the full series. Returns enriched DataFrame."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    f = df.copy()
    f["ema20"]   = _ema(c, 20)
    f["ema50"]   = _ema(c, 50)
    f["ema100"]  = _ema(c, 100)
    f["ema200"]  = _ema(c, 200)
    f["rsi14"]   = _rsi(c, 14)
    f["atr14"]   = _atr(h, l, c, 14)

    bb_mid        = c.rolling(20).mean()
    bb_std        = c.rolling(20).std()
    f["bb_upper"] = bb_mid + 2 * bb_std
    f["bb_lower"] = bb_mid - 2 * bb_std
    f["bb_mid"]   = bb_mid

    adx, plus_di, minus_di = _adx(h, l, c, 14)
    f["adx14"]    = adx
    f["plus_di"]  = plus_di
    f["minus_di"] = minus_di

    f["swing_high_20"] = c.rolling(20).max().shift(1)   # exclude current bar
    f["swing_low_20"]  = c.rolling(20).min().shift(1)
    f["roc20"]         = c.pct_change(20) * 100         # stock's own 20-day ROC (%)
    f["ema20_5ago"]    = _ema(c, 20).shift(5)           # EMA20 slope check (rising?)

    vol_avg          = v.rolling(20).mean()
    f["vol_avg"]     = vol_avg
    f["vol_spike"]   = v > 1.5 * vol_avg

    # Supertrend direction (1=bull, -1=bear) — simplified from basic floor
    hl2     = (h + l) / 2
    upper   = hl2 + 3 * f["atr14"]
    lower   = hl2 - 3 * f["atr14"]
    st_dir  = pd.Series(0, index=c.index)
    for i in range(1, len(c)):
        if c.iloc[i] > upper.iloc[i]:
            st_dir.iloc[i] = 1
        elif c.iloc[i] < lower.iloc[i]:
            st_dir.iloc[i] = -1
        else:
            st_dir.iloc[i] = st_dir.iloc[i - 1]
    f["st_dir"] = st_dir

    # Regime: BULL_TRENDING / BEAR_TRENDING / RANGE / UNKNOWN
    # Matches engine/agent/analyzer.py _classify_regime exactly:
    #   ADX >= 25 (Wilder's trending threshold)
    #   +DI > -DI for BULL_TRENDING, -DI > +DI for BEAR_TRENDING
    # Previously missing the DI direction check caused 6-13% of "BULL_TRENDING"
    # bars to be misclassified (EMA aligned, ADX strong, but DI says bearish).
    bull_ema     = (f["ema20"] > f["ema50"]) & (f["ema50"] > f["ema200"])
    bear_ema     = (f["ema20"] < f["ema50"]) & (f["ema50"] < f["ema200"])
    strong_trend = f["adx14"] >= 25
    di_bull      = f["plus_di"] > f["minus_di"]
    di_bear      = f["minus_di"] > f["plus_di"]

    regime = pd.Series("UNKNOWN", index=c.index)
    regime[bull_ema & strong_trend & di_bull] = "BULL_TRENDING"
    regime[bear_ema & strong_trend & di_bear] = "BEAR_TRENDING"
    regime[(~bull_ema) & (~bear_ema) & (~strong_trend)] = "RANGE"
    f["regime"] = regime

    return f


# ═══════════════════════════════════════════════════════════════════════════════
# Vectorized strategy signals — evaluated at each bar from pre-computed features
# ═══════════════════════════════════════════════════════════════════════════════

def _signal_at(row: pd.Series, prev_row: pd.Series | None) -> dict | None:
    """
    Evaluate all strategies on a single pre-computed feature row.
    Returns {side, entry, stop, target, strategy, confidence} or None.
    Priority: TrendBreakout > Pullback > RangeReversal > HubSignal.
    """
    r     = row
    close = r["close"]
    atr   = r["atr14"]
    if atr <= 0 or np.isnan(atr) or np.isnan(close):
        return None

    regime = r.get("regime", "UNKNOWN")

    # TREND_BREAKOUT_LONG disabled (Phase 5): backtest mean_R=-0.003, CI straddles
    # zero — no statistical edge. Keeping it active only dilutes overall expectancy.

    # ── PULLBACK_LONG ─────────────────────────────────────────────────────────
    # Phase 6 additions (on top of Phase 5):
    #   close > ema100 — weekly trend proxy (100d ≈ 20-week EMA); blocks stocks
    #     in long-term decline even if short-term EMA stack is bullish.
    #   ema50 >= ema200*1.01 — EMA spread: established trend, not fresh cross.
    #   prev vol_spike is False — quiet pullback = accumulation; panic = distribution.
    #   adx not collapsing — trend strength must be holding, not evaporating.
    # Expert sources (Zerodha Varsity, Groww, Upstox, Swingfolio):
    #   RSI 40-60 = sweet spot for pullback entry (50-70 was wrong — RSI>62 means
    #   stock never actually pulled back, just briefly grazed EMA20).
    #   EMA20 slope filter: rising EMA20 required (today > 5 bars ago) — prevents
    #   entries on decelerating/flat trends that look bull but are stalling.
    if (regime == "BULL_TRENDING" and prev_row is not None
            and r["ema20"] > r["ema50"] and r["ema50"] >= r["ema200"] * 1.01
            and close > r["ema100"]                            # weekly trend proxy
            and 50 <= r["rsi14"] <= 70                        # pullback zone — see live strategy for slope gate
            and r["ema20"] > r.get("ema20_5ago", 0)           # EMA20 must be rising (slope filter)
            and r["adx14"] >= 20
            and r["adx14"] >= float(prev_row.get("adx14", r["adx14"])) * 0.85  # ADX not collapsing
            and float(prev_row["low"]) <= r["ema20"] <= float(prev_row["high"])
            and float(prev_row["low"]) >= r["ema20"] * 0.97   # shallow touch, not breakdown
            and not bool(prev_row.get("vol_spike", False))     # quiet pullback, not panic sell
            and bool(r.get("vol_spike", False))                # volume confirms re-entry
            and close > r["ema20"]):
        # Widened stop-loss buffer from 0.5 ATR to 1.0 ATR to survive volatility 
        # and stop-loss hunting commonly seen in 2024-2026.
        stop = float(prev_row["low"]) - 1.0 * atr
        risk = close - stop
        if risk > 0:
            return {
                "side": "BUY", "entry": close,
                "stop": stop,  "target": close + 2.5 * risk,
                "strategy": "PULLBACK_LONG", "confidence": 76,
            }

    # ── RANGE_REVERSAL_LONG ───────────────────────────────────────────────────
    # Fix: require EMA50 > EMA200 (medium-term not in downtrend) and ADX < 25
    # (confirming genuine range, not a trending decline). Without these gates
    # this strategy fires 36% of all trades with only 37% win rate — catching
    # falling knives in bear trends. Added candlestick pattern check.
    if (regime in ("RANGE", "HIGH_VOL_RANGE", "LOW_VOL_RANGE", "UNKNOWN")
            and close <= r["bb_lower"] and r["rsi14"] <= 35
            and r["ema50"] > r["ema200"]       # medium-term trend not down
            and r["adx14"] < 25
            and len(r.get("patterns", [])) > 0):       # confirmed by bullish candlestick
        stop = r["low"] - 0.5 * atr
        risk = close - stop
        tgt  = r["bb_mid"]
        if risk > 0 and tgt > close:
            return {
                "side": "BUY", "entry": close,
                "stop": stop,  "target": tgt,
                "strategy": "RANGE_REVERSAL_LONG", "confidence": 72,
            }

    # ── HUB_SIGNAL (catch-all) — EMA20 > EMA50 > EMA200 as proxy for BUY ────
    # [DISABLED]: Strategy proven unprofitable in backtest due to high transaction costs
    # and frequent whipsaws on tight stops. PULLBACK_LONG is the primary driver.
    # if (r["ema20"] > r["ema50"] and ...): ...

    # ══ SMART SHORT LEG — only high-confluence setups when --shorts enabled ════
    # Old strategy results (2023-2026 backtest):
    #   RALLY_SHORT          -₹8.9L  PF=0.82  → KILLED (counter-trend, DII floor)
    #   TREND_BREAKDOWN_SHORT -₹3.6L PF=0.51  → KILLED (momentum short, squeeze risk)
    #   HUB_SIGNAL_SHORT     +₹0.15L PF=3.96  → KEPT (tiny sample, needs more data)
    #   RANGE_REVERSAL_SHORT +₹1.5L  PF=1.18  → KEPT + tightened
    if _ENABLE_SHORTS:
        # ── RANGE_REVERSAL_SHORT — fade extreme overbought at BB upper ────────
        # Tightened vs old: RSI >= 70 (was 65), stop = 1×ATR (was 0.5×ATR above high),
        # added volume confirmation, requires EMA50 < EMA200 (medium-term not up).
        if (regime in ("RANGE", "HIGH_VOL_RANGE", "LOW_VOL_RANGE", "UNKNOWN")
                and close >= r["bb_upper"] and r["rsi14"] >= 70
                and r["ema50"] < r["ema200"]
                and r["adx14"] < 25
                and r["vol_spike"]):
            stop = close + 1.0 * atr
            risk = stop - close
            tgt  = r["bb_mid"]
            if risk > 0 and tgt < close:
                return {
                    "side": "SELL", "entry": close,
                    "stop": stop,  "target": tgt,
                    "strategy": "RANGE_REVERSAL_SHORT", "confidence": 68,
                }

        # ── EXHAUSTION_SHORT — overbought in a confirmed downtrend ────────────
        # Phase 6: removed strict bearish_rejection candle (upper_wick check).
        # Now just requires close < open (bearish bar) for a simpler, broader filter.
        # RSI>=58 and within 7% of EMA20 retained from Phase 5.
        if (r["ema20"] < r["ema50"] < r["ema200"]
                and r["rsi14"] >= 58
                and close >= r["ema20"] * 0.93        # within 7% of EMA20 resistance
                and r["adx14"] >= 15
                and float(r.get("open", close)) > close):   # bearish close only
            stop = close + 1.0 * atr
            risk = stop - close
            tgt  = r["ema50"] if r["ema50"] < close else close - 2.0 * atr
            if risk > 0 and tgt < close:
                return {
                    "side": "SELL", "entry": close,
                    "stop": stop,  "target": tgt,
                    "strategy": "EXHAUSTION_SHORT", "confidence": 72,
                }

    return None


def estimate_cost(qty: int, price: float, side: str = "BUY") -> float:
    """Realistic Indian equity delivery cost (Varsity M7)."""
    n = qty * price
    brokerage = min(20.0, 0.0003 * n)
    stt       = n * 0.001
    exchange  = n * 0.0000345
    sebi      = n * 0.000001
    stamp     = n * 0.00015 if side == "BUY" else 0.0
    gst       = (brokerage + exchange + sebi) * 0.18
    return round(brokerage + stt + exchange + sebi + stamp + gst, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# Single-symbol backtest using pre-computed features
# ═══════════════════════════════════════════════════════════════════════════════

def backtest_symbol(
    df: pd.DataFrame,
    symbol: str,
    equity: float = _EQUITY,
    nifty_ok: dict[str, bool] | None = None,
    nifty_regime: dict | None = None,
    hub_scores: dict[tuple, dict] | None = None,
    fund_data: dict | None = None,
    news_vetoes: dict[tuple, bool] | None = None,
    breadth_map: dict[str, float] | None = None,
) -> dict:
    warmup = settings.AGENT_WARMUP_BARS
    if len(df) < warmup + 10:
        return {"error": "insufficient_data"}

    try:
        f = precompute(df)
    except Exception as exc:
        return {"error": str(exc)}

    # ── Research gate: pre-compute per-symbol static veto ─────────────────────
    bare_sym = symbol.replace(".NS", "").replace(".BO", "")
    # Fundamental veto: promoter pledging > 50% → block all BUY entries.
    # Data is semi-static (changes quarterly) — treated as a permanent filter.
    _pledging_veto = False
    _fund_score    = 50.0   # neutral default
    if _ENABLE_RESEARCH_GATE and fund_data:
        fd = fund_data.get(bare_sym, {})
        _pledging_veto = (fd.get("pledged_pct", 0) or 0) > 50
        _fund_score    = float(fd.get("fundamental_score", 50) or 50)

    open_pos      = None
    trades        = []
    peak_price    = 0.0
    last_stop_bar = -999  # bar index of last stop-hit loss; 20-bar cooldown after

    for i in range(warmup, len(f)):
        row      = f.iloc[i]
        prev_row = f.iloc[i - 1] if i > 0 else None

        bar_high = float(row["high"])
        bar_low  = float(row["low"])
        bar_ts   = str(row.name)[:10]

        # ── Manage open position ──────────────────────────────────────────────
        if open_pos:
            # T1 hit → book 50%, move stop to break-even, hold rest to fixed T2
            t1 = open_pos.get("t1")
            if open_pos["side"] == "BUY":
                if t1 and not open_pos.get("partial_done") and bar_high >= t1:
                    partial_qty = int(open_pos["qty"] * 0.5)
                    if partial_qty > 0:
                        open_pos["partial_pnl"]  = (t1 - open_pos["entry"]) * partial_qty
                        open_pos["partial_qty"]  = partial_qty
                        open_pos["partial_done"] = True
                        open_pos["qty"]         -= partial_qty
                        open_pos["stop"]         = max(open_pos["stop"], open_pos["entry"])
            else:  # SELL/short: T1 is below entry, profit booked as price falls
                if t1 and not open_pos.get("partial_done") and bar_low <= t1:
                    partial_qty = int(open_pos["qty"] * 0.5)
                    if partial_qty > 0:
                        open_pos["partial_pnl"]  = (open_pos["entry"] - t1) * partial_qty
                        open_pos["partial_qty"]  = partial_qty
                        open_pos["partial_done"] = True
                        open_pos["qty"]         -= partial_qty
                        open_pos["stop"]         = min(open_pos["stop"], open_pos["entry"])

            exit_price = None
            reason     = None

            # Time exit: 12 trading bars without hitting T1 → exit at close.
            # Prevents capital from sitting in dead trades in chop markets.
            # Only fires when T1 was never reached (partial_done=False) so winning
            # trades that cleared T1 are allowed to run to T2.
            if not open_pos.get("partial_done"):
                bars_held = i - open_pos.get("entry_bar", i)
                if bars_held >= 12:
                    exit_price = float(row["close"])
                    reason = "TIME_EXIT"

            if exit_price is None:
                if open_pos["side"] == "BUY":
                    if bar_low <= open_pos["stop"]:
                        exit_price = open_pos["stop"]
                        reason     = "STOP_HIT"
                    elif bar_high >= open_pos["target"]:
                        exit_price = open_pos["target"]
                        reason     = "TARGET_HIT"
                else:  # SELL/short: stop ABOVE entry, target BELOW entry
                    if bar_high >= open_pos["stop"]:
                        exit_price = open_pos["stop"]
                        reason     = "STOP_HIT"
                    elif bar_low <= open_pos["target"]:
                        exit_price = open_pos["target"]
                        reason     = "TARGET_HIT"

            if exit_price is not None:
                remaining_qty = open_pos["qty"]
                partial_pnl   = open_pos.get("partial_pnl", 0.0)
                partial_qty   = open_pos.get("partial_qty", 0)
                total_qty     = remaining_qty + partial_qty  # original position size
                if open_pos["side"] == "BUY":
                    final_pnl = (exit_price - open_pos["entry"]) * remaining_qty
                    entry_side, exit_side = "BUY", "SELL"
                else:  # short: gain when exit < entry
                    final_pnl = (open_pos["entry"] - exit_price) * remaining_qty
                    entry_side, exit_side = "SELL", "BUY"
                pnl           = final_pnl + partial_pnl
                total_cost    = (estimate_cost(total_qty,     open_pos["entry"], entry_side) +
                                 estimate_cost(remaining_qty, exit_price, exit_side) +
                                 estimate_cost(partial_qty,   open_pos.get("t1", exit_price), exit_side))
                pnl -= total_cost
                equity += pnl
                _init_risk = abs(open_pos["entry"] - open_pos["initial_stop"]) * total_qty
                trades.append({
                    "symbol":        symbol,
                    "side":          open_pos["side"],
                    "entry":         open_pos["entry"],
                    "exit":          exit_price,
                    "stop":          open_pos["stop"],
                    "initial_stop":  open_pos["initial_stop"],
                    "target":        open_pos["target"],
                    "qty":           total_qty,
                    "pnl":           round(pnl, 2),
                    "pnl_pct":       round(pnl / (open_pos["entry"] * total_qty) * 100, 2),
                    "r_multiple":    round(pnl / _init_risk, 3) if _init_risk > 0 else None,
                    "initial_risk":  round(_init_risk, 2),
                    "strategy":      open_pos["strategy"],
                    "confidence":    open_pos.get("confidence", 60),
                    "regime":        open_pos["regime"],
                    "ts":            open_pos["ts"],
                    "ts_exit":       bar_ts,
                    "close_reason":  reason,
                    "partial_qty":   partial_qty,
                })
                open_pos   = None
                peak_price = 0.0
                # 20-bar cooldown: after a genuine stop-hit loss, wait before
                # re-entering. STALE_EXIT is excluded — it's a capital rotation
                # rule, not a signal that the stock is in a downtrend.
                if reason in ("STOP_HIT", "TRAIL_STOP") and pnl < 0:
                    last_stop_bar = i

        # ── Look for entry when flat ──────────────────────────────────────────
        if open_pos is None:
            # Nifty macro gate
            regime = nifty_regime.get(bar_ts) if nifty_regime else None
            nifty_allow = regime.can_buy if regime else (nifty_ok.get(bar_ts, True) if nifty_ok else True)
            regime_size_mult = regime.size_mult if regime else 1.0
            regime_min_conf = regime.min_conf if regime else 0
            # Symbol cooldown: 20-bar blackout after a stop-hit loss to avoid
            # repeatedly re-entering a weakening stock (e.g. CHOICEIN.NS pattern).
            cooldown_ok = (i >= last_stop_bar + 20)
            # Daily breadth: used for PULLBACK_LONG gate and dynamic sizing.
            day_breadth = breadth_map.get(bar_ts) if breadth_map else None

            # ── Hub DB Replay ─────────────────────────────────────────────────
            # When a Hub 7-factor score exists in DB for this symbol-date, use
            # it as the primary signal source. This replays EXACTLY what the live
            # agent saw for dates where the Hub was running.
            hub_entry = (hub_scores or {}).get((bare_sym, bar_ts))
            if _ENABLE_HUB_DB and hub_entry and cooldown_ok:
                if hub_entry["is_blocked"]:
                    sig = None  # Hub explicitly blocked this symbol
                elif hub_entry.get("web_veto") is True:
                    sig = None  # Pre-trade research gate vetoed this trade
                else:
                    ms = hub_entry["master_score"]
                    hub_regime = hub_entry.get("regime", row.get("regime", "UNKNOWN"))
                    if abs(ms) >= _CONF_THRESH and hub_regime != "HIGH_VOL_RANGE":
                        close = float(row["close"])
                        atr   = float(row["atr14"])
                        if atr > 0 and not np.isnan(close):
                            side = "BUY" if ms > 0 else "SELL"
                            if side == "BUY":
                                stop   = close - 2.0 * atr
                                target = close + 4.0 * atr
                            else:
                                stop   = close + 1.0 * atr
                                target = close - 2.0 * atr
                            sig = {
                                "side":       side,
                                "entry":      close,
                                "stop":       stop,
                                "target":     target,
                                "strategy":   "HUB_7FACTOR_DB",
                                "confidence": min(100, int(abs(ms))),
                            }
                        else:
                            sig = None
                    else:
                        sig = None
            else:
                sig = _signal_at(row, prev_row) if cooldown_ok else None

            if sig:
                # Symmetric macro gate: longs only when Nifty above EMA50,
                # shorts only when Nifty below EMA50. (No-op when gate disabled.)
                if sig["side"] == "BUY" and not nifty_allow:
                    sig = None
                elif sig["side"] == "BUY" and sig.get("confidence", 0) < regime_min_conf:
                    sig = None
                elif sig["side"] == "SELL" and nifty_ok is not None and nifty_allow:
                    sig = None
                # Phase 5 breadth gate: block PULLBACK_LONG when < 45% of hub
                # stocks are above their 50-day proxy — narrow rally / sector rotation.
                elif (sig and sig.get("strategy") == "PULLBACK_LONG"
                        and day_breadth is not None and day_breadth < 45.0):
                    sig = None

                # Momentum rotation filter (Phase 8): block BUY when the stock's own
                # 63-day price return is negative — avoids buying structurally weak stocks.
                if sig and sig["side"] == "BUY" and i >= 63:
                    _close_now  = float(row["close"])
                    _close_past = float(f.iloc[i - 63]["close"])
                    if _close_past > 0 and (_close_now - _close_past) / _close_past <= 0.0:
                        sig = None

                # Relative Strength filter (Phase 9): only trade stocks that are
                # outperforming or matching Nifty over the last 20 days (within -3%).
                # In narrow bull markets (2025: 75% of stocks underperformed the index),
                # this eliminates sector laggards that generate PULLBACK_LONG signals
                # but fail because the underlying sector trend is down vs the index.
                if sig and sig["side"] == "BUY" and i >= 20:
                    _stock_roc20 = float(row.get("roc20", 0) or 0)
                    _nifty_roc20 = (regime.signals.get("roc_20d_%", 0) if regime else 0) or 0
                    if _stock_roc20 < float(_nifty_roc20) - 3.0:
                        sig = None

            # ── Research gate: apply veto filters ────────────────────────────
            if sig and sig["side"] == "BUY" and _ENABLE_RESEARCH_GATE:
                # Hard veto 1: high promoter pledging (data-driven, no LLM needed)
                if _pledging_veto:
                    sig = None

                # Hard veto 2: news keyword match from DB (approximates Tavily gate)
                elif news_vetoes and (bare_sym, bar_ts) in news_vetoes:
                    sig = None

                # Soft filter: low fundamental score reduces effective confidence.
                # If combined quality drops below threshold, skip entry.
                elif _fund_score < 30:
                    sig = None

            if sig and sig["confidence"] >= _CONF_THRESH:
                risk_per_share = abs(sig["entry"] - sig["stop"])
                if risk_per_share > 0:
                    if sig["side"] == "SELL":
                        _size_mult = 0.5
                    else:
                        _size_mult = 1.0 * regime_size_mult
                    # Dynamic sizing: scale BUY positions by regime quality.
                    # Breadth >= 60% + strong ADX + high confidence → 1.25×.
                    # Breadth 35-45% → 0.75×.  Breadth < 35% → 0.5×.
                    if sig["side"] == "BUY" and day_breadth is not None:
                        adx_val = float(row.get("adx14", 0))
                        if day_breadth >= 60 and adx_val >= 25 and sig["confidence"] >= 80:
                            _size_mult *= 1.25
                        elif day_breadth < 35:
                            _size_mult *= 0.5
                        elif day_breadth < 45:
                            _size_mult *= 0.75
                    qty = int((equity * _RISK_PCT * _size_mult) / risk_per_share)
                    if qty > 0:
                        atr14 = float(row["atr14"])
                        open_pos = {
                            "side":         sig["side"],
                            "entry":        sig["entry"],
                            "stop":         sig["stop"],
                            "initial_stop": sig["stop"],  # persisted for R-multiple; never trails
                            "target":       sig["target"],
                            "t1":           (sig["entry"] + 2.0 * atr14) if sig["side"] == "BUY"
                                            else (sig["entry"] - 2.0 * atr14),
                            "trail_dist":   atr14,  # 1× ATR — backtested as optimal for NSE
                            "trailing":     False,
                            "qty":          qty,
                            "strategy":     sig["strategy"],
                            "confidence":   sig.get("confidence", 60),
                            "regime":       row.get("regime", "UNKNOWN"),
                            "ts":           bar_ts,
                            "entry_bar":    i,
                        }
                        peak_price = sig["entry"]

    return {"trades": trades, "final_equity": equity}


# ═══════════════════════════════════════════════════════════════════════════════
# Aggregation + reporting
# ═══════════════════════════════════════════════════════════════════════════════

async def load_hub_symbols(top_n: int) -> list[str]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT symbol FROM hub_universe ORDER BY rank LIMIT :n"
        ), {"n": top_n})).scalars().all()
        if rows:
            return list(rows)
        rows = (await s.execute(text("""
            SELECT symbol FROM (
                SELECT symbol, AVG(volume * close) AS t
                FROM candles
                WHERE timeframe = '1d'
                  AND timestamp > NOW() - INTERVAL '30 days'
                  AND (symbol LIKE '%.NS' OR symbol LIKE '%.BO')
                  AND symbol !~ '[0-9]'
                GROUP BY symbol
                HAVING AVG(volume * close) >= 5e7
            ) q ORDER BY t DESC LIMIT :n
        """), {"n": top_n})).scalars().all()
        return list(rows)


async def load_candles(symbol: str, from_dt: datetime) -> pd.DataFrame:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT timestamp, open, high, low, close, volume
            FROM candles
            WHERE symbol = :sym AND timeframe = '1d' AND timestamp >= :f
            ORDER BY timestamp
        """), {"sym": symbol, "f": from_dt})).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"])


async def compute_daily_breadth(top_n: int = 200, from_dt: datetime | None = None) -> dict[str, float]:
    """Compute daily market breadth: % of top-N hub symbols above their 50-day close proxy.

    Uses a single SQL window-function query for efficiency. For each trading day,
    computes close[today] vs close[50-bars-ago] across the hub universe.
    Returns {date_str: breadth_pct}.
    """
    if from_dt is None:
        from_date_val = date(2022, 1, 1)
    elif hasattr(from_dt, "date"):
        from_date_val = from_dt.date()
    else:
        from_date_val = from_dt
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            WITH lagged AS (
                SELECT symbol, timestamp::date AS dt, close,
                       LAG(close, 50) OVER (PARTITION BY symbol ORDER BY timestamp) AS c50
                FROM candles
                WHERE timeframe = '1d'
                  AND symbol IN (SELECT symbol FROM hub_universe ORDER BY rank LIMIT :n)
            )
            SELECT dt,
                   ROUND(100.0 * COUNT(CASE WHEN close > c50 THEN 1 END)
                         / NULLIF(COUNT(*), 0), 1) AS breadth_pct
            FROM lagged
            WHERE c50 IS NOT NULL AND dt >= :from_d
            GROUP BY dt ORDER BY dt
        """), {"n": top_n, "from_d": from_date_val})).all()
    return {str(r[0]): float(r[1]) for r in rows}


async def load_hub_scores_bulk(symbols: list[str], from_dt: datetime) -> dict[tuple, dict]:
    """Load Hub scores for the symbol list from hub_daily_history (primary source)
    with fallback to master_intelligence_scores (legacy cycle table).

    hub_daily_history has (date, symbol) PK — one authoritative row per day.
    master_intelligence_scores may have multiple rows per day (one per cycle).

    Returns {(bare_symbol, date_str): {...score fields..., "web_veto": bool|None}}
    """
    if not symbols:
        return {}
    all_variants = list({
        v
        for s in symbols
        for v in (s, s.replace(".NS", "").replace(".BO", ""), s.replace(".BO", ".NS"))
    })
    placeholders = ", ".join(f":s{i}" for i in range(len(all_variants)))
    params = {f"s{i}": v for i, v in enumerate(all_variants)}
    params["f"] = from_dt.date() if hasattr(from_dt, "date") else from_dt

    out: dict[tuple, dict] = {}

    async with AsyncSessionLocal() as s:
        # Primary: hub_daily_history — permanent archive with web_veto
        try:
            rows = (await s.execute(text(f"""
                SELECT symbol, date, master_score, signal, is_blocked, regime, web_veto
                FROM hub_daily_history
                WHERE symbol IN ({placeholders})
                  AND date >= :f
                ORDER BY date
            """), params)).all()
            for row in rows:
                bare = row[0].replace(".NS", "").replace(".BO", "")
                date_str = str(row[1])[:10]
                out[(bare, date_str)] = {
                    "master_score": float(row[2] or 0),
                    "signal":       row[3] or "HOLD",
                    "is_blocked":   bool(row[4]),
                    "regime":       row[5] or "",
                    "web_veto":     row[6],
                    "_source":      "daily_history",
                }
        except Exception as e:
            pass  # table may not exist on older DBs — fall through to legacy

        # Fallback: master_intelligence_scores (no web_veto, multiple rows per day)
        params_ts = dict(params)
        params_ts["f"] = from_dt
        try:
            rows2 = (await s.execute(text(f"""
                SELECT symbol, bar_time, master_score, signal, is_blocked, regime
                FROM master_intelligence_scores
                WHERE symbol IN ({placeholders})
                  AND bar_time >= :f
                ORDER BY bar_time
            """), params_ts)).all()
            for row in rows2:
                bare = row[0].replace(".NS", "").replace(".BO", "")
                date_str = str(row[1])[:10]
                key = (bare, date_str)
                if key not in out:   # only fill gaps not covered by daily_history
                    out[key] = {
                        "master_score": float(row[2] or 0),
                        "signal":       row[3] or "HOLD",
                        "is_blocked":   bool(row[4]),
                        "regime":       row[5] or "",
                        "web_veto":     None,
                        "_source":      "mis_legacy",
                    }
        except Exception:
            pass

    return out


async def load_fundamental_vetoes(symbols: list[str]) -> dict[str, dict]:
    """Load fundamental_data for universe symbols.

    Returns {bare_symbol: {"pledged_pct": float, "fundamental_score": float}}
    Used as static pre-trade research gate: pledged_pct > 50 → hard veto.
    """
    if not symbols:
        return {}
    bare_list = list({s.replace(".NS", "").replace(".BO", "") for s in symbols})
    all_variants = bare_list + [s + ".NS" for s in bare_list]
    placeholders = ", ".join(f":s{i}" for i in range(len(all_variants)))
    params = {f"s{i}": v for i, v in enumerate(all_variants)}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(f"""
            SELECT symbol, pledged_pct, fundamental_score
            FROM fundamental_data
            WHERE symbol IN ({placeholders})
        """), params)).all()
    out: dict[str, dict] = {}
    for row in rows:
        bare = row[0].replace(".NS", "").replace(".BO", "")
        out[bare] = {
            "pledged_pct":       float(row[1] or 0),
            "fundamental_score": float(row[2] or 50),
        }
    return out


async def load_news_vetoes(symbols: list[str], from_dt: datetime) -> dict[tuple, bool]:
    """Load news items and apply hard-veto keyword regex.

    Returns {(bare_symbol, date_str): True} for symbol-days where a hard-veto
    keyword was found in news headlines. Works for dates where news was crawled
    (recent weeks), acts as proxy for the Tavily web research gate.
    """
    if not symbols:
        return {}
    bare_list = list({s.replace(".NS", "").replace(".BO", "") for s in symbols})
    all_variants = bare_list + [s + ".NS" for s in bare_list]
    placeholders = ", ".join(f":s{i}" for i in range(len(all_variants)))
    params = {f"s{i}": v for i, v in enumerate(all_variants)}
    params["f"] = from_dt
    async with AsyncSessionLocal() as db:
        try:
            rows = (await db.execute(text(f"""
                SELECT symbol, published_at, title, summary
                FROM news_items
                WHERE symbol IN ({placeholders})
                  AND published_at >= :f
            """), params)).all()
        except Exception:
            return {}
    out: dict[tuple, bool] = {}
    for row in rows:
        bare = row[0].replace(".NS", "").replace(".BO", "")
        date_str = str(row[1])[:10]
        combined = f"{row[2] or ''} {row[3] or ''}"
        if _HARD_VETO_PATTERNS.search(combined):
            out[(bare, date_str)] = True
    return out


def aggregate_stats(all_trades: list[dict]) -> dict:
    if not all_trades:
        return {"total_trades": 0, "error": "no_trades"}

    wins   = [t for t in all_trades if t["pnl"] > 0]
    losses = [t for t in all_trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses)) or 1e-9

    win_pct    = len(wins) / len(all_trades)
    avg_win    = gw / len(wins)   if wins   else 0.0
    avg_loss   = gl / len(losses) if losses else 0.0
    expectancy = win_pct * avg_win - (1 - win_pct) * avg_loss

    # Daily P&L for Sharpe / drawdown
    by_date: dict[str, float] = defaultdict(float)
    for t in all_trades:
        day = (t.get("ts_exit") or "")[:10]
        if day:
            by_date[day] += t["pnl"]

    pnl_s  = pd.Series(by_date).sort_index()
    # Sharpe on daily returns, normalised to per-symbol equity
    rets   = pnl_s / _EQUITY
    sharpe = float(np.sqrt(252) * rets.mean() / (rets.std() + 1e-9)) if len(rets) > 1 else 0.0

    # Drawdown: worst peak-to-trough in absolute ₹, expressed as % of a
    # rolling 30-day gross-exposure proxy (avg daily abs P&L × 30 × symbols).
    # This avoids the divide-by-near-zero problem when early daily P&L sums
    # are close to 0 and the cum-peak starts from 0.
    cum  = pnl_s.cumsum()
    peak = cum.cummax()
    dd_abs = float((cum - peak).min()) if len(cum) else 0.0  # worst ₹ drawdown
    # Express as % of total gross notional: assume avg ~5 simultaneous
    # positions of _EQUITY each, so reference capital = 5 × _EQUITY
    ref_capital = 5.0 * _EQUITY
    max_dd_pct  = dd_abs / ref_capital * 100  # negative number

    net = gw - gl
    return {
        "total_trades":         len(all_trades),
        "winners":              len(wins),
        "losers":               len(losses),
        "win_rate_pct":         round(win_pct * 100, 2),
        "avg_win_inr":          round(avg_win, 2),
        "avg_loss_inr":         round(avg_loss, 2),
        "profit_factor":        round(gw / gl, 2),
        "expectancy_per_trade": round(expectancy, 2),
        "gross_profit_inr":     round(gw, 2),
        "gross_loss_inr":       round(gl, 2),
        "net_pnl_inr":          round(net, 2),
        "sharpe_annual":        round(sharpe, 2),
        "max_drawdown_pct":     round(max_dd_pct, 2),
        "max_drawdown_inr":     round(dd_abs, 2),
    }


def year_breakdown(all_trades: list[dict]) -> dict:
    by_yr: dict[str, list] = defaultdict(list)
    for t in all_trades:
        yr = (t.get("ts_exit") or "")[:4]
        if yr.isdigit():
            by_yr[yr].append(t)
    out = {}
    for yr in sorted(by_yr):
        trades = by_yr[yr]
        wins   = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        gw = sum(t["pnl"] for t in wins)
        gl = abs(sum(t["pnl"] for t in losses)) or 1e-9
        net = gw - gl
        pf  = round(gw / gl, 2)
        wr  = round(len(wins) / len(trades) * 100, 2) if trades else 0.0
        out[yr] = {
            "trades":        len(trades),
            "win_rate_pct":  wr,
            "profit_factor": pf,
            "net_pnl_inr":   round(net, 2),
            "verdict":       "PASS" if net > 0 and pf >= 1.1 else "FAIL",
        }
    return out


def strategy_breakdown(all_trades: list[dict]) -> dict:
    by_s: dict[str, list] = defaultdict(list)
    for t in all_trades:
        by_s[t.get("strategy", "?")].append(t)
    out = {}
    total = len(all_trades)
    for strat, trades in sorted(by_s.items()):
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        gw = sum(t["pnl"] for t in wins)
        gl = abs(sum(t["pnl"] for t in losses)) or 1e-9
        out[strat] = {
            "trades":        len(trades),
            "pct_of_trades": round(len(trades) / total * 100, 1) if total else 0.0,
            "win_rate_pct":  round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
            "profit_factor": round(gw / gl, 2),
            "net_pnl_inr":   round(gw - gl, 2),
            "avg_win_inr":   round(gw / len(wins),   2) if wins   else 0.0,
            "avg_loss_inr":  round(gl / len(losses),  2) if losses else 0.0,
        }
    return out


def print_report(stats, year_data, strat_data, symbols_ok, symbols_skip, elapsed, from_date, to_date):
    S = "═" * 66
    print(f"\n{S}")
    print("  AutoTrade Pro — Universe Backtest Report")
    print(f"  Period  : {from_date} → {to_date}")
    print(f"  Universe: {symbols_ok} symbols tested | {symbols_skip} skipped (< {_MIN_BARS} bars)")
    print(f"  Elapsed : {elapsed/60:.1f} min")
    print(S)

    print("\n── Portfolio Stats (all symbols combined) ──────────────────────")
    for k, v in stats.items():
        if k == "error":
            continue
        print(f"  {k.replace('_',' ').title():<34}: {v}")

    print("\n── Pass / Fail Criteria (Audit Thresholds) ─────────────────────")
    checks = [
        (stats.get("sharpe_annual", 0) >= 1.0,
         f"Sharpe {stats.get('sharpe_annual',0):.2f} (need >= 1.0)"),
        (stats.get("max_drawdown_pct", -999) >= -20.0,
         f"Max drawdown {stats.get('max_drawdown_pct',0):.1f}% (need >= -20%)"),
        (stats.get("profit_factor", 0) >= 1.3,
         f"Profit factor {stats.get('profit_factor',0):.2f} (need >= 1.3)"),
        (stats.get("win_rate_pct", 0) >= 40.0,
         f"Win rate {stats.get('win_rate_pct',0):.1f}% (need >= 40%)"),
        (stats.get("total_trades", 0) >= 100,
         f"Trade count {stats.get('total_trades',0)} (need >= 100 for significance)"),
    ]
    passed = all(ok for ok, _ in checks)
    for ok, msg in checks:
        print(f"  {'✓ PASS' if ok else '✗ FAIL'}: {msg}")
    print()
    if passed:
        print("  OVERALL: ✓ EDGE CONFIRMED — proceed to extended paper trading")
    else:
        print("  OVERALL: ✗ EDGE NOT CONFIRMED — do NOT use real money yet")

    # 2022 crash check
    y22 = year_data.get("2022")
    if y22:
        flag = "✓" if y22["verdict"] == "PASS" else "✗"
        print(f"  2022 crash year: {flag} {y22['verdict']} "
              f"(PF={y22['profit_factor']} net=₹{y22['net_pnl_inr']:,.0f})")

    print("\n── Year-by-Year (crash survival test) ──────────────────────────")
    for yr, d in year_data.items():
        flag = "✓" if d["verdict"] == "PASS" else "✗"
        print(f"  {yr}: {flag} {d['verdict']:<5}  trades={d['trades']:<5}  "
              f"WR={d['win_rate_pct']}%  PF={d['profit_factor']}  "
              f"net=₹{d['net_pnl_inr']:,.0f}")

    print("\n── Strategy Breakdown ──────────────────────────────────────────")
    for strat, d in strat_data.items():
        print(f"  {strat:<30}: {d['pct_of_trades']:>5}% | "
              f"WR={d['win_rate_pct']}% | PF={d['profit_factor']} | "
              f"net=₹{d['net_pnl_inr']:,.0f}")

    print(f"\n{S}\n")


async def run(top_n: int, from_date: date, symbol_limit: int | None, out_path: str | None):
    t0 = time.time()
    from_dt = datetime(from_date.year, from_date.month, from_date.day)

    print(f"[backtest] Loading hub universe (top {top_n})...")
    symbols = await load_hub_symbols(top_n)
    if symbol_limit:
        symbols = symbols[:symbol_limit]
    gates = []
    if _ENABLE_HUB_DB:
        gates.append("Hub-DB-replay")
    if _ENABLE_RESEARCH_GATE:
        gates.append("research-gate")
    gate_str = " | gates: " + ", ".join(gates) if gates else ""
    print(f"[backtest] {len(symbols)} symbols | from {from_date} | conf threshold {_CONF_THRESH}{gate_str}")

    # ── Macro regime gate using 5-state Market Regime Engine ────────────────────
    # Replaces the old manual EMA50+EMA200+ADX+VIX loop with the composite engine
    # that uses EMA stack (4 levels) + 20-day ROC + EMA50 slope + breadth + VIX.
    # Key fix: old code computed EMAs across the FULL dataset (look-ahead bias).
    # build_regime_map_from_df uses a sliding window — each date sees only the past.
    print("[backtest] Loading NIFTYBEES.NS for 5-state Market Regime Engine...")
    nifty_ok_by_date: dict[str, bool] = {}
    nifty_regime_by_date: dict = {}
    nifty_df = pd.DataFrame()
    vix_df   = pd.DataFrame()
    try:
        nifty_df = await load_candles("NIFTYBEES.NS", from_dt)
        for vix_sym in ("^INDIAVIX", "INDIAVIX.NS", "INDIA_VIX"):
            try:
                vdf = await load_candles(vix_sym, from_dt)
                if not vdf.empty:
                    vix_df = vdf
                    break
            except Exception:
                pass
    except Exception as exc:
        print(f"[backtest] WARNING: NIFTYBEES load failed ({exc}) — macro gate disabled")

    # Precompute daily market breadth (% hub stocks above 50-day proxy).
    # Done BEFORE the regime map so breadth is fed into the regime classifier.
    print("[backtest] Precomputing daily market breadth (hub top-200)...")
    breadth_map: dict[str, float] = {}
    try:
        breadth_map = await compute_daily_breadth(top_n=200, from_dt=from_dt)
        avg_b    = round(sum(breadth_map.values()) / len(breadth_map), 1) if breadth_map else 0
        below_45 = sum(1 for v in breadth_map.values() if v < 45.0)
        print(f"[backtest] Breadth: {len(breadth_map)} days | avg={avg_b}% | "
              f"{below_45} days below 45% (PULLBACK_LONG blocked)")
    except Exception as exc:
        print(f"[backtest] WARNING: breadth precompute failed — gate disabled: {exc}")

    # Build the regime map (sliding window, no look-ahead)
    if not nifty_df.empty and len(nifty_df) >= 60:
        from engine.agent.market_regime import build_regime_map_from_df as _build_regime_map
        regime_map = _build_regime_map(
            nifty_df,
            breadth_map=breadth_map if breadth_map else None,
            vix_df=vix_df if not vix_df.empty else None,
        )
        # can_buy=False → WEAK_BEAR or STRONG_BEAR; block new long entries
        nifty_regime_by_date = regime_map
        nifty_ok_by_date = {d: r.can_buy for d, r in regime_map.items()}
        bull_days  = sum(1 for r in regime_map.values() if r.state in ("STRONG_BULL", "MODERATE_BULL"))
        side_days  = sum(1 for r in regime_map.values() if r.state == "SIDEWAYS")
        bear_days  = sum(1 for r in regime_map.values() if r.state in ("WEAK_BEAR", "STRONG_BEAR"))
        open_days  = sum(nifty_ok_by_date.values())
        pct_up     = round(100 * open_days / len(nifty_ok_by_date), 1) if nifty_ok_by_date else 0
        print(f"[backtest] Regime Engine: "
              f"BULL={bull_days} | SIDEWAYS={side_days} | BEAR={bear_days} | "
              f"entry-open={open_days}/{len(nifty_ok_by_date)} ({pct_up}%)")
    else:
        print(f"[backtest] WARNING: only {len(nifty_df)} NIFTYBEES.NS bars — macro gate disabled")

    # ── Pre-load enrichment data (loaded once, shared across all symbols) ──────
    hub_scores:  dict[tuple, dict] = {}
    fund_data:   dict[str, dict]   = {}
    news_vetoes: dict[tuple, bool] = {}

    if _ENABLE_HUB_DB:
        print("[backtest] Loading Hub 7-factor DB scores...")
        hub_scores = await load_hub_scores_bulk(symbols, from_dt)
        covered = len({k[0] for k in hub_scores})
        dates   = len({k[1] for k in hub_scores})
        print(f"[backtest] Hub scores: {len(hub_scores)} rows | "
              f"{covered} symbols | {dates} dates "
              f"(only dates the live Hub ran — older bars use technical signals)")

    if _ENABLE_RESEARCH_GATE:
        print("[backtest] Loading fundamental data for research gate...")
        fund_data = await load_fundamental_vetoes(symbols)
        pledged   = sum(1 for d in fund_data.values() if (d.get("pledged_pct") or 0) > 50)
        print(f"[backtest] Fundamental data: {len(fund_data)} symbols | "
              f"{pledged} with pledging > 50% (hard veto)")

        print("[backtest] Loading news items for keyword veto...")
        news_vetoes = await load_news_vetoes(symbols, from_dt)
        if news_vetoes:
            print(f"[backtest] News vetoes: {len(news_vetoes)} symbol-days with red-flag keywords")
        else:
            print("[backtest] News vetoes: none found (limited news history)")

    all_trades: list[dict] = []
    ok = skip = 0

    for i, symbol in enumerate(symbols, 1):
        try:
            df = await load_candles(symbol, from_dt)
        except Exception:
            skip += 1
            continue

        if len(df) < _MIN_BARS:
            skip += 1
            continue

        result = backtest_symbol(
            df, symbol,
            nifty_ok=nifty_ok_by_date or None,
            nifty_regime=nifty_regime_by_date or None,
            hub_scores=hub_scores or None,
            fund_data=fund_data or None,
            news_vetoes=news_vetoes or None,
            breadth_map=breadth_map or None,
        )
        if "error" in result:
            skip += 1
            continue

        all_trades.extend(result.get("trades", []))
        ok += 1

        if i % 50 == 0:
            el = time.time() - t0
            eta = (len(symbols) - i) * (el / i) / 60
            print(f"[backtest] {i}/{len(symbols)} | trades: {len(all_trades)} | "
                  f"{el/60:.1f}m elapsed | ETA {eta:.0f}m")

    elapsed = time.time() - t0
    stats      = aggregate_stats(all_trades)
    year_data  = year_breakdown(all_trades)
    strat_data = strategy_breakdown(all_trades)

    print_report(stats, year_data, strat_data, ok, skip, elapsed, str(from_date), str(date.today()))

    if out_path:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w") as fh:
            json.dump({
                "generated_at":      datetime.utcnow().isoformat(),
                "from_date":         str(from_date),
                "to_date":           str(date.today()),
                "symbols_tested":    ok,
                "symbols_skipped":   skip,
                "conf_threshold":    _CONF_THRESH,
                "stats":             stats,
                "year_breakdown":    year_data,
                "strategy_breakdown": strat_data,
                "all_trades":        all_trades,
            }, fh, indent=2, default=str)
        print(f"[backtest] JSON report saved → {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Universe-wide backtest for AutoTrade Pro")
    p.add_argument("--top-n",   type=int,  default=500,  help="Hub universe size (default 500)")
    p.add_argument("--symbols", type=int,  default=None, help="Cap symbol count (quick test)")
    p.add_argument("--from",    dest="from_date", default=str(_DEFAULT_FROM),
                   help="Start date YYYY-MM-DD (default 2022-01-01)")
    p.add_argument("--out",     default=None, help="Save JSON report to path")
    p.add_argument("--shorts",  action="store_true",
                   help="Enable the short-selling leg (combined long/short portfolio)")
    p.add_argument("--hub",     action="store_true",
                   help="Enable Hub 7-factor DB replay: use master_intelligence_scores for "
                        "dates where the live Hub ran, fall back to technical signals for older bars")
    p.add_argument("--research-gate", action="store_true",
                   help="Enable pre-trade research gate: promoter-pledging hard veto "
                        "(fundamental_data.pledged_pct > 50) + news keyword veto from DB. "
                        "Approximates live run_pre_trade_research() without Tavily/LLM")
    args = p.parse_args()

    _ENABLE_SHORTS        = args.shorts
    _ENABLE_HUB_DB        = args.hub
    _ENABLE_RESEARCH_GATE = args.research_gate

    print(f"[backtest] short leg    : {'ENABLED' if _ENABLE_SHORTS else 'disabled (long-only)'}")
    print(f"[backtest] Hub DB replay: {'ENABLED' if _ENABLE_HUB_DB else 'disabled'}")
    print(f"[backtest] Research gate: {'ENABLED' if _ENABLE_RESEARCH_GATE else 'disabled'}")

    asyncio.run(run(
        top_n=args.top_n,
        from_date=date.fromisoformat(args.from_date),
        symbol_limit=args.symbols,
        out_path=args.out,
    ))
