"""PRE-2026 BOUNDARY CORRECTION — recompute the affected fold-2025 metrics.

The V1 row for prediction_session 2025-12-31 has target_session 2026-01-01, so
its target is a function of a 2026 close. 1,914 such rows exist in the 2025
shard (638 of them in the liquid universe). They were included in the fold-2025
evaluations of Phases 2R, 2R.1 and 2S.

CORRECTED BOUNDARY
    prediction_session <= 2025-12-30   AND   target_session <= 2025-12-31

TRAINING IS UNAFFECTED. Fold 2025 trains on 2017-06-28 .. 2024-12-30, which
contains none of these rows, so the fitted models are identical and only the
evaluation population changes. Each metric is computed TWICE — with the session
(to reproduce the published number, proving the recomputation is faithful) and
without it (the correction).

Nothing is retrained, tuned, or redefined. The V1 dataset is not written to.
Existing phase outputs are not overwritten.
"""
from __future__ import annotations

import argparse, collections, csv, json, os, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import v1_contract as C                      # noqa: E402
from scripts.phase2q_signal_shape import tstat, spearman, buckets   # noqa: E402
from scripts.phase2r_r1 import (load, SEED, TOPK,        # noqa: E402
                                session_metrics as metrics_2R)
from scripts.phase2r1_liquidity import (TURNOVER, LIQUID_QUANTILE,  # noqa: E402
                                        SESSION_CONSTANT_MKT,
                                        metrics as metrics_2R1, group_excess)
from scripts.phase2s_economics import curve_stats, COSTS_BPS, KS as S1_KS  # noqa: E402

BAD_SESSION = "2025-12-31"
YEAR = 2025

# The fold boundaries are read from the IMMUTABLE phase manifests, not from
# scripts/phase2r_r1.py. That file was modified at 19:18 on 2026-09-09 — during
# this correction's first run — and its FOLDS dict now carries only fold 2025
# with va1 = 2025-12-30. Sourcing from it silently excluded the very session
# under audit and made the comparison vacuous. The manifests record what the
# published runs actually used: validation 2025-01-01 .. 2025-12-31, 249
# sessions, 454,872 rows.
def authoritative_folds(dirpath):
    m = json.load(open(os.path.join(dirpath, "phase2r", "phase2r_manifest.json")))
    out = {}
    for k, v in m["folds"].items():
        out[int(k)] = (v["train"][0], v["train"][1], v["purge"],
                       v["validation"][0], v["validation"][1])
    return out


