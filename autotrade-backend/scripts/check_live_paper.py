"""Step 3: Check whether live paper-trade performance matches backtest expectancy.

Queries the paper_trades table for closed trades since 2025-01-01,
computes R-multiple statistics (using the Phase 1 attribution columns),
and compares to the backtest's OOS 2025-26 expectancy of -0.097R.

Usage:
  .venv/bin/python scripts/check_live_paper.py
  .venv/bin/python scripts/check_live_paper.py --since 2024-01-01
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKTEST_OOS_MEAN_R = -0.097   # from Phase 2 OOS 2025-26
BACKTEST_OOS_CI_LO  = -0.146
BACKTEST_OOS_CI_HI  = -0.048


async def fetch_paper_trades(since: date):
    from db.database import AsyncSessionLocal
    from sqlalchemy import text

    since_str = since.isoformat()
    query = text("""
        SELECT
            id, symbol, strategy_name, regime_at_entry,
            confidence_bucket, exit_reason,
            pnl, r_multiple, initial_risk_inr,
            holding_hours, mfe_r, mae_r,
            entry_price, exit_price,
            opened_at, closed_at
        FROM paper_trades
        WHERE status = 'CLOSED'
          AND closed_at >= :since
        ORDER BY closed_at DESC
    """)

    async with AsyncSessionLocal() as session:
        result = await session.execute(query, {"since": datetime.combine(since, datetime.min.time())})
        rows = result.mappings().all()
        return [dict(r) for r in rows]


def analyse(trades: list[dict], since: date) -> None:
    print("\n" + "═" * 72)
    print("  Live Paper Trade Performance vs Backtest OOS Expectancy")
    print("═" * 72)
    print(f"\n  Period   : {since} → today")
    print(f"  Backtest OOS 2025-26: mean_R = {BACKTEST_OOS_MEAN_R}  "
          f"CI [{BACKTEST_OOS_CI_LO}, {BACKTEST_OOS_CI_HI}]  (NEGATIVE)")

    if not trades:
        print(f"\n  ⚠  No closed paper trades found since {since}.")
        print("     The live paper system has not accumulated enough trades to compare.")
        print("     → Continue paper-trading; re-run this check after 30+ closed trades.")
        print("\n" + "═" * 72 + "\n")
        return

    # R-multiple distribution
    r_vals = [float(t["r_multiple"]) for t in trades if t.get("r_multiple") is not None]
    no_r   = len(trades) - len(r_vals)
    wins   = [t for t in trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl") or 0) <= 0]
    gw     = sum(float(t["pnl"]) for t in wins   if t.get("pnl"))
    gl     = abs(sum(float(t["pnl"]) for t in losses if t.get("pnl"))) or 1e-9

    mean_r  = float(np.mean(r_vals))  if r_vals else None
    med_r   = float(np.median(r_vals)) if r_vals else None

    # Bootstrap CI
    ci_lo = ci_hi = None
    if len(r_vals) >= 10:
        rng   = np.random.default_rng(42)
        arr   = np.array(r_vals)
        boots = rng.choice(arr, size=(10_000, len(arr)), replace=True).mean(axis=1)
        ci_lo = round(float(np.percentile(boots, 2.5)), 4)
        ci_hi = round(float(np.percentile(boots, 97.5)), 4)

    print(f"\n  Live paper trades  : {len(trades)}")
    print(f"  With R-multiple    : {len(r_vals)}  (missing initial_risk: {no_r})")
    print(f"  Win rate           : {len(wins) / len(trades) * 100:.1f}%")
    print(f"  Profit factor      : {round(gw / gl, 3)}")
    print(f"  Net P&L            : ₹{gw - gl:,.0f}")
    if mean_r is not None:
        ci_str = f"CI [{ci_lo}, {ci_hi}]" if ci_lo is not None else "(need ≥10 trades for CI)"
        print(f"  Mean R/trade       : {mean_r:.4f}  {ci_str}")
        print(f"  Median R/trade     : {med_r:.4f}")

    # Comparison verdict
    print(f"\n  ── Comparison vs Backtest ───────────────────────────────────────")
    if mean_r is None:
        print("  Cannot compare — no R-multiples available (initial_risk_inr not captured).")
        print("  Ensure Phase 1 attribution is running (execution.py / trade_simulator.py).")
    elif len(r_vals) < 30:
        print(f"  ⚠  Only {len(r_vals)} trades with R — too few for reliable comparison (need ≥30).")
        print(f"  Current live mean_R = {mean_r:.3f} vs backtest OOS = {BACKTEST_OOS_MEAN_R}")
        print("  Direction check only (not statistically significant):")
        direction = "CONSISTENT" if mean_r < 0 else "DIVERGES (live better than backtest)"
        print(f"  {direction}")
    else:
        print(f"  Backtest OOS mean_R : {BACKTEST_OOS_MEAN_R}  CI [{BACKTEST_OOS_CI_LO}, {BACKTEST_OOS_CI_HI}]")
        print(f"  Live paper mean_R   : {mean_r:.3f}  CI [{ci_lo}, {ci_hi}]")
        if ci_lo is not None and ci_hi is not None:
            if ci_lo > BACKTEST_OOS_CI_HI:
                verdict = "DIVERGES POSITIVE — live significantly outperforms backtest OOS"
                note = "Possible causes: (a) backtest regime bug inflated negative trades; (b) paper system applies regime/hub filters the backtest misses."
            elif ci_hi < BACKTEST_OOS_CI_LO:
                verdict = "DIVERGES NEGATIVE — live worse than backtest OOS"
                note = "Possible causes: (a) execution costs higher than modelled; (b) selection bias in paper system."
            elif (ci_lo < 0 < ci_hi) and mean_r > 0:
                verdict = "UNCERTAIN — live positive but CI straddles zero"
                note = "Accumulate more trades."
            elif mean_r < 0 and ci_hi < 0:
                verdict = "CONSISTENT with backtest OOS — live also negative"
                note = "Backtest is trustworthy. The edge is genuinely absent in 2025-26."
            else:
                verdict = "BROADLY CONSISTENT — CIs overlap"
                note = "Backtest and live are telling the same story."
            print(f"  Verdict : {verdict}")
            print(f"  Note    : {note}")

    # By strategy
    by_strat: dict[str, list] = {}
    for t in trades:
        s = t.get("strategy_name") or "UNKNOWN"
        by_strat.setdefault(s, []).append(t)
    if len(by_strat) > 1:
        print(f"\n  ── By Strategy ──────────────────────────────────────────────────")
        for strat, ts in sorted(by_strat.items()):
            rs = [float(t["r_multiple"]) for t in ts if t.get("r_multiple") is not None]
            mr = f"{np.mean(rs):.3f}" if rs else "N/A"
            wr = f"{sum(1 for t in ts if (t.get('pnl') or 0) > 0) / len(ts) * 100:.0f}%"
            print(f"    {strat:<28} n={len(ts):<4}  WR={wr}  mean_R={mr}")

    # By exit reason
    by_exit: dict[str, list] = {}
    for t in trades:
        e = t.get("exit_reason") or "UNKNOWN"
        by_exit.setdefault(e, []).append(t)
    if by_exit:
        print(f"\n  ── By Exit Reason ───────────────────────────────────────────────")
        for reason, ts in sorted(by_exit.items(), key=lambda x: -len(x[1])):
            rs = [float(t["r_multiple"]) for t in ts if t.get("r_multiple") is not None]
            mr = f"{np.mean(rs):.3f}" if rs else "N/A"
            print(f"    {reason:<20} n={len(ts):<4}  mean_R={mr}")

    print("\n" + "═" * 72 + "\n")


async def run(since: date) -> None:
    print(f"[check_live_paper] Querying paper trades since {since}...")
    try:
        trades = await fetch_paper_trades(since)
    except Exception as exc:
        print(f"[check_live_paper] DB query failed: {exc}")
        print("  Is postgres running?  sudo docker compose up -d postgres")
        return
    analyse(trades, since)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=date.fromisoformat, default=date(2025, 1, 1))
    args = parser.parse_args()
    asyncio.run(run(args.since))
