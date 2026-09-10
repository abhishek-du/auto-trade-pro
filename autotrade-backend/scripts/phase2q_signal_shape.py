"""PHASE 2Q — V1 nonlinearity and signal-shape investigation.

ANALYSIS ONLY. Reads the frozen V1 baseline, writes reports into a new
phase2q/ directory, and changes nothing else. No model is trained, no feature
is invented, no target is altered, and the 2026 shard is never opened — the
loader refuses year >= 2026 and the run aborts if one is ever requested.

Every statistic is computed PER PREDICTION SESSION first and only then
aggregated across sessions. A stock-day is not an independent observation:
adjacent rows for one symbol share 251 of their 252 lookback sessions, and
every stock in one session shares that session's market move.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import glob
import gzip
import json
import math
import os
import statistics as st
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import v1_contract as C          # noqa: E402

HELD_OUT_YEAR = 2026
YEARS = [2022, 2023, 2024, 2025]

# ── frozen features used, chosen BEFORE any result was seen ──────────────────
MOMENTUM = ["return_1d", "return_5d", "return_21d", "return_63d",
            "rel_strength_21d", "consecutive_up_days"]
PRIMARY_MOMENTUM = "return_21d"          # the B3/B6 feature under investigation
VOLUME_FEATURE = "volume_ratio_20d"      # the feature B4 gates on
VOL_FEATURE = "realised_vol_21d"
REGIME_FEATURES = ["mkt_above_sma200", "mkt_above_sma50"]
STRUCTURE = ["range_position_20d", "range_position_52w", "dist_from_52w_high",
             "close_vs_sma20", "close_vs_sma200", "breakout_20d", "breakdown_20d"]

NEEDED = (["symbol", "prediction_session", "next_session_return",
           "extreme_return_flag"]
          + MOMENTUM + [VOLUME_FEATURE, VOL_FEATURE] + REGIME_FEATURES + STRUCTURE)
NEEDED = list(dict.fromkeys(NEEDED))


# ── statistics ───────────────────────────────────────────────────────────────

def tstat(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    n = len(xs)
    if n < 3:
        return {"n": n, "mean": None, "median": None, "std": None,
                "t": None, "p": None, "pos_share": None}
    m, sd = st.mean(xs), st.stdev(xs)
    t = m / (sd / math.sqrt(n)) if sd > 0 else None
    p = math.erfc(abs(t) / math.sqrt(2)) if t is not None else None
    return {"n": n, "mean": m, "median": st.median(xs), "std": sd,
            "t": t, "p": p, "pos_share": sum(1 for x in xs if x > 0) / n,
            "worst": min(xs), "best": max(xs)}


def _ranks(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    r = np.empty(len(a), dtype=float)
    r[order] = np.arange(1, len(a) + 1, dtype=float)
    # average ties
    s = a[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            r[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return r


def spearman(y: np.ndarray, x: np.ndarray) -> float | None:
    if len(y) < 5 or len(np.unique(x)) < 2:
        return None
    ry, rx = _ranks(y), _ranks(x)
    ry -= ry.mean(); rx -= rx.mean()
    d = math.sqrt(float((ry ** 2).sum()) * float((rx ** 2).sum()))
    return float((ry * rx).sum() / d) if d > 0 else None


def buckets(x: np.ndarray, edges=(0.30, 0.70)) -> np.ndarray:
    """Cross-sectional bucket per session: 0 bottom 30%, 1 middle 40%, 2 top 30%."""
    lo, hi = np.quantile(x, edges[0]), np.quantile(x, edges[1])
    b = np.ones(len(x), dtype=int)
    b[x <= lo] = 0
    b[x > hi] = 2
    return b


# ── load ─────────────────────────────────────────────────────────────────────

def load(dirpath: str):
    opened, skipped = [], []
    cols = {k: [] for k in NEEDED}
    for p in sorted(glob.glob(os.path.join(dirpath, "v1_baseline_*.csv.gz"))):
        year = int(os.path.basename(p).split("_")[-1].split(".")[0])
        if year >= HELD_OUT_YEAR:
            skipped.append(os.path.basename(p))
            continue
        if year not in YEARS:                       # only the validation years
            skipped.append(os.path.basename(p) + " (outside 2022-2025)")
            continue
        opened.append(os.path.basename(p))
        with gzip.open(p, "rt", newline="") as f:
            for r in csv.DictReader(f):
                if r["extreme_return_flag"] == "1":
                    continue
                for k in NEEDED:
                    cols[k].append(r[k])
    return cols, opened, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    man = json.load(open(os.path.join(args.dir, "manifest.json")))
    cols, opened, skipped = load(args.dir)
    for f in opened:
        assert str(HELD_OUT_YEAR) not in f, f"held-out shard opened: {f}"
    n = len(cols["symbol"])
    print(f"[load] opened {opened}")
    print(f"[load] skipped {skipped}")
    print(f"[load] {n:,} validation rows (quarantined rows already excluded)")

    sess_str = cols["prediction_session"]
    symbols = set(cols["symbol"])
    y = np.array([float(v) for v in cols["next_session_return"]])
    feat = {}
    for k in NEEDED:
        if k in ("symbol", "prediction_session", "next_session_return",
                 "extreme_return_flag"):
            continue
        feat[k] = np.array([float(v) if v != "" else np.nan for v in cols[k]])
    del cols

    # group rows by session
    idx_by_sess = collections.defaultdict(list)
    for i, s in enumerate(sess_str):
        idx_by_sess[s].append(i)
    sessions = sorted(idx_by_sess)
    assert not any(s.startswith("2026") for s in sessions), "2026 session leaked in"
    print(f"[load] {len(sessions)} sessions, {len(symbols)} symbols, "
          f"{sessions[0]} .. {sessions[-1]}")

    # per-session excess return
    excess = np.empty_like(y)
    for s in sessions:
        ii = idx_by_sess[s]
        excess[ii] = y[ii] - y[ii].mean()

    def yr(s):
        return int(s[:4])

    R = {"meta": {
        "dataset_digest": man["dataset_digest"],
        "contract_version": man["contract_version"],
        "dataset_rows": man["row_count"], "dataset_symbols": man["symbol_count"],
        "analysis_rows": n, "analysis_symbols": len(symbols),
        "sessions": len(sessions),
        "session_range": [sessions[0], sessions[-1]],
        "shards_opened": opened, "shards_skipped": skipped,
        "features_used": sorted(feat),
    }}
    tables = []          # flat rows for the CSV

    # ── Q1 — decile shape of return_21d ──────────────────────────────────────
    print("\n[Q1] return_21d decile shape")
    q1 = {}
    for scope in YEARS + ["combined"]:
        ss = [s for s in sessions if scope == "combined" or yr(s) == scope]
        per = {d: {"exc": [], "ret": [], "hit": [], "cnt": 0} for d in range(1, 11)}
        for s in ss:
            ii = np.array(idx_by_sess[s])
            x = feat[PRIMARY_MOMENTUM][ii]
            ok = np.isfinite(x)
            ii, x = ii[ok], x[ok]
            if len(ii) < 50:
                continue
            q = np.quantile(x, np.arange(0.1, 1.0, 0.1))
            d = np.digitize(x, q) + 1
            for dd in range(1, 11):
                m = d == dd
                if not m.any():
                    continue
                per[dd]["exc"].append(float(excess[ii[m]].mean()))
                per[dd]["ret"].append(float(y[ii[m]].mean()))
                per[dd]["hit"].append(float((y[ii[m]] > 0).mean()))
                per[dd]["cnt"] += int(m.sum())
        out = {}
        for dd in range(1, 11):
            e, r, h = per[dd]["exc"], per[dd]["ret"], per[dd]["hit"]
            te, tr = tstat(e), tstat(r)
            out[f"D{dd}"] = {
                "observations": per[dd]["cnt"], "sessions": len(e),
                "mean_return": tr["mean"], "median_return": tr["median"],
                "mean_excess": te["mean"], "median_excess": te["median"],
                "excess_std": te["std"], "excess_t": te["t"], "excess_p": te["p"],
                "positive_session_share": te["pos_share"],
                "worst_session_excess": te["worst"],
                "hit_rate": st.mean(h) if h else None,
            }
            tables.append({"question": "Q1", "scope": scope, "cell": f"D{dd}",
                           "observations": per[dd]["cnt"], "sessions": len(e),
                           "mean_return": tr["mean"], "mean_excess": te["mean"],
                           "median_excess": te["median"], "t": te["t"],
                           "p": te["p"], "pos_session_share": te["pos_share"],
                           "hit_rate": st.mean(h) if h else None,
                           "worst_session": te["worst"]})
        means = [out[f"D{d}"]["mean_excess"] for d in range(1, 11)]
        diffs = [means[i + 1] - means[i] for i in range(9)]
        up, down = sum(1 for x in diffs if x > 0), sum(1 for x in diffs if x < 0)
        lo3, mid4, hi3 = st.mean(means[:3]), st.mean(means[3:7]), st.mean(means[7:])
        if up >= 8:
            shape = "monotonic positive"
        elif down >= 8:
            shape = "monotonic negative"
        elif lo3 > mid4 and hi3 > mid4:
            shape = "U-shaped"
        elif lo3 < mid4 and hi3 < mid4:
            shape = "inverted-U"
        elif max(means) - min(means) < 0.0005:
            shape = "flat"
        else:
            shape = "irregular / unstable"
        out["_shape"] = {"classification": shape, "steps_up": up, "steps_down": down,
                         "mean_excess_D1_D3": lo3, "mean_excess_D4_D7": mid4,
                         "mean_excess_D8_D10": hi3,
                         "spread_D10_minus_D1": means[9] - means[0]}
        q1[str(scope)] = out
        print(f"   {scope}: shape={shape}  D1={means[0]:+.5f} D10={means[9]:+.5f} "
              f"spread={means[9]-means[0]:+.5f}")
    R["Q1_decile_shape"] = q1

    # ── Q2 — every frozen momentum horizon ───────────────────────────────────
    print("\n[Q2] frozen momentum horizons")
    q2 = {}
    for f in MOMENTUM:
        q2[f] = {}
        for scope in YEARS + ["combined"]:
            ss = [s for s in sessions if scope == "combined" or yr(s) == scope]
            ics, t10e, t10h = [], [], []
            for s in ss:
                ii = np.array(idx_by_sess[s])
                x = feat[f][ii]
                ok = np.isfinite(x)
                ii2, x2 = ii[ok], x[ok]
                if len(ii2) < 50:
                    continue
                ic = spearman(y[ii2], x2)
                if ic is not None:
                    ics.append(ic)
                top = ii2[np.argsort(-x2, kind="mergesort")[:10]]
                if len(top) == 10:
                    t10e.append(float(excess[top].mean()))
                    t10h.append(float((y[top] > 0).mean()))
            tic, tte = tstat(ics), tstat(t10e)
            q2[f][str(scope)] = {
                "sessions": tic["n"], "mean_ic": tic["mean"],
                "median_ic": tic["median"], "ic_std": tic["std"],
                "ic_t": tic["t"], "ic_p": tic["p"],
                "positive_ic_share": tic["pos_share"],
                "top10_excess": tte["mean"], "top10_excess_t": tte["t"],
                "top10_excess_p": tte["p"],
                "top10_hit_rate": st.mean(t10h) if t10h else None,
            }
            tables.append({"question": "Q2", "scope": scope, "cell": f,
                           "sessions": tic["n"], "mean_ic": tic["mean"],
                           "t": tic["t"], "p": tic["p"],
                           "pos_session_share": tic["pos_share"],
                           "mean_excess": tte["mean"],
                           "hit_rate": st.mean(t10h) if t10h else None})
        c = q2[f]["combined"]
        print(f"   {f:22s} IC={c['mean_ic']:+.4f} t={c['ic_t']:+.2f} "
              f"top10_excess={c['top10_excess']:+.5f}")
    R["Q2_momentum_horizons"] = q2

    # ── Q3 / Q4 — 3x3 conditional grids ──────────────────────────────────────
    def grid(second: str, label: str):
        print(f"\n[{label}] {PRIMARY_MOMENTUM} x {second}")
        g = {}
        for scope in YEARS + ["combined"]:
            ss = [s for s in sessions if scope == "combined" or yr(s) == scope]
            cell = {(a, b): {"exc": [], "ret": [], "hit": [], "cnt": 0}
                    for a in range(3) for b in range(3)}
            for s in ss:
                ii = np.array(idx_by_sess[s])
                m, v = feat[PRIMARY_MOMENTUM][ii], feat[second][ii]
                ok = np.isfinite(m) & np.isfinite(v)
                ii2 = ii[ok]
                if len(ii2) < 50:
                    continue
                bm, bv = buckets(m[ok]), buckets(v[ok])
                for a in range(3):
                    for b in range(3):
                        msk = (bm == a) & (bv == b)
                        if msk.sum() < 3:
                            continue
                        sel = ii2[msk]
                        cell[(a, b)]["exc"].append(float(excess[sel].mean()))
                        cell[(a, b)]["ret"].append(float(y[sel].mean()))
                        cell[(a, b)]["hit"].append(float((y[sel] > 0).mean()))
                        cell[(a, b)]["cnt"] += int(msk.sum())
            names = ["bottom30", "middle40", "top30"]
            out = {}
            for (a, b), d in cell.items():
                te, tr = tstat(d["exc"]), tstat(d["ret"])
                key = f"mom_{names[a]}__{label.lower()}_{names[b]}"
                out[key] = {"observations": d["cnt"], "sessions": te["n"],
                            "mean_return": tr["mean"], "median_return": tr["median"],
                            "mean_excess": te["mean"], "median_excess": te["median"],
                            "excess_t": te["t"], "excess_p": te["p"],
                            "positive_session_share": te["pos_share"],
                            "hit_rate": st.mean(d["hit"]) if d["hit"] else None}
                tables.append({"question": label, "scope": scope, "cell": key,
                               "observations": d["cnt"], "sessions": te["n"],
                               "mean_return": tr["mean"], "mean_excess": te["mean"],
                               "median_excess": te["median"], "t": te["t"],
                               "p": te["p"], "pos_session_share": te["pos_share"],
                               "hit_rate": st.mean(d["hit"]) if d["hit"] else None})
            g[str(scope)] = out
        c = g["combined"]
        for k in sorted(c):
            print(f"   {k:44s} exc={c[k]['mean_excess']:+.5f} t={c[k]['excess_t']:+.2f}")
        return g

    R["Q3_momentum_x_volume"] = grid(VOLUME_FEATURE, "Q3")
    R["Q4_momentum_x_volatility"] = grid(VOL_FEATURE, "Q4")

    # ── Q5 — market regime, using ONLY the frozen binary trend flags ─────────
    print("\n[Q5] momentum under the frozen market-trend flags")
    q5 = {}
    for rf in REGIME_FEATURES:
        q5[rf] = {}
        for state in (0, 1):
            ss = [s for s in sessions
                  if np.isfinite(feat[rf][idx_by_sess[s][0]])
                  and int(feat[rf][idx_by_sess[s][0]]) == state]
            ics, t10 = [], []
            per_year = collections.Counter()
            for s in ss:
                per_year[yr(s)] += 1
                ii = np.array(idx_by_sess[s])
                x = feat[PRIMARY_MOMENTUM][ii]
                ok = np.isfinite(x)
                ii2, x2 = ii[ok], x[ok]
                if len(ii2) < 50:
                    continue
                ic = spearman(y[ii2], x2)
                if ic is not None:
                    ics.append(ic)
                top = ii2[np.argsort(-x2, kind="mergesort")[:10]]
                if len(top) == 10:
                    t10.append(float(excess[top].mean()))
            tic, tte = tstat(ics), tstat(t10)
            q5[rf][f"state_{state}"] = {
                "sessions": len(ss), "sessions_by_year": dict(sorted(per_year.items())),
                "mean_ic": tic["mean"], "ic_t": tic["t"], "ic_p": tic["p"],
                "positive_ic_share": tic["pos_share"],
                "top10_excess": tte["mean"], "top10_excess_t": tte["t"],
                "top10_excess_p": tte["p"]}
            tables.append({"question": "Q5", "scope": "combined",
                           "cell": f"{rf}={state}", "sessions": len(ss),
                           "mean_ic": tic["mean"], "t": tic["t"], "p": tic["p"],
                           "mean_excess": tte["mean"]})
            print(f"   {rf}={state}: sessions={len(ss):4d} IC={tic['mean']:+.4f} "
                  f"top10_excess={tte['mean']:+.5f} (t={tte['t']:+.2f})")
    R["Q5_market_regime"] = q5

    # ── Q6 — price / breakout structure ──────────────────────────────────────
    print("\n[Q6] price-structure features")
    q6 = {}
    for f in STRUCTURE:
        q6[f] = {}
        for scope in YEARS + ["combined"]:
            ss = [s for s in sessions if scope == "combined" or yr(s) == scope]
            ics, top_e, bot_e = [], [], []
            for s in ss:
                ii = np.array(idx_by_sess[s])
                x = feat[f][ii]
                ok = np.isfinite(x)
                ii2, x2 = ii[ok], x[ok]
                if len(ii2) < 50 or len(np.unique(x2)) < 2:
                    continue
                ic = spearman(y[ii2], x2)
                if ic is not None:
                    ics.append(ic)
                b = buckets(x2)
                if (b == 2).sum() >= 3:
                    top_e.append(float(excess[ii2[b == 2]].mean()))
                if (b == 0).sum() >= 3:
                    bot_e.append(float(excess[ii2[b == 0]].mean()))
            tic, tt, tb = tstat(ics), tstat(top_e), tstat(bot_e)
            q6[f][str(scope)] = {
                "sessions": tic["n"], "mean_ic": tic["mean"],
                "median_ic": tic["median"], "ic_std": tic["std"],
                "ic_t": tic["t"], "ic_p": tic["p"],
                "positive_ic_share": tic["pos_share"],
                "top30_excess": tt["mean"], "top30_excess_t": tt["t"],
                "top30_excess_p": tt["p"],
                "bottom30_excess": tb["mean"], "bottom30_excess_t": tb["t"],
                "bottom30_excess_p": tb["p"]}
            tables.append({"question": "Q6", "scope": scope, "cell": f,
                           "sessions": tic["n"], "mean_ic": tic["mean"],
                           "t": tic["t"], "p": tic["p"],
                           "pos_session_share": tic["pos_share"],
                           "mean_excess": tt["mean"]})
        c = q6[f]["combined"]
        print(f"   {f:22s} IC={c['mean_ic']:+.4f} t={c['ic_t']:+.2f} "
              f"top30_exc={c['top30_excess']:+.5f} bot30_exc={c['bottom30_excess']:+.5f}")
    R["Q6_price_structure"] = q6

    with open(os.path.join(args.out, "phase2q_report.json"), "w") as fh:
        json.dump(R, fh, indent=2, default=str, sort_keys=True)
    keys = ["question", "scope", "cell", "observations", "sessions", "mean_return",
            "mean_excess", "median_excess", "mean_ic", "t", "p",
            "pos_session_share", "hit_rate", "worst_session"]
    with open(os.path.join(args.out, "phase2q_tables.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(tables)
    print(f"\n[out] {args.out}/phase2q_report.json  +  phase2q_tables.csv "
          f"({len(tables)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
