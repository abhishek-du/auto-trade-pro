"""Step 1: Diagnose 2025-26 regime mislabelling.

Compares:
  - Backtest precompute() regime (ADX > 20 threshold) — what generated trades
  - Live analyzer regime (ADX >= 25 threshold) — what would actually fire live

Reports ADX distribution at all signal bars, misclassification rate, and
whether the ADX 20-25 "weak trend" zone is responsible for OOS losses.

Usage:
  .venv/bin/python scripts/diagnose_regime.py --phase2 results/phase2.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.run_backtest import precompute, load_candles, _MIN_BARS


async def load_trades_with_indicators(
    trades: list[dict],
    year_filter: str,
) -> list[dict]:
    """Re-load candles for each trade in year_filter and attach indicator values at entry."""
    year_trades = [t for t in trades if (t.get("ts") or "")[:4] == year_filter]
    print(f"  {len(year_trades)} trades in {year_filter}")

    # Group by symbol to avoid redundant downloads
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for t in year_trades:
        by_symbol[t["symbol"]].append(t)

    enriched = []
    for symbol, sym_trades in by_symbol.items():
        try:
            from_dt = datetime(int(year_filter) - 1, 1, 1)  # load one year of warmup
            df = await load_candles(symbol, from_dt)
            if len(df) < _MIN_BARS:
                continue
            f_df = precompute(df)
        except Exception:
            continue

        for trade in sym_trades:
            entry_date = (trade.get("ts") or "")[:10]
            # Find the bar at or just before entry_date
            try:
                mask = f_df.index.strftime("%Y-%m-%d") <= entry_date
                if not mask.any():
                    continue
                row = f_df[mask].iloc[-1]

                # Compute what the LIVE analyzer would classify
                ema20   = float(row["ema20"])
                ema50   = float(row["ema50"])
                ema200  = float(row["ema200"])
                adx14   = float(row["adx14"])
                plus_di = float(row.get("plus_di", 0))
                minus_di = float(row.get("minus_di", 0))
                close   = float(row["close"])

                # Live analyzer logic (from engine/agent/analyzer.py _classify_regime)
                trending_live = adx14 >= 25
                bull_live     = (close > ema50 > ema200) and (plus_di > minus_di)
                if trending_live and bull_live:
                    live_regime = "BULL_TRENDING"
                elif trending_live and (close < ema50 < ema200) and (minus_di > plus_di):
                    live_regime = "BEAR_TRENDING"
                else:
                    live_regime = "RANGE_OR_UNKNOWN"

                enriched.append({
                    **trade,
                    "adx14_at_entry":       round(adx14, 2),
                    "ema20_at_entry":        round(ema20, 2),
                    "ema50_at_entry":        round(ema50, 2),
                    "close_at_entry":        round(close, 2),
                    "backtest_regime":       str(row.get("regime", "?")),
                    "live_regime":           live_regime,
                    "misclassified":         row.get("regime") == "BULL_TRENDING" and live_regime != "BULL_TRENDING",
                })
            except Exception:
                continue

    return enriched


def analyse_regime_quality(enriched: list[dict], year: str) -> dict:
    if not enriched:
        return {"year": year, "n": 0}

    adx_vals     = [t["adx14_at_entry"] for t in enriched]
    mislabelled  = [t for t in enriched if t.get("misclassified")]
    correct_bull = [t for t in enriched if not t.get("misclassified")]

    # ADX buckets
    weak_zone    = [t for t in enriched if 20 <= t["adx14_at_entry"] < 25]  # mislabelled zone
    strong_zone  = [t for t in enriched if t["adx14_at_entry"] >= 25]
    below_20     = [t for t in enriched if t["adx14_at_entry"] < 20]

    # R-multiple by ADX zone
    def _mean_r(trades):
        rs = [t.get("r_multiple") for t in trades if t.get("r_multiple") is not None]
        return round(float(np.mean(rs)), 4) if rs else None

    def _wr(trades):
        if not trades: return None
        w = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        return round(w / len(trades), 4)

    result = {
        "year": year,
        "n": len(enriched),
        "adx_at_entry": {
            "mean":   round(float(np.mean(adx_vals)), 2),
            "median": round(float(np.median(adx_vals)), 2),
            "p25":    round(float(np.percentile(adx_vals, 25)), 2),
            "p75":    round(float(np.percentile(adx_vals, 75)), 2),
            "min":    round(min(adx_vals), 2),
            "max":    round(max(adx_vals), 2),
        },
        "adx_zones": {
            "below_20_n":    len(below_20),
            "weak_20_25_n":  len(weak_zone),
            "strong_25plus_n": len(strong_zone),
            "weak_pct":      round(len(weak_zone) / len(enriched) * 100, 1),
        },
        "misclassification": {
            "n":    len(mislabelled),
            "pct":  round(len(mislabelled) / len(enriched) * 100, 1),
        },
        "mean_r_by_zone": {
            "weak_20_25":   _mean_r(weak_zone),
            "strong_25plus": _mean_r(strong_zone),
            "all":          _mean_r(enriched),
        },
        "wr_by_zone": {
            "weak_20_25":   _wr(weak_zone),
            "strong_25plus": _wr(strong_zone),
        },
    }
    return result


def print_diagnosis(results: list[dict]) -> None:
    print("\n" + "═" * 72)
    print("  Regime Mislabelling Diagnosis")
    print("═" * 72)
    print("\n  KEY: backtest uses ADX > 20; live analyzer uses ADX >= 25")
    print("  Bars with 20 < ADX < 25 are BULL_TRENDING in backtest but")
    print("  RANGE/UNKNOWN in live — these are phantom trades.\n")

    for r in results:
        if r.get("n", 0) == 0:
            print(f"  {r['year']}: no data")
            continue
        print(f"  ── {r['year']} ─────────────────────────────────────────────────")
        print(f"  N = {r['n']}")
        adx = r["adx_at_entry"]
        print(f"  ADX at entry: mean={adx['mean']}  median={adx['median']}  "
              f"p25={adx['p25']}  p75={adx['p75']}")
        zones = r["adx_zones"]
        print(f"  Zone breakdown:")
        print(f"    ADX < 20  (no trade even in backtest): {zones['below_20_n']}")
        print(f"    ADX 20-25 (phantom zone, backtest=BULL, live=RANGE): "
              f"{zones['weak_20_25_n']}  ({zones['weak_pct']}% of trades)")
        print(f"    ADX >= 25 (both agree = BULL_TRENDING):  {zones['strong_25plus_n']}")
        mis = r["misclassification"]
        print(f"  Misclassification rate: {mis['pct']}%  ({mis['n']} trades live would reject)")
        mr = r["mean_r_by_zone"]
        wr = r["wr_by_zone"]
        print(f"  Mean R by zone:")
        print(f"    Phantom (ADX 20-25):   mean_R={mr['weak_20_25']}  WR={wr['weak_20_25']}")
        print(f"    Genuine (ADX >= 25):   mean_R={mr['strong_25plus']}  WR={wr['strong_25plus']}")
        print(f"    All:                   mean_R={mr['all']}\n")

    # Verdict
    all_phantom_r = [r["mean_r_by_zone"]["weak_20_25"] for r in results
                     if r.get("mean_r_by_zone", {}).get("weak_20_25") is not None]
    all_genuine_r = [r["mean_r_by_zone"]["strong_25plus"] for r in results
                     if r.get("mean_r_by_zone", {}).get("strong_25plus") is not None]

    print("  ── Verdict ──────────────────────────────────────────────────────")
    if all_phantom_r and all_genuine_r:
        avg_ph = float(np.mean(all_phantom_r))
        avg_gn = float(np.mean(all_genuine_r))
        if avg_ph < avg_gn - 0.05:
            print(f"  MISLABELLING CONFIRMED: phantom zone mean_R={avg_ph:.3f} vs "
                  f"genuine zone mean_R={avg_gn:.3f}")
            print(f"  → Fix: change precompute() threshold from ADX > 20 to ADX >= 25")
            print(f"  → Expected improvement: eliminate {avg_ph:.3f}R-per-trade drag from phantom trades")
        elif avg_ph > avg_gn:
            print(f"  MISLABELLING NOT THE CAUSE: phantom zone (mean_R={avg_ph:.3f}) "
                  f"outperforms genuine zone (mean_R={avg_gn:.3f})")
            print(f"  → The weak-trend entries are NOT the drag — investigate entry conditions instead")
        else:
            print(f"  AMBIGUOUS: phantom mean_R={avg_ph:.3f} vs genuine mean_R={avg_gn:.3f}")
            print(f"  → Fixing ADX threshold is still correct (match live system); impact unclear")
    else:
        print("  INSUFFICIENT DATA for verdict — run on larger universe")
    print("═" * 72 + "\n")


async def run(phase2_path: str, years: list[str]) -> None:
    with open(phase2_path) as fh:
        p2 = json.load(fh)

    # Phase 2 doesn't save individual trades (too large) — re-derive from per-year stats
    # Instead we need the raw trades. Check if they're in the JSON.
    all_trades = p2.get("_trades")  # not saved by default

    if not all_trades:
        print("[diagnose] Phase 2 JSON does not contain raw trades (expected — they're too large).")
        print("[diagnose] Re-running a targeted backtest for diagnosis years only...")
        from scripts.run_backtest import load_hub_symbols
        from scripts.validate_edge import backtest_corrected

        symbols = await load_hub_symbols(443)
        from_dt = datetime(2024, 1, 1)

        nifty_ok: dict[str, bool] = {}
        try:
            from scripts.run_backtest import load_candles as lc
            nifty_df = await lc("NIFTYBEES.NS", from_dt)
            if len(nifty_df) >= 55:
                ema50 = nifty_df["close"].ewm(span=50, adjust=False).mean()
                for ts, v in (nifty_df["close"] > ema50).items():
                    nifty_ok[str(ts)[:10]] = bool(v)
        except Exception:
            pass

        all_trades = []
        print(f"[diagnose] Backtesting {len(symbols)} symbols from 2024-01-01 ...")
        ok = 0
        for i, symbol in enumerate(symbols, 1):
            if i % 100 == 0:
                print(f"  [{i}/{len(symbols)}] {ok} ok ...")
            try:
                df = await load_candles(symbol, from_dt)
                if len(df) < _MIN_BARS:
                    continue
                r = backtest_corrected(df, symbol, nifty_ok=nifty_ok)
                all_trades.extend(r.get("trades", []))
                ok += 1
            except Exception:
                pass
        print(f"[diagnose] {len(all_trades)} trades collected")

    results = []
    for year in years:
        print(f"\n[diagnose] Enriching {year} trades with indicator snapshots...")
        enriched = await load_trades_with_indicators(all_trades, year)
        result   = analyse_regime_quality(enriched, year)
        results.append(result)

    print_diagnosis(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Regime mislabelling diagnosis")
    parser.add_argument("--phase2", default="results/phase2.json")
    parser.add_argument("--years",  nargs="+", default=["2024", "2025", "2026"])
    args = parser.parse_args()
    asyncio.run(run(args.phase2, args.years))
