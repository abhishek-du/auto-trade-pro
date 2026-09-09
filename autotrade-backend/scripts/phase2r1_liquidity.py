"""PHASE 2R.1 — liquidity robustness + market-proxy neutralisation diagnostic.

Nothing is tuned, no feature is added or removed, no target is transformed, the
folds are the frozen ones, and the 2026 shard is never opened. M1 is the same
predefined configuration as Phase 2R.

LIQUIDITY-RESTRICTED UNIVERSE (PIT-safe by construction)
--------------------------------------------------------
For each prediction session D, keep the rows whose frozen `avg_turnover_20d`
lies in the TOP TERCILE of that session's cross-section:

    keep(i) iff avg_turnover_20d[i] >= quantile(avg_turnover_20d[session(D)], 2/3)

`avg_turnover_20d` is a 20-session rolling mean over sessions <= D, and the
percentile is taken across stocks WITHIN the same session — contemporaneous
values only. No future information and no cross-session threshold enters, so
the restriction cannot leak. It is a filter over the frozen dataset, not a new
feature: nothing is written back.

Three arms are run per fold:
  A  UNRESTRICTED   — the Phase 2R result, refitted for a like-for-like baseline
  B  EVAL-ONLY      — arm A's model, scored only on liquid rows (is the signal
                      present in liquid names, even when trained on everything?)
  C  RESTRICTED     — trained AND evaluated on liquid rows only (the real test)
"""
from __future__ import annotations

import argparse, collections, csv, json, os, statistics as st, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import v1_contract as C          # noqa: E402
from scripts.phase2q_signal_shape import tstat, spearman, buckets   # noqa: E402
from scripts.phase2r_r1 import load, SEED, TOPK, HELD_OUT_YEAR  # noqa: E402
from scripts.fold_contract import FOLDS      # manifest-authoritative  # noqa: E402

TURNOVER = "avg_turnover_20d"
LIQUID_QUANTILE = 2.0 / 3.0
# market features that are identical for every stock in a session (verified at
# runtime). rel_strength_21d and beta_63d are stock-specific and are NOT touched.
SESSION_CONSTANT_MKT = ["mkt_return_1d", "mkt_return_5d", "mkt_return_21d",
                        "mkt_vol_21d", "mkt_above_sma50", "mkt_above_sma200"]


def metrics(scores, y, excess, idx_by_sess, order_sessions, keep=None):
    ics, tk, bk, cov = [], {k: {"e": [], "r": [], "h": []} for k in TOPK}, \
                       {k: [] for k in TOPK}, []
    for s in order_sessions:
        ii = np.asarray(idx_by_sess[s])
        if keep is not None:
            ii = ii[keep[ii]]
        if len(ii) < 50:
            continue
        sc = scores[ii]
        ok = np.isfinite(sc)
        cov.append(float(ok.mean()))
        ii2, sc2 = ii[ok], sc[ok]
        if len(ii2) < 50:
            continue
        # excess is recomputed WITHIN the evaluated universe: a liquid-only
        # benchmark must be the liquid cross-section, not the whole market.
        loc_exc = y[ii2] - y[ii2].mean()
        ic = spearman(y[ii2], sc2)
        if ic is not None:
            ics.append(ic)
        o = np.argsort(-sc2, kind="mergesort")
        for k in TOPK:
            if len(o) >= k:
                tk[k]["e"].append(float(loc_exc[o[:k]].mean()))
                tk[k]["r"].append(float(y[ii2[o[:k]]].mean()))
                tk[k]["h"].append(float((y[ii2[o[:k]]] > 0).mean()))
                bk[k].append(float(loc_exc[o[-k:]].mean()))
    out = {"ic": tstat(ics), "coverage": st.mean(cov) if cov else None,
           "topk": {}, "bottomk": {}}
    for k in TOPK:
        t = tstat(tk[k]["e"])
        out["topk"][k] = {**t, "mean_raw_return": st.mean(tk[k]["r"]) if tk[k]["r"] else None,
                          "hit_rate": st.mean(tk[k]["h"]) if tk[k]["h"] else None}
        out["bottomk"][k] = tstat(bk[k])
    return out


