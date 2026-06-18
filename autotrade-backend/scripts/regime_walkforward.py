"""
Regime segmentation + walk-forward (fixed-parameter OOS stability) + top trades.

Reads a run_backtest.py JSON (all_trades) and reports:
  1. Per-regime metrics using the audit's calendar windows (segmented by ENTRY date).
  2. Walk-forward / per-test-year metrics (Sharpe, PF, MaxDD, WR) on the real Rs20L
     equity curve. NOTE: run_backtest.py uses FIXED strategy thresholds, so no
     parameter re-optimization happens per window — this is an out-of-sample
     *stability* test across years, which is the honest reading given that the
     thresholds were hand-tuned on the 2022-2026 set.
  3. Largest wins / losses with full attribution.

Usage: .venv/bin/python -m scripts.regime_walkforward results/bt_revalidate.json
"""
import json
import sys
from collections import defaultdict
from datetime import date

import numpy as np
import pandas as pd

WALLET = 2_000_000.0
RF_ANNUAL = 0.065

# Audit regime windows, matched on ENTRY date (inclusive).
REGIMES = {
    "Bull 2016-2018":        (date(2016, 1, 1), date(2018, 12, 31)),
    "Bull 2020-2021":        (date(2020, 1, 1), date(2021, 12, 31)),
    "Bear H1-2022":          (date(2022, 1, 1), date(2022, 6, 30)),
    "Sideways 2018-2019":    (date(2018, 1, 1), date(2019, 12, 31)),
    "Sideways 2023-2024":    (date(2023, 1, 1), date(2024, 12, 31)),
    "COVID crash Jan-Mar20":  (date(2020, 1, 1), date(2020, 3, 31)),
    "Russia-Ukraine Feb-Mar22": (date(2022, 2, 1), date(2022, 3, 31)),
    "Post-COVID Apr-Dec20":  (date(2020, 4, 1), date(2020, 12, 31)),
}


def block(trades):
    if not trades:
        return {"n": 0}
    pnl = np.array([t["pnl"] for t in trades], float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gp = wins.sum()
    gl = abs(losses.sum()) or 1e-9
    return {
        "n": len(trades),
        "win%": round(100 * len(wins) / len(trades), 1),
        "PF": round(gp / gl, 2),
        "avg_win": round(wins.mean(), 0) if len(wins) else 0,
        "avg_loss": round(abs(losses.mean()), 0) if len(losses) else 0,
        "RR": round(wins.mean() / abs(losses.mean()), 2) if len(wins) and len(losses) else 0,
        "expectancy": round(pnl.mean(), 0),
        "net_pnl": round(pnl.sum(), 0),
    }


def curve_metrics(trades):
    """Sharpe / MaxDD on the Rs20L single-account equity curve for a trade subset."""
    if len(trades) < 2:
        return {"sharpe": 0.0, "max_dd_pct": 0.0}
    by_day = defaultdict(float)
    for t in trades:
        by_day[date.fromisoformat(t["ts_exit"][:10])] += t["pnl"]
    s = pd.Series(by_day).sort_index()
    equity = WALLET + s.cumsum()
    peak = equity.cummax()
    dd = ((equity - peak) / peak).min()
    rets = equity.pct_change().dropna()
    rf_daily = RF_ANNUAL / 252
    sharpe = np.sqrt(252) * (rets.mean() - rf_daily) / (rets.std() + 1e-12)
    return {"sharpe": round(float(sharpe), 2), "max_dd_pct": round(100 * float(dd), 1)}


def main(path):
    d = json.load(open(path))
    trades = d["all_trades"]
    for t in trades:
        t["d_in"] = date.fromisoformat(t["ts"][:10])
        t["d_out"] = date.fromisoformat(t["ts_exit"][:10])

    print("=" * 90)
    print(f"REGIME + WALK-FORWARD  |  {path}  |  {len(trades)} trades  "
          f"({d['from_date']} -> {d['to_date']})")
    print("=" * 90)

    print("\n[PER-REGIME — segmented by ENTRY date]")
    print(f"  {'regime':28} {'N':>5} {'win%':>6} {'PF':>6} {'RR':>5} "
          f"{'avgWin':>9} {'avgLoss':>9} {'netPnL':>12}")
    for name, (lo, hi) in REGIMES.items():
        sub = [t for t in trades if lo <= t["d_in"] <= hi]
        b = block(sub)
        if b["n"] == 0:
            print(f"  {name:28} {'--- no trades in window ---':>50}")
            continue
        print(f"  {name:28} {b['n']:>5} {b['win%']:>6} {b['PF']:>6} {b['RR']:>5} "
              f"{b['avg_win']:>9,.0f} {b['avg_loss']:>9,.0f} {b['net_pnl']:>12,.0f}")

    print("\n[WALK-FORWARD — per test YEAR, fixed params (OOS stability)]")
    print(f"  {'year':5} {'N':>5} {'win%':>6} {'PF':>6} {'sharpe':>7} {'maxDD%':>7} {'netPnL':>12}")
    by_year = defaultdict(list)
    for t in trades:
        by_year[t["d_out"].year].append(t)
    sharpes = []
    for y in sorted(by_year):
        sub = by_year[y]
        b = block(sub)
        cm = curve_metrics(sub)
        sharpes.append(cm["sharpe"])
        print(f"  {y:5} {b['n']:>5} {b['win%']:>6} {b['PF']:>6} {cm['sharpe']:>7} "
              f"{cm['max_dd_pct']:>7} {b['net_pnl']:>12,.0f}")
    print(f"\n  Avg per-year Sharpe: {np.mean(sharpes):.2f}   "
          f"Years PF>=1.5: {sum(1 for y in by_year if block(by_year[y])['PF']>=1.5)}/{len(by_year)}")

    print("\n[TOP 10 WINNERS]")
    for t in sorted(trades, key=lambda x: -x["pnl"])[:10]:
        print(f"  +{t['pnl']:>10,.0f}  {t['symbol']:14} {t['strategy']:20} "
              f"{t['regime']:14} {t['ts']}->{t['ts_exit']} {t['close_reason']}")
    print("\n[TOP 10 LOSERS]")
    for t in sorted(trades, key=lambda x: x["pnl"])[:10]:
        print(f"  {t['pnl']:>11,.0f}  {t['symbol']:14} {t['strategy']:20} "
              f"{t['regime']:14} {t['ts']}->{t['ts_exit']} {t['close_reason']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results/bt_revalidate.json")
