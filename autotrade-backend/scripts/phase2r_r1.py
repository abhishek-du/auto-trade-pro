"""PHASE 2R — R1 controlled cross-sectional ranking experiment.

Two predefined models, the frozen 47 features, the frozen folds, the frozen
target. No hyperparameter search, no feature selection, no target transform,
and the 2026 shard is never opened.

Preprocessing is fitted on the TRAINING portion of each fold only. M1 needs no
imputation at all — HistGradientBoosting handles NaN natively — so the only
fitted preprocessing is M0's median imputer and scaler.
"""
from __future__ import annotations

import argparse, collections, csv, gzip, json, math, os, statistics as st, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import v1_contract as C          # noqa: E402
from scripts.phase2q_signal_shape import tstat, spearman, buckets   # noqa: E402
from scripts.fold_contract import FOLDS      # manifest-authoritative  # noqa: E402

HELD_OUT_YEAR = 2026
SEED = 42
TOPK = (5, 10, 20)
KEY = ["symbol", "prediction_session", "next_session_return", "extreme_return_flag"]


def load(dirpath: str):
    """Stream 2017-2025 into float32. Year >= 2026 is refused, not filtered.

    Parsed with pandas' C engine: the first version called float() per cell,
    which is 137M Python calls for 2.9M rows x 47 features and had not finished
    loading after ten minutes.
    """
    import pandas as pd
    man = json.load(open(os.path.join(dirpath, "manifest.json")))
    opened, skipped = [], []
    for e in man["files"]:
        year = int(e["file"].split("_")[-1].split(".")[0])
        (skipped if year >= HELD_OUT_YEAR else opened).append(e["file"])
    use = KEY + [c for c in C.FEATURES if c not in KEY]
    parts = []
    for f in opened:
        assert str(HELD_OUT_YEAR) not in f, f"held-out shard requested: {f}"
        d = pd.read_csv(os.path.join(dirpath, f), usecols=use,
                        dtype={c: "float32" for c in C.FEATURES})
        d = d[d["extreme_return_flag"] != 1]
        parts.append(d)
        print(f"   [load] {f}: {len(d):,}", flush=True)
    d = pd.concat(parts, ignore_index=True)
    del parts
    X = np.ascontiguousarray(d[C.FEATURES].to_numpy(dtype=np.float32))
    y = d["next_session_return"].to_numpy(dtype=np.float64)
    sess = d["prediction_session"].tolist()
    syms = d["symbol"].tolist()
    del d
    return X, y, sess, syms, opened, skipped, man


