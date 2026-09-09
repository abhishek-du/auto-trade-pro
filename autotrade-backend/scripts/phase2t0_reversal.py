"""PHASE 2T.0 — daily open-to-close reversal study.

RESEARCH ONLY. No feature, model, hyperparameter, universe, fold or dataset is
changed. No intraday data is used — only the canonical DAILY open and close.

PRIMARY HYPOTHESIS (declared before any result was computed)
    The M1 score negatively predicts the next session's open-to-close return.
    Primary test: session-level Spearman IC between score and intraday return.
    Deciles and top-K are SECONDARY diagnostics.

2026 FIREWALL
    Bars are fetched with a hard upper bound of 2025-12-31 and every fetched
    date is asserted to be < 2026. Prediction session 2025-12-31 is EXCLUDED:
    its target session is 2026-01-01, so its V1 target is a function of a 2026
    close (1,914 rows). Earlier phases included it; this one does not.
"""
from __future__ import annotations

import argparse, asyncio, collections, csv, datetime as dt, json, math, os
import statistics as st, sys, time
import numpy as np
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import v1_contract as C          # noqa: E402
from scripts.phase2q_signal_shape import tstat, spearman   # noqa: E402
from scripts.phase2r_r1 import load, SEED, HELD_OUT_YEAR   # noqa: E402
from scripts.fold_contract import FOLDS      # manifest-authoritative  # noqa: E402
from scripts.phase2r1_liquidity import TURNOVER, LIQUID_QUANTILE  # noqa: E402
from db.database import AsyncSessionLocal      # noqa: E402

HARD_MAX_DATE = dt.date(2025, 12, 31)          # nothing later is ever read
KS = (5, 10, 20, 50, 100, 200)
COSTS_BPS = (5, 10, 15, 20, 30, 50)


