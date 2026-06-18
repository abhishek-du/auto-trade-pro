"""
Phase 1 — 10-year historical backtest metric battery (rigorous recompute).

Reads trade-level records and computes the full required metric suite the way a
risk analyst would, AVOIDING the known-broken stored max_drawdown (which divided
by 5x equity). Drawdown is computed on a single-account equity curve seeded with
the real wallet (Rs 20L) so the <20% gate is decision-relevant.

Usage: .venv/bin/python -m scripts.phase1_metrics results/bt_final.json
"""
import json
import sys
from collections import defaultdict
from datetime import date

import numpy as np
import pandas as pd

WALLET = 2_000_000.0      # real wallet (project memory: Rs 20L)
RF_ANNUAL = 0.065         # Indian risk-free ~6.5% (T-bill)
ROUND_TRIP_COST = 0.0024  # 0.24% round trip per user spec


def regime_of_year(y: int) -> str:
    return {2022: "bear/correction", 2023: "bull_trend", 2024: "mixed",
            2025: "chop/decline", 2026: "chop/decline"}.get(y, "?")


def block(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    pnl = np.array([t["pnl"] for t in trades], float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gp = wins.sum()
    gl = abs(losses.sum()) or 1e-9
    return {
        "n": len(trades),
        "win_rate_pct": round(100 * len(wins) / len(trades), 2),
        "avg_win": round(wins.mean(), 0) if len(wins) else 0,
        "avg_loss": round(abs(losses.mean()), 0) if len(losses) else 0,
        "rr": round(wins.mean() / abs(losses.mean()), 2) if len(wins) and len(losses) else 0,
        "profit_factor": round(gp / gl, 3),
        "net_pnl": round(pnl.sum(), 0),
        "expectancy_per_trade": round(pnl.mean(), 0),
    }


def main(path: str):
    d = json.load(open(path))
    trades = d["all_trades"]
    for t in trades:
        t["d_in"] = date.fromisoformat(t["ts"][:10])
        t["d_out"] = date.fromisoformat(t["ts_exit"][:10])
        t["hold_days"] = (t["d_out"] - t["d_in"]).days
    trades.sort(key=lambda t: t["d_out"])
    from_d, to_d = trades[0]["d_in"], trades[-1]["d_out"]
    years = (to_d - from_d).days / 365.25

    print("=" * 74)
    print(f"PHASE 1 METRIC BATTERY  |  source={path}")
    print(f"Window: {from_d} -> {to_d}  ({years:.2f} yr)   Trades: {len(trades)}")
    print(f"Capital base for DD/return: Rs {WALLET:,.0f} (real wallet)")
    print("=" * 74)

    # ---- PROFITABILITY ----
    ov = block(trades)
    net = ov["net_pnl"]
    roi = 100 * net / WALLET
    cagr = 100 * ((WALLET + net) / WALLET) ** (1 / years) - 100
    print("\n[PROFITABILITY]")
    print(f"  Net Profit          : Rs {net:,.0f}   ({roi:+.1f}% on wallet, CAGR {cagr:+.1f}%)")
    print(f"  Profit Factor       : {ov['profit_factor']}")
    print(f"  Expectancy / trade  : Rs {ov['expectancy_per_trade']:,.0f}")
    print(f"  Avg Win / Avg Loss  : Rs {ov['avg_win']:,.0f} / Rs {ov['avg_loss']:,.0f}  (R:R {ov['rr']})")

    # ---- EQUITY CURVE (single account, realized pnl by exit date) ----
    by_day = defaultdict(float)
    for t in trades:
        by_day[t["d_out"]] += t["pnl"]
    s = pd.Series(by_day).sort_index()
    equity = WALLET + s.cumsum()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = dd.min()
    # drawdown duration (longest stretch below a prior peak)
    underwater = equity < peak
    dur, mx = 0, 0
    for u in underwater:
        dur = dur + 1 if u else 0
        mx = max(mx, dur)
    # daily returns for Sharpe/Sortino on the equity curve
    rets = equity.pct_change().dropna()
    ann = np.sqrt(252)
    rf_daily = RF_ANNUAL / 252
    sharpe = ann * (rets.mean() - rf_daily) / (rets.std() + 1e-12)
    downside = rets[rets < 0]
    sortino = ann * (rets.mean() - rf_daily) / (downside.std() + 1e-12)
    calmar = (cagr / 100) / abs(max_dd) if max_dd else 0

    print("\n[RISK]")
    print(f"  Sharpe (ann, rf={RF_ANNUAL:.1%}) : {sharpe:.2f}")
    print(f"  Sortino (ann)        : {sortino:.2f}")
    print(f"  Max Drawdown         : {100*max_dd:.1f}%  (on Rs 20L equity curve)")
    print(f"  Max DD Duration      : {mx} trading days underwater")
    print(f"  Calmar (CAGR/MaxDD)  : {calmar:.2f}")

    # ---- CONSISTENCY: monthly distribution ----
    m = s.copy()
    m.index = pd.to_datetime(m.index)
    monthly = m.resample("ME").sum()
    pos = (monthly > 0).sum()
    print("\n[CONSISTENCY]")
    print(f"  Win Rate (overall)   : {ov['win_rate_pct']}%")
    print(f"  Positive months      : {pos}/{len(monthly)}  ({100*pos/len(monthly):.0f}%)")

    print("\n  By YEAR / REGIME:")
    print(f"    {'yr':4} {'regime':16} {'N':>5} {'win%':>6} {'PF':>6} {'netPnL':>12}")
    bull_wr = bear_wr = None
    by_year = defaultdict(list)
    for t in trades:
        by_year[t["d_out"].year].append(t)
    for y in sorted(by_year):
        b = block(by_year[y])
        print(f"    {y:4} {regime_of_year(y):16} {b['n']:>5} {b['win_rate_pct']:>6} "
              f"{b['profit_factor']:>6} {b['net_pnl']:>12,.0f}")

    # regime-grouped win rates for the gates
    bull = [t for y in (2023,) for t in by_year.get(y, [])]
    bear = [t for y in (2022,) for t in by_year.get(y, [])]
    chop = [t for y in (2025, 2026) for t in by_year.get(y, [])]
    bull_wr = block(bull)["win_rate_pct"]
    bear_wr = block(bear)["win_rate_pct"]
    chop_wr = block(chop)["win_rate_pct"]
    print(f"\n  Bull(2023) WR={bull_wr}%  Bear(2022) WR={bear_wr}%  Chop(2025-26) WR={chop_wr}%")

    # ---- EFFICIENCY ----
    months = years * 12
    holds = np.array([t["hold_days"] for t in trades])
    reasons = defaultdict(int)
    for t in trades:
        reasons[t.get("close_reason", "?")] += 1
    n = len(trades)
    tp = reasons.get("TAKE_PROFIT", 0)
    sl = reasons.get("STOP_LOSS", 0)
    # cost impact: round-trip * turnover / gross profit
    turnover = sum(t["entry"] * t["qty"] for t in trades) * (1 + 1)  # in+out approx
    cost_est = turnover * ROUND_TRIP_COST / 2  # round-trip already x2 above
    gross = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    print("\n[EFFICIENCY]")
    print(f"  Trade frequency      : {n/months:.1f} trades/month")
    print(f"  Avg holding period   : {holds.mean():.1f} days (median {np.median(holds):.0f})")
    print(f"  Exit reasons         : " + ", ".join(f"{k}={v} ({100*v/n:.0f}%)" for k, v in sorted(reasons.items(), key=lambda x: -x[1])))
    print(f"  Held to TARGET vs STOP: {100*tp/n:.0f}% TP / {100*sl/n:.0f}% SL")
    print(f"  Est. cost drag       : Rs {cost_est:,.0f}  (~{100*cost_est/gross:.0f}% of gross profit)")

    # ---- CRITICAL GATES ----
    print("\n" + "=" * 74)
    print("CRITICAL GATES")
    print("=" * 74)
    gates = [
        ("Sharpe >= 1.0", sharpe, 1.0, sharpe >= 1.0, "Strategy doesn't beat risk-free"),
        ("Profit Factor >= 1.5", ov["profit_factor"], 1.5, ov["profit_factor"] >= 1.5, "Not profitable after costs"),
        ("Max Drawdown <= 20%", abs(100*max_dd), 20, abs(100*max_dd) <= 20, "Too risky for capital preservation"),
        ("Bull WR >= 55%", bull_wr, 55, bull_wr >= 55, "Fails to capture upside"),
        ("Bear WR >= 45%", bear_wr, 45, bear_wr >= 45, "Too vulnerable in downturns"),
    ]
    passed = 0
    for name, val, thr, ok, msg in gates:
        tag = "PASS" if ok else "FAIL"
        passed += ok
        print(f"  [{tag}] {name:24} actual={val:.2f}   {'' if ok else '<- ' + msg}")
    print(f"\n  GATES PASSED: {passed}/5")
    verdict = "GO" if passed == 5 else "NO-GO"
    print(f"  PHASE 1 RECOMMENDATION: {verdict}")
    print("=" * 74)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results/bt_final.json")
