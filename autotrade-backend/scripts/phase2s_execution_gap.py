"""PHASE 2S — execution-timing diagnostic.

The V1 target is close[D+1]/close[D]-1. The signal is computed FROM close[D]
(return_1d, breakout_20d and the rest all use it), so the decision cannot exist
until close[D] has printed — and a fill AT close[D] is therefore unattainable.
The earliest executable fill is the NEXT session's open.

This splits the frozen target into the two pieces that decide whether any of it
is reachable:

    close[D] -> open[D+1]   overnight gap      NOT capturable
    open[D+1] -> close[D+1] intraday leg       capturable
    close[D] -> close[D+1]  the V1 target      = the product of the two

open[D+1] is read from the canonical `candles` source as an EVALUATION FILL
PRICE. It is never a model input, no feature is added, and V1 is not modified.
"""
from __future__ import annotations

import argparse, asyncio, collections, csv, json, math, os, statistics as st, sys, time
import numpy as np
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import v1_contract as C          # noqa: E402
from scripts.phase2q_signal_shape import tstat   # noqa: E402
from scripts.phase2r_r1 import load, SEED, HELD_OUT_YEAR   # noqa: E402
from scripts.fold_contract import FOLDS      # manifest-authoritative  # noqa: E402
from scripts.phase2r1_liquidity import TURNOVER, LIQUID_QUANTILE  # noqa: E402
from db.database import AsyncSessionLocal      # noqa: E402

KS = (5, 10, 20)


async def fetch_bars(symbols, lo, hi):
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT symbol, timestamp::date, open, close FROM candles
            WHERE timeframe='1d' AND to_char(timestamp,'HH24:MI')='03:45'
              AND symbol = ANY(:s) AND timestamp::date BETWEEN :a AND :b
            ORDER BY symbol, timestamp
        """), {"s": sorted(symbols), "a": lo, "b": hi})).all()
    per = collections.defaultdict(list)
    for sym, d, o, c in rows:
        assert d.year < HELD_OUT_YEAR, f"2026 bar fetched: {sym} {d}"
        per[sym].append((d, float(o), float(c)))
    return per


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    from sklearn.ensemble import HistGradientBoostingRegressor

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

    picks = []          # (year, session, symbol, rank)
    for year, (tr0, tr1, purge, va0, va1) in FOLDS.items():
        trm = np.fromiter((tr0 <= s <= tr1 for s in sess), bool, len(sess)) & keep
        vam = np.fromiter((va0 <= s <= va1 for s in sess), bool, len(sess)) & keep
        t = time.time()
        m = HistGradientBoostingRegressor(
            max_iter=100, learning_rate=0.1, max_leaf_nodes=31, min_samples_leaf=20,
            l2_regularization=0.0, early_stopping=False, random_state=SEED)
        m.fit(X[trm], y[trm])
        sc = np.full(len(y), np.nan); sc[vam] = m.predict(X[vam])
        for s in [x for x in alls if va0 <= x <= va1]:
            ii = np.asarray(idx[s]); ii = ii[keep[ii]]
            ok = np.isfinite(sc[ii])
            ii2 = ii[ok]
            if len(ii2) < 50:
                continue
            o = np.argsort(-sc[ii2], kind="mergesort")[:max(KS)]
            for rank, j in enumerate(ii2[o]):
                picks.append((year, s, syms[j], rank, float(y[j])))
        print(f"[fold {year}] picks collected {time.time()-t:.0f}s", flush=True)

    want = {p[2] for p in picks}
    lo = min(p[1] for p in picks)
    hi = "2025-12-31"
    import datetime as dt
    per = asyncio.run(fetch_bars(want, dt.date.fromisoformat(lo),
                                 dt.date.fromisoformat(hi)))
    pos = {sym: {d: i for i, (d, _o, _c) in enumerate(v)} for sym, v in per.items()}
    print(f"[bars] {len(per)} symbols fetched, {lo} .. {hi}", flush=True)

    rows_out, agg = [], collections.defaultdict(lambda: collections.defaultdict(list))
    missing = 0
    for year, s, sym, rank, tgt in picks:
        d = dt.date.fromisoformat(s)
        v = per.get(sym)
        if not v or d not in pos[sym]:
            missing += 1
            continue
        i = pos[sym][d]
        if i + 1 >= len(v):
            missing += 1
            continue
        cD = v[i][2]
        oN, cN = v[i + 1][1], v[i + 1][2]
        if not (cD > 0 and oN > 0 and cN > 0):
            missing += 1
            continue
        assert v[i + 1][0].year < HELD_OUT_YEAR
        gap = oN / cD - 1.0
        intraday = cN / oN - 1.0
        c2c = cN / cD - 1.0
        for k in KS:
            if rank < k:
                agg[(year, k)]["gap"].append(gap)
                agg[(year, k)]["intraday"].append(intraday)
                agg[(year, k)]["c2c"].append(c2c)
    print(f"[join] unusable picks: {missing}", flush=True)

    out = {"meta": {"dataset_digest": man["dataset_digest"],
                    "fill_price_source": "canonical candles open[D+1] (evaluation only)",
                    "unusable_picks": missing},
           "by_fold": {}}
    print(f"\n{'k':4} {'fold':5} {'c2c (V1)':>11} {'overnight gap':>14} "
          f"{'open->close':>12} {'gap share':>10}")
    for k in KS:
        for year in FOLDS:
            a = agg[(year, k)]
            if not a["c2c"]:
                continue
            tg, ti, tc = tstat(a["gap"]), tstat(a["intraday"]), tstat(a["c2c"])
            share = tg["mean"] / tc["mean"] if tc["mean"] else None
            out["by_fold"].setdefault(f"top{k}", {})[str(year)] = {
                "close_to_close": tc, "overnight_gap": tg, "open_to_close": ti,
                "gap_share_of_total": share}
            rows_out.append({"k": k, "fold": year, "picks": tc["n"],
                             "close_to_close_mean": tc["mean"],
                             "overnight_gap_mean": tg["mean"],
                             "open_to_close_mean": ti["mean"],
                             "open_to_close_t": ti["t"],
                             "open_to_close_p": ti["p"],
                             "gap_share_of_total": share})
            print(f"{k:4} {year:5} {tc['mean']:+11.5f} {tg['mean']:+14.5f} "
                  f"{ti['mean']:+12.5f} {share:>10.1%}")
    with open(os.path.join(args.out, "phase2s_execution_gap.json"), "w") as fh:
        json.dump(out, fh, indent=2, default=str, sort_keys=True)
    with open(os.path.join(args.out, "phase2s_execution_gap.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
        w.writeheader(); w.writerows(rows_out)
    print(f"\n[out] {args.out}/phase2s_execution_gap.*")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
