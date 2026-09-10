"""PHASE 2Q — Q7 conditional pockets, Q8 stability, Q9/Q10 statistics.

The five candidate conditions below were written down BEFORE this script was
run and are stated verbatim in CANDIDATES. They are EXPLORATORY: they were
chosen after reading Q1-Q6, not predefined in the Phase 2O protocol, and the
report says so. No condition was added, removed or re-tuned after seeing its
result.

Also resolves the Phase 2P paradox — negative rank IC alongside a positive
top-10 excess — by measuring how the excess decays with selection size. That
is the same frozen feature examined at different depths, not a search.
"""
from __future__ import annotations

import argparse, collections, csv, glob, gzip, json, math, os, statistics as st, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import v1_contract as C          # noqa: E402
from scripts.phase2q_signal_shape import (     # noqa: E402
    HELD_OUT_YEAR, YEARS, MOMENTUM, PRIMARY_MOMENTUM, VOLUME_FEATURE,
    VOL_FEATURE, REGIME_FEATURES, STRUCTURE, NEEDED, tstat, buckets, load,
)

TOPK_LADDER = (5, 10, 20, 50, 100, 200)

# ── the five candidates, fixed before execution ─────────────────────────────
CANDIDATES = [
    ("C1_breakout_only",
     "breakout_20d == 1",
     "Q6: the strongest single structural effect (IC +0.0269, t +17.95)"),
    ("C2_low_momentum_high_volume",
     "return_21d bottom 30% AND volume_ratio_20d top 30%",
     "Q3: the best 3x3 cell (+0.00147, t +11.34)"),
    ("C3_high_momentum_low_volatility",
     "return_21d top 30% AND realised_vol_21d bottom 30%",
     "Q4: momentum only survives in the low-volatility column (+0.00062, t +3.10)"),
    ("C4_high_momentum_high_volume",
     "return_21d top 30% AND volume_ratio_20d top 30%",
     "named in the brief; Q3 shows it positive (+0.00068) but weaker than C2"),
    ("C5_breakout_and_high_volume",
     "breakout_20d == 1 AND volume_ratio_20d top 30%",
     "combines the two strongest independent axes found in Q3 and Q6"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    man = json.load(open(os.path.join(args.dir, "manifest.json")))
    cols, opened, skipped = load(args.dir)
    for f in opened:
        assert str(HELD_OUT_YEAR) not in f, f"held-out shard opened: {f}"
    n = len(cols["symbol"])
    sess_str = cols["prediction_session"]
    y = np.array([float(v) for v in cols["next_session_return"]])
    feat = {k: np.array([float(v) if v != "" else np.nan for v in cols[k]])
            for k in NEEDED if k not in ("symbol", "prediction_session",
                                         "next_session_return", "extreme_return_flag")}
    del cols
    idx_by_sess = collections.defaultdict(list)
    for i, s in enumerate(sess_str):
        idx_by_sess[s].append(i)
    sessions = sorted(idx_by_sess)
    assert not any(s.startswith("2026") for s in sessions)
    excess = np.empty_like(y)
    for s in sessions:
        ii = idx_by_sess[s]
        excess[ii] = y[ii] - y[ii].mean()
    print(f"[load] {n:,} rows, {len(sessions)} sessions, opened={opened}")

    def yr(s):
        return int(s[:4])

    R = {"meta": {"dataset_digest": man["dataset_digest"], "rows": n,
                  "sessions": len(sessions), "shards_opened": opened,
                  "shards_skipped": skipped}}
    tables = []

    # ── selection-depth ladder: why IC is negative but top-10 is positive ───
    print("\n[Q1b] excess by selection depth, ranked on return_21d")
    ladder = {}
    for scope in YEARS + ["combined"]:
        ss = [s for s in sessions if scope == "combined" or yr(s) == scope]
        acc = {k: [] for k in TOPK_LADDER}
        acc_bot = {k: [] for k in TOPK_LADDER}
        for s in ss:
            ii = np.array(idx_by_sess[s])
            x = feat[PRIMARY_MOMENTUM][ii]
            ok = np.isfinite(x)
            ii2, x2 = ii[ok], x[ok]
            if len(ii2) < 250:
                continue
            order = np.argsort(-x2, kind="mergesort")
            for k in TOPK_LADDER:
                acc[k].append(float(excess[ii2[order[:k]]].mean()))
                acc_bot[k].append(float(excess[ii2[order[-k:]]].mean()))
        row = {}
        for k in TOPK_LADDER:
            t, b = tstat(acc[k]), tstat(acc_bot[k])
            row[f"top{k}"] = {"mean_excess": t["mean"], "t": t["t"], "p": t["p"],
                              "sessions": t["n"], "pos_share": t["pos_share"]}
            row[f"bottom{k}"] = {"mean_excess": b["mean"], "t": b["t"], "p": b["p"],
                                 "sessions": b["n"], "pos_share": b["pos_share"]}
            tables.append({"question": "Q1b", "scope": scope, "cell": f"top{k}",
                           "sessions": t["n"], "mean_excess": t["mean"],
                           "t": t["t"], "p": t["p"], "pos_session_share": t["pos_share"]})
            tables.append({"question": "Q1b", "scope": scope, "cell": f"bottom{k}",
                           "sessions": b["n"], "mean_excess": b["mean"],
                           "t": b["t"], "p": b["p"], "pos_session_share": b["pos_share"]})
        ladder[str(scope)] = row
        if scope == "combined":
            for k in TOPK_LADDER:
                print(f"   top{k:<4} exc={row[f'top{k}']['mean_excess']:+.5f} "
                      f"t={row[f'top{k}']['t']:+.2f}   "
                      f"bottom{k:<4} exc={row[f'bottom{k}']['mean_excess']:+.5f} "
                      f"t={row[f'bottom{k}']['t']:+.2f}")
    R["Q1b_selection_depth"] = ladder

    # ── Q7 / Q8 — the five candidates, year by year ─────────────────────────
    print("\n[Q7] conditional pockets (EXPLORATORY)")
    q7 = {}
    for cid, rule, why in CANDIDATES:
        q7[cid] = {"rule": rule, "motivation": why, "classification": "EXPLORATORY",
                   "by_scope": {}}
        for scope in YEARS + ["combined"]:
            ss = [s for s in sessions if scope == "combined" or yr(s) == scope]
            per_sess, hits, obs = [], [], 0
            for s in ss:
                ii = np.array(idx_by_sess[s])
                mom = feat[PRIMARY_MOMENTUM][ii]
                vol = feat[VOLUME_FEATURE][ii]
                rv = feat[VOL_FEATURE][ii]
                bo = feat["breakout_20d"][ii]
                ok = np.isfinite(mom) & np.isfinite(vol) & np.isfinite(rv) & np.isfinite(bo)
                ii2 = ii[ok]
                if len(ii2) < 50:
                    continue
                bm, bv, brv = buckets(mom[ok]), buckets(vol[ok]), buckets(rv[ok])
                b_break = bo[ok] == 1.0
                if cid == "C1_breakout_only":
                    m = b_break
                elif cid == "C2_low_momentum_high_volume":
                    m = (bm == 0) & (bv == 2)
                elif cid == "C3_high_momentum_low_volatility":
                    m = (bm == 2) & (brv == 0)
                elif cid == "C4_high_momentum_high_volume":
                    m = (bm == 2) & (bv == 2)
                else:
                    m = b_break & (bv == 2)
                if m.sum() < 3:
                    continue
                sel = ii2[m]
                per_sess.append(float(excess[sel].mean()))
                hits.append(float((y[sel] > 0).mean()))
                obs += int(m.sum())
            t = tstat(per_sess)
            q7[cid]["by_scope"][str(scope)] = {
                "sessions": t["n"], "observations": obs,
                "mean_excess": t["mean"], "median_excess": t["median"],
                "excess_std": t["std"], "t": t["t"], "p": t["p"],
                "positive_session_share": t["pos_share"],
                "worst_session": t["worst"],
                "hit_rate": st.mean(hits) if hits else None,
                "avg_names_per_session": obs / t["n"] if t["n"] else None}
            tables.append({"question": "Q7", "scope": scope, "cell": cid,
                           "observations": obs, "sessions": t["n"],
                           "mean_excess": t["mean"], "median_excess": t["median"],
                           "t": t["t"], "p": t["p"],
                           "pos_session_share": t["pos_share"],
                           "hit_rate": st.mean(hits) if hits else None,
                           "worst_session": t["worst"]})
        yearly = [q7[cid]["by_scope"][str(v)]["mean_excess"] for v in YEARS]
        same_sign = len({x > 0 for x in yearly if x is not None}) == 1
        sig = [q7[cid]["by_scope"][str(v)]["p"] for v in YEARS]
        all_sig = all(p is not None and p < 0.05 for p in sig)
        if same_sign and all_sig and min(abs(x) for x in yearly) > 0.5 * max(abs(x) for x in yearly):
            grade = "GREEN"
        elif same_sign and all_sig:
            grade = "YELLOW"
        elif same_sign:
            grade = "YELLOW"
        else:
            grade = "RED"
        q7[cid]["stability"] = {"yearly_mean_excess": yearly, "same_sign": same_sign,
                                "all_years_p_lt_0.05": all_sig, "grade": grade}
        c = q7[cid]["by_scope"]["combined"]
        print(f"   {cid:34s} exc={c['mean_excess']:+.5f} t={c['t']:+.2f} "
              f"names/sess={c['avg_names_per_session']:.0f} "
              f"yearly={['%+.5f' % v for v in yearly]} {grade}")
    R["Q7_conditional_pockets"] = q7

    # ── Q8 — stability grading of the Phase 2P headline ─────────────────────
    print("\n[Q8] Phase 2P headline, re-graded")
    p2p = {"2022": 0.00798, "2023": 0.00413, "2024": 0.00310, "2025": 0.00069}
    decay = p2p["2025"] / p2p["2022"]
    R["Q8_phase2p_headline"] = {
        "metric": "B3/B6 top-10 excess return",
        "yearly": p2p, "same_sign": True,
        "ratio_2025_to_2022": decay,
        "grade": "YELLOW" if decay > 0.2 else "RED",
        "note": ("same sign in all four years but the magnitude falls to "
                 f"{decay:.0%} of its 2022 value and 2025 is not significant "
                 "(p = 0.42 in Phase 2P)")}
    print(f"   decay 2025/2022 = {decay:.1%} -> {R['Q8_phase2p_headline']['grade']}")

    with open(os.path.join(args.out, "phase2q_pockets.json"), "w") as fh:
        json.dump(R, fh, indent=2, default=str, sort_keys=True)
    keys = ["question", "scope", "cell", "observations", "sessions", "mean_excess",
            "median_excess", "t", "p", "pos_session_share", "hit_rate", "worst_session"]
    with open(os.path.join(args.out, "phase2q_pockets.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(tables)
    print(f"\n[out] {args.out}/phase2q_pockets.json + .csv ({len(tables)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
