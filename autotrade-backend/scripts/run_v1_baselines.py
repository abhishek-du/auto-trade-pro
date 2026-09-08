"""Execute the frozen B0-B6 baselines over the frozen folds (Phase 2P).

No model is trained, nothing is tuned, and the 2026 shard is never opened —
the loader refuses to read it and the run aborts if it is ever requested.

Everything comes from the frozen protocol: `scripts/v1_protocol.py` supplies
the baselines and the metrics, `scripts/v1_contract.py` the column names. This
file only wires them to the data and prints what came out. Nothing here chooses
a winner.

    .venv/bin/python scripts/run_v1_baselines.py --dir datasets/v1_baseline
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import v1_contract as C          # noqa: E402
from scripts import v1_protocol as P          # noqa: E402

HELD_OUT_YEAR = 2026                          # never opened in this phase
FOLDS = [2022, 2023, 2024, 2025]
TOPK = (10, 20, 50)

NEEDED = ["symbol", "prediction_session", "next_session_return",
          "extreme_return_flag", "return_21d", "volume_ratio_20d"]


def load(dirpath: str):
    """Rows for 2017-2025 only. The held-out shard is not opened."""
    rows = []
    opened = []
    for p in sorted(glob.glob(os.path.join(dirpath, "v1_baseline_*.csv.gz"))):
        year = int(os.path.basename(p).split("_")[-1].split(".")[0])
        if year >= HELD_OUT_YEAR:
            print(f"[load] SKIPPED (held out): {os.path.basename(p)}")
            continue
        opened.append(os.path.basename(p))
        with gzip.open(p, "rt", newline="") as f:
            for r in csv.DictReader(f):
                rows.append({k: r[k] for k in NEEDED})
    print(f"[load] opened {len(opened)} shards, {len(rows):,} rows")
    return rows, opened


def _f(x):
    return float(x) if x not in ("", None) else None


def ttest_mean_zero(xs):
    """t-statistic of the mean against zero, for a session-level series."""
    n = len(xs)
    if n < 3:
        return float("nan"), float("nan")
    m, sd = st.mean(xs), st.stdev(xs)
    if sd == 0:
        return float("nan"), float("nan")
    t = m / (sd / math.sqrt(n))
    # two-sided normal approximation; n is in the hundreds so this is adequate
    p = math.erfc(abs(t) / math.sqrt(2))
    return t, p


def evaluate(name, predict, val_by_session, k_list=TOPK):
    """All four metric groups for one baseline over one fold."""
    y_all, p_all = [], []
    ics, per_k = [], {k: [] for k in k_list}
    excess_k = {k: [] for k in k_list}

    for sess in sorted(val_by_session):
        rows = val_by_session[sess]
        scored = [(predict(r), r["y"]) for r in rows]
        usable = [(s, y) for s, y in scored if s is not None]
        if not usable:
            continue
        y_all.extend([y for _s, y in usable])
        p_all.extend([s for s, _y in usable])

        if len({s for s, _ in usable}) > 1:            # ties carry no ranking
            ics.append(P.spearman([y for _s, y in usable],
                                  [s for s, _y in usable]))
        sess_mean = st.mean([y for _s, y in usable])
        for k in k_list:
            r = P.topk_session(scored, k)
            if r:
                per_k[k].append(r)
                excess_k[k].append(r["avg_return"] - sess_mean)

    out = {"baseline": name, "n_rows": len(y_all),
           "n_sessions": len({s for s in val_by_session})}
    if not y_all:
        return out

    out["return"] = {"mae": P.mae(y_all, p_all), "rmse": P.rmse(y_all, p_all),
                     "pearson": P.pearson(y_all, p_all)}
    out["direction"] = P.direction_scores(y_all, p_all)

    if ics:
        t, p = ttest_mean_zero(ics)
        out["ranking"] = {"sessions_with_ic": len(ics), "mean_ic": st.mean(ics),
                          "median_ic": st.median(ics),
                          "ic_std": st.pstdev(ics),
                          "ic_t_stat": t, "ic_p_value": p,
                          "ic_positive_rate": sum(1 for x in ics if x > 0) / len(ics)}
    out["topk"] = {}
    for k in k_list:
        if not per_k[k]:
            continue
        agg = P.aggregate_topk(per_k[k])
        t, p = ttest_mean_zero(excess_k[k])
        agg["mean_excess_vs_session_mean"] = st.mean(excess_k[k])
        agg["excess_t_stat"], agg["excess_p_value"] = t, p
        out["topk"][k] = agg
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    man = json.load(open(os.path.join(args.dir, "manifest.json")))
    print(f"[dataset] {man['dataset_version']} / {man['contract_version']}")
    print(f"[dataset] digest={man['dataset_digest']}")
    print(f"[dataset] rows={man['row_count']:,} symbols={man['symbol_count']}")

    rows, opened = load(args.dir)
    assert all(str(HELD_OUT_YEAR) not in f for f in opened), "held-out shard opened"

    flagged = sum(1 for r in rows if r["extreme_return_flag"] == "1")
    rows = [r for r in rows if r["extreme_return_flag"] != "1"]
    print(f"[dataset] quarantined rows removed: {flagged:,}")

    for r in rows:
        r["session"] = dt.date.fromisoformat(r["prediction_session"])
        r["y"] = float(r["next_session_return"])
        r["return_21d"] = _f(r["return_21d"])
        r["volume_ratio_20d"] = _f(r["volume_ratio_20d"])

    sessions = sorted({r["session"] for r in rows})
    by_session = collections.defaultdict(list)
    for r in rows:
        by_session[r["session"]].append(r)
    print(f"[dataset] usable sessions {sessions[0]} .. {sessions[-1]} "
          f"({len(sessions):,})")

    boundaries = []
    for y in FOLDS:
        first = next((d for d in sessions if d.year == y), None)
        if first is None:
            print(f"[folds] fold {y} has no sessions — ABORT")
            return 2
        boundaries.append(first)
    folds = P.expanding_folds(sessions, boundaries)

    results = {}
    print("\n" + "=" * 78)
    for fold, year in zip(folds, FOLDS):
        tr = [r for r in rows if fold.contains_train(r["session"])]
        va_sessions = {d: by_session[d] for d in sessions
                       if fold.contains_val(d) and d.year == year}
        va_rows = sum(len(v) for v in va_sessions.values())
        print(f"\nFOLD {year}  train {fold.train_start} .. {fold.train_end} "
              f"({len(tr):,} rows)   validate {min(va_sessions)} .. {max(va_sessions)} "
              f"({va_rows:,} rows, {len(va_sessions)} sessions)")
        print(f"   purged session(s): {[str(d) for d in fold.purged]}")
        train_returns = [r["y"] for r in tr]

        fitted = {
            "B0_zero": P.Baselines.zero(),
            "B1_historical_mean": P.Baselines.historical_mean(train_returns),
            "B2_historical_median": P.Baselines.historical_median(train_returns),
            "B3_momentum_21d": P.Baselines.momentum_21d(),
            "B4_volume_weighted_momentum": P.Baselines.volume_weighted_momentum(),
            "B5_majority_class": (lambda f: (lambda r: 1.0 if f(r) else -1.0))(
                P.Baselines.majority_class(train_returns)),
            "B6_rank_by_momentum": P.Baselines.rank_by_momentum(),
        }
        for name, fn in fitted.items():
            res = evaluate(name, fn, va_sessions)
            results.setdefault(name, {})[year] = res

    # ── report ───────────────────────────────────────────────────────────
    def cell(v, w=9, d=4):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—".rjust(w)
        return f"{v:{w}.{d}f}"

    print("\nRETURN + DIRECTION  (validation rows, per fold)")
    print(f"{'baseline':28s} {'fold':>6} {'MAE':>9} {'RMSE':>9} {'Pearson':>9} "
          f"{'acc':>8} {'bal_acc':>8} {'prec_up':>8} {'rec_up':>8}")
    for name in results:
        for year in FOLDS:
            r = results[name][year]
            if "return" not in r:
                continue
            d = r["direction"]
            print(f"{name:28s} {year:>6} {cell(r['return']['mae'])} "
                  f"{cell(r['return']['rmse'])} {cell(r['return']['pearson'])} "
                  f"{cell(d['accuracy'],8,4)} {cell(d['balanced_accuracy'],8,4)} "
                  f"{cell(d['precision_up'],8,4)} {cell(d['recall_up'],8,4)}")

    print("\nRANKING — per-session Spearman IC")
    print(f"{'baseline':28s} {'fold':>6} {'sessions':>9} {'mean IC':>9} "
          f"{'median IC':>10} {'IC std':>8} {'t':>8} {'p':>10} {'IC>0':>7}")
    for name in results:
        for year in FOLDS:
            r = results[name][year].get("ranking")
            if not r:
                continue
            print(f"{name:28s} {year:>6} {r['sessions_with_ic']:>9} "
                  f"{cell(r['mean_ic'])} {cell(r['median_ic'],10)} "
                  f"{cell(r['ic_std'],8)} {cell(r['ic_t_stat'],8,2)} "
                  f"{cell(r['ic_p_value'],10,6)} {cell(r['ic_positive_rate'],7,3)}")

    for k in TOPK:
        print(f"\nTOP-{k} — per session, then aggregated across sessions")
        print(f"{'baseline':28s} {'fold':>6} {'sess':>6} {'mean ret':>10} "
              f"{'median':>10} {'excess':>10} {'t':>7} {'p':>9} {'hit':>7} "
              f"{'worst dec':>10} {'worst':>10} {'cover':>7}")
        for name in results:
            for year in FOLDS:
                a = results[name][year].get("topk", {}).get(k)
                if not a:
                    continue
                print(f"{name:28s} {year:>6} {a['sessions_evaluated']:>6} "
                      f"{cell(a['mean_of_session_avg_return'],10,5)} "
                      f"{cell(a['median_of_session_avg_return'],10,5)} "
                      f"{cell(a['mean_excess_vs_session_mean'],10,5)} "
                      f"{cell(a['excess_t_stat'],7,2)} {cell(a['excess_p_value'],9,5)} "
                      f"{cell(a['mean_hit_rate'],7,3)} "
                      f"{cell(a['worst_decile_of_session_avg'],10,5)} "
                      f"{cell(a['worst_session_avg'],10,5)} "
                      f"{cell(a['mean_coverage'],7,3)}")

    print("\nSTABILITY — sign and spread of the fold-level statistics")
    for name in results:
        ics = [results[name][y].get("ranking", {}).get("mean_ic") for y in FOLDS]
        ics = [x for x in ics if x is not None]
        ex = [results[name][y].get("topk", {}).get(TOPK[0], {}).get(
                  "mean_excess_vs_session_mean") for y in FOLDS]
        ex = [x for x in ex if x is not None]
        if not ics and not ex:
            continue
        line = f"{name:28s}"
        if ics:
            line += (f"  IC folds={['%+.4f' % x for x in ics]}"
                     f" same-sign={len({x > 0 for x in ics}) == 1}")
        if ex:
            line += f"  top{TOPK[0]}-excess={['%+.5f' % x for x in ex]}"
        print(line)

    print("\n" + "=" * 78)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2, default=str, sort_keys=True)
        print(f"[out] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