def group_excess(mask_fn, y, X, idx_by_sess, order_sessions, keep=None):
    per = []
    for s in order_sessions:
        ii = np.asarray(idx_by_sess[s])
        if keep is not None:
            ii = ii[keep[ii]]
        if len(ii) < 50:
            continue
        m = mask_fn(ii, X)
        if m is None or m.sum() < 3:
            continue
        loc = y[ii] - y[ii].mean()
        per.append(float(loc[m].mean()))
    return tstat(per)


def _flush(out, R, rows_csv):
    with open(os.path.join(out, "phase2r1_results.json"), "w") as fh:
        json.dump(R, fh, indent=2, default=str, sort_keys=True)
    keys = ["fold", "arm", "metric", "value", "median", "std", "t", "p", "sessions",
            "pos_share", "hit_rate", "worst"]
    with open(os.path.join(out, "phase2r1_results.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(rows_csv)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    from sklearn.ensemble import HistGradientBoostingRegressor

    X, y, sess, syms, opened, skipped, man = load(args.dir)
    assert not any(s.startswith("2026") for s in sess)
    print(f"[load] X={X.shape} opened={len(opened)} skipped={skipped}", flush=True)

    idx = collections.defaultdict(list)
    for i, s in enumerate(sess):
        idx[s].append(i)
    alls = sorted(idx)
    excess = np.empty_like(y)
    for s in alls:
        ii = idx[s]
        excess[ii] = y[ii] - y[ii].mean()

    # ── PIT-safe liquidity mask ─────────────────────────────────────────────
    jt = C.FEATURES.index(TURNOVER)
    keep = np.zeros(len(y), bool)
    kept_frac = []
    for s in alls:
        ii = np.asarray(idx[s])
        tv = X[ii, jt].astype(float)
        ok = np.isfinite(tv)
        if ok.sum() < 50:
            continue
        thr = np.quantile(tv[ok], LIQUID_QUANTILE)
        sel = ok & (tv >= thr)
        keep[ii[sel]] = True
        kept_frac.append(float(sel.mean()))
    print(f"[universe] liquid rows {keep.sum():,} of {len(keep):,} "
          f"({keep.mean():.1%}); mean per-session share {st.mean(kept_frac):.3f}",
          flush=True)

    # verify which market features really are session-constant
    const_check = {}
    probe = alls[len(alls) // 2]
    pi = np.asarray(idx[probe])
    for f in C.MARKET_FEATURES:
        v = X[pi, C.FEATURES.index(f)].astype(float)
        v = v[np.isfinite(v)]
        const_check[f] = bool(len(np.unique(v)) <= 1)
    print(f"[probe {probe}] session-constant market features: "
          f"{[k for k, v in const_check.items() if v]}", flush=True)
    print(f"[probe {probe}] stock-specific market features:   "
          f"{[k for k, v in const_check.items() if not v]}", flush=True)
    neutral_cols = [C.FEATURES.index(f) for f in SESSION_CONSTANT_MKT]

    R = {"meta": {"dataset_digest": man["dataset_digest"],
                  "rows_loaded": int(X.shape[0]), "features": len(C.FEATURES),
                  "seed": SEED, "shards_opened": opened, "shards_skipped": skipped,
                  "liquidity_rule": (f"{TURNOVER} >= per-session quantile "
                                     f"{LIQUID_QUANTILE:.4f} (top tercile)"),
                  "liquid_rows": int(keep.sum()),
                  "liquid_share": float(keep.mean()),
                  "session_constant_market_features": const_check,
                  "neutralised_features": SESSION_CONSTANT_MKT},
         "folds": {}}
    rows_csv = []

    def mk_model():
        return HistGradientBoostingRegressor(
            max_iter=100, learning_rate=0.1, max_leaf_nodes=31,
            min_samples_leaf=20, l2_regularization=0.0,
            early_stopping=False, random_state=SEED)

    for year, (tr0, tr1, purge, va0, va1) in FOLDS.items():
        trm = np.fromiter((tr0 <= s <= tr1 for s in sess), bool, len(sess))
        vam = np.fromiter((va0 <= s <= va1 for s in sess), bool, len(sess))
        vas = [s for s in alls if va0 <= s <= va1]
        assert not any(s.startswith("2026") for s in vas)
        assert not (trm & vam).any()
        fold = {"train_range": [tr0, tr1], "purged_session": purge,
                "validation_range": [va0, va1],
                "train_rows_all": int(trm.sum()),
                "train_rows_liquid": int((trm & keep).sum()),
                "validation_rows_all": int(vam.sum()),
                "validation_rows_liquid": int((vam & keep).sum()),
                "validation_sessions": len(vas), "arms": {}}
        print(f"\nFOLD {year}  train {fold['train_rows_all']:,} "
              f"(liquid {fold['train_rows_liquid']:,})  "
              f"val {fold['validation_rows_all']:,} "
              f"(liquid {fold['validation_rows_liquid']:,})", flush=True)

        # ── arm A: unrestricted (like-for-like Phase 2R baseline) ──────────
        t = time.time()
        mA = mk_model(); mA.fit(X[trm], y[trm])
        sA = np.full(len(y), np.nan); sA[vam] = mA.predict(X[vam])
        fold["arms"]["A_unrestricted"] = metrics(sA, y, excess, idx, vas)
        print(f"   A unrestricted  {time.time()-t:5.0f}s "
              f"IC={fold['arms']['A_unrestricted']['ic']['mean']:+.4f} "
              f"top10={fold['arms']['A_unrestricted']['topk'][10]['mean']:+.5f}", flush=True)

        # ── arm B: same model, scored on liquid rows only ──────────────────
        fold["arms"]["B_eval_only_liquid"] = metrics(sA, y, excess, idx, vas, keep=keep)
        print(f"   B eval-on-liquid      IC="
              f"{fold['arms']['B_eval_only_liquid']['ic']['mean']:+.4f} "
              f"top10={fold['arms']['B_eval_only_liquid']['topk'][10]['mean']:+.5f}", flush=True)

        # ── arm C: trained AND evaluated on liquid rows ────────────────────
        t = time.time()
        trL = trm & keep
        mC = mk_model(); mC.fit(X[trL], y[trL])
        sC = np.full(len(y), np.nan)
        vl = vam & keep
        sC[vl] = mC.predict(X[vl])
        fold["arms"]["C_restricted"] = metrics(sC, y, excess, idx, vas, keep=keep)
        print(f"   C restricted    {time.time()-t:5.0f}s "
              f"IC={fold['arms']['C_restricted']['ic']['mean']:+.4f} "
              f"top10={fold['arms']['C_restricted']['topk'][10]['mean']:+.5f}", flush=True)

        # ── market-proxy neutralisation, on BOTH arms ─────────────────────
        for tag, model, mask, kp in (("A_unrestricted", mA, vam, None),
                                     ("C_restricted", mC, vl, keep)):
            Xn = X[mask].copy()
            for j in neutral_cols:
                col = X[trm if tag == "A_unrestricted" else trL, j].astype(float)
                Xn[:, j] = np.float32(np.nanmedian(col))     # train-fold constant
            sn = np.full(len(y), np.nan); sn[mask] = model.predict(Xn)
            mn = metrics(sn, y, excess, idx, vas, keep=kp)
            # how much does the RANKING move?
            rho = []
            for s in vas:
                ii = np.asarray(idx[s])
                if kp is not None:
                    ii = ii[kp[ii]]
                a, b = sA if tag == "A_unrestricted" else sC, sn
                o = np.isfinite(a[ii]) & np.isfinite(b[ii])
                if o.sum() >= 50:
                    r = spearman(a[ii][o], b[ii][o])
                    if r is not None:
                        rho.append(r)
            fold["arms"][f"{tag}__market_neutralised"] = {
                **mn, "rank_corr_with_original": tstat(rho)}
            print(f"   {tag} market-neutralised  IC={mn['ic']['mean']:+.4f} "
                  f"top10={mn['topk'][10]['mean']:+.5f}  "
                  f"rank_corr_vs_original={st.mean(rho):.4f}", flush=True)

        # ── benchmarks recomputed inside the restricted universe ──────────
        j21 = C.FEATURES.index("return_21d")
        jbo = C.FEATURES.index("breakout_20d")
        jvr = C.FEATURES.index("volume_ratio_20d")
        b6 = np.full(len(y), np.nan); b6[vl] = X[vl, j21]
        fold["arms"]["B6_restricted"] = metrics(b6, y, excess, idx, vas, keep=keep)
        fold["arms"]["C1_restricted"] = {"group_excess": group_excess(
            lambda ii, XX: (XX[ii, jbo] == 1.0), y, X, idx, vas, keep=keep)}
        def c2(ii, XX):
            m0, v0 = XX[ii, j21].astype(float), XX[ii, jvr].astype(float)
            ok = np.isfinite(m0) & np.isfinite(v0)
            if ok.sum() < 50:
                return None
            out = np.zeros(len(ii), bool)
            out[np.flatnonzero(ok)[(buckets(m0[ok]) == 0) & (buckets(v0[ok]) == 2)]] = True
            return out
        fold["arms"]["C2_restricted"] = {"group_excess": group_excess(
            c2, y, X, idx, vas, keep=keep)}

        # ── liquidity profile of the restricted model's picks ─────────────
        pt, at = [], []
        for s in vas:
            ii = np.asarray(idx[s]); ii = ii[keep[ii]]
            if len(ii) < 50:
                continue
            sc = sC[ii]; ok = np.isfinite(sc)
            ii2 = ii[ok]
            if len(ii2) < 10:
                continue
            sel = ii2[np.argsort(-sc[ok], kind="mergesort")[:10]]
            pt.append(float(np.nanmedian(X[sel, jt])))
            at.append(float(np.nanmedian(X[np.asarray(idx[s]), jt])))
        fold["restricted_pick_liquidity"] = {
            "median_turnover_picks": float(np.median(pt)),
            "median_turnover_full_cross_section": float(np.median(at)),
            "ratio": float(np.median(pt) / np.median(at))}
        print(f"   C picks median turnover = {np.median(pt):,.0f} vs full "
              f"cross-section {np.median(at):,.0f} (ratio "
              f"{np.median(pt)/np.median(at):.2f})", flush=True)

        for arm, res in fold["arms"].items():
            base = {"fold": year, "arm": arm}
            if "ic" in res:
                ic = res["ic"]
                rows_csv.append({**base, "metric": "ic", "value": ic["mean"],
                                 "median": ic["median"], "std": ic["std"],
                                 "t": ic["t"], "p": ic["p"], "sessions": ic["n"],
                                 "pos_share": ic["pos_share"]})
                for k in TOPK:
                    tt = res["topk"][k]
                    rows_csv.append({**base, "metric": f"top{k}_excess",
                                     "value": tt["mean"], "median": tt["median"],
                                     "t": tt["t"], "p": tt["p"], "sessions": tt["n"],
                                     "pos_share": tt["pos_share"],
                                     "hit_rate": tt["hit_rate"], "worst": tt["worst"]})
                    bb = res["bottomk"][k]
                    rows_csv.append({**base, "metric": f"bottom{k}_excess",
                                     "value": bb["mean"], "t": bb["t"], "p": bb["p"]})
            else:
                g = res["group_excess"]
                rows_csv.append({**base, "metric": "group_excess", "value": g["mean"],
                                 "t": g["t"], "p": g["p"], "sessions": g["n"],
                                 "pos_share": g["pos_share"]})
        R["folds"][str(year)] = fold
        _flush(args.out, R, rows_csv)
        print(f"   [checkpoint] wrote results through fold {year}", flush=True)
        del mA, mC, sA, sC

    print(f"\n[out] {args.out}  ({len(rows_csv)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
