"""
Diagnose WHY the edge died in 2025-26 vs the healthy 2023-24 era.

Splits the trade ledger into GOOD (2023-2024) and BAD (2025-2026) and compares
along every axis that could explain a breakdown:
  - strategy MIX shift (did a loser fire more?)
  - per-strategy WIN-RATE / expectancy DECAY (same strat, worse results?)
  - regime LABEL distribution (is the classifier mislabeling chop as trend?)
  - EXIT behavior (give-back via close_reason mix)
  - loss SIZING / hold time

Verdict logic: if losers are CONCENTRATED in one strategy/regime -> gateable.
If decay is UNIVERSAL across strategies -> structural (long-only in a falling tape).
"""
import json
import sys
from collections import defaultdict
from datetime import date

import numpy as np


def load(path):
    d = json.load(open(path))
    for t in d["all_trades"]:
        t["y"] = date.fromisoformat(t["ts_exit"][:10]).year
    return d["all_trades"]


def stats(ts):
    if not ts:
        return dict(n=0, wr=0, pf=0, mean=0, avgwin=0, avgloss=0)
    pnl = np.array([t["pnl"] for t in ts], float)
    w, l = pnl[pnl > 0], pnl[pnl <= 0]
    return dict(
        n=len(ts), wr=round(100 * len(w) / len(ts), 1),
        pf=round(w.sum() / (abs(l.sum()) or 1e-9), 2),
        mean=round(pnl.mean(), 0),
        avgwin=round(w.mean(), 0) if len(w) else 0,
        avgloss=round(abs(l.mean()), 0) if len(l) else 0,
    )


def main(path):
    trades = load(path)
    good = [t for t in trades if t["y"] in (2023, 2024)]
    bad = [t for t in trades if t["y"] in (2025, 2026)]

    print("=" * 78)
    print("BREAKDOWN DIAGNOSIS  —  GOOD(2023-24) vs BAD(2025-26)")
    print("=" * 78)
    g, b = stats(good), stats(bad)
    print(f"\nAGGREGATE   GOOD: N={g['n']} WR={g['wr']}% PF={g['pf']} mean=Rs{g['mean']:,.0f}")
    print(f"            BAD : N={b['n']} WR={b['wr']}% PF={b['pf']} mean=Rs{b['mean']:,.0f}")

    # 1. STRATEGY MIX + per-strategy decay
    print("\n[1] STRATEGY MIX & PER-STRATEGY DECAY")
    print(f"  {'strategy':22} {'GOOD: N(%)  WR   PF   mean':32}  {'BAD: N(%)  WR   PF   mean'}")
    strats = sorted({t["strategy"] for t in trades})
    for s in strats:
        gs = [t for t in good if t["strategy"] == s]
        bs = [t for t in bad if t["strategy"] == s]
        gst, bst = stats(gs), stats(bs)
        gmix = 100 * gst["n"] / g["n"] if g["n"] else 0
        bmix = 100 * bst["n"] / b["n"] if b["n"] else 0
        print(f"  {s:22} {gst['n']:>4}({gmix:4.0f}%) {gst['wr']:>5} {gst['pf']:>5} {gst['mean']:>7,.0f}   "
              f"{bst['n']:>4}({bmix:4.0f}%) {bst['wr']:>5} {bst['pf']:>5} {bst['mean']:>7,.0f}")

    # 2. REGIME LABEL distribution (is classifier calling chop a trend?)
    print("\n[2] REGIME LABEL @ ENTRY (share of trades fired in each label)")
    for era, name in ((good, "GOOD"), (bad, "BAD")):
        dist = defaultdict(int)
        for t in era:
            dist[t.get("regime", "?")] += 1
        tot = len(era) or 1
        line = "  ".join(f"{k}={100*v/tot:.0f}%" for k, v in sorted(dist.items(), key=lambda x: -x[1]))
        print(f"  {name}: {line}")
        # win rate WITHIN each regime label
        for rk in sorted(dist):
            rs = stats([t for t in era if t.get("regime") == rk])
            print(f"        {rk:16} N={rs['n']:>4} WR={rs['wr']}% PF={rs['pf']} mean=Rs{rs['mean']:,.0f}")

    # 3. EXIT behavior (give-back signature)
    print("\n[3] EXIT REASON MIX (give-back signature)")
    for era, name in ((good, "GOOD"), (bad, "BAD")):
        dist = defaultdict(int)
        for t in era:
            dist[t.get("close_reason", "?")] += 1
        tot = len(era) or 1
        print(f"  {name}: " + "  ".join(f"{k}={100*v/tot:.0f}%" for k, v in sorted(dist.items(), key=lambda x: -x[1])))

    # 4. loss sizing / hold
    print("\n[4] LOSS SIZING")
    print(f"  GOOD avg loss=Rs{g['avgloss']:,.0f}  avg win=Rs{g['avgwin']:,.0f}")
    print(f"  BAD  avg loss=Rs{b['avgloss']:,.0f}  avg win=Rs{b['avgwin']:,.0f}")

    # ---- VERDICT ----
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    # is decay universal? count strategies that flipped from PF>1 to PF<1
    flips, universal = [], 0
    for s in strats:
        gp = stats([t for t in good if t["strategy"] == s])["pf"]
        bp = stats([t for t in bad if t["strategy"] == s])["pf"]
        gn = stats([t for t in good if t["strategy"] == s])["n"]
        bn = stats([t for t in bad if t["strategy"] == s])["n"]
        if gn >= 30 and bn >= 30:
            if gp >= 1.0 and bp < 1.0:
                flips.append(s)
                universal += 1
    print(f"  Strategies with N>=30 in both eras that flipped PF>=1 -> PF<1: {flips or 'none'}")
    if universal >= 2:
        print("  -> DECAY IS UNIVERSAL across strategies. Not a single bad strategy to gate off.")
        print("     This is STRUCTURAL: a long-only trend/breakout book in a chop/decline tape.")
    else:
        print("  -> Decay concentrated; a regime/strategy gate MAY recover some edge.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results/bt_final.json")