async def fetch_bars(symbols):
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT symbol, timestamp::date, open, close FROM candles
            WHERE timeframe='1d' AND to_char(timestamp,'HH24:MI')='03:45'
              AND symbol = ANY(:s)
              AND timestamp::date BETWEEN :a AND :b
            ORDER BY symbol, timestamp
        """), {"s": sorted(symbols), "a": dt.date(2021, 11, 1), "b": HARD_MAX_DATE})).all()
    per = collections.defaultdict(list)
    for sym, d, o, c in rows:
        assert d <= HARD_MAX_DATE and d.year < HELD_OUT_YEAR, f"firewall breach {sym} {d}"
        per[sym].append((d, float(o), float(c)))
    return per


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    from sklearn.ensemble import HistGradientBoostingRegressor

    cache = os.path.join(args.out, "_observations.npz")
    man = json.load(open(os.path.join(args.dir, "manifest.json")))
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        S, SYM, SC, OV, IN, TO, TV = (z["S"], z["SYM"], z["SC"], z["OV"],
                                      z["IN"], z["TO"], z["TV"])
        skipped = list(z["skipped"]); dropped = dict(z["dropped"].item())
        print(f"[cache] reusing {len(S):,} observations from {cache}", flush=True)
        return analyse(args, man, S, SYM, SC, OV, IN, TO, TV, skipped, dropped)

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
    print(f"[load] rows={len(y):,} liquid={keep.sum():,} skipped={skipped}", flush=True)

    # ── score the liquid validation rows, fold by fold ──────────────────────
    score = np.full(len(y), np.nan)
    for year, (tr0, tr1, purge, va0, va1) in FOLDS.items():
        trm = np.fromiter((tr0 <= s <= tr1 for s in sess), bool, len(sess)) & keep
        vam = np.fromiter((va0 <= s <= va1 for s in sess), bool, len(sess)) & keep
        assert not (trm & vam).any()
        t = time.time()
        m = HistGradientBoostingRegressor(
            max_iter=100, learning_rate=0.1, max_leaf_nodes=31, min_samples_leaf=20,
            l2_regularization=0.0, early_stopping=False, random_state=SEED)
        m.fit(X[trm], y[trm])
        score[vam] = m.predict(X[vam])
        print(f"[fold {year}] scored {int(vam.sum()):,} liquid rows "
              f"({time.time()-t:.0f}s)", flush=True)
        del m

    wanted = {syms[i] for i in np.flatnonzero(np.isfinite(score))}
    per = asyncio.run(fetch_bars(wanted))
    pos = {s: {d: k for k, (d, _o, _c) in enumerate(v)} for s, v in per.items()}
    print(f"[bars] {len(per)} symbols, hard max date {HARD_MAX_DATE}", flush=True)

    # ── build the observation table ─────────────────────────────────────────
    rec = []            # (session, symbol, score, overnight, intraday, total, turnover)
    dropped = collections.Counter()
    for i in np.flatnonzero(np.isfinite(score)):
        s, sym = sess[i], syms[i]
        d = dt.date.fromisoformat(s)
        v = per.get(sym)
        if not v or d not in pos[sym]:
            dropped["no_bar_at_D"] += 1; continue
        k = pos[sym][d]
        if k + 1 >= len(v):
            dropped["target_beyond_firewall"] += 1; continue
        nd, oN, cN = v[k + 1]
        cD = v[k][2]
        assert nd <= HARD_MAX_DATE
        if not (cD > 0 and oN > 0 and cN > 0):
            dropped["invalid_price"] += 1; continue
        rec.append((s, sym, float(score[i]), oN / cD - 1.0, cN / oN - 1.0,
                    cN / cD - 1.0, float(X[i, jt])))
    print(f"[join] observations={len(rec):,} dropped={dict(dropped)}", flush=True)

    S = np.array([r[0] for r in rec])
    _tmp = None
    SYM = np.array([r[1] for r in rec])
    SC = np.array([r[2] for r in rec])
    OV = np.array([r[3] for r in rec])
    IN = np.array([r[4] for r in rec])
    TO = np.array([r[5] for r in rec])
    TV = np.array([r[6] for r in rec])
    np.savez_compressed(
        cache, S=np.array([r[0] for r in rec]), SYM=np.array([r[1] for r in rec]),
        SC=np.array([r[2] for r in rec]), OV=np.array([r[3] for r in rec]),
        IN=np.array([r[4] for r in rec]), TO=np.array([r[5] for r in rec]),
        TV=np.array([r[6] for r in rec]), skipped=np.array(skipped, dtype=object),
        dropped=np.array(dict(dropped), dtype=object))
    print(f"[cache] wrote {cache}", flush=True)
    return analyse(args, man, S, SYM, SC, OV, IN, TO, TV, skipped, dict(dropped))


def analyse(args, man, S, SYM, SC, OV, IN, TO, TV, skipped, dropped):
    maxpred = max(S.tolist())
    by = collections.defaultdict(list)
    for i, s in enumerate(S):
        by[s].append(i)
    sessions = sorted(by)
    # identity check
    resid = np.abs((1 + OV) * (1 + IN) - 1 - TO)
    print(f"[T5] max |(1+overnight)(1+intraday)-1 - total| = {resid.max():.3e}", flush=True)

    def yr(s): return int(s[:4])
    SCOPES = [2022, 2023, 2024, 2025, "pooled"]

    R = {"meta": {"dataset_digest": man["dataset_digest"],
                  "observations": len(S), "sessions": len(sessions),
                  "symbols": int(len(set(SYM))),
                  "max_prediction_session": str(maxpred),
                  "hard_max_bar_date": str(HARD_MAX_DATE),
                  "excluded": dict(dropped),
                  "shards_skipped": skipped,
                  "identity_max_residual": float(resid.max()),
                  "primary_hypothesis": ("M1 score negatively predicts next-session "
                                         "open-to-close return; primary test is the "
                                         "session-level Spearman IC"),
                  "universe": f"{TURNOVER} >= session quantile {LIQUID_QUANTILE:.4f}"}}
    tables = []

    def scope_sessions(sc):
        return sessions if sc == "pooled" else [s for s in sessions if yr(s) == sc]

    # ── T4 / T5 — the PRIMARY test and the decomposition ───────────────────
    print("\n[T4/T5] session-level IC of score vs each leg", flush=True)
    R["T4_T5_ic"] = {}
    ic_store = {}
    for leg, arr in (("overnight", OV), ("intraday", IN), ("total", TO)):
        ic_store[leg] = {}
        for sc in SCOPES:
            ics, per_sess = [], {}
            for s in scope_sessions(sc):
                ii = np.asarray(by[s])
                if len(ii) < 50:
                    continue
                r = spearman(arr[ii], SC[ii])
                if r is not None:
                    ics.append(r); per_sess[s] = r
            t = tstat(ics)
            ic_store[leg][sc] = per_sess
            R["T4_T5_ic"].setdefault(leg, {})[str(sc)] = t
            tables.append({"test": "T4_IC", "leg": leg, "scope": sc,
                           "sessions": t["n"], "mean": t["mean"], "median": t["median"],
                           "std": t["std"], "t": t["t"], "p": t["p"],
                           "pos_share": t["pos_share"]})
        c = R["T4_T5_ic"][leg]["pooled"]
        print(f"   {leg:10s} IC={c['mean']:+.4f} t={c['t']:+7.2f} p={c['p']:.3e} "
              f"pos={c['pos_share']:.3f}", flush=True)
    ps = ic_store["intraday"]["pooled"]
    order = sorted(ps, key=lambda s: ps[s])
    R["T4_worst_10_sessions"] = [(s, ps[s]) for s in order[:10]]
    R["T4_best_10_sessions"] = [(s, ps[s]) for s in order[-10:]][::-1]

    # ── T1 — score deciles ─────────────────────────────────────────────────
    print("\n[T1] score deciles", flush=True)
    R["T1_deciles"] = {}
    for sc in SCOPES:
        acc = {d: {"in": [], "ov": [], "to": [], "pos": [], "n": 0} for d in range(1, 11)}
        for s in scope_sessions(sc):
            ii = np.asarray(by[s])
            if len(ii) < 100:
                continue
            q = np.quantile(SC[ii], np.arange(0.1, 1.0, 0.1))
            d = np.digitize(SC[ii], q) + 1
            for dd in range(1, 11):
                m = d == dd
                if m.sum() < 3:
                    continue
                acc[dd]["in"].append(float(IN[ii[m]].mean()))
                acc[dd]["ov"].append(float(OV[ii[m]].mean()))
                acc[dd]["to"].append(float(TO[ii[m]].mean()))
                acc[dd]["pos"].append(float((IN[ii[m]] > 0).mean()))
                acc[dd]["n"] += int(m.sum())
        out = {}
        for dd in range(1, 11):
            ti, tv2, tt = tstat(acc[dd]["in"]), tstat(acc[dd]["ov"]), tstat(acc[dd]["to"])
            out[f"D{dd}"] = {"observations": acc[dd]["n"], "sessions": ti["n"],
                             "mean_intraday": ti["mean"], "median_intraday": ti["median"],
                             "std_intraday": ti["std"], "t": ti["t"], "p": ti["p"],
                             "pct_positive": st.mean(acc[dd]["pos"]) if acc[dd]["pos"] else None,
                             "pct_negative": 1 - st.mean(acc[dd]["pos"]) if acc[dd]["pos"] else None,
                             "mean_overnight": tv2["mean"], "mean_total": tt["mean"]}
            tables.append({"test": "T1_decile", "scope": sc, "cell": f"D{dd}",
                           "observations": acc[dd]["n"], "sessions": ti["n"],
                           "mean_intraday": ti["mean"], "mean_overnight": tv2["mean"],
                           "mean_total": tt["mean"], "t": ti["t"], "p": ti["p"]})
        means = [out[f"D{d}"]["mean_intraday"] for d in range(1, 11)]
        diffs = [means[i + 1] - means[i] for i in range(9)]
        down = sum(1 for x in diffs if x < 0)
        out["_shape"] = {"steps_down": down, "steps_up": 9 - down,
                         "D1": means[0], "D10": means[9], "spread_D10_minus_D1":
                         means[9] - means[0],
                         "monotonic_negative": down >= 8}
        R["T1_deciles"][str(sc)] = out
        if sc == "pooled":
            for dd in range(1, 11):
                o = out[f"D{dd}"]
                print(f"   D{dd:<2} intraday={o['mean_intraday']:+.5f} "
                      f"overnight={o['mean_overnight']:+.5f} total={o['mean_total']:+.5f} "
                      f"t={o['t']:+6.2f}", flush=True)
            print(f"   shape: steps_down={down}/9  spread(D10-D1)="
                  f"{means[9]-means[0]:+.5f}", flush=True)

    # ── T2 / T3 / T6 / T8 — top-K and bottom-K ─────────────────────────────
    print("\n[T2/T3] top-K and bottom-K", flush=True)
    R["T2_topk"], R["T3_bottomk"], R["T6_turnover"] = {}, {}, {}
    for k in KS:
        for sc in SCOPES:
            tin, tov, tto, thit, ttv = [], [], [], [], []
            bin_, bov, bto, bhit = [], [], [], []
            for s in scope_sessions(sc):
                ii = np.asarray(by[s])
                if len(ii) < max(2 * k, 100):
                    continue
                o = np.argsort(-SC[ii], kind="mergesort")
                top, bot = ii[o[:k]], ii[o[-k:]]
                tin.append(float(IN[top].mean())); tov.append(float(OV[top].mean()))
                tto.append(float(TO[top].mean())); thit.append(float((IN[top] > 0).mean()))
                ttv.append(float(np.nanmedian(TV[top])))
                bin_.append(float(IN[bot].mean())); bov.append(float(OV[bot].mean()))
                bto.append(float(TO[bot].mean())); bhit.append(float((IN[bot] > 0).mean()))
            ti, tb = tstat(tin), tstat(bin_)
            eq = float(np.prod([1 + x for x in tin]) - 1) if tin else None
            R["T2_topk"].setdefault(f"top{k}", {})[str(sc)] = {
                **ti, "mean_overnight": tstat(tov)["mean"],
                "mean_total": tstat(tto)["mean"],
                "hit_rate_intraday_positive": st.mean(thit) if thit else None,
                "compounded_supplementary": eq}
            R["T3_bottomk"].setdefault(f"bottom{k}", {})[str(sc)] = {
                **tb, "mean_overnight": tstat(bov)["mean"],
                "mean_total": tstat(bto)["mean"],
                "hit_rate_intraday_positive": st.mean(bhit) if bhit else None}
            R["T6_turnover"].setdefault(f"top{k}", {})[str(sc)] = {
                "median_turnover_of_picks": st.median(ttv) if ttv else None}
            tables.append({"test": "T2_topk", "scope": sc, "cell": f"top{k}",
                           "sessions": ti["n"], "mean_intraday": ti["mean"],
                           "median_intraday": ti["median"], "t": ti["t"], "p": ti["p"],
                           "pos_share": ti["pos_share"], "worst": ti["worst"],
                           "best": ti["best"], "hit_rate": st.mean(thit) if thit else None,
                           "mean_overnight": tstat(tov)["mean"],
                           "mean_total": tstat(tto)["mean"]})
            tables.append({"test": "T3_bottomk", "scope": sc, "cell": f"bottom{k}",
                           "sessions": tb["n"], "mean_intraday": tb["mean"],
                           "t": tb["t"], "p": tb["p"], "worst": tb["worst"],
                           "mean_overnight": tstat(bov)["mean"]})
        c = R["T2_topk"][f"top{k}"]["pooled"]; b = R["T3_bottomk"][f"bottom{k}"]["pooled"]
        print(f"   k={k:<4} top intraday={c['mean']:+.5f} (t={c['t']:+6.2f})   "
              f"bottom intraday={b['mean']:+.5f} (t={b['t']:+6.2f})   "
              f"spread={c['mean']-b['mean']:+.5f}", flush=True)

    # ── T7 — concentration ────────────────────────────────────────────────
    print("\n[T7] concentration (top-10 basket, pooled)", flush=True)
    k = 10
    sess_mean, sess_ids, stock_c = [], [], collections.Counter()
    for s in sessions:
        ii = np.asarray(by[s])
        if len(ii) < 100:
            continue
        o = np.argsort(-SC[ii], kind="mergesort")[:k]
        sel = ii[o]
        sess_mean.append(float(IN[sel].mean())); sess_ids.append(s)
        for j in sel:
            stock_c[SYM[j]] += float(IN[j]) / k
    N = len(sess_mean)
    tot = sum(sess_mean)
    srt = sorted(sess_mean)
    stock_tot = sum(stock_c.values())
    def loo_sessions():
        return [(tot - v) / (N - 1) for v in sess_mean]
    loo = loo_sessions()
    R["T7_concentration"] = {
        "basket": "top10", "sessions": N,
        "overall_mean_intraday": tot / N,
        "top10_sessions_share_of_sum": sum(srt[:10]) / tot if tot else None,
        "top20_sessions_share_of_sum": sum(srt[:20]) / tot if tot else None,
        "distinct_stocks": len(stock_c),
        "top10_stocks_share_of_total": sum(v for _s, v in stock_c.most_common(10)) / stock_tot,
        "top20_stocks_share_of_total": sum(v for _s, v in stock_c.most_common(20)) / stock_tot,
        "most_negative_10_stocks": stock_c.most_common()[-10:],
        "leave_one_session_out": {"min": min(loo), "max": max(loo),
                                  "sign_always_negative": max(loo) < 0}}
    print(f"   overall top10 intraday = {tot/N:+.5f}", flush=True)
    print(f"   leave-one-session-out range: [{min(loo):+.5f}, {max(loo):+.5f}]  "
          f"sign stable={max(loo) < 0}", flush=True)
    print(f"   distinct stocks={len(stock_c)}  top-10 stocks = "
          f"{sum(v for _s,v in stock_c.most_common(10))/stock_tot:.1%} of total", flush=True)

    # leave-one-stock-out on the top-10 basket
    per_stock_sessions = collections.defaultdict(list)
    for s in sessions:
        ii = np.asarray(by[s])
        if len(ii) < 100:
            continue
        o = np.argsort(-SC[ii], kind="mergesort")[:k]
        for j in ii[o]:
            per_stock_sessions[SYM[j]].append(float(IN[j]))
    los = []
    for symn, vals in per_stock_sessions.items():
        adj = (tot * k - sum(vals)) / (N * k - len(vals)) if (N * k - len(vals)) else None
        if adj is not None:
            los.append((symn, adj))
    lo_min = min(los, key=lambda x: x[1]); lo_max = max(los, key=lambda x: x[1])
    R["T7_concentration"]["leave_one_stock_out"] = {
        "stocks_evaluated": len(los), "min": lo_min[1], "min_stock": lo_min[0],
        "max": lo_max[1], "max_stock": lo_max[0],
        "sign_always_negative": lo_max[1] < 0}
    print(f"   leave-one-stock-out range: [{lo_min[1]:+.5f} ({lo_min[0]}), "
          f"{lo_max[1]:+.5f} ({lo_max[0]})]  sign stable={lo_max[1] < 0}", flush=True)

    # ── T8 — friction sensitivity ─────────────────────────────────────────
    R["T8_cost_sensitivity"] = {"note": ("sensitivity only — NOT executable "
                                         "profitability; no strategy is constructed"),
                                "convention": "ROUND-TRIP basis points", "bands": {}}
    for k2 in (5, 10, 20, 50):
        c = R["T2_topk"][f"top{k2}"]["pooled"]["mean"]
        R["T8_cost_sensitivity"]["bands"][f"top{k2}"] = {
            f"{b}bps": abs(c) - b / 10000.0 for b in COSTS_BPS}
    with open(os.path.join(args.out, "phase2t0_results.json"), "w") as fh:
        json.dump(R, fh, indent=2, default=str, sort_keys=True)
    keys = ["test", "leg", "scope", "cell", "observations", "sessions", "mean",
            "median", "std", "mean_intraday", "median_intraday", "mean_overnight",
            "mean_total", "t", "p", "pos_share", "hit_rate", "worst", "best"]
    with open(os.path.join(args.out, "phase2t0_tables.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(tables)
    print(f"\n[out] {args.out}  ({len(tables)} rows)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
