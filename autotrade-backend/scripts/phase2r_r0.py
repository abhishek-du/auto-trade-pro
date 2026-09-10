"""PHASE 2R — R0 attribution and robustness gate.

Analysis only, on the frozen V1 baseline. No model is fitted here, no feature
is added or removed, and the 2026 shard is never opened.

R0.1 asks whether C1 (`breakout_20d`) carries information beyond `return_1d`.
The decisive test is conditional, not correlational: inside each session,
stocks are bucketed by `return_1d` decile and breakout==1 is compared with
breakout==0 WITHIN each bucket. If breakout still pays once same-session return
is held fixed, it is not a repackaging of it.
"""
from __future__ import annotations

import argparse, collections, csv, json, math, os, statistics as st, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.phase2q_signal_shape import (      # noqa: E402
    HELD_OUT_YEAR, YEARS, PRIMARY_MOMENTUM, VOLUME_FEATURE, NEEDED,
    tstat, buckets, load,
)

FRICTIONS = (0.0005, 0.0010, 0.0015, 0.0020, 0.0025, 0.0030)


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
    sess_str = cols["prediction_session"]
    sym_arr = cols["symbol"]
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

    # ── R0.1 — is C1 independent of return_1d? ──────────────────────────────
    print("\n[R0.1] C1 (breakout_20d) vs return_1d")
    r01 = {}
    for scope in YEARS + ["combined"]:
        ss = [s for s in sessions if scope == "combined" or yr(s) == scope]
        c1_exc, r1_top_exc, overlap, xcorr = [], [], [], []
        ic_break, ic_r1 = [], []
        # conditional: breakout==1 vs ==0 inside each return_1d decile
        cond = {d: {"b1": [], "b0": []} for d in range(10)}
        for s in ss:
            ii = np.array(idx_by_sess[s])
            bo, r1 = feat["breakout_20d"][ii], feat["return_1d"][ii]
            ok = np.isfinite(bo) & np.isfinite(r1)
            ii2, bo2, r12 = ii[ok], bo[ok] == 1.0, r1[ok]
            if len(ii2) < 100 or bo2.sum() < 3:
                continue
            k = int(bo2.sum())
            c1_exc.append(float(excess[ii2[bo2]].mean()))
            top_r1 = ii2[np.argsort(-r12, kind="mergesort")[:k]]     # same count
            r1_top_exc.append(float(excess[top_r1].mean()))
            overlap.append(len(set(ii2[bo2]) & set(top_r1)) / k)
            xcorr.append(float(np.corrcoef(bo2.astype(float), r12)[0, 1]))
            from scripts.phase2q_signal_shape import spearman
            a = spearman(y[ii2], bo2.astype(float));  b = spearman(y[ii2], r12)
            if a is not None: ic_break.append(a)
            if b is not None: ic_r1.append(b)
            q = np.quantile(r12, np.arange(0.1, 1.0, 0.1))
            d = np.digitize(r12, q)
            for dd in range(10):
                m1 = (d == dd) & bo2
                m0 = (d == dd) & ~bo2
                if m1.sum() >= 3 and m0.sum() >= 3:
                    cond[dd]["b1"].append(float(excess[ii2[m1]].mean()))
                    cond[dd]["b0"].append(float(excess[ii2[m0]].mean()))
        t_c1, t_r1 = tstat(c1_exc), tstat(r1_top_exc)
        t_ib, t_ir = tstat(ic_break), tstat(ic_r1)
        conds = {}
        for dd in range(10):
            if not cond[dd]["b1"]:
                continue
            diff = [a - b for a, b in zip(cond[dd]["b1"], cond[dd]["b0"])]
            td = tstat(diff)
            conds[f"return_1d_D{dd+1}"] = {
                "sessions": td["n"], "breakout1_excess": st.mean(cond[dd]["b1"]),
                "breakout0_excess": st.mean(cond[dd]["b0"]),
                "difference": td["mean"], "t": td["t"], "p": td["p"],
                "positive_session_share": td["pos_share"]}
            tables.append({"question": "R0.1_conditional", "scope": scope,
                           "cell": f"return_1d_D{dd+1}", "sessions": td["n"],
                           "mean_excess": td["mean"], "t": td["t"], "p": td["p"],
                           "pos_session_share": td["pos_share"]})
        r01[str(scope)] = {
            "c1_excess": t_c1["mean"], "c1_t": t_c1["t"], "c1_p": t_c1["p"],
            "return_1d_matched_topN_excess": t_r1["mean"], "return_1d_t": t_r1["t"],
            "breakout_ic": t_ib["mean"], "breakout_ic_t": t_ib["t"],
            "return_1d_ic": t_ir["mean"], "return_1d_ic_t": t_ir["t"],
            "mean_cross_sectional_corr": st.mean(xcorr) if xcorr else None,
            "mean_selection_overlap": st.mean(overlap) if overlap else None,
            "conditional_by_return_1d_decile": conds}
        tables.append({"question": "R0.1", "scope": scope, "cell": "C1_breakout",
                       "sessions": t_c1["n"], "mean_excess": t_c1["mean"],
                       "t": t_c1["t"], "p": t_c1["p"]})
        tables.append({"question": "R0.1", "scope": scope,
                       "cell": "return_1d_matched_topN", "sessions": t_r1["n"],
                       "mean_excess": t_r1["mean"], "t": t_r1["t"], "p": t_r1["p"]})
        print(f"   {str(scope):9s} C1={t_c1['mean']:+.5f} r1_matched={t_r1['mean']:+.5f} "
              f"corr={st.mean(xcorr):+.3f} overlap={st.mean(overlap):.1%} "
              f"IC_break={t_ib['mean']:+.4f} IC_r1={t_ir['mean']:+.4f}")
    # verdict
    c = r01["combined"]
    _cd = c["conditional_by_return_1d_decile"].values()
    diffs = [v["difference"] for v in _cd if v["difference"] is not None]
    ps = [v["p"] for v in _cd if v["p"] is not None]
    pos = sum(1 for d in diffs if d > 0)
    sig = sum(1 for p in ps if p is not None and p < 0.05)
    nd = len(diffs)
    verdict = ("C1_INDEPENDENT" if nd >= 6 and pos >= 0.8 * nd and sig >= 0.6 * nd
               else "C1_REDUNDANT" if nd >= 6 and pos <= 0.3 * nd
               else "C1_UNPROVEN")
    r01["verdict"] = {"classification": verdict,
                      "deciles_evaluated": nd,
                      "deciles_with_positive_breakout_premium": pos,
                      "deciles_significant_p<0.05": sig,
                      "mean_conditional_premium": st.mean(diffs) if diffs else None}
    print(f"   VERDICT: {verdict}  ({pos}/{nd} deciles positive, {sig} significant, "
          f"mean premium {st.mean(diffs) if diffs else float('nan'):+.5f})")
    for k, v in c["conditional_by_return_1d_decile"].items():
        if v["difference"] is None:
            continue
        print(f"      {k:16s} breakout1={v['breakout1_excess']:+.5f} "
              f"breakout0={v['breakout0_excess']:+.5f} "
              f"diff={v['difference']:+.5f} t={v['t']:+6.2f} p={v['p']:.2e}")
    R["R0_1_c1_attribution"] = r01

    # ── R0.2 — C2 concentration ─────────────────────────────────────────────
    print("\n[R0.2] C2 concentration")
    per_sess, sess_names, sym_contrib = [], [], collections.Counter()
    yearly = collections.defaultdict(list)
    for s in sessions:
        ii = np.array(idx_by_sess[s])
        mom, vol = feat[PRIMARY_MOMENTUM][ii], feat[VOLUME_FEATURE][ii]
        ok = np.isfinite(mom) & np.isfinite(vol)
        ii2 = ii[ok]
        if len(ii2) < 50:
            continue
        m = (buckets(mom[ok]) == 0) & (buckets(vol[ok]) == 2)
        if m.sum() < 3:
            continue
        sel = ii2[m]
        e = float(excess[sel].mean())
        per_sess.append(e); sess_names.append(s); yearly[yr(s)].append(e)
        for j in sel:
            sym_contrib[sym_arr[j]] += float(excess[j]) / len(sel)
    S = len(per_sess)
    t = tstat(per_sess)
    order = np.argsort(per_sess)
    worst = [(sess_names[i], per_sess[i]) for i in order[:10]]
    best = [(sess_names[i], per_sess[i]) for i in order[-10:]][::-1]
    total = sum(sym_contrib.values())
    top_syms = sym_contrib.most_common(10)
    sc = sorted((abs(v) for v in per_sess), reverse=True)
    r02 = {"sessions": S, "mean_session_excess": t["mean"],
           "median_session_excess": t["median"], "std": t["std"], "t": t["t"],
           "p": t["p"], "positive_session_share": t["pos_share"],
           "worst_10_sessions": worst, "best_10_sessions": best,
           "yearly": {str(k): {"sessions": len(v), "mean": st.mean(v),
                               "median": st.median(v),
                               "pos_share": sum(1 for x in v if x > 0) / len(v)}
                      for k, v in sorted(yearly.items())},
           "symbol_concentration": {
               "distinct_symbols": len(sym_contrib),
               "top10_symbols_share_of_total": sum(v for _s, v in top_syms) / total,
               "top10_symbols": [(s, v / total) for s, v in top_syms]},
           "session_concentration": {
               "top_10_sessions_share_of_abs": sum(sc[:10]) / sum(sc),
               "top_50_sessions_share_of_abs": sum(sc[:50]) / sum(sc),
               "sessions_needed_for_50pct_of_abs":
                   next(i + 1 for i in range(S)
                        if sum(sc[:i + 1]) >= 0.5 * sum(sc))}}
    R["R0_2_c2_concentration"] = r02
    print(f"   sessions={S} mean={t['mean']:+.5f} median={t['median']:+.5f} "
          f"std={t['std']:.5f} t={t['t']:+.2f} pos={t['pos_share']:.3f}")
    print(f"   distinct symbols={len(sym_contrib)}  top-10 symbols = "
          f"{r02['symbol_concentration']['top10_symbols_share_of_total']:.1%} of total")
    print(f"   top-10 sessions = {r02['session_concentration']['top_10_sessions_share_of_abs']:.1%}"
          f" of |contribution|; 50% needs "
          f"{r02['session_concentration']['sessions_needed_for_50pct_of_abs']} of {S} sessions")
    for k, v in r02["yearly"].items():
        print(f"   {k}: n={v['sessions']} mean={v['mean']:+.5f} pos={v['pos_share']:.3f}")
    for f_ in FRICTIONS:
        tables.append({"question": "R0.3", "scope": "combined",
                       "cell": f"friction_{f_*100:.2f}pct",
                       "mean_excess": t["mean"] - f_})

    # ── R0.3 — cost sensitivity (illustrative thresholds only) ──────────────
    print("\n[R0.3] C2 gross edge vs illustrative friction (NOT a cost estimate)")
    gross = t["mean"]
    r03 = {"gross_mean_excess": gross, "note":
           "illustrative thresholds only; no broker charge or slippage assumed",
           "net_after_friction": {}}
    for f_ in FRICTIONS:
        r03["net_after_friction"][f"{f_*100:.2f}%"] = gross - f_
        print(f"   friction {f_*100:4.2f}%  ->  net {gross - f_:+.5f}")
    r03["conclusion"] = ("below" if gross < FRICTIONS[0] else
                         "near" if gross < FRICTIONS[2] else "comfortably above")
    print(f"   gross {gross:+.5f} is {r03['conclusion'].upper()} the tested band")
    R["R0_3_c2_cost_sensitivity"] = r03

    with open(os.path.join(args.out, "phase2r_r0_attribution.json"), "w") as fh:
        json.dump(R, fh, indent=2, default=str, sort_keys=True)
    keys = ["question", "scope", "cell", "sessions", "mean_excess", "t", "p",
            "pos_session_share"]
    with open(os.path.join(args.out, "phase2r_r0_tables.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(tables)
    print(f"\n[out] {args.out}/phase2r_r0_attribution.json + phase2r_r0_tables.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
