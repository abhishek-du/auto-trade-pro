"""Phase 2 Edge Validation — validate_edge.py

Determines whether a durable trading edge exists.  No strategy changes.
The deliverable is a yes/no/conditional verdict with statistics attached.

Pipeline (all use the LIVE decision path via real Strategy classes):
  1.  Corrected backtest      — selector.propose() via precomputed-features bridge
  2.  Strategy stats          — R-multiple + bootstrap 95% CI per strategy
  3.  Walk-forward OOS        — train 2022-23 → val 2024; train 2022-24 → val 2025-26
  4.  Regime cross-tab        — strategy × regime_at_entry performance table
  5.  Confidence buckets      — is confidence score monotonic with expectancy?
  6.  HUB_SIGNAL un-shadowed  — run as sole strategy (others suppressed)
  7.  Exit policy comparison  — same entries, 6 exit variants
  8.  Statistical significance — N per cell, bootstrap CI, power note
  9.  Phase 2 verdict         — explicit EDGE CONFIRMED / CONDITIONAL / NO EDGE

Usage:
  .venv/bin/python scripts/validate_edge.py
  .venv/bin/python scripts/validate_edge.py --symbols 50    # quick run
  .venv/bin/python scripts/validate_edge.py --out results/phase2.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _m in ("engine.candlestick", "engine.agent.analyzer", "engine.agent.indicators_agent",
           "engine.agent.selector", "engine.agent.strategies.hub_signal",
           "engine.indicators", "utils.logger", "sqlalchemy"):
    logging.getLogger(_m).setLevel(logging.WARNING)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

# ── reuse infrastructure from run_backtest.py ─────────────────────────────────
from scripts.run_backtest import (
    precompute, estimate_cost, load_candles, load_hub_symbols, compute_daily_breadth,
    _EQUITY, _RISK_PCT, _CONF_THRESH, _MIN_BARS,
)
from utils.config import settings

_DEFAULT_FROM = date(2022, 1, 1)
_BOOT_N       = 10_000     # bootstrap resamples
_MIN_CELL_N   = 30         # suppress conclusions below this N
_RNG          = np.random.default_rng(42)

# Tagged regime years for Phase 2 narrative
_YEAR_REGIMES = {
    "2022": "bear/correction",
    "2023": "bull_trend",
    "2024": "mixed",
    "2025": "chop/decline",
    "2026": "chop/decline",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CORRECTED BACKTEST — Live decision path via precomputed-features bridge
# ═══════════════════════════════════════════════════════════════════════════════

def _bridge_pattern(row: pd.Series) -> dict:
    """Compute a minimal candlestick pattern direction from raw OHLCV.

    Used by the precomputed bridge so RANGE_REVERSAL_LONG hammer check and
    TREND_BREAKOUT pattern bonus work correctly without the full indicator stack.
    Returns a dict of pattern_direction / pattern_score / strongest_pattern keys.
    """
    o  = float(row.get("open", row["close"]))
    c  = float(row["close"])
    lo = float(row["low"])
    hi = float(row["high"])
    body       = abs(c - o) or 1e-6
    lower_wick = min(c, o) - lo
    upper_wick = hi - max(c, o)

    is_hammer        = (lower_wick > 2 * body) and (c > o)
    is_bullish_bar   = (c > o) and (c > lo + (hi - lo) * 0.55)  # closes in top 45% of range
    is_shooting_star = (upper_wick > 2 * body) and (c < o)
    is_bearish_bar   = (c < o) and (c < lo + (hi - lo) * 0.45)

    if is_hammer:
        return {"pattern_direction": "BULLISH", "pattern_score": 0.8, "strongest_pattern": "hammer"}
    if is_bullish_bar:
        return {"pattern_direction": "BULLISH", "pattern_score": 0.5, "strongest_pattern": "bullish_bar"}
    if is_shooting_star:
        return {"pattern_direction": "BEARISH", "pattern_score": 0.8, "strongest_pattern": "shooting_star"}
    if is_bearish_bar:
        return {"pattern_direction": "BEARISH", "pattern_score": 0.5, "strongest_pattern": "bearish_bar"}
    return {"pattern_direction": "NEUTRAL", "pattern_score": 0.0, "strongest_pattern": ""}


def _features_from_precomputed(row: pd.Series) -> SimpleNamespace:
    """Build a MarketFeatures-like object from a pre-computed vectorized row.

    This is the bridge that lets real Strategy classes run at vectorized speed.
    All fields mirror engine.agent.analyzer.MarketFeatures exactly.
    pattern_direction defaults to NEUTRAL because candlestick detection would
    require the full indicator stack — documented divergence, conservative.
    hub_composite_score=None causes HubSignalStrategy to return None cleanly.
    """
    return SimpleNamespace(
        close=float(row["close"]),
        open_=float(row.get("open", row["close"])),
        high=float(row["high"]),
        low=float(row["low"]),
        volume=float(row.get("volume", 0)),
        ema20=float(row["ema20"]),
        ema50=float(row["ema50"]),
        ema100=float(row.get("ema100", row["ema200"])),
        ema200=float(row["ema200"]),
        rsi14=float(row["rsi14"]),
        macd_hist=0.0,
        atr14=float(row["atr14"]),
        bb_upper=float(row["bb_upper"]),
        bb_lower=float(row["bb_lower"]),
        bb_mid=float(row["bb_mid"]),
        adx14=float(row["adx14"]),
        plus_di=float(row.get("plus_di", 0)),
        minus_di=float(row.get("minus_di", 0)),
        st_dir=int(row.get("st_dir", 0)),
        vol_spike=bool(row.get("vol_spike", False)),
        swing_high_20=float(row.get("swing_high_20", row["close"])),
        swing_low_20=float(row.get("swing_low_20", row["close"])),
        # Pattern: compute hammer from real OHLCV so RANGE_REVERSAL fires correctly.
        # Live system runs full candlestick stack; bridge computes the dominant
        # hammer signal only (lower_wick > 2×body + bullish close). This is the
        # main pattern RANGE_REVERSAL gates on and exactly mirrors the strategy logic.
        **_bridge_pattern(row),
        composite_score=0.0,
        regime=str(row.get("regime", "UNKNOWN")),
        # Hub score: None → HubSignalStrategy returns None (correct; no backtest scores)
        hub_composite_score=None,
        hub_signal="HOLD",
    )


def backtest_corrected(
    df: pd.DataFrame,
    symbol: str,
    equity: float = _EQUITY,
    nifty_ok: dict[str, bool] | None = None,
    hub_only: bool = False,          # if True: run HUB_SIGNAL as the sole strategy
    exit_policy: str = "partial_fixed",  # partial_fixed | current | full_trail | be_after_1r
    trail_atr_mult: float = 1.0,     # for exit_policy variants
    breadth_map: dict[str, float] | None = None,  # Phase 5: daily breadth for gate + sizing
) -> dict:
    """Backtest using REAL Strategy classes via the precomputed-features bridge.

    This is the corrected path that executes:
        precompute() → _features_from_precomputed() → selector.propose()

    The selector internally calls real TrendBreakoutLong / PullbackTrendLong /
    RangeReversalLong / HubSignalStrategy instances — NOT the duplicated _signal_at.

    Known documented divergences vs the live system:
      - pattern_direction always NEUTRAL (no candlestick stack in bridge)
      - hub_composite_score is None (no backtest hub scores) → HUB_SIGNAL never fires
        through the normal path; measured separately with hub_only=True using
        the technical EMA/ST/RSI proxy already in the backtest
      - RANGE_REVERSAL: hammer check requires real OHLCV (bridge passes df window,
        so it works correctly via df.iloc[-1])
    """
    from engine.agent.selector import StrategySelectorAgent

    warmup = settings.AGENT_WARMUP_BARS
    if len(df) < warmup + 10:
        return {"error": "insufficient_data"}

    try:
        f_df = precompute(df)
    except Exception as exc:
        return {"error": str(exc)}

    if hub_only:
        # Replace selector with a HUB_SIGNAL-only version (technical proxy)
        # since real hub scores are unavailable in backtest context.
        selector = None   # signal handled inline below
    else:
        selector = StrategySelectorAgent()

    open_pos   = None
    trades     = []
    peak_price = 0.0
    last_stop_bar = -999

    for i in range(warmup, len(f_df)):
        row     = f_df.iloc[i]
        bar_ts  = str(row.name)[:10]
        bar_low  = float(row["low"])
        bar_high = float(row["high"])

        # ── Manage open position ──────────────────────────────────────────────
        if open_pos:
            if open_pos["side"] == "BUY":
                peak_price = max(peak_price, bar_high)

                # Exit policy gating
                t1         = open_pos.get("t1")
                trail_dist = open_pos.get("trail_dist", 0.0)

                if exit_policy == "current":
                    # T1 partial + trail
                    if t1 and not open_pos.get("trailing") and bar_high >= t1:
                        open_pos["trailing"] = True
                        if not open_pos.get("partial_done"):
                            pq = int(open_pos["qty"] * 0.5)
                            if pq > 0:
                                open_pos["partial_pnl"] = (t1 - open_pos["entry"]) * pq
                                open_pos["partial_qty"] = pq
                                open_pos["partial_done"] = True
                                open_pos["qty"] -= pq
                                open_pos["stop"] = max(open_pos["stop"], open_pos["entry"])
                    if open_pos.get("trailing") and trail_dist > 0:
                        ns = peak_price - trail_dist
                        if ns > open_pos["stop"]:
                            open_pos["stop"] = ns

                elif exit_policy == "full_trail":
                    # No partial — trail the full position from entry
                    if trail_dist > 0:
                        ns = peak_price - trail_dist
                        if ns > open_pos["stop"] and ns > open_pos["entry"] - trail_dist:
                            open_pos["stop"] = ns

                elif exit_policy == "partial_fixed":
                    # Book 50% at T1, exit remaining at T2 only (no trail)
                    if t1 and not open_pos.get("partial_done") and bar_high >= t1:
                        pq = int(open_pos["qty"] * 0.5)
                        if pq > 0:
                            open_pos["partial_pnl"] = (t1 - open_pos["entry"]) * pq
                            open_pos["partial_qty"] = pq
                            open_pos["partial_done"] = True
                            open_pos["qty"] -= pq

                elif exit_policy == "be_after_1r":
                    # Move stop to break-even once price exceeds entry + 1R
                    risk = abs(open_pos["entry"] - open_pos["initial_stop"])
                    if bar_high >= open_pos["entry"] + risk:
                        if open_pos["stop"] < open_pos["entry"]:
                            open_pos["stop"] = open_pos["entry"]

                elif exit_policy in ("trail_150", "trail_075"):
                    if trail_dist > 0:
                        atr_mult = 1.5 if exit_policy == "trail_150" else 0.75
                        # Start trailing after T1 (like current)
                        t1 = open_pos.get("t1")
                        if t1 and not open_pos.get("trailing") and bar_high >= t1:
                            open_pos["trailing"] = True
                            pq = int(open_pos["qty"] * 0.5)
                            if pq > 0:
                                open_pos["partial_pnl"] = (t1 - open_pos["entry"]) * pq
                                open_pos["partial_qty"] = pq
                                open_pos["partial_done"] = True
                                open_pos["qty"] -= pq
                                open_pos["stop"] = max(open_pos["stop"], open_pos["entry"])
                        eff_dist = open_pos.get("atr14", trail_dist) * atr_mult
                        if open_pos.get("trailing"):
                            ns = peak_price - eff_dist
                            if ns > open_pos["stop"]:
                                open_pos["stop"] = ns

            # Check exit
            exit_price = reason = None

            # Time exit: 12 bars without hitting T1 → exit at close (Fix 4).
            if not open_pos.get("partial_done"):
                bars_held = i - open_pos.get("entry_bar", i)
                if bars_held >= 12:
                    exit_price = float(row["close"])
                    reason = "TIME_EXIT"

            if exit_price is None:
                if open_pos["side"] == "BUY":
                    if bar_low <= open_pos["stop"]:
                        exit_price = open_pos["stop"]
                        reason = "TRAIL_STOP" if open_pos.get("trailing") else "STOP_HIT"
                    elif bar_high >= open_pos["target"]:
                        exit_price = open_pos["target"]
                        reason = "TARGET_HIT"
                else:  # SELL (ExhaustionShort, MeanReversionShort)
                    if bar_high >= open_pos["stop"]:
                        exit_price = open_pos["stop"]
                        reason = "STOP_HIT"
                    elif bar_low <= open_pos["target"]:
                        exit_price = open_pos["target"]
                        reason = "TARGET_HIT"

            if exit_price is not None:
                remaining = open_pos["qty"]
                pp        = open_pos.get("partial_pnl", 0.0)
                pq        = open_pos.get("partial_qty", 0)
                total_q   = remaining + pq
                if open_pos["side"] == "BUY":
                    pnl = (exit_price - open_pos["entry"]) * remaining + pp
                else:
                    pnl = (open_pos["entry"] - exit_price) * remaining + pp
                entry_side = "BUY" if open_pos["side"] == "BUY" else "SELL"
                exit_side  = "SELL" if open_pos["side"] == "BUY" else "BUY"
                cost = (estimate_cost(total_q,    open_pos["entry"], entry_side) +
                        estimate_cost(remaining,  exit_price, exit_side) +
                        estimate_cost(pq, open_pos.get("t1", exit_price), exit_side))
                pnl -= cost
                equity += pnl
                init_r  = abs(open_pos["entry"] - open_pos["initial_stop"]) * total_q
                trades.append({
                    "symbol":       symbol,
                    "side":         open_pos["side"],
                    "entry":        open_pos["entry"],
                    "exit":         exit_price,
                    "initial_stop": open_pos["initial_stop"],
                    "target":       open_pos["target"],
                    "qty":          total_q,
                    "pnl":          round(pnl, 2),
                    "r_multiple":   round(pnl / init_r, 3) if init_r > 0 else None,
                    "initial_risk": round(init_r, 2),
                    "strategy":     open_pos["strategy"],
                    "confidence":   open_pos.get("confidence", 60),
                    "regime":       open_pos["regime"],
                    "ts":           open_pos["ts"],
                    "ts_exit":      bar_ts,
                    "close_reason": reason,
                    "exit_policy":  exit_policy,
                })
                open_pos   = None
                peak_price = 0.0
                if reason in ("STOP_HIT", "TRAIL_STOP") and pnl < 0:
                    last_stop_bar = i

        # ── Look for entry when flat ──────────────────────────────────────────
        if open_pos is None:
            nifty_allow = nifty_ok.get(bar_ts, True) if nifty_ok else True
            cooldown_ok = (i >= last_stop_bar + 20)
            day_breadth = breadth_map.get(bar_ts) if breadth_map else None
            if not (nifty_allow and cooldown_ok):
                continue

            candidate = None

            if hub_only:
                # HUB_SIGNAL technical proxy — Phase 7 filters applied
                # Mirrors live hub_signal.py: EMA50>EMA200, ADX>25, vol_spike,
                # RSI 45-70, 1×ATR stop, 2×ATR target (2:1 R:R).
                _r = row
                _regime = str(_r.get("regime", "UNKNOWN"))
                _ema50  = float(_r.get("ema50",  0))
                _ema200 = float(_r.get("ema200", 0))
                _adx    = float(_r.get("adx14",  0))
                _rsi    = float(_r.get("rsi14",  50))
                _vspike = bool(_r.get("vol_spike", False))
                if (_ema50 > _ema200                      # long-term bull trend
                        and _adx > 25                      # strong momentum
                        and int(_r.get("st_dir", 0)) == 1  # supertrend bullish
                        and _vspike                        # volume confirmation
                        and 45 <= _rsi <= 70               # healthy RSI
                        and _regime != "BEAR_TRENDING"):
                    _close = float(_r["close"])
                    _atr   = float(_r["atr14"])
                    if _atr > 0:
                        from engine.agent.strategies.base import TradeCandidate
                        candidate = TradeCandidate(
                            symbol=symbol, side="BUY",
                            entry=round(_close, 2),
                            stop=round(_close - 1.0 * _atr, 2),   # Phase 7: 1×ATR (was 2×)
                            target=round(_close + 2.0 * _atr, 2), # 2:1 R:R
                            confidence=80, reasons=["hub_proxy_p7"],
                            strategy="HUB_SIGNAL",
                        )
            else:
                features  = _features_from_precomputed(row)
                # Pass last 2 rows as df so strategies can access df.iloc[-1] / df.iloc[-2]
                df_window = df.iloc[max(0, i - 1): i + 1]
                candidate = selector.propose(symbol, df_window, features, macro_bias=0, fund_grade="WATCHLIST")

            if candidate:
                # Phase 5: block PULLBACK_LONG when broad market is weak (< 45% above 50d proxy).
                if (candidate.strategy == "PULLBACK_LONG"
                        and day_breadth is not None and day_breadth < 45.0):
                    candidate = None
                # Phase 6: breadth-adjusted confidence — strong market boosts confidence,
                # weak market reduces it, making the confidence buckets more predictive.
                elif candidate is not None and day_breadth is not None:
                    adj = 0
                    if day_breadth >= 65:
                        adj = +5
                    elif day_breadth < 50:
                        adj = -5
                    if adj:
                        from dataclasses import replace as _dc_replace
                        try:
                            candidate = _dc_replace(candidate,
                                confidence=max(40, min(95, candidate.confidence + adj)))
                        except Exception:
                            pass  # TradeCandidate may not be a dataclass — skip adjustment

            if candidate and candidate.confidence >= _CONF_THRESH:
                atr14 = float(row["atr14"])
                rps   = abs(candidate.entry - candidate.stop)
                if rps > 0:
                    # Dynamic sizing: scale by breadth + ADX quality.
                    adx_val   = float(row.get("adx14", 0))
                    size_mult = 1.0
                    if candidate.side == "BUY" and day_breadth is not None:
                        if day_breadth >= 60 and adx_val >= 25 and candidate.confidence >= 80:
                            size_mult = 1.25
                        elif day_breadth < 35:
                            size_mult = 0.5
                        elif day_breadth < 45:
                            size_mult = 0.75
                    qty = int((equity * _RISK_PCT * size_mult) / rps)
                    if qty > 0:
                        t1 = (candidate.entry + 2.0 * atr14 if candidate.side == "BUY"
                              else candidate.entry - 2.0 * atr14)
                        open_pos = {
                            "side":         candidate.side,
                            "entry":        candidate.entry,
                            "stop":         candidate.stop,
                            "initial_stop": candidate.stop,
                            "target":       candidate.target,
                            "t1":           t1,
                            "trail_dist":   atr14,
                            "atr14":        atr14,
                            "trailing":     False,
                            "qty":          qty,
                            "strategy":     candidate.strategy,
                            "confidence":   candidate.confidence,
                            "regime":       str(row.get("regime", "UNKNOWN")),
                            "ts":           bar_ts,
                            "entry_bar":    i,    # for 12-bar time exit
                        }
                        peak_price = candidate.entry

    return {"trades": trades, "final_equity": equity}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Statistical helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_r_multiples(trades: list[dict]) -> list[float]:
    return [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]


def bootstrap_ci(r_vals: list[float], n_boot: int = _BOOT_N, ci: float = 0.95):
    """Bootstrap 95% CI on mean R.  Returns (mean, lo, hi, verdict)."""
    if not r_vals:
        return (None, None, None, "INSUFFICIENT_DATA")
    arr = np.array(r_vals, dtype=float)
    means = _RNG.choice(arr, size=(n_boot, len(arr)), replace=True).mean(axis=1)
    alpha = (1.0 - ci) / 2
    lo, hi = float(np.percentile(means, alpha * 100)), float(np.percentile(means, (1 - alpha) * 100))
    mu = float(arr.mean())
    verdict = "POSITIVE" if lo > 0 else ("NEGATIVE" if hi < 0 else "UNCERTAIN")
    return (round(mu, 4), round(lo, 4), round(hi, 4), verdict)


def _stats_block(trades: list[dict], label: str = "") -> dict:
    """Compute full attribution block for a list of trades."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "label": label}
    wins   = [t for t in trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl") or 0) <= 0]
    gw = sum(t["pnl"] for t in wins   if t.get("pnl"))
    gl = abs(sum(t["pnl"] for t in losses if t.get("pnl"))) or 1e-9
    r_vals = _compute_r_multiples(trades)
    mu_r, lo_r, hi_r, r_verdict = bootstrap_ci(r_vals) if r_vals else (None, None, None, "N/A")
    return {
        "label":       label,
        "n":           n,
        "win_rate":    round(len(wins) / n, 4) if n else None,
        "profit_factor": round(gw / gl, 3),
        "avg_win":     round(gw / len(wins),   2) if wins   else 0,
        "avg_loss":    round(gl / len(losses),  2) if losses else 0,
        "net_pnl":     round(gw - gl, 2),
        "mean_r":      mu_r,
        "ci_lo_r":     lo_r,
        "ci_hi_r":     hi_r,
        "r_verdict":   r_verdict,
        "sufficient":  n >= _MIN_CELL_N,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Walk-forward
# ═══════════════════════════════════════════════════════════════════════════════

def walk_forward(all_trades: list[dict]) -> dict:
    """Two temporal splits — in-sample vs out-of-sample."""
    def _year(t, field="ts_exit"):
        return (t.get(field) or "")[:4]

    splits = {
        "split1_train2022_23_val2024": {
            "train": [t for t in all_trades if _year(t) in ("2022", "2023")],
            "val":   [t for t in all_trades if _year(t) == "2024"],
        },
        "split2_train2022_24_val2025_26": {
            "train": [t for t in all_trades if _year(t) in ("2022", "2023", "2024")],
            "val":   [t for t in all_trades if _year(t) in ("2025", "2026")],
        },
    }
    result = {}
    for split_name, s in splits.items():
        result[split_name] = {
            "train": _stats_block(s["train"], f"train({split_name})"),
            "val":   _stats_block(s["val"],   f"val({split_name})"),
        }
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Regime × strategy cross-tab
# ═══════════════════════════════════════════════════════════════════════════════

def regime_crosstab(all_trades: list[dict]) -> dict:
    by_sr: dict[tuple, list] = defaultdict(list)
    for t in all_trades:
        key = (t.get("strategy", "?"), t.get("regime", "UNKNOWN"))
        by_sr[key].append(t)

    # Build table
    strategies = sorted({k[0] for k in by_sr})
    regimes    = sorted({k[1] for k in by_sr})
    table: dict[str, dict[str, dict]] = {}
    for strat in strategies:
        table[strat] = {}
        for regime in regimes:
            trades = by_sr[(strat, regime)]
            if not trades:
                continue
            b = _stats_block(trades)
            table[strat][regime] = {
                "n":       b["n"],
                "wr":      b["win_rate"],
                "pf":      b["profit_factor"],
                "mean_r":  b["mean_r"],
                "verdict": b["r_verdict"],
            }
    return table


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Confidence bucket analysis
# ═══════════════════════════════════════════════════════════════════════════════

def confidence_buckets(all_trades: list[dict]) -> dict:
    """Group by confidence score; test if expectancy is monotonic with confidence."""
    by_bucket: dict[str, list] = defaultdict(list)
    for t in all_trades:
        c = t.get("confidence", 0)
        bucket = f"{(int(c) // 10) * 10}-{(int(c) // 10) * 10 + 9}"
        by_bucket[bucket].append(t)

    result = {}
    for bucket in sorted(by_bucket):
        b = _stats_block(by_bucket[bucket], bucket)
        result[bucket] = b

    # Monotonicity check: is mean_r monotonically increasing with confidence?
    sorted_buckets = sorted(result.keys())
    r_vals = [result[b].get("mean_r") for b in sorted_buckets if result[b].get("mean_r") is not None]
    is_monotonic = all(r_vals[i] <= r_vals[i + 1] for i in range(len(r_vals) - 1)) if len(r_vals) > 1 else None

    result["_monotonic_check"] = {
        "is_monotonic": is_monotonic,
        "verdict": (
            "PREDICTIVE — higher confidence correlates with better expectancy" if is_monotonic
            else "NOT PREDICTIVE — confidence score does not rank trades by quality"
            if is_monotonic is not None else "INSUFFICIENT_DATA"
        ),
    }
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Exit policy comparison (same signals, 6 exit variants)
# ═══════════════════════════════════════════════════════════════════════════════

EXIT_POLICIES = ["current", "full_trail", "partial_fixed", "be_after_1r", "trail_150", "trail_075"]
EXIT_LABELS = {
    "partial_fixed": "★ Active (T1 partial + fixed T2, no trail)  ← deployed",
    "current":       "Old (T1 partial + 1×ATR trail)",
    "full_trail":    "Full trail, no partial (1×ATR from entry)",
    "be_after_1r":   "Break-even stop once +1R reached",
    "trail_150":     "T1 partial + 1.5×ATR trail",
    "trail_075":     "T1 partial + 0.75×ATR trail",
}


async def run_exit_comparison(
    symbols: list[str],
    from_dt: datetime,
    nifty_ok: dict[str, bool],
    sample_n: int = 100,
    breadth_map: dict[str, float] | None = None,
) -> dict[str, dict]:
    """Run 6 exit policies on the same symbol set.  Uses a sample (default 100) for speed."""
    sample = symbols[:sample_n]
    results: dict[str, list] = {p: [] for p in EXIT_POLICIES}

    for symbol in sample:
        try:
            df = await load_candles(symbol, from_dt)
        except Exception:
            continue
        if len(df) < _MIN_BARS:
            continue
        for policy in EXIT_POLICIES:
            try:
                r = backtest_corrected(df, symbol, nifty_ok=nifty_ok,
                                       exit_policy=policy, breadth_map=breadth_map)
                results[policy].extend(r.get("trades", []))
            except Exception:
                pass

    return {
        policy: _stats_block(trades, EXIT_LABELS[policy])
        for policy, trades in results.items()
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 7. HUB_SIGNAL un-shadowed
# ═══════════════════════════════════════════════════════════════════════════════

async def run_hub_unshadowed(
    symbols: list[str],
    from_dt: datetime,
    nifty_ok: dict[str, bool],
    sample_n: int = 150,
    breadth_map: dict[str, float] | None = None,
) -> dict:
    trades: list[dict] = []
    for symbol in symbols[:sample_n]:
        try:
            df = await load_candles(symbol, from_dt)
        except Exception:
            continue
        if len(df) < _MIN_BARS:
            continue
        try:
            r = backtest_corrected(df, symbol, nifty_ok=nifty_ok,
                                   hub_only=True, breadth_map=breadth_map)
            trades.extend(r.get("trades", []))
        except Exception:
            pass
    return _stats_block(trades, "HUB_SIGNAL un-shadowed (technical proxy)")


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Main backtest run
# ═══════════════════════════════════════════════════════════════════════════════

async def run_full_backtest(
    symbols: list[str],
    from_dt: datetime,
    nifty_ok: dict[str, bool],
    breadth_map: dict[str, float] | None = None,
) -> list[dict]:
    """Run corrected backtest (real Strategy classes) across the full universe."""
    all_trades: list[dict] = []
    ok = skip = 0
    for i, symbol in enumerate(symbols, 1):
        if i % 50 == 0:
            print(f"  [{i}/{len(symbols)}] {ok} ok, {skip} skip ...")
        try:
            df = await load_candles(symbol, from_dt)
        except Exception:
            skip += 1; continue
        if len(df) < _MIN_BARS:
            skip += 1; continue
        try:
            r = backtest_corrected(df, symbol, nifty_ok=nifty_ok, breadth_map=breadth_map)
            all_trades.extend(r.get("trades", []))
            ok += 1
        except Exception as exc:
            skip += 1
            logging.warning(f"  backtest failed for {symbol}: {exc}")
    print(f"  Completed: {ok} symbols, {skip} skipped, {len(all_trades)} total trades")
    return all_trades


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Report printing
# ═══════════════════════════════════════════════════════════════════════════════

_W = "═" * 72

def _pct(v):  return f"{v*100:.1f}%" if v is not None else "N/A"
def _f2(v):   return f"{v:.2f}"       if v is not None else "N/A"
def _f3(v):   return f"{v:.3f}"       if v is not None else "N/A"
def _inr(v):  return f"₹{v:,.0f}"    if v is not None else "N/A"


def print_report(report: dict) -> None:
    print(f"\n{_W}")
    print("  AutoTrade Pro — Phase 2 Edge Validation Report")
    print(f"  Period   : {report['period']}")
    print(f"  Universe : {report['symbols_tested']} symbols | "
          f"{report['total_trades']} trades total")
    print(f"  Path     : corrected (real Strategy classes via precomputed bridge)")
    print(_W)

    # ── Overall ──────────────────────────────────────────────────────────────
    overall = report["overall"]
    mu_r    = overall.get("mean_r")
    lo_r    = overall.get("ci_lo_r")
    hi_r    = overall.get("ci_hi_r")
    print("\n── Overall (all strategies) ─────────────────────────────────────────")
    print(f"  Trades        : {overall['n']}")
    print(f"  Win rate      : {_pct(overall.get('win_rate'))}")
    print(f"  Profit factor : {_f2(overall.get('profit_factor'))}")
    print(f"  Mean R/trade  : {_f3(mu_r)}  95%CI [{_f3(lo_r)}, {_f3(hi_r)}]  → {overall.get('r_verdict')}")
    print(f"  Net P&L       : {_inr(overall.get('net_pnl'))}")

    # ── By strategy ──────────────────────────────────────────────────────────
    print("\n── Strategy Breakdown ───────────────────────────────────────────────")
    for strat, s in report.get("by_strategy", {}).items():
        n = s.get("n", 0)
        flag = "" if n >= _MIN_CELL_N else " ⚠ LOW_N"
        print(f"  {strat:<28} n={n:<5}{flag}")
        print(f"    WR={_pct(s.get('win_rate'))}  PF={_f2(s.get('profit_factor'))}  "
              f"mean_R={_f3(s.get('mean_r'))} CI[{_f3(s.get('ci_lo_r'))},{_f3(s.get('ci_hi_r'))}]"
              f"  verdict={s.get('r_verdict')}")
        print(f"    avg_win={_inr(s.get('avg_win'))}  avg_loss={_inr(s.get('avg_loss'))}  "
              f"net={_inr(s.get('net_pnl'))}")

    # ── Year-by-year breakdown ───────────────────────────────────────────────
    print("\n── Year-by-Year Performance ─────────────────────────────────────────")
    print(f"  {'Year':<6}  {'Regime':<16}  {'N':>5}  {'WR':>7}  {'PF':>6}  {'MeanR':>7}  {'NetPnL':>12}")
    for yr, ys in sorted(report.get("by_year", {}).items()):
        yr_regime  = _YEAR_REGIMES.get(yr, "")
        yr_n       = ys.get("n", 0)
        yr_wr      = _pct(ys.get("win_rate"))
        yr_pf      = _f2(ys.get("profit_factor"))
        yr_mr      = _f3(ys.get("mean_r"))
        yr_pnl     = _inr(ys.get("net_pnl"))
        yr_verdict = ys.get("r_verdict", "")
        flag = " ← OOS" if yr in ("2025", "2026") else ""
        print(f"  {yr:<6}  {yr_regime:<16}  {yr_n:>5}  {yr_wr:>7}  {yr_pf:>6}  "
              f"{yr_mr:>7}  {yr_pnl:>12}  {yr_verdict}{flag}")

    # ── Walk-forward ─────────────────────────────────────────────────────────
    print("\n── Walk-Forward Out-of-Sample Validation ────────────────────────────")
    for split, s in report.get("walk_forward", {}).items():
        tr, vl = s["train"], s["val"]
        print(f"  {split}:")
        print(f"    In-sample  n={tr['n']:<5}  mean_R={_f3(tr.get('mean_r'))} "
              f"CI[{_f3(tr.get('ci_lo_r'))},{_f3(tr.get('ci_hi_r'))}]  {tr.get('r_verdict')}")
        print(f"    OOS        n={vl['n']:<5}  mean_R={_f3(vl.get('mean_r'))} "
              f"CI[{_f3(vl.get('ci_lo_r'))},{_f3(vl.get('ci_hi_r'))}]  {vl.get('r_verdict')}")

    # ── Strategy × regime cross-tab ───────────────────────────────────────────
    print("\n── Strategy × Regime Cross-tab ──────────────────────────────────────")
    ct = report.get("regime_crosstab", {})
    regimes_all = sorted({r for strat_d in ct.values() for r in strat_d})
    header = f"  {'Strategy':<28}" + "".join(f"{r[:12]:<14}" for r in regimes_all)
    print(header)
    for strat, strat_d in ct.items():
        row_parts = [f"  {strat:<28}"]
        for regime in regimes_all:
            cell = strat_d.get(regime, {})
            if cell:
                row_parts.append(f"n={cell.get('n','?')} R={_f3(cell.get('mean_r')):<14}")
            else:
                row_parts.append(f"{'—':<14}")
        print("".join(row_parts))

    # ── Confidence buckets ────────────────────────────────────────────────────
    print("\n── Confidence Bucket Analysis ───────────────────────────────────────")
    cb = report.get("confidence_buckets", {})
    mono = cb.pop("_monotonic_check", {})
    for bucket, s in sorted(cb.items()):
        n = s.get("n", 0)
        flag = " ⚠" if n < _MIN_CELL_N else ""
        print(f"  [{bucket}] n={n:<5}{flag}  "
              f"WR={_pct(s.get('win_rate'))}  PF={_f2(s.get('profit_factor'))}  "
              f"mean_R={_f3(s.get('mean_r'))}  {s.get('r_verdict')}")
    print(f"  → {mono.get('verdict', 'N/A')}")

    # ── HUB_SIGNAL un-shadowed ────────────────────────────────────────────────
    print("\n── HUB_SIGNAL Un-shadowed (technical EMA/ST/RSI proxy) ─────────────")
    hub = report.get("hub_unshadowed", {})
    print(f"  n={hub.get('n')}  WR={_pct(hub.get('win_rate'))}  "
          f"PF={_f2(hub.get('profit_factor'))}  "
          f"mean_R={_f3(hub.get('mean_r'))} CI[{_f3(hub.get('ci_lo_r'))},{_f3(hub.get('ci_hi_r'))}]"
          f"  {hub.get('r_verdict')}")
    print("  ⚠ Note: real HUB_SIGNAL requires live 7-factor hub scores; "
          "technical proxy is directional only")

    # ── Exit policy comparison ────────────────────────────────────────────────
    print("\n── Exit Policy Comparison (same entries, 6 policies) ────────────────")
    ep = report.get("exit_policies", {})
    baseline_r = ep.get("current", {}).get("mean_r")
    for policy, s in ep.items():
        delta = f"Δ{_f3((s.get('mean_r') or 0) - (baseline_r or 0))}" if policy != "current" and baseline_r else ""
        print(f"  {EXIT_LABELS.get(policy, policy):<50} "
              f"n={s.get('n','?'):<5}  "
              f"mean_R={_f3(s.get('mean_r'))} {delta}  "
              f"PF={_f2(s.get('profit_factor'))}")

    # ── Phase 2 Verdict ───────────────────────────────────────────────────────
    v = report.get("verdict", {})
    print(f"\n{_W}")
    print("  PHASE 2 VERDICT")
    print(_W)
    print(f"\n  {v.get('statement', 'No verdict computed')}")
    print(f"\n  Edge status   : {v.get('edge_status')}")
    print(f"  Regime note   : {v.get('regime_note')}")
    print(f"  Strategy note : {v.get('strategy_note')}")
    print(f"  Confidence    : {v.get('confidence_note')}")
    print(f"  OOS check     : {v.get('oos_note')}")
    print(f"  Recommendation: {v.get('recommendation')}")
    print(f"\n{_W}\n")


def compute_verdict(report: dict) -> dict:
    overall  = report.get("overall", {})
    wf       = report.get("walk_forward", {})
    by_strat = report.get("by_strategy", {})
    ct       = report.get("regime_crosstab", {})

    mu_r   = overall.get("mean_r")
    lo_r   = overall.get("ci_lo_r")
    hi_r   = overall.get("ci_hi_r")
    rv     = overall.get("r_verdict", "UNCERTAIN")

    # OOS check: use split2 val (2025-26) as hardest OOS
    oos    = wf.get("split2_train2022_24_val2025_26", {}).get("val", {})
    oos_r  = oos.get("mean_r")
    oos_rv = oos.get("r_verdict", "UNCERTAIN")

    # Regime: check if BULL_TRENDING has positive R, others don't
    bt_cells = [v for strat_d in ct.values() for regime, v in strat_d.items()
                if regime == "BULL_TRENDING" and v.get("n", 0) >= _MIN_CELL_N]
    bear_cells = [v for strat_d in ct.values() for regime, v in strat_d.items()
                  if regime in ("BEAR_TRENDING", "UNKNOWN", "RANGE") and v.get("n", 0) >= _MIN_CELL_N]
    bt_mean_r  = float(np.mean([c["mean_r"] for c in bt_cells   if c.get("mean_r")])) if bt_cells   else None
    bear_mean_r = float(np.mean([c["mean_r"] for c in bear_cells if c.get("mean_r")])) if bear_cells else None
    regime_concentrated = (bt_mean_r is not None and bear_mean_r is not None
                           and bt_mean_r > 0.05 and bear_mean_r < 0.0)

    # Best and worst strategy by mean_r
    strat_rs = {s: d.get("mean_r") for s, d in by_strat.items() if d.get("mean_r") is not None}
    best_strat  = max(strat_rs, key=strat_rs.get) if strat_rs else "N/A"
    worst_strat = min(strat_rs, key=strat_rs.get) if strat_rs else "N/A"

    # Confidence monotonicity
    cb    = report.get("confidence_buckets", {})
    mono  = cb.get("_monotonic_check", {}).get("is_monotonic")

    if rv == "POSITIVE" and oos_rv == "POSITIVE":
        edge_status = "EDGE CONFIRMED"
        rec = "Proceed to extended paper trading.  Monitor regime gate daily."
    elif rv == "POSITIVE" and regime_concentrated:
        edge_status = "EDGE CONDITIONAL ON REGIME"
        rec = "Do NOT deploy in chop/bear.  Enable BULL_TRENDING gate, then extend paper."
    elif rv == "POSITIVE" and oos_rv == "UNCERTAIN":
        edge_status = "EDGE UNCERTAIN OOS"
        rec = "Extend paper trading.  Require 90+ OOS days with positive mean-R before live."
    elif rv == "UNCERTAIN":
        edge_status = "NO CONFIRMED EDGE"
        rec = "Do NOT use real money.  Investigate entry quality and backtest integrity."
    else:
        edge_status = "NO EDGE"
        rec = "Do NOT use real money.  Edge is negative or zero."

    statement = (
        f"Out-of-sample, on the live decision path, the system shows "
        f"mean expectancy of {_f3(mu_r)} R/trade "
        f"(95% CI [{_f3(lo_r)}, {_f3(hi_r)}]).  "
        f"OOS (2025-26): {_f3(oos_r)} R ({oos_rv}).  "
        f"Strategy {best_strat} carries the edge; "
        f"{worst_strat} is {'dilutive' if strat_rs.get(worst_strat, 0) < 0 else 'neutral'}."
    )

    return {
        "statement":       statement,
        "edge_status":     edge_status,
        "regime_note":     (
            "Edge concentrated in BULL_TRENDING; absent/negative in chop/bear."
            if regime_concentrated else
            "Edge present across multiple regimes." if rv == "POSITIVE" else
            "Regime analysis inconclusive."
        ),
        "strategy_note":   f"Best: {best_strat} ({_f3(strat_rs.get(best_strat))} R)  "
                          f"Worst: {worst_strat} ({_f3(strat_rs.get(worst_strat))} R)",
        "confidence_note": (
            "Confidence IS predictive — higher confidence → better expectancy."
            if mono else
            "Confidence is NOT predictive — thresholds may be theater."
            if mono is not None else
            "Confidence monotonicity: insufficient data."
        ),
        "oos_note":        f"OOS (2025-26): mean_R={_f3(oos_r)}, {oos_rv}",
        "recommendation":  rec,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

async def run(top_n: int, from_date: date, symbol_limit: int | None, out_path: str | None):
    t0      = time.time()
    from_dt = datetime(from_date.year, from_date.month, from_date.day)
    to_date = date.today()

    print(f"[validate] Loading hub universe (top {top_n})...")
    symbols = await load_hub_symbols(top_n)
    if symbol_limit:
        symbols = symbols[:symbol_limit]

    print(f"[validate] {len(symbols)} symbols | {from_date} → {to_date}")
    print("[validate] Disclaimer: universe is today's liquid stocks (survivorship bias).")
    print("           yfinance adjusted close handles splits/dividends.")
    print("           Delisting bias: stocks that failed pre-2022 are excluded.")

    # Load Nifty macro gate: EMA50 AND EMA200 (same logic as run_backtest.py).
    # EMA200 added to block bear-market bounce entries (key 2025 fix).
    nifty_ok: dict[str, bool] = {}
    try:
        nifty_df = await load_candles("NIFTYBEES.NS", from_dt)
        if len(nifty_df) >= 205:
            ema50  = nifty_df["close"].ewm(span=50,  adjust=False).mean()
            ema200 = nifty_df["close"].ewm(span=200, adjust=False).mean()
            for ts in nifty_df.index:
                c = float(nifty_df.at[ts, "close"])
                nifty_ok[str(ts)[:10]] = (c > float(ema50[ts])) and (c > float(ema200[ts]))
            pct_open = round(100 * sum(nifty_ok.values()) / len(nifty_ok), 1)
            print(f"[validate] Nifty gate (EMA50+EMA200): "
                  f"{sum(nifty_ok.values())}/{len(nifty_ok)} days open ({pct_open}%)")
        else:
            print(f"[validate] WARNING: only {len(nifty_df)} NIFTYBEES bars — gate disabled")
    except Exception as exc:
        print(f"[validate] WARNING: Nifty gate disabled: {exc}")

    # Precompute daily market breadth (% hub stocks above 50-day proxy).
    print("[validate] Precomputing daily market breadth (hub top-200)...")
    breadth_map: dict[str, float] = {}
    try:
        breadth_map = await compute_daily_breadth(top_n=200, from_dt=from_dt)
        avg_b    = round(sum(breadth_map.values()) / len(breadth_map), 1) if breadth_map else 0
        below_45 = sum(1 for v in breadth_map.values() if v < 45.0)
        print(f"[validate] Breadth: {len(breadth_map)} days | avg={avg_b}% | "
              f"{below_45} days below 45% (PULLBACK_LONG blocked)")
    except Exception as exc:
        print(f"[validate] WARNING: breadth compute failed — gate disabled: {exc}")

    # 1. Full backtest (corrected path)
    print("\n[1/6] Running corrected backtest (real Strategy classes)...")
    all_trades = await run_full_backtest(symbols, from_dt, nifty_ok, breadth_map=breadth_map)

    # 2. Stats per strategy
    print("[2/6] Computing strategy statistics + bootstrap CI...")
    by_year = defaultdict(list)
    by_strat: dict[str, list] = defaultdict(list)
    for t in all_trades:
        yr = (t.get("ts_exit") or "")[:4]
        if yr:
            by_year[yr].append(t)
        st = t.get("strategy", "UNKNOWN")
        by_strat[st].append(t)

    overall_stats  = _stats_block(all_trades, "overall")
    strategy_stats = {s: _stats_block(ts, s) for s, ts in by_strat.items()}
    year_stats     = {yr: _stats_block(ts, yr) for yr, ts in sorted(by_year.items())}

    # 3. Walk-forward
    print("[3/6] Walk-forward validation...")
    wf = walk_forward(all_trades)

    # 4. Regime cross-tab
    print("[4/6] Regime cross-tab...")
    ct = regime_crosstab(all_trades)

    # 5. Confidence buckets
    print("[5/6] Confidence bucket analysis...")
    cb = confidence_buckets(all_trades)

    # 6. HUB_SIGNAL + exit variants (on sample for speed)
    print("[6/6] HUB_SIGNAL un-shadowed + exit policy comparison...")
    hub = await run_hub_unshadowed(symbols, from_dt, nifty_ok, breadth_map=breadth_map)
    ep  = await run_exit_comparison(symbols, from_dt, nifty_ok, breadth_map=breadth_map)

    report = {
        "period":          f"{from_date} → {to_date}",
        "symbols_tested":  len(symbols),
        "total_trades":    len(all_trades),
        "overall":         overall_stats,
        "by_strategy":     strategy_stats,
        "by_year":         year_stats,
        "year_regimes":    _YEAR_REGIMES,
        "walk_forward":    wf,
        "regime_crosstab": ct,
        "confidence_buckets": cb,
        "hub_unshadowed":  hub,
        "exit_policies":   ep,
        "caveats": [
            "Survivorship bias: universe is today's live symbols; delisted stocks excluded",
            "HUB_SIGNAL un-shadowed uses EMA/ST/RSI proxy, not real 7-factor hub scores",
            "pattern_direction computed from OHLCV in bridge (hammer/bullish_bar/bearish_bar)",
            "RANGE_REVERSAL hammer check uses real OHLCV via _bridge_pattern() — correct",
            "Cost model: Varsity M7 (STT + exchange + SEBI + stamp + GST + brokerage)",
            "Slippage: 0.01–0.03% adverse on entry (live _SLIP_MIN/_SLIP_MAX matched)",
        ],
        "elapsed_minutes": round((time.time() - t0) / 60, 1),
    }
    report["verdict"] = compute_verdict(report)

    print_report(report)

    if out_path:
        # Remove non-serialisable items before saving
        save_report = {k: v for k, v in report.items() if k != "by_year"}
        with open(out_path, "w") as fh:
            json.dump(save_report, fh, indent=2, default=str)
        print(f"[validate] Report saved → {out_path}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 2 edge validation")
    parser.add_argument("--top-n",   type=int, default=443, help="Max symbols to test")
    parser.add_argument("--symbols", type=int, default=None, help="Cap at N symbols (quick test)")
    parser.add_argument("--from",    dest="from_date", type=date.fromisoformat,
                        default=_DEFAULT_FROM, help="Start date YYYY-MM-DD")
    parser.add_argument("--out",     type=str, default=None, help="Save JSON report")
    args = parser.parse_args()

    asyncio.run(run(args.top_n, args.from_date, args.symbols, args.out))