def delta(orig, corr):
    if orig is None or corr is None:
        return {"original": orig, "corrected": corr, "abs_diff": None, "pct_diff": None}
    d = corr - orig
    return {"original": orig, "corrected": corr, "abs_diff": d,
            "pct_diff": (d / abs(orig) * 100.0) if orig else None}


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

    X, y, sess, syms, opened, skipped, man = load(args.dir)
    assert not any(s.startswith("2026") for s in sess)
    idx = collections.defaultdict(list)
    for i, s in enumerate(sess):
        idx[s].append(i)
    alls = sorted(idx)
    jt = C.FEATURES.index(TURNOVER)
    keep = np.zeros(len(y), bool)
    for s in alls:
        ii = np.asarray(idx[s]); tv = X[ii, jt].astype(float); ok = np.isfinite(tv)
        if ok.sum() < 50:
            continue
        keep[ii[ok & (tv >= np.quantile(tv[ok], LIQUID_QUANTILE))]] = True

    FOLDS_AUTH = authoritative_folds(args.dir)
    tr0, tr1, purge, va0, va1 = FOLDS_AUTH[YEAR]
    assert va1 == "2025-12-31", f"authoritative fold end is {va1}, expected 2025-12-31"
    print(f"[folds] authoritative fold 2025 from phase2r_manifest: "
          f"train {tr0}..{tr1} purge {purge} validate {va0}..{va1}")
    trm_all = np.fromiter((tr0 <= s <= tr1 for s in sess), bool, len(sess))
    vam_all = np.fromiter((va0 <= s <= va1 for s in sess), bool, len(sess))
    assert BAD_SESSION not in {s for s in sess if trm_all[list(idx[s])[0]]}, \
        "the excluded session must not be in the training block"
    vas_orig = [s for s in alls if va0 <= s <= va1]
    vas_corr = [s for s in vas_orig if s != BAD_SESSION]
    removed_rows_all = int(sum(1 for s in sess if s == BAD_SESSION))
    removed_rows_liq = int(sum(1 for i, s in enumerate(sess)
                               if s == BAD_SESSION and keep[i]))
    print(f"[scope] validation sessions {len(vas_orig)} -> {len(vas_corr)}")
    print(f"[scope] rows removed: all-universe {removed_rows_all:,}, "
          f"liquid {removed_rows_liq:,}")
    print(f"[scope] training block unaffected: {tr0} .. {tr1}")

    excess_all = np.empty_like(y)
    for s in alls:
        ii = idx[s]
        excess_all[ii] = y[ii] - y[ii].mean()

    R = {"meta": {
        "dataset_digest": man["dataset_digest"],
        "excluded_prediction_session": BAD_SESSION,
        "excluded_target_session": "2026-01-01",
        "rule": "prediction_session <= 2025-12-30 AND target_session <= 2025-12-31",
        "rows_removed_all_universe": removed_rows_all,
        "rows_removed_liquid_universe": removed_rows_liq,
        "sessions_removed": 1,
        "validation_sessions_before": len(vas_orig),
        "validation_sessions_after": len(vas_corr),
        "training_block": [tr0, tr1],
        "fold_source": "datasets/v1_baseline/phase2r/phase2r_manifest.json (immutable)",
        "fold_2025_validation": [va0, va1],
        "training_affected": False,
        "models_retrained": False,
        "models_refit_for_reproduction": True,
        "shards_skipped": skipped}, "phases": {}}

    # ── fit once; both evaluations use the SAME fitted model ───────────────
    t = time.time()
    m1_all = HistGradientBoostingRegressor(
        max_iter=100, learning_rate=0.1, max_leaf_nodes=31, min_samples_leaf=20,
        l2_regularization=0.0, early_stopping=False, random_state=SEED)
    m1_all.fit(X[trm_all], y[trm_all])
    s_unres = np.full(len(y), np.nan)
    s_unres[vam_all] = m1_all.predict(X[vam_all])
    print(f"[fit] unrestricted M1 {time.time()-t:.0f}s")

    imp = SimpleImputer(strategy="median").fit(X[trm_all])
    scl = StandardScaler().fit(imp.transform(X[trm_all]))
    m0 = Ridge(alpha=1.0, solver="lsqr", random_state=SEED)
    m0.fit(scl.transform(imp.transform(X[trm_all])), y[trm_all])
    s_m0 = np.full(len(y), np.nan)
    s_m0[vam_all] = m0.predict(scl.transform(imp.transform(X[vam_all])))
    print("[fit] M0 ridge done")

    trL = trm_all & keep
    vamL = vam_all & keep
    t = time.time()
    m1_liq = HistGradientBoostingRegressor(
        max_iter=100, learning_rate=0.1, max_leaf_nodes=31, min_samples_leaf=20,
        l2_regularization=0.0, early_stopping=False, random_state=SEED)
    m1_liq.fit(X[trL], y[trL])
    s_liq = np.full(len(y), np.nan)
    s_liq[vamL] = m1_liq.predict(X[vamL])
    print(f"[fit] restricted M1 {time.time()-t:.0f}s")

    b6 = np.full(len(y), np.nan)
    b6[vam_all] = X[vam_all, C.FEATURES.index("return_21d")]
    b6L = np.full(len(y), np.nan)
    b6L[vamL] = X[vamL, C.FEATURES.index("return_21d")]

    # ── PHASE 2R — unrestricted fold 2025 ─────────────────────────────────
    p2r = {}
    for name, sc in (("M1", s_unres), ("M0", s_m0), ("B6_momentum_rank", b6)):
        o = metrics_2R(sc, y, excess_all, idx, vas_orig)
        c = metrics_2R(sc, y, excess_all, idx, vas_corr)
        p2r[name] = {"ic": delta(o["ic"]["mean"], c["ic"]["mean"]),
                     "ic_t": delta(o["ic"]["t"], c["ic"]["t"]),
                     "ic_pos_share": delta(o["ic"]["pos_share"], c["ic"]["pos_share"]),
                     "sessions": {"original": o["ic"]["n"], "corrected": c["ic"]["n"]}}
        for k in TOPK:
            p2r[name][f"top{k}_excess"] = delta(o["topk"][k]["mean"], c["topk"][k]["mean"])
            p2r[name][f"top{k}_t"] = delta(o["topk"][k]["t"], c["topk"][k]["t"])
    R["phases"]["2R_fold2025_unrestricted"] = p2r
    print(f"[2R]  M1 IC {p2r['M1']['ic']['original']:+.6f} -> "
          f"{p2r['M1']['ic']['corrected']:+.6f}")

    # ── PHASE 2R.1 — three arms + neutralisation, fold 2025 ───────────────
    p21 = {}
    for name, sc, kp in (("A_unrestricted", s_unres, None),
                         ("B_eval_only_liquid", s_unres, keep),
                         ("C_restricted", s_liq, keep),
                         ("B6_restricted", b6L, keep)):
        o = metrics_2R1(sc, y, excess_all, idx, vas_orig, keep=kp)
        c = metrics_2R1(sc, y, excess_all, idx, vas_corr, keep=kp)
        p21[name] = {"ic": delta(o["ic"]["mean"], c["ic"]["mean"]),
                     "ic_t": delta(o["ic"]["t"], c["ic"]["t"]),
                     "top10_excess": delta(o["topk"][10]["mean"], c["topk"][10]["mean"]),
                     "top5_excess": delta(o["topk"][5]["mean"], c["topk"][5]["mean"]),
                     "top20_excess": delta(o["topk"][20]["mean"], c["topk"][20]["mean"]),
                     "sessions": {"original": o["ic"]["n"], "corrected": c["ic"]["n"]}}
    # market neutralisation on arm C
    Xn = X[vamL].copy()
    for f in SESSION_CONSTANT_MKT:
        j = C.FEATURES.index(f)
        Xn[:, j] = np.float32(np.nanmedian(X[trL, j].astype(float)))
    sn = np.full(len(y), np.nan)
    sn[vamL] = m1_liq.predict(Xn)
    o = metrics_2R1(sn, y, excess_all, idx, vas_orig, keep=keep)
    c = metrics_2R1(sn, y, excess_all, idx, vas_corr, keep=keep)
    p21["C_restricted__market_neutralised"] = {
        "ic": delta(o["ic"]["mean"], c["ic"]["mean"]),
        "ic_t": delta(o["ic"]["t"], c["ic"]["t"]),
        "top10_excess": delta(o["topk"][10]["mean"], c["topk"][10]["mean"]),
        "sessions": {"original": o["ic"]["n"], "corrected": c["ic"]["n"]}}
    j21 = C.FEATURES.index("return_21d"); jbo = C.FEATURES.index("breakout_20d")
    jvr = C.FEATURES.index("volume_ratio_20d")
    for nm, fn in (("C1_restricted", lambda ii, XX: (XX[ii, jbo] == 1.0)),
                   ("C2_restricted", None)):
        def c2(ii, XX):
            m0_, v0 = XX[ii, j21].astype(float), XX[ii, jvr].astype(float)
            ok = np.isfinite(m0_) & np.isfinite(v0)
            if ok.sum() < 50:
                return None
            out = np.zeros(len(ii), bool)
            out[np.flatnonzero(ok)[(buckets(m0_[ok]) == 0) & (buckets(v0[ok]) == 2)]] = True
            return out
        f = fn or c2
        go = group_excess(f, y, X, idx, vas_orig, keep=keep)
        gc = group_excess(f, y, X, idx, vas_corr, keep=keep)
        p21[nm] = {"group_excess": delta(go["mean"], gc["mean"]),
                   "t": delta(go["t"], gc["t"]),
                   "sessions": {"original": go["n"], "corrected": gc["n"]}}
    R["phases"]["2R1_fold2025"] = p21
    print(f"[2R.1] C IC {p21['C_restricted']['ic']['original']:+.6f} -> "
          f"{p21['C_restricted']['ic']['corrected']:+.6f}")

    # ── PHASE 2S — S1 basket, fold 2025 (liquid, arm C scores) ────────────
    p2s = {"S1": {}, "S3": {}}
    for k in S1_KS:
        for lab, vs in (("original", vas_orig), ("corrected", vas_corr)):
            gross, overlap = [], []
            prev = set()
            for s in vs:
                ii = np.asarray(idx[s]); ii = ii[keep[ii]]
                ok = np.isfinite(s_liq[ii])
                ii2 = ii[ok]
                if len(ii2) < 50:
                    continue
                sel = ii2[np.argsort(-s_liq[ii2], kind="mergesort")[:k]]
                gross.append(float(y[sel].mean()))
                cur = {syms[j] for j in sel}
                overlap.append(len(cur & prev) / k if prev else 0.0)
                prev = cur
            g = curve_stats(gross)
            turn = [1.0 - o2 for o2 in overlap]
            net = {f"{b}bps": curve_stats([r - tf * b / 10000.0
                                           for r, tf in zip(gross, turn)])
                   for b in COSTS_BPS}
            p2s["S1"].setdefault(f"top{k}", {})[lab] = {"gross": g, "net": net}
        a = p2s["S1"][f"top{k}"]["original"]; b2 = p2s["S1"][f"top{k}"]["corrected"]
        p2s["S1"][f"top{k}"]["delta"] = {
            "gross_mean": delta(a["gross"]["mean_session_return"],
                                b2["gross"]["mean_session_return"]),
            "gross_sharpe": delta(a["gross"]["sharpe_annualised"],
                                  b2["gross"]["sharpe_annualised"]),
            "net_20bps_mean": delta(a["net"]["20bps"]["mean_session_return"],
                                    b2["net"]["20bps"]["mean_session_return"])}
    # S3: IC original vs neutralised, 2025
    for lab, vs in (("original", vas_orig), ("corrected", vas_corr)):
        p2s["S3"][lab] = {
            "ic_original": metrics_2R1(s_liq, y, excess_all, idx, vs, keep=keep)["ic"]["mean"],
            "ic_neutralised": metrics_2R1(sn, y, excess_all, idx, vs, keep=keep)["ic"]["mean"]}
    p2s["S3"]["delta"] = {
        "ic_original": delta(p2s["S3"]["original"]["ic_original"],
                             p2s["S3"]["corrected"]["ic_original"]),
        "ic_neutralised": delta(p2s["S3"]["original"]["ic_neutralised"],
                                p2s["S3"]["corrected"]["ic_neutralised"])}
    R["phases"]["2S_fold2025"] = p2s
    print(f"[2S]  top10 gross {p2s['S1']['top10']['delta']['gross_mean']['original']:+.6f}"
          f" -> {p2s['S1']['top10']['delta']['gross_mean']['corrected']:+.6f}")

    R["phases"]["2T0"] = {"affected": False,
        "reason": ("Phase 2T.0 already excluded prediction_session 2025-12-31 — it "
                   "is the phase that identified the condition. Its bar fetch was "
                   "bounded at 2025-12-31 and 638 liquid rows were dropped as "
                   "'target_beyond_firewall'. No recomputation required.")}
    R["phases"]["2S_execution_gap"] = {"affected": False,
        "reason": ("the execution diagnostic fetched bars only to 2025-12-31, so "
                   "picks on 2025-12-31 had no next bar and were already dropped "
                   "(reported as 20 unusable picks). No recomputation required.")}

    with open(os.path.join(args.out, "boundary_correction.json"), "w") as fh:
        json.dump(R, fh, indent=2, default=str, sort_keys=True)
    rows = []
    for phase, node in R["phases"].items():
        if not isinstance(node, dict) or node.get("affected") is False:
            continue
        for arm, d in node.items():
            if not isinstance(d, dict):
                continue
            for metric, v in d.items():
                if isinstance(v, dict) and "abs_diff" in v:
                    rows.append({"phase": phase, "arm": arm, "metric": metric,
                                 "original": v["original"], "corrected": v["corrected"],
                                 "abs_diff": v["abs_diff"], "pct_diff": v["pct_diff"]})
    with open(os.path.join(args.out, "boundary_correction.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["phase", "arm", "metric", "original",
                                           "corrected", "abs_diff", "pct_diff"])
        w.writeheader(); w.writerows(rows)
    print(f"\n[out] {args.out}  ({len(rows)} metric comparisons)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