def session_metrics(scores, y, excess, idx_by_sess, order_sessions):
    """Per-session IC and top/bottom-k, aggregated across sessions."""
    ics, tk = [], {k: {"exc": [], "ret": [], "hit": []} for k in TOPK}
    bk = {k: {"exc": []} for k in TOPK}
    cov = []
    for s in order_sessions:
        ii = np.asarray(idx_by_sess[s])
        sc = scores[ii]
        ok = np.isfinite(sc)
        cov.append(float(ok.mean()))
        ii2, sc2 = ii[ok], sc[ok]
        if len(ii2) < 50:
            continue
        ic = spearman(y[ii2], sc2)
        if ic is not None:
            ics.append(ic)
        o = np.argsort(-sc2, kind="mergesort")
        for k in TOPK:
            if len(o) >= k:
                sel = ii2[o[:k]]
                tk[k]["exc"].append(float(excess[sel].mean()))
                tk[k]["ret"].append(float(y[sel].mean()))
                tk[k]["hit"].append(float((y[sel] > 0).mean()))
                bk[k]["exc"].append(float(excess[ii2[o[-k:]]].mean()))
    out = {"ic": tstat(ics), "coverage": st.mean(cov) if cov else None, "topk": {},
           "bottomk": {}}
    for k in TOPK:
        t = tstat(tk[k]["exc"])
        out["topk"][k] = {**t, "mean_raw_return": st.mean(tk[k]["ret"]) if tk[k]["ret"] else None,
                          "hit_rate": st.mean(tk[k]["hit"]) if tk[k]["hit"] else None}
        out["bottomk"][k] = tstat(bk[k]["exc"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.inspection import permutation_importance

    t0 = time.time()
    X, y, sess, syms, opened, skipped, man = load(args.dir)
    print(f"[load] opened={opened}")
    print(f"[load] skipped (held out / none)={skipped}")
    print(f"[load] X={X.shape} y={y.shape}  {time.time()-t0:.0f}s")
    assert not any(s.startswith("2026") for s in sess), "2026 row entered the matrix"

    idx_by_sess = collections.defaultdict(list)
    for i, s in enumerate(sess):
        idx_by_sess[s].append(i)
    all_sessions = sorted(idx_by_sess)
    excess = np.empty_like(y)
    for s in all_sessions:
        ii = idx_by_sess[s]
        excess[ii] = y[ii] - y[ii].mean()
    fi_col = {c: C.FEATURES.index(c) for c in
              ("return_1d", "return_21d", "breakout_20d", "volume_ratio_20d",
               "realised_vol_21d")}

    R = {"meta": {"dataset_digest": man["dataset_digest"],
                  "rows_loaded": int(X.shape[0]), "features": C.FEATURES,
                  "feature_count": len(C.FEATURES), "seed": SEED,
                  "shards_opened": opened, "shards_skipped": skipped,
                  "target": "next_session_return (frozen, untransformed)"},
         "models": {
            "M0": {"type": "sklearn.linear_model.Ridge",
                   "params": {"alpha": 1.0, "solver": "lsqr", "random_state": SEED},
                   "preprocessing": "SimpleImputer(median) + StandardScaler, "
                                    "both fitted on the TRAIN fold only"},
            "M1": {"type": "sklearn.ensemble.HistGradientBoostingRegressor",
                   "params": {"max_iter": 100, "learning_rate": 0.1,
                              "max_leaf_nodes": 31, "min_samples_leaf": 20,
                              "l2_regularization": 0.0, "early_stopping": False,
                              "random_state": SEED},
                   "preprocessing": "none — NaN handled natively; no scaling needed"}},
         "folds": {}}
    rows_csv, imp_csv = [], []

    for year, (tr0, tr1, purge, va0, va1) in FOLDS.items():
        tr_mask = np.fromiter((tr0 <= s <= tr1 for s in sess), bool, len(sess))
        va_sessions = [s for s in all_sessions if va0 <= s <= va1]
        va_mask = np.fromiter((va0 <= s <= va1 for s in sess), bool, len(sess))
        assert not any(s.startswith("2026") for s in va_sessions)
        ntr, nva = int(tr_mask.sum()), int(va_mask.sum())
        print(f"\nFOLD {year}: train {tr0}..{tr1} ({ntr:,}) purge {purge} "
              f"validate {va0}..{va1} ({nva:,}, {len(va_sessions)} sessions)")
        assert not tr_mask[va_mask].any()

        Xtr, ytr = X[tr_mask], y[tr_mask]
        fold = {"train_rows": ntr, "validation_rows": nva,
                "validation_sessions": len(va_sessions), "purged_session": purge,
                "train_range": [tr0, tr1], "validation_range": [va0, va1],
                "results": {}}

        # ── M0 ──────────────────────────────────────────────────────────────
        t = time.time()
        imp = SimpleImputer(strategy="median").fit(Xtr)
        sc = StandardScaler().fit(imp.transform(Xtr))
        m0 = Ridge(alpha=1.0, solver="lsqr", random_state=SEED)
        m0.fit(sc.transform(imp.transform(Xtr)), ytr)
        s0 = np.full(len(y), np.nan)
        s0[va_mask] = m0.predict(sc.transform(imp.transform(X[va_mask])))
        fold["results"]["M0"] = session_metrics(s0, y, excess, idx_by_sess, va_sessions)
        print(f"   M0 ridge      {time.time()-t:6.0f}s  IC={fold['results']['M0']['ic']['mean']:+.4f} "
              f"top10={fold['results']['M0']['topk'][10]['mean']:+.5f}")

        # ── M1 ──────────────────────────────────────────────────────────────
        t = time.time()
        m1 = HistGradientBoostingRegressor(
            max_iter=100, learning_rate=0.1, max_leaf_nodes=31,
            min_samples_leaf=20, l2_regularization=0.0,
            early_stopping=False, random_state=SEED)
        m1.fit(Xtr, ytr)
        s1 = np.full(len(y), np.nan)
        s1[va_mask] = m1.predict(X[va_mask])
        fold["results"]["M1"] = session_metrics(s1, y, excess, idx_by_sess, va_sessions)
        print(f"   M1 hist-gbr   {time.time()-t:6.0f}s  IC={fold['results']['M1']['ic']['mean']:+.4f} "
              f"top10={fold['results']['M1']['topk'][10]['mean']:+.5f}")

        # ── benchmarks on the same sessions ────────────────────────────────
        b6 = X[:, C.FEATURES.index("return_21d")].astype(float)
        b6s = np.full(len(y), np.nan); b6s[va_mask] = b6[va_mask]
        fold["results"]["B6_momentum_rank"] = session_metrics(
            b6s, y, excess, idx_by_sess, va_sessions)
        b0 = np.zeros(len(y)); b0[~va_mask] = np.nan
        fold["results"]["B0_zero"] = session_metrics(b0, y, excess, idx_by_sess, va_sessions)

        for name, col, sel in (("C1_breakout", "breakout_20d", None),
                               ("C2_lowmom_highvol", None, "c2")):
            per = []
            for s in va_sessions:
                ii = np.asarray(idx_by_sess[s])
                if sel is None:
                    v = X[ii, C.FEATURES.index(col)]
                    m = np.isfinite(v) & (v == 1.0)
                else:
                    mom = X[ii, C.FEATURES.index("return_21d")]
                    vol = X[ii, C.FEATURES.index("volume_ratio_20d")]
                    ok = np.isfinite(mom) & np.isfinite(vol)
                    if ok.sum() < 50:
                        continue
                    m = np.zeros(len(ii), bool)
                    m[np.flatnonzero(ok)[(buckets(mom[ok]) == 0) & (buckets(vol[ok]) == 2)]] = True
                if m.sum() >= 3:
                    per.append(float(excess[ii[m]].mean()))
            fold["results"][name] = {"group_excess": tstat(per)}

        for mname, res in fold["results"].items():
            base = {"fold": year, "model": mname}
            if "ic" in res:
                rows_csv.append({**base, "metric": "ic", "value": res["ic"]["mean"],
                                 "t": res["ic"]["t"], "p": res["ic"]["p"],
                                 "sessions": res["ic"]["n"],
                                 "pos_share": res["ic"]["pos_share"]})
                for k in TOPK:
                    tkk = res["topk"][k]
                    rows_csv.append({**base, "metric": f"top{k}_excess",
                                     "value": tkk["mean"], "t": tkk["t"], "p": tkk["p"],
                                     "sessions": tkk["n"], "pos_share": tkk["pos_share"],
                                     "median": tkk["median"], "worst": tkk["worst"],
                                     "hit_rate": tkk["hit_rate"],
                                     "mean_raw_return": tkk["mean_raw_return"]})
                    bkk = res["bottomk"][k]
                    rows_csv.append({**base, "metric": f"bottom{k}_excess",
                                     "value": bkk["mean"], "t": bkk["t"], "p": bkk["p"],
                                     "sessions": bkk["n"]})
            else:
                g = res["group_excess"]
                rows_csv.append({**base, "metric": "group_excess", "value": g["mean"],
                                 "t": g["t"], "p": g["p"], "sessions": g["n"],
                                 "pos_share": g["pos_share"]})

        # ── feature importance, diagnostic only ────────────────────────────
        rng = np.random.default_rng(SEED)
        vi = np.flatnonzero(va_mask)
        sub = rng.choice(vi, size=min(30000, len(vi)), replace=False)
        pi = permutation_importance(m1, X[sub], y[sub], n_repeats=3,
                                    random_state=SEED, scoring="neg_mean_squared_error")
        order = np.argsort(-pi.importances_mean)
        fold["m1_feature_importance_top15"] = [
            {"feature": C.FEATURES[j], "importance": float(pi.importances_mean[j]),
             "std": float(pi.importances_std[j])} for j in order[:15]]
        tot = float(np.abs(pi.importances_mean).sum()) or 1.0
        fold["m1_importance_share_top1"] = float(pi.importances_mean[order[0]] / tot)
        for j in order:
            imp_csv.append({"fold": year, "feature": C.FEATURES[j],
                            "importance": float(pi.importances_mean[j]),
                            "std": float(pi.importances_std[j]),
                            "share_of_total": float(pi.importances_mean[j] / tot)})
        print(f"   M1 top features: "
              f"{[C.FEATURES[j] for j in order[:5]]}  "
              f"top1 share={fold['m1_importance_share_top1']:.1%}")
        for c, j in fi_col.items():
            fold.setdefault("m1_importance_inspected", {})[c] = {
                "rank": int(np.where(order == j)[0][0]) + 1,
                "importance": float(pi.importances_mean[j])}
        R["folds"][str(year)] = fold

    with open(os.path.join(args.out, "phase2r_model_results.json"), "w") as fh:
        json.dump(R, fh, indent=2, default=str, sort_keys=True)
    keys = ["fold", "model", "metric", "value", "median", "t", "p", "sessions",
            "pos_share", "hit_rate", "worst", "mean_raw_return"]
    with open(os.path.join(args.out, "phase2r_model_results.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(rows_csv)
    with open(os.path.join(args.out, "phase2r_feature_importance.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["fold", "feature", "importance", "std",
                                           "share_of_total"])
        w.writeheader(); w.writerows(imp_csv)
    print(f"\n[out] {args.out}  ({len(rows_csv)} metric rows, {len(imp_csv)} importance rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
