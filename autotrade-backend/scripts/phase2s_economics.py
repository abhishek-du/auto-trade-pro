"""PHASE 2S — economic viability, capacity, and the 2025 market dependence.

RESEARCH ONLY. Nothing is tuned. The model is the frozen Phase 2R M1 at the
same seed, the universe is the frozen Phase 2R.1 rule, the folds are frozen,
and the 2026 shard is never opened.

WHY A REFIT HAPPENS HERE
------------------------
Phase 2R.1 persisted aggregate metrics, not per-row predictions, and S1-S3 all
need the picks. The model is refitted at the IDENTICAL configuration and seed,
which is deterministic, and the reproduced IC is asserted against the value
Phase 2R.1 recorded. That is reproduction, not retraining.

WHAT THE DATA CANNOT SUPPORT
----------------------------
The V1 target is close[D+1]/close[D]-1. A fill at close[D] IS the decision
instant, so no realized trading return is inferable from V1 alone, and V1
carries no open[D+1] column. S1 is therefore a SENSITIVITY BAND over an
idealised close-to-close basket, not a backtest. A separate, clearly labelled
execution diagnostic reads open[D+1] from the canonical candles to measure how
much of the edge survives a next-open fill; that price is an evaluation fill,
never a model input.
"""
from __future__ import annotations

import argparse, collections, csv, json, math, os, statistics as st, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import v1_contract as C          # noqa: E402
from scripts.phase2q_signal_shape import tstat, spearman   # noqa: E402
from scripts.phase2r_r1 import load, SEED, HELD_OUT_YEAR   # noqa: E402
from scripts.fold_contract import FOLDS      # manifest-authoritative  # noqa: E402
from scripts.phase2r1_liquidity import (TURNOVER, LIQUID_QUANTILE,  # noqa: E402
                                        SESSION_CONSTANT_MKT)

KS = (5, 10, 20)
COSTS_BPS = (5, 10, 15, 20, 30, 50)          # ROUND-TRIP, applied to traded fraction
CAPITALS = [1e5, 5e5, 1e6, 2.5e6, 5e6, 1e7]  # 1L, 5L, 10L, 25L, 50L, 1cr
PARTICIPATION_FLAGS = (0.01, 0.05, 0.10)

# ── S3 bucket rules, FIXED BEFORE ANY RESULT WAS SEEN ───────────────────────
S3_RULES = {
    "B1_mkt_above_sma200": "sessions split by the frozen binary flag (0 / 1)",
    "B2_mkt_above_sma50": "sessions split by the frozen binary flag (0 / 1)",
    "B3_mkt_vol_21d_tercile": ("session terciles of mkt_vol_21d, cut-points taken "
                               "from the TRAIN fold distribution (PIT-safe)"),
    "B4_calendar_quarter": "2025 split into Q1..Q4 by calendar date",
    "B5_single_feature_neutralisation": "each of the six market features neutralised alone",
    "B6_extreme_predictions": "IC recomputed excluding the top/bottom 1% of predictions",
    "B7_turnover_tercile_within_liquid": "liquid universe split into its own turnover terciles",
}


def maxdd(eq):
    peak, dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1.0)
    return dd


