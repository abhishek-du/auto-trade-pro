"""Statistical / data-quality report over a materialized V1 baseline (Phase 2N §4).

Reads only the written shards and the manifest. Prints a report and writes it
alongside the dataset as `quality_report.md`.
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
from scripts import v1_contract as C            # noqa: E402


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def dist(xs):
    return (st.mean(xs), st.median(xs), st.pstdev(xs),
            q(xs, .01), q(xs, .25), q(xs, .75), q(xs, .99), min(xs), max(xs))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()

    man = json.load(open(os.path.join(args.dir, "manifest.json")))
    rows = []
    for p in sorted(glob.glob(os.path.join(args.dir, "v1_baseline_*.csv.gz"))):
        with gzip.open(p, "rt", newline="") as f:
            rows.extend(csv.DictReader(f))

    L = []
    def out(s=""):
        L.append(s)
        print(s)

    n = len(rows)
    syms = collections.Counter(r["symbol"] for r in rows)
    sess = collections.Counter(r["prediction_session"] for r in rows)
    flagged = [r for r in rows if r["extreme_return_flag"] == "1"]
    train = [r for r in rows if r["extreme_return_flag"] != "1"]

    out("# V1 BASELINE — data-quality report")
    out()
    out(f"- dataset version   : {man['dataset_version']}")
    out(f"- contract version  : {man['contract_version']}")
    out(f"- dataset digest    : `{man['dataset_digest']}`")
    out(f"- created at        : {man['created_at']}")
    out()
    out("## Counts")
    out()
    out(f"| total rows | {n:,} |")
    out("|---|---|")
    out(f"| distinct symbols | {len(syms):,} |")
    out(f"| distinct prediction sessions | {len(sess):,} |")
    out(f"| first prediction session | {min(sess)} |")
    out(f"| last prediction session | {max(sess)} |")
    out(f"| first target session | {min(r['target_session'] for r in rows)} |")
    out(f"| last target session | {max(r['target_session'] for r in rows)} |")
    out(f"| flagged (extreme_return_flag) | {len(flagged):,} ({len(flagged)/n:.4%}) |")
    out(f"| default training population | {len(train):,} |")
    out()

    rs = sorted(syms.values())
    out("## Rows per symbol")
    out()
    out(f"min {rs[0]:,} · p25 {q(rs,.25):,} · median {st.median(rs):,.0f} · "
        f"p75 {q(rs,.75):,} · max {rs[-1]:,} · mean {st.mean(rs):,.0f}")
    out()
    ss = sorted(sess.values())
    out("## Rows per session")
    out()
    out(f"min {ss[0]:,} · p25 {q(ss,.25):,} · median {st.median(ss):,.0f} · "
        f"p75 {q(ss,.75):,} · max {ss[-1]:,}")
    thin = sorted((v, k) for k, v in sess.items())[:5]
    out(f"thinnest sessions: {[(k, v) for v, k in thin]}")
    out()

    out("## Target distributions — default training population "
        f"(n = {len(train):,})")
    out()
    out("| target | mean | median | std | p1 | p25 | p75 | p99 | min | max |")
    out("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for t in C.TARGETS:
        xs = [float(r[t]) for r in train]
        m, md, sd, p1, p25, p75, p99, lo, hi = dist(xs)
        out(f"| `{t}` | {m:+.5f} | {md:+.5f} | {sd:.5f} | {p1:+.4f} | {p25:+.4f} | "
            f"{p75:+.4f} | {p99:+.4f} | {lo:+.4f} | {hi:+.4f} |")
    out()

    r0 = [float(r["next_session_return"]) for r in train]
    pos = sum(1 for x in r0 if x > 0)
    neg = sum(1 for x in r0 if x < 0)
    flat = sum(1 for x in r0 if x == 0)
    hi = [float(r["next_session_high_return"]) for r in train]
    lo = [float(r["next_session_low_return"]) for r in train]
    out("## Base rates")
    out()
    out(f"| close return positive | {pos:,} | {pos/len(r0):.2%} |")
    out("|---|---:|---:|")
    out(f"| close return negative | {neg:,} | {neg/len(r0):.2%} |")
    out(f"| close return exactly flat | {flat:,} | {flat/len(r0):.2%} |")
    out(f"| high return positive (touched above prior close) | "
        f"{sum(1 for x in hi if x>0):,} | {sum(1 for x in hi if x>0)/len(hi):.2%} |")
    out(f"| low return negative (touched below prior close) | "
        f"{sum(1 for x in lo if x<0):,} | {sum(1 for x in lo if x<0)/len(lo):.2%} |")
    out()
    out("Touch is nearly free; the close is where the information is. These are "
        "RAW outcomes, not executable outcomes.")
    out()

    out("## Excluded rows, by reason")
    out()
    out("| reason | rows |")
    out("|---|---:|")
    for k, v in sorted(man["exclusion_rules"]["excluded_counts"].items()):
        out(f"| `{k}` | {v:,} |")
    out()

    nulls = collections.Counter()
    infs = collections.Counter()
    for r in rows:
        for c in C.FEATURES + C.TARGETS:
            v = r[c]
            if v == "":
                nulls[c] += 1
            elif not math.isfinite(float(v)):
                infs[c] += 1
    out("## Missing and infinite values")
    out()
    if nulls:
        out("| column | nulls | rate |")
        out("|---|---:|---:|")
        for k, v in nulls.most_common():
            out(f"| `{k}` | {v:,} | {v/n:.4%} |")
        out()
        unexpected = {k for k in nulls if k not in C.NULLABLE_ON_LOCKED_BAR}
        out(f"unexpected null columns: **{sorted(unexpected) or 'none'}**")
    else:
        out("no nulls in any feature or target column")
    out()
    out(f"infinite values: **{sum(infs.values())}**")
    lockrows = sum(1 for r in rows if r["locked_bar_flag"] == "1")
    out(f"locked-circuit bars (high == low at D): **{lockrows:,}** "
        f"({lockrows/n:.4%}) — the only rows permitted a null, in "
        f"{', '.join('`'+c+'`' for c in C.NULLABLE_ON_LOCKED_BAR)}")
    out()

    keys = collections.Counter((r["symbol"], r["prediction_session"]) for r in rows)
    out("## Structural checks")
    out()
    out("| check | violations |")
    out("|---|---:|")
    out(f"| duplicate (symbol, prediction_session) | "
        f"{sum(1 for v in keys.values() if v > 1)} |")
    out(f"| target_session <= prediction_session | "
        f"{sum(1 for r in rows if r['target_session'] <= r['prediction_session'])} |")
    out(f"| calendar_gap_days <= 0 | "
        f"{sum(1 for r in rows if int(r['calendar_gap_days']) <= 0)} |")
    out(f"| close_D <= 0 | {sum(1 for r in rows if float(r['close_D']) <= 0)} |")
    out()
    gaps = collections.Counter(int(r["calendar_gap_days"]) for r in rows)
    out(f"calendar_gap_days distribution: "
        f"{dict(sorted(gaps.items())[:8])}{' …' if len(gaps) > 8 else ''}")
    out(f"max calendar_gap_days: **{max(gaps)}** "
        f"({gaps[max(gaps)]:,} rows) — a closure, not a suspension")
    out()

    mp = man["provenance"]["market_proxy"]
    have = sum(1 for r in rows if r["mkt_return_21d"] != "")
    out("## Market-proxy coverage")
    out()
    out(f"- proxy: **{mp['name']}** (`{mp['symbol']}`), {mp['sessions']:,} sessions")
    out(f"- rows carrying a proxy value: {have:,} / {n:,} ({have/n:.4%})")
    out()

    out("## Quarantine coverage")
    out()
    out(f"- flagged rows: **{len(flagged):,}** across "
        f"{len({r['symbol'] for r in flagged})} symbols")
    out(f"- retained in the dataset, excluded from the default training population")
    out(f"- listed separately in `v1_baseline_anomalies.csv`")
    out()

    out("## Largest observations — for manual inspection")
    out()
    for label, key, rev in (("largest positive", "next_session_return", True),
                            ("largest negative", "next_session_return", False)):
        pick = sorted(rows, key=lambda r: float(r[key]), reverse=rev)[:10]
        out(f"**{label} `{key}` (all rows, flagged included)**")
        out()
        out("| symbol | prediction | target | close D | return | flagged |")
        out("|---|---|---|---:|---:|:--:|")
        for r in pick:
            out(f"| {r['symbol']} | {r['prediction_session']} | {r['target_session']} "
                f"| {float(r['close_D']):,.2f} | {float(r[key]):+.4f} "
                f"| {'YES' if r['extreme_return_flag']=='1' else ''} |")
        out()

    p = os.path.join(args.dir, "quality_report.md")
    with open(p, "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"\n[report] wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