def curve_stats(rets):
    if len(rets) < 3:
        return {}
    a = np.asarray(rets, float)
    eq = np.cumprod(1.0 + a)
    mu, sd = float(a.mean()), float(a.std(ddof=1))
    return {"sessions": len(a), "mean_session_return": mu, "median": float(np.median(a)),
            "std": sd, "sharpe_annualised": (mu / sd * math.sqrt(252)) if sd > 0 else None,
            "profitable_session_share": float((a > 0).mean()),
            "total_return": float(eq[-1] - 1.0), "max_drawdown": maxdd(eq),
            "worst_session": float(a.min()), "best_session": float(a.max())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    from sklearn.ensemble import HistGradientBoostingRegressor

    prior = json.load(open(os.path.join(
        args.dir, "phase2r1", "phase2r1_results.json")))
    X, y, sess, syms, opened, skipped, man = load(args.dir)
    assert not any(s.startswith("2026") for s in sess), "2026 row in the matrix"
    print(f"[load] X={X.shape}  skipped={skipped}", flush=True)

    idx = collections.defaultdict(list)
    for i, s in enumerate(sess):
        idx[s].append(i)
    alls = sorted(idx)
    jt = C.FEATURES.index(TURNOVER)

    keep = np.zeros(len(y), bool)
    for s in alls:
        ii = np.asarray(idx[s])
        tv = X[ii, jt].astype(float)
        ok = np.isfinite(tv)
        if ok.sum() < 50:
            continue
        keep[ii[ok & (tv >= np.quantile(tv[ok], LIQUID_QUANTILE))]] = True
    print(f"[universe] liquid {keep.sum():,} ({keep.mean():.1%})", flush=True)

    R = {"meta": {"dataset_digest": man["dataset_digest"],
                  "shards_opened": opened, "shards_skipped": skipped,
                  "liquidity_rule": f"{TURNOVER} >= session quantile {LIQUID_QUANTILE:.4f}",
                  "model": "HistGradientBoostingRegressor, Phase 2R M1 config, seed 42",
                  "refit_reason": "Phase 2R.1 persisted metrics only; predictions needed",
                  "cost_convention": "ROUND-TRIP basis points, applied to the traded fraction",
                  "s3_rules_fixed_in_advance": S3_RULES},
         "reproduction_check": {}, "S1": {}, "S2": {}, "S3": {}, "S4": {}}
    tables = []
    preds = {}          # year -> (session -> [(symbol, score, y, turnover, idx)])

    for year, (tr0, tr1, purge, va0, va1) in FOLDS.items():
        trm = np.fromiter((tr0 <= s <= tr1 for s in sess), bool, len(sess)) & keep
        vam = np.fromiter((va0 <= s <= va1 for s in sess), bool, len(sess)) & keep
        vas = [s for s in alls if va0 <= s <= va1]
        assert not any(s.startswith("2026") for s in vas)
        assert not (trm & vam).any()
        t = time.time()
        m = HistGradientBoostingRegressor(
            max_iter=100, learning_rate=0.1, max_leaf_nodes=31, min_samples_leaf=20,
            l2_regularization=0.0, early_stopping=False, random_state=SEED)
        m.fit(X[trm], y[trm])
        sc = np.full(len(y), np.nan)
        sc[vam] = m.predict(X[vam])

        ics = []
        by_s = {}
        for s in vas:
            ii = np.asarray(idx[s]); ii = ii[keep[ii]]
            if len(ii) < 50:
                continue
            ok = np.isfinite(sc[ii])
            ii2 = ii[ok]
            if len(ii2) < 50:
                continue
            ic = spearman(y[ii2], sc[ii2])
            if ic is not None:
                ics.append(ic)
            by_s[s] = ii2
        got = tstat(ics)["mean"]
        want = prior["folds"][str(year)]["arms"]["C_restricted"]["ic"]["mean"]
        R["reproduction_check"][str(year)] = {
            "phase2r1_ic": want, "reproduced_ic": got,
            "abs_diff": abs(got - want), "match": abs(got - want) < 1e-9}
        print(f"FOLD {year} refit {time.time()-t:.0f}s  IC={got:+.6f} "
              f"(2R.1 recorded {want:+.6f})  match={abs(got-want)<1e-9}", flush=True)
        preds[year] = (by_s, sc, m, trm, vam)

    assert all(v["match"] for v in R["reproduction_check"].values()), \
        "refit did not reproduce Phase 2R.1 — configuration drift"
    print("[repro] all four folds reproduce Phase 2R.1 exactly\n", flush=True)

    # ── S1 — cost sensitivity on an idealised close-to-close basket ─────────
    print("[S1] transaction-cost sensitivity (ROUND-TRIP bps)", flush=True)
    for k in KS:
        for year in FOLDS:
            by_s, sc, *_ = preds[year]
            ss = sorted(by_s)
            gross, overlap, part = [], [], []
            prev = set()
            for s in ss:
                ii = by_s[s]
                o = np.argsort(-sc[ii], kind="mergesort")[:k]
                sel = ii[o]
                gross.append(float(y[sel].mean()))
                cur = {syms[j] for j in sel}
                overlap.append(len(cur & prev) / k if prev else 0.0)
                prev = cur
            turn = [1.0 - o for o in overlap]
            g = curve_stats(gross)
            R["S1"].setdefault(f"top{k}", {})[str(year)] = {
                "gross": g, "mean_overlap_with_prev_session": st.mean(overlap),
                "mean_traded_fraction": st.mean(turn), "by_cost": {}}
            for bps in COSTS_BPS:
                c = bps / 10000.0
                net = [r - tf * c for r, tf in zip(gross, turn)]
                nstat = curve_stats(net)
                R["S1"][f"top{k}"][str(year)]["by_cost"][f"{bps}bps"] = nstat
                tables.append({"section": "S1", "k": k, "fold": year,
                               "cost_bps_roundtrip": bps,
                               "gross_mean": g["mean_session_return"],
                               "net_mean": nstat["mean_session_return"],
                               "net_total_return": nstat["total_return"],
                               "sharpe": nstat["sharpe_annualised"],
                               "profitable_sessions": nstat["profitable_session_share"],
                               "max_drawdown": nstat["max_drawdown"],
                               "traded_fraction": st.mean(turn)})
        print(f"   top{k} done", flush=True)

    # ── S2 — capacity, participation against avg_turnover_20d ──────────────
    print("\n[S2] capacity", flush=True)
    for k in KS:
        for cap in CAPITALS:
            per_pos = cap / k
            parts, flags = [], {f: 0 for f in PARTICIPATION_FLAGS}
            n = 0
            for year in FOLDS:
                by_s, sc, *_ = preds[year]
                for s, ii in by_s.items():
                    o = np.argsort(-sc[ii], kind="mergesort")[:k]
                    for j in ii[o]:
                        adv = float(X[j, jt])
                        if not math.isfinite(adv) or adv <= 0:
                            continue
                        p = per_pos / adv
                        parts.append(p); n += 1
                        for f in PARTICIPATION_FLAGS:
                            if p > f:
                                flags[f] += 1
            a = np.asarray(parts)
            R["S2"].setdefault(f"top{k}", {})[f"{cap:.0f}"] = {
                "capital": cap, "positions": k, "capital_per_position": per_pos,
                "participation_median": float(np.median(a)),
                "participation_p90": float(np.quantile(a, 0.90)),
                "participation_p99": float(np.quantile(a, 0.99)),
                "participation_max": float(a.max()),
                "picks_evaluated": n,
                "share_above_1pct_ADV": flags[0.01] / n,
                "share_above_5pct_ADV": flags[0.05] / n,
                "share_above_10pct_ADV": flags[0.10] / n}
            tables.append({"section": "S2", "k": k, "capital": cap,
                           "capital_per_position": per_pos,
                           "participation_median": float(np.median(a)),
                           "participation_p99": float(np.quantile(a, 0.99)),
                           "share_above_1pct_ADV": flags[0.01] / n,
                           "share_above_5pct_ADV": flags[0.05] / n})
        print(f"   top{k} done", flush=True)

    with open(os.path.join(args.out, "phase2s_results.json"), "w") as fh:
        json.dump(R, fh, indent=2, default=str, sort_keys=True)
    print("   [checkpoint] S1+S2 written", flush=True)

    # ── S3 — the 2025 market dependence ────────────────────────────────────
    print("\n[S3] 2025 market dependence", flush=True)
    neutral_all = [C.FEATURES.index(f) for f in SESSION_CONSTANT_MKT]
    for year in FOLDS:
        by_s, sc, m, trm, vam = preds[year]
        vas = sorted(by_s)
        base = {s: sc[by_s[s]] for s in vas}
        Xn = X[vam].copy()
        for j in neutral_all:
            Xn[:, j] = np.float32(np.nanmedian(X[trm, j].astype(float)))
        sn = np.full(len(y), np.nan)
        sn[vam] = m.predict(Xn)

        def ic_series(scores):
            out = {}
            for s in vas:
                ii = by_s[s]
                v = scores[ii]
                if np.isfinite(v).sum() >= 50:
                    r = spearman(y[ii], v)
                    if r is not None:
                        out[s] = r
            return out
        ic_a, ic_b = ic_series(sc), ic_series(sn)
        common = sorted(set(ic_a) & set(ic_b))
        d = [ic_a[s] - ic_b[s] for s in common]
        node = {"ic_original": tstat(list(ic_a.values())),
                "ic_neutralised": tstat(list(ic_b.values())),
                "delta_ic": tstat(d),
                "delta_concentration": {
                    "share_of_sum_from_top_10_sessions":
                        float(sum(sorted(d, reverse=True)[:10]) / sum(d)) if sum(d) else None,
                    "sessions_with_positive_delta": float(np.mean([x > 0 for x in d]))},
                "buckets": {}}

        # B1 / B2 — frozen binary flags
        for f in ("mkt_above_sma200", "mkt_above_sma50"):
            jf = C.FEATURES.index(f)
            for state in (0, 1):
                ssel = [s for s in common
                        if np.isfinite(X[by_s[s][0], jf])
                        and int(X[by_s[s][0], jf]) == state]
                if len(ssel) < 20:
                    continue
                node["buckets"][f"{f}={state}"] = {
                    "sessions": len(ssel),
                    "ic_original": tstat([ic_a[s] for s in ssel])["mean"],
                    "ic_neutralised": tstat([ic_b[s] for s in ssel])["mean"]}
        # B3 — mkt_vol_21d terciles from the TRAIN fold
        jv = C.FEATURES.index("mkt_vol_21d")
        tv = X[trm, jv].astype(float)
        tv = tv[np.isfinite(tv)]
        lo, hi = np.quantile(tv, 1 / 3), np.quantile(tv, 2 / 3)
        for lab, test in (("low", lambda v: v <= lo),
                          ("mid", lambda v: lo < v <= hi),
                          ("high", lambda v: v > hi)):
            ssel = [s for s in common if test(float(X[by_s[s][0], jv]))]
            if len(ssel) < 20:
                continue
            node["buckets"][f"mkt_vol_21d_{lab}"] = {
                "sessions": len(ssel),
                "ic_original": tstat([ic_a[s] for s in ssel])["mean"],
                "ic_neutralised": tstat([ic_b[s] for s in ssel])["mean"]}
        # B4 — calendar quarters
        for q, months in (("Q1", "01 02 03"), ("Q2", "04 05 06"),
                          ("Q3", "07 08 09"), ("Q4", "10 11 12")):
            ssel = [s for s in common if s[5:7] in months.split()]
            if len(ssel) < 20:
                continue
            node["buckets"][f"quarter_{q}"] = {
                "sessions": len(ssel),
                "ic_original": tstat([ic_a[s] for s in ssel])["mean"],
                "ic_neutralised": tstat([ic_b[s] for s in ssel])["mean"]}
        # B5 — one feature at a time
        node["single_feature_neutralisation"] = {}
        for f in SESSION_CONSTANT_MKT:
            j = C.FEATURES.index(f)
            X1 = X[vam].copy()
            X1[:, j] = np.float32(np.nanmedian(X[trm, j].astype(float)))
            s1 = np.full(len(y), np.nan)
            s1[vam] = m.predict(X1)
            node["single_feature_neutralisation"][f] = tstat(
                list(ic_series(s1).values()))["mean"]
        # B6 — excluding extreme predictions
        ic_trim = []
        for s in vas:
            ii = by_s[s]
            v = sc[ii]
            ok = np.isfinite(v)
            if ok.sum() < 100:
                continue
            q1, q9 = np.quantile(v[ok], 0.01), np.quantile(v[ok], 0.99)
            m2 = ok & (v > q1) & (v < q9)
            if m2.sum() >= 50:
                r = spearman(y[ii[m2]], v[m2])
                if r is not None:
                    ic_trim.append(r)
        node["ic_excluding_extreme_1pct"] = tstat(ic_trim)["mean"]
        # B7 — turnover terciles inside the liquid universe
        for lab, sel in (("liquid_low", 0), ("liquid_mid", 1), ("liquid_high", 2)):
            vals_a, vals_b = [], []
            for s in vas:
                ii = by_s[s]
                tvv = X[ii, jt].astype(float)
                ok = np.isfinite(tvv)
                if ok.sum() < 90:
                    continue
                q1, q2 = np.quantile(tvv[ok], 1 / 3), np.quantile(tvv[ok], 2 / 3)
                msk = (tvv <= q1) if sel == 0 else (tvv > q2) if sel == 2 else \
                      ((tvv > q1) & (tvv <= q2))
                if msk.sum() < 30:
                    continue
                ra = spearman(y[ii[msk]], sc[ii[msk]])
                rb = spearman(y[ii[msk]], sn[ii[msk]])
                if ra is not None:
                    vals_a.append(ra)
                if rb is not None:
                    vals_b.append(rb)
            node["buckets"][f"turnover_{lab}"] = {
                "sessions": len(vals_a),
                "ic_original": tstat(vals_a)["mean"],
                "ic_neutralised": tstat(vals_b)["mean"]}
        R["S3"][str(year)] = node
        print(f"   {year}: IC {node['ic_original']['mean']:+.4f} -> "
              f"{node['ic_neutralised']['mean']:+.4f}  "
              f"delta_t={node['delta_ic']['t']:+.2f}", flush=True)
        for f, v in node["single_feature_neutralisation"].items():
            print(f"      neutralise {f:20s} -> IC {v:+.4f}", flush=True)

    R["S4_note"] = ("arm comparisons are taken from the immutable Phase 2R.1 "
                    "results file and are not recomputed")
    with open(os.path.join(args.out, "phase2s_results.json"), "w") as fh:
        json.dump(R, fh, indent=2, default=str, sort_keys=True)
    keys = ["section", "k", "fold", "cost_bps_roundtrip", "capital",
            "capital_per_position", "gross_mean", "net_mean", "net_total_return",
            "sharpe", "profitable_sessions", "max_drawdown", "traded_fraction",
            "participation_median", "participation_p99", "share_above_1pct_ADV",
            "share_above_5pct_ADV"]
    with open(os.path.join(args.out, "phase2s_tables.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(tables)
    print(f"\n[out] {args.out}  ({len(tables)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
